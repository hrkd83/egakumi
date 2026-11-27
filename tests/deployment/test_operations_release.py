"""Operation release shares the Gateway lock and stops after a failed stage."""
from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from operations import release


class Context:
    def __init__(self, root):
        self.workspace = mock.Mock(repository=root)
        self.config_path = root / ".local/test/params.local.json"
        self.state_path = root / ".local/test/state/latest.json"
        self.commands = []
        self.gateway_arguments = []
        self.events = []
        self.fail_at = None
        self.lock_depth = 0

    @contextlib.contextmanager
    def locked(self):
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def record(self, kind, value):
        self.events.append((kind, copy.deepcopy(value)))

    def run_cli(self, command):
        if "--execute" in command:
            assert self.lock_depth == 1
        self.commands.append(command)
        if len(self.commands) + len(self.gateway_arguments) == self.fail_at:
            raise OSError("SECRET_SENTINEL")
        return "SECRET_SENTINEL"

    def apply_gateway(self, arguments, *, _locked_workspace):
        assert _locked_workspace is self.workspace and self.lock_depth == 1
        self.gateway_arguments.append(arguments)
        print("SECRET_SENTINEL")
        if len(self.commands) + len(self.gateway_arguments) == self.fail_at:
            raise OSError("SECRET_SENTINEL")
        return 0


class OperationsReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.context = Context(Path(directory.name).resolve())
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.loader = self.enterContext(mock.patch.object(release.OperationContext, "load", return_value=self.context))
        self.gateway = self.enterContext(mock.patch("deployment.deploy_gateway.main", side_effect=self.context.apply_gateway))
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.errors))

    def run_release(self, *args):
        return release.main(["--config", str(self.context.config_path), *args])

    def test_default_all_plans_existing_gateway_then_portal_and_hides_child_output(self):
        self.assertEqual(self.run_release("--component", "all"), 0)
        expected = [[sys.executable, "-B", str(self.context.workspace.repository / "scripts/deployment" / f"deploy_{name}.py"),
                     "--config", str(self.context.config_path), "--state-file", str(self.context.state_path)]
                    for name in ("gateway", "portal")]
        self.assertEqual(self.context.commands, expected)
        self.gateway.assert_not_called()
        self.assertEqual(self.context.events[-1][1]["status"], "planned")
        self.assertNotIn("SECRET_SENTINEL", self.output.getvalue() + self.errors.getvalue())

    def test_execute_passes_only_explicit_mode_and_same_checked_paths(self):
        self.assertEqual(self.run_release("--component", "all", "--execute"), 0)
        expected = ["--config", str(self.context.config_path), "--state-file", str(self.context.state_path), "--execute"]
        self.assertEqual(self.context.gateway_arguments, [expected])
        self.assertEqual(len(self.context.commands), 1)
        self.assertEqual(self.context.commands[0][3:], expected)
        self.assertEqual(self.context.events[-1][1]["completedComponents"], ["gateway", "portal"])
        self.assertNotIn("SECRET_SENTINEL", self.output.getvalue() + self.errors.getvalue())

    def test_single_component_digest_reuses_established_immutable_image_argument(self):
        digest = "sha256:" + "a" * 64
        self.assertEqual(self.run_release("--component", "portal", "--image-digest", digest, "--execute"), 0)
        self.assertEqual(len(self.context.commands), 1)
        self.assertEqual(self.context.commands[0][-3:], ["--image-digest", digest, "--execute"])

    def test_ambiguous_or_mutable_digest_is_rejected_before_context_load(self):
        for component, digest in (("all", "sha256:" + "a" * 64), ("portal", "latest"),
                                  ("gateway", "sha256:" + "A" * 64)):
            with self.subTest(component=component, digest=digest), self.assertRaises(SystemExit):
                self.run_release("--component", component, "--image-digest", digest)
        self.loader.assert_not_called()
        self.assertEqual(self.context.commands, [])

    def test_gateway_failure_does_not_start_portal_or_repeat_gateway(self):
        self.context.fail_at = 1
        self.assertEqual(self.run_release("--component", "all", "--execute"), 1)
        self.assertEqual(len(self.context.gateway_arguments), 1)
        self.assertEqual(self.context.commands, [])
        self.assertEqual(self.context.events[-1][1]["completedComponents"], [])
        self.assertEqual(self.context.events[-1][1]["component"], "gateway")
        self.assertNotIn("SECRET_SENTINEL", self.output.getvalue() + self.errors.getvalue())

    def test_portal_failure_keeps_prior_gateway_completion_in_receipt(self):
        self.context.fail_at = 2
        self.assertEqual(self.run_release("--component", "all", "--execute"), 1)
        self.assertEqual(len(self.context.gateway_arguments), 1)
        self.assertEqual(len(self.context.commands), 1)
        self.assertEqual(self.context.events[-1][1]["completedComponents"], ["gateway"])
        self.assertEqual(self.context.events[-1][1]["component"], "portal")

    def test_child_failure_exit_code_is_preserved_without_child_error_text(self):
        error = release.OperationError("SECRET_SENTINEL", exit_code=7)
        with mock.patch.object(self.context, "run_cli", side_effect=error):
            self.assertEqual(self.run_release("--component", "portal", "--execute"), 7)
        self.assertEqual(self.context.events[-1][1]["exitCode"], 7)
        self.assertEqual(self.context.lock_depth, 0)
        self.assertNotIn("SECRET_SENTINEL", self.output.getvalue() + self.errors.getvalue())

    def test_invalid_selected_context_starts_no_child_and_prints_no_raw_error(self):
        self.loader.side_effect = ValueError("SECRET_SENTINEL")
        self.assertEqual(self.run_release("--component", "gateway"), 1)
        self.assertEqual(self.context.commands, [])
        self.assertNotIn("SECRET_SENTINEL", self.output.getvalue() + self.errors.getvalue())


if __name__ == "__main__":
    unittest.main()
