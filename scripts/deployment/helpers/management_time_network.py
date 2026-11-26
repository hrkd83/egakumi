"""Retire only the owned management NTP NSG child and Firewall rule.

No Core deployment, NSG parent update, or other rule collection update occurs.
The last successful PHC preparation is checked; current clock accuracy is not
measured here. Cooperative operation locks and conditional requests do not
replace the immediate inventory checks and final readback.
"""
from __future__ import annotations

import copy
import ipaddress
import math
import re
import time
from uuid import UUID

from . import fixed_nsg as nsg
from .management_host_time import ManagementHostTimeError, require_management_host_time_core_ready


NETWORK_API = "2025-01-01"
COMPUTE_API = "2024-07-01"
GROUP_API = "2024-03-01"
RETIRED_NSG = {"allow-time-sync-egress": 325, "allow-time-sync": 260}
FW_RULE = "allow-mgmt-vm-ntp"
FW_COLLECTION = "allow-vm-platform"


class ManagementTimeNetworkError(RuntimeError):
    """Stop without disclosing protected inputs or ARM response values."""


def operation_ids(config):
    """Derive only the selected owned management and network resource paths."""
    try:
        if not isinstance(config["subscriptionId"], str):
            raise ValueError()
        UUID(config["subscriptionId"])
        stem = config["resourceStem"]
        groups = {key: config["resourceGroupNames"][key] for key in ("nw", "maint")}
        for value in (stem, *groups.values()):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._()-]{0,79}", value):
                raise ValueError()
        for key in ("productName", "environmentName", "location"):
            if not isinstance(config[key], str) or not config[key]:
                raise ValueError()
        prefix = ipaddress.ip_network(config["network"]["subnetPrefixes"]["management"], strict=True)
        if prefix.version != 4:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ManagementTimeNetworkError("Select verified management time network inputs") from None
    root = "/subscriptions/" + config["subscriptionId"] + "/resourceGroups/"
    nw = root + groups["nw"]
    maint = root + groups["maint"]
    network = nw + "/providers/Microsoft.Network/"
    vm = maint + "/providers/Microsoft.Compute/virtualMachines/vm-" + stem + "-mgmt"
    policy = network + "firewallPolicies/afwp-" + stem
    parent = network + "networkSecurityGroups/nsg-" + stem + "-vm"
    return {"nwGroup": nw, "maintGroup": maint, "vm": vm,
            "command": vm + "/runCommands/configure-linux-audit",
            "hostTimeCommand": vm + "/runCommands/configure-host-time",
            "nic": maint + "/providers/Microsoft.Network/networkInterfaces/nic-" + stem + "-mgmt",
            "vnet": network + "virtualNetworks/vnet-" + stem,
            "subnet": network + "virtualNetworks/vnet-" + stem + "/subnets/snet-" + stem + "-vm",
            "nsg": parent, "firewall": network + "azureFirewalls/afw-" + stem,
            "policy": policy, "rcg": policy + "/ruleCollectionGroups/rcg-platform",
            **{name: parent + "/securityRules/" + name for name in RETIRED_NSG}}


def _request(transport, method, target, api=NETWORK_API, *, body=None, headers=None):
    try:
        result = transport.request(method, target + "?api-version=" + api, body=body, headers=headers)
    except Exception:
        raise ManagementTimeNetworkError("Management time network response is unknown; inspect before retrying") from None
    allowed = (200, 404) if method == "GET" else (200, 201, 202) if method == "PUT" else (200, 202, 204)
    if not isinstance(result, dict) or type(result.get("statusCode")) is not int or result["statusCode"] not in allowed:
        raise ManagementTimeNetworkError("Management time network response is rejected or unverified")
    return result


