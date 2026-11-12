"""Gateway配備の順序、初回・更新の共通経路、秘密の保持を確認する。"""
import copy
from contextlib import ExitStack, contextmanager
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
import deploy_gateway as gateway
from deployment.helpers.paths import local_paths
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from test_credential_broker_initial_deployment import fixture as broker_fixture, DIGEST, ok


def fixture():
    """現行infraのgatewayFoundationと、配備済みbrokerの独立した合成出力。"""
    config, state = broker_fixture()
    state['apimInstanceId'] = apim_instance_id(config)
    stem, groups = config['resourceStem'], config['resourceGroupNames']
    state['outputs']['gatewayFoundation'] = {
        'resourceGroup': groups['svc'], 'identityResourceGroup': groups['id'],
        'apimName': apim_name(config), 'vaultName': 'kv-' + stem,
        'schemaIdentity': copy.deepcopy(state['outputs']['credentialBroker']['schemaIdentity']),
        'sqlServer': 'sql-' + stem + '.database.windows.net',
        'sqlPrivateEndpointId': '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + groups['svc'] + '/providers/Microsoft.Network/privateEndpoints/pep-' + stem + '-sql',
        'environmentName': 'cae-' + stem, 'environmentDomain': 'environment.example.com',
        'registryName': 'acr' + stem.replace('-', ''),
    }
    host = config['gateway']['publicHostName']
    state['outputs']['grpc'] = {'enabled': True, 'publicHostName': host,
                                'publicUrl': 'https://' + host + ':8443'}
    state['outputs'].setdefault('ingressSecrets', {}).update(enabled=True, grpcEnabled=True)
    return config, state


