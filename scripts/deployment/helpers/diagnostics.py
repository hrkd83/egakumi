"""同名の基盤診断設定を、既存の転送先・分類を失わずに配備する純粋な準備処理。"""
from __future__ import annotations

import copy
import re

from .apim_naming import apim_name
from .configuration import entra_app_separation_enabled
from .log_privacy import PRIVACY, audit_storage_account_name
from .portal_auth_certificate import gateway_obo_vault_name


API = "2021-05-01-preview"
FIREWALL_TRANSFORMED_URL_CATEGORIES = frozenset(category.casefold() for category in PRIVACY["firewallUrlTables"])
FIREWALL_URL_CATEGORIES = FIREWALL_TRANSFORMED_URL_CATEGORIES | {
    "azfwapplicationruleaggregation", "azurefirewallapplicationrule", "alllogs",
}
WRITABLE = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "eventHubName",
            "serviceBusRuleId", "marketplacePartnerId", "logAnalyticsDestinationType", "logs", "metrics"}
SINKS = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "serviceBusRuleId", "marketplacePartnerId"}
APIM_HTTP_LOGS = ("GatewayLogs", "GatewayLlmLogs")
NSG_LOGS = ("NetworkSecurityGroupEvent", "NetworkSecurityGroupRuleCounter")
BLOB_LOGS = ("StorageRead", "StorageWrite", "StorageDelete")


class DiagnosticPreservationError(ValueError):
    """読めない設定や曖昧な転送拡大を、新規・空設定へ置き換えずに停止する。"""


def _need(value, code):
    if not value:
        raise DiagnosticPreservationError("Infrastructure diagnostic preservation: " + code)


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


def _firewall_url_categories(properties):
    return {name for (_, name), entry in _entries(properties.get("logs"), "logs").items()
            if entry["enabled"] and name in FIREWALL_URL_CATEGORIES}


def _verify_other_log_routes(transport, item, managed_ids, sensitive_categories, code):
    """管理外の転送が正規の取り込み変換を迂回する場合、変更せず配備を停止する。"""
    prefix = item["parentId"] + "/providers/Microsoft.Insights/diagnosticSettings/"
    response = transport.request("GET", prefix.rstrip("/") + "?api-version=" + API)
    _need(isinstance(response, dict) and response.get("statusCode") == 200, "diagnostic-log-inventory")
    body = response.get("body")
    _need(isinstance(body, dict)
          and isinstance(body.get("value"), list) and not body.get("nextLink"), "diagnostic-log-inventory")
    seen = set()
    for setting in body["value"]:
        _need(isinstance(setting, dict) and isinstance(setting.get("id"), str)
              and isinstance(setting.get("name"), str) and setting["name"]
              and "/" not in setting["name"] and _same(setting.get("type"), "Microsoft.Insights/diagnosticSettings")
              and _same(setting["id"], prefix + setting["name"]), "diagnostic-log-identity")
        identifier = setting["id"].casefold()
        _need(identifier not in seen, "diagnostic-log-duplicate")
        seen.add(identifier)
        if identifier not in managed_ids:
            props = normalized_properties(setting.get("properties"))
            _need(not (_enabled_logs(props) & sensitive_categories), code)