def _owned(config, transport, target, kind, *, tags=True, pending=False, api=NETWORK_API):
    response = _request(transport, "GET", target, api)
    body = response.get("body")
    if (response["statusCode"] != 200 or not isinstance(body, dict)
            or not isinstance(body.get("id"), str) or body["id"].casefold() != target.casefold()
            or not isinstance(body.get("name"), str) or body["name"].casefold() != target.rsplit("/", 1)[-1].casefold()
            or not isinstance(body.get("type"), str) or body["type"].casefold() != kind.casefold()
            or not isinstance(body.get("properties"), dict)
            or body["properties"].get("provisioningState") not in (("Succeeded", "Updating") if pending else ("Succeeded",))):
        raise ManagementTimeNetworkError("Management time network identity or operation state is unverified")
    if tags:
        expected = {"product": config["productName"], "environment": config["environmentName"],
                    "azd-env-name": config["environmentName"]}
        if (not isinstance(body.get("tags"), dict) or any(body["tags"].get(key) != value for key, value in expected.items())
                or not isinstance(body.get("location"), str)
                or body["location"].replace(" ", "").casefold() != config["location"].replace(" ", "").casefold()):
            raise ManagementTimeNetworkError("Management time network ownership or location differs")
    return body


def _groups(config, transport, ids):
    for key in ("nwGroup", "maintGroup"):
        response = _request(transport, "GET", ids[key], GROUP_API)
        body = response.get("body")
        if (response["statusCode"] != 200 or not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != ids[key].casefold()
                or not isinstance(body.get("tags"), dict)
                or body["tags"].get("product") != config["productName"]
                or body["tags"].get("environment") != config["environmentName"]):
            raise ManagementTimeNetworkError("The selected resource group ownership is unverified")


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key not in ("etag", "systemData", "provisioningState")}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _topology(config, transport, ids):
    _groups(config, transport, ids)
    vm = _owned(config, transport, ids["vm"], "Microsoft.Compute/virtualMachines", api=COMPUTE_API)
    nic = _owned(config, transport, ids["nic"], "Microsoft.Network/networkInterfaces")
    vnet = _owned(config, transport, ids["vnet"], "Microsoft.Network/virtualNetworks")
    firewall = _owned(config, transport, ids["firewall"], "Microsoft.Network/azureFirewalls")
    policy = _owned(config, transport, ids["policy"], "Microsoft.Network/firewallPolicies")
    profile = vm["properties"].get("networkProfile")
    links = profile.get("networkInterfaces") if isinstance(profile, dict) else None
    entries = nic["properties"].get("ipConfigurations")
    subnets = vnet["properties"].get("subnets")
    if (not isinstance(links, list) or len(links) != 1 or nsg._ref(links[0]) != ids["nic"].casefold()
            or nsg._ref(nic["properties"].get("virtualMachine")) != ids["vm"].casefold()
            or not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict)
            or not isinstance(entries[0].get("properties"), dict) or not isinstance(subnets, list)):
        raise ManagementTimeNetworkError("The management VM network association is unverified")
    entry = entries[0]["properties"]
    prefix = config["network"]["subnetPrefixes"]["management"]
    try:
        nsg._address(entry.get("privateIPAddress"), prefix)
    except nsg.FixedNsgError:
        raise ManagementTimeNetworkError("The management address differs from the selected subnet") from None
    subnet = [item for item in subnets if isinstance(item, dict) and nsg._ref(item) == ids["subnet"].casefold()]
    if (nsg._ref(entry.get("subnet")) != ids["subnet"].casefold() or entry.get("publicIPAddress")
            or len(subnet) != 1 or not isinstance(subnet[0].get("properties"), dict)
            or subnet[0]["properties"].get("addressPrefix") != prefix
            or nsg._ref(subnet[0]["properties"].get("networkSecurityGroup")) != ids["nsg"].casefold()
            or nsg._ref(firewall["properties"].get("firewallPolicy")) != ids["policy"].casefold()):
        raise ManagementTimeNetworkError("The management subnet or Firewall policy association differs")
    return _clean({"vm": {"id": vm["id"], "vmId": vm["properties"].get("vmId"),
                           "timeCreated": vm["properties"].get("timeCreated"), "interfaces": links},
                   "nic": nic, "subnet": subnet[0], "firewall": firewall, "policy": policy})


