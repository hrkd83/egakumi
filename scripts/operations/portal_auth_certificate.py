#!/usr/bin/env python3
"""Portal認証証明書の初期発行・更新・手動登録確認・切替・受入・復旧・旧版廃止。既定は計画。"""
from __future__ import annotations

import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import base64
import hashlib
import json
import re
import subprocess
import time
import uuid

from deployment.helpers.portal_auth_certificate import (
    MARKER, COMPUTE_API, certificate_record, read_certificate_state, save_public_certificate,
)
from operations.common import OperationContext, OperationError

ROOT = Path(__file__).resolve().parents[2]
APP_API = '2025-01-01'


class SafeParser(argparse.ArgumentParser):
    """引数値をエラー出力へ転載しません。"""
    def error(self, _message):
        self.exit(2, 'Portal certificate operation stopped: invalid arguments.\n')


def save(ctx, state):
    ctx.save_private(ctx.workspace.state / 'portal-auth-certificate.json', state, overwrite=True)


def targets(ctx):
    vm = ctx.state['outputs']['managementVm']['id']
    prefix = '/subscriptions/' + ctx.config['subscriptionId'] + '/resourceGroups/'
    expected = prefix + ctx.config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + ctx.config['resourceStem'] + '-mgmt'
    if vm.casefold() != expected.casefold():
        raise OperationError('The management VM differs from the selected Core state')
    ctx.verify_resource(vm, COMPUTE_API, 'Microsoft.Compute/virtualMachines')
    ctx.verify_resource(ctx.service_id('keyVault'), '2023-07-01', 'Microsoft.KeyVault/vaults')
    return vm


