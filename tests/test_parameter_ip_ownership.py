"""環境IP入力・標準DNSの所有元と、生成ARMまでの値の伝達を検証する。

合成した企業用CIDRでローカルのloader・正規化・Bicepを通す。Azureの
到達性やNSG/Firewallの実際の適用結果を、ここで合格と扱うことはしない。
"""
from bicep_helpers import compile_bicep

import copy
from ipaddress import collapse_addresses, ip_network
import json
from pathlib import Path
import sys
import tempfile
import unittest

from arm_helpers import ArmExpression
from configuration_fixtures import fixture
from schema_helpers import local_schema_validator


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.configuration import (ConfigurationError, build_bicep_configuration,
                           load_configuration_source, normalize_configuration)
from deployment.lifecycle import parameters as arm_parameters


NETWORK_INPUTS = {
    "addressSpaces": ["10.40.0.0/24", "10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"],
    "subnetPrefixes": {"agw": "10.40.0.0/24", "pep": "10.40.1.0/24",
                       "firewall": "10.40.2.0/26", "apim": "10.40.2.64/26",
                       "management": "10.40.2.128/26", "aca": "10.40.2.192/26"},
}
FIREWALL_INPUTS = {
    "snatPrivateRanges": ["IANAPrivateRanges"],
    "idpsPrivateRanges": [], "bypassTrafficSettings": [],
}
RETIRED_FIREWALL_INPUTS = {
    "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
    "apimAdditionalApplicationRules", "managementAdditionalApplicationRules",
    "apimAllowedFqdns", "managementAllowedFqdns",
}
RETIRED_NETWORK_INPUTS = {
    "clientAddressPrefixes": ([], ["203.0.113.70/32"]),
    "certificatePublicProbeEnabled": (False, True),
    "dnsServers": ([], ["192.0.2.53"]),
    "operatorAddressPrefixes": ([], ["198.51.100.70/32"]),
    "operatorProtocol": ("", "Tcp"),
    "operatorPorts": ([], ["22"]),
    "ntpAddressPrefixes": ([], ["192.0.2.123/32"]),
    "ntpFqdns": ([], ["time.example.com"]),
    "managementAdditionalSecurityRules": ([], [{"name": "not-created"}]),
    "apimKeyVaultAccessEnabled": (False, True),
}


