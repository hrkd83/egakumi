"""Independent synthetic transition tests; no guest or Azure operations."""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest

from contextlib import redirect_stdout
import io
import os
import types
from unittest import mock
from tests import REPOSITORY
ROOT = REPOSITORY
from deployment.helpers import serial_initialization as initial
_spec = importlib.util.spec_from_file_location("management_serial_guest", ROOT / "infra/guest/initialize-management-serial.py")
draft = importlib.util.module_from_spec(_spec)
with mock.patch.dict(sys.modules, {"pwd": types.SimpleNamespace()} if os.name == "nt" else {}):
    _spec.loader.exec_module(draft)
def completion_matches(proof, expected):
    return initial.completion({"statusCode":200,"body":{"properties":{"instanceView":{"executionState":"Succeeded","exitCode":0,"output":json.dumps(proof)}}}},expected) is not None
import json
draft.completion_matches=completion_matches

OWNER = {"resourceId": "/subscriptions/00000000-0000-4000-8000-000000000000/resourceGroups/synthetic-maint/providers/Microsoft.Compute/virtualMachines/synthetic-vm",
         "vmId": "11111111-1111-4111-8111-111111111111", "principalId": "22222222-2222-4222-8222-222222222222",
         "tenantId": "33333333-3333-4333-8333-333333333333", "adminUsername": "syntheticadmin",
         "credentialGeneration": "44444444-4444-4444-8444-444444444444", "source": draft.SOURCE}
PASSWORD = "synthetic-only-Original1!" * 2


class Guest:
    def __init__(self):
        self.receipt = None
        self.actual = copy.deepcopy(OWNER)
        self.unconfigured = True
        self.writes = []
        self.auths = []
        self.current_password = None
        self.persist_count = 0
        self.fail_persist_at = None
        self.fail_write = False
        self.auth_result = None
        self.observations = 0
        self.change_observation_at = None

    def observe(self):
        self.observations += 1
        if self.change_observation_at == self.observations:
            return {**self.actual, "vmId": "55555555-5555-4555-8555-555555555555"}
        return copy.deepcopy(self.actual)

    def persist(self, value):
        self.persist_count += 1
        if self.persist_count == self.fail_persist_at:
            raise OSError("Synthetic durable publication failure")
        self.receipt = copy.deepcopy(value)

    def write(self, username, password):
        self.writes.append((username, password))
        self.current_password = password
        self.unconfigured = False
        if self.fail_write:
            raise RuntimeError("Synthetic unknown write response")

    def authenticate(self, username, password):
        self.auths.append((username, password))
        return password == self.current_password if self.auth_result is None else self.auth_result

    def run(self, owner=None):
        return draft.initialize(owner or OWNER, PASSWORD, observe=self.observe,
            read=lambda: copy.deepcopy(self.receipt), persist=self.persist,
            pristine=lambda: self.unconfigured, write_password=self.write, authenticate=self.authenticate)


