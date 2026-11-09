"""Portal HTTPSの正確なセッション値・1規則だけを調整する契約を検証する。"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from bicep_helpers import compile_bicep
import test_ssml_waf as gateway_fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.portal_gateway import _callback_policy, _portal_policy, portal_session_exclusions
from deployment.helpers.waf_names import policy_id
from waf_policy import desired_policy, exclusion, data_scope_exclusions, load_manifest, reconcile
import test_waf_policy as data_fixtures


class PortalSessionWafTests(unittest.TestCase):
    parameters = gateway_fixtures.SsmlWafTests.parameters
    value = gateway_fixtures.SsmlWafTests.value
    resource = gateway_fixtures.SsmlWafTests.resource

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("infra/modules/application-gateway.bicep")
        resources = cls.template["resources"]
        cls.resources = list(resources.values() if isinstance(resources, dict) else resources)

    def test_exact_exclusion_matches_real_https_auth_cookie_and_keeps_cookie_protection(self):
        node = shutil.which("node")
        if not node or not (ROOT / "apps/portal/node_modules/better-auth").is_dir():
            self.skipTest("Local Node and installed Portal dependencies are required")
        script = """
import { getCookies } from 'better-auth/cookies';
import { createPortalAuthOptions } from './lib/auth-options.ts';
globalThis.fetch=()=>{throw new Error('offline cookie contract must not use network');};
const fixture={secret:'synthetic-cookie-contract-secret-32-characters',
 microsoftClientId:'00000000-0000-0000-0000-000000000000',
 microsoftClientSecret:'SYNTHETIC_NOT_A_CREDENTIAL',
 microsoftTenantId:'ffffffff-ffff-ffff-ffff-ffffffffffff'};
const cookies=['https://portal.example.com','http://localhost:3000'].map(baseURL=>
 getCookies(createPortalAuthOptions({...fixture,baseURL},undefined)).sessionToken);
