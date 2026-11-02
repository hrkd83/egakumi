"""query非保持と監査経路の継続を、Azureに接続せず検査する。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import log_privacy as privacy
from bicep_helpers import compile_bicep


class PrivacyTransport:
    """指定した環境の資源だけを持ち、実PUT/PATCH後のreadbackを模擬する。"""
    def __init__(self, config):
        self.ids = privacy.resource_ids(config)
        self.calls = []
        self.ignore = False
        ids = self.ids
        self.resources = {
            ids["workspace"]: {"id": ids["workspace"], "etag": '"workspace-v1"', "properties": {"retentionInDays": 90, "workspaceCapping": {"dailyQuotaGb": -1}, "features": {"enableLogAccessUsingOnlyResourcePermissions": True}}},
            ids["export"]: {"id": ids["export"], "properties": {"destination": {"resourceId": ids["storage"]}, "enable": True, "tableNames": ["AppRequests", "AppDependencies", "AppExceptions", "AppTraces", "AppEvents", "Syslog", "OperatorTable"]}},
            ids["diagnostic"]: {"id": ids["diagnostic"], "properties": {"workspaceId": ids["workspace"], "storageAccountId": ids["storage"], "logs": [{"category": "ApplicationGatewayAccessLog", "enabled": True}, {"category": "ApplicationGatewayFirewallLog", "enabled": True}], "metrics": [{"category": "AllMetrics", "enabled": False}]}}
        }

    def request(self, method, path, body=None, headers=None):
        identifier = path.split("?", 1)[0]
        self.calls.append((method, identifier, copy.deepcopy(body), headers))
        if method == "GET":
            if identifier.endswith("/diagnosticSettings"):
                return {"statusCode": 200, "body": {"value": [copy.deepcopy(self.resources[self.ids["diagnostic"]]), {"id": identifier + "/setByPolicy-LogAnalytics", "properties": {"workspaceId": "/subscriptions/foreign/workspaces/hub"}}]}}
            value = self.resources.get(identifier)
            return {"statusCode": 200 if value else 404, "body": copy.deepcopy(value) if value else {}}
        if not self.ignore:
            if method == "PATCH":
                self.resources[identifier]["properties"].update(copy.deepcopy(body["properties"]))
                self.resources[identifier]["properties"]["modifiedDate"] = "provider-recomputed"
                self.resources[identifier]["properties"].setdefault("workspaceCapping", {})["quotaNextResetTime"] = "provider-recomputed"
            elif method == "POST" and identifier.endswith("/lock"):
                self.resources[identifier[:-5]]["properties"]["state"] = "Locked"
            else:
                self.resources[identifier] = {"id": identifier, **copy.deepcopy(body)}
                if identifier == self.ids["dcr"]:
                    self.resources[identifier]["properties"]["destinations"]["logAnalytics"][0]["workspaceId"] = "provider-computed-guid"
                if identifier.endswith("/immutabilityPolicies/default"):
                    self.resources[identifier].update(etag='"policy-exact-etag"')
                    self.resources[identifier]["properties"]["state"] = "Unlocked"
        return {"statusCode": 200, "body": {}}


class LogPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "resourceStem": "dev-fixture", "productName": "fixture", "environmentName": "dev", "location": "japaneast", "tags": {"purpose": "test"}, "resourceGroupNames": {"monitor": "rg-dev-fixture-monitor", "nw": "rg-dev-fixture-nw"}, "monitoring": {"auditImmutabilityMode": "Disabled", "auditRetentionDays": 2557}}
        self.transport = PrivacyTransport(self.config)

    def test_plan_is_read_only_and_preserves_existing_exports_waf_and_external_destinations(self):
        proposal = privacy.plan(self.config, self.transport)
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))
        self.assertEqual(proposal["azureWrites"], 0)
        export = next(step for step in proposal["steps"] if step["label"] == "transformed-access-export")
        self.assertEqual(export["body"]["properties"]["tableNames"][:-1], self.transport.resources[self.transport.ids["export"]]["properties"]["tableNames"])
        self.assertEqual(export["body"]["properties"]["tableNames"][-1], "AGWAccessLogs")
        base = next(step for step in proposal["steps"] if step["label"] == "access-law-only")
        self.assertNotIn("storageAccountId", base["body"]["properties"])
        self.assertEqual(base["body"]["properties"]["logs"], [{"category": "ApplicationGatewayAccessLog", "enabled": True}])
        retained = next(step for step in proposal["steps"] if step["label"] == "keep-waf-remove-raw-access")
        self.assertEqual(retained["body"]["properties"]["storageAccountId"], self.transport.ids["storage"])
        self.assertEqual(retained["body"]["properties"]["logs"], [{"category": "ApplicationGatewayFirewallLog", "enabled": True}])
        self.assertEqual([v["label"] for v in proposal["steps"] if v["stage"] == "route"], ["access-law-only", "keep-waf-remove-raw-access"])
        self.assertTrue(any("setByPolicy" in item["id"] for item in proposal["unchangedDiagnostics"]))

    def test_update_readback_preserves_workspace_cap_waf_and_all_prior_tables_then_is_idempotent(self):
        proposal = privacy.plan(self.config, self.transport)
        result = privacy.apply_steps(proposal, self.transport, sleep=lambda _: None)
        self.assertEqual(result["azureWrites"], 6)
        self.assertFalse(result["runtimeTransformAndBlobVerified"])
        ids = self.transport.ids
        self.assertEqual(self.transport.resources[ids["workspace"]]["properties"]["workspaceCapping"]["dailyQuotaGb"], -1)
        self.assertEqual(len(self.transport.resources[ids["export"]]["properties"]["tableNames"]), 8)
        self.assertEqual(self.transport.resources[ids["diagnostic"]]["properties"]["logs"], [{"category": "ApplicationGatewayFirewallLog", "enabled": True}])
        self.assertEqual(self.transport.resources[ids["diagnostic"]]["properties"]["storageAccountId"], ids["storage"])
        self.assertNotIn("storageAccountId", self.transport.resources[ids["accessDiagnostic"]]["properties"])
        second = privacy.apply_steps(privacy.plan(self.config, self.transport), self.transport)
        self.assertEqual(second["azureWrites"], 0)

    def test_unknown_workspace_transform_is_not_replaced(self):
        self.transport.resources[self.transport.ids["workspace"]]["properties"]["defaultDataCollectionRuleResourceId"] = "/subscriptions/foreign/dcr/operator"
        with self.assertRaisesRegex(privacy.LogPrivacyError, "another transformation"):
            privacy.plan(self.config, self.transport)
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))

    def test_stale_plan_and_unconfirmed_update_do_not_report_success(self):
        proposal = privacy.plan(self.config, self.transport)
        self.transport.resources[self.transport.ids["workspace"]]["properties"]["retentionInDays"] = 100
        with self.assertRaisesRegex(privacy.LogPrivacyError, "plan changed"):
            privacy.apply_steps(proposal, self.transport, sleep=lambda _: None)
        other = PrivacyTransport(self.config); other.ignore = True
        with self.assertRaises(privacy.LogPrivacyError):
            privacy.apply_steps(privacy.plan(self.config, other), other, sleep=lambda _: None)

    def test_etag_only_refresh_uses_current_condition_without_overwriting_property_changes(self):
        proposal = privacy.plan(self.config, self.transport)
        self.transport.resources[self.transport.ids["workspace"]]["etag"] = '"provider-refreshed"'
        privacy.apply_steps(proposal, self.transport, stages=("prepare",))
        update = next(call for call in self.transport.calls if call[0] == "PATCH")
        self.assertEqual(update[3], {"If-Match": '"provider-refreshed"'})

    def test_stage_selection_does_not_disable_direct_access_before_export_stage(self):
        result = privacy.apply_steps(privacy.plan(self.config, self.transport), self.transport, stages=("prepare",))
        self.assertEqual(result["azureWrites"], 3)
        self.assertIn("storageAccountId", self.transport.resources[self.transport.ids["diagnostic"]]["properties"])
        self.assertNotIn("AGWAccessLogs", self.transport.resources[self.transport.ids["export"]]["properties"]["tableNames"])

    def test_rejected_new_access_diagnostic_never_changes_existing_waf_sink(self):
        privacy.apply_steps(privacy.plan(self.config, self.transport), self.transport, stages=("prepare", "export"))
        before = copy.deepcopy(self.transport.resources[self.transport.ids["diagnostic"]])
        original = self.transport.request
        def reject(method, path, body=None, headers=None):
            if method == "PUT" and path.startswith(self.transport.ids["accessDiagnostic"] + "?"):
                return {"statusCode": 409, "body": {}}
            return original(method, path, body, headers)
        self.transport.request = reject
        with self.assertRaises(privacy.LogPrivacyError):
            privacy.apply_steps(privacy.plan(self.config, self.transport), self.transport, stages=("route",))
        self.assertEqual(self.transport.resources[self.transport.ids["diagnostic"]], before)

    def test_locked_policy_is_never_shortened_and_new_lock_uses_an_exact_etag(self):
        self.config["monitoring"]["auditImmutabilityMode"] = "Locked"
        proposal = privacy.plan(self.config, self.transport)
        privacy.apply_steps(proposal, self.transport, stages=("prepare",))
        lock = next(call for call in self.transport.calls if call[0] == "POST")
        self.assertEqual(lock[3], {"If-Match": '"policy-exact-etag"'})
        policy = self.transport.resources[self.transport.ids["container"] + "/immutabilityPolicies/default"]
        policy["properties"]["immutabilityPeriodSinceCreationInDays"] = 3000
        repeated = privacy.plan(self.config, self.transport)
        self.assertFalse(any(step["required"] for step in repeated["steps"] if step["label"] in ("access-audit-lock", "access-audit-retention")))


class LogPrivacyTemplateTests(unittest.TestCase):
    def test_bicep_uses_the_same_transform_and_access_storage_export(self):
        template = compile_bicep("infra/modules/monitoring.bicep")
        text = __import__("json").dumps(template)
        self.assertIn("WorkspaceTransforms", text)
        self.assertIn("Microsoft-Table-AGWAccessLogs", text)
        self.assertIn(privacy.PRIVACY["transformKql"], text)
        self.assertIn("AGWAccessLogs", text)
        self.assertIn("existingWorkspaceDataCollectionRuleResourceId", text)
        gateway = compile_bicep("infra/modules/application-gateway.bicep")
        resources = gateway["resources"]
        logs = next(value for value in resources if value.get("name") == "[format('diag-{0}-agw', parameters('resourceStem'))]")["properties"]
        self.assertIn("storageAccountId", __import__("json").dumps(logs))
        access = next(value for value in resources if value.get("name") == "[format('diag-{0}-agw-access', parameters('resourceStem'))]")["properties"]
        self.assertNotIn("storageAccountId", access)
        self.assertEqual(access["logs"], [{"category": "ApplicationGatewayAccessLog", "enabled": True}])


if __name__ == "__main__":
    unittest.main()
