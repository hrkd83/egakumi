"""初期構築の定数と、同じ入力から再現できる内部設定を生成する。"""
from __future__ import annotations

import copy

from .configuration import entra_app_separation_enabled


def apply_initial_configuration(configuration: dict, *, fail) -> dict:
    """公開入力を検査・合成した後、既存の内部設定契約へ固定値を補う。"""
    result = copy.deepcopy(configuration)
    def section(parent, key, path):
        """生成処理の前に、型の違う入力を値を出さずに拒否する。"""
        value = parent.setdefault(key, {})
        if not isinstance(value, dict):
            fail(path, "must be an object")
        return value

    result["publishApis"] = True
    # 空の内部配列は、Coreの投影とGateway公開で同じ標準API正本を読む指定。
    result["apiDefinitions"] = []
    audit_evidence = section(result, "auditEvidence", "auditEvidence")
    audit_evidence["enabled"] = True
    result["authorization"] = {
        key: [] for key in ("projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders")
    }
    network = section(result, "network", "network")
    firewall = section(network, "firewall", "network.firewall")
    spaces = network.get("addressSpaces", [])
    if not isinstance(spaces, list) or any(not isinstance(space, str) for space in spaces):
        fail("network.addressSpaces", "must be an array of strings")
    firewall["snatPrivateRanges"] = list(dict.fromkeys(["IANAPrivateRanges", *spaces]))
    firewall["idpsPrivateRanges"] = list(dict.fromkeys([
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", *spaces,
    ]))
    firewall["bypassTrafficSettings"] = []
    gateway = section(result, "gateway", "gateway")
    gateway["existingWafPolicyId"] = ""
    authentication = section(gateway, "authentication", "gateway.authentication")
    authentication["subscriptionKeyHeader"] = "api-key"
    if isinstance(authentication.get("entra"), dict):
        authentication["entra"]["requiredScope"] = "Gateway.Invoke"
    login = section(authentication, "credentialLogin", "gateway.authentication.credentialLogin") if "credentialLogin" in authentication else {}
    identity = login.get("identity")
    if isinstance(identity, dict) and not entra_app_separation_enabled(result):
        identity["tenantId"] = result.get("entraTenantId")
    monitoring = section(result, "monitoring", "monitoring")
    monitoring["auditAllowedIpAddresses"] = []
    monitoring["auditReaders"] = []
    certificate = section(result, "certificate", "certificate")
    certificate["plugin"] = "Azure"
    portal = section(result, "portal", "portal")
    certificate["domainNames"] = [gateway.get("publicHostName"), portal.get("publicHostName")]
    return result
