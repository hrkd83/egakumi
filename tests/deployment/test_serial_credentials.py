"""Initial Serial credentials reuse owned instances without modifying existing VMs."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from tests import REPOSITORY

ROOT = REPOSITORY

from deployment.helpers.paths import LocalPaths
from deployment.helpers.paths import private_parent
from deployment.helpers import serial_credentials as serial
from deployment.helpers import private_permissions as permissions


def configuration():
    """Independent synthetic deployment values; not copied from an environment."""
    return {"subscriptionId": "11111111-1111-4111-8111-111111111111",
            "productName": "fixture", "environmentName": "test", "resourceStem": "test-fixture",
            "resourceGroupNames": {"maint": "rg-test-fixture-maint"},
            "management": {"adminUsername": "fixtureoperator"}}


def vm(config, vm_id="22222222-2222-4222-8222-222222222222"):
    return {"id": serial._owner(config)[0], "type": "Microsoft.Compute/virtualMachines",
            "tags": {"product": config["productName"], "environment": config["environmentName"]},
            "properties": {"vmId": vm_id}}


class InitialSerialCredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="serial-credential-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = configuration()
        self.workspace = LocalPaths(self.root, "test")
        # Behavior tests use only this isolated synthetic checkout. Windows ACL
        # semantics have separate real-DACL tests below instead of mocking SID data.
        if os.name == "nt":
            self.acl = mock.patch.object(permissions, "_windows_owner_acl")
            self.acl.start()
            self.addCleanup(self.acl.stop)
        self.output = io.StringIO()
        self.errors = io.StringIO()

    @property
    def target(self):
        return self.workspace.secrets / "management-serial-credential.json"

    def prepare(self, response=None):
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            return serial.prepare_initial_serial_password(
                self.config, self.workspace, {"statusCode": 404, "body": {}} if response is None else response)

    def load(self):
        return json.loads(self.target.read_text(encoding="utf-8"))

    def assert_no_output(self, secret=None):
        self.assertEqual(self.output.getvalue() + self.errors.getvalue(), "")
        if secret:
            self.assertNotIn(secret, self.output.getvalue() + self.errors.getvalue())
        self.assertEqual(list(self.workspace.secrets.glob(".serial-credential-*")), [])

    def test_initial_secret_has_random_password_and_reuses_pending_instance(self):
        first = self.prepare()
        receipt = self.load()
        before = self.target.read_bytes()
        with mock.patch.object(serial, "_new", side_effect=AssertionError("Retry must not rotate the password")):
            self.assertEqual(self.prepare(), first)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertTrue(serial._valid_password(first))
        self.assertEqual(receipt["adminUsername"], self.config["management"]["adminUsername"])
        self.assertEqual(receipt["targetVmResourceId"], serial._owner(self.config)[0])
        self.assertIsNone(receipt["boundVmId"])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
            for directory in (self.root / ".local", self.workspace.root, self.workspace.secrets):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assert_no_output(first)

    def test_existing_vm_skips_without_reading_or_creating_credentials(self):
        with mock.patch.object(serial, "_storage", side_effect=AssertionError("Existing VM must not access secrets")), \
             mock.patch.object(serial, "_new", side_effect=AssertionError("Existing VM must not rotate a password")):
            self.assertEqual(self.prepare({"statusCode": 200, "body": vm(self.config)}), "")
        self.assertFalse(self.workspace.root.exists())
        self.assert_no_output()

    def test_existing_vm_does_not_read_even_an_invalid_pending_file(self):
        self.prepare()
        self.target.write_text("unrelated-secret-input", encoding="utf-8")
        before = self.target.read_bytes()
        with mock.patch.object(serial, "_read", side_effect=AssertionError("Existing VM does not read the local secret")):
            self.assertEqual(self.prepare({"statusCode": 200, "body": vm(self.config)}), "")
        self.assertEqual(self.target.read_bytes(), before)

    def test_unknown_vm_response_or_foreign_ownership_cannot_create_secret(self):
        foreign = vm(self.config)
        foreign["tags"]["product"] = "other-fixture"
        for response in ({"statusCode": 403}, {"statusCode": 500}, {"statusCode": 200, "body": foreign},
                         {"statusCode": 200, "body": None}, [], {"statusCode": "404"}):
            with self.subTest(response_type=type(response).__name__), self.assertRaises(serial.SerialCredentialError):
                self.prepare(response)
            self.assertFalse(self.workspace.root.exists())

    def test_configuration_or_selected_environment_change_does_not_reuse_secret(self):
        self.prepare()
        before = self.target.read_bytes()
        self.config["management"]["adminUsername"] = "anotherfixture"
        with self.assertRaises(serial.SerialCredentialError):
            self.prepare()
        self.assertEqual(self.target.read_bytes(), before)
        self.config["environmentName"] = "other"
        with self.assertRaises(serial.SerialCredentialError):
            self.prepare()
        self.assertEqual(self.target.read_bytes(), before)

    def test_bind_success_preserves_password_and_existing_retry_still_skips(self):
        first = self.prepare()
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config), initial_password=first)
        receipt = self.load()
        self.assertEqual(receipt["password"], first)
        self.assertEqual(receipt["boundVmId"], vm(self.config)["properties"]["vmId"])
        bound = self.target.read_bytes()
        serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config), initial_password=first)
        self.assertEqual(self.target.read_bytes(), bound)
        self.assertEqual(self.prepare({"statusCode": 200, "body": vm(self.config)}), "")
        self.assertEqual(self.target.read_bytes(), bound)
        self.assert_no_output(first)

    def test_rebuild_preserves_bound_old_secret_and_uses_new_instance(self):
        first = self.prepare()
        serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config), initial_password=first)
        old = self.load()
        old_bytes = self.target.read_bytes()
        second = self.prepare()
        current = self.load()
        archive = self.workspace.secrets / ("management-serial-" + old["generation"] + ".json")
        self.assertEqual(archive.read_bytes(), old_bytes)
        self.assertNotEqual(second, first)
        self.assertNotEqual(current["generation"], old["generation"])
        self.assertIsNone(current["boundVmId"])
        self.assertEqual(self.prepare(), second)
        self.assert_no_output(first)
        self.assert_no_output(second)

    def test_bind_cannot_attach_secret_to_a_different_vm_instance(self):
        first = self.prepare()
        serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config), initial_password=first)
        before = self.target.read_bytes()
        other = vm(self.config, "33333333-3333-4333-8333-333333333333")
        with self.assertRaises(serial.SerialCredentialError):
            serial.bind_initial_serial_password(self.config, self.workspace, other, initial_password=first)
        self.assertEqual(self.target.read_bytes(), before)

    def test_binding_requires_a_real_instance_id_without_touching_credentials(self):
        first = self.prepare()
        before = self.target.read_bytes()
        for properties in (None, [], {}, {"vmId": "not-a-vm-instance"}):
            with self.subTest(properties_type=type(properties).__name__):
                with self.assertRaises(serial.SerialCredentialError):
                    serial.bind_initial_serial_password(self.config, self.workspace,
                        {**vm(self.config), "properties": properties}, initial_password=first)
                self.assertEqual(self.target.read_bytes(), before)

    def test_forged_or_unowned_archive_is_not_overwritten(self):
        first = self.prepare()
        serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config), initial_password=first)
        before = self.target.read_bytes()
        old = self.load()
        archive = self.workspace.secrets / ("management-serial-" + old["generation"] + ".json")
        wrong = {**old, "generation": "44444444-4444-4444-8444-444444444444"}
        archive.write_text(json.dumps(wrong), encoding="utf-8")
        archive.chmod(0o600)
        with self.assertRaises(serial.SerialCredentialError):
            self.prepare()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(json.loads(archive.read_text()), wrong)

    def test_missing_binding_receipt_cannot_be_inferred_from_an_existing_vm(self):
        with self.assertRaises(serial.SerialCredentialError):
            serial.bind_initial_serial_password(self.config, self.workspace, vm(self.config),
                                               initial_password=serial._new(self.config)["password"])
        self.assertFalse(self.target.exists())

    def test_shared_directory_or_reparse_point_is_rejected_before_secret_generation(self):
        local = self.root / ".local"
        local.mkdir(mode=0o700)
        if os.name == "nt":
            with mock.patch.object(permissions, "_windows_owner_acl", side_effect=serial.SerialCredentialError("Unsafe ACL")), \
                 mock.patch.object(serial, "_new", side_effect=AssertionError("Unsafe storage must precede secret generation")):
                with self.assertRaises(serial.SerialCredentialError):
                    self.prepare()
        else:
            local.chmod(0o755)
            with mock.patch.object(serial, "_new", side_effect=AssertionError("Unsafe storage must precede secret generation")):
                with self.assertRaises(serial.SerialCredentialError):
                    self.prepare()
        self.assertFalse(self.target.exists())

    @unittest.skipIf(os.name == "nt", "POSIX permission-mode check; Windows DACL tests are separate")
    def test_shared_secret_file_is_rejected_without_reading_its_contents(self):
        self.prepare()
        self.target.chmod(0o644)
        open_existing = serial._open_existing
        def refuse_secret(path, **options):
            if path == self.target:
                raise AssertionError("Unsafe mode must be checked before reading")
            return open_existing(path, **options)
        with mock.patch.object(serial, "_open_existing", side_effect=refuse_secret):
            with self.assertRaises(serial.SerialCredentialError):
                self.prepare()

    def test_external_modification_before_publication_is_preserved(self):
        self.prepare()
        old, snapshot = serial._read(self.target, self.config)
        replaced = {**old, "generation": "55555555-5555-4555-8555-555555555555"}
        self.target.write_text(json.dumps(replaced), encoding="utf-8")
        with self.assertRaises(serial.SerialCredentialError):
            serial._publish(self.target, {**old, "boundVmId": vm(self.config)["properties"]["vmId"]}, snapshot)
        self.assertEqual(self.load(), replaced)
        self.assert_no_output(old["password"])

    def test_invalid_secret_encoding_does_not_disclose_file_content(self):
        self.prepare()
        self.target.write_bytes(b"PRIVATE-SYNTHETIC-DO-NOT-DISPLAY\xff")
        with self.assertRaises(serial.SerialCredentialError) as failure:
            self.prepare()
        self.assertNotIn("PRIVATE-SYNTHETIC", str(failure.exception))
        self.assert_no_output()

    def test_competing_prepare_is_rejected_without_rotating_the_secret(self):
        first = self.prepare()
        before = self.target.read_bytes()
        with serial._locked(self.workspace.secrets):
            with self.assertRaises(serial.SerialCredentialError):
                self.prepare()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.prepare(), first)
        self.assert_no_output(first)

    def test_failed_staging_protection_does_not_create_or_publish_a_secret(self):
        self.prepare()
        before = self.target.read_bytes()
        value, snapshot = serial._read(self.target, self.config)
        with mock.patch.object(serial, "_protect_new", side_effect=serial.SerialCredentialError("Unsafe staging ACL")):
            with self.assertRaises(serial.SerialCredentialError):
                serial._publish(self.target, {**value, "boundVmId": vm(self.config)["properties"]["vmId"]}, snapshot)
        self.assertEqual(self.target.read_bytes(), before)
        self.assert_no_output(value["password"])


class WindowsACLProcessTests(unittest.TestCase):
    @contextlib.contextmanager
    def native_worker(self, process=None, *, failure=None):
        from tests.deployment.configuration.test_private_acl_worker import FakeACLProcess
        worker = permissions._WindowsACLWorker()
        process = process or FakeACLProcess()
        with mock.patch.object(permissions, "_WINDOWS_ACL_WORKER", worker), \
             mock.patch.object(permissions, "locate_powershell", return_value="pwsh"), \
             mock.patch.object(permissions, "powershell_environment", return_value={}), \
             mock.patch.object(permissions.subprocess, "Popen", **(
                 {"side_effect": failure} if failure else {"return_value": process})) as launch:
            try:
                yield process, launch
            finally:
                worker.close()

    def test_acl_process_receives_json_path_via_stdin_and_never_secret_content(self):
        target = Path(tempfile.gettempdir()) / "synthetic-serial-acl-file"
        with self.native_worker() as (process, launch):
            permissions._windows_owner_acl(target, protect_new=True)
        argv = launch.call_args.args[0]
        environment = launch.call_args.kwargs["env"]
        self.assertNotIn(str(target), argv)
        self.assertNotIn("EGAKUMI_SERIAL_ACL_TARGET", environment)
        self.assertNotIn("EGAKUMI_SERIAL_ACL_OPERATION", environment)
        self.assertEqual(process.packets, [{"requestId": 1, "path": str(target), "operation": "protect-new"}])
        self.assertEqual(base64.b64decode(argv[-1]).decode("utf-16-le"), permissions._WINDOWS_ACL_WORKER_SCRIPT)
        self.assertNotIn("password", permissions._WINDOWS_ACL_SCRIPT.lower())
        self.assertEqual(launch.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_failed_acl_check_is_not_treated_as_chmod_protection(self):
        from tests.deployment.configuration.test_private_acl_worker import FakeACLProcess
        process = FakeACLProcess(lambda request, child: "Untrusted synthetic metadata\n")
        with self.native_worker(process):
            with self.assertRaises(permissions.PrivatePermissionsError) as failure:
                permissions._windows_owner_acl(Path("synthetic-file"))
        self.assertNotIn("Untrusted synthetic metadata", str(failure.exception))

    def test_acl_timeout_malformed_response_or_launch_failure_has_a_fixed_error(self):
        from tests.deployment.configuration.test_private_acl_worker import FakeACLProcess
        failures = [
            subprocess.TimeoutExpired("synthetic-untrusted-command", 20, output="synthetic-private-output"),
            OSError("synthetic-private-launch-error"),
            subprocess.CompletedProcess([], 0, "synthetic-private-invalid-json", ""),
            subprocess.CompletedProcess([], 0, '{"ownerOnly":false}', ""),
        ]
        for result in failures:
            process = (None if isinstance(result, Exception)
                       else FakeACLProcess(lambda request, child, value=result.stdout: value + "\n"))
            with self.subTest(failure_type=type(result).__name__), self.native_worker(
                    process, failure=result if isinstance(result, Exception) else None):
                with self.assertRaises(permissions.PrivatePermissionsError) as failure:
                    permissions._windows_owner_acl(Path("synthetic-file"))
                self.assertEqual(str(failure.exception), "Private runtime storage requires owner-only Windows ACLs")

    @unittest.skipUnless(os.name == "nt", "Uses Windows native DACLs")
    def test_real_windows_file_dacl_is_owner_only_and_protected(self):
        with tempfile.TemporaryDirectory(prefix="serial-windows-acl-fixture-") as folder:
            target = Path(folder) / "empty-file"
            target.touch()
            permissions._windows_owner_acl(target, protect_new=True)
            permissions._windows_owner_acl(target)

    @unittest.skipUnless(os.name == "nt", "Uses Windows native DACLs")
    def test_real_windows_initial_secret_binding_keeps_the_password_and_acl(self):
        with tempfile.TemporaryDirectory(prefix="serial-windows-binding-fixture-") as folder:
            config = configuration()
            workspace = LocalPaths(Path(folder), "test")
            # Exercise the formal private_parent -> Serial path, including
            # real ACL inspection of directories already created by paths.
            private_parent(workspace.secrets / "synthetic-placeholder", workspace.repository)
            first = serial.prepare_initial_serial_password(config, workspace, {"statusCode": 404, "body": {}})
            serial.bind_initial_serial_password(config, workspace, vm(config), initial_password=first)
            target = workspace.secrets / "management-serial-credential.json"
            value, _ = serial._read(target, config)
            self.assertEqual(value["password"], first)
            self.assertEqual(value["boundVmId"], vm(config)["properties"]["vmId"])
            permissions._windows_owner_acl(target)
            with mock.patch.object(serial, "_storage", side_effect=AssertionError("Existing VM must not touch secrets")):
                self.assertEqual(serial.prepare_initial_serial_password(
                    config, workspace, {"statusCode": 200, "body": vm(config)}), "")


if __name__ == "__main__":
    unittest.main()
