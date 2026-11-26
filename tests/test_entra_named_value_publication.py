"""Entra設定の基盤所有、独立checkpoint、完全型付きreadbackを合成ARMで検証する。"""
import copy
import json
import unittest
import xml.etree.ElementTree as ET

import test_publish_apis as legacy
import test_entra_publication_bootstrap as bootstrap
from test_entra_publication import APIM, GLOBAL, fixture, response, xml
from deployment.helpers.apim_authentication import (
    ENTRA_NAMED_VALUES, render_service_policy, validate_entra_named_values,
)
import entra_publication as publication
from bicep_helpers import compile_bicep
from arm_helpers import PolicyExpression
from deployment.helpers.apim_audit import render_audit_fragments
from deployment.helpers.apim_ingress import TRUSTED_INGRESS_ID, render_trusted_ingress_fragment
from deployment.helpers.apim_browser_exception import BROWSER_EXCEPTION_ID, render_browser_exception_fragment
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments
from authentication_source_fixture import annotated_authentication_source

publish = legacy.publish


def configured():
    base = legacy.PublicationTests("runTest")
    base.setUp()
    base.config["entraTenantId"] = "33333333-3333-4333-8333-333333333333"
    base.config["gateway"]["authentication"]["entra"] = {
        "audience": "44444444-4444-4444-8444-444444444444", "requiredScope": "Gateway.Invoke",
        "userLimits": {"requestRate": {"calls": 25, "renewalPeriodSeconds": 60},
                       "tokensPerMinute": 20000, "tokenQuota": {"tokens": 1000000, "period": "Monthly"}}}
    plan = publish.prepare_plan(base.config, base.state)
    inventory = copy.deepcopy(base.inventory)
    global_id = plan["apimId"] + "/policies/policy"
    inventory[global_id] = response(global_id, {"format": "rawxml", "value": render_service_policy()})
    for name, value in plan["entraNamedValues"].items():
        rid = plan["apimId"] + "/namedValues/" + name
        inventory[rid] = response(rid, {"displayName": name, "secret": False, "value": value})
    # Globalが呼ぶSTEP4は、独立したフラグメントとして同じ合成ARMから読み取る。
    for name, value in render_common_authentication_fragments(entra_enabled=False).items():
        rid = plan["apimId"] + "/policyFragments/" + name
        inventory[rid] = response(rid, {"format": "rawxml", "value": value})
    return base, plan, inventory


def checkpoint_fixture(*, entra_enabled=True, previous_explanations=False):
    base, plan, inventory = configured()
    if not entra_enabled:
        base.config["gateway"]["authentication"].pop("entra")
        plan = publish.prepare_plan(base.config, base.state)
    _, _, _, initial = bootstrap.controller_fixture()
    source = copy.deepcopy(initial.source)
    global_policy = render_service_policy(entra=plan["entraAuthentication"])
    # checkpointはGlobalの参照先を含む完全な生成元で検査する。
    # API・操作・Named Valuesの既存fixtureは変更せず、共通部品の本文だけを登録する。
    fragments = render_audit_fragments()
    fragments[TRUSTED_INGRESS_ID] = render_trusted_ingress_fragment()
    fragments[BROWSER_EXCEPTION_ID] = render_browser_exception_fragment()
    fragments.update(render_common_authentication_fragments(entra_enabled=entra_enabled))
    for name, value in fragments.items():
        source["policies"].append({"suffix": "/policyFragments/" + name, "value": value})
        rid = plan["apimId"] + "/policyFragments/" + name
        inventory[rid] = response(rid, {"format": "rawxml", "value": value})
    saved = publish.source_checkpoint(plan, source, global_policy)
    if previous_explanations:
        # Represent an already-saved annotated raw source. A new checkpoint
        # still requires canonical code; do not pass annotations to its writer.
        global_policy = annotated_authentication_source(global_policy)
        identifiers = set(render_common_authentication_fragments(entra_enabled=entra_enabled))
        for entry in source["policies"]:
            identifier = entry["suffix"].removeprefix("/policyFragments/")
            if identifier in identifiers:
                entry["value"] = annotated_authentication_source(entry["value"])
                inventory[plan["apimId"] + entry["suffix"]] = response(
                    plan["apimId"] + entry["suffix"], {"format": "rawxml", "value": entry["value"]})
        saved = {**saved, "globalPolicy": global_policy, "source": copy.deepcopy(source)}
    return base, plan, inventory, source, global_policy, saved


