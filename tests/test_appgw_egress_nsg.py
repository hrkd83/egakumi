"""AppGW 送信許可の限定回収を合成資源とオフライン ARM で検証する。"""
import contextlib
import copy
import ipaddress
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import appgw_egress_nsg as helper
from lifecycle_fixtures import LifecycleFixture, ScriptedTransport
from operations import nsg_appgw_egress as operation
from test_dns_nsg import DnsArmFixture


class EgressArmFixture(DnsArmFixture):
    """既存のオフライン ARM へ独立した AppGW/PE の合成資源を追加する。"""

    def __init__(self):
        super().__init__()
        self.resources = {self.group_id: {
            "id": self.group_id, "tags": self.tags()}}
        self.parents = []
        self.vnet = self.resource("virtualNetworks", "vnet-", {"subnets": []})
        self.subnets = {}
        for key in ("agw", "apim", "pep"):
            name = "snet-" + self.stem + "-" + key
            subnet = {"id": self.vnet["id"] + "/subnets/" + name,
                      "name": name, "properties": {
                          "addressPrefix": self.config["network"][
                              "subnetPrefixes"][key],
                          "networkSecurityGroup": {"id": self.prefix
                              + "networkSecurityGroups/nsg-" + self.stem
                              + "-" + key}}}
            self.vnet["properties"]["subnets"].append(subnet)
            self.subnets[key] = subnet
        self.gateway = self.resource("applicationGateways", "agw-", {
            "gatewayIPConfigurations": [{"properties": {
                "subnet": {"id": self.subnets["agw"]["id"]}}}]})
        self.parent = self.resource("networkSecurityGroups", "nsg-", {})
        del self.resources[self.parent["id"]]
        self.parent["id"] += "-agw"
        self.parent["name"] += "-agw"
        self.resources[self.parent["id"]] = self.parent
        self.parents = [self.parent["id"]]
        self.parent["properties"].update(
            securityRules=[], defaultSecurityRules=[], flushConnection=False,
            subnets=[{"id": self.subnets["agw"]["id"]}],
            flowLogs=[{"id": self.parent["id"] + "/flowLogs/synthetic"}])
        self.vault_ip = str(next(ipaddress.ip_network(
            self.config["network"]["subnetPrefixes"]["pep"]).hosts()))
        for name, (priority, port) in helper.RETIRED_RULES.items():
            self.add_rule(self.parent, name, self.conditions(
                priority, "Outbound", "Allow", "Tcp", port,
                self.config["network"]["subnetPrefixes"]["agw"],
                self.vault_ip if name == "allow-key-vault" else
                self.config["network"]["subnetPrefixes"]["apim"]))
        self.add_rule(self.parent, "preserved-inbound-deny", self.conditions(
            4096, "Inbound", "Deny", "*", "*", "*", "*"))
        self.add_rule(self.parent, "preserved-outbound-allow", self.conditions(
            500, "Outbound", "Allow", "Tcp", "443", "*", "Internet"))
        for name, priority, source, destination in (
                ("AllowVnetOutBound", 65000, "VirtualNetwork", "VirtualNetwork"),
                ("AllowInternetOutBound", 65001, "*", "Internet")):
            value = {"id": self.parent["id"] + "/defaultSecurityRules/" + name,
                     "name": name, "properties": self.conditions(
                         priority, "Outbound", "Allow", "*", "*",
                         source, destination)}
            value["properties"]["description"] = "Synthetic default permit"
            self.parent["properties"]["defaultSecurityRules"].append(value)
        self.pe = self.resource("privateEndpoints", "pe-", {})
        del self.resources[self.pe["id"]]
        self.pe["id"] += "-kv"
        self.pe["name"] += "-kv"
        self.resources[self.pe["id"]] = self.pe
        self.nic = self.resource("networkInterfaces", "nic-", {
            "privateEndpoint": {"id": self.pe["id"]},
            "ipConfigurations": [{"properties": {
                "privateIPAddress": self.vault_ip,
                "subnet": {"id": self.subnets["pep"]["id"]}}}]})
        self.pe["properties"].update(
            subnet={"id": self.subnets["pep"]["id"]},
            networkInterfaces=[{"id": self.nic["id"]}],
            privateLinkServiceConnections=[{"properties": {
                "privateLinkServiceId": "/subscriptions/"
                + self.config["subscriptionId"] + "/resourceGroups/"
                + self.config["resourceGroupNames"]["svc"]
                + "/providers/Microsoft.KeyVault/vaults/kv-" + self.stem,
                "groupIds": ["vault"],
                "privateLinkServiceConnectionState": {"status": "Approved"}}}])

    @staticmethod
    def conditions(priority, direction, access, protocol, port, source, dest):
        return {"priority": priority, "direction": direction, "access": access,
                "protocol": protocol, "sourcePortRange": "*",
                "destinationPortRange": port, "sourceAddressPrefix": source,
                "destinationAddressPrefix": dest}

    def rule(self, name="allow-apim-http"):
        return next(r for r in self.parent["properties"]["securityRules"]
                    if r["name"] == name)


