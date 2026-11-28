"""設定・API試験で共有する、秘密値を含まない独立した入力例。"""
import base64
import copy
import json
from uuid import UUID
from tests import REPOSITORY

CORE = REPOSITORY
from deployment.helpers import configuration
from deployment.helpers.capacity_profiles import compose_capacity_options


def bind_credential_fixture_id(parameters):
    """公開例のCredentialアプリ欄へ合成IDを入れる。"""
    auth = parameters.get("gateway", {}).get("authentication", {})
    credential = auth.get("credentialLogin", {}).get("identity", {})
    if credential.get("clientId", "").startswith("<ENTRA_"):
        credential["clientId"] = str(UUID(int=10, version=4))


def profile_options(profile="dev", repository=CORE):
    """サービス別の正本を本番の合成処理で読んだ標準設定を返す。"""
    source = repository / "infra/config" / f"{profile}.json"
    params = repository / "params" / f"{profile}.example.json"
    requested_capacity = json.loads(params.read_text(encoding="utf-8"))["capacity"]
    return compose_capacity_options(source, json.loads(source.read_text(encoding="utf-8")),
                                    requested_capacity=requested_capacity)


def public_parameters(profile="dev"):
    """公開paramsの構造を保ち、置き換え必須の欄だけへ合成値を入れる。"""
    parameters = json.loads((CORE / "params" / f"{profile}.example.json").read_text(encoding="utf-8"))
    parameters["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
    parameters["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
    parameters["entraTenantId"] = parameters["azureTenantId"]
    parameters["services"]["apimInstanceId"] = "abc123"
    parameters["gateway"]["authentication"]["entra"]["audience"] = "33333333-3333-4333-8333-333333333333"
    parameters["credentialProviders"]["graphDelegated"]["clientId"] = "44444444-4444-4444-8444-444444444444"
    bind_credential_fixture_id(parameters)
    parameters["certificate"]["azureDnsSubscriptionId"] = parameters["subscriptionId"]
    parameters["certificate"]["azureDnsManagedIdentityResourceId"] = (
        "/subscriptions/" + parameters["subscriptionId"] +
        "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
    return parameters


def public_fixture(profile="dev"):
    """合成済みのconfigを使う試験向けに、対応する企業入力を組み立てる。"""
    parameters = public_parameters(profile)
    parameters["configFile"] = "options.json"
    parameters.pop("capacity")
    return parameters, profile_options(profile)


def fixture():
    """設定試験で使う最小の独立した設定を作る。呼出元が変更しても公開サンプルは書き換えない。"""
    value = configuration.load_configuration_source(CORE / "params/dev.example.json")
    # 汎用テストではプロファイルとは別の、固定された合成アドレスと名前を使います。
    reference = json.loads((CORE / "params/prod.example.json").read_text(encoding="utf-8"))
    value.pop("capacityProfile")
    value["environmentName"] = "test"
    value["network"].update(reference["network"])
    for key in ("snatPrivateRanges", "idpsPrivateRanges"):
        value["network"]["firewall"].pop(key, None)
    value["gateway"]["publicHostName"] = reference["gateway"]["publicHostName"]
    value["portal"]["publicHostName"] = reference["portal"]["publicHostName"]
    # 同じ合成入力で撤去・purgeの分岐も独立して試験できるよう、保護設定を明示します。
    value["monitoring"].update(auditStorageSku="Standard_LRS", auditImmutabilityMode="Disabled",
                               auditRetentionDays=2557)
    value["services"].update(vaultRetentionDays=7, vaultPurgeProtection=False)
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
    value["services"]["apimInstanceId"] = "abc123"
    value["services"]["modelDeployments"] = []
    # Generic unit cases opt out of application publication independently of shipped defaults.
    value["publishApis"] = False
    # 基礎fixtureはCMを使わず、接続試験ではconnection_fixtureで追加する。
    value["credentialProviders"] = []
    value["gateway"]["authentication"].pop("credentialLogin", None)
    # 汎用試験はEntra追加前の入力互換も扱う。Entra試験で別途追加する。
    value["gateway"]["authentication"].pop("entra", None)
    value["portal"].pop("authentication", None)
    value["certificate"].pop("domainNames", None)
    return value


def ssh_fixture_key():
    """実運用の秘密鍵を使わず、入力形式の検証専用に合成OpenSSH公開鍵を作る。"""
    def field(value):
        return len(value).to_bytes(4, "big") + value
    blob = field(b"ssh-ed25519") + field(b"x" * 32)
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii") + " fixture-only"


def api_fixture():
    """標準WAFの公開ルートと独立した、認証・操作・モデル制約付きカスタムAPIを作る。"""
    value = fixture()
    value["services"]["modelDeployments"] = [{
        "name": "gpt-approved", "modelName": "gpt-approved", "modelFormat": "OpenAI",
        "modelVersion": "2026-01-01", "skuName": "GlobalStandard", "capacity": 1,
        "versionUpgradeOption": "NoAutoUpgrade",
    }]
    value["apiDefinitions"] = [{
        "name": "models", "displayName": "Approved Models", "path": "models/custom/v1",
        "backendService": "foundryOpenAi", "backendAudience": "https://cognitiveservices.azure.com",
        "productId": "model-users", "subscriptionKeyHeader": "api-key",
        "operations": [{"name": "responses-create", "displayName": "Create stateless response", "method": "POST", "urlTemplate": "/responses", "templateParameters": []}],
        "authorization": {
            "mode": "subscriptionKey",
            "operationConstraints": {"responses-create": {"kind": "statelessResponses", "allowedModels": ["gpt-approved"], "maxRequestBytes": 32768, "maxOutputTokens": 128, "outputTokenParameter": "max_output_tokens"}},
        },
    }]
    value["publishApis"] = True
    return value


def profiled_api_fixture():
    """Responses操作に基本設定とモデル別差分を持つ合成契約を作る。"""
    raw = api_fixture()
    api = raw["apiDefinitions"][0]
    api.update(name="openai-inference-v1", path="models/openai/v1", serviceClass="platform")
    authorization = api["authorization"]
    constraint = authorization["operationConstraints"]["responses-create"]
    model = copy.deepcopy(raw["services"]["modelDeployments"][0])
    model.update(name="gpt-secondary", modelName="gpt-secondary")
    raw["services"]["modelDeployments"].append(model)
    constraint.update(allowedModels=["gpt-approved", "gpt-secondary"], modelSettings={
        "gpt-approved": {"settingId": "response"},
        "gpt-secondary": {"settingId": "secondary", "maxRequestBytes": 2048000, "maxOutputTokens": 512},
    })
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
        "subscriptionKeyHeader": "api-key",
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
        "subscriptionKeyHeader": "api-key",
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
    return raw