class EntraFoundationPublicationTests(unittest.TestCase):
    def test_plan_keeps_four_entra_values_separate_from_key_header_and_arm_parameters(self):
        _, plan, _ = configured()
        self.assertEqual(set(plan["entraNamedValues"]), set(ENTRA_NAMED_VALUES))
        self.assertEqual(plan["commonAuthentication"], {"core-subscription-key-header": "api-key"})
        self.assertEqual(plan["entraNamedValues"]["core-entra-tenant-id"], plan["entraTenantId"])
        self.assertNotIn("entraNamedValues", plan["parameters"])

    def test_publication_template_cannot_write_any_foundation_entra_value(self):
        base, _, _ = configured()
        for name in ENTRA_NAMED_VALUES:
            template = copy.deepcopy(base.template)
            template["resources"].append({"type": "Microsoft.ApiManagement/service/namedValues", "name": "fixture/" + name,
                                          "properties": {"displayName": name, "secret": False, "value": "other"}})
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "authentication NamedValues"):
                publish.validate_publication_template(template)

    def test_foundation_entra_readback_is_required_before_any_write(self):
        _, plan, original = configured()
        for name in ENTRA_NAMED_VALUES:
            for mismatch in ("missing", "name", "secret", "keyVault", "value"):
                inventory = copy.deepcopy(original)
                rid = plan["apimId"] + "/namedValues/" + name
                props = inventory[rid]["body"]["properties"]
                if mismatch == "missing": inventory[rid] = response(rid, {}, status=404)
                elif mismatch == "name": props["displayName"] = "other"
                elif mismatch == "secret": props["secret"] = True
                elif mismatch == "keyVault": props["keyVault"] = {"secretIdentifier": "https://vault.invalid/secrets/other"}
                else: props["value"] = "foreign-setting"
                transport = legacy.Transport(inventory)
                with self.subTest(name=name, mismatch=mismatch), self.assertRaisesRegex(ValueError, "common authentication"):
                    publish.verify_common_authentication(plan, transport)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_readback_allows_json_formatting_but_rejects_scope_case_and_incomplete_limits(self):
        _, plan, original = configured()
        rid = plan["apimId"] + "/namedValues/core-entra-user-limits"
        reordered = dict(reversed(list(json.loads(plan["entraNamedValues"]["core-entra-user-limits"]).items())))
        inventory = copy.deepcopy(original)
        inventory[rid]["body"]["properties"]["value"] = json.dumps(reordered, indent=2)
        publish.verify_common_authentication(plan, legacy.Transport(inventory))
        for name, value in (("core-entra-required-scope", "gateway.invoke"),
                            ("core-entra-user-limits", '{"requestRate":null,"tokensPerMinute":null}'),
                            ("core-entra-user-limits", '{"requestRate":null,"tokensPerMinute":true,"tokenQuota":null}'),
                            ("core-entra-user-limits", '{"requestRate":null,"requestRate":null,"tokensPerMinute":null,"tokenQuota":null}')):
            inventory = copy.deepcopy(original)
            inventory[plan["apimId"] + "/namedValues/" + name]["body"]["properties"]["value"] = value
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "common authentication"):
                publish.verify_common_authentication(plan, legacy.Transport(inventory))


