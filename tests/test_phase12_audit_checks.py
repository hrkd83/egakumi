"""合成監査行で、欠測を成功やbackend-zeroへ誤分類しないことを確認する。

operationsの純粋な監査評価helperを使う。Azure・VM・実値は使わない。
"""
import copy
from pathlib import Path
import unittest

from operations import apim_phase12_audit_checks as checks


# All identifiers and resource names below are independent synthetic examples.
SUB = "11111111-1111-4111-8111-111111111111"
RID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EDGE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TENANT = "22222222-2222-4222-8222-222222222222"
PRINCIPAL = "33333333-3333-4333-8333-333333333333"
CLIENT = "44444444-4444-4444-8444-444444444444"
CHILD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
WHEN = "2000-01-01T00:01:00Z"


def scope(**changes):
    values = dict(apim_id=f"/subscriptions/{SUB}/resourceGroups/synthetic-group/providers/Microsoft.ApiManagement/service/synthetic-apim",
                  appgw_id=f"/subscriptions/{SUB}/resourceGroups/synthetic-group/providers/Microsoft.Network/applicationGateways/synthetic-appgw",
                  start="2000-01-01T00:00:00Z", end="2000-01-01T00:10:00Z")
    return checks.AuditScope(**{**values, **changes})


def expectation(**changes):
    return {"requestId": RID, "apiId": "synthetic-model", "operationId": "respond", "httpMethod": "POST",
            "status": 200, "mode": "bearer", "transport": "rest", "tenantId": TENANT,
            "principalId": PRINCIPAL, "clientId": CLIENT, **changes}


def native(e=None, **changes):
    e = e or expectation()
    return {"resourceId": scope().apim_id, "nativeLogAt": WHEN, "requestId": e["requestId"],
            "apiId": e["apiId"] + ";rev=2", "operationId": e.get("operationId") or "mcp-invoke",
            "httpMethod": e["httpMethod"], "responseCode": e["status"], "backendResponseCode": 200,
            "traceCount": 2, "headerTraceId": EDGE, **changes}


def trace(e=None, phase="accepted", **changes):
    e = e or expectation()
    operation = e.get("operationId") or "mcp-invoke"
    key = e["mode"] == "key"
    caller = "subscription:" + e["subscriptionId"] if key else "user:" + e["tenantId"] + ":" + e["principalId"]
    method = "subscriptionKey" if key else "user"
    return {"resourceId": scope().apim_id, "nativeLogAt": WHEN, "requestId": e["requestId"], "traceRequestId": e["requestId"],
            "traceApiId": e["apiId"], "traceOperationId": operation, "traceType": "gateway-request",
            "schemaVersion": 1, "auditSchemaVersion": 2, "phase": phase,
            "code": "GATEWAY_ALLOWED" if phase == "accepted" else "BACKEND_RESPONSE",
            "traceStatus": 0 if phase == "accepted" else e["status"], "authorization": "allowed", "method": method,
            "subscriptionId": e.get("subscriptionId"), "callerId": caller, "allocationId": None if key else caller,
            "verifiedTenantId": None if key else e["tenantId"], "verifiedPrincipalId": None if key else e["principalId"],
            "verifiedClientId": None if key else e.get("clientId"), "subjectStatus": "verified",
            "subjectKind": "apimSubscription" if key else "entraUser", "subjectMethod": method,
            "subjectSubscriptionId": e.get("subscriptionId"), "subjectCallerId": caller,
            "subjectAllocationId": None if key else caller, "subjectTenantId": None if key else e["tenantId"],
            "subjectPrincipalId": None if key else e["principalId"], "subjectClientId": None if key else e.get("clientId"),
            "contextRequestId": e["requestId"], "contextApiId": e["apiId"], "contextOperationId": operation,
            "contextHttpMethod": e["httpMethod"], "contextReceivedAt": WHEN, "contextTraceId": EDGE,
            "measurementRole": e.get("measurementRole", "facade" if e["transport"] == "mcp" else "execution"),
            "targetModel": e.get("expectedModel"), "requestedModel": e.get("expectedAlias"),
            "modelAliasApplied": bool(e.get("expectedAlias")),
            "usageSource": "ApiManagementGatewayLlmLog" if e.get("requireChatUsage") else "none", **changes}


