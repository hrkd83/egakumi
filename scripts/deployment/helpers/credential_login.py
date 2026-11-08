"""本人接続brokerの公開入口と、CM tokenの本人照合を組み立てる。"""
from __future__ import annotations

import base64
import json
from xml.sax.saxutils import escape

from .paths import REPOSITORY
from .apim_naming import apim_name
from .credential_pools import physical_providers, logical_profiles

API_ID = "credential-manager-login"
API_PATH = "auth/connections"
OPERATIONS = {"start": "POST", "status": "GET", "logout": "POST", "session-commit": "POST", "logout-all": "POST", "disconnect": "POST"}
OPERATION_PATHS = {"start": "start", "status": "status", "logout": "logout", "session-commit": "session/commit", "logout-all": "logout-all", "disconnect": "disconnect"}
BROWSER_API_ID = "credential-manager-browser"
BROWSER_API_PATH = "auth/connections/browser"
BROWSER_OPERATIONS = {"launch": "GET", "launch-post": "POST", "identity-callback": "GET", "callback": "GET"}
BROWSER_OPERATION_PATHS = {"launch": "launch", "launch-post": "launch", "identity-callback": "identity-callback", "callback": "callback"}
# 各値の上限は誤入力を防ぐための構築入力の境界です。稼働時の期限判定はSQLが行います。
SESSION_POLICY_DEFAULTS = {
    "idleTimeoutSeconds": 604800, "absoluteLifetimeSeconds": 2592000,
    "rotationIntervalSeconds": 86400, "rotationGraceSeconds": 300,
    "rotationHandoffSeconds": 600, "rotationCandidateTtlSeconds": 600,
}
SESSION_POLICY_BOUNDS = {
    "idleTimeoutSeconds": (300, 31536000), "absoluteLifetimeSeconds": (300, 31536000),
    "rotationIntervalSeconds": (300, 2592000), "rotationGraceSeconds": (1, 3600),
    "rotationHandoffSeconds": (60, 3600), "rotationCandidateTtlSeconds": (60, 3600),
}
MAX_TRANSACTION_LIFETIME_SECONDS = 600
POLICY_DIRECTORY = REPOSITORY / "apps/gateway/apim/policies/credential-login"


def settings(configuration: dict) -> dict | None:
    """公開済みの設定だけを取り出す。client secret や CM token は含めない。"""
    login = configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin")
    if login is None:
        return None
    names = set(login["providerNames"])
    providers = [{key: provider[key] for key in ("name", "sourceName", "oauthTenantId", "identityEndpoint", "identityIdProperty")}
                 for provider in physical_providers(configuration) if provider["sourceName"] in names]
    profiles = [{"name": name, "tenantId": profile["tenantId"]}
                for name, profile in logical_profiles(configuration).items()]
    group = configuration["resourceGroupNames"]["svc"]
    return {**login, "publicOrigin": "https://" + configuration["gateway"]["publicHostName"],
            "azureTenantId": configuration["azureTenantId"],
            "keyHeader": configuration["gateway"]["authentication"]["subscriptionKeyHeader"],
            "apimResourceId": f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{group}/providers/Microsoft.ApiManagement/service/{apim_name(configuration)}",
            "profiles": profiles, "providers": providers}


def encode(value) -> str:
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def render(filename: str, login: dict) -> str:
    """検証済みの固定入力を XML へ差し込む。要求から接続先を組み立てない。"""
    replacements = {"__CREDENTIAL_LOGIN_SETTINGS__": encode(login),
                    "__CREDENTIAL_LOGIN_ORIGIN_BASE64__": base64.b64encode(login["publicOrigin"].encode()).decode(),
                    "__CREDENTIAL_IDENTITY_TENANTS__": encode(sorted({p["oauthTenantId"] for p in login["providers"]})),
                    "__CREDENTIAL_LOGIN_ORIGIN__": escape(login["publicOrigin"], {'"': '&quot;'}),
                    "__CREDENTIAL_TRANSACTION_TTL__": str(login["transactionLifetimeSeconds"]),
                    "__CREDENTIAL_LOGIN_CALLS__": str(login["loginCallsPerMinute"]),
                    "__CREDENTIAL_KEY_HEADER__": login["keyHeader"]}
    value = (POLICY_DIRECTORY / filename).read_text(encoding="utf-8")
    for key, replacement in replacements.items():
        value = value.replace(key, replacement)
    return value


def render_connection(filename: str, login: dict, telemetry: str = "") -> str:
    """業務APIの本人接続と認証案内を、同じ設定・監査処理で展開する。"""
    directory = POLICY_DIRECTORY.parent
    challenge = (directory / "credential-challenge.xml").read_text(encoding="utf-8").replace("<fragment>", "").replace("</fragment>", "")
    value = (directory / filename).read_text(encoding="utf-8").replace("__CREDENTIAL_CHALLENGE_XML__", challenge)
    return value.replace("__CREDENTIAL_LOGIN_SETTINGS__", encode(login)).replace("__CREDENTIAL_LOGIN_ORIGIN_BASE64__", base64.b64encode(login["publicOrigin"].encode()).decode()).replace("__GATEWAY_TELEMETRY_XML__", telemetry)


def anonymous_policy(login=None) -> str:
    """ブラウザーの一時 URL と CM 戻り先だけを、厳密な経路一致で匿名公開する。"""
    if not login:
        return ""
    condition = (f'context.Api != null && context.Operation != null && context.Api.Id == "{BROWSER_API_ID}" '
                 '&& ((context.Operation.Id == "launch" && context.Request.Method == "GET" '
                 f'&& context.Request.OriginalUrl.Path == "/{BROWSER_API_PATH}/launch") '
                 '|| (context.Operation.Id == "launch-post" && context.Request.Method == "POST" '
                 f'&& context.Request.OriginalUrl.Path == "/{BROWSER_API_PATH}/launch") '
                 '|| (context.Operation.Id == "callback" && context.Request.Method == "GET" '
                 f'&& context.Request.OriginalUrl.Path == "/{BROWSER_API_PATH}/callback") '
                 '|| (context.Operation.Id == "identity-callback" && context.Request.Method == "GET" '
                 f'&& context.Request.OriginalUrl.Path == "/{BROWSER_API_PATH}/identity-callback"))')
    return ('<!-- 一時取引を提示するブラウザー操作だけを匿名公開し、各操作が秘密・Cookie・期限を確認します。 -->'
            '<when condition="@(' + escape(condition, {'"': '&quot;'}) + ')">'
            '<set-variable name="gatewayCommonAuthenticationAnonymous" value="@(true)" /></when>')
