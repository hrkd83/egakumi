"""Offline SQL-contract checks; real row-lock behavior belongs to test_session_sql."""
import unittest

from credential_broker.store import StoreConflict, StoreUnavailable
from _store_support import OWNER, TENANT, TX, fixture


NOW = 10000000
RETENTION = 5184000
VERSION = b'\x00\x00\x00\x00\x00\x00\x00\x01'
CLAIM_VERSION = b'\x00\x00\x00\x00\x00\x00\x00\x02'


def connection(**changes):
    return {'ProfileName': 'graph', 'TenantId': TENANT, 'ObjectId': OWNER,
            'ProviderName': 'graph-a', 'ConnectionId': 'member', 'State': 'ready',
            'LastUsedAt': NOW-RETENTION, 'LastCheckedAt': None, 'LeaseOwner': None,
            'LeaseUntil': 0, 'SecurityEpoch': 0, 'Version': VERSION, **changes}


def mapping(**changes):
    return {'profileName': 'graph', 'tenantId': TENANT, 'objectId': OWNER,
            'providerName': 'graph-a', 'connectionId': 'member', 'state': 'ready',
            'version': VERSION, **changes}


def claim(**changes):
    return mapping(state='deleting', version=CLAIM_VERSION, leaseOwner='worker-a',
                   leaseUntil=NOW+300, **changes)


