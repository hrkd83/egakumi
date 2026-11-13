"""DNS 子規則の限定回収を合成構成とオフライン ARM で検証する。"""
import contextlib
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configuration_fixtures import fixture
from deployment import lifecycle
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers import dns_nsg
from lifecycle_fixtures import LifecycleFixture, ScriptedTransport
from operations import nsg_dns


class DnsArmFixture:
    """合成資源だけを返し、未知の通信と親 PUT を拒否する。"""
    def __init__(self):
        self.config = normalize_configuration(fixture())
        self.group_id = ("/subscriptions/" + self.config["subscriptionId"]
                         + "/resourceGroups/" + self.config["resourceGroupNames"]["nw"])
        self.prefix = self.group_id + "/providers/Microsoft.Network/"
        self.stem = self.config["resourceStem"]
        self.resources, self.calls, self.counts = {}, [], {}
        self.get_hook = self.delete_hook = None
        self.unknown_response = False
        self.pending = 0
        self.stale_child = None
        self.resources[self.group_id] = {"id": self.group_id, "tags": self.tags()}
        vnet = self.resource("virtualNetworks", "vnet-", {"dhcpOptions": {"dnsServers": []}})
        policy = self.resource("firewallPolicies", "afwp-", {"dnsSettings": {
            "servers": [], "enableProxy": True}})
        self.resource("azureFirewalls", "afw-", {"firewallPolicy": {"id": policy["id"]}})
        self.vnet_id, self.policy_id = vnet["id"], policy["id"]
        self.parents = []
        for suffix, protocols in dns_nsg.RETIRED_RULES.items():
            parent = self.resource("networkSecurityGroups", "nsg-", {})
            del self.resources[parent["id"]]
            parent["id"] += "-" + suffix
            parent["name"] += "-" + suffix
            self.resources[parent["id"]] = parent
            parent["properties"].update(securityRules=[], defaultSecurityRules=[],
                subnets=[{"id": vnet["id"] + "/subnets/synthetic"}],
                networkInterfaces=[{"id": self.prefix + "networkInterfaces/synthetic"}],
                flowLogs=[{"id": parent["id"] + "/flowLogs/synthetic"}], flushConnection=False)
            for protocol, priority in protocols.items():
                self.add_rule(parent, "allow-dns-" + protocol.lower(), {
                    "priority": priority, "direction": "Outbound", "access": "Allow",
                    "protocol": protocol, "sourcePortRange": "*", "destinationPortRange": "53",
                    "sourceAddressPrefix": "*", "destinationAddressPrefix": dns_nsg.DNS_ADDRESS})
            self.add_rule(parent, "same-priority-inbound", {
                "priority": next(iter(protocols.values())), "direction": "Inbound",
                "access": "Allow", "protocol": "Tcp", "sourcePortRange": "*",
                "destinationPortRange": "443", "sourceAddressPrefix": "*",
                "destinationAddressPrefix": "*"})
            self.add_rule(parent, "custom-preserved", {"priority": 500, "direction": "Outbound",
                "access": "Deny", "protocol": "*", "sourcePortRange": "*",
                "destinationPortRange": "*", "sourceAddressPrefix": "*",
                "destinationAddressPrefix": "*", "description": "Synthetic custom rule"})
            self.parents.append(parent["id"])

    def tags(self):
        return {"product": self.config["productName"], "environment": self.config["environmentName"],
                "azd-env-name": self.config["environmentName"], "custom-owner": "preserved"}

    def resource(self, kind, prefix, props):
        name = prefix + self.stem
        resource_id = self.prefix + kind + "/" + name
        value = {"id": resource_id, "name": name, "type": "Microsoft.Network/" + kind,
                 "location": self.config["location"], "tags": self.tags(), "etag": '"before"',
                 "properties": {"provisioningState": "Succeeded", **props}}
        self.resources[resource_id] = value
        return value

    def add_rule(self, parent, name, props):
        value = {"id": parent["id"] + "/securityRules/" + name, "name": name,
                 "type": dns_nsg.NSG_TYPE + "/securityRules", "etag": '"child"',
                 "properties": {"provisioningState": "Succeeded", **props}}
        parent["properties"]["securityRules"].append(value)

    def rule(self, index=0, name="allow-dns-tcp"):
        return next(item for item in self.resources[self.parents[index]]["properties"]["securityRules"]
                    if item["name"] == name)

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))
        resource_id = path.partition("?")[0]
        self.counts[resource_id] = self.counts.get(resource_id, 0) + 1
        if method == "GET":
            if self.get_hook:
                self.get_hook(self, resource_id, self.counts[resource_id])
            if "/securityRules/" in resource_id:
                parent, name = resource_id.rsplit("/securityRules/", 1)
                value = next((rule for rule in self.resources[parent]["properties"]["securityRules"]
                              if rule["name"] == name), None)
                if value is None and self.stale_child and self.stale_child["id"] == resource_id:
                    value, self.stale_child = self.stale_child, None
            else:
                value = self.resources.get(resource_id)
            value = copy.deepcopy(value)
            if value and resource_id in self.parents and self.writes() and self.pending:
                self.pending -= 1
                value["properties"]["provisioningState"] = "Updating"
            return {"statusCode": 200 if value is not None else 404, "body": value}
        if method == "DELETE" and "/securityRules/" in resource_id:
            parent, name = resource_id.rsplit("/securityRules/", 1)
            value = self.resources[parent]
            rule = next(item for item in value["properties"]["securityRules"] if item["name"] == name)
            if headers:
                assert headers == {"If-Match": rule["etag"]}
            value["properties"]["securityRules"].remove(rule)
            value["etag"] = '"after-' + str(len(self.writes())) + '"'
            value["systemData"] = {"lastModifiedByType": "Application"}
            if self.delete_hook:
                self.delete_hook(self, parent)
            if self.unknown_response:
                raise RuntimeError("SYNTHETIC_PRIVATE_BODY")
            return {"statusCode": 202}
        raise AssertionError("Unexpected offline ARM operation")

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class DnsNsgTests(unittest.TestCase):
    def setUp(self):
        self.arm = DnsArmFixture()

    def run_retirement(self, **kwargs):
        return dns_nsg.retire_dns_rules(self.arm.config, self.arm, **kwargs)

    def test_plan_is_read_only_and_records_complete_before_inventory(self):
        records = []
        result = self.run_retirement(record=lambda event, value: records.append((event, value)))
        self.assertEqual(result, {"status": "planned", "plannedDeletes": 6, "azureWrites": 0})
        self.assertFalse(self.arm.writes())
        plan = records[0][1]
        self.assertEqual(len(plan["parents"]), 3)
        self.assertEqual(len(plan["dns"]), 3)
        self.assertEqual(len(plan["targets"]), 6)

    def test_only_six_exact_children_are_deleted_and_reexecution_has_zero_writes(self):
        before = copy.deepcopy(self.arm.resources)
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 6)
        self.assertTrue(all(row[0] == "DELETE" and row[2] is None for row in self.arm.writes()))
        self.assertEqual(len({row[1] for row in self.arm.writes()}), 6)
        for resource_id, old in before.items():
            wanted = copy.deepcopy(old)
            if resource_id in self.arm.parents:
                wanted["properties"]["securityRules"] = [rule for rule in wanted["properties"]["securityRules"]
                    if rule["name"] not in {"allow-dns-tcp", "allow-dns-udp"}]
                self.assertEqual(dns_nsg._snapshot(self.arm.resources[resource_id]), dns_nsg._snapshot(wanted))
            else:
                self.assertEqual(self.arm.resources[resource_id], wanted)
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 0)
        self.assertEqual(len(self.arm.writes()), 6)

    def test_new_network_and_partial_retirement_are_idempotent(self):
        self.arm.resources.clear()
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 0)
        self.arm = DnsArmFixture()
        for parent_id in self.arm.parents:
            rules = self.arm.resources[parent_id]["properties"]["securityRules"]
            rules[:] = [rule for rule in rules if rule["name"] != "allow-dns-tcp"]
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 3)
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 0)

    def test_late_shape_mismatch_stops_before_any_delete(self):
        changes = [{"priority": 271}, {"direction": "Inbound"}, {"access": "Deny"},
                   {"protocol": "Tcp"}, {"destinationPortRange": "54"},
                   {"sourcePortRange": "53"}, {"sourceAddressPrefix": "VirtualNetwork"},
                   {"destinationAddressPrefix": "AzurePlatformDNS"}, {"description": "Custom rule"},
                   {"destinationApplicationSecurityGroups": []}]
        for change in changes:
            with self.subTest(change=change):
                self.arm = DnsArmFixture()
                self.arm.rule(2, "allow-dns-udp")["properties"].update(change)
                with self.assertRaises(dns_nsg.DnsNsgError):
                    self.run_retirement(execute=True)
                self.assertFalse(self.arm.writes())

    def test_azure_single_value_plural_form_is_safe_but_extra_destination_is_not(self):
        props = self.arm.rule()["properties"]
        props["destinationAddressPrefixes"] = [props.pop("destinationAddressPrefix")]
        props.update(sourcePortRanges=[], sourceAddressPrefixes=[], description="")
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 6)
        self.arm = DnsArmFixture()
        props = self.arm.rule()["properties"]
        props["destinationAddressPrefixes"] = [props.pop("destinationAddressPrefix"), "Internet"]
        with self.assertRaises(dns_nsg.DnsNsgError):
            self.run_retirement(execute=True)
        self.assertFalse(self.arm.writes())

    def test_configuration_custom_dns_or_invalid_type_stops_before_arm(self):
        for value in ([dns_nsg.DNS_ADDRESS], None, ""):
            for section in ("network", "firewall"):
                with self.subTest(value=value, section=section):
                    self.arm = DnsArmFixture()
                    config = self.arm.config["network"]
                    (config if section == "network" else config["firewall"])["dnsServers"] = value
                    with self.assertRaises(dns_nsg.DnsNsgError):
                        self.run_retirement(execute=True)
                    self.assertFalse(self.arm.calls)

    def test_custom_live_dns_missing_dependency_and_wrong_association_stop(self):
        def custom_vnet(arm):
            arm.resources[arm.vnet_id]["properties"]["dhcpOptions"]["dnsServers"] = [dns_nsg.DNS_ADDRESS]
        def custom_policy(arm):
            arm.resources[arm.policy_id]["properties"]["dnsSettings"]["servers"] = [dns_nsg.DNS_ADDRESS]
        def custom_firewall(arm):
            arm.resources[arm.prefix + "azureFirewalls/afw-" + arm.stem]["properties"]["dnsSettings"] = {"dnsServers": [dns_nsg.DNS_ADDRESS]}
        def wrong_association(arm):
            arm.resources[arm.prefix + "azureFirewalls/afw-" + arm.stem]["properties"]["firewallPolicy"] = {"id": arm.policy_id + "-other"}
        for change in (custom_vnet, custom_policy, custom_firewall, wrong_association,
                       lambda arm: arm.resources.pop(arm.policy_id)):
            self.arm = DnsArmFixture()
            change(self.arm)
            with self.subTest(change=change.__name__), self.assertRaises(dns_nsg.DnsNsgError):
                self.run_retirement(execute=True)
            self.assertFalse(self.arm.writes())

    def test_parent_group_and_child_identity_mismatch_stop(self):
        changes = [lambda arm: arm.resources[arm.parents[2]]["tags"].update(product="foreign"),
                   lambda arm: arm.resources[arm.group_id]["tags"].update(environment="foreign"),
                   lambda arm: arm.rule().update(id=arm.rule()["id"] + "-other")]
        for change in changes:
            self.arm = DnsArmFixture()
            change(self.arm)
            with self.subTest(change=changes.index(change)), self.assertRaises(dns_nsg.DnsNsgError):
                self.run_retirement(execute=True)
            self.assertFalse(self.arm.writes())

    def test_predelete_drift_stops_and_postdelete_association_change_is_failure(self):
        def change(arm, resource_id, count):
            if resource_id == arm.parents[1] and count == 2:
                arm.resources[resource_id]["tags"]["custom-owner"] = "changed"
        self.arm.get_hook = change
        with self.assertRaises(dns_nsg.DnsNsgError):
            self.run_retirement(execute=True)
        self.assertFalse(self.arm.writes())
        self.arm = DnsArmFixture()
        self.arm.delete_hook = lambda arm, parent: arm.resources[parent]["properties"].update(subnets=[])
        with self.assertRaises(dns_nsg.DnsNsgError):
            self.run_retirement(execute=True)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_pending_parent_waits_before_next_delete_and_unknown_response_never_resends(self):
        self.arm.pending = 1
        sleeps = []
        self.assertEqual(self.run_retirement(execute=True, sleep=sleeps.append)["azureWrites"], 6)
        self.assertEqual(sleeps, [2])
        self.arm = DnsArmFixture()
        self.arm.unknown_response = True
        with self.assertRaises(dns_nsg.DnsNsgError) as error:
            self.run_retirement(execute=True)
        self.assertNotIn("SYNTHETIC_PRIVATE_BODY", str(error.exception))
        self.assertEqual(len(self.arm.writes()), 1)

    def test_dns_metadata_changes_are_ignored_and_stale_child_waits_for_absence(self):
        old_child = copy.deepcopy(self.arm.rule())
        old_child["properties"]["provisioningState"] = "Deleting"
        def metadata(arm, resource_id, count):
            if resource_id == arm.policy_id:
                arm.resources[resource_id]["etag"] = '"read-' + str(count) + '"'
                arm.resources[resource_id]["systemData"] = {"lastModifiedByType": "Application"}
        def stale(arm, parent_id):
            if len(arm.writes()) == 1:
                arm.stale_child = old_child
        self.arm.get_hook, self.arm.delete_hook = metadata, stale
        sleeps = []
        self.assertEqual(self.run_retirement(execute=True, sleep=sleeps.append)["azureWrites"], 6)
        self.assertEqual(sleeps, [2])

    def test_child_and_parent_equivalent_condition_forms_compare_canonically(self):
        original = self.arm.request
        def request(method, path, body=None, headers=None):
            response = original(method, path, body, headers)
            if method == "GET" and "/securityRules/" in path and response["statusCode"] == 200:
                props = response["body"]["properties"]
                props["destinationAddressPrefixes"] = [props.pop("destinationAddressPrefix")]
            return response
        self.arm.request = request
        self.assertEqual(self.run_retirement(execute=True)["azureWrites"], 6)

    def test_timeout_is_not_success_and_never_retries_a_delete(self):
        self.arm.pending = 10
        with self.assertRaises(dns_nsg.DnsNsgError):
            self.run_retirement(execute=True, max_checks=1)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_final_readback_checks_the_other_two_parents_after_last_delete(self):
        def drift(arm, parent_id):
            if len(arm.writes()) == 6:
                arm.resources[arm.parents[0]]["properties"]["networkInterfaces"] = []
        self.arm.delete_hook = drift
        with self.assertRaises(dns_nsg.DnsNsgError):
            self.run_retirement(execute=True)
        self.assertEqual(len(self.arm.writes()), 6)

    def test_missing_plan_record_prevents_delete_and_cli_execute_takes_lock(self):
        def failure(event, value):
            raise OSError("Synthetic protected storage unavailable")
        with self.assertRaises(OSError):
            self.run_retirement(execute=True, record=failure)
        self.assertFalse(self.arm.writes())
        for execute in (False, True):
            context = mock.Mock()
            context.locked.return_value = contextlib.nullcontext()
            result = {"status": "completed" if execute else "planned", "azureWrites": 0}
            args = ["--config", "synthetic-params.json"] + (["--execute"] if execute else [])
            with mock.patch.object(nsg_dns.OperationContext, "load", return_value=context), \
                    mock.patch.object(nsg_dns, "run", return_value=result) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(nsg_dns.main(args), 0)
            self.assertEqual(context.locked.call_count, int(execute))
            self.assertEqual(bool(run.call_args.kwargs.get("execute")), execute)

    def test_operation_uses_owned_context_and_records_before_deleting(self):
        def request(method, resource_id, version, headers=None):
            return self.arm.request(method, resource_id + "?api-version=" + version, headers=headers)
        context = mock.Mock(config=self.arm.config)
        context.request.side_effect = request
        records = []
        context.record.side_effect = lambda kind, value: records.append((kind, len(self.arm.writes())))
        self.assertEqual(nsg_dns.run(context)["azureWrites"], 0)
        self.assertEqual(nsg_dns.run(context, execute=True)["azureWrites"], 6)
        self.assertIn(("nsg-dns-attempt", 0), records)
        self.assertEqual(records[-1], ("nsg-dns-result", 6))


