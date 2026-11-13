"""Azure 提供 DNS 用の不要な NSG 子規則だけを照合して回収する。"""
from __future__ import annotations

import copy
import re
import time
from uuid import UUID


API = "2025-01-01"
GROUP_API = "2024-03-01"
NSG_TYPE = "Microsoft.Network/networkSecurityGroups"
DNS_ADDRESS = "168.63.129.16"  # Azure が公開仕様で定義する共通アドレス。
# 廃止する子規則の識別子であり、新しい規則の採番表ではない。
RETIRED_RULES = {"agw": {"Tcp": 225, "Udp": 230},
                 "apim": {"Tcp": 275, "Udp": 280},
                 "vm": {"Tcp": 265, "Udp": 270}}


class DnsNsgError(RuntimeError):
    """構成値や ARM 本文を公開せず、安全に回収を停止する。"""


def _scope(config):
    try:
        UUID(config["subscriptionId"])
        group, stem = config["resourceGroupNames"]["nw"], config["resourceStem"]
        for value in (group, stem):
            if not isinstance(value, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._()-]{0,79}", value):
                raise ValueError()
        network = config["network"]
        if (not isinstance(network, dict)
                or network.get("dnsServers", []) != []
                or not isinstance(network.get("firewall", {}), dict)
                or network.get("firewall", {}).get("dnsServers", []) != []):
            raise ValueError()
        return ("/subscriptions/" + config["subscriptionId"]
                + "/resourceGroups/" + group), stem
    except (KeyError, TypeError, ValueError):
        raise DnsNsgError("DNS retirement requires a verified Azure-provided DNS configuration") from None


def _request(transport, method, resource_id, api=API, headers=None):
    try:
        result = transport.request(method, resource_id + "?api-version=" + api,
                                   headers=headers)
    except Exception:
        raise DnsNsgError("DNS retirement response is unknown; inspect current state before retrying") from None
    allowed = (200, 404) if method == "GET" else (200, 202, 204, 404)
    if (not isinstance(result, dict)
            or type(result.get("statusCode")) is not int
            or result["statusCode"] not in allowed):
        raise DnsNsgError("DNS retirement could not verify the ARM response")
    return result


def _read(config, transport, resource_id, resource_type, *, pending=False):
    response = _request(transport, "GET", resource_id)
    if response["statusCode"] == 404:
        return None
    value = response.get("body")
    tags = {"product": config["productName"],
            "environment": config["environmentName"],
            "azd-env-name": config["environmentName"]}
    if (not isinstance(value, dict)
            or str(value.get("id", "")).casefold() != resource_id.casefold()
            or str(value.get("name", "")).casefold()
            != resource_id.rsplit("/", 1)[-1].casefold()
            or str(value.get("type", "")).casefold() != resource_type.casefold()
            or not isinstance(value.get("location"), str)
            or value["location"].replace(" ", "").casefold()
            != config["location"].replace(" ", "").casefold()
            or not isinstance(value.get("tags"), dict)
            or any(value["tags"].get(key) != item for key, item in tags.items())
            or not isinstance(value.get("properties"), dict)):
        raise DnsNsgError("DNS retirement resource ownership could not be verified")
    states = ("Succeeded", "Updating") if pending else ("Succeeded",)
    if value["properties"].get("provisioningState") not in states:
        raise DnsNsgError("DNS retirement resource has an unresolved operation")
    return value


def _properties(rule):
    if not isinstance(rule, dict):
        raise DnsNsgError("DNS rule identity is unavailable")
    properties = rule.get("properties")
    if not isinstance(properties, dict):
        raise DnsNsgError("DNS rule properties are unavailable")
    return {key: value for key, value in properties.items()
            if key != "provisioningState"
            and not (key.endswith(("PortRanges", "AddressPrefixes"))
                     and value == [])
            and not (key.endswith(("PortRange", "AddressPrefix"))
                     and value in (None, ""))
            and not (key == "description" and value in (None, ""))}


def _inventory(parent):
    rules = parent["properties"].get("securityRules")
    if not isinstance(rules, list):
        raise DnsNsgError("NSG rule inventory is unavailable")
    result, priorities = {}, set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("name"), str)
                or rule["name"].casefold() in result):
            raise DnsNsgError("NSG rule identities are ambiguous")
        props = _properties(rule)
        identity = props.get("direction"), props.get("priority")
        if (identity[0] not in ("Inbound", "Outbound")
                or type(identity[1]) is not int
                or not 100 <= identity[1] <= 4096 or identity in priorities
                or props.get("access") not in ("Allow", "Deny")):
            raise DnsNsgError("NSG rule priorities are ambiguous")
        priorities.add(identity)
        result[rule["name"].casefold()] = rule
    return result


