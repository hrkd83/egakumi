"""Initial Serial secrets cross real lifecycle boundaries with synthetic ARM only."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import private_permissions, serial_credentials as serial, serial_initialization as initial
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths
from configuration_fixtures import fixture
from lifecycle_fixtures import ScriptedTransport


class SerialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="serial-lifecycle-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = normalize_configuration(fixture())
        self.paths = LocalPaths(self.root, self.config["environmentName"])
        patch = mock.patch.object(lifecycle, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        if os.name == "nt":
            acl = mock.patch.object(private_permissions, "_windows_owner_acl")
            acl.start()
            self.addCleanup(acl.stop)
        process = mock.patch.object(lifecycle.subprocess, "run", side_effect=AssertionError("No external process in synthetic lifecycle tests"))
        process.start()
        self.addCleanup(process.stop)
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.generation = "44444444-4444-4444-8444-444444444444"
        generation = mock.patch.object(serial.uuid, "uuid4", return_value=uuid.UUID(self.generation))
        generation.start()
        self.addCleanup(generation.stop)

    def missing(self):
        return {"statusCode": 404, "body": None, "headers": {}}

    def group(self):
        return {"statusCode": 200, "body": {
            "id": f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames']['maint']}",
            "location": self.config["location"], "tags": {
                "product": self.config["productName"], "environment": self.config["environmentName"],
                "azd-env-name": self.config["environmentName"], "layer": "maint"},
        }}

    def vm(self):
        return {"statusCode": 200, "body": {
            "id": serial._owner(self.config)[0], "type": "Microsoft.Compute/virtualMachines",
            "location": self.config["location"], "tags": {
                "product": self.config["productName"], "environment": self.config["environmentName"]},
            "identity": {"type": "SystemAssigned", "principalId": "33333333-3333-4333-8333-333333333333", "tenantId": self.config["azureTenantId"]},
            "properties": {"provisioningState": "Succeeded", "vmId": "22222222-2222-4222-8222-222222222222",
                "osProfile": {"adminUsername": self.config["management"]["adminUsername"],
                              "linuxConfiguration": {"disablePasswordAuthentication": True}}},
        }}

    def outputs(self):
        vm = self.vm()["body"]
        management = {"id":vm["id"], "vmId":vm["properties"]["vmId"], "principalId":vm["identity"]["principalId"], "tenantId":vm["identity"]["tenantId"]}
        return {"managementVm": management, "managementInitialSerialCreation": {"generation":self.generation,
                "coreDeploymentId":f"/subscriptions/{self.config['subscriptionId']}/providers/Microsoft.Resources/deployments/{self.config['resourceStem']}-core", "managementVm":management},
                "resourceGroups": self.config["resourceGroupNames"],
                "endpoints": {"public": "https://fixture.example.invalid"}}

    def deployment(self, status="Succeeded"):
        return {"statusCode": 200, "body": {"id":self.outputs()["managementInitialSerialCreation"]["coreDeploymentId"],
            "tags":{"azd-env-name":self.config["environmentName"],"azd-project-name":"ai-workforce-core","azd-layer-name":""},
            "properties": {"parameters":{"managementInitialSerialGeneration":{"value":self.generation}},"provisioningState": status,
            "outputs": {key: {"value": value} for key, value in self.outputs().items()}}}}

    def core(self, transport, *, secrets=None):
        with contextlib.ExitStack() as stack:
            for name, value in (("run", {"resources": []}), ("ArmTransport", transport),
                                ("prepare_core_network_upgrade", None), ("complete_core_network_upgrade", None),
                                ("discover_existing_network_security_groups", []), ("discover_existing_route_table_routes", {}),
                                ("discover_existing_apim_hostnames", {}), ("discover_existing_apim_egress_fqdns", []),
                                ("discover_existing_portal_app", False), ("read_workspace_privacy_link", ""),
                                ("_save_deployment_state", {}), ("configure_grpc_endpoint", {"enabled": True})):
                stack.enter_context(mock.patch.object(lifecycle, name, return_value=value))
            stack.enter_context(mock.patch.object(lifecycle, "prepare_diagnostic_settings", side_effect=lambda config, transport, phase, *a:
                {key: item["properties"] for key, item in lifecycle.canonical_settings(lifecycle.build_bicep_configuration(config), phase).items()}))
            def verified_initialization(config, workspace, value, password, transport, template, writer):
                initial.save(workspace,{**value,"dispatchAttempted":True},writer)
                return {**value["target"],"schemaVersion":1,"status":"complete","passwordWriteRequested":True,"verificationAttempted":True,"pamAuthenticated":True,"sshVerified":True}
            stack.enter_context(mock.patch.object(initial,"initialize_guest",side_effect=verified_initialization))
            stack.enter_context(contextlib.redirect_stdout(self.output))
            stack.enter_context(contextlib.redirect_stderr(self.errors))
            return lifecycle.deploy_phase(self.config, "core", secrets=secrets or {})

    def receipt(self):
        return json.loads((self.paths.secrets / "management-serial-credential.json").read_text(encoding="utf-8"))

    def test_initial_core_supplies_only_a_secure_input_and_binds_actual_vm_instance(self):
        transport = ScriptedTransport([self.missing(), self.missing(), self.missing(), self.missing(),
            {"statusCode": 202, "body": None}, self.deployment(), self.group(), self.vm()])
        supplied = {"managementAdminPassword": "caller-cannot-override-initial-generation"}
        self.assertEqual(self.core(transport, secrets=supplied), self.outputs())
        receipt = self.receipt()
        request, = transport.mutations()
        values = request["body"]["properties"]["parameters"]
        self.assertNotIn("managementAdminPassword",values)
        self.assertEqual(values["managementInitialSerialGeneration"]["value"], receipt["generation"])
        self.assertNotIn(receipt["password"],json.dumps(request["body"]))
        self.assertNotIn(receipt["password"], json.dumps(values["configuration"]))
        self.assertEqual(receipt["boundVmId"], self.vm()["body"]["properties"]["vmId"])
        self.assertEqual(supplied["managementAdminPassword"], "caller-cannot-override-initial-generation")
        self.assertEqual(self.output.getvalue() + self.errors.getvalue(), "")

    def test_existing_core_never_accesses_serial_secret_and_ignores_supplied_password(self):
        transport = ScriptedTransport([self.group(), self.vm(), self.group(), self.vm(),
            {"statusCode": 202, "body": None}, self.deployment()])
        with mock.patch.object(serial, "_storage", side_effect=AssertionError("Existing VM cannot access credentials")), \
             mock.patch.object(serial, "_read", side_effect=AssertionError("Existing VM cannot read credentials")):
            self.core(transport, secrets={"managementAdminPassword": "caller-cannot-change-existing-vm"})
        request, = transport.mutations()
        self.assertNotIn("managementAdminPassword",request["body"]["properties"]["parameters"])
        self.assertEqual(request["body"]["properties"]["parameters"]["managementInitialSerialGeneration"]["value"], "")
        self.assertFalse((self.paths.secrets / "management-serial-credential.json").exists())

    def test_foreign_parent_stops_before_secret_generation_or_core_put(self):
        foreign = self.group()
        foreign["body"]["tags"]["product"] = "another-fixture"
        transport = ScriptedTransport([foreign])
        with self.assertRaises(lifecycle.DeploymentError):
            self.core(transport)
        self.assertFalse(self.paths.secrets.exists())
        self.assertEqual(transport.mutations(), [])

    def test_vm_appearing_before_put_stops_and_retains_the_initial_pending_generation(self):
        transport = ScriptedTransport([self.missing(), self.missing(), self.group(), self.vm()])
        with self.assertRaises(lifecycle.DeploymentError):
            self.core(transport)
        self.assertIsNone(self.receipt()["boundVmId"])
        self.assertEqual(transport.mutations(), [])

    def test_failed_core_retains_pending_secret_for_an_absent_vm_retry(self):
        def attempt():
            return ScriptedTransport([self.missing(), self.missing(), self.missing(), self.missing(),
                {"statusCode": 202, "body": None}, self.deployment("Failed")])
        with self.assertRaises(lifecycle.DeploymentError):
            self.core(attempt())
        before = self.receipt()
        with mock.patch.object(serial, "_new", side_effect=AssertionError("An absent-VM retry reuses pending credentials")):
            with self.assertRaises(lifecycle.DeploymentError):
                self.core(attempt())
        self.assertEqual(self.receipt(), before)
        self.assertIsNone(before["boundVmId"])

    def test_binding_rejects_the_password_of_another_generation(self):
        first = serial.prepare_initial_serial_password(self.config, self.paths, self.missing())
        other = serial._new(self.config)["password"]
        with self.assertRaises(serial.SerialCredentialError):
            serial.bind_initial_serial_password(self.config, self.paths, self.vm()["body"], initial_password=other)
        self.assertEqual(self.receipt()["password"], first)
        self.assertIsNone(self.receipt()["boundVmId"])

    def test_failed_staging_protection_closes_the_descriptor_and_cleans_the_empty_file(self):
        target = self.paths.parameters
        module, operation = ((lifecycle, "protect_serial_storage") if os.name == "nt"
                             else (lifecycle.os, "fchmod"))
        with mock.patch.object(module, operation, side_effect=private_permissions.PrivatePermissionsError("Unsafe fixture ACL")):
            with self.assertRaises(private_permissions.PrivatePermissionsError):
                lifecycle.protected_json(target, {"synthetic": "secret-content-must-not-be-written"})
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(".core-*")), [])

    def test_native_postprovision_recovers_after_generated_input_cleanup(self):
        first = serial.prepare_initial_serial_password(self.config, self.paths, self.missing())
        initial.record_core_request(self.config,self.paths,first,"native",lifecycle.protected_json)
        self.assertFalse(self.paths.parameters.exists())
        transport = ScriptedTransport([self.group(),self.vm(),self.deployment(),self.group(),self.vm()])
        response = self.deployment()["body"]
        def verified_initialization(config,workspace,value,password,transport,template,writer):
            initial.save(workspace,{**value,"dispatchAttempted":True},writer)
            return {**value["target"],"schemaVersion":1,"status":"complete","passwordWriteRequested":True,"verificationAttempted":True,"pamAuthenticated":True,"sshVerified":True}
        with mock.patch.object(lifecycle,"run",side_effect=[{"CORE_DEPLOYMENT_NAME":self.config["resourceStem"]+"-core"},response,{"CORE_DEPLOYMENT_NAME":self.config["resourceStem"]+"-core"},{"resources":[]}]),              mock.patch.object(initial,"initialize_guest",side_effect=verified_initialization),              mock.patch.object(lifecycle,"ArmTransport",return_value=transport),              mock.patch.object(lifecycle,"_save_deployment_state"),              mock.patch.object(lifecycle,"complete_core_network_upgrade"),              mock.patch.object(lifecycle,"configure_grpc_endpoint",return_value={"enabled":False}),              mock.patch.object(lifecycle,"configure_public_dns",return_value={"status":"configured"}),              contextlib.redirect_stdout(self.output):
            lifecycle.postprovision(self.config)
        self.assertEqual(self.receipt()["boundVmId"],self.vm()["body"]["properties"]["vmId"])
        self.assertFalse(self.paths.parameters.exists())
        self.assertNotIn(first,self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
