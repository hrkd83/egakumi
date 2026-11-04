"""Application orchestration uses real CLI boundaries without Azure operations."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_applications as applications


class ApplicationDeploymentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = {'subscriptionId': '11111111-1111-4111-8111-111111111111',
                       'azureTenantId': '22222222-2222-4222-8222-222222222222',
                       'resourceStem': 'test-fixture', 'environmentName': 'test', 'publishApis': True}
        self.source = self.root / '.local/test/params.local.json'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('public configuration fixture')
        self.state = self.root / '.local/test/state/latest.json'
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({key: self.config[key]
                                         for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}))
        self.artifact = self.state.parent / 'gateway-deployment.json'
        self.artifact.write_text('prior-stage-receipt')
        self.output, self.errors = io.StringIO(), io.StringIO()
        for patcher in (mock.patch.object(applications, 'REPOSITORY', self.root),
                        mock.patch.object(applications, 'load_configuration', return_value=self.config),
                        mock.patch.object(applications.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)),
                        mock.patch.dict(os.environ, {'AZURE_ENV_NAME': 'test', 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True),
                        contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors)):
            self.enterContext(patcher)

    def run_commands(self):
        return [call.args[0] for call in applications.subprocess.run.call_args_list]

    def test_default_plan_invokes_gateway_then_portal_with_same_absolute_inputs(self):
        self.assertEqual(applications.main([]), 0)
        self.assertEqual(self.run_commands(), [
            [sys.executable, str(self.root / 'scripts' / filename), '--config', str(self.source),
             '--state-file', str(self.state)] for filename in ('deploy_gateway.py', 'deploy_portal.py')])
        applications.load_configuration.assert_called_once_with(self.source)
        for call in applications.subprocess.run.call_args_list:
            self.assertEqual(call.kwargs, {'cwd': self.root, 'shell': False, 'check': False})
        self.assertIn('GatewayとPortalの計画が完了しました。', self.output.getvalue())

    def test_unselected_environment_does_not_start_either_child(self):
        with mock.patch.dict(os.environ, {'PYTHONDONTWRITEBYTECODE': '1'}, clear=True):
            self.assertEqual(applications.main([]), 1)
        applications.subprocess.run.assert_not_called()
        applications.load_configuration.assert_not_called()

    def test_foreign_environment_paths_are_refused_before_children(self):
        for option, path in (("--config", self.root / ".local/other/params.local.json"),
                             ("--state-file", self.root / ".local/other/state/latest.json")):
            with self.subTest(option=option):
                self.assertEqual(applications.main([option, str(path)]), 1)
        applications.subprocess.run.assert_not_called()

    def test_execute_waits_for_success_before_portal_and_streams_existing_clis(self):
        stages = []
        def run(command, **kwargs):
            stages.append(Path(command[1]).name)
            self.assertIn('--execute', command)
            self.assertEqual(kwargs, {'cwd': self.root, 'shell': False, 'check': False})
            return subprocess.CompletedProcess(command, 0)
        applications.subprocess.run.side_effect = run
        self.assertEqual(applications.main(['--execute']), 0)
        self.assertEqual(stages, ['deploy_gateway.py', 'deploy_portal.py'])
        self.assertIn('GatewayとPortalの配備が完了しました。', self.output.getvalue())

    def test_environment_and_explicit_paths_are_resolved_from_repository(self):
        selected = self.source.parent / 'selected.json'
        selected.write_text('selected public configuration')
        with mock.patch.dict(os.environ, {'WORKFORCE_CONFIG': '.local/test/selected.json'}):
            self.assertEqual(applications.main([]), 0)
        self.assertEqual(self.run_commands()[0][3], str(selected))
        applications.subprocess.run.reset_mock()
        self.assertEqual(applications.main(['--config', str(selected), '--state-file', '.local/test/state/latest.json']), 0)
        self.assertEqual(self.run_commands()[1][3:], [str(selected), '--state-file', str(self.state)])

    def test_publication_opt_in_is_required_only_for_execute_and_never_rewritten(self):
        self.config['publishApis'] = False
        before = copy.deepcopy(self.config)
        self.assertEqual(applications.main(['--execute']), 1)
        applications.subprocess.run.assert_not_called()
        self.assertIn('publishApis=true', self.errors.getvalue())
        self.assertEqual(applications.main([]), 0)
        self.assertEqual(self.config, before)

    def test_gateway_failure_stops_before_portal_and_preserves_all_state_assets(self):
        before = {path: path.read_bytes() for path in (self.source, self.state, self.artifact)}
        applications.subprocess.run.return_value = subprocess.CompletedProcess([], 7)
        self.assertEqual(applications.main(['--execute']), 7)
        self.assertEqual(len(self.run_commands()), 1)
        self.assertIn('Gateway deployment stopped', self.errors.getvalue())
        self.assertNotIn('完了しました', self.output.getvalue())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_portal_failure_preserves_gateway_success_and_stops_nonzero(self):
        applications.subprocess.run.side_effect = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 3)]
        self.assertEqual(applications.main(['--execute']), 3)
        self.assertEqual(len(self.run_commands()), 2)
        self.assertEqual(self.artifact.read_text(), 'prior-stage-receipt')
        self.assertIn('Portal deployment stopped', self.errors.getvalue())

    def test_invalid_or_foreign_core_state_stops_before_children(self):
        for value in ('invalid-json', json.dumps([]), json.dumps({**self.config, 'resourceStem': 'another-environment'})):
            self.state.write_text(value)
            with self.subTest(value=value):
                self.assertEqual(applications.main(['--execute']), 1)
        applications.subprocess.run.assert_not_called()

    def test_process_failures_and_interruptions_do_not_expose_private_diagnostics(self):
        for failure, expected in ((OSError('PRIVATE_SENTINEL'), 1),
                                  (subprocess.SubprocessError('PRIVATE_SENTINEL'), 1),
                                  (KeyboardInterrupt(), 130)):
            applications.subprocess.run.side_effect = failure
            with self.subTest(failure=type(failure).__name__):
                self.assertEqual(applications.main(['--execute']), expected)
                self.assertEqual(len(self.run_commands()), 1)
                self.assertNotIn('PRIVATE_SENTINEL', self.output.getvalue() + self.errors.getvalue())
            applications.subprocess.run.reset_mock()


if __name__ == '__main__':
    unittest.main()
