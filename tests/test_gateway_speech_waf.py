"""SpeechのSSML/WAVだけを通し、他のWAF保護・経路・並行変更の検出を維持する。"""
import copy
import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

from test_gateway_callback_waf import (
    CONFIG, GATEWAY_ID, BASE_ID, POLICY_ID as CALLBACK_POLICY_ID, gateway, policy, api_mapping,
    ready_gateway, _callback_gateway, _writable, _writable_policy,
)
from gateway_ingress import (
    SPEECH_RULE_NAME, configure_gateway_speech_waf, speech_content_type_paths, _path_gateway, _speech_policy, _raw_body_policy, _speech_path_gateway,
)
from test_portal_gateway import Transport

ROOT = Path(__file__).resolve().parents[1]
POLICY_ID = BASE_ID + '-custom-mime'
PATHS = ['/models/azure-speech/v1/recognize', '/models/azure-speech/v1/synthesize']


def configuration():
    """実配備と同じAPI定義を使い、環境の識別子は合成値にする。"""
    return {**copy.deepcopy(CONFIG), 'apiDefinitions': [json.loads(
        (ROOT / 'apps/gateway/apim/apis/azure-speech-v1.json').read_text(encoding='utf-8'))]}


def desired_gateway(source):
    return ready_gateway(_path_gateway(configuration(), source, GATEWAY_ID, POLICY_ID, paths=PATHS,
                                       rule_name=SPEECH_RULE_NAME, policy_suffix='-custom-mime'))