def _phc(config, transport):
    try:
        proof = require_management_host_time_core_ready(config, transport)
    except ManagementHostTimeError as error:
        raise ManagementTimeNetworkError(str(error)) from None
    if proof.get("newManagementVm") is not False or proof.get("hostTimePrepared") is not True:
        raise ManagementTimeNetworkError("An existing PHC-prepared management VM is required")
    return proof


def _nsg_plan(config, transport, ids):
    parent = _owned(config, transport, ids["nsg"], nsg.NSG_TYPE)
    try:
        inventory = nsg._inventory(parent)
        nsg._defaults(parent)
    except nsg.FixedNsgError as error:
        raise ManagementTimeNetworkError(str(error)) from None
    associations = parent["properties"].get("subnets")
    if not isinstance(associations, list) or len(associations) != 1 or nsg._ref(associations[0]) != ids["subnet"].casefold():
        raise ManagementTimeNetworkError("The management NSG is not dedicated to its owned subnet")
    targets = [name for name in RETIRED_NSG if name in inventory]
    if len(targets) > 1:
        raise ManagementTimeNetworkError("The management NTP child inventory is ambiguous")
    target = targets[0] if targets else None
    if target:
        expected = {"priority": RETIRED_NSG[target], "direction": "Outbound", "access": "Allow", "protocol": "Udp",
                    "sourcePortRange": ["*"], "destinationPortRange": ["123"],
                    "sourceAddressPrefix": [config["network"]["subnetPrefixes"]["management"]],
                    "destinationAddressPrefix": ["Internet"]}
        try:
            if nsg._properties(inventory[target]) != expected:
                raise nsg.FixedNsgError("The management NTP child differs from its exact retired definition")
            nsg._etag(inventory[target])
            child = _request(transport, "GET", ids[target])
            actual_child = child.get("body")
            if (child["statusCode"] != 200 or not isinstance(actual_child, dict)
                    or nsg._ref(actual_child) != ids[target].casefold()
                    or str(actual_child.get("name", "")).casefold() != target
                    or str(actual_child.get("type", "")).casefold() != (nsg.NSG_TYPE + "/securityRules").casefold()
                    or not isinstance(actual_child.get("properties"), dict)
                    or actual_child["properties"].get("provisioningState") != "Succeeded"
                    or nsg._properties(actual_child) != expected
                    or nsg._etag(actual_child) != nsg._etag(inventory[target])):
                raise nsg.FixedNsgError("The management NTP parent and child inventories differ")
        except nsg.FixedNsgError as error:
            raise ManagementTimeNetworkError(str(error)) from None
    return parent, target


def _rcg_properties(properties):
    """Keep only writable RCG settings; validate its documented computed size.

    Microsoft.Network 2025-01-01 firewallPolicy.json defines size as a
    readOnly string in FirewallPolicyRuleCollectionGroupProperties.
    https://learn.microsoft.com/en-us/rest/api/firewall/firewall-policy-rule-collection-groups/get?view=rest-firewall-2025-01-01
    """
    if (not isinstance(properties, dict)
            or set(properties) - {"priority", "ruleCollections", "provisioningState", "size"}):
        raise ManagementTimeNetworkError("The platform rule collection group has unknown conditions")
    if "size" in properties:
        size = properties["size"]
        if (not isinstance(size, str) or not size.strip() or len(size) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in size)):
            raise ManagementTimeNetworkError("The platform rule collection computed size is malformed")
    return {key: copy.deepcopy(value) for key, value in properties.items()
            if key not in ("size", "provisioningState")}


def _rcg_snapshot(group):
    result = copy.deepcopy(group)
    result["properties"] = _rcg_properties(group["properties"])
    return result


