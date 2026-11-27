"""基盤側のEntraアプリ分離を、合成入力とARM応答で確認する。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import json
import unittest
import uuid

from tests.support.bicep_helpers import compile_bicep
from tests.support.configuration_fixtures import public_fixture
from deployment.helpers.configuration import (build_bicep_configuration, load_configuration_source,
                                               normalize_configuration)
from deployment.helpers.certificate_jobs import _arguments
from deployment.helpers.paths import LocalPaths
from deployment.helpers.portal_auth_certificate import application_vault_origin
from deployment.helpers.portal_api_auth import (CONTENT_TYPE, GENERATION, SECRET, TAGS,
                                                ensure_initial_key)
from deployment.lifecycle import protected_json


def client(role):
    """実テナントの登録から独立した合成Application ID。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://example.com/entra-rollout/' + role))


class EntraCoreAppsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.params, self.options = public_fixture()
        self.options['auditEvidence']['enabled'] = False
        self.params['entraTenantId'] = client('authentication-tenant')
        self.params['gateway']['authentication']['entra']['audience'] = client('api')
        self.params['credentialProviders']['graphDelegated']['clientId'] = client('tools')
        self.params['gateway']['authentication']['credentialLogin'] = {
            'identity': {'clientId': client('credential'), 'clientSecretFile': './secrets/broker-oidc.txt'}}

    def configured(self):
        path = self.root / 'params.json'
        path.write_text(json.dumps(self.params))
        (self.root / 'options.json').write_text(json.dumps(self.options))
        raw = load_configuration_source(path)
        return normalize_configuration(raw, base_directory=self.root)

    def test_greenfield_core_accepts_three_registrations_then_validates_the_portal(self):
        config = self.configured()
        self.assertNotIn('codex', config)
        self.assertNotEqual(config['entraTenantId'], config['azureTenantId'])
        self.assertNotIn('authentication', config['portal'])
        projected = build_bicep_configuration(config)
        self.assertTrue(projected['entraRolloutEnabled'])
        self.assertNotIn('codex', projected)
        self.assertEqual(_arguments(config, 'portal', 'initial', {'purpose': 'gateway-obo'}),
                         {'purpose': 'gateway-obo'})
        self.params['portal']['authentication'] = {'clientId': client('portal'), 'tenantId': config['entraTenantId']}
        portal_config = self.configured()
        self.assertTrue(portal_config['portal']['apiAuthentication']['enabled'])
        self.assertNotIn('codex', portal_config)

    def test_trial_requires_the_foundation_identities_and_rejects_reused_clients(self):
        auth = self.params['gateway']['authentication']
        credential = auth.pop('credentialLogin')
        self.params['portal']['apiAuthentication'] = {'enabled': True}
        with self.assertRaisesRegex(ValueError, 'dedicated API, Tools and Credential'):
            self.configured()
        auth['credentialLogin'] = credential
        self.params['portal']['authentication'] = {
            'clientId': client('tools'), 'tenantId': self.params['entraTenantId']}
        with self.assertRaisesRegex(ValueError, 'distinct client ID'):
            self.configured()

    def test_portal_storage_key_stores_only_provenance_and_reuses_the_exact_version(self):
        config = self.configured()
        workspace = LocalPaths(self.root, config['environmentName'])
        transport = Mock()
        requests = []
        arm_resource = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
                        f"/providers/Microsoft.KeyVault/vaults/kv-{config['resourceStem']}/secrets/{SECRET}")
        key_url = application_vault_origin(config["resourceStem"]) + f"/secrets/{SECRET}/" + "a" * 32
        metadata = {'id': arm_resource, 'tags': {}, 'properties': {'secretUriWithVersion': key_url,
                    'contentType': CONTENT_TYPE, 'attributes': {'enabled': True}}}
        secret_value = None

        def request(method, path, body=None):
            nonlocal secret_value
            self.assertEqual(path.split('?')[0], arm_resource)
            requests.append(method)
            if method == 'PUT':
                self.assertEqual({key: body['tags'][key] for key in TAGS}, TAGS)
                metadata['tags'] = body['tags']
                secret_value = body['properties']['value']
                self.assertRegex(secret_value, r'^[A-Za-z0-9_-]{43}$')
                return {'statusCode': 201}
            return {'statusCode': 200, 'body': metadata} if secret_value else {'statusCode': 404}

        transport.request.side_effect = request
        call = lambda: ensure_initial_key(config, workspace, transport, protected_json)
        self.assertEqual(call(), key_url)
        self.assertEqual(call(), key_url)
        self.assertEqual(requests, ['GET', 'PUT', 'GET', 'GET'])
        saved = workspace.state.joinpath('portal-api-auth-key.json').read_text()
        self.assertNotIn(secret_value, saved)
        metadata['tags'][GENERATION] = 'a' * 32
        with self.assertRaisesRegex(ValueError, 'outside the recorded deployment'):
            call()
        self.assertEqual(requests.count('PUT'), 1)

    def test_generic_portal_job_cannot_switch_to_an_arbitrary_certificate(self):
        config = self.configured()
        with self.assertRaisesRegex(ValueError, 'invalid inputs'):
            _arguments(config, 'portal', 'initial', {'purpose': 'other'})
        self.params['gateway']['authentication'].pop('credentialLogin')
        without_credential_login = self.configured()
        self.assertFalse(build_bicep_configuration(without_credential_login).get('entraRolloutEnabled', False))
        self.assertFalse(without_credential_login['portal'].get('apiAuthentication', {}).get('enabled', False))
        with self.assertRaisesRegex(ValueError, 'invalid inputs'):
            _arguments(without_credential_login, 'portal', 'initial', {'purpose': 'gateway-obo'})

    def test_compiled_portal_mounts_the_reader_only_in_the_interactive_app(self):
        template = compile_bicep('apps/portal/deploy/main.bicep')
        role, = [resource for resource in template['resources']
                 if resource['type'] == 'Microsoft.Authorization/roleAssignments']
        self.assertIn('core-portal-api-auth-key', role['scope'])
        self.assertIn('apiAuthentication', role['properties']['principalId'])
        app = compile_bicep('apps/portal/deploy/application.bicep')
        self.assertIn('PORTAL_API_AUTH_KEY_CLIENT_ID', str(app['variables']['apiAuthenticationVariables']))
        schema = compile_bicep('apps/portal/deploy/schema-job.bicep')
        job, = [resource for resource in schema['resources'] if resource['type'] == 'Microsoft.App/jobs']
        self.assertEqual(job['identity']['userAssignedIdentities'], {
            "[format('{0}', parameters('schemaIdentity').id)]": {},
        })


if __name__ == '__main__':
    unittest.main()
