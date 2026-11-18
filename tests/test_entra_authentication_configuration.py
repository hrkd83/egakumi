"""同一APIへの委任認証設定と、未検証の購読解除を拒否する公開境界を検証する。

独立した合成設定と偽transportだけを使う。実Entra/APIMの接続・キー検証・
分散counterやSSEの動作を確認したことにはしない。
"""
import copy
import itertools
import json
import time
import unittest
from unittest import mock

from configuration_fixtures import CORE, api_fixture, configuration, fixture, connection_fixture
import test_publish_apis as publication_tests
from schema_helpers import local_schema_validator
from deployment.helpers.apim_authentication import (
    common_authentication_settings, entra_authentication_settings, _entra_api_names,
    entra_authentication_named_values, entra_named_value_settings, ENTRA_NAMED_VALUES,
)

from deployment.helpers.apim_common_authentication import render_common_authentication_fragments

publish = publication_tests.publish


def add_common_authentication_inventory(fixture_test):
    """分離したSTEP4の本文を、既存のキー経路と同じ合成ARMへ登録する。"""
    for name, value in render_common_authentication_fragments(entra_enabled=False).items():
        resource_id = fixture_test.plan["apimId"] + "/policyFragments/" + name
        fixture_test.inventory[resource_id] = publication_tests.ok({
            "properties": {"format": "rawxml", "value": value}})


def entra_fixture():
    """社員向け委任APIと本人別枠を表す完全な公開設定の合成値を返す。"""
    return {
        "audience": "11111111-1111-4111-8111-111111111111",
        "requiredScope": "Gateway.Invoke",
        "userLimits": {
            "requestRate": {"calls": 60, "renewalPeriodSeconds": 60},
            "tokensPerMinute": 10000,
            "tokenQuota": {"tokens": 100000, "period": "Daily"},
        },
    }


