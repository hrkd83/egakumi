"""Credential rotation keeps the old credential until separately confirmed new authentication."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers.paths import LocalPaths
from operations import entra_secret as secret
from operations.common import OperationContext, OperationError

TENANT = '11111111-1111-1111-1111-111111111111'
CLIENT = '22222222-2222-2222-2222-222222222222'
OBJECT = '33333333-3333-3333-3333-333333333333'
OLD_ID = '44444444-4444-4444-4444-444444444444'
NEW_ID = '55555555-5555-5555-5555-555555555555'
OLD = 'SYNTHETIC_RETAINED_VALUE'
NEW = 'SYNTHETIC_NEW_VALUE'


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = LocalPaths(Path(self.directory.name).resolve(), 'test')
        self.workspace.ensure()
        provider = {'name': 'graph', 'displayName': 'Graph', 'clientId': CLIENT,
                    'clientSecretFile': str(self.workspace.secrets / 'current.txt'),
                    'oauthTenantId': TENANT, 'identityEndpoint': 'https://graph.microsoft.com/v1.0/me',
                    'identityIdProperty': 'id'}
        config = {'environmentName': 'test', 'productName': 'fixture', 'resourceStem': 'test-fixture',
                  'subscriptionId': TENANT, 'azureTenantId': TENANT, 'entraTenantId': TENANT,
                  'resourceGroupNames': {'svc': 'rg-test-fixture-svc'},
                  'services': {'apimInstanceId': 'abc123'}, 'credentialProviders': [provider],
                  'gateway': {'authentication': {'credentialLogin': {'providerNames': ['graph']}}}}
        scope = f'/subscriptions/{TENANT}/resourceGroups/rg-test-fixture-svc/providers/'
        state = {'serviceResourceIds': {'keyVault': scope + 'Microsoft.KeyVault/vaults/kv-test-fixture',
                                       'apim': scope + 'Microsoft.ApiManagement/service/apim-test-fixture-abc123'}}
        self.ctx = OperationContext(self.workspace.params, config, state, self.workspace)
        self.ctx.save_private(provider['clientSecretFile'], OLD)
        self.new_file = self.workspace.secrets / 'next.txt'
        self.receipt = self.workspace.state / 'operations/rotation.json'
        self.credentials = [{'keyId': OLD_ID, 'endDateTime': self.future()},
                            {'keyId': NEW_ID, 'endDateTime': self.future()}]
        self.calls, self.parameter_documents = [], []
        self.failure = None
        self.enterContext(patch.object(secret.OperationContext, 'load', return_value=self.ctx))
        self.enterContext(patch.object(self.ctx, 'verify_resource', return_value={}))
        self.enterContext(patch.object(self.ctx, 'run_cli', side_effect=self.cli))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def future(self):
        return (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()

    def cli(self, args):
        self.calls.append(args)
        if args[:3] == ['az', 'account', 'show']:
            return TENANT + '\n'
        if args[:2] == ['az', 'rest']:
            url = args[args.index('--url') + 1]
            if args[args.index('--method') + 1] == 'GET':
                return json.dumps({'id': OBJECT, 'appId': CLIENT, 'passwordCredentials': self.credentials})
            if url.endswith('/addPassword'):
                if self.failure == 'create':
                    raise OperationError('uncertain')
                return json.dumps({'keyId': NEW_ID, 'secretText': NEW})
            if url.endswith('/removePassword'):
                if self.failure == 'retire':
                    raise OperationError('uncertain')
                self.assertEqual(json.loads(Path(args[args.index('--body') + 1][1:]).read_text()), {'keyId': OLD_ID})
                self.credentials = [v for v in self.credentials if v['keyId'] != OLD_ID]
                return ''
        if args[:4] == ['az', 'deployment', 'group', 'create']:
            document = json.loads(Path(args[args.index('--parameters') + 1][1:]).read_text())
            self.parameter_documents.append(document)
            if self.failure == 'kv':
                raise OperationError('uncertain')
        elif any('deploy_gateway.py' in arg for arg in args):
            if self.failure == 'gateway':
                raise OperationError('gateway failed')
        return ''

    def run_stage(self, stage, *options):
        return secret.main(['--config', str(self.workspace.params), '--profile', 'graph',
                            '--receipt', str(self.receipt), '--stage', stage, *options])

    def switch(self):
        self.ctx.save_private(self.new_file, NEW)
        return self.run_stage('switch', '--new-secret-file', str(self.new_file), '--old-key-id', OLD_ID,
                              '--new-key-id', NEW_ID, '--execute')

    def assert_no_public_value(self):
        text = self.stdout.getvalue() + self.stderr.getvalue() + json.dumps(self.calls)
        self.assertNotIn(OLD, text)
        self.assertNotIn(NEW, text)
        if self.receipt.exists():
            self.assertNotIn(OLD, self.receipt.read_text())
            self.assertNotIn(NEW, self.receipt.read_text())

    def test_plan_does_not_authenticate_or_read_new_value(self):
        self.assertEqual(self.run_stage('create', '--old-key-id', OLD_ID, '--new-secret-file', str(self.new_file)), 0)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.new_file.exists())

    def test_create_adds_one_password_without_reset_and_saves_value_privately(self):
        self.assertEqual(self.run_stage('create', '--new-secret-file', str(self.new_file),
                                        '--old-key-id', OLD_ID, '--execute'), 0)
        self.assertEqual(self.new_file.read_text(), NEW)
        self.assertEqual(stat.S_IMODE(self.new_file.stat().st_mode), 0o600)
        self.assertEqual(Path(self.ctx.config['credentialProviders'][0]['clientSecretFile']).read_text(), OLD)
        self.assertEqual(sum('/addPassword' in ' '.join(a) for a in self.calls), 1)
        self.assert_no_public_value()

    def test_uncertain_creation_cannot_be_repeated(self):
        self.failure = 'create'
        options = ('--new-secret-file', str(self.new_file), '--old-key-id', OLD_ID, '--execute')
        self.assertEqual(self.run_stage('create', *options), 1)
        self.assertEqual(self.run_stage('create', *options), 1)
        self.assertEqual(sum('/addPassword' in ' '.join(a) for a in self.calls), 1)
        self.assertEqual(json.loads(self.receipt.read_text())['phase'], 'create-requested')

    def test_existing_output_stops_before_credential_creation(self):
        self.ctx.save_private(self.new_file, 'already-present')
        self.assertEqual(self.run_stage('create', '--new-secret-file', str(self.new_file),
                                        '--old-key-id', OLD_ID, '--execute'), 1)
        self.assertFalse(any('/addPassword' in ' '.join(a) for a in self.calls))

    def test_empty_existing_receipt_is_never_overwritten(self):
        self.ctx.save_private(self.receipt, {})
        self.assertEqual(self.run_stage('create', '--new-secret-file', str(self.new_file),
                                        '--old-key-id', OLD_ID, '--execute'), 1)
        self.assertEqual(self.receipt.read_text().strip(), '{}')
        self.assertEqual(self.calls, [])

    def test_receipt_cannot_share_new_value_or_current_input_path(self):
        for target in (self.new_file, Path(self.ctx.config['credentialProviders'][0]['clientSecretFile'])):
            with self.subTest(target=target):
                self.assertEqual(secret.main(['--config', str(self.workspace.params), '--profile', 'graph',
                    '--receipt', str(target), '--stage', 'create', '--new-secret-file', str(target),
                    '--old-key-id', OLD_ID, '--execute']), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(Path(self.ctx.config['credentialProviders'][0]['clientSecretFile']).read_text(), OLD)

    def test_new_value_cannot_replace_current_input_during_creation(self):
        target = self.ctx.config['credentialProviders'][0]['clientSecretFile']
        self.assertEqual(self.run_stage('create', '--new-secret-file', target, '--old-key-id', OLD_ID, '--execute'), 1)
        self.assertFalse(any('/addPassword' in ' '.join(a) for a in self.calls))
        self.assertEqual(Path(target).read_text(), OLD)

    def test_value_save_failure_preserves_created_key_metadata_without_second_post(self):
        original = self.ctx.save_private
        def fail_value(path, value, **options):
            if Path(path) == self.new_file and value == NEW:
                raise OperationError('value save failed')
            return original(path, value, **options)
        with patch.object(self.ctx, 'save_private', side_effect=fail_value):
            options = ('--new-secret-file', str(self.new_file), '--old-key-id', OLD_ID, '--execute')
            self.assertEqual(self.run_stage('create', *options), 1)
            self.assertEqual(self.run_stage('create', *options), 1)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt['phase'], 'created-value-save-pending')
        self.assertEqual(receipt['newKeyId'], NEW_ID)
        self.assertEqual(sum('/addPassword' in ' '.join(a) for a in self.calls), 1)
        self.assert_no_public_value()

    def test_foreign_profile_tenant_stops_before_graph_or_secret_changes(self):
        self.ctx.config['credentialProviders'][0]['oauthTenantId'] = CLIENT
        self.assertEqual(self.run_stage('create', '--new-secret-file', str(self.new_file),
                                        '--old-key-id', OLD_ID, '--execute'), 1)
        self.assertEqual(self.calls, [])

    def test_switch_uses_only_secure_module_then_gateway_and_keeps_backups(self):
        self.assertEqual(self.switch(), 0)
        document = self.parameter_documents[0]['parameters']
        self.assertEqual(set(document), {'vaultName', 'profiles', 'providerSecrets'})
        self.assertEqual(document['providerSecrets']['value'], {'graph': NEW})
        self.assertEqual(set(document['profiles']['value']), {'graph'})
        command = next(v for v in self.calls if v[:4] == ['az', 'deployment', 'group', 'create'])
        self.assertEqual(command[command.index('--mode') + 1], 'Incremental')
        self.assertIn('shared-entra-secrets.bicep', ' '.join(command))
        self.assertFalse(Path(command[command.index('--parameters') + 1][1:]).exists())
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt['phase'], 'switched-auth-unverified')
        self.assertEqual(Path(receipt['backups'][0]['backup']).read_text(), OLD)
        self.assertFalse(any('/removePassword' in ' '.join(a) for a in self.calls))
        self.assert_no_public_value()

    def test_kv_failure_does_not_publish_gateway_or_retire(self):
        self.failure = 'kv'
        self.assertEqual(self.switch(), 1)
        self.assertFalse(any('deploy_gateway.py' in ' '.join(a) for a in self.calls))
        self.assertEqual(self.run_stage('retire', '--verified-new-sign-in', '--verified-new-connection', '--execute'), 1)
        self.assertFalse(any('/removePassword' in ' '.join(a) for a in self.calls))

    def test_retire_requires_both_fresh_authentication_attestations(self):
        self.assertEqual(self.switch(), 0)
        self.assertEqual(self.run_stage('retire', '--execute'), 1)
        self.assertFalse(any('/removePassword' in ' '.join(a) for a in self.calls))

    def test_retire_removes_only_selected_old_id_after_switch(self):
        self.assertEqual(self.switch(), 0)
        self.assertEqual(self.run_stage('retire', '--verified-new-sign-in', '--verified-new-connection', '--execute'), 0)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt['phase'], 'retired')
        self.assertEqual(receipt['authConfirmation']['source'], 'operator-attestation')
        self.assertEqual([v['keyId'] for v in self.credentials], [NEW_ID])
        self.assert_no_public_value()

    def test_uncertain_retirement_is_not_replayed(self):
        self.assertEqual(self.switch(), 0)
        self.failure = 'retire'
        options = ('--verified-new-sign-in', '--verified-new-connection', '--execute')
        self.assertEqual(self.run_stage('retire', *options), 1)
        self.assertEqual(self.run_stage('retire', *options), 1)
        self.assertEqual(sum('/removePassword' in ' '.join(a) for a in self.calls), 1)

    def test_changed_input_blocks_old_credential_removal(self):
        self.assertEqual(self.switch(), 0)
        Path(self.ctx.config['credentialProviders'][0]['clientSecretFile']).write_text('different-current-value')
        self.assertEqual(self.run_stage('retire', '--verified-new-sign-in', '--verified-new-connection', '--execute'), 1)
        self.assertFalse(any('/removePassword' in ' '.join(a) for a in self.calls))

    def test_failed_gateway_can_restore_retained_old_input_and_consumers(self):
        self.failure = 'gateway'
        self.assertEqual(self.switch(), 1)
        self.failure = None
        self.assertEqual(self.run_stage('rollback', '--execute'), 0)
        current = Path(self.ctx.config['credentialProviders'][0]['clientSecretFile'])
        self.assertEqual(current.read_text(), OLD)
        self.assertEqual(self.parameter_documents[-1]['parameters']['providerSecrets']['value'], {'graph': OLD})
        self.assertFalse(any('/removePassword' in ' '.join(a) for a in self.calls))

    def test_wrong_receipt_scope_and_same_key_ids_stop(self):
        self.assertEqual(self.switch(), 0)
        receipt = json.loads(self.receipt.read_text())
        receipt['scope']['apimInstanceId'] = 'other1'
        self.ctx.save_private(self.receipt, receipt, overwrite=True)
        self.calls.clear()
        self.assertEqual(self.run_stage('retire', '--execute'), 1)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
