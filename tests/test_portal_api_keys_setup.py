"""Portalの所有グループ入力が正本だけに保存され、無指定で全製品を許可しないことを確認する。"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers.configuration import load_configuration
from deployment.helpers.portal_api_keys import available_products, prepare_api_key_groups, approved_product_catalog, verification_subscriptions
from configuration_fixtures import fixture


class PortalApiKeySetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve() / 'environment.local.json'
        self.raw = fixture()
        self.raw['portalApiKeys'] = {'groups': [], 'groupNamePattern': '^ORG_'}
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        self.config = load_configuration(self.path)

    def test_noninteractive_requires_explicit_owner_group_without_writing(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '所有グループが未設定'):
            prepare_api_key_groups(self.path, self.config, interactive=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_group_and_selected_products_are_saved_only_to_same_master(self):
        choices = iter(['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '1', ''])
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare_api_key_groups(self.path, self.config, interactive=True, reader=lambda _: next(choices))
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual({k: v for k, v in saved.items() if k != 'portalApiKeys'},
                         {k: v for k, v in self.raw.items() if k != 'portalApiKeys'})
        self.assertEqual(saved['portalApiKeys']['groups'][0]['products'],
                         [{'productId': available_products(self.config)[0], 'maxSubscriptions': 5}])
        self.assertEqual(result['portalApiKeys'], saved['portalApiKeys'])
        self.assertEqual(saved['portalApiKeys']['platformGroupId'], 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.assertEqual(saved['portalApiKeys']['groupNamePattern'], '^ORG_')
        self.assertNotIn('displayName', saved['portalApiKeys']['groups'][0])
        self.assertEqual(saved['portalApiKeys']['verificationSubscriptions'], [{'subscriptionId': 'platform-' + available_products(self.config)[0],
            'productId': available_products(self.config)[0], 'displayName': 'Platform verification - ' + available_products(self.config)[0]}])
        self.assertEqual([path.name for path in self.path.parent.iterdir()], [self.path.name])
        self.assertIs(prepare_api_key_groups(self.path, result, interactive=False), result)

    def test_blank_product_selection_does_not_grant_all_products(self):
        choices = iter(['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', ''])
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, '明示'):
            prepare_api_key_groups(self.path, self.config, interactive=True, reader=lambda _: next(choices))
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_edits_are_not_overwritten(self):
        choices = iter(['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '1', '5'])
        changed = copy.deepcopy(self.raw)
        changed['tags']['parallel-edit'] = 'kept'
        def answer(_):
            value = next(choices)
            if value == '5':
                self.path.write_text(json.dumps(changed), encoding='utf-8')
            return value
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, '入力中にparamsが変更'):
            prepare_api_key_groups(self.path, self.config, interactive=True, reader=answer)
        self.assertEqual(json.loads(self.path.read_text()), changed)

    def test_cancel_never_writes(self):
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, '中止'):
            prepare_api_key_groups(self.path, self.config, interactive=True, reader=lambda _: 'q')
        self.assertEqual(self.path.read_bytes(), before)

    def test_unconfigured_pattern_is_prompted_and_saved_without_a_default_company(self):
        self.raw['portalApiKeys']['groupNamePattern'] = ''
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        config = load_configuration(self.path)
        choices = iter(['^(DEPT_|DIV_)', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '1', ''])
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare_api_key_groups(self.path, config, interactive=True, reader=lambda _: next(choices))
        self.assertEqual(result['portalApiKeys']['groupNamePattern'], '^(DEPT_|DIV_)')
        self.assertNotIn('groupPrefix', result['portalApiKeys'])

    def test_blank_pattern_does_not_mean_all_groups_and_never_writes(self):
        self.raw['portalApiKeys']['groupNamePattern'] = ''
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        config = load_configuration(self.path)
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, '正規表現'):
            prepare_api_key_groups(self.path, config, interactive=True, reader=lambda _: '')
        self.assertEqual(self.path.read_bytes(), before)

    def test_pattern_only_input_preserves_existing_owner_and_product_approvals(self):
        self.raw['portalApiKeys'] = {'groups': [{'groupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'products': [{'productId': available_products(self.config)[0], 'maxSubscriptions': 3}]}],
            'platformGroupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'groupNamePattern': ''}
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare_api_key_groups(self.path, load_configuration(self.path), interactive=True, reader=lambda _: '^会社_')
        self.assertEqual(result['portalApiKeys']['groups'], self.raw['portalApiKeys']['groups'])
        self.assertEqual(result['portalApiKeys']['groupNamePattern'], '^会社_')

    def test_invalid_group_id_is_rejected_without_requesting_display_name(self):
        choices = iter(['not-an-object-id'])
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'Object ID'):
            prepare_api_key_groups(self.path, self.config, interactive=True, reader=lambda _: next(choices))
        self.assertEqual(self.path.read_bytes(), before)

    def test_platform_group_must_be_selected_explicitly(self):
        self.raw['portalApiKeys']['groups'] = [{'groupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'products': [{'productId': available_products(self.config)[0]}]}]
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        config = load_configuration(self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '基盤管理グループ'):
            prepare_api_key_groups(self.path, config, interactive=False)
        self.assertEqual(self.path.read_bytes(), before)
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare_api_key_groups(self.path, config, interactive=True, reader=lambda _: '1')
        self.assertEqual(result['portalApiKeys']['platformGroupId'], config['portalApiKeys']['groups'][0]['groupId'])

    def test_normal_catalog_uses_only_released_product_and_keeps_verification_approvals(self):
        config = {'portalApiKeys': {'groups': [
            {'products': [{'productId': 'model-users', 'maxSubscriptions': 10}]},
            {'products': [{'productId': 'model-users', 'maxSubscriptions': 3}, {'productId': 'pii-users', 'maxSubscriptions': 5}]},
        ]}}
        before = copy.deepcopy(config)
        self.assertEqual(approved_product_catalog(config), [
            {'productId': 'released-apis', 'displayName': 'Released APIs', 'maxSubscriptions': 5},
        ])
        self.assertEqual(config, before)

    def test_verification_defaults_only_use_the_platform_groups_products_and_keep_explicit_ids(self):
        group = {'groupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'products': [{'productId': 'model-users'}]}
        settings = {'platformGroupId': group['groupId'], 'groups': [group,
            {'groupId': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'products': [{'productId': 'pii-users'}]}]}
        self.assertEqual([value['productId'] for value in verification_subscriptions(settings)], ['model-users'])
        settings['verificationSubscriptions'] = [{'subscriptionId': 'existing-id', 'productId': 'model-users', 'displayName': 'Test'}]
        self.assertEqual(verification_subscriptions(settings), settings['verificationSubscriptions'])

    def test_long_approved_product_keeps_the_generated_declaration_valid(self):
        product = 'p' * 80
        settings = {'platformGroupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'groups': [
            {'groupId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'products': [{'productId': product}]}]}
        generated = verification_subscriptions(settings)[0]
        self.assertEqual(generated['subscriptionId'], 'platform-' + product)
        self.assertLessEqual(len(generated['displayName']), 80)


if __name__ == '__main__':
    unittest.main()
