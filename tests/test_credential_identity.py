"""独立したCM本人確認アプリの境界と、未指定時の既存契約を合成入力で検査する。"""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'apps/credential-broker'))

from configuration_fixtures import connection_fixture
from deployment.helpers import configuration, credential_login, credential_pools, prepare_secrets
from operations import entra_secret
from operations.common import OperationError
from credential_broker.identity import OidcClient
from credential_broker.settings import Profile, Settings
import test_deploy_gateway as gateway_tests
from bicep_helpers import _compiled_template

IDENTITY_CLIENT = '55555555-5555-4555-8555-555555555555'
SECOND_TENANT = '88888888-8888-4888-8888-888888888888'
SYNTHETIC_SECRET = 'SYNTHETIC_IDENTITY_SECRET'


def raw_configuration(*, independent=True):
    raw = connection_fixture()
    if independent:
        raw['gateway']['authentication']['credentialLogin']['identity'] = {
            'tenantId': raw['azureTenantId'], 'clientId': IDENTITY_CLIENT,
            'clientSecretFile': 'secrets/identity-fixture.txt',
        }
    return raw


def normalized(*, independent=True):
    return configuration.normalize_configuration(raw_configuration(independent=independent))


def operation_context(config):
    """Rotationの選択・配備境界だけを検査し、実ファイルやAzureへ接続しない。"""
    group = config['resourceGroupNames']['svc']
    prefix = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + group + '/providers/'
    resources = {
        'keyVault': prefix + 'Microsoft.KeyVault/vaults/kv-' + config['resourceStem'],
        'apim': prefix + 'Microsoft.ApiManagement/service/apim-fixture',
    }
    return SimpleNamespace(config=config, config_path=Path('synthetic-params.json'),
        output_path=lambda value: Path(value), service_id=lambda kind: resources[kind],
        verify_resource=mock.Mock(), run_cli=mock.Mock(), save_private=mock.Mock())


