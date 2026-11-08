#!/usr/bin/env python3
"""Azure資源を作る前に、Entra登録に必要な名前と戻り先URIを計算する。

保護されたローカルparamsのプロダクト名、環境名、保存済みAPIM識別子、
API Centerリージョン、Gatewayの公開FQDNから、予定名と戻り先をJSONで返す。
クライアントの名前・戻り先は独立リポジトリの設定で管理する。
名前の計算は実構築と同じBicepの式をローカル実行して行い、Python独自の
計算で異なる名前になることを避ける。Bicep CLIは事前に必要である。
Azureログイン、Azure通信、Entraアプリ登録、リソース作成、課金は行わない。
"""
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
import re
import shutil
import subprocess
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.paths import LocalPaths, private_output

NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
REGION = re.compile(r"[a-z][a-z0-9]+")


def load_registration_configuration(path: Path | str, repository: Path | None = None) -> dict:
    """明示された保護paramsだけを読み、参照先configや秘密ファイルを開かない。"""
    root = ROOT if repository is None else Path(repository).resolve()
    target = Path(path).expanduser()
    target = private_output(target if target.is_absolute() else root / target, root)
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise ValueError("Use .local/<environment>/params.local.json for registration preparation") from None
    if (len(relative.parts) != 3 or relative.parts[0] != ".local"
            or relative.parts[2] != "params.local.json"):
        raise ValueError("Use .local/<environment>/params.local.json for registration preparation")
    paths = LocalPaths(root, relative.parts[1])
    paths.checked(target)
    if not target.is_file() or (os.name != "nt" and stat.S_IMODE(target.stat().st_mode) != 0o600):
        raise ValueError("Registration parameters require a regular owner-only file (chmod 600)")
    for directory in (paths.root, root / ".local"):
        if os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError("Registration parameter directories require owner-only permissions (chmod 700)")
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Registration parameters could not be read") from None
    if not isinstance(document, dict) or document.get("environmentName") != paths.environment:
        raise ValueError("Registration parameters must match their local environment directory")
    return document


def registration_inputs(config: dict) -> dict:
    """命名に必要な項目だけを検査する。Entra登録前の全構成検証は不要。"""
    if not isinstance(config, dict):
        raise ValueError("Registration preparation requires a configuration object")
    product_name = config.get("productName")
    environment_name = config.get("environmentName")
    for label, value, limit in (("productName", product_name, 24), ("environmentName", environment_name, 12)):
        if not isinstance(value, str) or len(value) > limit or not NAME.fullmatch(value):
            raise ValueError(f"{label} must use lowercase letters, digits and single internal hyphens (maximum {limit} characters)")
    stem = f"{environment_name}-{product_name}"
    if len(stem) > 17:
        raise ValueError("environmentName-productName must fit within 17 characters for the credentials Container App; shorten either name")
    identifier = apim_instance_id(config)
    # 同じ保存済み入力から求めることを命名ヘルパーとも照合する。
    apim_name({"resourceStem": stem, "services": {"apimInstanceId": identifier}})
    api_center_location = config.get("apiCenterLocation")
    if not isinstance(api_center_location, str) or not REGION.fullmatch(api_center_location):
        raise ValueError("apiCenterLocation must be an Azure region code")
    gateway = config.get("gateway")
    public_host_name = gateway.get("publicHostName") if isinstance(gateway, dict) else None
    if (not isinstance(public_host_name, str) or len(public_host_name) > 253
            or len(public_host_name.split('.')) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                   for part in public_host_name.split('.'))):
        raise ValueError("gateway.publicHostName must be the approved lowercase public DNS hostname")
    return {
        "WORKFORCE_REGISTRATION_RESOURCE_STEM": stem,
        "WORKFORCE_REGISTRATION_APIM_INSTANCE_ID": identifier,
        "WORKFORCE_REGISTRATION_API_CENTER_LOCATION": api_center_location,
        "WORKFORCE_REGISTRATION_PUBLIC_HOST_NAME": public_host_name,
    }


def bicep_executable() -> str:
    """BICEP_CLIまたは既存のBicep CLIを探す。インストールやAzureログインはしない。"""
    override = os.environ.get("BICEP_CLI")
    candidates = [override] if override else ["bicep", str(Path.home() / ".azure/bin" / ("bicep.exe" if os.name == "nt" else "bicep"))]
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return executable
    raise ValueError("Install the Bicep CLI or set BICEP_CLI to its installed executable; no Azure login is needed")


def prepare_registration(config: dict) -> dict:
    """保存済み命名入力をBicepで計算し、登録用の名前とURIを返す。"""
    public_inputs = registration_inputs(config)
    executable = bicep_executable()
    environment = {**os.environ, **public_inputs}
    # 単独配布の.NET版CLIは、実行時に書込可能な展開先を必要とする場合がある。
    # 一時フォルダーを子プロセスだけに渡し、呼出元の設定や既存ファイルを変更しない。
    with tempfile.TemporaryDirectory(prefix="workforce-registration-") as temporary:
        environment.setdefault("DOTNET_BUNDLE_EXTRACT_BASE_DIR", temporary)
        try:
            result = subprocess.run(
                [executable, "build-params", str(ROOT / "infra/registration-names.bicepparam"), "--stdout", "--no-restore"],
                env=environment, capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("The local Bicep CLI could not build registration names") from None
    if result.returncode:
        raise ValueError("The local Bicep CLI could not build registration names; check its version and registration-names.bicepparam")
    try:
        compiled = json.loads(result.stdout)
        parameters = json.loads(compiled["parametersJson"])
        names = parameters["parameters"]["registration"]["value"]
        if not isinstance(names, dict) or set(names) != {"resourceStem", "apimName", "apiCenterName", "gatewayOrigin", "credentialManagerRedirectUri", "apiCenterPortalRedirectUri"}:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("The local Bicep CLI returned an unexpected registration format") from None
    return names


def main(argv=None) -> int:
    """登録前の公開入力を受け取り、用途を分けた戻り先をJSON表示する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True,
                        help=".local/<environment>/params.local.json")
    args = parser.parse_args(argv)
    try:
        names = prepare_registration(load_registration_configuration(args.config))
        print(json.dumps(names, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError):
        print("Registration preparation failed: check the protected local parameters, naming inputs and Bicep CLI; no Azure access was performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
