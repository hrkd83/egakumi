"""選択環境のparamsだけを更新し、保護した同環境stateへ移行前入力を残す。"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from api_definition_helpers import configured_base
from deployment.helpers.paths import private_parent
import prepare_credential_pool_config as pool


class CredentialPoolLocalStorageTests(unittest.TestCase):
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
        with mock.patch.object(pool, 'REPOSITORY', root), \
                mock.patch.dict(os.environ, {'AZURE_ENV_NAME': environment}, clear=True), \
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
            self.assertEqual(json.loads(path.read_text())['credentialProviders'][0]['name'], 'graph-delegated')
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


if __name__ == '__main__':
    unittest.main()
