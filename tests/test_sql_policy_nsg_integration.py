"""Use the real SQL/NSG APIs and operation journal with independent synthetic ARM."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_fixed_nsg_migration import MemoryTransport, fixture_config
from deployment.helpers import fixed_nsg as nsg
from deployment.helpers import sql_connection_policy as policy
from deployment.helpers.paths import local_paths
from operations.common import OperationContext
from operations import sql_connection_policy as operation


class CombinedArm(MemoryTransport):
    """NSG reads use the existing independent ARM store; SQL has no ETag contract."""
    def __init__(self, mode='Proxy'):
        config = fixture_config()
        config['azureTenantId'] = '22222222-2222-4222-8222-222222222222'
        super().__init__(config)
        self.server = self.groups['svc'] + '/providers/Microsoft.Sql/servers/sql-' + self.stem
        self.policy = self.server + '/connectionPolicies/default'
        self.resources[self.server]['properties'].update(
            publicNetworkAccess='Disabled', state='Ready', minimalTlsVersion='1.2',
            administrators={'azureADOnlyAuthentication': True})
        self.resources[self.policy] = {
            'id': self.policy, 'name': 'default', 'type': 'Microsoft.Sql/servers/connectionPolicies',
            'properties': {'connectionType': mode}}
        base = self.groups['svc'] + '/providers/Microsoft.App/'
        self.app = base + 'containerApps/ca-' + self.stem + '-portal'
        self.schema = base + 'jobs/caj-' + self.stem + '-psql'
        self.reconcile = base + 'jobs/caj-' + self.stem + '-psync'
        self.template = {'containers': [{'name': 'portal',
            'image': 'synthetic.invalid/portal@sha256:' + 'a' * 64,
            'env': [{'name': 'PORTAL_SQL_AUTH', 'value': 'managed-identity'}]}]}
        identity = {'type': 'UserAssigned', 'userAssignedIdentities': {
            self.groups['svc'] + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-fixture': {
                'principalId': '33333333-3333-4333-8333-333333333333'}}}
        for target in (self.app, self.schema, self.reconcile):
            configuration = ({'activeRevisionsMode': 'Single', 'ingress': {'external': False, 'targetPort': 3000},
                              'secrets': [], 'registries': []} if target == self.app else
                             {'triggerType': 'Manual', 'replicaTimeout': 300, 'replicaRetryLimit': 0,
                              'secrets': [], 'registries': []})
            self.add(target, 'Microsoft.App/containerApps' if target == self.app else 'Microsoft.App/jobs',
                     {'template': self.template, 'configuration': configuration})
            self.resources[target]['identity'] = copy.deepcopy(identity)
            self.resources[target]['systemData'] = {'createdAt': '2026-01-01T00:00:00Z'}
        self.revision = {'id': self.app + '/revisions/ca-' + self.stem + '-portal--r1',
            'name': 'ca-' + self.stem + '-portal--r1', 'type': 'Microsoft.App/containerApps/revisions',
            'properties': {'active': True, 'createdTime': '2026-01-01T00:00:00Z',
                'provisioningState': 'Succeeded', 'healthState': 'Healthy', 'runningState': 'Running',
                'template': copy.deepcopy(self.template)}}
        self.resources[self.app + '/revisions'] = {'value': [self.revision]}
        self.state = {key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        self.state['outputs'] = {'gatewayFoundation': {'sqlServerId': self.server},
                                'portal': {'sharedSqlServerId': self.server}}
        self.now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.drop_response = False

    def request(self, method, path, body=None, headers=None):
        if method == 'PUT' and path.split('?', 1)[0] == self.policy:
            if headers is not None or body not in (
                    {'properties': {'connectionType': 'Proxy'}},
                    {'properties': {'connectionType': 'Redirect'}}):
                raise AssertionError('Only an unconditional minimal SQL policy child PUT is permitted')
            self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': None})
            self.resources[self.policy]['properties']['connectionType'] = body['properties']['connectionType']
            if self.drop_response:
                self.drop_response = False
                raise ConnectionError('Synthetic lost SQL policy response')
            return {'statusCode': 200, 'body': None}
        return super().request(method, path, body=body, headers=headers)

    def proof(self):
        """Fixture evidence is independent of the production generation-reader API."""
        resources = {}
        for key, target in (('app', self.app), ('schemaJob', self.schema), ('reconcileJob', self.reconcile)):
            item = self.resources[target]
            resources[key] = {'id': target, 'createdAt': item['systemData']['createdAt'],
                              'lastModifiedAt': item['systemData'].get('lastModifiedAt'),
                              'identity': copy.deepcopy(item['identity']),
                              'template': copy.deepcopy(item['properties']['template']),
                              'configuration': copy.deepcopy(item['properties'].get('configuration'))}
        return {'schemaVersion': 1, 'kind': 'portal-odbc18-proxy-runtime', 'status': 'verified',
            'sqlServerId': self.server, 'observedPolicy': 'Proxy', 'verifiedAt': '2026-01-02T00:00:00Z',
            'checks': {key: True for key in ('nativeLinux', 'managedIdentity', 'tlsHostname',
                       'runtimeProxySql', 'authAndSessions', 'jobsCompatibility')},
            'resources': resources, 'activeRevisions': [{'id': self.revision['id'],
                'createdTime': self.revision['properties']['createdTime'],
                'template': copy.deepcopy(self.revision['properties']['template'])}]}


class SqlPolicyNsgIntegrationTests(unittest.TestCase):
    def change(self, arm, proof):
        with tempfile.TemporaryDirectory() as directory:
            workspace = local_paths(arm.config, Path(directory))
            context = OperationContext(workspace.root / 'params.local.json', arm.config,
                                       arm.state, workspace, transport=arm)
            journal = workspace.state / 'sql-policy-integration.json'
            def record(event, receipt):
                context.save_private(journal, receipt, overwrite=journal.exists())
            with context.locked():
                result = policy.change_sql_connection_policy(arm.config, operation._Transport(context),
                    server_id=arm.server, target_mode='Redirect', expected_current='Proxy', execute=True,
                    readiness_verifier=lambda *args, **kwargs: proof, record=record, now=arm.now)
            self.assertEqual(json.loads(journal.read_text())['status'], 'verified')
            return result

    def test_real_nsg_gate_reads_four_rules_before_the_one_sql_child_mutation(self):
        arm = CombinedArm()
        before = copy.deepcopy(arm.resources)
        result = self.change(arm, arm.proof())
        self.assertEqual(result['status'], 'verified')
        self.assertFalse(result['runtimeVerified'])
        self.assertEqual([(call['method'], call['path'].split('?', 1)[0]) for call in arm.mutations()],
                         [('PUT', arm.policy)])
        reads = {call['path'].split('?', 1)[0] for call in arm.calls if call['method'] == 'GET'}
        self.assertTrue({arm.nsg_id(group) for group in ('pep', 'management', 'aca')} <= reads)
        for target, original in before.items():
            if target != arm.policy:
                self.assertEqual(arm.resources[target], original)

    def test_real_nsg_tuple_mismatch_stops_before_the_sql_child_put(self):
        arm = CombinedArm()
        parent = arm.resources[arm.nsg_id('management')]
        target = next(row for row in parent['properties']['securityRules'] if row['name'] == 'allow-sql-egress')
        target['properties']['destinationPortRange'] = '1433'
        with self.assertRaises((policy.SqlConnectionPolicyError, nsg.FixedNsgError)):
            self.change(arm, arm.proof())
        self.assertEqual(arm.mutations(), [])

    def test_current_job_generation_is_read_even_when_the_image_is_unchanged(self):
        arm = CombinedArm()
        proof = arm.proof()
        arm.resources[arm.schema]['systemData']['createdAt'] = '2026-01-02T00:00:00Z'
        with self.assertRaises(policy.SqlConnectionPolicyError):
            self.change(arm, proof)
        self.assertEqual(arm.mutations(), [])

    def test_current_job_configuration_is_bound_to_runtime_acceptance(self):
        arm = CombinedArm()
        proof = arm.proof()
        arm.resources[arm.schema]['properties']['configuration'] = {'triggerType': 'Manual', 'replicaRetryLimit': 2}
        with self.assertRaises(policy.SqlConnectionPolicyError):
            self.change(arm, proof)
        self.assertEqual(arm.mutations(), [])

    def test_portal_generation_is_rechecked_after_the_real_nsg_preflight(self):
        arm = CombinedArm()
        proof = arm.proof()
        def change_job_during_nsg_read(store, identity):
            if identity == arm.nsg_id('management'):
                store.resources[arm.schema]['systemData']['lastModifiedAt'] = '2026-01-02T00:00:01Z'
        arm.get_hook = change_job_during_nsg_read
        with self.assertRaises(policy.SqlConnectionPolicyError):
            self.change(arm, proof)
        self.assertEqual(arm.mutations(), [])


class SqlPolicyJournalIntegrationTests(unittest.TestCase):
    def context(self, arm, root):
        workspace = local_paths(arm.config, root)
        return OperationContext(workspace.root / 'params.local.json', arm.config,
                                arm.state, workspace, transport=arm)

    def run_proxy(self, context):
        with context.locked():
            return operation.run(context, target_mode='Proxy', expected_current='Redirect', execute=True)

    def test_actual_cli_journal_reconciles_a_lost_response_without_resending(self):
        arm = CombinedArm('Redirect')
        arm.drop_response = True
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(arm, Path(directory))
            with self.assertRaises(operation.OperationError):
                self.run_proxy(context)
            journal = context.workspace.state / 'sql-connection-policy/redirect-to-proxy.json'
            self.assertEqual(json.loads(journal.read_text())['status'], 'response-unknown')
            result = self.run_proxy(context)
            self.assertEqual(result['status'], 'reconciled')
            self.assertEqual(result['azureWrites'], 0)
            self.assertEqual(json.loads(journal.read_text())['status'], 'reconciled')
        self.assertEqual(len(arm.mutations()), 1)

    def test_actual_journal_saved_before_put_blocks_retry_after_record_failure(self):
        arm = CombinedArm('Redirect')
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(arm, Path(directory))
            with mock.patch.object(context, 'record', side_effect=OSError('Synthetic record interruption')):
                with self.assertRaises(operation.OperationError):
                    self.run_proxy(context)
            journal = context.workspace.state / 'sql-connection-policy/redirect-to-proxy.json'
            self.assertEqual(json.loads(journal.read_text())['status'], 'submitting')
            with self.assertRaises(operation.OperationError):
                self.run_proxy(context)
        self.assertEqual(arm.mutations(), [])


if __name__ == '__main__':
    unittest.main()
