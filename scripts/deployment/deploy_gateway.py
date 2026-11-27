#!/usr/bin/env python3
"""GatewayのDB・CM・Broker・APIを順に配備する。既定は計画のみ、--executeで実行。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deployment.helpers.configuration import (
    build_bicep_configuration, load_configuration_source, load_credential_provider_secrets,
    load_credential_identity_secret,
)
from deployment.helpers.paths import REPOSITORY, local_paths
from deployment.helpers.private_permissions import check_private_permissions
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.credential_image import (
    build_image, checked, read_build_receipt, reusable_image, source_bundle,
)
from deployment.helpers.credential_broker_lifecycle import broker_environment_id, prepare_secret, uses_broker_environment
from deployment.helpers.credential_pools import credential_login_enabled, resource_names, verify_existing_provider_registrations
from deployment.helpers.management_client_assets import management_client_inventory, update_management_client_assets
from deployment.helpers.ingress import configure_named_values, require_shared_ingress
from deployment.lifecycle import ArmTransport, DeploymentError, protected_json, state_from_outputs
from catalog.standard_apis import resolve_publication_configuration
from operations.common import OperationError, lock_environment
from operations.waf_policy import reconcile as configure_data_waf, selected_profiles as data_waf_profiles

# 統合配備はAPIMとCredential Brokerの内部実装を読み込みます。
sys.path[:0] = [str(REPOSITORY / 'apps/api-gateway/deploy'), str(REPOSITORY / 'apps/credential-broker/deploy')]
import api_publication
from broker_deployment import wait_for_application, wait_for_schema
from gateway_ingress import (
    configure_gateway_identity_callback_waf, configure_gateway_speech_waf, speech_content_type_paths,
    configure_gateway_document_waf, document_upload_limit,
)

APP_API = '2025-01-01'
ARM_API = '2022-09-01'
MODULE = REPOSITORY / 'apps/api-gateway/deploy/main.bicep'
AUDIT_MODULE = REPOSITORY / 'apps/audit-relay/deploy/application.bicep'


def group_scope(config):
    """今回選んだ環境のサービス用RGを返す。"""
    return f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"


def foundation_inputs(config, state):
    """現在のinfraが保存した共通基盤出力だけを参照する。"""
    if (any(state.get(key) != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem'))
            or state.get('apimInstanceId') != apim_instance_id(config)):
        raise ValueError('Core deployment state differs from the selected configuration')
    output, stem = state['outputs'], config['resourceStem']
    foundation = output.get('gatewayFoundation')
    if not isinstance(foundation, dict) or not foundation:
        raise ValueError('Current infrastructure gatewayFoundation output is required; use the current Core deployment state')
    require_shared_ingress(config, output)
    foundation = copy.deepcopy(foundation)
    expected = {
        'resourceGroup': config['resourceGroupNames']['svc'],
        'identityResourceGroup': config['resourceGroupNames']['id'],
        'apimName': apim_name(config), 'vaultName': f'kv-{stem}',
        'brokerEnvironmentName': f'cae-{stem}-broker', 'registryName': 'acr' + stem.replace('-', ''),
        'sqlServer': f'sql-{stem}.database.windows.net',
    }
    for name, value in expected.items():
        if foundation.get(name) != value:
            raise ValueError('Core foundation differs from this environment: ' + name)
    subnet_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['nw']}"
                 f"/providers/Microsoft.Network/virtualNetworks/vnet-{stem}/subnets/snet-{stem}-broker")
    for name, value in (('brokerEnvironmentId', broker_environment_id(config)), ('brokerSubnetId', subnet_id)):
        if str(foundation.get(name, '')).casefold() != value.casefold():
            raise ValueError('Core foundation differs from this environment: ' + name)
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+\.azurecontainerapps\.io', str(foundation.get('brokerEnvironmentDomain', ''))):
        raise ValueError('Core foundation must record the private Broker environment domain')
    identity = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['id']}"
                f'/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-{stem}-credential-schema')
    schema = foundation.get('schemaIdentity')
    if not isinstance(schema, dict) or str(schema.get('id', '')).lower() != identity.lower():
        raise ValueError('Core schema identity differs from this environment')
    management_client_inventory(config, state)
    vault = 'https://' + expected['vaultName'] + '.vault.azure.net/secrets/'
    features = output.get('ingressSecrets', {})
    foundation['l7IngressSecretIdentifier'] = vault + 'core-l7-ingress-secret' if features.get('l7IngressEnabled') is True else ''
    foundation['realtimeTicketSecretIdentifier'] = vault + 'core-realtime-ticket-key' if features.get('ownerTicketEnabled', features.get('realtimeEnabled')) else ''
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
    environment = checked(transport, 'GET', foundation['brokerEnvironmentId'] + '?api-version=' + APP_API,
                          label='Read Broker Container Apps environment')
    properties = environment.get('properties', {})
    vnet = properties.get('vnetConfiguration') or {}
    if (str(environment.get('id', '')).casefold() != foundation['brokerEnvironmentId'].casefold()
            or properties.get('provisioningState') != 'Succeeded'
            or properties.get('defaultDomain') != foundation['brokerEnvironmentDomain']
            or properties.get('publicNetworkAccess') not in (None, 'Disabled')
            or vnet.get('internal') is not True
            or str(vnet.get('infrastructureSubnetId', '')).casefold() != foundation['brokerSubnetId'].casefold()):
        raise ValueError('The dedicated private Broker environment or its subnet differs from Core state')
    verify_broker_workload_bindings(config, foundation['brokerEnvironmentId'], transport)


def verify_broker_workload_bindings(config, environment_id, transport):
    """同名の既存アプリとジョブの所属環境を、配備による変更の前に確認します。"""
    names = resource_names(config)
    scope = group_scope(config) + '/providers/Microsoft.App/'
    for kind, name in (('containerApps', names['application']), ('jobs', names['schemaJob']), ('jobs', names['reconcileJob'])):
        target = scope + kind + '/' + name
        response = transport.request('GET', target + '?api-version=' + APP_API)
        if response.get('statusCode') == 404:
            continue
        body = response.get('body') or {}
        if response.get('statusCode') != 200 or str(body.get('id', '')).casefold() != target.casefold():
            raise ValueError('Existing Credential Broker workload could not be verified')
        if not uses_broker_environment(body.get('properties') or {}, environment_id):
            raise ValueError('An existing Credential Broker workload belongs to a different environment; plan its migration before deployment')


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


def deploy_resources(config, values, template, transport, *, component='gateway', timeout=1800, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """差分をIncrementalで反映し、成功した配備の公開出力を返す。"""
    target = group_scope(config) + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-' + component
    path = target + '?api-version=' + ARM_API
    checked(transport, 'PUT', path, {'properties': {'mode': 'Incremental', 'template': template,
        'parameters': {key: {'value': value} for key, value in values.items()}}}, label='Deploy application resources')
    deadline, notice = monotonic() + timeout, monotonic() + 45
    while monotonic() < deadline:
        body = checked(transport, 'GET', path, label='Read application deployment')
        properties = body.get('properties', {})
        if properties.get('provisioningState') == 'Succeeded':
            return {key: value['value'] for key, value in properties.get('outputs', {}).items()}
        if properties.get('provisioningState') in ('Failed', 'Canceled'):
            raise ValueError(f'{component} resource deployment failed; inspect the selected deployment.')
        if monotonic() >= notice:
            progress(f'{component}: Azureリソースへの反映を待っています。', flush=True)
            notice = monotonic() + 45
        sleep(10)
    raise ValueError(f'{component} deployment is still pending; inspect the selected deployment.')


def audit_foundation(config, state):
    """選択環境の監査基盤と既存のv2 Gateway audienceを照合します。"""
    if config.get('auditEvidence', {}).get('enabled') is not True:
        return None
    foundation = state['outputs'].get('auditEvidence')
    stem = config['resourceStem']
    expected_environment = group_scope(config) + '/providers/Microsoft.App/managedEnvironments/cae-' + stem + '-audit'
    if (not isinstance(foundation, dict)
            or str(foundation.get('environmentId', '')).casefold() != expected_environment.casefold()
            or foundation.get('registryName') != 'acr' + stem.replace('-', '')):
        raise ValueError('Provision Core audit evidence infrastructure before deploying Gateway')
    audience = config.get('gateway', {}).get('authentication', {}).get('entra', {}).get('audience')
    azure_tenant = config['azureTenantId']
    if (not re.fullmatch(r'[0-9a-fA-F-]{36}', str(audience or ''))
            or (config.get('entraTenantId') or azure_tenant).casefold() != azure_tenant.casefold()):
        raise ValueError('Audit Relay requires the existing v2 Gateway API registration in the deployment tenant')
    return foundation


def deploy_audit_pipeline(config, state, foundation, apim_principal, template, transport, workspace):
    """共有イメージを非公開ACRで解決し、本文の経路を開く前に両アプリを配備します。"""
    api_publication.verify_audit_payload_retention(config, state, transport)
    receipt = workspace.checked(workspace.state / 'audit-relay-build.json')
    data, source_hash = source_bundle(audit=True)
    digest = reusable_image(config, state, source_hash, receipt, transport=transport, audit=True)
    if digest is None:
        digest = build_image(config, state, transport, data=data, source_hash=source_hash,
                             record=lambda value: protected_json(receipt, value),
                             prior_receipt=read_build_receipt(receipt), audit=True)
    registry = foundation['registryName'] + '.azurecr.io'
    relay_environment = {
        'AUDIT_TENANT_ID': config['azureTenantId'],
        'AUDIT_APIM_PRINCIPAL_ID': apim_principal,
        'AUDIT_TOKEN_AUDIENCE': config['gateway']['authentication']['entra']['audience'],
    }
    values = {'foundation': foundation, 'registryServer': registry,
              'image': registry + '/audit-relay@' + digest,
              'relayEnvironment': [{'name': name, 'value': value} for name, value in relay_environment.items()]}
    print('本文監査: 中継と保存ワーカーを配備します。', flush=True)
    deploy_resources(config, values, template, transport, component='audit-evidence')
    for attempt in range(24):
        try:
            api_publication.verify_audit_relay(config, state, transport)
            break
        except api_publication.AuditRelayNotReady:
            if attempt == 23:
                raise
            time.sleep(5)


def deploy_release(config, state, values, templates, transport, *, deploy=deploy_resources,
                   schema_wait=wait_for_schema, readiness_wait=wait_for_application,
                   refresh=configure_named_values, publish=api_publication.execute_plan,
                   callback_waf=configure_gateway_identity_callback_waf,
                   speech_waf=configure_gateway_speech_waf,
                   document_waf=configure_gateway_document_waf, data_waf=configure_data_waf, progress=print,
                   publication_checkpoint=None, record_stage=None, client_prepare=update_management_client_assets):
    """SQL成功後だけアプリとAPIを更新する。共有鍵・CM接続・既存SQLデータは維持する。"""
    progress('Gateway: 資格情報DB・CM・SQL初期化ジョブを配備します。', flush=True)
    initialized = deploy(config, values, templates['gateway'], transport)
    client_prepare(config, state, initialized, transport, component='gateway')
    values['brokerKey'] = ''  # 2段階目に同じ鍵の新バージョンを作らない。
    job = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob']
    execution = checked(transport, 'POST', job + '/start?api-version=' + APP_API,
                        label='Start Gateway SQL initialization')
    schema_wait(config, execution.get('name'), transport)
    progress('Gateway: ブローカーとAPIM接続設定を反映します。', flush=True)
    values['deployApplication'] = True
    deployed = deploy(config, values, templates['gateway'], transport)
    client_prepare(config, state, deployed, transport, component='gateway')
    readiness_wait(config, transport)
    current = copy.deepcopy(state)
    current['outputs']['credentialBroker'] = deployed.get('credentialBroker', {})
    refresh(config, current['outputs'], transport)
    progress('Gateway: API・製品・購読・ポリシーを公開します。', flush=True)
    plan = api_publication.prepare_plan(config, current)
    deployment = {'azdUpExecuted': False, 'sqlInitialized': True, 'applicationReady': True,
                  'imageDigest': values['imageDigest'] or None, 'credentialBroker': deployed.get('credentialBroker', {})}
    options = {}
    if publication_checkpoint is not None:
        options['checkpoint'] = publication_checkpoint
    if record_stage is not None:
        def save_stage(publication):
            record_stage({'scope': publication_scope(config, plan), 'gatewayDeployment': deployment,
                          'publication': publication})

        options['record_stage'] = save_stage
    publication = publish(config, current, plan, templates['apis'], transport, **options)
    waf = configure_gateway_waf(config, transport, callback_waf=callback_waf, speech_waf=speech_waf,
                                document_waf=document_waf, data_waf=data_waf, progress=progress)
    return {**deployment, 'status': 'deployed', 'apiPublication': publication, **waf, 'runtimeVerified': False}


def publication_scope(config, plan):
    return {**{key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')},
            'apimId': plan['apimId'], 'deploymentId': plan['deploymentId']}


def configure_gateway_waf(config, transport, *, callback_waf=configure_gateway_identity_callback_waf,
                          speech_waf=configure_gateway_speech_waf, document_waf=configure_gateway_document_waf,
                          data_waf=configure_data_waf, progress=print):
    callback = None
    if credential_login_enabled(config):
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
    return {'callbackWaf': callback, 'speechWaf': speech, 'documentWaf': document, 'dataWaf': data}


def main(argv=None, *, _locked_workspace=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--deployment-name', help='Completed Core subscription deployment for CI or a missing local state')
    parser.add_argument('--image-digest', help='Use an existing credential-broker image by immutable sha256 digest')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume-closed-stage', action='store_true',
                        help='Resume the recorded same-scope Gateway API stage after a terminal ARM failure')
    args = parser.parse_args(argv)
    if args.resume_closed_stage and (not args.execute or args.image_digest is not None):
        parser.error('--resume-closed-stage requires --execute and does not rebuild the Broker image')
    source = load_configuration_source(args.config)
    workspace = local_paths(source, REPOSITORY)
    workspace.checked(args.config)
    workspace.check_selection(source)
    if _locked_workspace is not None:
        if not args.execute or _locked_workspace != workspace:
            raise ValueError('The held release lock does not match the selected Gateway environment')
        return run_gateway(args, source, workspace)
    if args.execute:
        with lock_environment(workspace):
            source = load_configuration_source(args.config)
            workspace.check_selection(source)
            if local_paths(source, REPOSITORY).root != workspace.root:
                raise ValueError('Gateway configuration changed its selected environment before the operation lock')
            return run_gateway(args, source, workspace)
    return run_gateway(args, source, workspace)


def run_gateway(args, source, workspace):
    args.state_file = workspace.checked(args.state_file or workspace.state / 'latest.json')
    args.output = workspace.checked(args.output or workspace.state / 'gateway-plan.json')
    build_record = workspace.state / 'credential-broker-build.json'
    publication_record = workspace.checked(workspace.state / 'gateway-api-source.json')
    stage_path = workspace.checked(workspace.state / 'gateway-api-closed-stage.json')
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
    if args.execute and publication_record.is_file():
        # A checkpoint supplies expected source only. Fresh raw policy/ETag/
        # membership readback is still mandatory and detects partial drift.
        check_private_permissions(publication_record)
        state['apiPublicationSourceCheckpoint'] = json.loads(publication_record.read_text(encoding='utf-8'))
    if args.execute and config.get('publishApis') is not True:
        raise ValueError('Set publishApis=true to deploy the Gateway APIs')
    foundation = foundation_inputs(config, state)
    evidence = audit_foundation(config, state)
    if args.image_digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image_digest):
        raise ValueError('Use an immutable sha256 image digest')
    publication_plan = api_publication.prepare_plan(config, state)
    if args.resume_closed_stage:
        check_private_permissions(stage_path)
        stage = json.loads(stage_path.read_text(encoding='utf-8'))
        if (not isinstance(stage, dict) or set(stage) != {'scope', 'gatewayDeployment', 'publication'}
                or stage['scope'] != publication_scope(config, publication_plan)
                or not isinstance(stage['publication'], dict)):
            raise ValueError('The recorded Gateway API stage does not match the selected publication')
        deployment = stage['gatewayDeployment']
        if (not isinstance(deployment, dict) or set(deployment) != {
                'azdUpExecuted', 'sqlInitialized', 'applicationReady', 'imageDigest', 'credentialBroker'}
                or deployment['azdUpExecuted'] is not False or deployment['sqlInitialized'] is not True
                or deployment['applicationReady'] is not True or not isinstance(deployment['credentialBroker'], dict)):
            raise ValueError('The recorded Gateway workload deployment is incomplete')
        transport = ArmTransport(config)
        template = run_json(['az', 'bicep', 'build', '--file', str(api_publication.MODULE), '--stdout'])
        publication = api_publication.execute_plan(config, state, publication_plan, template, transport,
            stage_record=stage['publication'], checkpoint=lambda value: protected_json(publication_record, value))
        waf = configure_gateway_waf(config, transport)
        result = {**deployment, **waf, 'status': 'deployed', 'publicationResumed': True,
                  'apiPublication': publication, 'auditEvidenceEnabled': evidence is not None, 'runtimeVerified': False}
        protected_json(workspace.state / 'gateway-deployment.json', result)
        return report_deployment(result)
    login_enabled = credential_login_enabled(config)
    data, source_hash = source_bundle() if not args.image_digest else (None, None)
    image_state = build_state(state, foundation)
    transport = ArmTransport(config) if args.execute else None
    digest = args.image_digest or reusable_image(config, image_state, source_hash, build_record, transport=transport)
    values = {'configuration': build_bicep_configuration(config), 'foundation': foundation,
              'imageDigest': digest or '', 'deployApplication': False}
    from deployment.helpers.gateway_obo_certificate import deployment_settings as gateway_obo_settings
    graph_obo = gateway_obo_settings(config, workspace)
    if graph_obo:
        values['graphObo'] = graph_obo
    summary = {'status': 'planned', 'azureWrites': 0, 'azdUpRequired': False,
        'apiSelection': 'specified' if source.get('apiDefinitions') else 'standard',
        'apiCount': len(config['apiDefinitions']), 'credentialBrokerEnabled': True,
        'auditEvidenceEnabled': evidence is not None,
        'application': resource_names(config)['application'],
        'database': resource_names(config)['database'],
        'imageBuild': not digest,
        'imageReuseVerificationPending': not args.execute and not args.image_digest,
        'steps': (['build-and-deploy-audit-workloads'] if evidence is not None else [])
                 + ['build-or-reuse-image', 'prepare-database-and-credential-manager', 'initialize-sql',
                  'update-broker-and-apim-settings', 'publish-apis-products', 'reconcile-released-api-membership', 'remove-default-apim-samples']
                 + (['configure-gateway-identity-callback-waf'] if login_enabled else [])
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
    if evidence is not None:
        templates['audit'] = run_json(['az', 'bicep', 'build', '--file', str(AUDIT_MODULE), '--stdout'])
    values['credentialProviderSecrets'] = load_credential_provider_secrets(config)
    if config.get('gateway', {}).get('authentication', {}).get('credentialLogin', {}).get('identity'):
        values['credentialIdentitySecret'] = load_credential_identity_secret(config)
    verify_foundation(config, foundation, transport)
    if graph_obo:
        verify_existing_provider_registrations(config, publication_plan['apimId'], transport)
    values['brokerKey'] = broker_secret(config, foundation, transport)
    if not digest:
        values['imageDigest'] = build_image(config, image_state, transport, data=data, source_hash=source_hash,
            record=lambda value: protected_json(build_record, value), prior_receipt=read_build_receipt(build_record))
    if evidence is not None:
        deploy_audit_pipeline(config, state, evidence, foundation['apimPrincipalId'], templates['audit'], transport, workspace)
    result = deploy_release(config, state, values, templates, transport,
                            publication_checkpoint=lambda value: protected_json(publication_record, value),
                            record_stage=lambda value: protected_json(stage_path, value))
    result['auditEvidenceEnabled'] = evidence is not None
    protected_json(workspace.state / 'gateway-deployment.json', result)
    return report_deployment(result)


def report_deployment(result):
    summary = copy.deepcopy(result)
    # Actual source XML is protected output, never CLI stdout or public evidence.
    summary.get('apiPublication', {}).pop('entraPublicationSource', None)
    summary.get('apiPublication', {}).pop('credentialBrowserPublicationSource', None)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, DeploymentError, OperationError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, (ValueError, DeploymentError, OperationError)) else 'check the selected configuration and completed Core state'
        raise SystemExit('Gateway deployment stopped: ' + detail) from None
