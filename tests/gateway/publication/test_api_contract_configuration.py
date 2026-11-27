"""Operation defaults and model overrides preserve authorization and backend boundaries."""
import copy
import unittest
from tests import REPOSITORY

CORE = REPOSITORY
from deployment.helpers.configuration import _apis, normalize_configuration, ConfigurationError

TENANT = '11111111-1111-4111-8111-111111111111'
AUDIENCE = '22222222-2222-4222-8222-222222222222'

def contract():
    constraint = {'kind':'bodyModel','allowedModels':['text-model','vision-model'],'bodyFormat':'json',
        'maxRequestBytes':32768,'maxOutputTokens':128,'outputTokenParameter':'max_tokens'}
    settings = {'text-model': {'settingId': 'chat'},
        'vision-model': {'settingId': 'vision', 'maxRequestBytes': 262144}}
    return {'name':'openai-inference-v1','displayName':'Inference','serviceClass':'platform',
        'apiType':'http','path':'models/openai/v1','backendService':'foundryOpenAi',
        'backendAudience':'https://cognitiveservices.azure.com',
        'productId':'model-users','subscriptionKeyHeader':'api-key',
        'operations':[{'name':'chat-completions','displayName':'Chat','method':'POST',
            'urlTemplate':'/chat/completions','templateParameters':[]}],
        'authorization':{'mode':'subscriptionKey',
            'operationConstraints':{'chat-completions':dict(constraint,modelSettings=settings)}}}