class CredentialIdentityConfigurationTests(unittest.TestCase):
    def test_absent_identity_preserves_per_profile_apps_and_environment_names(self):
        raw = raw_configuration(independent=False)
        second = copy.deepcopy(raw['credentialProviders'][0])
        second.update(name='second-oauth', clientId=IDENTITY_CLIENT, oauthTenantId=SECOND_TENANT,
                      clientSecretFile='secrets/second-cm.txt')
        raw['credentialProviders'].append(second)
        raw['gateway']['authentication']['credentialLogin']['providerNames'].append(second['name'])
        config = configuration.normalize_configuration(raw)
        profiles = credential_pools.logical_profiles(config)
        for index, provider in enumerate(config['credentialProviders']):
            with self.subTest(profile=provider['name']):
                self.assertEqual(profiles[provider['name']]['tenantId'], provider['oauthTenantId'])
                self.assertEqual(profiles[provider['name']]['clientId'], provider['clientId'])
                self.assertEqual(profiles[provider['name']]['clientSecretEnv'], 'OIDC_SECRET_' + str(index))

    def test_independent_app_changes_oidc_only_and_keeps_cm_identity_endpoint_and_pool(self):
        legacy = normalized(independent=False)
        config = normalized()
        profile = credential_pools.logical_profiles(config)['approved-oauth']
        legacy_profile = credential_pools.logical_profiles(legacy)['approved-oauth']
        self.assertEqual(profile['clientId'], IDENTITY_CLIENT)
        self.assertEqual(profile['clientSecretEnv'], 'OIDC_SECRET_IDENTITY')
        for key in ('identityEndpoint', 'identityIdProperty', 'pools'):
            self.assertEqual(profile[key], legacy_profile[key])
        self.assertEqual(credential_pools.physical_providers(config), credential_pools.physical_providers(legacy))
        self.assertEqual(config['credentialProviders'], legacy['credentialProviders'])

    def test_explicit_existing_app_and_file_is_supported_for_compatible_transition(self):
        raw = raw_configuration()
        provider = raw['credentialProviders'][0]
        identity = raw['gateway']['authentication']['credentialLogin']['identity']
        identity.update(clientId=provider['clientId'], clientSecretFile=provider['clientSecretFile'])
        config = configuration.normalize_configuration(raw)
        profile = credential_pools.logical_profiles(config)['approved-oauth']
        self.assertEqual(profile['clientId'], provider['clientId'])
        self.assertEqual(profile['clientSecretEnv'], 'OIDC_SECRET_IDENTITY')

    def test_multiple_selected_cm_apps_use_the_common_oidc_app_without_changing_their_credentials(self):
        raw = raw_configuration()
        other = copy.deepcopy(raw['credentialProviders'][0])
        other.update(name='second-oauth', clientId='77777777-7777-4777-8777-777777777777',
                     clientSecretFile='secrets/second-cm.txt')
        raw['credentialProviders'].append(other)
        raw['gateway']['authentication']['credentialLogin']['providerNames'].append(other['name'])
        config = configuration.normalize_configuration(raw)
        profiles = credential_pools.logical_profiles(config)
        self.assertEqual({profile['clientId'] for profile in profiles.values()}, {IDENTITY_CLIENT})
        self.assertEqual({profile['clientSecretEnv'] for profile in profiles.values()}, {'OIDC_SECRET_IDENTITY'})
        self.assertEqual({provider['clientId'] for provider in credential_pools.physical_providers(config)},
                         {provider['clientId'] for provider in raw['credentialProviders']})

    def test_incomplete_invalid_or_unknown_identity_fields_are_rejected(self):
        for field, invalid in (
            ('tenantId', None), ('tenantId', 'invalid'), ('clientId', None),
            ('clientId', '00000000-0000-0000-0000-000000000000'),
            ('clientSecretFile', None), ('clientSecretFile', ''), ('unexpected', True),
        ):
            raw = raw_configuration()
            raw['gateway']['authentication']['credentialLogin']['identity'][field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw)
        for invalid in (None, [], 'identity'):
            raw = raw_configuration()
            raw['gateway']['authentication']['credentialLogin']['identity'] = invalid
            with self.subTest(identity=invalid), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw)

    def test_independent_tenant_must_match_all_selected_cm_providers(self):
        raw = raw_configuration()
        second = copy.deepcopy(raw['credentialProviders'][0])
        second.update(name='second-oauth', oauthTenantId=SECOND_TENANT,
                      clientSecretFile='secrets/second-cm.txt')
        raw['credentialProviders'].append(second)
        raw['gateway']['authentication']['credentialLogin']['providerNames'].append(second['name'])
        with self.assertRaisesRegex(configuration.ConfigurationError, 'every selected'):
            configuration.normalize_configuration(raw)

    def test_different_apps_cannot_share_a_normalized_secret_file(self):
        raw = raw_configuration()
        raw['gateway']['authentication']['credentialLogin']['identity']['clientSecretFile'] = raw['credentialProviders'][0]['clientSecretFile']
        with self.assertRaisesRegex(configuration.ConfigurationError, 'different Entra application'):
            configuration.normalize_configuration(raw)

    def test_public_bicep_and_apim_settings_strip_protected_identity_reference_without_mutation(self):
        config = normalized()
        original = copy.deepcopy(config)
        public = configuration.build_bicep_configuration(config)
        login = credential_login.settings(config)
        self.assertNotIn('clientSecretFile', json.dumps(public))
        self.assertEqual(public['gateway']['authentication']['credentialLogin']['identity']['clientId'], IDENTITY_CLIENT)
        self.assertNotIn('identity', login)
        self.assertNotIn(IDENTITY_CLIENT, json.dumps(login))
        self.assertEqual(config, original)

    def test_identity_reference_is_resolved_and_rebased_with_other_protected_inputs(self):
        raw = raw_configuration()
        original = copy.deepcopy(raw)
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder).resolve() / 'source', Path(folder).resolve() / 'output'
            resolved = configuration._resolve_file_references(raw, source)
            actual = resolved['gateway']['authentication']['credentialLogin']['identity']['clientSecretFile']
            self.assertEqual(Path(actual), source / 'secrets/identity-fixture.txt')
            rebased = configuration.configuration_for_file(raw, base_directory=source, output_directory=destination)
            self.assertEqual(rebased['gateway']['authentication']['credentialLogin']['identity']['clientSecretFile'], '../source/secrets/identity-fixture.txt')
        self.assertEqual(raw, original)

    def test_reference_traversal_leaves_malformed_sections_for_normalization_to_reject(self):
        for raw in ({'gateway': None}, {'gateway': {'authentication': None}},
                    {'gateway': {'authentication': {'credentialLogin': None}}}):
            with self.subTest(raw=raw):
                self.assertEqual(list(configuration._file_reference_fields(raw)), [])

    def test_identity_secret_is_absent_without_read_and_independent_when_configured(self):
        with mock.patch.object(configuration, '_read_protected_file', return_value=SYNTHETIC_SECRET) as read:
            self.assertEqual(configuration.load_credential_identity_secret(normalized(independent=False)), '')
            read.assert_not_called()
            config = normalized()
            self.assertEqual(configuration.load_credential_identity_secret(config), SYNTHETIC_SECRET)
            read.assert_called_once_with(config['gateway']['authentication']['credentialLogin']['identity']['clientSecretFile'],
                                         'credentialLogin.identity.clientSecretFile')

    def test_empty_identity_secret_is_rejected(self):
        with mock.patch.object(configuration, '_read_protected_file', return_value=''):
            with self.assertRaisesRegex(configuration.ConfigurationError, 'must not be empty'):
                configuration.load_credential_identity_secret(normalized())

    def test_secret_preparation_separates_app_purposes_and_storage_destinations(self):
        config = normalized()
        specifications = prepare_secrets._specifications(config)
        self.assertEqual(len(specifications), 2)
        cm, identity = specifications
        self.assertEqual([purpose['kind'] for purpose in cm['purposes']], ['cm'])
        self.assertEqual([purpose['kind'] for purpose in identity['purposes']], ['identity'])
        self.assertEqual(prepare_secrets._purpose_label(identity), 'CM本人確認（OIDC）')
        guides = prepare_secrets._storage_guide(config, Path(config['credentialProviders'][0]['clientSecretFile']).parent,
                                               specifications)
        self.assertIn('APIM', guides[0])
        self.assertNotIn('Key Vault', guides[0])
        self.assertIn('認証窓口用Key Vault', guides[1])
        self.assertNotIn('APIM', guides[1])

    def test_secret_preparation_deduplicates_same_app_shared_file_and_retains_both_purposes(self):
        raw = raw_configuration()
        provider = raw['credentialProviders'][0]
        raw['gateway']['authentication']['credentialLogin']['identity'].update(
            clientId=provider['clientId'], clientSecretFile=provider['clientSecretFile'])
        specifications = prepare_secrets._specifications(configuration.normalize_configuration(raw))
        self.assertEqual(len(specifications), 1)
        self.assertEqual({purpose['kind'] for purpose in specifications[0]['purposes']}, {'cm', 'identity'})
        self.assertIn('CM接続', prepare_secrets._purpose_label(specifications[0]))
        self.assertIn('CM本人確認（OIDC）', prepare_secrets._purpose_label(specifications[0]))


