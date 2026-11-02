"""APIキー所有グループの環境別入力が、基盤・秘密管理の境界を守ることを確認する。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from configuration_fixtures import configuration, fixture, CORE


class PortalApiKeyConfigurationTests(unittest.TestCase):
    def value(self):
        return {"groups": [{
            "groupId": "11111111-AAAA-4111-8111-111111111111",
            "products": [{"productId": "model-users"}],
        }]}

    def test_unconfigured_groups_do_not_block_foundation(self):
        self.assertEqual(configuration.normalize_configuration(fixture())["portalApiKeys"], {"groups": [], "groupNamePattern": "", "verificationSubscriptions": []})

    def test_ownership_is_environment_specific_and_original_is_unchanged(self):
        raw = fixture()
        raw["portalApiKeys"] = self.value()
        original = copy.deepcopy(raw)
        result = configuration.normalize_configuration(raw)["portalApiKeys"]
        self.assertEqual(result["groups"][0]["groupId"], "11111111-aaaa-4111-8111-111111111111")
        self.assertEqual(result["groups"][0]["products"][0]["maxSubscriptions"], 5)
        self.assertEqual(raw, original)

    def test_duplicate_groups_products_and_secret_fields_are_rejected(self):
        cases = []
        value = self.value()
        value["groups"].append(copy.deepcopy(value["groups"][0]))
        cases.append(value)
        value = self.value()
        value["groups"][0]["products"] *= 2
        cases.append(value)
        value = self.value()
        value["groups"][0]["primaryKey"] = "not-allowed"
        cases.append(value)
        value = self.value()
        value["groups"][0]["products"][0]["maxSubscriptions"] = 0
        cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration({**fixture(), "portalApiKeys": value})

    def test_standard_config_cannot_silently_supply_another_companys_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "options.json").write_text(json.dumps({"portalApiKeys": self.value()}), encoding="utf-8")
            path = root / "params.json"
            path.write_text(json.dumps({"configFile": "options.json", "portalApiKeys": {"groups": []}}), encoding="utf-8")
            with self.assertRaisesRegex(configuration.ConfigurationError, "config.portalApiKeys"):
                configuration.load_configuration_source(path)

    def test_editor_schema_exposes_groups_only_as_environment_input(self):
        params = json.loads((CORE / "schemas/params.schema.json").read_text(encoding="utf-8"))
        options = json.loads((CORE / "schemas/options.schema.json").read_text(encoding="utf-8"))
        self.assertIn("portalApiKeys", params["properties"])
        self.assertNotIn("portalApiKeys", options["properties"])

    def test_display_name_is_not_an_environment_input(self):
        value = self.value()
        value["groups"][0]["displayName"] = "ORG_StaleName"
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration({**fixture(), "portalApiKeys": value})
        params = json.loads((CORE / "schemas/params.schema.json").read_text(encoding="utf-8"))
        group = params["$defs"]["portalApiKeys"]["properties"]["groups"]["items"]
        self.assertNotIn("displayName", group["properties"])

    def test_both_environment_templates_include_interactive_portal_group_settings(self):
        for name in ("test", "production"):
            raw = json.loads((CORE / f"params/{name}.example.json").read_text(encoding="utf-8"))
            with self.subTest(environment=name):
                self.assertEqual(raw["portalApiKeys"], {"groupNamePattern": "", "groups": [], "verificationSubscriptions": []})
                self.assertIn("platformGroupId", raw["$comment"])

    def test_platform_group_is_explicit_and_belongs_to_configured_verification_owners(self):
        value = self.value()
        value["platformGroupId"] = value["groups"][0]["groupId"]
        normalized = configuration.normalize_configuration({**fixture(), "portalApiKeys": value})["portalApiKeys"]
        self.assertEqual(normalized["platformGroupId"], "11111111-aaaa-4111-8111-111111111111")
        self.assertEqual(normalized["groupNamePattern"], "")
        value["platformGroupId"] = "22222222-2222-4222-8222-222222222222"
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration({**fixture(), "portalApiKeys": value})

    def test_name_patterns_are_environment_specific_without_a_company_default(self):
        for pattern in ("", "^PJT_", "^(ORG_|DEPT_)", "^組織_[0-9]+$", r"^\p{L}+$"):
            with self.subTest(pattern=pattern):
                result = configuration.normalize_configuration({**fixture(), "portalApiKeys": {"groups": [], "groupNamePattern": pattern}})
                self.assertEqual(result["portalApiKeys"]["groupNamePattern"], pattern)
        for pattern in (None, 3, "x" * 513, "^ORG_\n"):
            with self.subTest(pattern=pattern), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration({**fixture(), "portalApiKeys": {"groups": [], "groupNamePattern": pattern}})
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration({**fixture(), "portalApiKeys": {"groups": [], "groupPrefix": "ORG_"}})

    def test_verification_subscriptions_require_explicit_product_scopes_and_never_master(self):
        value = self.value()
        value['platformGroupId'] = value['groups'][0]['groupId']
        entry = {'subscriptionId': 'legacy-key', 'productId': 'model-users', 'displayName': 'Platform test'}
        value['verificationSubscriptions'] = [entry]
        result = configuration.normalize_configuration({**fixture(), 'portalApiKeys': value})
        self.assertEqual(result['portalApiKeys']['verificationSubscriptions'], [entry])
        for mutate in (
            lambda data: data[0].update(subscriptionId='master'),
            lambda data: data[0].update(subscriptionId='MaStEr'),
            lambda data: data[0].update(subscriptionId='../other'),
            lambda data: data[0].update(productId='unapproved-product'),
            lambda data: data[0].update(primaryKey='forbidden'),
            lambda data: data.append(copy.deepcopy(data[0])),
        ):
            candidate = copy.deepcopy(value)
            mutate(candidate['verificationSubscriptions'])
            with self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration({**fixture(), 'portalApiKeys': candidate})
