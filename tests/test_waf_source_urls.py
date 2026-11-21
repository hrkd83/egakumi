"""提供元が取得するURLの除外を、承認した入口・項目・個別規則へ限定する。"""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers.waf_source_urls import source_url_exclusions
from waf_contracts import contract_fields


def expected_exclusion(selector):
    return {
        'matchVariable': 'RequestArgValues',
        'selectorMatchOperator': 'Equals',
        'selector': selector,
        'exclusionManagedRuleSets': [{
            'ruleSetType': 'OWASP',
            'ruleSetVersion': '3.2',
            'ruleGroups': [{
                'ruleGroupName': 'REQUEST-931-APPLICATION-ATTACK-RFI',
                'rules': [{'ruleId': '931130'}],
            }],
        }],
    }


PROFILES = (
    {'name': 'g-document-upload', 'scope': 'gateway', 'paths': ['/tools/documents/v1/analyze']},
    {'name': 'g-responses', 'scope': 'gateway', 'paths': ['/models/openai/v1/responses']},
)
SELECTORS = (('urlSource',), ('input.content.file_url', 'input.output.file_url'))


class WafSourceUrlTests(unittest.TestCase):
    def test_manifest_and_lookup_select_only_the_three_approved_values_and_rule(self):
        manifest = json.loads((ROOT / 'apps/gateway/waf-source-url-exclusions.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['profiles']), 2)
        self.assertEqual({profile['name'] for profile in manifest['profiles']},
                         {profile['name'] for profile in PROFILES})
        for profile, selectors in zip(PROFILES, SELECTORS):
            with self.subTest(profile=profile['name']):
                declared = next(item for item in manifest['profiles'] if item['name'] == profile['name'])
                self.assertEqual({key: declared[key] for key in profile}, profile)
                expected = [expected_exclusion(selector) for selector in selectors]
                self.assertEqual(declared['exclusions'], expected)
                self.assertEqual(source_url_exclusions(profile), expected)

    def test_changed_or_missing_name_scope_and_complete_path_list_receive_no_exclusion(self):
        for profile in PROFILES:
            changes = (
                {'name': 'g-chat-completions'},
                {'scope': 'portal'},
                {'paths': [profile['paths'][0] + '/other']},
                {'paths': [profile['paths'][0] + '*']},
                {'paths': profile['paths'] + ['/unrelated']},
                {'paths': profile['paths'] * 2},
                {'paths': []},
            )
            for changed in changes:
                with self.subTest(profile=profile['name'], changed=changed):
                    self.assertEqual(source_url_exclusions({**profile, **changed}), [])
            for missing in ('name', 'scope', 'paths'):
                with self.subTest(profile=profile['name'], missing=missing):
                    self.assertEqual(source_url_exclusions({key: value for key, value in profile.items()
                                                           if key != missing}), [])

    def test_speech_portal_document_and_status_profiles_receive_no_source_url_adjustment(self):
        for profile in (
            {'name': 'g-speech', 'scope': 'gateway', 'paths': ['/models/azure-speech/v1/synthesize']},
            {'name': 'p-document-upload', 'scope': 'portal', 'paths': ['/api/tools/documents']},
            {'name': 'g-document-status', 'scope': 'gateway', 'paths': ['/tools/documents/v1/operations/*']},
        ):
            with self.subTest(profile=profile['name']):
                self.assertEqual(source_url_exclusions(profile), [])

    def test_returned_exclusions_are_independent_nested_copies(self):
        for profile, selectors in zip(PROFILES, SELECTORS):
            with self.subTest(profile=profile['name']):
                before = copy.deepcopy(profile)
                result = source_url_exclusions(profile)
                result[0]['selector'] = 'unrelated'
                result[0]['exclusionManagedRuleSets'][0]['ruleGroups'][0]['rules'][0]['ruleId'] = '942100'
                result.append(expected_exclusion('another'))
                self.assertEqual(source_url_exclusions(profile),
                                 [expected_exclusion(selector) for selector in selectors])
                self.assertEqual(profile, before)

    def test_file_urls_keep_the_control_classification_without_ai_group_exclusions(self):
        fields = contract_fields('g-responses')
        for selector in SELECTORS[1]:
            with self.subTest(selector=selector):
                selected = [field for field in fields if field['selector'] == selector]
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0]['data_type'], 'control')
                self.assertEqual(selected[0]['groups'], [])


if __name__ == '__main__':
    unittest.main()
