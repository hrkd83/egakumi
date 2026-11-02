"""Coreと別工程のアプリをまとめ、途中構築でも共用CAEの前に回収する。"""
from __future__ import annotations

import copy
import unittest

from cleanup_fixtures import CleanupFixture, FakeTransport, cleanup


class ApplicationTransport(FakeTransport):
    """親アプリ・ジョブの削除で子実行も消え、CAEは子の残存時に削除できないARM。"""

    def __init__(self, resources, environment_id, child_ids):
        super().__init__(resources)
        self.environment_id = environment_id
        self.child_ids = child_ids

    def request(self, method, path, *, body=None, headers=None):
        target = path.split("?", 1)[0]
        if method == "DELETE" and target == self.environment_id:
            if any(child in self.resources for child in self.child_ids):
                raise AssertionError("shared environment deleted before its applications and jobs")
        response = super().request(method, path, body=body, headers=headers)
        if method == "DELETE" and target not in self.resources:
            for resource_id in list(self.resources):
                if resource_id.startswith(target + "/"):
                    self.resources.pop(resource_id)
        return response


class ApplicationTeardownTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.state["outputs"] = {}
        stem = self.configuration["resourceStem"]
        prefix = self.svc_rg + "/providers/Microsoft.App/"
        self.environment_id = prefix + "managedEnvironments/cae-" + stem
        self.portal_id = prefix + "containerApps/ca-" + stem + "-portal"
        self.broker_id = prefix + "containerApps/ca-" + stem + "-credential-broker"
        self.job_ids = [prefix + "jobs/caj-" + stem + suffix
                        for suffix in ("-portal-schema", "-portal-groups", "-schema", "-recon")]
        self.child_ids = [self.portal_id, self.broker_id, *self.job_ids]
        self.subnet_id = (cleanup._rg_id(self.configuration, "nw")
                          + "/providers/Microsoft.Network/virtualNetworks/vnet-" + stem
                          + "/subnets/snet-" + stem + "-aca")

    def transport(self, state="Succeeded", *, missing_bindings=False):
        """成功出力を持たず、実資源の所有情報だけを検証可能にする。"""
        resources = {}
        for target in [self.environment_id, *self.child_ids]:
            kind = target.rsplit("/", 2)[1]
            properties = {"provisioningState": state}
            if not missing_bindings:
                if target == self.environment_id:
                    properties["vnetConfiguration"] = {"infrastructureSubnetId": self.subnet_id}
                else:
                    properties["environmentId" if kind == "jobs" else "managedEnvironmentId"] = self.environment_id
            resources[target] = {"id": target, "name": target.rsplit("/", 1)[-1],
                "type": "Microsoft.App/" + kind, "location": self.configuration["location"],
                "tags": {"product": self.configuration["productName"], "environment": self.configuration["environmentName"]},
                "properties": properties}
        resources[self.subnet_id] = {"id": self.subnet_id, "properties": {"ipConfigurations": [], "serviceAssociationLinks": []}}
        return ApplicationTransport(resources, self.environment_id, self.child_ids)

    def test_all_application_children_are_removed_without_completed_deployment_outputs(self):
        transport = self.transport()
        # ARMの親削除で回収する実行・revisionも含め、親を消す順序を検証します。
        children = [self.portal_id + "/revisions/revision-1", self.job_ids[0] + "/executions/execution-1"]
        for target in children:
            transport.resources[target] = {"id": target}
        unrelated = self.svc_rg + "/providers/Microsoft.App/containerApps/another-application"
        transport.resources[unrelated] = {"id": unrelated}
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        deleted = [call["id"] for call in transport.mutations()]
        self.assertEqual(set(deleted), {*self.child_ids, self.environment_id})
        for target in self.child_ids:
            self.assertLess(deleted.index(target), deleted.index(self.environment_id))
        self.assertTrue(all(target not in transport.resources for target in [*self.child_ids, *children, self.environment_id]))
        self.assertIn(unrelated, transport.resources)
        self.assertEqual(result["portal"]["subnet"]["status"], "released")

    def test_creating_resources_with_verified_bindings_can_be_removed(self):
        transport = self.transport("Creating")
        self.assertEqual(self.pre(transport)["status"], "ready-for-azd-down")
        self.assertNotIn(self.environment_id, transport.resources)

    def test_failed_or_canceled_resources_can_be_removed_before_bindings_were_written(self):
        for state in ("Failed", "Canceled", "Cancelled"):
            with self.subTest(state=state):
                transport = self.transport(state, missing_bindings=True)
                self.assertEqual(self.pre(transport)["status"], "ready-for-azd-down")
                self.assertNotIn(self.environment_id, transport.resources)

    def test_unverified_or_foreign_binding_stops_before_any_resource_is_changed(self):
        cases = [(self.environment_id, "vnetConfiguration", {"infrastructureSubnetId": self.subnet_id + "-foreign"})]
        cases += [(target, "environmentId", self.environment_id + "-foreign") for target in self.child_ids]
        for target, key, value in cases:
            with self.subTest(target=target, key=key):
                transport = self.transport("Failed")
                transport.resources[target]["properties"][key] = value
                with self.assertRaises(cleanup.CleanupError):
                    self.pre(transport)
                self.assertEqual(transport.mutations(), [])
        for state in ("Succeeded", "Creating", "Updating"):
            with self.subTest(state=state, missing_bindings=True):
                transport = self.transport(state, missing_bindings=True)
                with self.assertRaises(cleanup.CleanupError):
                    self.pre(transport)
                self.assertEqual(transport.mutations(), [])

    def test_foreign_child_owner_blocks_all_teardown_mutations(self):
        for target in self.child_ids:
            with self.subTest(target=target):
                transport = self.transport()
                transport.resources[target]["tags"]["environment"] = "another-environment"
                with self.assertRaises(cleanup.CleanupError):
                    self.pre(transport)
                self.assertEqual(transport.mutations(), [])

    def test_failed_or_pending_job_delete_preserves_shared_environment_and_can_resume(self):
        for failure in ("denied", "pending"):
            with self.subTest(failure=failure):
                transport = self.transport()
                target = self.job_ids[0]
                if failure == "denied":
                    transport.forced["DELETE", target] = 403
                else:
                    transport.keep_after_delete.add(target)
                with self.assertRaises(cleanup.CleanupError):
                    self.pre(transport)
                self.assertIn(self.environment_id, transport.resources)
                self.assertFalse(any(call["id"] == self.environment_id for call in transport.mutations()))
                transport.forced.clear()
                transport.keep_after_delete.clear()
                self.assertEqual(self.pre(transport)["status"], "ready-for-azd-down")
                self.assertNotIn(self.environment_id, transport.resources)

    def test_child_replacement_before_environment_delete_requires_new_ownership_verification(self):
        transport = self.transport()
        replacement = copy.deepcopy(transport.resources[self.job_ids[0]])
        replacement["tags"]["environment"] = "another-environment"
        request = transport.request

        def replace_after_portal_delete(method, path, *, body=None, headers=None):
            response = request(method, path, body=body, headers=headers)
            if method == "DELETE" and path.split("?", 1)[0] == self.portal_id:
                transport.resources[self.job_ids[0]] = replacement
            return response

        transport.request = replace_after_portal_delete
        with self.assertRaises(cleanup.CleanupError):
            self.pre(transport)
        self.assertIn(self.environment_id, transport.resources)
        self.assertFalse(any(call["id"] == self.environment_id for call in transport.mutations()))


if __name__ == "__main__":
    unittest.main()
