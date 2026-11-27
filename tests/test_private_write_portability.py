"""Synthetic private storage: native permissions, atomic updates and safe CLI failure."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from clients import integration_client


class PrivateWritePortabilityTests(unittest.TestCase):
    def workspace(self, directory):
        root = Path(directory)
        integration_client.protect_new_path(root, directory=True)
        return root

    def test_missing_posix_flags_and_fchmod_do_not_break_protected_writer(self):
        portable_os = SimpleNamespace(
            fdopen=os.fdopen, fsync=os.fsync, replace=os.replace)
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "synthetic-evidence.json"
            with patch.object(integration_client, "os", portable_os):
                integration_client.private_write(output, '{"synthetic": true}\n')
            self.assertEqual(output.read_text(encoding="utf-8"), '{"synthetic": true}\n')
            integration_client.check_private_permissions(output)

    def test_atomic_update_keeps_original_until_complete_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "sdk-tokens.json"
            integration_client.private_write(output, "synthetic-original")
            native_replace = os.replace
            def replace_checked(source, target):
                self.assertEqual(output.read_text(encoding="utf-8"), "synthetic-original")
                self.assertEqual(Path(source).read_text(encoding="utf-8"), "更新済みの合成テキスト")
                integration_client.check_private_permissions(Path(source))
                native_replace(source, target)
            with patch.object(integration_client.os, "replace", side_effect=replace_checked) as replace:
                integration_client.private_write(output, "更新済みの合成テキスト")
            replace.assert_called_once()
            self.assertEqual(integration_client.private_text(output), "更新済みの合成テキスト")
            integration_client.check_private_permissions(output)
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_protection_failure_preserves_original_and_removes_empty_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "sdk-tokens.json"
            integration_client.private_write(output, "synthetic-original")
            def fail_protection(temporary):
                self.assertEqual(temporary.stat().st_size, 0)
                raise ValueError("Synthetic protection failure")
            with patch.object(integration_client, "protect_new_path", side_effect=fail_protection), \
                 patch.object(integration_client.os, "replace") as replaced:
                with self.assertRaisesRegex(ValueError, "Synthetic protection failure"):
                    integration_client.private_write(output, "synthetic-new-token")
            replaced.assert_not_called()
            self.assertEqual(integration_client.private_text(output), "synthetic-original")
            integration_client.check_private_permissions(output)
            self.assertEqual(list(output.parent.iterdir()), [output])

    @unittest.skipUnless(os.name == "nt", "Uses real Windows DACLs")
    def test_native_windows_credentials_and_updated_file_have_owner_only_dacl(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "credential.json"
            integration_client.private_write(output, '{"access_token":"<SYNTHETIC_FIRST_TOKEN>"}')
            integration_client.check_private_permissions(output)
            self.assertEqual(integration_client.auth_headers(output), {"Authorization": "Bearer <SYNTHETIC_FIRST_TOKEN>"})
            integration_client.private_write(output, '{"access_token":"<SYNTHETIC_UPDATED_TOKEN>"}')
            integration_client.check_private_permissions(output)
            self.assertEqual(integration_client.auth_headers(output), {"Authorization": "Bearer <SYNTHETIC_UPDATED_TOKEN>"})

    def test_unsafe_existing_file_is_refused_without_repair_or_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "unsafe-file"
            output.write_text("synthetic-original", encoding="utf-8")
            if os.name != "nt":
                output.chmod(0o644)
            # Windows file permissions are inherited, not a protected DACL.
            with self.assertRaises(ValueError):
                integration_client.private_text(output)
            with patch.object(integration_client.tempfile, "mkstemp") as created:
                with self.assertRaises(ValueError):
                    integration_client.private_write(output, "synthetic-replacement")
                created.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), "synthetic-original")
            with self.assertRaises(ValueError):
                integration_client.check_private_permissions(output)

    def test_reader_accepts_explicit_external_input_without_creating_or_repairing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            external = self.workspace(directory) / "credential"
            external.touch()
            integration_client.protect_new_path(external)
            external.write_text("合成の資格情報", encoding="utf-8")
            with patch.object(integration_client, "private_parent", side_effect=AssertionError("output-only helper")), \
                 patch.object(integration_client, "protect_new_path", side_effect=AssertionError("ACL repair")):
                self.assertEqual(integration_client.private_text(external), "合成の資格情報")

    def test_linked_parent_is_refused_without_reading_or_changing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            outside = root / "outside"
            outside.mkdir()
            integration_client.protect_new_path(outside, directory=True)
            target = outside / "credential"
            integration_client.private_write(target, "synthetic-original")
            link = root / "linked"
            if os.name == "nt":
                from deployment.helpers.powershell_runtime import locate_powershell, powershell_environment
                executable = locate_powershell()
                environment = powershell_environment(executable)
                environment.update({"EGAKUMI_TEST_LINK": str(link), "EGAKUMI_TEST_TARGET": str(outside)})
                result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command",
                                         "New-Item -ItemType Junction -Path $env:EGAKUMI_TEST_LINK -Value $env:EGAKUMI_TEST_TARGET | Out-Null"],
                                        env=environment, capture_output=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, "Synthetic junction creation failed")
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                with self.assertRaises(ValueError):
                    integration_client.private_text(link / "credential")
                with self.assertRaises(ValueError):
                    integration_client.private_write(link / "credential", "synthetic-replacement")
                self.assertEqual(integration_client.private_text(target), "synthetic-original")
            finally:
                if os.name == "nt":
                    link.rmdir()
                else:
                    link.unlink()

    def test_parent_validation_happens_before_file_open(self):
        with patch.object(integration_client, "private_parent", side_effect=ValueError("link rejected")), \
             patch.object(integration_client.tempfile, "mkstemp") as opened:
            with self.assertRaisesRegex(ValueError, "link rejected"):
                integration_client.private_write(Path("synthetic-link.json"), "{}")
            opened.assert_not_called()

    def test_final_storage_failure_has_fixed_class_and_exit_two_without_error_content(self):
        async def synthetic_probe(*args, **kwargs):
            return {"kind": "pii", "succeeded": True, "httpStatus": 200}
        with tempfile.TemporaryDirectory() as directory:
            output = self.workspace(directory) / "result.json"
            standard_output, standard_error = io.StringIO(), io.StringIO()
            with patch.object(sys, "argv", ["integration", "pii", "--url", "https://gateway.example.invalid",
                                           "--output", str(output)]), \
                 patch.object(integration_client, "rest_probe", side_effect=synthetic_probe), \
                 patch.object(integration_client, "private_write", side_effect=OSError("synthetic-private-token /synthetic/private-path")), \
                 redirect_stdout(standard_output), redirect_stderr(standard_error):
                self.assertEqual(integration_client.main(), 2)
            result = json.loads(standard_output.getvalue())
            self.assertEqual(result, {"kind": "pii", "succeeded": False, "failureClass": "client-output-storage",
                                      "probeResult": {"kind": "pii", "succeeded": True, "httpStatus": 200}})
            self.assertEqual(standard_error.getvalue(), "")
            self.assertNotIn("synthetic-private", standard_output.getvalue())
            self.assertNotIn("Traceback", standard_output.getvalue())


if __name__ == "__main__":
    unittest.main()
