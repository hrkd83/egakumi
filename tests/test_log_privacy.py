"""query非保持と監査経路の継続を、Azureに接続せず検査する。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import log_privacy as privacy
from bicep_helpers import compile_bicep


class PrivacyTransport:
    def __init__(self, config):
        self.ids = privacy.resource_ids(config)
        self.calls = []
        workspace = self.ids["workspace"]
        self.resources = {workspace: {"id": workspace, "etag": '"workspace-v1"', "properties": {
            "retentionInDays": 90, "workspaceCapping": {"dailyQuotaGb": -1}}}}

    def request(self, method, path, body=None, headers=None):
        identifier = path.split("?", 1)[0]
        self.calls.append((method, identifier, headers))
        if method == "GET":
            value = self.resources.get(identifier)
            return {"statusCode": 200 if value else 404, "body": copy.deepcopy(value) if value else {}}
        if method == "PATCH":
            properties = self.resources[identifier]["properties"]
            properties.update(copy.deepcopy(body["properties"]))
            properties["modifiedDate"] = "provider-recomputed"
            properties["workspaceCapping"]["quotaNextResetTime"] = "provider-recomputed"
        else:
            self.resources[identifier] = {"id": identifier, **copy.deepcopy(body)}
        return {"statusCode": 200, "body": {}}


class LogPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "resourceStem": "dev-fixture",
                       "productName": "fixture", "environmentName": "dev", "location": "japaneast",
                       "resourceGroupNames": {"monitor": "rg-dev-fixture-monitor"}}
        self.transport = PrivacyTransport(self.config)

    def test_workspace_association_uses_exact_resources_and_is_safe_to_retry(self):
        ids = self.transport.ids
        self.assertEqual(privacy.existing_workspace_link(self.config, self.transport), "")
        self.assertEqual(privacy.ensure_workspace_link(self.config, self.transport), ids["dcr"])
        writes = [call for call in self.transport.calls if call[0] != "GET"]
        self.assertEqual(writes, [("PUT", ids["dcr"], {"If-None-Match": "*"}),
                                  ("PATCH", ids["workspace"], {"If-Match": '"workspace-v1"'})])
        self.assertEqual(privacy.existing_workspace_link(self.config, self.transport), ids["dcr"])
        self.assertEqual(self.transport.resources[ids["workspace"]]["properties"]["retentionInDays"], 90)
        self.transport.calls.clear()
        privacy.ensure_workspace_link(self.config, self.transport)
        self.assertTrue(all(method == "GET" for method, *_ in self.transport.calls))

    def test_unknown_workspace_transform_is_not_replaced(self):
        self.transport.resources[self.transport.ids["workspace"]]["properties"][
            "defaultDataCollectionRuleResourceId"] = "/subscriptions/foreign/transform"
        with self.assertRaises(privacy.LogPrivacyError):
            privacy.ensure_workspace_link(self.config, self.transport)
        self.assertTrue(all(method == "GET" for method, *_ in self.transport.calls))


class LogPrivacyTemplateTests(unittest.TestCase):
    def test_bicep_uses_the_same_transform_and_access_storage_export(self):
        template = compile_bicep("infra/modules/monitoring/monitoring.bicep")
        text = __import__("json").dumps(template)
        self.assertIn("WorkspaceTransforms", text)
        self.assertIn("Microsoft-Table-AGWAccessLogs", text)
        self.assertIn(privacy.PRIVACY["transformKql"], text)
        self.assertIn("AGWAccessLogs", text)
        self.assertIn("existingWorkspaceDataCollectionRuleResourceId", text)
        gateway = compile_bicep("infra/modules/ingress/application-gateway.bicep")
        resources = gateway["resources"]
        logs = next(value for value in resources if value.get("name") == "[format('diag-{0}-agw', parameters('resourceStem'))]")["properties"]
        self.assertEqual(logs, "[parameters('diagnosticSettingProperties')['agw-logs']]")
        access = next(value for value in resources if value.get("name") == "[format('diag-{0}-agw-access', parameters('resourceStem'))]")["properties"]
        self.assertEqual(access, "[parameters('diagnosticSettingProperties')['agw-access']]")
        from deployment.helpers.diagnostics import canonical_settings
        from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration
        from configuration_fixtures import fixture
        config = build_bicep_configuration(normalize_configuration(fixture()))
        config["gateway"]["skuName"] = "WAF_v2"
        canonical = canonical_settings(config, "core")
        self.assertIn("storageAccountId", canonical["agw-logs"]["properties"])
        self.assertEqual(canonical["agw-logs"]["properties"]["logs"], [{"category": "ApplicationGatewayFirewallLog", "enabled": True}])
        self.assertNotIn("storageAccountId", canonical["agw-access"]["properties"])
        self.assertEqual(canonical["agw-access"]["properties"]["logs"], [{"category": "ApplicationGatewayAccessLog", "enabled": True}])


if __name__ == "__main__":
    unittest.main()
