"""Canonical API migration preserves grants, contracts and fixed destinations."""

from copy import deepcopy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from api_definition_helpers import catalog_overlay
from api_migration import (EXTERNAL_SAMPLE_API_NAMES, MigrationError, migrate_api_definitions,
                           migrate_configuration, _write_with_backup)


def legacy_api(name, operation, *, contract="original-contract", model="approved-model", method="POST", route="/chat/completions"):
    return {
        "name": name, "displayName": name, "path": "legacy/" + name,
        "backendService": "foundryOpenAi", "backendAudience": "https://cognitiveservices.azure.com",
        "productId": "model-users", "keySubscriptions": [],
        "operations": [{"name": operation, "method": method, "urlTemplate": route, "templateParameters": []}],
        "authorization": {
            "mode": "subscriptionKey",
            "operationConstraints": {operation: {"kind": "bodyModel", "allowedModels": [model],
                "maxRequestBytes": 32768, "maxOutputTokens": 512, "outputTokenParameter": "max_completion_tokens"}},
        },
    }


class ApiMigrationTests(unittest.TestCase):
    def test_public_catalog_migration_is_lossless_and_idempotent(self):
        definitions = catalog_overlay()["apiDefinitions"]
        migrated = migrate_api_definitions(definitions)
        self.assertEqual({api["name"]: api for api in migrated}, {api["name"]: api for api in definitions})
        self.assertEqual(migrate_api_definitions(migrated), migrated)

    def test_same_person_keeps_different_contracts_for_text_and_vision(self):
        text = legacy_api("models", "chat-completions", contract="text-contract")
        vision = legacy_api("model-multimodal", "vision", contract="vision-contract")
        vision["authorization"]["operationConstraints"]["vision"]["maxRequestBytes"] = 2048000
        source = [text, vision]
        before = deepcopy(source)
        api = migrate_api_definitions(source)[0]
        self.assertEqual(source, before)
        self.assertEqual(len(api["operations"]), 1)
        constraint = api["authorization"]["operationConstraints"]["chat-completions"]
        self.assertEqual(constraint["defaultProfile"], "chat")
        self.assertEqual(constraint["maxRequestBytes"], 32768)
        profiles = {item["profileId"]: item for item in constraint["profiles"]}
        self.assertEqual(profiles["vision"]["maxRequestBytes"], 2048000)
        self.assertEqual(profiles["chat"]["modalities"], ["text"])
        self.assertEqual(profiles["vision"]["modalities"], ["text", "image"])


    def test_realtime_merges_same_contract_but_keeps_disjoint_models_for_other_contracts(self):
        apis = []
        for name, model, contract in (("model-realtime", "regular-model", "regular-contract"),
                                      ("model-realtime-mini", "mini-model", "mini-contract")):
            api = legacy_api(name, "invoke", contract=contract, method="GET", route="/")
            api.update(apiType="websocket", backendService="external", backendUrl="wss://fixed.example/realtime",
                       websocket={"queryParameters": {"model": model}})
            api["authorization"]["operationConstraints"]["invoke"] = {"kind": "registeredOperation", "maxRequestBytes": 65536, "bodyFormat": "none"}
            apis.append(api)
        result = migrate_api_definitions(apis)[0]
        self.assertEqual(result["websocket"], {"queryParameters": {}, "allowedModels": ["regular-model", "mini-model"], "defaultModel": "regular-model"})
    def test_speech_keeps_each_fixed_backend_and_does_not_inherit_tts_headers(self):
        apis = []
        for name, operation, route in (("model-tts", "tts", "/synthesize"), ("model-stt", "stt", "/recognize")):
            api = legacy_api(name, operation, route=route)
            api.update(backendService="external", backendUrl="https://" + operation + ".example/fixed",
                       backendAuthentication={"mode": "speechEntra", "resourceId": "synthetic-speech"},
                       backendHeaders={"X-Microsoft-OutputFormat": "wav"} if operation == "tts" else {"Content-Type": "audio/wav"})
            api["operations"][0]["backendUrlTemplate"] = "/fixed-" + operation
            apis.append(api)
        result = migrate_api_definitions(apis)[0]
        operations = {item["name"]: item for item in result["operations"]}
        self.assertEqual(operations["tts"]["backendOverride"]["backendUrl"], "https://tts.example/fixed")
        self.assertEqual(operations["stt"]["backendOverride"]["backendUrl"], "https://stt.example/fixed")
        self.assertNotIn("X-Microsoft-OutputFormat", operations["stt"]["backendOverride"]["backendHeaders"])

    def test_conflicting_backends_and_ambiguous_contracts_are_rejected(self):
        left = legacy_api("model-cohere-embeddings", "cohere-embeddings", route="/embed")
        right = legacy_api("model-rerank", "rerank", route="/rerank")
        right["backendAudience"] = "https://different.example"
        with self.assertRaises(MigrationError):
            migrate_api_definitions([left, right])
        left = legacy_api("model-cohere-embeddings", "invoke", contract="first", route="/invoke")
        right = legacy_api("model-rerank", "invoke", contract="second", route="/invoke")
        right["authorization"]["operationConstraints"]["invoke"]["maxOutputTokens"] = 256
        with self.assertRaises(MigrationError):
            migrate_api_definitions([left, right])

    def test_legacy_and_canonical_samples_are_excluded_without_touching_other_settings(self):
        platform = legacy_api("models", "chat-completions")
        samples = [{"name": name} for name in sorted(EXTERNAL_SAMPLE_API_NAMES)]
        samples.append({"name": "custom-external-demo", "serviceClass": "sample"})
        original = {"publishApis": True, "portal": {"publicHostName": "portal.example.com"},
                    "apiDefinitions": [platform, *samples]}
        before = deepcopy(original)
        migrated = migrate_configuration(original)
        self.assertEqual([api["name"] for api in migrated["apiDefinitions"]], ["openai-inference-v1"])
        self.assertEqual(migrated["portal"], original["portal"])
        self.assertTrue(migrated["publishApis"])
        self.assertEqual(original, before)
        self.assertEqual(migrate_configuration(migrated), migrated)

    def test_merge_requires_subscription_keys_and_supported_authorization_fields(self):
        left = legacy_api("model-cohere-embeddings", "embeddings", route="/embed")
        right = legacy_api("model-rerank", "rerank", route="/rerank")
        right["authorization"]["mode"] = "keyOrEntra"
        with self.assertRaisesRegex(MigrationError, "subscription key"):
            migrate_api_definitions([left, right])
        left["authorization"]["unknownProperty"] = []
        with self.assertRaisesRegex(MigrationError, "Unsupported"):
            migrate_api_definitions([left])

    def test_formal_graph_api_is_retained_despite_its_external_backend(self):
        api = legacy_api("graph-profile-v1", "graph_profile_self", method="GET", route="/me")
        api.update(backendService="external", backendUrl="https://graph.microsoft.com/v1.0")
        migrated = migrate_api_definitions([api])
        self.assertEqual([api["name"] for api in migrated], ["graph-profile-v1"])
        self.assertEqual(migrated[0]["path"], "tools/graph/v1")
        self.assertTrue(all(api["serviceClass"] == "platform" for api in migrated))

    def test_unrelated_settings_and_publication_are_preserved(self):
        original = {"publishApis": False, "certificate": {"mode": "prepared"}, "apiDefinitions": [legacy_api("models", "chat-completions")]}
        result = migrate_configuration(original)
        self.assertFalse(result["publishApis"])
        self.assertEqual(result["certificate"], original["certificate"])
        self.assertEqual(original["apiDefinitions"][0]["name"], "models")
        self.assertEqual(result, migrate_configuration(result))

    def test_cli_dry_run_is_default_and_does_not_echo_subjects_or_credentials(self):
        source = {"apiDefinitions": [legacy_api("models", "chat-completions")], "opaqueSecret": "do-not-print-sensitive-data"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(source))
            before = path.read_bytes()
            completed = subprocess.run([sys.executable, str(CORE / "scripts/api_migration.py"), str(path)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(path.read_bytes(), before)
            output = json.loads(completed.stdout)
            self.assertFalse(output["written"])
            for value in ("synthetic-person", "synthetic-client", "do-not-print-sensitive-data"):
                self.assertNotIn(value, completed.stdout + completed.stderr)

    def test_write_has_private_backup_outside_checkout_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            path.write_text('{"original":true}\n')
            backup = _write_with_backup(path, '{"migrated":true}\n')
            try:
                self.assertEqual(backup.read_text(), '{"original":true}\n')
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(backup.parent.stat().st_mode), 0o700)
                self.assertFalse(backup.is_relative_to(CORE))
                self.assertEqual(path.read_text(), '{"migrated":true}\n')
            finally:
                backup.unlink()
                backup.parent.rmdir()
            link = Path(directory) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(MigrationError):
                _write_with_backup(link, "{}")


if __name__ == "__main__":
    unittest.main()
