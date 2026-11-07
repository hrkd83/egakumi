#!/usr/bin/env python3
"""Azure資源を作る前に、Entra登録に必要な名前と戻り先URIを計算する。

対象サブスクリプション、プロダクト名、環境名、API Centerリージョン、
Gatewayの公開FQDNを入力し、基盤の予定名と戻り先をJSONで返す。
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
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
REGION = re.compile(r"[a-z][a-z0-9]+")


def registration_inputs(
    subscription_id: str,
    product_name: str,
    environment_name: str,
    api_center_location: str,
    public_host_name: str = "gateway.example.com",
) -> dict:
    """命名に必要な公開入力を検査し、Bicepへ渡す環境変数の辞書を作る。"""
    if not isinstance(subscription_id, str):
        raise ValueError("subscription-id must be a non-zero canonical GUID")
    try:
        parsed = uuid.UUID(subscription_id)
    except (ValueError, AttributeError):
        raise ValueError("subscription-id must be a non-zero canonical GUID") from None
    if not parsed.int or str(parsed) != subscription_id.lower():
        raise ValueError("subscription-id must be a non-zero canonical GUID")
    for label, value, limit in (("product-name", product_name, 24), ("environment-name", environment_name, 12)):
        if not isinstance(value, str) or len(value) > limit or not NAME.fullmatch(value):
            raise ValueError(f"{label} must use lowercase letters, digits and single internal hyphens (maximum {limit} characters)")
    # 実構築と同じ名前を必ず使います。長い入力を切り詰めて別名を作るのではなく、
    # Broker CA の ca-<stem>-credentials が32文字以内になる入力を事前に選んでもらいます。
    # このstem上限ならKey Vaultとハイフンを除くStorage名の制約も満たします。
    stem = f"{environment_name}-{product_name}"
    if len(stem) > 17:
        raise ValueError("environment-name and product-name combined as environment-product must fit within 17 characters for the credentials Container App; shorten either name")
    if not isinstance(api_center_location, str) or not REGION.fullmatch(api_center_location):
        raise ValueError("api-center-location must be an Azure region code")
    if (not isinstance(public_host_name, str) or len(public_host_name) > 253
            or len(public_host_name.split('.')) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                   for part in public_host_name.split('.'))):
        raise ValueError("public-host-name must be the approved lowercase public DNS hostname")
    return {
        "WORKFORCE_REGISTRATION_RESOURCE_STEM": stem,
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


def prepare_registration(
    subscription_id: str,
    product_name: str,
    environment_name: str,
    api_center_location: str = "eastus",
    public_host_name: str = "gateway.example.com",
) -> dict:
    """実構築と同じBicep式をローカル実行し、確定した名前・リダイレクトURIを返す。"""
    public_inputs = registration_inputs(subscription_id, product_name, environment_name,
                                        api_center_location, public_host_name)
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
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--environment-name", required=True)
    parser.add_argument("--api-center-location", default="eastus")
    parser.add_argument("--public-host-name", default="gateway.example.com",
                        help="API Gatewayの公開FQDN。例: gateway.example.com")
    args = parser.parse_args(argv)
    try:
        names = prepare_registration(args.subscription_id, args.product_name,
                                     args.environment_name, args.api_center_location,
                                     args.public_host_name)
        print(json.dumps(names, ensure_ascii=False, indent=2))
        return 0
    except ValueError as error:
        print("Registration preparation failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
