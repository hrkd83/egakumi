"""Shared offline broker fixtures; never imported by production code."""
from __future__ import annotations

import copy
import secrets
import unittest

from credential_broker.identity import Identity, IdentityError
from credential_broker.service import BrokerService
from credential_broker.settings import Profile, Settings
from credential_broker.store import (StoreCapacity, StoreConflict, StoreNotAuthorized, StoreRateLimited,
                                    StoreRotationConflict, StoreRotationExpired)


TENANT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
PROFILE = "graph-delegated"
ORIGIN = "https://gateway.example.test"
KEY = "test-broker-key-" + "a" * 40


def settings():
    profile = Profile(PROFILE, TENANT, CLIENT, "test-oidc-secret", pools=((PROFILE + "-a", 8000),))
    return Settings(ORIGIN, KEY, {PROFILE: profile}, transaction_ttl=300)


class LedgerDouble:
    """No production fallback: this ledger is only used in the broker unit tests."""
    def __init__(self):
        self.transactions, self.mappings, self.sessions = {}, {}, {}
        self.candidates = {}
        self.now = 1000
        self.capacity_error = False
        self.ready_error = False

    def ensure_ready(self):
        if self.ready_error:
            raise RuntimeError("secret-do-not-log")

    def health(self):
        return not self.ready_error

    def create_transaction(self, tx, profile, sub, poll, launch, expires, **kwargs):
        return self.create_challenge(tx, profile, sub, poll, launch, expires, **kwargs)

    def create_challenge(self, tx, profile, sub, poll, launch, expires,
                         *, pending_session_id, pending_secret_hash, source_session=None, force_cm_login=False, login_limit=10):
        required = {}
        if source_session:
            row = self.sessions.get(source_session[0])
            if (not row or row["secretHash"] != source_session[1] or row["subscriptionId"] != sub
                    or row["profileName"] != profile):
                raise StoreNotAuthorized()
            required = dict(requiredTenantId=row["tenantId"], requiredObjectId=row["objectId"])
            if force_cm_login:
                mapping = self.mappings[(profile, row["tenantId"], row["objectId"])]
                mapping.update(state="unverified", leaseOwner=None)
                for session in self.sessions.values():
                    if (session["profileName"], session["tenantId"], session["objectId"]) == (profile, row["tenantId"], row["objectId"]):
                        session["revoked"] = True
        recent = [row for row in self.transactions.values() if row["subscriptionId"] == sub and row["createdAt"] > self.now - 60]
        if len(recent) >= login_limit:
            raise StoreRateLimited()
        self.transactions[tx] = dict(transactionId=tx, profileName=profile, subscriptionId=sub,
            pollHash=poll, launchHash=launch, expiresAt=expires, pendingSessionId=pending_session_id,
            pendingSecretHash=pending_secret_hash, phase="pending",
            createdAt=self.now, forceCmLogin=force_cm_login, **required)
        return self.get_transaction(tx)

    def get_transaction(self, tx):
        return copy.deepcopy(self.transactions.get(tx))

    def active(self, tx, phase):
        row = self.transactions[tx]
        if row["phase"] not in phase or row["expiresAt"] <= self.now:
            raise StoreConflict()
        return row

    def consume_launch(self, tx, launch, cookie, state, flow):
        row = self.active(tx, ["pending"])
        if row["launchHash"] != launch:
            raise StoreConflict()
        row.update(launchHash=None, cookieHash=cookie, oidcStateHash=state, flow=copy.deepcopy(flow), phase="identityPending")
        return copy.deepcopy(row)

    def consume_identity(self, tx, cookie, state):
        row = self.active(tx, ["identityPending"])
        if row["cookieHash"] != cookie or row["oidcStateHash"] != state:
            raise StoreConflict()
        result = copy.deepcopy(row)
        row.update(phase="identityProcessing", oidcStateHash=None)
        row.pop("flow")
        return result

    def set_identity(self, tx, tenant, owner, upn):
        row = self.active(tx, ["identityProcessing"])
        row.update(tenantId=tenant, objectId=owner, ownerId=owner, upn=upn, phase="identityVerified")
        return copy.deepcopy(row)

    def allocate_connection(self, profile, tenant, owner, upn, lease_owner):
        if self.capacity_error:
            raise StoreCapacity()
        key = profile, tenant, owner
        if key not in self.mappings:
            self.mappings[key] = dict(profileName=profile, tenantId=tenant, objectId=owner,
                providerName=profile + "-a", connectionId="member-1234567890abcdef", state="reserved", leaseOwner=lease_owner, version=b"one")
        elif self.mappings[key]["state"] == "unverified":
            self.mappings[key]["leaseOwner"] = lease_owner
        row = copy.deepcopy(self.mappings[key])
        row["leaseOwned"] = row["leaseOwner"] == lease_owner
        return row

    def set_connection_state(self, profile, tenant, owner, state, lease_owner):
        row = self.mappings[(profile, tenant, owner)]
        if row["leaseOwner"] != lease_owner:
            raise StoreConflict()
        row["state"] = state
        return copy.deepcopy(row)

    def observe_connection(self, profile, tenant, owner, exists, expected_version):
        row = self.mappings[(profile, tenant, owner)]
        if row["version"] != expected_version:
            raise StoreConflict()
        if not exists:
            row["state"] = "unverified"
        row["version"] = b"two"

    def prepare_cm_callback(self, tx, cookie, state):
        row = self.active(tx, ["identityVerified"])
        if row["cookieHash"] != cookie:
            raise StoreConflict()
        mapping = self.mappings[(row["profileName"], row["tenantId"], row["objectId"])]
        row.update(providerName=mapping["providerName"], connectionId=mapping["connectionId"], cmStateHash=state, phase="cmPending")
        return copy.deepcopy(row)

    def create_callback_ticket(self, tx, cookie, state, ticket):
        row = self.active(tx, ["cmPending"])
        if row["cookieHash"] != cookie or row["cmStateHash"] != state:
            raise StoreConflict()
        row.update(ticketHash=ticket, cmStateHash=None, phase="verifying")
        return copy.deepcopy(row)

    def complete_transaction(self, tx, ticket, owner, *, policy):
        row = self.active(tx, ["verifying"])
        if row["ticketHash"] != ticket or row["objectId"] != owner:
            raise StoreConflict()
        mapping = self.mappings[(row["profileName"], row["tenantId"], row["objectId"])]
        mapping.update(state="ready", leaseOwner=None)
        row.update(phase="complete", ticketHash=None)
        self.sessions[row["pendingSessionId"]] = dict(row, sessionId=row["pendingSessionId"],
            secretHash=row["pendingSecretHash"], revoked=False, authenticatedAt=self.now,
            lastUsedAt=self.now, idleExpiresAt=self.now + policy["idleTimeoutSeconds"],
            absoluteExpiresAt=self.now + policy["absoluteLifetimeSeconds"], generation=1,
            nextRotationAt=self.now + policy["rotationIntervalSeconds"], policy=dict(policy),
            previousHash=None, previousValidUntil=0, handoffAt=None)
        return copy.deepcopy(row)

    def status(self, tx, poll, sub):
        row = self.active(tx, ["pending", "identityPending", "identityVerified", "cmPending", "verifying", "complete"])
        if row["pollHash"] != poll or row["subscriptionId"] != sub:
            raise StoreConflict()
        result = dict(row, status="complete" if row["phase"] == "complete" else "pending")
        if row["phase"] == "complete":
            result.update(self.sessions[row["pendingSessionId"]])
        return result

    def resolve_session(self, tx, secret, sub, profile):
        row = self.sessions.get(tx)
        if (not row or secret not in {row["secretHash"], row["previousHash"]} or row["subscriptionId"] != sub
                or (profile is not None and row["profileName"] != profile)):
            raise StoreNotAuthorized()
        mapping = self.mappings.get((row["profileName"], row["tenantId"], row["objectId"]))
        kind = "current" if row["secretHash"] == secret else "previous"
        if kind == "previous" and row["previousValidUntil"] <= self.now:
            raise StoreNotAuthorized()
        if (row["revoked"] or min(row["idleExpiresAt"], row["absoluteExpiresAt"]) <= self.now
                or not mapping or mapping["state"] != "ready"):
            return None
        return dict(copy.deepcopy(row), credentialKind=kind,
            updateRequired=row["handoffAt"] is not None and row["handoffAt"] <= self.now)

    def finalize_business_use(self, sid, secret, sub, profile, owner, *, candidate_id, candidate_hash):
        record = self.resolve_session(sid, secret, sub, profile)
        if record is None:
            return None
        if record["objectId"] != owner:
            raise StoreNotAuthorized()
        row = self.sessions[sid]
        overdue = record["updateRequired"]
        if not overdue:
            row.update(lastUsedAt=self.now, idleExpiresAt=min(self.now + row["policy"]["idleTimeoutSeconds"], row["absoluteExpiresAt"]))
        result = self.resolve_session(sid, secret, sub, profile)
        result.update(allowed=not overdue, status=409 if overdue else 200)
        if overdue:
            result["errorCode"] = "SessionUpdateRequired"
        if self.now >= row["nextRotationAt"] and record["credentialKind"] == "current":
            if row["handoffAt"] is None:
                row["handoffAt"] = self.now + row["policy"]["rotationHandoffSeconds"]
            candidate = dict(rotationId=candidate_id, fromGeneration=row["generation"], generation=row["generation"] + 1,
                expiresAt=min(self.now + row["policy"]["rotationCandidateTtlSeconds"], row["absoluteExpiresAt"]),
                secretHash=candidate_hash, sessionId=sid)
            self.candidates[candidate_id] = candidate
            result["rotationCandidate"] = {key: value for key, value in candidate.items() if key not in {"secretHash", "sessionId"}}
        return result

    def commit_rotation(self, sid, current_hash, sub, candidate_id, candidate_hash, from_generation):
        row = self.sessions.get(sid)
        candidate = self.candidates.get(candidate_id)
        if (row and candidate and row.get("winningCandidate") == candidate_id and row["secretHash"] == candidate_hash
                and row["previousHash"] == current_hash and row["generation"] == from_generation + 1
                and row["subscriptionId"] == sub and not row["revoked"]
                and min(row["idleExpiresAt"], row["absoluteExpiresAt"]) > self.now):
            return copy.deepcopy(row)
        record = self.resolve_session(sid, current_hash, sub, None)
        if not record:
            raise StoreNotAuthorized()
        if row["generation"] != from_generation or record["credentialKind"] != "current":
            raise StoreRotationConflict()
        if not candidate or candidate["expiresAt"] <= self.now:
            raise StoreRotationExpired()
        if (candidate["secretHash"] != candidate_hash or candidate["sessionId"] != sid
                or candidate["fromGeneration"] != from_generation):
            raise StoreRotationConflict()
        row.update(previousHash=current_hash, previousValidUntil=self.now + row["policy"]["rotationGraceSeconds"],
            secretHash=candidate_hash, generation=from_generation + 1, nextRotationAt=self.now + row["policy"]["rotationIntervalSeconds"],
            handoffAt=None, winningCandidate=candidate_id)
        return copy.deepcopy(row)

    def revoke_session(self, tx, secret, sub):
        row = self.sessions.get(tx)
        if row and row["secretHash"] == secret and row["subscriptionId"] == sub:
            row["revoked"] = True
            return True
        return False

    def revoke_subject_sessions(self, sid, secret, sub):
        record = self.resolve_session(sid, secret, sub, None)
        if not record:
            raise StoreNotAuthorized()
        for row in self.sessions.values():
            if (row["tenantId"], row["objectId"]) == (record["tenantId"], record["objectId"]):
                row["revoked"] = True

    def begin_disconnect(self, sid, secret, sub):
        record = self.resolve_session(sid, secret, sub, None)
        if not record:
            raise StoreNotAuthorized()
        mapping = self.mappings[(record["profileName"], record["tenantId"], record["objectId"])]
        mapping.update(state="deleting")
        for row in self.sessions.values():
            if (row["profileName"], row["tenantId"], row["objectId"]) == (record["profileName"], record["tenantId"], record["objectId"]):
                row["revoked"] = True
        return copy.deepcopy(record)

    def finish_disconnect(self, profile, tenant, owner, provider, connection):
        key = profile, tenant, owner
        mapping = self.mappings[key]
        if mapping["providerName"] != provider or mapping["connectionId"] != connection or mapping["state"] != "deleting":
            raise StoreConflict()
        del self.mappings[key]

    def fail_transaction(self, tx, code):
        self.transactions[tx].update(phase="failed", failureCode=code)


