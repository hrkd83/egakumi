"""モデル・音声・ファイル・gRPCのAPI設定とAPIM式を検証する。

公開する操作と上流の形式を組み合わせ、未許可モデル・不正なサイズ・別セッションを拒否する。実XMLから取り出した式も動かすが、Azureへの配備成功は別の実機試験で確認する。
"""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE, api_fixture, configuration, fixture
from test_gateway_limits_telemetry import PWSH, expression
import prepare_coverage
import standard_apis
from api_definition_helpers import catalog_overlay, configured_base

POLICIES = CORE / "apps/gateway/apim/policies"
SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"


def _test_bindings(value, raw):
    """合成試験の公開ID/固定URLだけを、入力に実在するplaceholderへ渡す。"""
    known = {"TENANT_ID": raw.get("entraTenantId", raw["azureTenantId"]),
        "CM_CLIENT_ID": "66666666-6666-4666-8666-666666666666", "CM_CLIENT_SECRET_FILE": "../secrets/cm-client.txt",
        "PUBLIC_HOST_NAME": raw["gateway"]["publicHostName"], "SPEECH_RESOURCE_ID": "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture-svc/providers/Microsoft.CognitiveServices/accounts/speech-fixture",
        "HOSTED_AGENT_BASE_URL": "https://fixture.services.ai.azure.com/api/projects/core/agents/approved-agent/endpoint/protocols/openai",
        "VOICE_LIVE_WS_BASE_URL": "wss://provider.example.com/voice-live/realtime",
        "VOICE_LIVE_CALLS_WS_BASE_URL": "wss://provider.example.com/voice-live/realtime/calls",
        "VOICE_LIVE_MODEL": "gpt-6.1-sol", "VOICE_LIVE_CALLS_MODEL": "gpt-6.1-sol",
        "LIVE_WS_BASE_URL": "wss://provider.example.com/openai/v1/live/sessions",
        "TRANSCRIPTION_WS_BASE_URL": "wss://provider.example.com/openai/v1/realtime"}
    bindings = {}
    for name in prepare_coverage.required_bindings(value):
        if name in known:
            bindings[name] = known[name]
        elif name.endswith("WS_BASE_URL"):
            bindings[name] = "wss://provider.example.com/realtime"
        elif name.endswith(("BASE_URL", "BACKEND")):
            bindings[name] = "https://provider.example.com"
        elif name.endswith("MODEL"):
            bindings[name] = "approved-" + name.lower().replace("_model", "")
        elif name.endswith("SKU"):
            bindings[name] = "GlobalStandard"
        else:
            bindings[name] = "2026-01-01"
    return bindings


def bound_coverage():
    """正式18 APIを実際の準備処理で合成する。"""
    raw = configured_base()
    value = catalog_overlay()
    return prepare_coverage.prepare(raw, value, _test_bindings(value, raw), base_directory=CORE / "config")


def bound_external_coverage():
    """汎用protocol能力の試験だけに、外部6 APIのfixtureを明示追加する。"""
    from urllib.parse import urlsplit
    raw = bound_coverage()
    fixture_data = json.loads((CORE / "tests/fixtures/external-api-definitions.json").read_text())
    external = prepare_coverage.substitute(fixture_data, _test_bindings(fixture_data, raw))
    raw["apiDefinitions"].extend(external["apiDefinitions"])
    for api in external["apiDefinitions"]:
        hostname = urlsplit(api.get("backendUrl", "")).hostname
        destinations = raw["network"]["firewall"]["apimAllowedFqdns"]
        if hostname and hostname not in destinations:
            destinations.append(hostname)
    return raw


