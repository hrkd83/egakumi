"""Original creation, native resume and managed-command boundaries with synthetic ARM."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment import lifecycle
from deployment.helpers import serial_credentials as serial, serial_initialization as initial, private_permissions
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths
from configuration_fixtures import fixture
from lifecycle_fixtures import ScriptedTransport


class InitialCreationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="synthetic-serial-initialization-")
        self.addCleanup(directory.cleanup)
        self.config = normalize_configuration(fixture())
        self.paths = LocalPaths(Path(directory.name), self.config["environmentName"])
        if os.name == "nt":
            patch = mock.patch.object(private_permissions, "_windows_owner_acl")
            patch.start()
            self.addCleanup(patch.stop)
        self.writer = lifecycle.protected_json
        self.password = None

    def vm(self):
        return {"id": serial._owner(self.config)[0], "type": "Microsoft.Compute/virtualMachines",
            "tags": {"product": self.config["productName"], "environment": self.config["environmentName"]},
            "properties": {"vmId": "11111111-1111-4111-8111-111111111111", "provisioningState": "Succeeded",
                "osProfile": {"adminUsername": self.config["management"]["adminUsername"], "linuxConfiguration": {"disablePasswordAuthentication": True}}},
            "identity": {"type": "SystemAssigned", "principalId": "22222222-2222-4222-8222-222222222222", "tenantId": self.config["azureTenantId"]}}

    def record(self, mode="direct"):
        self.password = serial.prepare_initial_serial_password(self.config, self.paths, {"statusCode": 404})
        initial.record_core_request(self.config, self.paths, self.password, mode, self.writer)
        return initial.journal(self.config, self.paths)

    def core(self):
        value = initial.journal(self.config, self.paths)
        vm = self.vm()
        deployment = f"/subscriptions/{self.config['subscriptionId']}/providers/Microsoft.Resources/deployments/{self.config['resourceStem']}-core"
        output = {"id": vm["id"], "vmId": vm["properties"]["vmId"], "principalId": vm["identity"]["principalId"], "tenantId": vm["identity"]["tenantId"]}
        return {"statusCode": 200, "body": {"id": deployment,
            "tags": {"azd-env-name": self.config["environmentName"], "azd-project-name": "ai-workforce-core", "azd-layer-name": ""},
            "properties": {"provisioningState": "Succeeded", "parameters": {"managementInitialSerialGeneration": {"value": value["credentialGeneration"]}},
                "outputs": {"managementInitialSerialCreation": {"value": {"generation": value["credentialGeneration"], "coreDeploymentId": deployment, "managementVm": output}}}}}}

    def recover(self, response=None, vm=None):
        return initial.recover_password(self.config, self.paths, vm or self.vm(), lambda value: response or self.core(), self.writer)

    def proof(self, value):
        return {**value["target"], "schemaVersion": 1, "status": "complete", "passwordWriteRequested": True,
                "verificationAttempted": True, "pamAuthenticated": True, "sshVerified": True}

    def command_result(self, proof):
        return {"statusCode": 200, "body": {"properties": {"instanceView": {"executionState": "Succeeded", "exitCode": 0, "output": json.dumps(proof)}}}}

    def confirmed(self):
        self.record()
        self.recover()
        return initial.journal(self.config, self.paths)

    def test_existing_vm_without_original_receipt_never_opens_or_regenerates_credentials(self):
        with mock.patch.object(serial, "_read", side_effect=AssertionError("Existing VM must not read credentials")), \
             mock.patch.object(serial, "_new", side_effect=AssertionError("Existing VM must not regenerate credentials")):
            self.assertEqual(initial.recover_password(self.config, self.paths, self.vm(), lambda value: self.fail("No Core read without provenance"), self.writer), "")
        self.assertFalse(self.paths.secrets.exists())

    def test_empty_generation_lookup_never_opens_credentials(self):
        with mock.patch.object(serial, "_read", side_effect=AssertionError("No secret access for empty initial input")):
            self.assertEqual(serial.prepared_initial_serial_generation(self.config, self.paths, ""), "")

    def test_original_404_generation_is_saved_before_core_and_reused_without_rotation(self):
        value = self.record()
        self.assertIsNone(value["target"])
        self.assertTrue(value["initialVmAbsenceVerified"])
        self.assertFalse(value["dispatchAttempted"])
        self.assertNotIn(self.password, json.dumps(value))
        with mock.patch.object(serial, "_new", side_effect=AssertionError("A pending generation must be reused")):
            self.assertEqual(serial.prepare_initial_serial_password(self.config, self.paths, {"statusCode": 404}), self.password)
        self.assertEqual(serial.prepared_initial_serial_generation(self.config, self.paths, self.password), value["credentialGeneration"])

    def test_core_generation_mismatch_blocks_before_any_secret_read(self):
        self.record()
        core = self.core()
        core["body"]["properties"]["parameters"]["managementInitialSerialGeneration"]["value"] = "33333333-3333-4333-8333-333333333333"
        with mock.patch.object(serial, "_read", side_effect=AssertionError("Core mismatch must precede secret access")):
            with self.assertRaises(serial.SerialCredentialError): self.recover(core)

    def test_each_owned_core_proof_field_is_enforced_before_secret_access(self):
        self.record()
        for mutate in (
            lambda x: x["body"]["tags"].update({"azd-project-name": "synthetic-other-project"}),
            lambda x: x["body"]["properties"].update({"provisioningState": "Failed"}),
            lambda x: x["body"]["properties"]["outputs"]["managementInitialSerialCreation"]["value"]["managementVm"].update({"vmId": "55555555-5555-4555-8555-555555555555"}),
            lambda x: x["body"].update({"id": x["body"]["id"] + "-other"}),
        ):
            response = copy.deepcopy(self.core())
            mutate(response)
            with mock.patch.object(serial, "_read", side_effect=AssertionError("Ownership proof must precede secret access")):
                with self.assertRaises(serial.SerialCredentialError): self.recover(response)

    def test_native_resume_after_generated_input_cleanup_uses_original_provenance(self):
        self.record("native")
        self.assertFalse(self.paths.parameters.exists())
        self.assertEqual(self.recover(), self.password)
        value = initial.journal(self.config, self.paths)
        self.assertEqual(value["status"], "target-confirmed")
        self.assertEqual(value["target"]["vmId"], self.vm()["properties"]["vmId"])

    def test_failed_original_core_recovers_child_vm_uuid_without_opening_password(self):
        self.record()
        response = self.core()
        creation = response["body"]["properties"]["outputs"]["managementInitialSerialCreation"]["value"]
        response["body"]["properties"]["provisioningState"] = "Failed"
        response["body"]["properties"]["outputs"] = {}
        response["managementCreation"] = creation
        with mock.patch.object(serial, "_read", side_effect=AssertionError("Failed Core must not open credentials")):
            self.assertEqual(self.recover(response), "")
        value = initial.journal(self.config, self.paths)
        self.assertIsNotNone(value["target"])
        self.assertEqual(value["target"]["vmId"], self.vm()["properties"]["vmId"])
        self.assertFalse(value["dispatchAttempted"])

    def test_failed_original_core_without_child_generation_proof_is_blocked(self):
        self.record()
        response = self.core()
        response["body"]["properties"]["provisioningState"] = "Failed"
        response["body"]["properties"]["outputs"] = {}
        with mock.patch.object(serial, "_read", side_effect=AssertionError("No child proof must precede secret access")):
            with self.assertRaises(serial.SerialCredentialError): self.recover(response)

    def test_target_is_persisted_before_original_secret_is_opened(self):
        self.record()
        original = serial._read
        def read_after_provenance(path, config):
            value = initial.journal(config, self.paths)
            self.assertIsNotNone(value["target"])
            return original(path, config)
        with mock.patch.object(serial, "_read", side_effect=read_after_provenance):
            self.assertEqual(self.recover(), self.password)

    def test_initial_dispatch_uses_secure_input_and_records_uncertainty_before_put(self):
        value = self.confirmed()
        transport = ScriptedTransport([{ "statusCode": 404 }, { "statusCode": 202 },
            { "statusCode": 200, "body": { "properties": { "provisioningState": "Succeeded" }}}, self.command_result(self.proof(value))])
        result = initial.initialize_guest(self.config, self.paths, value, self.password, transport, {"resources": []}, self.writer)
        self.assertEqual(result, self.proof(value))
        request, = transport.mutations()
        self.assertEqual(request["body"]["properties"]["parameters"]["operation"]["value"], "initial")
        self.assertEqual(request["body"]["properties"]["parameters"]["initialPassword"]["value"], self.password)
        self.assertTrue(initial.journal(self.config, self.paths)["dispatchAttempted"])

    def test_deployment_success_waits_for_atomic_named_command_proof_without_another_put(self):
        value = self.confirmed()
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            {"statusCode": 200, "body": {"properties": {"instanceView": {"executionState": "Pending"}}}},
            {"statusCode": 200, "body": {"properties": {"instanceView": {"executionState": "Succeeded", "exitCode": 0, "output": ""}}}},
            self.command_result(self.proof(value))])
        with mock.patch.object(initial.time, "sleep"):
            result = initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        self.assertEqual(result, self.proof(value))
        self.assertEqual(len(transport.mutations()), 1)
        self.assertEqual(transport.mutations()[0]["body"]["properties"]["parameters"]["operation"]["value"], "initial")
        gets = [request for request in transport.calls if request["method"] == "GET"]
        self.assertEqual(sum("/runCommands/" in request["path"] for request in gets), 4)
        self.assertEqual(sum("/providers/Microsoft.Resources/deployments/" in request["path"] for request in gets), 1)

    def test_explicit_command_failure_stops_before_any_later_success_or_password_retry(self):
        value = self.confirmed()
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            {"statusCode": 200, "body": {"properties": {"instanceView": {"executionState": "Failed", "exitCode": 1,
                "output": '{"schemaVersion":1,"status":"blocked"}'}}}},
            self.command_result(self.proof(value))])
        with mock.patch.object(initial.time, "sleep") as sleep:
            with self.assertRaises(serial.SerialCredentialError):
                initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        sleep.assert_not_called()
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(len(transport.mutations()), 1)

    def test_nonempty_foreign_complete_proof_is_terminal_even_when_execution_fields_lag(self):
        value = self.confirmed()
        foreign = {**self.proof(value), "credentialGeneration": "33333333-3333-4333-8333-333333333333"}
        response = self.command_result(foreign)
        response["body"]["properties"]["instanceView"]["executionState"] = "Running"
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            response, self.command_result(self.proof(value))])
        with mock.patch.object(initial.time, "sleep") as sleep:
            with self.assertRaises(serial.SerialCredentialError):
                initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        sleep.assert_not_called()
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(len(transport.mutations()), 1)

    def test_nonempty_malformed_complete_proof_is_not_treated_as_propagation_delay(self):
        value = self.confirmed()
        incomplete = self.proof(value)
        incomplete.pop("pamAuthenticated")
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            self.command_result(incomplete), self.command_result(self.proof(value))])
        with mock.patch.object(initial.time, "sleep") as sleep:
            with self.assertRaises(serial.SerialCredentialError):
                initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        sleep.assert_not_called()
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(len(transport.mutations()), 1)

    def test_boolean_exit_code_cannot_bypass_terminal_result_type_check(self):
        value = self.confirmed()
        malformed = self.command_result(self.proof(value))
        malformed["body"]["properties"]["instanceView"]["exitCode"] = False
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            malformed, self.command_result(self.proof(value))])
        with mock.patch.object(initial.time, "sleep") as sleep:
            with self.assertRaises(serial.SerialCredentialError):
                initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        sleep.assert_not_called()
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(len(transport.mutations()), 1)

    def test_missing_command_result_uses_shared_budget_and_retains_original_pending_generation(self):
        value = self.confirmed()
        transport = ScriptedTransport([{"statusCode": 404}, {"statusCode": 202},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded"}}},
            {"statusCode": 404}])
        with mock.patch.object(initial.time, "monotonic", side_effect=[0, 1, 300]), mock.patch.object(initial.time, "sleep"):
            with self.assertRaises(serial.SerialCredentialError):
                initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer)
        current = initial.journal(self.config, self.paths)
        self.assertEqual(current["credentialGeneration"], value["credentialGeneration"])
        self.assertTrue(current["dispatchAttempted"])
        self.assertEqual(len(transport.mutations()), 1)

    def test_uncertain_initial_dispatch_is_never_reissued_as_a_password_write(self):
        value = self.confirmed()
        first = ScriptedTransport([{ "statusCode": 404 }, { "statusCode": 503 }])
        with self.assertRaises(serial.SerialCredentialError):
            initial.initialize_guest(self.config, self.paths, value, self.password, first, {"resources": []}, self.writer)
        second = ScriptedTransport([{ "statusCode": 404 }, { "statusCode": 202 },
            { "statusCode": 200, "body": { "properties": { "provisioningState": "Succeeded" }}}, self.command_result(self.proof(value))])
        initial.initialize_guest(self.config, self.paths, initial.journal(self.config, self.paths), self.password, second, {"resources": []}, self.writer)
        self.assertEqual(second.mutations()[0]["body"]["properties"]["parameters"]["operation"]["value"], "recover")

    def test_failed_dispatch_provenance_publication_sends_zero_put(self):
        value = self.confirmed()
        transport = ScriptedTransport([{ "statusCode": 404 }])
        with self.assertRaises(OSError):
            initial.initialize_guest(self.config, self.paths, value, self.password, transport, {"resources": []},
                lambda path, data: (_ for _ in ()).throw(OSError("Synthetic publication failed")))
        self.assertEqual(transport.mutations(), [])

    def test_exact_completed_readback_sends_zero_put(self):
        value = self.confirmed()
        transport = ScriptedTransport([self.command_result(self.proof(value))])
        self.assertEqual(initial.initialize_guest(self.config, self.paths, value, self.password, transport, {}, self.writer), self.proof(value))
        self.assertEqual(transport.mutations(), [])

    def test_completed_journal_keeps_existing_manual_password_and_skips_credential_read(self):
        value = self.confirmed()
        initial.save(self.paths, {**value, "dispatchAttempted": True}, self.writer)
        initial.mark_complete(self.config, self.paths, self.writer)
        with mock.patch.object(serial, "_read", side_effect=AssertionError("Completed generation must not reopen secrets")):
            self.assertEqual(self.recover(), "")

    def test_resource_shape_keeps_password_out_of_osprofile_and_uses_named_protected_parameters(self):
        management = (ROOT / "infra/modules/management.bicep").read_text(encoding="utf-8")
        module = (ROOT / "infra/modules/management-serial-initialization.bicep").read_text(encoding="utf-8")
        self.assertNotIn("adminPassword", management)
        self.assertIn("disablePasswordAuthentication: true", management)
        self.assertIn("@secure()\nparam initialPassword string", module)
        self.assertIn("protectedParameters: [{ name: 'CORE_SERIAL_PASSWORD', value: initialPassword }]", module)
        self.assertNotIn("initialPassword}", module)
        self.assertIn("exec /usr/bin/python3 -I -B -", module)


if __name__ == "__main__": unittest.main()
