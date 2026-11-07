"""監視Exportの単独既知失敗だけを、同じ要求で一回再適用する境界を検証する。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import stat
import unittest
from unittest import mock

from bicep_helpers import compile_bicep
from lifecycle_fixtures import LifecycleFixture, ScriptedTransport
from deployment import lifecycle
from deployment.helpers.log_privacy import resource_ids


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class MonitoringExportReapplyTests(LifecycleFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/main.bicep')
        module = next(item['properties']['template'] for item in cls.template['resources']
                      if isinstance(item.get('properties', {}).get('template', {}).get('variables', {}).get('exportTables'), list))
        privacy = json.loads((Path(__file__).resolve().parents[1] / 'infra/log-privacy.json').read_text())
        cls.tables = [privacy['table'] if value == "[variables('privacy').table]" else value
                      for value in module['variables']['exportTables']]
        assert len(cls.tables) == 7 and all(isinstance(value, str) and not value.startswith('[') for value in cls.tables)

    def responses(self):
        ids = resource_ids(self.config)
        root = f"/subscriptions/{self.config['subscriptionId']}/providers/Microsoft.Resources/deployments/{self.config['resourceStem']}-monitoring"
        child = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames']['monitor']}/providers/Microsoft.Resources/deployments/{self.config['resourceStem']}-monitoring"
        failure = {'id': root, 'properties': {'provisioningState': 'Failed', 'error': {
            'code': 'DeploymentFailed', 'details': [{'code': 'ResourceDeploymentFailure', 'target': child}]}}}
        operations = {'value': [
            {'properties': {'provisioningState': 'Succeeded', 'targetResource': {'id': ids['workspace']}}},
            {'properties': {'provisioningState': 'Failed', 'targetResource': {
                'id': ids['export'], 'resourceType': 'Microsoft.OperationalInsights/workspaces/dataExports'},
                'statusMessage': {'error': {'code': 'InvalidParameter', 'message':
                    'HttpClient: Response status code does not indicate success - 404 (Not Found), due to reason - [The workspace could not be found].'}}}}]}
        workspace = {'id': ids['workspace'], 'name': 'law-' + self.config['resourceStem'],
            'type': 'Microsoft.OperationalInsights/workspaces', 'location': self.config['location'],
            'tags': {'product': self.config['productName'], 'environment': self.config['environmentName']},
            'properties': {'provisioningState': 'Succeeded', 'customerId': '11111111-1111-1111-1111-111111111111'}}
        export = {'id': ids['export'], 'properties': {'enable': True,
            'destination': {'resourceId': ids['storage']}, 'tableNames': list(self.tables)}}
        succeeded = {'properties': {'provisioningState': 'Succeeded', 'outputs': {'fixture': {'value': 'ready'}}}}
        ok = lambda body, status=200: {'statusCode': status, 'headers': {}, 'body': body}
        return [ok({}, 201), ok(failure), ok(operations), ok(workspace), ok(export), ok({}, 201), ok(succeeded)]

    def exercise(self, responses, *, phase='monitoring', clock=None, unknown_put=None, expire_after_read=False, expire_after_put=False, template=None, advance_404_seconds=0, unknown_read=None, expire_after_intent=False):
        for path in self.paths.state.glob('monitoring-reapply-*.json'):
            path.unlink()
        lifecycle.protected_json(self.paths.state / 'latest.json', {
            **{key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')},
            'monitoring': {'workspaceId': resource_ids(self.config)['workspace'],
                          'workspaceCustomerId': '11111111-1111-1111-1111-111111111111'}})
        test = self
        clock = clock or Clock()

        class Transport(ScriptedTransport):
            intent_before_put = False

            def request(self, method, path, body=None, headers=None):
                put_index = sum(call['method'] == 'PUT' for call in self.calls) + 1
                if method == 'PUT' and put_index == 2:
                    files = list(test.paths.state.glob('monitoring-reapply-*.json'))
                    test.assertEqual(len(files), 1)
                    record = json.loads(files[0].read_text())
                    test.assertEqual(record['status'], 'reapply-intent')
                    test.assertEqual(record['initialDeploymentState'], 'Failed')
                    test.assertEqual(record['additionalPutAttempts'], 1)
                    test.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
                    test.assertIs(body, self.calls[0]['body'])
                    test.assertEqual(path, self.calls[0]['path'])
                    self.intent_before_put = True
                if method == 'PUT' and put_index == unknown_put:
                    self.calls.append({'method': method, 'path': path, 'body': body})
                    raise lifecycle.DeploymentError('Synthetic unknown write: do not replay')
                if method == 'GET' and unknown_read is not None and unknown_read in path:
                    self.calls.append({'method': method, 'path': path, 'body': body})
                    raise lifecycle.DeploymentError('Synthetic unknown read')
                value = super().request(method, path, body, headers)
                if '/dataExports/' in path and value.get('statusCode') == 404:
                    clock.now += advance_404_seconds
                if expire_after_put and method == 'PUT' and put_index == 2:
                    clock.now = 7201
                if expire_after_read and '/dataExports/' in path:
                    clock.now = 7201  # in-flight GET can finish beyond the original deadline.
                return value

        transport = Transport(responses)
        record_intent = getattr(lifecycle, '_record_monitoring_reapply', None)
        def record_then_expire(*args):
            record = record_intent(*args)
            if expire_after_intent:
                clock.now = 7201
            return record
        with mock.patch.object(lifecycle, 'run', return_value=copy.deepcopy(self.template if template is None else template)) as compiler, \
             mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'discover_existing_network_security_groups', return_value=[]), \
             mock.patch.object(lifecycle, 'discover_existing_route_table_routes', return_value={}), \
             mock.patch.object(lifecycle, 'discover_existing_apim_hostnames', return_value={}), \
             mock.patch.object(lifecycle, 'discover_existing_portal_app', return_value=False), \
             mock.patch.object(lifecycle, '_record_monitoring_reapply', side_effect=record_then_expire, create=True), \
             mock.patch.object(lifecycle.time, 'monotonic', side_effect=clock.monotonic), \
             mock.patch.object(lifecycle.time, 'sleep', side_effect=clock.sleep), \
             contextlib.redirect_stdout(io.StringIO()):
            try:
                output = lifecycle.deploy_phase(self.config, phase, preserve_collection=True)
                error = None
            except lifecycle.DeploymentError as failure:
                output, error = None, failure
        self.assertEqual(compiler.call_count, 1)
        return output, error, transport, clock

    def test_exact_known_failure_reapplies_identical_body_once_with_durable_intent(self):
        output, error, transport, clock = self.exercise(self.responses())
        self.assertIsNone(error)
        self.assertEqual(output, {'fixture': 'ready'})
        self.assertTrue(transport.intent_before_put)
        self.assertEqual(len(transport.mutations()), 2)
        self.assertEqual(clock.sleeps, [])
        record = json.loads(next(self.paths.state.glob('monitoring-reapply-*.json')).read_text())
        self.assertEqual(record['status'], 'reapply-succeeded')
        self.assertEqual(record['exportReadinessProbes'], 1)
        self.assertNotIn('id', record)

    def test_only_exact_export_404_waits_until_desired_200(self):
        rows = self.responses()
        rows.insert(4, {'statusCode': 404, 'body': None})
        output, error, transport, clock = self.exercise(rows)
        self.assertIsNone(error)
        self.assertEqual(output, {'fixture': 'ready'})
        self.assertEqual(clock.sleeps, [15])
        self.assertEqual(len(transport.mutations()), 2)
        record = json.loads(next(self.paths.state.glob('monitoring-reapply-*.json')).read_text())
        self.assertEqual(record['exportReadinessProbes'], 2)

    def test_persistent_404_is_probe_bounded_and_does_not_reapply(self):
        rows = self.responses()[:4] + [{'statusCode': 404, 'body': None}] * 20
        _, error, transport, clock = self.exercise(rows)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 1)
        self.assertEqual(len([call for call in transport.calls if '/dataExports/' in call['path']]), 20)
        self.assertEqual(clock.sleeps, [15] * 19)
        self.assertEqual(list(self.paths.state.glob('monitoring-reapply-*.json')), [])

    def test_readiness_soft_deadline_stops_after_in_flight_404_crosses_it(self):
        rows = self.responses()[:4] + [{'statusCode': 404, 'body': None}] * 20
        _, error, transport, clock = self.exercise(rows, advance_404_seconds=100)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 1)
        self.assertEqual(len([call for call in transport.calls if '/dataExports/' in call['path']]), 3)
        self.assertEqual(clock.sleeps, [15, 15])
        self.assertGreater(clock.now, 300)  # soft bound never claims to interrupt the inherited transport.
        self.assertEqual(list(self.paths.state.glob('monitoring-reapply-*.json')), [])

    def test_unknown_guard_read_never_starts_another_write(self):
        for path in ('/operations?', '/workspaces/law-', '/dataExports/'):
            with self.subTest(path=path):
                _, error, transport, clock = self.exercise(self.responses(), unknown_read=path)
                self.assertIsNotNone(error)
                self.assertEqual(len(transport.mutations()), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertEqual(list(self.paths.state.glob('monitoring-reapply-*.json')), [])

    def test_unknown_or_noncanonical_initial_failure_never_reapplies(self):
        cases = ('foreign-child', 'multiple-parent-failures', 'wrong-type', 'wrong-id', 'wrong-code',
                 'other-404', 'multiple-child-failures', 'pending-child', 'paginated', 'empty-next-link', 'unparseable-message')
        for case in cases:
            with self.subTest(case=case):
                rows = self.responses()
                detail = rows[1]['body']['properties']['error']['details'][0]
                operation = rows[2]['body']['value'][1]['properties']
                if case == 'foreign-child': detail['target'] += '-other'
                elif case == 'multiple-parent-failures': rows[1]['body']['properties']['error']['details'].append(copy.deepcopy(detail))
                elif case == 'wrong-type': operation['targetResource']['resourceType'] = 'Microsoft.Insights/components'
                elif case == 'wrong-id': operation['targetResource']['id'] += '-other'
                elif case == 'wrong-code': operation['statusMessage']['error']['code'] = 'AuthorizationFailed'
                elif case == 'other-404': operation['statusMessage']['error']['message'] = '404 (Not Found): another resource'
                elif case == 'multiple-child-failures': rows[2]['body']['value'].append(copy.deepcopy(rows[2]['body']['value'][1]))
                elif case == 'pending-child': rows[2]['body']['value'][0]['properties']['provisioningState'] = 'Running'
                elif case == 'paginated': rows[2]['body']['nextLink'] = 'https://management.example.invalid/next'
                elif case == 'empty-next-link': rows[2]['body']['nextLink'] = ''
                elif case == 'unparseable-message': operation['statusMessage'] = 'not-json'
                _, error, transport, _ = self.exercise(rows)
                self.assertIsNotNone(error)
                self.assertEqual(len(transport.mutations()), 1)
                self.assertEqual(list(self.paths.state.glob('monitoring-reapply-*.json')), [])

    def test_workspace_or_current_export_mismatch_stops_without_wait_or_reapply(self):
        cases = ('law403', 'foreign-owner', 'customer-changed', 'export403', 'disabled', 'other-storage',
                 'missing-table', 'extra-table', 'duplicate-table')
        for case in cases:
            with self.subTest(case=case):
                rows = self.responses()
                workspace, export = rows[3]['body'], rows[4]['body']
                if case == 'law403': rows[3] = {'statusCode': 403, 'body': None}
                elif case == 'foreign-owner': workspace['tags']['environment'] = 'another-fixture'
                elif case == 'customer-changed': workspace['properties']['customerId'] = '22222222-2222-2222-2222-222222222222'
                elif case == 'export403': rows[4] = {'statusCode': 403, 'body': None}
                elif case == 'disabled': export['properties']['enable'] = False
                elif case == 'other-storage': export['properties']['destination']['resourceId'] += '-other'
                elif case == 'missing-table': export['properties']['tableNames'].pop()
                elif case == 'extra-table': export['properties']['tableNames'].append('AnotherTable')
                elif case == 'duplicate-table': export['properties']['tableNames'].append(self.tables[0])
                _, error, transport, clock = self.exercise(rows)
                self.assertIsNotNone(error)
                self.assertEqual(len(transport.mutations()), 1)
                self.assertEqual(clock.sleeps, [])

    def test_unknown_first_or_second_put_is_not_replayed(self):
        for index in (1, 2):
            with self.subTest(put=index):
                _, error, transport, _ = self.exercise(self.responses(), unknown_put=index)
                self.assertIsNotNone(error)
                self.assertEqual(len(transport.mutations()), index)
                if index == 2:
                    record = json.loads(next(self.paths.state.glob('monitoring-reapply-*.json')).read_text())
                    self.assertEqual(record['status'], 'reapply-request-failed-or-unconfirmed')

    def test_second_failure_does_not_issue_a_third_put(self):
        rows = self.responses()
        rows[6] = copy.deepcopy(rows[1])
        _, error, transport, _ = self.exercise(rows)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 2)
        self.assertEqual(json.loads(next(self.paths.state.glob('monitoring-reapply-*.json')).read_text())['status'], 'reapply-failed')

    def test_original_deadline_is_rechecked_after_readiness_and_not_renewed(self):
        _, error, transport, _ = self.exercise(self.responses(), expire_after_read=True)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 1)
        self.assertEqual(list(self.paths.state.glob('monitoring-reapply-*.json')), [])

    def test_deadline_crossed_during_durable_intent_prevents_second_put(self):
        _, error, transport, _ = self.exercise(self.responses(), expire_after_intent=True)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 1)
        files = list(self.paths.state.glob('monitoring-reapply-*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(record['status'], 'deadline-expired-no-reapply')
        self.assertEqual(record['additionalPutAttempts'], 0)

    def test_reapply_does_not_renew_the_original_phase_deadline(self):
        rows = self.responses()
        rows[6] = {'statusCode': 200, 'body': {'properties': {'provisioningState': 'Running'}}}
        _, error, transport, clock = self.exercise(rows, expire_after_put=True)
        self.assertIsNotNone(error)
        self.assertEqual(len(transport.mutations()), 2)
        self.assertEqual(clock.sleeps, [])

    def test_unused_table_catalog_does_not_authorize_reapply(self):
        template = copy.deepcopy(self.template)
        module = next(item['properties']['template'] for item in template['resources']
                      if isinstance(item.get('properties', {}).get('template', {}).get('variables', {}).get('exportTables'), list))
        export = next(item for item in module['resources']
                      if item.get('type', '').casefold() == 'microsoft.operationalinsights/workspaces/dataexports')
        export['properties']['tableNames'] = ['OtherTable']
        _, error, transport, _ = self.exercise(self.responses(), template=template)
        self.assertIsNotNone(error)
        self.assertEqual([call['method'] for call in transport.calls], ['PUT', 'GET'])

    def test_core_failure_has_no_monitoring_reapply_branch(self):
        _, error, transport, _ = self.exercise(self.responses(), phase='core')
        self.assertIsNotNone(error)
        self.assertEqual([call['method'] for call in transport.calls], ['PUT', 'GET'])
