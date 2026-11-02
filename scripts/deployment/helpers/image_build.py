"""Portalと資格情報窓口に共通する、イメージ構築前後の照合。"""
from __future__ import annotations

import json
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


def reusable_digest(receipt, source_hash, command_prefix):
    """同じソースを構築し、一時権限の回収まで完了した記録だけを認める。"""
    if (receipt.get('status') != 'built' or receipt.get('sourceSha256') != source_hash
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
