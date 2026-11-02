"""論理的な本人接続プロファイルからCMの物理プールを組み立てる。

UPNや要求ヘッダーからプロバイダー名を作らず、検証済みの構築設定だけを使う。
設定ファイルは変更しない。秘密値を読む処理もここには置かない。
"""
from __future__ import annotations

import copy
import string


def physical_providers(configuration: dict) -> list[dict]:
    result = []
    for source in configuration.get("credentialProviders", []):
        pool = source.get("pool")
        suffixes = list(string.ascii_lowercase[:pool["count"]]) if pool else [None]
        for suffix in suffixes:
            provider = copy.deepcopy(source)
            provider.pop("pool", None)
            provider["sourceName"] = source["name"]
            provider["name"] = source["name"] + ("-" + suffix if suffix else "")
            provider["displayName"] = source["displayName"] + (" " + suffix.upper() if suffix else "")
            if pool:
                provider["poolCapacity"] = pool["capacity"]
            result.append(provider)
    return result


def logical_profiles(configuration: dict) -> dict:
    selected = configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin", {}).get("providerNames", [])
    physical = physical_providers(configuration)
    result = {}
    for index, name in enumerate(selected):
        provider = next(item for item in configuration["credentialProviders"] if item["name"] == name)
        result[name] = {
            "tenantId": provider["oauthTenantId"],
            "clientId": provider["clientId"],
            "clientSecretEnv": f"OIDC_SECRET_{index}",
            "identityEndpoint": provider["identityEndpoint"],
            "identityIdProperty": provider["identityIdProperty"],
            "pools": [{"name": item["name"], "capacity": item.get("poolCapacity", 8000)}
                      for item in physical if item["sourceName"] == name],
        }
    return result


def resource_names(configuration: dict) -> dict:
    stem = configuration["resourceStem"]
    return {
        "sqlServer": f"sql-{stem}",
        "database": f"sqldb-{stem}-credentials",
        "application": f"ca-{stem}-credential-broker",
        "schemaJob": f"caj-{stem}-schema",
        "reconcileJob": f"caj-{stem}-recon",
        "runtimeIdentity": f"id-{stem}-credential-broker",
        "schemaIdentity": f"id-{stem}-credential-schema",
    }


def broker_enabled(configuration: dict) -> bool:
    return bool(configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin"))
