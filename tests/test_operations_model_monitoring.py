"""モデル・監視の通常保守を、合成ARM応答と保護記録で検証する。"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operations import model, monitoring
from operations.common import OperationError


class Clock:
    """待機時間を実sleepせず再現する。"""

    def __init__(self):
        """合成時刻と待機履歴を作る。"""
        self.value, self.sleeps = 0, []

    def now(self):
        """単調増加時刻を返す。"""
        return self.value

    def sleep(self, seconds):
        """指定秒数を合成時刻へ加える。"""
        self.sleeps.append(seconds)
        self.value += seconds


class Context:
    """資源一件の読取・変更・記録だけを再現し、実Azureには接続しない。"""

    def __init__(self):
        """独立した架空環境と、一配備・通知先・監視規則を用意する。"""
        subscription = "11111111-1111-1111-1111-111111111111"
        self.config = {"subscriptionId": subscription, "resourceStem": "sandbox-fixture",
                       "resourceGroupNames": {k: "rg-fixture-" + k for k in ("svc", "monitor", "nw")},
                       "services": {"modelDeployments": [{"name": "fixture-model", "modelName": "fixture-base",
                           "modelFormat": "OpenAI", "modelVersion": "1", "skuName": "GlobalStandard",
                           "capacity": 2, "versionUpgradeOption": "NoAutoUpgrade"}]},
                       "monitoring": {"operatorEmailAddresses": ["new@example.com"]}}
        self.foundry = self.scope("svc") + "/providers/Microsoft.CognitiveServices/accounts/aif-sandbox-fixture"
        self.apim = self.scope("svc") + "/providers/Microsoft.ApiManagement/service/apim-sandbox-fixture-abc123"
        self.deployment = self.foundry + "/deployments/fixture-model"
        self.group = self.scope("monitor") + "/providers/Microsoft.Insights/actionGroups/ag-sandbox-fixture-ops"
        self.log = self.scope("monitor") + "/providers/Microsoft.Insights/scheduledQueryRules/la-sandbox-fixture-appi-ingestion-high"
        self.metric = self.scope("monitor") + "/providers/Microsoft.Insights/metricAlerts/ma-sandbox-fixture-apim-capacity-high"
        self.state = {"storage": {key: {"id": self.scope("monitor") + "/providers/Microsoft.Storage/storageAccounts/" + name}
                                   for key, name in (("collectJapan", "fixturejapan"), ("collectFoundry", "fixturefoundry"))}}
        self.resources = {
            self.foundry: {"id": self.foundry, "type": "Microsoft.CognitiveServices/accounts", "properties": {"provisioningState": "Succeeded"}},
            self.deployment: {"id": self.deployment, "type": model.TYPE, "etag": '"first"',
                "sku": {"name": "GlobalStandard", "capacity": 1}, "tags": {"keep": "value"},
                "properties": {"model": {"format": "OpenAI", "name": "fixture-base", "version": "1", "publisher": "OpenAI"},
                    "versionUpgradeOption": "NoAutoUpgrade", "raiPolicyName": "preserved-rai",
                    "spilloverDeploymentName": "preserved-spillover", "currentCapacity": 1,
                    "dynamicThrottlingEnabled": False, "provisioningState": "Succeeded"}},
            self.group: {"id": self.group, "type": "Microsoft.Insights/actionGroups", "location": "global", "tags": {"keep": "value"},
                "properties": {"enabled": True, "groupShortName": "fixture", "emailReceivers": [
                    {"name": "operator-0", "emailAddress": "old@example.com", "useCommonAlertSchema": True, "status": "Enabled"}],
                    "webhookReceivers": [{"name": "existing", "serviceUri": "https://notify.example.com/hook?sig=dummy"}]}},
        }
        for target, kind, scope in ((self.log, "scheduledQueryRules", monitoring._expected_scope(self, "appi-ingestion-high")),
                                    (self.metric, "metricAlerts", self.apim)):
            self.resources[target] = {"id": target, "type": "Microsoft.Insights/" + kind,
                "location": "japaneast", "tags": {"keep": "value"}, "etag": '"first"',
                "properties": {"scopes": [scope], "enabled": True, "severity": 3, "evaluationFrequency": "PT5M",
                    "criteria": {"allOf": [{"threshold": 80, "operator": "GreaterThan",
                        **({"query": "AppRequests | summarize count()", "timeAggregation": "Count"} if kind == "scheduledQueryRules"
                           else {"name": "capacity", "criterionType": "StaticThresholdCriterion", "metricName": "Capacity"})}]},
                    "actions": {"actionGroups": [self.group]} if kind == "scheduledQueryRules" else [{"actionGroupId": self.group}]}}
        self.resources[self.log].update(kind="LogAlert", identity={"type": "SystemAssigned", "principalId": "fake"})
        self.calls, self.records, self.reads = [], [], {}
        self.get_hook = self.write_hook = None
        self.headers = None
        self.http_status = 202
        self.lock_count = 0
        self.readback_sequence = []
        self.mutation_headers = {}

    def scope(self, layer):
        """架空環境の所有RGを返す。"""
        return f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames'][layer]}"

    def service_id(self, key):
        """記録済みサービスだけを返す。"""
        return {"foundry": self.foundry, "apim": self.apim}[key]

    def verify_resource(self, target, api, expected_type=None):
        """共通層の所有照合済み本文を再現する。"""
        value = copy.deepcopy(self.resources[target])
        if value["type"].casefold() != expected_type.casefold():
            raise OperationError("Synthetic ownership check failed")
        return value

    def request(self, method, target, api, body=None, headers=None):
        """照会と単一変更を記録し、日時ヘッダーだけは毎回変化させる。"""
        self.calls.append((method, target, api, copy.deepcopy(body), headers))
        if method == "GET":
            self.reads[target] = self.reads.get(target, 0) + 1
            if self.get_hook:
                self.get_hook(self, target, self.reads[target])
            if self.mutations() and self.readback_sequence:
                return copy.deepcopy(self.readback_sequence.pop(0))
            value = copy.deepcopy(self.resources.get(target))
            metadata = {"Date": str(len(self.calls)), "x-ms-request-id": str(len(self.calls))}
            if value and "etag" in value:
                metadata["ETag"] = value["etag"]
            metadata.update(self.headers or {})
            return {"statusCode": 200 if value else 404, "body": value, "headers": metadata}
        if self.http_status not in (200, 201, 202):
            return {"statusCode": self.http_status, "body": {"error": "PRIVATE_SENTINEL"}}
        old = self.resources.get(target, {})
        if method == "PATCH":
            value = copy.deepcopy(old)
            for key, content in body.items():
                if key == "properties":
                    value["properties"].update(copy.deepcopy(content))
                else:
                    value[key] = copy.deepcopy(content)
        else:
            value = {"id": target, "type": old.get("type", model.TYPE), **copy.deepcopy(body)}
        if target == self.group:
            for channel, receivers in value["properties"].items():
                if not channel.endswith("Receivers") or not isinstance(receivers, list):
                    continue
                for item in receivers:
                    if isinstance(item, dict):
                        previous = next((r for r in old["properties"].get(channel, []) if r.get("name") == item.get("name")
                                         and {k: v for k, v in r.items() if k != "status"} == item), {})
                        if "status" in previous:
                            item["status"] = previous["status"]
        if target == self.deployment:
            value["properties"]["provisioningState"] = "Succeeded"
            value["properties"]["currentCapacity"] = value["sku"]["capacity"]
            value["properties"]["dynamicThrottlingEnabled"] = old.get("properties", {}).get("dynamicThrottlingEnabled", False)
            value.setdefault("tags", {})
            value["properties"].setdefault("raiPolicyName", "Microsoft.Default")
        self.resources[target] = value
        if self.write_hook:
            self.write_hook(self, target)
        return {"statusCode": self.http_status, "body": None, "headers": self.mutation_headers}

    def record(self, kind, payload):
        """秘密を含まない計画と結果の保護保存を再現する。"""
        self.records.append((kind, copy.deepcopy(payload)))

    @contextmanager
    def locked(self):
        """execute入口が環境単位の共通ロックを取得したことを記録する。"""
        self.lock_count += 1
        yield

    def mutations(self):
        """変更要求だけを数える。"""
        return [call for call in self.calls if call[0] != "GET"]


class ModelTests(unittest.TestCase):
    """モデルの意図・既存保護・部分変更・有限失敗を検証する。"""

    def setUp(self):
        """試験ごとに独立した架空資源を用意する。"""
        self.context = Context()

    def test_plan_is_read_only_and_reports_current_and_expected_capacity(self):
        """既定計画は現在値と差分を記録し、変更要求を送らない。"""
        result = model.operate(self.context, "fixture-model", "update")
        self.assertEqual(result["before"]["sku"]["capacity"], 1)
        self.assertEqual(result["expected"]["sku"]["capacity"], 2)
        self.assertFalse(self.context.mutations())

    def test_capacity_patch_ignores_date_headers_and_preserves_other_settings(self):
        """毎回違うHTTP管理ヘッダーで停止せず、SKU容量だけPATCHする。"""
        result = model.operate(self.context, "fixture-model", "update", execute=True)
        write = self.context.mutations()[0]
        self.assertEqual(write[0], "PATCH")
        self.assertEqual(write[3], {"sku": {"name": "GlobalStandard", "capacity": 2}})
        self.assertEqual(write[4], {"If-Match": '"first"'})
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.context.resources[self.context.deployment]["properties"]["raiPolicyName"], "preserved-rai")

    def test_existing_identity_sku_and_upgrade_policy_cannot_be_replaced(self):
        """モデル名・形式・SKU・更新方針の意図外置換は書込み前に停止する。"""
        for key, value in (("modelName", "another"), ("modelFormat", "Cohere"),
                           ("skuName", "Standard"), ("versionUpgradeOption", "OnceCurrentVersionExpired")):
            with self.subTest(key=key):
                context = Context()
                context.config["services"]["modelDeployments"][0][key] = value
                with self.assertRaises(OperationError):
                    model.operate(context, "fixture-model", "update", execute=True)
                self.assertFalse(context.mutations())

    def test_version_put_is_explicit_and_preserves_writable_policy_and_tags(self):
        """版の明示許可を要求し、PUTでもRAI・spillover・tagsを保持する。"""
        self.context.config["services"]["modelDeployments"][0]["modelVersion"] = "2"
        with self.assertRaises(OperationError):
            model.operate(self.context, "fixture-model", "update", execute=True)
        result = model.operate(self.context, "fixture-model", "update", allow_version_change=True, execute=True)
        body = self.context.mutations()[0][3]
        self.assertEqual(result["method"], "PUT")
        self.assertEqual(body["properties"]["raiPolicyName"], "preserved-rai")
        self.assertEqual(body["properties"]["spilloverDeploymentName"], "preserved-spillover")
        self.assertEqual(body["tags"], {"keep": "value"})
        self.assertNotIn("provisioningState", body["properties"])

    def test_nondefault_dynamic_throttling_blocks_version_put_but_not_capacity_patch(self):
        """安全に保持できない読取属性を版PUTで落とさず停止する。"""
        self.context.resources[self.context.deployment]["properties"]["dynamicThrottlingEnabled"] = True
        self.context.config["services"]["modelDeployments"][0]["modelVersion"] = "2"
        with self.assertRaises(OperationError):
            model.operate(self.context, "fixture-model", "update", allow_version_change=True, execute=True)
        self.assertFalse(self.context.mutations())
        self.context.config["services"]["modelDeployments"][0]["modelVersion"] = "1"
        model.operate(self.context, "fixture-model", "update", execute=True)

    def test_create_requires_absence_and_accepts_service_added_defaults(self):
        """新規作成は既存配備を上書きせず、Azure既定値をfalse-failにしない。"""
        with self.assertRaises(OperationError):
            model.operate(self.context, "fixture-model", "create", execute=True)
        self.context.resources.pop(self.context.deployment)
        result = model.operate(self.context, "fixture-model", "create", execute=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.context.mutations()[0][4], {"If-None-Match": "*"})

    def test_fresh_change_and_unresolved_operation_prevent_mutation(self):
        """比較後の変更や未完了の配備へ更新を重ねない。"""
        for fresh_change in (True, False):
            context = Context()
            if fresh_change:
                context.get_hook = lambda c, t, n: c.resources[t]["tags"].update(other="changed") if t == c.deployment and n == 2 else None
            else:
                context.resources[context.deployment]["properties"]["provisioningState"] = "Updating"
            with self.assertRaises(OperationError):
                model.operate(context, "fixture-model", "update", execute=True)
            self.assertFalse(context.mutations())

    def test_missing_etag_uses_comparison_and_invalid_etag_never_falls_back(self):
        """ETag不提供と、不正なETag応答を区別する。"""
        self.context.resources[self.context.deployment].pop("etag")
        result = model.operate(self.context, "fixture-model", "update", execute=True)
        self.assertTrue(result["nonAtomicComparison"])
        self.assertIsNone(self.context.mutations()[0][4])
        for value in ("", "*", '"*"', '""', 'W/""', '"unterminated', "bad\nvalue"):
            context = Context()
            context.headers = {"ETag": value}
            with self.assertRaises(OperationError):
                model.operate(context, "fixture-model", "update", execute=True)
            self.assertFalse(context.mutations())

    def test_rejected_or_failed_write_is_never_repeated(self):
        """412や非同期失敗を成功にせず、変更要求は一回で止める。"""
        for status in (412, 202):
            context = Context()
            context.http_status = status
            context.write_hook = lambda c, t: c.resources[t]["properties"].update(provisioningState="Failed")
            with self.assertRaises(OperationError):
                model.operate(context, "fixture-model", "update", execute=True)
            self.assertEqual(len(context.mutations()), 1)

    def test_create_waits_for_404_propagation_and_honors_retry_after(self):
        """作成直後404は伝播待ちにし、受付応答と照会の待機指示を尊重する。"""
        self.context.resources.pop(self.context.deployment)
        self.context.mutation_headers = {"Retry-After": "2"}
        self.context.readback_sequence = [{"statusCode": 404, "body": None, "headers": {"Retry-After": "7"}}]
        clock = Clock()
        result = model.operate(self.context, "fixture-model", "create", execute=True,
                               sleep=clock.sleep, monotonic=clock.now)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(clock.sleeps, [2, 7])
        self.assertEqual(len(self.context.mutations()), 1)

    def test_succeeded_old_model_settings_wait_but_third_settings_stop(self):
        """Succeededの旧値だけを有限に待ち、目的外の第三の設定値は拒否する。"""
        old = copy.deepcopy(self.context.resources[self.context.deployment])
        self.context.readback_sequence = [{"statusCode": 200, "body": old, "headers": {}}]
        clock = Clock()
        result = model.operate(self.context, "fixture-model", "update", execute=True,
                               sleep=clock.sleep, monotonic=clock.now)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(clock.sleeps, [5])
        context = Context()
        old["sku"]["capacity"] = 7
        context.readback_sequence = [{"statusCode": 200, "body": old, "headers": {}}]
        with self.assertRaises(OperationError):
            model.operate(context, "fixture-model", "update", execute=True)
        self.assertEqual(len(context.mutations()), 1)

    def test_long_retry_hint_stops_before_an_early_poll(self):
        """残り期限より長い指示を短縮してGETしない。"""
        self.context.mutation_headers = {"Retry-After": "901"}
        clock = Clock()
        with self.assertRaises(OperationError):
            model.operate(self.context, "fixture-model", "update", execute=True,
                          sleep=clock.sleep, monotonic=clock.now)
        self.assertEqual(self.context.reads[self.context.deployment], 2)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(len(self.context.mutations()), 1)


class MonitoringTests(unittest.TestCase):
    """通知先と単一条件だけの変更・競合停止・通知非送信を確認する。"""

    def test_unchanged_execute_records_final_status_without_a_write(self):
        """変更不要の実行も最終結果を保存し、計画記録を書き換えず変更要求0とする。"""
        for module in (model, monitoring):
            with self.subTest(module=module.__name__):
                context = Context()
                if module is model:
                    context.config["services"]["modelDeployments"][0]["capacity"] = 1
                    result = model.operate(context, "fixture-model", "update", execute=True)
                else:
                    context.config["monitoring"]["operatorEmailAddresses"] = ["old@example.com"]
                    result = monitoring.operate(context, "recipients", execute=True)
                self.assertEqual(result["status"], "unchanged")
                self.assertEqual(context.records[0][1]["status"], "planned")
                self.assertEqual(context.records[-1][1]["status"], "unchanged")
                self.assertFalse(context.records[-1][1]["runtimeVerified"])
                self.assertFalse(context.records[-1][1]["requestAttempted"])
                self.assertFalse(context.mutations())

    def test_recipient_update_preserves_other_channels_and_never_sends_notifications(self):
        """設定更新でwebhook等を保持し、通知テストPOSTを送らない。"""
        context = Context()
        old = copy.deepcopy(context.resources[context.group])
        plan = monitoring.operate(context, "recipients")
        self.assertFalse(context.mutations())
        result = monitoring.operate(context, "recipients", execute=True)
        self.assertEqual(result["status"], "applied")
        write = context.mutations()[0]
        self.assertEqual(write[0], "PUT")
        self.assertEqual(write[3]["properties"]["webhookReceivers"], old["properties"]["webhookReceivers"])
        self.assertEqual(write[3]["properties"]["emailReceivers"][0]["emailAddress"], "new@example.com")
        self.assertTrue(plan["nonAtomicComparison"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(context.records))
        self.assertFalse(result["notificationTestSent"])

    def test_existing_recipient_name_and_schema_are_preserved(self):
        """既存宛先を保持する場合、その受信者名と通知形式を変更しない。"""
        context = Context()
        context.config["monitoring"]["operatorEmailAddresses"].append("old@example.com")
        context.resources[context.group]["properties"]["emailReceivers"][0].update(name="existing-name", useCommonAlertSchema=False)
        monitoring.operate(context, "recipients", execute=True)
        values = context.resources[context.group]["properties"]["emailReceivers"]
        self.assertEqual(values[1], {"name": "existing-name", "emailAddress": "old@example.com", "useCommonAlertSchema": False, "status": "Enabled"})

    def test_unchanged_disabled_receiver_is_not_silently_reenabled(self):
        """既存受信者の購読解除状態が変わったら、更新完了とは記録しない。"""
        context = Context()
        context.config["monitoring"]["operatorEmailAddresses"].append("old@example.com")
        context.resources[context.group]["properties"]["emailReceivers"][0]["status"] = "Disabled"
        context.write_hook = lambda c, t: c.resources[t]["properties"]["emailReceivers"][1].update(status="Enabled")
        with self.assertRaises(OperationError):
            monitoring.operate(context, "recipients", execute=True)
        self.assertEqual(len(context.mutations()), 1)

    def test_static_log_and_metric_thresholds_patch_only_criteria(self):
        """数値・ログの双方で、scopeやアクションを変えずcriteriaだけを送る。"""
        for kind, rule in (("log", "appi-ingestion-high"), ("metric", "apim-capacity-high")):
            with self.subTest(kind=kind):
                context = Context()
                result = monitoring.operate(context, "threshold", rule_kind=kind, rule=rule, threshold=90, execute=True)
                write = context.mutations()[0]
                self.assertEqual(write[0], "PATCH")
                self.assertEqual(set(write[3]["properties"]), {"criteria"})
                self.assertEqual(write[3]["properties"]["criteria"]["allOf"][0]["threshold"], 90)
                self.assertEqual(write[4], {"If-Match": '"first"'})
                self.assertEqual(result["status"], "applied")

    def test_wrong_scope_unknown_rule_and_ambiguous_criteria_stop_before_write(self):
        """別scope・未知規則・複数条件を、通常一条件の更新へ広げない。"""
        for case in ("scope", "rule", "criteria"):
            with self.subTest(case=case):
                context = Context()
                props = context.resources[context.log]["properties"]
                if case == "scope":
                    props["scopes"] = [context.apim]
                if case == "criteria":
                    props["criteria"]["allOf"] *= 2
                with self.assertRaises(OperationError):
                    monitoring.operate(context, "threshold", rule_kind="log", rule="unknown" if case == "rule" else "appi-ingestion-high", threshold=90, execute=True)
                self.assertFalse(context.mutations())

    def test_invalid_etag_and_changed_snapshot_do_not_use_unconditional_put(self):
        """不正なETagや直前変更を、ETag不提供APIの代替へ流さない。"""
        for bad_etag in (True, False):
            context = Context()
            if bad_etag:
                context.headers = {"ETag": ""}
            else:
                context.get_hook = lambda c, t, n: c.resources[t]["tags"].update(other="changed") if n == 2 else None
            with self.assertRaises(OperationError):
                monitoring.operate(context, "recipients", execute=True)
            self.assertFalse(context.mutations())

    def test_nonfinite_threshold_and_missing_action_group_are_rejected(self):
        """不正な数値と目的外の通知先を持つ規則を更新しない。"""
        for value in (float("nan"), float("inf"), -1, True):
            context = Context()
            with self.assertRaises(OperationError):
                monitoring.operate(context, "threshold", rule_kind="log", rule="appi-ingestion-high", threshold=value, execute=True)
            self.assertFalse(context.mutations())
        context = Context()
        context.resources[context.log]["properties"]["actions"] = {"actionGroups": [context.group + "-other"]}
        with self.assertRaises(OperationError):
            monitoring.operate(context, "threshold", rule_kind="log", rule="appi-ingestion-high", threshold=90, execute=True)
        self.assertFalse(context.mutations())

    def test_readback_difference_stays_incomplete_without_write_retry(self):
        """設定保持が読戻しと一致しなければ成功とせず、PUTを繰り返さない。"""
        context = Context()
        context.write_hook = lambda c, t: c.resources[t]["tags"].update(unexpected="changed")
        with self.assertRaises(OperationError):
            monitoring.operate(context, "recipients", execute=True, sleep=lambda _: None, monotonic=lambda: 0)
        self.assertEqual(len(context.mutations()), 1)

    def test_monitoring_pending_states_wait_and_initial_pending_still_blocks(self):
        """更新後の既知pendingだけを待ち、実行前の未完了資源は変更しない。"""
        context = Context()
        current = copy.deepcopy(context.resources[context.group])
        current["properties"]["provisioningState"] = "Updating"
        context.mutation_headers = {"Retry-After": "3"}
        context.readback_sequence = [{"statusCode": 200, "body": current, "headers": {"Retry-After": "8"}}]
        clock = Clock()
        result = monitoring.operate(context, "recipients", execute=True, sleep=clock.sleep, monotonic=clock.now)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(clock.sleeps, [3, 8])
        self.assertEqual(len(context.mutations()), 1)
        context = Context()
        context.resources[context.group]["properties"]["provisioningState"] = "Updating"
        with self.assertRaises(OperationError):
            monitoring.operate(context, "recipients", execute=True)
        self.assertFalse(context.mutations())

    def test_monitoring_old_values_do_not_extend_the_deadline(self):
        """旧設定が続く場合も待機期限で停止し、通知先PUTを再送しない。"""
        context = Context()
        current = copy.deepcopy(context.resources[context.group])
        context.readback_sequence = [{"statusCode": 200, "body": current, "headers": {}}] * 10
        clock = Clock()
        with self.assertRaises(OperationError):
            monitoring.operate(context, "recipients", execute=True, sleep=clock.sleep, monotonic=clock.now, timeout=12)
        self.assertLessEqual(clock.value, 12)
        self.assertEqual(len(context.mutations()), 1)

    def test_cli_locks_execute_and_hides_private_details(self):
        """CLIのexecuteは共通ロックを使い、実値や外部本文を表示しない。"""
        context = Context()
        for module, argv in ((model, ["--config", "fixture.json", "--name", "fixture-model", "--intent", "update", "--execute"]),
                             (monitoring, ["--config", "fixture.json", "--action", "recipients", "--execute"])):
            output = io.StringIO()
            with patch.object(module.OperationContext, "load", return_value=context), patch("sys.stdout", output):
                self.assertEqual(module.main(argv), 0)
            self.assertNotIn("example.com", output.getvalue())
            self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
            self.assertNotIn(context.foundry, output.getvalue())
        self.assertEqual(context.lock_count, 2)


if __name__ == "__main__":
    unittest.main()
