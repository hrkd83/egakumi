"""Portal証明書の初期状態の照合と基盤構築時の公開証明書取得を確認する。"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment import lifecycle
from deployment.helpers import portal_auth_certificate as material
from deployment.helpers import private_permissions
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths, private_parent
from operations import portal_auth_certificate as cli
from operations import portal_certificate_initialization as initial
from operations.common import OperationContext, OperationError
from tests.support.configuration_fixtures import fixture
from tests.certificates.test_portal_auth_certificate import record
from tests.support.bicep_helpers import compile_bicep
from tests.support.cleanup_fixtures import CleanupFixture


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

    def test_missing_core_certificate_job_stops_initialization(self):
        del self.ctx.state['outputs']['certificateBootstrap']
        with self.assertRaisesRegex(OperationError, 'requires the deployed ACA certificate job'):
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
        with self.ctx.locked():
            private_permissions.check_private_permissions(lock)
            with self.assertRaises(OperationError):
                with self.ctx.locked():
                    self.fail('Concurrent operation entered')
        if os.name != 'nt':
            lock.chmod(0o644)
            with self.assertRaises(OperationError):
                with self.ctx.locked():
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
