"""認証callback・SSML/WAV・MCP・インラインメディアの誤検知を、正規 URI に限って調整する。

コンパイルした ARM 式を合成入力で展開し、共通 WAF の保護と APIM への経路を
具体値で比較する。Azure 受入試験の代替ではなく、クラウドへの要求は実行しない。
"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import unittest

from arm_helpers import ArmExpression


CORE = Path(__file__).resolve().parents[1]


class GatewayExpression(ArmExpression):
    """既存のローカル ARM 評価器に、この入口定義で使う関数だけを足す。"""

    def __init__(self, expression, variables):
        super().__init__(expression)
        self.variables = variables

    def resolve(self, item, parameters, index=0):
        if isinstance(item, str) and item.startswith("[") and item.endswith("]"):
            return GatewayExpression(item, self.variables).evaluate(parameters, index)
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
        extra = {"variables", "and", "or", "not", "empty", "contains", "coalesce", "tryGet", "map", "filter",
                 "flatten", "shallowMerge", "resourceId", "true", "false", "length", "concat", "greater", "range"}
        if name not in extra:
            return super().evaluate(parameters, index, tree, bindings)
        values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
        if name == "variables":
            if values[0] in self.variables:
                return self.resolve(self.variables[values[0]], parameters, index)
            definition = next(item for item in self.variables.get("copy", []) if item["name"] == values[0])
            return [self.resolve(definition["input"], parameters, number)
                    for number in range(self.resolve(definition["count"], parameters))]
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
                          if "customMimePaths" in item.get("properties", {}).get("parameters", {}))
        cls.template = cls.module["properties"]["template"]
        resources = cls.template["resources"]
        cls.resources = list(resources.values() if isinstance(resources, dict) else resources)

    def parameters(self, **changes):
        result = {key: copy.deepcopy(value["defaultValue"]) for key, value in self.template["parameters"].items()
                  if "defaultValue" in value}
        result.update(productName="fixture", environmentName="test", resourceStem="fixture-test", location="japaneast",
            subnetId="/fixture-subnet", publicIpId="/fixture-public-ip", apimPrivateIp="10.40.2.68",
            apimHostName="apim-fixture.azure-api.net", publicHostName="api.example.com",
            keyVaultCertificateSecretId="https://kv-fixture.vault.azure.net/secrets/tls",
            certificateIdentityId="/fixture-mi", workspaceId="/fixture-workspace", auditStorageId="/fixture-audit",
            customMimePaths=["/models/tts/v1/synthesize"], grpcEnabled=True, l7IngressSecret="SYNTHETIC_PRIVATE_INPUT",
            wafExclusions=[{"matchVariable": "RequestHeaderValues", "selector": "X-Fixture", "selectorMatchOperator": "Equals"}],
            wafCustomRules=[{"name": "fixture-rule", "priority": 1, "action": "Block"}],
            additionalWafLogScrubbingRules=[{"state": "Enabled", "matchVariable": "RequestHeaderNames", "selector": "X-Fixture", "selectorMatchOperator": "Equals"}])
        result.update(portalHostName="portal.example.com", portalBackendIp="10.40.2.200",
            portalBackendHost="portal.fixture.azurecontainerapps.io")
        result.update(changes)
        return result

    def value(self, expression, parameters):
        return GatewayExpression(expression, self.template["variables"]).evaluate(parameters)

    def resource(self, suffix):
        return next(item for item in self.resources if item["name"] == suffix)

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

    def test_merged_media_profiles_have_one_path_and_both_scoped_exclusions(self):
        parameters = self.parameters(inlineImagePaths=["/models/openai/v1/chat/completions"],
            inlineAudioPaths=["/models/openai/v1/chat/completions"])
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        properties = self.value(gateway["properties"], parameters)
        rules = self.protocol_path_rules(properties)
        self.assertIn("inline-media-data", rules)
        self.assertNotIn("inline-image-data", rules)
        self.assertNotIn("inline-audio-data", rules)
        self.assertEqual(rules["inline-media-data"]["paths"], ["/models/openai/v1/chat/completions"])
        policy = self.resource("[format('wafpol-agw-{0}-inline-media', parameters('resourceStem'))]")
        configured = self.value(policy["properties"], parameters)
        selectors = {item["selector"] for item in configured["managedRules"]["exclusions"]}
        self.assertIn("messages.content.image_url.url", selectors)
        self.assertIn("messages.content.input_audio.data", selectors)
        self.assertTrue(configured["policySettings"]["requestBodyCheck"])

    def test_ssml_policy_preserves_every_baseline_setting_and_changes_only_rule_920420(self):
        parameters = self.parameters()
        common = self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")
        ssml = self.resource("[format('wafpol-agw-{0}-custom-mime', parameters('resourceStem'))]")
        baseline = self.value(common["properties"], parameters)
        adjusted = self.value(ssml["properties"], parameters)
        self.assertEqual(adjusted["policySettings"], baseline["policySettings"])
        self.assertEqual(adjusted["customRules"], parameters["wafCustomRules"])
        self.assertEqual(adjusted["managedRules"]["exclusions"], parameters["wafExclusions"])
        group = adjusted["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"]
        self.assertEqual(group, [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [{"ruleId": "920420", "state": "Disabled"}]}])
        adjusted["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"] = []
        self.assertEqual(adjusted, baseline)
        self.assertTrue(baseline["policySettings"]["requestBodyCheck"])
        self.assertEqual(baseline["policySettings"]["mode"], "Prevention")
        self.assertEqual(len(baseline["policySettings"]["logScrubbing"]["scrubbingRules"]), 6)

    def test_legacy_excludes_only_the_session_id_key_from_rule_943120(self):
        parameters = self.parameters(legacyMcpMessagePaths=["/tools/custom-legacy/messages/"])
        baseline = self.value(self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")["properties"], parameters)
        legacy = self.resource("[format('wafpol-agw-{0}-mcp-legacy', parameters('resourceStem'))]")
        adjusted = self.value(legacy["properties"], parameters)
        self.assertEqual(adjusted["policySettings"], baseline["policySettings"])
        self.assertEqual(adjusted["customRules"], baseline["customRules"])
        self.assertEqual(adjusted["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
        self.assertEqual(adjusted["managedRules"]["exclusions"][:-1], baseline["managedRules"]["exclusions"])
        self.assertEqual(adjusted["managedRules"]["exclusions"][-1], {
            "matchVariable": "RequestArgKeys", "selectorMatchOperator": "Equals", "selector": "session_id",
            "exclusionManagedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": [{
                "ruleGroupName": "REQUEST-943-APPLICATION-ATTACK-SESSION-FIXATION", "rules": [{"ruleId": "943120"}]}]}]})
        adjusted["managedRules"]["exclusions"].pop()
        self.assertEqual(adjusted, baseline)

    def test_inline_values_exclude_only_the_observed_json_field_and_two_xss_rules(self):
        parameters = self.parameters(inlineImagePaths=["/enterprise/multi/chat/completions"],
                                     inlinePdfPaths=["/enterprise/multi/responses"])
        baseline = self.value(self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")["properties"], parameters)
        for suffix, selector in (("inline-image", "messages.content.image_url.url"), ("inline-pdf", "input.content.file_data")):
            resource = self.resource("[format('wafpol-agw-{0}-" + suffix + "', parameters('resourceStem'))]")
            adjusted = self.value(resource["properties"], parameters)
            self.assertTrue(self.value(resource["condition"], parameters))
            self.assertEqual(adjusted["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
            self.assertEqual(adjusted["managedRules"]["exclusions"][-1], {
                "matchVariable": "RequestArgValues", "selectorMatchOperator": "Equals", "selector": selector,
                "exclusionManagedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": [{
                    "ruleGroupName": "REQUEST-941-APPLICATION-ATTACK-XSS", "rules": [{"ruleId": "941130"}, {"ruleId": "941170"}]}]}]})
            adjusted["managedRules"]["exclusions"].pop()
            self.assertEqual(adjusted, baseline)

    def test_inline_audio_excludes_only_the_base64_value_from_rule_942450(self):
        """音声以外の入力や他SQLi規則を除外せず、共通の本文検査をそのまま残す。"""
        parameters = self.parameters(inlineAudioPaths=["/enterprise/audio/v1/chat/completions"])
        baseline = self.value(self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")["properties"], parameters)
        resource = self.resource("[format('wafpol-agw-{0}-inline-audio', parameters('resourceStem'))]")
        adjusted = self.value(resource["properties"], parameters)
        self.assertTrue(self.value(resource["condition"], parameters))
        self.assertEqual(adjusted["managedRules"]["exclusions"][-1], {
            "matchVariable": "RequestArgNames", "selectorMatchOperator": "Equals",
            "selector": "messages.content.input_audio.data",
            "exclusionManagedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": [{
                "ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI", "rules": [{"ruleId": "942450"}]}]}]})
        adjusted["managedRules"]["exclusions"].pop()
        self.assertEqual(adjusted, baseline)
        self.assertTrue(adjusted["policySettings"]["requestBodyCheck"])
        for changes in ({"inlineAudioPaths": []}, {"existingWafPolicyId": "/enterprise-waf"}, {"skuName": "Standard_v2"}):
            selected = self.parameters(**{"inlineAudioPaths": parameters["inlineAudioPaths"], **changes})
            self.assertFalse(self.value(resource["condition"], selected))


    def test_exact_path_and_default_keep_same_apim_backend_and_trusted_rewrite(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        for grpc_on in (False, True):
            parameters = self.parameters(grpcEnabled=grpc_on)
            properties = self.value(gateway["properties"], parameters)
            pathmap = properties["urlPathMaps"][0]["properties"]
            rule = self.protocol_path_rules(properties)["custom-content-type"]
            self.assertEqual(rule["paths"], ["/models/tts/v1/synthesize"])
            self.assertFalse(any("*" in path for path in rule["paths"]))
            self.assertEqual(rule["backendAddressPool"], pathmap["defaultBackendAddressPool"])
            self.assertEqual(rule["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
            self.assertTrue(rule["firewallPolicy"]["id"].endswith("wafpol-agw-fixture-test-custom-mime"))
            self.assertTrue(properties["firewallPolicy"]["id"].endswith("wafpol-agw-fixture-test"))
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
        ssml = self.resource("[format('wafpol-agw-{0}-custom-mime', parameters('resourceStem'))]")
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
        policy = self.resource("[format('wafpol-agw-{0}-gateway-auth', parameters('resourceStem'))]")
        base = self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")
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
            parameters = self.parameters(customMimePaths=[], grpcEnabled=grpc_on)
            properties = self.value(gateway["properties"], parameters)
            pathmap = next(item for item in properties["urlPathMaps"] if item["name"] == "pathmap-protocols")["properties"]
            rules = self.protocol_path_rules(properties)
            self.assertEqual(set(rules), {"credential-identity-callback"})
            callback = rules["credential-identity-callback"]
            self.assertEqual(callback["paths"], ["/auth/connections/browser/identity-callback"])
            self.assertTrue(callback["firewallPolicy"]["id"].endswith("-gateway-auth"))
            routing = next(item for item in properties["requestRoutingRules"] if item["name"] == "rule-https-to-apim")["properties"]
            self.assertEqual(routing["ruleType"], "PathBasedRouting")
            self.assertTrue(routing["urlPathMap"]["id"].endswith("/pathmap-protocols"))
            if grpc_on:
                self.assertEqual(callback["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])
            else:
                self.assertNotIn("rewriteRuleSet", callback)
                self.assertNotIn("defaultRewriteRuleSet", pathmap)

    def test_portal_callback_excludes_only_code_value_from_two_observed_sql_rules(self):
        parameters = self.parameters()
        policy = self.resource("[format('wafpol-agw-{0}-portal-auth', parameters('resourceStem'))]")
        base = self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")
        adjusted, baseline = self.value(policy["properties"], parameters), self.value(base["properties"], parameters)
        self.assertEqual(adjusted["policySettings"], baseline["policySettings"])
        self.assertEqual(adjusted["customRules"], baseline["customRules"])
        self.assertEqual(adjusted["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
        exclusion = adjusted["managedRules"]["exclusions"].pop()
        self.assertEqual(adjusted, baseline)
        self.assertEqual(exclusion["matchVariable"], "RequestArgValues")
        self.assertEqual(exclusion["selectorMatchOperator"], "Equals")
        self.assertEqual(exclusion["selector"], "code")
        rules = exclusion["exclusionManagedRuleSets"][0]
        self.assertEqual(rules["ruleSetType"], "OWASP")
        self.assertEqual(rules["ruleSetVersion"], "3.2")
        self.assertEqual(rules["ruleGroups"], [{"ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                         "rules": [{"ruleId": "942430"}, {"ruleId": "942440"}]}])
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        properties = self.value(gateway["properties"], parameters)
        pathmap = next(item for item in properties["urlPathMaps"] if item["name"] == "pathmap-portal-auth")["properties"]
        path = next(item for item in pathmap["pathRules"] if item["name"] == "microsoft-auth-callback")
        self.assertEqual(path["properties"]["paths"], ["/api/auth/callback/microsoft"])
        self.assertTrue(path["properties"]["firewallPolicy"]["id"].endswith("-portal-auth"))
        self.assertEqual(path["properties"]["backendAddressPool"], pathmap["defaultBackendAddressPool"])
        self.assertEqual(path["properties"]["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
        self.assertTrue(pathmap["defaultBackendAddressPool"]["id"].endswith("bepool-portal"))
        self.assertFalse(self.value(policy["condition"], self.parameters(existingWafPolicyId="/enterprise-waf")))

    def test_legacy_exact_path_keeps_default_and_custom_mime_routing_independent(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        for mime_paths in ([], ["/models/stt/v1/recognize"]):
            parameters = self.parameters(customMimePaths=mime_paths,
                legacyMcpMessagePaths=["/tools/custom-legacy/messages/"])
            properties = self.value(gateway["properties"], parameters)
            pathmap = properties["urlPathMaps"][0]["properties"]
            rules = self.protocol_path_rules(properties)
            self.assertEqual(set(rules), {"legacy-mcp-session", "credential-identity-callback"} | ({"custom-content-type"} if mime_paths else set()))
            legacy = rules["legacy-mcp-session"]
            self.assertEqual(legacy["paths"], ["/tools/custom-legacy/messages/"])
            self.assertTrue(legacy["firewallPolicy"]["id"].endswith("wafpol-agw-fixture-test-mcp-legacy"))
            for rule in rules.values():
                self.assertEqual(rule["backendAddressPool"], pathmap["defaultBackendAddressPool"])
                self.assertEqual(rule["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
                self.assertEqual(rule["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])
            self.assertTrue(properties["firewallPolicy"]["id"].endswith("wafpol-agw-fixture-test"))
        parameters = self.parameters(legacyMcpMessagePaths=["/tools/custom-legacy/messages/"], existingWafPolicyId="/enterprise-waf")
        properties = self.value(gateway["properties"], parameters)
        self.assertNotIn("urlPathMaps", properties)
        self.assertEqual(properties["firewallPolicy"]["id"], "/enterprise-waf")
        for suffix in ("custom-mime", "mcp-legacy"):
            self.assertFalse(self.value(self.resource("[format('wafpol-agw-{0}-" + suffix + "', parameters('resourceStem'))]")["condition"], parameters))

    def test_all_five_path_policies_keep_the_baseline_routing_and_enterprise_override(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        additions = {"legacyMcpMessagePaths": ["/tools/custom/messages/"],
                     "inlineImagePaths": ["/multi/chat/completions"], "inlinePdfPaths": ["/multi/responses"],
                     "inlineAudioPaths": ["/audio/v1/chat/completions"]}
        parameters = self.parameters(**additions)
        properties = self.value(gateway["properties"], parameters)
        pathmap = properties["urlPathMaps"][0]["properties"]
        rules = self.protocol_path_rules(properties)
        self.assertEqual(set(rules), {"custom-content-type", "legacy-mcp-session", "inline-image-data", "inline-pdf-data", "inline-audio-data", "credential-identity-callback"})
        self.assertEqual(rules["inline-image-data"]["paths"], additions["inlineImagePaths"])
        self.assertEqual(rules["inline-pdf-data"]["paths"], additions["inlinePdfPaths"])
        self.assertEqual(rules["inline-audio-data"]["paths"], additions["inlineAudioPaths"])
        self.assertTrue(rules["inline-audio-data"]["firewallPolicy"]["id"].endswith("-inline-audio"))
        self.assertFalse(any("*" in path for path in rules["inline-audio-data"]["paths"]))
        for rule in rules.values():
            self.assertEqual(rule["backendAddressPool"], pathmap["defaultBackendAddressPool"])
            self.assertEqual(rule["backendHttpSettings"], pathmap["defaultBackendHttpSettings"])
            self.assertEqual(rule["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])
        self.assertEqual(properties["listeners"][0]["properties"]["protocol"], "Tcp")
        self.assertIn("redirectConfiguration", properties["requestRoutingRules"][0]["properties"])
        for changes in ({"existingWafPolicyId": "/enterprise-waf"}, {"skuName": "Standard_v2"}):
            parameters = self.parameters(**additions, **changes)
            properties = self.value(gateway["properties"], parameters)
            self.assertNotIn("urlPathMaps", properties)
            for suffix in ("custom-mime", "mcp-legacy", "inline-image", "inline-pdf", "inline-audio"):
                resource = self.resource("[format('wafpol-agw-{0}-" + suffix + "', parameters('resourceStem'))]")
                self.assertFalse(self.value(resource["condition"], parameters))

    def test_main_audio_path_requires_the_explicit_post_json_contract(self):
        api = {"path": "enterprise/audio/v2", "operations": [
            {"name": "audio-chat", "method": "POST", "urlTemplate": "/chat/completions"}],
            "authorization": {"operationConstraints": {"audio-chat": {"kind": "bodyModel", "bodyFormat": "json"}}}}
        expression = GatewayExpression(self.main["variables"]["inlineAudioPaths"], self.main["variables"])
        self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": [api]}}),
                         ["/enterprise/audio/v2/chat/completions"])
        for mutation in ("name", "method", "url", "kind", "format", "wildcard", "template", "query"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(api)
                operation = changed["operations"][0]
                constraint = changed["authorization"]["operationConstraints"]["audio-chat"]
                if mutation == "name":
                    operation["name"] = "chat-completions"
                    changed["authorization"]["operationConstraints"][operation["name"]] = constraint
                if mutation == "method": operation["method"] = "GET"
                if mutation == "url": operation["urlTemplate"] = "/other"
                if mutation == "kind": constraint["kind"] = "registeredOperation"
                if mutation == "format": constraint["bodyFormat"] = "none"
                if mutation == "wildcard": changed["path"] = "unsafe/*"
                if mutation == "template": changed["path"] = "unsafe/{id}"
                if mutation == "query": changed["path"] = "unsafe/?name=value"
                self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": [changed]}}), [])
        self.assertEqual(self.module["properties"]["parameters"]["inlineAudioPaths"],
                         {"value": "[variables('inlineAudioPaths')]"})
        self.assertNotIn("inlineAudioPaths", self.main["parameters"])

    def test_main_derives_custom_prefixes_and_exact_post_ssml_operations_without_public_params(self):
        operations = [{"name": "speak", "method": "POST", "urlTemplate": "/synthesize"},
                      {"name": "listen", "method": "POST", "urlTemplate": "/recognize"},
                      {"name": "get", "method": "GET", "urlTemplate": "/get"},
                      {"name": "json", "method": "POST", "urlTemplate": "/json"},
                      {"name": "variable", "method": "POST", "urlTemplate": "/{voice}"},
                      {"name": "query", "method": "POST", "urlTemplate": "/speak?version={version}"},
                      {"name": "wildcard", "method": "POST", "urlTemplate": "/*"}]
        api = {"path": "enterprise/audio/v2", "operations": operations,
               "authorization": {"operationConstraints": {op["name"]: {"contentTypes": ["application/ssml+xml"]}
                                                            for op in operations}}}
        api["authorization"]["operationConstraints"]["json"] = {"contentTypes": ["application/json"]}
        api["authorization"]["operationConstraints"]["listen"] = {"contentTypes": ["audio/wav"]}
        expression = GatewayExpression(self.main["variables"]["customMimePaths"], self.main["variables"])
        self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": [api]}}, 0), ["/enterprise/audio/v2/synthesize", "/enterprise/audio/v2/recognize"])
        self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": []}}, 0), [])
        self.assertEqual(self.module["properties"]["parameters"]["customMimePaths"], {"value": "[variables('customMimePaths')]"})
        self.assertNotIn("customMimePaths", self.main["parameters"])

    def test_main_derives_only_canonical_native_legacy_paths_without_public_params(self):
        native = {"apiType": "mcp", "mcpProxy": {"transport": "sse", "endpoints": [
            {"name": "sse", "uriTemplate": "/legacy/sse"},
            {"name": "message", "uriTemplate": "/legacy/messages/"}]}, "path": "enterprise/legacy/v2"}
        stream = {**native, "mcpProxy": {"transport": "streamable"}}
        definitions = [{"path": "ordinary/http"}, native, stream,
                       {**native, "path": "unsafe/*"}, {**native, "path": "unsafe/{version}"},
                       {**native, "path": "unsafe?query"}]
        expression = GatewayExpression(self.main["variables"]["legacyMcpMessagePaths"], self.main["variables"])
        self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": definitions}}), ["/enterprise/legacy/v2/legacy/messages/"])
        self.assertEqual(self.module["properties"]["parameters"]["legacyMcpMessagePaths"], {"value": "[variables('legacyMcpMessagePaths')]"})
        self.assertNotIn("legacyMcpMessagePaths", self.main["parameters"])

    def test_inline_paths_require_the_explicit_operation_contract_and_fixed_url(self):
        api = {"path": "enterprise/multimodal/v3", "operations": [
            {"name": "vision", "method": "POST", "urlTemplate": "/chat/completions"},
            {"name": "pdf", "method": "POST", "urlTemplate": "/responses"}],
            "authorization": {"operationConstraints": {"vision": {"kind": "bodyModel", "bodyFormat": "json"},
                                                      "pdf": {"kind": "statelessResponses", "bodyFormat": "json"}}}}
        for variable, operation, expected in (("inlineImagePaths", "vision", "/chat/completions"), ("inlinePdfPaths", "pdf", "/responses")):
            expression = GatewayExpression(self.main["variables"][variable], self.main["variables"])
            self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": [api]}}), ["/enterprise/multimodal/v3" + expected])
            for mutation in ("method", "url", "kind", "format", "prefix"):
                changed = copy.deepcopy(api)
                target = next(op for op in changed["operations"] if op["name"] == operation)
                constraint = changed["authorization"]["operationConstraints"][operation]
                if mutation == "method": target["method"] = "GET"
                if mutation == "url": target["urlTemplate"] = "/unrelated"
                if mutation == "kind": constraint["kind"] = "registeredOperation"
                if mutation == "format": constraint["bodyFormat"] = "none"
                if mutation == "prefix": changed["path"] = "unsafe/*"
                self.assertEqual(expression.evaluate({"configuration": {"apiDefinitions": [changed]}}), [])
            self.assertEqual(self.module["properties"]["parameters"][variable], {"value": "[variables('" + variable + "')]"})
            self.assertNotIn(variable, self.main["parameters"])


if __name__ == "__main__":
    unittest.main()
