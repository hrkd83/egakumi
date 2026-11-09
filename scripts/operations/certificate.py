#!/usr/bin/env python3
"""既存VM workerで証明書を更新し、参照同期と公開TLSを確認する。既定は計画。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit
import uuid

from operations.common import OperationContext, OperationError

ROOT = Path(__file__).resolve().parents[2]
APIM_REFRESH_API = '2025-09-01-preview'
COMPUTE_API = '2024-07-01'
NETWORK_API = '2024-05-01'
MARKER = 'EGAKUMI_CERT_METADATA:'
REFRESH_TAG = 'egakumi-certificate-refresh'


def checked_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,127}', value):
        raise OperationError('A validated existing certificate name is required')
    return value


def targets(ctx):
    outputs = ctx.state['outputs']
    vm = outputs['managementVm']['id']
    gateway = outputs['gateway']['id']
    prefix = f"/subscriptions/{ctx.config['subscriptionId']}/resourceGroups/"
    expected_vm = prefix + ctx.config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + ctx.config['resourceStem'] + '-mgmt'
    expected_gateway = prefix + ctx.config['resourceGroupNames']['nw'] + '/providers/Microsoft.Network/applicationGateways/agw-' + ctx.config['resourceStem']
    if vm.casefold() != expected_vm.casefold() or gateway.casefold() != expected_gateway.casefold():
        raise OperationError('The recorded VM or Application Gateway differs from the selected environment')
    vault = ctx.service_id('keyVault').rsplit('/', 1)[-1]
    name = checked_name(ctx.config['certificate']['certificateName'])
    checked_name(vault)
    return vm, gateway, vault, name


def tls_leaf(host, port=443):
    """公開ホストの信頼・名前検証を有効にし、公開leafだけを要約する。"""
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as stream:
                certificate = stream.getpeercert()
                return {'host': host, 'port': port,
                        'thumbprint': hashlib.sha1(stream.getpeercert(binary_form=True)).hexdigest().upper(),
                        'notAfter': certificate.get('notAfter'), 'trustedAndHostnameVerified': True}
    except (OSError, ssl.SSLError, ValueError):
        raise OperationError('Public TLS verification failed; certificate synchronization is not accepted') from None


def metadata_program(vault, name, unit):
    """VM既存MIで公開certificate metadataだけを取得。secret/PFXは要求しない。"""
    return '''import base64, hashlib, json, subprocess, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None
try:
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net', headers={'Metadata':'true'})
    with opener.open(request, timeout=10) as response: token=json.load(response)['access_token']
    request = urllib.request.Request(URL, headers={'Authorization':'Bearer '+token})
    with opener.open(request, timeout=15) as response: value=json.load(response)
    token=None
    result={'certificateId':value['id'], 'secretVersionUri':value['sid'], 'enabled':value['attributes']['enabled'], 'expires':value['attributes']['exp'], 'thumbprint':hashlib.sha1(base64.b64decode(value['cer'], validate=True)).hexdigest().upper(), 'timerEnabled':subprocess.run(['systemctl','is-enabled','--quiet',UNIT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode==0}
    print(MARKER+json.dumps(result, separators=(',',':')))
except Exception:
    print('Certificate public metadata could not be read')
    raise SystemExit(1)
'''.replace('URL', repr(f'https://{vault}.vault.azure.net/certificates/{name}?api-version=7.4')).replace('UNIT', repr(unit + '.timer')).replace('MARKER', repr(MARKER))


def remote_script(vault, name, *, renew=False, thumbprint=None):
    directory = '/var/lib/egakumi-certificates/' + vault + '-' + name
    unit = 'egakumi-certificate-' + vault + '-' + name
    expected = hashlib.sha256((ROOT / 'guest/certificate.ps1').read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    lines = ['#!/bin/bash', 'set -euo pipefail', 'set +x', 'umask 077',
             f"dir='{directory}'", f"unit='{unit}'",
             '[ ! -L "$dir" ] && [ ! -L "$dir/certificate.ps1" ] && [ ! -L "$dir/config.json" ]',
             f"[ \"$(sha256sum \"$dir/certificate.ps1\" | cut -d' ' -f1)\" = '{expected}' ]"]
    if renew:
        lines += ['if systemctl is-active --quiet "$unit.service"; then exit 1; fi',
                  'command -v systemd-run >/dev/null 2>&1',
                  # Reuse the installed worker with the timer's OS login and
                  # cache environment. No service or timer is installed.
                  f"task_worker_unit='{unit}-operations-{uuid.uuid4().hex[:12]}.service'",
                  'task_worker_started=false',
                  'finish_worker() { task_status=$?; trap - EXIT; if [ "$task_worker_started" = true ] && [ "$task_status" -ne 0 ]; then systemctl stop "$task_worker_unit" >/dev/null 2>&1 || true; fi; exit "$task_status"; }',
                  'trap finish_worker EXIT', "trap 'exit 130' INT", "trap 'exit 143' TERM",
                  'task_worker_load=$(systemctl show --property=LoadState --value "$task_worker_unit" 2>/dev/null || true)',
                  '[ "$task_worker_load" = not-found ]', 'task_worker_started=true',
                  'systemd-run --system --quiet --wait --pipe --collect --service-type=exec \\',
                  ' --uid=root --gid=root --property=SetLoginEnvironment=yes --property=UMask=0077 \\',
                  ' --property="Environment=XDG_CACHE_HOME=$dir/cache" \\',
                  ' --property="Environment=XDG_CONFIG_HOME=$dir/config" \\',
                  ' --property="Environment=XDG_DATA_HOME=$dir/data" \\',
                  ' --property=NoNewPrivileges=yes --property=PrivateTmp=yes --property=ProtectHome=yes \\',
                  ' --property=ProtectSystem=strict --property="ReadWritePaths=$dir" \\',
                  ' --property=RuntimeMaxSec=3300 --property=TimeoutStopSec=30 --expand-environment=no \\',
                  ' --unit="$task_worker_unit" /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File "$dir/certificate.ps1" -Action Renew -ConfigFile "$dir/config.json"' +
                  (' -RotateFromThumbprint ' + thumbprint if thumbprint else '')]
    lines += ["python3 - <<'CERTIFICATE_METADATA'", metadata_program(vault, name, unit), 'CERTIFICATE_METADATA']
    return '\n'.join(lines) + '\n'


def public_metadata(output, vault, name):
    lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise OperationError('The VM public certificate result is missing or ambiguous')
    try:
        value = json.loads(lines[0])
        if set(value) != {'certificateId', 'secretVersionUri', 'enabled', 'expires', 'thumbprint', 'timerEnabled'}:
            raise ValueError()
        version = r'[0-9a-f]{32}'
        if (not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/certificates/{name}/') + version, value['certificateId'])
                or not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/secrets/{name}/') + version, value['secretVersionUri'])
                or not re.fullmatch(r'[0-9A-F]{40}', value['thumbprint'])
                or value['enabled'] is not True or value['timerEnabled'] is not True
                or isinstance(value['expires'], bool) or not isinstance(value['expires'], (int, float))
                or value['expires'] <= time.time()):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError):
        raise OperationError('The VM public certificate metadata did not match this environment') from None


def read_worker_command(ctx, vm, resource_id):
    """既存の同一VM commandだけを読む。実行要求や回収は行わない。"""
    if not re.fullmatch(re.escape(vm + '/runCommands/operations-certificate-') + r'[0-9a-f]{12}', resource_id):
        raise OperationError('Select the exact recorded certificate command for this VM')
    # Explicit VM identifiers select the managed-command CLI branch;
    # --ids alone is not expanded by this overloaded show command.
    raw = ctx.run_cli(['az', 'vm', 'run-command', 'show',
                      '--subscription', ctx.config['subscriptionId'],
                      '--resource-group', ctx.config['resourceGroupNames']['maint'],
                      '--vm-name', vm.rsplit('/', 1)[-1],
                      '--run-command-name', resource_id.rsplit('/', 1)[-1],
                      '--instance-view', '--only-show-errors', '--output', 'json'])
    observed = json.loads(raw)
    if not isinstance(observed, dict) or str(observed.get('id', '')).casefold() != resource_id.casefold():
        raise OperationError('The VM command result belongs to another resource')
    properties = observed.get('properties', observed)
    view = properties.get('instanceView') or observed.get('instanceView') or {}
    return properties, view


def run_worker(ctx, vm, vault, name, *, renew=False, thumbprint=None, timeout=3600, sleep=time.sleep, clock=time.monotonic):
    """単一のManaged Run Command。PUTは再送せず、そのexact resourceだけを待つ。"""
    resource_id = vm + '/runCommands/operations-certificate-' + uuid.uuid4().hex[:12]
    body = {'location': ctx.config['location'],
            'tags': {'product': ctx.config['productName'], 'environment': ctx.config['environmentName']},
            'properties': {'source': {'script': remote_script(vault, name, renew=renew, thumbprint=thumbprint)},
                           'asyncExecution': False, 'timeoutInSeconds': timeout, 'treatFailureAsDeploymentFailure': True}}
    ctx.record('certificate-vm-requested', {'resourceId': resource_id, 'renew': renew})
    response = ctx.request('PUT', resource_id, COMPUTE_API, body)
    if response.get('statusCode') not in (200, 201, 202):
        raise OperationError('The VM certificate command was not accepted')
    deadline = clock() + timeout
    while clock() < deadline:
        properties, view = read_worker_command(ctx, vm, resource_id)
        state = view.get('executionState')
        if state in ('Failed', 'Canceled', 'TimedOut') or properties.get('provisioningState') in ('Failed', 'Canceled'):
            ctx.record('certificate-vm-retained', {'resourceId': resource_id, 'state': 'failed', 'deleted': False})
            raise OperationError('The VM certificate command failed; its request was not repeated')
        if state == 'Succeeded':
            if view.get('exitCode') != 0:
                raise OperationError('The VM certificate worker did not finish successfully')
            metadata = public_metadata(view.get('output', ''), vault, name)
            ctx.record('certificate-vm-completed', {'resourceId': resource_id, 'metadata': metadata})
            deletion = ctx.request('DELETE', resource_id, COMPUTE_API)
            if deletion.get('statusCode') not in (200, 202, 204):
                raise OperationError('The completed VM command could not be reclaimed')
            cleanup_deadline = clock() + 60
            while clock() < cleanup_deadline:
                after = ctx.request('GET', resource_id, COMPUTE_API)
                if after.get('statusCode') == 404:
                    ctx.record('certificate-vm-reclaimed', {'resourceId': resource_id, 'absent': True})
                    return metadata
                if after.get('statusCode') != 200 or str((after.get('body') or {}).get('id', '')).casefold() != resource_id.casefold():
                    raise OperationError('VM command reclamation is not confirmed')
                sleep(min(5, max(0, cleanup_deadline - clock())))
            raise OperationError('VM command deletion remains pending; no additional DELETE was sent')
        sleep(min(5, max(0, deadline - clock())))
    ctx.record('certificate-vm-retained', {'resourceId': resource_id, 'state': 'pending', 'deleted': False})
    raise OperationError('The VM certificate command remains pending; inspect it before retrying')


def verify_references(ctx, gateway, vault, name):
    expected = f'https://{vault}.vault.azure.net/secrets/{name}'
    value = ctx.verify_resource(gateway, NETWORK_API, 'Microsoft.Network/applicationGateways')
    properties = value.get('properties', {})
    certificates = [v for v in properties.get('sslCertificates', []) if v.get('name') == 'gateway-tls']
    if len(certificates) != 1 or str(certificates[0].get('properties', {}).get('keyVaultSecretId', '')).rstrip('/') != expected:
        raise OperationError('Application Gateway does not reference the expected versionless certificate')
    for listener_name, host in (('listener-https', ctx.config['gateway']['publicHostName']),
                                ('portal-https', ctx.config['portal']['publicHostName'])):
        listeners = [v.get('properties', {}) for v in properties.get('httpListeners', []) if v.get('name') == listener_name]
        if (len(listeners) != 1 or listeners[0].get('protocol') != 'Https'
                or str(listeners[0].get('hostName', '')).casefold() != host.casefold()
                or str(listeners[0].get('sslCertificate', {}).get('id', '')).casefold() !=
                    (gateway + '/sslCertificates/gateway-tls').casefold()):
            raise OperationError('The selected public host is not bound to the owned certificate listener')
    grpc = ctx.config.get('gateway', {}).get('grpc', {})
    if grpc.get('enabled'):
        apim = ctx.verify_resource(ctx.service_id('apim'), '2024-05-01', 'Microsoft.ApiManagement/service')
        own = [v for v in apim.get('properties', {}).get('hostnameConfigurations', [])
               if v.get('type') == 'Proxy' and v.get('hostName') == grpc['publicHostName']]
        if len(own) != 1 or str(own[0].get('keyVaultId', '')).rstrip('/') != expected:
            raise OperationError('API Management does not reference the expected versionless certificate')


def wait_refresh(ctx, response, *, timeout=3600, sleep=time.sleep, clock=time.monotonic):
    """公式refreshHostnamesのLRO URLはRAM内で限定検査し、GETだけを送る。"""
    if response.get('statusCode') == 200:
        return
    if response.get('statusCode') != 202:
        raise OperationError('Certificate synchronization was not accepted')
    headers = {k.lower(): v for k, v in response.get('headers', {}).items()}
    status_endpoint = 'azure-asyncoperation' in headers
    url = headers.get('azure-asyncoperation') if status_endpoint else headers.get('location')
    if not isinstance(url, str):
        raise OperationError('Certificate synchronization is pending without a status URL')
    parsed = urlsplit(url)
    prefix = f"/subscriptions/{ctx.config['subscriptionId']}/"
    region_prefix = prefix + 'providers/Microsoft.ApiManagement/locations/' + ctx.config['location'] + '/'
    resource_prefix = ctx.service_id('apim') + '/'
    if (parsed.scheme != 'https' or parsed.netloc != 'management.azure.com' or parsed.fragment
            or '%' in parsed.path or '\\' in parsed.path or '..' in parsed.path.split('/')
            or not any(parsed.path.casefold().startswith(v.casefold()) for v in (region_prefix, resource_prefix))):
        raise OperationError('Certificate synchronization returned an untrusted status URL')
    deadline = clock() + timeout
    while clock() < deadline:
        hint = headers.get('retry-after')
        if hint is None:
            delay = 5
        else:
            try:
                if not isinstance(hint, str):
                    raise ValueError()
                if re.fullmatch(r'[0-9]{1,6}', hint):
                    delay = max(1, int(hint))
                else:
                    date = parsedate_to_datetime(hint)
                    if date.tzinfo is None:
                        raise ValueError()
                    delay = max(1, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                raise OperationError('Certificate synchronization supplied an unsupported Retry-After; keep it pending') from None
        if delay >= deadline - clock():
            raise OperationError('Certificate synchronization Retry-After exceeds the remaining wait; keep it pending')
        sleep(delay)
        if clock() >= deadline:
            break
        try:
            poll = ctx.transport.request('GET', url)
        except Exception:
            raise OperationError('Certificate synchronization status is unknown; no request was repeated') from None
        body = poll.get('body')
        if body is None:
            body = {}
        if not isinstance(body, dict) or body.get('error') is not None:
            raise OperationError('Certificate synchronization did not confirm successful completion')
        properties = body.get('properties')
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            raise OperationError('Certificate synchronization did not confirm successful completion')
        states = (body.get('status'), properties.get('provisioningState'))
        if any(value in ('Failed', 'Canceled', 'Cancelled') for value in states):
            raise OperationError('Certificate synchronization did not confirm successful completion')
        resource_result = not status_endpoint and ('id' in body or 'type' in body)
        if resource_result and (str(body.get('id', '')).casefold() != ctx.service_id('apim').casefold()
                or str(body.get('type', '')).casefold() != 'microsoft.apimanagement/service'):
            raise OperationError('Certificate synchronization returned a different resource')
        status = states[0] or states[1]
        if not status_endpoint and poll.get('statusCode') in (200, 204) and not body:
            return
        # refreshHostnames uses final-state-via=location. Its pending 202 can
        # be empty; its final 200 is the owned service resource, not a status envelope.
        if poll.get('statusCode') == 200 and resource_result and status is None:
            return
        if poll.get('statusCode') == 200 and status == 'Succeeded':
            return
        location_pending = not status_endpoint and poll.get('statusCode') == 202 and not body
        if not location_pending and (poll.get('statusCode') not in (200, 202)
                or status not in ('Accepted', 'InProgress', 'Running', 'Updating')):
            raise OperationError('Certificate synchronization did not confirm successful completion')
        headers = {k.lower(): v for k, v in poll.get('headers', {}).items()}
    raise OperationError('Certificate synchronization remains pending; the POST was not repeated')


def etag(value):
    tag = value.get('etag')
    if (not isinstance(tag, str) or len(tag) > 512
            or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', tag)
            or tag in ('*', '"*"', 'W/"*"')):
        raise OperationError('A concrete Application Gateway ETag is required')
    return tag


def wait_gateway(ctx, gateway, tag_value, *, timeout=1800, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while clock() < deadline:
        response = ctx.request('GET', gateway, NETWORK_API)
        value = response.get('body') or {}
        state = value.get('properties', {}).get('provisioningState')
        if response.get('statusCode') != 200 or str(value.get('id', '')).casefold() != gateway.casefold() or state in ('Failed', 'Canceled'):
            raise OperationError('Application Gateway update did not complete successfully')
        if state == 'Succeeded' and value.get('tags', {}).get(REFRESH_TAG) == tag_value:
            return value
        sleep(min(5, max(0, deadline - clock())))
    raise OperationError('Application Gateway update remains pending; inspect it before retrying')


def refresh_gateway(ctx, gateway):
    before = ctx.verify_resource(gateway, NETWORK_API, 'Microsoft.Network/applicationGateways')
    tags = copy.deepcopy(before.get('tags') or {})
    marker = uuid.uuid4().hex
    response = ctx.request('PATCH', gateway, NETWORK_API, {'tags': {**tags, REFRESH_TAG: marker}}, {'If-Match': etag(before)})
    if response.get('statusCode') not in (200, 201, 202):
        raise OperationError('Application Gateway certificate refresh was not accepted')
    current = wait_gateway(ctx, gateway, marker)
    restored = copy.deepcopy(current.get('tags') or {})
    if REFRESH_TAG in tags:
        restored[REFRESH_TAG] = tags[REFRESH_TAG]
    else:
        restored.pop(REFRESH_TAG, None)
    response = ctx.request('PATCH', gateway, NETWORK_API, {'tags': restored}, {'If-Match': etag(current)})
    if response.get('statusCode') not in (200, 201, 202):
        raise OperationError('The temporary certificate refresh tag still requires restoration')
    wait_gateway(ctx, gateway, tags.get(REFRESH_TAG))


def observe(ctx, metadata, *, timeout=300, sleep=time.sleep, clock=time.monotonic):
    hosts = [(ctx.config['gateway']['publicHostName'], 443), (ctx.config['portal']['publicHostName'], 443)]
    grpc = ctx.config['gateway'].get('grpc', {})
    if grpc.get('enabled'):
        hosts.append((grpc['publicHostName'], grpc['publicPort']))
    deadline = clock() + timeout
    while True:
        values = [tls_leaf(host, port) for host, port in hosts]
        if all(v['thumbprint'] == metadata['thumbprint'] for v in values):
            return values
        if clock() >= deadline:
            raise OperationError('Public TLS still differs from the current Key Vault certificate')
        sleep(min(5, deadline - clock()))


def operate(ctx, args):
    vm, gateway, vault, name = targets(ctx)
    if args.thumbprint and not re.fullmatch(r'[0-9A-Fa-f]{40}', args.thumbprint):
        raise OperationError('Planned rotation requires the current 40-character public leaf thumbprint')
    plan = {'stage': args.stage, 'status': 'planned', 'azureWrites': 0,
            'apimRefreshApi': APIM_REFRESH_API, 'newJob': False, 'pfxDownload': False,
            'publicTls': 'not-verified'}
    if not args.execute:
        ctx.record('certificate-plan', plan)
        return plan
    ctx.verify_resource(vm, COMPUTE_API, 'Microsoft.Compute/virtualMachines')
    verify_references(ctx, gateway, vault, name)
    metadata = run_worker(ctx, vm, vault, name)
    if args.stage == 'renew':
        previous_thumbprint = metadata['thumbprint']
        if args.thumbprint and args.thumbprint.upper() != metadata['thumbprint']:
            raise OperationError('The current Key Vault leaf changed; inspect it before requesting rotation')
        rotation_thumbprint = args.thumbprint.upper() if args.thumbprint else metadata['thumbprint'] if getattr(args, 'rotate', False) else None
        metadata = run_worker(ctx, vm, vault, name, renew=True,
                              thumbprint=rotation_thumbprint)
        if rotation_thumbprint and metadata['thumbprint'] == previous_thumbprint:
            raise OperationError('The requested rotation did not produce a different public leaf')
        ctx.record('certificate-renewed', {'metadata': metadata, 'publicTls': 'not-verified'})
    if args.stage in ('renew', 'sync'):
        grpc = ctx.config['gateway'].get('grpc', {})
        if grpc.get('enabled'):
            # A completed public delivery can outlive the previous CLI's LRO
            # wait. Reuse that positive, trusted observation only for sync;
            # an observation failure stops rather than repeating a pending POST.
            delivered = tls_leaf(grpc['publicHostName'], grpc['publicPort']) if args.stage == 'sync' else None
            if delivered is not None and delivered['thumbprint'] == metadata['thumbprint']:
                ctx.record('certificate-apim-refresh-skipped', {
                    'basis': 'trusted-public-tls-matches-current-key-vault',
                    'postSkipped': True, 'lroCompletionObserved': False, 'publicTls': delivered})
            else:
                ctx.record('certificate-apim-refresh-requested', {'apiVersion': APIM_REFRESH_API, 'status': 'requested'})
                response = ctx.request('POST', ctx.service_id('apim') + '/refreshHostnames', APIM_REFRESH_API)
                # Preserve the definite response without retaining messages, headers,
                # signed polling URLs, or other external response fields.
                status = response.get('statusCode')
                body = response.get('body')
                error = body.get('error') if isinstance(body, dict) else None
                code = error.get('code') if isinstance(error, dict) else None
                ctx.record('certificate-apim-refresh-response', {
                    'httpStatus': status if type(status) is int and 100 <= status <= 599 else None,
                    'azureErrorCode': code if isinstance(code, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,127}', code) else None})
                wait_refresh(ctx, response)
        ctx.record('certificate-gateway-refresh-requested', {'status': 'requested'})
        refresh_gateway(ctx, gateway)
    observed = observe(ctx, metadata)
    ctx.record('certificate-result', {'stage': args.stage, 'status': 'public-tls-verified',
                                     'metadata': metadata, 'publicTls': observed})
    return {'stage': args.stage, 'status': 'public-tls-verified',
            'renewalRequested': args.stage == 'renew', 'newLeafRequired': bool(args.thumbprint or getattr(args, 'rotate', False))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--stage', choices=('check', 'renew', 'sync'), default='check')
    rotation = parser.add_mutually_exclusive_group()
    rotation.add_argument('--thumbprint')
    rotation.add_argument('--rotate', action='store_true', help='VMで確認した現在の公開leafを期限前に一度だけ更新する')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if (args.rotate or args.thumbprint) and args.stage != 'renew':
        parser.error('--rotate/--thumbprint require --stage renew')
    try:
        ctx = OperationContext.load(args.config)
        if args.execute:
            with ctx.locked():
                result = operate(ctx, args)
        else:
            result = operate(ctx, args)
        print(json.dumps(result))
        return 0
    except (OperationError, OSError, ValueError, KeyError, TypeError):
        print('Certificate operation stopped; inspect its protected receipt and current state before retrying.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
