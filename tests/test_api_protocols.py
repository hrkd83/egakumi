"""モデル・音声・ファイル・WebSocket/MCPのAPI設定とAPIM式を検証する。

公開する操作と上流の形式を組み合わせ、モデル宣言・不正なサイズ・別セッションの条件を確認する。実XMLから取り出した式も動かすが、Azureへの配備成功は別の実機試験で確認する。
"""
from policy_fixture_paths import policy_fixture_path
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE, api_fixture, configuration, fixture
from test_gateway_limits_telemetry import PWSH, expression, single_expression
import prepare_coverage
import standard_apis
from api_definition_helpers import catalog_overlay, configured_base
from deployment.helpers.apim_api_request import expand_api_request_preparation_fragment

POLICIES = CORE / "apps/apim/policies"
SERVICE_POLICY = CORE / "apps/apim/policies/service.xml"


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
    """汎用protocol能力の試験だけに、外部5 APIのfixtureを明示追加する。"""
    raw = bound_coverage()
    fixture_data = json.loads((CORE / "tests/fixtures/external-api-definitions.json").read_text())
    external = prepare_coverage.substitute(fixture_data, _test_bindings(fixture_data, raw))
    raw["apiDefinitions"].extend(external["apiDefinitions"])
    return raw


class ProtocolConfigurationTests(unittest.TestCase):
    """モデル・音声・ファイル・WebSocket/MCPのAPI設定とAPIM式を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_bound_example_has_only_formal_apis_and_stays_unpublished(self):
        actual = self.normalize(bound_coverage())
        self.assertEqual({a["productId"] for a in actual["apiDefinitions"]}, {"model-users", "pii-users", "connection-users"})
        self.assertFalse(actual["publishApis"])
        self.assertNotIn("apimKeyVaultAccessEnabled", actual["network"])
        self.assertEqual({a["apiType"] for a in actual["apiDefinitions"]}, {"http", "websocket"})

    def test_external_protocol_fixtures_are_explicitly_separate_from_standard(self):
        actual = self.normalize(bound_external_coverage())
        self.assertEqual(len(actual["apiDefinitions"]), len(standard_apis.STANDARD_API_NAMES) + 5)
        self.assertEqual(sum(api["serviceClass"] == "sample" for api in actual["apiDefinitions"]), 5)
        self.assertEqual({api["apiType"] for api in actual["apiDefinitions"]}, {"http", "websocket", "mcp"})

    def test_dedicated_operation_routes_remain_required_in_formal_configuration(self):
        kinds = ('hostedAgentPost', 'liveCreate', 'realtimeCreate', 'realtimeHangup',
                 'documentAnalysis', 'documentAnalysisStatus', 'pii')
        source = bound_external_coverage()
        for kind in kinds:
            for change in ('valid', 'method', 'path', 'name'):
                raw = copy.deepcopy(source)
                api = next(api for api in raw['apiDefinitions']
                           if any(value['kind'] == kind for value in api['authorization']['operationConstraints'].values()))
                constraints = api['authorization']['operationConstraints']
                operation = next(operation for operation in api['operations'] if constraints[operation['name']]['kind'] == kind)
                if change == 'method':
                    operation['method'] = 'POST' if operation['method'] == 'GET' else 'GET'
                elif change == 'path':
                    operation['urlTemplate'] = '/neighbor' + ''.join('/{' + entry['name'] + '}'
                                                                     for entry in operation['templateParameters'])
                elif change == 'name':
                    old_name = operation['name']
                    operation['name'] = 'synthetic-renamed-operation'
                    constraints[operation['name']] = constraints.pop(old_name)
                    if 'mcp' in api:
                        api['mcp']['operationNames'] = [operation['name'] if name == old_name else name
                                                        for name in api['mcp'].get('operationNames', [])]
                fixed_name = kind in ('documentAnalysis', 'documentAnalysisStatus', 'pii')
                tuned_waf_name = kind in ('liveCreate', 'realtimeCreate')
                rejected = change in ('method', 'path') or change == 'name' and fixed_name
                with self.subTest(kind=kind, change=change):
                    if change == 'name' and tuned_waf_name:
                        # APIMのkind検査とは別に、現行の専用WAFは登録操作名の変更を拒否します。
                        with self.assertRaisesRegex(ValueError, 'WAF-tuned route has a changed API input contract'):
                            self.normalize(raw)
                    elif rejected:
                        with self.assertRaises(configuration.ConfigurationError):
                            self.normalize(raw)
                    else:
                        normalized = self.normalize(raw)
                        actual = next(item for item in normalized['apiDefinitions'] if item['name'] == api['name'])
                        self.assertEqual(actual['authorization']['operationConstraints'][operation['name']]['kind'], kind)
                        self.assertIn(operation['name'], [item['name'] for item in actual['operations']])
        # APIM固有の実設定検査単体に、Hosted/Live/Realtimeの新しい固定名制限は追加しません。
        # WAFを含む全体の配備可否は上の対照で確認し、単体の成功で置き換えません。
        normalized = self.normalize(source)
        models = {item['name'] for item in normalized['services']['modelDeployments']}
        for kind in ('hostedAgentPost', 'liveCreate', 'realtimeCreate', 'realtimeHangup'):
            api = copy.deepcopy(next(api for api in normalized['apiDefinitions']
                                     if any(value['kind'] == kind for value in api['authorization']['operationConstraints'].values())))
            constraints = api['authorization']['operationConstraints']
            operation = next(operation for operation in api['operations'] if constraints[operation['name']]['kind'] == kind)
            constraint = copy.deepcopy(constraints[operation['name']])
            operation['name'] = 'synthetic-renamed-operation'
            with self.subTest(kind=kind, layer='APIM operation constraint'):
                configuration._normalize_operation_constraint(constraint, api, operation, models, 'synthetic.operation')
                self.assertEqual(constraint['kind'], kind)

    def test_model_overrides_cannot_select_a_dedicated_operation_kind(self):
        for kind in ('hostedAgentPost', 'liveCreate', 'realtimeCreate', 'realtimeHangup',
                     'documentAnalysis', 'documentAnalysisStatus', 'pii'):
            raw = api_fixture()
            constraint = raw['apiDefinitions'][0]['authorization']['operationConstraints']['responses-create']
            constraint['modelSettings'] = {'gpt-approved': {'kind': kind}}
            with self.subTest(kind=kind), self.assertRaisesRegex(configuration.ConfigurationError, 'retain.*JSON model contract'):
                self.normalize(raw)

    def test_deployed_models_are_reachable_through_declared_api_contracts(self):
        """API例が公開するモデルと、接続契約を追加確認するモデルを明示照合する。"""
        raw = bound_coverage()
        apis = {api["name"]: api for api in self.normalize(raw)["apiDefinitions"]}
        deployed = {model["name"] for model in raw["services"]["modelDeployments"]}
        published = set()
        for api in apis.values():
            for constraint in api["authorization"]["operationConstraints"].values():
                # OCR/layout are built-in service models, not Foundry model deployments.
                if constraint["kind"] not in ("documentAnalysis", "documentAnalysisStatus"):
                    published.update(constraint.get("allowedModels", []))
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
            self.assertEqual(set(constraint["modelSettings"]), set(constraint["allowedModels"]))
            for model, settings in constraint["modelSettings"].items():
                variant = settings["settingId"]
                self.assertEqual(model, f"FLUX.2-{variant}")
                self.assertEqual(settings["backendUrlTemplate"], f"/providers/blackforestlabs/v1/flux-2-{variant}?api-version=preview")
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
        stem = self.normalize(raw)["resourceStem"]
        tls_vault = ".".join((f"kv-{stem}-tls", "vault.azure.net"))
        api["backendAuthentication"]["secretIdentifier"] = f"https://{tls_vault}/secrets/registered-provider"
        with self.assertRaisesRegex(configuration.ConfigurationError, "public TLS certificate vault"):
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
        for change in ("inspect", "upload"):
            raw = bound_coverage()
            if change == "body": raw["gateway"]["wafMaxRequestBodySizeInKb"] = 128; raw["gateway"]["wafRequestBodyInspectLimitInKb"] = 128
            if change == "inspect": raw["gateway"]["wafRequestBodyInspectLimitInKb"] = 2001
            if change == "upload": raw["gateway"]["wafFileUploadLimitInMb"] = 0
            with self.subTest(change=change), self.assertRaises(configuration.ConfigurationError): self.normalize(raw)
        # Managed AI paths inspect a bounded prefix while accepting the full request.
        raw = bound_coverage()
        raw["gateway"].update(wafMaxRequestBodySizeInKb=128, wafRequestBodyInspectLimitInKb=128)
        self.normalize(raw)
        # A custom path never inherits that relaxation from its API prefix.
        custom = next(a for a in raw["apiDefinitions"] if a["name"] == "microsoft-decision-v1")
        custom["path"] = "custom/decision/v1"
        with self.assertRaises(configuration.ConfigurationError): self.normalize(raw)
        # An external WAF's limits are unknown locally; its owner must verify it.
        raw = bound_coverage(); raw["gateway"]["existingWafPolicyId"] = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-network/providers/Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies/external"
        raw["gateway"].update(wafMaxRequestBodySizeInKb=128,wafRequestBodyInspectLimitInKb=128)
        self.normalize(raw)

    def test_native_protocol_contract_cannot_inspect_frames(self):
        for api_type in ("websocket", "mcp"):
            raw = bound_external_coverage()
            api = next(a for a in raw["apiDefinitions"] if a.get("apiType") == api_type)
            api["authorization"]["operationConstraints"]["invoke"]["bodyFormat"] = "json"
            with self.subTest(api_type=api_type), self.assertRaisesRegex(configuration.ConfigurationError, "streamed bodies"):
                self.normalize(raw)

    def test_external_protocol_fixtures_use_apim_managed_identity(self):
        definitions = self.normalize(bound_external_coverage())["apiDefinitions"]
        samples = [api for api in definitions if api["serviceClass"] == "sample"]
        self.assertEqual(len(samples), 5)
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
            if change == "network": raw["network"]["apimKeyVaultAccessEnabled"] = False
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
        from deployment.helpers.grpc_lifecycle import realtime_enabled
        raw = fixture()
        raw["gateway"].pop("realtimeControlEnabled")
        without_realtime = self.normalize(raw)
        self.assertFalse(without_realtime["gateway"]["realtimeControlEnabled"])
        self.assertFalse(realtime_enabled(without_realtime))
        self.assertNotIn("grpc", without_realtime["gateway"])
        raw["gateway"]["realtimeControlEnabled"] = True
        actual = self.normalize(raw)
        self.assertTrue(actual["gateway"]["realtimeControlEnabled"])
        self.assertTrue(realtime_enabled(actual))
        self.assertNotIn("grpc", actual["gateway"])
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

    def test_retired_grpc_api_and_proto_contract_are_explicitly_rejected(self):
        for field, value in (("apiType", "grpc"), ("grpc", {"definition": 'syntax = "proto3";'})):
            raw = bound_external_coverage()
            raw["apiDefinitions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

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
    sources = {
        ("rest.xml", "gatewayOperationSelection"): "core-api-operation-settings.cs",
        ("rest.xml", "gatewayDecision"): "core-api-authentication-result.cs",
        ("rest.xml", "gatewayTargetDecision"): "core-api-target-validation.cs",
    }
    if (path, name) in sources:
        # 棚卸済みHTTP部品は、Azure用XMLへ展開する同名C#の正本を直接実行します。
        return policy_fixture_path(POLICIES, sources[(path, name)]).read_text(encoding="utf-8")
    value = expression(policy_fixture_path(POLICIES, path), "./inbound/set-variable[@name='" + name + "']", "value")
    return value.replace('JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("__AUTHORIZATION_BASE64__")))', 'JObject.Parse((string)context.Variables["config"])')


def responses_code_interpreter_input_cases():
    """input配列直下の既知のコンテナ呼出しだけを拒否する境界の合成例。"""
    call = {'type': 'code_interpreter_call', 'id': 'synthetic-code-call',
            'container_id': 'synthetic-container', 'status': 'completed',
            'code': 'print("synthetic")', 'outputs': []}
    message = {'type': 'message', 'id': 'synthetic-message', 'role': 'assistant', 'content': [
        {'type': 'output_text', 'text': 'synthetic code_interpreter_call', 'annotations': [
            {'type': 'container_file_citation', 'file_id': 'synthetic-file', 'container_id': 'synthetic-container'}]}]}
    function = {'type': 'function_call', 'id': 'synthetic-function', 'call_id': 'synthetic-call',
                'name': 'synthetic_tool', 'arguments': '{}'}
    output = {'type': 'function_call_output', 'id': 'synthetic-output', 'call_id': 'synthetic-call',
              'output': {'file_id': 'synthetic-file', 'container_id': 'synthetic-container'}}
    cases = [
        ('direct', [call], 403), ('type-only', [{'type': 'code_interpreter_call'}], 403),
        ('first', [call, message, function, output], 403),
        ('last', [message, function, output, call], 403),
        ('mixed-non-object', [None, 1, 'synthetic', [call], call], 403),
        ('ordinary-items', [message, {'type': 'reasoning', 'id': 'synthetic-reasoning',
                                    'encrypted_content': 'synthetic-encrypted', 'summary': []},
                            function, output, {'type': 'item_reference', 'id': 'synthetic-reference'},
                            {'id': 'synthetic-client-id', 'content': 'synthetic'},
                            {'type': 'input_file', 'file_id': 'synthetic-file'}], 200),
        ('empty-array', [], 200),
        ('nested-extension', [{**message, 'provider_extension': call}], 200),
        ('nested-content', [{**message, 'content': [call]}], 200),
        ('nested-array', [[call]], 200),
        ('function-output', [{**output, 'output': call}], 200),
        ('non-array-object', call, 200),
        ('type-missing', [{'id': 'synthetic-code-call', 'container_id': 'synthetic-container'}], 200),
        ('type-null', [{**call, 'type': None}], 200),
        ('type-boolean', [{**call, 'type': True}], 200),
        ('type-number', [{**call, 'type': 1}], 200),
        ('type-array', [{**call, 'type': ['code_interpreter_call']}], 200),
        ('type-object', [{**call, 'type': {'name': 'code_interpreter_call'}}], 200),
        ('different-case', [{**call, 'type': 'Code_Interpreter_Call'}], 200),
        ('surrounding-whitespace', [{**call, 'type': ' code_interpreter_call '}], 200),
        ('different-property-case', [{'Type': 'code_interpreter_call', 'container_id': 'synthetic-container'}], 200),
    ]
    return [(label, {'input': copy.deepcopy(items)}, status) for label, items, status in cases]


def responses_include_cases():
    """includeの原値を渡し、独立したツール・コンテナ拒否だけを維持する合成例。"""
    cases = [
        ('absent', {}, 200),
        ('encrypted-content', {'include': ['reasoning.encrypted_content']}, 200),
        ('logprobs', {'include': ['message.output_text.logprobs']}, 200),
        ('existing-values', {'include': ['reasoning.encrypted_content', 'message.output_text.logprobs']}, 200),
        ('additional-public-value', {'include': ['file_search_call.results']}, 200),
        ('mixed-values', {'include': ['file_search_call.results', 'reasoning.encrypted_content', 'synthetic.include']}, 200),
        ('unknown-value', {'include': ['synthetic.include']}, 200),
        ('null', {'include': None}, 200), ('empty-array', {'include': []}, 200),
        ('string', {'include': 'reasoning.encrypted_content'}, 200),
        ('number', {'include': 1}, 200), ('boolean', {'include': False}, 200),
        ('object', {'include': {'selection': ['synthetic.include']}}, 200),
        ('mixed-types', {'include': ['reasoning.encrypted_content', None, 1, False,
                                     {'selection': 'synthetic.include'}, ['synthetic.include']]}, 200),
        ('order-and-duplicates', {'include': ['message.output_text.logprobs', 'file_search_call.results',
                                             'reasoning.encrypted_content', 'message.output_text.logprobs']}, 200),
        ('allowed-tools', {'include': ['synthetic.include'], 'tools': [{'type': 'function', 'name': 'synthetic_tool'}],
                           'tool_choice': {'type': 'function', 'name': 'synthetic_tool'}}, 200),
        ('forbidden-tools', {'include': ['file_search_call.results'], 'tools': [{'type': 'web_search'}]}, 403),
        ('forbidden-tool-choice', {'include': None, 'tool_choice': {'type': 'code_interpreter'}}, 403),
        ('code-interpreter-input', {'include': {'selection': ['synthetic.include']},
                                    'input': [{'type': 'code_interpreter_call', 'container_id': 'synthetic-container'}]}, 403),
    ]
    return [(label, copy.deepcopy(fields), status) for label, fields, status in cases]


def responses_tool_choice_cases():
    """allowed_tools内のfunctionだけを許可し、名前・mode・原値保持を実式で対照する。"""
    function = {'type': 'function', 'name': 'synthetic_tool'}
    choice = {'type': 'allowed_tools', 'mode': 'auto', 'tools': [function]}
    cases = [('absent', {}, 200)]
    for label, value in (
        ('null', None), ('auto', 'auto'), ('required', 'required'), ('none', 'none'),
        ('unknown-string', 'synthetic_choice'), ('number', 1), ('boolean', False),
        ('empty-array', []), ('object-array', [{'type': 'code_interpreter'}]),
    ):
        cases.append(('nonobject-' + label, {'tool_choice': value}, 200))
    cases.extend([
        ('function', {'tool_choice': function}, 200),
        ('function-missing-name', {'tool_choice': {'type': 'function'}}, 200),
        ('function-raw-fields', {'tool_choice': {'type': 'function', 'name': None, 'mode': 1,
            'tools': [{'type': 'mcp'}], 'provider_extension': {'type': 'code_interpreter'}}}, 200),
    ])
    for label, fields in (
        ('missing', {}), ('null', {'type': None}), ('number', {'type': 1}),
        ('boolean', {'type': True}), ('array', {'type': ['function']}),
        ('object', {'type': {'name': 'function'}}), ('empty', {'type': ''}),
        ('different-case', {'type': 'Allowed_Tools'}), ('builtin', {'type': 'code_interpreter'}),
        ('mcp', {'type': 'mcp'}), ('unknown', {'type': 'synthetic_choice'}),
    ):
        cases.append(('object-type-' + label, {'tool_choice': fields}, 403))
    cases.extend([
        ('allowed-one', {'tool_choice': choice}, 200),
        ('allowed-empty', {'tool_choice': {**choice, 'tools': []}}, 200),
        ('allowed-multiple', {'tool_choice': {**choice, 'tools': [function,
            {'type': 'function', 'name': 'synthetic_other_tool'}]}}, 200),
        ('allowed-duplicates', {'tool_choice': {**choice, 'tools': [function, function]}}, 200),
        ('allowed-mode-missing', {'tool_choice': {'type': 'allowed_tools', 'tools': [function]}}, 200),
        ('allowed-mode-required', {'tool_choice': {**choice, 'mode': 'required'}}, 200),
        ('allowed-mode-unknown', {'tool_choice': {**choice, 'mode': 'synthetic_mode'}}, 200),
        ('allowed-mode-null', {'tool_choice': {**choice, 'mode': None}}, 200),
        ('allowed-mode-number', {'tool_choice': {**choice, 'mode': 1}}, 200),
        ('allowed-mode-array', {'tool_choice': {**choice, 'mode': ['synthetic_mode']}}, 200),
        ('allowed-name-unknown', {'tool_choice': {**choice,
            'tools': [{'type': 'function', 'name': 'synthetic_undeclared_tool'}]}}, 200),
        ('allowed-name-missing', {'tool_choice': {**choice, 'tools': [{'type': 'function'}]}}, 200),
        ('allowed-name-null', {'tool_choice': {**choice, 'tools': [{'type': 'function', 'name': None}]}}, 200),
        ('allowed-raw-fields', {'tool_choice': {**choice, 'provider_extension': {'type': 'mcp'},
            'tools': [{'type': 'function', 'name': {'synthetic': ['tool']}, 'mode': None,
                       'provider_extension': {'type': 'code_interpreter'}}]}}, 200),
        ('allowed-tools-missing', {'tool_choice': {'type': 'allowed_tools', 'mode': 'auto'}}, 403),
    ])
    for label, value in (('null', None), ('string', 'synthetic'), ('number', 1),
                         ('boolean', False), ('object', function)):
        cases.append(('allowed-tools-' + label, {'tool_choice': {**choice, 'tools': value}}, 403))
    for label, value in (('null', None), ('string', 'synthetic'), ('number', 1),
                         ('boolean', False), ('array', [function])):
        cases.append(('allowed-item-' + label, {'tool_choice': {**choice, 'tools': [function, value]}}, 403))
    cases.append(('allowed-item-type-missing', {'tool_choice': {**choice,
                  'tools': [function, {'name': 'synthetic_tool'}]}}, 403))
    for label, value in (
        ('null', None), ('number', 1), ('boolean', True), ('array', ['function']),
        ('object', {'name': 'function'}), ('different-case', 'Function'),
        ('whitespace', ' function '), ('unknown', 'synthetic_tool_type'),
        ('code-interpreter', 'code_interpreter'), ('web-search', 'web_search'),
        ('file-search', 'file_search'), ('mcp', 'mcp'),
    ):
        cases.append(('allowed-item-type-' + label, {'tool_choice': {**choice,
                      'tools': [function, {'type': value, 'name': 'synthetic_tool'}]}}, 403))
    cases.extend([
        ('allowed-mixed-builtin-first', {'tool_choice': {**choice,
            'tools': [{'type': 'code_interpreter'}, function]}}, 403),
        ('allowed-mixed-mcp-first', {'tool_choice': {**choice,
            'tools': [{'type': 'mcp', 'server_label': 'synthetic_server'}, function]}}, 403),
        ('declared-functions', {'tool_choice': {**choice,
            'tools': [{'type': 'function', 'name': 'synthetic_undeclared_tool'}]}, 'tools': [function]}, 200),
        ('declared-mcp', {'tool_choice': choice, 'tools': [function,
            {'type': 'mcp', 'server_label': 'synthetic_server'}]}, 403),
        ('declared-null', {'tool_choice': choice, 'tools': None}, 403),
        ('code-interpreter-input', {'tool_choice': choice,
            'input': [{'type': 'code_interpreter_call', 'container_id': 'synthetic-container'}]}, 403),
        ('nested-code-interpreter', {'tool_choice': choice, 'input': [{'type': 'function_call_output',
            'call_id': 'synthetic-call', 'output': {'type': 'code_interpreter_call',
                                                  'container_id': 'synthetic-container'}}]}, 200),
    ])
    return [(label, copy.deepcopy(fields), status) for label, fields, status in cases]


def json_object_body_failure_cases():
    """本文の内容・取得不備を、入力項目の判定とは別に実readerへ渡す合成例。"""
    cases = [(label, {'rawBody': raw}, 1) for label, raw in (
        ('empty', ''), ('blank', ' \t'), ('malformed', '{'), ('array', '[]'),
        ('string', '"synthetic"'), ('number', '1'), ('boolean', 'false'), ('null', 'null'),
    )]
    cases.extend([
        ('absent', {'bodyAbsent': True}, 0),
        ('reader-exception', {'bodyReadUnavailable': True}, 1),
        ('reader-null', {'bodyReadReturnsNull': True}, 1),
    ])
    return cases


def a2a_json_rpc_target_cases():
    """本文読取り不備400と、読めたJSON-RPCの既存条件403を分ける合成例。"""
    valid = {'jsonrpc': '2.0', 'id': 'synthetic-request', 'method': 'message/send'}
    cases = [('json-' + label, {'body': valid, **patch}, 400, reads)
             for label, patch, reads in json_object_body_failure_cases()]
    cases.extend([
        ('string-id', {'body': valid}, 200, 1),
        ('integer-id', {'body': {**valid, 'id': 1}}, 200, 1),
        ('empty-object', {'body': {}}, 403, 1),
        ('version-mismatch', {'body': {**valid, 'jsonrpc': '1.0'}}, 403, 1),
        ('id-type', {'body': {**valid, 'id': True}}, 403, 1),
        ('method-type', {'body': {**valid, 'method': 1}}, 403, 1),
        ('method-not-allowed', {'body': {**valid, 'method': 'message/stream'}}, 403, 1),
    ])
    return [(label, copy.deepcopy(patch), status, reads) for label, patch, status, reads in cases]


def hosted_agent_target_cases():
    """Agent入力・通常オプションの原値透過と、定義・状態選択／コンテナ403・読取り400の対照。"""
    valid = {'input': 'synthetic', 'store': False, 'metadata': {'sample_mode': 'fixed'}}
    array_input = [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic'}]}]
    cases = []
    for mode in ('fixed', 'model', 'pii', 'auth-probe'):
        for stream in (False, True):
            cases.append((mode + '-' + str(stream), {'body': {**valid, 'stream': stream,
                'metadata': {'sample_mode': mode}}}, 200, 1))
    cases.extend([
        ('minimal', {'body': valid}, 200, 1),
        ('array-input', {'body': {**valid, 'input': array_input}}, 200, 1),
        ('stream-null', {'body': {**valid, 'stream': None}}, 200, 1),
        ('stream-string', {'body': {**valid, 'stream': 'true'}}, 200, 1),
        ('input-blank', {'body': {**valid, 'input': ' \t'}}, 200, 1),
        ('input-empty-string', {'body': {**valid, 'input': ''}}, 200, 1),
    ])
    for label, text, status in (('empty', '', 200), ('blank', ' \t', 200), ('number', 1, 200)):
        cases.append(('input-array-text-' + label, {'body': {**valid, 'input': [
            {'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]}}, status, 1))
    # 複数入力の順序・値を保持し、入力の型・役割・参照項目の適否は提供元へ任せます。
    first = {'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic background'}]}
    second = {'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic question'}]}
    multiple = [first, second]
    for label, items in (
        ('two', multiple),
        ('three', [first, {'role': 'user', 'content': [{'type': 'input_text', 'text': '合成入力\n第二段落'}]}, second]),
        ('empty-and-blank-text', [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}
                                  for text in ('', ' \t')]),
    ):
        body = {**valid, 'input': items}
        patch = {'body': body}
        if label == 'three':
            # Unicodeを含むケースは送信wireを明示し、親fixtureのJObject再生成との表記差を避けます。
            patch['rawBody'] = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        cases.append(('input-multiple-' + label, patch, 200, 1))
    for label, provider_item in (
        ('null-item', None), ('string-item', 'synthetic'),
        ('id-only', {'id': 'synthetic-saved-item'}),
        ('item-reference', {'type': 'item_reference', 'id': 'synthetic-saved-item'}),
        ('inline-id', {**first, 'id': 'synthetic-saved-item'}),
        ('assistant', {**first, 'role': 'assistant'}),
        ('content-missing', {'role': 'user'}),
        ('content-string', {'role': 'user', 'content': 'synthetic'}),
        ('content-null-item', {'role': 'user', 'content': [None]}),
        ('input-file', {'role': 'user', 'content': [{'type': 'input_file', 'file_id': 'synthetic-saved-file'}]}),
        ('text-file-id', {'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic', 'file_id': 'synthetic-saved-file'}]}),
        ('text-missing', {'role': 'user', 'content': [{'type': 'input_text'}]}),
        ('text-null', {'role': 'user', 'content': [{'type': 'input_text', 'text': None}]}),
        ('text-number', {'role': 'user', 'content': [{'type': 'input_text', 'text': 1}]}),
    ):
        for position, items in (('first', [provider_item, first, second]), ('last', [first, second, provider_item])):
            cases.append(('input-multiple-' + label + '-' + position,
                          {'body': {**valid, 'input': items}}, 200, 1))
    cases.extend([
        ('input-multiple-store-true', {'body': {**valid, 'input': multiple, 'store': True}}, 200, 1),
        ('input-multiple-state-reference', {'body': {**valid, 'input': multiple,
            'previous_response_id': 'synthetic-saved-response'}}, 403, 1),
    ])
    # 同一メッセージ内の複数partも結合せず、参照・型を含めて全て原値で渡します。
    parts = first['content'] + second['content']
    multipart = {'role': 'user', 'content': parts}
    for position, items in (('first', [multipart, first, second]), ('last', [first, second, multipart])):
        # MOD-70時点では件数だけで403だった同じ条件を、現在は通過させます。
        cases.append(('input-multiple-content-multiple-' + position, {'body': {**valid, 'input': items}}, 200, 1))
    for label, items in (
        ('two-parts', [multipart]),
        ('multiple-messages', [multipart, {'role': 'user', 'content': [
            {'type': 'input_text', 'text': '合成入力\n第二段落'}, {'type': 'input_text', 'text': ''},
            {'type': 'input_text', 'text': ' \t'}]}]),
        ('empty-blank-text', [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': ''}, {'type': 'input_text', 'text': ' \t'}]}]),
    ):
        body = {**valid, 'input': items}
        # 送信wireを明示し、Unicode表記を含めた本文の完全保持を直接・親フローで比較します。
        cases.append(('content-parts-' + label, {'body': body,
            'rawBody': json.dumps(body, ensure_ascii=False, separators=(',', ':'))}, 200, 1))
    for label, provider_part in (
        ('null', None), ('string', 'synthetic'), ('id-only', {'id': 'synthetic-saved-item'}),
        ('item-reference', {'type': 'item_reference', 'id': 'synthetic-saved-item'}),
        ('input-file', {'type': 'input_file', 'file_id': 'synthetic-saved-file'}),
        ('input-image', {'type': 'input_image', 'image_url': 'https://example.com/synthetic.png'}),
        ('extra-file-id', {'type': 'input_text', 'text': 'synthetic', 'file_id': 'synthetic-saved-file'}),
        ('type-missing', {'text': 'synthetic'}), ('type-null', {'type': None, 'text': 'synthetic'}),
        ('text-missing', {'type': 'input_text'}), ('text-null', {'type': 'input_text', 'text': None}),
        ('text-number', {'type': 'input_text', 'text': 1}),
    ):
        for position, content in (('first', [provider_part, *parts]), ('last', [*parts, provider_part])):
            cases.append(('content-parts-' + label + '-' + position,
                {'body': {**valid, 'input': [first, {'role': 'user', 'content': content}]}}, 200, 1))
    cases.extend([
        ('content-parts-middle-reference', {'body': {**valid, 'input': [first, {'role': 'user', 'content': [
            parts[0], {'type': 'input_text', 'text': 'synthetic', 'file_id': 'synthetic-saved-file'}, parts[1]]}]}}, 200, 1),
        ('content-parts-empty', {'body': {**valid, 'input': [first, {'role': 'user', 'content': []}]}}, 200, 1),
        ('content-parts-store-true', {'body': {**valid, 'input': [multipart], 'store': True}}, 200, 1),
        ('content-parts-state-reference', {'body': {**valid, 'input': [multipart],
            'previous_response_id': 'synthetic-saved-response'}}, 403, 1),
    ])
    # 空配列と後続の提供元入力を保持し、空contentだけで入力を補完・削除しません。
    empty_message = {'role': 'user', 'content': []}
    for label, items in (
        ('input-empty-array', []),
        ('empty-content-only', [empty_message]),
        ('empty-content-multiple', [empty_message, empty_message]),
        ('empty-content-before-text', [empty_message, first, second]),
        ('empty-content-between-text', [first, empty_message, second]),
        ('empty-content-around-unicode', [empty_message, {'role': 'user', 'content': [
            {'type': 'input_text', 'text': '合成入力\n第二段落'}]}, empty_message]),
    ):
        body = {**valid, 'input': items}
        cases.append((label, {'body': body,
            'rawBody': json.dumps(body, ensure_ascii=False, separators=(',', ':'))}, 200, 1))
    for label, provider_item in (
        ('null-message', None), ('string-message', 'synthetic'),
        ('id-only', {'id': 'synthetic-saved-item'}),
        ('item-reference', {'type': 'item_reference', 'id': 'synthetic-saved-item'}),
        ('message-id', {**first, 'id': 'synthetic-saved-item'}),
        ('assistant', {**first, 'role': 'assistant'}),
        ('content-missing', {'role': 'user'}),
        ('content-null', {'role': 'user', 'content': None}),
        ('content-object', {'role': 'user', 'content': {}}),
        ('input-file', {'role': 'user', 'content': [
            {'type': 'input_file', 'file_id': 'synthetic-saved-file'}]}),
        ('text-file-id', {'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'synthetic', 'file_id': 'synthetic-saved-file'}]}),
        ('null-part', {'role': 'user', 'content': [None]}),
        ('text-null', {'role': 'user', 'content': [{'type': 'input_text', 'text': None}]}),
        ('invalid-later-part', {'role': 'user', 'content': [*parts, None]}),
    ):
        cases.append(('after-empty-content-' + label,
            {'body': {**valid, 'input': [empty_message, provider_item]}}, 200, 1))
    for label, patch, reads in json_object_body_failure_cases():
        cases.append(('json-' + label, {'body': valid, **patch}, 400, reads))
    # metadataの省略・型・sample_modeはサンプル固有の受付条件にせず、値を保持します。
    for label, body in (
        ('metadata-missing', {key: value for key, value in valid.items() if key != 'metadata'}),
        ('metadata-null', {**valid, 'metadata': None}), ('metadata-array', {**valid, 'metadata': []}),
        ('metadata-empty', {**valid, 'metadata': {}}),
        ('metadata-unknown-mode', {**valid, 'metadata': {'sample_mode': 'synthetic'}}),
        ('metadata-extra', {**valid, 'metadata': {'sample_mode': 'fixed', 'extra': True}}),
    ):
        cases.append((label, {'body': body}, 200, 1))
    # 保存指定は値や型で拒否せず、親フローで全Hosted操作へBoolean falseを書き込みます。
    for label, body in (
        ('store-missing', {key: value for key, value in valid.items() if key != 'store'}),
        ('store-null', {**valid, 'store': None}), ('store-number', {**valid, 'store': 1}),
        ('store-true', {**valid, 'store': True}),
        ('store-string-true', {**valid, 'store': 'true'}),
        ('store-string-false', {**valid, 'store': 'false'}),
        ('store-array', {**valid, 'store': []}), ('store-object', {**valid, 'store': {}}),
    ):
        cases.append((label, {'body': body}, 200, 1))
    for label, body, status in (
        ('empty-object', {}, 200), ('unexpected-field', {**valid, 'provider_extension': True}, 200),
        ('state-reference', {**valid, 'previous_response_id': 'synthetic-response'}, 403),
        ('input-missing', {key: value for key, value in valid.items() if key != 'input'}, 200),
        ('input-null', {**valid, 'input': None}, 200),
        ('input-object', {**valid, 'input': {'text': 'synthetic'}}, 200),
        ('input-nonobject-item', {**valid, 'input': [None]}, 200),
        ('input-extra-content-field', {**valid, 'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'synthetic', 'file_id': 'synthetic-file'}]}]}, 200),
    ):
        cases.append((label, {'body': body}, status, 1))
    # 通常Responsesと同じ境界を使い、配列直下の明示Code Interpreterだけを拒否します。
    # 深いtype・非配列input・型違いのtypeや一般のIDを新しい拒否条件にしません。
    for label, fields, status in responses_code_interpreter_input_cases():
        cases.append(('code-interpreter-' + label, {'body': {**valid, **fields}}, status, 1))
    # 公式の直接履歴と完全Itemsを合成値で渡し、role・content・ID・functionの対応を保持します。
    transcript = [
        {'role': 'user', 'content': '合成 first question 🧪'},
        {'role': 'assistant', 'content': '合成 previous answer\n第二段落'},
        {'role': 'user', 'content': ' \t合成 follow-up '},
    ]
    reasoning = {'type': 'reasoning', 'id': 'synthetic-reasoning',
                 'encrypted_content': 'synthetic-encrypted',
                 'summary': [{'type': 'summary_text', 'text': '合成 reasoning'}]}
    function = {'type': 'function_call', 'id': 'synthetic-function', 'call_id': 'synthetic-call',
                'name': 'synthetic_tool', 'arguments': '{"query":"synthetic"}'}
    function_output = {'type': 'function_call_output', 'id': 'synthetic-output',
                       'call_id': 'synthetic-call', 'output': '合成 tool output'}
    citation_message = {'type': 'message', 'id': 'synthetic-answer', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': '合成 cited answer', 'annotations': [
                            {'type': 'file_citation', 'file_id': 'synthetic-citation-file',
                             'filename': 'synthetic.pdf', 'index': 0},
                            {'type': 'container_file_citation', 'file_id': 'synthetic-container-file',
                             'container_id': 'synthetic-container'},
                            {'type': 'file_path', 'file_id': 'synthetic-path-file'},
                        ]}]}
    for label, items in (
        ('official-multi-turn', transcript),
        ('complete-function-items', [transcript[0], reasoning, function, function_output, citation_message]),
        ('citation-annotations', [citation_message, transcript[-1]]),
        ('file-inputs', [{'role': 'user', 'content': [
            {'type': 'input_file', 'file_url': 'https://example.com/synthetic.pdf', 'file_id': None},
            {'type': 'input_file', 'file_id': 'synthetic-saved-file'},
        ]}]),
        ('number', 1), ('boolean', False),
        ('mixed-provider-types', [None, False, 1, 'synthetic', [], {'provider_extension': True}]),
    ):
        body = {**valid, 'input': items}
        cases.append(('input-' + label, {'body': body,
            'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    # 定義・状態選択の拒否は残し、conversation/backgroundは親の保存防止編集へ原値で渡します。
    for field, value, status in (
        ('conversation', None, 200), ('background', False, 200), ('tools', [], 403), ('instructions', None, 403),
        ('agent_reference', {'type': 'agent_reference', 'name': 'synthetic-agent'}, 403),
        ('agent_session_id', 'synthetic-session', 403), ('previous_response_id', None, 403),
    ):
        cases.append(('top-level-' + field, {'body': {**valid, 'input': None, field: value}}, status, 1))
    json_values = (
        ('null', None), ('false', False), ('true', True), ('integer', 1), ('zero', 0), ('number', 1.5),
        ('empty-string', ''), ('string', '合成 provider value'), ('empty-array', []), ('array', ['synthetic']),
        ('empty-object', {}), ('object', {'synthetic': True}),
    )
    # 各オプションは代表的な指定形とnullを渡し、型・値・一覧の受付を提供元へ任せます。
    for field, representative in (
        ('include', ['reasoning.encrypted_content']), ('text', {'format': {'type': 'text'}}),
        ('max_output_tokens', 64),
        ('tool_choice', {'type': 'allowed_tools', 'mode': 'auto',
                         'tools': [{'type': 'function', 'name': 'synthetic_tool'}]}),
        ('model', 'synthetic-new-deployment'), ('provider_extension', {'file_id': 'synthetic-file'}),
    ):
        for label, value in (('representative', representative), ('null', None)):
            body = {**valid, field: value}
            cases.append(('option-' + field + '-' + label, {'body': body,
                'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    # 型違いでcast例外へ落ちない対照はmodelへ集約し、登録表にない文字列とnullは上で確認します。
    for label, value in json_values:
        if label in ('null', 'string'):
            continue
        body = {**valid, 'model': value}
        cases.append(('option-model-' + label, {'body': body,
            'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    # 定義を変える正式形と、存在だけで拒否するnullを各項目で確認します。
    for field in ('instructions', 'tools', 'agent_reference', 'prompt'):
        cases.append(('definition-' + field + '-null', {'body': {**valid, field: None}}, 403, 1))
    for field, value in (
        ('instructions', 'Use different synthetic instructions'),
        ('tools', [{'type': 'function', 'name': 'synthetic_tool', 'parameters': {'type': 'object'}}]),
        ('agent_reference', {'type': 'agent_reference', 'name': 'synthetic-agent', 'version': '1'}),
        ('prompt', {'id': 'synthetic-prompt', 'variables': {'query': 'synthetic'}}),
    ):
        cases.append(('definition-' + field + '-selector-shape', {'body': {**valid, field: value}}, 403, 1))
    # 空文字列・型違いでも例外や通過へ変わらない対照はinstructionsへ集約します。
    for label, value in json_values:
        if label not in ('null', 'string'):
            cases.append(('definition-instructions-' + label,
                {'body': {**valid, 'instructions': value}}, 403, 1))
    # 保存済み応答／sandboxの正式な選択形とnullを拒否し、一般inputのID透過と区別します。
    for field, selector in (('previous_response_id', 'synthetic-response'), ('agent_session_id', 'synthetic-session')):
        for label, value in (('selector-shape', selector), ('null', None)):
            cases.append(('state-' + field + '-' + label, {'body': {**valid, field: value}}, 403, 1))
    # 状態選択の型違いはagent_session_idへ集約し、例外ではなく同じ403で先行停止させます。
    for label, value in json_values:
        if label not in ('null', 'string'):
            cases.append(('state-agent_session_id-' + label,
                {'body': {**valid, 'agent_session_id': value}}, 403, 1))
    # ここでは本文を編集せず、全型のconversation/backgroundを親のnull/false正規化へ渡します。
    for field in ('conversation', 'background'):
        for label, value in json_values:
            body = {**valid, field: value}
            cases.append(('storage-' + field + '-' + label, {'body': body,
                'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    for label, fields in (
        ('ordinary-options', {'include': ['synthetic.include'], 'text': {'format': {'type': 'text'}},
            'max_output_tokens': 0, 'tool_choice': {'type': 'code_interpreter'},
            'model': 'synthetic-new-deployment', 'provider_extension': {'file_id': 'synthetic-file'}}),
        ('storage-both', {'conversation': {'id': 'synthetic-conversation'}, 'background': True}),
        ('storage-null-and-object', {'conversation': None, 'background': {'provider_extension': True}}),
    ):
        body = {**valid, 'input': transcript, **fields}
        cases.append((label, {'body': body,
            'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    selectors = {'instructions': 'synthetic', 'tools': [{'type': 'code_interpreter'}],
                 'agent_reference': {'type': 'agent_reference', 'name': 'synthetic-agent'},
                 'prompt': {'id': 'synthetic-prompt'}, 'previous_response_id': 'synthetic-response',
                 'agent_session_id': 'synthetic-session', 'type': 'code_interpreter_call'}
    # 既知の項目名でも深いデータを再帰検査せず、input・metadata・提供元拡張をそのまま渡します。
    for label, fields in (
        ('input', {'input': [{'role': 'user', 'content': 'synthetic', 'provider_extension': selectors}]}),
        ('metadata', {'metadata': {'provider_extension': selectors}}),
        ('provider-extension', {'provider_extension': selectors}),
    ):
        body = {**valid, **fields}
        cases.append(('nested-selectors-' + label, {'body': body,
            'rawBody': ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n '}, 200, 1))
    # 通常項目や保存防止指定が同居しても、本文直下の定義・状態選択の拒否を回避させません。
    for field in ('instructions', 'tools', 'agent_reference', 'prompt', 'previous_response_id', 'agent_session_id'):
        cases.append(('selector-with-storage-' + field, {'body': {**valid, field: None,
            'conversation': None, 'background': False, 'include': ['synthetic.include']}}, 403, 1))
    cases.extend([
        ('different-method', {'body': valid, 'method': 'GET'}, 200, 1),
        ('different-method-invalid-json', {'body': valid, 'method': 'GET', 'rawBody': '{'}, 400, 1),
        ('different-path', {'body': valid, 'path': '/neighbor'}, 200, 1),
    ])
    return [(label, copy.deepcopy(patch), status, reads) for label, patch, status, reads in cases]


def dedicated_target_route_cases():
    """共通Targetの直接評価で、登録済み経路の再確認や専用入力の読取りを省く対照。"""
    # APIMの操作照合や、後続のSDP・チケット・Document・PII専用処理はこの直接評価に含めません。
    routes = (
        ('liveCreate', 'live-create', 'POST', '/sessions'),
        ('realtimeCreate', 'realtime-create', 'POST', '/calls'),
        ('realtimeHangup', 'realtime-hangup', 'POST', '/calls/{callId}/hangup'),
        ('documentAnalysis', 'analyze_document', 'POST', '/analyze'),
        ('documentAnalysisStatus', 'get_document_operation', 'GET', '/operations/{operationId}'),
        ('pii', 'pii_redact_text', 'POST', '/redact'),
    )
    cases = []
    for kind, operation, method, path in routes:
        base = {'operation': operation, 'method': method, 'path': path, 'body': {'synthetic': True}}
        for label, patch in (
            ('registered', {}), ('different-method', {'method': 'DELETE'}),
            ('different-path', {'path': '/neighbor'}),
            ('different-operation', {'operation': 'synthetic-other-operation'}),
            ('malformed-body', {'rawBody': '{'}), ('reader-exception', {'bodyReadUnavailable': True}),
            ('body-absent', {'bodyAbsent': True}),
        ):
            cases.append((kind, label, {**base, **patch}))
    return [(kind, label, copy.deepcopy(patch)) for kind, label, patch in cases]


def responses_item_reference_cases():
    """明示参照・完全Items・深いデータを、すべて元の順序と値で渡す合成例。"""
    reference = {'type': 'item_reference', 'id': 'synthetic-reference'}
    message = {'type': 'message', 'id': 'synthetic-message', 'role': 'assistant', 'phase': 'commentary',
               'content': [{'type': 'output_text', 'text': 'synthetic text'}]}
    final_message = {**message, 'id': 'synthetic-final-message', 'phase': 'final_answer'}
    reasoning = {'type': 'reasoning', 'id': 'synthetic-reasoning', 'encrypted_content': 'synthetic-encrypted',
                 'summary': [{'type': 'summary_text', 'text': 'synthetic summary'}]}
    call = {'type': 'function_call', 'id': 'synthetic-function', 'call_id': 'synthetic-call',
            'name': 'synthetic_tool', 'arguments': '{}'}
    output = {'type': 'function_call_output', 'id': 'synthetic-output', 'call_id': 'synthetic-call',
              'output': 'synthetic output'}
    nested = {**message, 'provider_extension': {'type': 'item_reference', 'id': 'synthetic-nested-reference'}}
    omitted = {'id': 'synthetic-type-omitted'}
    other_types = [{'type': None, 'id': 'synthetic-null-type'}, {'type': 1, 'id': 'synthetic-number-type'},
                   {'type': ['item_reference'], 'id': 'synthetic-array-type'}]
    different_case = {'type': 'Item_Reference', 'id': 'synthetic-different-case'}
    cases = [
        ('mixed', [reference, message, reference, reasoning, call, output, reference, final_message]),
        ('first', [reference, message]), ('middle', [message, reference, reasoning]),
        ('last', [message, reference]), ('consecutive', [message, reference, reference, reasoning]),
        ('all', [reference, reference]), ('empty', []), ('nested', [reference, nested]),
        ('type-omitted', [reference, omitted]), ('other-types', [reference, *other_types]),
        ('different-case', [reference, different_case]),
        ('non-object-items', [reference, None, 1, 'synthetic']),
        ('non-array-object', reference), ('non-array-string', 'synthetic'),
        ('non-array-null', None), ('non-array-number', 1),
    ]
    return [(label, copy.deepcopy(original), copy.deepcopy(original)) for label, original in cases]


def responses_untyped_item_reference_cases():
    """type欠落/nullを含むid・本文・call_id・深い同名項目の原値保持を対照する。"""
    cases = []
    id_values = [('string', 'synthetic-id'), ('empty', ''), ('whitespace', ' \t'), ('null', None),
                 ('number', 1), ('boolean', True), ('array', ['synthetic-id']), ('object', {'id': 'synthetic-id'})]
    for type_label, type_fields in (('missing', {}), ('null', {'type': None})):
        for id_label, id_value in id_values:
            item = {'id': id_value, **type_fields}
            cases.append((type_label + '-id-only-' + id_label, [item]))
            content = {'content': 'synthetic body', 'vendor_extension': {'id': 'synthetic-deep-id', 'type': 'item_reference'}}
            cases.append((type_label + '-with-content-' + id_label, [{**item, **content}]))
    untyped_items = [
        {'id': 'synthetic-message', 'role': 'assistant', 'phase': 'final_answer',
         'content': [{'type': 'output_text', 'text': 'synthetic'}]},
        {'id': 'synthetic-reasoning', 'encrypted_content': 'synthetic-encrypted', 'summary': []},
        {'id': 'synthetic-function', 'call_id': 'synthetic-call', 'name': 'synthetic_tool', 'arguments': '{}'},
        {'id': 'synthetic-output', 'call_id': 'synthetic-call', 'output': 'synthetic output'},
    ]
    typed_message = {'type': 'message', 'id': 'synthetic-typed-message', 'role': 'assistant',
                     'phase': 'commentary', 'content': [{'type': 'output_text', 'text': 'synthetic'}]}
    deep = {**typed_message, 'provider_extension': [{'id': 'synthetic-deep-missing'},
                                                 {'id': 'synthetic-deep-null', 'type': None}]}
    reference = {'id': 'synthetic-reference'}
    null_reference = {'id': None, 'type': None}
    explicit = {'id': 'synthetic-explicit-reference', 'type': 'item_reference'}
    other_types = [{'id': 'synthetic-empty-type', 'type': ''}, {'id': 'synthetic-bool-type', 'type': False},
                   {'id': 'synthetic-object-type', 'type': {'vendor': 'item_reference'}}]
    no_id = [{'content': 'synthetic'}, {'type': None, 'content': 'synthetic'}]
    cases.extend([
        ('untyped-content-items', untyped_items),
        ('null-type-content-items', [{'type': None, **item} for item in untyped_items]),
        ('unknown-property', [{'id': 'synthetic-outer', 'vendor': {'id': 'synthetic-deep', 'type': None}}]),
        ('null-other-properties', [{'id': 'synthetic-id', 'content': None},
                                   {'id': 'synthetic-id', 'type': None, 'content': None},
                                   {'id': 'synthetic-id', 'vendor': None},
                                   {'id': 'synthetic-id', 'type': None, 'vendor': None}]),
        ('case-sensitive-id', [{'ID': 'synthetic-upper'}, {'id': 'synthetic-lower', 'ID': 'synthetic-upper'}]),
        ('no-id', no_id),
        ('adjacent-mixed', [explicit, reference, null_reference, typed_message, untyped_items[0], null_reference, deep]),
        ('all-references', [explicit, reference, null_reference]),
        ('other-typed', other_types), ('non-array-untyped', reference),
        ('non-array-null-type', {'id': 'synthetic-id', 'type': None, 'content': 'synthetic'}),
    ])
    return [(label, copy.deepcopy(original), copy.deepcopy(original)) for label, original in cases]


def responses_file_id_null_cases():
    """ファイル参照・引用・深い拡張とincludeを保持し、toolsの別条件と対照する。"""
    document = {'type': 'input_file', 'file_id': None, 'filename': 'synthetic.txt', 'file_data': 'c3ludGhldGlj'}
    image = {'type': 'input_image', 'file_id': None, 'image_url': 'data:image/png;base64,AA=='}
    message = {'type': 'message', 'id': 'synthetic-message', 'role': 'user', 'content': 'synthetic'}
    unknown = {**message, 'provider_extension': {'nested': [{'file_id': None, 'value': 'synthetic'}]}}
    citation = {'type': 'message', 'id': 'synthetic-citation', 'role': 'assistant', 'content': [
        {'type': 'output_text', 'text': 'synthetic', 'annotations': [
            {'type': 'file_citation', 'file_id': None, 'index': 0}]}]}
    call = {'type': 'function_call_output', 'id': 'synthetic-output', 'call_id': 'synthetic-call',
            'output': {'file_id': None, 'value': 'synthetic'}}
    cases = [
        ('direct-file-null', {'input': [document]}, 200),
        ('direct-image-null', {'input': [image]}, 200),
        ('nested-file-null', {'input': [{**message, 'content': [document]}]}, 200),
        ('nested-image-null', {'input': [{**message, 'content': [image]}]}, 200),
        ('deep-unknown-null', {'input': [unknown]}, 200),
        ('citation-null', {'input': [citation]}, 200),
        ('multiple-null-values', {'input': [document, image, unknown, citation, call]}, 200),
        ('case-sensitive-file-id', {'input': [{**document, 'FILE_ID': 'synthetic-upper'}]}, 200),
        ('missing-file-id', {'input': [{'type': 'input_file', 'file_data': 'c3ludGhldGlj'}]}, 200),
        ('non-array-null', {'input': document}, 200),
        ('non-array-nonnull', {'input': {**document, 'file_id': 'synthetic-file'}}, 200),
        ('non-array-other-references', {'input': {'file_id': None, 'file_ids': None, 'vector_store_ids': []}}, 200),
        ('top-level-null', {'input': [message], 'file_id': None}, 200),
        ('top-level-nonnull', {'input': [message], 'file_id': 'synthetic-file'}, 200),
        ('other-body-object', {'input': [message], 'metadata': {'file_id': 'synthetic-file'}}, 200),
        ('other-body-array', {'input': [message], 'provider_extension': [{'file_id': 'synthetic-file'}]}, 200),
        ('null-with-item-references', {'input': [document, {'type': 'item_reference', 'id': 'synthetic-reference'},
                                               {'id': 'synthetic-reference'}, {'id': 'synthetic-content', 'content': 'synthetic'}]}, 200),
    ]
    for label, value in (('string', 'synthetic-file'), ('empty', ''), ('whitespace', ' \t'), ('number', 0),
                         ('boolean', False), ('empty-array', []), ('empty-object', {}),
                         ('null-array', [None]), ('null-object', {'child': None})):
        cases.append(('nonnull-' + label, {'input': [{**document, 'file_id': value}]}, 200))
    cases.extend([
        ('mixed-nonnull-sibling', {'input': [document, image, {'file_id': 'synthetic-file'}]}, 200),
        ('mixed-nonnull-deep', {'input': [document, {**unknown, 'extra': [{'nested': {'file_id': False}}]}]}, 200),
    ])
    for field in ('file_ids', 'vector_store_ids'):
        for label, value in (('null', None), ('empty-array', []), ('array', ['synthetic-reference'])):
            cases.append((field + '-' + label, {'input': [{**document, field: value}]}, 200))
    cases.extend([
        ('deep-file-ids-null', {'input': [{**unknown, 'extra': [{'file_ids': None}]}]}, 200),
        ('deep-vector-store-ids-empty', {'input': [{**unknown, 'extra': {'vector_store_ids': []}}]}, 200),
        ('null-with-forbidden-tools', {'input': [document], 'tools': [{'type': 'web_search'}]}, 403),
        ('null-with-additional-include', {'input': [document], 'include': ['file_search_call.results']}, 200),
        ('null-with-allowed-tools', {'input': [document], 'tools': [{'type': 'function', 'name': 'synthetic_tool'}],
                                    'tool_choice': {'type': 'function', 'name': 'synthetic_tool'},
                                    'include': ['reasoning.encrypted_content', 'message.output_text.logprobs']}, 200),
    ])
    for citation_type in ('file_citation', 'container_file_citation', 'file_path', 'synthetic_extension'):
        annotated = copy.deepcopy(citation)
        annotated['content'][0]['annotations'][0].update(type=citation_type, file_id='synthetic-file')
        cases.append(('citation-'+citation_type, {'input':[annotated]}, 200))
    return [(label, copy.deepcopy(fields), status, copy.deepcopy(fields))
            for label, fields, status in cases]


CSHARP = r'''
using System;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body { public string Data; public int Reads; public bool Unavailable; public bool ReturnsNull; public T As<T>(bool preserveContent=false) { Reads++; if (Unavailable) throw new Exception("Synthetic body read unavailable"); return ReturnsNull ? default(T) : (T)(object)JObject.Parse(Data); } }
public class Url { public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }
public class Request { public string Method="POST"; public Body Body; public Url Url=new Url(); public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>(); }
public class Named { public string Id; public string UrlTemplate="/responses"; }
public class Context { public Guid RequestId=Guid.Parse("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"); public Request Request=new Request(); public Named Operation=new Named {Id="agent-responses"}; public Named Api=new Named {Id="gui-added-api"}; public Named Subscription; public Named Product; public Dictionary<string,object> Variables=new Dictionary<string,object>(); }
public static class Extensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> d,string key,string fallback) { return d.ContainsKey(key)? String.Join(" ",d[key]):fallback; }
 public static string GetValueOrDefault(this Dictionary<string,string> d,string key,string fallback) { return d.ContainsKey(key)?d[key]:fallback; }
}
public static class ExpandedPolicyFixture {
 public static string TargetDecision(Context context) { __TARGET__ }
 public static bool Target(Context context) { return (bool)JObject.Parse(TargetDecision(context))["allowed"]; }
 public static bool Ingress(Context context) { __INGRESS__ }
 public static bool Query(Context context) { __QUERY__ }
 public static bool AuthenticationMissing(Context context) { __AUTH_GUARD__ }
 public static string Auth(Context context) { __AUTH__ }
 public static bool TargetRejected(Context context) { return __TARGET_REJECTION_CONDITION__; }
 public static JObject TargetRejection(Context context) {
  // 呼出元XMLの実式を実際の順序で評価します。APIM応答engineやログ到達は再現しません。
  __TARGET_AUDIT_ASSIGNMENTS__
  return new JObject(new JProperty("phase", context.Variables["gatewayAuditPhase"]),
   new JProperty("code", context.Variables["gatewayAuditCode"]),
   new JProperty("auditStatus", context.Variables["gatewayAuditStatus"]),
   new JProperty("status", __TARGET_RESPONSE_STATUS__), new JProperty("reason", __TARGET_RESPONSE_REASON__),
   new JProperty("requestIdHeader", __TARGET_RESPONSE_REQUEST_ID__));
 }
 public static string Run(string input) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var c=new Context(); c.Request.Body=new Body {Data=item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None)};
   c.Request.Body.Unavailable=(bool?)item["bodyReadUnavailable"]==true;
   c.Request.Body.ReturnsNull=(bool?)item["bodyReadReturnsNull"]==true;
   if((bool?)item["bodyAbsent"]==true)c.Request.Body=null;
   if(item["method"]!=null)c.Request.Method=(string)item["method"];
   if(item["path"]!=null)c.Operation.UrlTemplate=(string)item["path"];
   if(item["operation"]!=null)c.Operation.Id=(string)item["operation"];
   c.Variables["config"]=(item["config"]??new JObject()).ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"]="Ocp-Apim-Subscription-Key";
   foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties()) c.Request.Headers[entry.Name]=entry.Value is JArray ? entry.Value.Select(x=>(string)x).ToArray():new [] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties()) c.Request.Url.Query[entry.Name]=entry.Value is JArray ? entry.Value.Select(x=>(string)x).ToArray():new [] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["parameters"]??new JObject())).Properties()) c.Request.MatchedParameters[entry.Name]=(string)entry.Value;
   if(item["key"]!=null) { c.Subscription=new Named {Id=(string)item["key"]};c.Product=new Named {Id="tool-users"};c.Request.Headers["Ocp-Apim-Subscription-Key"]=new [] {"fixture-not-a-secret"}; }
   if(item["authenticated"]!=null)c.Variables["gatewayCommonAuthenticationValidated"]=(bool)item["authenticated"];
   if(item["api"]!=null)c.Api.Id=(string)item["api"];
   if(item["grpc"]!=null)c.Variables["coreNativeGrpc"]=(bool)item["grpc"];
   string mode=(string)item["mode"];
   // 固定queryの試験にも、先行モデル準備が返す空文字を渡します。
   if(mode=="query")c.Variables["gatewayProtocolModel"]="";
   if(mode!="ingress" && mode!="query" && mode!="auth") {
    // HTTP入口の選択処理と同様に、Targetへは準備済みの操作設定JSONを渡します。
    c.Variables["gatewayOperationConstraint"]=item["config"]["operationConstraints"][c.Operation.Id].ToString(Formatting.None);
    if((bool?)item["constraintMissing"]==true)c.Variables.Remove("gatewayOperationConstraint");
    if(item.Property("constraintRaw")!=null)c.Variables["gatewayOperationConstraint"]=((JValue)item["constraintRaw"]).Value;
   }
   if(item.Property("effectiveModel")!=null)c.Variables["gatewayEffectiveModel"]=((JValue)item["effectiveModel"]).Value;
   JObject rejection=null;
   JToken result;
   if(mode=="target-decision") {
    var raw=TargetDecision(c);c.Variables["gatewayTargetDecision"]=raw;result=JObject.Parse(raw);
    if(TargetRejected(c))rejection=TargetRejection(c);
   } else {
    result=mode=="ingress"?(JToken)new JValue(Ingress(c)):mode=="query"?new JValue(Query(c)):mode=="auth"?(AuthenticationMissing(c)?new JObject(new JProperty("allowed",false)):JObject.Parse(Auth(c))):new JValue(Target(c));
   }
   var observation=new JObject(new JProperty("result",result),new JProperty("reads",c.Request.Body==null ? 0 : c.Request.Body.Reads),
    new JProperty("bodyAfter", c.Request.Body==null ? null : c.Request.Body.Data));
   if(rejection!=null)observation["rejection"]=rejection;
   results[(string)item["id"]]=observation;
  }
  return results.ToString(Formatting.None);
 }
}
'''


class ExpandedPolicyTests(unittest.TestCase):
    """モデル・音声・ファイル・WebSocket/MCPのAPI設定とAPIM式を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH: raise unittest.SkipTest("C# runtime unavailable; no live checks were performed")
        hosted = {"operationConstraints": {"agent-responses": {"kind": "hostedAgentPost"}}}
        cases = []
        cls.hosted_target_cases = {}
        for label, patch, status, reads in hosted_agent_target_cases():
            identifier = 'target-hosted-' + label
            original = None if patch.get('bodyAbsent') else patch.get('rawBody', json.dumps(patch['body'], separators=(',', ':')))
            cls.hosted_target_cases[identifier] = (original, status, reads)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': hosted, **patch})
        for label, patch in (('missing-settings', {'constraintMissing': True}),
                             ('malformed-settings', {'constraintRaw': '{'})):
            identifier = 'target-hosted-' + label
            body = {'input': 'synthetic', 'store': False, 'metadata': {'sample_mode': 'fixed'}}
            cls.hosted_target_cases[identifier] = (json.dumps(body), 500, 0)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': hosted, 'body': body, **patch})
        cls.dedicated_target_cases = {}
        for kind, label, patch in dedicated_target_route_cases():
            identifier = 'target-dedicated-' + kind + '-' + label
            original = None if patch.get('bodyAbsent') else patch.get('rawBody', json.dumps(patch['body'], separators=(',', ':')))
            cls.dedicated_target_cases[identifier] = original
            cases.append({'id': identifier, 'mode': 'target-decision',
                          'config': {'operationConstraints': {patch['operation']: {'kind': kind}}}, **patch})
        for name, headers, api in (("missing", {}, "gui-added-api"), ("spoof", {"Host": "trusted.example.com", "X-Core-L7-Ingress": "invented"}, "gui-added-api"),
                                   ("grpc-header", {"Content-Type": "application/grpc", "coreNativeGrpc": "true"}, "gui-added-api"),
                                   ("valid", {"X-Core-L7-Ingress": "fixture-ingress-proof"}, "gui-added-api"),
                                   ("duplicate", {"X-Core-L7-Ingress": ["fixture-ingress-proof", "fixture-ingress-proof"]}, "gui-added-api"), ("grpc", {}, "grpc-api")):
            cases.append({"id": "ingress-" + name, "mode": "ingress", "headers": headers, "api": api, "grpc": name == "grpc"})
        for name, query in (("missing", {}), ("fixed", {"model": "approved", "api-version": "2026-01-01"}),
                             ("target", {"model": "unapproved"}), ("agent", {"agent_id": "other"}),
                             ("duplicate", {"model": ["approved", "unapproved"]})):
            cases.append({"id": "query-" + name, "mode": "query", "query": query})
        responses = {"operationConstraints": {"agent-responses": {"kind": "statelessResponses", "allowedModels": ["approved"]}}}
        target = {"operationConstraints": {"agent-responses": {"kind": "bodyModel", "allowedModels": ["approved"]}}}
        for name, patch in (
            ("valid", {}), ("unregistered-model", {"body": {"model": "unregistered-synthetic-model"}}),
            ("malformed-body", {"rawBody": "{synthetic malformed JSON"}),
            ("missing-settings", {"constraintMissing": True}), ("malformed-settings", {"constraintRaw": "{"}),
        ):
            cases.append({"id": "target-decision-" + name, "mode": "target-decision", "config": target,
                          "body": {"model": "approved"}, **patch})
        # genericは本文を読まず、Responsesはmodelを再確認せず残る本文条件だけを検査します。
        cls.http_model_cases = {}
        cls.target_preserved_bodies = {}
        for kind in ("bodyModel", "textEmbeddings", "rerank", "statelessResponses"):
            constraint = {"kind": kind, "allowedModels": ["approved"]}
            responses_body = kind == "statelessResponses"
            for label, invalid_body, patch in (
                ("registered", False, {}),
                ("unregistered", False, {"body": {"model": "unregistered-synthetic-model", "input": "synthetic"}}),
                ("missing-model", False, {"body": {"input": "synthetic"}}),
                ("null-model", False, {"body": {"model": None, "input": "synthetic"}}),
                ("numeric-model", False, {"body": {"model": 1, "input": "synthetic"}}),
                ("array-model", False, {"body": {"model": ["approved"], "input": "synthetic"}}),
                ("object-model", False, {"body": {"model": {"name": "approved"}, "input": "synthetic"}}),
                ("malformed-body", True, {"rawBody": "{synthetic malformed JSON"}),
                ("array-body", True, {"rawBody": "[]"}),
                ("scalar-body", True, {"rawBody": '"synthetic"'}),
                ("json-null-body", True, {"rawBody": "null"}),
                ("body-read-unavailable", True, {"bodyReadUnavailable": True}),
                ("body-read-null", True, {"bodyReadReturnsNull": True}),
                ("body-absent", True, {"bodyAbsent": True}),
                ("unused-effective-model", False, {"effectiveModel": 1}),
            ):
                identifier = "target-http-" + kind + "-" + label
                status = 400 if responses_body and invalid_body else 200
                reads = 1 if responses_body and label != "body-absent" else 0
                cls.http_model_cases[identifier] = (status, reads)
                cases.append({"id": identifier, "mode": "target-decision",
                              "config": {"operationConstraints": {"agent-responses": constraint}},
                              "body": {"model": "approved", "input": "synthetic"}, **patch})
            for label, catalog in (("missing-catalog", None), ("null-catalog", None), ("object-catalog", {})):
                configured = copy.deepcopy(constraint)
                if label == "missing-catalog":
                    configured.pop("allowedModels")
                else:
                    configured["allowedModels"] = catalog
                identifier = "target-http-" + kind + "-" + label
                cls.http_model_cases[identifier] = (200, 1 if responses_body else 0)
                cases.append({"id": identifier, "mode": "target-decision",
                              "config": {"operationConstraints": {"agent-responses": configured}},
                              "body": {"model": "unregistered-synthetic-model", "input": "synthetic"}})
        identifier = "target-http-statelessResponses-unregistered-forbidden-tools"
        cls.http_model_cases[identifier] = (403, 1)
        cases.append({"id": identifier, "mode": "target-decision", "config": responses,
                      "body": {"model": "unregistered-synthetic-model", "input": "synthetic", "tools": [{"type": "web_search"}]}})
        for field in ('previous_response_id', 'conversation', 'conversation_id', 'prompt'):
            for label, present, value in (("absent", False, None), ("string", True, "synthetic-reference"),
                                         ("empty-string", True, ""), ("null", True, None),
                                         ("number", True, 1), ("boolean", True, True),
                                         ("array", True, ["synthetic-reference"]),
                                         ("object", True, {"id": "synthetic-reference"})):
                identifier = "target-http-statelessResponses-" + field + "-" + label
                cls.http_model_cases[identifier] = (200, 1)
                body = {'input': 'synthetic', **({field: value} if present else {})}
                cases.append({"id": identifier, "mode": "target-decision", "config": responses, "body": body})
        identifier = 'target-http-statelessResponses-prompt-saved-file-variables'
        cls.http_model_cases[identifier] = (200, 1)
        cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                      'body': {'input': 'synthetic', 'prompt': {'id': 'synthetic-template', 'version': '1',
                               'variables': {'document': {'type': 'input_file', 'file_id': 'synthetic-file'},
                                             'label': 'synthetic'}}}})
        for label, present, value in (('absent', False, None), ('true', True, True), ('false', True, False),
                                     ('null', True, None), ('string', True, 'synthetic'), ('number', True, 1),
                                     ('array', True, [True]), ('object', True, {'enabled': True})):
            identifier = 'target-http-statelessResponses-background-' + label
            cls.http_model_cases[identifier] = (200, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': {'input': 'synthetic', **({'background': value} if present else {})}})
        for label, present, value in (
            ('absent', False, None), ('null', True, None), ('number', True, 1), ('boolean', True, True),
            ('object', True, {'text': 'synthetic'}),
            ('object-saved-file', True, {'type': 'input_file', 'file_id': 'synthetic-file'}),
            ('string', True, 'synthetic'), ('empty-string', True, ''),
            ('array', True, [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic'}]}]),
            ('empty-array', True, []),
        ):
            identifier = 'target-http-statelessResponses-input-' + label
            cls.http_model_cases[identifier] = (200, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': {'model': 'approved', **({'input': value} if present else {})}})
        for label, item in (('nested-file-id', {'content': [{'type': 'input_file', 'file_id': 'synthetic-file'}]}),
                            ('file-id', {'type': 'input_file', 'file_id': 'synthetic-file'}),
                            ('file-ids', {'file_ids': ['synthetic-file']}),
                            ('vector-store-ids', {'vector_store_ids': ['synthetic-vector-store']})):
            identifier = 'target-http-statelessResponses-input-file-preserved-' + label
            cls.http_model_cases[identifier] = (200, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': {'model': 'approved', 'input': [item]}})
        for label, original, _expected in responses_item_reference_cases():
            identifier = 'target-http-statelessResponses-item-reference-' + label
            cls.http_model_cases[identifier] = (200, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': {'model': 'approved', 'input': original}})
        for label, original, _expected in responses_untyped_item_reference_cases():
            identifier = 'target-http-statelessResponses-untyped-item-reference-' + label
            cls.http_model_cases[identifier] = (200, 1)
            cls.target_preserved_bodies[identifier] = {'model': 'approved', 'input': original}
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': cls.target_preserved_bodies[identifier]})
        for label, fields, status, _expected in responses_file_id_null_cases():
            identifier = 'target-http-statelessResponses-file-id-null-' + label
            cls.http_model_cases[identifier] = (status, 1)
            cls.target_preserved_bodies[identifier] = {'model': 'approved', **fields}
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses,
                          'body': cls.target_preserved_bodies[identifier]})
        cls.responses_code_interpreter_cases = {}
        for label, fields, status in responses_code_interpreter_input_cases():
            identifier = 'target-http-statelessResponses-code-interpreter-' + label
            body = {'model': 'approved', 'previous_response_id': 'synthetic-response',
                    'prompt': {'id': 'synthetic-template'}, **fields}
            cls.responses_code_interpreter_cases[identifier] = (body, status, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses, 'body': body})
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            identifier = 'target-http-' + kind + '-code-interpreter-scope'
            body = {'model': 'approved', 'input': [{'type': 'code_interpreter_call',
                    'id': 'synthetic-code-call', 'container_id': 'synthetic-container'}]}
            cls.responses_code_interpreter_cases[identifier] = (body, 200, 0)
            cases.append({'id': identifier, 'mode': 'target-decision',
                          'config': {'operationConstraints': {'agent-responses': {'kind': kind}}}, 'body': body})
        cls.responses_include_cases = {}
        for label, fields, status in responses_include_cases():
            identifier = 'target-http-statelessResponses-include-' + label
            body = {'model': 'approved', 'input': 'synthetic', 'previous_response_id': 'synthetic-response',
                    'prompt': {'id': 'synthetic-template'}, **fields}
            cls.responses_include_cases[identifier] = (body, status, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses, 'body': body})
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            identifier = 'target-http-' + kind + '-include-scope'
            body = {'model': 'approved', 'input': 'synthetic', 'include': [None, 'synthetic.include']}
            cls.responses_include_cases[identifier] = (body, 200, 0)
            cases.append({'id': identifier, 'mode': 'target-decision',
                          'config': {'operationConstraints': {'agent-responses': {'kind': kind}}}, 'body': body})
        cls.responses_tool_choice_cases = {}
        for label, fields, status in responses_tool_choice_cases():
            identifier = 'target-http-statelessResponses-tool-choice-' + label
            body = {'model': 'approved', 'input': 'synthetic', 'include': ['synthetic.include'],
                    'previous_response_id': 'synthetic-response', 'prompt': {'id': 'synthetic-template'}, **fields}
            cls.responses_tool_choice_cases[identifier] = (body, status, 1)
            cases.append({'id': identifier, 'mode': 'target-decision', 'config': responses, 'body': body})
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            identifier = 'target-http-' + kind + '-tool-choice-scope'
            body = {'model': 'approved', 'input': 'synthetic',
                    'tool_choice': {'type': 'allowed_tools', 'tools': [{'type': 'mcp'}]}}
            cls.responses_tool_choice_cases[identifier] = (body, 200, 0)
            cases.append({'id': identifier, 'mode': 'target-decision',
                          'config': {'operationConstraints': {'agent-responses': {'kind': kind}}}, 'body': body})
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            identifier = 'target-http-' + kind + '-file-id-scope'
            cls.http_model_cases[identifier] = (200, 0)
            cls.target_preserved_bodies[identifier] = {'model': 'approved',
                'input': [{'file_id': 'synthetic-file', 'file_ids': None, 'vector_store_ids': []}]}
            cases.append({'id': identifier, 'mode': 'target-decision',
                          'config': {'operationConstraints': {'agent-responses': {'kind': kind}}},
                          'body': cls.target_preserved_bodies[identifier]})
        # APIMの操作照合を通さない直接評価でも、経路ではなく残る本文条件だけを判定します。
        # 正式なPOST /responses登録の境界はConfigurationTestsで別に検証します。
        for route_label, route_patch in (("different-method", {"method": "GET"}),
                                         ("different-path", {"path": "/neighbor"})):
            for body_label, status, body_patch in (
                ("valid", 200, {}),
                ("invalid-json", 400, {"rawBody": "{"}),
                ("saved-file-reference", 200, {"body": {"input": [{"type": "input_file", "file_id": "synthetic-file"}]}}),
            ):
                identifier = "target-http-statelessResponses-" + route_label + "-" + body_label
                cls.http_model_cases[identifier] = (status, 1)
                cases.append({"id": identifier, "mode": "target-decision", "config": responses,
                              "body": {"input": "synthetic"}, **route_patch, **body_patch})
        cls.a2a_preserved_bodies = {}
        for label, patch, status, reads in a2a_json_rpc_target_cases():
            identifier = "target-a2a-" + label
            cls.http_model_cases[identifier] = (status, reads)
            cls.a2a_preserved_bodies[identifier] = (None if patch.get('bodyAbsent') else
                patch.get('rawBody', json.dumps(patch['body'], separators=(',', ':'))))
            cases.append({"id": identifier, "mode": "target-decision",
                          "config": {"operationConstraints": {"agent-responses": {
                              "kind": "a2aJsonRpc", "allowedMethods": ["message/send"]}}}, **patch})
        for name, parameter, parameters in (
            ("path-registered", "deployment", {"deployment": "approved"}),
            ("path-unregistered", "deployment", {"deployment": "future-synthetic-model"}),
            ("path-missing", "deployment", {}), ("path-empty", "deployment", {"deployment": ""}),
            ("path-whitespace", "deployment", {"deployment": " \t"}),
            ("path-dot", "deployment", {"deployment": "."}), ("path-parent", "deployment", {"deployment": ".."}),
            ("path-generic-parameter", "modelName", {"modelName": "future/model?version=x%2F"}),
        ):
            cases.append({"id": "target-decision-" + name, "mode": "target-decision", "parameters": parameters,
                          "config": {"operationConstraints": {"agent-responses": {"kind": "pathModel",
                                     "parameter": parameter, "allowedModels": ["approved"]}}}})
        for name, patch in (("optional", {"client_metadata": {"client": "synthetic"}, "metadata": {"purpose": "synthetic"}, "prompt_cache_key": "synthetic", "vendor_extension": True}),
                            ("previous", {"previous_response_id": "other"}), ("conversation", {"conversation": "other"}),
                            ("conversation-id", {"conversation_id": "synthetic-conversation"}),
                            ("prompt", {"prompt": {"id": "other"}}), ("background", {"background": True}),
                            ("file-reference", {"input": [{"type": "input_file", "file_id": "other"}]})):
            cases.append({"id": "responses-" + name, "mode": "target-decision", "config": responses,
                          "body": {"model": "approved", "input": "synthetic", **patch}})
        auth = {"mode": "subscriptionKey"}
        cases.append({"id": "auth-valid", "mode": "auth", "config": auth, "key": "approved-key", "authenticated": True})
        cases.append({"id": "auth-deny-missing", "mode": "auth", "config": auth})
        cases.append({"id": "auth-deny-empty", "mode": "auth", "config": auth, "key": ""})
        ingress = expression(SERVICE_POLICY, "./inbound/choose/when", "condition").replace("{{core-l7-ingress-enabled}}", "true").replace("{{core-l7-ingress-secret}}", "fixture-ingress-proof")
        protocol = expand_api_request_preparation_fragment((POLICIES / "api-templates" / "shared/stream.xml").read_text(encoding="utf-8"))
        guard_condition = ET.fromstring(protocol).find("./inbound/choose/when").get("condition")
        auth_guard = "return " + guard_condition[2:-1] + ";"
        query = actual_expression("stream.xml", "gatewayProtocolQueryAllowed").replace("__WEBSOCKET_QUERY_BASE64__", base64.b64encode(b'{"model":"approved","api-version":"2026-01-01"}').decode())
        source = CSHARP.replace("__TARGET__", actual_expression("rest.xml", "gatewayTargetDecision")).replace("__INGRESS__", ingress).replace("__QUERY__", query).replace("__AUTH_GUARD__", auth_guard).replace("__AUTH__", actual_expression("stream.xml", "gatewayDecision"))
        cls.target_rejection = ET.parse(policy_fixture_path(POLICIES, "rest.xml")).find(
            "./inbound/choose[@id='core-api-target-validation-selection']/when/choose/when")
        audit_nodes = cls.target_rejection.findall("./set-variable")
        assignments = "\n".join(f'context.Variables[{json.dumps(node.get("name"))}] = {single_expression(node.get("value"))};'
                                for node in audit_nodes)
        for marker, value in {
            "__TARGET_AUDIT_ASSIGNMENTS__": assignments,
            "__TARGET_REJECTION_CONDITION__": single_expression(cls.target_rejection.get("condition")),
            "__TARGET_RESPONSE_STATUS__": single_expression(cls.target_rejection.find("./return-response/set-status").get("code")),
            "__TARGET_RESPONSE_REASON__": single_expression(cls.target_rejection.find("./return-response/set-status").get("reason")),
            "__TARGET_RESPONSE_REQUEST_ID__": single_expression(cls.target_rejection.find("./return-response/set-header[@name='apim-request-id']/value").text),
        }.items():
            source = source.replace(marker, value)
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
        for identifier, (original, status, reads) in self.hosted_target_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                code = ('TARGET_ALLOWED' if status == 200 else 'INVALID_REQUEST_BODY' if status == 400
                        else 'OPERATION_SETTINGS_INVALID' if status == 500 else 'AUTHORIZATION_DENIED')
                self.assertEqual(observation['result'], {'allowed': status == 200, 'status': status, 'code': code})
                self.assertEqual(observation['reads'], reads)
                if original is None:
                    self.assertIsNone(observation['bodyAfter'])
                elif identifier.startswith('target-hosted-json-') or identifier.endswith('-invalid-json'):
                    self.assertEqual(observation['bodyAfter'], original)
                else:
                    self.assertEqual(json.loads(observation['bodyAfter']), json.loads(original))
                if status == 200:
                    self.assertNotIn('rejection', observation)
                else:
                    self.assertEqual(observation['rejection'], {'phase': 'policy-error' if status == 500 else 'rejected',
                        'code': code, 'auditStatus': status, 'status': status,
                        'reason': 'Invalid request body' if status == 400 else 'Gateway processing failed' if status == 500
                                  else 'API target is not authorized',
                        'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})

    def test_dedicated_kinds_do_not_repeat_registered_routes_or_read_the_body(self):
        for identifier, original in self.dedicated_target_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                self.assertEqual(observation['result'], {'allowed': True, 'status': 200, 'code': 'TARGET_ALLOWED'})
                self.assertEqual(observation['reads'], 0)
                self.assertEqual(observation['bodyAfter'], original)
                self.assertNotIn('rejection', observation)

    def test_responses_accepts_native_extensions_and_preserves_shared_state_boundaries(self):
        for name in ("optional", "previous", "conversation", "conversation-id", "prompt", "background", "file-reference"):
            self.assertTrue(self.results["responses-" + name]["result"]["allowed"])
            self.assertNotIn("rejection", self.results["responses-" + name])

    def test_target_internal_settings_failures_are_distinct_from_body_free_success_and_path_rejections(self):
        nodes = list(self.target_rejection)
        positions = {node.get("name"): nodes.index(node) for node in self.target_rejection.findall("./set-variable")}
        telemetry_position = nodes.index(self.target_rejection.find("./include-fragment[@fragment-id='core-gateway-telemetry']"))
        self.assertLess(positions["gatewayAuditStatus"], positions["gatewayAuditPhase"])
        self.assertLess(max(positions.values()), telemetry_position)
        self.assertIsNone(self.target_rejection.find("./return-response/set-body"))
        for name, status, code, reads in (
            ("valid", 200, "TARGET_ALLOWED", 0),
            ("unregistered-model", 200, "TARGET_ALLOWED", 0),
            ("malformed-body", 200, "TARGET_ALLOWED", 0),
            ("missing-settings", 500, "OPERATION_SETTINGS_INVALID", 0),
            ("malformed-settings", 500, "OPERATION_SETTINGS_INVALID", 0),
            ("path-registered", 200, "TARGET_ALLOWED", 0), ("path-unregistered", 200, "TARGET_ALLOWED", 0),
            ("path-generic-parameter", 200, "TARGET_ALLOWED", 0),
            ("path-missing", 403, "AUTHORIZATION_DENIED", 0), ("path-empty", 403, "AUTHORIZATION_DENIED", 0),
            ("path-whitespace", 403, "AUTHORIZATION_DENIED", 0), ("path-dot", 403, "AUTHORIZATION_DENIED", 0),
            ("path-parent", 403, "AUTHORIZATION_DENIED", 0),
        ):
            with self.subTest(case=name):
                observation = self.results["target-decision-" + name]
                self.assertEqual(observation["result"], {"allowed": status == 200, "status": status, "code": code})
                self.assertEqual(observation["reads"], reads)
                if status == 200:
                    self.assertNotIn("rejection", observation)
                else:
                    self.assertEqual(observation["rejection"], {"phase": "policy-error" if status == 500 else "rejected",
                                     "code": code, "auditStatus": status, "status": status,
                                     "reason": "Gateway processing failed" if status == 500 else "API target is not authorized",
                                     "requestIdHeader": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})

    def test_http_target_skips_model_revalidation_and_reads_only_for_operation_body_conditions(self):
        for identifier, (status, reads) in self.http_model_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                self.assertEqual(observation["result"], {"allowed": status == 200, "status": status,
                                 "code": "TARGET_ALLOWED" if status == 200 else "INVALID_REQUEST_BODY" if status == 400 else "AUTHORIZATION_DENIED"})
                self.assertEqual(observation["reads"], reads)
                if identifier in self.target_preserved_bodies:
                    self.assertEqual(json.loads(observation['bodyAfter']), self.target_preserved_bodies[identifier])
                if identifier in self.a2a_preserved_bodies:
                    self.assertEqual(observation['bodyAfter'], self.a2a_preserved_bodies[identifier])
                if status == 200:
                    self.assertNotIn("rejection", observation)
                else:
                    self.assertEqual(observation["rejection"]["phase"], "rejected")
                    self.assertEqual(observation["rejection"]["auditStatus"], status)
                    self.assertEqual(observation["rejection"]["status"], status)
                    self.assertEqual(observation["rejection"]["code"], observation["result"]["code"])
                    self.assertEqual(observation["rejection"]["reason"], "Invalid request body" if status == 400 else "API target is not authorized")
                    self.assertEqual(observation["rejection"]["requestIdHeader"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def test_l4_listener_cannot_bypass_l7_proof_for_normal_api(self):
        for name in ("missing", "spoof", "duplicate", "grpc-header", "grpc"): self.assertTrue(self.results["ingress-" + name]["result"])
        for name in ("valid",): self.assertFalse(self.results["ingress-" + name]["result"])

    def test_code_interpreter_items_are_rejected_only_at_responses_input_array_root(self):
        for identifier, (original, status, reads) in self.responses_code_interpreter_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                code = 'TARGET_ALLOWED' if status == 200 else 'AUTHORIZATION_DENIED'
                self.assertEqual(observation['result'], {'allowed': status == 200, 'status': status, 'code': code})
                self.assertEqual(observation['reads'], reads)
                self.assertEqual(json.loads(observation['bodyAfter']), original)
                if status == 403:
                    self.assertEqual(observation['rejection'], {'phase': 'rejected', 'code': code,
                        'auditStatus': 403, 'status': 403, 'reason': 'API target is not authorized',
                        'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
                else:
                    self.assertNotIn('rejection', observation)
        response = self.target_rejection.find('./return-response')
        self.assertIsNone(response.find('set-body'))
        self.assertEqual(response.find("set-header[@name='Cache-Control']/value").text, 'no-store')

    def test_include_values_are_delegated_and_only_independent_tool_or_container_conditions_reject(self):
        for identifier, (original, status, reads) in self.responses_include_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                code = 'TARGET_ALLOWED' if status == 200 else 'AUTHORIZATION_DENIED'
                self.assertEqual(observation['result'], {'allowed': status == 200, 'status': status, 'code': code})
                self.assertEqual(observation['reads'], reads)
                self.assertEqual(json.loads(observation['bodyAfter']), original)
                if status == 403:
                    self.assertEqual(observation['rejection'], {'phase': 'rejected', 'code': code,
                        'auditStatus': 403, 'status': 403, 'reason': 'API target is not authorized',
                        'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
                else:
                    self.assertNotIn('rejection', observation)

    def test_allowed_tool_choices_require_only_function_items_and_preserve_raw_fields(self):
        for identifier, (original, status, reads) in self.responses_tool_choice_cases.items():
            with self.subTest(case=identifier):
                observation = self.results[identifier]
                code = 'TARGET_ALLOWED' if status == 200 else 'AUTHORIZATION_DENIED'
                self.assertEqual(observation['result'], {'allowed': status == 200, 'status': status, 'code': code})
                self.assertEqual(observation['reads'], reads)
                self.assertEqual(json.loads(observation['bodyAfter']), original)
                if status == 403:
                    self.assertEqual(observation['rejection'], {'phase': 'rejected', 'code': code,
                        'auditStatus': 403, 'status': 403, 'reason': 'API target is not authorized',
                        'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
                else:
                    self.assertNotIn('rejection', observation)

    def test_websocket_target_query_is_fixed_and_unread(self):
        for name in ("missing", "fixed", "target"): self.assertTrue(self.results["query-" + name]["result"])
        for name in ("agent", "duplicate"): self.assertFalse(self.results["query-" + name]["result"])
        for name, item in self.results.items():
            if name.startswith(("query-", "auth-", "ingress-")): self.assertEqual(item["reads"], 0)

    def test_protocol_invoke_requires_a_verified_subscription(self):
        self.assertTrue(self.results["auth-valid"]["result"]["allowed"])
        for name in ("missing", "empty"):
            self.assertFalse(self.results["auth-deny-" + name]["result"]["allowed"])


if __name__ == "__main__": unittest.main()