def first(e=None, native_rows=None, traces=None, complete=True):
    e = e or expectation()
    return checks.evaluate_first_phase(scope(), [e], checks.QueryRows(
        [native(e)] if native_rows is None else native_rows, complete), checks.QueryRows(
        [trace(e), trace(e, "backend-response")] if traces is None else traces, complete))


def aggregate(e=None, **changes):
    e = e or expectation()
    subject = trace(e)
    outcomes = [{"event": "gateway-request", "phase": "accepted", "code": "GATEWAY_ALLOWED", "status": 0},
                {"event": "gateway-request", "phase": "backend-response", "code": "BACKEND_RESPONSE", "status": e["status"]}]
    if e.get("authRejected") or e.get("expectedOutcome") == "denied":
        outcomes = [{"event": "gateway-request", "phase": "rejected",
                     "code": "INVALID_AUTHENTICATION" if e.get("authRejected") else "AUTHORIZATION_DENIED", "status": e["status"]}]
    return {"nativeLogAt": WHEN, "requestId": e["requestId"], "apiId": e["apiId"], "operationId": e.get("operationId") or "mcp-invoke",
            "responseCode": e["status"], "backendResponseCode": 200, "nativeMetadataStatus": "consistent",
            "requestContextStatus": "available", "authenticatedSubjectStatus": "verified", "subjectKind": subject["subjectKind"],
            "method": subject["subjectMethod"], "subscriptionId": subject["subjectSubscriptionId"], "callerId": subject["subjectCallerId"],
            "allocationId": subject["subjectAllocationId"], "tenantId": subject["subjectTenantId"],
            "principalId": subject["subjectPrincipalId"], "clientId": subject["subjectClientId"],
            "inconsistentSubjectCount": 0, "malformedContextCount": 0, "unlinkedV2ObservationCount": 0,
            "appGatewayCorrelationStatus": "matched", "appGatewayTraceId": EDGE, "customResults": outcomes, **changes}


def pending_report(reason="FIRST_PHASE_MISSING"):
    return {"status": "pending", "reasons": (reason,), "requests": {}, "edgeIds": {}, "childReferences": {}}


def access(e=None, **changes):
    e = e or expectation()
    return {"resourceId": scope().appgw_id, "TimeGenerated": WHEN, "transactionId": EDGE,
            "httpMethod": e["httpMethod"], "httpStatus": e["status"], "serverStatus": e["status"], **changes}


def second(e=None, first_report=None, aggregate_rows=None, access_rows=None, waf_rows=(), usage=(), complete=True):
    e = e or expectation()
    return checks.evaluate_second_phase(scope(), [e], first_report or first(e),
        checks.QueryRows([aggregate(e)] if aggregate_rows is None else aggregate_rows, complete),
        checks.QueryRows([access(e)] if access_rows is None else access_rows, complete),
        checks.QueryRows(list(waf_rows), complete), checks.QueryRows(list(usage), complete))


