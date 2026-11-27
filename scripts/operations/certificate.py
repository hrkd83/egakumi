#!/usr/bin/env python3
"""公開TLS証明書を確認・更新し、AppGWへの反映を確認する。既定は計画。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import copy
import hashlib
import json
import re
import socket
import ssl
import time
import uuid

from operations.common import OperationContext, OperationError

NETWORK_API = '2024-05-01'
REFRESH_TAG = 'egakumi-certificate-refresh'


def checked_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,127}', value):
        raise OperationError('A validated existing certificate name is required')
    return value


def targets(ctx):
    outputs = ctx.state['outputs']
    gateway = outputs['gateway']['id']
    prefix = f"/subscriptions/{ctx.config['subscriptionId']}/resourceGroups/"
    expected_gateway = prefix + ctx.config['resourceGroupNames']['nw'] + '/providers/Microsoft.Network/applicationGateways/agw-' + ctx.config['resourceStem']
    if not isinstance(gateway, str) or gateway.casefold() != expected_gateway.casefold():
        raise OperationError('The recorded Application Gateway differs from the selected environment')

    vault_id = ctx.service_id('certificateVault')
    vault = checked_name('kvtls-' + ctx.config['resourceStem'])
    expected_vault = prefix + ctx.config['resourceGroupNames']['svc'] + '/providers/Microsoft.KeyVault/vaults/' + vault
    if vault_id.casefold() != expected_vault.casefold():
        raise OperationError('The recorded public TLS vault differs from the selected environment')

    name = checked_name(ctx.config['certificate']['certificateName'])
    bootstrap = outputs.get('certificateBootstrap')
    expected_secret = f'https://{vault}.vault.azure.net/secrets/{name}'
    if (not isinstance(bootstrap, dict)
            or not isinstance(bootstrap.get('certificateVaultId'), str)
            or bootstrap['certificateVaultId'].casefold() != vault_id.casefold()
            or bootstrap.get('certificateVaultName') != vault
            or bootstrap.get('certificateSecretUri') not in (expected_secret, expected_secret + '/')):
        raise OperationError('The recorded certificate job does not match the public TLS vault and certificate')
    return gateway, vault, name


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
    ctx.record('certificate-gateway-before', {'resourceId': gateway, 'etag': etag(before), 'tags': tags})
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
    deadline = clock() + timeout
    while True:
        values = [tls_leaf(host, port) for host, port in hosts]
        if all(v['thumbprint'] == metadata['thumbprint'] for v in values):
            return values
        if clock() >= deadline:
            raise OperationError('Public TLS still differs from the current Key Vault certificate')
        sleep(min(5, deadline - clock()))


def job_metadata(ctx, *, renew=False, thumbprint=None):
    from deployment.helpers.certificate_jobs import run_certificate_action
    from deployment.lifecycle import ArmTransport

    transport = ctx.transport if ctx.transport is not None else ArmTransport(ctx.config)
    arguments = {'rotateFromThumbprint': thumbprint} if thumbprint else None
    result = run_certificate_action(ctx.config, ctx.state['outputs'], transport,
                                    lambda path, record: ctx.save_private(path, record, overwrite=True),
                                    workspace=ctx.workspace, mode='tls', action='renew' if renew else 'check',
                                    arguments=arguments, retry_failed=True)
    return {'thumbprint': result['certificateThumbprint'].upper(), 'notBefore': result['notBefore'],
            'notAfter': result['expires'], 'certificateId': result['certificateId']}


def operate(ctx, args):
    gateway, vault, name = targets(ctx)
    if args.thumbprint and not re.fullmatch(r'[0-9A-Fa-f]{40}', args.thumbprint):
        raise OperationError('Planned rotation requires the current 40-character public leaf thumbprint')
    plan = {'stage': args.stage, 'status': 'planned', 'azureWrites': 0,
            'certificateConsumer': 'application-gateway', 'execution': 'container-app-job', 'pfxDownload': False,
            'publicTls': 'not-verified'}
    if not args.execute:
        ctx.record('certificate-plan', plan)
        return plan
    verify_references(ctx, gateway, vault, name)
    metadata = job_metadata(ctx)
    if args.stage == 'renew':
        previous_thumbprint = metadata['thumbprint']
        if args.thumbprint and args.thumbprint.upper() != metadata['thumbprint']:
            raise OperationError('The current Key Vault leaf changed; inspect it before requesting rotation')
        rotation_thumbprint = args.thumbprint.upper() if args.thumbprint else metadata['thumbprint'] if getattr(args, 'rotate', False) else None
        metadata = job_metadata(ctx, renew=True, thumbprint=rotation_thumbprint)
        if rotation_thumbprint and metadata['thumbprint'] == previous_thumbprint:
            raise OperationError('The requested rotation did not produce a different public leaf')
        ctx.record('certificate-renewed', {'metadata': metadata, 'publicTls': 'not-verified'})
    if args.stage in ('renew', 'sync'):
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
    rotation.add_argument('--rotate', action='store_true', help='確認した現在の公開leafを期限前に一度だけ更新する')
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