def _enabled_logs(properties):
    return {name for (_, name), entry in _entries(properties.get("logs"), "logs").items() if entry["enabled"]}


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
    """基盤の診断分類と送信先を一か所で定義し、Bicepへ同じ名前・scopeで渡す。"""
    _need(phase in ("monitoring", "core"), "phase")
    subscription, stem = config["subscriptionId"], config["resourceStem"]
    groups, location = config["resourceGroupNames"], config["location"]
    base = f"/subscriptions/{subscription}"
    resource = lambda group, provider, name: f"{base}/resourceGroups/{groups[group]}/providers/{provider}/{name}"
    workspace = resource("monitor", "Microsoft.OperationalInsights/workspaces", "law-" + stem)
    platform_storage = resource("monitor", "Microsoft.Storage/storageAccounts", audit_storage_account_name(stem, "collectPlatform"))
    foundry_storage = resource("monitor", "Microsoft.Storage/storageAccounts", audit_storage_account_name(stem, "collectFoundry"))
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

    for collector, account, region in (("collectPlatform", platform_storage, location), ("collectFoundry", foundry_storage, config["foundryLocation"])):
        add("blob-" + collector, account + "/blobServices/default", "Microsoft.Storage/storageAccounts/blobServices", "2023-05-01",
            f"diag-{stem}-{collector}-blob", logs=BLOB_LOGS, dedicated=True,
            owner=account, owner_type="Microsoft.Storage/storageAccounts", region=region)
    add("law", workspace, "Microsoft.OperationalInsights/workspaces", "2023-09-01", f"diag-{stem}-law",
        logs=["Audit"], dedicated=True)
    if phase == "monitoring":
        return settings
    firewall = resource("nw", "Microsoft.Network/azureFirewalls", "afw-" + stem)
    firewall_logs = ["AZFWNetworkRule"]
    if config["network"].get("firewall", {}).get("sku", "Premium") == "Premium":
        firewall_logs.append("AZFWIdpsSignature")
    add("fw-logs", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw", logs=firewall_logs, storage=platform_storage, dedicated=True)
    # 既存の所有済み設定をマージする場合も、この2分類の直接転送を停止します。
    settings["fw-logs"]["properties"]["logs"].extend(
        {"category": category, "enabled": False} for category in PRIVACY["firewallUrlTables"]
    )
    add("fw-url-logs", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw-url-logs",
        logs=PRIVACY["firewallUrlTables"], dedicated=True)
    add("fw-dns-logs", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw-dns-logs",
        logs=["AZFWDnsQuery", "AZFWFqdnResolveFailure"], dedicated=True)
    add("fw-metrics", firewall, "Microsoft.Network/azureFirewalls", "2025-01-01", f"diag-{stem}-fw-metrics", metrics=["AllMetrics"])
    for suffix in ("agw", "apim", "pep", "vm", "portal", "broker", "certificate"):
        nsg = resource("nw", "Microsoft.Network/networkSecurityGroups", f"nsg-{stem}-{suffix}")
        add(f"nsg-{suffix}", nsg, "Microsoft.Network/networkSecurityGroups", "2025-01-01", "core-audit",
            logs=NSG_LOGS, storage=platform_storage)
    vaults = {"vault": f"kv-{stem}", "certificate-vault": f"kvtls-{stem}"}
    if entra_app_separation_enabled(config):
        vaults["obo-vault"] = gateway_obo_vault_name(stem)
    for key, name in vaults.items():
        vault = resource("svc", "Microsoft.KeyVault/vaults", name)
        add(f"{key}-logs", vault, "Microsoft.KeyVault/vaults", "2023-07-01", "core-audit",
            logs=["AuditEvent"], storage=platform_storage)
        if key in ("vault", "obo-vault"):
            add(f"{key}-metrics", vault, "Microsoft.KeyVault/vaults", "2023-07-01", "core-metrics", metrics=["AllMetrics"])
    certificate_storage = resource("svc", "Microsoft.Storage/storageAccounts", "st" + stem.replace("-", "") + "cert")
    add("certificate-blob-logs", certificate_storage + "/blobServices/default", "Microsoft.Storage/storageAccounts/blobServices",
        "2025-01-01", "core-audit", logs=BLOB_LOGS, storage=platform_storage, dedicated=True, owner=certificate_storage,
        owner_type="Microsoft.Storage/storageAccounts")
    registry = resource("svc", "Microsoft.ContainerRegistry/registries", "acr" + stem.replace("-", ""))
    add("acr-logs", registry, "Microsoft.ContainerRegistry/registries", "2025-11-01", "core-audit",
        logs=["ContainerRegistryLoginEvents", "ContainerRegistryRepositoryEvents"], storage=platform_storage, dedicated=True)
    sql = resource("svc", "Microsoft.Sql/servers", "sql-" + stem)
    add("sql-logs", sql + "/databases/master", "Microsoft.Sql/servers/databases", "2023-08-01", "core-audit",
        logs=["SQLSecurityAuditEvents"], storage=platform_storage, owner=sql, owner_type="Microsoft.Sql/servers")
    foundry_region = config["foundryLocation"]
    foundry = resource("svc", "Microsoft.CognitiveServices/accounts", "aif-" + stem)
    for key, name, logs in (("foundry-logs", "core-audit", ["Audit", "RequestResponse"]),
                            ("foundry-usage", "core-usage", ["AzureOpenAIRequestUsage"])):
        add(key, foundry, "Microsoft.CognitiveServices/accounts", "2025-06-01", name, logs=logs,
            storage=foundry_storage, region=foundry_region)
    add("foundry-project-logs", foundry + "/projects/proj-" + stem, "Microsoft.CognitiveServices/accounts/projects",
        "2025-06-01", "core-audit", logs=["Audit"], storage=foundry_storage, owner=foundry,
        owner_type="Microsoft.CognitiveServices/accounts", region=foundry_region)
    apim = resource("svc", "Microsoft.ApiManagement/service", apim_name(config))
    add("apim-logs", apim, "Microsoft.ApiManagement/service", "2024-05-01", "core-gateway-logs",
        logs=APIM_HTTP_LOGS, dedicated=True)
    if any(api.get("apiType", "http") == "websocket" for api in config.get("infrastructureApiDefinitions", config["apiDefinitions"])):
        add("apim-websocket", apim, "Microsoft.ApiManagement/service", "2024-05-01", "core-audit",
            logs=["WebSocketConnectionLogs"], storage=platform_storage, dedicated=True)
        settings["apim-websocket"]["properties"]["logs"].extend({"category": category, "enabled": False} for category in APIM_HTTP_LOGS)
    add("apim-metrics", apim, "Microsoft.ApiManagement/service", "2024-05-01", "core-metrics", metrics=["AllMetrics"])
    gateway = resource("nw", "Microsoft.Network/applicationGateways", "agw-" + stem)
    waf = config["gateway"]["skuName"] == "WAF_v2"
    add("agw-logs", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw",
        logs=["ApplicationGatewayFirewallLog" if waf else "ApplicationGatewayAccessLog"], dedicated=True)
    if waf:
        add("agw-access", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw-access", logs=["ApplicationGatewayAccessLog"], dedicated=True)
    add("agw-metrics", gateway, "Microsoft.Network/applicationGateways", "2025-01-01", f"diag-{stem}-agw-metrics", metrics=["AllMetrics"])
    add("portal", resource("svc", "Microsoft.App/managedEnvironments", "cae-" + stem), "Microsoft.App/managedEnvironments", "2025-01-01", "portal-logs", logs=["ContainerAppConsoleLogs", "ContainerAppSystemLogs"])
    add("broker", resource("svc", "Microsoft.App/managedEnvironments", "cae-" + stem + "-broker"),
        "Microsoft.App/managedEnvironments", "2025-07-01", "broker-logs", logs=["ContainerAppConsoleLogs", "ContainerAppSystemLogs"])
    add("certificate-logs", resource("svc", "Microsoft.App/managedEnvironments", "cae-" + stem + "-certificate"),
        "Microsoft.App/managedEnvironments", "2025-01-01", "certificate-jobs", logs=["ContainerAppConsoleLogs", "ContainerAppSystemLogs"])
    management = config.get("management", {})
    if "AutomaticByPlatform" in (management.get("patchAssessmentMode"), management.get("patchMode")):
        vm = resource("maint", "Microsoft.Compute/virtualMachines", f"vm-{stem}-mgmt")
        add("management-patch-logs", vm, "Microsoft.Compute/virtualMachines", "2024-11-01", "core-patch-logs",
            logs=["SoftwareUpdateProfile", "SoftwareUpdates"])
    add("activity", base, "Microsoft.Resources/subscriptions", "2022-12-01", f"diag-{stem}-activity", logs=["Administrative", "ServiceHealth", "ResourceHealth", "Alert", "Autoscale", "Security", "Recommendation", "Policy"], storage=platform_storage)
    return settings


def prepare_settings(config, transport, phase, preserve_collection=False):
    """最初の監視PUTより先に基盤の全診断設定を照合し、その段階で必要なpropertiesだけ返す。"""
    all_settings = canonical_settings(config, "core", True)
    requested = canonical_settings(config, phase, preserve_collection)
    expected_tags = {"product": config["productName"], "environment": config["environmentName"], "azd-env-name": config["environmentName"]}
    owners, observed = {}, {}
    archive = all_settings["blob-collectPlatform"]["ownerId"]
    archives = {all_settings[key]["ownerId"].casefold() for key in ("blob-collectPlatform", "blob-collectFoundry")}
    private_logs = {
        "agw-logs": {"applicationgatewayaccesslog", "applicationgatewayfirewalllog"},
        "agw-access": {"applicationgatewayaccesslog", "applicationgatewayfirewalllog"},
        "apim-logs": {category.casefold() for category in APIM_HTTP_LOGS},
        "law": {"audit"},
    }
    sensitive_sources = {
        "fw-logs": (FIREWALL_URL_CATEGORIES, "external-firewall-url-route"),
        **{key: (private_logs[key] | {"alllogs"}, "external-untransformed-log-route") for key in ("agw-logs", "apim-logs", "law")},
    }

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
        if key in sensitive_sources:
            categories, code = sensitive_sources[key]
            managed_ids = {setting["id"].casefold() for setting in all_settings.values() if _same(setting["parentId"], item["parentId"])}
            _verify_other_log_routes(transport, item, managed_ids, categories, code)
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
        # 管理対象の単一sinkを置換する場合は、以前のsinkも基盤の管理対象と確認する。
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
        # 既知の診断設定から既存監査Storageへ直送した分類だけ、変換後exportに切り替える。
        # 取り込み経路を共有する別の分類・送信先を意図せず外さない。
        if key in ("agw-logs", "law") and _same(props.get("storageAccountId"), archive):
            _need(_enabled_logs(props) <= private_logs[key]
                  and not any(entry["enabled"] for entry in _entries(props.get("metrics"), "metrics").values()),
                  "raw-archive-has-other-logs")
            props.pop("storageAccountId")
        observed[key] = props
        merged = merge_properties(props, item["properties"])
        if key in private_logs or key in ("agw-metrics", "apim-metrics", "apim-websocket"):
            categories = private_logs.get(key, private_logs["agw-logs"] if key.startswith("agw-") else private_logs["apim-logs"])
            active = _enabled_logs(merged) & (categories | {"alllogs"})
            _need(not active or not any(merged.get(sink) for sink in SINKS - {"workspaceId"}), "raw-log-route")
        if key in ("fw-logs", "fw-url-logs", "fw-dns-logs", "fw-metrics"):
            url_categories = _firewall_url_categories(merged)
            _need(not url_categories or (
                key == "fw-url-logs" and url_categories <= FIREWALL_TRANSFORMED_URL_CATEGORIES
                and not any(merged.get(sink) for sink in SINKS - {"workspaceId"})
            ), "raw-firewall-url-route")
        if key in ("blob-collectPlatform", "blob-collectFoundry"):
            _need(merged.get("storageAccountId", "").casefold() not in archives, "recursive-archive-route")
    return {key: merge_properties(observed[key], item["properties"]) for key, item in requested.items()}