def public_documents(name="test"):
    """公開例だけを読み、実在する組織や秘密ファイルを使わない。"""
    options = json.loads((CORE / f"config/{name}.json").read_text(encoding="utf-8"))
    parameters = json.loads((CORE / f"params/{name}.example.json").read_text(encoding="utf-8"))
    parameters["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
    parameters["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
    parameters["entraTenantId"] = parameters["azureTenantId"]
    parameters["services"]["apimInstanceId"] = "abc123"
    parameters["credentialProviders"]["graphDelegated"]["clientId"] = "66666666-6666-4666-8666-666666666666"
    dns = fixture()["certificate"]
    for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
        parameters["certificate"][key] = dns[key]
    options["services"]["modelDeployments"] = []
    parameters["certificate"].pop("domainNames", None)
    return options, parameters


def custom_documents():
    options, parameters = public_documents()
    network = copy.deepcopy(NETWORK_INPUTS)
    network["addressSpaces"] = [item.replace("10.40.", "10.70.") for item in network["addressSpaces"]]
    network["subnetPrefixes"] = {key: value.replace("10.40.", "10.70.")
                                  for key, value in network["subnetPrefixes"].items()}
    parameters["network"] = network
    options["network"]["firewall"].update(sku="Premium", idpsMode="Alert")
    return options, parameters


def load_documents(options, parameters):
    with tempfile.TemporaryDirectory(prefix="core-ip-inputs-") as folder:
        directory = Path(folder)
        (directory / "config.json").write_text(json.dumps(options))
        path = directory / "params.json"
        path.write_text(json.dumps({**parameters, "configFile": "config.json"}))
        return load_configuration_source(path)


class ParameterIpOwnershipTests(unittest.TestCase):
    def test_public_samples_keep_only_environment_addresses_as_network_inputs(self):
        """独立した合成IP計画を期待値にし、固定NSG/DNS/NTP設定の再導入を検出する。"""
        for name in ("test", "production"):
            options, parameters = public_documents(name)
            with self.subTest(name=name):
                self.assertEqual({key: parameters["network"][key] for key in NETWORK_INPUTS}, NETWORK_INPUTS)
                self.assertNotIn("firewall", parameters["network"])
                self.assertNotIn("auditAllowedIpAddresses", parameters["monitoring"])
                self.assertFalse(set(options["network"]) & set(NETWORK_INPUTS))
                self.assertFalse(set(options["network"]["firewall"]) & set(FIREWALL_INPUTS))
                self.assertNotIn("dnsServers", options["network"]["firewall"])
                self.assertNotIn("auditAllowedIpAddresses", options["monitoring"])
                self.assertIn("rateLimits", options["gateway"])
                for key in RETIRED_FIREWALL_INPUTS:
                    self.assertNotIn(key, options["network"]["firewall"])
                    self.assertNotIn(key, parameters["network"].get("firewall", {}))
                for key in RETIRED_NETWORK_INPUTS:
                    self.assertNotIn(key, options["network"])
                    self.assertNotIn(key, parameters["network"])

    def test_config_cannot_supply_moved_fields_even_when_empty_or_overridden(self):
        """paramsが正しくても、configへの二重記載は黙って上書きしない。"""
        paths = [("network", key) for key in NETWORK_INPUTS]
        for path in paths:
            for value in (None, [], ["192.0.2.99/32"]):
                options, parameters = custom_documents()
                target = options
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.subTest(path=path, value=value), self.assertRaisesRegex(ConfigurationError, "params"):
                    load_documents(options, parameters)

    def test_parameter_network_rejects_unknown_and_platform_only_fields(self):
        cases = [("network", "addressSpace"), ("network", "bgpRoutePropagation"),
                 ("network", "generatedIdpsPrivateRanges"),
                 ("network", "firewall", "sku"), ("network", "firewall", "dnsServer"),
                 ("monitoring", "auditAllowdIpAddresses")]
        for path in cases:
            options, parameters = custom_documents()
            target = parameters
            for key in path[:-1]:
                target = target.setdefault(key, {})
            target[path[-1]] = []
            with self.subTest(path=path), self.assertRaises(ConfigurationError):
                load_documents(options, parameters)

    def test_required_cidr_inputs_do_not_fall_back_to_platform_defaults(self):
        # loaderの部分JSON読込は互換のため許す。完全設定の正規化では必ず止まる。
        for missing in ("network", "addressSpaces", "subnetPrefixes"):
            options, parameters = custom_documents()
            if missing == "network":
                del parameters[missing]
            else:
                del parameters["network"][missing]
            with self.subTest(missing=missing), self.assertRaises(ConfigurationError):
                normalize_configuration(load_documents(options, parameters))

    def test_custom_cidrs_and_fixed_firewall_ranges_reach_bicep_input(self):
        options, parameters = custom_documents()
        normalized = normalize_configuration(load_documents(options, parameters))
        result = build_bicep_configuration(normalized)
        for key in NETWORK_INPUTS:
            self.assertEqual(result["network"][key], parameters["network"][key])
        for key in RETIRED_NETWORK_INPUTS:
            self.assertNotIn(key, result["network"])
        firewall = result["network"]["firewall"]
        self.assertEqual(firewall["snatPrivateRanges"], ["IANAPrivateRanges", *parameters["network"]["addressSpaces"]])
        self.assertEqual(firewall["bypassTrafficSettings"], [])
        for key in ("sku", "idpsMode", "zones"):
            self.assertEqual(firewall[key], options["network"]["firewall"][key])
        self.assertNotIn("dnsServers", firewall)
        self.assertFalse(RETIRED_FIREWALL_INPUTS.intersection(firewall))
        self.assertIn("graph.microsoft.com", result["generatedApimServiceFqdns"])
        self.assertEqual(result["monitoring"]["auditAllowedIpAddresses"], [])
        for kind, common in options["gateway"]["rateLimits"].items():
            for key, value in common.items():
                self.assertEqual(result["gateway"]["rateLimits"][kind][key], value)
        for kind in ("subscriptionKey", "application"):
            self.assertEqual(result["gateway"]["rateLimits"][kind]["overrides"], [])

    def test_generated_idps_ranges_subtract_only_the_gateway(self):
        options, parameters = custom_documents()
        network = parameters["network"]
        extra_internal_space = "192.0.2.0/24"
        original_gateway = network["subnetPrefixes"]["agw"]
        network["addressSpaces"].append(extra_internal_space)
        original = list(collapse_addresses(map(ip_network, (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", *network["addressSpaces"],
        ))))
        for gateway in (original_gateway, extra_internal_space):
            with self.subTest(gateway=gateway):
                network["subnetPrefixes"]["agw"] = gateway
                normalized = normalize_configuration(load_documents(options, parameters))
                result = arm_parameters(normalized, "core")["parameters"]["configuration"]["value"]
                actual = list(map(ip_network, result["network"]["generatedIdpsPrivateRanges"]))
                app_gateway = ip_network(gateway)
                self.assertTrue(all(not block.overlaps(app_gateway) for block in actual))
                self.assertTrue(all(any(block.subnet_of(old) for old in original) for block in actual))
                self.assertEqual(actual, list(collapse_addresses(actual)))
                self.assertEqual(sum(block.num_addresses for block in actual) + app_gateway.num_addresses,
                                 sum(block.num_addresses for block in original))
                for kind in ("apim", "aca"):
                    subnet = ip_network(network["subnetPrefixes"][kind])
                    self.assertTrue(any(subnet.subnet_of(block) for block in actual), kind)

    def test_fixed_audit_and_firewall_public_inputs_are_rejected_even_when_empty(self):
        paths = [("network", "firewall", key) for key in FIREWALL_INPUTS]
        paths += [("monitoring", "auditAllowedIpAddresses"), ("monitoring", "auditReaders"), ("authorization",)]
        for path in paths:
            for owner in ("parameters", "config"):
                for value in (None, [], ["PRIVATE-INPUT-SENTINEL"]):
                    options, parameters = custom_documents()
                    target = parameters if owner == "parameters" else options
                    for key in path[:-1]:
                        target = target.setdefault(key, {})
                    target[path[-1]] = value
                    with self.subTest(path=path, owner=owner, value=value), self.assertRaisesRegex(
                            ConfigurationError, "retired initial input") as caught:
                        load_documents(options, parameters)
                    self.assertNotIn("PRIVATE-INPUT-SENTINEL", str(caught.exception))

    def test_retired_network_inputs_are_rejected_in_both_public_owners(self):
        """空配列やfalseでも、廃止入力を有効な上書きとして黙って受け取らない。"""
        for owner in ("parameters", "config"):
            for key, values in RETIRED_NETWORK_INPUTS.items():
                for value in (None, *values):
                    options, parameters = custom_documents()
                    target = parameters if owner == "parameters" else options
                    target["network"][key] = copy.deepcopy(value)
                    with self.subTest(owner=owner, key=key, value=value), self.assertRaisesRegex(
                            ConfigurationError, r"network.*unsupported field"):
                        load_documents(options, parameters)

    def test_retired_firewall_inputs_are_rejected_in_split_and_legacy_inputs(self):
        for key in sorted(RETIRED_FIREWALL_INPUTS):
            populated = ["support.example.com"] if key.endswith("Fqdns") else [{"name": "not-created"}]
            for value in (None, [], populated):
                for owner in ("parameters", "config", "legacy"):
                    options, parameters = custom_documents()
                    if owner == "legacy":
                        document = fixture()
                        document["network"]["firewall"][key] = copy.deepcopy(value)
                        action = lambda: normalize_configuration(document)
                    else:
                        target = parameters if owner == "parameters" else options
                        target["network"].setdefault("firewall", {})[key] = copy.deepcopy(value)
                        action = lambda: load_documents(options, parameters)
                    with self.subTest(key=key, owner=owner, value=value), self.assertRaisesRegex(
                            ConfigurationError, r"network.*unsupported field"):
                        action()

    def test_firewall_dns_input_is_rejected_in_both_public_owners(self):
        for owner in ("parameters", "config"):
            for value in (None, [], ["168.63.129.16"], ["192.0.2.53"]):
                options, parameters = custom_documents()
                target = parameters if owner == "parameters" else options
                target["network"].setdefault("firewall", {})["dnsServers"] = copy.deepcopy(value)
                with self.subTest(owner=owner, value=value), self.assertRaisesRegex(
                        ConfigurationError, r"network.*unsupported field.*(?:firewall|dnsServers)"):
                    load_documents(options, parameters)

    def test_combined_internal_json_still_accepts_the_full_network(self):
        options, parameters = custom_documents()
        merged = load_documents(options, parameters)
        with tempfile.TemporaryDirectory(prefix="core-ip-combined-") as folder:
            path = Path(folder) / "internal.json"
            path.write_text(json.dumps(merged))
            normalized = normalize_configuration(load_configuration_source(path))
        self.assertEqual(normalized["network"]["subnetPrefixes"], parameters["network"]["subnetPrefixes"])
        for key in RETIRED_NETWORK_INPUTS:
            self.assertNotIn(key, normalized["network"])
        self.assertNotIn("dnsServers", normalized["network"]["firewall"])

    def test_combined_internal_json_cannot_restore_retired_inputs(self):
        options, parameters = custom_documents()
        current = load_documents(options, parameters)
        cases = [(False, key, values[0]) for key, values in RETIRED_NETWORK_INPUTS.items()]
        cases.append((True, "dnsServers", []))
        for firewall, key, value in cases:
            combined = copy.deepcopy(current)
            target = combined["network"]["firewall"] if firewall else combined["network"]
            target[key] = copy.deepcopy(value)
            with tempfile.TemporaryDirectory(prefix="core-ip-retired-") as folder:
                path = Path(folder) / "internal.json"
                path.write_text(json.dumps(combined))
                with self.subTest(firewall=firewall, key=key), self.assertRaisesRegex(
                        ConfigurationError, r"network.*unsupported field"):
                    normalize_configuration(load_configuration_source(path))

    def test_custom_ranges_are_validated_before_any_template_is_emitted(self):
        cases = [
            ("spaces-overlap", lambda n: n["addressSpaces"].append("10.70.0.0/25")),
            ("subnets-overlap", lambda n: n["subnetPrefixes"].update(management="10.70.2.64/26")),
            ("outside-vnet", lambda n: n["subnetPrefixes"].update(management="10.71.2.128/26")),
            ("too-small", lambda n: n["subnetPrefixes"].update(agw="10.70.0.0/25")),
            ("host-bits", lambda n: n["addressSpaces"].__setitem__(0, "10.70.0.1/24")),
            ("ipv6", lambda n: n["addressSpaces"].__setitem__(0, "2001:db8::/64")),
        ]
        for label, change in cases:
            options, parameters = custom_documents()
            change(parameters["network"])
            with self.subTest(label=label), self.assertRaises(ConfigurationError):
                normalize_configuration(load_documents(options, parameters))

    def test_schema_requires_network_and_rejects_ip_settings_in_config(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        schemas = {kind: json.loads((CORE / path).read_text()) for kind, path in (
            ("params", "schemas/params.schema.json"), ("config", "schemas/options.schema.json"))}
        options, parameters = custom_documents()
        for schema in schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)
        params_validator = local_schema_validator(schemas["params"])
        options_validator = local_schema_validator(schemas["config"])
        self.assertTrue(params_validator.is_valid(parameters), list(params_validator.iter_errors(parameters)))
        self.assertTrue(options_validator.is_valid(options), list(options_validator.iter_errors(options)))
        for missing in ("network", "addressSpaces", "subnetPrefixes"):
            value = copy.deepcopy(parameters)
            del (value if missing == "network" else value["network"])[missing]
            with self.subTest(missing=missing):
                self.assertFalse(params_validator.is_valid(value))
        for group, fields in (("network", NETWORK_INPUTS), ("firewall", FIREWALL_INPUTS),
                              ("monitoring", {"auditAllowedIpAddresses": []})):
            for key in fields:
                value = copy.deepcopy(options)
                target = value["network"]["firewall"] if group == "firewall" else value[group]
                target[key] = copy.deepcopy(fields[key])
                with self.subTest(group=group, key=key):
                    self.assertFalse(options_validator.is_valid(value))
        for owner, document, validator in (("params", parameters, params_validator),
                                            ("config", options, options_validator)):
            for path in [("network", "firewall", key) for key in FIREWALL_INPUTS] + [("monitoring", "auditAllowedIpAddresses")]:
                value = copy.deepcopy(document)
                target = value
                for key in path[:-1]:
                    target = target.setdefault(key, {})
                target[path[-1]] = []
                with self.subTest(owner=owner, fixed=".".join(path)):
                    self.assertFalse(validator.is_valid(value))
            for key, values in RETIRED_NETWORK_INPUTS.items():
                for retired in values:
                    value = copy.deepcopy(document)
                    value["network"][key] = copy.deepcopy(retired)
                    with self.subTest(owner=owner, key=key, retired=retired):
                        self.assertFalse(validator.is_valid(value))
            value = copy.deepcopy(document)
            value["network"].setdefault("firewall", {})["dnsServers"] = []
            with self.subTest(owner=owner, key="firewall.dnsServers"):
                self.assertFalse(validator.is_valid(value))


class NetworkArmExpression(ArmExpression):
    """既存のARM読取器を、実ネットワーク式の配列・UDF展開だけ拡張する。"""
    def __init__(self, expression, template, cache=None):
        super().__init__(expression)
        self.template = template
        self.cache = {} if cache is None else cache

    def resolve(self, value, parameters):
        if isinstance(value, dict):
            return {key: self.resolve(item, parameters) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, parameters) for item in value]
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return NetworkArmExpression(value, self.template, self.cache).evaluate(parameters, 0)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] != "call":
            return super().evaluate(parameters, index, tree, bindings)
        _, name, arguments = tree
        if name == "variables":
            key = self.evaluate(parameters, index, arguments[0], bindings)
            if key not in self.cache:
                self.cache[key] = self.resolve(self.template["variables"][key], parameters)
            return self.cache[key]
        if name.startswith("__bicep."):
            namespace, member = name.split(".", 1)
            function = next(item for item in self.template["functions"] if item["namespace"] == namespace)["members"][member]
            values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
            supplied = dict(zip((item["name"] for item in function["parameters"]), values))
            return self.resolve(function["output"]["value"], supplied)
        if name == "null":
            return None
        if name == "resourceId":
            return "/mock/" + "/".join(str(self.evaluate(parameters, index, item, bindings)) for item in arguments)
        extra = {"length", "empty", "concat", "range", "map", "flatten", "union", "shallowMerge", "true", "false", "and", "string", "environment", "contains", "toLower", "replace"}
        if name not in extra:
            return super().evaluate(parameters, index, tree, bindings)
        values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
        if name == "length": return len(values[0])
        if name == "empty": return not values[0]
        if name == "concat": return [item for group in values for item in group]
        if name == "range": return list(range(values[0], values[0] + values[1]))
        if name == "map": return [values[1](item) for item in values[0]]
        if name == "flatten": return [item for group in values[0] for item in group]
        if name == "true": return True
        if name == "false": return False
        if name == "and": return all(values)
        if name == "contains": return values[1] in values[0]
        if name == "toLower": return values[0].lower()
        if name == "replace": return values[0].replace(values[1], values[2])
        if name == "string": return str(values[0])
        # 公開Azureクラウドの既知suffixを合成する。実環境やAzure APIは読まない。
        if name == "environment": return {"suffixes": {"storage": "core.windows.net"}}
        if name == "shallowMerge": values = values[0]
        if name in ("union", "shallowMerge"):
            if isinstance(values[0], dict):
                return {key: value for group in values for key, value in group.items()}
            result = []
            for group in values:
                for item in group:
                    if item not in result: result.append(item)
            return result
        raise AssertionError("Unsupported network ARM function: " + name)


class ParameterIpCompiledNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'network': compile_bicep('infra/modules/network.bicep'),
            'network-bindings': compile_bicep('infra/modules/network-bindings.bicep'),
            'portal-network-bindings': compile_bicep('infra/modules/portal-network-bindings.bicep'),
        }

    def configuration(self):
        options, parameters = custom_documents()
        return build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))

    def resolver(self, module, config):
        template = self.templates[module]
        parameters = {"config": config["network"], "location": config["location"], "resourcePrefix": "test-fixture", "subnetPrefixes": config["network"]["subnetPrefixes"],
            "firewallTlsInspection": {"managedIdentityResourceId": "/synthetic/uami", "intermediateCaSecretId": "https://fixture.example.com/secrets/ca"},
            "generatedApimServiceFqdns": config["generatedApimServiceFqdns"],
            "gatewayPublicHostName": config["gateway"]["publicHostName"],
            "portalPublicHostName": config["portal"]["publicHostName"],
            "certificateDirectory": config["certificate"]["directory"],
            "foundryPrivateIps": ["10.70.1.4"], "keyVaultPrivateIps": ["10.70.1.5"],
            "auditStorageJapanPrivateIps": ["10.70.1.12"], "auditStorageFoundryPrivateIps": ["10.70.1.14"],
            "managementPrivateIp": "10.70.2.132", "firewallPublicIpAddress": "192.0.2.10",
            "apimPrivateIp": "10.70.2.68", "appGatewayPublicIpAddress": "198.51.100.10",
            "existingNetworkSecurityGroupNames": [],
            "nsgNames": {key: "nsg-test-fixture-" + suffix
                         for key, suffix in (("agw", "agw"), ("apim", "apim"), ("pep", "pep"),
                                             ("management", "vm"), ("aca", "aca"))}}
        return NetworkArmExpression("[createObject()]", template), parameters

    def test_custom_vnet_address_spaces_and_all_six_subnets_are_in_compiled_arm(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        properties = self.templates["network"]["resources"]["vnet"]["properties"]
        self.assertEqual(expression.resolve(properties["addressSpace"], parameters), {"addressPrefixes": config["network"]["addressSpaces"]})
        self.assertEqual(expression.resolve(properties["dhcpOptions"], parameters), {"dnsServers": []})
        subnets = expression.resolve(properties["subnets"], parameters)
        self.assertEqual([item["properties"]["addressPrefix"] for item in subnets],
                         [config["network"]["subnetPrefixes"][key] for key in ("agw", "firewall", "apim", "pep", "management", "aca")])
        self.assertEqual(len(subnets), 6)
        for subnet, suffix in zip(subnets, ("agw", None, "apim", "pep", "vm", "aca")):
            with self.subTest(suffix=suffix):
                if suffix is None:
                    self.assertEqual(subnet["name"], "AzureFirewallSubnet")
                    self.assertNotIn("networkSecurityGroup", subnet["properties"])
                else:
                    self.assertEqual(subnet["properties"]["networkSecurityGroup"]["id"],
                                     "/mock/Microsoft.Network/networkSecurityGroups/nsg-test-fixture-" + suffix)
        self.assertEqual(subnets[3]["properties"]["privateEndpointNetworkPolicies"],
                         "NetworkSecurityGroupEnabled")

    def test_firewall_and_its_public_ip_default_to_azure_zone_allocation(self):
        config = self.configuration()
        config["network"]["appGatewayZones"] = ["1", "2", "3"]
        resources = self.templates["network"]["resources"]
        for supplied, expected in (([], None), (["2"], ["2"])):
            with self.subTest(zones=supplied):
                config["network"]["firewall"]["zones"] = supplied
                expression, parameters = self.resolver("network", config)
                firewall = resources["firewall"]
                self.assertIn("firewallPublicIp", firewall["dependsOn"])
                for name in ("firewall", "firewallPublicIp"):
                    self.assertEqual(expression.resolve(resources[name]["zones"], parameters), expected)
                self.assertEqual(expression.resolve(resources["appGatewayPublicIp"]["zones"], parameters),
                                 ["1", "2", "3"])

    def test_aca_delegation_links_the_subnet_to_the_firewall_egress_route(self):
        config = self.configuration()
        config["network"]["subnetPrefixes"]["aca"] = "10.70.3.128/27"
        expression, parameters = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        subnets = expression.resolve(resources["vnet"]["properties"]["subnets"], parameters)
        self.assertEqual(len(subnets), 6)
        aca = subnets[-1]["properties"]
        self.assertEqual(aca["addressPrefix"], "10.70.3.128/27")
        self.assertEqual(aca["delegations"][0]["properties"]["serviceName"], "Microsoft.App/environments")
        self.assertIs(aca["defaultOutboundAccess"], False)
        self.assertEqual(aca["routeTable"]["id"], "/mock/Microsoft.Network/routeTables/rt-test-fixture-aca")
        self.assertIs(resources["portalRouteTable"]["properties"]["disableBgpRoutePropagation"], True)

        routes = resources["portalRoutes"]
        self.assertIn("firewall", routes["dependsOn"])
        self.assertIn("portalRouteTable", routes["dependsOn"])
        self.assertEqual(expression.resolve(routes["properties"]["addressPrefix"], parameters), "0.0.0.0/0")
        self.assertEqual(routes["properties"]["nextHopType"], "VirtualAppliance")
        self.assertEqual(routes["properties"]["nextHopIpAddress"],
                         "[reference('firewall').ipConfigurations[0].properties.privateIPAddress]")

    def test_custom_cidrs_and_allocated_ips_feed_static_and_dynamic_nsg_rules(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        static = expression.resolve("[variables('staticNsgRules')]", parameters)
        rules = {(entry["nsgIndex"], entry["rule"]["name"]): entry["rule"]["properties"] for entry in static}
        for name, priority, port in (("allow-client-http-ingress", 121, "80"),
                                     ("allow-client-https-ingress", 122, "443")):
            with self.subTest(rule=name):
                rule = rules[0, name]
                self.assertEqual(rule["sourceAddressPrefix"], "0.0.0.0/0")
                self.assertEqual(rule["destinationAddressPrefix"], "10.70.0.0/24")
                self.assertEqual((rule["priority"], rule["destinationPortRange"]), (priority, port))
        self.assertEqual(rules[1, "allow-firewall-https-ingress"]["sourceAddressPrefix"], config["network"]["subnetPrefixes"]["firewall"])
        self.assertEqual(rules[1, "allow-firewall-https-ingress"]["destinationAddressPrefix"], "10.70.2.64/26")
        self.assertNotIn((3, "allow-time-sync-egress"), rules)
        self.assertEqual(rules[1, "allow-time-sync-egress"]["sourceAddressPrefix"], "10.70.2.64/26")
        self.assertEqual(rules[1, "allow-time-sync-egress"]["destinationAddressPrefix"], "Internet")
        self.assertEqual(rules[1, "allow-time-sync-egress"]["priority"], 328)
        self.assertEqual(rules[4, "allow-firewall-https-ingress"]["destinationAddressPrefix"], "10.70.2.192/26")
        expression, parameters = self.resolver("network-bindings", config)
        entries = expression.resolve("[variables('dynamicRules')]", parameters)
        dynamic = {(entry["nsgIndex"], entry["rule"]["name"]): entry["rule"]["properties"] for entry in entries}
        self.assertEqual(dynamic[0, "allow-firewall-public-https-ingress"]["sourceAddressPrefix"], "192.0.2.10/32")
        self.assertEqual(dynamic[0, "allow-firewall-public-https-ingress"]["priority"], 120)
        self.assertEqual(dynamic[0, "allow-apim-https-egress"]["destinationAddressPrefix"], "10.70.2.68/32")
        self.assertEqual(dynamic[1, "allow-mgmt-vm-https-ingress"]["sourceAddressPrefix"], "10.70.2.132/32")
        self.assertEqual(dynamic[1, "allow-mgmt-vm-https-ingress"]["destinationAddressPrefix"], "10.70.2.64/26")
        self.assertEqual(dynamic[3, "allow-apim-https-egress"]["sourceAddressPrefix"], "10.70.2.132/32")
        self.assertEqual(dynamic[3, "allow-apim-https-egress"]["destinationAddressPrefix"], "10.70.2.64/26")
        self.assertEqual(dynamic[4, "allow-appgw-https-egress"]["destinationAddressPrefix"], "198.51.100.10/32")

    def test_app_gateway_firewall_uses_only_the_two_https_backend_names(self):
        template = self.templates["portal-network-bindings"]
        collection = template["resources"]["ingressRules"]["properties"]["ruleCollections"]
        apim_host, portal_host = "gateway.example.com", "portal-backend.example.com"
        gateway_subnet = "192.0.2.0/24"
        parameters = {"agwPrefix": gateway_subnet, "apimGatewayFqdn": apim_host, "portalBackendHost": portal_host}
        resolved, = NetworkArmExpression("[createObject()]", template).resolve(collection, parameters)
        self.assertEqual(resolved["action"], {"type": "Allow"})
        self.assertEqual(resolved["rules"], [
            {"name": name, "ruleType": "ApplicationRule", "sourceAddresses": [gateway_subnet],
             "protocols": [{"protocolType": "Https", "port": 443}], "targetFqdns": [host], "terminateTLS": True}
            for name, host in (("allow-appgw-apim-https", apim_host), ("allow-appgw-portal-https", portal_host))
        ])

    def test_apim_certificate_split_preserves_the_previous_effective_allow_scope(self):
        """証明書・更新・goの分割で旧HTTP/HTTPS許可を増減させない。"""
        config = self.configuration()
        expression, supplied = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        collections = expression.resolve(resources["egressRules"]["properties"], supplied)["ruleCollections"]
        collection, = [item for item in collections if item["name"] == "allow-apim-egress"]
        certificate, = [rule for rule in collection["rules"]
                         if rule["name"] == "allow-apim-certificate-validation"]
        # 分割前の許可集合を独立した回帰基準として保持する。
        previous_certificate_hosts = {
            "mscrl.microsoft.com", "crl.microsoft.com", "oneocsp.microsoft.com",
            "cacerts.digicert.com", "crl3.digicert.com", "csp.digicert.com", "issuer.pki.azure.com",
        }
        previous_update_hosts = {
            "*.update.microsoft.com", "*.ctldl.windowsupdate.com", "ctldl.windowsupdate.com",
            "download.windowsupdate.com", "*.windowsupdate.com", "*.delivery.mp.microsoft.com", "go.microsoft.com",
        }
        update, = [rule for rule in collection["rules"]
                   if rule["name"] == "allow-apim-windows-update"]
        microsoft_go, = [rule for rule in collection["rules"]
                         if set(rule.get("targetFqdns", [])) == {"go.microsoft.com"}]
        self.assertEqual(set(certificate["targetFqdns"]), previous_certificate_hosts)
        self.assertEqual(set(update["targetFqdns"]), previous_update_hosts - {"go.microsoft.com"})
        self.assertEqual(len(certificate["targetFqdns"]), 7)
        self.assertEqual(len(update["targetFqdns"]), 6)
        self.assertEqual(len(microsoft_go["targetFqdns"]), 1)
        source = config["network"]["subnetPrefixes"]["apim"]
        observed = {
            (rule_source, host, protocol["protocolType"], protocol["port"], rule["terminateTLS"])
            for rule in (certificate, update, microsoft_go)
            for rule_source in rule["sourceAddresses"]
            for host in rule["targetFqdns"]
            for protocol in rule["protocols"]
        }
        previous = {
            (source, host, protocol, port, False)
            for host in previous_certificate_hosts | previous_update_hosts
            for protocol, port in (("Http", 80), ("Https", 443))
        }
        self.assertEqual(observed, previous)
        self.assertEqual(collection["priority"], 200)
        self.assertEqual(collection["action"], {"type": "Allow"})

    def test_management_certificate_package_split_preserves_the_previous_effective_allow_scope(self):
        """管理VMの用途分割で旧11宛先のHTTP/HTTPS許可とAPIM共有宛先を維持する。"""
        config = self.configuration()
        expression, supplied = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        collections = expression.resolve(resources["egressRules"]["properties"], supplied)["ruleCollections"]
        collection, = [item for item in collections if item["name"] == "allow-vm-egress"]
        certificate, = [rule for rule in collection["rules"]
                         if rule["name"] == "allow-mgmt-vm-certificate-validation"]
        packages, = [rule for rule in collection["rules"]
                      if rule["name"] == "allow-mgmt-vm-linux-packages"]
        previous_certificate_hosts = {
            "mscrl.microsoft.com", "crl.microsoft.com", "oneocsp.microsoft.com",
            "cacerts.digicert.com", "crl3.digicert.com", "csp.digicert.com", "issuer.pki.azure.com",
        }
        previous_package_hosts = {
            "azure.archive.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com", "packages.microsoft.com",
        }
        self.assertEqual(set(certificate["targetFqdns"]), previous_certificate_hosts)
        self.assertEqual(set(packages["targetFqdns"]), previous_package_hosts)
        self.assertEqual(len(certificate["targetFqdns"]), 7)
        self.assertEqual(len(packages["targetFqdns"]), 4)
        source = config["network"]["subnetPrefixes"]["management"]
        observed = {
            (rule_source, host, protocol["protocolType"], protocol["port"], rule["terminateTLS"])
            for rule in (certificate, packages)
            for rule_source in rule["sourceAddresses"]
            for host in rule["targetFqdns"]
            for protocol in rule["protocols"]
        }
        previous = {
            (source, host, protocol, port, False)
            for host in previous_certificate_hosts | previous_package_hosts
            for protocol, port in (("Http", 80), ("Https", 443))
        }
        self.assertEqual(observed, previous)
        self.assertEqual(collection["priority"], 210)
        self.assertEqual(collection["action"], {"type": "Allow"})
        apim, = [item for item in collections if item["name"] == "allow-apim-egress"]
        apim_certificate, = [rule for rule in apim["rules"]
                              if rule["name"] == "allow-apim-certificate-validation"]
        self.assertEqual(set(apim_certificate["targetFqdns"]), previous_certificate_hosts)

    def test_apim_diagnostics_split_preserves_the_previous_infrastructure_allow_scope(self):
        """内部診断・Defender・portalの分割で旧8宛先の443許可を増減させない。"""
        config = self.configuration()
        expression, supplied = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        collections = expression.resolve(resources["egressRules"]["properties"], supplied)["ruleCollections"]
        collection, = [item for item in collections if item["name"] == "allow-apim-egress"]
        diagnostics, = [rule for rule in collection["rules"]
                         if rule["name"] == "allow-apim-platform-diagnostics"]
        # Global Azureの合成評価で、旧固定配列とは独立した許可集合を確認する。
        previous_diagnostics_hosts = {
            "azurewatsonanalysis-prod.core.windows.net", "*.data.microsoft.com",
            "azureprofiler.trafficmanager.net", "shavamanifestazurecdnprod1.azureedge.net",
            "shavamanifestcdnprod1.azureedge.net",
        }
        previous_remaining_hosts = {
            "wdcp.microsoft.com", "wdcpalt.microsoft.com", "dc.services.visualstudio.com",
        }
        defender, = [rule for rule in collection["rules"]
                      if rule["name"] == "allow-apim-windows-defender"]
        portal, = [rule for rule in collection["rules"]
                   if set(rule.get("targetFqdns", [])) == {"dc.services.visualstudio.com"}]
        self.assertEqual(set(diagnostics["targetFqdns"]), previous_diagnostics_hosts)
        self.assertEqual(set(defender["targetFqdns"]), previous_remaining_hosts - {"dc.services.visualstudio.com"})
        self.assertEqual(len(diagnostics["targetFqdns"]), 5)
        self.assertEqual(len(defender["targetFqdns"]), 2)
        self.assertEqual(len(portal["targetFqdns"]), 1)
        source = config["network"]["subnetPrefixes"]["apim"]
        observed = {
            (rule_source, host, protocol["protocolType"], protocol["port"], rule["terminateTLS"])
            for rule in (diagnostics, defender, portal)
            for rule_source in rule["sourceAddresses"]
            for host in rule["targetFqdns"]
            for protocol in rule["protocols"]
        }
        previous = {
            (source, host, "Https", 443, False)
            for host in previous_diagnostics_hosts | previous_remaining_hosts
        }
        self.assertEqual(observed, previous)
        self.assertEqual(collection["priority"], 200)
        self.assertEqual(collection["action"], {"type": "Allow"})

    def test_fixed_dns_and_time_dependencies_are_implemented_without_config_inputs(self):
        options, parameters = custom_documents()
        config = build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))
        expression, supplied = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        self.assertEqual(expression.resolve(resources["vnet"]["properties"]["dhcpOptions"], supplied),
                         {"dnsServers": []})
        static = expression.resolve("[variables('staticNsgRules')]", supplied)
        self.assertFalse(any("dns" in entry["rule"]["name"] for entry in static))
        for key in RETIRED_NETWORK_INPUTS:
            self.assertNotIn(key, config["network"])
        firewall = expression.resolve(resources["firewallPolicy"]["properties"], supplied)
        self.assertEqual(firewall["dnsSettings"], {"enableProxy": True, "servers": []})
        collections = expression.resolve(resources["platformRules"]["properties"], supplied)["ruleCollections"]
        rules = {rule["name"]: rule for collection in collections for rule in collection["rules"]}
        self.assertNotIn("allow-mgmt-vm-ntp", rules)
        self.assertEqual(rules["allow-apim-ntp"]["sourceAddresses"], ["10.70.2.64/26"])
        self.assertEqual(rules["allow-apim-ntp"]["destinationPorts"], ["123"])
        self.assertEqual(rules["allow-apim-ntp"]["ipProtocols"], ["UDP"])
        self.assertEqual(rules["allow-apim-ntp"]["destinationAddresses"], ["*"])
        self.assertNotIn("destinationFqdns", rules["allow-apim-ntp"])

    def test_firewall_arm_keeps_config_sku_and_constructed_vnet_ranges_together(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        firewall = expression.resolve(resources["firewallPolicy"]["properties"], parameters)
        self.assertEqual(firewall["sku"]["tier"], "Premium")
        self.assertEqual(firewall["dnsSettings"]["servers"], [])
        self.assertEqual(firewall["snat"]["privateRanges"], ["IANAPrivateRanges", *config["network"]["addressSpaces"]])
        self.assertEqual(firewall["intrusionDetection"]["configuration"]["privateRanges"],
                         config["network"]["generatedIdpsPrivateRanges"])
        self.assertEqual(firewall["intrusionDetection"]["configuration"]["bypassTrafficSettings"], config["network"]["firewall"]["bypassTrafficSettings"])
        for resource in ("platformRules", "egressRules"):
            collections = expression.resolve(resources[resource]["properties"], parameters)["ruleCollections"]
            actual = [rule for collection in collections for rule in collection["rules"]]
            self.assertFalse(any(rule["name"].endswith(("-custom-ip", "-custom-web")) for rule in actual))

    def test_approved_service_rules_are_constant_and_keep_existing_certificate_allow_scope(self):
        fixed_management = {
            "management.azure.com", "cdn.oneget.org", "cdn.powershellgallery.com",
            "*.powershellgallery.com", "go.microsoft.com", "aka.ms",
        }
        for prefix in ("10.70", "10.80"):
            for directory in ("production", "staging"):
                for generated in ([], ["backend.example.com"], ["oneocsp.microsoft.com", "backend.example.com"]):
                    config = self.configuration()
                    config["network"]["subnetPrefixes"] = {
                        key: value.replace("10.70.", prefix + ".")
                        for key, value in config["network"]["subnetPrefixes"].items()}
                    config["certificate"]["directory"] = directory
                    config["generatedApimServiceFqdns"] = generated
                    expression, parameters = self.resolver("network", config)
                    resource = self.templates["network"]["resources"]["egressRules"]
                    self.assertNotIn("condition", resource)
                    self.assertEqual(resource["properties"]["priority"], 400)
                    collections = expression.resolve(resource["properties"], parameters)["ruleCollections"]
                    by_collection = {collection["name"]: collection for collection in collections}
                    with self.subTest(prefix=prefix, directory=directory, generated=generated):
                        apim = by_collection["allow-apim-egress"]
                        management = by_collection["allow-vm-egress"]
                        self.assertEqual(apim["priority"], 200)
                        self.assertEqual(management["priority"], 210)
                        for collection in (apim, management):
                            self.assertEqual(collection["action"], {"type": "Allow"})
                            self.assertEqual(collection["ruleCollectionType"], "FirewallPolicyFilterRuleCollection")
                        apim_rule, = [rule for rule in apim["rules"] if rule["name"] == "allow-apim-approved-services"]
                        management_rule, = [rule for rule in management["rules"] if rule["name"] == "allow-mgmt-vm-approved-services"]
                        for rule, source in ((apim_rule, config["network"]["subnetPrefixes"]["apim"]),
                                             (management_rule, config["network"]["subnetPrefixes"]["management"])):
                            self.assertEqual(rule["ruleType"], "ApplicationRule")
                            self.assertEqual(rule["sourceAddresses"], [source])
                            self.assertEqual(rule["protocols"], [{"protocolType": "Https", "port": 443}])
                            self.assertIs(rule["terminateTLS"], False)
                            self.assertNotIn("condition", rule)
                            self.assertTrue(rule["targetFqdns"])
                        expected_apim = {"oneocsp.microsoft.com", *generated}
                        self.assertEqual(set(apim_rule["targetFqdns"]), expected_apim)
                        self.assertEqual(len(apim_rule["targetFqdns"]), len(expected_apim))
                        certificate, = [rule for rule in apim["rules"] if rule["name"] == "allow-apim-certificate-validation"]
                        anchor = (config["network"]["subnetPrefixes"]["apim"], "oneocsp.microsoft.com", "Https", 443, False)
                        certificate_tuples = {(source, host, protocol["protocolType"], protocol["port"], certificate["terminateTLS"])
                                              for source in certificate["sourceAddresses"] for host in certificate["targetFqdns"]
                                              for protocol in certificate["protocols"]}
                        self.assertIn(anchor, certificate_tuples)
                        ca = "acme-v02.api.letsencrypt.org" if directory == "production" else "acme-staging-v02.api.letsencrypt.org"
                        expected_management = fixed_management | {ca, config["gateway"]["publicHostName"], config["portal"]["publicHostName"]}
                        self.assertEqual(set(management_rule["targetFqdns"]), expected_management)
                        self.assertEqual(len(management_rule["targetFqdns"]), len(expected_management))

    def test_static_and_dynamic_appgw_allow_children_are_unconditional(self):
        """IP確定前後の両段階で、固定規則が子リソースとして更新される。"""
        config = self.configuration()
        for module, collection, resource_name, rule_name in (
            ("network", "staticNsgRules", "staticSecurityRules", "allow-client-http-ingress"),
            ("network", "staticNsgRules", "staticSecurityRules", "allow-client-https-ingress"),
            ("network-bindings", "dynamicRules", "dynamicSecurityRules", "allow-firewall-public-https-ingress"),
        ):
            expression, supplied = self.resolver(module, config)
            entries = expression.resolve("[variables('" + collection + "')]", supplied)
            matches = [(index, entry["rule"]) for index, entry in enumerate(entries)
                       if entry["nsgIndex"] == 0 and entry["rule"]["name"] == rule_name]
            with self.subTest(module=module, rule=rule_name):
                self.assertEqual(len(matches), 1)
                index, rule = matches[0]
                self.assertEqual(rule["properties"]["access"], "Allow")
                child = self.templates[module]["resources"][resource_name]
                self.assertEqual(child["type"], "Microsoft.Network/networkSecurityGroups/securityRules")
                self.assertNotIn("condition", child)
                self.assertEqual(expression.resolve(child["copy"]["count"], supplied), len(entries))
                child_name = NetworkArmExpression(child["name"], self.templates[module]).evaluate(supplied, index)
                self.assertEqual(child_name, "nsg-test-fixture-agw/" + rule_name)


if __name__ == "__main__":
    unittest.main()
