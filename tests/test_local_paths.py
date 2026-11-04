"""環境別運用領域の分離、上書き防止、権限とbridge準備の失敗時回収を検証する。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers.azd_parameters import complete_arm_parameters
from deployment.helpers.paths import LocalPaths, configuration_path, local_paths, private_output, private_parent
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import fixture
import prepare_local_environment


class LocalPathsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.environment = mock.patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_environments_have_independent_state_secrets_and_generated_inputs(self):
        for name in ("alpha", "beta"):
            paths = LocalPaths(self.root, name)
            paths.ensure()
            target = private_parent(paths.state / "latest.json", self.root)
            target.write_text(name)
            self.assertEqual(target.read_text(), name)
            for folder in (self.root / ".local", paths.root, paths.secrets, paths.state, paths.parameters.parent):
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertNotEqual(LocalPaths(self.root, "alpha").parameters, LocalPaths(self.root, "beta").parameters)
        with self.assertRaises(ValueError):
            LocalPaths(self.root, "alpha").checked(LocalPaths(self.root, "beta").state / "latest.json")

    def test_private_files_cannot_be_created_in_public_or_legacy_directories(self):
        for relative in ("params/test.local.json", "secrets/key", ".state/latest.json", "infra/main.parameters.json", "output/report.json"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                private_parent(self.root / relative, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_runtime_roots_and_nested_paths_cannot_be_symlinks(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / ".local").symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            LocalPaths(self.root, "test")
        (self.root / ".local").unlink()
        paths = LocalPaths(self.root, "test")
        paths.ensure()
        (paths.state / "linked").symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            paths.checked(paths.state / "linked" / "file.json")
        self.assertEqual(list(other.iterdir()), [])

    def test_explicit_external_outputs_are_allowed_but_external_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory).resolve()
            target = external / "runtime-output.json"
            self.assertEqual(private_output(target, self.root), target)
            (external / "linked").symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                private_output(external / "linked/.local/alpha/runtime-output.json", self.root)

    @unittest.skipUnless(sys.platform == "darwin" and Path("/tmp").is_symlink()
                         and Path("/tmp").resolve() == Path("/private/tmp"),
                         "Requires the macOS system temporary-directory alias")
    def test_system_alias_is_resolved_before_classifying_the_checkout_boundary(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve() / "checkout"
            root.mkdir()
            alias = Path("/tmp") / root.relative_to("/private/tmp")
            for relative in ("config/runtime-output.json", "infra/main.parameters.json"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    private_output(alias / relative, root)
            target = root / ".local/alpha/runtime-output.json"
            self.assertEqual(private_output(alias / ".local/alpha/runtime-output.json", root), target)
            self.assertEqual(list(root.iterdir()), [])

    def test_environment_names_cannot_escape_the_private_root(self):
        for value in ("", "../test", "test/other", "Test", "-test", "a" * 13):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LocalPaths(self.root, value)

    def test_selection_mismatch_fails_without_writing_any_state(self):
        config = {"environmentName": "test", "subscriptionId": "11111111-1111-1111-1111-111111111111", "azureTenantId": "22222222-2222-2222-2222-222222222222"}
        paths = local_paths(config, self.root)
        for variable, value in (("AZURE_ENV_NAME", "other"), ("AZURE_SUBSCRIPTION_ID", "other"),
                                ("AZURE_TENANT_ID", "other"),
                                ("EGAKUMI_ARM_PARAMETERS_FILE", ".local/other/generated/main.parameters.json")):
            with self.subTest(variable=variable), mock.patch.dict(os.environ, {variable: value}, clear=True), self.assertRaises(ValueError):
                paths.check_selection(config)
        self.assertFalse((self.root / ".local").exists())

    def test_configuration_selection_has_no_implicit_test_environment(self):
        with self.assertRaises(ValueError):
            configuration_path(self.root)
        with mock.patch.dict(os.environ, {"AZURE_ENV_NAME": "alpha"}):
            self.assertEqual(configuration_path(self.root), LocalPaths(self.root, "alpha").params)
        path = private_parent(self.root / ".azure/config.json", self.root)
        path.write_text(json.dumps({"defaultEnvironment": "beta"}))
        self.assertEqual(configuration_path(self.root), LocalPaths(self.root, "beta").params)


class LocalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        environment = mock.patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "params", self.root / "params")
        shutil.copytree(source / "config", self.root / "config")
        shutil.copytree(source / "schemas", self.root / "schemas")

    def test_prepare_rebases_references_and_preserves_subsequent_user_edits(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = prepare_local_environment.prepare("alpha", "test", self.root)
        self.assertEqual(result["status"], "created")
        paths = LocalPaths(self.root, "alpha")
        raw = json.loads(paths.params.read_text())
        self.assertEqual(raw["configFile"], "../../config/test.json")
        self.assertEqual(raw["environmentName"], "alpha")
        self.assertEqual(raw["management"]["sshPublicKeyFile"], "./secrets/management-ssh.pub")
        self.assertEqual(stat.S_IMODE(paths.params.stat().st_mode), 0o600)
        raw["productName"] = "synthetic-product"
        paths.params.write_text(json.dumps(raw))
        content = paths.params.read_bytes()
        self.assertEqual(prepare_local_environment.prepare("alpha", "test", self.root)["status"], "existing-input-preserved")
        self.assertEqual(paths.params.read_bytes(), content)
        for relative, value in before.items():
            self.assertEqual((self.root / relative).read_bytes(), value)
        self.assertEqual(list(paths.secrets.iterdir()), [])

    def test_existing_environment_profile_is_never_overwritten(self):
        prepare_local_environment.prepare("alpha", "production", self.root)
        paths = LocalPaths(self.root, "alpha")
        before = paths.params.read_bytes()
        with self.assertRaises(ValueError):
            prepare_local_environment.prepare("alpha", "test", self.root)
        self.assertEqual(paths.params.read_bytes(), before)

    def test_parameter_bridge_check_cleans_generated_input_on_success_and_failure(self):
        config = normalize_configuration(fixture(), base_directory=self.root / "config")
        target = local_paths(config, self.root).parameters
        expected = complete_arm_parameters(lifecycle.parameters(config, "core"))
        with mock.patch.object(lifecycle, "ROOT", self.root), mock.patch.dict(os.environ, {"BICEP_CLI": sys.executable}):
            with mock.patch.object(lifecycle, "run", return_value={"parametersJson": json.dumps(expected)}) as run:
                lifecycle.check_parameter_bridge(config)
                self.assertIn("build-params", run.call_args.args[0])
                self.assertFalse(target.exists())
            with mock.patch.object(lifecycle, "run", side_effect=lifecycle.DeploymentError("compile failed")):
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.check_parameter_bridge(config)
                self.assertFalse(target.exists())
        self.assertFalse((self.root / "infra/main.parameters.json").exists())


if __name__ == "__main__":
    unittest.main()
