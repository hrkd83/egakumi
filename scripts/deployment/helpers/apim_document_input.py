"""Document解析の入力設定確認を、同名XMLとC#から生成する。"""
from __future__ import annotations

from .apim_audit import CORE_DOCUMENT_INPUT_VALIDATION_MARKER, render_audit_policy
from .paths import REPOSITORY


DOCUMENT_INPUT_VALIDATION_FRAGMENT_ID = "core-document-input-validation"
_DIRECTORY = REPOSITORY / "apps/apim/policies/fragments/request" / DOCUMENT_INPUT_VALIDATION_FRAGMENT_ID


def render_document_input_validation_fragment() -> str:
    """説明コメント付きのAzure用部品を生成する。本文の解析・変換は行わない。"""
    template = (_DIRECTORY / (DOCUMENT_INPUT_VALIDATION_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_DOCUMENT_INPUT_VALIDATION_MARKER) != 1:
        raise ValueError("Document input validation needs one direct C# source insertion point")
    return render_audit_policy(template)
