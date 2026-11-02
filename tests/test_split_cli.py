"""paramsとconfigの分離入力を、実際のローカルCLIで通して検証する。

異なるフォルダーに置いたファイルを使い、モデルと秘密ファイル参照が出力先変更で壊れないことを調べる。PATHからAzure CLIを外し、意図しないクラウド接続を防ぐ。
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CORE = Path(__file__).resolve().parents[1]
SCRIPTS = CORE / "scripts"
sys.path.insert(0, str(SCRIPTS))
from deployment.helpers.configuration import load_configuration
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "99999999-9999-4999-8999-999999999999"
CONNECTION = "44444444-4444-4444-8444-444444444444"


class SplitCliTests(unittest.TestCase):
    """paramsとconfigの分離入力を、実際のローカルCLIで通して検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workforce-split-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parameter_directory = self.root / "company" / "parameters"
        self.config_directory = self.root / "platform" / "config"
        self.output_directory = self.root / "generated"
        for directory in (self.parameter_directory, self.config_directory, self.output_directory):
            directory.mkdir(parents=True)
        self.options = json.loads((CORE / "config/test.json").read_text())
        self.parameters = json.loads((CORE / "params/test.example.json").read_text())
        self.parameters.update(subscriptionId=SUBSCRIPTION, azureTenantId=TENANT, entraTenantId=TENANT,
                               configFile="../../platform/config/options.json")
        self.assertEqual(self.parameters["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
        self.assertEqual(self.parameters["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        self.assertNotIn("oauthTenantId", self.parameters["credentialProviders"][0])
        self.parameters["credentialProviders"][0]["clientId"] = CONNECTION
        # 企業固有の参照はparamsだけに置きます。標準configの配置や合成先が
        # 異なるフォルダーでも、同じ公開鍵・DNS入力先を参照し続けることを調べます。
        self.parameters["management"]["sshPublicKeyFile"] = "../nonexistent-protected/management.pub"
        self.parameters["certificate"]["pluginArgumentsFile"] = "../nonexistent-protected/dns.json"
        self.parameters["credentialProviders"][0]["clientSecretFile"] = "../nonexistent-protected/connection.txt"
        self.config = self.config_directory / "options.json"
        self.source = self.parameter_directory / "company.local.json"
        self.config.write_text(json.dumps(self.options))
        self.source.write_text(json.dumps(self.parameters))

    def run_cli(self, script, *arguments):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, arguments)],
                                cwd=self.root, capture_output=True, text=True,
                                env={**os.environ, "PATH": str(self.root / "no-external-tools"),
                                     "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_composed_paths(self, compiled, output):
        """保存JSONは相対参照とし、paramsの宣言元を再読込後も保つ。"""
        expected = {
            ("management", "sshPublicKeyFile"): self.parameter_directory / "../nonexistent-protected/management.pub",
            ("certificate", "pluginArgumentsFile"): self.parameter_directory / "../nonexistent-protected/dns.json",
        }
        reloaded = load_configuration(output)
        for (section, key), original_target in expected.items():
            saved = compiled[section][key]
            self.assertFalse(Path(saved).is_absolute())
            self.assertEqual((output.parent / saved).resolve(), original_target.resolve())
            self.assertEqual(reloaded[section][key], str(original_target.resolve()))
            self.assertFalse(original_target.exists())
        # 読込・保存の試験では管理鍵を生成しない。
        self.assertFalse((self.parameter_directory / "../nonexistent-protected/management").exists())
        self.assertNotIn("configFile", compiled)

    def test_integration_cli_preserves_all_pinned_models_and_source_relative_paths(self):
        output = self.output_directory / "integration.local.json"
        inputs_before = (self.source.read_bytes(), self.config.read_bytes())
        receipt = self.run_cli("configure_trial.py", "--base", self.source, "--output", output,
                               "--cm-client-id", CONNECTION,
                               "--cm-client-secret-file", "../nonexistent-secret/connection.txt")
        compiled = json.loads(output.read_text())
        self.assert_composed_paths(compiled, output)
        self.assertEqual(compiled["services"]["modelDeployments"], self.options["services"]["modelDeployments"])
        self.assertEqual({api["name"] for api in compiled["apiDefinitions"]},
                         {"openai-inference-v1", "pii-v1", "graph-profile-v1"})
        saved_secret = compiled["credentialProviders"][0]["clientSecretFile"]
        expected_secret = (self.output_directory / "../nonexistent-secret/connection.txt").resolve()
        self.assertFalse(Path(saved_secret).is_absolute())
        self.assertEqual((output.parent / saved_secret).resolve(), expected_secret)
        self.assertEqual(load_configuration(output)["credentialProviders"][0]["clientSecretFile"], str(expected_secret))
        self.assertFalse(expected_secret.exists())
        self.assertFalse(receipt["azureChangesPerformed"])
        self.assertFalse(receipt["secretValuesRead"])
        self.assertEqual((self.source.read_bytes(), self.config.read_bytes()), inputs_before)
        if os.name != "nt":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_coverage_template_cli_uses_split_parameters_without_azure(self):
        output = self.output_directory / "bindings.json"
        receipt = self.run_cli("prepare_coverage.py", "--base", self.source,
                               "--overlay", CORE / "apps/gateway/apim/apis",
                               "--bindings-template", "--output", output)
        bindings = json.loads(output.read_text())
        self.assertIn("OPENAI_RESOURCE_BASE_URL", bindings)
        self.assertFalse(any(key.startswith(("TOOLS_", "HOSTED_AGENT_")) for key in bindings))
        self.assertEqual(set(bindings.values()), {""})
        self.assertEqual(receipt["resourceMutations"], 0)
        self.assertFalse(receipt["publishApis"])

    def test_full_standard_cli_preserves_formal_apis_without_sample_bindings(self):
        entra_tenant = "88888888-8888-4888-8888-888888888888"
        self.parameters["entraTenantId"] = entra_tenant
        self.source.write_text(json.dumps(self.parameters))
        integration = self.output_directory / "integration-standard.json"
        self.run_cli("configure_trial.py", "--base", self.source, "--output", integration,
                     "--cm-client-id", CONNECTION,
                     "--cm-client-secret-file", "../nonexistent-secret/connection.txt")
        binding_file = self.output_directory / "standard-bindings.json"
        self.run_cli("prepare_coverage.py", "--base", self.source,
                     "--overlay", CORE / "apps/gateway/apim/apis",
                     "--bindings-template", "--output", binding_file)
        bindings = json.loads(binding_file.read_text())
        self.assertFalse(any(key.startswith(("TOOLS_", "HOSTED_AGENT_")) for key in bindings))
        known = {
            "PUBLIC_HOST_NAME": self.parameters["gateway"]["publicHostName"],
            "SPEECH_RESOURCE_ID": "/subscriptions/" + SUBSCRIPTION + "/resourceGroups/rg-speech/providers/Microsoft.CognitiveServices/accounts/speech-fixture",
            "VOICE_LIVE_WS_BASE_URL": "wss://provider.example.com/voice-live/realtime",
            "VOICE_LIVE_CALLS_WS_BASE_URL": "wss://provider.example.com/voice-live/realtime/calls",
            "LIVE_WS_BASE_URL": "wss://provider.example.com/openai/v1/live/sessions",
            "TRANSCRIPTION_WS_BASE_URL": "wss://provider.example.com/openai/v1/realtime",
            "STT_LANGUAGE": "en-US",
        }
        for key in bindings:
            bindings[key] = known.get(key) or (
                "wss://provider.example.com/realtime" if key.endswith("WS_BASE_URL") else
                "https://provider.example.com" if key.endswith("BASE_URL") else
                "approved-voice-model" if key.endswith("MODEL") else "2026-01-01")
        binding_file.write_text(json.dumps(bindings))
        output = self.output_directory / "formal-standard.json"
        receipt = self.run_cli("prepare_coverage.py", "--base", self.source,
                               "--overlay", CORE / "apps/gateway/apim/apis",
                               "--bindings", binding_file, "--output", output)
        compiled = json.loads(output.read_text())
        self.assert_composed_paths(compiled, output)
        definitions = compiled["apiDefinitions"]
        self.assertEqual(compiled["azureTenantId"], TENANT)
        self.assertEqual(compiled["entraTenantId"], entra_tenant)
        self.assertEqual({api["name"] for api in definitions},
                         {file.stem for file in (CORE / "apps/gateway/apim/apis").glob("*.json")})
        self.assertTrue(all(api["serviceClass"] == "platform" for api in definitions))
        self.assertFalse(any(api["name"].startswith("sample-") for api in definitions))
        self.assertEqual(receipt["resourceMutations"], 0)
        self.assertFalse(compiled["publishApis"])
        for sample_binding in ("TOOLS_", "HOSTED_AGENT_"):
            self.assertNotIn(sample_binding, output.read_text())
        arm = self.output_directory / "formal-arm.json"
        self.run_cli("deployment/helpers/configuration.py", "render", "--config", output, "--output", arm)
        rendered = json.loads(arm.read_text())
        self.assertEqual([api["name"] for api in rendered["apiDefinitions"]],
                         [api["name"] for api in definitions])
        self.assertTrue(all(api["serviceClass"] == "platform" for api in rendered["apiDefinitions"]))
        self.assertNotIn("TOOLS_", arm.read_text())
        self.assertNotIn("HOSTED_AGENT_", arm.read_text())

    def test_foundation_cli_preserves_models_and_paths_when_compiling_to_another_folder(self):
        template = self.output_directory / "foundation-bindings.json"
        self.run_cli("prepare_coverage.py", "--base", self.source,
                     "--overlay", CORE / "apps/gateway/apim/apis", "--phase", "foundation",
                     "--bindings-template", "--output", template)
        bindings = json.loads(template.read_text())
        models = {model["modelFormat"] + "/" + model["name"]: model
                  for model in self.options["services"]["modelDeployments"]}
        purpose = {
            "EMBEDDING": models["OpenAI/text-embedding-3-small"],
            "IMAGE": models["OpenAI/gpt-image-2.5-sunburst"],
            "VISION": models["OpenAI/gpt-6.1-sol"],
            "PDF": models["OpenAI/gpt-6.1-sol"],
            "REALTIME": models["OpenAI/gpt-realtime-2.1"],
            "RERANK": models["Cohere/Cohere-rerank-v4.0-fast"],
        }
        for key in bindings:
            if key == "PUBLIC_HOST_NAME":
                bindings[key] = self.parameters["gateway"]["publicHostName"]
                continue
            selected = purpose.get(key.split("_", 1)[0])
            self.assertIsNotNone(selected, "Unexpected foundation binding: " + key)
            suffix = key[len(key.split("_", 1)[0]) + 1:]
            bindings[key] = selected[{"MODEL": "modelName", "MODEL_VERSION": "modelVersion", "SKU": "skuName"}[suffix]]
        template.write_text(json.dumps(bindings))
        output = self.output_directory / "foundation.local.json"
        receipt = self.run_cli("prepare_coverage.py", "--base", self.source,
                               "--overlay", CORE / "apps/gateway/apim/apis",
                               "--phase", "foundation", "--bindings", template, "--output", output)
        compiled = json.loads(output.read_text())
        self.assert_composed_paths(compiled, output)
        # API定義はモデル配備を持たず、元のconfigにある版・容量を保ちます。
        self.assertEqual(compiled["services"]["modelDeployments"], self.options["services"]["modelDeployments"])
        self.assertEqual(compiled["apiDefinitions"], [])
        provider = compiled["credentialProviders"][0]
        self.assertEqual(provider["clientId"], CONNECTION)
        self.assertNotIn("oauthTenantId", provider)
        self.assertEqual(load_configuration(output)["credentialProviders"][0]["oauthTenantId"], TENANT)
        self.assertEqual(compiled["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        expected_secret = (self.parameter_directory / "../nonexistent-protected/connection.txt").resolve()
        self.assertEqual((output.parent / provider["clientSecretFile"]).resolve(), expected_secret)
        self.assertFalse(expected_secret.exists())
        self.assertFalse(compiled["publishApis"])
        self.assertEqual(receipt["resourceMutations"], 0)

    def test_local_inventory_cli_reports_composed_target_without_azure_or_secret_reads(self):
        output = self.output_directory / "inventory.json"
        receipt = self.run_cli("preparation_inventory.py", "--config", self.source, "--output", output)
        report = json.loads(output.read_text())
        self.assertEqual(receipt["mode"], "localOnly")
        self.assertEqual(report["target"]["subscriptionId"], SUBSCRIPTION)
        self.assertEqual(report["target"]["location"], self.parameters["location"])
        self.assertEqual(report["target"]["foundryLocation"], self.parameters["foundryLocation"])
        self.assertEqual(report["resourceMutations"], 0)
        self.assertEqual(report["inferenceRequests"], 0)
        self.assertFalse(report["tools"]["az"])
        self.assertNotIn("nonexistent-protected", json.dumps(report))

    def test_native_lifecycle_validation_accepts_the_same_split_source(self):
        result = self.run_cli("deployment/lifecycle.py", "validate", "--config", self.source)
        self.assertTrue(result["valid"])
        self.assertEqual(result["resourceGroups"]["id"], "rg-test-egakumi-id")
        self.assertEqual(len(result["resourceGroups"]), 5)

    def test_bicep_render_omits_loader_metadata_and_local_input_paths(self):
        output = self.output_directory / "arm.json"
        self.run_cli("deployment/helpers/configuration.py", "render", "--config", self.source, "--output", output)
        rendered = json.loads(output.read_text())
        self.assertNotIn("configFile", rendered)
        self.assertNotIn("$schema", rendered)
        self.assertNotIn("sshPublicKeyFile", rendered["management"])
        self.assertNotIn("pluginArgumentsFile", rendered["certificate"])
        self.assertNotIn("clientSecretFile", rendered["credentialProviders"][0])
        self.assertEqual(rendered["credentialProviders"][0]["clientId"], CONNECTION)
        self.assertEqual(rendered["credentialProviders"][0]["oauthTenantId"], TENANT)
        self.assertNotIn("nonexistent-protected", json.dumps(rendered))
        self.assertEqual(rendered["services"]["modelDeployments"],
                         json.loads((CORE / "config/test.json").read_text())["services"]["modelDeployments"])


if __name__ == "__main__":
    unittest.main()
