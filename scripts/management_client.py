#!/usr/bin/env python3
"""管理VM自身のIDで、構築した資源の保守データを保護ファイルへ取得する。

人間のAzure CLIログインや保存tokenは使用しない。実サービスの認可・通信成功は
各操作の実検証で確認する。入力inventoryは構築出力から生成した内部資材である。
"""
from __future__ import annotations

import os
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ARM_ORIGIN = 'https://management.azure.com'
ARM_AUDIENCE = ARM_ORIGIN + '/'
STORAGE_AUDIENCE = 'https://storage.azure.com/'
LOGS_ORIGIN = 'https://api.loganalytics.azure.com'
# The HTTP endpoint and Entra resource identifier are separate contracts. Keep
# the registered Log Analytics resource used by the official SDK/token examples
# while sending queries to the current Azure endpoint.
# https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/authentication-authorization
# https://learn.microsoft.com/en-us/python/api/azure-monitor-query/azure.monitor.query.logsqueryclient
LOGS_AUDIENCE = 'https://api.loganalytics.io'
IMDS_ORIGIN = 'http://169.254.169.254'
IMDS_TOKEN_API = '2018-02-01'
IMDS_COMPUTE_API = '2021-02-01'
APP_API = '2025-07-01'
JOB_API = '2023-11-02-preview'
JOB_METADATA_API = '2025-10-02-preview'
STORAGE_API = '2023-11-03'
# Wire paths are pinned to these public official source snapshots. Runtime
# authorization and success still require verification on the constructed VM.
AZURE_CLI_SOURCE = '51552fd56854d33f646a031bbbf024cf1fbc4d42'
AZURE_CLI_EXTENSION_SOURCE = '753f189436229fa116c5d232dde4f33cc69bd6b9'
# https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py
# https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/_ssh_utils.py
# https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/_clients.py
# https://github.com/Azure/azure-cli-extensions/blob/753f189436229fa116c5d232dde4f33cc69bd6b9/src/containerapp/azext_containerapp/custom.py
# https://github.com/Azure/azure-cli-extensions/blob/753f189436229fa116c5d232dde4f33cc69bd6b9/src/containerapp/azext_containerapp/_clients.py
GUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
SELECTOR = r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'
MAX_JSON_BYTES = 16 * 1024 * 1024


