"""独立した合成生成元で、過去policyの実読取りと現行scopeの検査を確認する。"""
import copy
from dataclasses import replace
import unittest
import xml.etree.ElementTree as ET

from test_entra_publication import APIM, fixture, publication


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


class HistoricalPolicyGraphTests(unittest.TestCase):
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
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
