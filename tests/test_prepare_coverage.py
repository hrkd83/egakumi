"""基本設定と正式18 API定義を、公開せずに合成する処理を検証する。

モデルの重複、版の衝突、placeholderの不足、構築対象モデル、相対ファイル参照を調べる。生成した設定は別途確認してからAzureへ適用する。
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest

from configuration_fixtures import CORE
sys.path.insert(0, str(CORE / "scripts"))
import prepare_coverage
from api_definition_helpers import catalog_overlay, configured_base


class CoveragePreparationTests(unittest.TestCase):
    """基本設定と正式18 API定義を、公開せずに合成する処理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.base = configured_base()
        self.overlay = catalog_overlay()
        self.bindings = {}
        ids = {"PUBLIC_HOST_NAME": self.base["gateway"]["publicHostName"],
               "SPEECH_RESOURCE_ID": "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture-svc/providers/Microsoft.CognitiveServices/accounts/speech-fixture"}
        for key in prepare_coverage.required_bindings(self.overlay):
            self.bindings[key] = ids.get(key) or ("wss://provider.example.com/realtime" if key.endswith("WS_BASE_URL") else
                "https://fixture.vault.azure.net/secrets/provider-rerank" if key.endswith("SECRET_IDENTIFIER") else
                "https://provider.example.com" if key.endswith(("BASE_URL", "BACKEND")) else
                "approved-" + key.lower().replace("_model", "") if key.endswith("MODEL") else
                "GlobalStandard" if key.endswith("SKU") else "2026-01-01")
        self.bindings["VOICE_LIVE_WS_BASE_URL"] = "wss://provider.example.com/voice-live/realtime"
        self.bindings["VOICE_LIVE_CALLS_WS_BASE_URL"] = "wss://provider.example.com/voice-live/realtime/calls"
        self.bindings["LIVE_WS_BASE_URL"] = "wss://provider.example.com/openai/v1/live/sessions"
        self.bindings["TRANSCRIPTION_WS_BASE_URL"] = "wss://provider.example.com/openai/v1/realtime"
        self.bindings["VOICE_LIVE_MODEL"] = "gpt-4.1"
        self.bindings["VOICE_LIVE_CALLS_MODEL"] = "gpt-4.1"

    def test_standard_bindings_and_definitions_have_no_external_sample_dependency(self):
        self.assertTrue(all(api["serviceClass"] == "platform" for api in self.overlay["apiDefinitions"]))
        self.assertFalse(any(name.startswith(("TOOLS_", "HOSTED_AGENT_")) for name in prepare_coverage.required_bindings(self.overlay)))

    def test_external_samples_do_not_introduce_production_binding_requirements(self):
        external = json.loads((CORE / "tests/fixtures/external-api-definitions.json").read_text())["apiDefinitions"]
        base, overlay = copy.deepcopy(self.base), copy.deepcopy(self.overlay)
        base["apiDefinitions"] = copy.deepcopy(external)
        overlay["apiDefinitions"].extend(copy.deepcopy(external))
        before = copy.deepcopy((base, overlay))
        result = prepare_coverage.prepare(base, overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual({api["name"] for api in result["apiDefinitions"]},
                         {api["name"] for api in self.overlay["apiDefinitions"] if api["serviceClass"] == "platform"})
        self.assertTrue(all(api["serviceClass"] == "platform" for api in result["apiDefinitions"]))
        self.assertEqual((base, overlay), before)
        self.assertNotIn("HOSTED_AGENT", json.dumps(result))
        self.assertNotIn("TOOLS_", json.dumps(result))

    def test_voice_live_webrtc_has_a_distinct_registered_control_endpoint(self):
        """通常 WSS と WebRTC の認可・固定上流・差込み値を分ける。"""
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        apis = {api["name"]: api for api in result["apiDefinitions"]}
        calls = apis["azure-voice-live-webrtc-v1"]
        self.assertEqual(calls["path"], "models/voice-live/v1/webrtc")
        self.assertEqual(calls["apiType"], "websocket")
        self.assertEqual(calls["backendUrl"], self.bindings["VOICE_LIVE_CALLS_WS_BASE_URL"])
        self.assertEqual(calls["websocket"]["queryParameters"]["model"], self.bindings["VOICE_LIVE_CALLS_MODEL"])
        self.assertEqual(calls["authorization"], apis["azure-voice-live-ws-v1"]["authorization"])
        self.bindings["VOICE_LIVE_CALLS_WS_BASE_URL"] = self.bindings["VOICE_LIVE_WS_BASE_URL"]
        with self.assertRaisesRegex(ValueError, "distinct WSS or WebRTC"):
            prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")

    def test_formal_apis_merge_without_enabling_publication_or_changing_base(self):
        before = copy.deepcopy(self.base)
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual(self.base, before)
        self.assertFalse(result["publishApis"])
        self.assertEqual(len(result["apiDefinitions"]), len(self.base.get("apiDefinitions", [])) + len(self.overlay["apiDefinitions"]))
        self.assertEqual({api["productId"] for api in result["apiDefinitions"]}, {"model-users", "pii-users", "connection-users"})
        self.assertEqual({api.get("apiType", "http") for api in result["apiDefinitions"]}, {"http", "websocket"})
        self.assertTrue(all(api["serviceClass"] == "platform" for api in result["apiDefinitions"]))
        self.assertTrue(Path(result["management"]["sshPublicKeyFile"]).is_absolute())
        self.assertIn("provider.example.com", result["network"]["firewall"]["apimAllowedFqdns"])

    def test_formal_catalog_reuses_base_personal_cm_without_duplicating_credentials(self):
        """API定義は本人接続を参照し、CM設定は基盤入力だけで管理する。"""
        before = copy.deepcopy(self.base)
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual(result["credentialProviders"][0]["clientId"], before["credentialProviders"][0]["clientId"])
        self.assertEqual(result["gateway"]["authentication"]["credentialLogin"],
                         before["gateway"]["authentication"]["credentialLogin"])
        connection = next(api for api in result["apiDefinitions"] if "credentialConnection" in api)
        self.assertEqual(connection["credentialConnection"]["mode"], "personal")
        self.assertNotIn("credentialProviders", self.overlay)
        self.assertFalse(result["publishApis"])
        self.assertEqual(self.base, before)

    def test_identical_api_comparison_uses_merged_common_configuration(self):
        """独自overlayがCM設定を補う場合も、同じGraph APIを重複作成しない。"""
        graph = next(api for api in self.overlay["apiDefinitions"] if api["name"] == "graph-profile-v1")
        base = copy.deepcopy(self.base)
        providers = base.pop("credentialProviders")
        login = base["gateway"]["authentication"].pop("credentialLogin")
        base["apiDefinitions"] = [copy.deepcopy(graph)]
        overlay = {"apiDefinitions": [copy.deepcopy(graph)], "credentialProviders": providers,
                   "gateway": {"authentication": {"credentialLogin": login}}}
        result = prepare_coverage.prepare(base, overlay, {}, base_directory=CORE, overlay_directory=CORE)
        self.assertEqual([api["name"] for api in result["apiDefinitions"]], ["graph-profile-v1"])
        self.assertEqual(result["apiDefinitions"][0]["productId"], "connection-users")

    def test_separate_entra_tenant_binds_cm_without_moving_azure(self):
        base = copy.deepcopy(self.base)
        base["entraTenantId"] = "77777777-7777-4777-8777-777777777777"
        result = prepare_coverage.prepare(base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual(result["azureTenantId"], self.base["azureTenantId"])
        self.assertEqual(result["entraTenantId"], base["entraTenantId"])
        graph = next(api for api in result["apiDefinitions"] if api["name"] == "graph-profile-v1")
        self.assertEqual(graph["credentialConnection"], {"mode": "personal", "providerName": "graph-delegated", "enabled": True})
        self.assertEqual(graph["backendAuthentication"], {"mode": "none"})
        from deployment.helpers.configuration import normalize_configuration
        normalized = normalize_configuration(result, base_directory=CORE / "config")
        self.assertEqual(normalized["credentialProviders"][0]["oauthTenantId"], base["entraTenantId"])

    def test_multimodal_output_limits_follow_the_model_capability(self):
        """画像/PDFでも試験の初期値をモデル上限と取り違えない。"""
        base = copy.deepcopy(self.base)
        result = prepare_coverage.prepare(base, self.overlay, self.bindings, base_directory=CORE / "config")
        api = next(api for api in result["apiDefinitions"] if api["name"] == "openai-inference-v1")
        constraints = api["authorization"]["operationConstraints"]
        profiles = {profile["profileId"]: profile for constraint in constraints.values() for profile in constraint["profiles"]}
        for profile in ("vision", "pdf"):
            self.assertEqual(profiles[profile]["maxOutputTokensByModel"]["gpt-6.1-sol"], 128000)
        self.assertEqual(profiles["vision"]["outputTokenParameter"], "max_completion_tokens")
        self.assertEqual(profiles["pdf"]["outputTokenParameter"], "max_output_tokens")

    def test_stt_public_wav_and_fixed_speech_backend_header_survive_normalization(self):
        """WAFへcodec構文を送らず、Speech向けには公式の固定値を残す。"""
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        api = next(api for api in result["apiDefinitions"] if api["name"] == "azure-speech-v1")
        constraint = api["authorization"]["operationConstraints"]["stt"]
        self.assertEqual(constraint["contentTypes"], ["audio/wav"])
        self.assertEqual(constraint["bodyFormat"], "binary")
        stt = next(operation for operation in api["operations"] if operation["name"] == "stt")
        self.assertEqual(stt["backendOverride"]["backendHeaders"], {"Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"})

    def test_same_real_model_keeps_base_capacity_and_conflicting_version_is_rejected(self):
        # 公開モデル名・版は資材で固定します。基本側で増やした容量だけを追加定義が奪わないことを調べます。
        model = copy.deepcopy(next(model for model in self.base["services"]["modelDeployments"] if model["name"] == "gpt-6.1-sol"))
        self.overlay["services"] = {"modelDeployments": [copy.deepcopy(model)]}
        model["capacity"] = 123
        next(item for item in self.base["services"]["modelDeployments"] if item["name"] == model["name"])["capacity"] = model["capacity"]
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual([m for m in result["services"]["modelDeployments"] if m["name"] == model["name"]], [model])
        next(m for m in self.overlay["services"]["modelDeployments"] if m["name"] == model["name"])["modelVersion"] = "different-version"
        with self.assertRaisesRegex(ValueError, "Conflicting model"):
            prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")

    def test_missing_bindings_and_duplicate_api_fail_before_output(self):
        del self.bindings["OPENAI_RESOURCE_BASE_URL"]
        with self.assertRaisesRegex(ValueError, "Unresolved public bindings: OPENAI_RESOURCE_BASE_URL"):
            prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.setUp()
        self.base["apiDefinitions"] = [prepare_coverage.substitute(self.overlay["apiDefinitions"][0], self.bindings)]
        self.base["apiDefinitions"][0]["displayName"] = "Different existing API contract"
        with self.assertRaisesRegex(ValueError, "silently replace"):
            prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")

    def test_identical_registered_api_is_reused_without_duplicating_it(self):
        """基本3 APIを準備済みでも、全18 APIの読み込みで同じAPIを増やさない。"""
        inference = next(api for api in self.overlay["apiDefinitions"] if api["name"] == "openai-inference-v1")
        self.base["apiDefinitions"] = [prepare_coverage.substitute(inference, self.bindings)]
        result = prepare_coverage.prepare(self.base, self.overlay, self.bindings, base_directory=CORE / "config")
        self.assertEqual({api["name"] for api in result["apiDefinitions"]},
                         {api["name"] for api in self.overlay["apiDefinitions"] if api["serviceClass"] == "platform"})
        self.assertEqual(sum(api["name"] == "openai-inference-v1" for api in result["apiDefinitions"]), 1)

    def test_foundation_does_not_need_sample_endpoints_or_publish_apis(self):
        effective = {key: value for key, value in self.overlay.items() if key != "apiDefinitions"}
        bindings = {key: self.bindings[key] for key in prepare_coverage.required_bindings(effective)}
        result = prepare_coverage.prepare(self.base, self.overlay, bindings, base_directory=CORE / "config", phase="foundation")
        self.assertEqual(len(result["apiDefinitions"]), len(self.base.get("apiDefinitions", [])))
        self.assertTrue(result["gateway"]["grpc"]["enabled"])
        self.assertEqual(len(result["credentialProviders"]), len(self.base["credentialProviders"]))
        self.assertEqual(result["gateway"]["authentication"]["credentialLogin"],
                         self.base["gateway"]["authentication"]["credentialLogin"])
        self.assertFalse(result["publishApis"])


    def test_cli_saves_relative_paths_and_reload_keeps_source_references(self):
        """生成先が別の深さでもDNS・公開鍵の参照は同じで、秘密読出しや鍵生成はしない。"""
        from deployment.helpers.configuration import load_configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output = root / "params/base.json", root / "generated/deep/coverage.local.json"
            source.parent.mkdir()
            (root / "secrets").mkdir()
            credential = root / "secrets/dns.json"
            credential.write_text("SENTINEL-PRIVATE-CONTENT")
            base = copy.deepcopy(self.base)
            base["management"]["sshPublicKeyFile"] = "../secrets/management-ssh.pub"
            base["certificate"]["pluginArgumentsFile"] = "../secrets/dns.json"
            source.write_text(json.dumps(base))
            bindings = root / "bindings.json"
            bindings.write_text(json.dumps(self.bindings))
            before = source.read_bytes()
            actual = subprocess.run([sys.executable, str(CORE / "scripts/prepare_coverage.py"),
                "--base", str(source), "--overlay", str(CORE / "apps/gateway/apim/apis"),
                "--bindings", str(bindings), "--output", str(output)], text=True, capture_output=True)
            self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["management"]["sshPublicKeyFile"], "../../secrets/management-ssh.pub")
            self.assertEqual(saved["certificate"]["pluginArgumentsFile"], "../../secrets/dns.json")
            loaded = load_configuration(output)
            self.assertEqual(loaded["management"]["sshPublicKeyFile"], str(root / "secrets/management-ssh.pub"))
            self.assertEqual(loaded["certificate"]["pluginArgumentsFile"], str(credential))
            self.assertEqual(source.read_bytes(), before)
            self.assertNotIn("SENTINEL-PRIVATE-CONTENT", actual.stdout + actual.stderr + output.read_text())
            self.assertFalse((root / "secrets/management-ssh").exists())
            self.assertFalse((root / "secrets/management-ssh.pub").exists())

    def test_cli_custom_overlay_uses_its_own_directory_for_file_references(self):
        """企業がAPI例を別の場所へコピーしても、そこで宣言したsecret参照を保つ。"""
        from deployment.helpers.configuration import load_configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, overlay = root / "params/base.json", root / "api-definitions/custom.json"
            bindings, output = root / "bindings.json", root / ".state/generated-config/coverage.local.json"
            source.parent.mkdir()
            overlay.parent.mkdir()
            source.write_text(json.dumps(self.base))
            custom = {"credentialProviders": copy.deepcopy(self.base["credentialProviders"])}
            custom["credentialProviders"][0]["clientSecretFile"] = "../private/company-cm.txt"
            overlay.write_text(json.dumps(custom))
            known = {"TENANT_ID": self.base["entraTenantId"], "CM_CLIENT_ID": "11111111-1111-4111-8111-111111111111"}
            bindings.write_text(json.dumps({key: known[key] for key in prepare_coverage.required_bindings(custom)}))
            before = source.read_bytes(), overlay.read_bytes()
            actual = subprocess.run([sys.executable, str(CORE / "scripts/prepare_coverage.py"),
                "--base", str(source), "--overlay", str(overlay), "--bindings", str(bindings),
                "--output", str(output)], text=True, capture_output=True)
            self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
            loaded = load_configuration(output)
            self.assertEqual(loaded["credentialProviders"][0]["clientSecretFile"],
                             str(root / "private/company-cm.txt"))
            saved = json.loads(output.read_text())
            self.assertEqual(saved["credentialProviders"][0]["clientSecretFile"],
                             "../../private/company-cm.txt")
            self.assertFalse((root / "private").exists())
            self.assertEqual((source.read_bytes(), overlay.read_bytes()), before)

    def test_cli_standard_template_saves_no_anthropic_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.json"
            output = Path(directory) / "bindings.json"
            original = json.dumps(self.base)
            base.write_text(original)
            command = [sys.executable, str(CORE / "scripts/prepare_coverage.py"), "--base", str(base),
                "--overlay", str(CORE / "apps/gateway/apim/apis"), "--bindings-template",
                "--output", str(output)]
            actual = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
            self.assertFalse(any(name.startswith("ANTHROPIC_") for name in json.loads(output.read_text())))
            self.assertEqual(base.read_text(), original)
            self.assertFalse(json.loads(actual.stdout)["publishApis"])

if __name__ == "__main__": unittest.main()
