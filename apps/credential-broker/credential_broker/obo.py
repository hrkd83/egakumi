"""検証済み社員Bearerを固定したMicrosoft Graphの委任トークンに交換する。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from threading import Lock
import time
from uuid import uuid4

import httpx
import jwt
from azure.identity import ManagedIdentityCredential

from .caller import UserCaller


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_GRANTED_SCOPE = "https://graph.microsoft.com/user.read"
GATEWAY_SCOPE = "Gateway.Invoke"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_ASSERTION = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
CLIENT_CAPABILITIES = '{"access_token":{"xms_cc":{"values":["cp1"]}}}'
KEY_VAULT_SCOPE = "https://vault.azure.net/.default"
CLIENT_ASSERTION_LIFETIME_SECONDS = 120
CLIENT_ASSERTION_CACHE_SECONDS = 60
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.~+/=-]{1,24000}")
CLAIM_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


class GraphOboError(RuntimeError):
    def __init__(self, status=503, claims=""):
        self.status, self.claims = status, claims
        super().__init__("Graph OBO request failed")


def base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def json_segment(value):
    return base64url(json.dumps(value, separators=(",", ":")).encode())


def unique_json_items(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def claims_challenge(value):
    """Entraの追加認証要求から、サイズと型を確認したclaimsだけを返す。"""
    if not isinstance(value, str) or not 1 <= len(value) <= 3072:
        return ""
    try:
        root = json.loads(value, object_pairs_hook=unique_json_items)
    except (ValueError, RecursionError):
        return ""
    if not isinstance(root, dict) or set(root) != {"access_token"}:
        return ""
    claims = root["access_token"]
    if not isinstance(claims, dict) or not 1 <= len(claims) <= 16:
        return ""

    def claim_value(value):
        return isinstance(value, str) and 1 <= len(value) <= 256 and value.isascii() and value.isprintable()

    for name, requirement in claims.items():
        if not CLAIM_NAME.fullmatch(name):
            return ""
        if requirement is None:
            continue
        if not isinstance(requirement, dict) or not requirement.keys() <= {"essential", "value", "values"}:
            return ""
        if "essential" in requirement and not isinstance(requirement["essential"], bool):
            return ""
        if "value" in requirement and ("values" in requirement or not claim_value(requirement["value"])):
            return ""
        if "values" in requirement:
            values = requirement["values"]
            if not isinstance(values, list) or not 1 <= len(values) <= 16 or not all(map(claim_value, values)):
                return ""
    encoded = json.dumps(root, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return base64.b64encode(encoded).decode("ascii") if len(encoded) <= 3072 else ""


class GraphOboClient:
    def __init__(self, settings, client, credential, *, clock=time.time):
        self.settings = settings
        self.client = client
        self.credential = credential
        self.clock = clock
        self.issuer = f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0"
        self.token_url = f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token"
        self._assertion_lock = Lock()
        self._cached_assertion = None

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.graph_obo, httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False),
                   ManagedIdentityCredential(client_id=settings.managed_identity_client_id, retry_total=0))

    def close(self):
        self.client.close()
        self.credential.close()

    def _assertion_matches_caller(self, assertion, caller):
        # APIMが署名検証した値をBrokerの信頼境界で照合する。Entraも交換時に元の署名を検証する。
        if (not isinstance(caller, UserCaller) or caller.tenant_id != self.settings.tenant_id
                or not isinstance(assertion, str) or len(assertion) > 24000
                or re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", assertion) is None):
            return False
        try:
            claims = jwt.decode(assertion, options={"verify_signature": False})
        except (ValueError, jwt.PyJWTError):
            return False
        scopes = claims.get("scp")
        return (claims.get("aud") == self.settings.client_id and claims.get("iss") == self.issuer
                and claims.get("ver") == "2.0" and claims.get("tid") == caller.tenant_id
                and claims.get("oid") == caller.principal_id and claims.get("idtyp") != "app"
                and isinstance(scopes, str) and GATEWAY_SCOPE in scopes.split(" "))

    def _post(self, url, **kwargs):
        with self.client.stream("POST", url, **kwargs) as response:
            if response.status_code not in (200, 400, 401, 403):
                raise GraphOboError()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 65536:
                    raise GraphOboError()
            payload = json.loads(body, object_pairs_hook=unique_json_items)
            if not isinstance(payload, dict):
                raise GraphOboError()
            return response.status_code, payload

    def _client_assertion(self):
        # Freeze the cache at this client's tenant, application and certificate version.
        with self._assertion_lock:
            start = int(self.clock())
            if self._cached_assertion is not None:
                issued_at, signed = self._cached_assertion
                if 0 <= start - issued_at < CLIENT_ASSERTION_CACHE_SECONDS:
                    return signed
            self._cached_assertion = None
            signed = self._sign_client_assertion(start)
            self._cached_assertion = start, signed
            return signed

    def _sign_client_assertion(self, start):
        header = {"alg": "PS256", "typ": "JWT", "x5t#S256": self.settings.certificate_thumbprint}
        claims = {"aud": self.token_url, "iss": self.settings.client_id, "sub": self.settings.client_id,
                  "jti": str(uuid4()), "iat": start, "nbf": start - 5,
                  "exp": start + CLIENT_ASSERTION_LIFETIME_SECONDS}
        unsigned = json_segment(header) + "." + json_segment(claims)
        digest = base64url(hashlib.sha256(unsigned.encode("ascii")).digest())
        token = self.credential.get_token(KEY_VAULT_SCOPE).token
        status, response = self._post(self.settings.key_url + "/sign", params={"api-version": "2025-07-01"},
                                      json={"alg": "PS256", "value": digest},
                                      headers={"Authorization": "Bearer " + token})
        signature = response.get("value")
        if (status != 200 or response.get("kid") != self.settings.key_url or not isinstance(signature, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{342,683}", signature) is None):
            raise GraphOboError()
        return unsigned + "." + signature

    def exchange(self, assertion, caller):
        if not self._assertion_matches_caller(assertion, caller):
            raise GraphOboError(403)
        try:
            status, response = self._post(self.token_url, data={
                "grant_type": JWT_BEARER, "requested_token_use": "on_behalf_of",
                "client_id": self.settings.client_id, "client_assertion_type": CLIENT_ASSERTION,
                "client_assertion": self._client_assertion(), "assertion": assertion, "scope": GRAPH_SCOPE,
                "claims": CLIENT_CAPABILITIES,
            })
            if status == 400 and response.get("error") in (
                "interaction_required", "login_required", "consent_required", "invalid_grant"
            ):
                raise GraphOboError(401, claims_challenge(response.get("claims")))
            if status in (400, 403) and response.get("error") == "access_denied":
                raise GraphOboError(403)
            access_token = response.get("access_token")
            scope = response.get("scope")
            expires = response.get("expires_in")
            if (status != 200 or response.get("token_type") != "Bearer" or not isinstance(access_token, str)
                    or not TOKEN_PATTERN.fullmatch(access_token) or not isinstance(scope, str)
                    or scope.lower() not in ("user.read", GRAPH_GRANTED_SCOPE) or type(expires) is not int or expires <= 0):
                raise GraphOboError()
            return access_token
        except GraphOboError:
            raise
        except Exception:
            # HTTP／MI／Entra例外には認証情報や元のエラー本文が含まれる可能性がある。
            raise GraphOboError() from None
