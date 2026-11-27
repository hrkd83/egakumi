"""入力検査の登録対象を、公開設定・合成追加API・生成済みARMで確認する。"""
import base64
import copy
import json
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import DiscoveryExpression, compiled_policy_value
from bicep_helpers import compile_bicep
from configuration_fixtures import CORE, configuration
from deployment.helpers.apim_input_contract import input_contracts
from test_api_protocols import bound_coverage, bound_external_coverage


STANDARD_OPERATIONS = {
    ('openai-inference-v1', 'chat-completions'), ('openai-inference-v1', 'responses-create'),
    ('openai-images-v1', 'image-generate'), ('openai-images-v1', 'image-edit'),
    ('blackforest-images-v1', 'flux2-generate'), ('blackforest-images-v1', 'flux2-edit'),
    ('openai-embeddings-v1', 'embeddings'), ('cohere-inference-v1', 'cohere-embeddings'),
    ('cohere-inference-v1', 'rerank'), ('openai-audio-v1', 'audio-transcriptions'),
    ('azure-speech-v1', 'stt'), ('azure-speech-v1', 'tts'), ('microsoft-decision-v1', 'systemone'),
}


class InputContractSelectionTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / 'config')

    def external(self):
        raw = bound_external_coverage()
        api = next(api for api in raw['apiDefinitions'] if api['name'] == 'sample-rest-v1')
        # 元のfixtureは本文なしのGETです。追加検査の試験ではJSONを受ける社内APIとして宣言します。
        operation = api['operations'][0]
        constraint = api['authorization']['operationConstraints'][operation['name']]
        constraint.update(bodyFormat='json', contentTypes=['application/json'])
        return raw, api

    def test_only_the_13_approved_standard_operations_are_selected(self):
        config = self.normalize(bound_coverage())
        selected = {(api['name'], operation['name']) for api in config['apiDefinitions']
                    for operation in api['operations'] if 'inputContract' in operation}
        self.assertEqual(selected, STANDARD_OPERATIONS)
        for api in config['apiDefinitions']:
            table = input_contracts(api)
            self.assertEqual(set(table), {operation['name'] for operation in api['operations'] if 'inputContract' in operation})
            for name, contract in table.items():
                self.assertEqual(contract, {})
                operation = next(operation for operation in api['operations'] if operation['name'] == name)
                self.assertEqual(operation['inputContract'], {})

    def test_external_json_operation_can_opt_in_without_known_service_names(self):
        raw, api = self.external()
        operation = api['operations'][0]
        operation['inputContract'] = {}
        operation['method'] = 'PUT'
        before = copy.deepcopy(raw)
        actual = next(api for api in self.normalize(raw)['apiDefinitions'] if api['name'] == 'sample-rest-v1')
        self.assertEqual(input_contracts(actual), {operation['name']: {}})
        self.assertEqual(actual['authorization']['operationConstraints'][operation['name']]['contentTypes'], ['application/json'])
        self.assertEqual(actual['operations'][0]['method'], 'PUT')
        self.assertEqual(raw, before)
        unselected = copy.deepcopy(actual)
        unselected['operations'][0].pop('inputContract')
        self.assertEqual(input_contracts(unselected), {})
        actual['apiType'] = 'mcp'
        self.assertEqual(input_contracts(actual), {})

    def test_invalid_contracts_are_rejected_during_normalization(self):
        for value in (None, [], True, {'method': 'POST'}, {'contentTypes': ['application/json']}, {'queryParameters': {}},
                      {'queryParameters': {'workspace': ['synthetic']}}, {'extra': True}):
            raw, api = self.external()
            api['operations'][0]['inputContract'] = value
            with self.subTest(value=value), self.assertRaisesRegex(configuration.ConfigurationError, 'inputContract'):
                self.normalize(raw)

    def test_empty_input_contract_cannot_create_query_or_media_allowlists(self):
        raw, api = self.external()
        operation = api['operations'][0]
        operation['inputContract'] = {}
        actual = next(api for api in self.normalize(raw)['apiDefinitions'] if api['name'] == 'sample-rest-v1')
        self.assertEqual(input_contracts(actual)[operation['name']], {})

    def test_compiled_external_api_calls_input_validation_once_before_profile_and_body_processing(self):
        raw, api = self.external()
        api['operations'][0]['inputContract'] = {}
        api['operations'][0]['method'] = 'PUT'
        api['operations'][0]['urlTemplate'] = '/synthetic/messages/{messageId}'
        api['operations'][0]['templateParameters'] = [{'name': 'messageId', 'type': 'string', 'required': True}]
        api = next(api for api in self.normalize(raw)['apiDefinitions'] if api['name'] == 'sample-rest-v1')
        template = compile_bicep('apps/api-gateway/api-publication.bicep')
        parameters = {name: copy.deepcopy(value['defaultValue']) for name, value in template['parameters'].items() if 'defaultValue' in value}
        parameters['apiDefinitions'] = [api]
        root = ET.fromstring(compiled_policy_value(template, parameters))
        # 入力表は対象IDだけを持ち、操作のMethod/URLや本文形式の設定は独立して維持します。
        settings = root.find('inbound/set-variable[@name="gatewayApiSettingsSource"]')
        lines = base64.b64decode(settings.get('value'), validate=True).decode('utf-8').split('\n')
        controls = json.loads(lines[2])
        table = controls['inputContracts']
        operation = api['operations'][0]
        self.assertEqual(table, {operation['name']: {}})
        self.assertEqual(controls['operationConstraints'], api['authorization']['operationConstraints'])
        self.assertEqual(controls['operationConstraints'][operation['name']]['contentTypes'], ['application/json'])
        reader = DiscoveryExpression('[variables(\'operationEntries\')]', template['variables'], functions=template['functions'])
        entries = reader.evaluate(parameters)
        selected_index = next(index for index, entry in enumerate(entries)
                              if entry['apiName'] == api['name'] and entry['operation']['name'] == operation['name'])
        properties = reader.resolve(template['resources']['operations']['properties'], parameters, selected_index)
        self.assertEqual(properties['method'], 'PUT')
        self.assertEqual(properties['urlTemplate'], '/synthetic/messages/{messageId}')
        inbound = list(root.find('inbound'))
        calls = root.findall('inbound/include-fragment[@fragment-id="core-api-input-validation"]')
        self.assertEqual(len(calls), 1)
        selection = root.find('inbound/include-fragment[@fragment-id="core-api-operation-settings"]')
        preparation = root.find('inbound/include-fragment[@fragment-id="core-api-request-preparation"]')
        observation = root.find('inbound/include-fragment[@fragment-id="core-query-observation"]')
        self.assertLess(inbound.index(preparation), inbound.index(observation))
        self.assertLess(inbound.index(observation), inbound.index(calls[0]))
        self.assertLess(inbound.index(calls[0]), inbound.index(selection))
        response = inbound[inbound.index(calls[0]) + 1].find('when/return-response')
        self.assertEqual(response.find('set-status').get('code'), '400')
        self.assertIsNone(response.find('set-body'))
        for node in inbound[:inbound.index(calls[0]) + 1]:
            self.assertNotIn('context.Request.Body', ET.tostring(node, encoding='unicode'))


if __name__ == '__main__':
    unittest.main()
