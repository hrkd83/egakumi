"""Initial Serial secrets cross real lifecycle boundaries with synthetic ARM only."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from deployment import lifecycle
from deployment.helpers import private_permissions, serial_credentials as serial, serial_initialization as initial
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths
from tests.support.configuration_fixtures import fixture
from tests.support.lifecycle_fixtures import ScriptedTransport


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
                "certificateBootstrap": {"jobName": "synthetic-job"},
                "endpoints": {"public": "https://fixture.example.invalid"}}

    def deployment(self, status="Succeeded"):
        return {"statusCode": 200, "body": {"id":self.outputs()["managementInitialSerialCreation"]["coreDeploymentId"],
            "tags":{"azd-env-name":self.config["environmentName"],"azd-project-name":"ai-workforce-core","azd-layer-name":""},
            "properties": {"parameters":{"managementInitialSerialGeneration":{"value":self.generation}},"provisioningState": status,
            "outputs": {key: {"value": value} for key, value in self.outputs().items()}}}}

    def receipt(self):
        return json.loads((self.paths.secrets / "management-serial-credential.json").read_text(encoding="utf-8"))

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

    def test_native_postprovision_binds_the_original_vm_generation(self):
        first = serial.prepare_initial_serial_password(self.config, self.paths, self.missing())
        initial.record_core_request(self.config, self.paths, first, lifecycle.protected_json)
        lifecycle.protected_json(self.paths.parameters, {"parameters": {
            "l7IngressSecret": {"value": "synthetic-input"},
            "firewallTlsInspection": {"value": {}},
        }})
        transport = ScriptedTransport([self.group(), self.vm(), self.deployment(), self.group(), self.vm()])
        response = self.deployment()["body"]

        def verified_initialization(config, workspace, value, password, transport, template, writer):
            initial.save(workspace, {**value, "dispatchAttempted": True}, writer)
            return {**value["target"], "schemaVersion": 1, "status": "complete", "passwordWriteRequested": True,
                    "verificationAttempted": True, "pamAuthenticated": True, "sshVerified": True}

        def finish(config, outputs, transport, password, ingress_secret, firewall_ca):
            lifecycle._bind_management_serial_password(config, outputs, transport, password)
            return outputs, {"status": "configured"}

        with mock.patch.object(lifecycle, "run", side_effect=[
                {"CORE_DEPLOYMENT_NAME": self.config["resourceStem"]+"-core"},
                response, {"CORE_DEPLOYMENT_NAME": self.config["resourceStem"]+"-core"}, {"resources": []}]), \
             mock.patch.object(initial, "initialize_guest", side_effect=verified_initialization), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "finish_certificate_core", side_effect=finish), \
             contextlib.redirect_stdout(self.output):
            lifecycle.postprovision(self.config)
        self.assertEqual(self.receipt()["boundVmId"],self.vm()["body"]["properties"]["vmId"])
        self.assertFalse(self.paths.parameters.exists())
        self.assertNotIn(first,self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
