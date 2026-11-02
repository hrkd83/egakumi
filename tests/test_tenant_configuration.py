"""Azure管理と入口認証のテナント境界を、架空のIDとオフライン応答で検証する。"""
import copy
import json
import unittest
from schema_helpers import local_schema_validator
from unittest import mock

from configuration_fixtures import CORE, fixture, connection_fixture, pii_fixture
from deployment.helpers import configuration
from deployment import lifecycle

ENTRA = '22222222-2222-4222-8222-222222222222'


def authentication_in(raw, tenant=ENTRA):
    raw = copy.deepcopy(raw)
    raw['entraTenantId'] = tenant
    return raw


class TenantConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / 'params')

    def test_omitted_and_explicit_same_tenant_have_identical_behavior(self):
        raw = fixture()
        raw.pop('entraTenantId')
        original = copy.deepcopy(raw)
        implicit = self.normalize(raw)
        explicit = self.normalize({**raw, 'entraTenantId': raw['azureTenantId']})
        self.assertEqual(implicit, explicit)
        self.assertEqual(implicit['azureTenantId'], implicit['entraTenantId'])
        self.assertNotIn('tenantId', implicit)
        self.assertEqual(raw, original)

    def test_separate_authentication_tenant_keeps_azure_resources_and_role_assignments(self):
        raw = authentication_in(pii_fixture())
        config = self.normalize(raw)
        self.assertNotEqual(config['azureTenantId'], config['entraTenantId'])
        self.assertEqual(config['resourceGroupNames'], self.normalize(pii_fixture())['resourceGroupNames'])
        self.assertEqual(config['authorization'], raw['authorization'])
        self.assertEqual(config['entraTenantId'], ENTRA)
        parameters = lifecycle.parameters(config, 'core')['parameters']['configuration']['value']
        self.assertEqual(parameters['azureTenantId'], raw['azureTenantId'])
        self.assertEqual(parameters['entraTenantId'], ENTRA)
        self.assertNotIn('tenantId', parameters)


    def test_personal_oauth_provider_explicitly_uses_authentication_tenant(self):
        raw = authentication_in(connection_fixture())
        raw['credentialProviders'][0]['oauthTenantId'] = ENTRA
        config = self.normalize(raw)
        self.assertEqual(config['credentialProviders'][0]['oauthTenantId'], ENTRA)
        self.assertEqual(config['azureTenantId'], raw['azureTenantId'])
        self.assertEqual(config['apiDefinitions'][0]['backendAuthentication']['mode'], 'none')

    def test_personal_oauth_provider_defaults_to_authentication_tenant(self):
        raw = authentication_in(connection_fixture())
        raw['credentialProviders'][0].pop('oauthTenantId')
        config = self.normalize(raw)
        self.assertEqual(config['credentialProviders'][0]['oauthTenantId'], ENTRA)
        connection = config['apiDefinitions'][0]['credentialConnection']
        self.assertEqual(connection['mode'], 'personal')
        self.assertNotIn('ownerTenantId', connection)
        self.assertEqual(config['azureTenantId'], raw['azureTenantId'])

    def test_explicit_downstream_oauth_tenant_is_preserved(self):
        raw = authentication_in(connection_fixture())
        configured = raw['credentialProviders'][0]['oauthTenantId']
        self.assertNotEqual(configured, ENTRA)
        self.assertEqual(self.normalize(raw)['credentialProviders'][0]['oauthTenantId'], configured)

    def test_invalid_explicit_authentication_tenant_never_falls_back(self):
        for value in (None, '', False, [], 'invalid'):
            with self.subTest(value=value), self.assertRaisesRegex(configuration.ConfigurationError, 'entraTenantId'):
                self.normalize({**fixture(), 'entraTenantId': value})


    def test_azure_tenant_is_required_even_when_authentication_tenant_is_present(self):
        raw = fixture()
        del raw['azureTenantId']
        raw['entraTenantId'] = ENTRA
        with self.assertRaisesRegex(configuration.ConfigurationError, 'azureTenantId'):
            self.normalize(raw)

    def test_management_transport_uses_azure_tenant_and_only_reads_selected_subscription(self):
        config = self.normalize(authentication_in(pii_fixture()))
        transport = lifecycle.ArmTransport(config)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        response.read.return_value = b'{}'
        auth = {'accessToken': 'OFFLINE_FIXTURE', 'expires_on': 9999999999}
        with mock.patch.object(lifecycle, 'arm_access_token', return_value=auth) as acquire, mock.patch.object(transport.opener, 'open', return_value=response):
            transport.request('GET', f"/subscriptions/{config['subscriptionId']}/resourcegroups?api-version=2022-09-01")
        acquire.assert_called_once_with(config['subscriptionId'], config['azureTenantId'])
        self.assertEqual(transport.tenant, config['azureTenantId'])

    def test_azd_azure_tenant_environment_is_not_the_authentication_tenant(self):
        config = self.normalize(authentication_in(pii_fixture()))
        with mock.patch.dict('os.environ', {'AZURE_TENANT_ID': ENTRA, 'AZURE_ENV_NAME': config['environmentName']}, clear=True), mock.patch.object(lifecycle.shutil, 'which', side_effect=AssertionError('Tool must not start')):
            with self.assertRaisesRegex(lifecycle.DeploymentError, 'AZURE_TENANT_ID'):
                lifecycle.local_preflight(config)

    def test_deployment_state_records_both_tenants_without_changing_cleanup_identity(self):
        config = self.normalize(authentication_in(pii_fixture()))
        state = lifecycle.state_from_outputs(config, {})
        self.assertEqual(state['azureTenantId'], config['azureTenantId'])
        self.assertEqual(state['entraTenantId'], ENTRA)
        self.assertNotIn('tenantId', state)


    def test_both_schemas_require_azure_tenant_and_allow_optional_authentication_tenant(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema is available in the integration venv')
        for name in ('params', 'configuration'):
            with self.subTest(schema=name):
                schema = json.loads((CORE / f'schemas/{name}.schema.json').read_text())
                self.assertIn('azureTenantId', schema['required'])
                self.assertNotIn('entraTenantId', schema['required'])
                self.assertNotIn('tenantId', schema['properties'])
                jsonschema.Draft202012Validator.check_schema(schema)
                sample = json.loads((CORE / 'params/test.example.json').read_text()) if name == 'params' else fixture()
                sample['azureTenantId'] = fixture()['azureTenantId']
                sample['subscriptionId'] = fixture()['subscriptionId']
                self.assertEqual(sample['gateway']['authentication']['subscriptionKeyHeader'], 'api-key')
                for tenant in (None, ENTRA):
                    value = copy.deepcopy(sample)
                    if tenant is not None:
                        value['entraTenantId'] = tenant
                    else:
                        value.pop('entraTenantId', None)
                    local_schema_validator(schema).validate(value)
