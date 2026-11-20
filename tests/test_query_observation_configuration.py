"""観測ラベルの登録契約と、退役する拒否設定を黙って無視しない境界を検証する。"""
import copy
import json
import unittest

from configuration_fixtures import CORE, configuration, fixture
from schema_helpers import local_schema_validator
from test_api_protocols import bound_external_coverage


class QueryObservationConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_observation_has_no_gateway_mode_configuration(self):
        raw = fixture()
        before = copy.deepcopy(raw)
        config = self.normalize(raw)
        self.assertEqual(raw, before)
        for output in (config, configuration.build_bicep_configuration(config)):
            self.assertNotIn("queryGuards", output["gateway"])
            self.assertNotIn("queryObservations", output["gateway"])

    def test_retired_gateway_modes_are_explicit_input_errors(self):
        for value in ({}, {"liveCreate": "reject"}, {"boxBrowse": "observe"}, None, [], "reject"):
            raw = fixture()
            raw["gateway"]["queryGuards"] = value
            with self.subTest(value=value), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

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

    def test_retired_operation_and_endpoint_names_are_not_silently_ignored(self):
        for scope in ("operation", "mcp", "native"):
            raw = bound_external_coverage()
            if scope == "operation":
                target = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-rest-v1")["operations"][0]
            elif scope == "mcp":
                target = next(api for api in raw["apiDefinitions"] if api["name"] == "pii-v1")["mcp"]
            else:
                target = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-mcp-v1")["mcpProxy"]["endpoints"][0]
            target["queryGuard"] = "customQuery"
            with self.subTest(scope=scope), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_public_schemas_share_the_optional_label_contract_without_mode_settings(self):
        for filename in ("configuration", "params"):
            schema = json.loads((CORE / "schemas" / (filename + ".schema.json")).read_text(encoding="utf-8"))
            with self.subTest(schema=filename):
                self.assertNotIn("queryGuards", schema["properties"]["gateway"]["properties"])
                definition = schema["$defs"]["queryObservationReference"]
                self.assertEqual(definition["maxLength"], 64)
                for label in ("boxBrowse", "workiqSearch", "x" * 64):
                    self.assertFalse(list(local_schema_validator(definition).iter_errors(label)))
                for label in ("none", "unknown", "1label", "bad.label", "ラベル", "x" * 65, "label\n"):
                    self.assertTrue(list(local_schema_validator(definition).iter_errors(label)))

    def test_editor_schemas_explicitly_reject_retired_names_at_all_three_registration_points(self):
        for filename in ("configuration", "params"):
            schema = json.loads((CORE / "schemas" / (filename + ".schema.json")).read_text(encoding="utf-8"))
            api = schema["properties"]["apiDefinitions"]["items"]["properties"]
            registrations = {"operation": api["operations"]["items"], "mcp": api["mcp"],
                             "native": api["mcpProxy"]["properties"]["endpoints"]["items"]}
            for name, registration in registrations.items():
                validator = local_schema_validator({"$defs": schema["$defs"], **registration})
                with self.subTest(schema=filename, registration=name):
                    errors = list(validator.iter_errors({"queryGuard": "customQuery"}))
                    self.assertTrue(any(error.validator == "not" for error in errors))
                    errors = list(validator.iter_errors({"queryObservation": "customQuery"}))
                    self.assertFalse(any(error.validator == "not" for error in errors))
        options = json.loads((CORE / "schemas/options.schema.json").read_text(encoding="utf-8"))
        validator = local_schema_validator(options)
        self.assertTrue(list(validator.iter_errors({"gateway": {"queryGuards": {"liveCreate": "reject"}}})))


if __name__ == "__main__":
    unittest.main()
