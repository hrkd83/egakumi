"""公開APIのURLを、承認済みの下流URLへ書き換える条件を検証する。

Bicepをローカルでコンパイルし、公開要求の任意queryや資格情報を下流URLへ混入させない構造を調べる。実AzureのAPIは呼ばない。
"""

from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class BackendRewriteDeploymentTests(unittest.TestCase):
    """公開APIのURLを、承認済みの下流URLへ書き換える条件を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')

    def test_compiled_operation_policy_keeps_auth_and_fixed_query_contract(self):
        resources = self.template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        # WebSocket onHandshake も同じ ARM resource type を使うため、REST 操作の
        # collection を名前で選ぶ。追加された WS を REST と誤認しない。
        policies = [resource for resource in resources
                    if resource["type"] == "Microsoft.ApiManagement/service/apis/operations/policies"
                    and resource.get("copy", {}).get("name") == "operationPolicies"]
        self.assertEqual(len(policies), 1)
        expression = policies[0]["properties"]["value"]
        self.assertRegex(expression, r'<policies>(?:<!--[^>]*-->)*<inbound>')
        self.assertLess(expression.index('__bicep.requiresSpeechSsmlGuard('), expression.index("'<base />'"))
        self.assertIn('copy-unmatched-params="false"', expression)
        self.assertIn('<backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error>', expression)

    def test_compiled_operation_policy_escapes_attributes_and_preserves_special_branches(self):
        resources = self.template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        operation_policy = next(resource for resource in resources
            if resource["type"] == "Microsoft.ApiManagement/service/apis/operations/policies"
            and resource.get("copy", {}).get("name") == "operationPolicies")
        expression = operation_policy["properties"]["value"]
        for kind in ("pii", "realtimeCreate", "realtimeHangup"):
            self.assertIn(".constraint.kind, '" + kind + "'", expression)
        self.assertIn("__bicep.xmlText(variables('operationEntries')[copyIndex()].operation.backendUrlTemplate)", expression)
        functions = next(item for item in self.template["functions"] if item["namespace"] == "__bicep")
        escaping = functions["members"]["xmlText"]["output"]["value"]
        for pair in ("'&', '&amp;'", "'\"', '&quot;'", "'<', '&lt;'", "'>', '&gt;'"):
            self.assertIn(pair, escaping)


if __name__ == "__main__":
    unittest.main()
