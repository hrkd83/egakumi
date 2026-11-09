"""Offline SQL store contracts; live concurrency is tested separately."""
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from credential_broker.store import SqlStore, StoreConflict, StoreNotAuthorized, StoreRateLimited, StoreUnavailable, connection_name, _flow_json
from _store_support import Connection, Cursor, OWNER, PROOF, TENANT, TX, fixture, transaction

POLICY = dict(idleTimeoutSeconds=604800, absoluteLifetimeSeconds=2592000,
              rotationIntervalSeconds=86400, rotationGraceSeconds=300,
              rotationHandoffSeconds=600, rotationCandidateTtlSeconds=600)


class NamingTests(unittest.TestCase):
    def test_names_use_fresh_128_bit_generations_for_the_same_identity(self):
        with patch('credential_broker.store.secrets.token_hex', side_effect=['a' * 32, 'b' * 32]) as entropy:
            first = connection_name(TENANT, OWNER, 'graph-delegated', 'Test.User@example.invalid')
            second = connection_name(TENANT, OWNER, 'graph-delegated', 'Test.User@example.invalid')
        self.assertRegex(first, r'^test-user-[a-f0-9]{32}$')
        self.assertNotEqual(first, second)
        self.assertEqual([call(16), call(16)], entropy.call_args_list)

    def test_names_normalize_readable_prefix_and_remain_within_eighty_characters(self):
        self.assertRegex(connection_name(TENANT, OWNER, 'graph-delegated', 'Ö Test___User@example.invalid'),
                         r'^o-test-user-[a-f0-9]{32}$')
        self.assertRegex(connection_name(TENANT, OWNER, 'graph-delegated', '日本語@example.invalid'),
                         r'^member-[a-f0-9]{32}$')
        long = connection_name(TENANT, OWNER, 'graph-delegated', 'x' * 250 + '@example.invalid')
        self.assertEqual(80, len(long))
        self.assertRegex(long, r'^x{47}-[a-f0-9]{32}$')

    def test_names_still_require_verified_identity_and_configured_profile(self):
        with patch('credential_broker.store.secrets.token_hex') as entropy:
            for args in [('invalid', OWNER, 'graph-delegated', 'member@example.invalid'),
                         (TENANT, '00000000-0000-0000-0000-000000000000', 'graph-delegated', 'member@example.invalid'),
                         (TENANT, OWNER, 'Invalid/Profile', 'member@example.invalid'),
                         (TENANT, OWNER, 'graph-delegated', 'x' * 321)]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    connection_name(*args)
            entropy.assert_not_called()

    def test_flow_rejects_token_payloads_but_allows_one_use_pkce_state(self):
        self.assertIn("code_verifier", _flow_json({"flow": {"state": "nonce", "code_verifier": "verifier"}}))
        for key in ("access_token", "refresh_token", "id_token"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                _flow_json({"flow": {key: "PRIVATE-TOKEN"}})


class StoreContractTests(unittest.TestCase):
    def session_row(self, **changes):
        return {"SessionId": TX, "SecretHash": PROOF, "SubscriptionId": "subscription-a", "ProfileName": "graph-delegated",
                "TenantId": TENANT, "ObjectId": OWNER, "ProviderName": "graph-delegated-a", "ConnectionId": "existing-user",
                "ExpiresAt": 1500, "IdleExpiresAt": 1500, "AbsoluteExpiresAt": 2000,
                "Revoked": False, "ConnectionState": "ready", "SubjectEpoch": 0,
                "SubjectEpochAtAuthentication": 0, "ConnectionEpoch": 0,
                "ConnectionEpochAtAuthentication": 0, **changes}

    def test_challenge_validates_expired_owner_proof_and_rate_before_creating_transaction(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
            ("SELECT * FROM broker.Sessions", self.session_row(ExpiresAt=900, IdleExpiresAt=900)),
            ("SELECT State FROM broker.Connections", {"State": "ready"}),
            ("UPDATE broker.Connections", 1), ("UPDATE broker.Sessions", 1), ("SELECT COUNT(*)", {"Count": 0}),
            ("INSERT broker.Transactions", None), ("SELECT * FROM broker.Transactions", transaction()), ("COMMIT", None)])
        store.create_challenge("0" * 64, "graph-delegated", "subscription-a", "1" * 64, "2" * 64, 1300,
            pending_session_id="3" * 64, pending_secret_hash="4" * 64,
            source_session=(TX, PROOF), force_cm_login=True, login_limit=10)
        proof_lookup = next(sql for sql, _ in cursor.calls if "SELECT * FROM broker.Sessions" in sql)
        self.assertNotIn("ExpiresAt", proof_lookup)
        self.assertIn("UPDLOCK,HOLDLOCK", proof_lookup)
        count, count_params = next(call for call in cursor.calls if "SELECT COUNT(*)" in call[0])
        self.assertIn("INDEX(IX_BrokerTransactionSubscriptionCreated)", count)
        self.assertEqual(("subscription-a", 940), count_params)
        statement, values = next(call for call in cursor.calls if call[0].startswith("INSERT"))
        self.assertEqual((TENANT, OWNER, True, "3" * 64, "4" * 64), values[-5:])
        self.assertNotIn(TENANT, statement)
        self.assertTrue(any("SecurityEpoch=SecurityEpoch+1" in sql for sql, _ in cursor.calls))
        self.assertFalse(any("Pools" in sql for sql, _ in cursor.calls))

    def test_both_reauthentication_modes_reject_deleting_before_transaction_creation(self):
        for force in (False, True):
            store, cursor = fixture([
                ('BEGIN TRANSACTION', None), ('DATEDIFF_BIG', {'Now': 1000}),
                ('SELECT * FROM broker.Sessions', self.session_row()),
                ('SELECT State FROM broker.Connections', {'State': 'deleting'}), ('ROLLBACK', None)])
            with self.subTest(force=force), self.assertRaises(StoreConflict):
                store.create_challenge('0'*64, 'graph-delegated', 'subscription-a', '1'*64, '2'*64, 1300,
                    pending_session_id='3'*64, pending_secret_hash='4'*64,
                    source_session=(TX, PROOF), force_cm_login=force)
            self.assertFalse(any(sql.startswith(('UPDATE', 'INSERT')) for sql, _ in cursor.calls))

    def test_removed_connection_allows_fresh_pinned_identity_authentication_in_both_modes(self):
        for force in (False, True):
            responses = [('BEGIN TRANSACTION', None), ('DATEDIFF_BIG', {'Now': 1000}),
                         ('SELECT * FROM broker.Sessions', self.session_row()),
                         ('SELECT State FROM broker.Connections', [])]
            if force:
                responses.append(('UPDATE broker.Sessions', 1))
            responses.extend([('SELECT COUNT(*)', {'Count': 0}), ('INSERT broker.Transactions', None),
                              ('SELECT * FROM broker.Transactions', transaction()), ('COMMIT', None)])
            store, cursor = fixture(responses)
            with self.subTest(force=force):
                store.create_challenge('0'*64, 'graph-delegated', 'subscription-a', '1'*64, '2'*64, 1300,
                    pending_session_id='3'*64, pending_secret_hash='4'*64,
                    source_session=(TX, PROOF), force_cm_login=force)
            self.assertTrue(any(sql.startswith('INSERT broker.Transactions') for sql, _ in cursor.calls))
            self.assertFalse(any(sql.startswith('UPDATE broker.Connections') for sql, _ in cursor.calls))

    def test_challenge_rejects_spoofed_proof_before_count_and_insert(self):
        for row in [[], self.session_row(SecretHash="0" * 64), self.session_row(SubscriptionId="subscription-b"),
                    self.session_row(ProfileName="another")]:
            store, cursor = fixture([
                ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
                ("SELECT * FROM broker.Sessions", row), ("ROLLBACK", None)])
            with self.assertRaises(StoreNotAuthorized):
                store.create_challenge("0" * 64, "graph-delegated", "subscription-a", "1" * 64, "2" * 64, 1300,
                    pending_session_id="3" * 64, pending_secret_hash="4" * 64,
                    source_session=(TX, PROOF))
            self.assertFalse(any("COUNT(*)" in sql or sql.startswith("INSERT") for sql, _ in cursor.calls))

    def test_start_and_challenge_limit_rolls_back_before_issuing_proofs(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
            ("SELECT COUNT(*)", {"Count": 10}), ("ROLLBACK", None)])
        with self.assertRaises(StoreRateLimited):
            store.create_transaction(TX, "graph-delegated", "subscription-a", PROOF, "c" * 64, 1300,
                pending_session_id="3" * 64, pending_secret_hash="4" * 64)
        self.assertFalse(any(sql.startswith("INSERT") for sql, _ in cursor.calls))

    def test_resolve_session_separates_recoverable_expiry_and_mapping_state_from_forgery(self):
        for changes in [{"IdleExpiresAt": 900}, {"AbsoluteExpiresAt": 900}, {"Revoked": True},
                        {"SubjectEpoch": 1}, {"ConnectionEpoch": 1}, {"ConnectionState": "pending"},
                        {"ConnectionState": None}]:
            store, _ = fixture([("SELECT s.*,c.State", self.session_row(**changes)), ("DATEDIFF_BIG", {"Now": 1000})])
            self.assertIsNone(store.resolve_session(TX, PROOF, "subscription-a", "graph-delegated"))
        store, _ = fixture([("SELECT s.*,c.State", self.session_row(ExpiresAt=1500)), ("DATEDIFF_BIG", {"Now": 1000})])
        self.assertEqual(OWNER, store.resolve_session(TX, PROOF, "subscription-a", "graph-delegated")["objectId"])
        for row in [[], self.session_row(SecretHash="0" * 64), self.session_row(SubscriptionId="other"),
                    self.session_row(ProfileName="other")]:
            store, _ = fixture([("SELECT s.*,c.State", row), ("DATEDIFF_BIG", {"Now": 1000})])
            with self.assertRaises(StoreNotAuthorized):
                store.resolve_session(TX, PROOF, "subscription-a", "graph-delegated")

    def test_identity_flow_is_consumed_once_in_a_transaction(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("SELECT * FROM broker.Transactions", transaction()),
            ("DATEDIFF_BIG", {"Now": 1000}), ("UPDATE broker.Transactions", 1), ("COMMIT", None)])
        value = store.consume_identity(TX, "d" * 64, "e" * 64)
        self.assertEqual(value["phase"], "identityProcessing")
        self.assertEqual(value["flow"]["code_verifier"], "private-verifier")
        update = next(sql for sql, _ in cursor.calls if sql.startswith("UPDATE"))
        self.assertIn("OidcFlow=NULL", update)
        self.assertIn("OidcStateHash=NULL", update)
        self.assertIn("UPDLOCK,HOLDLOCK", cursor.calls[1][0])
        reused, reused_cursor = fixture([
            ("BEGIN TRANSACTION", None), ("SELECT * FROM broker.Transactions", transaction(Phase="identityProcessing")),
            ("DATEDIFF_BIG", {"Now": 1000}), ("ROLLBACK", None)])
        with self.assertRaises(StoreConflict):
            reused.consume_identity(TX, "d" * 64, "e" * 64)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in reused_cursor.calls))

    def test_invalid_browser_proof_rolls_back_before_flow_consumption(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("SELECT * FROM broker.Transactions", transaction()),
            ("DATEDIFF_BIG", {"Now": 1000}), ("ROLLBACK", None)])
        with self.assertRaises(StoreConflict):
            store.consume_identity(TX, "a" * 64, "e" * 64)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in cursor.calls))

    def test_wrong_graph_owner_cannot_create_a_session(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("SELECT * FROM broker.Transactions", transaction(Phase="verifying")),
            ("DATEDIFF_BIG", {"Now": 1000}), ("ROLLBACK", None)])
        with self.assertRaises(StoreConflict):
            store.complete_transaction(TX, "f" * 64, TENANT, policy=POLICY)
        self.assertFalse(any(sql.startswith("INSERT") for sql, _ in cursor.calls))

    def test_allocation_uses_fixed_stored_procedure_and_parameterized_identity(self):
        row = {"ProviderName": "graph-delegated-a", "ConnectionId": "existing-readable-name",
               "State": "ready", "LeaseOwned": False, "IsNew": False}
        store, cursor = fixture([("EXEC broker.AllocateConnection", row), ("EXEC broker.AllocateConnection", row)])
        with patch('credential_broker.store.secrets.token_hex', side_effect=['a' * 32, 'b' * 32]):
            result = store.allocate_connection("graph-delegated", TENANT, OWNER, "changed@example.invalid", TX)
            again = store.allocate_connection("graph-delegated", TENANT, OWNER, "renamed@example.invalid", TX)
        self.assertEqual(result["connectionId"], "existing-readable-name")
        self.assertEqual(result['connectionId'], again['connectionId'])
        sql, values = cursor.calls[0]
        self.assertNotIn("changed@example.invalid", sql)
        self.assertEqual(values[3], "changed@example.invalid")
        self.assertNotEqual(values[4], cursor.calls[1][1][4])
        self.assertFalse(result["isNew"])

    def test_new_allocation_lifecycles_pass_distinct_ids_to_the_sql_reservation(self):
        old, new = 'member-' + 'a' * 32, 'member-' + 'b' * 32
        rows = [{'ProviderName': 'graph-delegated-a', 'ConnectionId': name,
                 'State': 'reserved', 'LeaseOwned': True, 'IsNew': True} for name in (old, new)]
        store, cursor = fixture([('EXEC broker.AllocateConnection', row) for row in rows])
        with patch('credential_broker.store.secrets.token_hex', side_effect=['a' * 32, 'b' * 32]):
            first = store.allocate_connection('graph-delegated', TENANT, OWNER, 'member@example.invalid', TX)
            # The SQL lifecycle has ended before this new reservation is returned.
            second = store.allocate_connection('graph-delegated', TENANT, OWNER, 'member@example.invalid', TX)
        self.assertNotEqual(first['connectionId'], second['connectionId'])
        self.assertEqual([old, new], [params[4] for _, params in cursor.calls])

    def test_missing_cm_observation_does_not_release_capacity(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
            ("UPDATE broker.Connections", 1), ("COMMIT", None)])
        store.observe_connection("graph-delegated", TENANT, OWNER, False, b"version")
        self.assertTrue(any("'unverified'" in sql for sql, _ in cursor.calls))
        self.assertFalse(any("broker.Pools" in sql or "DELETE" in sql for sql, _ in cursor.calls))

    def test_pool_quarantine_only_blocks_new_allocation_and_never_clears_itself(self):
        store, cursor = fixture([("BEGIN TRANSACTION", None), ("UPDATE broker.Pools", 1), ("COMMIT", None)])
        self.assertTrue(store.block_pool_allocation("graph", "graph-a"))
        statement = next(sql for sql, _ in cursor.calls if sql.startswith("UPDATE"))
        self.assertIn("SET AllocationBlocked=1", statement)
        self.assertIn("AND AllocationBlocked=0", statement)
        self.assertFalse(any("Connections" in sql or "Sessions" in sql for sql, _ in cursor.calls))
        source = (Path(__file__).resolve().parents[1] / "sql/001_initial.sql").read_text()
        self.assertIn("Enabled=1 AND AllocationBlocked=0 AND Allocated<Capacity", source)
        self.assertIn("IF COL_LENGTH(N'broker.Pools', N'AllocationBlocked') IS NULL", source)

    def test_driver_messages_never_escape_the_store(self):
        store, cursor = fixture([("SELECT * FROM broker.Transactions", RuntimeError("PRIVATE SQL TOKEN server-secret"))])
        with self.assertRaises(StoreUnavailable) as error:
            store.get_transaction(TX)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_expired_transient_cleanup_is_bounded_and_preserves_connections_and_capacity(self):
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
            ("UPDATE TOP", 2), ("DELETE TOP (?) FROM broker.SessionRotationCandidates", 5),
            ("DELETE TOP (?) FROM broker.Sessions", 3),
            ("DELETE TOP (?) FROM broker.Transactions", 4), ("COMMIT", None)])
        result = store.purge_expired_state(batch_size=1000)
        self.assertEqual(result, {"expiredProofs": 7, "deletedSessions": 3, "deletedTransactions": 4})
        statements = [sql for sql, _ in cursor.calls]
        clear = next(sql for sql in statements if sql.startswith("UPDATE"))
        self.assertIn("OidcFlow=NULL", clear)
        self.assertIn("ExpiresAt<=?", clear)
        final_delete = next(sql for sql in statements if "DELETE" in sql and "broker.Transactions" in sql)
        self.assertIn("WHERE ExpiresAt<=?", final_delete)
        self.assertNotIn("SessionExpiresAt", final_delete)
        self.assertFalse(any("broker.Connections" in sql or "broker.Pools" in sql for sql in statements))
        candidate_delete = next(params for sql, params in cursor.calls if sql.startswith("DELETE") and "broker.SessionRotationCandidates" in sql)
        self.assertEqual((1000, 1000), candidate_delete)
        session_delete = next(params for sql, params in cursor.calls if sql.startswith("DELETE") and "broker.Sessions" in sql)
        self.assertEqual((1000, 1000 - 86400), session_delete)
        with self.assertRaises(ValueError): store.purge_expired_state(batch_size=10001)

    def test_sql_schema_defines_atomic_capacity_and_two_distinct_uniqueness_constraints(self):
        source = (Path(__file__).resolve().parents[1] / "sql/001_initial.sql").read_text()
        self.assertIn("PRIMARY KEY (TenantId, ObjectId, ProfileName)", source)
        self.assertIn("UNIQUE (ProviderName, ConnectionId)", source)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE", source)
        self.assertIn("WITH (UPDLOCK,HOLDLOCK)", source)
        self.assertIn("Allocated<Capacity", source)
        self.assertIn("ROLLBACK TRANSACTION", source)


