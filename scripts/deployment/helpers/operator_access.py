"""Remove only the owned operator rule when no operator CIDRs are configured."""
from __future__ import annotations

import copy
import ipaddress
import re
import time
from uuid import UUID


API = "2025-01-01"
GROUP_API = "2024-03-01"
TYPE = "Microsoft.Network/networkSecurityGroups"
RULE_NAME = "allow-operator-management"


class OperatorAccessError(RuntimeError):
    """Stop without exposing configuration values or ARM response bodies."""


def _disabled(config):
    network = config.get("network")
    if not isinstance(network, dict) or not isinstance(network.get("operatorAddressPrefixes", []), list):
        raise OperatorAccessError("Operator access configuration is invalid")
    return not network.get("operatorAddressPrefixes", [])


def _inventory(rules):
    if not isinstance(rules, list):
        raise OperatorAccessError("Existing management NSG rules are unavailable")
    result, priorities = {}, set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("name"), str)
                or not isinstance(rule.get("properties"), dict)
                or rule["name"].casefold() in result):
            raise OperatorAccessError("Existing management NSG rule identities are ambiguous")
        properties = rule["properties"]
        identity = properties.get("direction"), properties.get("priority")
        if (properties.get("direction") not in ("Inbound", "Outbound")
                or properties.get("access") not in ("Allow", "Deny")
                or type(properties.get("priority")) is not int
                or not 100 <= properties["priority"] <= 4096 or identity in priorities):
            raise OperatorAccessError("Existing management NSG priorities are ambiguous")
        priorities.add(identity)
        result[rule["name"].casefold()] = rule
    return result


def _properties(rule):
    # Azure may complement a single condition with empty plural properties.
    return {key: value for key, value in rule["properties"].items()
            if key != "provisioningState"
            and not (key.endswith(("PortRanges", "AddressPrefixes")) and value == [])
            and not (key.endswith(("PortRange", "AddressPrefix")) and value in (None, ""))
            and not (key == "description" and value in (None, ""))}


def _values(properties, singular, plural):
    present = [key for key in (singular, plural) if key in properties]
    if len(present) != 1:
        raise OperatorAccessError("The owned operator rule has ambiguous conditions")
    value = properties[present[0]]
    result = value if present[0] == plural else [value]
    if not isinstance(result, list) or not result or any(not isinstance(item, str) for item in result):
        raise OperatorAccessError("The owned operator rule has invalid conditions")
    return result


def _ranges(values):
    if not isinstance(values, list) or not values:
        raise OperatorAccessError("Operator ports cannot be verified")
    result = []
    for value in values:
        if value == "*":
            return [(0, 65535)]
        if not isinstance(value, str) or not re.fullmatch(r"\d+(?:-\d+)?", value):
            raise OperatorAccessError("Operator ports cannot be verified")
        pair = list(map(int, value.split("-")))
        if not 0 <= pair[0] <= pair[-1] <= 65535:
            raise OperatorAccessError("Operator ports cannot be verified")
        result.append((pair[0], pair[-1]))
    return sorted(result)


