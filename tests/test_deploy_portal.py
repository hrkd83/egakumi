"""Portal配備の環境境界と、SQL初期化を先行する更新順序を確認する。"""
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
import deploy_portal as portal
from deployment.helpers.paths import local_paths


def fixture():
    config = {
        'subscriptionId': '11111111-1111-4111-8111-111111111111',
        'azureTenantId': '22222222-2222-4222-8222-222222222222',
        'resourceStem': 'test-sample', 'location': 'japaneast',
        'resourceGroupNames': {'svc': 'rg-test-sample-svc', 'id': 'rg-test-sample-id', 'nw': 'rg-test-sample-nw'},
        'network': {'subnetPrefixes': {'aca': '10.20.0.0/26'}},
        'tags': {}, 'productName': 'sample', 'environmentName': 'test',
        'portalAuthentication': {'credentialProviderName': 'graph-delegated'},
        'credentialProviders': [{'name': 'graph-delegated', 'clientId': '33333333-3333-4333-8333-333333333333',
                                  'oauthTenantId': '44444444-4444-4444-8444-444444444444'}],
        'portal': {'publicHostName': 'portal.example.com'},
        'gateway': {'publicHostName': 'gateway.example.com'},
    }
    identities = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/rg-test-sample-id/providers/Microsoft.ManagedIdentity/userAssignedIdentities/'
    state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
    state['outputs'] = {
        'portal': {'applicationName': 'ca-test-sample-portal', 'environmentName': 'cae-test-sample',
            'registryName': 'acrtestsample', 'pullIdentityId': identities + 'id-test-sample-portal-pull',
            'runtimeIdentity': {'id': identities + 'id-test-sample-portal-pull'},
            'schemaIdentity': {'id': identities + 'id-test-sample-credential-schema'},
            'sharedSqlServer': 'sql-test-sample.database.windows.net',
            'applicationId': portal.group_scope(config) + '/providers/Microsoft.App/containerApps/ca-test-sample-portal'},
    }
    return config, state


