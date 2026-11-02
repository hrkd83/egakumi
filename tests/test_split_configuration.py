"""paramsと標準configの読込・合成とSchemaを検証する。

各JSONを基準にしたパス解決、オブジェクトの再帰合成、配列の置換、循環参照・多段参照・未知項目の拒否を確認する。
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from schema_helpers import local_schema_validator

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.configuration import (ConfigurationError, build_bicep_configuration,
                           load_configuration, load_configuration_source,
                           normalize_configuration, PARAMETER_OWNED_PATHS)


NETWORK_IP_KEYS = ("addressSpaces", "subnetPrefixes", "clientAddressPrefixes", "operatorAddressPrefixes",
                   "dnsServers", "ntpAddressPrefixes", "managementAdditionalSecurityRules")
FIREWALL_IP_KEYS = ("dnsServers", "snatPrivateRanges", "idpsPrivateRanges", "bypassTrafficSettings",
                    "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
                    "apimAdditionalApplicationRules", "managementAdditionalApplicationRules")


class SplitConfigurationTests(unittest.TestCase):
    """paramsと標準configの読込・合成とSchemaを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.options = self.root / "defaults" / "config.json"
        self.parameters = self.root / "inputs" / "test.local.json"

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def sources(self, options=None, parameters=None):
        self.write(self.options, options or {})
        self.write(self.parameters, {"configFile": "../defaults/config.json", **(parameters or {})})
        return load_configuration_source(self.parameters)

    def test_nested_parameters_merge_without_erasing_platform_options(self):
        result = self.sources(
            {"services": {"apimSkuName": "Developer", "apimCapacity": 1}},
            {"services": {"publisherName": "Example", "publisherEmail": "owner@example.com"}},
        )
        self.assertEqual(result["services"], {
            "apimSkuName": "Developer", "publisherName": "Example",
            "apimCapacity": 1, "publisherEmail": "owner@example.com",
        })
        self.assertNotIn("configFile", result)

    def test_common_key_and_personal_login_inputs_belong_to_parameters(self):
        authentication = {"subscriptionKeyHeader": "api-key", "credentialLogin": {
            "providerNames": ["graph-personal"], "idleTimeoutSeconds": 604800,
            "transactionLifetimeSeconds": 300, "loginCallsPerMinute": 10}}
        result = self.sources({"gateway": {"skuName": "WAF_v2"}},
                              {"gateway": {"authentication": authentication}})
        self.assertEqual(result["gateway"]["authentication"], authentication)
        self.assertEqual(result["gateway"]["skuName"], "WAF_v2")
        with self.assertRaisesRegex(ConfigurationError, "move.*params.gateway.authentication"):
            self.sources({"gateway": {"authentication": authentication}},
                         {"gateway": {"authentication": authentication}})

    def test_empty_parameter_array_clears_previous_selection(self):
        result = self.sources({"network": {"operatorPorts": ["22"]}}, {"apiDefinitions": []})
        self.assertEqual(result["apiDefinitions"], [])

    def test_references_resolve_from_the_enterprise_parameters_directory(self):
        # 鍵や資格情報の保存先も企業入力です。標準configへコピーしません。
        result = self.sources(
            {"certificate": {"mode": "prepared"}},
            {"management": {"sshPublicKeyFile": "key.pub"},
             "certificate": {"preparedPfxFile": "tls.pfx", "preparedPfxPasswordFile": "tls.txt", "pluginArgumentsFile": "dns.json"},
             "credentialProviders": [{"clientSecretFile": "oauth.txt"}]},
        )
        for key, filename in (("preparedPfxFile", "tls.pfx"), ("preparedPfxPasswordFile", "tls.txt"), ("pluginArgumentsFile", "dns.json")):
            self.assertEqual(result["certificate"][key], str(self.parameters.parent / filename))
        self.assertEqual(result["management"]["sshPublicKeyFile"], str(self.parameters.parent / "key.pub"))
        self.assertEqual(result["credentialProviders"][0]["clientSecretFile"], str(self.parameters.parent / "oauth.txt"))

    def test_combined_internal_configuration_remains_supported(self):
        self.write(self.parameters, {"management": {"sshPublicKeyFile": "key.pub"}})
        result = load_configuration_source(self.parameters)
        self.assertEqual(result["management"]["sshPublicKeyFile"], str(self.parameters.parent / "key.pub"))

    def test_unknown_manifest_metadata_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "unsupported field.*configFiles"):
            self.sources(parameters={"configFiles": []})

    def test_platform_settings_do_not_silently_expand_parameters_surface(self):
        for settings in ({"network": {"operatorPorts": ["22"]}},
                         {"network": {"firewall": {"sku": "Premium"}}},
                         {"services": {"apimSkuName": "Premium"}},
                         {"services": {"unexpectedSetting": "synthetic"}}):
            with self.subTest(settings=settings), self.assertRaises(ConfigurationError):
                self.sources(parameters=settings)

    def test_no_recursive_includes_or_cycles(self):
        for reference in ("../inputs/test.local.json", "other.json"):
            with self.subTest(reference=reference), self.assertRaisesRegex(ConfigurationError, "must not include another"):
                self.sources(options={"configFile": reference})

    def test_self_reference_is_rejected(self):
        self.write(self.parameters, {"configFile": "test.local.json"})
        with self.assertRaisesRegex(ConfigurationError, "itself"):
            load_configuration_source(self.parameters)

    def test_invalid_reference_is_rejected_without_network_or_value_echo(self):
        for reference in (None, [], "", "https://example.com/config.json", "settings.txt", "secret\x00.json"):
            self.write(self.parameters, {"configFile": reference})
            with self.subTest(reference=reference), self.assertRaises(ConfigurationError):
                load_configuration_source(self.parameters)

    def test_missing_file_and_invalid_json_are_actionable_errors(self):
        for path in (self.parameters, self.options):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{invalid-private-content", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "cannot read a valid JSON") as caught:
                load_configuration_source(path)
            self.assertNotIn("invalid-private-content", str(caught.exception))

    def test_unrecognized_standard_config_fields_fail_before_merge(self):
        with self.assertRaisesRegex(ConfigurationError, "unsupported field.*unknown"):
            self.sources(options={"unknown": "value"})

    def test_public_examples_load_and_normalize_both_modes(self):
        for name in ("test", "production"):
            raw = load_configuration_source(CORE / f"params/{name}.example.json")
            raw["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
            raw["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
            raw["entraTenantId"] = raw["azureTenantId"]
            self.write(self.parameters, raw)
            with self.subTest(name=name):
                loaded = load_configuration(self.parameters)
                self.assertEqual(loaded["services"]["projectDisplayName"], "core-platform")
                self.assertTrue(loaded["services"]["grantApimFoundryModelsAccess"])
                self.assertEqual(loaded["management"]["sshPublicKeyFile"], str((CORE / "secrets/management-ssh.pub").resolve()))
                arm = build_bicep_configuration(loaded)
                self.assertNotIn("configFile", arm)
                self.assertNotIn("$schema", arm)
                self.assertNotIn("sshPublicKeyFile", arm["management"])
                self.assertNotIn("pluginArgumentsFile", arm["certificate"])

    def test_project_default_uses_core_platform(self):
        raw = load_configuration_source(CORE / "params/test.example.json")
        raw["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
        raw["azureTenantId"] = "99999999-9999-4999-8999-999999999999"
        raw["entraTenantId"] = raw["azureTenantId"]
        raw["services"].pop("projectDisplayName")
        raw["services"].pop("projectDescription")
        normalized = normalize_configuration(raw)
        self.assertEqual(normalized["services"]["projectDisplayName"], "core-platform")
        self.assertIn("Core platform", normalized["services"]["projectDescription"])

    def test_ip_parameters_merge_with_operational_config_and_partial_loader_inputs_stay_supported(self):
        # loaderは小さい部分入力にも使う。完全設定の必須CIDRはschemaとnormalizeで検査します。
        result = self.sources({"network": {"operatorPorts": ["22"], "firewall": {"sku": "Premium", "idpsMode": "Deny"}}},
                              {"network": {"dnsServers": [], "firewall": {"dnsServers": ["10.1.1.4"],
                                "apimAdditionalNetworkRules": [{"name": "approved", "destinationAddresses": ["192.0.2.1"]}]}},
                               "monitoring": {"auditAllowedIpAddresses": ["198.51.100.10/32"]}})
        self.assertEqual(result["network"]["operatorPorts"], ["22"])
        self.assertEqual(result["network"]["firewall"]["sku"], "Premium")
        self.assertEqual(result["network"]["firewall"]["dnsServers"], ["10.1.1.4"])
        self.assertEqual(len(result["network"]["firewall"]["apimAdditionalNetworkRules"]), 1)
        self.assertEqual(result["monitoring"]["auditAllowedIpAddresses"], ["198.51.100.10/32"])
        self.assertEqual(self.sources(parameters={"network": {}})["network"], {})

    def test_ip_fields_in_config_are_rejected_even_when_empty_or_overridden_by_params(self):
        # 誤った環境のIPを隠して継承しない。値そのものを例外へ表示しません。
        for keys, nested in ((NETWORK_IP_KEYS, False), (FIREWALL_IP_KEYS, True)):
            for key in keys:
                for value in ([], ["PRIVATE-INPUT-SENTINEL"]):
                    network = {"firewall": {key: value}} if nested else {key: value}
                    with self.subTest(key=key, nested=nested, value=value):
                        with self.assertRaisesRegex(ConfigurationError, "move.*params.network") as caught:
                            self.sources({"network": network}, {"network": network})
                        self.assertNotIn("PRIVATE-INPUT-SENTINEL", str(caught.exception))
        with self.assertRaisesRegex(ConfigurationError, "move.*params.monitoring.auditAllowedIpAddresses"):
            self.sources({"monitoring": {"auditAllowedIpAddresses": []}}, {"monitoring": {"auditAllowedIpAddresses": []}})

    def test_complete_split_configuration_requires_environment_cidrs(self):
        options = json.loads((CORE / "config/test.json").read_text())
        parameters = json.loads((CORE / "params/test.example.json").read_text())
        for key in ("azureTenantId", "entraTenantId", "subscriptionId"): parameters[key] = "11111111-1111-4111-8111-111111111111"
        parameters.pop("configFile")
        for key in ("addressSpaces", "subnetPrefixes"):
            missing = copy.deepcopy(parameters); del missing["network"][key]
            with self.subTest(missing=key):
                merged = self.sources(options, missing)
                with self.assertRaisesRegex(ConfigurationError, "network." + key): normalize_configuration(merged)

    def test_combined_internal_ip_configuration_is_not_subject_to_split_ownership_rules(self):
        internal = {"network": {"addressSpaces": ["10.0.0.0/16"], "firewall": {"dnsServers": ["10.0.0.4"]}},
                    "monitoring": {"auditAllowedIpAddresses": ["198.51.100.1/32"]}}
        self.write(self.parameters, internal)
        self.assertEqual(load_configuration_source(self.parameters), internal)

    def test_explicit_empty_ip_selections_survive_merge_and_normalization(self):
        options = json.loads((CORE / "config/test.json").read_text())
        parameters = json.loads((CORE / "params/test.example.json").read_text())
        parameters.pop("configFile")
        for key in ("azureTenantId", "entraTenantId", "subscriptionId"): parameters[key] = "11111111-1111-4111-8111-111111111111"
        parameters["network"]["clientAddressPrefixes"] = []
        result = normalize_configuration(self.sources(options, parameters))
        for key in ("clientAddressPrefixes", "operatorAddressPrefixes", "dnsServers", "managementAdditionalSecurityRules"):
            self.assertEqual(result["network"][key], [])
        self.assertEqual(result["monitoring"]["auditAllowedIpAddresses"], [])

    def test_public_sample_ip_values_move_unchanged_and_operational_defaults_stay_in_config(self):
        expected = {"addressSpaces": ["10.40.0.0/24", "10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"],
                    "subnetPrefixes": {"agw": "10.40.0.0/24", "pep": "10.40.1.0/24", "firewall": "10.40.2.0/26",
                                       "apim": "10.40.2.64/26", "management": "10.40.2.128/26", "aca": "10.40.2.192/26"},
                    "clientAddressPrefixes": ["0.0.0.0/0"], "operatorAddressPrefixes": [], "dnsServers": [],
                    "ntpAddressPrefixes": ["Internet"], "managementAdditionalSecurityRules": [],
                    "firewall": {key: (["IANAPrivateRanges"] if key == "snatPrivateRanges" else []) for key in FIREWALL_IP_KEYS}}
        for name in ("test", "production"):
            parameters = json.loads((CORE / "params" / (name + ".example.json")).read_text())
            options = json.loads((CORE / "config" / (name + ".json")).read_text())
            with self.subTest(sample=name):
                self.assertEqual({key: parameters["network"][key] for key in expected if key != "firewall"}, {key: value for key, value in expected.items() if key != "firewall"})
                self.assertEqual({key: parameters["network"]["firewall"][key] for key in FIREWALL_IP_KEYS}, expected["firewall"])
                self.assertEqual(parameters["monitoring"]["auditAllowedIpAddresses"], [])
                self.assertFalse(set(NETWORK_IP_KEYS) & set(options["network"]))
                self.assertFalse(set(FIREWALL_IP_KEYS) & set(options["network"]["firewall"]))
                self.assertNotIn("auditAllowedIpAddresses", options["monitoring"])
                self.assertEqual(options["network"]["firewall"]["sku"], "Standard" if name == "test" else "Premium")
                self.assertEqual(options["network"]["operatorPorts"], ["22"])
                self.assertIn("rateLimits", options["gateway"])

    def test_every_environment_field_is_rejected_in_config_even_if_parameters_override_it(self):
        for path in PARAMETER_OWNED_PATHS:
            for value in ([], "ENTERPRISE-VALUE-SENTINEL"):
                options, parameters = {}, {}
                for target in (options, parameters):
                    for key in path[:-1]:
                        target = target.setdefault(key, {})
                    target[path[-1]] = copy.deepcopy(value)
                if path == ("authorization",):
                    parameters["authorization"] = {}
                if path == ("gateway", "authentication"):
                    parameters["gateway"]["authentication"] = {"subscriptionKeyHeader": "api-key"}
                with self.subTest(path=".".join(path), empty=value == []):
                    with self.assertRaisesRegex(ConfigurationError, r"move.*params\.") as caught:
                        self.sources(options, parameters)
                    self.assertNotIn("ENTERPRISE-VALUE-SENTINEL", str(caught.exception))

    def test_nested_params_expose_only_environment_fields_and_not_platform_options(self):
        for settings in ({"gateway": {"grpc": {"enabled": True}}},
                         {"gateway": {"rateLimits": {"user": {"calls": 1}}}},
                         {"gateway": {"rateLimits": {"application": {"calls": 1}}}},
                         {"certificate": {"renewBeforeDays": 10}},
                         {"monitoring": {"enableLogAlerts": True}}):
            with self.subTest(settings=settings), self.assertRaises(ConfigurationError):
                self.sources(parameters=settings)

    def test_environment_regions_do_not_silently_fall_back_to_another_region(self):
        raw = load_configuration_source(CORE / "params/test.example.json")
        for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
            raw[key] = "11111111-1111-4111-8111-111111111111"
        for key in ("location", "foundryLocation", "apiCenterLocation"):
            missing = copy.deepcopy(raw)
            del missing[key]
            with self.subTest(missing=key), self.assertRaisesRegex(ConfigurationError, key):
                normalize_configuration(missing)

    def test_canonical_config_has_no_local_or_example_variants(self):
        # 実行時の派生設定は.state、入力はparamsへ置き、config正本は2個だけです。
        self.assertEqual({p.name for p in (CORE / "config").glob("*.json")}, {"test.json", "production.json"})


class SplitSchemaTests(unittest.TestCase):
    """paramsと標準configの読込・合成とSchemaを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""

    def test_schema_editor_defaults_match_the_files_that_use_them(self):
        # エディターが挿入するschema既定値も、JSON利用元から新配置へ解決します。
        for filename, directory in (("params", "params"), ("options", "config")):
            schema = json.loads((CORE / "schemas" / (filename + ".schema.json")).read_text())
            reference = schema["properties"]["$schema"]["default"]
            self.assertEqual((CORE / directory / reference).resolve(), CORE / "schemas" / (filename + ".schema.json"))
        combined = json.loads((CORE / "schemas/configuration.schema.json").read_text())
        # 合成出力先はCLIで変更できるため、不正確な固定相対パスを勧めません。
        self.assertNotIn("default", combined["properties"]["$schema"])

    def test_schemas_do_not_accept_apim_resource_name_overrides(self):
        """サービス設定は既知の公開フィールドだけを受け付ける。"""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        for filename in ("options.schema.json", "configuration.schema.json"):
            schema = json.loads((CORE / "schemas" / filename).read_text())
            service = schema["properties"]["services"]
            self.assertFalse(service.get("additionalProperties", True))
            validator = jsonschema.Draft202012Validator(service)
            self.assertFalse(validator.is_valid({"unexpectedSetting": "synthetic"}))

    def test_samples_and_merged_configs_match_their_separate_schemas(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        for name in ("test", "production"):
            for kind in ("params", "config"):
                path = CORE / (f"params/{name}.example.json" if kind == "params" else f"config/{name}.json")
                value = json.loads(path.read_text())
                if kind == "params":
                    for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
                        value[key] = "11111111-1111-4111-8111-111111111111"
                schema = json.loads((path.parent / value["$schema"]).read_text())
                with self.subTest(name=name, kind=kind):
                    jsonschema.Draft202012Validator.check_schema(schema)
                    jsonschema.validate(value, schema)
            merged = load_configuration_source(CORE / f"params/{name}.example.json")
            for key in ("subscriptionId", "azureTenantId", "entraTenantId"):
                merged[key] = "11111111-1111-4111-8111-111111111111"
            schema = json.loads((CORE / "schemas/configuration.schema.json").read_text())
            with self.subTest(name=name, kind="merged"):
                local_schema_validator(schema).validate(merged)

    def test_params_network_cidrs_are_required_and_firewall_does_not_require_config_sku(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema is available in the integration venv")
        schema = json.loads((CORE / "schemas/params.schema.json").read_text())
        sample = json.loads((CORE / "params/test.example.json").read_text())
        for key in ("azureTenantId", "entraTenantId", "subscriptionId"): sample[key] = "11111111-1111-4111-8111-111111111111"
        for key in ("network", "addressSpaces", "subnetPrefixes"):
            invalid = copy.deepcopy(sample)
            if key == "network": del invalid[key]
            else: del invalid["network"][key]
            with self.subTest(missing=key), self.assertRaises(jsonschema.ValidationError): jsonschema.validate(invalid, schema)
        firewall = schema["properties"]["network"]["properties"]["firewall"]
        jsonschema.validate({"bypassTrafficSettings": [{"name": "approved"}], "idpsPrivateRanges": ["10.0.0.0/8"]}, firewall)
        self.assertNotIn("allOf", firewall)
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({"sku": "Premium"}, firewall)

    def test_options_schema_rejects_every_enterprise_owned_field_including_empty_values(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        schema = json.loads((CORE / "schemas/options.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        for path in PARAMETER_OWNED_PATHS:
            for empty in ([], {}, ""):
                document = {}
                target = document
                for key in path[:-1]:
                    target = target.setdefault(key, {})
                target[path[-1]] = empty
                with self.subTest(path=".".join(path), empty=empty):
                    self.assertFalse(validator.is_valid(document))

    def test_parameters_schema_requires_explicit_deployment_regions(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        schema = json.loads((CORE / "schemas/params.schema.json").read_text())
        sample = json.loads((CORE / "params/test.example.json").read_text())
        for key in ("azureTenantId", "entraTenantId", "subscriptionId"):
            sample[key] = "11111111-1111-4111-8111-111111111111"
        for region in ("location", "foundryLocation", "apiCenterLocation"):
            missing = copy.deepcopy(sample)
            del missing[region]
            with self.subTest(region=region), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(missing, schema)

    def test_options_schema_rejects_ip_fields_and_merged_schema_keeps_sku_conditions(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema is available in the integration venv")
        options = json.loads((CORE / "schemas/options.schema.json").read_text())
        for key in NETWORK_IP_KEYS:
            with self.subTest(network=key), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({"network": {key: []}}, options)
        for key in FIREWALL_IP_KEYS:
            with self.subTest(firewall=key), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({"network": {"firewall": {key: []}}}, options)
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({"monitoring": {"auditAllowedIpAddresses": []}}, options)
        internal = json.loads((CORE / "schemas/configuration.schema.json").read_text())
        firewall = internal["properties"]["network"]["properties"]["firewall"]
        for key, nonempty in (("bypassTrafficSettings", [{}]), ("idpsPrivateRanges", ["10.0.0.0/8"]), ("signatureOverrides", [{}])):
            with self.subTest(key=key):
                jsonschema.validate({"sku": "Premium", "idpsMode": "Alert", key: nonempty}, firewall)
                with self.assertRaises(jsonschema.ValidationError): jsonschema.validate({"sku": "Standard", "idpsMode": "Off", key: nonempty}, firewall)


if __name__ == "__main__":
    unittest.main()