class EntraConfigurationTests(unittest.TestCase):
    def normalize(self, settings, raw=None):
        raw = api_fixture() if raw is None else raw
        raw["gateway"]["authentication"]["entra"] = copy.deepcopy(settings)
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_omission_preserves_legacy_configuration_without_inserting_a_toggle(self):
        raw = api_fixture()
        raw["gateway"]["authentication"].pop("entra", None)
        original = copy.deepcopy(raw)
        legacy = configuration.normalize_configuration(raw, base_directory=CORE / "config")
        self.assertEqual(raw, original)
        self.assertNotIn("entra", legacy["gateway"]["authentication"])
        self.assertEqual(entra_authentication_settings(legacy), {"enabled": False})
        for incomplete in ({}, None):
            with self.subTest(incomplete=incomplete), self.assertRaises(configuration.ConfigurationError):
                self.normalize(incomplete, copy.deepcopy(raw))

    def test_canonical_resource_and_authentication_tenant_do_not_change_azure_tenant(self):
        raw = api_fixture()
        raw["entraTenantId"] = "22222222-2222-4222-8222-222222222222"
        settings = entra_fixture()
        settings["audience"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        settings["requiredScope"] = "Gateway_Invoke-v2"
        actual = self.normalize(settings, raw)
        normalized = actual["gateway"]["authentication"]["entra"]
        self.assertEqual(normalized["audience"], settings["audience"].lower())
        self.assertEqual(normalized["requiredScope"], "Gateway_Invoke-v2")
        self.assertEqual(actual["entraTenantId"], raw["entraTenantId"])
        self.assertNotEqual(actual["entraTenantId"], actual["azureTenantId"])
        self.assertNotIn("tenantId", normalized)
        self.assertNotIn("enabled", normalized)
        self.assertNotIn("apiNames", normalized)
        self.assertEqual(raw["gateway"]["authentication"]["entra"], settings)

    def test_present_definition_requires_resource_scope_and_explicit_user_limits(self):
        for field in ("audience", "requiredScope", "userLimits"):
            settings = entra_fixture()
            settings.pop(field)
            with self.subTest(missing=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)
        for field in ("requestRate", "tokensPerMinute", "tokenQuota"):
            settings = entra_fixture()
            settings["userLimits"].pop(field)
            with self.subTest(missing_limit=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)

    def test_each_explicit_null_disables_only_that_limit_without_defaulting_or_mutating_input(self):
        names = ("requestRate", "tokensPerMinute", "tokenQuota")
        for disabled in itertools.product((False, True), repeat=len(names)):
            settings = entra_fixture()
            for name, disable in zip(names, disabled):
                if disable:
                    settings["userLimits"][name] = None
            original = copy.deepcopy(settings)
            with self.subTest(disabled=disabled):
                actual = self.normalize(settings)
                self.assertEqual(actual["gateway"]["authentication"]["entra"]["userLimits"], settings["userLimits"])
                self.assertEqual(settings, original)

    def test_request_rate_only_accepts_provisional_bulk_request_guard(self):
        settings = entra_fixture()
        settings["userLimits"] = {
            "requestRate": {"calls": 1000, "renewalPeriodSeconds": 60},
            "tokensPerMinute": None, "tokenQuota": None,
        }
        actual = self.normalize(settings)
        self.assertEqual(actual["gateway"]["authentication"]["entra"]["userLimits"], settings["userLimits"])

    def test_limit_objects_reject_missing_empty_unknown_fields_even_with_other_limits_disabled(self):
        fields = {"requestRate": ("calls", "renewalPeriodSeconds"), "tokenQuota": ("tokens", "period")}
        for name, required in fields.items():
            for missing in required:
                settings = entra_fixture()
                limits = settings["userLimits"]
                for other in limits:
                    if other != name:
                        limits[other] = None
                limits[name].pop(missing)
                with self.subTest(name=name, missing=missing), self.assertRaises(configuration.ConfigurationError):
                    self.normalize(settings)
        for path in ((), ("requestRate",), ("tokenQuota",)):
            for malformed in (None, {}, [], "SENTINEL", False):
                if path and malformed is None:
                    continue  # 各limitそのもののnullだけが明示的な無効化です。
                settings = entra_fixture()
                if path:
                    settings["userLimits"][path[0]] = malformed
                else:
                    settings["userLimits"] = malformed
                with self.subTest(path=path, malformed=malformed), self.assertRaises(configuration.ConfigurationError):
                    self.normalize(settings)
            settings = entra_fixture()
            target = settings["userLimits"] if not path else settings["userLimits"][path[0]]
            target["unknownLimit"] = True
            with self.subTest(path=path, unknown=True), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)

    def test_finite_limits_reject_bool_zero_negative_noninteger_and_overflow(self):
        cases = (
            (("requestRate", "calls"), 1000000),
            (("requestRate", "renewalPeriodSeconds"), 300),
            (("tokensPerMinute",), 2147483647),
            (("tokenQuota", "tokens"), 2147483647),
        )
        for path, maximum in cases:
            invalid_values = (True, False, 0, -1, 1.5, "60", maximum + 1)
            if len(path) > 1:
                invalid_values += (None,)
            for invalid in invalid_values:
                settings = entra_fixture()
                target = settings["userLimits"]
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = invalid
                with self.subTest(path=path, invalid=invalid), self.assertRaises(configuration.ConfigurationError):
                    self.normalize(settings)
            for valid in (1, maximum):
                settings = entra_fixture()
                target = settings["userLimits"]
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = valid
                self.normalize(settings)

    def test_quota_period_is_required_and_case_sensitive(self):
        for invalid in (None, "daily", "Unlimited", 1, True):
            settings = entra_fixture()
            settings["userLimits"]["tokenQuota"]["period"] = invalid
            with self.subTest(period=invalid), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)
        for valid in ("Hourly", "Daily", "Weekly", "Monthly", "Yearly"):
            settings = entra_fixture()
            settings["userLimits"]["tokenQuota"]["period"] = valid
            self.normalize(settings)

    def test_invalid_resource_and_scope_are_rejected_without_echoing_private_input(self):
        for value in ("api://SENTINEL", "SENTINEL", "00000000-0000-0000-0000-000000000000", None, {}):
            settings = entra_fixture()
            settings["audience"] = value
            with self.subTest(value=value), self.assertRaises(configuration.ConfigurationError) as error:
                self.normalize(settings)
            self.assertNotIn("SENTINEL", str(error.exception))
        for scope in ("api://SENTINEL/Gateway.Invoke", "Gateway.Invoke offline_access", "Gateway/Invoke", "A" * 129):
            settings = entra_fixture()
            settings["requiredScope"] = scope
            with self.subTest(scope=scope), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)

    def test_unknown_fields_cannot_supply_activation_proof_or_user_identity(self):
        for field in ("enabled", "allowedClientIds", "apiNames", "tenantId", "verified", "activationProof", "oid", "groups", "clientSecret", "userGrants"):
            settings = entra_fixture()
            settings[field] = True
            with self.subTest(field=field), self.assertRaises(configuration.ConfigurationError):
                self.normalize(settings)

    def test_mapper_adds_only_internal_state_without_mutating_public_configuration(self):
        actual = self.normalize(entra_fixture())
        original = copy.deepcopy(actual)
        settings = entra_authentication_settings(actual)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["tenantId"], actual["entraTenantId"])
        self.assertEqual(settings["apiNames"], ["models"])
        self.assertNotIn("allowedClientIds", settings)
        self.assertEqual(actual, original)
        public = configuration.build_bicep_configuration(actual)["gateway"]["authentication"]["entra"]
        self.assertEqual(set(public), {"audience", "requiredScope", "userLimits"})

    def test_foundation_and_publication_share_only_four_canonical_named_values(self):
        actual = self.normalize(entra_fixture())
        original = copy.deepcopy(actual)
        values = entra_named_value_settings(actual)
        self.assertEqual(tuple(values), ENTRA_NAMED_VALUES)
        self.assertEqual(values, entra_authentication_named_values(entra_authentication_settings(actual)))
        self.assertEqual(values["core-entra-tenant-id"], actual["entraTenantId"])
        self.assertEqual(json.loads(values["core-entra-user-limits"]), entra_fixture()["userLimits"])
        self.assertEqual(actual, original)
        actual["gateway"]["authentication"].pop("entra")
        self.assertEqual(entra_named_value_settings(actual), {})

    def test_mapper_rechecks_incomplete_or_disallowed_public_input(self):
        for missing in ("audience", "requiredScope", "userLimits"):
            raw = api_fixture()
            raw["gateway"]["authentication"]["entra"] = entra_fixture()
            del raw["gateway"]["authentication"]["entra"][missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                entra_authentication_settings(raw)
        for field, value in (("enabled", True), ("enabled", False), ("allowedClientIds", []),
                             ("apiNames", ["models"]), ("tenantId", "SENTINEL"), ("unknown", True)):
            raw = api_fixture()
            raw["gateway"]["authentication"]["entra"] = {**entra_fixture(), field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                entra_authentication_settings(raw)
        for malformed in ({}, None):
            raw = api_fixture()
            raw["gateway"]["authentication"]["entra"] = malformed
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                entra_authentication_settings(raw)

    def test_internal_headers_cannot_be_reconfigured_as_a_subscription_key(self):
        for header in ("X-Core-Caller-Id", "x-core-caller-id", "X-Core-Subscription-Id", "x-core-subscription-id",
                       "X-Ms-Client-Request-Id", "x-ms-client-request-id", "X-Appgw-Trace-Id", "x-appgw-trace-id",
                       "X-Core-Connection-Custom", "x-core-connection-custom"):
            raw = api_fixture()
            raw["gateway"]["authentication"]["subscriptionKeyHeader"] = header
            raw["apiDefinitions"][0]["subscriptionKeyHeader"] = header.lower()
            with self.subTest(header=header):
                with self.assertRaises(configuration.ConfigurationError):
                    configuration.normalize_configuration(raw)
                with self.assertRaises(ValueError):
                    common_authentication_settings(raw)
                # APIだけで予約名へ切り替えても、共通の専用キー名との不一致を拒否する。
                api_override = api_fixture()
                api_override["gateway"]["authentication"]["subscriptionKeyHeader"] = "api-key"
                api_override["apiDefinitions"][0]["subscriptionKeyHeader"] = header
                with self.assertRaises(configuration.ConfigurationError):
                    configuration.normalize_configuration(api_override)

    def test_standard_targets_are_generated_from_canonical_definitions_without_backend_resolution(self):
        from standard_apis import API_DEFINITION_DIRECTORY, load_api_definitions
        actual = self.normalize(entra_fixture(), fixture())
        original = copy.deepcopy(actual)
        with mock.patch("standard_apis._bindings", side_effect=AssertionError("no endpoint or state resolution")):
            settings = entra_authentication_settings(actual)
        definitions = load_api_definitions(API_DEFINITION_DIRECTORY)
        expected = [name for api in definitions for name in
                    ([api["name"]] + ([api["mcp"]["name"]] if "mcp" in api else []))]
        self.assertEqual(settings["apiNames"], expected)
        self.assertNotIn("credential-manager-login", settings["apiNames"])
        self.assertEqual(actual, original)

    def test_custom_targets_include_all_transports_and_generated_mcp_and_reject_ambiguous_ids(self):
        definitions = [{"name": kind + "-business", "apiType": kind} for kind in ("http", "websocket", "mcp", "grpc")]
        definitions[0]["mcp"] = {"name": "generated-business-mcp"}
        self.assertEqual(_entra_api_names(definitions),
                         ["http-business", "generated-business-mcp", "websocket-business", "mcp-business", "grpc-business"])
        for malformed in (None, [None], [{"name": "bad/api"}], [{"name": "same"}, {"name": "SAME"}],
                          [{"name": "same", "mcp": {"name": "same"}}], [{"name": "business", "mcp": None}]):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                _entra_api_names(malformed)

    def test_configured_credential_login_adds_only_the_protected_utility_api_without_resolution(self):
        raw = connection_fixture()
        raw["gateway"]["authentication"]["entra"] = entra_fixture()
        normalized = self.normalize(entra_fixture(), copy.deepcopy(raw))
        for config in (raw, normalized):
            original = copy.deepcopy(config)
            with self.subTest(normalized=config is normalized), mock.patch(
                    "deployment.helpers.credential_login.settings", side_effect=AssertionError("no backend or secret resolution")):
                settings = entra_authentication_settings(config)
            self.assertEqual(settings["apiNames"], ["profile", "credential-manager-login"])
            self.assertNotIn("credential-manager-browser", settings["apiNames"])
            self.assertEqual(config, original)
        raw["gateway"]["authentication"]["credentialLogin"] = None
        self.assertEqual(entra_authentication_settings(raw)["apiNames"], ["profile"])

    def test_configured_credential_login_rejects_business_and_generated_mcp_id_collisions(self):
        for reserved in ("credential-manager-login", "CREDENTIAL-MANAGER-LOGIN", "credential-manager-browser"):
            for definitions in ([{"name": reserved}], [{"name": "business", "mcp": {"name": reserved}}]):
                with self.subTest(reserved=reserved, definitions=definitions), self.assertRaises(ValueError):
                    _entra_api_names(definitions, credential_login_configured=True)

    def test_standard_targets_append_credential_login_without_including_browser_operations(self):
        definitions = [{"name": "business", "mcp": {"name": "business-mcp"}}]
        with mock.patch("standard_apis.load_api_definitions", return_value=definitions):
            self.assertEqual(_entra_api_names([], credential_login_configured=True),
                             ["business", "business-mcp", "credential-manager-login"])


class EntraSchemaTests(unittest.TestCase):
    def test_parameters_own_authentication_and_configuration_reuses_local_definition(self):
        params = json.loads((CORE / "schemas/params.schema.json").read_text(encoding="utf-8"))
        combined = json.loads((CORE / "schemas/configuration.schema.json").read_text(encoding="utf-8"))
        options = json.loads((CORE / "schemas/options.schema.json").read_text(encoding="utf-8"))
        auth = lambda schema: schema["properties"]["gateway"]["properties"]["authentication"]
        self.assertEqual(auth(params)["properties"]["entra"], {"$ref": "#/$defs/entraAuthentication"})
        self.assertEqual(auth(combined)["properties"]["entra"], {"$ref": "params.schema.json#/$defs/entraAuthentication"})
        self.assertNotIn("authentication", options["properties"]["gateway"]["properties"])
        self.assertIn("entra", configuration.PARAMETER_GATEWAY_NESTED_KEYS["authentication"])

    def test_local_schema_requires_complete_definition_without_a_public_toggle(self):
        params = json.loads((CORE / "schemas/params.schema.json").read_text(encoding="utf-8"))
        schema = {"$schema": params["$schema"], "$ref": "params.schema.json#/$defs/entraAuthentication"}
        try:
            validator = local_schema_validator(schema)
        except ImportError as error:
            self.skipTest(str(error))
        for valid in (entra_fixture(),):
            with self.subTest(valid=valid):
                self.assertEqual(list(validator.iter_errors(valid)), [])
        for field in ("audience", "requiredScope", "userLimits"):
            invalid = entra_fixture()
            invalid.pop(field)
            with self.subTest(missing=field):
                self.assertTrue(list(validator.iter_errors(invalid)))
        for invalid in ({}, None, {"userLimits": {}}, {"tenantId": "SENTINEL"}):
            self.assertTrue(list(validator.iter_errors(invalid)))
        for value in (True, False, 1):
            invalid = {**entra_fixture(), "enabled": value}
            with self.subTest(public_enabled=value):
                self.assertTrue(list(validator.iter_errors(invalid)))
        for field, value in (("allowedClientIds", []), ("apiNames", [])):
            with self.subTest(removed_field=field):
                self.assertTrue(list(validator.iter_errors({**entra_fixture(), field: value})))

    def test_params_and_combined_schema_accept_each_null_and_reject_incomplete_or_invalid_limits(self):
        params = json.loads((CORE / "schemas/params.schema.json").read_text(encoding="utf-8"))
        combined = json.loads((CORE / "schemas/configuration.schema.json").read_text(encoding="utf-8"))
        references = {
            "params": {"$ref": "params.schema.json#/$defs/entraAuthentication"},
            "configuration": combined["properties"]["gateway"]["properties"]["authentication"]["properties"]["entra"],
        }
        for source, reference in references.items():
            try:
                validator = local_schema_validator({"$schema": params["$schema"], **reference})
            except ImportError as error:
                self.skipTest(str(error))
            names = ("requestRate", "tokensPerMinute", "tokenQuota")
            for disabled in itertools.product((False, True), repeat=len(names)):
                settings = entra_fixture()
                for name, disable in zip(names, disabled):
                    if disable:
                        settings["userLimits"][name] = None
                with self.subTest(source=source, disabled=disabled):
                    self.assertEqual(list(validator.iter_errors(settings)), [])
            for missing in names:
                settings = entra_fixture()
                settings["userLimits"].pop(missing)
                with self.subTest(source=source, missing=missing):
                    self.assertTrue(list(validator.iter_errors(settings)))
            invalid_limits = (
                None, {}, {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None, "unknownLimit": True},
                {"requestRate": {}, "tokensPerMinute": None, "tokenQuota": None},
                {"requestRate": {"calls": None, "renewalPeriodSeconds": 60}, "tokensPerMinute": None, "tokenQuota": None},
                {"requestRate": {"calls": 1000001, "renewalPeriodSeconds": 60}, "tokensPerMinute": None, "tokenQuota": None},
                {"requestRate": {"calls": 1000, "renewalPeriodSeconds": 301}, "tokensPerMinute": None, "tokenQuota": None},
                {"requestRate": None, "tokensPerMinute": True, "tokenQuota": None},
                {"requestRate": None, "tokensPerMinute": 0, "tokenQuota": None},
                {"requestRate": None, "tokensPerMinute": 2147483648, "tokenQuota": None},
                {"requestRate": None, "tokensPerMinute": None, "tokenQuota": {}},
                {"requestRate": None, "tokensPerMinute": None, "tokenQuota": {"tokens": None, "period": "Monthly"}},
                {"requestRate": None, "tokensPerMinute": None, "tokenQuota": {"tokens": 2147483648, "period": "Monthly"}},
                {"requestRate": None, "tokensPerMinute": None, "tokenQuota": {"tokens": 1, "period": "Unlimited"}},
                {"requestRate": None, "tokensPerMinute": None, "tokenQuota": {"tokens": 1, "period": "Monthly", "unknownLimit": True}},
            )
            for limits in invalid_limits:
                settings = entra_fixture()
                settings["userLimits"] = limits
                with self.subTest(source=source, invalid_limits=limits):
                    self.assertTrue(list(validator.iter_errors(settings)))


class EntraPublicationBoundaryTests(unittest.TestCase):
    def test_omitted_feature_preserves_api_urls_products_keys_and_all_deployment_parameters(self):
        config, state = publication_tests.publication_fixture()
        legacy = publish.prepare_plan(config, state)
        original = copy.deepcopy(config)
        repeated = publish.prepare_plan(config, state)
        self.assertEqual(repeated["parameters"], legacy["parameters"])
        self.assertEqual(repeated["publishedApiIds"], legacy["publishedApiIds"])
        self.assertEqual(repeated["releasedProduct"], legacy["releasedProduct"])
        self.assertEqual(repeated["commonAuthentication"], legacy["commonAuthentication"])
        self.assertEqual(repeated["entraAuthentication"], {"enabled": False})
        self.assertNotIn("entra", config["gateway"]["authentication"])
        self.assertEqual(config, original)

    def test_enabled_plan_requires_generated_source_baseline_before_any_mutation(self):
        fixture_test = publication_tests.PublicationTests()
        fixture_test.setUp()
        add_common_authentication_inventory(fixture_test)
        config, state = fixture_test.config, fixture_test.state
        config["entraTenantId"] = "55555555-5555-4555-8555-555555555555"
        config["gateway"]["authentication"]["entra"] = entra_fixture()
        config["gateway"]["authentication"]["entra"]["userLimits"] = {
            "requestRate": {"calls": 1000, "renewalPeriodSeconds": 60},
            "tokensPerMinute": None, "tokenQuota": None,
        }
        approved = copy.deepcopy(api_fixture()["apiDefinitions"][0])
        approved["name"] = "model"
        config["apiDefinitions"][0] = approved
        original = copy.deepcopy(config)
        plan = publish.prepare_plan(config, state)
        self.assertTrue(plan["parameters"]["entraAuthentication"]["value"]["enabled"])
        self.assertEqual(plan["publishedApiIds"], fixture_test.plan["publishedApiIds"])
        self.assertEqual(config, original)
        policy = fixture_test.inventory[plan["apimId"] + "/policies/policy"]["body"]
        policy["etag"] = '"fixture-entra-etag"'
        for name, value in entra_authentication_named_values(plan["entraAuthentication"]).items():
            resource_id = plan["apimId"] + "/namedValues/" + name
            fixture_test.inventory[resource_id] = publication_tests.ok({
                "id": resource_id, "properties": {"displayName": name, "secret": False, "value": value},
            })
        transport = publication_tests.Transport(fixture_test.inventory)
        with self.assertRaisesRegex(ValueError, "protected generated source is missing"):
            publish.execute_plan(config, state, plan, fixture_test.template, transport, sleep=lambda _: None)
        self.assertTrue(transport.calls)
        self.assertTrue(all(method == "GET" for method, _, _, _ in transport.calls))
        self.assertTrue(any(path.split("?", 1)[0] == plan["deploymentId"] for _, path, _, _ in transport.calls))

    def test_configuration_rejects_public_activation_and_enabled_controls(self):
        for field, value in (("activationProof", True), ("enabled", True), ("enabled", False), ("enabled", 1)):
            raw = api_fixture()
            raw["gateway"]["authentication"]["entra"] = entra_fixture()
            raw["gateway"]["authentication"]["entra"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(configuration.ConfigurationError, "unsupported field"):
                configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_internal_legacy_settings_reach_merge_and_readback_validation_without_subscription_changes(self):
        fixture_test = publication_tests.PublicationTests()
        fixture_test.setUp()
        add_common_authentication_inventory(fixture_test)
        plan = publish.prepare_plan(fixture_test.config, fixture_test.state)
        transport = publication_tests.Transport(fixture_test.inventory)
        with mock.patch.object(publish, "merge_common_authentication_policy", wraps=publish.merge_common_authentication_policy) as merge:
            prepared = publish.verify_common_authentication(plan, transport)
        self.assertEqual(merge.call_args.kwargs["entra"], plan["entraAuthentication"])
        self.assertFalse(prepared["changed"])
        with mock.patch.object(publish, "validate_common_authentication_policy", wraps=publish.validate_common_authentication_policy) as validate:
            publish.install_common_authentication(prepared, transport, sleep=lambda _: None, deadline=time.monotonic() + 2)
        self.assertEqual(validate.call_args.kwargs["entra"], plan["entraAuthentication"])
        self.assertTrue(all(method == "GET" for method, _, _, _ in transport.calls))


if __name__ == "__main__":
    unittest.main()
