"""合成ARMでSQL ID発見の世代照合・未知結果・一時資源回収を検証する。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import management_sql_discovery as discovery
from deployment.helpers.management_sql_identity import MARKER
from test_management_client_assets import fixture


class Transport:
    def __init__(self, config, inventory):
        self.calls, self.command = [], None
        self.vm = {'id': inventory['managementVmId'], 'type': 'Microsoft.Compute/virtualMachines',
                   'location': config['location'], 'identity': {'type': 'SystemAssigned',
                   'principalId': inventory['managementPrincipalId'], 'tenantId': inventory['azureTenantId']},
                   'properties': {'vmId': str(uuid.UUID(int=7)), 'provisioningState': 'Succeeded'}}
        self.output = {'schemaVersion': 1, 'status': 'verified', 'identity': {
            'sqlClientId': str(uuid.UUID(int=9)), 'principalId': inventory['managementPrincipalId'],
            'tenantId': inventory['azureTenantId'], 'managementVmId': inventory['managementVmId'],
            'vmId': self.vm['properties']['vmId']}}
        self.put_unknown, self.tampered, self.cleanup_pending, self.delayed_reads = False, False, False, 0

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        target = path.split('?', 1)[0]
        if target == self.vm['id']:
            return {'statusCode': 200, 'body': copy.deepcopy(self.vm)}
        if method == 'PUT':
            if self.put_unknown:
                raise OSError('Synthetic response loss')
            self.command = {'id': target, 'type': 'Microsoft.Compute/virtualMachines/runCommands', **copy.deepcopy(body)}
            self.command['properties']['instanceView'] = {'executionState': 'Succeeded', 'exitCode': 0,
                'output': MARKER + json.dumps(self.output)}
            if self.tampered:
                self.command['tags'] = {'purpose': 'other-synthetic-owner'}
            return {'statusCode': 202, 'body': {}}
        if method == 'DELETE':
            if not self.cleanup_pending:
                self.command = None
            return {'statusCode': 202, 'body': {}}
        if self.command is not None and self.delayed_reads:
            self.delayed_reads -= 1
            return {'statusCode': 404, 'body': {}}
        return {'statusCode': 200, 'body': copy.deepcopy(self.command)} if self.command else {'statusCode': 404, 'body': {}}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config, state, _, _ = fixture()
        self.inventory = state['outputs']['managementClientInventory']
        self.transport = Transport(self.config, self.inventory)
        self.receipts, self.ticks = [], 0

    def clock(self):
        self.ticks += 1
        return self.ticks

    def execute(self, **options):
        return discovery.discover_management_sql_identity(self.config, self.inventory, self.transport,
            record=lambda value: self.receipts.append(copy.deepcopy(value)), sleep=lambda _: None,
            monotonic=self.clock, timeout=10, **options)

    def test_verified_current_vm_id_returns_only_after_exact_command_cleanup(self):
        self.transport.delayed_reads = 2
        self.assertEqual(self.execute(), str(uuid.UUID(int=9)))
        self.assertTrue(self.receipts[-1]['temporaryCommandRemoved'])
        self.assertFalse(self.receipts[-1]['runtimeSqlLoginVerified'])
        self.assertEqual([m for m, _ in self.transport.calls if m != 'GET'], ['PUT', 'DELETE'])

    def test_other_vm_or_tenant_stops_before_any_arm_request(self):
        self.config['azureTenantId'] = str(uuid.UUID(int=8))
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.transport.calls, [])

    def test_existing_unknown_receipt_blocks_another_command_before_any_request(self):
        with self.assertRaisesRegex(ValueError, 'previous'):
            self.execute(prior_receipt={'temporaryCommandRemoved': False})
        self.assertEqual(self.transport.calls, [])

    def test_lost_put_and_absent_resource_retains_unknown_and_does_not_delete(self):
        self.transport.put_unknown = True
        with self.assertRaisesRegex(ValueError, 'unknown'): self.execute()
        self.assertEqual(self.receipts[-1]['status'], 'cleanup-unknown')
        self.assertFalse(self.receipts[-1]['temporaryCommandRemoved'])
        self.assertEqual([m for m, _ in self.transport.calls if m != 'GET'], ['PUT'])

    def test_foreign_command_is_never_adopted_or_deleted(self):
        self.transport.tampered = True
        with self.assertRaisesRegex(ValueError, 'ownership'): self.execute()
        self.assertFalse(self.receipts[-1]['temporaryCommandRemoved'])
        self.assertNotIn('DELETE', [m for m, _ in self.transport.calls])

    def test_duplicate_or_other_vm_probe_output_is_rejected_and_owned_command_removed(self):
        for field, value in (('principalId', str(uuid.UUID(int=8))), ('vmId', str(uuid.UUID(int=8)))):
            with self.subTest(field=field):
                self.setUp()
                self.transport.output['identity'][field] = value
                with self.assertRaises(ValueError): self.execute()
                self.assertTrue(self.receipts[-1]['temporaryCommandRemoved'])
                self.assertNotIn('sqlClientId', self.receipts[-1])

    def test_pending_cleanup_prevents_returning_identity(self):
        self.transport.cleanup_pending = True
        with self.assertRaisesRegex(ValueError, 'pending'): self.execute()
        self.assertFalse(self.receipts[-1]['temporaryCommandRemoved'])

    def test_vm_generation_change_after_probe_is_not_accepted(self):
        original = self.transport.request
        reads = 0
        def request(method, path, body=None):
            nonlocal reads
            if path.split('?', 1)[0] == self.transport.vm['id']:
                reads += 1
                if reads == 2: self.transport.vm['properties']['vmId'] = str(uuid.UUID(int=8))
            return original(method, path, body)
        with mock.patch.object(self.transport, 'request', side_effect=request):
            with self.assertRaises(ValueError): self.execute()
        self.assertTrue(self.receipts[-1]['temporaryCommandRemoved'])


if __name__ == '__main__': unittest.main()
