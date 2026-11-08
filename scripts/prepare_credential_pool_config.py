#!/usr/bin/env python3
"""既存paramsの本人接続をSQLプール方式へ切り替える。既定は確認のみ。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from deployment.helpers.configuration import load_configuration_source, normalize_configuration
from deployment.helpers.paths import REPOSITORY, configuration_path, local_paths, private_output, private_parent
from deployment.helpers.credential_login import SESSION_POLICY_DEFAULTS


def transform(document, *, old_name="graph-personal", new_name="graph-delegated", count=12, capacity=8000):
    if type(count) is not int or not 1 <= count <= 26 or type(capacity) is not int or not 1 <= capacity <= 8000:
        raise ValueError("Pool count or capacity is outside the supported range")
    result = copy.deepcopy(document)
    providers = result.get("credentialProviders", [])
    targets = [p for p in providers if p.get("name") in (old_name, new_name)]
    if len(targets) != 1:
        raise ValueError("Select exactly one existing Graph credential profile")
    provider = targets[0]
    previous = provider["name"]
    if provider.get("connectionNames"):
        raise ValueError("Review fixed service connections separately before converting this profile")
    provider.update(name=new_name, displayName="Delegated Graph connection", pool={"count": count, "capacity": capacity})
    login = result["gateway"]["authentication"]["credentialLogin"]
    login["providerNames"] = [new_name if name == previous else name for name in login["providerNames"]]
    login["transactionLifetimeSeconds"] = 600
    login.pop("sessionLifetimeSeconds", None)
    for field, default in SESSION_POLICY_DEFAULTS.items():
        login.setdefault(field, default)
    for api in result.get("apiDefinitions", []):
        connection = api.get("credentialConnection", {})
        if connection.get("providerName") == previous and connection.get("mode") == "personal":
            connection["providerName"] = new_name
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Parameters; default: WORKFORCE_CONFIG or the selected azd environment")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    path = args.config.expanduser() if args.config else configuration_path(REPOSITORY)
    path = private_output(path if path.is_absolute() else REPOSITORY / path, REPOSITORY)
    original = path.read_bytes()
    updated = transform(json.loads(original.decode("utf-8")))
    configuration = normalize_configuration(transform(load_configuration_source(path)))
    runtime = local_paths(configuration, REPOSITORY)
    runtime.check_selection(configuration)
    if args.execute:
        path = runtime.checked(path)
    changed = json.loads(original.decode("utf-8")) != updated
    backup = None
    if args.execute and changed:
        backup = runtime.state / ("credential-pools-" + hashlib.sha256(original).hexdigest() + ".params.backup.json")
        private_parent(backup, REPOSITORY)
        private_parent(path, REPOSITORY)
        if not backup.exists():
            with open(backup, "xb") as stream:
                os.chmod(backup, 0o600)
                stream.write(original)
        if path.read_bytes() != original:
            raise ValueError("Parameters changed during validation; retry without overwriting them")
        fd, staged = tempfile.mkstemp(prefix=".credential-pools-", suffix=".local.json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.chmod(staged, 0o600)
                json.dump(updated, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(staged, path)
        finally:
            Path(staged).unlink(missing_ok=True)
    print(json.dumps({"mode": "execute" if args.execute else "plan", "parameterFileChanged": bool(args.execute and changed),
        "profile": "graph-delegated", "physicalProviders": 12, "capacityPerProvider": 8000,
        "subscriptionKeyHeader": updated["gateway"]["authentication"]["subscriptionKeyHeader"],
        "backup": str(backup) if backup else None, "azureWrites": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError):
        raise SystemExit("Parameter conversion stopped; check the current provider and configuration. No Azure resources were changed.") from None
