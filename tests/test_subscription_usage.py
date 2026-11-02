"""購読集計KQLの境界と、共有するLLM照合の重複・欠落処理を検証します。

KQLエンジンの実行試験ではありません。ネイティブ列・集計順・nullの保持を固定し、
現行の監査照合器にも同じ要求の複数traceと重複LLM行のfixtureを通します。
"""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_usage import gateway_events, join_usage

APIM = "/subscriptions/fixture/resourceGroups/core/providers/Microsoft.ApiManagement/service/core"
RID = "22222222-2222-4222-8222-222222222222"


class SubscriptionUsageQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.query = (ROOT / "scripts/subscription-usage.kql").read_text(encoding="utf-8")
        cls.statements = re.sub(r"//[^\n]*", "", cls.query)

    def test_apim_subscription_is_not_azure_subscription_or_caller_input(self):
        self.assertIn("subscriptionId=tolower(tostring(ApimSubscriptionId))", self.statements)
        self.assertNotRegex(self.statements, r"(?<![A-Za-z])_SubscriptionId\b")
        self.assertIn("where _ResourceId =~ apimResourceId", self.statements)
        self.assertNotIn("tostring(SubscriptionId)", self.statements)

    def test_request_count_precedes_trace_expansion_and_many_side_joins(self):
        native = self.statements.split("let nativeRequests =", 1)[1].split("let llmEvidence =", 1)[0]
        self.assertIn("by requestId", native)
        self.assertIn("subscriptionVariants=make_set(subscriptionId, 2)", native)
        self.assertNotIn("mv-expand", native)
        self.assertIn("'[ambiguous]'", native)
        self.assertIn("'[unattributed]'", native)
        self.assertIn("rowsWithoutCorrelationId = toscalar", self.statements)
        self.assertIn("DuplicateRows=sum(rawRows - 1)", self.statements)
        usage = self.statements.split("let nativeUsage =", 1)[1].split("let requests =", 1)[0]
        self.assertIn("by requestId, targetModel", usage)
        self.assertIn("join kind=leftouter nativeUsage on requestId, targetModel", self.statements)
        self.assertNotRegex(self.statements, r"Requests\s*=\s*dcount")
        # timeはKQLの予約語。実LAWでSYN0002になったため動的propertyは括弧で読む。
        self.assertNotRegex(native, r"\.time\b")
        self.assertIn("resultVariants[0]['time']", native)

    def test_closed_minute_window_zero_minutes_and_rate_units(self):
        for text in (
            "windowEnd = bin(now() - 5m, 1m)", "TimeGenerated >= windowStart and TimeGenerated < windowEnd",
            "RequestsPerSecond=Requests / 60.0", "default=0 on Minute from windowStart to windowEnd step 1m",
            "P95RequestsPerMinute=percentile(MinuteRequests, 95)", "MaxRequestsPerMinute=max(MinuteRequests)",
            "P95MinuteAverageRps=P95RequestsPerMinute / 60.0", "ObservedMinutes=count()",
        ):
            self.assertIn(text, self.statements)
        # 分単位平均を瞬間最大や同時接続数と呼ばない。処理時間列も未確認の単位に変換しない。
        for misleading in ("InstantaneousRps", "ConcurrentRequests", "P95TotalTimeMs", "totalTime / 1000"):
            self.assertNotIn(misleading, self.statements)

    def test_response_classes_do_not_infer_success_or_count_only_allowed_traces(self):
        for text in (
            "Responses429=countif(responseCode == 429)",
            "Responses4xx=countif(responseCode >= 400 and responseCode < 500)",
            "Responses5xx=countif(responseCode >= 500 and responseCode < 600)",
            "UnknownResults=countif(isnull(responseCode)",
        ):
            self.assertIn(text, self.statements)
        native = self.statements.split("let nativeRequests =", 1)[1].split("let llmEvidence =", 1)[0]
        self.assertNotIn("authorization", native)
        self.assertNotIn("responseCode == 200", native)

    def test_llm_values_require_current_evidence_exact_join_and_remain_nullable(self):
        for text in (
            "tostring(record.measurementRole) == 'execution'",
            "tostring(record.usageSource) == 'ApiManagementGatewayLlmLog'",
            "tolower(tostring(record.subscriptionId)) == subscriptionId",
            "totalTokens == inputTokens + outputTokens",
            "usageCount != 1, 'ambiguous-usage'",
            "InputTokens=iff(MeasuredTokenRequests > 0, KnownInputTokens, long(null))",
            "OutputTokens=iff(MeasuredTokenRequests > 0, KnownOutputTokens, long(null))",
            "TotalTokens=iff(MeasuredTokenRequests > 0, KnownTotalTokens, long(null))",
            "union isfuzzy=true ApiManagementGatewayLlmLog",
        ):
            self.assertIn(text, self.statements)
        for column in ("PromptTokens", "CompletionTokens", "TotalTokens"):
            self.assertIn(f"boundedNumber(pack_array({column})[0])", self.statements)
        for secret in ("RequestBody", "ResponseBody", "RequestHeaders", "ResponseHeaders", "RequestMessages", "ResponseMessages"):
            self.assertNotIn(secret, self.statements)

    def logs(self):
        common = dict(schemaVersion=1, event="gateway-request", requestId=RID,
                      authorization="allowed", method="subscriptionKey", subscriptionId="portal-fixture",
                      callerId="subscription:portal-fixture", measurementRole="execution",
                      usageSource="ApiManagementGatewayLlmLog", targetModel="fixture-model")
        trace = [{"message": json.dumps({**common, "phase": phase})}
                 for phase in ("accepted", "backend-response")]
        return [dict(_ResourceId=APIM, ApimSubscriptionId="portal-fixture", CorrelationId=RID, TraceRecords=trace)]

    def llm(self, **changes):
        return dict(_ResourceId=APIM, CorrelationId=RID, DeploymentName="fixture-model",
                    RequestId="fixture-provider-request", PromptTokens=12, CompletionTokens=4,
                    TotalTokens=16, **changes)

    def test_existing_usage_contract_counts_two_phases_and_duplicate_native_rows_once(self):
        events = gateway_events(self.logs() * 2, apim_resource_id=APIM)
        self.assertEqual(len(events), 4)
        results = join_usage(events, [self.llm(), self.llm()], apim_resource_id=APIM)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["usage"], {"promptTokens": 12, "completionTokens": 4, "totalTokens": 16})

    def test_existing_usage_contract_missing_and_conflicting_usage_never_become_zero(self):
        events = gateway_events(self.logs(), apim_resource_id=APIM)
        for rows in ([], [{**self.llm(), "PromptTokens": "12"}],
                     [self.llm(), {**self.llm(), "CompletionTokens": 5, "TotalTokens": 17}]):
            result = join_usage(events, rows, apim_resource_id=APIM)[0]
            self.assertNotIn("usage", result)
            self.assertNotEqual(result["usageStatus"], "available")


if __name__ == "__main__":
    unittest.main()
