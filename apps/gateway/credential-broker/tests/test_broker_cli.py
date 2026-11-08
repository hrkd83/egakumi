"""SQL runtime grants and read-only Credential Manager reconciliation."""
import copy
import ast
import contextlib
import io
import json
import os
import re
import uuid
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from credential_broker import cli
from credential_broker.cli import migrate, pool_inventory, reconcile, _identifier, grant_runtime_access, RUNTIME_TABLE_PERMISSIONS, observe_schema_before_migration
from credential_broker.store import StoreConflict, StoreUnavailable, SqlStore
from _store_support import OWNER, TENANT, fixture, Connection


# Independent acceptance whitelist: no schema-version DML or future-table access.
EXPECTED_RUNTIME_PERMISSIONS = {
    "SchemaVersions": {"SELECT"},
    "Pools": {"SELECT", "UPDATE"},
    "Connections": {"SELECT", "UPDATE", "DELETE"},
    "Transactions": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "Sessions": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "SubjectSecurity": {"SELECT", "INSERT", "UPDATE"},
    "SessionRotationCandidates": {"SELECT", "INSERT", "DELETE"},
}


class SchemaObservationTests(unittest.TestCase):
    def test_empty_and_existing_database_metadata_use_one_read_only_query(self):
        for count, present in ((0, 0), (9, 1)):
            store, cursor = fixture([("SELECT CASE WHEN HAS_PERMS_BY_NAME", {"tables": count, "present": present})])
            with self.subTest(count=count):
                self.assertEqual(observe_schema_before_migration(store), {
                    "stage": "before-migration", "userTableCount": count, "migrationTablePresent": bool(present)})
                self.assertEqual(len(cursor.calls), 1)
                query = cursor.calls[0][0]
                self.assertIn("sys.tables WHERE is_ms_shipped=0", query)
                self.assertIn("broker.SchemaVersions", query)
                self.assertIn("'VIEW DEFINITION'", query)

    def test_missing_visibility_or_malformed_metadata_is_not_an_empty_database(self):
        for row in ([], {"tables": None, "present": 0}, {"tables": -1, "present": 0},
                    {"tables": "0", "present": 0}, {"tables": 0, "present": 2}):
            store, _cursor = fixture([("SELECT CASE WHEN HAS_PERMS_BY_NAME", row)])
            with self.subTest(row=row), self.assertRaises(StoreUnavailable):
                observe_schema_before_migration(store)

    def test_formal_migrate_entry_records_before_ddl_and_stops_on_observation_error(self):
        observation = {"stage": "before-migration", "userTableCount": 0, "migrationTablePresent": False}
        environment = {"SQL_RUNTIME_CLIENT_ID": TENANT, "SQL_RUNTIME_USER_NAME": "id-runtime", "BROKER_PROFILES_JSON": "{}"}
        output = io.StringIO()
        def migrate_after_observation(*args, **kwargs):
            self.assertEqual(json.loads(output.getvalue()), observation)
            return {"status": "ready"}
        with mock.patch.dict(os.environ, environment), mock.patch.object(cli.SqlStore, "from_environment"), \
             mock.patch.object(cli, "observe_schema_before_migration", return_value=observation), \
             mock.patch.object(cli, "migrate", side_effect=migrate_after_observation) as migrate_call, \
             contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["migrate"]), 0)
        migrate_call.assert_called_once()
        with mock.patch.object(cli.SqlStore, "from_environment"), \
             mock.patch.object(cli, "observe_schema_before_migration", side_effect=StoreUnavailable("metadata unavailable")), \
             mock.patch.object(cli, "migrate") as migrate_call, contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(StoreUnavailable):
                cli.main(["migrate"])
        migrate_call.assert_not_called()
        self.assertEqual(output.getvalue(), "")


