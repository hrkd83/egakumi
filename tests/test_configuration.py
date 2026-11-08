"""公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証する。

一時フォルダー内の合成設定を使い、命名・サブネット・API認可・SSH公開鍵・DNS資格情報の入力を調べる。実在するテナントや秘密値をテストの正解にしない。
"""
import base64
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


from configuration_fixtures import (
    CORE, configuration, fixture, ssh_fixture_key, api_fixture,
    profiled_api_fixture, connection_fixture, pii_fixture, pii_key_fixture,
)


class PublicSampleTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def sample(self, name):
        manifest = CORE / "params" / (name + ".example.json")
        parameters = json.loads(manifest.read_text())
        options = json.loads((manifest.parent / parameters["configFile"]).read_text())
        raw = configuration.load_configuration_source(manifest)
        raw["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
        raw["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
        raw["entraTenantId"] = raw["azureTenantId"]
        raw["certificate"]["azureDnsSubscriptionId"] = raw["subscriptionId"]
        raw["certificate"]["azureDnsManagedIdentityResourceId"] = (
            "/subscriptions/" + raw["subscriptionId"] +
            "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
        raw["credentialProviders"][0]["clientId"] = "44444444-4444-4444-8444-444444444444"
        return parameters, options, configuration.normalize_configuration(raw, base_directory=manifest.parent)

    def test_apim_identifier_is_required_parameter_owned_and_never_regenerated(self):
        for value in (None, "", "ABC123", "abc12", "abc1234", "abc-12", 123456):
            raw = fixture()
            if value is None:
                raw["services"].pop("apimInstanceId", None)
            else:
                raw["services"]["apimInstanceId"] = value
            with self.subTest(value=value), self.assertRaisesRegex(configuration.ConfigurationError, r"services\.apimInstanceId"):
                configuration.normalize_configuration(raw)
        raw = fixture()
        raw["services"]["apimInstanceId"] = "m8q3r2"
        self.assertEqual(configuration.normalize_configuration(raw)["services"]["apimInstanceId"], "m8q3r2")
        self.assertEqual(raw["services"]["apimInstanceId"], "m8q3r2")
        self.assertIn("apimInstanceId", configuration.PARAMETER_SECTION_KEYS["services"])
        self.assertIn(("services", "apimInstanceId"), configuration.PARAMETER_OWNED_PATHS)

    def test_schema_and_public_examples_require_synthetic_apim_identifier(self):
        for filename in ("params.schema.json", "configuration.schema.json"):
            service = json.loads((CORE / "schemas" / filename).read_text())["properties"]["services"]
            with self.subTest(schema=filename):
                self.assertIn("apimInstanceId", service["required"])
                self.assertEqual(service["properties"]["apimInstanceId"]["pattern"], "^[a-z0-9]{6}$")
                self.assertNotIn("default", service["properties"]["apimInstanceId"])
        for name in ("test", "production"):
            parameters, options, actual = self.sample(name)
            with self.subTest(profile=name):
                self.assertEqual(parameters["services"]["apimInstanceId"], "k7m2q9")
                self.assertEqual(actual["services"]["apimInstanceId"], "k7m2q9")
                self.assertNotIn("apimInstanceId", options["services"])

    def test_required_org_inputs_are_separate_from_standard_options(self):
        for name in ("test", "production"):
            with self.subTest(name=name):
                parameters, options, actual = self.sample(name)
                self.assertEqual(parameters["configFile"], "../config/" + name + ".json")
                self.assertEqual(parameters["productName"], "egakumi")
                self.assertEqual(parameters["subscriptionId"], "00000000-0000-0000-0000-000000000000")
                self.assertEqual(parameters["entraTenantId"], "00000000-0000-0000-0000-000000000000")
                self.assertEqual(parameters["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
                self.assertEqual(parameters["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
                self.assertEqual(parameters["credentialProviders"][0]["clientId"], "00000000-0000-0000-0000-000000000000")
                self.assertNotIn("oauthTenantId", parameters["credentialProviders"][0])
                provider = actual["credentialProviders"][0]
                self.assertEqual(provider["name"], "graph-delegated")
                self.assertEqual(provider["pool"], {"count": 12, "capacity": 8000})
                self.assertEqual(provider["clientId"], "44444444-4444-4444-8444-444444444444")
                self.assertEqual(provider["oauthTenantId"], actual["entraTenantId"])
                self.assertEqual(provider["connectionNames"], [])
                self.assertEqual(provider["identityEndpoint"], "https://graph.microsoft.com/v1.0/me")
                self.assertEqual(parameters["credentialProviders"][0]["clientSecretFile"], "../secrets/entra-client-secret.txt")
                deployed = configuration.build_bicep_configuration(actual)
                self.assertNotIn("clientSecretFile", deployed["credentialProviders"][0])
                self.assertEqual([item["name"] for item in deployed["credentialPhysicalProviders"]],
                                 ["graph-delegated-" + suffix for suffix in "abcdefghijkl"])
                self.assertEqual(len(deployed["credentialProfiles"]["graph-delegated"]["pools"]), 12)
                self.assertIn("addressSpaces", parameters["network"])
                self.assertIn("subnetPrefixes", parameters["network"])
                self.assertNotIn("addressSpaces", options["network"])
                self.assertNotIn("subnetPrefixes", options["network"])
                self.assertNotIn("modelDeployments", parameters["services"])
                self.assertNotIn("subscriptionId", options)
                self.assertNotIn("azureTenantId", options)
                self.assertNotIn("entraTenantId", options)
                self.assertNotIn("publicHostName", options["gateway"])
                self.assertNotIn("sshPublicKeyFile", options["management"])
                self.assertNotIn("publisherEmail", options["services"])

    def test_both_samples_supply_full_model_catalogue_and_access(self):
        # モデル一覧の一意性と設定検証は test_initial_models.py で確認する。
        # ここでは公開例に主要用途が揃い、APIMから利用する設定が有効なことを調べる。
        required = {"gpt-6.1-sol", "text-embedding-3-small", "gpt-image-2.5-sunburst", "gpt-realtime-2.1", "Cohere-rerank-v4.0-fast", "decision-1"}
        for name in ("test", "production"):
            with self.subTest(name=name):
                _, options, actual = self.sample(name)
                models = actual["services"]["modelDeployments"]
                self.assertTrue(required.issubset({model["name"] for model in models}))
                self.assertTrue(options["services"]["grantApimFoundryModelsAccess"])
                self.assertTrue(options["services"]["speechEnabled"])
                self.assertEqual(options["services"]["projectDisplayName"], "core-platform")
                # Decisionだけは既存APIの配備名を保持し、提供者の正式名へ対応付ける。
                aliases = {model["name"]: model["modelName"] for model in models
                           if model["name"] != model["modelName"]}
                self.assertEqual(aliases, {"decision-1": "Microsoft-Decision-1"})
                for model in models:
                    self.assertEqual(model["versionUpgradeOption"], "NoAutoUpgrade")

    def test_test_and_production_zone_choices_remain_consistent(self):
        for name, zones in (("test", []), ("production", ["1", "2", "3"])):
            with self.subTest(name=name):
                _, options, actual = self.sample(name)
                self.assertEqual(options["network"]["appGatewayZones"], zones)
                self.assertEqual(actual["gateway"]["zones"], zones)
                self.assertEqual(options["network"]["firewall"]["idpsMode"], "Off" if name == "test" else "Alert")
                if name == "test":
                    self.assertNotIn("idpsMode", actual["network"]["firewall"])

    def test_explicit_network_defaults_include_optional_rule_arrays(self):
        """企業のIP条件はparams、通信機能の既定値はconfigに明示する。"""
        for name in ("test", "production"):
            parameters, options, _ = self.sample(name)
            network = options["network"]
            addresses = parameters["network"]
            self.assertTrue(network["certificatePublicProbeEnabled"])
            self.assertEqual(network["dnsTtl"], 300)
            self.assertEqual(addresses["managementAdditionalSecurityRules"], [])
            self.assertEqual(network["operatorPorts"], ["22"])
            self.assertEqual(addresses["firewall"]["snatPrivateRanges"], ["IANAPrivateRanges"])
            self.assertEqual(network["firewall"]["signatureOverrides"], [])
            for key in ("idpsPrivateRanges", "bypassTrafficSettings", "dnsServers",
                        "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
                        "apimAdditionalApplicationRules", "managementAdditionalApplicationRules"):
                self.assertEqual(addresses["firewall"][key], [])
                self.assertNotIn(key, network["firewall"])
            self.assertEqual(parameters["monitoring"]["auditAllowedIpAddresses"], [])
            self.assertNotIn("auditAllowedIpAddresses", options["monitoring"])

    def test_gateway_defaults_cover_large_requests_voice_and_native_grpc(self):
        for name in ("test", "production"):
            _, options, actual = self.sample(name)
            gateway = options["gateway"]
            self.assertEqual(gateway["wafMaxRequestBodySizeInKb"], 2000)
            self.assertEqual(gateway["wafRequestBodyInspectLimitInKb"], 2000)
            self.assertEqual(gateway["wafFileUploadLimitInMb"], 4)
            self.assertTrue(gateway["realtimeControlEnabled"])
            self.assertTrue(gateway["grpc"]["enabled"])
            self.assertEqual(actual["gateway"]["grpc"]["publicHostName"], actual["gateway"]["publicHostName"])
            # An optional gRPC hostname must follow the one company-specific FQDN.
            self.assertNotIn("publicHostName", gateway["grpc"])

    def test_org_inputs_cannot_be_deployed_with_unset_target_guids(self):
        for name in ("test", "production"):
            with self.subTest(name=name), self.assertRaisesRegex(configuration.ConfigurationError, "subscriptionId"):
                configuration.load_configuration(CORE / "params" / (name + ".example.json"))


class CanonicalApiConfigurationTests(unittest.TestCase):
    """統合でURLの境界がなくなっても、元契約・権限・上限を保持する。"""

    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_release_is_explicit_and_reserved_product_cannot_bypass_it(self):
        raw = api_fixture()
        self.assertFalse(self.normalize(raw)["apiDefinitions"][0]["released"])
        raw["apiDefinitions"][0]["released"] = True
        self.assertTrue(self.normalize(raw)["apiDefinitions"][0]["released"])
        raw["apiDefinitions"][0]["released"] = "true"
        with self.assertRaisesRegex(configuration.ConfigurationError, "released"):
            self.normalize(raw)
        raw["apiDefinitions"][0].update(released=False, productId="released-apis")
        with self.assertRaisesRegex(configuration.ConfigurationError, "reserved"):
            self.normalize(raw)

    def test_same_identity_can_keep_disjoint_text_and_pdf_contracts(self):
        actual = self.normalize(profiled_api_fixture())["apiDefinitions"][0]
        self.assertEqual(actual["name"], "openai-inference-v1")
        self.assertEqual(actual["serviceClass"], "platform")
        authorization = actual["authorization"]
        self.assertEqual(authorization["mode"], "subscriptionKey")
        self.assertEqual(set(authorization), {"mode", "operationConstraints"})
        constraint = authorization["operationConstraints"]["responses-create"]
        self.assertEqual(constraint["defaultProfile"], "response")
        profiles = {entry["profileId"]: entry for entry in constraint["profiles"]}
        self.assertEqual(profiles["response"]["maxRequestBytes"], 32768)
        self.assertEqual(profiles["pdf"]["maxRequestBytes"], 2048000)
        self.assertEqual(profiles["response"]["maxOutputTokens"], 128)
        self.assertEqual(profiles["pdf"]["maxOutputTokens"], 512)
        self.assertEqual(profiles["response"]["modalities"], ["text"])
        self.assertEqual(profiles["pdf"]["modalities"], ["text", "file"])


    def test_profiles_cannot_be_recursive_unknown_or_missing_from_grants(self):
        for mutation in ("recursive", "unknownDefault", "unknownModality", "wrongKind"):
            raw = profiled_api_fixture()
            authorization = raw["apiDefinitions"][0]["authorization"]
            constraint = authorization["operationConstraints"]["responses-create"]
            if mutation == "recursive": constraint["profiles"][0]["profiles"] = []
            if mutation == "unknownDefault": constraint["defaultProfile"] = "unregistered"
            if mutation == "unknownModality": constraint["profiles"][0]["modalities"] = ["arbitrary"]
            if mutation == "wrongKind": constraint["profiles"][0]["kind"] = "bodyModel"
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_service_class_is_explicit_and_sample_names_cannot_claim_platform(self):
        raw = profiled_api_fixture()
        raw["apiDefinitions"][0]["serviceClass"] = "unregistered"
        with self.assertRaisesRegex(configuration.ConfigurationError, "serviceClass"):
            self.normalize(raw)
        raw = api_fixture()
        raw["apiDefinitions"][0].update(name="sample-contract-v1", serviceClass="platform")
        with self.assertRaisesRegex(configuration.ConfigurationError, "classified as sample"):
            self.normalize(raw)

    def test_flat_custom_api_without_optional_profiles_is_supported(self):
        actual = self.normalize(api_fixture())["apiDefinitions"][0]
        self.assertEqual(actual["name"], "models")
        self.assertNotIn("profiles", actual["authorization"]["operationConstraints"]["responses-create"])


class PortableFileReferenceTests(unittest.TestCase):
    """保存する設定だけを相対化し、実行時の参照先と非ファイル値を守る。"""

    def test_known_references_round_trip_without_reading_or_generating_secrets(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, destination = root / "input", root / "generated" / "nested"
            source.mkdir()
            destination.mkdir(parents=True)
            value = {
                "management": {"sshPublicKeyFile": "../secrets/management-ssh.pub"},
                "certificate": {"mode": "dnsPlugin", "plugin": "Azure"},
                "credentialProviders": [{"clientSecretFile": "../secrets/cm.txt"}],
                "apiDefinitions": [{"backendUrl": "https://provider.example.com/api"}],
                "resourceId": "/subscriptions/public-id/resourceGroups/example",
                "$schema": "https://example.com/schema.json",
            }
            original = copy.deepcopy(value)
            with patch.object(Path, "open", side_effect=AssertionError("must not open secret files")):
                saved = configuration.configuration_for_file(value, base_directory=source,
                                                              output_directory=destination)
            self.assertEqual(value, original)
            self.assertFalse((root / "secrets").exists())
            self.assertEqual(saved["resourceId"], value["resourceId"])
            self.assertEqual(saved["$schema"], value["$schema"])
            self.assertEqual(saved["apiDefinitions"][0]["backendUrl"], value["apiDefinitions"][0]["backendUrl"])
            for parent, key, _ in configuration._file_reference_fields(saved):
                self.assertFalse(Path(parent[key]).is_absolute())
                self.assertTrue(parent[key].startswith("../../secrets/"))
            filename = destination / "config.json"
            filename.write_text(json.dumps(saved))
            reloaded = configuration.load_configuration_source(filename)
            self.assertEqual(reloaded, configuration._resolve_file_references(original, source))

    def test_saved_references_follow_a_repository_copied_to_another_location(self):
        import shutil
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary).resolve() / "first", Path(temporary).resolve() / "another-home" / "clone"
            (first / "config").mkdir(parents=True)
            raw = {"management": {"sshPublicKeyFile": str(first / "secrets/management-ssh.pub")}}
            saved = configuration.configuration_for_file(raw, base_directory=first,
                                                          output_directory=first / "config")
            (first / "config/generated.json").write_text(json.dumps(saved))
            shutil.copytree(first, second)
            actual = configuration.load_configuration_source(second / "config/generated.json")
            self.assertEqual(actual["management"]["sshPublicKeyFile"],
                             str(second / "secrets/management-ssh.pub"))
            self.assertNotIn(str(first), json.dumps(saved))
            self.assertFalse((second / "secrets").exists())

    def test_schema_and_config_references_use_the_declaring_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            saved = configuration.configuration_for_file(
                {"$schema": "../schemas/input.json", "configFile": "../config/options.json"},
                base_directory=root / "params", output_directory=root / "generated/deep")
            self.assertEqual(saved, {"$schema": "../../schemas/input.json",
                                     "configFile": "../../config/options.json"})


class ConfigurationTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def normalize(self, value=None):
        return configuration.normalize_configuration(fixture() if value is None else value, base_directory=CORE / "config")


    def test_external_subsystems_are_not_core_configuration(self):
        """別システムのホスト設定を黙ってコア資源へ取り込まない。"""
        for key in ("managementApplication", "platformTools"):
            raw = fixture()
            raw[key] = {}
            with self.subTest(key=key), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)
        raw = fixture()
        raw["network"]["subnetPrefixes"]["external"] = "10.40.3.0/26"
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)
        self.assertEqual(set(self.normalize()["network"]["subnetPrefixes"]),
                         {"agw", "firewall", "apim", "pep", "management", "aca"})

    def test_names_are_derived_for_exact_five_groups(self):
        actual = self.normalize()
        self.assertEqual(set(actual["resourceGroupNames"]), {"id", "nw", "monitor", "svc", "maint"})
        self.assertEqual(actual["resourceGroupNames"]["id"], "rg-test-egakumi-id")
        self.assertEqual(actual["certificateIdentityName"], "id-test-egakumi-certificate")
        self.assertNotIn("nameSuffix", actual)

    def test_no_mutation_of_input(self):
        raw = fixture()
        before = copy.deepcopy(raw)
        self.normalize(raw)
        self.assertEqual(raw, before)

    def test_omitted_firewall_sku_has_production_default_without_environment_branch(self):
        raw = fixture()
        raw["network"]["firewall"] = {}
        actual = self.normalize(raw)
        self.assertEqual(actual["network"]["firewall"]["sku"], "Premium")
        self.assertEqual(actual["network"]["firewall"]["idpsMode"], "Alert")
        self.assertEqual(actual["apiPublicationStatus"], "not-published")

    def test_production_template_requires_target_ids(self):
        raw = configuration.load_configuration_source(CORE / "params/production.example.json")
        with self.assertRaisesRegex(configuration.ConfigurationError, "subscriptionId"):
            self.normalize(raw)
        raw["subscriptionId"], raw["azureTenantId"] = fixture()["subscriptionId"], fixture()["azureTenantId"]
        raw["entraTenantId"] = raw["azureTenantId"]
        raw["certificate"]["azureDnsSubscriptionId"] = raw["subscriptionId"]
        raw["certificate"]["azureDnsManagedIdentityResourceId"] = (
            "/subscriptions/" + raw["subscriptionId"] +
            "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
        raw["credentialProviders"][0]["clientId"] = "44444444-4444-4444-8444-444444444444"
        self.assertEqual(raw["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
        self.assertEqual(raw["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        actual = self.normalize(raw)
        self.assertEqual(actual["credentialProviders"][0]["oauthTenantId"], actual["entraTenantId"])
        self.assertEqual(actual["network"]["firewall"]["sku"], "Premium")
        self.assertEqual(actual["network"]["firewall"]["idpsMode"], "Alert")
        self.assertEqual(actual["services"]["apimSkuName"], "Premium")
        self.assertTrue(actual["services"]["vaultPurgeProtection"])
        self.assertEqual(actual["monitoring"]["auditImmutabilityMode"], "Locked")


    def test_no_raw_secrets_in_config(self):
        raw = fixture()
        raw["management"]["adminPassword"] = "not-a-secret-fixture-value"
        with self.assertRaises(configuration.ConfigurationError) as error:
            self.normalize(raw)
        self.assertNotIn("not-a-secret-fixture-value", str(error.exception))

    def test_subnet_overlap_rejected(self):
        raw = fixture()
        raw["network"]["subnetPrefixes"]["management"] = "10.40.2.64/26"
        with self.assertRaisesRegex(configuration.ConfigurationError, "overlap"):
            self.normalize(raw)


    def test_bgp_route_propagation_defaults_to_enabled_per_route_table(self):
        raw = fixture()
        raw["network"].pop("bgpRoutePropagation", None)
        self.assertEqual(self.normalize(raw)["network"]["bgpRoutePropagation"], {
            "agw": True, "apim": True, "management": True,
        })

    def test_client_ip_restriction_is_optional_and_operators_remain_closed_by_default(self):
        raw = fixture()
        raw["network"].pop("clientAddressPrefixes", None)
        raw["network"].pop("operatorAddressPrefixes", None)
        actual = self.normalize(raw)["network"]
        self.assertEqual(actual["clientAddressPrefixes"], ["0.0.0.0/0"])
        self.assertEqual(actual["operatorAddressPrefixes"], [])

    def test_explicit_client_source_ranges_are_preserved(self):
        raw = fixture()
        raw["network"]["clientAddressPrefixes"] = ["203.0.113.10/32", "10.20.0.0/16"]
        self.assertEqual(self.normalize(raw)["network"]["clientAddressPrefixes"], raw["network"]["clientAddressPrefixes"])

    def test_explicit_empty_client_source_ranges_remain_closed(self):
        raw = fixture()
        raw["network"]["clientAddressPrefixes"] = []
        self.assertEqual(self.normalize(raw)["network"]["clientAddressPrefixes"], [])

    def test_bgp_route_propagation_preserves_explicit_false_and_defaults_missing_keys(self):
        raw = fixture()
        raw["network"]["bgpRoutePropagation"] = {"agw": False, "apim": True}
        self.assertEqual(self.normalize(raw)["network"]["bgpRoutePropagation"], {
            "agw": False, "apim": True, "management": True,
        })

    def test_bgp_route_propagation_rejects_non_booleans(self):
        for value in ("false", 0, None):
            with self.subTest(value=value):
                raw = fixture()
                raw["network"]["bgpRoutePropagation"] = {"agw": value}
                with self.assertRaisesRegex(configuration.ConfigurationError, "bgpRoutePropagation.agw"):
                    self.normalize(raw)

    def test_subnet_outside_vnet_rejected(self):
        raw = fixture()
        raw["network"]["subnetPrefixes"]["management"] = "10.41.2.128/26"
        with self.assertRaisesRegex(configuration.ConfigurationError, "fit"):
            self.normalize(raw)

    def test_host_bits_and_undersized_agw_rejected(self):
        for prefix in ("10.40.0.3/24", "10.40.0.0/27"):
            raw = fixture()
            raw["network"]["subnetPrefixes"]["agw"] = prefix
            with self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_standard_rejects_premium_idps_settings(self):
        for updates in ({"idpsMode": "Alert"}, {"signatureOverrides": [{"id": "1", "mode": "Alert"}]}):
            raw = fixture()
            raw["network"]["firewall"].update(updates)
            with self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_premium_does_not_allow_idps_off(self):
        raw = fixture()
        raw["network"]["firewall"].update(sku="Premium", idpsMode="Off")
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)

    def test_developer_cannot_have_two_units(self):
        raw = fixture()
        raw["services"]["apimCapacity"] = 2
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)

    def test_resource_names_use_environment_product_without_overrides(self):
        """すべての名前は同じ入力から決まり、元の設定を勝手に変更しない。"""
        raw = fixture()
        before = copy.deepcopy(raw)
        actual = self.normalize(raw)
        self.assertEqual(actual["resourceStem"], "test-egakumi")
        self.assertEqual(raw, before)


    def test_combined_names_fit_container_app_key_vault_and_storage_without_truncation(self):
        """個別の長さだけでなく、Azureへ送る連結名が上限に収まることを確かめる。"""
        raw = fixture()
        raw["productName"], raw["environmentName"] = "p" * 12, "test"
        actual = self.normalize(raw)
        self.assertEqual(actual["resourceStem"], "test-" + "p" * 12)
        self.assertEqual(len("ca-" + actual["resourceStem"] + "-credentials"), 32)
        self.assertEqual(len("kv-" + actual["resourceStem"]), 20)
        self.assertEqual(len("st" + actual["resourceStem"].replace("-", "") + "jp"), 20)
        for product, environment in (("p" * 13, "test"), ("p" * 16, "test"), ("p" * 24, "test"), ("product-name", "environment")):
            raw["productName"], raw["environmentName"] = product, environment
            with self.subTest(product=product, environment=environment), self.assertRaisesRegex(configuration.ConfigurationError, "17 characters"):
                self.normalize(raw)

    def test_image_version_must_be_fixed(self):
        raw = fixture()
        raw["management"]["imageReference"]["version"] = "latest"
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)

    def test_v6_controller_must_be_nvme(self):
        raw = fixture()
        raw["management"]["diskControllerType"] = "SCSI"
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)

    def test_gateway_and_pip_zones_match(self):
        raw = fixture()
        raw["gateway"]["zones"] = ["1"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "Public IP"):
            self.normalize(raw)

    def test_bicep_config_excludes_secret_paths(self):
        result = configuration.build_bicep_configuration(self.normalize())
        rendered = json.dumps(result)
        self.assertNotIn("sshPublicKeyFile", rendered)
        self.assertNotIn("pluginArgumentsFile", rendered)
        self.assertIn("moduleVersion", result["certificate"])

    def test_credential_provider_public_configuration_excludes_secret_path(self):
        raw = connection_fixture()
        normalized = self.normalize(raw)
        provider = normalized["credentialProviders"][0]
        self.assertTrue(Path(provider["clientSecretFile"]).is_absolute())
        public = configuration.build_bicep_configuration(normalized)
        self.assertNotIn("clientSecretFile", json.dumps(public))
        self.assertEqual(public["credentialProviders"][0]["clientId"], raw["credentialProviders"][0]["clientId"])
        self.assertEqual(public["credentialProviders"][0]["connectionNames"], [])
        self.assertEqual(public["credentialProviders"][0]["identityEndpoint"], "https://graph.microsoft.com/v1.0/me")
        self.assertIn("clientSecretFile", provider)

    def test_external_operation_can_pin_backend_query_without_changing_public_route(self):
        raw = connection_fixture()
        operation = raw["apiDefinitions"][0]["operations"][0]
        operation["backendUrlTemplate"] = "/me?$select=id&$count=false"
        normalized = self.normalize(raw)
        result = configuration.build_bicep_configuration(normalized)["apiDefinitions"][0]["operations"][0]
        self.assertEqual(result["urlTemplate"], "/me")
        self.assertEqual(result["backendUrlTemplate"], "/me?$select=id&$count=false")
        operation.update(urlTemplate="/users/{id}", templateParameters=[{"name": "id", "type": "string", "required": True}],
                         backendUrlTemplate="/users/{id}?$select=id")
        self.assertEqual(self.normalize(raw)["apiDefinitions"][0]["operations"][0]["backendUrlTemplate"], "/users/{id}?$select=id")

    def test_backend_rewrite_rejects_authority_credentials_policy_syntax_and_unknown_parameters(self):
        invalid = ["https://graph.microsoft.com/v1.0/me", "//user:password@example.invalid/me", "/%2fexample.invalid/me",
                   "/https://example.invalid/me", "/user:password@example.invalid/me", "/me#fragment", "/me\\..\\users",
                   "/me?$select=id\n", "/me?$select=%0did", "/me?client_secret=SENTINEL-SECRET",
                   "/me?access%5ftoken=SENTINEL-SECRET", "/me?sig=SENTINEL-SECRET", '/me?x=\" /><send-request />',
                   "/me?x=@(context.Request.Url)", "/me?x={{named-value}}", "/users/{unregistered}", "/users/{id", "/me?value=%xx"]
        for template in invalid:
            with self.subTest(template=template):
                raw = connection_fixture()
                raw["apiDefinitions"][0]["operations"][0]["backendUrlTemplate"] = template
                with self.assertRaisesRegex(configuration.ConfigurationError, "backendUrlTemplate") as caught:
                    self.normalize(raw)
                self.assertNotIn("SENTINEL", str(caught.exception))

    def test_backend_rewrite_cannot_override_pii_or_model_endpoint_contracts(self):
        for raw in (api_fixture(), pii_fixture()):
            raw["apiDefinitions"][0]["operations"][0]["backendUrlTemplate"] = "/unreviewed"
            with self.assertRaisesRegex(configuration.ConfigurationError, "backendUrlTemplate"):
                self.normalize(raw)

    def test_credential_provider_secrets_load_only_from_protected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            files = {
                "public-key": ssh_fixture_key(), "provider": "SENTINEL-OAUTH-SECRET",
            }
            for name, text in files.items():
                (target / name).write_text(text)
                (target / name).chmod(0o600)
            raw = connection_fixture()
            raw["management"]["sshPublicKeyFile"] = str(target / "public-key")
            raw["credentialProviders"][0]["clientSecretFile"] = str(target / "provider")
            normalized = self.normalize(raw)
            secrets = configuration.load_secret_inputs(normalized)
            self.assertEqual(secrets["credentialProviderSecrets"], {"approved-oauth": files["provider"]})
            public = json.dumps(configuration.build_bicep_configuration(normalized))
            for marker in (files["provider"], "SENTINEL-DNS-TOKEN", "clientSecretFile"):
                self.assertNotIn(marker, public)
            (target / "provider").write_text("")
            with self.assertRaisesRegex(configuration.ConfigurationError, "must not be empty"):
                configuration.load_secret_inputs(normalized)
            if os.name != "nt":
                (target / "provider").write_text(files["provider"])
                (target / "provider").chmod(0o644)
                with self.assertRaisesRegex(configuration.ConfigurationError, "chmod 600") as caught:
                    configuration.load_secret_inputs(normalized)
                self.assertNotIn(files["provider"], str(caught.exception))

    def test_credential_provider_rejects_plain_secret_or_incomplete_oauth_endpoints(self):
        raw = connection_fixture()
        raw["credentialProviders"][0]["clientSecret"] = "SENTINEL-MISPLACED-SECRET"
        with self.assertRaises(configuration.ConfigurationError) as caught:
            self.normalize(raw)
        self.assertNotIn("SENTINEL-MISPLACED-SECRET", str(caught.exception))
        raw = connection_fixture()
        raw["gateway"]["authentication"].pop("credentialLogin")
        raw["apiDefinitions"][0]["credentialConnection"].update(mode="service", connectionName="service-fixed")
        provider = raw["credentialProviders"][0]
        provider.pop("resourceUri")
        provider["connectionNames"] = ["service-fixed"]
        provider.update(identityProvider="oauth2", authorizationUrl="https://provider.example/authorize", tokenUrl="https://provider.example/token", refreshUrl="https://provider.example/token")
        self.assertEqual(self.normalize(raw)["credentialProviders"][0]["identityProvider"], "oauth2")
        for field in ("authorizationUrl", "tokenUrl", "refreshUrl"):
            with self.subTest(endpoint=field):
                broken = copy.deepcopy(raw)
                broken["credentialProviders"][0][field] = "http://provider.example/insecure"
                with self.assertRaises(configuration.ConfigurationError):
                    self.normalize(broken)

    def test_personal_credential_connection_uses_shared_login_and_session(self):
        raw = connection_fixture()
        actual = self.normalize(raw)
        self.assertEqual(actual["apiDefinitions"][0]["credentialConnection"]["mode"], "personal")
        self.assertNotIn("connectionName", actual["apiDefinitions"][0]["credentialConnection"])
        self.assertEqual(actual["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["approved-oauth"])
        raw["apiDefinitions"][0]["credentialConnection"]["enabled"] = False
        self.assertFalse(self.normalize(raw)["apiDefinitions"][0]["credentialConnection"]["enabled"])
        for field, value in (("ownerTenantId", "66666666-6666-4666-8666-666666666666"), ("connectionName", "other-owner"), ("providerName", "other-provider")):
            with self.subTest(field=field):
                broken = connection_fixture()
                broken["apiDefinitions"][0]["credentialConnection"][field] = value
                with self.assertRaises(configuration.ConfigurationError):
                    self.normalize(broken)

    def test_credential_connection_requires_cm_owned_external_authentication(self):
        for mode in ("managedIdentity", "foundry", "unregistered"):
            with self.subTest(mode=mode):
                raw = connection_fixture()
                api = raw["apiDefinitions"][0]
                if mode == "managedIdentity":
                    api["backendAuthentication"] = {"mode": "managedIdentity"}
                elif mode == "unregistered":
                    api["credentialConnection"]["providerName"] = "unknown"
                else:
                    api.update(backendService="foundryServices", backendAudience="https://ai.azure.com")
                    api.pop("backendUrl")
                with self.assertRaises(configuration.ConfigurationError):
                    self.normalize(raw)


    def test_pii_rest_and_generated_mcp_share_registered_key_contract(self):
        actual = self.normalize(pii_key_fixture())["apiDefinitions"][0]
        self.assertEqual(actual["authorization"]["mode"], "subscriptionKey")
        self.assertEqual(actual["keySubscriptions"][0]["subscriptionId"], "pii-system")
        self.assertNotIn("oauthScopes", actual["mcp"])
        self.assertEqual(set(actual["authorization"]), {"mode", "operationConstraints"})

    def test_pii_key_only_rest_does_not_require_a_personal_oauth_registration(self):
        raw = pii_key_fixture()
        api = raw["apiDefinitions"][0]
        api.pop("mcp")
        actual = self.normalize(raw)["apiDefinitions"][0]
        self.assertEqual(set(actual["authorization"]), {"mode", "operationConstraints"})
        self.assertNotIn("mcp", actual)


    def test_connection_api_identifiers_are_reserved_for_both_rest_and_mcp(self):
        for target in ("rest", "mcp"):
            with self.subTest(target=target):
                raw = pii_fixture()
                if target == "rest":
                    raw["apiDefinitions"][0]["name"] = "credential-manager-login"
                else:
                    raw["apiDefinitions"][0]["mcp"]["name"] = "credential-manager-browser"
                with self.assertRaisesRegex(configuration.ConfigurationError, "identifier"):
                    self.normalize(raw)

    def test_mcp_identifier_uses_the_api_name_length_limit(self):
        raw = pii_fixture()
        raw["apiDefinitions"][0]["mcp"]["name"] = "m" * 80
        actual = self.normalize(raw)
        self.assertEqual(len(actual["apiDefinitions"][0]["mcp"]["name"]), 80)
        raw["apiDefinitions"][0]["mcp"]["name"] = "m" * 81
        with self.assertRaisesRegex(configuration.ConfigurationError, "mcp.name"):
            self.normalize(raw)

    def test_audit_readers_are_scoped_to_owned_collector_containers(self):
        raw = fixture()
        principal = "77777777-7777-4777-8777-777777777777"
        raw["monitoring"]["auditReaders"] = [
            {"principalId": principal, "principalType": "Group", "collector": "collectJapan", "containerName": "insights-logs-gatewaylogs"},
            {"principalId": principal, "principalType": "Group", "collector": "collectJapan", "containerName": "insights-logs-gatewayllmlogs"},
            {"principalId": principal, "principalType": "Group", "collector": "collectJapan", "containerName": "insights-logs-websocketconnectionlogs"},
            {"principalId": principal, "principalType": "Group", "collector": "collectFoundry", "containerName": "insights-logs-audit"},
        ]
        actual = self.normalize(raw)
        self.assertEqual(configuration.build_bicep_configuration(actual)["monitoring"]["auditReaders"], raw["monitoring"]["auditReaders"])
        for collector, container in (("collectFoundry", "insights-logs-gatewaylogs"),
                                     ("collectFoundry", "insights-logs-gatewayllmlogs"),
                                     ("collectFoundry", "insights-logs-websocketconnectionlogs"),
                                     ("collectJapan", "$logs"), ("collectJapan", "all"), ("unownedCollector", "insights-logs-audit")):
            with self.subTest(collector=collector, container=container):
                broken = fixture()
                broken["monitoring"]["auditReaders"] = [{"principalId": principal, "principalType": "Group", "collector": collector, "containerName": container}]
                with self.assertRaises(configuration.ConfigurationError):
                    self.normalize(broken)
        raw["monitoring"]["auditReaders"].append(copy.deepcopy(raw["monitoring"]["auditReaders"][0]))
        with self.assertRaisesRegex(configuration.ConfigurationError, "duplicate"):
            self.normalize(raw)

    def test_azure_dns_and_ca_egress_derived(self):
        allowed = self.normalize()["network"]["firewall"]["managementAllowedFqdns"]
        self.assertIn("management.azure.com", allowed)
        self.assertIn("acme-v02.api.letsencrypt.org", allowed)
        self.assertNotIn("api.cloudflare.com", allowed)
        self.assertNotIn("route53.amazonaws.com", allowed)

    def test_azure_dns_api_egress_is_added_without_duplicates(self):
        for initial in ([], ["approved.example.com", "management.azure.com"]):
            with self.subTest(configured_hosts=bool(initial)):
                raw = fixture()
                raw["network"]["firewall"]["managementAllowedFqdns"] = initial
                allowed = self.normalize(raw)["network"]["firewall"]["managementAllowedFqdns"]
                for hostname in [*initial, "management.azure.com"]:
                    self.assertEqual(allowed.count(hostname), 1)
                self.assertIn("acme-v02.api.letsencrypt.org", allowed)

    def test_other_dns_plugins_are_unsupported_even_with_explicit_egress(self):
        for plugin in ("Cloudflare", "Route53", "UnlistedProvider", "azure"):
            for allow in ([], ["dns-api.example.com"]):
                with self.subTest(plugin=plugin, explicit_egress=bool(allow)):
                    raw = fixture()
                    raw["certificate"]["plugin"] = plugin
                    raw["network"]["firewall"]["managementAllowedFqdns"] = allow
                    with self.assertRaisesRegex(configuration.ConfigurationError, "Azure Public DNS"):
                        self.normalize(raw)
                    with mock.patch.object(configuration, "load_ssh_public_key", side_effect=AssertionError("no secret reads")):
                        with self.assertRaises(configuration.ConfigurationError):
                            configuration.load_secret_inputs(raw)

    def test_empty_api_set_is_reserved_for_standard_publication_without_expanding_during_validation(self):
        for publish in (False, True):
            with self.subTest(publish=publish):
                raw = fixture()
                raw["publishApis"] = publish
                result = self.normalize(raw)
                self.assertEqual(result["apiDefinitions"], [])
                self.assertIs(result["publishApis"], publish)

    def test_stateless_response_contract(self):
        self.assertEqual(self.normalize(api_fixture())["apiPublicationStatus"], "configured")

    def test_responses_cannot_bypass_stateless_contract(self):
        raw = api_fixture()
        raw["apiDefinitions"][0]["authorization"]["operationConstraints"]["responses-create"]["kind"] = "bodyModel"
        with self.assertRaisesRegex(configuration.ConfigurationError, "explicit stateless"):
            self.normalize(raw)

    def test_stateful_agent_and_other_response_routes_are_rejected(self):
        for method, route in (("GET", "/responses"), ("DELETE", "/responses/{response_id}"), ("POST", "/agents/invoke")):
            with self.subTest(method=method, route=route):
                raw = api_fixture()
                op = raw["apiDefinitions"][0]["operations"][0]
                op["method"], op["urlTemplate"] = method, route
                op["templateParameters"] = [{"name": "response_id", "type": "string", "required": True}] if "{" in route else []
                with self.assertRaisesRegex(configuration.ConfigurationError, "only resource Models"):
                    self.normalize(raw)

    def test_undeployed_model_rejected(self):
        raw = api_fixture()
        raw["apiDefinitions"][0]["authorization"]["operationConstraints"]["responses-create"]["allowedModels"] = ["other-model"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "configured model"):
            self.normalize(raw)

    def test_optional_quota_usage_name_is_explicit_public_metadata(self):
        raw = api_fixture()
        raw["services"]["modelDeployments"][0]["quotaUsageName"] = "OpenAI.GlobalStandard.gpt-approved"
        actual = self.normalize(raw)
        self.assertEqual(actual["services"]["modelDeployments"][0]["quotaUsageName"], "OpenAI.GlobalStandard.gpt-approved")
        for invalid in ("", " padded ", None, 123):
            raw["services"]["modelDeployments"][0]["quotaUsageName"] = invalid
            with self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_model_names_and_versions_are_pinned(self):
        raw = api_fixture()
        raw["services"]["modelDeployments"][0]["versionUpgradeOption"] = "OnceNewDefaultVersionAvailable"
        with self.assertRaisesRegex(configuration.ConfigurationError, "NoAutoUpgrade"):
            self.normalize(raw)

    def test_microsoft_deployment_alias_preserves_formal_identity_and_pinning(self):
        """Microsoft の予約語回避だけを許し、モデルの正式名や版は書き換えない。"""
        raw = fixture()
        model = {"name": "decision-1", "modelName": "Microsoft-Decision-1", "modelFormat": "Microsoft",
                 "modelVersion": "1", "skuName": "DataZoneStandard", "capacity": 10,
                 "versionUpgradeOption": "NoAutoUpgrade"}
        raw["services"]["modelDeployments"] = [model]
        normalized = self.normalize(raw)
        self.assertEqual(normalized["services"]["modelDeployments"][0], model)
        for unsafe in ("/decision", "decision/other", "a?sig=value", "a" * 65, ".decision", "decision name"):
            value = copy.deepcopy(raw); value["services"]["modelDeployments"][0]["name"] = unsafe
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(configuration.ConfigurationError, "safe model deployment"):
                self.normalize(value)
        raw["services"]["modelDeployments"][0]["versionUpgradeOption"] = "OnceNewDefaultVersionAvailable"
        with self.assertRaisesRegex(configuration.ConfigurationError, "NoAutoUpgrade"):
            self.normalize(raw)

    def test_other_provider_aliases_remain_rejected(self):
        """既存providerまで別名を解放せず、今回の変更範囲を保つ。"""
        for provider, formal in (("OpenAI", "gpt-approved"), ("Cohere", "Cohere-rerank")):
            raw = fixture()
            raw["services"]["modelDeployments"] = [{"name": "an-alias", "modelName": formal, "modelFormat": provider,
                "modelVersion": "1", "skuName": "GlobalStandard", "capacity": 1, "versionUpgradeOption": "NoAutoUpgrade"}]
            with self.subTest(provider=provider), self.assertRaisesRegex(configuration.ConfigurationError, "except for Microsoft"):
                self.normalize(raw)


    def test_prepared_certificate_import_is_rejected_before_secret_reads(self):
        for include_files in (False, True):
            raw = fixture()
            raw["certificate"] = {"mode": "prepared"}
            if include_files:
                raw["certificate"].update(preparedPfxFile="../secrets/server.pfx", preparedPfxPasswordFile="../secrets/pfx-password.txt")
            with mock.patch.object(configuration, "_read_protected_file", side_effect=AssertionError("must not read supplied PFX")):
                with self.assertRaises(configuration.ConfigurationError): self.normalize(raw)
                with self.assertRaisesRegex(configuration.ConfigurationError, "DNS-01 issuance requires dnsPlugin mode"):
                    configuration.load_secret_inputs(raw)

    def test_azure_secret_inputs_do_not_read_or_forward_dns_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            public_key = Path(directory) / "public-key.pub"
            public_key.write_text(ssh_fixture_key() + "\n")
            raw = fixture()
            raw["management"]["sshPublicKeyFile"] = str(public_key)
            normalized = self.normalize(raw)
            with mock.patch.object(configuration, "load_dns_plugin_arguments", side_effect=AssertionError("Azure uses managed identity")):
                secrets = configuration.load_secret_inputs(normalized)
            self.assertEqual(secrets["dnsPluginArgumentsJson"], "")
            self.assertNotIn("preparedPfxBase64", secrets)
            self.assertNotIn("preparedPfxPassword", secrets)
            self.assertNotIn("pluginArgumentsFile", configuration.build_bicep_configuration(normalized)["certificate"])

    def test_plain_cloudflare_token_is_rejected_without_value_in_error(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            public_key, plugin = target / "public-key.pub", target / "plugin"
            public_key.write_text(ssh_fixture_key())
            plugin.write_text(json.dumps({"CFToken": "never-print-this-fixture"}))
            plugin.chmod(0o600)
            raw = fixture()
            raw["management"]["sshPublicKeyFile"] = str(public_key)
            raw["certificate"]["pluginArgumentsFile"] = str(plugin)
            with self.assertRaises(configuration.ConfigurationError) as error:
                configuration.load_secret_inputs(self.normalize(raw))
            self.assertNotIn("never-print-this-fixture", str(error.exception))

    def test_ssh_public_key_accepts_world_readable_public_file_and_rejects_private_or_malformed_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            public_key = Path(directory) / "management.pub"
            public_key.write_text(ssh_fixture_key())
            public_key.chmod(0o644)
            self.assertEqual(configuration.load_ssh_public_key(str(public_key)), ssh_fixture_key())
            for value in ("-----BEGIN OPENSSH PRIVATE KEY-----\nSENTINEL\n-----END OPENSSH PRIVATE KEY-----",
                          "ssh-ed25519 not-base64", "ssh-ed25519 " + base64.b64encode(b"bad").decode(),
                          ssh_fixture_key() + "\n" + ssh_fixture_key()):
                public_key.write_text(value)
                with self.assertRaisesRegex(configuration.ConfigurationError, "public key") as caught:
                    configuration.load_ssh_public_key(str(public_key))
                self.assertNotIn("SENTINEL", str(caught.exception))

    def test_environment_name_does_not_select_configuration_defaults(self):
        raw = fixture()
        before = self.normalize(raw)
        raw["environmentName"] = "lab-01"
        actual = self.normalize(raw)
        self.assertEqual(actual["resourceStem"], "lab-01-egakumi")
        for key in ("network", "gateway", "monitoring"):
            self.assertEqual(actual[key], before[key])
        self.assertEqual(actual["services"]["apimSkuName"], before["services"]["apimSkuName"])

    def test_linux_patch_modes_and_windows_password_configuration_are_rejected(self):
        for mode in ("AutomaticByOS", "Manual"):
            raw = fixture()
            raw["management"]["patchMode"] = mode
            with self.assertRaisesRegex(configuration.ConfigurationError, "patchMode"):
                self.normalize(raw)
        raw = fixture()
        raw["management"]["adminPasswordFile"] = "not-used"
        with self.assertRaisesRegex(configuration.ConfigurationError, "adminPasswordFile"):
            self.normalize(raw)


class DnsPluginInputTests(unittest.TestCase):
    """初回入力の検査がSSH未作成でも動き、資格情報を出力しないことを調べる。"""

    def test_standalone_dns_reader_preserves_provider_types_without_ssh_or_output(self):
        cases = {
            "Cloudflare": {"CFToken": {"type": "secureString", "value": "fixture-cloudflare-secret"}},
            "Route53": {"R53AccessKey": "fixture-access-key",
                        "R53SecretKey": {"type": "secureString", "value": "fixture-aws-secret"}},
            "Azure": {"AZSubscriptionId": "11111111-1111-4111-8111-111111111111", "AZTenantId": "22222222-2222-4222-8222-222222222222",
                      "AZAppCred": {"type": "credential", "username": "33333333-3333-4333-8333-333333333333", "password": "fixture-azure-secret"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dns.json"
            missing_ssh = Path(directory) / "management-ssh.pub"
            for plugin, arguments in cases.items():
                with self.subTest(plugin=plugin):
                    path.write_text(json.dumps(arguments))
                    path.chmod(0o600)
                    output, errors = io.StringIO(), io.StringIO()
                    with mock.patch.object(configuration, "load_ssh_public_key", side_effect=AssertionError("DNS input must not load SSH")) as ssh_reader:
                        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                            actual = configuration.load_dns_plugin_arguments({"plugin": plugin, "pluginArgumentsFile": str(path)})
                    expected = {key: {"type": "string", "value": value} if isinstance(value, str) else value
                                for key, value in arguments.items()}
                    self.assertEqual(json.loads(actual), expected)
                    ssh_reader.assert_not_called()
                    self.assertFalse(missing_ssh.exists())
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(errors.getvalue(), "")

    def test_advanced_plugin_typed_arguments_remain_supported(self):
        arguments = {
            "PrivateValue": {"type": "secureString", "value": "fixture-private-value"},
            "Credential": {"type": "credential", "username": "fixture-user", "password": "fixture-password"},
            "Account": "fixture-account", "UseIdentity": {"type": "boolean", "value": True},
            "Attempts": {"type": "integer", "value": 3},
            "Zones": {"type": "stringArray", "value": ["example.com", "example.net"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dns.json"
            path.write_text(json.dumps(arguments))
            path.chmod(0o600)
            actual = json.loads(configuration.load_dns_plugin_arguments({"plugin": "AdvancedProvider", "pluginArgumentsFile": str(path)}))
            arguments["Account"] = {"type": "string", "value": "fixture-account"}
            self.assertEqual(actual, arguments)

    def test_invalid_standalone_dns_inputs_hide_values_and_emit_nothing(self):
        sentinel = "never-print-this-dns-secret"
        invalid = [
            sentinel, json.dumps([sentinel]), "{}",
            json.dumps({"CFToken": sentinel}),
            json.dumps({sentinel: {"type": "secureString", "value": sentinel}}),
            json.dumps({"CFToken": {"type": "secureString", "value": "", "extra": sentinel}}),
            json.dumps({"CFToken": {"type": "secureString", "value": ""}}),
            json.dumps({"CFToken": {"type": "secureString", "value": sentinel}, "Flag": {"type": "boolean", "value": sentinel}}),
            json.dumps({"CFToken": {"type": "secureString", "value": sentinel}, "Count": {"type": "integer", "value": True}}),
            json.dumps({"CFToken": {"type": "secureString", "value": sentinel}, "Zones": {"type": "stringArray", "value": [""]}}),
            json.dumps({"CFToken": {"type": "secureString", "value": sentinel}, "Identity": {"type": "credential", "username": "fixture", "password": ""}}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dns.json"
            for content in invalid:
                with self.subTest(index=invalid.index(content)):
                    path.write_text(content)
                    path.chmod(0o600)
                    output, errors = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        with self.assertRaises(configuration.ConfigurationError) as caught:
                            configuration.load_dns_plugin_arguments({"plugin": "Cloudflare", "pluginArgumentsFile": str(path)})
                    self.assertNotIn(sentinel, str(caught.exception))
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(errors.getvalue(), "")

    @unittest.skipIf(os.name == "nt", "POSIX private file mode check")
    def test_standalone_dns_reader_keeps_protected_file_permissions_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dns.json"
            path.write_text(json.dumps({"CFToken": {"type": "secureString", "value": "fixture-secret"}}))
            path.chmod(0o644)
            with self.assertRaisesRegex(configuration.ConfigurationError, "chmod 600") as caught:
                configuration.load_dns_plugin_arguments({"plugin": "Cloudflare", "pluginArgumentsFile": str(path)})
            self.assertNotIn("fixture-secret", str(caught.exception))

    def read_arguments(self, plugin, arguments):
        """一時領域の合成値だけを通し、現在の端末のsecretsは読みません。"""
        with tempfile.TemporaryDirectory(prefix="core-dns-signature-") as folder:
            path = Path(folder) / "dns.json"
            path.write_text(json.dumps(arguments))
            path.chmod(0o600)
            return json.loads(configuration.load_dns_plugin_arguments({"plugin": plugin, "pluginArgumentsFile": str(path)}))

    def standard_arguments(self):
        return {
            "Cloudflare": {"CFToken": {"type": "secureString", "value": "signature-test-secret"}},
            "Route53": {"R53AccessKey": "signature-test-access", "R53SecretKey": {"type": "secureString", "value": "signature-test-secret"}},
            "Azure": {"AZSubscriptionId": "11111111-1111-4111-8111-111111111111", "AZTenantId": "22222222-2222-4222-8222-222222222222",
                      "AZAppCred": {"type": "credential", "username": "33333333-3333-4333-8333-333333333333", "password": "signature-test-secret"}},
        }

    def test_standard_provider_credentials_cannot_be_reused_for_another_provider(self):
        for selected in self.standard_arguments():
            for original, arguments in self.standard_arguments().items():
                with self.subTest(selected=selected, original=original):
                    if selected == original:
                        self.read_arguments(selected, arguments)
                    else:
                        with self.assertRaises(configuration.DnsProviderMismatchError) as error:
                            self.read_arguments(selected, arguments)
                        self.assertNotIn("signature-test-secret", str(error.exception))

    def test_known_foreign_arguments_are_rejected_even_when_selected_credentials_are_present(self):
        examples = self.standard_arguments()
        for plugin, arguments in examples.items():
            for foreign, other in examples.items():
                if plugin == foreign:
                    continue
                with self.subTest(plugin=plugin, foreign=foreign), self.assertRaises(configuration.DnsProviderMismatchError):
                    self.read_arguments(plugin, {**arguments, **other})

    def test_standard_provider_missing_required_field_or_wrong_type_is_a_provider_mismatch(self):
        cases = [
            ("Cloudflare", {"CFToken": "signature-test-secret"}),
            ("Route53", {"R53AccessKey": "access-only"}),
            ("Route53", {"R53AccessKey": "access", "R53SecretKey": "not-a-secure-string"}),
            ("Route53", {"R53UseIAMRole": {"type": "boolean", "value": False}}),
            ("Azure", {"AZSubscriptionId": "11111111-1111-4111-8111-111111111111"}),
            ("Azure", {"AZSubscriptionId": "invalid-guid", "AZUseIMDS": {"type": "boolean", "value": True}}),
            ("Azure", {"AZSubscriptionId": "11111111-1111-4111-8111-111111111111", "AZUseIMDS": {"type": "boolean", "value": False}}),
        ]
        for plugin, arguments in cases:
            with self.subTest(plugin=plugin, fields=list(arguments)), self.assertRaises(configuration.DnsProviderMismatchError):
                self.read_arguments(plugin, arguments)

    def test_advanced_official_authentication_forms_remain_supported(self):
        sub = "11111111-1111-4111-8111-111111111111"
        tenant = "22222222-2222-4222-8222-222222222222"
        application = "33333333-3333-4333-8333-333333333333"
        cases = [
            ("Cloudflare", {"CFAuthEmail": "dns@example.com", "CFAuthKeySecure": {"type": "secureString", "value": "synthetic-key"}}),
            ("Route53", {"R53ProfileName": "synthetic-profile", "R53Region": "us-east-1"}),
            ("Route53", {"R53UseIAMRole": {"type": "boolean", "value": True}}),
            ("Azure", {"AZSubscriptionId": sub, "AZUseIMDS": {"type": "boolean", "value": True}}),
            ("Azure", {"AZSubscriptionId": sub, "AZTenantId": tenant, "AZAppUsername": application,
                       "AZUseIMDS": {"type": "boolean", "value": True}, "AZArcAgentAPIVersion": "2021-02-01"}),
            ("Azure", {"AZSubscriptionId": sub, "AZTenantId": tenant, "AZAppUsername": application, "AZCertPfx": "/synthetic-only/unlocked.pfx"}),
            ("Azure", {"AZSubscriptionId": sub, "AZAccessTokenSecure": {"type": "secureString", "value": "synthetic-token"}}),
            ("Azure", {"AZSubscriptionId": sub, "AZTenantId": tenant, "AZAppUsername": application,
                       "AZCertPfx": "/synthetic-only/cert.pfx", "AZPfxPassSecure": {"type": "secureString", "value": "synthetic-password"}}),
        ]
        for plugin, arguments in cases:
            with self.subTest(plugin=plugin, fields=list(arguments)):
                actual = self.read_arguments(plugin, arguments)
                self.assertEqual(set(actual), set(arguments))

    def test_corrupted_typed_values_are_not_a_provider_mismatch(self):
        for arguments in ({"CFToken": {"type": "secureString", "value": ""}},
                          {"CFToken": {"type": "secureString", "value": "synthetic", "extra": "bad"}},
                          {"CFToken": {"type": "unknown", "value": "synthetic"}}):
            with self.subTest(arguments=arguments), self.assertRaises(configuration.ConfigurationError) as error:
                self.read_arguments("Cloudflare", arguments)
            self.assertNotIsInstance(error.exception, configuration.DnsProviderMismatchError)

    def test_powershell_case_insensitive_names_work_and_duplicates_are_rejected(self):
        self.read_arguments("Cloudflare", {"cftoken": {"type": "secureString", "value": "synthetic"}})
        arguments = {"CFToken": {"type": "secureString", "value": "synthetic"}, "cftoken": {"type": "secureString", "value": "other"}}
        with self.assertRaises(configuration.ConfigurationError) as error:
            self.read_arguments("Cloudflare", arguments)
        self.assertNotIsInstance(error.exception, configuration.DnsProviderMismatchError)


class UnknownConfigurationFieldTests(unittest.TestCase):
    """Reject unrecognized fields at every public configuration boundary."""

    def test_unknown_fields_are_rejected_by_loader_and_editor_schemas(self):
        from schema_helpers import local_schema_validator
        locations = ((), ("services",), ("gateway", "authentication"),
                     ("apiDefinitions", 0), ("apiDefinitions", 0, "authorization"),
                     ("apiDefinitions", 0, "mcp"))
        schemas = {
            name: local_schema_validator(json.loads((CORE / f"schemas/{name}.schema.json").read_text()))
            for name in ("configuration", "params")
        }
        for location in locations:
            with self.subTest(location=location):
                raw = pii_fixture()
                target = raw
                for part in location:
                    target = target[part]
                target["unexpectedSetting"] = "synthetic"
                with self.assertRaisesRegex(configuration.ConfigurationError, "unexpectedSetting"):
                    configuration.normalize_configuration(raw, base_directory=CORE / "config")
                for name, validator in schemas.items():
                    with self.subTest(schema=name):
                        errors = list(validator.iter_errors(raw))
                        self.assertTrue(any(error.validator == "additionalProperties"
                                            and "unexpectedSetting" in error.message for error in errors))


class PolicySourceTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.root = ET.parse(CORE / "apps/gateway/apim/policies/gateway.xml").getroot()
        self.guard = next(element.attrib["value"] for element in self.root.iter("set-variable") if element.attrib.get("name") == "gatewayTargetAllowed")

    def test_stateless_post_has_exact_route_and_allowed_models(self):
        self.assertIn('kind == "statelessResponses"', self.guard)
        self.assertIn('context.Request.Method != "POST"', self.guard)
        self.assertIn('context.Operation.UrlTemplate != "/responses"', self.guard)
        self.assertIn('constraint["allowedModels"]', self.guard)

    def test_state_lookup_fields_not_in_explicit_allowed_fields(self):
        declaration = self.guard.split("var allowedFields =", 1)[1].split(";", 1)[0]
        for forbidden in ("previous_response_id", "conversation", "metadata", "prompt"):
            self.assertNotIn('"' + forbidden + '"', declaration)
        self.assertIn("!allowedFields.Contains(p.Name)", self.guard)
        self.assertIn('"item_reference"', self.guard)
        self.assertIn('"$..file_id"', self.guard)
        self.assertIn('"background"', self.guard)

    def test_store_false_is_forced_without_rewriting_sse_response(self):
        branch = next(node for node in self.root.findall("inbound/choose/when") if "gatewayStatelessResponses" in node.attrib.get("condition", ""))
        bodies = list(branch.iter("set-body"))
        self.assertEqual(len(bodies), 1)
        self.assertIn('body["store"] = false', bodies[0].text)
        self.assertIsNone(self.root.find("outbound/set-body"))
        self.assertEqual(self.root.find("backend/forward-request").attrib["buffer-response"], "false")


if __name__ == "__main__":
    unittest.main()