class IndependentAuthenticationCheckpointTests(unittest.TestCase):
    def test_checkpoint_preserves_old_api_source_and_restores_independent_expectations(self):
        for enabled, previous_explanations in ((enabled, previous) for enabled in (False, True)
                                              for previous in (False, True)):
            base, plan, _, source, global_policy, saved = checkpoint_fixture(
                entra_enabled=enabled, previous_explanations=previous_explanations)
            state = {**base.state, "apiPublicationSourceCheckpoint": saved}
            before_config, before_state, before_source = (copy.deepcopy(value) for value in
                                                         (base.config, state, source))
            with self.subTest(entra=enabled, previous_explanations=previous_explanations):
                self.assertEqual(saved["source"], source)
                self.assertFalse(any(entry["suffix"].rsplit("/", 1)[-1] in ENTRA_NAMED_VALUES
                                     for entry in source["namedValues"]))
                self.assertEqual(saved["globalPolicy"], global_policy)
                self.assertEqual(saved["entraAuthentication"], plan["entraAuthentication"])
                self.assertEqual(saved["entraNamedValues"], plan["entraNamedValues"])
                restored = publish.prepare_plan(base.config, state)
                self.assertEqual(restored["previousEntraAuthentication"], plan["entraAuthentication"])
                self.assertEqual(restored["previousGlobalPolicy"], global_policy)
                self.assertEqual(base.config, before_config)
                self.assertEqual(state, before_state)
                self.assertEqual(source, before_source)
                old = {key: copy.deepcopy(value) for key, value in saved.items()
                       if key not in ("globalPolicy", "entraAuthentication", "entraNamedValues")}
                self.assertIsNone(publish.checkpoint_authentication(old, plan))

    def test_checkpoint_rejects_its_missing_or_tampered_current_dictionary_without_rewriting_it(self):
        for enabled in (False, True):
            _, plan, _, _, _, saved = checkpoint_fixture(entra_enabled=enabled)
            original = copy.deepcopy(saved)
            identifiers = ("core-common-authentication", "core-entra-authentication-error") if enabled else (
                "core-common-authentication",)
            for identifier in identifiers:
                suffix = "/policyFragments/" + identifier
                for missing in (False, True):
                    invalid = copy.deepcopy(saved)
                    if missing:
                        invalid["source"]["policies"] = [entry for entry in invalid["source"]["policies"]
                                                         if entry["suffix"] != suffix]
                    else:
                        entry = next(item for item in invalid["source"]["policies"] if item["suffix"] == suffix)
                        if identifier == "core-common-authentication":
                            self.assertIn("Invalid subscription key", entry["value"])
                            entry["value"] = entry["value"].replace("Invalid subscription key", "Unreviewed failure", 1)
                        else:
                            document = ET.fromstring(entry["value"])
                            document.find(".//set-header[@name='WWW-Authenticate']/value").text = "Changed challenge"
                            entry["value"] = ET.tostring(document, encoding="unicode")
                    before = copy.deepcopy(invalid)
                    with self.subTest(entra=enabled, fragment=identifier, missing=missing), self.assertRaises(ValueError):
                        publish.checkpoint_authentication(invalid, plan)
                    self.assertEqual(invalid, before)
                    self.assertEqual(saved, original)

    def test_inconsistent_or_partial_checkpoint_cannot_supply_expected_authentication(self):
        _, plan, _, _, _, saved = checkpoint_fixture()
        for mismatch in ("missing-field", "scope", "limits", "global", "unrepresented"):
            invalid = copy.deepcopy(saved)
            if mismatch == "missing-field": invalid.pop("entraNamedValues")
            elif mismatch == "scope": invalid["entraNamedValues"]["core-entra-required-scope"] = "gateway.invoke"
            elif mismatch == "limits": invalid["entraNamedValues"]["core-entra-user-limits"] = "{}"
            elif mismatch == "global": invalid["globalPolicy"] = xml("foreign")
            else: invalid["source"]["apis"] = []
            with self.subTest(mismatch=mismatch), self.assertRaises(ValueError):
                publish.checkpoint_authentication(invalid, plan)

    def test_global_readback_must_match_saved_custom_processing_and_trusted_blocks(self):
        base, _, inventory, _, global_policy, saved = checkpoint_fixture()
        plan = publish.prepare_plan(base.config, {**base.state, "apiPublicationSourceCheckpoint": saved})
        rid = plan["apimId"] + "/policies/policy"
        inventory[rid] = response(rid, {"format": "rawxml", "value": global_policy.replace(
            "<inbound>", '<inbound><set-variable name="unreviewed-change" value="true" />', 1)})
        with self.assertRaises(ValueError):
            publish.verify_common_authentication(plan, legacy.Transport(inventory))

    def test_settings_update_uses_old_global_structure_and_new_explicit_values(self):
        base, _, inventory, _, global_policy, saved = checkpoint_fixture()
        base.config["gateway"]["authentication"]["entra"]["requiredScope"] = "Gateway.Next"
        base.config["gateway"]["authentication"]["entra"]["userLimits"]["tokensPerMinute"] = None
        plan = publish.prepare_plan(base.config, {**base.state, "apiPublicationSourceCheckpoint": saved})
        inventory[plan["apimId"] + "/policies/policy"] = response(plan["apimId"] + "/policies/policy",
            {"format": "rawxml", "value": global_policy})
        for name, value in plan["entraNamedValues"].items():
            inventory[plan["apimId"] + "/namedValues/" + name]["body"]["properties"]["value"] = value
        verified = publish.verify_common_authentication(plan, legacy.Transport(inventory))
        self.assertEqual(verified["previousEntra"]["requiredScope"], "Gateway.Invoke")
        self.assertEqual(verified["entraNamedValues"]["core-entra-required-scope"], "Gateway.Next")
        self.assertEqual(verified["entraAuthentication"]["requiredScope"], "Gateway.Next")

    def test_manifest_readback_receives_independent_typed_entra_expectations(self):
        current, transport = fixture()
        _, plan, _ = configured()
        current.policies[GLOBAL] = xml("named").replace("<inbound>", "<inbound>" + "".join(
            '<set-variable name="fixture" value="{{' + name + '}}" />' for name in ENTRA_NAMED_VALUES))
        transport.refresh(GLOBAL, {"value": current.policies[GLOBAL]})
        plan["parameters"]["names"]["value"]["keyVault"] = "fixture"
        for name, value in plan["entraNamedValues"].items():
            if name == "core-entra-user-limits": value = json.dumps(json.loads(value), indent=2)
            transport.refresh(APIM + "/namedValues/" + name, {"displayName": name, "secret": False, "value": value})
        complete = publication.complete_named_value_expectations(current, plan, transport)
        publication.read_policy_graph(complete, transport)
        self.assertEqual(len(set(complete.named_values) - set(current.named_values)), 4)
        transport.inventory[APIM + "/namedValues/core-entra-user-limits"]["body"]["properties"]["value"] = "{}"
        with self.assertRaises(ValueError):
            publication.read_policy_graph(complete, transport)

    def test_api_source_cannot_replace_independent_foundation_values(self):
        _, plan, _, source, global_policy, _ = checkpoint_fixture()
        source["namedValues"].append({"suffix": "/namedValues/core-entra-required-scope",
                                      "properties": {"displayName": "core-entra-required-scope", "secret": False, "value": "Foreign.Scope"}})
        with self.assertRaisesRegex(ValueError, "conflict"):
            publish.source_checkpoint(plan, source, global_policy)


