"""Brokerイメージの梱包・ビルド失敗・一時権限回収と起動判定を確認する。"""
import copy
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'apps/gateway/deploy'))
import broker_deployment as deploy
from deployment.helpers import credential_image as image
from deployment.helpers import cleanup
from test_credential_pool_deployment import configured

DIGEST = 'sha256:' + 'a' * 64


def fixture():
    config = configured()
    base = '/subscriptions/' + config['subscriptionId']
    stem, groups = config['resourceStem'], config['resourceGroupNames']
    identity = base + '/resourceGroups/' + groups['id'] + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/'
    broker = {
        'applicationName': 'ca-' + stem + '-credentials', 'resourceGroup': groups['svc'],
        'sqlServer': 'sql-' + stem + '.database.windows.net', 'database': 'sqldb-' + stem + '-credentials',
        'runtimeIdentity': {'id': identity + 'id-' + stem + '-credential-broker',
                            'principalId': '12345678-1234-4234-8234-123456789001',
                            'clientId': '12345678-1234-4234-8234-123456789002'},
        'schemaIdentity': {'id': identity + 'id-' + stem + '-credential-schema',
                           'principalId': '12345678-1234-4234-8234-123456789003',
                           'clientId': '12345678-1234-4234-8234-123456789004'},
        'environmentName': 'cae-' + stem, 'registryName': 'acr' + stem.replace('-', ''),
        'brokerKeySecretUri': 'https://vault.invalid/secrets/key', 'oidcSecrets': [],
        'apimPrincipalId': '12345678-1234-4234-8234-123456789005',
    }
    state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
    state['outputs'] = {'credentialBroker': broker, 'managementVm': {
        'id': base + '/resourceGroups/' + groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + stem + '-mgmt'}}
    return config, state


def ok(body=None, code=200):
    return {'statusCode': code, 'body': copy.deepcopy(body or {})}


class BuildTransport:
    def __init__(self, config, state, source_hash, *, failed=False, pending=False, cleanup_failed=False):
        self.targets = image.build_targets(config, state)
        self.calls, self.present = [], set()
        self.source_hash, self.failed, self.pending, self.cleanup_failed = source_hash, failed, pending, cleanup_failed
        if pending:
            self.present.add(self.targets['commandId'])

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        target = path.split('?')[0]
        if method == 'GET':
            if target == self.targets['vmId']:
                return ok({'id': target, 'location': 'japaneast',
                           'identity': {'principalId': '12345678-1234-4234-8234-123456789006'}})
            if target == self.targets['registryId']:
                return ok({'id': target, 'properties': {'creationDate': '2026-01-01T00:00:00.1234567Z', 'loginServer': self.targets['registryServer'], 'publicNetworkAccess': 'Disabled'}})
            if target not in self.present:
                return ok({'error': {'code': 'ResourceNotFound', 'message': 'The Resource was not found.'}}, code=404)
            if image.owned_build_command(target, self.targets['commandId']):
                result = {'status': 'failed', 'stage': 'image-build'} if self.failed else {
                    'status': 'built', 'sourceSha256': self.source_hash, 'imageDigest': DIGEST}
                view = {'executionState': 'Running' if self.pending else 'Failed' if self.failed else 'Succeeded',
                        'exitCode': 1 if self.failed else 0, 'output': image.MARKER + json.dumps(result)}
                return ok({'properties': {'provisioningState': 'Succeeded', 'instanceView': view}})
            return ok({'id': target})
        if method == 'PUT':
            self.present.add(target)
            return ok(code=201)
        if method == 'DELETE':
            if self.cleanup_failed and '/roleAssignments/' in target:
                return ok(code=403)
            self.present.discard(target)
            return ok(code=204)
        raise AssertionError((method, path))