def _rule(rule, suffix, protocol, parent_id, *, pending=False):
    name = "allow-dns-" + protocol.lower()
    expected = {"priority": RETIRED_RULES[suffix][protocol],
                "direction": "Outbound", "access": "Allow",
                "protocol": protocol, "sourcePortRange": "*",
                "destinationPortRange": "53", "sourceAddressPrefix": "*",
                "destinationAddressPrefix": DNS_ADDRESS}
    actual = _properties(rule)
    # Azure は単一条件でも複数形を返せる。意味が単一のときだけ正規化する。
    for singular, plural in (("sourcePortRange", "sourcePortRanges"),
                             ("destinationPortRange", "destinationPortRanges"),
                             ("sourceAddressPrefix", "sourceAddressPrefixes"),
                             ("destinationAddressPrefix", "destinationAddressPrefixes")):
        if plural in actual and singular not in actual:
            values = actual.pop(plural)
            if not isinstance(values, list) or len(values) != 1:
                raise DnsNsgError("The DNS child has unverified conditions")
            actual[singular] = values[0]
    if isinstance(actual.get("protocol"), str):
        actual["protocol"] = actual["protocol"].capitalize()
    states = ("Succeeded", "Updating", "Deleting") if pending else ("Succeeded",)
    if (actual != expected
            or str(rule.get("id", "")).casefold()
            != (parent_id + "/securityRules/" + name).casefold()
            or str(rule.get("name", "")).casefold() != name
            or str(rule.get("type", "")).casefold()
            != (NSG_TYPE + "/securityRules").casefold()
            or rule["properties"].get("provisioningState") not in states):
        raise DnsNsgError("The DNS child does not match the retired definition")
    return actual


def _snapshot(value):
    if value is None:
        return None
    result = copy.deepcopy(value)
    for key in ("etag", "systemData"):
        result.pop(key, None)
    props = result["properties"]
    props.pop("provisioningState", None)
    props.setdefault("flushConnection", False)
    for name in ("securityRules", "defaultSecurityRules"):
        for rule in props.get(name, []):
            rule.pop("etag", None)
            rule["properties"].pop("provisioningState", None)
        if name in props:
            props[name].sort(key=lambda item: item["name"].casefold())
    return result


def _dns(config, transport, group_id, stem):
    definitions = (("virtualNetworks", "vnet-"),
                   ("firewallPolicies", "afwp-"),
                   ("azureFirewalls", "afw-"))
    found = {}
    for kind, prefix in definitions:
        resource_id = group_id + "/providers/Microsoft.Network/" + kind + "/" + prefix + stem
        value = _read(config, transport, resource_id, "Microsoft.Network/" + kind)
        found[kind] = None
        if value is None:
            continue
        props = value["properties"]
        container = props.get("dhcpOptions" if kind == "virtualNetworks" else "dnsSettings", {})
        field = "servers" if kind == "firewallPolicies" else "dnsServers"
        if not isinstance(container, dict) or container.get(field, []) != []:
            raise DnsNsgError("Custom DNS is present; no DNS setting or rule was changed")
        if kind == "firewallPolicies" and container.get("enableProxy") is not True:
            raise DnsNsgError("The Firewall DNS proxy configuration cannot be verified")
        if kind == "azureFirewalls":
            expected = group_id + "/providers/Microsoft.Network/firewallPolicies/afwp-" + stem
            association = props.get("firewallPolicy")
            if (not isinstance(association, dict)
                    or str(association.get("id", "")).casefold() != expected.casefold()):
                raise DnsNsgError("The Firewall policy association cannot be verified")
        # 毎回所有・成功状態を再照合し、比較対象は DNS と関連付けに限る。
        # DNS を更新しない本処理で、etag/systemData の更新は差分にしない。
        settings = copy.deepcopy(container)
        settings.setdefault(field, [])
        found[kind] = {"id": value["id"].casefold(), "dns": settings}
        if kind == "azureFirewalls":
            found[kind]["policyId"] = association["id"].casefold()
    return found