class EntraNamedValueInfrastructureTests(unittest.TestCase):
    def resources(self, template):
        resources = template["resources"]
        return list(resources.values()) if isinstance(resources, dict) else resources

    def module(self, template, expected):
        return next(resource for resource in self.resources(template)
                    if isinstance(resource.get("properties"), dict) and resource["properties"].get("template") == expected)

    def test_shared_module_compiles_only_four_nonsecret_named_values_from_existing_config(self):
        template = compile_bicep("apps/gateway/deploy/entra-named-values.bicep")
        _, plan, _ = configured()
        entra = {key: value for key, value in plan["entraAuthentication"].items() if key in ("audience", "requiredScope", "userLimits")}
        parameters = {"apimName": "fixture", "tenantId": plan["entraTenantId"], "entra": entra}
        expression = PolicyExpression("[variables('settings')]", template.get("variables", {}))
        settings = expression.evaluate(parameters)
        self.assertEqual(validate_entra_named_values(settings), plan["entraNamedValues"])
        writable = [resource for resource in self.resources(template) if resource.get("existing") is not True]
        self.assertEqual(len(writable), 1)
        resource = writable[0]
        self.assertEqual(resource["type"], "Microsoft.ApiManagement/service/namedValues")
        self.assertEqual(expression.resolve(resource["copy"]["count"], parameters), 4)
        actual = {}
        for index in range(4):
            name = expression.resolve(resource["name"], parameters, index).rsplit("/", 1)[-1]
            properties = expression.resolve(resource["properties"], parameters, index)
            self.assertIs(properties["secret"], False)
            self.assertEqual(properties["displayName"], name)
            self.assertNotIn("keyVault", properties)
            actual[name] = properties["value"]
        self.assertEqual(validate_entra_named_values(actual), plan["entraNamedValues"])

    def test_formal_entrances_use_same_module_before_api_publication(self):
        shared = compile_bicep("apps/gateway/deploy/entra-named-values.bicep")
        foundation = compile_bicep("infra/main.bicep")
        gateway = compile_bicep("apps/gateway/deploy/main.bicep")
        settings = compile_bicep("apps/gateway/deploy/apim-settings.bicep")
        foundation_entry = self.module(foundation, shared)
        gateway_entry = self.module(gateway, settings)
        settings_entry = self.module(settings, shared)
        self.assertTrue(any("services" in value for value in foundation_entry["dependsOn"]))
        self.assertIn("deployCore", foundation_entry["condition"])
        self.assertIn("entra", foundation_entry["condition"])
        self.assertIn("deployApplication", gateway_entry["condition"])
        self.assertIn("entra", settings_entry["condition"])
        for entry in (foundation_entry, gateway_entry):
            parameter = "tenantId" if entry is foundation_entry else "entraTenantId"
            self.assertIn("entraTenantId", entry["properties"]["parameters"][parameter]["value"])
            self.assertIn("azureTenantId", entry["properties"]["parameters"][parameter]["value"])


if __name__ == "__main__":
    unittest.main()
