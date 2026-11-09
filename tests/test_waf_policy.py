"""Data exclusions retain Prevention and remain confined to finite published paths."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from waf_policy import (exclusion, load_manifest, selected_profiles, published_paths,
                        desired_policy, reconcile, canonical_gateway, RESERVED_POLICY_KEYS)
from deployment.helpers.portal_gateway import portal_session_exclusions
from deployment.helpers.waf_names import (DATA_POLICY_KEYS, POLICY_KEYS,
                                           policy_id as waf_policy_id, policy_name)
from test_gateway_callback_waf import CONFIG, GATEWAY_ID, BASE_ID, POOL, BACKEND, REWRITE, gateway, policy, api_mapping

CHAT = '/models/openai/v1/chat/completions'


def configuration():
    value = copy.deepcopy(CONFIG)
    value['apiDefinitions'] = [{'path': 'models/openai/v1', 'operations': [
        {'name': 'chat', 'method': 'POST', 'urlTemplate': '/chat/completions'}],
        'authorization': {'operationConstraints': {'chat': {'kind': 'bodyModel', 'profiles': [
            {'modalities': ['text', 'image', 'audio']}]}}}}]
    return value


def manifest():
    return {'version': 1, 'profiles': [{'name': 'g-chat-completions', 'scope': 'gateway', 'paths': [CHAT],
        'exclusions': [exclusion('messages.content', 'REQUEST-942-APPLICATION-ATTACK-SQLI', ['942100'])]}]}


class ArmMemory:
    def __init__(self, gateway_value=None, policies=None, subresource_metadata=False):
        self.resources = {GATEWAY_ID: gateway_value or gateway(), BASE_ID: policy()}
        self.resources.update(policies or {})
        self.calls = []
        self.conflict = False
        self.subresource_metadata = subresource_metadata

    def request(self, method, path, body=None, headers=None):
        identifier = path.split('?', 1)[0]
        self.calls.append((method, identifier, copy.deepcopy(body), headers))
        current = self.resources.get(identifier)
        if method == 'GET':
            if not current:
                return {'statusCode': 404}
            value = copy.deepcopy(current)
            if self.subresource_metadata and identifier == GATEWAY_ID:
                def decorate(item, location):
                    if isinstance(item, dict):
                        if isinstance(item.get('name'), str) and isinstance(item.get('properties'), dict):
                            item.setdefault('id', location + '/' + item['name'])
                            item.setdefault('type', 'Microsoft.Network/applicationGateways/inlineResource')
                        for key, nested in item.items():
                            decorate(nested, location + '/' + key)
                    elif isinstance(item, list):
                        for nested in item:
                            decorate(nested, location)
                decorate(value, identifier)
            return {'statusCode': 200, 'body': value}
        if method == 'PUT':
            if self.conflict or (current and headers.get('If-Match') != current['etag']):
                return {'statusCode': 412}
            value = copy.deepcopy(body)
            value.update(id=identifier, name=identifier.rsplit('/', 1)[-1], etag='"updated"')
            value['type'] = ('Microsoft.Network/applicationGateways' if identifier == GATEWAY_ID
                             else 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies')
            value['properties']['provisioningState'] = 'Succeeded'
            self.resources[identifier] = value
            return {'statusCode': 202}
        raise AssertionError(method)


class DataWafTests(unittest.TestCase):
    def test_manifest_keys_match_their_gateway_or_portal_scope(self):
        for name, scope in [('p-generate', 'gateway'), ('g-chat-completions', 'portal'),
                            ('chat-text', 'gateway'), ('g--chat', 'gateway')]:
            value = manifest()
            value['profiles'][0].update(name=name, scope=scope)
            with self.subTest(name=name, scope=scope), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'manifest.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_manifest(path)

    def test_data_profiles_cannot_overwrite_any_reserved_protocol_policy(self):
        for name in RESERVED_POLICY_KEYS:
            value = manifest()
            value['profiles'][0].update(name=name, scope='gateway' if name.startswith('g-') else 'portal')
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'manifest.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_manifest(path)
                with self.assertRaises(ValueError):
                    reconcile(configuration(), None, manifest=value)

    def test_data_profiles_reject_unlisted_policy_keys(self):
        for name in ('g-unlisted', 'p-unlisted'):
            value = manifest()
            value['profiles'][0].update(name=name, scope='gateway' if name.startswith('g-') else 'portal')
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    selected_profiles(configuration(), manifest=value)
                with self.assertRaises(ValueError):
                    reconcile(configuration(), None, manifest=value)
                with self.assertRaises(ValueError):
                    policy_name(CONFIG, name)

    def test_standard_profile_merges_keep_the_approved_full_exclusion_unions(self):
        profiles = {item['name']: item for item in load_manifest()['profiles']}
        self.assertEqual(set(profiles), {
            'g-chat-completions', 'g-responses', 'g-embeddings', 'g-rerank', 'g-decision',
            'g-images', 'g-transcribe', 'g-realtime', 'g-live', 'g-pii-redact',
            'p-generate', 'p-embeddings', 'p-rerank', 'p-decision', 'p-images',
            'p-speech', 'p-realtime', 'p-document-upload', 'p-pii-redact'})
        self.assertEqual(set(profiles), DATA_POLICY_KEYS)
        self.assertEqual(POLICY_KEYS, DATA_POLICY_KEYS | RESERVED_POLICY_KEYS)
        self.assertEqual(len(POLICY_KEYS), 25)
        # Golden tuples include variable/operator/selector and OWASP type/version/group/ID.
        # They were captured from the approved unions of the previous separate profiles.
        expected = {
            'g-embeddings': (2, 90, '1a49ab9b9cd891c6219af00946ebc644702ed869b80834dd32757964ece32ab0'),
            'g-images': (6, 48, '200b43a7b5bb1ee91534d6e089cc0dc66042ff2164a5476c31aeca7b0f8a1dca'),
            'g-pii-redact': (2, 90, '3067508805f9dc859ddc93dbea1df3f305557caee8b5d7c0ab501546cb013798'),
            'p-pii-redact': (2, 45, '19799e83b1e4ee13e438d30009ce38529c6309c493cc33934bd5ca4e78d24664'),
        }
        for name, (path_count, tuple_count, digest) in expected.items():
            profile = profiles[name]
            entries = profile['exclusions']
            tuples = sorted((e['matchVariable'], e['selectorMatchOperator'], e['selector'],
                             rule_set['ruleSetType'], rule_set['ruleSetVersion'],
                             group['ruleGroupName'], rule['ruleId'])
                            for e in entries for rule_set in e['exclusionManagedRuleSets']
                            for group in rule_set['ruleGroups'] for rule in group['rules'])
            with self.subTest(profile=name):
                self.assertEqual(len(profile['paths']), path_count)
                self.assertEqual(len(tuples), tuple_count)
                self.assertEqual(len(tuples), len(set(tuples)))
                actual = hashlib.sha256(json.dumps(tuples, separators=(',', ':')).encode()).hexdigest()
                self.assertEqual(actual, digest)
        self.assertEqual(set(profiles['g-embeddings']['paths']),
                         {'/models/cohere/v1/embed', '/models/openai-embeddings/v1/embeddings'})
        self.assertEqual(set(profiles['g-pii-redact']['paths']),
                         {'/tools/pii/v1/redact', '/tools/pii-v1-mcp/mcp'})
        self.assertEqual(set(profiles['p-pii-redact']['paths']),
                         {'/api/tools/pii', '/api/tools/pii-mcp'})

    def test_standard_media_exceptions_are_explicit_and_do_not_follow_config_modalities(self):
        profiles = {item['name']: item for item in load_manifest()['profiles']}
        for name, selector in [('g-chat-completions', 'messages.content.image_url.url'),
                               ('g-responses', 'input.content.file_data')]:
            profile = profiles[name]
            expected = exclusion(selector, 'REQUEST-941-APPLICATION-ATTACK-XSS', ['941130', '941170'])
            if name == 'g-chat-completions':
                expected['exclusionManagedRuleSets'][0]['ruleGroups'].append({
                    'ruleGroupName': 'REQUEST-942-APPLICATION-ATTACK-SQLI',
                    'rules': [{'ruleId': '942340'}, {'ruleId': '942450'}]})
            with self.subTest(profile=name):
                self.assertEqual([e for e in profile['exclusions'] if e['selector'] == selector], [expected])
                without_media = copy.deepcopy(profile)
                without_media['exclusions'].remove(expected)
                result = desired_policy(policy(), without_media, configuration())
                self.assertNotIn(expected, result['properties']['managedRules']['exclusions'])
        self.assertFalse(any(e['selector'] == 'messages.content.input_audio.data'
                             for p in profiles.values() for e in p['exclusions']))

    def test_chat_observed_rule_update_changes_only_two_fields_and_one_owned_policy(self):
        current = next(p for p in load_manifest()['profiles'] if p['name'] == 'g-chat-completions')
        previous = copy.deepcopy(current)
        additions = {
            ('RequestArgValues', 'Equals', 'messages.content.image_url.url'): {'942340', '942450'},
            ('RequestArgKeys', 'StartsWith', 'tools.function.parameters.'): {'942200', '942260', '942480'},
        }
        for entry in previous['exclusions']:
            remove = additions.get((entry['matchVariable'], entry['selectorMatchOperator'], entry['selector']), set())
            rule_set = entry['exclusionManagedRuleSets'][0]
            for group in rule_set['ruleGroups']:
                group['rules'] = [rule for rule in group['rules'] if rule['ruleId'] not in remove]
            rule_set['ruleGroups'] = [group for group in rule_set['ruleGroups'] if group['rules']]

        config, transport = configuration(), ArmMemory()
        reconcile(config, transport, scopes=('gateway',), manifest={'version': 1, 'profiles': [previous]})
        before = copy.deepcopy(transport.resources)
        transport.calls.clear()
        result = reconcile(config, transport, scopes=('gateway',), manifest={'version': 1, 'profiles': [current]})
        identifier = waf_policy_id(config, 'g-chat-completions')
        self.assertEqual((result['policyUpdates'], result['gatewayUpdated'], result['azureWrites']), (1, False, 1))
        writes = [call for call in transport.calls if call[0] == 'PUT']
        self.assertEqual([(call[1], call[3]) for call in writes], [(identifier, {'If-Match': '"updated"'})])
        self.assertEqual(set(transport.resources), set(before))
        for resource_id in before:
            if resource_id != identifier:
                self.assertEqual(transport.resources[resource_id], before[resource_id])

        def tuples(properties):
            return {(entry['matchVariable'], entry['selectorMatchOperator'], entry['selector'],
                     rule_set['ruleSetType'], rule_set['ruleSetVersion'], group['ruleGroupName'], rule['ruleId'])
                    for entry in properties['managedRules']['exclusions']
                    for rule_set in entry.get('exclusionManagedRuleSets', [])
                    for group in rule_set['ruleGroups'] for rule in group['rules']}

        old, new = before[identifier]['properties'], transport.resources[identifier]['properties']
        expected = {(*scope, 'OWASP', '3.2', 'REQUEST-942-APPLICATION-ATTACK-SQLI', rule_id)
                    for scope, rule_ids in additions.items() for rule_id in rule_ids}
        self.assertEqual(tuples(new) - tuples(old), expected)
        self.assertFalse(tuples(old) - tuples(new))
        for name in ('policySettings', 'customRules'):
            self.assertEqual(new[name], old[name])
        self.assertEqual(new['managedRules']['managedRuleSets'], old['managedRules']['managedRuleSets'])
        transport.calls.clear()
        self.assertEqual(reconcile(config, transport, scopes=('gateway',),
                                   manifest={'version': 1, 'profiles': [current]})['azureWrites'], 0)
        self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_manifest_rejects_wildcard_duplicate_scope_and_unscoped_rules(self):
        for change in ('path', 'duplicate', 'global', 'aggregate', 'selector'):
            value = manifest()
            item = value['profiles'][0]
            if change == 'path': item['paths'] = ['/models/*']
            elif change == 'duplicate': value['profiles'].append({**copy.deepcopy(item), 'name': 'g-responses'})
            elif change == 'global': item['exclusions'][0].pop('exclusionManagedRuleSets')
            elif change == 'aggregate': item['exclusions'][0]['exclusionManagedRuleSets'][0]['ruleGroups'][0]['rules'] = [{'ruleId': '949110'}]
            else: item['exclusions'][0]['selector'] = ''
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'manifest.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError): load_manifest(path)

    def test_startswith_requires_a_complete_field_boundary(self):
        for selector, allowed in [('input', False), ('mess', False), ('state.', True), ('schema.', True)]:
            value = manifest()
            item = value['profiles'][0]['exclusions'][0]
            item.update(selectorMatchOperator='StartsWith', selector=selector)
            with self.subTest(selector=selector), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'manifest.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                if allowed:
                    self.assertEqual(load_manifest(path), value)
                else:
                    with self.assertRaises(ValueError):
                        load_manifest(path)

    def test_log4j_exclusion_requires_exact_known_cve_group_and_rule(self):
        for group, rule, valid in [('Known-CVEs', '800100', True), ('Known-CVEs', '800110', False),
                                   ('Known-CVEs', '942100', False), ('REQUEST-942-APPLICATION-ATTACK-SQLI', '800100', False)]:
            value = manifest()
            value['profiles'][0]['exclusions'] = [exclusion('messages.content', group, [rule])]
            with self.subTest(group=group, rule=rule), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'manifest.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                if valid:
                    self.assertEqual(load_manifest(path), value)
                else:
                    with self.assertRaises(ValueError):
                        load_manifest(path)

    def test_finite_deployment_expansion_and_unpublished_paths_are_filtered(self):
        config = configuration()
        api = config['apiDefinitions'][0]
        api['operations'].append({'name': 'legacy-chat', 'method': 'POST', 'urlTemplate': '/deployments/{deployment}/chat/completions'})
        api['authorization']['operationConstraints']['legacy-chat'] = {'allowedModels': ['a', 'b', '../unsafe']}
        api['mcp'] = {'path': 'tools/pii-v1-mcp'}
        paths = published_paths(config)
        self.assertIn('/tools/pii-v1-mcp/mcp', paths)
        self.assertIn('/models/openai/v1/deployments/a/chat/completions', paths)
        self.assertIn('/models/openai/v1/deployments/b/chat/completions', paths)
        self.assertFalse(any('unsafe' in value or '{' in value for value in paths))
        value = manifest()
        value['profiles'][0]['paths'].append('/unpublished')
        self.assertEqual(selected_profiles(config, manifest=value)[0]['paths'], [CHAT])

    def test_plan_and_external_policy_make_no_azure_calls(self):
        config = configuration()
        self.assertEqual(reconcile(config, None, manifest=manifest(), execute=False)['azureWrites'], 0)
        config['gateway']['existingWafPolicyId'] = '/external/policy'
        self.assertEqual(reconcile(config, None, manifest=manifest())['status'], 'external-policy-skipped')

    def test_basic_route_preserves_backend_rewrite_and_other_host_with_manifest_exclusions(self):
        transport = ArmMemory()
        before = copy.deepcopy(transport.resources[GATEWAY_ID])
        result = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(result['azureWrites'], 2)
        updated = transport.resources[GATEWAY_ID]
        mapping = api_mapping(updated)
        self.assertEqual(mapping['defaultRewriteRuleSet'], REWRITE)
        self.assertEqual(mapping['pathRules'][0]['properties'], {
            'paths': [CHAT], 'firewallPolicy': {'id': waf_policy_id(CONFIG, 'g-chat-completions')},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE})
        self.assertEqual(updated['properties']['urlPathMaps'][0], before['properties']['urlPathMaps'][0])
        for key in ('httpListeners', 'rewriteRuleSets', 'backendAddressPools', 'backendHttpSettingsCollection'):
            self.assertEqual(updated['properties'][key], before['properties'][key])
        configured = transport.resources[waf_policy_id(CONFIG, 'g-chat-completions')]['properties']
        self.assertEqual(configured['policySettings'], policy()['properties']['policySettings'])
        self.assertEqual(configured['customRules'], policy()['properties']['customRules'])
        selectors = {item['selector'] for item in configured['managedRules']['exclusions']}
        self.assertEqual(selectors, {'messages.content', 'Existing'})
        writes = [item for item in transport.calls if item[0] == 'PUT']
        self.assertEqual(writes[0][3], {'If-None-Match': '*'})
        self.assertEqual(writes[1][3], {'If-Match': '"gateway-v1"'})
        transport.calls.clear()
        again = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(again['azureWrites'], 0)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_splits_existing_data_path_rule_without_duplicate_path_or_lost_neighbor(self):
        source = gateway(path_based=True)
        media_id = waf_policy_id(CONFIG, 'g-chat-completions')
        media = copy.deepcopy(policy())
        media.update(id=media_id, name=policy_name(CONFIG, 'g-chat-completions'))
        media['properties']['managedRules']['exclusions'].append(manifest()['profiles'][0]['exclusions'][0])
        api_mapping(source)['pathRules'].append({'name': 'shared-data', 'properties': {
            'paths': [CHAT, '/other-model/chat/completions'], 'firewallPolicy': {'id': media_id},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE}})
        transport = ArmMemory(source, {media_id: media})
        value = manifest()
        value['profiles'][0]['name'] = 'g-responses'
        reconcile(configuration(), transport, scopes=('gateway',), manifest=value)
        rules = api_mapping(transport.resources[GATEWAY_ID])['pathRules']
        self.assertEqual(sum(CHAT in item['properties']['paths'] for item in rules), 1)
        old = next(item for item in rules if item['name'] == 'shared-data')
        self.assertEqual(old['properties']['paths'], ['/other-model/chat/completions'])
        self.assertEqual(reconcile(configuration(), transport, scopes=('gateway',), manifest=value)['azureWrites'], 0)

    def test_prevention_and_body_inspection_cannot_be_relaxed(self):
        for key, value in [('mode', 'Detection'), ('state', 'Disabled'), ('requestBodyCheck', False)]:
            base = policy()
            base['properties']['policySettings'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                desired_policy(base, manifest()['profiles'][0], configuration())

    def test_etag_conflict_stops_before_gateway_write(self):
        transport = ArmMemory()
        transport.conflict = True
        with self.assertRaisesRegex(ValueError, '412'):
            reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertFalse(any(method == 'PUT' and target == GATEWAY_ID for method, target, _, _ in transport.calls))

    def test_arm_subresource_ids_complete_polling_and_do_not_cause_a_second_put(self):
        transport = ArmMemory(subresource_metadata=True)
        first = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest(), timeout=1)
        self.assertTrue(first['gatewayUpdated'])
        self.assertEqual(first['azureWrites'], 2)
        transport.calls.clear()
        again = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest(), timeout=1)
        self.assertEqual(again['azureWrites'], 0)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_gateway_canonicalization_keeps_policy_backend_and_rewrite_reference_ids(self):
        source = gateway(path_based=True)
        before = copy.deepcopy(source)
        normalized = canonical_gateway(source)
        self.assertEqual(source, before)
        for key in ('firewallPolicy', 'backendAddressPool', 'backendHttpSettings', 'rewriteRuleSet'):
            changed = copy.deepcopy(source)
            rule = api_mapping(changed)['pathRules'][0]['properties']
            rule[key] = {'id': '/another/reference'}
            self.assertNotEqual(canonical_gateway(changed), normalized)

    def test_partial_profile_then_full_manifest_preserves_existing_path_order(self):
        config, value = configuration(), manifest()
        other = copy.deepcopy(value['profiles'][0])
        other.update(name='g-responses', paths=['/models/openai/v1/responses'])
        config['apiDefinitions'][0]['operations'].append(
            {'name': 'responses', 'method': 'POST', 'urlTemplate': '/responses'})
        config['apiDefinitions'][0]['authorization']['operationConstraints']['responses'] = {'kind': 'statelessResponses'}
        transport = ArmMemory(subresource_metadata=True)
        reconcile(config, transport, scopes=('gateway',), manifest=value, timeout=1)
        reconcile(config, transport, scopes=('gateway',), manifest={'version': 1, 'profiles': [other]}, timeout=1)
        before = copy.deepcopy(transport.resources[GATEWAY_ID])
        transport.calls.clear()
        value['profiles'].append(other)
        result = reconcile(config, transport, scopes=('gateway',), manifest=value, timeout=1)
        self.assertEqual(result['azureWrites'], 0)
        self.assertEqual(transport.resources[GATEWAY_ID], before)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_existing_exact_profile_moves_before_covering_wildcard_only_once(self):
        seed = ArmMemory()
        reconcile(configuration(), seed, scopes=('gateway',), manifest=manifest())
        source = copy.deepcopy(seed.resources[GATEWAY_ID])
        mapping = api_mapping(source)
        exact = mapping['pathRules'].pop(0)
        def route(name, paths):
            return {'name': name, 'properties': {
                'paths': paths, 'firewallPolicy': {'id': BASE_ID},
                'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE}}
        unrelated = route('unrelated', ['/other/*'])
        covering = route('covering', ['/MODELS/*'])
        trailing = route('trailing', ['/last/*'])
        mapping['pathRules'] = [unrelated, covering, exact, trailing]
        identifier = waf_policy_id(CONFIG, 'g-chat-completions')
        transport = ArmMemory(source, {identifier: seed.resources[identifier]})
        result = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(result['azureWrites'], 1)
        self.assertEqual(result['policyUpdates'], 0)
        self.assertTrue(result['gatewayUpdated'])
        rules = api_mapping(transport.resources[GATEWAY_ID])['pathRules']
        self.assertEqual(rules, [unrelated, exact, covering, trailing])
        transport.calls.clear()
        again = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(again['azureWrites'], 0)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))


    def _deadline_case(self, target_kind, final_state='Succeeded', mismatch=False):
        seed = ArmMemory()
        reconcile(configuration(), seed, scopes=('gateway',), manifest=manifest())
        policy_id = waf_policy_id(CONFIG, 'g-chat-completions')
        source = copy.deepcopy(seed.resources[GATEWAY_ID])
        existing = copy.deepcopy(seed.resources[policy_id])
        target = policy_id if target_kind == 'policy' else GATEWAY_ID
        if target_kind == 'policy':
            existing['properties']['managedRules']['exclusions'] = []
        else:
            mapping = api_mapping(source)
            mapping['pathRules'] = [item for item in mapping['pathRules'] if item['name'] != 'waf-data-g-chat-completions']
        clock = [0]

        class AtDeadline(ArmMemory):
            def __init__(self):
                super().__init__(source, {policy_id: existing})
                self.written = False
                self.after_put_get_times = []

            def request(self, method, path, body=None, headers=None):
                result = super().request(method, path, body, headers)
                identifier = path.split('?', 1)[0]
                if method == 'PUT' and identifier == target:
                    self.written = True
                elif method == 'GET' and identifier == target and self.written:
                    self.after_put_get_times.append(clock[0])
                    final = len(self.after_put_get_times) > 1
                    result['body']['properties']['provisioningState'] = final_state if final else 'Updating'
                    if final and mismatch:
                        if target_kind == 'policy':
                            result['body']['properties']['policySettings']['requestBodyCheck'] = False
                        else:
                            result['body']['properties']['backendHttpSettingsCollection'][0]['properties']['port'] = 81
                return result

        transport = AtDeadline()

        def sleep(seconds):
            clock[0] += seconds

        def run():
            return reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest(),
                             timeout=1, sleep=sleep, monotonic=lambda: clock[0])
        return transport, run

    def _assert_one_put_and_one_final_get(self, transport):
        self.assertEqual(len([item for item in transport.calls if item[0] == 'PUT']), 1)
        self.assertEqual(transport.after_put_get_times, [0, 10])

    def test_deadline_final_get_accepts_only_succeeded_and_exact_configuration(self):
        for target in ('policy', 'gateway'):
            with self.subTest(target=target):
                transport, run = self._deadline_case(target)
                self.assertEqual(run()['azureWrites'], 1)
                self._assert_one_put_and_one_final_get(transport)

    def test_deadline_final_get_still_rejects_configuration_mismatch(self):
        for target in ('policy', 'gateway'):
            with self.subTest(target=target):
                transport, run = self._deadline_case(target, mismatch=True)
                with self.assertRaisesRegex(ValueError, 'pending; provisioningState=Succeeded; configurationMismatch=true'):
                    run()
                self._assert_one_put_and_one_final_get(transport)

    def test_deadline_final_get_still_rejects_failed_and_canceled(self):
        for target in ('policy', 'gateway'):
            for state in ('Failed', 'Canceled'):
                with self.subTest(target=target, state=state):
                    transport, run = self._deadline_case(target, final_state=state)
                    with self.assertRaisesRegex(ValueError, 'failed; provisioningState=' + state):
                        run()
                    self._assert_one_put_and_one_final_get(transport)

    def test_deadline_diagnostics_keep_unknown_state_value_private(self):
        transport, run = self._deadline_case('gateway', final_state='synthetic-provider-detail')
        with self.assertRaisesRegex(ValueError, 'pending; provisioningState=Unknown; configurationMismatch=false') as raised:
            run()
        self.assertNotIn('synthetic-provider-detail', str(raised.exception))
        self._assert_one_put_and_one_final_get(transport)

    def test_external_path_policy_is_not_replaced(self):
        source = gateway(path_based=True)
        api_mapping(source)['pathRules'].insert(0, {'name': 'enterprise', 'properties': {
            'paths': [CHAT], 'firewallPolicy': {'id': '/external/policy'},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND}})
        transport = ArmMemory(source)
        with self.assertRaisesRegex(ValueError, 'external'):
            reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_policy_ownership_requires_an_exact_current_id(self):
        other_config = copy.deepcopy(CONFIG)
        other_config['services']['apimInstanceId'] = 'def456'
        external = [BASE_ID.replace('wafpol-g-base-', 'wafpol-g-unlisted-'),
                    BASE_ID.replace('wafpol-g-base-', 'wafpol-p-unlisted-'),
                    waf_policy_id(CONFIG, 'p-base'), waf_policy_id(other_config, 'g-chat-completions')]
        for identifier in external:
            source = gateway(path_based=True)
            api_mapping(source)['pathRules'].insert(0, {'name': 'unowned', 'properties': {
                'paths': [CHAT], 'firewallPolicy': {'id': identifier},
                'backendAddressPool': POOL, 'backendHttpSettings': BACKEND}})
            transport = ArmMemory(source)
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, 'external'):
                reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
            self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_owned_source_protection_differences_stop_before_writes(self):
        identifier = waf_policy_id(CONFIG, 'g-chat-completions')
        for change in ('settings', 'custom', 'overrides'):
            source = gateway(path_based=True)
            api_mapping(source)['pathRules'].insert(0, {'name': 'media', 'properties': {
                'paths': [CHAT], 'firewallPolicy': {'id': identifier},
                'backendAddressPool': POOL, 'backendHttpSettings': BACKEND}})
            current = policy()
            current.update(id=identifier, name=policy_name(CONFIG, 'g-chat-completions'))
            properties = current['properties']
            if change == 'settings':
                properties['policySettings']['requestBodyCheck'] = False
            elif change == 'custom':
                properties['customRules'] = []
            else:
                properties['managedRules']['managedRuleSets'][0]['ruleGroupOverrides'] = [{
                    'ruleGroupName': 'REQUEST-920-PROTOCOL-ENFORCEMENT',
                    'rules': [{'ruleId': '920420', 'state': 'Disabled'}]}]
            transport = ArmMemory(source, {identifier: current})
            value = manifest()
            value['profiles'][0]['name'] = 'g-responses'
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'differs|differ'):
                reconcile(configuration(), transport, scopes=('gateway',), manifest=value)
            self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_existing_field_alias_is_rejected_without_any_write(self):
        identifier = waf_policy_id(CONFIG, 'g-chat-completions')
        source = gateway(path_based=True)
        api_mapping(source)['pathRules'].insert(0, {'name': 'existing-data', 'properties': {
            'paths': [CHAT], 'firewallPolicy': {'id': identifier},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE}})
        current = policy()
        current.update(id=identifier, name=policy_name(CONFIG, 'g-chat-completions'))
        current['properties']['managedRules']['exclusions'].append(exclusion(
            'messages.content', 'REQUEST-942-APPLICATION-ATTACK-SQLI', ['942100'], 'RequestArgNames'))
        transport = ArmMemory(source, {identifier: current})
        value = manifest()
        value['profiles'][0]['name'] = 'g-responses'
        with self.assertRaisesRegex(ValueError, 'Represent the existing path-specific WAF exclusion'):
            reconcile(configuration(), transport, scopes=('gateway',), manifest=value)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_gateway_and_portal_merged_paths_each_use_one_policy_and_remain_idempotent(self):
        profiles = {p['name']: p for p in load_manifest()['profiles']}
        for name, scope in [('g-embeddings', 'gateway'), ('p-pii-redact', 'portal')]:
            config, source = configuration(), gateway()
            profile = profiles[name]
            config['apiDefinitions'] = [{'path': path.rsplit('/', 1)[0].lstrip('/'), 'operations': [{
                'name': 'operation', 'method': 'POST', 'urlTemplate': '/' + path.rsplit('/', 1)[1]}]}
                for path in profile['paths']]
            if scope == 'portal':
                config['portal'] = {'publicHostName': 'portal.example.test'}
                properties = source['properties']
                properties['httpListeners'].append({'name': 'portal-https', 'properties': {
                    'protocol': 'Https', 'hostName': 'portal.example.test',
                    'firewallPolicy': {'id': waf_policy_id(CONFIG, 'p-base')}}})
                properties['requestRoutingRules'][1]['properties']['httpListener'] = {
                    'id': GATEWAY_ID + '/httpListeners/portal-https'}
                mapping = properties['urlPathMaps'][0]['properties']
                pool = {'id': GATEWAY_ID + '/backendAddressPools/bepool-portal'}
                backend = {'id': GATEWAY_ID + '/backendHttpSettingsCollection/behttp-portal'}
                mapping.update(defaultBackendAddressPool=pool, defaultBackendHttpSettings=backend)
                mapping['pathRules'][0]['properties'].update(backendAddressPool=pool, backendHttpSettings=backend)
            transport = ArmMemory(source)
            value = {'version': 1, 'profiles': [profile]}
            with self.subTest(profile=name):
                first = reconcile(config, transport, scopes=(scope,), manifest=value)
                self.assertEqual(first['policyUpdates'], 1)
                identifier = waf_policy_id(config, name)
                self.assertEqual(transport.resources[identifier]['name'], policy_name(config, name))
                maps = transport.resources[GATEWAY_ID]['properties']['urlPathMaps']
                rules = [rule for item in maps for rule in item['properties']['pathRules']
                         if rule['name'] == 'waf-data-' + name]
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0]['properties']['paths'], profile['paths'])
                self.assertEqual(rules[0]['properties']['firewallPolicy'], {'id': identifier})
                transport.calls.clear()
                self.assertEqual(reconcile(config, transport, scopes=(scope,), manifest=value)['azureWrites'], 0)
                self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_portal_profile_keeps_gateway_and_auth_callback_routing(self):
        config, source = configuration(), gateway()
        config['portal'] = {'publicHostName': 'portal.example.test'}
        properties = source['properties']
        properties['httpListeners'].append({'name': 'portal-https', 'properties': {
            'protocol': 'Https', 'hostName': 'portal.example.test',
            'firewallPolicy': {'id': waf_policy_id(CONFIG, 'p-base')}}})
        properties['requestRoutingRules'][1]['properties']['httpListener'] = {'id': GATEWAY_ID + '/httpListeners/portal-https'}
        mapping = properties['urlPathMaps'][0]['properties']
        pool = {'id': GATEWAY_ID + '/backendAddressPools/bepool-portal'}
        backend = {'id': GATEWAY_ID + '/backendHttpSettingsCollection/behttp-portal'}
        mapping.update(defaultBackendAddressPool=pool, defaultBackendHttpSettings=backend)
        mapping['pathRules'][0]['properties'].update(backendAddressPool=pool, backendHttpSettings=backend)
        before = copy.deepcopy(source)
        value = manifest()
        value['profiles'][0].update(name='p-generate', scope='portal', paths=['/api/models/text'])
        transport = ArmMemory(source)
        reconcile(config, transport, scopes=('portal',), manifest=value)
        updated = transport.resources[GATEWAY_ID]
        self.assertEqual(updated['properties']['requestRoutingRules'][0], before['properties']['requestRoutingRules'][0])
        current = updated['properties']['urlPathMaps'][0]['properties']
        self.assertEqual(current['pathRules'][1:], mapping['pathRules'])
        self.assertEqual(current['pathRules'][0]['properties']['backendAddressPool'], pool)
        self.assertEqual(updated['properties']['httpListeners'], before['properties']['httpListeners'])
        self.assertEqual(transport.resources[BASE_ID], policy())
        exclusions = transport.resources[waf_policy_id(CONFIG, 'p-generate')]['properties']['managedRules']['exclusions']
        self.assertTrue(all(item in exclusions for item in portal_session_exclusions()))
        self.assertEqual(reconcile(config, transport, scopes=('portal',), manifest=value)['azureWrites'], 0)

    def test_images_share_only_the_approved_prompt_and_binary_payload_exclusions(self):
        profiles = load_manifest()['profiles']
        path = '/models/blackforestlabs/v1/flux-2/edits'
        selected = [profile for profile in profiles if path in profile['paths']]
        self.assertEqual(len(selected), 1)
        profile = selected[0]
        self.assertEqual(profile['scope'], 'gateway')
        self.assertEqual(profile['name'], 'g-images')
        self.assertEqual(len(profile['paths']), 6)
        image = [item for item in profile['exclusions'] if item['selector'] == 'input_image']
        self.assertEqual(image, [exclusion('input_image', 'REQUEST-942-APPLICATION-ATTACK-SQLI',
                                          ['942340', '942390', '942450'])])
        self.assertTrue(any(item['selector'] == 'prompt' for item in profile['exclusions']))
        self.assertFalse(any(item['selector'] == 'input_image' for other in profiles
                             if other is not profile for item in other['exclusions']))
        base = policy()
        desired = desired_policy(base, profile, configuration())
        self.assertEqual(desired['properties']['policySettings'], base['properties']['policySettings'])
        self.assertEqual(desired['properties']['customRules'], base['properties']['customRules'])
        self.assertIn(image[0], desired['properties']['managedRules']['exclusions'])

    def test_responses_function_arguments_keep_one_exact_sql_rule_across_generators(self):
        from bicep_helpers import compile_bicep
        from arm_helpers import PolicyExpression

        current = load_manifest(ROOT / 'apps/gateway/waf-data-exclusions.json')
        response_path = '/models/openai/v1/responses'
        profiles = [profile for profile in current['profiles'] if response_path in profile['paths']]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual((profile['scope'], profile['paths']), ('gateway', [response_path]))
        argument = [item for item in profile['exclusions'] if item['selector'] == 'input.arguments']
        self.assertEqual(len(argument), 1)
        self.assertEqual((argument[0]['matchVariable'], argument[0]['selectorMatchOperator']),
                         ('RequestArgValues', 'Equals'))
        rule_set = argument[0]['exclusionManagedRuleSets']
        self.assertEqual([(item['ruleSetType'], item['ruleSetVersion']) for item in rule_set], [('OWASP', '3.2')])
        sql = next(group for group in rule_set[0]['ruleGroups']
                   if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')
        self.assertEqual(sql['rules'].count({'ruleId': '942100'}), 1)
        self.assertFalse(any(item['ruleId'] in ('942110', '942450', '949110') for item in sql['rules']))
        self.assertFalse(any(item['selector'].startswith('input.arguments') and item is not argument[0]
                             for item in profile['exclusions']))
        old_profile = copy.deepcopy(profile)
        old_argument = next(item for item in old_profile['exclusions'] if item['selector'] == 'input.arguments')
        old_sql = next(group for group in old_argument['exclusionManagedRuleSets'][0]['ruleGroups']
                       if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')
        old_sql['rules'].remove({'ruleId': '942100'})
        base = policy()
        desired = desired_policy(base, profile, configuration())
        previous = desired_policy(base, old_profile, configuration())
        restored = copy.deepcopy(desired)
        restored_argument = next(item for item in restored['properties']['managedRules']['exclusions']
                                 if item['selector'] == 'input.arguments')
        next(group for group in restored_argument['exclusionManagedRuleSets'][0]['ruleGroups']
             if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')['rules'].remove({'ruleId': '942100'})
        self.assertEqual(restored, previous)
        self.assertEqual(desired['properties']['policySettings'], base['properties']['policySettings'])
        self.assertEqual(desired['properties']['customRules'], base['properties']['customRules'])
        compiled = compile_bicep(str(ROOT / 'infra/modules/application-gateway.bicep'))
        expanded = PolicyExpression("[variables('dataManifest')]", compiled['variables'],
                                    functions=compiled.get('functions', [])).evaluate({})
        self.assertEqual(expanded, current)

    def test_chat_serialized_function_arguments_keep_one_exact_sql_rule(self):
        current = load_manifest(ROOT / 'apps/gateway/waf-data-exclusions.json')
        profiles = [profile for profile in current['profiles'] if CHAT in profile['paths']]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual((profile['scope'], profile['paths']), ('gateway', [CHAT]))
        entries = [item for item in profile['exclusions'] if item['selector'] == 'messages.tool_calls.function.arguments']
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['matchVariable'], entries[0]['selectorMatchOperator']), ('RequestArgValues', 'Equals'))
        rule_sets = entries[0]['exclusionManagedRuleSets']
        self.assertEqual([(item['ruleSetType'], item['ruleSetVersion']) for item in rule_sets], [('OWASP', '3.2')])
        sql = next(group for group in rule_sets[0]['ruleGroups'] if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')
        self.assertEqual(sql['rules'].count({'ruleId': '942100'}), 1)
        self.assertFalse(any(item['ruleId'] in ('942110', '942450', '949110') for item in sql['rules']))
        previous_profile = copy.deepcopy(profile)
        old_entry = next(item for item in previous_profile['exclusions'] if item['selector'] == 'messages.tool_calls.function.arguments')
        next(group for group in old_entry['exclusionManagedRuleSets'][0]['ruleGroups']
             if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')['rules'].remove({'ruleId': '942100'})
        base = policy()
        desired = desired_policy(base, profile, configuration())
        reverted = copy.deepcopy(desired)
        reverted_entry = next(item for item in reverted['properties']['managedRules']['exclusions']
                              if item['selector'] == 'messages.tool_calls.function.arguments')
        next(group for group in reverted_entry['exclusionManagedRuleSets'][0]['ruleGroups']
             if group['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')['rules'].remove({'ruleId': '942100'})
        self.assertEqual(reverted, desired_policy(base, previous_profile, configuration()))
        self.assertEqual(desired['properties']['policySettings'], base['properties']['policySettings'])
        self.assertEqual(desired['properties']['customRules'], base['properties']['customRules'])

    def test_chat_function_schema_keys_keep_only_eight_exact_sql_rules(self):
        current = load_manifest(ROOT / 'apps/gateway/waf-data-exclusions.json')
        profiles = [profile for profile in current['profiles'] if CHAT in profile['paths']]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual((profile['scope'], profile['paths']), ('gateway', [CHAT]))
        entries = [item for item in profile['exclusions']
                   if item['matchVariable'] == 'RequestArgKeys' and item['selector'] == 'tools.function.parameters.']
        self.assertEqual(len(entries), 1)
        expected = exclusion('tools.function.parameters.', 'REQUEST-942-APPLICATION-ATTACK-SQLI',
                             ['942100', '942130', '942180', '942200', '942260', '942330', '942390', '942480'],
                             'RequestArgKeys')
        expected['selectorMatchOperator'] = 'StartsWith'
        self.assertEqual(entries, [expected])
        self.assertFalse(any(item['matchVariable'] == 'RequestArgKeys' and item['selector'].startswith('tools.function.parameters')
                             and item is not entries[0] for item in profile['exclusions']))
        previous = copy.deepcopy(profile)
        previous['exclusions'].remove(expected)
        base = policy()
        desired = desired_policy(base, profile, configuration())
        reverted = copy.deepcopy(desired)
        reverted['properties']['managedRules']['exclusions'].remove(expected)
        self.assertEqual(reverted, desired_policy(base, previous, configuration()))
        self.assertEqual(desired['properties']['policySettings'], base['properties']['policySettings'])
        self.assertEqual(desired['properties']['customRules'], base['properties']['customRules'])
        for other in current['profiles']:
            if other is profile:
                continue
            self.assertNotIn(expected, other['exclusions'])

    def test_responses_schema_property_names_keep_one_delimited_sql_exclusion(self):
        current = load_manifest(ROOT / 'apps/gateway/waf-data-exclusions.json')
        profiles = [profile for profile in current['profiles']
                    if '/models/openai/v1/responses' in profile['paths']]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual((profile['scope'], profile['paths']),
                         ('gateway', ['/models/openai/v1/responses']))
        entries = [item for item in profile['exclusions']
                   if item['matchVariable'] == 'RequestArgKeys' and item['selector'] == 'text.format.schema.']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['selectorMatchOperator'], 'StartsWith')
        self.assertEqual([(item['ruleSetType'], item['ruleSetVersion'])
                          for item in entries[0]['exclusionManagedRuleSets']], [('OWASP', '3.2')])
        sql = next(item for item in entries[0]['exclusionManagedRuleSets'][0]['ruleGroups']
                   if item['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')
        self.assertEqual(sql['rules'].count({'ruleId': '942100'}), 1)
        self.assertNotIn({'ruleId': '949110'}, sql['rules'])
        previous_profile = copy.deepcopy(profile)
        old_entry = next(item for item in previous_profile['exclusions']
                         if item['matchVariable'] == 'RequestArgKeys' and item['selector'] == 'text.format.schema.')
        next(item for item in old_entry['exclusionManagedRuleSets'][0]['ruleGroups']
             if item['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')['rules'].remove({'ruleId': '942100'})
        base = policy()
        desired = desired_policy(base, profile, configuration())
        reverted = copy.deepcopy(desired)
        reverted_entry = next(item for item in reverted['properties']['managedRules']['exclusions']
                              if item['matchVariable'] == 'RequestArgKeys' and item['selector'] == 'text.format.schema.')
        next(item for item in reverted_entry['exclusionManagedRuleSets'][0]['ruleGroups']
             if item['ruleGroupName'] == 'REQUEST-942-APPLICATION-ATTACK-SQLI')['rules'].remove({'ruleId': '942100'})
        self.assertEqual(reverted, desired_policy(base, previous_profile, configuration()))
        self.assertEqual(desired['properties']['policySettings'], base['properties']['policySettings'])
        self.assertEqual(desired['properties']['customRules'], base['properties']['customRules'])

    def test_compiled_manifest_retains_declared_protection_and_uses_one_rule_per_exact_path(self):
        from bicep_helpers import bicep_executable
        from test_ssml_waf import GatewayExpression

        class CompiledExpression(GatewayExpression):
            def resolve(self, item, parameters, index=0):
                if isinstance(item, str) and item.startswith('[') and item.endswith(']'):
                    return CompiledExpression(item, self.variables).evaluate(parameters, index)
                if isinstance(item, dict): return {key: self.resolve(value, parameters, index) for key, value in item.items()}
                if isinstance(item, list): return [self.resolve(value, parameters, index) for value in item]
                return item

            def evaluate(self, parameters, index=0, tree=None, bindings=None):
                selected = self.tree if tree is None else tree
                if selected[0] == 'call' and selected[1] == 'union':
                    values = [self.evaluate(parameters, index, item, bindings) for item in selected[2]]
                    if isinstance(values[0], list):
                        result = []
                        for values_list in values:
                            for item in values_list:
                                if item not in result: result.append(item)
                        return result
                return super().evaluate(parameters, index, tree, bindings)

        sample = manifest()
        sample['profiles'][0]['exclusions'].append(exclusion(
            'messages.content.image_url.url', 'REQUEST-941-APPLICATION-ATTACK-XSS', ['941130', '941170']))
        sample['profiles'][0]['paths'].append('/unpublished')
        sample['profiles'].append({**copy.deepcopy(sample['profiles'][0]), 'name': 'p-generate',
                                   'scope': 'portal', 'paths': ['/api/models/text']})
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = (ROOT / 'infra/modules/application-gateway.bicep').read_text(encoding='utf-8')
            source = source.replace("loadJsonContent('../../apps/gateway/waf-data-exclusions.json')", "loadJsonContent('./manifest.json')")
            source = source.replace("loadJsonContent('../../apps/gateway/waf-raw-body-controls.json')", "loadJsonContent('./raw-controls.json')")
            source = source.replace("loadJsonContent('../../apps/portal/waf-session-exclusions.json')", "loadJsonContent('./portal-session.json')")
            (folder / 'gateway.bicep').write_text(source, encoding='utf-8')
            (folder / 'manifest.json').write_text(json.dumps(sample), encoding='utf-8')
            (folder / 'raw-controls.json').write_text((ROOT / 'apps/gateway/waf-raw-body-controls.json').read_text(encoding='utf-8'), encoding='utf-8')
            (folder / 'portal-session.json').write_text(json.dumps(portal_session_exclusions()), encoding='utf-8')
            built = subprocess.run([bicep_executable(), 'build', str(folder / 'gateway.bicep'), '--stdout', '--no-restore'],
                                   env={**os.environ, 'DOTNET_BUNDLE_EXTRACT_BASE_DIR': str(folder)},
                                   capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            template = json.loads(built.stdout)
        variables = copy.deepcopy(template['variables'])
        parameters = {key: copy.deepcopy(item.get('defaultValue', 'fixture')) for key, item in template['parameters'].items()}
        parameters.update(resourceStem='test-fixture', wafPolicyIdentifier='abc123', location='japaneast', publishedApiPaths=[CHAT],
                          grpcEnabled=True,
                          wafCustomRules=[{'name': 'retain-custom', 'priority': 1, 'action': 'Block'}],
                          wafExclusions=[{'matchVariable': 'RequestHeaderValues', 'selectorMatchOperator': 'Equals', 'selector': 'Existing'}])
        evaluate = lambda item, index=0: CompiledExpression('[true()]', variables).resolve(item, parameters, index)
        for variable in variables.get('copy', []):
            variables[variable['name']] = [evaluate(variable['input'], index) for index in range(evaluate(variable['count']))]
        profiles = evaluate("[variables('dataProfiles')]")
        self.assertEqual(profiles[0]['paths'], [CHAT])
        resources = template['resources']
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        resource = next(item for item in resources if item['type'] == 'Microsoft.Network/applicationGateways')
        properties = CompiledExpression("[true()]", variables).resolve(resource['properties'], parameters)
        mapping = next(item['properties'] for item in properties['urlPathMaps'] if item['name'] == 'pathmap-protocols')
        self.assertEqual(sum(CHAT in item['properties']['paths'] for item in mapping['pathRules']), 1)
        configured = variables['dataPolicyProperties'][0]
        self.assertEqual(configured['policySettings']['mode'], 'Prevention')
        self.assertTrue(configured['policySettings']['requestBodyCheck'])
        self.assertEqual(configured['customRules'], parameters['wafCustomRules'])
        self.assertEqual(configured, desired_policy({**policy(), 'properties': {**policy()['properties'],
            'policySettings': configured['policySettings'], 'customRules': parameters['wafCustomRules'],
            'managedRules': {'managedRuleSets': configured['managedRules']['managedRuleSets'],
                             'exclusions': parameters['wafExclusions']}}}, profiles[0], configuration())['properties'])

    def test_main_finite_paths_match_python_including_deployments_and_generated_mcp(self):
        from arm_helpers import PolicyExpression
        from bicep_helpers import compile_bicep
        from standard_apis import infrastructure_api_definitions

        class MainExpression(PolicyExpression):
            def evaluate(self, parameters, index=0, tree=None, bindings=None):
                selected = self.tree if tree is None else tree
                if selected[0] == 'call' and selected[1] == 'concat':
                    return [value for item in selected[2]
                            for value in self.evaluate(parameters, index, item, bindings)]
                return super().evaluate(parameters, index, tree, bindings)

        template = compile_bicep('infra/main.bicep')
        resources = template['resources']
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        module = next(item for item in resources if 'publishedApiPaths' in item.get('properties', {}).get('parameters', {}))
        expression = module['properties']['parameters']['publishedApiPaths']['value']
        config = {'apiDefinitions': [], 'infrastructureApiDefinitions': infrastructure_api_definitions([])}
        actual = MainExpression(expression, template['variables']).evaluate({'configuration': config})
        self.assertEqual(set(actual), published_paths({'apiDefinitions': []}))
        self.assertIn('/tools/pii-v1-mcp/mcp', actual)
        self.assertFalse(any('{' in value for value in actual))


if __name__ == '__main__':
    unittest.main()
