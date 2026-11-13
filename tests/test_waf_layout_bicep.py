"""用途別WAFの個数・経路と共通保護を、コンパイル済みARMで確認する。"""
import copy
import json
from pathlib import Path
import unittest

from bicep_helpers import compile_bicep
from test_ssml_waf import GatewayExpression
from deployment.helpers.waf_body import ai_body_settings
from waf_policy import desired_policy


ROOT = Path(__file__).resolve().parents[1]
WAF_TYPE = "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies"
IDENTIFIER = "abc123"  # 実環境から独立した合成識別子。
SPEECH_PATHS = ["/models/azure-speech/v1/recognize", "/models/azure-speech/v1/synthesize"]
DOCUMENT_PATH = "/tools/documents/v1/analyze"
DATA_PURPOSES = {
    "g-chat-completions", "g-responses", "g-embeddings", "g-rerank", "g-decision",
    "g-images", "g-transcribe", "g-realtime", "g-live", "g-pii-redact",
    "g-document-status", "g-realtime-ticket",
    "p-generate", "p-embeddings", "p-rerank", "p-decision", "p-images", "p-speech",
    "p-realtime", "p-document-upload", "p-pii-redact",
    "p-document-status",
}


class LayoutExpression(GatewayExpression):
    """ARMの不変variablesを入力ごとに再利用し、大きなmanifestの再展開を防ぐ。"""
    def __init__(self, expression, variables, cache):
        super().__init__(expression, variables)
        self.cache = cache

    def resolve(self, value, parameters, index=0):
        if isinstance(value, str) and value.startswith("[["):
            return value[1:]  # ARM escapes a literal leading bracket.
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return LayoutExpression(value, self.variables, self.cache).evaluate(parameters, index)
        return super().resolve(value, parameters, index)

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        selected = self.tree if tree is None else tree
        if selected[:2] == ("call", "variables"):
            name = self.evaluate(parameters, index, selected[2][0], bindings)
            if name not in self.cache:
                self.cache[name] = super().evaluate(parameters, index, selected, bindings)
            return self.cache[name]
        return super().evaluate(parameters, index, tree, bindings)


class WafLayoutBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("infra/modules/application-gateway.bicep")
        cls.main = compile_bicep("infra/main.bicep")
        items = cls.template["resources"]
        cls.resources = list(items.values()) if isinstance(items, dict) else items
        cls.profiles = json.loads((ROOT / "apps/gateway/waf-data-exclusions.json").read_text())["profiles"]
        cls.cookie = json.loads((ROOT / "apps/portal/waf-session-exclusions.json").read_text())[0]
        cls.json_controls = json.loads((ROOT / "apps/gateway/waf-json-content-controls.json").read_text())
        cls.content_profiles = {p["name"]: p for p in cls.json_controls["profiles"]}

    def parameters(self, **changes):
        values = {key: copy.deepcopy(value["defaultValue"])
                  for key, value in self.template["parameters"].items() if "defaultValue" in value}
        values.update(
            productName="fixture", environmentName="test", resourceStem="test-fixture",
            wafPolicyIdentifier=IDENTIFIER, location="japaneast", diagnosticSettingProperties={},
            subnetId="/fixture-subnet", publicIpId="/fixture-public-ip", publicIpAddress="192.0.2.10",
            apimPrivateIp="192.0.2.11", apimHostName="apim.example.com",
            publicHostName="gateway.example.com", portalHostName="portal.example.com",
            portalBackendIp="192.0.2.12", portalBackendHost="backend.example.com",
            keyVaultCertificateSecretId="https://vault.example.com/secrets/tls",
            certificateIdentityId="/fixture-identity", grpcEnabled=True,
            speechRawBodyPaths=SPEECH_PATHS,
            documentUploadPaths=[DOCUMENT_PATH],
            publishedApiPaths=[path for profile in self.profiles if profile["scope"] == "gateway"
                               for path in profile["paths"]] + SPEECH_PATHS + [DOCUMENT_PATH],
            wafMaxRequestBodySizeInKb=2000, wafRequestBodyInspectLimitInKb=2000, wafFileUploadLimitInMb=4,
            wafCustomRules=[{"name": "fixture-extra", "priority": 1, "action": "Block"}],
            wafExclusions=[{"matchVariable": "RequestHeaderValues", "selectorMatchOperator": "Equals",
                            "selector": "X-Fixture"}],
        )
        values.update(changes)
        return values

    def resolve(self, value, parameters, index=0):
        caches = self.__dict__.setdefault("variable_caches", {})
        cache = caches.setdefault(json.dumps(parameters, sort_keys=True), {})
        return LayoutExpression("[true()]", self.template["variables"], cache).resolve(value, parameters, index)

    def deployed(self, parameters, resource_type):
        result = []
        for resource in self.resources:
            if resource["type"] != resource_type:
                continue
            count = self.resolve(resource["copy"]["count"], parameters) if "copy" in resource else 1
            for index in range(count):
                if not self.resolve(resource.get("condition", True), parameters, index):
                    continue
                result.append({"name": self.resolve(resource["name"], parameters, index),
                               "properties": self.resolve(resource["properties"], parameters, index)})
        return result

    def policies(self, parameters):
        resources = self.deployed(parameters, WAF_TYPE)
        self.assertEqual(len(resources), len({item["name"] for item in resources}), "WAF名の重複作成")
        return {item["name"]: item["properties"] for item in resources}

    def gateway(self, parameters):
        return self.deployed(parameters, "Microsoft.Network/applicationGateways")[0]["properties"]

    def name(self, purpose):
        return f"wafpol-{purpose}-{IDENTIFIER}"

    def test_identifier_contract_is_shared_with_apim_and_standard_layout_has_28_policies(self):
        identifier = self.template["parameters"]["wafPolicyIdentifier"]
        self.assertEqual((identifier["minLength"], identifier["maxLength"]), (6, 6))
        self.assertNotIn("defaultValue", identifier)
        modules = self.main["resources"]
        modules = list(modules.values()) if isinstance(modules, dict) else modules
        module = next(item for item in modules if "wafPolicyIdentifier" in item.get("properties", {}).get("parameters", {}))
        wiring = module["properties"]["parameters"]["wafPolicyIdentifier"]["value"]
        self.assertEqual(GatewayExpression(wiring, self.main["variables"]).evaluate({
            "configuration": {"services": {"apimInstanceId": IDENTIFIER}}}), IDENTIFIER)
        self.assertEqual({profile["name"] for profile in self.profiles}, DATA_PURPOSES)
        policies = self.policies(self.parameters())
        expected = DATA_PURPOSES | {"g-base", "p-base", "p-auth", "g-auth", "g-speech", "g-document-upload"}
        self.assertEqual(set(policies), {self.name(purpose) for purpose in expected})
        self.assertEqual(sum(name.startswith("wafpol-g-") for name in policies), 16)
        self.assertEqual(sum(name.startswith("wafpol-p-") for name in policies), 12)

    def test_merged_profiles_bind_each_exact_path_once_to_the_complete_policy(self):
        parameters = self.parameters()
        gateway = self.gateway(parameters)
        maps = {item["name"]: item["properties"] for item in gateway["urlPathMaps"]}
        for scope, mapping_name, backend in (("gateway", "pathmap-protocols", "apim"),
                                              ("portal", "pathmap-portal-auth", "portal")):
            mapping = maps[mapping_name]
            all_paths = [path for rule in mapping["pathRules"] for path in rule["properties"]["paths"]]
            self.assertEqual(len(all_paths), len(set(all_paths)))
            for profile in (item for item in self.profiles if item["scope"] == scope):
                selected = [item for item in mapping["pathRules"] if item["name"] == "waf-data-" + profile["name"]]
                self.assertEqual(len(selected), 1)
                rule = selected[0]["properties"]
                self.assertEqual(set(rule["paths"]), set(profile["paths"]))
                self.assertTrue(rule["firewallPolicy"]["id"].endswith("/" + self.name(profile["name"])))
                self.assertTrue(rule["backendAddressPool"]["id"].endswith("/bepool-" + backend))
                self.assertTrue(rule["backendHttpSettings"]["id"].endswith("/behttp-" + backend))
                self.assertEqual("rewriteRuleSet" in rule, scope == "gateway")
        counts = {profile["name"]: len(profile["paths"]) for profile in self.profiles}
        for name, count in (("g-images", 6), ("g-embeddings", 2), ("g-pii-redact", 2), ("p-pii-redact", 2)):
            self.assertEqual(counts[name], count)
        listener = next(item["properties"] for item in gateway["httpListeners"] if item["name"] == "portal-https")
        self.assertTrue(listener["firewallPolicy"]["id"].endswith("/" + self.name("p-base")))
        self.assertTrue(gateway["firewallPolicy"]["id"].endswith("/" + self.name("g-base")))

    def test_every_portal_policy_keeps_cookie_and_every_data_policy_keeps_common_protection(self):
        parameters = self.parameters(wafMode="Detection")
        policies = self.policies(parameters)
        baseline = policies[self.name("g-base")]
        for name, policy in policies.items():
            self.assertIn(parameters["wafExclusions"][0], policy["managedRules"]["exclusions"])
            for field in ("state", "requestBodyCheck", "logScrubbing"):
                self.assertEqual(policy["policySettings"][field], baseline["policySettings"][field])
            if name.startswith("wafpol-p-"):
                self.assertEqual(policy["managedRules"]["exclusions"].count(self.cookie), 1)
                if name not in {self.name(p) for p in set(self.content_profiles) | {"p-document-upload"}}:
                    self.assertEqual(policy["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
            if name in {self.name(purpose) for purpose in (DATA_PURPOSES - {"g-document-status", "g-realtime-ticket", "p-document-status"}) | {"g-speech", "g-document-upload"}}:
                self.assertEqual(policy["policySettings"], ai_body_settings({**baseline["policySettings"], "mode": "Prevention"}))
            elif name in {self.name(purpose) for purpose in {"g-document-status", "g-realtime-ticket", "p-document-status"}}:
                self.assertEqual(policy["policySettings"], {**baseline["policySettings"], "mode": "Prevention"})
            else:
                self.assertEqual(policy["policySettings"], baseline["policySettings"])
            if name in {self.name(purpose) for purpose in DATA_PURPOSES - {"p-document-upload"} - set(self.content_profiles)}:
                self.assertEqual(policy["customRules"], parameters["wafCustomRules"])
        self.assertNotIn(self.cookie, baseline["managedRules"]["exclusions"])
        for purpose in ("p-base", "p-auth", "g-auth"):
            self.assertEqual(policies[self.name(purpose)]["policySettings"]["mode"], "Detection")

    def test_ai_content_controls_match_python_and_change_only_the_18_policies(self):
        custom = [{"name": "fixture-one", "priority": 1, "action": "Block"},
                  {"name": "fixture-three", "priority": 3, "action": "Block"}]
        policies = self.policies(self.parameters(wafCustomRules=custom))
        baseline = policies[self.name("g-base")]
        source = {"location": "japaneast", "properties": copy.deepcopy(baseline)}
        owned_name = self.json_controls["rule"]["name"]
        self.assertEqual(set(self.content_profiles), DATA_PURPOSES - {"g-transcribe", "p-document-upload", "g-document-status", "g-realtime-ticket"})
        for purpose in self.content_profiles:
            profile = next(p for p in self.profiles if p["name"] == purpose)
            actual = policies[self.name(purpose)]
            expected = desired_policy(source, profile, {})["properties"]
            self.assertEqual(actual, expected)
            self.assertEqual(actual["customRules"][:-1], custom)
            rule, = [r for r in actual["customRules"] if r["name"] == owned_name]
            self.assertEqual(rule["priority"], 2)
            self.assertEqual(rule["action"], "Block")
            present, condition = rule["matchConditions"]
            self.assertEqual(present["matchValues"], [r"[\s\S]"])
            self.assertFalse(present["negationConditon"])
            for item in (present, condition):
                self.assertEqual(item["transforms"], ["Lowercase"])
            self.assertTrue(condition["negationConditon"])
            self.assertEqual(condition["matchValues"], [next(f["pattern"] for f in self.json_controls["formats"]
                                                            if f["name"] == self.content_profiles[purpose]["format"])])
            self.assertEqual(actual["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"], [{
                "ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT",
                "rules": [{"ruleId": "920420", "state": "Disabled"}],
            }])
        for name, value in policies.items():
            if name not in {self.name(purpose) for purpose in self.content_profiles}:
                self.assertFalse(any(r["name"] == owned_name for r in value["customRules"]))

    def test_ai_content_controls_follow_each_nonempty_published_gateway_subset(self):
        for profile in (p for p in self.profiles if p["scope"] == "gateway" and p["name"] in self.content_profiles):
            for path in profile["paths"]:
                with self.subTest(profile=profile["name"], publishedPath=path):
                    parameters = self.parameters(publishedApiPaths=[path])
                    policies = self.policies(parameters)
                    source = {"location": "japaneast", "properties": copy.deepcopy(policies[self.name("g-base")])}
                    selected = {**profile, "paths": [path]}
                    self.assertEqual(policies[self.name(profile["name"])], desired_policy(source, selected, {})["properties"])
                    gateway = self.gateway(parameters)
                    mapping = next(m["properties"] for m in gateway["urlPathMaps"] if m["name"] == "pathmap-protocols")
                    route, = [r for r in mapping["pathRules"] if r["name"] == "waf-data-" + profile["name"]]
                    self.assertEqual(route["properties"]["paths"], [path])

    def test_single_speech_policy_preserves_raw_and_document_controls(self):
        parameters = self.parameters()
        policies = self.policies(parameters)
        base = policies[self.name("g-base")]
        expected_overrides = [
            {"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT",
             "rules": [{"ruleId": "920420", "state": "Disabled"}]},
            {"ruleGroupName": "REQUEST-930-APPLICATION-ATTACK-LFI",
             "rules": [{"ruleId": "930110", "state": "Disabled"}]},
        ]
        for purpose in ("g-speech", "g-document-upload", "p-document-upload"):
            raw = policies[self.name(purpose)]
            self.assertEqual(raw["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"], expected_overrides)
            if purpose != "p-document-upload":
                self.assertEqual(raw["managedRules"]["exclusions"], base["managedRules"]["exclusions"])
            self.assertEqual(raw["customRules"][:1], parameters["wafCustomRules"])
            blocks = raw["customRules"][1:]
            self.assertEqual(len(blocks), 4)
            self.assertEqual({r["action"] for r in blocks}, {"Block"})
            self.assertEqual({r["matchConditions"][0]["matchVariables"][0]["variableName"] for r in blocks},
                             {"RequestUri", "QueryString", "RequestHeaders", "RequestCookies"})
            self.assertEqual(len({r["priority"] for r in raw["customRules"]}), 5)
        self.assertEqual(policies[self.name("g-speech")]["policySettings"], ai_body_settings(base["policySettings"]))
        self.assertEqual(policies[self.name("g-document-upload")]["policySettings"], ai_body_settings(base["policySettings"]))
        gateway = self.gateway(parameters)
        rules = next(m["properties"]["pathRules"] for m in gateway["urlPathMaps"] if m["name"] == "pathmap-protocols")
        speech, = [r for r in rules if r["name"] == "speech"]
        self.assertEqual(set(speech["properties"]["paths"]), set(SPEECH_PATHS))
        self.assertTrue(speech["properties"]["firewallPolicy"]["id"].endswith("/" + self.name("g-speech")))
        absent = self.policies(self.parameters(speechRawBodyPaths=[]))
        self.assertNotIn(self.name("g-speech"), absent)

    def test_no_extra_generators_or_dynamic_data_exclusions_remain(self):
        removed = {"customMimePaths", "legacyMcpMessagePaths", "inlineImagePaths", "inlinePdfPaths", "inlineAudioPaths"}
        self.assertFalse(removed & set(self.template["parameters"]))
        modules = self.main["resources"]
        modules = list(modules.values()) if isinstance(modules, dict) else modules
        module = next(item for item in modules if "wafPolicyIdentifier" in item.get("properties", {}).get("parameters", {}))
        self.assertFalse(removed & set(module["properties"]["parameters"]))
        self.assertFalse(removed & set(self.main["variables"]))
        declarations = [r for r in self.resources if r["type"] == WAF_TYPE]
        self.assertEqual(sum("copy" not in r for r in declarations), 6)
        self.assertEqual(sum("copy" in r for r in declarations), 1)
        parameters = self.parameters()
        parameters["publishedApiPaths"] += ["/enterprise/custom-binary", "/enterprise/mcp/messages", "/enterprise/chat"]
        policies = self.policies(parameters)
        self.assertEqual(len(policies), 28)
        for profile in self.profiles:
            expected = [*parameters["wafExclusions"]]
            if profile["scope"] == "portal":
                expected.append(self.cookie)
            for item in profile["exclusions"]:
                if item not in expected:
                    expected.append(item)
            actual = policies[self.name(profile["name"])]
            self.assertEqual(actual["managedRules"]["exclusions"], expected)
            if profile["name"] == "p-document-upload":
                expected_overrides = policies[self.name("g-document-upload")]["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"]
            elif profile["name"] in self.content_profiles:
                expected_overrides = [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT",
                                       "rules": [{"ruleId": "920420", "state": "Disabled"}]}]
            else:
                expected_overrides = []
            self.assertEqual(actual["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"], expected_overrides)
        for purpose, selector in (("g-chat-completions", "messages.content.image_url.url"),
                                  ("g-responses", "input.content.file_data")):
            profile = next(p for p in self.profiles if p["name"] == purpose)
            entry, = [e for e in profile["exclusions"] if e["selector"] == selector]
            self.assertEqual((entry["matchVariable"], entry["selectorMatchOperator"]), ("RequestArgValues", "Equals"))
            groups = [{"ruleGroupName": "REQUEST-941-APPLICATION-ATTACK-XSS",
                       "rules": [{"ruleId": "941130"}, {"ruleId": "941170"}]}]
            if purpose == "g-chat-completions":
                groups.insert(0, {"ruleGroupName": "REQUEST-931-APPLICATION-ATTACK-RFI",
                                  "rules": [{"ruleId": "931130"}]})
                groups.append({"ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                               "rules": [{"ruleId": "942340"}, {"ruleId": "942450"}]})
            else:
                groups = [{"ruleGroupName": name} for name in (
                    "REQUEST-930-APPLICATION-ATTACK-LFI", "REQUEST-931-APPLICATION-ATTACK-RFI",
                    "REQUEST-932-APPLICATION-ATTACK-RCE", "REQUEST-933-APPLICATION-ATTACK-PHP",
                    "REQUEST-941-APPLICATION-ATTACK-XSS", "REQUEST-942-APPLICATION-ATTACK-SQLI")]
            self.assertEqual(entry["exclusionManagedRuleSets"][0]["ruleGroups"], groups)

    def test_external_policy_boundary_remains_readonly(self):
        parameters = self.parameters()
        for changes in ({"existingWafPolicyId": "/fixture-existing-waf"}, {"skuName": "Standard_v2"}):
            with self.subTest(changes=changes):
                external = {**parameters, **changes}
                self.assertEqual(self.policies(external), {})
                gateway = self.gateway(external)
                self.assertNotIn("urlPathMaps", gateway)
                if "existingWafPolicyId" in changes:
                    self.assertEqual(gateway["firewallPolicy"], {"id": "/fixture-existing-waf"})
                else:
                    self.assertNotIn("firewallPolicy", gateway)


if __name__ == "__main__":
    unittest.main()