class ApiContractConfigurationTests(unittest.TestCase):
    def normalize(self, api):
        return _apis([api],TENANT,{'text-model','vision-model'},'https://gateway.example.com',
            {'subscriptionKeyHeader':'api-key'})[0]

    def test_request_body_size_check_preserves_explicit_modes_and_existing_default(self):
        base = self.normalize(contract())
        self.assertNotIn('requestBodySizeCheck', base['authorization']['operationConstraints']['chat-completions'])
        actual = contract()
        actual['authorization']['operationConstraints']['chat-completions']['requestBodySizeCheck'] = 'actual'
        self.assertEqual(self.normalize(actual)['authorization']['operationConstraints']['chat-completions'],
                         {**base['authorization']['operationConstraints']['chat-completions'], 'requestBodySizeCheck': 'actual'})
        for body_format, media in (('binary', 'audio/wav'), ('multipart', 'multipart/form-data'), ('text', 'text/plain')):
            for mode in ('actual', 'declared'):
                api = contract()
                api['authorization']['operationConstraints']['chat-completions'] = {
                    'kind': 'registeredOperation', 'bodyFormat': body_format, 'contentTypes': [media],
                    'maxRequestBytes': 32768, 'requestBodySizeCheck': mode}
                with self.subTest(format=body_format, mode=mode):
                    normalized = self.normalize(api)
                    self.assertEqual(normalized['authorization']['operationConstraints']['chat-completions']['requestBodySizeCheck'], mode)
                    self.assertEqual(self.normalize(normalized), normalized)

    def test_request_body_size_check_rejects_invalid_values_formats_and_model_scope(self):
        for value in ('', 'buffered', None, False, 1, [], {}):
            api = contract()
            api['authorization']['operationConstraints']['chat-completions']['requestBodySizeCheck'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ConfigurationError, 'requestBodySizeCheck'):
                self.normalize(api)
        for body_format in ('json', 'none', None):
            api = contract()
            constraint = {'kind': 'registeredOperation', 'maxRequestBytes': 32768, 'requestBodySizeCheck': 'declared'}
            if body_format is not None:
                constraint['bodyFormat'] = body_format
            api['authorization']['operationConstraints']['chat-completions'] = constraint
            with self.subTest(format=body_format), self.assertRaisesRegex(ConfigurationError, 'requestBodySizeCheck'):
                self.normalize(api)
        for mode in ('actual', 'declared'):
            api = contract()
            api['authorization']['operationConstraints']['chat-completions']['modelSettings']['text-model']['requestBodySizeCheck'] = mode
            with self.subTest(model_mode=mode), self.assertRaisesRegex(ConfigurationError, 'requestBodySizeCheck'):
                self.normalize(api)

    def test_editor_schemas_restrict_declared_body_size_check_to_explicit_upload_formats(self):
        from tests.support.schema_helpers import local_schema_validator, read_schema
        for name in ('configuration', 'params'):
            schema = read_schema(name)
            validator = local_schema_validator({'$schema': schema['$schema'], '$ref': '#/$defs/operationConstraint', '$defs': schema['$defs']})
            base = {'kind': 'registeredOperation', 'maxRequestBytes': 32768}
            for body_format in ('json', 'binary', 'multipart', 'text', 'none'):
                for mode in ('actual', 'declared'):
                    value = {**base, 'bodyFormat': body_format, 'requestBodySizeCheck': mode}
                    with self.subTest(schema=name, format=body_format, mode=mode):
                        self.assertEqual(not list(validator.iter_errors(value)), mode == 'actual' or body_format in ('binary', 'multipart', 'text'))
            for value in ('', 'buffered', None, False, 1, [], {}):
                with self.subTest(schema=name, value=value):
                    self.assertTrue(list(validator.iter_errors({**base, 'bodyFormat': 'binary', 'requestBodySizeCheck': value})))
            with self.subTest(schema=name, missing_format=True):
                self.assertTrue(list(validator.iter_errors({**base, 'requestBodySizeCheck': 'declared'})))
            with self.subTest(schema=name, existing_default=True):
                self.assertEqual(list(validator.iter_errors(contract()['authorization']['operationConstraints']['chat-completions'])), [])
            nested = contract()['authorization']['operationConstraints']['chat-completions']
            nested['modelSettings']['text-model']['requestBodySizeCheck'] = 'actual'
            with self.subTest(schema=name, model_scope=True):
                self.assertTrue(list(validator.iter_errors(nested)))

    def test_operation_model_alias_targets_existing_allowed_deployment(self):
        api = contract()
        api['authorization']['operationConstraints']['chat-completions']['modelAliases'] = {'synthetic-review': 'text-model'}
        result = self.normalize(api)['authorization']['operationConstraints']['chat-completions']
        self.assertEqual(result['modelAliases'], {'synthetic-review': 'text-model'})
        self.assertEqual(result['allowedModels'], ['text-model', 'vision-model'])
        self.assertNotIn('modelAliases', result['modelSettings']['text-model'])

    def test_model_alias_rejects_shadowing_chains_unallowed_targets_and_model_scope(self):
        for aliases in ({}, [], {'bad*': 'text-model'}, {'text-model': 'text-model'}, {'vision-model': 'text-model'},
                {'synthetic-review': 'unregistered'}, {'synthetic-review': 1},
                {'first': 'second', 'second': 'text-model'}, {'self': 'self'}):
            api = contract()
            api['authorization']['operationConstraints']['chat-completions']['modelAliases'] = aliases
            with self.subTest(aliases=aliases), self.assertRaises(ConfigurationError):
                self.normalize(api)
        api = contract()
        api['authorization']['operationConstraints']['chat-completions']['modelSettings']['text-model']['modelAliases'] = {'synthetic-review': 'text-model'}
        with self.assertRaisesRegex(ConfigurationError, 'modelAliases'):
            self.normalize(api)

    def test_model_settings_preserve_limits_while_outer_envelope_bounds_bytes(self):
        result=self.normalize(contract())['authorization']['operationConstraints']['chat-completions']
        self.assertEqual(result['maxRequestBytes'],262144)
        self.assertEqual([p['maxRequestBytes'] for p in result['modelSettings'].values()],[32768,262144])
        self.assertEqual(result['allowedModels'],['text-model','vision-model'])


    def test_invalid_nested_model_settings_and_contract_overrides_are_rejected(self):
        mutations=[lambda c:c.update(defaultProfile='other'),
            lambda c:c['modelSettings']['text-model'].update(settingId='invalid*'),
            lambda c:c['modelSettings']['text-model'].update(modelSettings={}),
            lambda c:c['modelSettings'].update({'unregistered-model': {}}),
            lambda c:c['modelSettings']['text-model'].update(allowedModels=['unregistered-model']),
            lambda c:c['modelSettings']['text-model'].update(bodyFormat='binary')]
        for mutate in mutations:
            api=contract();mutate(api['authorization']['operationConstraints']['chat-completions'])
            with self.assertRaises(ConfigurationError):self.normalize(api)

    def test_partial_and_empty_model_settings_keep_base_fallback(self):
        for settings in ({}, {'vision-model': {'outputTokenParameter': 'max_completion_tokens'}}):
            api = contract()
            api['authorization']['operationConstraints']['chat-completions']['modelSettings'] = settings
            result = self.normalize(api)['authorization']['operationConstraints']['chat-completions']
            self.assertEqual(result['maxRequestBytes'], 32768)
            self.assertEqual(result['allowedModels'], ['text-model', 'vision-model'])
            self.assertEqual(result['modelSettings'], settings)

    def test_normalization_is_idempotent_and_preserves_unlisted_model_size(self):
        api = contract()
        api['authorization']['operationConstraints']['chat-completions']['modelSettings'].pop('text-model')
        normalized = self.normalize(api)
        constraint = normalized['authorization']['operationConstraints']['chat-completions']
        self.assertEqual(constraint['maxRequestBytes'], 262144)
        self.assertEqual(constraint['modelSettings']['text-model']['maxRequestBytes'], 32768)
        self.assertEqual(self.normalize(normalized), normalized)

    def test_top_level_limit_tables_are_replaced_without_partial_merge(self):
        api = contract()
        constraint = api['authorization']['operationConstraints']['chat-completions']
        constraint['integerLimits'] = {'shared': {'minimum': 0, 'maximum': 10}}
        constraint['modelSettings']['vision-model']['integerLimits'] = {'special': {'minimum': 1, 'maximum': 2}}
        result = self.normalize(api)['authorization']['operationConstraints']['chat-completions']
        self.assertEqual(set(result['integerLimits']), {'shared'})
        self.assertEqual(set(result['modelSettings']['vision-model']['integerLimits']), {'special'})

    def test_model_routing_requires_routes_for_every_allowed_model(self):
        from catalog.standard_apis import load_api_definitions, API_DEFINITION_DIRECTORY
        base = next(api for api in load_api_definitions(API_DEFINITION_DIRECTORY) if api['name'] == 'blackforest-images-v1')
        normalize = lambda api: _apis([api], TENANT, {'FLUX.2-flex', 'FLUX.2-pro'},
            'https://gateway.example.com', {'subscriptionKeyHeader': 'api-key'})
        self.assertEqual(normalize(base)[0]['backendService'], 'foundryServices')
        for mutation in ('missing-entry', 'missing-route', 'invalid-route'):
            api = copy.deepcopy(base)
            settings = api['authorization']['operationConstraints']['flux2-generate']['modelSettings']
            if mutation == 'missing-entry': settings.pop('FLUX.2-pro')
            if mutation == 'missing-route': settings['FLUX.2-pro'].pop('backendUrlTemplate')
            if mutation == 'invalid-route': settings['FLUX.2-pro']['backendUrlTemplate'] = 'https://untrusted.example.com/path'
            with self.subTest(mutation=mutation), self.assertRaises(ConfigurationError):
                normalize(api)

    def test_editor_schemas_accept_model_differences_and_reject_old_or_nested_selection(self):
        from tests.support.schema_helpers import local_schema_validator, read_schema
        constraint = contract()['authorization']['operationConstraints']['chat-completions']
        constraint['modelAliases'] = {'synthetic-review': 'text-model'}
        for name in ('configuration', 'params'):
            schema = read_schema(name)
            validator = local_schema_validator({'$schema': schema['$schema'], '$ref': '#/$defs/operationConstraint', '$defs': schema['$defs']})
            with self.subTest(schema=name):
                self.assertEqual(list(validator.iter_errors(constraint)), [])
            for field, value in (('profiles', []), ('defaultProfile', 'chat')):
                broken = dict(constraint, **{field: value})
                with self.subTest(schema=name, field=field):
                    self.assertTrue(list(validator.iter_errors(broken)))
            for field, value in (('modelSettings', {}), ('allowedModels', ['text-model']), ('modelAliases', {})):
                broken = copy.deepcopy(constraint)
                broken['modelSettings']['text-model'][field] = value
                with self.subTest(schema=name, nested_field=field):
                    self.assertTrue(list(validator.iter_errors(broken)))

    def test_sample_classification_cannot_be_marked_as_platform(self):
        api=contract();api['name']='sample-inference-v1'
        with self.assertRaisesRegex(ConfigurationError,'serviceClass'):self.normalize(api)
        api['serviceClass']='sample'
        self.assertEqual(self.normalize(api)['serviceClass'],'sample')

    def test_duplicate_method_and_suffix_are_rejected(self):
        api=contract();op=copy.deepcopy(api['operations'][0]);op['name']='alias';api['operations'].append(op)
        with self.assertRaisesRegex(ConfigurationError,'route'):self.normalize(api)

    def test_operation_backend_override_preserves_api_default(self):
        api=contract();api['operations'][0]['backendOverride']={'backendService':'external',
            'backendUrl':'https://speech.example.com','backendAudience':'https://cognitiveservices.azure.com',
            'backendAuthentication':{'mode':'speechEntra','resourceId':'/subscriptions/'+TENANT+'/resourceGroups/fixture/providers/Microsoft.CognitiveServices/accounts/speech'}}
        result=self.normalize(api)
        self.assertEqual(result['backendAuthentication']['mode'],'managedIdentity')
        self.assertEqual(result['operations'][0]['backendOverride']['backendAuthentication']['mode'],'speechEntra')
        self.assertEqual(result['operations'][0]['backendOverride']['backendHeaders'],{})

    def test_operation_speech_backend_requires_foundation_speech_access(self):
        from tests.support.configuration_fixtures import api_fixture
        config=api_fixture()
        config['services']['speechEnabled']=False
        operation=config['apiDefinitions'][0]['operations'][0]
        operation['urlTemplate']='/synthesize'
        config['apiDefinitions'][0]['authorization']['operationConstraints'][operation['name']]={
            'kind':'registeredOperation','bodyFormat':'json','maxRequestBytes':32768}
        operation['backendOverride']={
            'backendService':'external','backendUrl':'https://speech.example.com',
            'backendAudience':'https://cognitiveservices.azure.com',
            'backendAuthentication':{'mode':'speechEntra','resourceId':'/subscriptions/'+TENANT+'/resourceGroups/fixture/providers/Microsoft.CognitiveServices/accounts/speech'}}
        with self.assertRaisesRegex(ConfigurationError,'services.speechEnabled'):
            normalize_configuration(config,base_directory=CORE/'infra/config')

    def test_override_rejects_untrusted_url_or_wrong_credential_boundary(self):
        variants=[{'backendService':'external','backendUrl':'http://plain.example.com'},
            {'backendService':'foundryOpenAi','backendUrl':'https://ignored.example.com'},
            {'backendService':'external','backendUrl':'https://service.example.com', 'backendAudience':'https://wrong.example.com','backendAuthentication':{'mode':'forwardValidatedToken'}},
            {'backendService':'external','backendUrl':'https://service.example.com', 'backendAuthentication':{'mode':'keyVaultHeader','headerName':'api-key','secretIdentifier':'https://kv-fixture.vault.azure.net/secrets/example'}}]
        for override in variants:
            api=contract();api['operations'][0]['backendOverride']=override
            with self.assertRaises(ConfigurationError):self.normalize(api)

    def websocket(self):
        api=contract();api.update(name='openai-realtime-ws-v1',apiType='websocket',backendService='external',backendUrl='wss://service.example.com/realtime')
        api['operations']=[{'name':'invoke','displayName':'Invoke','method':'GET','urlTemplate':'/','templateParameters':[]}]
        api['authorization']['operationConstraints']={'invoke':{'kind':'registeredOperation','bodyFormat':'none','maxRequestBytes':32768}}
        api['websocket']={'queryParameters':{},'allowedModels':['text-model','vision-model'],'defaultModel':'text-model'}
        return api


    def test_websocket_does_not_accept_unregistered_or_ambiguous_model(self):
        for mutate in [lambda a:a['websocket'].update(defaultModel='other'),lambda a:a['websocket']['queryParameters'].update(model='text-model')]:
            api=self.websocket();mutate(api)
            with self.assertRaises(ConfigurationError):self.normalize(api)

if __name__=='__main__':unittest.main()
