"""管理VMが検証した公開証明書を取得し、Portalの固定参照を保護stateへ渡す。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .paths import local_paths, private_parent
from .private_permissions import check_private_permissions, protect_new_path

MARKER = 'EGAKUMI_PORTAL_AUTH:'
COMPUTE_API = '2024-07-01'


def certificate_record(value: dict, config: dict, *, allow_expired=False) -> dict:
    """取得元と版・期限・公開DERのthumbprintを照合し、想定外の項目を捨てます。"""
    try:
        vault = 'https://' + 'kv-' + config['resourceStem'] + '.vault.azure.net'
        name = config['portal']['certificate']['certificateName']
        for field, kind in (('certificateId', 'certificates'), ('keyId', 'keys')):
            prefix = vault + '/' + kind + '/' + name + '/'
            if not value[field].startswith(prefix) or not re.fullmatch('[a-f0-9]{32}', value[field][len(prefix):]):
                raise ValueError()
        der = base64.b64decode(value['publicCertificate'], validate=True)
        if not 128 <= len(der) <= 16384 or der[0] != 0x30:
            raise ValueError()
        thumbprint = hashlib.sha1(der).hexdigest()
        if thumbprint != value['certificateThumbprint']:
            raise ValueError()
        if (type(value['expires']) is not int or type(value['notBefore']) is not int
                or value['notBefore'] >= value['expires']
                or value['notBefore'] > time.time() + 300
                or not allow_expired and value['expires'] <= time.time()):
            raise ValueError()
        return {key: value[key] for key in ('certificateId', 'keyId', 'certificateThumbprint', 'notBefore', 'expires', 'publicCertificate')}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('Portal authentication certificate material is incomplete or differs from the selected vault') from None


def parse_worker_output(output: str, config: dict) -> dict:
    """Run Commandの出力から単一の公開metadataだけを読みます。本文は例外へ含めません。"""
    try:
        lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
        if len(lines) != 1 or len(lines[0]) > 24000:
            raise ValueError()
        return certificate_record(json.loads(lines[0]), config)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('The management VM did not return verified public certificate material') from None


def save_public_certificate(workspace, record: dict, filename='portal-auth-public.cer') -> None:
    """Entraへ渡す公開証明書だけを環境別generatedへ原子的に保存します。"""
    target = workspace.checked(workspace.root / 'generated' / filename)
    private_parent(target, workspace.repository)
    if target.exists():
        check_private_permissions(target)
    encoded = record['publicCertificate']
    pem = '-----BEGIN CERTIFICATE-----\n' + '\n'.join(encoded[i:i+64] for i in range(0, len(encoded), 64)) + '\n-----END CERTIFICATE-----\n'
    descriptor, name = tempfile.mkstemp(prefix='.public-certificate-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
            protect_new_path(temporary)
            stream.write(pem)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def collect_initial_certificate(config, outputs, transport, save_json, *, workspace=None) -> None:
    """初期資材が実行したexact Run Commandの終了を確認し、currentを勝手に置換しません。"""
    vm = outputs['managementVm']['id']
    expected_vm = ('/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['maint']
                   + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt')
    if vm.casefold() != expected_vm.casefold():
        raise ValueError('Initial certificate execution belongs to another management VM')
    resource = outputs['portal']['authCertificateRunCommandId']
    if resource != vm + '/runCommands/initialize-portal-auth-certificate':
        raise ValueError('Initial authentication certificate command differs from the recorded management VM')
    response = transport.request('GET', resource + '?api-version=' + COMPUTE_API + '&$expand=instanceView')
    body = response.get('body') or {}
    view = body.get('properties', {}).get('instanceView') or body.get('instanceView') or {}
    if (response.get('statusCode') != 200 or body.get('id') != resource
            or view.get('executionState') != 'Succeeded' or view.get('exitCode') != 0):
        raise ValueError('Initial authentication certificate issuance has not completed successfully')
    record = parse_worker_output(view.get('output', ''), config)
    workspace = workspace if workspace is not None else local_paths(config)
    store_initial_certificate(config, workspace, record, save_json)


def store_initial_certificate(config, workspace, record, save_json):
    """発行方法を問わず、検証済み公開証明書を既存のPortal配備記録へ結び付ける。"""
    record = certificate_record(record, config)
    path = workspace.checked(workspace.state / 'portal-auth-certificate.json')
    if path.exists():
        check_private_permissions(path)
        state = json.loads(path.read_text(encoding='utf-8'))
        validate_certificate_state(state, config)
        if state['current'] != record:
            raise ValueError('The current certificate differs from the protected state; recover the interrupted rotation first')
    else:
        save_json(path, {'subscriptionId': config['subscriptionId'], 'azureTenantId': config['azureTenantId'], 'resourceStem': config['resourceStem'], 'current': record, 'phase': 'initial'})
    save_public_certificate(workspace, record)


def validate_certificate_state(state, config):
    """別環境のstateと不完全な固定参照を受け付けません。"""
    if not isinstance(state, dict) or any(state.get(key) != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')):
        raise ValueError('Portal certificate state belongs to another environment')
    certificate_record(state.get('current'), config, allow_expired=True)
    for field in ('candidate', 'previous'):
        if field in state:
            certificate_record(state[field], config, allow_expired=field == 'previous')
    return state


def read_certificate_state(config, workspace):
    path = workspace.checked(workspace.state / 'portal-auth-certificate.json')
    check_private_permissions(path)
    return validate_certificate_state(json.loads(path.read_text(encoding='utf-8')), config)


def deployment_authentication(config, workspace, slot='current') -> dict:
    """手動Entra登録結果と生成stateを束ね、配備直前に必要な値を揃えます。"""
    auth = config['portal'].get('authentication')
    if not auth or not all(auth.get(key) for key in ('tenantId', 'clientId')):
        raise ValueError('Complete the dedicated Portal Entra registration before deploying the Portal image')
    state = read_certificate_state(config, workspace)
    if slot not in ('current', 'candidate', 'previous') or slot not in state:
        raise ValueError('The requested certificate deployment slot is unavailable')
    if slot == 'current' and state.get('phase') in ('switching', 'rolling-back'):
        raise ValueError('Complete the certificate switch or rollback before a normal Portal deployment')
    if slot == 'candidate' and state.get('phase') not in ('registered', 'switching'):
        raise ValueError('Confirm manual Entra certificate registration before switching')
    selected = certificate_record(state[slot], config)
    for field in ('keyId', 'certificateThumbprint'):
        if field in auth and auth[field] != selected[field]:
            raise ValueError('Explicit Portal certificate input differs from the selected protected state')
    return {**auth, 'keyId': selected['keyId'], 'certificateThumbprint': selected['certificateThumbprint']}
