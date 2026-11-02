"""固定 APIM/provider だけへ ARM 要求を送り、CM token の本文を扱わない。"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit


class CmError(RuntimeError):
    pass


def connection_metadata_status(response):
    """HTTP入口と定期照合で同じCM分類を使い、不明な障害で本人接続を失効させない。"""
    if response is None:
        return "requires_reauthentication"
    properties = response.get("properties") if isinstance(response, dict) else None
    if not isinstance(properties, dict):
        return "unavailable"
    status = properties.get("status")
    if not isinstance(status, str):
        return "unavailable"
    if status == "Connected":
        return "connected"
    if status in {"Disconnected", "NotConnected", "Unauthenticated"}:
        return "requires_reauthentication"
    known = {"invalid_grant", "interaction_required", "login_required", "consent_required", "token_revoked"}
    error = properties.get("error", {})
    for _ in range(4):
        if not isinstance(error, dict):
            break
        if str(error.get("code", "")).lower() in known:
            return "requires_reauthentication"
        error = error.get("innererror", error.get("innerError", {}))
    return "unavailable"


def allowed_login_url(value):
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and not url.username and not url.password and not url.fragment
            and url.port in (None, 443) and (url.hostname == "authorization-manager.consent.azure-apim.net"
                or re.fullmatch(r"logic-apis-[a-z0-9-]+\.consent\.azure-apim\.net", url.hostname or "") is not None))
    except (ValueError, TypeError):
        return False


class CmClient:
    def __init__(self, settings, *, credential=None, client=None):
        if credential is None:
            from azure.identity import ManagedIdentityCredential
            credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        if client is None:
            import httpx
            client = httpx.Client(timeout=25, follow_redirects=False, trust_env=False,
                headers={"Accept": "application/json", "User-Agent": "egakumi-credential-broker/1.0"})
        self.settings, self.credential, self.client = settings, credential, client

    def _path(self, provider, connection):
        configured = {name for profile in self.settings.profiles.values() for name, _ in profile.pools}
        if provider not in configured or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", connection):
            raise CmError("Connection is not configured")
        return self.settings.apim_resource_id + "/authorizationProviders/" + quote(provider, safe="") + "/authorizations/" + quote(connection, safe="")

    def _request(self, method, path, body=None, *, allow_missing=False):
        try:
            token = self.credential.get_token("https://management.azure.com/.default").token
            response = self.client.request(method, "https://management.azure.com" + path + "?api-version=2024-05-01",
                json=body, headers={"Authorization": "Bearer " + token, "If-Match": "*"} if method == "PUT"
                else {"Authorization": "Bearer " + token})
            if allow_missing and response.status_code == 404:
                return None
            if method == "DELETE" and response.status_code in (200, 202, 204):
                return {"deletionAccepted": True}
            if response.status_code not in (200, 201):
                raise CmError("Credential Manager operation failed")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise CmError("Credential Manager operation failed") from None

    def ensure_connection(self, provider, connection):
        path = self._path(provider, connection)
        if self._request("GET", path, allow_missing=True) is None:
            self._request("PUT", path, {"properties": {"authorizationType": "OAuth2", "oauth2grantType": "AuthorizationCode"}})
        # APIM 自身が CM token を利用できるよう、既知の MI だけへ利用権限を設定します。
        self._request("PUT", path + "/accessPolicies/" + self.settings.apim_principal_id,
            {"properties": {"tenantId": self.settings.azure_tenant_id, "objectId": self.settings.apim_principal_id}})

    def is_connected(self, provider, connection):
        state = self.connection_status(provider, connection)
        if state == "unavailable":
            raise CmError("Credential Manager state is unavailable")
        return state == "connected"

    def connection_status(self, provider, connection):
        """token取得不能だけで失効と決めず、CMが明示した認証不成立だけを区別する。"""
        response = self._request("GET", self._path(provider, connection), allow_missing=True)
        return connection_metadata_status(response)

    def delete_connection(self, provider, connection):
        """削除応答だけで容量を解放せず、GETの404まで確認する。"""
        path = self._path(provider, connection)
        if self._request("GET", path, allow_missing=True) is None:
            return True
        self._request("DELETE", path, allow_missing=True)
        return self._request("GET", path, allow_missing=True) is None

    def login_link(self, provider, connection, redirect_uri):
        parsed = urlsplit(redirect_uri)
        if (parsed.scheme + "://" + parsed.netloc != self.settings.public_origin
                or parsed.path != "/auth/connections/browser/callback" or parsed.fragment):
            raise CmError("Invalid callback endpoint")
        result = self._request("POST", self._path(provider, connection) + "/getLoginLinks",
            {"postLoginRedirectUrl": redirect_uri})
        link = result.get("loginLink")
        if not allowed_login_url(link):
            raise CmError("Credential Manager returned an invalid endpoint")
        return link
