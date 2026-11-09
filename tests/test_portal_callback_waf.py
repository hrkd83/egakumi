"""OAuth codeの実測したWAF誤検知だけを、callback完全パスで除外する。"""
import copy
import unittest

from test_portal_gateway import CONFIG, Transport, fixture
from deployment.helpers.waf_names import policy_id, policy_name
from deployment.helpers.portal_gateway import (CALLBACK_PATH, configure_portal_callback_waf,
                                                gateway_id, _callback_gateway, _callback_policy, _portal_policy,
                                                portal_session_exclusions, _writable)

GATEWAY_ID = gateway_id(CONFIG)
BASE_ID = policy_id(CONFIG, "g-base")
POLICY_ID = policy_id(CONFIG, "p-auth")
SESSION_POLICY_ID = policy_id(CONFIG, "p-base")


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
    return {"id": BASE_ID, "name": policy_name(CONFIG, "g-base"), "type": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies",
            "etag": '"policy-v1"', "location": "japaneast", "tags": {"environment": "test"},
            "properties": {"provisioningState": "Succeeded", "applicationGateways": [{"id": GATEWAY_ID}],
                "policySettings": {"mode": "Prevention", "state": "Enabled", "requestBodyCheck": True,
                                   "maxRequestBodySizeInKb": 128, "logScrubbing": {"state": "Enabled"}},
                "customRules": [{"name": "corporate-rule", "priority": 1, "action": "Block"}],
                "managedRules": {"managedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2", "ruleGroupOverrides": []}],
                                 "exclusions": [{"matchVariable": "RequestHeaderValues", "selector": "Existing", "selectorMatchOperator": "Equals"}]}}}


