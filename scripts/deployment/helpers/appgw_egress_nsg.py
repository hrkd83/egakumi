"""AppGW の既定送信許可と重複する、指定した 3 子規則だけを回収する。"""
from __future__ import annotations

import copy
import ipaddress
import math
import re
import time
from uuid import UUID


API = "2025-01-01"
GROUP_API = "2024-03-01"
NSG_TYPE = "Microsoft.Network/networkSecurityGroups"
# 廃止対象の照合用であり、新しい規則の採番や互換範囲ではない。
RETIRED_RULES = {"allow-apim-http": (120, "80"),
                 "allow-apim-grpc": (125, "443"),
                 "allow-key-vault": (220, "443")}
_PAIRS = (("sourcePortRange", "sourcePortRanges"),
          ("destinationPortRange", "destinationPortRanges"),
          ("sourceAddressPrefix", "sourceAddressPrefixes"),
          ("destinationAddressPrefix", "destinationAddressPrefixes"))


class AppgwEgressNsgError(RuntimeError):
    """外部応答や環境の実値を含めず、回収を停止する。"""


def _scope(config: dict) -> tuple[str, str, dict]:
    try:
        UUID(config["subscriptionId"])
        group, stem = config["resourceGroupNames"]["nw"], config["resourceStem"]
        if any(not isinstance(v, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._()-]{0,79}", v) for v in (group, stem)):
            raise ValueError()
        prefixes = {key: str(ipaddress.ip_network(
            config["network"]["subnetPrefixes"][key], strict=True))
            for key in ("agw", "apim", "pep")}
        if any(ipaddress.ip_network(v).version != 4 for v in prefixes.values()):
            raise ValueError()
        return ("/subscriptions/" + config["subscriptionId"]
                + "/resourceGroups/" + group), stem, prefixes
    except (KeyError, TypeError, ValueError):
        raise AppgwEgressNsgError("AppGW retirement requires a verified network scope") from None


def _request(transport, method, resource_id, api=API, headers=None):
    if method not in ("GET", "DELETE"):
        raise AppgwEgressNsgError("Only exact AppGW child retirement is permitted")
    try:
        result = transport.request(method, resource_id + "?api-version=" + api,
                                   headers=headers)
    except Exception:
        raise AppgwEgressNsgError("AppGW retirement response is unknown; inspect current state before retrying") from None
    allowed = (200, 404) if method == "GET" else (200, 202, 204, 404)
    if (not isinstance(result, dict) or type(result.get("statusCode")) is not int
            or result["statusCode"] not in allowed):
        raise AppgwEgressNsgError("AppGW retirement could not verify the ARM response")
    return result


def _identity(value, resource_id, kind, config, *, tags=True, pending=False):
    states = ("Succeeded", "Updating") if pending else ("Succeeded",)
    expected = {"product": config["productName"],
                "environment": config["environmentName"],
                "azd-env-name": config["environmentName"]}
    if (not isinstance(value, dict)
            or str(value.get("id", "")).casefold() != resource_id.casefold()
            or str(value.get("name", "")).casefold() != resource_id.rsplit("/", 1)[-1].casefold()
            or str(value.get("type", "")).casefold() != kind.casefold()
            or not isinstance(value.get("location"), str)
            or value["location"].replace(" ", "").casefold()
            != config["location"].replace(" ", "").casefold()
            or not isinstance(value.get("properties"), dict)
            or value["properties"].get("provisioningState") not in states
            or tags and (not isinstance(value.get("tags"), dict)
                         or any(value["tags"].get(k) != v for k, v in expected.items()))):
        raise AppgwEgressNsgError("AppGW retirement resource ownership or state is unverified")
    return value


def _read(config, transport, identity, kind, *, pending=False):
    response = _request(transport, "GET", identity)
    return None if response["statusCode"] == 404 else _identity(
        response.get("body"), identity, kind, config, pending=pending)


def _etag(value):
    tag = value.get("etag")
    if (not isinstance(tag, str) or tag.strip('" ') in ("", "*")
            or "\r" in tag or "\n" in tag):
        raise AppgwEgressNsgError("A concrete AppGW retirement ETag is required")
    return tag


def _conditions(rule, *, default=False):
    if not isinstance(rule, dict) or not isinstance(rule.get("properties"), dict):
        raise AppgwEgressNsgError("NSG rule conditions are unavailable")
    props = copy.deepcopy(rule["properties"])
    props.pop("provisioningState", None)
    description = props.pop("description", None)
    if description not in (None, "") and (not default or not isinstance(description, str)):
        raise AppgwEgressNsgError("The retired child has additional conditions")
    for singular, plural in _PAIRS:
        if props.get(plural) == []:
            props.pop(plural)
        if singular in props and props[singular] in (None, ""):
            props.pop(singular)
        if (singular in props) == (plural in props):
            raise AppgwEgressNsgError("NSG rule conditions are ambiguous")
        values = props.pop(plural) if plural in props else [props.pop(singular)]
        if (not isinstance(values, list) or not values
                or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)):
            raise AppgwEgressNsgError("NSG rule conditions cannot be verified")
        props[singular] = sorted(values)
    if isinstance(props.get("protocol"), str):
        props["protocol"] = props["protocol"].capitalize()
    return props


