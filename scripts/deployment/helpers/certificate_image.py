"""証明書Jobの公開ソースを、既存の管理VMから非公開ACRへ投入する。"""
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

from management_client import IMDS_ORIGIN

from . import credential_image, image_build
from .paths import REPOSITORY
from .private_permissions import check_private_permissions

MARKER = 'CORE_CERTIFICATE_IMAGE_RESULT='
IMAGE_REPOSITORY = 'certificate-jobs'
REGISTRY_API = '2025-11-01'


def source_bundle(root=REPOSITORY):
    """Workerに必要な固定ファイルだけを梱包し、VMへソースを配布する。"""
    root = Path(root).resolve()
    common = root / 'apps/certificate-jobs'
    sources = [common / name for name in ('Dockerfile', 'job.py', 'certificate-job.ps1', 'staging-ca.crt')]
    sources += [root / 'infra/guest' / name for name in ('certificate.ps1', 'portal-auth-certificate.ps1')]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        for path in sources:
            if (path.resolve() != path or not path.is_file() or path.stat().st_size > 400_000):
                raise ValueError('Certificate image source is not an approved regular file')
            data = path.read_bytes().replace(b'\r\n', b'\n')
            item = tarfile.TarInfo(path.name)
            item.size, item.mode, item.mtime = len(data), 0o600, 0
            archive.addfile(item, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), mtime=0)
    if len(data) > 400_000:
        raise ValueError('Certificate image archive exceeds the inline build limit')
    return data, hashlib.sha256(data).hexdigest()


def ensure_private_registry(registry):
    """公開アクセスも例外も無効の専用データエンドポイント付きACRを確認する。"""
    properties = registry.get('properties') or {}
    policy = (properties.get('policies') or {}).get('azureADAuthenticationAsArmPolicy') or {}
    if (registry.get('sku', {}).get('name') != 'Premium'
            or properties.get('publicNetworkAccess') != 'Disabled'
            or properties.get('networkRuleBypassOptions') != 'None'
            or properties.get('adminUserEnabled') is not False
            or properties.get('dataEndpointEnabled') is not True
            or str(properties.get('roleAssignmentMode', '')).casefold() != 'legacyregistrypermissions'
            or str(policy.get('status', '')).casefold() != 'enabled'):
        raise ValueError('The private registry configuration, RBAC mode, or ARM audience policy differs from the supported certificate image path')


def targets(config, state):
    return image_build.build_targets(config, state, output='certificateBootstrap', command='build-certificate-jobs')


def render_build_script(config, target, data, source_hash, build_tag, root=REPOSITORY):
    if (not re.fullmatch(r'[a-z0-9]+\.azurecr\.io', target['registryServer'])
            or not re.fullmatch(r'[0-9a-fA-F-]{36}', config['azureTenantId'])
            or not re.fullmatch(r'[0-9a-f]{64}', source_hash)
            or not re.fullmatch(r'build-[0-9a-f]{32}', build_tag)):
        raise ValueError('Invalid certificate image build inputs')
    path = Path(root).resolve() / 'apps/certificate-jobs/deploy/build-image.sh'
    if not path.is_file() or path.resolve() != path:
        raise ValueError('Certificate image build script is not an approved regular file')
    script = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    values = {'__REGISTRY__': target['registryServer'], '__TENANT__': config['azureTenantId'],
              '__SOURCE_BASE64__': base64.b64encode(data).decode(), '__SOURCE_SHA256__': source_hash,
              '__BUILD_TAG__': build_tag, '__IMDS_ORIGIN__': IMDS_ORIGIN}
    for key, value in values.items():
        if key not in script:
            raise ValueError('Certificate image build script is missing a protected template marker')
        script = script.replace(key, value)
    return script


def parse_build_result(view, source_hash):
    if view.get('executionState') == 'Succeeded' and type(view.get('exitCode')) is not int:
        raise ValueError('Certificate image build did not return a valid exit code')
    return image_build.parse_build_result(view, source_hash, marker=MARKER, label='Certificate')


