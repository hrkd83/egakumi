"""Operator-rule retirement uses synthetic configuration and an offline ARM fake."""
import contextlib
import copy
import io
import ipaddress
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configuration_fixtures import fixture
from deployment import lifecycle
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers import operator_access as operator
from lifecycle_fixtures import LifecycleFixture, ScriptedTransport


class ArmFixture:
    def __init__(self):
        self.config = normalize_configuration(fixture())
        self.config["network"]["operatorAddressPrefixes"] = []
        group = self.config["resourceGroupNames"]["nw"]
        self.group_id = "/subscriptions/" + self.config["subscriptionId"] + "/resourceGroups/" + group
        self.parent_id = self.group_id + "/providers/" + operator.TYPE + "/nsg-" + self.config["resourceStem"] + "-vm"
        self.child_id = self.parent_id + "/securityRules/" + operator.RULE_NAME
        subnet = ipaddress.ip_network(self.config["network"]["subnetPrefixes"]["management"])
        self.address = str(subnet.network_address + 4)
        source = self.config["network"]["clientAddressPrefixes"][0]
        properties = {"priority": 220, "direction": "Inbound", "access": "Allow",
                      "protocol": self.config["network"]["operatorProtocol"], "sourcePortRange": "*",
                      "destinationPortRange": self.config["network"]["operatorPorts"][0],
                      "sourceAddressPrefix": source, "destinationAddressPrefix": self.address}
        terminal = {"priority": 4096, "direction": "Inbound", "access": "Deny", "protocol": "*",
                    "sourcePortRange": "*", "destinationPortRange": "*",
                    "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}
        custom = {**terminal, "priority": 500, "direction": "Outbound", "access": "Allow"}
        self.parent = {"id": self.parent_id, "name": self.parent_id.rsplit("/", 1)[-1], "type": operator.TYPE,
                       "location": self.config["location"], "etag": '"before"',
                       "tags": {"product": self.config["productName"], "environment": self.config["environmentName"],
                                "azd-env-name": self.config["environmentName"], "custom-owner": "preserved"},
                       "properties": {"provisioningState": "Succeeded", "flushConnection": False,
                                      "securityRules": [self.rule(operator.RULE_NAME, properties),
                                                        self.rule("deny-other-inbound", terminal),
                                                        self.rule("custom-preserved", custom)],
                                      "subnets": [{"id": self.parent_id + "/synthetic-reference"}]}}
        self.calls, self.reads = [], 0
        self.get_hook, self.write_hook = None, None
        self.failure, self.pending = False, 0
        self.owner_valid, self.child_valid = True, True

    def rule(self, name, properties):
        return {"id": self.parent_id + "/securityRules/" + name, "name": name,
                "type": operator.TYPE + "/securityRules", "etag": '"child"',
                "properties": {**properties, "provisioningState": "Succeeded"}}

    def named(self, name):
        return next(rule for rule in self.parent["properties"]["securityRules"] if rule["name"] == name)

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))
        target = path.split("?", 1)[0]
        if method == "GET" and target == self.parent_id:
            self.reads += 1
            if self.get_hook:
                self.get_hook(self, self.reads)
            if self.parent is None:
                return {"statusCode": 404, "body": None}
            value = copy.deepcopy(self.parent)
            if self.pending and self.writes():
                self.pending -= 1
                value["properties"]["provisioningState"] = "Updating"
            return {"statusCode": 200, "body": value}
        if method == "GET" and target == self.group_id:
            return {"statusCode": 200, "body": {"id": self.group_id, "tags": {
                "product": self.config["productName"] if self.owner_valid else "foreign",
                "environment": self.config["environmentName"]}}}
        if method == "GET" and target == self.child_id:
            child = next((rule for rule in self.parent["properties"]["securityRules"]
                          if rule["name"] == operator.RULE_NAME), None)
            if child is None:
                return {"statusCode": 404, "body": None}
            value = copy.deepcopy(child)
            if not self.child_valid:
                value["id"] += "/foreign"
            return {"statusCode": 200, "body": value}
        if method == "DELETE" and target == self.child_id:
            if self.failure:
                raise RuntimeError("PRIVATE_SENTINEL")
            self.parent["properties"]["securityRules"].remove(self.named(operator.RULE_NAME))
            self.parent["etag"] = '"after"'
            self.parent["systemData"] = {"lastModifiedByType": "Application"}
            if self.write_hook:
                self.write_hook(self)
            return {"statusCode": 202, "body": None}
        raise AssertionError("Unexpected offline ARM request")

    def writes(self):
        return [row for row in self.calls if row[0] != "GET"]


