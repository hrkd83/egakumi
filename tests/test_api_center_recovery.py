"""API Centerの同名復元を合成ARM応答で検証する。Azure要求は実行しない。"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import fixture
from lifecycle_fixtures import ScriptedTransport


def response(status=200, body=None):
    """秘密や実IDを使わない、状態コード付きの合成HTTP応答を作る。"""
    return {"statusCode": status, "headers": {}, "body": body}


class ApiCenterRecoveryTests(unittest.TestCase):
    """今回のexact IDだけを復元し、曖昧な状態は成功にしない。"""

    def setUp(self):
        self.config = normalize_configuration(fixture(), base_directory=Path(__file__).resolve().parents[1] / "config")
        self.name = "apic-" + self.config["resourceStem"]
        self.outputs = {"names": {"apiCenter": self.name}}
        self.scope = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                      f"{self.config['resourceGroupNames']['svc']}/providers/Microsoft.ApiCenter")
        self.service_id = self.scope + "/services/" + self.name
        self.list_path = self.scope + "/deletedServices?api-version=2024-06-01-preview"
        self.live = {"id": self.service_id, "name": self.name, "type": "Microsoft.ApiCenter/services",
                     "location": self.config["apiCenterLocation"],
                     "tags": {"product": self.config["productName"], "environment": self.config["environmentName"],
                              "azd-env-name": self.config["environmentName"]},
                     "properties": {"provisioningState": "Succeeded"}}
        self.deleted = {"id": self.scope + "/deletedServices/" + self.name, "name": self.name,
                        "type": "Microsoft.ApiCenter/deletedServices"}
        # 単体試験がCLI・実HTTP・待機へ漏れたら、その場で失敗させる。
        for target in ("run", "ArmTransport"):
            patcher = mock.patch.object(lifecycle, target, side_effect=AssertionError("No real Azure operation permitted"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(lifecycle.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, responses, outputs=None):
        """要求を捕捉する偽transportだけを使い、結果と全要求を返す。"""
        transport = ScriptedTransport(responses)
        result = lifecycle.ensure_api_center_available(self.config, outputs or self.outputs, transport)
        return result, transport

    def test_fresh_environment_is_left_to_bicep_without_any_mutation(self):
        result, transport = self.invoke([response(404), response(body={"value": []})])
        self.assertEqual(result["status"], "available-for-create")
        self.assertFalse(transport.mutations())
        self.assertEqual(transport.calls[1]["path"], "https://management.azure.com" + self.list_path)

    def test_existing_owned_service_is_not_put_or_listed(self):
        result, transport = self.invoke([response(body=self.live)])
        self.assertEqual(result["status"], "existing")
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(transport.mutations())

    def test_matching_tombstone_restores_only_exact_id_then_reads_actual_service(self):
        result, transport = self.invoke([response(404), response(body={"value": [self.deleted]}),
                                         response(202), response(body=self.live)])
        self.assertEqual(result["status"], "restored")
        self.assertEqual(transport.mutations(), [{"method": "PUT", "path": self.service_id + "?api-version=2024-06-01-preview",
            "body": {"location": self.config["apiCenterLocation"], "properties": {"restore": True}}, "headers": None}])
        self.assertEqual(transport.calls[-1]["method"], "GET")
        self.assertEqual(transport.calls[-1]["path"], self.service_id + "?api-version=2024-03-01")

    def test_other_deleted_service_is_never_restored(self):
        other = {**self.deleted, "id": self.scope + "/deletedServices/another-service", "name": "another-service"}
        result, transport = self.invoke([response(404), response(body={"value": [other]})])
        self.assertEqual(result["status"], "available-for-create")
        self.assertFalse(transport.mutations())

    def test_all_pages_are_verified_before_matching_service_is_restored(self):
        next_link = "https://management.azure.com" + self.list_path + "&$skiptoken=fixture"
        result, transport = self.invoke([response(404), response(body={"value": [self.deleted], "nextLink": next_link}),
                                         response(body={"value": []}), response(200), response(body=self.live)])
        self.assertEqual(result["status"], "restored")
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "GET", "GET", "PUT", "GET"])
        self.assertEqual(transport.calls[2]["path"], next_link)

    def test_external_wrong_scope_cycle_and_malformed_continuation_stop_before_put(self):
        links = ["https://example.invalid/steal", self.list_path.replace("rg-", "other-rg-", 1),
                 "https://management.azure.com" + self.list_path, self.list_path.replace("2024-06-01-preview", "2024-03-15-preview"),
                 "https://management.azure.com:bad" + self.list_path, 17, ""]
        for link in links:
            with self.subTest(link=link):
                transport = ScriptedTransport([response(404), response(body={"value": [self.deleted], "nextLink": link})])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
                self.assertEqual(len(transport.calls), 2)
                self.assertFalse(transport.mutations())

    def test_malformed_missing_and_duplicate_list_entries_fail_closed(self):
        bad_pages = [{}, {"value": None}, {"value": [None]},
                     {"value": [{**self.deleted, "id": self.deleted["id"].replace("/resourceGroups/", "/other/")}]},
                     {"value": [{**self.deleted, "type": "Microsoft.ApiManagement/deletedServices"}]},
                     {"value": [self.deleted, self.deleted]}]
        for page in bad_pages:
            with self.subTest(page=page):
                transport = ScriptedTransport([response(404), response(body=page)])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
                self.assertFalse(transport.mutations())

    def test_wrong_live_id_name_type_location_or_ownership_never_allows_put(self):
        bad_live = [{**self.live, field: value} for field, value in [
            ("id", self.service_id + "-other"), ("name", "another-service"),
            ("type", "Microsoft.ApiManagement/service"), ("location", "westus"), ("tags", {}),
            ("properties", {}), ("properties", {"provisioningState": "Failed"})]]
        for live in bad_live:
            with self.subTest(live=live):
                transport = ScriptedTransport([response(body=live)])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
                self.assertFalse(transport.mutations())

    def test_http_errors_do_not_turn_into_missing_or_success(self):
        scenarios = [[response(403)], [response(404), response(502)],
                     [response(404), response(body={"value": [self.deleted]}), response(400)],
                     [response(404), response(body={"value": [self.deleted]}), response(202), response(403)]]
        for responses in scenarios:
            with self.subTest(statuses=[r["statusCode"] for r in responses]):
                transport = ScriptedTransport(responses)
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
                self.assertLessEqual(len(transport.mutations()), 1)

    def test_restore_success_requires_readback_ownership_and_terminal_state(self):
        for actual in (None, {**self.live, "tags": {}}, {**self.live, "properties": {"provisioningState": "Failed"}}):
            with self.subTest(actual=actual):
                transport = ScriptedTransport([response(404), response(body={"value": [self.deleted]}),
                                               response(200), response(body=actual)])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
                self.assertEqual(len(transport.mutations()), 1)

    def test_asynchronous_restore_polls_only_the_exact_service(self):
        for state in ("Accepted", "Creating", "Updating"):
            with self.subTest(state=state):
                self.sleep.reset_mock()
                pending = copy.deepcopy(self.live)
                pending["properties"]["provisioningState"] = state
                result, transport = self.invoke([response(404), response(body={"value": [self.deleted]}), response(202),
                                                 response(404), response(body=pending), response(body=self.live)])
                self.assertEqual(result["status"], "restored")
                self.assertEqual(len(transport.mutations()), 1)
                self.assertEqual(self.sleep.call_count, 2)
                self.assertEqual({call["path"] for call in transport.calls[3:]}, {self.service_id + "?api-version=2024-03-01"})

    def test_restore_timeout_does_not_issue_a_second_put(self):
        transport = ScriptedTransport([response(404), response(body={"value": [self.deleted]}), response(202)]
                                      + [response(404)] * 60)
        with self.assertRaisesRegex(lifecycle.DeploymentError, "still pending"):
            lifecycle.ensure_api_center_available(self.config, self.outputs, transport)
        self.assertEqual(len(transport.mutations()), 1)
        self.assertEqual(self.sleep.call_count, 59)

    def test_missing_or_foreign_bicep_name_stops_before_any_read(self):
        for names in (None, "unexpected", {}, {"apiCenter": "apic-another-product-123456"}):
            with self.subTest(names=names):
                transport = ScriptedTransport([])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.ensure_api_center_available(self.config, {"names": names}, transport)
                self.assertFalse(transport.calls)


if __name__ == "__main__":
    unittest.main()
