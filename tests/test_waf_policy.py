"""Data exclusions retain Prevention and remain confined to finite published paths."""
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from waf_policy import exclusion, load_manifest, selected_profiles, published_paths, desired_policy, reconcile, canonical_gateway
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
    return {'version': 1, 'profiles': [{'name': 'chat-text', 'scope': 'gateway', 'paths': [CHAT],
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
    def test_manifest_rejects_wildcard_duplicate_scope_and_unscoped_rules(self):
        for change in ('path', 'duplicate', 'global', 'aggregate', 'selector'):
            value = manifest()
            item = value['profiles'][0]
            if change == 'path': item['paths'] = ['/models/*']
            elif change == 'duplicate': value['profiles'].append({**copy.deepcopy(item), 'name': 'another'})
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

    def test_basic_route_preserves_backend_rewrite_and_other_host_with_media_exclusions(self):
        transport = ArmMemory()
        before = copy.deepcopy(transport.resources[GATEWAY_ID])
        result = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(result['azureWrites'], 2)
        updated = transport.resources[GATEWAY_ID]
        mapping = api_mapping(updated)
        self.assertEqual(mapping['defaultRewriteRuleSet'], REWRITE)
        self.assertEqual(mapping['pathRules'][0]['properties'], {
            'paths': [CHAT], 'firewallPolicy': {'id': BASE_ID + '-data-chat-text'},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE})
        self.assertEqual(updated['properties']['urlPathMaps'][0], before['properties']['urlPathMaps'][0])
        for key in ('httpListeners', 'rewriteRuleSets', 'backendAddressPools', 'backendHttpSettingsCollection'):
            self.assertEqual(updated['properties'][key], before['properties'][key])
        configured = transport.resources[BASE_ID + '-data-chat-text']['properties']
        self.assertEqual(configured['policySettings'], policy()['properties']['policySettings'])
        self.assertEqual(configured['customRules'], policy()['properties']['customRules'])
        selectors = {item['selector'] for item in configured['managedRules']['exclusions']}
        self.assertTrue({'messages.content', 'messages.content.image_url.url', 'messages.content.input_audio.data', 'Existing'} <= selectors)
        writes = [item for item in transport.calls if item[0] == 'PUT']
        self.assertEqual(writes[0][3], {'If-None-Match': '*'})
        self.assertEqual(writes[1][3], {'If-Match': '"gateway-v1"'})
        transport.calls.clear()
        again = reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertEqual(again['azureWrites'], 0)
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_splits_existing_media_path_rule_without_duplicate_path_or_lost_neighbor(self):
        source = gateway(path_based=True)
        media_id = BASE_ID + '-inline-media'
        media = copy.deepcopy(policy())
        media.update(id=media_id, name='media')
        media['properties']['managedRules']['exclusions'].append(
            exclusion('messages.content.image_url.url', 'REQUEST-941-APPLICATION-ATTACK-XSS', ['941130', '941170']))
        media['properties']['managedRules']['exclusions'].append(
            exclusion('messages.content.input_audio.data', 'REQUEST-942-APPLICATION-ATTACK-SQLI', ['942450'], 'RequestArgNames'))
        api_mapping(source)['pathRules'].append({'name': 'old-media', 'properties': {
            'paths': [CHAT, '/other-model/chat/completions'], 'firewallPolicy': {'id': media_id},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE}})
        transport = ArmMemory(source, {media_id: media})
        reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        rules = api_mapping(transport.resources[GATEWAY_ID])['pathRules']
        self.assertEqual(sum(CHAT in item['properties']['paths'] for item in rules), 1)
        old = next(item for item in rules if item['name'] == 'old-media')
        self.assertEqual(old['properties']['paths'], ['/other-model/chat/completions'])
        self.assertEqual(reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())['azureWrites'], 0)

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
        other.update(name='second-text', paths=['/models/openai/v1/responses'])
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

    def test_external_path_policy_is_not_replaced(self):
        source = gateway(path_based=True)
        api_mapping(source)['pathRules'].insert(0, {'name': 'enterprise', 'properties': {
            'paths': [CHAT], 'firewallPolicy': {'id': '/external/policy'},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND}})
        transport = ArmMemory(source)
        with self.assertRaisesRegex(ValueError, 'external'):
            reconcile(configuration(), transport, scopes=('gateway',), manifest=manifest())
        self.assertFalse(any(item[0] == 'PUT' for item in transport.calls))

    def test_portal_profile_keeps_gateway_and_auth_callback_routing(self):
        config, source = configuration(), gateway()
        config['portal'] = {'publicHostName': 'portal.example.test'}
        properties = source['properties']
        properties['httpListeners'].append({'name': 'portal-https', 'properties': {
            'protocol': 'Https', 'hostName': 'portal.example.test'}})
        properties['requestRoutingRules'][1]['properties']['httpListener'] = {'id': GATEWAY_ID + '/httpListeners/portal-https'}
        mapping = properties['urlPathMaps'][0]['properties']
        pool = {'id': GATEWAY_ID + '/backendAddressPools/bepool-portal'}
        backend = {'id': GATEWAY_ID + '/backendHttpSettingsCollection/behttp-portal'}
        mapping.update(defaultBackendAddressPool=pool, defaultBackendHttpSettings=backend)
        mapping['pathRules'][0]['properties'].update(backendAddressPool=pool, backendHttpSettings=backend)
        before = copy.deepcopy(source)
        value = manifest()
        value['profiles'][0].update(name='portal-text', scope='portal', paths=['/api/models/text'])
        transport = ArmMemory(source)
        reconcile(config, transport, scopes=('portal',), manifest=value)
        updated = transport.resources[GATEWAY_ID]
        self.assertEqual(updated['properties']['requestRoutingRules'][0], before['properties']['requestRoutingRules'][0])
        current = updated['properties']['urlPathMaps'][0]['properties']
        self.assertEqual(current['pathRules'][1:], mapping['pathRules'])
        self.assertEqual(current['pathRules'][0]['properties']['backendAddressPool'], pool)
        self.assertEqual(reconcile(config, transport, scopes=('portal',), manifest=value)['azureWrites'], 0)

    def test_compiled_manifest_retains_media_protection_and_uses_one_rule_per_exact_path(self):
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
        sample['profiles'][0]['paths'].append('/unpublished')
        sample['profiles'].append({**copy.deepcopy(sample['profiles'][0]), 'name': 'portal-text',
                                   'scope': 'portal', 'paths': ['/api/models/text']})
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = (ROOT / 'infra/modules/application-gateway.bicep').read_text(encoding='utf-8')
            source = source.replace("loadJsonContent('../../apps/gateway/waf-data-exclusions.json')", "loadJsonContent('./manifest.json')")
            source = source.replace("loadJsonContent('../../apps/gateway/waf-raw-body-controls.json')", "loadJsonContent('./raw-controls.json')")
            (folder / 'gateway.bicep').write_text(source, encoding='utf-8')
            (folder / 'manifest.json').write_text(json.dumps(sample), encoding='utf-8')
            (folder / 'raw-controls.json').write_text((ROOT / 'apps/gateway/waf-raw-body-controls.json').read_text(encoding='utf-8'), encoding='utf-8')
            built = subprocess.run([bicep_executable(), 'build', str(folder / 'gateway.bicep'), '--stdout', '--no-restore'],
                                   capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            template = json.loads(built.stdout)
        variables = copy.deepcopy(template['variables'])
        parameters = {key: copy.deepcopy(item.get('defaultValue', 'fixture')) for key, item in template['parameters'].items()}
        parameters.update(resourceStem='test-fixture', location='japaneast', publishedApiPaths=[CHAT],
                          inlineImagePaths=[CHAT], inlineAudioPaths=[CHAT], grpcEnabled=True,
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
        self.assertFalse(any(item['name'] == 'inline-media-data' for item in mapping['pathRules']))
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
