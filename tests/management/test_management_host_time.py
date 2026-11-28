"""Read-only infrastructure check: prove PHC preparation before retiring NTP networking."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import uuid
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment.helpers import management_host_time as host_time
from deployment import lifecycle


class ReadOnlyTransport:
    def __init__(self, vm, command, audit=None):
        self.responses = [vm, command, {"statusCode": 404} if audit is None else audit]
        self.calls = []

    def request(self, method, path, *args, **kwargs):
        if method != "GET" or args or kwargs:
            raise AssertionError("Host-time readiness must never modify Azure")
        self.calls.append(path)
        return deepcopy(self.responses[len(self.calls) - 1])


class ManagementHostTimeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": str(uuid.UUID(int=1)), "resourceStem": "test-synthetic",
                       "resourceGroupNames": {"maint": "rg-synthetic-maint"},
                       "productName": "synthetic", "environmentName": "test"}
        self.target = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/rg-synthetic-maint"
                       "/providers/Microsoft.Compute/virtualMachines/vm-test-synthetic-mgmt")
        self.vm = {"statusCode": 200, "body": {"id": self.target,
                   "type": "Microsoft.Compute/virtualMachines", "tags": {"product": "synthetic", "environment": "test"},
                   "properties": {"provisioningState": "Succeeded", "timeCreated": "2025-01-01T00:00:00Z"}}}
        proof = {"status": "configured", "timeService": "chrony.service",
                 "timeSource": "azure-host-phc", "timeSynchronized": True}
        self.command = {"statusCode": 200, "body": {"id": self.target + "/runCommands/configure-linux-audit",
                        "type": "Microsoft.Compute/virtualMachines/runCommands",
                        "properties": {"provisioningState": "Succeeded",
                                       "source": {"script": host_time.GUEST_SCRIPT.read_text(encoding="utf-8")},
                                       "instanceView": {"executionState": "Succeeded", "exitCode": 0,
                                                        "startTime": "2025-01-02T00:00:00Z", "endTime": "2025-01-02T00:01:00Z",
                                                        "output": json.dumps(proof) + "\n"}}}}

    def check(self, vm=None, command=None):
        transport = ReadOnlyTransport(self.vm if vm is None else vm, self.command if command is None else command)
        result = host_time.require_management_host_time_core_ready(self.config, transport)
        return result, transport.calls

    def test_exact_vm_absence_allows_initial_core_without_reading_a_command(self):
        result, calls = self.check(vm={"statusCode": 404})
        self.assertEqual(result, {"newManagementVm": True, "hostTimePreparationRequired": False})
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith(self.target + "?"))

    def test_owned_vm_and_current_command_prove_prior_preparation_without_clock_measurement(self):
        result, calls = self.check()
        self.assertEqual(result, {"newManagementVm": False, "hostTimePrepared": True, "currentClockMeasured": False})
        self.assertEqual(len(calls), 2)
        self.assertIn("&$expand=instanceView", calls[1])

    def test_crlf_material_is_normalized_before_comparison(self):
        command = deepcopy(self.command)
        command["body"]["properties"]["source"]["script"] = host_time.GUEST_SCRIPT.read_text(encoding="utf-8").replace("\n", "\r\n")
        self.assertTrue(self.check(command=command)[0]["hostTimePrepared"])

    def test_failed_or_unrecognized_vm_reads_never_allow_core(self):
        for status in (401, 403, 409, 500):
            with self.subTest(status=status), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(vm={"statusCode": status})

    def test_only_structured_integer_http_status_is_accepted(self):
        for value in (None, [], {}, {"statusCode": 404.0}, {"statusCode": "404"}, {"statusCode": True}):
            transport = ReadOnlyTransport(value, self.command)
            with self.subTest(value=value), self.assertRaises(host_time.ManagementHostTimeError):
                host_time.require_management_host_time_core_ready(self.config, transport)

    def test_wrong_vm_ownership_identity_or_state_is_rejected(self):
        for field, value in (("id", self.target + "-other"), ("type", "Microsoft.Compute/disks"),
                             ("tags", {}), ("properties", {"provisioningState": "Updating"})):
            vm = deepcopy(self.vm)
            vm["body"][field] = value
            with self.subTest(field=field), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(vm=vm)

    def test_missing_or_unknown_command_is_not_success(self):
        for status in (404, 403, 500):
            with self.subTest(status=status), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(command={"statusCode": status})

    def test_unverified_guest_material_cannot_authorize_network_changes(self):
        for source in (None, {}, {"script": "#!/bin/bash\n# old external NTP setup\n"}):
            command = deepcopy(self.command)
            command["body"]["properties"]["source"] = source
            with self.subTest(source=source), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(command=command)

    def test_only_terminal_success_with_integer_zero_exit_code_is_accepted(self):
        for field, values in (("executionState", ("Running", "Failed", "Canceled", None)),
                              ("exitCode", (False, "0", 1, None)), ("output", (None, ""))):
            for value in values:
                command = deepcopy(self.command)
                command["body"]["properties"]["instanceView"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(host_time.ManagementHostTimeError):
                    self.check(command=command)

    def test_command_from_a_previous_vm_instance_or_invalid_time_order_is_rejected(self):
        vm = deepcopy(self.vm)
        vm["body"]["properties"]["timeCreated"] = "2025-01-03T00:00:00Z"
        with self.assertRaises(host_time.ManagementHostTimeError):
            self.check(vm=vm)
        for field, value in (("startTime", None), ("endTime", "invalid"),
                             ("endTime", "2025-01-01T00:00:00Z"), ("startTime", "2025-01-02T09:00:00+09:00")):
            command = deepcopy(self.command)
            command["body"]["properties"]["instanceView"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(command=command)

    def test_old_external_ntp_daemon_and_ambiguous_success_outputs_are_rejected(self):
        output = self.command["body"]["properties"]["instanceView"]["output"]
        proof = json.loads(output)
        invalid = [output + output]
        for field, value in (("timeSource", "external-ntp"), ("timeSynchronized", False),
                             ("timeSynchronized", "true"), ("timeService", "systemd-timesyncd.service")):
            invalid.append(json.dumps({**proof, field: value}))
        invalid.append(json.dumps({"status": "configured", "timeService": "chrony.service"}))
        for text in invalid:
            command = deepcopy(self.command)
            command["body"]["properties"]["instanceView"]["output"] = text
            with self.subTest(output=text), self.assertRaises(host_time.ManagementHostTimeError):
                self.check(command=command)

    def test_gate_runs_before_app_gateway_and_nsg_checks(self):
        with patch.object(lifecycle, "require_management_host_time_core_ready",
                          side_effect=host_time.ManagementHostTimeError("PHC preparation required")), \
             patch.object(lifecycle, "require_appgw_backend_tls_ready") as portal, \
             patch.object(lifecycle, "verify_fixed_nsg_before_deployment") as nsg:
            with self.assertRaises(lifecycle.DeploymentError):
                lifecycle.verify_core_network_before_deployment(self.config, ReadOnlyTransport(self.vm, self.command))
            portal.assert_not_called()
            nsg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
