"""FoundryのChat保存禁止対象を選び、同名の本文編集部品を展開する。"""
from __future__ import annotations

from .apim_audit import CORE_CHAT_STORE_DISABLED_MARKER, render_audit_policy
from .paths import REPOSITORY


CHAT_STORE_FRAGMENT_ID = "core-chat-store-disabled"
_DIRECTORY = REPOSITORY / "apps/gateway/apim/policies/fragments/request" / CHAT_STORE_FRAGMENT_ID


def chat_store_disabled_operations(api: dict, *, generated_mcp: bool = False) -> list[str]:
    """Bicepと同じ検証済みの操作宣言から選ぶ。API名やモデル表は参照しない。"""
    if generated_mcp or api.get("apiType", "http") != "http":
        return []
    constraints = api["authorization"]["operationConstraints"]
    return [operation["name"] for operation in api["operations"]
            if operation["method"] == "POST" and operation["urlTemplate"] == "/chat/completions"
            and constraints[operation["name"]]["kind"] == "bodyModel"
            and operation.get("backendOverride", api)["backendService"] == "foundryOpenAi"]


def render_chat_store_fragment() -> str:
    """同名XMLとC#を、説明コメントを含む一つのフラグメントへ展開する。"""
    template = (_DIRECTORY / (CHAT_STORE_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_CHAT_STORE_DISABLED_MARKER) != 1:
        raise ValueError("Chat store processing needs one direct C# source insertion point")
    return render_audit_policy(template)
