"""実コンパイル後のXMLでoperationごとの宛先・認証の一体選択を確かめる。"""
import unittest
import xml.etree.ElementTree as ET

import test_websocket_publication as publication_support
from arm_helpers import PolicyExpression


class OperationBackendPublicationTests(unittest.TestCase):
    setUpClass = classmethod(publication_support.WebSocketPublicationTests.setUpClass.__func__)

    def fixture(self):
        return {"name": "speech", "displayName": "Speech", "serviceClass": "platform", "apiType": "http",
                "path": "models/speech/v1", "productId": "models", "subscriptionKeyHeader": "Ocp-Apim-Subscription-Key",
                "backendService": "external", "backendUrl": "https://tts.example.com",
                "backendAudience": "https://cognitiveservices.azure.com", "backendAuthentication": {"mode": "speechEntra", "resourceId": "tts-resource"},
                "backendHeaders": {"X-Upstream": "tts"}, "keySubscriptions": [],
                "authorization": {"mode": "subscriptionKey",
                                  "operationConstraints": {name: {"kind": "registeredOperation", "maxRequestBytes": 1024} for name in ("tts", "stt")}},
                "operations": [{"name": "tts", "displayName": "TTS", "method": "POST", "urlTemplate": "/synthesize", "templateParameters": []},
                               {"name": "stt", "displayName": "STT", "method": "POST", "urlTemplate": "/recognize", "templateParameters": [],
                                "backendOverride": {"backendService": "external", "backendUrl": "https://stt.example.com",
                                                    "backendAudience": "https://stt.example.com/audience", "backendAuthentication": {"mode": "managedIdentity"},
                                                    "backendHeaders": {"X-Upstream": "stt"}}}]}

    def evaluate(self, expression, api):
        return PolicyExpression(expression, self.template["variables"], functions=self.template["functions"]).evaluate(
            {"apiDefinitions": [api], "publicHostName": "gateway.example.com", "names": {"apim": "apim-fixture"}})

    def test_operation_backend_branch_does_not_execute_api_default_authentication_first(self):
        api = self.fixture()
        policy = ET.fromstring(self.evaluate("[variables('policyValues')]", api)[0])
        inbound = policy.find("inbound")
        self.assertEqual(inbound.findall("authentication-managed-identity"), [])
        self.assertEqual(inbound.findall("set-backend-service"), [])
        selection = inbound.find('./choose/otherwise/choose')
        branch = selection.find("when")
        self.assertEqual(branch.get("condition"), '@(context.Operation.Id == "stt")')
        backend_id = branch.find("set-backend-service").get("backend-id")
        self.assertRegex(backend_id, r"^op-[a-z0-9]{13}-backend$")
        self.assertEqual(self.evaluate(self.template["resources"]["operationBackends"]["name"], api), "apim-fixture/" + backend_id)
        self.assertEqual(branch.find("authentication-managed-identity").get("resource"), "https://stt.example.com/audience")
        self.assertEqual(branch.find('./set-header[@name="X-Upstream"]/value').text, "stt")
        self.assertIsNone(branch.find('./set-header[@name="Authorization"]'))
        default = selection.find("otherwise")
        self.assertEqual(default.find("set-backend-service").get("backend-id"), "speech-backend")
        self.assertIn("tts-resource", default.find('./set-header[@name="Authorization"]/value').text)
        self.assertEqual(default.find('./set-header[@name="X-Upstream"]/value').text, "tts")
        entries = self.evaluate("[variables('operationBackendEntries')]", api)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["backend"]["backendUrl"], "https://stt.example.com")
        for resource in ("apiPolicies", "websocketPolicies"):
            if resource == "apiPolicies":
                self.assertIn("operationBackends", self.template["resources"][resource]["dependsOn"])

    def test_api_without_override_keeps_single_backend_and_existing_auth(self):
        api = self.fixture()
        api["operations"][1].pop("backendOverride")
        policy = ET.fromstring(self.evaluate("[variables('policyValues')]", api)[0])
        self.assertEqual(len(policy.findall("./inbound/set-backend-service")), 1)
        self.assertIsNotNone(policy.find('./inbound/choose/otherwise/authentication-managed-identity'))
        self.assertEqual(self.evaluate("[variables('operationBackendEntries')]", api), [])

    def test_operation_backend_names_do_not_confuse_hyphenated_pairs_or_exceed_length(self):
        first = self.evaluate("[__bicep.operationBackendId('a-b', 'c')]", self.fixture())
        second = self.evaluate("[__bicep.operationBackendId('a', 'b-c')]", self.fixture())
        self.assertNotEqual(first, second)
        self.assertLess(len(first), 80)

    def test_dynamic_websocket_model_is_set_only_after_registry_authorization(self):
        api = self.fixture()
        api.update(apiType="websocket", backendUrl="wss://realtime.example.com", backendAuthentication={"mode": "managedIdentity"},
                   websocket={"allowedModels": ["standard", "mini"], "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}})
        api["operations"] = [{"name": "invoke", "displayName": "Invoke", "method": "GET", "urlTemplate": "/", "templateParameters": []}]
        api["authorization"]["operationConstraints"] = {"invoke": {"kind": "registeredOperation", "maxRequestBytes": 1024}}
        policy = ET.fromstring(self.evaluate("[variables('policyValues')]", api)[0])
        inbound = policy.find("inbound")
        model = inbound.find('./set-query-parameter[@name="model"]')
        self.assertEqual(model.find("value").text, '@((string)context.Variables["gatewayProtocolModel"])')
        self.assertEqual(inbound.find('./set-query-parameter[@name="api-version"]/value').text, "fixed")
        self.assertLess(list(inbound).index(inbound.find('./set-variable[@name="gatewayDecision"]')), list(inbound).index(model))
        self.assertEqual(inbound.findall("set-backend-service"), [])


if __name__ == "__main__":
    unittest.main()
