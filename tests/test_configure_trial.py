"""Gateway と本人接続の公開 ID・ファイル参照から試験設定を作る。"""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from configuration_fixtures import fixture, CORE
from deployment.helpers.configuration import normalize_configuration
import configure_trial as trial
from api_definition_helpers import configured_base

APP='11111111-1111-4111-8111-111111111111'
OWNER='22222222-2222-4222-8222-222222222222'

class TrialConfigurationTests(unittest.TestCase):
    def build(self, **changes):
        args=dict(base=configured_base(), base_directory=CORE/'config', output_directory=CORE/'config',
                  cm_client_id=APP, cm_client_secret_file=Path('../secrets/absent.txt'))
        args.update(changes)
        return trial.build_trial_configuration(**args)

    def test_core_application_configures_personal_oauth_and_common_key_entry(self):
        result=self.build()
        self.assertEqual(result['gateway']['authentication']['subscriptionKeyHeader'], 'api-key')
        self.assertEqual(result['gateway']['authentication']['credentialLogin']['providerNames'], ['graph-delegated'])
        self.assertEqual(result['credentialProviders'][0]['clientId'], APP)
        self.assertEqual(len(result['apiDefinitions']), 3)
        self.assertTrue(result['publishApis'])
        for api in result['apiDefinitions']:
            self.assertEqual(api['authorization']['mode'], 'subscriptionKey')
        self.assertEqual(result['apiDefinitions'][2]['credentialConnection'],
                         {'mode': 'personal', 'providerName': 'graph-delegated', 'enabled': True})

    def test_models_and_tools_share_key_contract_and_personal_cm_is_explicit(self):
        result=self.build()
        model,pii,connection=result['apiDefinitions']
        for api in (model,pii,connection):
            self.assertEqual(api['authorization']['mode'],'subscriptionKey')
        self.assertNotIn('credentialConnection',model)
        self.assertNotIn('credentialConnection',pii)
        self.assertEqual(connection['credentialConnection']['mode'],'personal')
        self.assertEqual(result['credentialProviders'][0]['connectionNames'],[])

    def test_existing_models_and_source_paths_are_preserved(self):
        base=configured_base();before=copy.deepcopy(base)
        result=self.build(base=base)
        self.assertEqual(base,before)
        self.assertTrue(Path(result['credentialProviders'][0]['clientSecretFile']).is_absolute())
        self.assertTrue(result['services']['modelDeployments'])
        self.assertIn('graph.microsoft.com',result['network']['firewall']['apimAllowedFqdns'])

    def test_separate_entra_tenant_sets_personal_oauth_without_changing_azure(self):
        base=configured_base();base['entraTenantId']=OWNER
        result=self.build(base=base)
        self.assertEqual(result['azureTenantId'],base['azureTenantId'])
        normalized = normalize_configuration(result, base_directory=CORE/'config')
        self.assertEqual(normalized['credentialProviders'][0]['oauthTenantId'],OWNER)
        self.assertEqual(result['apiDefinitions'][2]['credentialConnection']['mode'],'personal')
        self.assertNotIn('ownerTenantId',result['apiDefinitions'][2]['credentialConnection'])

    def test_missing_base_cm_configuration_is_rejected(self):
        """接続設定の正本がない状態でAPI側から別設定を作らない。"""
        with self.assertRaises(ValueError):
            self.build(base=fixture())

    def test_existing_cm_profile_options_and_models_are_preserved(self):
        """指定したID・秘密参照以外のプール条件とモデル配備を保持する。"""
        base = configured_base()
        base["credentialProviders"][0]["oauthTenantId"] = OWNER
        base["gateway"]["authentication"]["credentialLogin"]["idleTimeoutSeconds"] = 1800
        before = copy.deepcopy(base)
        result = self.build(base=base)
        expected = copy.deepcopy(before["credentialProviders"][0])
        expected["clientId"] = APP
        expected["clientSecretFile"] = result["credentialProviders"][0]["clientSecretFile"]
        self.assertEqual(result["credentialProviders"][0], expected)
        self.assertEqual(result["gateway"]["authentication"]["credentialLogin"],
                         before["gateway"]["authentication"]["credentialLogin"])
        self.assertEqual(result["services"]["modelDeployments"], before["services"]["modelDeployments"])
        self.assertEqual(base, before)

    def test_different_cm_client_id_does_not_change_common_key_authorization(self):
        result=self.build(cm_client_id=OWNER)
        self.assertEqual(result['credentialProviders'][0]['clientId'],OWNER)
        self.assertEqual(result['gateway']['authentication']['subscriptionKeyHeader'],'api-key')

    def test_missing_invalid_and_zero_cm_client_ids_are_rejected(self):
        for changes in ({'cm_client_id':None},{'cm_client_id':''},
                        {'cm_client_id':'PRIVATE_INVALID_ID'},{'cm_client_id':'00000000-0000-0000-0000-000000000000'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError) as error:
                self.build(**changes)
            self.assertNotIn('PRIVATE_INVALID_ID',str(error.exception))

    def test_cli_creates_protected_file_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);output=path/'result.json'
            with mock.patch.object(trial,'load_configuration_source',return_value=configured_base()):
                args=['--base',str(path/'base.json'),'--output',str(output),'--cm-client-id',APP]
                self.assertEqual(trial.main(args),0)
                before=output.read_bytes()
                self.assertEqual(trial.main(args),1)
                self.assertEqual(output.read_bytes(),before)
            if __import__('os').name=='posix':
                self.assertEqual(output.stat().st_mode & 0o777,0o600)
            saved=json.loads(output.read_text())
            self.assertEqual(saved['gateway']['authentication']['subscriptionKeyHeader'],'api-key')
            self.assertEqual(saved['credentialProviders'][0]['clientId'],APP)

    def test_cli_defaults_follow_selected_environment_without_reading_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch.object(trial, 'CORE', root), \
                    mock.patch.dict(os.environ, {'AZURE_ENV_NAME': 'test'}, clear=True), \
                    mock.patch.object(trial, 'load_configuration_source', return_value=configured_base()), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(trial.main(['--cm-client-id', APP]), 0)
            output = root / '.local/test/state/integration.local.json'
            saved = json.loads(output.read_text())
            secret = (output.parent / saved['credentialProviders'][0]['clientSecretFile']).resolve()
            self.assertEqual(secret, root / '.local/test/secrets/entra-client-secret.txt')
            self.assertFalse(secret.exists())
            self.assertFalse((root / '.state').exists())
            self.assertFalse((root / 'secrets').exists())
            if os.name != 'nt':
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)

    def test_cli_rejects_public_output_and_other_selected_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for output, environment in [(root / 'infra/private.json', 'test'),
                                        (root / '.local/other/state/trial.json', 'test'),
                                        (root / '.local/test/state/trial.json', 'other')]:
                with self.subTest(output=output, environment=environment), \
                        mock.patch.object(trial, 'CORE', root), \
                        mock.patch.dict(os.environ, {'AZURE_ENV_NAME': environment}, clear=True), \
                        mock.patch.object(trial, 'load_configuration_source', return_value=configured_base()), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(trial.main(['--base', str(root / '.local/test/params.local.json'),
                                                 '--output', str(output), '--cm-client-id', APP]), 1)
                self.assertFalse(output.exists())
