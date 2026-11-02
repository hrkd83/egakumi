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

    def test_retired_text_families_are_absent_from_deployments_and_api_contracts(self):
        definition = json.loads((CORE / "apps/gateway/apim/apis/openai-inference-v1.json").read_text(encoding="utf-8"))
        capabilities = json.loads((CORE / "apps/gateway/apim/model-capabilities.json").read_text(encoding="utf-8"))
        api_models = set()
        for operation in definition["authorization"]["operationConstraints"].values():
            for constraint in (operation, *operation.get("profiles", [])):
                api_models.update(constraint["allowedModels"])
        capability_models = {
            entry["modelId"] for entry in capabilities["entries"]
            if definition["name"] in entry["apiIds"]
        }
        self.assertEqual(capability_models, api_models)
        for name, models in self.models.items():
            with self.subTest(file=name):
                deployment_names = {model["name"] for model in models}
                self.assertTrue(api_models <= deployment_names)
                for model_id in deployment_names | api_models | capability_models:
                    self.assertFalse(model_id.startswith(("gpt-5.4", "gpt-5.5")), model_id)

    def test_each_environment_catalog_passes_public_configuration_validation(self):
        for name, models in self.models.items():
            with self.subTest(file=name):
                raw = fixture()
                raw["services"]["modelDeployments"] = copy.deepcopy(models)
                actual = configuration.normalize_configuration(raw, base_directory=CORE / "config")
                self.assertEqual(actual["services"]["modelDeployments"], models)


if __name__ == "__main__":
    unittest.main()
