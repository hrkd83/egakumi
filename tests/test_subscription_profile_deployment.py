"""購読プロファイル接続の秘密再利用・有効化順序・配備資材の参照を検証します。"""
from bicep_helpers import compile_bicep

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_portal as portal
from deployment.helpers import subscription_profiles as profiles

APIM = '/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-test-fixture-svc/providers/Microsoft.ApiManagement/service/apim-test-fixture'


class ProfileTransport:
    """実Azureへ接続せず、API版ごとのMCP一覧差と未作成・競合を再現します。"""
    def __init__(self, inventory=None):
        self.inventory = copy.deepcopy(inventory or {})
        self.calls = []
        self.put_status = 201
        self.keep_disabled = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), headers))
        key = path.split('?', 1)[0]
        if method == 'GET':
            response = copy.deepcopy(self.inventory.get(path, self.inventory.get(key, {'statusCode': 404})))
            if urlsplit(path).path.casefold() == (APIM + '/apis').casefold():
                version = parse_qs(urlsplit(path).query, keep_blank_values=True).get('api-version')
                if version not in (['2024-05-01'], ['2025-09-01-preview']):
                    return {'statusCode': 400}
                rows = response.get('body', {}).get('value')
                if version == ['2024-05-01'] and isinstance(rows, list):
                    # 安定版一覧はnative MCPを返さず、HTTP/WS等の既存APIは返す。
                    response['body']['value'] = [row for row in rows
                        if not isinstance(row, dict) or row.get('properties', {}).get('type') != 'mcp']
            return response
        if method != 'PUT':
            raise AssertionError('Unexpected method in profile deployment')
        if self.put_status in (200, 201, 202):
            saved = copy.deepcopy(body)
            if self.keep_disabled and key.endswith(profiles.PREFIX + 'enabled'):
                saved['properties']['value'] = 'false'
            self.inventory[key] = {'statusCode': 200, 'body': saved}
        return {'statusCode': self.put_status}


def named(suffix):
    return APIM + '/namedValues/' + profiles.PREFIX + suffix


TARGET_APIS = [{'name': 'fixture-model', 'apiType': 'http'}]
PROFILE_INLINE = (ROOT / 'apps/gateway/apim/policies/subscription-profile.xml').read_text(encoding='utf-8').replace('<fragment>', '').replace('</fragment>', '')
PROFILE_POLICY = '<policies><inbound>' + PROFILE_INLINE + '<include-fragment fragment-id="core-rate-limit" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'


def policy_path(name, api_type):
    scope = '/operations/onHandshake' if api_type == 'websocket' else ''
    return APIM + '/apis/' + name + scope + '/policies/policy'


def ready_inventory(enabled='false', definitions=None):
    definitions = TARGET_APIS if definitions is None else definitions
    inventory = {
        APIM + '/policyFragments/core-rate-limit': {'statusCode': 200, 'body': {'properties': {
            'value': '<fragment><!-- core-subscription-profile-v1: current --></fragment>'}}},
        named('secret'): {'statusCode': 200, 'body': {'properties': {
            'secret': True, 'keyVault': {'secretIdentifier': 'https://kv-fixture.vault.azure.net/secrets/' + profiles.SECRET_NAME,
                                      'lastStatus': {'code': 'Success'}}}}},
        named('enabled'): {'statusCode': 200, 'body': {'properties': {'value': enabled}}},
        APIM + '/apis': {'statusCode': 200, 'body': {'value': []}},
    }
    for api in definitions:
        name, api_type = api['name'], api.get('apiType', 'http')
        inventory[APIM + '/apis']['body']['value'].append({
            'name': name, 'id': APIM + '/apis/' + name, 'properties': {'type': api_type}})
        inventory[policy_path(name, api_type)] = {'statusCode': 200, 'body': {'properties': {'value': PROFILE_POLICY}}}
        if 'mcp' in api:
            name = api['mcp']['name']
            inventory[APIM + '/apis']['body']['value'].append({
                'name': name, 'id': APIM + '/apis/' + name, 'properties': {'type': 'mcp'}})
    return inventory


