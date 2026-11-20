"""App実構築出力からのclient単独配備と、失敗時の境界を確認する。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers import management_client_assets as assets
from deployment import lifecycle
from management_fixtures import client_core_outputs


def fixture():
    config = {'subscriptionId': '11111111-1111-4111-8111-111111111111',
              'azureTenantId': '22222222-2222-4222-8222-222222222222', 'resourceStem': 'test-fixture',
              'productName': 'fixture', 'environmentName': 'test', 'location': 'japaneast', 'tags': {},
              'resourceGroupNames': {kind: 'rg-test-fixture-' + kind for kind in ('svc', 'monitor', 'maint')}}
    state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
    state['outputs'] = client_core_outputs(config)
    expected = assets._expected_targets(config)['portal']
    outputs = {'managementMaintenance': copy.deepcopy(expected)}
    template = {'parameters': {'inventory': {'type': 'secureObject'}}, 'resources': [
        {'type': 'Microsoft.Compute/virtualMachines/runCommands', 'name': 'prepare-maintenance-client'}]}
    return config, state, outputs, template


class AssetTransport:
    def __init__(self, config, state, *, failed=False, active=False):
        self.config, self.inventory, self.calls = config, state['outputs']['managementClientInventory'], []
        self.failed, self.active, self.submitted = failed, active, False

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        target = path.split('?', 1)[0]
        tags = {'product': self.config['productName'], 'environment': self.config['environmentName'], 'azd-env-name': self.config['environmentName']}
        resource_type = target.split('/providers/')[-1].rsplit('/', 1)[0]
        if '/Microsoft.Resources/deployments/' in target:
            if method == 'PUT':
                self.submitted = True
                return {'statusCode': 202, 'body': {}}
            return {'statusCode': 200, 'body': {'properties': {'provisioningState': 'Failed' if self.failed else 'Succeeded'}}}
        if '/runCommands/' in target:
            if not self.submitted and not self.active:
                return {'statusCode': 404, 'body': {}}
            return {'statusCode': 200, 'body': {'id': target, 'type': 'Microsoft.Compute/virtualMachines/runCommands', 'tags': tags,
                'properties': {'instanceView': {'executionState': 'Running' if self.active else 'Succeeded', 'exitCode': 0,
                    'output': '{"status":"client-prepared","runtimeVerified":false}\n'}}}}
        if target == self.inventory['managementVmId']:
            return {'statusCode': 200, 'body': {'id': target, 'type': 'Microsoft.Compute/virtualMachines', 'tags': tags,
                'identity': {'principalId': self.inventory['managementPrincipalId']}, 'properties': {'provisioningState': 'Succeeded'}}}
        return {'statusCode': 200, 'body': {'id': target, 'type': resource_type, 'tags': tags, 'properties': {'provisioningState': 'Succeeded'}}}


class ManagementClientAssetTests(unittest.TestCase):
    def test_local_compilation_uses_the_shared_windows_aware_runner(self):
        config, state, outputs, template = fixture()
        assets._client_template.cache_clear()
        self.addCleanup(assets._client_template.cache_clear)
        with mock.patch.object(lifecycle, 'run', return_value=template) as run:
            self.assertEqual(assets._client_template(), template)
        run.assert_called_once_with(['az', 'bicep', 'build', '--file',
            str(assets.REPOSITORY / 'infra/modules/management-maintenance-client.bicep'), '--stdout'])

    def test_missing_or_foreign_core_inventory_is_not_inferred_from_names(self):
        config, state, outputs, _ = fixture()
        for bad in ({}, {'outputs': {}}, {**state, 'subscriptionId': 'other'}):
            with self.subTest(state=bad), self.assertRaises(ValueError):
                assets.management_client_inventory(config, bad)
        state['outputs']['managementClientInventory']['applications'] = ['/another/application']
        with self.assertRaises(ValueError):
            assets.management_client_inventory(config, state)

    def test_other_app_scope_is_rejected_before_any_azure_calls(self):
        config, state, outputs, template = fixture()
        outputs['managementMaintenance']['jobIds'] = ['/another/job']
        transport = mock.Mock()
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError, 'another constructed scope'):
            assets.update_management_client_assets(config, state, outputs, transport,
                component='portal', template=template, repository=Path(root))
        transport.request.assert_not_called()

    def test_unrelated_storage_in_the_same_monitor_group_and_unknown_container_are_rejected(self):
        for change in ('other-storage', 'other-container', 'other-workspace-id', 'other-vm-principal'):
            config, state, outputs, template = fixture()
            inventory = state['outputs']['managementClientInventory']
            if change == 'other-storage':
                inventory['auditStorage'][0]['id'] = inventory['auditStorage'][0]['id'].rsplit('/', 1)[0] + '/stunrelatedfixture'
            elif change == 'other-container':
                inventory['auditStorage'][0]['containers'].append('unrelated-container')
            elif change == 'other-workspace-id':
                inventory['workspace']['customerId'] = '77777777-7777-4777-8777-777777777777'
            else:
                inventory['managementPrincipalId'] = '77777777-7777-4777-8777-777777777777'
            transport = mock.Mock()
            with tempfile.TemporaryDirectory() as root, self.subTest(change=change), self.assertRaises(ValueError):
                assets.update_management_client_assets(config, state, outputs, transport,
                    component='portal', template=template, repository=Path(root))
            transport.request.assert_not_called()

    def test_success_deploys_only_the_client_in_maint_and_records_protected_inventory(self):
        config, state, outputs, template = fixture()
        original = copy.deepcopy(state)
        transport = AssetTransport(config, state)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(lifecycle, 'protected_json') as record:
            result = assets.update_management_client_assets(config, state, outputs, transport,
                component='portal', template=template, repository=Path(root))
            self.assertEqual(result, {'clientPrepared': True, 'runtimeVerified': False})
            self.assertTrue(all(path.is_relative_to(Path(root).resolve() / '.local/test') for path, value in (call.args for call in record.call_args_list)))
            saved = next(value for path, value in (call.args for call in record.call_args_list) if path.name == 'management-client-inventory.json')
            self.assertEqual(saved['inventory']['jobs'], outputs['managementMaintenance']['jobIds'])
        writes = [call for call in transport.calls if call[0] == 'PUT']
        self.assertEqual(len(writes), 1)
        _, path, payload = writes[0]
        self.assertIn('/resourceGroups/rg-test-fixture-maint/providers/Microsoft.Resources/deployments/', path)
        self.assertEqual(payload['properties']['mode'], 'Incremental')
        self.assertEqual(payload['properties']['template'], template)
        self.assertEqual(payload['properties']['parameters']['inventory']['value']['applications'], original['outputs']['managementClientInventory']['applications'])
        self.assertEqual(state, original)
        self.assertTrue(transport.calls[-1][1].endswith('&$expand=instanceView'))

    def test_active_guest_operation_stops_without_submission_or_checkpoint(self):
        config, state, outputs, template = fixture()
        transport = AssetTransport(config, state, active=True)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(lifecycle, 'protected_json') as record:
            with self.assertRaisesRegex(ValueError, 'still active'):
                assets.update_management_client_assets(config, state, outputs, transport,
                    component='portal', template=template, repository=Path(root))
        self.assertFalse(any(method == 'PUT' for method, path, body in transport.calls))
        record.assert_not_called()

    def test_failed_client_deployment_does_not_advance_inventory_checkpoint(self):
        config, state, outputs, template = fixture()
        transport = AssetTransport(config, state, failed=True)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(lifecycle, 'protected_json') as record:
            with self.assertRaisesRegex(ValueError, 'asset deployment failed'):
                assets.update_management_client_assets(config, state, outputs, transport,
                    component='portal', template=template, repository=Path(root))
            self.assertEqual(record.call_count, 1)
            self.assertEqual(record.call_args.args[1]['status'], 'planned')

    def test_broad_resource_template_is_rejected_without_submission(self):
        config, state, outputs, template = fixture()
        template['resources'].append({'type': 'Microsoft.Network/routeTables'})
        transport = AssetTransport(config, state)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(lifecycle, 'protected_json') as record:
            with self.assertRaisesRegex(ValueError, 'only its protected runCommand'):
                assets.update_management_client_assets(config, state, outputs, transport,
                    component='portal', template=template, repository=Path(root))
        self.assertFalse(any(method == 'PUT' for method, path, body in transport.calls))
        record.assert_not_called()


if __name__ == '__main__':
    unittest.main()
