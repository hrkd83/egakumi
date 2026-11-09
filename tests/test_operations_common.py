"""保守の所有境界、秘密保存、排他、応答不明時の安全停止を確認する。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup_fixtures import CleanupFixture, FakeTransport
from deployment.helpers.paths import LocalPaths
from operations.common import OperationContext, OperationError, poll_delay


class OperationsCommonTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.workspace = LocalPaths(Path(self.temp.name), "test")
        self.workspace.ensure()
        for path, content in ((self.workspace.params, self.raw),
                              (self.workspace.state / "latest.json", self.state)):
            path.write_text(json.dumps(content))
            path.chmod(0o600)
        self.ctx = OperationContext(self.workspace.params, self.configuration,
                                    self.state, self.workspace)

    def test_load_requires_matching_private_inputs_without_authentication(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("operations.common.local_paths", return_value=self.workspace), \
                mock.patch("operations.common.ArmTransport") as transport:
            context = OperationContext.load(self.workspace.params)
        self.assertEqual(context.state["apimInstanceId"], "abc123")
        transport.assert_not_called()

    def test_mismatched_state_and_public_inputs_stop_before_authentication(self):
        for bad in ("state", "public", "permissions"):
            with self.subTest(bad=bad):
                state = copy.deepcopy(self.state)
                state["apimInstanceId"] = "other1" if bad == "state" else "abc123"
                self.ctx.save_private(self.ctx.state_path, state, overwrite=True)
                self.workspace.params.chmod(0o644 if bad == "permissions" else 0o600)
                with mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch("operations.common.local_paths", return_value=self.workspace), \
                        mock.patch("operations.common.ArmTransport") as transport:
                    with self.assertRaises(OperationError):
                        OperationContext.load(self.config_path if bad == "public" else self.workspace.params)
                    transport.assert_not_called()

    def test_retired_instance_stops_before_operations(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("operations.common.local_paths", return_value=self.workspace), \
                mock.patch("operations.common.assert_current_instance", side_effect=ValueError("retired")), \
                mock.patch("operations.common.ArmTransport") as transport:
            with self.assertRaises(OperationError):
                OperationContext.load(self.workspace.params)
            transport.assert_not_called()

    def test_text_and_json_are_private_and_never_overwrite_existing_by_default(self):
        text_path = self.workspace.secrets / "operation-value.txt"
        record = self.workspace.state / "operations/result.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.ctx.save_private(text_path, "SYNTHETIC_PRIVATE_VALUE\n")
            self.ctx.save_private(record, {"status": "planned"})
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(text_path.read_text(), "SYNTHETIC_PRIVATE_VALUE\n")
        for path in (text_path, record):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        with self.assertRaises(OperationError):
            self.ctx.save_private(text_path, "replacement")
        self.assertEqual(text_path.read_text(), "SYNTHETIC_PRIVATE_VALUE\n")

    def test_outputs_cannot_escape_the_environment_or_follow_links(self):
        target = self.workspace.secrets / "linked.txt"
        public = Path(self.temp.name) / "public.txt"
        public.write_text("original")
        target.symlink_to(public)
        for path in (public, target, self.workspace.root.parent / "other/value.json"):
            with self.subTest(path=path), self.assertRaises(OperationError):
                self.ctx.save_private(path, "replacement")
        self.assertEqual(public.read_text(), "original")

    def test_failed_atomic_save_leaves_no_partial_value(self):
        target = self.workspace.secrets / "new.txt"
        with mock.patch("operations.common.os.link", side_effect=OSError("fixture")):
            with self.assertRaises(OperationError):
                self.ctx.save_private(target, "SYNTHETIC_PRIVATE_VALUE")
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(".operation-*")), [])

    def test_record_rejects_secret_fields_and_keeps_multiple_operation_results(self):
        for field in ("secretText", "providerSecrets", "primaryKey", "accessToken"):
            with self.assertRaises(OperationError):
                self.ctx.record("sample", {"nested": [{field: "private"}]})
        first = self.ctx.record("sample", {"status": "planned"})
        second = self.ctx.record("sample", {"status": "complete"})
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_other_subscription_group_url_or_query_is_rejected_before_transport(self):
        resource = self.state["serviceResourceIds"]["apim"]
        self.ctx.transport = mock.Mock()
        for invalid in (resource.replace(self.configuration["subscriptionId"], "other"),
                        resource.replace(self.configuration["resourceGroupNames"]["svc"], "other"),
                        "https://management.azure.com" + resource, resource + "?other=1",
                        resource + "/../other", resource + "/%2Fother"):
            with self.subTest(path=invalid), self.assertRaises(OperationError):
                self.ctx.request("DELETE", invalid, "2024-05-01")
        self.ctx.transport.request.assert_not_called()

    def test_resource_verification_requires_owned_group_and_exact_identity(self):
        resource = self.state["serviceResourceIds"]["apim"]
        group = self.svc_rg
        valid = {"id": resource, "type": "Microsoft.ApiManagement/service"}
        tags = {"product": self.configuration["productName"], "environment": "test"}
        for failure in (None, "group", "resource", "type"):
            transport = FakeTransport({group: {"id": group, "tags": dict(tags)}, resource: dict(valid)})
            if failure == "group": transport.resources[group]["tags"]["environment"] = "other"
            if failure == "resource": transport.resources[resource]["id"] += "-other"
            if failure == "type": transport.resources[resource]["type"] = "other"
            self.ctx.transport = transport
            if failure:
                with self.assertRaises(OperationError):
                    self.ctx.verify_resource(resource, "2024-05-01", valid["type"])
            else:
                self.assertEqual(self.ctx.verify_resource(resource, "2024-05-01", valid["type"]), valid)
            self.assertTrue(all(call["method"] == "GET" for call in transport.calls))

    def test_same_environment_operation_lock_is_exclusive_and_recovers(self):
        with self.ctx.locked():
            with self.assertRaises(OperationError):
                with self.ctx.locked():
                    self.fail("A second operation acquired the same lock")
        with self.ctx.locked():
            pass

    def test_polling_respects_numeric_hints_and_rejects_ambiguous_values(self):
        self.assertEqual(poll_delay({}), 5)
        self.assertEqual(poll_delay({"headers": {"retry-after": "120"}}), 120)
        self.assertEqual(poll_delay({"headers": {"Retry-After": "0003"}}), 5)
        self.assertEqual(poll_delay({"headers": {"Retry-After": "9" * 5000}}), float("inf"))
        for headers in ({"Retry-After": "-1"}, {"Retry-After": "invalid"},
                        {"Retry-After": "3", "retry-after": "3"}, {"Retry-After": None}):
            with self.subTest(headers=headers), self.assertRaises(OperationError):
                poll_delay({"headers": headers})

    def test_cli_never_prints_or_replays_failed_or_timed_out_secret_output(self):
        for result in (subprocess.CompletedProcess(["fixture"], 1, "PRIVATE_STDOUT", "PRIVATE_STDERR"),
                       subprocess.TimeoutExpired(["fixture"], 1, output="PRIVATE_STDOUT")):
            with self.subTest(result=type(result).__name__):
                output = io.StringIO()
                with mock.patch("operations.common.subprocess.run", side_effect=result if isinstance(result, Exception) else None,
                                return_value=result) as run, \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    with self.assertRaises(OperationError) as caught:
                        self.ctx.run_cli(["fixture"])
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(run.call_count, 1)

    def test_cli_disables_internal_response_file_logging_for_all_children(self):
        completed = subprocess.CompletedProcess(["fixture"], 0, "PRIVATE_RESPONSE", "")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"AZURE_LOGGING_ENABLE_LOG_FILE": "true"}), \
                mock.patch("operations.common.subprocess.run", return_value=completed) as run, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(self.ctx.run_cli(["fixture"]), "PRIVATE_RESPONSE")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(run.call_args.kwargs["env"]["AZURE_LOGGING_ENABLE_LOG_FILE"], "false")
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_arm_authentication_cli_inherits_file_log_suppression(self):
        transport = mock.Mock()
        resource = self.state["serviceResourceIds"]["apim"]
        with mock.patch.dict(os.environ, {"AZURE_LOGGING_ENABLE_LOG_FILE": "true"}), \
                mock.patch("operations.common.ArmTransport", return_value=transport) as factory:
            self.ctx.request("GET", resource, "2024-05-01")
            self.assertEqual(os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"], "false")
            factory.assert_called_once_with(self.ctx.config)


if __name__ == "__main__":
    unittest.main()
