"""Real JWT signature checks and isolated MSAL/ARM protocol contracts."""
from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import msal
from cryptography.hazmat.primitives.asymmetric import rsa

from credential_broker.cm import CmClient, CmError, allowed_login_url
from credential_broker.identity import IdentityError, JwtVerifier, OidcClient
from credential_broker.settings import Profile, Settings, SettingsError


TENANT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
INFRA = "44444444-4444-4444-8444-444444444444"
PRINCIPAL = "55555555-5555-4555-8555-555555555555"
PROFILE = Profile("graph-delegated", TENANT, CLIENT, "test-oidc-secret", pools=(("graph-delegated-a", 8000),))
SETTINGS = Settings("https://gateway.example.test", "x" * 40, {PROFILE.name: PROFILE},
    subscription_id=INFRA, resource_group="rg-test-svc", apim_name="apim-test",
    apim_principal_id=PRINCIPAL, azure_tenant_id=INFRA, managed_identity_client_id=CLIENT)


class MetadataClient:
    """Supply Entra discovery locally while exercising the real MSAL flow creator."""
    def get(self, url, **kwargs):
        return SimpleNamespace(status_code=200, text=json.dumps({
            "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
            "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0"}), headers={})


class IdentityTests(unittest.TestCase):
    def test_real_msal_produces_state_nonce_and_pkce(self):
        client = OidcClient(SETTINGS.public_origin, application_factory=lambda profile:
            msal.ConfidentialClientApplication(profile.client_id, profile.client_secret,
                authority="https://login.microsoftonline.com/" + profile.tenant_id,
                instance_discovery=False, http_client=MetadataClient()))
        envelope = client.begin(PROFILE)
        query = parse_qs(urlsplit(envelope["auth_uri"]).query)
        self.assertEqual([envelope["state"]], query["state"])
        self.assertEqual([envelope["expectedNonce"]], query["nonce"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertIn("code_verifier", envelope["msalFlow"])
        self.assertIn("openid", query["scope"][0].split())
        self.assertIn("profile", query["scope"][0].split())
        self.assertNotIn("access_token", envelope["msalFlow"])

    def test_signed_id_token_rejects_wrong_audience_issuer_nonce_expiry_and_key(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT,
            "tid": TENANT, "oid": OWNER, "sub": "pairwise-subject", "nonce": "request-nonce",
            "iat": now, "exp": now + 300, "preferred_username": "member@example.test"}
        verifier = JwtVerifier()
        endpoint = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
        verifier._keys[endpoint] = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
        token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "one"})
        self.assertEqual(OWNER, verifier.verify(token, PROFILE, "request-nonce")["oid"])
        for changed in [{"aud": INFRA}, {"iss": "https://evil.example"}, {"nonce": "other"}, {"exp": now - 60}]:
            with self.subTest(changed=changed), self.assertRaises(Exception):
                verifier.verify(jwt.encode(dict(claims, **changed), private, algorithm="RS256"), PROFILE, "request-nonce")
        with self.assertRaises(Exception):
            verifier.verify(jwt.encode(claims, wrong_key, algorithm="RS256"), PROFILE, "request-nonce")

    def test_complete_drops_tokens_and_rejects_other_tenant_or_state(self):
        class Application:
            calls = 0
            def acquire_token_by_auth_code_flow(self, flow, response):
                self.calls += 1
                return {"id_token": "private-id-token", "access_token": "private-access-token", "refresh_token": "private-refresh-token"}
        app = Application()
        claims = {"tid": TENANT, "oid": OWNER, "preferred_username": "member@example.test"}
        verifier = SimpleNamespace(verify=lambda *args: claims)
        client = OidcClient(SETTINGS.public_origin, application_factory=lambda profile: app, verifier=verifier)
        flow = {"state": "a" * 64, "expectedNonce": "nonce", "msalFlow": {"code_verifier": "proof"}}
        result = client.complete(PROFILE, flow, {"state": "a" * 64, "code": "one-time-code"})
        self.assertEqual(OWNER, result.object_id)
        self.assertNotIn("token", repr(result))
        with self.assertRaises(IdentityError):
            client.complete(PROFILE, flow, {"state": "b" * 64, "code": "one-time-code"})
        self.assertEqual(1, app.calls)
        claims["tid"] = INFRA
        with self.assertRaises(IdentityError) as error:
            client.complete(PROFILE, flow, {"state": "a" * 64, "code": "one-time-code"})
        self.assertNotIn("private", str(error.exception))


class CmTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        def handler(request):
            self.requests.append(request)
            code, value = self.responses.pop(0)
            return httpx.Response(code, json=value)
        http = httpx.Client(transport=httpx.MockTransport(handler), headers={"Accept": "application/json"})
        credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="private-arm-token"))
        self.cm = CmClient(SETTINGS, credential=credential, client=http)

    def test_create_connection_and_grant_only_configured_apim_identity(self):
        self.responses = [(404, {}), (201, {}), (201, {})]
        self.cm.ensure_connection("graph-delegated-a", "member-0123456789abcdef")
        self.assertEqual(["GET", "PUT", "PUT"], [item.method for item in self.requests])
        self.assertTrue(all(item.url.host == "management.azure.com" for item in self.requests))
        self.assertEqual({"properties": {"authorizationType": "OAuth2", "oauth2grantType": "AuthorizationCode"}}, json.loads(self.requests[1].content))
        grant = json.loads(self.requests[2].content)
        self.assertEqual({"tenantId": INFRA, "objectId": PRINCIPAL}, grant["properties"])
        self.assertTrue(self.requests[2].url.path.endswith("/accessPolicies/" + PRINCIPAL))
        self.assertEqual("2024-05-01", self.requests[2].url.params["api-version"])

    def test_existing_connection_and_unknown_provider(self):
        self.responses = [(200, {"properties": {"status": "Connected"}}), (200, {})]
        self.cm.ensure_connection("graph-delegated-a", "member-0123456789abcdef")
        self.assertEqual(["GET", "PUT"], [item.method for item in self.requests])
        with self.assertRaises(CmError):
            self.cm.ensure_connection("unconfigured", "member")
        self.assertEqual(2, len(self.requests))

    def test_regional_login_link_and_protocol_failures(self):
        good = "https://logic-apis-japaneast.consent.azure-apim.net/authorize?code=opaque"
        self.responses = [(200, {"loginLink": good})]
        result = self.cm.login_link("graph-delegated-a", "member", SETTINGS.public_origin + "/auth/connections/browser/callback?transactionId=x&state=y")
        self.assertEqual(good, result)
        self.assertEqual("POST", self.requests[0].method)
        for value in ["http://logic-apis-japaneast.consent.azure-apim.net", good.replace(".net", ".net.evil.example"),
                      "https://user@logic-apis-japaneast.consent.azure-apim.net", good + "#fragment",
                      "https://logic-apis-japaneast.consent.azure-apim.net:8443"]:
            self.assertFalse(allowed_login_url(value), value)
        with self.assertRaises(CmError):
            self.cm.login_link("graph-delegated-a", "member", "https://evil.example/auth/connections/browser/callback")
        self.responses = [(403, {"error": {"message": "secret-in-body"}})]
        with self.assertRaises(CmError) as error:
            self.cm.is_connected("graph-delegated-a", "member")
        self.assertNotIn("secret", str(error.exception))

    def test_only_explicit_cm_authentication_failures_request_reauthentication(self):
        cases = [
            (404, {}, "requires_reauthentication"),
            (200, {"properties": {"status": "Connected"}}, "connected"),
            (200, {"properties": {"status": "Disconnected"}}, "requires_reauthentication"),
            (200, {"properties": {"status": "Error", "error": {"code": "invalid_grant"}}}, "requires_reauthentication"),
            (200, {"properties": {"status": "Error", "error": {"code": "BackendTimeout"}}}, "unavailable"),
            (200, {"properties": {}}, "unavailable")]
        for status, payload, expected in cases:
            with self.subTest(expected=expected, status=status):
                self.responses = [(status, payload)]
                self.assertEqual(expected, self.cm.connection_status("graph-delegated-a", "member"))
        self.responses = [(503, {"error": {"message": "private-token"}})]
        with self.assertRaises(CmError):
            self.cm.connection_status("graph-delegated-a", "member")

    def test_cm_deletion_is_only_confirmed_when_get_returns_404(self):
        self.responses = [(200, {"properties": {"status": "Connected"}}), (202, {}), (200, {"properties": {"status": "Connected"}})]
        self.assertFalse(self.cm.delete_connection("graph-delegated-a", "member"))
        self.responses = [(200, {"properties": {"status": "Connected"}}), (204, {}), (404, {})]
        self.assertTrue(self.cm.delete_connection("graph-delegated-a", "member"))
        self.responses = [(404, {})]
        self.assertTrue(self.cm.delete_connection("graph-delegated-a", "member"))