class CleanupStoreTests(unittest.TestCase):
    def test_candidates_are_bounded_pool_scoped_sql_clock_and_fairly_ordered(self):
        store, cursor = fixture([('DATEDIFF_BIG', {'Now': NOW}), ('SELECT TOP', [connection()])])
        rows = store.cleanup_candidates({('graph', 'graph-a')}, batch_size=2)
        self.assertEqual(len(rows), 1)
        sql, params = cursor.calls[-1]
        self.assertEqual(params, (2, 'graph', 'graph-a', NOW-RETENTION, NOW, NOW))
        for required in ("State='deleting' OR c.LastUsedAt<=?", 'c.LeaseUntil<=?',
                         't.RequiredTenantId=c.TenantId', 't.RequiredObjectId=c.ObjectId',
                         "Phase NOT IN ('complete','failed')", 't.ExpiresAt>?',
                         'ORDER BY COALESCE(c.LastCheckedAt,0),c.LastUsedAt'):
            self.assertIn(required, sql)
        self.assertFalse(any('broker.Sessions' in sql for sql, _ in cursor.calls))
        empty, empty_cursor = fixture([])
        self.assertEqual(empty.cleanup_candidates(set()), [])
        self.assertEqual(empty_cursor.calls, [])
        for kwargs in ({'batch_size': 0}, {'batch_size': 1001}, {'idle_seconds': 0}):
            with self.assertRaises(ValueError):
                empty.cleanup_candidates({('graph', 'graph-a')}, **kwargs)

    def test_claim_accepts_exact_idle_boundary_and_revokes_without_active_session_veto(self):
        updated = connection(State='deleting', LeaseOwner='worker-a', LeaseUntil=NOW+300,
                             LastCheckedAt=NOW, SecurityEpoch=1, Version=CLAIM_VERSION)
        store, cursor = fixture([
            ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', connection()),
            ('SELECT ExpiresAt', []), ('DATEDIFF_BIG', {'Now': NOW}),
            ('UPDATE broker.Connections', updated), ('UPDATE broker.Sessions', 17),
            ('UPDATE broker.Transactions', 1), ('DELETE r', 1), ('COMMIT', None)])
        result = store.claim_cleanup(mapping(), 'worker-a')
        self.assertEqual((result['version'], result['lastCheckedAt'], result['leaseUntil']),
                         (CLAIM_VERSION, NOW, NOW+300))
        query, params = cursor.calls[1]
        self.assertIn('ProviderName=? AND ConnectionId=? AND Version=?', query)
        self.assertEqual(params, ('graph', TENANT, OWNER, 'graph-a', 'member', VERSION))
        sql = [sql for sql, _ in cursor.calls]
        self.assertIn('RequiredTenantId=?', sql[2])
        self.assertIn('SecurityEpoch=SecurityEpoch+1', sql[4])
        self.assertIn('LastCheckedAt=? OUTPUT INSERTED.*', sql[4])
        self.assertFalse(any('SELECT' in query and 'broker.Sessions' in query for query in sql))
        self.assertFalse(any('broker.Pools' in query for query in sql))

    def test_claim_rechecks_recent_usage_lease_and_both_identity_authentications(self):
        for row, authentications in [
                (connection(LastUsedAt=NOW-RETENTION+1), []),
                (connection(LeaseUntil=NOW+1), []),
                (connection(), [{'ExpiresAt': NOW+1}])]:
            store, cursor = fixture([
                ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', row),
                ('SELECT ExpiresAt', authentications), ('DATEDIFF_BIG', {'Now': NOW}), ('ROLLBACK', None)])
            with self.subTest(row=row), self.assertRaises(StoreConflict):
                store.claim_cleanup(mapping(), 'worker-a')
            self.assertFalse(any(sql.startswith(('UPDATE', 'DELETE')) for sql, _ in cursor.calls))

    def test_changed_candidate_stops_before_authentication_or_writes(self):
        store, cursor = fixture([('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', []), ('ROLLBACK', None)])
        with self.assertRaises(StoreConflict):
            store.claim_cleanup(mapping(), 'worker-a')
        self.assertEqual(len(cursor.calls), 3)

    def test_expired_deleting_lease_can_be_reclaimed_despite_recent_activity_clock(self):
        row = connection(State='deleting', LastUsedAt=NOW, LeaseUntil=NOW,
                         LeaseOwner='previous-worker')
        updated = connection(State='deleting', LastUsedAt=NOW, LastCheckedAt=NOW,
                             LeaseUntil=NOW+300, LeaseOwner='worker-a', Version=CLAIM_VERSION)
        store, _ = fixture([
            ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', row),
            ('SELECT ExpiresAt', [{'ExpiresAt': NOW}]), ('DATEDIFF_BIG', {'Now': NOW}),
            ('UPDATE broker.Connections', updated), ('UPDATE broker.Sessions', 0),
            ('UPDATE broker.Transactions', 1), ('DELETE r', 0), ('COMMIT', None)])
        self.assertEqual(store.claim_cleanup(mapping(), 'worker-a')['leaseOwner'], 'worker-a')

    def test_validate_claim_checks_exact_identity_owner_version_and_sql_lease_clock(self):
        for response, expected in [({'Version': CLAIM_VERSION}, True), ([], False)]:
            store, cursor = fixture([('SELECT Version', response)])
            self.assertIs(store.validate_cleanup_claim(claim()), expected)
            sql, params = cursor.calls[0]
            self.assertIn("Version=? AND State='deleting' AND LeaseOwner=? AND LeaseUntil>DATEDIFF_BIG", sql)
            self.assertEqual(params, ('graph', TENANT, OWNER, 'graph-a', 'member', CLAIM_VERSION, 'worker-a'))

    def test_finish_claim_releases_capacity_once_and_rejects_expiry_or_replacement(self):
        row = connection(State='deleting', LeaseOwner='worker-a', LeaseUntil=NOW+300, Version=CLAIM_VERSION)
        store, cursor = fixture([
            ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', row),
            ('DATEDIFF_BIG', {'Now': NOW}), ('DELETE broker.Connections', 1),
            ('UPDATE broker.Pools', 1), ('COMMIT', None)])
        self.assertTrue(store.finish_cleanup(claim()))
        self.assertIn('Version=?', cursor.calls[3][0])
        self.assertIn('LeaseOwner=?', cursor.calls[3][0])
        self.assertEqual(cursor.calls[4][1], ('graph', 'graph-a'))
        for stale in [[], connection(State='deleting', LeaseUntil=NOW, Version=CLAIM_VERSION)]:
            store, cursor = fixture([
                ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', stale),
                ('DATEDIFF_BIG', {'Now': NOW}), ('ROLLBACK', None)])
            with self.assertRaises(StoreConflict):
                store.finish_cleanup(claim())
            self.assertFalse(any(sql.startswith(('DELETE', 'UPDATE')) for sql, _ in cursor.calls))

    def test_failed_counter_update_rolls_back_the_mapping_delete(self):
        store, cursor = fixture([
            ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', connection(LeaseUntil=NOW+300)),
            ('DATEDIFF_BIG', {'Now': NOW}), ('DELETE broker.Connections', 1),
            ('UPDATE broker.Pools', 0), ('ROLLBACK', None)])
        with self.assertRaises(StoreConflict):
            store.finish_cleanup(claim())
        self.assertIn('ROLLBACK', cursor.calls[-1][0])

    def test_deadlock_rolls_back_before_any_confirmed_claim(self):
        store, cursor = fixture([
            ('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', connection()),
            ('SELECT ExpiresAt', RuntimeError('1205 private-driver-detail')), ('ROLLBACK', None)])
        with self.assertRaises(StoreUnavailable) as error:
            store.claim_cleanup(mapping(), 'worker-a')
        self.assertNotIn('private-driver', str(error.exception))
        self.assertIn('ROLLBACK', cursor.calls[-1][0])

    def test_manual_disconnect_requires_the_exact_post_update_version(self):
        value = mapping(connectionVersion=CLAIM_VERSION)
        store, cursor = fixture([('SELECT Version', {'Version': CLAIM_VERSION})])
        self.assertTrue(store.validate_disconnect(value))
        self.assertEqual(cursor.calls[0][1][-1], CLAIM_VERSION)
        self.assertNotIn('LeaseOwner', cursor.calls[0][0])
        store, cursor = fixture([('BEGIN TRANSACTION', None), ('SELECT * FROM broker.Connections', []), ('ROLLBACK', None)])
        with self.assertRaises(StoreConflict):
            store.finish_disconnect('graph', TENANT, OWNER, 'graph-a', 'member', expected_version=CLAIM_VERSION)
        self.assertFalse(any(sql.startswith(('UPDATE', 'DELETE')) for sql, _ in cursor.calls))

    def test_deleting_allocator_error_is_a_sanitized_conflict(self):
        store, _ = fixture([('EXEC broker.AllocateConnection', RuntimeError('51002 private SQL values'))])
        with self.assertRaises(StoreConflict) as error:
            store.allocate_connection('graph', TENANT, OWNER, 'member@example.test', TX)
        self.assertNotIn('private', str(error.exception))