class AppgwEgressNsgTests(unittest.TestCase):
    def setUp(self):
        self.arm = EgressArmFixture()

    def retire(self, **kwargs):
        return helper.retire_appgw_egress_rules(
            self.arm.config, self.arm, wait_seconds=0, **kwargs)

    def test_plan_is_read_only_and_has_all_target_and_topology_evidence(self):
        records = []
        result = self.retire(record=lambda *row: records.append(row))
        self.assertEqual(result, {"status": "planned", "plannedDeletes": 3,
                                  "azureWrites": 0})
        self.assertFalse(self.arm.writes())
        self.assertEqual(set(records[0][1]["targets"]), set(helper.RETIRED_RULES))
        self.assertEqual(set(records[0][1]["topology"]),
                         {"vnet", "applicationGateway"})

    def test_only_exact_children_deleted_and_every_other_property_preserved(self):
        before = copy.deepcopy(self.arm.resources)
        self.assertEqual(self.retire(execute=True)["azureWrites"], 3)
        self.assertEqual({c[0] for c in self.arm.writes()}, {"DELETE"})
        self.assertEqual({c[1].split("?")[0] for c in self.arm.writes()}, {
            self.arm.parent["id"] + "/securityRules/" + name
            for name in helper.RETIRED_RULES})
        for identity, old in before.items():
            if identity == self.arm.parent["id"]:
                old["properties"]["securityRules"] = [
                    r for r in old["properties"]["securityRules"]
                    if r["name"] not in helper.RETIRED_RULES]
                self.assertEqual(helper._snapshot(self.arm.resources[identity]),
                                 helper._snapshot(old))
            else:
                self.assertEqual(self.arm.resources[identity], old)
        self.assertEqual(self.retire(execute=True)["azureWrites"], 0)
        self.assertEqual(len(self.arm.writes()), 3)

    def test_new_environment_or_absent_rules_need_no_topology_or_pe(self):
        self.arm.resources.clear()
        self.assertEqual(self.retire(execute=True)["azureWrites"], 0)
        self.assertEqual(len(self.arm.calls), 1)
        self.arm = EgressArmFixture()
        self.arm.parent["properties"]["securityRules"] = []
        self.arm.resources.pop(self.arm.vnet["id"])
        self.arm.resources.pop(self.arm.pe["id"])
        self.assertEqual(self.retire(execute=True)["azureWrites"], 0)

    def test_partial_retirement_only_deletes_remaining_known_rules(self):
        self.arm.parent["properties"]["securityRules"].remove(self.arm.rule())
        self.assertEqual(self.retire(execute=True)["azureWrites"], 2)

    def test_late_rule_mismatch_or_additional_conditions_block_all_deletes(self):
        for change in ({"priority": 221}, {"direction": "Inbound"},
                       {"access": "Deny"}, {"protocol": "Udp"},
                       {"destinationPortRange": "80"},
                       {"sourcePortRange": "443"},
                       {"sourceAddressPrefix": "*"},
                       {"destinationAddressPrefix": "Internet"},
                       {"description": "custom"},
                       {"destinationApplicationSecurityGroups": []}):
            with self.subTest(change=change):
                self.arm = EgressArmFixture()
                self.arm.rule("allow-key-vault")["properties"].update(change)
                with self.assertRaises(helper.AppgwEgressNsgError):
                    self.retire(execute=True)
                self.assertFalse(self.arm.writes())

    def test_single_plural_equivalence_but_extra_destination_rejected(self):
        props = self.arm.rule()["properties"]
        props["destinationAddressPrefixes"] = [
            props.pop("destinationAddressPrefix")]
        props.update(sourceAddressPrefixes=[], sourcePortRanges=[], description="")
        self.assertEqual(self.retire(execute=True)["azureWrites"], 3)
        self.arm = EgressArmFixture()
        props = self.arm.rule()["properties"]
        props["destinationAddressPrefixes"] = [
            props.pop("destinationAddressPrefix"), "Internet"]
        with self.assertRaises(helper.AppgwEgressNsgError):
            self.retire(execute=True)
        self.assertFalse(self.arm.writes())

    def test_missing_or_changed_defaults_and_custom_outbound_deny_stop(self):
        for kind in ("missing", "changed", "deny", "flush"):
            with self.subTest(kind=kind):
                self.arm = EgressArmFixture()
                p = self.arm.parent["properties"]
                if kind == "missing":
                    p["defaultSecurityRules"].pop()
                elif kind == "changed":
                    p["defaultSecurityRules"][0]["properties"]["access"] = "Deny"
                elif kind == "flush":
                    p["flushConnection"] = True
                else:
                    self.arm.rule("preserved-outbound-allow")["properties"]["access"] = "Deny"
                with self.assertRaises(helper.AppgwEgressNsgError):
                    self.retire(execute=True)
                self.assertFalse(self.arm.writes())

    def test_owned_subnet_prefix_association_and_gateway_use_are_required(self):
        for kind in ("prefix", "nsg", "backlink", "gateway", "foreign", "missing"):
            with self.subTest(kind=kind):
                self.arm = EgressArmFixture()
                if kind == "prefix":
                    self.arm.subnets["apim"]["properties"]["addressPrefix"] = "192.0.2.0/24"
                elif kind == "nsg":
                    self.arm.subnets["pep"]["properties"]["networkSecurityGroup"]["id"] += "-other"
                elif kind == "backlink":
                    self.arm.parent["properties"]["subnets"].append(
                        {"id": self.arm.subnets["apim"]["id"]})
                elif kind == "gateway":
                    self.arm.gateway["properties"]["gatewayIPConfigurations"][0]["properties"]["subnet"]["id"] = self.arm.subnets["apim"]["id"]
                elif kind == "foreign":
                    self.arm.vnet["tags"]["product"] = "other"
                else:
                    self.arm.resources.pop(self.arm.gateway["id"])
                with self.assertRaises(helper.AppgwEgressNsgError):
                    self.retire(execute=True)
                self.assertFalse(self.arm.writes())

    def test_key_vault_endpoint_identity_connection_and_nic_scope_are_required(self):
        for kind in ("approval", "vault", "subnet", "nic", "backlink", "ip", "shape"):
            with self.subTest(kind=kind):
                self.arm = EgressArmFixture()
                p = self.arm.pe["properties"]
                c = p["privateLinkServiceConnections"][0]["properties"]
                if kind == "approval":
                    c["privateLinkServiceConnectionState"]["status"] = "Pending"
                elif kind == "vault":
                    c["privateLinkServiceId"] += "-other"
                elif kind == "subnet":
                    p["subnet"]["id"] = self.arm.subnets["apim"]["id"]
                elif kind == "nic":
                    p["networkInterfaces"][0]["id"] += "/foreign"
                elif kind == "backlink":
                    self.arm.nic["properties"]["privateEndpoint"]["id"] += "-other"
                elif kind == "ip":
                    self.arm.nic["properties"]["ipConfigurations"][0]["properties"]["privateIPAddress"] = "192.0.2.1"
                else:
                    p["privateLinkServiceConnections"] = [None]
                with self.assertRaises(helper.AppgwEgressNsgError):
                    self.retire(execute=True)
                self.assertFalse(self.arm.writes())

    def test_owned_identity_state_etag_and_group_are_required(self):
        for kind in ("tag", "state", "etag", "group", "name"):
            with self.subTest(kind=kind):
                self.arm = EgressArmFixture()
                if kind == "tag":
                    self.arm.parent["tags"]["product"] = "other"
                elif kind == "state":
                    self.arm.parent["properties"]["provisioningState"] = "Failed"
                elif kind == "etag":
                    self.arm.parent["etag"] = "*"
                elif kind == "name":
                    self.arm.parent["name"] = None
                else:
                    self.arm.resources[self.arm.group_id]["tags"]["product"] = "other"
                with self.assertRaises(helper.AppgwEgressNsgError):
                    self.retire(execute=True)
                self.assertFalse(self.arm.writes())

    def test_fresh_parent_or_live_prefix_drift_blocks_first_delete(self):
        for drift in ("parent", "prefix"):
            self.arm = EgressArmFixture()
            def change(arm, identity, count):
                if drift == "parent" and identity == arm.parent["id"] and count == 2:
                    arm.parent["tags"]["custom-owner"] = "changed"
                if drift == "prefix" and identity == arm.vnet["id"] and count == 2:
                    arm.subnets["agw"]["properties"]["addressPrefix"] = "192.0.2.0/24"
            self.arm.get_hook = change
            with self.assertRaises(helper.AppgwEgressNsgError):
                self.retire(execute=True)
            self.assertFalse(self.arm.writes())

    def test_post_delete_unrelated_change_stops_without_next_delete(self):
        self.arm.delete_hook = lambda arm, parent: arm.parent["tags"].update(
            changed="synthetic")
        with self.assertRaises(helper.AppgwEgressNsgError):
            self.retire(execute=True)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_async_deletion_waits_for_child_and_parent_then_never_resends(self):
        self.arm.pending = 2
        self.arm.stale_child = copy.deepcopy(self.arm.rule())
        self.assertEqual(self.retire(execute=True)["azureWrites"], 3)
        self.arm = EgressArmFixture()
        self.arm.pending = 5
        with self.assertRaises(helper.AppgwEgressNsgError):
            self.retire(execute=True, max_checks=2)
        self.assertEqual(len(self.arm.writes()), 1)

    def test_unknown_response_is_sanitized_and_not_automatically_retried(self):
        self.arm.unknown_response = True
        with self.assertRaises(helper.AppgwEgressNsgError) as caught:
            self.retire(execute=True)
        self.assertNotIn("SYNTHETIC_PRIVATE_BODY", str(caught.exception))
        self.assertEqual(len(self.arm.writes()), 1)

    def test_failed_protected_plan_record_prevents_any_delete(self):
        def fail(*args):
            raise OSError("synthetic storage failure")
        with self.assertRaises(OSError):
            self.retire(execute=True, record=fail)
        self.assertFalse(self.arm.writes())

    def test_operation_records_attempt_before_each_delete_and_cli_locks(self):
        context = mock.Mock(config=self.arm.config)
        context.request.side_effect = lambda method, identity, version, headers=None: self.arm.request(
            method, identity + "?api-version=" + version, headers=headers)
        records = []
        context.record.side_effect = lambda event, value: records.append(
            (event, len(self.arm.writes())))
        self.assertEqual(operation.run(context, execute=True)["azureWrites"], 3)
        self.assertEqual([n for event, n in records if event.endswith("attempt")],
                         [0, 1, 2])
        for execute in (False, True):
            context = mock.Mock()
            context.locked.return_value = contextlib.nullcontext()
            args = ["--config", "synthetic.json"] + (["--execute"] if execute else [])
            with mock.patch.object(operation.OperationContext, "load", return_value=context), \
                    mock.patch.object(operation, "run", return_value={}) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(operation.main(args), 0)
            self.assertEqual(context.locked.call_count, int(execute))
            self.assertEqual(bool(run.call_args.kwargs.get("execute")), execute)


