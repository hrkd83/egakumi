"""CAEの削除で取得できなくなる管理RGのIDを、変更前に保護保存する。"""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, lifecycle
from deployment.helpers import cleanup
from cleanup_fixtures import FakeTransport


class TeardownCheckpointTests(LifecycleFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.state = lifecycle.state_from_outputs(self.config, self.outputs())
        lifecycle.protected_json(lifecycle.STATE, self.state)
        prefix = "/subscriptions/" + self.config["subscriptionId"] + "/resourceGroups/"
        self.inventory = [{
            "environmentId": prefix + self.config["resourceGroupNames"]["svc"]
                + "/providers/Microsoft.App/managedEnvironments/cae-" + self.config["resourceStem"],
            "resourceGroupId": prefix + "ME-fixture-application",
        }]

    def run_hook(self, command):
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return lifecycle.main([command, "--config", "fixture.json"])

    def test_interrupted_predown_preserves_inventory_for_postdown(self):
        def interrupted(source, state, transport, *, checkpoint):
            checkpoint(self.inventory)
            saved = json.loads(lifecycle.STATE.read_text())
            self.assertEqual(saved["applicationInfrastructure"], self.inventory)
            self.assertEqual(saved["outputs"], self.state["outputs"])
            self.assertEqual(state["applicationInfrastructure"], self.inventory)
            raise cleanup.CleanupError("cleanup-pending", "Fixture deletion is pending")

        with mock.patch.object(cleanup, "pre_down", side_effect=interrupted):
            self.assertEqual(self.run_hook("predown"), 1)
        self.assertFalse((self.root / ".state/cleanup-predown.json").exists())

        def finish(source, state, transport):
            self.assertEqual(state["applicationInfrastructure"], self.inventory)
            return {"status": "complete"}

        with mock.patch.object(cleanup, "post_down", side_effect=finish), \
             mock.patch.object(lifecycle, "remove_grpc_secret"):
            self.assertEqual(self.run_hook("postdown"), 0)

    def test_checkpoint_write_failure_stops_before_cleanup_continues(self):
        mutations = []

        def execute(source, state, transport, *, checkpoint):
            checkpoint(self.inventory)
            mutations.append("delete")

        with mock.patch.object(lifecycle, "protected_json", side_effect=OSError("fixture storage unavailable")), \
             mock.patch.object(cleanup, "pre_down", side_effect=execute):
            self.assertEqual(self.run_hook("predown"), 1)
        self.assertEqual(mutations, [])
        self.assertEqual(json.loads(lifecycle.STATE.read_text()), self.state)

    def test_missing_core_state_stops_without_starting_cleanup(self):
        lifecycle.STATE.unlink()
        with mock.patch.object(cleanup, "pre_down") as operation:
            self.assertEqual(self.run_hook("predown"), 1)
        operation.assert_not_called()

    def test_rebuild_preserves_checkpoint_through_monitoring_and_core_then_checks_old_and_new_groups(self):
        self.state['applicationInfrastructure'] = copy.deepcopy(self.inventory)
        lifecycle.protected_json(lifecycle.STATE, self.state)
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 404, 'body': None, 'headers': {}}
        with mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
             mock.patch.object(lifecycle, 'prepare_grpc_secret', return_value={}), \
             mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'capacity_preflight'), \
             mock.patch.object(lifecycle, 'deploy_phase', return_value=self.outputs()), \
             mock.patch.object(lifecycle, 'lock_and_verify_policies'), \
             mock.patch.object(lifecycle, 'configure_app_insights_cap'), \
             mock.patch.object(lifecycle, 'discover_existing_network_security_groups', return_value=[]), \
             mock.patch.object(lifecycle, 'discover_existing_route_table_routes', return_value={}), \
             mock.patch.object(lifecycle, 'discover_existing_apim_hostnames', return_value={}), \
             mock.patch.object(lifecycle, 'discover_existing_portal_app', return_value=False), \
             mock.patch.object(lifecycle, 'discover_existing_apim_egress_fqdns', return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        self.assertEqual(json.loads(lifecycle.STATE.read_text())['applicationInfrastructure'], self.inventory)

        outputs = {**self.outputs(), 'endpoints': {'public': 'https://gateway.example.test'}}
        response = {'properties': {'outputs': {key: {'value': value} for key, value in outputs.items()}}}
        with mock.patch.object(lifecycle, 'run', side_effect=[{'CORE_DEPLOYMENT_NAME': 'rebuilt-core'}, response]), \
             mock.patch.object(lifecycle, 'ArmTransport'), \
             mock.patch.object(lifecycle, 'configure_grpc_endpoint', return_value={'enabled': False}), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.postprovision(self.config)
        rebuilt = json.loads(lifecycle.STATE.read_text())
        self.assertEqual(rebuilt['applicationInfrastructure'], self.inventory)
        self.assertEqual(rebuilt['outputs'], outputs)

        new_group = self.inventory[0]['resourceGroupId'] + '-rebuilt'
        environment = self.inventory[0]['environmentId']
        subnet = (cleanup._rg_id(self.config, 'nw') + '/providers/Microsoft.Network/virtualNetworks/vnet-'
                  + self.config['resourceStem'] + '/subnets/snet-' + self.config['resourceStem'] + '-aca')
        remaining = FakeTransport({environment: {
            'id': environment, 'name': environment.rsplit('/', 1)[-1], 'type': 'Microsoft.App/managedEnvironments',
            'location': self.config['location'],
            'tags': {'product': self.config['productName'], 'environment': self.config['environmentName']},
            'properties': {'provisioningState': 'Succeeded', 'infrastructureResourceGroup': new_group.rsplit('/', 1)[-1],
                           'vnetConfiguration': {'infrastructureSubnetId': subnet}},
        }})
        snapshots = []
        with mock.patch.object(cleanup, 'load_configuration', return_value=self.config):
            result = cleanup.pre_down('fixture.json', rebuilt, remaining, max_checks=2, wait_seconds=0,
                apim_max_checks=2, apim_wait_seconds=0, sleep=lambda _: None, checkpoint=snapshots.append)
        self.assertEqual({item['resourceGroupId'] for item in snapshots[0]},
                         {self.inventory[0]['resourceGroupId'], new_group})
        self.assertEqual(result['portal']['applicationInfrastructure'], snapshots[0])
        rebuilt['applicationInfrastructure'] = snapshots[0]
        pending = FakeTransport({self.inventory[0]['resourceGroupId']: {'id': self.inventory[0]['resourceGroupId']}})
        with mock.patch.object(cleanup, 'load_configuration', return_value=self.config), \
             self.assertRaises(cleanup.CleanupError) as raised:
            cleanup.post_down('fixture.json', rebuilt, pending, max_checks=2, wait_seconds=0, sleep=lambda _: None)
        self.assertEqual(raised.exception.code, 'application-infrastructure-remains')
        self.assertFalse(pending.mutations())

    def test_foreign_or_invalid_checkpoint_blocks_rebuild_before_local_or_azure_changes(self):
        invalid = [None, [], [self.state], 'invalid-state']
        for key in ('subscriptionId', 'azureTenantId', 'resourceStem'):
            invalid.append({**self.state, key: 'different-environment', 'applicationInfrastructure': self.inventory})
        invalid.append({**self.state, 'resourceGroupNames': {}, 'applicationInfrastructure': self.inventory})
        for inventory in (None, {}, [{}], [{**self.inventory[0], 'resourceGroupId': cleanup._rg_id(self.config, 'svc')}]):
            invalid.append({**self.state, 'applicationInfrastructure': inventory})
        for state in invalid:
            lifecycle.protected_json(lifecycle.STATE, state)
            original = lifecycle.STATE.read_bytes()
            with self.subTest(state=state), mock.patch.object(lifecycle, 'local_preflight') as local, \
                 mock.patch.object(lifecycle, 'run') as cli, mock.patch.object(lifecycle, 'deploy_phase') as deploy:
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.preprovision(self.config)
                self.assertEqual(self.run_hook('preup'), 1)
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle._save_deployment_state(self.config, self.outputs())
            local.assert_not_called()
            cli.assert_not_called()
            deploy.assert_not_called()
            self.assertEqual(lifecycle.STATE.read_bytes(), original)

    def test_state_without_checkpoint_keeps_existing_rebuild_behavior_for_other_environments(self):
        lifecycle.protected_json(lifecycle.STATE, {**self.state, 'resourceStem': 'another-environment'})
        saved = lifecycle._save_deployment_state(self.config, self.outputs())
        self.assertEqual(saved, lifecycle.state_from_outputs(self.config, self.outputs()))
        self.assertNotIn('applicationInfrastructure', saved)

    def test_state_conversion_remains_pure_and_save_failure_preserves_previous_checkpoint(self):
        previous = {**self.state, 'applicationInfrastructure': self.inventory}
        lifecycle.protected_json(lifecycle.STATE, previous)
        self.assertNotIn('applicationInfrastructure', lifecycle.state_from_outputs(self.config, self.outputs()))
        original = lifecycle.STATE.read_bytes()
        with mock.patch.object(lifecycle.os, 'replace', side_effect=OSError('fixture storage unavailable')):
            with self.assertRaises(OSError):
                lifecycle._save_deployment_state(self.config, self.outputs())
        self.assertEqual(lifecycle.STATE.read_bytes(), original)
        self.assertEqual(list(lifecycle.STATE.parent.glob('.core-*')), [])

    def test_postprovision_invalid_checkpoint_retains_state_clears_secret_inputs_and_skips_finishing(self):
        previous = {**self.state, 'applicationInfrastructure': [{}]}
        lifecycle.protected_json(lifecycle.STATE, previous)
        lifecycle.protected_json(lifecycle.PARAMETERS, {'fixture': 'PRIVATE_INPUT'})
        original = lifecycle.STATE.read_bytes()
        outputs = self.outputs()
        response = {'properties': {'outputs': {key: {'value': value} for key, value in outputs.items()}}}
        with mock.patch.object(lifecycle, 'run', side_effect=[{'CORE_DEPLOYMENT_NAME': 'rebuilt-core'}, response]), \
             mock.patch.object(lifecycle, 'configure_grpc_endpoint') as grpc:
            with self.assertRaises(lifecycle.DeploymentError):
                lifecycle.postprovision(self.config)
        grpc.assert_not_called()
        self.public_dns.assert_not_called()
        self.assertEqual(lifecycle.STATE.read_bytes(), original)
        self.assertFalse(lifecycle.PARAMETERS.exists())


if __name__ == "__main__":
    unittest.main()
