"""同名のCore診断設定を、既存の転送先・分類を失わずに配備する純粋な準備処理。"""
from __future__ import annotations

import copy
import re

from .apim_naming import apim_name


API = "2021-05-01-preview"
WRITABLE = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "eventHubName",
            "serviceBusRuleId", "marketplacePartnerId", "logAnalyticsDestinationType", "logs", "metrics"}
SINKS = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "serviceBusRuleId", "marketplacePartnerId"}


class DiagnosticPreservationError(ValueError):
    """読めない設定や曖昧な転送拡大を、新規・空設定へ置き換えずに停止する。"""


def _need(value, code):
    if not value:
        raise DiagnosticPreservationError("Core diagnostic preservation: " + code)


def _missing(response):
    """RP未登録や不明な404を、確認できた親・設定の不在と混同しない。"""
    _need(isinstance(response, dict) and response.get("statusCode") == 404, "missing-status")
    body = response.get("body")
    error = body.get("error", body) if isinstance(body, dict) else None
    _need(isinstance(error, dict) and error.get("code") in {"ResourceNotFound", "ResourceGroupNotFound", "ParentResourceNotFound", "NotFound"}, "unconfirmed-404")
    return True


def _same(actual, expected):
    return isinstance(actual, str) and isinstance(expected, str) and actual.casefold() == expected.casefold()