class OperatorAccessTests(unittest.TestCase):
    def setUp(self):
        self.arm = ArmFixture()

    def cleanup(self, **kwargs):
        return operator.cleanup_disabled_operator(self.arm.config, self.arm, **kwargs)

    def test_nonempty_operator_is_a_complete_noop_even_without_a_terminal(self):
        self.arm.config["network"]["operatorAddressPrefixes"] = [self.arm.address]
        rules = [self.arm.named(operator.RULE_NAME)]
        self.assertEqual(operator.remove_disabled_operator(self.arm.config, rules), rules)
        self.assertEqual(self.cleanup(), {"status": "not-required", "azureWrites": 0})
        self.assertFalse(self.arm.calls)

    def test_absent_nsg_and_absent_rule_are_read_only_and_repeatable(self):
        self.arm.parent = None
        self.assertEqual(self.cleanup()["status"], "nsg-absent")
        self.assertFalse(self.arm.writes())
        self.arm = ArmFixture()
        self.arm.parent["properties"]["securityRules"].remove(self.arm.named(operator.RULE_NAME))
        self.assertEqual(self.cleanup()["status"], "rule-absent")
        self.assertEqual(self.cleanup()["azureWrites"], 0)
        self.assertFalse(self.arm.writes())

    def test_current_allow_is_removed_without_changing_other_rules(self):
        before = copy.deepcopy(self.arm.parent["properties"]["securityRules"])
        result = operator.remove_disabled_operator(self.arm.config, before)
        self.assertEqual(result, before[1:])
        self.assertEqual(before, self.arm.parent["properties"]["securityRules"])
        props = self.arm.named(operator.RULE_NAME)["properties"]
        props["sourceAddressPrefixes"] = [props.pop("sourceAddressPrefix")]
        props.update(sourceAddressPrefix="", destinationPortRanges=[], sourcePortRanges=[])
        self.assertEqual(operator.remove_disabled_operator(self.arm.config,
                         self.arm.parent["properties"]["securityRules"]), before[1:])

    def test_custom_operator_scope_or_conditions_stop_without_deletion(self):
        changes = [lambda p: p.update(priority=221), lambda p: p.update(access="Deny"),
                   lambda p: p.update(protocol="Udp"), lambda p: p.update(destinationPortRange="23"),
                   lambda p: p.update(description="custom meaning"),
                   lambda p: p.update(destinationAddressPrefix=self.arm.config["network"]["subnetPrefixes"]["management"]),
                   lambda p: p.update(destinationAddressPrefix=self.arm.config["network"]["subnetPrefixes"]["agw"]),
                   lambda p: p.update(sourceAddressPrefix="VirtualNetwork"),
                   lambda p: p.update(destinationApplicationSecurityGroups=[{"id": self.arm.parent_id}])]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                arm = ArmFixture()
                change(arm.named(operator.RULE_NAME)["properties"])
                with self.assertRaises(operator.OperatorAccessError):
                    operator.cleanup_disabled_operator(arm.config, arm)
                self.assertFalse(arm.writes())

    def test_terminal_must_already_be_a_successful_exact_all_traffic_deny(self):
        changes = [lambda p: p.update(priority=4095), lambda p: p.update(protocol="Tcp"),
                   lambda p: p.update(access="Allow"), lambda p: p.update(destinationPortRange="22"),
                   lambda p: p.update(provisioningState="Updating")]
        for change in changes:
            arm = ArmFixture()
            change(arm.named("deny-other-inbound")["properties"])
            with self.subTest(change=changes.index(change)), self.assertRaises(operator.OperatorAccessError):
                operator.cleanup_disabled_operator(arm.config, arm)
            self.assertFalse(arm.writes())
        self.arm.parent["properties"]["securityRules"].remove(self.arm.named("deny-other-inbound"))
        with self.assertRaises(operator.OperatorAccessError):
            self.cleanup()

    def test_delete_exact_child_once_wait_for_404_and_reexecution_writes_zero(self):
        before = copy.deepcopy(self.arm.parent)
        self.assertEqual(self.cleanup(), {"status": "removed", "azureWrites": 1})
        write, = self.arm.writes()
        self.assertEqual(write[:2], ("DELETE", self.arm.child_id + "?api-version=" + operator.API))
        expected = copy.deepcopy(before)
        expected["properties"]["securityRules"] = expected["properties"]["securityRules"][1:]
        self.assertEqual(operator._snapshot(self.arm.parent), operator._snapshot(expected))
        self.assertEqual(self.cleanup(), {"status": "rule-absent", "azureWrites": 0})
        self.assertEqual(len(self.arm.writes()), 1)

    def test_parent_and_group_and_child_ownership_fail_before_delete(self):
        changes = [lambda arm: arm.parent.update(id=arm.parent_id + "/foreign"),
                   lambda arm: arm.parent["tags"].update(product="foreign"),
                   lambda arm: setattr(arm, "owner_valid", False),
                   lambda arm: setattr(arm, "child_valid", False)]
        for change in changes:
            arm = ArmFixture()
            change(arm)
            with self.subTest(change=changes.index(change)), self.assertRaises(operator.OperatorAccessError):
                operator.cleanup_disabled_operator(arm.config, arm)
            self.assertFalse(arm.writes())

    def test_predelete_race_stops_and_postdelete_drift_is_not_success(self):
        def race(arm, count):
            if count == 2:
                arm.parent["tags"]["custom-owner"] = "changed"
        self.arm.get_hook = race
        with self.assertRaises(operator.OperatorAccessError):
            self.cleanup()
        self.assertFalse(self.arm.writes())
        self.arm = ArmFixture()
        self.arm.write_hook = lambda arm: arm.parent["properties"].update(subnets=[])
        with self.assertRaises(operator.OperatorAccessError):
            self.cleanup()
        self.assertEqual(len(self.arm.writes()), 1)

    def test_unknown_delete_response_is_not_retried_and_does_not_expose_body(self):
        self.arm.failure = True
        with self.assertRaises(operator.OperatorAccessError) as error:
            self.cleanup()
        self.assertNotIn("PRIVATE_SENTINEL", str(error.exception))
        self.assertEqual(len(self.arm.writes()), 1)

    def test_pending_parent_is_waited_without_resending_delete(self):
        self.arm.pending = 1
        sleeps = []
        self.assertEqual(self.cleanup(sleep=sleeps.append)["status"], "removed")
        self.assertEqual(sleeps, [2])
        self.assertEqual(len(self.arm.writes()), 1)


