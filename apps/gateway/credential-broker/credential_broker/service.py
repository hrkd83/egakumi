"""本人確認・SQL取引・CM操作を順に進め、失敗時は利用許可を返さない。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlencode

from .cm import CmError
from .identity import IdentityError
from .store import (StoreCapacity, StoreConflict, StoreNotAuthorized, StoreNotFound,
                    StoreRateLimited, StoreRotationConflict, StoreRotationExpired, StoreUnavailable)


COOKIE_NAME = "__Host-CoreCredentialLogin"
HEX = re.compile(r"[a-f0-9]{64}\Z")


class BrokerError(RuntimeError):
    def __init__(self, code="ConnectionUnavailable", status=503):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def checked_hex(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise BrokerError("ConnectionNotAuthorized", 403)
    return value


def browser_parts(value):
    """短時間のブラウザー取引証明を継続セッションと混同しない。"""
    if not isinstance(value, str) or len(value) != 129 or value[64] != ".":
        raise BrokerError("ConnectionNotAuthorized", 403)
    return checked_hex(value[:64]), checked_hex(value[65:])


def session_parts(value):
    """新しい本人セッションだけを受理し、取引ID・pollSecretは受け付けない。"""
    if not isinstance(value, str) or len(value) != 133 or not value.startswith("cs2.") or value[68] != ".":
        raise BrokerError("ConnectionNotAuthorized", 403)
    return checked_hex(value[4:68]), checked_hex(value[69:])


def browser_cookie(value):
    """Cookieヘッダー内の対象名が一意であることを確認する。"""
    if not isinstance(value, str) or len(value) > 16384:
        raise BrokerError("ConnectionNotAuthorized", 403)
    matches = [item.strip().split("=", 1)[1] for item in value.split(";")
               if item.strip().startswith(COOKIE_NAME + "=")]
    if len(matches) != 1:
        raise BrokerError("ConnectionNotAuthorized", 403)
    browser_parts(matches[0])
    return matches[0]


class BrokerService:
    def __init__(self, settings, store, identity, cm, *, clock=time.time, random=secrets.token_hex):
        self.settings, self.store, self.identity, self.cm = settings, store, identity, cm
        self.clock, self.random = clock, random

    def authenticate(self, key):
        if (not isinstance(key, str) or not hmac.compare_digest(key.encode(), self.settings.shared_key.encode())):
            raise BrokerError("BrokerAuthenticationRequired", 401)

    def profile(self, name):
        if not isinstance(name, str) or name not in self.settings.profiles:
            raise BrokerError("InvalidProvider", 400)
        return self.settings.profiles[name]

    def subscription(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", value):
            raise BrokerError("AuthenticationRequired", 401)
        return value

    def _guard(self, operation):
        try:
            return operation()
        except BrokerError:
            raise
        except StoreCapacity:
            raise BrokerError("ConnectionCapacityReached", 503) from None
        except StoreNotAuthorized:
            raise BrokerError("ConnectionNotAuthorized", 403) from None
        except StoreRateLimited:
            raise BrokerError("ConnectionRateLimited", 429) from None
        except StoreRotationExpired:
            raise BrokerError("SessionRotationExpired", 409) from None
        except StoreRotationConflict:
            raise BrokerError("SessionRotationSuperseded", 409) from None
        except (StoreConflict, StoreNotFound):
            raise BrokerError("ConnectionNotAuthorized", 409) from None
        except (StoreUnavailable, CmError, IdentityError):
            raise BrokerError("ConnectionUnavailable", 503) from None

    def start(self, provider, subscription_id):
        profile, sub = self.profile(provider), self.subscription(subscription_id)
        transaction, poll, launch, session_id, session_secret = (self.random(32) for _ in range(5))
        now = int(self.clock())
        self._guard(lambda: self.store.create_transaction(transaction, profile.name, sub, digest(poll), digest(launch),
            now + self.settings.transaction_ttl, pending_session_id=session_id,
            pending_secret_hash=digest(session_secret),
            login_limit=self.settings.login_calls_per_minute))
        return self._login_response(transaction, poll, launch, session_id, session_secret)

    def _login_response(self, transaction, poll, launch, session_id, session_secret):
        return {"status": "need_login", "protocolVersion": 2,
            "provisionalConnectionSession": "cs2." + session_id + "." + session_secret,
            "transactionId": transaction, "pollSecret": poll,
            "loginUrl": self.settings.public_origin + "/auth/connections/browser/launch#" + urlencode({
                "transactionId": transaction, "launch": launch}), "expiresIn": self.settings.transaction_ttl,
            "statusUrl": self.settings.public_origin + "/auth/connections/status?" + urlencode({"transactionId": transaction})}

    def challenge(self, provider, subscription_id, connection_session, reason):
        """業務 API の応答用に、本人確認を始める短時間の案内だけを作る。"""
        profile, sub = self.profile(provider), self.subscription(subscription_id)
        if (not isinstance(reason, str) or reason not in {"session_required", "token_rejected"}
                or not isinstance(connection_session, str)):
            raise BrokerError("InvalidRequest", 400)
        source = session_parts(connection_session) if connection_session else None
        if reason == "token_rejected" and source is None:
            raise BrokerError("ConnectionNotAuthorized", 403)
        transaction, poll, launch, session_id, session_secret = (self.random(32) for _ in range(5))
        now = int(self.clock())
        self._guard(lambda: self.store.create_challenge(transaction, profile.name, sub, digest(poll), digest(launch),
            now + self.settings.transaction_ttl, pending_session_id=session_id,
            pending_secret_hash=digest(session_secret),
            source_session=(source[0], digest(source[1])) if source else None, force_cm_login=reason == "token_rejected",
            login_limit=self.settings.login_calls_per_minute))
        return self._login_response(transaction, poll, launch, session_id, session_secret)

    def launch(self, transaction, launch_secret, origin):
        checked_hex(transaction)
        checked_hex(launch_secret)
        if origin != self.settings.public_origin:
            raise BrokerError("ConnectionNotAuthorized", 403)
        def operation():
            record = self.store.get_transaction(transaction)
            if not record:
                raise BrokerError("ConnectionNotAuthorized", 403)
            profile = self.profile(record["profileName"])
            flow = self.identity.begin(profile)
            cookie_secret = self.random(32)
            self.store.consume_launch(transaction, digest(launch_secret), digest(cookie_secret), digest(flow["state"]), flow)
            return {"loginUrl": flow["auth_uri"]}, transaction + "." + cookie_secret
        return self._guard(operation)

    def identity_callback(self, query, cookie):
        transaction, secret = browser_parts(cookie)
        state = checked_hex(query.get("state"))
        if not isinstance(query.get("code"), str) or not 1 <= len(query["code"]) <= 16384 or "error" in query:
            raise BrokerError("IdentitySignInRequired", 400)
        def operation():
            record = self.store.consume_identity(transaction, digest(secret), digest(state))
            profile = self.profile(record["profileName"])
            user = self.identity.complete(profile, record["flow"], query)
            # 期限切れセッションの持ち主と異なるアカウントへの切替えを防ぎます。
            if (record.get("requiredTenantId") and (record["requiredTenantId"] != user.tenant_id
                    or record.get("requiredObjectId") != user.object_id)):
                raise BrokerError("ConnectionNotAuthorized", 403)
            self.store.set_identity(transaction, user.tenant_id, user.object_id, user.upn)
            mapping = self.store.allocate_connection(profile.name, user.tenant_id, user.object_id, user.upn, transaction)
            provider, connection = mapping["providerName"], mapping["connectionId"]
            if provider not in {name for name, _ in profile.pools}:
                raise BrokerError()
            if mapping.get("leaseOwned"):
                self.cm.ensure_connection(provider, connection)
                self.store.set_connection_state(profile.name, user.tenant_id, user.object_id, "pending", transaction)
            elif mapping.get("state") != "ready":
                raise BrokerError("ConnectionBusy", 409)
            reuse_ready = mapping.get("state") == "ready"
            if reuse_ready and (record.get("forceCmLogin") or not self.cm.is_connected(provider, connection)):
                # CM側で削除・切断された接続は、SQLの版を照合して再認証へ戻します。
                self.store.observe_connection(profile.name, user.tenant_id, user.object_id, False, mapping["version"])
                mapping = self.store.allocate_connection(profile.name, user.tenant_id, user.object_id, user.upn, transaction)
                if not mapping.get("leaseOwned") or mapping["providerName"] != provider or mapping["connectionId"] != connection:
                    raise BrokerError("ConnectionBusy", 409)
                self.cm.ensure_connection(provider, connection)
                self.store.set_connection_state(profile.name, user.tenant_id, user.object_id, "pending", transaction)
                reuse_ready = False
            cm_state = self.random(32)
            self.store.prepare_cm_callback(transaction, digest(secret), digest(cm_state))
            callback = self.settings.public_origin + "/auth/connections/browser/callback?" + urlencode({
                "transactionId": transaction, "state": cm_state})
            # 既存接続もAPIMのtoken・Graph本人照合を通してから利用可能にします。
            # 本人照合が済んだ既存接続だけを再利用します。初回・再認証はCMへ進みます。
            return callback if reuse_ready else self.cm.login_link(provider, connection, callback)
        try:
            return self._guard(operation)
        except BrokerError:
            try:
                self.store.fail_transaction(transaction, "IDENTITY_CONNECTION_FAILED")
            except (StoreUnavailable, StoreConflict, StoreNotFound):
                pass
            raise

    def callback_context(self, transaction, state, cookie):
        transaction, state = checked_hex(transaction), checked_hex(state)
        cookie_tx, secret = browser_parts(cookie)
        if not hmac.compare_digest(cookie_tx, transaction):
            raise BrokerError("ConnectionNotAuthorized", 403)
        ticket = self.random(32)
        record = self._guard(lambda: self.store.create_callback_ticket(transaction, digest(secret), digest(state), digest(ticket)))
        profile = self.profile(record["profileName"])
        if record["providerName"] not in {name for name, _ in profile.pools}:
            raise BrokerError()
        return {"providerName": record["providerName"], "connectionId": record["connectionId"],
            "ownerId": record.get("ownerId", record["objectId"]), "tenantId": record["tenantId"],
            "identityEndpoint": profile.identity_endpoint, "identityIdProperty": profile.identity_id_property,
            "callbackTicket": ticket}

    def complete(self, transaction, ticket, owner):
        checked_hex(transaction)
        checked_hex(ticket)
        # SqlStore が保存した本人 ID と Graph の確認結果を原子的に照合します。
        self._guard(lambda: self.store.complete_transaction(transaction, digest(ticket), owner,
            policy=self.settings.session_policy))
        return {"status": "complete"}

    def status(self, transaction, poll_secret, subscription_id):
        checked_hex(transaction)
        checked_hex(poll_secret)
        sub = self.subscription(subscription_id)
        record = self._guard(lambda: self.store.status(transaction, digest(poll_secret), sub))
        result = {key: record[key] for key in ("status", "sessionId", "expiresAt", "idleExpiresAt", "absoluteExpiresAt",
            "authenticatedAt", "generation", "nextRotationAt") if key in record}
        result["protocolVersion"] = 2
        if result.get("status") == "complete":
            result.update({key: record[key] for key in ("providerName", "ownerId", "tenantId") if key in record})
        return result

    def _session_context(self, record, profile):
        if (record.get("providerName") not in {name for name, _ in profile.pools}
                or record.get("tenantId") != profile.tenant_id):
            raise BrokerError()
        return {"allowed": True, "status": 200, "protocolVersion": 2, "providerName": record["providerName"],
            "connectionId": record["connectionId"], "ownerId": record.get("ownerId", record.get("objectId")),
            "tenantId": record["tenantId"], "identityEndpoint": profile.identity_endpoint,
            "identityIdProperty": profile.identity_id_property,
            **{key: record[key] for key in ("sessionId", "authenticatedAt", "expiresAt", "generation", "idleExpiresAt", "absoluteExpiresAt",
                "nextRotationAt", "credentialKind", "updateRequired") if key in record}}

    def authorize(self, token, subscription_id, profile_name):
        session_id, secret = session_parts(token)
        sub, profile = self.subscription(subscription_id), self.profile(profile_name)
        record = self._guard(lambda: self.store.resolve_session(session_id, digest(secret), sub, profile.name))
        if not record:
            return {"allowed": False, "status": 409, "protocolVersion": 2, "errorCode": "ConnectionRequired"}
        return self._session_context(record, profile)

    def use(self, token, subscription_id, profile_name, verified_owner_id, api_id, operation_id):
        """CMとGraphの本人照合後だけ、SQLで再検証して業務利用を確定する。"""
        session_id, secret = session_parts(token)
        sub, profile = self.subscription(subscription_id), self.profile(profile_name)
        if (not isinstance(api_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}(?:;rev=[0-9]{1,10})?", api_id)
                or not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", operation_id)):
            raise BrokerError("InvalidRequest", 400)
        candidate_id, candidate_secret = self.random(32), self.random(32)
        record = self._guard(lambda: self.store.finalize_business_use(session_id, digest(secret), sub,
            profile.name, verified_owner_id, candidate_id=candidate_id, candidate_hash=digest(candidate_secret)))
        if not record:
            return {"allowed": False, "status": 409, "protocolVersion": 2, "errorCode": "ConnectionRequired"}
        if record.get("allowed") is False and "providerName" not in record:
            return {"allowed": False, "status": record.get("status", 409), "protocolVersion": 2,
                "errorCode": record.get("errorCode", "ConnectionRequired")}
        result = self._session_context(record, profile)
        result.update({key: record[key] for key in ("allowed", "status", "errorCode") if key in record})
        if record.get("rotationCandidate"):
            # 平文候補はこの応答だけに含め、SQLへ渡すのはハッシュだけです。
            result["rotation"] = dict(record["rotationCandidate"],
                candidateSession="cs2." + session_id + "." + candidate_secret)
        return result

    def commit_rotation(self, token, subscription_id, rotation_id, candidate_session, from_generation):
        """保存済み候補の確定を冪等に行い、業務利用期限は更新しない。"""
        session_id, secret = session_parts(token)
        candidate_sid, candidate_secret = session_parts(candidate_session)
        checked_hex(rotation_id)
        if candidate_sid != session_id or type(from_generation) is not int or from_generation < 1:
            raise BrokerError("InvalidRequest", 400)
        record = self._guard(lambda: self.store.commit_rotation(session_id, digest(secret),
            self.subscription(subscription_id), rotation_id, digest(candidate_secret), from_generation))
        return {"status": "updated", "protocolVersion": 2,
            **{key: record[key] for key in ("sessionId", "expiresAt", "authenticatedAt", "generation", "idleExpiresAt",
            "absoluteExpiresAt", "nextRotationAt", "previousValidUntil") if key in record}}

    def logout(self, token, subscription_id):
        transaction, secret = session_parts(token)
        sub = self.subscription(subscription_id)
        self._guard(lambda: self.store.revoke_session(transaction, digest(secret), sub))
        return {"status": "signed_out"}

    def logout_all(self, token, subscription_id):
        session_id, secret = session_parts(token)
        self._guard(lambda: self.store.revoke_subject_sessions(session_id, digest(secret), self.subscription(subscription_id)))
        return {"status": "signed_out_all"}

    def disconnect(self, token, subscription_id):
        session_id, secret = session_parts(token)
        mapping = self._guard(lambda: self.store.begin_disconnect(session_id, digest(secret), self.subscription(subscription_id)))
        deleted = self._guard(lambda: self.cm.delete_connection(mapping["providerName"], mapping["connectionId"],
            validate_claim=lambda: self.store.validate_disconnect(mapping)))
        if deleted:
            self._guard(lambda: self.store.finish_disconnect(mapping["profileName"], mapping["tenantId"],
                mapping.get("ownerId", mapping.get("objectId")), mapping["providerName"], mapping["connectionId"],
                expected_version=mapping["connectionVersion"]))
        return {"status": "disconnected" if deleted else "disconnect_pending"}

    def connection_status(self, token, subscription_id, profile_name):
        context = self.authorize(token, subscription_id, profile_name)
        if not context["allowed"]:
            return {"status": "reauthentication_required", "reauthenticationRequired": True}
        state = self._guard(lambda: self.cm.connection_status(context["providerName"], context["connectionId"]))
        if state == "requires_reauthentication":
            return {"status": "reauthentication_required", "reauthenticationRequired": True}
        # Connectedでもtoken取得失敗の原因が認証とは限らず、再認証へ誘導しません。
        raise BrokerError("ConnectionUnavailable", 503)