def ready_policy(desired):
    value = copy.deepcopy(desired)
    value.update({'id': POLICY_ID, 'name': 'wafpol-agw-test-fixture-custom-mime', 'etag': '"speech-v2"',
                  'type': 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies'})
    value['properties']['provisioningState'] = 'Succeeded'
    return value


class GatewaySpeechWafTests(unittest.TestCase):
    def test_paths_come_only_from_canonical_post_contract_and_follow_exact_prefix(self):
        config = configuration()
        self.assertEqual(speech_content_type_paths(config), PATHS)
        config['apiDefinitions'][0]['path'] = 'tools/speech/v2'
        self.assertEqual(speech_content_type_paths(config), ['/tools/speech/v2/recognize', '/tools/speech/v2/synthesize'])
        config['apiDefinitions'][0]['name'] = 'unrelated-api'
        self.assertEqual(speech_content_type_paths(config), [])

    def test_wildcards_parameter_paths_mime_and_method_drift_are_rejected(self):
        for field, value in [('path', 'models/speech/*'), ('path', 'models/{speech}'), ('path', 'models/../speech'),
                             ('method', 'GET'), ('route', '/synthesize/*'), ('mime', ['text/xml']), ('format', 'json')]:
            config = configuration()
            api = config['apiDefinitions'][0]
            operation = next(item for item in api['operations'] if item['name'] == 'tts')
            if field == 'path': api['path'] = value
            elif field == 'method': operation['method'] = value
            elif field == 'route': operation['urlTemplate'] = value
            else: api['authorization']['operationConstraints']['tts']['contentTypes' if field == 'mime' else 'bodyFormat'] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                speech_content_type_paths(config)

    def test_policy_changes_only_920420_and_retains_all_common_protection(self):
        base = policy()
        before = copy.deepcopy(base)
        desired = _speech_policy(base)
        overrides = desired['properties']['managedRules']['managedRuleSets'][0].pop('ruleGroupOverrides')
        expected = _writable_policy(base)
        expected['properties']['managedRules']['managedRuleSets'][0].pop('ruleGroupOverrides')
        self.assertEqual(desired, expected)
        self.assertEqual(base, before)
        self.assertEqual(overrides, [{'ruleGroupName': 'REQUEST-920-PROTOCOL-ENFORCEMENT',
                                     'rules': [{'ruleId': '920420', 'state': 'Disabled'}]}])

    def test_existing_overrides_survive_and_920470_cannot_be_disabled(self):
        base = policy()
        rules = [{'ruleId': '920470', 'state': 'Enabled'}, {'ruleId': '920420', 'state': 'Enabled'}]
        base['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides'] = [
            {'ruleGroupName': 'REQUEST-920-PROTOCOL-ENFORCEMENT', 'rules': rules},
            {'ruleGroupName': 'REQUEST-942-APPLICATION-ATTACK-SQLI', 'rules': [{'ruleId': '942100', 'state': 'Enabled'}]}]
        desired = _speech_policy(base)
        actual = desired['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides']
        self.assertEqual(actual[0]['rules'], [{'ruleId': '920470', 'state': 'Enabled'}, {'ruleId': '920420', 'state': 'Disabled'}])
        self.assertEqual(actual[1], base['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides'][1])
        rules[0]['state'] = 'Disabled'
        with self.assertRaisesRegex(ValueError, '920470'):
            _speech_policy(base)

    def test_existing_callback_portal_routes_and_l7_rewrite_are_preserved(self):
        source = ready_gateway(_callback_gateway(CONFIG, gateway(path_based=True), GATEWAY_ID, CALLBACK_POLICY_ID))
        original = copy.deepcopy(source)
        desired = desired_gateway(source)
        speech = api_mapping(desired)['pathRules'][0]
        self.assertEqual(speech['name'], SPEECH_RULE_NAME)
        self.assertEqual(speech['properties']['paths'], PATHS)
        self.assertEqual(speech['properties']['firewallPolicy'], {'id': POLICY_ID})
        self.assertEqual(api_mapping(desired)['pathRules'][1:], api_mapping(source)['pathRules'])
        self.assertEqual(desired['properties']['urlPathMaps'][0], source['properties']['urlPathMaps'][0])
        for key in ('rewriteRuleSets', 'httpListeners', 'backendHttpSettingsCollection', 'backendAddressPools'):
            self.assertEqual(desired['properties'][key], source['properties'][key])
        self.assertEqual(source, original)

    def test_reuses_policy_and_route_names_and_second_run_does_not_write(self):
        source, base = gateway(path_based=True), policy()
        updated_gateway, updated_policy = desired_gateway(source), ready_policy(_raw_body_policy(base))
        transport = Transport([{'statusCode': 200, 'body': source}, {'statusCode': 200, 'body': base},
            {'statusCode': 404}, {'statusCode': 202}, {'statusCode': 200, 'body': updated_policy},
            {'statusCode': 202}, {'statusCode': 200, 'body': updated_gateway}])
        receipt = configure_gateway_speech_waf(configuration(), transport, sleep=lambda _: None)
        self.assertEqual(receipt['policyId'], POLICY_ID)
        self.assertEqual(receipt['disabledRuleIds'], ['920420', '930110'])
        self.assertEqual(receipt['retainedRuleIds'], ['920470', '930100'])
        writes = [call for call in transport.calls if call['method'] == 'PUT']
        self.assertEqual(writes[0]['headers'], {'If-None-Match': '*'})
        self.assertEqual(writes[1]['headers'], {'If-Match': '"gateway-v1"'})
        again = Transport([{'statusCode': 200, 'body': updated_gateway}, {'statusCode': 200, 'body': base},
                           {'statusCode': 200, 'body': updated_policy}])
        receipt = configure_gateway_speech_waf(configuration(), again)
        self.assertFalse(receipt['policyUpdated'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertEqual([call['method'] for call in again.calls], ['GET', 'GET', 'GET'])

    def test_azure_reordered_paths_finish_routing_poll_and_repeated_run_is_readonly(self):
        source, base = desired_gateway(gateway(path_based=True)), policy()
        mapping = api_mapping(source)
        # The managed rule stays between unrelated rules; only its policy needs updating.
        selected = mapping['pathRules'].pop(0)
        mapping['pathRules'].append(selected)
        following = copy.deepcopy(mapping['pathRules'][0])
        following['name'] = 'following-api'
        following['properties']['paths'] = ['/unrelated/exact']
        mapping['pathRules'].append(following)
        selected['properties']['firewallPolicy'] = {'id': BASE_ID + '-previous'}
        before = copy.deepcopy(source)
        observed = desired_gateway(source)
        observed_rule = next(rule for rule in api_mapping(observed)['pathRules']
                             if rule['name'] == SPEECH_RULE_NAME)
        observed_rule['properties']['paths'].reverse()
        updated_policy = ready_policy(_raw_body_policy(base))
        transport = Transport([{'statusCode': 200, 'body': source}, {'statusCode': 200, 'body': base},
            {'statusCode': 200, 'body': updated_policy}, {'statusCode': 202},
            {'statusCode': 200, 'body': observed}])
        receipt = configure_gateway_speech_waf(configuration(), transport, timeout=1,
            sleep=lambda _: self.fail('Succeeded routing with the same paths must finish polling'))
        self.assertTrue(receipt['gatewayUpdated'])
        self.assertFalse(receipt['policyUpdated'])
        self.assertEqual(source, before)
        writes = [call for call in transport.calls if call['method'] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['headers'], {'If-Match': '"gateway-v2"'})
        sent = writes[0]['body']
        self.assertEqual([rule['name'] for rule in api_mapping(sent)['pathRules']],
                         [rule['name'] for rule in mapping['pathRules']])
        self.assertEqual(api_mapping(sent)['pathRules'][0], mapping['pathRules'][0])
        self.assertEqual(api_mapping(sent)['pathRules'][2], mapping['pathRules'][2])
        self.assertEqual(sent['properties']['rewriteRuleSets'], source['properties']['rewriteRuleSets'])
        self.assertEqual(api_mapping(sent)['defaultRewriteRuleSet'], mapping['defaultRewriteRuleSet'])
        self.assertEqual(api_mapping(sent)['pathRules'][1]['properties']['rewriteRuleSet'],
                         selected['properties']['rewriteRuleSet'])
        again = Transport([{'statusCode': 200, 'body': observed}, {'statusCode': 200, 'body': base},
                           {'statusCode': 200, 'body': updated_policy}])
        receipt = configure_gateway_speech_waf(configuration(), again)
        self.assertFalse(receipt['policyUpdated'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertEqual([call['method'] for call in again.calls], ['GET', 'GET', 'GET'])
        self.assertEqual(observed_rule['properties']['paths'], list(reversed(PATHS)))

    def test_existing_exact_rule_rejects_path_or_backend_drift(self):
        for case in ('missing-path', 'extra-path', 'duplicate-path', 'pool', 'settings', 'rewrite'):
            source = desired_gateway(gateway(path_based=True))
            current = api_mapping(source)['pathRules'][0]['properties']
            current['paths'].reverse()
            if case == 'missing-path':
                current['paths'].pop()
            elif case == 'extra-path':
                current['paths'].append('/unrelated/extra')
            elif case == 'duplicate-path':
                current['paths'].append(current['paths'][0])
            else:
                key = {'pool': 'backendAddressPool', 'settings': 'backendHttpSettings',
                       'rewrite': 'rewriteRuleSet'}[case]
                current[key] = {'id': '/other/' + case}
            before = copy.deepcopy(source)
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, 'backend, rewrite or path'):
                _path_gateway(configuration(), source, GATEWAY_ID, POLICY_ID,
                    paths=PATHS, rule_name=SPEECH_RULE_NAME, policy_suffix='-custom-mime')
            self.assertEqual(source, before)

    def test_reordered_managed_paths_still_move_above_a_matching_wildcard(self):
        source = desired_gateway(gateway(path_based=True))
        mapping = api_mapping(source)
        selected = mapping['pathRules'].pop(0)
        selected['properties']['paths'].reverse()
        mapping['pathRules'][0]['properties']['paths'] = ['/models/*']
        mapping['pathRules'].append(selected)
        before = copy.deepcopy(source)
        result = _speech_path_gateway(configuration(), source, GATEWAY_ID, POLICY_ID,
            paths=PATHS, rule_name=SPEECH_RULE_NAME, policy_suffix='-custom-mime')
        rules = api_mapping(result)['pathRules']
        self.assertEqual([rule['name'] for rule in rules], [SPEECH_RULE_NAME, 'existing-api'])
        self.assertEqual(rules[0]['properties']['paths'], list(reversed(PATHS)))
        self.assertEqual(rules[1], mapping['pathRules'][0])
        self.assertEqual(result['properties']['rewriteRuleSets'], source['properties']['rewriteRuleSets'])
        self.assertEqual(source, before)

    def test_arm_empty_skipped_rule_sets_finish_policy_polling_and_second_run_is_readonly(self):
        source, base = desired_gateway(gateway(path_based=True)), policy()
        for rule in base['properties']['customRules']:
            rule['skippedManagedRuleSets'] = []
        observed = ready_policy(_raw_body_policy(base))
        for rule in observed['properties']['customRules']:
            rule['skippedManagedRuleSets'] = []
        transport = Transport([{'statusCode': 200, 'body': source}, {'statusCode': 200, 'body': base},
            {'statusCode': 404}, {'statusCode': 202}, {'statusCode': 200, 'body': observed}])
        receipt = configure_gateway_speech_waf(configuration(), transport, timeout=1, sleep=lambda _: None)
        self.assertTrue(receipt['policyUpdated'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertEqual(len([call for call in transport.calls if call['method'] == 'PUT']), 1)
        again = Transport([{'statusCode': 200, 'body': source}, {'statusCode': 200, 'body': base},
                           {'statusCode': 200, 'body': observed}])
        receipt = configure_gateway_speech_waf(configuration(), again)
        self.assertFalse(receipt['policyUpdated'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertTrue(all(call['method'] == 'GET' for call in again.calls))

    def test_policy_normalization_preserves_nonempty_skips_and_unknown_custom_rule_fields(self):
        base = policy()
        custom = base['properties']['customRules'][0]
        custom['skippedManagedRuleSets'] = [{'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2'}]
        custom['futureProtection'] = {'keep': True}
        before = copy.deepcopy(base)
        self.assertEqual(_writable_policy(base)['properties']['customRules'], before['properties']['customRules'])
        self.assertEqual(base, before)
        for unknown in (None, '', {'future': True}):
            custom['skippedManagedRuleSets'] = unknown
            self.assertEqual(_writable_policy(base)['properties']['customRules'][0]['skippedManagedRuleSets'], unknown)
        custom['skippedManagedRuleSets'] = []
        normalized = _writable_policy(base)['properties']['customRules'][0]
        self.assertNotIn('skippedManagedRuleSets', normalized)
        self.assertEqual(normalized['futureProtection'], {'keep': True})

    def test_external_waf_is_rejected_and_no_speech_api_requires_no_azure_read(self):
        source = gateway()
        source['properties']['firewallPolicy']['id'] = '/enterprise/waf'
        transport = Transport([{'statusCode': 200, 'body': source}])
        with self.assertRaisesRegex(ValueError, 'external WAF'):
            configure_gateway_speech_waf(configuration(), transport)
        self.assertEqual([call['method'] for call in transport.calls], ['GET'])
        empty = Transport([])
        self.assertFalse(configure_gateway_speech_waf(CONFIG, empty)['required'])
        self.assertEqual(empty.calls, [])

    def test_raw_body_adjustment_keeps_settings_and_adds_four_non_body_block_controls(self):
        base = policy()
        configured = _raw_body_policy(base)['properties']
        self.assertEqual(base, policy())
        self.assertEqual(configured['policySettings'], base['properties']['policySettings'])
        rules = configured['customRules']
        self.assertEqual(rules[0], base['properties']['customRules'][0])
        self.assertEqual([rule['priority'] for rule in rules[1:]], [2, 3, 4, 5])
        self.assertEqual({rule['matchConditions'][0]['matchVariables'][0]['variableName'] for rule in rules[1:]},
                         {'RequestUri', 'QueryString', 'RequestHeaders', 'RequestCookies'})
        for rule in rules[1:]:
            self.assertEqual(rule['action'], 'Block')
            condition = rule['matchConditions'][0]
            self.assertEqual(len(condition['matchVariables']), 1)
            self.assertNotIn('selector', condition['matchVariables'][0])
            expression = re.compile(condition['matchValues'][0])
            for value in ('../file', r'..\file', '/dir/..', 'q=../file', '%2e%2e%2ffile', '%252e%252e%252ffile', '%25252e%25252e%25252ffile', '.%00./file'):
                self.assertIsNotNone(expression.search(unquote(value).replace('\x00', '').lower()), value)
            for depth in range(9):
                dot, slash = '%' + '25' * depth + '2e', '%' + '25' * depth + '5c'
                self.assertIsNotNone(expression.search(unquote(dot + dot + slash).lower()))
            self.assertIsNone(expression.search('normal/file.txt?key=value'))
        groups = configured['managedRules']['managedRuleSets'][0]['ruleGroupOverrides']
        self.assertEqual(groups[-1], {'ruleGroupName': 'REQUEST-930-APPLICATION-ATTACK-LFI',
                                     'rules': [{'ruleId': '930110', 'state': 'Disabled'}]})

    def test_raw_custom_rule_collisions_disabled_930100_and_detection_mode_are_rejected(self):
        for mutation in ('name', 'priorities', '930100', 'mode'):
            base = policy()
            if mutation == 'name': base['properties']['customRules'][0]['name'] = 'coreRawTraversalUri'
            elif mutation == 'priorities': base['properties']['customRules'] = [
                {'name': 'existing-' + str(priority), 'priority': priority, 'action': 'Block'} for priority in range(1, 101)]
            elif mutation == 'mode': base['properties']['policySettings']['mode'] = 'Detection'
            else: base['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides'] = [{
                'ruleGroupName': 'REQUEST-930-APPLICATION-ATTACK-LFI', 'rules': [{'ruleId': '930100', 'state': 'Disabled'}]}]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _raw_body_policy(base)

    def test_existing_speech_rule_does_not_move_or_require_a_gateway_put(self):
        source = desired_gateway(gateway(path_based=True))
        mapping = api_mapping(source)
        mapping['pathRules'].append(mapping['pathRules'].pop(0))
        result = _speech_path_gateway(configuration(), source, GATEWAY_ID, POLICY_ID,
            paths=PATHS, rule_name=SPEECH_RULE_NAME, policy_suffix='-custom-mime')
        self.assertEqual(result, _writable(source))

    def test_mixed_mime_route_splits_only_canonical_speech_paths(self):
        source = desired_gateway(gateway(path_based=True))
        api_mapping(source)['pathRules'][0]['properties']['paths'].append('/company/ssml')
        new_id = BASE_ID + '-speech-raw-body'
        result = _speech_path_gateway(configuration(), source, GATEWAY_ID, new_id,
            paths=PATHS, rule_name='speech-raw-body', policy_suffix='-speech-raw-body')
        rules = api_mapping(result)['pathRules']
        self.assertEqual(rules[0]['name'], 'speech-raw-body')
        self.assertEqual(rules[0]['properties']['paths'], PATHS)
        self.assertEqual(next(rule for rule in rules if rule['name'] == SPEECH_RULE_NAME)['properties']['paths'], ['/company/ssml'])

    def test_bicep_matches_raw_helper_and_separates_unrelated_mime_contracts(self):
        from test_gateway_document_waf import GatewayDocumentWafBicepTests
        from test_ssml_waf import GatewayExpression
        GatewayDocumentWafBicepTests.setUpClass()
        helper = GatewayDocumentWafBicepTests()
        for mixed in (False, True):
            values = helper.parameters(documentUploadPaths=[], customMimePaths=PATHS + (['/company/ssml'] if mixed else []), speechRawBodyPaths=PATHS)
            common = helper.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")
            baseline = helper.value(common['properties'], values)
            suffix = 'speech-raw-body' if mixed else 'custom-mime'
            resource = helper.resource("[format('wafpol-agw-{0}-" + suffix + "', parameters('resourceStem'))]")
            self.assertEqual(helper.value(resource['properties'], values), _raw_body_policy({'properties': baseline})['properties'])
            gateway_resource = next(item for item in helper.resources if item['type'] == 'Microsoft.Network/applicationGateways')
            properties = helper.value(gateway_resource['properties'], values)
            mapping = next(item['properties'] for item in properties['urlPathMaps'] if item['name'] == 'pathmap-protocols')
            selected = next(item for item in mapping['pathRules'] if item['name'] == ('speech-raw-body' if mixed else SPEECH_RULE_NAME))
            self.assertEqual(selected['properties']['paths'], PATHS)
            self.assertEqual(sum(path in rule['properties']['paths'] for rule in mapping['pathRules'] for path in PATHS), 2)
            if mixed:
                mime = next(item for item in mapping['pathRules'] if item['name'] == SPEECH_RULE_NAME)
                self.assertEqual(mime['properties']['paths'], ['/company/ssml'])
                common_mime = helper.resource("[format('wafpol-agw-{0}-custom-mime', parameters('resourceStem'))]")
                self.assertEqual(helper.value(common_mime['properties'], values)['customRules'], baseline['customRules'])
        from standard_apis import infrastructure_api_definitions
        api = configuration()['apiDefinitions'][0]
        expression = helper.main['variables']['speechRawBodyPaths']
        evaluate = lambda candidate: GatewayExpression(expression, helper.main['variables']).evaluate({
            'configuration': {'apiDefinitions': [], 'infrastructureApiDefinitions': infrastructure_api_definitions([candidate])}})
        self.assertEqual(sorted(evaluate(api)), PATHS)
        api['name'] = 'unrelated-ssml'
        self.assertEqual(evaluate(api), [])

    def test_raw_controls_have_no_ineffective_ssml_body_rule_and_fit_four_free_priorities(self):
        base = policy()
        controls = _raw_body_policy(base)['properties']['customRules']
        self.assertEqual(len(controls), len(base['properties']['customRules']) + 4)
        self.assertEqual([rule['name'] for rule in controls[-4:]], ['coreRawTraversalUri', 'coreRawTraversalQuery', 'coreRawTraversalHeaders', 'coreRawTraversalCookies'])
        self.assertNotIn('RequestBody', json.dumps(controls[-4:]))
        base['properties']['customRules'] = [{'name': 'existing-' + str(priority), 'priority': priority, 'action': 'Block'} for priority in range(1, 97)]
        self.assertEqual(len(_raw_body_policy(base)['properties']['customRules']), 100)
        base['properties']['customRules'].append({'name': 'last-slot', 'priority': 97, 'action': 'Block'})
        with self.assertRaisesRegex(ValueError, 'priorities'):
            _raw_body_policy(base)


if __name__ == '__main__':
    unittest.main()
