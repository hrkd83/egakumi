#!/usr/bin/env python3
"""config正本のモデル一配備を計画し、明示実行で容量・版・新規作成を反映する。"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import copy
from contextlib import nullcontext
import json
from pathlib import Path
import re
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.common import OperationContext, OperationError, poll_delay


API = "2025-06-01"
TYPE = "Microsoft.CognitiveServices/accounts/deployments"
# 容量PATCHはsku、作成・版変更PUTはDeploymentを受ける。変更要求は再送しない。
# https://learn.microsoft.com/rest/api/microsoftfoundry/accountmanagement/deployments/update?view=rest-microsoftfoundry-accountmanagement-2025-06-01
# https://learn.microsoft.com/rest/api/microsoftfoundry/accountmanagement/deployments/create-or-update?view=rest-microsoftfoundry-accountmanagement-2025-06-01
READ_ONLY = {"provisioningState", "capabilities", "callRateLimit", "rateLimits",
             "dynamicThrottlingEnabled", "currentCapacity"}


def _read(context, target: str) -> dict:
    """指定した一配備の存在・ID・種類だけを確認し、応答本文は表示しない。"""
    response = context.request("GET", target, API)
    if response.get("statusCode") == 404:
        return response
    value = response.get("body")
    if (response.get("statusCode") != 200 or not isinstance(value, dict)
        or str(value.get("id", "")).casefold() != target.casefold()
        or str(value.get("type", "")).casefold() != TYPE.casefold()
        or not isinstance(value.get("properties"), dict)
        or not isinstance(value.get("sku"), dict)
        or not isinstance(value["properties"].get("model"), dict)):
        raise OperationError("Model deployment metadata could not be verified")
    return response


def _etag(response: dict) -> str | None:
    """返された具体的ETagだけを使用し、空値・重複・wildcardを拒否する。"""
    headers = response.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise OperationError("Invalid concurrency metadata")
    values = [v for k, v in headers.items() if str(k).casefold() == "etag"]
    body = response.get("body") or {}
    if "etag" in body:
        values.append(body["etag"])
    if not values:
        return None
    if (len([k for k in headers if str(k).casefold() == "etag"]) > 1
        or any(not isinstance(v, str) or len(v) > 512
               or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', v)
               or v.strip('"') == "*"
               for v in values) or len(set(values)) != 1):
        raise OperationError("Invalid concurrency metadata")
    return values[0]


def _writable(value: dict) -> dict:
    """設定値を保持し、公開仕様の読取専用・稼働容量だけを更新本文から除く。"""
    result = {key: copy.deepcopy(value[key]) for key in ("sku", "properties", "tags") if key in value}
    properties = result["properties"]
    for key in READ_ONLY:
        properties.pop(key, None)
    properties.get("model", {}).pop("callRateLimit", None)
    if not result.get("tags"):
        result.pop("tags", None)
    return result


def _contains(actual, expected) -> bool:
    """新規配備は要求値をすべて確認し、サービスが補う既定値を許す。"""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _contains(actual[k], v) for k, v in expected.items())
    return actual == expected


def _summary(value: dict | None) -> dict | None:
    """計画・復旧で必要なモデル識別と採用値だけを保護記録へ渡す。"""
    if value is None:
        return None
    properties = value.get("properties", {})
    model = properties.get("model", {})
    return {"model": {k: model.get(k) for k in ("format", "name", "version")},
            "sku": copy.deepcopy(value.get("sku")),
            "versionUpgradeOption": properties.get("versionUpgradeOption")}


def _pause(delay, deadline, sleep, monotonic) -> bool:
    """待機指示を短縮せず、期限内だけ最大60秒ずつ待って次のGETを許す。"""
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


def operate(context, name: str, intent: str, *, allow_version_change: bool = False,
            execute: bool = False, sleep=time.sleep, monotonic=time.monotonic,
            timeout: float = 900) -> dict:
    """一配備を照合し、計画または一変更要求と有限の読戻しを実施する。

    configのモデル名・形式・SKUを既存配備の別用途へ置換しない。ETagのないAPIは
    直前比較で停止条件を設けるが、外部操作との原子的競合防止は保証しない。
    """
    rows = [row for row in context.config["services"]["modelDeployments"] if row["name"] == name]
    if (len(rows) != 1 or intent not in ("create", "update")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name)):
        raise OperationError("Select one configured deployment and an explicit intent")
    row = rows[0]
    account = context.service_id("foundry")
    parent = context.verify_resource(account, API, expected_type="Microsoft.CognitiveServices/accounts")
    if (not isinstance(parent.get("properties"), dict)
        or parent["properties"].get("provisioningState") != "Succeeded"):
        raise OperationError("The Foundry account is not ready")
    target = account + "/deployments/" + name
    observed = _read(context, target)
    current = observed.get("body") if observed["statusCode"] == 200 else None
    if (intent == "create") != (current is None):
        raise OperationError("Create or update intent differs from the current deployment")
    desired = {"sku": {"name": row["skuName"], "capacity": row["capacity"]},
               "properties": {"model": {"format": row["modelFormat"], "name": row["modelName"],
                                         "version": row["modelVersion"]},
                              "versionUpgradeOption": row["versionUpgradeOption"]}}
    method, body = "PUT", desired
    if current is not None:
        properties = current.get("properties", {})
        model = properties.get("model", {})
        if properties.get("provisioningState") != "Succeeded":
            raise OperationError("Reconcile the existing model operation before updating")
        if (model.get("format") != row["modelFormat"] or model.get("name") != row["modelName"]
            or current.get("sku", {}).get("name") != row["skuName"]
            or properties.get("versionUpgradeOption") != row["versionUpgradeOption"]):
            raise OperationError("Existing model identity, SKU or upgrade policy differs")
        version_changed = model.get("version") != row["modelVersion"]
        if version_changed and not allow_version_change:
            raise OperationError("Version changes require an explicit version-change option")
        if version_changed and properties.get("dynamicThrottlingEnabled") not in (None, False):
            # 2025-06-01ではreadOnlyだが、非既定の既存動作をPUTで保持できると仮定しない。
            raise OperationError("The existing dynamic throttling setting cannot be safely preserved by a version PUT")
        desired = _writable(current)
        desired["sku"]["capacity"] = row["capacity"]
        desired["properties"]["model"]["version"] = row["modelVersion"]
        method = "PUT" if version_changed else "PATCH"
        body = copy.deepcopy(desired) if version_changed else {"sku": desired["sku"]}
        if version_changed and "currentCapacity" in properties:
            # 仕様上書込可能な値はPUTでも保持する。読戻しでは稼働容量と設定上限を分ける。
            body["properties"]["currentCapacity"] = properties["currentCapacity"]
    changed = current is None or _writable(current) != desired
    etag = _etag(observed)
    receipt = {"status": "planned", "resourceId": target, "intent": intent,
               "method": method, "changed": changed, "before": _summary(current),
               "expected": _summary(desired), "ifMatchAvailable": etag is not None,
               "nonAtomicComparison": True, "requestSubmitted": False,
               "requestAttempted": False, "requestAccepted": False, "runtimeVerified": False}
    context.record("model", receipt)
    if not execute or not changed:
        receipt["status"] = "planned" if not execute else "unchanged"
        if execute:
            context.record("model", receipt)
        return receipt
    fresh = _read(context, target)
    if (fresh.get("statusCode") != observed.get("statusCode") or
        current is not None and (fresh.get("body") != current or _etag(fresh) != etag)):
        raise OperationError("Model changed after comparison; prepare a new plan")
    headers = {"If-None-Match": "*"} if current is None else {"If-Match": etag} if etag else None
    # 条件ヘッダーのAPI側保証に依存せず、直前GETと最後の設定照合も必須にする。
    context.record("model", {**receipt, "status": "requesting", "requestAttempted": True})
    updated = context.request(method, target, API, body=body, headers=headers)
    receipt.update(requestAttempted=True, requestSubmitted=True, httpStatus=updated.get("statusCode"))
    if updated.get("statusCode") not in (200, 201, 202):
        context.record("model", {**receipt, "status": "rejected"})
        raise OperationError("Model update was rejected; inspect current state before retrying")
    receipt["requestAccepted"] = True
    context.record("model", {**receipt, "status": "accepted"})
    deadline = monotonic() + timeout
    delay = poll_delay(updated, default=0)
    for _ in range(180):
        if not _pause(delay, deadline, sleep, monotonic):
            break
        readback = _read(context, target)
        value = readback.get("body") if readback["statusCode"] == 200 else None
        state = (value or {}).get("properties", {}).get("provisioningState")
        if state == "Succeeded":
            actual = _writable(value)
            matches = _contains(actual, desired) if current is None else actual == desired
            if not matches and (current is None or actual != _writable(current)):
                raise OperationError("Model readback differs from the requested settings")
            if (current is not None and "dynamicThrottlingEnabled" in current["properties"]
                and value["properties"].get("dynamicThrottlingEnabled") != current["properties"]["dynamicThrottlingEnabled"]):
                raise OperationError("The existing dynamic throttling setting changed")
            if matches:
                receipt.update(status="applied", requestSubmitted=True, after=_summary(value))
                context.record("model", receipt)
                return receipt
        elif not (current is None and readback["statusCode"] == 404) and state not in ("Accepted", "Creating", "Updating", "Moving"):
            raise OperationError("Model operation is not confirmed successful")
        delay = poll_delay(readback)
    raise OperationError("Model operation is pending; do not repeat the change blindly")


def main(argv=None) -> int:
    """秘密や実資源IDを表示せず、計画・変更結果の要約だけを返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--intent", required=True, choices=("create", "update"))
    parser.add_argument("--allow-version-change", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    context = None
    try:
        context = OperationContext.load(args.config)
        with context.locked() if args.execute else nullcontext():
            result = operate(context, args.name, args.intent, allow_version_change=args.allow_version_change,
                             execute=args.execute)
        print(json.dumps({"status": result["status"], "changed": result["changed"]}))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        if context is not None:
            try:
                context.record("model", {"status": "failed", "execute": args.execute,
                                         "reason": "inspect-operation-and-current-model"})
            except (OperationError, ValueError, OSError, TypeError):
                pass
        print("Model maintenance stopped; inspect the protected operation records.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
