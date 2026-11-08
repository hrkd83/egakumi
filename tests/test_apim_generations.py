"""APIM世代の更新・再開・撤去先の取り違えを、合成記録だけで検証する。"""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from configuration_fixtures import fixture
from deployment.helpers import apim_generations as generations
from deployment.helpers.apim_naming import apim_name
from deployment.helpers.paths import LocalPaths
import prepare_local_environment


class ApimGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = fixture()
        self.config['configFile'] = '../../config/test.json'
        self.paths = LocalPaths(self.root, self.config['environmentName'])
        self.paths.ensure()
        stem = self.config['environmentName'] + '-' + self.config['productName']
        groups = {kind: 'rg-' + stem + '-' + kind for kind in ('id', 'nw', 'monitor', 'svc', 'maint')}
        self.state = {'subscriptionId': self.config['subscriptionId'], 'azureTenantId': self.config['azureTenantId'],
                      'resourceStem': stem, 'apimInstanceId': 'abc123', 'resourceGroupNames': groups,
                      'serviceResourceIds': {'apim': '/subscriptions/' + self.config['subscriptionId']
                          + '/resourceGroups/' + groups['svc'] + '/providers/Microsoft.ApiManagement/service/' + apim_name(self.config)},
                      'applicationInfrastructure': []}
        self.receipt = {key: self.state[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem', 'apimInstanceId')}
        self.receipt['result'] = {'phase': 'postdown', 'status': 'complete-with-protected-retention',
            'resourceGroups': [{'resourceId': '/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/' + name,
                                'status': 'absent', 'httpStatus': 404} for name in groups.values()],
            'applicationInfrastructure': []}
        self.write(self.paths.params, self.config)
        self.write(self.paths.state / 'latest.json', self.state)

    def write(self, path, value):
        generations._write(self.paths, path, value)

    def completed(self):
        self.write(self.paths.state / 'cleanup-postdown.json', self.receipt)

    def prepare_next(self):
        return prepare_local_environment.prepare(self.paths.environment, 'test', self.root, new_apim_instance=True)

    def test_active_instance_accepts_retry_but_rejects_another_identifier(self):
        generations.assert_current_instance(self.config, self.paths)
        other = copy.deepcopy(self.config)
        other['services']['apimInstanceId'] = 'def456'
        with self.assertRaisesRegex(ValueError, 'different APIM instance'):
            generations.assert_current_instance(other, self.paths)

    def test_retired_instance_requires_explicit_preparation_before_up(self):
        self.completed()
        with self.assertRaisesRegex(ValueError, '--new-apim-instance'):
            generations.assert_current_instance(self.config, self.paths)

    def test_unverified_internal_cm_release_does_not_block_a_new_apim_name(self):
        self.receipt['result']['resourceCleanupStatus'] = self.receipt['result']['status']
        self.receipt['result']['status'] = 'resources-absent-with-unverified-credential-manager-release'
        self.completed()
        result = self.prepare_next()
        self.assertNotEqual(result['apimInstanceId'], 'abc123')

    def test_rotation_keeps_inputs_and_secrets_and_archives_all_old_state(self):
        self.completed()
        secret = self.paths.secrets / 'fixture-secret.txt'
        secret.write_text('synthetic-only')
        secret.chmod(0o600)
        self.write(self.paths.state / 'image-build.json', {'status': 'old-generation'})
        with mock.patch.object(generations, 'generate_apim_instance_id', return_value='def456') as generate:
            result = self.prepare_next()
        generate.assert_called_once_with({'abc123'})
        current = json.loads(self.paths.params.read_text())
        expected = copy.deepcopy(self.config)
        expected['services']['apimInstanceId'] = 'def456'
        self.assertEqual(current, expected)
        self.assertEqual(secret.read_text(), 'synthetic-only')
        self.assertEqual(result['apimName'], apim_name(expected))
        archived = self.paths.root / 'history/apim/abc123/state'
        self.assertEqual(json.loads((archived / 'latest.json').read_text()), self.state)
        self.assertTrue((archived / 'image-build.json').is_file())
        self.assertEqual(list(self.paths.state.iterdir()), [])
        generations.assert_current_instance(current, self.paths)
        with self.assertRaisesRegex(ValueError, 'retired'):
            generations.assert_current_instance(self.config, self.paths)
        self.assertEqual(os.stat(self.paths.params).st_mode & 0o777, 0o600)

    def test_missing_or_foreign_postdown_never_changes_parameters(self):
        original = self.paths.params.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare_next()
        self.assertEqual(self.paths.params.read_bytes(), original)
        self.receipt['apimInstanceId'] = 'def456'
        self.completed()
        with self.assertRaisesRegex(ValueError, 'different APIM instance'):
            self.prepare_next()
        self.assertEqual(self.paths.params.read_bytes(), original)

    def test_incomplete_resource_group_cleanup_blocks_generation_change(self):
        self.receipt['result']['resourceGroups'][0]['httpStatus'] = 200
        self.completed()
        with self.assertRaisesRegex(ValueError, 'GET 404'):
            self.prepare_next()
        self.assertEqual(json.loads(self.paths.params.read_text()), self.config)

    def test_unconfirmed_managed_group_blocks_generation_change(self):
        self.state['applicationInfrastructure'] = [{'resourceGroupId': '/subscriptions/'
            + self.config['subscriptionId'] + '/resourceGroups/ME-fixture'}]
        self.write(self.paths.state / 'latest.json', self.state)
        self.completed()
        with self.assertRaisesRegex(ValueError, 'managed resource groups'):
            self.prepare_next()

    def test_stale_params_cannot_overwrite_another_edit(self):
        self.completed()
        changed = copy.deepcopy(self.config)
        changed['services']['publisherName'] = 'Synthetic changed publisher'
        self.write(self.paths.params, changed)
        with self.assertRaisesRegex(ValueError, 'changed during'):
            generations.prepare_next_instance(self.paths, self.config)
        self.assertEqual(json.loads(self.paths.params.read_text()), changed)

    def test_second_preparation_cannot_run_concurrently(self):
        self.completed()
        with generations._preparation_lock(self.paths):
            with self.assertRaisesRegex(ValueError, 'already running'):
                self.prepare_next()
        self.assertEqual(json.loads(self.paths.params.read_text()), self.config)

    def test_interruption_before_state_move_resumes_saved_identifier(self):
        self.completed()
        with mock.patch.object(generations, 'generate_apim_instance_id', return_value='def456'):
            with mock.patch.object(Path, 'rename', side_effect=OSError('synthetic interruption')):
                with self.assertRaises(OSError):
                    self.prepare_next()
        with mock.patch.object(generations, 'generate_apim_instance_id') as generate:
            result = self.prepare_next()
        generate.assert_not_called()
        self.assertEqual(result['apimInstanceId'], 'def456')

    def test_interruption_after_state_move_resumes_saved_identifier(self):
        self.completed()
        write = generations._write
        def interrupted(paths, target, value):
            if target == self.paths.params:
                raise OSError('synthetic interruption')
            return write(paths, target, value)
        with mock.patch.object(generations, 'generate_apim_instance_id', return_value='def456'):
            with mock.patch.object(generations, '_write', side_effect=interrupted):
                with self.assertRaises(OSError):
                    self.prepare_next()
        self.assertFalse((self.paths.state / 'latest.json').exists())
        with mock.patch.object(generations, 'generate_apim_instance_id') as generate:
            result = self.prepare_next()
        generate.assert_not_called()
        self.assertEqual(result['apimInstanceId'], 'def456')

    def test_lost_params_cannot_silently_allocate_another_identifier(self):
        self.paths.params.unlink()
        with self.assertRaisesRegex(ValueError, 'restore the exact'):
            prepare_local_environment.prepare(self.paths.environment, 'test', self.root)
        self.assertFalse(self.paths.params.exists())

    def test_missing_state_does_not_hide_a_retired_generation(self):
        self.completed()
        (self.paths.state / 'latest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Restore the previous Core state'):
            generations.assert_current_instance(self.config, self.paths)

    def test_symlink_history_cannot_move_state_outside_the_environment(self):
        self.completed()
        (self.paths.root / 'history').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            self.prepare_next()
        self.assertTrue((self.paths.state / 'latest.json').exists())

    def test_old_apim_name_cannot_be_relabelled_as_new_instance_state(self):
        self.state['serviceResourceIds']['apim'] = self.state['serviceResourceIds']['apim'].rsplit('-', 1)[0]
        self.write(self.paths.state / 'latest.json', self.state)
        with self.assertRaisesRegex(ValueError, 'resource ID differs'):
            generations.assert_current_instance(self.config, self.paths)


if __name__ == '__main__':
    unittest.main()
