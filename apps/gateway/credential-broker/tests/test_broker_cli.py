"""SQL runtime grants and read-only Credential Manager reconciliation."""
import copy
from pathlib import Path
import tempfile
import unittest

from credential_broker.cli import migrate, pool_inventory, reconcile, _identifier
from credential_broker.store import StoreConflict
from _store_support import OWNER, TENANT, fixture


class MigrationTests(unittest.TestCase):
    def test_migration_creates_client_id_bound_runtime_with_data_roles_and_proc_execute(self):
        store, cursor = fixture([
            ("SELECT CASE WHEN OBJECT_ID", {"exists": 0}), ("SELECT schemafixture", None),
            ("SELECT secondfixture", None), ("SELECT sid,type", []), ("CREATE USER", None),
            ("SELECT IS_ROLEMEMBER", {"member": 0}), ("ALTER ROLE [db_datareader]", None),
            ("SELECT IS_ROLEMEMBER", {"member": 0}), ("ALTER ROLE [db_datawriter]", None),
            ("GRANT EXECUTE ON OBJECT::broker.AllocateConnection", None),
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
            ("SELECT IS_ROLEMEMBER", {"member": 1}), ("SELECT IS_ROLEMEMBER", {"member": 1}),
            ("GRANT EXECUTE", None), ("BEGIN TRANSACTION", None), ("UPDATE broker.Pools SET Enabled=0", None),
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
