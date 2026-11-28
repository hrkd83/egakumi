"""公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証する。

一時フォルダー内の合成設定を使い、命名・サブネット・API認可・SSH公開鍵・Azure DNSのマネージドID入力を調べる。実在するテナントや秘密値をテストの正解にしない。
"""
import base64
import copy
import contextlib
import io
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


from tests.support.configuration_fixtures import (
    CORE, configuration, fixture, ssh_fixture_key, api_fixture,
    profiled_api_fixture, connection_fixture, pii_fixture, pii_key_fixture, bind_credential_fixture_id,
)
from tests.support.schema_helpers import read_schema
from deployment.helpers.firewall_destinations import generated_apim_service_fqdns


class PublicSampleTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def sample(self, name):
        manifest = CORE / "params" / (name + ".example.json")
        parameters = json.loads(manifest.read_text(encoding="utf-8"))
        options = json.loads((manifest.parent / parameters["configFile"]).read_text(encoding="utf-8"))
        raw = configuration.load_configuration_source(manifest)
        bind_credential_fixture_id(raw)
        raw["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
        raw["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
        raw["entraTenantId"] = raw["azureTenantId"]
        raw["certificate"]["azureDnsSubscriptionId"] = raw["subscriptionId"]
        raw["certificate"]["azureDnsManagedIdentityResourceId"] = (
            "/subscriptions/" + raw["subscriptionId"] +
            "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
        raw["credentialProviders"][0]["clientId"] = "44444444-4444-4444-8444-444444444444"
        raw["credentialProviders"][0]["oauthTenantId"] = raw["entraTenantId"]
        raw["services"]["apimInstanceId"] = "abc123"
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

    def test_schema_and_templates_distinguish_generated_apim_identifier(self):
        for filename in ("params.schema.json", "configuration.schema.json"):
            service = read_schema(filename.removesuffix(".schema.json"))["properties"]["services"]
            with self.subTest(schema=filename):
                self.assertIn("apimInstanceId", service["required"])
                self.assertEqual(service["properties"]["apimInstanceId"]["pattern"], "^[a-z0-9]{6}$")
                self.assertNotIn("default", service["properties"]["apimInstanceId"])
        for name in ("test", "production"):
            parameters, options, actual = self.sample(name)
            with self.subTest(profile=name):
                self.assertEqual(parameters["services"]["apimInstanceId"], "<AUTO_GENERATED_APIM_INSTANCE_ID>")
                self.assertEqual(actual["services"]["apimInstanceId"], "abc123")
                self.assertNotIn("apimInstanceId", options["services"])

    def test_required_org_inputs_are_separate_from_standard_options(self):
        for name in ("test", "production"):
            with self.subTest(name=name):
                parameters, options, actual = self.sample(name)
                self.assertEqual(parameters["configFile"], "../infra/config/" + name + ".json")
                self.assertEqual(parameters["productName"], "egakumi")
                self.assertEqual(parameters["subscriptionId"], "00000000-0000-0000-0000-000000000000")
                self.assertEqual(parameters["entraTenantId"], "00000000-0000-0000-0000-000000000000")
                self.assertNotIn("subscriptionKeyHeader", parameters["gateway"]["authentication"])
                self.assertEqual(actual["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
                self.assertEqual(options["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
                self.assertEqual(parameters["credentialProviders"]["graphDelegated"]["clientId"], "00000000-0000-0000-0000-000000000000")
                self.assertNotIn("oauthTenantId", parameters["credentialProviders"]["graphDelegated"])
                provider = actual["credentialProviders"][0]
                self.assertEqual(provider["name"], "graph-delegated")
                self.assertEqual(provider["pool"], {"count": 12, "capacity": 8000})
                self.assertEqual(provider["clientId"], "44444444-4444-4444-8444-444444444444")
                self.assertEqual(provider["oauthTenantId"], actual["entraTenantId"])
                self.assertEqual(provider["connectionNames"], [])
                self.assertEqual(provider["identityEndpoint"], "https://graph.microsoft.com/v1.0/me")
                self.assertEqual(parameters["credentialProviders"]["graphDelegated"]["clientSecretFile"], "../secrets/entra-client-secret.txt")
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
                self.assertEqual(options["network"]["firewall"]["zones"], [])
                self.assertEqual(actual["network"]["firewall"]["zones"], [])
                self.assertEqual(options["network"]["firewall"]["idpsMode"], "Deny")
                self.assertEqual(actual["network"]["firewall"]["idpsMode"], "Deny")

    def test_network_defaults_match_current_firewall_and_monitoring_parameters(self):
        for name in ("test", "production"):
            with self.subTest(environment=name):
                parameters, options, actual = self.sample(name)
                addresses = parameters["network"]
                self.assertEqual(options["network"]["dnsTtl"], 300)
                self.assertEqual(actual["network"]["dnsTtl"], 300)
                self.assertEqual(actual["network"]["firewall"]["snatPrivateRanges"],
                                 ["IANAPrivateRanges", *addresses["addressSpaces"]])
                self.assertEqual(options["network"]["firewall"]["signatureOverrides"], [])
                self.assertEqual(actual["monitoring"]["auditAllowedIpAddresses"], [])

    def test_gateway_defaults_apply_waf_body_limits_and_realtime_control(self):
        for name in ("test", "production"):
            _, options, actual = self.sample(name)
            gateway = options["gateway"]
            self.assertEqual(gateway["wafMaxRequestBodySizeInKb"], 2000)
            self.assertEqual(gateway["wafRequestBodyInspectLimitInKb"], 2000)
            self.assertEqual(gateway["wafFileUploadLimitInMb"], 4)
            self.assertTrue(gateway["realtimeControlEnabled"])
            for key in ("wafMaxRequestBodySizeInKb", "wafRequestBodyInspectLimitInKb",
                        "wafFileUploadLimitInMb", "realtimeControlEnabled"):
                self.assertEqual(actual["gateway"][key], gateway[key])

    def test_org_inputs_cannot_be_deployed_with_unset_target_guids(self):
        for name in ("test", "production"):
            with self.subTest(name=name), self.assertRaisesRegex(configuration.ConfigurationError, "subscriptionId"):
                configuration.load_configuration(CORE / "params" / (name + ".example.json"))


class QueryObservationConfigurationTests(unittest.TestCase):
    """操作と受付に対するクエリー観測ラベルの入力を検証する。"""

    def test_http_and_generated_mcp_references_are_independent_and_optional(self):
        raw = pii_fixture()
        api = raw["apiDefinitions"][0]
        api["operations"][0]["queryObservation"] = "piiRestRule"
        api["mcp"]["queryObservation"] = "piiFacadeRule"
        result = configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")["apiDefinitions"][0]
        self.assertEqual(result["operations"][0]["queryObservation"], "piiRestRule")
        self.assertEqual(result["mcp"]["queryObservation"], "piiFacadeRule")
        self.assertNotIn("queryObservation", configuration.normalize_configuration(pii_fixture(), base_directory=CORE / "infra/config")["apiDefinitions"][0]["mcp"])
        for scope in ("operation", "mcp"):
            for value in (None, False, {}, "none", "unknown", "invalid.rule"):
                changed = pii_fixture()
                target = changed["apiDefinitions"][0]
                (target["operations"][0] if scope == "operation" else target["mcp"])["queryObservation"] = value
                with self.subTest(scope=scope, value=value), self.assertRaises(configuration.ConfigurationError):
                    configuration.normalize_configuration(changed, base_directory=CORE / "infra/config")


class CanonicalApiConfigurationTests(unittest.TestCase):
    """統合でURLの境界がなくなっても、元契約・権限・上限を保持する。"""

    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")

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

    def test_ai_contract_uses_technical_body_ceiling_and_provider_numeric_limits(self):
        raw = api_fixture()
        raw["apiDefinitions"][0].update(name="openai-inference-v1", path="models/openai/v1")
        constraint = raw["apiDefinitions"][0]["authorization"]["operationConstraints"]["responses-create"]
        constraint.update(maxRequestBytes=524288000, maxOutputTokens=2000000, arrayLimits={"input": 12000})
        actual = self.normalize(raw)["apiDefinitions"][0]["authorization"]["operationConstraints"]["responses-create"]
        self.assertEqual(actual["maxRequestBytes"], 524288000)
        self.assertEqual(actual["maxOutputTokens"], 2000000)
        self.assertEqual(actual["arrayLimits"]["input"], 12000)
        constraint["maxRequestBytes"] += 1
        with self.assertRaisesRegex(configuration.ConfigurationError, "maxRequestBytes"):
            self.normalize(raw)

    def test_operation_defaults_and_model_settings_preserve_limits(self):
        actual = self.normalize(profiled_api_fixture())["apiDefinitions"][0]
        self.assertEqual(actual["name"], "openai-inference-v1")
        self.assertEqual(actual["serviceClass"], "platform")
        authorization = actual["authorization"]
        self.assertEqual(authorization["mode"], "subscriptionKey")
        self.assertEqual(set(authorization), {"mode", "operationConstraints"})
        constraint = authorization["operationConstraints"]["responses-create"]
        settings = constraint["modelSettings"]
        self.assertEqual(constraint["allowedModels"], ["gpt-approved", "gpt-secondary"])
        self.assertEqual(constraint["maxRequestBytes"], 2048000)
        self.assertEqual(settings["gpt-approved"]["maxRequestBytes"], 32768)
        self.assertEqual(settings["gpt-secondary"]["maxRequestBytes"], 2048000)
        self.assertEqual(constraint["maxOutputTokens"], 128)
        self.assertNotIn("maxOutputTokens", settings["gpt-approved"])
        self.assertEqual(settings["gpt-secondary"]["maxOutputTokens"], 512)
        self.assertEqual(settings["gpt-approved"]["settingId"], "response")


    def test_model_settings_cannot_be_recursive_unknown_or_change_the_contract(self):
        for mutation in ("recursive", "unknownModel", "invalidSettingId", "wrongKind"):
            raw = profiled_api_fixture()
            authorization = raw["apiDefinitions"][0]["authorization"]
            constraint = authorization["operationConstraints"]["responses-create"]
            if mutation == "recursive": constraint["modelSettings"]["gpt-approved"]["modelSettings"] = {}
            if mutation == "unknownModel": constraint["modelSettings"]["unregistered"] = {}
            if mutation == "invalidSettingId": constraint["modelSettings"]["gpt-approved"]["settingId"] = "invalid*"
            if mutation == "wrongKind": constraint["modelSettings"]["gpt-approved"]["kind"] = "bodyModel"
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

    def test_flat_custom_api_without_optional_model_settings_is_supported(self):
        actual = self.normalize(api_fixture())["apiDefinitions"][0]
        self.assertEqual(actual["name"], "models")
        self.assertNotIn("modelSettings", actual["authorization"]["operationConstraints"]["responses-create"])


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
                {"$schema": "./params.schema.json", "configFile": "../infra/config/options.json"},
                base_directory=root / "params", output_directory=root / "generated/deep")
            self.assertEqual(saved, {"$schema": "../../params/params.schema.json",
                                     "configFile": "../../infra/config/options.json"})


class ConfigurationTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def normalize(self, value=None):
        return configuration.normalize_configuration(fixture() if value is None else value, base_directory=CORE / "infra/config")


    def test_certificate_subnet_is_allocated_from_free_space_or_checked_when_explicit(self):
        raw = fixture()
        prefixes = raw["network"]["subnetPrefixes"]
        prefixes.pop("certificate", None)
        derived = self.normalize(raw)["network"]["subnetPrefixes"]["certificate"]
        self.assertEqual(derived, "10.40.2.192/27")
        for prefix in ("10.40.3.0/28", prefixes["portal"], "192.0.2.0/27"):
            prefixes["certificate"] = prefix
            with self.subTest(prefix=prefix), self.assertRaisesRegex(configuration.ConfigurationError, "subnetPrefixes"):
                self.normalize(raw)

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
                         {"agw", "firewall", "apim", "pep", "management", "portal", "broker", "certificate"})

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
        self.assertEqual(actual["network"]["firewall"]["idpsMode"], "Deny")
        self.assertEqual(actual["apiPublicationStatus"], "not-published")

    def test_production_template_requires_target_ids(self):
        raw = configuration.load_configuration_source(CORE / "params/production.example.json")
        with self.assertRaisesRegex(configuration.ConfigurationError, "subscriptionId"):
            self.normalize(raw)
        raw["subscriptionId"], raw["azureTenantId"] = fixture()["subscriptionId"], fixture()["azureTenantId"]
        bind_credential_fixture_id(raw)
        raw["entraTenantId"] = raw["azureTenantId"]
        raw["certificate"]["azureDnsSubscriptionId"] = raw["subscriptionId"]
        raw["certificate"]["azureDnsManagedIdentityResourceId"] = (
            "/subscriptions/" + raw["subscriptionId"] +
            "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")
        raw["credentialProviders"][0]["clientId"] = "44444444-4444-4444-8444-444444444444"
        raw["credentialProviders"][0]["oauthTenantId"] = raw["entraTenantId"]
        raw["services"]["apimInstanceId"] = "abc123"
        self.assertEqual(raw["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
        self.assertEqual(raw["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        actual = self.normalize(raw)
        self.assertEqual(actual["credentialProviders"][0]["oauthTenantId"], actual["entraTenantId"])
        self.assertEqual(actual["network"]["firewall"]["sku"], "Premium")
        self.assertEqual(actual["network"]["firewall"]["idpsMode"], "Deny")
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

    def test_omitted_broker_subnet_is_derived_from_unused_vnet_space(self):
        raw = fixture()
        network = raw["network"]
        network["subnetPrefixes"].pop("broker", None)
        reserved = dict(network["subnetPrefixes"])
        spare = ipaddress.ip_network(network["addressSpaces"][2])
        allocated = tuple(map(ipaddress.ip_network, reserved.values()))
        remaining = next(subnet for subnet in spare.subnets(new_prefix=26)
                         if not any(subnet.overlaps(block) for block in allocated))
        self.assertEqual(self.normalize(raw)["network"]["subnetPrefixes"], {
            **reserved, "broker": str(remaining),
        })
        only_broker = next(remaining.subnets(new_prefix=27))
        network["addressSpaces"] = [*reserved.values(), str(only_broker)]
        self.assertEqual(self.normalize(raw)["network"]["subnetPrefixes"]["broker"], str(only_broker))
        network["addressSpaces"].pop()
        with self.assertRaisesRegex(configuration.ConfigurationError, r"subnetPrefixes\.broker"):
            self.normalize(raw)
        network["subnetPrefixes"]["broker"] = reserved["portal"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "overlap"):
            self.normalize(raw)

    def test_bgp_route_propagation_defaults_to_enabled_per_route_table(self):
        raw = fixture()
        raw["network"].pop("bgpRoutePropagation", None)
        self.assertEqual(self.normalize(raw)["network"]["bgpRoutePropagation"], {
            "agw": True, "apim": True, "management": True,
        })

    def test_fixed_network_rules_need_no_client_or_operator_configuration(self):
        raw = fixture()
        before = copy.deepcopy(raw)
        actual = self.normalize(raw)
        for output in (actual, configuration.build_bicep_configuration(actual)):
            for key in ("clientAddressPrefixes", "certificatePublicProbeEnabled",
                        "operatorAddressPrefixes", "operatorProtocol", "operatorPorts"):
                self.assertNotIn(key, output["network"])
            for key in ("addressSpaces", "subnetPrefixes"):
                self.assertEqual(output["network"][key], raw["network"][key])
        self.assertEqual(raw, before)

    def test_appgw_rule_inputs_are_rejected_even_with_fixed_values(self):
        cases = {
            "clientAddressPrefixes": (
                ["0.0.0.0/0"], ["203.0.113.10/32", "198.51.100.0/24"],
                ["203.0.113.10"], [], None, "203.0.113.10/32", {},
                True, [True], [1], [""], ["::/0"], ["203.0.113.10/24"],
                ["203.0.113.0/33"], ["PRIVATE-INPUT-SENTINEL"],
            ),
            "certificatePublicProbeEnabled": (
                True, False, None, [], {}, 1, "PRIVATE-INPUT-SENTINEL",
            ),
        }
        for key, values in cases.items():
            for value in values:
                raw = fixture()
                raw["network"][key] = value
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(
                            configuration.ConfigurationError,
                            r"network.*unsupported field.*" + key) as caught:
                        self.normalize(raw)
                    self.assertNotIn("PRIVATE-INPUT-SENTINEL", str(caught.exception))
        raw = fixture()
        raw["network"].update(
            clientAddressPrefixes=["0.0.0.0/0"],
            certificatePublicProbeEnabled=True,
        )
        with self.assertRaisesRegex(configuration.ConfigurationError,
                                    r"network.*unsupported field") as caught:
            self.normalize(raw)
        for key in cases:
            self.assertIn(key, str(caught.exception))

    def test_appgw_rule_inputs_stop_provisioning_before_external_operations(self):
        from deployment import lifecycle
        for settings in ({"clientAddressPrefixes": []},
                         {"certificatePublicProbeEnabled": True},
                         {"clientAddressPrefixes": ["0.0.0.0/0"],
                          "certificatePublicProbeEnabled": False}):
            raw = fixture()
            raw["network"].update(settings)
            raw["management"]["sshPublicKeyFile"] = "fixture-management-key.pub"
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "combined.json"
                source.write_text(json.dumps(raw), encoding="utf-8")
                errors = io.StringIO()
                with mock.patch.object(lifecycle, "preprovision") as provision, \
                     mock.patch.object(lifecycle, "ArmTransport") as transport, \
                     mock.patch.object(lifecycle, "run") as external, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                    self.assertEqual(lifecycle.main(["preprovision", "--config", str(source)]), 1)
                self.assertIn("network", errors.getvalue())
                for key in settings:
                    self.assertIn(key, errors.getvalue())
                provision.assert_not_called()
                transport.assert_not_called()
                external.assert_not_called()

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

    def test_firewall_requires_premium(self):
        raw = fixture()
        raw["network"]["firewall"]["sku"] = "Standard"
        with self.assertRaisesRegex(configuration.ConfigurationError, r"network\.firewall\.sku"):
            self.normalize(raw)

    def test_premium_does_not_allow_idps_off(self):
        raw = fixture()
        raw["network"]["firewall"].update(sku="Premium", idpsMode="Off")
        with self.assertRaises(configuration.ConfigurationError):
            self.normalize(raw)

    def test_firewall_tls_input_loads_only_public_root_and_versioned_reference(self):
        import shutil
        import ssl
        import subprocess

        openssl = shutil.which("openssl")
        if openssl is None:
            self.skipTest("OpenSSL is required to generate a synthetic test certificate")
        raw = fixture()
        authority_host = ".".join(("fixture", "vault", "azure", "net"))
        secret_container = "https://" + authority_host + "/secrets/inspection-ca"
        secret_uri = secret_container + "/" + "1" * 32
        identity = ("/subscriptions/" + raw["subscriptionId"] +
                    "/resourceGroups/rg-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-fixture-ca")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / "ca.cer", root / "ca.key"
            subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                            "-subj", "/CN=fixture.invalid", "-addext", "basicConstraints=critical,CA:TRUE",
                            "-keyout", str(key), "-out", str(cert)],
                           check=True, capture_output=True)
            cert.chmod(0o600)
            raw["firewallTlsInspection"] = {"managedIdentityResourceId": identity,
                                            "intermediateCaSecretId": secret_uri,
                                            "rootCaCertificateFile": str(cert)}
            resolved = self.normalize(raw)
            expected = base64.b64encode(ssl.PEM_cert_to_DER_cert(cert.read_text())).decode("ascii")
            self.assertEqual(configuration.load_firewall_tls_inspection(resolved), {
                "managedIdentityResourceId": identity, "intermediateCaSecretId": secret_uri, "rootCaData": expected,
            })
            self.assertNotIn("firewallTlsInspection", configuration.build_bicep_configuration(resolved))
            settings = resolved["firewallTlsInspection"]
            settings["intermediateCaSecretId"] = secret_container
            with self.assertRaisesRegex(configuration.ConfigurationError, "versioned Azure Key Vault"):
                configuration.load_firewall_tls_inspection(resolved)
            settings["intermediateCaSecretId"] = secret_uri
            settings["rootCaCertificateFile"] = str(key)
            with self.assertRaisesRegex(configuration.ConfigurationError, "public root CA"):
                configuration.load_firewall_tls_inspection(resolved)

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
        # pathModelもモデル名以外の固定URIを設定として保持し、別のparameter名で登録できます。
        operation.update(urlTemplate="/vendors/{vendor}/models/{modelName}",
                         templateParameters=[{"name": name, "type": "string", "required": True} for name in ("vendor", "modelName")],
                         backendUrlTemplate="/custom/{vendor}/models/{modelName}/invoke?api-version=preview")
        raw["apiDefinitions"][0]["authorization"]["operationConstraints"][operation["name"]].update(
            kind="pathModel", parameter="modelName", allowedModels=["synthetic-model"])
        result = configuration.build_bicep_configuration(self.normalize(raw))["apiDefinitions"][0]
        self.assertEqual(result["operations"][0]["backendUrlTemplate"], "/custom/{vendor}/models/{modelName}/invoke?api-version=preview")
        self.assertEqual(result["backendUrl"], raw["apiDefinitions"][0]["backendUrl"])
        self.assertEqual(result["authorization"]["operationConstraints"][operation["name"]]["parameter"], "modelName")
        self.assertEqual(result["authorization"]["operationConstraints"][operation["name"]]["allowedModels"], ["synthetic-model"])

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

    @mock.patch.object(configuration, "load_firewall_tls_inspection", return_value={})
    def test_credential_provider_secrets_load_only_from_protected_files(self, _firewall_tls):
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
        # 内部設定の読取りでは所有するcollector/containerへのgrantを保持します。
        self.assertEqual(actual["monitoring"]["auditReaders"], raw["monitoring"]["auditReaders"])
        # 固定資材へ渡す際は旧overrideを黙って無視せず、移行確認のため拒否します。
        with self.assertRaisesRegex(configuration.ConfigurationError, r"monitoring\.auditReaders"):
            configuration.build_bicep_configuration(actual)
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

    def test_certificate_directory_remains_a_business_setting_without_public_firewall_lists(self):
        for directory in ("production", "staging"):
            raw = fixture()
            raw["certificate"]["directory"] = directory
            with self.subTest(directory=directory):
                result = self.normalize(raw)
                self.assertEqual(result["certificate"]["directory"], directory)
                self.assertNotIn("managementAllowedFqdns", result["network"]["firewall"])

    def test_internal_api_destinations_are_reproducible_and_follow_backend_overrides_and_identity(self):
        empty = configuration.build_bicep_configuration(self.normalize())
        self.assertEqual(empty["generatedApimServiceFqdns"], [])
        raw = connection_fixture()
        raw["apiDefinitions"][0].pop("credentialConnection")
        raw["apiDefinitions"][0]["operations"][0]["backendOverride"] = {
            "backendService": "external", "backendUrl": "https://override.example.com/v1",
            "backendAudience": "https://override.example.com", "backendAuthentication": {"mode": "none"},
        }
        before = copy.deepcopy(raw)
        first = configuration.build_bicep_configuration(self.normalize(raw))
        second = configuration.build_bicep_configuration(self.normalize(raw))
        self.assertEqual(first["generatedApimServiceFqdns"],
                         ["graph.microsoft.com", "management.azure.com", "override.example.com"])
        self.assertEqual(second["generatedApimServiceFqdns"], first["generatedApimServiceFqdns"])
        self.assertEqual(raw, before)
        self.assertNotIn("apimAllowedFqdns", first["network"]["firewall"])
        raw["generatedApimServiceFqdns"] = []
        with self.assertRaisesRegex(configuration.ConfigurationError, "unsupported field.*generatedApimServiceFqdns"):
            self.normalize(raw)

    def test_internal_firewall_destinations_stop_on_unrepresentable_tls_backends_without_echoing_input(self):
        urls = (
            "http://private-input-marker.example.com/v1", "ws://private-input-marker.example.com/v1",
            "https://192.0.2.70/v1", "https://[2001:db8::70]/v1",
            "https://private-input-marker.example.com:8443/v1",
            "https://private-input-marker.example.com:invalid/v1",
            "https://user:private-input-marker@backend.example.com/v1",
            "https://private-input-marker.example.com/v1#fragment",
            "https://backend.example.com/v1?api_key=private-input-marker",
            "https://*.example.com/v1", "https://backend_name.example.com/v1",
            "https://-backend.example.com/v1", "https://backend-.example.com/v1",
            "https://backend..example.com/v1", "https://backend/v1",
            "https://" + "a" * 64 + ".example.com/v1",
            "https://" + ".".join(["a" * 63] * 4) + "/v1",
            *("https://back" + chr(code) + "end.example.com/v1" for code in (0, 9, 10, 13, 32, 127)),
        )
        for url in urls:
            for source in ("api", "override", "identity"):
                backend = {"backendService": "external", "backendUrl": url}
                document = {"apiDefinitions": [backend]} if source == "api" else (
                    {"apiDefinitions": [{"operations": [{"backendOverride": backend}]}]}
                    if source == "override" else {
                        "gateway": {"authentication": {"credentialLogin": {"providerNames": ["selected"]}}},
                        "credentialProviders": [{"name": "selected", "identityEndpoint": url}],
                    })
                with self.subTest(source=source, url=url), self.assertRaises(ValueError) as caught:
                    generated_apim_service_fqdns(document)
                self.assertNotIn(url, str(caught.exception))
                self.assertNotIn("private-input-marker", str(caught.exception))
        for url in ("https://192.0.2.70/v1", "https://backend.example.com:8443/v1",
                    "https://*.example.com/v1", "https://backend_name.example.com/v1",
                    "https://back" + chr(0) + "end.example.com/v1"):
            raw = connection_fixture()
            raw["apiDefinitions"][0]["backendUrl"] = url
            with self.subTest(core_backend=url), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)

    def test_internal_firewall_destinations_accept_fixed_dns_tls_endpoints(self):
        for scheme in ("https", "wss"):
            for port in ("", ":443"):
                document = {"apiDefinitions": [{"backendService": "external",
                    "backendUrl": scheme + "://Backend-1.Example.com" + port + "/v1"}]}
                with self.subTest(scheme=scheme, port=port):
                    self.assertEqual(generated_apim_service_fqdns(document), ["backend-1.example.com"])

    def test_broker_defaults_are_validated_without_personal_login_or_fabricated_profiles(self):
        normalized = self.normalize()
        result = configuration.build_bicep_configuration(normalized)
        self.assertNotIn("credentialLogin", result["gateway"]["authentication"])
        self.assertEqual(result["credentialProfiles"], {})
        self.assertEqual(result["credentialPhysicalProviders"], [])
        self.assertEqual(result["credentialBroker"]["sqlCapacity"], 2)
        self.assertEqual(result["credentialBroker"]["minReplicas"], 2)
        self.assertEqual(result["credentialBrokerSession"]["transactionLifetimeSeconds"], 300)
        self.assertEqual(result["credentialBrokerSession"]["loginCallsPerMinute"], 10)
        for broken in ({"sqlCapacity": 0}, {"minReplicas": 0}, {"minReplicas": 3, "maxReplicas": 2}):
            raw = fixture()
            raw["credentialBroker"] = broken
            with self.subTest(configuration=broken), self.assertRaisesRegex(
                    configuration.ConfigurationError, "credentialBroker"):
                self.normalize(raw)

    def test_other_dns_plugins_are_unsupported_without_reading_secrets(self):
        for plugin in ("Cloudflare", "Route53", "UnlistedProvider", "azure"):
            with self.subTest(plugin=plugin):
                raw = fixture()
                raw["certificate"]["plugin"] = plugin
                with self.assertRaisesRegex(configuration.ConfigurationError, "Azure Public DNS"):
                    self.normalize(raw)
                with mock.patch.object(configuration, "load_ssh_public_key", side_effect=AssertionError("no secret reads")):
                    with self.assertRaisesRegex(configuration.ConfigurationError, "Azure Public DNS"):
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
        for method, route in (("GET", "/responses"), ("DELETE", "/responses/{response_id}"),
                              ("POST", "/agents/invoke"), ("POST", "/neighbor")):
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


    def test_portal_certificate_name_must_differ_from_effective_https_name(self):
        raw = fixture()
        raw["certificate"].pop("certificateName", None)
        normalized = self.normalize(raw)
        self.assertEqual(normalized["certificate"]["certificateName"], "tls-public")
        self.assertEqual(normalized["portal"]["certificate"]["certificateName"], "portal-auth")

        for https_name in (None, "tls-public", "TLS-PUBLIC"):
            with self.subTest(https_name=https_name):
                collision = copy.deepcopy(raw)
                if https_name is not None:
                    collision["certificate"]["certificateName"] = https_name
                collision["portal"]["certificate"] = {"certificateName": "tls-public"}
                with self.assertRaisesRegex(configuration.ConfigurationError,
                                            r"portal\.certificate\.certificateName: must differ"):
                    self.normalize(collision)

    @mock.patch.object(configuration, "load_firewall_tls_inspection", return_value={})
    def test_azure_secret_inputs_do_not_read_or_forward_dns_credentials(self, _firewall_tls):
        with tempfile.TemporaryDirectory() as directory:
            public_key = Path(directory) / "public-key.pub"
            public_key.write_text(ssh_fixture_key() + "\n")
            raw = fixture()
            raw["management"]["sshPublicKeyFile"] = str(public_key)
            normalized = self.normalize(raw)
            with mock.patch.object(configuration, "load_dns_plugin_arguments", side_effect=AssertionError("Azure uses managed identity")):
                secrets = configuration.load_secret_inputs(normalized)
            self.assertNotIn("dnsPluginArgumentsJson", secrets)
            self.assertNotIn("pluginArgumentsFile", configuration.build_bicep_configuration(normalized)["certificate"])

    def test_azure_rejects_a_dns_credentials_path_before_reading_inputs(self):
        raw = fixture()
        raw["certificate"]["pluginArgumentsFile"] = "synthetic-unused-credentials.json"
        with mock.patch.object(configuration, "load_ssh_public_key") as ssh, \
                mock.patch.object(configuration, "load_dns_plugin_arguments") as dns:
            with self.assertRaisesRegex(configuration.ConfigurationError, "pluginArgumentsFile") as error:
                configuration.load_secret_inputs(raw)
        ssh.assert_not_called()
        dns.assert_not_called()
        self.assertNotIn("synthetic-unused-credentials.json", str(error.exception))

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


class UnknownConfigurationFieldTests(unittest.TestCase):
    """Reject unrecognized fields at every public configuration boundary."""

    def test_unknown_fields_are_rejected_by_loader_and_editor_schemas(self):
        from tests.support.schema_helpers import local_schema_validator
        locations = ((), ("services",), ("gateway", "authentication"),
                     ("apiDefinitions", 0), ("apiDefinitions", 0, "authorization"),
                     ("apiDefinitions", 0, "mcp"))
        internal_schema = local_schema_validator(json.loads((CORE / "infra/config/configuration.schema.json").read_text(encoding="utf-8")))
        for location in locations:
            with self.subTest(location=location):
                raw = pii_fixture()
                target = raw
                for part in location:
                    target = target[part]
                target["unexpectedSetting"] = "synthetic"
                with self.assertRaisesRegex(configuration.ConfigurationError, "unexpectedSetting"):
                    configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")
                errors = list(internal_schema.iter_errors(raw))
                self.assertTrue(any(error.validator == "additionalProperties"
                                    and "unexpectedSetting" in error.message for error in errors))
        from tests.support.configuration_fixtures import public_fixture
        public_schema = local_schema_validator(json.loads((CORE / "params/params.schema.json").read_text(encoding="utf-8")))
        for location in ((), ("services",), ("gateway", "authentication"),
                         ("credentialProviders", "graphDelegated")):
            with self.subTest(public_location=location):
                raw, options = public_fixture()
                target = raw
                for part in location:
                    target = target[part]
                target["unexpectedSetting"] = "synthetic"
                errors = list(public_schema.iter_errors(raw))
                self.assertTrue(any(error.validator == "additionalProperties"
                                    and "unexpectedSetting" in error.message for error in errors))
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / "options.json").write_text(json.dumps(options), encoding="utf-8")
                    source = root / "params.json"
                    source.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaises(configuration.ConfigurationError):
                        configuration.load_configuration(source)


class PolicySourceTests(unittest.TestCase):
    """公開サンプルと配備設定の型・値・組合せ・秘密ファイル境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        from deployment.helpers.apim_audit import render_audit_policy
        self.root = ET.fromstring(render_audit_policy(
            (CORE / "apps/api-gateway/policies/api-templates/rest/rest.xml").read_text(encoding="utf-8")))
        self.guard = (CORE / "apps/api-gateway/policies/fragments/request/core-api-target-validation/core-api-target-validation.cs").read_text(encoding="utf-8")

    def test_stateless_route_matching_is_delegated_without_model_revalidation(self):
        branch = self.guard.split('if (kind == "statelessResponses") {', 1)[1].split('if (kind == "hostedAgentPost")', 1)[0]
        self.assertNotIn('context.Request.Method', branch)
        self.assertNotIn('context.Operation.UrlTemplate', branch)
        self.assertNotIn('constraint["allowedModels"]', self.guard)
        self.assertNotIn('body["model"]', self.guard)
        for delegated_source in ('context.Request.Method', 'context.Operation.UrlTemplate', 'context.Operation.Id'):
            self.assertNotIn(delegated_source, self.guard)

    def test_state_reference_presence_is_not_rejected_while_other_input_conditions_remain(self):
        branch = self.guard.split('if (kind == "statelessResponses") {', 1)[1].split('if (kind == "hostedAgentPost")', 1)[0]
        self.assertNotIn('stateFields', branch)
        for forwarded_or_normalized in ('previous_response_id', 'conversation', 'conversation_id', 'prompt'):
            self.assertNotIn('body.Property("' + forwarded_or_normalized + '")', branch)
        self.assertNotIn('input.SelectTokens("$..type")', branch)
        for field in ('file_id', 'file_ids', 'vector_store_ids'):
            self.assertNotIn('input.SelectTokens("$..' + field + '")', branch)
        self.assertNotIn('body["background"]', branch)
        self.assertNotIn('body["include"]', branch)
        self.assertNotIn('allowedIncludes', branch)

    def test_store_false_is_forced_without_rewriting_sse_response(self):
        branch = next(node for node in self.root.findall("inbound/choose/when") if "gatewayStatelessResponses" in node.attrib.get("condition", ""))
        bodies = list(branch.iter("set-body"))
        self.assertEqual(len(bodies), 1)
        self.assertIn('body["store"] = false', bodies[0].text)
        self.assertNotIn('body["previous_response_id"] =', bodies[0].text)
        self.assertIn('body.Property("conversation") != null', bodies[0].text)
        self.assertIn('body["conversation"] = JValue.CreateNull()', bodies[0].text)
        self.assertNotIn('body["prompt"] =', bodies[0].text)
        self.assertIn('body.Property("background") != null', bodies[0].text)
        self.assertIn('body["background"] = false', bodies[0].text)
        self.assertNotIn('item.Remove("id")', bodies[0].text)
        self.assertNotIn('input.RemoveAt(', bodies[0].text)
        self.assertNotIn('item["id"] =', bodies[0].text)
        for forwarded_field in ('tools', 'tool_choice'):
            self.assertNotIn('body["' + forwarded_field + '"] =', bodies[0].text)
        self.assertEqual(bodies[0].text.count('context.Request.Body.As<string>'), 1)
        self.assertIsNone(self.root.find("outbound/set-body"))
        self.assertEqual(self.root.find("backend/forward-request").attrib["buffer-response"], "false")


if __name__ == "__main__":
    unittest.main()
