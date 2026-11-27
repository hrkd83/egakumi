"""専用ローカルSQLで、期限・失効・更新競合を実トランザクションで検証する。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from credential_broker.store import (SqlStore, StoreCapacity, StoreConflict, StoreNotAuthorized,
                                     StoreRotationConflict, StoreRotationExpired, StoreUnavailable)

TENANT = '11111111-1111-4111-8111-111111111111'
OWNER = '22222222-2222-4222-8222-222222222222'
PROFILE = 'graph-delegated'
SUB = 'test-subscription'
POLICY = dict(idleTimeoutSeconds=604800, absoluteLifetimeSeconds=2592000,
              rotationIntervalSeconds=86400, rotationGraceSeconds=300,
              rotationHandoffSeconds=600, rotationCandidateTtlSeconds=600)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@unittest.skipUnless(os.environ.get('BROKER_SQL_TEST_CONNECTION'), 'Dedicated local SQL connection required')
class SessionSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pyodbc
        cls.connect = staticmethod(lambda: pyodbc.connect(os.environ['BROKER_SQL_TEST_CONNECTION'], autocommit=True, timeout=10))
        with cls.connect() as connection:
            database = connection.cursor().execute('SELECT DB_NAME()').fetchone()[0]
            if not database.startswith('egakumi_broker_test_'):
                raise RuntimeError('Only a dedicated temporary local test database is allowed')
        cls.store = SqlStore(cls.connect)
        cls.store.ensure_ready()

    def query(self, statement, *values):
        with self.connect() as connection:
            cursor = connection.cursor().execute(statement, *values)
            return cursor.fetchall() if cursor.description else None

    def now(self):
        return int(self.query("SELECT DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME())")[0][0])

    def setUp(self):
        self.query('DELETE broker.SessionRotationCandidates; DELETE broker.Sessions; DELETE broker.Transactions; DELETE broker.Connections; DELETE broker.SubjectSecurity; DELETE broker.Pools;')
        now = self.now()
        self.query("INSERT broker.Pools(ProfileName,ProviderName,Capacity,Allocated) VALUES(?,?,10,1)", PROFILE, PROFILE + '-a')
        self.query("INSERT broker.Connections(ProfileName,TenantId,ObjectId,UserPrincipalName,ProviderName,ConnectionId,State,CreatedAt,LastUsedAt) VALUES(?,?,?,?,?,?,'ready',?,?)", PROFILE, TENANT, OWNER, 'member@example.test', PROFILE + '-a', 'member', now, now)
        self.query('INSERT broker.SubjectSecurity(TenantId,ObjectId,RevocationEpoch,UpdatedAt) VALUES(?,?,0,?)', TENANT, OWNER, now)
        self.tx, self.sid, self.secret = (secrets.token_hex(32) for _ in range(3))
        self.poll = secrets.token_hex(32)
        self.store.create_transaction(self.tx, PROFILE, SUB, digest(self.poll), 'c' * 64, now + 600,
            pending_session_id=self.sid, pending_secret_hash=digest(self.secret))

    def activate(self):
        self.query("UPDATE broker.Transactions SET Phase='verifying',TenantId=?,ObjectId=?,ProviderName=?,ConnectionId='member',SubjectEpochAtVerification=0,ConnectionEpochAtVerification=0,TicketHash=? WHERE TransactionId=?", TENANT, OWNER, PROFILE + '-a', 'd' * 64, self.tx)
        return self.store.complete_transaction(self.tx, 'd' * 64, OWNER, policy=POLICY)

    def age_for_rotation(self):
        now = self.now()
        self.query('UPDATE broker.Sessions SET AuthenticatedAt=?,LastBusinessUsedAt=?,IdleExpiresAt=?,ExpiresAt=?,AbsoluteExpiresAt=?,NextRotationAt=? WHERE SessionId=?', now-90000, now-10, now+100000, now+100000, now+200000, now-1, self.sid)

    def test_parallel_first_reservations_for_same_owner_share_one_pool_slot(self):
        """同一本人の初回並行要求は実SQL手続きでも1接続・1予約に収束します。"""
        self.query('DELETE broker.Connections; DELETE broker.Pools;')
        self.query('INSERT broker.Pools(ProfileName,ProviderName,Capacity,Allocated) VALUES(?,?,1,0)', PROFILE, PROFILE+'-a')
        barrier=threading.Barrier(2)
        def reserve(index):
            barrier.wait()
            return self.store.allocate_connection(PROFILE,TENANT,OWNER,'member@example.test','parallel-'+str(index))
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows=list(executor.map(reserve,range(2)))
        self.assertEqual(1,len({(row['providerName'],row['connectionId']) for row in rows}))
        self.assertEqual(1,self.query('SELECT COUNT(*) FROM broker.Connections')[0][0])
        self.assertEqual(1,self.query('SELECT SUM(Allocated) FROM broker.Pools')[0][0])

    def test_remaining_capacity_is_reserved_once_and_full_pool_keeps_existing_owner(self):
        """残り1枠への別本人の並行要求でも過剰予約せず、満杯後も既存対応は取得できます。"""
        self.query('DELETE broker.Connections; DELETE broker.Pools;')
        for suffix in ('a','b'):
            self.query('INSERT broker.Pools(ProfileName,ProviderName,Capacity,Allocated) VALUES(?,?,1,0)',PROFILE,PROFILE+'-'+suffix)
        existing=self.store.allocate_connection(PROFILE,TENANT,OWNER,'member@example.test','first-owner')
        owners=('33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444')
        barrier=threading.Barrier(2)
        def reserve(index):
            barrier.wait()
            try:return self.store.allocate_connection(PROFILE,TENANT,owners[index],'member'+str(index)+'@example.test','remaining-'+str(index))
            except StoreCapacity:return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows=list(executor.map(reserve,range(2)))
        self.assertEqual(1,sum(row is not None for row in rows))
        self.assertEqual([(1,1),(1,1)],[tuple(row)for row in self.query('SELECT Capacity,Allocated FROM broker.Pools ORDER BY ProviderName')])
        again=self.store.allocate_connection(PROFILE,TENANT,OWNER,'renamed@example.test','first-owner')
        self.assertEqual(existing['connectionId'],again['connectionId'])
        self.assertEqual(existing['providerName'],again['providerName'])

    def test_upn_rename_preserves_mapping_and_same_prefix_other_owner_does_not_collide(self):
        """UPN変更は既存名を変更せず、同じ前半や長いUPNの別本人とは混同しません。"""
        self.query('DELETE broker.Connections; DELETE broker.Pools;')
        self.query('INSERT broker.Pools(ProfileName,ProviderName,Capacity,Allocated) VALUES(?,?,3,0)',PROFILE,PROFILE+'-a')
        first=self.store.allocate_connection(PROFILE,TENANT,OWNER,'member@example.test','rename-owner')
        renamed=self.store.allocate_connection(PROFILE,TENANT,OWNER,'changed@example.test','rename-owner')
        other=self.store.allocate_connection(PROFILE,TENANT,'33333333-3333-4333-8333-333333333333','member@another.test','other-owner')
        long=self.store.allocate_connection(PROFILE,TENANT,'44444444-4444-4444-8444-444444444444','x'*250+'@example.test','long-owner')
        self.assertEqual(first['connectionId'],renamed['connectionId'])
        self.assertNotEqual(first['connectionId'],other['connectionId'])
        self.assertLessEqual(len(long['connectionId']),80)
        self.assertEqual(3,self.query('SELECT Allocated FROM broker.Pools')[0][0])

    def test_reallocation_after_confirmed_release_uses_a_new_connection_generation(self):
        """同本人・同providerの再予約でも、削除前のCM Resource IDへ戻しません。"""
        self.query('DELETE broker.Connections; UPDATE broker.Pools SET Allocated=0;')
        first = self.store.allocate_connection(PROFILE, TENANT, OWNER, 'member@example.test', 'old-generation')
        self.query("UPDATE broker.Connections SET State='deleting',LeaseUntil=0")
        version = self.query('SELECT Version FROM broker.Connections')[0][0]
        # This SQL-only contract starts where its caller has confirmed CM404.
        self.store.finish_disconnect(PROFILE, TENANT, OWNER, first['providerName'],
                                     first['connectionId'], expected_version=version)
        second = self.store.allocate_connection(PROFILE, TENANT, OWNER, 'member@example.test', 'new-generation')
        self.assertEqual(first['providerName'], second['providerName'])
        self.assertNotEqual(first['connectionId'], second['connectionId'])
        self.assertRegex(second['connectionId'], r'^member-[a-f0-9]{32}$')
        self.assertEqual(1, self.query('SELECT Allocated FROM broker.Pools')[0][0])

    def test_runtime_policy_expansion_does_not_extend_an_authenticated_absolute_deadline(self):
        """認証後に最長期限の構成を拡張しても、実SQLへ保存済みの期限を延ばしません。"""
        from dataclasses import replace
        from credential_broker.service import BrokerService
        from _broker_support import settings
        self.activate()
        original=self.store.resolve_session(self.sid,digest(self.secret),SUB,PROFILE)
        changed=replace(settings(),absolute_lifetime_seconds=POLICY['absoluteLifetimeSeconds']*2,
            idle_timeout_seconds=POLICY['idleTimeoutSeconds']*2)
        service=BrokerService(changed,self.store,None,None)
        used=service.use('cs2.'+self.sid+'.'+self.secret,SUB,PROFILE,OWNER,'graph-profile-v1','get-me')
        self.assertTrue(used['allowed'])
        self.assertEqual(original['absoluteExpiresAt'],used['absoluteExpiresAt'])
        saved=self.store.resolve_session(self.sid,digest(self.secret),SUB,PROFILE)
        self.assertEqual(POLICY['absoluteLifetimeSeconds'],saved['absoluteExpiresAt']-saved['authenticatedAt'])
        self.assertEqual(POLICY['idleTimeoutSeconds'],saved['idleTimeoutSeconds'])

    def prepare(self):
        candidate_id, candidate = secrets.token_hex(32), secrets.token_hex(32)
        result = self.store.finalize_business_use(self.sid, digest(self.secret), SUB, PROFILE, OWNER,
            candidate_id=candidate_id, candidate_hash=digest(candidate))
        return result, candidate_id, candidate

    def test_activation_clock_is_completion_and_transaction_remains_short(self):
        self.query('UPDATE broker.Transactions SET CreatedAt=CreatedAt-250 WHERE TransactionId=?', self.tx)
        before = self.now()
        self.activate()
        record = self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE)
        self.assertGreaterEqual(record['authenticatedAt'], before)
        self.assertEqual(record['absoluteExpiresAt'] - record['authenticatedAt'], 2592000)
        self.assertEqual(record['idleExpiresAt'] - record['authenticatedAt'], 604800)
        metadata = self.store.status(self.tx, digest(self.poll), SUB)
        self.assertEqual(metadata['sessionId'], self.sid)
        self.assertIsNone(metadata['pendingSessionSecretHash'])
        self.assertLess(self.query('SELECT ExpiresAt FROM broker.Transactions WHERE TransactionId=?', self.tx)[0][0], record['idleExpiresAt'])
        with self.assertRaises(StoreNotAuthorized):
            self.store.resolve_session(self.tx, digest(self.poll), SUB, PROFILE)

    def test_real_service_status_matches_client_without_returning_session_secret(self):
        """手書きの応答fixtureに頼らず、SQL→Service→クライアントの契約を照合する。"""
        import sys
        from credential_broker.service import BrokerService
        from _broker_support import settings
        with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[4] / 'scripts'), *sys.path]):
            from credential_client import CredentialClient
            from credential_session_store import MemorySessionStore
        service = BrokerService(settings(), self.store, None, None)
        self.activate()
        response = service.status(self.tx, self.poll, SUB)
        self.assertNotIn('connectionSession', response)
        self.assertNotIn('pendingSessionSecretHash', response)
        class Reply:
            status, headers = 200, {}
            def read(self, limit): return json.dumps(response).encode()
            def __enter__(self): return self
            def __exit__(self, *args): pass
        token = 'cs2.' + self.sid + '.' + self.secret
        state = MemorySessionStore(dict(revision=0, protocolVersion=2, status='pending', generation=0,
            sessionId=self.sid, connectionSession=token, authentication=dict(transactionId=self.tx)))
        client = CredentialClient('https://gateway.example.test', 'test-subscription-key', store=state,
            expected_subject=dict(tenantId=TENANT, ownerId=OWNER), subscription_id=SUB,
            opener=SimpleNamespace(open=lambda *args, **kwargs: Reply()))
        self.assertEqual(client.status(self.tx, self.poll)['status'], 'complete')
        self.assertEqual(state.read()['connectionSession'], token)
        self.assertNotIn('authentication', state.read())

    def test_validation_and_polling_do_not_extend_idle(self):
        self.activate()
        before = self.query('SELECT LastBusinessUsedAt,IdleExpiresAt FROM broker.Sessions')[0]
        self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE)
        self.store.status(self.tx, digest(self.poll), SUB)
        self.assertEqual(before, self.query('SELECT LastBusinessUsedAt,IdleExpiresAt FROM broker.Sessions')[0])

    def test_expired_session_never_revives_or_prepares(self):
        self.activate()
        now = self.now()
        self.query('UPDATE broker.Sessions SET IdleExpiresAt=?,ExpiresAt=?', now, now)
        result, _, _ = self.prepare()
        self.assertFalse(result['allowed'])
        self.assertIsNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
        self.assertEqual(self.query('SELECT COUNT(*) FROM broker.SessionRotationCandidates')[0][0], 0)
        self.assertEqual(self.query('SELECT IdleExpiresAt FROM broker.Sessions')[0][0], now)

    def test_expiry_is_checked_after_waiting_for_the_sql_row_lock(self):
        self.activate()
        expiry = self.now() + 2
        self.query('UPDATE broker.Sessions SET IdleExpiresAt=?,ExpiresAt=?', expiry, expiry)
        locked = self.connect()
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            cursor = locked.cursor()
            cursor.execute('BEGIN TRANSACTION; SELECT * FROM broker.Sessions WITH(XLOCK,HOLDLOCK,INDEX(1)) WHERE SessionId=?', self.sid).fetchall()
            started = threading.Event()
            def waiting_business_use():
                started.set()
                return self.prepare()[0]
            future = executor.submit(waiting_business_use)
            self.assertTrue(started.wait(2))
            while self.now() < expiry:
                time.sleep(0.05)
            cursor.execute('COMMIT TRANSACTION;')
            self.assertFalse(future.result(timeout=10)['allowed'])
            self.assertEqual(self.query('SELECT IdleExpiresAt FROM broker.Sessions')[0][0], expiry)
        finally:
            locked.close()
            executor.shutdown(wait=True)

    def test_authentication_cannot_complete_after_waiting_beyond_transaction_expiry(self):
        self.query("UPDATE broker.Transactions SET Phase='verifying',TenantId=?,ObjectId=?,ProviderName=?,ConnectionId='member',SubjectEpochAtVerification=0,ConnectionEpochAtVerification=0,TicketHash=? WHERE TransactionId=?", TENANT, OWNER, PROFILE+'-a', 'd'*64, self.tx)
        expiry = self.now() + 2
        self.query('UPDATE broker.Transactions SET ExpiresAt=?', expiry)
        locked = self.connect()
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            cursor = locked.cursor()
            cursor.execute('BEGIN TRANSACTION; SELECT * FROM broker.Connections WITH(XLOCK,HOLDLOCK,INDEX(1))').fetchall()
            future = executor.submit(self.store.complete_transaction, self.tx, 'd'*64, OWNER, policy=POLICY)
            while self.now() < expiry:
                time.sleep(0.05)
            cursor.execute('COMMIT TRANSACTION;')
            with self.assertRaises(StoreConflict):
                future.result(timeout=10)
            self.assertEqual(self.query('SELECT COUNT(*) FROM broker.Sessions')[0][0], 0)
        finally:
            locked.close()
            executor.shutdown(wait=True)

    def test_absolute_deadline_caps_business_activity(self):
        self.activate()
        now = self.now()
        self.query('UPDATE broker.Sessions SET IdleExpiresAt=?,ExpiresAt=?,AbsoluteExpiresAt=?', now+5, now+5, now+5)
        result, _, _ = self.prepare()
        self.assertTrue(result['allowed'])
        self.assertEqual(result['idleExpiresAt'], now+5)
        self.query('UPDATE broker.Sessions SET IdleExpiresAt=?,ExpiresAt=?,AbsoluteExpiresAt=?', now, now, now)
        self.assertFalse(self.prepare()[0]['allowed'])

    def test_prepare_loss_commit_loss_and_fixed_previous_grace(self):
        self.activate(); self.age_for_rotation()
        first, _, lost = self.prepare()
        self.assertTrue(first['allowed'])
        self.assertIsNotNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
        with self.assertRaises(StoreNotAuthorized):
            self.store.resolve_session(self.sid, digest(lost), SUB, PROFILE)
        prepared, rotation, candidate = self.prepare()
        absolute = prepared['absoluteExpiresAt']
        committed = self.store.commit_rotation(self.sid, digest(self.secret), SUB, rotation, digest(candidate), 1)
        self.assertEqual(committed['generation'], 2)
        self.assertEqual(committed['absoluteExpiresAt'], absolute)
        self.assertIsNotNone(self.store.resolve_session(self.sid, digest(candidate), SUB, PROFILE))
        self.query('UPDATE broker.Sessions SET PreviousValidUntil=?', self.now()+5)
        before = self.query('SELECT LastBusinessUsedAt,IdleExpiresAt,AbsoluteExpiresAt,NextRotationAt,PreviousValidUntil FROM broker.Sessions')[0]
        self.store.commit_rotation(self.sid, digest(self.secret), SUB, rotation, digest(candidate), 1)
        self.assertEqual(before, self.query('SELECT LastBusinessUsedAt,IdleExpiresAt,AbsoluteExpiresAt,NextRotationAt,PreviousValidUntil FROM broker.Sessions')[0])
        self.query('UPDATE broker.Sessions SET PreviousValidUntil=?', self.now())
        with self.assertRaises(StoreNotAuthorized):
            self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE)
        recovered = self.store.commit_rotation(self.sid, digest(candidate), SUB, rotation, digest(candidate), 1)
        self.assertEqual(recovered['generation'], 2)
        self.assertEqual(recovered['absoluteExpiresAt'], absolute)

    def test_expired_candidate_preserves_current_and_does_not_extend_lifetimes(self):
        self.activate(); self.age_for_rotation()
        _, rotation, candidate = self.prepare()
        self.query('UPDATE broker.SessionRotationCandidates SET ExpiresAt=?', self.now())
        before = self.query('SELECT IdleExpiresAt,AbsoluteExpiresAt,LastBusinessUsedAt FROM broker.Sessions')[0]
        with self.assertRaises(StoreRotationExpired):
            self.store.commit_rotation(self.sid, digest(self.secret), SUB, rotation, digest(candidate), 1)
        self.assertEqual(before, self.query('SELECT IdleExpiresAt,AbsoluteExpiresAt,LastBusinessUsedAt FROM broker.Sessions')[0])
        self.assertIsNotNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
        fresh, _, _ = self.prepare()
        self.assertIn('rotationCandidate', fresh)

    def test_only_one_parallel_rotation_wins(self):
        self.activate(); self.age_for_rotation()
        candidates = [self.prepare()[1:] for _ in range(2)]
        barrier = threading.Barrier(2)
        def commit(pair):
            barrier.wait()
            try:
                return self.store.commit_rotation(self.sid, digest(self.secret), SUB, pair[0], digest(pair[1]), 1)['generation']
            except StoreRotationConflict:
                return 'superseded'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(commit, candidates))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count('superseded'), 1)

    def test_handoff_requires_update_before_dispatch_without_idle_extension(self):
        self.activate(); self.age_for_rotation(); self.prepare()
        self.query('UPDATE broker.Sessions SET RotationDeadlineAt=?', self.now())
        before = self.query('SELECT IdleExpiresAt,LastBusinessUsedAt FROM broker.Sessions')[0]
        result, rotation, candidate = self.prepare()
        self.assertEqual(result['errorCode'], 'SessionUpdateRequired')
        self.assertFalse(result['allowed'])
        self.assertEqual(before, self.query('SELECT IdleExpiresAt,LastBusinessUsedAt FROM broker.Sessions')[0])
        self.store.commit_rotation(self.sid, digest(self.secret), SUB, rotation, digest(candidate), 1)
        self.assertFalse(self.store.resolve_session(self.sid, digest(candidate), SUB, PROFILE)['updateRequired'])

    def test_logout_all_rejects_pending_rotation_and_late_business_finalize(self):
        self.activate(); self.age_for_rotation()
        _, rotation, candidate = self.prepare()
        self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE)
        self.store.revoke_subject_sessions(self.sid, digest(self.secret), SUB)
        self.assertFalse(self.prepare()[0]['allowed'])
        with self.assertRaises(StoreNotAuthorized):
            self.store.commit_rotation(self.sid, digest(self.secret), SUB, rotation, digest(candidate), 1)

    def test_disconnect_holds_capacity_until_explicit_confirmation(self):
        self.activate()
        pending_tx = secrets.token_hex(32)
        self.store.create_transaction(pending_tx, PROFILE, SUB, 'a'*64, 'b'*64, self.now()+600,
            pending_session_id='e'*64, pending_secret_hash='f'*64)
        self.query("UPDATE broker.Transactions SET Phase='verifying',TenantId=?,ObjectId=?,ProviderName=?,ConnectionId='member',SubjectEpochAtVerification=0,TicketHash=? WHERE TransactionId=?", TENANT, OWNER, PROFILE+'-a', 'd'*64, pending_tx)
        mapping = self.store.begin_disconnect(self.sid, digest(self.secret), SUB)
        self.assertIsNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 1)
        self.store.finish_disconnect(PROFILE, TENANT, OWNER, PROFILE+'-a', 'member',
                                     expected_version=mapping['connectionVersion'])
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 0)
        with self.assertRaises(StoreConflict):
            self.store.complete_transaction(pending_tx, 'd'*64, OWNER, policy=POLICY)

    def cleanup_candidate(self):
        return self.store.cleanup_candidates({(PROFILE, PROFILE+'-a')})[0]

    def age_connection(self):
        self.query('UPDATE broker.Connections SET LastUsedAt=?', self.now()-5184000-1)

    def test_allocation_and_authentication_do_not_extend_connection_business_clock(self):
        used = self.now()-1000
        self.query('UPDATE broker.Connections SET LastUsedAt=?', used)
        self.store.allocate_connection(PROFILE, TENANT, OWNER, 'member@example.test', 'auth-owner')
        self.assertEqual(self.query('SELECT LastUsedAt FROM broker.Connections')[0][0], used)
        self.activate()
        self.assertEqual(self.query('SELECT LastUsedAt FROM broker.Connections')[0][0], used)
        self.assertTrue(self.prepare()[0]['allowed'])
        self.assertGreater(self.query('SELECT LastUsedAt FROM broker.Connections')[0][0], used)
        # A session with an older local clock cannot move the connection clock back.
        future = self.now()+10
        self.query('UPDATE broker.Connections SET LastUsedAt=?', future)
        self.assertTrue(self.prepare()[0]['allowed'])
        self.assertEqual(self.query('SELECT LastUsedAt FROM broker.Connections')[0][0], future)

    def test_idle_cleanup_revokes_live_sessions_and_blocks_use_and_reauthentication(self):
        self.activate(); self.age_connection()
        claimed = self.store.claim_cleanup(self.cleanup_candidate(), 'cleanup-worker')
        self.assertTrue(self.store.validate_cleanup_claim(claimed))
        self.assertFalse(self.prepare()[0]['allowed'])
        with self.assertRaises(StoreConflict):
            self.store.allocate_connection(PROFILE, TENANT, OWNER, 'member@example.test', 'new-auth')
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 1)
        self.assertEqual(self.query('SELECT COUNT(*) FROM broker.Sessions WHERE Revoked=0')[0][0], 0)
        self.store.finish_cleanup(claimed)
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 0)
        with self.assertRaises(StoreConflict):
            self.store.finish_cleanup(claimed)

    def test_business_use_after_candidate_snapshot_prevents_claim(self):
        self.activate(); self.age_connection()
        candidate = self.cleanup_candidate()
        self.assertTrue(self.prepare()[0]['allowed'])
        with self.assertRaises(StoreConflict):
            self.store.claim_cleanup(candidate, 'cleanup-worker')
        self.assertEqual(self.query('SELECT State FROM broker.Connections')[0][0], 'ready')

    def test_identity_pinned_pending_authentication_is_protected_before_identity_callback(self):
        self.activate(); self.age_connection()
        candidate = self.cleanup_candidate()
        self.store.create_challenge('e'*64, PROFILE, SUB, 'f'*64, '1'*64, self.now()+600,
            pending_session_id='2'*64, pending_secret_hash='3'*64,
            source_session=(self.sid, digest(self.secret)))
        self.assertEqual(self.store.cleanup_candidates({(PROFILE, PROFILE+'-a')}), [])
        with self.assertRaises(StoreConflict):
            self.store.claim_cleanup(candidate, 'cleanup-worker')

    def test_parallel_claims_have_one_winner_and_do_not_release_capacity(self):
        self.activate(); self.age_connection()
        candidate = self.cleanup_candidate()
        def claim_one(number):
            try:
                return self.store.claim_cleanup(candidate, 'worker-'+str(number))
            except (StoreConflict, StoreUnavailable):
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            values = list(executor.map(claim_one, range(2)))
        self.assertEqual(sum(value is not None for value in values), 1)
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 1)

    def test_reclaimed_lease_and_late_manual_finish_cannot_release_a_later_row(self):
        self.activate()
        manual = self.store.begin_disconnect(self.sid, digest(self.secret), SUB)
        self.assertTrue(self.store.validate_disconnect(manual))
        first = self.store.claim_cleanup(self.cleanup_candidate(), 'worker-one')
        self.assertFalse(self.store.validate_disconnect(manual))
        self.query('UPDATE broker.Connections SET LeaseUntil=?', self.now()-1)
        second = self.store.claim_cleanup(self.cleanup_candidate(), 'worker-two')
        self.assertFalse(self.store.validate_cleanup_claim(first))
        with self.assertRaises(StoreConflict):
            self.store.finish_cleanup(first)
        self.store.finish_cleanup(second)
        allocated = self.store.allocate_connection(PROFILE, TENANT, OWNER, 'member@example.test', 'new-auth')
        self.store.set_connection_state(PROFILE, TENANT, OWNER, 'deleting', 'new-auth')
        with self.assertRaises(StoreConflict):
            self.store.finish_disconnect(PROFILE, TENANT, OWNER, allocated['providerName'],
                allocated['connectionId'], expected_version=manual['connectionVersion'])
        self.assertEqual(self.query('SELECT Allocated FROM broker.Pools')[0][0], 1)

    def test_reauthentication_creates_new_proof_without_reviving_old(self):
        self.activate()
        self.store.create_challenge('e'*64, PROFILE, SUB, 'f'*64, '1'*64, self.now()+600,
            pending_session_id='2'*64, pending_secret_hash='3'*64,
            source_session=(self.sid, digest(self.secret)), force_cm_login=True)
        self.assertIsNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
        self.assertEqual(self.query('SELECT RequiredObjectId FROM broker.Transactions WHERE TransactionId=?', 'e'*64)[0][0].lower(), OWNER)

    def test_old_verification_ticket_cannot_restore_invalidated_cm_connection(self):
        for mode in ('force_login', 'missing_cm'):
            with self.subTest(mode=mode):
                self.setUp()
                self.activate()
                pending = secrets.token_hex(32)
                self.store.create_transaction(pending, PROFILE, SUB, 'a'*64, 'b'*64, self.now()+600,
                    pending_session_id='e'*64, pending_secret_hash='f'*64)
                self.query("UPDATE broker.Transactions SET Phase='verifying',TenantId=?,ObjectId=?,ProviderName=?,ConnectionId='member',SubjectEpochAtVerification=0,ConnectionEpochAtVerification=0,TicketHash=? WHERE TransactionId=?", TENANT, OWNER, PROFILE+'-a', 'd'*64, pending)
                if mode == 'force_login':
                    self.store.create_challenge('e'*64, PROFILE, SUB, 'f'*64, '1'*64, self.now()+600,
                        pending_session_id='2'*64, pending_secret_hash='3'*64,
                        source_session=(self.sid, digest(self.secret)), force_cm_login=True)
                else:
                    version = self.query('SELECT Version FROM broker.Connections')[0][0]
                    self.store.observe_connection(PROFILE, TENANT, OWNER, False, version)
                with self.assertRaises(StoreNotAuthorized):
                    self.store.complete_transaction(pending, 'd'*64, OWNER, policy=POLICY)
                self.assertEqual(self.query('SELECT State FROM broker.Connections')[0][0], 'unverified')

    def test_applied_migration_never_deletes_existing_new_sessions_again(self):
        import re
        self.activate()
        script=(Path(__file__).resolve().parents[1]/'sql/002_session_lifecycle.sql').read_text(encoding='utf-8')
        with self.connect() as connection:
            for batch in re.split(r'(?im)^GO\s*$', script):
                if batch.strip():
                    cursor=connection.cursor().execute(batch)
                    while cursor.nextset(): pass
        self.assertIsNotNone(self.store.resolve_session(self.sid, digest(self.secret), SUB, PROFILE))
