"""Offline tests for the fixed Graph on-behalf-of exchange and Broker boundary."""
import base64
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from fastapi.testclient import TestClient

from _broker_support import BrokerTestCase, CLIENT, KEY, OWNER, TENANT
from credential_broker.app import create_app
from credential_broker.caller import UserCaller
from credential_broker.obo import KEY_VAULT_SCOPE, GraphOboClient, GraphOboError, base64url
from credential_broker.settings import GraphOboSettings, SettingsError


TEST_VAULT = "kv-obo-example"
KEY_URL = f"https://{TEST_VAULT}.vault.azure.net/keys/gateway-obo/" + "a" * 32
THUMBPRINT = base64url(b"a" * 32)
GRAPH_TOKEN = "synthetic.graph.token"
CALLER = UserCaller(TENANT, OWNER)
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
SETTINGS = GraphOboSettings(TENANT, CLIENT, KEY_URL, THUMBPRINT)


def assertion(**changes):
    claims = {"tid": TENANT, "oid": OWNER, "aud": CLIENT, "iss": ISSUER, "ver": "2.0", "scp": "Gateway.Invoke"}
    return jwt.encode({**claims, **changes}, b"synthetic-jwt-signing-material!!", algorithm="HS256")


class TestGraphOboSettings(unittest.TestCase):
    def test_enabled_configuration_binds_certificate_version(self):
        env = {
            "BROKER_OBO_TENANT_ID": TENANT, "BROKER_OBO_CLIENT_ID": CLIENT,
            "BROKER_OBO_KEY_URL": KEY_URL, "BROKER_OBO_CERTIFICATE_THUMBPRINT": THUMBPRINT,
        }
        self.assertIsNone(GraphOboSettings.from_environment({}))
        self.assertEqual(GraphOboSettings.from_environment(env), SETTINGS)
        for key in env:
            with self.subTest(key=key), self.assertRaises(SettingsError):
                GraphOboSettings.from_environment({k: v for k, v in env.items() if k != key})
        for uri in (KEY_URL.rsplit("/", 1)[0], KEY_URL + "/sign", KEY_URL + "?query", KEY_URL.replace("/gateway-obo/", "/other-key/"),
                    KEY_URL.replace(".vault.azure.net", ".example.com")):
            with self.subTest(uri=uri), self.assertRaises(SettingsError):
                GraphOboSettings.from_environment({**env, "BROKER_OBO_KEY_URL": uri})


