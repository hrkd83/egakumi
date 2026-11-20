"""操作の基本設定とモデル別設定を選ぶ部品を、Azure用XMLへ展開する。"""
from __future__ import annotations

from .apim_audit import CORE_API_OPERATION_SETTINGS_MARKER, render_audit_policy
from .paths import REPOSITORY


OPERATION_SETTINGS_FRAGMENT_ID = "core-api-operation-settings"
_DIRECTORY = REPOSITORY / "apps/gateway/apim/policies/fragments/request" / OPERATION_SETTINGS_FRAGMENT_ID


def render_operation_settings_fragment() -> str:
    """同名XMLとC#を、説明コメントを含む一つのフラグメントへ展開する。"""
    template = (_DIRECTORY / (OPERATION_SETTINGS_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_API_OPERATION_SETTINGS_MARKER) != 1:
        raise ValueError("Operation settings needs one direct C# source insertion point")
    return render_audit_policy(template)
