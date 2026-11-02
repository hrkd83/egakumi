"""WebSocket認可を自動生成onHandshakeへ置き、全体ガードを継承することを検査する。

APIスコープで拒否された実機不備を、Bicep生成結果とXMLの既知制約で防ぐ。
WebSocketの実接続やAzure側のpolicy検証は、別の実機確認で行う。
"""
from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from arm_helpers import ArmExpression
from test_api_protocols import bound_external_coverage, configuration


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"


class WebSocketPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')

    def test_private_http_hop_accepts_ws_while_native_grpc_remains_https(self):
        """AppGWのTLS終端後の経路を許可し、gRPCのTLS条件を変えない。"""
        expression = ArmExpression(self.template["resources"]["apis"]["properties"])
        for kind, expected in (("websocket", ["ws", "wss"]), ("grpc", ["https"])):
            api = {"displayName": "Fixture", "name": "fixture", "path": "fixture",
                   "apiType": kind, "authorization": {"mode": "entraOnly"},
                   "subscriptionKeyHeader": "Ocp-Apim-Subscription-Key",
                   "backendUrl": "wss://backend.example.com/voice", "grpc": {"definition": "fixture"}}
            actual = expression.evaluate({"apiDefinitions": [api]}, 0)
            self.assertEqual(actual["protocols"], expected)

    def test_compiled_protocol_policies_select_the_registered_backend(self):
        """WebSocketはWSSのserviceUrlを使い、HTTPは登録済みのbackendを選ぶ。"""
        config = configuration.normalize_configuration(bound_external_coverage(), base_directory=CORE / "config")
        definitions = config["apiDefinitions"]
        policy_array = PolicyExpression("[variables('policyValues')]", self.template["variables"], functions=self.template['functions'])
        parameters = {"apiDefinitions": definitions, "publicHostName": "gateway.example.com"}
        policies = policy_array.evaluate(parameters)
        api_properties = ArmExpression(self.template["resources"]["apis"]["properties"])
        kinds = set()
        for index, (api, value) in enumerate(zip(definitions, policies)):
            kinds.add(api["apiType"])
            root = ET.fromstring(value)
            selection = root.findall("./inbound/set-backend-service")
            with self.subTest(api=api["name"]):
                if api["apiType"] == "websocket":
                    self.assertEqual(selection, [])
                    properties = api_properties.evaluate({"apiDefinitions": definitions}, index)
                    self.assertEqual(properties["serviceUrl"], api["backendUrl"])
                    self.assertTrue(properties["serviceUrl"].startswith("wss://"))
                    self.assertIsNotNone(root.find("./inbound//authentication-managed-identity"))
                    self.assertIsNotNone(root.find('./inbound/set-header[@name="Authorization"][@exists-action="delete"]'))
                    self.assertIsNone(root.find('.//validate-azure-ad-token'))
                    inbound = root.find("./inbound")
                    self.assertIsNotNone(inbound.find("base"))
                    self.assertLess(list(inbound).index(inbound.find('set-variable[@name="gatewayAuditSettings"]')),
                                    list(inbound).index(inbound.find("base")))
                    self.assertIsNotNone(root.find('./inbound/set-variable[@name="gatewayDecision"]'))
                    self.assertIsNotNone(root.find('./inbound/set-variable[@name="gatewayProtocolQueryAllowed"]'))
                    for key, expected in api.get("websocket", {}).get("queryParameters", {}).items():
                        self.assertEqual(root.find('./inbound/set-query-parameter[@name="' + key + '"]/value').text, expected)
                    self.assertIsNotNone(root.find("./backend/forward-request"))
                    self.assertIsNotNone(root.find('./inbound/include-fragment[@fragment-id="core-gateway-telemetry"]'))
                elif any("backendOverride" in operation for operation in api["operations"]):
                    self.assertEqual(selection, [])
                    branches = root.findall('./inbound/choose/otherwise/choose/when/set-backend-service')
                    self.assertEqual(len(branches), sum("backendOverride" in operation for operation in api["operations"]))
                    self.assertIsNotNone(root.find('./inbound/choose/otherwise/choose/otherwise/set-backend-service'))
                else:
                    self.assertEqual(len(selection), 1)
                    self.assertEqual(selection[0].get("backend-id"), api["name"] + "-backend")
        self.assertEqual(kinds, {"http", "grpc", "mcp", "websocket"})

    def test_only_websocket_uses_onhandshake_without_replacing_global_policy(self):
        resources = self.template["resources"]
        api_policy = resources["apiPolicies"]
        handshake = resources["websocketPolicies"]
        self.assertEqual(api_policy["condition"], "[not(equals(parameters('apiDefinitions')[copyIndex()].apiType, 'websocket'))]")
        self.assertEqual(handshake["condition"], "[equals(parameters('apiDefinitions')[copyIndex()].apiType, 'websocket')]")
        self.assertEqual(handshake["type"], "Microsoft.ApiManagement/service/apis/operations/policies")
        self.assertIn("/onHandshake/policy", handshake["name"])
        self.assertEqual(handshake["properties"]["value"], api_policy["properties"]["value"])
        self.assertIn("apis", handshake["dependsOn"])
        self.assertNotIn("publicationPolicy", resources)
        # システム操作そのものを作り直さず、API作成後にpolicyだけ設定する。
        self.assertNotIn("onHandshake", resources["operations"]["name"])

    def test_handshake_and_its_fragments_have_no_known_unsupported_policies(self):
        # 公式WebSocket制約一覧。set-bodyは拒否応答の中に置いても対象になる。
        unsupported = {"mock-response", "cache-lookup", "cache-store", "cross-domain", "cors", "jsonp",
                       "set-method", "set-body", "xml-to-json", "json-to-xml", "xsl-transform",
                       "validate-content", "validate-parameters", "validate-headers", "validate-status-code"}
        for filename in ("protocol.xml", "service.xml", "gateway-telemetry.xml", "realtime-observer.xml", "rate-limit.xml", "rate-limit-error.xml"):
            root = ET.parse(SERVICE_POLICY if filename == "service.xml" else POLICIES / filename).getroot()
            with self.subTest(filename=filename):
                self.assertFalse({node.tag for node in root.iter()} & unsupported)
                self.assertFalse(any(node.get("name", "").lower() == "host"
                                     for node in root.iter("set-header")))
        protocol = ET.parse(POLICIES / "protocol.xml")
        self.assertIsNotNone(protocol.find("./inbound/base"))
        self.assertIsNone(protocol.find('.//validate-azure-ad-token'))
        self.assertIsNone(ET.parse(SERVICE_POLICY).find('.//validate-azure-ad-token'))
        self.assertIsNotNone(protocol.find('./inbound/set-variable[@name="gatewayDecision"]'))
        self.assertIsNotNone(protocol.find('./inbound/set-variable[@name="gatewayProtocolQueryAllowed"]'))
        fragments = {node.get("fragment-id") for node in protocol.iter("include-fragment")}
        self.assertEqual(fragments, {"core-gateway-telemetry", "core-rate-limit", "core-audit-context", "core-backend-audit"})
        observer = ET.parse(POLICIES / "realtime-observer.xml")
        self.assertIsNotNone(observer.find('./set-variable[@name="realtimeObserverOwnerValid"]'))
        self.assertEqual(observer.find('./choose/when/return-response/set-status').get("code"), "403")
        service = ET.parse(SERVICE_POLICY)
        self.assertEqual(len(service.findall("./inbound/choose[@id='core-common-authentication']")), 1)

    def test_global_policy_preserves_ingress_proof_without_an_api_inventory(self):
        """全体のAPI ID制限を設けず、入口証明・転送・相関IDを保持する。"""
        service = ET.parse(SERVICE_POLICY).getroot()
        self.assertEqual([section.tag for section in service], ["inbound", "backend", "outbound", "on-error"])
        self.assertEqual(service.findall(".//base"), [])
        reasons = [status.get("reason") for status in service.findall("./inbound/choose/when/return-response/set-status")]
        self.assertEqual(reasons, ["Trusted ingress required"])
        self.assertTrue(all(status.get("code") == "403" for status in service.findall("./inbound/choose/when/return-response/set-status")))
        self.assertEqual([node.tag for node in service.find("backend")], ["forward-request"])
        # API登録側は全体policyを保持し、本文を記録しない診断設定を作る。
        resources = self.template["resources"]
        self.assertNotIn("publicationPolicy", resources)
        for stage in ("frontend", "backend"):
            for direction in ("request", "response"):
                pipeline=resources["globalDiagnostic"]["properties"][stage][direction]
                if isinstance(pipeline,str): pipeline=self.template["variables"]["diagnosticRequest"]
                self.assertEqual(pipeline["body"]["bytes"],0)


if __name__ == "__main__":
    unittest.main()