def _inventory(parent):
    props = parent["properties"]
    if props.get("flushConnection", False) is not False:
        raise AppgwEgressNsgError("Connection flushing is not permitted")
    rules = props.get("securityRules")
    if not isinstance(rules, list):
        raise AppgwEgressNsgError("AppGW NSG inventory is unavailable")
    result, priorities = {}, set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("name"), str)
                or not isinstance(rule.get("properties"), dict)
                or rule["name"].casefold() in result):
            raise AppgwEgressNsgError("AppGW NSG rule identities are ambiguous")
        p = rule["properties"]
        priority = p.get("direction"), p.get("priority")
        if (priority[0] not in ("Inbound", "Outbound")
                or type(priority[1]) is not int or not 100 <= priority[1] <= 4096
                or priority in priorities or p.get("access") not in ("Allow", "Deny")):
            raise AppgwEgressNsgError("AppGW NSG rule priorities are ambiguous")
        priorities.add(priority)
        result[rule["name"].casefold()] = rule
    return result


def _fallback(parent, rules):
    if any(r["properties"].get("direction") == "Outbound"
           and r["properties"].get("access") == "Deny" for r in rules.values()):
        raise AppgwEgressNsgError("Custom outbound deny prevents redundant allow retirement")
    defaults = parent["properties"].get("defaultSecurityRules")
    if not isinstance(defaults, list):
        raise AppgwEgressNsgError("Default outbound permits are unavailable")
    for name, priority, source, destination in (
            ("AllowVnetOutBound", 65000, "VirtualNetwork", "VirtualNetwork"),
            ("AllowInternetOutBound", 65001, "*", "Internet")):
        matches = [r for r in defaults if isinstance(r, dict)
                   and str(r.get("name", "")).casefold() == name.casefold()]
        expected = {"priority": priority, "direction": "Outbound", "access": "Allow",
                    "protocol": "*", "sourcePortRange": ["*"],
                    "destinationPortRange": ["*"], "sourceAddressPrefix": [source],
                    "destinationAddressPrefix": [destination]}
        if (len(matches) != 1 or _conditions(matches[0], default=True) != expected
                or str(matches[0].get("id", "")).casefold()
                != (parent["id"] + "/defaultSecurityRules/" + name).casefold()):
            raise AppgwEgressNsgError("Default outbound permits cannot be verified")


def _reference(value):
    return str(value.get("id", "")).casefold() if isinstance(value, dict) else ""


