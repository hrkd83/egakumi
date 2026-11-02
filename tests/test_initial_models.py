"""Validate the shipped deployment catalog without duplicating its model versions."""
import copy
import json
import unittest

from configuration_fixtures import CORE, configuration, fixture


PUBLIC_FILES = ("config/test.json", "config/production.json")


class InitialModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {
            name: json.loads((CORE / name).read_text(encoding="utf-8"))["services"]["modelDeployments"]
            for name in PUBLIC_FILES
        }

    def test_environments_share_unique_deployments(self):
        expected = self.models[PUBLIC_FILES[0]]
        self.assertTrue(expected)
        for name, models in self.models.items():
            with self.subTest(file=name):
                self.assertEqual(models, expected)
                self.assertEqual(len({model["name"].casefold() for model in models}), len(models))

    def test_models_have_explicit_versions_quotas_and_positive_capacity(self):
        for model in self.models[PUBLIC_FILES[0]]:
            with self.subTest(model=model["name"]):
                self.assertEqual(model["versionUpgradeOption"], "NoAutoUpgrade")
                self.assertTrue(model["modelVersion"].strip())
                self.assertTrue(model["quotaUsageName"].strip())
                self.assertIs(type(model["capacity"]), int)
                self.assertGreater(model["capacity"], 0)

    def test_each_environment_catalog_passes_public_configuration_validation(self):
        for name, models in self.models.items():
            with self.subTest(file=name):
                raw = fixture()
                raw["services"]["modelDeployments"] = copy.deepcopy(models)
                actual = configuration.normalize_configuration(raw, base_directory=CORE / "config")
                self.assertEqual(actual["services"]["modelDeployments"], models)


if __name__ == "__main__":
    unittest.main()
