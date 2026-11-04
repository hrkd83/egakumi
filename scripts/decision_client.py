#!/usr/bin/env python3
"""Microsoft Decision API の短い正常・拒否試験を、明示した場合だけ各1回送る。

既定は通信しない計画表示です。--execute のときだけ保護 token を読み、
指定した HTTPS API へ1要求を送ります。redirect、自動再試行、モデルの
自動切替は行いません。証跡には本文・質問・回答・token を保存しません。
HTTP の拒否だけで backend 未実行や監査記録の存在を合格とせず、別照合を
pending として残します。この API は OpenAI /decisions とは別の仕様です。
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import time
import uuid

from integration_client import auth_headers, correlations, endpoint, usage_numbers
from deployment.helpers.paths import private_parent


CASES = ("normal", "invalid-question", "anonymous", "unregistered", "disallowed-model", "missing-model", "oversized")
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 65536
MODEL = "Microsoft-Decision-1"
SAFE_ERROR_CODES = {"AUTHORIZATION_DENIED", "INVALID_REQUEST", "REQUEST_TOO_LARGE"}


def body_for(case, model):
    """合成した状態に3種類の質問をまとめ、推論回数を増やさず形式を確認する。"""
    if case not in CASES or not isinstance(model, str) or not model or len(model) > 100:
        raise ValueError("Choose a bounded explicit Decision deployment and case")
    body = {
        "model": model,
        "state": {"message": "The service charged me twice. Please refund the duplicate payment.", "contact_count": 2},
        "questions": {
            "refund": {"type": "noul", "instructions": "Is the customer requesting a refund?"},
            "team": {"type": "choice", "instructions": "Which team should handle this request?",
                     "criteria": {"billing": "Payments, duplicate charges, and refunds", "engineering": "Software errors and outages", "support": "General help"}},
            "severity": {"type": "score", "instructions": "How urgent is this issue?",
                         "criteria": ["Routine", "Important", "Urgent"]},
        },
    }
    if case == "invalid-question":
        body["questions"]["refund"]["type"] = "unsupported-question-type"
    elif case == "disallowed-model":
        body["model"] = "egakumi-unapproved-decision-model"
    elif case == "missing-model":
        del body["model"]
    elif case == "oversized":
        # 余分な生成指示は使わず、同じ無害な状態を APIM の上限より1 byteだけ
        # 大きくします。正常に拒否された場合、Foundry の推論は実行されません。
        body["state"] = "x"
        padding = MAX_REQUEST_BYTES + 1 - len(encode(body))
        body["state"] += "x" * padding
    return body


def encode(body):
    """送信するJSONを決定的に符号化し、byte上限の試験を正確にする。"""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def probability(value):
    """bool、NaN、範囲外を確率として合格にしない。"""
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def distribution(value, keys):
    """全選択肢が過不足なく返り、有限な確率の和が1になることを調べる。"""
    return (isinstance(value, dict) and set(value) == set(keys)
            and all(probability(number) for number in value.values())
            and math.isclose(sum(value.values()), 1.0, rel_tol=0, abs_tol=0.0001))


def inspect_decision(value):
    """型付きの3回答を検査し、回答値の代わりに真偽と利用量だけを返す。

    request.model は配備名ですが response.model は基礎モデル名です。公式の
    大文字小文字差を許し、別モデルからの応答は合格にしません。これは少数の
    通信・形式試験であり、分類精度や確率の校正を測ったとの主張はしません。
    """
    result = {"shapeValid": False, "modelVerified": False, "noulValid": False,
              "choiceValid": False, "scoreValid": False, "expectedChoiceObserved": False, "usage": {}}
    if not isinstance(value, dict) or "error" in value:
        return result
    result["modelVerified"] = isinstance(value.get("model"), str) and value["model"].lower() == MODEL.lower()
    result["usage"] = usage_numbers(value.get("usage"))
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != {"refund", "team", "severity"}:
        return result
    if not all(isinstance(answer, dict) for answer in answers.values()):
        return result
    noul, choice, score = (answers[name] for name in ("refund", "team", "severity"))
    result["noulValid"] = noul.get("type") == "noul" and probability(noul.get("noul"))
    choices = choice.get("probabilities")
    result["choiceValid"] = (choice.get("type") == "choice" and distribution(choices, ("billing", "engineering", "support"))
                             and isinstance(choice.get("choice"), str) and choice["choice"] in choices
                             and choices[choice["choice"]] == max(choices.values())
                             and ("confidence" not in choice or probability(choice["confidence"])))
    result["expectedChoiceObserved"] = choice.get("choice") == "billing"
    levels = score.get("probabilities")
    result["scoreValid"] = (score.get("type") == "score" and distribution(levels, ("0", "1", "2"))
                            and type(score.get("score")) in (int, float) and math.isfinite(score["score"])
                            and math.isclose(score["score"], sum(int(key) * number for key, number in levels.items()), abs_tol=0.0001)
                            and ("confidence" not in score or probability(score["confidence"])))
    result["shapeValid"] = all(result[key] for key in ("modelVerified", "noulValid", "choiceValid", "scoreValid"))
    return result


def expected(case):
    """拒否の期待値を固定し、401やWAF403を不正質問の成功と取り違えない。"""
    if case == "normal":
        return (200,), None
    if case == "invalid-question":
        return (400, 422), None
    if case == "anonymous":
        return (401,), None
    if case == "oversized":
        return (413,), "REQUEST_TOO_LARGE"
    return (403,), None


async def probe(url, model, case, headers, *, transport=None, timeout=60):
    """指定ケースを一度だけ送り、機微情報を除いた照合用の証跡を返す。"""
    import httpx2
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    body = encode(body_for(case, model))
    started = time.monotonic()
    record = {"case": case, "startedAt": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4()),
              "httpAttempts": 0, "inferenceRetryCount": 0, "requestBytes": len(body),
              "status": None, "responseChecksPassed": False, "succeeded": False,
              "auditVerification": "pending", "backendInvocationVerification": "pending"}
    outgoing = {} if case == "anonymous" else dict(headers)
    outgoing.update({"Content-Type": "application/json", "Accept": "application/json", "x-ms-client-request-id": record["requestId"]})
    try:
        async with httpx2.AsyncClient(transport=transport or httpx2.AsyncHTTPTransport(retries=0),
                                     follow_redirects=False, trust_env=False, timeout=timeout) as client:
            record["httpAttempts"] = 1
            async with client.stream("POST", endpoint(url), headers=outgoing, content=body) as response:
                record.update(status=response.status_code, apimReturnedCorrelations=correlations(response.headers))
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("Response inspection limit exceeded")
                    raw.extend(chunk)
                record["responseBytes"] = len(raw)
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError):
                    value = None
                error = value.get("error") if isinstance(value, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                record["knownErrorCode"] = code if isinstance(code, str) and code in SAFE_ERROR_CODES else None
                statuses, required_code = expected(case)
                correlated = bool(record["apimReturnedCorrelations"].get("apim-request-id"))
                passed = response.status_code in statuses and correlated
                if required_code:
                    passed = passed and code == required_code
                if case == "normal":
                    record.update(inspect_decision(value))
                    passed = passed and record["shapeValid"] and record["expectedChoiceObserved"]
                record["responseChecksPassed"] = bool(passed)
                # HTTP とモデル応答の確認だけです。APIM/Foundry の監査・未実行の
                # 照合は root の受入作業として pending を維持し、偽って完了にしません。
                record["succeeded"] = bool(passed)
    except Exception as exc:
        record["failureClass"] = type(exc).__name__
    record["durationMs"] = round((time.monotonic() - started) * 1000, 2)
    return record


def open_new(path):
    """要求を送る前に証跡を確保し、既存ファイルやリンクは上書きしない。"""
    path = private_parent(Path(path))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w")


def write_new(path, record):
    """保護した新規ファイルへ、機微情報を除いた検査結果を保存する。"""
    with open_new(path) as target:
        json.dump(record, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def main():
    """計画時は秘密ファイルを読まず、実行は1ケースと新規出力を必須にする。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--case", choices=CASES, default="normal")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--token-file")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    endpoint(args.url)
    body = body_for(args.case, args.model)
    if not args.execute:
        print(json.dumps({"mode": "plan-only-no-network", "case": args.case, "requestBytes": len(encode(body)),
                          "questionCount": len(body["questions"]), "httpAttempts": 0, "inferenceRetryCount": 0}))
        return 0
    if not args.output or args.output.exists() or args.output.is_symlink():
        raise ValueError("A new evidence output is required before sending")
    if args.case != "anonymous" and not args.token_file:
        raise ValueError("An explicitly approved protected token is required")
    with open_new(args.output) as evidence:
        # 権限不足や出力先の競合を推論前に検出します。中断時も未完了の印を
        # 残し、保存に失敗したことを理由に同じ推論を再実行しないようにします。
        evidence.write(json.dumps({"case": args.case, "status": "not-completed", "succeeded": False}) + "\n")
        evidence.flush()
        headers = {} if args.case == "anonymous" else auth_headers(args.token_file)
        result = asyncio.run(probe(args.url, args.model, args.case, headers))
        evidence.seek(0)
        json.dump(result, evidence, ensure_ascii=False, indent=2, allow_nan=False)
        evidence.write("\n")
        evidence.truncate()
    print(json.dumps(result, allow_nan=False))
    return 0 if result["responseChecksPassed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"succeeded": False, "failureClass": type(exc).__name__}))
        raise SystemExit(2)
