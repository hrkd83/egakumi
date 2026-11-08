"""モデル→APIM→RGの撤去順と、保護対象を残す動作を検証する。

偽のARM応答で削除・一覧の全ページ・404・WORM保持・Key Vault保護・完全削除の対象IDを再現する。成功を装わず、処理を止める必要があるケースも確認する。
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import cleanup
from deployment.helpers.configuration import load_configuration


from cleanup_fixtures import (
    CleanupFixture, FakeTransport, ApiCenterListTransport, ModelTransport,
    CredentialTransport, ProviderGetInconsistencyTransport, InventoryTransport, ManagementTransport,
)


class ManagementCleanupTests(CleanupFixture, unittest.TestCase):
    def management_transport(self, *, auto_nic=False, models=0):
        """Azure DNS用の外部UAMIを付けた、現行構成の正常なVM応答を用意する。"""
        transport = super().management_transport(auto_nic=auto_nic, models=models)
        dns_identity = self.configuration["certificate"]["azureDnsManagedIdentityResourceId"]
        transport.resources[transport.management_vm_id]["identity"].update(
            type="SystemAssigned, UserAssigned", userAssignedIdentities={dns_identity: {}})
        return transport

    def test_management_async_vm_delete_auto_removes_nic_without_duplicate_delete(self):
        transport = self.management_transport(auto_nic=True, models=1)
        vm_id, nic_id = transport.management_vm_id, transport.management_nic_id
        transport.resources[self.apim()["id"]] = self.apim()
        transport.delayed_reads[vm_id] = 1
        result = self.pre(transport)
        self.assertTrue(result["management"]["crossResourceGroupSubnetReferenceRemoved"])
        self.assertTrue(result["management"]["vm"]["deleteRequested"])
        self.assertFalse(result["management"]["nic"]["deleteRequested"])
        self.assertNotIn(vm_id, transport.resources)
        self.assertNotIn(nic_id, transport.resources)
        mutations = transport.mutations()
        self.assertEqual([transport.collection_id + "/model-0", self.apim()["id"], vm_id], [c["id"] for c in mutations])
        self.assertFalse(any(c["method"] == "DELETE" and c["id"] == nic_id for c in transport.calls))
        before_delete = next(i for i,c in enumerate(transport.calls) if c["id"] == vm_id and c["method"] == "DELETE")
        self.assertGreaterEqual(sum(c["id"] == vm_id and c["method"] == "GET" for c in transport.calls[before_delete+1:]), 3)

    def test_management_remaining_nic_is_deleted_only_after_vm404_and_revalidation(self):
        transport = self.management_transport()
        vm_id, nic_id = transport.management_vm_id, transport.management_nic_id
        result = self.pre(transport)
        self.assertTrue(result["management"]["nic"]["deleteRequested"])
        self.assertEqual([vm_id, nic_id], [c["id"] for c in transport.mutations()])
        vm_delete = next(i for i,c in enumerate(transport.calls) if c["method"] == "DELETE" and c["id"] == vm_id)
        nic_delete = next(i for i,c in enumerate(transport.calls) if c["method"] == "DELETE" and c["id"] == nic_id)
        self.assertTrue(any(c["method"] == "GET" and c["id"] == vm_id for c in transport.calls[vm_delete+1:nic_delete]))
        self.assertTrue(any(c["method"] == "GET" and c["id"] == nic_id for c in transport.calls[vm_delete+1:nic_delete]))
        self.assertFalse(any("/disks/" in c["id"] or "/resourceGroups/" in c["id"] and "/providers/" not in c["id"] for c in transport.mutations()))

    def test_management_dns_identity_is_matched_to_configuration_and_retained(self):
        for identity_type in ("SystemAssigned, UserAssigned", "UserAssigned,SystemAssigned"):
            with self.subTest(identity_type=identity_type):
                transport, dns_id = self.azure_dns_management_transport()
                identity = transport.resources[transport.management_vm_id]["identity"]
                identity["type"] = identity_type
                identity["userAssignedIdentities"] = {dns_id.upper(): {}}
                result = self.pre(transport)
                self.assertTrue(result["management"]["vm"]["deleteRequested"])
                self.assertEqual([transport.management_vm_id, transport.management_nic_id],
                                 [call["id"] for call in transport.mutations()])
                self.assertFalse(any("Microsoft.ManagedIdentity" in call["id"] for call in transport.calls))

    def test_management_dns_identity_mismatch_blocks_before_any_mutation(self):
        for mode in ("missing", "foreign", "extra", "system-only", "user-only", "principal", "malformed", "bad-type"):
            with self.subTest(mode=mode):
                transport, dns_id = self.azure_dns_management_transport(models=1)
                identity = transport.resources[transport.management_vm_id]["identity"]
                if mode == "missing": identity.pop("userAssignedIdentities")
                if mode == "foreign": identity["userAssignedIdentities"] = {dns_id + "-foreign": {}}
                if mode == "extra": identity["userAssignedIdentities"][dns_id + "-extra"] = {}
                if mode == "system-only": identity["type"] = "SystemAssigned"
                if mode == "user-only": identity["type"] = "UserAssigned"
                if mode == "principal": identity["principalId"] = "55555555-5555-4555-8555-555555555555"
                if mode == "malformed": identity["userAssignedIdentities"] = []
                if mode == "bad-type": identity["type"] = ["SystemAssigned", "UserAssigned"]
                self.assert_code("management-binding-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_management_unconfigured_user_identity_blocks_before_any_mutation(self):
        transport = self.management_transport(models=1)
        identity = transport.resources[transport.management_vm_id]["identity"]
        identity.update(type="SystemAssigned, UserAssigned", userAssignedIdentities={"/foreign": {}})
        self.assert_code("management-binding-mismatch", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_management_state_or_owner_mismatch_blocks_before_any_mutation(self):
        for field in ("id", "name", "nicId", "osDiskId", "principalId"):
            with self.subTest(field=field):
                transport = self.management_transport(models=1)
                self.state["outputs"]["managementVm"][field] = "wrong"
                self.assert_code("management-state-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
        for field in ("id", "name", "type", "location", "tags"):
            with self.subTest(field=field):
                transport = self.management_transport(models=1)
                transport.resources[transport.management_vm_id][field] = {} if field == "tags" else "wrong"
                self.assert_code("management-owner-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_management_binding_mismatch_blocks_before_any_mutation(self):
        for mode in ("vm-nic", "vm-extra-nic", "vm-disk", "vm-keep-disk", "vm-keep-nic", "vm-data-disk", "vm-principal", "nic-subnet", "nic-other-vm", "nic-public-ip", "nic-extra-ip"):
            with self.subTest(mode=mode):
                transport = self.management_transport(models=1)
                vm = transport.resources[transport.management_vm_id]
                nic = transport.resources[transport.management_nic_id]
                if mode == "vm-nic": vm["properties"]["networkProfile"]["networkInterfaces"][0]["id"] += "-foreign"
                if mode == "vm-extra-nic": vm["properties"]["networkProfile"]["networkInterfaces"].append({"id": "foreign"})
                if mode == "vm-disk": vm["properties"]["storageProfile"]["osDisk"]["managedDisk"]["id"] += "-foreign"
                if mode == "vm-keep-disk": vm["properties"]["storageProfile"]["osDisk"]["deleteOption"] = "Detach"
                if mode == "vm-keep-nic": vm["properties"]["networkProfile"]["networkInterfaces"][0]["properties"]["deleteOption"] = "Detach"
                if mode == "vm-data-disk": vm["properties"]["storageProfile"]["dataDisks"] = [{"name": "foreign"}]
                if mode == "vm-principal": vm["identity"]["principalId"] = "55555555-5555-4555-8555-555555555555"
                if mode == "nic-subnet": nic["properties"]["ipConfigurations"][0]["properties"]["subnet"]["id"] += "-foreign"
                if mode == "nic-other-vm": nic["properties"]["virtualMachine"]["id"] += "-foreign"
                if mode == "nic-public-ip": nic["properties"]["ipConfigurations"][0]["properties"]["publicIPAddress"] = {"id": "foreign"}
                if mode == "nic-extra-ip": nic["properties"]["ipConfigurations"].append({})
                self.assert_code("management-binding-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_management_missing_output_or_forbidden_read_is_not_owned_by_name_alone(self):
        transport = self.management_transport(models=1)
        self.state["outputs"].pop("managementVm")
        self.assert_code("management-output-unverified", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())
        transport = self.management_transport(models=1)
        transport.forced["GET", transport.management_nic_id] = 403
        self.assert_code("azure-request-failed", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_management_already_deleting_waits404_without_resending_vm_delete(self):
        transport = self.management_transport(auto_nic=True)
        vm_id = transport.management_vm_id
        transport.resources[vm_id]["properties"]["provisioningState"] = "Deleting"
        transport.delayed_reads[vm_id] = 3
        transport.pending_delete.add(vm_id)
        result = self.pre(transport)
        self.assertFalse(result["management"]["vm"]["deleteRequested"])
        self.assertFalse(transport.mutations())

    def test_management_vm_or_nic_pending_never_hands_off(self):
        for key in ("vm", "nic"):
            with self.subTest(key=key):
                transport = self.management_transport()
                target = transport.management_vm_id if key == "vm" else transport.management_nic_id
                transport.keep_after_delete.add(target)
                self.assert_code("cleanup-pending", lambda: self.pre(transport))
                if key == "vm":
                    self.assertFalse(any(c["method"] == "DELETE" and c["id"] == transport.management_nic_id for c in transport.calls))
                self.assertFalse(any(c["id"] == self.diag_id for c in transport.calls))

    def test_management_nic_foreign_reattachment_after_vm_delete_is_not_deleted(self):
        transport = self.management_transport()
        nic_id = transport.management_nic_id
        transport.after_vm_deleted = lambda current: current.resources[nic_id]["properties"]["virtualMachine"].update(id=transport.management_vm_id + "-foreign")
        self.assert_code("management-binding-mismatch", lambda: self.pre(transport))
        self.assertEqual([transport.management_vm_id], [c["id"] for c in transport.mutations()])

    def test_partial_main_recovers_exact_succeeded_management_module_before_model_first_cleanup(self):
        transport = self.management_transport(models=1)
        module_id = self.management_module(transport)
        self.state["outputs"]["managementVm"] = {}
        state_before = copy.deepcopy(self.state)
        transport.resources[self.apim()["id"]] = self.apim()
        result = self.pre(transport)
        self.assertEqual(self.state, state_before)  # 回復はメモリー内だけで、手元stateを変更しない。
        self.assertEqual(result["management"]["recordSource"], "management-deployment-output")
        self.assertEqual(result["management"]["recordSourceId"], module_id)
        self.assertEqual([c["id"] for c in transport.mutations()], [transport.collection_id + "/model-0",
            self.apim()["id"], transport.management_vm_id, transport.management_nic_id])
        module_get = next(i for i,c in enumerate(transport.calls) if c["id"] == module_id)
        first_write = next(i for i,c in enumerate(transport.calls) if c["method"] != "GET")
        self.assertLess(module_get, first_write)
        self.assertEqual(next(c["path"] for c in transport.calls if c["id"] == module_id), module_id + "?api-version=2022-09-01")
        self.assertFalse(any(c["method"] != "GET" and c["id"] == module_id for c in transport.calls))

    def test_missing_management_output_rejects_wrong_module_or_output_shape_before_changes(self):
        for mode in ("module-id", "module-name", "module-type", "missing-output", "output-type", "output-value", "empty-output"):
            with self.subTest(mode=mode):
                transport = self.management_transport(models=1)
                module_id = self.management_module(transport)
                self.state["outputs"].pop("managementVm")
                module = transport.resources[module_id]
                output = module["properties"]["outputs"]["managementVm"]
                if mode == "module-id": module["id"] += "-foreign"
                if mode == "module-name": module["name"] += "-foreign"
                if mode == "module-type": module["type"] = "Microsoft.Compute/virtualMachines"
                if mode == "missing-output": module["properties"]["outputs"] = {}
                if mode == "output-type": output["type"] = "array"
                if mode == "output-value": output["value"] = []
                if mode == "empty-output": output["value"] = {}
                self.assert_code("management-output-unverified", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_recovered_management_ids_and_principal_use_the_existing_strict_validator(self):
        for field in ("id", "name", "nicId", "osDiskId", "principalId"):
            with self.subTest(field=field):
                transport = self.management_transport(models=1)
                module_id = self.management_module(transport)
                self.state["outputs"]["managementVm"] = {}
                transport.resources[module_id]["properties"]["outputs"]["managementVm"]["value"][field] = "wrong"
                self.assert_code("management-state-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_recovered_management_output_still_requires_live_owner_and_binding_match(self):
        for mode in ("principal", "nic-subnet", "owner-tag"):
            with self.subTest(mode=mode):
                transport = self.management_transport(models=1)
                module_id = self.management_module(transport)
                self.state["outputs"]["managementVm"] = {}
                if mode == "principal":
                    transport.resources[module_id]["properties"]["outputs"]["managementVm"]["value"]["principalId"] = "55555555-5555-4555-8555-555555555555"
                if mode == "nic-subnet":
                    transport.resources[transport.management_nic_id]["properties"]["ipConfigurations"][0]["properties"]["subnet"]["id"] += "-foreign"
                if mode == "owner-tag": transport.resources[transport.management_vm_id]["tags"]["product"] = "foreign"
                self.assert_code("management-owner-mismatch" if mode == "owner-tag" else "management-binding-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_management_module_absent_unsuccessful_or_unreadable_stops_before_any_write(self):
        for mode in ("absent", "Running", "Failed", "Creating", "forbidden"):
            with self.subTest(mode=mode):
                transport = self.management_transport(models=1)
                module_id = self.management_module(transport)
                self.state["outputs"]["managementVm"] = {}
                if mode == "absent": transport.resources.pop(module_id)
                elif mode == "forbidden": transport.forced["GET", module_id] = 403
                else: transport.resources[module_id]["properties"]["provisioningState"] = mode
                self.assert_code("azure-request-failed" if mode == "forbidden" else "management-output-unverified", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_missing_output_without_live_vm_or_nic_does_not_read_management_module(self):
        transport = self.model_transport(1)
        self.state["outputs"]["managementVm"] = {}
        result = self.pre(transport)
        self.assertEqual(result["management"]["recordSource"], "not-created")
        management_module_id = (cleanup._rg_id(self.configuration, "maint")
                                + "/providers/Microsoft.Resources/deployments/test-egakumi-management")
        self.assertFalse(any(c["id"] == management_module_id for c in transport.calls))
        self.assertEqual([transport.collection_id + "/model-0"], [c["id"] for c in transport.mutations()])

    def test_malformed_saved_management_output_never_falls_back_to_other_record(self):
        for invalid in (None, [], False, "", {"id": "foreign"}):
            with self.subTest(invalid=invalid):
                transport = self.management_transport(models=1)
                self.management_module(transport)
                self.state["outputs"]["managementVm"] = invalid
                self.assert_code("management-state-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.calls)
                self.assertFalse(transport.mutations())


if __name__ == "__main__":
    unittest.main()
