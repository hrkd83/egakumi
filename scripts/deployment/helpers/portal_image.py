"""Portalの公開ソースを既存管理VMでビルドし、非公開ACRへ登録する。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import lzma
from pathlib import Path
import re
import tarfile
import time
import uuid

from .paths import REPOSITORY
from . import image_build
from .image_build import owned_build_command, read_build_receipt
from .credential_image import checked

COMPUTE_API = '2024-07-01'
REGISTRY_API = '2023-07-01'
ROLE_API = '2022-04-01'
ACR_PUSH = '8311e382-0749-4cb8-b61a-304f252e45ec'
MARKER = 'CORE_PORTAL_IMAGE_RESULT='
MAX_SOURCE_BYTES = 20_000_000
# Base64化後の実際のRun Command本文も別途検査し、1 MiB未満に収めます。
# https://learn.microsoft.com/azure/azure-resource-manager/management/azure-subscription-service-limits
MAX_INLINE_BYTES = 760_000
MAX_COMMAND_BYTES = 1_048_576
MAX_FILE_BYTES = 5_000_000
TOP_LEVEL_FILES = frozenset({
    'Dockerfile', '.dockerignore', 'package.json', 'package-lock.json',
    'next.config.ts', 'tsconfig.json', 'proxy.ts',
})
SOURCE_EXTENSIONS = {
    'app': {'.ts', '.tsx', '.css', '.json', '.ico', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.avif'},
    'components': {'.ts', '.tsx', '.css'},
    'lib': {'.ts', '.tsx', '.json'},
    'styles': {'.css'},
    'public': {'.ico', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif', '.woff', '.woff2', '.wav'},
    'sql': {'.sql'},
}
EXTRA_FILES = frozenset({'scripts/migrate-auth.ts', 'scripts/auth-migrations.ts', 'scripts/auth-runtime-grants.ts',
                         'scripts/api-key-group-config.ts', 'scripts/api-key-administration-config.ts',
                         'scripts/reconcile-api-key-groups.ts',
                         'scripts/api-key-subscription-import.ts', 'scripts/bootstrap-api-key-subscriptions.ts',
                         'scripts/prepare-model-catalog.ts',
                         'scripts/prepare-realtime-worklet.ts', 'scripts/serve-portal.ts',
                         'deploy/build-image.sh', 'styles/shadcn.LICENSE.md'})
MODEL_CATALOG_PATH = 'lib/generated/model-api-definitions.json'
REALTIME_WORKLET_PATH = 'public/realtime-audio-worklet.js'


def model_catalog_snapshot(root):
    """モデルAPIの正本を同梱し、ローカルの古い生成ファイルに依存しないビルドにする。"""
    folder = Path(root) / 'apps/gateway/apim/apis'
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Gateway model API definitions are missing')
    bindings_file = folder.parent / 'public-bindings.json'
    bindings = {}
    if bindings_file.exists():
        if bindings_file.is_symlink():
            raise ValueError('Gateway public model bindings must be a regular file')
        public = json.loads(bindings_file.read_text(encoding='utf-8'))
        allowed_keys = {'VOICE_LIVE_MODEL', 'VOICE_LIVE_CALLS_MODEL', 'VOICE_LIVE_API_VERSION', 'VOICE_LIVE_CALLS_API_VERSION'}
        bindings = public.get('bindings', {})
        if (public.get('schemaVersion') != 1 or set(public) != {'schemaVersion', 'bindings'} or not isinstance(bindings, dict)
                or set(bindings) != allowed_keys
                or any(not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9._-]{1,80}', value) for value in bindings.values())):
            raise ValueError('Gateway public model bindings are invalid')
    definitions = []
    for path in sorted(folder.glob('*.json')):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Gateway model API definitions must be regular files')
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Gateway model API definition exceeds the size limit')
        definition = json.loads(path.read_text(encoding='utf-8'))
        # 公開モデル名とAPI版だけを解決します。接続先・認証・秘密のバインドは取り込みません。
        serialized = json.dumps(definition, ensure_ascii=False)
        for name, value in bindings.items():
            serialized = serialized.replace(json.dumps('__' + name + '__'), json.dumps(value))
        definition = json.loads(serialized)
        if str(definition.get('path', '')).startswith('models/'):
            definitions.append(definition)
    if not definitions:
        raise ValueError('Gateway model API definitions are missing')
    from model_capabilities import apply_model_capabilities, load_model_capabilities
    capabilities_file = folder.parent / 'model-capabilities.json'
    if capabilities_file.is_symlink():
        raise ValueError('Gateway model capabilities must be a regular file')
    capabilities = load_model_capabilities(capabilities_file)
    definitions = apply_model_capabilities(definitions, capabilities)
    return (json.dumps({'schemaVersion': 1, 'definitions': definitions, 'modelCapabilities': capabilities}, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def allowed_source_path(name):
    """Dockerへ渡してよいアプリ資材だけを選び、ローカル秘密情報を除外する。"""
    path = Path(name)
    parts = path.parts
    if path.is_absolute() or '..' in parts or not parts or '\\' in name or ':' in name:
        return False
    if any(part in ('node_modules', '.next', '.git', 'tests', 'secrets') or part.startswith('.env')
           for part in parts):
        return False
    if name in TOP_LEVEL_FILES or name in EXTRA_FILES:
        return True
    return len(parts) > 1 and path.suffix.lower() in SOURCE_EXTENSIONS.get(parts[0], set())


def source_bundle(root=REPOSITORY):
    """公開資材を再現可能なtarへまとめる。node_modulesや.envは参照しない。"""
    source = Path(root) / 'apps/portal'
    if source.is_symlink():
        raise ValueError('Portal source directory must not be a symbolic link')
    files = [source / name for name in TOP_LEVEL_FILES]
    for directory in SOURCE_EXTENSIONS:
        folder = source / directory
        if folder.is_symlink():
            raise ValueError('Portal source directories must not be symbolic links')
        if folder.is_dir():
            for path in folder.rglob('*'):
                if path.is_symlink():
                    raise ValueError('Portal source files must not be symbolic links')
                if (path.is_file() and path.relative_to(source).as_posix() not in {MODEL_CATALOG_PATH, REALTIME_WORKLET_PATH}
                        and allowed_source_path(path.relative_to(source).as_posix())):
                    files.append(path)
    files.extend(source / name for name in EXTRA_FILES if (source / name).exists())
    files = sorted(set(files), key=lambda path: path.relative_to(source).as_posix())
    if len(files) > 1000:
        raise ValueError('Portal source contains too many files for the inline build')
    if not (source / 'app').is_dir() or not (source / 'sql/001_initial.sql').is_file():
        raise ValueError('Portal application or SQL sources are missing')
    total = 0
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        for path in files:
            if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source.parent)
                    or not path.is_file() or not path.resolve().is_relative_to(source.resolve())):
                raise ValueError('Portal build source must be a regular file')
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Portal build source file exceeds the size limit')
            data = path.read_bytes()
            if path.suffix in {'.ts', '.tsx', '.css', '.json', '.md', '.sh', '.sql'} or path.name in {'Dockerfile', '.dockerignore'}:
                data = data.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
            total += len(data)
            if total > MAX_SOURCE_BYTES:
                raise ValueError('Portal build source exceeds the size limit')
            item = tarfile.TarInfo(path.relative_to(source).as_posix())
            item.size, item.mode, item.mtime = len(data), 0o600, 0
            archive.addfile(item, io.BytesIO(data))
        catalog = model_catalog_snapshot(root)
        if total + len(catalog) > MAX_SOURCE_BYTES:
            raise ValueError('Portal source exceeds the size limit')
        item = tarfile.TarInfo(MODEL_CATALOG_PATH)
        item.size, item.mode, item.mtime = len(catalog), 0o600, 0
        archive.addfile(item, io.BytesIO(catalog))
    # XZは時刻やOS名をヘッダーへ含めず、標準ライブラリだけで展開できる。
    # 方式・圧縮率・検査方式を固定し、WindowsとWSLで同じ資材を照合する。
    data = lzma.compress(raw.getvalue(), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=9)
    if len(data) > MAX_INLINE_BYTES:
        raise ValueError('Portal source exceeds the inline build limit; use a scoped source artifact instead')
    return data, hashlib.sha256(data).hexdigest()


def build_targets(config, state):
    return image_build.build_targets(config, state, output='portal', command='build-portal')


def render_build_script(config, targets, data, source_hash, build_tag, root=REPOSITORY):
    if (not re.fullmatch(r'[a-z0-9]+\.azurecr\.io', targets['registryServer'])
            or not re.fullmatch(r'[0-9a-fA-F-]{36}', config['azureTenantId'])
            or not re.fullmatch(r'[0-9a-f]{64}', source_hash)
            or not re.fullmatch(r'build-[0-9a-f]{32}', build_tag)
            or len(data) > MAX_INLINE_BYTES or hashlib.sha256(data).hexdigest() != source_hash):
        raise ValueError('Invalid portal image build inputs')
    script = (Path(root) / 'apps/portal/deploy/build-image.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    for key, value in {'__REGISTRY__': targets['registryServer'], '__TENANT__': config['azureTenantId'],
                       '__SOURCE_BASE64__': base64.b64encode(data).decode(), '__SOURCE_SHA256__': source_hash,
                       '__BUILD_TAG__': build_tag}.items():
        script = script.replace(key, value)
    return script


def reusable_image(config, state, source_hash, path):
    """同じ環境・ソースで登録と一時資源回収に成功したイメージを再利用する。"""
    receipt = read_build_receipt(path)
    if not receipt:
        return None
    return image_build.reusable_digest(receipt, source_hash, build_targets(config, state)['commandId'])


def build_command_body(location, script):
    """ソースを含む要求全体を測り、Base64・JSONエスケープ後の本文上限を守る。"""
    body = {'location': location, 'properties': {
        'source': {'script': script}, 'asyncExecution': True, 'timeoutInSeconds': 2400,
        'treatFailureAsDeploymentFailure': True,
    }}
    if len(json.dumps(body).encode('utf-8')) >= MAX_COMMAND_BYTES:
        raise ValueError('Portal image Run Command exceeds the request size limit')
    return body


def parse_build_result(view, source_hash):
    return image_build.parse_build_result(view, source_hash, marker=MARKER, label='Portal')


def build_image(config, state, transport, *, data, source_hash, record, sleep=time.sleep,
                monotonic=time.monotonic, progress=print, timeout=2700, prior_receipt=None):
    if len(data) > MAX_INLINE_BYTES or hashlib.sha256(data).hexdigest() != source_hash:
        raise ValueError('Portal source archive checksum or size is invalid')
    targets = build_targets(config, state)
    prior_id = (prior_receipt or {}).get('commandId')
    if not owned_build_command(prior_id, targets['commandId']):
        prior_id = targets['commandId']
    prior_path = prior_id + '?api-version=' + COMPUTE_API
    prior = checked(transport, 'GET', prior_path + '&$expand=instanceView', allowed=(200, 404), label='Read image build operation')
    if prior:
        properties = prior.get('properties', {})
        view = properties.get('instanceView', {})
        if view.get('executionState') not in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
            raise ValueError('Portal image build is still pending; inspect the management VM Run Command before retrying')
        # Recreate a completed operation so its old instanceView cannot be taken as the new result.
        checked(transport, 'DELETE', prior_path, allowed=(200, 202, 204, 404), label='Remove completed image build command')
        for _ in range(60):
            if transport.request('GET', prior_path).get('statusCode') == 404:
                break
            sleep(2)
        else:
            raise ValueError('Completed portal image build command deletion is still pending')
    vm = checked(transport, 'GET', targets['vmId'] + '?api-version=' + COMPUTE_API, label='Read build VM')
    principal = vm.get('identity', {}).get('principalId', '')
    if str(vm.get('id', '')).lower() != targets['vmId'].lower() or not re.fullmatch(r'[0-9a-fA-F-]{36}', principal):
        raise ValueError('Build VM is missing its system managed identity')
    registry = checked(transport, 'GET', targets['registryId'] + '?api-version=' + REGISTRY_API, label='Read private registry')
    if (str(registry.get('id', '')).lower() != targets['registryId'].lower()
            or registry.get('properties', {}).get('loginServer') != targets['registryServer']):
        raise ValueError('Build registry differs from the configured registry')
    # 前回の回収だけが失敗した場合も、同じ記録にある一時ロールだけを回収してから再開する。
    previous_role = (prior_receipt or {}).get('temporaryRoleId')
    role_prefix = targets['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/'
    if ((prior_receipt or {}).get('temporaryResourcesRemoved') is False
            and owned_build_command((prior_receipt or {}).get('commandId'), targets['commandId'])
            and isinstance(previous_role, str)
            and re.fullmatch(re.escape(role_prefix) + r'[0-9a-fA-F-]{36}', previous_role)):
        previous_path = previous_role + '?api-version=' + ROLE_API
        previous = checked(transport, 'GET', previous_path, allowed=(200, 404), label='Read previous temporary AcrPush')
        if previous:
            properties = previous.get('properties', {})
            if (str(properties.get('principalId', '')).lower() != principal.lower()
                    or not str(properties.get('roleDefinitionId', '')).lower().endswith('/' + ACR_PUSH)):
                raise ValueError('Previous temporary role no longer matches the Portal build')
            checked(transport, 'DELETE', previous_path, allowed=(200, 202, 204, 404), label='Remove previous temporary AcrPush')
            for _ in range(60):
                if transport.request('GET', previous_path).get('statusCode') == 404:
                    break
                sleep(2)
            else:
                raise ValueError('Previous temporary AcrPush cleanup is still pending')
    run_id = uuid.uuid4().hex
    # A unique resource name prevents a recreated Run Command from reusing the VM agent's
    # previous execution state. The receipt still protects a running prior build on retry.
    command_id = targets['commandId'] + '-' + run_id[:12]
    command_path = command_id + '?api-version=' + COMPUTE_API
    view_path = command_path + '&$expand=instanceView'
    role_id = targets['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/' + str(uuid.uuid4())
    role_path = role_id + '?api-version=' + ROLE_API
    script = render_build_script(config, targets, data, source_hash, 'build-' + run_id)
    command_body = build_command_body(vm['location'], script)
    receipt = {'status': 'building', 'commandId': command_id, 'temporaryRoleId': role_id,
               'sourceSha256': source_hash, 'imageDigest': None}
    record(receipt)
    submitted, cleanup_errors, failure = False, [], None
    try:
        progress('ポータル: 管理VMへ一時的にAcrPushを付与します。', flush=True)
        checked(transport, 'PUT', role_path, {'properties': {
            'principalId': principal, 'principalType': 'ServicePrincipal',
            'roleDefinitionId': '/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Authorization/roleDefinitions/' + ACR_PUSH,
        }}, label='Grant temporary AcrPush')
        progress('ポータル: 管理VMでイメージを作成し、非公開ACRへ登録します。', flush=True)
        # submitted is set before sending: a lost PUT response must not leave an untracked root command.
        submitted = True
        checked(transport, 'PUT', command_path, command_body, label='Start portal image build')
        deadline, notice = monotonic() + timeout, monotonic() + 60
        while monotonic() < deadline:
            body = checked(transport, 'GET', view_path, label='Read image build status')
            properties = body.get('properties', {})
            view = properties.get('instanceView', {})
            if view.get('executionState') in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
                digest = parse_build_result(view, source_hash)
                receipt.update(status='built', imageDigest=digest)
                break
            if properties.get('provisioningState') == 'Failed':
                raise ValueError('Portal image Run Command provisioning failed')
            if monotonic() >= notice:
                progress('ポータル: イメージの作成・登録を継続中です。', flush=True)
                notice = monotonic() + 60
            sleep(10)
        else:
            raise ValueError('Portal image build exceeded the wait limit')
    except BaseException as error:
        failure = error
        receipt['status'] = 'failed'
        raise
    finally:
        # Remove this ARM command and only our unique role assignment.
        # A deleted ARM resource does not prove the guest process exited; a later build's
        # exclusive lock also guards against an interrupted guest process still running.
        for target, label, enabled in ((command_path, 'Remove image build command', submitted),
                                       (role_path, 'Remove temporary AcrPush', True)):
            if enabled:
                try:
                    # A rejected grant created nothing: avoid an unnecessary DELETE requiring
                    # permissions the caller may not have. An unknown PUT outcome is read back.
                    if target == role_path and transport.request('GET', target).get('statusCode') == 404:
                        continue
                    checked(transport, 'DELETE', target, allowed=(200, 202, 204, 404), label=label)
                    for _ in range(60):
                        observed = transport.request('GET', target)
                        if observed.get('statusCode') == 404:
                            break
                        if observed.get('statusCode') not in (200, 202):
                            raise ValueError(label + ' could not be verified')
                        sleep(2)
                    else:
                        raise ValueError(label + ' is still pending')
                except Exception:
                    cleanup_errors.append(label)
        receipt['temporaryResourcesRemoved'] = not cleanup_errors
        receipt['cleanupErrors'] = cleanup_errors
        record(receipt)
        if cleanup_errors:
            detail = (str(failure) + '; ') if failure is not None else ''
            raise ValueError(detail + 'Image build cleanup did not finish; inspect .local/<environment>/state/portal-build.json') from None
    return receipt['imageDigest']
