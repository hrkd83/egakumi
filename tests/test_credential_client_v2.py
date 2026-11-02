"""本人セッションの保存境界・日次更新・業務再送防止を、実通信なしで検査する。"""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from credential_client import CredentialClient, ConnectionError, LoginRequired, NoRedirect, ROTATION_HEADERS
from credential_session_store import MemorySessionStore


ORIGIN = "https://gateway.example.com"
PATH = "/tools/graph/v1/me"
TX, POLL, LAUNCH, SID, SECRET = [character * 64 for character in "abcde"]
CURRENT, NEXT = f"cs2.{SID}.{SECRET}", f"cs2.{SID}.{'1' * 64}"
SUBJECT = {"tenantId": "11111111-2222-4333-8444-555555555555", "ownerId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}


def authentication():
    return {"protocolVersion": 2, "status": "need_login", "transactionId": TX, "pollSecret": POLL,
            "provisionalConnectionSession": CURRENT, "expiresIn": 600,
            "loginUrl": ORIGIN + "/auth/connections/browser/launch#transactionId=" + TX + "&launch=" + LAUNCH,
            "statusUrl": ORIGIN + "/auth/connections/status?transactionId=" + TX}


def metadata(generation=1):
    return {"protocolVersion": 2, "sessionId": SID, "generation": generation,
            "idleExpiresAt": 2000000000, "absoluteExpiresAt": 2100000000,
            "nextRotationAt": 1900000000 + generation * 86400, "expiresAt": 2000000000}


def completed():
    return {"status": "complete", "providerName": "graph-delegated-a", **metadata(), **SUBJECT}


def rotation():
    return {"rotationId": "f" * 64, "candidateSession": NEXT, "fromGeneration": 1, "generation": 2, "expiresAt": 2000000300}


class Response:
    def __init__(self, value, status=200, headers=None):
        self.value, self.status, self.headers = value, status, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return json.dumps(self.value).encode()[:limit]


class Opener:
    def __init__(self, values, before_open=None):
        self.values, self.requests, self.before_open = list(values), [], before_open
    def open(self, request, **kwargs):
        self.requests.append(request)
        if self.before_open: self.before_open(request)
        value = self.values.pop(0)
        if isinstance(value, Exception): raise value
        return value if isinstance(value, Response) else Response(value)


def client(values=(), *, saved=False, store=None, **kwargs):
    state = {"protocolVersion": 2, "status": "complete", "connectionSession": CURRENT, **metadata(), **SUBJECT} if saved else None
    store = store or MemorySessionStore(state)
    opener = Opener(values)
    return CredentialClient(ORIGIN, "private-key", store=store, opener=opener,
                            expected_subject=SUBJECT, subscription_id="business-subscription", **kwargs), opener, store


class SessionAuthenticationTests(unittest.TestCase):
    def test_challenge_is_persisted_before_login_url_can_be_read(self):
        instance, opener, store = client([Response({"error": {"code": "ConnectionRequired"}, "authentication": authentication()}, 409)])
        continuation = instance.get_api()
        self.assertIsInstance(continuation, LoginRequired)
        state = store.read()
        self.assertEqual(state["connectionSession"], CURRENT)
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["authentication"]["pollSecret"], POLL)
        self.assertNotIn(POLL, continuation.login_url)
        self.assertNotIn(SECRET, continuation.login_url)
        self.assertEqual(len(opener.requests), 1)

    def test_failed_persistence_never_exposes_login_url(self):
        class BrokenStore(MemorySessionStore):
            def write(self, *args, **kwargs): raise OSError("disk unavailable")
        instance, _, _ = client([Response({"error": {"code": "ConnectionRequired"}, "authentication": authentication()}, 409)], store=BrokenStore())
        with self.assertRaises(ConnectionError): instance.get_api()

    def test_status_binds_verified_subject_and_retains_independent_initial_value(self):
        instance, opener, store = client([authentication(), {"status": "pending"}, completed(), {"id": SUBJECT["ownerId"]}])
        started = instance.start("graph-delegated")
        self.assertEqual(store.read()["connectionSession"], CURRENT)
        self.assertEqual(instance.status(TX, POLL), {"status": "pending"})
        result = instance.status(TX, POLL)
        self.assertNotIn("connectionSession", result)
        self.assertNotIn("pollSecret", result)
        self.assertEqual(store.read()["connectionSession"], CURRENT)
        self.assertNotIn("authentication", store.read())
        self.assertEqual(instance.get_api(), {"id": SUBJECT["ownerId"]})
        self.assertEqual(opener.requests[2].get_header("X-core-connection-poll"), POLL)
        self.assertEqual(opener.requests[3].get_header("X-core-connection-session"), CURRENT)
        self.assertNotIn(POLL, opener.requests[2].full_url)

    def test_polling_old_transaction_cannot_recover_or_overwrite_live_session(self):
        instance, _, store = client([completed()], saved=True)
        original = store.read()
        with self.assertRaisesRegex(ConnectionError, "cannot recover"): instance.status(TX, POLL)
        self.assertEqual(store.read(), original)

    def test_status_never_accepts_session_value_in_response(self):
        instance, _, _ = client([authentication(), {**completed(), "connectionSession": CURRENT}])
        instance.start("graph-delegated")
        with self.assertRaisesRegex(ConnectionError, "must not disclose"): instance.status(TX, POLL)

    def test_wrong_person_cannot_complete_application_login(self):
        instance, _, store = client([authentication(), {**completed(), "ownerId": SUBJECT["tenantId"]}])
        instance.start("graph-delegated")
        with self.assertRaises(ConnectionError) as failure: instance.status(TX, POLL)
        self.assertEqual(failure.exception.diagnostics["reason"], "identity-mismatch")
        self.assertEqual(store.read()["status"], "pending")

    def test_business_get_resumes_only_once_after_authentication(self):
        instance, opener, _ = client([Response({"error": {"code": "ConnectionRequired"}, "authentication": authentication()}, 409),
                                      {"status": "pending"}, completed(), {"id": "person"}])
        continuation = instance.get_api()
        self.assertIs(continuation.resume(), continuation)
        self.assertEqual(continuation.resume(), {"id": "person"})
        with self.assertRaises(ConnectionError): continuation.resume()
        self.assertEqual([request.method for request in opener.requests], ["GET"] * 4)

    def test_invalid_initial_session_and_origin_are_rejected(self):
        for changes in ({"protocolVersion": 1}, {"provisionalConnectionSession": TX + "." + POLL},
                        {"provisionalConnectionSession": f"cs2.{TX}.{SECRET}"},
                        {"provisionalConnectionSession": f"cs2.{SID}.{POLL}"}, {"expiresIn": True},
                        {"loginUrl": authentication()["loginUrl"].replace(ORIGIN, "https://other.example.com")},
                        {"statusUrl": "https://other.example.com/status"}):
            with self.subTest(changes=tuple(changes)):
                instance, _, store = client([{**authentication(), **changes}])
                with self.assertRaises(ConnectionError): instance.start("graph-delegated")
                self.assertNotIn("connectionSession", store.read())

    def test_binding_prevents_other_subject_origin_subscription_or_profile_reuse(self):
        instance, _, store = client([authentication()])
        instance.start("graph-delegated")
        for overrides in ({"origin": "https://other.example.com"}, {"subscription_id": "another"},
                          {"profile": "other-profile"}, {"expected_subject": {**SUBJECT, "ownerId": SUBJECT["tenantId"]}}):
            args = {"origin": ORIGIN, "api_key": "private-key", "store": store, "expected_subject": SUBJECT,
                    "subscription_id": "business-subscription", **overrides}
            with self.subTest(changes=tuple(overrides)), self.assertRaises(ValueError): CredentialClient(**args)

    def test_paths_headers_and_legacy_values_cannot_leak_credentials(self):
        instance, opener, _ = client()
        for path in ("https://other.example.com/me", "//other/me", "/../me", "/tools/%2e%2e/me", "/auth/connections/start", "/me?x=y"):
            with self.subTest(path=path), self.assertRaises(ValueError): instance.get_api(path)
        with self.assertRaises(ValueError): instance.get_api(connection_session=TX + "." + POLL)
        with self.assertRaises(ConnectionError): NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example.com")
        self.assertEqual(opener.requests, [])

    def test_claims_challenge_and_permission_denial_remain_safe_distinct_codes(self):
        for code in ("ConnectionClaimsRequired", "ConnectionPermissionDenied"):
            instance, opener, _ = client([Response({"error": {"code": code, "claims": "PRIVATE_CLAIMS"}}, 403)], saved=True)
            with self.subTest(code=code), self.assertRaises(ConnectionError) as failure: instance.get_api()
            self.assertEqual(failure.exception.diagnostics["errorCode"], code)
            self.assertNotIn("PRIVATE_CLAIMS", str(failure.exception))
            self.assertEqual(len(opener.requests), 1)