class CredentialIdentityRotationTests(unittest.TestCase):
    def test_default_rotation_keeps_cm_application_and_identity_only_is_explicit(self):
        config = normalized()
        ctx = operation_context(config)
        cm = entra_secret.selection(ctx, 'approved-oauth')
        identity = entra_secret.selection(ctx, 'approved-oauth', 'identity')
        provider = config['credentialProviders'][0]
        self.assertEqual(cm['clientId'], provider['clientId'])
        self.assertEqual(cm['profiles'], ['approved-oauth'])
        self.assertEqual(cm['inputFiles'], [provider['clientSecretFile']])
        self.assertNotIn('identity', cm)
        self.assertEqual(identity['clientId'], IDENTITY_CLIENT)
        self.assertEqual(identity['profiles'], [])
        self.assertEqual(identity['inputFiles'], [config['gateway']['authentication']['credentialLogin']['identity']['clientSecretFile']])
        self.assertTrue(identity['identity'])

    def test_same_app_rotation_includes_cm_and_identity_files_for_either_selection(self):
        raw = raw_configuration()
        raw['gateway']['authentication']['credentialLogin']['identity']['clientId'] = raw['credentialProviders'][0]['clientId']
        config = configuration.normalize_configuration(raw)
        ctx = operation_context(config)
        cm = entra_secret.selection(ctx, 'approved-oauth')
        self.assertEqual(cm, entra_secret.selection(ctx, 'approved-oauth', 'identity'))
        self.assertTrue(cm['identity'])
        self.assertEqual(len(cm['inputFiles']), 2)
        self.assertEqual(cm['profiles'], ['approved-oauth'])

    def test_rotation_includes_unselected_cm_consumers_of_the_same_application(self):
        raw = raw_configuration()
        other = copy.deepcopy(raw['credentialProviders'][0])
        other.update(name='unselected', clientSecretFile='secrets/unselected-fixture.txt', connectionNames=['shared'])
        raw['credentialProviders'].append(other)
        config = configuration.normalize_configuration(raw)
        scope = entra_secret.selection(operation_context(config), 'approved-oauth')
        self.assertEqual(scope['profiles'], ['approved-oauth'])
        self.assertEqual(len(scope['inputFiles']), 2)
        self.assertNotIn('identity', scope)

    def test_rotation_rejects_absent_independent_identity_or_unknown_profile_and_purpose(self):
        ctx = operation_context(normalized(independent=False))
        for profile, purpose in (('approved-oauth', 'identity'), ('unknown', 'cm'), ('approved-oauth', 'other')):
            with self.subTest(profile=profile, purpose=purpose), self.assertRaises(OperationError):
                entra_secret.selection(ctx, profile, purpose)

    def test_identity_only_rotation_skips_cm_key_vault_updates_and_redeploys_gateway(self):
        ctx = operation_context(normalized())
        scope = entra_secret.selection(ctx, 'approved-oauth', 'identity')
        with mock.patch.object(entra_secret, 'update_shared_cm_secrets') as shared, \
                mock.patch.object(entra_secret, 'save') as save:
            entra_secret.update_consumers(ctx, scope, {}, Path('synthetic-receipt.json'), {}, 'switch')
        shared.assert_not_called()
        save.assert_called_once()
        ctx.verify_resource.assert_called()
        ctx.run_cli.assert_called_once()
        args = ctx.run_cli.call_args.args[0]
        self.assertIn('-B', args)
        self.assertIn('--execute', args)
        self.assertTrue(any(arg.endswith('deploy_gateway.py') for arg in args))
        self.assertNotIn(SYNTHETIC_SECRET, json.dumps(args))

    def test_cm_rotation_updates_shared_vault_only_for_shared_login(self):
        for independent, expected in ((True, ['gateway']), (False, ['cm', 'gateway'])):
            with self.subTest(independent=independent):
                ctx = operation_context(normalized(independent=independent))
                scope = entra_secret.selection(ctx, 'approved-oauth')
                events = []
                ctx.run_cli.side_effect = lambda args: events.append('gateway')
                with mock.patch.object(entra_secret, 'update_shared_cm_secrets', side_effect=lambda *args: events.append('cm')), \
                        mock.patch.object(entra_secret, 'save'):
                    entra_secret.update_consumers(ctx, scope, {}, Path('synthetic-receipt.json'),
                                                 {'approved-oauth': SYNTHETIC_SECRET}, 'switch')
                self.assertEqual(events, expected)


