"""アクセス監査のqueryをLAW取り込み前に除去する。計画作成はGETだけで行う。

Core以外の診断設定・Workspace・DCRは変更しない。Storage直送は取り込み変換を
経由しないため、アクセスログだけを変換後exportへ切り替え、WAF直送は保持する。
"""
from __future__ import annotations

import copy
import json
import time
from .paths import REPOSITORY

PRIVACY = json.loads((REPOSITORY / "infra/log-privacy.json").read_text(encoding="utf-8"))
WORKSPACE_API = "2023-09-01"
DCR_API = "2023-03-11"
DIAGNOSTIC_API = "2021-05-01-preview"
EXPORT_API = "2023-09-01"
STORAGE_API = "2023-05-01"


class LogPrivacyError(ValueError):
    """内部応答を転載せず、privacy設定を確認できなかった理由を通知する。"""


def resource_ids(config: dict) -> dict:
    """確認済みの構築入力から、同じ環境の固定IDだけを導出する。"""
    subscription, stem = config["subscriptionId"], config["resourceStem"]
    groups = config["resourceGroupNames"]
    monitoring = f"/subscriptions/{subscription}/resourceGroups/{groups['monitor']}/providers/"
    workspace = monitoring + f"Microsoft.OperationalInsights/workspaces/law-{stem}"
    storage = monitoring + "Microsoft.Storage/storageAccounts/st" + stem.replace("-", "") + "jp"
    gateway = f"/subscriptions/{subscription}/resourceGroups/{groups['nw']}/providers/Microsoft.Network/applicationGateways/agw-{stem}"
    return {"workspace": workspace, "dcr": monitoring + f"Microsoft.Insights/dataCollectionRules/dcr-{stem}-workspace",
            "storage": storage, "container": storage + "/blobServices/default/containers/am-agwaccesslogs",
            "export": workspace + f"/dataExports/export-{stem}-audit",
            "diagnostic": gateway + f"/providers/Microsoft.Insights/diagnosticSettings/diag-{stem}-agw",
            "accessDiagnostic": gateway + f"/providers/Microsoft.Insights/diagnosticSettings/diag-{stem}-agw-access",
            "gateway": gateway}


def _read(transport, identifier: str, version: str, missing=False) -> dict | None:
    response = transport.request("GET", identifier + "?api-version=" + version)
    if response["statusCode"] == 404 and missing:
        return None
    if response["statusCode"] != 200 or not isinstance(response.get("body"), dict):
        raise LogPrivacyError("Log privacy resource could not be read")
    body = response["body"]
    if str(body.get("id", "")).casefold() != identifier.casefold():
        raise LogPrivacyError("Log privacy resource identity differs from the selected environment")
    return body


def existing_workspace_link(config: dict, transport) -> str:
    """再配備前に実際の関連付けを読み、親Workspace PUTで消さない。"""
    workspace = _read(transport, resource_ids(config)["workspace"], WORKSPACE_API, missing=True)
    link = workspace.get("properties", {}).get("defaultDataCollectionRuleResourceId", "") if workspace else ""
    if link and link.casefold() != resource_ids(config)["dcr"].casefold():
        raise LogPrivacyError("Workspace uses another transformation DCR; its owner must merge privacy before Core redeployment")
    return link


def desired_dcr(config: dict) -> dict:
    """Bicepと同じJSON正本から、Workspace専用変換の定義を作る。"""
    ids = resource_ids(config)
    tags = {**config.get("tags", {}), "product": config["productName"], "environment": config["environmentName"], "azd-env-name": config["environmentName"]}
    return {"location": config["location"], "kind": "WorkspaceTransforms", "tags": tags, "properties": {
        "dataSources": {}, "destinations": {"logAnalytics": [{"name": "law-core", "workspaceResourceId": ids["workspace"]}]},
        "dataFlows": [{"streams": [PRIVACY["stream"]], "destinations": ["law-core"], "transformKql": PRIVACY["transformKql"]}]}}


