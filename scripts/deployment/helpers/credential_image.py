"""公開ソースを既存管理VMへ渡し、非公開ACR用イメージを作る。"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import time
import uuid

from .paths import REPOSITORY
from . import image_build
from .image_build import owned_build_command, read_build_receipt

COMPUTE_API = '2024-07-01'
REGISTRY_API = '2023-07-01'
ROLE_API = '2022-04-01'
ACR_PUSH = '8311e382-0749-4cb8-b61a-304f252e45ec'
MARKER = 'CORE_CREDENTIAL_IMAGE_RESULT='


def source_bundle(root=REPOSITORY):
    """実行に必要な公開資材だけを梱包。params/secrets/ローカル状態は含めない。"""
    source = Path(root) / 'apps/gateway/credential-broker'
    if source.is_symlink() or any((source / name).is_symlink() for name in ('credential_broker', 'sql')):
        raise ValueError('Credential broker source directories must not be symbolic links')
    files = [source / name for name in ('Dockerfile', 'requirements.txt')]
    files += sorted((source / 'credential_broker').glob('*.py'))
    files += sorted((source / 'sql').glob('*.sql'))
    if not any(path.suffix == '.sql' for path in files) or not any(path.name == 'app.py' for path in files):
        raise ValueError('Credential broker build sources are missing')
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        for path in files:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Credential broker source must be a regular file')
            data = path.read_text(encoding='utf-8').replace('\r\n', '\n').encode()
            item = tarfile.TarInfo(path.relative_to(source).as_posix())
            item.size, item.mode, item.mtime = len(data), 0o600, 0
            archive.addfile(item, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), mtime=0)
    if len(data) > 400_000:
        raise ValueError('Credential broker source exceeds the inline build limit')
    return data, hashlib.sha256(data).hexdigest()


def build_targets(config, state):
    return image_build.build_targets(config, state, output='credentialBroker', command='build-credential-broker')


def render_build_script(config, targets, data, source_hash, build_tag, root=REPOSITORY):
    if (not re.fullmatch(r'[a-z0-9]+\.azurecr\.io', targets['registryServer'])
            or not re.fullmatch(r'[0-9a-fA-F-]{36}', config['azureTenantId'])
            or not re.fullmatch(r'[0-9a-f]{64}', source_hash)
            or not re.fullmatch(r'build-[0-9a-f]{32}', build_tag)):
        raise ValueError('Invalid credential image build inputs')
    script = (Path(root) / 'guest/build-credential-broker.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    for key, value in {'__REGISTRY__': targets['registryServer'], '__TENANT__': config['azureTenantId'],
                       '__SOURCE_BASE64__': base64.b64encode(data).decode(), '__SOURCE_SHA256__': source_hash,
                       '__BUILD_TAG__': build_tag}.items():
        script = script.replace(key, value)
    return script


def reusable_image(config, state, source_hash, path, *, transport=None):
    """実行時に現在のACR世代まで確認できた成功イメージだけを再利用する。"""
    receipt = read_build_receipt(path)
    # 既定の計画はネットワーク0。旧形式の世代なしreceiptも成功へ読み替えない。
    if transport is None or not receipt or not isinstance(receipt.get('registryGeneration'), dict):
        return None
    targets = build_targets(config, state)
    generation = image_build.current_registry_generation(transport, targets)
    return image_build.reusable_digest(receipt, source_hash, targets['commandId'], registry_generation=generation)


def checked(transport, method, path, body=None, *, allowed=(200, 201, 202, 204), label):
    return checked_response(transport.request(method, path, body), allowed=allowed, label=label)


def checked_response(response, *, allowed=(200, 201, 202, 204), label):
    """既に受けたARM応答を検証し、変更要求を再送しない。"""
    if response.get('statusCode') not in allowed:
        def codes(error, depth=0):
            if not isinstance(error, dict) or depth > 4:
                return []
            code = error.get('code', '')
            result = [code] if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', code) else []
            details = error.get('details')
            for child in details[:5] if isinstance(details, list) else []:
                result.extend(codes(child, depth + 1))
            return result
        error = response.get('body')
        error = error.get('error') if isinstance(error, dict) else None
        chain = list(dict.fromkeys(codes(error)))[:10]
        code = ': ' + ' > '.join(chain) if chain else ''
        raise ValueError(f'{label} failed (HTTP {response.get("statusCode", 0)}{code})')
    # ARM returns a nonempty {error: ...} document for an absent resource.
    # An explicitly allowed 404 is absence, never an existing pending operation.
    if response.get('statusCode') == 404:
        return {}
    return response.get('body') or {}


def parse_build_result(view, source_hash):
    return image_build.parse_build_result(view, source_hash, marker=MARKER, label='Credential')


def build_image(config, state, transport, *, data, source_hash, record, sleep=time.sleep,
                monotonic=time.monotonic, progress=print, timeout=2700, prior_receipt=None):
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
            raise ValueError('Credential image build is still pending; inspect the management VM Run Command before retrying')
        # Recreate a completed operation so its old instanceView cannot be taken as the new result.
        checked(transport, 'DELETE', prior_path, allowed=(200, 202, 204, 404), label='Remove completed image build command')
        for _ in range(60):
            if transport.request('GET', prior_path).get('statusCode') == 404:
                break
            sleep(2)
        else:
            raise ValueError('Completed credential image build command deletion is still pending')
    vm = checked(transport, 'GET', targets['vmId'] + '?api-version=' + COMPUTE_API, label='Read build VM')
    principal = vm.get('identity', {}).get('principalId', '')
    if str(vm.get('id', '')).lower() != targets['vmId'].lower() or not re.fullmatch(r'[0-9a-fA-F-]{36}', principal):
        raise ValueError('Build VM is missing its system managed identity')
    registry = checked(transport, 'GET', targets['registryId'] + '?api-version=' + REGISTRY_API, label='Read private registry')
    if (str(registry.get('id', '')).lower() != targets['registryId'].lower()
            or registry.get('properties', {}).get('loginServer') != targets['registryServer']):
        raise ValueError('Build registry differs from the configured registry')
    registry_generation = image_build.registry_generation(registry, targets)
    run_id = uuid.uuid4().hex
    # A unique resource name prevents a recreated Run Command from reusing the VM agent's
    # previous execution state. The receipt still protects a running prior build on retry.
    command_id = targets['commandId'] + '-' + run_id[:12]
    command_path = command_id + '?api-version=' + COMPUTE_API
    view_path = command_path + '&$expand=instanceView'
    role_id = targets['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/' + str(uuid.uuid4())
    role_path = role_id + '?api-version=' + ROLE_API
    receipt = {'status': 'building', 'commandId': command_id, 'temporaryRoleId': role_id,
               'sourceSha256': source_hash, 'imageDigest': None, 'registryGeneration': registry_generation}
    record(receipt)
    submitted, cleanup_errors, failure = False, [], None
    try:
        progress('認証窓口: 管理VMへ一時的にAcrPushを付与します。', flush=True)
        checked(transport, 'PUT', role_path, {'properties': {
            'principalId': principal, 'principalType': 'ServicePrincipal',
            'roleDefinitionId': '/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Authorization/roleDefinitions/' + ACR_PUSH,
        }}, label='Grant temporary AcrPush')
        script = render_build_script(config, targets, data, source_hash, 'build-' + run_id)
        progress('認証窓口: 管理VMでイメージを作成し、非公開ACRへ登録します。', flush=True)
        # submitted is set before sending: a lost PUT response must not leave an untracked root command.
        submitted = True
        command_body = {'location': vm['location'], 'properties': {
            'source': {'script': script}, 'asyncExecution': True, 'timeoutInSeconds': 2400,
            'treatFailureAsDeploymentFailure': True,
        }}
        started = transport.request('PUT', command_path, command_body)
        deadline, notice = monotonic() + timeout, monotonic() + 60
        observed = image_build.reconcile_start_503(transport, command_path, command_body, started, receipt, record)
        if observed is None:
            checked_response(started, label='Start credential image build')
        while monotonic() < deadline:
            body = observed if observed is not None else checked(transport, 'GET', view_path, label='Read image build status')
            observed = None
            properties = body.get('properties', {})
            view = properties.get('instanceView', {})
            if view.get('executionState') in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
                digest = parse_build_result(view, source_hash)
                receipt.update(status='built', imageDigest=digest)
                break
            if properties.get('provisioningState') == 'Failed':
                raise ValueError('Credential image Run Command provisioning failed')
            if monotonic() >= notice:
                progress('認証窓口: イメージの作成・登録を継続中です。', flush=True)
                notice = monotonic() + 60
            sleep(10)
        else:
            raise ValueError('Credential image build exceeded the wait limit')
    except BaseException as error:
        failure = error
        receipt['status'] = 'failed'
        raise
    finally:
        # Delete this command (also terminates a timed-out build), then only our unique role assignment.
        # Existing roles and all other Run Commands remain untouched.
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
            raise ValueError(detail + 'Image build cleanup did not finish; inspect .local/<environment>/state/credential-broker-build.json') from None
    return receipt['imageDigest']
