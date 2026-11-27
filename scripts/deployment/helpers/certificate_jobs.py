"""非公開ACRの証明書Jobを配備し、対象実行と公開証明書の結果を照合する。"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from management_client import IMDS_ORIGIN, STORAGE_AUDIENCE

from . import certificate_image
from .credential_image import checked, checked_response
from .paths import REPOSITORY
from .portal_auth_certificate import certificate_record
from .private_permissions import check_private_permissions

APP_API = '2025-07-01'
COMPUTE_API = '2024-07-01'
ARM_API = '2022-09-01'
BLOB_READER = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
ATTEMPT = re.compile(r'[0-9a-f]{32}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
EXECUTION = re.compile(r'[a-zA-Z0-9-]{1,100}')
VM_RESULT = 'EGAKUMI_CERT_JOB_RESULT='
ACTIONS = {'tls': {'initial', 'renew', 'check'},
           'portal': {'initial', 'prepare', 'inspect', 'activate', 'retire'}}


def _guid(value):
    try:
        return str(uuid.UUID(value)) == value.lower() and uuid.UUID(value).int != 0
    except (ValueError, AttributeError, TypeError):
        return False


def _load(path):
    if not path.exists():
        return {}
    check_private_permissions(path)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            return raw
    except (ValueError, OSError):
        pass
    raise ValueError('The protected certificate state is incomplete; review it before retrying')


def _subscription(config):
    if not _guid(config['subscriptionId']):
        raise ValueError('Certificate jobs require the selected Azure subscription')
    return '/subscriptions/' + config['subscriptionId']


def _targets(config, outputs):
    """対象を名前だけで推測せず、構築出力と同じ環境の固定IDを照合する。"""
    try:
        subscription = _subscription(config)
        b = outputs['certificateBootstrap']
        svc = subscription + '/resourceGroups/' + config['resourceGroupNames']['svc']
        vm = subscription + '/resourceGroups/' + config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt'
        app_scope = svc + '/providers/Microsoft.App'
        ids = b['jobPlan']
        stem = config['resourceStem']
        registry = 'acr' + stem.replace('-', '')
        storage = b['storageAccountName']
        environment = b['environmentId']
        cert_vault = b['certificateVaultName']
        names = (b['jobName'], b['portalJobName'])
        expected_names = (f'caj-{stem}-tls', f'caj-{stem}-auth-cert')
        if (outputs['managementVm']['id'].casefold() != vm.casefold()
                or b['resourceGroup'] != config['resourceGroupNames']['svc']
                or b['location'] != config['location']
                or b['registryName'] != registry or b['registryServer'] != registry + '.azurecr.io'
                or b['registryId'].casefold() != (svc + '/providers/Microsoft.ContainerRegistry/registries/' + registry).casefold()
                or b['receiptContainerName'] != 'certificate-jobs'
                or not re.fullmatch(r'[a-z0-9]{3,24}', storage)
                or b['storageAccountId'].casefold() != (svc + '/providers/Microsoft.Storage/storageAccounts/' + storage).casefold()
                or b['storageUrl'].rstrip('/') != 'https://' + storage + '.blob.core.windows.net'
                or names != expected_names
                or any(not re.fullmatch(r'[a-z][a-z0-9-]{0,29}[a-z0-9]', name) for name in names)
                or b['environmentName'] != f'cae-{stem}-certificate'
                or environment.casefold() != (app_scope + '/managedEnvironments/' + b['environmentName']).casefold()
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,23}', cert_vault)
                or b['certificateSecretUri'].rstrip('/') !=
                    f"https://{cert_vault}.vault.azure.net/secrets/{config['certificate']['certificateName']}"
                or ids['environmentId'].casefold() != environment.casefold() or ids['registryServer'] != b['registryServer']
                or ids['jobName'] != b['jobName'] or ids['portalJobName'] != b['portalJobName']
                or ids['storageUrl'].rstrip('/') != b['storageUrl'].rstrip('/')
                or ids['receiptContainerName'] != b['receiptContainerName']
                or ids['portalStateContainerName'] != 'portal-certificate-state'):
            raise ValueError()
        return {**b, 'subscription': subscription, 'svc': svc, 'vmId': vm,
                'tlsJobId': app_scope + '/jobs/' + b['jobName'],
                'portalJobId': app_scope + '/jobs/' + b['portalJobName'],
                'receiptContainers': {'tls': b['receiptContainerName'], 'portal': ids['portalStateContainerName']}}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('The certificate job outputs differ from the selected Core deployment') from None


def _sleep(sleep, clock, deadline, response=None):
    seconds = 5
    if response:
        values = [value for name, value in (response.get('headers') or {}).items() if name.lower() == 'retry-after']
        if values:
            hint = values[0]
            if not isinstance(hint, str) or not re.fullmatch(r'[0-9]{1,8}', hint.strip()):
                raise ValueError('The certificate operation requested an unsupported wait')
            seconds = max(seconds, int(hint.strip()))
    remaining = deadline - clock()
    if remaining > 0:
        sleep(min(remaining, seconds))


def _compile(path):
    # Import lazily to share the existing az/Bicep wrapper without a module cycle.
    from ..lifecycle import run
    return run(['az', 'bicep', 'build', '--file', str(path), '--stdout'])


def deploy_jobs(target, digest, transport, *, account_email, repository=REPOSITORY, compile_template=None,
                sleep=time.sleep, monotonic=time.monotonic, timeout=1500):
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError('Certificate jobs require the immutable image digest')
    compiled = (compile_template or _compile)(Path(repository) / 'infra/certificate-jobs.bicep')
    # Separate digest-specific deployments allow a running deployment to be read back
    # rather than start another PUT after loss of its response.
    label = 'certificate-jobs-' + hashlib.sha256(json.dumps(
        [compiled, target['jobPlan'], digest], sort_keys=True).encode()).hexdigest()[:16]
    deployment_id = target['svc'] + '/providers/Microsoft.Resources/deployments/' + label
    path = deployment_id + '?api-version=' + ARM_API
    response = transport.request('GET', path)
    body = checked_response(response, allowed=(200, 404), label='Read certificate job deployment')
    if body and str(body.get('id', '')).casefold() != deployment_id.casefold():
        raise ValueError('Certificate job deployment belongs to another resource')
    if response['statusCode'] == 404 or body.get('properties', {}).get('provisioningState') in ('Failed', 'Canceled'):
        response = transport.request('PUT', path, {'properties': {'mode': 'Incremental', 'template': compiled,
            'parameters': {'jobPlan': {'value': target['jobPlan']}, 'imageDigest': {'value': digest},
                           'accountEmail': {'value': account_email},
                           'storageAudience': {'value': STORAGE_AUDIENCE}}}})
        checked_response(response, allowed=(200, 201, 202), label='Deploy certificate jobs')
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        body = checked(transport, 'GET', path, allowed=(200, 404), label='Read deployed certificate jobs')
        properties = body.get('properties', {})
        state = properties.get('provisioningState')
        if body and str(body.get('id', '')).casefold() != deployment_id.casefold():
            raise ValueError('Certificate job deployment belongs to another resource')
        if state == 'Succeeded':
            for output in ('tlsJobId', 'portalJobId'):
                if str(properties.get('outputs', {}).get(output, {}).get('value', '')).casefold() != target[output].casefold():
                    raise ValueError('The deployed certificate job differs from the selected resource')
            return
        if state in ('Failed', 'Canceled'):
            raise ValueError('Certificate job deployment failed')
        _sleep(sleep, monotonic, deadline)
    raise ValueError('Certificate job deployment is still pending')


def _job_image(target, digest):
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError('The certificate job must use an immutable private image')
    return target['registryServer'] + '/' + certificate_image.IMAGE_REPOSITORY + '@' + digest


def _version_id(value, vault, category, name):
    return (isinstance(value, str)
            and re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/{category}/{name}/') + r'[0-9a-f]{32}', value))


def _arguments(config, mode, action, values):
    if mode not in ACTIONS or action not in ACTIONS[mode] or not isinstance(values, dict):
        raise ValueError('Unsupported certificate job action')
    if mode == 'tls':
        if not values or action == 'renew' and set(values) == {'rotateFromThumbprint'} and isinstance(
                values['rotateFromThumbprint'], str) and re.fullmatch(r'[0-9a-fA-F]{40}', values['rotateFromThumbprint']):
            return values
    else:
        if action == 'initial' and config.get('codex') and values == {'purpose': 'gateway-obo'}:
            return values
        expected = {'initial': set(), 'prepare': {'requestId', 'currentKeyId'},
                    'inspect': {'certificateId'}, 'activate': {'certificateId'},
                    'retire': {'certificateId', 'currentKeyId'}}[action]
        portal = config['portal']['certificate']
        vault = 'kv-' + config['resourceStem']
        if (set(values) == expected
                and ('requestId' not in values or isinstance(values['requestId'], str) and ATTEMPT.fullmatch(values['requestId']))
                and ('certificateId' not in values or _version_id(values['certificateId'], vault, 'certificates', portal['certificateName']))
                and ('currentKeyId' not in values or _version_id(values['currentKeyId'], vault, 'keys', portal['certificateName']))):
            return values
    raise ValueError('The certificate job action has invalid inputs')


def _template(job, target, mode, action, args, attempt, image):
    expected = target[mode + 'JobId']
    props = job.get('properties', {})
    template = copy.deepcopy(props.get('template', {}))
    containers = template.get('containers') or []
    # Start permits overriding the entire execution. Bind the known execution
    # identity to the exact fixed Job image and only these nonsecret arguments.
    plan = target['jobPlan']
    runtime = plan[mode + 'Identity']
    identities = {plan['pullIdentityId'].casefold(): 'None', runtime['id'].casefold(): 'Main'}
    if mode == 'tls':
        identities[plan['dnsIdentityId'].casefold()] = 'Main'
    identity = job.get('identity') or {}
    configuration = props.get('configuration', {})
    registries = configuration.get('registries') or []
    settings = configuration.get('identitySettings') or []
    if (str(job.get('id', '')).casefold() != expected.casefold()
            or str(props.get('environmentId', '')).casefold() != target['environmentId'].casefold()
            or props.get('provisioningState') != 'Succeeded'
            or len(containers) != 1 or containers[0].get('image') != image
            or containers[0].get('name') != 'certificate-worker'
            or any(containers[0].get(key) for key in ('command', 'args', 'volumeMounts'))
            or template.get('initContainers') or template.get('volumes')
            or configuration.get('triggerType') != ('Schedule' if mode == 'tls' else 'Manual')
            or (identity.get('type') or '').casefold() != 'userassigned'
            or {key.casefold() for key in (identity.get('userAssignedIdentities') or {})} != set(identities)
            or len(settings) != len(identities)
            or {str(setting.get('identity', '')).casefold(): setting.get('lifecycle') for setting in settings} != identities
            or len(registries) != 1 or registries[0].get('server') != target['registryServer']
            or str(registries[0].get('identity', '')).casefold() != plan['pullIdentityId'].casefold()
            or registries[0].get('passwordSecretRef')):
        raise ValueError('The certificate job configuration differs from the deployed identity or image')
    container = containers[0]
    env = container.get('env') or []
    expected_env = {'CERTIFICATE_MODE': mode, 'CERTIFICATE_CLIENT_ID': runtime['clientId'],
                    'CERTIFICATE_STORAGE_URL': target['storageUrl'],
                    'CERTIFICATE_STORAGE_AUDIENCE': STORAGE_AUDIENCE,
                    'CERTIFICATE_RECEIPT_CONTAINER': target['receiptContainers'][mode],
                    'CERTIFICATE_ACTION': 'renew' if mode == 'tls' else 'initial',
                    'CERTIFICATE_DNS_CLIENT_ID': plan['dnsClientId'] if mode == 'tls' else '',
                    'CERTIFICATE_PORTAL_STATE_CONTAINER': plan['portalStateContainerName'] if mode == 'portal' else ''}
    existing = {item.get('name'): item.get('value') for item in env}
    worker_cfg = existing.get('CERTIFICATE_CONFIG_JSON')
    try:
        actual_cfg = json.loads(worker_cfg)
    except (ValueError, TypeError):
        actual_cfg = None
    names = set(expected_env) | {'CERTIFICATE_CONFIG_JSON'}
    if mode == 'tls':
        names.add('CERTIFICATE_ACCOUNT_EMAIL')
        if [item.get('secretRef') for item in env if item.get('name') == 'CERTIFICATE_ACCOUNT_EMAIL'] != ['acme-account-contact']:
            raise ValueError('The certificate job account configuration is not a secret reference')
    if any(item.get('secretRef') for item in env if item.get('name') != 'CERTIFICATE_ACCOUNT_EMAIL'):
        raise ValueError('The certificate job has unexpected configuration secret references')

    def contains_value(name, value):
        # Container Apps may normalize the empty values of the other mode to null.
        return name in existing and (existing[name] or '') == value
    if (len(existing) != len(env) or set(existing) != names
            or not all(contains_value(name, value) for name, value in expected_env.items())
            or actual_cfg != plan[mode + 'Config']
            or mode == 'tls' and existing.get('CERTIFICATE_ACCOUNT_EMAIL')):
        raise ValueError('The certificate job runtime environment differs from the deployed identity or storage')
    changes = {'CERTIFICATE_ATTEMPT_ID': attempt, 'CERTIFICATE_ACTION': action,
               'CERTIFICATE_ARGUMENTS_JSON': json.dumps(args, separators=(',', ':'), sort_keys=True)}
    if args.get('purpose') == 'gateway-obo':
        expected_obo = {**plan['portalConfig'], 'certificateName': 'gateway-obo', 'validityMonths': 12}
        if plan.get('gatewayOboConfig') != expected_obo:
            raise ValueError('The Gateway OBO certificate plan differs from the selected foundation')
        changes['CERTIFICATE_CONFIG_JSON'] = json.dumps(expected_obo, separators=(',', ':'), sort_keys=True)
        changes['CERTIFICATE_ARGUMENTS_JSON'] = '{}'
        changes['CERTIFICATE_PORTAL_STATE_OBJECT'] = 'gateway-obo'
    if 'rotateFromThumbprint' in args:
        changes['CERTIFICATE_ROTATE_FROM_THUMBPRINT'] = args['rotateFromThumbprint']
    replaced = set(changes) | {'CERTIFICATE_ROTATE_FROM_THUMBPRINT'}
    container['env'] = [item for item in env if item.get('name') not in replaced] + [
        {'name': key, 'value': value} for key, value in changes.items()]
    return template


def _execution_id(value, root):
    if (isinstance(value, str) and value.casefold().startswith((root + '/executions/').casefold())
            and EXECUTION.fullmatch(value.rsplit('/', 1)[-1])
            and value.count('/') == root.count('/') + 2):
        return root + '/executions/' + value.rsplit('/', 1)[-1]
    return None


def _response_execution_id(value, root):
    # List and GET can return only name. A supplied id must still identify
    # an execution of this job, and both fields must agree when present.
    if not isinstance(value, dict):
        return None
    execution = None
    if 'id' in value:
        execution = _execution_id(value['id'], root)
        if not execution:
            return None
    if 'name' not in value:
        return execution
    name = value['name']
    if not isinstance(name, str) or not EXECUTION.fullmatch(name):
        return None
    canonical = root + '/executions/' + name
    if execution and canonical.casefold() != execution.casefold():
        return None
    return canonical


def _next_url(link, allowed):
    try:
        parsed = urlsplit(link)
        if (parsed.scheme == 'https' and parsed.netloc.lower() == 'management.azure.com'
                and parsed.path.casefold() == allowed.casefold() and not parsed.fragment and parsed.query):
            return link
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError('The certificate execution readback targeted an unexpected location')


def _attempt_execution_id(value, root, image, attempt):
    execution = _response_execution_id(value, root)
    if not execution:
        return None
    containers = value.get('properties', {}).get('template', {}).get('containers') or []
    if (len(containers) != 1 or containers[0].get('image') != image
            or [item.get('value') for item in containers[0].get('env') or []
                if item.get('name') == 'CERTIFICATE_ATTEMPT_ID'] != [attempt]):
        return None
    return execution


def _find_execution(transport, root, image, attempt):
    allowed = root + '/executions'
    path = allowed + '?api-version=' + APP_API
    matches = {}
    seen = set()
    for _ in range(20):
        if path in seen:
            raise ValueError('The certificate execution list returned a repeated page')
        seen.add(path)
        value = checked(transport, 'GET', path, label='List certificate job executions')
        rows = value.get('value', [])
        if not isinstance(rows, list):
            raise ValueError('The certificate execution list format is invalid')
        for row in rows:
            execution = _attempt_execution_id(row, root, image, attempt)
            if execution:
                matches[execution.casefold()] = execution
        if not value.get('nextLink'):
            if len(matches) > 1:
                raise ValueError('Multiple executions matched the saved certificate job attempt')
            return next(iter(matches.values()), None)
        path = _next_url(value['nextLink'], allowed)
    raise ValueError('The certificate execution list could not be checked in full')


def wait_execution(transport, record, save, *, first_response=None, sleep=time.sleep, monotonic=time.monotonic, timeout=3600):
    """Jobの開始受付では公開しない。今回のテンプレートと同じ完了実行だけを採用する。"""
    deadline = monotonic() + timeout
    root, image, attempt = (record[k] for k in ('jobId', 'image', 'attemptId'))
    first_body = (first_response or {}).get('body') or {}
    feed = (first_response or {}).get('headers') or {}
    if not record.get('executionId') and (first_response or {}).get('statusCode') in (200, 202):
        exact = _response_execution_id(first_body, root)
        # Azure may return an opaque provider LRO. Use an exact same-job
        # execution Location only when the body did not specify an identity.
        # Malformed or conflicting body fields require discovery by nonce.
        if not exact and isinstance(first_body, dict) and not {'id', 'name'} & first_body.keys():
            for name, location in feed.items():
                if name.lower() != 'location' or not isinstance(location, str):
                    continue
                try:
                    parsed = urlsplit(location)
                except ValueError:
                    continue
                if parsed.scheme == 'https' and parsed.netloc.lower() == 'management.azure.com' and not parsed.fragment:
                    exact = _execution_id(parsed.path, root)
                    if exact:
                        break
        if exact:
            record['executionId'] = exact
            save(record)
    if (first_response or {}).get('statusCode') == 202 and feed:
        _sleep(sleep, monotonic, deadline, first_response)
    while monotonic() < deadline:
        execution = record.get('executionId')
        if not execution:
            execution = _find_execution(transport, root, image, attempt)
            if execution:
                record['executionId'] = execution
                save(record)
        if execution:
            if not _execution_id(execution, root):
                raise ValueError('The recorded certificate execution belongs to another job')
            response = transport.request('GET', execution + '?api-version=' + APP_API)
            value = checked_response(response, allowed=(200, 404), label='Read the exact certificate execution')
            if response['statusCode'] == 200:
                actual = _attempt_execution_id(value, root, image, attempt)
                if not actual or actual.casefold() != execution.casefold():
                    raise ValueError('The certificate execution does not match its saved attempt and image')
                status = value['properties'].get('status')
                if status == 'Succeeded':
                    record['status'] = 'executed'
                    save(record)
                    return
                if status in ('Failed', 'Stopped', 'Degraded', 'Canceled', 'Cancelled'):
                    record['status'] = 'failed'
                    save(record)
                    raise ValueError('The identified certificate job execution failed')
            _sleep(sleep, monotonic, deadline, response)
        else:
            _sleep(sleep, monotonic, deadline)
    raise ValueError('The exact certificate job execution remains unconfirmed; the request was not repeated')


def _reader_script(storage_url, container, attempt, offset):
    # ARM's Managed Run Command only exposes the final portion of output.
    # Return bounded, hash-linked chunks from the one fixed immutable receipt.
    settings = json.dumps([storage_url, container, attempt, offset, IMDS_ORIGIN, STORAGE_AUDIENCE])
    return """#!/bin/bash
