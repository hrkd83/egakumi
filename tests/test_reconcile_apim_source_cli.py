"""APIM整合専用CLIの計画境界・保護されたbackupと生成元の分離を検証する。"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reconcile_apim_source as cli
import entra_publication
from test_entra_publication import APIM, response


class ReconcileApimSourceCliTests(unittest.TestCase):
    def run_cli(self, *, execute=False, previous=None, failure=False):
        with tempfile.TemporaryDirectory(prefix='synthetic-apim-cli-') as directory:
            root = Path(directory)
            state_directory = root / 'state'
            state_directory.mkdir()
            (state_directory / 'latest.json').write_text(json.dumps({'status': 'synthetic-foundation'}), encoding='utf-8')
            checkpoint = state_directory / 'gateway-api-source.json'
            if previous is not None:
                checkpoint.write_text(json.dumps(previous), encoding='utf-8')
            selected = root / 'params.local.json'
            source = {'synthetic': True}
            config = {'synthetic': 'resolved'}
            plan = {'publishedApiIds': [APIM + '/apis/synthetic-a', APIM + '/apis/synthetic-b'],
                    'privatePayload': '<policies>SYNTHETIC_PRIVATE_XML</policies>', 'apimId': APIM}
            source_checkpoint = {'schemaVersion': 2, 'generatedSource': 'SYNTHETIC_GENERATED_SOURCE'}
            rid = APIM + '/policies/policy'
            inventory = entra_publication.ReconciliationInventory(APIM, {
                rid: (rid + '?format=rawxml', {'Accept': 'application/json'},
                      response(rid, {'value': '<policies>SYNTHETIC_READBACK_BACKUP</policies>'}))},
                frozenset(plan['publishedApiIds']), frozenset(plan['publishedApiIds']), frozenset())
            result = {'status': 'management-plane-published', 'publishedApiIds': plan['publishedApiIds'],
                      'entraAuthentication': {'sourceReconciled': True, 'managementPlaneVerified': True,
                                              'employeePathOpen': True},
                      'privateResult': '<policies>SYNTHETIC_PRIVATE_XML</policies>'}
            writes, observed = [], {}
            workspace = SimpleNamespace(root=root, state=state_directory,
                check_selection=mock.Mock())

            def checked(path):
                path = Path(path)
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError('Synthetic evidence path escaped workspace')
                return path

            workspace.checked = mock.Mock(side_effect=checked)
            transport = object()

            def run_publication(actual_config, actual_state, actual_plan, template, actual_transport, **kwargs):
                observed.update(config=actual_config, state=copy.deepcopy(actual_state), plan=actual_plan,
                                template=template, transport=actual_transport, kwargs=kwargs)
                if failure:
                    raise cli.DeploymentError('Synthetic preflight failed')
                kwargs['backup'](inventory)
                kwargs['checkpoint'](source_checkpoint)
                return result

            with contextlib.ExitStack() as stack:
                stdout = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                load = stack.enter_context(mock.patch.object(cli, 'load_configuration_source', return_value=source))
                stack.enter_context(mock.patch.object(cli, 'local_paths', return_value=workspace))
                stack.enter_context(mock.patch.object(cli, 'resolve_publication_configuration', return_value=config))
                prepare = stack.enter_context(mock.patch.object(cli.api_publication, 'prepare_plan', return_value=plan))
                compiler = stack.enter_context(mock.patch.object(cli, 'run_json', return_value={'syntheticTemplate': True}))
                arm = stack.enter_context(mock.patch.object(cli, 'ArmTransport', return_value=transport))
                publication = stack.enter_context(mock.patch.object(cli.api_publication, 'execute_plan', side_effect=run_publication))
                stack.enter_context(mock.patch.object(cli, 'protected_json', side_effect=lambda path, value: writes.append((Path(path), copy.deepcopy(value)))))
                stack.enter_context(mock.patch.object(cli.secrets, 'token_hex', return_value='synthetic'))
                args = ['--config', str(selected)] + (['--execute'] if execute else [])
                if failure:
                    with self.assertRaisesRegex(cli.DeploymentError, 'Synthetic preflight'):
                        cli.main(args)
                else:
                    self.assertEqual(cli.main(args), 0)
                load.assert_called_once_with(selected)
                workspace.check_selection.assert_called_once_with(source)
                return SimpleNamespace(stdout=stdout.getvalue(), writes=writes, observed=observed,
                    compiler=compiler, arm=arm, publication=publication, prepare=prepare,
                    plan=plan, config=config, checkpoint=checkpoint, source_checkpoint=source_checkpoint,
                    workspace=workspace, transport=transport)

    def test_default_plan_does_not_compile_construct_azure_transport_or_execute(self):
        actual = self.run_cli()
        actual.compiler.assert_not_called()
        actual.arm.assert_not_called()
        actual.publication.assert_not_called()
        summary = json.loads(actual.stdout)
        self.assertEqual((summary['status'], summary['azureWrites'], summary['apiCount']), ('planned', 0, 2))
        self.assertTrue(summary['employeePathInterruptedDuringExecution'])
        for key in ('modelsDeployed', 'foundationDeployed', 'brokerDeployed', 'portalDeployed', 'runtimeVerified'):
            self.assertIs(summary[key], False)
        self.assertEqual(len(actual.writes), 1)
        self.assertEqual(actual.writes[0][1]['apiPublication'], actual.plan)
        self.assertNotIn(APIM, actual.stdout)
        self.assertNotIn('SYNTHETIC_PRIVATE_XML', actual.stdout)

    def test_execute_uses_api_only_template_and_explicit_reconciliation_with_existing_checkpoint(self):
        previous = {'schemaVersion': 1, 'syntheticPreviousSource': True}
        actual = self.run_cli(execute=True, previous=previous)
        actual.compiler.assert_called_once_with(['az', 'bicep', 'build', '--file', str(cli.api_publication.MODULE), '--stdout'])
        actual.arm.assert_called_once_with(actual.config)
        self.assertIs(actual.observed['transport'], actual.transport)
        self.assertIs(actual.observed['kwargs']['reconcile_existing_source'], True)
        self.assertEqual(actual.observed['state']['apiPublicationSourceCheckpoint'], previous)
        self.assertEqual(actual.prepare.call_args[0][1]['apiPublicationSourceCheckpoint'], previous)
        summary = json.loads(actual.stdout)
        self.assertEqual(summary['apiCount'], 2)
        self.assertTrue(summary['sourceReconciled'])
        self.assertTrue(summary['managementPlaneVerified'])
        self.assertTrue(summary['employeePathOpen'])
        self.assertIs(summary['runtimeVerified'], False)
        self.assertNotIn(APIM, actual.stdout)
        self.assertNotIn('SYNTHETIC_PRIVATE_XML', actual.stdout)

    def test_readback_backup_never_becomes_generated_source_checkpoint(self):
        actual = self.run_cli(execute=True)
        backups = [value for path, value in actual.writes if path.name == 'inventory-before.json']
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]['provenance'], 'readback-backup-not-generated-source')
        self.assertIn('SYNTHETIC_READBACK_BACKUP', json.dumps(backups[0]))
        checkpoints = [value for path, value in actual.writes if path == actual.checkpoint]
        self.assertEqual(checkpoints, [actual.source_checkpoint])
        self.assertNotIn('SYNTHETIC_READBACK_BACKUP', json.dumps(checkpoints))
        self.assertEqual([path.name for path, _ in actual.writes],
                         ['apim-source-reconciliation-plan.json', 'inventory-before.json',
                          'gateway-api-source.json', 'apim-source-reconciliation.json'])
        self.assertNotIn('SYNTHETIC_READBACK_BACKUP', actual.stdout)

    def test_preflight_failure_writes_no_success_checkpoint_or_result(self):
        actual = self.run_cli(execute=True, failure=True)
        self.assertEqual([path.name for path, _ in actual.writes], ['apim-source-reconciliation-plan.json'])
        self.assertEqual(actual.stdout, '')


if __name__ == '__main__':
    unittest.main()
