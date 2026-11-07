#!/usr/bin/env python3
"""試験前に、手元のツールとAzure側の公開メタデータを一覧にする。

標準はローカルのみで、設定の公開値と実行ツール・ライブラリの有無を調べる。
--azureを明示したときだけ、設定されたサブスクリプションのモデル一覧、利用枠、
VM SKU、プロバイダー登録状況などを読む。ログイン先が一致しなければ先へ進まない。
どちらのモードもプロバイダー登録、Entra変更、モデル配備・予約、推論は行わない。
一覧にモデルが見えることは購入資格や配備成功の証明ではない。資格情報の有効性や
本人同意など、別に確認すべき項目も結果のlimitsとして残す。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess

from integration_client import private_write
from deployment.helpers.configuration import entra_tenant_id, load_configuration_source

MODEL_TERMS = ("6.1", "embedding", "cohere-rerank", "gpt-image-2.5", "opus", "realtime", "dragon")
PROVIDERS = ("Microsoft.Network", "Microsoft.ApiManagement", "Microsoft.CognitiveServices", "Microsoft.ApiCenter",
             "Microsoft.KeyVault", "Microsoft.Compute", "Microsoft.Insights", "Microsoft.OperationalInsights", "Microsoft.Storage", "Microsoft.Web")


def read_cli(arguments, timeout=30):
    """Azure CLIの読取を有限時間で行い、失敗本文を出さず読取可否を返す。"""
    try:
        completed = subprocess.run(["az", *arguments, "--only-show-errors", "--output", "json"], capture_output=True, text=True, timeout=timeout,
                                   env={**os.environ, "AZURE_CORE_COLLECT_TELEMETRY": "false"})
    except subprocess.TimeoutExpired:
        return {"state": "unavailable", "reason": "AzureReadTimeout"}
    if completed.returncode: return {"state": "unavailable", "reason": "AzureReadFailed", "exitCode": completed.returncode}
    try: return {"state": "read", "data": json.loads(completed.stdout)}
    except ValueError: return {"state": "unavailable", "reason": "InvalidMetadataResponse"}


def selected_models(rows):
    """モデル一覧から試験に関係する公開属性・SKUだけを抜き出す。"""
    if not isinstance(rows, list): return []
    result = []
    for entry in rows:
        if not isinstance(entry, dict): continue
        model = entry.get("model", entry)
        if not isinstance(model, dict): continue
        name = model.get("name", "")
        if not isinstance(name, str) or not any(term in name.lower() for term in MODEL_TERMS): continue
        item = {key: model[key] for key in ("name", "version", "format", "lifecycleStatus", "capabilities") if key in model}
        skus = entry.get("skus", model.get("skus", []))
        if isinstance(skus, list):
            item["skus"] = [{k: sku[k] for k in ("name", "usageName", "capacity") if k in sku} for sku in skus if isinstance(sku, dict)]
        result.append(item)
    return result


def inventory(config, azure=False, reader=read_cli):
    """ローカルツール一覧を作り、azure指定時だけ一致したサブスクの公開メタデータを読む。

    読取結果は予約ではなく確認時点の情報である。購入資格・本人同意・秘密の有効性など
    この一覧だけで検査できない範囲も、出力に明記する。
    """
    result = {"checkedAt": datetime.now(timezone.utc).isoformat(), "mode": "readOnlyAzureMetadata" if azure else "localOnly",
              "resourceMutations": 0, "inferenceRequests": 0, "quotaReservation": False,
              "target": {key: config.get(key) for key in ("productName", "environmentName", "subscriptionId", "azureTenantId", "location", "foundryLocation", "apiCenterLocation")},
              "tools": {name: bool(shutil.which(name)) for name in ("az", "azd", "node", "pwsh")}, "libraries": {}}
    for name in ("httpx2", "mcp", "websockets", "grpcio", "grpcio-tools", "azure-ai-projects", "azure-ai-agentserver-responses", "azure-identity", "aiohttp"):
        try: result["libraries"][name] = metadata.version(name)
        except metadata.PackageNotFoundError: result["libraries"][name] = None
    result["target"]["entraTenantId"] = entra_tenant_id(config)
    if not azure: return result
    subscription = config["subscriptionId"]
    account = reader(["account", "show", "--subscription", subscription, "--query", "{subscription:id,tenant:tenantId,state:state}"])
    result["account"] = account
    account_data = account.get("data")
    if account.get("state") != "read" or not isinstance(account_data, dict) or account_data.get("subscription") != subscription or account_data.get("tenant") != config["azureTenantId"]:
        result["azureChecks"] = "blocked: configured subscription/tenant were not verified"
        return result
    result["regions"] = {}
    for region in dict.fromkeys((config["location"], config["foundryLocation"])):
        catalog = reader(["cognitiveservices", "model", "list", "--subscription", subscription, "--location", region])
        if catalog["state"] == "read": catalog["data"] = selected_models(catalog["data"])
        quota = reader(["cognitiveservices", "usage", "list", "--subscription", subscription, "--location", region])
        if quota["state"] == "read":
            rows = quota["data"] if isinstance(quota["data"], list) else []
            selected = []
            for entry in rows:
                if not isinstance(entry, dict) or not any(term in str(entry.get("name", "")).lower() for term in MODEL_TERMS):
                    continue
                item = {key: entry[key] for key in ("unit", "currentValue", "limit", "quotaPeriod") if key in entry}
                name = entry.get("name")
                item["name"] = ({key: name[key] for key in ("value", "localizedValue") if key in name}
                                if isinstance(name, dict) else name)
                selected.append(item)
            quota["data"] = selected
        result["regions"][region] = {"catalog": catalog, "quota": quota}
    sku = config.get("management", {}).get("vmSize")
    if sku:
        result["managementVmSku"] = reader(["vm", "list-skus", "--subscription", subscription, "--location", config["location"], "--resource-type", "virtualMachines", "--size", sku,
                                           "--query", "[].{name:name,restrictions:restrictions,locations:locations,locationInfo:locationInfo}"])
    result["providers"] = reader(["provider", "list", "--subscription", subscription, "--query", "[].{namespace:namespace,registrationState:registrationState}"])
    if result["providers"]["state"] == "read": result["providers"]["data"] = [entry for entry in result["providers"]["data"] if entry.get("namespace") in PROVIDERS]
    result["appServicePythonRuntimes"] = reader(["webapp", "list-runtimes", "--os", "linux"])
    if result["appServicePythonRuntimes"]["state"] == "read":
        rows = result["appServicePythonRuntimes"]["data"]
        result["appServicePythonRuntimes"]["data"] = [
            {key: row[key] for key in ("config", "support", "end_of_life") if key in row} if isinstance(row, dict) else row
            for row in rows if (isinstance(row, dict) and row.get("runtime") == "Python") or (isinstance(row, str) and row.startswith("PYTHON|"))]
    result["limits"] = ["Catalog visibility is not a deployment or offer entitlement check", "Quota was not reserved; availability can change before deployment",
                        "Entra consent and secret validity require separate approved GUI verification", "Marketplace purchase/terms and Speech resource-specific voice/PE behavior are not verified by this inventory"]
    return result


def main():
    """公開設定を読み、ローカルまたは明示したAzure読取の結果を保護ファイルへ保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--azure", action="store_true")
    args = parser.parse_args()
    try:
        config = load_configuration_source(args.config)
        result = inventory(config, args.azure)
        private_write(args.output, json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps({"saved": str(args.output), "mode": result["mode"], "resourceMutations": 0, "inferenceRequests": 0,
                          "regions": {region: {kind: entry["state"] for kind, entry in checks.items()} for region, checks in result.get("regions", {}).items()}}))
        return 1 if "azureChecks" in result else 0
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"succeeded": False, "failureClass": type(exc).__name__, "resourceMutations": 0})); return 2


if __name__ == "__main__": raise SystemExit(main())
