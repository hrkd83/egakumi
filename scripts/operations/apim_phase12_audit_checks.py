"""Bounded APIM audit queries and pure evaluation; no I/O, auth or VM operations."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

MAX_IDS = 512
MAX_QUERY_BYTES = 1024 * 1024
MAX_WINDOW = timedelta(hours=2)
TRACE_LIMIT = 128
REASON_CODES = frozenset(["ACCESS_METADATA_MISSING","ACCESS_ROW_MISSING","ACCESS_STATUS_MISMATCH","ACCESS_VARIANTS_CONFLICT","AGGREGATE_INCONSISTENT","AGGREGATE_METADATA_INCOMPLETE","AGGREGATE_RESULTS_INVALID","AGGREGATE_RESULTS_MISSING","AGGREGATE_RESULTS_TRUNCATED","AGGREGATE_ROW_MISSING","AGGREGATE_VARIANTS_CONFLICT","ALIAS_MISMATCH","API_OPERATION_MISMATCH","APPGW_EDGE_CONFLICT","APPGW_EDGE_MISSING","APPGW_JOIN_INCOMPLETE","AUTHORIZATION_MISMATCH","AUTHORIZATION_MISSING","BACKEND_STATUS_MISMATCH","BACKEND_STATUS_MISSING","CHILD_API_OPERATION_MISMATCH","CHILD_AUDIT_MISSING","CHILD_EXPECTATION_MISSING","CHILD_LINK_UNAVAILABLE","CHILD_SUBJECT_MISMATCH","COMPLETION_TRACE_MISSING","CONTEXT_MISMATCH","CONTEXT_MISSING","FIRST_PHASE_MISSING","HTTP_STATUS_MISMATCH","INPUT_INVALID_AGGREGATE_SOURCE","INPUT_INVALID_CHILD_REFERENCE","INPUT_INVALID_EXPECTATION","INPUT_INVALID_ID","INPUT_INVALID_MANIFEST","INPUT_INVALID_NUMERIC_CLAIM","INPUT_INVALID_REPORT","INPUT_INVALID_RESOURCE","INPUT_INVALID_ROW_CAP","INPUT_INVALID_SCOPE","INPUT_INVALID_SUBJECT","INPUT_INVALID_WINDOW","INPUT_QUERY_TOO_LARGE","INPUT_RESOURCE_SCOPE_MISMATCH","MANIFEST_DUPLICATE_ID","MEASUREMENT_ROLE_MISMATCH","MODEL_MISMATCH","MODEL_MISSING","NATIVE_METADATA_MISSING","NATIVE_ROW_MISSING","NATIVE_VARIANTS_CONFLICT","PARENT_AUDIT_MISSING","PII_SUCCESS_TRACE_MISSING","QUERY_METHOD_MISMATCH","QUERY_OBSERVATION_INVALID","QUERY_OBSERVATION_UNKNOWN","QUERY_STATUS_CONFLICT","REJECTION_ALLOWED_TRACE","REJECTION_BACKEND_OBSERVED","REJECTION_BACKEND_UNKNOWN","REJECTION_TRACE_MISSING","RESULT_INCOMPLETE","RESULT_INVALID_ROW","RESULT_SCOPE_MISMATCH","ROW_SCOPE_MISMATCH","SUBJECT_INCONSISTENT","SUBJECT_MISMATCH","SUBJECT_UNEXPECTED_ON_REJECTION","SUBJECT_UNVERIFIED","SUBJECT_VARIANTS_CONFLICT","TRACE_INCOMPLETE","TRACE_METADATA_MISSING","TRACE_MISSING","TRACE_OUTCOME_CONFLICT","TRACE_OUTCOME_UNKNOWN","TRACE_PHASE_CODE_INVALID","TRACE_REQUEST_MISMATCH","TRACE_REQUEST_MISSING","TRACE_SCHEMA_INVALID","TRACE_SCHEMA_MISSING","TRACE_STATUS_INVALID","TRACE_STATUS_MISSING","UNEXPECTED_REQUEST_ID","UPSTREAM_INCONSISTENT","UPSTREAM_PENDING","USAGE_INVALID","USAGE_MISSING","USAGE_SOURCE_MISSING","USAGE_VARIANTS_CONFLICT","WAF_BLOCK_OBSERVED","WAF_FIELDS_MISSING"])
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_GUID = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")
_RESOURCE = r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/[A-Za-z0-9._()-]+/providers/{}/[A-Za-z0-9._()-]+"
PROJECTION = """requestId,nativeLogAt,nativeLogRows,nativeMetadataStatus,apiId,operationId,responseCode,backendResponseCode,
authenticatedSubjectStatus,subjectKind,method,callerId,subscriptionId,tenantId,principalId,clientId,allocationId,
requestContextStatus,inconsistentSubjectCount,malformedContextCount,unlinkedV2ObservationCount,eventNames,gatewayPhases,queryGuardPhases,customResults,
appGatewayTraceId,appGatewayTraceCandidates,appGatewayCorrelationStatus,appGatewayHttpStatus,appGatewayServerStatus,appGatewayLogRows,appGatewayDistinctVariants"""

class AuditInputError(ValueError):
    """Messages are fixed codes, never caller values."""

def _fail(code):
    raise AuditInputError(code)

def canonical_id(value):
    if not isinstance(value, str) or not _GUID.fullmatch(value):
        _fail("INPUT_INVALID_ID")
    result = str(uuid.UUID(value))
    if result == str(uuid.UUID(int=0)):
        _fail("INPUT_INVALID_ID")
    return result

def _id(value):
    try:
        return canonical_id(value)
    except AuditInputError:
        return ""

def _utc(value):
    if isinstance(value, str):
        if not value.endswith(("Z", "+00:00")):
            _fail("INPUT_INVALID_WINDOW")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail("INPUT_INVALID_WINDOW")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _fail("INPUT_INVALID_WINDOW")
    return value.astimezone(timezone.utc)

def _name(value):
    return isinstance(value, str) and _NAME.fullmatch(value) is not None

def _text(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)

def _integer(value):
    return type(value) is int

@dataclass(frozen=True)
class AuditScope:
    apim_id: str
    appgw_id: str
    start: object
    end: object
    row_cap: int = 2000

    def __post_init__(self):
        subscriptions = []
        for value, provider in ((self.apim_id, "Microsoft.ApiManagement/service"),
                                (self.appgw_id, "Microsoft.Network/applicationGateways")):
            if not isinstance(value, str) or len(value) > 2048:
                _fail("INPUT_INVALID_RESOURCE")
            match = re.fullmatch(_RESOURCE.replace("{}", re.escape(provider)), value, re.IGNORECASE)
            if not match:
                _fail("INPUT_INVALID_RESOURCE")
            subscriptions.append(canonical_id(match[1]))
        if subscriptions[0] != subscriptions[1]:
            _fail("INPUT_RESOURCE_SCOPE_MISMATCH")
        start, end = _utc(self.start), _utc(self.end)
        if not start < end or end - start > MAX_WINDOW:
            _fail("INPUT_INVALID_WINDOW")
        if not _integer(self.row_cap) or not 1 <= self.row_cap <= 5000:
            _fail("INPUT_INVALID_ROW_CAP")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

@dataclass(frozen=True)
class QueryRows:
    rows: object = ()
    complete: bool = True


def qualify_initialized_notification(record, step_index):
    """Only original successful SDK evidence can qualify this control response."""
    required = ("initialized", "toolsCapabilityAdvertised", "toolsListReceived",
                "toolContractCheckedBeforeCall", "normalTerminal", "succeeded", "passed")
    if not isinstance(record, dict) or record.get("kind") != "mcp" or record.get("apiId") != "pii-v1-mcp" or record.get("mode") not in ("key", "bearer"):
        return False
    if record.get("failureClass") or record.get("failureLeaves") or record.get("rpcErrorCodes"):
        return False
    if not all(record.get(key) is True for key in required):
        return False
    if type(record.get("toolPostCount")) is not int or record["toolPostCount"] != 1 or record.get("toolsListMorePages") is not False:
        return False
    steps = record.get("httpSteps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 64 or type(step_index) is not int or not 0 <= step_index < len(steps):
        return False
    if any(not isinstance(step, dict) for step in steps):
        return False
    step = steps[step_index]
    if type(step.get("status")) is not int:
        return False
    if any(step.get(key) != value for key, value in (("role", "mcp"), ("rpcMethod", "notifications/initialized"), ("httpMethod", "POST"), ("status", 202))):
        return False
    tool_steps = [(index, item) for index, item in enumerate(steps) if item.get("rpcMethod") == "tools/call"]
    if len(tool_steps) != 1:
        return False
    index, tool = tool_steps[0]
    return index > step_index and type(tool.get("status")) is int and all(tool.get(key) == value for key, value in
        (("role", "mcp"), ("httpMethod", "POST"), ("status", 200)))

def _initialized_notification_completed(e):
    return (e.get("sdkInitializedNotificationVerified") is True
        and e.get("apiId") == "pii-v1-mcp" and e.get("transport") == "mcp"
        and e.get("measurementRole") == "facade" and e.get("rpcMethod") == "notifications/initialized"
        and e.get("httpMethod") == "POST" and e.get("status") == 202
        and e.get("expectedOutcome") == "success" and e.get("authRejected") is False
        and e.get("requireAppGateway") is True and e.get("requireChatUsage") is False
        and not any(e.get(key) is not None for key in
            ("expectedModel", "expectedAlias", "expectedPiiModelVersion", "expectedBackendStatus",
             "piiResultRequestId", "restApiId", "restOperationId"))
        and (e.get("piiResultValidated") is None or e.get("piiResultValidated") is False))


def _manifest(expectations):
    if not isinstance(expectations, (list, tuple)):
        _fail("INPUT_INVALID_MANIFEST")
    entries = list(expectations)
    if not entries or len(entries) > MAX_IDS:
        _fail("INPUT_INVALID_MANIFEST")
    result = {}
    for item in entries:
        if not isinstance(item, dict):
            _fail("INPUT_INVALID_MANIFEST")
        e = dict(item)
        rid = canonical_id(e.get("requestId"))
        if rid in result:
            _fail("MANIFEST_DUPLICATE_ID")
        e["requestId"] = rid
        if not _name(e.get("apiId")) or not isinstance(e.get("httpMethod"), str) or re.fullmatch(r"[A-Z]{1,20}", e["httpMethod"]) is None:
            _fail("INPUT_INVALID_EXPECTATION")
        if e.get("transport", "rest") not in ("rest", "sse", "mcp", "websocket") or e.get("mode") not in ("key", "bearer"):
            _fail("INPUT_INVALID_EXPECTATION")
        if e.get("operationId") is None:
            if e.get("transport") != "mcp":
                _fail("INPUT_INVALID_EXPECTATION")
        elif not _name(e["operationId"]):
            _fail("INPUT_INVALID_EXPECTATION")
        if not _integer(e.get("status")) or not 100 <= e["status"] <= 599:
            _fail("INPUT_INVALID_EXPECTATION")
        if e["mode"] == "key":
            if not _text(e.get("subscriptionId")):
                _fail("INPUT_INVALID_SUBJECT")
        else:
            for field in ("tenantId", "principalId"):
                e[field] = canonical_id(e.get(field))
            if e.get("clientId") is not None:
                e["clientId"] = canonical_id(e["clientId"])
        e.setdefault("transport", "rest")
        e.setdefault("measurementRole", "facade" if e["transport"] == "mcp" else "execution")
        e.setdefault("requireAppGateway", True)
        e.setdefault("requireChatUsage", False)
        e.setdefault("authRejected", False)
        e.setdefault("expectedOutcome", "success")
        if e["expectedOutcome"] not in ("success", "denied"):
            _fail("INPUT_INVALID_EXPECTATION")
        if e["expectedOutcome"] == "denied":
            if e["authRejected"] or e["status"] != 403 or e.get("expectedAuditCode") != "AUTHORIZATION_DENIED":
                _fail("INPUT_INVALID_EXPECTATION")
        elif e.get("expectedAuditCode") is not None:
            _fail("INPUT_INVALID_EXPECTATION")
        if any(type(e[field]) is not bool for field in ("requireAppGateway", "requireChatUsage", "authRejected")):
            _fail("INPUT_INVALID_EXPECTATION")
        if e["measurementRole"] not in ("execution", "facade", "observation"):
            _fail("INPUT_INVALID_EXPECTATION")
        if e["authRejected"] and (e["mode"] != "bearer" or e["status"] != 401):
            _fail("INPUT_INVALID_EXPECTATION")
        for field in ("expectedModel", "expectedAlias", "expectedPiiModelVersion", "restApiId", "restOperationId"):
            if e.get(field) is not None and not _name(e[field]):
                _fail("INPUT_INVALID_EXPECTATION")
        if e.get("expectedAlias") and not e.get("expectedModel"):
            _fail("INPUT_INVALID_EXPECTATION")
        if e.get("expectedBackendStatus") is not None and (not _integer(e["expectedBackendStatus"]) or not 100 <= e["expectedBackendStatus"] <= 599):
            _fail("INPUT_INVALID_EXPECTATION")
        if e["requireChatUsage"] and (e["authRejected"] or e["expectedOutcome"] != "success" or e.get("apiKind") != "chat" or e["transport"] not in ("rest", "sse") or e["measurementRole"] != "execution" or not e.get("expectedModel")):
            _fail("INPUT_INVALID_NUMERIC_CLAIM")
        if e.get("piiResultRequestId") is not None:
            if e.get("piiResultValidated") is not True or e["transport"] != "mcp" or e.get("rpcMethod") != "tools/call" or e["authRejected"] or e["expectedOutcome"] != "success" or e["status"] != 200:
                _fail("INPUT_INVALID_CHILD_REFERENCE")
            e["piiResultRequestId"] = canonical_id(e["piiResultRequestId"])
        if "sdkInitializedNotificationVerified" in e:
            if type(e["sdkInitializedNotificationVerified"]) is not bool:
                _fail("INPUT_INVALID_EXPECTATION")
            if e["sdkInitializedNotificationVerified"] and not _initialized_notification_completed(e):
                _fail("INPUT_INVALID_EXPECTATION")
        result[rid] = e
    return result

def _literal(value):
    return json.dumps(value, ensure_ascii=True)

def _ids(values):
    result = sorted({canonical_id(value) for value in values})
    if len(result) > MAX_IDS:
        _fail("INPUT_INVALID_MANIFEST")
    return "dynamic(" + json.dumps(result) + ")"

def _finish(scope, query):
    value = query.rstrip() + "\n| take " + str(scope.row_cap + 1) + "\n"
    if len(value.encode("utf-8")) > MAX_QUERY_BYTES:
        _fail("INPUT_QUERY_TOO_LARGE")
    return value

def _header(scope, ids):
    if not isinstance(scope, AuditScope):
        _fail("INPUT_INVALID_SCOPE")
    return f"""let apimResourceId = {_literal(scope.apim_id)};
