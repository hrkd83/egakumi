"""構築VMのSQL用IDを毎回照合し、アプリ配備の内部入力へ返す。"""
from __future__ import annotations

import re
import time
import uuid

from management_client import _guid, validate_inventory
from .management_sql_identity import render_sql_identity_probe, read_sql_identity_probe

COMPUTE_API = '2024-07-01'
OWNER_TAG = {'purpose': 'management-sql-identity-discovery'}


def discover_management_sql_identity(config, inventory, transport, *, record,
                                     repository=None, sleep=time.sleep,
                                     monotonic=time.monotonic, timeout=300, prior_receipt=None):
    """一時readonly RunCommandを回収し、現VMのSAMI client IDだけを採用する。"""
    inventory = validate_inventory(inventory)
    if prior_receipt is not None and (not isinstance(prior_receipt, dict)
            or prior_receipt.get('temporaryCommandRemoved') is not True):
        raise ValueError('Reconcile the previous SQL identity command before another discovery')
    vm_id = inventory['managementVmId']
    expected = ('/subscriptions/' + config['subscriptionId'] + '/resourceGroups/'
                + config['resourceGroupNames']['maint']
                + '/providers/Microsoft.Compute/virtualMachines/vm-'
                + config['resourceStem'] + '-mgmt')
    if (vm_id.casefold() != expected.casefold()
            or inventory['subscriptionId'] != config['subscriptionId']
            or inventory['azureTenantId'] != config['azureTenantId']):
        raise ValueError('SQL identity discovery requires the constructed management VM')

    def request(method, path, body=None, *, allowed=(200, 201, 202, 204)):
        reply = transport.request(method, path, body)
        if reply.get('statusCode') not in allowed:
            raise ValueError('Management SQL identity ARM operation failed; raw output suppressed')
        return reply.get('body') or {}

    compute = request('GET', vm_id + '?api-version=' + COMPUTE_API)
    identity = compute.get('identity', {})
    if (str(compute.get('id', '')).casefold() != vm_id.casefold()
            or str(compute.get('type', '')).casefold() != 'microsoft.compute/virtualmachines'
            or str(identity.get('principalId', '')).casefold() != inventory['managementPrincipalId'].casefold()
            or str(identity.get('tenantId', '')).casefold() != inventory['azureTenantId'].casefold()
            or 'systemassigned' not in re.split(r'\s*,\s*', str(identity.get('type', '')).casefold())
            or not _guid(compute.get('properties', {}).get('vmId'))
            or compute.get('properties', {}).get('provisioningState') != 'Succeeded'):
        raise ValueError('Management SQL identity discovery VM ownership differs')
    options = {} if repository is None else {'repository': repository}
    script = render_sql_identity_probe(inventory, **options)
    command_id = vm_id + '/runCommands/sql-identity-' + uuid.uuid4().hex
    path = command_id + '?api-version=' + COMPUTE_API
    view_path = path + '&$expand=instanceView'
    receipt = {'schemaVersion': 1, 'status': 'prepared', 'commandId': command_id,
               'vmId': compute.get('properties', {}).get('vmId'),
               'temporaryCommandRemoved': False, 'runtimeSqlLoginVerified': False}
    record(receipt.copy())
    submitted = False
    client_id = None

    def owned_command(command):
        if (str(command.get('id', '')).casefold() != command_id.casefold()
                or str(command.get('type', '')).casefold() != 'microsoft.compute/virtualmachines/runcommands'
                or command.get('tags') != OWNER_TAG
                or command.get('properties', {}).get('source', {}).get('script') != script):
            raise ValueError('The temporary SQL identity command ownership differs')

    try:
        if transport.request('GET', path).get('statusCode') != 404:
            raise ValueError('The new SQL identity command already exists or could not be checked')
        submitted = True
        receipt['status'] = 'submission-requested'
        record(receipt.copy())
        request('PUT', path, {'location': compute['location'], 'tags': OWNER_TAG,
                'properties': {'source': {'script': script}, 'asyncExecution': True,
                               'timeoutInSeconds': 180, 'treatFailureAsDeploymentFailure': True}})
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            reply = transport.request('GET', view_path)
            if reply.get('statusCode') == 404:
                sleep(5)
                continue
            if reply.get('statusCode') != 200:
                raise ValueError('The SQL identity command could not be read')
            command = reply.get('body') or {}
            owned_command(command)
            view = command.get('properties', {}).get('instanceView', {})
            if view.get('executionState') in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
                output = view.get('output', '')
                if (view.get('executionState') != 'Succeeded' or view.get('exitCode') != 0
                        or not isinstance(output, str) or len(output) > 65536):
                    raise ValueError('The management SQL identity guest probe failed')
                current = request('GET', vm_id + '?api-version=' + COMPUTE_API)
                client_id = read_sql_identity_probe(output, inventory, current)
                if current.get('properties', {}).get('vmId') != receipt['vmId']:
                    raise ValueError('The management VM generation changed during SQL discovery')
                receipt.update(status='identity-verified', sqlClientId=client_id)
                record(receipt.copy())
                break
            if command.get('properties', {}).get('provisioningState') == 'Failed':
                raise ValueError('The SQL identity guest probe could not be provisioned')
            sleep(5)
        else:
            raise ValueError('Management SQL identity discovery timed out')
    except BaseException:
        receipt['status'] = 'failed-or-interrupted'
        record(receipt.copy())
        raise
    finally:
        if submitted:
            response = transport.request('GET', view_path)
            if response.get('statusCode') == 200:
                owned_command(response.get('body') or {})
                request('DELETE', path, allowed=(200, 202, 204, 404))
                for _ in range(60):
                    reply = transport.request('GET', path)
                    if reply.get('statusCode') == 404:
                        break
                    if reply.get('statusCode') not in (200, 202):
                        raise ValueError('Temporary SQL identity command removal could not be checked')
                    sleep(2)
                else:
                    raise ValueError('Temporary SQL identity command removal is pending')
            elif response.get('statusCode') == 404:
                # A lost PUT followed by 404 may precede asynchronous creation.
                # Retain the exact receipt; absence alone does not prove cleanup.
                receipt['status'] = 'cleanup-unknown'
                record(receipt.copy())
                raise ValueError('SQL identity command creation is unknown; inspect the retained receipt')
            else:
                raise ValueError('Temporary SQL identity command ownership could not be checked')
            receipt['temporaryCommandRemoved'] = True
            record(receipt.copy())
    return client_id