def _rcg_plan(config, transport, ids):
    group = _owned(config, transport, ids["rcg"], "Microsoft.Network/firewallPolicies/ruleCollectionGroups", tags=False)
    props = _rcg_properties(group["properties"])
    if type(props.get("priority")) is not int or props["priority"] != 300:
        raise ManagementTimeNetworkError("The platform rule collection group has unknown conditions")
    collections = props.get("ruleCollections")
    if not isinstance(collections, list) or not collections:
        raise ManagementTimeNetworkError("The platform rule collection inventory is incomplete")
    names = set(); priorities = set(); matched = []
    for collection in collections:
        if (not isinstance(collection, dict) or set(collection) - {"name", "priority", "ruleCollectionType", "action", "rules"}
                or not isinstance(collection.get("name"), str)
                or collection["name"].casefold() in names or type(collection.get("priority")) is not int
                or not 100 <= collection["priority"] <= 65000 or collection["priority"] in priorities
                or collection.get("ruleCollectionType") not in ("FirewallPolicyFilterRuleCollection", "FirewallPolicyNatRuleCollection")
                or collection.get("action") not in ({"type": "Allow"}, {"type": "Deny"}, {"type": "Dnat"})
                or not isinstance(collection.get("rules"), list)):
            raise ManagementTimeNetworkError("The platform collections are ambiguous")
        names.add(collection["name"].casefold()); priorities.add(collection["priority"])
        if ((collection["ruleCollectionType"] == "FirewallPolicyNatRuleCollection")
                != (collection["action"] == {"type": "Dnat"})):
            raise ManagementTimeNetworkError("The platform collection action and kind disagree")
        rule_names = set()
        for rule in collection["rules"]:
            if not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or rule["name"].casefold() in rule_names:
                raise ManagementTimeNetworkError("The platform rule identities are ambiguous")
            rule_names.add(rule["name"].casefold())
            common = {"name", "ruleType", "description", "sourceAddresses", "sourceIpGroups", "ipv6Rule"}
            fields = {
                "NetworkRule": common | {"ipProtocols", "destinationAddresses", "destinationPorts", "destinationFqdns", "destinationIpGroups"},
                "ApplicationRule": common | {"protocols", "targetFqdns", "targetUrls", "fqdnTags", "terminateTLS", "httpHeadersToInsert", "webCategories"},
                "NatRule": common | {"ipProtocols", "destinationAddresses", "destinationPorts", "translatedAddress", "translatedPort", "translatedFqdn"},
            }
            if rule.get("ruleType") not in fields or set(rule) - fields[rule["ruleType"]]:
                raise ManagementTimeNetworkError("The platform rules contain unsupported properties")
            # The reviewed 2025-01-01 public Swagger does not declare this
            # provider extension. Preserve non-target flags without assuming
            # they are read-only, and reject anything except an actual bool.
            if "ipv6Rule" in rule and type(rule["ipv6Rule"]) is not bool:
                raise ManagementTimeNetworkError("The platform rule ipv6Rule flag is malformed")
            if ("description" in rule and rule["description"] is not None and not isinstance(rule["description"], str)):
                raise ManagementTimeNetworkError("The platform rule description is malformed")
            arrays = {"sourceAddresses", "sourceIpGroups", "destinationAddresses", "destinationPorts",
                      "destinationFqdns", "destinationIpGroups", "ipProtocols", "targetFqdns", "targetUrls", "fqdnTags", "webCategories"}
            for field in arrays & set(rule):
                if not isinstance(rule[field], list) or any(not isinstance(item, str) for item in rule[field]):
                    raise ManagementTimeNetworkError("The platform rule conditions are malformed")
            if rule["name"].casefold() == FW_RULE:
                matched.append((collection, rule))
    vm = [collection for collection in collections if collection["name"].casefold() == FW_COLLECTION]
    if (len(vm) != 1 or vm[0].get("priority") != 210
            or vm[0].get("ruleCollectionType") != "FirewallPolicyFilterRuleCollection"
            or vm[0].get("action") != {"type": "Allow"} or len(matched) > 1):
        raise ManagementTimeNetworkError("The management Firewall collection identity or action differs")
    changed = bool(matched)
    if changed:
        collection, rule = matched[0]
        expected = {"name": FW_RULE, "ruleType": "NetworkRule",
                    "sourceAddresses": [config["network"]["subnetPrefixes"]["management"]],
                    "destinationAddresses": ["*"], "destinationPorts": ["123"], "ipProtocols": ["UDP"]}
        normalized = copy.deepcopy(rule)
        if "ipv6Rule" in normalized:
            if normalized["ipv6Rule"] is not False:
                raise ManagementTimeNetworkError("The management IPv4 NTP target has an unsupported ipv6Rule flag")
            # Only the comparison omits this known false flag. Non-target
            # payload and readback retain their original boolean values.
            normalized.pop("ipv6Rule")
        normalized.pop("description", None)
        for field in ("sourceIpGroups", "destinationIpGroups", "destinationFqdns"):
            if normalized.get(field) == []:
                normalized.pop(field)
        if collection is not vm[0] or normalized != expected:
            raise ManagementTimeNetworkError("The management Firewall NTP rule differs from its exact retired definition")
        _etag(group)
    desired = {"priority": props["priority"], "ruleCollections": copy.deepcopy(collections)}
    for collection in desired["ruleCollections"]:
        if collection["name"].casefold() == FW_COLLECTION:
            collection["rules"] = [rule for rule in collection["rules"] if rule["name"].casefold() != FW_RULE]
    return group, desired, changed


