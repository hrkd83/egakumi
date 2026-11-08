"""ホスト暗号化の機能登録を、CLIを起動せず状態遷移と構築前の停止で検証する。"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle


class HostEncryptionRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "subscriptionId": "11111111-1111-4111-8111-111111111111",
            "management": {"encryptionAtHost": True},
            "environmentName": "test",
        }
        self.enterContext(mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}))
        # 依存の差し替え漏れがあっても、実Azure CLIを起動させない。
        guard = mock.patch.object(lifecycle.subprocess, "run", side_effect=AssertionError("No CLI process is permitted"))
        guard.start()
        self.addCleanup(guard.stop)

    def feature(self, operation):
        return ["az", "feature", operation, "--namespace", "Microsoft.Compute", "--name", "EncryptionAtHost",
                "--subscription", self.config["subscriptionId"], "-o", "json"]

    def provider(self, operation):
        return ["az", "provider", operation, "--namespace", "Microsoft.Compute",
                "--subscription", self.config["subscriptionId"], "-o", "json"]

    def run_transition(self, states, provider_states=("Registered",)):
        """登録応答を完了と誤認しないよう、書込受付には意図的に未完了を返す。"""
        observations = iter(states)
        providers = iter(provider_states)

        def command(arguments):
            if arguments == self.feature("show"):
                return {"properties": {"state": next(observations)}}
            if arguments == self.feature("register"):
                return {"properties": {"state": "Registering"}}
            if arguments == self.provider("register"):
                return {"registrationState": "Registering"}
            if arguments == self.provider("show"):
                return {"registrationState": next(providers)}
            self.fail("Unexpected CLI command: " + repr(arguments))

        with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
             mock.patch.object(lifecycle.time, "monotonic", return_value=0), \
             mock.patch.object(lifecycle.time, "sleep") as sleep:
            lifecycle.ensure_host_encryption(self.config)
        return [call.args[0] for call in run.call_args_list], sleep

    def test_disabled_or_omitted_setting_makes_no_feature_or_provider_calls(self):
        for management in ({"encryptionAtHost": False}, {}):
            with self.subTest(management=management):
                self.config["management"] = management
                with mock.patch.object(lifecycle, "run") as run:
                    lifecycle.ensure_host_encryption(self.config)
                run.assert_not_called()
        self.config.pop("management")
        with mock.patch.object(lifecycle, "run") as run:
            lifecycle.ensure_host_encryption(self.config)
        run.assert_not_called()

    def test_already_registered_skips_feature_registration_but_refreshes_compute(self):
        calls, sleep = self.run_transition(["Registered"])
        self.assertEqual(calls, [self.feature("show"), self.provider("register"), self.provider("show")])
        sleep.assert_not_called()

    def test_compute_refresh_cli_failure_reports_stage_without_external_output(self):
        external = "SENSITIVE-REGISTRATION-ERROR-FIXTURE"
        with mock.patch.object(lifecycle, "run", side_effect=[
                {"properties": {"state": "Registered"}}, lifecycle.DeploymentError(external)]) as run, \
             mock.patch.object(lifecycle.time, "monotonic", return_value=0), \
             mock.patch.object(lifecycle.time, "sleep") as sleep:
            with self.assertRaises(lifecycle.DeploymentError) as raised:
                lifecycle.ensure_host_encryption(self.config)
        message = str(raised.exception)
        self.assertIn("Microsoft.Compute", message)
        self.assertIn("provider-refresh-register", message)
        self.assertIn("sign-in", message)
        self.assertIn("registration permissions", message)
        self.assertIn("registration state", message)
        self.assertNotIn(external, message)
        self.assertNotIn("corresponding deployment", message)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [self.feature("show"), self.provider("register")])
        sleep.assert_not_called()

    def test_retry_after_interrupted_compute_refresh_does_not_treat_registered_feature_as_ready(self):
        with mock.patch.object(lifecycle, "run", side_effect=[
                {"properties": {"state": "Registered"}}, lifecycle.DeploymentError("CLI operation failed")]) as run, \
             mock.patch.object(lifecycle.time, "monotonic", return_value=0):
            with self.assertRaises(lifecycle.DeploymentError):
                lifecycle.ensure_host_encryption(self.config)
        self.assertEqual([call.args[0] for call in run.call_args_list], [self.feature("show"), self.provider("register")])
        calls, _ = self.run_transition(["Registered"], ["Registering", "Registered"])
        self.assertEqual(calls, [self.feature("show"), self.provider("register"),
            self.provider("show"), self.provider("show")])

    def test_unregistered_feature_registers_once_then_refreshes_compute_after_completion(self):
        for initial in ("NotRegistered", "Unregistered"):
            with self.subTest(initial=initial):
                calls, sleep = self.run_transition([initial, "Registering", "Registered"])
                self.assertEqual(calls, [self.feature("show"), self.feature("register"),
                    self.feature("show"), self.feature("show"), self.provider("register"), self.provider("show")])
                self.assertGreaterEqual(sleep.call_count, 1)
                self.assertTrue(all(call.args == (10,) for call in sleep.call_args_list))

    def test_in_progress_registration_waits_and_refreshes_without_duplicate_feature_registration(self):
        calls, sleep = self.run_transition(["Registering", "Registering", "Registered"])
        self.assertEqual(calls, [self.feature("show")] * 3 + [self.provider("register"), self.provider("show")])
        self.assertGreaterEqual(sleep.call_count, 1)

    def test_unregistering_waits_before_requesting_registration(self):
        calls, sleep = self.run_transition(["Unregistering", "Unregistering", "Unregistered", "Registering", "Registered"])
        self.assertEqual(calls, [self.feature("show")] * 3 + [self.feature("register"),
            self.feature("show"), self.feature("show"), self.provider("register"), self.provider("show")])
        self.assertGreaterEqual(sleep.call_count, 2)

    def test_compute_refresh_is_polled_and_no_unbounded_wait_option_is_used(self):
        calls, sleep = self.run_transition(["Registering", "Registered"], ["Registering", "Registered"])
        self.assertEqual(calls[-3:], [self.provider("register"), self.provider("show"), self.provider("show")])
        self.assertTrue(all("--wait" not in arguments for arguments in calls))
        self.assertGreaterEqual(sleep.call_count, 1)

    def test_malformed_or_unknown_feature_state_fails_without_registering(self):
        for response in ({}, {"properties": {}}, {"properties": None}, {"properties": []},
                         {"properties": {"state": None}}, {"properties": {"state": "Failed"}},
                         {"properties": {"state": "Pending"}}, {"properties": {"state": "unexpected"}}):
            with self.subTest(response=response):
                with mock.patch.object(lifecycle, "run", return_value=response) as run, \
                     mock.patch.object(lifecycle.time, "monotonic", return_value=0):
                    with self.assertRaises(lifecycle.DeploymentError):
                        lifecycle.ensure_host_encryption(self.config)
                self.assertEqual([call.args[0] for call in run.call_args_list], [self.feature("show")])

    def test_unverifiable_compute_refresh_fails_instead_of_allowing_vm_deployment(self):
        for response in ({}, {"registrationState": None}, {"registrationState": "Failed"}):
            with self.subTest(response=response):
                with mock.patch.object(lifecycle, "run", side_effect=[
                        {"properties": {"state": "Registered"}}, {}, response]) as run, \
                     mock.patch.object(lifecycle.time, "monotonic", return_value=0):
                    with self.assertRaises(lifecycle.DeploymentError):
                        lifecycle.ensure_host_encryption(self.config)
                self.assertEqual([call.args[0] for call in run.call_args_list],
                    [self.feature("show"), self.provider("register"), self.provider("show")])

    def test_pending_feature_times_out_without_compute_refresh_or_repeat_registration(self):
        for initial in ("NotRegistered", "Registering", "Unregistering"):
            with self.subTest(initial=initial):
                clock = [0]

                def sleep(seconds):
                    self.assertEqual(seconds, 10)
                    clock[0] += 1800

                def command(arguments):
                    self.assertIn(arguments, (self.feature("show"), self.feature("register")))
                    return {"properties": {"state": "Registering" if initial == "NotRegistered" and clock[0] else initial}}

                with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
                     mock.patch.object(lifecycle.time, "monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(lifecycle.time, "sleep", side_effect=sleep):
                    with self.assertRaisesRegex(lifecycle.DeploymentError, "EncryptionAtHost"):
                        lifecycle.ensure_host_encryption(self.config)
                writes = [call.args[0] for call in run.call_args_list if call.args[0][2] == "register"]
                self.assertEqual(writes, [self.feature("register")] if initial == "NotRegistered" else [])

    def test_compute_refresh_shares_feature_deadline_instead_of_resetting_it(self):
        clock = [0]
        shows = 0

        def command(arguments):
            nonlocal shows
            if arguments == self.feature("show"):
                shows += 1
                return {"properties": {"state": "Registering" if shows == 1 else "Registered"}}
            if arguments == self.provider("register"):
                return {"registrationState": "Registering"}
            if arguments == self.provider("show"):
                return {"registrationState": "Registering"}
            self.fail("Unexpected CLI command: " + repr(arguments))

        def sleep(seconds):
            self.assertEqual(seconds, 10)
            clock[0] += 900

        with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
             mock.patch.object(lifecycle.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(lifecycle.time, "sleep", side_effect=sleep):
            with self.assertRaises(lifecycle.DeploymentError):
                lifecycle.ensure_host_encryption(self.config)
        self.assertLessEqual(clock[0], 1800)
        self.assertEqual(sum(call.args[0] == self.provider("register") for call in run.call_args_list), 1)

    def test_cli_failures_do_not_reveal_stdout_stderr_or_continue_after_failure(self):
        secret = "SENSITIVE-CLI-OUTPUT-FIXTURE"
        sequence = [self.feature("show"), self.feature("register"), self.feature("show"),
                    self.provider("register"), self.provider("show")]
        for failing in range(len(sequence)):
            with self.subTest(command=sequence[failing]):
                observed = []

                def process(arguments, **kwargs):
                    position = len(observed)
                    observed.append(arguments)
                    self.assertEqual(arguments, sequence[position])
                    if position == failing:
                        return subprocess.CompletedProcess(arguments, 1, stdout=secret, stderr=secret)
                    if arguments == self.feature("show"):
                        state = "NotRegistered" if position == 0 else "Registered"
                        return subprocess.CompletedProcess(arguments, 0, stdout='{"properties":{"state":"' + state + '"}}', stderr="")
                    return subprocess.CompletedProcess(arguments, 0, stdout="{}", stderr="")

                output = io.StringIO()
                with mock.patch.object(lifecycle.subprocess, "run", side_effect=process), \
                     mock.patch.object(lifecycle.time, "monotonic", return_value=0), \
                     mock.patch.object(lifecycle.time, "sleep"), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    with self.assertRaises(lifecycle.DeploymentError) as raised:
                        lifecycle.ensure_host_encryption(self.config)
                self.assertEqual(len(observed), failing + 1)
                self.assertNotIn(secret, str(raised.exception) + output.getvalue())


class HostEncryptionHookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111",
                       "management": {"encryptionAtHost": True},
            "environmentName": "test",
                       "services": {}}
        guard = mock.patch.object(lifecycle.subprocess, "run", side_effect=AssertionError("No CLI process is permitted"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_feature_preparation_runs_after_every_required_provider_finishes_registering(self):
        events = []
        network_shows = 0

        def command(arguments):
            nonlocal network_shows
            namespace = arguments[arguments.index("--namespace") + 1]
            events.append((arguments[2], namespace))
            if namespace == "Microsoft.Network" and arguments[2] == "show":
                network_shows += 1
                return {"registrationState": "NotRegistered" if network_shows == 1 else "Registered"}
            return {"registrationState": "Registered"}

        def encryption(config):
            self.assertIs(config, self.config)
            self.assertEqual(network_shows, 2)
            self.assertEqual({namespace for operation, namespace in events if operation == "show"}, set(lifecycle.PROVIDERS))
            events.append(("feature-preparation", "Microsoft.Compute"))

        with mock.patch.object(lifecycle, "run", side_effect=command), \
             mock.patch.object(lifecycle, "ensure_host_encryption", side_effect=encryption) as feature:
            lifecycle.register_providers(self.config)
        feature.assert_called_once_with(self.config)
        self.assertEqual(events[-2:], [("show", "Microsoft.Network"), ("feature-preparation", "Microsoft.Compute")])

    def test_required_provider_cli_failure_reports_stage_without_external_output(self):
        external = "SENSITIVE-REGISTRATION-ERROR-FIXTURE"
        with mock.patch.object(lifecycle, "run", side_effect=[
                {"registrationState": "NotRegistered"}, lifecycle.DeploymentError(external)]) as run, \
             mock.patch.object(lifecycle, "ensure_host_encryption") as feature, \
             mock.patch.object(lifecycle.time, "sleep") as sleep:
            with self.assertRaises(lifecycle.DeploymentError) as raised:
                lifecycle.register_providers(self.config)
        message = str(raised.exception)
        self.assertIn("Microsoft.Network", message)
        self.assertIn("provider-register", message)
        self.assertIn("sign-in", message)
        self.assertIn("registration permissions", message)
        self.assertIn("registration state", message)
        self.assertNotIn(external, message)
        self.assertNotIn("corresponding deployment", message)
        self.assertEqual([call.args[0][:3] for call in run.call_args_list],
                         [["az", "provider", "show"], ["az", "provider", "register"]])
        self.assertTrue(all(call.args[0][call.args[0].index("--namespace") + 1] == "Microsoft.Network"
                            for call in run.call_args_list))
        feature.assert_not_called()
        sleep.assert_not_called()

    def test_provider_timeout_prevents_feature_preparation(self):
        with mock.patch.object(lifecycle, "run", return_value={"registrationState": "Registering"}), \
             mock.patch.object(lifecycle.time, "monotonic", side_effect=[0, 1800]), \
             mock.patch.object(lifecycle.time, "sleep") as sleep, \
             mock.patch.object(lifecycle, "ensure_host_encryption") as feature:
            with self.assertRaises(lifecycle.DeploymentError):
                lifecycle.register_providers(self.config)
        feature.assert_not_called()
        sleep.assert_not_called()

    def test_feature_preparation_error_propagates_and_prevents_billable_preparation(self):
        # 実preprovisionを通し、機能未登録が有料の監視準備より先に停止することを確認する。
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(lifecycle, "ROOT", Path(directory).resolve()), \
             mock.patch.object(lifecycle, "local_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
             mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "run", return_value={"registrationState": "Registered"}), \
             mock.patch.object(lifecycle, "ensure_host_encryption", side_effect=lifecycle.DeploymentError("EncryptionAtHost pending")), \
             mock.patch.object(lifecycle, "ArmTransport") as arm, \
             mock.patch.object(lifecycle, "deploy_phase") as deploy:
            with self.assertRaisesRegex(lifecycle.DeploymentError, "EncryptionAtHost"):
                lifecycle.preprovision(self.config)
        arm.assert_not_called()
        deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
