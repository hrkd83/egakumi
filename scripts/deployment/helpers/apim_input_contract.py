"""操作定義から、HTTPヘッダー検査の対象操作を生成する。"""
from __future__ import annotations

from .apim_audit import render_audit_policy
from .paths import REPOSITORY


INPUT_VALIDATION_FRAGMENT_ID = "core-api-input-validation"
INPUT_VALIDATION_MARKER = "__CORE_API_INPUT_VALIDATION_CSHARP__"
_DIRECTORY = REPOSITORY / "apps/apim/policies/fragments/request" / INPUT_VALIDATION_FRAGMENT_ID


def validate_input_contract(operation: dict, api_type: str) -> None:
    """明示した検査契約だけを検査する。サービス名から対象を追加しない。"""
    if "inputContract" not in operation:
        return
    contract = operation["inputContract"]
    if api_type != "http" or not isinstance(contract, dict) or contract:
        raise ValueError("requires an empty HTTP inputContract object identifying an operation for header checks")


def input_contracts(api: dict) -> dict:
    """Bicepと同じ操作ID別の対応表を、ローカル検証用に生成する。"""
    if api.get("apiType", "http") != "http":
        return {}
    result = {}
    for operation in api["operations"]:
        if "inputContract" not in operation:
            continue
        validate_input_contract(operation, "http")
        result[operation["name"]] = {}
    return result


def render_input_validation_fragment() -> str:
    """同名XMLとC#を、説明コメントを含むAzure用フラグメントへ展開する。"""
    template = (_DIRECTORY / (INPUT_VALIDATION_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(INPUT_VALIDATION_MARKER) != 1:
        raise ValueError("Input validation needs one direct C# source insertion point")
    return render_audit_policy(template)
