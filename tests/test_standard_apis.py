"""標準APIの自動展開と、環境設定・明示APIを保持する境界を確認する。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from configuration_fixtures import CORE, api_fixture, fixture
from deployment.helpers import configuration
import standard_apis


def standard_fixture():
    """公開例と合成した架空環境を使い、実在環境や秘密をテストへ持ち込まない。"""
    raw = fixture()
    raw["services"]["modelDeployments"] = json.loads((CORE / "config/test.json").read_text())["services"]["modelDeployments"]
    raw["credentialProviders"] = [{
        "name": "graph-delegated", "displayName": "Fixture delegated Graph",
        "identityProvider": "aad", "clientId": "44444444-4444-4444-8444-444444444444",
        "clientSecretFile": "/fixture-private/cm-secret.txt", "oauthTenantId": raw["entraTenantId"],
        "resourceUri": "https://graph.microsoft.com",
        "scopes": "openid profile email offline_access https://graph.microsoft.com/User.Read",
        "connectionNames": [], "identityEndpoint": "https://graph.microsoft.com/v1.0/me",
        "identityIdProperty": "id", "pool": {"count": 12, "capacity": 8000},
    }]
    raw["gateway"]["authentication"]["credentialLogin"] = {"providerNames": ["graph-delegated"]}
    base = configuration.normalize_configuration(raw)
    foundry_name = "aif-" + base["resourceStem"]
    foundry_id = ("/subscriptions/" + base["subscriptionId"] + "/resourceGroups/"
                  + base["resourceGroupNames"]["svc"]
                  + "/providers/Microsoft.CognitiveServices/accounts/" + foundry_name)
    state = {key: copy.deepcopy(base[key]) for key in ("subscriptionId", "azureTenantId", "resourceStem", "resourceGroupNames")}
    state.update(serviceResourceIds={"foundry": foundry_id}, outputs={
        "resourceGroups": copy.deepcopy(base["resourceGroupNames"]),
        "names": {"foundry": foundry_name}, "services": {"foundry": foundry_id},
        "endpoints": {"foundry": "https://" + foundry_name + ".services.ai.azure.com",
                      "public": "https://" + base["gateway"]["publicHostName"]},
    })
    return raw, state


class StandardApiTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.state = standard_fixture()

    def test_empty_array_resolves_all_finalized_apis_and_operations(self):
        result = standard_apis.resolve_publication_configuration(self.raw, self.state)
        self.assertEqual({api["name"] for api in result["apiDefinitions"]}, standard_apis.STANDARD_API_NAMES)
        self.assertNotRegex(json.dumps(result["apiDefinitions"]), r"__[A-Z][A-Z0-9_]+__")

    def test_input_objects_and_model_configuration_remain_unchanged(self):
        before = copy.deepcopy((self.raw, self.state))
        result = standard_apis.resolve_publication_configuration(self.raw, self.state)
        expected = configuration.normalize_configuration(self.raw)
        self.assertEqual((self.raw, self.state), before)
        for key in ("services", "credentialProviders", "gateway", "network", "certificate", "management", "monitoring"):
            self.assertEqual(result[key], expected[key], key)
        self.assertFalse(result["publishApis"])

    def test_publication_flag_is_preserved_for_both_values(self):
        for flag in (False, True):
            with self.subTest(flag=flag):
                self.raw["publishApis"] = flag
                result = standard_apis.resolve_publication_configuration(self.raw, self.state)
                self.assertIs(result["publishApis"], flag)

    def test_api_products_and_mcp_remain_canonical_without_trial_subscriptions(self):
        result = standard_apis.resolve_publication_configuration(self.raw, self.state)
        apis = {api["name"]: api for api in result["apiDefinitions"]}
        canonical = {api["name"]: api for path in standard_apis.DEFINITION_FILES
                     for api in [json.loads(path.read_text(encoding="utf-8"))]}
        self.assertEqual({name: api["productId"] for name, api in apis.items()},
                         {name: api["productId"] for name, api in canonical.items()})
        self.assertEqual(apis["graph-profile-v1"]["productId"], "connection-users")
        self.assertEqual(apis["pii-v1"]["mcp"]["name"], "pii-v1-mcp")
        self.assertTrue(all(api["keySubscriptions"] == [] for api in apis.values()))
        self.assertTrue(all(api["released"] is True for api in apis.values()))

    def test_backends_and_speech_defaults_use_selected_foundry(self):
        apis = {api["name"]: api for api in standard_apis.resolve_publication_configuration(self.raw, self.state)["apiDefinitions"]}
        name = self.state["outputs"]["names"]["foundry"]
        cognitive = "https://" + name + ".cognitiveservices.azure.com"
        realtime = "wss://" + name + ".openai.azure.com/openai/v1/realtime"
        self.assertEqual(apis["openai-images-v1"]["backendUrl"], "https://" + name + ".openai.azure.com")
        for identifier in ("openai-realtime-ws-v1", "openai-realtime-observer-v1", "openai-transcription-ws-v1"):
            self.assertEqual(apis[identifier]["backendUrl"], realtime)
        self.assertEqual(apis["openai-live-ws-v1"]["backendUrl"], "wss://" + name + ".openai.azure.com/openai/v1/live/sessions")
        self.assertEqual(apis["openai-transcription-ws-v1"]["websocket"]["queryParameters"], {"intent": "transcription"})
        speech = apis["azure-speech-v1"]
        self.assertEqual(speech["backendUrl"], cognitive + "/tts")
        self.assertEqual(speech["backendAuthentication"]["resourceId"], self.state["outputs"]["services"]["foundry"])
        stt = next(operation for operation in speech["operations"] if operation["name"] == "stt")
        self.assertEqual(stt["backendOverride"]["backendUrl"], cognitive + "/stt")
        self.assertIn("language=ja-JP", stt["backendUrlTemplate"])
        voice = apis["azure-voice-live-ws-v1"]
        calls = apis["azure-voice-live-webrtc-v1"]
        self.assertEqual(voice["backendUrl"], "wss://" + name + ".cognitiveservices.azure.com/voice-live/realtime")
        self.assertEqual(calls["backendUrl"], voice["backendUrl"] + "/calls")
        self.assertEqual(voice["websocket"]["queryParameters"], {"model": "gpt-6.1-sol", "api-version": "2026-04-10"})
        self.assertEqual(calls["websocket"]["queryParameters"], {"model": "gpt-6.1-sol", "api-version": "2026-01-01-preview"})
        for api in (voice, calls):
            constraint = api["authorization"]["operationConstraints"]["invoke"]
            self.assertEqual(constraint["maxOutputTokensByModel"], {"gpt-6.1-sol": 128000})

    def test_header_follows_environment_setting(self):
        self.raw["gateway"]["authentication"]["subscriptionKeyHeader"] = "X-Fixture-Key"
        result = standard_apis.resolve_publication_configuration(self.raw, self.state)
        self.assertTrue(all(api["subscriptionKeyHeader"] == "X-Fixture-Key" for api in result["apiDefinitions"]))

    def test_custom_nonempty_api_is_normalized_without_standard_or_state_requirements(self):
        raw = api_fixture()
        before = copy.deepcopy(raw)
        with mock.patch.object(standard_apis, "_definitions", side_effect=AssertionError("unexpected template read")):
            result = standard_apis.resolve_publication_configuration(raw, {})
        self.assertEqual(result, configuration.normalize_configuration(raw))
        self.assertEqual(raw, before)
        self.assertEqual(result["apiDefinitions"][0]["keySubscriptions"][0]["subscriptionId"], "system-a")

    def test_missing_cm_provider_or_shared_login_fails_without_dropping_graph(self):
        for remove in ("provider", "login"):
            with self.subTest(remove=remove):
                raw = copy.deepcopy(self.raw)
                if remove == "provider":
                    raw["credentialProviders"] = []
                    raw["gateway"]["authentication"].pop("credentialLogin")
                else:
                    raw["credentialProviders"][0].pop("pool")
                    raw["credentialProviders"][0]["connectionNames"] = ["fixture-existing"]
                    raw["gateway"]["authentication"].pop("credentialLogin")
                with self.assertRaisesRegex(ValueError, "graph-delegated CM profile"):
                    standard_apis.resolve_publication_configuration(raw, self.state)

    def test_other_environment_or_untrusted_endpoint_fails(self):
        cases = (
            (("subscriptionId",), "22222222-2222-4222-8222-222222222222"),
            (("azureTenantId",), "22222222-2222-4222-8222-222222222222"),
            (("resourceStem",), "other-fixture"),
            (("resourceGroupNames", "svc"), "rg-other-svc"),
            (("outputs", "names", "foundry"), "aif-other"),
            (("outputs", "services", "foundry"), "/subscriptions/other/resource"),
            (("serviceResourceIds", "foundry"), "/subscriptions/other/resource"),
            (("outputs", "endpoints", "foundry"), "https://other.example.com"),
            (("outputs", "endpoints", "public"), "https://other.example.com"),
        )
        for path, value in cases:
            with self.subTest(path=path):
                state = copy.deepcopy(self.state)
                target = state
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(ValueError, "differs|differ"):
                    standard_apis.resolve_publication_configuration(self.raw, state)

    def test_missing_deployment_record_fails(self):
        with self.assertRaisesRegex(ValueError, "completed core deployment record"):
            standard_apis.resolve_publication_configuration(self.raw, {})

    def test_unknown_binding_fails(self):
        definitions = standard_apis._definitions()
        definitions[0]["displayName"] = "__UNEXPECTED_PUBLIC_VALUE__"
        with mock.patch.object(standard_apis, "_definitions", return_value=definitions):
            with self.assertRaisesRegex(ValueError, "unsupported binding: UNEXPECTED_PUBLIC_VALUE"):
                standard_apis.resolve_publication_configuration(self.raw, self.state)

    def test_removed_or_extra_standard_api_is_rejected(self):
        document = {"apiDefinitions": [{"name": "unexpected"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apis.json"
            path.write_text(json.dumps(document))
            with mock.patch.object(standard_apis, "DEFINITION_FILES", (path,)):
                with self.assertRaisesRegex(ValueError, "standard API file inventory"):
                    standard_apis.resolve_publication_configuration(self.raw, self.state)

    def test_only_api_templates_are_read_and_no_file_is_written(self):
        original = Path.read_text
        reads = []
        def read(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read), \
                mock.patch.object(Path, "write_text", side_effect=AssertionError("unexpected write")), \
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected write")):
            standard_apis.resolve_publication_configuration(self.raw, self.state)
        capabilities = CORE / "apps/gateway/apim/model-capabilities.json"
        self.assertEqual([path for path in reads if path != capabilities], list(standard_apis.DEFINITION_FILES))
        self.assertTrue(all(path in standard_apis.DEFINITION_FILES or path == capabilities for path in reads))

    def test_each_canonical_file_owns_only_its_named_api(self):
        for path in standard_apis.DEFINITION_FILES:
            with self.subTest(path=path.name):
                api = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(path.stem, api["name"])
                self.assertEqual(api["keySubscriptions"], [])
                self.assertTrue({"services", "gateway", "network", "credentialProviders", "apiDefinitions"}.isdisjoint(api))
        directory_apis = standard_apis.load_api_definitions(standard_apis.API_DEFINITION_DIRECTORY)
        self.assertEqual({api["name"] for api in directory_apis}, standard_apis.STANDARD_API_NAMES)

    def test_directory_rejects_mismatched_filename_and_duplicate_api_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "first.json"
            path.write_text(json.dumps({"name": "wrong"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "matching its filename"):
                standard_apis.load_api_definitions(Path(directory))
            path.write_text(json.dumps({"apiDefinitions": [{"name": "first"}, {"name": "first"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique API names"):
                standard_apis.load_api_definitions(path)


if __name__ == "__main__":
    unittest.main()
