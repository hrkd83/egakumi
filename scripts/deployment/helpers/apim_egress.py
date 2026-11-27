"""APIMの追加HTTPS規則を、GatewayとCoreの配備で共通の条件で照合する。"""
from __future__ import annotations

import re

APIM_EGRESS_RULE_NAME = "allow-apim-approved-services"

_FQDN = re.compile(
    r"(?:\*\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)


def validate_apim_egress_rule(rule: dict, source_prefix: str) -> None:
    """未知の許可条件を正常化せず停止する。応答中の実値はエラーへ含めない。"""
    empty_fields = (
        "sourceIpGroups", "destinationAddresses", "fqdnTags", "targetUrls",
        "httpHeaders", "httpHeadersToInsert", "webCategories",
    )
    if (rule.get("name") != APIM_EGRESS_RULE_NAME
            or rule.get("ruleType") != "ApplicationRule"
            or rule.get("sourceAddresses") != [source_prefix]
            or rule.get("protocols") != [{"protocolType": "Https", "port": 443}]
            or any(rule.get(key) not in (None, []) for key in empty_fields)
            or (rule.get("terminateTLS") is not None and rule.get("terminateTLS") is not False)
            or (rule.get("ipv6Rule") is not None and rule.get("ipv6Rule") is not False)
            or not isinstance(rule.get("targetFqdns"), list)):
        raise ValueError("The existing APIM FQDN rule has a different source/protocol boundary")
    # 空集合の読取りは許可するが、URL・IP・全許可をFQDNとして引き継がない。
    if any(not isinstance(host, str) or len(host) > 253 or not _FQDN.fullmatch(host)
           for host in rule["targetFqdns"]):
        raise ValueError("Existing API egress destination is not a supported FQDN")
