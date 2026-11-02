"""API公開時にWebSocket監査だけを有効化し、既存の出力設定を保持する。"""
import copy
import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/gateway/deploy"))
import api_publication as publish
import test_publish_apis as publication_cases
from test_standard_apis import standard_fixture
import standard_apis


def response(body, status=200):
    return {"statusCode": status, "headers": {}, "body": copy.deepcopy(body)}


class DiagnosticTransport:
    """Azureへ接続せず、同じ診断設定だけへのGET/PUTを記録する。"""
    def __init__(self, resource):
        self.resource = copy.deepcopy(resource)
        self.calls = []
        self.get_status = 200
        self.put_status = 200
        self.ignore_update = False
        self.reorder_readback = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), headers))
        if method == "GET":
            result = copy.deepcopy(self.resource)
            if self.reorder_readback and any(call[0] == "PUT" for call in self.calls):
                result["properties"]["logs"].reverse()
                for item in result["properties"]["logs"]:
                    item.setdefault("retentionPolicy", {"enabled": False, "days": 0})
            return response(result, self.get_status)
        if method == "PUT":
            if self.put_status < 300 and not self.ignore_update:
                self.resource = {"id": self.resource["id"], **copy.deepcopy(body)}
            return response(self.resource, self.put_status)
        raise AssertionError("Unexpected diagnostic operation: " + method)