class Phase12AuditQueryTests(unittest.TestCase):
    def test_queries_use_normalized_response_ids_fixed_utc_observed_edges_and_four_query_second_phase(self):
        e = expectation(requestId=RID.replace("-", "").upper())
        one = checks.build_first_phase(scope(row_cap=7), [e])
        self.assertEqual(set(one), {"native", "traces"})
        for query in one.values():
            self.assertIn(RID, query)
            self.assertIn("2000-01-01T00:00:00+00:00", query)
            self.assertTrue(query.endswith("| take 8\n"))
            self.assertNotIn("ago(", query)
        source = (Path(__file__).resolve().parents[1] / "scripts/audit-identification.kql").read_text(encoding="utf-8")
        two = checks.build_second_phase(scope(), [expectation()], first(), source)
        self.assertEqual(set(two), {"aggregate", "access", "waf", "chatUsage"})
        self.assertIn(EDGE, two["aggregate"])
        self.assertIn("canonicalId(tostring(CorrelationId)) in (selectedIds)", two["aggregate"])
        self.assertIn("canonicalId(tostring(TransactionId)) in (selectedAppGatewayIds)", two["aggregate"])
        output = two["aggregate"].rsplit("\n| project ", 1)[1]
        for forbidden in ("reportedUserAgent", "appGatewayUserAgent", "appGatewayVariants", "requestContext,", "contextVariants"):
            self.assertNotIn(forbidden, output)
        for query in (*one.values(), two["access"], two["waf"], two["chatUsage"]):
            self.assertNotIn("project *", query)
            self.assertNotIn("RequestBody", query)
            self.assertNotIn("ResponseBody", query)
            self.assertNotIn("DetailedData", query)
            self.assertNotIn("RequestUri", query)

    def test_unbounded_non_utc_bad_ids_resource_injection_and_row_caps_are_rejected(self):
        for change in ({"end": "2000-01-02T00:00:00Z"}, {"start": "2000-01-01T00:00:00"},
                       {"start": "2000-01-01T09:00:00+09:00"}, {"end": "2000-01-01T00:00:00Z"},
                       {"row_cap": 0}, {"row_cap": 5001}, {"row_cap": True},
                       {"apim_id": scope().apim_id + "'; print unsafe=1"}):
            with self.subTest(change=tuple(change)), self.assertRaises(checks.AuditInputError):
                scope(**change)
        for value in ("0" * 32, RID + "');print unsafe=1", "not-a-guid"):
            with self.subTest(identifier=value), self.assertRaises(checks.AuditInputError):
                checks.build_first_phase(scope(), [expectation(requestId=value)])
        with self.assertRaises(checks.AuditInputError):
            checks.build_second_phase(scope(), [expectation()], first(), "print raw='unreviewed source'")