class SessionRotationTests(unittest.TestCase):
    def headers(self):
        return {name: str(rotation()[key]) for key, name in ROTATION_HEADERS.items()}

    def test_successful_business_keeps_body_and_commits_only_after_candidate_is_saved(self):
        instance, opener, store = client([Response({"id": "unchanged-business-body"}, headers=self.headers()), metadata(2)], saved=True)
        def before_open(request):
            if request.method == "POST":
                self.assertEqual(store.read()["pendingRotation"], rotation())
                self.assertEqual(store.read()["connectionSession"], CURRENT)
        opener.before_open = before_open
        self.assertEqual(instance.get_api(), {"id": "unchanged-business-body"})
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST"])
        commit = opener.requests[1]
        self.assertEqual(commit.full_url, ORIGIN + "/auth/connections/session/commit")
        self.assertEqual(json.loads(commit.data), {key: rotation()[key] for key in ("rotationId", "candidateSession", "fromGeneration")})
        self.assertEqual(commit.get_header("X-core-connection-session"), CURRENT)
        self.assertEqual(store.read()["connectionSession"], NEXT)
        self.assertEqual(store.read()["generation"], 2)
        self.assertNotIn("pendingRotation", store.read())

    def test_lost_commit_response_preserves_success_and_retries_only_commit_before_next_business(self):
        instance, opener, store = client([Response({"id": "first"}, headers=self.headers()), URLError(TimeoutError()),
                                          metadata(2), {"id": "second"}], saved=True)
        self.assertEqual(instance.get_api(), {"id": "first"})
        self.assertTrue(instance.session_update_pending)
        self.assertEqual(store.read()["pendingRotation"], rotation())
        self.assertEqual(instance.get_api(), {"id": "second"})
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST", "POST", "GET"])
        self.assertEqual(opener.requests[1].data, opener.requests[2].data)
        self.assertEqual(opener.requests[3].get_header("X-core-connection-session"), NEXT)
        self.assertFalse(instance.session_update_pending)

    def test_process_restart_recovers_saved_candidate_with_same_commit_request(self):
        instance, _, store = client([Response({"id": "already-done"}, headers=self.headers()), URLError("offline")], saved=True)
        instance.get_api()
        restarted, opener, _ = client([metadata(2), {"id": "next"}], store=store)
        self.assertEqual(restarted.get_api(), {"id": "next"})
        self.assertEqual([request.method for request in opener.requests], ["POST", "GET"])
        self.assertEqual(opener.requests[0].get_header("X-core-connection-session"), NEXT)

    def test_candidate_not_yet_committed_falls_back_to_current_proof_and_expired_candidate_is_discarded(self):
        instance, opener, store = client([Response({"id": "already-done"}, headers=self.headers()), URLError("lost"),
            Response({"error": {"code": "ConnectionNotAuthorized"}}, 403),
            Response({"error": {"code": "SessionRotationExpired"}}, 409), {"id": "next"}], saved=True)
        self.assertEqual(instance.get_api(), {"id": "already-done"})
        self.assertEqual(instance.get_api(), {"id": "next"})
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST", "POST", "POST", "GET"])
        self.assertEqual(opener.requests[2].get_header("X-core-connection-session"), NEXT)
        self.assertEqual(opener.requests[3].get_header("X-core-connection-session"), CURRENT)
        self.assertEqual(store.read()["connectionSession"], CURRENT)
        self.assertNotIn("pendingRotation", store.read())
        self.assertNotIn("rotationCommitAttempted", store.read())

    def test_business_failure_still_processes_update_and_keeps_original_http_status(self):
        instance, opener, store = client([Response({"error": {"code": "TooManyRequests"}}, status=429, headers=self.headers()), metadata(2)], saved=True)
        with self.assertRaises(ConnectionError) as failure: instance.get_api()
        self.assertEqual(failure.exception.diagnostics["httpStatus"], 429)
        self.assertEqual(store.read()["connectionSession"], NEXT)
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST"])

    def test_preexecution_update_required_is_the_only_rotation_that_retries_business(self):
        instance, opener, _ = client([Response({"error": {"code": "SessionUpdateRequired"}, "rotation": rotation()}, 409),
                                      metadata(2), {"id": "executed-once"}], saved=True)
        self.assertEqual(instance.get_api(), {"id": "executed-once"})
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST", "GET"])

    def test_repeated_preexecution_rejection_does_not_loop(self):
        response = Response({"error": {"code": "SessionUpdateRequired"}, "rotation": rotation()}, 409)
        instance, opener, _ = client([response, metadata(2), response], saved=True)
        with self.assertRaises(ConnectionError): instance.get_api()
        self.assertEqual(len(opener.requests), 3)

    def test_competing_old_generation_cannot_roll_saved_value_back(self):
        instance, _, store = client([Response({"id": "done"}, headers=self.headers()), metadata(2)], saved=True)
        instance.get_api()
        old_state = store.read()
        with self.assertRaises(ConnectionError): instance._persist_rotation(rotation())
        self.assertEqual(store.read(), old_state)
        with self.assertRaises(ValueError): instance.get_api(connection_session=CURRENT)

    def test_superseded_candidate_is_not_mistaken_for_reauthentication(self):
        instance, opener, store = client([Response({"id": "done"}, headers=self.headers()),
                                          Response({"error": {"code": "SessionRotationSuperseded"}}, 409),
                                          Response({"error": {"code": "SessionRotationSuperseded"}}, 409)], saved=True)
        self.assertEqual(instance.get_api(), {"id": "done"})
        with self.assertRaises(ConnectionError) as failure: instance.get_api()
        self.assertEqual(failure.exception.diagnostics["errorCode"], "SessionRotationSuperseded")
        self.assertEqual([request.method for request in opener.requests], ["GET", "POST", "POST"])
        self.assertNotIn("authentication", store.read())

    def test_rotation_does_not_change_absolute_expiry(self):
        instance, _, store = client([Response({"id": "done"}, headers=self.headers()), metadata(2)], saved=True)
        initial = store.read()["absoluteExpiresAt"]
        instance.get_api()
        self.assertEqual(store.read()["absoluteExpiresAt"], initial)

    def test_invalid_rotation_does_not_send_secrets_to_commit(self):
        for changes in ({"candidateSession": f"cs2.{TX}.{'1' * 64}"}, {"candidateSession": CURRENT},
                        {"fromGeneration": 2}, {"fromGeneration": True}, {"generation": 3}, {"rotationId": "short"}, {"expiresAt": float("nan")}):
            instance, opener, _ = client([Response({"error": {"code": "SessionUpdateRequired"}, "rotation": {**rotation(), **changes}}, 409)], saved=True)
            with self.subTest(changes=tuple(changes)), self.assertRaises(ConnectionError): instance.get_api()
            self.assertEqual(len(opener.requests), 1)

    def test_logout_variants_use_proof_not_free_form_user_identifiers(self):
        for operation in ("logout", "logout_all", "disconnect"):
            instance, opener, store = client([{"status": "signed_out"}], saved=True)
            getattr(instance, operation)()
            self.assertEqual(opener.requests[0].get_header("X-core-connection-session"), CURRENT)
            self.assertIsNone(opener.requests[0].data)
            self.assertEqual(store.read()["status"], "signed_out")
            self.assertNotIn("connectionSession", store.read())

    def test_disconnect_pending_202_clears_local_proof_after_sql_revoke(self):
        instance, opener, store = client([Response({"status": "disconnect_pending"}, 202)], saved=True)
        self.assertEqual(instance.disconnect(), {"status": "disconnect_pending"})
        self.assertEqual(store.read()["status"], "signed_out")
        self.assertNotIn("connectionSession", store.read())
        self.assertIsNone(opener.requests[0].data)

    def test_logout_recovers_lost_commit_ack_with_active_candidate_after_old_value_grace(self):
        for operation in ("logout", "logout_all", "disconnect"):
            instance, opener, store = client([Response({"id": "already-done"}, headers=self.headers()), URLError("lost ack"),
                                              metadata(2), {"status": "signed_out"}], saved=True)
            with self.subTest(operation=operation):
                self.assertEqual(instance.get_api(), {"id": "already-done"})
                self.assertTrue(store.read()["rotationCommitAttempted"])
                if operation == "logout":
                    # 以前の呼出側スナップショットを渡しても、新しい共有状態を優先する。
                    instance.logout(CURRENT)
                else:
                    getattr(instance, operation)()
                self.assertEqual([request.method for request in opener.requests], ["GET", "POST", "POST", "POST"])
                self.assertEqual(opener.requests[2].get_header("X-core-connection-session"), NEXT)
                self.assertEqual(opener.requests[3].get_header("X-core-connection-session"), NEXT)
                self.assertEqual(store.read()["status"], "signed_out")
                self.assertNotIn("connectionSession", store.read())
                self.assertNotIn("pendingRotation", store.read())


class CredentialCliV2Tests(unittest.TestCase):
    def test_cli_persists_before_print_and_uses_saved_value_after_metadata_only_completion(self):
        instance, opener, store = client([Response({"error": {"code": "ConnectionRequired"}, "authentication": authentication()}, 409), completed(), {"id": "person"}])
        output = StringIO()
        def print_checked(value):
            if value.startswith("Open this temporary login URL:"):
                self.assertEqual(store.read()["status"], "pending")
                self.assertEqual(store.read()["connectionSession"], CURRENT)
            output.write(value + "\n")
        with patch("credential_client.CredentialClient", return_value=instance), patch("integration_client.private_text", return_value="private-key"), \
                patch("credential_client.FileSessionStore", return_value=store), patch("builtins.print", side_effect=print_checked):
            import credential_client
            self.assertEqual(credential_client.main(["--origin", ORIGIN, "--api-path", PATH, "--key-file", "key", "--session-file", "state"]), 0)
        self.assertEqual(opener.requests[-1].get_header("X-core-connection-session"), CURRENT)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["response"], {"id": "person"})
        for secret in (POLL, SECRET, CURRENT, "private-key"):
            self.assertNotIn(secret, output.getvalue())
