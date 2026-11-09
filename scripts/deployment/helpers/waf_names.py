"""保存済みの構築IDから、WAFの用途別リソース名を共通に求める。"""
from __future__ import annotations

from .apim_naming import apim_instance_id


GATEWAY_DATA_POLICY_KEYS = frozenset({
    "g-chat-completions", "g-responses", "g-embeddings", "g-rerank", "g-decision",
    "g-images", "g-transcribe", "g-realtime", "g-live", "g-pii-redact",
})
PORTAL_DATA_POLICY_KEYS = frozenset({
    "p-generate", "p-embeddings", "p-rerank", "p-decision", "p-images", "p-speech",
    "p-realtime", "p-document-upload", "p-pii-redact",
})
DATA_POLICY_KEYS = GATEWAY_DATA_POLICY_KEYS | PORTAL_DATA_POLICY_KEYS
POLICY_KEYS = DATA_POLICY_KEYS | {
    "g-base", "g-auth", "g-speech", "g-document-upload", "p-base", "p-auth",
}


def policy_name(config: dict, key: str) -> str:
    """Gatewayはg、Portalはpを使い、同じ構築入力では名前を変えない。"""
    if not isinstance(key, str) or key not in POLICY_KEYS:
        raise ValueError("WAF policy key must be one of the defined Gateway or Portal purposes")
    return f"wafpol-{key}-{apim_instance_id(config)}"


def policy_id(config: dict, key: str) -> str:
    """検証済み構成のネットワークRGだけに、用途別ポリシーIDを組み立てる。"""
    name = policy_name(config, key)
    return (f"/subscriptions/{config['subscriptionId']}"
            f"/resourceGroups/{config['resourceGroupNames']['nw']}"
            f"/providers/Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies/{name}")
