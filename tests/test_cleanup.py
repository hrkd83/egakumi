"""モデル→APIM→RGの撤去順と、保護対象を残す動作を検証する。

偽のARM応答で削除・一覧の全ページ・404・WORM保持・Key Vault保護・完全削除の対象IDを再現する。成功を装わず、処理を止める必要があるケースも確認する。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import cleanup
from deployment.helpers.configuration import load_configuration


from cleanup_fixtures import (
    CleanupFixture, FakeTransport, ApiCenterListTransport, ModelTransport,
    CredentialTransport, ProviderGetInconsistencyTransport, InventoryTransport, ManagementTransport,
)


class CleanupTests(CleanupFixture, unittest.TestCase):
    def test_missing_or_other_apim_generation_rejects_state_before_read_or_delete(self):
        for value in (None, "def456"):
            with self.subTest(value=value):
                original = self.state.pop("apimInstanceId")
                if value is not None:
                    self.state["apimInstanceId"] = value
                try:
                    transport = FakeTransport()
                    self.assert_code("state-environment-mismatch", lambda: self.pre(transport))
                    self.assertEqual(transport.calls, [])
                finally:
                    self.state["apimInstanceId"] = original

    def test_test_unlocked_policy_removal_has_exact_etag_and_distinct_zero_retention(self):
        transport = FakeTransport({resource_id: self.policy(resource_id) for resource_id in self.policy_ids})
        result = self.pre(transport)
        self.assertEqual("ready-for-azd-down", result["status"])
        self.assertEqual(["retention-removed"] * 2, [item["status"] for item in result["policies"]])
        self.assertTrue(all(call["headers"] == {"If-Match": '"version-1"'} for call in transport.mutations()))

    def test_deleted_zero_metadata_proceeds_without_delete_or_etag_and_is_not_404(self):
        resources = {}
        for resource_id in self.policy_ids:
            policy = self.policy(resource_id, mode="Deleted", etag="")
            policy["properties"]["immutabilityPeriodSinceCreationInDays"] = 0
            resources[resource_id] = policy
        transport = FakeTransport(resources)
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        self.assertFalse(transport.mutations())
        self.assertEqual(result["policies"], [{"resourceId": resource_id, "status": "retention-removed", "httpStatus": 200,
                                              "azureState": "Deleted"} for resource_id in self.policy_ids])

    def test_deleted_zero_requires_own_id_exact_integer_and_known_state(self):
        for period in (None, 1, -1, False, 0.0, "0"):
            with self.subTest(period=period):
                policy = self.policy(self.policy_ids[0], mode="Deleted", etag="")
                if period is None:
                    policy["properties"].pop("immutabilityPeriodSinceCreationInDays")
                else:
                    policy["properties"]["immutabilityPeriodSinceCreationInDays"] = period
                transport = FakeTransport({self.policy_ids[0]: policy})
                self.assert_code("unknown-retention", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
        for mode, expected in (("Locked", "locked-retention"), ("Unknown", "unknown-retention")):
            policy = self.policy(self.policy_ids[0], mode=mode, etag="")
            policy["properties"]["immutabilityPeriodSinceCreationInDays"] = 0
            transport = FakeTransport({self.policy_ids[0]: policy})
            self.assert_code(expected, lambda: self.pre(transport))
            self.assertFalse(transport.mutations())
        policy = self.policy(self.policy_ids[0] + "-foreign", mode="Deleted", etag="")
        policy["properties"]["immutabilityPeriodSinceCreationInDays"] = 0
        transport = FakeTransport({self.policy_ids[0]: policy})
        self.assert_code("resource-mismatch", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_delete_readback_accepts_deleted_zero_metadata_as_http200(self):
        transport = FakeTransport({resource_id: self.policy(resource_id) for resource_id in self.policy_ids})
        transport.policy_state_after_delete = "Deleted"
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        self.assertEqual(len(transport.mutations()), 2)
        self.assertTrue(all(call["headers"] == {"If-Match": '"version-1"'} for call in transport.mutations()))
        self.assertTrue(all(item["status"] == "retention-removed" and item["httpStatus"] == 200 and item["azureState"] == "Deleted"
                            for item in result["policies"]))

    def test_late_locked_policy_refuses_before_any_mutation(self):
        transport = FakeTransport({self.policy_ids[0]: self.policy(self.policy_ids[0]), self.policy_ids[1]: self.policy(self.policy_ids[1], "Locked")})
        self.assert_code("locked-retention", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_configured_retention_never_removes_unlocked_policy(self):
        self.raw["monitoring"]["auditImmutabilityMode"] = "Unlocked"
        self.write_configuration()
        transport = FakeTransport({self.policy_ids[0]: self.policy(self.policy_ids[0])})
        self.assert_code("configured-retention", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_missing_etag_refuses_without_wildcard(self):
        transport = FakeTransport({self.policy_ids[0]: self.policy(self.policy_ids[0], etag=None)})
        self.assert_code("missing-etag", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_own_subscription_diagnostic_removed_after_destination_verification(self):
        transport = FakeTransport({self.diag_id: {"id": self.diag_id, "properties": {"workspaceId": self.workspace_id, "storageAccountId": self.state["storage"]["collectJapan"]["id"]}}})
        result = self.pre(transport)
        self.assertEqual(404, result["activityDiagnostic"]["httpStatus"])
        self.assertEqual([self.diag_id], [call["id"] for call in transport.mutations()])

    def test_foreign_diagnostic_destination_is_preserved(self):
        transport = FakeTransport({self.diag_id: {"id": self.diag_id, "properties": {"workspaceId": self.workspace_id + "-foreign"}}})
        self.assert_code("diagnostic-owner-mismatch", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_wrong_environment_state_makes_zero_transport_calls(self):
        self.state["subscriptionId"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        transport = FakeTransport()
        self.assert_code("state-environment-mismatch", lambda: self.pre(transport))
        self.assertFalse(transport.calls)

    def test_foreign_policy_is_not_a_deletion_target(self):
        self.state["storage"]["immutabilityPolicyIds"] = [self.policy_ids[0].replace("rg-test-egakumi-monitor", "rg-other")]
        transport = FakeTransport()
        self.assert_code("invalid-state", lambda: self.pre(transport))
        self.assertFalse(transport.calls)

    def test_policy_inventory_rejects_non_string(self):
        self.state["storage"]["immutabilityPolicyIds"] = [{}]
        transport = FakeTransport()
        self.assert_code("invalid-state", lambda: self.pre(transport))
        self.assertFalse(transport.calls)

    def test_postdown_purges_only_three_exact_services_and_never_requests_role_definitions(self):
        resources = self.tombstones()
        transport = FakeTransport(resources)
        result = self.post(transport)
        self.assertEqual("complete", result["status"])
        self.assertTrue(all(item["httpStatus"] == 404 for item in result["resourceGroups"] + result["softDeletedServices"]))
        mutations = transport.mutations()
        self.assertEqual(
            [("POST" if "Microsoft.KeyVault/" in target else "DELETE",
              target + "/purge" if "Microsoft.KeyVault/" in target else target) for target in resources],
            [(call["method"], call["id"]) for call in mutations],
        )
        expected_reads = ({cleanup._rg_id(self.configuration, kind) for kind in ("id", "nw", "monitor", "svc", "maint")}
                          | set(resources) | {self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"})
        self.assertTrue(all(call["id"] in expected_reads for call in transport.calls if call["method"] == "GET"))
        self.assertEqual(set(result), {"phase", "status", "resourceGroups", "applicationInfrastructure",
                                       "softDeletedServices", "apiCenterRetention"})
        self.assertTrue(all("/roleDefinitions/" not in call["id"] for call in transport.calls))
        self.assertTrue(all("graph.microsoft" not in call["id"] for call in transport.calls))

    def test_api_center_empty_list_is_verified_absence_without_purge(self):
        transport = FakeTransport()
        result = self.post(transport)
        self.assertEqual(result["status"], "complete")
        retention = result["apiCenterRetention"]
        self.assertEqual(retention["status"], "absent")
        self.assertEqual(retention["lookup"], "resource-group-deletedServices-list")
        self.assertTrue(retention["listReadVerified"])
        self.assertFalse(retention["purgeRequested"])
        self.assertFalse(transport.mutations())
        self.assertEqual(set(cleanup.SERVICE_TYPES), {"keyVault", "foundry", "apim"})

    def test_api_center_retention_records_real_deadlines_without_changing_it(self):
        tombstone = self.api_center_tombstone()
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        transport = FakeTransport({**self.tombstones(), collection: {"value": [tombstone]}})
        result = self.post(transport)
        self.assertEqual(result["status"], "complete-with-api-center-retention")
        retention = result["apiCenterRetention"]
        self.assertEqual(retention["status"], "retained-soft-delete")
        self.assertEqual(retention["softDeletionDate"], tombstone["properties"]["softDeletionDate"])
        self.assertEqual(retention["scheduledPurgeDate"], tombstone["properties"]["scheduledPurgeDate"])
        self.assertEqual(retention["deletedResourceId"], tombstone["id"])
        self.assertFalse(retention["purgeRequested"])
        api_center_calls = [c for c in transport.calls if "Microsoft.ApiCenter" in c["id"]]
        self.assertEqual([(c["method"], c["id"]) for c in api_center_calls], [("GET", collection)])
        self.assertEqual(len(transport.mutations()), 3)

    def test_api_center_other_deleted_names_are_not_reported_as_ours_or_changed(self):
        tombstone = self.api_center_tombstone()
        tombstone["id"] = tombstone["id"].rsplit("/", 1)[0] + "/another-service"
        tombstone["name"] = "another-service"
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        transport = FakeTransport({collection: {"value": [tombstone]}})
        result = self.post(transport)
        self.assertEqual(result["apiCenterRetention"]["status"], "absent")
        self.assertFalse(transport.mutations())

    def test_api_center_retention_is_recorded_only_after_all_safe_pages(self):
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        next_link = "https://management.azure.com" + collection + "?api-version=2024-06-01-preview&$skiptoken=fixture"
        transport = ApiCenterListTransport(collection, [
            {"value": [self.api_center_tombstone()], "nextLink": next_link}, {"value": []}])
        result = self.post(transport)
        self.assertEqual(result["apiCenterRetention"]["status"], "retained-soft-delete")
        self.assertFalse(transport.pages)
        self.assertEqual(sum(c["id"] == collection for c in transport.calls), 2)
        self.assertFalse(transport.mutations())

    def test_api_center_unsafe_or_cyclic_pagination_is_unverified(self):
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        path = collection + "?api-version=2024-06-01-preview"
        for next_link in ("https://example.invalid/steal", path.replace("rg-test-egakumi", "rg-other"),
                          "https://management.azure.com" + path, "https://management.azure.com:bad" + path,
                          path.replace("2024-06-01-preview", "2024-03-15-preview"), "", 17):
            with self.subTest(next_link=next_link):
                transport = ApiCenterListTransport(collection, [
                    {"value": [self.api_center_tombstone()], "nextLink": next_link}], self.tombstones())
                result = self.post(transport)
                self.assertEqual(result["apiCenterRetention"]["status"], "unverified")
                self.assertFalse(result["apiCenterRetention"]["listReadVerified"])
                self.assertEqual(result["apiCenterRetention"]["reasonCode"], "api-center-retention-unverified")
                self.assertEqual(sum(c["id"] == collection for c in transport.calls), 1)
                self.assertEqual(len(transport.mutations()), 3)

    def test_api_center_mismatches_missing_deadlines_and_bad_pages_are_unverified(self):
        tombstone = self.api_center_tombstone()
        wrong_id = {**tombstone, "id": tombstone["id"].replace("rg-test-egakumi", "rg-other")}
        wrong_type = {**tombstone, "type": "Microsoft.ApiManagement/deletedServices"}
        wrong_name = {**tombstone, "name": "another-service"}
        missing_dates = {**tombstone, "properties": {}}
        invalid_dates = {**tombstone, "properties": {"softDeletionDate": "unknown", "scheduledPurgeDate": "unknown"}}
        pages = [{}, {"value": None}, {"value": [None]}, {"value": [wrong_id]}, {"value": [wrong_type]},
                 {"value": [wrong_name]}, {"value": [missing_dates]}, {"value": [invalid_dates]},
                 {"value": [tombstone, tombstone]}]
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        for page in pages:
            with self.subTest(page=page):
                transport = ApiCenterListTransport(collection, [page], self.tombstones())
                result = self.post(transport)
                self.assertEqual(result["apiCenterRetention"]["status"], "unverified")
                self.assertFalse(result["apiCenterRetention"]["purgeRequested"])
                self.assertEqual(len(transport.mutations()), 3)
                self.assertTrue(all("Microsoft.ApiCenter" not in c["id"] for c in transport.mutations()))

    def test_api_center_missing_bicep_name_never_guesses_a_name_or_blocks_other_purges(self):
        for names in (None, {}, {"apiCenter": "apic-foreign-test-abc123"}):
            with self.subTest(names=names):
                self.state["outputs"]["names"] = names
                transport = FakeTransport(self.tombstones())
                result = self.post(transport)
                self.assertEqual(result["apiCenterRetention"]["status"], "unverified")
                self.assertEqual(len(transport.mutations()), 3)
                self.assertFalse(any("Microsoft.ApiCenter" in c["id"] for c in transport.calls))

    def test_rg_still_present_blocks_every_postdown_mutation(self):
        group = cleanup._rg_id(self.configuration, "maint")
        transport = FakeTransport({group: {"id": group}, **self.tombstones()})
        self.assert_code("resource-groups-remain", lambda: self.post(transport))
        self.assertFalse(transport.mutations())

    def test_original_id_mismatch_blocks_same_name_purge(self):
        resources = self.tombstones()
        next(iter(resources.values()))["properties"]["vaultId"] += "-other"
        transport = FakeTransport(resources)
        self.assert_code("tombstone-mismatch", lambda: self.post(transport))
        self.assertFalse(transport.mutations())

    def test_disabled_configuration_does_not_override_actual_purge_protection(self):
        resources = self.tombstones()
        next(iter(resources.values()))["properties"]["purgeProtectionEnabled"] = True
        transport = FakeTransport(resources)
        result = self.post(transport)
        self.assertEqual(result["status"], "complete-with-protected-retention")
        vault = next(item for item in result["softDeletedServices"] if item["kind"] == "keyVault")
        self.assertEqual(vault["status"], "retained-protection")
        self.assertEqual(len(transport.mutations()), 2)

    def test_protected_vault_is_retained_while_other_owned_tombstones_are_purged(self):
        self.raw["services"]["vaultPurgeProtection"] = True
        self.write_configuration()
        transport = FakeTransport(self.tombstones())
        result = self.post(transport)
        self.assertEqual("complete-with-protected-retention", result["status"])
        self.assertEqual(["retained-protection", "absent", "absent"], [item["status"] for item in result["softDeletedServices"]])
        self.assertEqual(len(transport.mutations()), 2)

    def test_purge_403_is_failure_and_does_not_print_response_body(self):
        transport = FakeTransport(self.tombstones())
        tombstone = next(iter(transport.resources))
        transport.forced["POST", tombstone + "/purge"] = 403
        self.assert_code("azure-request-failed", lambda: self.post(transport))

    def test_successful_delete_without_get_404_is_pending_not_complete(self):
        transport = FakeTransport(self.tombstones())
        transport.keep_after_delete.add(next(iter(transport.resources)))
        self.assert_code("cleanup-pending", lambda: self.post(transport, purge_timeout_seconds=1))

    def test_repeating_postdown_is_idempotent_with_only_gets(self):
        transport = FakeTransport(self.tombstones())
        self.assertEqual("complete", self.post(transport)["status"])
        transport.calls.clear()
        self.assertEqual("complete", self.post(transport)["status"])
        self.assertFalse(transport.mutations())

    def test_region_display_label_is_not_used_in_tombstone_path(self):
        self.state["serviceLocations"]["apim"] = "Japan East"
        transport = FakeTransport()
        self.assert_code("invalid-state", lambda: self.post(transport))
        self.assertFalse(transport.calls)

    def test_get_500_is_not_equated_with_absence(self):
        transport = FakeTransport()
        transport.forced["GET", self.diag_id] = 500
        self.assert_code("azure-request-failed", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_all_paged_models_deleted_and_absent_before_account_or_rg_removal(self):
        transport = self.model_transport(2)
        unrelated = self.state["serviceResourceIds"]["foundry"] + "-other/deployments/keep-model"
        transport.resources[unrelated] = {"id": unrelated}
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        models = result["modelDeployments"]
        self.assertTrue(models["modelDeletionVerified"])
        self.assertFalse(models["quotaReleaseVerified"])
        self.assertEqual(models["quotaStatus"], "not-read")
        self.assertEqual(len(models["deployments"]), 2)
        self.assertIn(unrelated, transport.resources)
        first_delete = next(index for index, call in enumerate(transport.calls) if call["method"] == "DELETE")
        self.assertTrue(any("$skiptoken=1" in call["path"] for call in transport.calls[:first_delete]))
        self.assertTrue(all(call["id"].startswith(transport.collection_id + "/") for call in transport.mutations()))
        self.assertEqual(transport.calls[-1]["id"], self.diag_id)
        # Native azd is not called here and cannot delete the parent until this
        # successful hook result. This helper never deletes account/RG itself.
        self.assertIn(transport.account_id, transport.resources)
        transport.calls.clear()
        again = self.pre(transport)
        self.assertTrue(again["modelDeployments"]["modelDeletionVerified"])
        self.assertFalse(transport.mutations())

    def test_async_model_delete_requires_get404_and_empty_collection(self):
        transport = self.model_transport(1)
        child = transport.collection_id + "/model-0"
        transport.delayed_reads[child] = 1
        result = self.pre(transport)
        self.assertTrue(result["modelDeployments"]["modelDeletionVerified"])
        self.assertEqual(len([call for call in transport.calls if call["method"] == "GET" and call["id"] == child]), 2)
        transport = self.model_transport(1)
        transport.keep_after_delete.add(transport.collection_id + "/model-0")
        self.assert_code("cleanup-pending", lambda: self.pre(transport))
        self.assertFalse(any(call["id"] == self.diag_id for call in transport.calls))
        transport = self.model_transport(0)
        transport.inventory_extra = [{"id": transport.collection_id + "/concurrent-model", "name": "concurrent-model", "type": "Microsoft.CognitiveServices/accounts/deployments"}]
        self.assert_code("model-cleanup-pending", lambda: self.pre(transport))

    def test_foreign_page_or_model_blocks_before_any_child_delete(self):
        for mode in ("host", "account", "entry"):
            with self.subTest(mode=mode):
                transport = self.model_transport(2)
                if mode == "host":
                    transport.next_link_override = "https://other.example" + transport.collection_id + "?api-version=2025-06-01"
                elif mode == "account":
                    transport.next_link_override = transport.collection_id.replace("aif-test-egakumi", "aif-other-test-abc123") + "?api-version=2025-06-01"
                else:
                    transport.inventory_extra = [{"id": transport.collection_id.replace("aif-test-egakumi", "another-account") + "/foreign-model", "name": "foreign-model", "type": "Microsoft.CognitiveServices/accounts/deployments"}]
                self.assert_code("model-owner-mismatch" if mode == "entry" else "model-list-unverified", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_failed_model_list_or_delete_is_not_success_and_hides_error_body(self):
        for method in ("GET", "DELETE"):
            with self.subTest(method=method):
                transport = self.model_transport(1)
                resource = transport.collection_id if method == "GET" else transport.collection_id + "/model-0"
                transport.forced[method, resource] = 403
                self.assert_code("azure-request-failed", lambda: self.pre(transport))
                self.assertFalse(any(call["id"] == self.diag_id for call in transport.calls))

    def test_parent_already_absent_is_not_claimed_as_child_first_or_quota_release(self):
        result = self.pre(FakeTransport())
        models = result["modelDeployments"]
        self.assertEqual(models["status"], "account-already-absent")
        self.assertFalse(models["modelDeletionVerified"])
        self.assertFalse(models["quotaReleaseVerified"])

    def test_owned_apim_deleted_only_after_models404_and_empty_inventory(self):
        """全モデルの404と最後の一覧を通過してから、APIM1件だけを削除する。"""
        transport = self.model_transport(2)
        apim_id = self.state["serviceResourceIds"]["apim"]
        transport.resources[apim_id] = self.apim()
        foreign_id = apim_id.replace("apim-test-egakumi-abc123", "apim-other-test-abc123")
        transport.resources[foreign_id] = {"id": foreign_id}
        result = self.pre(transport)
        self.assertTrue(result["modelDeployments"]["modelDeletionVerified"])
        self.assertEqual(result["apiManagement"], {"resourceId": apim_id, "status": "absent",
                         "httpStatus": 404, "apimDeletionVerified": True, "deleteRequested": True,
                         "alreadyDeleting": False, "resourceInventory": {"resourceGroupId": self.svc_rg,
                         "apiVersion": "2021-04-01", "targetAbsenceVerified": True,
                         "listReadVerified": True, "resourceGroupAbsentVerified": False}})
        self.assertNotIn(apim_id, transport.resources)
        self.assertIn(foreign_id, transport.resources)
        calls = transport.calls
        apim_delete = next(i for i, c in enumerate(calls) if c["id"] == apim_id and c["method"] == "DELETE")
        last_inventory = max(i for i, c in enumerate(calls) if c["id"] == transport.collection_id)
        self.assertLess(last_inventory, apim_delete)
        for model in result["modelDeployments"]["deployments"]:
            self.assertTrue(any(c["id"] == model["resourceId"] and c["method"] == "GET"
                                for c in calls[:last_inventory]))
        self.assertTrue(all(c["id"].startswith(transport.collection_id + "/") or c["id"] == apim_id
                            for c in transport.mutations()))
        self.assertTrue(all(c["id"] != foreign_id for c in calls))
        self.assertEqual(calls[-1]["id"], self.diag_id)
        transport.calls.clear()
        again = self.pre(transport)
        self.assertTrue(again["apiManagement"]["apimDeletionVerified"])
        self.assertFalse(again["apiManagement"]["deleteRequested"])
        self.assertFalse(transport.mutations())

    def test_async_apim_delete_must_reach404(self):
        transport = self.model_transport(0)
        apim_id = self.state["serviceResourceIds"]["apim"]
        transport.resources[apim_id] = self.apim()
        transport.delayed_reads[apim_id] = 1
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        self.assertEqual(3, sum(c["id"] == apim_id and c["method"] == "GET" for c in transport.calls))

    def test_apim_already404_is_recorded_without_delete(self):
        transport = self.model_transport(0)
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        self.assertEqual(result["apiManagement"]["httpStatus"], 404)
        self.assertFalse(result["apiManagement"]["deleteRequested"])
        self.assertFalse(transport.mutations())

    def test_partial_apim_without_identity_or_success_state_can_be_cleaned_up(self):
        """構築途中のFailed/Creatingや状態欠落でも、所有照合できれば撤去できる。"""
        for state in ("Failed", "Creating", None):
            with self.subTest(state=state):
                apim = self.apim()
                if state is not None:
                    apim["properties"] = {"provisioningState": state}
                # identity自体が未返却の部分構築を再現する。tenantIdや成功状態は
                # 所有確認の代用にせず、ID・region・所有タグを正確に照合する。
                self.assertNotIn("identity", apim)
                transport = FakeTransport({apim["id"]: apim})
                result = self.pre(transport)
                self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
                self.assertEqual([apim["id"]], [c["id"] for c in transport.mutations()])

    def test_owned_already_deleting_apim_waits404_without_resending_delete(self):
        """前回の削除が進行中なら、再downでは同じIDのGETだけで完了を待つ。"""
        transport = self.model_transport(0)
        apim = self.apim()
        apim["properties"] = {"provisioningState": "Deleting"}
        apim_id = apim["id"]
        transport.resources[apim_id] = apim
        transport.delayed_reads[apim_id] = 1
        transport.pending_delete.add(apim_id)
        result = self.pre(transport)
        self.assertEqual(result["apiManagement"], {"resourceId": apim_id, "status": "absent",
                         "httpStatus": 404, "apimDeletionVerified": True, "deleteRequested": False,
                         "alreadyDeleting": True, "resourceInventory": {"resourceGroupId": self.svc_rg,
                         "apiVersion": "2021-04-01", "targetAbsenceVerified": True,
                         "listReadVerified": True, "resourceGroupAbsentVerified": False}})
        self.assertFalse(transport.mutations())
        self.assertEqual(2, sum(c["id"] == apim_id and c["method"] == "GET" for c in transport.calls))

    def test_apim_foreign_scope_or_unexpected_name_is_not_requested(self):
        for field in ("scope", "name"):
            with self.subTest(field=field):
                original = self.state["serviceResourceIds"]["apim"]
                self.state["serviceResourceIds"]["apim"] = original.replace(
                    "rg-test-egakumi-svc" if field == "scope" else "apim-test-egakumi-abc123",
                    "rg-other-svc" if field == "scope" else "apim-other-test-abc123")
                transport = FakeTransport()
                self.assert_code("invalid-state" if field == "scope" else "apim-owner-mismatch",
                                 lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
                self.assertFalse(any(c["id"] == self.state["serviceResourceIds"]["apim"] for c in transport.calls))
                self.state["serviceResourceIds"]["apim"] = original

    def test_standard_apim_name_deletes_only_that_service(self):
        """同じ資源グループの無関係なAPIMを削除しない。"""
        own_id = self.state["serviceResourceIds"]["apim"]
        unrelated_id = own_id.rsplit("/", 1)[0] + "/apim-unrelated-service"
        transport = self.model_transport(0)
        transport.resources.update({own_id: self.apim(), unrelated_id: {"id": unrelated_id}})
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        self.assertNotIn(own_id, transport.resources)
        self.assertIn(unrelated_id, transport.resources)
        self.assertFalse(any(call["id"] == unrelated_id for call in transport.calls))

    def test_standard_apim_name_purges_only_its_exact_tombstone(self):
        resources = self.tombstones()
        own_id = next(resource_id for resource_id in resources if "/deletedservices/" in resource_id.lower())
        unrelated_id = own_id.rsplit("/", 1)[0] + "/apim-unrelated-service"
        resources[unrelated_id] = {"id": unrelated_id}
        transport = FakeTransport(resources)
        result = self.post(transport)
        apim = next(item for item in result["softDeletedServices"] if item["kind"] == "apim")
        self.assertTrue(apim["resourceId"].endswith("/apim-test-egakumi-abc123"))
        self.assertEqual(apim["status"], "absent")
        self.assertIn(unrelated_id, transport.resources)
        self.assertFalse(any(call["id"] == unrelated_id for call in transport.calls))

    def test_foreign_apim_identity_stops_delete_and_purge(self):
        """別サービスの配備記録を使って削除・完全削除しない。"""
        self.state["serviceResourceIds"]["apim"] = self.state["serviceResourceIds"]["apim"].rsplit("/", 1)[0] + "/apim-unrelated-service"
        transport = FakeTransport()
        self.assert_code("apim-owner-mismatch", lambda: self.pre(transport))
        self.assert_code("apim-owner-mismatch", lambda: cleanup.post_down(
            self.config_path, self.state, transport, max_checks=1, wait_seconds=0, sleep=lambda _: None))
        self.assertFalse(transport.calls)

    def test_standard_apim_name_rejects_foreign_scope_before_any_request(self):
        original = self.state["serviceResourceIds"]["apim"]
        for scope, foreign in (("rg-test-egakumi-svc", "rg-other-svc"),
                (self.configuration["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")):
            self.state["serviceResourceIds"]["apim"] = original.replace(scope, foreign)
            transport = FakeTransport()
            with self.subTest(scope=scope):
                self.assert_code("invalid-state", lambda: self.pre(transport))
                self.assertFalse(transport.calls)

    def test_apim_live_identity_type_location_and_ownership_must_match(self):
        for field in ("id", "name", "type", "location", "product", "environment", "tags-shape"):
            with self.subTest(field=field):
                apim = self.apim()
                if field in ("product", "environment"):
                    apim["tags"][field] = "other"
                elif field == "tags-shape":
                    apim["tags"] = ["unknown"]
                else:
                    apim[field] += "-other"
                transport = FakeTransport({self.state["serviceResourceIds"]["apim"]: apim})
                self.assert_code("apim-owner-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
                self.assertFalse(any(c["id"] == self.diag_id for c in transport.calls))

    def test_apim_http403_or_transport_timeout_is_not_absence(self):
        for method in ("GET", "DELETE"):
            with self.subTest(method=method):
                apim_id = self.state["serviceResourceIds"]["apim"]
                transport = FakeTransport({apim_id: self.apim()})
                transport.forced[method, apim_id] = 403
                self.assert_code("azure-request-failed", lambda: self.pre(transport))
                self.assertIn(apim_id, transport.resources)
                self.assertFalse(any(c["id"] == self.diag_id for c in transport.calls))

        class TimedOut(FakeTransport):
            def request(inner, method, path, **kwargs):
                if path.split("?", 1)[0] == self.state["serviceResourceIds"]["apim"]:
                    raise TimeoutError("PRIVATE_SENTINEL")
                return super().request(method, path, **kwargs)

        transport = TimedOut()
        self.assert_code("transport-failed", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_apim_delete_accepted_but_still_present_stops_native_handoff(self):
        apim_id = self.state["serviceResourceIds"]["apim"]
        transport = FakeTransport({apim_id: self.apim()})
        transport.keep_after_delete.add(apim_id)
        self.assert_code("cleanup-pending", lambda: self.pre(transport))
        self.assertIn(apim_id, transport.resources)
        self.assertEqual(3, sum(c["id"] == apim_id and c["method"] == "GET" for c in transport.calls))
        self.assertFalse(any(c["id"] == self.diag_id for c in transport.calls))

    def test_model_delete_failure_or_nonempty_inventory_never_touches_apim(self):
        for mode in ("delete-forbidden", "delete-pending", "inventory-nonempty"):
            with self.subTest(mode=mode):
                transport = self.model_transport(1 if mode != "inventory-nonempty" else 0)
                apim_id = self.state["serviceResourceIds"]["apim"]
                transport.resources[apim_id] = self.apim()
                if mode == "delete-forbidden":
                    transport.forced["DELETE", transport.collection_id + "/model-0"] = 403
                    expected = "azure-request-failed"
                elif mode == "delete-pending":
                    transport.keep_after_delete.add(transport.collection_id + "/model-0")
                    expected = "cleanup-pending"
                else:
                    transport.inventory_extra = [{"id": transport.collection_id + "/concurrent-model",
                                                  "name": "concurrent-model", "type": "Microsoft.CognitiveServices/accounts/deployments"}]
                    expected = "model-cleanup-pending"
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertIn(apim_id, transport.resources)
                self.assertFalse(any(c["id"] == apim_id for c in transport.calls))

    def test_actual_locked_retention_blocks_even_model_deletion(self):
        self.raw["monitoring"]["auditImmutabilityMode"] = "Locked"
        self.write_configuration()
        transport = self.model_transport(1)
        transport.resources[self.policy_ids[0]] = self.policy(self.policy_ids[0], "Locked")
        self.assert_code("locked-retention", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_apim_rg_inventory_waits_all_pages_after_individual404_in_both_paths(self):
        for already_absent in (False, True):
            with self.subTest(already_absent=already_absent):
                original = self.model_transport(0)
                apim = self.apim()
                if not already_absent:
                    original.resources[apim["id"]] = apim
                collection = self.svc_rg + "/resources"
                pages = [{"statusCode": 200, "body": {"value": [], "nextLink": "https://management.azure.com" + collection + "?api-version=2021-04-01&$skiptoken=next"}},
                    {"statusCode": 200, "body": {"value": [apim]}},
                    {"statusCode": 200, "body": {"value": []}}]
                transport = InventoryTransport(original, collection, pages)
                result = self.pre(transport)
                self.assertTrue(result["apiManagement"]["resourceInventory"]["targetAbsenceVerified"])
                self.assertEqual(not already_absent, result["apiManagement"]["deleteRequested"])
                self.assertEqual(3, sum(c["id"] == collection for c in transport.calls))
                self.assertFalse(transport.pages)
                self.assertEqual(int(not already_absent), sum(c["method"] == "DELETE" and c["id"] == apim["id"] for c in transport.calls))
                self.assertEqual(self.diag_id, transport.calls[-1]["id"])

    def test_apim_rg_inventory_remaining_or_unreadable_never_hands_off(self):
        collection = self.svc_rg + "/resources"
        for mode in ("remains", "forbidden", "bad-value", "404-existing-rg"):
            with self.subTest(mode=mode):
                original = self.model_transport(0)
                page = {"statusCode": 200, "body": {"value": [self.apim()]}}
                if mode == "forbidden": page = {"statusCode": 403, "body": {"error": "PRIVATE_SENTINEL"}}
                if mode == "bad-value": page = {"statusCode": 200, "body": {"value": {}}}
                if mode == "404-existing-rg":
                    page = {"statusCode": 404, "body": {}}
                    original.resources[self.svc_rg] = {"id": self.svc_rg}
                transport = InventoryTransport(original, collection, [page, page])
                expected = {"remains": "resource-inventory-pending", "forbidden": "azure-request-failed"}.get(mode, "resource-inventory-unverified")
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
                self.assertFalse(any(c["id"] == self.diag_id for c in transport.calls))

    def test_apim_rg_inventory_absent_rg_is_distinct_from_read_empty(self):
        collection = self.svc_rg + "/resources"
        transport = InventoryTransport(self.model_transport(0), collection, [{"statusCode": 404, "body": {}}])
        result = self.pre(transport)["apiManagement"]["resourceInventory"]
        self.assertTrue(result["resourceGroupAbsentVerified"])
        self.assertFalse(result["listReadVerified"])

    def test_apim_rg_inventory_rejects_scope_type_duplicate_and_unsafe_nextlinks(self):
        collection = self.svc_rg + "/resources"
        valid = self.apim()
        bad_items = [{**valid, "id": valid["id"].replace(self.svc_rg, self.monitor_rg)},
                     {**valid, "type": "Microsoft.Compute/virtualMachines"},
                     {**valid, "id": valid["id"] + "%2Fchild"},
                     {**valid, "id": valid["id"] + "/odd"}]
        for item in bad_items:
            with self.subTest(item_kind=item.get("type")):
                transport = InventoryTransport(self.model_transport(0), collection, [{"statusCode": 200, "body": {"value": [item]}}])
                self.assert_code("resource-inventory-owner-mismatch", lambda: self.pre(transport))
        pages = [{"statusCode": 200, "body": {"value": [valid], "nextLink": collection + "?api-version=2021-04-01&$skiptoken=next"}},
                 {"statusCode": 200, "body": {"value": [valid]}}]
        self.assert_code("resource-inventory-owner-mismatch", lambda: self.pre(InventoryTransport(self.model_transport(0), collection, pages)))
        for tail in ("https://other.example" + collection + "?api-version=2021-04-01", collection + "?api-version=2024-01-01",
                     collection + "?api-version=2021-04-01&$filter=resourceType", collection + "?api-version=2021-04-01&api-version=2021-04-01",
                     collection + "?api-version=2021-04-01", collection + "?api-version=2021-04-01&$skiptoken=a&$skiptoken=b"):
            with self.subTest(nextlink=tail):
                transport = InventoryTransport(self.model_transport(0), collection, [{"statusCode": 200, "body": {"value": [], "nextLink": tail}}])
                self.assert_code("resource-inventory-unverified", lambda: self.pre(transport))
                self.assertEqual(1, sum(c["id"] == collection for c in transport.calls))
                self.assertFalse(transport.mutations())


if __name__ == "__main__":
    unittest.main()