def retire_dns_rules(config: dict, transport, *, execute: bool = False,
                     record=None, max_checks: int = 30,
                     wait_seconds: float = 2, sleep=time.sleep) -> dict:
    """所有する 6 子規則だけを計画し、指定時に一度ずつ DELETE する。

    全規則と DNS を事前照合する。応答不明は再送せず停止し、部分完了後の
    再実行では残った規則だけを再照合する。外部 writer の排他は保証しない。
    record には実値を含むため、呼出元が環境別の保護領域へ保存する。
    """
    if (type(max_checks) is not int or not 1 <= max_checks <= 120
            or not 0 <= wait_seconds <= 60):
        raise DnsNsgError("DNS retirement wait limits are invalid")
    group_id, stem = _scope(config)
    parents, targets = {}, []
    for suffix, protocols in RETIRED_RULES.items():
        parent_id = group_id + "/providers/" + NSG_TYPE + "/nsg-" + stem + "-" + suffix
        parent = _read(config, transport, parent_id, NSG_TYPE)
        parents[parent_id] = parent
        if parent is None:
            continue
        rules = _inventory(parent)
        for protocol in protocols:
            name = "allow-dns-" + protocol.lower()
            rule = rules.get(name)
            if rule is not None:
                canonical = _rule(rule, suffix, protocol, parent_id)
                child_id = parent_id + "/securityRules/" + name
                response = _request(transport, "GET", child_id)
                if response["statusCode"] != 200:
                    raise DnsNsgError("DNS child inventory changed during planning")
                if _rule(response.get("body", {}), suffix, protocol, parent_id) != canonical:
                    raise DnsNsgError("DNS child inventory is inconsistent")
                targets.append({"id": child_id, "parent": parent_id,
                                "suffix": suffix, "protocol": protocol})
    dns = _dns(config, transport, group_id, stem)
    if targets and any(value is None for value in dns.values()):
        raise DnsNsgError("Existing DNS rules require the complete owned network")
    if any(value is not None for value in [*parents.values(), *dns.values()]):
        response = _request(transport, "GET", group_id, GROUP_API)
        owner = response.get("body", {})
        if (response["statusCode"] != 200
                or str(owner.get("id", "")).casefold() != group_id.casefold()
                or not isinstance(owner.get("tags"), dict)
                or owner["tags"].get("product") != config["productName"]
                or owner["tags"].get("environment") != config["environmentName"]):
            raise DnsNsgError("Network resource group ownership could not be verified")
    emit = record or (lambda event, value: None)
    emit("plan", {"parents": parents, "dns": dns, "targets": targets,
                  "execute": execute, "azureWrites": 0})
    writes = 0
    if execute:
        for target in targets:
            if _dns(config, transport, group_id, stem) != dns:
                raise DnsNsgError("Network DNS settings changed before deletion")
            for parent_id, expected in parents.items():
                fresh = _read(config, transport, parent_id, NSG_TYPE)
                if (_snapshot(fresh) != _snapshot(expected)
                        or (fresh is not None and fresh.get("etag") != expected.get("etag"))):
                    raise DnsNsgError("NSG inventory changed before DNS retirement")
            child = _request(transport, "GET", target["id"])
            if child["statusCode"] != 200:
                raise DnsNsgError("DNS child changed before deletion")
            _rule(child.get("body", {}), target["suffix"],
                  target["protocol"], target["parent"])
            expected = copy.deepcopy(parents[target["parent"]])
            expected["properties"]["securityRules"] = [
                rule for rule in expected["properties"]["securityRules"]
                if rule["id"].casefold() != target["id"].casefold()]
            emit("attempt", {"target": target["id"], "ordinal": writes + 1})
            headers = {"If-Match": child["body"]["etag"]} if child["body"].get("etag") else None
            _request(transport, "DELETE", target["id"], headers=headers)
            writes += 1
            for attempt in range(max_checks):
                response = _request(transport, "GET", target["id"])
                if response["statusCode"] == 200:
                    _rule(response.get("body", {}), target["suffix"],
                          target["protocol"], target["parent"], pending=True)
                current = _read(config, transport, target["parent"], NSG_TYPE, pending=True)
                if response["statusCode"] == 404 and _snapshot(current) == _snapshot(expected):
                    if current["properties"]["provisioningState"] == "Succeeded":
                        parents[target["parent"]] = current
                        emit("removed", {"target": target["id"], "azureWrites": writes})
                        break
                elif _snapshot(current) not in (
                        _snapshot(parents[target["parent"]]), _snapshot(expected)):
                    raise DnsNsgError("NSG settings changed outside the DNS child")
                if attempt + 1 == max_checks:
                    raise DnsNsgError("DNS retirement is pending; inspect current state before retrying")
                sleep(wait_seconds)
        for parent_id, expected in parents.items():
            if _snapshot(_read(config, transport, parent_id, NSG_TYPE)) != _snapshot(expected):
                raise DnsNsgError("NSG final readback differs from the approved result")
        if _dns(config, transport, group_id, stem) != dns:
            raise DnsNsgError("Network DNS final readback changed")
    result = {"status": "completed" if execute else "planned",
              "plannedDeletes": len(targets), "azureWrites": writes}
    emit("result", result)
    return result