let appGatewayResourceId = {_literal(scope.appgw_id)};
let startTime = datetime({scope.start.isoformat()});
let endTime = datetime({scope.end.isoformat()});
let selectedIds = {_ids(ids)};
let canonicalId = (v:string) {{ iff(v matches regex @'^[0-9a-fA-F]{{32}}$|^[0-9a-fA-F]{{8}}(-[0-9a-fA-F]{{4}}){{3}}-[0-9a-fA-F]{{12}}$' and tostring(toguid(v)) != '00000000-0000-0000-0000-000000000000', tostring(toguid(v)), '') }};
let logicalApiId = (v:string) {{ tostring(split(v, ';')[0]) }};
"""

_NATIVE = """ApiManagementGatewayLogs
| where _ResourceId =~ apimResourceId and TimeGenerated between (startTime .. endTime)
| extend requestId = canonicalId(tostring(CorrelationId))
| where requestId in (selectedIds)
"""
_TRACE = """| mv-expand with_itemindex=traceOrdinal trace=TraceRecords limit 128
| extend rawTrace=coalesce(tostring(trace.message),tostring(trace.Message)),traceSource=coalesce(tostring(trace.source),tostring(trace.Source))
| extend r=parse_json(rawTrace)
| extend gateway=tostring(r.event)=='gateway-request' and gettype(r.schemaVersion) in ('long','int') and tolong(r.schemaVersion)==1,
    query=tostring(r.event)=='query-guard' and gettype(r.schemaVersion) in ('long','int') and tolong(r.schemaVersion) in (1,2),
    pii=traceSource=='core-pii' and canonicalId(tostring(r.requestId))==requestId
