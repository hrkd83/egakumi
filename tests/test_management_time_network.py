"""Exercise two-target NTP retirement with independent synthetic ARM data."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers import management_time_network as network
from operations import management_time_network as cli
from operations.common import OperationError
from test_fixed_nsg_migration import MemoryTransport, fixture_config


class TwoTargetTransport(MemoryTransport):
    def __init__(self, *, nsg_rule="allow-time-sync-egress", firewall_rule=True):
        config = fixture_config()
        super().__init__(config)
        self.ids = network.operation_ids(config)
        if nsg_rule:
            parent = self.resources[self.ids["nsg"]]
            parent["properties"]["securityRules"].append(self.child(parent["id"], nsg_rule, {
                "priority": 325 if nsg_rule == "allow-time-sync-egress" else 260,
                "direction": "Outbound", "access": "Allow", "protocol": "Udp",
                "sourcePortRange": ["*"], "destinationPortRange": ["123"],
                "sourceAddressPrefix": [config["network"]["subnetPrefixes"]["management"]],
                "destinationAddressPrefix": ["Internet"],
            }))
        self.add(self.ids["firewall"], "Microsoft.Network/azureFirewalls", {
            "firewallPolicy": {"id": self.ids["policy"]},
            "ipConfigurations": [{"name": "primary", "properties": {"privateIPAddress": "192.0.2.4"}}],
        })
        apim = {"name": "allow-apim-ntp", "ruleType": "NetworkRule",
                "sourceAddresses": [config["network"]["subnetPrefixes"]["apim"]],
                "destinationAddresses": ["*"], "destinationPorts": ["123"], "ipProtocols": ["UDP"]}
        vm_rule = {"name": "allow-mgmt-vm-arm", "ruleType": "NetworkRule",
                   "sourceAddresses": [config["network"]["subnetPrefixes"]["management"]],
                   "destinationAddresses": ["AzureResourceManager"], "destinationPorts": ["443"], "ipProtocols": ["TCP"]}
        ntp = {"name": "allow-mgmt-vm-ntp", "ruleType": "NetworkRule",
               "sourceAddresses": [config["network"]["subnetPrefixes"]["management"]],
               "destinationAddresses": ["*"], "destinationPorts": ["123"], "ipProtocols": ["UDP"]}
        self.add(self.ids["rcg"], "Microsoft.Network/firewallPolicies/ruleCollectionGroups", {
            "priority": 300, "ruleCollections": [
                {"name": "allow-apim-platform", "priority": 200, "ruleCollectionType": "FirewallPolicyFilterRuleCollection",
                 "action": {"type": "Allow"}, "rules": [apim]},
                {"name": "allow-vm-platform", "priority": 210, "ruleCollectionType": "FirewallPolicyFilterRuleCollection",
                 "action": {"type": "Allow"}, "rules": [vm_rule] + ([ntp] if firewall_rule else [])},
                {"name": "retained-enterprise", "priority": 220, "ruleCollectionType": "FirewallPolicyFilterRuleCollection",
                 "action": {"type": "Deny"}, "rules": [{**deepcopy(vm_rule), "name": "retained-enterprise-boundary"}]},
            ]})
        self.other_rcg = self.ids["policy"] + "/ruleCollectionGroups/rcg-egress"
        self.add(self.other_rcg, "Microsoft.Network/firewallPolicies/ruleCollectionGroups", {
            "priority": 400, "ruleCollections": [{"name": "retained", "priority": 200,
                "ruleCollectionType": "FirewallPolicyFilterRuleCollection", "action": {"type": "Allow"},
                "rules": [{"name": "retained-http", "ruleType": "ApplicationRule", "sourceAddresses": ["192.0.2.64/26"],
                           "targetFqdns": ["service.example.com"], "protocols": [{"protocolType": "Https", "port": 443}]}]}]})
        self.reject_put = False; self.lose_put = False; self.lose_delete = False; self.corrupt_put = False
        self.computed_size = None

    def vm_collection(self):
        return self.resources[self.ids["rcg"]]["properties"]["ruleCollections"][1]

    def request(self, method, path, body=None, headers=None):
        identity = path.split("?", 1)[0]
        if method == "PUT" and identity == self.ids["rcg"]:
            self.calls.append({"method": method, "path": path, "body": deepcopy(body), "headers": deepcopy(headers)})
            current = self.resources[identity]
            if self.reject_put or headers != {"If-Match": current["etag"]}:
                return {"statusCode": 412, "body": None}
            self.serial += 1
            current["properties"] = {**deepcopy(body["properties"]), "provisioningState": "Succeeded"}
            if self.computed_size is not None:
                current["properties"]["size"] = self.computed_size
            current["etag"] = '"rcg-' + str(self.serial) + '"'
            # A child update can change parent metadata, without changing its settings.
            self.resources[self.ids["policy"]]["etag"] = '"policy-' + str(self.serial) + '"'
            if self.corrupt_put:
                current["properties"]["ruleCollections"][0]["rules"][0]["destinationPorts"] = ["124"]
            if self.lose_put:
                self.lose_put = False
                raise OSError("Synthetic accepted PUT response was lost")
            return {"statusCode": 200, "body": deepcopy(current)}
        result = super().request(method, path, body=body, headers=headers)
        if method == "DELETE" and self.lose_delete:
            self.lose_delete = False
            raise OSError("Synthetic accepted DELETE response was lost")
        return result


class RetirementTests(unittest.TestCase):
    def run_operation(self, transport, *, execute=True, record=None, **kwargs):
        return network.retire_management_time_network(transport.config, transport, execute=execute,
            record=record or (lambda event, value: None), wait_seconds=0, max_checks=2, **kwargs)

    def test_plan_has_zero_writes_and_execute_changes_exactly_two_targets(self):
        transport = TwoTargetTransport()
        before = deepcopy(transport.resources)
        plan = self.run_operation(transport, execute=False)
        self.assertEqual((plan["plannedNsgDeletes"], plan["plannedFirewallUpdates"], plan["azureWrites"]), (1, 1, 0))
        self.assertFalse(transport.mutations())
        result = self.run_operation(transport)
        self.assertEqual(result["azureWrites"], 2)
        writes = transport.mutations()
        self.assertEqual([(item["method"], item["path"].split("?")[0]) for item in writes], [
            ("DELETE", transport.ids["allow-time-sync-egress"]), ("PUT", transport.ids["rcg"])])
        self.assertTrue(all(item["headers"]["If-Match"] not in ("*", '"*"') for item in writes))
        for identity, value in before.items():
            if identity not in (transport.ids["nsg"], transport.ids["rcg"], transport.ids["policy"]):
                self.assertEqual(transport.resources[identity], value, identity)
        expected_nsg = deepcopy(before[transport.ids["nsg"]])
        expected_nsg["properties"]["securityRules"] = [rule for rule in expected_nsg["properties"]["securityRules"]
                                                      if rule["name"] != "allow-time-sync-egress"]
        self.assertEqual(network._snapshot(transport.resources[transport.ids["nsg"]]), network._snapshot(expected_nsg))
        expected_fw = deepcopy(before[transport.ids["rcg"]]["properties"])
        expected_fw["ruleCollections"][1]["rules"].pop()
        self.assertEqual(network._clean(transport.resources[transport.ids["rcg"]]["properties"]), network._clean(expected_fw))
        self.assertEqual(network._clean(transport.resources[transport.ids["policy"]]), network._clean(before[transport.ids["policy"]]))

    def test_original_260_tuple_is_also_retired_without_staging_or_other_rule_changes(self):
        transport = TwoTargetTransport(nsg_rule="allow-time-sync")
        result = self.run_operation(transport)
        self.assertEqual(result["azureWrites"], 2)
        self.assertEqual(transport.mutations()[0]["path"].split("?")[0], transport.ids["allow-time-sync"])

    def test_documented_readonly_size_is_not_put_and_its_recomputed_value_is_accepted(self):
        transport = TwoTargetTransport()
        # Independent synthetic values, unrelated to any deployed environment.
        transport.resources[transport.ids["rcg"]]["properties"]["size"] = "8.75MB"
        transport.computed_size = "7.25MB"
        before_rules = deepcopy(transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"])
        result = self.run_operation(transport)
        self.assertEqual(result["azureWrites"], 2)
        put, = [call for call in transport.mutations() if call["method"] == "PUT"]
        self.assertNotIn("size", put["body"]["properties"])
        self.assertNotIn("provisioningState", put["body"]["properties"])
        expected = before_rules
        expected[1]["rules"].pop()
        self.assertEqual(put["body"]["properties"]["ruleCollections"], expected)
        self.assertEqual(transport.resources[transport.ids["rcg"]]["properties"]["size"], "7.25MB")
        transport.calls.clear()
        self.assertEqual(self.run_operation(transport)["azureWrites"], 0)

    def test_malformed_size_or_unknown_writable_property_stops_before_any_mutation(self):
        for size in (None, 1, True, {}, [], "", "size\n", "x" * 257):
            transport = TwoTargetTransport()
            transport.resources[transport.ids["rcg"]]["properties"]["size"] = size
            with self.subTest(size=size), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())
        transport = TwoTargetTransport()
        transport.resources[transport.ids["rcg"]]["properties"].update(size="8.75MB", unknownWritableSetting=True)
        with self.assertRaises(network.ManagementTimeNetworkError):
            self.run_operation(transport)
        self.assertFalse(transport.mutations())

    def test_false_ipv6_flags_are_retained_for_all_non_target_rules(self):
        transport = TwoTargetTransport()
        for collection in transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"]:
            for rule in collection["rules"]:
                rule["ipv6Rule"] = False
        before_rules = deepcopy(transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"])
        result = self.run_operation(transport)
        self.assertEqual(result["azureWrites"], 2)
        put, = [call for call in transport.mutations() if call["method"] == "PUT"]
        expected = before_rules
        expected[1]["rules"].pop()
        self.assertEqual(put["body"]["properties"]["ruleCollections"], expected)
        self.assertEqual(transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"], expected)
        for collection in expected:
            for rule in collection["rules"]:
                self.assertIs(rule["ipv6Rule"], False)

    def test_true_non_target_ipv6_flag_is_not_removed_or_changed(self):
        transport = TwoTargetTransport()
        transport.vm_collection()["rules"][-1]["ipv6Rule"] = False
        retained = transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"][2]["rules"][0]
        retained["ipv6Rule"] = True
        expected = deepcopy(retained)
        self.assertEqual(self.run_operation(transport)["azureWrites"], 2)
        put, = [call for call in transport.mutations() if call["method"] == "PUT"]
        self.assertEqual(put["body"]["properties"]["ruleCollections"][2]["rules"][0], expected)
        actual = transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"][2]["rules"][0]
        self.assertEqual(actual, expected)
        self.assertIs(actual["ipv6Rule"], True)

    def test_target_true_or_nonboolean_ipv6_flags_stop_before_any_write(self):
        for value in (True, None, 0, 1, "false", [], {}):
            transport = TwoTargetTransport()
            transport.vm_collection()["rules"][-1]["ipv6Rule"] = value
            before = deepcopy(transport.resources)
            with self.subTest(value=value), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())
            self.assertEqual(transport.resources, before)

    def test_non_target_nonboolean_ipv6_flags_stop_before_any_write(self):
        for value in (None, 0, 1, "true", [], {}):
            transport = TwoTargetTransport()
            transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"][0]["rules"][0]["ipv6Rule"] = value
            with self.subTest(value=value), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())

    def test_absent_targets_are_idempotent_and_partial_states_only_change_the_remaining_target(self):
        for rule, firewall, writes in ((None, False, 0), (None, True, 1), ("allow-time-sync-egress", False, 1)):
            transport = TwoTargetTransport(nsg_rule=rule, firewall_rule=firewall)
            with self.subTest(nsg=rule, firewall=firewall):
                self.assertEqual(self.run_operation(transport)["azureWrites"], writes)
                transport.calls.clear()
                self.assertEqual(self.run_operation(transport)["azureWrites"], 0)
                self.assertFalse(transport.mutations())

    def test_phc_gate_failure_or_missing_vm_causes_no_network_write(self):
        for mode in ("failed", "absent"):
            transport = TwoTargetTransport()
            before = deepcopy(transport.resources)
            if mode == "failed":
                transport.resources.pop(transport.host_time_command_id)
            else:
                transport.resources.pop(transport.vm_id)
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())
            self.assertEqual(transport.resources[transport.ids["nsg"]], before[transport.ids["nsg"]])
            self.assertEqual(transport.resources[transport.ids["rcg"]], before[transport.ids["rcg"]])

    def test_modified_staged_or_duplicate_nsg_targets_stop_before_any_write(self):
        for mode in ("priority", "source", "destination", "port", "etag", "both"):
            transport = TwoTargetTransport()
            parent = transport.resources[transport.ids["nsg"]]
            child = next(row for row in parent["properties"]["securityRules"] if row["name"] == "allow-time-sync-egress")
            if mode == "priority": child["properties"]["priority"] = 1009
            elif mode == "source": child["properties"]["sourceAddressPrefix"] = "*"
            elif mode == "destination": child["properties"]["destinationAddressPrefix"] = "192.0.2.123/32"
            elif mode == "port": child["properties"]["destinationPortRange"] = "319"
            elif mode == "etag": child.pop("etag")
            else:
                other = deepcopy(child); other["name"] = "allow-time-sync"; other["id"] = transport.ids["allow-time-sync"]
                other["properties"]["priority"] = 260; parent["properties"]["securityRules"].append(other)
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())

    def test_modified_duplicate_or_unknown_firewall_shapes_stop_before_any_write(self):
        for mode in ("destination", "port", "duplicate", "outside", "priority", "etag", "unknown"):
            transport = TwoTargetTransport()
            rule = transport.vm_collection()["rules"][-1]
            if mode == "destination": rule["destinationAddresses"] = ["192.0.2.123/32"]
            elif mode == "port": rule["destinationPorts"] = ["319"]
            elif mode == "duplicate": transport.vm_collection()["rules"].append(deepcopy(rule))
            elif mode == "outside":
                transport.resources[transport.ids["rcg"]]["properties"]["ruleCollections"][0]["rules"].append(rule)
                transport.vm_collection()["rules"].pop()
            elif mode == "priority": transport.vm_collection()["priority"] = 211
            elif mode == "etag": transport.resources[transport.ids["rcg"]].pop("etag")
            else: transport.vm_collection()["rules"][0]["unknownFutureProperty"] = True
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())

    def test_changed_ownership_or_association_causes_no_write(self):
        for mode in ("group", "nsg-subnet", "nic", "policy"):
            transport = TwoTargetTransport()
            if mode == "group": transport.resources[transport.ids["nwGroup"]]["tags"]["product"] = "foreign"
            elif mode == "nsg-subnet": transport.resources[transport.ids["nsg"]]["properties"]["subnets"] = []
            elif mode == "nic": transport.resources[transport.ids["nic"]]["properties"]["virtualMachine"] = {"id": "/synthetic/foreign-vm"}
            else: transport.resources[transport.ids["firewall"]]["properties"]["firewallPolicy"] = {"id": "/synthetic/foreign-policy"}
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())

    def test_latest_etag_or_non_target_change_prevents_the_first_write(self):
        for mode in ("etag", "non-target"):
            transport = TwoTargetTransport(); counts = {}
            def race(current, identity):
                if identity != current.ids["nsg"]: return
                counts[identity] = counts.get(identity, 0) + 1
                if counts[identity] == 2:
                    parent = current.resources[identity]
                    if mode == "etag": parent["etag"] = '"concurrent-parent"'
                    else: parent["properties"]["securityRules"][0]["properties"]["description"] = "Concurrent synthetic edit"
            transport.get_hook = race
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertFalse(transport.mutations())

    def test_accepted_but_lost_delete_response_is_not_replayed_or_followed_by_firewall_write(self):
        transport = TwoTargetTransport(); transport.lose_delete = True
        with self.assertRaises(network.ManagementTimeNetworkError): self.run_operation(transport)
        self.assertEqual([call["method"] for call in transport.mutations()], ["DELETE"])
        transport.calls.clear()
        self.assertEqual(self.run_operation(transport)["azureWrites"], 1)
        self.assertEqual([call["method"] for call in transport.mutations()], ["PUT"])

    def test_concurrent_firewall_edit_after_nsg_retirement_prevents_the_put(self):
        transport = TwoTargetTransport(); counts = {}
        def race(current, identity):
            if identity != current.ids["rcg"]: return
            counts[identity] = counts.get(identity, 0) + 1
            if counts[identity] == 3:
                group = current.resources[identity]
                group["etag"] = '"concurrent-rcg"'
                group["properties"]["ruleCollections"][2]["rules"][0]["destinationPorts"] = ["444"]
        transport.get_hook = race
        with self.assertRaises(network.ManagementTimeNetworkError): self.run_operation(transport)
        self.assertEqual([call["method"] for call in transport.mutations()], ["DELETE"])

    def test_unresolved_nsg_deletion_does_not_start_the_firewall_update(self):
        transport = TwoTargetTransport(); transport.hold_pending = True
        with self.assertRaises(network.ManagementTimeNetworkError): self.run_operation(transport)
        self.assertEqual([call["method"] for call in transport.mutations()], ["DELETE"])

    def test_accepted_but_lost_firewall_response_is_not_replayed(self):
        transport = TwoTargetTransport(); transport.lose_put = True
        with self.assertRaises(network.ManagementTimeNetworkError): self.run_operation(transport)
        self.assertEqual([call["method"] for call in transport.mutations()], ["DELETE", "PUT"])
        transport.calls.clear()
        self.assertEqual(self.run_operation(transport)["azureWrites"], 0)
        self.assertFalse(transport.mutations())

    def test_rejected_or_non_target_firewall_readback_stops_without_extra_writes(self):
        for mode in ("rejected", "corrupted"):
            transport = TwoTargetTransport()
            transport.reject_put = mode == "rejected"; transport.corrupt_put = mode == "corrupted"
            with self.subTest(mode=mode), self.assertRaises(network.ManagementTimeNetworkError):
                self.run_operation(transport)
            self.assertEqual([call["method"] for call in transport.mutations()], ["DELETE", "PUT"])

    def test_write_ahead_journal_failure_prevents_first_mutation(self):
        transport = TwoTargetTransport()
        def record(event, value):
            if event == "attempt": raise OperationError("Synthetic protected journal failure")
        with self.assertRaises(OperationError): self.run_operation(transport, record=record)
        self.assertFalse(transport.mutations())

    def test_execution_without_a_protected_record_is_rejected_before_transport_use(self):
        transport = TwoTargetTransport()
        with self.assertRaises(network.ManagementTimeNetworkError):
            network.retire_management_time_network(transport.config, transport, execute=True)
        self.assertFalse(transport.calls)


class SyntheticContext:
    def __init__(self, directory):
        self.transport = TwoTargetTransport()
        self.config = self.transport.config
        self.state = {"outputs": {"synthetic": "unchanged"}}
        self.state_path = Path(directory) / "latest.json"
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.records = []; self.lock_entries = 0; self.locked_now = False; self.change_state = False

    def _resource_path(self, path):
        if not path.startswith("/subscriptions/" + self.config["subscriptionId"] + "/resourceGroups/"):
            raise OperationError("Synthetic ownership boundary")
        return path

    def request(self, method, path, api, body=None, headers=None):
        if method != "GET" and not self.locked_now: raise AssertionError("Mutation outside operation lock")
        return self.transport.request(method, path + "?api-version=" + api, body=body, headers=headers)

    def record(self, kind, value):
        self.records.append((kind, deepcopy(value)))
        if self.change_state and kind.endswith("-plan"):
            self.state_path.write_text(json.dumps({"outputs": {"synthetic": "intervening"}}), encoding="utf-8")

    @contextmanager
    def locked(self):
        self.lock_entries += 1; self.locked_now = True
        try: yield
        finally: self.locked_now = False


class OperationBoundaryTests(unittest.TestCase):
    def test_invalid_arguments_never_disclose_synthetic_private_values_or_paths(self):
        protected_path = "synthetic-protected-folder/synthetic-params.local.json"
        private_value = "synthetic-private-invalid-value"
        for suffix in (["--unknown-private-argument", private_value], ["--execute=" + protected_path]):
            stdout = io.StringIO(); stderr = io.StringIO()
            with self.subTest(suffix=suffix), patch.object(cli.OperationContext, "load") as load, \
                    redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as stopped:
                cli.main(["--config", protected_path, *suffix])
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(json.loads(stderr.getvalue()), {"status": "stopped", "invalidArguments": True})
            self.assertNotIn(protected_path, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(private_value, stdout.getvalue() + stderr.getvalue())
            load.assert_not_called()

    def test_cli_passes_explicit_runtime_and_holds_lock_for_plan_and_execute(self):
        for execute in (False, True):
            with tempfile.TemporaryDirectory(prefix="core-time-network-") as directory:
                context = SyntheticContext(directory)
                before = context.state_path.read_bytes()
                args = ["--config", "synthetic-params.json", "--runtime-root", "synthetic-runtime"]
                if execute: args.append("--execute")
                with patch.object(cli.OperationContext, "load", return_value=context) as load, redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(args), 0)
                load.assert_called_once_with(Path("synthetic-params.json"), runtime_root=Path("synthetic-runtime"))
                self.assertEqual(context.lock_entries, 1)
                self.assertEqual(context.state_path.read_bytes(), before)

    def test_intervening_state_change_stops_before_first_network_write(self):
        with tempfile.TemporaryDirectory(prefix="core-time-network-state-") as directory:
            context = SyntheticContext(directory); context.change_state = True
            with context.locked(), self.assertRaises(OperationError): cli.run(context, execute=True)
            self.assertFalse(context.transport.mutations())

    def test_adapter_rejects_unrelated_paths_queries_and_write_methods(self):
        with tempfile.TemporaryDirectory(prefix="core-time-network-adapter-") as directory:
            context = SyntheticContext(directory); adapter = cli._Transport(context)
            ids = adapter.ids
            cases = [
                ("PUT", ids["nsg"] + "?api-version=" + network.NETWORK_API),
                ("DELETE", ids["policy"] + "?api-version=" + network.NETWORK_API),
                ("GET", ids["vm"] + "?api-version=" + network.COMPUTE_API + "&$expand=instanceView"),
                ("GET", ids["command"] + "?api-version=" + network.COMPUTE_API + "&$expand=other"),
                ("GET", ids["command"] + "?api-version=" + network.COMPUTE_API + "&api-version=" + network.COMPUTE_API),
                ("GET", ids["nsg"] + "?api-version=" + network.NETWORK_API + "&unreviewed=true"),
                ("GET", "https://example.com" + ids["vm"] + "?api-version=" + network.COMPUTE_API),
                ("POST", ids["command"] + "?api-version=" + network.COMPUTE_API + "&$expand=instanceView"),
            ]
            for method, path in cases:
                with self.subTest(method=method, path=path), self.assertRaises(OperationError):
                    adapter.request(method, path)
            with self.assertRaises(OperationError):
                adapter.request("GET", ids["nsg"] + "?api-version=" + network.NETWORK_API,
                                headers={"Authorization": "synthetic-override"})
            self.assertFalse(context.transport.calls)


if __name__ == "__main__":
    unittest.main()
