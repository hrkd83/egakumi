"""環境IP入力・標準DNSの所有元と、生成ARMまでの値の伝達を検証する。

合成した企業用CIDRでローカルのloader・正規化・Bicepを通す。Azureの
到達性やNSG/Firewallの実際の適用結果を、ここで合格と扱うことはしない。
"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from arm_helpers import ArmExpression
from configuration_fixtures import fixture


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.configuration import (ConfigurationError, build_bicep_configuration,
                           load_configuration_source, normalize_configuration)


NETWORK_INPUTS = {
    "addressSpaces": ["10.40.0.0/24", "10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"],
    "subnetPrefixes": {"agw": "10.40.0.0/24", "pep": "10.40.1.0/24",
                       "firewall": "10.40.2.0/26", "apim": "10.40.2.64/26",
                       "management": "10.40.2.128/26", "aca": "10.40.2.192/26"},
    "clientAddressPrefixes": ["0.0.0.0/0"], "operatorAddressPrefixes": [],
    "ntpAddressPrefixes": ["Internet"],
    "managementAdditionalSecurityRules": [],
}
FIREWALL_INPUTS = {
    "snatPrivateRanges": ["IANAPrivateRanges"],
    "idpsPrivateRanges": [], "bypassTrafficSettings": [],
    "apimAdditionalNetworkRules": [], "managementAdditionalNetworkRules": [],
    "apimAdditionalApplicationRules": [], "managementAdditionalApplicationRules": [],
}


def public_documents(name="test"):
    """公開例だけを読み、実在する組織や秘密ファイルを使わない。"""
    options = json.loads((CORE / f"config/{name}.json").read_text())
    parameters = json.loads((CORE / f"params/{name}.example.json").read_text())
    parameters["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
    parameters["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
    parameters["entraTenantId"] = parameters["azureTenantId"]
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
    network.update(clientAddressPrefixes=["203.0.113.70/32"],
                   operatorAddressPrefixes=["198.51.100.70/32"],
                   ntpAddressPrefixes=["192.0.2.123/32"])
    options["network"]["dnsServers"] = []
    options["network"]["firewall"]["dnsServers"] = []
    network["managementAdditionalSecurityRules"] = [{
        "name": "allow-enterprise-support", "properties": {
            "priority": 370, "direction": "Outbound", "access": "Allow", "protocol": "Tcp",
            "sourcePortRange": "*", "destinationPortRange": "443",
            "sourceAddressPrefix": "10.70.2.128/26", "destinationAddressPrefix": "192.0.2.70/32"}}]
    firewall = copy.deepcopy(FIREWALL_INPUTS)
    firewall.update(snatPrivateRanges=["10.70.0.0/16"],
                    idpsPrivateRanges=["10.70.0.0/16"], bypassTrafficSettings=[{
                        "name": "reviewed-support", "protocol": "TCP",
                        "sourceAddresses": ["10.70.2.128/26"],
                        "destinationAddresses": ["192.0.2.70"], "destinationPorts": ["443"]}])
    for owner, source in (("apim", "10.70.2.64/26"), ("management", "10.70.2.128/26")):
        firewall[owner + "AdditionalNetworkRules"] = [{
            "name": owner + "-custom-ip", "ruleType": "NetworkRule", "ipProtocols": ["TCP"],
            "sourceAddresses": [source], "destinationAddresses": ["192.0.2.70"],
            "destinationPorts": ["443"]}]
        firewall[owner + "AdditionalApplicationRules"] = [{
            "name": owner + "-custom-web", "ruleType": "ApplicationRule",
            "sourceAddresses": [source], "protocols": [{"protocolType": "Https", "port": 443}],
            "targetFqdns": ["support.example.com"]}]
    firewall.update(apimAllowedFqdns=[], managementAllowedFqdns=[])
    network["firewall"] = firewall
    network["ntpFqdns"] = parameters["network"]["ntpFqdns"]
    parameters["network"] = network
    parameters["monitoring"]["auditAllowedIpAddresses"] = ["203.0.113.71/32"]
    options["network"]["firewall"].update(sku="Premium", idpsMode="Alert")
    parameters["network"]["firewall"]["apimAllowedFqdns"] = ["backend.example.com"]
    parameters["network"]["firewall"]["managementAllowedFqdns"] = ["support.example.com"]
    return options, parameters


def load_documents(options, parameters):
    with tempfile.TemporaryDirectory(prefix="core-ip-inputs-") as folder:
        directory = Path(folder)
        (directory / "config.json").write_text(json.dumps(options))
        path = directory / "params.json"
        path.write_text(json.dumps({**parameters, "configFile": "config.json"}))
        return load_configuration_source(path)


class ParameterIpOwnershipTests(unittest.TestCase):
    def test_public_samples_keep_environment_inputs_and_default_dns_in_their_owners(self):
        """移動元の既知値を独立期待値にし、空配列も移動漏れと区別する。"""
        for name in ("test", "production"):
            options, parameters = public_documents(name)
            with self.subTest(name=name):
                self.assertEqual({key: parameters["network"][key] for key in NETWORK_INPUTS}, NETWORK_INPUTS)
                self.assertEqual({key: parameters["network"]["firewall"][key] for key in FIREWALL_INPUTS}, FIREWALL_INPUTS)
                self.assertEqual(parameters["monitoring"]["auditAllowedIpAddresses"], [])
                self.assertFalse(set(options["network"]) & set(NETWORK_INPUTS))
                self.assertEqual(options["network"]["dnsServers"], [])
                self.assertNotIn("dnsServers", parameters["network"])
                self.assertFalse(set(options["network"]["firewall"]) & set(FIREWALL_INPUTS))
                self.assertEqual(options["network"]["firewall"]["dnsServers"], [])
                self.assertNotIn("dnsServers", parameters["network"]["firewall"])
                self.assertNotIn("auditAllowedIpAddresses", options["monitoring"])
                self.assertIn("rateLimits", options["gateway"])
                self.assertIn("operatorPorts", options["network"])
                self.assertNotIn("apimAllowedFqdns", options["network"]["firewall"])
                self.assertIn("apimAllowedFqdns", parameters["network"]["firewall"])

    def test_config_cannot_supply_moved_fields_even_when_empty_or_overridden(self):
        """paramsが正しくても、configへの二重記載は黙って上書きしない。"""
        paths = [("network", key) for key in NETWORK_INPUTS]
        paths += [("network", "firewall", key) for key in FIREWALL_INPUTS]
        paths += [("monitoring", "auditAllowedIpAddresses")]
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
        cases = [("network", "addressSpace"), ("network", "operatorPorts"),
                 ("network", "dnsServers"),
                 ("network", "firewall", "sku"), ("network", "firewall", "dnsServer"),
                 ("network", "firewall", "dnsServers"),
                 ("monitoring", "auditAllowdIpAddresses")]
        for path in cases:
            options, parameters = custom_documents()
            target = parameters
            for key in path[:-1]:
                target = target[key]
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

    def test_custom_cidrs_and_mixed_firewall_settings_reach_bicep_input(self):
        options, parameters = custom_documents()
        normalized = normalize_configuration(load_documents(options, parameters))
        result = build_bicep_configuration(normalized)
        for key in NETWORK_INPUTS:
            self.assertEqual(result["network"][key], parameters["network"][key])
        self.assertEqual(result["network"]["dnsServers"], options["network"]["dnsServers"])
        firewall = result["network"]["firewall"]
        for key in FIREWALL_INPUTS:
            self.assertEqual(firewall[key], parameters["network"]["firewall"][key])
        for key in ("sku", "idpsMode", "zones", "dnsServers"):
            self.assertEqual(firewall[key], options["network"]["firewall"][key])
        # 正規化で証明書取得等の基盤FQDNを追加するが、paramsの明示値は失わない。
        for key in ("apimAllowedFqdns", "managementAllowedFqdns"):
            self.assertTrue(set(parameters["network"]["firewall"][key]).issubset(firewall[key]))
        self.assertEqual(result["monitoring"]["auditAllowedIpAddresses"], ["203.0.113.71/32"])
        for kind, common in options["gateway"]["rateLimits"].items():
            for key, value in common.items():
                self.assertEqual(result["gateway"]["rateLimits"][kind][key], value)
        for kind in ("subscriptionKey", "application"):
            self.assertEqual(result["gateway"]["rateLimits"][kind]["overrides"], parameters["gateway"]["rateLimits"][kind]["overrides"])

    def test_explicit_empty_dns_and_operator_ranges_survive_normalization(self):
        options, parameters = custom_documents()
        options["network"]["dnsServers"] = []
        parameters["network"]["operatorAddressPrefixes"] = []
        options["network"]["firewall"]["dnsServers"] = []
        parameters["monitoring"]["auditAllowedIpAddresses"] = []
        result = build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))
        for key in ("dnsServers", "operatorAddressPrefixes"):
            self.assertEqual(result["network"][key], [])
        self.assertEqual(result["network"]["clientAddressPrefixes"], parameters["network"]["clientAddressPrefixes"])
        self.assertEqual(result["network"]["firewall"]["dnsServers"], [])
        self.assertEqual(result["monitoring"]["auditAllowedIpAddresses"], [])

    def test_parameter_dns_cannot_override_the_config_even_with_matching_values(self):
        for nested in (False, True):
            for value in ([], ["168.63.129.16"], ["10.70.2.4"]):
                options, parameters = custom_documents()
                target = parameters["network"]["firewall"] if nested else parameters["network"]
                target["dnsServers"] = value
                with self.subTest(firewall=nested, value=value), self.assertRaisesRegex(
                        ConfigurationError, r"network.*unsupported field.*dnsServers"):
                    load_documents(options, parameters)

    def test_config_dns_is_fixed_before_any_template_is_emitted(self):
        for nested in (False, True):
            for value in (["168.63.129.16"], ["10.70.2.4"], ["10.70.2.4/32"],
                          ["dns.example.com"], ["2001:db8::53"], "10.70.2.4"):
                options, parameters = custom_documents()
                target = options["network"]["firewall"] if nested else options["network"]
                target["dnsServers"] = value
                with self.subTest(firewall=nested, value=value), self.assertRaisesRegex(
                        ConfigurationError, r"network\.(?:firewall\.)?dnsServers"):
                    normalize_configuration(load_documents(options, parameters))

    def test_omitted_config_dns_keeps_azure_provided_dns_for_both_consumers(self):
        options, parameters = custom_documents()
        options["network"].pop("dnsServers")
        options["network"]["firewall"].pop("dnsServers")
        result = build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))
        self.assertEqual(result["network"]["dnsServers"], [])
        self.assertEqual(result["network"]["firewall"]["dnsServers"], [])

    def test_empty_client_ranges_are_rejected_before_bicep_configuration(self):
        options, parameters = custom_documents()
        parameters["network"]["clientAddressPrefixes"] = []
        raw = load_documents(options, parameters)
        with self.assertRaisesRegex(ConfigurationError, r"network\.clientAddressPrefixes"):
            normalize_configuration(raw)

    def test_combined_internal_json_still_accepts_the_full_network(self):
        options, parameters = custom_documents()
        merged = load_documents(options, parameters)
        with tempfile.TemporaryDirectory(prefix="core-ip-combined-") as folder:
            path = Path(folder) / "internal.json"
            path.write_text(json.dumps(merged))
            normalized = normalize_configuration(load_configuration_source(path))
        self.assertEqual(normalized["network"]["subnetPrefixes"], parameters["network"]["subnetPrefixes"])

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
        params_validator = jsonschema.Draft202012Validator(schemas["params"])
        options_validator = jsonschema.Draft202012Validator(schemas["config"])
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
        if name == "resourceId":
            return "/mock/" + "/".join(str(self.evaluate(parameters, index, item, bindings)) for item in arguments)
        extra = {"length", "empty", "concat", "range", "map", "flatten", "union", "shallowMerge", "true", "false", "and", "string", "environment"}
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
        }

    def configuration(self, empty_optional=False):
        options, parameters = custom_documents()
        if empty_optional:
            options["network"]["dnsServers"] = []
            parameters["network"]["operatorAddressPrefixes"] = []
            options["network"]["firewall"]["dnsServers"] = []
        return build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))

    def resolver(self, module, config):
        template = self.templates[module]
        parameters = {"config": config["network"], "resourcePrefix": "test-fixture", "subnetPrefixes": config["network"]["subnetPrefixes"],
            "foundryPrivateIps": ["10.70.1.4"], "keyVaultPrivateIps": ["10.70.1.5"],
            "managementPrivateIp": "10.70.2.132", "firewallPublicIpAddress": "192.0.2.10",
            "nsgNames": {key: "nsg-test-fixture-" + suffix
                         for key, suffix in (("agw", "agw"), ("apim", "apim"), ("pep", "pep"), ("management", "vm"))},
            "grpcEnabled": True, "grpcPublicPort": 8443, "grpcBackendPort": 443}
        return NetworkArmExpression("[createObject()]", template), parameters

    def test_custom_vnet_address_spaces_and_all_six_subnets_are_in_compiled_arm(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        properties = self.templates["network"]["resources"]["vnet"]["properties"]
        self.assertEqual(expression.resolve(properties["addressSpace"], parameters), {"addressPrefixes": config["network"]["addressSpaces"]})
        self.assertEqual(expression.resolve(properties["dhcpOptions"], parameters), {"dnsServers": []})
        self.assertEqual([expression.resolve(item["properties"]["addressPrefix"], parameters) for item in expression.resolve(properties["subnets"], parameters)],
                         [config["network"]["subnetPrefixes"][key] for key in ("agw", "firewall", "apim", "pep", "management", "aca")])

    def test_portal_adds_only_the_explicit_delegated_subnet(self):
        config = self.configuration()
        config["network"]["subnetPrefixes"]["aca"] = "10.70.3.128/27"
        expression, parameters = self.resolver("network", config)
        subnets = expression.resolve(self.templates["network"]["resources"]["vnet"]["properties"]["subnets"], parameters)
        self.assertEqual(len(subnets), 6)
        self.assertEqual(subnets[-1]["properties"]["addressPrefix"], "10.70.3.128/27")
        self.assertEqual(subnets[-1]["properties"]["delegations"][0]["properties"]["serviceName"], "Microsoft.App/environments")

    def test_custom_cidrs_feed_static_and_dynamic_nsg_rules(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        static = expression.resolve("[variables('staticNsgRules')]", parameters)
        rules = {(entry["nsgIndex"], entry["rule"]["name"]): entry["rule"]["properties"] for entry in static}
        self.assertEqual(rules[0, "allow-client-web"]["sourceAddressPrefix"], "203.0.113.70/32")
        self.assertEqual(rules[0, "allow-client-web"]["destinationAddressPrefix"], "10.70.0.0/24")
        self.assertEqual(rules[1, "allow-agw-api"]["destinationAddressPrefix"], "10.70.2.64/26")
        self.assertEqual(rules[3, "allow-time-sync"]["destinationAddressPrefix"], "192.0.2.123/32")
        self.assertEqual(rules[3, "allow-enterprise-support"]["sourceAddressPrefix"], "10.70.2.128/26")
        self.assertEqual(rules[0, "allow-dns-tcp"]["destinationAddressPrefix"], "168.63.129.16")
        expression, parameters = self.resolver("network-bindings", config)
        groups = expression.resolve("[variables('rulesByNsg')]", parameters)
        dynamic = {item["name"]: item["properties"] for group in groups for item in group}
        self.assertEqual(dynamic["allow-operator-management"]["sourceAddressPrefix"], "198.51.100.70/32")
        self.assertEqual(dynamic["allow-client-grpc"]["sourceAddressPrefix"], "203.0.113.70/32")
        self.assertEqual(dynamic["allow-apim-http"]["destinationAddressPrefix"], "10.70.2.64/26")

    def test_fixed_azure_dns_reaches_vnet_nsg_rules_and_firewall(self):
        options, parameters = custom_documents()
        config = build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))
        expression, supplied = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        self.assertEqual(expression.resolve(resources["vnet"]["properties"]["dhcpOptions"], supplied),
                         {"dnsServers": []})
        static = expression.resolve("[variables('staticNsgRules')]", supplied)
        rules = {(entry["nsgIndex"], entry["rule"]["name"]): entry["rule"]["properties"] for entry in static}
        for nsg_index in (0, 1, 3):
            for rule_name in ("allow-dns-tcp", "allow-dns-udp"):
                with self.subTest(nsg=nsg_index, rule=rule_name):
                    self.assertEqual(rules[nsg_index, rule_name]["destinationAddressPrefix"],
                                     "168.63.129.16")
                    self.assertEqual(rules[nsg_index, rule_name]["destinationPortRange"], "53")
        firewall = expression.resolve(resources["firewallPolicy"]["properties"], supplied)
        self.assertEqual(firewall["dnsSettings"]["servers"], [])

    def test_firewall_arm_keeps_platform_sku_and_parameter_ip_settings_together(self):
        config = self.configuration()
        expression, parameters = self.resolver("network", config)
        resources = self.templates["network"]["resources"]
        firewall = expression.resolve(resources["firewallPolicy"]["properties"], parameters)
        self.assertEqual(firewall["sku"]["tier"], "Premium")
        self.assertEqual(firewall["dnsSettings"]["servers"], [])
        self.assertEqual(firewall["snat"]["privateRanges"], ["10.70.0.0/16"])
        self.assertEqual(firewall["intrusionDetection"]["configuration"]["privateRanges"], ["10.70.0.0/16"])
        self.assertEqual(firewall["intrusionDetection"]["configuration"]["bypassTrafficSettings"], config["network"]["firewall"]["bypassTrafficSettings"])
        for resource, suffix in (("platformRules", "NetworkRules"), ("egressRules", "ApplicationRules")):
            collections = expression.resolve(resources[resource]["properties"], parameters)["ruleCollections"]
            actual = [rule for collection in collections for rule in collection["rules"]]
            for owner in ("apim", "management"):
                for rule in config["network"]["firewall"][owner + "Additional" + suffix]:
                    self.assertIn(rule, actual)

    def test_client_sources_keep_web_and_grpc_children_with_fixed_allow_priorities(self):
        for clients in (None, ["0.0.0.0/0"], ["203.0.113.70/32"],
                        ["192.0.2.0/24", "198.51.100.0/24"]):
            options, parameters = custom_documents()
            if clients is None:
                parameters["network"].pop("clientAddressPrefixes")
            else:
                parameters["network"]["clientAddressPrefixes"] = clients
            config = build_bicep_configuration(normalize_configuration(load_documents(options, parameters)))
            expected = clients if clients is not None else ["0.0.0.0/0"]
            self.assertEqual(config["network"]["clientAddressPrefixes"], expected)
            if clients is None:
                # 公開入力の省略値と、直接ARM入力で省略した場合の既定値を両方確認する。
                config["network"].pop("clientAddressPrefixes")
            for module, collection, resource_name, rule_name, priority in (
                ("network", "staticNsgRules", "staticSecurityRules", "allow-client-web", 120),
                ("network-bindings", "dynamicRules", "dynamicSecurityRules", "allow-client-grpc", 125),
            ):
                expression, supplied = self.resolver(module, config)
                entries = expression.resolve("[variables('" + collection + "')]", supplied)
                matches = [(index, entry["rule"]) for index, entry in enumerate(entries)
                           if entry["nsgIndex"] == 0 and entry["rule"]["name"] == rule_name]
                with self.subTest(clients=clients, module=module):
                    self.assertEqual(len(matches), 1)
                    index, rule = matches[0]
                    properties = rule["properties"]
                    self.assertEqual((properties["priority"], properties["access"]), (priority, "Allow"))
                    source_key = "sourceAddressPrefix" if len(expected) == 1 else "sourceAddressPrefixes"
                    self.assertEqual(properties[source_key], expected[0] if len(expected) == 1 else expected)
                    self.assertEqual(properties["destinationAddressPrefix"], "10.70.0.0/24")
                    child = self.templates[module]["resources"][resource_name]
                    self.assertEqual(child["type"], "Microsoft.Network/networkSecurityGroups/securityRules")
                    self.assertNotIn("condition", child)
                    self.assertEqual(expression.resolve(child["copy"]["count"], supplied), len(entries))
                    child_name = NetworkArmExpression(child["name"], self.templates[module]).evaluate(supplied, index)
                    self.assertEqual(child_name, "nsg-test-fixture-agw/" + rule_name)

    def test_empty_operator_ranges_omit_management_allow_and_dns_uses_azure_defaults(self):
        config = self.configuration(empty_optional=True)
        expression, parameters = self.resolver("network", config)
        rules = expression.resolve("[variables('staticNsgRules')]", parameters)
        by_name = {entry["rule"]["name"]: entry["rule"]["properties"] for entry in rules}
        self.assertEqual((by_name["allow-client-web"]["priority"], by_name["allow-client-web"]["access"]), (120, "Allow"))
        self.assertEqual(by_name["allow-dns-tcp"]["destinationAddressPrefix"], "168.63.129.16")
        vnet = self.templates["network"]["resources"]["vnet"]["properties"]
        self.assertEqual(expression.resolve(vnet["dhcpOptions"], parameters), {"dnsServers": []})
        firewall = expression.resolve(self.templates["network"]["resources"]["firewallPolicy"]["properties"], parameters)
        self.assertEqual(firewall["dnsSettings"]["servers"], [])
        expression, parameters = self.resolver("network-bindings", config)
        groups = expression.resolve("[variables('rulesByNsg')]", parameters)
        rules = {item["name"]: item["properties"] for group in groups for item in group}
        self.assertEqual((rules["allow-client-grpc"]["priority"], rules["allow-client-grpc"]["access"]), (125, "Allow"))
        self.assertNotIn("allow-operator-management", rules)


if __name__ == "__main__":
    unittest.main()
