"""準備済みの操作設定から対象を確認する部品を、Azure用XMLへ展開する。"""
from __future__ import annotations

from .apim_audit import CORE_API_TARGET_VALIDATION_MARKER, render_audit_policy
from .paths import REPOSITORY


TARGET_VALIDATION_FRAGMENT_ID = "core-api-target-validation"
_DIRECTORY = REPOSITORY / "apps/apim/policies/fragments/request" / TARGET_VALIDATION_FRAGMENT_ID


def render_target_validation_fragment() -> str:
    """同名XMLとC#を、説明コメントを含む一つのフラグメントへ展開する。"""
    template = (_DIRECTORY / (TARGET_VALIDATION_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_API_TARGET_VALIDATION_MARKER) != 1:
        raise ValueError("Target validation needs one direct C# source insertion point")
    return render_audit_policy(template)