class SubscriptionProfileSettingsTests(unittest.TestCase):
    def test_first_initialization_is_disabled_and_create_only(self):
        transport = ProfileTransport()
        profiles.initialize_profile_settings(APIM, transport)
        writes = [call for call in transport.calls if call[0] == 'PUT']
        self.assertEqual(len(writes), 3)
        for suffix, value in profiles.DEFAULTS.items():
            call = next(call for call in writes if call[1].split('?')[0] == named(suffix))
            self.assertEqual(call[2]['properties'], {
                'displayName': profiles.PREFIX + suffix, 'secret': suffix == 'secret', 'value': value})
            self.assertEqual(call[3], {'If-None-Match': '*'})
        self.assertEqual(transport.inventory[named('enabled')]['body']['properties']['value'], 'false')
        transport.calls.clear()
        profiles.initialize_profile_settings(APIM, transport)
        self.assertEqual([call[0] for call in transport.calls], ['GET'] * 3)

    def test_existing_portal_url_keyvault_reference_and_activation_are_preserved(self):
        inventory = ready_inventory('true')
        inventory[named('url')] = {'statusCode': 200, 'body': {'properties': {
            'value': 'https://ca-test-fixture-portal.private.example/api/internal/subscription-profile'}}}
        before = copy.deepcopy(inventory)
        transport = ProfileTransport(inventory)
        profiles.initialize_profile_settings(APIM, transport)
        self.assertEqual(transport.inventory, before)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_initialization_does_not_overwrite_a_concurrent_creator(self):
        transport = ProfileTransport()
        transport.put_status = 412
        profiles.initialize_profile_settings(APIM, transport)
        self.assertEqual([call[3] for call in transport.calls if call[0] == 'PUT'], [{'If-None-Match': '*'}] * 3)
        self.assertEqual(transport.inventory, {})

    def test_unreadable_settings_do_not_fall_back_to_defaults(self):
        for status in (401, 403, 429, 500):
            transport = ProfileTransport({named('enabled'): {'statusCode': status}})
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'could not be read'):
                profiles.initialize_profile_settings(APIM, transport)
            self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))
        transport = ProfileTransport()
        transport.put_status = 403
        with self.assertRaisesRegex(ValueError, 'could not be initialized'):
            profiles.initialize_profile_settings(APIM, transport)

    def test_activation_requires_current_fragment_and_ready_keyvault_reference(self):
        cases = []
        old = ready_inventory()
        old[APIM + '/policyFragments/core-rate-limit']['body']['properties']['value'] = '<fragment />'
        cases.append(('old-policy', old))
        for field, value in (('secret', False), ('secretIdentifier', 'https://kv-fixture.vault.azure.net/secrets/other'),
                             ('lastStatus', {'code': 'Denied'}), ('lastStatus', {})):
            inventory = ready_inventory()
            properties = inventory[named('secret')]['body']['properties']
            if field == 'secret':
                properties[field] = value
            else:
                properties['keyVault'][field] = value
            cases.append((field, inventory))
        for label, inventory in cases:
            transport = ProfileTransport(inventory)
            with self.subTest(label=label), self.assertRaises(ValueError):
                profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
            self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_activation_updates_only_switch_and_requires_confirmation(self):
        transport = ProfileTransport(ready_inventory())
        result = profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
        self.assertEqual(result, {'enabled': True, 'cacheTtlSeconds': 30, 'runtimeVerified': False})
        writes = [call for call in transport.calls if call[0] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1].split('?')[0], named('enabled'))
        self.assertEqual(writes[0][2]['properties']['value'], 'true')
        self.assertEqual(transport.calls[-1][0], 'GET')
        transport = ProfileTransport(ready_inventory('true'))
        profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))
        transport = ProfileTransport(ready_inventory())
        transport.keep_disabled = True
        with self.assertRaisesRegex(ValueError, 'not confirmed'):
            profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)

    def test_partial_api_rollout_or_untrusted_continuation_never_enables_profiles(self):
        policy_path = APIM + '/apis/fixture-model/policies/policy'
        for case in ('missing-inline', 'late-inline', 'no-core', 'invalid-name', 'foreign-next-page'):
            inventory = ready_inventory()
            if case == 'missing-inline':
                inventory[policy_path]['body']['properties']['value'] = '<include-fragment fragment-id="core-rate-limit" />'
            elif case == 'late-inline':
                inventory[policy_path]['body']['properties']['value'] = '<include-fragment fragment-id="core-rate-limit" /><!-- core-subscription-profile-v1 -->'
            elif case == 'no-core':
                inventory[APIM + '/apis']['body']['value'] = []
            elif case == 'invalid-name':
                inventory[APIM + '/apis']['body']['value'] = [{'name': '../another-api'}]
            else:
                inventory[APIM + '/apis']['body']['nextLink'] = 'https://untrusted.example/apis'
            transport = ProfileTransport(inventory)
            with self.subTest(case=case), self.assertRaises(ValueError):
                profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
            self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_websocket_only_checks_handshake_policy_and_can_activate(self):
        definitions = [{'name': 'fixture-stream', 'apiType': 'websocket'}]
        transport = ProfileTransport(ready_inventory(definitions=definitions))
        result = profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        self.assertTrue(result['enabled'])
        policy_reads = [call[1].split('?')[0] for call in transport.calls if '/policies/' in call[1]]
        self.assertEqual(policy_reads, [policy_path('fixture-stream', 'websocket')])

    def test_omitted_http_type_is_accepted_but_explicit_invalid_and_non_http_omissions_are_not(self):
        inventory = ready_inventory()
        properties = inventory[APIM + '/apis']['body']['value'][0]['properties']
        del properties['type']
        properties.update(displayName='Synthetic HTTP API', serviceUrl='https://backend.example.com',
                          path='tools/fixture', protocols=['http', 'https'])
        transport = ProfileTransport(inventory)
        result = profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
        self.assertTrue(result['enabled'])
        self.assertFalse(any(call[1].split('?')[0] == APIM + '/apis/fixture-model' for call in transport.calls))
        for api_type in ('websocket', 'grpc', 'mcp'):
            with self.subTest(api_type=api_type):
                definitions = [{'name': 'fixture-api', 'apiType': api_type}]
                broken = ready_inventory(definitions=definitions)
                del broken[APIM + '/apis']['body']['value'][0]['properties']['type']
                transport = ProfileTransport(broken)
                with self.assertRaisesRegex(ValueError, 'metadata differs'):
                    profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))
        for value in (None, '', 'soap', 'websocket'):
            with self.subTest(explicit_type=value):
                broken = ready_inventory()
                broken[APIM + '/apis']['body']['value'][0]['properties']['type'] = value
                transport = ProfileTransport(broken)
                with self.assertRaisesRegex(ValueError, 'metadata differs'):
                    profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_mixed_api_scopes_are_all_checked_and_unmanaged_apis_are_untouched(self):
        definitions = [*TARGET_APIS, {'name': 'fixture-stream', 'apiType': 'websocket'}]
        inventory = ready_inventory(definitions=definitions)
        for name, api_type in (('company-api', 'http'), ('company-stream', 'websocket')):
            inventory[APIM + '/apis']['body']['value'].append({
                'name': name, 'id': APIM + '/apis/' + name, 'properties': {'type': api_type}})
            inventory[policy_path(name, api_type)] = {'statusCode': 200, 'body': {'properties': {'value': 'incomplete company XML'}}}
        transport = ProfileTransport(inventory)
        profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        policy_reads = [call[1].split('?')[0] for call in transport.calls if '/policies/' in call[1]]
        self.assertEqual(policy_reads, [policy_path('fixture-model', 'http'), policy_path('fixture-stream', 'websocket')])

    def test_correct_policy_on_the_wrong_scope_cannot_enable_profiles(self):
        for api_type in ('http', 'websocket'):
            with self.subTest(api_type=api_type):
                definitions = [{'name': 'fixture-api', 'apiType': api_type}]
                inventory = ready_inventory(definitions=definitions)
                del inventory[policy_path('fixture-api', api_type)]
                other_type = 'http' if api_type == 'websocket' else 'websocket'
                inventory[policy_path('fixture-api', other_type)] = {
                    'statusCode': 200, 'body': {'properties': {'value': PROFILE_POLICY}}}
                transport = ProfileTransport(inventory)
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_missing_or_mismatched_managed_api_metadata_never_enables_profiles(self):
        definitions = [*TARGET_APIS, {'name': 'fixture-stream', 'apiType': 'websocket'}]
        for case in ('missing-ws', 'wrong-type', 'missing-type', 'missing-id', 'foreign-id', 'duplicate'):
            with self.subTest(case=case):
                inventory = ready_inventory(definitions=definitions)
                rows = inventory[APIM + '/apis']['body']['value']
                if case == 'missing-ws':
                    rows.pop()
                elif case == 'wrong-type':
                    rows[1]['properties']['type'] = 'http'
                elif case == 'missing-type':
                    rows[1]['properties'] = {}
                elif case == 'missing-id':
                    rows[1]['id'] = None
                elif case == 'foreign-id':
                    rows[1]['id'] += '-another'
                else:
                    rows.append(copy.deepcopy(rows[1]))
                transport = ProfileTransport(inventory)
                with self.assertRaises(ValueError):
                    profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_handshake_requires_complete_inbound_profile_lookup_before_rate_limit(self):
        definitions = [{'name': 'fixture-stream', 'apiType': 'websocket'}]
        broken_policies = {
            'truncated': PROFILE_POLICY[:-11],
            'marker-only': '<policies><inbound><!-- core-subscription-profile-v1: current --><include-fragment fragment-id="core-rate-limit" /></inbound></policies>',
            'missing-rate': PROFILE_POLICY.replace('<include-fragment fragment-id="core-rate-limit" />', ''),
            'missing-lookup': PROFILE_POLICY.replace('send-request', 'other-request'),
            'wrong-profile-key': PROFILE_POLICY.replace('{{core-subscription-profile-secret}}', '{{other-secret}}'),
            'missing-profile-variable': PROFILE_POLICY.replace('name="gatewaySubscriptionProfile"', 'name="otherProfile"'),
            'rate-first': '<policies><inbound><include-fragment fragment-id="core-rate-limit" />' + PROFILE_INLINE + '</inbound></policies>',
            'wrong-section': '<policies><inbound /><outbound>' + PROFILE_INLINE + '<include-fragment fragment-id="core-rate-limit" /></outbound></policies>',
        }
        for case, xml in broken_policies.items():
            with self.subTest(case=case):
                inventory = ready_inventory(definitions=definitions)
                inventory[policy_path('fixture-stream', 'websocket')]['body']['properties']['value'] = xml
                transport = ProfileTransport(inventory)
                with self.assertRaises(ValueError):
                    profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_generated_mcp_uses_protected_rest_policy_and_native_mcp_proxy_needs_its_own(self):
        definitions = [{'name': 'fixture-rest', 'apiType': 'http', 'mcp': {'name': 'fixture-tools'}},
                       {'name': 'fixture-proxy', 'apiType': 'mcp'}]
        inventory = ready_inventory(definitions=definitions)
        transport = ProfileTransport(inventory)
        profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        self.assertFalse(any('/fixture-tools/policies/' in call[1] for call in transport.calls))
        for name in ('fixture-rest', 'fixture-proxy'):
            with self.subTest(name=name):
                broken = copy.deepcopy(inventory)
                del broken[policy_path(name, 'http')]
                transport = ProfileTransport(broken)
                with self.assertRaises(ValueError):
                    profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_default_projection_preserves_generated_mcp_identity_for_activation(self):
        definitions = portal.infrastructure_api_definitions([])
        targets = profiles._managed_apis(definitions)
        generated = [api['mcp']['name'] for api in definitions if 'mcp' in api]
        self.assertTrue(generated)
        for name in generated:
            self.assertEqual(targets[name.casefold()], (name, 'mcp', False))

    def test_preview_inventory_includes_generated_mcp_and_keeps_stable_handshake_policy_reads(self):
        definitions = [
            {'name': 'fixture-rest', 'apiType': 'http', 'mcp': {'name': 'fixture-tools'}},
            {'name': 'fixture-stream', 'apiType': 'websocket'},
        ]
        transport = ProfileTransport(ready_inventory(definitions=definitions))
        stable = transport.request('GET', APIM + '/apis?api-version=2024-05-01')
        preview = transport.request('GET', APIM + '/apis?api-version=2025-09-01-preview')
        self.assertEqual([row['name'] for row in stable['body']['value']], ['fixture-rest', 'fixture-stream'])
        self.assertEqual([row['name'] for row in preview['body']['value']], ['fixture-rest', 'fixture-tools', 'fixture-stream'])
        transport.calls.clear()
        result = profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        self.assertTrue(result['enabled'])
        inventory_reads = [path for method, path, *_ in transport.calls
                           if method == 'GET' and urlsplit(path).path == APIM + '/apis']
        self.assertEqual(inventory_reads, [APIM + '/apis?api-version=2025-09-01-preview'])
        policy_reads = [path for method, path, *_ in transport.calls if '/policies/' in path]
        self.assertEqual(policy_reads, [
            policy_path('fixture-rest', 'http') + '?api-version=2024-05-01&format=rawxml',
            policy_path('fixture-stream', 'websocket') + '?api-version=2024-05-01&format=rawxml',
        ])
        ordinary_requests = [path for _, path, *_ in transport.calls if urlsplit(path).path != APIM + '/apis']
        self.assertTrue(all(parse_qs(urlsplit(path).query)['api-version'] == ['2024-05-01']
                            for path in ordinary_requests))

    def test_missing_mcp_in_preview_inventory_still_prevents_activation(self):
        definitions = [{'name': 'fixture-rest', 'mcp': {'name': 'fixture-tools'}}]
        inventory = ready_inventory(definitions=definitions)
        inventory[APIM + '/apis']['body']['value'].pop()
        transport = ProfileTransport(inventory)
        with self.assertRaisesRegex(ValueError, 'Core API inventory is incomplete'):
            profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_later_preview_page_mcp_is_required_before_activation(self):
        definitions = [
            {'name': 'fixture-rest', 'apiType': 'http', 'mcp': {'name': 'fixture-tools'}},
            {'name': 'fixture-stream', 'apiType': 'websocket'},
        ]
        inventory = ready_inventory(definitions=definitions)
        rows = inventory[APIM + '/apis']['body']['value']
        next_page = 'https://management.azure.com' + APIM + '/apis?api-version=2025-09-01-preview&$skip=2'
        inventory[APIM + '/apis']['body'] = {'value': [rows[0], rows[2]], 'nextLink': next_page}
        inventory[next_page] = {'statusCode': 200, 'body': {'value': [rows[1]]}}
        transport = ProfileTransport(inventory)
        self.assertTrue(profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)['enabled'])
        second = next(i for i, call in enumerate(transport.calls) if call[1] == next_page)
        activated = next(i for i, call in enumerate(transport.calls) if call[0] == 'PUT')
        self.assertLess(second, activated)
        self.assertIn(policy_path('fixture-stream', 'websocket') + '?api-version=2024-05-01&format=rawxml',
                      [call[1] for call in transport.calls])

    def test_inventory_continuation_rejects_stable_or_duplicate_version_before_activation(self):
        for query in ('api-version=2024-05-01',
                      'api-version=2025-09-01-preview&api-version=',
                      'api-version=2025-09-01-preview&api-version=2025-09-01-preview'):
            with self.subTest(query=query):
                inventory = ready_inventory()
                next_page = 'https://management.azure.com' + APIM + '/apis?' + query + '&$skip=1'
                inventory[APIM + '/apis']['body']['nextLink'] = next_page
                transport = ProfileTransport(inventory)
                with self.assertRaisesRegex(ValueError, 'Unexpected API inventory continuation'):
                    profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' or call[1] == next_page for call in transport.calls))

    def test_all_managed_pages_are_checked_before_activation(self):
        definitions = [*TARGET_APIS, {'name': 'fixture-stream', 'apiType': 'websocket'}]
        inventory = ready_inventory(definitions=definitions)
        rows = inventory[APIM + '/apis']['body']['value']
        next_page = 'https://management.azure.com' + APIM + '/apis?api-version=' + profiles.API_INVENTORY_VERSION + '&$skip=1'
        inventory[APIM + '/apis']['body'] = {'value': rows[:1], 'nextLink': next_page}

        class PagedTransport(ProfileTransport):
            def request(self, method, path, body=None, headers=None):
                if method == 'GET' and path == next_page:
                    self.calls.append((method, path, copy.deepcopy(body), headers))
                    return {'statusCode': 200, 'body': {'value': rows[1:]}}
                return super().request(method, path, body, headers)

        transport = PagedTransport(inventory)
        profiles.activate_profile_settings(APIM, transport, definitions, timeout=0)
        second = next(index for index, call in enumerate(transport.calls) if call[1] == next_page)
        activated = next(index for index, call in enumerate(transport.calls) if call[0] == 'PUT')
        self.assertLess(second, activated)

    def test_malformed_fragment_is_not_accepted_from_its_marker_text(self):
        for xml in ('<fragment><!-- core-subscription-profile-v1: current -->',
                    '<policies><!-- core-subscription-profile-v1: current --></policies>'):
            with self.subTest(xml=xml):
                inventory = ready_inventory()
                inventory[APIM + '/policyFragments/core-rate-limit']['body']['properties']['value'] = xml
                transport = ProfileTransport(inventory)
                with self.assertRaises(ValueError):
                    profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=0)
                self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_async_activation_waits_for_readback_without_resending_the_write(self):
        class DelayedActivation(ProfileTransport):
            written = False
            confirmation_reads = 0

            def request(self, method, path, body=None, headers=None):
                response = super().request(method, path, body, headers)
                if path.split('?')[0] == named('enabled'):
                    if method == 'PUT':
                        self.written = True
                    elif method == 'GET' and self.written:
                        self.confirmation_reads += 1
                        if self.confirmation_reads == 1:
                            response['body']['properties']['value'] = 'false'
                return response

        transport = DelayedActivation(ready_inventory())
        transport.put_status = 202
        with mock.patch.object(profiles.time, 'sleep') as pause:
            result = profiles.activate_profile_settings(APIM, transport, TARGET_APIS, timeout=30, sleep=pause, monotonic=lambda: 0)
        self.assertTrue(result['enabled'])
        self.assertEqual(transport.confirmation_reads, 2)
        self.assertEqual(sum(call[0] == 'PUT' for call in transport.calls), 1)
        pause.assert_called_once_with(5)

    def test_keyvault_propagation_is_rechecked_before_activation(self):
        class PropagatingTransport(ProfileTransport):
            secret_reads = 0
            def request(self, method, path, body=None, headers=None):
                if method == 'GET' and path.split('?')[0] == named('secret'):
                    self.secret_reads += 1
                    self.inventory[named('secret')]['body']['properties']['keyVault']['lastStatus']['code'] = (
                        'Success' if self.secret_reads > 1 else 'Pending')
                return super().request(method, path, body, headers)
        transport = PropagatingTransport(ready_inventory())
        with mock.patch.object(profiles.time, 'sleep') as pause:
            result = profiles.activate_profile_settings(APIM, transport, TARGET_APIS, sleep=pause, timeout=30, monotonic=lambda: 0)
        self.assertTrue(result['enabled'])
        self.assertEqual(transport.secret_reads, 2)
        pause.assert_called_once_with(5)
        first_write = next(index for index, call in enumerate(transport.calls) if call[0] == 'PUT')
        secret_reads_before_write = [call for call in transport.calls[:first_write]
                                    if call[0] == 'GET' and call[1].split('?')[0] == named('secret')]
        self.assertEqual(len(secret_reads_before_write), 2)


class SubscriptionProfileSecretTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'subscriptionId': '11111111-1111-4111-8111-111111111111',
            'azureTenantId': '22222222-2222-4222-8222-222222222222',
            'resourceStem': 'test-fixture', 'resourceGroupNames': {'svc': 'rg-test-fixture-svc'},
            'environmentName': 'test',
        }
        self.enterContext(mock.patch.dict(os.environ, {'PYTHONDONTWRITEBYTECODE': '1'}))
        self.values = {'vaultName': 'kv-test-fixture'}

    def test_existing_vault_secret_is_not_retrieved_or_rotated(self):
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 200, 'body': {'properties': {'enabled': True}}}
        with mock.patch.object(portal, '_prepare_credential') as prepare:
            self.assertEqual(portal.profile_secret(self.config, self.values, transport), '')
        prepare.assert_not_called()
        self.assertEqual(transport.request.call_count, 1)
        method, path = transport.request.call_args.args
        self.assertEqual(method, 'GET')
        self.assertTrue(path.endswith('/secrets/' + profiles.SECRET_NAME + '?api-version=2023-07-01'))

    def test_absent_vault_secret_reuses_same_private_local_value(self):
        transport = mock.Mock()
        transport.request.return_value = {'statusCode': 404}
        writes = []
        def write_protected(path, value):
            writes.append(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')
            if os.name != 'nt':
                path.chmod(0o600)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(portal, 'protected_json', side_effect=write_protected), \
             mock.patch('deployment.helpers.grpc_lifecycle.secrets.token_hex', return_value='a' * 128) as random_value, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            root = Path(directory).resolve()
            first = portal.profile_secret(self.config, self.values, transport, root)
            second = portal.profile_secret(self.config, self.values, transport, root)
            self.assertEqual(writes[0], root / '.local/test/secrets/subscription-profile-test-fixture.json')
        self.assertEqual(first, second)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].name, 'subscription-profile-test-fixture.json')
        random_value.assert_called_once_with(64)
        self.assertEqual(output.getvalue(), '')

    def test_unreadable_vault_never_generates_replacement_secret(self):
        for status in (401, 403, 429, 500):
            transport = mock.Mock()
            transport.request.return_value = {'statusCode': status}
            with self.subTest(status=status), mock.patch.object(portal, '_prepare_credential') as prepare, \
                 self.assertRaisesRegex(ValueError, 'could not be read'):
                portal.profile_secret(self.config, self.values, transport)
            prepare.assert_not_called()


class SubscriptionProfileBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/portal/deploy/main.bicep')

    def module(self, suffix):
        resources = self.template['resources']
        resources = resources.values() if isinstance(resources, dict) else resources
        return next(resource for resource in resources if suffix in resource['name'])

    def resources(self, template):
        resources = template['resources']
        return list(resources.values()) if isinstance(resources, dict) else resources

    def test_secret_input_stays_secure_and_database_preserves_existing_value(self):
        self.assertEqual(self.template['parameters']['profileSecret']['type'], 'securestring')
        self.assertEqual(self.template['parameters']['profileSecret']['defaultValue'], '')
        database = self.module('-portal-database')
        self.assertEqual(database['properties']['parameters']['profileSecret']['value'], "[parameters('profileSecret')]")
        template = database['properties']['template']
        self.assertEqual(template['parameters']['profileSecret']['type'], 'securestring')
        key = next(resource for resource in self.resources(template) if resource['type'] == 'Microsoft.KeyVault/vaults/secrets'
                   and 'core-subscription-profile-key' in resource['name'])
        self.assertEqual(key['condition'], "[not(empty(parameters('profileSecret')))]")
        self.assertEqual(key['properties']['value'], "[parameters('profileSecret')]")
        self.assertIn('secrets/core-subscription-profile-key', template['outputs']['profileSecretUri']['value'])
        self.assertIn('.vaultUri', template['outputs']['profileSecretUri']['value'])
        self.assertNotIn("parameters('profileSecret')", json.dumps(template.get('outputs', {})))

    def test_profile_connection_is_private_url_and_keyvault_reference_without_activation(self):
        connection = self.module('-portal-subscription-profile')
        self.assertIn('profileSecretUri', connection['properties']['parameters'])
        self.assertEqual(connection['properties']['parameters']['vaultName']['value'], "[parameters('vaultName')]")
        resources = self.resources(connection['properties']['template'])
        named_values = [resource for resource in resources if resource['type'] == 'Microsoft.ApiManagement/service/namedValues']
        self.assertEqual(len(named_values), 2)
        url = next(resource for resource in named_values if 'profile-url' in resource['name'])
        self.assertIn('/api/internal/subscription-profile', url['properties']['value'])
        self.assertIn('defaultDomain', url['properties']['value'])
        self.assertNotIn('publicOrigin', url['properties']['value'])
        key = next(resource for resource in named_values if 'profile-secret' in resource['name'])
        self.assertTrue(key['properties']['secret'])
        self.assertNotIn('value', key['properties'])
        self.assertEqual(key['properties']['keyVault']['secretIdentifier'], "[parameters('profileSecretUri')]")
        self.assertFalse(any('profile-enabled' in resource['name'] for resource in resources))

    def test_apim_identity_can_read_only_the_dedicated_profile_secret(self):
        connection = self.module('-portal-subscription-profile')['properties']['template']
        assignments = [resource for resource in self.resources(connection)
                       if resource['type'] == 'Microsoft.Authorization/roleAssignments']
        self.assertEqual(len(assignments), 1)
        assignment = assignments[0]
        self.assertIn('Microsoft.KeyVault/vaults/secrets', assignment['scope'])
        self.assertIn('core-subscription-profile-key', assignment['scope'])
        self.assertIn('Microsoft.ApiManagement/service', assignment['properties']['principalId'])
        self.assertIn('.identity.principalId', assignment['properties']['principalId'])
        self.assertEqual(assignment['properties']['principalType'], 'ServicePrincipal')
        self.assertIn('4633458b-17de-408a-b874-0445c86b69e6', connection['variables']['secretsUser'])
        key = next(resource for resource in self.resources(connection)
                   if resource['type'] == 'Microsoft.ApiManagement/service/namedValues'
                   and 'profile-secret' in resource['name'])
        self.assertTrue(any('Microsoft.Authorization/roleAssignments' in dependency
                            for dependency in key['dependsOn']))

    def test_application_uses_reference_and_cannot_output_secret_value(self):
        application = self.module('-portal-application')
        self.assertEqual(application['condition'], "[parameters('deployApplication')]")
        template = application['properties']['template']
        app = next(resource for resource in self.resources(template) if resource['type'] == 'Microsoft.App/containerApps')
        settings = app['properties']['configuration']['secrets']
        profile = next(secret for secret in settings if secret['name'] == 'subscription-profile-secret')
        self.assertEqual(profile['keyVaultUrl'], "[parameters('profileSecretUri')]")
        self.assertNotIn('value', profile)
        env = app['properties']['template']['containers'][0]['env']
        self.assertIn('PORTAL_SUBSCRIPTION_PROFILE_SECRET', str(env))
        self.assertIn('subscription-profile-secret', str(env))
        self.assertNotIn('profileSecret', json.dumps(self.template.get('outputs', {})))

    def test_only_readiness_probe_uses_sql_and_profile_key_readiness_endpoint(self):
        template = self.module('-portal-application')['properties']['template']
        app = next(resource for resource in self.resources(template) if resource['type'] == 'Microsoft.App/containerApps')
        container = app['properties']['template']['containers'][0]
        probe_copy = next(item for item in container['copy'] if item['name'] == 'probes')
        path = probe_copy['input']['httpGet']['path']
        self.assertIn("'Readiness'", path)
        self.assertIn("'/api/health/ready'", path)
        self.assertIn("'/api/health'", path)
        self.assertTrue(path.startswith('[if(equals('))


class SubscriptionProfileGatewayContractTests(unittest.TestCase):
    def test_actual_compiled_gateway_policies_satisfy_the_activation_check(self):
        from arm_helpers import PolicyExpression
        from test_api_protocols import bound_external_coverage, configuration

        template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        config = configuration.normalize_configuration(bound_external_coverage(), base_directory=ROOT / 'config')
        parameters = {'apiDefinitions': config['apiDefinitions'], 'credentialLogin': {},
            'publicHostName': config['gateway']['publicHostName'], 'entraTenantId': config['entraTenantId'],
            'rateLimits': config['gateway']['rateLimits']}
        protocols = set()
        for index, api in enumerate(parameters['apiDefinitions']):
            with self.subTest(name=api['name'], api_type=api['apiType']):
                xml = PolicyExpression("[variables('policyValues')[copyIndex()]]", template['variables'],
                    functions=template.get('functions', [])).evaluate(parameters, index)
                profiles._profile_policy_ready(xml)
                protocols.add(api['apiType'])
        self.assertTrue({'http', 'websocket'}.issubset(protocols))


if __name__ == '__main__':
    unittest.main()
