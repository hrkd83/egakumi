"""FW の診断設定をコンパイルし、SKU・保存先・構築順をローカルで検証する。"""

from bicep_helpers import compile_bicep

import ast
import json
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class FirewallDiagnosticsDeploymentTests(unittest.TestCase):
    """ログ欠落と、Standard への IDPS 設定の混入を防ぐ。Azure へは接続しない。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'diagnostics': compile_bicep('infra/modules/firewall-diagnostics.bicep'),
            'network': compile_bicep('infra/modules/network.bicep'),
            'main': compile_bicep('infra/main.bicep'),
        }

    def test_normal_logs_are_resource_specific_and_idps_is_premium_only(self):
        """標準の3カテゴリにだけ、Premium 条件付き IDPS を追加する。"""
        template = self.templates["diagnostics"]
        self.assertEqual(template["parameters"]["firewallTier"]["allowedValues"], ["Standard", "Premium"])
        self.assertEqual({item["type"] for item in template["resources"]}, {"Microsoft.Insights/diagnosticSettings"})
        logs, = [item for item in template["resources"] if "-metrics" not in item["name"]]
        properties = logs["properties"]
        for category in ("AZFWNetworkRule", "AZFWApplicationRule", "AZFWThreatIntel"):
            self.assertIn("createObject('category', '" + category + "', 'enabled', true())", properties)
        self.assertIn("if(equals(parameters('firewallTier'), 'Premium'), createArray(createObject('category', 'AZFWIdpsSignature', 'enabled', true())), createArray())", properties)
        self.assertIn("'logAnalyticsDestinationType', 'Dedicated'", properties)
        self.assertNotIn("FlowTrace", properties)
        self.assertNotIn("FatFlow", properties)
        self.assertEqual(logs["scope"], "[resourceId('Microsoft.Network/azureFirewalls', parameters('firewallName'))]")

    def test_audit_storage_is_conditional_and_metrics_use_only_workspace(self):
        """監査対象ログだけを既存 Storage へ送り、メトリクスを長期保存に混ぜない。"""
        template = self.templates["diagnostics"]
        logs, = [item for item in template["resources"] if "-metrics" not in item["name"]]
        metrics, = [item for item in template["resources"] if "-metrics" in item["name"]]
        self.assertIn("if(parameters('enableAuditCollection'), createObject('storageAccountId', parameters('auditStorageId')), createObject())",
                      logs["properties"])
        self.assertEqual(metrics["properties"], {
            "workspaceId": "[parameters('workspaceId')]",
            "metrics": [{"category": "AllMetrics", "enabled": True}],
        })

    def test_main_connects_actual_firewall_and_existing_collect_japan_after_creation(self):
        """実 FW の出力と保護済み監査保存先が揃ってからログ収集を構成する。"""
        resources = self.templates["main"]["resources"]
        resources = resources.values() if isinstance(resources, dict) else resources
        module, = [item for item in resources if "-firewall-diagnostics" in item.get("name", "")]
        values = module["properties"]["parameters"]
        self.assertTrue(values["enableAuditCollection"]["value"])
        self.assertIn(".outputs.firewallId.value", values["firewallName"]["value"])
        self.assertIn(".outputs.storage.value.collectJapan.id", values["auditStorageId"]["value"])
        self.assertIn(".outputs.monitoring.value.workspaceId", values["workspaceId"]["value"])
        self.assertEqual(module["condition"], "[variables('deployCore')]")
        dependencies = json.dumps(module["dependsOn"])
        self.assertIn("-network", dependencies)
        self.assertIn("-monitoring", dependencies)

    def test_apim_azure_monitor_event_hubs_dependency_is_allowed_through_nsg_and_firewall(self):
        """Monitor 本体と別の Event Hubs 転送先も、APIM 発の必要 TCP だけを許可する。"""
        network = self.templates["network"]
        apim, = [group for group in network["variables"]["nsgDefinitions"] if group["key"] == "apim"]
        event_hubs, = [rule for rule in apim["rules"] if "'allow-monitor-event-hubs'" in rule]
        self.assertEqual(event_hubs,
                         "[__bicep.allowRule('allow-monitor-event-hubs', 235, 'Outbound', 'Tcp', "
                         "createArray(variables('subnetPrefixes').apim), createArray('EventHub'), "
                         "createArray('443', '5671', '5672'))]")
        dependency, = [item for item in network["variables"]["apimPlatformDependencies"]
                       if item["name"] == "monitor-event-hubs"]
        self.assertEqual(dependency, {"name": "monitor-event-hubs", "destinations": ["EventHub"],
                                      "ports": ["443", "5671", "5672"]})
        # 変数の追加だけで終わらず、FW の実ルールへ同じ宛先・port が配線されることを確認する。
        rules = network["resources"]["platformRules"]["properties"]["ruleCollections"]
        self.assertIn("map(variables('apimPlatformDependencies')", rules)
        self.assertIn("'sourceAddresses', createArray(variables('subnetPrefixes').apim)", rules)
        self.assertIn("'destinationAddresses', lambdaVariables('dependency').destinations", rules)
        self.assertIn("'destinationPorts', lambdaVariables('dependency').ports", rules)
        self.assertIn("'ipProtocols', createArray('TCP')", rules)
        # 既存 NSG 本体を空で更新せず、従来どおり子規則だけで追加する。
        self.assertIn("existingNetworkSecurityGroupNames", network["variables"]["newNsgResourceNames"])
        self.assertEqual(network["resources"]["staticSecurityRules"]["properties"],
                         "[variables('staticNsgRules')[copyIndex()].rule.properties]")

    def test_nsg_declarations_are_disjoint_for_fresh_existing_and_partial_environments(self):
        """実ARMのループを展開し、同じNSGの二重宣言と既存本体へのPUTを防ぐ。"""
        template = self.templates["network"]
        resources = template["resources"]
        expected = {"nsg-egakumi-test-" + suffix for suffix in ("agw", "apim", "pep", "vm")}

        def evaluate(expression, parameters, index=0):
            """NSG名の式に使うARM関数だけを評価する。Azureや任意コードは実行しない。"""
            if not isinstance(expression, str) or not expression.startswith("["):
                return expression
            source = expression[1:-1].replace("lambda(", "arm_lambda(").replace("not(", "arm_not(")

            def visit(node, bindings):
                if isinstance(node, ast.Constant):
                    return node.value
                if isinstance(node, ast.Attribute):
                    return visit(node.value, bindings)[node.attr]
                if isinstance(node, ast.Subscript):
                    return visit(node.value, bindings)[visit(node.slice, bindings)]
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    raise AssertionError("Unexpected ARM expression node")
                name = node.func.id
                if name == "arm_lambda":
                    key = visit(node.args[0], bindings)
                    return lambda value: visit(node.args[1], {**bindings, key: value})
                functions = {
                    "parameters": lambda key: parameters[key],
                    "variables": lambda key: evaluate(template["variables"][key], parameters, index),
                    "lambdaVariables": lambda key: bindings[key],
                    "copyIndex": lambda: index,
                    "format": lambda pattern, *values: pattern.format(*values),
                    "length": len, "contains": lambda values, value: value in values,
                    "arm_not": lambda value: not value,
                    "map": lambda values, transform: list(map(transform, values)),
                    "filter": lambda values, predicate: list(filter(predicate, values)),
                    "resourceId": lambda kind, resource_name: kind + "/" + resource_name,
                }
                return functions[name](*(visit(argument, bindings) for argument in node.args))

            return visit(ast.parse(source, mode="eval").body, {})

        for existing in (set(), expected, {"nsg-egakumi-test-agw", "nsg-egakumi-test-vm"}):
            with self.subTest(existing=sorted(existing)):
                parameters = {"resourcePrefix": "egakumi-test", "existingNetworkSecurityGroupNames": sorted(existing)}
                def expand(resource, property_name="name"):
                    count = evaluate(resource["copy"]["count"], parameters)
                    return [evaluate(resource[property_name], parameters, index) for index in range(count)]
                declarations = [resource for resource in resources.values()
                                if resource["type"] == "Microsoft.Network/networkSecurityGroups"]
                names = [name for resource in declarations for name in expand(resource)]
                self.assertEqual(len(names), len(set(names)), "ARM must not declare the same NSG ID twice")
                self.assertEqual(set(names), expected)
                self.assertEqual(set(expand(resources["createdNsgs"])), expected - existing)
                self.assertEqual(set(expand(resources["nsgs"])), existing)
                self.assertTrue(resources["nsgs"]["existing"])
                self.assertEqual(set(expand(resources["nsgTagUpdates"], "scope")),
                                 {"Microsoft.Network/networkSecurityGroups/" + name for name in existing})
                self.assertEqual(resources["nsgTagUpdates"]["properties"]["tags"], "[parameters('tags')]")
                # 子規則の更新は全NSGに共通の名前から行い、新規／既存の片方へ限定しない。
                self.assertIn("variables('nsgResourceNames')", resources["staticSecurityRules"]["name"])


if __name__ == "__main__":
    unittest.main()