| where gateway or query or pii
| project resourceId=_ResourceId,nativeLogAt=TimeGenerated,requestId,traceOrdinal,traceType=case(gateway,'gateway-request',query,'query-guard','core-pii'),
    schemaVersion=tolong(r.schemaVersion),auditSchemaVersion=tolong(r.auditSchemaVersion),
    traceRequestId=canonicalId(tostring(r.requestId)),traceApiId=tostring(r.apiId),traceOperationId=tostring(r.operationId),
    phase=tostring(r.phase),code=tostring(r.code),traceStatus=toint(r.status),authorization=tostring(r.authorization),
    method=tostring(r.method),subscriptionId=tostring(r.subscriptionId),callerId=tostring(r.callerId),allocationId=tostring(r.allocationId),
    verifiedTenantId=canonicalId(tostring(r.verifiedTenantId)),verifiedPrincipalId=canonicalId(tostring(r.verifiedPrincipalId)),verifiedClientId=canonicalId(tostring(r.verifiedClientId)),
    subjectStatus=tostring(r.authenticatedSubject.status),subjectKind=tostring(r.authenticatedSubject.kind),subjectMethod=tostring(r.authenticatedSubject.method),
    subjectSubscriptionId=tostring(r.authenticatedSubject.subscriptionId),subjectCallerId=tostring(r.authenticatedSubject.callerId),subjectAllocationId=tostring(r.authenticatedSubject.allocationId),
    subjectTenantId=canonicalId(tostring(r.authenticatedSubject.tenantId)),subjectPrincipalId=canonicalId(tostring(r.authenticatedSubject.principalId)),subjectClientId=canonicalId(tostring(r.authenticatedSubject.clientId)),
    contextRequestId=canonicalId(tostring(r.requestContext.requestId)),contextApiId=tostring(r.requestContext.apiId),contextOperationId=tostring(r.requestContext.operationId),
    contextHttpMethod=tostring(r.requestContext.httpMethod),contextReceivedAt=todatetime(extract_json('$.requestContext.requestReceivedAt',rawTrace,typeof(string))),
    contextTraceId=canonicalId(tostring(r.requestContext.appGatewayTraceId)),
    targetModel=extract_json('$.targetModel',rawTrace,typeof(string)),requestedModel=extract_json('$.requestedModel',rawTrace,typeof(string)),modelAliasApplied=tobool(r.modelAliasApplied),
    backendService=tostring(r.backendService),backendAuthentication=tostring(r.backendAuthentication),measurementRole=tostring(r.measurementRole),usageStatus=tostring(r.usageStatus),usageSource=tostring(r.usageSource),
    backendApimRequestId=canonicalId(tostring(r.backendResponseIds['apim-request-id'])),
    parentLinkStatus=tostring(r.parentLinkStatus),claimedParentId=canonicalId(tostring(r.claimedParentId)),parentClaimActorId=tostring(r.parentClaimActorId),
    queryRoute=tostring(r.route),queryHttpMethod=tostring(r.httpMethod),queryPresence=tostring(r.presence),queryDuplicates=tostring(r.duplicates),queryStatusClass=tostring(r.statusClass),
    piiModelVersion=extract_json('$.modelVersion',rawTrace,typeof(string)),piiDetectedCount=tolong(r.detectedCount),piiInputBytes=tolong(r.inputBytes),piiDocumentCount=tolong(r.documentCount),piiUpstreamAttempt=tolong(r.upstreamAttempt)
"""

def build_first_phase(scope, expectations):
    """Two queries; never emit whole headers, bodies, URIs or raw trace messages."""
    manifest = _manifest(expectations)
    prefix = _header(scope, manifest)
    native = prefix + _NATIVE + """| extend h=RequestHeaders['x-appgw-trace-id']
| extend headerTraceId=canonicalId(case(gettype(h) in ('string','guid'),tostring(h),gettype(h)=='array' and array_length(h)==1,tostring(h[0]),''))
| project resourceId=_ResourceId,nativeLogAt=TimeGenerated,requestId,apiId=tostring(ApiId),logicalApiId=logicalApiId(tostring(ApiId)),operationId=tostring(OperationId),
    httpMethod=tostring(column_ifexists('Method','')),responseCode=toint(ResponseCode),backendResponseCode=toint(BackendResponseCode),headerTraceId,traceCount=array_length(TraceRecords)
"""
    return {"native": _finish(scope, native), "traces": _finish(scope, prefix + _NATIVE + _TRACE)}

def _aggregate(scope, ids, edges, source):
    """Caller supplies trusted canonical audit-identification source text."""
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_QUERY_BYTES or "\x00" in source:
        _fail("INPUT_INVALID_AGGREGATE_SOURCE")
    replacements = {"apimResourceId": _literal(scope.apim_id), "appGatewayResourceId": _literal(scope.appgw_id),
                    "startTime": "datetime(" + scope.start.isoformat() + ")", "endTime": "datetime(" + scope.end.isoformat() + ")"}
    for name, value in replacements.items():
        source, count = re.subn(r"(?m)^let " + name + r" = [^\r\n]*;$", lambda _: "let " + name + " = " + value + ";", source)
        if count != 1:
            _fail("INPUT_INVALID_AGGREGATE_SOURCE")
    for table, variable, field, selected in (("ApiManagementGatewayLogs", "apimResourceId", "CorrelationId", "selectedIds"),
                                              ("AGWAccessLogs", "appGatewayResourceId", "TransactionId", "selectedAppGatewayIds")):
        anchor = "| where _ResourceId =~ " + variable + " and TimeGenerated between (startTime .. endTime)"
        if source.count(anchor) != 1 or table not in source:
            _fail("INPUT_INVALID_AGGREGATE_SOURCE")
        source = source.replace(anchor, anchor + "\n| where canonicalId(tostring(" + field + ")) in (" + selected + ")")
    if not source.rstrip().endswith("| order by nativeLogAt desc"):
        _fail("INPUT_INVALID_AGGREGATE_SOURCE")
    source = "let selectedIds = " + _ids(ids) + ";\nlet selectedAppGatewayIds = " + _ids(edges) + ";\n" + source
    return _finish(scope, source + "\n| project " + PROJECTION)

def build_second_phase(scope, expectations, first_report, aggregate_source):
    """Four queries after observed AppGW edge IDs; source text is caller supplied."""
    manifest = _manifest(expectations)
    first_report = _validate_report(first_report, manifest)
    edges = {edge for values in first_report.get("edgeIds", {}).values() for edge in values}
    prefix = _header(scope, manifest) + "let selectedAppGatewayIds = " + _ids(edges) + ";\n"
    access = prefix + """AGWAccessLogs