def remote_script(action, config):
    """配置済みworkerのハッシュを確認してから、秘密を含まない設定だけを渡します。"""
    digest = hashlib.sha256((ROOT / 'infra/guest/portal-auth-certificate.ps1').read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    encoded = base64.b64encode(json.dumps(config, separators=(',', ':')).encode()).decode()
    if action not in ('Prepare', 'Inspect', 'Activate', 'Retire'):
        raise OperationError('Unsupported certificate worker action')
    return '\n'.join([
        '#!/bin/bash', 'set -euo pipefail', 'set +x', 'umask 077',
        'task_worker=/var/lib/egakumi-portal-auth/worker.ps1',
        '[ ! -L /var/lib/egakumi-portal-auth ] && [ ! -L "$task_worker" ]',
        f'[ "$(sha256sum "$task_worker" | cut -d\' \' -f1)" = \'{digest}\' ]',
        f'export PortalAuthConfigJson=$(printf \'%s\' \'{encoded}\' | base64 --decode)',
        'pwsh -NoLogo -NoProfile -NonInteractive -Command \'& /var/lib/egakumi-portal-auth/worker.ps1 -Action ' + action + ' -ConfigJson $env:PortalAuthConfigJson\'',
    ])


def run_job_worker(ctx, state, action, arguments, *, retry_failed=False):
    from deployment.helpers.certificate_jobs import run_certificate_action
    from deployment.lifecycle import ArmTransport

    pending = state.get('jobRequest')
    if pending is None:
        pending = {'id': uuid.uuid4().hex, 'action': action, 'arguments': arguments}
        state['jobRequest'] = pending
        save(ctx, state)
    elif pending.get('action') != action or pending.get('arguments') != arguments:
        raise OperationError('Finish the saved certificate job before starting another action')
    transport = ctx.transport if ctx.transport is not None else ArmTransport(ctx.config)
    result = run_certificate_action(ctx.config, ctx.state['outputs'], transport,
                                    lambda path, record: ctx.save_private(path, record, overwrite=True),
                                    workspace=ctx.workspace, mode='portal', action=action.lower(),
                                    arguments=arguments, operation_id=pending['id'], retry_failed=retry_failed)
    if action == 'Retire':
        if result != {'retired': True, 'keyId': state['previous']['keyId']}:
            raise OperationError('Old key disabling was not confirmed')
    else:
        result = certificate_record(result, ctx.config)
        if action in ('Activate', 'Inspect') and result['certificateId'] != arguments['certificateId']:
            raise OperationError('The worker returned another certificate version')
    pending['result'] = result
    save(ctx, state)
    return result


def run_worker(ctx, state, action, arguments, *, sleep=time.sleep, clock=time.monotonic, timeout=960):
    """応答不明時は保存済みexact commandを照合し、PUTを重ねません。"""
    vm = targets(ctx)
    request_config = {'vaultName': 'kv-' + ctx.config['resourceStem'], **ctx.config['portal']['certificate'], **arguments}
    pending = state.get('vmRequest')
    if pending:
        if pending['action'] != action or pending['config'] != request_config:
            raise OperationError('Finish the saved VM command before starting another action')
        resource = pending['resourceId']
    else:
        resource = vm + '/runCommands/portal-auth-' + uuid.uuid4().hex
        state['vmRequest'] = {'resourceId': resource, 'action': action, 'config': request_config}
        save(ctx, state)
        response = ctx.request('PUT', resource, COMPUTE_API, {
            'location': ctx.config['location'],
            'properties': {'source': {'script': remote_script(action, request_config)}, 'asyncExecution': False,
                           'timeoutInSeconds': 900, 'treatFailureAsDeploymentFailure': True},
        })
        if response.get('statusCode') not in (200, 201, 202):
            raise OperationError('VM command acceptance is unknown; inspect the saved command')
    deadline = clock() + timeout
    while clock() < deadline:
        raw = ctx.run_cli(['az', 'vm', 'run-command', 'show', '--subscription', ctx.config['subscriptionId'],
                          '--resource-group', ctx.config['resourceGroupNames']['maint'], '--vm-name', vm.rsplit('/', 1)[-1],
                          '--run-command-name', resource.rsplit('/', 1)[-1], '--instance-view', '--only-show-errors', '--output', 'json'])
        body = json.loads(raw)
        if body.get('id') != resource:
            raise OperationError('The saved VM command cannot be confirmed; no command was resent')
        properties = body.get('properties', body)
        view = properties.get('instanceView') or body.get('instanceView') or {}
        if view.get('executionState') in ('Failed', 'Canceled', 'TimedOut') or properties.get('provisioningState') in ('Failed', 'Canceled'):
            raise OperationError('The saved VM command failed; review its protected checkpoint before recovery')
        if view.get('executionState') == 'Succeeded':
            if view.get('exitCode') != 0:
                raise OperationError('The certificate worker did not complete successfully')
            lines = [line[len(MARKER):] for line in view.get('output', '').splitlines() if line.startswith(MARKER)]
            if len(lines) != 1 or len(lines[0]) > 24000:
                raise OperationError('The certificate worker result is incomplete')
            result = json.loads(lines[0])
            if action == 'Retire':
                if result != {'retired': True, 'keyId': state['previous']['keyId']}:
                    raise OperationError('Old key disabling was not confirmed')
            else:
                result = certificate_record(result, ctx.config)
                if action in ('Activate', 'Inspect') and result['certificateId'] != arguments['certificateId']:
                    raise OperationError('The worker returned another certificate version')
            state['vmRequest']['result'] = result
            save(ctx, state)
            return result
        sleep(min(5, max(0, deadline - clock())))
    raise OperationError('The saved VM command is still pending; no additional command was sent')


def finish_worker(ctx, state):
    """結果を永続化した後だけexact commandを回収します。"""
    if 'result' in state.get('jobRequest', {}):
        state.pop('jobRequest')
        save(ctx, state)
        return
    pending = state.get('vmRequest')
    if not pending or 'result' not in pending:
        return
    if not pending.get('cleanupRequested'):
        pending['cleanupRequested'] = True
        save(ctx, state)
        response = ctx.request('DELETE', pending['resourceId'], COMPUTE_API)
        if response.get('statusCode') not in (200, 202, 204):
            raise OperationError('The completed certificate command could not be reclaimed')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = ctx.request('GET', pending['resourceId'], COMPUTE_API)
        if response.get('statusCode') == 404:
            break
        if response.get('statusCode') != 200 or (response.get('body') or {}).get('id') != pending['resourceId']:
            raise OperationError('VM command reclamation is not confirmed')
        time.sleep(5)
    else:
        raise OperationError('VM command reclamation remains pending; no deletion was repeated')
    state.pop('vmRequest')
    save(ctx, state)


def verify_runtime(ctx, certificate, image):
    """Portal本体・Graph定期ジョブが同じ固定証明書を使うことを現物で確認します。"""
    prefix = '/subscriptions/' + ctx.config['subscriptionId'] + '/resourceGroups/' + ctx.config['resourceGroupNames']['svc'] + '/providers/Microsoft.App/'
    stem = ctx.config['resourceStem']
    expected = {'PORTAL_AUTH_KEY_ID': certificate['keyId'], 'PORTAL_AUTH_CERTIFICATE_THUMBPRINT': certificate['certificateThumbprint'],
                'MICROSOFT_CLIENT_ID': ctx.config['portal']['authentication']['clientId'], 'MICROSOFT_TENANT_ID': ctx.config['portal']['authentication']['tenantId']}
    for kind, name in (('containerApps', 'ca-' + stem + '-portal'), ('jobs', 'caj-' + stem + '-psync')):
        value = ctx.verify_resource(prefix + kind + '/' + name, APP_API, 'Microsoft.App/' + kind)
        containers = value.get('properties', {}).get('template', {}).get('containers', [])
        if len(containers) != 1 or containers[0].get('image', '').split('@')[-1] != image:
            raise OperationError('Portal or reconciliation job image differs from the selected release')
        env = {item['name']: item.get('value') for item in containers[0].get('env', [])}
        if any(env.get(key) != val for key, val in expected.items()) or 'MICROSOFT_CLIENT_SECRET' in env:
            raise OperationError('Portal or reconciliation job authentication differs from the selected certificate')


def deploy(ctx, slot, image):
    if not re.fullmatch('sha256:[a-f0-9]{64}', image or ''):
        raise OperationError('A recorded immutable Portal image is required for switching and rollback')
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/deploy_portal.py'), '--config', str(ctx.config_path),
                             '--certificate-slot', slot, '--image-digest', image, '--execute'], cwd=ROOT, check=False)
    if result.returncode:
        raise OperationError('Portal deployment did not complete; retain both certificate versions')


