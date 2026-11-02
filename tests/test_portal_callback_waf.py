"""OAuth codeの実測したWAF誤検知だけを、callback完全パスで除外する。"""
import copy
import unittest

from test_portal_gateway import CONFIG, Transport, fixture
from deployment.helpers.portal_gateway import (CALLBACK_PATH, configure_portal_callback_waf,
                                                gateway_id, _callback_gateway, _callback_policy, _writable)

GATEWAY_ID = gateway_id(CONFIG)
BASE_ID = GATEWAY_ID.rsplit("/applicationGateways/", 1)[0] + "/ApplicationGatewayWebApplicationFirewallPolicies/wafpol-agw-test-fixture"
POLICY_ID = BASE_ID + "-portal-auth"


def gateway():
    body = fixture(["200"], "/api/health")
    props = body["properties"]
    props["firewallPolicy"] = {"id": BASE_ID}
    props["httpListeners"] = [{"name": "portal-https", "properties": {"hostName": "portal.example.test", "protocol": "Https"}}]
    props["requestRoutingRules"] = [
        {"name": "rule-https-to-apim", "properties": {"ruleType": "PathBasedRouting", "priority": 200,
         "urlPathMap": {"id": GATEWAY_ID + "/urlPathMaps/pathmap-protocols"}}},
        {"name": "rule-portal-https", "properties": {"ruleType": "Basic", "priority": 210,
         "httpListener": {"id": GATEWAY_ID + "/httpListeners/portal-https"},
         "backendAddressPool": {"id": GATEWAY_ID + "/backendAddressPools/bepool-portal"},
         "backendHttpSettings": {"id": GATEWAY_ID + "/backendHttpSettingsCollection/behttp-portal"}}},
    ]
    props["urlPathMaps"] = [{"name": "pathmap-protocols", "properties": {"pathRules": [{"name": "existing-api", "properties": {"paths": ["/api/*"]}}]}}]
    return body


def policy():
    return {"id": BASE_ID, "name": "wafpol-agw-test-fixture", "type": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies",
            "etag": '"policy-v1"', "location": "japaneast", "tags": {"environment": "test"},
            "properties": {"provisioningState": "Succeeded", "applicationGateways": [{"id": GATEWAY_ID}],
                "policySettings": {"mode": "Prevention", "state": "Enabled", "requestBodyCheck": True,
                                   "maxRequestBodySizeInKb": 128, "logScrubbing": {"state": "Enabled"}},
                "customRules": [{"name": "corporate-rule", "priority": 1, "action": "Block"}],
                "managedRules": {"managedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroupOverrides": []}],
                                 "exclusions": [{"matchVariable": "RequestHeaderValues", "selector": "Existing", "selectorMatchOperator": "Equals"}]}}}


def ready_policy(desired):
    body = copy.deepcopy(desired)
    body.update({"id": POLICY_ID, "name": "wafpol-agw-test-fixture-portal-auth", "type": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies", "etag": '"policy-v2"'})
    body["properties"]["provisioningState"] = "Succeeded"
    return body


def ready_gateway(desired):
    body = copy.deepcopy(desired)
    body.update({"id": GATEWAY_ID, "name": "agw-test-fixture", "type": "Microsoft.Network/applicationGateways", "etag": '"gateway-v2"'})
    body["properties"]["provisioningState"] = "Succeeded"
    return body


class PortalCallbackWafTests(unittest.TestCase):
    def test_clones_current_protection_and_changes_only_code_rules_on_exact_portal_callback(self):
        current, base = gateway(), policy()
        original_gateway, original_base = copy.deepcopy(current), copy.deepcopy(base)
        desired_policy = _callback_policy(base)
        desired_gateway = _callback_gateway(current, GATEWAY_ID, POLICY_ID)
        transport = Transport([
            {"statusCode": 200, "body": current}, {"statusCode": 200, "body": base},
            {"statusCode": 404, "body": None}, {"statusCode": 202, "body": None},
            {"statusCode": 200, "body": ready_policy(desired_policy)}, {"statusCode": 202, "body": None},
            {"statusCode": 200, "body": ready_gateway(desired_gateway)},
        ])
        receipt = configure_portal_callback_waf(CONFIG, transport, sleep=lambda _: None)
        self.assertTrue(receipt["policyUpdated"])
        self.assertTrue(receipt["gatewayUpdated"])
        self.assertEqual(current, original_gateway)
        self.assertEqual(base, original_base)
        changes = [call for call in transport.calls if call["method"] == "PUT"]
        self.assertEqual(len(changes), 2)
        actual = changes[0]["body"]["properties"]
        self.assertEqual(actual["policySettings"], base["properties"]["policySettings"])
        self.assertEqual(actual["customRules"], base["properties"]["customRules"])
        self.assertEqual(actual["managedRules"]["managedRuleSets"], base["properties"]["managedRules"]["managedRuleSets"])
        exclusion = actual["managedRules"]["exclusions"][-1]
        self.assertEqual(exclusion["matchVariable"], "RequestArgValues")
        self.assertEqual(exclusion["selector"], "code")
        self.assertEqual([r["ruleId"] for r in exclusion["exclusionManagedRuleSets"][0]["ruleGroups"][0]["rules"]], ["942430", "942440"])
        props = changes[1]["body"]["properties"]
        self.assertEqual(props["requestRoutingRules"][0], current["properties"]["requestRoutingRules"][0])
        self.assertEqual(props["urlPathMaps"][0], current["properties"]["urlPathMaps"][0])
        portal_map = props["urlPathMaps"][1]["properties"]
        self.assertEqual(portal_map["pathRules"][0]["properties"]["paths"], [CALLBACK_PATH])
        self.assertEqual(portal_map["defaultBackendHttpSettings"]["id"], GATEWAY_ID + "/backendHttpSettingsCollection/behttp-portal")

    def test_repeating_completed_configuration_does_not_write(self):
        desired_policy = ready_policy(_callback_policy(policy()))
        desired_gateway = ready_gateway(_callback_gateway(gateway(), GATEWAY_ID, POLICY_ID))
        transport = Transport([{"statusCode": 200, "body": desired_gateway}, {"statusCode": 200, "body": policy()},
                               {"statusCode": 200, "body": desired_policy}])
        receipt = configure_portal_callback_waf(CONFIG, transport)
        self.assertFalse(receipt["policyUpdated"])
        self.assertFalse(receipt["gatewayUpdated"])
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_external_policy_or_foreign_route_stops_without_write(self):
        for case in ("policy", "route", "listener"):
            body = gateway()
            if case == "policy":
                body["properties"]["firewallPolicy"]["id"] = "/enterprise/policy"
            elif case == "route":
                body["properties"]["requestRoutingRules"][1]["properties"]["backendAddressPool"]["id"] = "/other/backend"
            else:
                body["properties"]["httpListeners"][0]["properties"]["firewallPolicy"] = {"id": "/enterprise/listener-policy"}
            transport = Transport([{"statusCode": 200, "body": body}])
            with self.subTest(case=case), self.assertRaises(ValueError):
                configure_portal_callback_waf(CONFIG, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])


if __name__ == "__main__":
    unittest.main()
