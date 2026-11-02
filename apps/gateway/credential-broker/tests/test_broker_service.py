"""Broker service authorization and transaction contracts."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from credential_broker.identity import Identity
from credential_broker.service import BrokerError, session_parts
from credential_broker.store import StoreConflict
from _broker_support import BrokerTestCase, CLIENT, OWNER, PROFILE, TENANT, ORIGIN


class BrokerServiceTests(BrokerTestCase):
    def identity_flow(self):
        start = self.service.start(PROFILE, "subscription-a")
        parts = {k: v[0] for k, v in parse_qs(urlsplit(start["loginUrl"]).fragment).items()}
        launch, cookie = self.service.launch(parts["transactionId"], parts["launch"], ORIGIN)
        state = parse_qs(urlsplit(launch["loginUrl"]).query)["state"][0]
        location = self.service.identity_callback({"code": "opaque-code", "state": state}, cookie)
        callback = location if urlsplit(location).hostname == urlsplit(ORIGIN).hostname else self.cm.callback
        callback_values = {k: v[0] for k, v in parse_qs(urlsplit(callback).query).items()}
        return start, parts, cookie, callback_values

    def completed_flow(self):
        start, parts, cookie, callback = self.identity_flow()
        context = self.service.callback_context(callback["transactionId"], callback["state"], cookie)
        self.service.complete(start["transactionId"], context["callbackTicket"], OWNER)
        return start, context

    def test_new_connection_proof_and_subscription_bound_session(self):
        start, context = self.completed_flow()
        tx = start["transactionId"]
        status = self.service.status(tx, start["pollSecret"], "subscription-a")
        self.assertEqual("complete", status["status"])
        self.assertNotIn("connectionSession", status)
        self.assertEqual(2, status["protocolVersion"])
        token = start["provisionalConnectionSession"]
        self.assertNotIn(tx, token)
        self.assertNotIn(start["pollSecret"], token)
        self.assertEqual(session_parts(token)[0], status["sessionId"])
        self.assertEqual(1, len(self.cm.ensured))
        session = self.service.authorize(token, "subscription-a", PROFILE)
        self.assertTrue(session["allowed"])
        self.assertEqual(OWNER, session["ownerId"])
        with self.assertRaises(BrokerError) as error:
            self.service.authorize(token, "subscription-b", PROFILE)
        self.assertEqual(403, error.exception.status)
        self.assertNotIn("pollSecret", context)
        stored = json.dumps(self.store.transactions)
        self.assertNotIn(start["pollSecret"], stored)
        self.assertNotIn("opaque-code", stored)

    def test_ready_mapping_reuse_still_requires_fresh_owner_proof(self):
        first, _ = self.completed_flow()
        self.cm.connected = True
        second, _, cookie, callback = self.identity_flow()
        self.assertEqual(1, len(self.cm.ensured))
        self.assertEqual(1, len(self.store.mappings))
        pending = self.service.status(second["transactionId"], second["pollSecret"], "subscription-a")
        self.assertEqual("pending", pending["status"])
        self.assertNotIn("connectionSession", pending)
        context = self.service.callback_context(second["transactionId"], callback["state"], cookie)
        with self.assertRaises(BrokerError):
            self.service.complete(second["transactionId"], context["callbackTicket"], CLIENT)
        self.service.complete(second["transactionId"], context["callbackTicket"], OWNER)
        self.assertEqual("complete", self.service.status(second["transactionId"], second["pollSecret"], "subscription-a")["status"])
        self.assertNotEqual(first["pollSecret"], second["pollSecret"])

    def test_launch_and_callback_are_one_use(self):
        start, parts, cookie, callback = self.identity_flow()
        with self.assertRaises(BrokerError):
            self.service.launch(start["transactionId"], parts["launch"], ORIGIN)
        context = self.service.callback_context(start["transactionId"], callback["state"], cookie)
        with self.assertRaises(BrokerError):
            self.service.callback_context(start["transactionId"], callback["state"], cookie)
        self.service.complete(start["transactionId"], context["callbackTicket"], OWNER)
        with self.assertRaises(BrokerError):
            self.service.complete(start["transactionId"], context["callbackTicket"], OWNER)

    def test_wrong_origin_cookie_state_and_expiry_fail_closed(self):
        start = self.service.start(PROFILE, "subscription-a")
        fragment = {k: v[0] for k, v in parse_qs(urlsplit(start["loginUrl"]).fragment).items()}
        with self.assertRaises(BrokerError):
            self.service.launch(start["transactionId"], fragment["launch"], "https://evil.example")
        self.store.now += 301
        with self.assertRaises(BrokerError):
            self.service.launch(start["transactionId"], fragment["launch"], ORIGIN)
        self.store.now = 1000
        start, _, cookie, callback = self.identity_flow()
        for bad_cookie, bad_state in [(cookie[:-1] + ("a" if cookie[-1] != "a" else "b"), callback["state"]), (cookie, "0" * 64)]:
            with self.assertRaises(BrokerError):
                self.service.callback_context(start["transactionId"], bad_state, bad_cookie)

    def test_capacity_and_identity_errors_do_not_create_connection(self):
        self.store.capacity_error = True
        with self.assertRaises(BrokerError) as error:
            self.identity_flow()
        self.assertEqual("ConnectionCapacityReached", error.exception.code)
        self.assertFalse(self.cm.ensured)
        self.store.capacity_error = False
        self.identity.failure = True
        with self.assertRaises(BrokerError) as error:
            self.identity_flow()
        self.assertEqual("ConnectionUnavailable", str(error.exception))
        self.assertNotIn("secret", str(error.exception))

    def test_logout_and_expiry_invalidate_session_only(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.service.logout(token, "subscription-a")
        self.assertFalse(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        self.assertEqual("ready", next(iter(self.store.mappings.values()))["state"])
        new, _ = self.completed_flow()
        self.store.now += self.service.settings.idle_timeout_seconds
        self.assertFalse(self.service.authorize(new["provisionalConnectionSession"], "subscription-a", PROFILE)["allowed"])

    def test_ready_mapping_disconnected_in_cm_is_repaired_with_same_allocation(self):
        self.completed_flow()
        original = next(iter(self.store.mappings.values())).copy()
        self.cm.connected = False
        start, _, cookie, callback = self.identity_flow()
        self.assertEqual(1, len(self.store.mappings))
        self.assertEqual(original["connectionId"], next(iter(self.store.mappings.values()))["connectionId"])
        self.assertEqual(2, len(self.cm.ensured))
        self.assertEqual("cmPending", self.store.transactions[start["transactionId"]]["phase"])

    def test_new_or_unverified_mapping_uses_cm_login_even_if_cm_connected(self):
        self.cm.connected = True
        start, _, cookie, callback = self.identity_flow()
        self.assertIsNotNone(self.cm.callback)
        context = self.service.callback_context(start["transactionId"], callback["state"], cookie)
        with self.assertRaises(BrokerError):
            self.service.complete(start["transactionId"], context["callbackTicket"], CLIENT)

    def test_repair_stops_on_concurrent_mapping_change(self):
        self.completed_flow()
        self.cm.connected = False
        self.store.observe_connection = lambda *args: (_ for _ in ()).throw(StoreConflict())
        with self.assertRaises(BrokerError):
            self.identity_flow()
        self.assertEqual(1, len(self.cm.ensured))

    def challenge_identity(self, result):
        fragment = {key: values[0] for key, values in parse_qs(urlsplit(result["loginUrl"]).fragment).items()}
        login, cookie = self.service.launch(result["transactionId"], fragment["launch"], ORIGIN)
        state = parse_qs(urlsplit(login["loginUrl"]).query)["state"][0]
        return self.service.identity_callback({"code": "opaque-code", "state": state}, cookie)

    def test_api_challenge_only_creates_private_login_proofs_before_sign_in(self):
        first = self.service.challenge(PROFILE, "subscription-a", "", "session_required")
        second = self.service.challenge(PROFILE, "subscription-a", "", "session_required")
        self.assertNotEqual(first["transactionId"], second["transactionId"])
        self.assertNotEqual(first["pollSecret"], second["pollSecret"])
        self.assertEqual(ORIGIN + "/auth/connections/status?transactionId=" + first["transactionId"], first["statusUrl"])
        self.assertEqual("need_login", first["status"])
        self.assertFalse(self.cm.ensured)
        self.assertFalse(self.store.mappings)
        self.assertNotIn(first["pollSecret"], json.dumps(self.store.transactions))

    def test_authentic_expired_session_challenge_keeps_owner_and_reuses_connected_cm(self):
        previous, _ = self.completed_flow()
        token = previous["provisionalConnectionSession"]
        self.store.now += self.service.settings.idle_timeout_seconds
        self.cm.connected = True
        self.assertFalse(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        result = self.service.challenge(PROFILE, "subscription-a", token, "session_required")
        pending = self.store.transactions[result["transactionId"]]
        self.assertEqual((TENANT, OWNER), (pending["requiredTenantId"], pending["requiredObjectId"]))
        self.cm.callback = None
        location = self.challenge_identity(result)
        self.assertTrue(location.startswith(ORIGIN + "/auth/connections/browser/callback?"))
        self.assertIsNone(self.cm.callback)
        self.assertEqual(1, len(self.cm.ensured))
        self.assertEqual(1, len(self.store.mappings))

    def test_wrong_proof_cannot_start_login_but_revoked_authentic_proof_only_constrains_owner(self):
        previous, _ = self.completed_flow()
        token = previous["provisionalConnectionSession"]
        sid, _ = session_parts(token)
        for bad_token, sub in [("bad", "subscription-a"), ("0" * 64 + "." + "1" * 64, "subscription-a"),
                (previous["transactionId"] + "." + "0" * 64, "subscription-a"), (token, "subscription-b")]:
            with self.subTest(token_shape=len(bad_token), subscription=sub), self.assertRaises(BrokerError) as error:
                self.service.challenge(PROFILE, sub, bad_token, "session_required")
            self.assertEqual(403, error.exception.status)
        self.store.sessions[sid]["profileName"] = "other-profile"
        with self.assertRaises(BrokerError) as error:
            self.service.challenge(PROFILE, "subscription-a", token, "session_required")
        self.assertEqual(403, error.exception.status)
        self.store.sessions[sid].update(profileName=PROFILE, revoked=True)
        result = self.service.challenge(PROFILE, "subscription-a", token, "session_required")
        self.assertEqual(OWNER, self.store.transactions[result["transactionId"]]["requiredObjectId"])
        self.assertFalse(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])

    def test_token_rejection_forces_cm_login_even_when_cm_reports_connected(self):
        previous, _ = self.completed_flow()
        token = previous["provisionalConnectionSession"]
        self.cm.connected = True
        self.cm.callback = None
        result = self.service.challenge(PROFILE, "subscription-a", token, "token_rejected")
        location = self.challenge_identity(result)
        self.assertTrue(location.startswith("https://logic-apis-"))
        self.assertIsNotNone(self.cm.callback)
        self.assertEqual(2, len(self.cm.ensured))
        self.assertEqual(1, len(self.store.mappings))
        self.assertEqual("pending", next(iter(self.store.mappings.values()))["state"])

    def test_reauthentication_cannot_replace_the_original_user_with_another_account(self):
        previous, _ = self.completed_flow()
        token = previous["provisionalConnectionSession"]
        self.cm.connected = True
        result = self.service.challenge(PROFILE, "subscription-a", token, "token_rejected")
        self.identity.complete = lambda *args: Identity(TENANT, CLIENT, "other@example.test")
        with self.assertRaises(BrokerError) as error:
            self.challenge_identity(result)
        self.assertEqual(403, error.exception.status)
        self.assertEqual(1, len(self.cm.ensured))
        self.assertEqual("unverified", next(iter(self.store.mappings.values()))["state"])
        self.assertEqual("failed", self.store.transactions[result["transactionId"]]["phase"])

    def test_start_and_api_challenge_share_a_subscription_scoped_rolling_limit(self):
        self.service.settings = replace(self.service.settings, login_calls_per_minute=2)
        self.service.start(PROFILE, "subscription-a")
        self.service.challenge(PROFILE, "subscription-a", "", "session_required")
        with self.assertRaises(BrokerError) as error:
            self.service.challenge(PROFILE, "subscription-a", "", "session_required")
        self.assertEqual((429, "ConnectionRateLimited"), (error.exception.status, error.exception.code))
        self.service.start(PROFILE, "subscription-b")
        self.store.now += 60
        self.service.challenge(PROFILE, "subscription-a", "", "session_required")

    def test_invalid_challenge_reason_is_not_an_operational_error(self):
        for reason in [None, {}, "arbitrary"]:
            with self.assertRaises(BrokerError) as error:
                self.service.challenge(PROFILE, "subscription-a", "", reason)
            self.assertEqual(400, error.exception.status)
        with self.assertRaises(BrokerError) as error:
            self.service.challenge(PROFILE, "subscription-a", "", "token_rejected")
        self.assertEqual(403, error.exception.status)

    def test_completion_starts_lifetime_and_polling_never_releases_or_extends_session(self):
        start, _, cookie, callback = self.identity_flow()
        self.store.now += 120
        context = self.service.callback_context(start["transactionId"], callback["state"], cookie)
        self.service.complete(start["transactionId"], context["callbackTicket"], OWNER)
        token = start["provisionalConnectionSession"]
        session = self.service.authorize(token, "subscription-a", PROFILE)
        expected = 1120 + self.service.settings.idle_timeout_seconds
        self.assertEqual(expected, session["idleExpiresAt"])
        self.assertEqual(1120 + self.service.settings.absolute_lifetime_seconds, session["absoluteExpiresAt"])
        self.store.now += 10
        status = self.service.status(start["transactionId"], start["pollSecret"], "subscription-a")
        self.assertEqual(expected, status["idleExpiresAt"])
        self.assertNotIn(token, json.dumps(status))
        self.assertNotIn("connectionSession", status)
        self.store.now += 300
        with self.assertRaises(BrokerError):
            self.service.status(start["transactionId"], start["pollSecret"], "subscription-a")
        self.assertTrue(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        for legacy in [start["transactionId"] + "." + start["pollSecret"], start["pollSecret"], "cs2.bad.bad"]:
            with self.assertRaises(BrokerError):
                self.service.authorize(legacy, "subscription-a", PROFILE)

    def use(self, token, owner=OWNER):
        return self.service.use(token, "subscription-a", PROFILE, owner, "graph-profile-v1", "get-me")

    def commit(self, token, rotation):
        return self.service.commit_rotation(token, "subscription-a", rotation["rotationId"],
            rotation["candidateSession"], rotation["fromGeneration"])

    def test_business_use_extends_idle_but_authorize_wrong_owner_and_expiry_do_not(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        original = self.service.authorize(token, "subscription-a", PROFILE)
        self.store.now += 3600
        self.assertEqual(original["idleExpiresAt"], self.service.authorize(token, "subscription-a", PROFILE)["idleExpiresAt"])
        with self.assertRaises(BrokerError):
            self.use(token, CLIENT)
        self.assertEqual(original["idleExpiresAt"], self.service.authorize(token, "subscription-a", PROFILE)["idleExpiresAt"])
        updated = self.use(token)
        self.assertEqual(original["idleExpiresAt"] + 3600, updated["idleExpiresAt"])
        self.assertEqual(original["absoluteExpiresAt"], updated["absoluteExpiresAt"])
        self.store.now = updated["idleExpiresAt"]
        self.assertFalse(self.use(token)["allowed"])
        sid, _ = session_parts(token)
        self.assertEqual(updated["idleExpiresAt"], self.store.sessions[sid]["idleExpiresAt"])

    def test_apim_revision_suffix_is_valid_business_operation_context(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        result = self.service.use(token, "subscription-a", PROFILE, OWNER, "graph-profile-v1;rev=12", "get-me")
        self.assertTrue(result["allowed"])
        for api_id in ["graph;rev=bad", "graph\n", "graph;other=1"]:
            with self.subTest(api_id=api_id), self.assertRaises(BrokerError) as error:
                self.service.use(token, "subscription-a", PROFILE, OWNER, api_id, "get-me")
            self.assertEqual(400, error.exception.status)

    def test_daily_rotation_survives_response_loss_and_uses_fixed_previous_grace(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.store.now += self.service.settings.rotation_interval_seconds
        lost = self.use(token)["rotation"]
        self.assertTrue(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        with self.assertRaises(BrokerError):
            self.service.authorize(lost["candidateSession"], "subscription-a", PROFILE)
        rotation = self.use(token)["rotation"]
        before = self.service.authorize(token, "subscription-a", PROFILE)
        committed = self.commit(token, rotation)
        self.assertEqual(2, committed["generation"])
        self.assertEqual(before["idleExpiresAt"], committed["idleExpiresAt"])
        self.assertEqual(before["absoluteExpiresAt"], committed["absoluteExpiresAt"])
        self.store.now += 10
        retry = self.commit(token, rotation)
        self.assertEqual(committed["previousValidUntil"], retry["previousValidUntil"])
        previous = self.use(token)
        self.assertNotIn("rotation", previous)
        self.assertEqual("previous", previous["credentialKind"])
        self.store.now = committed["previousValidUntil"]
        with self.assertRaises(BrokerError):
            self.service.authorize(token, "subscription-a", PROFILE)
        self.assertTrue(self.service.authorize(rotation["candidateSession"], "subscription-a", PROFILE)["allowed"])

    def test_concurrent_candidate_loser_is_update_conflict_without_reauthentication(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.store.now += self.service.settings.rotation_interval_seconds
        first, second = self.use(token)["rotation"], self.use(token)["rotation"]
        self.commit(token, first)
        with self.assertRaises(BrokerError) as error:
            self.commit(token, second)
        self.assertEqual((409, "SessionRotationSuperseded"), (error.exception.status, error.exception.code))
        self.assertTrue(self.service.authorize(first["candidateSession"], "subscription-a", PROFILE)["allowed"])

    def test_expired_candidate_is_distinct_from_superseded_and_does_not_require_login(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.store.now += self.service.settings.rotation_interval_seconds
        rotation = self.use(token)["rotation"]
        self.store.now = rotation["expiresAt"]
        with self.assertRaises(BrokerError) as error:
            self.commit(token, rotation)
        self.assertEqual((409, "SessionRotationExpired"), (error.exception.status, error.exception.code))
        self.assertTrue(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        retry = self.use(token)
        self.assertEqual("SessionUpdateRequired", retry["errorCode"])
        self.commit(token, retry["rotation"])
        self.assertTrue(self.use(retry["rotation"]["candidateSession"])["allowed"])

    def test_unacknowledged_rotation_blocks_business_without_extending_idle(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.store.now += self.service.settings.rotation_interval_seconds
        original = self.use(token)
        self.store.now += self.service.settings.rotation_handoff_seconds
        blocked = self.use(token)
        self.assertEqual((False, 409, "SessionUpdateRequired"),
            (blocked["allowed"], blocked["status"], blocked["errorCode"]))
        self.assertEqual(original["idleExpiresAt"], blocked["idleExpiresAt"])
        self.commit(token, blocked["rotation"])
        self.assertTrue(self.use(blocked["rotation"]["candidateSession"])["allowed"])

    def test_absolute_lifetime_caps_active_usage_and_rotation_cannot_restore_revocation(self):
        self.service.settings = replace(self.service.settings, idle_timeout_seconds=300,
            absolute_lifetime_seconds=900, rotation_interval_seconds=100)
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        absolute = self.service.authorize(token, "subscription-a", PROFILE)["absoluteExpiresAt"]
        for _ in range(8):
            self.store.now += 100
            result = self.use(token)
            rotation = result["rotation"]
            self.commit(token, rotation)
            token = rotation["candidateSession"]
            self.assertEqual(absolute, result["absoluteExpiresAt"])
        self.store.now = absolute
        self.assertFalse(self.use(token)["allowed"])

    def test_logout_all_and_disconnect_invalidate_every_related_session_before_cm_cleanup(self):
        first, _ = self.completed_flow()
        self.cm.connected = True
        second, _ = self.completed_flow()
        self.service.logout_all(first["provisionalConnectionSession"], "subscription-a")
        self.assertFalse(self.service.authorize(second["provisionalConnectionSession"], "subscription-a", PROFILE)["allowed"])
        third, _ = self.completed_flow()
        token = third["provisionalConnectionSession"]
        self.cm.deletion_confirmed = False
        self.assertEqual("disconnect_pending", self.service.disconnect(token, "subscription-a")["status"])
        self.assertFalse(self.service.authorize(token, "subscription-a", PROFILE)["allowed"])
        self.assertEqual("deleting", next(iter(self.store.mappings.values()))["state"])
        self.assertEqual(1, len(self.cm.deleted))

    def test_cm_null_is_not_automatically_treated_as_expired_identity(self):
        start, _ = self.completed_flow()
        token = start["provisionalConnectionSession"]
        self.assertTrue(self.service.connection_status(token, "subscription-a", PROFILE)["reauthenticationRequired"])
        for state in ["connected", "unavailable"]:
            self.cm.connection_status = lambda *args: state
            with self.assertRaises(BrokerError) as error:
                self.service.connection_status(token, "subscription-a", PROFILE)
            self.assertEqual((503, "ConnectionUnavailable"), (error.exception.status, error.exception.code))


if __name__ == "__main__":
    unittest.main()
