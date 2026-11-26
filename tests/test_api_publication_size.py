"""APIが増えた際のARM展開後サイズを、配備前に実生成物で確認する。

ARMはソースファイルだけでなく、変数・copyループの展開結果にもサイズ制限を
適用する。全APIのXMLを段階別配列へ保存する回帰を防ぐ。この見積りは、Azure
内部の圧縮済みジョブサイズ算定そのものを再実装するものではない。
"""
from bicep_helpers import compile_bicep

import base64
import copy
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

from test_api_protocols import bound_coverage, configuration
from arm_helpers import DiscoveryExpression
from deployment.helpers import credential_login

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "apps/gateway/deploy"))
from publication_templates import publication_parameters, split_publication_templates, source_generation_id
from api_publication import SOURCE_PRINCIPAL_EXPRESSION, validate_publication_template

SYNTHETIC_SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
SYNTHETIC_PRINCIPAL = "77777777-7777-4777-8777-777777777777"
SOURCE_OUTPUTS = {"entraPublicationSource", "credentialBrowserPublicationSource"}
REQUEST_BUDGET = 2_800_000
RESOURCE_BUDGET = 1_000_000
POLICY_BUDGET = 512 * 1024
REQUEST_TAGS = {"azd-env-name": "test-fixture", "azd-project-name": "egakumi"}


def serialized_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def source_without_identity_binding(template):
    """Only the existing APIM identity may be read for generated CM settings.

    Policy/routing expectations must remain assignment expressions; never adopt
    a runtime API, policy, backend or NamedValue as the expected source.
    """
    value = json.dumps(template["outputs"]["entraPublicationSource"])
    return value.replace("reference('apim', '2024-05-01', 'full').identity.principalId", "approved-apim-identity")


class SizeExpression(DiscoveryExpression):
    """実コンパイルされたARM式を、Azure接続せずに展開する。

    resourceId等の公開IDだけを合成する。未知の式は失敗させ、XMLの内容を
    別実装で置き換えてサイズを小さく見せない。
    """

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        if tree[0] == "call":
            _, name, arguments = tree
            if name == "subscription":
                return {"subscriptionId": SYNTHETIC_SUBSCRIPTION}
            if name == "reduce":
                collection = self.evaluate(parameters, index, arguments[0], bindings)
                accumulator = self.evaluate(parameters, index, arguments[1], bindings)
                _, lambda_name, definition = arguments[2]
                if lambda_name != "lambda" or len(definition) != 3:
                    raise AssertionError("Expected a two-parameter ARM reduce lambda")
                first, second = (self.evaluate(parameters, index, item, bindings) for item in definition[:2])
                for entry in collection:
                    accumulator = self.evaluate(parameters, index, definition[2], {**bindings, first: accumulator, second: entry})
                return accumulator
            if name in ("resourceId", "shallowMerge"):
                values = [self.evaluate(parameters, index, item, bindings)
                          for item in arguments]
                if name == "shallowMerge":
                    return {key: value for item in values[0]
                            for key, value in item.items()}
                kind, *names = values
                provider, *types = kind.split("/")
                suffix = "/".join(part for pair in zip(types, names) for part in pair)
                return (f"/subscriptions/{SYNTHETIC_SUBSCRIPTION}"
                        f"/resourceGroups/rg-test-fixture-svc/providers/{provider}/{suffix}")
        return super().evaluate(parameters, index, tree, bindings)


def expand(template, parameters):
    """変数とresourceのcopyを展開し、実XMLを含むサイズ測定用の構造を返す。"""
    parameters = {**{name: definition["defaultValue"] for name, definition in template.get("parameters", {}).items()
                     if "defaultValue" in definition}, **parameters}
    cache = {}
    reader = SizeExpression("[true()]", template["variables"], cache,
                            template.get("functions", []))
    names = [name for name in template["variables"] if name != "copy"]
    names.extend(entry["name"] for entry in template["variables"].get("copy", []))
    for name in names:
        tree = SizeExpression(f"[variables('{name}')]", template["variables"]).tree
        reader.evaluate(parameters, tree=tree)
    resources = []
    for resource in template["resources"].values():
        if resource.get("existing"):
            continue
        count = reader.resolve(resource["copy"]["count"], parameters) if "copy" in resource else 1
        for index in range(count):
            if "condition" in resource and not reader.resolve(resource["condition"], parameters, index):
                continue
            body = {key: value for key, value in resource.items()
                    if key not in ("copy", "condition")}
            resources.append(reader.resolve(body, parameters, index))
    return {"variables": cache, "resources": resources, "parameters": parameters,
            "functions": template.get("functions", [])}


def measure(template, parameters):
    """全outputs・metadata・既存tagsも含め、提出時と展開後の量を別々に測ります。

    Azure内部の圧縮済みjobや管理応答の完全再現ではありません。出力を省くことで
    source生成要求を過小評価しないため、既存expandとは別の測定を使います。
    """
    submitted = publication_parameters(template, {name: {"value": value} for name, value in parameters.items()})
    expanded = expand(template, {name: wrapper["value"] for name, wrapper in submitted.items()})
    reader = SizeExpression("[true()]", template["variables"], expanded["variables"],
                            template.get("functions", []))
    outputs = reader.resolve(template.get("outputs", {}), expanded["parameters"])
    expanded = {**{key: reader.resolve(value, expanded["parameters"])
                   for key, value in template.items()
                   if key not in ("variables", "resources", "parameters", "functions", "outputs")},
                **expanded, "outputs": outputs, "tags": REQUEST_TAGS}
    request = {"properties": {"mode": "Incremental", "template": template,
                              "parameters": submitted},
               "tags": REQUEST_TAGS}
    policies = []
    for resource in expanded["resources"]:
        if resource["type"].endswith(("/policies", "/policyFragments")):
            policies.append(resource["properties"]["value"])
    for name in SOURCE_OUTPUTS & outputs.keys():
        policies.extend(entry["value"] for entry in outputs[name]["value"].get("policies", []))
    return {"expanded": expanded, "submittedParameters": submitted, "compiledBytes": serialized_size(request),
            "expandedBytes": serialized_size(expanded), "outputBytes": serialized_size(outputs),
            "resourceBytes": {resource["type"] + "/" + resource["name"]: serialized_size(resource)
                              for resource in expanded["resources"]},
            "policyBytes": [len(value.encode("utf-8")) for value in policies]}


