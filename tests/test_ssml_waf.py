"""認証callback・Speech・標準データURIの誤検知を、固定の用途別WAFだけで調整する。

コンパイルした ARM 式を合成入力で展開し、共通 WAF の保護と APIM への経路を
具体値で比較する。Azure 受入試験の代替ではなく、クラウドへの要求は実行しない。
"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import sys
import unittest

from arm_helpers import ArmExpression


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / 'scripts'))
from deployment.helpers.waf_body import ai_body_settings


class GatewayExpression(ArmExpression):
    """既存のローカル ARM 評価器に、この入口定義で使う関数だけを足す。"""

    def __init__(self, expression, variables, variable_caches=None):
        super().__init__(expression)
        self.variables = variables
        self.variable_caches = {} if variable_caches is None else variable_caches

    def resolve(self, item, parameters, index=0):
        if isinstance(item, str) and item.startswith("[["):
            return item[1:]  # ARM escapes a literal leading bracket.
        if isinstance(item, str) and item.startswith("[") and item.endswith("]"):
            return GatewayExpression(item, self.variables, self.variable_caches).evaluate(parameters, index)
        if isinstance(item, dict):
            return {key: self.resolve(value, parameters, index) for key, value in item.items()}
        if isinstance(item, list):
            return [self.resolve(value, parameters, index) for value in item]
        return item

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        if tree[0] != "call":
            return super().evaluate(parameters, index, tree, bindings)
        _, name, arguments = tree
        if name == "union":
            values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
            if isinstance(values[0], list):
                result = []
                for items in values:
                    for item in items:
                        if item not in result:
                            result.append(item)
                return result
        extra = {"variables", "and", "or", "not", "empty", "contains", "coalesce", "tryGet", "map", "filter",
                 "flatten", "shallowMerge", "resourceId", "true", "false", "length", "concat", "greater", "range"}
        if name not in extra:
            return super().evaluate(parameters, index, tree, bindings)
        values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
        if name == "variables":
            # ARM variables are immutable for one parameter input. Share their
            # evaluated values through recursive resolvers, never across inputs.
            cache = self.variable_caches.setdefault(json.dumps(parameters, sort_keys=True), {})
            if values[0] in cache:
                return cache[values[0]]
            if values[0] in self.variables:
                result = self.resolve(self.variables[values[0]], parameters, index)
            else:
                definition = next(item for item in self.variables.get("copy", []) if item["name"] == values[0])
                result = [self.resolve(definition["input"], parameters, number)
                          for number in range(self.resolve(definition["count"], parameters))]
            cache[values[0]] = result
            return result
        if name == "and": return all(values)
        if name == "or": return any(values)
        if name == "not": return not values[0]
        if name == "empty": return not values[0]
        if name == "contains": return values[1] in values[0]
        if name == "coalesce": return next(item for item in values if item is not None)
        if name == "tryGet": return values[0].get(values[1]) if isinstance(values[0], dict) else None
        if name == "map": return [values[1](item) for item in values[0]]
        if name == "filter": return [item for item in values[0] if values[1](item)]
        if name in ("flatten", "concat"):
            lists = values[0] if name == "flatten" else values
            return [item for items in lists for item in items]
        if name == "shallowMerge": return {key: value for item in values[0] for key, value in item.items()}
        if name == "resourceId": return "/fixture-resources/" + "/".join(values)
        if name == "true": return True
        if name == "false": return False
        if name == "greater": return values[0] > values[1]
        if name == "length": return len(values[0])
        if name == "range": return list(range(values[0], values[0] + values[1]))
        raise AssertionError("Unsupported ARM function: " + name)


class SsmlWafTests(unittest.TestCase):
    """正規 SSML パス以外の保護と、既存の HTTP/TCP 転送を維持する。"""

    @classmethod
    def setUpClass(cls):
        cls.main = compile_bicep('infra/main.bicep')
        resources = cls.main["resources"]
        cls.module = next(item for item in (resources.values() if isinstance(resources, dict) else resources)
                          if "wafPolicyIdentifier" in item.get("properties", {}).get("parameters", {}))
        cls.template = cls.module["properties"]["template"]
        resources = cls.template["resources"]
        cls.resources = list(resources.values() if isinstance(resources, dict) else resources)

    def parameters(self, **changes):
        result = {key: copy.deepcopy(value["defaultValue"]) for key, value in self.template["parameters"].items()
                  if "defaultValue" in value}
        result.update(productName="fixture", environmentName="test", resourceStem="fixture-test", wafPolicyIdentifier="abc123", location="japaneast",
            subnetId="/fixture-subnet", publicIpId="/fixture-public-ip", apimPrivateIp="10.40.2.68",
            apimHostName="apim-fixture.azure-api.net", publicHostName="api.example.com",
            keyVaultCertificateSecretId="https://kv-fixture.vault.azure.net/secrets/tls",
            certificateIdentityId="/fixture-mi", workspaceId="/fixture-workspace", auditStorageId="/fixture-audit",
            speechRawBodyPaths=["/models/azure-speech/v1/recognize", "/models/azure-speech/v1/synthesize"],
            grpcEnabled=True, l7IngressSecret="SYNTHETIC_PRIVATE_INPUT",
            wafExclusions=[{"matchVariable": "RequestHeaderValues", "selector": "X-Fixture", "selectorMatchOperator": "Equals"}],
            wafCustomRules=[{"name": "fixture-rule", "priority": 1, "action": "Block"}],
            additionalWafLogScrubbingRules=[{"state": "Enabled", "matchVariable": "RequestHeaderNames", "selector": "X-Fixture", "selectorMatchOperator": "Equals"}])
        result.update(portalHostName="portal.example.com", portalBackendIp="10.40.2.200",
            portalBackendHost="portal.fixture.azurecontainerapps.io")
        result.update(changes)
        return result

    def value(self, expression, parameters):
        return GatewayExpression(expression, self.template["variables"]).evaluate(parameters)

    def resource(self, purpose):
        name = "[format('wafpol-" + purpose + "-{0}', parameters('wafPolicyIdentifier'))]"
        return next(item for item in self.resources if item["name"] == name)

    def protocol_path_rules(self, properties):
        """API用の特殊WAF規則だけを返し、管理画面の経路混入を拒否する。"""
        pathmap = properties["urlPathMaps"][0]["properties"]
        rules = {rule["name"]: rule["properties"] for rule in pathmap["pathRules"]}
        self.assertTrue(pathmap["defaultBackendAddressPool"]["id"].endswith("/bepool-apim"))
        self.assertTrue(pathmap["defaultBackendHttpSettings"]["id"].endswith("/behttp-apim"))
        for rule in rules.values():
            self.assertEqual(rule["backendAddressPool"], pathmap["defaultBackendAddressPool"])
            self.assertEqual(rule["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
        return rules


    def test_required_portal_has_its_own_host_backend_and_health_probe(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        enabled = self.value(gateway["properties"], self.parameters(portalHostName="portal.example.com",
            portalBackendIp="10.40.2.200", portalBackendHost="portal.fixture.azurecontainerapps.io"))
        listener = next(item for item in enabled["httpListeners"] if item["name"] == "portal-https")
        self.assertEqual(listener["properties"]["hostName"], "portal.example.com")
        self.assertEqual(listener["properties"]["protocol"], "Https")
        pool = next(item for item in enabled["backendAddressPools"] if item["name"] == "bepool-portal")
        self.assertEqual(pool["properties"]["backendAddresses"], [{"ipAddress": "10.40.2.200"}])
        backend = next(item for item in enabled["backendHttpSettingsCollection"] if item["name"] == "behttp-portal")
        self.assertEqual(backend["properties"]["hostName"], "portal.fixture.azurecontainerapps.io")
        self.assertEqual(backend["properties"]["protocol"], "Https")
        probe = next(item for item in enabled["probes"] if item["name"] == "probe-portal")
        self.assertEqual(probe["properties"]["path"], "/api/health")
        self.assertEqual(probe["properties"]["host"], "portal.fixture.azurecontainerapps.io")
        self.assertEqual(probe["properties"]["match"]["statusCodes"], ["200"])
        self.assertEqual(len(enabled["httpListeners"]), 4)
        self.assertEqual(len(enabled["backendAddressPools"]), 2)
        self.protocol_path_rules(enabled)
        compiled = json.dumps(self.template)

    def test_speech_policy_keeps_baseline_and_has_only_two_raw_rule_adjustments(self):
        parameters = self.parameters()
        baseline = self.value(self.resource("g-base")["properties"], parameters)
        adjusted = self.value(self.resource("g-speech")["properties"], parameters)
        self.assertEqual(adjusted["policySettings"], ai_body_settings(baseline["policySettings"]))
        self.assertEqual(adjusted["managedRules"]["exclusions"], baseline["managedRules"]["exclusions"])
        self.assertEqual(adjusted["customRules"][:len(baseline["customRules"])], baseline["customRules"])
        self.assertEqual([rule["name"] for rule in adjusted["customRules"][len(baseline["customRules"]):]],
                         ["coreRawTraversalUri", "coreRawTraversalQuery", "coreRawTraversalHeaders", "coreRawTraversalCookies"])
        self.assertTrue(all(rule["action"] == "Block" for rule in adjusted["customRules"]))
        self.assertEqual(adjusted["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"], [{
            "ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [{"ruleId": "920420", "state": "Disabled"}]}, {
            "ruleGroupName": "REQUEST-930-APPLICATION-ATTACK-LFI", "rules": [{"ruleId": "930110", "state": "Disabled"}]}])
        self.assertTrue(baseline["policySettings"]["requestBodyCheck"])
        self.assertEqual(baseline["policySettings"]["mode"], "Prevention")
        self.assertEqual(len(baseline["policySettings"]["logScrubbing"]["scrubbingRules"]), 6)

    def test_chat_and_responses_keep_media_exclusions_in_their_manifest_profiles(self):
        manifest = json.loads((CORE / "apps/gateway/waf-data-exclusions.json").read_text())["profiles"]
        content_controls = json.loads((CORE / "apps/gateway/waf-json-content-controls.json").read_text())
        json_pattern = next(item["pattern"] for item in content_controls["formats"] if item["name"] == "json")
        selected = {profile["name"]: profile for profile in manifest
                    if profile["name"] in ("g-chat-completions", "g-responses")}
        parameters = self.parameters(publishedApiPaths=[path for profile in selected.values() for path in profile["paths"]])
        profiles = self.value("[variables('dataProfiles')]", parameters)
        properties = self.value("[variables('dataPolicyProperties')]", parameters)
        baseline = self.value(self.resource("g-base")["properties"], parameters)
        configured = {profile["name"]: value for profile, value in zip(profiles, properties)}
        for purpose, selector in (("g-chat-completions", "messages.content.image_url.url"),
                                  ("g-responses", "input.content.file_data")):
            with self.subTest(purpose=purpose):
                policy = configured[purpose]
                self.assertEqual(policy["managedRules"]["exclusions"],
                                 parameters["wafExclusions"] + selected[purpose]["exclusions"])
                expected_sets = copy.deepcopy(baseline["managedRules"]["managedRuleSets"])
                expected_sets[0]["ruleGroupOverrides"] = [{
                    "ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT",
                    "rules": [{"ruleId": "920420", "state": "Disabled"}]}]
                self.assertEqual(policy["managedRules"]["managedRuleSets"], expected_sets)
                self.assertEqual(policy["customRules"], baseline["customRules"] + [{
                    "name": "coreAiContentType", "priority": 2, "ruleType": "MatchRule",
                    "state": "Enabled", "action": "Block", "matchConditions": [{
                        "matchVariables": [{"variableName": "RequestHeaders", "selector": "Content-Type"}],
                        "operator": "Regex", "negationConditon": False,
                        "matchValues": [r"[\s\S]"], "transforms": ["Lowercase"]}, {
                        "matchVariables": [{"variableName": "RequestHeaders", "selector": "Content-Type"}],
                        "operator": "Regex", "negationConditon": True,
                        "matchValues": [json_pattern], "transforms": ["Lowercase"]}]}])
                self.assertEqual(policy["policySettings"], ai_body_settings(baseline["policySettings"]))
                values = [item for item in policy["managedRules"]["exclusions"] if item["selector"] == selector]
                groups = [{"ruleGroupName": "REQUEST-941-APPLICATION-ATTACK-XSS",
                           "rules": [{"ruleId": "941130"}, {"ruleId": "941170"}]}]
                if purpose == "g-chat-completions":
                    groups.append({"ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                                   "rules": [{"ruleId": "942340"}, {"ruleId": "942450"}]})
                self.assertEqual(values, [{"matchVariable": "RequestArgValues", "selectorMatchOperator": "Equals", "selector": selector,
                    "exclusionManagedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": groups}]}])

    def test_exact_path_and_default_keep_same_apim_backend_and_trusted_rewrite(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        for grpc_on in (False, True):
            parameters = self.parameters(grpcEnabled=grpc_on)
            properties = self.value(gateway["properties"], parameters)
            pathmap = properties["urlPathMaps"][0]["properties"]
            rule = self.protocol_path_rules(properties)["speech"]
            self.assertEqual(rule["paths"], parameters["speechRawBodyPaths"])
            self.assertFalse(any("*" in path for path in rule["paths"]))
            self.assertEqual(rule["backendAddressPool"], pathmap["defaultBackendAddressPool"])
            self.assertEqual(rule["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
            self.assertTrue(rule["firewallPolicy"]["id"].endswith("wafpol-g-speech-abc123"))
            self.assertTrue(properties["firewallPolicy"]["id"].endswith("wafpol-g-base-abc123"))
            self.assertEqual(properties["requestRoutingRules"][1]["properties"]["ruleType"], "PathBasedRouting")
            self.assertEqual(properties["backendAddressPools"][0]["properties"]["backendAddresses"], [{"ipAddress": "10.40.2.68"}])
            self.assertEqual(properties["backendHttpSettingsCollection"][0]["properties"]["port"], 80)
            self.assertIn("redirectConfiguration", properties["requestRoutingRules"][0]["properties"])
            if grpc_on:
                self.assertEqual(rule["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])
                self.assertEqual(properties["listeners"][0]["properties"]["protocol"], "Tcp")
            else:
                self.assertNotIn("rewriteRuleSet", rule)
                self.assertNotIn("defaultRewriteRuleSet", pathmap)

    def test_enterprise_policy_and_standard_sku_do_not_create_an_override(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        ssml = self.resource("g-speech")
        for changes in ({"existingWafPolicyId": "/enterprise-waf"}, {"skuName": "Standard_v2"}):
            parameters = self.parameters(**changes)
            self.assertFalse(self.value(ssml["condition"], parameters))
            properties = self.value(gateway["properties"], parameters)
            maps = {item["name"] for item in properties.get("urlPathMaps", [])}
            self.assertNotIn("pathmap-protocols", maps)
            self.assertEqual(maps, set())
            basic = properties["requestRoutingRules"][1]["properties"]
            self.assertEqual(basic["ruleType"], "Basic")
            self.assertIn("backendHttpSettings", basic)
            self.assertIn("rewriteRuleSet", basic)
            if "existingWafPolicyId" in changes:
                self.assertEqual(properties["firewallPolicy"]["id"], "/enterprise-waf")

    def test_gateway_identity_callback_excludes_only_code_value_from_three_observed_sql_rules(self):
        parameters = self.parameters()
        policy = self.resource("g-auth")
        base = self.resource("g-base")
        adjusted, baseline = self.value(policy["properties"], parameters), self.value(base["properties"], parameters)
        self.assertTrue(self.value(policy["condition"], parameters))
        exclusion = adjusted["managedRules"]["exclusions"].pop()
        self.assertEqual(adjusted, baseline)
        self.assertEqual(exclusion, {
            "matchVariable": "RequestArgValues", "selectorMatchOperator": "Equals", "selector": "code",
            "exclusionManagedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": [{
                "ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                "rules": [{"ruleId": "942430"}, {"ruleId": "942440"}, {"ruleId": "942450"}]}]}]})
        for changes in ({"existingWafPolicyId": "/enterprise-waf"}, {"skuName": "Standard_v2"}):
            self.assertFalse(self.value(policy["condition"], self.parameters(**changes)))

    def test_gateway_identity_callback_exists_without_other_protocol_paths_and_keeps_rewrite(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        for grpc_on in (False, True):
            parameters = self.parameters(speechRawBodyPaths=[], grpcEnabled=grpc_on)
            properties = self.value(gateway["properties"], parameters)
            pathmap = next(item for item in properties["urlPathMaps"] if item["name"] == "pathmap-protocols")["properties"]
            rules = self.protocol_path_rules(properties)
            self.assertEqual(set(rules), {"credential-identity-callback"})
            callback = rules["credential-identity-callback"]
            self.assertEqual(callback["paths"], ["/auth/connections/browser/identity-callback"])
            self.assertTrue(callback["firewallPolicy"]["id"].endswith("/wafpol-g-auth-abc123"))
            routing = next(item for item in properties["requestRoutingRules"] if item["name"] == "rule-https-to-apim")["properties"]
            self.assertEqual(routing["ruleType"], "PathBasedRouting")
            self.assertTrue(routing["urlPathMap"]["id"].endswith("/pathmap-protocols"))
            if grpc_on:
                self.assertEqual(callback["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])
            else:
                self.assertNotIn("rewriteRuleSet", callback)
                self.assertNotIn("defaultRewriteRuleSet", pathmap)

    def test_portal_callback_keeps_code_value_scoped_to_three_observed_sql_rules(self):
        parameters = self.parameters()
        policy = self.resource("p-auth")
        base = self.resource("g-base")
        adjusted, baseline = self.value(policy["properties"], parameters), self.value(base["properties"], parameters)
        self.assertEqual(adjusted["policySettings"], baseline["policySettings"])
        self.assertEqual(adjusted["customRules"], baseline["customRules"])
        self.assertEqual(adjusted["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
        exclusion = adjusted["managedRules"]["exclusions"].pop()
        for session in json.loads((CORE / "apps/portal/waf-session-exclusions.json").read_text(encoding="utf-8")):
            self.assertEqual(adjusted["managedRules"]["exclusions"].count(session), 1)
            adjusted["managedRules"]["exclusions"].remove(session)
        self.assertEqual(adjusted, baseline)
        self.assertEqual(exclusion["matchVariable"], "RequestArgValues")
        self.assertEqual(exclusion["selectorMatchOperator"], "Equals")
        self.assertEqual(exclusion["selector"], "code")
        rules = exclusion["exclusionManagedRuleSets"][0]
        self.assertEqual(rules["ruleSetType"], "OWASP")
        self.assertEqual(rules["ruleSetVersion"], "3.2")
        self.assertEqual(rules["ruleGroups"], [{"ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                         "rules": [{"ruleId": "942430"}, {"ruleId": "942440"}, {"ruleId": "942450"}]}])
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        properties = self.value(gateway["properties"], parameters)
        pathmap = next(item for item in properties["urlPathMaps"] if item["name"] == "pathmap-portal-auth")["properties"]
        path = next(item for item in pathmap["pathRules"] if item["name"] == "microsoft-auth-callback")
        self.assertEqual(path["properties"]["paths"], ["/api/auth/callback/microsoft"])
        self.assertTrue(path["properties"]["firewallPolicy"]["id"].endswith("/wafpol-p-auth-abc123"))
        self.assertEqual(path["properties"]["backendAddressPool"], pathmap["defaultBackendAddressPool"])
        self.assertEqual(path["properties"]["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
        self.assertTrue(pathmap["defaultBackendAddressPool"]["id"].endswith("bepool-portal"))
        self.assertFalse(self.value(policy["condition"], self.parameters(existingWafPolicyId="/enterprise-waf")))

    def test_waf_schema_has_only_fixed_purposes_and_manifest_data_policies(self):
        policy_type = "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies"
        resources = [item for item in self.resources if item["type"] == policy_type]
        fixed = {"g-base", "p-base", "g-auth", "p-auth", "g-speech", "g-document-upload"}
        fixed_names = {self.resource(purpose)["name"] for purpose in fixed}
        self.assertEqual({item["name"] for item in resources if "copy" not in item}, fixed_names)
        data = [item for item in resources if "copy" in item]
        self.assertEqual(len(data), 1)
        for obsolete in ("customMimePaths", "legacyMcpMessagePaths", "inlineImagePaths", "inlinePdfPaths", "inlineAudioPaths"):
            with self.subTest(parameter=obsolete):
                self.assertNotIn(obsolete, self.template["parameters"])
                self.assertNotIn(obsolete, self.module["properties"]["parameters"])
                self.assertNotIn(obsolete, self.main["variables"])
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        parameters = self.parameters(speechRawBodyPaths=[])
        properties = self.value(gateway["properties"], parameters)
        rules = self.protocol_path_rules(properties)
        self.assertEqual(set(rules), {"credential-identity-callback"})
        # Unpublished and custom paths retain the common default instead of creating
        # another policy or silently receiving a standard API's field exclusions.
        parameters = self.parameters(publishedApiPaths=["/custom/chat/completions", "/custom/messages/"])
        profiles = self.value("[variables('dataProfiles')]", parameters)
        self.assertFalse(any(profile["scope"] == "gateway" for profile in profiles))

    def test_main_derives_speech_paths_only_from_the_canonical_api_contract(self):
        from standard_apis import infrastructure_api_definitions
        original = json.loads((CORE / "apps/gateway/apim/apis/azure-speech-v1.json").read_text())
        expression = GatewayExpression(self.main["variables"]["speechRawBodyPaths"], self.main["variables"])
        def paths(api):
            configuration = {"apiDefinitions": [], "infrastructureApiDefinitions": infrastructure_api_definitions([api])}
            return sorted(expression.evaluate({"configuration": configuration}))
        self.assertEqual(paths(original), ["/models/azure-speech/v1/recognize", "/models/azure-speech/v1/synthesize"])
        for mutation in ("name", "method", "url", "kind", "format", "mime"):
            changed = copy.deepcopy(original)
            operation = next(item for item in changed["operations"] if item["name"] == "tts")
            constraint = changed["authorization"]["operationConstraints"]["tts"]
            if mutation == "name": changed["name"] = "custom-speech"
            elif mutation == "method": operation["method"] = "GET"
            elif mutation == "url": operation["urlTemplate"] = "/other"
            elif mutation == "kind": constraint["kind"] = "bodyModel"
            elif mutation == "format": constraint["bodyFormat"] = "binary"
            else: constraint["contentTypes"] = ["application/json"]
            with self.subTest(mutation=mutation):
                expected = [] if mutation == "name" else ["/models/azure-speech/v1/recognize"]
                self.assertEqual(paths(changed), expected)
        self.assertEqual(self.module["properties"]["parameters"]["speechRawBodyPaths"],
                         {"value": "[variables('speechRawBodyPaths')]"})
        self.assertNotIn("speechRawBodyPaths", self.main["parameters"])


if __name__ == "__main__":
    unittest.main()
