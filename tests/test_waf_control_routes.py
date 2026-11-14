"""署名ticketとPortal handleの除外を、既存の制御入口に限定する。"""
import copy
import json
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.portal_gateway import portal_session_exclusions
from deployment.helpers.waf_body import portal_document_profile
from deployment.helpers.waf_names import policy_id
from waf_policy import (AI_DATA_RULE_GROUPS, CONTROL_DATA_SCOPES, SIGNED_OPERATION_ROUTES,
                        control_data_profile, desired_policy, load_manifest, published_paths,
                        reconcile, selected_profiles)
from test_gateway_callback_waf import CONFIG, GATEWAY_ID, REWRITE, api_mapping, gateway, policy
from test_waf_policy import ArmMemory


def contracts():
    """公開ソースの既存3 APIだけを、外部接続なしで読みます。"""
    return [json.loads((ROOT / "apps/gateway/apim/apis" / (name + ".json")).read_text())
            for name in ("document-analysis-v1", "openai-realtime-control-v1", "openai-realtime-observer-v1")]


class WafControlRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()
        cls.profiles = {p["name"]: p for p in cls.manifest["profiles"] if p["name"] in CONTROL_DATA_SCOPES}

    def test_only_existing_signed_operation_contracts_publish_identifier_patterns(self):
        config = {"apiDefinitions": contracts()}
        paths = published_paths(config)
        self.assertEqual({path for path in paths if "*" in path}, {route[-1] for route in SIGNED_OPERATION_ROUTES})
        self.assertIn("/models/openai-realtime/v1/observer/", paths)
        self.assertNotIn("/tools/documents/v1/*", paths)
        self.assertNotIn("/models/openai-realtime/v1/*", paths)
        for signature in SIGNED_OPERATION_ROUTES:
            api_name, api_path, op_name, method, template, kind, body_format, pattern = signature
            original = next(api for api in contracts() if api["name"] == api_name)
            for field, value in (("name", "unapproved-api"), ("path", api_path + "-neighbor"),
                                 ("operation", op_name + "-neighbor"), ("method", "DELETE"),
                                 ("urlTemplate", template + "/neighbor"), ("kind", "registeredOperation"),
                                 ("bodyFormat", "json")):
                mutated = copy.deepcopy(original)
                operation = next(op for op in mutated["operations"] if op["name"] == op_name)
                constraint = mutated["authorization"]["operationConstraints"][op_name]
                if field in ("name", "path"):
                    mutated[field] = value
                elif field == "operation":
                    operation["name"] = value
                    mutated["authorization"]["operationConstraints"][value] = constraint
                elif field in ("method", "urlTemplate"):
                    operation[field] = value
                else:
                    constraint[field] = value
                with self.subTest(api=api_name, mutation=field):
                    self.assertNotIn(pattern, published_paths({"apiDefinitions": [mutated]}))

    def test_header_selector_and_pattern_cannot_be_reused_on_neighbor_routes(self):
        for name in ("g-document-status", "g-realtime-ticket"):
            original = self.profiles[name]
            for mutation in ("header", "prefix", "body", "all-paths", "neighbor", "scope"):
                profile = copy.deepcopy(original)
                item = profile["exclusions"][0]
                if mutation == "header": item["selector"] = "Authorization"
                elif mutation == "prefix": item.update(selectorMatchOperator="StartsWith", selector=item["selector"] + ".")
                elif mutation == "body": item["matchVariable"] = "RequestArgValues"
                elif mutation == "all-paths": profile["paths"] = ["/*"]
                elif mutation == "neighbor": profile["paths"] = [original["paths"][0] + "/neighbor"]
                else: profile["scope"] = "portal"
                with self.subTest(profile=name, mutation=mutation), self.assertRaises(ValueError):
                    selected_profiles({"apiDefinitions": contracts()}, manifest={"version": 2, "profiles": [profile]})

    def test_calls_family_keeps_creation_exact_and_only_post_hangup_is_registered(self):
        realtime = next(api for api in contracts() if api["name"] == "openai-realtime-control-v1")
        # AppGW cannot use a middle wildcard. The existing APIM operation table
        # remains the application boundary for unknown children and HTTP verbs.
        routes = [(op["method"], re.compile(re.escape("/" + realtime["path"] + op["urlTemplate"])
                   .replace(re.escape("{callId}"), r"[^/]+") + r"\Z")) for op in realtime["operations"]]
        def operations(method, path):
            return [pattern for verb, pattern in routes if verb == method and pattern.fullmatch(path)]
        call = "/models/openai-realtime/v1/calls/synthetic-call/hangup"
        self.assertEqual(len(operations("POST", call)), 1)
        for method, path in (("GET", call), ("DELETE", call), ("POST", call + "/extra"),
                             ("POST", call.replace("/hangup", "/other")),
                             ("POST", call.replace("/synthetic-call/", "/synthetic-call/extra/"))):
            with self.subTest(method=method, path=path): self.assertEqual(operations(method, path), [])
        creation = next(p for p in self.manifest["profiles"] if p["name"] == "g-realtime")
        self.assertEqual(creation["paths"], ["/models/openai-realtime/v1/calls"])
        self.assertNotIn(creation["paths"][0], self.profiles["g-realtime-ticket"]["paths"])
        self.assertTrue(all("*" not in p or p.endswith("/*") for p in self.profiles["g-realtime-ticket"]["paths"]))

    def test_observer_same_url_requires_the_owned_websocket_operation_in_python_and_bicep(self):
        from arm_helpers import PolicyExpression
        from bicep_helpers import compile_bicep

        class MainExpression(PolicyExpression):
            def resolve(self, value, parameters, index=0):
                if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                    return MainExpression(value, self.variables, self.cache, self.functions).evaluate(parameters, index)
                return value

            def evaluate(self, parameters, index=0, tree=None, bindings=None):
                selected = self.tree if tree is None else tree
                if selected[:2] == ('call', 'concat'):
                    return [value for item in selected[2] for value in self.evaluate(parameters, index, item, bindings)]
                return super().evaluate(parameters, index, tree, bindings)

        template = compile_bicep('infra/main.bicep')
        resources = template['resources']
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        module = next(r for r in resources if 'publishedApiPaths' in r.get('properties', {}).get('parameters', {}))
        expression = module['properties']['parameters']['publishedApiPaths']['value']
        original = next(api for api in contracts() if api['name'] == 'openai-realtime-observer-v1')
        cases = [('standard', original)]
        for field, value in (('name', 'custom-observer'), ('apiType', 'http'), ('websocket', {}),
                             ('operation', 'custom-invoke'), ('method', 'POST'),
                             ('kind', 'realtimeCreate'), ('bodyFormat', 'json')):
            api = copy.deepcopy(original)
            operation = api['operations'][0]
            constraint = api['authorization']['operationConstraints']['invoke']
            if field in ('name', 'apiType', 'websocket'): api[field] = value
            elif field == 'operation':
                operation['name'] = value
                api['authorization']['operationConstraints'][value] = constraint
            elif field == 'method': operation[field] = value
            else: constraint[field] = value
            cases.append((field, api))
        for name, api in cases:
            config = {'apiDefinitions': [api]}
            actual = MainExpression(expression, template['variables']).evaluate({'configuration': config})
            with self.subTest(contract=name):
                self.assertEqual(set(actual), published_paths(config))
                self.assertEqual('/models/openai-realtime/v1/observer/' in actual, name == 'standard')
                if name not in ('standard', 'websocket'):
                    # Deployment now stops before compiling a changed API on a
                    # tuned URI. The pure published-path expressions above
                    # must still agree; a rejected contract cannot inherit the
                    # ticket exception by reaching Bicep through another path.
                    with self.assertRaisesRegex(ValueError, 'changed API input contract'):
                        selected_profiles(config, scopes=('gateway',))
                else:
                    selected = selected_profiles(config, scopes=('gateway',))
                    self.assertEqual(any(p['name'] == 'g-realtime-ticket' for p in selected), name == 'standard')

    def test_only_six_application_groups_are_added_to_each_exact_control_value(self):
        expected = {
            "g-document-status": ("RequestHeaderValues", "X-Core-Document-Ticket"),
            "g-realtime-ticket": ("RequestHeaderValues", "X-Core-Call-Ticket"),
            "p-document-status": ("RequestArgValues", "handle"),
        }
        self.assertEqual(set(self.profiles), set(expected))
        for name, selector in expected.items():
            item, = self.profiles[name]["exclusions"]
            self.assertEqual((item["matchVariable"], item["selector"]), selector)
            self.assertEqual(item["selectorMatchOperator"], "Equals")
            groups = item["exclusionManagedRuleSets"][0]["ruleGroups"]
            self.assertEqual({g["ruleGroupName"] for g in groups}, AI_DATA_RULE_GROUPS)
            self.assertTrue(all(set(g) == {"ruleGroupName"} for g in groups))

    def test_control_policies_keep_base_body_limits_custom_rules_and_cookie_union(self):
        source = policy()
        before = copy.deepcopy(source)
        for name, profile in self.profiles.items():
            desired = desired_policy(source, profile, {})
            actual = desired["properties"]
            self.assertEqual(actual["policySettings"], source["properties"]["policySettings"])
            self.assertFalse(portal_document_profile(profile))
            self.assertTrue(control_data_profile(profile))
            if name == "p-document-status":
                self.assertEqual(actual["customRules"][:-1], source["properties"]["customRules"])
                self.assertEqual(actual["customRules"][-1]["name"], "coreAiContentType")
                overrides = actual["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"]
                self.assertEqual(overrides, [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT",
                                             "rules": [{"ruleId": "920420", "state": "Disabled"}]}])
            else:
                self.assertEqual(actual["customRules"], source["properties"]["customRules"])
                self.assertEqual(actual["managedRules"]["managedRuleSets"], source["properties"]["managedRules"]["managedRuleSets"])
            expected = source["properties"]["managedRules"]["exclusions"] + (
                portal_session_exclusions() if name.startswith("p-") else []) + profile["exclusions"]
            self.assertEqual(actual["managedRules"]["exclusions"], expected)
            self.assertEqual(desired_policy(source, profile, {}, sources=(desired,)), desired)
        self.assertEqual(source, before)

    def test_reconcile_adds_only_three_owned_rules_and_is_idempotent(self):
        config = copy.deepcopy(CONFIG)
        config.update(apiDefinitions=contracts(), portal={"publicHostName": "portal.example.test"})
        source = gateway(path_based=True)
        props = source["properties"]
        props["httpListeners"].append({"name": "portal-https", "properties": {
            "protocol": "Https", "hostName": "portal.example.test",
            "firewallPolicy": {"id": policy_id(CONFIG, "p-base")}}})
        props["requestRoutingRules"][1]["properties"]["httpListener"] = {"id": GATEWAY_ID + "/httpListeners/portal-https"}
        portal_map = props["urlPathMaps"][0]["properties"]
        pool = {"id": GATEWAY_ID + "/backendAddressPools/bepool-portal"}
        backend = {"id": GATEWAY_ID + "/backendHttpSettingsCollection/behttp-portal"}
        portal_map.update(defaultBackendAddressPool=pool, defaultBackendHttpSettings=backend)
        portal_map["pathRules"][0]["properties"].update(backendAddressPool=pool, backendHttpSettings=backend)
        before = copy.deepcopy(source)
        selected = {"version": 2, "profiles": list(self.profiles.values())}
        transport = ArmMemory(source, empty_group_rules=True)
        result = reconcile(config, transport, manifest=selected)
        self.assertEqual(result["policyUpdates"], 3)
        self.assertEqual(result["azureWrites"], 4)
        updated = transport.resources[GATEWAY_ID]
        for field in ("httpListeners", "requestRoutingRules", "rewriteRuleSets", "backendAddressPools", "backendHttpSettingsCollection"):
            self.assertEqual(updated["properties"][field], before["properties"][field])
        for old_map in before["properties"]["urlPathMaps"]:
            current = next(m for m in updated["properties"]["urlPathMaps"] if m["name"] == old_map["name"])
            retained = [r for r in current["properties"]["pathRules"] if not r["name"].startswith("waf-data-")]
            self.assertEqual(retained, old_map["properties"]["pathRules"])
        for profile in self.profiles.values():
            routes = [r["properties"] for m in updated["properties"]["urlPathMaps"]
                      for r in m["properties"]["pathRules"] if r["name"] == "waf-data-" + profile["name"]]
            route, = routes
            self.assertEqual(route["paths"], profile["paths"])
            self.assertEqual(route["firewallPolicy"], {"id": policy_id(config, profile["name"])})
            if profile["scope"] == "gateway": self.assertEqual(route["rewriteRuleSet"], REWRITE)
        transport.calls.clear()
        self.assertEqual(reconcile(config, transport, manifest=selected)["azureWrites"], 0)
        self.assertFalse(any(call[0] == "PUT" for call in transport.calls))

    def test_custom_paths_do_not_acquire_implicit_signed_route_exceptions(self):
        published = published_paths({"apiDefinitions": []})
        self.assertNotIn("/tools/documents/v1/analyze/*", published)
        source = {"apiDefinitions": [{"name": "custom", "path": "custom/control", "operations": [
            {"name": "get_document_operation", "method": "GET", "urlTemplate": "/operations/{operationId}"}]}]}
        self.assertFalse(any(p["scope"] == "gateway" for p in selected_profiles(source)))
        self.assertEqual(selected_profiles(source, scopes=("gateway",)), [])


if __name__ == "__main__":
    unittest.main()