def _etag(body):
    try:
        return nsg._etag(body)
    except nsg.FixedNsgError as error:
        raise ManagementTimeNetworkError(str(error)) from None


def _snapshot(parent):
    try:
        return nsg._snapshot(parent)
    except (KeyError, TypeError, ValueError):
        raise ManagementTimeNetworkError("The management NSG snapshot is incomplete") from None


def retire_management_time_network(config, transport, *, execute=False, record=None,
                                   max_checks=30, wait_seconds=2, sleep=time.sleep):
    """Plan or perform at most one NSG DELETE and one Firewall RCG PUT."""
    if (type(execute) is not bool or type(max_checks) is not int or not 1 <= max_checks <= 120
            or type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 60
            or execute and not callable(record)):
        raise ManagementTimeNetworkError("Management time network execution requires valid limits and a protected journal")
    ids = operation_ids(config)
    proof = _phc(config, transport)
    topology = _topology(config, transport, ids)
    parent, target = _nsg_plan(config, transport, ids)
    rcg, desired_rcg, firewall_change = _rcg_plan(config, transport, ids)
    emit = record if callable(record) else (lambda event, payload: None)
    emit("plan", {"execute": execute, "phcPreparation": proof, "topology": topology,
                  "nsgBefore": parent, "firewallBefore": rcg, "firewallExpected": desired_rcg,
                  "nsgChild": target, "azureWrites": 0})
    writes = 0
    if execute and target:
        _phc(config, transport)
        if _topology(config, transport, ids) != topology:
            raise ManagementTimeNetworkError("The management topology changed before NTP retirement")
        fresh, name = _nsg_plan(config, transport, ids)
        current_rcg, _, current_change = _rcg_plan(config, transport, ids)
        if (name != target or _snapshot(fresh) != _snapshot(parent) or _etag(fresh) != _etag(parent)
                or _rcg_snapshot(current_rcg) != _rcg_snapshot(rcg) or current_change != firewall_change):
            raise ManagementTimeNetworkError("The management NTP inventories changed before deletion")
        expected = copy.deepcopy(parent)
        expected["properties"]["securityRules"] = [row for row in expected["properties"]["securityRules"]
                                                      if row["name"].casefold() != target]
        child = next(row for row in fresh["properties"]["securityRules"] if row["name"].casefold() == target)
        emit("attempt", {"method": "DELETE", "target": ids[target], "ordinal": 1, "ifMatch": _etag(child)})
        _request(transport, "DELETE", ids[target], headers={"If-Match": _etag(child)})
        writes += 1
        for index in range(max_checks):
            response = _request(transport, "GET", ids[target])
            if response["statusCode"] == 200:
                current_child = response.get("body")
                try:
                    if (not isinstance(current_child, dict) or nsg._ref(current_child) != ids[target].casefold()
                            or str(current_child.get("type", "")).casefold() != (nsg.NSG_TYPE + "/securityRules").casefold()
                            or nsg._properties(current_child) != nsg._properties(child)):
                        raise nsg.FixedNsgError("The retiring NTP child changed during readback")
                except nsg.FixedNsgError as error:
                    raise ManagementTimeNetworkError(str(error)) from None
            observed = _owned(config, transport, ids["nsg"], nsg.NSG_TYPE, pending=True)
            snapshot = _snapshot(observed)
            if response["statusCode"] == 404 and snapshot == _snapshot(expected) and observed["properties"]["provisioningState"] == "Succeeded":
                parent = observed; emit("nsg-removed", {"azureWrites": writes}); break
            if snapshot not in (_snapshot(parent), _snapshot(expected)):
                raise ManagementTimeNetworkError("Non-target NSG conditions changed during retirement")
            if index + 1 == max_checks:
                raise ManagementTimeNetworkError("Management NTP deletion is unresolved; inspect before retrying")
            sleep(wait_seconds)
    if execute and firewall_change:
        _phc(config, transport)
        if _topology(config, transport, ids) != topology:
            raise ManagementTimeNetworkError("The management topology changed before Firewall retirement")
        current_parent, remaining = _nsg_plan(config, transport, ids)
        fresh, desired, changed = _rcg_plan(config, transport, ids)
        if (remaining is not None or _snapshot(current_parent) != _snapshot(parent)
                or _rcg_snapshot(fresh) != _rcg_snapshot(rcg) or not changed or desired != desired_rcg):
            raise ManagementTimeNetworkError("The management inventories changed before the Firewall update")
        emit("attempt", {"method": "PUT", "target": ids["rcg"], "ordinal": writes + 1, "ifMatch": _etag(fresh)})
        _request(transport, "PUT", ids["rcg"], body={"properties": desired}, headers={"If-Match": _etag(fresh)})
        writes += 1
        for index in range(max_checks):
            observed = _owned(config, transport, ids["rcg"], "Microsoft.Network/firewallPolicies/ruleCollectionGroups", tags=False, pending=True)
            clean = _rcg_properties(observed["properties"])
            if clean == desired and observed["properties"]["provisioningState"] == "Succeeded":
                rcg = observed; emit("firewall-removed", {"azureWrites": writes}); break
            if clean not in (_rcg_properties(fresh["properties"]), desired):
                raise ManagementTimeNetworkError("Non-target Firewall conditions changed during retirement")
            if index + 1 == max_checks:
                raise ManagementTimeNetworkError("Management Firewall update is unresolved; inspect before retrying")
            sleep(wait_seconds)
    if execute:
        if _topology(config, transport, ids) != topology:
            raise ManagementTimeNetworkError("The management topology final readback differs")
        final_parent, remaining = _nsg_plan(config, transport, ids)
        final_rcg, _, still_present = _rcg_plan(config, transport, ids)
        if (remaining is not None or still_present or _snapshot(final_parent) != _snapshot(parent)
                or _rcg_properties(final_rcg["properties"]) != desired_rcg):
            raise ManagementTimeNetworkError("Management NTP final readback differs from the reviewed result")
    result = {"status": "completed" if execute else "planned", "plannedNsgDeletes": int(target is not None),
              "plannedFirewallUpdates": int(firewall_change), "azureWrites": writes,
              "currentClockMeasured": False, "nonAtomicComparison": True}
    emit("result", result)
    return result
