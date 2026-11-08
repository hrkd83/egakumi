"""保存済みの構築識別子から、APIMの名前を一貫して求める。"""
from __future__ import annotations

import re
import secrets
import string
from collections.abc import Iterable


INSTANCE_ID = re.compile(r"[a-z0-9]{6}\Z")
RESOURCE_STEM = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


def apim_instance_id(config: dict) -> str:
    """必須の保存済みIDを検査する。欠落時の自動生成は行わない。"""
    services = config.get("services") if isinstance(config, dict) else None
    value = services.get("apimInstanceId") if isinstance(services, dict) else None
    if not isinstance(value, str) or not INSTANCE_ID.fullmatch(value):
        raise ValueError("services.apimInstanceId must contain exactly 6 lowercase letters or digits; prepare the local deployment identifier explicitly")
    return value


def apim_name(config: dict) -> str:
    """APIMだけに保存済みIDを付け、同じ入力では同じ名前を返す。"""
    identifier = apim_instance_id(config)
    stem = config.get("resourceStem")
    if stem is None:
        environment = config.get("environmentName")
        product = config.get("productName")
        if not isinstance(environment, str) or not isinstance(product, str):
            raise ValueError("APIM naming requires valid environmentName and productName")
        stem = f"{environment}-{product}"
    if not isinstance(stem, str) or len(stem) > 17 or not RESOURCE_STEM.fullmatch(stem):
        raise ValueError("APIM naming requires a valid resource stem of at most 17 characters")
    return f"apim-{stem}-{identifier}"


def generate_apim_instance_id(used_ids: Iterable[str] = ()) -> str:
    """初回準備用のIDを生成し、渡された過去のIDと重複させない。"""
    try:
        used = set(used_ids)
    except (TypeError, ValueError):
        raise ValueError("Previously used APIM identifiers must be a collection of strings") from None
    if any(not isinstance(value, str) or not INSTANCE_ID.fullmatch(value) for value in used):
        raise ValueError("Previously used APIM identifiers must contain exactly 6 lowercase letters or digits")
    alphabet = string.ascii_lowercase + string.digits
    for _ in range(128):
        value = "".join(secrets.choice(alphabet) for _ in range(6))
        if value not in used:
            return value
    raise ValueError("Could not prepare an unused APIM identifier; no input was changed")
