"""Portal配備の環境境界と、SQL初期化を先行する更新順序を確認する。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment import deploy_portal as portal
from deployment.helpers.paths import local_paths
from tests.support.management_fixtures import client_core_outputs


def fixture():
    config = {
        'subscriptionId': '11111111-1111-4111-8111-111111111111',
        'azureTenantId': '22222222-2222-4222-8222-222222222222',
        'resourceStem': 'test-sample', 'location': 'japaneast',
        'services': {'apimInstanceId': 'abc123'},
        'resourceGroupNames': {'svc': 'rg-test-sample-svc', 'id': 'rg-test-sample-id', 'nw': 'rg-test-sample-nw',
                               'maint': 'rg-test-sample-maint', 'monitor': 'rg-test-sample-monitor'},
        'network': {'subnetPrefixes': {'portal': '192.0.2.0/26', 'apim': '192.0.2.64/26'}},
        'entraTenantId': '44444444-4444-4444-8444-444444444444',
        'tags': {}, 'productName': 'sample', 'environmentName': 'test',
        'portalAuthentication': {},
        'credentialProviders': [{'name': 'graph-delegated', 'clientId': '33333333-3333-4333-8333-333333333333',
                                  'oauthTenantId': '44444444-4444-4444-8444-444444444444'}],
        'portal': {'publicHostName': 'portal.example.com', 'apiAuthentication': {'enabled': True}, 'authentication': {
            'tenantId': '44444444-4444-4444-8444-444444444444',
            'clientId': '33333333-3333-4333-8333-333333333333',
            'keyId': 'https://kv-test-sample.vault.azure.net/keys/portal-auth/' + 'a'*32,
            'certificateThumbprint': 'a'*40}},
        'gateway': {'publicHostName': 'gateway.example.com', 'authentication': {'entra': {
            'audience': '55555555-5555-4555-8555-555555555555', 'requiredScope': 'Gateway.Invoke'}}},
        'apiDefinitions': [],
    }
    identities = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/rg-test-sample-id/providers/Microsoft.ManagedIdentity/userAssignedIdentities/'
    state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
    state['apimInstanceId'] = 'abc123'
    state['outputs'] = {
        **client_core_outputs(config),
        'names': {'apim': 'apim-test-sample-abc123'},
        'gateway': {'publicUrl': 'https://gateway.example.com'},
        'portal': {'applicationName': 'ca-test-sample-portal', 'environmentName': 'cae-test-sample',
            'registryName': 'acrtestsample', 'pullIdentityId': identities + 'id-test-sample-portal-pull',
            'runtimeIdentity': {'id': identities + 'id-test-sample-portal-pull'},
            'schemaIdentity': {'id': identities + 'id-test-sample-credential-schema'},
            'authIdentity': {'id': identities + 'id-test-sample-portal-auth'},
            'apiAuthIdentity': {'id': identities + 'id-test-sample-portal-api-auth'},
            'sharedSqlServer': 'sql-test-sample.database.windows.net',
            'applicationId': portal.group_scope(config) + '/providers/Microsoft.App/containerApps/ca-test-sample-portal'},
    }
    return config, state


class PortalDeploymentTests(unittest.TestCase):
    def setUp(self):
        # 証明書stateの境界は専用試験で確認し、配備順序のfixtureへ登録済み設定を渡します。
        authentication = mock.patch.object(portal, 'deployment_authentication', side_effect=lambda config, workspace, slot: config['portal']['authentication'])
        authentication.start()
        self.addCleanup(authentication.stop)
        # Native ACL behavior is checked by the dedicated Serial integration
        # suite. This offline CLI fixture clears environment/subprocess state.
        if os.name == 'nt':
            acl = mock.patch('deployment.helpers.private_permissions._windows_owner_acl')
            acl.start()
            self.addCleanup(acl.stop)

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

    def test_cli_records_verified_tls_after_portal_release(self):
        config, state = fixture()
        readiness = {"protocol": "Https", "port": 443, "portalProbe": "/api/health"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = local_paths(config, root)
            workspace.ensure()
            state_file = workspace.state / "latest.json"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(portal, "REPOSITORY", root), \
                 mock.patch.dict(os.environ, {"AZURE_ENV_NAME": config["environmentName"]}, clear=True), \
                 mock.patch.object(portal, "load_configuration", return_value=config), \
                 mock.patch.object(portal, "ArmTransport"), \
                 mock.patch.object(portal, "require_appgw_backend_tls_ready") as verify_tls, \
                 mock.patch.object(portal, "run_json", return_value={}), \
                 mock.patch.object(portal, "verify_foundation"), \
                 mock.patch.object(portal, "signing_secret"), \
                 mock.patch("deployment.helpers.portal_api_auth.ensure_initial_key", return_value="synthetic-key-ref"), \
                 mock.patch.object(portal, "deploy_release", return_value={
                     "backendTls": readiness, "callbackWaf": {}, "dataWaf": {}}), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(portal.main(["--config", str(workspace.params), "--state-file", str(state_file),
                                              "--image-digest", "sha256:" + "a" * 64, "--execute"]), 0)
            verify_tls.assert_called_once()
            receipt = json.loads((workspace.state / "portal-deployment.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["backendTls"], readiness)
            self.assertTrue(receipt["publicHealthVerified"])

    def test_existing_core_output_is_usable_without_redeploying_core(self):
        config, state = fixture()
        original = copy.deepcopy(state)
        values = portal.deployment_inputs(config, state)
        self.assertEqual(values['sqlServer'], 'sql-test-sample.database.windows.net')
        self.assertEqual(values['publicOrigin'], 'https://portal.example.com')
        self.assertEqual(values['gatewayOrigin'], 'https://gateway.example.com')
        self.assertEqual(values['networkResourceGroup'], 'rg-test-sample-nw')
        self.assertEqual(values['portalPrefix'], config['network']['subnetPrefixes']['portal'])
        self.assertFalse(values['deployApplication'])
        self.assertEqual(values['tenantId'], config['credentialProviders'][0]['oauthTenantId'])
        self.assertEqual(state, original)

    def test_portal_firewall_source_must_match_the_live_environment_and_subnet(self):
        for mismatch in (None, 'cae-subnet', 'cidr', 'subnet-id'):
            with self.subTest(mismatch=mismatch):
                config, state = fixture()
                config['portal']['apiAuthentication']['enabled'] = False
                values = portal.deployment_inputs(config, state)
                environment_id = (portal.group_scope(config) + '/providers/Microsoft.App/managedEnvironments/'
                                  + values['environmentName'])
                subnet_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/rg-test-sample-nw"
                             '/providers/Microsoft.Network/virtualNetworks/vnet-test-sample/subnets/PortalSubnet')
                subnet = {'id': subnet_id.upper(), 'properties': {'addressPrefix': values['portalPrefix']}}
                environment = {'id': environment_id.upper(), 'properties': {'provisioningState': 'Succeeded',
                    'vnetConfiguration': {'internal': True, 'infrastructureSubnetId': subnet_id.upper()}}}
                if mismatch == 'cae-subnet':
                    environment['properties']['vnetConfiguration']['infrastructureSubnetId'] = subnet_id + '-other'
                elif mismatch == 'cidr':
                    subnet['properties'] = {'addressPrefixes': [config['network']['subnetPrefixes']['apim']]}
                elif mismatch == 'subnet-id':
                    subnet['id'] += '-other'
                identities = {values[key]['id'] for key in ('runtimeIdentity', 'schemaIdentity', 'authIdentity')}
                sql_id = portal.group_scope(config) + '/providers/Microsoft.Sql/servers/sql-test-sample'
                inventory = {environment_id: environment, subnet_id: subnet, sql_id: {'properties': {
                    'fullyQualifiedDomainName': values['sqlServer'], 'publicNetworkAccess': 'Disabled'}}}

                def request(method, url, body):
                    self.assertEqual(method, 'GET')
                    self.assertIsNone(body)
                    resource_id = url.split('?', 1)[0]
                    if resource_id in identities:
                        resource = {'id': resource_id, 'name': resource_id.rsplit('/', 1)[1],
                                    'properties': {'clientId': config['subscriptionId'],
                                                   'principalId': config['azureTenantId']}}
                    else:
                        resource = inventory[resource_id]
                    return {'statusCode': 200, 'body': resource}

                transport = mock.Mock()
                transport.request.side_effect = request
                if mismatch is None:
                    portal.verify_foundation(config, values, transport)
                    subnet['properties'] = {'addressPrefixes': [values['portalPrefix']]}
                    portal.verify_foundation(config, values, transport)
                else:
                    with self.assertRaisesRegex(ValueError, 'Portal subnet'):
                        portal.verify_foundation(config, values, transport)

    def test_app_preparation_requires_core_maintenance_inventory(self):
        config, state = fixture()
        del state['outputs']['managementClientInventory']
        with self.assertRaisesRegex(ValueError, 'management client inventory is required'):
            portal.deployment_inputs(config, state)

    def test_portal_rejects_missing_or_mismatched_apim_identity(self):
        for change in ('missing-id', 'other-id', 'other-name'):
            with self.subTest(change=change):
                config, state = fixture()
                if change == 'missing-id':
                    del state['apimInstanceId']
                elif change == 'other-id':
                    state['apimInstanceId'] = 'def456'
                else:
                    state['outputs']['names']['apim'] = 'apim-test-sample-def456'
                with self.assertRaisesRegex(ValueError, 'state differs|APIM name differs'):
                    portal.deployment_inputs(config, state)

    def test_gateway_origin_uses_the_selected_public_domain_without_a_shared_key(self):
        for host in ('gateway.test.example.com', 'gateway.production.example.com'):
            with self.subTest(host=host):
                config, state = fixture()
                config['gateway']['publicHostName'] = host
                state['outputs']['gateway']['publicUrl'] = 'https://' + host
                values = portal.deployment_inputs(config, state)
                self.assertEqual(values['gatewayOrigin'], 'https://' + host)
                self.assertFalse(any('subscription_key' in key.lower() for key in values))

    def test_missing_or_invalid_gateway_output_is_not_filled_from_configuration_or_aliases(self):
        for change in ('missing-gateway', 'missing-url', 'null-gateway', 'list-gateway',
                       'null-url', 'number-url', 'object-url', 'empty-url'):
            with self.subTest(change=change):
                config, state = fixture()
                expected = state['outputs']['gateway']['publicUrl']
                state['outputs']['endpoints'] = {'public': expected}
                state['outputs']['portal']['gatewayOrigin'] = expected
                if change == 'missing-gateway':
                    del state['outputs']['gateway']
                elif change == 'missing-url':
                    del state['outputs']['gateway']['publicUrl']
                elif change in ('null-gateway', 'list-gateway'):
                    state['outputs']['gateway'] = None if change == 'null-gateway' else []
                else:
                    state['outputs']['gateway']['publicUrl'] = {
                        'null-url': None, 'number-url': 443, 'object-url': {'url': expected},
                        'empty-url': '',
                    }[change]
                original = copy.deepcopy((config, state))
                with self.assertRaisesRegex(ValueError, '^Current Core Gateway public URL output is required$'):
                    portal.deployment_inputs(config, state)
                self.assertEqual((config, state), original)

    def test_gateway_url_mismatch_or_non_origin_value_stops_without_exposing_or_changing_inputs(self):
        invalid_urls = (
            'https://another.example.com', 'http://gateway.example.com',
            'https://gateway.example.com:443', 'https://gateway.example.com:8443',
            'https://operator@gateway.example.com', 'https://gateway.example.com/',
            'https://gateway.example.com/path', 'https://gateway.example.com?query=value',
            'https://gateway.example.com#fragment', ' https://gateway.example.com',
            'https://gateway.example.com\n', 'https://gateway.example.com\t',
            'not-an-origin',
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                config, state = fixture()
                state['outputs']['gateway']['publicUrl'] = url
                original = copy.deepcopy((config, state))
                with self.assertRaises(ValueError) as rejected:
                    portal.deployment_inputs(config, state)
                self.assertEqual(str(rejected.exception),
                                 'Core Gateway public URL differs from the selected configuration')
                self.assertNotIn(url, str(rejected.exception))
                self.assertNotIn(config['gateway']['publicHostName'], str(rejected.exception))
                self.assertEqual((config, state), original)

    def test_configuration_only_gateway_change_requires_matching_core_output(self):
        config, state = fixture()
        config['gateway']['publicHostName'] = 'new-gateway.example.com'
        original = copy.deepcopy((config, state))
        with self.assertRaisesRegex(ValueError, '^Core Gateway public URL differs from the selected configuration$'):
            portal.deployment_inputs(config, state)
        self.assertEqual((config, state), original)

    def test_cli_rejects_missing_or_mismatched_gateway_before_preparation_build_or_azure_writes(self):
        for gateway in (None, {'publicUrl': 'https://another.example.com'}):
            with self.subTest(gateway=gateway), tempfile.TemporaryDirectory() as directory:
                config, state = fixture()
                state['outputs']['gateway'] = gateway
                root = Path(directory).resolve()
                workspace = local_paths(config, root)
                workspace.ensure()
                workspace.params.write_text('unchanged-input')
                state_file = workspace.state / 'latest.json'
                state_file.write_text(json.dumps(state))
                before = {path: path.read_bytes() for path in (workspace.params, state_file)}
                with mock.patch.object(portal, 'REPOSITORY', root), \
                     mock.patch.dict(os.environ, {'AZURE_ENV_NAME': config['environmentName'], 'PYTHONDONTWRITEBYTECODE': '1'}, clear=True), \
                     mock.patch.object(portal, 'load_configuration', return_value=config), \
                     mock.patch.object(portal, 'source_bundle') as bundle, \
                     mock.patch.object(portal, 'run_json') as cli, \
                     mock.patch.object(portal, 'ArmTransport') as transport, \
                     mock.patch.object(portal, 'protected_json') as write:
                    with self.assertRaisesRegex(ValueError, 'Core Gateway public URL'):
                        portal.main(['--config', str(workspace.params), '--state-file', str(state_file), '--execute'])
                    for boundary in (bundle, cli, transport, write):
                        boundary.assert_not_called()
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_core_output_supplies_portal_schema_identity(self):
        config, state = fixture()
        output = state['outputs']['portal']
        self.assertNotIn('credentialBroker', state['outputs'])
        self.assertEqual(portal.deployment_inputs(config, state)['schemaIdentity'], output['schemaIdentity'])

    def test_portal_requires_runtime_schema_and_sql_outputs(self):
        for missing in ('runtimeIdentity', 'schemaIdentity', 'sharedSqlServer'):
            config, state = fixture()
            output = state['outputs']['portal']
            del output[missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                portal.deployment_inputs(config, state)

    def test_environment_mismatch_cannot_target_another_foundation(self):
        for mutate in (
            lambda c, s: s.update(subscriptionId='other-subscription'),
            lambda c, s: s['outputs']['portal'].update(applicationName='ca-other-portal'),
            lambda c, s: s['outputs']['portal'].update(applicationId='/another/app'),
            lambda c, s: s['outputs']['portal'].update(runtimeIdentity={'id': '/another/identity'}),
            lambda c, s: s['outputs']['portal'].update(sharedSqlServer='other.database.windows.net'),
            lambda c, s: c['portal'].pop('authentication'),
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

    def release(self, schema_failure=False, public_failure=False, readiness_failure=False):
        config, state = fixture()
        values = portal.deployment_inputs(config, state)
        values.update(signingSecret='synthetic-signing-value', imageDigest='sha256:' + 'a' * 64)
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
        def public(*args, api_authentication=False):
            phases.append('bearer-trial' if api_authentication else 'public')
            if public_failure:
                raise ValueError('public failed')
        def tls_ready(*args, **kwargs):
            phases.append('tls-ready')
            if readiness_failure:
                raise ValueError('readiness failed')
            return {'protocol': 'Https', 'port': 443}
        with mock.patch.object(portal, 'deploy_template', side_effect=deploy), \
             mock.patch.object(portal, 'wait_schema', side_effect=schema), \
             mock.patch.object(portal, 'configure_portal_callback_waf', side_effect=lambda *a: phases.append('callback-waf')), \
             mock.patch.object(portal, 'configure_data_waf', side_effect=lambda *a, **k: phases.append('data-waf')), \
             mock.patch.object(portal, 'wait_application', side_effect=lambda *a, **k: phases.append('ready')), \
             mock.patch.object(portal, 'require_appgw_backend_tls_ready', side_effect=tls_ready), \
             mock.patch.object(portal, 'verify_public', side_effect=public), \
             contextlib.redirect_stdout(io.StringIO()):
            if schema_failure or public_failure or readiness_failure:
                error = 'schema failed' if schema_failure else 'public failed' if public_failure else 'readiness failed'
                with self.assertRaisesRegex(ValueError, error):
                    portal.deploy_release(config, values, {}, transport, state=state, client_prepare=lambda *a, **k: None)
            else:
                release = portal.deploy_release(config, values, {}, transport, state=state, client_prepare=lambda *a, **k: None)
                self.assertEqual(release['backendTls'], {'protocol': 'Https', 'port': 443})
        transport.request.assert_called_once_with('POST', portal.group_scope(config)
            + '/providers/Microsoft.App/jobs/caj-' + config['resourceStem'] + '-psql/start?api-version=' + portal.APP_API, None)
        return phases

    def test_schema_failure_preserves_running_application(self):
        self.assertEqual(self.release(schema_failure=True), ['database', 'sql'])

    def test_update_order_includes_public_verification(self):
        self.assertEqual(self.release(), ['database', 'sql', 'callback-waf', 'data-waf', 'application',
                                          'ready', 'tls-ready', 'public', 'bearer-trial'])

    def test_second_phase_reuses_existing_secret_versions(self):
        self.release()
        first, second = self.deployment_values
        self.assertEqual(first['signingSecret'], 'synthetic-signing-value')
        self.assertEqual(second['signingSecret'], '')

    def test_portal_public_health_failure_stops_bearer_trial_verification(self):
        self.assertEqual(self.release(public_failure=True),
                         ['database', 'sql', 'callback-waf', 'data-waf', 'application',
                          'ready', 'tls-ready', 'public'])

    def test_tls_readiness_failure_stops_public_verification(self):
        self.assertEqual(self.release(readiness_failure=True),
                         ['database', 'sql', 'callback-waf', 'data-waf', 'application',
                          'ready', 'tls-ready'])

    def test_application_waits_for_expected_image_in_both_app_and_ready_revision(self):
        config, _ = fixture()
        digest = 'sha256:' + 'a' * 64
        expected = 'acrtestsample.azurecr.io/portal@' + digest
        revision = 'ca-test-sample-portal--expected'
        def app(image, **ingress):
            return {'properties': {'provisioningState': 'Succeeded',
                'latestRevisionName': revision, 'latestReadyRevisionName': revision,
                'configuration': {'ingress': {'targetPort': 3000, 'external': True, **ingress}},
                'template': {'containers': [{'name': 'portal', 'image': image}]}}}
        def ready(image):
            return {'properties': {'healthState': 'Healthy', 'provisioningState': 'Provisioned',
                'runningState': 'Running', 'template': {'containers': [{'name': 'portal', 'image': image}]}}}
        transport = mock.Mock()
        transport.request.side_effect = [{'statusCode': 200, 'body': body} for body in (
            app('old-healthy-image'), app(expected, allowInsecure=True), app(expected), ready('old-healthy-image'), app(expected), ready(expected))]
        ticks = [0]
        def sleep(seconds):
            ticks[0] += seconds
        result = portal.wait_application(config, transport, expected_image_digest=digest,
                                        timeout=45, sleep=sleep, monotonic=lambda: ticks[0])
        self.assertEqual(result, {'revision': revision, 'expectedImageVerified': True})
        self.assertEqual(ticks[0], 30)
        self.assertEqual(transport.request.call_count, 6)
        self.assertTrue(all(call.args[0] == 'GET' for call in transport.request.call_args_list))

    def test_public_health_uses_explicit_protocol_headers_and_requires_no_store(self):
        for cacheable in (False, True):
            reply = (200, {'status': 'ok', 'service': 'egakumi-portal'},
                     'public, max-age=3600' if cacheable else 'no-store')
            times = iter([0, 0, 2])
            with self.subTest(cacheable=cacheable), mock.patch.object(
                    portal, 'read_public_json', return_value=reply) as read:
                if cacheable:
                    with self.assertRaisesRegex(ValueError, 'public health check'):
                        portal.verify_public('https://portal.example.com', timeout=1,
                            sleep=lambda _: None, monotonic=lambda: next(times))
                else:
                    portal.verify_public('https://portal.example.com', timeout=1,
                        sleep=lambda _: None, monotonic=lambda: next(times))
            read.assert_called_once_with('https://portal.example.com/api/health',
                accept='application/json', user_agent='egakumi-portal-deployment/1.0', timeout=20)

    def test_public_health_rejects_coerced_status_and_nonexact_json(self):
        expected = {'status': 'ok', 'service': 'egakumi-portal'}
        for reply in (('200', expected, 'no-store'), (True, expected, 'no-store'),
                      (200, [], 'no-store'), (200, {'status': True, 'service': 'egakumi-portal'}, 'no-store'),
                      (200, {**expected, 'extra': 'synthetic'}, 'no-store'), (200, expected, True)):
            ticks = iter([0, 0, 2])
            with self.subTest(reply=reply), mock.patch.object(portal, 'read_public_json', return_value=reply):
                with self.assertRaisesRegex(ValueError, 'public health check'):
                    portal.verify_public('https://portal.example.com', timeout=1,
                        sleep=lambda _: None, monotonic=lambda: next(ticks))


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
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(portal.main(['--config', str(workspace.params), '--state-file', str(state_file)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['azureWrites'], 0)
            self.assertFalse(result['azdUpRequired'])
            transport.assert_not_called()
            cli.assert_not_called()
            prepare.assert_not_called()
            saved = json.loads((workspace.state / 'portal-plan.json').read_text(encoding='utf-8'))
            self.assertNotIn('signingSecret', saved['parameters'])


if __name__ == '__main__':
    unittest.main()
