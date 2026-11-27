"""MCPを含む各プロトコルの購読キー認可を、実コンパイル結果で確認する。

生成PII MCP、Streamable HTTP、従来SSEで同じ購読キーの契約を使い、
認可・監査・接続先認証が最終XMLに正しく組み込まれることを調べる。
Azure / Entra には接続せず、この試験だけでクラウド受入成功とは扱わない。
"""
from tests.support.arm_helpers import DiscoveryExpression
from tests.support.bicep_helpers import compile_bicep
import base64
import copy
import json
import unittest
import xml.etree.ElementTree as ET

from tests.gateway.publication.test_api_protocols import bound_external_coverage, configuration
from tests.support.arm_helpers import compiled_policy_value
from tests import REPOSITORY


CORE = REPOSITORY
PUBLIC_HOST = "gateway.example.com"


class McpOAuthDiscoveryTests(unittest.TestCase):
    """公開契約から生成されたXMLとAPIのキー必須設定を突き合わせる。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/api-gateway/api-publication.bicep")
        # Explicit external fixtures exercise native MCP alongside the standard PII facade.
        raw = bound_external_coverage()
        cls.config = configuration.normalize_configuration(raw, base_directory=CORE / "config")
        cls.definitions = cls.config["apiDefinitions"]

    @classmethod
    def compile(cls, relative):
        return compile_bicep(relative)

    def parameters(self, definitions=None):
        return {**{name: definition['defaultValue']
                   for name, definition in self.template['parameters'].items()
                   if 'defaultValue' in definition},
                "apiDefinitions": self.definitions if definitions is None else definitions,
                "publishApis": True, "l7IngressSecretIdentifier": ""}

    def value(self, expression, definitions=None, index=0):
        return DiscoveryExpression(expression, self.template["variables"], functions=self.template["functions"]).evaluate(self.parameters(definitions), index)

    def variable(self, name, definitions=None):
        if name == 'policyValues':
            parameters = self.parameters(definitions)
            return [compiled_policy_value(self.template, parameters, index)
                    for index in range(len(parameters['apiDefinitions']))]
        if name == 'mcpPolicyValues':
            parameters = self.parameters(definitions)
            return [compiled_policy_value(self.template, parameters, index, resource_name='mcpPolicies')
                    for index in range(len(self.variable('mcpApis', definitions)))]
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

    def test_generated_mcp_carries_audit_and_rest_operation_settings(self):
        """生成MCPの最終XMLは入口の監査区分と、対応するREST操作の設定を持つ。"""
        apis = [api for api in self.definitions if "mcp" in api]
        for api, policy in zip(apis, self.variable("mcpPolicyValues")):
            with self.subTest(api=api["name"]):
                root = ET.fromstring(policy)
                node = root.find('./inbound/set-variable[@name="gatewayApiSettingsSource"]')
                self.assertIsNotNone(node)
                lines = base64.b64decode(node.get("value"), validate=True).decode("utf-8").split("\n")
                self.assertEqual(len(lines), 3)
                self.assertEqual(lines[0], "mcp")
                self.assertEqual(json.loads(lines[1]), {
                    "serviceClass": api["serviceClass"], "backendService": api["backendService"],
                    "backendAuthentication": api["backendAuthentication"]["mode"],
                    "operationBackends": {},
                    "llmOperationIds": [], "websocketModel": "", "targetAgent": "",
                    "measurementRole": "facade"})
                self.assertEqual(json.loads(lines[2]), {
                    "operationId": api["mcp"]["operationNames"][0],
                    "operationConstraints": api["authorization"]["operationConstraints"],
                    "tokenLimitOperationIds": [], "chatStoreDisabledOperationIds": [], "speechSsmlOperationIds": [],
                    "queryObservations": {}, "inputContracts": {},
                    "mcpQueryObservations": {method+" /mcp": api["mcp"]["queryObservation"] for method in ("POST","GET","DELETE")} if "queryObservation" in api["mcp"] else {}})
                inbound = list(root.find("inbound"))
                calls = root.findall('./inbound/include-fragment[@fragment-id="core-api-settings"]')
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].attrib, {"id": "core-api-settings", "fragment-id": "core-api-settings"})
                self.assertEqual(inbound.index(calls[0]), inbound.index(node) + 1)
                self.assertLess(inbound.index(calls[0]), inbound.index(root.find("inbound/base")))

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
        external = [policy for api, policy in zip(self.definitions, self.variable("policyValues"), strict=True)
                    if api["apiType"] == "mcp"]
        generated = self.variable("mcpPolicyValues")
        self.assertTrue(external)
        self.assertTrue(generated)
        for policy in [*external, *generated]:
            root = ET.fromstring(policy)
            self.assertFalse(any(header.get("name", "").casefold() == "www-authenticate"
                                 for header in root.iter("set-header")))
        for policy in generated:
            self.assertIn('subscriptionKey', policy)
            root = ET.fromstring(policy)
            key_branch = next(branch for branch in root.findall('./inbound/choose/when')
                              if branch.find('./set-header[@name="Authorization"]') is not None)
            self.assertIn('"method"] == "subscriptionKey"', key_branch.get('condition'))
            self.assertEqual(key_branch.find('./set-header[@name="Authorization"]').get('exists-action'), 'delete')

    def test_final_policies_expand_and_preserve_protocol_body_boundaries(self):
        for api, policy in zip(self.definitions, self.variable("policyValues")):
            with self.subTest(api=api["name"]):
                root = ET.fromstring(policy)
                self.assertNotRegex(policy, r"__[A-Z][A-Z0-9_]*__")
                if api["apiType"] == "http":
                    self.assertIsNotNone(root.find('./inbound/include-fragment[@fragment-id="core-api-authentication-result"]'))
                else:
                    self.assertIsNotNone(root.find('./inbound/set-variable[@name="gatewayDecision"]'))
                self.assertNotEqual(api["apiType"], "grpc")
                if api["apiType"] == "websocket":
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
        for template in (main, self.compile("infra/modules/ingress/public-ingress.bicep")):
            for resource_type in resource_types(template):
                self.assertFalse(str(resource_type).startswith("Microsoft.ApiManagement/service/apis"))
                self.assertNotEqual(resource_type, "Microsoft.ApiManagement/service/backends")
        for suffix in ('services', 'core-entra-settings', 'credential-sql', 'certificate-storage'):
            module, = [resource for resource in resources
                       if f"-{suffix}'" in resource.get('name', '')
                       and 'tenantId' in resource.get('properties', {}).get('parameters', {})]
            expected = ("[coalesce(tryGet(parameters('configuration'), 'entraTenantId'), parameters('configuration').azureTenantId)]"
                        if suffix == 'core-entra-settings' else "[parameters('configuration').azureTenantId]")
            self.assertEqual(module['properties']['parameters']['tenantId']['value'], expected)


if __name__ == "__main__":
    unittest.main()
