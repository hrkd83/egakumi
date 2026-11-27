"""SQLプールの命名、設定移行と配備の停止条件を確認する。"""
from tests.support.bicep_helpers import compile_bicep

import copy
import json
import sys
import unittest
from unittest import mock
from tests import REPOSITORY

ROOT = REPOSITORY
sys.path.insert(0, str(ROOT / 'apps/credential-broker/deploy'))
from deployment.helpers.credential_pools import (
    broker_session_settings, logical_profiles, physical_providers, resource_names, verify_existing_provider_registrations,
)
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration, ConfigurationError, load_configuration_source
from broker_deployment import wait_for_schema
from deployment.deploy_gateway import foundation_inputs
from tests.support.configuration_fixtures import fixture
from tests.support.arm_helpers import ArmExpression
from tests.support.management_fixtures import client_core_outputs
from tests.network.test_parameter_ip_ownership import NetworkArmExpression


def raw_fixture():
    value = fixture()
    master = load_configuration_source(ROOT / 'params/test.example.json')
    value['credentialProviders'] = master['credentialProviders']
    value['gateway']['authentication']['credentialLogin'] = master['gateway']['authentication']['credentialLogin']
    value['gateway']['authentication']['credentialLogin'].pop('identity', None)
    value['credentialProviders'][0]['clientId'] = '44444444-4444-4444-8444-444444444444'
    value['credentialProviders'][0]['oauthTenantId'] = value['entraTenantId']
    return value


def configured():
    return normalize_configuration(raw_fixture())