def _terminal(rules):
    rule = rules.get("deny-other-inbound")
    expected = {"priority": 4096, "direction": "Inbound", "access": "Deny", "protocol": "*",
                "sourcePortRange": "*", "destinationPortRange": "*",
                "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}
    if (rule is None or _properties(rule) != expected
            or rule["properties"].get("provisioningState", "Succeeded") != "Succeeded"):
        raise OperatorAccessError("A verified terminal inbound deny is required before operator removal")


def _operator_rule(config, rule):
    props = _properties(rule)
    common = {"priority", "direction", "access", "protocol", "sourcePortRange",
              "sourceAddressPrefix", "sourceAddressPrefixes", "destinationAddressPrefix",
              "destinationPortRange", "destinationPortRanges"}
    if (set(props) - common or props.get("direction") != "Inbound"
            or props.get("sourcePortRange") != "*"
            or type(props.get("priority")) is not int
            or props["priority"] != 220 or props.get("access") != "Allow"
            or rule["properties"].get("provisioningState", "Succeeded") != "Succeeded"):
        raise OperatorAccessError("The owned operator rule does not match the current definition")
    network = config["network"]
    protocol = network.get("operatorProtocol", "Tcp")
    if (not isinstance(props.get("protocol"), str) or protocol not in ("Tcp", "Udp", "*")
            or props["protocol"].casefold() != protocol.casefold()):
        raise OperatorAccessError("The operator protocol differs from the selected configuration")
    ports = _values(props, "destinationPortRange", "destinationPortRanges")
    if _ranges(ports) != _ranges(network.get("operatorPorts", ["22"])):
        raise OperatorAccessError("The operator ports differ from the selected configuration")
    try:
        destination = ipaddress.ip_network(props.get("destinationAddressPrefix", ""), strict=False)
        subnet = ipaddress.ip_network(network["subnetPrefixes"]["management"], strict=True)
        if destination.version != 4 or destination.prefixlen != 32 or not destination.subnet_of(subnet):
            raise ValueError()
        sources = _values(props, "sourceAddressPrefix", "sourceAddressPrefixes")
        if any(ipaddress.ip_network(source, strict=False).version != 4 for source in sources):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise OperatorAccessError("The operator address scope cannot be verified") from None


def remove_disabled_operator(config, rules):
    """Return all rules unchanged except one verified, disabled operator child.

    The caller remains responsible for resource ownership and live comparisons.
    """
    if not _disabled(config):
        return copy.deepcopy(rules)
    inventory = _inventory(rules)
    rule = inventory.get(RULE_NAME)
    if rule is None:
        return copy.deepcopy(rules)
    _terminal(inventory)
    _operator_rule(config, rule)
    return [copy.deepcopy(item) for item in rules if item["name"].casefold() != RULE_NAME]


def _snapshot(value):
    if value is None:
        return None
    result = copy.deepcopy(value)
    for key in ("etag", "systemData"):
        result.pop(key, None)
    properties = result["properties"]
    properties.pop("provisioningState", None)
    properties.setdefault("flushConnection", False)
    for name in ("securityRules", "defaultSecurityRules"):
        for rule in properties.get(name, []):
            rule.pop("etag", None)
            rule["properties"].pop("provisioningState", None)
        if name in properties:
            properties[name].sort(key=lambda rule: rule["name"].casefold())
    return result


def _request(transport, method, resource_id, api=API, headers=None):
    try:
        response = transport.request(method, resource_id + "?api-version=" + api, headers=headers)
    except Exception:
        raise OperatorAccessError("Operator cleanup response is unknown; inspect the resource before retrying") from None
    allowed = (200, 404) if method == "GET" else (200, 202, 204, 404)
    if (not isinstance(response, dict) or type(response.get("statusCode")) is not int
            or response["statusCode"] not in allowed):
        raise OperatorAccessError("Operator cleanup could not verify the ARM response")
    return response


def _read_nsg(config, transport, resource_id, *, pending=False):
    response = _request(transport, "GET", resource_id)
    if response["statusCode"] == 404:
        return None
    value = response.get("body")
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    if (not isinstance(value, dict) or str(value.get("id", "")).casefold() != resource_id.casefold()
            or str(value.get("name", "")).casefold() != resource_id.rsplit("/", 1)[-1].casefold()
            or str(value.get("type", "")).casefold() != TYPE.casefold()
            or not isinstance(value.get("location"), str)
            or value["location"].replace(" ", "").casefold() != config["location"].replace(" ", "").casefold()
            or not isinstance(value.get("tags"), dict)
            or any(value["tags"].get(key) != expected for key, expected in expected_tags.items())
            or not isinstance(value.get("properties"), dict)):
        raise OperatorAccessError("Management NSG ownership could not be verified")
    states = ("Succeeded", "Updating") if pending else ("Succeeded",)
    if value["properties"].get("provisioningState") not in states:
        raise OperatorAccessError("Management NSG has an unresolved operation")
    _inventory(value["properties"].get("securityRules"))
    return value


def _child(response, resource_id, expected):
    value = response.get("body")
    if (not isinstance(value, dict) or str(value.get("id", "")).casefold() != resource_id.casefold()
            or str(value.get("name", "")).casefold() != RULE_NAME
            or str(value.get("type", "")).casefold() != (TYPE + "/securityRules").casefold()
            or not isinstance(value.get("properties"), dict)
            or _properties(value) != _properties(expected)):
        raise OperatorAccessError("The operator child changed or has an unverified identity")


def cleanup_disabled_operator(config, transport, *, max_checks=30, wait_seconds=2, sleep=time.sleep):
    """DELETE the exact owned child once and verify absence and other settings.

    GET comparisons detect concurrent changes but do not provide atomic exclusion
    against an external writer. A lost DELETE response is never retried here.
    """
    if not _disabled(config):
        return {"status": "not-required", "azureWrites": 0}
    if type(max_checks) is not int or not 1 <= max_checks <= 120 or not 0 <= wait_seconds <= 60:
        raise OperatorAccessError("Operator cleanup wait limits are invalid")
    try:
        UUID(config["subscriptionId"])
        group_name, stem = config["resourceGroupNames"]["nw"], config["resourceStem"]
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._()-]{0,79}", value)
               for value in (group_name, stem)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise OperatorAccessError("Operator cleanup scope is invalid") from None
    group_id = "/subscriptions/" + config["subscriptionId"] + "/resourceGroups/" + group_name
    resource_id = group_id + "/providers/" + TYPE + "/nsg-" + stem + "-vm"
    before = _read_nsg(config, transport, resource_id)
    if before is None:
        return {"status": "nsg-absent", "azureWrites": 0}
    rules = before["properties"]["securityRules"]
    wanted = remove_disabled_operator(config, rules)
    if wanted == rules:
        return {"status": "rule-absent", "azureWrites": 0}
    expected = copy.deepcopy(before)
    expected["properties"]["securityRules"] = wanted
    owner = _request(transport, "GET", group_id, GROUP_API)
    group = owner.get("body")
    if (owner["statusCode"] != 200 or not isinstance(group, dict)
            or str(group.get("id", "")).casefold() != group_id.casefold()
            or not isinstance(group.get("tags"), dict)
            or group["tags"].get("product") != config["productName"]
            or group["tags"].get("environment") != config["environmentName"]):
        raise OperatorAccessError("Network resource group ownership could not be verified")
    child_id = resource_id + "/securityRules/" + RULE_NAME
    child = _request(transport, "GET", child_id)
    writes = 0
    if child["statusCode"] == 200:
        old_rule = _inventory(rules)[RULE_NAME]
        _child(child, child_id, old_rule)
        fresh = _read_nsg(config, transport, resource_id)
        if _snapshot(fresh) != _snapshot(before) or fresh.get("etag") != before.get("etag"):
            raise OperatorAccessError("Management NSG changed before operator cleanup")
        _request(transport, "DELETE", child_id)
        writes = 1
    for attempt in range(max_checks):
        response = _request(transport, "GET", child_id)
        if response["statusCode"] == 404:
            current = _read_nsg(config, transport, resource_id, pending=True)
            if _snapshot(current) == _snapshot(expected) and current["properties"]["provisioningState"] == "Succeeded":
                return {"status": "removed" if writes else "rule-absent", "azureWrites": writes}
            if current is None or _snapshot(current) not in (_snapshot(before), _snapshot(expected)):
                raise OperatorAccessError("Management NSG changed outside the operator rule")
        else:
            _child(response, child_id, _inventory(rules)[RULE_NAME])
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    raise OperatorAccessError("Operator cleanup is pending; inspect the resource before retrying")