| where _ResourceId =~ appGatewayResourceId and TimeGenerated between (startTime .. endTime)
| extend transactionId=canonicalId(tostring(TransactionId))
| where transactionId in (selectedAppGatewayIds)
| project resourceId=_ResourceId,TimeGenerated,transactionId,httpMethod=tostring(column_ifexists('HttpMethod','')),httpStatus=toint(HttpStatus),serverStatus=toint(ServerStatus)
"""
    waf = prefix + """AGWFirewallLogs
| where _ResourceId =~ appGatewayResourceId and TimeGenerated between (startTime .. endTime)
| extend transactionId=canonicalId(tostring(TransactionId))
| where transactionId in (selectedAppGatewayIds)
| project resourceId=_ResourceId,TimeGenerated,transactionId,action=tostring(column_ifexists('Action','')),ruleId=tostring(column_ifexists('RuleId','')),
    ruleSetType=tostring(column_ifexists('RuleSetType','')),ruleSetVersion=tostring(column_ifexists('RuleSetVersion',''))
"""
    chat_ids = [rid for rid, e in manifest.items() if e["requireChatUsage"]]
    chat = _header(scope, chat_ids) + """let boundedNumber=(v:dynamic) { iff(gettype(v) in ('long','int','real','double') and todouble(v)>=0 and todouble(v)<=9007199254740991.0 and todouble(v)==tolong(v),tolong(v),long(null)) };
ApiManagementGatewayLlmLog
| where _ResourceId =~ apimResourceId and TimeGenerated between (startTime .. endTime)
| extend requestId=canonicalId(tostring(CorrelationId))
| where requestId in (selectedIds)
| extend model=tostring(DeploymentName),provider=tostring(RequestId)
| project resourceId=_ResourceId,TimeGenerated,requestId,targetModel=iff(model matches regex @'^[A-Za-z0-9_.-]{1,160}$',model,''),
    providerRequestId=iff(provider matches regex @'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$',provider,''),
    promptTokens=boundedNumber(pack_array(PromptTokens)[0]),completionTokens=boundedNumber(pack_array(CompletionTokens)[0]),totalTokens=boundedNumber(pack_array(TotalTokens)[0])