def ready_policy(desired, identifier=POLICY_ID):
    body = copy.deepcopy(desired)
    body.update({"id": identifier, "name": identifier.rsplit("/", 1)[-1], "type": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies", "etag": '"policy-v2"'})
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
        desired_session_policy = _portal_policy(base)
        desired_gateway = _callback_gateway(CONFIG, current, GATEWAY_ID, POLICY_ID)
        transport = Transport([
            {"statusCode": 200, "body": current}, {"statusCode": 200, "body": base},
            {"statusCode": 404, "body": None}, {"statusCode": 404, "body": None},
            {"statusCode": 202, "body": None},
            {"statusCode": 200, "body": ready_policy(desired_session_policy, SESSION_POLICY_ID)},
            {"statusCode": 202, "body": None},
            {"statusCode": 200, "body": ready_policy(desired_policy)},
            {"statusCode": 200, "body": current}, {"statusCode": 202, "body": None},
            {"statusCode": 200, "body": ready_gateway(desired_gateway)},
        ])
        receipt = configure_portal_callback_waf(CONFIG, transport, sleep=lambda _: None)
        self.assertTrue(receipt["policyUpdated"])
        self.assertTrue(receipt["sessionPolicyUpdated"])
        self.assertTrue(receipt["gatewayUpdated"])
        self.assertEqual(current, original_gateway)
        self.assertEqual(base, original_base)
        changes = [call for call in transport.calls if call["method"] == "PUT"]
        self.assertEqual(len(changes), 3)
        self.assertEqual(changes[0]["headers"], {"If-None-Match": "*"})
        self.assertEqual(changes[0]["body"]["properties"]["managedRules"]["exclusions"],
                         base["properties"]["managedRules"]["exclusions"] + portal_session_exclusions())
        actual = changes[1]["body"]["properties"]
        self.assertEqual(actual["policySettings"], base["properties"]["policySettings"])
        self.assertEqual(actual["customRules"], base["properties"]["customRules"])
        self.assertEqual(actual["managedRules"]["managedRuleSets"], base["properties"]["managedRules"]["managedRuleSets"])
        exclusion = actual["managedRules"]["exclusions"][-1]
        self.assertEqual(exclusion["matchVariable"], "RequestArgValues")
        self.assertEqual(exclusion["selector"], "code")
        self.assertEqual([r["ruleId"] for r in exclusion["exclusionManagedRuleSets"][0]["ruleGroups"][0]["rules"]], ["942430", "942440", "942450"])
        props = changes[2]["body"]["properties"]
        self.assertEqual(changes[2]["headers"], {"If-Match": '"v1"'})
        self.assertEqual(props["httpListeners"][0]["properties"]["firewallPolicy"], {"id": SESSION_POLICY_ID})
        self.assertEqual(props["requestRoutingRules"][0], current["properties"]["requestRoutingRules"][0])
        self.assertEqual(props["urlPathMaps"][0], current["properties"]["urlPathMaps"][0])
        portal_map = props["urlPathMaps"][1]["properties"]
        self.assertEqual(portal_map["pathRules"][0]["properties"]["paths"], [CALLBACK_PATH])
        self.assertEqual(portal_map["defaultBackendHttpSettings"]["id"], GATEWAY_ID + "/backendHttpSettingsCollection/behttp-portal")

    def test_repeating_completed_configuration_does_not_write(self):
        desired_policy = ready_policy(_callback_policy(policy()))
        desired_session_policy = ready_policy(_portal_policy(policy()), SESSION_POLICY_ID)
        desired_gateway = ready_gateway(_callback_gateway(CONFIG, gateway(), GATEWAY_ID, POLICY_ID))
        transport = Transport([{"statusCode": 200, "body": desired_gateway}, {"statusCode": 200, "body": policy()},
                               {"statusCode": 200, "body": desired_session_policy},
                               {"statusCode": 200, "body": desired_policy}])
        receipt = configure_portal_callback_waf(CONFIG, transport)
        self.assertFalse(receipt["policyUpdated"])
        self.assertFalse(receipt["sessionPolicyUpdated"])
        self.assertFalse(receipt["gatewayUpdated"])
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_external_policy_or_foreign_route_stops_without_write(self):
        for case in ("policy", "route", "listener", "protocol", "sku", "path-policy"):
            body = gateway()
            if case == "policy":
                body["properties"]["firewallPolicy"]["id"] = "/enterprise/policy"
            elif case == "route":
                body["properties"]["requestRoutingRules"][1]["properties"]["backendAddressPool"]["id"] = "/other/backend"
            elif case == "listener":
                body["properties"]["httpListeners"][0]["properties"]["firewallPolicy"] = {"id": "/enterprise/listener-policy"}
            elif case == "protocol":
                body["properties"]["httpListeners"][0]["properties"]["protocol"] = "Http"
            elif case == "sku":
                body["properties"]["sku"]["name"] = "Standard_v2"
            else:
                body = _callback_gateway(CONFIG, body, GATEWAY_ID, POLICY_ID)
                body["properties"]["urlPathMaps"][1]["properties"]["pathRules"][0]["properties"]["firewallPolicy"] = {"id": "/enterprise/callback-policy"}
            transport = Transport([{"statusCode": 200, "body": body}])
            with self.subTest(case=case), self.assertRaises(ValueError):
                configure_portal_callback_waf(CONFIG, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_explicit_enterprise_policy_and_wrong_portal_host_never_write(self):
        config = copy.deepcopy(CONFIG)
        config["gateway"] = {"existingWafPolicyId": "/enterprise/policy"}
        transport = Transport([])
        with self.assertRaisesRegex(ValueError, "enterprise"):
            configure_portal_callback_waf(config, transport)
        self.assertEqual(transport.calls, [])
        config.pop("gateway")
        config["portal"] = {"publicHostName": "another.example.test"}
        transport = Transport([{"statusCode": 200, "body": gateway()}])
        with self.assertRaisesRegex(ValueError, "public host"):
            configure_portal_callback_waf(config, transport)
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_callback_target_must_be_the_exact_owned_purpose_and_instance(self):
        for identifier in (SESSION_POLICY_ID, POLICY_ID.replace("abc123", "def456"),
                           BASE_ID + "-portal-auth"):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "owned purpose"):
                _callback_gateway(CONFIG, gateway(), GATEWAY_ID, identifier)

    def test_gateway_etag_is_required_before_any_policy_write(self):
        current = gateway()
        current.pop("etag")
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 200, "body": policy()},
                               {"statusCode": 404}, {"statusCode": 404}])
        with self.assertRaisesRegex(ValueError, "ETag"):
            configure_portal_callback_waf(CONFIG, transport)
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_existing_policy_update_uses_etag_and_conflict_stops_before_gateway_write(self):
        old_session_policy = ready_policy(policy(), SESSION_POLICY_ID)
        transport = Transport([{"statusCode": 200, "body": gateway()}, {"statusCode": 200, "body": policy()},
                               {"statusCode": 200, "body": old_session_policy}, {"statusCode": 404},
                               {"statusCode": 412}])
        with self.assertRaisesRegex(ValueError, "412"):
            configure_portal_callback_waf(CONFIG, transport)
        writes = [call for call in transport.calls if call["method"] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"].split("?", 1)[0], SESSION_POLICY_ID)
        self.assertEqual(writes[0]["headers"], {"If-Match": '"policy-v2"'})

    def test_owned_portal_listener_policy_reference_casing_does_not_trigger_a_gateway_put(self):
        current = ready_gateway(_callback_gateway(CONFIG, gateway(), GATEWAY_ID, POLICY_ID))
        current["properties"]["httpListeners"][0]["properties"]["firewallPolicy"]["id"] = SESSION_POLICY_ID.lower()
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 200, "body": policy()},
                               {"statusCode": 200, "body": ready_policy(_portal_policy(policy()), SESSION_POLICY_ID)},
                               {"statusCode": 200, "body": ready_policy(_callback_policy(policy()))}])
        receipt = configure_portal_callback_waf(CONFIG, transport)
        self.assertFalse(receipt["gatewayUpdated"])
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_policy_updates_refresh_gateway_etag_without_changing_other_fields(self):
        current, base = gateway(), policy()
        fresh = copy.deepcopy(current)
        fresh["etag"] = '"after-policy-updates"'
        desired = _callback_gateway(CONFIG, current, GATEWAY_ID, POLICY_ID)
        transport = Transport([
            {"statusCode": 200, "body": current}, {"statusCode": 200, "body": base},
            {"statusCode": 404}, {"statusCode": 404},
            {"statusCode": 202}, {"statusCode": 200, "body": ready_policy(_portal_policy(base), SESSION_POLICY_ID)},
            {"statusCode": 202}, {"statusCode": 200, "body": ready_policy(_callback_policy(base))},
            {"statusCode": 200, "body": fresh}, {"statusCode": 202},
            {"statusCode": 200, "body": ready_gateway(desired)},
        ])
        receipt = configure_portal_callback_waf(CONFIG, transport, sleep=lambda _: None)
        self.assertTrue(receipt["gatewayUpdated"])
        gateway_write = next(index for index, call in enumerate(transport.calls)
                             if call["method"] == "PUT" and call["path"].split("?", 1)[0] == GATEWAY_ID)
        self.assertEqual(transport.calls[gateway_write - 1]["method"], "GET")
        self.assertEqual(transport.calls[gateway_write - 1]["path"].split("?", 1)[0], GATEWAY_ID)
        self.assertEqual(transport.calls[gateway_write]["headers"], {"If-Match": '"after-policy-updates"'})
        self.assertEqual(transport.calls[gateway_write]["body"], desired)

    def test_fresh_gateway_body_drift_stops_without_overwriting_new_backend_settings(self):
        current, base = gateway(), policy()
        current["properties"]["backendHttpSettingsCollection"][0]["properties"]["requestTimeout"] = 600
        fresh = copy.deepcopy(current)
        fresh["etag"] = '"concurrent-update"'
        fresh["properties"]["backendHttpSettingsCollection"][0]["properties"]["requestTimeout"] = 601
        transport = Transport([
            {"statusCode": 200, "body": current}, {"statusCode": 200, "body": base},
            {"statusCode": 200, "body": ready_policy(_portal_policy(base), SESSION_POLICY_ID)},
            {"statusCode": 200, "body": ready_policy(_callback_policy(base))},
            {"statusCode": 200, "body": fresh},
        ])
        with self.assertRaisesRegex(ValueError, "changed before"):
            configure_portal_callback_waf(CONFIG, transport)
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_fresh_gateway_requires_ownership_succeeded_state_and_etag(self):
        for issue in ("etag", "state", "id"):
            current, base = gateway(), policy()
            fresh = copy.deepcopy(current)
            if issue == "etag":
                fresh.pop("etag")
            elif issue == "state":
                fresh["properties"]["provisioningState"] = "Updating"
            else:
                fresh["id"] = GATEWAY_ID + "-foreign"
            transport = Transport([
                {"statusCode": 200, "body": current}, {"statusCode": 200, "body": base},
                {"statusCode": 200, "body": ready_policy(_portal_policy(base), SESSION_POLICY_ID)},
                {"statusCode": 200, "body": ready_policy(_callback_policy(base))},
                {"statusCode": 200, "body": fresh},
            ])
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                configure_portal_callback_waf(CONFIG, transport)
            self.assertTrue(all(call["method"] == "GET" for call in transport.calls))


if __name__ == "__main__":
    unittest.main()
