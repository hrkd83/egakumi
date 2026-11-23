"""Synthetic-only safety tests for the SQL connection-policy child operation."""
import contextlib
import copy
from datetime import datetime, timezone
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import fixed_nsg, sql_connection_policy as helper
from operations import sql_connection_policy as operation


class SqlArm:
    """Independent SQL and ACA ARM fixtures with no ETag on the SQL child."""
    def __init__(self, mode='Proxy'):
        self.config = {'subscriptionId': '11111111-1111-4111-8111-111111111111',
                       'azureTenantId': '22222222-2222-4222-8222-222222222222',
                       'resourceStem': 'synthetic', 'resourceGroupNames': {'svc': 'rg-synthetic'},
                       'productName': 'fixture', 'environmentName': 'synthetic', 'location': 'japaneast'}
        group = '/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/rg-synthetic'
        self.server = group + '/providers/Microsoft.Sql/servers/sql-synthetic'
        self.policy = self.server + '/connectionPolicies/default'
        tags = {'product': 'fixture', 'environment': 'synthetic', 'azd-env-name': 'synthetic'}
        self.resources = {group: {'id': group, 'tags': tags}, self.server: {
            'id': self.server, 'name': 'sql-synthetic', 'type': 'Microsoft.Sql/servers',
            'location': 'japaneast', 'tags': tags, 'properties': {
                'state': 'Ready', 'publicNetworkAccess': 'Disabled', 'minimalTlsVersion': '1.2',
                'administrators': {'azureADOnlyAuthentication': True}}}, self.policy: {
            'id': self.policy, 'name': 'default', 'type': 'Microsoft.Sql/servers/connectionPolicies',
            'properties': {'connectionType': mode}}}
        self.app = group + '/providers/Microsoft.App/containerApps/ca-synthetic-portal'
        self.schema = group + '/providers/Microsoft.App/jobs/caj-synthetic-psql'
        self.recon = group + '/providers/Microsoft.App/jobs/caj-synthetic-psync'
        self.template = {'containers': [{'name': 'portal', 'image': 'synthetic.invalid/portal@sha256:' + 'a' * 64,
                                         'env': [{'name': 'PORTAL_SQL_AUTH', 'value': 'managed-identity'}]}]}
        identity = {'type': 'UserAssigned', 'userAssignedIdentities': {
            group + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-synthetic': {
                'principalId': '33333333-3333-4333-8333-333333333333'}}}
        for target in (self.app, self.schema, self.recon):
            self.resources[target] = {'id': target, 'name': target.rsplit('/', 1)[-1],
                'type': 'Microsoft.App/containerApps' if target == self.app else 'Microsoft.App/jobs',
                'tags': tags, 'identity': identity, 'systemData': {
                    'createdAt': '2026-01-01T00:00:00Z', 'lastModifiedAt': '2026-01-01T00:00:00Z'},
                'properties': {'provisioningState': 'Succeeded', 'template': copy.deepcopy(self.template),
                    'configuration': {'secrets': [{'name': 'synthetic-ref', 'keyVaultUrl': 'https://example.invalid/secrets/synthetic'}],
                                      'registries': [{'server': 'synthetic.invalid', 'identity': 'system'}]}}}
        self.revision = {'id': self.app + '/revisions/ca-synthetic-portal--r1',
            'name': 'ca-synthetic-portal--r1', 'type': 'Microsoft.App/containerApps/revisions',
            'properties': {'active': True, 'createdTime': '2026-01-01T00:00:00Z',
                'provisioningState': 'Succeeded', 'healthState': 'Healthy', 'runningState': 'Running',
                'template': copy.deepcopy(self.template)}}
        self.resources[self.app + '/revisions'] = {'value': [self.revision]}
        self.state = {key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        self.state['outputs'] = {'gatewayFoundation': {'sqlServerId': self.server},
                                 'portal': {'sharedSqlServerId': self.server}}
        self.calls = []
        self.policy_reads = 0
        self.second_read_mode = None
        self.failure = None
        self.http_status = 200
        self.delay_reads = 0
        self.pending = None
        self.now = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        target = path.split('?', 1)[0]
        if method == 'GET':
            if target == self.policy:
                self.policy_reads += 1
                if self.policy_reads == 2 and self.second_read_mode:
                    self.resources[target]['properties']['connectionType'] = self.second_read_mode
                if self.pending and self.delay_reads <= 0:
                    self.resources[target]['properties']['connectionType'] = self.pending
                    self.pending = None
                else:
                    self.delay_reads -= 1
            return {'statusCode': 200, 'body': copy.deepcopy(self.resources[target])}
        if method != 'PUT' or target != self.policy:
            raise AssertionError('Only the SQL policy child may be mutated')
        if self.failure == 'before': raise ConnectionError('SYNTHETIC_PRIVATE_ERROR')
        if self.http_status in (200, 202):
            if self.delay_reads:
                self.pending = body['properties']['connectionType']
            else:
                self.resources[target]['properties']['connectionType'] = body['properties']['connectionType']
        if self.failure == 'after': raise ConnectionError('SYNTHETIC_PRIVATE_ERROR')
        return {'statusCode': self.http_status, 'body': None}

    def writes(self):
        return [row for row in self.calls if row[0] != 'GET']

    def proof(self):
        generations = helper._portal_generations(self.config, self)
        return {'schemaVersion': 1, 'kind': 'portal-odbc18-proxy-runtime', 'status': 'verified',
            'sqlServerId': self.server, 'observedPolicy': 'Proxy', 'verifiedAt': '2026-01-02T00:00:00Z',
            'checks': {key: True for key in helper.READINESS_CHECKS}, **generations}


class SqlConnectionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.arm = SqlArm()
        self.records = []
        self.nsg_gate = mock.Mock(return_value={
            'targetMode': 'Redirect', 'sqlRulesVerified': 4, 'runtimeVerified': False})

    def change(self, **options):
        values = {'server_id': self.arm.server, 'target_mode': 'Redirect', 'expected_current': 'Proxy',
                  'record': lambda event, value: self.records.append((event, value)),
                  'sleep': lambda _: None, 'now': self.arm.now, **options}
        with mock.patch.object(fixed_nsg, 'verify_sql_nsg_rules', self.nsg_gate):
            return helper.change_sql_connection_policy(self.arm.config, self.arm, **values)

    def test_read_only_plan_without_runtime_evidence_is_never_ready(self):
        result = self.change()
        self.assertEqual(result['status'], 'planned')
        self.assertFalse(result['redirectExecutionAvailable'])
        self.assertFalse(result['runtimeVerified'])
        self.assertFalse(self.arm.writes())

    def test_redirect_cannot_execute_with_missing_or_boolean_verifier(self):
        for verifier in (None, True):
            with self.subTest(verifier=verifier), self.assertRaises(helper.SqlConnectionPolicyError):
                self.change(execute=True, readiness_verifier=verifier)
        self.assertFalse(self.arm.writes())

    def test_proxy_rollback_sends_one_minimal_child_put_without_etag(self):
        self.arm = SqlArm('Redirect')
        before = copy.deepcopy(self.arm.resources)
        result = self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
        self.assertEqual(result['azureWrites'], 1)
        self.assertFalse(result['runtimeVerified'])
        self.assertTrue(result['connectionDrainRequired'])
        self.assertEqual(self.arm.writes(), [('PUT', self.arm.policy + '?api-version=2023-08-01',
                                            {'properties': {'connectionType': 'Proxy'}})])
        for key, old in before.items():
            if key != self.arm.policy: self.assertEqual(old, self.arm.resources[key])
        self.assertFalse(self.records[-1][1]['conditionalPutSupported'])
        self.assertEqual([row[0] for row in self.records], ['submitting', 'accepted', 'verified'])

    def test_redirect_requires_current_revision_job_generation_and_nsg_gate(self):
        proof = self.arm.proof()
        result = self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['azureWrites'], 1)
        self.nsg_gate.assert_called_once_with(self.arm.config, self.arm,
                                             target_mode='Redirect', require_retired=True)

    def test_changed_active_revision_or_job_or_identity_stops_before_put(self):
        for changed in ('revision', 'job', 'identity'):
            with self.subTest(changed=changed):
                self.arm = SqlArm()
                proof = self.arm.proof()
                if changed == 'revision': self.arm.revision['properties']['createdTime'] = '2026-01-02T00:00:00Z'
                elif changed == 'job': self.arm.resources[self.arm.schema]['properties']['template']['containers'][0]['image'] = 'synthetic.invalid/portal@sha256:' + 'b' * 64
                else: self.arm.resources[self.arm.app]['identity'] = {'type': 'None'}
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
                self.assertFalse(self.arm.writes())

    def test_stale_false_missing_or_wrong_sql_runtime_evidence_is_rejected(self):
        for field in ('stale', 'false', 'missing', 'sql', 'boolean-version'):
            proof = self.arm.proof()
            if field == 'stale': proof['verifiedAt'] = '2025-12-01T00:00:00Z'
            elif field == 'false': proof['checks']['managedIdentity'] = False
            elif field == 'missing': proof['checks'].pop('nativeLinux')
            elif field == 'sql': proof['sqlServerId'] += '-other'
            else: proof['schemaVersion'] = True
            with self.subTest(field=field), self.assertRaises(helper.SqlConnectionPolicyError):
                self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertFalse(self.arm.writes())

    def test_incomplete_or_unhealthy_revision_inventory_cannot_authorize_switch(self):
        proof = self.arm.proof()
        self.arm.resources[self.arm.app + '/revisions']['nextLink'] = 'https://example.invalid/next'
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertFalse(self.arm.writes())

    def test_bad_nsg_gate_prevents_sql_mutation(self):
        proof = self.arm.proof()
        self.nsg_gate.return_value = {'targetMode': 'Redirect', 'sqlRulesVerified': 3}
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertFalse(self.arm.writes())

    def test_nsg_gate_exception_is_sanitized_without_traceback_or_mutation(self):
        proof = self.arm.proof()
        self.nsg_gate.side_effect = fixed_nsg.FixedNsgError('SYNTHETIC_PRIVATE_ERROR')
        with self.assertRaises(helper.SqlConnectionPolicyError) as error:
            self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR', str(error.exception))
        self.assertFalse(self.arm.writes())

    def test_runtime_proof_binds_app_and_job_configuration_and_modified_time(self):
        for target, field in (('app', 'configuration'), ('schema', 'configuration'),
                              ('recon', 'configuration'), ('app', 'lastModifiedAt')):
            with self.subTest(target=target, field=field):
                self.arm = SqlArm()
                proof = self.arm.proof()
                resource = self.arm.resources[getattr(self.arm, target)]
                if field == 'configuration':
                    resource['properties']['configuration']['triggerType'] = 'Schedule'
                else:
                    resource['systemData']['lastModifiedAt'] = '2026-01-02T00:00:00Z'
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
                self.assertFalse(self.arm.writes())

    def test_missing_configuration_cannot_authorize_runtime_acceptance(self):
        for target in ('app', 'schema', 'recon'):
            with self.subTest(target=target):
                self.arm = SqlArm()
                proof = self.arm.proof()
                self.arm.resources[getattr(self.arm, target)]['properties'].pop('configuration')
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
                self.assertFalse(self.arm.writes())

    def test_optional_modified_time_is_bound_when_added(self):
        resource = self.arm.resources[self.arm.schema]
        resource['systemData'].pop('lastModifiedAt')
        proof = self.arm.proof()
        self.assertIsNone(proof['resources']['schemaJob']['lastModifiedAt'])
        resource['systemData']['lastModifiedAt'] = '2026-01-02T00:00:00Z'
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
        self.assertFalse(self.arm.writes())

    def test_portal_change_during_nsg_or_sql_preflight_is_rejected_before_put(self):
        for during in ('nsg', 'sql'):
            with self.subTest(during=during):
                self.arm = SqlArm()
                proof = self.arm.proof()
                normal_request = self.arm.request

                def change_portal():
                    self.arm.resources[self.arm.recon]['properties']['configuration']['triggerType'] = 'Schedule'

                if during == 'nsg':
                    def nsg(*args, **kwargs):
                        change_portal()
                        return {'targetMode': 'Redirect', 'sqlRulesVerified': 4}
                    self.nsg_gate.side_effect = nsg
                else:
                    self.nsg_gate.side_effect = None
                    def request(method, path, body=None):
                        response = normal_request(method, path, body)
                        if method == 'GET' and path.split('?', 1)[0] == self.arm.policy and self.arm.policy_reads == 2:
                            change_portal()
                        return response
                    self.arm.request = request
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(execute=True, readiness_verifier=lambda *args, **kwargs: proof)
                self.assertFalse(self.arm.writes())

    def test_preflight_policy_drift_and_owned_tag_mismatch_are_rejected(self):
        self.arm = SqlArm('Redirect')
        self.arm.second_read_mode = 'Default'
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
        self.assertFalse(self.arm.writes())
        self.arm = SqlArm()
        self.arm.resources[self.arm.server]['tags'] = {'product': 'unowned'}
        with self.assertRaises(helper.SqlConnectionPolicyError): self.change()
        self.assertFalse(self.arm.writes())

    def test_missing_constructed_output_or_wrong_explicit_target_never_infers(self):
        self.assertEqual(helper.constructed_sql_server_id(self.arm.config, self.arm.state), self.arm.server)
        self.arm.state['outputs']['portal'].clear()
        with self.assertRaises(helper.SqlConnectionPolicyError): helper.constructed_sql_server_id(self.arm.config, self.arm.state)
        with self.assertRaises(helper.SqlConnectionPolicyError): self.change(server_id=self.arm.server + '-other')
        self.assertFalse(self.arm.calls)

    def test_accepted_but_lost_response_is_reconciled_without_any_resend(self):
        self.arm = SqlArm('Redirect')
        self.arm.failure = 'after'
        with self.assertRaises(helper.SqlConnectionPolicyError) as error:
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR', str(error.exception))
        prior = self.records[-1][1]
        self.assertEqual(prior['status'], 'response-unknown')
        self.arm.failure = None
        result = self.change(target_mode='Proxy', expected_current='Redirect', execute=True, prior_receipt=prior)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['azureWrites'], 0)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_lost_response_before_acceptance_remains_unresolved_and_is_not_resent(self):
        self.arm = SqlArm('Redirect')
        self.arm.failure = 'before'
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
        prior = self.records[-1][1]
        self.arm.failure = None
        with self.assertRaisesRegex(helper.SqlConnectionPolicyError, 'no mutation will be resent'):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True, prior_receipt=prior)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_crash_journal_and_failed_journal_persistence_cannot_trigger_put(self):
        self.arm = SqlArm('Redirect')
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True,
                        record=lambda *args: (_ for _ in ()).throw(OSError('SYNTHETIC_PRIVATE_ERROR')))
        self.assertFalse(self.arm.writes())

    def test_readback_failure_is_not_retried_as_mutation(self):
        self.arm = SqlArm('Redirect')
        self.arm.delay_reads = 100
        clock = iter((0, 200))
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True,
                        monotonic=lambda: next(clock), timeout=10)
        self.assertEqual(len(self.arm.writes()), 1)
        self.assertEqual(self.records[-1][1]['status'], 'readback-unknown')

    def test_timeout_or_server_error_cannot_be_retried_as_known_rejection(self):
        for status in (408, 429, 500, 503):
            with self.subTest(status=status):
                self.arm = SqlArm('Redirect')
                self.arm.http_status = status
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
                prior = self.records[-1][1]
                self.assertEqual(prior['status'], 'response-unknown')
                self.arm.http_status = 200
                with self.assertRaises(helper.SqlConnectionPolicyError):
                    self.change(target_mode='Proxy', expected_current='Redirect', execute=True, prior_receipt=prior)
                self.assertEqual(len(self.arm.writes()), 1)

    def test_incomplete_prior_receipt_cannot_be_reconciled(self):
        self.arm = SqlArm('Redirect')
        self.arm.failure = 'after'
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True)
        prior = copy.deepcopy(self.records[-1][1])
        prior['before'].pop('connectionType')
        self.arm.failure = None
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(target_mode='Proxy', expected_current='Redirect', execute=True, prior_receipt=prior)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_redirect_noop_still_has_no_public_verifier_bypass(self):
        self.arm = SqlArm('Redirect')
        with self.assertRaises(helper.SqlConnectionPolicyError):
            self.change(expected_current='Redirect', execute=True)
        self.assertFalse(self.arm.writes())

    def test_public_run_sanitizes_missing_construction_outputs(self):
        context = types.SimpleNamespace(config=self.arm.config, state={'outputs': {}})
        with self.assertRaises(operation.OperationError) as error:
            operation.run(context, target_mode='Proxy', expected_current='Redirect', execute=True)
        self.assertNotIn(self.arm.server, str(error.exception))

    def test_cli_persistent_journal_survives_lost_response_and_prevents_resend(self):
        self.arm = SqlArm('Redirect')
        self.arm.failure = 'after'
        saved = {}

        class Journal:
            parent = Path('synthetic-state')
            def exists(self): return bool(saved)
            def read_text(self, **kwargs):
                import json
                return json.dumps(saved)

        journal = Journal()
        context = types.SimpleNamespace(config=self.arm.config, state=self.arm.state,
            workspace=types.SimpleNamespace(state=Path('synthetic-state')),
            output_path=lambda _: journal, record=lambda *args: None)

        def save(path, value, overwrite=False):
            self.assertIs(path, journal)
            saved.clear()
            saved.update(copy.deepcopy(value))

        def request(method, identity, api, body=None):
            if method == 'PUT': self.assertEqual(saved['status'], 'submitting')
            return self.arm.request(method, identity + '?api-version=' + api, body=body)

        context.save_private = save
        context.request = request
        with mock.patch.object(operation, 'check_private_permissions'):
            with self.assertRaises(operation.OperationError):
                operation.run(context, target_mode='Proxy', expected_current='Redirect', execute=True)
            self.assertEqual(saved['status'], 'response-unknown')
            self.arm.failure = None
            result = operation.run(context, target_mode='Proxy', expected_current='Redirect', execute=True)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(saved['status'], 'reconciled')
        self.assertEqual(len(self.arm.writes()), 1)

    def test_public_cli_sanitizes_failures_and_has_no_readiness_bypass(self):
        output = io.StringIO()
        with mock.patch.object(operation.OperationContext, 'load', side_effect=operation.OperationError('SYNTHETIC_PRIVATE_ERROR')):
            with contextlib.redirect_stdout(output):
                status = operation.main(['--config', 'synthetic-input.json', '--target-mode', 'Redirect', '--expected-current', 'Proxy', '--execute'])
        self.assertEqual(status, 1)
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR', output.getvalue())
        self.assertNotIn(self.arm.server, output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            operation.main(['--config', 'synthetic-input.json', '--target-mode', 'Redirect', '--expected-current', 'Proxy', '--ready'])

    def test_runtime_checkout_is_explicit_and_not_discovered_or_copied(self):
        with mock.patch.object(operation.OperationContext, 'load',
                side_effect=operation.OperationError('SYNTHETIC_PRIVATE_ERROR')) as load:
            with contextlib.redirect_stdout(io.StringIO()):
                status = operation.main(['--config', 'synthetic-input.json',
                    '--runtime-root', 'synthetic-runtime', '--target-mode', 'Redirect',
                    '--expected-current', 'Proxy'])
        self.assertEqual(status, 1)
        load.assert_called_once_with(Path('synthetic-input.json'), runtime_root=Path('synthetic-runtime'))


if __name__ == '__main__':
    unittest.main()
