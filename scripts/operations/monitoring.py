#!/usr/bin/env python3
"""既存Action groupの通知先、または既存アラート一規則の閾値を対象限定で更新する。"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import copy
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import re
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.common import OperationContext, OperationError, poll_delay


APIS = {"recipients": "2023-01-01", "metric": "2018-03-01", "log": "2023-12-01"}
METRIC_RULES = {"law-export-failed", "audit-blob-availability", "audit-eus2-blob-availability",
                "agw-unhealthy-backend", "firewall-unhealthy", "apim-capacity-high"}
LOG_RULES = {"audit-write-failed", "appi-ingestion-high", "api-failure-rate"}
# ActionGroupはPUTだけが通知先を更新する。ログ・数値アラートはcriteriaだけPATCHする。
# https://github.com/Azure/azure-rest-api-specs/blob/main/specification/monitor/resource-manager/Microsoft.Insights/Insights/stable/2023-01-01/actionGroups_API.json
# https://github.com/Azure/azure-rest-api-specs/blob/main/specification/monitor/resource-manager/Microsoft.Insights/Insights/stable/2023-12-01/scheduledQueryRule_API.json


def _etag(response: dict) -> str | None:
    """ETagが返る場合だけ条件付き更新に使い、不正な値から欠落扱いへ降格しない。"""
    headers = response.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise OperationError("Invalid monitoring concurrency metadata")
    values = [v for k, v in headers.items() if str(k).casefold() == "etag"]
    if len(values) > 1:
        raise OperationError("Duplicate monitoring ETag headers")
    body = response.get("body") or {}
    if "etag" in body:
        values.append(body["etag"])
    if not values:
        return None
    if (any(not isinstance(v, str) or len(v) > 512
            or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', v)
            or v.strip('"') == "*" for v in values)
        or len(set(values)) != 1):
        raise OperationError("Invalid monitoring concurrency metadata")
    return values[0]


def _read(context, target: str, api: str, resource_type: str, *, allow_pending: bool = False) -> dict:
    """指定した既存監視資源だけを読み、ID・種類の一致を要求する。"""
    response = context.request("GET", target, api)
    value = response.get("body")
    if (response.get("statusCode") != 200 or not isinstance(value, dict)
        or str(value.get("id", "")).casefold() != target.casefold()
        or str(value.get("type", "")).casefold() != resource_type.casefold()
        or not isinstance(value.get("properties"), dict)):
        raise OperationError("Monitoring resource identity could not be verified")
    state = value["properties"].get("provisioningState", "Succeeded")
    if state != "Succeeded" and not (allow_pending and state in ("Updating", "Creating", "Accepted")):
        raise OperationError("The monitoring resource has an unresolved operation")
    return response


def _writable(value: dict, kind: str) -> dict:
    """目的外の設定を保持し、読み取り専用の管理属性だけを除く。"""
    result = {k: copy.deepcopy(value[k]) for k in ("location", "tags", "kind", "identity", "properties") if k in value}
    props = result["properties"]
    for key in ("provisioningState", "createdWithApiVersion", "isLegacyLogAnalyticsRule",
                "isWorkspaceAlertsStorageConfigured", "lastUpdatedTime", "isMigrated"):
        props.pop(key, None)
    if kind == "recipients":
        for key, receivers in props.items():
            if key.endswith("Receivers") and isinstance(receivers, list):
                for receiver in receivers:
                    if isinstance(receiver, dict):
                        receiver.pop("status", None)
    identity = result.get("identity")
    if isinstance(identity, dict):
        identity.pop("principalId", None)
        identity.pop("tenantId", None)
        for attributes in identity.get("userAssignedIdentities", {}).values():
            if isinstance(attributes, dict):
                attributes.pop("principalId", None)
                attributes.pop("clientId", None)
    return result


def _scope(context, layer: str) -> str:
    """設定済みの所有RGから参照範囲を組み立てる。別Subscriptionを受けない。"""
    return f"/subscriptions/{context.config['subscriptionId']}/resourceGroups/{context.config['resourceGroupNames'][layer]}"


def _expected_scope(context, rule: str) -> str:
    """IaCで定義した各監視規則の対象を現在のstateと命名から固定する。"""
    stem = context.config["resourceStem"]
    if rule in ("audit-blob-availability", "audit-eus2-blob-availability"):
        key = "collectJapan" if rule == "audit-blob-availability" else "collectFoundry"
        return context.state["storage"][key]["id"] + "/blobServices/default"
    if rule == "agw-unhealthy-backend":
        return _scope(context, "nw") + "/providers/Microsoft.Network/applicationGateways/agw-" + stem
    if rule == "firewall-unhealthy":
        return _scope(context, "nw") + "/providers/Microsoft.Network/azureFirewalls/afw-" + stem
    if rule == "apim-capacity-high":
        return context.service_id("apim")
    return _scope(context, "monitor") + "/providers/Microsoft.OperationalInsights/workspaces/law-" + stem


def _receivers(context, current: list) -> list:
    """configの通知先一覧を使い、同じ宛先の既存名と通知形式を保持する。"""
    addresses = context.config["monitoring"]["operatorEmailAddresses"]
    if (not addresses or len({v.casefold() for v in addresses}) != len(addresses)
        or any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", v) for v in addresses)):
        raise OperationError("Configured email recipients are invalid")
    if any(not isinstance(item, dict) or not isinstance(item.get("emailAddress"), str)
           or not isinstance(item.get("name"), str) for item in current):
        raise OperationError("Existing email recipients could not be verified")
    existing = {item["emailAddress"]: item for item in current}
    if len(existing) != len(current) or len({item["name"] for item in current}) != len(current):
        raise OperationError("Existing email recipients are ambiguous")
    used = {existing[address]["name"] for address in addresses if address in existing}
    result = []
    for index, address in enumerate(addresses):
        if address in existing:
            result.append(copy.deepcopy(existing[address]))
            continue
        name = f"operator-{index}"
        while name in used:
            name += "-new"
        used.add(name)
        result.append({"name": name, "emailAddress": address, "useCommonAlertSchema": True})
    return result


def _receiver_states_preserved(original: dict, desired: dict, actual: dict) -> bool:
    """変更しない受信者の無効化・購読解除状態も、読戻しで保持を確認する。"""
    for channel, receivers in original["properties"].items():
        if not channel.endswith("Receivers") or not isinstance(receivers, list):
            continue
        planned = desired["properties"].get(channel, [])
        observed = actual["properties"].get(channel, [])
        for receiver in receivers:
            if not isinstance(receiver, dict) or "status" not in receiver:
                continue
            configuration = {k: v for k, v in receiver.items() if k != "status"}
            if configuration in planned and not any(
                isinstance(item, dict) and item.get("name") == receiver.get("name")
                and item.get("status") == receiver["status"] for item in observed
            ):
                return False
    return True


def _pause(delay, deadline, sleep, monotonic) -> bool:
    """Retry-Afterを短縮せず、期限内だけ最大60秒ずつ待つ。"""
    if delay >= deadline - monotonic():
        return False
    while delay > 0:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        seconds = min(delay, remaining, 60)
        sleep(seconds)
        delay -= seconds
    return monotonic() < deadline


def operate(context, action: str, *, rule_kind: str | None = None, rule: str | None = None,
            threshold: float | None = None, execute: bool = False,
            sleep=time.sleep, monotonic=time.monotonic, timeout: float = 300) -> dict:
    """通知先または一条件の閾値だけを変更し、その他の設定保持を読戻しで確認する。

    APIがETagを返さない場合は直前GET比較を使う。外部の管理者との原子的な
    競合防止を保証しないため、同時保守を避ける。通知テストは送信しない。
    """
    stem = context.config["resourceStem"]
    group = _scope(context, "monitor") + "/providers/Microsoft.Insights/actionGroups/ag-" + stem + "-ops"
    if action == "recipients":
        if any(v is not None for v in (rule_kind, rule, threshold)):
            raise OperationError("Recipient updates do not accept alert condition options")
        kind, target, resource_type = action, group, "Microsoft.Insights/actionGroups"
    elif (action == "threshold" and rule_kind in ("metric", "log")
          and rule in (METRIC_RULES if rule_kind == "metric" else LOG_RULES)
          and type(threshold) in (int, float) and math.isfinite(threshold) and threshold >= 0):
        kind = rule_kind
        resource_type = "Microsoft.Insights/" + ("metricAlerts" if kind == "metric" else "scheduledQueryRules")
        target = _scope(context, "monitor") + "/providers/" + resource_type + "/" + ("ma-" if kind == "metric" else "la-") + stem + "-" + rule
    else:
        raise OperationError("Select an existing rule and a finite nonnegative threshold")
    api = APIS[kind]
    context.verify_resource(target, api, expected_type=resource_type)
    observed = _read(context, target, api, resource_type)
    value = observed["body"]
    before = _writable(value, kind)
    desired = copy.deepcopy(before)
    if action == "recipients":
        old = before["properties"].get("emailReceivers")
        if not isinstance(old, list):
            raise OperationError("Existing email recipients could not be verified")
        desired["properties"]["emailReceivers"] = _receivers(context, old)
        previous, expected = old, desired["properties"]["emailReceivers"]
        method, body = "PUT", desired
    else:
        properties = before["properties"]
        scopes = properties.get("scopes")
        if (not isinstance(scopes, list) or len(scopes) != 1
            or str(scopes[0]).casefold() != _expected_scope(context, rule).casefold()):
            raise OperationError("Alert scope differs from the configured owned resource")
        actions = properties.get("actions")
        groups = ([item.get("actionGroupId") for item in actions] if kind == "metric" and isinstance(actions, list) and all(isinstance(item, dict) for item in actions)
                  else actions.get("actionGroups", []) if isinstance(actions, dict) else [])
        if not isinstance(groups, list) or group.casefold() not in {str(item).casefold() for item in groups}:
            raise OperationError("The alert does not reference the owned action group")
        criteria = desired["properties"].get("criteria", {}).get("allOf")
        if (not isinstance(criteria, list) or len(criteria) != 1 or not isinstance(criteria[0], dict)
            or type(criteria[0].get("threshold")) not in (int, float)
            or not math.isfinite(criteria[0]["threshold"])
            or kind == "metric" and criteria[0].get("criterionType") != "StaticThresholdCriterion"
            or kind == "log" and value.get("kind") != "LogAlert"):
            raise OperationError("Select a single existing static alert condition")
        previous, expected = criteria[0]["threshold"], threshold
        criteria[0]["threshold"] = threshold
        method, body = "PATCH", {"properties": {"criteria": desired["properties"]["criteria"]}}
    etag = _etag(observed)
    receipt = {"status": "planned", "action": action, "resourceId": target,
               "before": previous, "expected": expected, "changed": before != desired,
               "ifMatchAvailable": etag is not None, "nonAtomicComparison": True,
               "requestSubmitted": False, "requestAttempted": False, "requestAccepted": False,
               "notificationTestSent": False, "runtimeVerified": False,
               "originalSettingsSha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()}
    context.record("monitoring", receipt)
    if not execute or before == desired:
        receipt["status"] = "planned" if not execute else "unchanged"
        if execute:
            context.record("monitoring", receipt)
        return receipt
    fresh = _read(context, target, api, resource_type)
    if fresh["body"] != value or _etag(fresh) != etag:
        raise OperationError("Monitoring settings changed; prepare a new plan")
    context.record("monitoring", {**receipt, "status": "requesting", "requestAttempted": True})
    updated = context.request(method, target, api, body=body, headers={"If-Match": etag} if etag else None)
    receipt.update(requestAttempted=True, requestSubmitted=True, httpStatus=updated.get("statusCode"))
    if updated.get("statusCode") not in (200, 201, 202):
        context.record("monitoring", {**receipt, "status": "rejected"})
        raise OperationError("Monitoring update was rejected; inspect current state")
    receipt["requestAccepted"] = True
    context.record("monitoring", {**receipt, "status": "accepted"})
    deadline = monotonic() + timeout
    delay = poll_delay(updated, default=0)
    for _ in range(60):
        if not _pause(delay, deadline, sleep, monotonic):
            break
        response = _read(context, target, api, resource_type, allow_pending=True)
        actual = response["body"]
        readback = _writable(actual, kind)
        if readback not in (before, desired):
            raise OperationError("Monitoring settings changed outside the requested difference")
        if readback == desired and actual["properties"].get("provisioningState", "Succeeded") == "Succeeded":
            if action == "recipients" and not _receiver_states_preserved(value, desired, actual):
                raise OperationError("An unchanged notification receiver state changed")
            receipt.update(status="applied", requestSubmitted=True)
            context.record("monitoring", receipt)
            return receipt
        delay = poll_delay(response)
    raise OperationError("Monitoring readback is pending or differs; do not repeat the change blindly")


def main(argv=None) -> int:
    """実値をstdoutへ出さず、保護記録と固定の結果要約を使う。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--action", required=True, choices=("recipients", "threshold"))
    parser.add_argument("--rule-kind", choices=("metric", "log"))
    parser.add_argument("--rule")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    context = None
    try:
        context = OperationContext.load(args.config)
        with context.locked() if args.execute else nullcontext():
            result = operate(context, args.action, rule_kind=args.rule_kind, rule=args.rule,
                             threshold=args.threshold, execute=args.execute)
        print(json.dumps({"status": result["status"], "changed": result["changed"]}))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        if context is not None:
            try:
                context.record("monitoring", {"status": "failed", "execute": args.execute,
                                              "reason": "inspect-operation-and-current-settings"})
            except (OperationError, ValueError, OSError, TypeError):
                pass
        print("Monitoring maintenance stopped; inspect the protected operation records.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
