"""Compile every owned NSG rule against one public priority contract.

These checks use synthetic network inputs and do not contact Azure.
"""
import copy
import json
from pathlib import Path
import unittest

from bicep_helpers import compile_bicep
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
            ("agw", "allow-apim-http"), ("agw", "allow-apim-grpc"),
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
        self.assertEqual((count, terminal), (49, 6))

    def test_every_compiled_rule_matches_exact_contract_without_new_outbound_deny(self):
        rules = self.compiled_rules(self.parameters())
        for group, actual in rules.items():
            expected = self.contract["groups"][group]["rules"]
            self.assertEqual(set(actual), set(expected), group)
            for name, rule in actual.items():
                self.assertEqual({key: rule[key] for key in ("direction", "priority")}, expected[name])
            if group in ("agw", "pep"):
                self.assertNotIn("deny-other-outbound", actual)
        self.assertNotIn("allow-internet", rules["agw"])
        suffixes = self.resolve("network", "[variables('nsgResourceNames')]", self.parameters())
        self.assertEqual(suffixes, ["nsg-test-fixture-" + self.contract["groups"][name]["suffix"]
                                    for name in GROUPS])

    def test_app_gateway_uses_the_requested_exact_sequence(self):
        rules = self.compiled_rules(self.parameters())["agw"]
        for direction, expected in {
                "Inbound": {"allow-client-web": 120, "allow-client-grpc": 125,
                            "allow-certificate-observer": 220, "allow-gateway-manager": 225,
                            "allow-load-balancer": 230, "deny-other-inbound": 4096},
                "Outbound": {"allow-apim-http": 120, "allow-apim-grpc": 125,
                             "allow-key-vault": 220}}.items():
            self.assertEqual({name: rule["priority"] for name, rule in rules.items()
                              if rule["direction"] == direction}, expected)

    def test_disabled_operator_is_absent_and_other_optional_denies_keep_their_scope(self):
        parameters = self.parameters()
        parameters["config"].update(operatorAddressPrefixes=[], apimKeyVaultAccessEnabled=False,
                                     certificatePublicProbeEnabled=False)
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
        observer = rules["agw"]["allow-certificate-observer"]
        self.assertEqual((observer["priority"], observer["access"]), (220, "Deny"))
        self.assertLess(rules["agw"]["allow-client-web"]["priority"], observer["priority"])
        self.assertEqual(observer["sourceAddressPrefix"], parameters["firewallPublicIpAddress"] + "/32")
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