def perform(ctx, state, action, args):
    """手動登録と受入を挟み、現在版を無効化しない順序で更新します。"""
    phase = state['phase']
    retry_failed = getattr(args, 'resume_failed_worker', False)
    if ctx.state['outputs'].get('certificateBootstrap'):
        def invoke(action, arguments):
            return run_job_worker(ctx, state, action, arguments, retry_failed=retry_failed)
    else:
        def invoke(action, arguments):
            return run_worker(ctx, state, action, arguments)
    if retry_failed and state.get('vmRequest'):
        pending = state['vmRequest']
        vm = targets(ctx)
        if not pending['resourceId'].startswith(vm + '/runCommands/portal-auth-'):
            raise OperationError('The saved recovery command belongs to another VM')
        observed = json.loads(ctx.run_cli(['az', 'vm', 'run-command', 'show', '--subscription', ctx.config['subscriptionId'],
            '--resource-group', ctx.config['resourceGroupNames']['maint'], '--vm-name', vm.rsplit('/', 1)[-1],
            '--run-command-name', pending['resourceId'].rsplit('/', 1)[-1], '--instance-view', '--only-show-errors', '--output', 'json']))
        view = observed.get('properties', observed).get('instanceView') or observed.get('instanceView') or {}
        if observed.get('id') != pending['resourceId'] or view.get('executionState') not in ('Failed', 'Canceled', 'TimedOut'):
            raise OperationError('Only an observed terminal failed worker can be resumed; pending work must not be repeated')
        ctx.record('portal-auth-worker-recovery', {'resourceId': pending['resourceId'], 'action': pending['action']})
        state.pop('vmRequest')
        save(ctx, state)
    if (state.get('vmRequest', {}).get('result') or state.get('jobRequest', {}).get('result')) and phase in ('prepared', 'switched', 'retired', 'initial'):
        finish_worker(ctx, state)
    if action == 'prepare':
        if phase not in ('initial', 'verified', 'retired', 'preparing', 'prepared'):
            raise OperationError('Finish or roll back the current rotation before preparing another certificate')
        if phase != 'prepared':
            state.setdefault('requestId', uuid.uuid4().hex)
            state['phase'] = 'preparing'
            save(ctx, state)
            candidate = invoke('Prepare', {'requestId': state['requestId'], 'currentKeyId': state['current']['keyId']})
            if candidate['keyId'] == state['current']['keyId']:
                raise OperationError('The renewal candidate reused the current key')
            state['candidate'] = candidate
            state['phase'] = 'prepared'
            save(ctx, state)
            finish_worker(ctx, state)
        save_public_certificate(ctx.workspace, state['candidate'], 'portal-auth-renewal-public.cer')
    elif action == 'confirm-registration':
        if phase != 'prepared' or not args.entra_registration_confirmed or not ctx.config['portal'].get('authentication'):
            raise OperationError('Confirm the new public certificate upload to the dedicated Portal Entra app first')
        state['registration'] = dict(ctx.config['portal']['authentication'])
        state['phase'] = 'registered'
        save(ctx, state)
    elif action == 'switch':
        if phase not in ('registered', 'switching') or state.get('registration') != ctx.config['portal'].get('authentication'):
            raise OperationError('The candidate registration does not match the selected Entra app')
        if phase == 'registered':
            receipt_path = ctx.workspace.checked(ctx.workspace.state / 'portal-deployment.json')
            from deployment.helpers.private_permissions import check_private_permissions
            check_private_permissions(receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt.get('authentication') != {**ctx.config['portal']['authentication'], 'keyId': state['current']['keyId'], 'certificateThumbprint': state['current']['certificateThumbprint']}:
                raise OperationError('The existing release is not recorded with the current certificate')
            state['imageDigest'] = receipt['imageDigest']
            state['previous'] = state['current']
            state['phase'] = 'switching'
            save(ctx, state)
        if phase == 'registered':
            verify_runtime(ctx, state['current'], state['imageDigest'])
        deploy(ctx, 'candidate', state['imageDigest'])
        verify_runtime(ctx, state['candidate'], state['imageDigest'])
        invoke('Activate', {'certificateId': state['candidate']['certificateId']})
        state['current'] = state.pop('candidate')
        state['phase'] = 'switched'
        save(ctx, state)
        finish_worker(ctx, state)
        save_public_certificate(ctx.workspace, state['current'])
    elif action == 'verify':
        if phase != 'switched' or not (args.login_verified and args.graph_verified and args.job_verified):
            raise OperationError('Verify actual login, Graph management and the scheduled job before acceptance')
        verify_runtime(ctx, state['current'], state['imageDigest'])
        state['phase'] = 'verified'
        save(ctx, state)
    elif action == 'rollback':
        if phase not in ('switching', 'switched', 'verified', 'rolling-back') or 'previous' not in state:
            raise OperationError('An enabled previous certificate and release are required for rollback')
        state['phase'] = 'rolling-back'
        save(ctx, state)
        deploy(ctx, 'previous', state['imageDigest'])
        verify_runtime(ctx, state['previous'], state['imageDigest'])
        invoke('Activate', {'certificateId': state['previous']['certificateId']})
        state['current'] = state.pop('previous')
        state.pop('candidate', None)
        state['phase'] = 'initial'
        state.pop('requestId', None)
        save(ctx, state)
        finish_worker(ctx, state)
        save_public_certificate(ctx.workspace, state['current'])
    elif action == 'retire':
        if phase != 'verified' or not args.entra_old_certificate_removed:
            raise OperationError('Complete acceptance and manually remove the old Entra certificate before retiring its key')
        verify_runtime(ctx, state['current'], state['imageDigest'])
        invoke('Retire', {'certificateId': state['previous']['certificateId'], 'currentKeyId': state['current']['keyId']})
        state['phase'] = 'retired'
        state.pop('requestId', None)
        save(ctx, state)
        finish_worker(ctx, state)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('action', choices=('initialize', 'prepare', 'confirm-registration', 'switch', 'verify', 'rollback', 'retire'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--runtime-root', help='Explicit protected runtime checkout for initialization from isolated source')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume-failed-worker', action='store_true', help='Resume a terminal failed worker using its saved KV request checkpoint')
    for flag in ('entra-registration-confirmed', 'login-verified', 'graph-verified', 'job-verified', 'entra-old-certificate-removed'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'initialize':
            if args.resume_failed_worker or any(getattr(args, flag) for flag in
                    ('entra_registration_confirmed', 'login_verified', 'graph_verified', 'job_verified', 'entra_old_certificate_removed')):
                raise OperationError('Initialization resumes recorded deployments; failed work requires checkpoint review')
            from operations.portal_certificate_initialization import load_context, initialize, locked
            ctx = load_context(args.config, args.runtime_root)
            with locked(ctx):
                print(json.dumps(initialize(ctx, execute=args.execute)))
            return 0
        if args.runtime_root is not None:
            raise OperationError('The explicit runtime root is currently supported only for initialization')
        ctx = OperationContext.load(args.config)
        state = read_certificate_state(ctx.config, ctx.workspace)
        print(json.dumps({'action': args.action, 'phase': state['phase'], 'execute': args.execute,
                          'renewalDue': state['current']['expires'] - time.time() <= ctx.config['portal']['certificate']['renewBeforeDays'] * 86400,
                          'privateKeyExport': False, 'entraChangesAutomatic': False}))
        if args.execute:
            with ctx.locked():
                state = read_certificate_state(ctx.config, ctx.workspace)
                perform(ctx, state, args.action, args)
            print('Portal certificate operation completed; retain the protected state for the next stage.')
        return 0
    except OperationError as error:
        print('Portal certificate operation stopped: ' + str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Portal certificate operation stopped; review prerequisites and the saved operation before retrying.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
