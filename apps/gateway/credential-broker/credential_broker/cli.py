"""Schema bootstrap and read-first CM reconciliation for the SQL broker.

The migration job uses the separate SQL Entra administrator identity. Runtime
connections use a contained Entra user with explicit object permissions. Neither
command accepts a SQL password or emits OAuth tokens or user display names.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit

from .store import SqlStore, StoreError, StoreConflict, StoreUnavailable, _name
from .cm import connection_metadata_status, delete_authorization


SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "001_initial.sql"
CLEANUP_BATCH_SIZE = 100
CLEANUP_IDLE_SECONDS = 60 * 24 * 60 * 60
CLEANUP_BUDGET_SECONDS = 300

# Runtime includes the web application and its reconciliation job. Schema and
# pool initialization remain with the separate migration identity. In particular,
# schema-version writes and direct connection insertion are not runtime grants;
# AllocateConnection inserts the mapping through its same-owner stored procedure.
RUNTIME_TABLE_PERMISSIONS = {
    "SchemaVersions": "SELECT",
    "Pools": "SELECT, UPDATE",
    "Connections": "SELECT, UPDATE, DELETE",
    "Transactions": "SELECT, INSERT, UPDATE, DELETE",
    "Sessions": "SELECT, INSERT, UPDATE, DELETE",
    "SubjectSecurity": "SELECT, INSERT, UPDATE",
    "SessionRotationCandidates": "SELECT, INSERT, DELETE",
}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("Invalid configured SQL principal name")
    return "[" + value + "]"


def pool_inventory(profiles):
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Configured credential pools are required")
    rows, providers = [], set()
    for profile, settings in profiles.items():
        profile = _name(profile)
        pools = settings.get("pools") if isinstance(settings, dict) else None
        if not isinstance(pools, list) or not pools:
            raise ValueError("A profile requires configured physical pools")
        for pool in pools:
            if not isinstance(pool, dict):
                raise ValueError("A physical credential pool must be an object")
            name = _name(pool.get("name"))
            capacity = pool.get("capacity")
            if name in providers or type(capacity) is not int or not 1 <= capacity <= 10000:
                raise ValueError("Invalid or shared credential pool capacity")
            providers.add(name)
            rows.append((profile, name, capacity))
    return rows


def grant_runtime_access(store, *, runtime_user_name, runtime_sid):
    """Atomically replace the dedicated runtime user's broad data-role grants."""
    name = _identifier(runtime_user_name)
    with store._db(transaction=True) as cursor:
        # Recheck in this transaction, after schema initialization. A replacement
        # principal must not receive grants merely because its name still matches.
        cursor.execute("SELECT sid,type FROM sys.database_principals WHERE name=?", runtime_user_name)
        existing = cursor.fetchone()
        if existing is None or bytes(existing[0]) != runtime_sid or existing[1] != "E":
            raise StoreConflict("Existing SQL runtime principal differs from the configured identity")
        for table, permissions in RUNTIME_TABLE_PERMISSIONS.items():
            cursor.execute("GRANT " + permissions + " ON OBJECT::broker." + _identifier(table) + " TO " + name)
        cursor.execute("GRANT EXECUTE ON OBJECT::broker.AllocateConnection TO " + name)
        # Grant the required objects before dropping the old memberships. The
        # transaction keeps a repeated deployment from exposing a partial grant set.
        for role in ("db_datareader", "db_datawriter"):
            cursor.execute("SELECT IS_ROLEMEMBER(?,?)", role, runtime_user_name)
            membership = cursor.fetchone()
            if membership is None or membership[0] not in (0, 1):
                raise StoreConflict("SQL runtime role membership is not known")
            if membership[0] == 1:
                cursor.execute("ALTER ROLE " + _identifier(role) + " DROP MEMBER " + name)