class ProtocolConfigurationTests(unittest.TestCase):
    """モデル・音声・ファイル・gRPCのAPI設定とAPIM式を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_bound_example_has_only_formal_apis_and_stays_unpublished(self):
        actual = self.normalize(bound_coverage())
        self.assertEqual({a["productId"] for a in actual["apiDefinitions"]}, {"model-users", "pii-users", "connection-users"})
        self.assertFalse(actual["publishApis"])
        self.assertTrue(actual["network"]["apimKeyVaultAccessEnabled"])
        self.assertEqual({a["apiType"] for a in actual["apiDefinitions"]}, {"http", "websocket"})

    def test_external_protocol_fixtures_are_explicitly_separate_from_standard(self):
        actual = self.normalize(bound_external_coverage())
        self.assertEqual(len(actual["apiDefinitions"]), len(standard_apis.STANDARD_API_NAMES) + 6)
        self.assertEqual(sum(api["serviceClass"] == "sample" for api in actual["apiDefinitions"]), 6)
        self.assertEqual({api["apiType"] for api in actual["apiDefinitions"]}, {"http", "websocket", "mcp", "grpc"})

    def test_deployed_models_are_reachable_through_declared_api_contracts(self):
        """API例が公開するモデルと、接続契約を追加確認するモデルを明示照合する。"""
        raw = bound_coverage()
        apis = {api["name"]: api for api in self.normalize(raw)["apiDefinitions"]}
        deployed = {model["name"] for model in raw["services"]["modelDeployments"]}
        published = set()
        for api in apis.values():
            for constraint in api["authorization"]["operationConstraints"].values():
                # OCR/layout are built-in service models, not Foundry model deployments.
                if constraint["kind"] != "documentAnalysis":
                    published.update(constraint.get("allowedModels", []))
                for profile in constraint.get("profiles", []):
                    published.update(profile["allowedModels"])
                if "model" in constraint:
                    published.add(constraint["model"])
            model = api.get("websocket", {}).get("queryParameters", {}).get("model")
            if model:
                published.add(model)
            published.update(api.get("websocket", {}).get("allowedModels", []))
            published.update(api.get("websocket", {}).get("session", {}).get("models", []))
        self.assertEqual(deployed - published, set())
        self.assertTrue((published - deployed).issubset({
            api["websocket"]["queryParameters"]["model"] for api in apis.values()
            if api.get("websocket", {}).get("queryParameters", {}).get("model")
        }))

    def test_flux_routes_and_audio_forms_remain_explicit_model_bound_azure_contracts(self):
        """BFL直販の鍵・polling URL、SpeechのSSML/WAVとOpenAI Audioを混在させません。"""
        apis = {api["name"]: api for api in self.normalize(bound_coverage())["apiDefinitions"]}
        api = apis["blackforest-images-v1"]
        self.assertEqual(api["backendService"], "foundryServices")
        self.assertEqual(api["backendAudience"], "https://cognitiveservices.azure.com")
        self.assertEqual(api["backendAuthentication"]["mode"], "managedIdentity")
        self.assertEqual({op["name"] for op in api["operations"]}, {"flux2-generate", "flux2-edit"})
        for op in api["operations"]:
            self.assertTrue(op["urlTemplate"].startswith("/flux-2/"))
            self.assertNotIn("backendUrlTemplate", op)
            constraint = api["authorization"]["operationConstraints"][op["name"]]
            self.assertEqual(constraint["kind"], "bodyModel")
            self.assertEqual(constraint["allowedModels"], ["FLUX.2-flex", "FLUX.2-pro"])
            self.assertEqual(constraint["defaultProfile"], "flex")
            for profile in constraint["profiles"]:
                variant = profile["profileId"]
                self.assertEqual(profile["allowedModels"], [f"FLUX.2-{variant}"])
                self.assertEqual(profile["backendUrlTemplate"], f"/providers/blackforestlabs/v1/flux-2-{variant}?api-version=preview")
            self.assertEqual(constraint["integerLimits"]["num_images"]["maximum"], 1)
        audio = apis["openai-audio-v1"]
        stt = audio["authorization"]["operationConstraints"]["audio-transcriptions"]
        self.assertEqual(stt["kind"], "pathModel")
        self.assertEqual(stt["bodyFormat"], "multipart")
        self.assertEqual(stt["allowedModels"], ["gpt-transcribe"])
        self.assertNotIn("audio-speech", audio["authorization"]["operationConstraints"])
        operation = next(op for op in audio["operations"] if op["name"] == "audio-transcriptions")
        self.assertIn("/deployments/{deployment}/audio/transcriptions", operation["backendUrlTemplate"])
        speech = apis["azure-speech-v1"]
        self.assertEqual(speech["authorization"]["operationConstraints"]["tts"]["bodyFormat"], "text")
        self.assertEqual(speech["authorization"]["operationConstraints"]["stt"]["bodyFormat"], "binary")
        for operation in speech["operations"]:
            self.assertEqual(operation["backendOverride"]["backendAuthentication"]["mode"], "speechEntra")

    def test_realtime_mini_is_an_allowed_ws_model_and_owned_calls_are_not_duplicated(self):
        """同じチケットを別モデルのWebRTC入口へ使う構成を増やさず、既存所有者制御を保ちます。"""
        apis = {api["name"]: api for api in self.normalize(bound_coverage())["apiDefinitions"]}
        mini = apis["openai-realtime-ws-v1"]
        self.assertEqual(mini["apiType"], "websocket")
        self.assertEqual(mini["websocket"], {"queryParameters": {}, "allowedModels": ["gpt-realtime-2.1", "gpt-realtime-2.1-mini"], "defaultModel": "gpt-realtime-2.1"})
        self.assertEqual(mini["authorization"]["mode"], "subscriptionKey")
        controllers = [(api["name"], constraint) for api in apis.values() for constraint in api["authorization"]["operationConstraints"].values() if constraint["kind"] == "realtimeCreate"]
        self.assertEqual(len(controllers), 1)
        self.assertEqual(controllers[0][1]["model"], "gpt-realtime-2.1")
        observer = apis["openai-realtime-observer-v1"]["websocket"]["realtimeObserver"]
        self.assertEqual(observer["publicCallPath"], controllers[0][1]["publicCallPath"])


    def test_realtime_models_must_be_deployed_explicit_and_granted_per_caller(self):
        for mutation in ("undeployed", "unapprovedDefault", "fixedQuery"):
            raw = bound_coverage()
            api = next(api for api in raw["apiDefinitions"] if api["name"] == "openai-realtime-ws-v1")
            if mutation == "undeployed": api["websocket"]["allowedModels"].append("undeployed-model")
            if mutation == "unapprovedDefault": api["websocket"]["defaultModel"] = "unapproved-model"
            if mutation == "fixedQuery": api["websocket"]["queryParameters"]["model"] = "gpt-realtime-2.1"
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_speech_operation_override_accepts_supported_backend_credentials(self):
        for mutation in ("credentialHeader", "secretAuthentication", "unknownAuthentication"):
            raw = bound_coverage()
            api = next(api for api in raw["apiDefinitions"] if api["name"] == "azure-speech-v1")
            override = api["operations"][1]["backendOverride"]
            if mutation == "credentialHeader": override["backendHeaders"]["Authorization"] = "SENTINEL_PROVIDER_SECRET"
            if mutation == "secretAuthentication": override["backendAuthentication"] = {
                "mode": "keyVaultHeader", "headerName": "api-key", "secretIdentifier": "https://fixture.vault.azure.net/secrets/provider",
            }
            if mutation == "unknownAuthentication": override["backendAuthentication"] = {"mode": "unknown"}
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError) as caught:
                self.normalize(raw)
            self.assertNotIn("SENTINEL_PROVIDER_SECRET", str(caught.exception))

    def test_shared_product_and_identical_subscription_are_valid_but_conflicts_fail(self):
        raw = api_fixture()
        second = copy.deepcopy(raw["apiDefinitions"][0])
        second.update(name="models-second", path="models/second/v1")
        raw["apiDefinitions"].append(second)
        self.normalize(raw)
        second["keySubscriptions"][0]["displayName"] = "conflicting subscription"
        with self.assertRaisesRegex(configuration.ConfigurationError, "identical settings"):
            self.normalize(raw)

    def test_provider_keys_are_only_external_key_vault_references(self):
        raw = bound_external_coverage()
        api = next(a for a in raw["apiDefinitions"] if a["name"] == "sample-rest-v1")
        # 外部サービス向けのキーをKey Vault参照として設定します。
        api.update(backendService="external", backendUrl="https://provider.example.com/rest")
        api.pop("backendPath", None)
        api["backendAuthentication"] = {"mode": "keyVaultHeader", "headerName": "Authorization", "headerValuePrefix": "Bearer ", "secretIdentifier": "https://fixture.vault.azure.net/secrets/registered-provider"}
        # Key Vaultの読取許可を明示的に切り替えて検証します。
        raw["services"]["grantApimSecretAccess"] = False
        with self.assertRaisesRegex(configuration.ConfigurationError, "grantApimSecretAccess"):
            self.normalize(raw)
        raw["services"]["grantApimSecretAccess"] = True
        self.normalize(raw)
        api["backendAuthentication"]["secretIdentifier"] = "SENTINEL_RAW_KEY"
        with self.assertRaises(configuration.ConfigurationError) as caught: self.normalize(raw)
        self.assertNotIn("SENTINEL_RAW_KEY", str(caught.exception))
        raw = bound_external_coverage()
        api = next(a for a in raw["apiDefinitions"] if a["name"] == "openai-embeddings-v1")
        api["backendAuthentication"] = {"mode": "keyVaultHeader", "headerName": "api-key", "secretIdentifier": "https://fixture.vault.azure.net/secrets/registered-provider"}
        with self.assertRaisesRegex(configuration.ConfigurationError, "disables local key"):
            self.normalize(raw)

    def test_managed_waf_and_api_body_limits_cannot_contradict(self):
        for change in ("body", "inspect", "upload"):
            raw = bound_coverage()
            if change == "body": raw["gateway"]["wafMaxRequestBodySizeInKb"] = 128; raw["gateway"]["wafRequestBodyInspectLimitInKb"] = 128
            if change == "inspect": raw["gateway"]["wafRequestBodyInspectLimitInKb"] = 2001
            if change == "upload": raw["gateway"]["wafFileUploadLimitInMb"] = 0
            with self.subTest(change=change), self.assertRaises(configuration.ConfigurationError): self.normalize(raw)
        # An external WAF's limits are unknown locally; its owner must verify it.
        raw = bound_coverage(); raw["gateway"]["existingWafPolicyId"] = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-network/providers/Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies/external"
        raw["gateway"].update(wafMaxRequestBodySizeInKb=128,wafRequestBodyInspectLimitInKb=128)
        self.normalize(raw)

    def test_native_protocol_contract_cannot_inspect_frames(self):
        for api_type in ("grpc", "websocket", "mcp"):
            raw = bound_external_coverage()
            api = next(a for a in raw["apiDefinitions"] if a.get("apiType") == api_type)
            api["authorization"]["operationConstraints"]["invoke"]["bodyFormat"] = "json"
            with self.subTest(api_type=api_type), self.assertRaisesRegex(configuration.ConfigurationError, "streamed bodies"):
                self.normalize(raw)

    def test_external_protocol_fixtures_use_apim_managed_identity(self):
        definitions = self.normalize(bound_external_coverage())["apiDefinitions"]
        samples = [api for api in definitions if api["serviceClass"] == "sample"]
        self.assertEqual(len(samples), 6)
        for api in samples:
            with self.subTest(api=api["name"]):
                self.assertEqual(api["backendAuthentication"], {"mode": "managedIdentity"})
                expected_audience = "https://ai.azure.com" if api["name"] == "sample-hosted-agent-v1" else "https://provider.example.com"
                self.assertEqual(api["backendAudience"], expected_audience)
                self.assertEqual(api["authorization"]["mode"], "subscriptionKey")

    def test_websocket_targets_and_provider_credentials_are_fixed(self):
        for mutate in (lambda a: a["websocket"]["queryParameters"].update(agent_id="dynamic"),
                       lambda a: a["backendHeaders"].update(Authorization="raw-secret"),
                       lambda a: a["backendHeaders"].update(x="{{secret}}")):
            raw = bound_coverage()
            api = next(a for a in raw["apiDefinitions"] if a.get("apiType") == "websocket")
            api["backendHeaders"] = {}
            mutate(api)
            with self.assertRaises(configuration.ConfigurationError): self.normalize(raw)

    def test_multipart_model_requires_path_binding_and_explicit_media(self):
        for mutate in (lambda a, c: a["operations"][1].pop("backendUrlTemplate"),
                       lambda a, c: c.update(contentTypes=["*/*"]),
                       lambda a, c: c.update(integerLimits={"n": {"minimum": 1, "maximum": 1}})):
            raw = bound_coverage()
            api = next(a for a in raw["apiDefinitions"] if a["name"] == "openai-images-v1")
            constraint = api["authorization"]["operationConstraints"]["image-edit"]
            mutate(api, constraint)
            with self.assertRaises(configuration.ConfigurationError): self.normalize(raw)

    def test_realtime_key_network_lifetime_and_call_id_are_required(self):
        for change in ("network", "ttl", "callId", "missingHangup", "unknownModel"):
            raw = bound_coverage()
            api = next(a for a in raw["apiDefinitions"] if a["name"] == "openai-realtime-control-v1")
            if change == "network": raw["network"]["apimKeyVaultAccessEnabled"] = False; raw["gateway"]["grpc"]["enabled"] = False
            if change == "ttl": api["authorization"]["operationConstraints"]["realtime-hangup"]["sessionTtlSeconds"] = 300
            if change == "callId": api["operations"][1]["urlTemplate"] = "/calls/{other}/hangup"; api["operations"][1]["templateParameters"][0]["name"] = "other"
            if change == "missingHangup": api["operations"].pop(); api["authorization"]["operationConstraints"].pop("realtime-hangup")
            if change == "unknownModel": api["authorization"]["operationConstraints"]["realtime-create"]["model"] = "unknown"
            with self.subTest(change=change), self.assertRaises(configuration.ConfigurationError): self.normalize(raw)

    def test_foundry_models_broad_builtin_role_is_explicit_opt_in(self):
        raw = fixture()
        self.assertTrue(self.normalize(raw)["services"]["grantApimFoundryModelsAccess"])
        raw["services"].pop("grantApimFoundryModelsAccess")
        self.assertFalse(self.normalize(raw)["services"]["grantApimFoundryModelsAccess"])
        self.assertTrue(self.normalize(bound_coverage())["services"]["grantApimFoundryModelsAccess"])
        raw = fixture(); raw["services"]["grantApimFoundryModelsAccess"] = "true"
        with self.assertRaisesRegex(configuration.ConfigurationError, "grantApimFoundryModelsAccess"):
            self.normalize(raw)

    def test_foundation_can_prepare_realtime_key_before_apis_exist(self):
        raw = fixture()
        raw["gateway"]["grpc"]["enabled"] = False
        raw["gateway"].pop("realtimeControlEnabled")
        self.assertFalse(self.normalize(raw)["gateway"]["realtimeControlEnabled"])
        raw["gateway"]["realtimeControlEnabled"] = True
        raw["network"]["apimKeyVaultAccessEnabled"] = False
        with self.assertRaisesRegex(configuration.ConfigurationError, "Realtime control ticket"):
            self.normalize(raw)
        raw["network"]["apimKeyVaultAccessEnabled"] = True
        actual = self.normalize(raw)
        self.assertTrue(actual["gateway"]["realtimeControlEnabled"])
        self.assertEqual(actual["apiDefinitions"], [])
        raw["gateway"]["realtimeControlEnabled"] = "true"
        with self.assertRaisesRegex(configuration.ConfigurationError, "realtimeControlEnabled"):
            self.normalize(raw)

    def test_observer_is_bound_to_the_same_session_owner_contract(self):
        for change in ("ttl", "controller", "credentials", "mixedQuery"):
            raw = bound_coverage()
            api = next(a for a in raw["apiDefinitions"] if a["name"] == "openai-realtime-observer-v1")
            if change == "ttl": api["websocket"]["realtimeObserver"]["sessionTtlSeconds"] = 300
            if change == "controller": api["websocket"]["realtimeObserver"]["publicCallPath"] = "/other/calls"
            if change == "credentials": api["backendAuthentication"] = {"mode": "none"}
            if change == "mixedQuery": api["websocket"]["queryParameters"] = {"model": "unapproved"}
            with self.subTest(change=change), self.assertRaises(configuration.ConfigurationError): self.normalize(raw)

    def test_grpc_import_has_no_external_proto_dependencies_and_uses_valid_port(self):
        raw = bound_external_coverage()
        api = next(a for a in raw["apiDefinitions"] if a.get("apiType") == "grpc")
        self.assertEqual(api["path"], "")
        api["grpc"]["definition"] += '\nimport "external.proto";'
        with self.assertRaisesRegex(configuration.ConfigurationError, "self-contained"):
            self.normalize(raw)
        for port in (65000, 65535, 443, 3389):
            raw = bound_external_coverage(); raw["gateway"]["grpc"]["publicPort"] = port
            with self.subTest(port=port), self.assertRaises(configuration.ConfigurationError): self.normalize(raw)

    def test_mcp_endpoint_names_and_transport_are_official_contracts(self):
        raw = bound_external_coverage()
        api = next(a for a in raw["apiDefinitions"] if a.get("mcpProxy", {}).get("transport") == "sse")
        api["mcpProxy"]["endpoints"].pop()
        with self.assertRaisesRegex(configuration.ConfigurationError, "official transport"):
            self.normalize(raw)

    def test_mcp_endpoint_paths_are_literal_and_never_supply_a_proxy_destination(self):
        """旧SSEの公開POSTへ使う値に、別host・query・経路解釈の違いを入れない。"""
        for path in ("https://other.example.com/messages/", "//other.example.com/messages/",
                     "/legacy/messages/?next=https://other.example.com", "/legacy/messages/#fragment",
                     "/legacy/../messages/", "/legacy/./messages/", "/legacy//messages/",
                     "/legacy/%2e%2e/messages/", "/legacy%2fmessages/", "/messages/{target}"):
            raw = bound_external_coverage()
            api = next(a for a in raw["apiDefinitions"] if a.get("mcpProxy", {}).get("transport") == "sse")
            next(endpoint for endpoint in api["mcpProxy"]["endpoints"] if endpoint["name"] == "message")["uriTemplate"] = path
            with self.subTest(path=path), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_legacy_mcp_uses_origin_and_complete_server_paths_without_tool_reconfiguration(self):
        """Toolsが単体公開時に返すmessage先を、そのままAPIM登録へ使う。"""
        api = next(a for a in self.normalize(bound_external_coverage())["apiDefinitions"] if a["name"] == "sample-mcp-legacy")
        self.assertEqual(api["backendUrl"], "https://provider.example.com")
        self.assertEqual({endpoint["name"]: endpoint["uriTemplate"] for endpoint in api["mcpProxy"]["endpoints"]},
                         {"sse": "/legacy/sse", "message": "/legacy/messages/"})

    def test_static_card_cannot_expand_named_value_or_policy_code(self):
        raw = bound_external_coverage()
        api = next(a for a in raw["apiDefinitions"] if a["name"] == "sample-a2a-v1")
        api["operations"][1]["staticResponse"]["body"]["description"] = "{{private-secret}}"
        with self.assertRaisesRegex(configuration.ConfigurationError, "named-value"):
            self.normalize(raw)


def actual_expression(path, name):
    """APIM policyから指定の実C#式を取り出し、別実装へ置き換えずに検証する。"""
    value = expression(POLICIES / path, "./inbound/set-variable[@name='" + name + "']", "value")
    return value.replace('JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("__AUTHORIZATION_BASE64__")))', 'JObject.Parse((string)context.Variables["config"])')


