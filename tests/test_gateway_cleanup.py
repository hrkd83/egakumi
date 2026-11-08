"""Gatewayを別配備にしても、共通CAEの前に既存の子アプリを回収できることを確認する。"""
import copy
import unittest

from cleanup_fixtures import FakeTransport, cleanup


class GatewayCleanupTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "productName": "fixture",
                       "environmentName": "test", "resourceStem": "test-fixture", "location": "japaneast",
                       "resourceGroupNames": {"svc": "rg-test-fixture-svc"}, "services": {"apimInstanceId": "abc123"}}
        self.base = "/subscriptions/" + self.config["subscriptionId"] + "/resourceGroups/rg-test-fixture-svc/providers/Microsoft.App/"
        self.foundation = {"resourceGroup": "rg-test-fixture-svc", "apimName": "apim-test-fixture-abc123",
                           "environmentName": "cae-test-fixture"}
        self.state = {"outputs": {"gatewayFoundation": self.foundation}}
        self.ids = [self.base + "jobs/caj-test-fixture-recon", self.base + "jobs/caj-test-fixture-schema",
                    self.base + "containerApps/ca-test-fixture-credentials"]
        self.resources = {resource_id: {"id": resource_id, "name": resource_id.rsplit('/', 1)[-1],
                          "type": "Microsoft.App/" + resource_id.split("/")[-2],
                          "location": "Japan East", "tags": {"product": "fixture", "environment": "test"},
                          "properties": {"environmentId" if "/jobs/" in resource_id else "managedEnvironmentId":
                                         self.base + "managedEnvironments/cae-test-fixture"}}
                          for resource_id in self.ids}

    def test_new_core_foundation_discovers_only_known_app_and_job_ids(self):
        transport = FakeTransport(self.resources)
        self.assertEqual(cleanup._credential_apps_preflight(self.config, self.state, transport), self.ids)
        self.assertEqual([item["id"] for item in transport.calls], self.ids)
        self.assertEqual(transport.mutations(), [])

    def test_fresh_core_without_gateway_app_has_no_children(self):
        transport = FakeTransport()
        self.assertEqual(cleanup._credential_apps_preflight(self.config, self.state, transport), [])
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(transport.mutations(), [])

    def test_previous_broker_deployment_record_remains_usable(self):
        state = {"outputs": {"credentialBroker": {"applicationName": "ca-test-fixture-credentials",
                                                   "resourceGroup": "rg-test-fixture-svc"}}}
        transport = FakeTransport(self.resources)
        self.assertEqual(cleanup._credential_apps_preflight(self.config, state, transport), self.ids)
        self.assertEqual(transport.mutations(), [])

    def test_missing_or_stale_outputs_do_not_skip_current_gateway_resources(self):
        for outputs in ({}, {'credentialBroker': {}}, {'portal': {'unrelated': 'core-output'}}):
            with self.subTest(outputs=outputs):
                transport = FakeTransport(self.resources)
                self.assertEqual(cleanup._credential_apps_preflight(self.config, {'outputs': outputs}, transport), self.ids)
                self.assertEqual(transport.mutations(), [])

    def test_current_environment_bindings_must_all_agree(self):
        for key, value in [('managedEnvironmentId', self.base + 'managedEnvironments/cae-foreign'),
                           ('environmentId', self.base + 'managedEnvironments/cae-foreign')]:
            resources = copy.deepcopy(self.resources)
            resources[self.ids[0]]['properties'][key] = value
            with self.subTest(key=key), self.assertRaises(cleanup.CleanupError):
                cleanup._credential_apps_preflight(self.config, self.state, FakeTransport(resources))

    def test_healthy_or_actively_creating_children_without_binding_are_not_deleted(self):
        for status in ('Succeeded', 'Creating', 'Updating', ''):
            resources = copy.deepcopy(self.resources)
            resources[self.ids[0]]['properties'] = {'provisioningState': status}
            transport = FakeTransport(resources)
            with self.subTest(status=status), self.assertRaises(cleanup.CleanupError):
                cleanup._credential_apps_preflight(self.config, {'outputs': {}}, transport)
            self.assertFalse(transport.mutations())

    def test_failed_child_without_binding_still_requires_exact_identity_and_tags(self):
        resources = copy.deepcopy(self.resources)
        resources[self.ids[0]]['properties'] = {'provisioningState': 'Failed'}
        transport = FakeTransport(resources)
        self.assertEqual(cleanup._credential_apps_preflight(self.config, {'outputs': {}}, transport), self.ids)
        for mutation in ({'name': 'foreign'}, {'tags': None}, {'type': 'Microsoft.App/managedEnvironments'},
                         {'location': 'westus'}, {'properties': None}):
            changed = copy.deepcopy(resources)
            changed[self.ids[0]].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(cleanup.CleanupError):
                cleanup._credential_apps_preflight(self.config, {'outputs': {}}, FakeTransport(changed))

    def test_foreign_foundation_or_resource_is_rejected_before_mutation(self):
        for field, value in (("resourceGroup", "another-rg"), ("apimName", "apim-another"),
                             ("environmentName", "cae-another")):
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                state["outputs"]["gatewayFoundation"][field] = value
                transport = FakeTransport(self.resources)
                with self.assertRaises(cleanup.CleanupError):
                    cleanup._credential_apps_preflight(self.config, state, transport)
                self.assertEqual(transport.calls, [])
        resources = copy.deepcopy(self.resources)
        resources[self.ids[0]]["properties"]["environmentId"] = self.base + "managedEnvironments/cae-another"
        transport = FakeTransport(resources)
        with self.assertRaises(cleanup.CleanupError):
            cleanup._credential_apps_preflight(self.config, self.state, transport)
        self.assertEqual(transport.mutations(), [])


if __name__ == "__main__":
    unittest.main()
