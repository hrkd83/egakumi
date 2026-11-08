"""APIMの保存済み識別子、決定的命名と安全な初回生成を合成値で検証する。"""
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers.apim_naming import apim_instance_id, apim_name, generate_apim_instance_id


class ApimNamingTests(unittest.TestCase):
    def config(self, identifier="k7m2q9"):
        return {"environmentName": "test", "productName": "sample", "services": {"apimInstanceId": identifier}}

    def test_saved_identifier_and_stem_are_used_without_regeneration(self):
        config = self.config()
        with mock.patch("deployment.helpers.apim_naming.secrets.choice") as choice:
            self.assertEqual(apim_name(config), "apim-test-sample-k7m2q9")
            self.assertEqual(apim_name(config), "apim-test-sample-k7m2q9")
            self.assertEqual(apim_name({**config, "resourceStem": "prod-sample"}), "apim-prod-sample-k7m2q9")
        choice.assert_not_called()

    def test_missing_or_invalid_identifier_is_rejected_without_echoing_values(self):
        for value in (None, "", "short", "longer7", "ABC123", "ab-c12", "123456\n", 123456, True):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                apim_instance_id(self.config(value))
            if isinstance(value, str) and value:
                self.assertNotIn(value, str(caught.exception))
        for config in ({}, {"services": {}}, {"services": []}, None):
            with self.subTest(config=config), self.assertRaises(ValueError):
                apim_name(config)

    def test_names_obey_stem_boundary_and_missing_inputs_fail(self):
        self.assertEqual(apim_name({"resourceStem": "a" * 17, "services": {"apimInstanceId": "123456"}}),
                         "apim-" + "a" * 17 + "-123456")
        for stem in ("a" * 18, "Upper", "bad--stem", "bad\nstem", 42, ""):
            with self.subTest(stem=stem), self.assertRaises(ValueError):
                apim_name({"resourceStem": stem, "services": {"apimInstanceId": "k7m2q9"}})
        with self.assertRaises(ValueError):
            apim_name({"services": {"apimInstanceId": "k7m2q9"}})

    def test_secure_generation_excludes_previously_used_values(self):
        with mock.patch("deployment.helpers.apim_naming.secrets.choice", side_effect=list("aaaaaak7m2q9")) as choice:
            self.assertEqual(generate_apim_instance_id(["aaaaaa"]), "k7m2q9")
        self.assertEqual(choice.call_count, 12)
        for _ in range(3):
            self.assertRegex(generate_apim_instance_id(), r"^[a-z0-9]{6}$")

    def test_invalid_history_and_exhausted_retries_fail_safely(self):
        for used in (["INVALID"], [None], [[]]):
            with self.subTest(used=used), self.assertRaises(ValueError):
                generate_apim_instance_id(used)
        with mock.patch("deployment.helpers.apim_naming.secrets.choice", return_value="a"), self.assertRaises(ValueError):
            generate_apim_instance_id(["aaaaaa"])


if __name__ == "__main__":
    unittest.main()