def _contains(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for item in expected:
            match = next((index for index, value in enumerate(remaining) if _contains(value, item)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    if isinstance(actual, str) and isinstance(expected, str) and expected.startswith("/subscriptions/"):
        return actual.casefold() == expected.casefold()
    return actual == expected


def _change(identifier, version, current, desired, label, stage, method="PUT"):
    """既存の同じ設定は再更新せず、ETagが提供される資源は競合も検知する。"""
    return {"id": identifier, "apiVersion": version, "method": method, "label": label, "stage": stage,
            "before": current, "body": desired, "required": not _contains(current, desired),
            "etag": (current or {}).get("etag")}


def workspace_plan(config: dict, transport) -> list[dict]:
    """他者DCRを置換せず、Core所有DCRとWorkspace関連付けの2項目を計画する。"""
    ids = resource_ids(config)
    workspace = _read(transport, ids["workspace"], WORKSPACE_API)
    link = workspace["properties"].get("defaultDataCollectionRuleResourceId")
    if link and link.casefold() != ids["dcr"].casefold():
        raise LogPrivacyError("Workspace already uses another transformation DCR; its owner must merge the privacy transformation")
    dcr = _read(transport, ids["dcr"], DCR_API, missing=True)
    desired = desired_dcr(config)
    if dcr and (dcr.get("kind") != "WorkspaceTransforms" or not _contains(dcr.get("properties"), desired["properties"])):
        raise LogPrivacyError("Existing Core transformation has different flows; preserve it and review the merge")
    return [_change(ids["dcr"], DCR_API, dcr, desired, "workspace-transform", "prepare"),
            _change(ids["workspace"], WORKSPACE_API, workspace,
                    {"properties": {"defaultDataCollectionRuleResourceId": ids["dcr"]}}, "workspace-link", "prepare", "PATCH")]


def plan(config: dict, transport) -> dict:
    """既存6exportと他カテゴリを残し、変更するCore資源を具体的に列挙する。"""
    ids = resource_ids(config)
    steps = workspace_plan(config, transport)
    container = _read(transport, ids["container"], STORAGE_API, missing=True)
    steps.append(_change(ids["container"], STORAGE_API, container, {"properties": {"publicAccess": "None"}}, "access-audit-container", "prepare"))
    if config["monitoring"]["auditImmutabilityMode"] != "Disabled":
        policy_id = ids["container"] + "/immutabilityPolicies/default"
        policy = _read(transport, policy_id, STORAGE_API, missing=True)
        required = config["monitoring"]["auditRetentionDays"]
        properties = (policy or {}).get("properties", {})
        if policy and (properties.get("immutabilityPeriodSinceCreationInDays", 0) < required or not properties.get("allowProtectedAppendWrites")):
            raise LogPrivacyError("Existing access audit retention does not satisfy the requested protection")
        if policy and properties.get("state") not in ("Unlocked", "Locked"):
            raise LogPrivacyError("Existing access audit retention state could not be verified")
        retained_days = properties.get("immutabilityPeriodSinceCreationInDays", required)
        steps.append(_change(policy_id, STORAGE_API, policy, {"properties": {"immutabilityPeriodSinceCreationInDays": retained_days, "allowProtectedAppendWrites": True}}, "access-audit-retention", "prepare"))
        if config["monitoring"]["auditImmutabilityMode"] == "Locked" and properties.get("state") != "Locked":
            steps.append({"id": policy_id, "apiVersion": STORAGE_API, "method": "POST", "action": "lock", "stage": "prepare", "label": "access-audit-lock", "required": True, "body": None, "before": policy})
    export = _read(transport, ids["export"], EXPORT_API)
    properties = export.get("properties", {})
    if str(properties.get("destination", {}).get("resourceId", "")).casefold() != ids["storage"].casefold() or not properties.get("enable"):
        raise LogPrivacyError("Existing audit export destination or enabled state differs")
    tables = properties.get("tableNames")
    if not isinstance(tables, list) or not all(isinstance(value, str) for value in tables):
        raise LogPrivacyError("Existing audit export tables could not be verified")
    desired_export = {"properties": {"destination": {"resourceId": ids["storage"]},
                       "tableNames": tables + ([] if PRIVACY["table"] in tables else [PRIVACY["table"]]), "enable": True}}
    steps.append(_change(ids["export"], EXPORT_API, export, desired_export, "transformed-access-export", "export"))
    diagnostic = _read(transport, ids["diagnostic"], DIAGNOSTIC_API)
    properties = diagnostic.get("properties", {})
    if str(properties.get("workspaceId", "")).casefold() != ids["workspace"].casefold():
        raise LogPrivacyError("App Gateway audit workspace differs from this Core environment")
    if properties.get("storageAccountId") and properties["storageAccountId"].casefold() != ids["storage"].casefold():
        raise LogPrivacyError("App Gateway direct audit storage differs from this Core environment")
    waf = any(item.get("category") == "ApplicationGatewayFirewallLog" and item.get("enabled") for item in properties.get("logs", []))
    if not waf:
        raise LogPrivacyError("Existing combined setting must retain enabled WAF audit collection")
    current = _read(transport, ids["accessDiagnostic"], DIAGNOSTIC_API, missing=True)
    desired_access = {"properties": {"workspaceId": ids["workspace"], "logAnalyticsDestinationType": "Dedicated",
                      "logs": [{"category": "ApplicationGatewayAccessLog", "enabled": True}]}}
    if current and (not _contains(current, desired_access) or current.get("properties", {}).get("storageAccountId")):
        raise LogPrivacyError("Existing dedicated access setting has different properties")
    steps.append(_change(ids["accessDiagnostic"], DIAGNOSTIC_API, current, desired_access, "access-law-only", "route"))
    safe_properties = copy.deepcopy(properties)
    safe_properties["logs"] = [item for item in safe_properties.get("logs", []) if item.get("category") != "ApplicationGatewayAccessLog"]
    steps.append(_change(ids["diagnostic"], DIAGNOSTIC_API, diagnostic, {"properties": safe_properties}, "keep-waf-remove-raw-access", "route"))
    others = transport.request("GET", ids["gateway"] + "/providers/Microsoft.Insights/diagnosticSettings?api-version=" + DIAGNOSTIC_API)
    if others["statusCode"] != 200:
        raise LogPrivacyError("App Gateway diagnostic destinations could not be listed")
    unchanged = [{"id": item.get("id"), "properties": copy.deepcopy(item.get("properties", {}))}
                 for item in others.get("body", {}).get("value", [])
                 if str(item.get("id", "")).casefold() not in {ids["diagnostic"].casefold(), ids["accessDiagnostic"].casefold()}]
    return {"steps": steps, "unchangedDiagnostics": unchanged,
            "diagnosticCollectionPath": ids["gateway"] + "/providers/Microsoft.Insights/diagnosticSettings?api-version=" + DIAGNOSTIC_API,
            "activationVerificationRequired": "Wait for a fresh canary to be transformed (Microsoft documents up to 60 minutes), then verify the transformed export reaches am-agwaccesslogs before the route stage.",
            "externalBoundary": "Policy-owned company audit collection is intentionally retained and belongs to a separate trust boundary. Core query stripping does not assert privacy across that destination.", "azureWrites": 0}


def apply_steps(plan: dict, transport, stages=("prepare", "export", "route"), *, sleep=time.sleep) -> dict:
    """選択した段階だけ更新し、GETで結果と保持すべき設定を検査する。ログ削除は行わない。"""
    changed = []
    for step in plan["steps"]:
        if not step["required"] or step["stage"] not in stages:
            continue
        if step["stage"] in ("export", "route"):
            for preparation in plan["steps"]:
                if preparation["label"] in ("workspace-transform", "workspace-link"):
                    prepared = _read(transport, preparation["id"], preparation["apiVersion"], missing=True)
                    if not _contains(prepared, preparation["body"]):
                        raise LogPrivacyError("Prepare the workspace privacy transformation before changing export or routing")
        if step["stage"] == "route":
            export = next(value for value in plan["steps"] if value["label"] == "transformed-access-export")
            if not _contains(_read(transport, export["id"], export["apiVersion"]), export["body"]):
                raise LogPrivacyError("Prepare the transformed export before changing direct audit routing")
        current = _read(transport, step["id"], step["apiVersion"], missing=True)
        if step.get("action") != "lock" and _contains(current, step["body"]):
            continue
        headers = {"If-Match": current["etag"]} if current and current.get("etag") else {"If-None-Match": "*"} if current is None else {}
        if step.get("action") == "lock":
            if not current or not current.get("etag") or current.get("properties", {}).get("state") != "Unlocked":
                raise LogPrivacyError("Access audit lock requires a confirmed Unlocked policy and exact ETag")
        else:
            # DCR作成だけでもLAWのETagは変わり得ます。属性が同じなら新ETagで
            # 条件付き更新し、属性そのものが変わった場合だけ再計画を要求します。
            planned = copy.deepcopy(step.get("before")); snapshot = copy.deepcopy(current)
            for value in (planned, snapshot):
                if isinstance(value, dict):
                    value.pop("etag", None)
            if snapshot != planned:
                raise LogPrivacyError("Log privacy plan changed; read a fresh plan before updating")
        path = step["id"] + ("/lock" if step.get("action") == "lock" else "") + "?api-version=" + step["apiVersion"]
        response = transport.request(step["method"], path, body=step.get("body"), headers=headers)
        if response["statusCode"] not in (200, 201, 202):
            raise LogPrivacyError("Log privacy update failed; inspect the named resource before retrying")
        for attempt in range(30):
            result = _read(transport, step["id"], step["apiVersion"], missing=True)
            if result is None or result.get("properties", {}).get("provisioningState") not in (None, "Succeeded"):
                sleep(2)
                continue
            if step.get("action") == "lock":
                complete = result.get("properties", {}).get("state") == "Locked"
            else:
                complete = _contains(result, step["body"])
                if step["label"] == "access-law-only":
                    complete = complete and not result.get("properties", {}).get("storageAccountId")
            if complete:
                if step["label"] == "workspace-link":
                    before = copy.deepcopy((current or {}).get("properties", {})); after = copy.deepcopy(result["properties"])
                    for value in (before, after):
                        value.pop("defaultDataCollectionRuleResourceId", None); value.pop("provisioningState", None)
                        # PATCHの結果としてAzureが再計算する読取専用情報。保持日数・
                        # dailyQuotaGb等の書込可能設定は比較対象から外しません。
                        value.pop("modifiedDate", None)
                        if isinstance(value.get("workspaceCapping"), dict):
                            value["workspaceCapping"].pop("quotaNextResetTime", None)
                    if before != after:
                        raise LogPrivacyError("Workspace properties changed beyond its privacy association")
                changed.append(step["label"]); break
            sleep(2)
        else:
            raise LogPrivacyError("Log privacy readback is not confirmed; do not repeat pending updates")
    if "route" in stages and plan.get("unchangedDiagnostics"):
        observed = transport.request("GET", plan["diagnosticCollectionPath"])
        if observed["statusCode"] != 200:
            raise LogPrivacyError("Unrelated diagnostic destinations could not be verified after routing")
        actual = {str(item.get("id", "")).casefold(): item.get("properties", {}) for item in observed.get("body", {}).get("value", [])}
        for expected in plan["unchangedDiagnostics"]:
            if actual.get(str(expected["id"]).casefold()) != expected["properties"]:
                raise LogPrivacyError("An unrelated diagnostic setting changed during routing; inspect it before continuing")
    return {"changed": changed, "azureWrites": len(changed), "configurationVerified": True,
            "runtimeTransformAndBlobVerified": False}


def ensure_workspace_link(config: dict, transport) -> str:
    """初期monitoring完了後に関連付けを仕上げ、次のcore配備へ実IDを引き継ぐ。"""
    apply_steps({"steps": workspace_plan(config, transport)}, transport, stages=("prepare",))
    return resource_ids(config)["dcr"]