class PortalDeploymentTests(unittest.TestCase):
    def test_cli_refuses_public_or_other_environment_inputs_before_building(self):
        """同名ファイルを探したり、別環境の入力からbuildを始めたりしない。"""
        config, state = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = local_paths(config, root)
            workspace.ensure()
            workspace.params.write_text('unchanged-input')
            state_file = workspace.state / 'latest.json'
            state_file.write_text(json.dumps(state))
            before = {path: path.read_bytes() for path in (workspace.params, state_file)}
            for option, relative in (
                ('--config', 'params/test.local.json'),
                ('--config', '.local/other/params.local.json'),
                ('--state-file', '.local/other/state/latest.json'),
            ):
                with self.subTest(option=option, path=relative), \
                     mock.patch.object(portal, 'REPOSITORY', root), \
                     mock.patch.dict(os.environ, {'AZURE_ENV_NAME': config['environmentName'], 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True), \
                     mock.patch.object(portal, 'load_configuration', return_value=config), \
                     mock.patch.object(portal, 'source_bundle') as bundle, \
                     mock.patch.object(portal, 'run_json') as cli, \
                     mock.patch.object(portal, 'ArmTransport') as transport, \
                     mock.patch.object(portal, 'protected_json') as write:
                    arguments = ['--config', str(workspace.params), '--state-file', str(state_file), '--execute']
                    arguments[arguments.index(option) + 1] = str(root / relative)
                    with self.assertRaisesRegex(ValueError, r'Runtime (?:files|input/output)'):
                        portal.main(arguments)
                    for boundary in (bundle, cli, transport, write):
                        boundary.assert_not_called()
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_existing_core_output_is_usable_without_redeploying_core(self):
        config, state = fixture()
        original = copy.deepcopy(state)
        values = portal.deployment_inputs(config, state)
        self.assertEqual(values['sqlServer'], 'sql-test-sample.database.windows.net')
        self.assertEqual(values['publicOrigin'], 'https://portal.example.com')
        self.assertEqual(values['gatewayOrigin'], 'https://gateway.example.com')
        self.assertEqual(values['networkResourceGroup'], 'rg-test-sample-nw')
        self.assertEqual(values['acaPrefix'], '10.20.0.0/26')
        self.assertFalse(values['deployApplication'])
        self.assertEqual(values['tenantId'], config['credentialProviders'][0]['oauthTenantId'])
        self.assertEqual(values['apimName'], 'apim-test-sample')
        self.assertEqual(state, original)

    def test_gateway_origin_uses_the_selected_public_domain_without_a_shared_key(self):
        for host in ('gateway.test.example.com', 'gateway.production.example.com'):
            with self.subTest(host=host):
                config, state = fixture()
                config['gateway']['publicHostName'] = host
                values = portal.deployment_inputs(config, state)
                self.assertEqual(values['gatewayOrigin'], 'https://' + host)
                self.assertFalse(any('subscription_key' in key.lower() for key in values))

    def test_new_foundation_output_does_not_need_broker_application(self):
        config, state = fixture()
        output = state['outputs']['portal']
        self.assertNotIn('credentialBroker', state['outputs'])
        self.assertEqual(portal.deployment_inputs(config, state)['schemaIdentity'], output['schemaIdentity'])

    def test_legacy_identity_and_broker_aliases_do_not_fill_missing_current_outputs(self):
        for missing in ('runtimeIdentity', 'schemaIdentity', 'sharedSqlServer'):
            config, state = fixture()
            output = state['outputs']['portal']
            state['outputs']['credentialBroker'] = {'schemaIdentity': output['schemaIdentity'],
                'sqlServer': output['sharedSqlServer']}
            del output[missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                portal.deployment_inputs(config, state)

    def test_verification_ids_are_passed_to_the_schema_job_without_issuing_or_reading_keys(self):
        config, state = fixture()
        group_id = '55555555-5555-4555-8555-555555555555'
        declarations = [{'subscriptionId': 'existing-test', 'productId': 'model-users', 'displayName': 'Platform test'}]
        config['portalApiKeys'] = {'platformGroupId': group_id, 'groupNamePattern': '^ORG_',
            'groups': [{'groupId': group_id, 'products': [{'productId': 'model-users'}]}], 'verificationSubscriptions': declarations}
        result = portal.deployment_inputs(config, state)
        self.assertEqual(result['apiKeyVerificationSubscriptions'], declarations)

    def test_environment_mismatch_cannot_target_another_foundation(self):
        for mutate in (
            lambda c, s: s.update(subscriptionId='other-subscription'),
            lambda c, s: s['outputs']['portal'].update(applicationName='ca-other-portal'),
            lambda c, s: s['outputs']['portal'].update(applicationId='/another/app'),
            lambda c, s: s['outputs']['portal'].update(runtimeIdentity={'id': '/another/identity'}),
            lambda c, s: s['outputs']['portal'].update(sharedSqlServer='other.database.windows.net'),
            lambda c, s: c.update(credentialProviders=[]),
        ):
            config, state = fixture()
            mutate(config, state)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                portal.deployment_inputs(config, state)

    def test_existing_vault_signing_key_is_not_rotated(self):
        config, state = fixture()
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 200, 'body': {}}
        with mock.patch.object(portal, 'prepare_secret') as prepare:
            self.assertEqual(portal.signing_secret(config, portal.deployment_inputs(config, state), transport), '')
        prepare.assert_not_called()
        self.assertEqual(transport.request.call_args.args[0], 'GET')

    def test_signing_key_is_created_only_when_absent(self):
        config, state = fixture()
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 404}
        with mock.patch.object(portal, 'prepare_secret', return_value={'portalAuthenticationSecret': 'new-secret'}) as prepare:
            self.assertEqual(portal.signing_secret(config, portal.deployment_inputs(config, state), transport), 'new-secret')
        prepare.assert_called_once()
        transport.request.return_value = {'statusCode': 403}
        with self.assertRaises(ValueError):
            portal.signing_secret(config, portal.deployment_inputs(config, state), transport)

    def release(self, schema_failure=False, public_failure=False):
        config, state = fixture()
        values = portal.deployment_inputs(config, state)
        values.update(signingSecret='synthetic-signing-value', profileSecret='synthetic-profile-value')
        phases = []
        self.deployment_values = []
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 200, 'body': {'name': 'schema-execution'}}
        def schema(*args):
            phases.append('sql')
            if schema_failure:
                raise ValueError('schema failed')
        def deploy(config, values, *args):
            phases.append('application' if values['deployApplication'] else 'database')
            self.deployment_values.append(copy.deepcopy(values))
        def public(*args):
            phases.append('public')
            if public_failure:
                raise ValueError('public failed')
        with mock.patch.object(portal, 'deploy_template', side_effect=deploy), \
             mock.patch.object(portal, 'wait_schema', side_effect=schema), \
             mock.patch.object(portal, 'configure_portal_probe', side_effect=lambda *a: phases.append('gateway')), \
             mock.patch.object(portal, 'configure_portal_callback_waf', side_effect=lambda *a: phases.append('callback-waf')), \
             mock.patch.object(portal, 'configure_data_waf', side_effect=lambda *a, **k: phases.append('data-waf')), \
             mock.patch.object(portal, 'wait_application', side_effect=lambda *a: phases.append('ready')), \
             mock.patch.object(portal, 'verify_public', side_effect=public), \
             mock.patch.object(portal, 'activate_profile_settings', side_effect=lambda *a: phases.append('profiles')), \
             contextlib.redirect_stdout(io.StringIO()):
            if schema_failure or public_failure:
                with self.assertRaisesRegex(ValueError, 'schema failed' if schema_failure else 'public failed'):
                    portal.deploy_release(config, values, {}, transport)
            else:
                portal.deploy_release(config, values, {}, transport)
        return phases

    def test_schema_failure_preserves_running_application(self):
        self.assertEqual(self.release(schema_failure=True), ['database', 'sql'])

    def test_update_order_includes_public_verification(self):
        self.assertEqual(self.release(), ['database', 'sql', 'gateway', 'callback-waf', 'data-waf', 'application', 'ready', 'public', 'profiles'])

    def test_second_phase_reuses_existing_secret_versions(self):
        self.release()
        first, second = self.deployment_values
        self.assertEqual(first['signingSecret'], 'synthetic-signing-value')
        self.assertEqual(first['profileSecret'], 'synthetic-profile-value')
        self.assertEqual(second['signingSecret'], '')
        self.assertEqual(second['profileSecret'], '')

    def test_public_failure_never_activates_subscription_profiles(self):
        self.assertEqual(self.release(public_failure=True),
                         ['database', 'sql', 'gateway', 'callback-waf', 'data-waf', 'application', 'ready', 'public'])

    def test_public_health_uses_explicit_protocol_headers_and_requires_no_store(self):
        for cacheable in (False, True):
            response = io.StringIO(json.dumps({'status': 'ok', 'service': 'egakumi-portal'}))
            response.status = 200
            response.headers = {'Cache-Control': 'public, max-age=3600' if cacheable else 'no-store'}
            opener = mock.Mock()
            opener.open.return_value = response
            times = iter([0, 0, 2])
            with self.subTest(cacheable=cacheable), mock.patch.object(portal, 'build_opener', return_value=opener):
                if cacheable:
                    with self.assertRaisesRegex(ValueError, 'public health check'):
                        portal.verify_public('https://portal.example.com', timeout=1,
                            sleep=lambda _: None, monotonic=lambda: next(times))
                else:
                    portal.verify_public('https://portal.example.com', timeout=1,
                        sleep=lambda _: None, monotonic=lambda: next(times))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header('Accept'), 'application/json')
            self.assertEqual(request.get_header('User-agent'), 'egakumi-portal-deployment/1.0')

    def test_plan_does_not_authenticate_or_write_to_azure(self):
        config, state = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = local_paths(config, root)
            workspace.ensure()
            state_file = workspace.state / 'latest.json'
            state_file.write_text(json.dumps(state), encoding='utf-8')
            with mock.patch.object(portal, 'REPOSITORY', root), \
                 mock.patch.dict(os.environ, {'AZURE_ENV_NAME': config['environmentName'], 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True), \
                 mock.patch.object(portal, 'load_configuration', return_value=config), \
                 mock.patch.object(portal, 'source_bundle', return_value=(b'public', 'checksum')), \
                 mock.patch.object(portal, 'reusable_image', return_value=None), \
                 mock.patch.object(portal, 'ArmTransport') as transport, \
                 mock.patch.object(portal, 'run_json') as cli, \
                 mock.patch.object(portal, 'prepare_secret') as prepare, \
                 mock.patch.object(portal, 'profile_secret') as profile_secret, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(portal.main(['--config', str(workspace.params), '--state-file', str(state_file)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['azureWrites'], 0)
            self.assertFalse(result['azdUpRequired'])
            self.assertIn('prepare-apim-key-management-permission', result['steps'])
            self.assertIn('enable-subscription-profiles', result['steps'])
            transport.assert_not_called()
            cli.assert_not_called()
            prepare.assert_not_called()
            profile_secret.assert_not_called()
            saved = json.loads((workspace.state / 'portal-plan.json').read_text(encoding='utf-8'))
            self.assertNotIn('signingSecret', saved['parameters'])
            self.assertNotIn('profileSecret', saved['parameters'])


if __name__ == '__main__':
    unittest.main()
