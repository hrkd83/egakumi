"""独立した合成生成元で、過去policyの実読取りと現行scopeの検査を確認する。"""
import copy
import base64
from dataclasses import replace
import json
import unittest
import xml.etree.ElementTree as ET

from test_entra_publication import APIM, GLOBAL, fixture, publication, source_fixture
from test_common_authentication_fragments import authentication_graph
from test_entra_gateway_authentication import settings as entra_settings
from deployment.helpers.apim_authentication import (
    _render_inline_common_authentication_service_policy, entra_authentication_named_values,
)


def historical_fixture():
    current, transport = fixture()
    policies = copy.deepcopy(current.policies)
    resource_id = APIM + "/policyFragments/core-api-settings"
    root = ET.fromstring(policies[resource_id])
    variable = root.find("set-variable[@name='gatewayAuditSettings']")
    # 現行の固定生成元とは異なる、合成の過去生成元を独立したコメントで表す。
    variable.set("value", variable.get("value").replace(
        "@{", "@{ /* synthetic historical generation */", 1))
    policies[resource_id] = ET.tostring(root, encoding="unicode")
    baseline = replace(current, policies=policies)
    transport.refresh(resource_id, {"format": "rawxml", "value": policies[resource_id]})
    return baseline, current, transport, resource_id


def mixed_global_fixture():
    """旧inline認証と、新しい操作エラー部品が混在する意図したstagingを作る。"""
    desired, transport = authentication_graph()
    for name, value in entra_authentication_named_values(entra_settings()).items():
        resource_id = APIM + "/namedValues/" + name
        desired.named_values[resource_id] = {"displayName": name, "secret": False, "value": value}
        transport.refresh(resource_id, desired.named_values[resource_id])
    api_id = APIM + "/apis/model-a"
    root = ET.fromstring(desired.policies[api_id + "/policies/policy"])
    seed = "http\n{}\n" + json.dumps({"operationConstraints": {"responses": {"kind": "pii"}}}, separators=(",", ":"))
    root.find("inbound/set-variable[@name='gatewayApiSettingsSource']").set("value", base64.b64encode(seed.encode()).decode())
    desired.policies[api_id + "/policies/policy"] = ET.tostring(root, encoding="unicode")
    operation = api_id + "/operations/responses/policies/policy"
    desired.policies[operation] = ('<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound>'
        '<on-error><include-fragment id="core-entra-authentication-error" fragment-id="core-entra-authentication-error" />'
        '<return-response><set-status code="502" reason="Synthetic downstream error" /></return-response></on-error></policies>')
    old_policies = copy.deepcopy(desired.policies)
    old_policies[GLOBAL] = _render_inline_common_authentication_service_policy(entra=entra_settings())
    root = ET.fromstring(old_policies[operation])
    error = root.find("on-error")
    error.remove(error.find("include-fragment"))
    old_policies[operation] = ET.tostring(root, encoding="unicode")
    baseline = replace(desired, policies=old_policies)
    for resource_id, value in baseline.policies.items():
        transport.refresh(resource_id, {"format": "rawxml", "value": value})
    transport.members = set(desired.operations)
    transport.sync_members()
    transport.calls.clear()
    return baseline, desired, transport, operation


