"""監査経路の変更通知が、4操作を限定して成功・失敗の両方を監視することを確認する。

実機で拒否されたサブスクリプション範囲の広い条件を再発させないため、
生成するARMのoperationName条件と、共通の終端状態・通知先の配線を検査する。
Azureへのデプロイや、実際の通知到着はこのローカル試験では確認しない。
"""
from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class MonitoringActivityAlertTests(unittest.TestCase):
    """Bicepの構文だけでなく、生成後の監視条件の契約も確認する。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/modules/monitoring-bindings.bicep')
        cls.variables = cls.template["variables"]
        cls.pipeline = next(item for item in cls.variables["copy"]
                            if item["name"] == "pipelineActivityDefinitions")

    def test_pipeline_has_exactly_four_distinct_write_delete_operations(self):
        operations = self.variables["pipelineOperations"]
        self.assertEqual(len(operations), 4)
        self.assertEqual(len({item["key"] for item in operations}), 4)
        self.assertEqual({item["operationName"] for item in operations}, {
            "Microsoft.Insights/diagnosticSettings/write",
            "Microsoft.Insights/diagnosticSettings/delete",
            "Microsoft.OperationalInsights/workspaces/dataexports/write",
            "Microsoft.OperationalInsights/workspaces/dataexports/delete",
        })
        self.assertEqual(self.pipeline["count"], "[length(variables('pipelineOperations'))]")

    def test_subscription_operation_filter_is_a_direct_allof_leaf(self):
        definition = self.pipeline["input"]
        self.assertEqual(definition["scopes"], ["[subscription().id]"])
        self.assertEqual(definition["conditions"],
                         "[concat(variables('terminalAdministrative'), "
                         "createArray(createObject('field', 'operationName', 'equals', "
                         "variables('pipelineOperations')[copyIndex('pipelineActivityDefinitions')].operationName)))]")
        # 監査経路の操作条件をanyOfへ戻したり、失敗だけに狭めたりしない。
        self.assertNotIn("anyOf", definition["conditions"])
        self.assertEqual(self.variables["terminalAdministrative"], [
            {"field": "category", "equals": "Administrative"},
            {"anyOf": [{"field": "status", "equals": "Succeeded"},
                       {"field": "status", "equals": "Failed"}]},
        ])

    def test_generated_rules_retain_global_enabled_action_group_wiring(self):
        alerts = [item for item in self.template["resources"]
                  if item["type"] == "Microsoft.Insights/activityLogAlerts"]
        self.assertEqual(len(alerts), 1)  # ARMのcopyで定義数だけ展開される。
        alert = alerts[0]
        self.assertEqual(alert["copy"]["count"], "[length(variables('activityDefinitions'))]")
        self.assertEqual(alert["location"], "global")
        self.assertIs(alert["properties"]["enabled"], True)
        self.assertEqual(alert["properties"]["condition"]["allOf"],
                         "[variables('activityDefinitions')[copyIndex()].conditions]")
        groups = alert["properties"]["actions"]["actionGroups"]
        self.assertEqual(len(groups), 1)
        self.assertIn("Microsoft.Insights/actionGroups", groups[0]["actionGroupId"])
        self.assertIn("variables('pipelineActivityDefinitions')", self.variables["activityDefinitions"])


if __name__ == "__main__":
    unittest.main()