class SizeMeasurementTests(unittest.TestCase):
    def test_measure_includes_all_outputs_generation_binding_metadata_and_tags(self):
        template = {"variables": {}, "resources": {},
                    "parameters": {"defaultOnly": {"type": "string", "defaultValue": "literal-default"}},
                    "metadata": {"fixture": "retained"},
                    "outputs": {"additionalOutput": {"type": "string", "value": "payload" * 2000},
                                "publicationSourceGenerationId": {"type": "string", "value": "synthetic-source-id"},
                                "publicationSourcePrincipalId": {"type": "string", "value": SOURCE_PRINCIPAL_EXPRESSION}}}
        result = measure(template, {})
        expected = copy.deepcopy(template["outputs"])
        expected["publicationSourcePrincipalId"]["value"] = SYNTHETIC_PRINCIPAL
        self.assertEqual(result["expanded"]["outputs"], expected)
        self.assertEqual(result["expanded"]["metadata"], template["metadata"])
        self.assertEqual(result["expanded"]["tags"], REQUEST_TAGS)
        self.assertEqual(result["submittedParameters"], {"defaultOnly": {"value": "literal-default"}})
        self.assertGreater(result["outputBytes"], 14_000)
        without_outputs = {key: value for key, value in result["expanded"].items() if key != "outputs"}
        self.assertGreater(result["expandedBytes"] - serialized_size(without_outputs), 14_000)


