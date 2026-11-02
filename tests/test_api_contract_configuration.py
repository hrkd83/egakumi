"""Merged operations preserve authorization, profile and backend boundaries."""
import copy
from pathlib import Path
import sys
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / 'scripts'))
from deployment.helpers.configuration import _apis, normalize_configuration, ConfigurationError

TENANT = '11111111-1111-4111-8111-111111111111'
AUDIENCE = '22222222-2222-4222-8222-222222222222'

def contract():
    constraint = {'kind':'bodyModel','allowedModels':['text-model'],'bodyFormat':'json',
        'maxRequestBytes':32768,'maxOutputTokens':128,'outputTokenParameter':'max_tokens'}
    profiles = [dict(copy.deepcopy(constraint), profileId='chat', modalities=['text']),
        dict(copy.deepcopy(constraint), profileId='vision', modalities=['text','image'],
             allowedModels=['vision-model'],maxRequestBytes=262144)]
    return {'name':'openai-inference-v1','displayName':'Inference','serviceClass':'platform',
        'apiType':'http','path':'models/openai/v1','backendService':'foundryOpenAi',
        'backendAudience':'https://cognitiveservices.azure.com',
        'productId':'model-users','subscriptionKeyHeader':'api-key',
        'operations':[{'name':'chat-completions','displayName':'Chat','method':'POST',
            'urlTemplate':'/chat/completions','templateParameters':[]}],
        'keySubscriptions':[], 'authorization':{'mode':'subscriptionKey',
            'operationConstraints':{'chat-completions':dict(constraint,profiles=profiles,defaultProfile='chat')}}}

class ApiContractConfigurationTests(unittest.TestCase):
    def normalize(self, api):
        return _apis([api],TENANT,{'text-model','vision-model'},'https://gateway.example.com',
            {'subscriptionKeyHeader':'api-key'})[0]

    def test_profiles_preserve_limits_while_outer_envelope_bounds_bytes(self):
        result=self.normalize(contract())['authorization']['operationConstraints']['chat-completions']
        self.assertEqual(result['maxRequestBytes'],262144)
        self.assertEqual([p['maxRequestBytes'] for p in result['profiles']],[32768,262144])
        self.assertEqual(result['allowedModels'],['text-model','vision-model'])


    def test_invalid_default_modality_nested_profile_and_model_are_rejected(self):
        mutations=[lambda c:c.update(defaultProfile='other'),
            lambda c:c['profiles'][0].update(modalities=['unknown']),
            lambda c:c['profiles'][0].update(profiles=[]),
            lambda c:c['profiles'][0].update(allowedModels=['unregistered-model']),
            lambda c:c['profiles'][0].update(bodyFormat='binary')]
        for mutate in mutations:
            api=contract();mutate(api['authorization']['operationConstraints']['chat-completions'])
            with self.assertRaises(ConfigurationError):self.normalize(api)

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
        from configuration_fixtures import api_fixture
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
            normalize_configuration(config,base_directory=CORE/'config')

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
