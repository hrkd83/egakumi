"""公開資料で確認したモデル上限を、API・操作・モデルの組合せへ展開する。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

CAPABILITIES_FILE = Path(__file__).resolve().parents[1] / "apps/gateway/apim/model-capabilities.json"
LIMIT_KINDS = {"documented", "dynamic", "unsupported", "unverified"}


def load_model_capabilities(path=CAPABILITIES_FILE):
    """値と根拠の形式を検査し、未確認を架空の数値へ置き換えない。"""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "entries"} or value["schemaVersion"] != 1 or not isinstance(value["entries"], list):
        raise ValueError("Model capabilities have an invalid schema")
    seen = set()
    for entry in value["entries"]:
        required = {"apiIds", "operationIds", "modelId", "maxOutputTokens", "limitKind", "sourceUrls", "checkedAt"}
        if not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - {"numericMaxOutputTokens", "supportsMaximumOutput"}:
            raise ValueError("Model capability entry has invalid fields")
        for field in ("apiIds", "operationIds"):
            values = entry[field]
            if not isinstance(values, list) or not values or len(values) != len(set(values)) or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", item) for item in values):
                raise ValueError("Model capability target is invalid")
        if not isinstance(entry["modelId"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", entry["modelId"]) or entry["limitKind"] not in LIMIT_KINDS:
            raise ValueError("Model capability identity is invalid")
        maximum = entry["maxOutputTokens"]
        if entry["limitKind"] == "documented":
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                raise ValueError("Documented model capability requires a numeric limit")
        elif maximum is not None:
            raise ValueError("Variable or unsupported limits must not invent a numeric maximum")
        if "numericMaxOutputTokens" in entry and (isinstance(entry["numericMaxOutputTokens"], bool) or not isinstance(entry["numericMaxOutputTokens"], int) or maximum is None or not 1 <= entry["numericMaxOutputTokens"] <= maximum):
            raise ValueError("Numeric API limit must fit the model maximum")
        if "supportsMaximumOutput" in entry and not isinstance(entry["supportsMaximumOutput"], bool):
            raise ValueError("Maximum-output support must be explicit")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["checkedAt"] or "") or not isinstance(entry["sourceUrls"], list) or not entry["sourceUrls"]:
            raise ValueError("Model capability requires its source and verification date")
        for source in entry["sourceUrls"]:
            if not isinstance(source, str):
                raise ValueError("Model capability source is invalid")
            url = urlsplit(source)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Model capability source must use HTTPS")
        for api in entry["apiIds"]:
            for operation in entry["operationIds"]:
                key = (api, operation, entry["modelId"])
                if key in seen:
                    raise ValueError("Model capabilities must not duplicate a target")
                seen.add(key)
    return value


def apply_model_capabilities(definitions, capabilities=None, bindings=None):
    """既存profile IDを保ち、モデル別の上限と不明理由だけを付与する。"""
    capabilities = capabilities if capabilities is not None else load_model_capabilities()
    bindings = bindings or {}
    index = {(api, operation, entry["modelId"]): entry for entry in capabilities["entries"] for api in entry["apiIds"] for operation in entry["operationIds"]}
    covered = {(api, operation) for api, operation, _ in index}
    result = copy.deepcopy(definitions)
    for api in result:
        for operation, constraint in api.get("authorization", {}).get("operationConstraints", {}).items():
            if (api["name"], operation) not in covered:
                continue
            for selected in constraint.get("profiles", [constraint]):
                websocket = api.get("websocket", {})
                models = selected.get("allowedModels") or ([selected["model"]] if selected.get("model") else websocket.get("allowedModels") or websocket.get("session", {}).get("models") or [websocket.get("queryParameters", {}).get("model")])
                resolved = [bindings.get(model[2:-2], model) if isinstance(model, str) and model.startswith("__") and model.endswith("__") else model for model in models]
                if not resolved or any((api["name"], operation, model) not in index for model in resolved):
                    raise ValueError("A published model has no verified capability entry")
                limits = {model: index[(api["name"], operation, model)]["maxOutputTokens"] for model in resolved}
                selected["maxOutputTokensByModel"] = limits
                selected["outputTokenLimitKinds"] = {model: index[(api["name"], operation, model)]["limitKind"] for model in resolved}
                numbers = [number for number in limits.values() if number is not None]
                selected["maxOutputTokens"] = max(numbers) if numbers else None
            if constraint.get("profiles"):
                constraint["maxOutputTokensByModel"] = {model: limit for selected in constraint["profiles"] for model, limit in selected["maxOutputTokensByModel"].items()}
                constraint["outputTokenLimitKinds"] = {model: kind for selected in constraint["profiles"] for model, kind in selected["outputTokenLimitKinds"].items()}
                numbers = [number for number in constraint["maxOutputTokensByModel"].values() if number is not None]
                constraint["maxOutputTokens"] = max(numbers) if numbers else None
    return result
