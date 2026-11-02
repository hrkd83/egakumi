"""通常モデルとazdの対象外のモデルの配備境界を検証する。"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from configuration_fixtures import CORE, configuration, fixture
from lifecycle_fixtures import lifecycle


class ModelDeploymentScopeTests(unittest.TestCase):
    def normalize(self, value):
        return configuration.normalize_configuration(value, base_directory=CORE / "config")

    def model(self, **overrides):
        return {"name": "claude-fixture", "modelName": "claude-fixture", "modelFormat": "Anthropic",
                "modelVersion": "1", "skuName": "GlobalStandard", "capacity": 1,
                "versionUpgradeOption": "NoAutoUpgrade", **overrides}


    def test_anthropic_deployments_are_rejected_independently_of_provider_casing(self):
        for provider in ("Anthropic", "anthropic", "ANTHROPIC", "AnThRoPiC"):
            with self.subTest(provider=provider):
                raw = fixture()
                raw["services"]["modelDeployments"] = [self.model(
                    name="provider-fixture", modelName="provider-fixture", modelFormat=provider)]
                with self.assertRaisesRegex(configuration.ConfigurationError, "outside azd provisioning"):
                    self.normalize(raw)

    def test_out_of_scope_models_cannot_bypass_validation_with_another_provider(self):
        for field in ("name", "modelName"):
            for provider in ("OpenAI", "Cohere", "Microsoft"):
                with self.subTest(field=field, provider=provider):
                    raw = fixture()
                    model = self.model(name="model-fixture", modelName="model-fixture", modelFormat=provider)
                    model[field] = "ClAuDe-fixture"
                    raw["services"]["modelDeployments"] = [model]
                    with self.assertRaisesRegex(configuration.ConfigurationError, "outside azd provisioning"):
                        self.normalize(raw)

    def test_invalid_model_stops_both_hooks_before_local_preparation_or_azure_access(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            raw = fixture()
            raw["services"]["modelDeployments"] = [self.model()]
            source.write_text(json.dumps(raw), encoding="utf-8")
            for command in ("preup", "preprovision"):
                with self.subTest(command=command), \
                     mock.patch.object(lifecycle, "local_preflight") as local, \
                     mock.patch.object(lifecycle, "run") as cli, \
                     mock.patch.object(lifecycle, "ArmTransport") as arm, \
                     contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(lifecycle.main([command, "--config", str(source)]), 1)
                self.assertIn("outside azd provisioning", error.getvalue())
                local.assert_not_called()
                cli.assert_not_called()
                arm.assert_not_called()


    def test_model_schemas_match_supported_provisioning_scope(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is available in the integration venv")
        for name in ("configuration", "options"):
            schema = json.loads((CORE / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema["$defs"]["modelDeployment"])
            self.assertTrue(validator.is_valid(self.model(
                name="model-fixture", modelName="model-fixture", modelFormat="OpenAI")))
            for provider in ("Anthropic", "anthropic", "ANTHROPIC"):
                with self.subTest(schema=name, provider=provider):
                    self.assertFalse(validator.is_valid(self.model(
                        name="model-fixture", modelName="model-fixture", modelFormat=provider)))
            for field in ("name", "modelName"):
                with self.subTest(schema=name, field=field):
                    model = self.model(name="model-fixture", modelName="model-fixture", modelFormat="OpenAI")
                    model[field] = "ClAuDe-fixture"
                    self.assertFalse(validator.is_valid(model))

    def test_parameter_editor_metadata_is_preserved_locally_and_never_sent_to_arm(self):
        raw = fixture()
        raw["$schema"] = "./configuration.schema.json"
        raw["$comment"] = "入力候補はschemaに記載。"
        normalized = self.normalize(raw)
        self.assertEqual(normalized["$schema"], raw["$schema"])
        self.assertEqual(normalized["$comment"], raw["$comment"])
        public = configuration.build_bicep_configuration(normalized)
        self.assertNotIn("$schema", public)
        self.assertNotIn("$comment", public)


if __name__ == "__main__":
    unittest.main()