class MaintenanceError(RuntimeError):
    """token・本文・実資源IDをエラー出力へ含めない保守失敗。"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise MaintenanceError('The maintenance endpoint returned an unexpected redirect')


def _guid(value):
    return isinstance(value, str) and re.fullmatch(GUID, value) and value != '00000000-0000-0000-0000-000000000000'


def _plain_path(path):
    """親も含めてリンク・junctionを拒否し、公開checkoutへの出力を防ぐ。"""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for node in [path, *path.parents]:
        if node.exists() or node.is_symlink():
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise MaintenanceError('Maintenance files must not use links or reparse points')
    for parent in path.parents:
        if (parent / 'AGENTS.md').is_file() and (parent / 'infra').is_dir():
            relative = path.relative_to(parent)
            if not relative.parts or relative.parts[0] != '.local':
                raise MaintenanceError('Checkout maintenance files must remain under its protected .local directory')
            break
    return path


def _owner_only(path, *, directory=False):
    if os.name != 'posix':
        raise MaintenanceError('This maintenance client requires the Linux management VM')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)):
        raise MaintenanceError('Maintenance files require owner-only permissions')


def read_protected(path):
    path = _plain_path(path)
    _owner_only(path.parent, directory=True)
    _owner_only(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        actual, expected = os.fstat(stream.fileno()), path.lstat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise MaintenanceError('The protected maintenance input changed while opening')
        result = stream.read(MAX_JSON_BYTES + 1)
    if len(result) > MAX_JSON_BYTES:
        raise MaintenanceError('The protected maintenance input is too large')
    return result


@contextmanager
def protected_output(path):
    """保護済みフォルダー内の新規0600ファイルだけへ本文を保存する。"""
    path = _plain_path(path)
    _owner_only(path.parent, directory=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        _owner_only(path)
        actual, expected = os.fstat(stream.fileno()), path.lstat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise MaintenanceError('The protected maintenance output changed while opening')
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def load_inventory(path):
    return validate_inventory(json.loads(read_protected(path)))


def validate_inventory(inventory):
    """構築出力から生成する内部inventoryのscopeも、VM内と同じ規則で検査する。"""
    if (not isinstance(inventory, dict) or inventory.get('schemaVersion') != 1
            or not _guid(inventory.get('subscriptionId')) or not _guid(inventory.get('azureTenantId'))
            or not _guid(inventory.get('managementPrincipalId'))):
        raise MaintenanceError('A complete constructed-resource maintenance inventory is required')
    scope = '/subscriptions/' + inventory['subscriptionId'] + '/resourceGroups/'

    def resource(value, provider):
        pattern = re.escape(scope) + r'[A-Za-z0-9._()-]+/providers/' + re.escape(provider) + '/' + SELECTOR
        if not isinstance(value, str) or not re.fullmatch(pattern, value, re.IGNORECASE):
            raise MaintenanceError('Maintenance inventory contains an invalid or foreign resource scope')

    resource(inventory.get('managementVmId'), 'Microsoft.Compute/virtualMachines')
    for family, provider in (('applications', 'Microsoft.App/containerApps'),
                             ('jobs', 'Microsoft.App/jobs'), ('environments', 'Microsoft.App/managedEnvironments')):
        if not isinstance(inventory.get(family), list):
            raise MaintenanceError('Maintenance inventory lacks constructed ACA target families')
        for value in inventory[family]:
            resource(value, provider)
    workspace = inventory.get('workspace')
    if not isinstance(workspace, dict) or not _guid(workspace.get('customerId')):
        raise MaintenanceError('The constructed workspace query identity is required')
    resource(workspace.get('id'), 'Microsoft.OperationalInsights/workspaces')
    if not isinstance(inventory.get('auditStorage'), list):
        raise MaintenanceError('Constructed audit container targets are required')
    for account in inventory['auditStorage']:
        if not isinstance(account, dict):
            raise MaintenanceError('Constructed audit container targets are invalid')
        resource(account.get('id'), 'Microsoft.Storage/storageAccounts')
        if (not re.fullmatch(r'[a-z0-9]{3,24}', account['id'].rsplit('/', 1)[-1])
                or not isinstance(account.get('containers'), list)
                or not all(isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', name)
                           for name in account['containers'])):
            raise MaintenanceError('Constructed audit account or container targets are invalid')
    return inventory


def owned_target(inventory, family, resource_id):
    targets = [inventory['workspace']['id']] if family == 'workspace' else (
        [account['id'] for account in inventory['auditStorage']] if family == 'auditStorage' else inventory[family])
    if not isinstance(resource_id, str):
        raise MaintenanceError('Select an explicit constructed resource ID')
    found = [target for target in targets if target.casefold() == resource_id.casefold()]
    if len(found) != 1:
        raise MaintenanceError('The selected resource is not a unique constructed maintenance target')
    return found[0]


def selector(value):
    if not isinstance(value, str) or not re.fullmatch(SELECTOR, value) or value in ('.', '..'):
        raise MaintenanceError('Use an explicit revision, replica, execution or container name')
    return value


class HttpTransport:
    """TLSを検証し、proxy・redirect・環境認証cacheを使わないHTTP通信。"""
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def open(self, method, url, *, headers=None, body=None, timeout=30):
        try:
            return self.opener.open(Request(url, data=body, headers=headers or {}, method=method), timeout=timeout)
        except (HTTPError, URLError, OSError):
            raise MaintenanceError('The maintenance HTTP request failed; verify its scoped authorization and route') from None

    def json(self, method, url, *, headers=None, body=None):
        with self.open(method, url, headers=headers, body=body) as response:
            data = response.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise MaintenanceError('The maintenance metadata response is too large')
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise MaintenanceError('The maintenance endpoint did not return valid metadata') from None


class VmIdentity:
    """IMDSのSystemAssigned tokenだけをメモリーに保持する。"""
    def __init__(self, inventory, transport):
        self.inventory, self.transport, self.tokens = inventory, transport, {}
        self.verified = False

    def verify_vm(self):
        compute = self.transport.json('GET', IMDS_ORIGIN + '/metadata/instance/compute?'
            + urlencode({'api-version': IMDS_COMPUTE_API}), headers={'Metadata': 'true'})
        if (not isinstance(compute, dict)
                or str(compute.get('resourceId', '')).casefold() != self.inventory['managementVmId'].casefold()):
            raise MaintenanceError('This client must run on its constructed management VM')
        self.verified = True

    def token(self, audience):
        if audience not in (ARM_AUDIENCE, STORAGE_AUDIENCE, LOGS_AUDIENCE):
            raise MaintenanceError('The maintenance token audience is not approved')
        if not self.verified:
            self.verify_vm()
        cached = self.tokens.get(audience)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        value = self.transport.json('GET', IMDS_ORIGIN + '/metadata/identity/oauth2/token?'
            + urlencode({'api-version': IMDS_TOKEN_API, 'resource': audience}), headers={'Metadata': 'true'})
        try:
            token = value['access_token']
            payload = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            expiry = int(value['expires_on'])
            valid = (value['token_type'].casefold() == 'bearer' and value['resource'] == audience
                     and claims['oid'].casefold() == self.inventory['managementPrincipalId'].casefold()
                     and claims['tid'].casefold() == self.inventory['azureTenantId'].casefold()
                     and expiry > time.time() + 60)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError, UnicodeError):
            valid = False
        if not valid:
            raise MaintenanceError('IMDS did not return the constructed VM identity for the approved audience')
        self.tokens[audience] = (token, expiry)
        return token


def _headers(identity, audience, *, content_type=None):
    result = {'Authorization': 'Bearer ' + identity.token(audience)}
    if content_type:
        result['Content-Type'] = content_type
    return result


def _copy(response, output, *, limit=64 * 1024 * 1024):
    size = 0
    while True:
        chunk = response.read(min(65536, limit - size + 1))
        if not chunk:
            return size
        size += len(chunk)
        if size > limit:
            raise MaintenanceError('The maintenance output exceeded the selected bounded capture size')
        output.write(chunk)


def blob_read(inventory, resource_id, container, blob, identity, transport, output):
    target = owned_target(inventory, 'auditStorage', resource_id)
    account = next(item for item in inventory['auditStorage'] if item['id'] == target)
    if (container not in account['containers'] or not isinstance(blob, str) or not blob or len(blob) > 1024
            or blob.startswith('/') or '\\' in blob or any(segment in ('.', '..') for segment in blob.split('/'))
            or any(ord(character) < 32 for character in blob)):
        raise MaintenanceError('Select an explicit constructed audit container and Blob name')
    name = target.rsplit('/', 1)[-1]
    url = 'https://' + name + '.blob.core.windows.net/' + quote(container, safe='') + '/' + quote(blob, safe='/')
    headers = _headers(identity, STORAGE_AUDIENCE)
    headers['x-ms-version'] = STORAGE_API
    with transport.open('GET', url, headers=headers) as response:
        _copy(response, output)


def workspace_query(inventory, resource_id, query, identity, transport, output):
    owned_target(inventory, 'workspace', resource_id)
    if not isinstance(query, str) or not query.strip() or len(query.encode('utf-8')) > 1024 * 1024:
        raise MaintenanceError('Select an explicit bounded Log Analytics query file')
    url = LOGS_ORIGIN + '/v1/workspaces/' + inventory['workspace']['customerId'] + '/query'
    result = transport.json('POST', url, headers=_headers(identity, LOGS_AUDIENCE, content_type='application/json'),
                            body=json.dumps({'query': query}).encode('utf-8'))
    if not isinstance(result, dict) or result.get('error') or not isinstance(result.get('tables'), list):
        raise MaintenanceError('The saved-log query did not complete successfully')
    output.write((json.dumps(result, ensure_ascii=False) + '\n').encode('utf-8'))


def _arm_metadata(target, api, identity, transport, *, suffix=''):
    value = transport.json('GET', ARM_ORIGIN + target + suffix + '?' + urlencode({'api-version': api}),
                           headers=_headers(identity, ARM_AUDIENCE))
    if not isinstance(value, dict):
        raise MaintenanceError('The selected maintenance resource did not return valid metadata')
    if not suffix and str(value.get('id', '')).casefold() != target.casefold():
        raise MaintenanceError('The maintenance metadata belongs to another resource')
    return value


def _operation_token(target, api, identity, transport):
    value = transport.json('POST', ARM_ORIGIN + target + '/getAuthToken?' + urlencode({'api-version': api}),
                           headers=_headers(identity, ARM_AUDIENCE))
    token = value.get('properties', {}).get('token') if isinstance(value, dict) else None
    if not isinstance(token, str) or not token or any(character.isspace() for character in token):
        raise MaintenanceError('The selected resource did not return a valid short-lived operation token')
    return token


def _dev_endpoint(value):
    """ARMが返すAzure public Dev API endpointだけを採用する。"""
    if not isinstance(value, str):
        raise MaintenanceError('The official ACA Dev API endpoint is missing')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.azurecontainerapps.dev')
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.azurecontainerapps\.dev', parsed.hostname)
            or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or '%' in parsed.path or '\\' in parsed.path
            or any(segment in ('.', '..') for segment in parsed.path.split('/'))):
        raise MaintenanceError('The ACA operation endpoint is outside the approved public Dev API boundary')
    return value


def _dev_path(target, family):
    root, name = target.rsplit('/providers/', 1)
    return root + '/' + family + '/' + name.rsplit('/', 1)[-1]


def _dev_prefix(endpoint):
    endpoint = _dev_endpoint(endpoint)
    position = endpoint.find('/subscriptions/')
    if position < 0:
        raise MaintenanceError('The ACA endpoint lacks its documented resource-path boundary')
    return endpoint[:position]


def _log_capture(url, operation_token, tail, transport, output):
    _valid_tail(tail)
    # Follow is deliberately bounded to a finite tail request. No implicit
    # revision/replica/execution selection or live session is started.
    url = url + '?' + urlencode({'follow': 'false', 'output': 'json', 'tailLines': tail})
    with transport.open('GET', url, headers={'Authorization': 'Bearer ' + operation_token}) as response:
        _copy(response, output)


def _valid_tail(tail):
    if type(tail) is not int or not 0 <= tail <= 300:
        raise MaintenanceError('ACA log tail must be between zero and 300')


def _app_replica(target, revision, replica, container, identity, transport):
    revision, replica, container = map(selector, (revision, replica, container))
    suffix = '/revisions/' + revision + '/replicas/' + replica
    value = _arm_metadata(target, APP_API, identity, transport, suffix=suffix)
    if str(value.get('id', '')).casefold() != (target + suffix).casefold():
        raise MaintenanceError('The selected App replica belongs to another resource')
    containers = value.get('properties', {}).get('containers', [])
    selected = [item for item in containers if isinstance(item, dict) and item.get('name') == container]
    if len(selected) != 1:
        raise MaintenanceError('The selected App container is not present in its replica')
    return selected[0]


def app_logs(inventory, resource_id, kind, revision, replica, container, tail, identity, transport, output):
    _valid_tail(tail)
    target = owned_target(inventory, 'applications', resource_id)
    if kind == 'console':
        _app_replica(target, revision, replica, container, identity, transport)
    elif kind != 'system' or any(value is not None for value in (revision, replica, container)):
        raise MaintenanceError('App system logs do not accept console revision, replica or container selectors')
    metadata = _arm_metadata(target, APP_API, identity, transport)
    prefix = _dev_prefix(metadata.get('properties', {}).get('eventStreamEndpoint'))
    path = _dev_path(target, 'containerApps')
    if kind == 'console':
        path += '/revisions/' + revision + '/replicas/' + replica + '/containers/' + container + '/logstream'
    else:
        path += '/eventstream'
    token = _operation_token(target, APP_API, identity, transport)
    _log_capture(prefix + path, token, tail, transport, output)


def environment_logs(inventory, resource_id, tail, identity, transport, output):
    _valid_tail(tail)
    target = owned_target(inventory, 'environments', resource_id)
    metadata = _arm_metadata(target, APP_API, identity, transport)
    endpoint = metadata.get('properties', {}).get('eventStreamEndpoint')
    expected = _dev_path(target, 'managedEnvironments') + '/eventstream'
    if endpoint is None:
        # This fallback, only for a missing endpoint, follows the pinned official
        # stream_environment_logs implementation. It is not an inferred App URL.
        location = metadata.get('location')
        if not isinstance(location, str) or not re.fullmatch(r'[a-zA-Z0-9 ]{1,64}', location):
            raise MaintenanceError('The ACA environment lacks an official endpoint or valid Azure location')
        endpoint = 'https://' + location.replace(' ', '').lower() + '.azurecontainerapps.dev' + expected
    endpoint = _dev_endpoint(endpoint)
    if not urlsplit(endpoint).path.casefold().endswith(expected.casefold()):
        raise MaintenanceError('The environment stream endpoint belongs to another resource')
    token = _operation_token(target, APP_API, identity, transport)
    _log_capture(endpoint, token, tail, transport, output)


def job_logs(inventory, resource_id, execution, replica, container, tail, identity, transport, output):
    _valid_tail(tail)
    target = owned_target(inventory, 'jobs', resource_id)
    execution, replica, container = map(selector, (execution, replica, container))
    metadata = _arm_metadata(target, JOB_METADATA_API, identity, transport)
    containers = metadata.get('properties', {}).get('template', {}).get('containers', [])
    if not any(isinstance(item, dict) and item.get('name') == container for item in containers):
        raise MaintenanceError('The selected Job container is not part of its constructed template')
    replicas = _arm_metadata(target, JOB_API, identity, transport, suffix='/executions/' + execution + '/replicas')
    if not any(isinstance(item, dict) and item.get('name') == replica for item in replicas.get('value', [])):
        raise MaintenanceError('The selected replica is not part of the explicit Job execution')
    prefix = _dev_prefix(metadata.get('properties', {}).get('eventStreamEndpoint'))
    path = _dev_path(target, 'jobs') + '/executions/' + execution + '/replicas/' + replica + '/containers/' + container + '/logstream'
    token = _operation_token(target, JOB_API, identity, transport)
    _log_capture(prefix + path, token, tail, transport, output)


def app_exec(inventory, resource_id, revision, replica, container, command, seconds, identity, transport, output, *, connect=None):
    target = owned_target(inventory, 'applications', resource_id)
    if (not isinstance(command, str) or not command.strip() or len(command.encode('utf-8')) > 8192
            or '\0' in command or type(seconds) is not int or not 1 <= seconds <= 600):
        raise MaintenanceError('App exec requires an explicit bounded command file and capture duration')
    selected = _app_replica(target, revision, replica, container, identity, transport)
    prefix = _dev_prefix(selected.get('logStreamEndpoint'))
    url = prefix.replace('https://', 'wss://', 1) + _dev_path(target, 'containerApps')
    url += '/revisions/' + revision + '/replicas/' + replica + '/containers/' + container + '/exec?'
    url += urlencode({'command': command})
    token = _operation_token(target, APP_API, identity, transport)
    if connect is None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            raise MaintenanceError('Install the pinned maintenance WebSocket dependency before explicit App exec') from None
    try:
        from websockets.exceptions import ConnectionClosedOK
    except ImportError:
        ConnectionClosedOK = ()
    size, deadline = 0, time.monotonic() + seconds
    with connect(url, additional_headers={'Authorization': 'Bearer ' + token}, proxy=None,
                 open_timeout=30, close_timeout=5, max_size=65536, compression=None) as connection:
        connection.send(b'\x00\x04' + b'{"Width":80,"Height":24}')
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MaintenanceError('The explicit App exec capture reached its time limit; command exit status is unverified')
            try:
                frame = connection.recv(timeout=remaining)
            except ConnectionClosedOK:
                return
            except TimeoutError:
                raise MaintenanceError('The explicit App exec did not finish within the bounded capture window') from None
            if not frame:
                return
            if not isinstance(frame, bytes) or len(frame) < 2:
                raise MaintenanceError('The ACA exec endpoint returned an unsupported control frame')
            proxy, channel = frame[0], frame[1]
            if proxy == 0 and channel in (1, 2):
                data = frame[2:]
            elif proxy in (1, 2):
                data = frame[1:]
            else:
                raise MaintenanceError('The ACA exec endpoint returned an unsupported control channel')
            size += len(data)
            if size > 64 * 1024 * 1024:
                raise MaintenanceError('The explicit App exec output exceeded its capture limit')
            output.write(data)
            if proxy == 2:
                raise MaintenanceError('The ACA exec proxy reported an error; details are in protected output')


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--inventory', required=True, help='Constructed-resource protected JSON; never public parameters')
    value.add_argument('--output', required=True, help='New output file within an existing owner-only directory')
    commands = value.add_subparsers(dest='operation', required=True)
    blob = commands.add_parser('blob-read')
    blob.add_argument('--resource-id', required=True)
    blob.add_argument('--container', required=True)
    blob.add_argument('--blob', required=True)
    query = commands.add_parser('workspace-query')
    query.add_argument('--resource-id', required=True)
    query.add_argument('--query-file', required=True)
    app = commands.add_parser('app-logs')
    app.add_argument('--resource-id', required=True)
    app.add_argument('--type', choices=('console', 'system'), required=True)
    for name in ('revision', 'replica', 'container'):
        app.add_argument('--' + name)
    app.add_argument('--tail', type=int, default=50)
    environment = commands.add_parser('environment-logs')
    environment.add_argument('--resource-id', required=True)
    environment.add_argument('--tail', type=int, default=50)
    job = commands.add_parser('job-logs')
    job.add_argument('--resource-id', required=True)
    for name in ('execution', 'replica', 'container'):
        job.add_argument('--' + name, required=True)
    job.add_argument('--tail', type=int, default=50)
    execute = commands.add_parser('app-exec', help='Explicit App command; command exit status is not asserted by this protocol')
    execute.add_argument('--resource-id', required=True)
    for name in ('revision', 'replica', 'container'):
        execute.add_argument('--' + name, required=True)
    execute.add_argument('--command-file', required=True, help='Protected command file; command content is not passed in local process arguments')
    execute.add_argument('--seconds', type=int, default=60)
    return value


def main(argv=None):
    arguments = parser().parse_args(argv)
    inventory = load_inventory(arguments.inventory)
    transport = HttpTransport()
    identity = VmIdentity(inventory, transport)
    query = read_protected(arguments.query_file).decode('utf-8') if arguments.operation == 'workspace-query' else None
    command = read_protected(arguments.command_file).decode('utf-8').strip() if arguments.operation == 'app-exec' else None
    with protected_output(arguments.output) as output:
        if arguments.operation == 'blob-read':
            blob_read(inventory, arguments.resource_id, arguments.container, arguments.blob, identity, transport, output)
        elif arguments.operation == 'workspace-query':
            workspace_query(inventory, arguments.resource_id, query, identity, transport, output)
        elif arguments.operation == 'app-logs':
            app_logs(inventory, arguments.resource_id, arguments.type, arguments.revision, arguments.replica,
                     arguments.container, arguments.tail, identity, transport, output)
        elif arguments.operation == 'environment-logs':
            environment_logs(inventory, arguments.resource_id, arguments.tail, identity, transport, output)
        elif arguments.operation == 'job-logs':
            job_logs(inventory, arguments.resource_id, arguments.execution, arguments.replica, arguments.container,
                     arguments.tail, identity, transport, output)
        elif arguments.operation == 'app-exec':
            app_exec(inventory, arguments.resource_id, arguments.revision, arguments.replica, arguments.container,
                     command, arguments.seconds, identity, transport, output)
    print('The requested maintenance data was saved to the protected output file.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except MaintenanceError as error:
        raise SystemExit('Management client stopped: ' + str(error)) from None
    except Exception:
        raise SystemExit('Management client stopped; verify the protected input, scoped authorization and network route.') from None