class GatewayDeploymentTests(unittest.TestCase):
    @contextmanager
    def isolated_cli(self, config, state):
        """実ファイル・Azureに書き込まず、公開入口の順序と失敗時の境界を検査する。"""
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            folder = Path(folder).resolve()
            workspace = local_paths(config, folder)
            workspace.ensure()
            params, state_file = workspace.params, workspace.state / 'latest.json'
            params.write_text('original-user-input')
            state_file.write_text(json.dumps(state))
            replacements = {
                'load_configuration_source': {**config, 'apiDefinitions': []},
                'resolve_publication_configuration': config,
                'source_bundle': (b'public', 'source-hash'),
                'reusable_image': None,
                'run_json': {},
                'load_credential_provider_secrets': [],
                'verify_foundation': None,
                'broker_secret': '',
                'build_image': DIGEST,
                'deploy_release': {'status': 'deployed'},
                'protected_json': None,
            }
            observed = {name: patches.enter_context(mock.patch.object(gateway, name, return_value=value))
                        for name, value in replacements.items()}
            observed['publication_plan'] = patches.enter_context(
                mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}))
            observed['transport'] = patches.enter_context(mock.patch.object(gateway, 'ArmTransport'))
            patches.enter_context(mock.patch.object(gateway, 'REPOSITORY', folder))
            patches.enter_context(mock.patch.dict(os.environ, {'AZURE_ENV_NAME': config['environmentName'], 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True))
            patches.enter_context(mock.patch('sys.stdout', new=io.StringIO()))
            arguments = ['--config', str(params), '--state-file', str(state_file),
                         '--output', str(workspace.state / 'gateway-plan.json'), '--execute']
            yield arguments, observed
            self.assertEqual(params.read_text(), 'original-user-input')

    def test_existing_core_state_requires_no_reprovision(self):
        config, state = fixture()
        original = copy.deepcopy(state)
        foundation = gateway.foundation_inputs(config, state)
        self.assertEqual(foundation['sqlServer'], 'sql-' + config['resourceStem'] + '.database.windows.net')
        self.assertEqual(foundation['schemaIdentity'], state['outputs']['credentialBroker']['schemaIdentity'])
        self.assertEqual(state, original)

    def test_other_or_missing_apim_instance_stops_before_build_or_deployment(self):
        for value in (None, 'def456'):
            config, state = fixture()
            config['publishApis'] = True
            if value is None:
                del state['apimInstanceId']
            else:
                state['apimInstanceId'] = value
            with self.subTest(value=value), self.isolated_cli(config, state) as (arguments, observed):
                with self.assertRaisesRegex(ValueError, 'state differs'):
                    gateway.main(arguments)
                for boundary in ('source_bundle', 'run_json', 'transport', 'build_image', 'deploy_release'):
                    observed[boundary].assert_not_called()

    def test_old_grpc_foundation_stops_before_build_or_azure_requests(self):
        for change in ('disabled', 'port', 'host', 'proof'):
            config, state = fixture()
            config['publishApis'] = True
            if change == 'disabled':
                state['outputs']['grpc']['enabled'] = False
            elif change == 'port':
                state['outputs']['grpc']['publicUrl'] = state['outputs']['grpc']['publicUrl'].replace(':8443', ':9443')
            elif change == 'host':
                state['outputs']['grpc']['publicHostName'] += '.invalid'
            else:
                state['outputs']['ingressSecrets']['grpcEnabled'] = False
            with self.subTest(change=change), self.isolated_cli(config, state) as (arguments, observed):
                with self.assertRaisesRegex(ValueError, 'update the Core'):
                    gateway.main(arguments)
                for boundary in ('source_bundle', 'run_json', 'transport', 'build_image', 'deploy_release', 'publication_plan'):
                    observed[boundary].assert_not_called()

    def test_cli_refuses_public_or_other_environment_paths_before_building(self):
        """引数で別環境を指定しても、計画・build・Azure要求へ進めない。"""
        for option, relative in (
            ('--config', 'params/test.local.json'),
            ('--config', '.local/other/params.local.json'),
            ('--state-file', '.local/other/state/latest.json'),
            ('--output', '.local/other/state/gateway-plan.json'),
        ):
            config, state = fixture()
            with self.subTest(option=option, path=relative), self.isolated_cli(config, state) as (arguments, observed):
                arguments[arguments.index(option) + 1] = str(gateway.REPOSITORY / relative)
                with self.assertRaisesRegex(ValueError, r'Runtime (?:files|input/output)'):
                    gateway.main(arguments)
                for name in ('resolve_publication_configuration', 'source_bundle', 'run_json',
                             'build_image', 'deploy_release', 'protected_json', 'transport'):
                    observed[name].assert_not_called()

    def test_new_core_state_has_no_precreated_broker_or_database(self):
        config, state = fixture()
        foundation = gateway.foundation_inputs(config, state)
        del state['outputs']['credentialBroker']
        state['outputs']['gatewayFoundation'] = foundation
        self.assertEqual(gateway.foundation_inputs(config, state), foundation)
        self.assertEqual(gateway.build_state(state, foundation)['outputs']['credentialBroker']['registryName'], foundation['registryName'])
        self.assertNotIn('credentialBroker', state['outputs'])

    def test_foreign_state_cannot_target_another_environment(self):
        config, state = fixture()
        state['subscriptionId'] = 'other'
        with self.assertRaisesRegex(ValueError, 'differs'):
            gateway.foundation_inputs(config, state)

    def test_legacy_or_incomplete_outputs_are_not_used_to_reconstruct_foundation(self):
        for replacement in (None, {}, {'schemaIdentity': {}}):
            config, state = fixture()
            if replacement is None:
                del state['outputs']['gatewayFoundation']
            else:
                state['outputs']['gatewayFoundation'] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                gateway.foundation_inputs(config, state)

    def test_existing_broker_key_is_never_read_or_rotated(self):
        config, state = fixture()
        transport = mock.Mock()
        transport.request.return_value = ok({'properties': {'value': 'not-exposed'}})
        with mock.patch.object(gateway, 'prepare_secret') as generate:
            self.assertEqual(gateway.broker_secret(config, gateway.foundation_inputs(config, state), transport), '')
        generate.assert_not_called()
        self.assertEqual(transport.request.call_args.args[0], 'GET')
        self.assertNotIn('getSecret', transport.request.call_args.args[1])

    def test_new_broker_key_is_created_only_after_confirmed_absence(self):
        config, state = fixture()
        transport = mock.Mock()
        transport.request.return_value = ok(code=404)
        with mock.patch.object(gateway, 'prepare_secret', return_value={'credentialBrokerKey': 'new'}) as generate:
            self.assertEqual(gateway.broker_secret(config, gateway.foundation_inputs(config, state), transport), 'new')
        generate.assert_called_once()
        transport.request.return_value = ok(code=403)
        with mock.patch.object(gateway, 'prepare_secret') as generate:
            with self.assertRaises(ValueError):
                gateway.broker_secret(config, gateway.foundation_inputs(config, state), transport)
        generate.assert_not_called()

    def release(self, fail=None):
        config, state = fixture()
        values = {'deployApplication': False, 'imageDigest': DIGEST, 'brokerKey': 'private-key'}
        events = []
        transport = mock.Mock()
        transport.request.side_effect = lambda *args: events.append('schema-start') or ok({'name': 'schema-run'})
        def deploy(config, values, template, transport):
            events.append('app' if values['deployApplication'] else 'database-cm')
            if values['deployApplication']:
                self.assertEqual(values['brokerKey'], '')
            return {'credentialBroker': state['outputs']['credentialBroker']}
        def schema(*args):
            events.append('schema-wait')
            if fail == 'schema':
                raise ValueError('schema failed')
        def ready(*args):
            events.append('ready')
            if fail == 'ready':
                raise ValueError('not ready')
        def publish(*args):
            events.append('apis')
            return {'status': 'management-plane-published'}
        def callback(*args):
            events.append('callback-waf')
            if fail == 'callback-waf':
                raise ValueError('callback WAF failed')
            return {'path': '/auth/connections/browser/identity-callback', 'policyUpdated': True}
        with mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            result = gateway.deploy_release(config, state, values, {'gateway': {}, 'apis': {}}, transport,
                deploy=deploy, schema_wait=schema, readiness_wait=ready,
                refresh=lambda *args: events.append('named-values'), publish=publish, callback_waf=callback,
                data_waf=lambda *args, **kwargs: events.append('data-waf'),
                progress=lambda *args, **kwargs: None)
        return result, events

    def test_sql_precedes_application_and_api_publication(self):
        result, events = self.release()
        self.assertEqual(events, ['database-cm', 'schema-start', 'schema-wait', 'app', 'ready', 'named-values', 'apis', 'callback-waf', 'data-waf'])
        self.assertTrue(result['sqlInitialized'])
        self.assertTrue(result['applicationReady'])
        self.assertFalse(result['azdUpExecuted'])
        self.assertFalse(result['runtimeVerified'])
        self.assertEqual(result['callbackWaf']['path'], '/auth/connections/browser/identity-callback')

    def test_callback_failure_is_not_reported_as_successful_deployment(self):
        with self.assertRaisesRegex(ValueError, 'callback WAF failed'):
            self.release('callback-waf')

    def test_failed_sql_or_revision_cannot_publish_apis(self):
        for fail in ('schema', 'ready'):
            with self.subTest(fail=fail), self.assertRaises(ValueError):
                self.release(fail)

    def test_disabled_publication_is_rejected_before_compilation_secrets_or_azure(self):
        config, state = fixture()
        config['publishApis'] = False
        with self.isolated_cli(config, state) as (arguments, observed):
            with self.assertRaisesRegex(ValueError, 'publishApis=true'):
                gateway.main(arguments)
            for name in ('run_json', 'load_credential_provider_secrets', 'transport',
                         'build_image', 'deploy_release', 'protected_json'):
                observed[name].assert_not_called()

    def test_invalid_digest_is_rejected_before_compilation_or_azure(self):
        config, state = fixture()
        config['publishApis'] = True
        with self.isolated_cli(config, state) as (arguments, observed):
            with self.assertRaisesRegex(ValueError, 'immutable sha256'):
                gateway.main(arguments + ['--image-digest', 'latest'])
            observed['run_json'].assert_not_called()
            observed['transport'].assert_not_called()
            observed['deploy_release'].assert_not_called()

    def test_build_failure_does_not_start_database_application_or_api_deployment(self):
        config, state = fixture()
        config['publishApis'] = True
        with self.isolated_cli(config, state) as (arguments, observed):
            observed['build_image'].side_effect = ValueError('image build failed')
            with self.assertRaisesRegex(ValueError, 'image build failed'):
                gateway.main(arguments)
            observed['build_image'].assert_called_once()
            observed['deploy_release'].assert_not_called()

    def test_existing_digest_skips_image_build_and_temporary_push_permission(self):
        config, state = fixture()
        config['publishApis'] = True
        with self.isolated_cli(config, state) as (arguments, observed):
            self.assertEqual(gateway.main(arguments + ['--image-digest', DIGEST]), 0)
            observed['source_bundle'].assert_not_called()
            observed['build_image'].assert_not_called()
            observed['deploy_release'].assert_called_once()
            self.assertEqual(observed['deploy_release'].call_args.args[2]['imageDigest'], DIGEST)

    def test_broker_disabled_updates_settings_and_apis_without_sql_or_readiness(self):
        config, state = fixture()
        events = []
        transport = mock.Mock()
        schema_wait, readiness_wait = mock.Mock(), mock.Mock()
        callback_waf = mock.Mock()
        values = {'deployApplication': False, 'imageDigest': ''}
        def settings(*args):
            events.append('settings')
            self.assertTrue(values['deployApplication'])
            return {}
        with mock.patch.object(gateway, 'broker_enabled', return_value=False), \
                mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            result = gateway.deploy_release(config, state, values, {'gateway': {}, 'apis': {}}, transport,
                deploy=settings, schema_wait=schema_wait, readiness_wait=readiness_wait,
                refresh=lambda *args: events.append('named-values'),
                publish=lambda *args: events.append('apis') or {'status': 'management-plane-published'},
                callback_waf=callback_waf, data_waf=lambda *args, **kwargs: events.append('data-waf'),
                progress=lambda *args, **kwargs: None)
        self.assertEqual(events, ['settings', 'named-values', 'apis', 'data-waf'])
        schema_wait.assert_not_called()
        readiness_wait.assert_not_called()
        callback_waf.assert_not_called()
        transport.request.assert_not_called()
        self.assertFalse(result['sqlInitialized'])
        self.assertFalse(result['applicationReady'])

    def test_default_plan_is_offline_and_does_not_read_secrets_or_edit_parameters(self):
        config, state = fixture()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve()
            workspace = local_paths(config, folder)
            workspace.ensure()
            params, state_file, output = workspace.params, workspace.state / 'latest.json', workspace.state / 'gateway-plan.json'
            params.write_text('original-user-input')
            state_file.write_text(json.dumps(state))
            with mock.patch.object(gateway, 'REPOSITORY', folder), \
                    mock.patch.dict(os.environ, {'AZURE_ENV_NAME': config['environmentName'], 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True), \
                    mock.patch.object(gateway, 'load_configuration_source', return_value={**config, 'apiDefinitions': []}), \
                    mock.patch.object(gateway, 'resolve_publication_configuration', return_value=config), \
                    mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}), \
                    mock.patch.object(gateway, 'source_bundle', return_value=(b'public', 'hash')), \
                    mock.patch.object(gateway, 'reusable_image', return_value=None), \
                    mock.patch.object(gateway, 'load_credential_provider_secrets') as secrets, \
                    mock.patch.object(gateway, 'run_json') as runner, \
                    mock.patch.object(gateway, 'ArmTransport') as transport, \
                    mock.patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(gateway.main(['--config', str(params), '--state-file', str(state_file), '--output', str(output)]), 0)
            secrets.assert_not_called()
            runner.assert_not_called()
            transport.assert_not_called()
            self.assertEqual(params.read_text(), 'original-user-input')
            plan = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(plan['azureWrites'], 0)
            self.assertFalse(plan['azdUpRequired'])
            self.assertNotIn('credentialProviderSecrets', plan['parameters'])
            self.assertNotIn('brokerKey', plan['parameters'])

    def test_speech_waf_follows_api_publication_without_requiring_the_broker(self):
        config, state = fixture()
        config['apiDefinitions'].append(json.loads(
            (ROOT / 'apps/gateway/apim/apis/azure-speech-v1.json').read_text(encoding='utf-8')))
        events = []
        speech = mock.Mock(side_effect=lambda *args: events.append('speech-waf') or {'paths': ['/speech']})
        with mock.patch.object(gateway, 'broker_enabled', return_value=False), \
                mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            result = gateway.deploy_release(config, state, {'imageDigest': ''}, {'gateway': {}, 'apis': {}}, mock.Mock(),
                deploy=lambda *args: {}, refresh=lambda *args: None,
                publish=lambda *args: events.append('apis') or {}, speech_waf=speech,
                progress=lambda *args, **kwargs: None)
        self.assertEqual(events, ['apis', 'speech-waf'])
        self.assertEqual(result['speechWaf'], {'paths': ['/speech']})
        speech.assert_called_once()

    def test_speech_waf_failure_is_not_reported_as_successful_deployment(self):
        config, state = fixture()
        config['apiDefinitions'].append(json.loads(
            (ROOT / 'apps/gateway/apim/apis/azure-speech-v1.json').read_text(encoding='utf-8')))
        with mock.patch.object(gateway, 'broker_enabled', return_value=False), \
                mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            with self.assertRaisesRegex(ValueError, 'speech WAF failed'):
                gateway.deploy_release(config, state, {'imageDigest': ''}, {'gateway': {}, 'apis': {}}, mock.Mock(),
                    deploy=lambda *args: {}, refresh=lambda *args: None, publish=lambda *args: {},
                    speech_waf=mock.Mock(side_effect=ValueError('speech WAF failed')),
                    progress=lambda *args, **kwargs: None)

    def test_offline_plan_includes_the_speech_waf_step_for_reconstruction(self):
        config, state = fixture()
        config['apiDefinitions'].append(json.loads(
            (ROOT / 'apps/gateway/apim/apis/azure-speech-v1.json').read_text(encoding='utf-8')))
        with self.isolated_cli(config, state) as (arguments, observed):
            arguments.remove('--execute')
            self.assertEqual(gateway.main(arguments), 0)
            plan = observed['protected_json'].call_args.args[1]
            self.assertIn('configure-gateway-speech-waf', plan['steps'])
            self.assertEqual(plan['azureWrites'], 0)
            observed['transport'].assert_not_called()
            observed['deploy_release'].assert_not_called()

    def test_document_waf_follows_publication_and_is_required_for_success(self):
        config,state=fixture()
        config['apiDefinitions'].append(json.loads((ROOT/'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8')))
        events=[]
        document=mock.Mock(side_effect=lambda *args: events.append('document-waf') or {'paths':['/tools/documents/v1/analyze']})
        with mock.patch.object(gateway,'broker_enabled',return_value=False), mock.patch.object(gateway.api_publication,'prepare_plan',return_value={}):
            result=gateway.deploy_release(config,state,{'imageDigest':''},{'gateway':{},'apis':{}},mock.Mock(),
                deploy=lambda *args:{},refresh=lambda *args:None,publish=lambda *args:events.append('apis') or {},document_waf=document,progress=lambda *args,**kwargs:None)
        self.assertEqual(events,['apis','document-waf'])
        self.assertEqual(result['documentWaf']['paths'],['/tools/documents/v1/analyze'])
        with mock.patch.object(gateway,'broker_enabled',return_value=False), mock.patch.object(gateway.api_publication,'prepare_plan',return_value={}):
            with self.assertRaisesRegex(ValueError,'document WAF failed'):
                gateway.deploy_release(config,state,{'imageDigest':''},{'gateway':{},'apis':{}},mock.Mock(),deploy=lambda *args:{},refresh=lambda *args:None,publish=lambda *args:{},document_waf=mock.Mock(side_effect=ValueError('document WAF failed')),progress=lambda *args,**kwargs:None)

    def test_offline_plan_includes_document_waf_without_writing_azure(self):
        config,state=fixture()
        config['apiDefinitions'].append(json.loads((ROOT/'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8')))
        with self.isolated_cli(config,state) as (arguments,observed):
            arguments.remove('--execute')
            self.assertEqual(gateway.main(arguments),0)
            plan=observed['protected_json'].call_args.args[1]
            self.assertIn('configure-gateway-document-waf',plan['steps'])
            self.assertEqual(plan['azureWrites'],0)
            observed['transport'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
