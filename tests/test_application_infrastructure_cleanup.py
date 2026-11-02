"""Observe CAE infrastructure groups durably without deleting provider-owned groups."""
import copy
import unittest
from urllib.parse import urlsplit

from cleanup_fixtures import CleanupFixture, FakeTransport, cleanup


class ApplicationInfrastructureCleanupTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.environment_id = self.svc_rg + "/providers/Microsoft.App/managedEnvironments/cae-test-egakumi"
        self.subnet_id = cleanup._rg_id(self.configuration, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-test-egakumi/subnets/snet-test-egakumi-aca"
        self.managed_name = "ME-fixture-shared-applications"
        self.managed_group = "/subscriptions/" + self.configuration["subscriptionId"] + "/resourceGroups/" + self.managed_name
        self.inventory = [{"environmentId": self.environment_id, "resourceGroupId": self.managed_group}]

    def transport(self):
        return FakeTransport({
            self.environment_id: {
                "id": self.environment_id, "name": "cae-test-egakumi", "type": "Microsoft.App/managedEnvironments",
                "location": "japaneast", "tags": {"product": "egakumi", "environment": "test"},
                "properties": {"provisioningState": "Succeeded", "infrastructureResourceGroup": self.managed_name,
                               "vnetConfiguration": {"infrastructureSubnetId": self.subnet_id}},
            },
            self.subnet_id: {"id": self.subnet_id, "properties": {"ipConfigurations": [], "serviceAssociationLinks": []}},
            self.managed_group: {"id": self.managed_group, "name": self.managed_name,
                                 "type": "Microsoft.Resources/resourceGroups", "location": "japaneast"},
        })

    def assert_no_managed_group_mutations(self, transport):
        self.assertFalse(any(call["id"].casefold().startswith(self.managed_group.casefold())
                             for call in transport.mutations()))

    def test_live_environment_infrastructure_is_checkpointed_before_any_delete_and_survives_retry(self):
        transport = self.transport()
        snapshots = []

        def checkpoint(inventory):
            self.assertEqual(transport.mutations(), [])
            snapshots.append(copy.deepcopy(inventory))

        result = self.pre(transport, checkpoint=checkpoint)
        self.assertEqual(snapshots, [self.inventory])
        self.assertEqual(result["portal"]["applicationInfrastructure"], self.inventory)
        self.assertNotIn(self.environment_id, transport.resources)
        self.assertIn(self.managed_group, transport.resources)
        self.assert_no_managed_group_mutations(transport)
        # Simulate the lifecycle's durable write followed by a new cleanup process.
        self.state["applicationInfrastructure"] = copy.deepcopy(snapshots[0])
        resumed = FakeTransport({self.managed_group: transport.resources[self.managed_group]})
        retry_snapshots = []
        retried = self.pre(resumed, checkpoint=lambda value: retry_snapshots.append(copy.deepcopy(value)))
        self.assertEqual(retry_snapshots, [self.inventory])
        self.assertEqual(retried["portal"]["applicationInfrastructure"], self.inventory)
        self.assertEqual(resumed.mutations(), [])

    def test_checkpoint_failure_stops_before_any_resource_mutation(self):
        transport = self.transport()
        calls = []

        def cannot_save(inventory):
            calls.append(copy.deepcopy(inventory))
            raise RuntimeError("checkpoint failed")

        with self.assertRaises(RuntimeError):
            self.pre(transport, checkpoint=cannot_save)
        self.assertEqual(calls, [self.inventory])
        self.assertEqual(transport.mutations(), [])
        self.assertIn(self.environment_id, transport.resources)

    def test_locked_retention_is_checked_before_checkpoint_and_all_mutations(self):
        transport = self.transport()
        transport.resources[self.policy_ids[0]] = self.policy(self.policy_ids[0], mode="Locked")
        snapshots = []
        with self.assertRaises(cleanup.CleanupError) as raised:
            self.pre(transport, checkpoint=snapshots.append)
        self.assertEqual(raised.exception.code, "locked-retention")
        self.assertEqual(snapshots, [])
        self.assertEqual(transport.mutations(), [])

    def test_foreign_environment_is_rejected_before_checkpoint(self):
        transport = self.transport()
        transport.resources[self.environment_id]["tags"]["environment"] = "foreign"
        snapshots = []
        with self.assertRaises(cleanup.CleanupError):
            self.pre(transport, checkpoint=snapshots.append)
        self.assertEqual(snapshots, [])
        self.assertEqual(transport.mutations(), [])

    def test_pending_recorded_group_blocks_completion_before_any_soft_delete_purge(self):
        self.state["applicationInfrastructure"] = copy.deepcopy(self.inventory)
        tombstones = self.tombstones()
        transport = FakeTransport({self.managed_group: {"id": self.managed_group}, **tombstones})
        with self.assertRaises(cleanup.CleanupError) as raised:
            self.post(transport)
        self.assertEqual(raised.exception.code, "application-infrastructure-remains")
        self.assertEqual(sum(call["id"] == self.managed_group for call in transport.calls), 2)
        self.assertFalse(any(call["id"] in tombstones for call in transport.calls))
        self.assertEqual(transport.mutations(), [])

    def test_postdown_waits_for_the_exact_recorded_group_404_without_deleting_it(self):
        self.state["applicationInfrastructure"] = copy.deepcopy(self.inventory)
        unrelated = self.managed_group + "-unrelated"
        transport = FakeTransport({self.managed_group: {"id": self.managed_group}, unrelated: {"id": unrelated}})
        original_request = transport.request
        reads = []

        def provider_finishes_deletion(method, path, *, body=None, headers=None):
            if method == "GET" and urlsplit(path).path == self.managed_group:
                reads.append(path)
                if len(reads) == 2:
                    transport.resources.pop(self.managed_group)
            return original_request(method, path, body=body, headers=headers)

        transport.request = provider_finishes_deletion
        result = self.post(transport)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["applicationInfrastructure"], [
            {**self.inventory[0], "status": "absent", "httpStatus": 404},
        ])
        self.assertEqual(len(reads), 2)
        self.assertFalse(any(call["id"] == unrelated for call in transport.calls))
        self.assertEqual(transport.mutations(), [])

    def test_recorded_group_already_404_is_idempotent(self):
        self.state["applicationInfrastructure"] = copy.deepcopy(self.inventory)
        transport = FakeTransport()
        result = self.post(transport)
        self.assertEqual(result["applicationInfrastructure"], [
            {**self.inventory[0], "status": "absent", "httpStatus": 404},
        ])
        self.assertEqual(sum(call["id"] == self.managed_group for call in transport.calls), 1)
        self.assertEqual(transport.mutations(), [])

    def test_changed_infrastructure_group_after_checkpoint_stops_and_retry_records_both_groups(self):
        transport = self.transport()
        replacement_name = "ME-replacement-applications"
        replacement_group = self.managed_group.rsplit("/", 1)[0] + "/" + replacement_name
        snapshots = []

        def concurrent_environment_replacement(inventory):
            snapshots.append(copy.deepcopy(inventory))
            transport.resources[self.environment_id]["properties"]["infrastructureResourceGroup"] = replacement_name

        with self.assertRaises(cleanup.CleanupError) as raised:
            self.pre(transport, checkpoint=concurrent_environment_replacement)
        self.assertEqual(raised.exception.code, "application-infrastructure-changed")
        self.assertEqual(snapshots, [self.inventory])
        self.assertEqual(transport.mutations(), [])
        self.assertIn(self.environment_id, transport.resources)
        self.state["applicationInfrastructure"] = snapshots[0]
        retry_snapshots = []
        result = self.pre(transport, checkpoint=lambda value: retry_snapshots.append(copy.deepcopy(value)))
        expected_groups = {self.managed_group, replacement_group}
        self.assertEqual({item["resourceGroupId"] for item in retry_snapshots[0]}, expected_groups)
        self.assertEqual({item["resourceGroupId"] for item in result["portal"]["applicationInfrastructure"]}, expected_groups)
        self.assert_no_managed_group_mutations(transport)

    def test_group_first_published_after_checkpoint_is_not_lost_during_environment_deletion(self):
        transport = self.transport()
        transport.resources[self.environment_id]["properties"].pop("infrastructureResourceGroup")
        snapshots = []

        def provider_finishes_provisioning(inventory):
            snapshots.append(copy.deepcopy(inventory))
            transport.resources[self.environment_id]["properties"]["infrastructureResourceGroup"] = self.managed_name

        with self.assertRaises(cleanup.CleanupError) as raised:
            self.pre(transport, checkpoint=provider_finishes_provisioning)
        self.assertEqual(raised.exception.code, "application-infrastructure-changed")
        self.assertEqual(snapshots, [[]])
        self.assertEqual(transport.mutations(), [])
        self.assertIn(self.environment_id, transport.resources)

    def test_invalid_recorded_inventory_is_rejected_before_mutation(self):
        foreign_subscription = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        invalid = [None, {}, "PRIVATE_SENTINEL", [None], [{}],
                   [{"environmentId": self.environment_id + "-foreign", "resourceGroupId": self.managed_group}]]
        for group in (self.managed_group.replace(self.configuration["subscriptionId"], foreign_subscription),
                      self.managed_group + "/providers/Microsoft.Network/loadBalancers/foreign",
                      self.managed_group + "?api-version=PRIVATE_SENTINEL", self.managed_group + "#PRIVATE_SENTINEL",
                      self.managed_group.replace(self.managed_name, ".."),
                      "https://management.azure.com" + self.managed_group,
                      *[form for kind in ("id", "nw", "monitor", "svc", "maint")
                        for group in [cleanup._rg_id(self.configuration, kind)]
                        for form in (group, group + "/", "/" + group)]):
            invalid.append([{"environmentId": self.environment_id, "resourceGroupId": group}])
        for inventory in invalid:
            for phase in ("pre", "post"):
                with self.subTest(inventory=inventory, phase=phase):
                    self.state["applicationInfrastructure"] = inventory
                    transport = FakeTransport()
                    with self.assertRaises(cleanup.CleanupError) as raised:
                        (self.pre if phase == "pre" else self.post)(transport)
                    self.assertEqual(raised.exception.code, "application-infrastructure-state-mismatch")
                    self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))
                    self.assertEqual(transport.mutations(), [])

    def test_invalid_live_group_name_is_not_checkpointed_or_used_for_deletion(self):
        invalid_names = [self.configuration["resourceGroupNames"]["svc"], "../foreign", "foreign/child",
                         "https://outside.invalid/PRIVATE_SENTINEL", "name?PRIVATE_SENTINEL", "name#PRIVATE_SENTINEL"]
        for name in invalid_names:
            with self.subTest(name=name):
                transport = self.transport()
                transport.resources[self.environment_id]["properties"]["infrastructureResourceGroup"] = name
                snapshots = []
                with self.assertRaises(cleanup.CleanupError) as raised:
                    self.pre(transport, checkpoint=snapshots.append)
                self.assertEqual(raised.exception.code, "application-infrastructure-state-mismatch")
                self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))
                self.assertEqual(snapshots, [])
                self.assertEqual(transport.mutations(), [])


if __name__ == "__main__":
    unittest.main()