class ApiPublicationSizeTests(unittest.TestCase):
    """公開例の全APIと追加利用者を展開し、サイズに余裕があることを調べる。"""

    @classmethod
    def setUpClass(cls):
        # 正式の公開例の全APIから、実際のXMLとARM展開後サイズを調べます。
        raw = bound_coverage()
        config = configuration.normalize_configuration(raw, base_directory=CORE / "config")
        cls.credential_login = credential_login.settings(config)
        definitions = config["apiDefinitions"]
        # 実ARMは親main.bicepから解決済みURLを受け取る。ここでは合成IDだけを使う。
        for api in definitions:
            api.setdefault("backendUrl", "https://aif-test-fixture.services.ai.azure.com"
                           + api.get("backendPath", ""))
            for operation in api["operations"]:
                if "backendOverride" in operation:
                    operation["backendOverride"].setdefault("backendUrl", "https://aif-test-fixture.services.ai.azure.com")
        cls.parameters = {
            "apiDefinitions": definitions,
            "rateLimits": config["gateway"]["rateLimits"],
            "names": {"apim": "apim-test-fixture"},
            "publishApis": True,
            "l7IngressSecretIdentifier": "https://kv-test-fixture.vault.azure.net/secrets/core-l7-ingress-secret",
            "realtimeTicketSecretIdentifier": "https://kv-test-fixture.vault.azure.net/secrets/core-realtime-ticket-key",
        }
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        cls.measurements = {}

    def split_measurements(self, parameters):
        """同じ完成Bicepを分割し、実配備と両source出力の同一性も確認します。"""
        key = json.dumps(parameters, sort_keys=True)
        if key in self.measurements:
            return self.measurements[key]
        publication, source = split_publication_templates(self.template)
        self.assertIsNotNone(source)
        self.assertEqual(publication["resources"], self.template["resources"])
        self.assertEqual(source["resources"], {"apim": self.template["resources"]["apim"]})
        self.assertTrue(source["resources"]["apim"]["existing"])
        self.assertEqual(set(source["outputs"]), SOURCE_OUTPUTS)
        self.assertEqual(publication.get("outputs", {}),
                         {name: definition for name, definition in self.template["outputs"].items()
                          if name not in SOURCE_OUTPUTS})
        for name in SOURCE_OUTPUTS:
            self.assertEqual(source["outputs"][name], self.template["outputs"][name])
        for template in (publication, source):
            self.assertEqual(template.get("metadata"), self.template.get("metadata"))
        resource_scope = (f"/subscriptions/{SYNTHETIC_SUBSCRIPTION}"
                          "/resourceGroups/rg-test-fixture-svc/providers/")
        plan = {"deploymentId": resource_scope + "Microsoft.Resources/deployments/core-api-publication",
                "apimId": resource_scope + "Microsoft.ApiManagement/service/apim-test-fixture",
                "parameters": publication_parameters(self.template, {name: {"value": value}
                                                                     for name, value in parameters.items()})}
        source_id = source_generation_id(plan, self.template, identity_principal_id=SYNTHETIC_PRINCIPAL)
        binding = {"type": "string", "value": source_id}
        for template in (publication, source):
            template.setdefault("outputs", {})["publicationSourceGenerationId"] = copy.deepcopy(binding)
        source["outputs"]["publicationSourcePrincipalId"] = {"type": "string", "value": SOURCE_PRINCIPAL_EXPRESSION}
        self.assertNotIn("publicationSourcePrincipalId", publication["outputs"])
        result = {"publication": measure(publication, parameters), "source": measure(source, parameters)}
        self.assertEqual(result["source"]["expanded"]["resources"], [])
        original_resources = expand(self.template, parameters)["resources"]
        self.assertEqual(result["publication"]["expanded"]["resources"], original_resources)
        reader = SizeExpression("[true()]", self.template["variables"], functions=self.template.get("functions", []))
        original_outputs = reader.resolve(self.template["outputs"], result["publication"]["expanded"]["parameters"])
        for name, expected in original_outputs.items():
            actual = result["source" if name in SOURCE_OUTPUTS else "publication"]["expanded"]["outputs"][name]
            self.assertEqual(actual, expected)
        self.measurements[key] = result
        return result

    def assert_split_budgets(self, parameters):
        result = self.split_measurements(parameters)
        for name, measured in result.items():
            with self.subTest(request=name):
                self.assertLess(measured["compiledBytes"], REQUEST_BUDGET,
                                name + " compiled request bytes: " + str(measured["compiledBytes"]))
                self.assertLess(measured["expandedBytes"], REQUEST_BUDGET,
                                name + " expanded request including all outputs bytes: " + str(measured["expandedBytes"]))
                self.assertLess(measured["outputBytes"], 4_000_000)
                self.assertTrue(all(size < RESOURCE_BUDGET for size in measured["resourceBytes"].values()),
                                name + " resource definition bytes: " + str(measured["resourceBytes"]))
                self.assertTrue(all(size < POLICY_BUDGET for size in measured["policyBytes"]),
                                name + " policy/fragment UTF-8 bytes: " + str(measured["policyBytes"]))
        return result

    def test_standard_apis_fit_expanded_budget_with_headroom(self):
        # 正式な配備入口でも、propertiesがARM式になった実コンパイル資材を受け入れます。
        self.assertIsNone(validate_publication_template(self.template))
        self.assertTrue(all(api["serviceClass"] == "platform" for api in self.parameters["apiDefinitions"]))
        self.assertFalse(any(api["name"].startswith("sample-") for api in self.parameters["apiDefinitions"]))
        expanded = self.assert_split_budgets(copy.deepcopy(self.parameters))["publication"]["expanded"]
        # 公式4MB境界に対して、全outputs・binding・metadata込みで280万B未満に保ちます。
        self.assertEqual(sum(resource["type"] == "Microsoft.ApiManagement/service/apis" for resource in expanded["resources"]), len(self.parameters["apiDefinitions"]) + sum("mcp" in api for api in self.parameters["apiDefinitions"]))

    def test_compiled_browser_exception_keeps_lowercase_csharp_boolean_with_azure_string_behavior(self):
        class AzureBooleanExpression(SizeExpression):
            """Azureで確認したstring(bool)だけを再現し、実UDFの本文はそのまま評価する。"""
            def evaluate(self, parameters, index=0, tree=None, bindings=None):
                tree = self.tree if tree is None else tree
                if tree[0] == "call" and tree[1] == "string":
                    value = self.evaluate(parameters, index, tree[2][0], bindings)
                    if type(value) is bool:
                        return "True" if value else "False"
                return super().evaluate(parameters, index, tree, bindings)

        expression = ("[__bicep.browserExceptionFragmentValue(variables('browserExceptionTemplate'), "
                      "variables('browserExceptionSource'), parameters('fixtureConfigured'))]")
        for configured, expected in ((True, "true"), (False, "false")):
            with self.subTest(credential_login_configured=configured):
                reader = AzureBooleanExpression("[true()]", self.template["variables"],
                                                functions=self.template.get("functions", []))
                parameters = {"fixtureConfigured": configured}
                self.assertEqual(reader.resolve("[string(parameters('fixtureConfigured'))]", parameters),
                                 "True" if configured else "False")
                rendered = reader.resolve(expression, parameters)
                self.assertNotIn("__CREDENTIAL_BROWSER_CONFIGURED__", rendered)
                value = ET.fromstring(rendered).find("set-variable[@name='gatewayCredentialBrowserException']").get("value")
                self.assertRegex(value, r"\breturn " + expected + r"\s")
                self.assertNotRegex(value, r"\breturn (?:True|False)\b")

    def test_entra_open_product_omits_subscription_only_approval_and_preserves_native_contracts(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["entraAuthentication"] = {"enabled": True}
        expanded = expand(self.template, parameters)
        products = [resource for resource in expanded["resources"]
                    if resource["type"] == "Microsoft.ApiManagement/service/products"]
        dedicated = [product for product in products if product["name"].endswith("/core-entra-model-users")]
        self.assertEqual(len(dedicated), 1)
        self.assertEqual(dedicated[0]["properties"], {
            "displayName": "Core Entra model users", "description": "core-owned-entra-model-users-v1",
            "subscriptionRequired": False, "state": "notPublished"})
        protected = [product for product in products if product not in dedicated]
        self.assertTrue(protected)
        self.assertTrue(all(product["properties"]["subscriptionRequired"] is True
                            and product["properties"]["approvalRequired"] is True for product in protected))
        self.assertTrue(all(resource["properties"]["subscriptionRequired"] is True for resource in expanded["resources"]
                            if resource["type"] == "Microsoft.ApiManagement/service/apis"))
        policy = next(resource for resource in expanded["resources"]
                      if resource["type"] == "Microsoft.ApiManagement/service/products/policies"
                      and resource["name"].endswith("/core-entra-model-users/policy"))
        self.assertEqual(policy["properties"]["format"], "rawxml")
        root = ET.fromstring(policy["properties"]["value"])
        self.assertEqual([section.tag for section in root], ["inbound", "backend", "outbound", "on-error"])
        self.assertTrue(all(len(section) == 1 and section[0].tag == "base" for section in root))
        self.assertFalse(any(resource["type"] == "Microsoft.ApiManagement/service/products/apis"
                             and "/core-entra-model-users/" in resource["name"] for resource in expanded["resources"]))

    def test_protected_source_output_matches_resource_assignments_and_fits_expanded_budget(self):
        result = self.assert_split_budgets(copy.deepcopy(self.parameters))
        expanded = result["publication"]["expanded"]
        source = result["source"]["expanded"]["outputs"]["entraPublicationSource"]["value"]
        actual = {}
        actual_properties = {}
        for resource in expanded["resources"]:
            parts = resource["name"].split("/")
            if "properties" in resource and resource["type"].startswith("Microsoft.ApiManagement/service/"):
                kinds = resource["type"].split("/")[2:]
                suffix = "/" + "/".join(part for pair in zip(kinds, parts[1:]) for part in pair)
                actual_properties[suffix] = resource["properties"]
            if resource["type"].endswith("/policyFragments"):
                actual["/policyFragments/" + parts[1]] = resource["properties"]["value"]
            elif resource["type"].endswith("/apis/operations/policies"):
                actual["/apis/" + parts[1] + "/operations/" + parts[2] + "/policies/policy"] = resource["properties"]["value"]
            elif resource["type"].endswith("/apis/policies"):
                actual["/apis/" + parts[1] + "/policies/policy"] = resource["properties"]["value"]
        self.assertTrue(source["apis"])
        self.assertEqual(source["schemaVersion"], 2)
        self.assertEqual({entry["name"] for entry in source["apis"]},
                         {api["name"] for api in self.parameters["apiDefinitions"]}
                         | {api["mcp"]["name"] for api in self.parameters["apiDefinitions"] if "mcp" in api})
        for entry in source["apis"]:
            observed = actual_properties["/apis/" + entry["name"]]
            self.assertEqual({key: observed[key] for key in entry["properties"]}, entry["properties"])
        for entry in source["policies"]:
            self.assertEqual(entry["value"], actual[entry["suffix"]])
            self.assertNotIn("__AUDIT_", entry["value"])
            self.assertNotIn("__CORE_", entry["value"])
        for key in ("backends", "namedValues", "apiChildren"):
            for entry in source[key]:
                self.assertEqual(entry["properties"], actual_properties[entry["suffix"]])
        self.assertNotIn("reference(", source_without_identity_binding(self.template))
        # 初期値のキー構成だけでなく、標準のCMとBearerを同時に使う構成も測る。
        for credential_enabled, entra_enabled in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(credential_enabled=credential_enabled, entra_enabled=entra_enabled):
                parameters = copy.deepcopy(self.parameters)
                parameters["entraAuthentication"] = {"enabled": entra_enabled}
                if credential_enabled:
                    parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
                case = self.assert_split_budgets(parameters)
                source_case = case["source"]["expanded"]["outputs"]["entraPublicationSource"]["value"]
                self.assertEqual(any(entry["suffix"] == "/apis/credential-manager-login/policies/policy"
                                     for entry in source_case["policies"]), credential_enabled)
                for entry in source_case["policies"]:
                    self.assertNotIn("__AUDIT_", entry["value"])
                    self.assertNotIn("__CORE_", entry["value"])
                    self.assertNotIn("__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__", entry["value"])
                browser_case = case["source"]["expanded"]["outputs"]["credentialBrowserPublicationSource"]["value"]
                self.assertEqual(bool(browser_case), credential_enabled)

    def test_full_source_contains_every_fragment_and_retains_non_cm_model_authentication(self):
        for credential_enabled in (False, True):
            with self.subTest(credential_enabled=credential_enabled):
                parameters = copy.deepcopy(self.parameters)
                if credential_enabled:
                    parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
                # 追加HTTPの外部キー認証も、CM変数を用意せず宣言した下流認証を生成します。
                external = copy.deepcopy(next(api for api in parameters["apiDefinitions"]
                                              if api["name"] == "graph-profile-v1"))
                external.pop("credentialConnection")
                external.update(name="synthetic-external-header-api", path="contracts/external-header",
                                displayName="Synthetic external header API",
                                backendUrl="https://provider.example.com/v1", backendAudience="https://provider.example.com",
                                backendHeaders={"x-ms-client-request-id": "synthetic-external-request-id"},
                                backendAuthentication={"mode": "keyVaultHeader", "headerName": "Authorization",
                                                       "headerValuePrefix": "Bearer ",
                                                       "secretIdentifier": "https://fixture.vault.azure.net/secrets/registered-provider"})
                parameters["apiDefinitions"].append(external)
                expanded = expand(self.template, parameters)
                reader = SizeExpression("[true()]", self.template["variables"], functions=self.template.get("functions", []))
                source = reader.resolve(self.template["outputs"]["entraPublicationSource"]["value"], expanded["parameters"])
                raw_policies = {}
                for resource in expanded["resources"]:
                    if "properties" not in resource or not resource["type"].endswith(("/policies", "/policyFragments")):
                        continue
                    kinds = resource["type"].split("/")[2:]
                    names = resource["name"].split("/")[1:]
                    suffix = "/" + "/".join(part for pair in zip(kinds, names) for part in pair)
                    raw_policies[suffix] = resource["properties"]["value"]
                policies = {}
                for entry in source["policies"]:
                    value = raw_policies[entry["suffix"]]
                    self.assertEqual(entry["value"], value)
                    policies[entry["suffix"]] = value
                http_apis = {"/apis/" + api["name"] + "/policies/policy": api for api in parameters["apiDefinitions"]
                             if api["apiType"] == "http"}
                non_cm_http = {suffix: api for suffix, api in http_apis.items() if "credentialConnection" not in api}
                self.assertTrue(non_cm_http)
                self.assertTrue(set(http_apis) <= set(policies))
                self.assertTrue(source["apis"])
                preparation = ET.fromstring(policies["/policyFragments/core-api-request-preparation"])
                request_ids = preparation.findall('set-header[@name="x-ms-client-request-id"]')
                self.assertEqual([(node.get("exists-action"), node.find("value").text) for node in request_ids],
                                 [("override", "@(context.RequestId.ToString())")])
                for suffix, value in policies.items():
                    root = ET.fromstring(value)
                    for include in root.iter("include-fragment"):
                        fragment = include.get("fragment-id")
                        self.assertIn("/policyFragments/" + fragment, policies, (suffix, fragment))
                    if suffix in http_apis:
                        api = http_apis[suffix]
                        inbound = root.find("inbound")
                        nodes = list(inbound)
                        prepare = inbound.find('include-fragment[@fragment-id="core-api-request-preparation"]')
                        self.assertIsNotNone(prepare)
                        self.assertEqual(len(inbound.findall('include-fragment[@fragment-id="core-api-request-preparation"]')), 1)
                        self.assertEqual(nodes.index(prepare), nodes.index(inbound.find("base")) + 1)
                        # 固定の再設定はなく、明示した下流認証・headerのwriterだけを残します。
                        backends = [operation["backendOverride"] for operation in api["operations"] if "backendOverride" in operation] + [api]
                        declared = []
                        for backend in backends:
                            authentication = backend["backendAuthentication"]
                            if (authentication["mode"] == "keyVaultHeader"
                                    and authentication["headerName"].casefold() == "x-ms-client-request-id"):
                                # keyVaultHeaderはAPI側だけで受付可能で、操作別overrideには使えません。
                                declared.append((authentication["headerName"],
                                                 authentication["headerValuePrefix"] + "{{" + api["name"] + "-backend-secret}}"))
                            declared.extend((name, text) for name, text in backend.get("backendHeaders", {}).items()
                                            if name.casefold() == "x-ms-client-request-id")
                        writers = [node for node in inbound.iter("set-header")
                                   if node.get("name").casefold() == "x-ms-client-request-id"]
                        self.assertEqual([(node.get("name"), node.find("value").text) for node in writers], declared, suffix)
                        self.assertTrue(all(node.get("exists-action") == "override" for node in writers))
                    if suffix in non_cm_http:
                        api = non_cm_http[suffix]
                        inbound = root.find("inbound")
                        self.assertNotIn("gatewayCredentialConnection", value, suffix)
                        self.assertIsNone(inbound.find('set-variable[@name="gatewayCredentialConnection"]'))
                        self.assertFalse(any(node.get("fragment-id").startswith("core-credential-") for node in root.iter("include-fragment")))
                        for header in ("Authorization", api["subscriptionKeyHeader"], "Ocp-Apim-Subscription-Key", "api-key"):
                            self.assertIsNotNone(inbound.find('set-header[@name="' + header + '"][@exists-action="delete"]'))
                        for query in ("subscription-key", "api-key"):
                            self.assertIsNotNone(inbound.find('set-query-parameter[@name="' + query + '"][@exists-action="delete"]'))
                        if not any("backendOverride" in operation for operation in api["operations"]):
                            self.assertIsNotNone(inbound.find('set-backend-service[@backend-id="' + api["name"] + '-backend"]'))
                            if api["backendAuthentication"]["mode"] == "managedIdentity":
                                identity = inbound.find("authentication-managed-identity")
                                self.assertIsNotNone(identity)
                                self.assertEqual(identity.get("resource"), api["backendAudience"])
                                self.assertIsNone(root.find('./inbound/choose/otherwise/authentication-managed-identity'))
                            elif api["backendAuthentication"]["mode"] == "keyVaultHeader":
                                authentication = api["backendAuthentication"]
                                backend = inbound.find('set-header[@name="' + authentication["headerName"] + '"][@exists-action="override"]')
                                self.assertIsNotNone(backend)
                                self.assertEqual(backend.find("value").text,
                                                 authentication["headerValuePrefix"] + "{{" + api["name"] + "-backend-secret}}")
                                ingress = inbound.find('set-header[@name="' + authentication["headerName"] + '"][@exists-action="delete"]')
                                if ingress is not None:
                                    self.assertLess(list(inbound).index(ingress), list(inbound).index(backend))
                                for request_id in inbound.findall("set-header"):
                                    if request_id.get("name").casefold() == "x-ms-client-request-id" and request_id is not backend:
                                        self.assertLess(list(inbound).index(backend), list(inbound).index(request_id))
                self.assertNotIn("reference(", source_without_identity_binding(self.template))

    def test_http_without_cm_preserves_operation_backend_selection_and_header_cleanup(self):
        parameters = copy.deepcopy(self.parameters)
        api = next(api for api in parameters["apiDefinitions"] if api["name"] == "openai-inference-v1")
        api["backendHeaders"]["x-ms-client-request-id"] = "synthetic-default-request-id"
        operation = api["operations"][0]
        operation["backendOverride"] = {name: copy.deepcopy(api[name]) for name in (
            "backendUrl", "backendService", "backendAudience", "backendAuthentication", "backendHeaders")}
        operation["backendOverride"]["backendUrl"] += "/alternate"
        operation["backendOverride"]["backendHeaders"]["x-ms-client-request-id"] = "synthetic-operation-request-id"
        expanded = expand(self.template, parameters)
        policy = next(resource["properties"]["value"] for resource in expanded["resources"]
                      if resource["type"] == "Microsoft.ApiManagement/service/apis/policies"
                      and resource["name"].endswith("/" + api["name"] + "/policy"))
        root = ET.fromstring(policy)
        inbound = root.find("inbound")
        selection = next(node for node in inbound.findall("choose")
                         if node.find("when/set-backend-service") is not None)
        self.assertIn(operation["name"], selection.find("when").get("condition"))
        self.assertIsNotNone(selection.find("when/authentication-managed-identity"))
        self.assertIsNotNone(selection.find("otherwise/set-backend-service"))
        self.assertIsNotNone(selection.find("otherwise/authentication-managed-identity"))
        self.assertIsNone(inbound.find('set-variable[@name="gatewayCredentialConnection"]'))
        self.assertNotIn("gatewayCredentialConnection", policy)
        self.assertFalse(any(node.get("fragment-id").startswith("core-credential-")
                             for node in root.iter("include-fragment")))
        nodes = list(inbound)
        preparation = inbound.find('include-fragment[@fragment-id="core-api-request-preparation"]')
        self.assertIsNotNone(preparation)
        self.assertEqual(nodes.index(preparation), nodes.index(inbound.find("base")) + 1)
        self.assertLess(nodes.index(preparation), nodes.index(selection))
        self.assertIsNone(inbound.find('set-header[@name="x-ms-client-request-id"]'))
        for branch, expected in ((selection.find("when"), "synthetic-operation-request-id"),
                                 (selection.find("otherwise"), "synthetic-default-request-id")):
            request_id = branch.find('set-header[@name="x-ms-client-request-id"]')
            self.assertIsNotNone(request_id)
            self.assertEqual(request_id.find("value").text, expected)
            self.assertLess(list(branch).index(branch.find("authentication-managed-identity")), list(branch).index(request_id))
        for header in ("Authorization", "X-Core-Connection-Session", "X-Core-Connection-Poll", "X-Core-Connection-Transaction"):
            self.assertIsNotNone(inbound.find('set-header[@name="' + header + '"][@exists-action="delete"]'))

    def test_released_membership_uses_declared_business_apis_and_preserves_verification_products(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
        # APIが存在してもfalse/未指定なら通常製品へは所属させません。
        parameters["apiDefinitions"][0]["released"] = False
        parameters["apiDefinitions"][1].pop("released")
        expanded = expand(self.template, parameters)
        links = {r["name"] for r in expanded["resources"]
                 if r["type"] == "Microsoft.ApiManagement/service/products/apis"}
        selected = parameters["apiDefinitions"]
        expected = {"apim-test-fixture/released-apis/" + api["name"] for api in selected if api.get("released")}
        expected.update("apim-test-fixture/released-apis/" + api["mcp"]["name"]
                        for api in selected if api.get("released") and "mcp" in api)
        expected.add("apim-test-fixture/released-apis/credential-manager-login")
        self.assertEqual({name for name in links if "/released-apis/" in name}, expected)
        self.assertFalse(any("credential-manager-browser" in name for name in links))
        self.assertTrue(all("apim-test-fixture/" + api["productId"] + "/" + api["name"] in links for api in selected))
        products = [r for r in expanded["resources"] if r["type"] == "Microsoft.ApiManagement/service/products"
                    and r["name"].endswith("/released-apis")]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["properties"]["displayName"], "Released APIs")
        self.assertTrue(products[0]["properties"]["subscriptionRequired"])
        parameters["apiDefinitions"][1]["released"] = True
        expanded = expand(self.template, parameters)
        links = {r["name"] for r in expanded["resources"]
                 if r["type"] == "Microsoft.ApiManagement/service/products/apis"}
        self.assertIn("apim-test-fixture/released-apis/pii-v1", links)
        self.assertIn("apim-test-fixture/released-apis/pii-v1-mcp", links)

    def test_graph_api_publishes_once_with_cm_and_key_authorization(self):
        expanded = expand(self.template, copy.deepcopy(self.parameters))
        graph_names = {"graph-profile-v1"}
        graphs = [api for api in self.parameters["apiDefinitions"] if api["name"] in graph_names]
        self.assertEqual({api["name"] for api in graphs}, graph_names)
        for api in graphs:
            self.assertEqual(api["authorization"]["mode"], "subscriptionKey")
            self.assertEqual(api["credentialConnection"]["mode"], "personal")
            self.assertEqual(api["backendAuthentication"]["mode"], "none")
        graph_policies = [r["properties"]["value"] for r in expanded["resources"]
                          if r["type"] == "Microsoft.ApiManagement/service/apis/policies"
                          and any('/' + name + '/policy' in r["name"] for name in graph_names)]
        self.assertEqual(len(graph_policies), 1)
        for policy in graph_policies:
            root = ET.fromstring(policy)
            connection = root.find('./inbound/include-fragment[@fragment-id="core-credential-connection"]')
            self.assertIsNotNone(connection)
            self.assertIsNotNone(root.find('./inbound/include-fragment[@fragment-id="core-credential-session-use"]'))
            self.assertIsNotNone(root.find('./outbound/include-fragment[@fragment-id="core-credential-connection-outbound"]'))
            self.assertNotEqual(root.find('./inbound/set-variable[@name="gatewayCredentialConnection"]').get("value"), "")
            self.assertEqual(root.findall('.//validate-azure-ad-token'), [])
        fragment_names = {r["name"].rsplit('/', 1)[-1] for r in expanded["resources"]
                          if r["type"] == "Microsoft.ApiManagement/service/policyFragments"}
        self.assertTrue({"core-credential-connection", "core-credential-connection-outbound"} <= fragment_names)
        self.assertFalse({"core-obo", "core-obo-outbound"} & fragment_names)

    def test_disabled_http_cm_uses_the_same_direct_connection_flow_for_standard_and_additional_apis(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
        parameters["entraAuthentication"] = {"enabled": True}
        graph = next(api for api in parameters["apiDefinitions"] if api["name"] == "graph-profile-v1")
        graph["credentialConnection"]["enabled"] = False
        extra = copy.deepcopy(graph)
        extra.update(name="synthetic-extra-cm-api", path="contracts/extra-cm",
                     displayName="Synthetic additional CM API")
        parameters["apiDefinitions"].append(extra)
        expanded = expand(self.template, parameters)
        reader = SizeExpression("[true()]", self.template["variables"], functions=self.template.get("functions", []))
        source = reader.resolve(self.template["outputs"]["entraPublicationSource"]["value"], expanded["parameters"])
        source_policies = {entry["suffix"]: entry["value"] for entry in source["policies"]}
        self.assertEqual(sum(api["name"] == extra["name"] for api in source["apis"]), 1)
        for api in (graph, extra):
            with self.subTest(api=api["name"]):
                policy = next(resource["properties"]["value"] for resource in expanded["resources"]
                              if resource["type"] == "Microsoft.ApiManagement/service/apis/policies"
                              and resource["name"].endswith("/" + api["name"] + "/policy"))
                self.assertEqual(source_policies["/apis/" + api["name"] + "/policies/policy"], policy)
                root = ET.fromstring(policy)
                inbound = root.find("inbound")
                prepare = inbound.find('include-fragment[@fragment-id="core-api-request-preparation"]')
                self.assertIsNotNone(prepare)
                self.assertEqual(list(inbound).index(prepare), list(inbound).index(inbound.find("base")) + 1)
                self.assertEqual([node.find("value").text for node in inbound.iter("set-header")
                                  if node.get("name").casefold() == "x-ms-client-request-id"],
                                 [text for name, text in api["backendHeaders"].items() if name.casefold() == "x-ms-client-request-id"])
                configured = inbound.find('set-variable[@name="gatewayCredentialConnection"]')
                decoded = json.loads(base64.b64decode(configured.get("value")))
                self.assertEqual(decoded, api["credentialConnection"])
                self.assertIs(decoded["enabled"], False)
                # 無効はCM未設定ではありません。埋めた設定の直後に同じ接続・使用時確認へ進めます。
                nodes = list(inbound)
                position = nodes.index(configured)
                self.assertEqual([(node.tag, node.get("fragment-id")) for node in nodes[position + 1:position + 4]],
                                 [("include-fragment", "core-credential-connection"),
                                  ("choose", None),
                                  ("include-fragment", "core-credential-session-use")])
                self.assertEqual(nodes[position + 2].get("id"), "core-credential-caller-required")
                self.assertEqual([node.get("fragment-id") for node in inbound.iter("include-fragment")
                                  if node.get("fragment-id").startswith("core-credential-")],
                                 ["core-credential-connection", "core-credential-session-use"])
                self.assertFalse(any("gatewayCredentialConnection" in node.get("condition", "")
                                     for node in inbound.iter("when")))
                self.assertEqual([node.tag for node in inbound.iter() if node.tag.startswith("authentication-")], [])
                self.assertIsNotNone(inbound.find('set-backend-service[@backend-id="' + api["name"] + '-backend"]'))
                self.assertIsNotNone(root.find('./outbound/include-fragment[@fragment-id="core-credential-connection-outbound"]'))
        # 実配備する共通部品にもenabledの拒否と停止応答が残ることを確認します。
        fragments = {resource["name"].rsplit("/", 1)[-1]: resource["properties"]["value"]
                     for resource in expanded["resources"]
                     if resource["type"] == "Microsoft.ApiManagement/service/policyFragments"}
        for name in ("core-api-request-preparation", "core-credential-connection", "core-credential-session-use"):
            self.assertEqual(source_policies["/policyFragments/" + name], fragments[name])
        preparation = ET.fromstring(fragments["core-api-request-preparation"])
        self.assertEqual([(node.get("exists-action"), node.find("value").text)
                          for node in preparation.findall('set-header[@name="x-ms-client-request-id"]')],
                         [("override", "@(context.RequestId.ToString())")])
        connection = ET.fromstring(fragments["core-credential-connection"])
        selection = "".join(connection.find('set-variable[@name="gatewayConnectionSelection"]').get("value").split())
        self.assertIn('newJProperty("allowed",false)', selection)
        self.assertIn('newJProperty("status",403)', selection)
        self.assertIn('if(configured["enabled"]?.Type!=JTokenType.Boolean||!(bool)configured["enabled"]){returnresult.ToString();}', selection)
        session_use = ET.fromstring(fragments["core-credential-session-use"])
        denied = next(node for node in session_use.findall("./choose/when")
                      if '!(bool)JObject.Parse((string)context.Variables["gatewayConnectionSelection"])["allowed"]'
                      in node.get("condition", ""))
        self.assertIsNotNone(denied.find("return-response"))

    def test_subscription_overrides_still_fit(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["rateLimits"]["subscriptionKey"]["overrides"] = [
            {"callerId": "subscription-fixture-" + str(index), "calls": 30, "renewalPeriodSeconds": 60}
            for index in range(16)]
        for credential_enabled, entra_enabled in ((False, False), (True, True)):
            with self.subTest(credential_enabled=credential_enabled, entra_enabled=entra_enabled):
                case = copy.deepcopy(parameters)
                case["entraAuthentication"] = {"enabled": entra_enabled}
                if credential_enabled:
                    case["credentialLogin"] = copy.deepcopy(self.credential_login)
                self.assert_split_budgets(case)

    def test_additional_api_contract_preserves_source_and_resource_definitions_with_headroom(self):
        parameters = copy.deepcopy(self.parameters)
        extra = copy.deepcopy(next(api for api in parameters["apiDefinitions"]
                                   if api["apiType"] == "http" and api["backendService"] == "foundryOpenAi"
                                   and "credentialConnection" not in api and "mcp" not in api))
        extra.update(name="synthetic-extra-model-api", path="contracts/extra-model",
                     displayName="Synthetic additional model API")
        parameters["apiDefinitions"].append(extra)
        parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
        parameters["entraAuthentication"] = {"enabled": True}
        result = self.assert_split_budgets(parameters)
        source = result["source"]["expanded"]["outputs"]["entraPublicationSource"]["value"]
        self.assertEqual(sum(api["name"] == extra["name"] for api in source["apis"]), 1)
        policy = next(entry["value"] for entry in source["policies"]
                      if entry["suffix"] == "/apis/" + extra["name"] + "/policies/policy")
        self.assertNotIn("gatewayCredentialConnection", policy)
        inbound = ET.fromstring(policy).find("inbound")
        self.assertIsNone(inbound.find('set-variable[@name="gatewayCredentialConnection"]'))
        preparation = inbound.find('include-fragment[@fragment-id="core-api-request-preparation"]')
        self.assertIsNotNone(preparation)
        self.assertEqual(list(inbound).index(preparation), list(inbound).index(inbound.find("base")) + 1)
        self.assertEqual([node.find("value").text for node in inbound.iter("set-header")
                          if node.get("name").casefold() == "x-ms-client-request-id"],
                         [text for name, text in extra["backendHeaders"].items() if name.casefold() == "x-ms-client-request-id"])

    def test_generated_profile_routes_and_live_operation_match_public_contracts(self):
        expanded = expand(self.template, copy.deepcopy(self.parameters))
        policies = [r for r in expanded["resources"] if r["type"] == "Microsoft.ApiManagement/service/apis/operations/policies"]
        flux = [r for r in policies if '/blackforest-images-v1/' in r['name']]
        self.assertEqual(len(flux), 2)
        for resource in flux:
            root = ET.fromstring(resource['properties']['value'])
            self.assertIsNotNone(root.find('./inbound/base'))
            routes = root.findall('./inbound/choose/when/rewrite-uri')
            self.assertEqual({r.get('template') for r in routes}, {
                '/providers/blackforestlabs/v1/flux-2-flex?api-version=preview',
                '/providers/blackforestlabs/v1/flux-2-pro?api-version=preview',
            })
            self.assertTrue(all(r.get('copy-unmatched-params') == 'false' for r in routes))
        live = [r for r in policies if '/openai-live-control-v1/live-create/' in r['name']]
        self.assertEqual(len(live), 1)
        root = ET.fromstring(live[0]['properties']['value'])
        self.assertIsNotNone(root.find('./inbound/base'))
        self.assertEqual(root.find('./inbound/rewrite-uri').get('template'), '/live/sessions')
        self.assertIsNotNone(root.find('./outbound/choose/when/set-variable[@name="liveSessionAnswer"]'))


    def test_credential_login_apis_expand_without_unresolved_settings_or_token_diagnostics(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["credentialLogin"] = copy.deepcopy(self.credential_login)
        expanded = expand(self.template, parameters)
        policies = [r for r in expanded["resources"] if r["type"].endswith("/policies") and "/credential-manager-" in r["name"]]
        api_policy = [policy for policy in policies if policy["type"].endswith("/apis/policies")]
        self.assertEqual(len(api_policy), 1)
        self.assertTrue(api_policy[0]["name"].endswith("/credential-manager-login/policy"))
        api_root = ET.fromstring(api_policy[0]["properties"]["value"])
        self.assertEqual([node.tag for node in api_root.find('inbound')], ['base'])
        self.assertTrue(all(len(api_root.find(section)) == 1 and api_root.find(section)[0].tag == 'base'
                            for section in ('backend', 'outbound', 'on-error')))
        operation_policies = [policy for policy in policies if policy not in api_policy]
        self.assertEqual(len(operation_policies), len(credential_login.OPERATIONS) + len(credential_login.BROWSER_OPERATIONS))
        reader = SizeExpression("[true()]", self.template["variables"], functions=self.template.get("functions", []))
        fixture_settings = {**parameters["credentialLogin"], "apimPrincipalId": reader.resolve(
            "[reference('fixture').identity.principalId]", expanded["parameters"])}
        for policy in operation_policies:
            value = policy["properties"]["value"]
            self.assertNotRegex(value, r"__[A-Z][A-Z0-9_]*__")
            self.assertNotIn("__ARM_", value)
            root = ET.fromstring(value)
            operation_name = policy["name"].split("/")[-2]
            from policy_fixture_paths import policy_fixture_path
            from deployment.helpers.apim_audit import render_audit_policy
            raw_policy = render_audit_policy(policy_fixture_path(
                CORE / "apps/gateway/apim/policies", "credential-login/" + operation_name + ".xml").read_text(encoding="utf-8"))
            expected = reader.resolve("[__bicep.renderCredentialLoginOperation(parameters('fixturePolicy'), parameters('fixtureSettings'), '')]",
                                      {**expanded["parameters"], "fixturePolicy": raw_policy, "fixtureSettings": fixture_settings})
            self.assertEqual(ET.tostring(root), ET.tostring(ET.fromstring(expected)), operation_name)
            for response in root.findall(".//return-response"):
                self.assertIsNotNone(response.find("set-header[@name='apim-request-id']"))
        result = self.assert_split_budgets(parameters)
        diagnostics = [r for r in expanded["resources"] if r["type"].endswith("/diagnostics") and "/credential-manager-" in r["name"]]
        self.assertEqual(len(diagnostics), 2)
        for diagnostic in diagnostics:
            self.assertEqual(diagnostic["properties"]["frontend"]["response"]["headers"], [])
            self.assertEqual(diagnostic["properties"]["frontend"]["request"]["body"]["bytes"], 0)
        fragments=[r for r in expanded['resources'] if r['type'].endswith('/policyFragments') and r['name'].rsplit('/',1)[-1] in ('core-credential-connection','core-credential-session-use','core-credential-connection-outbound')]
        self.assertEqual(len(fragments),3)
        oversized_fragments={fragment['name'].rsplit('/',1)[-1]:len(fragment['properties']['value'].encode('utf-8'))
                             for fragment in fragments if len(fragment['properties']['value'].encode('utf-8'))>=32768}
        self.assertFalse(oversized_fragments,'Credential fragment UTF-8 bytes exceed the publication budget: '+str(oversized_fragments))
        source_policies={entry['suffix']:entry['value'] for entry in result['source']['expanded']['outputs']['entraPublicationSource']['value']['policies']}
        session_value=next(fragment['properties']['value'] for fragment in fragments
                           if fragment['name'].endswith('/core-credential-session-use'))
        self.assertEqual(source_policies['/policyFragments/core-credential-session-use'],session_value)
        expected_session=credential_login.render_connection('credential-session-use.xml',fixture_settings,telemetry='synthetic-enabled')
        self.assertEqual(ET.tostring(ET.fromstring(session_value)),ET.tostring(ET.fromstring(expected_session)))
        self.assertLess(len(expected_session.encode('utf-8')),32768)
        self.assertNotIn('__CORE_CREDENTIAL_USE_RESULT_CSHARP__',session_value)
        credential_audit_messages = []
        for fragment in fragments:
            value=fragment['properties']['value']
            self.assertNotRegex(value,r'__[A-Z][A-Z0-9_]*__')
            # Compact connection-failure audit retains both verified key/user
            # subjects without embedding the full business telemetry body.
            messages = [message.text or '' for message in ET.fromstring(value).findall('.//trace[@source="core-gateway"]/message')]
            self.assertTrue(messages)
            self.assertIn('context.Subscription', value)
            credential_audit_messages.extend(messages)
            user_messages = [message for message in messages if 'credentialCallerId' in message]
            self.assertTrue(user_messages)
            for message in user_messages:
                self.assertIn('gatewayUserAuthentication', message)
                self.assertIn('verifiedTenantId', message)
                self.assertIn('verifiedPrincipalId', message)
                self.assertIn('verifiedClientId', message)
            self.assertTrue(all('core-user-audit-' not in message for message in messages))
            if fragment['name'].endswith('/core-credential-connection'):
                self.assertIn('/v2/session/authorize',value)
            else:
                self.assertIn('/v1/challenge',value)
        self.assertTrue(any('context.Subscription' in message and '"subscriptionKey"' in message for message in credential_audit_messages))
        apis = {r["properties"]["path"]: r["properties"] for r in expanded["resources"] if r["type"] == "Microsoft.ApiManagement/service/apis"}
        self.assertTrue(apis['auth/connections']['subscriptionRequired'])
        self.assertFalse(apis['auth/connections/browser']['subscriptionRequired'])
        operations={r['name'].rsplit('/',1)[-1]:r['properties']['urlTemplate'] for r in expanded['resources']
                    if r['type']=='Microsoft.ApiManagement/service/apis/operations' and '/credential-manager-login/' in r['name']}
        self.assertEqual(operations,{name:'/'+path for name,path in credential_login.OPERATION_PATHS.items()})


if __name__ == "__main__":
    unittest.main()
