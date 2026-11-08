"""Portalイメージ作成の公開資材境界と、一時権限・処理の回収を確認する。"""
import copy
import base64
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers import portal_image as image

DIGEST = 'sha256:' + 'a' * 64
SOURCE = b'public source fixture'
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()
PRINCIPAL = '12345678-1234-4234-8234-123456789006'


def fixture():
    config = {'subscriptionId': '12345678-1234-4234-8234-123456789000',
              'azureTenantId': '12345678-1234-4234-8234-123456789001',
              'resourceStem': 'test-sample', 'resourceGroupNames': {'maint': 'rg-test-sample-maint', 'svc': 'rg-test-sample-svc'}}
    base = '/subscriptions/' + config['subscriptionId']
    vm = base + '/resourceGroups/rg-test-sample-maint/providers/Microsoft.Compute/virtualMachines/vm-test-sample-mgmt'
    state = {**{key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')},
             'outputs': {'portal': {'registryName': 'acrtestsample'}, 'managementVm': {'id': vm}}}
    return config, state


def response(body=None, status=200):
    return {'statusCode': status, 'body': copy.deepcopy(body or {})}


class BuildTransport:
    def __init__(self, *, failed=False, pending=False, cleanup_failed=False, lost_submit=False):
        config, state = fixture()
        self.targets = image.build_targets(config, state)
        self.calls, self.present, self.roles = [], set(), {}
        self.failed, self.pending, self.cleanup_failed, self.lost_submit = failed, pending, cleanup_failed, lost_submit
        if pending:
            self.present.add(self.targets['commandId'])

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        target = path.split('?')[0]
        if method == 'GET':
            if target == self.targets['vmId']:
                return response({'id': target, 'location': 'japaneast', 'identity': {'principalId': PRINCIPAL}})
            if target == self.targets['registryId']:
                return response({'id': target, 'properties': {'creationDate': '2026-01-01T00:00:00.1234567Z', 'loginServer': self.targets['registryServer']}})
            if target not in self.present:
                return response({'error': {'code': 'ResourceNotFound'}}, 404)
            if image.owned_build_command(target, self.targets['commandId']):
                value = {'status': 'failed', 'stage': 'image-build'} if self.failed else {
                    'status': 'built', 'sourceSha256': SOURCE_HASH, 'imageDigest': DIGEST}
                state = 'Running' if self.pending else 'Failed' if self.failed else 'Succeeded'
                return response({'properties': {'instanceView': {'executionState': state,
                    'exitCode': 1 if self.failed else 0, 'output': image.MARKER + json.dumps(value)}}})
            return response({'id': target, 'properties': self.roles.get(target, {})})
        if method == 'PUT':
            self.present.add(target)
            if '/roleAssignments/' in target:
                self.roles[target] = body['properties']
            if self.lost_submit and '/runCommands/' in target:
                raise TimeoutError('lost response')
            return response(status=201)
        if method == 'DELETE':
            if self.cleanup_failed and '/roleAssignments/' in target:
                return response(status=403)
            self.present.discard(target)
            return response(status=204)
        raise AssertionError((method, path))


def build(transport, **kwargs):
    config, state = fixture()
    return image.build_image(config, state, transport, data=SOURCE, source_hash=SOURCE_HASH,
        record=kwargs.pop('record', lambda value: None), progress=lambda *args, **kw: None, **kwargs)


class PortalImageTests(unittest.TestCase):
    def test_actual_run_command_json_includes_base64_and_escape_overhead_in_size_limit(self):
        config, state = fixture()
        targets = image.build_targets(config, state)
        archive = b'x' * image.MAX_INLINE_BYTES
        script = image.render_build_script(config, targets, archive, hashlib.sha256(archive).hexdigest(), 'build-' + 'a' * 32)
        body = image.build_command_body('japaneast', script)
        self.assertLess(len(json.dumps(body).encode('utf-8')), image.MAX_COMMAND_BYTES)
        with self.assertRaisesRegex(ValueError, 'request size limit'):
            image.build_command_body('japaneast', '\u65e5' * (image.MAX_COMMAND_BYTES // 4))

    def test_deployment_bundle_contains_sql_configuration_and_scheduled_job_entrypoints(self):
        # 実配備で送るtarを調べ、Docker COPY後のマイグレーション・定期ジョブが欠けないことを確認する。
        data, _ = image.source_bundle(ROOT)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
            names = set(archive.getnames())
        for name in ('scripts/api-key-group-config.ts', 'scripts/api-key-administration-config.ts',
                     'scripts/api-key-subscription-import.ts', 'scripts/bootstrap-api-key-subscriptions.ts',
                     'scripts/reconcile-api-key-groups.ts', 'lib/api-key-administration/service.ts',
                     'lib/api-keys/group-policy.ts', 'sql/004_group_lifecycle.sql',
                     'sql/005_api_key_administration.sql', 'sql/006_group_name_pattern.sql',
                     'sql/007_apim_subscription_identity.sql'):
            with self.subTest(path=name):
                self.assertIn(name, names)
        self.assertIn('lib/company-administration/repository.ts', names)
        self.assertIn('sql/008_key_usage_companies.sql', names)
        self.assertIn('sql/009_subscription_sync.sql', names)
        self.assertIn('lib/subscription-sync/service.ts', names)
        self.assertIn('lib/subscription-sync/repository.ts', names)
        self.assertIn('app/api/subscription-sync/route.ts', names)
        for name in ('sql/010_connection_sessions.sql', 'lib/connection-session/client.ts',
                     'lib/connection-session/crypto.ts', 'lib/connection-session/store.ts',
                     'lib/graph-tool-server.ts', 'app/api/tools/graph/keys/route.ts',
                     'app/api/tools/graph/profile/route.ts', 'app/api/tools/graph/connection/route.ts'):
            with self.subTest(path=name):
                self.assertIn(name, names)

    def make_source(self, root):
        source = root / 'apps/portal'
        source.mkdir(parents=True)
        for name in image.TOP_LEVEL_FILES | image.EXTRA_FILES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'public\r\n')
        (source / 'app').mkdir()
        (source / 'app/page.tsx').write_bytes(b'page\r\n')
        (source / 'sql').mkdir()
        (source / 'sql/001_initial.sql').write_bytes(b'CREATE TABLE portal;\r\n')
        definitions = root / 'apps/gateway/apim/apis'
        definitions.mkdir(parents=True)
        (definitions / 'model.json').write_text(json.dumps({'name': 'model', 'path': 'models/openai/v1', 'operations': []}))
        (definitions.parent / 'model-capabilities.json').write_text(json.dumps({'schemaVersion': 1, 'entries': []}))
        return source

    def test_model_snapshot_uses_canonical_definitions_instead_of_stale_generated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = self.make_source(root)
            stale = source / image.MODEL_CATALOG_PATH
            stale.parent.mkdir(parents=True)
            stale.write_text('{"stale":true}')
            canonical = root / 'apps/gateway/apim/apis/model.json'
            data, checksum = image.source_bundle(root)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
                self.assertEqual(archive.getnames().count(image.MODEL_CATALOG_PATH), 1)
                snapshot = json.load(archive.extractfile(image.MODEL_CATALOG_PATH))
                self.assertEqual(snapshot['definitions'][0]['name'], 'model')
            stale.write_text('{"different_stale":true}')
            self.assertEqual(image.source_bundle(root)[1], checksum)
            canonical.write_text(json.dumps({'name': 'model', 'path': 'models/changed/v1', 'operations': []}))
            self.assertNotEqual(image.source_bundle(root)[1], checksum)

    def test_microphone_worklet_is_built_from_typescript_not_a_stale_local_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = self.make_source(root)
            generated = source / image.REALTIME_WORKLET_PATH
            generated.parent.mkdir(parents=True)
            generated.write_text('stale local artifact')
            data, checksum = image.source_bundle(root)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
                names = archive.getnames()
                self.assertNotIn(image.REALTIME_WORKLET_PATH, names)
                self.assertIn('scripts/prepare-realtime-worklet.ts', names)
                self.assertIn('scripts/serve-portal.ts', names)
            generated.write_text('another stale artifact')
            self.assertEqual(image.source_bundle(root)[1], checksum)

    def test_model_snapshot_resolves_only_public_model_bindings_and_tracks_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.make_source(root)
            folder = root / 'apps/gateway/apim'
            values = {'VOICE_LIVE_MODEL': 'gpt-6.1-sol', 'VOICE_LIVE_CALLS_MODEL': 'gpt-6.1-sol',
                      'VOICE_LIVE_API_VERSION': '2026-04-10', 'VOICE_LIVE_CALLS_API_VERSION': '2026-01-01-preview'}
            bindings = folder / 'public-bindings.json'
            bindings.write_text(json.dumps({'schemaVersion': 1, 'bindings': values}))
            definition = folder / 'apis/model.json'
            definition.write_text(json.dumps({'name': 'model', 'path': 'models/voice/v1', 'operations': [],
                'backendUrl': '__VOICE_LIVE_WS_BASE_URL__', 'websocket': {'queryParameters': {'model': '__VOICE_LIVE_MODEL__'}}}))
            actual = json.loads(image.model_catalog_snapshot(root))['definitions'][0]
            self.assertEqual(actual['websocket']['queryParameters']['model'], 'gpt-6.1-sol')
            self.assertEqual(actual['backendUrl'], '__VOICE_LIVE_WS_BASE_URL__')
            before = image.source_bundle(root)[1]
            values['VOICE_LIVE_MODEL'] = 'another-public-model'
            bindings.write_text(json.dumps({'schemaVersion': 1, 'bindings': values}))
            self.assertNotEqual(image.source_bundle(root)[1], before)
            values['SECRET'] = 'not-allowed'
            bindings.write_text(json.dumps({'schemaVersion': 1, 'bindings': values}))
            with self.assertRaisesRegex(ValueError, 'public model bindings'):
                image.model_catalog_snapshot(root)

    def test_archive_includes_binary_brand_assets_but_excludes_local_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = self.make_source(root)
            (source / 'public/brand').mkdir(parents=True)
            logo = b'\x00\x81binary\r\nimage'
            (source / 'public/brand/logo.webp').write_bytes(logo)
            (source / 'public/samples').mkdir()
            audio = b'RIFF\x81binary\r\nWAVE'
            (source / 'public/samples/speech-ja-jp.wav').write_bytes(audio)
            for name in ('.env.local', 'node_modules/package.json', '.next/server.js', 'lib/.env.local', 'public/session.json', 'deploy/main.bicep'):
                path = source / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('private-local-value')
            data, checksum = image.source_bundle(root)
            self.assertEqual(hashlib.sha256(data).hexdigest(), checksum)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
                names = archive.getnames()
                self.assertFalse(any(name.startswith(('.env', '.next/', 'node_modules/')) for name in names))
                self.assertNotIn('public/session.json', names)
                self.assertNotIn('deploy/main.bicep', names)
                self.assertEqual(archive.extractfile('public/brand/logo.webp').read(), logo)
                self.assertEqual(archive.extractfile('public/samples/speech-ja-jp.wav').read(), audio)
                self.assertEqual(archive.extractfile('app/page.tsx').read(), b'page\n')
                self.assertTrue(all(item.mtime == 0 and item.mode == 0o600 for item in archive.getmembers()))
            (source / 'app/page.tsx').write_bytes(b'page\n')
            self.assertEqual(image.source_bundle(root), (data, checksum))
            (source / 'deploy/build-image.sh').write_text('changed build protocol')
            self.assertNotEqual(image.source_bundle(root)[1], checksum)

    def test_archive_rejects_large_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = self.make_source(root)
            with mock.patch.object(image, 'MAX_FILE_BYTES', 3):
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    image.source_bundle(root)
            original = Path.is_symlink
            with mock.patch.object(Path, 'is_symlink', lambda path: path == source / 'app/page.tsx' or original(path)):
                with self.assertRaisesRegex(ValueError, 'symbolic links'):
                    image.source_bundle(root)

    def test_fresh_clone_does_not_need_generated_next_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.make_source(root)
            data, _ = image.source_bundle(root)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
                self.assertNotIn('next-env.d.ts', archive.getnames())

    def test_generated_next_env_changes_do_not_change_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = self.make_source(root)
            expected = image.source_bundle(root)
            for mode in ('dev', 'production'):
                (source / 'next-env.d.ts').write_text(f'import "./.next/{mode}/types/routes.d.ts";\n')
                self.assertEqual(image.source_bundle(root), expected)
            self.assertFalse(image.allowed_source_path('next-env.d.ts'))

    def test_xz_compression_format_and_checksum_are_explicit_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.make_source(root)
            expected = image.source_bundle(root)
            with mock.patch.object(image.lzma, 'compress', wraps=lzma.compress) as compress:
                actual = image.source_bundle(root)
            self.assertEqual(actual, expected)
            self.assertEqual(compress.call_args.kwargs,
                             {'format': lzma.FORMAT_XZ, 'check': lzma.CHECK_CRC64, 'preset': 9})
            self.assertTrue(actual[0].startswith(b'\xfd7zXZ\x00'))
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
            raw = decoder.decompress(actual[0])
            self.assertTrue(decoder.eof)
            self.assertEqual(decoder.check, lzma.CHECK_CRC64)
            self.assertEqual(lzma.compress(raw, format=lzma.FORMAT_XZ,
                                          check=lzma.CHECK_CRC64, preset=9), actual[0])

    def test_path_boundary_rejects_traversal_secrets_and_unknown_types(self):
        for name in ('../app/file.ts', '/app/file.ts', 'C:/app/file.ts', 'app\\file.ts',
                     'lib/.env.local', 'public/secrets/file.png', 'public/tokens.json', 'app/test.pem', 'scripts/bootstrap-local-sql.ts'):
            with self.subTest(name=name):
                self.assertFalse(image.allowed_source_path(name))
        self.assertTrue(image.allowed_source_path('scripts/auth-runtime-grants.ts'))
        self.assertTrue(image.allowed_source_path('public/brand/wordmark.webp'))
        self.assertTrue(image.allowed_source_path('public/samples/speech-ja-jp.wav'))

    def test_targets_use_portal_output_without_requiring_credential_broker(self):
        config, state = fixture()
        targets = image.build_targets(config, state)
        self.assertTrue(targets['commandId'].endswith('/runCommands/build-portal'))
        state['outputs']['portal']['registryName'] = 'different'
        with self.assertRaisesRegex(ValueError, 'management VM and registry'):
            image.build_targets(config, state)

    def run_guest_extraction(self, data, folder, *, received_data=None):
        config, state = fixture()
        script = image.render_build_script(config, image.build_targets(config, state), data,
            hashlib.sha256(data).hexdigest(), 'build-' + '1' * 32)
        python = script.split("python3 - \"$work\" <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        (folder / 'source').mkdir()
        (folder / 'source.b64').write_bytes(base64.b64encode(data if received_data is None else received_data) + b'\n')
        return subprocess.run([sys.executable, '-c', python, str(folder)], capture_output=True, check=False)

    def test_guest_extraction_accepts_real_bundle_and_rejects_traversal(self):
        data, _ = image.source_bundle()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            result = self.run_guest_extraction(data, folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((folder / 'source/app/api/health/route.ts').is_file())
            self.assertTrue((folder / 'source/scripts/auth-runtime-grants.ts').is_file())
            self.assertTrue((folder / 'source/scripts/api-key-group-config.ts').is_file())
            self.assertTrue((folder / 'source/scripts/reconcile-api-key-groups.ts').is_file())
            self.assertTrue((folder / 'source/scripts/api-key-subscription-import.ts').is_file())
            self.assertTrue((folder / 'source/scripts/bootstrap-api-key-subscriptions.ts').is_file())
            self.assertTrue((folder / 'source/sql/007_apim_subscription_identity.sql').is_file())
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            item = tarfile.TarInfo('../outside.ts')
            item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_guest_extraction(lzma.compress(stream.getvalue()), Path(directory))
            self.assertNotEqual(result.returncode, 0)

    def guest_archive(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name, data, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.size = len(data) if kind == tarfile.REGTYPE else 0
                archive.addfile(item, io.BytesIO(data))
        return lzma.compress(stream.getvalue(), format=lzma.FORMAT_XZ, preset=9)

    def test_guest_extraction_requires_xz_and_checks_source_integrity(self):
        data = self.guest_archive([('app/page.tsx', b'page', tarfile.REGTYPE)])
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_guest_extraction(gzip.compress(lzma.decompress(data)), Path(directory))
            self.assertNotEqual(result.returncode, 0)
        # tarの終端後も含め、受信した圧縮データ全体をSHA-256で照合する。
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_guest_extraction(data, Path(directory), received_data=data[:-16] + b'\x00' * 16)
            self.assertNotEqual(result.returncode, 0)

    def test_guest_extraction_keeps_secret_duplicate_and_link_boundaries(self):
        cases = (
            [('lib/.env.local', b'secret', tarfile.REGTYPE)],
            [('public/secrets/key.png', b'secret', tarfile.REGTYPE)],
            [('app/page.tsx', b'first', tarfile.REGTYPE), ('app/page.tsx', b'second', tarfile.REGTYPE)],
            [('app/page.tsx', b'', tarfile.SYMTYPE)],
            [('app/page.tsx', b'', tarfile.LNKTYPE)],
        )
        for entries in cases:
            with self.subTest(entries=[(name, kind) for name, _, kind in entries]):
                with tempfile.TemporaryDirectory() as directory:
                    result = self.run_guest_extraction(self.guest_archive(entries), Path(directory))
                    self.assertNotEqual(result.returncode, 0)

    def test_guest_extraction_keeps_twenty_mb_expanded_size_limit(self):
        # 圧縮後に十分小さくても、展開するソースの合計サイズは増やさない。
        entries = [(f'app/part{index}.ts', b'x' * 4_000_001, tarfile.REGTYPE) for index in range(5)]
        data = self.guest_archive(entries)
        self.assertLess(len(data), image.MAX_INLINE_BYTES)
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_guest_extraction(data, Path(directory))
            self.assertNotEqual(result.returncode, 0)

    def test_guest_extraction_keeps_file_size_and_file_count_limits(self):
        cases = (
            [('app/large.ts', b'x' * 5_000_001, tarfile.REGTYPE)],
            [(f'app/part{index}.ts', b'x', tarfile.REGTYPE) for index in range(1001)],
        )
        for entries in cases:
            with self.subTest(file_count=len(entries)):
                with tempfile.TemporaryDirectory() as directory:
                    result = self.run_guest_extraction(self.guest_archive(entries), Path(directory))
                    self.assertNotEqual(result.returncode, 0)

    def test_maximum_inline_source_stays_below_one_mb_after_base64_and_json(self):
        config, state = fixture()
        data = b'x' * image.MAX_INLINE_BYTES
        script = image.render_build_script(config, image.build_targets(config, state), data,
            hashlib.sha256(data).hexdigest(), 'build-' + '1' * 32)
        payload = {'location': 'japaneast', 'properties': {'source': {'script': script},
            'asyncExecution': True, 'timeoutInSeconds': 2400, 'treatFailureAsDeploymentFailure': True}}
        self.assertLess(len(json.dumps(payload).encode('utf-8')), 1_048_576)

    def test_receipt_reuse_requires_success_same_source_and_cleanup(self):
        config, state = fixture()
        targets = image.build_targets(config, state)
        receipt = {'status': 'built', 'sourceSha256': SOURCE_HASH, 'commandId': targets['commandId'] + '-0123456789ab',
                   'imageDigest': DIGEST, 'temporaryResourcesRemoved': True,
                   'registryGeneration': {'resourceId': targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            path.write_text(json.dumps(receipt))
            self.assertEqual(image.reusable_image(config, state, SOURCE_HASH, path, transport=BuildTransport()), DIGEST)
            for field, invalid in (('status', 'failed'), ('sourceSha256', 'other'), ('temporaryResourcesRemoved', False),
                                   ('imageDigest', 'latest'), ('commandId', targets['commandId'] + '-other-task')):
                path.write_text(json.dumps({**receipt, field: invalid}))
                self.assertIsNone(image.reusable_image(config, state, SOURCE_HASH, path, transport=BuildTransport()))


    def test_recreated_or_absent_registry_and_unbound_receipt_cannot_reuse_old_digest(self):
        from deployment.helpers import credential_image
        config, state = fixture()
        state['outputs']['credentialBroker'] = {'registryName': state['outputs']['portal']['registryName']}
        for implementation in (image, credential_image):
            targets = implementation.build_targets(config, state)
            generation = {'resourceId': targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'}
            receipt = {'status': 'built', 'sourceSha256': SOURCE_HASH, 'commandId': targets['commandId'],
                       'imageDigest': DIGEST, 'temporaryResourcesRemoved': True, 'registryGeneration': generation}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'receipt.json'
                for name, status, created in (('same', 200, '2026-01-01T00:00:00.123456700+00:00'),
                                               ('recreated', 200, '2026-01-02T00:00:00.1234567Z'),
                                               ('absent', 404, None)):
                    path.write_text(json.dumps(receipt))
                    transport = mock.Mock()
                    transport.request.return_value = response({'id': targets['registryId'], 'properties': {
                        'loginServer': targets['registryServer'], 'creationDate': created}}, status)
                    with self.subTest(implementation=implementation.__name__, registry=name):
                        actual = implementation.reusable_image(config, state, SOURCE_HASH, path, transport=transport)
                        self.assertEqual(actual, DIGEST if name == 'same' else None)
                        transport.request.assert_called_once_with('GET', targets['registryId'] + '?api-version=2023-07-01')
                path.write_text(json.dumps({key: value for key, value in receipt.items() if key != 'registryGeneration'}))
                transport = mock.Mock()
                self.assertIsNone(implementation.reusable_image(config, state, SOURCE_HASH, path, transport=transport))
                transport.request.assert_not_called()
                path.write_text(json.dumps(receipt))
                self.assertIsNone(implementation.reusable_image(config, state, SOURCE_HASH, path))

    def test_unreadable_or_malformed_current_registry_stops_without_reuse(self):
        from deployment.helpers import credential_image
        config, state = fixture()
        state['outputs']['credentialBroker'] = {'registryName': state['outputs']['portal']['registryName']}
        for implementation in (image, credential_image):
            targets = implementation.build_targets(config, state)
            generation = {'resourceId': targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'}
            receipt = {'status': 'built', 'sourceSha256': SOURCE_HASH, 'commandId': targets['commandId'],
                       'imageDigest': DIGEST, 'temporaryResourcesRemoved': True, 'registryGeneration': generation}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'receipt.json'
                path.write_text(json.dumps(receipt))
                for status, body in ((403, {}), (503, {}), (200, {'id': targets['registryId'], 'properties': {
                    'loginServer': targets['registryServer']}}), (200, {'id': targets['registryId'], 'properties': {
                    'loginServer': targets['registryServer'], 'creationDate': 'not-a-date'}})):
                    transport = mock.Mock()
                    transport.request.return_value = response(body, status)
                    with self.subTest(implementation=implementation.__name__, status=status, body=body):
                        with self.assertRaises(ValueError):
                            implementation.reusable_image(config, state, SOURCE_HASH, path, transport=transport)
                        self.assertEqual(transport.request.call_count, 1)


    def test_success_uses_built_in_role_and_cleans_only_own_resources(self):
        transport, records = BuildTransport(), []
        self.assertEqual(build(transport, record=lambda item: records.append(copy.deepcopy(item))), DIGEST)
        self.assertFalse(transport.present)
        self.assertTrue(records[-1]['temporaryResourcesRemoved'])
        self.assertEqual(records[-1]['registryGeneration'], {'resourceId': transport.targets['registryId'].casefold(), 'createdAt': '2026-01-01T00:00:00.1234567Z'})
        puts = [(path, body) for method, path, body in transport.calls if method == 'PUT']
        self.assertEqual(len(puts), 2)
        self.assertTrue(puts[0][1]['properties']['roleDefinitionId'].endswith('/' + image.ACR_PUSH))
        self.assertNotEqual(puts[1][0].split('?')[0], transport.targets['commandId'])
        script = puts[1][1]['properties']['source']['script']
        self.assertIn('CORE_PORTAL_IMAGE_RESULT=', script)
        self.assertIn('/portal:', script)
        self.assertIn('egakumi-portal-image.lock', script)
        self.assertIn('--password-stdin', script)
        self.assertNotIn('__SOURCE_BASE64__', script)
        self.assertNotIn('MICROSOFT_CLIENT_SECRET', script)
        self.assertFalse(any('/roleDefinitions/' in path for method, path, body in transport.calls if method != 'GET'))

    def test_failed_and_unknown_submit_always_revoke_created_resources(self):
        for kwargs, failure in (({'failed': True}, 'image-build'), ({'lost_submit': True}, 'lost response')):
            with self.subTest(kwargs=kwargs):
                transport, records = BuildTransport(**kwargs), []
                with self.assertRaisesRegex(Exception, failure):
                    build(transport, record=lambda item: records.append(copy.deepcopy(item)))
                self.assertFalse(transport.present)
                self.assertTrue(records[-1]['temporaryResourcesRemoved'])
                self.assertEqual(records[-1]['status'], 'failed')

    def test_pending_run_is_not_restarted_or_deleted(self):
        transport = BuildTransport(pending=True)
        with self.assertRaisesRegex(ValueError, 'still pending'):
            build(transport)
        self.assertEqual([method for method, path, body in transport.calls], ['GET'])

    def test_cleanup_failure_is_reported_and_previous_role_is_recovered(self):
        transport, records = BuildTransport(cleanup_failed=True), []
        with self.assertRaisesRegex(ValueError, 'cleanup did not finish'):
            build(transport, record=lambda item: records.append(copy.deepcopy(item)))
        self.assertFalse(records[-1]['temporaryResourcesRemoved'])
        self.assertEqual(records[-1]['cleanupErrors'], ['Remove temporary AcrPush'])
        transport.cleanup_failed = False
        self.assertEqual(build(transport, prior_receipt=records[-1]), DIGEST)
        self.assertFalse(transport.present)

    def test_invalid_source_checksum_stops_before_azure_requests(self):
        config, state = fixture()
        transport = BuildTransport()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            image.build_image(config, state, transport, data=SOURCE, source_hash='a' * 64,
                              record=lambda value: None)
        self.assertFalse(transport.calls)

    def test_foreign_result_marker_cannot_be_deployed(self):
        view = {'executionState': 'Succeeded', 'exitCode': 0, 'output': 'CORE_CREDENTIAL_IMAGE_RESULT=' + json.dumps({
            'status': 'built', 'sourceSha256': SOURCE_HASH, 'imageDigest': DIGEST})}
        with self.assertRaisesRegex(ValueError, 'verified digest'):
            image.parse_build_result(view, SOURCE_HASH)


if __name__ == '__main__':
    unittest.main()
