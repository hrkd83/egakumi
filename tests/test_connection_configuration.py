"""本人セッション用CMと固定サービス接続の設定境界を検証する。"""
import copy
import unittest
from configuration_fixtures import configuration, connection_fixture, CORE

class ConnectionOwnerConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / 'config')

    def test_personal_connection_is_selected_by_verified_session(self):
        raw = connection_fixture()
        actual = self.normalize(raw)
        connection = actual['apiDefinitions'][0]['credentialConnection']
        self.assertEqual(connection, {'mode': 'personal', 'providerName': 'approved-oauth', 'enabled': True})
        self.assertEqual(actual['credentialProviders'][0]['connectionNames'], [])
        login = actual['gateway']['authentication']['credentialLogin']
        self.assertEqual(login['idleTimeoutSeconds'], 604800)
        self.assertEqual(login['absoluteLifetimeSeconds'], 2592000)
        self.assertEqual(login['rotationIntervalSeconds'], 86400)
        self.assertEqual(login['rotationGraceSeconds'], 300)
        self.assertNotIn('sessionLifetimeSeconds', login)
        self.assertEqual(actual['apiDefinitions'][0]['authorization']['mode'], 'subscriptionKey')

    def test_personal_connection_requires_registered_login_provider(self):
        raw = connection_fixture()
        raw['gateway']['authentication'].pop('credentialLogin')
        raw['credentialProviders'][0]['connectionNames'] = ['service-fixed']
        with self.assertRaisesRegex(configuration.ConfigurationError, 'personal connections require'):
            self.normalize(raw)

    def test_personal_connection_rejects_caller_selected_fixed_owner(self):
        for field, value in [('ownerTenantId', '55555555-5555-4555-8555-555555555555'),
                             ('ownerPrincipalId', '55555555-5555-4555-8555-555555555555'),
                             ('connectionName', 'someone-else')]:
            raw = connection_fixture()
            raw['apiDefinitions'][0]['credentialConnection'][field] = value
            with self.subTest(field=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_service_connection_requires_registered_fixed_connection(self):
        raw = connection_fixture()
        raw['gateway']['authentication'].pop('credentialLogin')
        raw['credentialProviders'][0]['connectionNames'] = ['service-fixed']
        raw['apiDefinitions'][0]['credentialConnection'].update(mode='service', connectionName='service-fixed')
        self.assertEqual(self.normalize(raw)['apiDefinitions'][0]['credentialConnection']['connectionName'], 'service-fixed')
        for value in (None, 'other-service'):
            broken = copy.deepcopy(raw)
            if value is None:
                broken['apiDefinitions'][0]['credentialConnection'].pop('connectionName')
            else:
                broken['apiDefinitions'][0]['credentialConnection']['connectionName'] = value
            with self.subTest(value=value), self.assertRaises(configuration.ConfigurationError):
                self.normalize(broken)

    def test_personal_identity_endpoint_is_fixed_to_provider_resource_host(self):
        for endpoint in ('http://graph.microsoft.com/v1.0/me', 'https://other.example/me',
                         'https://graph.microsoft.com/v1.0/me?user=other',
                         'https://name:secret@graph.microsoft.com/v1.0/me'):
            raw = connection_fixture()
            raw['credentialProviders'][0]['identityEndpoint'] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_personal_login_lifetimes_and_identity_property_are_validated(self):
        for field, value in [('idleTimeoutSeconds', 299), ('absoluteLifetimeSeconds', 299),
                             ('rotationIntervalSeconds', 299), ('rotationGraceSeconds', 0),
                             ('rotationHandoffSeconds', 59), ('rotationCandidateTtlSeconds', 59),
                             ('sessionLifetimeSeconds', 3600), ('transactionLifetimeSeconds', 601),
                             ('loginCallsPerMinute', 0), ('providerNames', ['approved-oauth', 'approved-oauth'])]:
            raw = connection_fixture()
            raw['gateway']['authentication']['credentialLogin'][field] = value
            with self.subTest(field=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)
        raw = connection_fixture()
        raw['credentialProviders'][0]['identityIdProperty'] = 'user.id'
        with self.assertRaisesRegex(configuration.ConfigurationError, 'identityIdProperty'):
            self.normalize(raw)

    def test_session_lifecycle_settings_reject_inconsistent_boundaries(self):
        for fields in ({'idleTimeoutSeconds': 2592001}, {'rotationIntervalSeconds': 2592000},
                       {'rotationIntervalSeconds': 300, 'rotationGraceSeconds': 300},
                       {'rotationCandidateTtlSeconds': 601, 'rotationHandoffSeconds': 600}):
            raw = connection_fixture()
            raw['gateway']['authentication']['credentialLogin'].update(fields)
            with self.subTest(fields=fields), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)
