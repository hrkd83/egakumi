"""MCPを含む各プロトコルの購読キー認可を、実コンパイル結果で確認する。

生成PII MCP、Streamable HTTP、従来SSEで同じ購読キーの契約を使い、
認可・監査・接続先認証が最終XMLに正しく組み込まれることを調べる。
Azure / Entra には接続せず、この試験だけでクラウド受入成功とは扱わない。
"""
from arm_helpers import DiscoveryExpression
from bicep_helpers import compile_bicep
import base64
import asyncio
import copy
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from test_api_protocols import bound_external_coverage, configuration
from arm_helpers import PolicyExpression


CORE = Path(__file__).resolve().parents[1]
PUBLIC_HOST = "gateway.example.com"


class McpOAuthDiscoveryTests(unittest.TestCase):
    """公開契約から生成されたXMLとAPIのキー必須設定を突き合わせる。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        # Explicit external fixtures exercise native MCP alongside the standard PII facade.
        raw = bound_external_coverage()
        cls.config = configuration.normalize_configuration(raw, base_directory=CORE / "config")
        cls.definitions = cls.config["apiDefinitions"]

    @classmethod
    def compile(cls, relative):
        return compile_bicep(relative)

    def parameters(self, definitions=None):
        return {"apiDefinitions": self.definitions if definitions is None else definitions,
                "publishApis": True, "l7IngressSecretIdentifier": ""}

    def value(self, expression, definitions=None, index=0):
        return DiscoveryExpression(expression, self.template["variables"], functions=self.template["functions"]).evaluate(self.parameters(definitions), index)

    def variable(self, name, definitions=None):
        return self.value("[variables('" + name + "')]", definitions)

    def expanded(self, value, definitions=None, index=0):
        if isinstance(value, dict):
            return {key: self.expanded(item, definitions, index) for key, item in value.items()}
        if isinstance(value, list):
            return [self.expanded(item, definitions, index) for item in value]
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return self.value(value, definitions, index)
        return value

    def test_reader_evaluates_nested_objects_arrays_and_preserves_literal_types(self):
        """構造化した ARM 値でも、式以外の値や配列の階層を壊さない。"""
        reader = DiscoveryExpression("[parameters('label')]", {}, functions=self.template["functions"])
        value = {"entries": [{"name": "[parameters('label')]", "flags": [True, False, None, 7]},
                             ["literal", "[concat('prefix-', parameters('label'))]"]]}
        self.assertEqual(reader.resolve(value, {"label": "fixture"}),
                         {"entries": [{"name": "fixture", "flags": [True, False, None, 7]},
                                      ["literal", "prefix-fixture"]]})

    def test_generated_mcp_audit_object_is_evaluated_from_compiled_udf(self):
        """実 UDF の object 本文を評価し、最終 XML に同じ監査値が届く。"""
        function = next(group for group in self.template["functions"] if group["namespace"] == "__bicep")["members"]["auditSettings"]
        self.assertIsInstance(function["output"]["value"], dict)
        apis = [api for api in self.definitions if "mcp" in api]
        for api, policy in zip(apis, self.variable("mcpPolicyValues")):
            with self.subTest(api=api["name"]):
                node = ET.fromstring(policy).find('./inbound/set-variable[@name="gatewayAuditSettings"]')
                encoded = re.search(r'FromBase64String\("([A-Za-z0-9+/=]+)"\)', node.get("value")).group(1)
                self.assertEqual(json.loads(base64.b64decode(encoded)), {
                    "apiId": api["name"], "serviceClass": api["serviceClass"], "backendService": api["backendService"],
                    "backendAuthentication": api["backendAuthentication"]["mode"],
                    "operationId": api["mcp"]["operationNames"][0],
                    "operationConstraints": api["authorization"]["operationConstraints"],
                    "operationBackends": {},
                    "llmOperationIds": [], "tokenLimitOperationIds": [], "websocketModel": "", "targetAgent": "",
                    "measurementRole": "facade"})

    def test_key_mcp_does_not_publish_gateway_oauth_discovery(self):
        for name in ("oauthMetadataApi", "oauthMetadataOperations", "oauthMetadataPolicy"):
            self.assertNotIn(name, self.template["resources"])
        self.assertNotIn("oauthMetadataUrls", self.template["outputs"])
        self.assertNotIn("publicHostName", self.template["parameters"])

    def test_adding_mcp_does_not_create_a_global_token_or_oauth_requirement(self):
        extra = copy.deepcopy(next(api for api in self.definitions if api["apiType"] == "mcp"))
        extra["name"], extra["path"] = "new-business-tool", "tools/new-business-tool"
        policies = self.variable("policyValues", [extra])
        self.assertEqual(len(policies), 1)
        root = ET.fromstring(policies[0])
        self.assertEqual(root.findall('.//set-header[@name="WWW-Authenticate"]'), [])
        self.assertEqual(root.findall('.//validate-azure-ad-token'), [])
        self.assertIn('subscriptionKey', policies[0])

    def test_all_api_and_generated_mcp_entries_require_the_configured_key_header(self):
        for resource_name in ("apis", "mcpServers"):
            resource = self.template["resources"][resource_name]
            for index in range(len(self.definitions) if resource_name == "apis" else len(self.variable("mcpApis"))):
                properties = self.expanded(resource["properties"], index=index)
                self.assertTrue(properties["subscriptionRequired"])
                self.assertEqual(properties["subscriptionKeyParameterNames"]["header"], "api-key")
                self.assertNotIn("authenticationSettings", properties)

    def test_all_business_apis_use_the_common_subscription_key_authorization(self):
        for api, policy in zip(self.definitions, self.variable("policyValues")):
            with self.subTest(api=api["name"]):
                root = ET.fromstring(policy)
                validators = root.findall("./inbound/validate-azure-ad-token")
                self.assertEqual(validators, [])
                self.assertEqual(api["authorization"]["mode"], "subscriptionKey")

    def test_mcp_key_failures_do_not_advertise_a_global_bearer_login(self):
        policies = [*self.variable("policyValues"), *self.variable("mcpPolicyValues")]
        for policy in policies:
            root = ET.fromstring(policy)
            self.assertEqual(root.findall('.//set-header[@name="WWW-Authenticate"]'), [])
            self.assertNotIn('gatewayMcpChallenge', policy)
        for policy in self.variable("mcpPolicyValues"):
            self.assertNotIn('gatewayMcpOAuth', policy)
            self.assertIn('subscriptionKey', policy)
            root = ET.fromstring(policy)
            self.assertEqual(root.find('./inbound/set-header[@name="Authorization"]').get('exists-action'), 'delete')

    def test_final_policies_expand_and_preserve_protocol_body_boundaries(self):
        for api, policy in zip(self.definitions, self.variable("policyValues")):
            with self.subTest(api=api["name"]):
                root = ET.fromstring(policy)
                self.assertNotRegex(policy, r"__[A-Z][A-Z0-9_]*__")
                self.assertIsNotNone(root.find('./inbound/set-variable[@name="gatewayDecision"]'))
                if api["apiType"] == "grpc":
                    self.assertEqual(root.find("./backend/forward-request").get("http-version"), "2")
                if api["apiType"] in ("grpc", "websocket"):
                    self.assertEqual(root.findall('.//set-body'), [])
                    self.assertNotRegex(policy, r'(?:Request|Response)\s*\.\s*Body')
        for policy in self.variable("mcpPolicyValues"):
            ET.fromstring(policy)
            self.assertNotRegex(policy, r"__[A-Z][A-Z0-9_]*__")

    def test_foundation_template_contains_no_api_or_backend_registration(self):
        main = self.compile("infra/main.bicep")
        # main は通常 ARM 配列、API モジュールは symbolic-name 辞書で出力されます。
        # コンパイラーの別表現を資材の不具合とせず、実際の子 template で対象を選びます。
        resources = list(main["resources"].values()) if isinstance(main["resources"], dict) else main["resources"]
        def resource_types(value):
            if isinstance(value, dict):
                if "type" in value:
                    yield value["type"]
                for item in value.values():
                    yield from resource_types(item)
            elif isinstance(value, list):
                for item in value:
                    yield from resource_types(item)
        for resource_type in resource_types(main):
            self.assertFalse(str(resource_type).startswith("Microsoft.ApiManagement/service/apis"))
            self.assertNotEqual(resource_type, "Microsoft.ApiManagement/service/backends")
        azure_modules = [resource for resource in resources if 'tenantId' in resource.get('properties', {}).get('parameters', {})]
        self.assertEqual(len(azure_modules), 2)
        for resource in azure_modules:
            self.assertEqual(resource['properties']['parameters']['tenantId']['value'], "[parameters('configuration').azureTenantId]")


if __name__ == "__main__":
    unittest.main()
