#!/usr/bin/env python3
"""Entra secretの作成・共有KV/CM切替・確認後廃止を分離する。既定は計画。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import stat
import uuid

from deployment.helpers.credential_pools import logical_profiles
from operations.common import OperationContext, OperationError

ROOT = Path(__file__).resolve().parents[2]
GRAPH = 'https://graph.microsoft.com/v1.0/'


def guid(value):
    """資格情報の対象を曖昧な表示名から作らない。"""
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise OperationError('An explicit credential/application UUID is required') from None


def read_secret(ctx, path):
    """保護された新値または切戻し値だけを読む。値は例外へ含めない。"""
    target = ctx.output_path(path)
    try:
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()):
            raise ValueError()
        value = target.read_text(encoding='utf-8').rstrip('\r\n')
        if not value:
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError):
        raise OperationError('An owner-only nonempty credential file is required') from None


def secret_path(ctx, path, scope, *, new=False):
    target = ctx.output_path(path)
    inputs = {ctx.output_path(p) for p in scope['inputFiles']}
    if not target.is_relative_to(ctx.workspace.secrets) or target in inputs:
        raise OperationError('Use a separate file in the selected secrets directory')
    if new and (target.exists() or target.is_symlink()):
        raise OperationError('The new credential output already exists')
    if target.exists():
        info = target.stat()
        if any((info.st_dev, info.st_ino) == (p.stat().st_dev, p.stat().st_ino) for p in inputs):
            raise OperationError('New and current credential files must not be aliases')
    return target


def selection(ctx, profile):
    """同じアプリを使用する既存CM入力と共有KVの論理profileを選ぶ。"""
    profiles = logical_profiles(ctx.config)
    if profile not in profiles:
        raise OperationError('Select an existing login credential profile')
    client_id = guid(profiles[profile]['clientId'])
    names = sorted(n for n, p in profiles.items() if guid(p['clientId']) == client_id)
    tenant = guid(ctx.config.get('entraTenantId', ctx.config['azureTenantId']))
    if any(guid(profiles[name]['tenantId']) != tenant for name in names):
        raise OperationError('The selected profiles must use the configured Entra application tenant')
    providers = [p for p in ctx.config['credentialProviders'] if guid(p['clientId']) == client_id]
    scope = {'subscriptionId': ctx.config['subscriptionId'],
             'apimInstanceId': ctx.config['services']['apimInstanceId'],
             'tenantId': tenant,
             'clientId': client_id, 'profiles': names,
             'inputFiles': sorted({str(ctx.output_path(p['clientSecretFile'])) for p in providers})}
    return scope


def graph(ctx, method, path, body=None):
    """既存CLI資格情報のみ使用。認証・同意・権限追加は行わない。"""
    args = ['az', 'rest', '--method', method, '--url', GRAPH + path,
            '--only-show-errors', '--output', 'json']
    request_file = None
    try:
        if body is not None:
            request_file = ctx.save_private(ctx.workspace.root / 'generated' /
                ('entra-request-' + uuid.uuid4().hex + '.json'), body)
            args += ['--body', '@' + str(request_file)]
        raw = ctx.run_cli(args)
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        raise OperationError('The Graph response was not recognized; inspect the application before retrying') from None
    finally:
        if request_file is not None:
            request_file.unlink(missing_ok=True)


def application(ctx, scope):
    tenant = ctx.run_cli(['az', 'account', 'show', '--query', 'tenantId', '--output', 'tsv']).strip()
    if guid(tenant) != guid(scope['tenantId']):
        raise OperationError('The existing CLI tenant must match the Entra application tenant')
    app = graph(ctx, 'GET', f"applications(appId='{scope['clientId']}')?$select=id,appId,passwordCredentials")
    if guid(app.get('appId')) != scope['clientId'] or not isinstance(app.get('passwordCredentials'), list):
        raise OperationError('The Entra application identity could not be verified')
    guid(app.get('id'))
    return app


def credential(app, key_id, *, required=True):
    found = [v for v in app['passwordCredentials'] if guid(v.get('keyId')) == key_id]
    if len(found) > 1 or required and len(found) != 1:
        raise OperationError('The selected credential metadata is missing or ambiguous')
    return found[0] if found else None


def valid_credential(value):
    try:
        end = datetime.fromisoformat(value['endDateTime'].replace('Z', '+00:00'))
        return end.tzinfo is not None and end > datetime.now(timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False


def save(ctx, path, receipt, phase):
    receipt['phase'] = phase
    receipt['updatedAt'] = datetime.now(timezone.utc).isoformat()
    ctx.save_private(path, receipt, overwrite=path.exists())


def update_consumers(ctx, scope, receipt, receipt_path, values, label):
    """既存moduleだけのIncremental配備とG。旧credentialは削除しない。"""
    vault_id = ctx.service_id('keyVault')
    expected = (f"/subscriptions/{ctx.config['subscriptionId']}/resourceGroups/"
                f"{ctx.config['resourceGroupNames']['svc']}/providers/Microsoft.KeyVault/vaults/kv-{ctx.config['resourceStem']}")
    if vault_id.casefold() != expected.casefold():
        raise OperationError('Key Vault does not match the selected environment')
    ctx.verify_resource(vault_id, '2023-07-01', 'Microsoft.KeyVault/vaults')
    ctx.verify_resource(ctx.service_id('apim'), '2024-05-01', 'Microsoft.ApiManagement/service')
    parameter_file = ctx.save_private(ctx.workspace.root / 'generated' /
        ('entra-rotation-' + uuid.uuid4().hex + '.parameters.json'), {
            '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
            'contentVersion': '1.0.0.0', 'parameters': {
                'vaultName': {'value': vault_id.rsplit('/', 1)[-1]},
                'profiles': {'value': {name: {} for name in scope['profiles']}},
                'providerSecrets': {'value': values}}})
    save(ctx, receipt_path, receipt, label + '-kv-requested')
    try:
        ctx.run_cli(['az', 'deployment', 'group', 'create', '--subscription', ctx.config['subscriptionId'],
            '--resource-group', ctx.config['resourceGroupNames']['svc'],
            '--name', 'entra-rotation-' + receipt['operationId'], '--mode', 'Incremental',
            '--template-file', str(ROOT / 'infra/modules/shared-entra-secrets.bicep'),
            '--parameters', '@' + str(parameter_file), '--only-show-errors', '--output', 'none'])
    finally:
        parameter_file.unlink(missing_ok=True)
    save(ctx, receipt_path, receipt, label + '-gateway-requested')
    ctx.run_cli([sys.executable, '-B', str(ROOT / 'scripts/deploy_gateway.py'),
                 '--config', str(ctx.config_path), '--execute'])


def operate(ctx, args):
    scope = selection(ctx, args.profile)
    path = ctx.output_path(args.receipt)
    if not path.is_relative_to(ctx.workspace.state / 'operations'):
        raise OperationError('The rotation receipt must belong to state/operations')
    if args.stage == 'create':
        if path.exists() or not args.new_secret_file or not args.old_key_id or not 1 <= args.days <= 180:
            raise OperationError('Creation requires a new receipt/output, old key ID and a lifetime of 1..180 days')
        guid(args.old_key_id)
        secret_path(ctx, args.new_secret_file, scope, new=True)
    plan = {'stage': args.stage, 'status': 'planned', 'azureWrites': 0,
            'freshAuthentication': 'operator-confirmation-required', 'oldCredentialRetained': args.stage != 'retire'}
    if not args.execute:
        ctx.record('entra-secret-plan', {**plan, 'scope': scope})
        return plan
    if path.exists():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()):
            raise OperationError('An owner-only rotation receipt is required')
    receipt = json.loads(path.read_text()) if path.exists() else None
    if receipt is not None and not isinstance(receipt, dict):
        raise OperationError('The rotation receipt format is not recognized')
    if receipt and receipt.get('scope') != scope:
        raise OperationError('This operation receipt belongs to different inputs')
    for source in scope['inputFiles']:
        if not ctx.output_path(source).is_relative_to(ctx.workspace.secrets):
            raise OperationError('Current credential inputs must belong to the selected secrets directory')
        read_secret(ctx, source)
    if args.stage == 'create':
        if receipt:
            raise OperationError('Creation was already requested; inspect its receipt instead of creating another secret')
        if not args.new_secret_file or not args.old_key_id or not 1 <= args.days <= 180:
            raise OperationError('Creation requires a new protected file, the old key ID and a lifetime of 1..180 days')
        app = application(ctx, scope)
        old_id = guid(args.old_key_id)
        credential(app, old_id)
        new_file = ctx.save_private(secret_path(ctx, args.new_secret_file, scope, new=True), '')
        receipt = {'operationId': uuid.uuid4().hex, 'scope': scope, 'oldKeyId': old_id,
                   'newFile': str(new_file), 'applicationObjectId': guid(app['id'])}
        save(ctx, path, receipt, 'create-requested')
        result = graph(ctx, 'POST', 'applications/' + receipt['applicationObjectId'] + '/addPassword', {
            'passwordCredential': {'displayName': 'Egakumi rotation ' + receipt['operationId'],
                'endDateTime': (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()}})
        new_id = guid(result.get('keyId'))
        value = result.get('secretText')
        if new_id == old_id or not isinstance(value, str) or not value:
            raise OperationError('Creation response was incomplete; inspect the application and keep the old credential')
        receipt['newKeyId'] = new_id
        save(ctx, path, receipt, 'created-value-save-pending')
        ctx.save_private(new_file, value, overwrite=True)
        save(ctx, path, receipt, 'created')
        return {'stage': 'create', 'status': 'created-old-retained'}
    if args.stage == 'switch' and receipt is None:
        if not all((args.new_secret_file, args.old_key_id, args.new_key_id)):
            raise OperationError('Switch requires a protected new Value and explicit old/new key IDs')
        receipt = {'operationId': uuid.uuid4().hex, 'scope': scope,
                   'oldKeyId': guid(args.old_key_id), 'newKeyId': guid(args.new_key_id),
                   'newFile': str(secret_path(ctx, args.new_secret_file, scope))}
        read_secret(ctx, receipt['newFile'])
        save(ctx, path, receipt, 'provided')
    if not receipt or receipt.get('newKeyId') == receipt.get('oldKeyId'):
        raise OperationError('A bound new/old credential receipt is required')
    app = application(ctx, scope)
    if app['id'] != receipt.get('applicationObjectId', app['id']):
        raise OperationError('The Entra application object has changed')
    receipt['applicationObjectId'] = guid(app['id'])
    new = credential(app, guid(receipt['newKeyId']))
    if args.stage != 'rollback' and not valid_credential(new):
        raise OperationError('The new credential is not currently valid')
    if args.stage == 'switch':
        if receipt['phase'] not in ('created', 'provided'):
            raise OperationError('A switch was already attempted; inspect partial work before any further action')
        credential(app, guid(receipt['oldKeyId']))
        value = read_secret(ctx, secret_path(ctx, receipt['newFile'], scope))
        backups = []
        for index, source in enumerate(scope['inputFiles']):
            old = read_secret(ctx, source)
            candidate = ctx.workspace.secrets / ('entra-' + receipt['operationId'] + '-' + str(index) + '.previous')
            if candidate == Path(receipt['newFile']):
                raise OperationError('The backup and new Value paths collide')
            backup = ctx.save_private(secret_path(ctx, candidate, scope, new=True), old)
            backups.append({'input': source, 'backup': str(backup)})
        receipt['backups'] = backups
        save(ctx, path, receipt, 'switch-inputs-requested')
        for source in scope['inputFiles']:
            ctx.save_private(source, value, overwrite=True)
        receipt['inputDigests'] = {source: hashlib.sha256(Path(source).read_bytes()).hexdigest() for source in scope['inputFiles']}
        update_consumers(ctx, scope, receipt, path, {n: value for n in scope['profiles']}, 'switch')
        save(ctx, path, receipt, 'switched-auth-unverified')
        return {'stage': 'switch', 'status': 'switched-auth-unverified', 'oldCredentialRetained': True}
    if args.stage == 'rollback':
        old = credential(app, guid(receipt['oldKeyId']))
        if (not valid_credential(old) or not receipt.get('backups')
                or not receipt['phase'].startswith('switch')):
            raise OperationError('Rollback requires the retained valid old credential and its protected backups')
        restore = {item['input']: read_secret(ctx, item['backup']) for item in receipt['backups']}
        if set(restore) != set(scope['inputFiles']) or len(set(restore.values())) != 1:
            raise OperationError('Rollback input mapping is incomplete or ambiguous')
        save(ctx, path, receipt, 'rollback-inputs-requested')
        for source, value in restore.items():
            ctx.save_private(source, value, overwrite=True)
        update_consumers(ctx, scope, receipt, path, {n: next(iter(restore.values())) for n in scope['profiles']}, 'rollback')
        save(ctx, path, receipt, 'rolled-back-auth-unverified')
        return {'stage': 'rollback', 'status': 'rolled-back-auth-unverified'}
    if receipt['phase'] != 'switched-auth-unverified' or not args.verified_new_sign_in or not args.verified_new_connection:
        raise OperationError('Retirement requires a successful switch and explicit fresh sign-in/connection confirmations')
    expected = receipt.get('inputDigests', {})
    if set(expected) != set(scope['inputFiles']) or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != expected[p] for p in scope['inputFiles']):
        raise OperationError('Credential inputs changed after switching; keep the old credential')
    receipt['authConfirmation'] = {'source': 'operator-attestation', 'newSignIn': True, 'newConnection': True}
    old_id = guid(receipt['oldKeyId'])
    if credential(app, old_id, required=False) is not None:
        save(ctx, path, receipt, 'retirement-requested')
        graph(ctx, 'POST', 'applications/' + receipt['applicationObjectId'] + '/removePassword', {'keyId': old_id})
        if credential(application(ctx, scope), old_id, required=False) is not None:
            raise OperationError('Credential removal is not confirmed; do not repeat it blindly')
    save(ctx, path, receipt, 'retired')
    return {'stage': 'retire', 'status': 'old-credential-retired', 'postRetirementAuthentication': 'not-verified'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--stage', choices=('create', 'switch', 'rollback', 'retire'), required=True)
    parser.add_argument('--new-secret-file')
    parser.add_argument('--old-key-id')
    parser.add_argument('--new-key-id')
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--verified-new-sign-in', action='store_true')
    parser.add_argument('--verified-new-connection', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
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
        print('Secret operation stopped; inspect the protected receipt and keep the old credential.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