def grant_replies(members=(0, 0)):
    replies = [("BEGIN TRANSACTION", None),
               ("SELECT sid,type", {"sid": uuid.UUID(TENANT).bytes_le, "type": "E"})]
    replies.extend(("GRANT " + permissions + " ON OBJECT::broker.[" + table + "]", None)
                   for table, permissions in RUNTIME_TABLE_PERMISSIONS.items())
    replies.append(("GRANT EXECUTE ON OBJECT::broker.AllocateConnection", None))
    for role, member in zip(("db_datareader", "db_datawriter"), members):
        replies.append(("SELECT IS_ROLEMEMBER", {"member": member}))
        if member == 1:
            replies.append(("ALTER ROLE [" + role + "] DROP MEMBER [id-runtime]", None))
    return replies + [("COMMIT", None)]


class MigrationTests(unittest.TestCase):
    def test_migration_creates_client_id_bound_runtime_with_limited_objects_and_proc_execute(self):
        store, cursor = fixture([
            ("SELECT CASE WHEN OBJECT_ID", {"exists": 0}), ("SELECT schemafixture", None),
            ("SELECT secondfixture", None), ("SELECT sid,type", []), ("CREATE USER", None),
            *grant_replies(),
            ("BEGIN TRANSACTION", None), ("UPDATE broker.Pools SET Enabled=0", None),
            ("SELECT Allocated", []), ("INSERT broker.Pools", None), ("COMMIT", None),
            ("SELECT MAX(Version)", {"version": 2}), ("SELECT TOP(0)", None)])
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder) / "001_fixture.sql"
            schema.write_text("SELECT schemafixture;\nGO\n", encoding="utf-8")
            (Path(folder) / "002_fixture.sql").write_text("SELECT secondfixture;", encoding="utf-8")
            result = migrate(store, runtime_client_id=TENANT, runtime_user_name="id-runtime",
                profiles={"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}, schema_path=schema)
        sql = "\n".join(query for query, _ in cursor.calls)
        import uuid
        self.assertIn("WITH SID=0x" + uuid.UUID(TENANT).bytes_le.hex() + ", TYPE=E", sql)
        self.assertNotIn(uuid.UUID(OWNER).bytes_le.hex(), sql)
        self.assertNotIn("FROM EXTERNAL PROVIDER", sql)
        self.assertNotIn("db_owner", sql)
        self.assertNotIn("CREATE LOGIN", sql)
        self.assertNotIn("ADD MEMBER", sql)
        self.assertEqual({table: set(value.split(", ")) for table, value in RUNTIME_TABLE_PERMISSIONS.items()},
                         EXPECTED_RUNTIME_PERMISSIONS)
        self.assertEqual(result["poolCount"], 1)
        self.assertEqual(result["schemaVersion"], 2)

    def test_existing_sql_user_bound_to_other_identity_stops_before_any_role_grant(self):
        store, cursor = fixture([("SELECT CASE WHEN OBJECT_ID", {"exists": 0}),
            ("SELECT schemafixture", None), ("SELECT sid,type", {"sid": b"wrong", "type": "E"})])
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder) / "001_fixture.sql"
            schema.write_text("SELECT schemafixture;", encoding="utf-8")
            with self.assertRaises(StoreConflict):
                migrate(store, runtime_client_id=TENANT, runtime_user_name="id-runtime",
                    profiles={"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}, schema_path=schema)
        self.assertFalse(any("GRANT" in query or "ALTER ROLE" in query for query, _ in cursor.calls))

    def test_applied_migrations_are_not_replayed_on_a_later_deployment(self):
        import uuid
        store, cursor = fixture([
            ("SELECT CASE WHEN OBJECT_ID", {"exists": 1}), ("SELECT Version", [{"version": 1}, {"version": 2}]),
            ("SELECT sid,type", {"sid": uuid.UUID(TENANT).bytes_le, "type": "E"}),
            *grant_replies((1, 1)),
            ("BEGIN TRANSACTION", None), ("UPDATE broker.Pools SET Enabled=0", None),
            ("SELECT Allocated", [{"allocated": 0}]), ("UPDATE broker.Pools SET Capacity", None),
            ("COMMIT", None), ("SELECT MAX(Version)", {"version": 2}), ("SELECT TOP(0)", None)])
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder) / "001_fixture.sql"
            schema.write_text("RAISE MUST_NOT_REPLAY_INITIAL;", encoding="utf-8")
            (Path(folder) / "002_fixture.sql").write_text("RAISE MUST_NOT_RESET_SESSION;", encoding="utf-8")
            result = migrate(store, runtime_client_id=TENANT, runtime_user_name="id-runtime",
                profiles={"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}, schema_path=schema)
        self.assertEqual(2, result["schemaVersion"])
        self.assertFalse(any("RAISE" in sql for sql, _ in cursor.calls))


class GrantStateCursor:
    """Transactional permission model; no SQL engine or credentials are used."""
    def __init__(self, *, sid=None, kind="E", fail=None, unknown_role=None):
        self.sid = uuid.UUID(TENANT).bytes_le if sid is None else sid
        self.kind, self.fail, self.unknown_role = kind, fail, unknown_role
        self.roles = {role: {"id-runtime", "id-other"} for role in ("db_datareader", "db_datawriter")}
        self.permissions = {"id-runtime": set(), "id-other": {("OtherTable", "SELECT")}}
        self.snapshot = None
        self.calls, self.rows = [], []
        self.commits = self.rollbacks = 0

    def execute(self, query, *params):
        self.calls.append((query, params))
        self.rows = []
        if self.fail and self.fail in query:
            raise RuntimeError("synthetic operation failure")
        if "BEGIN TRANSACTION" in query:
            self.snapshot = copy.deepcopy((self.roles, self.permissions))
        elif "ROLLBACK TRANSACTION" in query:
            self.roles, self.permissions = self.snapshot
            self.snapshot = None
            self.rollbacks += 1
        elif query == "COMMIT TRANSACTION;":
            self.snapshot = None
            self.commits += 1
        elif query.startswith("SELECT sid,type"):
            if params != ("id-runtime",):
                raise AssertionError("Another principal was selected")
            self.rows = [(self.sid, self.kind)]
        elif query.startswith("SELECT IS_ROLEMEMBER"):
            role, principal = params
            self.rows = [(None if role == self.unknown_role else int(principal in self.roles[role]),)]
        elif query.startswith("GRANT "):
            match = re.fullmatch(r"GRANT ([A-Z, ]+) ON OBJECT::broker\.\[?([A-Za-z]+)\]? TO \[([A-Za-z-]+)\]", query)
            if not match:
                raise AssertionError("Unexpected permission statement")
            permissions, table, principal = match.groups()
            self.permissions[principal].update((table, value) for value in permissions.split(", "))
        elif query.startswith("ALTER ROLE "):
            match = re.fullmatch(r"ALTER ROLE \[(db_datareader|db_datawriter)\] DROP MEMBER \[([A-Za-z-]+)\]", query)
            if not match:
                raise AssertionError("Role addition or another role is forbidden")
            role, principal = match.groups()
            self.roles[role].remove(principal)
        else:
            raise AssertionError("Unexpected grant transaction operation")
        return self

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


class RuntimeGrantTests(unittest.TestCase):
    def run_grants(self, cursor):
        grant_runtime_access(SqlStore(lambda: Connection(cursor)), runtime_user_name="id-runtime",
                             runtime_sid=uuid.UUID(TENANT).bytes_le)

    def test_existing_broad_memberships_become_exact_object_grants_for_only_this_principal(self):
        cursor = GrantStateCursor()
        self.run_grants(cursor)
        expected = {(table, permission) for table, permissions in EXPECTED_RUNTIME_PERMISSIONS.items()
                    for permission in permissions} | {("AllocateConnection", "EXECUTE")}
        self.assertEqual(cursor.permissions["id-runtime"], expected)
        self.assertEqual(sum(len(value) for value in EXPECTED_RUNTIME_PERMISSIONS.values()), 20)
        for role in cursor.roles.values():
            self.assertEqual(role, {"id-other"})
        self.assertEqual(cursor.permissions["id-other"], {("OtherTable", "SELECT")})
        self.assertNotIn(("SchemaVersions", "UPDATE"), expected)
        self.assertNotIn(("Connections", "INSERT"), expected)
        self.assertFalse(any("ADD MEMBER" in query or "WITH GRANT OPTION" in query
                             for query, _ in cursor.calls))
        self.assertEqual((cursor.commits, cursor.rollbacks), (1, 0))

    def test_repeated_grants_preserve_the_same_permissions_without_readding_roles(self):
        cursor = GrantStateCursor()
        self.run_grants(cursor)
        first = copy.deepcopy((cursor.roles, cursor.permissions))
        self.run_grants(cursor)
        self.assertEqual((cursor.roles, cursor.permissions), first)
        self.assertEqual(sum("DROP MEMBER" in query for query, _ in cursor.calls), 2)
        self.assertEqual((cursor.commits, cursor.rollbacks), (2, 0))

    def test_principal_replacement_or_type_mismatch_rolls_back_before_grants(self):
        for changes in ({"sid": uuid.UUID(OWNER).bytes_le}, {"kind": "S"}):
            with self.subTest(changes=changes):
                cursor = GrantStateCursor(**changes)
                original = copy.deepcopy((cursor.roles, cursor.permissions))
                with self.assertRaises(StoreConflict):
                    self.run_grants(cursor)
                self.assertEqual((cursor.roles, cursor.permissions), original)
                self.assertFalse(any(query.startswith(("GRANT ", "ALTER ROLE ")) for query, _ in cursor.calls))
                self.assertEqual((cursor.commits, cursor.rollbacks), (0, 1))

    def test_failure_after_one_role_drop_restores_both_roles_and_all_permissions(self):
        cursor = GrantStateCursor(fail="ALTER ROLE [db_datawriter] DROP MEMBER")
        original = copy.deepcopy((cursor.roles, cursor.permissions))
        with self.assertRaises(StoreUnavailable):
            self.run_grants(cursor)
        self.assertEqual((cursor.roles, cursor.permissions), original)
        self.assertTrue(any("ALTER ROLE [db_datareader] DROP MEMBER" in query for query, _ in cursor.calls))
        self.assertEqual((cursor.commits, cursor.rollbacks), (0, 1))

    def test_unknown_membership_does_not_commit_a_partially_restricted_user(self):
        cursor = GrantStateCursor(unknown_role="db_datawriter")
        original = copy.deepcopy((cursor.roles, cursor.permissions))
        with self.assertRaises(StoreConflict):
            self.run_grants(cursor)
        self.assertEqual((cursor.roles, cursor.permissions), original)
        self.assertEqual((cursor.commits, cursor.rollbacks), (0, 1))

    def test_whitelist_covers_runtime_sql_including_cleanup_alias_deletes(self):
        source = Path(__file__).parents[1] / "credential_broker" / "store.py"
        found = set()
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "execute" or not node.args:
                continue
            text = " ".join(item.value for item in ast.walk(node.args[0])
                            if isinstance(item, ast.Constant) and isinstance(item.value, str))
            patterns = {
                "SELECT": r"\b(?:FROM|JOIN)\s+broker\.([A-Za-z]+)",
                "INSERT": r"\bINSERT(?:\s+INTO)?\s+broker\.([A-Za-z]+)",
                "UPDATE": r"\bUPDATE(?:\s+TOP\s*\([^)]*\))?\s+broker\.([A-Za-z]+)",
                "DELETE": r"\bDELETE(?:\s+TOP\s*\([^)]*\))?\s+(?:FROM\s+)?broker\.([A-Za-z]+)",
            }
            for operation, pattern in patterns.items():
                for table in re.findall(pattern, text, re.IGNORECASE):
                    found.add((table, operation))
            for table in re.findall(r"\bDELETE\s+[A-Za-z]+\s+FROM\s+broker\.([A-Za-z]+)", text, re.IGNORECASE):
                found.add((table, "DELETE"))
        allowed = {(table, permission) for table, permissions in EXPECTED_RUNTIME_PERMISSIONS.items()
                   for permission in permissions}
        self.assertTrue(found)
        self.assertEqual(found - allowed, set())
        self.assertEqual({table for table, _ in found}, set(EXPECTED_RUNTIME_PERMISSIONS))
        self.assertNotIn(("SchemaVersions", "INSERT"), found)
        self.assertNotIn(("Connections", "INSERT"), found)
        self.assertIn(("Connections", "DELETE"), found)
        self.assertIn(("SessionRotationCandidates", "DELETE"), found)


class ReconcileTests(unittest.TestCase):
    def test_requested_disconnect_only_releases_capacity_after_confirmed_cm_deletion(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        profiles = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        class Store:
            def __init__(self): self.released = []
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self):
                return [{"profileName": "graph", "providerName": "graph-a", "connectionId": "user",
                    "tenantId": TENANT, "objectId": OWNER, "state": "deleting", "version": b"v"}]
            def finish_disconnect(self, *args): self.released.append(args)
        for confirmed in [False, True]:
            with self.subTest(confirmed=confirmed):
                store, deletions = Store(), []
                def delete(path):
                    deletions.append(path)
                    return confirmed
                result = reconcile(store, lambda _: (200, {"value": []}), resource,
                    profiles=profiles, execute=True, delete_connection=delete)
                self.assertTrue(result["cmDeletionPerformed"])
                self.assertEqual(int(confirmed), result["deletedConnections"])
                self.assertEqual(int(confirmed), len(store.released))
                self.assertEqual(1, len(deletions))
    def test_pool_inventory_rejects_shared_physical_providers(self):
        profiles = {"graph-delegated": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        self.assertEqual(pool_inventory(profiles), [("graph-delegated", "graph-a", 8000)])
        profiles["another"] = copy.deepcopy(profiles["graph-delegated"])
        with self.assertRaises(ValueError): pool_inventory(profiles)
        with self.assertRaises(ValueError): _identifier("runtime]; DROP TABLE x;--")

    def test_reconcile_is_read_only_by_default_and_never_reclaims_on_unknown_status(self):
        class Store:
            def __init__(self): self.writes = []
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self):
                return [{"profileName": "graph-delegated", "providerName": "graph-" + str(i), "connectionId": "user-" + str(i),
                         "tenantId": TENANT, "objectId": OWNER, "version": b"v"} for i in range(3)]
            def observe_connection(self, *args): self.writes.append(args)
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        profiles = {"graph-delegated": {"pools": [{"name": "graph-" + str(i), "capacity": 8000} for i in range(3)]}}
        def arm(path):
            provider = path.split("/")[-2]
            if provider == "graph-2": return 403, None
            values = [] if provider == "graph-1" else [{"id": path + "/user-0", "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations", "properties": {"status": "Connected"}}]
            return 200, {"value": values}
        store = Store()
        result = reconcile(store, arm, resource, profiles=profiles)
        self.assertEqual(store.writes, [])
        self.assertEqual((result["present"], result["missing"], result["unknown"]), (1, 1, 1))
        reconcile(store, arm, resource, profiles=profiles, execute=True)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual([row[3] for row in store.writes], [False])

    def test_reconcile_rejects_untrusted_pagination_before_marking_missing(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        class Store:
            def __init__(self): self.writes = []
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self):
                return [{"profileName": "graph", "providerName": "graph-a", "connectionId": "user", "tenantId": TENANT, "objectId": OWNER, "version": b"v"}]
            def observe_connection(self, *args): self.writes.append(args)
        profiles = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        for link in ("https://evil.invalid/token", "https://management.azure.com/other?x=1", "https://management.azure.com@evil.invalid/token", "https://management.azure.com:443/other?x=1"):
            with self.subTest(link=link):
                calls, store = [], Store()
                def arm(path):
                    calls.append(path)
                    return 200, {"value": [], "nextLink": link}
                result = reconcile(store, arm, resource, profiles=profiles, execute=True)
                self.assertEqual(result["unknown"], 1)
                self.assertEqual(store.writes, [])
                self.assertEqual(len(calls), 1)

    def test_complete_inventory_closes_disconnected_and_counts_unmapped_without_deletion(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        class Store:
            def __init__(self): self.writes, self.blocks = [], []
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def pool_connection_ids(self, profile, provider): return {"user"}
            def block_pool_allocation(self, profile, provider):
                self.blocks.append((profile, provider))
                return True
            def reconciliation_candidates(self):
                return [{"profileName": "graph", "providerName": "graph-a", "connectionId": "user", "tenantId": TENANT, "objectId": OWNER, "version": b"v"},
                        {"profileName": "retired", "providerName": "old-provider", "connectionId": "old-user", "tenantId": TENANT, "objectId": OWNER, "version": b"v"}]
            def observe_connection(self, *args): self.writes.append(args)
        profiles = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        path = resource + "/authorizationProviders/graph-a/authorizations"
        def arm(request):
            self.assertTrue(request.startswith(path))
            name, state = ("user", "Error") if "?" not in request else ("orphan", "Connected")
            result = {"value": [{"id": path + "/" + name, "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations", "properties": {"status": state}}]}
            if state == "Error":
                result["value"][0]["properties"]["error"] = {"code": "invalid_grant"}
            if "?" not in request: result["nextLink"] = "https://management.azure.com" + path + "?$skiptoken=opaque"
            return 200, result
        store = Store()
        result = reconcile(store, arm, resource, profiles=profiles, execute=True)
        self.assertEqual((result["disconnected"], result["unknown"], result["unmappedCmConnections"]), (1, 1, 1))
        self.assertEqual(len(store.writes), 1)
        self.assertFalse(store.writes[0][3])
        self.assertFalse(result["cmDeletionPerformed"])
        self.assertEqual(result["poolsQuarantined"], 1)
        self.assertEqual(store.blocks, [("graph", "graph-a")])

    def test_unknown_cm_failure_does_not_invalidate_connection_or_advance_its_epoch(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        path = resource + "/authorizationProviders/graph-a/authorizations"
        profiles = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        class Store:
            def __init__(self): self.state, self.epoch, self.writes = "ready", 3, []
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self):
                return [{"profileName": "graph", "providerName": "graph-a", "connectionId": "user",
                    "tenantId": TENANT, "objectId": OWNER, "state": self.state, "version": b"v"}]
            def pool_connection_ids(self, *args): return {"user"}
            def observe_connection(self, *args):
                self.writes.append(args)
                self.state, self.epoch = "unverified", self.epoch + 1
        for properties in [{"status": "Error"}, {"status": "Error", "error": {"code": "BackendTimeout"}},
                {"status": "Pending"}, {}, {"status": None}, {"status": {}}, {"status": []}]:
            with self.subTest(properties=properties):
                store = Store()
                entry = {"id": path + "/user", "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations",
                    "properties": properties}
                result = reconcile(store, lambda _: (200, {"value": [entry]}), resource, profiles=profiles, execute=True)
                self.assertEqual((1, 0, 0), (result["unknown"], result["disconnected"], result["changed"]))
                self.assertEqual(("ready", 3, []), (store.state, store.epoch, store.writes))

    def test_refreshed_concurrent_mapping_is_not_quarantined_and_normal_scan_does_not_unblock(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        path = resource + "/authorizationProviders/graph-a/authorizations"
        item = {"id": path + "/recent-user", "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations", "properties": {"status": "Connected"}}
        class Store:
            allocation_blocked = True
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self): return []
            def pool_connection_ids(self, profile, provider): return {"recent-user"}
            def block_pool_allocation(self, *args): raise AssertionError("Concurrent mapping is tracked")
        store = Store()
        result = reconcile(store, lambda _: (200, {"value": [item]}), resource,
            profiles={"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}, execute=True)
        self.assertEqual(result["unmappedCmConnections"], 0)
        self.assertEqual(result["poolsQuarantined"], 0)
        self.assertTrue(store.allocation_blocked)

    def test_incorrect_resource_id_type_or_duplicate_invalidates_inventory(self):
        resource = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
        path = resource + "/authorizationProviders/graph-a/authorizations"
        valid = {"id": path + "/user", "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations", "properties": {"status": "Connected"}}
        class Store:
            def purge_expired_state(self, **kwargs): return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
            def reconciliation_candidates(self):
                return [{"profileName": "graph", "providerName": "graph-a", "connectionId": "user", "tenantId": TENANT, "objectId": OWNER, "version": b"v"}]
            def observe_connection(self, *args): raise AssertionError("Unsafe write")
        profiles = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}
        for values in ([{**valid, "id": path + "/../user"}], [{**valid, "type": "other"}], [{**valid, "type": None}], [valid, valid]):
            result = reconcile(Store(), lambda _: (200, {"value": values}), resource, profiles=profiles, execute=True)
            self.assertEqual(result["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
