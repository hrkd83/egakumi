#!/usr/bin/env python3
"""Portal専用のDB・SQL初期化・コンテナーを配備する。既定は計画、--executeで実行。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import REPOSITORY, local_paths
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.portal_authentication import prepare_secret
from deployment.helpers.portal_resources import resource_names
from deployment.helpers.portal_image import source_bundle, build_image, reusable_image, read_build_receipt
from deployment.helpers.portal_gateway import configure_portal_probe, configure_portal_callback_waf
from deployment.helpers.portal_backend_http import verify_portal_http_ready
from deployment.helpers.public_health_http import read_public_json
from deployment.helpers.portal_api_keys import prepare_api_key_groups, available_products, approved_product_catalog, verification_subscriptions
from deployment.helpers.management_maintenance import management_maintenance_inputs
from deployment.helpers.management_client_assets import management_client_inventory, update_management_client_assets
from deployment.helpers.management_sql_discovery import discover_management_sql_identity
from deployment.helpers.grpc_lifecycle import _prepare_credential
from deployment.helpers.subscription_profiles import SECRET_NAME, activate_profile_settings
from deployment.helpers.credential_image import checked
from deployment.lifecycle import ArmTransport, DeploymentError, protected_json, state_from_outputs, upgrade_journal
from waf_policy import reconcile as configure_data_waf
from standard_apis import infrastructure_api_definitions

APP_API = '2025-01-01'
ARM_API = '2022-09-01'


def run_json(arguments):
    """標準出力のJSONだけを読み、資格情報を含み得る失敗出力は表示しない。"""
    result = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise ValueError('Azure CLI or Bicep operation failed; inspect the selected command')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def group_scope(config):
    return f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"


def deployment_inputs(config, state, image_digest=None):
    """現在のinfraが保存した基盤出力から、今回の環境だけを参照する。"""
    if image_digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', image_digest):
        raise ValueError('Use an immutable sha256 image digest')
    if (any(state.get(key) != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem'))
            or state.get('apimInstanceId') != apim_instance_id(config)):
        raise ValueError('Core deployment state differs from the selected configuration')
    stem = config['resourceStem']
    outputs = state.get('outputs', {})
    if not isinstance(outputs, dict):
        raise ValueError('Current Core deployment outputs are required')
    if outputs.get('names', {}).get('apim') != apim_name(config):
        raise ValueError('Core APIM name differs from the selected construction instance')
    gateway = outputs.get('gateway')
    if (not isinstance(gateway, dict)
            or not isinstance(gateway.get('publicUrl'), str) or not gateway['publicUrl']):
        raise ValueError('Current Core Gateway public URL output is required')
    # load_configurationで検証済みの公開ホストと正式出力を厳密に照合します。
    # 別ホストやポート・パス等を持つURLを補完・正規化して配備を続けません。
    gateway_origin = 'https://' + config['gateway']['publicHostName']
    if gateway['publicUrl'] != gateway_origin:
        raise ValueError('Core Gateway public URL differs from the selected configuration')
    portal = outputs.get('portal', {})
    svc = group_scope(config) + '/providers/'
    identities = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['id']}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
    expected = {
        'applicationName': f'ca-{stem}-portal', 'environmentName': f'cae-{stem}',
        'registryName': 'acr' + stem.replace('-', ''),
    }
    for key, value in expected.items():
        if portal.get(key) != value:
            raise ValueError('Portal foundation output differs from the selected environment: ' + key)
    if str(portal.get('applicationId', '')).lower() != (svc + 'Microsoft.App/containerApps/' + expected['applicationName']).lower():
        raise ValueError('Portal application scope differs from the selected service group')
    runtime = portal.get('runtimeIdentity')
    schema = portal.get('schemaIdentity')
    if not isinstance(runtime, dict) or not isinstance(schema, dict):
        raise ValueError('Current Portal foundation identity outputs are required')
    for identity, name in ((runtime, f'id-{stem}-portal-pull'), (schema, f'id-{stem}-credential-schema')):
        if str(identity.get('id', '')).lower() != (identities + name).lower():
            raise ValueError('Portal SQL identity differs from the selected foundation')
    sql_server = portal.get('sharedSqlServer')
    if sql_server != f'sql-{stem}.database.windows.net':
        raise ValueError('Shared SQL server is missing from the completed foundation')
    maintenance = management_maintenance_inputs(config, portal.get('managementMaintenance'))
    management_client_inventory(config, state)
    profile_name = config['portalAuthentication']['credentialProviderName']
    profiles = [value for value in config['credentialProviders'] if value['name'] == profile_name]
    if len(profiles) != 1:
        raise ValueError('Portal requires one configured Entra credential profile')
    profile = profiles[0]
    return {
        'stem': stem, 'location': config['location'],
        'tags': {**config.get('tags', {}), 'product': config['productName'], 'environment': config['environmentName'], 'azd-env-name': config['environmentName']},
        'environmentName': expected['environmentName'], 'registryName': expected['registryName'],
        'apimName': apim_name(config),
        'apiKeyGroups': config.get('portalApiKeys', {}).get('groups', []),
        'apiKeyPlatformGroupId': config.get('portalApiKeys', {}).get('platformGroupId', ''),
        'apiKeyGroupNamePattern': config.get('portalApiKeys', {}).get('groupNamePattern', ''),
        'apiKeyVerificationSubscriptions': verification_subscriptions(config.get('portalApiKeys', {})),
        'apiKeyCatalogProducts': approved_product_catalog(config),
        'runtimeIdentity': runtime, 'schemaIdentity': schema, 'sqlServer': sql_server,
        'managementPrincipalId': maintenance['principalId'],
        'managementSqlClientId': maintenance.get('sqlClientId', ''),
        'managementRoleDefinitionIds': maintenance['roleDefinitionIds'],
        'databaseSettings': config['portalAuthentication'], 'vaultName': f'kv-{stem}',
        'credentialProviderName': profile_name, 'clientId': profile['clientId'], 'tenantId': profile['oauthTenantId'],
        'publicOrigin': 'https://' + config['portal']['publicHostName'],
        # Core出力との一致を確認した公開ドメインを使い、共通購読キーは配備しません。
        'gatewayOrigin': gateway_origin,
        'networkResourceGroup': config['resourceGroupNames']['nw'],
        'acaPrefix': config['network']['subnetPrefixes']['aca'],
        'expectedEgressIp': portal.get('expectedEgressIp', ''),
        'imageDigest': image_digest, 'deployApplication': False,
    }


def verify_foundation(config, values, transport):
    """今回使う共通リソースだけを読み、現在のマネージドID情報を取得する。"""
    for key in ('runtimeIdentity', 'schemaIdentity'):
        expected_id = values[key]['id']
        body = checked(transport, 'GET', expected_id + '?api-version=2023-01-31', label='Read Portal identity')
        properties = body.get('properties', {})
        if str(body.get('id', '')).lower() != expected_id.lower():
            raise ValueError('Portal identity response targeted another resource')
        identity = {'id': body['id'], 'name': body['name'], 'clientId': properties.get('clientId'), 'principalId': properties.get('principalId')}
        if not all(re.fullmatch(r'[0-9a-fA-F-]{36}', str(identity[field] or '')) for field in ('clientId', 'principalId')):
            raise ValueError('Portal managed identity is incomplete')
        values[key] = identity
    prefix = group_scope(config) + '/providers/'
    sql_id = prefix + 'Microsoft.Sql/servers/sql-' + values['stem']
    server = checked(transport, 'GET', sql_id + '?api-version=2023-08-01', label='Read shared SQL server')
    if (server.get('properties', {}).get('fullyQualifiedDomainName') != values['sqlServer']
            or server.get('properties', {}).get('publicNetworkAccess') != 'Disabled'):
        raise ValueError('Shared SQL server must use its existing private connection')
    environment = checked(transport, 'GET', prefix + 'Microsoft.App/managedEnvironments/' + values['environmentName'] + '?api-version=' + APP_API,
                          label='Read existing Container Apps environment')
    if environment.get('properties', {}).get('provisioningState') != 'Succeeded':
        raise ValueError('Existing Container Apps environment is not ready')
    apim_id = prefix + 'Microsoft.ApiManagement/service/' + values['apimName']
    apim = checked(transport, 'GET', apim_id + '?api-version=2024-05-01', label='Read existing Portal API management target')
    if str(apim.get('id', '')).lower() != apim_id.lower():
        raise ValueError('Portal API management target differs from the selected service group')
    approved_products = {product['productId'] for group in values['apiKeyGroups'] for product in group['products']}
    catalog_products = {product['productId'] for product in values['apiKeyCatalogProducts']}
    for product_id in sorted(approved_products | catalog_products):
        if product_id not in available_products(config) and product_id not in catalog_products:
            raise ValueError('APIキー管理のProductが選択中のAPI定義にありません。')
        product_path = apim_id + '/products/' + product_id
        product = checked(transport, 'GET', product_path + '?api-version=2024-05-01', label='Read approved API key product')
        if str(product.get('id', '')).lower() != product_path.lower() or not product.get('properties', {}).get('subscriptionRequired'):
            raise ValueError('APIキー管理には配備済みの購読必須Productが必要です。Gateway配備を確認してください。')
    secret = prefix + 'Microsoft.KeyVault/vaults/' + values['vaultName'] + '/secrets/core-credential-' + values['credentialProviderName']
    checked(transport, 'GET', secret + '?api-version=2023-07-01', label='Read shared Entra secret metadata')


def signing_secret(config, values, transport, root=REPOSITORY):
    """既存Key Vault値は更新しない。初回に限り署名鍵を生成して保護保存する。"""
    target = group_scope(config) + '/providers/Microsoft.KeyVault/vaults/' + values['vaultName'] + '/secrets/core-portal-auth-secret'
    response = transport.request('GET', target + '?api-version=2023-07-01')
    if response.get('statusCode') == 200:
        return ''
    if response.get('statusCode') != 404:
        raise ValueError('Portal signing secret metadata could not be read')
    return prepare_secret(config, root, protected_json)['portalAuthenticationSecret']


def profile_secret(config, values, transport, root=REPOSITORY):
    """PortalとAPIM専用の照会鍵を初回だけ作り、再配備では既存値を保持する。"""
    target = group_scope(config) + '/providers/Microsoft.KeyVault/vaults/' + values['vaultName'] + '/secrets/' + SECRET_NAME
    response = transport.request('GET', target + '?api-version=2023-07-01')
    if response.get('statusCode') == 200:
        return ''
    if response.get('statusCode') != 404:
        raise ValueError('Subscription profile secret metadata could not be read')
    return _prepare_credential(config, root, 'subscription-profile', protected_json)


def error_codes(error):
    """ARM診断はコードだけを返し、要求値を含むメッセージは返さない。"""
    if not isinstance(error, dict):
        return []
    values = [error['code']] if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', str(error.get('code', ''))) else []
    for child in error.get('details', [])[:5]:
        values.extend(error_codes(child))
    return list(dict.fromkeys(values))[:10]


def deploy_template(config, values, template, transport, *, timeout=1800, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    target = group_scope(config) + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-portal'
    path = target + '?api-version=' + ARM_API
    checked(transport, 'PUT', path, {'properties': {'mode': 'Incremental', 'template': template,
        'parameters': {key: {'value': value} for key, value in values.items()}}}, label='Deploy Portal resources')
    deadline, notice = monotonic() + timeout, monotonic() + 45
    while monotonic() < deadline:
        body = checked(transport, 'GET', path, label='Read Portal deployment')
        properties = body.get('properties', {})
        status = properties.get('provisioningState')
        if status == 'Succeeded':
            return {key: value['value'] for key, value in properties.get('outputs', {}).items()}
        if status in ('Failed', 'Canceled'):
            raise ValueError('Portal resource deployment failed: ' + ', '.join(error_codes(properties.get('error', {}))) + '; inspect ' + target)
        if monotonic() >= notice:
            progress('ポータル: Azureリソースへの反映を待っています。', flush=True)
            notice = monotonic() + 45
        sleep(10)
    raise ValueError('Portal deployment is still pending; inspect ' + target)


def wait_schema(config, execution, transport, *, timeout=900, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    if not re.fullmatch(r'[A-Za-z0-9-]+', str(execution or '')):
        raise ValueError('Portal SQL execution was not identified')
    target = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob'] + '/executions/' + execution
    deadline, notice = monotonic() + timeout, monotonic() + 45
    while monotonic() < deadline:
        body = checked(transport, 'GET', target + '?api-version=' + APP_API, allowed=(200, 404), label='Read Portal SQL initialization')
        status = body.get('properties', {}).get('status')
        if status == 'Succeeded':
            return
        if status in ('Failed', 'Stopped', 'Degraded'):
            raise ValueError('Portal SQL initialization failed; the application was not updated')
        if monotonic() >= notice:
            progress('ポータル: SQL初期化の完了を待っています。', flush=True)
            notice = monotonic() + 45
        sleep(10)
    raise ValueError('Portal SQL initialization is still pending; inspect its job execution')


def wait_application(config, transport, *, expected_image_digest=None, timeout=900, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """今回の期待イメージが反映された正常revisionまで待ち、旧Healthyだけで進まない。"""
    if expected_image_digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_image_digest):
        raise ValueError('Portal application readiness requires an immutable image digest')
    expected_image = (f"acr{config['resourceStem'].replace('-', '')}.azurecr.io/portal@{expected_image_digest}"
                      if expected_image_digest is not None else None)
    target = group_scope(config) + '/providers/Microsoft.App/containerApps/ca-' + config['resourceStem'] + '-portal'
    deadline, notice = monotonic() + timeout, monotonic() + 45
    while monotonic() < deadline:
        app = checked(transport, 'GET', target + '?api-version=' + APP_API, label='Read Portal application')
        properties = app.get('properties', {})
        revision = properties.get('latestRevisionName')
        ingress = properties.get('configuration', {}).get('ingress', {})
        containers = properties.get('template', {}).get('containers', [])
        expected_app = (expected_image is None or (len(containers) == 1
                        and containers[0].get('name') == 'portal' and containers[0].get('image') == expected_image
                        and ingress.get('allowInsecure') is True and ingress.get('targetPort') == 3000))
        if (properties.get('provisioningState') == 'Succeeded' and expected_app
                and isinstance(revision, str) and re.fullmatch(r'[a-z0-9-]+', revision)
                and revision == properties.get('latestReadyRevisionName')):
            ready = checked(transport, 'GET', target + '/revisions/' + revision + '?api-version=' + APP_API, label='Read Portal revision').get('properties', {})
            containers = ready.get('template', {}).get('containers', [])
            expected_revision = expected_image is None or (len(containers) == 1
                and containers[0].get('name') == 'portal' and containers[0].get('image') == expected_image)
            if (ready.get('healthState') == 'Healthy' and ready.get('provisioningState') == 'Provisioned'
                    and ready.get('runningState') == 'Running' and expected_revision):
                return {'revision': revision, 'expectedImageVerified': expected_image is not None}
        if properties.get('provisioningState') == 'Failed':
            raise ValueError('Portal application failed to provision')
        if monotonic() >= notice:
            progress('ポータル: 新しいリビジョンの起動を待っています。', flush=True)
            notice = monotonic() + 45
        sleep(10)
    raise ValueError('Portal revision is not ready; inspect Container Apps')


def verify_public(origin, *, timeout=180, sleep=time.sleep, monotonic=time.monotonic):
    """Keep the public JSON/cache contract with the host's verified TLS."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            status, body, cache_control = read_public_json(origin + '/api/health',
                accept='application/json', user_agent='egakumi-portal-deployment/1.0', timeout=20)
            if (type(status) is int and status == 200 and type(body) is dict
                    and body == {'status': 'ok', 'service': 'egakumi-portal'}
                    and isinstance(cache_control, str) and 'no-store' in cache_control.lower()):
                return
        except (OSError, ValueError):
            pass
        sleep(5)
    raise ValueError('Portal revision is running but the public health check did not succeed')


