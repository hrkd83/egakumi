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
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment import deploy_gateway as gateway
from deployment.helpers.paths import local_paths
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers import credential_broker_lifecycle
from deployment.helpers.gateway_obo_certificate import deployment_settings as gateway_obo_settings
from deployment.lifecycle import protected_json
from tests.deployment.test_credential_broker_initial_deployment import fixture as broker_fixture, DIGEST, ok
from tests.support.management_fixtures import client_core_outputs


def fixture():
    """現行infraのgatewayFoundationと、配備済みbrokerの独立した合成出力。"""
    config, state = broker_fixture()
    # このfixtureはCLIの正規化をmockするため、現行の固定Firewall入力を使う。
    for field in ('snatPrivateRanges', 'idpsPrivateRanges'):
        config['network']['firewall'].pop(field, None)
    state['apimInstanceId'] = apim_instance_id(config)
    stem, groups = config['resourceStem'], config['resourceGroupNames']
    state['outputs']['gatewayFoundation'] = {
        'resourceGroup': groups['svc'], 'identityResourceGroup': groups['id'],
        'apimName': apim_name(config), 'vaultName': 'kv-' + stem,
        'schemaIdentity': copy.deepcopy(state['outputs']['credentialBroker']['schemaIdentity']),
        'sqlServer': 'sql-' + stem + '.database.windows.net',
        'sqlPrivateEndpointId': '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + groups['svc'] + '/providers/Microsoft.Network/privateEndpoints/pep-' + stem + '-sql',
        'environmentName': 'cae-' + stem, 'environmentDomain': 'environment.example.com',
        'brokerEnvironmentName': 'cae-' + stem + '-broker',
        'brokerEnvironmentId': credential_broker_lifecycle.broker_environment_id(config),
        'brokerEnvironmentDomain': 'synthetic-broker.fixture.' + 'azurecontainerapps.io',
        'brokerSubnetId': '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + groups['nw']
                          + '/providers/Microsoft.Network/virtualNetworks/vnet-' + stem + '/subnets/CredentialBrokerSubnet',
        'registryName': 'acr' + stem.replace('-', ''),
    }
    host = config['gateway']['publicHostName']
    state['outputs'].setdefault('ingressSecrets', {}).update(enabled=True, l7IngressEnabled=True, publicHostName=host)
    state['outputs'].update(client_core_outputs(config))
    return config, state


def speech_fixture():
    """正規化後のCLI境界を使うため、未展開tokenを独立した合成URLへ置き換える。"""
    definition = (ROOT / 'apps/api-gateway/apis/azure-speech-v1.json').read_text(encoding='utf-8')
    for marker, value in {
        '__SPEECH_TTS_BASE_URL__': 'https://speech-tts.example.invalid',
        '__SPEECH_STT_BASE_URL__': 'https://speech-stt.example.invalid',
        '__SPEECH_RESOURCE_ID__': '/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture-svc/providers/Microsoft.CognitiveServices/accounts/speech-fixture',
    }.items():
        definition = definition.replace(marker, value)
    return json.loads(definition)


