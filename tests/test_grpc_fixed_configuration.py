"""常設gRPCの内部固定値と、利用者入力の境界を合成入力だけで確認する。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from configuration_fixtures import CORE, configuration, fixture


FIXED_GRPC = {"enabled": True, "publicPort": 8443, "backendPort": 443}
INVALID_GRPC_VALUES = {
    "enabled": (False, 0, 1, "true", None),
    "publicPort": (443, 3389, 8444, 9443, 65000, True, False, "8443", None),
    "backendPort": (80, 8443, True, False, "443", None),
}


class FixedGrpcConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_omitted_settings_generate_the_fixed_internal_and_bicep_contract(self):
        variants = (None, {}, {"publicHostName": "gateway.example.com"})
        for settings in variants:
            raw = fixture()
            raw["gateway"]["publicHostName"] = "gateway.example.com"
            if settings is None:
                raw["gateway"].pop("grpc", None)
            else:
                raw["gateway"]["grpc"] = copy.deepcopy(settings)
            before = copy.deepcopy(raw)
            with self.subTest(settings=settings):
                normalized = self.normalize(raw)
                expected = {**FIXED_GRPC, "publicHostName": "gateway.example.com"}
                self.assertEqual(normalized["gateway"]["grpc"], expected)
                self.assertEqual(configuration.build_bicep_configuration(normalized)["gateway"]["grpc"], expected)
                self.assertEqual(raw, before)

    def test_fixed_input_fields_are_rejected_even_with_the_same_values(self):
        invalid = {key: (FIXED_GRPC[key], *values, float(FIXED_GRPC[key])) if key != "enabled" else (True, *values)
                   for key, values in INVALID_GRPC_VALUES.items()}
        for key, values in invalid.items():
            for value in values:
                raw = fixture()
                raw["gateway"]["grpc"][key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                        configuration.ConfigurationError, r"gateway\.grpc.*unsupported field.*" + key):
                    self.normalize(raw)

    def test_fixed_endpoint_still_requires_key_vault_and_the_shared_hostname(self):
        raw = fixture()
        raw["gateway"].pop("grpc", None)
        raw["network"]["apimKeyVaultAccessEnabled"] = False
        with self.assertRaisesRegex(configuration.ConfigurationError, r"network\.apimKeyVaultAccessEnabled"):
            self.normalize(raw)
        raw = fixture()
        raw["gateway"]["grpc"]["publicHostName"] = "different.example.com"
        with self.assertRaisesRegex(configuration.ConfigurationError, "existing certificate hostname"):
            self.normalize(raw)

    def test_combined_file_accepts_hostname_only_and_rejects_fixed_input_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "combined.json"
            raw = fixture()
            raw["management"]["sshPublicKeyFile"] = "fixture-management-key.pub"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(configuration.load_configuration(path)["gateway"]["grpc"],
                             {**FIXED_GRPC, **raw["gateway"]["grpc"]})
            for key, values in INVALID_GRPC_VALUES.items():
                for value in (FIXED_GRPC[key], *values):
                    invalid = copy.deepcopy(raw)
                    invalid["gateway"]["grpc"][key] = value
                    path.write_text(json.dumps(invalid), encoding="utf-8")
                    with self.subTest(key=key, value=value), self.assertRaisesRegex(
                            configuration.ConfigurationError, r"gateway\.grpc.*unsupported field.*" + key):
                        configuration.load_configuration(path)

    def test_split_config_has_no_grpc_options_and_params_cannot_set_fixed_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = root / "options.json"
            parameters = root / "params.json"
            options.write_text("{}", encoding="utf-8")
            raw = {"configFile": "options.json", "gateway": {"grpc": {"publicHostName": "gateway.example.com"}}}
            parameters.write_text(json.dumps(raw), encoding="utf-8")
            loaded = configuration.load_configuration_source(parameters)
            self.assertEqual(loaded["gateway"]["grpc"], {"publicHostName": "gateway.example.com"})
            for settings in ({}, FIXED_GRPC, *({key: value} for key, value in FIXED_GRPC.items())):
                options.write_text(json.dumps({"gateway": {"grpc": settings}}), encoding="utf-8")
                with self.subTest(config=settings), self.assertRaisesRegex(
                        configuration.ConfigurationError, r"config\.gateway.*unsupported field.*grpc"):
                    configuration.load_configuration_source(parameters)
            options.write_text("{}", encoding="utf-8")
            for key, value in FIXED_GRPC.items():
                invalid = copy.deepcopy(raw)
                invalid["gateway"]["grpc"][key] = value
                parameters.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(key=key), self.assertRaisesRegex(
                        configuration.ConfigurationError, "unsupported field.*" + key):
                    configuration.load_configuration_source(parameters)

    def test_shipped_profiles_omit_fixed_inputs_and_normalize_to_the_same_endpoint(self):
        for profile in ("test", "production"):
            options = json.loads((CORE / "config" / (profile + ".json")).read_text(encoding="utf-8"))
            with self.subTest(profile=profile):
                self.assertNotIn("grpc", options["gateway"])
                raw = configuration.load_configuration_source(CORE / "params" / (profile + ".example.json"))
                synthetic = fixture()
                for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
                    raw[key] = synthetic[key]
                for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
                    raw["certificate"][key] = synthetic["certificate"][key]
                raw["services"]["modelDeployments"] = []
                raw["credentialProviders"] = synthetic["credentialProviders"]
                raw["gateway"]["authentication"] = synthetic["gateway"]["authentication"]
                raw["publishApis"] = False
                self.assertEqual(self.normalize(raw)["gateway"]["grpc"],
                                 {**FIXED_GRPC, "publicHostName": raw["gateway"]["publicHostName"]})


class FixedGrpcSchemaTests(unittest.TestCase):
    def setUp(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is required for schema validation")
        self.validator = Draft202012Validator

    def grpc_schema(self, name):
        schema = json.loads((CORE / "schemas" / (name + ".schema.json")).read_text(encoding="utf-8"))
        return schema["properties"]["gateway"]["properties"]["grpc"]

    def test_options_schema_rejects_the_entire_grpc_block(self):
        schema = json.loads((CORE / "schemas/options.schema.json").read_text(encoding="utf-8"))
        gateway = schema["properties"]["gateway"]
        self.assertNotIn("grpc", gateway["properties"])
        validator = self.validator(gateway)
        validator.validate({})
        for settings in ({}, FIXED_GRPC, {"publicHostName": "gateway.example.com"}):
            with self.subTest(settings=settings):
                self.assertFalse(validator.is_valid({"grpc": settings}))

    def test_input_schemas_reject_fixed_fields_even_with_the_same_values(self):
        for name in ("configuration", "params"):
            schema = self.grpc_schema(name)
            self.validator.check_schema(schema)
            validator = self.validator(schema)
            for settings in ({}, {"publicHostName": "gateway.example.com"}):
                with self.subTest(schema=name, settings=settings):
                    validator.validate(settings)
            for key, values in INVALID_GRPC_VALUES.items():
                for value in (FIXED_GRPC[key], *values):
                    with self.subTest(schema=name, key=key, value=value):
                        self.assertFalse(validator.is_valid({key: value}))

    def test_params_schema_keeps_only_the_existing_hostname_input(self):
        schema = self.grpc_schema("params")
        self.assertEqual(set(schema["properties"]), {"publicHostName"})
        validator = self.validator(schema)
        validator.validate({"publicHostName": "gateway.example.com"})
        for key, value in FIXED_GRPC.items():
            with self.subTest(key=key):
                self.assertFalse(validator.is_valid({key: value}))


if __name__ == "__main__":
    unittest.main()
