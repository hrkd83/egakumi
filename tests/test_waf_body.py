"""AI payload acceptance stays confined to the paths with managed AI policies."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.waf_body import ai_body_settings, managed_ai_paths


def api(name):
    return json.loads((ROOT / "apps/gateway/apim/apis" / (name + ".json")).read_text(encoding="utf-8"))


class AiWafBodyTests(unittest.TestCase):
    def test_ai_override_keeps_critical_settings_and_does_not_mutate_the_strict_source(self):
        strict = {"state": "Enabled", "mode": "Prevention", "requestBodyCheck": True,
                  "maxRequestBodySizeInKb": 128, "requestBodyInspectLimitInKB": 128,
                  "requestBodyEnforcement": True, "fileUploadEnforcement": True,
                  "fileUploadLimitInMb": 100, "logScrubbing": {"state": "Enabled"},
                  "futureProtection": {"retain": True}}
        before = copy.deepcopy(strict)
        configured = ai_body_settings(strict)
        self.assertEqual(configured, {**before, "maxRequestBodySizeInKb": 2000,
            "requestBodyInspectLimitInKB": 2000, "requestBodyEnforcement": False,
            "fileUploadEnforcement": False})
        configured["futureProtection"]["retain"] = False
        self.assertEqual(strict, before)

    def test_standard_ai_paths_do_not_relax_auth_admin_custom_or_neighbor_paths(self):
        contract = api("openai-inference-v1")
        contract["operations"] = [
            next(o for o in contract["operations"] if o["name"] == "responses-create"),
            {"name": "neighbor", "method": "POST", "urlTemplate": "/responses/other"},
            {"name": "admin", "method": "POST", "urlTemplate": "/admin"},
            {"name": "unknown", "method": "POST", "urlTemplate": "/{operation}"}]
        custom = copy.deepcopy(contract)
        custom["path"] = "custom/openai/v1"
        auth = {"path": "auth/connections/browser", "operations": [
            {"name": "callback", "method": "POST", "urlTemplate": "/identity-callback"}]}
        self.assertEqual(managed_ai_paths({"apiDefinitions": [contract, custom, auth]}),
                         {"/models/openai/v1/responses"})

    def test_document_and_speech_scope_follows_the_canonical_start_contract_not_capacity(self):
        document, speech = api("document-analysis-v1"), api("azure-speech-v1")
        document["authorization"]["operationConstraints"]["analyze_document"]["maxRequestBytes"] = 500000000
        expected = {"/tools/documents/v1/analyze", "/models/azure-speech/v1/synthesize",
                    "/models/azure-speech/v1/recognize"}
        self.assertEqual(managed_ai_paths({"apiDefinitions": [document, speech]}), expected)
        document["authorization"]["operationConstraints"]["analyze_document"]["bodyFormat"] = "json"
        speech["name"] = "custom-speech"
        self.assertEqual(managed_ai_paths({"apiDefinitions": [document, speech]}), set())

    def test_deployment_routes_expand_only_registered_exact_manifest_paths(self):
        images = api("openai-images-v1")
        constraints = images["authorization"]["operationConstraints"]
        allowed = constraints["image-edit"]["allowedModels"]
        expected = {"/models/openai-images/v1/generations",
                    *("/models/openai-images/v1/deployments/" + model + "/edits" for model in allowed)}
        allowed.append("../escape")
        selected = managed_ai_paths({"apiDefinitions": [images]})
        self.assertEqual(selected, expected)
        self.assertTrue(all("{" not in path and "*" not in path for path in selected))
        allowed.append("unlisted-model")
        with self.assertRaisesRegex(ValueError, "canonical API definition"):
            managed_ai_paths({"apiDefinitions": [images]})

    def test_signed_status_and_realtime_control_do_not_acquire_ai_body_capacity(self):
        config = {"apiDefinitions": [api("document-analysis-v1"), api("openai-realtime-control-v1"),
                                     api("openai-realtime-observer-v1")]}
        self.assertEqual(managed_ai_paths(config), {"/tools/documents/v1/analyze", "/models/openai-realtime/v1/calls"})


if __name__ == "__main__":
    unittest.main()