def _reconcile_build(config, target, prior, transport, record, *, sleep=time.sleep, monotonic=time.monotonic):
    """中断した始動を別実行へ読み替えず、今回保存したビルドと一時権限を回収する。"""
    command = prior.get('commandId')
    role = prior.get('temporaryRoleId')
    role_scope = target['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/'
    if not image_build.owned_build_command(command, target['commandId']) or not isinstance(role, str) or not re.fullmatch(
            re.escape(role_scope) + r'[0-9a-f-]{36}', role):
        raise ValueError('The interrupted image build does not identify the owned command and role')
    try:
        if str(uuid.UUID(role[len(role_scope):])) != role[len(role_scope):]:
            raise ValueError()
    except ValueError:
        raise ValueError('The interrupted image build role ID is invalid') from None
    vm = credential_image.checked(transport, 'GET', target['vmId'] + '?api-version=' + credential_image.COMPUTE_API,
                                  label='Verify the interrupted certificate build VM')
    principal = vm.get('identity', {}).get('principalId')
    if (str(vm.get('id', '')).casefold() != target['vmId'].casefold()
            or not isinstance(principal, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', principal)):
        raise ValueError('The interrupted certificate image build VM could not be verified')
    command_path = command + '?api-version=' + credential_image.COMPUTE_API
    role_path = role + '?api-version=' + credential_image.ROLE_API
    view = credential_image.checked(transport, 'GET', command_path + '&$expand=instanceView',
                                    allowed=(200, 404), label='Read the interrupted certificate build')
    assignment = credential_image.checked(transport, 'GET', role_path, allowed=(200, 404),
                                          label='Read the interrupted certificate build role')
    if assignment:
        properties = assignment.get('properties') or {}
        role_def = ('/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Authorization/roleDefinitions/'
                    + credential_image.ACR_PUSH)
        if (str(assignment.get('id', '')).casefold() != role.casefold()
                or str(properties.get('scope', '')).casefold() != target['registryId'].casefold()
                or str(properties.get('principalId', '')).casefold() != principal.casefold()
                or str(properties.get('roleDefinitionId', '')).casefold() != role_def.casefold()):
            raise ValueError('The interrupted certificate build role differs from the intended principal or registry')
    if view:
        if str(view.get('id', '')).casefold() != command.casefold():
            raise ValueError('The interrupted certificate build belongs to another command')
        instances = view.get('properties', {}).get('instanceView', {})
        state = instances.get('executionState')
        if state not in ('Succeeded', 'Failed', 'Canceled', 'TimedOut'):
            raise ValueError('The saved certificate build is still pending; no additional build was started')
        try:
            digest = parse_build_result(instances, prior['sourceSha256'])
        except ValueError:
            prior.update(status='failed', imageDigest=None)
        else:
            prior.update(status='built', imageDigest=digest)
    elif prior.get('status') != 'built':
        prior.update(status='failed', imageDigest=None)
    record(prior)
    for path, found in ((command_path, view), (role_path, assignment)):
        if not found:
            continue
        credential_image.checked(transport, 'DELETE', path, allowed=(200, 202, 204, 404),
                                  label='Recover temporary certificate build resources')
        deadline = monotonic() + 120
        while monotonic() < deadline:
            response = transport.request('GET', path)
            if response.get('statusCode') == 404:
                break
            if response.get('statusCode') not in (200, 202):
                raise ValueError('Temporary certificate build resource recovery is unconfirmed')
            sleep(min(3, max(0, deadline - monotonic())))
        else:
            raise ValueError('Temporary certificate build resource recovery is pending')
    prior['temporaryResourcesRemoved'] = True
    prior['cleanupErrors'] = []
    record(prior)
    return prior


def build_or_reuse(config, outputs, transport, save_json, workspace, *, repository=REPOSITORY, **timing):
    state = {'outputs': outputs}
    target = targets(config, state)
    registry = credential_image.checked(transport, 'GET', target['registryId'] + '?api-version=' + REGISTRY_API,
                                        label='Read private certificate registry')
    ensure_private_registry(registry)
    generation = image_build.registry_generation(registry, target)
    data, source_hash = source_bundle(repository)
    path = workspace.checked(workspace.state / 'certificate-image-build.json')
    if path.exists():
        check_private_permissions(path)
    prior = image_build.read_build_receipt(path)
    if prior and prior.get('temporaryResourcesRemoved') is not True:
        prior = _reconcile_build(config, target, prior, transport, lambda value: save_json(path, value), **timing)
    reuse = image_build.reusable_digest(prior, source_hash, target['commandId'], registry_generation=generation)
    if reuse:
        return reuse
    return credential_image.build_image(
        config, state, transport, data=data, source_hash=source_hash, prior_receipt=prior,
        record=lambda result: save_json(path, result), registry_api=REGISTRY_API, **timing,
        pipeline=(targets, lambda c, t, d, h, b: render_build_script(c, t, d, h, b, repository),
                  parse_build_result, ensure_private_registry, '証明書Job'))