class Transitions(unittest.TestCase):
    def test_original_creation_verifies_and_completes(self):
        guest = Guest()
        result = guest.run()
        self.assertTrue(draft.completion_matches(result, OWNER))
        self.assertEqual(len(guest.writes), 1)
        self.assertEqual(len(guest.auths), 1)
        self.assertNotIn("password", result)

    def test_request_persist_failure_never_writes_or_authenticates(self):
        guest = Guest()
        guest.fail_persist_at = 1
        with self.assertRaises(OSError): guest.run()
        self.assertEqual((guest.writes, guest.auths, guest.receipt), ([], [], None))

    def test_write_unknown_recovers_without_password_rewrite(self):
        guest = Guest()
        guest.fail_write = True
        with self.assertRaises(RuntimeError): guest.run()
        self.assertEqual(guest.receipt["status"], "requested")
        guest.fail_write = False
        result = guest.run()
        self.assertTrue(draft.completion_matches(result, OWNER))
        self.assertEqual(len(guest.writes), 1)
        self.assertEqual(len(guest.auths), 1)

    def test_verification_persist_failure_can_only_recover_original_password(self):
        guest = Guest()
        guest.fail_persist_at = 2
        with self.assertRaises(OSError): guest.run()
        self.assertFalse(guest.receipt["verificationAttempted"])
        guest.fail_persist_at = None
        self.assertTrue(draft.completion_matches(guest.run(), OWNER))
        self.assertEqual((len(guest.writes), len(guest.auths)), (1, 1))

    def test_completion_persist_failure_cannot_repeat_authentication(self):
        guest = Guest()
        guest.fail_persist_at = 3
        with self.assertRaises(OSError): guest.run()
        self.assertTrue(guest.receipt["verificationAttempted"])
        guest.fail_persist_at = None
        with self.assertRaises(draft.Blocked): guest.run()
        self.assertEqual((len(guest.writes), len(guest.auths)), (1, 1))

    def test_failed_authentication_never_retries(self):
        guest = Guest()
        guest.auth_result = False
        with self.assertRaises(draft.Blocked): guest.run()
        with self.assertRaises(draft.Blocked): guest.run()
        self.assertEqual((len(guest.writes), len(guest.auths)), (1, 1))

    def test_pending_without_applied_password_never_resets(self):
        guest = Guest()
        guest.receipt = {**OWNER, "schemaVersion": 1, "status": "requested", "passwordWriteRequested": True,
                         "verificationAttempted": False, "pamAuthenticated": False, "sshVerified": True}
        with self.assertRaises(draft.Blocked): guest.run()
        with self.assertRaises(draft.Blocked): guest.run()
        self.assertEqual((len(guest.writes), len(guest.auths)), (0, 1))

    def test_completed_generation_preserves_manual_password_rotation(self):
        guest = Guest()
        guest.run()
        guest.current_password = "synthetic-manual-rotation-only"
        result = guest.run()
        self.assertTrue(draft.completion_matches(result, OWNER))
        self.assertEqual(guest.current_password, "synthetic-manual-rotation-only")
        self.assertEqual((len(guest.writes), len(guest.auths)), (1, 1))

    def test_no_receipt_configured_password_is_never_overwritten(self):
        guest = Guest()
        guest.unconfigured = False
        with self.assertRaises(draft.Blocked): guest.run()
        self.assertEqual((guest.writes, guest.auths, guest.receipt), ([], [], None))

    def test_every_generation_owner_and_source_field_is_enforced(self):
        for field in OWNER:
            with self.subTest(field=field):
                guest = Guest()
                guest.actual[field] += "-mismatch"
                with self.assertRaises(draft.Blocked): guest.run()
                self.assertEqual((guest.writes, guest.auths), ([], []))

    def test_every_pending_identity_field_is_enforced(self):
        for field in OWNER:
            with self.subTest(field=field):
                guest = Guest()
                guest.run()
                guest.receipt[field] += "-mismatch"
                with self.assertRaises(draft.Blocked): guest.run()
                self.assertEqual((len(guest.writes), len(guest.auths)), (1, 1))

    def test_changed_identity_after_authentication_cannot_complete(self):
        guest = Guest()
        guest.change_observation_at = 2
        with self.assertRaises(draft.Blocked): guest.run()
        self.assertEqual(guest.receipt["status"], "requested")
        self.assertTrue(guest.receipt["verificationAttempted"])

    def test_invalid_complete_flags_cannot_bind(self):
        guest = Guest()
        guest.run()
        for field in ("passwordWriteRequested", "verificationAttempted", "pamAuthenticated", "sshVerified"):
            with self.subTest(field=field):
                proof = {**guest.receipt, field: False}
                self.assertFalse(draft.completion_matches(proof, OWNER))

    def test_recovery_without_guest_receipt_never_writes_password(self):
        guest = Guest()
        with self.assertRaises(draft.Blocked):
            draft.initialize(OWNER, PASSWORD, observe=guest.observe, read=lambda:None, persist=guest.persist,
                pristine=lambda:True, write_password=guest.write, authenticate=guest.authenticate,recovery=True)
        self.assertEqual((guest.writes,guest.auths,guest.receipt),([],[],None))

    def test_named_secret_is_removed_before_subprocess_and_never_reaches_argv_or_child_env(self):
        supplied={"CORE_SERIAL_PASSWORD":PASSWORD,"CORE_SERIAL_EXPECTED":json.dumps(OWNER),"CORE_SERIAL_MODE":"initial","SYNTHETIC_ENV_CANARY":"must-not-propagate"}
        with mock.patch.dict(os.environ,supplied,clear=True):
            expected,password,recovery=draft.inputs(os.environ)
            self.assertEqual(expected,OWNER)
            self.assertFalse(recovery)
            self.assertNotIn("CORE_SERIAL_PASSWORD",os.environ)
            self.assertNotIn("CORE_SERIAL_EXPECTED",os.environ)
            with mock.patch.object(draft.subprocess,"run",return_value=types.SimpleNamespace(returncode=0)) as command:
                draft.write_password(OWNER["adminUsername"],password)
        args,kwargs=command.call_args
        self.assertEqual(args[0],["/usr/sbin/chpasswd"])
        self.assertEqual(kwargs["input"],(OWNER["adminUsername"]+":"+PASSWORD+"\n").encode())
        self.assertNotIn("CORE_SERIAL_PASSWORD",kwargs["env"])
        self.assertNotIn("SYNTHETIC_ENV_CANARY",kwargs["env"])
        self.assertEqual(kwargs["stdout"],draft.subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"],draft.subprocess.DEVNULL)

    def test_failure_suppresses_exception_secret_and_system_output(self):
        output=io.StringIO()
        with mock.patch.object(draft,"inputs",side_effect=RuntimeError(PASSWORD)),redirect_stdout(output):
            self.assertEqual(draft.main(),1)
        self.assertEqual(json.loads(output.getvalue()),{"schemaVersion":1,"status":"blocked"})
        self.assertNotIn(PASSWORD,output.getvalue())


if __name__ == "__main__":
    unittest.main()
