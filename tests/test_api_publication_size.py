"""APIが増えた際のARM展開後サイズを、配備前に実生成物で確認する。

ARMはソースファイルだけでなく、変数・copyループの展開結果にもサイズ制限を
適用する。全APIのXMLを段階別配列へ保存する回帰を防ぐ。この見積りは、Azure
内部の圧縮済みジョブサイズ算定そのものを再実装するものではない。
"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from test_api_protocols import bound_coverage, configuration
from arm_helpers import DiscoveryExpression
from deployment.helpers import credential_login

CORE = Path(__file__).resolve().parents[1]
SYNTHETIC_SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


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


class ApiPublicationSizeTests(unittest.TestCase):
    """公開例の全APIと追加利用者を展開し、サイズに余裕があることを調べる。"""

    @classmethod
    def setUpClass(cls):
        # 正式18 APIの公開例から実際のXMLとARM展開後サイズを調べます。
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

    def test_standard_apis_fit_expanded_budget_with_headroom(self):
        self.assertTrue(all(api["serviceClass"] == "platform" for api in self.parameters["apiDefinitions"]))
        self.assertFalse(any(api["name"].startswith("sample-") for api in self.parameters["apiDefinitions"]))
        expanded = expand(self.template, copy.deepcopy(self.parameters))
        size = len(json.dumps(expanded, ensure_ascii=False, separators=(",", ":")).encode())
        # 4MiB境界を狙わず、メタデータ等の余裕を残して350万bytes未満にする。
        self.assertLess(size, 3_500_000, "Expanded publication bytes: " + str(size))
        self.assertEqual(sum(resource["type"] == "Microsoft.ApiManagement/service/apis" for resource in expanded["resources"]), len(self.parameters["apiDefinitions"]) + sum("mcp" in api for api in self.parameters["apiDefinitions"]))

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
            self.assertIsNotNone(root.find('./inbound/choose/when/include-fragment[@fragment-id="core-credential-connection"]'))
            self.assertEqual(root.findall('.//validate-azure-ad-token'), [])
        fragment_names = {r["name"].rsplit('/', 1)[-1] for r in expanded["resources"]
                          if r["type"] == "Microsoft.ApiManagement/service/policyFragments"}
        self.assertTrue({"core-credential-connection", "core-credential-connection-outbound"} <= fragment_names)
        self.assertFalse({"core-obo", "core-obo-outbound"} & fragment_names)

    def test_subscription_overrides_still_fit(self):
        parameters = copy.deepcopy(self.parameters)
        parameters["rateLimits"]["subscriptionKey"]["overrides"] = [
            {"callerId": "subscription-fixture-" + str(index), "calls": 30, "renewalPeriodSeconds": 60}
            for index in range(16)]
        expanded = expand(self.template, parameters)
        size = len(json.dumps(expanded, ensure_ascii=False, separators=(",", ":")).encode())
        self.assertLess(size, 3_500_000, "Expanded additional-contract publication bytes: " + str(size))

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
        self.assertEqual(len(policies), len(credential_login.OPERATIONS) + len(credential_login.BROWSER_OPERATIONS))
        for policy in policies:
            value = policy["properties"]["value"]
            self.assertNotRegex(value, r"__[A-Z][A-Z0-9_]*__")
            self.assertNotIn("__ARM_", value)
            root = ET.fromstring(value)
            for response in root.findall(".//return-response"):
                self.assertIsNotNone(response.find("set-header[@name='apim-request-id']"))
        self.assertLess(len(json.dumps(expanded, ensure_ascii=False).encode()), 3_500_000)
        diagnostics = [r for r in expanded["resources"] if r["type"].endswith("/diagnostics") and "/credential-manager-" in r["name"]]
        self.assertEqual(len(diagnostics), 2)
        for diagnostic in diagnostics:
            self.assertEqual(diagnostic["properties"]["frontend"]["response"]["headers"], [])
            self.assertEqual(diagnostic["properties"]["frontend"]["request"]["body"]["bytes"], 0)
        fragments=[r for r in expanded['resources'] if r['type'].endswith('/policyFragments') and r['name'].rsplit('/',1)[-1] in ('core-credential-connection','core-credential-session-use','core-credential-connection-outbound')]
        self.assertEqual(len(fragments),3)
        for fragment in fragments:
            value=fragment['properties']['value']
            self.assertNotRegex(value,r'__[A-Z][A-Z0-9_]*__')
            self.assertLess(len(value.encode('utf-8')),32768)
            if fragment['name'].endswith('/core-credential-connection'):
                self.assertIn('/v2/session/authorize',value)
            else:
                self.assertIn('/v1/challenge',value)
        apis = {r["properties"]["path"]: r["properties"] for r in expanded["resources"] if r["type"] == "Microsoft.ApiManagement/service/apis"}
        self.assertTrue(apis['auth/connections']['subscriptionRequired'])
        self.assertFalse(apis['auth/connections/browser']['subscriptionRequired'])
        operations={r['name'].rsplit('/',1)[-1]:r['properties']['urlTemplate'] for r in expanded['resources']
                    if r['type']=='Microsoft.ApiManagement/service/apis/operations' and '/credential-manager-login/' in r['name']}
        self.assertEqual(operations,{name:'/'+path for name,path in credential_login.OPERATION_PATHS.items()})


if __name__ == "__main__":
    unittest.main()
