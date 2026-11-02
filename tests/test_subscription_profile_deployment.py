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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_portal as portal
from deployment.helpers import subscription_profiles as profiles

APIM = '/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-test-fixture-svc/providers/Microsoft.ApiManagement/service/apim-test-fixture'


class ProfileTransport:
    """実Azureへ接続せず、未作成・既存・読取拒否・競合を再現します。"""
    def __init__(self, inventory=None):
        self.inventory = copy.deepcopy(inventory or {})
        self.calls = []
        self.put_status = 201
        self.keep_disabled = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), headers))
        key = path.split('?', 1)[0]
        if method == 'GET':
            return copy.deepcopy(self.inventory.get(key, {'statusCode': 404}))
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


def ready_inventory(enabled='false'):
    return {
        APIM + '/policyFragments/core-rate-limit': {'statusCode': 200, 'body': {'properties': {
            'value': '<fragment><!-- core-subscription-profile-v1 --></fragment>'}}},
        named('secret'): {'statusCode': 200, 'body': {'properties': {
            'secret': True, 'keyVault': {'secretIdentifier': 'https://kv-fixture.vault.azure.net/secrets/' + profiles.SECRET_NAME,
                                      'lastStatus': {'code': 'Success'}}}}},
        named('enabled'): {'statusCode': 200, 'body': {'properties': {'value': enabled}}},
        APIM + '/apis': {'statusCode': 200, 'body': {'value': [{'name': 'fixture-model'}]}},
        APIM + '/apis/fixture-model/policies/policy': {'statusCode': 200, 'body': {'properties': {
            'value': '<policies><!-- core-subscription-profile-v1 --><include-fragment fragment-id="core-rate-limit" /></policies>'}}},
    }


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
                profiles.activate_profile_settings(APIM, transport, timeout=0)
            self.assertFalse(any(call[0] == 'PUT' for call in transport.calls))

    def test_activation_updates_only_switch_and_requires_confirmation(self):
        transport = ProfileTransport(ready_inventory())
        result = profiles.activate_profile_settings(APIM, transport, timeout=0)
        self.assertEqual(result, {'enabled': True, 'cacheTtlSeconds': 30, 'runtimeVerified': False})
        writes = [call for call in transport.calls if call[0] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1].split('?')[0], named('enabled'))
        self.assertEqual(writes[0][2]['properties']['value'], 'true')
        self.assertEqual(transport.calls[-1][0], 'GET')
        transport = ProfileTransport(ready_inventory('true'))
        profiles.activate_profile_settings(APIM, transport, timeout=0)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))
        transport = ProfileTransport(ready_inventory())
        transport.keep_disabled = True
        with self.assertRaisesRegex(ValueError, 'not confirmed'):
            profiles.activate_profile_settings(APIM, transport, timeout=0)

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
                profiles.activate_profile_settings(APIM, transport, timeout=0)
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
            result = profiles.activate_profile_settings(APIM, transport, timeout=30, sleep=pause, monotonic=lambda: 0)
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
            result = profiles.activate_profile_settings(APIM, transport, sleep=pause, timeout=30, monotonic=lambda: 0)
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
        }
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
            first = portal.profile_secret(self.config, self.values, transport, Path(directory))
            second = portal.profile_secret(self.config, self.values, transport, Path(directory))
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


if __name__ == '__main__':
    unittest.main()
