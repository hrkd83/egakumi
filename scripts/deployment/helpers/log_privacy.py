"""AppGWのqueryとFirewallの検査URLをLAW取り込み前に除去する。

Core以外の診断設定・Workspace・DCRは変更しない。Storage直送は取り込み変換を
経由しないため、アクセスログだけを変換後exportへ切り替え、WAF直送は保持する。
"""
from __future__ import annotations

import copy
import json
import time
from .paths import REPOSITORY

PRIVACY = json.loads((REPOSITORY / "infra/modules/monitoring/log-privacy.json").read_text(encoding="utf-8"))
WORKSPACE_API = "2023-09-01"
DCR_API = "2023-03-11"


class LogPrivacyError(ValueError):
    """内部応答を転載せず、privacy設定を確認できなかった理由を通知する。"""


def resource_ids(config: dict) -> dict:
    """確認済みの構築入力から、同じ環境の固定IDだけを導出する。"""
    subscription, stem = config["subscriptionId"], config["resourceStem"]
    groups = config["resourceGroupNames"]
    monitoring = f"/subscriptions/{subscription}/resourceGroups/{groups['monitor']}/providers/"
    workspace = monitoring + f"Microsoft.OperationalInsights/workspaces/law-{stem}"
    storage = monitoring + "Microsoft.Storage/storageAccounts/st" + stem.replace("-", "") + "jp"
    return {"workspace": workspace, "dcr": monitoring + f"Microsoft.Insights/dataCollectionRules/dcr-{stem}-workspace",
            "storage": storage, "export": workspace + f"/dataExports/export-{stem}-audit"}


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
    ids = resource_ids(config)
    workspace = _read(transport, ids["workspace"], WORKSPACE_API, missing=True)
    link = workspace.get("properties", {}).get("defaultDataCollectionRuleResourceId", "") if workspace else ""
    if link and link.casefold() != ids["dcr"].casefold():
        raise LogPrivacyError("Workspace uses another transformation DCR; its owner must merge privacy before Core redeployment")
    if workspace:
        _validate_existing_dcr(_read(transport, ids["dcr"], DCR_API, missing=True), desired_dcr(config))
    return link


def desired_dcr(config: dict) -> dict:
    """Bicepと同じJSON正本から、Workspace専用変換の定義を作る。"""
    ids = resource_ids(config)
    tags = {**config.get("tags", {}), "product": config["productName"], "environment": config["environmentName"], "azd-env-name": config["environmentName"]}
    transforms = [(PRIVACY["stream"], PRIVACY["transformKql"])]
    transforms.extend(("Microsoft-Table-" + table, PRIVACY["firewallUrlTransformKql"]) for table in PRIVACY["firewallUrlTables"])
    return {"location": config["location"], "kind": "WorkspaceTransforms", "tags": tags, "properties": {
        "dataSources": {}, "destinations": {"logAnalytics": [{"name": "law-core", "workspaceResourceId": ids["workspace"]}]},
        "dataFlows": [{"streams": [stream], "destinations": ["law-core"], "transformKql": query} for stream, query in transforms]}}


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


def _change(identifier, version, current, desired, label, method="PUT"):
    """既存の同じ設定は再更新せず、ETagが提供される資源は競合も検知する。"""
    return {"id": identifier, "apiVersion": version, "method": method, "label": label,
            "before": current, "body": desired, "required": not _contains(current, desired)}


def _validate_existing_dcr(current: dict | None, desired: dict) -> None:
    """現行資材の変換と一致することを確認する。"""
    if current is None:
        return
    properties = desired["properties"]
    if (current.get("kind") != "WorkspaceTransforms"
            or not _contains(current.get("properties"), properties)):
        raise LogPrivacyError("Existing Core transformation has different flows; preserve it and review the merge")


def workspace_plan(config: dict, transport) -> list[dict]:
    """他者DCRを置換せず、Core所有DCRとWorkspace関連付けの2項目を計画する。"""
    ids = resource_ids(config)
    workspace = _read(transport, ids["workspace"], WORKSPACE_API)
    link = workspace["properties"].get("defaultDataCollectionRuleResourceId")
    if link and link.casefold() != ids["dcr"].casefold():
        raise LogPrivacyError("Workspace already uses another transformation DCR; its owner must merge the privacy transformation")
    dcr = _read(transport, ids["dcr"], DCR_API, missing=True)
    desired = desired_dcr(config)
    _validate_existing_dcr(dcr, desired)
    return [_change(ids["dcr"], DCR_API, dcr, desired, "workspace-transform"),
            _change(ids["workspace"], WORKSPACE_API, workspace,
                    {"properties": {"defaultDataCollectionRuleResourceId": ids["dcr"]}}, "workspace-link", "PATCH")]


def _stable_properties(workspace: dict) -> dict:
    """リンク更新で変わる関連付けと読み取り専用項目を除いて比較する。"""
    properties = copy.deepcopy(workspace.get("properties", {}))
    for key in ("defaultDataCollectionRuleResourceId", "provisioningState", "modifiedDate"):
        properties.pop(key, None)
    if isinstance(properties.get("workspaceCapping"), dict):
        properties["workspaceCapping"].pop("quotaNextResetTime", None)
    return properties


def _apply_workspace_plan(steps, transport, *, sleep=time.sleep) -> None:
    for step in steps:
        if not step["required"]:
            continue
        identifier, version = step["id"], step["apiVersion"]
        current = _read(transport, identifier, version, missing=True)
        if _contains(current, step["body"]):
            continue
        before, observed = step["before"] or {}, current or {}
        if ({key: value for key, value in observed.items() if key != "etag"}
                != {key: value for key, value in before.items() if key != "etag"}):
            raise LogPrivacyError("Workspace privacy settings changed after planning")
        headers = {"If-Match": current["etag"]} if current and current.get("etag") else (
            {"If-None-Match": "*"} if current is None else {})
        response = transport.request(step["method"], identifier + "?api-version=" + version,
                                     body=step["body"], headers=headers)
        if response["statusCode"] not in (200, 201, 202):
            raise LogPrivacyError("Log privacy update failed; inspect the named resource before retrying")
        for _ in range(30):
            result = _read(transport, identifier, version, missing=True)
            complete = (result and result.get("properties", {}).get("provisioningState") in (None, "Succeeded")
                        and _contains(result, step["body"]))
            if complete:
                if (step["label"] == "workspace-link"
                        and _stable_properties(current or {}) != _stable_properties(result)):
                    raise LogPrivacyError("Workspace properties changed beyond its privacy association")
                break
            sleep(2)
        else:
            raise LogPrivacyError("Log privacy readback is not confirmed; do not repeat pending updates")


def ensure_workspace_link(config: dict, transport) -> str:
    """初期monitoring完了後に関連付けを仕上げ、次のCore配備へ実IDを引き継ぐ。"""
    _apply_workspace_plan(workspace_plan(config, transport), transport)
    return resource_ids(config)["dcr"]