class Phase12AuditFirstPhaseTests(unittest.TestCase):
    def test_normal_key_and_bearer_are_verified_without_changing_the_manifest_or_rows(self):
        for e in (expectation(), expectation(mode="key", subscriptionId="synthetic-subscription")):
            rows, events = [native(e)], [trace(e), trace(e, "backend-response")]
            before = copy.deepcopy((e, rows, events))
            report = first(e, rows, events)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["edgeIds"][RID], (EDGE,))
            self.assertEqual((e, rows, events), before)

    def test_missing_delayed_partial_or_truncated_evidence_stays_pending(self):
        samples = (first(native_rows=[]), first(traces=[]), first(complete=False),
                   first(native_rows=[native(traceCount=129)]), first(native_rows=[native(responseCode=None)]),
                   first(native_rows=[native(operationId=None)]),
                   first(traces=[trace(schemaVersion=None), trace(phase="backend-response")]),
                   first(traces=[trace(traceApiId=None), trace(phase="backend-response")]))
        for report in samples:
            self.assertEqual(report["status"], "pending")
        rows = [native()] * 3
        report = checks.evaluate_first_phase(scope(row_cap=2), [expectation()], checks.QueryRows(rows), checks.QueryRows([trace(), trace(phase="backend-response")]))
        self.assertEqual(report["status"], "pending")
        self.assertIn("RESULT_INCOMPLETE", report["reasons"])

    def test_wrong_tenant_client_status_backend_and_context_are_not_accepted(self):
        samples = ((first(traces=[trace(subjectTenantId=SUB), trace(phase="backend-response")]), "SUBJECT_MISMATCH"),
                   (first(traces=[trace(subjectClientId=SUB), trace(phase="backend-response")]), "SUBJECT_MISMATCH"),
                   (first(native_rows=[native(responseCode=500)]), "HTTP_STATUS_MISMATCH"),
                   (first(native_rows=[native(backendResponseCode=500)]), "BACKEND_STATUS_MISMATCH"),
                   (first(traces=[trace(contextRequestId=CHILD), trace(phase="backend-response")]), "CONTEXT_MISMATCH"),
                   (first(traces=[trace(traceRequestId=CHILD), trace(phase="backend-response")]), "TRACE_REQUEST_MISMATCH"))
        for report, reason in samples:
            self.assertEqual(report["status"], "inconsistent")
            self.assertIn(reason, report["reasons"])
        missing = trace(); missing.pop("contextReceivedAt")
        self.assertEqual(first(traces=[missing, trace(phase="backend-response")])["status"], "pending")

    def test_duplicate_references_conflicting_native_variants_and_conflicting_subjects_remain_distinct(self):
        self.assertEqual(first(native_rows=[native(), native()])["status"], "pass")
        self.assertIn("NATIVE_VARIANTS_CONFLICT", first(native_rows=[native(), native(operationId="changed")])["reasons"])
        report = checks.evaluate_first_phase(scope(), [expectation(), expectation()], checks.QueryRows(), checks.QueryRows())
        self.assertEqual(report["status"], "inconsistent")
        self.assertIn("MANIFEST_DUPLICATE_ID", report["reasons"])
        self.assertIn("SUBJECT_VARIANTS_CONFLICT", first(traces=[trace(), trace(phase="backend-response", subjectPrincipalId=SUB)])["reasons"])

    def test_actual_model_alias_and_appgw_edge_must_match_all_observations(self):
        e = expectation(expectedModel="synthetic-deployment", expectedAlias="synthetic-alias")
        self.assertEqual(first(e)["status"], "pass")
        self.assertIn("MODEL_MISMATCH", first(e, traces=[trace(e, targetModel="changed"), trace(e, "backend-response")])["reasons"])
        self.assertIn("ALIAS_MISMATCH", first(e, traces=[trace(e, requestedModel="changed"), trace(e, "backend-response")])["reasons"])
        self.assertIn("APPGW_EDGE_CONFLICT", first(traces=[trace(contextTraceId=CHILD), trace(phase="backend-response")])["reasons"])

    def test_pii_success_model_and_all_scoped_trace_timestamps_are_checked(self):
        e = expectation(expectedPiiModelVersion="2000-01-01")
        pii = {"resourceId": scope().apim_id, "nativeLogAt": WHEN, "requestId": RID, "traceRequestId": RID,
               "traceType": "core-pii", "traceStatus": 200, "piiModelVersion": "2000-01-01", "piiDetectedCount": 0,
               "piiDocumentCount": 1, "piiUpstreamAttempt": 1}
        events = [trace(e), trace(e, "backend-response"), pii]
        self.assertEqual(first(e, traces=events)["status"], "pass")
        self.assertIn("PII_SUCCESS_TRACE_MISSING", first(e)["reasons"])
        self.assertIn("MODEL_MISMATCH", first(e, traces=[*events[:-1], {**pii, "piiModelVersion": "changed"}])["reasons"])
        self.assertIn("ROW_SCOPE_MISMATCH", first(e, traces=[*events[:-1], {**pii, "nativeLogAt": "2000-01-01T01:00:00Z"}])["reasons"])
        self.assertIn("TRACE_PHASE_CODE_INVALID", first(traces=[trace(code="unreviewed-code"), trace(phase="backend-response")])["reasons"])

    def test_invalid_bearer_needs_explicit_rejection_and_complete_non_backend_evidence(self):
        e = expectation(status=401, authRejected=True)
        rejection = trace(e, "rejected", code="INVALID_AUTHENTICATION", traceStatus=401, authorization="unconfirmed", subjectStatus="unverified")
        good = first(e, [native(e, backendResponseCode=0, traceCount=1)], [rejection])
        self.assertEqual(good["status"], "pass")
        self.assertEqual(first(e, [native(e, backendResponseCode=0)], [])["status"], "pending")
        incomplete = native(e); incomplete.pop("backendResponseCode")
        self.assertEqual(first(e, [incomplete], [rejection])["status"], "pending")
        self.assertIn("REJECTION_BACKEND_OBSERVED", first(e, [native(e)], [rejection])["reasons"])
        self.assertIn("REJECTION_ALLOWED_TRACE", first(e, [native(e, backendResponseCode=0)], [rejection, trace(e)])["reasons"])

    def test_success_cannot_override_any_later_backend_failure_policy_error_or_rejection(self):
        failures = (("backend-response", "BACKEND_RESPONSE", 500), ("policy-error", "POLICY_ERROR", 500),
                    ("rejected", "AUTHORIZATION_DENIED", 403))
        for phase, code, status in failures:
            with self.subTest(phase=phase):
                report = first(traces=[trace(), trace(phase="backend-response"), trace(phase=phase, code=code, traceStatus=status)])
                self.assertEqual(report["status"], "inconsistent")
                self.assertIn("TRACE_OUTCOME_CONFLICT", report["reasons"])
                self.assertEqual(second(first_report=report)["status"], "inconsistent")
                row = aggregate()
                row["customResults"].append({"event": "gateway-request", "phase": phase, "code": code, "status": status})
                aggregate_report = second(aggregate_rows=[row])
                self.assertEqual(aggregate_report["status"], "inconsistent")
                self.assertIn("TRACE_OUTCOME_CONFLICT", aggregate_report["reasons"])

    def test_invalid_bearer_null_backend_is_unknown_and_verified_rejection_is_inconsistent(self):
        e = expectation(status=401, authRejected=True)
        rejection = trace(e, "rejected", code="INVALID_AUTHENTICATION", traceStatus=401, authorization="unconfirmed", subjectStatus="unverified")
        null_report = first(e, [native(e, backendResponseCode=None)], [rejection])
        self.assertEqual(null_report["status"], "pending")
        self.assertIn("REJECTION_BACKEND_UNKNOWN", null_report["reasons"])
        verified = {**rejection, "subjectStatus": "verified"}
        report = first(e, [native(e, backendResponseCode=0)], [verified])
        self.assertEqual(report["status"], "inconsistent")
        self.assertIn("SUBJECT_UNEXPECTED_ON_REJECTION", report["reasons"])
        row = aggregate(e, backendResponseCode=None, authenticatedSubjectStatus="unverified")
        report = second(e, first_report=first(e, [native(e, backendResponseCode=0)], [rejection]), aggregate_rows=[row])
        self.assertEqual(report["status"], "pending")
        self.assertIn("REJECTION_BACKEND_UNKNOWN", report["reasons"])

    def test_present_unknown_query_observations_remain_pending_and_conflicting_status_is_inconsistent(self):
        query = trace(traceType="query-guard", schemaVersion=2, phase="received", traceStatus=None,
                      queryRoute="responses", queryHttpMethod="POST", queryPresence="none", queryDuplicates="no", queryStatusClass="unknown")
        self.assertEqual(first(traces=[trace(), query, trace(phase="backend-response")])["status"], "pass")
        unknown = {**query, "phase": "unknown", "queryRoute": "unknown", "queryHttpMethod": "unknown",
                   "queryPresence": "unknown", "queryDuplicates": "unknown", "queryStatusClass": "unknown"}
        report = first(traces=[trace(), unknown, trace(phase="backend-response")])
        self.assertEqual(report["status"], "pending")
        self.assertIn("QUERY_OBSERVATION_UNKNOWN", report["reasons"])
        bad = {**query, "phase": "backend-response", "queryStatusClass": "5xx"}
        self.assertIn("QUERY_STATUS_CONFLICT", first(traces=[trace(), bad, trace(phase="backend-response")])["reasons"])
        row = aggregate()
        row["customResults"].append({"event": "query-guard", "phase": "unknown", "code": "", "status": None})
        self.assertEqual(second(aggregate_rows=[row])["status"], "pending")

    def test_expected_functional_403_preserves_verified_subject_and_rejects_backend_or_success_evidence(self):
        for e in (expectation(status=403, expectedOutcome="denied", expectedAuditCode="AUTHORIZATION_DENIED"),
                  expectation(status=403, mode="key", subscriptionId="synthetic-subscription", expectedOutcome="denied", expectedAuditCode="AUTHORIZATION_DENIED")):
            # The API-level decision is allowed; the CI feature guard rejects
            # this target before the accepted/backend execution stages.
            rejection = trace(e, "rejected", code="AUTHORIZATION_DENIED", traceStatus=403, authorization="allowed")
            report = first(e, [native(e, backendResponseCode=0)], [rejection])
            self.assertEqual(report["status"], "pass")
            self.assertEqual(second(e, first_report=report, aggregate_rows=[aggregate(e, backendResponseCode=0)])["status"], "pass")
            self.assertIn("REJECTION_BACKEND_OBSERVED", first(e, [native(e)], [rejection])["reasons"])
            self.assertIn("REJECTION_ALLOWED_TRACE", first(e, [native(e, backendResponseCode=0)], [rejection, trace(e)])["reasons"])
            self.assertEqual(first(e, [native(e, backendResponseCode=0)], [{**rejection, "subjectStatus": "unverified"}])["status"], "pending")
            self.assertIn("AUTHORIZATION_MISMATCH", first(e, [native(e, backendResponseCode=0)], [{**rejection, "authorization": "denied"}])["reasons"])
            self.assertIn("TRACE_OUTCOME_CONFLICT", first(e, [native(e, backendResponseCode=0)], [{**rejection, "code": "UNREVIEWED_DENIAL"}])["reasons"])
            with self.assertRaises(checks.AuditInputError):
                checks.build_first_phase(scope(), [{**e, "expectedAuditCode": "UNREVIEWED_DENIAL"}])
            with self.assertRaises(checks.AuditInputError):
                checks.build_first_phase(scope(), [{**e, "status": 500}])

    def test_ci_denied_api_allowed_identity_must_still_match_the_verified_subject(self):
        cases = (expectation(status=403, expectedOutcome="denied", expectedAuditCode="AUTHORIZATION_DENIED"),
                 expectation(status=403, mode="key", subscriptionId="synthetic-subscription", expectedOutcome="denied", expectedAuditCode="AUTHORIZATION_DENIED"))
        for e in cases:
            rejection = trace(e, "rejected", code="AUTHORIZATION_DENIED", traceStatus=403, authorization="allowed")
            patches = [{"callerId": "synthetic-unmatched-caller"}, {"method": "other"}]
            patches += [{"subscriptionId": "synthetic-other-subscription"}] if e["mode"] == "key" else [
                {"verifiedTenantId": SUB}, {"verifiedPrincipalId": SUB}, {"verifiedClientId": SUB},
                {"allocationId": "synthetic-unmatched-allocation"}]
            for patch in patches:
                with self.subTest(mode=e["mode"], field=tuple(patch)):
                    report = first(e, [native(e, backendResponseCode=0)], [{**rejection, **patch}])
                    self.assertEqual(report["status"], "inconsistent")
                    self.assertIn("AUTHORIZATION_MISMATCH", report["reasons"])


