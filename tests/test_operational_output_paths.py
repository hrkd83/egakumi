"""運用補助の最終出力が私有領域・保護した外部workspaceだけへ保存されるか確認する。"""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import coverage_client
import credential_session_store
import decision_client
from deployment.helpers import configuration, paths
import integration_client
import prepare_coverage
import render_apim_policy
import waf_probe


class OperationalOutputPathTests(unittest.TestCase):
    """通信をせず、共通writerの保存境界と要求前の拒否を検証する。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.repository = self.base / "repository"
        self.repository.mkdir()
        self.original_parent = paths.private_parent
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for module in (integration_client, coverage_client, decision_client,
                       credential_session_store, waf_probe, render_apim_policy,
                       prepare_coverage, paths):
            self.patches.enter_context(patch.object(module, "private_parent", self.private_parent))

    def private_parent(self, target):
        return self.original_parent(target, self.repository)

    def writers(self):
        return {
            "integration": lambda target: integration_client.private_write(target, "Synthetic result.\n"),
            "coverage": lambda target: coverage_client.private_bytes(target, b"Synthetic artifact.\n"),
            "decision": lambda target: decision_client.write_new(target, {"succeeded": False}),
            "session": lambda target: credential_session_store.FileSessionStore(target).write(
                {"synthetic": True}, expected_revision=0),
            "waf": lambda target: waf_probe._write(target, {"network_requests": 0}),
            "policy": lambda target: render_apim_policy.main(["--output", str(target)]),
        }

    def test_final_outputs_are_owner_only_under_the_selected_private_root(self):
        with patch.object(render_apim_policy, "render_service_policy", return_value="<policies />"), \
                redirect_stdout(io.StringIO()):
            for name, write in self.writers().items():
                with self.subTest(writer=name):
                    target = self.repository / ".local/example/evidence" / (name + ".json")
                    write(target)
                    self.assertEqual(0o600, target.stat().st_mode & 0o777)
                    self.assertEqual(0o700, target.parent.stat().st_mode & 0o777)

    def test_old_repository_output_locations_are_refused_without_creating_files(self):
        for name, write in self.writers().items():
            with self.subTest(writer=name):
                target = self.repository / ".state" / (name + ".json")
                with self.assertRaisesRegex(ValueError, "under .local or .azure"):
                    write(target)
        self.assertFalse((self.repository / ".state").exists())

    def test_existing_external_private_workspace_remains_usable(self):
        with patch.object(render_apim_policy, "render_service_policy", return_value="<policies />"), \
                redirect_stdout(io.StringIO()):
            for name, write in self.writers().items():
                with self.subTest(writer=name):
                    target = self.base / "private-workspace/evidence" / (name + ".json")
                    write(target)
                    self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_private_parent_symlinks_are_rejected_without_touching_the_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.repository / ".local").symlink_to(outside, target_is_directory=True)
        for name, write in self.writers().items():
            with self.subTest(writer=name):
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    write(self.repository / ".local/example/evidence" / (name + ".json"))
        self.assertEqual([], list(outside.iterdir()))

    def test_integration_refuses_old_output_before_reading_credentials_or_sending(self):
        with patch.object(sys, "argv", ["integration", "pii", "--url", "https://gateway.example.invalid",
                                       "--output", str(self.repository / ".state/result.json")]), \
                patch.object(integration_client, "auth_headers", side_effect=AssertionError("credential read")), \
                patch.object(integration_client, "rest_probe", side_effect=AssertionError("network request")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(2, integration_client.main())

    def test_coverage_refuses_old_artifact_before_credentials_or_sending(self):
        with patch.object(sys, "argv", ["coverage", "run", "tts", "--url", "https://gateway.example.invalid",
                                       "--output", str(self.repository / ".local/example/evidence/result.json"),
                                       "--artifact", str(self.repository / "outputs/audio.wav")]), \
                patch.object(coverage_client, "auth_headers", side_effect=AssertionError("credential read")), \
                patch.object(coverage_client, "probe", side_effect=AssertionError("network request")):
            with self.assertRaisesRegex(ValueError, "under .local or .azure"):
                coverage_client.main()
        self.assertFalse((self.repository / "outputs").exists())

    def test_waf_refuses_old_output_before_sending(self):
        case = SimpleNamespace(case_id="synthetic", category="synthetic", intent="synthetic",
                               path="/synthetic", target="gateway")
        with patch.object(waf_probe, "build_corpus", return_value=[case]), \
                patch.object(waf_probe, "run_probe", side_effect=AssertionError("network request")), \
                patch.object(waf_probe, "ThreadPoolExecutor", side_effect=AssertionError("network pool")), \
                patch.object(waf_probe.argparse.ArgumentParser, "error", side_effect=ValueError("output rejected")):
            with self.assertRaisesRegex(ValueError, "output rejected"):
                waf_probe.main(["--gateway-base-url", "https://gateway.example.invalid",
                                "--output", str(self.repository / ".state/waf.json")])

    def test_coverage_preparation_rejects_old_output_before_reading_inputs(self):
        with patch.object(sys, "argv", ["prepare", "--base", "not-read.json", "--overlay", "not-read-api.json",
                                       "--output", str(self.repository / "params/example.local.json")]), \
                patch.object(prepare_coverage, "load_configuration_source", side_effect=AssertionError("input read")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(2, prepare_coverage.main())
        self.assertFalse((self.repository / "params").exists())

    def test_configuration_render_uses_the_protected_destination_and_refuses_legacy(self):
        config = {"resourceGroupNames": {}, "apiPublicationStatus": "configured"}
        target = self.repository / ".local/example/generated/configuration.json"
        with patch.object(configuration, "load_configuration", return_value=config), \
                patch.object(configuration, "build_bicep_configuration", return_value={"synthetic": True}), \
                patch.object(configuration.sys, "stderr", io.StringIO()), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, configuration.main(["render", "--config", "not-read.json", "--output", str(target)]))
            self.assertEqual(2, configuration.main([
                "render", "--config", "not-read.json", "--output",
                str(self.repository / "infra/main.parameters.json")]))
        self.assertEqual({"synthetic": True}, json.loads(target.read_text()))
        self.assertEqual(0o600, target.stat().st_mode & 0o777)
        self.assertFalse((self.repository / "infra").exists())


if __name__ == "__main__":
    unittest.main()
