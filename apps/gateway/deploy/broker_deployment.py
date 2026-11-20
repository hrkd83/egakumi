"""Gateway配備で使うSQL初期化ジョブとBrokerの起動完了を確認する。"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import time

from deployment.helpers.credential_image import checked, source_bundle
from deployment.helpers.credential_pools import broker_session_settings, logical_profiles, resource_names
from deployment.helpers.paths import REPOSITORY

APP_API = "2025-01-01"
BROKER_MODULE = REPOSITORY / 'apps/gateway/deploy/broker.bicep'


def prepare_broker_only_update(config, state, transport):
    """現在の設定と実資源を照合し、Broker・SQLジョブだけの更新入力を作ります。"""
    # Gateway全体の配備と同じ基盤照合を再利用します。実行時importで循環を避けます。
    from deploy_gateway import foundation_inputs, verify_foundation
    snapshot = {}
    class ReadOnly:
        def request(self, method, path, body=None, headers=None):
            if method != 'GET' or body is not None:
                raise ValueError('Broker preparation must remain read-only')
            response = transport.request(method, path, headers=headers)
            target = path.split('?', 1)[0]
            if response.get('statusCode') == 200 and not target.endswith('/revisions'):
                found = response.get('body', {}).get('id')
                if not isinstance(found, str) or found.lower() != target.lower():
                    raise ValueError('Existing Broker foundation response differs from the requested resource')
            snapshot[path] = copy.deepcopy(response)
            return response
    reader = ReadOnly()
    foundation = foundation_inputs(config, state)
    verify_foundation(config, foundation, reader)
    names, scope = resource_names(config), group_scope(config)
    id_scope = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['id']
    runtime_id = id_scope + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/' + names['runtimeIdentity']
    runtime = checked(reader, 'GET', runtime_id + '?api-version=2023-01-31', label='Read existing Broker identity')
    if (runtime.get('id', '').lower() != runtime_id.lower() or runtime.get('name') != names['runtimeIdentity']
            or runtime.get('properties', {}).get('tenantId', '').lower() != config['azureTenantId'].lower()):
        raise ValueError('Existing Broker identity differs from the selected Azure tenant')
    runtime = {'id': runtime['id'], 'name': runtime['name'], **runtime['properties']}
    for identity in (runtime, foundation['schemaIdentity']):
        if (identity.get('tenantId', '').lower() != config['azureTenantId'].lower()
                or any(not re.fullmatch(r'[0-9a-fA-F-]{36}', str(identity.get(key, ''))) for key in ('clientId', 'principalId'))):
            raise ValueError('Existing Broker identities are incomplete')
    app_id = scope + '/providers/Microsoft.App/containerApps/' + names['application']
    app = checked(reader, 'GET', app_id + '?api-version=' + APP_API, label='Read existing Broker application')
    props = app.get('properties', {})
    containers = props.get('template', {}).get('containers', [])
    if (app.get('id', '').lower() != app_id.lower() or props.get('provisioningState') != 'Succeeded'
            or props.get('latestReadyRevisionName') != props.get('latestRevisionName')
            or len(containers) != 1
            or {key.lower() for key in app.get('identity', {}).get('userAssignedIdentities', {})} != {runtime_id.lower()}
            or props.get('managedEnvironmentId', '').lower() != (scope + '/providers/Microsoft.App/managedEnvironments/'
                + foundation['environmentName']).lower()):
        raise ValueError('The existing Broker must be ready with its verified runtime identity')
    environment = props.get('configuration', {})
    if environment.get('activeRevisionsMode') != 'Single':
        raise ValueError('Broker-only update preserves the single-revision release contract')
    expected_host = names['application'] + '.' + foundation['environmentDomain']
    if environment.get('ingress', {}).get('fqdn') != expected_host:
        raise ValueError('Broker-only update cannot change the existing application endpoint')
    env_rows = containers[0].get('env', [])
    env = {item['name']: item for item in env_rows}
    if len(env) != len(env_rows):
        raise ValueError('Existing Broker environment has duplicate settings')
    profiles = logical_profiles(config)
    login = config.get('gateway', {}).get('authentication', {}).get('credentialLogin', {})
    session = broker_session_settings(config)
    if json.loads(env.get('BROKER_PROFILES_JSON', {}).get('value', '{}')) != profiles:
        raise ValueError('Current credential tenant/client/pool profiles differ from the existing Broker')
    expected = {'AZURE_SQL_SERVER': foundation['sqlServer'], 'AZURE_SQL_DATABASE': names['database'],
        'AZURE_SUBSCRIPTION_ID': config['subscriptionId'], 'AZURE_RESOURCE_GROUP': config['resourceGroupNames']['svc'],
        'AZURE_TENANT_ID': config['azureTenantId'], 'AZURE_CLIENT_ID': runtime['clientId'],
        'APIM_NAME': foundation['apimName'], 'APIM_PRINCIPAL_ID': foundation['apimPrincipalId'],
        'BROKER_PUBLIC_ORIGIN': 'https://' + config['gateway']['publicHostName']}
    timing = {'idleTimeoutSeconds': 'BROKER_IDLE_TIMEOUT_SECONDS', 'absoluteLifetimeSeconds': 'BROKER_ABSOLUTE_LIFETIME_SECONDS',
        'rotationIntervalSeconds': 'BROKER_ROTATION_INTERVAL_SECONDS', 'rotationGraceSeconds': 'BROKER_ROTATION_GRACE_SECONDS',
        'rotationHandoffSeconds': 'BROKER_ROTATION_HANDOFF_SECONDS', 'rotationCandidateTtlSeconds': 'BROKER_ROTATION_CANDIDATE_TTL_SECONDS',
        'transactionLifetimeSeconds': 'BROKER_TRANSACTION_TTL_SECONDS', 'loginCallsPerMinute': 'BROKER_LOGIN_CALLS_PER_MINUTE'}
    expected.update({target: str(session[key]) for key, target in timing.items()})
    if any(env.get(key, {}).get('value') != value for key, value in expected.items()):
        raise ValueError('Broker-only update cannot change existing connection or session settings')
    secrets_rows = environment.get('secrets', [])
    secrets = {item['name']: item for item in secrets_rows}
    if len(secrets) != len(secrets_rows):
        raise ValueError('Existing Broker has duplicate secret references')
    vault = 'https://' + foundation['vaultName'] + '.vault.azure.net/secrets/'
    def secret_reference(name, uri):
        value = secrets.get(name, {})
        if (value.get('value') or value.get('keyVaultUrl') != uri or value.get('identity', '').lower() != runtime_id.lower()):
            raise ValueError('Broker-only update requires the existing Managed Identity Key Vault references')
    broker_uri = vault + 'core-credential-broker-key'
    if env.get('BROKER_SHARED_KEY', {}).get('secretRef') != 'broker-key':
        raise ValueError('Existing Broker shared-key reference differs from the supported contract')
    secret_reference('broker-key', broker_uri)
    oidc = {}
    for name, profile in profiles.items():
        key = profile['clientSecretEnv']
        reference = env.get(key, {}).get('secretRef')
        uri = vault + ('core-oidc-identity' if login.get('identity') else 'core-credential-' + name)
        if not isinstance(reference, str) or not reference:
            raise ValueError('Existing OIDC secret settings differ from current credential profiles')
        secret_reference(reference, uri)
        oidc[key] = {'env': key, 'uri': uri, 'name': reference}
    if (set(env) != set(expected) | {'BROKER_SHARED_KEY', 'BROKER_PROFILES_JSON'} | set(oidc)
            or set(secrets) != {'broker-key'} | {item['name'] for item in oidc.values()}):
        raise ValueError('Broker-only update cannot remove unrecognized environment or secret settings')
    database = scope + '/providers/Microsoft.Sql/servers/' + names['sqlServer'] + '/databases/' + names['database']
    db = checked(reader, 'GET', database + '?api-version=2023-08-01', label='Read existing credential database metadata')
    if db.get('id', '').lower() != database.lower() or db.get('properties', {}).get('status') != 'Online':
        raise ValueError('The existing credential database must remain online and unchanged')
    registry = checked(reader, 'GET', scope + '/providers/Microsoft.ContainerRegistry/registries/' + foundation['registryName']
        + '?api-version=2023-07-01', label='Read existing Broker registry')
    if registry.get('properties', {}).get('loginServer') != foundation['registryName'] + '.azurecr.io':
        raise ValueError('Existing Broker registry differs from the selected resource')
    old_image = containers[0].get('image', '')
    if not re.fullmatch(re.escape(registry['properties']['loginServer']) + r'/credential-broker@sha256:[a-f0-9]{64}',
                        old_image):
        raise ValueError('Existing Broker image must use the verified registry and immutable digest')
    for name in (names['schemaJob'], names['reconcileJob']):
        job = checked(reader, 'GET', scope + '/providers/Microsoft.App/jobs/' + name + '?api-version=' + APP_API,
            label='Read existing Broker job backup')
        if job.get('id', '').lower() != (scope + '/providers/Microsoft.App/jobs/' + name).lower():
            raise ValueError('Existing Broker job differs from the selected resource')
        identity = foundation['schemaIdentity'] if name == names['schemaJob'] else runtime
        if ({key.lower() for key in job.get('identity', {}).get('userAssignedIdentities', {})} != {identity['id'].lower()}
                or job.get('properties', {}).get('environmentId', '').lower()
                   != (scope + '/providers/Microsoft.App/managedEnvironments/' + foundation['environmentName']).lower()):
            raise ValueError('Existing Broker job identity or environment differs from the selected resource')
    revisions = checked(reader, 'GET', app_id + '/revisions?api-version=' + APP_API, label='Read existing Broker revision backups')
    if revisions.get('nextLink') or not isinstance(revisions.get('value'), list):
        raise ValueError('Broker revision backup inventory is incomplete')
    for revision in revisions['value']:
        rid = revision.get('id', '')
        if not rid.startswith(app_id + '/revisions/') or not re.fullmatch(r'[A-Za-z0-9-]+', rid.rsplit('/', 1)[-1]):
            raise ValueError('Existing Broker revisions have an unsupported identity')
        checked(reader, 'GET', rid + '?api-version=' + APP_API, label='Read existing Broker revision backup')
    scale = props.get('template', {}).get('scale', {})
    if any(type(scale.get(key)) is not int for key in ('minReplicas', 'maxReplicas')):
        raise ValueError('Existing Broker replica settings must be explicit')
    values = {'stem': config['resourceStem'], 'location': config['location'], 'environmentName': foundation['environmentName'],
        'registryName': foundation['registryName'], 'runtimeIdentity': runtime, 'schemaIdentity': foundation['schemaIdentity'],
        'sqlServer': foundation['sqlServer'], 'database': names['database'], 'profiles': profiles,
        'publicOrigin': expected['BROKER_PUBLIC_ORIGIN'], 'apimPrincipalId': foundation['apimPrincipalId'],
        'apimName': foundation['apimName'], 'brokerKeySecretUri': broker_uri, 'oidcSecrets': list(oidc.values()),
        'minReplicas': scale['minReplicas'], 'maxReplicas': scale['maxReplicas'], 'imageDigest': '',
        'deployApplication': False, 'schemaAction': 'observe-schema', **{key: session[key] for key in timing}}
    _, source_hash = source_bundle()
    return {'schemaVersion': 1, 'parameters': values, 'snapshot': snapshot, 'previousImage': old_image,
        'sourceSha256': source_hash, 'databaseId': database,
        'expectedCredentialInterruption': True, 'changesEntraOrPortal': False, 'changesModelCapacity': False}


def compile_broker_only_template():
    """既存のBicep CLIで単独モジュールを読み、Azureへ接続せずにコンパイルします。"""
    from prepare_registration import bicep_executable
    result = subprocess.run([bicep_executable(), 'build', str(BROKER_MODULE), '--stdout', '--no-restore'],
        capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise ValueError('Current Broker-only template compilation failed')
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        raise ValueError('Current Broker-only template compilation returned invalid JSON') from None


def _validate_broker_only_template(template):
    if not isinstance(template, dict):
        raise ValueError('Broker-only deployment requires the current compiled template')
    resources = template.get('resources', {})
    entries = list(resources.values()) if isinstance(resources, dict) else resources
    expected = {
        ('microsoft.app/jobs', "[format('caj-{0}-schema', parameters('stem'))]"): None,
        ('microsoft.app/containerapps', "[format('ca-{0}-credentials', parameters('stem'))]"):
            "[parameters('deployApplication')]",
        ('microsoft.app/jobs', "[format('caj-{0}-recon', parameters('stem'))]"):
            "[parameters('deployApplication')]",
    }
    if (not isinstance(entries, list) or len(entries) != 3
            or any(not isinstance(entry, dict) or not isinstance(entry.get('type'), str)
                   or not isinstance(entry.get('name'), str) for entry in entries)):
        raise ValueError('Broker-only deployment must contain only the existing app and two jobs')
    keys = [(entry['type'].lower(), entry['name']) for entry in entries]
    if (len(set(keys)) != 3 or any('scope' in entry or 'resources' in entry or 'copy' in entry for entry in entries)
            or any(key not in expected or entry.get('condition') != expected[key]
                   for key, entry in zip(keys, entries))):
        raise ValueError('Broker-only deployment must contain only the existing app and two jobs')
    # 名前だけでなく、Managed Identity、秘密参照、接続設定、ジョブコマンドも
    # 現在の管理資材から生成された内容に限定します。手渡しされたテンプレートを信頼しません。
    if template != compile_broker_only_template():
        raise ValueError('Broker-only deployment must use the unchanged current compiled module')


def _broker_response_for_comparison(response):
    """HTTPごとに変わる日時・相関ヘッダーを除き、実設定とETagを照合します。"""
    if not isinstance(response, dict):
        return None
    headers = response.get('headers', {})
    etag = next((value for key, value in headers.items() if key.casefold() == 'etag'), None) if isinstance(headers, dict) else None
    return {'statusCode': response.get('statusCode'), 'body': response.get('body'), 'etag': etag}


def _broker_plan_matches(current, prepared):
    if not isinstance(current, dict) or not isinstance(prepared, dict):
        return False
    if ({key: value for key, value in current.items() if key != 'snapshot'}
            != {key: value for key, value in prepared.items() if key != 'snapshot'}):
        return False
    current_snapshot, prepared_snapshot = current.get('snapshot'), prepared.get('snapshot')
    if (not isinstance(current_snapshot, dict) or not isinstance(prepared_snapshot, dict)
            or current_snapshot.keys() != prepared_snapshot.keys()):
        return False
    return all(_broker_response_for_comparison(value) == _broker_response_for_comparison(prepared_snapshot[path])
               for path, value in current_snapshot.items())


def execute_broker_only_update(config, state, plan, template, transport, image_digest, *, backup, record,
                              read_schema_observation, sleep=time.sleep, timeout=1800):
    """SQL観測→移行→対応版Brokerを配備し、APIMやCM・Portalには書き込みません。"""
    _validate_broker_only_template(template)
    if not all(callable(value) for value in (backup, record, read_schema_observation)):
        raise ValueError('Broker-only update requires protected backup, checkpoints and schema observation')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_digest):
        raise ValueError('Broker-only update requires the current immutable image digest')
    if not _broker_plan_matches(prepare_broker_only_update(config, state, transport), plan):
        raise ValueError('Existing Broker resources or current source changed after preparation')
    backup(copy.deepcopy(plan))
    values = copy.deepcopy(plan['parameters'])
    values['imageDigest'] = image_digest
    scope, names = group_scope(config), resource_names(config)
    deployment = scope + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-broker-only-update'
    deadline = time.monotonic() + timeout
    def deploy():
        path = deployment + '?api-version=2022-09-01'
        checked(transport, 'PUT', path, {'properties': {'mode': 'Incremental', 'template': template,
            'parameters': {key: {'value': value} for key, value in values.items()}}}, label='Deploy Broker-only release stage')
        while time.monotonic() < deadline:
            result = checked(transport, 'GET', path, label='Read Broker-only release stage')
            status = result.get('properties', {}).get('provisioningState')
            if status == 'Succeeded':
                return
            if status in ('Failed', 'Canceled'):
                raise ValueError('Broker-only release stage failed; inspect the protected checkpoint')
            sleep(10)
        raise ValueError('Broker-only release stage is still pending')
    def start():
        result = checked(transport, 'POST', scope + '/providers/Microsoft.App/jobs/' + names['schemaJob']
            + '/start?api-version=' + APP_API, label='Start Broker schema job')
        execution = result.get('name')
        wait_for_schema(config, execution, transport, timeout=max(1, deadline - time.monotonic()), sleep=sleep)
        return execution
    record('prepared', {'schemaMigrationStarted': False, 'applicationUpdated': False})
    deploy()
    execution = start()
    observed = read_schema_observation(config, execution)
    if (not isinstance(observed, dict) or observed.get('stage') != 'schema-snapshot'
            or type(observed.get('schemaVersion')) is not int or observed['schemaVersion'] not in (0, 1, 2, 3, 4)
            or type(observed.get('userTableCount')) is not int or observed['userTableCount'] < 0
            or type(observed.get('migrationTablePresent')) is not bool
            or (observed['schemaVersion'] == 0) != (observed['userTableCount'] == 0)
            or (observed['schemaVersion'] > 0) != observed['migrationTablePresent']
            or any(type(observed.get(key)) is not bool for key in ('transactionCallerColumn', 'sessionCallerColumn'))
            or (observed['schemaVersion'] == 4) != observed['transactionCallerColumn']
            or (observed['schemaVersion'] == 4) != observed['sessionCallerColumn']):
        raise ValueError('The successful observation job must supply verified pre-migration schema metadata')
    record('schema-before-migration', observed)
    values['schemaAction'] = 'migrate'
    deploy()
    record('schema-migration-starting', {'previousSchemaVersion': observed['schemaVersion'], 'forwardRecoveryRequired': True})
    execution = start()
    record('schema-migration-succeeded', {'execution': execution, 'forwardRecoveryRequired': True})
    values['deployApplication'] = True
    app_path = scope + '/providers/Microsoft.App/containerApps/' + names['application'] + '?api-version=' + APP_API
    current_app = transport.request('GET', app_path)
    if _broker_response_for_comparison(current_app) != _broker_response_for_comparison(plan['snapshot'].get(app_path)):
        raise ValueError('Existing Broker application changed during schema migration; application update stopped')
    deploy()
    wait_for_application(config, transport, sleep=sleep, timeout=max(1, deadline - time.monotonic()))
    deployed_app = checked(transport, 'GET', app_path, label='Verify current Broker image after readiness')
    deployed_containers = deployed_app.get('properties', {}).get('template', {}).get('containers', [])
    expected_image = values['registryName'] + '.azurecr.io/credential-broker@' + image_digest
    if len(deployed_containers) != 1 or deployed_containers[0].get('image') != expected_image:
        raise ValueError('The ready Broker application does not run the verified current image')
    record('application-ready', {'schemaVersion': 4, 'applicationUpdated': True})
    return {'status': 'ready', 'schemaVersion': 4, 'imageDigest': image_digest, 'apiPublicationPerformed': False,
        'changesEntraOrPortal': False, 'changesModelCapacity': False}


def wait_for_schema(config, execution_name, transport, *, timeout=900, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    job = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob']
    if not isinstance(execution_name, str) or not re.fullmatch(r'[A-Za-z0-9-]+', execution_name):
        raise ValueError('SQL schema execution name is invalid')
    deadline = monotonic() + timeout
    notice = monotonic() + 60
    while monotonic() < deadline:
        current = checked(transport, 'GET', job + '/executions/' + execution_name + '?api-version=' + APP_API,
                          allowed=(200, 404), label='Read SQL schema execution')
        status = current.get('properties', {}).get('status')
        if status == "Succeeded":
            return
        if status in ("Failed", "Stopped", "Degraded"):
            raise ValueError("SQL schema job failed; the application was not updated")
        if monotonic() >= notice:
            progress('認証窓口: SQL初期化の完了を待っています。', flush=True)
            notice = monotonic() + 60
        sleep(10)
    raise ValueError("SQL schema job did not complete within the wait limit")


def group_scope(config):
    """今回の配備先となるサービス用リソースグループのIDを返す。"""
    return '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['svc']


def wait_for_application(config, transport, *, sleep=time.sleep, monotonic=time.monotonic, timeout=900, progress=print):
    target = group_scope(config) + '/providers/Microsoft.App/containerApps/' + resource_names(config)['application']
    deadline = monotonic() + timeout
    notice = monotonic() + 60
    while monotonic() < deadline:
        body = checked(transport, 'GET', target + '?api-version=' + APP_API, label='Read broker readiness')
        properties = body.get('properties', {})
        revision = properties.get('latestRevisionName')
        if revision and properties.get('latestReadyRevisionName') == revision:
            ready = checked(transport, 'GET', target + '/revisions/' + revision + '?api-version=' + APP_API,
                            label='Read broker revision health').get('properties', {})
            if (ready.get('healthState') == 'Healthy' and ready.get('provisioningState') == 'Provisioned'
                    and ready.get('runningState') == 'Running'):
                return
        if properties.get('provisioningState') == 'Failed':
            raise ValueError('Credential broker application failed to provision')
        if monotonic() >= notice:
            progress('認証窓口: 新しいリビジョンの正常起動を待っています。', flush=True)
            notice = monotonic() + 60
        sleep(10)
    raise ValueError('Credential broker revision is not ready; inspect Container Apps before publishing APIs')
