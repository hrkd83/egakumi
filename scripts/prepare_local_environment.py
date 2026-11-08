#!/usr/bin/env python3
"""公開例から.local/<environment>の保護された入力を作る。Azureへ接続しない。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path
import stat

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deployment.helpers.configuration import _file_reference_fields
from deployment.helpers.apim_naming import apim_instance_id, generate_apim_instance_id
from deployment.helpers.apim_generations import assert_preparation_available, prepare_next_instance
from deployment.helpers.paths import LocalPaths, REPOSITORY, private_parent


def prepare(environment: str, profile: str, repository: Path = REPOSITORY, *, new_apim_instance: bool = False) -> dict:
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
        if (not isinstance(current, dict) or current.get("environmentName") != environment
                or current.get("configFile") != f"../../config/{profile}.json"):
            raise ValueError("Existing local parameters select a different environment/profile; no file was replaced")
        apim_instance_id(current)
        if new_apim_instance:
            return prepare_next_instance(paths, current)
        paths.ensure()
        return {"status": "existing-input-preserved", "parameters": str(paths.params.relative_to(paths.repository))}
    if new_apim_instance:
        raise ValueError("Restore the existing local parameters before preparing another APIM instance")
    assert_preparation_available(paths)
    document = json.loads((paths.repository / "params" / f"{profile}.example.json").read_text(encoding="utf-8"))
    document["environmentName"] = environment
    document["services"]["apimInstanceId"] = generate_apim_instance_id()
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
    parser.add_argument("--new-apim-instance", action="store_true",
                        help="Prepare another APIM identifier after verified postdown; preserve the previous state as history")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.environment, args.profile, new_apim_instance=args.new_apim_instance), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("Local preparation stopped: " + message + ". Preserve the existing parameters and preparation records.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