class SettingsTests(unittest.TestCase):
    def env(self):
        return {"BROKER_PUBLIC_ORIGIN": SETTINGS.public_origin, "BROKER_SHARED_KEY": "x" * 40,
            "BROKER_PROFILES_JSON": json.dumps({PROFILE.name: {"tenantId": TENANT, "clientId": CLIENT,
                "clientSecretEnv": "OIDC_SECRET_0", "identityEndpoint": PROFILE.identity_endpoint,
                "identityIdProperty": "id", "pools": [{"name": "graph-delegated-a", "capacity": 8000}]}}),
            "OIDC_SECRET_0": "super-secret", "AZURE_SUBSCRIPTION_ID": INFRA,
            "AZURE_RESOURCE_GROUP": "rg-test-svc", "APIM_NAME": "apim-test", "APIM_PRINCIPAL_ID": PRINCIPAL,
            "AZURE_TENANT_ID": INFRA, "AZURE_CLIENT_ID": CLIENT}

    def test_secret_not_in_repr_and_missing_config_or_ttl_fail_closed(self):
        env = self.env()
        value = Settings.from_environment(env)
        self.assertNotIn("super-secret", repr(value))
        self.assertEqual(10, value.login_calls_per_minute)
        self.assertEqual((604800, 2592000, 86400),
            (value.idle_timeout_seconds, value.absolute_lifetime_seconds, value.rotation_interval_seconds))
        self.assertEqual(600, value.session_policy["rotationCandidateTtlSeconds"])
        self.assertEqual(25, Settings.from_environment(dict(env, BROKER_LOGIN_CALLS_PER_MINUTE="25")).login_calls_per_minute)
        self.assertNotIn("super-secret", repr(value.profiles[PROFILE.name]))
        for changed in [{"OIDC_SECRET_0": ""}, {"BROKER_SHARED_KEY": "short"},
                        {"BROKER_PUBLIC_ORIGIN": "http://gateway.example"},
                        {"BROKER_IDLE_TIMEOUT_SECONDS": "60", "BROKER_ROTATION_INTERVAL_SECONDS": "61"},
                        {"BROKER_ABSOLUTE_LIFETIME_SECONDS": "60"},
                        {"BROKER_ROTATION_GRACE_SECONDS": "3601"},
                        {"BROKER_ROTATION_CANDIDATE_TTL_SECONDS": "0"}, {"APIM_PRINCIPAL_ID": "0" * 36},
                        {"BROKER_LOGIN_CALLS_PER_MINUTE": "0"}, {"BROKER_LOGIN_CALLS_PER_MINUTE": "10001"}]:
            with self.subTest(changed=changed), self.assertRaises(SettingsError):
                Settings.from_environment(dict(env, **changed))

    def test_settings_match_public_policy_bounds_and_allow_rotation_longer_than_idle(self):
        value = Settings.from_environment(dict(self.env(), BROKER_IDLE_TIMEOUT_SECONDS="600",
            BROKER_ABSOLUTE_LIFETIME_SECONDS="7200", BROKER_ROTATION_INTERVAL_SECONDS="3600",
            BROKER_ROTATION_GRACE_SECONDS="1200", BROKER_ROTATION_HANDOFF_SECONDS="600",
            BROKER_ROTATION_CANDIDATE_TTL_SECONDS="600"))
        self.assertEqual((600, 3600, 1200),
            (value.idle_timeout_seconds, value.rotation_interval_seconds, value.rotation_grace_seconds))
        for changed in [{"BROKER_ROTATION_INTERVAL_SECONDS": "2592000"},
                {"BROKER_ROTATION_INTERVAL_SECONDS": "300", "BROKER_ROTATION_GRACE_SECONDS": "300"},
                {"BROKER_ROTATION_CANDIDATE_TTL_SECONDS": "601"},
                {"BROKER_ROTATION_HANDOFF_SECONDS": "59"}]:
            with self.subTest(changed=changed), self.assertRaises(SettingsError):
                Settings.from_environment(dict(self.env(), **changed))


if __name__ == "__main__":
    unittest.main()