class CredentialIdentityRuntimeTests(unittest.TestCase):
    def test_environment_binds_independent_oidc_credentials_and_keeps_cm_owner_endpoint(self):
        config = normalized()
        profiles = credential_pools.logical_profiles(config)
        env = {
            'BROKER_PROFILES_JSON': json.dumps(profiles), 'OIDC_SECRET_IDENTITY': SYNTHETIC_SECRET,
            'BROKER_SHARED_KEY': 'SYNTHETIC_BROKER_SHARED_KEY_VALUE_32',
            'BROKER_PUBLIC_ORIGIN': 'https://gateway.example.com',
            'AZURE_SUBSCRIPTION_ID': config['subscriptionId'],
            'AZURE_RESOURCE_GROUP': 'rg-fixture-svc', 'APIM_NAME': 'apim-fixture',
            'APIM_PRINCIPAL_ID': IDENTITY_CLIENT, 'AZURE_TENANT_ID': config['azureTenantId'],
            'AZURE_CLIENT_ID': config['credentialProviders'][0]['clientId'],
        }
        profile = Settings.from_environment(env).profiles['approved-oauth']
        self.assertEqual(profile.identity_app.client_id, IDENTITY_CLIENT)
        self.assertEqual(profile.identity_app.client_secret, SYNTHETIC_SECRET)
        self.assertEqual(profile.identity_endpoint, config['credentialProviders'][0]['identityEndpoint'])
        self.assertNotIn(SYNTHETIC_SECRET, repr(profile))
        self.assertNotIn(SYNTHETIC_SECRET, repr(profile.identity_app))

    def test_msal_application_uses_oidc_identity_and_does_not_request_cm_permissions(self):
        """資格情報の配線を検査する。MSAL/実署名検証の統合試験とは区別する。"""
        config = normalized()
        profile = Profile('approved-oauth', config['azureTenantId'], IDENTITY_CLIENT, SYNTHETIC_SECRET)
        msal = SimpleNamespace(ConfidentialClientApplication=mock.Mock(), TokenCache=mock.Mock())
        with mock.patch.dict(sys.modules, {'msal': msal}):
            OidcClient('https://gateway.example.com')._application(profile)
        msal.ConfidentialClientApplication.assert_called_once_with(IDENTITY_CLIENT,
            client_credential=SYNTHETIC_SECRET,
            authority='https://login.microsoftonline.com/' + config['azureTenantId'],
            token_cache=msal.TokenCache.return_value, exclude_scopes=['offline_access'])


