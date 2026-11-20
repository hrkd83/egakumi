"""退役するmode設定を生成せず、既存manifestの一般照合は維持する。"""
import unittest

from test_entra_publication import APIM, fixture, response
import entra_publication as publication

RETIRED_ID = "core-query-guard-modes"


class QueryObservationNamedValueRetirementTests(unittest.TestCase):
    def graph(self, value):
        manifest, transport = fixture()
        identifier = APIM + "/namedValues/" + RETIRED_ID
        expected = {"displayName": RETIRED_ID, "secret": False, "value": '{"liveCreate":"reject"}'}
        manifest.named_values[identifier] = expected
        # 退役前の保存sourceは旧fragmentとNVを参照するままです。
        # 新stageに生成されないNVも、適用中グラフの一般dependency照合から読みます。
        fragment_id = APIM + "/policyFragments/core-query-guard"
        fragment = '<fragment><set-variable name="gatewayQueryGuardModes" value="{{' + RETIRED_ID + '}}" /></fragment>'
        manifest.policies[fragment_id] = fragment
        transport.inventory[fragment_id] = response(fragment_id, {"format": "rawxml", "value": fragment})
        global_id = APIM + "/policies/policy"
        global_policy = manifest.policies[global_id].replace(
            "</inbound>", '<include-fragment fragment-id="core-query-guard" /></inbound>')
        manifest.policies[global_id] = global_policy
        transport.inventory[global_id] = response(global_id, {"format": "rawxml", "value": global_policy})
        transport.inventory[identifier] = response(identifier, value)
        return manifest, transport

    def test_predecessor_manifest_still_checks_retired_named_values_as_general_resources(self):
        expected = {"displayName": RETIRED_ID, "secret": False, "value": '{"liveCreate":"reject"}'}
        manifest, transport = self.graph(expected)
        snapshot = publication.read_policy_graph(manifest, transport)
        self.assertIn(APIM + "/namedValues/" + RETIRED_ID, snapshot)
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))
        for change in ({"value": '{"liveCreate":"observe"}'}, {"secret": True}, {"displayName": "changed"}):
            actual = {**expected, **change}
            manifest, transport = self.graph(actual)
            with self.subTest(change=change), self.assertRaises(ValueError):
                publication.read_policy_graph(manifest, transport)
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))


if __name__ == "__main__":
    unittest.main()