class GatewayDeploymentTests(unittest.TestCase):
    def setUp(self):
        # These tests mock CLI subprocesses and clear the process environment.
        # Real Windows DACLs are exercised by the dedicated Serial suite;
        # POSIX runs retain actual private-file mode checks here.
        if os.name == 'nt':
            acl = mock.patch('deployment.helpers.private_permissions._windows_owner_acl')
            acl.start()
            self.addCleanup(acl.stop)

    def test_graph_obo_uses_api_identity_and_requires_target_vault_and_key_version(self):
        config, state = fixture()
        foundation = state['outputs']['gatewayFoundation']
        config['entraTenantId'] = '88888888-8888-8888-8888-888888888888'
        auth = config['gateway'].setdefault('authentication', {})
        auth['credentialLogin'] = {'identity': {'clientId': config['subscriptionId']},
                                   'providerNames': ['graph-delegated']}
        entra = auth.setdefault('entra', {})
        entra['audience'] = '77777777-7777-4777-8777-777777777777'
        entra['requiredScope'] = 'Gateway.Invoke'
        with tempfile.TemporaryDirectory() as directory:
            workspace = local_paths(config, Path(directory).resolve())
            with self.assertRaisesRegex(ValueError, 'register|Register'):
                gateway_obo_settings(config, workspace)
            key_url = f"https://{foundation['vaultName']}.vault.azure.net/keys/gateway-obo/" + 'a' * 32
            entra['obo'] = {'keyId': key_url, 'certificateThumbprint': '00' * 32}
            self.assertEqual(gateway_obo_settings(config, workspace), {
                'tenantId': config['entraTenantId'], 'clientId': entra['audience'],
                'keyUrl': key_url, 'certificateThumbprint': 'A' * 43,
            })
            for wrong_url in (key_url.rsplit('/', 1)[0], key_url.replace('gateway-obo', 'portal-auth'), key_url + '?other=value'):
                with self.subTest(wrong_url=wrong_url), self.assertRaisesRegex(ValueError, 'selected infrastructure vault'):
                    entra['obo']['keyId'] = wrong_url
                    gateway_obo_settings(config, workspace)

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

    def test_missing_core_inventory_stops_before_build_or_azure(self):
        config, state = fixture()
        config['publishApis'] = True
        del state['outputs']['managementClientInventory']
        with self.isolated_cli(config, state) as (arguments, observed):
            with self.assertRaisesRegex(ValueError, 'management client inventory is required'):
                gateway.main(arguments)
            for boundary in ('source_bundle', 'run_json', 'transport', 'build_image', 'deploy_release'):
                observed[boundary].assert_not_called()

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

    def test_stale_ingress_foundation_stops_before_build_or_azure_requests(self):
        for field,value in (("enabled", False), ("l7IngressEnabled", False), ("publicHostName", "foreign.example.com")):
            config,state=fixture()
            config["publishApis"]=True
            state["outputs"]["ingressSecrets"][field]=value
            with self.subTest(field=field),self.isolated_cli(config,state) as (arguments,observed):
                with self.assertRaisesRegex(ValueError,"Deploy the infrastructure"): gateway.main(arguments)
                for boundary in ("source_bundle","run_json","transport","build_image","deploy_release","publication_plan"):
                    observed[boundary].assert_not_called()

    def test_gateway_publication_uses_core_outputs_and_broker_without_additional_workloads(self):
        config, state = fixture()
        config["publishApis"] = True
        with self.isolated_cli(config, state) as (arguments, observed):
            self.assertEqual(gateway.main(arguments), 0)
            self.assertEqual(observed["run_json"].call_count, 2)
            self.assertEqual(set(observed["deploy_release"].call_args.args[3]), {"gateway", "apis"})
            observed["publication_plan"].assert_called_once_with(config, state)

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

    def test_foundation_requires_complete_core_outputs(self):
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

    def test_empty_business_login_still_prepares_key_and_discovers_existing_broker(self):
        config, state = fixture()
        config['gateway']['authentication'].pop('credentialLogin')
        config['credentialProviders'] = []
        writer = mock.Mock()
        with mock.patch.object(credential_broker_lifecycle, 'prepare_credential', return_value='synthetic-key') as prepare:
            self.assertEqual(gateway.prepare_secret(config, ROOT, writer), {'credentialBrokerKey': 'synthetic-key'})
        prepare.assert_called_once_with(config, ROOT, 'credential-broker', writer)
        prefix = (gateway.group_scope(config) + '/providers/Microsoft.App/')
        transport = mock.Mock()
        transport.request.return_value = ok({
            'id': prefix + 'containerApps/' + state['outputs']['credentialBroker']['applicationName'],
            'properties': {'managedEnvironmentId': state['outputs']['gatewayFoundation']['brokerEnvironmentId'],
                           'provisioningState': 'Succeeded'},
        })
        self.assertTrue(credential_broker_lifecycle.discover_existing_application(config, transport))
        self.assertEqual(transport.request.call_args.args[0], 'GET')
        transport.request.return_value = ok(code=404)
        self.assertFalse(credential_broker_lifecycle.discover_existing_application(config, transport))

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
                client_prepare=lambda *args, **kwargs: None,
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

    def test_release_reuses_only_its_already_held_gateway_environment_lock(self):
        config, state = fixture()
        config['publishApis'] = True
        with self.isolated_cli(config, state) as (arguments, observed):
            workspace = local_paths(config, gateway.REPOSITORY)
            with gateway.lock_environment(workspace):
                with self.assertRaisesRegex(gateway.OperationError, 'Another operation'):
                    gateway.main(arguments)
                with self.assertRaisesRegex(ValueError, 'held release lock does not match'):
                    gateway.main(arguments, _locked_workspace=object())
                self.assertEqual(gateway.main(arguments + ['--image-digest', DIGEST], _locked_workspace=workspace), 0)
            observed['deploy_release'].assert_called_once()

    def test_generated_source_is_saved_protected_and_excluded_from_cli_summary(self):
        config, state = fixture()
        config['publishApis'] = True
        source = {'schemaVersion': 1, 'fixtureSourceXml': '<fragment>synthetic-private-source</fragment>'}
        result = {'status': 'deployed', 'apiPublication': {'entraPublicationSource': source, 'runtimeVerified': False}}
        with self.isolated_cli(config, state) as (arguments, observed):
            observed['deploy_release'].return_value = result
            self.assertEqual(gateway.main(arguments + ['--image-digest', DIGEST]), 0)
            stored_path, stored = observed['protected_json'].call_args.args
            self.assertEqual(stored_path.name, 'gateway-deployment.json')
            self.assertEqual(stored['apiPublication']['entraPublicationSource'], source)
            self.assertNotIn('synthetic-private-source', sys.stdout.getvalue())
            self.assertNotIn('entraPublicationSource', sys.stdout.getvalue())
            self.assertEqual(result['apiPublication']['entraPublicationSource'], source)

    def test_resume_preserves_recorded_workload_evidence_and_updates_gateway_receipt(self):
        config, state = fixture()
        config['publishApis'] = True
        plan = {'apimId': 'synthetic-apim', 'deploymentId': 'synthetic-publication'}
        workload = {'azdUpExecuted': False, 'sqlInitialized': True, 'applicationReady': True,
                    'imageDigest': DIGEST, 'credentialBroker': {'provisioningState': 'Succeeded'}}
        publication = {'status': 'management-plane-published', 'entraPublicationSource': {'privateSource': True}}
        waf = {'speechWaf': {'prepared': True}}
        with self.isolated_cli(config, state) as (arguments, observed), \
                mock.patch.object(gateway.api_publication, 'execute_plan', return_value=publication) as resume, \
                mock.patch.object(gateway, 'configure_gateway_waf', return_value=waf):
            observed['publication_plan'].return_value = plan
            path = Path(arguments[3]).parent / 'gateway-api-closed-stage.json'
            stage = {'scope': {**gateway.publication_scope(config, plan), 'resourceStem': 'synthetic-foreign'},
                     'gatewayDeployment': workload, 'publication': {'schemaVersion': 1}}
            protected_json(path, stage)
            with self.assertRaisesRegex(ValueError, 'does not match the selected publication'):
                gateway.main(arguments + ['--resume-closed-stage'])
            observed['transport'].assert_not_called()
            resume.assert_not_called()
            stage['scope'] = gateway.publication_scope(config, plan)
            protected_json(path, stage)
            self.assertEqual(gateway.main(arguments + ['--resume-closed-stage']), 0)
            self.assertEqual(resume.call_args.kwargs['stage_record'], stage['publication'])
            saved_path, saved = observed['protected_json'].call_args.args
            self.assertEqual(saved_path.name, 'gateway-deployment.json')
            self.assertEqual(saved, {**workload, **waf, 'status': 'deployed', 'publicationResumed': True,
                'apiPublication': publication, 'runtimeVerified': False})
            observed['deploy_release'].assert_not_called()
            observed['build_image'].assert_not_called()
            self.assertNotIn('entraPublicationSource', sys.stdout.getvalue())

    def test_empty_business_profiles_still_initialize_broker_before_publication(self):
        config, state = fixture()
        config['gateway']['authentication'].pop('credentialLogin')
        config['credentialProviders'] = []
        events = []
        transport = mock.Mock()
        transport.request.side_effect = lambda *args: events.append('schema-start') or ok({'name': 'schema-run'})
        schema_wait = mock.Mock(side_effect=lambda *args: events.append('schema-wait'))
        readiness_wait = mock.Mock(side_effect=lambda *args: events.append('ready'))
        callback_waf = mock.Mock()
        values = {'deployApplication': False, 'imageDigest': DIGEST, 'brokerKey': 'synthetic-initial-key'}
        def settings(*args):
            events.append('app' if values['deployApplication'] else 'database')
            if values['deployApplication']:
                self.assertEqual(values['brokerKey'], '')
            return {'credentialBroker': state['outputs']['credentialBroker']}
        with mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            result = gateway.deploy_release(config, state, values, {'gateway': {}, 'apis': {}}, transport,
                deploy=settings, schema_wait=schema_wait, readiness_wait=readiness_wait,
                refresh=lambda *args: events.append('named-values'),
                publish=lambda *args: events.append('apis') or {'status': 'management-plane-published'},
                callback_waf=callback_waf, data_waf=lambda *args, **kwargs: events.append('data-waf'),
                client_prepare=lambda *args, **kwargs: events.append('maintenance'),
                progress=lambda *args, **kwargs: None)
        self.assertEqual(events, ['database', 'maintenance', 'schema-start', 'schema-wait',
                                 'app', 'maintenance', 'ready', 'named-values', 'apis', 'data-waf'])
        schema_wait.assert_called_once_with(config, 'schema-run', transport)
        readiness_wait.assert_called_once_with(config, transport)
        callback_waf.assert_not_called()
        self.assertEqual(transport.request.call_args.args[0], 'POST')
        self.assertTrue(result['sqlInitialized'])
        self.assertTrue(result['applicationReady'])
        self.assertNotIn('credentialLogin', config['gateway']['authentication'])

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

    def test_speech_waf_follows_api_publication_without_business_login(self):
        config, state = fixture()
        config['gateway']['authentication'].pop('credentialLogin')
        config['apiDefinitions'].append(speech_fixture())
        events = []
        speech = mock.Mock(side_effect=lambda *args: events.append('speech-waf') or {'paths': ['/speech']})
        transport = mock.Mock()
        transport.request.return_value = ok({'name': 'schema-run'})
        with mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            result = gateway.deploy_release(config, state, {'imageDigest': DIGEST, 'deployApplication': False}, {'gateway': {}, 'apis': {}}, transport,
                deploy=lambda *args: {}, refresh=lambda *args: None,
                publish=lambda *args: events.append('apis') or {}, speech_waf=speech,
                schema_wait=lambda *args: None, readiness_wait=lambda *args: None,
                client_prepare=lambda *args, **kwargs: None, data_waf=lambda *args, **kwargs: None,
                progress=lambda *args, **kwargs: None)
        self.assertEqual(events, ['apis', 'speech-waf'])
        self.assertEqual(result['speechWaf'], {'paths': ['/speech']})
        speech.assert_called_once()

    def test_speech_waf_failure_is_not_reported_as_successful_deployment(self):
        config, state = fixture()
        config['gateway']['authentication'].pop('credentialLogin')
        config['apiDefinitions'].append(speech_fixture())
        transport = mock.Mock()
        transport.request.return_value = ok({'name': 'schema-run'})
        with mock.patch.object(gateway.api_publication, 'prepare_plan', return_value={}):
            with self.assertRaisesRegex(ValueError, 'speech WAF failed'):
                gateway.deploy_release(config, state, {'imageDigest': DIGEST, 'deployApplication': False}, {'gateway': {}, 'apis': {}}, transport,
                    deploy=lambda *args: {}, refresh=lambda *args: None, publish=lambda *args: {},
                    speech_waf=mock.Mock(side_effect=ValueError('speech WAF failed')),
                    schema_wait=lambda *args: None, readiness_wait=lambda *args: None,
                    client_prepare=lambda *args, **kwargs: None,
                    progress=lambda *args, **kwargs: None)

    def test_offline_plan_includes_the_speech_waf_step_for_reconstruction(self):
        config, state = fixture()
        config['apiDefinitions'].append(speech_fixture())
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
        config['gateway']['authentication'].pop('credentialLogin')
        config['apiDefinitions'].append(json.loads((ROOT/'apps/api-gateway/apis/document-analysis-v1.json').read_text(encoding='utf-8')))
        events=[]
        document=mock.Mock(side_effect=lambda *args: events.append('document-waf') or {'paths':['/tools/documents/v1/analyze']})
        transport = mock.Mock()
        transport.request.return_value = ok({'name': 'schema-run'})
        with mock.patch.object(gateway.api_publication,'prepare_plan',return_value={}):
            result=gateway.deploy_release(config,state,{'imageDigest':DIGEST,'deployApplication':False},{'gateway':{},'apis':{}},transport,
                deploy=lambda *args:{},refresh=lambda *args:None,publish=lambda *args:events.append('apis') or {},document_waf=document,
                schema_wait=lambda *args:None,readiness_wait=lambda *args:None,client_prepare=lambda *args,**kwargs:None,
                data_waf=lambda *args,**kwargs:events.append('data-waf') or {},progress=lambda *args,**kwargs:None)
        self.assertEqual(events,['apis','document-waf','data-waf'])
        self.assertEqual(result['documentWaf']['paths'],['/tools/documents/v1/analyze'])
        with mock.patch.object(gateway.api_publication,'prepare_plan',return_value={}):
            with self.assertRaisesRegex(ValueError,'document WAF failed'):
                gateway.deploy_release(config,state,{'imageDigest':DIGEST,'deployApplication':False},{'gateway':{},'apis':{}},transport,
                    deploy=lambda *args:{},refresh=lambda *args:None,publish=lambda *args:{},
                    document_waf=mock.Mock(side_effect=ValueError('document WAF failed')),
                    schema_wait=lambda *args:None,readiness_wait=lambda *args:None,client_prepare=lambda *args,**kwargs:None,
                    progress=lambda *args,**kwargs:None)

    def test_offline_plan_includes_document_waf_without_writing_azure(self):
        config,state=fixture()
        config['apiDefinitions'].append(json.loads((ROOT/'apps/api-gateway/apis/document-analysis-v1.json').read_text(encoding='utf-8')))
        with self.isolated_cli(config,state) as (arguments,observed):
            arguments.remove('--execute')
            self.assertEqual(gateway.main(arguments),0)
            plan=observed['protected_json'].call_args.args[1]
            self.assertIn('configure-gateway-document-waf',plan['steps'])
            self.assertEqual(plan['azureWrites'],0)
            observed['transport'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