"""
    return {"aggregate": _aggregate(scope, manifest, edges, aggregate_source), "access": _finish(scope, access),
            "waf": _finish(scope, waf), "chatUsage": _finish(scope, chat)}

def build_child_lookup(scope, first_report, parent_request_id, rest_api_id, rest_operation_id):
    """Only explicit response/header or validated PII-result links authorize a lookup."""
    parent = canonical_id(parent_request_id)
    first_report = _validate_report(first_report)
    if not _name(rest_api_id) or not _name(rest_operation_id):
        _fail("INPUT_INVALID_EXPECTATION")
    links = first_report.get("childReferences", {}).get(parent, ())
    ids = [link["requestId"] for link in links if link.get("source") in ("backend-header", "pii-result")]
    if not ids:
        _fail("CHILD_LINK_UNAVAILABLE")
    native = _header(scope, ids) + _NATIVE
    native += "| where logicalApiId(tostring(ApiId)) == " + _literal(rest_api_id) + " and tostring(OperationId) == " + _literal(rest_operation_id) + "\n"
    return _finish(scope, native + "| project resourceId=_ResourceId,nativeLogAt=TimeGenerated,requestId,apiId=tostring(ApiId),operationId=tostring(OperationId),responseCode=toint(ResponseCode),backendResponseCode=toint(BackendResponseCode)")

def _decision(reasons):
    if any(kind not in ("pass", "pending", "inconsistent") or not isinstance(code, str) or code not in REASON_CODES for kind, code in reasons):
        return {"status": "inconsistent", "reasons": ("INPUT_INVALID_REPORT",)}
    codes = tuple(sorted({code for _, code in reasons}))
    state = "inconsistent" if any(kind == "inconsistent" for kind, _ in reasons) else "pending" if reasons else "pass"
    return {"status": state, "reasons": codes}

def _report(requests, reasons=(), edges=None, children=None):
    all_reasons = list(reasons)
    for item in requests.values():
        all_reasons.extend((item["status"], code) for code in item["reasons"])
    return {**_decision(all_reasons), "requests": requests, "edgeIds": edges or {}, "childReferences": children or {}}


def _validate_report(report, manifest=None):
    """Normalize fixed producer statuses/codes; never echo arbitrary report text."""
    if not isinstance(report, dict) or set(report) != {"status", "reasons", "requests", "edgeIds", "childReferences"}:
        _fail("INPUT_INVALID_REPORT")
    def decision(item):
        if not isinstance(item, dict) or set(item) != {"status", "reasons"}:
            _fail("INPUT_INVALID_REPORT")
        status, reasons = item["status"], item["reasons"]
        if status not in ("pass", "pending", "inconsistent") or not isinstance(reasons, (list, tuple)) or len(reasons) > len(REASON_CODES):
            _fail("INPUT_INVALID_REPORT")
        if any(not isinstance(code, str) or code not in REASON_CODES for code in reasons) or status == "pass" and reasons:
            _fail("INPUT_INVALID_REPORT")
        if status != "pass" and not reasons:
            reasons = ("UPSTREAM_PENDING" if status == "pending" else "UPSTREAM_INCONSISTENT",)
        return {"status": status, "reasons": tuple(sorted(set(reasons)))}
    global_decision = decision({"status": report["status"], "reasons": report["reasons"]})
    raw_requests = report["requests"]
    if not isinstance(raw_requests, dict) or len(raw_requests) > MAX_IDS:
        _fail("INPUT_INVALID_REPORT")
    requests = {}
    for raw_id, item in raw_requests.items():
        try:
            rid = canonical_id(raw_id)
        except AuditInputError:
            _fail("INPUT_INVALID_REPORT")
        if rid in requests or manifest is not None and rid not in manifest:
            _fail("INPUT_INVALID_REPORT")
        requests[rid] = decision(item)
    fields = {}
    for field in ("edgeIds", "childReferences"):
        raw = report[field]
        if not isinstance(raw, dict) or len(raw) > MAX_IDS:
            _fail("INPUT_INVALID_REPORT")
        values = {}
        for raw_parent, items in raw.items():
            try:
                parent = canonical_id(raw_parent)
            except AuditInputError:
                _fail("INPUT_INVALID_REPORT")
            if parent in values or parent not in requests or not isinstance(items, (list, tuple)) or len(items) > MAX_IDS:
                _fail("INPUT_INVALID_REPORT")
            normalized = []
            for item in items:
                try:
                    if field == "edgeIds":
                        value = canonical_id(item)
                    else:
                        if not isinstance(item, dict) or set(item) != {"requestId", "source"} or item["source"] not in ("backend-header", "pii-result"):
                            _fail("INPUT_INVALID_REPORT")
                        child = canonical_id(item["requestId"])
                        if child == parent:
                            _fail("INPUT_INVALID_REPORT")
                        value = {"requestId": child, "source": item["source"]}
                except AuditInputError:
                    _fail("INPUT_INVALID_REPORT")
                normalized.append(value)
            values[parent] = tuple(normalized)
        fields[field] = values
    request_codes = {code for item in requests.values() for code in item["reasons"]}
    upstream = tuple((global_decision["status"], code) for code in global_decision["reasons"] if code not in request_codes)
    normalized = _report(requests, upstream, fields["edgeIds"], fields["childReferences"])
    normalized["_upstream"] = upstream
    return normalized

def _rejected(e):
    return e["authRejected"] or e["expectedOutcome"] == "denied"

def _rejection(e):
    return ("INVALID_AUTHENTICATION", 401) if e["authRejected"] else ("AUTHORIZATION_DENIED", 403)

def _outcome_issues(e, event, phase, code, status):
    if event == "query-guard":
        if phase == "unknown" or phase is None:
            return [("pending", "QUERY_OBSERVATION_UNKNOWN")]
        if phase not in ("received", "backend-response", "policy-error"):
            return [("inconsistent", "QUERY_OBSERVATION_INVALID")]
        if phase == "policy-error":
            return [("inconsistent", "TRACE_OUTCOME_CONFLICT")]
        if _rejected(e) and phase == "backend-response":
            return [("inconsistent", "REJECTION_ALLOWED_TRACE")]
        return []
    if event != "gateway-request":
        return [("inconsistent", "AGGREGATE_RESULTS_INVALID")]
    if phase is None or phase == "unknown":
        return [("pending", "TRACE_OUTCOME_UNKNOWN")]
    if phase not in ("accepted", "rejected", "backend-response", "policy-error"):
        return [("inconsistent", "TRACE_PHASE_CODE_INVALID")]
    if status is None:
        status_issues = [("pending", "TRACE_STATUS_MISSING")]
    elif not _integer(status) or not 0 <= status <= 599:
        status_issues = [("inconsistent", "TRACE_STATUS_INVALID")]
    else:
        status_issues = []
    if _rejected(e):
        if phase in ("accepted", "backend-response"):
            return status_issues + [("inconsistent", "REJECTION_ALLOWED_TRACE")]
        wanted_code, wanted_status = _rejection(e)
        if phase == "policy-error" or phase == "rejected" and (code not in (None, wanted_code) or status is not None and status != wanted_status):
            return status_issues + [("inconsistent", "TRACE_OUTCOME_CONFLICT")]
        if code is None:
            status_issues.append(("pending", "TRACE_INCOMPLETE"))
        return status_issues
    if phase in ("rejected", "policy-error"):
        return status_issues + [("inconsistent", "TRACE_OUTCOME_CONFLICT")]
    wanted_codes = ("GATEWAY_ALLOWED",) if phase == "accepted" else ("BACKEND_RESPONSE", "PII_REDACTED")
    if code is None:
        status_issues.append(("pending", "TRACE_INCOMPLETE"))
    elif code not in wanted_codes:
        status_issues.append(("inconsistent", "TRACE_OUTCOME_CONFLICT"))
    if _integer(status) and (phase == "backend-response" and status != e["status"] or phase == "accepted" and status >= 400):
        status_issues.append(("inconsistent", "TRACE_OUTCOME_CONFLICT"))
    return status_issues

def _query_issues(row, e):
    reasons = _outcome_issues(e, "query-guard", row.get("phase"), None, None)
    route = row.get("queryRoute")
    if not route or route in ("none", "unknown"):
        reasons.append(("pending", "QUERY_OBSERVATION_UNKNOWN"))
    elif not isinstance(route, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", route) is None:
        reasons.append(("inconsistent", "QUERY_OBSERVATION_INVALID"))
    for field, allowed in (("queryPresence", ("none", "present")), ("queryDuplicates", ("yes", "no"))):
        value = row.get(field)
        if value is None or value == "unknown":
            reasons.append(("pending", "QUERY_OBSERVATION_UNKNOWN"))
        elif value not in allowed:
            reasons.append(("inconsistent", "QUERY_OBSERVATION_INVALID"))
    method = row.get("queryHttpMethod")
    if method is None or method == "unknown":
        reasons.append(("pending", "QUERY_OBSERVATION_UNKNOWN"))
    elif method != e["httpMethod"]:
        reasons.append(("inconsistent", "QUERY_METHOD_MISMATCH"))
    status_class = row.get("queryStatusClass")
    wanted = str(e["status"] // 100) + "xx" if 200 <= e["status"] <= 599 else "unknown"
    if row.get("phase") == "received":
        if status_class not in (None, "unknown"):
            reasons.append(("inconsistent", "QUERY_STATUS_CONFLICT"))
        if status_class is None:
            reasons.append(("pending", "QUERY_OBSERVATION_UNKNOWN"))
    elif row.get("phase") == "backend-response":
        if status_class is None or status_class == "unknown" and wanted != "unknown":
            reasons.append(("pending", "QUERY_OBSERVATION_UNKNOWN"))
        elif status_class != wanted:
            reasons.append(("inconsistent", "QUERY_STATUS_CONFLICT"))
    return reasons

def _aggregate_outcomes(row, e):
    results = row.get("customResults")
    if not isinstance(results, (list, tuple)) or not results:
        return [("pending", "AGGREGATE_RESULTS_MISSING")]
    reasons = []
    if len(results) >= 64:
        reasons.append(("pending", "AGGREGATE_RESULTS_TRUNCATED"))
    accepted, completed, rejected = False, False, False
    for outcome in results[:64]:
        if not isinstance(outcome, dict) or set(outcome) != {"event", "phase", "code", "status"}:
            reasons.append(("inconsistent", "AGGREGATE_RESULTS_INVALID"))
            continue
        event, phase, code, status = (outcome[key] for key in ("event", "phase", "code", "status"))
        reasons += _outcome_issues(e, event, phase, code, status)
        if event == "gateway-request":
            accepted |= phase == "accepted" and code == "GATEWAY_ALLOWED"
            completed |= phase == "backend-response" and code in ("BACKEND_RESPONSE", "PII_REDACTED") and status == e["status"]
            rejected |= phase == "rejected" and (code, status) == _rejection(e)
    if _rejected(e) and not rejected:
        reasons.append(("pending", "REJECTION_TRACE_MISSING"))
    elif not _rejected(e) and (not accepted or not completed and not _initialized_notification_completed(e)):
        reasons.append(("pending", "COMPLETION_TRACE_MISSING"))
    return reasons


def _inputs(expectations):
    try:
        return _manifest(expectations), None
    except AuditInputError as error:
        return {}, _report({}, (("inconsistent", str(error)),))

def _checked_rows(batch, scope):
    if not isinstance(batch, QueryRows) or not isinstance(batch.rows, (list, tuple)) or type(batch.complete) is not bool:
        return (), [("pending", "RESULT_INCOMPLETE")]
    reasons = []
    if not batch.complete or len(batch.rows) > scope.row_cap:
        reasons.append(("pending", "RESULT_INCOMPLETE"))
    if any(not isinstance(row, dict) for row in batch.rows):
        return (), reasons + [("inconsistent", "RESULT_INVALID_ROW")]
    return tuple(batch.rows[:scope.row_cap + 1]), reasons

def _logical(value):
    return value.split(";", 1)[0] if isinstance(value, str) else ""

def _location(row, scope, resource, time_field):
    if "resourceId" not in row or time_field not in row:
        return [("pending", "NATIVE_METADATA_MISSING")]
    if not isinstance(row["resourceId"], str) or row["resourceId"].casefold() != resource.casefold():
        return [("inconsistent", "ROW_SCOPE_MISMATCH")]
    try:
        valid_time = scope.start <= _utc(row[time_field]) <= scope.end
    except AuditInputError:
        valid_time = False
    return [] if valid_time else [("inconsistent", "ROW_SCOPE_MISMATCH")]

def _subject(row, aggregate=False):
    names = (("authenticatedSubjectStatus", "subjectKind", "method", "subscriptionId", "callerId", "allocationId", "tenantId", "principalId", "clientId")
             if aggregate else ("subjectStatus", "subjectKind", "subjectMethod", "subjectSubscriptionId", "subjectCallerId", "subjectAllocationId", "subjectTenantId", "subjectPrincipalId", "subjectClientId"))
    return dict(zip(("status", "kind", "method", "subscription", "caller", "allocation", "tenant", "principal", "client"), (row.get(name) for name in names)))

def _match_subject(actual, e):
    if actual["status"] == "inconsistent":
        return [("inconsistent", "SUBJECT_INCONSISTENT")]
    if actual["status"] != "verified":
        return [("pending", "SUBJECT_UNVERIFIED")]
    if e["mode"] == "key":
        good = actual["kind"] == "apimSubscription" and actual["method"] == "subscriptionKey" and actual["subscription"] == e["subscriptionId"] and actual["caller"] == "subscription:" + e["subscriptionId"]
        good = good and all(not actual[name] for name in ("tenant", "principal", "client", "allocation"))
    else:
        caller = "user:" + e["tenantId"] + ":" + e["principalId"]
        good = actual["kind"] == "entraUser" and actual["method"] == "user" and not actual["subscription"]
        good = good and _id(actual["tenant"]) == e["tenantId"] and _id(actual["principal"]) == e["principalId"] and actual["caller"] == caller and actual["allocation"] == caller
        good = good and (e.get("clientId") is None or _id(actual["client"]) == e["clientId"])
    return [] if good else [("inconsistent", "SUBJECT_MISMATCH")]

def _allowed_identity_issues(row, e, subject):
    """API authorization may remain allowed even when its CI target is denied."""
    method = "subscriptionKey" if e["mode"] == "key" else "user"
    valid = row.get("method") == method and row.get("callerId") == subject["caller"]
    if e["mode"] == "key":
        valid = valid and row.get("subscriptionId") == subject["subscription"] == e["subscriptionId"]
        valid = valid and not any(row.get(key) for key in ("verifiedTenantId", "verifiedPrincipalId", "verifiedClientId", "allocationId"))
    else:
        valid = valid and not row.get("subscriptionId") and row.get("allocationId") == subject["allocation"]
        valid = valid and _id(row.get("verifiedTenantId")) == _id(subject["tenant"]) == e["tenantId"]
        valid = valid and _id(row.get("verifiedPrincipalId")) == _id(subject["principal"]) == e["principalId"]
        flat_client, subject_client = row.get("verifiedClientId"), subject["client"]
        valid = valid and (not flat_client and not subject_client or _id(flat_client) != "" and _id(flat_client) == _id(subject_client))
    return [] if valid else [("inconsistent", "AUTHORIZATION_MISMATCH")]


def evaluate_first_phase(scope, expectations, native, traces):
    manifest, invalid = _inputs(expectations)
    if invalid:
        return invalid
    native_rows, issues = _checked_rows(native, scope)
    trace_rows, trace_issues = _checked_rows(traces, scope)
    issues += trace_issues
    if any(_id(row.get("requestId")) not in manifest for row in native_rows + tuple(trace_rows)):
        issues.append(("inconsistent", "UNEXPECTED_REQUEST_ID"))
    decisions, edges, children = {}, {}, {}
    for rid, e in manifest.items():
        reasons = list(issues)
        rows = [row for row in native_rows if _id(row.get("requestId")) == rid]
        events = [row for row in trace_rows if _id(row.get("requestId")) == rid]
        candidates = set()
        if not rows:
            reasons.append(("pending", "NATIVE_ROW_MISSING"))
        variants = set()
        for row in rows:
            reasons += _location(row, scope, scope.apim_id, "nativeLogAt")
            required = ("apiId", "operationId", "httpMethod", "responseCode", "backendResponseCode", "traceCount")
            if any(key not in row for key in required):
                reasons.append(("pending", "NATIVE_METADATA_MISSING"))
            known = all(row.get(key) is not None for key in ("apiId", "operationId", "httpMethod", "responseCode")) and "backendResponseCode" in row
            if known:
                variants.add((_logical(row.get("apiId")), row.get("operationId"), row.get("httpMethod"), row.get("responseCode"), row.get("backendResponseCode")))
            else:
                reasons.append(("pending", "NATIVE_METADATA_MISSING"))
            if row.get("apiId") is not None and _logical(row["apiId"]) != e["apiId"] or e.get("operationId") is not None and row.get("operationId") is not None and row["operationId"] != e["operationId"]:
                reasons.append(("inconsistent", "API_OPERATION_MISMATCH"))
            if row.get("httpMethod") is not None and row["httpMethod"] != e["httpMethod"] or row.get("responseCode") is not None and row["responseCode"] != e["status"]:
                reasons.append(("inconsistent", "HTTP_STATUS_MISMATCH"))
            if not _integer(row.get("traceCount")) or row["traceCount"] > TRACE_LIMIT:
                reasons.append(("pending", "TRACE_INCOMPLETE"))
            backend = row.get("backendResponseCode")
            if _rejected(e):
                if "backendResponseCode" in row and backend is None:
                    reasons.append(("pending", "REJECTION_BACKEND_UNKNOWN"))
                elif "backendResponseCode" in row and backend != 0:
                    reasons.append(("inconsistent", "REJECTION_BACKEND_OBSERVED"))
            elif backend in (None, 0):
                if e["transport"] != "mcp" or e.get("expectedBackendStatus") is not None:
                    reasons.append(("pending", "BACKEND_STATUS_MISSING"))
            elif not _integer(backend) or not 100 <= backend < 400 or e.get("expectedBackendStatus") is not None and backend != e["expectedBackendStatus"]:
                reasons.append(("inconsistent", "BACKEND_STATUS_MISMATCH"))
            if _id(row.get("headerTraceId")):
                candidates.add(_id(row["headerTraceId"]))
        if len(variants) > 1:
            reasons.append(("inconsistent", "NATIVE_VARIANTS_CONFLICT"))
        if not events:
            reasons.append(("pending", "TRACE_MISSING"))
        accepted, completed, rejected, verified, pii_success = False, False, False, set(), False
        native_operation = rows[0].get("operationId") if rows and rows[0].get("operationId") is not None else e.get("operationId")
        refs = []
        for row in events:
            reasons += _location(row, scope, scope.apim_id, "nativeLogAt")
            kind, phase = row.get("traceType"), row.get("phase")
            operation_for_trace = native_operation if native_operation is not None else row.get("traceOperationId")
            if not row.get("traceRequestId"):
                reasons.append(("pending", "TRACE_REQUEST_MISSING"))
            elif _id(row["traceRequestId"]) != rid:
                reasons.append(("inconsistent", "TRACE_REQUEST_MISMATCH"))
            if kind == "core-pii":
                if row.get("traceStatus") == 200 and row.get("piiDocumentCount") == 1 and row.get("piiUpstreamAttempt") == 1 and _integer(row.get("piiDetectedCount")) and row["piiDetectedCount"] >= 0:
                    pii_success = True
                if e.get("expectedPiiModelVersion") and row.get("piiModelVersion") != e["expectedPiiModelVersion"]:
                    reasons.append(("inconsistent", "MODEL_MISMATCH"))
                continue
            if row.get("schemaVersion") is None or row.get("auditSchemaVersion") is None:
                reasons.append(("pending", "TRACE_SCHEMA_MISSING"))
            elif kind not in ("gateway-request", "query-guard") or row["schemaVersion"] != (1 if kind == "gateway-request" else 2) or row["auditSchemaVersion"] != 2:
                reasons.append(("inconsistent", "TRACE_SCHEMA_INVALID"))
            if kind == "gateway-request":
                if phase is None or row.get("code") is None:
                    reasons.append(("pending", "TRACE_INCOMPLETE"))
                elif phase not in ("accepted", "rejected", "backend-response", "policy-error", "unknown") or not isinstance(row["code"], str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["code"]) is None:
                    reasons.append(("inconsistent", "TRACE_PHASE_CODE_INVALID"))
                if row.get("traceStatus") is None:
                    reasons.append(("pending", "TRACE_STATUS_MISSING"))
                elif not _integer(row["traceStatus"]) or not 0 <= row["traceStatus"] <= 599:
                    reasons.append(("inconsistent", "TRACE_STATUS_INVALID"))
            if not row.get("traceApiId") or not row.get("traceOperationId"):
                reasons.append(("pending", "TRACE_METADATA_MISSING"))
            elif _logical(row["traceApiId"]) != e["apiId"] or row["traceOperationId"] != operation_for_trace:
                reasons.append(("inconsistent", "API_OPERATION_MISMATCH"))
            context_fields = ("contextRequestId", "contextApiId", "contextOperationId", "contextHttpMethod", "contextReceivedAt")
            if any(row.get(key) is None or row.get(key) == "" for key in context_fields):
                reasons.append(("pending", "CONTEXT_MISSING"))
            elif _id(row["contextRequestId"]) != rid or _logical(row["contextApiId"]) != e["apiId"] or row["contextOperationId"] != operation_for_trace or row["contextHttpMethod"] != e["httpMethod"]:
                reasons.append(("inconsistent", "CONTEXT_MISMATCH"))
            else:
                try:
                    in_window = scope.start <= _utc(row["contextReceivedAt"]) <= scope.end
                except AuditInputError:
                    in_window = False
                if not in_window:
                    reasons.append(("inconsistent", "CONTEXT_MISMATCH"))
            if _id(row.get("contextTraceId")):
                candidates.add(_id(row["contextTraceId"]))
            subject = _subject(row)
            if subject["status"] == "verified":
                verified.add(json.dumps(subject, sort_keys=True, default=str))
                reasons += _match_subject(subject, e)
            elif subject["status"] == "inconsistent":
                reasons.append(("inconsistent", "SUBJECT_INCONSISTENT"))
            if e["authRejected"] and subject["status"] == "verified":
                reasons.append(("inconsistent", "SUBJECT_UNEXPECTED_ON_REJECTION"))
            if kind == "query-guard":
                reasons += _query_issues(row, e)
                continue
            if kind != "gateway-request":
                continue
            reasons += _outcome_issues(e, kind, phase, row.get("code"), row.get("traceStatus"))
            if _rejected(e):
                rejected |= phase == "rejected" and (row.get("code"), row.get("traceStatus")) == _rejection(e)
                if e["expectedOutcome"] == "denied" and phase == "rejected":
                    reasons += _match_subject(subject, e)
                    if row.get("authorization") in (None, "", "unconfirmed"):
                        reasons.append(("pending", "AUTHORIZATION_MISSING"))
                    elif row["authorization"] != "allowed":
                        reasons.append(("inconsistent", "AUTHORIZATION_MISMATCH"))
                    else:
                        reasons += _allowed_identity_issues(row, e, subject)
                if phase in ("accepted", "backend-response") or e["authRejected"] and row.get("authorization") == "allowed":
                    reasons.append(("inconsistent", "REJECTION_ALLOWED_TRACE"))
                continue
            if phase in ("accepted", "backend-response"):
                reasons += _match_subject(subject, e)
                if row.get("authorization") != "allowed":
                    reasons.append(("inconsistent", "AUTHORIZATION_MISMATCH"))
                reasons += _allowed_identity_issues(row, e, subject)
                if row.get("measurementRole") != e["measurementRole"]:
                    reasons.append(("inconsistent", "MEASUREMENT_ROLE_MISMATCH"))
                if e["requireChatUsage"] and row.get("usageSource") != "ApiManagementGatewayLlmLog":
                    reasons.append(("pending", "USAGE_SOURCE_MISSING"))
                if e.get("expectedModel"):
                    if not row.get("targetModel"):
                        reasons.append(("pending", "MODEL_MISSING"))
                    elif row["targetModel"] != e["expectedModel"]:
                        reasons.append(("inconsistent", "MODEL_MISMATCH"))
                if e.get("expectedAlias"):
                    if row.get("requestedModel") != e["expectedAlias"] or row.get("modelAliasApplied") is not True:
                        reasons.append(("inconsistent", "ALIAS_MISMATCH"))
                elif row.get("modelAliasApplied") is True:
                    reasons.append(("inconsistent", "ALIAS_MISMATCH"))
            accepted |= phase == "accepted" and row.get("code") == "GATEWAY_ALLOWED"
            completed |= phase == "backend-response" and row.get("code") in ("BACKEND_RESPONSE", "PII_REDACTED") and row.get("traceStatus") == e["status"]
            child = _id(row.get("backendApimRequestId"))
            if e["transport"] == "mcp" and phase == "backend-response" and child and child != rid:
                refs.append({"requestId": child, "source": "backend-header"})
        if len(verified) > 1:
            reasons.append(("inconsistent", "SUBJECT_VARIANTS_CONFLICT"))
        if _rejected(e) and not rejected:
            reasons.append(("pending", "REJECTION_TRACE_MISSING"))
        elif not _rejected(e) and (not accepted or not completed and not _initialized_notification_completed(e)):
            reasons.append(("pending", "COMPLETION_TRACE_MISSING"))
        if e.get("expectedPiiModelVersion") and not pii_success:
            reasons.append(("pending", "PII_SUCCESS_TRACE_MISSING"))
        if len(candidates) > 1:
            reasons.append(("inconsistent", "APPGW_EDGE_CONFLICT"))
        elif e["requireAppGateway"] and not candidates:
            reasons.append(("pending", "APPGW_EDGE_MISSING"))
        if e.get("piiResultRequestId") and e["piiResultRequestId"] != rid:
            refs.append({"requestId": e["piiResultRequestId"], "source": "pii-result"})
        edges[rid] = tuple(sorted(candidates))
        children[rid] = tuple(refs)
        decisions[rid] = _decision(reasons)
    return _report(decisions, edges=edges, children=children)

def evaluate_second_phase(scope, expectations, first_report, aggregate, access, waf, chat_usage):
    manifest, invalid = _inputs(expectations)
    if invalid:
        return invalid
    try:
        first_report = _validate_report(first_report, manifest)
    except AuditInputError:
        return _report({}, (("inconsistent", "INPUT_INVALID_REPORT"),))
    aggregates, agg_issues = _checked_rows(aggregate, scope)
    access_rows, access_issues = _checked_rows(access, scope)
    waf_rows, waf_issues = _checked_rows(waf, scope)
    usage_rows, usage_issues = _checked_rows(chat_usage, scope)
    decisions = {}
    edge_ids_all = {edge for values in first_report.get("edgeIds", {}).values() for edge in values}
    chat_ids = {rid for rid, item in manifest.items() if item["requireChatUsage"]}
    if any(_id(row.get("requestId")) not in manifest for row in aggregates) or any(_id(row.get("transactionId")) not in edge_ids_all for row in access_rows + waf_rows) or any(_id(row.get("requestId")) not in chat_ids for row in usage_rows):
        agg_issues += [("inconsistent", "RESULT_SCOPE_MISMATCH")]
    for rid, e in manifest.items():
        initial = first_report.get("requests", {}).get(rid)
        reasons = [(initial["status"], code) for code in initial["reasons"]] if initial else [("pending", "FIRST_PHASE_MISSING")]
        reasons += list(first_report["_upstream"]) + agg_issues
        rows = [row for row in aggregates if _id(row.get("requestId")) == rid]
        if not rows:
            reasons.append(("pending", "AGGREGATE_ROW_MISSING"))
        elif len({json.dumps(row, sort_keys=True, default=str) for row in rows}) > 1:
            reasons.append(("inconsistent", "AGGREGATE_VARIANTS_CONFLICT"))
        for row in rows:
            if "nativeLogAt" not in row:
                reasons.append(("pending", "AGGREGATE_METADATA_INCOMPLETE"))
            else:
                try:
                    valid_time = scope.start <= _utc(row["nativeLogAt"]) <= scope.end
                except AuditInputError:
                    valid_time = False
                if not valid_time:
                    reasons.append(("inconsistent", "ROW_SCOPE_MISMATCH"))
            backend = row.get("backendResponseCode")
            if "backendResponseCode" not in row:
                reasons.append(("pending", "AGGREGATE_METADATA_INCOMPLETE"))
            elif _rejected(e) and backend is None:
                reasons.append(("pending", "REJECTION_BACKEND_UNKNOWN"))
            elif _rejected(e) and backend != 0:
                reasons.append(("inconsistent", "REJECTION_BACKEND_OBSERVED"))
            elif not _rejected(e) and backend in (None, 0):
                if e["transport"] != "mcp" or e.get("expectedBackendStatus") is not None:
                    reasons.append(("pending", "BACKEND_STATUS_MISSING"))
            elif not _rejected(e) and (not _integer(backend) or not 100 <= backend < 400 or e.get("expectedBackendStatus") is not None and backend != e["expectedBackendStatus"]):
                reasons.append(("inconsistent", "BACKEND_STATUS_MISMATCH"))
            if row.get("nativeMetadataStatus") == "inconsistent" or row.get("requestContextStatus") == "inconsistent":
                reasons.append(("inconsistent", "AGGREGATE_INCONSISTENT"))
            elif row.get("nativeMetadataStatus") != "consistent" or row.get("requestContextStatus") != "available":
                reasons.append(("pending", "AGGREGATE_METADATA_INCOMPLETE"))
            counts = [row.get(key) for key in ("inconsistentSubjectCount", "malformedContextCount", "unlinkedV2ObservationCount")]
            if any(value is None for value in counts):
                reasons.append(("pending", "AGGREGATE_METADATA_INCOMPLETE"))
            elif any(not _integer(value) or value != 0 for value in counts):
                reasons.append(("inconsistent", "AGGREGATE_INCONSISTENT"))
            if any(row.get(key) is None for key in ("apiId", "operationId", "responseCode")):
                reasons.append(("pending", "AGGREGATE_METADATA_INCOMPLETE"))
            elif _logical(row["apiId"]) != e["apiId"] or e.get("operationId") is not None and row["operationId"] != e["operationId"] or row["responseCode"] != e["status"]:
                reasons.append(("inconsistent", "API_OPERATION_MISMATCH"))
            reasons += _aggregate_outcomes(row, e)
            if not e["authRejected"]:
                reasons += _match_subject(_subject(row, True), e)
            elif row.get("authenticatedSubjectStatus") == "verified":
                reasons.append(("inconsistent", "SUBJECT_UNEXPECTED_ON_REJECTION"))
        if e["requireAppGateway"]:
            reasons += access_issues + waf_issues
            edge_ids = first_report.get("edgeIds", {}).get(rid, ())
            if len(edge_ids) != 1:
                reasons.append(("pending", "APPGW_EDGE_MISSING"))
            else:
                transaction = edge_ids[0]
                observed = [row for row in access_rows if _id(row.get("transactionId")) == transaction]
                if not observed:
                    reasons.append(("pending", "ACCESS_ROW_MISSING"))
                variants = set()
                for row in observed:
                    reasons += _location(row, scope, scope.appgw_id, "TimeGenerated")
                    variants.add((row.get("httpMethod"), row.get("httpStatus"), row.get("serverStatus")))
                    if not row.get("httpMethod") or row.get("httpStatus") is None or row.get("serverStatus") in (None, 0):
                        reasons.append(("pending", "ACCESS_METADATA_MISSING"))
                    elif row["httpMethod"] != e["httpMethod"] or row["httpStatus"] != e["status"] or row["serverStatus"] != e["status"]:
                        reasons.append(("inconsistent", "ACCESS_STATUS_MISMATCH"))
                if len(variants) > 1:
                    reasons.append(("inconsistent", "ACCESS_VARIANTS_CONFLICT"))
                for row in rows:
                    if row.get("appGatewayCorrelationStatus") != "matched" or _id(row.get("appGatewayTraceId")) != transaction:
                        reasons.append(("pending", "APPGW_JOIN_INCOMPLETE"))
                for row in waf_rows:
                    if _id(row.get("transactionId")) != transaction:
                        continue
                    reasons += _location(row, scope, scope.appgw_id, "TimeGenerated")
                    if not isinstance(row.get("action"), str) or not _text(row["action"], 64) or not _text(row.get("ruleId"), 160):
                        reasons.append(("pending", "WAF_FIELDS_MISSING"))
                    elif row["action"].casefold() in ("block", "blocked"):
                        reasons.append(("inconsistent", "WAF_BLOCK_OBSERVED"))
            # Firewall logs are match events. No WAF row is not evidence of disabled inspection.
        if e["requireChatUsage"]:
            reasons += usage_issues
            matching = [row for row in usage_rows if _id(row.get("requestId")) == rid]
            variants = set()
            for row in matching:
                reasons += _location(row, scope, scope.apim_id, "TimeGenerated")
                if not row.get("targetModel"):
                    reasons.append(("pending", "MODEL_MISSING"))
                elif row["targetModel"] != e["expectedModel"]:
                    reasons.append(("inconsistent", "MODEL_MISMATCH"))
                numbers = [row.get(key) for key in ("promptTokens", "completionTokens", "totalTokens")]
                good = all(_integer(n) and 0 <= n <= 9007199254740991 for n in numbers) and numbers[2] > 0 and numbers[0] + numbers[1] == numbers[2]
                provider = row.get("providerRequestId")
                good = good and isinstance(provider, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", provider) is not None
                if good:
                    variants.add((row["targetModel"], provider, *numbers))
                else:
                    reasons.append(("pending", "USAGE_INVALID"))
            if not matching:
                reasons.append(("pending", "USAGE_MISSING"))
            elif len(variants) > 1:
                reasons.append(("inconsistent", "USAGE_VARIANTS_CONFLICT"))
            elif not variants:
                reasons.append(("pending", "USAGE_INVALID"))
        decisions[rid] = _decision(reasons)
    return _report(decisions, edges=first_report.get("edgeIds"), children=first_report.get("childReferences"))

def evaluate_child_reference(parent_expectation, child_expectation, first_report, child_report):
    """A body/header observation is only a link; the child still needs its own audit."""
    manifest, invalid = _inputs([parent_expectation, child_expectation])
    if invalid:
        return invalid
    parent, child = list(manifest.values())
    try:
        first_report = _validate_report(first_report)
        child_report = _validate_report(child_report)
    except AuditInputError:
        return _decision((("inconsistent", "INPUT_INVALID_REPORT"),))
    refs = first_report.get("childReferences", {}).get(parent["requestId"], ())
    if child["requestId"] not in {ref["requestId"] for ref in refs if ref.get("source") in ("backend-header", "pii-result")}:
        return _decision((("pending", "CHILD_LINK_UNAVAILABLE"),))
    if parent.get("restApiId") is None or parent.get("restOperationId") is None:
        return _decision((("pending", "CHILD_EXPECTATION_MISSING"),))
    if child["apiId"] != parent["restApiId"] or child["operationId"] != parent["restOperationId"]:
        return _decision((("inconsistent", "CHILD_API_OPERATION_MISMATCH"),))
    subject_fields = ("mode", "subscriptionId") if parent["mode"] == "key" else ("mode", "tenantId", "principalId", "clientId")
    if any(parent.get(key) != child.get(key) for key in subject_fields):
        return _decision((("inconsistent", "CHILD_SUBJECT_MISMATCH"),))
    decision = child_report.get("requests", {}).get(child["requestId"])
    if decision is None:
        return _decision((("pending", "CHILD_AUDIT_MISSING"),))
    parent_decision = first_report.get("requests", {}).get(parent["requestId"])
    if parent_decision is None:
        return _decision((("pending", "PARENT_AUDIT_MISSING"),))
    return _decision([(item["status"], code) for item in (parent_decision, decision) for code in item["reasons"]] + list(first_report["_upstream"]) + list(child_report["_upstream"]))
