"""公開の分離入力を架空値で読み、固定資材と所有区分の境界を検証する。

実環境の入力やstateには触れず、公開サンプルを一時フォルダーへ展開する。
Azureの操作や秘密ファイルの読込は行わない。
"""
import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from tests import REPOSITORY


sys.dont_write_bytecode = True
CORE = REPOSITORY

from deployment.helpers import configuration
from deployment.helpers.credential_pools import logical_profiles, physical_providers
from catalog.standard_apis import DEFINITION_FILES, load_api_definitions
from tests.support.configuration_fixtures import connection_fixture


def put(target, path, value):
    """試験で指定した既知の欄へだけ、独立した値を設定する。"""
    parent = target
    for key in path[:-1]:
        parent = parent.setdefault(key, {})
    parent[path[-1]] = copy.deepcopy(value)


class FixedInitialConfigurationTests(unittest.TestCase):
    """paramsの識別情報とconfigの動作設定から、同じ初期構成を生成できるか調べる。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.manifest = self.directory / "company-input" / "params.json"
        self.options_file = self.directory / "shared-options" / "options.json"
        self.manifest.parent.mkdir()
        self.options_file.parent.mkdir()
        self.parameters = json.loads((CORE / "params/test.example.json").read_text(encoding="utf-8"))
        self.parameters["gateway"]["authentication"].pop("credentialLogin")
        self.options = json.loads((CORE / "infra/config/test.json").read_text(encoding="utf-8"))
        self.parameters["configFile"] = "../shared-options/options.json"
        self.parameters["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
        self.parameters["azureTenantId"] = "22222222-2222-4222-8222-222222222222"
        self.parameters["entraTenantId"] = self.parameters["azureTenantId"]
        self.parameters["services"]["apimInstanceId"] = "abc123"
        self.parameters["gateway"]["authentication"]["entra"]["audience"] = "44444444-4444-4444-8444-444444444444"
        self.parameters["credentialProviders"]["graphDelegated"]["clientId"] = "55555555-5555-4555-8555-555555555555"
        self.parameters["credentialProviders"]["graphDelegated"]["clientSecretFile"] = "./reference-only/graph.secret"
        self.parameters["management"]["sshPublicKeyFile"] = "./reference-only/management.pub"
        self.parameters["certificate"]["azureDnsSubscriptionId"] = self.parameters["subscriptionId"]
        self.parameters["certificate"]["azureDnsManagedIdentityResourceId"] = (
            "/subscriptions/" + self.parameters["subscriptionId"]
            + "/resourceGroups/rg-dns-synthetic/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-synthetic"
        )

    def write_inputs(self):
        for path, value in ((self.manifest, self.parameters), (self.options_file, self.options)):
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def source(self):
        self.write_inputs()
        return configuration.load_configuration_source(self.manifest)

    def normalized(self):
        self.write_inputs()
        return configuration.load_configuration(self.manifest)

    def assert_rejected(self, path, forbidden_value=None):
        with self.assertRaises(configuration.ConfigurationError) as error:
            self.source()
        message = str(error.exception)
        self.assertIn(path, message)
        if forbidden_value is not None:
            self.assertNotIn(forbidden_value, message)

    def canonical_products(self):
        return sorted({api["productId"] for filename in DEFINITION_FILES
                       for api in load_api_definitions(filename)})

    def test_public_sources_normalize_without_reading_referenced_secrets(self):
        self.write_inputs()
        with mock.patch.object(configuration, "load_secret_inputs", side_effect=AssertionError("secret reads forbidden")), \
                mock.patch.object(configuration, "load_ssh_public_key", side_effect=AssertionError("SSH reads forbidden")):
            actual = configuration.load_configuration(self.manifest)
        self.assertTrue(actual["publishApis"])
        self.assertEqual(actual["apiPublicationStatus"], "configured")
        self.assertEqual(actual["apiDefinitions"], [])
        self.assertEqual(actual["credentialProviders"][0]["oauthTenantId"], self.parameters["entraTenantId"])
        self.assertEqual(len(physical_providers(actual)), 12)

    def test_rejects_every_reviewed_fixed_input_even_when_empty(self):
        fixed_paths = (
            ("authorization",), ("apiDefinitions",),
            ("network", "firewall", "snatPrivateRanges"),
            ("network", "firewall", "idpsPrivateRanges"),
            ("network", "firewall", "bypassTrafficSettings"),
            ("gateway", "existingWafPolicyId"),
            ("gateway", "authentication", "subscriptionKeyHeader"),
            ("gateway", "authentication", "entra", "requiredScope"),
            ("gateway", "authentication", "credentialLogin", "identity", "tenantId"),
            ("monitoring", "auditAllowedIpAddresses"),
            ("monitoring", "auditReaders"),
            ("certificate", "domainNames"), ("certificate", "plugin"),
        )
        for target_name, owner in (("parameters", "params"), ("options", "config")):
            original = copy.deepcopy(getattr(self, target_name))
            for path in fixed_paths:
                with self.subTest(owner=owner, path=".".join(path)):
                    target = copy.deepcopy(original)
                    put(target, path, [])
                    setattr(self, target_name, target)
                    self.assert_rejected(owner + "." + ".".join(path))
            setattr(self, target_name, original)

    def test_public_config_cannot_change_automatic_api_publication(self):
        for target_name, owner in (("parameters", "params"), ("options", "config")):
            target = getattr(self, target_name)
            for value in (False, True, None):
                with self.subTest(owner=owner, value=value):
                    target["publishApis"] = value
                    self.assert_rejected(owner + ".publishApis")
            target.pop("publishApis")

    def test_authentication_combines_shared_policy_with_registration_and_fixed_contract(self):
        identity = {
            "clientId": "77777777-7777-4777-8777-777777777777",
            "clientSecretFile": "./reference-only/broker.secret",
        }
        put(self.parameters, ("gateway", "authentication", "credentialLogin", "identity"), identity)
        actual = self.normalized()
        authentication = actual["gateway"]["authentication"]
        self.assertEqual(authentication["subscriptionKeyHeader"], "api-key")
        self.assertEqual(authentication["entra"]["requiredScope"], "Gateway.Invoke")
        self.assertEqual(authentication["entra"]["audience"], self.parameters["gateway"]["authentication"]["entra"]["audience"])
        self.assertEqual(authentication["entra"]["userLimits"], self.options["gateway"]["authentication"]["entra"]["userLimits"])
        login = authentication["credentialLogin"]
        for key, value in self.options["gateway"]["authentication"]["credentialLogin"].items():
            self.assertEqual(login[key], value)
        self.assertEqual(login["identity"]["tenantId"], self.parameters["entraTenantId"])
        self.assertEqual(login["identity"]["clientId"], identity["clientId"])
        self.assertEqual(actual["credentialProviders"][0]["clientId"], self.parameters["credentialProviders"]["graphDelegated"]["clientId"])
        profile = logical_profiles(actual)["graph-delegated"]
        self.assertEqual(profile["clientId"], identity["clientId"])
        self.assertEqual(profile["clientSecretEnv"], "OIDC_SECRET_IDENTITY")

    def test_credential_login_fallback_keeps_selected_provider_registration(self):
        actual = self.normalized()
        login = actual["gateway"]["authentication"]["credentialLogin"]
        self.assertNotIn("identity", login)
        profile = logical_profiles(actual)["graph-delegated"]
        self.assertEqual(profile["clientId"], self.parameters["credentialProviders"]["graphDelegated"]["clientId"])
        self.assertEqual(profile["tenantId"], self.parameters["entraTenantId"])
        self.assertEqual(profile["clientSecretEnv"], "OIDC_SECRET_0")

    def test_omitted_entra_tenant_uses_azure_tenant_for_standard_graph_and_broker_identity(self):
        self.parameters.pop("entraTenantId")
        for use_independent_identity in (False, True):
            with self.subTest(independent_identity=use_independent_identity):
                if use_independent_identity:
                    put(self.parameters, ("gateway", "authentication", "credentialLogin", "identity"), {
                        "clientId": "77777777-7777-4777-8777-777777777777",
                        "clientSecretFile": "./reference-only/broker.secret",
                    })
                actual = self.normalized()
                self.assertEqual(actual["entraTenantId"], self.parameters["azureTenantId"])
                self.assertEqual(actual["credentialProviders"][0]["oauthTenantId"], self.parameters["azureTenantId"])
                login = actual["gateway"]["authentication"]["credentialLogin"]
                if use_independent_identity:
                    self.assertEqual(login["identity"]["tenantId"], self.parameters["azureTenantId"])
                self.assertEqual(logical_profiles(actual)["graph-delegated"]["tenantId"], self.parameters["azureTenantId"])

    def test_params_cannot_override_authentication_behavior_owned_by_config(self):
        behavior_paths = (
            ("gateway", "authentication", "entra", "userLimits"),
            ("gateway", "authentication", "credentialLogin", "providerNames"),
            ("gateway", "authentication", "credentialLogin", "idleTimeoutSeconds"),
        )
        original = copy.deepcopy(self.parameters)
        for path in behavior_paths:
            with self.subTest(path=".".join(path)):
                put(self.parameters, path, "synthetic-private-value")
                self.assert_rejected("gateway.authentication", "synthetic-private-value")
                self.parameters = copy.deepcopy(original)

    def test_config_cannot_supply_environment_specific_identity(self):
        registration_paths = (
            ("gateway", "authentication", "entra", "audience"),
            ("gateway", "authentication", "credentialLogin", "identity", "clientId"),
            ("gateway", "authentication", "credentialLogin", "identity", "clientSecretFile"),
            ("credentialProviders", "graphDelegated", "clientId"),
            ("credentialProviders", "graphDelegated", "clientSecretFile"),
        )
        original = copy.deepcopy(self.options)
        for path in registration_paths:
            with self.subTest(path=".".join(path)):
                put(self.options, path, "synthetic-private-value")
                self.assert_rejected("config." + ".".join(path), "synthetic-private-value")
                self.options = copy.deepcopy(original)

    def test_malformed_shared_config_objects_are_rejected_before_params_can_replace_them(self):
        shared_paths = (
            ("network",), ("gateway",),
            ("gateway", "authentication"),
            ("gateway", "authentication", "entra"),
            ("monitoring",), ("management",), ("services",), ("certificate",),
        )
        original = copy.deepcopy(self.options)
        for path in shared_paths:
            for value in ([], None, "synthetic-private-shape-value"):
                with self.subTest(path=".".join(path), value_kind=type(value).__name__):
                    self.options = copy.deepcopy(original)
                    put(self.options, path, value)
                    # ネットワーク型不正が別のゾーン整合エラーで止まるだけでは
                    # 足りない。通常のゾーン構成でもconfig自身の型を拒否する。
                    if path == ("network",):
                        self.options["gateway"]["zones"] = ["1", "2", "3"]
                    self.assert_rejected("config." + ".".join(path), "synthetic-private-shape-value")
        self.options = original

    def test_malformed_config_metadata_is_not_hidden_by_valid_params_metadata(self):
        for key in ("$schema", "$comment"):
            original = self.options[key]
            for value in ([], None, 7):
                with self.subTest(metadata=key, value_kind=type(value).__name__):
                    self.options[key] = value
                    self.assert_rejected("config." + key)
            self.options[key] = original

    def test_config_rejects_empty_foreign_identity_and_portal_containers(self):
        put(self.parameters, ("gateway", "authentication", "credentialLogin", "identity"), {
            "clientId": "77777777-7777-4777-8777-777777777777",
            "clientSecretFile": "./reference-only/broker.secret",
        })
        for value in ({}, [], None):
            with self.subTest(container="identity", value_kind=type(value).__name__):
                self.options["gateway"]["authentication"]["credentialLogin"]["identity"] = value
                self.assert_rejected("config.gateway.authentication.credentialLogin")
        self.options["gateway"]["authentication"]["credentialLogin"].pop("identity")
        for value in ({}, [], None):
            with self.subTest(container="portal", value_kind=type(value).__name__):
                self.options["portal"] = value
                self.assert_rejected("config.portal")
        self.options.pop("portal")

    def test_standard_graph_invalid_client_id_is_rejected_at_public_registration_path(self):
        sentinel = "synthetic-private-client-that-must-not-appear"
        self.parameters["credentialProviders"]["graphDelegated"]["clientId"] = sentinel
        self.assert_rejected("params.credentialProviders.graphDelegated.clientId", sentinel)

    def test_standard_graph_zero_guid_remains_a_template_value_but_cannot_be_normalized(self):
        self.parameters["credentialProviders"]["graphDelegated"]["clientId"] = "00000000-0000-0000-0000-000000000000"
        source = self.source()
        self.assertEqual(source["credentialProviders"][0]["clientId"], "00000000-0000-0000-0000-000000000000")
        with self.assertRaisesRegex(configuration.ConfigurationError, r"credentialProviders\[0\]\.clientId"):
            self.normalized()

    def test_relative_references_use_the_declaring_params_folder(self):
        put(self.parameters, ("gateway", "authentication", "credentialLogin", "identity"), {
            "clientId": "77777777-7777-4777-8777-777777777777",
            "clientSecretFile": "./reference-only/broker.secret",
        })
        self.options["credentialProviders"]["additional"] = [{
            "name": "synthetic-aad-service", "displayName": "Synthetic additional service",
            "identityProvider": "aad", "scopes": "read",
            "resourceUri": "https://service.example.com", "connectionNames": ["synthetic-service"],
        }]
        self.parameters["credentialProviders"]["additional"] = [{
            "name": "synthetic-aad-service", "clientId": "88888888-8888-4888-8888-888888888888",
            "clientSecretFile": "./reference-only/additional.secret",
            "oauthTenantId": "99999999-9999-4999-8999-999999999999",
        }]
        actual = self.normalized()
        references = (
            (actual["management"]["sshPublicKeyFile"], "management.pub"),
            (actual["credentialProviders"][0]["clientSecretFile"], "graph.secret"),
            (actual["credentialProviders"][1]["clientSecretFile"], "additional.secret"),
            (actual["gateway"]["authentication"]["credentialLogin"]["identity"]["clientSecretFile"], "broker.secret"),
        )
        for reference, filename in references:
            with self.subTest(reference_kind=filename):
                self.assertTrue(Path(reference) == (self.manifest.parent / "reference-only" / filename).resolve(),
                                "reference must retain its declaring folder")
                self.assertFalse(Path(reference).exists(), "references are not secret files created by this test")

    def test_source_documents_and_input_dictionaries_are_not_modified_or_given_generated_fields(self):
        self.write_inputs()
        original_parameters = copy.deepcopy(self.parameters)
        original_options = copy.deepcopy(self.options)
        original_files = (self.manifest.read_bytes(), self.options_file.read_bytes())
        actual = configuration.load_configuration(self.manifest)
        self.assertEqual(self.parameters, original_parameters)
        self.assertEqual(self.options, original_options)
        self.assertTrue((self.manifest.read_bytes(), self.options_file.read_bytes()) == original_files,
                        "loading must not rewrite either source")
        actual["credentialProviders"][0]["pool"]["count"] = 1
        repeated = configuration.load_configuration(self.manifest)
        self.assertEqual(repeated["credentialProviders"][0]["pool"]["count"], 12)
        self.assertNotIn("domainNames", self.parameters["certificate"])

    def test_selected_profiles_default_to_premium_blocking_and_keep_snat(self):
        actual = self.normalized()["network"]["firewall"]
        self.assertEqual((actual["sku"], actual["idpsMode"]), ("Premium", "Deny"))
        self.assertEqual(actual["snatPrivateRanges"], ["IANAPrivateRanges", *self.parameters["network"]["addressSpaces"]])

    def test_premium_firewall_derives_private_ranges_and_never_adds_a_bypass(self):
        self.options["network"]["firewall"]["sku"] = "Premium"
        self.options["network"]["firewall"]["idpsMode"] = "Deny"
        actual = self.normalized()["network"]["firewall"]
        self.assertEqual(actual["idpsMode"], "Deny")
        self.assertEqual(actual["idpsPrivateRanges"], [
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            *self.parameters["network"]["addressSpaces"],
        ])
        self.assertEqual(actual["bypassTrafficSettings"], [])

    def test_split_configuration_rejects_standard_firewall(self):
        self.options["network"]["firewall"]["sku"] = "Standard"
        with self.assertRaisesRegex(configuration.ConfigurationError, r"network\.firewall\.sku"):
            self.normalized()

    def test_certificate_covers_exactly_gateway_and_portal_fqdns(self):
        self.parameters["gateway"]["publicHostName"] = "gateway.synthetic.example.com"
        self.parameters["portal"]["publicHostName"] = "portal.synthetic.example.com"
        certificate = self.normalized()["certificate"]
        self.assertEqual(certificate["plugin"], "Azure")
        self.assertEqual(certificate["domainNames"], ["gateway.synthetic.example.com", "portal.synthetic.example.com"])

    def test_initial_empty_sets_and_managed_waf_reference_are_fixed(self):
        actual = self.normalized()
        self.assertEqual(actual["authorization"], {
            "projectAdministrators": [], "projectDevelopers": [],
            "foundryReaders": [], "catalogReaders": [],
        })
        self.assertEqual(actual["gateway"]["existingWafPolicyId"], "")
        self.assertEqual(actual["monitoring"]["auditAllowedIpAddresses"], [])
        self.assertEqual(actual["monitoring"]["auditReaders"], [])

    def test_internal_json_without_config_file_preserves_explicit_settings(self):
        internal = self.source()
        internal["publishApis"] = False
        internal["apiDefinitions"] = [{"name": "synthetic-internal-api"}]
        internal["gateway"]["authentication"]["subscriptionKeyHeader"] = "synthetic-internal-key"
        internal["certificate"]["domainNames"] = ["internal.synthetic.example.com"]
        filename = self.directory / "internal.json"
        filename.write_text(json.dumps(internal), encoding="utf-8")
        loaded = configuration.load_configuration_source(filename)
        self.assertEqual(loaded, internal)

    def test_bicep_output_rejects_ignored_internal_material_overrides_without_value_echo(self):
        self.options["network"]["firewall"].update(sku="Premium", idpsMode="Alert")
        original = self.source()
        principal = {"principalId": "11111111-1111-4111-8111-111111111111", "principalType": "Group"}
        cases = [
            (("gateway", "existingWafPolicyId"), "https://waf.example.com/policy"),
            (("monitoring", "auditAllowedIpAddresses"), ["203.0.113.71/32"]),
            (("monitoring", "auditReaders"), [{**principal, "collector": "collectJapan", "containerName": "insights-logs-audit"}]),
            (("network", "firewall", "bypassTrafficSettings"), [{"name": "synthetic-bypass"}]),
            (("network", "firewall", "snatPrivateRanges"), ["192.0.2.0/24"]),
            (("network", "firewall", "snatPrivateRanges"), []),
            (("network", "firewall", "idpsPrivateRanges"), ["192.0.2.0/24"]),
        ]
        cases += [(('authorization', key), [principal]) for key in
                  ("projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders")]
        for path, value in cases:
            internal = copy.deepcopy(original)
            put(internal, path, value)
            normalized = configuration.normalize_configuration(internal)
            with self.subTest(path=".".join(path)), self.assertRaisesRegex(
                    configuration.ConfigurationError, "retired material override") as caught:
                configuration.build_bicep_configuration(normalized)
            self.assertIn(".".join(path), str(caught.exception))
            self.assertNotIn(str(value), str(caught.exception))

    def test_bicep_material_guard_preserves_internal_apis_providers_and_publish_false(self):
        internal = connection_fixture()
        internal["publishApis"] = False
        normalized = configuration.normalize_configuration(internal)
        before = copy.deepcopy(normalized)
        rendered = configuration.build_bicep_configuration(normalized)
        self.assertEqual(normalized, before)
        self.assertFalse(rendered["publishApis"])
        self.assertEqual([api["name"] for api in rendered["apiDefinitions"]],
                         [api["name"] for api in normalized["apiDefinitions"]])
        self.assertEqual([provider["name"] for provider in rendered["credentialProviders"]],
                         [provider["name"] for provider in normalized["credentialProviders"]])
        self.assertEqual([provider["clientId"] for provider in rendered["credentialProviders"]],
                         [provider["clientId"] for provider in normalized["credentialProviders"]])

    def test_bicep_material_guard_rejects_malformed_range_elements_without_value_echo(self):
        internal = self.normalized()
        sentinel = "synthetic-private-value-that-must-not-appear"
        internal["network"]["firewall"]["snatPrivateRanges"] = [{"value": sentinel}]
        with self.assertRaisesRegex(configuration.ConfigurationError, "network.firewall.snatPrivateRanges") as caught:
            configuration.build_bicep_configuration(internal)
        self.assertNotIn(sentinel, str(caught.exception))

    def test_render_rejects_retired_override_before_preparing_or_truncating_output(self):
        internal = self.normalized()
        internal["gateway"]["existingWafPolicyId"] = "https://waf.example.com/policy"
        output = self.directory / "existing-output.json"
        output.write_text("synthetic-output-before", encoding="utf-8")
        with mock.patch.object(configuration, "load_configuration", return_value=internal), mock.patch(
                "deployment.helpers.paths.private_parent") as prepare, mock.patch.object(
                configuration.os, "open") as open_output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(configuration.main(["render", "--config", str(self.manifest), "--output", str(output)]), 2)
        prepare.assert_not_called()
        open_output.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "synthetic-output-before")
        self.assertIn("gateway.existingWafPolicyId", errors.getvalue())
        self.assertNotIn(internal["gateway"]["existingWafPolicyId"], errors.getvalue())

    def test_relocated_internal_file_preserves_secret_reference_targets(self):
        original = self.source()
        output = self.directory / "generated"
        output.mkdir()
        converted = configuration.configuration_for_file(original, base_directory=self.manifest.parent, output_directory=output)
        filename = output / "configuration.json"
        filename.write_text(json.dumps(converted), encoding="utf-8")
        loaded = configuration.load_configuration_source(filename)
        self.assertTrue(Path(loaded["management"]["sshPublicKeyFile"]) == Path(original["management"]["sshPublicKeyFile"]),
                        "SSH reference target must survive relocation")
        self.assertTrue(Path(loaded["credentialProviders"][0]["clientSecretFile"]) == Path(original["credentialProviders"][0]["clientSecretFile"]),
                        "credential reference target must survive relocation")

    def test_unknown_fields_and_invalid_secret_reference_errors_do_not_echo_values(self):
        sentinel = "synthetic-private-value-that-must-not-appear"
        self.parameters["unexpectedOption"] = sentinel
        self.assert_rejected("parameters", sentinel)
        self.parameters.pop("unexpectedOption")
        self.options["unexpectedOption"] = sentinel
        self.assert_rejected("config", sentinel)
        self.options.pop("unexpectedOption")
        self.parameters["credentialProviders"]["graphDelegated"]["clientSecretFile"] = " " + sentinel
        self.assert_rejected("clientSecretFile", sentinel)


if __name__ == "__main__":
    unittest.main()
