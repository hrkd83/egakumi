"""提供元の入力文書URLに対する個別規則の除外を、専用パスへ合成する。"""
from __future__ import annotations

import copy
import json

from .paths import REPOSITORY


SOURCE_URL_EXCLUSIONS = REPOSITORY / "apps/gateway/waf-source-url-exclusions.json"


def source_url_exclusions(profile):
    """Bicepと同じ正本を使い、用途・scope・完全パスが一致した場合だけ返す。"""
    source = json.loads(SOURCE_URL_EXCLUSIONS.read_text(encoding="utf-8"))
    matches = [item for item in source["profiles"]
               if item["name"] == profile.get("name") and item["scope"] == profile.get("scope")
               and sorted(item["paths"]) == sorted(profile.get("paths", []))]
    if len(matches) > 1:
        raise ValueError("Source URL WAF profile is duplicated")
    # 配備用の値を分離し、既存の制御項目分類や他の用途の除外を変更しません。
    return copy.deepcopy(matches[0]["exclusions"]) if matches else []