CSHARP = r'''
using System;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body { public string Data; public int Reads; public T As<T>(bool preserveContent=false) { Reads++; return (T)(object)JObject.Parse(Data); } }
public class Url { public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }
public class Request { public string Method="POST"; public Body Body; public Url Url=new Url(); public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>(); }
public class Named { public string Id; public string UrlTemplate="/responses"; }
public class Context { public Request Request=new Request(); public Named Operation=new Named {Id="agent-responses"}; public Named Api=new Named {Id="gui-added-api"}; public Named Subscription; public Named Product; public Dictionary<string,object> Variables=new Dictionary<string,object>(); }
public static class Extensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> d,string key,string fallback) { return d.ContainsKey(key)? String.Join(" ",d[key]):fallback; }
 public static string GetValueOrDefault(this Dictionary<string,string> d,string key,string fallback) { return d.ContainsKey(key)?d[key]:fallback; }
}
public static class ExpandedPolicyFixture {
 public static bool Target(Context context) { __TARGET__ }
 public static bool Ingress(Context context) { __INGRESS__ }
 public static bool Query(Context context) { __QUERY__ }
 public static string Auth(Context context) { __AUTH__ }
 public static string Run(string input) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var c=new Context(); c.Request.Body=new Body {Data=(item["body"]??new JObject()).ToString(Formatting.None)};
   c.Variables["config"]=(item["config"]??new JObject()).ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"]="Ocp-Apim-Subscription-Key";
   foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties()) c.Request.Headers[entry.Name]=entry.Value is JArray ? entry.Value.Select(x=>(string)x).ToArray():new [] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties()) c.Request.Url.Query[entry.Name]=entry.Value is JArray ? entry.Value.Select(x=>(string)x).ToArray():new [] {(string)entry.Value};
   if(item["key"]!=null) { c.Subscription=new Named {Id=(string)item["key"]};c.Product=new Named {Id="tool-users"};c.Request.Headers["Ocp-Apim-Subscription-Key"]=new [] {"fixture-not-a-secret"}; }
   if(item["api"]!=null)c.Api.Id=(string)item["api"];
   if(item["grpc"]!=null)c.Variables["coreNativeGrpc"]=(bool)item["grpc"];
   string mode=(string)item["mode"];
   JToken result=mode=="ingress"?(JToken)new JValue(Ingress(c)):mode=="query"?new JValue(Query(c)):mode=="auth"?JObject.Parse(Auth(c)):new JValue(Target(c));
   results[(string)item["id"]]=new JObject(new JProperty("result",result),new JProperty("reads",c.Request.Body.Reads));
  }
  return results.ToString(Formatting.None);
 }
}
'''


