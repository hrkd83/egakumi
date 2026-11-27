"""Constructed management identity grants never promote the runtime/schema ID."""
import contextlib
import io
import os
import unittest
import uuid
from unittest import mock

from credential_broker import cli
from credential_broker.store import StoreConflict, StoreUnavailable
from _store_support import fixture


# Independent ordered-byte and repeated-digit fixtures, never environment IDs.
MANAGEMENT = "00112233-4455-4677-8899-aabbccddeeff"
RUNTIME = "11111111-1111-4111-8111-111111111111"
SCHEMA = "22222222-2222-4222-8222-222222222222"
SID = uuid.UUID(MANAGEMENT).bytes_le
NAME = "core_management_vm"


class ManagementIdentityTests(unittest.TestCase):
    def values(self):
        return dict(management_client_id=MANAGEMENT, runtime_client_id=RUNTIME,
                    schema_client_id=SCHEMA, runtime_user_name="runtime-fixture")

    def test_client_id_sid_uses_guid_little_endian(self):
        self.assertEqual(cli.management_identity_sid(**self.values()).hex(), "33221100554477468899aabbccddeeff")

    def test_missing_zero_shared_or_colliding_identity_is_rejected_before_sql(self):
        changes = [{key: value} for key in ("management_client_id", "runtime_client_id", "schema_client_id")
                   for value in (None, "", "invalid", "00000000-0000-0000-0000-000000000000")]
        changes.extend(({"management_client_id": RUNTIME}, {"management_client_id": SCHEMA},
                        {"schema_client_id": RUNTIME}, {"runtime_user_name": NAME.upper()},
                        {"runtime_user_name": "invalid];"}))
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cli.management_identity_sid(**{**self.values(), **change})

    def test_formal_pipeline_validates_before_observation_or_migration(self):
        environment = {"SQL_MANAGEMENT_CLIENT_ID": MANAGEMENT, "SQL_RUNTIME_CLIENT_ID": MANAGEMENT,
                       "AZURE_CLIENT_ID": SCHEMA, "SQL_RUNTIME_USER_NAME": "runtime-fixture"}
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(cli.SqlStore, "from_environment"), \
             mock.patch.object(cli, "observe_schema_before_migration") as observe, \
             mock.patch.object(cli, "migrate") as migrate, \
             mock.patch.object(cli, "grant_management_access") as grant, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ValueError):
                cli.main(["migrate"])
        observe.assert_not_called()
        migrate.assert_not_called()
        grant.assert_not_called()
        self.assertEqual(output.getvalue(), "")


class ManagementGrantTests(unittest.TestCase):
    def initial(self, principals=None):
        return [("BEGIN TRANSACTION", None), ("sp_getapplock", None),
                ("SELECT name,sid,type", principals or [])]

    def matching(self):
        return [{"name": NAME, "sid": SID, "type": "E"}]

    def test_new_user_grant_and_verification_commit_as_one_transaction(self):
        replies = self.initial() + [("CREATE USER", None), ("SELECT IS_ROLEMEMBER", {"member": 0}),
            ("ALTER ROLE [db_owner] ADD MEMBER", None), ("SELECT IS_ROLEMEMBER", {"member": 1}), ("COMMIT", None)]
        store, cursor = fixture(replies)
        cli.grant_management_access(store, management_sid=SID)
        self.assertFalse(cursor.replies)
        self.assertIn("@LockOwner='Transaction'", cursor.calls[1][0])
        self.assertEqual(cursor.calls[2][1], (NAME, SID))
        self.assertEqual(cursor.calls[3][0], "CREATE USER [core_management_vm] WITH SID=0x" + SID.hex() + ", TYPE=E;")
        self.assertEqual(cursor.calls[5][0], "ALTER ROLE [db_owner] ADD MEMBER [core_management_vm]")
        self.assertTrue(cursor.calls[-1][0].startswith("COMMIT"))

    def test_repeated_matching_user_preserves_existing_owner_and_avoids_duplicate_ddl(self):
        store, cursor = fixture(self.initial(self.matching()) + [
            ("SELECT IS_ROLEMEMBER", {"member": 1}), ("SELECT IS_ROLEMEMBER", {"member": 1}), ("COMMIT", None)])
        cli.grant_management_access(store, management_sid=SID)
        self.assertFalse(cursor.replies)
        self.assertFalse(any(query.startswith(("CREATE", "ALTER")) for query, _ in cursor.calls))

    def test_existing_non_owner_is_promoted_without_recreating_principal(self):
        store, cursor = fixture(self.initial(self.matching()) + [
            ("SELECT IS_ROLEMEMBER", {"member": 0}), ("ALTER ROLE", None),
            ("SELECT IS_ROLEMEMBER", {"member": 1}), ("COMMIT", None)])
        cli.grant_management_access(store, management_sid=SID)
        self.assertFalse(any(query.startswith("CREATE") for query, _ in cursor.calls))

    def test_name_sid_type_or_duplicate_collisions_roll_back_before_grants(self):
        principal = self.matching()[0]
        for rows in ([{**principal, "sid": uuid.UUID(RUNTIME).bytes_le}],
                     [{**principal, "name": "another-user"}], [{**principal, "type": "S"}],
                     [{**principal, "sid": None}], [principal, principal]):
            with self.subTest(rows=rows):
                store, cursor = fixture(self.initial(rows) + [("ROLLBACK", None)])
                with self.assertRaises(StoreConflict):
                    cli.grant_management_access(store, management_sid=SID)
                self.assertFalse(cursor.replies)
                self.assertFalse(any(query.startswith(("CREATE", "ALTER")) for query, _ in cursor.calls))
                self.assertIn("ROLLBACK", cursor.calls[-1][0])

    def test_unknown_role_or_unverified_grant_rolls_back_new_user_and_owner_change(self):
        for membership in (None, "0", 2):
            with self.subTest(membership=membership):
                store, cursor = fixture(self.initial() + [("CREATE USER", None),
                    ("SELECT IS_ROLEMEMBER", {"member": membership}), ("ROLLBACK", None)])
                with self.assertRaises(StoreConflict):
                    cli.grant_management_access(store, management_sid=SID)
                self.assertIn("ROLLBACK", cursor.calls[-1][0])
                self.assertFalse(any(query.startswith("ALTER") for query, _ in cursor.calls))
        store, cursor = fixture(self.initial() + [("CREATE USER", None),
            ("SELECT IS_ROLEMEMBER", {"member": 0}), ("ALTER ROLE", None),
            ("SELECT IS_ROLEMEMBER", {"member": 0}), ("ROLLBACK", None)])
        with self.assertRaises(StoreConflict):
            cli.grant_management_access(store, management_sid=SID)
        self.assertIn("ROLLBACK", cursor.calls[-1][0])

    def test_lock_failure_stops_before_principal_changes_and_rolls_back(self):
        store, cursor = fixture([("BEGIN TRANSACTION", None),
            ("sp_getapplock", RuntimeError("synthetic lock failure")), ("ROLLBACK", None)])
        with self.assertRaises(StoreUnavailable):
            cli.grant_management_access(store, management_sid=SID)
        self.assertIn("ROLLBACK", cursor.calls[-1][0])
        self.assertEqual(len(cursor.calls), 3)


if __name__ == "__main__":
    unittest.main()
