"""Compile every owned NSG rule against one public priority contract.

These checks use synthetic network inputs and do not contact Azure.
"""
import copy
import json
from pathlib import Path
import unittest

from bicep_helpers import compile_bicep
from arm_helpers import DiscoveryExpression
import test_parameter_ip_ownership as ip_fixtures
from test_parameter_ip_ownership import NetworkArmExpression


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("agw", "apim", "pep", "management")


class NsgRulePriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / "infra/nsg-rule-priorities.json").read_text())
        cls.templates = {name: compile_bicep("infra/modules/" + name + ".bicep")
                         for name in ("network", "network-bindings", "portal-network",
                                      "credential-broker-network")}

    def parameters(self, **changes):
        fixtures = ip_fixtures.ParameterIpCompiledNetworkTests()
        fixtures.templates = self.templates
        config = fixtures.configuration()
        config["network"]["managementAdditionalSecurityRules"] = []
        _, result = fixtures.resolver("network-bindings", config)
        result.update(changes)
        return result

    def resolve(self, module, expression, parameters):
        return NetworkArmExpression("[true()]", self.templates[module]).resolve(expression, parameters)

    def entries(self, module, variable, parameters):
        return self.resolve(module, "[variables('" + variable + "')]", parameters)

    def resource(self, module, symbolic_name, rule_name):
        resources = self.templates[module]["resources"]
        if isinstance(resources, dict):
            return resources[symbolic_name]
        return next(item for item in resources
                    if item["type"] == "Microsoft.Network/networkSecurityGroups/securityRules"
                    and "'" + rule_name + "'" in item["name"])

    def compiled_rules(self, parameters):
        result = {name: {} for name in GROUPS}
        for module, variable in (("network", "staticNsgRules"),
                                 ("network-bindings", "dynamicRules"),
                                 ("network-bindings", "terminalRules")):
            for entry in self.entries(module, variable, parameters):
                group = result[GROUPS[entry["nsgIndex"]]]
                rule = entry["rule"]
                self.assertNotIn(rule["name"], group)
                group[rule["name"]] = rule["properties"]
        for module, resource, group, rule_name in (
                ("portal-network", "allowPull", "pep", "allow-aca-acr"),
                ("portal-network", "runnerRegistryAccess", "management", "allow-runner-acr"),
                ("credential-broker-network", "sqlIn", "pep", "allow-credential-broker-sql"),
                ("credential-broker-network", "vaultIn", "pep", "allow-credential-broker-vault"),
                ("credential-broker-network", "brokerOut", "apim", "allow-credential-broker")):
            item = self.resource(module, resource, rule_name)
            result[group][rule_name] = {
                key: self.resolve(module, item["properties"][key], parameters)
                for key in ("priority", "direction", "access")}
        return result

    def test_contract_has_only_current_rules_in_finite_unique_bands(self):
        self.assertEqual(set(self.contract), {"version", "groups"})
        self.assertEqual(self.contract["version"], 1)
        self.assertEqual(set(self.contract["groups"]), set(GROUPS))
        business = {
            ("agw", "allow-client-web"), ("agw", "allow-client-grpc"),
            ("apim", "allow-agw-api"), ("apim", "allow-agw-grpc"),
            ("apim", "allow-private-backends"), ("apim", "allow-credential-broker"),
            ("pep", "allow-apim-foundry")}
        count = 0
        terminal = 0
        for group, value in self.contract["groups"].items():
            self.assertEqual(set(value), {"suffix", "rules"})
            identities = set()
            for name, rule in value["rules"].items():
                count += 1
                self.assertEqual(set(rule), {"direction", "priority"})
                self.assertIn(rule["direction"], ("Inbound", "Outbound"))
                identity = rule["direction"], rule["priority"]
                self.assertNotIn(identity, identities)
                identities.add(identity)
                if name.startswith("deny-other-"):
                    terminal += 1
                    self.assertEqual(rule["priority"], 4096)
                else:
                    low, high = (120, 200) if (group, name) in business else (220, 300)
                    self.assertTrue(low <= rule["priority"] < high)
                    self.assertEqual((rule["priority"] - low) % 5, 0)
        self.assertEqual((count, terminal), (46, 6))

    def test_every_compiled_rule_matches_exact_contract_without_new_outbound_deny(self):
        rules = self.compiled_rules(self.parameters())
        for group, actual in rules.items():
            expected = self.contract["groups"][group]["rules"]
            self.assertEqual(set(actual), set(expected), group)
            for name, rule in actual.items():
                self.assertEqual({key: rule[key] for key in ("direction", "priority")}, expected[name])
            if group in ("agw", "pep"):
                self.assertNotIn("deny-other-outbound", actual)
        self.assertTrue(all(rule["direction"] == "Inbound" for rule in rules["agw"].values()))
        suffixes = self.resolve("network", "[variables('nsgResourceNames')]", self.parameters())
        self.assertEqual(suffixes, ["nsg-test-fixture-" + self.contract["groups"][name]["suffix"]
                                    for name in GROUPS])

    def test_app_gateway_uses_the_requested_exact_sequence(self):
        parameters = self.parameters()
        rules = self.compiled_rules(parameters)["agw"]
        for direction, expected in {
                "Inbound": {"allow-client-web": 120, "allow-client-grpc": 125,
                            "allow-firewall-public-ingress": 220, "allow-gateway-manager": 225,
                            "allow-load-balancer": 230, "deny-other-inbound": 4096},
                "Outbound": {}}.items():
            self.assertEqual({name: rule["priority"] for name, rule in rules.items()
                              if rule["direction"] == direction}, expected)
        self.assertEqual(rules["allow-firewall-public-ingress"], {
            "priority": 220, "direction": "Inbound", "access": "Allow", "protocol": "Tcp",
            "sourcePortRange": "*", "destinationPortRange": "443",
            "sourceAddressPrefix": parameters["firewallPublicIpAddress"] + "/32",
            "destinationAddressPrefix": parameters["subnetPrefixes"]["agw"],
        })
        for name, priority, port_properties in (
                ("allow-client-web", 120,
                 {"destinationPortRanges": ["80", "443"]}),
                ("allow-client-grpc", 125,
                 {"destinationPortRange": "8443"})):
            with self.subTest(rule=name):
                self.assertEqual(rules[name], {
                    "priority": priority, "direction": "Inbound",
                    "access": "Allow", "protocol": "Tcp",
                    "sourcePortRange": "*",
                    "sourceAddressPrefix": "0.0.0.0/0",
                    "destinationAddressPrefix":
                        parameters["subnetPrefixes"]["agw"],
                    **port_properties,
                })

    def test_direct_config_overrides_cannot_change_app_gateway_rules(self):
        parameters = self.parameters()
        expected = self.compiled_rules(parameters)["agw"]
        for override in (
                {"clientAddressPrefixes": []},
                {"clientAddressPrefixes":
                    ["203.0.113.10/32", "198.51.100.0/26"]},
                {"certificatePublicProbeEnabled": False},
                {"clientAddressPrefixes": ["203.0.113.10/32"],
                 "certificatePublicProbeEnabled": False}):
            direct = copy.deepcopy(parameters)
            direct["config"].update(override)
            with self.subTest(override=override):
                self.assertEqual(self.compiled_rules(direct)["agw"], expected)

    def test_gateway_egress_uses_defaults_without_rewriting_existing_parent_nsgs(self):
        parameters = self.parameters()
        names = self.resolve("network", "[variables('nsgResourceNames')]", parameters)
        parameters["existingNetworkSecurityGroupNames"] = names
        template = self.templates["network"]
        expression = DiscoveryExpression("[variables('newNsgResourceNames')]", template["variables"],
                                         functions=template.get("functions", []))
        self.assertEqual(expression.evaluate(parameters), [])
        resources = self.templates["network"]["resources"]
        self.assertEqual(resources["createdNsgs"]["properties"], {})
        self.assertEqual(resources["nsgTagUpdates"]["type"], "Microsoft.Resources/tags")
        for module in ("network", "network-bindings"):
            items = self.templates[module]["resources"]
            items = list(items.values()) if isinstance(items, dict) else items
            self.assertFalse(any(item["type"].endswith("/defaultSecurityRules") for item in items))
        rules = self.compiled_rules(parameters)
        self.assertEqual(sum(rule["direction"] == "Inbound" for rule in rules["agw"].values()), 6)
        self.assertFalse(any(rule["direction"] == "Outbound" for rule in rules["agw"].values()))
        for name, port in (("allow-agw-api", "80"), ("allow-agw-grpc", "443")):
            with self.subTest(receiver=name):
                self.assertEqual(rules["apim"][name]["access"], "Allow")
                self.assertEqual(rules["apim"][name]["sourceAddressPrefix"], parameters["subnetPrefixes"]["agw"])
                self.assertEqual(rules["apim"][name]["destinationAddressPrefix"], parameters["subnetPrefixes"]["apim"])
                self.assertEqual(rules["apim"][name]["destinationPortRange"], port)
        vault = rules["pep"]["allow-key-vault"]
        self.assertEqual(vault["sourceAddressPrefixes"], [parameters["subnetPrefixes"]["agw"], parameters["subnetPrefixes"]["apim"]])
        self.assertEqual(vault["destinationAddressPrefix"], parameters["keyVaultPrivateIps"][0])
        self.assertEqual((vault["access"], vault["destinationPortRange"]), ("Allow", "443"))

    def test_optional_apim_and_operator_settings_preserve_fixed_gateway_rules(self):
        parameters = self.parameters()
        parameters["config"].update(operatorAddressPrefixes=[], apimKeyVaultAccessEnabled=False)
        rules = self.compiled_rules(parameters)
        self.assertNotIn("allow-operator-management", rules["management"])
        terminal = rules["management"]["deny-other-inbound"]
        self.assertEqual((terminal["priority"], terminal["access"], terminal["protocol"]),
                         (4096, "Deny", "*"))
        self.assertEqual(terminal["destinationPortRange"], "*")
        self.assertEqual(terminal["sourceAddressPrefix"], "*")
        self.assertEqual(terminal["destinationAddressPrefix"], "*")
        self.assertEqual((rules["apim"]["allow-key-vault-pe"]["priority"],
                          rules["apim"]["allow-key-vault-pe"]["access"]), (220, "Deny"))
        ingress = rules["agw"]["allow-firewall-public-ingress"]
        self.assertEqual((ingress["priority"], ingress["access"]), (220, "Allow"))
        self.assertLess(rules["agw"]["allow-client-web"]["priority"], ingress["priority"])
        self.assertEqual(ingress["sourceAddressPrefix"], parameters["firewallPublicIpAddress"] + "/32")
        self.assertEqual(rules["pep"]["allow-key-vault"]["sourceAddressPrefix"],
                         parameters["subnetPrefixes"]["agw"])

    def test_enabled_operator_allows_only_the_selected_ranges_at_220(self):
        parameters = self.parameters()
        sources = ["192.0.2.0/26", "198.51.100.10/32"]
        parameters["config"]["operatorAddressPrefixes"] = sources
        operator = self.compiled_rules(parameters)["management"]["allow-operator-management"]
        self.assertEqual((operator["priority"], operator["access"], operator["protocol"]),
                         (220, "Allow", "Tcp"))
        self.assertEqual(operator["sourceAddressPrefixes"], sources)
        self.assertEqual(operator["destinationAddressPrefix"], parameters["managementPrivateIp"])
        self.assertEqual(operator["destinationPortRange"], "22")

    def test_custom_management_rule_is_not_renumbered_and_default_dns_needs_no_allow(self):
        parameters = self.parameters()
        custom = copy.deepcopy(ip_fixtures.custom_documents()[1]["network"]["managementAdditionalSecurityRules"][0])
        parameters["config"]["managementAdditionalSecurityRules"] = [custom]
        rules = self.compiled_rules(parameters)
        self.assertEqual(rules["management"][custom["name"]], custom["properties"])
        for group in GROUPS:
            self.assertNotIn("allow-dns-tcp", rules[group])
            self.assertNotIn("allow-dns-udp", rules[group])
        broker = self.resource("credential-broker-network", "brokerOut", "allow-credential-broker")
        self.assertFalse(self.resolve("credential-broker-network", broker["condition"],
                                      {"enableCredentialBroker": False}))


if __name__ == "__main__":
    unittest.main()
