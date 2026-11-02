"""APIMの初回API公開に必要なloggerと、Product更新の直列化を検査する。

新規APIMにはAzure Monitor loggerが存在するとは限らない。実際に送信する
ARMでloggerの作成と診断の依存を確認し、同じProductへのAPI追加を競合させない。
本文・資格情報を収集しない診断設定も維持する。Azureへの接続は行わない。
"""
from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class ApiPublicationPrerequisiteTests(unittest.TestCase):
    """コンパイル後の作成順を調べ、行順だけに依存する定義を検出する。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        # Lambdaを使うこのモジュールはsymbolic name付きARMを生成する。
        # 依存先を同じ資源名で比較することで、別APIMを文字列で参照する誤りも見つける。
        cls.resources = cls.template["resources"]

    def test_fresh_apim_creates_exactly_one_monitor_logger_without_credentials(self):
        loggers = {key: item for key, item in self.resources.items()
                   if item["type"] == "Microsoft.ApiManagement/service/loggers"}
        self.assertEqual(set(loggers), {"azureMonitorLogger"})
        logger = loggers["azureMonitorLogger"]
        self.assertNotIn("existing", logger)
        self.assertNotIn("condition", logger)
        self.assertEqual(logger["name"], "[format('{0}/{1}', parameters('names').apim, 'azuremonitor')]")
        self.assertEqual(logger["properties"], {"loggerType": "azureMonitor", "isBuffered": True})

    def test_global_rest_and_native_mcp_diagnostics_wait_for_same_owned_logger(self):
        diagnostics = {key: item for key, item in self.resources.items()
                       if item["type"] in ("Microsoft.ApiManagement/service/diagnostics",
                                           "Microsoft.ApiManagement/service/apis/diagnostics")}
        self.assertEqual(set(diagnostics), {"globalDiagnostic", "apiDiagnostics", "mcpDiagnostics", "credentialLoginDiagnostics"})
        for name, diagnostic in diagnostics.items():
            with self.subTest(diagnostic=name):
                self.assertEqual(diagnostic["properties"]["loggerId"],
                                 "[resourceId('Microsoft.ApiManagement/service/loggers', parameters('names').apim, 'azuremonitor')]")
                # resourceId()の手書きだけならこの依存は生成されず、初回に競合・欠落する。
                self.assertIn("azureMonitorLogger", diagnostic["dependsOn"])
                props = diagnostic["properties"]
                self.assertFalse(props["logClientIp"])
                self.assertEqual(props["sampling"], {"samplingType": "fixed", "percentage": 100})
                for side in ("frontend", "backend"):
                    for direction in ("request", "response"):
                        pipeline = props[side][direction]
                        if pipeline == "[variables('diagnosticRequest')]":
                            pipeline = self.template["variables"]["diagnosticRequest"]
                        if pipeline == "[variables('diagnosticResponse')]":
                            pipeline = self.template["variables"]["diagnosticResponse"]
                        self.assertEqual(pipeline["body"], {"bytes": 0})
                        expected = ["x-ms-client-request-id", "x-appgw-trace-id"] if (side, direction) == ("frontend", "request") else []
                        self.assertEqual(pipeline["headers"], expected)

    def test_compiled_header_masking_uses_provider_supported_mask_mode(self):
        """共用enumではコンパイルできるHideも、APIMのheader登録では拒否される。"""
        # APIキーの名前は環境ごとのパラメーターのまま残し、CMの資格情報も保護する。
        # value/modeという実際のARMプロパティまで検査し、name等の誤ったキーも検出する。
        expected_header_masking = (
            "[map(createArray(parameters('subscriptionKeyHeader'), 'Authorization', 'Cookie', "
            "'X-Core-Broker-Key', 'X-Core-Connection-Session', 'X-Core-Connection-Poll', 'X-Core-Connection-Update', 'X-Core-Connection-Update-Id'), "
            "lambda('name', createObject('value', lambdaVariables('name'), 'mode', 'Mask')))]"
        )
        expected_query_masking = (
            "[map(createArray('launch', 'state', 'code', 'session_state', 'error_description', 'transactionId', 'api-key', 'subscription-key'), "
            "lambda('name', createObject('value', lambdaVariables('name'), 'mode', 'Hide')))]"
        )
        request = self.template["variables"]["diagnosticRequest"]
        self.assertEqual(request["dataMasking"], {
            "headers": expected_header_masking,
            "queryParams": expected_query_masking,
        })
        # 全体、通常API、生成MCP、CM補助APIのすべてに修正済みの設定が届くことを検査する。
        for resource_name in ("globalDiagnostic", "apiDiagnostics", "mcpDiagnostics", "credentialLoginDiagnostics"):
            with self.subTest(diagnostic=resource_name):
                self.assertEqual(
                    self.resources[resource_name]["properties"]["frontend"]["request"],
                    "[variables('diagnosticRequest')]",
                )
                self.assertEqual(self.resources[resource_name]["properties"]["frontend"]["response"], "[variables('diagnosticResponse')]")
        response = self.template['variables']['diagnosticResponse']
        self.assertEqual(response['headers'], [])
        self.assertEqual(response['body'], {'bytes': 0})
        self.assertEqual(response['dataMasking']['headers'],
            "[map(createArray('X-Core-Connection-Update', 'X-Core-Connection-Update-Id'), lambda('name', createObject('value', lambdaVariables('name'), 'mode', 'Mask')))]")

    def test_large_policy_writes_are_serial_across_api_handshake_and_operations(self):
        """実ARMで一斉XML書込みを防ぎ、公開ガードが最後まで残ることを調べる。"""
        for name in ("apiPolicies", "websocketPolicies", "operationPolicies", "mcpPolicies"):
            with self.subTest(policy=name):
                resource = self.resources[name]
                self.assertEqual(resource["copy"]["mode"], "serial")
                self.assertEqual(resource["copy"]["batchSize"], 1)
                self.assertIn("value", resource["properties"])
        # 個々のループだけでなく、HTTP等→WS握手→操作という順にする。
        self.assertIn("apiPolicies", self.resources["websocketPolicies"]["dependsOn"])
        self.assertIn("websocketPolicies", self.resources["operationPolicies"]["dependsOn"])
        self.assertIn("apiPolicies", self.resources["operations"]["dependsOn"])
        # APIごとの認証と、自動生成onHandshakeへの配置条件も維持する。
        self.assertEqual(self.resources["apiPolicies"]["condition"],
                         "[not(equals(parameters('apiDefinitions')[copyIndex()].apiType, 'websocket'))]")
        self.assertEqual(self.resources["websocketPolicies"]["condition"],
                         "[equals(parameters('apiDefinitions')[copyIndex()].apiType, 'websocket')]")

    def test_all_product_membership_loops_update_one_binding_at_a_time(self):
        links = {key: item for key, item in self.resources.items()
                 if item["type"] == "Microsoft.ApiManagement/service/products/apis"}
        self.assertEqual(set(links), {"protectedProductApiLinks", "mcpProductApiLinks", "credentialLoginProductLinks",
                                     "releasedProductApiLinks", "releasedMcpProductApiLinks"})
        for name, link in links.items():
            with self.subTest(binding=name):
                self.assertEqual(link["copy"]["mode"], "serial")
                self.assertEqual(link["copy"]["batchSize"], 1)
        # 通常APIと生成MCPが同じProductを使っても、別ループ間で並列追加しない。
        self.assertIn("protectedProductApiLinks", links["mcpProductApiLinks"]["dependsOn"])
        # API 登録は共有の全体 policy を上書きしない。
        self.assertFalse(any(resource["type"] == "Microsoft.ApiManagement/service/policies"
                             for resource in self.resources.values()))
        for name in ("globalDiagnostic", "apiDiagnostics", "mcpDiagnostics"):
            self.assertIn(name, self.resources)


if __name__ == "__main__":
    unittest.main()