class InitialDeploymentTests(unittest.TestCase):
    def test_source_bundle_excludes_local_secrets_and_is_stable_across_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root / 'apps/gateway/credential-broker'
            (app / 'credential_broker').mkdir(parents=True)
            (app / 'sql').mkdir()
            for name in ('Dockerfile', 'requirements.txt', 'credential_broker/app.py', 'sql/001.sql'):
                (app / name).write_bytes(b'public\r\n')
            (app / 'secret.txt').write_text('must-not-travel')
            (app / 'credential_broker/local-session.json').write_text('must-not-travel')
            data, source_hash = image.source_bundle(root)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                self.assertEqual(set(archive.getnames()), {'Dockerfile', 'requirements.txt', 'credential_broker/app.py', 'sql/001.sql'})
            (app / 'Dockerfile').write_bytes(b'public\n')
            self.assertEqual(image.source_bundle(root), (data, source_hash))

    def test_idle_cleanup_schema_is_included_in_the_same_broker_image(self):
        data, _ = image.source_bundle()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            path = 'sql/003_idle_connection_cleanup.sql'
            self.assertIn(path, archive.getnames())
            self.assertEqual(archive.extractfile(path).read(),
                             (ROOT / 'apps/gateway/credential-broker' / path).read_bytes())
            self.assertIn(b"Version=3", archive.extractfile(path).read())

    def test_build_receipt_reuse_requires_same_source_environment_and_successful_cleanup(self):
        config, state = fixture()
        targets = image.build_targets(config, state)
        receipt = {'status': 'built', 'commandId': targets['commandId'], 'imageDigest': DIGEST,
                   'sourceSha256': 'a' * 64, 'temporaryResourcesRemoved': True,
                   'registryGeneration': {'resourceId': targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'build.json'
            path.write_text(json.dumps(receipt))
            self.assertEqual(image.reusable_image(config, state, 'a' * 64, path, transport=BuildTransport(config, state, 'a' * 64)), DIGEST)
            for field, invalid in (('status', 'failed'), ('sourceSha256', 'b' * 64),
                                   ('commandId', targets['commandId'] + '-other'),
                                   ('imageDigest', 'latest'), ('temporaryResourcesRemoved', False)):
                with self.subTest(field=field):
                    path.write_text(json.dumps({**receipt, field: invalid}))
                    self.assertIsNone(image.reusable_image(config, state, 'a' * 64, path, transport=BuildTransport(config, state, 'a' * 64)))

    def test_cleanup_targets_the_same_job_names_as_deployment(self):
        config, state = fixture()
        names = deploy.resource_names(config)
        prefix = deploy.group_scope(config) + '/providers/Microsoft.App/'
        expected = [prefix + 'jobs/' + names['reconcileJob'], prefix + 'jobs/' + names['schemaJob'],
                    prefix + 'containerApps/' + names['application']]
        class Transport:
            def request(self, method, path, body=None, headers=None):
                target = path.split('?')[0]
                if target not in expected:
                    return ok({'error': {'code': 'ResourceNotFound'}}, code=404)
                kind = 'containerApps' if '/containerApps/' in target else 'jobs'
                return ok({'id': target, 'name': target.rsplit('/', 1)[-1],
                    'type': 'Microsoft.App/' + kind, 'location': config['location'],
                    'tags': {'product': config['productName'], 'environment': config['environmentName']},
                    'properties': {'environmentId': prefix + 'managedEnvironments/cae-' + config['resourceStem']}})
        self.assertEqual(cleanup._credential_apps_preflight(config, state, Transport()), expected)

    def test_nested_template_error_codes_are_visible_without_printing_response_values(self):
        class Transport:
            def request(self, *args):
                return ok({'error': {'code': 'InvalidTemplateDeployment', 'message': 'private configuration',
                    'details': [{'code': 'ValidationForResourceFailed', 'details': [{'code': 'ContainerAppInvalidName'}]}]}}, code=400)
        with self.assertRaisesRegex(ValueError, 'InvalidTemplateDeployment > ValidationForResourceFailed > ContainerAppInvalidName') as failure:
            image.checked(Transport(), 'PUT', '/fixture', {}, label='Deploy')
        self.assertNotIn('private configuration', str(failure.exception))


    def test_build_uses_private_registry_builtin_role_and_removes_only_own_temporary_resources(self):
        config, state = fixture()
        data, source_hash = image.source_bundle()
        transport = BuildTransport(config, state, source_hash)
        records = []
        digest = image.build_image(config, state, transport, data=data, source_hash=source_hash,
            record=lambda value: records.append(copy.deepcopy(value)), progress=lambda *a, **k: None)
        self.assertEqual(digest, DIGEST)
        self.assertTrue(records[-1]['temporaryResourcesRemoved'])
        self.assertEqual(records[-1]['registryGeneration'], {'resourceId': transport.targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'})
        self.assertEqual(transport.present, set())
        puts = [(path, body) for method, path, body in transport.calls if method == 'PUT']
        self.assertEqual(len(puts), 2)
        role_path, role = puts[0]
        self.assertTrue(role_path.startswith(transport.targets['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/'))
        self.assertTrue(role['properties']['roleDefinitionId'].endswith('/' + image.ACR_PUSH))
        self.assertFalse(any('/roleDefinitions' in path for method, path, body in transport.calls if method != 'GET'))
        self.assertFalse(any(path.split('?')[0] == transport.targets['registryId'] for method, path, body in transport.calls if method != 'GET'))
        script = puts[1][1]['properties']['source']['script']
        self.assertNotEqual(puts[1][0].split('?')[0], transport.targets['commandId'])
        self.assertNotIn('__SOURCE_BASE64__', script)
        self.assertIn('--password-stdin', script)
        self.assertNotIn('entra-client-secret', script)
        self.assertEqual(puts[1][1]['properties']['timeoutInSeconds'], 2400)

    def test_allowed_404_error_document_is_absence_but_403_still_fails(self):
        class Missing:
            def request(self, *args):
                return ok({'error': {'code': 'ResourceNotFound', 'message': 'Not found'}}, code=404)
        self.assertEqual(image.checked(Missing(), 'GET', '/fixture', allowed=(200, 404), label='Read build'), {})
        class Forbidden:
            def request(self, *args):
                return ok({'error': {'code': 'AuthorizationFailed', 'message': 'Private response'}}, code=403)
        with self.assertRaisesRegex(ValueError, 'HTTP 403: AuthorizationFailed') as failure:
            image.checked(Forbidden(), 'GET', '/fixture', allowed=(200, 404), label='Read build')
        self.assertNotIn('Private response', str(failure.exception))

    def test_build_failure_still_revokes_temporary_role_and_returns_safe_stage(self):
        config, state = fixture()
        transport = BuildTransport(config, state, 'b' * 64, failed=True)
        records = []
        with self.assertRaisesRegex(ValueError, 'image-build'):
            image.build_image(config, state, transport, data=b'public', source_hash='b' * 64,
                record=lambda value: records.append(copy.deepcopy(value)), progress=lambda *a, **k: None)
        self.assertTrue(records[-1]['temporaryResourcesRemoved'])
        self.assertEqual(records[-1]['registryGeneration'], {'resourceId': transport.targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'})
        self.assertEqual(transport.present, set())

    def test_cleanup_failure_is_not_reported_as_success(self):
        config, state = fixture()
        transport = BuildTransport(config, state, 'b' * 64, cleanup_failed=True)
        records = []
        with self.assertRaisesRegex(ValueError, 'cleanup did not finish'):
            image.build_image(config, state, transport, data=b'public', source_hash='b' * 64,
                record=lambda value: records.append(copy.deepcopy(value)), progress=lambda *a, **k: None)
        self.assertFalse(records[-1]['temporaryResourcesRemoved'])
        self.assertEqual(records[-1]['cleanupErrors'], ['Remove temporary AcrPush'])

    def test_pending_build_is_not_restarted_or_deleted(self):
        config, state = fixture()
        transport = BuildTransport(config, state, 'b' * 64, pending=True)
        with self.assertRaisesRegex(ValueError, 'still pending'):
            image.build_image(config, state, transport, data=b'public', source_hash='b' * 64,
                record=lambda value: None, progress=lambda *a, **k: None)
        self.assertEqual([method for method, path, body in transport.calls], ['GET'])

    def test_unique_pending_build_is_protected_using_its_receipt(self):
        config, state = fixture()
        transport = BuildTransport(config, state, 'b' * 64, pending=True)
        actual = transport.targets['commandId'] + '-0123456789ab'
        transport.present.add(actual)
        with self.assertRaisesRegex(ValueError, 'still pending'):
            image.build_image(config, state, transport, data=b'public', source_hash='b' * 64,
                record=lambda value: None, progress=lambda *a, **k: None, prior_receipt={'commandId': actual})
        self.assertEqual([method for method, path, body in transport.calls], ['GET'])

    def test_unverified_digest_or_wrong_source_cannot_be_deployed(self):
        for value in ({'status': 'built', 'sourceSha256': 'other', 'imageDigest': DIGEST},
                      {'status': 'built', 'sourceSha256': 'hash', 'imageDigest': 'latest'}):
            with self.assertRaisesRegex(ValueError, 'verified digest'):
                image.parse_build_result({'executionState': 'Succeeded', 'exitCode': 0,
                    'output': image.MARKER + json.dumps(value)}, 'hash')


    def test_readiness_waits_for_new_healthy_revision(self):
        config, _ = fixture()
        responses = iter([
            ok({'properties': {'latestRevisionName': 'broker--new', 'latestReadyRevisionName': 'broker--old'}}),
            ok({'properties': {'latestRevisionName': 'broker--new', 'latestReadyRevisionName': 'broker--new'}}),
            ok({'properties': {'healthState': 'Healthy', 'provisioningState': 'Provisioned', 'runningState': 'Running'}}),
        ])
        class Transport:
            def request(self, *args):
                return next(responses)
        waits = []
        deploy.wait_for_application(config, Transport(), sleep=waits.append)
        self.assertEqual(waits, [10])

    @unittest.skipUnless(os.name == 'posix' and shutil.which('flock'),
                         'Guest build execution requires Bash and flock')
    def test_guest_build_pipeline_and_failure_cleanup_with_stubbed_docker(self):
        config, state = fixture()
        data, source_hash = image.source_bundle()
        targets = image.build_targets(config, state)
        script = image.render_build_script(config, targets, data, source_hash, 'build-' + 'a' * 32)
        for failure_stage in (None, 'image-build', 'image-startup-check'):
            with self.subTest(failure_stage=failure_stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                bin_dir = root / 'bin'
                bin_dir.mkdir()
                def executable(name, body):
                    path = bin_dir / name
                    path.write_text('#!/usr/bin/env bash\nset -eu\n' + body + '\n')
                    path.chmod(0o700)
                executable('systemctl', 'exit 0')
                executable('python3', 'if [[ "$1" == */registry-login.py ]]; then printf mock-private-token; '
                           'else exec ' + shlex.quote(sys.executable) + ' "$@"; fi')
                executable('docker', '''
printf '%s\\n' "$1" >> "$MOCK_CALLS"
if [ "$1" = build ]; then
    [ "${MOCK_BUILD_FAILURE:-}" != 1 ] || exit 1
    source="${@: -1}"
    test -s "$source/credential_broker/app.py"
    test -s "$source/sql/001_initial.sql"
elif [ "$1" = run ]; then
    [ "${MOCK_STARTUP_FAILURE:-}" != 1 ] || exit 1
    [ "$2" = --rm ] && [ "$3" = --network ] && [ "$4" = none ]
elif [ "$1" = login ]; then
    IFS= read -r value || true
    [ "$value" = mock-private-token ]
elif [ "$1" = image ] && [ "$2" = inspect ]; then
    printf '["%s/credential-broker@%s"]\\n' "$CORE_BUILD_REGISTRY" "''' + DIGEST + '''"
fi
''')
                # Only VM-specific root/OS/lock/tmp paths are substituted. Archive, token pipe,
                # Docker command sequence, digest parsing and EXIT cleanup execute unchanged.
                guest = script.replace('[ "$EUID" = 0 ]', ':').replace('. /etc/os-release', 'ID=ubuntu; VERSION_ID=24.04')
                guest = guest.replace('exec 9>/run/lock/egakumi-credential-image.lock', 'exec 9>' + shlex.quote(str(root / 'lock')))
                guest = guest.replace('/var/tmp/egakumi-credential-image.XXXXXXXX', str(root / 'work.XXXXXXXX'))
                environment = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'],
                               'MOCK_BUILD_FAILURE': '1' if failure_stage == 'image-build' else '0',
                               'MOCK_STARTUP_FAILURE': '1' if failure_stage == 'image-startup-check' else '0',
                               'MOCK_CALLS': str(root / 'docker-calls')}
                result = subprocess.run(['bash'], input=guest, text=True, capture_output=True, env=environment, timeout=30)
                self.assertEqual(result.returncode, 1 if failure_stage else 0, result.stderr)
                self.assertNotIn('mock-private-token', result.stdout + result.stderr)
                self.assertEqual(list(root.glob('work.*')), [])
                if failure_stage:
                    self.assertIn('"stage":"' + failure_stage + '"', result.stdout)
                    self.assertNotIn('push', (root / 'docker-calls').read_text().splitlines())
                else:
                    digest = image.parse_build_result({'executionState': 'Succeeded', 'exitCode': 0, 'output': result.stdout}, source_hash)
                    self.assertEqual(digest, DIGEST)


if __name__ == '__main__':
    unittest.main()