console.log(JSON.stringify(cookies));
"""
        result = subprocess.run([node, "--conditions=react-server", "--experimental-strip-types",
                                 "--input-type=module", "-e", script],
                                cwd=ROOT / "apps/portal", capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, "Local cookie contract process failed")
        secure, local = json.loads(result.stdout)
        canonical = portal_session_exclusions()
        self.assertEqual(len(canonical), 1)
        selected = canonical[0]
        self.assertEqual(selected["matchVariable"], "RequestCookieValues")
        self.assertEqual(selected["selectorMatchOperator"], "Equals")
        self.assertEqual(selected["selector"], secure["name"])
        self.assertNotEqual(selected["selector"], local["name"])
        self.assertTrue(secure["attributes"]["secure"])
        self.assertTrue(secure["attributes"]["httpOnly"])
        self.assertEqual(secure["attributes"]["sameSite"], "lax")
        self.assertFalse(local["attributes"]["secure"])
        self.assertEqual(selected["exclusionManagedRuleSets"], [{
            "ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroups": [{
                "ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI", "rules": [{"ruleId": "942450"}]}]}])

    def test_compiled_default_and_callback_match_python_without_changing_common_protection(self):
        parameters = self.parameters()
        base = self.resource("g-base")
        baseline = self.value(base["properties"], parameters)
        source = {"location": "japaneast", "properties": baseline}
        original = copy.deepcopy(source)
        for suffix, builder in (("p-base", _portal_policy), ("p-auth", _callback_policy)):
            with self.subTest(suffix=suffix):
                policy = self.resource(suffix)
                compiled = self.value(policy["properties"], parameters)
                expected = builder(source)["properties"]
                self.assertEqual(compiled, expected)
                for field in ("policySettings", "customRules"):
                    self.assertEqual(compiled[field], baseline[field])
                self.assertEqual(compiled["managedRules"]["managedRuleSets"], baseline["managedRules"]["managedRuleSets"])
                for item in portal_session_exclusions():
                    self.assertEqual(compiled["managedRules"]["exclusions"].count(item), 1)
        self.assertEqual(source, original)
        self.assertTrue(all(item not in baseline["managedRules"]["exclusions"]
                            for item in portal_session_exclusions()))

    def test_opaque_code_exception_is_limited_to_fixed_callback_policies(self):
        parameters = self.parameters()
        base = self.value(self.resource("g-base")["properties"], parameters)
        source = {"location": "japaneast", "properties": base}
        compiled = self.value(self.resource("p-auth")["properties"], parameters)
        callback = "/api/auth/callback/microsoft"
        expected = exclusion("code", "REQUEST-942-APPLICATION-ATTACK-SQLI", ["942430", "942440", "942450"])
        groups = [compiled["managedRules"]["exclusions"],
                  _callback_policy(source)["properties"]["managedRules"]["exclusions"]]
        for values in groups:
            with self.subTest(values=values):
                added = [item for item in values
                         if item not in base["managedRules"]["exclusions"] and item not in portal_session_exclusions()]
                self.assertEqual(added, [expected])
        for path in (callback, callback + "/neighbor", callback.upper(), "/api/auth/sign-in/social", "/api/models/generate"):
            with self.subTest(path=path):
                self.assertEqual(data_scope_exclusions({"scope": "portal", "paths": [path]}),
                                 portal_session_exclusions())
        self.assertEqual(data_scope_exclusions({"scope": "gateway", "paths": ["/auth/connections/browser/identity-callback"]}),
                         [])

    def test_only_portal_https_listener_has_the_new_default_and_enterprise_is_not_replaced(self):
        gateway = next(item for item in self.resources if item["type"] == "Microsoft.Network/applicationGateways")
        for changes, enabled in (({}, True), ({"existingWafPolicyId": "/enterprise/policy"}, False),
                                 ({"skuName": "Standard_v2"}, False)):
            with self.subTest(changes=changes):
                parameters = self.parameters(**changes)
                properties = self.value(gateway["properties"], parameters)
                for listener in properties["httpListeners"]:
                    reference = listener["properties"].get("firewallPolicy")
                    if listener["name"] == "portal-https" and enabled:
                        self.assertTrue(reference["id"].endswith("/wafpol-p-base-abc123"))
                    else:
                        self.assertIsNone(reference)
                portal = self.resource("p-base")
                self.assertEqual(self.value(portal["condition"], parameters), enabled)

    def test_replacing_data_policies_include_session_only_for_portal_profiles(self):
        manifest = json.loads((ROOT / "apps/gateway/waf-data-exclusions.json").read_text(encoding="utf-8"))
        path = next(item["paths"][0] for item in manifest["profiles"] if item["scope"] == "gateway")
        parameters = self.parameters(publishedApiPaths=[path])
        profiles = self.value("[variables('dataProfiles')]", parameters)
        policies = self.value("[variables('dataPolicyProperties')]", parameters)
        self.assertEqual(len(profiles), len(policies))
        self.assertEqual({item["scope"] for item in profiles}, {"portal", "gateway"})
        for profile, policy in zip(profiles, policies):
            for item in portal_session_exclusions():
                self.assertEqual(policy["managedRules"]["exclusions"].count(item),
                                 1 if profile["scope"] == "portal" else 0)
            self.assertTrue(policy["policySettings"]["requestBodyCheck"])
            self.assertEqual(policy["policySettings"]["logScrubbing"]["state"], "Enabled")

    def test_python_data_reapplication_keeps_the_same_scope_and_deduplicates_session(self):
        base = self.resource("g-base")
        source = {"properties": self.value(base["properties"], self.parameters())}
        for scope in ("portal", "gateway"):
            profile = {"scope": scope, "paths": ["/api/models/text"], "exclusions": [
                exclusion("input", "REQUEST-942-APPLICATION-ATTACK-SQLI", ["942100"])]}
            desired = desired_policy(source, profile, {})
            repeated = desired_policy(source, profile, {}, sources=(desired,))
            self.assertEqual(repeated, desired)
            for session in portal_session_exclusions():
                self.assertEqual(desired["properties"]["managedRules"]["exclusions"].count(session),
                                 1 if scope == "portal" else 0)


    def test_realtime_stop_handle_policy_is_exact_value_two_rules_and_matches_bicep(self):
        path = "/api/models/realtime/stop"
        profiles = load_manifest()["profiles"]
        selected = [item for item in profiles if path in item["paths"]]
        self.assertEqual(len(selected), 1)
        profile = selected[0]
        self.assertEqual((profile["name"], profile["scope"], profile["paths"]),
                         ("p-realtime", "portal", [path]))
        expected = exclusion("handle", "REQUEST-942-APPLICATION-ATTACK-SQLI", ["942430", "942440"])
        self.assertEqual(profile["exclusions"], [expected])
        self.assertTrue(all(item["selector"] != "handle" for item in portal_session_exclusions()))
        self.assertTrue(all(item != expected for other in profiles if other != profile for item in other["exclusions"]))
        parameters = self.parameters()
        compiled_profiles = self.value("[variables('dataProfiles')]", parameters)
        index = next(i for i, item in enumerate(compiled_profiles) if item["name"] == profile["name"])
        compiled = self.value("[variables('dataPolicyProperties')]", parameters)[index]
        base = self.value(self.resource("g-base")["properties"], parameters)
        self.assertEqual(compiled, desired_policy({"location": "japaneast", "properties": base}, profile, {})["properties"])
        for field in ("policySettings", "customRules"):
            self.assertEqual(compiled[field], base[field])
        self.assertEqual(compiled["managedRules"]["managedRuleSets"], base["managedRules"]["managedRuleSets"])
        self.assertTrue(compiled["policySettings"]["requestBodyCheck"])
        self.assertEqual(compiled["policySettings"]["mode"], "Prevention")
        self.assertEqual(compiled["managedRules"]["exclusions"].count(expected), 1)
        rules = self.value("[variables('dataPortalPathRules')]", parameters)
        route = [item for item in rules if item["properties"]["paths"] == [path]]
        self.assertEqual(len(route), 1)
        self.assertTrue(route[0]["properties"]["firewallPolicy"]["id"].endswith("/wafpol-p-realtime-abc123"))
        self.assertTrue(route[0]["properties"]["backendAddressPool"]["id"].endswith("/bepool-portal"))
        self.assertTrue(route[0]["properties"]["backendHttpSettings"]["id"].endswith("/behttp-portal"))
        for neighbor in (path + "/neighbor", path + "/", "/api/models/realtime/socket", "/api/models/generate"):
            self.assertNotIn(neighbor, profile["paths"])

    def test_realtime_stop_reconcile_adds_one_policy_one_exact_route_then_is_idempotent(self):
        config, source = data_fixtures.configuration(), data_fixtures.gateway()
        config["portal"] = {"publicHostName": "portal.example.test"}
        props = source["properties"]
        props["httpListeners"].append({"name": "portal-https", "properties": {"protocol": "Https",
            "hostName": "portal.example.test", "firewallPolicy": {"id": policy_id(config, "p-base")}}})
        props["requestRoutingRules"][1]["properties"]["httpListener"] = {"id": data_fixtures.GATEWAY_ID + "/httpListeners/portal-https"}
        mapping = props["urlPathMaps"][0]["properties"]
        pool = {"id": data_fixtures.GATEWAY_ID + "/backendAddressPools/bepool-portal"}
        backend = {"id": data_fixtures.GATEWAY_ID + "/backendHttpSettingsCollection/behttp-portal"}
        mapping.update(defaultBackendAddressPool=pool, defaultBackendHttpSettings=backend)
        mapping["pathRules"][0]["properties"].update(backendAddressPool=pool, backendHttpSettings=backend)
        original = copy.deepcopy(source)
        profile = next(item for item in load_manifest()["profiles"] if item["name"] == "p-realtime")
        manifest = {"version": 1, "profiles": [profile]}
        transport = data_fixtures.ArmMemory(source)
        receipt = reconcile(config, transport, scopes=("portal",), manifest=manifest)
        self.assertEqual((receipt["azureWrites"], receipt["policyUpdates"], receipt["gatewayUpdated"]), (2, 1, True))
        puts = [call for call in transport.calls if call[0] == "PUT"]
        self.assertEqual([call[1] for call in puts], [policy_id(config, "p-realtime"), data_fixtures.GATEWAY_ID])
        self.assertEqual(puts[0][3], {"If-None-Match": "*"})
        self.assertEqual(puts[1][3], {"If-Match": original["etag"]})
        updated = transport.resources[data_fixtures.GATEWAY_ID]["properties"]
        self.assertEqual(updated["httpListeners"], original["properties"]["httpListeners"])
        self.assertEqual(updated["requestRoutingRules"], original["properties"]["requestRoutingRules"])
        current = updated["urlPathMaps"][0]["properties"]
        self.assertEqual(current["pathRules"][1:], original["properties"]["urlPathMaps"][0]["properties"]["pathRules"])
        self.assertEqual(current["pathRules"][0]["properties"]["paths"], ["/api/models/realtime/stop"])
        self.assertEqual(transport.resources[data_fixtures.BASE_ID], data_fixtures.policy())
        self.assertEqual(reconcile(config, transport, scopes=("portal",), manifest=manifest)["azureWrites"], 0)


if __name__ == "__main__":
    unittest.main()
