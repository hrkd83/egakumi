"""Exercise postdown reporting with synthetic inputs in an isolated lifecycle root."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup, grpc_lifecycle
from configuration_fixtures import connection_fixture
from lifecycle_fixtures import LifecycleFixture, ScriptedTransport


class CredentialCleanupCliTests(LifecycleFixture, unittest.TestCase):
    """Run the real hook and cleanup against bounded, read-only fake ARM replies."""

    def prepare_postdown(self, *, credential_manager):
        if credential_manager:
            providers = copy.deepcopy(connection_fixture()["credentialProviders"])
            for provider in providers:
                provider["clientSecretFile"] = str(self.paths.secrets / "synthetic-oauth.txt")
            self.config["credentialProviders"] = providers
        self.state = lifecycle.state_from_outputs(self.config, self.outputs())
        lifecycle.protected_json(self.paths.state / "latest.json", self.state)
        lifecycle.protected_json(self.paths.params, {"fixture": "synthetic-config"})
        lifecycle.protected_json(self.paths.parameters, {"fixture": "SYNTHETIC_PRIVATE_INPUT"})
        self.generated_credentials = [
            grpc_lifecycle.secret_path(self.config, self.root, purpose)
            for purpose in ("l7-ingress", "realtime-ticket")
        ]
        for path in self.generated_credentials:
            lifecycle.protected_json(path, {"value": "SYNTHETIC_PRIVATE_INPUT"})
        # Five owned RGs and three exact service tombstones are already absent.
        # The last response is the complete, empty owned API Center history page.
        return ScriptedTransport(
            [{"statusCode": 404, "body": None, "headers": {}} for _ in range(8)]
            + [{"statusCode": 200, "body": {"value": []}, "headers": {}}]
        )

    def run_successful_postdown(self, *, credential_manager):
        transport = self.prepare_postdown(credential_manager=credential_manager)
        receipt_path = self.paths.state / "cleanup-postdown.json"
        stdout = io.StringIO()
        warning_observed = []
        case = self

        class CheckedStderr(io.StringIO):
            def write(self, value):
                if "Credential Manager:" in value:
                    # The user must get both the durable receipt and local cleanup,
                    # even when the final result carries an unresolved CM warning.
                    case.assertTrue(receipt_path.is_file())
                    case.assertFalse(case.paths.parameters.exists())
                    case.assertTrue(all(not path.exists() for path in case.generated_credentials))
                    warning_observed.append(True)
                return super().write(value)

        stderr = CheckedStderr()
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(cleanup, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(cleanup, "post_down", wraps=cleanup.post_down) as operation, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = lifecycle.main(["postdown", "--config", str(self.paths.params)])

        self.assertEqual(exit_code, 0)
        operation.assert_called_once_with(self.paths.params, self.state, transport)
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(json.loads(stdout.getvalue()), receipt)
        self.assertEqual(receipt["subscriptionId"], self.config["subscriptionId"])
        self.assertEqual(receipt["resourceStem"], self.config["resourceStem"])
        self.assertIn("observedAtUtc", receipt)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertFalse(self.paths.parameters.exists())
        self.assertTrue(all(not path.exists() for path in self.generated_credentials))
        self.assertEqual(len(transport.calls), 9)
        self.assertFalse(transport.responses)
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls))
        self.assertFalse(transport.mutations())
        self.arm_environment.assert_not_called()
        self.assertNotIn("SYNTHETIC_PRIVATE_INPUT", stdout.getvalue() + stderr.getvalue())
        return receipt["result"], stderr.getvalue(), warning_observed

    def test_postdown_saves_qualified_cm_receipt_cleans_inputs_then_warns(self):
        result, stderr, warnings = self.run_successful_postdown(credential_manager=True)
        self.assertEqual(result["status"], "resources-absent-with-unverified-credential-manager-release")
        self.assertEqual(result["resourceCleanupStatus"], "complete")
        self.assertEqual(result["credentialManager"]["status"], "unverified")
        self.assertFalse(result["credentialManager"]["internalAccountReleaseVerified"])
        self.assertFalse(result["credentialManager"]["sameNameCredentialRecreationVerified"])
        self.assertTrue(result["credentialManager"]["apimSoftDeleteAbsenceVerified"])
        self.assertEqual(warnings, [True])
        self.assertIn("未確認", stderr)
        self.assertIn("Microsoft", stderr)

    def test_postdown_without_cm_keeps_normal_result_and_emits_no_warning(self):
        result, stderr, warnings = self.run_successful_postdown(credential_manager=False)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("credentialManager", result)
        self.assertNotIn("resourceCleanupStatus", result)
        self.assertEqual(stderr, "")
        self.assertEqual(warnings, [])

    def test_failed_postdown_keeps_inputs_and_does_not_emit_success_warning(self):
        transport = self.prepare_postdown(credential_manager=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        failure = cleanup.CleanupError("cleanup-pending", "Synthetic cleanup is pending")
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(cleanup, "post_down", side_effect=failure), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = lifecycle.main(["postdown", "--config", str(self.paths.params)])
        self.assertEqual(exit_code, 1)
        self.assertFalse((self.paths.state / "cleanup-postdown.json").exists())
        self.assertTrue(self.paths.parameters.exists())
        self.assertTrue(all(path.exists() for path in self.generated_credentials))
        self.assertIn("cleanup-pending", stderr.getvalue())
        self.assertNotIn("Credential Manager:", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(transport.calls, [])
        self.arm_environment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
