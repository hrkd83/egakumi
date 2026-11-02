#!/usr/bin/env python3
"""GatewayのDB・CM・Broker・APIを順に配備する。既定は計画のみ、--executeで実行。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from deployment.helpers.configuration import (
    build_bicep_configuration, load_configuration_source, load_credential_provider_secrets,
)
from deployment.helpers.paths import REPOSITORY
from deployment.helpers.credential_image import (
    build_image, checked, read_build_receipt, reusable_image, source_bundle,
)
from deployment.helpers.credential_broker_lifecycle import prepare_secret
from deployment.helpers.credential_pools import broker_enabled, resource_names
from deployment.helpers.grpc_lifecycle import configure_named_values
from deployment.lifecycle import ArmTransport, DeploymentError, protected_json, state_from_outputs
from standard_apis import resolve_publication_configuration
from waf_policy import reconcile as configure_data_waf, selected_profiles as data_waf_profiles

# ハイフンを含むアプリフォルダーの配備実装は、この入口からだけ読み込みます。
sys.path.insert(0, str(REPOSITORY / 'apps/gateway/deploy'))
import api_publication
from broker_deployment import wait_for_application, wait_for_schema
from gateway_ingress import (
    configure_gateway_identity_callback_waf, configure_gateway_speech_waf, speech_content_type_paths,
    configure_gateway_document_waf, document_upload_limit,
)

APP_API = '2025-01-01'
ARM_API = '2022-09-01'
MODULE = REPOSITORY / 'apps/gateway/deploy/main.bicep'
BUILD_RECORD = REPOSITORY / '.state/credential-broker-build.json'


def group_scope(config):
    """今回選んだ環境のサービス用RGを返す。"""
    return f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"


def foundation_inputs(config, state):
    """構築済みの共通基盤を参照する。旧構築記録でもazd upのやり直しは不要。"""
    if any(state.get(key) != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')):
        raise ValueError('Core deployment state differs from the selected configuration')
    output, stem = state['outputs'], config['resourceStem']
    foundation = copy.deepcopy(output.get('gatewayFoundation') or {})
    if not foundation:
        # フォルダー移行前に作った既存環境を、そのまま同じIDで更新します。
        broker = output.get('credentialBroker', {})
        portal = output.get('portal', {})
        host = urlsplit(broker.get('url', '')).hostname or ''
        prefix = f'ca-{stem}-credential-broker.'
        foundation = {
            'resourceGroup': config['resourceGroupNames']['svc'],
            'identityResourceGroup': config['resourceGroupNames']['id'],
            'apimName': f'apim-{stem}', 'apimPrincipalId': broker.get('apimPrincipalId', ''),
            'schemaIdentity': broker.get('schemaIdentity') or portal.get('schemaIdentity', {}),
            'sqlServer': broker.get('sqlServer') or portal.get('sharedSqlServer', ''),
            'sqlPrivateEndpointId': broker.get('privateEndpointId') or portal.get('sharedSqlPrivateEndpointId', ''),
            'environmentName': broker.get('environmentName') or portal.get('environmentName', ''),
            'environmentDomain': host[len(prefix):] if host.startswith(prefix) else '',
            'registryName': broker.get('registryName') or portal.get('registryName', ''),
            'vaultName': f'kv-{stem}',
        }
    expected = {
        'resourceGroup': config['resourceGroupNames']['svc'],
        'identityResourceGroup': config['resourceGroupNames']['id'],
        'apimName': f'apim-{stem}', 'vaultName': f'kv-{stem}',
        'environmentName': f'cae-{stem}', 'registryName': 'acr' + stem.replace('-', ''),
        'sqlServer': f'sql-{stem}.database.windows.net',
    }
    for name, value in expected.items():
        if foundation.get(name) != value:
            raise ValueError('Core foundation differs from this environment: ' + name)
    identity = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['id']}"
                f'/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-{stem}-credential-schema')
    if str(foundation.get('schemaIdentity', {}).get('id', '')).lower() != identity.lower():
        raise ValueError('Core schema identity differs from this environment')
    vault = 'https://' + expected['vaultName'] + '.vault.azure.net/secrets/'
    features = output.get('ingressSecrets', {})
    foundation['l7IngressSecretIdentifier'] = vault + 'core-l7-ingress-secret' if output.get('grpc', {}).get('enabled') else ''
    foundation['realtimeTicketSecretIdentifier'] = vault + 'core-realtime-ticket-key' if features.get('realtimeEnabled') else ''
    return foundation


def build_state(state, foundation):
    """既存の共通イメージビルド処理へ、参照するACR情報だけを渡す。"""
    result = copy.deepcopy(state)
    result['outputs']['credentialBroker'] = {**result['outputs'].get('credentialBroker', {}),
        'registryName': foundation['registryName']}
    return result


def verify_foundation(config, foundation, transport):
    """配備先の共通リソースと現在のマネージドIDを読み取る。変更は行わない。"""
    service = group_scope(config) + '/providers/'
    apim = checked(transport, 'GET', service + 'Microsoft.ApiManagement/service/' + foundation['apimName']
                   + '?api-version=2024-05-01', label='Read Gateway APIM')
    principal = apim.get('identity', {}).get('principalId')
    if (apim.get('properties', {}).get('virtualNetworkType') != 'Internal'
            or not re.fullmatch(r'[0-9a-fA-F-]{36}', str(principal or ''))):
        raise ValueError('The selected internal APIM and its managed identity must be ready')
    foundation['apimPrincipalId'] = principal
    schema = checked(transport, 'GET', foundation['schemaIdentity']['id'] + '?api-version=2023-01-31',
                     label='Read shared SQL schema identity')
    foundation['schemaIdentity'] = {'id': schema['id'], 'name': schema['name'], **schema['properties']}
    server = checked(transport, 'GET', service + 'Microsoft.Sql/servers/sql-' + config['resourceStem']
                     + '?api-version=2023-08-01', label='Read shared SQL server')
    if (server.get('properties', {}).get('fullyQualifiedDomainName') != foundation['sqlServer']
            or server.get('properties', {}).get('publicNetworkAccess') != 'Disabled'):
        raise ValueError('The shared SQL server must use its existing private connection')
    environment = checked(transport, 'GET', service + 'Microsoft.App/managedEnvironments/' + foundation['environmentName']
                          + '?api-version=' + APP_API, label='Read shared Container Apps environment')
    properties = environment.get('properties', {})
    if properties.get('provisioningState') != 'Succeeded' or not properties.get('defaultDomain'):
        raise ValueError('The shared Container Apps environment is not ready')
    foundation['environmentDomain'] = properties['defaultDomain']


def broker_secret(config, foundation, transport):
    """既存の内部共有鍵を保持し、初回だけローカルで生成する。"""
    target = (group_scope(config) + '/providers/Microsoft.KeyVault/vaults/' + foundation['vaultName']
              + '/secrets/core-credential-broker-key?api-version=2023-07-01')
    response = transport.request('GET', target)
    if response.get('statusCode') == 200:
        return ''
    if response.get('statusCode') != 404:
        raise ValueError('Credential broker key metadata could not be read')
    return prepare_secret(config, REPOSITORY, protected_json)['credentialBrokerKey']


def run_json(arguments):
    """CLIのJSONを読み取る。失敗時に秘密を含み得る標準出力・エラーは表示しない。"""
    result = subprocess.run(arguments, cwd=REPOSITORY, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise ValueError('Azure CLI or Bicep operation failed; inspect the selected deployment')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def deploy_resources(config, values, template, transport, *, timeout=1800, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """差分をIncrementalで反映し、成功した配備の公開出力を返す。"""
    target = group_scope(config) + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-gateway'
    path = target + '?api-version=' + ARM_API
    checked(transport, 'PUT', path, {'properties': {'mode': 'Incremental', 'template': template,
        'parameters': {key: {'value': value} for key, value in values.items()}}}, label='Deploy Gateway resources')
    deadline, notice = monotonic() + timeout, monotonic() + 45
    while monotonic() < deadline:
        body = checked(transport, 'GET', path, label='Read Gateway deployment')
        properties = body.get('properties', {})
        if properties.get('provisioningState') == 'Succeeded':
            return {key: value['value'] for key, value in properties.get('outputs', {}).items()}
        if properties.get('provisioningState') in ('Failed', 'Canceled'):
            raise ValueError('Gateway resource deployment failed; inspect ' + target)
        if monotonic() >= notice:
            progress('Gateway: Azureリソースへの反映を待っています。', flush=True)
            notice = monotonic() + 45
        sleep(10)
    raise ValueError('Gateway deployment is still pending; inspect ' + target)


def deploy_release(config, state, values, templates, transport, *, deploy=deploy_resources,
                   schema_wait=wait_for_schema, readiness_wait=wait_for_application,
                   refresh=configure_named_values, publish=api_publication.execute_plan,
                   callback_waf=configure_gateway_identity_callback_waf,
                   speech_waf=configure_gateway_speech_waf,
                   document_waf=configure_gateway_document_waf, data_waf=configure_data_waf, progress=print):
    """SQL成功後だけアプリとAPIを更新する。共有鍵・CM接続・既存SQLデータは維持する。"""
    enabled = broker_enabled(config)
    if enabled:
        progress('Gateway: 資格情報DB・CM・SQL初期化ジョブを配備します。', flush=True)
        deploy(config, values, templates['gateway'], transport)
        values['brokerKey'] = ''  # 2段階目に同じ鍵の新バージョンを作らない。
        job = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob']
        execution = checked(transport, 'POST', job + '/start?api-version=' + APP_API,
                            label='Start Gateway SQL initialization')
        schema_wait(config, execution.get('name'), transport)
    progress('Gateway: ブローカーとAPIM接続設定を反映します。', flush=True)
    values['deployApplication'] = True
    deployed = deploy(config, values, templates['gateway'], transport)
    if enabled:
        readiness_wait(config, transport)
    current = copy.deepcopy(state)
    current['outputs']['credentialBroker'] = deployed.get('credentialBroker', {})
    refresh(config, current['outputs'], transport)
    progress('Gateway: API・製品・購読・ポリシーを公開します。', flush=True)
    plan = api_publication.prepare_plan(config, current)
    publication = publish(config, current, plan, templates['apis'], transport)
    callback = None
    if enabled:
        progress('Gateway: 本人認証callback用WAFを設定します。', flush=True)
        callback = callback_waf(config, transport)
    speech = None
    if speech_content_type_paths(config):
        progress('Gateway: 音声APIのSSML/WAV用WAFを設定します。', flush=True)
        speech = speech_waf(config, transport)
    document = None
    if document_upload_limit(config) is not None:
        progress('Gateway: 文書解析APIのファイル受付用WAFを設定します。', flush=True)
        document = document_waf(config, transport)
    data = None
    if data_waf_profiles(config, ('gateway',)):
        progress('Gateway: 公開APIのデータ欄だけへ実測済みWAF除外を設定します。', flush=True)
        data = data_waf(config, transport, scopes=('gateway',))
    return {'status': 'deployed', 'azdUpExecuted': False,
            'sqlInitialized': enabled, 'applicationReady': enabled,
            'imageDigest': values['imageDigest'] or None,
            'credentialBroker': deployed.get('credentialBroker', {}),
            'apiPublication': publication, 'callbackWaf': callback, 'speechWaf': speech, 'documentWaf': document,
            'dataWaf': data, 'runtimeVerified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-file', type=Path, default=REPOSITORY / '.state/latest.json')
    parser.add_argument('--deployment-name', help='Completed Core subscription deployment for CI or a missing local state')
    parser.add_argument('--image-digest', help='Use an existing credential-broker image by immutable sha256 digest')
    parser.add_argument('--output', type=Path, default=REPOSITORY / '.state/gateway-plan.json')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    source = load_configuration_source(args.config)
    if args.deployment_name:
        # 計画時も、このオプションを使った場合に限りCore配備を読み取ります。
        from deployment.helpers.configuration import load_configuration
        initial = load_configuration(args.config)
        deployed = run_json(['az', 'deployment', 'sub', 'show', '--name', args.deployment_name,
                             '--subscription', initial['subscriptionId'], '-o', 'json'])
        if deployed.get('properties', {}).get('provisioningState') != 'Succeeded':
            raise ValueError('Select a successfully completed Core deployment')
        state = state_from_outputs(initial, {key: value['value'] for key, value in deployed['properties']['outputs'].items()})
    else:
        state = json.loads(args.state_file.read_text(encoding='utf-8'))
    config = resolve_publication_configuration(source, state)
    if args.execute and config.get('publishApis') is not True:
        raise ValueError('Set publishApis=true to deploy the Gateway APIs')
    foundation = foundation_inputs(config, state)
    if args.image_digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image_digest):
        raise ValueError('Use an immutable sha256 image digest')
    publication_plan = api_publication.prepare_plan(config, state)
    active = broker_enabled(config)
    data, source_hash = source_bundle() if active and not args.image_digest else (None, None)
    image_state = build_state(state, foundation)
    digest = args.image_digest or (reusable_image(config, image_state, source_hash, BUILD_RECORD) if active else '')
    values = {'configuration': build_bicep_configuration(config), 'foundation': foundation,
              'imageDigest': digest or '', 'deployApplication': False}
    summary = {'status': 'planned', 'azureWrites': 0, 'azdUpRequired': False,
        'apiSelection': 'specified' if source.get('apiDefinitions') else 'standard',
        'apiCount': len(config['apiDefinitions']), 'credentialBrokerEnabled': active,
        'application': resource_names(config)['application'] if active else None,
        'database': resource_names(config)['database'] if active else None,
        'imageBuild': active and not digest,
        'steps': ['build-or-reuse-image', 'prepare-database-and-credential-manager', 'initialize-sql',
                  'update-broker-and-apim-settings', 'publish-apis-products-subscriptions', 'reconcile-released-api-membership', 'remove-default-apim-samples']
                 + (['configure-gateway-identity-callback-waf'] if active else [])
                 + (['configure-gateway-speech-waf'] if speech_content_type_paths(config) else [])
                 + (['configure-gateway-document-waf'] if document_upload_limit(config) is not None else [])
                 + (['configure-gateway-data-waf'] if data_waf_profiles(config, ('gateway',)) else [])}
    protected_json(args.output, {**summary, 'parameters': values, 'sourceSha256': source_hash,
                                  'apiPublication': publication_plan})
    if not args.execute:
        print(json.dumps({**summary, 'plan': str(args.output)}, ensure_ascii=False))
        return 0
    # コンパイルと秘密入力の読取に失敗した場合、Azureの変更はまだ始めません。
    templates = {'gateway': run_json(['az', 'bicep', 'build', '--file', str(MODULE), '--stdout']),
                 'apis': run_json(['az', 'bicep', 'build', '--file', str(api_publication.MODULE), '--stdout'])}
    values['credentialProviderSecrets'] = load_credential_provider_secrets(config)
    transport = ArmTransport(config)
    verify_foundation(config, foundation, transport)
    if active:
        values['brokerKey'] = broker_secret(config, foundation, transport)
        if not digest:
            values['imageDigest'] = build_image(config, image_state, transport, data=data, source_hash=source_hash,
                record=lambda value: protected_json(BUILD_RECORD, value), prior_receipt=read_build_receipt(BUILD_RECORD))
    result = deploy_release(config, state, values, templates, transport)
    protected_json(REPOSITORY / '.state/gateway-deployment.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, DeploymentError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, (ValueError, DeploymentError)) else 'check the selected configuration and completed Core state'
        raise SystemExit('Gateway deployment stopped: ' + detail) from None
