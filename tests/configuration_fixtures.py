"""設定・API試験で共有する、秘密値を含まない独立した入力例。"""
import base64
import copy
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers import configuration


def fixture():
    """設定試験で使う最小の独立した設定を作る。呼出元が変更しても公開サンプルは書き換えない。"""
    value = configuration.load_configuration_source(CORE / "params/test.example.json")
    # The published sample deliberately requires the adopter's target IDs.
    value["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
    value["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
    value["entraTenantId"] = value["azureTenantId"]
    value["certificate"]["azureDnsSubscriptionId"] = value["subscriptionId"]
    value["certificate"]["azureDnsManagedIdentityResourceId"] = (
        "/subscriptions/" + value["subscriptionId"] +
        "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
    # Most unit cases exercise unrelated validation using no model deployment.
    # PublicSampleTests separately verifies the complete published catalogue.
    value["services"]["modelDeployments"] = []
    # Generic unit cases opt out of application publication independently of shipped defaults.
    value["publishApis"] = False
    # 基礎fixtureはCMを使わず、接続試験ではconnection_fixtureで追加する。
    value["credentialProviders"] = []
    value["gateway"]["authentication"].pop("credentialLogin", None)
    value["certificate"].pop("domainNames", None)
    return value


def ssh_fixture_key():
    """実運用の秘密鍵を使わず、入力形式の検証専用に合成OpenSSH公開鍵を作る。"""
    def field(value):
        return len(value).to_bytes(4, "big") + value
    blob = field(b"ssh-ed25519") + field(b"x" * 32)
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii") + " fixture-only"


def api_fixture():
    """認証・操作・モデル制約を含むAPI定義の正常例を作る。"""
    value = fixture()
    value["services"]["modelDeployments"] = [{
        "name": "gpt-approved", "modelName": "gpt-approved", "modelFormat": "OpenAI",
        "modelVersion": "2026-01-01", "skuName": "GlobalStandard", "capacity": 1,
        "versionUpgradeOption": "NoAutoUpgrade",
    }]
    value["apiDefinitions"] = [{
        "name": "models", "displayName": "Approved Models", "path": "models/openai/v1",
        "backendService": "foundryOpenAi", "backendAudience": "https://cognitiveservices.azure.com",
        "productId": "model-users", "subscriptionKeyHeader": "api-key",
        "operations": [{"name": "responses-create", "displayName": "Create stateless response", "method": "POST", "urlTemplate": "/responses", "templateParameters": []}],
        "keySubscriptions": [{"subscriptionId": "system-a", "displayName": "System A", "enabled": True}],
        "authorization": {
            "mode": "subscriptionKey",
            "operationConstraints": {"responses-create": {"kind": "statelessResponses", "allowedModels": ["gpt-approved"], "maxRequestBytes": 32768, "maxOutputTokens": 128, "outputTokenParameter": "max_output_tokens"}},
        },
    }]
    value["publishApis"] = True
    return value


def profiled_api_fixture():
    """Responses操作のtext/PDF契約を作る。"""
    raw = api_fixture()
    api = raw["apiDefinitions"][0]
    api.update(name="openai-inference-v1", serviceClass="platform")
    authorization = api["authorization"]
    constraint = authorization["operationConstraints"]["responses-create"]
    text = {**copy.deepcopy(constraint), "profileId": "response", "modalities": ["text"]}
    pdf = {**copy.deepcopy(constraint), "profileId": "pdf", "modalities": ["text", "file"],
           "maxRequestBytes": 2048000, "maxOutputTokens": 512}
    constraint.update(defaultProfile="response", profiles=[text, pdf])
    return raw


def connection_fixture():
    """本人OAuth接続のproviderと所有者に対応する正常なAPI設定を作る。"""
    raw = fixture()
    raw["credentialProviders"] = [{
        "name": "approved-oauth", "displayName": "Approved personal connection", "identityProvider": "aad",
        "clientId": "44444444-4444-4444-8444-444444444444", "clientSecretFile": "../secrets/downstream-client.txt",
        "oauthTenantId": raw["azureTenantId"], "resourceUri": "https://graph.microsoft.com", "scopes": "User.Read",
        "connectionNames": [], "identityEndpoint": "https://graph.microsoft.com/v1.0/me", "identityIdProperty": "id",
    }]
    raw["gateway"]["authentication"]["credentialLogin"] = {"providerNames": ["approved-oauth"]}
    raw["apiDefinitions"] = [{
        "name": "profile", "displayName": "Registered owner profile", "path": "tools/profile/v1",
        "backendService": "external", "backendUrl": "https://graph.microsoft.com/v1.0", "backendAudience": "https://graph.microsoft.com",
        "backendAuthentication": {"mode": "none"}, "productId": "profile-users",
        "subscriptionKeyHeader": "api-key", "keySubscriptions": [],
        "operations": [{"name": "read-profile", "displayName": "Read profile", "method": "GET", "urlTemplate": "/me", "templateParameters": []}],
        "authorization": {"mode": "subscriptionKey",
                          "operationConstraints": {"read-profile": {"kind": "registeredOperation", "maxRequestBytes": 32768}}},
        "credentialConnection": {"providerName": "approved-oauth", "mode": "personal", "enabled": True},
    }]
    raw["publishApis"] = True
    return raw


def pii_fixture():
    """PIIの公開操作・入力制約・本人認証を持つ正常設定を作る。"""
    raw = fixture()
    raw["apiDefinitions"] = [{
        "name": "pii-v1", "displayName": "PII redaction", "path": "tools/pii/v1",
        "backendService": "foundryCognitive", "backendAudience": "https://cognitiveservices.azure.com",
        "productId": "pii-users",
        "subscriptionKeyHeader": "api-key", "keySubscriptions": [],
        "operations": [{"name": "pii_redact_text", "displayName": "Redact text", "method": "POST", "urlTemplate": "/redact", "templateParameters": []}],
        "authorization": {"mode": "subscriptionKey",
                          "operationConstraints": {"pii_redact_text": {"kind": "pii", "maxRequestBytes": 32768, "modelVersion": "2025-11-15", "allowedLanguages": ["ja", "en"], "allowedWarningCodes": []}}},
        "mcp": {"name": "pii-v1-mcp", "displayName": "PII MCP", "path": "tools/pii-v1-mcp", "operationNames": ["pii_redact_text"]},
    }]
    raw["publishApis"] = True
    return raw


def pii_key_fixture():
    """PIIを登録済みサービスの購読キーで呼ぶための正常設定を作る。"""
    raw = pii_fixture()
    api = raw["apiDefinitions"][0]
    api["authorization"]["mode"] = "subscriptionKey"
    api["keySubscriptions"] = [{"subscriptionId": "pii-system", "displayName": "PII service", "enabled": True}]
    return raw