def deploy_release(config, values, template, transport, *, record=None, state=None,
                   client_prepare=update_management_client_assets):
    """SQLの準備が成功した場合だけ、新しいアプリを公開して疎通確認する。"""
    print('ポータル: DB・実行権限・SQL初期化ジョブを配備します。', flush=True)
    initialized = deploy_template(config, values, template, transport)
    client_prepare(config, state, initialized, transport, component='portal')
    # 初回の署名鍵は作成済み。2段階目は同じ値の新規バージョンも作成しません。
    values['signingSecret'] = ''
    values['profileSecret'] = ''
    job = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob']
    started = checked(transport, 'POST', job + '/start?api-version=' + APP_API, label='Start Portal SQL initialization')
    wait_schema(config, started.get('name'), transport)
    print('ポータル: Microsoft認証の戻り先用WAF設定を確認します。', flush=True)
    callback_waf = configure_portal_callback_waf(config, transport)
    print('ポータル: APIのデータ欄だけへ実測済みWAF除外を設定します。', flush=True)
    data_waf = configure_data_waf(config, transport, scopes=('portal',))
    print('ポータル: SQL初期化が完了しました。アプリを更新します。', flush=True)
    values['deployApplication'] = True
    deployed = deploy_template(config, values, template, transport)
    client_prepare(config, state, deployed, transport, component='portal')
    wait_application(config, transport, expected_image_digest=values['imageDigest'])
    http_readiness = verify_portal_http_ready(config, transport, expected_image_digest=values['imageDigest'])
    print('ポータル: 準備済みHTTP80後段とヘルス監視へApp Gatewayを揃えます。', flush=True)
    gateway_probe = configure_portal_probe(config, transport, record=record)
    print('ポータル: 公開URLから起動状態を確認します。', flush=True)
    verify_public(values['publicOrigin'])
    apim_id = group_scope(config) + '/providers/Microsoft.ApiManagement/service/' + values['apimName']
    profiles = activate_profile_settings(apim_id, transport, infrastructure_api_definitions(config['apiDefinitions']))
    return {'probe': gateway_probe, 'httpReadiness': http_readiness, 'callbackWaf': callback_waf,
            'dataWaf': data_waf, 'subscriptionProfiles': profiles}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--deployment-name', help='Completed Core subscription deployment, when using CI or no local state file')
    parser.add_argument('--image-digest', help='Use an already registered portal image instead of building it')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    config = load_configuration(args.config)
    workspace = local_paths(config, REPOSITORY)
    workspace.checked(Path(args.config))
    workspace.check_selection(config)
    args.state_file = workspace.checked(args.state_file or workspace.state / 'latest.json')
    if args.deployment_name:
        result = run_json(['az', 'deployment', 'sub', 'show', '--name', args.deployment_name,
            '--subscription', config['subscriptionId'], '-o', 'json'])
        if result.get('properties', {}).get('provisioningState') != 'Succeeded':
            raise ValueError('Select a successfully completed Core deployment')
        state = state_from_outputs(config,
            {key: value['value'] for key, value in result.get('properties', {}).get('outputs', {}).items()})
    else:
        state = json.loads(Path(args.state_file).read_text(encoding='utf-8'))
    values = deployment_inputs(config, state, args.image_digest)
    if args.execute:
        config = prepare_api_key_groups(args.config, config)
        values = deployment_inputs(config, state, args.image_digest)
    data, source_hash = (None, None) if args.image_digest else source_bundle()
    build_record = workspace.state / 'portal-build.json'
    transport = ArmTransport(config) if args.execute else None
    if not args.image_digest:
        values['imageDigest'] = reusable_image(config, state, source_hash, build_record, transport=transport)
    plan = {'status': 'planned', 'azureWrites': 0, 'azdUpRequired': False,
        'application': 'ca-' + config['resourceStem'] + '-portal', 'database': 'sqldb-' + config['resourceStem'] + '-portal',
        'imageBuild': values['imageDigest'] is None,
        'imageReuseVerificationPending': not args.execute and not args.image_digest,
        'apiKeyGroupCount': len(values['apiKeyGroups']),
        'verificationSubscriptionCount': len(values['apiKeyVerificationSubscriptions']),
        'steps': ['discover-management-sql-identity', 'build-and-push-image', 'prepare-database-and-permissions', 'prepare-apim-key-management-permission', 'initialize-sql', 'bootstrap-verification-subscriptions', 'configure-portal-auth-callback-waf', 'update-application', 'verify-expected-image-and-http-ingress', 'verify-firewall-http80', 'configure-portal-health-probe', 'schedule-group-observation', 'verify-public-health', 'enable-subscription-profiles']}
    protected_json(workspace.state / 'portal-plan.json', {**plan, 'parameters': values, 'sourceSha256': source_hash})
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    template = run_json(['az', 'bicep', 'build', '--file', str(REPOSITORY / 'apps/portal/deploy/main.bicep'), '--stdout'])
    verify_foundation(config, values, transport)
    identity_record = workspace.state / 'portal-management-sql-identity.json'
    if identity_record.exists():
        from deployment.helpers.private_permissions import check_private_permissions
        check_private_permissions(identity_record)
    values['managementSqlClientId'] = discover_management_sql_identity(
        config, management_client_inventory(config, state), transport,
        record=lambda value: protected_json(identity_record, value),
        prior_receipt=json.loads(identity_record.read_text(encoding='utf-8')) if identity_record.exists() else None)
    if values['imageDigest'] is None:
        values['imageDigest'] = build_image(config, state, transport, data=data, source_hash=source_hash,
            record=lambda value: protected_json(build_record, value), prior_receipt=read_build_receipt(build_record))
    values['signingSecret'] = signing_secret(config, values, transport)
    values['profileSecret'] = profile_secret(config, values, transport)
    gateway_readiness = deploy_release(config, values, template, transport,
                                      record=upgrade_journal(config, 'portal-http80'), state=state)
    result = {'status': 'deployed', 'database': plan['database'], 'application': plan['application'],
        'sqlInitialized': True, 'applicationReady': True, 'publicHealthVerified': True,
        'imageDigest': values['imageDigest'], 'publicUrl': values['publicOrigin'], 'azdUpExecuted': False,
        'gatewayProbe': gateway_readiness['probe'], 'callbackWaf': gateway_readiness['callbackWaf'],
        'httpReadiness': gateway_readiness['httpReadiness'],
        'subscriptionProfiles': gateway_readiness['subscriptionProfiles']}
    protected_json(workspace.state / 'portal-deployment.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, DeploymentError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, (ValueError, DeploymentError)) else 'check the selected configuration and completed Core state'
        raise SystemExit('Portal deployment stopped: ' + detail) from None
