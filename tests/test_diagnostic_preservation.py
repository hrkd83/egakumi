"""同名Core診断の保持と、書込前に止める転送拡大を合成応答だけで確認する。"""
import copy
import json
from pathlib import Path
import unittest
from unittest import mock

from configuration_fixtures import fixture
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration
from deployment.helpers import diagnostics
from deployment import lifecycle

CANONICAL_FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures/diagnostic_canonical.json"


def configuration():
    """実ファイルの秘密を開かず、公開fixtureを通常の検証・構築変換へ通す。"""
    return build_bicep_configuration(normalize_configuration(fixture(), base_directory=Path("config")))


class Directory:
    """診断metadataだけの固定GET応答を返し、未知の書込を拒否する。"""
    def __init__(self, config, existing=True):
        self.calls, self.values = [], {}
        for key, item in diagnostics.canonical_settings(config, "core", True).items():
            if existing and key != "activity":
                self.values[item["ownerId"].casefold()] = {"id": item["ownerId"], "type": item["ownerType"], "location": item["location"], "tags": {
                    "product": config["productName"], "environment": config["environmentName"], "azd-env-name": config["environmentName"]}}
                if item["ownerId"] != item["parentId"]:
                    self.values[item["parentId"].casefold()] = {"id": item["parentId"], "type": item["parentType"]}
            if existing:
                self.values[item["id"].casefold()] = {"id": item["id"], "name": item["id"].rsplit("/", 1)[-1], "type": "Microsoft.Insights/diagnosticSettings", "properties": copy.deepcopy(item["properties"])}
        self.status = {}

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        assert method == "GET", "No cloud mutation is permitted in diagnostic controls"
        identifier = path.split("?", 1)[0].casefold()
        status = self.status.get(identifier, 200 if identifier in self.values else 404)
        return {"statusCode": status, "body": ({"error": {"code": "ResourceNotFound"}} if status == 404 else copy.deepcopy(self.values.get(identifier)))}


class DiagnosticPreservationTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.workspace = diagnostics.canonical_settings(self.config, "core")["law"]["parentId"]

    def test_canonical_settings_match_the_independent_previous_bicep_golden(self):
        cases = json.loads(CANONICAL_FIXTURE_FILE.read_text())["cases"]
        self.assertEqual(len(cases), 6)
        for case in cases:
            config = copy.deepcopy(self.config)
            config["gateway"]["skuName"] = case["gatewaySku"]
            config["network"]["firewall"]["sku"] = case["firewallTier"]
            config["infrastructureApiDefinitions"] = [{"apiType": "websocket"}] if case["websocket"] else []
            actual = {item["id"].casefold(): item["properties"] for item in diagnostics.canonical_settings(config, case["phase"], case["preserveCollection"]).values()}
            # 正本の分類・宛先関係を固定し、独立した合成fixtureのsubscriptionだけをメモリー内で束縛する。
            expected = json.loads(json.dumps(case["expected"]).replace("<AZURE_SUBSCRIPTION_ID>", config["subscriptionId"]))
            self.assertEqual(actual, expected)

    def test_metrics_only_additional_storage_survives_same_name_properties(self):
        setting = diagnostics.canonical_settings(self.config, "core")["agw-metrics"]
        transport = Directory(self.config)
        old = transport.values[setting["id"].casefold()]["properties"]
        old["storageAccountId"] = diagnostics.canonical_settings(self.config, "core")["blob-collectJapan"]["ownerId"]
        result = diagnostics.prepare_settings(self.config, transport, "core")
        self.assertEqual(result["agw-metrics"], old)
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_extra_enabled_disabled_retention_and_timegrain_are_not_replaced_by_array_union(self):
        base = {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True, "retentionPolicy": {"enabled": False, "days": 0}}, {"category": "Other", "enabled": True}, {"category": "Disabled", "enabled": False}],
                "metrics": [{"category": "Capacity", "enabled": False, "timeGrain": "PT1M"}]}
        result = diagnostics.merge_properties(base, {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True}]})
        self.assertEqual(result, base)
        self.assertEqual(len(result["logs"]), 3)

    def test_explicit_primary_workspace_wins_without_inventing_another_setting(self):
        desired = {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True}]}
        before = {**copy.deepcopy(desired), "workspaceId": self.workspace + "-previous"}
        self.assertEqual(diagnostics.merge_properties(before, desired), desired)

    def test_new_cross_product_is_rejected_even_when_individual_fields_are_valid(self):
        before = {"workspaceId": self.workspace, "storageAccountId": self.workspace.replace("Microsoft.OperationalInsights/workspaces/law-", "Microsoft.Storage/storageAccounts/st"), "logs": [{"category": "Audit", "enabled": True}]}
        desired = {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True}, {"category": "NewLog", "enabled": True}]}
        with self.assertRaisesRegex(diagnostics.DiagnosticPreservationError, "routing-cross-product"):
            diagnostics.merge_properties(before, desired)

    def test_enabled_alllogs_remains_a_group_and_disabled_conflicts_are_rejected(self):
        before = {"workspaceId": self.workspace, "logs": [{"categoryGroup": "allLogs", "enabled": True}]}
        desired = {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True}]}
        self.assertEqual(diagnostics.merge_properties(before, desired), before)
        before["logs"][0]["enabled"] = False
        with self.assertRaises(diagnostics.DiagnosticPreservationError): diagnostics.merge_properties(before, desired)

    def test_unknown_fields_duplicate_and_group_mixtures_fail_closed(self):
        for properties in ({"futureSink": "private"}, {"logs": [{"category": "Audit", "enabled": "true"}]},
                           {"logs": [{"category": "Audit", "enabled": True}] * 2},
                           {"logs": [{"categoryGroup": "allLogs", "enabled": True}, {"category": "Audit", "enabled": True}]},
                           {"logs": [{"categoryGroup": "audit", "enabled": True}]}):
            with self.subTest(properties=properties):
                with self.assertRaises(diagnostics.DiagnosticPreservationError): diagnostics.merge_properties(properties, {"workspaceId": self.workspace})

    def test_403_unknown_owner_and_missing_schema_are_not_fresh404(self):
        selected = diagnostics.canonical_settings(self.config, "core")["agw-metrics"]
        for kind in ("403", "owner", "schema"):
            transport = Directory(self.config)
            if kind == "403": transport.status[selected["id"].casefold()] = 403
            elif kind == "owner": transport.values[selected["ownerId"].casefold()]["tags"]["environment"] = "other"
            else: transport.values[selected["id"].casefold()]["properties"] = []
            with self.assertRaises(diagnostics.DiagnosticPreservationError): diagnostics.prepare_settings(self.config, transport, "monitoring")
            self.assertFalse(any(call[0] != "GET" for call in transport.calls))

    def test_fresh_parent_and_setting404_use_normal_canonical_creation(self):
        actual = diagnostics.prepare_settings(self.config, Directory(self.config, existing=False), "monitoring")
        expected = {key: item["properties"] for key, item in diagnostics.canonical_settings(self.config, "monitoring").items()}
        self.assertEqual(actual, expected)

    def test_confirmed_parent404_skips_child_diagnostic_and_registration404_stops(self):
        transport = Directory(self.config, existing=False)
        diagnostics.prepare_settings(self.config, transport, "monitoring")
        setting_reads = [call for call in transport.calls if "/diagnosticSettings/" in call[1]]
        self.assertEqual(len(setting_reads), 1)  # Subscription exists; all resource parents are confirmed absent.
        class Unregistered(Directory):
            def request(self, method, path, body=None, headers=None):
                return {"statusCode": 404, "body": {"error": {"code": "MissingSubscriptionRegistration"}}}
        with self.assertRaisesRegex(diagnostics.DiagnosticPreservationError, "unconfirmed-404"):
            diagnostics.prepare_settings(self.config, Unregistered(self.config, existing=False), "monitoring")

    def test_primary_replacement_requires_old_primary_owner_and_keeps_explicit_desired(self):
        selected = diagnostics.canonical_settings(self.config, "core")["agw-metrics"]
        for known in (True, False):
            transport = Directory(self.config)
            previous = self.workspace + "-previous"
            metadata = copy.deepcopy(transport.values[self.workspace.casefold()])
            metadata["id"] = previous
            if not known: metadata["tags"]["environment"] = "foreign"
            transport.values[previous.casefold()] = metadata
            transport.values[selected["id"].casefold()]["properties"]["workspaceId"] = previous
            if known:
                self.assertEqual(diagnostics.prepare_settings(self.config, transport, "core")["agw-metrics"]["workspaceId"], self.workspace)
            else:
                with self.assertRaises(diagnostics.DiagnosticPreservationError): diagnostics.prepare_settings(self.config, transport, "core")

    def test_default_workspace_mode_echo_matches_null_and_omission(self):
        expected = {"workspaceId": self.workspace, "metrics": [{"category": "AllMetrics", "enabled": True}]}
        for mode in (None, "AzureDiagnostics"):
            observed = {**copy.deepcopy(expected), "logAnalyticsDestinationType": mode}
            original = copy.deepcopy(observed)
            self.assertEqual(diagnostics.normalized_properties(observed), expected)
            self.assertEqual(diagnostics._pairs(diagnostics.normalized_properties(observed)), diagnostics._pairs(expected))
            self.assertEqual(observed, original)

    def test_default_mode_echo_preserves_existing_additional_metrics_sink(self):
        desired = {"workspaceId": self.workspace, "metrics": [{"category": "AllMetrics", "enabled": True}]}
        before = {**copy.deepcopy(desired), "logAnalyticsDestinationType": "AzureDiagnostics",
                  "storageAccountId": diagnostics.canonical_settings(self.config, "core")["blob-collectJapan"]["ownerId"]}
        expected = {key: value for key, value in before.items() if key != "logAnalyticsDestinationType"}
        self.assertEqual(diagnostics.merge_properties(before, desired), expected)

    def test_dedicated_mode_remains_distinct_and_unknown_modes_fail_closed(self):
        common = {"workspaceId": self.workspace, "logs": [{"category": "Audit", "enabled": True}]}
        observed = {**copy.deepcopy(common), "logAnalyticsDestinationType": "AzureDiagnostics"}
        desired = {**copy.deepcopy(common), "logAnalyticsDestinationType": "Dedicated"}
        self.assertNotEqual(diagnostics._pairs(diagnostics.normalized_properties(observed)), diagnostics._pairs(desired))
        observed["logs"].append({"category": "Other", "enabled": True})
        with self.assertRaisesRegex(diagnostics.DiagnosticPreservationError, "routing-cross-product"):
            diagnostics.merge_properties(observed, desired)
        for mode in ("Other", "", False, [], {}):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(diagnostics.DiagnosticPreservationError, "workspace-mode"):
                    diagnostics.normalized_properties({**common, "logAnalyticsDestinationType": mode})
        for mode in ("AzureDiagnostics", "Dedicated"):
            with self.assertRaisesRegex(diagnostics.DiagnosticPreservationError, "workspace-mode"):
                diagnostics.normalized_properties({"logAnalyticsDestinationType": mode})

    def test_core_conflict_is_detected_before_monitoring_deployment_put(self):
        transport = Directory(self.config)
        setting = diagnostics.canonical_settings(self.config, "core")["apim-logs"]
        props = transport.values[setting["id"].casefold()]["properties"]
        props["logs"] = [props["logs"][0]]
        props["eventHubAuthorizationRuleId"] = self.workspace.replace("Microsoft.OperationalInsights/workspaces/law-", "Microsoft.EventHub/namespaces/eh-") + "/authorizationRules/audit"
        with mock.patch.object(lifecycle, "run", return_value={}), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), mock.patch.object(lifecycle, "read_workspace_privacy_link", return_value=""):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "routing-cross-product"):
                lifecycle.deploy_phase(self.config, "monitoring")
        self.assertFalse(any(call[0] != "GET" for call in transport.calls))


if __name__ == "__main__":
    unittest.main()