class IdentityDouble:
    def __init__(self):
        self.failure = False

    def begin(self, profile):
        state = secrets.token_hex(32)
        return {"state": state, "auth_uri": "https://login.microsoftonline.com/authorize?state=" + state,
                "expectedNonce": "nonce", "msalFlow": {"code_verifier": "private-proof"}}

    def complete(self, profile, flow, query):
        if self.failure:
            raise IdentityError("secret-id-token-never-output")
        return Identity(TENANT, OWNER, "member@example.test")


class CmDouble:
    def __init__(self):
        self.ensured, self.connected, self.callback = [], False, None
        self.deleted, self.deletion_confirmed = [], True

    def ensure_connection(self, provider, connection):
        self.ensured.append((provider, connection))

    def is_connected(self, provider, connection):
        return self.connected

    def connection_status(self, provider, connection):
        return "connected" if self.connected else "requires_reauthentication"

    def delete_connection(self, provider, connection):
        self.deleted.append((provider, connection))
        return self.deletion_confirmed

    def login_link(self, provider, connection, callback):
        self.callback = callback
        return "https://logic-apis-japaneast.consent.azure-apim.net/connect?secret=opaque"


class BrokerTestCase(unittest.TestCase):
    def setUp(self):
        self.store, self.identity, self.cm = LedgerDouble(), IdentityDouble(), CmDouble()
        self.service = BrokerService(settings(), self.store, self.identity, self.cm, clock=lambda: self.store.now)
