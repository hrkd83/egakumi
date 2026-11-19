"""gRPC公開入力の廃止と、HTTP用入口保護の独立した契約を合成入力で確認する。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from configuration_fixtures import CORE, configuration, fixture, api_fixture


RETIRED_GRPC_SETTINGS = (None, {}, {"publicHostName": "gateway.example.com"},
                         {"enabled": True, "publicPort": 8443, "backendPort": 443})


class RetiredGrpcConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_normal_http_configuration_has_no_generated_grpc_contract(self):
        raw = fixture()
        before = copy.deepcopy(raw)
        normalized = self.normalize(raw)
        self.assertNotIn("grpc", normalized["gateway"])
        self.assertNotIn("grpc", configuration.build_bicep_configuration(normalized)["gateway"])
        self.assertEqual(raw, before)

    def test_entire_grpc_input_is_rejected_without_silent_migration(self):
        for settings in RETIRED_GRPC_SETTINGS:
            raw = fixture()
            raw["gateway"]["grpc"] = settings
            with self.subTest(settings=settings), self.assertRaisesRegex(
                    configuration.ConfigurationError, "gateway.*unsupported field.*grpc"):
                self.normalize(raw)

    def test_new_grpc_api_and_proto_definition_are_rejected(self):
        for settings in ({"apiType": "grpc"}, {"grpc": {"definition": 'syntax = "proto3";'}}):
            raw = api_fixture()
            raw["apiDefinitions"][0].update(settings)
            with self.subTest(settings=settings), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_combined_file_rejects_the_retired_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "combined.json"
            raw = fixture()
            raw["management"]["sshPublicKeyFile"] = "fixture-management-key.pub"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertNotIn("grpc", configuration.load_configuration(path)["gateway"])
            for settings in RETIRED_GRPC_SETTINGS:
                raw["gateway"]["grpc"] = settings
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(settings=settings), self.assertRaisesRegex(
                        configuration.ConfigurationError, "gateway.*unsupported field.*grpc"):
                    configuration.load_configuration(path)

    def test_params_and_config_both_reject_the_retired_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = root / "options.json"
            params = root / "params.json"
            for owner in ("params", "config"):
                for settings in RETIRED_GRPC_SETTINGS:
                    options.write_text(json.dumps({"gateway": {"grpc": settings}} if owner == "config" else {}), encoding="utf-8")
                    params.write_text(json.dumps({"configFile": "options.json", **({"gateway": {"grpc": settings}} if owner == "params" else {})}), encoding="utf-8")
                    with self.subTest(owner=owner, settings=settings), self.assertRaisesRegex(
                            configuration.ConfigurationError, "unsupported field.*grpc"):
                        configuration.load_configuration_source(params)

    def test_shipped_profiles_do_not_reintroduce_grpc(self):
        for profile in ("test", "production"):
            for directory, suffix in (("params", ".example"), ("config", "")):
                value = json.loads((CORE / directory / (profile + suffix + ".json")).read_text(encoding="utf-8"))
                with self.subTest(profile=profile, directory=directory):
                    self.assertNotIn("grpc", value["gateway"])


class RetiredGrpcSchemaTests(unittest.TestCase):
    def setUp(self):
        from jsonschema import Draft202012Validator
        self.validator = Draft202012Validator

    def test_every_input_schema_rejects_the_entire_grpc_block(self):
        for name in ("configuration", "params", "options"):
            schema = json.loads((CORE / "schemas" / (name + ".schema.json")).read_text(encoding="utf-8"))
            gateway = schema["properties"]["gateway"]
            self.validator.check_schema(gateway)
            self.assertNotIn("grpc", gateway["properties"])
            validator = self.validator(gateway)
            for settings in RETIRED_GRPC_SETTINGS:
                with self.subTest(schema=name, settings=settings):
                    self.assertFalse(validator.is_valid({"grpc": settings}))


if __name__ == "__main__":
    unittest.main()