set -euo pipefail
set +x
umask 077
python3 -B - <<'EGAKUMI_CERT_RECEIPT'
import base64, email.utils, hashlib, json, sys, time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = build_opener(ProxyHandler({}), NoRedirect())
storage, container, attempt, offset, imds_origin, storage_audience = """ + settings + """
uri = storage.rstrip('/') + '/' + container + '/receipts/' + attempt + '.json'
try:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        metadata = imds_origin + '/metadata/identity/oauth2/token?' + urlencode({
            'api-version': '2018-02-01', 'resource': storage_audience})
        with opener.open(Request(metadata, headers={'Metadata': 'true'}), timeout=10) as result:
            token = json.load(result)['access_token']
        try:
            with opener.open(Request(uri, headers={'Authorization': 'Bearer ' + token,
                    'x-ms-version': '2023-11-03', 'x-ms-date': email.utils.formatdate(usegmt=True)}), timeout=10) as result:
                data = result.read(65537)
            break
        except HTTPError as error:
            if error.code not in (403, 404):
                raise
            error.close()
            time.sleep(min(6, max(0, deadline - time.monotonic())))
    else:
        raise ValueError()
    if not 0 <= offset < len(data) <= 65536:
        raise ValueError()
    print('EGAKUMI_CERT_JOB_RESULT=' + json.dumps({'offset': offset, 'length': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'chunk': base64.b64encode(data[offset:offset+1800]).decode('ascii')}))