def _entries(value, kind):
    """APIのnull既定値を分類し、分類の重複・不正型を黙って捨てない。"""
    if value is None:
        return {}
    _need(isinstance(value, list) and len(value) <= 256, "category-list")
    result = {}
    for item in value:
        _need(isinstance(item, dict) and set(item) <= {"category", "categoryGroup", "enabled", "retentionPolicy", "timeGrain"}, "category-shape")
        _need(type(item.get("enabled")) is bool, "category-enabled")
        selectors = [key for key in ("category", "categoryGroup") if item.get(key) is not None]
        _need(len(selectors) == 1 and (kind == "logs" or selectors == ["category"]), "category-selector")
        selector = selectors[0]
        _need(isinstance(item[selector], str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", item[selector]), "category-name")
        _need(kind == "metrics" or item.get("timeGrain") is None, "log-timegrain")
        if item.get("timeGrain") is not None:
            _need(isinstance(item["timeGrain"], str) and re.fullmatch(r"P[0-9A-Z.]{1,60}", item["timeGrain"]), "metric-timegrain")
        retention = item.get("retentionPolicy")
        if retention is not None:
            _need(isinstance(retention, dict) and set(retention) == {"enabled", "days"}
                  and type(retention["enabled"]) is bool and type(retention["days"]) is int
                  and 0 <= retention["days"] <= 2**31 - 1, "category-retention")
        key = (selector, item[selector].casefold())
        _need(key not in result, "duplicate-category")
        result[key] = copy.deepcopy(item)
    if kind == "logs" and any(key[0] == "categoryGroup" for key in result):
        _need(len(result) == 1, "mixed-log-category-group")
        _need(next(iter(result)) == ("categoryGroup", "alllogs"), "unknown-category-group")
    return result


def normalized_properties(value):
    """書込契約の全項目を保持し、読取専用情報とnull既定値だけを比較用に整理する。"""
    _need(isinstance(value, dict) and not set(value) - WRITABLE - {"provisioningState"}, "properties-shape")
    result = {key: copy.deepcopy(item) for key, item in value.items() if key in WRITABLE and item is not None}
    for name in SINKS:
        if name in result:
            _need(isinstance(result[name], str) and re.fullmatch(r"/subscriptions/[A-Za-z0-9-]+/(?:resourceGroups/[A-Za-z0-9_.()-]+/)?providers/[A-Za-z0-9./_()-]+", result[name], re.I), "sink-resource-id")
    if "eventHubName" in result:
        _need(isinstance(result["eventHubName"], str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,260}", result["eventHubName"])
              and "eventHubAuthorizationRuleId" in result, "eventhub-pair")
    if "logAnalyticsDestinationType" in result:
        _need(result["logAnalyticsDestinationType"] in ("Dedicated", "AzureDiagnostics")
              and "workspaceId" in result, "workspace-mode")
        # ARM は既定の AzureDiagnostics を明示して返す。null/省略と同じ sink として比較する。
        if result["logAnalyticsDestinationType"] == "AzureDiagnostics":
            result.pop("logAnalyticsDestinationType")
    for kind in ("logs", "metrics"):
        if kind in result:
            entries = _entries(result[kind], kind)
            result[kind] = list(entries.values())
    return result


def _pairs(properties):
    """有効分類と送信先の組を比較し、個別fieldのunionによる新しい転送を検出する。"""
    sinks = []
    for key in SINKS:
        if properties.get(key):
            suffix = (properties.get("logAnalyticsDestinationType"),) if key == "workspaceId" else (properties.get("eventHubName"),) if key == "eventHubAuthorizationRuleId" else ()
            sinks.append((key, properties[key].casefold(), *suffix))
    result = set()
    for kind in ("logs", "metrics"):
        for key, entry in _entries(properties.get(kind), kind).items():
            if entry["enabled"]:
                name = "*" if key == ("categoryGroup", "alllogs") else key[1]
                result.update((kind, name, sink) for sink in sinks)
    return result


def merge_properties(existing, desired):
    """明示した基盤設定を優先し、既存追加分だけを転送範囲を広げず保持する。"""
    before, canonical = normalized_properties({} if existing is None else existing), normalized_properties(desired)
    result = {**copy.deepcopy(before), **copy.deepcopy(canonical)}
    for kind in ("logs", "metrics"):
        old, required = _entries(before.get(kind), kind), _entries(canonical.get(kind), kind)
        groups = [entry for key, entry in old.items() if key[0] == "categoryGroup"]
        if groups and required:
            _need(groups[0]["enabled"] and not any(key[0] == "categoryGroup" for key in required), "disabled-or-conflicting-group")
            result[kind] = list(old.values())
        elif old or required:
            merged = copy.deepcopy(old)
            for key, item in required.items():
                merged[key] = {**merged.get(key, {}), **copy.deepcopy(item)}
            result[kind] = list(merged.values())
        elif kind in before or kind in canonical:
            result[kind] = []
    old_pairs, desired_pairs, final_pairs = _pairs(before), _pairs(canonical), _pairs(result)
    allowed = old_pairs | desired_pairs
    _need(all(pair in allowed or (pair[0] == "logs" and (pair[0], "*", pair[2]) in allowed)
              for pair in final_pairs), "routing-cross-product")
    return result


def canonical_settings(config, phase, preserve_collection=False):
    """Coreの診断分類と送信先を一か所で定義し、Bicepへ同じ名前・scopeで渡す。"""
    _need(phase in ("monitoring", "core"), "phase")
    subscription, stem = config["subscriptionId"], config["resourceStem"]
    groups, location = config["resourceGroupNames"], config["location"]
    base = f"/subscriptions/{subscription}"
    resource = lambda group, provider, name: f"{base}/resourceGroups/{groups[group]}/providers/{provider}/{name}"
    workspace = resource("monitor", "Microsoft.OperationalInsights/workspaces", "law-" + stem)
    japan = resource("monitor", "Microsoft.Storage/storageAccounts", "st" + stem.replace("-", "") + "jp")
    foundry_storage = resource("monitor", "Microsoft.Storage/storageAccounts", "st" + stem.replace("-", "") + "us")
    collection = phase == "core" or preserve_collection
    settings = {}

    def add(key, parent, provider, version, name, *, logs=None, metrics=None, storage=None, dedicated=False, owner=None, owner_type=None, owner_version=None, region=location):
        props = {"workspaceId": workspace}
        if logs is not None:
            props["logs"] = [{"category": item, "enabled": True} for item in logs]
        if metrics is not None:
            props["metrics"] = [{"category": item, "enabled": True} for item in metrics]
        if storage:
            props["storageAccountId"] = storage
        if dedicated:
            props["logAnalyticsDestinationType"] = "Dedicated"
        settings[key] = {"id": parent + "/providers/Microsoft.Insights/diagnosticSettings/" + name,
                         "parentId": parent, "parentType": provider, "parentApi": version,
                         "ownerId": owner or parent, "ownerType": owner_type or provider,
                         "ownerApi": owner_version or version, "location": region,
                         "properties": props}

    for collector, account, region in (("collectJapan", japan, location), ("collectFoundry", foundry_storage, config["foundryLocation"])):
        add("blob-" + collector, account + "/blobServices/default", "Microsoft.Storage/storageAccounts/blobServices", "2023-05-01",
            f"diag-{stem}-{collector}-blob", logs=["StorageRead", "StorageWrite", "StorageDelete"], dedicated=True,
            owner=account, owner_type="Microsoft.Storage/storageAccounts", region=region)
    add("law", workspace, "Microsoft.OperationalInsights/workspaces", "2023-09-01", f"diag-{stem}-law",
        logs=["Audit"], storage=japan if collection else None, dedicated=True)
    if phase == "monitoring":
        return settings
    firewall = resource("nw", "Microsoft.Network/azureFirewalls", "afw-" + stem)
    firewall_logs = ["AZFWNetworkRule", "AZFWApplicationRule", "AZFWThreatIntel"]
    if config["network"].get("firewall", {}).get("sku", "Premium") == "Premium":
        firewall_logs.append("AZFWIdpsSignature")
    add("fw-logs", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw", logs=firewall_logs, storage=japan, dedicated=True)
    add("fw-metrics", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw-metrics", metrics=["AllMetrics"])
    for key, provider, name, version, region in (
        ("vault", "Microsoft.KeyVault/vaults", "kv-" + stem, "2023-07-01", location),
        ("foundry", "Microsoft.CognitiveServices/accounts", "aif-" + stem, "2025-06-01", config["foundryLocation"]),
        ("apim", "Microsoft.ApiManagement/service", apim_name(config), "2024-05-01", location)):
        parent = resource("svc", provider, name)
        categories = ["AuditEvent"] if key == "vault" else ["Audit"] if key == "foundry" else ["GatewayLogs", "GatewayLlmLogs"]
        if key == "apim" and any(api.get("apiType", "http") == "websocket" for api in config.get("infrastructureApiDefinitions", config["apiDefinitions"])):
            categories.append("WebSocketConnectionLogs")
        add(key + "-logs", parent, provider, version, "core-audit", logs=categories, storage=foundry_storage if key == "foundry" else japan, dedicated=key == "apim", region=region)
        add(key + "-usage" if key == "foundry" else key + "-metrics", parent, provider, version,
            "core-usage" if key == "foundry" else "core-metrics", logs=["AzureOpenAIRequestUsage"] if key == "foundry" else None,
            metrics=None if key == "foundry" else ["AllMetrics"], region=region)
    gateway = resource("nw", "Microsoft.Network/applicationGateways", "agw-" + stem)
    waf = config["gateway"]["skuName"] == "WAF_v2"
    add("agw-logs", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw",
        logs=["ApplicationGatewayFirewallLog" if waf else "ApplicationGatewayAccessLog"], storage=japan if waf else None, dedicated=True)
    if waf:
        add("agw-access", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw-access", logs=["ApplicationGatewayAccessLog"], dedicated=True)
    add("agw-metrics", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw-metrics", metrics=["AllMetrics"])
    add("portal", resource("svc", "Microsoft.App/managedEnvironments", "cae-" + stem), "Microsoft.App/managedEnvironments", "2025-01-01", "portal-logs", logs=["ContainerAppConsoleLogs", "ContainerAppSystemLogs"])
    add("activity", base, "Microsoft.Resources/subscriptions", "2022-12-01", f"diag-{stem}-activity", logs=["Administrative", "ServiceHealth", "ResourceHealth", "Alert", "Autoscale", "Security", "Recommendation", "Policy"], storage=japan)
    return settings


def prepare_settings(config, transport, phase, preserve_collection=False):
    """最初の監視PUTより先に全Core設定を照合し、その段階で必要なpropertiesだけ返す。"""
    all_settings = canonical_settings(config, "core", True)
    requested = canonical_settings(config, phase, preserve_collection)
    expected_tags = {"product": config["productName"], "environment": config["environmentName"], "azd-env-name": config["environmentName"]}
    owners, observed = {}, {}

    def owner(identifier, provider, version, region):
        if identifier.casefold() in owners:
            return owners[identifier.casefold()]
        response = transport.request("GET", identifier + "?api-version=" + version)
        _need(isinstance(response, dict) and response.get("statusCode") in (200, 404), "owner-unreadable")
        if response["statusCode"] == 404:
            _missing(response)
        body = response.get("body")
        if response["statusCode"] == 200:
            _need(isinstance(body, dict) and _same(body.get("id"), identifier) and _same(body.get("type"), provider)
                  and isinstance(body.get("tags"), dict) and all(body["tags"].get(key) == value for key, value in expected_tags.items())
                  and _same(str(body.get("location", "")).replace(" ", ""), str(region).replace(" ", "")), "owner-mismatch")
        owners[identifier.casefold()] = response["statusCode"] == 200
        return response["statusCode"] == 200

    for key, item in all_settings.items():
        if key != "activity":
            exists = owner(item["ownerId"], item["ownerType"], item["ownerApi"], item["location"])
        else:
            exists = True  # Subscription identity is fixed by the authenticated transport; its setting needs a Core-owned LAW below.
        if exists and item["ownerId"] != item["parentId"]:
            parent = transport.request("GET", item["parentId"] + "?api-version=" + item["parentApi"])
            _need(isinstance(parent, dict) and parent.get("statusCode") in (200, 404), "parent-unreadable")
            if parent["statusCode"] == 404:
                _missing(parent)
            exists = parent["statusCode"] == 200
            if exists:
                _need(isinstance(parent.get("body"), dict) and _same(parent["body"].get("id"), item["parentId"])
                      and _same(parent["body"].get("type"), item["parentType"]), "parent-identity")
        if not exists:
            observed[key] = None
            continue
        response = transport.request("GET", item["id"] + "?api-version=" + API)
        _need(isinstance(response, dict) and response.get("statusCode") in (200, 404), "setting-unreadable")
        if response["statusCode"] == 404:
            _missing(response)
            observed[key] = None
            continue
        body = response.get("body")
        _need(exists and isinstance(body, dict) and _same(body.get("id"), item["id"])
              and _same(body.get("type"), "Microsoft.Insights/diagnosticSettings")
              and _same(body.get("name"), item["id"].rsplit("/", 1)[-1]), "setting-identity")
        props = normalized_properties(body.get("properties"))
        # 管理対象の単一sinkを置換する場合は、以前のsinkもCore所有と確認する。
        # 外部追加sinkは同じfieldの別primaryと決めつけず、衝突として止める。
        for field, provider, version, region in (("workspaceId", "Microsoft.OperationalInsights/workspaces", "2023-09-01", config["location"]),
                                                  ("storageAccountId", "Microsoft.Storage/storageAccounts", "2023-05-01", item["location"])):
            previous, desired = props.get(field), item["properties"].get(field)
            if previous and desired and not _same(previous, desired):
                prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['monitor']}/providers/{provider}/"
                _need(previous.casefold().startswith(prefix.casefold()) and owner(previous, provider, version, region), "managed-primary-conflict")
        if key == "activity":
            primary = props.get("workspaceId")
            prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['monitor']}/providers/Microsoft.OperationalInsights/workspaces/"
            _need(isinstance(primary, str) and primary.casefold().startswith(prefix.casefold())
                  and owner(primary, "Microsoft.OperationalInsights/workspaces", "2023-09-01", config["location"]), "subscription-setting-owner")
        observed[key] = props
        merged = merge_properties(props, item["properties"])
        if key.startswith("agw-"):
            enabled = _entries(merged.get("logs"), "logs")
            access = any(entry["enabled"] and selector in (("category", "applicationgatewayaccesslog"), ("categoryGroup", "alllogs")) for selector, entry in enabled.items())
            _need(not access or not any(merged.get(sink) for sink in SINKS - {"workspaceId"}), "raw-access-route")
    return {key: merge_properties(observed[key], item["properties"]) for key, item in requested.items()}
