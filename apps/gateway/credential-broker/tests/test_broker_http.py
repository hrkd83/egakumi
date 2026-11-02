"""HTTP trust boundaries, browser cookies, and sanitized failures."""
from dataclasses import replace
import unittest
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from credential_broker.app import create_app
from credential_broker.service import COOKIE_NAME
from credential_broker.store import StoreRotationExpired
from _broker_support import BrokerTestCase, KEY, ORIGIN, PROFILE


class BrokerHttpTests(BrokerTestCase):
    def client(self):
        return TestClient(create_app(self.service))

    def headers(self, **extra):
        return {"X-Core-Broker-Key": KEY, "X-Core-Subscription-Id": "subscription-a", **extra}

    def test_shared_key_is_checked_before_claimed_subscription(self):
        with self.client() as client:
            for headers in [{"X-Core-Subscription-Id": "subscription-a"}, self.headers(**{"X-Core-Broker-Key": "wrong"})]:
                response = client.post("/v1/start", json={"providerName": PROFILE}, headers=headers)
                self.assertEqual(401, response.status_code)
            response = client.post("/v1/start", json={"providerName": PROFILE}, headers=[
                ("X-Core-Broker-Key", KEY), ("X-Core-Broker-Key", KEY), ("X-Core-Subscription-Id", "subscription-a")])
            self.assertEqual(401, response.status_code)
            self.assertFalse(self.store.transactions)
            self.assertEqual({"status": "ok"}, client.get("/health").json())

    def test_launch_sets_secure_cookie_and_rejects_duplicate_json(self):
        with self.client() as client:
            start = client.post("/v1/start", json={"providerName": PROFILE}, headers=self.headers()).json()
            data = {k: v[0] for k, v in parse_qs(urlsplit(start["loginUrl"]).fragment).items()}
            response = client.post("/v1/launch", json=data, headers=self.headers(Origin=ORIGIN))
            self.assertEqual(200, response.status_code)
            cookie = response.headers["set-cookie"]
            for attribute in [COOKIE_NAME + "=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"]:
                self.assertIn(attribute, cookie)
            self.assertIn("no-store", response.headers["cache-control"])
            response = client.post("/v1/start", content='{"providerName":"x","providerName":"y"}', headers=self.headers())
            self.assertEqual(400, response.status_code)

    def test_session_body_cannot_override_trusted_subscription(self):
        with self.client() as client:
            response = client.post("/v2/session/authorize", json={"connectionSession": "cs2." + "0" * 64 + "." + "1" * 64,
                "subscriptionId": "subscription-b", "profileName": PROFILE}, headers=self.headers())
            self.assertEqual(403, response.status_code)

    def test_v2_routes_require_exact_body_and_never_accept_caller_subject_override(self):
        token = "cs2." + "0" * 64 + "." + "1" * 64
        with self.client() as client:
            for path, extra in [
                ("authorize", {"profileName": PROFILE}),
                ("use", {"profileName": PROFILE, "verifiedOwnerId": "user", "apiId": "graph", "operationId": "me"}),
                ("commit", {"rotationId": "2" * 64, "candidateSession": token, "fromGeneration": 1}),
                ("logout-all", {}), ("disconnect", {})]:
                body = {"connectionSession": token, "subscriptionId": "subscription-a", **extra}
                self.assertEqual(401, client.post("/v2/session/" + path, json=body).status_code)
                self.assertEqual(400, client.post("/v2/session/" + path,
                    json={**body, "ownerId": "spoofed-user"}, headers=self.headers()).status_code)
                self.assertEqual(403, client.post("/v2/session/" + path,
                    json={**body, "subscriptionId": "subscription-b"}, headers=self.headers()).status_code)
            self.assertEqual(404, client.post("/v1/session", json={}, headers=self.headers()).status_code)

    def test_expired_update_candidate_has_a_distinct_safe_http_error(self):
        def expired(*args):
            raise StoreRotationExpired("private-do-not-log")
        self.store.commit_rotation = expired
        token = "cs2." + "0" * 64 + "." + "1" * 64
        with self.client() as client:
            response = client.post("/v2/session/commit", json={"connectionSession": token,
                "subscriptionId": "subscription-a", "rotationId": "2" * 64,
                "candidateSession": token, "fromGeneration": 1}, headers=self.headers())
        self.assertEqual(409, response.status_code)
        self.assertEqual("SessionRotationExpired", response.json()["error"]["code"])
        self.assertNotIn("private", response.text)
        self.assertNotIn(token, response.text)

    def test_logout_accepts_apim_generated_body_without_forwarding_secret_header(self):
        calls = []
        self.service.logout = lambda token, sub: calls.append((token, sub)) or {"status": "signed_out"}
        token = "cs2." + "0" * 64 + "." + "1" * 64
        with self.client() as client:
            response = client.post("/v1/logout", json={"connectionSession": token, "subscriptionId": "subscription-a"},
                headers=self.headers())
            self.assertEqual(200, response.status_code)
            self.assertEqual([(token, "subscription-a")], calls)
            response = client.post("/v1/logout", json={"connectionSession": token, "subscriptionId": "other"},
                headers=self.headers())
            self.assertEqual(403, response.status_code)
            self.assertEqual(1, len(calls))

    def test_challenge_http_contract_requires_trusted_key_and_exact_body(self):
        body = {"providerName": PROFILE, "connectionSession": "", "reason": "session_required"}
        with self.client() as client:
            self.assertEqual(401, client.post("/v1/challenge", json=body).status_code)
            response = client.post("/v1/challenge", json=body, headers=self.headers())
            self.assertEqual(200, response.status_code)
            self.assertIn("statusUrl", response.json())
            self.assertIn("no-store", response.headers["cache-control"])
            for invalid in [{**body, "subscriptionId": "spoofed"}, {**body, "reason": {}},
                    {"providerName": PROFILE, "connectionSession": ""}]:
                self.assertEqual(400, client.post("/v1/challenge", json=invalid, headers=self.headers()).status_code)

    def test_login_rate_limit_has_no_proof_and_includes_retry_after(self):
        self.service.settings = replace(self.service.settings, login_calls_per_minute=1)
        with self.client() as client:
            client.post("/v1/start", json={"providerName": PROFILE}, headers=self.headers())
            response = client.post("/v1/challenge", json={"providerName": PROFILE, "connectionSession": "",
                "reason": "session_required"}, headers=self.headers())
            self.assertEqual(429, response.status_code)
            self.assertEqual("60", response.headers["retry-after"])
            self.assertEqual("ConnectionRateLimited", response.json()["error"]["code"])
            self.assertNotIn("loginUrl", response.json())

    def test_internal_failure_is_fixed_and_startup_failure_is_sanitized(self):
        original = self.store.create_transaction
        self.store.create_transaction = lambda *args: (_ for _ in ()).throw(RuntimeError("secret-do-not-log"))
        with self.client() as client:
            response = client.post("/v1/start", json={"providerName": PROFILE}, headers=self.headers())
            self.assertEqual(503, response.status_code)
            self.assertEqual("ConnectionUnavailable", response.json()["error"]["code"])
            self.assertNotIn("secret", response.text)
        self.store.create_transaction = original
        self.store.ready_error = True
        with self.assertRaisesRegex(RuntimeError, "^Credential broker initialization failed$") as error:
            with self.client():
                self.fail("Must fail startup when SQL is unavailable")
        self.assertTrue(error.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
