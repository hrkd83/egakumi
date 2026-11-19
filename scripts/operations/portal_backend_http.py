#!/usr/bin/env python3
"""既存PortalのHTTP80後段を準備・確認・切戻しする。新規構築は固定Bicepを使う。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.helpers.portal_backend_http import (
    APP_API, FW_API, _environment, _firewall, _gateway_environment, _read,
    ensure_portal_firewall_port, portal_ids, require_portal_http_core_ready,
    rollback_portal_backend,
)
from deployment.helpers.portal_gateway import NETWORK_API, _portal_backend
from operations.common import OperationContext, OperationError


class _Transport:
    """共通contextを使い、正確な所有ARMパスと固定API versionへ限定する。"""

    def __init__(self, context):
        self.context = context

    def request(self, method, path, body=None, headers=None):
        resource_id, separator, version = path.partition('?api-version=')
        if not separator or method not in {'GET', 'PUT'}:
            raise OperationError('Select a fixed Portal HTTP migration operation')
        return self.context.request(method, resource_id, version, body=body, headers=headers)


def run(context: OperationContext, *, command='status', execute=False):
    """親RG所有を確認し、現在の他設定を保った限定更新と保護記録を行う。"""
    if command not in {'status', 'prepare', 'rollback'} or command == 'status' and execute:
        raise OperationError('Select status, prepare, or rollback; only preparation and rollback can execute')
    ids = portal_ids(context.config)
    for key, api, kind in (
        ('environment', APP_API, 'Microsoft.App/managedEnvironments'),
        ('app', APP_API, 'Microsoft.App/containerApps'),
        ('gateway', NETWORK_API, 'Microsoft.Network/applicationGateways'),
        ('firewall', FW_API, 'Microsoft.Network/firewallPolicies/ruleCollectionGroups'),
    ):
        context.verify_resource(ids[key], api, kind)
    transport = _Transport(context)
    record = lambda event, value: context.record('portal-http-' + event, value)
    address, host = _environment(context.config, transport)
    _, gateway = _read(transport, ids['gateway'], NETWORK_API, 'Microsoft.Network/applicationGateways')
    _gateway_environment(context.config, gateway, address, host)
    if command == 'prepare':
        result = ensure_portal_firewall_port(context.config, transport, execute=execute, record=record)
        return {**result, 'nextStep': 'deploy-portal-then-verify-before-core', 'backendChanged': False}
    if command == 'rollback':
        return rollback_portal_backend(context.config, transport, execute=execute, record=record)
    backend, _ = _portal_backend(gateway, ids['gateway'])
    _, _, _, found = _firewall(context.config, transport)
    ready = False
    if backend['properties']['protocol'] == 'Http':
        require_portal_http_core_ready(context.config, transport)
        ready = True
    return {'status': 'observed', 'backendProtocol': backend['properties']['protocol'],
            'backendPort': backend['properties']['port'], 'firewallHttpReady': '80' in found,
            'previousHttpsAllowed': '443' in found, 'coreHttpContractReady': ready,
            'httpMigrationRequired': not ready, 'azureWrites': 0}


def main(argv=None):
    """既定は読取り、変更は選択環境の運用ロック内でのみ実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'prepare', 'rollback'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config)
        if args.execute:
            with context.locked():
                result = run(context, command=args.command, execute=True)
        else:
            result = run(context, command=args.command)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'failed', 'message': 'Portal HTTP migration stopped; inspect protected records and current resources'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
