#!/usr/bin/env python3
"""通常購読の片側キーを一度だけ再生成し、新規の保護ファイルへ保存する。"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.common import OperationContext, OperationError
from deployment.helpers.paths import private_parent


API_VERSION = "2024-05-01"
SUBSCRIPTION = re.compile(r"portal-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Microsoft Subscription Regenerate Primary/Secondary Key returns 204; List Secrets returns 200.
# https://learn.microsoft.com/rest/api/apimanagement/subscription/regenerate-primary-key?view=rest-apimanagement-2024-05-01
# https://learn.microsoft.com/rest/api/apimanagement/subscription/list-secrets?view=rest-apimanagement-2024-05-01


def _subscription(context, resource_id: str, scope: str) -> None:
    response = context.request("GET", resource_id, API_VERSION)
    value = response.get("body")
    if response.get("statusCode") != 200 or not isinstance(value, dict):
        raise ValueError("Subscription metadata unavailable")
    properties = value.get("properties")
    if (not isinstance(value.get("id"), str) or value["id"].casefold() != resource_id.casefold()
        or not isinstance(properties, dict) or not isinstance(properties.get("scope"), str)
        or properties["scope"].casefold() != scope.casefold()
        or properties.get("state") not in ("active", "suspended")):
        raise ValueError("Subscription does not match the ordinary product scope")


def _output(context, path: Path) -> Path:
    target = context.output_path(path)
    if not target.is_relative_to(context.workspace.secrets):
        raise ValueError("Use the selected secret directory")
    private_parent(target, context.workspace.repository)
    if (target.exists() or target.is_symlink()
        or os.name != "nt" and target.parent.stat().st_uid != os.geteuid()
        or not os.access(target.parent, os.W_OK | os.X_OK)):
        raise ValueError("Use a new writable protected secret output")
    return target


def _intent_path(context, binding: dict) -> Path:
    name = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    return context.output_path(context.workspace.state / "operations" / f"api-key-{name}.json")


def _intent(path: Path, binding: dict) -> dict | None:
    """未解決の要求を別プロセスの再生成へ引き継がない。秘密値は保存しない。"""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 16384
            or os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid())):
            raise ValueError("Invalid protected regeneration intent")
        value = json.load(stream)
    if (not isinstance(value, dict) or value.get("version") != 1 or value.get("binding") != binding
        or not isinstance(value.get("operationId"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["operationId"])
        or value.get("status") not in ("regeneration-intent", "completed", "current-key-recovered")):
        raise ValueError("Invalid regeneration intent binding")
    return value


def _check_intent(value: dict | None, recover: bool) -> None:
    pending = value is not None and value["status"] == "regeneration-intent"
    if recover != pending:
        raise ValueError("Unresolved regeneration requires explicit current-key recovery")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--subscription-id", required=True, help="Portalが発行した通常購読のID")
    parser.add_argument("--key", choices=("primary", "secondary"), required=True)
    parser.add_argument("--secret-output", type=Path, required=True, help="同環境secrets内の新規ファイル")
    parser.add_argument("--recover-current", action="store_true", help="未解決要求の現在値だけを取得。再生成しない")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not SUBSCRIPTION.fullmatch(args.subscription_id):
        parser.error("Only a Portal ordinary subscription identifier is accepted")

    context = None
    resource_id = None
    output = None
    phase = "preflight"
    regeneration_requested = False
    regenerated = False
    saved = False
    http_status = None
    try:
        context = OperationContext.load(args.config)
        output = _output(context, args.secret_output)
        apim = context.service_id("apim")
        resource_id = apim + "/subscriptions/" + args.subscription_id
        scope = apim + "/products/released-apis"
        binding = {"subscriptionResourceId": resource_id.casefold(), "key": args.key}
        marker = _intent_path(context, binding)
        prior = _intent(marker, binding)
        _check_intent(prior, args.recover_current)
        payload = {"subscriptionResourceId": resource_id, "key": args.key, "secretOutput": str(output),
                   "mode": "recover-current" if args.recover_current else "regenerate", "intentFile": str(marker)}
        if not args.execute:
            context.record("api-key", {**payload, "status": "planned", "azureRequests": 0,
                                       "expectedProductScope": scope, "currentMetadataVerified": False})
            print(json.dumps({"status": "planned", "key": args.key}))
            return 0

        with context.locked():
            prior = _intent(marker, binding)
            _check_intent(prior, args.recover_current)
            context.verify_resource(apim, API_VERSION, expected_type="Microsoft.ApiManagement/service")
            _subscription(context, resource_id, scope)
            # 計画後に他処理が出力先を作った場合も、変更要求の直前で停止する。
            output = _output(context, args.secret_output)
            if not args.recover_current:
                replace_completed = prior is not None
                prior = {"version": 1, "binding": binding, "operationId": uuid.uuid4().hex,
                         "status": "regeneration-intent", "regenerationOutcome": "unconfirmed"}
                # 書込前に永続化し、HTTP応答や保存結果が不明でも別CLIから再送させない。
                context.save_private(marker, prior, overwrite=replace_completed)
                context.record("api-key", {**payload, "status": "regeneration-intent", "regenerationAttemptLimit": 1})
                phase = "regeneration-requested"
                regeneration_requested = True
                operation = "regeneratePrimaryKey" if args.key == "primary" else "regenerateSecondaryKey"
                response = context.request("POST", resource_id + "/" + operation, API_VERSION)
                http_status = response.get("statusCode")
                if http_status != 204:
                    raise ValueError("Regeneration was not confirmed")
                regenerated = True
                phase = "regenerated-not-saved"
            else:
                phase = "current-key-recovery-requested"
                context.record("api-key", {**payload, "status": phase, "regenerationAttempts": 0,
                                           "originalRegenerationOutcome": "unconfirmed"})
            # 読取でもPOSTなので再送しない。失敗してもキー再生成へは戻らない。
            response = context.request("POST", resource_id + "/listSecrets", API_VERSION)
            http_status = response.get("statusCode")
            secrets = response.get("body")
            value = secrets.get(args.key + "Key") if isinstance(secrets, dict) else None
            if http_status != 200 or not isinstance(value, str) or not re.fullmatch(r"[!-~]{16,512}", value):
                raise ValueError("Regenerated key could not be retrieved")
            context.save_private(output, value + "\n", overwrite=False)
            saved = True
            phase = "current-key-recovered" if args.recover_current else "completed"
            context.save_private(marker, {**prior, "status": phase, "secretOutput": str(output),
                "regenerationOutcome": "unconfirmed" if args.recover_current else "confirmed",
                "currentKeySaved": True}, overwrite=True)
            context.record("api-key", {**payload, "status": phase, "regenerationAttempts": int(regeneration_requested),
                "secretSaved": True, "originalRegenerationOutcome": "unconfirmed" if args.recover_current else "confirmed"})
        print(json.dumps({"status": phase, "key": args.key}))
        return 0
    except (OperationError, OSError, ValueError, TypeError, KeyError):
        if context is not None:
            failure = "saved-receipt-unconfirmed" if saved else "current-key-recovery-failed" if args.recover_current else "regenerated-not-saved" if regenerated else (
                "regeneration-unconfirmed" if regeneration_requested else "preflight-failed")
            try:
                context.record("api-key", {"status": failure, "phase": phase, "key": args.key,
                    "subscriptionResourceId": resource_id, "secretOutput": str(output) if output else None,
                    "httpStatus": http_status, "regenerationAttempts": int(regeneration_requested),
                    "secretSaved": saved, "retryRegeneration": False,
                    "nextAction": "inspect-state-and-retrieve-current-key-without-regenerating"})
            except (OperationError, OSError, ValueError, TypeError):
                pass
        print("API key operation stopped; do not repeat regeneration. Inspect the protected operation record.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