class PublicationDiagnosticTests(unittest.TestCase):
    def setUp(self):
        base = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-test-fixture-"
        self.apim_id = base + "svc/providers/Microsoft.ApiManagement/service/apim-test-fixture"
        self.resource_id = self.apim_id + "/providers/Microsoft.Insights/diagnosticSettings/core-audit"
        self.workspace_id = base + "monitor/providers/Microsoft.OperationalInsights/workspaces/law-test-fixture"
        self.storage_id = base + "monitor/providers/Microsoft.Storage/storageAccounts/sttestfixturejp"
        self.state = {"outputs": {
            "monitoring": {"workspaceId": self.workspace_id},
            "auditStorage": {"collectJapan": {"id": self.storage_id}},
        }}
        self.plan = {"apimId": self.apim_id,
                     "webSocketDiagnostics": {"required": True, "resourceId": self.resource_id}}
        self.properties = {
            "workspaceId": self.workspace_id,
            "storageAccountId": self.storage_id,
            "logAnalyticsDestinationType": "Dedicated",
            "logs": [
                {"category": "GatewayLogs", "enabled": True,
                 "retentionPolicy": {"enabled": False, "days": 0}},
                {"category": "GatewayLlmLogs", "enabled": True},
                {"category": "WebSocketConnectionLogs", "enabled": False},
                {"category": "DeveloperPortalAuditLogs", "enabled": False},
            ],
            "metrics": [{"category": "AllMetrics", "enabled": False,
                         "retentionPolicy": {"enabled": False, "days": 0}}],
        }
        self.transport = DiagnosticTransport({"id": self.resource_id, "name": "core-audit",
                                              "properties": self.properties})

    def enable(self, transport=None, timeout=1):
        return publish.enable_websocket_diagnostics(self.plan, self.state, transport or self.transport,
                                                    sleep=lambda _: None,
                                                    deadline=time.monotonic() + timeout)

    def puts(self, transport=None):
        return [call for call in (transport or self.transport).calls if call[0] == "PUT"]

    def test_disabled_websocket_category_is_enabled_without_changing_other_settings(self):
        result = self.enable()
        self.assertEqual(result, {"required": True, "updated": True, "status": "enabled",
                                  "resourceId": self.resource_id})
        self.assertEqual(len(self.puts()), 1)
        expected = copy.deepcopy(self.properties)
        expected["logs"][2]["enabled"] = True
        self.assertEqual(self.puts()[0][2], {"properties": expected})
        self.assertIsNone(self.puts()[0][3])
        self.assertTrue(all(call[1].split("?")[0] == self.resource_id for call in self.transport.calls))

    def test_missing_websocket_category_is_appended(self):
        self.transport.resource["properties"]["logs"].pop(2)
        before = copy.deepcopy(self.transport.resource["properties"])
        result = self.enable()
        self.assertTrue(result["updated"])
        actual = self.puts()[0][2]["properties"]
        self.assertEqual(actual["logs"][:-1], before["logs"])
        self.assertEqual(actual["logs"][-1]["category"], "WebSocketConnectionLogs")
        self.assertIs(actual["logs"][-1]["enabled"], True)
        self.assertEqual({key: value for key, value in actual.items() if key != "logs"},
                         {key: value for key, value in before.items() if key != "logs"})

    def test_http_only_publication_does_not_read_or_disable_diagnostics(self):
        self.plan["webSocketDiagnostics"]["required"] = False
        publish.verify_websocket_diagnostics(self.plan, self.state, self.transport)
        result = self.enable()
        self.assertEqual(result, {"required": False, "updated": False, "status": "not-required"})
        self.assertEqual(self.transport.calls, [])

    def test_already_enabled_category_or_all_logs_group_is_idempotent(self):
        for all_logs in (False, True):
            with self.subTest(all_logs=all_logs):
                resource = copy.deepcopy(self.transport.resource)
                if all_logs:
                    resource["properties"]["logs"] = [{"categoryGroup": "allLogs", "enabled": True}]
                else:
                    resource["properties"]["logs"][2]["enabled"] = True
                transport = DiagnosticTransport(resource)
                result = self.enable(transport)
                self.assertFalse(result["updated"])
                self.assertEqual(result["status"], "enabled")
                self.assertEqual(self.puts(transport), [])

    def test_new_operator_category_is_preserved_from_latest_read(self):
        publish.verify_websocket_diagnostics(self.plan, self.state, self.transport)
        self.transport.resource["properties"]["logs"].append(
            {"category": "GatewayMCPLogs", "enabled": True,
             "retentionPolicy": {"enabled": False, "days": 0}})
        self.enable()
        self.assertIn({"category": "GatewayMCPLogs", "enabled": True,
                       "retentionPolicy": {"enabled": False, "days": 0}},
                      self.puts()[0][2]["properties"]["logs"])

    def test_extra_existing_destinations_are_preserved(self):
        extra = {"eventHubAuthorizationRuleId": self.apim_id.rsplit("/providers/", 1)[0]
                 + "/providers/Microsoft.EventHub/namespaces/fixture/authorizationRules/audit",
                 "eventHubName": "fixture-audit"}
        self.transport.resource["properties"].update(extra)
        self.enable()
        actual = self.puts()[0][2]["properties"]
        self.assertEqual({key: actual[key] for key in extra}, extra)

    def test_readback_can_reorder_categories_and_add_default_values(self):
        self.transport.reorder_readback = True
        self.assertEqual(self.enable()["status"], "enabled")

    def test_null_optional_fields_survive_write_and_accept_equivalent_readback(self):
        class DefaultedReadback(DiagnosticTransport):
            def request(inner, method, path, body=None, headers=None):
                result = super().request(method, path, body, headers)
                if method == "GET" and any(call[0] == "PUT" for call in inner.calls):
                    properties = result["body"]["properties"]
                    properties.pop("metrics", None)
                    for entry in properties["logs"]:
                        if entry.get("categoryGroup") is None:
                            entry.pop("categoryGroup", None)
                        if entry.get("retentionPolicy") is None:
                            entry["retentionPolicy"] = {"enabled": False, "days": 0}
                return result
        resource = copy.deepcopy(self.transport.resource)
        resource["properties"]["metrics"] = None
        for entry in resource["properties"]["logs"]:
            entry["categoryGroup"] = None
            entry["retentionPolicy"] = None
        transport = DefaultedReadback(resource)
        result = self.enable(transport)
        self.assertEqual(result["status"], "enabled")
        self.assertTrue(result["updated"])
        written = self.puts(transport)[0][2]["properties"]
        expected = copy.deepcopy(resource["properties"])
        expected["logs"][2]["enabled"] = True
        self.assertEqual(written, expected)
        self.assertIsNone(written["metrics"])
        self.assertTrue(all(entry["categoryGroup"] is None and entry["retentionPolicy"] is None
                            for entry in written["logs"]))

    def test_wrong_resource_or_destinations_and_invalid_shapes_stop_before_put(self):
        cases = ("resource", "plan-resource", "workspace", "storage", "properties", "logs", "entry", "enabled")
        for reason in cases:
            with self.subTest(reason=reason):
                plan, resource = copy.deepcopy(self.plan), copy.deepcopy(self.transport.resource)
                if reason == "resource": resource["id"] += "-other"
                elif reason == "plan-resource": plan["webSocketDiagnostics"]["resourceId"] += "-other"
                elif reason == "workspace": resource["properties"]["workspaceId"] += "-other"
                elif reason == "storage": resource["properties"]["storageAccountId"] += "other"
                elif reason == "properties": resource["properties"] = []
                elif reason == "logs": resource["properties"]["logs"] = {}
                elif reason == "entry": resource["properties"]["logs"][0] = "invalid"
                else: resource["properties"]["logs"][2]["enabled"] = "false"
                transport = DiagnosticTransport(resource)
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    publish.verify_websocket_diagnostics(plan, self.state, transport)
                self.assertEqual(self.puts(transport), [])

    def test_unreadable_or_missing_diagnostic_never_creates_new_settings(self):
        for status in (403, 404):
            with self.subTest(status=status):
                transport = DiagnosticTransport(self.transport.resource)
                transport.get_status = status
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    publish.verify_websocket_diagnostics(self.plan, self.state, transport)
                self.assertEqual(self.puts(transport), [])

    def test_failed_update_does_not_report_success(self):
        for status in (400, 403, 412):
            with self.subTest(status=status):
                transport = DiagnosticTransport(self.transport.resource)
                transport.put_status = status
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    self.enable(transport)

    def test_ignored_update_fails_readback(self):
        self.transport.ignore_update = True
        with self.assertRaises((ValueError, publish.DeploymentError)):
            self.enable(timeout=0.02)
        self.assertEqual(len(self.puts()), 1)

    def test_changed_destination_after_update_fails_readback(self):
        class ChangedDestination(DiagnosticTransport):
            def request(inner, method, path, body=None, headers=None):
                result = super().request(method, path, body, headers)
                if method == "GET" and any(call[0] == "PUT" for call in inner.calls):
                    result["body"]["properties"]["storageAccountId"] += "other"
                return result
        with self.assertRaises((ValueError, publish.DeploymentError)):
            self.enable(ChangedDestination(self.transport.resource), timeout=0.02)

    def test_standard_empty_selection_requires_websocket_diagnostics(self):
        raw, state = standard_fixture()
        config = standard_apis.resolve_publication_configuration(raw, state)
        fixture = publication_cases.PublicationTests()
        fixture.setUp()
        fixture.config["apiDefinitions"] = config["apiDefinitions"]
        plan = publish.prepare_plan(fixture.config, fixture.state)
        self.assertIs(plan["webSocketDiagnostics"]["required"], True)
        self.assertEqual(plan["webSocketDiagnostics"]["resourceId"],
                         fixture.plan["apimId"] + "/providers/Microsoft.Insights/diagnosticSettings/core-audit")


class PublicationDiagnosticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publication_cases.PublicationTests()
        self.fixture.setUp()
        self.fixture.config["apiDefinitions"][1]["apiType"] = "websocket"
        self.fixture.plan = publish.prepare_plan(self.fixture.config, self.fixture.state)
        unit = PublicationDiagnosticTests()
        unit.setUp()
        self.fixture.state["outputs"]["monitoring"] = {"workspaceId": unit.workspace_id}
        self.fixture.state["outputs"]["auditStorage"] = {"collectJapan": {"id": unit.storage_id}}
        self.diagnostic_id = self.fixture.plan["apimId"] + "/providers/Microsoft.Insights/diagnosticSettings/core-audit"
        self.fixture.inventory[self.diagnostic_id] = response({"id": self.diagnostic_id,
                                                               "properties": unit.properties})

    def test_failed_api_deployment_does_not_change_diagnostic_settings(self):
        deployment_id = self.fixture.plan["deploymentId"]
        class FailedDeployment(publication_cases.Transport):
            def request(inner, method, path, body=None, headers=None):
                if method == "PUT" and path.split("?")[0] == deployment_id:
                    inner.calls.append((method, path, copy.deepcopy(body), headers))
                    return response({}, 400)
                return super().request(method, path, body, headers)
        transport = FailedDeployment(self.fixture.inventory)
        with self.assertRaises((ValueError, publish.DeploymentError)):
            publish.execute_plan(self.fixture.config, self.fixture.state, self.fixture.plan,
                                 self.fixture.template, transport, sleep=lambda _: None)
        self.assertFalse(any(method == "PUT" and path.split("?")[0] == self.diagnostic_id
                             for method, path, _, _ in transport.calls))

    def test_success_records_verified_diagnostics_after_api_deployment(self):
        policy_id = self.fixture.plan["apimId"] + "/policies/policy"
        self.fixture.inventory[policy_id] = response({"etag": '"initial-policy"', "properties": {
            "format": "rawxml", "value": "<policies><inbound/><backend><forward-request/></backend><outbound/></policies>"}})
        transport = publication_cases.Transport(self.fixture.inventory)
        result = publish.execute_plan(self.fixture.config, self.fixture.state, self.fixture.plan,
                                      self.fixture.template, transport, sleep=lambda _: None)
        self.assertEqual(result["webSocketDiagnostics"]["status"], "enabled")
        self.assertTrue(result["webSocketDiagnostics"]["updated"])
        writes = [path.split("?")[0] for method, path, _, _ in transport.calls if method == "PUT"]
        self.assertLess(writes.index(self.fixture.plan["deploymentId"]), writes.index(self.diagnostic_id))
        self.assertLess(writes.index(self.diagnostic_id), writes.index(policy_id))

    def test_invalid_diagnostic_preflight_stops_all_cloud_writes(self):
        for failure in (403, 404, "destination"):
            with self.subTest(failure=failure):
                inventory = copy.deepcopy(self.fixture.inventory)
                if failure == "destination":
                    inventory[self.diagnostic_id]["body"]["properties"]["workspaceId"] += "-different"
                else:
                    inventory[self.diagnostic_id]["statusCode"] = failure
                transport = publication_cases.Transport(inventory)
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    publish.execute_plan(self.fixture.config, self.fixture.state, self.fixture.plan,
                                         self.fixture.template, transport, sleep=lambda _: None)
                self.assertTrue(all(method == "GET" for method, _, _, _ in transport.calls))


if __name__ == "__main__":
    unittest.main()