class HistoricalPolicyGraphTests(unittest.TestCase):
    def test_inline_global_can_be_replaced_after_current_operation_handlers_are_verified(self):
        baseline, desired, transport, operation = mixed_global_fixture()
        def install():
            for resource_id, value in desired.policies.items():
                if resource_id != GLOBAL:
                    transport.refresh(resource_id, {"format": "rawxml", "value": value})
            mixed = replace(desired, policies={**desired.policies, GLOBAL: baseline.policies[GLOBAL]})
            with self.assertRaisesRegex(ValueError, "active common validator"):
                publication.read_policy_graph(mixed, transport)
            return desired
        result = publication.transition(baseline, desired.policies[GLOBAL], transport, install,
            preflight_scope_manifest=desired, previous_open_api_ids=set(baseline.operations),
            desired_open_api_ids=set(desired.operations), sleep=lambda _: None)
        self.assertTrue(result["managementPlaneVerified"])
        self.assertTrue(result["employeePathOpen"])
        self.assertEqual(transport.inventory[GLOBAL]["body"]["properties"]["value"], desired.policies[GLOBAL])
        self.assertEqual(transport.inventory[operation]["body"]["properties"]["value"], desired.policies[operation])
        publication.read_policy_graph(desired, transport)

    def test_mixed_stage_rejects_invalid_current_handler_fragment_and_old_global_drift(self):
        for fault in ("handler", "fragment", "old-global-etag"):
            with self.subTest(fault=fault):
                baseline, desired, transport, operation = mixed_global_fixture()
                def install():
                    for resource_id, value in desired.policies.items():
                        if resource_id != GLOBAL:
                            transport.refresh(resource_id, {"format": "rawxml", "value": value})
                    if fault == "handler":
                        policies = copy.deepcopy(desired.policies)
                        root = ET.fromstring(policies[operation])
                        root.find("on-error/include-fragment").set("id", "incorrect-handler")
                        policies[operation] = ET.tostring(root, encoding="unicode")
                        return replace(desired, policies=policies)
                    if fault == "fragment":
                        resource_id = APIM + "/policyFragments/core-api-settings"
                        root = ET.fromstring(desired.policies[resource_id])
                        root.set("unexpected", "value")
                        transport.refresh(resource_id, {"format": "rawxml", "value": ET.tostring(root, encoding="unicode")})
                    else:
                        transport.refresh(GLOBAL, {"format": "rawxml", "value": baseline.policies[GLOBAL]})
                    return desired
                with self.assertRaises(publication.TransitionError) as caught:
                    publication.transition(baseline, desired.policies[GLOBAL], transport, install,
                        preflight_scope_manifest=desired, previous_open_api_ids=set(baseline.operations),
                        desired_open_api_ids=set(desired.operations), sleep=lambda _: None)
                self.assertTrue(caught.exception.employee_path_closed)
                self.assertFalse(transport.members)
                self.assertFalse(any(method == "PUT" and resource_id == GLOBAL for method, resource_id, *_ in transport.calls))

    def test_reconciliation_passes_current_scope_to_its_following_transition(self):
        baseline, desired, transport, _ = mixed_global_fixture()
        children = {}
        for api_id, names in desired.operations.items():
            desired.api_properties[api_id]["protocols"] = ["http", "https"]
            transport.refresh(api_id, desired.api_properties[api_id])
            for kind in ("tools", "schemas"):
                transport.refresh(api_id + "/" + kind, value=[])
            for name in names:
                resource_id = api_id + "/operations/" + name
                children[resource_id] = {"method": "POST", "urlTemplate": "/responses"}
                transport.refresh(resource_id, children[resource_id])
        desired = replace(desired, source_version=2, api_children=children)
        baseline = replace(baseline, source_version=2, api_children=children)
        snapshot = publication.read_policy_graph(baseline, transport)
        product_snapshot, members = publication.read_open_product(APIM, transport, allowed_api_ids=set(baseline.operations))
        snapshot.update(product_snapshot)
        inventory = publication.ReconciliationInventory(APIM, snapshot, frozenset(baseline.operations),
            frozenset(baseline.operations), frozenset(members))
        backups, checkpoints = [], []
        def install():
            for resource_id, value in desired.policies.items():
                if resource_id != GLOBAL:
                    transport.refresh(resource_id, {"format": "rawxml", "value": value})
            return desired
        result = publication.reconcile_closed_publication(inventory, desired.policies[GLOBAL], transport, install,
            desired_open_api_ids=set(desired.operations), backup=backups.append,
            on_staged=lambda: checkpoints.append(True), sleep=lambda _: None)
        self.assertTrue(result["sourceReconciled"])
        self.assertTrue(result["employeePathOpen"])
        self.assertEqual(len(backups), 1)
        self.assertEqual(checkpoints, [True])
        self.assertEqual(transport.inventory[GLOBAL]["body"]["properties"]["value"], desired.policies[GLOBAL])
        publication.read_policy_graph(desired, transport)

    def test_source_parsing_keeps_historical_xml_and_requires_valid_current_scope(self):
        baseline, current, _, resource_id = historical_fixture()
        source = source_fixture(baseline)
        with self.assertRaises(ValueError):
            publication.source_manifest(source, APIM, ["model-a", "model-b"], baseline.policies[GLOBAL])
        restored = publication.source_manifest(source, APIM, ["model-a", "model-b"], baseline.policies[GLOBAL],
                                               scope_manifest=current)
        self.assertEqual(restored, baseline)
        self.assertEqual(restored.policies[resource_id], baseline.policies[resource_id])
        with self.assertRaises(ValueError):
            publication.source_manifest(source, APIM, ["model-a", "model-b"], baseline.policies[GLOBAL],
                                        scope_manifest=baseline)

    def test_transition_uses_scope_only_before_staging_and_checks_updated_source_itself(self):
        baseline, current, transport, resource_id = historical_fixture()
        staged = []
        def install():
            staged.append(True)
            transport.refresh(resource_id, {"format": "rawxml", "value": current.policies[resource_id]})
            return current
        result = publication.transition(baseline, current.policies[GLOBAL], transport, install,
                                        preflight_scope_manifest=current, sleep=lambda _: None)
        self.assertTrue(result["managementPlaneVerified"])
        self.assertEqual(staged, [True])
        self.assertEqual(transport.inventory[resource_id]["body"]["properties"]["value"], current.policies[resource_id])
        baseline, current, transport, _ = historical_fixture()
        with self.assertRaises(publication.TransitionError):
            publication.transition(baseline, current.policies[GLOBAL], transport, lambda: baseline,
                                   preflight_scope_manifest=current, sleep=lambda _: None)

    def test_historical_xml_is_read_exactly_while_current_scope_is_validated(self):
        baseline, current, transport, resource_id = historical_fixture()
        with self.assertRaises(ValueError):
            publication.read_policy_graph(baseline, transport)
        snapshot = publication.read_policy_graph(baseline, transport, scope_manifest=current)
        self.assertEqual(snapshot[resource_id][2]["body"]["properties"]["value"], baseline.policies[resource_id])
        publication.verify_snapshot(snapshot, transport)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_matching_current_xml_cannot_replace_historical_readback(self):
        baseline, current, transport, resource_id = historical_fixture()
        transport.refresh(resource_id, {"format": "rawxml", "value": current.policies[resource_id]})
        with self.assertRaisesRegex(ValueError, "Raw policy readback differs"):
            publication.read_policy_graph(baseline, transport, scope_manifest=current)

    def test_foreign_or_changed_nonpolicy_scope_is_rejected_before_reads(self):
        baseline, current, transport, _ = historical_fixture()
        changes = {
            "apim_id": APIM + "-foreign",
            "operations": {},
            "products": {},
            "api_properties": {},
            "api_children": {APIM + "/apis/model-a/schemas/extra": {}},
            "backends": {},
            "source_version": 2,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "same complete current API scope"):
                    publication.read_policy_graph(baseline, transport, scope_manifest=replace(current, **{field: value}))
                with self.assertRaisesRegex(ValueError, "same complete current API scope"):
                    publication.source_manifest(source_fixture(baseline), APIM, ["model-a", "model-b"],
                        baseline.policies[GLOBAL], scope_manifest=replace(current, **{field: value}))
                with self.assertRaisesRegex(ValueError, "same complete current API scope"):
                    publication.transition(baseline, current.policies[GLOBAL], transport,
                        lambda: self.fail("Invalid scope must not stage"),
                        preflight_scope_manifest=replace(current, **{field: value}), sleep=lambda _: None)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
