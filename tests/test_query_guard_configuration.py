"""query観測・拒否の設定契約を、合成入力とローカルSchemaだけで検証する。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from configuration_fixtures import CORE, configuration, fixture
from schema_helpers import local_schema_validator


DEFAULT = {"liveCreate": "observe", "realtimeCreate": "observe", "piiMcp": "observe"}
INVALID_OBJECTS = (None, [], True, False, 1, "observe")
INVALID_MODES = (None, True, False, 0, 1, [], {}, "", "off", "Observe", "REJECT",
                 " observe", "reject ", "observe\n", "observe\x00")


class QueryGuardConfigurationTests(unittest.TestCase):
    def raw(self):
        raw = fixture()
        raw["gateway"].pop("queryGuards", None)
        return raw

    def normalize(self, guards):
        raw = self.raw()
        raw["gateway"]["queryGuards"] = copy.deepcopy(guards)
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_omitted_and_empty_settings_default_to_observe_without_mutation(self):
        for omitted in (True, False):
            raw = self.raw()
            if not omitted:
                raw["gateway"]["queryGuards"] = {}
            original = copy.deepcopy(raw)
            with self.subTest(omitted=omitted):
                normalized = configuration.normalize_configuration(raw)
                self.assertEqual(normalized["gateway"]["queryGuards"], DEFAULT)
                self.assertEqual(raw, original)

    def test_each_route_can_be_configured_independently_in_either_mode(self):
        for route in DEFAULT:
            for mode in ("observe", "reject"):
                expected = {**DEFAULT, route: mode}
                with self.subTest(route=route, mode=mode):
                    normalized = self.normalize({route: mode})
                    self.assertEqual(normalized["gateway"]["queryGuards"], expected)
                    self.assertEqual(configuration.build_bicep_configuration(normalized)
                                     ["gateway"]["queryGuards"], expected)

    def test_mixed_modes_survive_bicep_conversion_without_sharing_mutable_state(self):
        guards = {"liveCreate": "reject", "realtimeCreate": "observe", "piiMcp": "reject"}
        raw = self.raw()
        raw["gateway"]["queryGuards"] = guards
        original = copy.deepcopy(raw)
        normalized = configuration.normalize_configuration(raw)
        normalized_before = copy.deepcopy(normalized)
        bicep = configuration.build_bicep_configuration(normalized)
        self.assertEqual(bicep["gateway"]["queryGuards"], guards)
        self.assertEqual(raw, original)
        self.assertEqual(normalized, normalized_before)
        bicep["gateway"]["queryGuards"]["realtimeCreate"] = "reject"
        normalized["gateway"]["queryGuards"]["liveCreate"] = "observe"
        self.assertEqual(raw, original)
        self.assertEqual(bicep["gateway"]["queryGuards"]["liveCreate"], "reject")
        self.assertEqual(normalized["gateway"]["queryGuards"]["realtimeCreate"], "observe")
        self.assertEqual(self.normalize({})["gateway"]["queryGuards"], DEFAULT)

    def test_query_guard_modes_do_not_change_other_gateway_protection_settings(self):
        observed = self.normalize({})
        rejected = self.normalize({route: "reject" for route in DEFAULT})
        observed["gateway"].pop("queryGuards")
        rejected["gateway"].pop("queryGuards")
        self.assertEqual(rejected, observed)

    def test_non_objects_and_unknown_route_names_are_rejected(self):
        for guards in (*INVALID_OBJECTS, {"unknown": "observe"},
                       {"liveCreate": "observe", "livecreate": "reject"},
                       {"paths": []}, {"mode": "reject"}):
            with self.subTest(guards=guards), self.assertRaisesRegex(
                    configuration.ConfigurationError, r"gateway\.queryGuards"):
                self.normalize(guards)

    def test_invalid_modes_are_rejected_for_every_route_without_echoing_values(self):
        for route in DEFAULT:
            for mode in (*INVALID_MODES, "PRIVATE_INPUT_SENTINEL"):
                with self.subTest(route=route, mode=mode), self.assertRaisesRegex(
                        configuration.ConfigurationError, r"gateway\.queryGuards\." + route) as caught:
                    self.normalize({route: mode})
                self.assertNotIn("PRIVATE_INPUT_SENTINEL", str(caught.exception))


class SplitQueryGuardConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="query-guard-config-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.options = root / "defaults" / "config.json"
        self.parameters = root / "inputs" / "params.json"
        self.options.parent.mkdir()
        self.parameters.parent.mkdir()

    def sources(self, options, parameters):
        self.options.write_text(json.dumps(options), encoding="utf-8")
        self.parameters.write_text(json.dumps({"configFile": "../defaults/config.json", **parameters}),
                                   encoding="utf-8")
        return configuration.load_configuration_source(self.parameters)

    def normalize_source(self, options, parameters):
        source = self.sources(options, parameters)
        raw = fixture()
        raw["gateway"].pop("queryGuards", None)
        raw["gateway"].update(source.get("gateway", {}))
        return configuration.normalize_configuration(raw)

    def test_environment_partial_override_preserves_unmentioned_standard_modes(self):
        options = {"gateway": {"queryGuards": {"liveCreate": "reject", "piiMcp": "reject"}}}
        parameters = {"gateway": {"queryGuards": {"piiMcp": "observe"}}}
        before = copy.deepcopy((options, parameters))
        normalized = self.normalize_source(options, parameters)
        expected = {"liveCreate": "reject", "realtimeCreate": "observe", "piiMcp": "observe"}
        self.assertEqual(normalized["gateway"]["queryGuards"], expected)
        self.assertEqual(configuration.build_bicep_configuration(normalized)
                         ["gateway"]["queryGuards"], expected)
        self.assertEqual((options, parameters), before)
        self.assertEqual(json.loads(self.options.read_text(encoding="utf-8")), options)
        saved = json.loads(self.parameters.read_text(encoding="utf-8"))
        self.assertEqual(saved["gateway"], parameters["gateway"])

    def test_omission_empty_override_and_params_only_complete_defaults(self):
        cases = (
            ({}, {}, DEFAULT),
            ({"gateway": {"queryGuards": {"liveCreate": "reject"}}},
             {"gateway": {"queryGuards": {}}}, {**DEFAULT, "liveCreate": "reject"}),
            ({}, {"gateway": {"queryGuards": {"realtimeCreate": "reject"}}},
             {**DEFAULT, "realtimeCreate": "reject"}),
        )
        for options, parameters, expected in cases:
            with self.subTest(options=options, parameters=parameters):
                self.assertEqual(self.normalize_source(options, parameters)
                                 ["gateway"]["queryGuards"], expected)

    def test_invalid_standard_or_environment_settings_cannot_reach_normalized_config(self):
        for source in ("options", "parameters"):
            for guards in (*INVALID_OBJECTS, {"unknown": "observe"}, {"piiMcp": "off"}):
                options, parameters = {}, {}
                target = options if source == "options" else parameters
                target["gateway"] = {"queryGuards": guards}
                with self.subTest(source=source, guards=guards), self.assertRaisesRegex(
                        configuration.ConfigurationError, r"gateway\.queryGuards"):
                    self.normalize_source(options, parameters)


class QueryGuardSourceContractTests(unittest.TestCase):
    """jsonschema未導入でも、公開既定値とエディターの設定境界を照合する。"""

    def test_both_standard_profiles_explicitly_default_all_routes_to_observe(self):
        for profile in ("test", "production"):
            options = json.loads((CORE / "config" / (profile + ".json")).read_text(encoding="utf-8"))
            with self.subTest(profile=profile):
                self.assertEqual(options["gateway"]["queryGuards"], DEFAULT)

    def test_all_three_schemas_define_the_same_optional_fixed_query_guard_contract(self):
        for name in ("options", "configuration", "params"):
            schema = json.loads((CORE / "schemas" / (name + ".schema.json")).read_text(encoding="utf-8"))
            guards = schema["properties"]["gateway"]["properties"]["queryGuards"]
            with self.subTest(schema=name):
                self.assertEqual(guards["type"], "object")
                self.assertIs(guards["additionalProperties"], False)
                self.assertEqual(set(guards["properties"]), set(DEFAULT))
                self.assertEqual(guards["default"], DEFAULT)
                self.assertNotIn("required", guards)
            for route, default in DEFAULT.items():
                field = guards["properties"][route]
                with self.subTest(schema=name, route=route):
                    self.assertEqual(field["type"], "string")
                    self.assertEqual(field["enum"], ["observe", "reject"])
                    self.assertEqual(field["default"], default)


class QueryGuardSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            raise unittest.SkipTest("jsonschema is required for local schema validation")
        cls.schemas = {name: json.loads((CORE / "schemas" / (name + ".schema.json"))
                                       .read_text(encoding="utf-8"))
                       for name in ("options", "configuration", "params")}
        for schema in cls.schemas.values():
            Draft202012Validator.check_schema(schema)

    def test_all_input_schemas_accept_partial_modes_and_reject_unknown_names_and_types(self):
        valid = ({}, DEFAULT, {route: "reject" for route in DEFAULT},
                 *({route: mode} for route in DEFAULT for mode in ("observe", "reject")))
        invalid = (*INVALID_OBJECTS, {"unknown": "observe"}, {"mode": "observe"},
                   *({route: mode} for route in DEFAULT for mode in INVALID_MODES))
        for name, schema in self.schemas.items():
            guards = schema["properties"]["gateway"]["properties"]["queryGuards"]
            validator = local_schema_validator(guards)
            for value in valid:
                with self.subTest(schema=name, valid=value):
                    validator.validate(value)
            for value in invalid:
                with self.subTest(schema=name, invalid=value):
                    self.assertFalse(validator.is_valid(value))

    def test_public_profiles_ship_observe_and_accept_environment_partial_override(self):
        synthetic = fixture()
        for profile in ("test", "production"):
            options = json.loads((CORE / "config" / (profile + ".json")).read_text(encoding="utf-8"))
            with self.subTest(profile=profile, source="options"):
                self.assertEqual(options["gateway"]["queryGuards"], DEFAULT)
                local_schema_validator(self.schemas["options"]).validate(options)
            parameters = json.loads((CORE / "params" / (profile + ".example.json"))
                                    .read_text(encoding="utf-8"))
            for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
                parameters[key] = synthetic[key]
            for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
                parameters["certificate"][key] = synthetic["certificate"][key]
            parameters["gateway"]["queryGuards"] = {"liveCreate": "reject"}
            with self.subTest(profile=profile, source="params"):
                local_schema_validator(self.schemas["params"]).validate(parameters)
            merged = configuration.load_configuration_source(CORE / "params" / (profile + ".example.json"))
            for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
                merged[key] = synthetic[key]
            for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
                merged["certificate"][key] = synthetic["certificate"][key]
            with self.subTest(profile=profile, source="combined"):
                local_schema_validator(self.schemas["configuration"]).validate(merged)


if __name__ == "__main__":
    unittest.main()
