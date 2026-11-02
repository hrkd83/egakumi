"""API公開後の基盤再配置が、既存のAPI外向き宛先だけを保持することを確認する。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, lifecycle


class ApimEgressRetentionTests(LifecycleFixture, unittest.TestCase):
    def snapshots(self):
        name = "afwp-" + self.config["resourceStem"]
        policy_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                     f"{self.config['resourceGroupNames']['nw']}/providers/Microsoft.Network/firewallPolicies/{name}")
        policy = {"statusCode": 200, "body": {
            "id": policy_id, "name": name, "type": "Microsoft.Network/firewallPolicies",
            "location": self.config["location"],
            "tags": {"product": self.config["productName"], "environment": self.config["environmentName"],
                     "azd-env-name": self.config["environmentName"]}}}
        group = {"statusCode": 200, "body": {
            "id": policy_id + "/ruleCollectionGroups/rcg-egress", "properties": {
                "provisioningState": "Succeeded", "ruleCollections": [{
                    "name": "allow-apim-egress", "priority": 200,
                    "ruleCollectionType": "FirewallPolicyFilterRuleCollection", "action": {"type": "Allow"},
                    "rules": [{"name": "apim-approved-services", "ruleType": "ApplicationRule",
                        "sourceAddresses": [self.config["network"]["subnetPrefixes"]["apim"]],
                        "protocols": [{"protocolType": "Https", "port": 443}], "terminateTLS": False,
                        "targetFqdns": ["graph.microsoft.com", "management.azure.com"]}]}]}}}
        return policy, group

    def test_only_owned_apim_https_destinations_are_read_and_returned(self):
        policy, group = self.snapshots()
        transport = ScriptedTransport([policy, group])
        self.assertEqual(lifecycle.discover_existing_apim_egress_fqdns(self.config, transport),
                         ["graph.microsoft.com", "management.azure.com"])
        self.assertEqual([call["path"] for call in transport.calls],
            [snapshot["body"]["id"] + "?api-version=2025-01-01" for snapshot in (policy, group)])
        self.assertFalse(transport.mutations())

    def test_missing_policy_or_missing_published_rule_is_empty(self):
        policy, group = self.snapshots()
        without_rule = copy.deepcopy(group)
        without_rule["body"]["properties"]["ruleCollections"][0]["rules"] = []
        for replies in ([{"statusCode": 404}], [policy, {"statusCode": 404}], [policy, without_rule]):
            with self.subTest(replies=len(replies)):
                transport = ScriptedTransport(replies)
                self.assertEqual(lifecycle.discover_existing_apim_egress_fqdns(self.config, transport), [])
                self.assertFalse(transport.mutations())

    def test_foreign_policy_or_failed_read_cannot_be_used_as_an_empty_snapshot(self):
        policy, _ = self.snapshots()
        foreign = copy.deepcopy(policy)
        foreign["body"]["tags"]["environment"] = "another-environment"
        for response in (foreign, {"statusCode": 403}, {"statusCode": 500}, {"statusCode": 200, "body": []}):
            with self.subTest(response=response):
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.discover_existing_apim_egress_fqdns(self.config, ScriptedTransport([response]))

    def test_changed_source_protocol_or_additional_constraints_are_not_widened(self):
        policy, group = self.snapshots()
        changes = ({"sourceAddresses": ["*"]}, {"protocols": [{"protocolType": "Http", "port": 80}]},
                   {"terminateTLS": True}, {"targetUrls": ["example.com/restricted-path"]},
                   {"sourceIpGroups": ["fixture-group"]}, {"ipv6Rule": True},
                   {"httpHeadersToInsert": [{"headerName": "X-Test", "headerValue": "fixture"}]},
                   {"targetFqdns": ["*"]}, {"targetFqdns": ["https://example.com"]})
        for fields in changes:
            with self.subTest(fields=fields):
                changed = copy.deepcopy(group)
                changed["body"]["properties"]["ruleCollections"][0]["rules"][0].update(fields)
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.discover_existing_apim_egress_fqdns(self.config, ScriptedTransport([policy, changed]))

    def test_azure_default_fields_and_extra_response_metadata_do_not_block_reprovision(self):
        """実Azureの空配列・false・読取メタデータを、通信条件の変更と誤認しない。"""
        policy, group = self.snapshots()
        rule = group["body"]["properties"]["ruleCollections"][0]["rules"][0]
        rule.update(ipv6Rule=False, httpHeadersToInsert=[], sourceIpGroups=[], destinationAddresses=[],
                    fqdnTags=[], targetUrls=[], webCategories=[], responseMetadata={"state": "observed"})
        transport = ScriptedTransport([policy, group])
        self.assertEqual(lifecycle.discover_existing_apim_egress_fqdns(self.config, transport),
                         ["graph.microsoft.com", "management.azure.com"])
        self.assertFalse(transport.mutations())

    def test_core_parameter_generation_unions_destinations_without_changing_source_configuration(self):
        self.config["network"]["firewall"]["apimAllowedFqdns"] = ["management.azure.com", "custom.example.com"]
        original = copy.deepcopy(self.config)
        values = lifecycle.parameters(self.config, "core", existing_apim_egress_fqdns=[
            "graph.microsoft.com", "MANAGEMENT.AZURE.COM"])["parameters"]
        self.assertEqual(values["configuration"]["value"]["network"]["firewall"]["apimAllowedFqdns"],
                         ["management.azure.com", "custom.example.com", "graph.microsoft.com"])
        self.assertEqual(self.config, original)
        self.assertEqual(lifecycle.parameters(self.config, "monitoring", existing_apim_egress_fqdns=[
            "graph.microsoft.com"])["parameters"]["configuration"]["value"]["network"]["firewall"]["apimAllowedFqdns"],
                         original["network"]["firewall"]["apimAllowedFqdns"])

    def test_real_preprovision_keeps_published_destinations_and_empty_api_definitions_only_in_generated_file(self):
        policy, group = self.snapshots()
        # 前段の監視、NSG、UDR、APIM、Portal確認を初回状態にし、最後のFWだけを既存にする。
        transport = ScriptedTransport([{"statusCode": 404}] * 11 + [policy, group])
        self.config["apiDefinitions"] = []
        original = copy.deepcopy(self.config)
        source = self.root / "params.json"
        source.write_text(json.dumps(self.config), encoding="utf-8")
        before = source.read_bytes()
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "lock_and_verify_policies"), mock.patch.object(lifecycle, "configure_app_insights_cap"), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        generated = json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["configuration"]["value"]
        self.assertIn("graph.microsoft.com", generated["network"]["firewall"]["apimAllowedFqdns"])
        self.assertEqual(generated["apiDefinitions"], [])
        self.assertEqual(self.config, original)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(transport.mutations())


if __name__ == "__main__":
    unittest.main()