class LifecycleDnsNsgTests(LifecycleFixture, unittest.TestCase):
    def test_core_cleans_before_discovery_and_monitoring_does_not_clean(self):
        events = []
        self.dns_nsg_cleanup.side_effect = lambda *args: events.append("dns")
        self.operator_cleanup.side_effect = lambda *args: events.append("operator")
        for phase in ("core", "monitoring"):
            transport = ScriptedTransport([{"statusCode": 201}, {"statusCode": 200,
                "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
            with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                    mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                    mock.patch.object(lifecycle, "discover_existing_network_security_groups",
                                      side_effect=lambda *args: events.append("discover") or []), \
                    mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False):
                lifecycle.deploy_phase(self.config, phase)
        self.assertEqual(events, ["dns", "operator", "discover"])
        self.dns_nsg_cleanup.assert_called_once()

    def test_core_stops_before_other_mutations_when_dns_verification_fails(self):
        self.dns_nsg_cleanup.side_effect = lifecycle.DeploymentError("DNS verification stopped")
        with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                mock.patch.object(lifecycle, "ArmTransport") as factory, \
                self.assertRaises(lifecycle.DeploymentError):
            lifecycle.deploy_phase(self.config, "core")
        self.operator_cleanup.assert_not_called()
        factory.return_value.request.assert_not_called()

    def test_native_preprovision_stops_before_core_inputs_when_dns_is_unverified(self):
        self.dns_nsg_cleanup.side_effect = lifecycle.DeploymentError("DNS verification stopped")
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
        self.dns_nsg_cleanup.assert_called_once_with(self.config, transport)
        self.operator_cleanup.assert_not_called()
        self.assertFalse(self.paths.parameters.exists())


if __name__ == "__main__":
    unittest.main()
