"""Owned, single-submit time operations against synthetic ARM and guest fixtures."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from operations import management_host_time as ops
from operations.common import OperationError

NOW = datetime(2025, 1, 2, tzinfo=timezone.utc)
IMAGE = {"publisher": "Canonical", "offer": "ubuntu-24_04-lts",
         "sku": "server", "version": "24.04.202610160"}


class SyntheticContext:
    def __init__(self, root):
        self.root = root
        self.config = {"subscriptionId": str(uuid.UUID(int=1)), "azureTenantId": str(uuid.UUID(int=2)),
                       "productName": "synthetic", "environmentName": "test", "resourceStem": "test-synthetic",
                       "resourceGroupNames": {"maint": "rg-synthetic-maint"}, "location": "japaneast",
                       "tags": {}, "management": {"imageReference": IMAGE}}
        self.target = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/rg-synthetic-maint/"
                       "providers/Microsoft.Compute/virtualMachines/vm-test-synthetic-mgmt")
        self.state = {"outputs": {"managementVm": {"id": self.target, "vmId": str(uuid.UUID(int=3)),
                                                   "principalId": str(uuid.UUID(int=4))}}}
        self.config_path, self.state_path = root / "params.local.json", root / "latest.json"
        root.chmod(0o700)
        for path, value in ((self.config_path, self.config), (self.state_path, self.state)):
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        self.evidence = root / "explicit-evidence"
        self.evidence.mkdir(mode=0o700)
        self.locked_now = False
        self.lock_entries = 0
        self.on_save = None
        self.transport = SyntheticArm(self)

    @contextmanager
    def locked(self):
        self.lock_entries += 1
        self.locked_now = True
        try:
            yield
        finally:
            self.locked_now = False

    def _resource_path(self, value):
        if not value.startswith(self.target):
            raise OperationError("Synthetic target scope differs")
        return value

    def request(self, method, target, api, body=None, headers=None):
        return self.transport.request(method, target + "?api-version=" + api, body=body, headers=headers)

    def verify_resource(self, target, api, kind):
        reply = self.request("GET", target, api)
        if reply.get("statusCode") != 200:
            raise OperationError("Synthetic VM read failed")
        return reply["body"]

    def output_path(self, path):
        path = Path(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise OperationError("Synthetic evidence escaped its selected root")
        return path

    def save_private(self, path, value, overwrite=False):
        path = self.output_path(path)
        if path.exists() and not overwrite:
            raise OperationError("Synthetic evidence already exists")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        if self.on_save:
            self.on_save(path)
        return path


class SyntheticArm:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []
        self.commands = {}
        self.raise_after_put = False
        self.pending = False
        self.retry_after = None
        self.override_output = None
        self.change_vm_after_put = False
        self.fingerprint = "c" * 64
        self.vm = {"id": ctx.target, "type": "Microsoft.Compute/virtualMachines", "location": "japaneast",
                   "tags": {"product": "synthetic", "environment": "test"},
                   "identity": {"type": "SystemAssigned", "principalId": str(uuid.UUID(int=4)),
                                "tenantId": str(uuid.UUID(int=2))},
                   "properties": {"provisioningState": "Succeeded", "vmId": str(uuid.UUID(int=3)),
                                  "timeCreated": "2025-01-01T00:00:00Z",
                                  "storageProfile": {"imageReference": IMAGE}}}

    def proof(self, target):
        if target.endswith("/configure-host-time"):
            return {"status": "configured", "timeService": "chrony.service", "timeSource": "azure-host-phc",
                    "timeSynchronized": True, "timeOnly": True}
        stage = "verify" if "/management-host-time-verify-" in target else "inspect"
        return {"status": "observed", "stage": stage, "observedAtUtc": "2025-01-02T00:00:01Z",
                "configurationKnown": True, "hostConfigurationCurrent": stage == "verify",
                "phcVerified": True, "timeService": "chrony.service", "currentTimeValid": stage == "verify",
                "phcSelected": stage == "verify", "leapNormal": True, "referenceVerified": stage == "verify",
                "systemCorrectionSeconds": 0.001,
                "fingerprints": {"ssh": {"present": True, "permissions": 0o644, "sha256": self.fingerprint},
                                 "rsyslog": {"present": False}}}

    def command(self, target, expected):
        output = json.dumps(self.proof(target)) + "\n" if self.override_output is None else self.override_output
        return {"id": target, "type": "Microsoft.Compute/virtualMachines/runCommands",
                **deepcopy(expected), "properties": {**deepcopy(expected["properties"]),
                    "provisioningState": "Succeeded", "instanceView": {
                        "executionState": "Running" if self.pending else "Succeeded", "exitCode": 0,
                        "startTime": "2025-01-02T00:00:01Z", "endTime": "2025-01-02T00:00:02Z",
                        "output": output, "error": ""}}}

    def request(self, method, path, body=None, headers=None):
        if not self.ctx.locked_now:
            raise AssertionError("Every synthetic ARM operation must hold the common environment lock")
        target = path.split("?", 1)[0]
        self.calls.append((method, target, deepcopy(body)))
        if method == "GET" and target == self.ctx.target:
            if self.change_vm_after_put and any(call[0] == "PUT" for call in self.calls):
                self.vm["properties"]["vmId"] = str(uuid.UUID(int=99))
            return {"statusCode": 200, "body": deepcopy(self.vm)}
        if method == "GET":
            if target not in self.commands:
                return {"statusCode": 404}
            reply = {"statusCode": 200, "body": deepcopy(self.commands[target])}
            if self.retry_after:
                reply["headers"] = {"Retry-After": self.retry_after}
            return reply
        if method != "PUT" or "/runCommands/" not in target:
            raise AssertionError("Only a single selected command PUT is permitted")
        stage = "apply" if target.endswith("/configure-host-time") else (
            "verify" if "/management-host-time-verify-" in target else "inspect")
        receipt = json.loads((self.ctx.evidence / (stage + "-receipt.json")).read_text())
        if receipt["status"] != "submission-requested" or receipt["commandId"] != target or receipt["expected"] != body:
            raise AssertionError("A matching protected write-ahead receipt must precede PUT")
        self.commands[target] = self.command(target, body)
        if self.raise_after_put:
            self.raise_after_put = False
            raise TimeoutError("synthetic response lost")
        return {"statusCode": 201, "body": deepcopy(self.commands[target])}


class ManagementHostTimeOperationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="core-time-operations-")
        self.root = Path(self.folder.name)
        self.ctx = SyntheticContext(self.root)
        self.arm = self.ctx.transport
        self.inputs = self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()

    def tearDown(self):
        self.folder.cleanup()

    def invoke(self, stage, execute=False, **options):
        with patch.object(ops, "load_configuration", return_value=deepcopy(self.ctx.config)):
            return ops.perform(self.ctx, stage, self.ctx.evidence, execute=execute, now=lambda: NOW, **options)

    def puts(self):
        return [call for call in self.arm.calls if call[0] == "PUT"]

    def inspect(self):
        self.assertEqual(self.invoke("inspect", True)["status"], "verified")

    def legacy_core(self):
        del self.ctx.state["outputs"]["managementVm"]["vmId"]
        self.ctx.state_path.write_text(json.dumps(self.ctx.state))
        self.inputs = self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()

    def install_current_host(self):
        binding = {"id": self.ctx.target, "location": "japaneast"}
        expected = ops.expected_command(self.ctx, binding, ops.host_time_script())
        target = self.ctx.target + "/runCommands/configure-host-time"
        self.arm.commands[target] = self.arm.command(target, expected)
        return self.arm.commands[target]

    def test_default_plans_hold_the_lock_and_do_not_submit_guest_commands(self):
        for stage in ("inspect", "apply", "verify"):
            self.assertEqual(self.invoke(stage)["status"], "planned")
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.ctx.lock_entries, 3)
        self.assertEqual(self.inputs, (self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()))

    def test_inspect_apply_verify_each_submit_only_their_command_and_preserve_inputs(self):
        self.inspect()
        self.assertTrue(self.invoke("apply", True)["applied"])
        self.assertTrue(self.invoke("verify", True)["currentClockMeasured"])
        self.assertEqual(len(self.puts()), 3)
        self.assertEqual(self.puts()[1][1], self.ctx.target + "/runCommands/configure-host-time")
        self.assertEqual(self.puts()[1][2]["properties"]["source"]["script"], ops.host_time_script())
        self.assertTrue(all("/runCommands/" in target for _, target, _ in self.puts()))
        self.assertFalse(any("DELETE" == method for method, _, _ in self.arm.calls))
        self.assertEqual(self.inputs, (self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()))

    def test_current_canonical_success_is_zero_write_and_never_claims_current_precision(self):
        self.install_current_host()
        result = self.invoke("apply", True)
        self.assertFalse(result["applied"])
        self.assertFalse(result["currentClockMeasured"])
        self.assertEqual(self.puts(), [])

    def test_legacy_core_without_uuid_freezes_inspection_binding_without_updating_core(self):
        self.legacy_core()
        self.inspect()
        receipt = json.loads((self.ctx.evidence / "inspect-receipt.json").read_text())
        self.assertEqual(receipt["binding"]["vmUuid"], self.arm.vm["properties"]["vmId"])
        self.assertEqual(receipt["binding"]["timeCreated"], self.arm.vm["properties"]["timeCreated"])
        self.assertTrue(self.invoke("apply", True)["applied"])
        self.assertTrue(self.invoke("verify", True)["currentClockMeasured"])
        self.assertNotIn("vmId", self.ctx.state["outputs"]["managementVm"])
        self.assertEqual(self.inputs, (self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()))

    def test_legacy_apply_and_verify_require_the_same_owned_inspection_first(self):
        self.legacy_core()
        for stage in ("apply", "verify"):
            with self.subTest(stage=stage), self.assertRaises(OperationError):
                self.invoke(stage, True)
        self.install_current_host()
        with self.assertRaises(OperationError):
            self.invoke("apply", True)
        self.assertEqual(self.puts(), [])

    def test_legacy_generation_change_cannot_replace_inspection_or_submit_another_stage(self):
        self.legacy_core()
        self.inspect()
        receipt = (self.ctx.evidence / "inspect-receipt.json").read_bytes()
        result = (self.ctx.evidence / "inspect-result.json").read_bytes()
        self.arm.vm["properties"]["vmId"] = str(uuid.UUID(int=99))
        self.arm.vm["properties"]["timeCreated"] = "2025-01-01T12:00:00Z"
        self.install_current_host()  # A fresh canonical success must not bypass the old inspection binding.
        for stage in ("inspect", "apply", "verify"):
            with self.subTest(stage=stage), self.assertRaises(OperationError):
                self.invoke(stage, True)
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual((self.ctx.evidence / "inspect-receipt.json").read_bytes(), receipt)
        self.assertEqual((self.ctx.evidence / "inspect-result.json").read_bytes(), result)
        self.assertEqual(self.inputs, (self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()))

    def test_present_core_uuid_must_be_valid_nonzero_and_match_the_live_vm(self):
        for value in (None, False, 1, "", "invalid", "00000000-0000-0000-0000-000000000000", str(uuid.UUID(int=99))):
            with self.subTest(value=value):
                self.ctx.state["outputs"]["managementVm"]["vmId"] = value
                self.ctx.state_path.write_text(json.dumps(self.ctx.state))
                with self.assertRaises(OperationError):
                    self.invoke("inspect", True)
        self.assertEqual(self.puts(), [])

    def test_standard_identity_type_whitespace_preserves_system_assigned_requirement(self):
        for value in ("SystemAssigned, UserAssigned", "UserAssigned , SystemAssigned", " SystemAssigned , UserAssigned "):
            with self.subTest(value=value):
                self.arm.vm["identity"]["type"] = value
                with self.ctx.locked():
                    _, binding = ops.owned_vm(self.ctx)
                self.assertEqual(binding["principalId"], self.ctx.state["outputs"]["managementVm"]["principalId"])
        self.arm.vm["identity"]["type"] = "UserAssigned"
        with self.ctx.locked(), self.assertRaises(OperationError):
            ops.owned_vm(self.ctx)
        self.assertEqual(self.puts(), [])

    def test_apply_requires_original_owned_inspection_not_a_standalone_success_json(self):
        self.ctx.save_private(self.ctx.evidence / "inspect-result.json", {"configurationKnown": True})
        with self.assertRaises(OperationError):
            self.invoke("apply", True)
        self.assertEqual(self.puts(), [])

    def test_existing_running_unknown_source_or_settings_stop_before_any_submission(self):
        for mutate in (
            lambda body: body["properties"]["instanceView"].update(executionState="Running"),
            lambda body: body["properties"]["source"].update(script="#!/bin/bash\n# synthetic other source\n"),
            lambda body: body["properties"].update(timeoutInSeconds=1),
            lambda body: body["properties"].update(protectedParameters=[{"name": "unexpected"}]),
            lambda body: body.update(tags={}),
        ):
            with self.subTest(mutate=mutate):
                body = self.install_current_host()
                mutate(body)
                with self.assertRaises(OperationError):
                    self.invoke("apply", True)
        self.assertEqual(self.puts(), [])

    def test_old_full_audit_is_not_rewritten_and_running_full_audit_blocks_inspection(self):
        target = self.ctx.target + "/runCommands/configure-linux-audit"
        body = {"id": target, "type": "Microsoft.Compute/virtualMachines/runCommands",
                "properties": {"source": {"script": "synthetic legacy external NTP material"},
                               "instanceView": {"executionState": "Succeeded"}}}
        self.arm.commands[target] = deepcopy(body)
        self.inspect()
        self.assertEqual(self.arm.commands[target], body)
        self.assertTrue(all(call[1] != target for call in self.puts()))
        self.arm.commands[target]["properties"]["instanceView"]["executionState"] = "Running"
        with self.assertRaises(OperationError):
            self.invoke("apply", True)
        self.assertEqual(len(self.puts()), 1)

    def test_lost_put_response_is_saved_and_resume_only_reads_the_recorded_target(self):
        self.arm.raise_after_put = True
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.assertEqual(len(self.puts()), 1)
        saved = json.loads((self.ctx.evidence / "inspect-put-response.json").read_text())
        self.assertEqual(saved["transportException"], "TimeoutError")
        result = self.invoke("inspect", True)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["guestReadCreated"])
        self.assertEqual(len(self.puts()), 1)

    def test_missing_recorded_target_never_causes_a_repeat_put(self):
        self.arm.raise_after_put = True
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.arm.commands.clear()
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.assertEqual(len(self.puts()), 1)

    def test_completed_readonly_receipt_is_rechecked_without_claiming_a_new_measurement(self):
        self.inspect()
        original = (self.ctx.evidence / "inspect-result.json").read_bytes()
        result = self.invoke("inspect", True)
        self.assertIs(result["currentClockMeasured"], False)
        self.assertIs(result["recordedObservationVerified"], True)
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual((self.ctx.evidence / "inspect-result.json").read_bytes(), original)

    def test_pending_command_uses_bounded_polling_without_shortening_retry_after(self):
        self.arm.pending = True
        self.arm.retry_after = "600"
        slept = []
        with self.assertRaises(OperationError):
            self.invoke("inspect", True, timeout=10, sleep=slept.append)
        self.assertEqual(slept, [])
        self.assertEqual(len(self.puts()), 1)
        with self.assertRaises(OperationError):
            self.invoke("inspect", True, timeout=10, sleep=slept.append)
        self.assertEqual(len(self.puts()), 1)

    def test_bool_exit_code_duplicate_or_truncated_output_is_not_success(self):
        for output in ("{}\n{}\n", "x" * 4000, json.dumps({"status": "observed", "stage": "inspect"})):
            with self.subTest(output_length=len(output)):
                self.arm.commands.clear()
                for path in self.ctx.evidence.glob("*"):
                    path.unlink()
                self.arm.override_output = output
                with self.assertRaises(OperationError):
                    self.invoke("inspect", True)
        self.arm.override_output = None
        self.arm.commands.clear()
        for path in self.ctx.evidence.glob("*"):
            path.unlink()
        self.arm.raise_after_put = True
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        body = next(iter(self.arm.commands.values()))
        body["properties"]["instanceView"]["exitCode"] = False
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)

    def test_changed_vm_generation_is_not_accepted_after_a_guest_command(self):
        self.arm.change_vm_after_put = True
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual(self.inputs, (self.ctx.config_path.read_bytes(), self.ctx.state_path.read_bytes()))

    def test_image_or_state_uuid_mismatch_stops_before_any_write(self):
        self.arm.vm["properties"]["storageProfile"]["imageReference"] = {**IMAGE, "publisher": "other"}
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.arm.vm["properties"]["storageProfile"]["imageReference"] = IMAGE
        self.arm.vm["properties"]["vmId"] = str(uuid.UUID(int=8))
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.assertEqual(self.puts(), [])

    def test_input_change_after_write_ahead_save_prevents_put(self):
        def change(path):
            if path.name == "inspect-receipt.json":
                self.ctx.config_path.write_bytes(self.ctx.config_path.read_bytes() + b"\n")
        self.ctx.on_save = change
        with self.assertRaises(OperationError):
            self.invoke("inspect", True)
        self.assertEqual(self.puts(), [])

    def test_verify_preserves_evidence_and_rejects_changed_ssh_fingerprint(self):
        self.inspect()
        self.invoke("apply", True)
        self.arm.fingerprint = "d" * 64
        with self.assertRaises(OperationError):
            self.invoke("verify", True)
        self.assertTrue((self.ctx.evidence / "verify-observed-command.json").is_file())
        self.assertFalse((self.ctx.evidence / "verify-result.json").exists())

    def test_verify_proof_requires_finite_small_correction_and_unambiguous_json(self):
        binding = {"id": self.ctx.target, "location": "japaneast", "timeCreated": "2025-01-01T00:00:00Z"}
        target = self.ctx.target + "/runCommands/management-host-time-verify-" + "a" * 32
        expected = ops.expected_command(self.ctx, binding, ops.readonly_script("verify"), temporary=True)
        body = self.arm.command(target, expected)
        proof = self.arm.proof(target)
        invalid = [json.dumps({**proof, "systemCorrectionSeconds": value})
                   for value in (True, None, -0.1, 0.101, float("nan"), float("inf"))]
        invalid.append(json.dumps(proof).replace('"configurationKnown": true',
                                                '"configurationKnown": false, "configurationKnown": true'))
        for output in invalid:
            with self.subTest(output=output):
                properties = deepcopy(body["properties"])
                properties["instanceView"]["output"] = output + "\n"
                with self.assertRaises(OperationError):
                    ops.observed_proof(properties, binding, "verify", NOW.isoformat())

    def test_only_exact_permanent_and_recorded_temporary_ids_can_use_expanded_get(self):
        binding = {"id": self.ctx.target}
        before = len(self.arm.calls)
        with self.assertRaises(OperationError):
            ops.expanded(self.ctx, binding, self.ctx.target + "/extensions/unrelated")
        with self.assertRaises(OperationError):
            ops.expanded(self.ctx, binding, self.ctx.target + "/runCommands/unrelated",
                         temporary=self.ctx.target + "/runCommands/unrelated")
        self.assertEqual(len(self.arm.calls), before)

    def test_cli_forwards_explicit_runtime_and_prints_only_stage_status_booleans(self):
        output = io.StringIO()
        args = ["--config", str(self.ctx.config_path), "--runtime-root", str(self.root),
                "--stage", "inspect", "--evidence-dir", str(self.ctx.evidence)]
        with patch.object(ops.OperationContext, "load", return_value=self.ctx) as load, \
                patch.object(ops, "load_configuration", return_value=deepcopy(self.ctx.config)), redirect_stdout(output):
            self.assertEqual(ops.main(args), 0)
        load.assert_called_once_with(str(self.ctx.config_path), runtime_root=str(self.root))
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["stage"], "inspect")
        self.assertTrue(all(type(value) is bool for key, value in summary.items() if key not in ("stage", "status")))
        self.assertNotIn(self.ctx.target, output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual(self.puts(), [])


class ReadOnlyGuestFixtureTests(unittest.TestCase):
    def test_actual_readonly_script_checks_fixture_without_any_configuration_writes(self):
        # Share only the fixture utilities, not an imported TestCase in module globals.
        from test_management_linux import LinuxTimeConfigurationTests
        LinuxTimeConfigurationTests.setUpClass()
        helper = LinuxTimeConfigurationTests()
        with tempfile.TemporaryDirectory(prefix="core-time-readonly-guest-") as folder:
            root = Path(folder)
            paths, _ = helper.fixture(root)
            device = root / "device"
            device.symlink_to("/dev/null")
            (root / "ptp/ptp7").mkdir(parents=True)
            (root / "ptp/ptp7/clock_name").write_text("hyperv\n")
            ssh, rsyslog = root / "ssh", root / "rsyslog"
            ssh.mkdir()
            rsyslog.mkdir()
            (ssh / "00-core-access.conf").write_text("synthetic untouched SSH")
            (rsyslog / "00-core-command-redaction.conf").write_text("synthetic untouched syslog")
            script = ops.readonly_script("inspect")
            replacements = [("/etc/chrony/chrony.conf", paths[0]), ("/etc/chrony/conf.d", paths[1]),
                            ("/etc/chrony/sources.d", paths[2]), ("/run/chrony-dhcp", paths[3]),
                            ("/etc/systemd/timesyncd.conf.d/90-core-platform.conf", paths[4]),
                            ("/run/core-management-time", paths[5]), ("/etc/ssh/sshd_config.d", ssh),
                            ("/etc/rsyslog.d", rsyslog)]
            for actual, fixture in replacements:
                script = script.replace(actual, str(fixture))
            script = script.replace("validate_host_clock /dev/ptp_hyperv /sys/class/ptp",
                                    f"validate_host_clock {device} {root / 'ptp'}")
            stubs = r'''
id() { printf '0\n'; }
uname() { printf 'Linux\n'; }
readlink() { printf '/dev/ptp7\n'; }
systemctl() { if [[ $1 == show ]]; then printf 'loaded\n'; else return 0; fi; }
chronyd() { return 0; }
chronyc() {
    if [[ $1 == -c ]]; then printf '#,*,PHC0,2,3,377,1,0,0,0\n'
    else printf 'Reference ID    : 50484330 (PHC0)\nSystem time     : 0.001 seconds slow of NTP time\nLeap status     : Normal\n'; fi
}
'''
            before = {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                      for path in root.rglob("*") if path.is_file() and not path.is_symlink()}
            result = subprocess.run(["bash", "-c", stubs + script], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            proof = json.loads(result.stdout)
            self.assertIs(proof["configurationKnown"], True)
            self.assertIs(proof["currentTimeValid"], False)
            self.assertNotIn("synthetic untouched", result.stdout)
            self.assertEqual(proof["fingerprints"]["ssh"]["sha256"], hashlib.sha256(b"synthetic untouched SSH").hexdigest())
            after = {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                     for path in root.rglob("*") if path.is_file() and not path.is_symlink()}
            self.assertEqual(after, before)
            self.assertFalse(paths[-1].exists())

            # 先行移行後の実verify sourceも、同じ共有検査を使って読取だけで成功する。
            self.assertEqual(helper.transaction("prepare", paths).returncode, 0)
            self.assertEqual(helper.transaction("commit", paths).returncode, 0)
            verified_script = ops.readonly_script("verify")
            for actual, fixture in replacements:
                verified_script = verified_script.replace(actual, str(fixture))
            verified_script = verified_script.replace("validate_host_clock /dev/ptp_hyperv /sys/class/ptp",
                                                      f"validate_host_clock {device} {root / 'ptp'}")
            verified_stubs = stubs.replace("PHC0", "AZUR").replace("50484330", "415A5552")
            before = {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                      for path in root.rglob("*") if path.is_file() and not path.is_symlink()}
            result = subprocess.run(["bash", "-c", verified_stubs + verified_script],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            proof = json.loads(result.stdout)
            self.assertIs(proof["currentTimeValid"], True)
            self.assertIs(proof["phcSelected"], True)
            self.assertIs(proof["leapNormal"], True)
            self.assertIs(proof["referenceVerified"], True)
            self.assertEqual({str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                              for path in root.rglob("*") if path.is_file() and not path.is_symlink()}, before)


if __name__ == "__main__":
    unittest.main()
