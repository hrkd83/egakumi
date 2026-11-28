"""FW の診断設定をコンパイルし、SKU・保存先・構築順をローカルで検証する。"""

from tests.support.bicep_helpers import compile_bicep

import json
import unittest

from tests.support.configuration_fixtures import fixture
from deployment.helpers import diagnostics
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration
from tests.network.test_parameter_ip_ownership import NetworkArmExpression
from tests import REPOSITORY


CORE = REPOSITORY


class FirewallDiagnosticsDeploymentTests(unittest.TestCase):
    """ログ欠落と、Standard への IDPS 設定の混入を防ぐ。Azure へは接続しない。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'diagnostics': compile_bicep('infra/modules/monitoring/firewall-diagnostics.bicep'),
            'network': compile_bicep('infra/modules/network/network.bicep'),
            'main': compile_bicep('infra/main.bicep'),
        }

    def test_normal_logs_are_resource_specific_and_idps_is_premium_only(self):
        """分類正本を一元化し、実FW診断は検証済みpropertiesをそのまま使用する。"""
        config = build_bicep_configuration(normalize_configuration(fixture(), base_directory=CORE / "infra/config"))
        config["network"]["firewall"]["sku"] = "Premium"
        definitions = diagnostics.canonical_settings(config, "core")
        definition = definitions["fw-logs"]["properties"]
        self.assertEqual([entry["category"] for entry in definition["logs"] if entry["enabled"]],
                         ["AZFWNetworkRule", "AZFWIdpsSignature"])
        url_logs = definitions["fw-url-logs"]["properties"]
        self.assertEqual([entry["category"] for entry in url_logs["logs"]],
                         ["AZFWApplicationRule", "AZFWThreatIntel"])
        self.assertEqual(url_logs["logAnalyticsDestinationType"], "Dedicated")
        self.assertNotIn("storageAccountId", url_logs)
        dns_logs = definitions["fw-dns-logs"]["properties"]
        self.assertEqual([entry["category"] for entry in dns_logs["logs"]],
                         ["AZFWDnsQuery", "AZFWFqdnResolveFailure"])
        self.assertEqual(dns_logs["workspaceId"], definition["workspaceId"])
        self.assertNotIn("storageAccountId", dns_logs)
        config["network"]["firewall"]["sku"] = "Standard"
        standard = diagnostics.canonical_settings(config, "core")["fw-logs"]["properties"]
        self.assertEqual([entry["category"] for entry in standard["logs"] if entry["enabled"]], ["AZFWNetworkRule"])
        template = self.templates["diagnostics"]
        self.assertEqual({item["type"] for item in template["resources"]}, {"Microsoft.Insights/diagnosticSettings"})
        logs, = [item for item in template["resources"] if item["properties"] == "[parameters('diagnosticSettingProperties')['fw-logs']]"]
        self.assertEqual(logs["properties"], "[parameters('diagnosticSettingProperties')['fw-logs']]")
        self.assertEqual(logs["scope"], "[resourceId('Microsoft.Network/azureFirewalls', parameters('firewallName'))]")
        url_resource, = [item for item in template["resources"] if "-fw-url-logs" in item["name"]]
        self.assertEqual(url_resource["properties"], "[parameters('diagnosticSettingProperties')['fw-url-logs']]")
        self.assertEqual(url_resource["scope"], logs["scope"])
        self.assertTrue(url_resource["dependsOn"])
        dns_resource, = [item for item in template["resources"] if "-fw-dns-logs" in item["name"]]
        self.assertEqual(dns_resource["properties"], "[parameters('diagnosticSettingProperties')['fw-dns-logs']]")
        self.assertEqual(dns_resource["scope"], logs["scope"])
        self.assertNotIn("FlowTrace", json.dumps(definition))
        self.assertNotIn("FatFlow", json.dumps(definition))

    def test_audit_storage_is_canonical_only_for_logs_and_existing_metrics_sink_can_be_retained(self):
        """既定メトリクスはLAWのみで、所有確認済みの既存追加sinkはmergeが保持する。"""
        config = build_bicep_configuration(normalize_configuration(fixture(), base_directory=CORE / "infra/config"))
        definitions = diagnostics.canonical_settings(config, "core")
        logs, metrics = definitions["fw-logs"]["properties"], definitions["fw-metrics"]["properties"]
        self.assertIn("storageAccountId", logs)
        self.assertNotIn("storageAccountId", metrics)
        before = {**metrics, "storageAccountId": logs["storageAccountId"]}
        self.assertEqual(diagnostics.merge_properties(before, metrics), before)
        metric_resource, = [item for item in self.templates["diagnostics"]["resources"] if "-metrics" in item["name"]]
        self.assertEqual(metric_resource["properties"], "[parameters('diagnosticSettingProperties')['fw-metrics']]")

    def test_main_connects_actual_firewall_and_monitoring_before_diagnostic_creation(self):
        """宛先IDが内部入力でも、実FWと監視保存先の作成順を保持する。"""
        resources = self.templates["main"]["resources"]
        resources = resources.values() if isinstance(resources, dict) else resources
        module, = [item for item in resources if "-firewall-diagnostics" in item.get("name", "")]
        values = module["properties"]["parameters"]
        self.assertIn(".outputs.firewallId.value", values["firewallName"]["value"])
        self.assertEqual(values["diagnosticSettingProperties"]["value"], "[parameters('diagnosticSettingProperties')]")
        self.assertEqual(module["condition"], "[variables('deployCore')]")
        dependencies = json.dumps(module["dependsOn"])
        self.assertIn("-network", dependencies)
        self.assertIn("-monitoring", dependencies)
        bindings, = [item for item in resources if "-portal-network-bindings" in item.get("name", "")]
        self.assertIn("-firewall-diagnostics", json.dumps(bindings["dependsOn"]))

    def test_apim_azure_monitor_event_hubs_dependency_is_allowed_through_nsg_and_firewall(self):
        """Monitor 本体と別の Event Hubs 転送先も、APIM 発の必要 TCP だけを許可する。"""
        network = self.templates["network"]
        configuration = build_bicep_configuration(normalize_configuration(fixture(), base_directory=CORE / "infra/config"))
        parameters = {"config": configuration["network"], "resourcePrefix": configuration["resourceStem"]}
        resolver = NetworkArmExpression("[createObject()]", network)
        groups = resolver.resolve(network["variables"]["nsgDefinitions"], parameters)
        apim, = [group for group in groups if group["key"] == "apim"]
        event_hubs, = [rule["properties"] for rule in apim["rules"] if rule["name"] == "allow-event-hubs-egress"]
        self.assertEqual(event_hubs["sourceAddressPrefix"], configuration["network"]["subnetPrefixes"]["apim"])
        self.assertEqual(event_hubs["destinationAddressPrefix"], "EventHub")
        self.assertEqual(event_hubs["destinationPortRanges"], ["443", "5671", "5672"])
        self.assertEqual((event_hubs["access"], event_hubs["direction"], event_hubs["protocol"]),
                         ("Allow", "Outbound", "Tcp"))
        dependency, = [item for item in network["variables"]["apimPlatformDependencies"]
                       if item["name"] == "monitor-event-hubs"]
        self.assertEqual(dependency, {"name": "monitor-event-hubs", "destinations": ["EventHub"],
                                      "ports": ["443", "5671", "5672"]})
        # 変数の追加だけで終わらず、FW の実ルールへ同じ宛先・port が配線されることを確認する。
        collections = network["resources"]["platformRules"]["properties"]["ruleCollections"]
        rules = next(item["rules"] for item in collections if item["name"] == "allow-apim-platform")
        self.assertIn("map(variables('apimPlatformDependencies')", rules)
        self.assertIn("'sourceAddresses', createArray(variables('subnetPrefixes').apim)", rules)
        self.assertIn("'destinationAddresses', lambdaVariables('dependency').destinations", rules)
        self.assertIn("'destinationPorts', lambdaVariables('dependency').ports", rules)
        self.assertIn("'ipProtocols', createArray('TCP')", rules)
        # 既存 NSG 本体を空で更新せず、従来どおり子規則だけで追加する。
        self.assertIn("existingNetworkSecurityGroupNames", network["variables"]["newNsgResourceNames"])
        self.assertEqual(network["resources"]["staticSecurityRules"]["properties"],
                         "[variables('staticNsgRules')[copyIndex()].rule.properties]")

    def test_apim_keeps_ntp_while_management_host_phc_needs_no_network_time_allow(self):
        """APIMのUDP123境界を保持し、管理VMにはNTP/PTPのネットワーク許可を作らない。"""
        configuration = build_bicep_configuration(normalize_configuration(fixture(), base_directory=CORE / "infra/config"))
        template = self.templates["network"]
        parameters = {"config": configuration["network"], "resourcePrefix": configuration["resourceStem"]}
        expression = NetworkArmExpression("[createObject()]", template)
        collections = template["resources"]["platformRules"]["properties"]["ruleCollections"]
        collection = next(item for item in collections if item["name"] == "allow-apim-platform")
        vm_collection = next(item for item in collections if item["name"] == "allow-vm-platform")
        self.assertEqual(collection["priority"], 200)
        self.assertEqual(vm_collection["priority"], 210)
        rules = expression.resolve(collection["rules"], parameters)
        vm_rules = expression.resolve(vm_collection["rules"], parameters)
        time_rule, = [rule for rule in rules if rule["name"] == "allow-apim-ntp"]
        self.assertEqual(time_rule, {
            "name": "allow-apim-ntp", "ruleType": "NetworkRule",
            "sourceAddresses": [configuration["network"]["subnetPrefixes"]["apim"]],
            "destinationAddresses": ["*"], "destinationPorts": ["123"], "ipProtocols": ["UDP"],
        })
        self.assertFalse(any(rule["name"] == "allow-apim-ntp" for rule in vm_rules))
        self.assertFalse(any(rule["name"] == "allow-mgmt-vm-ntp" for rule in rules))
        self.assertFalse(any(rule["name"] == "apim-platform-time" for rule in rules + vm_rules))
        self.assertFalse(any(rule["name"] == "management-time" for rule in rules + vm_rules))
        self.assertFalse(any(rule["name"] == "allow-mgmt-vm-ntp" for rule in vm_rules))
        self.assertTrue(all(rule["ipProtocols"] == ["TCP"] for rule in vm_rules))
        nsgs = expression.resolve(template["variables"]["nsgDefinitions"], parameters)
        apim = next(group for group in nsgs if group["key"] == "apim")
        nsg_time, = [rule for rule in apim["rules"] if rule["name"] == "allow-time-sync-egress"]
        properties = nsg_time["properties"]
        self.assertEqual(properties["sourceAddressPrefix"], configuration["network"]["subnetPrefixes"]["apim"])
        self.assertEqual(properties["destinationAddressPrefix"], "Internet")
        self.assertEqual(properties["destinationPortRange"], "123")
        self.assertEqual(properties["protocol"], "Udp")
        management = next(group for group in nsgs if group["key"] == "management")
        self.assertFalse(any(rule["name"] == "allow-time-sync-egress" for rule in management["rules"]))
        self.assertTrue(all(rule["properties"]["protocol"] == "Tcp" for rule in management["rules"]))

    def test_nsg_declarations_are_disjoint_for_fresh_existing_and_partial_environments(self):
        """実ARMのループを展開し、同じNSGの二重宣言と既存本体へのPUTを防ぐ。"""
        template = self.templates["network"]
        resources = template["resources"]
        configuration = build_bicep_configuration(normalize_configuration(fixture(), base_directory=CORE / "infra/config"))
        prefix = configuration["resourceStem"]
        suffixes = ("agw", "apim", "pep", "vm", "portal", "broker", "certificate")
        expected = {f"nsg-{prefix}-{suffix}" for suffix in suffixes}
        canonical = diagnostics.canonical_settings(configuration, "core")
        settings = {f"nsg-{suffix}": canonical[f"nsg-{suffix}"]["properties"] for suffix in suffixes}
        for setting in settings.values():
            self.assertEqual([entry["category"] for entry in setting["logs"]],
                             ["NetworkSecurityGroupEvent", "NetworkSecurityGroupRuleCounter"])
            self.assertIn("workspaceId", setting)
            self.assertIn("storageAccountId", setting)

        for existing in (set(), expected, {f"nsg-{prefix}-agw", f"nsg-{prefix}-vm"}):
            with self.subTest(existing=sorted(existing)):
                parameters = {"resourcePrefix": prefix, "config": configuration["network"],
                              "existingNetworkSecurityGroupNames": sorted(existing),
                              "diagnosticSettingProperties": settings}
                resolver = NetworkArmExpression("[createObject()]", template)

                def expand(resource, property_name="name"):
                    count = resolver.resolve(resource["copy"]["count"], parameters)
                    expression = NetworkArmExpression(resource[property_name], template, resolver.cache)
                    return [expression.evaluate(parameters, index) for index in range(count)]

                declarations = [resource for resource in resources.values()
                                if resource["type"] == "Microsoft.Network/networkSecurityGroups"]
                names = [name for resource in declarations for name in expand(resource)]
                self.assertEqual(len(names), len(set(names)), "ARM must not declare the same NSG ID twice")
                self.assertEqual(set(names), expected)
                self.assertEqual(set(expand(resources["createdNsgs"])), expected - existing)
                self.assertEqual(set(expand(resources["nsgs"])), existing)
                self.assertTrue(resources["nsgs"]["existing"])
                self.assertEqual(set(expand(resources["nsgTagUpdates"], "scope")),
                                 {"/mock/Microsoft.Network/networkSecurityGroups/" + name for name in existing})
                self.assertEqual(resources["nsgTagUpdates"]["properties"]["tags"], "[parameters('tags')]")
                # 子規則の更新は全NSGに共通の名前から行い、新規／既存の片方へ限定しない。
                self.assertIn("variables('nsgResourceNames')", resources["staticSecurityRules"]["name"])
                placements = []
                for symbol in ("createdNsgDiagnostics", "existingNsgDiagnostics"):
                    diagnostic = resources[symbol]
                    self.assertEqual(diagnostic["name"], "core-audit")
                    placements.extend(zip(expand(diagnostic, "scope"), expand(diagnostic, "properties"), strict=True))
                self.assertEqual(len(placements), len(expected))
                self.assertEqual(dict(placements), {
                    f"/mock/Microsoft.Network/networkSecurityGroups/nsg-{prefix}-{suffix}": settings[f"nsg-{suffix}"]
                    for suffix in suffixes
                })


if __name__ == "__main__":
    unittest.main()
