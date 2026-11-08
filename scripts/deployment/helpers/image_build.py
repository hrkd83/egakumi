"""Portalと資格情報窓口に共通する、イメージ構築前後の照合。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re


def build_targets(config, state, *, output, command):
    """配備記録を照合し、構築に使う管理VMとACRの完全なIDを返す。"""
    base = '/subscriptions/' + config['subscriptionId']
    groups, stem = config['resourceGroupNames'], config['resourceStem']
    vm_id = base + '/resourceGroups/' + groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + stem + '-mgmt'
    registry = 'acr' + stem.replace('-', '')
    if (state['outputs'][output]['registryName'] != registry
            or state['outputs'].get('managementVm', {}).get('id', '').lower() != vm_id.lower()):
        raise ValueError('Core output does not identify the configured management VM and registry')
    return {'vmId': vm_id,
            'registryId': base + '/resourceGroups/' + groups['svc'] + '/providers/Microsoft.ContainerRegistry/registries/' + registry,
            'registryServer': registry + '.azurecr.io',
            'commandId': vm_id + '/runCommands/' + command}


def read_build_receipt(path):
    try:
        receipt = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return receipt if isinstance(receipt, dict) else {}


def owned_build_command(command_id, prefix):
    return isinstance(command_id, str) and (command_id == prefix
        or re.fullmatch(re.escape(prefix) + r'-[0-9a-f]{12}', command_id) is not None)


def registry_generation(registry, targets):
    """ARMが返すACRの作成日時で、同名の再作成を識別する。"""
    properties = registry.get('properties', {}) if isinstance(registry, dict) else {}
    if (not isinstance(registry, dict) or not isinstance(properties, dict)
            or str(registry.get('id', '')).casefold() != targets['registryId'].casefold()
            or properties.get('loginServer') != targets['registryServer']):
        raise ValueError('Build registry differs from the configured registry')
    value = properties.get('creationDate')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('Registry creation generation could not be verified')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('Registry creation generation could not be verified') from None
    # Azureの100ns桁を落とさず、UTC表記と末尾ゼロだけを正規化する。
    fraction = (match.group(2) or '').rstrip('0')
    created = parsed.strftime('%Y-%m-%dT%H:%M:%S') + ('.' + fraction if fraction else '') + 'Z'
    return {'resourceId': targets['registryId'].casefold(), 'createdAt': created}


def current_registry_generation(transport, targets):
    """再利用直前に対象ACRだけを読み、403や不明状態をキャッシュmissにしない。"""
    response = transport.request('GET', targets['registryId'] + '?api-version=2023-07-01')
    if not isinstance(response, dict):
        raise ValueError('Registry generation read could not be verified')
    if response.get('statusCode') == 404:
        return None
    if response.get('statusCode') != 200:
        raise ValueError('Registry generation read could not be verified')
    return registry_generation(response.get('body'), targets)


def reusable_digest(receipt, source_hash, command_prefix, *, registry_generation):
    """同じソースを構築し、一時権限の回収まで完了した記録だけを認める。"""
    if (not isinstance(registry_generation, dict) or receipt.get('registryGeneration') != registry_generation
            or receipt.get('status') != 'built' or receipt.get('sourceSha256') != source_hash
            or not owned_build_command(receipt.get('commandId'), command_prefix)
            or receipt.get('temporaryResourcesRemoved') is not True
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(receipt.get('imageDigest', '')))):
        return None
    return receipt['imageDigest']


def parse_build_result(view, source_hash, *, marker, label):
    """専用マーカー・実行結果・ソースhash・不変digestをまとめて照合する。"""
    values = []
    for line in view.get('output', '').splitlines():
        if line.startswith(marker):
            try:
                values.append(json.loads(line[len(marker):]))
            except ValueError:
                pass
    value = values[-1] if values else {}
    if view.get('executionState') != 'Succeeded' or view.get('exitCode') != 0:
        stage = value.get('stage', 'managed-run-command')
        if not isinstance(stage, str) or not re.fullmatch(r'[a-z-]+', stage):
            stage = 'managed-run-command'
        raise ValueError(label + ' image build failed at ' + stage)
    if (value.get('status') != 'built' or value.get('sourceSha256') != source_hash
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(value.get('imageDigest', '')))):
        raise ValueError(label + ' image build did not return a verified digest')
    return value['imageDigest']


def reconcile_start_503(transport, path, requested, response, receipt, record):
    """Startの503だけを同じ命令のGET一回で照合する。PUTや新命令は追加しない。"""
    if response.get('statusCode') != 503:
        return None
    receipt.update(startHttpStatus=503, startAcceptance='unconfirmed', start503Readback='unconfirmed')
    record(receipt)
    try:
        observed = transport.request('GET', path + '&$expand=instanceView')
    except Exception:
        return None  # 元503を呼出元が失敗にし、通常のfinallyで回収する。
    if not isinstance(observed, dict):
        return None
    status = observed.get('statusCode')
    receipt['startReadbackHttpStatus'] = status if type(status) is int and 100 <= status <= 599 else None
    receipt['start503Readback'] = 'mismatch' if status == 200 else 'not-confirmed'
    body = observed.get('body')
    properties = body.get('properties') if isinstance(body, dict) else None
    expected = requested['properties']
    same = lambda a, b: isinstance(a, str) and isinstance(b, str) and a.casefold() == b.casefold()
    matched = (type(status) is int and status == 200 and isinstance(body, dict) and isinstance(properties, dict)
        and same(body.get('id'), path.split('?', 1)[0])
        and same(body.get('type'), 'Microsoft.Compute/virtualMachines/runCommands')
        and body.get('name') == path.split('?', 1)[0].rsplit('/', 1)[1]
        and same(body.get('location'), requested['location'])
        and properties.get('source') == expected['source']
        and properties.get('asyncExecution') is expected['asyncExecution']
        and type(properties.get('timeoutInSeconds')) is int
        and properties['timeoutInSeconds'] == expected['timeoutInSeconds']
        and properties.get('treatFailureAsDeploymentFailure') is expected['treatFailureAsDeploymentFailure'])
    if matched:
        # Azureが返す既知の空の既定値だけを許す。実行主体・引数・出力先を追加しない。
        empty_lists = {'parameters', 'protectedParameters'}
        empty_text = {'runAsUser', 'runAsPassword', 'outputBlobUri', 'errorBlobUri'}
        known = set(expected) | {'provisioningState', 'instanceView'} | empty_lists | empty_text
        view = properties.get('instanceView', {})
        matched = (set(properties) <= known
            and all(properties.get(key) in (None, []) for key in empty_lists)
            and all(properties.get(key) in (None, '') for key in empty_text)
            and properties.get('provisioningState') in ('Creating', 'Updating', 'Succeeded', 'Failed')
            and isinstance(view, dict)
            and view.get('executionState') in ('Unknown', 'Pending', 'Running', 'Succeeded', 'Failed', 'TimedOut', 'Canceled'))
    if matched:
        receipt.update(startAcceptance='existing-command-matched', start503Readback='matched-existing-command')
    record(receipt)
    return body if matched else None