def migrate(store, *, runtime_client_id, runtime_user_name, profiles, schema_path=SCHEMA):
    """Create versioned schema, seed capacity, and grant the runtime Entra user."""
    # WITH SID/TYPE=E uses the application (client) ID for a managed identity.
    # Its service-principal object ID is used for Azure RBAC, not this SQL SID.
    sid = uuid.UUID(runtime_client_id)
    if sid.int == 0:
        raise ValueError("Runtime identity must be a nonzero client ID")
    name = _identifier(runtime_user_name)
    pools = pool_inventory(profiles)
    migrations = sorted(Path(schema_path).parent.glob('[0-9][0-9][0-9]_*.sql'))
    if not migrations or len({int(path.name[:3]) for path in migrations}) != len(migrations):
        raise ValueError("Ordered schema migrations are required")
    # 適用済みの版は再実行しない。特に002の旧証明回収を通常の再配備で繰り返さない。
    with store._db() as cursor:
        cursor.execute("SELECT CASE WHEN OBJECT_ID(N'broker.SchemaVersions',N'U') IS NULL THEN 0 ELSE 1 END")
        applied = set()
        if cursor.fetchone()[0]:
            cursor.execute("SELECT Version FROM broker.SchemaVersions")
            applied = {int(row[0]) for row in cursor.fetchall()}
        if any(version > 3 for version in applied):
            raise StoreConflict("Database schema is newer than this application")
        for path in migrations:
            if int(path.name[:3]) in applied:
                continue
            batches = [batch.strip() for batch in re.split(r"(?im)^GO\s*$", path.read_text(encoding="utf-8")) if batch.strip()]
            for batch in batches:
                cursor.execute(batch)
                while cursor.nextset():
                    pass
        cursor.execute("SELECT sid,type FROM sys.database_principals WHERE name=?", runtime_user_name)
        existing = cursor.fetchone()
        raw_sid = sid.bytes_le
        if existing is None:
            cursor.execute("CREATE USER " + name + " WITH SID=0x" + raw_sid.hex() + ", TYPE=E;")
        elif bytes(existing[0]) != raw_sid or existing[1] != "E":
            raise StoreConflict("Existing SQL runtime principal differs from the configured identity")
    grant_runtime_access(store, runtime_user_name=runtime_user_name, runtime_sid=raw_sid)
    with store._db(transaction=True) as cursor:
        # Disable only new allocation into retired pools; existing user mappings
        # keep their provider and can still be used or reauthenticated.
        cursor.execute("UPDATE broker.Pools SET Enabled=0")
        for profile, provider, capacity in pools:
            cursor.execute("SELECT Allocated FROM broker.Pools WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND ProviderName=?", profile, provider)
            existing = cursor.fetchone()
            if existing is None:
                cursor.execute("INSERT broker.Pools(ProfileName,ProviderName,Capacity,Enabled) VALUES(?,?,?,1)", profile, provider, capacity)
            else:
                if existing[0] > capacity:
                    raise StoreConflict("New pool capacity is below its existing allocation")
                cursor.execute("UPDATE broker.Pools SET Capacity=?,Enabled=1 WHERE ProfileName=? AND ProviderName=?", capacity, profile, provider)
    store.ensure_ready()
    return {"status": "ready", "schemaVersion": 3, "poolCount": len(pools)}


def observe_schema_before_migration(store):
    """同じ配備用接続でDDL前のメタデータだけを記録し、不明を空DBとしない。"""
    with store._db() as cursor:
        cursor.execute("SELECT CASE WHEN HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','VIEW DEFINITION')=1 "
                       "THEN (SELECT COUNT(*) FROM sys.tables WHERE is_ms_shipped=0) ELSE NULL END, "
                       "CASE WHEN OBJECT_ID(N'broker.SchemaVersions',N'U') IS NULL THEN 0 ELSE 1 END")
        row = cursor.fetchone()
    if (row is None or len(row) != 2 or type(row[0]) is not int or row[0] < 0
            or type(row[1]) is not int or row[1] not in (0, 1)):
        raise StoreUnavailable("Initial schema metadata could not be verified")
    return {"stage": "before-migration", "userTableCount": row[0], "migrationTablePresent": bool(row[1])}


def _inventory(arm, path):
    """Read a complete pool before treating an absent connection as missing."""
    items, seen, request = {}, set(), path
    for _ in range(10000):
        if request in seen:
            return None
        seen.add(request)
        status, response = arm(request)
        if status != 200 or not isinstance(response, dict) or not isinstance(response.get("value"), list):
            return None
        for value in response["value"]:
            if not isinstance(value, dict):
                return None
            resource = value.get("id")
            kind = value.get("type")
            if (not isinstance(resource, str) or not resource.lower().startswith(path.lower() + "/")
                    or resource[len(path) + 1:].count("/") != 0
                    or not isinstance(kind, str) or kind.lower() != "microsoft.apimanagement/service/authorizationproviders/authorizations"):
                return None
            try:
                _name(resource[len(path) + 1:])
            except ValueError:
                return None
            normalized = resource.lower()
            if normalized in items:
                return None
            items[normalized] = value
        next_link = response.get("nextLink")
        if not next_link:
            return items
        if not isinstance(next_link, str):
            return None
        try:
            parsed = urlsplit(next_link)
        except ValueError:
            return None
        # Never send the managed identity bearer token to a returned host or
        # to a different resource. ARM continuation tokens remain opaque.
        if (parsed.scheme != "https" or parsed.netloc != "management.azure.com"
                or parsed.path.lower() != path.lower() or parsed.fragment or not parsed.query):
            return None
        request = parsed.path + "?" + parsed.query
    return None


