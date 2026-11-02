"""モデル別の公表上限と、可変・未対応を混同しない展開を検証する。"""
import copy
from pathlib import Path
import sys
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from model_capabilities import apply_model_capabilities, load_model_capabilities
from standard_apis import load_api_definitions, API_DEFINITION_DIRECTORY
from deployment.helpers.configuration import _apis, ConfigurationError
from test_api_contract_configuration import contract, TENANT


class ModelCapabilitiesTests(unittest.TestCase):
    def test_standard_profiles_share_one_canonical_map_without_changing_ids(self):
        apis = load_api_definitions(API_DEFINITION_DIRECTORY)
        text = next(api for api in apis if api["name"] == "openai-inference-v1")
        chat = text["authorization"]["operationConstraints"]["chat-completions"]
        profiles = {item["profileId"]: item for item in chat["profiles"]}
        self.assertEqual(set(profiles), {"chat", "vision", "compatible", "router"})
        self.assertEqual(profiles["chat"]["maxOutputTokensByModel"]["gpt-6.1-sol"], 128000)
        self.assertEqual(profiles["compatible"]["maxOutputTokensByModel"]["DeepSeek-V4-Flash"], 384000)
        self.assertEqual(profiles["router"]["maxOutputTokens"], None)
        self.assertEqual(profiles["router"]["outputTokenLimitKinds"]["model-router"], "dynamic")
        realtime = next(api for api in apis if api["name"] == "openai-realtime-control-v1")["authorization"]["operationConstraints"]["realtime-create"]
        self.assertEqual(realtime["maxOutputTokens"], 4096)
        self.assertEqual(realtime["initialOutputTokens"], 512)

    def test_config_accepts_real_large_limits_but_validates_exact_models_and_null_reason(self):
        api = contract()
        outer = api["authorization"]["operationConstraints"]["chat-completions"]
        for profile in outer["profiles"]:
            model = profile["allowedModels"][0]
            profile.update(maxOutputTokens=128000, maxOutputTokensByModel={model: 128000}, outputTokenLimitKinds={model: "documented"})
        outer.update(maxOutputTokens=128000, maxOutputTokensByModel={"text-model": 128000}, outputTokenLimitKinds={"text-model": "documented"})
        self.assertEqual(_apis([api], TENANT, {"text-model", "vision-model"}, "https://gateway.example.com", {"subscriptionKeyHeader": "api-key"})[0]["authorization"]["operationConstraints"]["chat-completions"]["profiles"][0]["maxOutputTokens"], 128000)
        for change in [lambda item: item["maxOutputTokensByModel"].update(other=1), lambda item: item["maxOutputTokensByModel"].update({"text-model": None}), lambda item: item["outputTokenLimitKinds"].clear()]:
            broken = copy.deepcopy(api); change(broken["authorization"]["operationConstraints"]["chat-completions"])
            with self.assertRaises(ConfigurationError):
                _apis([broken], TENANT, {"text-model", "vision-model"}, "https://gateway.example.com", {"subscriptionKeyHeader": "api-key"})

    def test_missing_model_capability_is_not_replaced_with_512(self):
        apis = load_api_definitions(API_DEFINITION_DIRECTORY)
        caps = load_model_capabilities()
        caps["entries"] = [entry for entry in caps["entries"] if entry["modelId"] != "gpt-6.1-sol"]
        with self.assertRaisesRegex(ValueError, "no verified capability"):
            apply_model_capabilities(apis, caps, {"VOICE_LIVE_MODEL": "gpt-4.1", "VOICE_LIVE_CALLS_MODEL": "gpt-4.1"})


if __name__ == "__main__":
    unittest.main()