def _topology(config, transport, group_id, stem, prefixes, parent):
    """実 VNet の同一アドレス空間・関連付け・AppGW 利用を照合する。"""
    prefix = group_id + "/providers/Microsoft.Network/"
    vnet_id = prefix + "virtualNetworks/vnet-" + stem
    vnet = _read(config, transport, vnet_id, "Microsoft.Network/virtualNetworks")
    gateway = _read(config, transport, prefix + "applicationGateways/agw-" + stem,
                    "Microsoft.Network/applicationGateways")
    if vnet is None or gateway is None:
        raise AppgwEgressNsgError("The owned AppGW network is unavailable")
    subnets = vnet["properties"].get("subnets")
    if not isinstance(subnets, list):
        raise AppgwEgressNsgError("The owned subnet inventory is unavailable")
    expected = {}
    for key in ("agw", "apim", "pep"):
        name = "snet-" + stem + "-" + key
        identity = vnet_id + "/subnets/" + name
        matches = [s for s in subnets if isinstance(s, dict)
                   and str(s.get("name", "")).casefold() == name.casefold()]
        if len(matches) != 1 or _reference(matches[0]) != identity.casefold():
            raise AppgwEgressNsgError("The owned subnet identity differs")
        props = matches[0].get("properties")
        if not isinstance(props, dict):
            raise AppgwEgressNsgError("The owned subnet properties differ")
        addresses = props.get("addressPrefixes")
        single = props.get("addressPrefix")
        if single and not addresses:
            addresses = [single]
        elif single:
            raise AppgwEgressNsgError("The owned subnet prefix is ambiguous")
        if (addresses != [prefixes[key]]
                or _reference(props.get("networkSecurityGroup"))
                != (prefix + "networkSecurityGroups/nsg-" + stem
                    + "-" + key).casefold()):
            raise AppgwEgressNsgError("The owned subnet prefix or NSG differs")
        expected[key] = identity.casefold()
    associations = parent["properties"].get("subnets")
    if (not isinstance(associations, list) or len(associations) != 1
            or _reference(associations[0]) != expected["agw"]):
        raise AppgwEgressNsgError("The AppGW NSG subnet association differs")
    configs = gateway["properties"].get("gatewayIPConfigurations")
    if (not isinstance(configs, list) or not configs
            or any(not isinstance(c, dict)
                   or not isinstance(c.get("properties"), dict)
                   or _reference(c["properties"].get("subnet"))
                   != expected["agw"] for c in configs)):
        raise AppgwEgressNsgError("The AppGW subnet use is unverified")
    return {"vnet": vnet, "applicationGateway": gateway}


def _key_vault_ips(config, transport, group_id, stem, prefixes):
    pe_id = group_id + "/providers/Microsoft.Network/privateEndpoints/pe-" + stem + "-kv"
    pe = _read(config, transport, pe_id, "Microsoft.Network/privateEndpoints")
    if pe is None:
        raise AppgwEgressNsgError("The owned Key Vault private endpoint is required")
    subnet = (group_id + "/providers/Microsoft.Network/virtualNetworks/vnet-" + stem
              + "/subnets/snet-" + stem + "-pep")
    p = pe["properties"]
    if _reference(p.get("subnet")) != subnet.casefold():
        raise AppgwEgressNsgError("The Key Vault endpoint subnet differs")
    connections = p.get("privateLinkServiceConnections")
    vault = ("/subscriptions/" + config["subscriptionId"] + "/resourceGroups/"
             + config["resourceGroupNames"]["svc"] + "/providers/Microsoft.KeyVault/vaults/kv-" + stem)
    connection = (connections[0].get("properties")
                  if isinstance(connections, list) and len(connections) == 1
                  and isinstance(connections[0], dict) else None)
    if (not isinstance(connection, dict)
            or str(connection.get("privateLinkServiceId", "")).casefold()
            != vault.casefold()
            or connection.get("groupIds") != ["vault"]
            or not isinstance(connection.get("privateLinkServiceConnectionState"), dict)
            or connection["privateLinkServiceConnectionState"].get("status") != "Approved"):
        raise AppgwEgressNsgError("The Key Vault endpoint connection is unverified")
    nics, addresses = p.get("networkInterfaces"), []
    if not isinstance(nics, list) or not nics:
        raise AppgwEgressNsgError("The Key Vault endpoint NIC inventory is unavailable")
    seen = set()
    for reference in nics:
        identity = reference.get("id") if isinstance(reference, dict) else None
        prefix = group_id + "/providers/Microsoft.Network/networkInterfaces/"
        if (not isinstance(identity, str) or not identity.casefold().startswith(prefix.casefold())
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._()-]{0,79}", identity[len(prefix):])
                or identity.casefold() in seen):
            raise AppgwEgressNsgError("The Key Vault endpoint NIC identity is unverified")
        seen.add(identity.casefold())
        response = _request(transport, "GET", identity)
        if response["statusCode"] != 200:
            raise AppgwEgressNsgError("The Key Vault endpoint NIC is unavailable")
        nic = _identity(response.get("body"), identity, "Microsoft.Network/networkInterfaces", config, tags=False)
        if _reference(nic["properties"].get("privateEndpoint")) != pe_id.casefold():
            raise AppgwEgressNsgError("The endpoint NIC backlink is unverified")
        ips = nic["properties"].get("ipConfigurations")
        if not isinstance(ips, list) or not ips:
            raise AppgwEgressNsgError("The endpoint NIC addresses are unavailable")
        for entry in ips:
            try:
                props = entry["properties"]
                address = ipaddress.ip_address(props["privateIPAddress"])
                if (address.version != 4 or address not in ipaddress.ip_network(prefixes["pep"])
                        or _reference(props.get("subnet")) != subnet.casefold()):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise AppgwEgressNsgError("The Key Vault address scope is unverified") from None
            addresses.append(str(address))
    if len(addresses) != len(set(addresses)):
        raise AppgwEgressNsgError("The endpoint address inventory is ambiguous")
    return sorted(addresses)