def _cleanup_connections(store, pools, apim_resource_id, delete_connection):
    """Lease one SQL-selected connection at a time within one bounded batch."""
    result = {"attempted": 0, "requested": 0, "deleted": 0, "pending": 0,
              "conflicts": 0, "failed": 0, "budgetExhausted": False}
    deadline = time.monotonic() + CLEANUP_BUDGET_SECONDS
    candidates = store.cleanup_candidates(pools, batch_size=CLEANUP_BATCH_SIZE,
                                          idle_seconds=CLEANUP_IDLE_SECONDS)
    lease_owner = uuid.uuid4().hex
    for candidate in candidates:
        # The limit applies equally to idle cleanup and explicit disconnect
        # retries. A running HTTP attempt may finish after this deadline; no new
        # attempt starts then. Each individual request has a finite timeout.
        if result["attempted"] >= CLEANUP_BATCH_SIZE or time.monotonic() >= deadline:
            result["budgetExhausted"] = True
            break
        result["attempted"] += 1
        try:
            profile, provider = _name(candidate["profileName"]), _name(candidate["providerName"])
            connection = _name(candidate["connectionId"])
            if (profile, provider) not in pools:
                raise StoreConflict("Cleanup pool is not configured")
            claim = store.claim_cleanup(candidate, lease_owner, idle_seconds=CLEANUP_IDLE_SECONDS,
                                        lease_seconds=300)
            identity = ("profileName", "providerName", "connectionId", "tenantId", "objectId")
            if (any(claim[key] != candidate[key] for key in identity)
                    or claim["leaseOwner"] != lease_owner or claim["state"] != "deleting"):
                raise StoreConflict("Cleanup claim identity changed")
            if time.monotonic() >= deadline:
                result["pending"] += 1
                result["budgetExhausted"] = True
                break
            resource_id = apim_resource_id + "/authorizationProviders/" + provider + "/authorizations/" + connection
            result["requested"] += 1
            if delete_connection(resource_id, validate_claim=lambda claim=claim: store.validate_cleanup_claim(claim)) is not True:
                result["pending"] += 1
                continue
            # This transaction rechecks the same lease/version and only then
            # removes the mapping and releases its reserved capacity.
            if store.finish_cleanup(claim) is not True:
                raise StoreConflict("Cleanup completion was not confirmed")
            result["deleted"] += 1
        except StoreConflict:
            result["conflicts"] += 1
        except Exception:
            # A single transport/SQL failure must not starve the remainder of
            # the batch. Logs contain aggregate counts, never bodies or IDs.
            result["failed"] += 1
    return result


