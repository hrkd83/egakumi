"""本人・共有キー・app-onlyの独立した回数制限を、合成設定だけで確認する。"""
import copy
import json
import unittest

from configuration_fixtures import CORE, api_fixture, configuration, fixture
from schema_helpers import local_schema_validator


KINDS = ("user", "subscriptionKey", "application")
CONTRACT_KINDS = KINDS[1:]
DEFAULT = {
    "user": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60},
    "subscriptionKey": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60, "overrides": []},
    "application": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60, "overrides": []},
}


class ContractRateConfigurationTests(unittest.TestCase):
    """認可と回数制限を分け、主体ごとの共通設定を正しく公開する。"""
    def normalized(self, limits=None, *, omit=False):
        raw = fixture()
        if omit:
            raw["gateway"].pop("rateLimits", None)
        else:
            raw["gateway"]["rateLimits"] = copy.deepcopy(limits)
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def assert_invalid(self, limits):
        with self.assertRaises(configuration.ConfigurationError): self.normalized(limits)

    def test_omitted_and_empty_objects_use_subject_defaults_without_mutating_source(self):
        raw = fixture(); raw["gateway"].pop("rateLimits", None)
        original = copy.deepcopy(raw)
        self.assertEqual(configuration.normalize_configuration(raw)["gateway"]["rateLimits"], DEFAULT)
        for limits in ({}, {kind: {} for kind in KINDS}):
            self.assertEqual(self.normalized(limits)["gateway"]["rateLimits"], DEFAULT)
        self.assertEqual(raw, original)

    def test_partial_and_disabled_settings_complete_each_independent_public_contract(self):
        limits = {"user": {"enabled": False, "calls": 90}, "subscriptionKey": {"enabled": True, "calls": 120},
                  "application": {"renewalPeriodSeconds": 30}}
        expected = copy.deepcopy(DEFAULT)
        for kind, fields in limits.items(): expected[kind].update(fields)
        result = self.normalized(limits)
        self.assertEqual(result["gateway"]["rateLimits"], expected)
        self.assertEqual(configuration.build_bicep_configuration(result)["gateway"]["rateLimits"], expected)

    def test_integer_boundaries_apply_to_all_subjects_and_contract_overrides(self):
        for calls, period in ((1, 1), (1000000, 300)):
            for kind in KINDS:
                with self.subTest(calls=calls, period=period, kind=kind):
                    rate = {"calls": calls, "renewalPeriodSeconds": period}
                    if kind in CONTRACT_KINDS:
                        rate["overrides"] = [{"callerId": "future-contract", **rate}]
                    result = self.normalized({kind: rate})["gateway"]["rateLimits"][kind]
                    self.assertEqual(result, dict(DEFAULT[kind], **rate))

    def test_non_objects_unknown_subjects_and_invalid_enabled_values_are_rejected(self):
        for limits in (None, [], True, 60, "enabled", {"unknown": {}}, {"enabled": True}):
            with self.subTest(limits=limits): self.assert_invalid(limits)
        for kind in KINDS:
            for rate in (None, [], True, 60, "enabled", {"unknown": True}):
                with self.subTest(kind=kind, rate=rate): self.assert_invalid({kind: rate})
            for enabled in (None, 0, 1, "true", []):
                with self.subTest(kind=kind, enabled=enabled): self.assert_invalid({kind: {"enabled": enabled}})

    def test_unimplemented_person_and_application_counters_cannot_be_enabled(self):
        for kind in ("user", "application"):
            with self.subTest(kind=kind):
                self.assert_invalid({kind: {"enabled": True}})
                self.assertFalse(self.normalized({kind: {"enabled": False}})["gateway"]["rateLimits"][kind]["enabled"])

    def test_calls_and_period_reject_non_integer_or_out_of_range_values_everywhere(self):
        for field, maximum in (("calls", 1000000), ("renewalPeriodSeconds", 300)):
            for value in (None, False, True, 0, -1, maximum + 1, 1.5, 60.0, "60", []):
                for kind in KINDS:
                    with self.subTest(field=field, value=value, kind=kind):
                        self.assert_invalid({kind: {field: value}})
                        if kind in CONTRACT_KINDS:
                            override = {"callerId": "a", "calls": 60, "renewalPeriodSeconds": 60, field: value}
                            self.assert_invalid({kind: {"overrides": [override]}})

    def test_user_does_not_accept_any_override_even_when_disabled(self):
        for enabled in (True, False):
            for overrides in ([], [{"callerId": "a", "calls": 1, "renewalPeriodSeconds": 1}]):
                with self.subTest(enabled=enabled, overrides=overrides):
                    self.assert_invalid({"user": {"enabled": enabled, "overrides": overrides}})


    def test_contract_overrides_require_all_known_fields_and_registered_id_string_rules(self):
        base = {"callerId": "contract-a", "calls": 10, "renewalPeriodSeconds": 30}
        for kind in CONTRACT_KINDS:
            for value in (None, "", " ", " contract-a", "contract-a ", "contract-a\n", "a" * 513, 123, []):
                with self.subTest(kind=kind, contract_id=value): self.assert_invalid({kind: {"overrides": [dict(base, callerId=value)]}})
            for field in base:
                invalid = dict(base); del invalid[field]
                with self.subTest(kind=kind, missing=field): self.assert_invalid({kind: {"overrides": [invalid]}})
            self.assert_invalid({kind: {"overrides": [dict(base, unexpectedField="unsupported")]}})
            for value in (None, {}, "a", [None], [[]]):
                with self.subTest(kind=kind, overrides=value): self.assert_invalid({kind: {"overrides": value}})

    def test_duplicate_contract_ids_are_rejected_within_each_independent_subject(self):
        first = {"callerId": "contract-a", "calls": 10, "renewalPeriodSeconds": 60}
        second = dict(first, calls=20)
        for kind in CONTRACT_KINDS:
            for duplicate in (dict(first), second, dict(first, renewalPeriodSeconds=30)):
                with self.subTest(kind=kind, duplicate=duplicate): self.assert_invalid({kind: {"overrides": [first, duplicate]}})
        limits = {"subscriptionKey": {"overrides": [first]}, "application": {"overrides": [second]}}
        result = self.normalized(limits)["gateway"]["rateLimits"]
        self.assertEqual(result["subscriptionKey"]["overrides"], [first])
        self.assertEqual(result["application"]["overrides"], [second])

    def test_future_contract_overrides_preserve_id_spelling_without_authorizing_callers(self):
        raw = api_fixture()
        original_auth = configuration.normalize_configuration(copy.deepcopy(raw))["apiDefinitions"][0]["authorization"]
        overrides = [{"callerId": contract, "calls": 17, "renewalPeriodSeconds": 30}
                     for contract in ("future-contract", "Contract-A", "contract-a", "部署 内の契約", "a" * 512)]
        raw["gateway"]["rateLimits"] = {kind: {"overrides": overrides} for kind in CONTRACT_KINDS}
        normalized = configuration.normalize_configuration(raw)
        for kind in CONTRACT_KINDS: self.assertEqual(normalized["gateway"]["rateLimits"][kind]["overrides"], overrides)
        self.assertEqual(normalized["apiDefinitions"][0]["authorization"], original_auth)

    def test_user_settings_are_shared_across_apis(self):
        raw = api_fixture()
        raw["apiDefinitions"].append(copy.deepcopy(raw["apiDefinitions"][0]))
        raw["apiDefinitions"][1].update(name="other-models", path="models/other/v1")
        result = configuration.normalize_configuration(raw, base_directory=CORE / "config")
        self.assertEqual(result["gateway"]["rateLimits"]["user"], DEFAULT["user"])

    def test_public_samples_validate_both_schemas_and_keep_optional_settings_out_of_params(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema is available in the integration venv")
        schemas = {name: json.loads((CORE / "schemas" / name).read_text()) for name in ("options.schema.json", "configuration.schema.json")}
        for schema in schemas.values(): jsonschema.Draft202012Validator.check_schema(schema)
        standard_limits = schemas["options.schema.json"]["properties"]["gateway"]["properties"]["rateLimits"]
        combined_limits = schemas["configuration.schema.json"]["properties"]["gateway"]["properties"]["rateLimits"]
        # 基本の保護値は同じ定義。企業の契約IDを含むoverridesだけparamsが所有します。
        for kind in KINDS:
            standard_fields = standard_limits["properties"][kind]["properties"]
            combined_fields = combined_limits["properties"][kind]["properties"]
            for field in ("enabled", "calls", "renewalPeriodSeconds"):
                self.assertEqual(standard_fields[field], combined_fields[field])
            self.assertNotIn("overrides", standard_fields)
        standard_default = {kind: {key: value for key, value in settings.items() if key != "overrides"}
                            for kind, settings in DEFAULT.items()}
        for name in ("test", "production"):
            with self.subTest(sample=name):
                options = json.loads((CORE / "config" / (name + ".json")).read_text())
                self.assertEqual(options["gateway"]["rateLimits"], standard_default)
                jsonschema.validate(options, schemas["options.schema.json"])
                manifest = CORE / "params" / (name + ".example.json")
                params = json.loads(manifest.read_text())
                for kind, settings in params["gateway"].get("rateLimits", {}).items():
                    self.assertIn(kind, CONTRACT_KINDS)
                    self.assertEqual(set(settings), {"overrides"})
                merged = configuration.load_configuration_source(manifest)
                for key in ("azureTenantId", "entraTenantId", "subscriptionId"): merged[key] = "11111111-1111-4111-8111-111111111111"
                local_schema_validator(schemas["configuration.schema.json"]).validate(merged)
                self.assertEqual(configuration.normalize_configuration(merged)["gateway"]["rateLimits"], DEFAULT)

    def test_schema_checks_subjects_and_user_override_boundaries(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema is available in the integration venv")
        for name in ("options.schema.json", "configuration.schema.json"):
            gateway = json.loads((CORE / "schemas" / name).read_text())["properties"]["gateway"]
            schema = gateway["properties"]["rateLimits"]
            jsonschema.validate({}, schema)
            for kind in KINDS:
                for invalid in ({"enabled": 1}, {"calls": 0}, {"calls": 1000001}, {"renewalPeriodSeconds": 301}, {"unknown": 1}):
                    with self.subTest(schema=name, kind=kind, invalid=invalid):
                        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({kind: invalid}, schema)
            for invalid in ({"user": {"overrides": []}}, {"unknown": {}}, {"user": {"enabled": True}}, {"application": {"enabled": True}}):
                with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(invalid, schema)

    def test_schema_checks_contract_override_boundaries_ids_and_unknown_fields(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema is available in the integration venv")
        schema = json.loads((CORE / "schemas/configuration.schema.json").read_text())["properties"]["gateway"]["properties"]["rateLimits"]
        base = {"callerId": "contract-a", "calls": 10, "renewalPeriodSeconds": 30}
        for kind in CONTRACT_KINDS:
            for contract in ("", " a", "a ", "a\n", "a" * 513):
                with self.subTest(kind=kind, contract=contract):
                    with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({kind: {"overrides": [dict(base, callerId=contract)]}}, schema)
            for entry in (dict(base, calls=0), dict(base, renewalPeriodSeconds=301), dict(base, unknown=True), {"callerId": "a"}):
                with self.subTest(kind=kind, entry=entry):
                    with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({kind: {"overrides": [entry]}}, schema)


if __name__ == "__main__": unittest.main()