class CredentialIdentityGatewayTests(unittest.TestCase):
    def configured_gateway(self):
        config, state = gateway_tests.fixture()
        provider = config['credentialProviders'][0]
        config['gateway']['authentication']['credentialLogin']['identity'] = {
            'tenantId': provider['oauthTenantId'], 'clientId': IDENTITY_CLIENT,
            'clientSecretFile': 'secrets/identity-fixture.txt',
        }
        config['publishApis'] = True
        return config, state

    def test_legacy_execution_does_not_read_or_pass_independent_identity_secret(self):
        config, state = gateway_tests.fixture()
        config['publishApis'] = True
        with gateway_tests.GatewayDeploymentTests.isolated_cli(self, config, state) as (arguments, observed), \
                mock.patch.object(gateway_tests.gateway, 'load_credential_identity_secret') as read:
            self.assertEqual(gateway_tests.gateway.main(arguments), 0)
            read.assert_not_called()
            self.assertNotIn('credentialIdentitySecret', observed['deploy_release'].call_args.args[2])

    def test_offline_plan_never_reads_or_saves_identity_secret_or_reference(self):
        config, state = self.configured_gateway()
        with gateway_tests.GatewayDeploymentTests.isolated_cli(self, config, state) as (arguments, observed), \
                mock.patch.object(gateway_tests.gateway, 'load_credential_identity_secret') as read:
            arguments.remove('--execute')
            self.assertEqual(gateway_tests.gateway.main(arguments), 0)
            read.assert_not_called()
            plan = observed['protected_json'].call_args.args[1]
            self.assertNotIn('credentialIdentitySecret', plan['parameters'])
            self.assertNotIn('clientSecretFile', json.dumps(plan))
            observed['load_credential_provider_secrets'].assert_not_called()
            observed['transport'].assert_not_called()

    def test_execution_passes_identity_secret_only_as_secure_deployment_input(self):
        config, state = self.configured_gateway()
        with gateway_tests.GatewayDeploymentTests.isolated_cli(self, config, state) as (arguments, observed), \
                mock.patch.object(gateway_tests.gateway, 'load_credential_identity_secret', return_value=SYNTHETIC_SECRET) as read:
            saved = []
            observed['protected_json'].side_effect = lambda path, content: saved.append(copy.deepcopy(content))
            self.assertEqual(gateway_tests.gateway.main(arguments), 0)
            read.assert_called_once_with(config)
            values = observed['deploy_release'].call_args.args[2]
            self.assertEqual(values['credentialIdentitySecret'], SYNTHETIC_SECRET)
            self.assertNotIn(SYNTHETIC_SECRET, json.dumps(saved))

    def test_compilation_failure_stops_before_identity_secret_read_and_deployment(self):
        config, state = self.configured_gateway()
        with gateway_tests.GatewayDeploymentTests.isolated_cli(self, config, state) as (arguments, observed), \
                mock.patch.object(gateway_tests.gateway, 'load_credential_identity_secret') as read:
            observed['run_json'].side_effect = ValueError('synthetic compilation failure')
            with self.assertRaisesRegex(ValueError, 'synthetic compilation failure'):
                gateway_tests.gateway.main(arguments)
            read.assert_not_called()
            observed['deploy_release'].assert_not_called()


class CredentialIdentityInfrastructureTests(unittest.TestCase):
    def test_compiled_identity_secret_is_secure_separate_and_conditional(self):
        executable = shutil.which(os.environ.get('BICEP_CLI', 'bicep'))
        if not executable:
            self.skipTest('A local Bicep compiler is required; no download or environment cache lookup is performed')
        main = _compiled_template((ROOT / 'apps/apim/deploy/main.bicep').resolve(), executable)
        access = _compiled_template((ROOT / 'apps/credential-broker/deploy/broker-access.bicep').resolve(), executable)
        self.assertEqual(main['parameters']['credentialIdentitySecret']['type'].casefold(), 'securestring')
        self.assertEqual(access['parameters']['credentialIdentitySecret']['type'].casefold(), 'securestring')
        secrets = [resource for resource in access['resources']
                   if resource['type'].casefold() == 'microsoft.keyvault/vaults/secrets']
        identity = next(resource for resource in secrets if 'core-oidc-identity' in resource['name'])
        self.assertIn("parameters('identityConfigured')", identity['condition'])
        self.assertEqual(identity['properties']['value'], "[parameters('credentialIdentitySecret')]")
        self.assertTrue(any('core-credential-broker-key' in resource['name'] for resource in secrets))


if __name__ == '__main__':
    unittest.main()
