"""Foundation routing uses API contract metadata before publication backends exist."""
import copy
import json
import unittest
from unittest import mock

from arm_helpers import DiscoveryExpression
from bicep_helpers import compile_bicep
from configuration_fixtures import CORE, configuration, fixture
from deployment.helpers.diagnostics import canonical_settings
import standard_apis


def foundation_configuration():
    return configuration.normalize_configuration(fixture(), base_directory=CORE / 'config')


class ApiFoundationProjectionTests(unittest.TestCase):
    def test_empty_public_array_projects_catalog_without_backend_or_secret_resolution(self):
        config = foundation_configuration()
        before = copy.deepcopy(config)
        with mock.patch.object(standard_apis, '_bindings', side_effect=AssertionError('No deployed endpoints exist yet')), \
             mock.patch.object(configuration, '_read_protected_file', side_effect=AssertionError('No secrets are needed')):
            built = configuration.build_bicep_configuration(config)
        self.assertEqual(built['apiDefinitions'], [])
        expected = standard_apis._definitions()
        self.assertEqual([api['path'] for api in built['infrastructureApiDefinitions']], [api['path'] for api in expected])
        self.assertEqual(config, before)
        for api in built['infrastructureApiDefinitions']:
            self.assertEqual(set(api) - {'mcp'}, {'name', 'apiType', 'path', 'operations', 'authorization'})
            if 'mcp' in api:
                self.assertEqual(set(api['mcp']), {'name', 'path'})
            for operation in api['operations']:
                self.assertEqual(set(operation), {'name', 'method', 'urlTemplate'})
            for constraint in api['authorization']['operationConstraints'].values():
                self.assertTrue(set(constraint) <= {'kind', 'bodyFormat', 'contentTypes', 'profiles', 'allowedModels', 'maxRequestBytes'})
                for profile in constraint.get('profiles', []):
                    self.assertEqual(set(profile), {'modalities'})
        self.assertNotIn('__OPENAI_RESOURCE_BASE_URL__', json.dumps(built['infrastructureApiDefinitions']))

    def test_projection_keeps_explicit_custom_media_profiles_without_aliasing_inputs(self):
        api = {'apiType': 'http', 'path': 'company/models/v2',
               'backendUrl': 'https://private-backend.example', 'backendAuthentication': {'mode': 'managedIdentity'},
               'operations': [{'name': 'chat', 'method': 'POST', 'urlTemplate': '/chat/completions', 'displayName': 'Chat'}],
               'authorization': {'mode': 'subscriptionKey', 'operationConstraints': {
                   'chat': {'kind': 'bodyModel', 'bodyFormat': 'json', 'allowedModels': ['private-deployment-name'],
                            'profiles': [{'profileId': 'image', 'modalities': ['text', 'image']},
                                         {'profileId': 'audio', 'modalities': ['text', 'audio']}]}}}}
        before = copy.deepcopy(api)
        projected, = standard_apis.infrastructure_api_definitions([api])
        self.assertEqual(projected['path'], api['path'])
        self.assertEqual(projected['authorization']['operationConstraints']['chat']['profiles'],
                         [{'modalities': ['text', 'image']}, {'modalities': ['text', 'audio']}])
        self.assertNotIn('backendUrl', projected)
        self.assertNotIn('backendAuthentication', projected)
        self.assertEqual(projected['authorization']['operationConstraints']['chat']['allowedModels'], ['private-deployment-name'])
        projected['authorization']['operationConstraints']['chat']['allowedModels'].append('another-public-deployment')
        projected['authorization']['operationConstraints']['chat']['profiles'][0]['modalities'].append('file')
        self.assertEqual(api, before)

    def test_explicit_mcp_configuration_keeps_only_its_declared_routes(self):
        raw = fixture()
        raw['apiDefinitions'] = [{
            'name': 'company-tools', 'displayName': 'Company tools', 'apiType': 'mcp', 'path': 'tools/company/v2',
            'backendService': 'external', 'backendUrl': 'https://tools.example.com', 'backendAudience': 'https://tools.example.com',
            'backendAuthentication': {'mode': 'managedIdentity'}, 'productId': 'company-users',
            'subscriptionKeyHeader': 'api-key', 'keySubscriptions': [],
            'operations': [{'name': 'invoke', 'displayName': 'Invoke', 'method': 'POST', 'urlTemplate': '/', 'templateParameters': []}],
            'authorization': {'mode': 'subscriptionKey', 'operationConstraints': {
                'invoke': {'kind': 'registeredOperation', 'bodyFormat': 'none', 'maxRequestBytes': 65536}}},
            'mcpProxy': {'transport': 'sse', 'endpoints': [{'name': 'sse', 'uriTemplate': '/events'},
                                                       {'name': 'message', 'uriTemplate': '/messages/'}]},
        }]
        normalized = configuration.normalize_configuration(raw, base_directory=CORE / 'config')
        built = configuration.build_bicep_configuration(normalized)
        projected, = built['infrastructureApiDefinitions']
        self.assertEqual(projected['mcpProxy'], raw['apiDefinitions'][0]['mcpProxy'])
        self.assertEqual(projected['path'], 'tools/company/v2')
        self.assertEqual(len(built['apiDefinitions']), 1)
        self.assertNotIn('backendUrl', projected)


class ApiFoundationBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = compile_bicep('infra/main.bicep')
        cls.gateway = next(item for item in cls.main['resources']
                           if 'publishedApiPaths' in item.get('properties', {}).get('parameters', {}))
        cls.services = next(item for item in cls.main['resources']
                            if 'modelDeployments' in item.get('properties', {}).get('parameters', {}))

    def value(self, expression, config):
        return DiscoveryExpression(expression, self.main['variables']).evaluate({'configuration': config})

    def paths(self, name, config):
        return self.value(self.gateway['properties']['parameters'][name]['value'], config)

    def apim_logs(self, config):
        # The CLI now generates the canonical properties; Bicep passes that
        # object to the same APIM diagnostic instead of a WebSocket-only flag.
        self.assertEqual(self.services['properties']['parameters']['diagnosticSettingProperties']['value'],
                         "[parameters('diagnosticSettingProperties')]")
        diagnostic = next(item for item in self.services['properties']['template']['resources']
                          if item.get('type') == 'Microsoft.Insights/diagnosticSettings'
                          and item.get('name') == 'core-audit' and "names').apim" in item.get('scope', ''))
        self.assertEqual(diagnostic['properties'], "[parameters('diagnosticSettingProperties')['apim-logs']]")
        return canonical_settings(config, 'core')['apim-logs']['properties']['logs']

    def test_standard_paths_and_websocket_logs_exist_while_public_array_stays_empty(self):
        config = configuration.build_bicep_configuration(foundation_configuration())
        self.assertEqual(config['apiDefinitions'], [])
        self.assertEqual(self.paths('speechRawBodyPaths', config), [
            '/models/azure-speech/v1/synthesize', '/models/azure-speech/v1/recognize'])
        self.assertEqual(self.paths('documentUploadPaths', config), ['/tools/documents/v1/analyze'])
        published = self.paths('publishedApiPaths', config)
        for path in ('/models/openai/v1/chat/completions', '/models/openai/v1/responses',
                     '/tools/pii/v1/redact', '/tools/pii-v1-mcp/mcp'):
            self.assertIn(path, published)
        self.assertTrue(all('{' not in path and '*' not in path for path in published))
        self.assertTrue(self.value(self.main['variables']['realtimeEnabled'], config))
        self.assertIn({'category': 'WebSocketConnectionLogs', 'enabled': True}, self.apim_logs(config))

    def test_custom_media_routes_are_exact_and_do_not_inherit_standard_paths(self):
        config = configuration.build_bicep_configuration(foundation_configuration())
        config['infrastructureApiDefinitions'] = standard_apis.infrastructure_api_definitions([{
            'path': 'company/models/v2', 'operations': [{'name': 'chat', 'method': 'POST', 'urlTemplate': '/chat/completions'}],
            'authorization': {'operationConstraints': {'chat': {'kind': 'bodyModel', 'bodyFormat': 'json',
                'profiles': [{'modalities': ['text', 'image']}, {'modalities': ['text', 'audio']}]}}},
        }])
        self.assertEqual(self.paths('publishedApiPaths', config), ['/company/models/v2/chat/completions'])
        for name in ('speechRawBodyPaths', 'documentUploadPaths'):
            self.assertEqual(self.paths(name, config), [])
        self.assertNotIn({'category': 'WebSocketConnectionLogs', 'enabled': True}, self.apim_logs(config))

    def test_existing_explicit_bicep_inputs_fall_back_without_internal_metadata(self):
        config = configuration.build_bicep_configuration(foundation_configuration())
        config['gateway']['realtimeControlEnabled'] = False
        config['apiDefinitions'] = [{
            'apiType': 'mcp', 'path': 'tools/company/v2',
            'operations': [{'name': 'invoke', 'method': 'POST', 'urlTemplate': '/'}],
            'authorization': {'operationConstraints': {'invoke': {'kind': 'registeredOperation', 'bodyFormat': 'none'}}},
            'mcpProxy': {'transport': 'sse', 'endpoints': [{'name': 'sse', 'uriTemplate': '/events'},
                                                       {'name': 'message', 'uriTemplate': '/messages/'}]},
        }]
        config.pop('infrastructureApiDefinitions')
        self.assertEqual(self.paths('publishedApiPaths', config), ['/tools/company/v2/'])
        self.assertEqual(self.paths('speechRawBodyPaths', config), [])
        self.assertEqual(self.paths('documentUploadPaths', config), [])
        self.assertEqual(config['apiDefinitions'][0]['mcpProxy']['endpoints'][1]['uriTemplate'], '/messages/')
        self.assertFalse(self.value(self.main['variables']['realtimeEnabled'], config))

    def test_only_declared_data_and_raw_inputs_are_wired_to_gateway(self):
        removed = {'customMimePaths', 'legacyMcpMessagePaths', 'inlineImagePaths', 'inlinePdfPaths', 'inlineAudioPaths'}
        parameters = self.gateway['properties']['parameters']
        self.assertTrue({'publishedApiPaths', 'speechRawBodyPaths', 'documentUploadPaths'} <= set(parameters))
        self.assertFalse(removed & set(parameters))
        self.assertFalse(removed & set(self.gateway['properties']['template']['parameters']))
        self.assertFalse(removed & set(self.main['variables']))

    def test_metadata_does_not_enable_realtime_without_the_signing_key_configuration(self):
        config = configuration.build_bicep_configuration(foundation_configuration())
        config['gateway']['realtimeControlEnabled'] = False
        self.assertEqual(config['apiDefinitions'], [])
        self.assertTrue(config['infrastructureApiDefinitions'])
        self.assertFalse(self.value(self.main['variables']['realtimeEnabled'], config))


if __name__ == '__main__':
    unittest.main()
