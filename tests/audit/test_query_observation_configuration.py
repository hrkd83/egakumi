"""観測ラベルの登録と入力形式を検証する。"""
import copy
import json
import unittest

from tests.support.configuration_fixtures import CORE, configuration
from tests.support.schema_helpers import local_schema_validator
from tests.gateway.publication.test_api_protocols import bound_external_coverage


class QueryObservationConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_http_generated_and_native_mcp_labels_are_independent(self):
        raw = bound_external_coverage()
        http = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-rest-v1")
        http["operations"][0]["queryObservation"] = "vendorMessages"
        generated = next(api for api in raw["apiDefinitions"] if api["name"] == "pii-v1")
        generated["mcp"]["queryObservation"] = "piiFacade"
        native = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-mcp-v1")
        native["mcpProxy"]["endpoints"][0]["queryObservation"] = "workiqSearch"
        before = copy.deepcopy(raw)
        actual = {api["name"]: api for api in self.normalize(raw)["apiDefinitions"]}
        self.assertEqual(raw, before)
        self.assertEqual(actual["sample-rest-v1"]["operations"][0]["queryObservation"], "vendorMessages")
        self.assertEqual(actual["pii-v1"]["mcp"]["queryObservation"], "piiFacade")
        self.assertEqual(actual["sample-mcp-v1"]["mcpProxy"]["endpoints"][0]["queryObservation"], "workiqSearch")

    def test_public_schemas_validate_observation_labels(self):
        for filename in ("configuration", "params"):
            schema = json.loads((CORE / "schemas" / (filename + ".schema.json")).read_text(encoding="utf-8"))
            with self.subTest(schema=filename):
                definition = schema["$defs"]["queryObservationReference"]
                self.assertEqual(definition["maxLength"], 64)
                for label in ("boxBrowse", "workiqSearch", "x" * 64):
                    self.assertFalse(list(local_schema_validator(definition).iter_errors(label)))
                for label in ("none", "unknown", "1label", "bad.label", "ラベル", "x" * 65, "label\n"):
                    self.assertTrue(list(local_schema_validator(definition).iter_errors(label)))


if __name__ == "__main__":
    unittest.main()
