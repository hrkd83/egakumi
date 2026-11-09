"""Durable SQL ledger for identities, capacity reservations and one-use proofs.

No production in-memory fallback exists. All caller values are SQL parameters;
Microsoft access/refresh/ID tokens are never part of the ledger contract.
"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import json
import os
import re
import secrets
import struct
import unicodedata
import uuid


class StoreError(RuntimeError):
    pass


class StoreConflict(StoreError):
    pass


class StoreRotationConflict(StoreConflict):
    """他の候補が先に確定した。本人の再認証は要求しない。"""


class StoreRotationExpired(StoreConflict):
    """候補だけが期限切れ。現行セッションを保持して次の業務要求で再取得する。"""


class StoreNotFound(StoreError):
    pass


class StoreNotAuthorized(StoreError):
    pass


class StoreRateLimited(StoreError):
    pass


class StoreUnavailable(StoreError):
    pass


class StoreCapacity(StoreConflict):
    pass


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value):
        raise ValueError("Invalid configured profile or provider")
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid proof hash or transaction identifier")
    return value


def _guid(value):
    try:
        parsed = uuid.UUID(str(value))
        if parsed.int == 0:
            raise ValueError
        return str(parsed)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Invalid verified identity") from None


def _version(value):
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != 8:
        raise ValueError("Invalid connection version")
    return bytes(value)


def connection_name(tenant_id, object_id, profile, upn):
    """Give each new allocation a readable name and independent 128-bit generation."""
    _guid(tenant_id), _guid(object_id), _name(profile)
    if not isinstance(upn, str) or len(upn) > 320:
        raise ValueError("Invalid identity display attribute")
    local = unicodedata.normalize("NFKD", upn.split("@", 1)[0]).encode("ascii", "ignore").decode().lower()
    prefix = re.sub(r"[^a-z0-9]+", "-", local).strip("-")[:47].rstrip("-") or "member"
    return prefix + "-" + secrets.token_hex(16)


def _flow_json(flow):
    def check(value):
        if isinstance(value, dict):
            if any(str(key).lower() in {"access_token", "refresh_token", "id_token"} for key in value):
                raise ValueError("OAuth tokens must remain outside the ledger")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    if not isinstance(flow, dict):
        raise ValueError("Invalid OIDC flow")
    check(flow)
    encoded = json.dumps(flow, separators=(",", ":"))
    if len(encoded.encode()) > 32768:
        raise ValueError("OIDC flow is too large")
    return encoded


_FIELDS = {
    "TransactionId": "transactionId", "ProfileName": "profileName", "SubscriptionId": "subscriptionId",
    "PollHash": "pollHash", "LaunchHash": "launchHash", "CookieHash": "cookieHash",
    "OidcStateHash": "oidcStateHash", "OidcFlow": "flow", "CmStateHash": "cmStateHash",
    "TicketHash": "ticketHash", "Phase": "phase", "TenantId": "tenantId", "ObjectId": "objectId",
    "UserPrincipalName": "upn", "ProviderName": "providerName", "ConnectionId": "connectionId",
    "ExpiresAt": "expiresAt", "CreatedAt": "createdAt",
    "FailureCode": "failureCode", "State": "state", "LeaseOwner": "leaseOwner", "LeaseUntil": "leaseUntil",
    "IsNew": "isNew", "LeaseOwned": "leaseOwned", "LastUsedAt": "lastUsedAt",
    "LastCheckedAt": "lastCheckedAt", "SessionId": "sessionId", "SecretHash": "secretHash",
    "Revoked": "revoked", "Version": "version", "Capacity": "capacity", "Allocated": "allocated", "Enabled": "enabled",
    "AllocationBlocked": "allocationBlocked",
    "RequiredTenantId": "requiredTenantId", "RequiredObjectId": "requiredObjectId", "ForceCmLogin": "forceCmLogin",
    "ConnectionState": "connectionState",
    "PendingSessionId": "pendingSessionId", "PendingSessionSecretHash": "pendingSessionSecretHash",
    "SubjectEpochAtVerification": "subjectEpochAtVerification",
    "ConnectionEpochAtVerification": "connectionEpochAtVerification",
    "AuthenticatedAt": "authenticatedAt", "LastBusinessUsedAt": "lastBusinessUsedAt",
    "IdleExpiresAt": "idleExpiresAt", "AbsoluteExpiresAt": "absoluteExpiresAt",
    "IdleTimeoutSeconds": "idleTimeoutSeconds", "RotationIntervalSeconds": "rotationIntervalSeconds",
    "RotationGraceSeconds": "rotationGraceSeconds", "RotationHandoffSeconds": "rotationHandoffSeconds",
    "RotationCandidateTtlSeconds": "rotationCandidateTtlSeconds", "Generation": "generation",
    "NextRotationAt": "nextRotationAt", "RotationDeadlineAt": "rotationDeadlineAt",
    "PreviousSecretHash": "previousSecretHash", "PreviousValidUntil": "previousValidUntil",
    "LastCommittedRotationId": "lastCommittedRotationId",
    "SubjectEpochAtAuthentication": "subjectEpochAtAuthentication",
    "ConnectionEpochAtAuthentication": "connectionEpochAtAuthentication",
    "SubjectEpoch": "subjectEpoch", "ConnectionEpoch": "connectionEpoch",
    "RevokedAt": "revokedAt", "RevocationReason": "revocationReason",
    "RotationId": "rotationId", "FromGeneration": "fromGeneration", "SecurityEpoch": "securityEpoch",
    "RevocationEpoch": "revocationEpoch",
}


def _row(cursor, row=None):
    row = cursor.fetchone() if row is None else row
    if row is None:
        return None
    result = {_FIELDS.get(column[0], column[0]): value for column, value in zip(cursor.description, row)}
    for key in ("tenantId", "objectId", "requiredTenantId", "requiredObjectId"):
        if result.get(key) is not None:
            result[key] = str(result[key]).lower()
    if result.get("objectId"):
        result["ownerId"] = result["objectId"]
    return result


class SqlStore:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    @classmethod
    def from_environment(cls):
        server, database, client_id = (os.environ.get(key, "") for key in
                                       ("AZURE_SQL_SERVER", "AZURE_SQL_DATABASE", "AZURE_CLIENT_ID"))
        if not re.fullmatch(r"[A-Za-z0-9.-]+\.database\.windows\.net", server):
            raise ValueError("AZURE_SQL_SERVER must be the configured Azure SQL hostname")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", database):
            raise ValueError("AZURE_SQL_DATABASE is invalid")
        client_id = _guid(client_id)
        import pyodbc
        from azure.identity import ManagedIdentityCredential
        credential = ManagedIdentityCredential(client_id=client_id)
        connection_string = ("Driver={ODBC Driver 18 for SQL Server};Server=tcp:" + server
                             + ",1433;Database=" + database
                             + ";Encrypt=yes;TrustServerCertificate=no;Connection Timeout=15;")
        def connect():
            token = credential.get_token("https://database.windows.net/.default").token.encode("utf-16-le")
            packed = struct.pack("<I", len(token)) + token
            return pyodbc.connect(connection_string, attrs_before={1256: packed}, autocommit=True)
        return cls(connect)

    @contextmanager
    def _db(self, transaction=False):
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            if transaction:
                cursor.execute("SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE; BEGIN TRANSACTION;")
            yield cursor
            if transaction:
                cursor.execute("COMMIT TRANSACTION;")
        except (StoreError, ValueError):
            if transaction and cursor is not None:
                try:
                    cursor.execute("IF XACT_STATE()<>0 ROLLBACK TRANSACTION;")
                except Exception:
                    pass
            raise
        except Exception as error:
            if transaction and cursor is not None:
                try:
                    cursor.execute("IF XACT_STATE()<>0 ROLLBACK TRANSACTION;")
                except Exception:
                    pass
            internal = str(error)
            if re.search(r"\b51001\b", internal):
                raise StoreCapacity("Credential pool capacity is exhausted") from None
            if re.search(r"\b51002\b", internal):
                raise StoreConflict("Credential connection is being deleted") from None
            if re.search(r"\b1205\b", internal):
                raise StoreUnavailable("Concurrent ledger operation must be retried") from None
            if re.search(r"\b(?:2601|2627)\b", internal):
                raise StoreConflict("Concurrent ledger operation must be retried") from None
            raise StoreUnavailable("Credential ledger operation failed") from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _now(cursor):
        cursor.execute("SELECT DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME()) AS Now;")
        return int(cursor.fetchone()[0])

    @staticmethod
    def _transaction(cursor, tx, lock=False):
        cursor.execute("SELECT * FROM broker.Transactions" + (" WITH (UPDLOCK,HOLDLOCK)" if lock else "")
                       + " WHERE TransactionId=?", _hash(tx))
        value = _row(cursor)
        if value is None:
            raise StoreNotFound("Credential transaction was not found")
        return value

    @staticmethod
    def _active(value, now, phases):
        if value["expiresAt"] <= now or value["phase"] not in phases:
            raise StoreConflict("Credential transaction is unavailable")

    def ensure_ready(self):
        with self._db() as cursor:
            cursor.execute("SELECT MAX(Version) FROM broker.SchemaVersions;")
            if cursor.fetchone()[0] != 3:
                raise StoreUnavailable("Credential ledger schema is not ready")
            cursor.execute("SELECT TOP(0) ProfileName,ProviderName,AllocationBlocked FROM broker.Pools;")
            cursor.execute("SELECT TOP(0) PendingSessionId,PendingSessionSecretHash,SubjectEpochAtVerification,ConnectionEpochAtVerification FROM broker.Transactions;")
            cursor.execute("SELECT TOP(0) IdleExpiresAt,AbsoluteExpiresAt,Generation FROM broker.Sessions;")

    def health(self):
        try:
            self.ensure_ready()
            return True
        except StoreError:
            return False

    def create_transaction(self, tx, profile, subscription_id, poll_hash, launch_hash, expires_at,
                           *, pending_session_id, pending_secret_hash, login_limit=10):
        return self.create_challenge(tx, profile, subscription_id, poll_hash, launch_hash, expires_at,
            pending_session_id=pending_session_id, pending_secret_hash=pending_secret_hash, login_limit=login_limit)

    def create_challenge(self, tx, profile, subscription_id, poll_hash, launch_hash, expires_at,
                         *, pending_session_id, pending_secret_hash, source_session=None,
                         force_cm_login=False, login_limit=10):
        """認証取引と新しい仮証明を分離する。古い証明は本人の制約にだけ利用する。"""
        tx, profile, poll_hash, launch_hash = _hash(tx), _name(profile), _hash(poll_hash), _hash(launch_hash)
        pending_session_id, pending_secret_hash = _hash(pending_session_id), _hash(pending_secret_hash)
        if not isinstance(subscription_id, str) or not 1 <= len(subscription_id) <= 256:
            raise ValueError("Invalid verified subscription")
        if type(force_cm_login) is not bool or force_cm_login and source_session is None:
            raise ValueError("A reauthentication attempt requires an existing proof")
        if type(login_limit) is not int or not 1 <= login_limit <= 10000:
            raise ValueError("Invalid login rate limit")
        with self._db(transaction=True) as cursor:
            now = self._now(cursor)
            if not now < expires_at <= now + 600:
                raise ValueError("Invalid transaction lifetime")
            required_tenant = required_owner = None
            if source_session is not None:
                session_id, secret_hash = (_hash(item) for item in source_session)
                cursor.execute("SELECT * FROM broker.Sessions WITH (UPDLOCK,HOLDLOCK) WHERE SessionId=?", session_id)
                source = _row(cursor)
                # 失効した証明も本人を限定することだけは可能。利用許可・延長は一切行わない。
                if (source is None or not hmac.compare_digest(source['secretHash'], secret_hash)
                        and not (source.get('previousSecretHash') and source.get('previousValidUntil', 0) > now
                                 and hmac.compare_digest(source['previousSecretHash'], secret_hash))
                        or source['subscriptionId'] != subscription_id or source['profileName'] != profile):
                    raise StoreNotAuthorized("Credential proof is unavailable")
                required_tenant, required_owner = source['tenantId'], source['objectId']
                cursor.execute("SELECT State FROM broker.Connections WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=?",
                               profile, required_tenant, required_owner)
                connection_state = cursor.fetchone()
                if connection_state is not None and connection_state[0] == 'deleting':
                    raise StoreConflict("Connection is being deleted")
                if force_cm_login:
                    if connection_state is not None:
                        cursor.execute("UPDATE broker.Connections SET State='unverified',SecurityEpoch=SecurityEpoch+1 WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND State<>'deleting'",
                                       profile, required_tenant, required_owner)
                        if cursor.rowcount != 1:
                            raise StoreConflict("Connection is unavailable")
                    cursor.execute("UPDATE broker.Sessions SET Revoked=1,RevokedAt=?,RevocationReason='REAUTHENTICATION_REQUIRED' WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND Revoked=0",
                                   now, profile, required_tenant, required_owner)
            cursor.execute("SELECT COUNT(*) FROM broker.Transactions WITH (UPDLOCK,HOLDLOCK,INDEX(IX_BrokerTransactionSubscriptionCreated)) WHERE SubscriptionId=? AND CreatedAt>?",
                           subscription_id, now - 60)
            if cursor.fetchone()[0] >= login_limit:
                raise StoreRateLimited("Credential login rate limit reached")
            cursor.execute("INSERT broker.Transactions(TransactionId,ProfileName,SubscriptionId,PollHash,LaunchHash,Phase,ExpiresAt,CreatedAt,RequiredTenantId,RequiredObjectId,ForceCmLogin,PendingSessionId,PendingSessionSecretHash) VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
                           tx, profile, subscription_id, poll_hash, launch_hash, int(expires_at), now,
                           required_tenant, required_owner, force_cm_login, pending_session_id, pending_secret_hash)
            return self._transaction(cursor, tx)

    def get_transaction(self, tx):
        try:
            with self._db() as cursor:
                result = self._transaction(cursor, tx)
                result.pop("flow", None)
                return result
        except StoreNotFound:
            return None

    def consume_launch(self, tx, launch_hash, cookie_hash, oidc_state_hash, flow):
        launch_hash, cookie_hash, oidc_state_hash = _hash(launch_hash), _hash(cookie_hash), _hash(oidc_state_hash)
        encoded = _flow_json(flow)
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            self._active(value, self._now(cursor), {"pending"})
            if value["launchHash"] != launch_hash:
                raise StoreConflict("Credential transaction is unavailable")
            cursor.execute("UPDATE broker.Transactions SET Phase='identityPending',LaunchHash=NULL,CookieHash=?,OidcStateHash=?,OidcFlow=? WHERE TransactionId=?",
                           cookie_hash, oidc_state_hash, encoded, tx)
            return {**value, "phase": "identityPending"}

    def consume_identity(self, tx, cookie_hash, oidc_state_hash):
        cookie_hash, oidc_state_hash = _hash(cookie_hash), _hash(oidc_state_hash)
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            self._active(value, self._now(cursor), {"identityPending"})
            if value["cookieHash"] != cookie_hash or value["oidcStateHash"] != oidc_state_hash:
                raise StoreConflict("Credential transaction is unavailable")
            flow = json.loads(value["flow"])
            cursor.execute("UPDATE broker.Transactions SET Phase='identityProcessing',OidcFlow=NULL,OidcStateHash=NULL WHERE TransactionId=?", tx)
            return {**value, "phase": "identityProcessing", "flow": flow}

    def set_identity(self, tx, tenant_id, object_id, upn):
        tenant_id, object_id = _guid(tenant_id), _guid(object_id)
        if not isinstance(upn, str) or len(upn) > 320:
            raise ValueError("Invalid identity display attribute")
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            now = self._now(cursor)
            self._active(value, now, {'identityProcessing'})
            cursor.execute("SELECT RevocationEpoch FROM broker.SubjectSecurity WITH (UPDLOCK,HOLDLOCK) WHERE TenantId=? AND ObjectId=?", tenant_id, object_id)
            existing = cursor.fetchone()
            epoch = int(existing[0]) if existing else 0
            if existing is None:
                cursor.execute("INSERT broker.SubjectSecurity(TenantId,ObjectId,RevocationEpoch,UpdatedAt) VALUES(?,?,0,?)", tenant_id, object_id, now)
            cursor.execute("UPDATE broker.Transactions SET Phase='identityVerified',TenantId=?,ObjectId=?,UserPrincipalName=?,SubjectEpochAtVerification=? WHERE TransactionId=?",
                           tenant_id, object_id, upn, epoch, tx)
            return {**value, 'phase': 'identityVerified', 'tenantId': tenant_id,
                    'objectId': object_id, 'ownerId': object_id, 'upn': upn, 'subjectEpochAtVerification': epoch}

    def allocate_connection(self, profile, tenant_id, object_id, upn, lease_owner, lease_seconds=300):
        profile, tenant_id, object_id = _name(profile), _guid(tenant_id), _guid(object_id)
        if not isinstance(lease_owner, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", lease_owner) or not 30 <= lease_seconds <= 600:
            raise ValueError("Invalid reservation lease")
        # The procedure uses this candidate only for a new mapping; existing IDs stay unchanged.
        name = connection_name(tenant_id, object_id, profile, upn)
        with self._db() as cursor:
            cursor.execute("EXEC broker.AllocateConnection ?,?,?,?,?,?,?", profile, tenant_id, object_id, upn, name, lease_owner, lease_seconds)
            result = _row(cursor)
            if result is None:
                raise StoreUnavailable("Credential reservation was not returned")
            return result

    def set_connection_state(self, profile, tenant_id, object_id, state, lease_owner):
        if state not in {"pending", "unverified", "deleting"}:
            raise ValueError("Connection readiness requires verified completion")
        with self._db(transaction=True) as cursor:
            now = self._now(cursor)
            cursor.execute("UPDATE broker.Connections SET State=? WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND LeaseOwner=? AND LeaseUntil>? AND State IN ('reserved','pending','unverified')",
                           state, _name(profile), _guid(tenant_id), _guid(object_id), lease_owner, now)
            if cursor.rowcount != 1:
                raise StoreConflict("Credential reservation lease is unavailable")
            cursor.execute("SELECT * FROM broker.Connections WHERE ProfileName=? AND TenantId=? AND ObjectId=?", profile, tenant_id, object_id)
            return _row(cursor)

    def prepare_cm_callback(self, tx, cookie_hash, state_hash):
        cookie_hash, state_hash = _hash(cookie_hash), _hash(state_hash)
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            now = self._now(cursor)
            self._active(value, now, {"identityVerified"})
            if value["cookieHash"] != cookie_hash:
                raise StoreConflict("Credential browser proof is unavailable")
            cursor.execute("SELECT * FROM broker.Connections WITH (UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=?",
                           value["profileName"], value["tenantId"], value["objectId"])
            connection = _row(cursor)
            if connection is None or connection["state"] not in {"ready", "pending", "unverified"}:
                raise StoreConflict("Credential connection is unavailable")
            if connection["state"] != "ready" and (connection["leaseOwner"] != tx or connection["leaseUntil"] <= now):
                raise StoreConflict("Credential reservation is being processed")
            cursor.execute("UPDATE broker.Transactions SET Phase='cmPending',CmStateHash=?,ProviderName=?,ConnectionId=?,ConnectionEpochAtVerification=? WHERE TransactionId=?",
                           state_hash, connection["providerName"], connection["connectionId"], connection['securityEpoch'], tx)
            return {**value, "phase": "cmPending", "providerName": connection["providerName"], "connectionId": connection["connectionId"]}

    def create_callback_ticket(self, tx, cookie_hash, state_hash, ticket_hash):
        cookie_hash, state_hash, ticket_hash = _hash(cookie_hash), _hash(state_hash), _hash(ticket_hash)
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            self._active(value, self._now(cursor), {"cmPending"})
            if value["cookieHash"] != cookie_hash or value["cmStateHash"] != state_hash:
                raise StoreConflict("Credential callback proof is unavailable")
            cursor.execute("UPDATE broker.Transactions SET Phase='verifying',TicketHash=?,CmStateHash=NULL WHERE TransactionId=?", ticket_hash, tx)
            return {**value, "phase": "verifying"}

    @staticmethod
    def _policy(policy):
        bounds = {'idleTimeoutSeconds': (300, 31536000), 'absoluteLifetimeSeconds': (300, 31536000),
                  'rotationIntervalSeconds': (300, 2592000), 'rotationGraceSeconds': (1, 3600),
                  'rotationHandoffSeconds': (60, 3600), 'rotationCandidateTtlSeconds': (60, 3600)}
        if not isinstance(policy, dict) or set(policy) != set(bounds):
            raise ValueError("Invalid session policy")
        for key, (minimum, maximum) in bounds.items():
            if type(policy[key]) is not int or not minimum <= policy[key] <= maximum:
                raise ValueError("Invalid session policy")
        if policy['idleTimeoutSeconds'] > policy['absoluteLifetimeSeconds']:
            raise ValueError("Idle lifetime exceeds absolute lifetime")
        if (policy['rotationIntervalSeconds'] >= policy['absoluteLifetimeSeconds']
                or policy['rotationGraceSeconds'] >= policy['rotationIntervalSeconds']
                or policy['rotationCandidateTtlSeconds'] > policy['rotationHandoffSeconds']):
            raise ValueError("Invalid session rotation policy")
        return policy

    @staticmethod
    def _metadata(record):
        return {key: record[key] for key in ('sessionId', 'generation', 'authenticatedAt',
                'idleExpiresAt', 'absoluteExpiresAt', 'expiresAt', 'nextRotationAt')}

    def complete_transaction(self, tx, ticket_hash, verified_owner_id, *, policy):
        """CM本人照合の完了を起点に、新しい独立セッションを一度だけ有効にする。"""
        ticket_hash, verified_owner_id = _hash(ticket_hash), _guid(verified_owner_id)
        policy = self._policy(policy)
        with self._db(transaction=True) as cursor:
            value = self._transaction(cursor, tx, True)
            now = self._now(cursor)
            self._active(value, now, {'verifying'})
            if value['ticketHash'] != ticket_hash or value['objectId'] != verified_owner_id:
                raise StoreConflict("Credential owner verification failed")
            cursor.execute("SELECT RevocationEpoch FROM broker.SubjectSecurity WITH(UPDLOCK,HOLDLOCK) WHERE TenantId=? AND ObjectId=?", value['tenantId'], value['objectId'])
            epoch = cursor.fetchone()
            if epoch is None or int(epoch[0]) != value['subjectEpochAtVerification']:
                raise StoreNotAuthorized("Subject was revoked during authentication")
            cursor.execute("SELECT SecurityEpoch FROM broker.Connections WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND State<>'deleting'", value['profileName'], value['tenantId'], value['objectId'], value['providerName'], value['connectionId'])
            connection_epoch = cursor.fetchone()
            if connection_epoch is None or int(connection_epoch[0]) != value['connectionEpochAtVerification']:
                raise StoreNotAuthorized("Connection was invalidated during authentication")
            connection_epoch = int(connection_epoch[0])
            # 本人・接続のロック待ちでも取引期限を超え得る。全ロック取得後に再検証する。
            now = self._now(cursor)
            self._active(value, now, {'verifying'})
            cursor.execute("UPDATE broker.Connections SET State='ready',LeaseOwner=NULL,LeaseUntil=0,LastCheckedAt=? WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND State<>'deleting'",
                           now, value['profileName'], value['tenantId'], value['objectId'], value['providerName'], value['connectionId'])
            if cursor.rowcount != 1:
                raise StoreConflict("Credential mapping changed during verification")
            absolute = now + policy['absoluteLifetimeSeconds']
            idle = min(now + policy['idleTimeoutSeconds'], absolute)
            cursor.execute("INSERT broker.Sessions(SessionId,SecretHash,SubscriptionId,ProfileName,TenantId,ObjectId,ProviderName,ConnectionId,AuthenticatedAt,LastBusinessUsedAt,IdleExpiresAt,AbsoluteExpiresAt,ExpiresAt,IdleTimeoutSeconds,RotationIntervalSeconds,RotationGraceSeconds,RotationHandoffSeconds,RotationCandidateTtlSeconds,Generation,NextRotationAt,SubjectEpochAtAuthentication,ConnectionEpochAtAuthentication,CreatedAt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                value['pendingSessionId'], value['pendingSessionSecretHash'], value['subscriptionId'], value['profileName'],
                value['tenantId'], value['objectId'], value['providerName'], value['connectionId'], now, now, idle, absolute, idle,
                policy['idleTimeoutSeconds'], policy['rotationIntervalSeconds'], policy['rotationGraceSeconds'],
                policy['rotationHandoffSeconds'], policy['rotationCandidateTtlSeconds'], now + policy['rotationIntervalSeconds'],
                value['subjectEpochAtVerification'], connection_epoch, now)
            cursor.execute("UPDATE broker.Transactions SET Phase='complete',TicketHash=NULL,CookieHash=NULL,LaunchHash=NULL,OidcFlow=NULL,OidcStateHash=NULL,CmStateHash=NULL,PendingSessionSecretHash=NULL WHERE TransactionId=?", tx)
            return {'phase': 'complete', 'sessionId': value['pendingSessionId'], 'expiresAt': idle}

    def status(self, tx, poll_hash, subscription_id):
        with self._db() as cursor:
            value = self._transaction(cursor, tx)
            if (value['pollHash'] != _hash(poll_hash) or value['subscriptionId'] != subscription_id
                    or value['expiresAt'] <= self._now(cursor) or value['phase'] == 'failed'):
                raise StoreConflict("Credential transaction is unavailable")
            if value['phase'] == 'complete':
                cursor.execute("SELECT * FROM broker.Sessions WHERE SessionId=? AND SubscriptionId=?", value['pendingSessionId'], subscription_id)
                session = _row(cursor)
                if session is None:
                    raise StoreConflict("Session is unavailable")
                value.update(self._metadata(session))
            # 状態確認から、現行値・候補値・ハッシュを呼出元へ返さない。
            return {**value, 'status': 'complete' if value['phase'] == 'complete' else 'pending'}

    def _session(self, cursor, sid, secret_hash, sub, profile=None, *, lock=False, allow_inactive=False):
        sid, secret_hash = _hash(sid), _hash(secret_hash)
        hint = ' WITH (UPDLOCK,HOLDLOCK)' if lock else ''
        cursor.execute("SELECT s.*,c.State AS ConnectionState,c.SecurityEpoch AS ConnectionEpoch,u.RevocationEpoch AS SubjectEpoch FROM broker.Sessions s" + hint
                       + " LEFT JOIN broker.Connections c ON c.ProfileName=s.ProfileName AND c.TenantId=s.TenantId AND c.ObjectId=s.ObjectId AND c.ProviderName=s.ProviderName AND c.ConnectionId=s.ConnectionId LEFT JOIN broker.SubjectSecurity u ON u.TenantId=s.TenantId AND u.ObjectId=s.ObjectId WHERE s.SessionId=?", sid)
        record = _row(cursor)
        # ロック待ちの間に期限を超える場合があるため、取得後のSQL時刻で判定する。
        now = self._now(cursor)
        if record is None or record['subscriptionId'] != sub or profile is not None and record['profileName'] != _name(profile):
            raise StoreNotAuthorized("Credential proof is unavailable")
        current = hmac.compare_digest(record['secretHash'], secret_hash)
        previous = (record.get('previousSecretHash') is not None and record.get('previousValidUntil', 0) > now
                    and hmac.compare_digest(record['previousSecretHash'], secret_hash))
        if not current and not previous:
            raise StoreNotAuthorized("Credential proof is unavailable")
        active = (not record['revoked'] and record['idleExpiresAt'] > now and record['absoluteExpiresAt'] > now
                  and record['connectionState'] == 'ready'
                  and record['subjectEpoch'] == record['subjectEpochAtAuthentication']
                  and record['connectionEpoch'] == record['connectionEpochAtAuthentication'])
        if not active and not allow_inactive:
            return None, now
        record['credentialKind'] = 'current' if current else 'previous'
        record['updateRequired'] = bool(record.get('rotationDeadlineAt') is not None and record['rotationDeadlineAt'] <= now)
        return record, now

    def resolve_session(self, sid, secret_hash, subscription_id, profile=None):
        with self._db() as cursor:
            record, _ = self._session(cursor, sid, secret_hash, subscription_id, profile)
            return record

    def finalize_business_use(self, sid, secret_hash, subscription_id, profile, verified_owner_id,
                              *, candidate_id, candidate_hash):
        """APIMが本人照合した業務要求だけを確定。失効・期限はSQLで再確認する。"""
        candidate_id, candidate_hash, owner = _hash(candidate_id), _hash(candidate_hash), _guid(verified_owner_id)
        with self._db(transaction=True) as cursor:
            record, now = self._session(cursor, sid, secret_hash, subscription_id, profile, lock=True)
            if record is None:
                return {'allowed': False, 'status': 409, 'errorCode': 'ConnectionRequired'}
            if record['objectId'] != owner:
                raise StoreNotAuthorized("Credential owner mismatch")
            due = record['credentialKind'] == 'current' and now >= record['nextRotationAt']
            if due and record.get('rotationDeadlineAt') is None:
                record['rotationDeadlineAt'] = now + record['rotationHandoffSeconds']
                cursor.execute("UPDATE broker.Sessions SET RotationDeadlineAt=? WHERE SessionId=?", record['rotationDeadlineAt'], sid)
            update_required = due and record['rotationDeadlineAt'] <= now
            if not update_required:
                # 猶予中の並行要求も実利用として数えるが、旧値の猶予自体は延長しない。
                used = max(now, record['lastBusinessUsedAt'])
                idle = min(used + record['idleTimeoutSeconds'], record['absoluteExpiresAt'])
                cursor.execute("UPDATE broker.Sessions SET LastBusinessUsedAt=?,IdleExpiresAt=?,ExpiresAt=? WHERE SessionId=?", used, idle, idle, sid)
                cursor.execute("UPDATE broker.Connections SET LastUsedAt=CASE WHEN LastUsedAt>? THEN LastUsedAt ELSE ? END WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND State='ready' AND SecurityEpoch=?",
                               used, used, profile, record['tenantId'], record['objectId'], record['providerName'], record['connectionId'], record['connectionEpoch'])
                if cursor.rowcount != 1:
                    raise StoreConflict("Credential connection changed during business use")
                record.update(lastBusinessUsedAt=used, idleExpiresAt=idle, expiresAt=idle)
            result = {**record, 'allowed': not update_required, 'status': 409 if update_required else 200}
            if update_required:
                result['errorCode'] = 'SessionUpdateRequired'
            if due:
                cursor.execute("DELETE FROM broker.SessionRotationCandidates WHERE SessionId=? AND ExpiresAt<=?", sid, now)
                cursor.execute("SELECT COUNT(*) FROM broker.SessionRotationCandidates WHERE SessionId=? AND FromGeneration=?", sid, record['generation'])
                if cursor.fetchone()[0] < 4:
                    expiry = min(now + record['rotationCandidateTtlSeconds'], record['idleExpiresAt'], record['absoluteExpiresAt'])
                    cursor.execute("INSERT broker.SessionRotationCandidates(RotationId,SessionId,SecretHash,FromGeneration,CreatedAt,ExpiresAt) VALUES(?,?,?,?,?,?)", candidate_id, sid, candidate_hash, record['generation'], now, expiry)
                    result['rotationCandidate'] = {'rotationId': candidate_id, 'fromGeneration': record['generation'], 'generation': record['generation'] + 1, 'expiresAt': expiry}
            return result

    def commit_rotation(self, sid, current_hash, subscription_id, candidate_id, candidate_hash, from_generation):
        """新値を受領・保存したクライアントだけが切替を確定する。再送は期限を変えない。"""
        candidate_id, candidate_hash = _hash(candidate_id), _hash(candidate_hash)
        if type(from_generation) is not int or not 1 <= from_generation < 9223372036854775807:
            raise ValueError("Invalid generation")
        with self._db(transaction=True) as cursor:
            record, now = self._session(cursor, sid, current_hash, subscription_id, lock=True)
            if record is None:
                raise StoreNotAuthorized("Session expired or revoked")
            if (record.get('lastCommittedRotationId') == candidate_id and record['generation'] == from_generation + 1
                    and hmac.compare_digest(record['secretHash'], candidate_hash)):
                return self._metadata(record)
            if record['credentialKind'] != 'current' or record['generation'] != from_generation:
                raise StoreRotationConflict("Rotation was superseded")
            cursor.execute("SELECT * FROM broker.SessionRotationCandidates WHERE RotationId=? AND SessionId=?", candidate_id, sid)
            candidate = _row(cursor)
            if candidate is None:
                raise StoreRotationExpired("Rotation candidate expired")
            if (candidate['fromGeneration'] != from_generation
                    or not hmac.compare_digest(candidate['secretHash'], candidate_hash)):
                raise StoreNotAuthorized("Rotation proof is unavailable")
            if candidate['expiresAt'] <= now:
                raise StoreRotationExpired("Rotation candidate expired")
            next_rotation = now + record['rotationIntervalSeconds']
            cursor.execute("UPDATE broker.Sessions SET PreviousSecretHash=SecretHash,PreviousValidUntil=?,SecretHash=?,Generation=Generation+1,NextRotationAt=?,RotationDeadlineAt=NULL,LastCommittedRotationId=? WHERE SessionId=?", now + record['rotationGraceSeconds'], candidate_hash, next_rotation, candidate_id, sid)
            cursor.execute("DELETE FROM broker.SessionRotationCandidates WHERE SessionId=?", sid)
            record.update(secretHash=candidate_hash, generation=from_generation + 1, nextRotationAt=next_rotation)
            return self._metadata(record)

    def revoke_session(self, sid, secret_hash, subscription_id):
        with self._db(transaction=True) as cursor:
            record, now = self._session(cursor, sid, secret_hash, subscription_id, lock=True, allow_inactive=True)
            cursor.execute("UPDATE broker.Sessions SET Revoked=1,RevokedAt=COALESCE(RevokedAt,?),RevocationReason='LOGOUT' WHERE SessionId=?", now, sid)
            cursor.execute("DELETE FROM broker.SessionRotationCandidates WHERE SessionId=?", sid)
            return True

    def revoke_subject_sessions(self, sid, secret_hash, subscription_id):
        with self._db(transaction=True) as cursor:
            record, now = self._session(cursor, sid, secret_hash, subscription_id, lock=True)
            if record is None:
                raise StoreNotAuthorized("Session expired or revoked")
            cursor.execute("UPDATE broker.SubjectSecurity SET RevocationEpoch=RevocationEpoch+1,UpdatedAt=? WHERE TenantId=? AND ObjectId=?", now, record['tenantId'], record['objectId'])
            cursor.execute("UPDATE broker.Sessions SET Revoked=1,RevokedAt=?,RevocationReason='SUBJECT_LOGOUT' WHERE TenantId=? AND ObjectId=? AND Revoked=0", now, record['tenantId'], record['objectId'])
            cursor.execute("DELETE r FROM broker.SessionRotationCandidates r JOIN broker.Sessions s ON s.SessionId=r.SessionId WHERE s.TenantId=? AND s.ObjectId=?", record['tenantId'], record['objectId'])
            return True

    def begin_disconnect(self, sid, secret_hash, subscription_id):
        with self._db(transaction=True) as cursor:
            record, now = self._session(cursor, sid, secret_hash, subscription_id, lock=True)
            if record is None:
                raise StoreNotAuthorized("Session expired or revoked")
            cursor.execute("UPDATE broker.Connections SET State='deleting',SecurityEpoch=SecurityEpoch+1,LeaseOwner=NULL,LeaseUntil=0 OUTPUT INSERTED.Version WHERE ProfileName=? AND TenantId=? AND ObjectId=?", record['profileName'], record['tenantId'], record['objectId'])
            changed = cursor.fetchone()
            if changed is None:
                raise StoreConflict("Credential connection changed during disconnect")
            record['connectionVersion'] = changed[0]
            cursor.execute("UPDATE broker.Sessions SET Revoked=1,RevokedAt=?,RevocationReason='DISCONNECT' WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND Revoked=0", now, record['profileName'], record['tenantId'], record['objectId'])
            # 解除前の完了チケットを、新しい割当で有効にしません。
            cursor.execute("UPDATE broker.Transactions SET Phase='failed',TicketHash=NULL,CookieHash=NULL,OidcFlow=NULL,OidcStateHash=NULL,CmStateHash=NULL,PendingSessionSecretHash=NULL,FailureCode='DISCONNECTED' WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND Phase<>'complete'", record['profileName'], record['tenantId'], record['objectId'])
            cursor.execute("DELETE r FROM broker.SessionRotationCandidates r JOIN broker.Sessions s ON s.SessionId=r.SessionId WHERE s.ProfileName=? AND s.TenantId=? AND s.ObjectId=?", record['profileName'], record['tenantId'], record['objectId'])
            return record

    def validate_disconnect(self, mapping):
        key = self._cleanup_key(mapping, version_field='connectionVersion')
        with self._db() as cursor:
            cursor.execute("SELECT Version FROM broker.Connections WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=? AND State='deleting'", *key)
            return cursor.fetchone() is not None

    def finish_disconnect(self, profile, tenant_id, object_id, provider, connection, *, expected_version):
        with self._db(transaction=True) as cursor:
            cursor.execute("SELECT * FROM broker.Connections WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=? AND State='deleting'", _name(profile), _guid(tenant_id), _guid(object_id), _name(provider), _name(connection), _version(expected_version))
            record = _row(cursor)
            if record is None:
                raise StoreConflict("Deletion was superseded")
            cursor.execute("DELETE broker.Connections WHERE ProfileName=? AND TenantId=? AND ObjectId=?", profile, tenant_id, object_id)
            cursor.execute("UPDATE broker.Pools SET Allocated=Allocated-1 WHERE ProfileName=? AND ProviderName=? AND Allocated>0", profile, provider)
            if cursor.rowcount != 1:
                raise StoreConflict("Credential capacity counter needs reconciliation")
            return True

    def fail_transaction(self, tx, code):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", code):
            raise ValueError("Invalid fixed failure code")
        with self._db(transaction=True) as cursor:
            cursor.execute("UPDATE broker.Transactions SET Phase='failed',OidcFlow=NULL,LaunchHash=NULL,OidcStateHash=NULL,TicketHash=NULL,FailureCode=? WHERE TransactionId=? AND Phase<>'complete'", code, _hash(tx))

    def purge_expired_state(self, batch_size=1000):
        """Remove expired transient state without touching a person's CM mapping.

        SQL time decides expiry. Unfinished OIDC proofs are cleared when the
        transaction expires and its row can then be removed independently of
        the continuing session. Expired session proof hashes remain for 24 hours
        only to bind reauthentication to the original user; they never authorize
        an API call. Expired rotation candidates are removed even when their
        session is still active. Small batches bound lock and log growth.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 10000:
            raise ValueError("Invalid transient cleanup batch size")
        with self._db(transaction=True) as cursor:
            now = self._now(cursor)
            cursor.execute("UPDATE TOP (?) broker.Transactions SET Phase='failed',OidcFlow=NULL,LaunchHash=NULL,CookieHash=NULL,OidcStateHash=NULL,CmStateHash=NULL,TicketHash=NULL,FailureCode='EXPIRED' WHERE ExpiresAt<=? AND Phase<>'complete' AND (Phase<>'failed' OR OidcFlow IS NOT NULL OR LaunchHash IS NOT NULL OR CookieHash IS NOT NULL OR OidcStateHash IS NOT NULL OR CmStateHash IS NOT NULL OR TicketHash IS NOT NULL)", batch_size, now)
            expired_proofs = cursor.rowcount
            cursor.execute("DELETE TOP (?) FROM broker.SessionRotationCandidates WHERE ExpiresAt<=?", batch_size, now)
            expired_proofs += cursor.rowcount
            cursor.execute("DELETE TOP (?) FROM broker.Sessions WHERE ExpiresAt<=?", batch_size, now - 86400)
            sessions = cursor.rowcount
            cursor.execute("DELETE TOP (?) FROM broker.Transactions WHERE ExpiresAt<=?", batch_size, now)
            transactions = cursor.rowcount
            return {"expiredProofs": expired_proofs, "deletedSessions": sessions, "deletedTransactions": transactions}

    def reconciliation_candidates(self):
        with self._db() as cursor:
            cursor.execute("SELECT * FROM broker.Connections ORDER BY ProfileName,ProviderName,ConnectionId")
            return [_row(cursor, row) for row in cursor.fetchall()]

    def pool_connection_ids(self, profile, provider):
        """Refresh one pool before classifying an externally observed orphan."""
        with self._db() as cursor:
            cursor.execute("SELECT ConnectionId FROM broker.Connections WHERE ProfileName=? AND ProviderName=?",
                           _name(profile), _name(provider))
            return {row[0] for row in cursor.fetchall()}

    def block_pool_allocation(self, profile, provider):
        """Quarantine new reservations only; operator review is needed to unblock.

        Existing mappings retain their identity and session state. The allocator
        locks this pool row in the same transaction as its capacity reservation.
        """
        with self._db(transaction=True) as cursor:
            cursor.execute("UPDATE broker.Pools SET AllocationBlocked=1 WHERE ProfileName=? AND ProviderName=? AND AllocationBlocked=0",
                           _name(profile), _name(provider))
            return cursor.rowcount == 1

    def observe_connection(self, profile, tenant_id, object_id, exists, expected_version):
        """An absent CM resource invalidates its mapping; it does not free capacity.

        Deletion and pool-counter release need a separately confirmed cleanup
        transaction. A concurrent mapping change causes this observation to fail.
        """
        with self._db(transaction=True) as cursor:
            now = self._now(cursor)
            cursor.execute("UPDATE broker.Connections SET LastCheckedAt=?,State=CASE WHEN ?=0 THEN 'unverified' ELSE State END,SecurityEpoch=SecurityEpoch+CASE WHEN ?=0 THEN 1 ELSE 0 END WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND Version=? AND State<>'deleting'",
                           now, bool(exists), bool(exists), _name(profile), _guid(tenant_id), _guid(object_id), expected_version)
            if cursor.rowcount != 1:
                raise StoreConflict("Credential mapping changed during reconciliation")

    @staticmethod
    def _cleanup_key(record, *, version_field='version'):
        if not isinstance(record, dict) or any(key not in record for key in (
                'profileName', 'tenantId', 'objectId', 'providerName', 'connectionId', version_field)):
            raise ValueError("Invalid cleanup mapping")
        return (_name(record['profileName']), _guid(record['tenantId']), _guid(record['objectId']),
                _name(record['providerName']), _name(record['connectionId']), _version(record[version_field]))

    @staticmethod
    def _cleanup_retention(idle_seconds):
        if type(idle_seconds) is not int or not 1 <= idle_seconds <= 315360000:
            raise ValueError("Invalid cleanup retention")
        return idle_seconds

    def cleanup_candidates(self, pools, *, batch_size=100, idle_seconds=5184000):
        """Read a bounded, fairly ordered set from the exact configured pools."""
        idle_seconds = self._cleanup_retention(idle_seconds)
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("Invalid cleanup batch size")
        configured = sorted({(_name(profile), _name(provider)) for profile, provider in pools})
        if len(configured) > 1000:
            raise ValueError("Too many cleanup pools")
        if not configured:
            return []
        scope = ' OR '.join('(c.ProfileName=? AND c.ProviderName=?)' for _ in configured)
        with self._db() as cursor:
            now = self._now(cursor)
            cursor.execute("SELECT TOP (?) c.* FROM broker.Connections c WHERE (" + scope + ")"
                + " AND (c.State='deleting' OR c.LastUsedAt<=?) AND c.LeaseUntil<=?"
                + " AND NOT EXISTS (SELECT 1 FROM broker.Transactions t WHERE t.ProfileName=c.ProfileName"
                + " AND ((t.TenantId=c.TenantId AND t.ObjectId=c.ObjectId)"
                + " OR (t.RequiredTenantId=c.TenantId AND t.RequiredObjectId=c.ObjectId))"
                + " AND t.Phase NOT IN ('complete','failed') AND t.ExpiresAt>?)"
                + " ORDER BY COALESCE(c.LastCheckedAt,0),c.LastUsedAt,c.ProfileName,c.ProviderName,c.TenantId,c.ObjectId,c.ConnectionId",
                batch_size, *(part for pair in configured for part in pair), now-idle_seconds, now, now)
            return [_row(cursor, row) for row in cursor.fetchall()]

    def claim_cleanup(self, candidate, lease_owner, *, idle_seconds=5184000, lease_seconds=300):
        """Fence a stale snapshot, invalidate proofs, and reserve one deletion.

        A SQL deadlock rolls this entire transaction back before CM is called.
        An existing deleting row remains quarantined when a lease expires.
        """
        key = self._cleanup_key(candidate)
        idle_seconds = self._cleanup_retention(idle_seconds)
        if (not isinstance(lease_owner, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", lease_owner)
                or type(lease_seconds) is not int or not 30 <= lease_seconds <= 600):
            raise ValueError("Invalid cleanup lease")
        profile, tenant, owner, provider, connection, _ = key
        with self._db(transaction=True) as cursor:
            cursor.execute("SELECT * FROM broker.Connections WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=?", *key)
            value = _row(cursor)
            if value is None:
                raise StoreConflict("Cleanup mapping changed")
            # Lock both verified and identity-pinned authentication rows. Read the
            # clock after waiting for these locks, not from the candidate scan.
            cursor.execute("SELECT ExpiresAt FROM broker.Transactions WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND ((TenantId=? AND ObjectId=?) OR (RequiredTenantId=? AND RequiredObjectId=?)) AND Phase NOT IN ('complete','failed')",
                           profile, tenant, owner, tenant, owner)
            authentications = [int(row[0]) for row in cursor.fetchall()]
            now = self._now(cursor)
            if (value['leaseUntil'] > now or value['state'] != 'deleting' and value['lastUsedAt'] > now-idle_seconds
                    or any(expiry > now for expiry in authentications)):
                raise StoreConflict("Connection is not eligible for cleanup")
            cursor.execute("UPDATE broker.Connections SET State='deleting',SecurityEpoch=SecurityEpoch+1,LeaseOwner=?,LeaseUntil=?,LastCheckedAt=? OUTPUT INSERTED.* WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=?",
                           lease_owner, now+lease_seconds, now, *key)
            claim = _row(cursor)
            if claim is None:
                raise StoreConflict("Cleanup mapping changed")
            cursor.execute("UPDATE broker.Sessions SET Revoked=1,RevokedAt=?,RevocationReason='CONNECTION_IDLE' WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND Revoked=0", now, profile, tenant, owner)
            cursor.execute("UPDATE broker.Transactions SET Phase='failed',TicketHash=NULL,CookieHash=NULL,LaunchHash=NULL,OidcFlow=NULL,OidcStateHash=NULL,CmStateHash=NULL,PendingSessionSecretHash=NULL,FailureCode='CONNECTION_IDLE' WHERE ProfileName=? AND ((TenantId=? AND ObjectId=?) OR (RequiredTenantId=? AND RequiredObjectId=?)) AND Phase<>'complete'", profile, tenant, owner, tenant, owner)
            cursor.execute("DELETE r FROM broker.SessionRotationCandidates r JOIN broker.Sessions s ON s.SessionId=r.SessionId WHERE s.ProfileName=? AND s.TenantId=? AND s.ObjectId=?", profile, tenant, owner)
            return claim

    def validate_cleanup_claim(self, claim):
        key = self._cleanup_key(claim)
        owner = claim.get('leaseOwner')
        if not isinstance(owner, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", owner):
            raise ValueError("Invalid cleanup lease")
        with self._db() as cursor:
            cursor.execute("SELECT Version FROM broker.Connections WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=? AND State='deleting' AND LeaseOwner=? AND LeaseUntil>DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME())", *key, owner)
            return cursor.fetchone() is not None

    def finish_cleanup(self, claim):
        """Release capacity only for the exact live claim after CM GET404."""
        key = self._cleanup_key(claim)
        owner = claim.get('leaseOwner')
        if not isinstance(owner, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", owner):
            raise ValueError("Invalid cleanup lease")
        with self._db(transaction=True) as cursor:
            cursor.execute("SELECT * FROM broker.Connections WITH(UPDLOCK,HOLDLOCK) WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=? AND State='deleting' AND LeaseOwner=?", *key, owner)
            value = _row(cursor)
            now = self._now(cursor)
            if value is None or value['leaseUntil'] <= now:
                raise StoreConflict("Cleanup lease was superseded or expired")
            cursor.execute("DELETE broker.Connections WHERE ProfileName=? AND TenantId=? AND ObjectId=? AND ProviderName=? AND ConnectionId=? AND Version=? AND State='deleting' AND LeaseOwner=?", *key, owner)
            if cursor.rowcount != 1:
                raise StoreConflict("Cleanup mapping changed")
            cursor.execute("UPDATE broker.Pools SET Allocated=Allocated-1 WHERE ProfileName=? AND ProviderName=? AND Allocated>0", key[0], key[3])
            if cursor.rowcount != 1:
                raise StoreConflict("Credential capacity counter needs reconciliation")
            return True