class LifecycleAppgwEgressTests(LifecycleFixture, unittest.TestCase):
    def test_core_order_and_monitoring_skip(self):
        events = []
        self.dns_nsg_cleanup.side_effect = lambda *args: events.append("dns")
        self.appgw_egress_cleanup.side_effect = lambda *args: events.append("egress")
        self.operator_cleanup.side_effect = lambda *args: events.append("operator")
        for phase in ("core", "monitoring"):
            transport = ScriptedTransport([
                {"statusCode": 201}, {"statusCode": 200, "body": {
                    "properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
            with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                    mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                    mock.patch.object(lifecycle, "discover_existing_network_security_groups", side_effect=lambda *args: events.append("discover") or []), \
                    mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False):
                lifecycle.deploy_phase(self.config, phase)
        self.assertEqual(events, ["dns", "egress", "operator", "discover"])
        self.appgw_egress_cleanup.assert_called_once()

    def test_failed_cleanup_stops_core_before_operator_or_any_deploy(self):
        self.appgw_egress_cleanup.side_effect = lifecycle.DeploymentError("stopped")
        with mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                mock.patch.object(lifecycle, "ArmTransport") as factory, \
                self.assertRaises(lifecycle.DeploymentError):
            lifecycle.deploy_phase(self.config, "core")
        self.operator_cleanup.assert_not_called()
        factory.return_value.request.assert_not_called()

    def test_preprovision_failure_prevents_parameters_and_later_mutations(self):
        self.appgw_egress_cleanup.side_effect = lifecycle.DeploymentError("stopped")
        transport = ScriptedTransport([{"statusCode": 404, "body": None}])
        with mock.patch.object(lifecycle, "local_preflight"), \
                mock.patch.object(lifecycle, "azure_preflight"), \
                mock.patch.object(lifecycle, "register_providers"), \
                mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "capacity_preflight"), \
                mock.patch.object(lifecycle, "lock_and_verify_policies"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
                mock.patch.object(lifecycle, "protected_json"), \
                mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(lifecycle.DeploymentError):
            lifecycle.preprovision(self.config)
        self.appgw_egress_cleanup.assert_called_once_with(self.config, transport)
        self.operator_cleanup.assert_not_called()
        self.assertFalse(self.paths.parameters.exists())


if __name__ == "__main__":
    unittest.main()