class CredentialPoolDeploymentTests(unittest.TestCase):
    def test_pool_expansion_is_stable_and_does_not_mutate_input(self):
        config = configured()
        original = copy.deepcopy(config)
        providers = physical_providers(config)
        self.assertEqual([p['name'] for p in providers], ['graph-delegated-' + c for c in 'abcdefghijkl'])
        config['credentialProviders'][0]['pool']['count'] = 13
        self.assertEqual(physical_providers(config)[:12], providers)
        self.assertEqual(original['credentialProviders'][0]['name'], 'graph-delegated')

    def test_public_broker_profiles_use_secret_environment_names(self):
        config = configured()
        public = build_bicep_configuration(config)
        self.assertNotIn('clientSecretFile', json.dumps(public))
        profile = logical_profiles(config)['graph-delegated']
        self.assertEqual(profile['clientSecretEnv'], 'OIDC_SECRET_0')
        self.assertEqual(profile['pools'][0], {'name':'graph-delegated-a', 'capacity':8000})

    def test_provider_preflight_allows_absence_and_prevents_in_place_registration_replacement(self):
        config = configured()
        config['credentialProviders'][0]['pool']['count'] = 2
        apim = '/subscriptions/synthetic/resourceGroups/rg-fixture/providers/Microsoft.ApiManagement/service/apim-fixture'
        first = apim + '/authorizationProviders/graph-delegated-a'
        second = apim + '/authorizationProviders/graph-delegated-b'
        existing = {'statusCode': 200, 'body': {'id': first.upper(), 'properties': {
            'identityProvider': 'aad', 'oauth2': {'grantTypes': {'authorizationCode': {
                'clientId': config['credentialProviders'][0]['clientId'], 'tenantId': config['entraTenantId'],
            }}},
        }}}
        transport = mock.Mock()
        transport.request.side_effect = [existing, {'statusCode': 404}]
        verify_existing_provider_registrations(config, apim, transport)
        self.assertEqual(transport.request.call_args_list, [
            mock.call('GET', path + '?api-version=2024-05-01') for path in (first, second)
        ])

        existing['body']['properties']['oauth2']['grantTypes']['authorizationCode']['clientId'] = 'SYNTHETIC-CHANGED'
        for response, error in ((existing, 'different registration'), ({'statusCode': 403}, 'could not be verified')):
            with self.subTest(error=error):
                transport.reset_mock()
                transport.request.side_effect = [response]
                with self.assertRaisesRegex(ValueError, error) as raised:
                    verify_existing_provider_registrations(config, apim, transport)
                transport.request.assert_called_once_with('GET', first + '?api-version=2024-05-01')
                self.assertNotIn('SYNTHETIC-CHANGED', str(raised.exception))

    def test_empty_business_login_keeps_internal_broker_defaults_without_inventing_authentication(self):
        raw = raw_fixture()
        raw['gateway']['authentication'].pop('credentialLogin')
        raw['credentialProviders'] = []
        before = copy.deepcopy(raw)
        config = normalize_configuration(raw)
        public = build_bicep_configuration(config)
        self.assertEqual(raw, before)
        self.assertNotIn('credentialLogin', public['gateway']['authentication'])
        self.assertEqual(public['credentialProfiles'], {})
        self.assertEqual(public['credentialPhysicalProviders'], [])
        self.assertEqual(public['credentialBrokerSession'], broker_session_settings(config))
        self.assertGreater(public['credentialBroker']['minReplicas'], 0)

    def test_compiled_broker_resource_graph_is_always_present_and_keeps_sql_phase_gate(self):
        gateway = compile_bicep('apps/api-gateway/deploy/main.bicep')
        for suffix in ('identity', 'database', 'access', 'broker'):
            with self.subTest(module=suffix):
                module, = [item for item in gateway['resources']
                           if '-gateway-' + suffix + "'" in item['name']]
                self.assertNotIn('condition', module)
        settings, = [item for item in gateway['resources']
                     if 'credentialBrokerUrl' in item.get('properties', {}).get('parameters', {})]
        self.assertEqual(settings['condition'], '[parameters(\'deployApplication\')]')
        broker = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        app, = [item for item in broker['resources'] if item['type'].lower() == 'microsoft.app/containerapps']
        self.assertEqual(app['condition'], '[parameters(\'deployApplication\')]')
        migration, = [item for item in broker['resources'] if item['type'].lower() == 'microsoft.app/jobs'
                      and item['properties']['configuration']['triggerType'] == 'Manual']
        self.assertNotIn('condition', migration)
        reconciliation, = [item for item in broker['resources'] if item['type'].lower() == 'microsoft.app/jobs'
                           and item['properties']['configuration']['triggerType'] == 'Schedule']
        self.assertEqual(reconciliation['condition'], '[parameters(\'deployApplication\')]')
        apim = compile_bicep('apps/api-gateway/deploy/apim-settings.bicep')
        for resource in apim['resources']:
            if 'credential-broker' in resource['name']:
                self.assertNotIn('condition', resource)
        for parameter in ('credentialBrokerUrl', 'credentialBrokerSecretIdentifier'):
            self.assertNotIn('defaultValue', apim['parameters'][parameter])

    def test_pool_limit_and_physical_name_collision_are_rejected(self):
        raw = raw_fixture()
        raw['credentialProviders'][0]['pool']['capacity'] = 10001
        with self.assertRaises(ConfigurationError): normalize_configuration(raw)
        raw = raw_fixture()
        duplicate = copy.deepcopy(raw['credentialProviders'][0])
        duplicate.pop('pool'); duplicate['name']='graph-delegated-a'; duplicate['connectionNames']=['fixed']
        raw['credentialProviders'].append(duplicate)
        with self.assertRaises(ConfigurationError): normalize_configuration(raw)

    def test_failed_schema_job_stops_before_any_application_deployment(self):
        config = configured()
        class Failed:
            def request(self, *args):
                return {'statusCode': 200, 'body': {'name':'execution', 'properties':{'status':'Failed'}}}
        with self.assertRaisesRegex(ValueError, 'not updated'):
            wait_for_schema(config, 'execution', Failed(), sleep=lambda _: None)

    def test_foreign_foundation_state_is_rejected(self):
        config = configured()
        with self.assertRaisesRegex(ValueError, 'differs'):
            foundation_inputs(config, {})

    def test_sql_deployment_outputs_reach_runtime_with_variable_environment_names(self):
        for stem in ('dev-egakumi', 'staging-orion'):
            with self.subTest(stem=stem):
                config = configured()
                config['resourceStem'] = stem
                config['resourceGroupNames'] = {layer: 'rg-' + stem + '-' + layer
                                                for layer in config['resourceGroupNames']}
                names = resource_names(config)
                groups = config['resourceGroupNames']
                group_base = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/'
                id_base = group_base + groups['id'] + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/'
                broker_environment = 'cae-' + stem + '-broker'
                broker_base = group_base + groups['svc'] + '/providers/Microsoft.App/managedEnvironments/'
                subnet_base = group_base + groups['nw'] + '/providers/Microsoft.Network/virtualNetworks/'
                foundation = {
                    'resourceGroup': config['resourceGroupNames']['svc'],
                    'identityResourceGroup': config['resourceGroupNames']['id'],
                    'apimName': apim_name(config),
                    'vaultName': 'kv-' + stem,
                    'sqlServer': 'sql-' + stem + '.database.windows.net',
                    'schemaIdentity': {'id': id_base + names['schemaIdentity']},
                    'environmentName': 'cae-' + stem,
                    'environmentDomain': 'environment.example.com',
                    'brokerEnvironmentName': broker_environment,
                    'brokerEnvironmentId': broker_base + broker_environment,
                    'brokerEnvironmentDomain': 'synthetic-broker.fixture.' + 'azurecontainerapps.io',
                    'brokerSubnetId': subnet_base + 'vnet-' + stem + '/subnets/snet-' + stem + '-broker',
                    'registryName': 'acr' + stem.replace('-', ''),
                }
                state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
                state['apimInstanceId'] = apim_instance_id(config)
                host = config['gateway']['publicHostName']
                state['outputs'] = {'gatewayFoundation': foundation,
                    **client_core_outputs(config),
                    'ingressSecrets': {'enabled': True, 'l7IngressEnabled': True, 'publicHostName': host}}
                original = copy.deepcopy(state)
                inputs = foundation_inputs(config, state)
                self.assertEqual(names['sqlServer'], 'sql-' + stem)
                self.assertEqual(names['database'], 'sqldb-' + stem + '-credentials')
                self.assertEqual(inputs['sqlServer'], foundation['sqlServer'])
                self.assertEqual(inputs['schemaIdentity']['id'], id_base + names['schemaIdentity'])
                self.assertEqual(inputs['brokerEnvironmentId'], broker_base + broker_environment)
                self.assertEqual(inputs['brokerEnvironmentDomain'], foundation['brokerEnvironmentDomain'])
                self.assertNotIn('database', inputs)
                self.assertNotIn('runtimeIdentity', inputs)
                self.assertEqual(state, original)

    def test_sql_is_private_and_separate_from_runtime_admin(self):
        sql = (ROOT/'infra/modules/services/shared-sql.bicep').read_text(encoding='utf-8')
        self.assertIn("publicNetworkAccess: 'Disabled'", sql)
        self.assertIn('azureADOnlyAuthentication: true', sql)
        self.assertIn('sid: schemaIdentity.principalId', sql)
        database = (ROOT/'apps/credential-broker/deploy/database.bicep').read_text(encoding='utf-8')
        self.assertIn('retentionDays: settings.backupRetentionDays', database)
        self.assertNotIn("resource database ", sql)
        app = (ROOT/'apps/credential-broker/deploy/broker.bicep').read_text(encoding='utf-8')
        self.assertIn('SQL_RUNTIME_CLIENT_ID: runtimeIdentity.clientId', app)
        self.assertIn('AZURE_CLIENT_ID: schemaIdentity.clientId', app)
        self.assertIn('if (deployApplication)', app)
        self.assertIn('BROKER_LOGIN_CALLS_PER_MINUTE: string(loginCallsPerMinute)',app)

    def test_compiled_job_names_match_runtime_and_fit_the_supported_stem_limit(self):
        template = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        jobs = [item for item in template['resources'] if item['type'].lower() == 'microsoft.app/jobs']
        self.assertEqual(len(jobs), 2)
        scheduled = [item for item in jobs if item['properties']['configuration']['triggerType'] == 'Schedule']
        self.assertEqual(len(scheduled), 1)
        schedule = scheduled[0]['properties']['configuration']['scheduleTriggerConfig']
        self.assertEqual(schedule, {'cronExpression': '17 * * * *', 'replicaCompletionCount': 1, 'parallelism': 1})
        container, = scheduled[0]['properties']['template']['containers']
        self.assertEqual(container['command'], ['python', '-m', 'credential_broker.cli'])
        self.assertEqual(container['args'], ['reconcile', '--execute'])
        for stem in ('dev-egakumi', 'staging-orion', 'stage-' + 'p' * 11):
            config = configured()
            config['resourceStem'] = stem
            names = resource_names(config)
            actual = [ArmExpression(item['name']).evaluate({'stem': stem}, 0) for item in jobs]
            self.assertEqual(set(actual), {names['schemaJob'], names['reconcileJob']})
            for name in actual:
                self.assertLessEqual(len(name), 32)
                self.assertRegex(name, r'^[a-z][a-z0-9-]*[a-z0-9]$')
                self.assertNotIn('--', name)

    def test_broker_probes_require_verified_user_ledger_capability(self):
        template = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        app, = [item for item in template['resources']
                if item['type'].lower() == 'microsoft.app/containerapps']
        container, = app['properties']['template']['containers']
        probe, = [item for item in container['copy']
                  if item['name'] == 'probes']
        self.assertEqual(probe['input']['httpGet'], {
            'path': '/health/gateway-caller-v1', 'port': 8000,
            'scheme': 'HTTP'})

    def test_broker_application_and_apim_url_match_with_the_longest_supported_stem(self):
        """有効な最大長入力を短縮せず、ACA作成・出力・参照先・Pythonの名前を照合する。"""
        broker = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        gateway = compile_bicep('apps/api-gateway/deploy/main.bicep')
        app, = [item for item in broker['resources'] if item['type'].lower() == 'microsoft.app/containerapps']
        deployment, = [item for item in gateway['resources'] if "-gateway-broker'" in item['name']]
        settings, = [item for item in gateway['resources']
                     if 'credentialBrokerUrl' in item.get('properties', {}).get('parameters', {})]
        for product, environment in (('sample', 'dev'), ('p' * 12, 'test')):
            with self.subTest(product=product, environment=environment):
                raw = raw_fixture()
                raw['productName'], raw['environmentName'] = product, environment
                config = normalize_configuration(raw)
                stem = config['resourceStem']
                expected = 'ca-' + stem + '-credentials'
                parameters = {'stem': stem}
                self.assertEqual(ArmExpression(app['name']).evaluate(parameters, 0), expected)
                self.assertEqual(ArmExpression(broker['outputs']['applicationName']['value']).evaluate(parameters, 0), expected)
                self.assertEqual(resource_names(config)['application'], expected)
                self.assertLessEqual(len(expected), 32)
                self.assertRegex(expected, r'^[a-z][a-z0-9-]*[a-z0-9]$')
                self.assertNotIn('--', expected)
                domain = 'broker-environment.example.invalid'
                environment_id = '/mock/Microsoft.App/managedEnvironments/cae-' + stem + '-broker'
                parameters['__references'] = {environment_id: {'defaultDomain': domain}}
                broker_expression = NetworkArmExpression('[true()]', broker)
                outputs = {key: {'value': broker_expression.resolve(broker['outputs'][key]['value'], parameters)}
                           for key in ('applicationName', 'environmentDomain', 'environmentId')}
                self.assertEqual(outputs['environmentId']['value'], environment_id)
                self.assertEqual(broker_expression.resolve(app['properties']['managedEnvironmentId'], parameters),
                                 environment_id)
                gateway_expression = NetworkArmExpression('[true()]', gateway)
                gateway_parameters = {'configuration': {'resourceStem': stem}}
                deployment_name = gateway_expression.resolve(deployment['name'], gateway_parameters)
                deployment_id = '/mock/Microsoft.Resources/deployments/' + deployment_name
                gateway_parameters['__references'] = {deployment_id: {'outputs': outputs}}
                url_expression = settings['properties']['parameters']['credentialBrokerUrl']['value']
                url = gateway_expression.resolve(url_expression, gateway_parameters)
                self.assertEqual(url, 'https://' + expected + '.' + domain)
                self.assertEqual(gateway_expression.resolve(gateway['outputs']['credentialBroker']['value']['url'],
                                                            gateway_parameters), url)
                if product == 'p' * 12:
                    self.assertEqual(len(stem), 17)
                    self.assertEqual(len(expected), 32)


if __name__ == '__main__': unittest.main()