class LifecycleOperatorAccessTests(LifecycleFixture, unittest.TestCase):
    def test_direct_core_runs_cleanup_before_discovery_and_deployment(self):
        events = []
        self.operator_cleanup.side_effect = lambda *args: events.append("cleanup")
        transport = ScriptedTransport([{"statusCode": 201}, {"statusCode": 200,
            "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
        with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "discover_existing_network_security_groups",
                                  side_effect=lambda *args: events.append("discover") or []), \
                mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}), \
                mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False):
            lifecycle.deploy_phase(self.config, "core")
        self.assertEqual(events, ["cleanup", "discover"])
        self.operator_cleanup.assert_called_once_with(self.config, transport)
        self.assertEqual(len(transport.mutations()), 1)

    def test_direct_core_cleanup_failure_stops_before_deployment(self):
        self.operator_cleanup.side_effect = operator.OperatorAccessError("Unverified operator rule")
        with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                mock.patch.object(lifecycle, "ArmTransport") as factory, \
                self.assertRaises(lifecycle.DeploymentError):
            lifecycle.deploy_phase(self.config, "core")
        factory.return_value.request.assert_not_called()

    def test_monitoring_does_not_clean_operator_rules(self):
        transport = ScriptedTransport([{"statusCode": 201}, {"statusCode": 200,
            "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
        with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                mock.patch.object(lifecycle, "ArmTransport", return_value=transport):
            lifecycle.deploy_phase(self.config, "monitoring")
        self.operator_cleanup.assert_not_called()

    def test_native_preprovision_cleans_before_core_inputs_and_stops_on_failure(self):
        self.operator_cleanup.side_effect = operator.OperatorAccessError("Unverified operator rule")
        transport = ScriptedTransport([{"statusCode": 404, "body": None}])
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
                mock.patch.object(lifecycle, "register_providers"), \
                mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "capacity_preflight"), \
                mock.patch.object(lifecycle, "lock_and_verify_policies"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(lifecycle.DeploymentError):
            lifecycle.preprovision(self.config)
        self.operator_cleanup.assert_called_once_with(self.config, transport)
        self.assertFalse(self.paths.parameters.exists())
        self.assertTrue((self.paths.state / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
