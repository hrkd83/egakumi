"""Protected journal and exact four-child orchestration with synthetic ARM only."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import fixed_nsg as nsg, private_permissions
from deployment.helpers.paths import LocalPaths
from operations.common import OperationContext, OperationError
from operations import sql_nsg_upgrade as operation
from bicep_helpers import compile_bicep
from test_fixed_nsg_migration import fixture_config, writable
from test_sql_redirect_nsg import SqlTransport


class UpgradeArm(SqlTransport):
    def __init__(self, config, *, proxy=True):
        super().__init__(config, proxy=proxy)
        self.deployment_failure = None
        self.deployment_state = 'Succeeded'
        self.child_failure = None
        self.deployment_headers = {}
        self.deployment_metadata = True
        self.export_template = False

    def request(self, method, path, body=None, headers=None):
        identity = path.split('?', 1)[0]
        if method in {'PUT', 'DELETE'} and '/securityRules/' in identity and self.child_failure == 'before':
            self.child_failure = None
            self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': headers})
            raise OSError('SYNTHETIC_PRIVATE_RESPONSE')
        if '/providers/Microsoft.Resources/deployments/' in identity:
            self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': headers})
            if method == 'PUT':
                if self.deployment_failure == 'before':
                    raise OSError('SYNTHETIC_PRIVATE_RESPONSE')
                self.assert_deployment(body)
                mode = body['properties']['parameters']['targetMode']['value']
                saved = copy.deepcopy(body)
                saved['id'] = identity
                saved['properties']['provisioningState'] = self.deployment_state
                if self.deployment_metadata:
                    for value in saved['properties']['parameters'].values(): value['type'] = 'Object'
                self.resources[identity] = saved
                if self.deployment_state == 'Succeeded': self.deploy_sql(mode)
                if self.deployment_failure == 'after':
                    raise OSError('SYNTHETIC_PRIVATE_RESPONSE')
                return {'statusCode': 201, 'body': copy.deepcopy(saved)}
            if method == 'POST' and identity.endswith('/exportTemplate'):
                saved = self.resources[identity[:-len('/exportTemplate')]]
                return {'statusCode': 200, 'body': {'template': copy.deepcopy(saved['properties']['template'])}}
            if method != 'GET': raise AssertionError('Only the exact saved deployment may be PUT')
            saved = copy.deepcopy(self.resources.get(identity))
            if saved and self.export_template: saved['properties'].pop('template')
            return {'statusCode': 200 if saved else 404, 'body': saved, 'headers': self.deployment_headers}
        return super().request(method, path, body=body, headers=headers)

    def assert_deployment(self, body):
        if set(body) != {'properties'} or body['properties']['mode'] != 'Incremental':
            raise AssertionError('A resource-group child deployment must be Incremental without location')
        operation.checked_template(body['properties']['template'])

    def deployment_writes(self):
        return [r for r in self.calls if r['method'] == 'PUT' and '/deployments/' in r['path']]


class RecordingContext(OperationContext):
    def record(self, kind, value):
        if self.hook: self.hook(kind, value)
        return super().record(kind, value)


class SqlNsgUpgradeOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/modules/sql-redirect-nsg-upgrade.bicep')

    def setUp(self):
        if os.name == 'nt':
            acl = mock.patch.object(private_permissions, '_windows_owner_acl')
            acl.start(); self.addCleanup(acl.stop)
        directory = tempfile.TemporaryDirectory(prefix='synthetic-sql-nsg-')
        self.addCleanup(directory.cleanup)
        self.config = fixture_config()
        self.config['azureTenantId'] = '22222222-2222-4222-8222-222222222222'
        self.arm = UpgradeArm(self.config)
        self.workspace = LocalPaths(Path(directory.name), self.config['environmentName'])
        state = {key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        server = self.arm.policy_id[:-len('/connectionPolicies/default')]
        state['outputs'] = {'gatewayFoundation': {'sqlServerId': server}, 'portal': {'sharedSqlServerId': server}}
        self.context = RecordingContext(self.workspace.params, self.config, state, self.workspace, self.arm)
        self.context.hook = None
        self.context.save_private(self.context.state_path, state)
        self.now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.options = {'compiler': lambda: copy.deepcopy(self.template), 'what_if': self.what_if,
                        'wait_seconds': 0, 'sleep': lambda _: None, 'now': lambda: self.now}

    def what_if(self, template_path, parameters_path):
        self.assertEqual(json.loads(template_path.read_text()), self.template)
        values = json.loads(parameters_path.read_text())['parameters']
        self.assertEqual(set(values), {'resourcePrefix', 'bindings', 'targetMode'})
        self.assertEqual(values['bindings']['value'], nsg._sql_binding_view(self.arm.bindings))
        mode = values['targetMode']['value']
        return {'status': 'Succeeded', 'changes': [{'resourceId': self.arm.nsg_id(r['group']) + '/securityRules/' + r['targetName'],
            'changeType': 'Create', 'after': {'properties': writable(r['target'])}}
            for r in nsg._sql_rows(self.arm.bindings, mode)]}

    def run_operation(self, **overrides):
        return operation.run(self.context, **{**self.options, **overrides})

    def journal(self, mode='Redirect'):
        path = self.workspace.state / 'sql-nsg-upgrade' / (mode.lower() + '.json')
        return json.loads(path.read_text())

    def proxy_phase(self):
        return operation.VerifiedProxyPhase(
            self.arm.policy_id[:-len('/connectionPolicies/default')], copy.deepcopy(self.context.state),
            nsg._sql_binding_view(self.arm.bindings), self.now,
            {k: True for k in operation.DRAIN_CHECKS})

    def test_default_plan_is_get_only_and_never_runs_live_what_if(self):
        result = self.run_operation(what_if=mock.Mock(side_effect=AssertionError('No live what-if during plan')))
        self.assertEqual(result['sourceChildrenToStage'], 4)
        self.assertEqual(result['azureWrites'], 0)
        self.assertTrue(all(r['method'] == 'GET' for r in self.arm.calls))
        self.assertFalse((self.workspace.state / 'sql-nsg-upgrade').exists())
        proxy = self.run_operation(target_mode='Proxy')
        self.assertEqual(proxy['sourceChildrenToStage'], 0)
        self.assertFalse(self.arm.mutations())

    def test_execute_changes_only_four_sources_and_one_four_child_deployment_and_is_idempotent(self):
        before = nsg._sql_without_rules(nsg._parents(self.config, self.arm), self.arm.bindings)
        result = self.run_operation(execute=True)
        self.assertEqual(result, {'status': 'completed', 'targetMode': 'Redirect', 'sqlRulesVerified': 4,
                                 'legacySqlRules': 0, 'azureWrites': 9, 'runtimeVerified': False})
        calls = self.arm.mutations()
        self.assertEqual([r['method'] for r in calls], ['PUT'] * 5 + ['DELETE'] * 4)
        sources = {self.arm.nsg_id(r['group']) + '/securityRules/' + r['name']
                   for r in nsg._sql_rows(self.arm.bindings, 'Redirect')}
        self.assertEqual({r['path'].split('?')[0] for r in calls if '/securityRules/' in r['path']}, sources)
        after = nsg._sql_without_rules(nsg._parents(self.config, self.arm), self.arm.bindings)
        self.assertEqual({g: nsg._snapshot(p) for g, p in before.items()},
                         {g: nsg._snapshot(p) for g, p in after.items()})
        self.assertEqual(self.journal()['phase'], 'completed')
        self.assertIsNone(self.journal()['pendingChild'])
        self.assertEqual(self.run_operation(execute=True)['azureWrites'], 0)
        self.assertEqual(len(self.arm.mutations()), 9)
        files = [p for p in Path(self.workspace.repository).rglob('*') if p.is_file()]
        self.assertTrue(all(p.is_relative_to(self.workspace.root) for p in files))

    def test_operation_lock_refuses_parallel_execution_before_any_arm_read(self):
        with self.context.locked(), self.assertRaisesRegex(OperationError, 'Another operation'):
            self.run_operation(execute=True)
        self.assertFalse(self.arm.calls)

    def test_failed_write_ahead_record_stops_before_first_mutation_and_does_not_resend(self):
        def fail(kind, value):
            if kind == 'sql-nsg-child-submitting': raise OSError('SYNTHETIC_PRIVATE_JOURNAL')
        self.context.hook = fail
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertFalse(self.arm.mutations())
        self.assertIsNotNone(self.journal()['pendingChild'])
        self.context.hook = None
        with self.assertRaisesRegex(OperationError, 'will not be resent'):
            self.run_operation(execute=True)
        self.assertFalse(self.arm.mutations())

    def test_lost_child_response_before_acceptance_is_never_resent(self):
        self.arm.child_failure = 'before'
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        with self.assertRaisesRegex(OperationError, 'will not be resent'): self.run_operation(execute=True)
        self.assertEqual(len(self.arm.mutations()), 1)

    def test_lost_child_response_after_acceptance_reconciles_and_only_submits_remaining_work(self):
        self.arm.fail_after_put = True
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertIsNotNone(self.journal()['pendingChild'])
        self.assertEqual(self.run_operation(execute=True)['status'], 'completed')
        source_puts = [r for r in self.arm.mutations() if r['method'] == 'PUT' and '/securityRules/' in r['path']]
        self.assertEqual(len(source_puts), 4)
        self.assertEqual(len({r['path'] for r in source_puts}), 4)

    def test_lost_delete_response_reconciles_without_repeating_deleted_child(self):
        self.arm.lose_delete = True
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertEqual(self.journal()['pendingChild']['method'], 'DELETE')
        self.run_operation(execute=True)
        deletes = [r for r in self.arm.mutations() if r['method'] == 'DELETE']
        self.assertEqual(len(deletes), 4)
        self.assertEqual(len({r['path'] for r in deletes}), 4)

    def test_lost_deployment_response_before_acceptance_is_never_resent_or_retired(self):
        self.arm.deployment_failure = 'before'
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        with self.assertRaisesRegex(OperationError, 'will not be resent'): self.run_operation(execute=True)
        self.assertEqual(len(self.arm.deployment_writes()), 1)
        self.assertFalse(any(r['method'] == 'DELETE' for r in self.arm.mutations()))

    def test_lost_deployment_response_after_acceptance_is_readback_verified_without_resubmission(self):
        self.arm.deployment_failure = 'after'
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertEqual(self.journal()['phase'], 'deployment-submitting')
        self.run_operation(execute=True)
        self.assertEqual(len(self.arm.deployment_writes()), 1)
        self.assertEqual(self.journal()['phase'], 'completed')

    def test_failed_or_pending_deployment_preserves_every_source_child_and_never_resends(self):
        for state in ('Failed', 'Updating'):
            with self.subTest(state=state):
                # A fresh protected environment for each state.
                self.arm = UpgradeArm(self.config)
                self.context.transport = self.arm
                journal_path = self.workspace.state / 'sql-nsg-upgrade/redirect.json'
                journal_path.unlink(missing_ok=True)
                self.arm.deployment_state = state
                self.arm.deployment_headers = {'Retry-After': '61'}
                with self.assertRaises(OperationError): self.run_operation(execute=True)
                with self.assertRaises(OperationError): self.run_operation(execute=True)
                self.assertEqual(len(self.arm.deployment_writes()), 1)
                self.assertFalse(any(r['method'] == 'DELETE' for r in self.arm.mutations()))
                self.assertEqual(self.journal()['phase'], 'deployment-submitting')

    def test_actual_deployment_parameter_metadata_is_accepted_and_export_is_read_only(self):
        self.arm.export_template = True
        self.run_operation(execute=True)
        self.assertTrue(any(r['method'] == 'POST' and '/exportTemplate?' in r['path'] for r in self.arm.calls))
        self.assertEqual(len(self.arm.deployment_writes()), 1)

    def test_changed_deployment_template_or_parameters_stop_before_retirement(self):
        self.arm.deployment_failure = 'after'
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        identity = self.arm.deployment_writes()[0]['path'].split('?')[0]
        saved = copy.deepcopy(self.arm.resources[identity])
        for issue in ('template', 'mode', 'parameters', 'identity'):
            with self.subTest(issue=issue):
                value = copy.deepcopy(saved)
                if issue == 'template': value['properties']['template']['resources'] = {}
                elif issue == 'mode': value['properties']['mode'] = 'Complete'
                elif issue == 'parameters': value['properties']['parameters']['bindings']['value'] = {}
                else: value['id'] += '-other'
                self.arm.resources[identity] = value
                with self.assertRaises(OperationError): self.run_operation(execute=True)
                self.assertFalse(any(r['method'] == 'DELETE' for r in self.arm.mutations()))
        self.assertEqual(len(self.arm.deployment_writes()), 1)

    def test_what_if_parent_unrelated_or_76_rule_expansion_blocks_deployment_and_retirement(self):
        def expanded(*paths):
            document = self.what_if(*paths)
            document['changes'].append({'resourceId': self.arm.nsg_id('apim'), 'changeType': 'Modify'})
            return document
        with self.assertRaises(OperationError): self.run_operation(execute=True, what_if=expanded)
        self.assertEqual(len(self.arm.mutations()), 4)
        self.assertFalse(self.arm.deployment_writes())
        def all_rules(*paths):
            document = self.what_if(*paths)
            for group, info in nsg._catalog('nsg-rule-priorities.json')['groups'].items():
                for name in info['rules']:
                    document['changes'].append({'resourceId': self.arm.nsg_id(group) + '/securityRules/' + name,
                                                'changeType': 'Modify'})
            return document
        with self.assertRaises(OperationError): self.run_operation(execute=True, what_if=all_rules)
        self.assertEqual(len(self.arm.mutations()), 4)

    def test_pinned_file_change_after_what_if_stops_deployment(self):
        def replace_file(template_path, parameters_path):
            document = self.what_if(template_path, parameters_path)
            self.context.save_private(parameters_path, {'parameters': {}}, overwrite=True)
            return document
        with self.assertRaisesRegex(OperationError, 'artifact changed'):
            self.run_operation(execute=True, what_if=replace_file)
        self.assertFalse(self.arm.deployment_writes())

    def test_unrelated_nsg_drift_after_first_stage_stops_next_mutation(self):
        def drift(kind, value):
            if kind == 'sql-nsg-helper' and value['event'] == 'staged':
                self.arm.resources[self.arm.nsg_id('apim')]['properties']['securityRules'][0]['properties']['description'] = 'Synthetic unrelated drift'
        self.context.hook = drift
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertEqual(len(self.arm.mutations()), 1)

    def test_sql_ip_drift_after_first_stage_stops_before_another_write(self):
        def drift(kind, value):
            if kind == 'sql-nsg-helper' and value['event'] == 'staged':
                nic = next(r for r in self.arm.resources.values()
                           if r.get('type') == 'Microsoft.Network/networkInterfaces'
                           and r.get('properties', {}).get('privateEndpoint', {}).get('id', '').endswith('-sql'))
                nic['properties']['ipConfigurations'][0]['properties']['privateIPAddress'] = '10.70.1.25'
        self.context.hook = drift
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertEqual(len(self.arm.mutations()), 1)

    def test_changed_child_etag_before_stage_stops_without_mutation(self):
        first = self.arm.nsg_id('pep') + '/securityRules/allow-aca-sql-1433-ingress'
        def drift(arm, identity):
            if identity == first:
                child = next(r for r in arm.resources[arm.nsg_id('pep')]['properties']['securityRules']
                             if r['name'] == 'allow-aca-sql-1433-ingress')
                child['etag'] = '"synthetic-concurrent-change"'
        self.arm.get_hook = drift
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertFalse(self.arm.mutations())

    def test_invalid_compiled_parent_or_loop_stops_before_any_source_is_staged(self):
        for issue in ('parent', 'condition', 'loop'):
            value = copy.deepcopy(self.template)
            child, = value['resources'].values()
            if issue == 'parent': child['type'] = nsg.NSG_TYPE
            elif issue == 'condition': child['condition'] = False
            else: child['copy']['count'] = 76
            with self.subTest(issue=issue), self.assertRaises(OperationError):
                self.run_operation(execute=True, compiler=lambda: value)
        self.assertFalse(self.arm.mutations())

    def test_four_targets_plus_unrelated_nochange_cannot_authorize_a_dynamic_fifth_put(self):
        value = copy.deepcopy(self.template)
        child, = value['resources'].values()
        child['copy']['count'] = "[length(variables('rules'))]"
        document = {'status': 'Succeeded', 'changes': [{'resourceId': self.arm.nsg_id(r['group']) + '/securityRules/' + r['targetName'],
            'changeType': 'Create', 'after': {'properties': writable(r['target'])}}
            for r in nsg._sql_rows(self.arm.bindings, 'Redirect')]}
        document['changes'].append({'resourceId': self.arm.nsg_id('apim') + '/securityRules/allow-storage-egress',
                                    'changeType': 'NoChange'})
        # ARM may include unrelated unchanged resources. The what-if gate may
        # accept those reads, but only a statically four-child template may PUT.
        nsg.verify_sql_nsg_upgrade_what_if(self.config, document, self.arm.bindings)
        with self.assertRaises(OperationError):
            self.run_operation(execute=True, compiler=lambda: value, what_if=lambda *_: document)
        self.assertFalse(self.arm.mutations())

    def test_core_state_drift_after_first_stage_stops_before_another_write(self):
        def change_state(kind, value):
            if kind == 'sql-nsg-helper' and value['event'] == 'staged':
                self.context.save_private(self.context.state_path, {**self.context.state, 'generation': 2}, overwrite=True)
        self.context.hook = change_state
        with self.assertRaises(OperationError): self.run_operation(execute=True)
        self.assertEqual(len(self.arm.mutations()), 1)

    def test_public_proxy_execute_is_refused_but_bound_internal_phase_restores_exact_proxy(self):
        self.arm = UpgradeArm(self.config, proxy=False); self.context.transport = self.arm
        for supplied in (None, True, {'checks': {k: True for k in operation.DRAIN_CHECKS}}):
            with self.subTest(supplied=supplied), self.assertRaises(OperationError):
                self.run_operation(execute=True, target_mode='Proxy', rollback_phase=supplied)
        self.assertFalse(self.arm.calls)
        self.arm.resources[self.arm.policy_id]['properties']['connectionType'] = 'Proxy'
        result = self.run_operation(execute=True, target_mode='Proxy', rollback_phase=self.proxy_phase())
        self.assertEqual(result['targetMode'], 'Proxy')
        self.assertEqual(len(self.arm.mutations()), 9)
        self.assertEqual(nsg.verify_sql_nsg_rules(self.config, self.arm, target_mode='Proxy', require_retired=True)['sqlRulesVerified'], 4)

    def test_expired_or_wrong_drain_context_refuses_proxy_before_first_mutation(self):
        self.arm = UpgradeArm(self.config, proxy=False); self.context.transport = self.arm
        self.arm.resources[self.arm.policy_id]['properties']['connectionType'] = 'Proxy'
        phase = self.proxy_phase()
        for issue in ('expired', 'checks', 'state', 'bindings', 'server'):
            fields = dict(phase.__dict__)
            if issue == 'expired': fields['verified_at'] -= timedelta(minutes=6)
            elif issue == 'checks': fields['checks'] = {k: False for k in operation.DRAIN_CHECKS}
            elif issue == 'state': fields['core_state'] = {}
            elif issue == 'bindings': fields['bindings'] = {}
            else: fields['sql_server_id'] += '-other'
            with self.subTest(issue=issue), self.assertRaises(OperationError):
                self.run_operation(execute=True, target_mode='Proxy', rollback_phase=operation.VerifiedProxyPhase(**fields))
        self.assertFalse(self.arm.mutations())

    def test_cli_requires_explicit_roots_and_sanitizes_success_and_failure(self):
        summary = {'status': 'planned', 'targetSqlChildren': 4, 'azureWrites': 0}
        for failing in (False, True):
            output = io.StringIO()
            with (mock.patch.object(operation.OperationContext, 'load', return_value=self.context) as load,
                 mock.patch.object(operation, 'run', return_value=summary,
                                   side_effect=OperationError('SYNTHETIC_PRIVATE_SENTINEL') if failing else None),
                 contextlib.redirect_stdout(output)):
                status = operation.main(['--config', str(self.workspace.params), '--runtime-root', str(self.workspace.repository)])
            load.assert_called_once_with(self.workspace.params, runtime_root=self.workspace.repository)
            self.assertEqual(status, int(failing))
            self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', output.getvalue())
            self.assertNotIn(str(self.workspace.repository), output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            operation.main(['--config', 'synthetic-input.json'])


if __name__ == '__main__':
    unittest.main()
