"""API公開後の基盤再配置が、既存のAPI外向き宛先だけを保持することを確認する。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, lifecycle
from configuration_fixtures import api_fixture


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
                    "rules": [{"name": "allow-apim-approved-services", "ruleType": "ApplicationRule",
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

    def test_legacy_rule_destinations_are_read_without_renaming_the_snapshot(self):
        policy, group = self.snapshots()
        group["body"]["properties"]["ruleCollections"][0]["rules"][0]["name"] = "apim-approved-services"
        before = copy.deepcopy(group)
        transport = ScriptedTransport([policy, group])
        self.assertEqual(lifecycle.discover_existing_apim_egress_fqdns(self.config, transport),
                         ["graph.microsoft.com", "management.azure.com"])
        self.assertEqual(group, before)
        self.assertFalse(transport.mutations())

    def test_current_and_legacy_name_duplicates_cannot_discard_existing_targets(self):
        for names in (("allow-apim-approved-services", "apim-approved-services"),
                      ("allow-apim-approved-services", "allow-apim-approved-services"),
                      ("apim-approved-services", "apim-approved-services")):
            policy, group = self.snapshots()
            rules = group["body"]["properties"]["ruleCollections"][0]["rules"]
            rules[0]["name"] = names[0]
            duplicate = copy.deepcopy(rules[0])
            duplicate.update(name=names[1], targetFqdns=["another.example.com"])
            rules.append(duplicate)
            transport = ScriptedTransport([policy, group])
            with self.subTest(names=names), self.assertRaisesRegex(lifecycle.DeploymentError, "duplicated"):
                lifecycle.discover_existing_apim_egress_fqdns(self.config, transport)
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
                   {"sourceIpGroups": ["fixture-group"]}, {"ipv6Rule": True}, {"ipv6Rule": 0},
                   {"terminateTLS": 0},
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
        api = api_fixture()["apiDefinitions"][0]
        api.update(backendService="external", backendUrl="https://custom.example.com/api")
        self.config["apiDefinitions"] = [api]
        original = copy.deepcopy(self.config)
        values = lifecycle.parameters(self.config, "core", existing_apim_egress_fqdns=[
            "graph.microsoft.com", "CUSTOM.EXAMPLE.COM"])["parameters"]
        generated = values["configuration"]["value"]
        self.assertEqual(generated["generatedApimServiceFqdns"], ["custom.example.com", "graph.microsoft.com"])
        self.assertNotIn("apimAllowedFqdns", generated["network"]["firewall"])
        self.assertEqual(self.config, original)
        self.assertEqual(lifecycle.parameters(self.config, "monitoring", existing_apim_egress_fqdns=[
            "graph.microsoft.com"])["parameters"]["configuration"]["value"]["generatedApimServiceFqdns"],
                         ["custom.example.com"])

    def test_real_preprovision_keeps_published_destinations_and_empty_api_definitions_only_in_generated_file(self):
        policy, group = self.snapshots()
        # 前段のACR・入口資格情報2件・監視・6 NSG・5 UDR・APIM・Portalを初回状態にする。
        absent_reads = 1 + 2 + 1 + 6 + 5 + 1 + 1
        transport = ScriptedTransport([{"statusCode": 404}] * absent_reads + [policy, group])
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
        generated = json.loads(self.paths.parameters.read_text())["parameters"]["configuration"]["value"]
        self.assertIn("graph.microsoft.com", generated["generatedApimServiceFqdns"])
        self.assertNotIn("apimAllowedFqdns", generated["network"]["firewall"])
        self.assertEqual(generated["apiDefinitions"], [])
        self.assertEqual(self.config, original)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(transport.mutations())
        self.assertEqual(transport.responses, [])
        self.assertEqual([call['path'] for call in transport.calls[-2:]], [
            snapshot['body']['id'] + '?api-version=2025-01-01' for snapshot in (policy, group)])


if __name__ == "__main__":
    unittest.main()