def _rule(rule, name, parent_id, prefixes, vault_ips, *, pending=False):
    priority, port = RETIRED_RULES[name]
    expected = {"priority": priority, "direction": "Outbound", "access": "Allow",
                "protocol": "Tcp", "sourcePortRange": ["*"],
                "destinationPortRange": [port], "sourceAddressPrefix": [prefixes["agw"]],
                "destinationAddressPrefix": vault_ips if name == "allow-key-vault" else [prefixes["apim"]]}
    actual = _conditions(rule)
    states = ("Succeeded", "Updating", "Deleting") if pending else ("Succeeded",)
    if (actual != expected or str(rule.get("id", "")).casefold()
            != (parent_id + "/securityRules/" + name).casefold()
            or str(rule.get("name", "")).casefold() != name
            or str(rule.get("type", "")).casefold() != (NSG_TYPE + "/securityRules").casefold()
            or rule["properties"].get("provisioningState") not in states):
        raise AppgwEgressNsgError("The AppGW child does not match the retired definition")
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
        rules = props.get(name, [])
        if (not isinstance(rules, list)
                or any(not isinstance(rule, dict)
                       or not isinstance(rule.get("name"), str)
                       or not isinstance(rule.get("properties"), dict)
                       for rule in rules)):
            raise AppgwEgressNsgError("NSG readback inventory is malformed")
        for rule in rules:
            rule.pop("etag", None)
            rule["properties"].pop("provisioningState", None)
        if name in props:
            props[name].sort(key=lambda r: r["name"].casefold())
    return result