class TestGraphOboExchange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.calls = []
        self.mi_scopes = []
        self.token_status = 200
        self.token_result = {"token_type": "Bearer", "scope": "User.Read", "expires_in": 1000, "access_token": GRAPH_TOKEN}
        self.signing_result_changes = {}
        client = httpx.Client(transport=httpx.MockTransport(self.route), follow_redirects=False)
        self.addCleanup(client.close)
        credential = SimpleNamespace(get_token=lambda scope: self.mi_scopes.append(scope) or SimpleNamespace(token="synthetic-mi"))
        self.obo = GraphOboClient(SETTINGS, client, credential, clock=lambda: 1000)

    def route(self, request):
        self.calls.append(request)
        if str(request.url).startswith(KEY_URL + "/sign?"):
            body = json.loads(request.content)
            self.assertEqual(body["alg"], "PS256")
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-mi")
            digest = base64.urlsafe_b64decode(body["value"] + "=")
            signature = self.signing_key.sign(digest, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                                              utils.Prehashed(hashes.SHA256()))
            return httpx.Response(200, json={"kid": KEY_URL, "value": base64url(signature), **self.signing_result_changes})
        self.assertEqual(str(request.url), TOKEN_URL)
        self.assertIsNone(request.headers.get("Authorization"))
        return httpx.Response(self.token_status, json=self.token_result)

    def test_exchanges_validated_assertion_using_versioned_managed_signer(self):
        supplied = assertion()
        self.assertEqual(self.obo.exchange(supplied, CALLER), self.token_result["access_token"])
        self.assertEqual(self.mi_scopes, [KEY_VAULT_SCOPE])
        form = parse_qs(self.calls[1].content.decode("ascii"))
        self.assertEqual(set(form), {"requested_token_use", "client_id", "scope", "assertion", "grant_type",
                                     "client_assertion", "client_assertion_type", "claims"})
        self.assertEqual(form["grant_type"], ["urn:ietf:params:oauth:grant-type:jwt-bearer"])
        self.assertEqual(form["client_assertion_type"], ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"])
        self.assertEqual(form["requested_token_use"], ["on_behalf_of"])
        self.assertEqual(form["client_id"], [CLIENT])
        self.assertEqual(form["assertion"], [supplied])
        self.assertEqual(form["scope"], ["https://graph.microsoft.com/.default"])
        self.assertEqual(json.loads(form["claims"][0]), {"access_token": {"xms_cc": {"values": ["cp1"]}}})
        signed = form["client_assertion"][0]
        self.assertEqual(jwt.get_unverified_header(signed), {"alg": "PS256", "typ": "JWT", "x5t#S256": THUMBPRINT})
        client_assertion = jwt.decode(signed, self.signing_key.public_key(), algorithms=["PS256"], audience=TOKEN_URL,
                                      options={"verify_exp": False})
        self.assertEqual((client_assertion["iss"], client_assertion["sub"]), (CLIENT, CLIENT))
        self.assertEqual(client_assertion["exp"] - client_assertion["iat"], 120)
        self.assertTrue(client_assertion["jti"])

    def test_binding_rejections_do_not_sign_or_exchange(self):
        for args in ({"oid": CLIENT}, {"tid": CLIENT}, {"aud": OWNER}, {"iss": "https://issuer.example.com"},
                     {"scp": ""}, {"scp": "Gateway.Invoke.Other"}, {"scp": "Other.Scope"},
                     {"idtyp": "app"}, {"ver": "1.0"}):
            with self.subTest(args=args), self.assertRaises(GraphOboError) as error:
                self.obo.exchange(assertion(**args), CALLER)
            self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.calls, [])

    def test_key_mismatch_and_wrong_scope_stop_exchange(self):
        self.signing_result_changes = {"kid": KEY_URL.replace("/gateway-obo/", "/other-key/")}
        with self.assertRaises(GraphOboError) as error:
            self.obo.exchange(assertion(), CALLER)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(len(self.calls), 1)
        self.signing_result_changes = {}
        self.token_result["scope"] = "User.Read Mail.Read"
        with self.assertRaises(GraphOboError) as error:
            self.obo.exchange(assertion(), CALLER)
        self.assertEqual(error.exception.status, 503)

    def test_interactive_claims_are_bounded_and_other_oauth_details_stay_internal(self):
        self.token_status = 400
        valid = '{"access_token":{"acrs":{"essential":true,"values":["synthetic-step-up"]}}}'
        for raw in (valid, '{"access_token":{"bad":{"value":"\\r\\nInjected"}}}', '{"id_token":{"acrs":null}}'):
            with self.subTest(raw=raw), self.assertRaises(GraphOboError) as error:
                self.token_result = {"error": "interaction_required", "claims": raw, "error_description": "synthetic-private-detail"}
                self.obo.exchange(assertion(), CALLER)
            self.assertEqual(error.exception.status, 401)
            self.assertNotIn("synthetic-private-detail", str(error.exception))
            if raw == valid:
                self.assertEqual(json.loads(base64.b64decode(error.exception.claims)), json.loads(valid))
            else:
                self.assertEqual(error.exception.claims, "")
        self.token_result["error"] = "invalid_client"
        with self.assertRaises(GraphOboError) as error:
            self.obo.exchange(assertion(), CALLER)
        self.assertEqual((error.exception.status, error.exception.claims), (503, ""))


class TestGraphOboHttp(BrokerTestCase):
    def test_trusted_broker_route_rejects_other_callers_and_caches_no_tokens(self):
        exchanges = []
        self.service.graph_obo = SimpleNamespace(exchange=lambda token, caller: exchanges.append((token, caller)) or GRAPH_TOKEN)
        path = "/v1/graph/obo"
        authorized = {"X-Core-Broker-Key": KEY, "X-Core-Caller-Id": CALLER.caller_id}
        with TestClient(create_app(self.service)) as client:
            self.assertEqual(client.post(path, json={"assertion": "user-token"}).status_code, 401)
            self.assertEqual(client.post(path, json={"assertion": "user-token"}, headers={
                "X-Core-Broker-Key": KEY, "X-Core-Subscription-Id": "example"}).status_code, 403)
            self.assertEqual(client.post(path, json={"assertion": "user-token", "scope": "other"}, headers=authorized).status_code, 400)
            self.assertEqual(client.post(path + "?resource=other", json={"assertion": "user-token"}, headers=authorized).status_code, 400)
            self.assertEqual(exchanges, [])
            result = client.post(path, json={"assertion": "user-token"}, headers=authorized)
            self.assertEqual(result.json(), {"accessToken": GRAPH_TOKEN})
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertEqual(exchanges, [("user-token", CALLER)])
            self.service.graph_obo = None
            result = client.post(path, json={"assertion": "user-token"}, headers=authorized)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["error"], {"code": "GraphOboUnavailable"})
