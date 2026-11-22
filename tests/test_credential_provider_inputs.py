"""合成した接続定義だけで、所有区分・名前照合・既存内部契約の保持を検証する。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock


sys.dont_write_bytecode = True
CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))

from deployment.helpers.credential_provider_inputs import compose_credential_providers
from deployment.helpers.credential_pools import physical_providers


class CompositionError(ValueError):
    """値を表示しない合成エラーを、normalizerに依存せず試験するための例外。"""


def fail(path, message):
    raise CompositionError(f"{path}: {message}")


class CredentialProviderInputsTests(unittest.TestCase):
    """架空の登録と標準契約を合成し、認証入力の移設で失ってはいけない値を確認する。"""

    def setUp(self):
        self.tenant = "11111111-1111-4111-8111-111111111111"
        self.options = {"graphDelegated": {
            "displayName": "Delegated Graph connection",
            "scopes": "openid profile email offline_access https://graph.microsoft.com/User.Read",
            "pool": {"count": 12, "capacity": 8000},
        }}
        self.registrations = {"graphDelegated": {
            "clientId": "22222222-2222-4222-8222-222222222222",
            "clientSecretFile": "./synthetic-secrets/graph-client.secret",
        }}

    def compose(self):
        return compose_credential_providers(
            self.options, self.registrations, self.tenant, fail=fail,
        )

    def add_oauth(self, name="Example-OAuth"):
        definition = {
            "name": name,
            "displayName": "Synthetic external OAuth connection",
            "identityProvider": "oauth2",
            "scopes": "read offline_access",
            "authorizationUrl": "https://identity.example.com/authorize",
            "tokenUrl": "https://identity.example.com/token",
            "refreshUrl": "https://identity.example.com/refresh",
            "identityEndpoint": "https://identity.example.com/me",
            "identityIdProperty": "subject",
            "connectionNames": ["example-service"],
        }
        registration = {
            "name": name,
            "clientId": "synthetic-string-client-id",
            "clientSecretFile": "./synthetic-secrets/external.secret",
        }
        self.options.setdefault("additional", []).append(definition)
        self.registrations.setdefault("additional", []).append(registration)
        return definition, registration

    def assert_failure(self, path):
        with self.assertRaises(CompositionError) as error:
            self.compose()
        self.assertEqual(str(error.exception).split(":", 1)[0], path)
        return str(error.exception)

    def test_standard_graph_reproduces_existing_provider_and_twelve_pools(self):
        expected = {
            **self.options["graphDelegated"],
            **self.registrations["graphDelegated"],
            "name": "graph-delegated",
            "identityProvider": "aad",
            "oauthTenantId": self.tenant,
            "resourceUri": "https://graph.microsoft.com",
            "identityEndpoint": "https://graph.microsoft.com/v1.0/me",
            "identityIdProperty": "id",
            "connectionNames": [],
        }
        providers = self.compose()
        self.assertEqual(providers, [expected])
        physical = physical_providers({"credentialProviders": providers})
        self.assertEqual([item["name"] for item in physical], [
            "graph-delegated-" + suffix for suffix in "abcdefghijkl"
        ])
        self.assertTrue(all(item["poolCapacity"] == 8000 for item in physical))

    def test_additional_inputs_default_to_empty_arrays(self):
        expected = self.compose()
        for target in (self.options, self.registrations):
            target["additional"] = []
            self.assertEqual(self.compose(), expected)

    def test_matches_names_independently_of_registration_order(self):
        first_definition, first_registration = self.add_oauth("Example-First")
        second_definition, second_registration = self.add_oauth("Example-Second")
        self.registrations["additional"].reverse()
        providers = self.compose()
        self.assertEqual(providers[1:], [
            {**first_definition, **first_registration},
            {**second_definition, **second_registration},
        ])

    def test_preserves_oauth_string_id_and_all_definition_fields(self):
        definition, registration = self.add_oauth()
        provider = self.compose()[1]
        self.assertEqual(provider, {**definition, **registration})
        self.assertEqual(provider["name"], "Example-OAuth")
        self.assertEqual(provider["clientId"], "synthetic-string-client-id")
        self.assertNotIn("oauthTenantId", provider)

    def test_standard_graph_client_id_rejects_non_guid_syntax_without_echoing_values(self):
        sentinel = "synthetic-private-client-that-must-not-appear"
        for value in (None, 7, [], {}, "", sentinel,
                      "22222222222242228222222222222222",
                      "{22222222-2222-4222-8222-222222222222}",
                      " 22222222-2222-4222-8222-222222222222 "):
            with self.subTest(value_kind=type(value).__name__):
                self.registrations["graphDelegated"]["clientId"] = value
                message = self.assert_failure("params.credentialProviders.graphDelegated.clientId")
                self.assertNotIn(sentinel, message)

    def test_standard_guid_syntax_keeps_template_marker_value_and_original_case(self):
        for value in ("00000000-0000-0000-0000-000000000000",
                      "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):
            with self.subTest(template_value=value.startswith("0000")):
                self.registrations["graphDelegated"]["clientId"] = value
                self.assertEqual(self.compose()[0]["clientId"], value)

    def test_additional_oauth_client_id_stays_a_general_string_after_standard_guid_validation(self):
        _, registration = self.add_oauth()
        registration["clientId"] = "external-client-synthetic@identity.example.com"
        self.assertEqual(self.compose()[1]["clientId"], registration["clientId"])

    def test_additional_aad_preserves_its_independent_tenant_and_pool(self):
        definition, registration = self.add_oauth("Example-Aad")
        definition["identityProvider"] = "aad"
        definition["resourceUri"] = "https://service.example.com"
        definition["identityEndpoint"] = "https://service.example.com/me"
        for field in ("authorizationUrl", "tokenUrl", "refreshUrl"):
            definition.pop(field)
        definition["pool"] = {"count": 2, "capacity": 4000}
        definition["connectionNames"] = []
        registration["oauthTenantId"] = "33333333-3333-4333-8333-333333333333"
        self.assertEqual(self.compose()[1], {**definition, **registration})
        self.assertNotEqual(self.compose()[1]["oauthTenantId"], self.tenant)

    def test_optional_additional_connections_default_without_overwriting_explicit_names(self):
        definition, _ = self.add_oauth()
        definition.pop("connectionNames")
        self.assertEqual(self.compose()[1]["connectionNames"], [])
        definition["connectionNames"] = ["fixed-synthetic-connection"]
        self.assertEqual(self.compose()[1]["connectionNames"], ["fixed-synthetic-connection"])

    def test_secret_references_are_retained_without_file_reads(self):
        _, registration = self.add_oauth()
        registration["clientSecretFile"] = "../synthetic-secrets/reference-only.secret"
        with mock.patch("builtins.open", side_effect=AssertionError("must not read files")), \
                mock.patch.object(Path, "open", side_effect=AssertionError("must not read files")):
            providers = self.compose()
        self.assertEqual(providers[0]["clientSecretFile"], self.registrations["graphDelegated"]["clientSecretFile"])
        self.assertEqual(providers[1]["clientSecretFile"], registration["clientSecretFile"])

    def test_inputs_and_mutable_output_fields_do_not_alias(self):
        self.add_oauth()
        original_options = copy.deepcopy(self.options)
        original_registrations = copy.deepcopy(self.registrations)
        providers = self.compose()
        self.assertEqual(self.options, original_options)
        self.assertEqual(self.registrations, original_registrations)
        providers[0]["pool"]["count"] = 1
        providers[1]["connectionNames"].append("another-synthetic-connection")
        self.assertEqual(self.options, original_options)
        self.assertEqual(self.registrations, original_registrations)
        self.assertEqual(self.compose()[0]["connectionNames"], [])

    def test_requires_both_standard_inputs_and_each_standard_field(self):
        for target_name, fields in (
            ("options", ("displayName", "scopes", "pool")),
            ("registrations", ("clientId", "clientSecretFile")),
        ):
            prefix = "config" if target_name == "options" else "params"
            target = getattr(self, target_name)
            original = copy.deepcopy(target)
            for field in fields:
                with self.subTest(target=target_name, field=field):
                    target["graphDelegated"].pop(field)
                    self.assert_failure(f"{prefix}.credentialProviders.graphDelegated.{field}")
                    target.clear()
                    target.update(copy.deepcopy(original))
            target.pop("graphDelegated")
            self.assert_failure(f"{prefix}.credentialProviders.graphDelegated")
            target.update(original)

    def test_rejects_fixed_standard_fields_from_each_public_owner(self):
        fixed = (
            "name", "identityProvider", "oauthTenantId", "resourceUri",
            "authorizationUrl", "tokenUrl", "refreshUrl", "connectionNames",
            "identityEndpoint", "identityIdProperty",
        )
        for target_name in ("options", "registrations"):
            target = getattr(self, target_name)["graphDelegated"]
            prefix = "config" if target_name == "options" else "params"
            for field in fixed:
                with self.subTest(target=target_name, field=field):
                    target[field] = "synthetic-private-value"
                    message = self.assert_failure(f"{prefix}.credentialProviders.graphDelegated")
                    self.assertNotIn("synthetic-private-value", message)
                    target.pop(field)

    def test_rejects_config_credentials_and_params_definition_fields(self):
        for field in ("clientId", "clientSecretFile", "oauthTenantId"):
            with self.subTest(owner="config", field=field):
                self.options["graphDelegated"][field] = "synthetic-private-value"
                self.assert_failure("config.credentialProviders.graphDelegated")
                self.options["graphDelegated"].pop(field)
        for field in ("displayName", "scopes", "pool"):
            with self.subTest(owner="params", field=field):
                self.registrations["graphDelegated"][field] = "synthetic-private-value"
                self.assert_failure("params.credentialProviders.graphDelegated")
                self.registrations["graphDelegated"].pop(field)
        definition, registration = self.add_oauth()
        for field in ("clientId", "clientSecretFile", "oauthTenantId"):
            with self.subTest(owner="additional config", field=field):
                definition[field] = "synthetic-private-value"
                self.assert_failure("config.credentialProviders.additional[0]")
                definition.pop(field)
        for field in definition.keys() - {"name"}:
            with self.subTest(owner="additional params", field=field):
                registration[field] = "synthetic-private-value"
                self.assert_failure("params.credentialProviders.additional[0]")
                registration.pop(field)

    def test_rejects_missing_and_unmatched_additional_registrations(self):
        self.add_oauth()
        self.registrations["additional"] = []
        self.assert_failure("config.credentialProviders.additional[0].name")
        self.options["additional"] = []
        self.registrations["additional"] = [{
            "name": "unknown-synthetic-provider",
            "clientId": "synthetic-private-id",
            "clientSecretFile": "synthetic-private-secret-reference",
        }]
        message = self.assert_failure("params.credentialProviders.additional[0].name")
        for value in self.registrations["additional"][0].values():
            self.assertNotIn(value, message)

    def test_matching_is_exact_even_though_duplicate_detection_is_case_insensitive(self):
        _, registration = self.add_oauth()
        registration["name"] = registration["name"].lower()
        self.assert_failure("params.credentialProviders.additional[0].name")

    def test_rejects_duplicate_names_and_standard_collisions_in_each_owner(self):
        self.add_oauth()
        for target_name in ("options", "registrations"):
            target = getattr(self, target_name)["additional"]
            prefix = "config" if target_name == "options" else "params"
            for duplicate_name in ("Example-OAuth", "example-oauth"):
                with self.subTest(target=target_name, name=duplicate_name):
                    duplicate = copy.deepcopy(target[0])
                    duplicate["name"] = duplicate_name
                    target.append(duplicate)
                    self.assert_failure(f"{prefix}.credentialProviders.additional[1].name")
                    target.pop()
            original_name = target[0]["name"]
            for standard_name in ("graph-delegated", "GRAPH-DELEGATED"):
                with self.subTest(target=target_name, name=standard_name):
                    target[0]["name"] = standard_name
                    self.assert_failure(f"{prefix}.credentialProviders.additional[0].name")
            target[0]["name"] = original_name

    def test_rejects_missing_additional_display_name_in_config(self):
        definition, _ = self.add_oauth()
        definition.pop("displayName")
        self.assert_failure("config.credentialProviders.additional[0].displayName")

    def test_rejects_unknown_top_level_keys_without_echoing_private_field_names(self):
        for target_name in ("options", "registrations"):
            target = getattr(self, target_name)
            prefix = "config" if target_name == "options" else "params"
            target["synthetic-private-field"] = "synthetic-private-value"
            message = self.assert_failure(f"{prefix}.credentialProviders")
            self.assertNotIn("synthetic-private-field", message)
            self.assertNotIn("synthetic-private-value", message)
            target.pop("synthetic-private-field")

    def test_rejects_bad_container_types_and_unusable_matching_names(self):
        for target_name in ("options", "registrations"):
            target = getattr(self, target_name)
            prefix = "config" if target_name == "options" else "params"
            for value in (None, {}, "synthetic-not-an-array"):
                with self.subTest(target=target_name, value=value):
                    target["additional"] = value
                    self.assert_failure(f"{prefix}.credentialProviders.additional")
            target.pop("additional")
        definition, registration = self.add_oauth()
        for target, prefix in ((definition, "config"), (registration, "params")):
            original_name = target["name"]
            for value in (None, 7, "", "   ", " Synthetic-Name "):
                with self.subTest(owner=prefix, value=value):
                    target["name"] = value
                    self.assert_failure(f"{prefix}.credentialProviders.additional[0].name")
            target["name"] = original_name

    def test_requires_complete_pool_metadata_and_rejects_extra_pool_fields(self):
        pool = self.options["graphDelegated"]["pool"]
        for key in ("count", "capacity"):
            original = pool.pop(key)
            self.assert_failure(f"config.credentialProviders.graphDelegated.pool.{key}")
            pool[key] = original
        pool["synthetic-private-field"] = "synthetic-private-value"
        self.assert_failure("config.credentialProviders.graphDelegated.pool")


if __name__ == "__main__":
    unittest.main()