except Exception:
    sys.exit('The exact private certificate receipt could not be read')
EGAKUMI_CERT_RECEIPT
"""


def _matching_role(body, role_id, scope, principal, definition):
    values = body.get('properties') or {}
    return (str(body.get('id', '')).casefold() == role_id.casefold()
            and str(values.get('scope', '')).casefold() == scope.casefold()
            and str(values.get('principalId', '')).casefold() == principal.casefold()
            and str(values.get('roleDefinitionId', '')).casefold() == definition.casefold())


def _matching_command(body, command_id, source):
    values = body.get('properties') or {}
    return (str(body.get('id', '')).casefold() == command_id.casefold()
            and values.get('source', {}).get('script') == source)


def _unique_json(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate fields in the certificate result')
    return result


def _chunk(output, offset):
    if not isinstance(output, str):
        raise ValueError('The private certificate receipt reader returned invalid output')
    try:
        matches = [line[len(VM_RESULT):] for line in output.splitlines() if line.startswith(VM_RESULT)]
        if len(matches) != 1 or len(matches[0]) > 3200:
            raise ValueError()
        value = json.loads(matches[0], object_pairs_hook=_unique_json)
        length = value['length']
        if (type(value['offset']) is not int or value['offset'] != offset
                or type(length) is not int or not offset < length <= 65536
                or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])):
            raise ValueError()
        data = base64.b64decode(value['chunk'], validate=True)
        if len(data) != min(1800, length - offset):
            raise ValueError()
        return data, length, value['sha256']
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('The private certificate receipt reader did not return a complete chunk') from None


def _cleanup_resource(transport, path, verify, *, sleep, monotonic):
    observed = transport.request('GET', path)
    if observed.get('statusCode') == 404:
        return
    body = checked_response(observed, allowed=(200,), label='Read temporary certificate receipt resources')
    if not verify(body):
        raise ValueError('A temporary certificate receipt resource no longer matches its saved identity')
    checked(transport, 'DELETE', path, allowed=(200, 202, 204, 404), label='Remove temporary certificate receipt resources')
    deadline = monotonic() + 90
    while monotonic() < deadline:
        response = transport.request('GET', path)
        if response.get('statusCode') == 404:
            return
        if response.get('statusCode') not in (200, 202):
            raise ValueError('Temporary certificate receipt resource removal is unconfirmed')
        _sleep(sleep, monotonic, deadline, response)
    raise ValueError('Temporary certificate receipt resource removal is still pending')


def read_private_receipt(target, transport, record, save, *, sleep=time.sleep, monotonic=time.monotonic, timeout=900):
    """管理VMへ一時読取を付け、PEから公開証明書のreceiptだけを読んで回収する。"""
    attempt = record['attemptId']
    mode = record.get('mode')
    if not ATTEMPT.fullmatch(attempt) or mode not in ('tls', 'portal'):
        raise ValueError('A receipt requires the recorded certificate job attempt')
    container = target['receiptContainers'][mode]
    vm_path = target['vmId'] + '?api-version=' + COMPUTE_API
    vm = checked(transport, 'GET', vm_path, label='Read certificate receipt VM')
    principal = vm.get('identity', {}).get('principalId')
    if (str(vm.get('id', '')).casefold() != target['vmId'].casefold()
            or vm.get('location') != target['location'] or not _guid(principal)):
        raise ValueError('Certificate receipt readback belongs to another management VM')
    role_def = target['subscription'] + '/providers/Microsoft.Authorization/roleDefinitions/' + BLOB_READER
    scope = target['storageAccountId'] + '/blobServices/default/containers/' + container
    role_name = str(uuid.uuid5(uuid.NAMESPACE_URL, scope.lower() + '/certificate-readback/' + attempt))
    role_id = scope + '/providers/Microsoft.Authorization/roleAssignments/' + role_name
    role_path = role_id + '?api-version=2022-04-01'
    offsets = record.get('readerOffsets', [])
    if not isinstance(offsets, list) or any(type(item) is not int or item < 0 or item >= 65536 or item % 1800 for item in offsets):
        raise ValueError('The protected certificate readback offsets are invalid')
    record['readerOffsets'] = list(offsets)
    save(record)
    clock = monotonic

    def role_matches(value):
        return _matching_role(value, role_id, scope, principal, role_def)

    def command(offset):
        command_id = target['vmId'] + f'/runCommands/cert-job-read-{attempt}-{offset:04x}'
        return (command_id, command_id + '?api-version=' + COMPUTE_API + '&$expand=instanceView',
                _reader_script(target['storageUrl'], container, attempt, offset))

    deadline = clock() + timeout
    data = bytearray()
    length, digest = None, None
    # The known stable identifiers also permit removal after a lost PUT response.
    try:
        found = transport.request('GET', role_path)
        if found.get('statusCode') == 404:
            checked(transport, 'PUT', role_path, {'properties': {'roleDefinitionId': role_def,
                    'principalId': principal, 'principalType': 'ServicePrincipal'}},
                    label='Grant temporary certificate receipt readback')
        elif not role_matches(checked_response(found, allowed=(200,), label='Read current certificate receipt role')):
            raise ValueError('The saved certificate receipt role differs from its intended scope')
        while length is None or len(data) < length:
            offset = len(data)
            command_id, path, script = command(offset)
            if offset not in record['readerOffsets']:
                record['readerOffsets'].append(offset)
                save(record)
            found = transport.request('GET', path)
            if found.get('statusCode') == 404:
                checked(transport, 'PUT', path.split('&', 1)[0], {'location': target['location'],
                    'properties': {'source': {'script': script}, 'asyncExecution': True,
                                  'timeoutInSeconds': 720, 'treatFailureAsDeploymentFailure': True}},
                    label='Start the bounded certificate receipt reader')
            while clock() < deadline:
                response = transport.request('GET', path)
                body = checked_response(response, allowed=(200, 404), label='Read certificate receipt reader status')
                if body:
                    if not _matching_command(body, command_id, script):
                        raise ValueError('The certificate receipt reader differs from the saved request')
                    view = body.get('properties', {}).get('instanceView', {})
                    state = view.get('executionState')
                    if state == 'Succeeded' and type(view.get('exitCode')) is int and view['exitCode'] == 0:
                        piece, chunk_length, chunk_digest = _chunk(view.get('output'), offset)
                        if length is not None and (length != chunk_length or digest != chunk_digest):
                            raise ValueError('The private certificate receipt changed during readback')
                        length, digest = chunk_length, chunk_digest
                        data.extend(piece)
                        break
                    if state in ('Failed', 'Canceled', 'TimedOut', 'Succeeded'):
                        raise ValueError('The certificate receipt reader failed')
                _sleep(sleep, monotonic, deadline, response)
            else:
                raise ValueError('The certificate receipt reader timed out')
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('The private certificate receipt did not match the readback hash')
        try:
            return json.loads(data, object_pairs_hook=_unique_json)
        except (ValueError, UnicodeError):
            raise ValueError('The private certificate receipt is not valid JSON') from None
    finally:
        errors = False
        for offset in record['readerOffsets']:
            command_id, path, script = command(offset)
            try:
                _cleanup_resource(transport, path, lambda value: _matching_command(value, command_id, script),
                                  sleep=sleep, monotonic=monotonic)
            except (ValueError, RuntimeError, OSError):
                errors = True
        try:
            _cleanup_resource(transport, role_path, role_matches, sleep=sleep, monotonic=monotonic)
        except (ValueError, RuntimeError, OSError):
            errors = True
        if errors:
            raise ValueError('Exact temporary certificate receipt resources could not be revoked; resume from the protected state') from None
        record.pop('readerOffsets', None)
        save(record)


def _covers(host, dns_name):
    host, dns_name = host.casefold(), dns_name.casefold()
    return host == dns_name or dns_name.startswith('*.') and host.count('.') == dns_name.count('.') and host.endswith(dns_name[1:])


def _result(config, target, mode, action, args, attempt, payload, *, now=None):
    try:
        if (set(payload) != {'attemptId', 'mode', 'action', 'result'} or payload['attemptId'] != attempt
                or payload['mode'] != mode or payload['action'] != action or not isinstance(payload['result'], dict)):
            raise ValueError()
        value = payload['result']
        if mode == 'portal':
            if action == 'retire':
                key = re.sub(r'/certificates/', '/keys/', args['certificateId'], count=1)
                if value != {'retired': True, 'keyId': key}:
                    raise ValueError()
                return value
            result = certificate_record(value, config, certificate_name=args.get('purpose'))
            if action in ('inspect', 'activate') and result['certificateId'] != args['certificateId']:
                raise ValueError()
            return result
        vault = target['certificateVaultName']
        name = config['certificate']['certificateName']
        now = time.time() if now is None else now
        der = base64.b64decode(value['publicCertificate'], validate=True)
        requested = [config['gateway']['publicHostName'], config['portal']['publicHostName']]
        dns_names = value['dnsNames']
        ancestry = [value['certificateId'], value['secretId']]
        if (not _version_id(ancestry[0], vault, 'certificates', name)
                or not _version_id(ancestry[1], vault, 'secrets', name)
                or ancestry[0].rsplit('/', 1)[-1] != ancestry[1].rsplit('/', 1)[-1]
                or not 128 <= len(der) <= 16384 or der[0] != 0x30
                or hashlib.sha1(der, usedforsecurity=False).hexdigest() != value['certificateThumbprint'].lower()
                or type(value['expires']) is not int or type(value['notBefore']) is not int
                or value['notBefore'] > now + 300 or value['notBefore'] >= value['expires'] or value['expires'] <= now
                or not isinstance(dns_names, list) or not dns_names
                or any(not isinstance(host, str) or not re.fullmatch(r'(?:\*\.)?[A-Za-z0-9.-]{1,253}', host) for host in dns_names)
                or not all(any(_covers(host, san) for san in dns_names) for host in requested)):
            raise ValueError()
        return {key: value[key] for key in ('certificateId', 'secretId', 'certificateThumbprint',
                                             'notBefore', 'expires', 'publicCertificate', 'dnsNames')}
    except (ValueError, KeyError, AttributeError, TypeError, IndexError):
        raise ValueError('The private receipt does not attest to the requested public certificate operation') from None


def run_certificate_action(config, outputs, transport, save_json, *, workspace, mode, action, arguments=None,
                           image_digest=None, operation_id=None, retry_failed=False, sleep=time.sleep,
                           monotonic=time.monotonic, timeout=3600):
    """単一Jobと入力を保護stateに固定し、応答不明でも開始要求を重ねない。"""
    target = _targets(config, outputs)
    args = _arguments(config, mode, action, {} if arguments is None else arguments)
    image = _job_image(target, image_digest or target.get('imageDigest') or outputs.get('certificateImageDigest'))
    if operation_id is not None and (not isinstance(operation_id, str) or not ATTEMPT.fullmatch(operation_id)):
        raise ValueError('The certificate operation identifier is invalid')
    root = target[mode + 'JobId']
    purpose = args.get('purpose', mode)
    path = workspace.checked(workspace.state / f'certificate-job-{purpose}-{action}.json')
    record = _load(path)
    identity = {'jobId': root, 'mode': mode, 'action': action, 'image': image,
                'arguments': args, 'operationId': operation_id}
    matches = all(record.get(key) == value for key, value in identity.items())
    status = record.get('status')
    if record and status not in ('requested', 'executed', 'failed', 'completed'):
        raise ValueError('The saved certificate job action state is invalid')
    if status in ('requested', 'executed') and not matches:
        raise ValueError('Finish reconciling the saved certificate job before requesting a different action')
    if status == 'failed' and not retry_failed:
        raise ValueError('The saved certificate job failed; review its protected checkpoint before retrying')
    if status == 'completed' and operation_id and matches:
        return _result(config, target, mode, action, args, record['attemptId'],
                       {'attemptId': record['attemptId'], 'mode': mode, 'action': action, 'result': record['result']})
    use_prior = status in ('requested', 'executed')
    response = None
    if not use_prior:
        job = checked(transport, 'GET', root + '?api-version=' + APP_API, label='Verify the selected certificate job')
        attempt = uuid.uuid4().hex
        template = _template(job, target, mode, action, args, attempt, image)
        record = {**identity, 'attemptId': attempt, 'status': 'requested'}
        # Save before the API to reconcile both an accepted-but-lost response and
        # an unexpected local failure. Unknown start result is never auto-resubmitted.
        save_json(path, record)
        try:
            response = transport.request('POST', root + '/start?api-version=' + APP_API, template)
        except (RuntimeError, OSError):
            response = None
        if response and response.get('statusCode') in (400, 401, 403, 404, 422):
            record['status'] = 'failed'
            save_json(path, record)
            checked_response(response, allowed=(200, 202), label='Start the exact certificate job')
    if not isinstance(record.get('attemptId'), str) or not ATTEMPT.fullmatch(record['attemptId']):
        raise ValueError('The saved certificate job attempt is invalid')
    if record['status'] != 'executed':
        wait_execution(transport, record, lambda state: save_json(path, state), first_response=response,
                       sleep=sleep, monotonic=monotonic, timeout=timeout)
    body = read_private_receipt(target, transport, record, lambda state: save_json(path, state),
                                sleep=sleep, monotonic=monotonic)
    result = _result(config, target, mode, action, args, record['attemptId'], body)
    record.update(status='completed', result=result)
    save_json(path, record)
    return result


def complete_initial_certificates(config, outputs, transport, save_json, *, workspace,
                                  repository=REPOSITORY, compile_template=None, sleep=time.sleep,
                                  monotonic=time.monotonic):
    """非公開イメージと両Jobの初回実行を完了してから、公開入口用の証明書結果を返す。"""
    target = _targets(config, outputs)
    digest = certificate_image.build_or_reuse(config, outputs, transport, save_json, workspace,
                                              repository=repository, sleep=sleep, monotonic=monotonic)
    deploy_jobs(target, digest, transport, account_email=config['certificate']['contactEmail'],
                repository=repository, compile_template=compile_template,
                sleep=sleep, monotonic=monotonic)
    def initial(mode):
        return run_certificate_action(config, outputs, transport, save_json, workspace=workspace, mode=mode,
                                      action='initial', image_digest=digest, retry_failed=True,
                                      sleep=sleep, monotonic=monotonic)
    issued = {'tls': initial('tls'), 'portal': initial('portal'), 'imageDigest': digest}
    if config.get('codex') and not config['gateway']['authentication']['entra'].get('obo'):
        issued['gatewayObo'] = run_certificate_action(
            config, outputs, transport, save_json, workspace=workspace, mode='portal', action='initial',
            arguments={'purpose': 'gateway-obo'}, image_digest=digest, retry_failed=True,
            sleep=sleep, monotonic=monotonic)
    return issued
