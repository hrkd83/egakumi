"""Portal証明書の初期状態の照合とCoreの公開証明書取得を確認する。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment import lifecycle
from deployment.helpers import portal_auth_certificate as material
from deployment.helpers import private_permissions
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths, private_parent
from operations import portal_auth_certificate as cli
from operations import portal_certificate_initialization as initial
from operations.common import OperationContext, OperationError
from configuration_fixtures import fixture
from test_portal_auth_certificate import record
from bicep_helpers import compile_bicep
from cleanup_fixtures import CleanupFixture


class InitializationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='portal-init-fixture-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = normalize_configuration(fixture())
        self.workspace = LocalPaths(self.root, self.config['environmentName'])
        self.workspace.ensure()
        self.ctx = OperationContext(self.workspace.params, self.config,
                                    {'outputs': {'certificateBootstrap': {'portalJobName': 'fixture-job'}}},
                                    self.workspace, transport=mock.Mock())
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def test_job_initialized_certificate_is_checked_read_only(self):
        material.store_initial_certificate(self.config, self.workspace, record(self.config),
                                           lifecycle.protected_json)
        state_path = self.workspace.state / 'portal-auth-certificate.json'
        original = state_path.read_bytes()
        for execute in (False, True):
            with self.subTest(execute=execute):
                self.assertEqual(initial.initialize(self.ctx, execute=execute), {
                    'action': 'initialize', 'phase': 'initial', 'execute': execute, 'resourcesPlanned': 0,
                    'privateKeyExport': False, 'entraChangesAutomatic': False,
                })
        self.assertEqual(state_path.read_bytes(), original)
        self.ctx.transport.request.assert_not_called()

    def test_old_core_does_not_create_initial_certificate_resources(self):
        del self.ctx.state['outputs']['certificateBootstrap']
        with self.assertRaisesRegex(OperationError, 'requires Core with the ACA certificate job'):
            initial.initialize(self.ctx, execute=True)
        self.assertEqual(list(self.workspace.state.iterdir()), [])
        self.ctx.transport.request.assert_not_called()

    def test_pending_initialization_and_rotation_require_completion(self):
        with self.assertRaisesRegex(OperationError, 'Resume azd up'):
            initial.initialize(self.ctx)
        material.store_initial_certificate(self.config, self.workspace, record(self.config),
                                           lifecycle.protected_json)
        state = material.read_certificate_state(self.config, self.workspace)
        lifecycle.protected_json(self.workspace.state / 'portal-auth-certificate.json',
                                 {**state, 'phase': 'prepared'})
        with self.assertRaisesRegex(OperationError, 'Finish the certificate rotation'):
            initial.initialize(self.ctx)
        self.ctx.transport.request.assert_not_called()

    def test_private_lock_and_explicit_runtime_refuse_links_and_unsafe_files(self):
        private_parent(self.workspace.state / 'latest.json', self.root)
        lock = self.workspace.root / '.operations.lock'
        with initial.locked(self.ctx):
            private_permissions.check_private_permissions(lock)
            with self.assertRaises(OperationError):
                with initial.locked(self.ctx):
                    self.fail('Concurrent operation entered')
        if os.name != 'nt':
            lock.chmod(0o644)
            with self.assertRaises(ValueError):
                with initial.locked(self.ctx):
                    self.fail('Unsafe existing lock was accepted')
            alias = self.root / 'runtime-link'
            alias.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError), mock.patch.object(initial, 'load_configuration') as load:
                initial.load_context(self.workspace.params, alias)
            load.assert_not_called()
        self.workspace.params.write_text('{}', encoding='utf-8')
        self.workspace.params.chmod(0o644)
        with mock.patch.object(initial, 'check_private_permissions', side_effect=ValueError('Unsafe input')), \
                mock.patch.object(initial, 'load_configuration') as load:
            with self.assertRaises(ValueError):
                initial.load_context(self.workspace.params, self.root)
            load.assert_not_called()

    def test_initial_cli_rejects_unrelated_acceptance_flags_before_loading(self):
        for flag in ('--login-verified', '--resume-failed-worker'):
            with mock.patch.object(initial, 'load_context') as load, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['initialize', '--config', 'synthetic-params', flag]), 1)
            load.assert_not_called()


class ExplicitRuntimeTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.workspace = LocalPaths(Path(self.temp.name), 'test')
        self.workspace.ensure()
        for path, value in ((self.workspace.params, self.raw), (self.workspace.state / 'latest.json', self.state)):
            path.write_text(json.dumps(value), encoding='utf-8')
            path.chmod(0o600)
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def test_explicit_runtime_loads_real_configuration_and_state_without_copy_or_authentication(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('operations.common.ArmTransport') as arm:
            ctx = initial.load_context(self.workspace.params, self.workspace.repository)
        self.assertEqual(ctx.config_path, self.workspace.params)
        self.assertEqual(ctx.workspace.repository, self.workspace.repository)
        self.assertEqual(ctx.state, self.state)
        self.assertEqual(sorted(path.name for path in self.workspace.state.iterdir()), ['latest.json'])
        arm.assert_not_called()

    def test_saved_azd_selection_must_match_explicit_runtime(self):
        selection = self.workspace.repository / '.azure/config.json'
        private_parent(selection, self.workspace.repository)
        selection.write_text('{"defaultEnvironment":"other"}', encoding='utf-8')
        selection.chmod(0o600)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('operations.common.ArmTransport') as arm:
            with self.assertRaises(OperationError):
                initial.load_context(self.workspace.params, self.workspace.repository)
        arm.assert_not_called()


class ExistingCoreReceiptTests(unittest.TestCase):
    """旧Coreの記録から再開するpostprovisionが発行済み公開証明書を照合する。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='portal-core-fixture-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = normalize_configuration(fixture())
        self.paths = LocalPaths(self.root, self.config['environmentName'])
        self.certificate = record(self.config)
        vm = '/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/' + self.config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + self.config['resourceStem'] + '-mgmt'
        self.command = vm + '/runCommands/initialize-portal-auth-certificate'
        self.outputs = {'managementVm': {'id': vm}, 'portal': {'authCertificateRunCommandId': self.command},
                        'resourceGroups': self.config['resourceGroupNames'], 'endpoints': {'public': 'https://fixture.example.invalid'}}
        self.response = {'statusCode': 200, 'body': {'id': self.command, 'properties': {'instanceView': {
            'executionState': 'Succeeded', 'exitCode': 0, 'output': material.MARKER + json.dumps(self.certificate)}}}}
        self.transport = mock.Mock()
        self.transport.request.side_effect = lambda method, path: copy.deepcopy(self.response)
        self.enterContext(mock.patch.object(lifecycle, 'ROOT', self.root))
        self.enterContext(mock.patch.object(material, 'local_paths', return_value=self.paths))
        self.enterContext(mock.patch.object(lifecycle, 'ArmTransport', return_value=self.transport))
        self.enterContext(mock.patch.object(lifecycle, '_save_deployment_state', side_effect=lambda config, outputs: lifecycle.protected_json(self.paths.state / 'latest.json', outputs)))
        self.enterContext(mock.patch.object(lifecycle, '_read_initial_serial_deployment_input', return_value=''))
        self.enterContext(mock.patch.object(lifecycle, '_bind_management_serial_password'))
        self.enterContext(mock.patch.object(lifecycle, 'verify_core_network_after_deployment'))
        self.ingress = self.enterContext(mock.patch.object(lifecycle, 'configure_grpc_endpoint', return_value={'enabled': False}))
        self.enterContext(mock.patch.object(lifecycle, 'configure_public_dns', return_value={'status': 'configured'}))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def postprovision(self):
        deployment = {'properties': {'provisioningState': 'Succeeded', 'outputs': {key: {'value': value} for key, value in self.outputs.items()}}}
        with mock.patch.object(lifecycle, 'run', side_effect=[{'CORE_DEPLOYMENT_NAME': 'synthetic-core'}, deployment]):
            lifecycle.postprovision(self.config)

    def test_existing_core_collects_and_reexecution_preserves_current(self):
        self.assertNotIn('authentication', self.config['portal'])
        self.postprovision()
        original = material.read_certificate_state(self.config, self.paths)
        self.postprovision()
        self.assertEqual(material.read_certificate_state(self.config, self.paths), original)
        self.assertTrue((self.paths.root / 'generated/portal-auth-public.cer').exists())
        self.assertEqual(self.transport.request.call_args_list, [mock.call('GET', self.command + '?api-version=' + material.COMPUTE_API + '&$expand=instanceView')] * 2)
        self.assertIn('"deployed": true', self.output.getvalue())

    def test_failed_initial_issuance_retains_core_state_and_stops_success_and_cleans_input(self):
        lifecycle.protected_json(self.paths.parameters, {'syntheticInput': 'synthetic'})
        self.response['body']['properties']['instanceView'].update(executionState='Failed', exitCode=1)
        with self.assertRaises(ValueError):
            self.postprovision()
        self.assertTrue((self.paths.state / 'latest.json').exists())
        self.assertFalse((self.paths.state / 'portal-auth-certificate.json').exists())
        self.assertFalse(self.paths.parameters.exists())
        self.ingress.assert_not_called()
        self.assertNotIn('"deployed": true', self.output.getvalue())

    def test_reconstruction_refuses_different_current_without_replacing_it(self):
        self.postprovision()
        original = material.read_certificate_state(self.config, self.paths)
        self.response['body']['properties']['instanceView']['output'] = material.MARKER + json.dumps(record(self.config, 'b'))
        with self.assertRaises(ValueError):
            self.postprovision()
        self.assertEqual(material.read_certificate_state(self.config, self.paths), original)


class FullConstructionBicepTests(unittest.TestCase):
    def test_core_prepares_portal_signing_identity_and_passes_independent_job_configuration(self):
        core = compile_bicep('infra/main.bicep')
        resources = core['resources']
        if isinstance(resources, dict):
            resources = resources.values()
        output = core['outputs']['certificateBootstrap']['value']
        self.assertIn('portalJobName', output)
        self.assertIn('portalConfig', output)
        self.assertTrue(any('-portal-auth-identity' in resource.get('name', '') for resource in resources))
        self.assertTrue(any('-certificate-storage' in resource.get('name', '') for resource in resources))


if __name__ == '__main__':
    unittest.main()
