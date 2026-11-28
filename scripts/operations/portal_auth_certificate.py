#!/usr/bin/env python3
"""Portal認証証明書の初期状態確認・更新・手動登録確認・切替・受入・復旧・旧版廃止。"""
from __future__ import annotations

import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import re
import subprocess
import time
import uuid

from deployment.helpers.portal_auth_certificate import (
    certificate_record, read_certificate_state, save_public_certificate,
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
        if action == 'Activate' and result['certificateId'] != arguments['certificateId']:
            raise OperationError('The worker returned another certificate version')
    pending['result'] = result
    save(ctx, state)
    return result


def finish_worker(ctx, state):
    if 'result' in state.get('jobRequest', {}):
        state.pop('jobRequest')
        save(ctx, state)


def verify_runtime(ctx, certificate, image):
    """Portal本体が指定した固定証明書を使うことを現物で確認します。"""
    prefix = '/subscriptions/' + ctx.config['subscriptionId'] + '/resourceGroups/' + ctx.config['resourceGroupNames']['svc'] + '/providers/Microsoft.App/'
    stem = ctx.config['resourceStem']
    expected = {'PORTAL_AUTH_KEY_ID': certificate['keyId'], 'PORTAL_AUTH_CERTIFICATE_THUMBPRINT': certificate['certificateThumbprint'],
                'MICROSOFT_CLIENT_ID': ctx.config['portal']['authentication']['clientId'], 'MICROSOFT_TENANT_ID': ctx.config['portal']['authentication']['tenantId']}
    application = prefix + 'containerApps/ca-' + stem + '-portal'
    value = ctx.verify_resource(application, APP_API, 'Microsoft.App/containerApps')
    containers = value.get('properties', {}).get('template', {}).get('containers', [])
    if len(containers) != 1 or containers[0].get('image', '').split('@')[-1] != image:
        raise OperationError('Portal image differs from the selected release')
    env = {item['name']: item.get('value') for item in containers[0].get('env', [])}
    if any(env.get(key) != val for key, val in expected.items()) or 'MICROSOFT_CLIENT_SECRET' in env:
        raise OperationError('Portal authentication differs from the selected certificate')


def deploy(ctx, slot, image):
    if not re.fullmatch('sha256:[a-f0-9]{64}', image or ''):
        raise OperationError('A recorded immutable Portal image is required for switching and rollback')
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/deployment/deploy_portal.py'), '--config', str(ctx.config_path),
                             '--certificate-slot', slot, '--image-digest', image, '--execute'], cwd=ROOT, check=False)
    if result.returncode:
        raise OperationError('Portal deployment did not complete; retain both certificate versions')


def perform(ctx, state, action, args):
    """手動登録と受入を挟み、現在版を無効化しない順序で更新します。"""
    phase = state['phase']
    retry_failed = getattr(args, 'resume_failed_worker', False)
    if not ctx.state['outputs'].get('certificateBootstrap'):
        raise OperationError('Complete the initial certificate job before rotating the Portal certificate')

    def invoke(action, arguments):
        return run_job_worker(ctx, state, action, arguments, retry_failed=retry_failed)

    if state.get('jobRequest', {}).get('result') and phase in ('prepared', 'switched', 'retired', 'initial'):
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
        if phase != 'switched' or not (args.login_verified and args.graph_verified):
            raise OperationError('Verify actual Portal login and Graph OBO before acceptance')
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
    parser.add_argument('--runtime-root', help='Explicit protected runtime checkout for checking initial state')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume-failed-worker', action='store_true', help='Resume a terminal failed worker using its saved KV request checkpoint')
    for flag in ('entra-registration-confirmed', 'login-verified', 'graph-verified', 'entra-old-certificate-removed'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'initialize':
            if args.resume_failed_worker or any(getattr(args, flag) for flag in
                    ('entra_registration_confirmed', 'login_verified', 'graph_verified', 'entra_old_certificate_removed')):
                raise OperationError('Initial state verification does not accept certificate rotation flags')
            from operations.portal_certificate_initialization import load_context, initialize
            ctx = load_context(args.config, args.runtime_root)
            with ctx.locked():
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
