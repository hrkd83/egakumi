"""azdの各段階を正しい順で実行し、失敗時に止まることを検証する。

偽のCLIとARM transportで事前確認・監視準備・構築後設定・撤去の境界を再現する。秘密の一時入力や保持設定を扱うため、成功系だけでなく中断時の挙動も調べる。
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import ssl
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class ProviderRegistrationTests(unittest.TestCase):
    """本物のCLIを呼ばず、標準RPの登録と完了待ちを検証する。"""

    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111"}

    def assert_provider_commands_only(self, calls):
        for call in calls:
            arguments = call.args[0]
            self.assertEqual(arguments[:2], ["az", "provider"])
            self.assertIn(arguments[2], ("show", "register"))
            self.assertEqual(arguments[arguments.index("--subscription") + 1], self.config["subscriptionId"])
            self.assertEqual(arguments[-2:], ["-o", "json"])

    def test_missing_standard_registrations_wait_until_registered(self):
        for namespace in ("Microsoft.Network", "Microsoft.Compute"):
            for initial in ({}, {"registrationState": "NotRegistered"}):
                with self.subTest(namespace=namespace, initial=initial):
                    observations = iter([initial, {"registrationState": "Registering"}, {"registrationState": "Registered"}])
                    def command(arguments):
                        if arguments[arguments.index("--namespace") + 1] != namespace:
                            return {"registrationState": "Registered"}
                        return next(observations) if arguments[2] == "show" else {"registrationState": "Registering"}
                    with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
                         mock.patch.object(lifecycle.time, "monotonic", return_value=0), \
                         mock.patch.object(lifecycle.time, "sleep") as sleep:
                        lifecycle.register_providers(self.config)
                    writes = [c.args[0] for c in run.call_args_list if c.args[0][2] == "register"]
                    self.assertEqual(len(writes), 1)
                    self.assertEqual(writes[0][writes[0].index("--namespace") + 1], namespace)
                    self.assertEqual(sum(c.args[0][2] == "show" and c.args[0][c.args[0].index("--namespace") + 1] == namespace for c in run.call_args_list), 3)
                    sleep.assert_called_once_with(10)
                    self.assert_provider_commands_only(run.call_args_list)

    def test_both_missing_providers_register_once_then_read_completion(self):
        counts = {namespace: 0 for namespace in ("Microsoft.Network", "Microsoft.Compute")}
        def command(arguments):
            namespace = arguments[arguments.index("--namespace") + 1]
            if namespace not in counts:
                return {"registrationState": "Registered"}
            if arguments[2] == "register":
                return {"registrationState": "Registering"}
            counts[namespace] += 1
            return {"registrationState": "NotRegistered" if counts[namespace] == 1 else "Registered"}
        with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
             mock.patch.object(lifecycle.time, "monotonic", return_value=0), \
             mock.patch.object(lifecycle.time, "sleep") as sleep:
            lifecycle.register_providers(self.config)
        writes = [c.args[0][c.args[0].index("--namespace") + 1] for c in run.call_args_list if c.args[0][2] == "register"]
        self.assertEqual(writes, list(("Microsoft.Network", "Microsoft.Compute")))
        self.assertEqual(set(counts.values()), {2})
        sleep.assert_not_called()
        self.assert_provider_commands_only(run.call_args_list)

    def test_standard_registration_timeout_stops_without_repeat_registration(self):
        for namespace in ("Microsoft.Network", "Microsoft.Compute"):
            with self.subTest(namespace=namespace):
                def command(arguments):
                    return {"registrationState": "Registering" if arguments[arguments.index("--namespace") + 1] == namespace else "Registered"}
                with mock.patch.object(lifecycle, "run", side_effect=command) as run, \
                     mock.patch.object(lifecycle.time, "monotonic", side_effect=[0, 1801]), \
                     mock.patch.object(lifecycle.time, "sleep") as sleep:
                    with self.assertRaisesRegex(lifecycle.DeploymentError, "registration is still pending"):
                        lifecycle.register_providers(self.config)
                self.assertEqual(sum(c.args[0][2] == "register" for c in run.call_args_list), 1)
                self.assertEqual(sum(c.args[0][2] == "show" and c.args[0][c.args[0].index("--namespace") + 1] == namespace for c in run.call_args_list), 2)
                sleep.assert_not_called()
                self.assert_provider_commands_only(run.call_args_list)


class ArmTransportTests(LifecycleFixture, unittest.TestCase):
    """azdの各段階を正しい順で実行し、失敗時に止まることを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        super().setUp()
        self.transport = lifecycle.ArmTransport(self.config)
        self.transport.token, self.transport.expires = "TOKEN_FIXTURE", float("inf")
        self.opener = mock.Mock()
        self.opener.open.side_effect = AssertionError("Forbidden request must not reach the HTTP opener")
        self.transport.opener = self.opener
        self.path = f"/subscriptions/{self.config['subscriptionId']}/providers/Microsoft.Resources/deployments/example?api-version=2022-09-01"

    def test_other_host_subscription_port_and_userinfo_are_rejected(self):
        invalid = [
            "https://other.example.com" + self.path,
            "https://management.azure.com.other.example.com" + self.path,
            self.path.replace(self.config["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            "https://management.azure.com:8443" + self.path,
            "https://:PRIVATE_PASSWORD_FIXTURE@management.azure.com" + self.path,
        ]
        for target in invalid:
            with self.subTest(target=target.split("@")[-1]):
                with self.assertRaises(lifecycle.DeploymentError):
                    self.transport.request("GET", target)
        self.opener.open.assert_not_called()

    def test_redirect_never_follows_or_sends_token_to_destination(self):
        handler = lifecycle.NoRedirect()
        self.assertIsNone(handler.redirect_request(mock.Mock(), None, 302, "Found", {}, "https://other.example.com/"))
        redirect = HTTPError("https://management.azure.com" + self.path, 302, "Found", {"Location": "https://other.example.com/"}, io.BytesIO(b'{"error":{"message":"PRIVATE_ERROR_FIXTURE"}}'))
        self.opener.open.side_effect = redirect
        observed = self.transport.request("GET", self.path)
        with self.assertRaises(lifecycle.DeploymentError) as error:
            lifecycle.require_success(observed, "mock ARM operation")
        self.assertIn("302", str(error.exception))
        self.assertNotIn("PRIVATE_ERROR_FIXTURE", str(error.exception))
        self.assertEqual(self.opener.open.call_count, 1)

    def response(self, status=200, body=None):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status, response.headers = status, {}
        response.read.return_value = json.dumps(body or {}).encode()
        return response

    def http_error(self, status, headers=None, body=None):
        return HTTPError("https://management.azure.com" + self.path,
                         status, "PRIVATE_HTTP_REASON_FIXTURE", headers or {},
                         io.BytesIO(json.dumps(body or {"error": {
                             "message": "PRIVATE_HTTP_BODY_FIXTURE"}}).encode()))

    def test_transient_http_get_errors_recover_without_exposing_response_or_query(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                error = self.http_error(status)
                self.opener.open.side_effect = [error, self.response(body={"ready": True})]
                output = io.StringIO()
                with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(output):
                    observed = self.transport.request("GET", self.path + "&secret=PRIVATE_QUERY_FIXTURE")
                self.assertEqual(observed["body"], {"ready": True})
                self.assertEqual(self.opener.open.call_count, 2)
                self.assertTrue(error.fp.closed)
                sleep.assert_called_once_with(1)
                self.assertIn(f"HTTP {status}", output.getvalue())
                for secret in ("TOKEN_FIXTURE", "PRIVATE_HTTP", "PRIVATE_QUERY", "api-version"):
                    self.assertNotIn(secret, output.getvalue())

    def test_transient_http_get_exhaustion_returns_last_response_after_three_attempts(self):
        errors = [self.http_error(504), self.http_error(503),
                  self.http_error(504, {"Retry-After": "300"}, {"error": {"code": "GatewayTimeout"}})]
        self.opener.open.side_effect = errors
        with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(io.StringIO()):
            observed = self.transport.request("GET", self.path)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        self.assertEqual(observed, {"statusCode": 504, "headers": {"Retry-After": "300"},
                                   "body": {"error": {"code": "GatewayTimeout"}}})
        self.assertTrue(all(error.fp.closed for error in errors))

    def test_http_and_connection_failures_share_the_three_attempt_read_budget(self):
        self.opener.open.side_effect = [URLError(TimeoutError()), self.http_error(504), self.response()]
        with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.transport.request("GET", self.path)["statusCode"], 200)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_mutations_and_get_with_body_never_retry_transient_http_statuses(self):
        for method, body in [(method, None) for method in ("PUT", "POST", "PATCH", "DELETE")] + [("GET", {})]:
            for status in (429, 504):
                with self.subTest(method=method, body=body, status=status):
                    self.opener.open.reset_mock()
                    self.opener.open.side_effect = self.http_error(status, {"Retry-After": "10"})
                    with mock.patch.object(lifecycle.time, "sleep") as sleep:
                        observed = self.transport.request(method, self.path, body)
                    self.assertEqual(observed["statusCode"], status)
                    self.assertEqual(self.opener.open.call_count, 1)
                    sleep.assert_not_called()

    def test_authentication_and_other_non_transient_http_errors_are_not_retried(self):
        for status in (302, 400, 401, 403, 404, 409, 412, 422, 501):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = self.http_error(status, {"Retry-After": "10"})
                with mock.patch.object(lifecycle.time, "sleep") as sleep:
                    self.assertEqual(self.transport.request("GET", self.path)["statusCode"], status)
                self.assertEqual(self.opener.open.call_count, 1)
                sleep.assert_not_called()

    def test_supported_numeric_retry_after_waits_at_least_the_hint(self):
        for value, delay in (("0", 1), ("3", 3), ("0003", 3), ("10", 10), ("\t3 ", 3)):
            with self.subTest(value=value):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = [self.http_error(429, {"retry-after": value}), self.response()]
                output = io.StringIO()
                with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(output):
                    self.assertEqual(self.transport.request("GET", self.path)["statusCode"], 200)
                sleep.assert_called_once_with(delay)
                self.assertEqual(self.opener.open.call_count, 2)
                self.assertNotIn("PRIVATE_RETRY_FIXTURE", output.getvalue())

    def test_long_or_unsupported_retry_after_returns_original_response_without_retry(self):
        for value in ("11", "300", "999999999", "9" * 100,
                      "Wed, 21 Oct 2026 07:28:00 GMT", "-1", "", None, 3,
                      "PRIVATE_RETRY_FIXTURE"):
            with self.subTest(value=value):
                self.opener.open.reset_mock()
                headers = {"Retry-After": value}
                body = {"error": {"code": "TooManyRequests", "message": "PRIVATE_HTTP_BODY_FIXTURE"}}
                error = self.http_error(429, headers, body)
                self.opener.open.side_effect = error
                output = io.StringIO()
                with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(output):
                    observed = self.transport.request("GET", self.path)
                self.assertEqual(observed, {"statusCode": 429, "headers": headers, "body": body})
                self.assertEqual(self.opener.open.call_count, 1)
                self.assertTrue(error.fp.closed)
                sleep.assert_not_called()
                self.assertNotIn("PRIVATE_", output.getvalue())

    def test_small_numeric_hint_does_not_shorten_the_second_attempt_backoff(self):
        self.opener.open.side_effect = [self.http_error(504),
                                       self.http_error(429, {"Retry-After": "0"}), self.response()]
        with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.transport.request("GET", self.path)["statusCode"], 200)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_transient_read_failure_is_retried_without_repeating_a_write(self):
        """配備の開始後、状態確認の通信断ではGETだけを再試行する。"""
        self.opener.open.side_effect = [self.response(202), URLError(TimeoutError()),
            self.response(body={"properties": {"provisioningState": "Succeeded",
                "outputs": {"result": {"value": "ready"}}}})]
        with mock.patch.object(lifecycle, "run", return_value={}), \
             mock.patch.object(lifecycle, "parameters", return_value={"parameters": {}}), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=self.transport), \
             mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.deploy_phase(self.config, "monitoring"), {"result": "ready"})
        self.assertEqual([call.args[0].get_method() for call in self.opener.open.call_args_list], ["PUT", "GET", "GET"])
        sleep.assert_called_once_with(1)

    def test_read_retries_are_bounded_and_failure_does_not_expose_query_or_credentials(self):
        """失敗箇所と原因を示し、トークン・クエリー・外部エラー本文を出さない。"""
        self.opener.open.side_effect = URLError(TimeoutError("PRIVATE_ERROR_FIXTURE"))
        output = io.StringIO()
        with mock.patch.object(lifecycle.time, "sleep") as sleep, contextlib.redirect_stderr(output):
            with self.assertRaises(lifecycle.DeploymentError) as error:
                self.transport.request("GET", self.path + "&secret=PRIVATE_QUERY_FIXTURE")
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        message = str(error.exception)
        self.assertIn("ARM GET", message)
        self.assertIn(self.path.split("?", 1)[0], message)
        self.assertIn("timeout", message)
        for secret in ("TOKEN_FIXTURE", "PRIVATE_ERROR_FIXTURE", "PRIVATE_QUERY_FIXTURE", "api-version"):
            self.assertNotIn(secret, message + output.getvalue())

    def test_mutations_are_never_retried_after_connection_failure(self):
        """応答が消えても、Azureに届いた可能性のある変更要求は再送しない。"""
        for method in ("PUT", "POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = URLError(TimeoutError("PRIVATE_ERROR_FIXTURE"))
                with mock.patch.object(lifecycle.time, "sleep") as sleep:
                    with self.assertRaises(lifecycle.DeploymentError) as error:
                        self.transport.request(method, self.path)
                self.assertIn("resource state must be checked before retry", str(error.exception))
                self.assertEqual(self.opener.open.call_count, 1)
                sleep.assert_not_called()

    def test_certificate_verification_failure_stops_without_retry(self):
        self.opener.open.side_effect = URLError(ssl.SSLCertVerificationError(1, "PRIVATE_CERTIFICATE_FIXTURE"))
        with mock.patch.object(lifecycle.time, "sleep") as sleep:
            with self.assertRaises(lifecycle.DeploymentError) as error:
                self.transport.request("GET", self.path)
        self.assertIn("certificate-verification", str(error.exception))
        self.assertNotIn("PRIVATE_CERTIFICATE_FIXTURE", str(error.exception))
        self.assertEqual(self.opener.open.call_count, 1)
        sleep.assert_not_called()


class ApplicationInsightsFailureDiagnosticsTests(unittest.TestCase):
    def command(self):
        return ["/fixture/pwsh", "-NoLogo", "-NoProfile", "-File",
                str(lifecycle.ROOT / "scripts/deployment/helpers/set-app-insights-cap.ps1")]

    def test_known_daily_cap_failure_preserves_safe_stage_and_suppresses_external_output(self):
        arguments = self.command()
        safe = {"status": "failed", "stage": "readback", "code": "ApplicationInsightsDailyCapFailed"}
        completed = lifecycle.subprocess.CompletedProcess(arguments, 1, json.dumps(safe), "PRIVATE_CAP_STDERR_FIXTURE")
        with mock.patch.object(lifecycle.subprocess, "run", return_value=completed) as process:
            with self.assertRaises(lifecycle.DeploymentError) as raised:
                lifecycle.run(arguments, payload={"accessToken": "<PRIVATE_CAP_TOKEN_FIXTURE>"})
        message = str(raised.exception)
        self.assertIn("ApplicationInsightsDailyCapFailed", message)
        self.assertIn("stage=readback", message)
        self.assertNotIn("corresponding deployment", message)
        self.assertNotIn("PRIVATE_CAP", message)
        process.assert_called_once()

    def test_unverified_daily_cap_failure_uses_generic_error_without_external_values(self):
        safe = {"status": "failed", "stage": "readback", "code": "ApplicationInsightsDailyCapFailed"}
        arguments = self.command()
        other_helper = [*arguments[:-1], str(lifecycle.ROOT / "scripts/other-helper.ps1")]
        attempts = [
            (arguments, json.dumps({**safe, "stage": []})),
            (arguments, json.dumps({**safe, "accessToken": "<PRIVATE_CAP_TOKEN_FIXTURE>"})),
            (arguments, " " * 4097 + json.dumps(safe)),
            (other_helper, json.dumps(safe)),
        ]
        for command, stdout in attempts:
            with self.subTest(helper=command[-1].split("/")[-1], stdout_bytes=len(stdout)):
                completed = lifecycle.subprocess.CompletedProcess(command, 1, stdout, "PRIVATE_CAP_STDERR_FIXTURE")
                with mock.patch.object(lifecycle.subprocess, "run", return_value=completed) as process:
                    with self.assertRaises(lifecycle.DeploymentError) as raised:
                        lifecycle.run(command)
                message = str(raised.exception)
                self.assertIn("operation failed", message)
                self.assertNotIn("ApplicationInsightsDailyCapFailed", message)
                self.assertNotIn("stage=", message)
                self.assertNotIn("PRIVATE_CAP", message)
                process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
