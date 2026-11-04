#!/usr/bin/env python3
"""公開例から.local/<environment>の保護された入力を作る。Azureへ接続しない。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

from deployment.helpers.configuration import _file_reference_fields
from deployment.helpers.paths import LocalPaths, REPOSITORY, private_parent


def prepare(environment: str, profile: str, repository: Path = REPOSITORY) -> dict:
    """既存入力を上書きせず、公開configへの参照と環境別の秘密参照を揃える。"""
    paths = LocalPaths(repository, environment)
    if profile not in ("test", "production"):
        raise ValueError("Select the test or production public profile")
    if paths.params.exists() or paths.params.is_symlink():
        paths.checked(paths.params)
        if not paths.params.is_file() or (os.name != "nt" and stat.S_IMODE(paths.params.stat().st_mode) != 0o600):
            raise ValueError("Existing local parameters require a regular owner-only file (chmod 600)")
        try:
            current = json.loads(paths.params.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Existing local parameters could not be verified; no file was replaced") from None
        if (current.get("environmentName") != environment
                or current.get("configFile") != f"../../config/{profile}.json"):
            raise ValueError("Existing local parameters select a different environment/profile; no file was replaced")
        paths.ensure()
        return {"status": "existing-input-preserved", "parameters": str(paths.params.relative_to(paths.repository))}
    document = json.loads((paths.repository / "params" / f"{profile}.example.json").read_text(encoding="utf-8"))
    document["environmentName"] = environment
    document["configFile"] = f"../../config/{profile}.json"
    document["$schema"] = "../../schemas/params.schema.json"
    document["$comment"] = "Local deployment inputs. Keep this file and its related runtime data out of Git and published artifacts."
    for parent, key, _label in _file_reference_fields(document):
        parent[key] = "./secrets/" + Path(parent[key]).name
    paths.ensure()
    target = private_parent(paths.params, paths.repository)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "created", "parameters": str(target.relative_to(paths.repository)),
            "profile": profile, "azureAccessPerformed": False,
            "next": "Edit the local parameters, then create/select the matching azd environment."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--profile", choices=("test", "production"), default="test")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.environment, args.profile), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError):
        print("Local preparation stopped: check the environment, existing input and owner-only folder permissions; no existing input was overwritten.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
