"""MSAL の state/nonce/PKCE と署名検証を通った本人識別子だけを返す。"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import secrets
from urllib.parse import parse_qs, urlsplit

from .settings import guid


class IdentityError(RuntimeError):
    """OAuth の応答・token を例外文へ含めない。"""


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    object_id: str
    upn: str


class JwtVerifier:
    def __init__(self):
        self._keys = {}

    def verify(self, token, profile, nonce):
        import jwt
        endpoint = f"https://login.microsoftonline.com/{profile.tenant_id}/discovery/v2.0/keys"
        client = self._keys.setdefault(endpoint, jwt.PyJWKClient(endpoint, cache_keys=True))
        key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=profile.client_id,
            issuer=f"https://login.microsoftonline.com/{profile.tenant_id}/v2.0", leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "oid", "tid", "nonce"]})
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise IdentityError("Identity verification failed")
        return claims


class OidcClient:
    def __init__(self, origin, *, application_factory=None, verifier=None):
        self.redirect_uri = origin + "/auth/connections/browser/identity-callback"
        self.application_factory = application_factory
        self.verifier = verifier or JwtVerifier()

    def _application(self, profile):
        # token cache は処理ごとのメモリー内だけで使用し、SQL やファイルへ保存しません。
        if self.application_factory:
            return self.application_factory(profile)
        import msal
        return msal.ConfidentialClientApplication(profile.client_id, client_credential=profile.client_secret,
            authority="https://login.microsoftonline.com/" + profile.tenant_id,
            token_cache=msal.TokenCache())

    def begin(self, profile):
        try:
            state = secrets.token_hex(32)
            # OIDC の既定 scope は MSAL が付与します。業務API用 token は CM が管理します。
            flow = self._application(profile).initiate_auth_code_flow([], redirect_uri=self.redirect_uri,
                state=state, response_mode="query")
            parsed = urlsplit(flow["auth_uri"])
            query = parse_qs(parsed.query)
            nonce = query.get("nonce", [""])[0]
            if (parsed.scheme != "https" or parsed.netloc != "login.microsoftonline.com"
                    or parsed.path != f"/{profile.tenant_id}/oauth2/v2.0/authorize"
                    or flow.get("state") != state or query.get("state") != [state]
                    or query.get("code_challenge_method") != ["S256"] or not query.get("code_challenge") or not nonce):
                raise ValueError()
            return {"auth_uri": flow["auth_uri"], "state": state, "expectedNonce": nonce, "msalFlow": flow}
        except Exception:
            raise IdentityError("Identity authorization could not be started") from None

    def complete(self, profile, envelope, response):
        try:
            if (not isinstance(response, dict) or not isinstance(response.get("code"), str)
                    or not response["code"] or "error" in response or not isinstance(response.get("state"), str)
                    or not hmac.compare_digest(response["state"], envelope["state"])):
                raise ValueError()
            # 保存した MSAL flow をそのまま戻し、state・nonce・PKCE の検証を委ねます。
            result = self._application(profile).acquire_token_by_auth_code_flow(envelope["msalFlow"], response)
            if "error" in result or not isinstance(result.get("id_token"), str):
                raise ValueError()
            claims = self.verifier.verify(result["id_token"], profile, envelope["expectedNonce"])
            tenant, owner = guid(claims.get("tid")), guid(claims.get("oid"))
            if tenant != profile.tenant_id:
                raise ValueError()
            upn = claims.get("preferred_username") or claims.get("upn") or owner
            if not isinstance(upn, str) or not upn or len(upn) > 320 or any(ord(c) < 32 for c in upn):
                raise ValueError()
            return Identity(tenant, owner, upn)
        except Exception:
            raise IdentityError("Identity verification failed") from None
