"""保存済みAPIM識別子を使ったEntra登録用URIのローカル計算を検証する。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_registration", CORE / "scripts/prepare_registration.py")
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)
INPUT = {"productName": "egakumi", "environmentName": "test", "apiCenterLocation": "eastus",
         "services": {"apimInstanceId": "k7m2q9"}, "gateway": {"publicHostName": "gateway.example.com"}}
EXPECTED = {
    "resourceStem": "test-egakumi",
    "apimName": "apim-test-egakumi-k7m2q9",
    "apiCenterName": "apic-test-egakumi",
    "gatewayOrigin": "https://gateway.example.com",
    "credentialManagerRedirectUri": "https://authorization-manager.consent.azure-apim.net/redirect/apim/apim-test-egakumi-k7m2q9",
    "apiCenterPortalRedirectUri": "https://apic-test-egakumi.portal.eastus.azure-apicenter.ms",
}


def compiled_result():
    return subprocess.CompletedProcess([], 0, json.dumps({"parametersJson": json.dumps({"parameters": {"registration": {"value": EXPECTED}}})}), "")


class RegistrationTests(unittest.TestCase):
    def test_real_official_bicep_matches_saved_identifier_and_registered_redirect(self):
        try:
            registration.bicep_executable()
        except ValueError as error:
            self.skipTest(str(error))
        self.assertEqual(registration.prepare_registration(INPUT), EXPECTED)

    def test_invalid_naming_inputs_fail_before_compiler_or_secret_read(self):
        cases = []
        for key, value in (("productName", "Upper"), ("productName", "1sample"),
                           ("environmentName", "test--bad"), ("apiCenterLocation", "East US")):
            cases.append({**INPUT, key: value})
        for value in (None, "", "ABC123", "12345", "1234567", "ab-c12"):
            cases.append({**INPUT, "services": {"apimInstanceId": value}})
        for value in ("https://gateway.example.com", "gateway", "Gateway.example.com", "gateway.example.com\n"):
            cases.append({**INPUT, "gateway": {"publicHostName": value}})
        with mock.patch.object(registration.subprocess, "run") as run, mock.patch.object(registration, "bicep_executable") as executable, \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("No files may be read")):
            for config in cases:
                with self.subTest(config=config), self.assertRaises(ValueError):
                    registration.prepare_registration(config)
        run.assert_not_called()
        executable.assert_not_called()

    def test_minimal_naming_inputs_do_not_require_entra_or_subscription_validation(self):
        config = {**INPUT, "subscriptionId": "<AZURE_SUBSCRIPTION_ID>", "credentialProviders": [{"clientSecretFile": "/unread/secret"}],
                  "configFile": "/unread/config"}
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("No referenced files may be read")):
            values = registration.registration_inputs(config)
        self.assertEqual(values["WORKFORCE_REGISTRATION_RESOURCE_STEM"], "test-egakumi")
        self.assertEqual(values["WORKFORCE_REGISTRATION_APIM_INSTANCE_ID"], "k7m2q9")
        self.assertNotIn("WORKFORCE_REGISTRATION_SUBSCRIPTION_SCOPE", values)
        self.assertEqual(registration.registration_inputs(INPUT), values)

    def test_combined_name_length_is_checked_before_compiler(self):
        with mock.patch.object(registration, "bicep_executable") as executable:
            with self.assertRaisesRegex(ValueError, "17 characters"):
                registration.prepare_registration({**INPUT, "productName": "p" * 13})
        executable.assert_not_called()
        values = registration.registration_inputs({**INPUT, "productName": "p" * 12})
        self.assertEqual(values["WORKFORCE_REGISTRATION_RESOURCE_STEM"], "test-" + "p" * 12)

    def test_cli_reads_only_explicit_protected_parameters_and_keeps_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / ".local/test/params.local.json"
            path.parent.mkdir(parents=True, mode=0o700)
            (root / ".local").chmod(0o700)
            path.write_text(json.dumps({**INPUT, "configFile": "unread-config.json", "credentialProviders": [{"clientSecretFile": "unread-secret"}]}))
            path.chmod(0o600)
            with mock.patch.object(registration, "ROOT", root), \
                 mock.patch.dict(os.environ, {"PRIVATE_SENTINEL": "DO_NOT_PRINT", "WORKFORCE_REGISTRATION_RESOURCE_STEM": "old-value"}), \
                 mock.patch.object(registration, "bicep_executable", return_value="/mock/bicep"), \
                 mock.patch.object(registration.subprocess, "run", return_value=compiled_result()) as run:
                before = dict(os.environ)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = registration.main(["--config", str(path)])
                self.assertEqual(dict(os.environ), before)
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), EXPECTED)
            self.assertNotIn("DO_NOT_PRINT", output.getvalue())
        self.assertEqual(run.call_args.args[0][0:2], ["/mock/bicep", "build-params"])
        self.assertIn("--no-restore", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["env"]["WORKFORCE_REGISTRATION_APIM_INSTANCE_ID"], "k7m2q9")

    def test_local_parameters_require_private_canonical_environment_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / ".local/test/params.local.json"
            path.parent.mkdir(parents=True, mode=0o700)
            (root / ".local").chmod(0o700)
            path.write_text(json.dumps(INPUT))
            path.chmod(0o600)
            self.assertEqual(registration.load_registration_configuration(path, root), INPUT)
            for mode in (0o644, 0o666):
                path.chmod(mode)
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    registration.load_registration_configuration(path, root)
            path.chmod(0o600)
            path.write_text(json.dumps({**INPUT, "environmentName": "other"}))
            with self.assertRaises(ValueError):
                registration.load_registration_configuration(path, root)
            path.unlink()
            (root / "outside.json").write_text(json.dumps(INPUT))
            path.symlink_to(root / "outside.json")
            with self.assertRaises(ValueError):
                registration.load_registration_configuration(path, root)
            with self.assertRaises(ValueError):
                registration.load_registration_configuration(root / "outside.json", root)

    def test_cli_failures_do_not_echo_parameters_or_private_process_output(self):
        for result in (subprocess.CompletedProcess([], 1, "PRIVATE_STDOUT", "PRIVATE_STDERR"), subprocess.CompletedProcess([], 0, "PRIVATE_STDOUT", "")):
            with self.subTest(code=result.returncode), mock.patch.object(registration, "bicep_executable", return_value="/mock/bicep"), \
                 mock.patch.object(registration.subprocess, "run", return_value=result), self.assertRaises(ValueError) as caught:
                registration.prepare_registration(INPUT)
            self.assertNotIn("PRIVATE", str(caught.exception))
        with mock.patch.object(registration, "load_registration_configuration", side_effect=ValueError("PRIVATE_INPUT")):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(registration.main(["--config", ".local/test/params.local.json"]), 1)
            self.assertNotIn("PRIVATE_INPUT", error.getvalue())

    def test_missing_explicit_compiler_fails_without_fallback(self):
        with mock.patch.dict(os.environ, {"BICEP_CLI": "/missing/bicep"}), mock.patch.object(registration.shutil, "which", return_value=None) as which:
            with self.assertRaisesRegex(ValueError, "BICEP_CLI"):
                registration.bicep_executable()
        which.assert_called_once_with("/missing/bicep")


if __name__ == "__main__":
    unittest.main()