class ManagedIdentityTests(unittest.TestCase):
    def test_connection_uses_utf16_access_token_without_password_or_interactive_fallback(self):
        captured = {}
        class Credential:
            def __init__(self, **kwargs): captured["identity"] = kwargs
            def get_token(self, scope):
                captured["scope"] = scope
                return SimpleNamespace(token="PRIVATE-ACCESS-TOKEN")
        def connect(string, **kwargs):
            captured.update(string=string, kwargs=kwargs)
            return Connection(Cursor([]))
        fake_identity = SimpleNamespace(ManagedIdentityCredential=Credential)
        env = {"AZURE_SQL_SERVER": "fixture.database.windows.net", "AZURE_SQL_DATABASE": "credentials",
               "AZURE_CLIENT_ID": TENANT}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"pyodbc": SimpleNamespace(connect=connect), "azure.identity": fake_identity}):
            store = SqlStore.from_environment()
            store._connect()
        self.assertEqual(captured["scope"], "https://database.windows.net/.default")
        self.assertEqual(captured["identity"], {"client_id": TENANT})
        self.assertNotIn("Password", captured["string"])
        self.assertNotIn("PRIVATE", captured["string"])
        packed = captured["kwargs"]["attrs_before"][1256]
        self.assertEqual(struct.unpack("<I", packed[:4])[0], len(packed[4:]))
        self.assertEqual(packed[4:].decode("utf-16-le"), "PRIVATE-ACCESS-TOKEN")


if __name__ == "__main__":
    unittest.main()
