"""Broker限定更新の実入力照合・SQL観測順序・失敗停止を合成ARMで確認する。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'),
               str(Path(__file__).resolve().parents[1] / 'apps/gateway/deploy')]
import broker_deployment as release
from bicep_helpers import compile_bicep
from deployment.helpers.credential_pools import logical_profiles, resource_names
from test_deploy_gateway import fixture
from test_credential_broker_initial_deployment import DIGEST

OLD_DIGEST = 'sha256:' + 'b' * 64
SCHEMA_OBSERVATION = {'stage': 'schema-snapshot', 'schemaVersion': 3,
    'userTableCount': 9, 'migrationTablePresent': True,
    'transactionCallerColumn': False, 'sessionCallerColumn': False}
TIMING = {
    'idleTimeoutSeconds': 'BROKER_IDLE_TIMEOUT_SECONDS',
    'absoluteLifetimeSeconds': 'BROKER_ABSOLUTE_LIFETIME_SECONDS',
    'rotationIntervalSeconds': 'BROKER_ROTATION_INTERVAL_SECONDS',
    'rotationGraceSeconds': 'BROKER_ROTATION_GRACE_SECONDS',
    'rotationHandoffSeconds': 'BROKER_ROTATION_HANDOFF_SECONDS',
    'rotationCandidateTtlSeconds': 'BROKER_ROTATION_CANDIDATE_TTL_SECONDS',
    'transactionLifetimeSeconds': 'BROKER_TRANSACTION_TTL_SECONDS',
    'loginCallsPerMinute': 'BROKER_LOGIN_CALLS_PER_MINUTE',
}


def response(resource_id, properties=None):
    return {'statusCode': 200, 'body': {'id': resource_id, 'name': resource_id.rsplit('/', 1)[-1],
            'etag': '"synthetic-existing"', 'properties': properties or {}},
            'headers': {'ETag': '"synthetic-existing"'}}


class BrokerTransport:
    """ARM要求とSQL段階を記録し、対象外への変更を即時拒否する。"""
    def __init__(self, config, inventory):
        self.config, self.inventory = config, inventory
        self.names, self.scope = resource_names(config), release.group_scope(config)
        self.calls, self.events = [], []
        self.parameters, self.executions = {}, {}
        self.schema_version, self.failure, self.volatile_headers = 3, None, False
        self.connection_rows = {'synthetic-key-connection': 'preserved', 'synthetic-user-session': 'preserved'}
        self.application_id = self.scope + '/providers/Microsoft.App/containerApps/' + self.names['application']
        self.schema_job = self.scope + '/providers/Microsoft.App/jobs/' + self.names['schemaJob']
        self.deployment_id = self.scope + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-broker-only-update'

    def request(self, method, path, body=None, headers=None):
        rid = path.split('?', 1)[0]
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(headers)))
        if method == 'GET':
            if rid == self.deployment_id:
                return response(rid, {'provisioningState': 'Succeeded'})
            if rid in self.executions:
                return response(rid, {'status': self.executions[rid]})
            observed = copy.deepcopy(self.inventory[rid])
            if self.volatile_headers:
                observed.setdefault('headers', {}).update({'Date': 'synthetic-date-' + str(len(self.calls)),
                    'x-ms-request-id': 'synthetic-request-' + str(len(self.calls))})
            return observed
        if method == 'PUT' and rid == self.deployment_id:
            self.parameters = {key: value['value'] for key, value in body['properties']['parameters'].items()}
            self.events.append(('deployment', self.parameters['schemaAction'], self.parameters['deployApplication']))
            if self.parameters['deployApplication']:
                if self.schema_version != 4:
                    raise AssertionError('Application update started before SQL migration')
                app = self.inventory[self.application_id]['body']['properties']
                app['latestRevisionName'] = app['latestReadyRevisionName'] = self.names['application'] + '--new'
                current_container = app['template']['containers'][0]
                current_container['image'] = current_container['image'].split('@', 1)[0] + '@' + DIGEST
                app['template']['containers'][0]['probes'] = [{'type': 'Readiness', 'httpGet': {'path': '/health/gateway-caller-v1'}}]
                new_revision = self.application_id + '/revisions/' + app['latestRevisionName']
                self.inventory[new_revision] = response(new_revision, {'healthState': 'Healthy', 'provisioningState': 'Provisioned', 'runningState': 'Running'})
            return response(rid)
        if method == 'POST' and rid == self.schema_job + '/start':
            action = self.parameters['schemaAction']
            self.events.append(('job', action))
            execution = 'synthetic-' + action + '-' + str(len(self.executions))
            failed = self.failure == action or (action == 'migrate' and self.failure == 'migrate-after-ddl')
            self.executions[self.schema_job + '/executions/' + execution] = 'Failed' if failed else 'Succeeded'
            if action == 'migrate' and not failed:
                self.schema_version = 4
                if self.failure == 'mid-migration-drift':
                    self.inventory[self.application_id]['headers']['ETag'] = '"changed-during-sql"'
                if self.failure == 'mid-migration-body-drift':
                    self.inventory[self.application_id]['body']['properties']['syntheticExternalSetting'] = True
            if action == 'migrate' and self.failure == 'migrate-after-ddl':
                self.schema_version = 4
            return {'statusCode': 200, 'body': {'name': execution}}
        raise AssertionError('Broker-only update attempted an unrelated resource mutation')


def broker_fixture():
    config, state = fixture()
    # Explicitly separate resource tenant from user sign-in tenant.
    config['entraTenantId'] = '98765432-9876-4987-8987-987654321000'
    foundation = state['outputs']['gatewayFoundation']
    names, scope = resource_names(config), release.group_scope(config)
    broker = state['outputs']['credentialBroker']
    runtime, schema = broker['runtimeIdentity'], foundation['schemaIdentity']
    profiles, login = logical_profiles(config), config['gateway']['authentication']['credentialLogin']
    registry_id = scope + '/providers/Microsoft.ContainerRegistry/registries/' + foundation['registryName']
    registry_server = foundation['registryName'] + '.azurecr.io'
    app_id = scope + '/providers/Microsoft.App/containerApps/' + names['application']
    env = {
        'AZURE_SQL_SERVER': foundation['sqlServer'], 'AZURE_SQL_DATABASE': names['database'],
        'AZURE_SUBSCRIPTION_ID': config['subscriptionId'], 'AZURE_RESOURCE_GROUP': config['resourceGroupNames']['svc'],
        'AZURE_TENANT_ID': config['azureTenantId'], 'AZURE_CLIENT_ID': runtime['clientId'],
        'APIM_NAME': foundation['apimName'], 'APIM_PRINCIPAL_ID': broker['apimPrincipalId'],
        'BROKER_PUBLIC_ORIGIN': 'https://' + config['gateway']['publicHostName'],
        'BROKER_PROFILES_JSON': json.dumps(profiles),
        **{target: str(login[key]) for key, target in TIMING.items()},
    }
    rows = [{'name': key, 'value': value} for key, value in env.items()]
    rows.append({'name': 'BROKER_SHARED_KEY', 'secretRef': 'broker-key'})
    vault = 'https://' + foundation['vaultName'] + '.vault.azure.net/secrets/'
    secrets = [{'name': 'broker-key', 'keyVaultUrl': vault + 'core-credential-broker-key', 'identity': runtime['id']}]
    for name, profile in profiles.items():
        reference = 'oidc-' + name
        rows.append({'name': profile['clientSecretEnv'], 'secretRef': reference})
        secrets.append({'name': reference, 'keyVaultUrl': vault + ('core-oidc-identity' if login.get('identity') else 'core-credential-' + name),
                        'identity': runtime['id']})
    inventory = {
        scope + '/providers/Microsoft.ApiManagement/service/' + foundation['apimName']:
            response(scope + '/providers/Microsoft.ApiManagement/service/' + foundation['apimName'], {'virtualNetworkType': 'Internal'}),
        schema['id']: response(schema['id'], {**schema, 'tenantId': config['azureTenantId']}),
        runtime['id']: response(runtime['id'], {**runtime, 'tenantId': config['azureTenantId']}),
        scope + '/providers/Microsoft.Sql/servers/sql-' + config['resourceStem']:
            response(scope + '/providers/Microsoft.Sql/servers/sql-' + config['resourceStem'],
                     {'fullyQualifiedDomainName': foundation['sqlServer'], 'publicNetworkAccess': 'Disabled'}),
        scope + '/providers/Microsoft.App/managedEnvironments/' + foundation['environmentName']:
            response(scope + '/providers/Microsoft.App/managedEnvironments/' + foundation['environmentName'],
                     {'provisioningState': 'Succeeded', 'defaultDomain': foundation['environmentDomain']}),
        app_id: response(app_id, {
            'managedEnvironmentId': scope + '/providers/Microsoft.App/managedEnvironments/' + foundation['environmentName'],
            'provisioningState': 'Succeeded', 'latestRevisionName': names['application'] + '--old',
            'latestReadyRevisionName': names['application'] + '--old',
            'configuration': {'activeRevisionsMode': 'Single', 'ingress': {'fqdn': names['application'] + '.' + foundation['environmentDomain']}, 'secrets': secrets},
            'template': {'containers': [{'name': 'credential-broker', 'image': registry_server + '/credential-broker@' + OLD_DIGEST,
                'env': rows, 'probes': [{'type': 'Readiness', 'httpGet': {'path': '/health'}}]}], 'scale': {'minReplicas': 2, 'maxReplicas': 4}},
        }),
        registry_id: response(registry_id, {'loginServer': registry_server}),
    }
    apim = scope + '/providers/Microsoft.ApiManagement/service/' + foundation['apimName']
    inventory[apim]['body']['identity'] = {'type': 'SystemAssigned', 'principalId': broker['apimPrincipalId']}
    inventory[app_id]['body']['identity'] = {'type': 'UserAssigned', 'userAssignedIdentities': {runtime['id']: {}}}
    database = scope + '/providers/Microsoft.Sql/servers/' + names['sqlServer'] + '/databases/' + names['database']
    inventory[database] = response(database, {'status': 'Online'})
    for key in ('schemaJob', 'reconcileJob'):
        rid = scope + '/providers/Microsoft.App/jobs/' + names[key]
        inventory[rid] = response(rid, {'environmentId': scope + '/providers/Microsoft.App/managedEnvironments/' + foundation['environmentName'],
            'template': {'containers': [{'image': registry_server + '/credential-broker@' + OLD_DIGEST}]}})
        inventory[rid]['body']['identity'] = {'type': 'UserAssigned', 'userAssignedIdentities': {(schema if key == 'schemaJob' else runtime)['id']: {}}}
    old_revision = app_id + '/revisions/' + names['application'] + '--old'
    inventory[app_id + '/revisions'] = {'statusCode': 200, 'body': {'value': [{'id': old_revision}]}}
    inventory[old_revision] = response(old_revision, {'healthState': 'Healthy', 'template': copy.deepcopy(inventory[app_id]['body']['properties']['template'])})
    return config, state, BrokerTransport(config, inventory)


class BrokerOnlyUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/deploy/broker.bicep')

    def setUp(self):
        # Use the real compiled source; do not run another compiler per fault.
        patcher = mock.patch.object(release, 'compile_broker_only_template', return_value=copy.deepcopy(self.template))
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, *, volatile_headers=False):
        config, state, transport = broker_fixture()
        transport.volatile_headers = volatile_headers
        plan = release.prepare_broker_only_update(config, state, transport)
        return config, state, transport, plan

    def run_release(self, *, observation=None, failure=None, template=None, mutate=None, volatile_headers=False):
        config, state, transport, plan = self.prepare(volatile_headers=volatile_headers)
        self.last_transport = transport
        before = copy.deepcopy(transport.connection_rows)
        transport.failure = failure
        if mutate:
            mutate(transport, plan)
        backups, records = [], []
        def backup(value):
            self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))
            backups.append(value)
            transport.events.append(('backup',))
        def record(stage, value):
            records.append((stage, copy.deepcopy(value)))
        def observe(_config, execution):
            transport.events.append(('observation-read',))
            if failure == 'observation-read':
                raise ValueError('Synthetic observation unavailable')
            return observation if observation is not None else copy.deepcopy(SCHEMA_OBSERVATION)
        result = release.execute_broker_only_update(config, state, plan,
            template if template is not None else compile_bicep('apps/gateway/deploy/broker.bicep'),
            transport, DIGEST, backup=backup, record=record, read_schema_observation=observe, sleep=lambda _: None)
        self.assertEqual(transport.connection_rows, before)
        return result, transport, plan, backups, records

    def test_preparation_is_read_only_and_preserves_old_image_revisions_and_separate_tenants(self):
        config, _, transport, plan = self.prepare()
        self.assertNotEqual(config['entraTenantId'], config['azureTenantId'])
        self.assertEqual(plan['parameters']['runtimeIdentity']['tenantId'], config['azureTenantId'])
        self.assertEqual(plan['parameters']['profiles'], logical_profiles(config))
        self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))
        self.assertTrue(plan['previousImage'].endswith(OLD_DIGEST))
        self.assertTrue(plan['expectedCredentialInterruption'])
        self.assertFalse(plan['changesEntraOrPortal'])
        self.assertFalse(plan['changesModelCapacity'])
        self.assertTrue(any('/revisions/' in path for path in plan['snapshot']))
        backup_app = next(value['body'] for path, value in plan['snapshot'].items() if path.split('?')[0] == transport.application_id)
        self.assertEqual(backup_app['properties']['template']['containers'][0]['probes'][0]['httpGet']['path'], '/health')
        self.assertNotIn('brokerKey', plan['parameters'])

    def test_backup_and_successful_actual_observation_precede_migration_and_new_application(self):
        result, transport, plan, backups, records = self.run_release()
        self.assertEqual(transport.events, [('backup',), ('deployment', 'observe-schema', False),
            ('job', 'observe-schema'), ('observation-read',), ('deployment', 'migrate', False),
            ('job', 'migrate'), ('deployment', 'migrate', True)])
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0], plan)
        self.assertEqual(dict(records)['schema-before-migration']['schemaVersion'], 3)
        self.assertTrue(dict(records)['schema-migration-starting']['forwardRecoveryRequired'])
        self.assertEqual(dict(records)['application-ready']['schemaVersion'], 4)
        self.assertEqual(transport.schema_version, 4)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['apiPublicationPerformed'])
        self.assertFalse(result['changesEntraOrPortal'])
        self.assertFalse(result['changesModelCapacity'])
        writes = [(method, path.split('?')[0]) for method, path, *_ in transport.calls if method != 'GET']
        self.assertTrue(all((method == 'PUT' and path == transport.deployment_id)
                           or (method == 'POST' and path == transport.schema_job + '/start') for method, path in writes))
        self.assertFalse(any(segment in path for method, path in writes
                             for segment in ('/authorizationProviders/', '/vaults/', '/Microsoft.Network/', '/Microsoft.CognitiveServices/')))

    def test_changing_http_date_and_request_id_headers_do_not_change_resource_snapshot(self):
        result, transport, plan, backups, _ = self.run_release(volatile_headers=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(transport.schema_version, 4)
        self.assertTrue(any('Date' in value['headers'] for value in plan['snapshot'].values()))
        self.assertEqual(backups[0]['snapshot'], plan['snapshot'])

    def test_observation_job_failure_and_missing_observation_stop_before_ddl_or_application(self):
        for failure in ('observe-schema', 'observation-read'):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.run_release(failure=failure)
            transport = self.last_transport
            self.assertEqual(transport.schema_version, 3)
            self.assertFalse(any(event[0] == 'job' and event[1] == 'migrate' for event in transport.events))
            self.assertFalse(any(event[0] == 'deployment' and event[2] for event in transport.events))

    def test_sql_migration_failure_stops_before_application_update(self):
        for failure, schema_version in (('migrate', 3), ('migrate-after-ddl', 4)):
            with self.subTest(failure=failure):
                config, state, transport, plan = self.prepare()
                transport.failure = failure
                records = []
                with self.assertRaisesRegex(ValueError, 'SQL schema job failed'):
                    release.execute_broker_only_update(config, state, plan, compile_bicep('apps/gateway/deploy/broker.bicep'),
                        transport, DIGEST, backup=lambda _: None, record=lambda stage, value: records.append((stage, value)),
                        read_schema_observation=lambda *_: copy.deepcopy(SCHEMA_OBSERVATION), sleep=lambda _: None)
                self.assertEqual(transport.schema_version, schema_version)
                self.assertTrue(dict(records)['schema-migration-starting']['forwardRecoveryRequired'])
                self.assertFalse(any(event == ('deployment', 'migrate', True) for event in transport.events))
                self.assertTrue(transport.inventory[transport.application_id]['body']['properties']['template']['containers'][0]['image'].endswith(OLD_DIGEST))

    def test_invalid_schema_observation_is_never_treated_as_approval_for_migration(self):
        for value in ({}, {**SCHEMA_OBSERVATION, 'schemaVersion': 4},
                      {**SCHEMA_OBSERVATION, 'schemaVersion': '3'},
                      {**SCHEMA_OBSERVATION, 'transactionCallerColumn': 0},
                      {**SCHEMA_OBSERVATION, 'migrationTablePresent': False},
                      {**SCHEMA_OBSERVATION, 'userTableCount': -1}):
            with self.subTest(observation=value), self.assertRaisesRegex(ValueError, 'verified pre-migration'):
                self.run_release(observation=value)
            self.assertEqual(self.last_transport.schema_version, 3)
            self.assertFalse(any(event[0] == 'job' and event[1] == 'migrate' for event in self.last_transport.events))

    def test_mid_migration_application_drift_stops_update_without_rolling_back_sql_four(self):
        for failure in ('mid-migration-drift', 'mid-migration-body-drift'):
            with self.subTest(failure=failure):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.run_release(failure=failure)
                transport = self.last_transport
                self.assertEqual(transport.schema_version, 4)
                self.assertFalse(any(event[0] == 'deployment' and event[2] for event in transport.events))
                self.assertTrue(transport.inventory[transport.application_id]['body']['properties']['template']['containers'][0]['image'].endswith(OLD_DIGEST))

    def test_stale_etag_prevents_backup_and_all_writes(self):
        for kind in ('etag', 'body'):
            with self.subTest(kind=kind):
                config, state, transport, plan = self.prepare()
                if kind == 'etag': transport.inventory[transport.application_id]['headers']['ETag'] = '"changed-after-preparation"'
                else: transport.inventory[transport.application_id]['body']['properties']['syntheticExternalSetting'] = True
                backups = []
                with self.assertRaisesRegex(ValueError, 'changed after preparation'):
                    release.execute_broker_only_update(config, state, plan, compile_bicep('apps/gateway/deploy/broker.bicep'),
                        transport, DIGEST, backup=backups.append, record=lambda *_: None, read_schema_observation=lambda *_: {})
                self.assertEqual(backups, [])
                self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))

    def test_preparation_rejects_changed_profile_tenant_runtime_tenant_or_secret_reference(self):
        for kind in ('profile', 'runtime-tenant', 'plain-secret'):
            config, state, transport = broker_fixture()
            if kind == 'runtime-tenant':
                rid = state['outputs']['credentialBroker']['runtimeIdentity']['id']
                transport.inventory[rid]['body']['properties']['tenantId'] = config['entraTenantId']
            elif kind == 'profile':
                env = transport.inventory[transport.application_id]['body']['properties']['template']['containers'][0]['env']
                next(item for item in env if item['name'] == 'BROKER_PROFILES_JSON')['value'] = '{}'
            else:
                transport.inventory[transport.application_id]['body']['properties']['configuration']['secrets'][0]['value'] = 'synthetic-plaintext-must-not-be-accepted'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                release.prepare_broker_only_update(config, state, transport)
            self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))

    def test_template_rejects_extra_resources_and_foreign_targets_before_any_writes(self):
        original = compile_bicep('apps/gateway/deploy/broker.bicep')
        for kind in ('extra-type', 'foreign-app', 'nested', 'scope', 'unconditional-app'):
            template = copy.deepcopy(original)
            resources = template['resources']
            entries = list(resources.values()) if isinstance(resources, dict) else resources
            app = next(entry for entry in entries if entry['type'] == 'Microsoft.App/containerApps')
            if kind == 'extra-type':
                extra = {'type': 'Microsoft.KeyVault/vaults', 'name': 'unrelated', 'apiVersion': '2023-07-01'}
                if isinstance(resources, dict): resources['unrelated'] = extra
                else: resources.append(extra)
            elif kind == 'foreign-app': app['name'] = 'ca-unrelated-portal'
            elif kind == 'nested': app['resources'] = [{'type': 'Microsoft.KeyVault/vaults', 'name': 'unrelated'}]
            elif kind == 'scope': app['scope'] = '/subscriptions/synthetic-foreign'
            else: app.pop('condition')
            config, state, transport, plan = self.prepare()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                release.execute_broker_only_update(config, state, plan, template, transport, DIGEST,
                    backup=lambda _: None, record=lambda *_: None, read_schema_observation=lambda *_: {})
            self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))


if __name__ == '__main__':
    unittest.main()
