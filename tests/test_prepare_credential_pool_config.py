"""選択環境のparamsだけを更新し、保護した同環境stateへ移行前入力を残す。"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from api_definition_helpers import configured_base
from deployment.helpers.paths import private_parent
import prepare_credential_pool_config as pool


class CredentialPoolLocalStorageTests(unittest.TestCase):
    def public_source(self, root, *, count=12, capacity=8000):
        """公開正本を一時フォルダーへ展開し、実値から独立した登録入力を用意する。"""
        repository = Path(__file__).resolve().parents[1]
        parameters = json.loads((repository / 'params/test.example.json').read_text(encoding='utf-8'))
        options = json.loads((repository / 'config/test.json').read_text(encoding='utf-8'))
        parameters['configFile'] = '../../config/test.json'
        parameters['services']['apimInstanceId'] = 'abc123'
        parameters['subscriptionId'] = '11111111-1111-4111-8111-111111111111'
        parameters['azureTenantId'] = '22222222-2222-4222-8222-222222222222'
        parameters['entraTenantId'] = '33333333-3333-4333-8333-333333333333'
        parameters['credentialProviders']['graphDelegated']['clientId'] = '44444444-4444-4444-8444-444444444444'
        parameters['credentialProviders']['graphDelegated']['clientSecretFile'] = './reference-only/graph.secret'
        parameters['management']['sshPublicKeyFile'] = './reference-only/management.pub'
        parameters['certificate']['azureDnsSubscriptionId'] = parameters['subscriptionId']
        parameters['certificate']['azureDnsManagedIdentityResourceId'] = (
            '/subscriptions/' + parameters['subscriptionId']
            + '/resourceGroups/rg-dns-synthetic/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-synthetic')
        options['credentialProviders']['graphDelegated']['pool'] = {'count': count, 'capacity': capacity}
        options['credentialProviders']['graphDelegated']['displayName'] = 'Synthetic configured Graph connection'
        options['gateway']['authentication']['credentialLogin']['transactionLifetimeSeconds'] = 120
        options_file = root / 'config/test.json'
        options_file.parent.mkdir()
        options_file.write_text(json.dumps(options), encoding='utf-8')
        parameters_file = root / '.local/test/params.local.json'
        private_parent(parameters_file, root)
        parameters_file.write_text(json.dumps(parameters), encoding='utf-8')
        parameters_file.chmod(0o600)
        return parameters_file, options_file

    def source(self, root, environment='test'):
        value = configured_base()
        value['environmentName'] = environment
        value['credentialProviders'][0]['name'] = 'graph-personal'
        value['credentialProviders'][0].pop('pool', None)
        value['gateway']['authentication']['credentialLogin']['providerNames'] = ['graph-personal']
        for api in value.get('apiDefinitions', []):
            connection = api.get('credentialConnection', {})
            if connection.get('providerName') == 'graph-delegated':
                connection['providerName'] = 'graph-personal'
        path = root / '.local' / environment / 'params.local.json'
        private_parent(path, root)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def invoke(self, root, arguments, environment='test'):
        output = io.StringIO()
        # Azureの選択入力を隔離し、Windowsの実ACL検査に必要なPATHなどは
        # 残す。全削除するとpwshを見つけられず、権限不正と誤判定される。
        process_environment = {key: os.environ[key] for key in (
            'PATH', 'PATHEXT', 'SystemRoot', 'WINDIR', 'COMSPEC', 'USERPROFILE',
            'HOMEDRIVE', 'HOMEPATH', 'HOME', 'TEMP', 'TMP', 'PSModulePath', 'WORKFORCE_PWSH',
        ) if key in os.environ}
        process_environment['AZURE_ENV_NAME'] = environment
        with mock.patch.object(pool, 'REPOSITORY', root), \
                mock.patch.dict(os.environ, process_environment, clear=True), \
                contextlib.redirect_stdout(output):
            status = pool.main(arguments)
        return status, json.loads(output.getvalue())

    def test_execute_uses_selected_params_and_preserves_original_in_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve();path = self.source(root);original = path.read_bytes()
            status, receipt = self.invoke(root, ['--execute'])
            self.assertEqual(status, 0)
            self.assertTrue(receipt['parameterFileChanged'])
            backup = Path(receipt['backup'])
            self.assertEqual(backup.parent, root / '.local/test/state')
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['credentialProviders'][0]['name'], 'graph-delegated')
            self.assertFalse((root / '.state').exists())
            if os.name != 'nt':
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_plan_does_not_create_backup_or_modify_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve();path = self.source(root);original = path.read_bytes()
            status, receipt = self.invoke(root, [])
            self.assertEqual(status, 0)
            self.assertFalse(receipt['parameterFileChanged'])
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((root / '.local/test/state').exists())

    def test_execute_rejects_other_environment_and_public_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve();path = self.source(root);original = path.read_bytes()
            with self.assertRaises(ValueError):
                self.invoke(root, ['--config', str(path), '--execute'], environment='other')
            public = root / 'params/example.json';public.parent.mkdir();public.write_bytes(original)
            with self.assertRaises(ValueError):
                self.invoke(root, ['--config', str(public), '--execute'])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(public.read_bytes(), original)

    def test_public_input_plan_uses_fixed_header_without_transforming_either_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, options_file = self.public_source(root)
            original = (path.read_bytes(), options_file.read_bytes())
            with mock.patch.object(pool, 'transform', side_effect=AssertionError('public inputs must not be converted')):
                status, receipt = self.invoke(root, [])
            self.assertEqual(status, 0)
            self.assertFalse(receipt['parameterFileChanged'])
            self.assertEqual(receipt['subscriptionKeyHeader'], 'api-key')
            self.assertEqual(receipt['physicalProviders'], 12)
            self.assertEqual(receipt['capacityPerProvider'], 8000)
            self.assertIsNone(receipt['backup'])
            self.assertTrue((path.read_bytes(), options_file.read_bytes()) == original,
                            'public params and config must stay byte-for-byte unchanged')
            self.assertFalse((root / '.local/test/state').exists())

    def test_public_input_execute_keeps_configured_pool_and_session_policy_without_a_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, options_file = self.public_source(root, count=7, capacity=1234)
            original = (path.read_bytes(), options_file.read_bytes())
            with mock.patch.object(pool, 'transform', side_effect=AssertionError('public inputs must not be converted')):
                status, receipt = self.invoke(root, ['--execute'])
            self.assertEqual(status, 0)
            self.assertEqual(receipt['mode'], 'execute')
            self.assertFalse(receipt['parameterFileChanged'])
            self.assertEqual(receipt['physicalProviders'], 7)
            self.assertEqual(receipt['capacityPerProvider'], 1234)
            self.assertEqual(receipt['subscriptionKeyHeader'], 'api-key')
            self.assertEqual(receipt['azureWrites'], 0)
            self.assertIsNone(receipt['backup'])
            self.assertTrue((path.read_bytes(), options_file.read_bytes()) == original,
                            'executing public-input validation must not replace shared settings')
            self.assertFalse((root / '.local/test/state').exists())
            self.assertEqual(json.loads(options_file.read_text(encoding='utf-8'))['gateway']['authentication']['credentialLogin']['transactionLifetimeSeconds'], 120)

    def test_public_input_does_not_silently_convert_a_legacy_provider_array(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, options_file = self.public_source(root)
            parameters = json.loads(path.read_text(encoding='utf-8'))
            parameters['credentialProviders'] = pool.load_configuration_source(path)['credentialProviders']
            path.write_text(json.dumps(parameters), encoding='utf-8')
            original = (path.read_bytes(), options_file.read_bytes())
            with self.assertRaisesRegex(ValueError, 'params.credentialProviders'):
                self.invoke(root, ['--execute'])
            self.assertTrue((path.read_bytes(), options_file.read_bytes()) == original,
                            'rejected legacy shape must not alter either source')
            self.assertFalse((root / '.local/test/state').exists())


if __name__ == '__main__':
    unittest.main()
