"""Reviewed wire types, prior-scope containment and model-independent rendering."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from waf_contracts import (APP_GROUPS, CATALOG, MANIFEST, approved_group_scopes,
                           check_legacy_coverage, contract_fields, contract_inventory,
                           generated_manifest, load_contracts, render, require_source_routes)


def common_data_before_manifest():
    """Expand the frozen pre-change oracle without consulting the current catalog."""
    value = json.loads((ROOT / 'tests/fixtures/waf-common-data-before.json').read_text())
    profiles = []
    for source in value['profiles']:
        profile = {key: source[key] for key in ('name', 'scope', 'paths')}
        profile['exclusions'] = [
            {'matchVariable': entry['variable'], 'selectorMatchOperator': entry['operator'],
             'selector': entry['selector'], 'exclusionManagedRuleSets': [{
                 'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2', 'ruleGroups': [
                     {'ruleGroupName': name, **({} if rules is None else {
                         'rules': [{'ruleId': rule} for rule in rules]})}
                     for name, rules in entry['groups'].items()]}]}
            for entry in source['entries']]
        profiles.append(profile)
    return {'version': 2, 'profiles': profiles}


class WireContractTests(unittest.TestCase):
    def tree(self, folder):
        root = Path(folder)
        for name in (CATALOG, MANIFEST, 'apps/gateway/apim/public-bindings.json'):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        source = ROOT / 'apps/gateway/apim/apis'
        shutil.copytree(source, root / 'apps/gateway/apim/apis')
        return root

    def test_static_bicep_asset_is_the_exact_deterministic_rendering(self):
        self.assertEqual(render(check=True), generated_manifest())

    def test_every_old_route_field_variable_and_rule_scope_is_contained(self):
        before = json.loads((ROOT / 'tests/fixtures/waf-legacy-coverage.json').read_text())
        self.assertEqual(check_legacy_coverage(before, generated_manifest()), [])

    def test_prior_coverage_oracle_detects_omitted_selector_and_rule(self):
        before = json.loads((ROOT / 'tests/fixtures/waf-legacy-coverage.json').read_text())
        value = generated_manifest()
        value['profiles'][0]['exclusions'].clear()
        self.assertTrue(check_legacy_coverage(before, value))

    def test_prior_coverage_oracle_detects_omitted_route(self):
        before = json.loads((ROOT / 'tests/fixtures/waf-legacy-coverage.json').read_text())
        value = generated_manifest()
        value['profiles'][0]['paths'].append('/unrelated')
        self.assertFalse(check_legacy_coverage(before, value))
        value['profiles'][0]['paths'].clear()
        self.assertTrue(check_legacy_coverage(before, value))

    def test_canonical_media_type_is_reused_at_message_and_tool_result_positions(self):
        catalog = load_contracts()
        content = catalog['types']['response-content']['variants']
        self.assertIn({'ref': 'response-media'}, content)
        item = catalog['types']['response-item']['properties']
        for name in ('content', 'output'):
            self.assertIn({'kind': 'array', 'items': {'ref': 'response-content'}}, item[name]['variants'])
        fields = contract_fields('g-responses')
        media = {f['selector']: f for f in fields if f['groups']}
        for parent in ('input.content', 'input.output'):
            for leaf in ('image_url', 'audio_url', 'file_data', 'encrypted_content'):
                self.assertEqual(set(media[parent + '.' + leaf]['groups']), APP_GROUPS)

    def test_native_opaque_variants_and_image_generation_history_are_classified(self):
        fields = {f['selector']: f for f in contract_fields('g-responses') if f['groups']}
        expected = {'input.encrypted_content': 'opaque', 'input.encrypted_function_args': 'opaque',
                    'input.result': 'opaque', 'input.revised_prompt': 'text',
                    'tools.input_image_mask.image_url': 'media-url'}
        for name, kind in expected.items():
            self.assertEqual(fields[name]['data_type'], kind)
            self.assertEqual(set(fields[name]['groups']), APP_GROUPS)

    def test_all_confirmed_flux_slots_share_one_opaque_type(self):
        fields = {f['selector']: f for f in contract_fields('g-images') if f['groups']}
        for i in range(1, 9):
            name = 'input_image' if i == 1 else 'input_image_' + str(i)
            self.assertEqual(fields[name]['data_type'], 'opaque')
            self.assertEqual(set(fields[name]['groups']), APP_GROUPS)
        self.assertNotIn('input_image_9', fields)
        self.assertNotIn('input_image_10', fields)
        profile = next(p for p in load_contracts()['profiles'] if p['name'] == 'g-images')
        self.assertIn('wire-name-unconfirmed', profile['unverified'][0]['status'])

    def test_model_content_urls_receive_finite_app_groups_but_control_urls_do_not(self):
        for profile, name in [('g-responses', 'input.content.image_url'),
                              ('g-chat-completions', 'messages.content.image_url.url')]:
            field = next(f for f in contract_fields(profile) if f['selector'] == name)
            self.assertEqual(set(field['groups']), APP_GROUPS)
        field = next(f for f in contract_fields('g-responses') if f['selector'] == 'input.content.file_url')
        self.assertEqual(field['data_type'], 'control')
        self.assertEqual(field['groups'], [])

    def test_semantic_types_never_disable_protocol_cve_or_aggregate_groups(self):
        for profile in generated_manifest()['profiles']:
            for entry in profile['exclusions']:
                for group in entry['exclusionManagedRuleSets'][0]['ruleGroups']:
                    if 'rules' not in group:
                        self.assertIn(group['ruleGroupName'], APP_GROUPS)
                    else:
                        self.assertFalse({'949110', '980130'} & {r['ruleId'] for r in group['rules']})

    def test_common_compatibility_expansion_preserves_all_prior_scopes_and_rules(self):
        before = common_data_before_manifest()
        after = generated_manifest()
        old_groups = {
            'REQUEST-930-APPLICATION-ATTACK-LFI', 'REQUEST-931-APPLICATION-ATTACK-RFI',
            'REQUEST-932-APPLICATION-ATTACK-RCE', 'REQUEST-933-APPLICATION-ATTACK-PHP',
            'REQUEST-941-APPLICATION-ATTACK-XSS', 'REQUEST-942-APPLICATION-ATTACK-SQLI',
        }

        def scopes(manifest):
            return {(p['name'], p['scope'], tuple(p['paths']), e['matchVariable'],
                     e['selectorMatchOperator'], e['selector']): {
                         g['ruleGroupName']: None if 'rules' not in g else {
                             r['ruleId'] for r in g['rules']}
                         for g in e['exclusionManagedRuleSets'][0]['ruleGroups']}
                    for p in manifest['profiles'] for e in p['exclusions']}

        previous, current = scopes(before), scopes(after)
        self.assertEqual(len(before['profiles']), 22)
        self.assertEqual(len(after['profiles']), 22)
        self.assertEqual(len(previous), 120)
        self.assertEqual(sum(len(p['exclusions']) for p in after['profiles']), 120)
        self.assertEqual(set(previous), set(current))
        expected_scopes = {key for key, groups in previous.items()
                           if {name for name, rules in groups.items() if rules is None} == old_groups}
        self.assertEqual(len(expected_scopes), 117)
        self.assertEqual(len({key[0] for key in expected_scopes}), 21)
        java = 'REQUEST-944-APPLICATION-ATTACK-JAVA'
        protocol = 'REQUEST-921-PROTOCOL-ATTACK'
        self.assertEqual({key for key, groups in current.items()
                          if java in groups and groups[java] is None}, expected_scopes)
        for rule in ('921110', '921130'):
            self.assertEqual({key for key, groups in current.items()
                              if rule in (groups.get(protocol) or set())}, expected_scopes)
        for key, groups in previous.items():
            expected = copy.deepcopy(groups)
            if key in expected_scopes:
                expected[java] = None
                expected.setdefault(protocol, set()).update({'921110', '921130'})
            with self.subTest(profile=key[0], variable=key[3], selector=key[5]):
                self.assertEqual(current[key], expected)
        frozen = json.loads((ROOT / 'tests/fixtures/waf-common-data-before.json').read_text())
        self.assertEqual(check_legacy_coverage(frozen, after), [])

    def test_catalog_rejects_unreviewed_common_protocol_rules_and_whole_protocol_group(self):
        for change in ('rule', 'group'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = self.tree(folder)
                value = load_contracts(root)
                if change == 'rule':
                    value['groupSetRuleExclusions']['ai-data'][
                        'REQUEST-921-PROTOCOL-ATTACK'].append('921120')
                else:
                    value['groupSets']['ai-data'].append('REQUEST-921-PROTOCOL-ATTACK')
                (root / CATALOG).write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    generated_manifest(root)

    def test_control_envelopes_retain_only_their_explicit_legacy_exceptions(self):
        for profile in ('p-document-upload', 'p-speech'):
            field = next(f for f in contract_fields(profile) if f['selector'] == 'input')
            self.assertEqual(field['data_type'], 'control')
            self.assertEqual(field['groups'], [])
        self.assertFalse(any(f['groups'] for f in contract_fields('g-responses')
                             if f['selector'] in ('model', 'input.id', 'input.operation.path')))

    def test_no_broad_parent_wildcard_is_generated(self):
        forbidden = {'input.', 'messages.', 'tools.', 'metadata.', '*'}
        for profile in generated_manifest()['profiles']:
            self.assertFalse(forbidden & {e['selector'] for e in profile['exclusions']})

    def test_unknown_leaf_and_reference_cycle_stop_rendering(self):
        for mutation in ('unclassified', 'cycle'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = self.tree(folder)
                catalog = load_contracts(root)
                catalog['types']['response-content'] = (
                    {'kind': 'leaf'} if mutation == 'unclassified' else {'ref': 'response-content'})
                (root / CATALOG).write_text(json.dumps(catalog))
                with self.assertRaises(ValueError):
                    generated_manifest(root)

    def test_new_published_operation_requires_input_contract_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder)
            path = root / 'apps/gateway/apim/apis/openai-images-v1.json'
            value = json.loads(path.read_text())
            value['operations'].append({'name': 'future-operation', 'method': 'POST', 'urlTemplate': '/future'})
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'Unreviewed published operation'):
                contract_inventory(root)

    def test_model_add_remove_needs_only_api_source_then_deterministic_regeneration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.tree(folder)
            path = root / 'apps/gateway/apim/apis/openai-images-v1.json'
            value = json.loads(path.read_text())
            constraint = value['authorization']['operationConstraints']['image-edit']
            old_models = constraint['allowedModels'][:]
            constraint['allowedModels'] = ['synthetic-image-model']
            path.write_text(json.dumps(value))
            expected = '/models/openai-images/v1/deployments/synthetic-image-model/edits'
            profile = next(p for p in generated_manifest(root)['profiles'] if p['name'] == 'g-images')
            self.assertIn(expected, profile['paths'])
            self.assertTrue(all('/deployments/' + model + '/edits' not in '\n'.join(profile['paths']) for model in old_models))
            self.assertTrue(any(row['model'] == 'synthetic-image-model' for row in contract_inventory(root)))
            with self.assertRaisesRegex(ValueError, 'manifest drift'):
                render(root, check=True)
            render(root)
            render(root, check=True)

    def test_normal_update_routes_expand_selected_canonical_models(self):
        from waf_policy import selected_profiles
        from standard_apis import infrastructure_api_definitions
        from deployment.helpers.waf_body import managed_ai_paths
        apis = infrastructure_api_definitions([])
        api = next(a for a in apis if a['name'] == 'openai-images-v1')
        selected = api['authorization']['operationConstraints']['image-edit']['allowedModels'][0]
        api['authorization']['operationConstraints']['image-edit']['allowedModels'] = [selected]
        config = {'apiDefinitions': apis}
        expected = '/models/openai-images/v1/deployments/' + selected + '/edits'
        result = next(p for p in selected_profiles(config) if p['name'] == 'g-images')
        self.assertIn(expected, result['paths'])
        self.assertIn(expected, managed_ai_paths(config))
        api['name'] = 'custom-api'
        with self.assertRaisesRegex(ValueError, 'changed API input contract'):
            selected_profiles(config)
        with self.assertRaises(ValueError):
            managed_ai_paths(config)

    def test_parameter_only_new_route_stops_before_static_bicep_can_diverge(self):
        from standard_apis import infrastructure_api_definitions
        from deployment.helpers.waf_body import managed_ai_paths
        apis = infrastructure_api_definitions([])
        api = next(a for a in apis if a['name'] == 'openai-images-v1')
        api['authorization']['operationConstraints']['image-edit']['allowedModels'] = ['synthetic-image-model']
        with self.assertRaisesRegex(ValueError, 'canonical API definition'):
            require_source_routes(apis)
        with self.assertRaises(ValueError):
            managed_ai_paths({'apiDefinitions': apis})

    def test_changed_canonical_mime_does_not_inherit_data_exclusion(self):
        from standard_apis import infrastructure_api_definitions
        apis = infrastructure_api_definitions([])
        api = next(a for a in apis if a['name'] == 'openai-audio-v1')
        api['authorization']['operationConstraints']['audio-transcriptions']['contentTypes'] = ['application/json']
        self.assertNotIn('g-transcribe', {p['name'] for p in generated_manifest(apis=apis)['profiles']})
        with self.assertRaisesRegex(ValueError, 'changed API input contract'):
            require_source_routes(apis)

    def test_inventory_includes_fixed_session_and_public_binding_models(self):
        apis = {json.loads(p.read_text())['name']: json.loads(p.read_text())
                for p in (ROOT / 'apps/gateway/apim/apis').glob('*.json')}
        inventory = contract_inventory()
        for api in apis.values():
            expected = set(api.get('websocket', {}).get('session', {}).get('models', []))
            expected.update(c['model'] for c in api.get('authorization', {}).get('operationConstraints', {}).values()
                            if c.get('model'))
            actual = {row['model'] for row in inventory if row['api'] == api['name']}
            self.assertLessEqual(expected, actual)
        self.assertTrue(all(row['model'] for row in inventory
                            if row['api'].startswith('azure-voice-live-')))

    def test_composed_policy_stops_before_exceeding_azure_exclusion_limit(self):
        from waf_policy import desired_policy, exclusion
        from test_gateway_callback_waf import policy
        profile = next(p for p in generated_manifest()['profiles'] if p['name'] == 'g-responses')
        base = policy()
        base['properties']['managedRules']['exclusions'] = [
            exclusion('synthetic_' + str(i), 'REQUEST-941-APPLICATION-ATTACK-XSS', ['941130'])
            for i in range(200)]
        with self.assertRaisesRegex(ValueError, '200 exclusion limit'):
            desired_policy(base, profile, {})

    def test_catalog_contains_no_model_identifier_lists(self):
        catalog = load_contracts()
        text = json.dumps(catalog)
        self.assertNotIn('allowedModels', text)
        models = {row['model'] for row in contract_inventory() if row['model']}
        for model in models:
            self.assertNotIn('"' + model + '"', text)

    def test_all_standard_operations_have_inventory_and_frame_boundary(self):
        inventory = contract_inventory()
        api_files = list((ROOT / 'apps/gateway/apim/apis').glob('*.json'))
        expected = {(a['name'], op['name']) for file in api_files
                    for a in [json.loads(file.read_text())] for op in a.get('operations', [])}
        self.assertEqual({(r['api'], r['operation']) for r in inventory}, expected)
        catalog = load_contracts()
        self.assertTrue(catalog['transportContracts'])
        self.assertTrue(all(t['waf_applicable'] is False and t['transport'] == 'websocket-frame'
                            for t in catalog['transportContracts']))

    def test_validation_permissions_and_rendering_have_identical_group_scopes(self):
        scopes = approved_group_scopes()
        for profile in generated_manifest()['profiles']:
            actual = {(e['matchVariable'], e['selectorMatchOperator'], e['selector'])
                      for e in profile['exclusions'] if any('rules' not in g
                          for g in e['exclusionManagedRuleSets'][0]['ruleGroups'])}
            self.assertEqual(actual, scopes[profile['name']]['fields'])

    def test_deployment_preflight_rejects_drift_before_process_or_azure_work(self):
        from deployment import lifecycle
        with mock.patch('waf_contracts.render', side_effect=ValueError('synthetic manifest drift')), \
                mock.patch.object(lifecycle, 'run') as process:
            with self.assertRaisesRegex(lifecycle.DeploymentError, 'Regenerate and review'):
                lifecycle.local_preflight({})
            process.assert_not_called()

    def test_normalized_omitted_json_default_retains_canonical_scope(self):
        from standard_apis import infrastructure_api_definitions
        apis = infrastructure_api_definitions([])
        for api in apis:
            for constraint in api.get('authorization', {}).get('operationConstraints', {}).values():
                if constraint.get('bodyFormat') is None and constraint.get('kind') in ('bodyModel', 'statelessResponses'):
                    constraint['bodyFormat'] = 'json'
        selected = {p['name'] for p in require_source_routes(apis)['profiles']}
        self.assertLessEqual({'g-chat-completions', 'g-responses'}, selected)


if __name__ == '__main__':
    unittest.main()