class ExpandedPolicyTests(unittest.TestCase):
    """モデル・音声・ファイル・gRPCのAPI設定とAPIM式を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH: raise unittest.SkipTest("C# runtime unavailable; no live checks were performed")
        hosted = {"operationConstraints": {"agent-responses": {"kind": "hostedAgentPost"}}}
        valid = {"input": "synthetic", "store": False, "metadata": {"sample_mode": "fixed"}}
        cases = []
        for mode in ("fixed", "model", "pii", "auth-probe"):
            for stream in (False, True):
                body = copy.deepcopy(valid); body["metadata"]["sample_mode"] = mode; body["stream"] = stream
                cases.append({"id": f"agent-{mode}-{stream}", "config": hosted, "body": body})
        for name, patch in (("state", {"previous_response_id": "other"}), ("store", {"store": True}),
                            ("metadata", {"metadata": {"sample_mode": "other"}}), ("nested", {"input": [{"role": "user", "content": [{"type": "input_text", "text": "x", "file_id": "other"}]}]})):
            cases.append({"id": "agent-deny-" + name, "config": hosted, "body": {**valid, **patch}})
        for name, headers, api in (("missing", {}, "gui-added-api"), ("spoof", {"Host": "trusted.example.com", "X-Core-L7-Ingress": "invented"}, "gui-added-api"),
                                   ("grpc-header", {"Content-Type": "application/grpc", "coreNativeGrpc": "true"}, "gui-added-api"),
                                   ("valid", {"X-Core-L7-Ingress": "fixture-ingress-proof"}, "gui-added-api"),
                                   ("duplicate", {"X-Core-L7-Ingress": ["fixture-ingress-proof", "fixture-ingress-proof"]}, "gui-added-api"), ("grpc", {}, "grpc-api")):
            cases.append({"id": "ingress-" + name, "mode": "ingress", "headers": headers, "api": api, "grpc": name == "grpc"})
        for name, query in (("missing", {}), ("fixed", {"model": "approved", "api-version": "2026-01-01"}),
                             ("target", {"model": "unapproved"}), ("agent", {"agent_id": "other"}),
                             ("duplicate", {"model": ["approved", "unapproved"]})):
            cases.append({"id": "query-" + name, "mode": "query", "query": query})
        auth = {"mode": "subscriptionKey"}
        cases.append({"id": "auth-valid", "mode": "auth", "config": auth, "key": "approved-key"})
        cases.append({"id": "auth-deny-missing", "mode": "auth", "config": auth})
        cases.append({"id": "auth-deny-empty", "mode": "auth", "config": auth, "key": ""})
        ingress = expression(SERVICE_POLICY, "./inbound/choose/when", "condition").replace("__L7_INGRESS_REQUIRED__", "true").replace("__L7_INGRESS_EXPECTED__", "fixture-ingress-proof")
        query = actual_expression("protocol.xml", "gatewayProtocolQueryAllowed").replace("__WEBSOCKET_QUERY_BASE64__", base64.b64encode(b'{"model":"approved","api-version":"2026-01-01"}').decode())
        source = CSHARP.replace("__TARGET__", actual_expression("gateway.xml", "gatewayTargetAllowed")).replace("__INGRESS__", ingress).replace("__QUERY__", query).replace("__AUTH__", actual_expression("protocol.xml", "gatewayDecision"))
        with tempfile.TemporaryDirectory(prefix="core-expanded-policies-") as temp:
            path = Path(temp); (path / "policy.cs").write_text(source); (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[ExpandedPolicyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")], capture_output=True, text=True, timeout=60)
            if result.returncode: raise AssertionError("Actual C# policy predicates failed:\n" + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_hosted_agent_fixed_model_pii_auth_probe_and_sse_contracts(self):
        for mode in ("fixed", "model", "pii", "auth-probe"):
            for stream in (False, True): self.assertTrue(self.results[f"agent-{mode}-{stream}"]["result"])
        for name in ("state", "store", "metadata", "nested"): self.assertFalse(self.results["agent-deny-" + name]["result"])

    def test_l4_listener_cannot_bypass_l7_proof_for_normal_api(self):
        for name in ("missing", "spoof", "duplicate", "grpc-header"): self.assertTrue(self.results["ingress-" + name]["result"])
        for name in ("valid", "grpc"): self.assertFalse(self.results["ingress-" + name]["result"])

    def test_websocket_target_query_is_fixed_and_unread(self):
        for name in ("missing", "fixed"): self.assertTrue(self.results["query-" + name]["result"])
        for name in ("target", "agent", "duplicate"): self.assertFalse(self.results["query-" + name]["result"])
        for name, item in self.results.items():
            if name.startswith(("query-", "auth-", "ingress-")): self.assertEqual(item["reads"], 0)

    def test_protocol_invoke_requires_a_verified_subscription(self):
        self.assertTrue(self.results["auth-valid"]["result"]["allowed"])
        for name in ("missing", "empty"):
            self.assertFalse(self.results["auth-deny-" + name]["result"]["allowed"])


if __name__ == "__main__": unittest.main()
