"""Entra登録に渡す予定名と戻り先URLのローカル計算を検証する。

Subscriptionと製品・環境名からBicepの名前計算を行い、資源やマネージドIDを先行作成しなくても登録準備できることを確かめる。
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock


CORE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_registration", CORE / "scripts/prepare_registration.py")
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)
# 製品・環境の同じ入力は、Subscriptionを変えても同じ公開名になります。
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
EXPECTED = {
    "resourceStem": "test-egakumi",
    "apimName": "apim-test-egakumi",
    "apiCenterName": "apic-test-egakumi",
    "gatewayOrigin": "https://gateway.example.com",
    "credentialManagerRedirectUri": "https://authorization-manager.consent.azure-apim.net/redirect/apim/apim-test-egakumi",
    "apiCenterPortalRedirectUri": "https://apic-test-egakumi.portal.eastus.azure-apicenter.ms",
}


class RegistrationTests(unittest.TestCase):
    """Entra登録に渡す予定名と戻り先URLのローカル計算を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_real_official_bicep_preserves_existing_registered_redirects(self):
        try:
            registration.bicep_executable()
        except ValueError as error:
            self.skipTest(str(error))
        self.assertEqual(registration.prepare_registration(SUBSCRIPTION, "egakumi", "test"), EXPECTED)

    def test_invalid_inputs_fail_before_any_process_or_config_read(self):
        cases = [
            ("00000000-0000-0000-0000-000000000000", "egakumi", "test", "eastus"),
            (SUBSCRIPTION.replace("-", ""), "egakumi", "test", "eastus"),
            (SUBSCRIPTION, "Egakumi", "test", "eastus"),
            (SUBSCRIPTION, "1egakumi", "test", "eastus"),
            (SUBSCRIPTION, "egakumi--bad", "test", "eastus"),
            (SUBSCRIPTION, "x" * 25, "test", "eastus"),
            (SUBSCRIPTION, "egakumi", "x" * 13, "eastus"),
            (SUBSCRIPTION, "egakumi", "test", "East US"),
        ]
        with mock.patch.object(registration.subprocess, "run") as run, mock.patch.object(registration, "bicep_executable") as executable:
            for values in cases:
                with self.subTest(values=values), self.assertRaises(ValueError):
                    registration.prepare_registration(*values)
        run.assert_not_called()
        executable.assert_not_called()

    def test_inputs_use_only_meaningful_environment_product_names(self):
        values = registration.registration_inputs(SUBSCRIPTION.upper(), "egakumi", "test", "eastus")
        self.assertEqual(values["WORKFORCE_REGISTRATION_RESOURCE_STEM"], "test-egakumi")
        self.assertNotIn("WORKFORCE_REGISTRATION_SUBSCRIPTION_SCOPE", values)
        self.assertNotIn("WORKFORCE_REGISTRATION_APIM_NAME", values)

    def test_names_are_independent_of_subscription_and_have_no_random_suffix(self):
        other = "22222222-2222-4222-8222-222222222222"
        self.assertEqual(registration.prepare_registration(other, "egakumi", "test"), EXPECTED)

    def test_combined_name_length_is_checked_before_compiler(self):
        with mock.patch.object(registration, "bicep_executable") as executable:
            for product, environment in (("p" * 13, "test"), ("p" * 16, "test"), ("p" * 24, "test")):
                with self.subTest(product=product), self.assertRaisesRegex(ValueError, "17 characters"):
                    registration.prepare_registration(SUBSCRIPTION, product, environment)
        executable.assert_not_called()
        values = registration.registration_inputs(SUBSCRIPTION, "p" * 12, "test", "eastus")
        self.assertEqual(values["WORKFORCE_REGISTRATION_RESOURCE_STEM"], "test-" + "p" * 12)


    def test_cli_uses_local_compiler_without_changing_environment_or_reading_config(self):
        compiled = {"parametersJson": json.dumps({"parameters": {"registration": {"value": EXPECTED}}})}
        result = subprocess.CompletedProcess([], 0, json.dumps(compiled), "")
        with mock.patch.dict(os.environ, {"PRIVATE_SENTINEL": "DO_NOT_PRINT", "WORKFORCE_REGISTRATION_RESOURCE_STEM": "untrusted-old-value"}), \
             mock.patch.object(registration, "bicep_executable", return_value="/mock/bicep"), \
             mock.patch.object(registration.subprocess, "run", return_value=result) as run, \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("No configuration or secret file may be read")):
            before = dict(os.environ)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = registration.main(["--subscription-id", SUBSCRIPTION, "--product-name", "egakumi",
                                            "--environment-name", "test", "--public-host-name", "gateway.example.com"])
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), EXPECTED)
        self.assertNotIn("DO_NOT_PRINT", output.getvalue())
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["/mock/bicep", "build-params"])
        self.assertIn("--no-restore", command)
        self.assertEqual(run.call_args.kwargs["env"]["WORKFORCE_REGISTRATION_RESOURCE_STEM"], "test-egakumi")
        self.assertEqual(run.call_args.kwargs["env"]["WORKFORCE_REGISTRATION_PUBLIC_HOST_NAME"], "gateway.example.com")

    def test_cli_failures_do_not_echo_environment_or_private_process_output(self):
        for result in (subprocess.CompletedProcess([], 1, "PRIVATE_STDOUT", "PRIVATE_STDERR"), subprocess.CompletedProcess([], 0, "PRIVATE_STDOUT", "")):
            with self.subTest(code=result.returncode), mock.patch.object(registration, "bicep_executable", return_value="/mock/bicep"), mock.patch.object(registration.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError) as error:
                    registration.prepare_registration(SUBSCRIPTION, "egakumi", "test")
                self.assertNotIn("PRIVATE", str(error.exception))

    def test_missing_explicit_compiler_fails_clearly_without_fallback(self):
        with mock.patch.dict(os.environ, {"BICEP_CLI": "/missing/bicep"}), mock.patch.object(registration.shutil, "which", return_value=None) as which:
            with self.assertRaisesRegex(ValueError, "BICEP_CLI"):
                registration.bicep_executable()
        which.assert_called_once_with("/missing/bicep")


if __name__ == "__main__":
    unittest.main()
