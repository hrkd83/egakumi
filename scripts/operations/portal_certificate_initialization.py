"""構築済み環境へ認証証明書の3部品だけを追加し、公開証明書を収集する。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit
import uuid
from contextlib import contextmanager

from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import LocalPaths, private_output, private_parent
from deployment.helpers.private_permissions import check_private_permissions, protect_new_path, regular_path
from deployment.helpers.portal_auth_certificate import (
    COMPUTE_API, collect_initial_certificate, read_certificate_state,
)
from deployment.lifecycle import run
from operations.common import OperationContext, OperationError, poll_delay

ROOT = Path(__file__).resolve().parents[2]
ARM_API = '2022-09-01'
# ARM guid()の公開namespace。環境から取得したIDではありません。
# https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/template-functions-string#guid
ARM_NAMESPACE = uuid.UUID('11fb06fb-712d-4ddd-98c7-e71bbd588830')
CERTIFICATES_OFFICER = 'a4417e6f-fecd-4de8-b567-7b0420556985'


def arm_guid(*values):
    """Bicepと同じ決定的なIDを算出し、変更予測の対象を固定します。"""
    return str(uuid.uuid5(ARM_NAMESPACE, '-'.join(values)))


def load_context(config_path, runtime_root=None):
    """読込み前に指定runtimeのリンク・所有者専用権限を確認します。"""
    root = Path(runtime_root).expanduser().absolute() if runtime_root is not None else ROOT
    # resolve()より前に、明示されたroot自体のリンク/reparseを拒否します。
    regular_path(root, directory=True)
    path = private_output(Path(config_path).absolute(), root)
    relative = path.relative_to(root)
    if len(relative.parts) != 3 or relative.parts[0] != '.local' or relative.parts[2] != 'params.local.json':
        raise OperationError('Select the exact protected parameters in the explicit runtime root')
    workspace = LocalPaths(root, relative.parts[1])
    for directory in (root / '.local', workspace.root, workspace.state):
        check_private_permissions(directory, directory=True)
    for target in (workspace.checked(path), workspace.checked(workspace.state / 'latest.json')):
        check_private_permissions(target)
    azure = private_output(root / '.azure/config.json', root)
    if azure.parent.exists():
        check_private_permissions(azure.parent, directory=True)
    if azure.exists():
        check_private_permissions(azure)
    configuration = load_configuration(path)
    if configuration['environmentName'] != workspace.environment:
        raise OperationError('The parameters differ from the selected protected environment')
    return OperationContext.load(path, runtime_root=root)


@contextmanager
def locked(ctx):
    """共通lockを使い、新しい空lockも本文を書込む前に実権限で保護します。"""
    path = ctx.workspace.checked(ctx.workspace.root / '.operations.lock')
    private_parent(path, ctx.workspace.repository)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        check_private_permissions(path)
    else:
        os.close(descriptor)
        protect_new_path(path)
    check_private_permissions(path)
    with ctx.locked():
        yield


def _read(ctx, path):
    """既存ファイルの実権限を検査し、本文を例外へ出さず読みます。"""
    target = ctx.workspace.checked(path)
    if not target.exists():
        return None
    check_private_permissions(target)
    return json.loads(target.read_text(encoding='utf-8'))


def _save(ctx, path, value):
    target = ctx.workspace.checked(path)
    if target.exists():
        check_private_permissions(target)
    ctx.save_private(target, value, overwrite=True)


def _group(ctx, key):
    target = '/subscriptions/' + ctx.config['subscriptionId'] + '/resourceGroups/' + ctx.config['resourceGroupNames'][key]
    response = ctx.request('GET', target, '2024-03-01')
    body = response.get('body') or {}
    if (response.get('statusCode') != 200 or str(body.get('id', '')).casefold() != target.casefold()
            or any(body.get('tags', {}).get(k) != ctx.config[v] for k, v in
                   (('product', 'productName'), ('environment', 'environmentName')))):
        raise OperationError('The certificate preparation resource group is not verified')
    return body['id']


def prerequisites(ctx):
    """既存Portal・VM・KVと証明書操作の前提設定をGETだけで照合します。"""
    config = ctx.config
    groups = {key: _group(ctx, key) for key in ('id', 'svc', 'maint')}
    vm_id = groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt'
    if ctx.state['outputs']['managementVm']['id'].casefold() != vm_id.casefold():
        raise OperationError('The management VM differs from Core state')
    vm = ctx.verify_resource(vm_id, COMPUTE_API, 'Microsoft.Compute/virtualMachines')
    vault = ctx.verify_resource(ctx.service_id('keyVault'), '2023-07-01', 'Microsoft.KeyVault/vaults')
    vault_id = groups['svc'] + '/providers/Microsoft.KeyVault/vaults/kv-' + config['resourceStem']
    if str(vault.get('id', '')).casefold() != vault_id.casefold() or vault.get('properties', {}).get('enableRbacAuthorization') is not True:
        raise OperationError('The selected Key Vault or its RBAC mode differs')
    principal = vm.get('identity', {}).get('principalId')
    if not isinstance(principal, str) or uuid.UUID(principal).int == 0:
        raise OperationError('The management VM identity is unavailable')
    for resource in (vm, vault):
        if resource.get('properties', {}).get('provisioningState') != 'Succeeded':
            raise OperationError('A certificate preparation dependency is not ready')
    portal_id = groups['svc'] + '/providers/Microsoft.App/containerApps/ca-' + config['resourceStem'] + '-portal'
    portal = ctx.verify_resource(portal_id, '2025-01-01', 'Microsoft.App/containerApps')
    if (portal.get('properties', {}).get('provisioningState') != 'Succeeded'
            or str(portal.get('properties', {}).get('managedEnvironmentId', '')).casefold()
            != (groups['svc'] + '/providers/Microsoft.App/managedEnvironments/cae-' + config['resourceStem']).casefold()):
        raise OperationError('The existing Portal environment is not ready')
    # ARMのinstanceViewを明示して再取得します。実runtimeはworker自身も検査します。
    tls_body = _command_view(ctx, vm_id + '/runCommands/configure-public-certificate')
    view = tls_body.get('properties', {}).get('instanceView') or tls_body.get('instanceView') or {}
    if (view.get('executionState') != 'Succeeded' or view.get('exitCode') != 0
            or '7.6.7' not in tls_body.get('properties', {}).get('source', {}).get('script', '')):
        raise OperationError('Complete the fixed PowerShell preparation before initialization')
    roles = ctx.request('GET', vault_id + '/providers/Microsoft.Authorization/roleAssignments', '2022-04-01')
    role_body = roles.get('body') or {}
    if roles.get('statusCode') != 200 or role_body.get('nextLink'):
        raise OperationError('The management certificate permissions cannot be verified')
    if not any(str(r.get('properties', {}).get('principalId', '')).casefold() == principal.casefold()
               and str(r.get('properties', {}).get('roleDefinitionId', '')).casefold().endswith('/' + CERTIFICATES_OFFICER)
               for r in role_body.get('value', [])):
        raise OperationError('The management certificate officer assignment is missing')
    return groups, vm_id, vault_id, principal


def _command_view(ctx, resource):
    """照合済みVMのexact commandだけを、instanceView付きで読みます。"""
    ctx._resource_path(resource)
    # 共通requestは単一api-versionだけを受けるため、許可済みパスへexpandを固定します。
    if ctx.transport is None:
        raise OperationError('The verified ARM transport is unavailable')
    try:
        response = ctx.transport.request('GET', resource + '?api-version=' + COMPUTE_API + '&$expand=instanceView')
    except Exception:
        raise OperationError('The exact certificate command response is unknown') from None
    body = response.get('body') or {}
    if response.get('statusCode') != 200 or str(body.get('id', '')).casefold() != resource.casefold():
        raise OperationError('The exact certificate command cannot be read')
    return body


def _compiler(relative):
    try:
        return run(['az', 'bicep', 'build', '--file', str(ROOT / relative), '--stdout', '--no-restore'])
    except Exception:
        raise OperationError('Compile the local initialization templates before proceeding') from None


def stages(ctx, groups, vm, vault, principal, execution_id, compiler=_compiler):
    """Coreと同じ3資材を生成し、4資源のID・要求を固定します。"""
    config = ctx.config
    stem = config['resourceStem']
    tags = {**config.get('tags', {}), 'product': config['productName'], 'environment': config['environmentName'],
            'azd-env-name': config['environmentName']}
    key = vault + '/keys/' + config['portal']['certificate']['certificateName']
    role = groups['svc'] + '/providers/Microsoft.Authorization/roleDefinitions/' + arm_guid(groups['svc'], 'portal-auth-key-operations')
    assignment = key + '/providers/Microsoft.Authorization/roleAssignments/' + arm_guid(key, principal, role)
    identity = groups['id'] + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-' + stem + '-portal-auth'
    command = vm + '/runCommands/initialize-portal-auth-certificate'
    permission = {'roleName': 'Portal authentication key operations ' + config['resourceGroupNames']['svc'],
                  'description': 'Read the authentication public key and disable an old key version.', 'type': 'CustomRole',
                  'assignableScopes': [groups['svc']], 'permissions': [{'actions': [], 'notActions': [],
                  'dataActions': ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/update/action'], 'notDataActions': []}]}
    worker_config = {'vaultName': 'kv-' + stem, 'certificateName': config['portal']['certificate']['certificateName'],
                     'validityMonths': config['portal']['certificate']['validityMonths']}
    script = (ROOT / 'infra/guest/portal-auth-certificate-bootstrap.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    worker = (ROOT / 'infra/guest/portal-auth-certificate.ps1').read_bytes().replace(b'\r\n', b'\n')
    script = script.replace('__PORTAL_AUTH_WORKER_BASE64__', base64.b64encode(worker).decode())
    definitions = [
        ('identity', 'apps/portal/deploy/auth-identity.bicep', groups['id'],
         {'stem': stem, 'location': config['location'], 'tags': tags},
         {identity: {'type': 'Microsoft.ManagedIdentity/userAssignedIdentities', 'location': config['location'], 'tags': tags}}),
        ('key-operations', 'infra/modules/portal-auth-key-operations.bicep', groups['svc'],
         {'vaultName': worker_config['vaultName'], 'keyName': worker_config['certificateName'], 'principalId': principal},
         {role: {'type': 'Microsoft.Authorization/roleDefinitions', 'properties': permission},
          assignment: {'type': 'Microsoft.Authorization/roleAssignments', 'properties': {
              'roleDefinitionId': role, 'principalId': principal, 'principalType': 'ServicePrincipal'}}}),
        ('certificate', 'infra/modules/portal-auth-certificate.bicep', groups['maint'],
         {'vmName': vm.rsplit('/', 1)[-1], 'location': config['location'], 'tags': tags, 'config': worker_config, 'executionId': execution_id},
         {command: {'type': 'Microsoft.Compute/virtualMachines/runCommands', 'location': config['location'],
                    'tags': {**tags, 'portalAuthExecution': execution_id}, 'properties': {
                        'source': {'script': script}, 'parameters': [{'name': 'PortalAuthConfigJson', 'value': json.dumps(worker_config, separators=(',', ':'))}],
                        'asyncExecution': False, 'timeoutInSeconds': 900, 'treatFailureAsDeploymentFailure': True}}}),
    ]
    result = []
    for label, source, group, parameters, resources in definitions:
        template = compiler(source)
        raw = template.get('resources', [])
        raw = list(raw.values()) if isinstance(raw, dict) else raw
        material = [r for r in raw if not r.get('existing')]
        if (len(material) != len(resources) or any(r.get('condition') is not None for r in material)
                or sorted(r['type'].casefold() for r in material) != sorted(r['type'].casefold() for r in resources.values())):
            raise OperationError('The initialization template exceeds its declared resources')
        result.append({'name': label, 'deploymentId': group + '/providers/Microsoft.Resources/deployments/' + stem + '-portal-init-' + label + '-' + execution_id[:12],
                       'body': {'properties': {'mode': 'Incremental', 'template': template,
                               'parameters': {k: {'value': v} for k, v in parameters.items()}}}, 'resources': resources})
    return result


def _matches(body, resource_id, desired, *, what_if=False):
    """ARMの空配列省略と付加metadataを許し、要求した権限・workerは照合します。"""
    if not isinstance(body, dict) or str(body.get('id', '')).casefold() != resource_id.casefold():
        return False
    def contains(actual, expected, field=None):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(contains(actual.get(k, [] if isinstance(v, list) and not v else None), v, k) for k, v in expected.items())
        if isinstance(expected, list):
            if not expected and actual is None:
                return True
            return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, e) for a, e in zip(actual, expected))
        # what-ifはprincipalTypeを省略する場合があります。principalIdとscopeは必須で照合し、
        # 実配備のreadbackではServicePrincipalを引き続き要求します。
        if (what_if and field == 'principalType' and actual is None and expected == 'ServicePrincipal'
                and desired['type'].endswith('/roleAssignments')):
            return True
        if field == 'value' and isinstance(expected, str):
            # Run Commandのstring(config)はARM側の空白・キー順が異なり得ます。
            try:
                return json.loads(actual) == json.loads(expected)
            except (ValueError, TypeError):
                return False
        if field == 'script' and isinstance(actual, str):
            return actual.replace('\r\n', '\n') == expected.replace('\r\n', '\n')
        if field in ('id', 'type', 'roleDefinitionId', 'principalId') and isinstance(actual, str):
            return actual.casefold() == expected.casefold()
        return actual == expected
    if desired['type'].endswith('/runCommands'):
        properties = body.get('properties') or {}
        if (properties.get('protectedParameters') or properties.get('runAsUser') or properties.get('runAsPassword')
                or properties.get('outputBlobUri') or properties.get('errorBlobUri')
                or set(properties.get('source', {})) != {'script'}):
            return False
    return contains(body, desired)


def verify_what_if(document, resources):
    """4資源以外の書込み、不完全な展開、既存資源の変更を拒否します。"""
    if not isinstance(document, dict) or document.get('status') != 'Succeeded':
        raise OperationError('The initialization what-if is incomplete')
    props = document.get('properties', {})
    for key in ('error', 'diagnostics', 'potentialChanges'):
        if document.get(key) or props.get(key):
            raise OperationError('The initialization what-if has unresolved diagnostics')
    changes = document.get('changes', props.get('changes'))
    if not isinstance(changes, list):
        raise OperationError('The initialization what-if lacks resource expansion')
    expected = {k.casefold(): (k, v) for k, v in resources.items()}
    seen = set()
    targets = set()
    for change in changes:
        if not isinstance(change, dict) or any(change.get(k) for k in ('error', 'diagnostics', 'potentialChanges', 'unsupportedReason')):
            raise OperationError('The initialization resource has unresolved diagnostics')
        identity = change.get('resourceId')
        if not isinstance(identity, str) or identity.casefold() in seen:
            raise OperationError('The initialization what-if has invalid or duplicate resources')
        seen.add(identity.casefold())
        action = change.get('changeType')
        if identity.casefold() not in expected:
            if action not in ('Ignore', 'NoChange'):
                raise OperationError('The initialization would write another resource')
            continue
        if action not in ('Create', 'NoChange'):
            raise OperationError('An initialization resource would be modified or deleted')
        resource, desired = expected[identity.casefold()]
        after = change.get('after') or (change.get('before') if action == 'NoChange' else None)
        if not _matches(after, resource, desired, what_if=True):
            raise OperationError('The initialization what-if differs from the exact request')
        targets.add(identity.casefold())
    if targets != set(expected):
        raise OperationError('The initialization what-if did not expand every declared resource')
    return {'resourcesVerified': len(targets)}


def what_if(ctx, stage, *, sleep=time.sleep, clock=time.monotonic, timeout=240):
    """what-ifの固定Managementホスト・同じsubscriptionの結果だけを読みます。"""
    response = ctx.request('POST', stage['deploymentId'] + '/whatIf', ARM_API,
                           {**stage['body'], 'properties': {**stage['body']['properties'], 'whatIfSettings': {'resultFormat': 'FullResourcePayloads'}}})
    deadline = clock() + timeout
    location = None
    while response.get('statusCode') == 202:
        location = next((v for k, v in response.get('headers', {}).items() if k.casefold() == 'location'), location)
        if not isinstance(location, str) or clock() >= deadline:
            raise OperationError('The initialization what-if remains pending')
        uri = urlsplit(location)
        query = parse_qs(uri.query, keep_blank_values=True)
        # ARMは署名付きのpolling contextをc/h/s/tへ付ける場合があります。
        # opaque値を変更・表示せず、同じホスト/Subscriptionの結果GETへだけ返します。
        valid_query = (set(query) in ({'api-version'}, {'api-version', 'c', 'h', 's', 't'})
                       and query.get('api-version') == [ARM_API]
                       and all(len(values) == 1 and 1 <= len(values[0]) <= 4096
                               and not any(ord(character) < 32 or ord(character) == 127 for character in values[0])
                               for values in query.values()))
        prefix = '/subscriptions/' + ctx.config['subscriptionId'] + '/'
        # ARMはSubscription直下、またはResources providerのlocation配下を返します。
        result_path = (uri.path.casefold().startswith(prefix.casefold())
                       and re.fullmatch(r'(?:operationresults|providers/Microsoft\.Resources/locations/[A-Za-z0-9-]+/(?:operationresults|operations))/[A-Za-z0-9-]+',
                                        uri.path[len(prefix):], re.IGNORECASE))
        if (len(location) > 32768 or uri.scheme != 'https' or uri.netloc != 'management.azure.com' or uri.fragment
                or not result_path
                or not valid_query):
            _save(ctx, ctx.workspace.root / 'evidence' / ('portal-auth-initial-' + stage['name'] + '-rejected-polling.json'), {'location': location})
            raise OperationError('The initialization what-if returned an unsupported polling URL')
        delay = poll_delay(response, 2)
        if delay > deadline - clock():
            raise OperationError('The initialization what-if remains pending')
        sleep(delay)
        try:
            response = ctx.transport.request('GET', location)
        except Exception:
            raise OperationError('The initialization what-if response is unknown') from None
    if response.get('statusCode') != 200:
        raise OperationError('The initialization what-if did not succeed')
    document = response.get('body')
    # 拒否した差分も同じ保護領域へ残し、表示せずに原因を確認できるようにします。
    _save(ctx, ctx.workspace.root / 'evidence' / ('portal-auth-initial-' + stage['name'] + '-what-if.json'), document)
    return verify_what_if(document, stage['resources'])


def _deployment(ctx, stage, receipt, path, *, sleep=time.sleep, clock=time.monotonic, timeout=1200):
    """PUT intentを先に保存し、再開時は同じ配備のGETだけで進めます。"""
    pending = receipt['deployments'].get(stage['name'])
    if pending is None:
        # 最初のPUT直前にも変更予測を再確認します。
        what_if(ctx, stage, sleep=sleep, clock=clock)
        receipt['deployments'][stage['name']] = {'status': 'submitted', 'deploymentId': stage['deploymentId']}
        _save(ctx, path, receipt)
        response = ctx.request('PUT', stage['deploymentId'], ARM_API, stage['body'])
        if response.get('statusCode') not in (200, 201, 202):
            raise OperationError('Initialization acceptance is unknown; retain the recorded deployment')
    elif pending.get('deploymentId') != stage['deploymentId']:
        raise OperationError('The saved initialization deployment differs')
    deadline = clock() + timeout
    while clock() < deadline:
        response = ctx.request('GET', stage['deploymentId'], ARM_API)
        body = response.get('body') or {}
        if response.get('statusCode') != 200 or str(body.get('id', '')).casefold() != stage['deploymentId'].casefold():
            raise OperationError('The saved initialization deployment cannot be confirmed; no PUT was repeated')
        status = body.get('properties', {}).get('provisioningState')
        if status in ('Failed', 'Canceled'):
            raise OperationError('The saved initialization deployment failed; review its checkpoint')
        if status == 'Succeeded':
            for resource, desired in stage['resources'].items():
                api = COMPUTE_API if desired['type'].endswith('/runCommands') else ('2023-01-31' if desired['type'].endswith('/userAssignedIdentities') else '2022-04-01')
                actual = ctx.verify_resource(resource, api, desired['type'])
                if not _matches(actual, resource, desired):
                    raise OperationError('An initialized resource differs from the recorded request')
            receipt['deployments'][stage['name']]['status'] = 'succeeded'
            _save(ctx, path, receipt)
            return
        delay = poll_delay(response)
        if delay > deadline - clock():
            break
        sleep(delay)
    raise OperationError('The saved initialization is still pending; no PUT was repeated')


def _collect(ctx, vm):
    try:
        collect_initial_certificate(ctx.config, {'managementVm': {'id': vm}, 'portal': {
            'authCertificateRunCommandId': vm + '/runCommands/initialize-portal-auth-certificate'}},
            ctx.transport, lambda p, v: _save(ctx, p, v), workspace=ctx.workspace)
    except OperationError:
        raise
    except Exception:
        raise OperationError('Initial public certificate collection did not complete; retain its checkpoint') from None


def initialize(ctx, *, execute=False, compiler=_compiler):
    """計画→固定要求→3部品→共通収集を、初回認証切替なしで進めます。"""
    groups, vm, vault, principal = prerequisites(ctx)
    path = ctx.workspace.state / 'portal-auth-initialization.json'
    receipt = _read(ctx, path)
    current_path = ctx.workspace.state / 'portal-auth-certificate.json'
    current = _read(ctx, current_path)
    if current is not None:
        state = read_certificate_state(ctx.config, ctx.workspace)
        if state.get('phase') != 'initial':
            raise OperationError('Finish the certificate rotation before initialization')
        if receipt is None and execute:
            _collect(ctx, vm)
        if receipt is None:
            return {'action': 'initialize', 'phase': 'initial', 'execute': execute, 'resourcesPlanned': 0, 'privateKeyExport': False, 'entraChangesAutomatic': False}
    if receipt is not None and (not isinstance(receipt, dict)
            or not isinstance(receipt.get('executionId'), str)
            or not isinstance(receipt.get('deployments'), dict)
            or receipt.get('phase') not in ('planned', 'collected')):
        raise OperationError('The saved initialization checkpoint is invalid')
    execution_id = receipt['executionId'] if receipt is not None else uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}', execution_id):
        raise OperationError('The saved initialization execution is invalid')
    planned = stages(ctx, groups, vm, vault, principal, execution_id, compiler)
    selection = {key: ctx.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem', 'environmentName')}
    fingerprint = hashlib.sha256(json.dumps(planned, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if receipt is None:
        receipt = {'schemaVersion': 1, 'selection': selection, 'executionId': execution_id, 'fingerprint': fingerprint,
                   'phase': 'planned', 'deployments': {}}
    elif (receipt.get('schemaVersion') != 1 or receipt.get('selection') != selection or receipt.get('fingerprint') != fingerprint):
        raise OperationError('The initialization input or source differs from its saved checkpoint')
    known = {stage['name']: stage['deploymentId'] for stage in planned}
    for name, deployment in receipt['deployments'].items():
        if (name not in known or not isinstance(deployment, dict)
                or deployment.get('deploymentId') != known[name]
                or deployment.get('status') not in ('submitted', 'succeeded')):
            raise OperationError('The saved initialization deployment is invalid')
    if receipt['phase'] == 'collected' and (current is None or set(receipt['deployments']) != set(known)
            or any(d['status'] != 'succeeded' for d in receipt['deployments'].values())):
        raise OperationError('The saved initialization collection is incomplete')
    # 全3部品の変更予測が揃ってから、最初の実PUTを許可します。
    for stage in planned:
        if stage['name'] not in receipt['deployments']:
            what_if(ctx, stage)
    _save(ctx, path, receipt)
    _save(ctx, ctx.workspace.root / 'evidence/portal-auth-initial-request.json', planned)
    if execute:
        for stage in planned:
            _deployment(ctx, stage, receipt, path)
        _collect(ctx, vm)
        receipt['phase'] = 'collected'
        _save(ctx, path, receipt)
    return {'action': 'initialize', 'phase': receipt['phase'], 'execute': execute,
            'resourcesPlanned': 4, 'privateKeyExport': False, 'entraChangesAutomatic': False}
