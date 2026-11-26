"""Narrow notification-control completion; no live data or requests."""
import copy
import unittest
from operations import apim_phase12_audit_checks as checks
import test_phase12_audit_checks as f

FLAG = "sdkInitializedNotificationVerified"

def sdk_record():
    return {"kind":"mcp","apiId":"pii-v1-mcp","mode":"bearer","initialized":True,
        "toolsCapabilityAdvertised":True,"toolsListReceived":True,"toolContractCheckedBeforeCall":True,
        "normalTerminal":True,"succeeded":True,"passed":True,"toolPostCount":1,"toolsListMorePages":False,
        "httpSteps":[{"role":"mcp","rpcMethod":"notifications/initialized","httpMethod":"POST","status":202},
                     {"role":"mcp","rpcMethod":"tools/call","httpMethod":"POST","status":200}]}

def expected(**changes):
    return f.expectation(apiId="pii-v1-mcp",operationId=None,transport="mcp",measurementRole="facade",
        rpcMethod="notifications/initialized",status=202,**{FLAG:True,**changes})

def first(e=None, **changes):
    e=e or expected()
    rows=changes.pop("native_rows",[f.native(e,backendResponseCode=None,traceCount=1)])
    events=changes.pop("traces",[f.trace(e)])
    return f.first(e,native_rows=rows,traces=events,**changes)

def second(e=None, report=None, **changes):
    e=e or expected()
    row=f.aggregate(e,backendResponseCode=None,customResults=[
        {"event":"gateway-request","phase":"accepted","code":"GATEWAY_ALLOWED","status":0}])
    return f.second(e,first_report=report or first(e),aggregate_rows=changes.pop("aggregate_rows",[row]),**changes)

class Notification202Tests(unittest.TestCase):
    def test_sdk_qualification_uses_original_complete_proof_and_subsequent_successful_tool(self):
        source=sdk_record()
        self.assertTrue(checks.qualify_initialized_notification(source,0))
        for field in ("initialized","toolsCapabilityAdvertised","toolsListReceived","toolContractCheckedBeforeCall","normalTerminal","succeeded","passed"):
            for value in (False,None,1):
                record=copy.deepcopy(source);record[field]=value
                self.assertFalse(checks.qualify_initialized_notification(record,0),field)
        for patch in ({"toolPostCount":True},{"toolPostCount":0},{"toolsListMorePages":True},
                      {"failureClass":"TimeoutError"},{"apiId":"external-mcp"},{"mode":"invalid-bearer"}):
            self.assertFalse(checks.qualify_initialized_notification({**source,**patch},0))
        record=copy.deepcopy(source);record["httpSteps"][0]["status"]=202.0
        self.assertFalse(checks.qualify_initialized_notification(record,0))
        record=copy.deepcopy(source);record["httpSteps"][1]["status"]=500
        self.assertFalse(checks.qualify_initialized_notification(record,0))
        record=copy.deepcopy(source);record["httpSteps"].reverse()
        self.assertFalse(checks.qualify_initialized_notification(record,1))
        record=copy.deepcopy(source);record["httpSteps"].append(copy.deepcopy(record["httpSteps"][1]))
        self.assertFalse(checks.qualify_initialized_notification(record,0))
        self.assertFalse(checks.qualify_initialized_notification(source,True))

    def test_qualified202_has_verified_accepted_completion_in_both_phases(self):
        self.assertEqual(first()["status"],"pass")
        self.assertEqual(second()["status"],"pass")
        for enabled in (False,None):
            e=expected(**{FLAG:enabled})
            if enabled is None:
                e.pop(FLAG)
            report=first(e)
            self.assertEqual(report["status"],"pending")
            self.assertIn("COMPLETION_TRACE_MISSING",report["reasons"])

    def test_flag_cannot_broaden_api_protocol_rpc_models_child_or_backend_claims(self):
        patches=({"apiId":"external-mcp"},{"transport":"rest","operationId":"notify"},
                 {"measurementRole":"execution"},{"rpcMethod":"initialize"},
                 {"rpcMethod":"tools/list"},{"rpcMethod":"tools/call"},{"httpMethod":"GET"},
                 {"status":200},{"expectedModel":"synthetic-model"},{"expectedBackendStatus":202},
                 {"expectedPiiModelVersion":"2000-01-01"},{"restApiId":"pii-v1"},
                 {"restOperationId":"pii_redact_text"},{"requireAppGateway":False},
                 {"requireChatUsage":True,"apiKind":"chat","expectedModel":"synthetic-model"},
                 {"authRejected":True,"status":401},{FLAG:1},{"piiResultValidated":0})
        for patch in patches:
            e=expected();e.update(patch)
            report=first(e)
            self.assertEqual(report["status"],"inconsistent",str(tuple(patch)))
            self.assertTrue(
                {"INPUT_INVALID_EXPECTATION", "INPUT_INVALID_NUMERIC_CLAIM"}.intersection(report["reasons"]),
                str(tuple(patch)),
            )

    def test_native_context_identity_partial_and_late_failures_are_still_enforced(self):
        e=expected()
        for report in (first(native_rows=[]),first(traces=[]),first(complete=False),
                       first(traces=[f.trace(e,contextReceivedAt=None)])):
            self.assertEqual(report["status"],"pending")
        for report in (first(native_rows=[f.native(e,responseCode=200)]),
                       first(traces=[f.trace(e,subjectTenantId=f.SUB)])):
            self.assertEqual(report["status"],"inconsistent")
        for phase,code,status in (("backend-response","BACKEND_RESPONSE",500),
                                  ("policy-error","POLICY_ERROR",500),
                                  ("rejected","AUTHORIZATION_DENIED",403)):
            report=first(traces=[f.trace(e),f.trace(e,phase=phase,code=code,traceStatus=status)])
            self.assertEqual(report["status"],"inconsistent")
            self.assertIn("TRACE_OUTCOME_CONFLICT",report["reasons"])
            row=f.aggregate(e,backendResponseCode=None,customResults=[
                {"event":"gateway-request","phase":"accepted","code":"GATEWAY_ALLOWED","status":0},
                {"event":"gateway-request","phase":phase,"code":code,"status":status}])
            self.assertEqual(second(aggregate_rows=[row])["status"],"inconsistent")

    def test_appgw202_and_complete_aggregate_remain_required(self):
        self.assertEqual(second()["status"],"pass")
        self.assertEqual(second(access_rows=[])["status"],"pending")
        self.assertEqual(second(access_rows=[f.access(expected(),httpStatus=200)])["status"],"inconsistent")
        self.assertEqual(second(complete=False)["status"],"pending")
        self.assertEqual(second(aggregate_rows=[f.aggregate(expected(),customResults=[])])["status"],"pending")

    def test_ordinary_initialization_list_tool_and_invalid_bearer_null_semantics_are_unchanged(self):
        for rpc in ("initialize","tools/list","tools/call"):
            e=expected();e.update(status=200,rpcMethod=rpc);e.pop(FLAG)
            self.assertEqual(first(e)["status"],"pending")
        e=f.expectation(status=401,authRejected=True)
        rejection=f.trace(e,"rejected",code="INVALID_AUTHENTICATION",traceStatus=401,
                          authorization="unconfirmed",subjectStatus="unverified")
        report=f.first(e,[f.native(e,backendResponseCode=None)],[rejection])
        self.assertEqual(report["status"],"pending")
        self.assertIn("REJECTION_BACKEND_UNKNOWN",report["reasons"])

if __name__=="__main__":
    unittest.main()