class Phase12AuditSecondPhaseTests(unittest.TestCase):
    def test_complete_native_access_and_aggregate_are_required_but_normal_waf_match_events_are_optional(self):
        self.assertEqual(second()["status"], "pass")
        for report in (second(aggregate_rows=[]), second(access_rows=[]), second(complete=False)):
            self.assertEqual(report["status"], "pending")
        self.assertEqual(second(access_rows=[access(serverStatus=None)])["status"], "pending")
        self.assertIn("ACCESS_STATUS_MISMATCH", second(access_rows=[access(httpStatus=403)])["reasons"])
        self.assertIn("APPGW_JOIN_INCOMPLETE", second(aggregate_rows=[aggregate(appGatewayCorrelationStatus="unmatched")])["reasons"])
        blocked = {"resourceId": scope().appgw_id, "TimeGenerated": WHEN, "transactionId": EDGE, "action": "Blocked", "ruleId": "synthetic-rule"}
        self.assertIn("WAF_BLOCK_OBSERVED", second(waf_rows=[blocked])["reasons"])

    def test_chat_numeric_usage_deduplicates_equal_logs_and_rejects_invalid_or_conflicting_measurements(self):
        e = expectation(apiKind="chat", requireChatUsage=True, expectedModel="synthetic-deployment")
        usage = {"resourceId": scope().apim_id, "TimeGenerated": WHEN, "requestId": RID, "targetModel": e["expectedModel"],
                 "providerRequestId": "req_synthetic", "promptTokens": 12, "completionTokens": 4, "totalTokens": 16}
        self.assertEqual(second(e, usage=[usage, usage])["status"], "pass")
        self.assertIn("USAGE_MISSING", second(e)["reasons"])
        self.assertIn("USAGE_INVALID", second(e, usage=[{**usage, "promptTokens": True}])["reasons"])
        self.assertIn("USAGE_INVALID", second(e, usage=[{**usage, "totalTokens": 17}])["reasons"])
        self.assertIn("USAGE_VARIANTS_CONFLICT", second(e, usage=[usage, {**usage, "providerRequestId": "req_other"}])["reasons"])
        with self.assertRaises(checks.AuditInputError):
            checks.build_first_phase(scope(), [expectation(apiKind="responses", requireChatUsage=True, expectedModel="synthetic-deployment")])

    def test_upstream_pending_is_preserved_and_arbitrary_reason_status_edges_and_refs_are_not_echoed(self):
        for target in ("global", "request"):
            report = first()
            decision = report if target == "global" else report["requests"][RID]
            decision.update(status="pending", reasons=())
            result = second(first_report=report)
            self.assertEqual(result["status"], "pending")
            self.assertIn("UPSTREAM_PENDING", result["reasons"])
        for mutation in ("reason", "status", "edge", "reference"):
            report = first()
            if mutation == "reason":
                report["requests"][RID].update(status="pending", reasons=("SYNTHETIC_UNTRUSTED_DETAIL",))
            elif mutation == "status":
                report["requests"][RID]["status"] = "unreviewed-status"
            elif mutation == "edge":
                report["edgeIds"][RID] = ("unreviewed-edge",)
            else:
                report["childReferences"][RID] = ({"requestId": CHILD, "source": "unreviewed-reference"},)
            result = second(first_report=report)
            self.assertEqual(result["status"], "inconsistent")
            self.assertEqual(result["reasons"], ("INPUT_INVALID_REPORT",))
            self.assertNotIn("SYNTHETIC_UNTRUSTED_DETAIL", str(result))