def reconcile(store, arm, apim_resource_id, *, profiles, execute=False, delete_connection=None):
    """Compare CM metadata and optionally reclaim SQL-claimed idle/deleting rows.

    Complete CM pool inventories establish absence. Applying closes missing
    or disconnected mappings without freeing their reserved capacity. CM
    connection metadata alone cannot establish the signed-in user's identity.
    """
    if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.ApiManagement/service/[A-Za-z0-9-]+", apim_resource_id):
        raise ValueError("An exact configured APIM resource ID is required")
    pools = {(profile, provider) for profile, provider, _ in pool_inventory(profiles)}
    expired = {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}
    if execute:
        for _ in range(100):
            result = store.purge_expired_state(batch_size=1000)
            for name in expired:
                expired[name] += result[name]
            if all(count < 1000 for count in result.values()):
                break
    inventory = {}
    for _, provider in pools:
        path = apim_resource_id + "/authorizationProviders/" + provider + "/authorizations"
        inventory[provider] = _inventory(arm, path)
    counts = {"present": 0, "missing": 0, "disconnected": 0, "unknown": 0,
              "changed": 0, "conflicts": 0, "unmappedCmConnections": 0, "poolsQuarantined": 0}
    mapped = set()
    for item in store.reconciliation_candidates():
        provider, connection = _name(item["providerName"]), _name(item["connectionId"])
        resource_id = (apim_resource_id + "/authorizationProviders/" + provider + "/authorizations/" + connection).lower()
        mapped.add(resource_id)
        values = inventory.get(provider)
        if (item["profileName"], provider) not in pools or values is None:
            counts["unknown"] += 1
            continue
        value = values.get(resource_id)
        state = connection_metadata_status(value)
        if state == "unavailable":
            counts["unknown"] += 1
            continue
        connected = state == "connected"
        counts["missing" if value is None else "present" if connected else "disconnected"] += 1
        # Healthy records need no write. At 80,000 users this also keeps the
        # scheduled scan bounded by the small number of actual state changes.
        if execute and not connected and item.get("state") not in {"unverified", "deleting"}:
            try:
                store.observe_connection(item["profileName"], item["tenantId"], item["objectId"], connected, item["version"])
                counts["changed"] += 1
            except StoreConflict:
                counts["conflicts"] += 1
    for profile, provider in pools:
        values = inventory[provider]
        if values is None or not any(resource not in mapped for resource in values):
            continue
        # A concurrent first login may have committed after the main snapshot.
        # Refresh this pool before quarantining it. Remaining uncertainty stops
        # new allocation without changing a person's existing session/mapping.
        prefix = (apim_resource_id + "/authorizationProviders/" + provider + "/authorizations/").lower()
        current = {prefix + _name(connection) for connection in store.pool_connection_ids(profile, provider)}
        orphan_count = sum(resource not in current for resource in values)
        counts["unmappedCmConnections"] += orphan_count
        if orphan_count and execute and store.block_pool_allocation(profile, provider):
            counts["poolsQuarantined"] += 1
    # Finish orphan checks against the pre-cleanup inventory before deleting
    # SQL mappings, so our own deletions cannot quarantine a healthy pool.
    cleanup = (_cleanup_connections(store, pools, apim_resource_id, delete_connection)
               if execute and delete_connection is not None else
               {"attempted": 0, "requested": 0, "deleted": 0, "pending": 0,
                "conflicts": 0, "failed": 0, "budgetExhausted": False})
    counts["conflicts"] += cleanup["conflicts"]
    return {"mode": "execute" if execute else "plan", "cmCleanupAttempted": bool(cleanup["requested"]),
            "deletedConnections": cleanup["deleted"], "cleanup": cleanup, "expiredState": expired, **counts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    check = commands.add_parser("reconcile")
    check.add_argument("--execute", "--apply", action="store_true")
    args = parser.parse_args(argv)
    store = SqlStore.from_environment()
    if args.command == "migrate":
        print(json.dumps(observe_schema_before_migration(store)), flush=True)
        result = migrate(store, runtime_client_id=os.environ["SQL_RUNTIME_CLIENT_ID"],
                         runtime_user_name=os.environ["SQL_RUNTIME_USER_NAME"],
                         profiles=json.loads(os.environ["BROKER_PROFILES_JSON"]))
    else:
        import httpx
        from azure.identity import ManagedIdentityCredential
        credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
        apim_id = (os.environ.get("APIM_RESOURCE_ID") or os.environ.get("BROKER_APIM_RESOURCE_ID")
                   or "/subscriptions/" + os.environ["AZURE_SUBSCRIPTION_ID"]
                   + "/resourceGroups/" + os.environ["AZURE_RESOURCE_GROUP"]
                   + "/providers/Microsoft.ApiManagement/service/" + os.environ["APIM_NAME"])
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            def arm(resource):
                try:
                    token = credential.get_token("https://management.azure.com/.default").token
                    response = client.get("https://management.azure.com" + resource
                                          + ("" if "?" in resource else "?api-version=2024-05-01"),
                                          headers={"Authorization": "Bearer " + token})
                    return response.status_code, response.json() if response.status_code == 200 else None
                except Exception:
                    # Scheduling logs contain only aggregate results, not HTTP
                    # bodies, bearer tokens, continuation tokens or user IDs.
                    return 0, None
            def delete_connection(resource, *, validate_claim):
                return delete_authorization(client, credential, resource, validate_claim=validate_claim)
            result = reconcile(store, arm, apim_id, profiles=json.loads(os.environ["BROKER_PROFILES_JSON"]),
                               execute=args.execute, delete_connection=delete_connection)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StoreError, ValueError, KeyError, OSError):
        print(json.dumps({"status": "failed", "error": "Credential ledger operation failed"}))
        raise SystemExit(1)
