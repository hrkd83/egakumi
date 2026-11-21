"""管理VMのSQL用SAMI client IDを内部配備入力として照合する。

Azureの呼出し・証跡保存は呼出し元が担当する。probeは新client資材を同梱するため、
既存VMに旧clientがあっても同じ検査を実行できる。tokenを返さない。
"""
from __future__ import annotations

import base64
import json
import re

from management_client import _guid, validate_inventory
from .paths import REPOSITORY

MARKER = 'CORE_MANAGEMENT_SQL_IDENTITY='


def render_sql_identity_probe(inventory, *, repository=REPOSITORY):
    inventory = validate_inventory(inventory)
    source_path = repository / 'scripts/management_client.py'
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError('The current public management client source is required')
    source = base64.b64encode(source_path.read_bytes()).decode('ascii')
    inputs = base64.b64encode(json.dumps(inventory, separators=(',', ':')).encode('utf-8')).decode('ascii')
    return ("#!/usr/bin/env bash\nset -euo pipefail\numask 077\nexport PYTHONDONTWRITEBYTECODE=1\n"
            "python3 -I -B - <<'PY_SQL_IDENTITY'\nimport base64,json,os,sys\n"
            "sys.dont_write_bytecode=True\nos.environ['PYTHONDONTWRITEBYTECODE']='1'\n"
            "try:\n    namespace={'__name__':'management_sql_probe'}\n"
            f"    exec(compile(base64.b64decode('{source}'),'management-client-source','exec'),namespace)\n"
            f"    inventory=namespace['validate_inventory'](json.loads(base64.b64decode('{inputs}')))\n"
            "    identity=namespace['VmIdentity'](inventory,namespace['HttpTransport']())\n"
            "    value=identity.sql_identity()\n"
            f"    print('{MARKER}'+json.dumps({{'schemaVersion':1,'status':'verified','identity':None}} | {{'identity':value}},separators=(',',':')))\n"
            "except Exception:\n"
            f"    print('{MARKER}'+json.dumps({{'schemaVersion':1,'status':'failed'}},separators=(',',':')))\n"
            "    raise SystemExit(3) from None\nPY_SQL_IDENTITY\n")


def read_sql_identity_probe(output, inventory, compute):
    """照合済みARM VM世代とguest IMDSの一意な成功記録だけを採用する。"""
    inventory = validate_inventory(inventory)
    if not isinstance(output, str):
        raise ValueError('The SQL identity probe did not return a valid private record')
    records = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if len(records) != 1:
        raise ValueError('The SQL identity probe must return one unambiguous private record')
    try:
        record = json.loads(records[0])
        identity = record['identity']
    except (ValueError, TypeError, KeyError):
        raise ValueError('The SQL identity probe did not complete its strict identity checks') from None
    expected = {'principalId': inventory['managementPrincipalId'], 'tenantId': inventory['azureTenantId'],
                'managementVmId': inventory['managementVmId']}
    if (set(record) != {'schemaVersion', 'status', 'identity'} or record.get('schemaVersion') != 1
            or record.get('status') != 'verified' or not isinstance(identity, dict)
            or set(identity) != {'sqlClientId', 'principalId', 'tenantId', 'managementVmId', 'vmId'}
            or not _guid(identity.get('sqlClientId')) or not _guid(identity.get('vmId'))
            or any(str(identity.get(key, '')).casefold() != value.casefold() for key, value in expected.items())):
        raise ValueError('The SQL identity probe belongs to a different constructed VM or tenant')
    if (not isinstance(compute, dict)
            or str(compute.get('id', '')).casefold() != inventory['managementVmId'].casefold()
            or str(compute.get('type', '')).casefold() != 'microsoft.compute/virtualmachines'
            or str(compute.get('identity', {}).get('principalId', '')).casefold() != inventory['managementPrincipalId'].casefold()
            or str(compute.get('identity', {}).get('tenantId', '')).casefold() != inventory['azureTenantId'].casefold()
            or 'systemassigned' not in re.split(r'\s*,\s*', str(compute.get('identity', {}).get('type', '')).casefold())
            or str(compute.get('properties', {}).get('vmId', '')).casefold() != identity['vmId'].casefold()
            or compute.get('properties', {}).get('provisioningState') != 'Succeeded'):
        raise ValueError('The SQL identity probe does not match the current ARM VM generation')
    return identity['sqlClientId']