class Phase12AuditMcpChildTests(unittest.TestCase):
    def parent(self, **changes):
        return expectation(**{"apiId": "synthetic-mcp", "operationId": None, "transport": "mcp", "rpcMethod": "initialize",
                              "restApiId": "synthetic-pii", "restOperationId": "pii_redact_text", **changes})

    def test_mcp_outer_operation_is_not_the_rest_tool_and_protocol_success_is_not_a_remote_call_claim(self):
        e = self.parent()
        report = first(e, [native(e, backendResponseCode=0)])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["childReferences"][RID], ())
        with self.assertRaisesRegex(checks.AuditInputError, "CHILD_LINK_UNAVAILABLE"):
            checks.build_child_lookup(scope(), report, RID, "synthetic-pii", "pii_redact_text")
        wrong = {**e, "operationId": "pii_redact_text"}
        self.assertIn("API_OPERATION_MISMATCH", first(wrong, [native(e)], [trace(e), trace(e, "backend-response")])["reasons"])

    def test_validated_pii_result_id_is_an_optional_separate_link_and_child_still_needs_exact_subject_and_operation(self):
        e = self.parent(rpcMethod="tools/call", piiResultRequestId=CHILD, piiResultValidated=True)
        report = first(e)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["childReferences"][RID], ({"requestId": CHILD, "source": "pii-result"},))
        query = checks.build_child_lookup(scope(), report, RID, "synthetic-pii", "pii_redact_text")
        self.assertIn(CHILD, query)
        self.assertNotIn(RID, query)
        child = expectation(requestId=CHILD, apiId="synthetic-pii", operationId="pii_redact_text", requireAppGateway=False)
        self.assertEqual(checks.evaluate_child_reference(e, child, report, first(child))["status"], "pass")
        self.assertEqual(checks.evaluate_child_reference(e, child, report, pending_report())["status"], "pending")
        self.assertIn("CHILD_SUBJECT_MISMATCH", checks.evaluate_child_reference(e, {**child, "tenantId": SUB}, report, first(child))["reasons"])
        self.assertIn("CHILD_API_OPERATION_MISMATCH", checks.evaluate_child_reference(e, {**child, "operationId": "changed"}, report, first(child))["reasons"])
        with self.assertRaises(checks.AuditInputError):
            checks.build_first_phase(scope(), [{**e, "piiResultValidated": False}])


if __name__ == "__main__":
    unittest.main()
