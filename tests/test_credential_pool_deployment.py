"""SQLプールの命名、設定移行と配備の停止条件を確認する。"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'apps/gateway/deploy'))
from deployment.helpers.credential_pools import physical_providers, logical_profiles, resource_names
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration, ConfigurationError
from prepare_credential_pool_config import transform
from broker_deployment import wait_for_schema
from deploy_gateway import foundation_inputs
from configuration_fixtures import fixture
from arm_helpers import ArmExpression


def raw_fixture():
    value = fixture()
    master = json.loads((ROOT / 'params/test.example.json').read_text(encoding='utf-8'))
    value['credentialProviders'] = master['credentialProviders']
    value['gateway']['authentication']['credentialLogin'] = master['gateway']['authentication']['credentialLogin']
    value['certificate']['domainNames'] = master['certificate']['domainNames']
    value['credentialProviders'][0]['clientId'] = '44444444-4444-4444-8444-444444444444'
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

    def test_migration_preserves_application_identity_and_key_header(self):
        raw = raw_fixture()
        raw['credentialProviders'][0]['name'] = 'graph-personal'
        raw['credentialProviders'][0].pop('pool', None)
        raw['gateway']['authentication']['credentialLogin']['providerNames'] = ['graph-personal']
        raw['gateway']['authentication']['subscriptionKeyHeader'] = 'my-api-key'
        before = copy.deepcopy(raw)
        new = transform(raw)
        self.assertEqual(raw, before)
        self.assertEqual(new['gateway']['authentication']['subscriptionKeyHeader'], 'my-api-key')
        self.assertEqual(new['credentialProviders'][0]['clientId'], before['credentialProviders'][0]['clientId'])
        self.assertEqual(new['certificate'], before['certificate'])

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
                id_base = ('/subscriptions/' + config['subscriptionId'] + '/resourceGroups/'
                           + config['resourceGroupNames']['id']
                           + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/')
                foundation = {
                    'resourceGroup': config['resourceGroupNames']['svc'],
                    'identityResourceGroup': config['resourceGroupNames']['id'],
                    'apimName': 'apim-' + stem,
                    'vaultName': 'kv-' + stem,
                    'sqlServer': 'sql-' + stem + '.database.windows.net',
                    'schemaIdentity': {'id': id_base + names['schemaIdentity']},
                    'environmentName': 'cae-' + stem,
                    'environmentDomain': 'environment.example.com',
                    'registryName': 'acr' + stem.replace('-', ''),
                }
                state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
                state['outputs'] = {'gatewayFoundation': foundation}
                original = copy.deepcopy(state)
                inputs = foundation_inputs(config, state)
                self.assertEqual(names['sqlServer'], 'sql-' + stem)
                self.assertEqual(names['database'], 'sqldb-' + stem + '-credentials')
                self.assertEqual(inputs['sqlServer'], foundation['sqlServer'])
                self.assertEqual(inputs['schemaIdentity']['id'], id_base + names['schemaIdentity'])
                self.assertNotIn('database', inputs)
                self.assertNotIn('runtimeIdentity', inputs)
                self.assertEqual(state, original)

    def test_sql_is_private_and_separate_from_runtime_admin(self):
        sql = (ROOT/'infra/modules/shared-sql.bicep').read_text(encoding='utf-8')
        self.assertIn("publicNetworkAccess: 'Disabled'", sql)
        self.assertIn('azureADOnlyAuthentication: true', sql)
        self.assertIn('sid: schemaIdentity.principalId', sql)
        database = (ROOT/'apps/gateway/deploy/database.bicep').read_text(encoding='utf-8')
        self.assertIn('retentionDays: settings.backupRetentionDays', database)
        self.assertNotIn("resource database ", sql)
        app = (ROOT/'apps/gateway/deploy/broker.bicep').read_text(encoding='utf-8')
        self.assertIn('SQL_RUNTIME_CLIENT_ID: runtimeIdentity.clientId', app)
        self.assertIn('AZURE_CLIENT_ID: schemaIdentity.clientId', app)
        self.assertIn('if (deployApplication)', app)
        self.assertIn('BROKER_LOGIN_CALLS_PER_MINUTE: string(loginCallsPerMinute)',app)

    def test_compiled_job_names_match_runtime_and_fit_the_supported_stem_limit(self):
        template = compile_bicep('apps/gateway/deploy/broker.bicep')
        jobs = [item for item in template['resources'] if item['type'].lower() == 'microsoft.app/jobs']
        self.assertEqual(len(jobs), 2)
        for stem in ('dev-egakumi', 'staging-orion', 'stage-' + 'p' * 15):
            config = configured()
            config['resourceStem'] = stem
            names = resource_names(config)
            actual = [ArmExpression(item['name']).evaluate({'stem': stem}, 0) for item in jobs]
            self.assertEqual(set(actual), {names['schemaJob'], names['reconcileJob']})
            for name in actual:
                self.assertLessEqual(len(name), 32)
                self.assertRegex(name, r'^[a-z][a-z0-9-]*[a-z0-9]$')
                self.assertNotIn('--', name)


if __name__ == '__main__': unittest.main()