def retire_appgw_egress_rules(config: dict, transport, *, execute: bool = False,
                              record=None, max_checks: int = 30,
                              wait_seconds: float = 2, sleep=time.sleep) -> dict:
    """全条件を照合し、指定時に所有する 3 子規則だけを DELETE する。

    既定許可を遮る custom Outbound Deny があれば停止する。外部 writer の
    排他は保証せず、応答不明時の DELETE は再送しない。record は保護保存する。
    """
    if (type(max_checks) is not int or not 1 <= max_checks <= 120
            or type(wait_seconds) not in (int, float)
            or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 60):
        raise AppgwEgressNsgError("AppGW retirement wait limits are invalid")
    group, stem, prefixes = _scope(config)
    parent_id = group + "/providers/" + NSG_TYPE + "/nsg-" + stem + "-agw"
    parent = _read(config, transport, parent_id, NSG_TYPE)
    rules = _inventory(parent) if parent else {}
    targets = [name for name in RETIRED_RULES if name in rules]
    vault_ips, topology = [], None
    if targets:
        _etag(parent)
        _fallback(parent, rules)
        topology = _topology(config, transport, group, stem, prefixes, parent)
        if "allow-key-vault" in targets:
            vault_ips = _key_vault_ips(config, transport, group, stem, prefixes)
        for name in targets:
            expected = _rule(rules[name], name, parent_id, prefixes, vault_ips)
            child = _request(transport, "GET", parent_id + "/securityRules/" + name)
            if (child["statusCode"] != 200 or _rule(child.get("body"), name,
                    parent_id, prefixes, vault_ips) != expected):
                raise AppgwEgressNsgError("AppGW parent and child inventories differ")
            _etag(child["body"])
    if parent is not None:
        response = _request(transport, "GET", group, GROUP_API)
        owner = response.get("body") or {}
        if (response["statusCode"] != 200 or str(owner.get("id", "")).casefold() != group.casefold()
                or not isinstance(owner.get("tags"), dict)
                or any(owner["tags"].get(k) != config[v]
                       for k, v in (("product", "productName"), ("environment", "environmentName")))):
            raise AppgwEgressNsgError("Network resource group ownership is unverified")
    emit = record or (lambda event, value: None)
    emit("plan", {"parent": parent, "topology": topology,
                  "targets": targets, "keyVaultPrivateIps": vault_ips,
                  "execute": execute, "azureWrites": 0})
    writes = 0
    if execute:
        for name in targets:
            fresh = _read(config, transport, parent_id, NSG_TYPE)
            if (_snapshot(fresh) != _snapshot(parent)
                    or fresh is None or _etag(fresh) != _etag(parent)):
                raise AppgwEgressNsgError("AppGW NSG changed before retirement")
            _fallback(fresh, _inventory(fresh))
            _topology(config, transport, group, stem, prefixes, fresh)
            if vault_ips and _key_vault_ips(config, transport, group, stem, prefixes) != vault_ips:
                raise AppgwEgressNsgError("Key Vault endpoint addresses changed before retirement")
            child_id = parent_id + "/securityRules/" + name
            child = _request(transport, "GET", child_id)
            if child["statusCode"] != 200:
                raise AppgwEgressNsgError("The AppGW child changed before retirement")
            _rule(child.get("body"), name, parent_id, prefixes, vault_ips)
            expected = copy.deepcopy(parent)
            expected["properties"]["securityRules"] = [r for r in expected["properties"]["securityRules"]
                if r["name"].casefold() != name]
            emit("attempt", {"target": child_id, "ordinal": writes + 1})
            _request(transport, "DELETE", child_id, headers={"If-Match": _etag(child["body"])})
            writes += 1
            for attempt in range(max_checks):
                remaining = _request(transport, "GET", child_id)
                if remaining["statusCode"] == 200:
                    _rule(remaining.get("body"), name, parent_id, prefixes, vault_ips, pending=True)
                current = _read(config, transport, parent_id, NSG_TYPE, pending=True)
                snapshot = _snapshot(current)
                if remaining["statusCode"] == 404 and snapshot == _snapshot(expected):
                    if current["properties"]["provisioningState"] == "Succeeded":
                        parent = current
                        emit("removed", {"target": child_id, "azureWrites": writes})
                        break
                elif snapshot not in (_snapshot(parent), _snapshot(expected)):
                    raise AppgwEgressNsgError("NSG settings changed outside the retired child")
                if attempt + 1 == max_checks:
                    raise AppgwEgressNsgError("AppGW retirement is pending; inspect before retrying")
                sleep(wait_seconds)
        if targets and _snapshot(_read(config, transport, parent_id, NSG_TYPE)) != _snapshot(parent):
            raise AppgwEgressNsgError("AppGW NSG final readback differs")
        if targets:
            _topology(config, transport, group, stem, prefixes, parent)
        if vault_ips and _key_vault_ips(config, transport, group, stem, prefixes) != vault_ips:
            raise AppgwEgressNsgError("Key Vault endpoint final readback differs")
    result = {"status": "completed" if execute else "planned",
              "plannedDeletes": len(targets), "azureWrites": writes}
    emit("result", result)
    return result
