"""Verify the two model-prefix WAF routes without widening APIM operations."""
import copy
import fnmatch
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
from tests import REPOSITORY

ROOT = REPOSITORY
from tests.support.arm_helpers import PolicyExpression
from tests.support.bicep_helpers import compile_bicep
from deployment.helpers import waf_body
from deployment.helpers.waf_names import policy_id, policy_name
from tests.waf.test_gateway_callback_waf import CONFIG, GATEWAY_ID, POOL, BACKEND, REWRITE, gateway, policy
from tests.waf.test_waf_policy import ArmMemory
from validation.waf_contracts import MODEL_WAF_PATHS, model_operation_waf_path, require_source_routes
from operations.waf_policy import canonical_gateway, canonical_policy, desired_policy, load_manifest, path_patterns_overlap, published_paths, reconcile


def contracts():
    return [json.loads((ROOT / 'apps/api-gateway/apis' / (name + '.json')).read_text(encoding='utf-8'))
            for name in ('openai-images-v1', 'openai-audio-v1')]


class ModelWafRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()
        cls.profiles = [p for p in cls.manifest['profiles'] if p['name'] in MODEL_WAF_PATHS]
        cls.main = compile_bicep('infra/main.bicep')
        cls.expression = cls.main['variables']['gatewayPlan']['publishedApiPaths']

    def test_only_the_two_formal_operations_publish_model_prefixes_in_python_and_main(self):
        expected = {'openai-images-v1': ('image-edit', 'g-images'),
                    'openai-audio-v1': ('audio-transcriptions', 'g-transcribe')}
        for api in contracts():
            op_name, profile = expected[api['name']]
            operation = next(o for o in api['operations'] if o['name'] == op_name)
            constraint = api['authorization']['operationConstraints'][op_name]
            self.assertEqual(model_operation_waf_path(api, operation, constraint), MODEL_WAF_PATHS[profile])
            for field, value in (('standard', None), ('name', 'synthetic-other-api'), ('path', api['path'] + '-neighbor'),
                                 ('apiType', 'mcp'), ('operation', 'synthetic-other-operation'), ('method', 'GET'),
                                 ('urlTemplate', operation['urlTemplate'] + '/other'), ('kind', 'registeredOperation'),
                                 ('bodyFormat', 'json'), ('parameter', 'modelName')):
                modified = copy.deepcopy(api)
                selected_op = next(o for o in modified['operations'] if o['name'] == op_name)
                selected_constraint = modified['authorization']['operationConstraints'][op_name]
                if field in ('name', 'path', 'apiType'): modified[field] = value
                elif field == 'operation':
                    selected_op['name'] = value
                    modified['authorization']['operationConstraints'][value] = selected_constraint
                elif field in ('method', 'urlTemplate'): selected_op[field] = value
                elif field != 'standard': selected_constraint[field] = value
                config = {'apiDefinitions': [modified]}
                actual = PolicyExpression(self.expression, self.main['variables'], functions=self.main.get('functions', [])).evaluate({'configuration': config})
                with self.subTest(api=api['name'], mutation=field):
                    self.assertEqual(set(actual), published_paths(config))
                    self.assertEqual(MODEL_WAF_PATHS[profile] in actual, field == 'standard')

    def test_model_metadata_does_not_change_waf_paths_and_apim_verbs_and_suffixes_remain_exact(self):
        before = published_paths({'apiDefinitions': contracts()})
        changed = contracts()
        for api in changed:
            for constraint in api['authorization']['operationConstraints'].values():
                if constraint.get('kind') == 'pathModel': constraint['allowedModels'] = ['synthetic-new-model']
        self.assertEqual(published_paths({'apiDefinitions': changed}), before)
        self.assertEqual({p['name']: p['paths'] for p in require_source_routes(changed)['profiles'] if p['name'] in MODEL_WAF_PATHS},
                         {p['name']: p['paths'] for p in require_source_routes(contracts())['profiles'] if p['name'] in MODEL_WAF_PATHS})
        for api in contracts():
            operation = next(o for o in api['operations'] if '{deployment}' in o['urlTemplate'])
            prefix = '/' + api['path']
            compiled = re.compile(re.escape(prefix + operation['urlTemplate']).replace(re.escape('{deployment}'), '[^/]+') + r'\Z')
            model_path = prefix + operation['urlTemplate'].replace('{deployment}', 'synthetic-new-model')
            pattern = next(value for value in MODEL_WAF_PATHS.values() if model_path.startswith(value[:-1]))
            self.assertTrue(fnmatch.fnmatchcase(model_path, pattern))
            self.assertTrue(compiled.fullmatch(model_path))
            for method, path in (('GET', model_path), ('POST', model_path + '/extra'),
                                 ('POST', model_path.rsplit('/', 1)[0] + '/other'),
                                 ('POST', model_path.replace('synthetic-new-model', 'synthetic-new-model/extra'))):
                with self.subTest(method=method, path=path):
                    self.assertTrue(fnmatch.fnmatchcase(path, pattern))
                    self.assertFalse(method == operation['method'] and compiled.fullmatch(path))

    def test_new_independent_operations_and_mcp_routes_under_model_prefix_require_review(self):
        for kind in ('operation', 'nested-api', 'mcp'):
            apis = contracts()
            if kind == 'operation':
                apis[0]['operations'].append({'name': 'synthetic-status', 'method': 'GET', 'urlTemplate': '/deployments/{deployment}/status'})
            elif kind == 'nested-api':
                apis.append({'name': 'synthetic-nested-api', 'path': 'models/openai-images/v1/deployments/synthetic-admin',
                             'operations': [{'name': 'invoke', 'method': 'POST', 'urlTemplate': '/status'}]})
            else:
                apis.append({'name': 'synthetic-mcp', 'path': 'custom/synthetic', 'operations': [],
                             'mcp': {'path': 'models/openai-audio/v1/deployments/synthetic-tools'}})
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                require_source_routes(apis)

    def test_model_prefix_intersection_distinguishes_parent_exact_from_trailing_slash_and_children(self):
        for suffix, overlaps in (('/deployments', False), ('/deployments-neighbor/{id}/status', False),
                                 ('/deployments/', True), ('/deployments/{deployment}/status', True)):
            apis = contracts() + [{'name': 'synthetic-parent-boundary', 'path': 'models/openai-images/v1',
                                  'operations': [{'name': 'synthetic-invoke', 'method': 'POST', 'urlTemplate': suffix}]}]
            with self.subTest(suffix=suffix):
                if overlaps:
                    with self.assertRaises(ValueError): require_source_routes(apis)
                else:
                    require_source_routes(apis)

    def fixture(self):
        all_apis = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((ROOT / 'apps/api-gateway/apis').glob('*.json'))]
        config = {**copy.deepcopy(CONFIG), 'apiDefinitions': all_apis}
        source = gateway(path_based=True)
        mapping = next(m['properties'] for m in source['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')
        policies = {}
        controls = json.loads(waf_body.JSON_CONTROLS.read_text(encoding='utf-8'))
        prior_profiles = []
        for profile in self.profiles:
            prior = copy.deepcopy(profile)
            api_name = 'openai-images-v1' if profile['name'] == 'g-images' else 'openai-audio-v1'
            api = next(a for a in config['apiDefinitions'] if a['name'] == api_name)
            operation = next(o for o in api['operations'] if '{deployment}' in o['urlTemplate'])
            constraint = api['authorization']['operationConstraints'][operation['name']]
            finite = ['/' + api['path'] + operation['urlTemplate'].replace('{deployment}', model) for model in constraint['allowedModels']]
            prior['paths'] = [p for p in prior['paths'] if p != MODEL_WAF_PATHS[profile['name']]] + finite
            prior_profiles.append(prior)
            for control in controls['profiles']:
                if control['name'] == profile['name']: control['paths'] = prior['paths']
            mapping['pathRules'].append({'name': 'waf-data-' + profile['name'], 'properties': {
                'paths': prior['paths'], 'firewallPolicy': {'id': policy_id(config, profile['name'])},
                'backendAddressPool': copy.deepcopy(POOL), 'backendHttpSettings': copy.deepcopy(BACKEND),
                'rewriteRuleSet': copy.deepcopy(REWRITE)}})
        with tempfile.TemporaryDirectory(prefix='waf-model-prior-controls-') as folder:
            target = Path(folder) / 'synthetic-content-controls.json'
            target.write_text(json.dumps(controls), encoding='utf-8')
            with mock.patch.object(waf_body, 'JSON_CONTROLS', target):
                for profile in prior_profiles:
                    desired = desired_policy(policy(), profile, config)
                    desired.update(id=policy_id(config, profile['name']), name=policy_name(config, profile['name']), etag='"synthetic-prior"')
                    desired['properties']['provisioningState'] = 'Succeeded'
                    policies[desired['id']] = desired
        return config, source, policies

    def test_reconcile_changes_only_owned_paths_and_preserves_waf_policy_content(self):
        config, source, policies = self.fixture()
        before = canonical_gateway(source)
        transport = ArmMemory(copy.deepcopy(source), copy.deepcopy(policies))
        result = reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': self.profiles})
        self.assertEqual((result['policyUpdates'], result['azureWrites']), (0, 1))
        after = canonical_gateway(transport.resources[GATEWAY_ID])
        for map_value in before['properties']['urlPathMaps']:
            for rule in map_value['properties']['pathRules']:
                if rule['name'] in {'waf-data-' + name for name in MODEL_WAF_PATHS}:
                    name = rule['name'][len('waf-data-'):]
                    rule['properties']['paths'] = next(p['paths'] for p in self.profiles if p['name'] == name)
        self.assertEqual(after, before)
        for identifier, previous in policies.items():
            self.assertEqual(canonical_policy(transport.resources[identifier]), canonical_policy(previous))
        transport.calls.clear()
        self.assertEqual(reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': self.profiles})['azureWrites'], 0)
        self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_overlap_is_case_insensitive_and_unknown_pattern_grammar_fails(self):
        pattern = MODEL_WAF_PATHS['g-images']
        for other, expected in ((pattern.upper(), True), (pattern[:-1] + 'new-model/edits', True),
                                (pattern[:-1] + 'new-model/*', True), ('/models/openai-images/v1/*', True),
                                ('/models/openai-images/v1/deployments-neighbor/*', False),
                                (MODEL_WAF_PATHS['g-transcribe'], False)):
            with self.subTest(other=other):
                self.assertEqual(path_patterns_overlap(pattern, other), expected)
                self.assertEqual(path_patterns_overlap(other, pattern), expected)
        for unknown in ('/models/*/edits', '/models/**', '/models/*/edits/*', 'relative'):
            with self.subTest(pattern=unknown), self.assertRaises(ValueError):
                path_patterns_overlap(pattern, unknown)

    def test_independent_or_external_overlaps_stop_before_any_put(self):
        for path in (MODEL_WAF_PATHS['g-images'][:-1] + 'synthetic-admin/status',
                     MODEL_WAF_PATHS['g-images'][:-1] + 'synthetic-admin/*', '/models/openai-images/v1/*'):
            for change in ('policy', 'backend', 'rewrite'):
                config, source, policies = self.fixture()
                mapping = next(m['properties'] for m in source['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')
                properties = {'paths': [path], 'backendAddressPool': copy.deepcopy(POOL), 'backendHttpSettings': copy.deepcopy(BACKEND),
                              'rewriteRuleSet': copy.deepcopy(REWRITE), 'firewallPolicy': {'id': policy_id(config, 'g-base')}}
                if change == 'policy': properties['firewallPolicy'] = {'id': '/synthetic-external-policy'}
                elif change == 'backend': properties['backendHttpSettings'] = {'id': '/synthetic-other-backend'}
                else: properties['rewriteRuleSet'] = {'id': '/synthetic-other-rewrite'}
                mapping['pathRules'].insert(0, {'name': 'synthetic-independent', 'properties': properties})
                transport = ArmMemory(source, policies)
                with self.subTest(path=path, change=change), self.assertRaisesRegex(ValueError, 'Model WAF prefix overlaps an independent path rule'):
                    reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': self.profiles})
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_default_equivalent_parent_route_is_retained_behind_the_model_prefix(self):
        config, source, policies = self.fixture()
        mapping = next(m['properties'] for m in source['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')
        parent = {'name': 'synthetic-default-parent', 'properties': {'paths': ['/models/openai-images/v1/*'],
                  'backendAddressPool': copy.deepcopy(POOL), 'backendHttpSettings': copy.deepcopy(BACKEND),
                  'rewriteRuleSet': copy.deepcopy(REWRITE), 'firewallPolicy': {'id': policy_id(config, 'g-base')}}}
        mapping['pathRules'].insert(0, copy.deepcopy(parent))
        transport = ArmMemory(source, policies)
        reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': self.profiles})
        updated = next(m['properties'] for m in transport.resources[GATEWAY_ID]['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')['pathRules']
        self.assertEqual(next(r for r in updated if r['name'] == parent['name']), parent)
        self.assertLess(next(i for i, r in enumerate(updated) if r['name'] == 'waf-data-g-images'),
                        next(i for i, r in enumerate(updated) if r['name'] == parent['name']))

    def test_same_pattern_default_rule_and_owned_route_drift_stop_before_any_put(self):
        for change in ('same-pattern', 'missing-rewrite', 'outside-owned-path'):
            config, source, policies = self.fixture()
            mapping = next(m['properties'] for m in source['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')
            owned = next(r['properties'] for r in mapping['pathRules'] if r['name'] == 'waf-data-g-images')
            if change == 'same-pattern':
                other = copy.deepcopy(owned)
                other.update(paths=[MODEL_WAF_PATHS['g-images']], firewallPolicy={'id': policy_id(config, 'g-base')})
                mapping['pathRules'].insert(0, {'name': 'synthetic-same-pattern-default', 'properties': other})
            elif change == 'missing-rewrite': owned.pop('rewriteRuleSet')
            else: owned['paths'].append('/synthetic-owned-neighbor')
            transport = ArmMemory(source, policies)
            with self.subTest(change=change), self.assertRaises(ValueError):
                reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': self.profiles})
            self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_earlier_owned_profile_cannot_remove_a_conflict_before_model_prefix_preflight(self):
        config, source, policies = self.fixture()
        earlier = next(p for p in self.manifest['profiles'] if p['name'] == 'g-chat-completions')
        mapping = next(m['properties'] for m in source['properties']['urlPathMaps'] if m['name'] == 'pathmap-protocols')
        mapping['pathRules'].insert(0, {'name': 'waf-data-g-chat-completions', 'properties': {
            'paths': [*earlier['paths'], MODEL_WAF_PATHS['g-images'][:-1] + 'synthetic-admin/status'],
            'firewallPolicy': {'id': policy_id(config, earlier['name'])}, 'backendAddressPool': copy.deepcopy(POOL),
            'backendHttpSettings': copy.deepcopy(BACKEND), 'rewriteRuleSet': copy.deepcopy(REWRITE)}})
        transport = ArmMemory(source, policies)
        with self.assertRaisesRegex(ValueError, 'Model WAF prefix overlaps an independent path rule'):
            reconcile(config, transport, scopes=('gateway',), manifest={'version': 2, 'profiles': [earlier, *self.profiles]})
        self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))


if __name__ == '__main__':
    unittest.main()
