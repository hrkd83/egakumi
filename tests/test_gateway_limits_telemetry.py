"""APIMの入力上限・生成token上限・安全な利用量記録を検証する。

policyからC#式を抽出してローカル実行する。操作別の制限と帰属を確認するもので、実APIMへの適用やログ到達の証明ではない。
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.apim_audit import expand_audit_fragments, render_audit_policy
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from policy_fixture_paths import policy_fixture_path

POLICIES = CORE / "apps/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.exists() else None


def expression(path, xpath, attribute=None):
    """XML内の指定位置からAPIMの実C#式を取り出す。想定外の形式なら試験を止める。"""
    rendered = expand_audit_fragments(expand_trusted_ingress_fragment(render_audit_policy(path.read_text(encoding="utf-8"))))
    element = ET.fromstring(rendered).find(xpath)
    if element is None:
        raise AssertionError("Expected the generated APIM expression at its original execution position")
    value = element.get(attribute) if attribute else element.text
    if not value or not value.strip().startswith("@{") or not value.strip().endswith("}"):
        raise AssertionError("Expected an APIM multi-statement expression")
    return value.strip()[2:-1]


LIMIT_EXPRESSION = expression(policy_fixture_path(POLICIES, "core-model-limits.xml"), "./set-variable", "value")
TELEMETRY_EXPRESSION = expression(policy_fixture_path(POLICIES, "core-gateway-telemetry.xml"), "./trace/message")
REQUEST_CONTEXT_EXPRESSION = expression(POLICIES / "service.xml", "./inbound/set-variable[@name='gatewayRequestContext']", "value")
SUBJECT_EXPRESSION = expression(POLICIES / "service.xml", "./inbound/set-variable[@name='gatewayAuthenticatedSubject']", "value")

# 本番XMLから例外時のstatus式を抽出します。C#で実行し、既定200を成功と誤記しないことを確認します。
ERROR_STATUS_EXPRESSIONS = {
    name: ET.parse(policy_fixture_path(POLICIES, name)).find("./on-error/set-variable[@name='gatewayAuditStatus']").get("value")
    for name in ("rest.xml", "stream.xml", "generated.xml")
}
ERROR_STATUS_EXPRESSION = ERROR_STATUS_EXPRESSIONS["rest.xml"]
if not ERROR_STATUS_EXPRESSION.startswith("@(") or not ERROR_STATUS_EXPRESSION.endswith(")"):
    raise AssertionError("Expected a single-statement APIM error status expression")


def single_expression(value):
    """呼出元XMLの単一式を取り出し、判定を試験側へ書き写さずに実行する。"""
    if not value or not value.startswith("@(") or not value.endswith(")"):
        raise AssertionError("Expected a single-statement APIM expression")
    return value[2:-1]


LIMIT_FAILURE_POLICY = next(
    node for node in ET.parse(policy_fixture_path(POLICIES, "rest.xml")).findall("./inbound/choose/when")
    if "gatewayLimitDecision" in node.get("condition", "")
)
LIMIT_AUDIT_NODES = [node for node in LIMIT_FAILURE_POLICY if node.tag == "set-variable"]
LIMIT_AUDIT_ASSIGNMENTS = "\n".join(
    f'context.Variables[{json.dumps(node.get("name"))}] = {single_expression(node.get("value"))};'
    for node in LIMIT_AUDIT_NODES
)
LIMIT_RESPONSE = LIMIT_FAILURE_POLICY.find("./return-response")
PII_LIMIT_BRANCH = LIMIT_FAILURE_POLICY.find("./choose[@id='core-pii-public-limit-error']/when")
PII_LIMIT_RESPONSE = PII_LIMIT_BRANCH.find("./return-response")
PII_LIMIT_BODY = PII_LIMIT_RESPONSE.find("./set-body").text.strip()
if not PII_LIMIT_BODY.startswith("@{") or not PII_LIMIT_BODY.endswith("}"):
    raise AssertionError("Expected a multi-statement APIM PII response body expression")

CSHARP = r"""
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

public sealed class Body {
  public byte[] Data;
  public int Reads;
  public int ByteReads;
  public bool ThrowOnByteRead;
  public bool AllReadsPreserved = true;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    AllReadsPreserved &= preserveContent;
    object value;
    if (typeof(T) == typeof(byte[])) {
      ByteReads++;
      if (ThrowOnByteRead) throw new Exception("Synthetic body reader failure details");
      value = Data;
    }
    else if (typeof(T) == typeof(JObject)) value = JObject.Parse(Encoding.UTF8.GetString(Data));
    else throw new Exception("Fixture does not support this message conversion");
    return (T)value;
  }
}
public sealed class Request {
  public Body Body; public string Method = "POST"; public string IpAddress = "192.0.2.10";
  public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>();
}
public sealed class Response {
  public int StatusCode;
  public Body Body { get { throw new Exception("Response body must remain untouched"); } }
}
public sealed class NamedContext { public string Id; public string UrlTemplate = "/responses"; }
public sealed class Jwt { public Dictionary<string, string[]> Claims = new Dictionary<string, string[]>(); }
public static class ClaimExtensions {
  // APIMの型付き変数取得を再現し、欠落と型不一致の挙動を区別します。
  public static T GetValueOrDefault<T>(this Dictionary<string,object> values,string key,T fallback) {
    object value; return values.TryGetValue(key,out value) ? (T)value : fallback;
  }
  public static string GetValueOrDefault(this Dictionary<string, string[]> claims, string key, string fallback) {
    return claims.ContainsKey(key) ? String.Join(" ", claims[key]) : fallback;
  }
}
public sealed class Context {
  public Dictionary<string, object> Variables = new Dictionary<string, object>();
  public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  public DateTime Timestamp = DateTime.Parse("2026-01-02T03:04:05Z", null, System.Globalization.DateTimeStyles.RoundtripKind);
  public Request Request;
  public Response Response;
  public NamedContext Api = new NamedContext { Id = "models" };
  public NamedContext Operation = new NamedContext { Id = "responses-create" };
  public NamedContext Subscription = new NamedContext { Id = "subscription-a" };
}
public static class PolicyFixture {
  public static string Limits(Context context) { __LIMITS__ }
  public static string Telemetry(Context context) { __TELEMETRY__ }
  public static string RequestContext(Context context) { __REQUEST_CONTEXT__ }
  public static string Subject(Context context) { __SUBJECT__ }
  public static int ErrorStatus(Context context) { return __ERROR_STATUS__; }
  public static string PiiLimitBody(Context context) { __PII_LIMIT_BODY__ }
  public static string LimitFailure(Context context) {
    // 実XMLの順序で監査変数を設定し、実Telemetry式と応答式を対照します。APIM応答engineは再現しません。
    __LIMIT_AUDIT_ASSIGNMENTS__
    var audit = JObject.Parse(Telemetry(context));
    var pii = __PII_LIMIT_CONDITION__;
    var response = new JObject(
      new JProperty("status", pii ? __PII_LIMIT_STATUS__ : __LIMIT_RESPONSE_STATUS__),
      new JProperty("reason", pii ? __PII_LIMIT_REASON__ : __LIMIT_RESPONSE_REASON__),
      new JProperty("requestIdHeader", pii ? __PII_LIMIT_REQUEST_ID__ : __LIMIT_RESPONSE_REQUEST_ID__),
      new JProperty("body", JObject.Parse(pii ? PiiLimitBody(context) : __LIMIT_RESPONSE_BODY__)));
    return new JObject(new JProperty("audit", audit),
      new JProperty("response", response)).ToString(Formatting.None);
  }
  public static string Run(string casesJson) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(casesJson)) {
      var context = new Context {
        Request = new Request { Body = new Body { Data = Encoding.UTF8.GetBytes((string)item["body"] ?? ""), ThrowOnByteRead = (bool?)item["throwOnByteRead"] ?? false } },
        Response = new Response { StatusCode = (int?)item["responseStatus"] ?? 200 }
      };
      if ((bool?)item["responseNull"] == true) context.Response = null;
      if ((bool?)item["nullSubscription"] == true) context.Subscription = null;
      if (item["api"] != null) context.Api.Id = (string)item["api"];
      if (item["operation"] != null) context.Operation.Id = (string)item["operation"];
      if ((bool?)item["nullApi"] == true) context.Api = null;
      if ((bool?)item["nullOperation"] == true) context.Operation = null;
      if (item["headers"] != null) foreach (var entry in ((JObject)item["headers"]).Properties()) context.Request.Headers[entry.Name] = new string[] { (string)entry.Value };
      if ((bool?)item["requestContextMissing"] != true) context.Variables["gatewayRequestContext"] = RequestContext(context);
      if (item["requestContextRaw"] != null) context.Variables["gatewayRequestContext"] = (string)item["requestContextRaw"];
      foreach (var field in new [] { "commonValidated", "entraValidated" }) {
        if (item[field] != null) context.Variables[field == "commonValidated" ? "gatewayCommonAuthenticationValidated" : "gatewayEntraAuthenticationValidated"] = ((JValue)item[field]).Value;
      }
      if (item["subscriptionSnapshot"] != null) context.Variables["gatewaySubscriptionId"] = (string)item["subscriptionSnapshot"];
      if (item["configuredTenant"] != null) context.Variables["gatewayEntraTenantId"] = (string)item["configuredTenant"];
      if (item["userAuthentication"] != null) context.Variables["gatewayUserAuthentication"] = item["userAuthentication"].Type == JTokenType.String ? (string)item["userAuthentication"] : item["userAuthentication"].ToString(Formatting.None);
      context.Variables["gatewayOperationConstraint"] = (item["constraint"] ?? new JObject()).ToString(Formatting.None);
      // 内部設定の読取り不備は、要求本文の不備と分けて実式へ渡します。
      if (item.Property("constraintRaw") != null) context.Variables["gatewayOperationConstraint"] = ((JValue)item["constraintRaw"]).Value;
      if ((bool?)item["constraintMissing"] == true) context.Variables.Remove("gatewayOperationConstraint");
      foreach (var field in new string[] { "gatewayAuditPhase", "gatewayAuditCode" }) {
        if (item[field] != null) context.Variables[field] = (string)item[field];
      }
      if (item["decision"] != null) context.Variables["gatewayDecision"] = item["decision"].Type == JTokenType.String ? (string)item["decision"] : item["decision"].ToString(Formatting.None);
      if (item["settings"] != null) {
        var audit=(JObject)item["settings"].DeepClone();
        var controls=new JObject();
        foreach(var field in new [] {"operationId","operationConstraints","tokenLimitOperationIds"}) {
          if(audit[field]!=null) { controls[field]=audit[field]; audit.Remove(field); }
        }
        audit.Remove("apiId");
        context.Variables["gatewayAuditSettings"] = audit.ToString(Formatting.None);
        context.Variables["gatewayControlSettings"] = controls.ToString(Formatting.None);
        if ((bool?)item["legacySettings"] == true) {
          context.Variables["gatewayAuditSettings"] = item["settings"].ToString(Formatting.None);
          context.Variables.Remove("gatewayControlSettings");
        }
      }
      if (item.Property("controlRaw") != null) context.Variables["gatewayControlSettings"] =
        item["controlRaw"].Type == JTokenType.Null ? null : (string)item["controlRaw"];
      if (item["selection"] != null) context.Variables["gatewayOperationSelection"] = item["selection"].ToString(Formatting.None);
      if (item.Property("selectionRaw") != null) context.Variables["gatewayOperationSelection"] = ((JValue)item["selectionRaw"]).Value;
      if (item["auditModel"] != null) context.Variables["gatewayAuditModel"] = (string)item["auditModel"];
      if (item["connectionExpiry"] != null) context.Variables["gatewayConnectionExpiresOn"] = ((JValue)item["connectionExpiry"]).Value;
      if (item["claims"] != null) {
        var jwt = new Jwt();
        foreach (var claim in ((JObject)item["claims"]).Properties()) jwt.Claims[claim.Name] = new string[] { (string)claim.Value };
        context.Variables["gatewayValidatedToken"] = jwt;
      }
      var mode = (string)item["mode"];
      if ((bool?)item["subjectMissing"] != true && (bool?)item["requestContextMissing"] != true) context.Variables["gatewayAuthenticatedSubject"] = Subject(context);
      if (item.Property("subjectRaw") != null) context.Variables["gatewayAuthenticatedSubject"] = item["subjectRaw"].Type == JTokenType.Null ? null : item["subjectRaw"].Type == JTokenType.String ? (string)item["subjectRaw"] : item["subjectRaw"].ToString(Formatting.None);
      if (mode == "on-error") context.Variables["gatewayAuditStatus"] = ErrorStatus(context);
      string raw;
      if (mode == "telemetry" || mode == "on-error") raw = Telemetry(context);
      else {
        // APIMのon-error engineは再現せず、実Limits式から外へ伝わった例外だけを観測します。
        try { raw = Limits(context); }
        catch {
          // 例外の詳細・型・要求本文を観測結果へ含めません。HTTP statusへの変換もしません。
          results[(string)item["id"]] = new JObject(new JProperty("exceptionPropagated", true));
          continue;
        }
        if ((bool?)item["propagationProbe"] == true) {
          // 伝播しなかった場合も結果本文や要求本文を出さず、成否だけを観測します。
          results[(string)item["id"]] = new JObject(new JProperty("exceptionPropagated", false));
          continue;
        }
        if (mode == "limits-audit") {
          context.Variables["gatewayLimitDecision"] = raw;
          raw = LimitFailure(context);
        }
      }
      results[(string)item["id"]] = new JObject(
        new JProperty("result", JObject.Parse(raw)),
        new JProperty("reads", context.Request.Body.Reads),
        new JProperty("byteReads", context.Request.Body.ByteReads),
        new JProperty("preserved", context.Request.Body.AllReadsPreserved),
        new JProperty("bodyAfter", Encoding.UTF8.GetString(context.Request.Body.Data)));
    }
    return results.ToString(Formatting.None);
  }
}
""".replace("__LIMITS__", LIMIT_EXPRESSION).replace("__TELEMETRY__", TELEMETRY_EXPRESSION).replace("__REQUEST_CONTEXT__", REQUEST_CONTEXT_EXPRESSION).replace("__SUBJECT__", SUBJECT_EXPRESSION).replace("__ERROR_STATUS__", ERROR_STATUS_EXPRESSION[2:-1])
for marker, value in {
    "__LIMIT_AUDIT_ASSIGNMENTS__": LIMIT_AUDIT_ASSIGNMENTS,
    "__LIMIT_RESPONSE_STATUS__": single_expression(LIMIT_RESPONSE.find("./set-status").get("code")),
    "__LIMIT_RESPONSE_REASON__": single_expression(LIMIT_RESPONSE.find("./set-status").get("reason")),
    "__LIMIT_RESPONSE_REQUEST_ID__": single_expression(LIMIT_RESPONSE.find("./set-header[@name='apim-request-id']/value").text),
    "__LIMIT_RESPONSE_BODY__": single_expression(LIMIT_RESPONSE.find("./set-body").text),
    "__PII_LIMIT_CONDITION__": single_expression(PII_LIMIT_BRANCH.get("condition")),
    "__PII_LIMIT_STATUS__": str(int(PII_LIMIT_RESPONSE.find("./set-status").get("code"))),
    "__PII_LIMIT_REASON__": json.dumps(PII_LIMIT_RESPONSE.find("./set-status").get("reason")),
    "__PII_LIMIT_REQUEST_ID__": single_expression(PII_LIMIT_RESPONSE.find("./set-header[@name='apim-request-id']/value").text),
    "__PII_LIMIT_BODY__": PII_LIMIT_BODY[2:-1],
}.items():
    CSHARP = CSHARP.replace(marker, value)


def limit_case(case_id, body, constraint=None):
    """要求と上限を変えた1ケースを組み立て、境界値と拒否理由を比較できるようにする。"""
    selected = dict(constraint or {"kind": "statelessResponses", "maxRequestBytes": 1024,
                                   "maxOutputTokens": 100, "outputTokenParameter": "max_output_tokens"})
    # 本文ありHTTP操作は配備normalizerでjsonを補います。noneを含む明示値は変更しません。
    # 設定不備の試験は、補完後に欠落させるか、明示した不正値をこのまま実式へ渡します。
    selected.setdefault("bodyFormat", "json")
    return {"id": case_id, "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")) if not isinstance(body, str) else body,
            "constraint": selected}


CASES = [
    limit_case("valid", {"input": "synthetic", "max_output_tokens": 100}),
    limit_case("missing-output-limit", {"input": "synthetic"}),
    limit_case("output-above-former-limit", {"input": "synthetic", "max_output_tokens": 101}),
    limit_case("large-output-limit", {"input": "synthetic", "max_output_tokens": 9223372036854775807}),
    limit_case("zero-output-limit", {"input": "synthetic", "max_output_tokens": 0}),
    limit_case("float-output-limit", {"input": "synthetic", "max_output_tokens": 10.0}),
    limit_case("string-output-limit", {"input": "synthetic", "max_output_tokens": "10"}),
    limit_case("bool-output-limit", {"input": "synthetic", "max_output_tokens": True}),
    limit_case("null-output-limit", {"input": "synthetic", "max_output_tokens": None}),
    limit_case("negative-output-limit", {"input": "synthetic", "max_output_tokens": -1}),
    limit_case("mixed-output-fields", {"input": "synthetic", "max_output_tokens": 10, "max_tokens": 10}),
    limit_case("chat", {"messages": [], "max_completion_tokens": 25},
               {"kind": "bodyModel", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_completion_tokens"}),
    limit_case("chat-alternate-output-field", {"messages": [], "max_output_tokens": 25},
               {"kind": "bodyModel", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_completion_tokens"}),
    limit_case("chat-null-alternate-output-field", {"messages": [], "max_output_tokens": None},
               {"kind": "bodyModel", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_completion_tokens"}),
    limit_case("get-connection", "", {"kind": "registeredOperation", "bodyFormat": "none", "maxRequestBytes": 32768}),
    limit_case("embedding", {"model": "synthetic-embedding", "input": "synthetic text"},
               {"kind": "bodyModel", "maxRequestBytes": 32768, "allowedModels": ["synthetic-embedding"]}),
    limit_case("rerank-invalid-json", "{synthetic malformed JSON",
               {"kind": "rerank", "maxRequestBytes": 1024,
                "stringChoices": {"quality": ["low"]}}),
    limit_case("registered-output-only-invalid-json", "{synthetic malformed JSON",
               {"kind": "registeredOperation", "maxRequestBytes": 1024, "outputTokenParameter": "max_output_tokens"}),
    limit_case("missing-config", {}, {"kind": "statelessResponses"}),
    limit_case("unused-output-token-setting", {"max_tokens": 10},
               {"kind": "statelessResponses", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_tokens"}),
    limit_case("byte-boundary", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 30}),
    limit_case("multibyte-excess", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 29}),
    {**limit_case("measured-byte-boundary", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 30}),
     "selection": {"allowed": True, "requestBodyBytes": 30}},
    {**limit_case("measured-multibyte-excess", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 29}),
     "selection": {"allowed": True, "requestBodyBytes": 30}},
    {"id": "audit-accepted", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "gatewayAuditPhase": "accepted", "gatewayAuditCode": "GATEWAY_ALLOWED",
     "decision": {"allowed": True, "callerId": "subscription:subscription-a", "method": "subscriptionKey", "accessToken": "SENTINEL_ACCESS_TOKEN"},
     "claims": {"ver": "2.0", "tid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "oid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "azp": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "unapproved": "SENTINEL_CLAIM_SECRET"}},
    {"id": "audit-denied", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "gatewayAuditPhase": "rejected", "gatewayAuditCode": "AUTHORIZATION_DENIED", "responseStatus": 403,
     "decision": {"allowed": False, "contractId": "SENTINEL_UNVERIFIED_CONTRACT", "callerId": "SENTINEL_UNVERIFIED_CALLER"}},
    {"id": "audit-no-token", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "gatewayAuditPhase": "accepted", "gatewayAuditCode": "GATEWAY_ALLOWED",
     "decision": {"allowed": True, "callerId": "subscription:subscription-a", "method": "subscriptionKey"}},
    {"id": "audit-caller-mismatch", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "decision": {"allowed": True, "callerId": "subscription:SENTINEL_UNVERIFIED", "method": "subscriptionKey"}},
    {"id": "audit-subscription-absent", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "nullSubscription": True,
     "decision": {"allowed": True, "callerId": "subscription:subscription-a", "method": "subscriptionKey"}},
    {"id": "audit-policy-error", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "gatewayAuditPhase": "policy-error", "gatewayAuditCode": "POLICY_ERROR", "responseStatus": 500,
     "decision": "SENTINEL_INVALID_JSON", "claims": {"ver": "2.0", "oid": "SENTINEL_INVALID_PRINCIPAL"}},
    {"id": "audit-untrusted-values", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "gatewayAuditPhase": "SENTINEL_UNTRUSTED_PHASE", "gatewayAuditCode": "SENTINEL secret with spaces"},
    {"id": "audit-connection-expiry", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "connectionExpiry": 1792382400},
    {"id": "audit-connection-expiry-absent", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT"},
    {"id": "audit-connection-expiry-zero", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "connectionExpiry": 0},
    {"id": "audit-connection-expiry-ms", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "connectionExpiry": 1792382400000},
    {"id": "audit-connection-expiry-text", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "connectionExpiry": "SENTINEL_METADATA"},
    {"id": "audit-pre-base", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "requestContextMissing": True,
     "gatewayAuditPhase":"rejected", "gatewayAuditCode":"INVALID_REQUEST", "responseStatus":400},
    {"id": "audit-broken-request-context", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "requestContextRaw":"{broken"},
    {"id": "audit-no-api-operation", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "nullApi":True,"nullOperation":True},
    {"id": "audit-mcp-operation-override", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "api":"synthetic-mcp","operation":"mcp-execute",
     "auditModel":"synthetic-model","settings":{"operationId":"chat-create","llmOperationIds":["chat-create"],
       "serviceClass":"platform","backendService":"foundryOpenAi","backendAuthentication":"managedIdentity","measurementRole":"facade"}},
]

# この5ケースは設定JSONの読取りだけを失敗させます。本文を読む前に停止することを確認します。
CONSTRAINT_READER_FAILURES = ["constraint-reader-missing"]
CASES.append({**limit_case("constraint-reader-missing", "SENTINEL_RAW_CLIENT_REQUEST"), "constraintMissing": True})
for label, raw in (("null", None), ("non-string", 1), ("malformed", "{"), ("array", "[]")):
    identifier = "constraint-reader-" + label
    CONSTRAINT_READER_FAILURES.append(identifier)
    CASES.append({**limit_case(identifier, "SENTINEL_RAW_CLIENT_REQUEST"), "constraintRaw": raw})

# 測定値を含む内部結果を読めない場合も、本文解析の400と区別します。
CASES.append({**limit_case("selection-reader-malformed", "SENTINEL_RAW_CLIENT_REQUEST"), "selectionRaw": "{"})

UNEXPECTED_LIMITS_CASES = ("unexpected-body-reader", "unexpected-kind-object")
CASES.append({**limit_case("unexpected-body-reader", "SENTINEL_SYNTHETIC_REQUEST_BODY"), "throwOnByteRead": True, "propagationProbe": True})
CASES.append({**limit_case("unexpected-kind-object", "SENTINEL_SYNTHETIC_REQUEST_BODY",
                          {"kind": {"synthetic": True}, "maxRequestBytes": 1024}), "propagationProbe": True})

# JSON整数として読めてもlongに収まらない設定値は、本文を読む前に設定不備として扱います。
CASES.append(limit_case("max-request-bytes-overflow", "SENTINEL_RAW_CLIENT_REQUEST",
                       {"kind": "registeredOperation", "maxRequestBytes": 9223372036854775808}))
MAX_BYTES_FAILURES = ["max-request-bytes-overflow"]
for body_format in ("binary", "multipart", "text"):
    identifier = "max-request-bytes-overflow-" + body_format
    MAX_BYTES_FAILURES.append(identifier)
    CASES.append(limit_case(identifier, "SENTINEL_RAW_CLIENT_REQUEST",
                           {"kind": "registeredOperation", "bodyFormat": body_format,
                            "maxRequestBytes": 9223372036854775808}))
# noneは本文を扱いません。JSONと形式を確認できれば、本文上限の欠落・不正を読まずに成功します。
NO_BODY_SIZE_CASES = ["none-size-missing", "none-size-overflow"]
CASES.extend([
    limit_case("none-size-missing", "SENTINEL_RAW_CLIENT_REQUEST",
               {"kind": "registeredOperation", "bodyFormat": "none"}),
    limit_case("none-size-overflow", "SENTINEL_RAW_CLIENT_REQUEST",
               {"kind": "registeredOperation", "bodyFormat": "none", "maxRequestBytes": 9223372036854775808}),
])
# runtimeへ届く内部設定不備を再現するため、欠落ケースだけは配備相当の補完後に項目を削除します。
BODY_FORMAT_FAILURES = ["body-format-missing"]
missing_format = limit_case("body-format-missing", "SENTINEL_RAW_CLIENT_REQUEST",
                            {"kind": "registeredOperation", "maxRequestBytes": 128})
missing_format["constraint"].pop("bodyFormat")
CASES.append(missing_format)
for label, value in (("null", None), ("unknown", "xml"), ("empty", ""), ("number", 1),
                     ("boolean", True), ("object", {}), ("array", [])):
    identifier = "body-format-" + label
    BODY_FORMAT_FAILURES.append(identifier)
    CASES.append(limit_case(identifier, "SENTINEL_RAW_CLIENT_REQUEST",
                           {"kind": "registeredOperation", "maxRequestBytes": 128, "bodyFormat": value}))
# PIIのcompact上限12バイトに対する、エスケープを含む受信表現の6倍境界を小さな本文で確認します。
PII_WIRE_BODY = r'{"text":"\u0061"}'
CASES.extend([
    limit_case("pii-wire-boundary", PII_WIRE_BODY.ljust(72), {"kind": "pii", "maxRequestBytes": 12}),
    limit_case("pii-wire-excess", PII_WIRE_BODY.ljust(73), {"kind": "pii", "maxRequestBytes": 12}),
])
# 設定不備と通常/PIIの本文超過を、実Limits→呼出元XML→Telemetry・応答式の順で検証します。
for identifier in ("missing-config", "multibyte-excess", "pii-wire-excess"):
    CASES.append({**next(case for case in CASES if case["id"] == identifier),
                  "id": "limits-audit-" + identifier, "mode": "limits-audit"})
# モデル選択を使わないJSONには計測値がなく、従来の実サイズ確認を続けます。
next(case for case in CASES if case["id"] == "byte-boundary")["selection"] = {"allowed": True}

# 認証は共通入口で完了し、認可decisionが未作成/拒否でも主体を保持するケース。
_subject_tenant = "11111111-1111-4111-8111-111111111111"
_subject_person = "22222222-2222-4222-8222-222222222222"
_subject_client = "33333333-3333-4333-8333-333333333333"
_subject_caller = "user:" + _subject_tenant + ":" + _subject_person
_subject_user = {"schemaVersion": 1, "tenantId": _subject_tenant, "principalId": _subject_person,
                 "clientId": _subject_client, "callerId": _subject_caller, "allocationId": _subject_caller}
_subject_base = {"mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "gatewayAuditPhase": "rejected",
                 "gatewayAuditCode": "AUTHORIZATION_DENIED", "responseStatus": 403,
                 "commonValidated": True, "entraValidated": True, "nullSubscription": True,
                 "configuredTenant": _subject_tenant, "userAuthentication": _subject_user}
CASES.extend([
    {**_subject_base, "id": "subject-user-early-denied", "decision": {"allowed": False, "callerId": "SENTINEL_UNVERIFIED_CALLER"}},
    {**_subject_base, "id": "subject-user-before-decision"},
    {**_subject_base, "id": "subject-user-client-absent", "userAuthentication": {**_subject_user, "clientId": ""}},
    {"id": "subject-key-early-denied", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "commonValidated": True, "subscriptionSnapshot": "subscription-a", "gatewayAuditPhase": "rejected",
     "headers": {"Authorization": "Bearer SENTINEL_TOKEN"}, "claims": {"oid": "SENTINEL_UNVERIFIED_PRINCIPAL"},
     "decision": {"allowed": False, "callerId": "SENTINEL_UNVERIFIED_CALLER"}},
    {**_subject_base, "id": "subject-key-priority", "nullSubscription": False, "subscriptionSnapshot": "subscription-a",
     "userAuthentication": "SENTINEL_INVALID_JSON"},
    {"id": "subject-no-common-marker", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "claims": {"tid": _subject_tenant, "oid": _subject_person}},
    {**_subject_base, "id": "subject-forged-package", "commonValidated": False, "entraValidated": False,
     "headers": {"X-Core-Caller-Id": "SENTINEL_FORGED_CALLER", "X-Core-Subscription-Id": "SENTINEL_FORGED_SUBSCRIPTION"}},
    {**_subject_base, "id": "subject-entra-marker-only", "commonValidated": False},
    {**_subject_base, "id": "subject-marker-wrong-type", "commonValidated": "true"},
    {**_subject_base, "id": "subject-marker-empty", "commonValidated": ""},
    {"id": "subject-key-inconsistent", "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
     "commonValidated": True, "subscriptionSnapshot": "SENTINEL_DIFFERENT_SUBSCRIPTION"},
])
for _label, _user in (
    ("caller", {**_subject_user, "callerId": "SENTINEL_UNVERIFIED_CALLER"}),
    ("allocation", {**_subject_user, "allocationId": "SENTINEL_UNVERIFIED_ALLOCATION"}),
    ("principal", {**_subject_user, "principalId": "00000000-0000-0000-0000-000000000000"}),
    ("client", {**_subject_user, "clientId": "SENTINEL_UNVERIFIED_CLIENT"}),
    ("schema", {**_subject_user, "schemaVersion": True}),
    ("malformed", "{broken"), ("empty", ""), ("missing-fields", {}), ("oversized", " " * 4097),
):
    CASES.append({**_subject_base, "id": "subject-user-inconsistent-" + _label, "userAuthentication": _user})
CASES.append({**_subject_base, "id": "subject-user-inconsistent-tenant", "configuredTenant": _subject_person})
for _label, _raw in (("malformed", "{broken"), ("null", None), ("oversized", " " * 1601),
                     ("wrong-type", {"status": "verified", "kind": "apimSubscription", "method": "subscriptionKey", "callerId": [], "subscriptionId": "subscription-a"}),
                     ("caller", {"status": "verified", "kind": "apimSubscription", "method": "subscriptionKey", "callerId": "SENTINEL_FORGED_CALLER", "subscriptionId": "subscription-a"})):
    CASES.append({"id": "subject-observation-" + _label, "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT", "subjectRaw": _raw})

# Regression cases use the shipped contracts, including optional native fields.
for _id, _api, _operation, _body in (
    ("native-responses-history", "openai-inference-v1", "responses-create", {"model": "gpt-6.1-sol", "input": "a" * 98304}),
    ("native-image-multiple", "openai-images-v1", "image-generate", {"model": "gpt-image-2", "n": 11, "size": "2048x1024", "quality": "high"}),
    ("native-image-default", "openai-images-v1", "image-generate", {"model": "gpt-image-2", "prompt": "synthetic"}),
    ("native-flux-dimensions", "blackforest-images-v1", "flux2-generate", {"model": "FLUX.2-flex", "width": 2048, "height": 1024}),
    ("native-flux-multiple-output", "blackforest-images-v1", "flux2-generate", {"model": "FLUX.2-flex", "n": 2, "num_images": 2, "output_format": "synthetic"}),
):
    _constraint = json.loads((CORE / "apps/apim/apis" / (_api + ".json")).read_text())["authorization"]["operationConstraints"][_operation]
    _constraint = dict(_constraint)
    if "outputTokenParameter" in _constraint:
        _constraint["maxOutputTokens"] = 2000000
    CASES.append(limit_case(_id, _body, _constraint))
CASES.append(limit_case("published-token-above-old-ceiling", {"max_tokens": 1500000},
                       {"kind": "bodyModel", "maxRequestBytes": 1024, "outputTokenParameter": "max_tokens", "maxOutputTokens": 2000000}))

MODEL_LIMITS = {"kind": "bodyModel", "maxRequestBytes": 4096, "maxOutputTokens": 384000,
                "outputTokenParameter": "max_tokens", "maxOutputTokensByModel": {"small": 128000, "large": 384000, "router": None}}
CASES.extend([
    limit_case("model-small-boundary", {"model": "small", "max_tokens": 128000}, MODEL_LIMITS),
    limit_case("model-output-above-former-limit", {"model": "small", "max_tokens": 128001}, MODEL_LIMITS),
    limit_case("model-large-boundary", {"model": "large", "max_tokens": 384000}, MODEL_LIMITS),
    limit_case("model-not-registered", {"model": "unknown", "max_tokens": 1}, MODEL_LIMITS),
    limit_case("model-router-auto", {"model": "router"}, MODEL_LIMITS),
])


# 切断・未確定・redirectをエラーstatusへ捏造せず、確定した4xx/5xxだけを保つ境界値です。
for _status in (None, 0, 199, 200, 302, 399, 400, 401, 403, 429, 500, 502, 504, 599):
    CASES.append({"id": "audit-on-error-" + ("null" if _status is None else str(_status)),
                  "mode": "on-error", "body": "SENTINEL_RAW_PROMPT", "responseNull": _status is None,
                  "responseStatus": _status, "gatewayAuditPhase": "policy-error", "gatewayAuditCode": "POLICY_ERROR"})

for _format, _media in (("binary", "audio/wav"), ("multipart", "multipart/form-data"), ("text", "application/ssml+xml")):
    for _suffix, _headers in (("valid", {"Content-Type": _media + "; boundary=synthetic", "Content-Length": "12"}),
                               ("unknown", {"Content-Type": _media}),
                               ("too-large", {"Content-Type": _media, "Content-Length": "1025"}),
                               ("chunked", {"Content-Type": _media, "Content-Length": "12", "Transfer-Encoding": "chunked"}),
                               ("wrong-type", {"Content-Type": "application/json", "Content-Length": "12"}),
                               ("missing-types", {"Content-Type": _media, "Content-Length": "12"})):
        _case = limit_case(_format + "-" + _suffix, "not JSON!", {"kind": "registeredOperation", "bodyFormat": _format, "maxRequestBytes": 1024, "contentTypes": [_media]})
        if _suffix == "missing-types":
            _case["constraint"].pop("contentTypes")
        _case["headers"] = _headers
        CASES.append(_case)
# HTTP層での受付は再現せず、APIMポリシー式へ既に渡されたヘッダーの扱いだけを検証します。
for _suffix, _length in (("invalid-length", "not-a-number"), ("negative-length", "-1")):
    _case = limit_case("binary-" + _suffix, "not JSON!",
                       {"kind": "registeredOperation", "bodyFormat": "binary", "maxRequestBytes": 1024})
    _case["headers"] = {"Content-Type": "application/json", "Content-Length": _length}
    CASES.append(_case)
for _suffix, _headers in (("lengthless", {}), ("understated", {"Content-Length": "1"}), ("chunked", {"Transfer-Encoding": "chunked"})):
    _case = limit_case("actual-upload-" + _suffix, "a" * 1025,
                       {"kind": "registeredOperation", "bodyFormat": "binary", "maxRequestBytes": 1024})
    _case["headers"] = {"Content-Type": "audio/wav", **_headers}
    CASES.append(_case)
# 非JSONの宣言値のみを確認する3形式と、同じ本文を全量確認する明示actualを比較します。
# 合成ヘッダーを式へ直接渡すため、HTTP層での受付や下流への転送は検証していません。
for _format, _headers in (("binary", {}), ("multipart", {"Content-Length": "1"}),
                           ("text", {"Content-Length": "not-a-number"})):
    _case = limit_case("declared-upload-" + _format, "a" * 1025,
                       {"kind": "registeredOperation", "bodyFormat": _format,
                        "maxRequestBytes": 1024, "requestBodySizeCheck": "declared"})
    _case["headers"] = _headers
    CASES.append(_case)
    CASES.append({**_case, "id": "explicit-actual-upload-" + _format,
                  "constraint": {**_case["constraint"], "requestBodySizeCheck": "actual"}})
_case = limit_case("declared-upload-excess", "a" * 1025,
                   {"kind": "registeredOperation", "bodyFormat": "binary",
                    "maxRequestBytes": 1024, "requestBodySizeCheck": "declared"})
_case["headers"] = {"Content-Length": "1025"}
CASES.append(_case)
# JSON/noneではこの指定を配備できません。式へ直接渡しても既存の本文処理を変えない境界を確認します。
for _format in ("json", "none"):
    CASES.append(limit_case("declared-upload-" + _format, "a" * 1025,
                           {"kind": "registeredOperation", "bodyFormat": _format,
                            "maxRequestBytes": 1024, "requestBodySizeCheck": "declared"}))

NATIVE_UPLOAD_OPERATIONS = (
    ("azure-speech-v1", "stt", "declared"),
    ("openai-audio-v1", "audio-transcriptions", "declared"),
    ("openai-images-v1", "image-edit", "declared"),
    ("azure-speech-v1", "tts", "declared"),
    ("document-analysis-v1", "analyze_document", "declared"),
)
for _api, _operation, _mode in NATIVE_UPLOAD_OPERATIONS:
    _constraint = json.loads((CORE / "apps/apim/apis" / (_api + ".json")).read_text())["authorization"]["operationConstraints"][_operation]
    # 標準操作の形式・検査方法を使い、500 MiBの本文を作らずに小さなサイズ境界で実式を確認します。
    CASES.append(limit_case("native-upload-" + _api + "-" + _operation, "a" * 1025,
                           {**_constraint, "maxRequestBytes": 1024}))
CASES.append(limit_case("native-rerank-default", {"query": "query", "documents": ["document"] * 17},
                       {"kind": "rerank", "maxRequestBytes": 65536}))
CASES.append(limit_case("protocol-unread", "opaque protocol frames", {"kind": "registeredOperation", "bodyFormat": "none", "maxRequestBytes": 1024}))
# JSONサイズ分岐以外は、使わない選択結果のJSONを読まないことも確認します。
for _case in CASES:
    if _case["id"] in ("binary-valid", "multipart-valid", "text-valid", "protocol-unread"):
        _case["selectionRaw"] = "{"
for _classification in ("platform", "sample", "untrusted-value"):
    CASES.append({"id": "audit-class-" + _classification, "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
                  "settings": {"serviceClass": _classification, "backendService": "foundryOpenAi", "backendAuthentication": "managedIdentity",
                               "measurementRole": "execution", "targetAgent": "", "knownAgents": [], "llmOperationIds": []}})
for _name, _body in (("bounded-fields", {"documents": ["a", "b"], "top_n": 2, "quality": "low"}),
                     ("documents-above-former-limit", {"documents": ["a", "b", "c"], "top_n": 2}),
                     ("float-integer", {"top_n": 2.0}), ("missing-required-integer", {}),
                     ("unlisted-choice", {"top_n": 1, "quality": "high"})):
    CASES.append(limit_case(_name, _body, {"kind": "bodyModel", "bodyFormat": "json", "maxRequestBytes": 1024,
         "integerLimits": {"top_n": {"minimum": 1, "maximum": 2, "required": True}},
         "arrayLimits": {"documents": 2}, "stringChoices": {"quality": ["low"]}}))

INTEGER_ONLY_CASES = ("integer-only-missing", "integer-only-string", "integer-only-malformed-json")
for _name, _body in zip(INTEGER_ONLY_CASES, ({}, {"count": "synthetic"}, "{synthetic malformed JSON")):
    CASES.append(limit_case(_name, _body, {"kind": "registeredOperation", "maxRequestBytes": 1024,
        "integerLimits": {"count": {"minimum": 1, "maximum": 2, "required": True}}}))

ARRAY_ONLY_CASES = ("array-only-above-former-limit", "array-only-null", "array-only-string", "array-only-malformed-json")
for _name, _body in zip(ARRAY_ONLY_CASES, ({"documents": ["first", "second"]}, {"documents": None},
                                         {"documents": "synthetic"}, "{synthetic malformed JSON")):
    CASES.append(limit_case(_name, _body, {"kind": "registeredOperation", "maxRequestBytes": 1024,
        "arrayLimits": {"documents": 1}}))

STRING_ONLY_CASES = ("string-only-unlisted", "string-only-null", "string-only-number", "string-only-malformed-json")
for _name, _body in zip(STRING_ONLY_CASES, ({"quality": "high"}, {"quality": None},
                                         {"quality": 7}, "{synthetic malformed JSON")):
    CASES.append(limit_case(_name, _body, {"kind": "registeredOperation", "maxRequestBytes": 1024,
        "stringChoices": {"quality": ["low"]}}))

RERANK_LIMITS = {"kind": "rerank", "bodyFormat": "json", "maxRequestBytes": 65536,
                 "arrayLimits": {"documents": 8},
                 "integerLimits": {"top_n": {"minimum": 1, "maximum": 3, "required": True}}}
RERANK_BODY = {"model": "synthetic-ranker", "query": "synthetic query", "documents": ["synthetic document"], "top_n": 1}
RERANK_TOP_VARIANTS = {
    "cross-field": {"top_n": 2}, "empty-documents": {"documents": []},
    "zero-top": {"top_n": 0}, "null-top": {"top_n": None},
    "float-top": {"top_n": 1.0}, "string-top": {"top_n": "1"}, "bool-top": {"top_n": True},
}
RERANK_INPUT_VARIANTS = {
    "null-documents": {"documents": None}, "string-documents": {"documents": "document"},
    "object-document": {"documents": [{"text": "document"}]},
    "null-document": {"documents": [None]}, "number-document": {"documents": [1]},
    "empty-document": {"documents": [""]}, "blank-document": {"documents": [" \t\n"]},
    "bom-document": {"documents": ["\ufeff"]}, "empty-query": {"query": ""},
    "blank-query": {"query": " \t\n"}, "unicode-blank-query": {"query": "\u00a0\u2003\u2028\u3000\ufeff"},
    "null-query": {"query": None}, "number-query": {"query": 1}, "array-query": {"query": ["query"]},
}
for _name, _patch in {**RERANK_TOP_VARIANTS, **RERANK_INPUT_VARIANTS}.items():
    CASES.append(limit_case("rerank-" + _name, {**RERANK_BODY, **_patch}, RERANK_LIMITS))
for _field in ("query", "documents", "top_n"):
    CASES.append(limit_case("rerank-missing-" + _field,
                           {k: v for k, v in RERANK_BODY.items() if k != _field}, RERANK_LIMITS))
CASES.append(limit_case("rerank-empty-documents-no-top",
                       {**{k: v for k, v in RERANK_BODY.items() if k != "top_n"}, "documents": []}, RERANK_LIMITS))
for _name, _patch in (("minimum", {}), ("maximum", {"documents": ["document"] * 8, "top_n": 3}),
                       ("above-former-top-bound", {"documents": ["document"] * 8, "top_n": 4}),
                       ("above-former-document-bound", {"documents": ["document"] * 9}),
                       ("non-js-whitespace", {"query": "\u0085", "documents": ["\u0085"]}),
                       ("preserve-surrounding-space", {"query": "  query  ", "documents": ["  document  "]})):
    CASES.append(limit_case("rerank-" + _name, {**RERANK_BODY, **_patch}, RERANK_LIMITS))
_rerank_json = json.dumps(RERANK_BODY, separators=(",", ":"))
for _size in (65536, 65537):
    CASES.append(limit_case("rerank-bytes-" + str(_size),
                           _rerank_json + " " * (_size - len(_rerank_json.encode("utf-8"))), RERANK_LIMITS))
for _field in ("arrayLimits", "integerLimits"):
    CASES.append(limit_case("rerank-unconfigured-" + _field, RERANK_BODY,
                           {k: v for k, v in RERANK_LIMITS.items() if k != _field}))


for label, requested, effective, aliases in (
    ("valid", "synthetic-review", "synthetic-model", {"synthetic-review": "synthetic-model"}),
    ("unknown", "SENTINEL_RAW_MODEL", "synthetic-model", {}),
    ("wrong-target", "synthetic-review", "other-model", {"synthetic-review": "synthetic-model"}),
    ("unsafe", "SENTINEL_RAW_MODEL/secret", "synthetic-model", {"SENTINEL_RAW_MODEL/secret": "synthetic-model"})):
    CASES.append({"id": "audit-alias-" + label, "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
        "auditModel": "synthetic-model", "settings": {"llmOperationIds": ["responses-create"]},
        "selection": {"allowed": True, "requestedModel": requested, "effectiveModel": effective,
            "constraint": {"modelAliases": aliases}}})

for _name in ('audit-mcp-operation-override','audit-alias-valid'):
    _base=next(item for item in CASES if item['id']==_name)
    CASES.append({**_base,'id':_name+'-legacy-mixed','legacySettings':True})
    for _label, _raw in (('malformed', '{broken'), ('null', None), ('blank', ''), ('array', '[]'), ('scalar', '42')):
        CASES.append({**_base,'id':_name+'-'+_label+'-new','legacySettings':True,'controlRaw':_raw})


class GatewayExpressionTests(unittest.TestCase):
    """APIMの入力上限・生成token上限・安全な利用量記録を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required for actual C# policy-expression evaluation; no Azure checks were performed")
        with tempfile.TemporaryDirectory(prefix="core-policy-fixtures-") as temp:
            base = Path(temp)
            (base / "policy.cs").write_text(CSHARP)
            (base / "cases.json").write_text(json.dumps(CASES))
            script = base / "run.ps1"
            script.write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName) + @([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701'
[PolicyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("Actual policy C# failed offline compilation/execution:\n" + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_on_error_status_does_not_report_default_or_unconfirmed_success(self):
        """null・既定200・redirectは未確定0、実4xx/5xxは元の値を監査へ渡す。"""
        self.assertEqual(len(set(ERROR_STATUS_EXPRESSIONS.values())), 1)
        for status in (None, 0, 199, 200, 302, 399, 400, 401, 403, 429, 500, 502, 504, 599):
            case_id = "audit-on-error-" + ("null" if status is None else str(status))
            with self.subTest(status=status):
                record = self.decision(case_id)
                self.assertEqual(record["status"], status if status is not None and status >= 400 else 0)
                self.assertEqual(record["phase"], "policy-error")
                self.assertEqual(record["code"], "POLICY_ERROR")
                self.assertNotIn("SENTINEL", json.dumps(record))
                self.assertEqual(self.results[case_id]["reads"], 0)

    def decision(self, case_id):
        return self.results[case_id]["result"]

    def test_authenticated_subject_preserves_verified_identity_before_authorization(self):
        expected = {"status": "verified", "kind": "entraUser", "method": "user", "callerId": _subject_caller,
                    "allocationId": _subject_caller, "tenantId": _subject_tenant,
                    "principalId": _subject_person, "clientId": _subject_client}
        for name in ("subject-user-early-denied", "subject-user-before-decision"):
            with self.subTest(case=name):
                self.assertEqual(self.decision(name)["authenticatedSubject"], expected)
                self.assertEqual(self.results[name]["reads"], 0)
        self.assertEqual(self.decision("subject-user-early-denied")["authorization"], "denied")
        self.assertEqual(self.decision("subject-user-before-decision")["authorization"], "unconfirmed")
        self.assertEqual(self.decision("subject-user-client-absent")["authenticatedSubject"], {**expected, "clientId": ""})

    def test_authenticated_subject_uses_subscription_before_any_bearer_identity(self):
        expected = {"status": "verified", "kind": "apimSubscription", "method": "subscriptionKey",
                    "callerId": "subscription:subscription-a", "subscriptionId": "subscription-a"}
        for name in ("subject-key-early-denied", "subject-key-priority"):
            with self.subTest(case=name):
                self.assertEqual(self.decision(name)["authenticatedSubject"], expected)
                self.assertEqual(self.results[name]["reads"], 0)
                self.assertNotIn("SENTINEL", json.dumps(self.decision(name)))

    def test_authenticated_subject_withholds_identity_for_unverified_or_inconsistent_results(self):
        self.assertEqual(self.decision("subject-no-common-marker")["authenticatedSubject"], {"status": "unverified"})
        self.assertEqual(self.decision("subject-forged-package")["authenticatedSubject"], {"status": "unverified"})
        self.assertNotIn("SENTINEL", json.dumps(self.decision("subject-forged-package")))
        inconsistent = ["subject-entra-marker-only", "subject-marker-wrong-type", "subject-marker-empty", "subject-key-inconsistent"]
        inconsistent += ["subject-user-inconsistent-" + label for label in ("caller", "allocation", "principal", "client", "schema", "malformed", "empty", "missing-fields", "oversized", "tenant")]
        for name in inconsistent:
            with self.subTest(case=name):
                self.assertEqual(self.decision(name)["authenticatedSubject"], {"status": "inconsistent"})
                self.assertEqual(self.results[name]["reads"], 0)
                self.assertNotIn("SENTINEL", json.dumps(self.decision(name)))
        for label in ("malformed", "null", "oversized", "wrong-type", "caller"):
            name = "subject-observation-" + label
            with self.subTest(case=name):
                self.assertEqual(self.decision(name)["authenticatedSubject"], {"status": "inconsistent"})
                self.assertEqual(self.results[name]["reads"], 0)
                self.assertNotIn("SENTINEL", json.dumps(self.decision(name)))

    def test_service_class_is_explicitly_allowlisted_without_reading_request_body(self):
        for classification in ("platform", "sample"):
            case_id = "audit-class-" + classification
            self.assertEqual(self.decision(case_id)["serviceClass"], classification)
            self.assertEqual(self.results[case_id]["reads"], 0)
        self.assertNotIn("serviceClass", self.decision("audit-class-untrusted-value"))

    def test_binary_multipart_and_protocol_frames_are_never_read_as_json(self):
        for body_format in ("binary", "multipart", "text"):
            for suffix, status in (("valid", 200), ("unknown", 200), ("too-large", 413), ("chunked", 200), ("wrong-type", 200), ("missing-types", 200)):
                case_id = body_format + "-" + suffix
                with self.subTest(case_id=case_id):
                    self.assertEqual(self.decision(case_id)["status"], status)
                    self.assertEqual(self.results[case_id]["reads"], 1 if status == 200 else 0)
                    self.assertTrue(self.results[case_id]["preserved"])
                    self.assertEqual(self.results[case_id]["bodyAfter"], "not JSON!")
        for case_id in ("binary-invalid-length", "binary-negative-length"):
            with self.subTest(content_length=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "BOUNDED_UPLOAD"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], "not JSON!")
        for case_id in ("protocol-unread", *NO_BODY_SIZE_CASES):
            with self.subTest(no_body=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "NO_BODY_CONTRACT"})
                self.assertEqual(self.results[case_id]["reads"], 0)
                self.assertTrue(self.results[case_id]["preserved"])

    def test_native_optional_counts_and_actual_chunked_byte_bounds(self):
        self.assertTrue(self.decision("native-rerank-default")["allowed"])
        for suffix in ("lengthless", "understated", "chunked"):
            self.assertEqual(self.decision("actual-upload-" + suffix)["status"], 413)
            self.assertEqual(self.results["actual-upload-" + suffix]["reads"], 1)
            self.assertTrue(self.results["actual-upload-" + suffix]["preserved"])
            self.assertEqual(self.results["actual-upload-" + suffix]["bodyAfter"], "a" * 1025)

    def test_declared_upload_size_check_preserves_body_without_full_reads(self):
        for body_format in ("binary", "multipart", "text"):
            case_id = "declared-upload-" + body_format
            with self.subTest(declared=body_format):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "DECLARED_SIZE_ONLY"})
                self.assertEqual(self.results[case_id]["reads"], 0)
            actual_id = "explicit-actual-upload-" + body_format
            with self.subTest(actual=body_format):
                self.assertEqual(self.decision(actual_id), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
                self.assertEqual(self.results[actual_id]["reads"], 1)
            for identifier in (case_id, actual_id):
                self.assertTrue(self.results[identifier]["preserved"])
                self.assertEqual(self.results[identifier]["bodyAfter"], "a" * 1025)
        self.assertEqual(self.decision("declared-upload-excess"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
        self.assertEqual(self.results["declared-upload-excess"]["reads"], 0)
        self.assertEqual(self.decision("declared-upload-json"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
        self.assertEqual(self.results["declared-upload-json"]["reads"], 1)
        self.assertEqual(self.decision("declared-upload-none"), {"allowed": True, "status": 200, "code": "NO_BODY_CONTRACT"})
        self.assertEqual(self.results["declared-upload-none"]["reads"], 0)
        for identifier in ("declared-upload-excess", "declared-upload-json", "declared-upload-none"):
            self.assertTrue(self.results[identifier]["preserved"])
            self.assertEqual(self.results[identifier]["bodyAfter"], "a" * 1025)

    def test_native_upload_size_check_matches_standard_operation_settings(self):
        declared = set()
        for path in (CORE / "apps/apim/apis").glob("*.json"):
            definition = json.loads(path.read_text())
            for operation, constraint in definition.get("authorization", {}).get("operationConstraints", {}).items():
                if constraint.get("requestBodySizeCheck") == "declared":
                    declared.add((definition["name"], operation))
        self.assertEqual(declared, {(api, operation) for api, operation, mode in NATIVE_UPLOAD_OPERATIONS if mode == "declared"})
        for api, operation, mode in NATIVE_UPLOAD_OPERATIONS:
            case_id = "native-upload-" + api + "-" + operation
            with self.subTest(api=api, operation=operation):
                expected = {"allowed": True, "status": 200, "code": "DECLARED_SIZE_ONLY"} if mode == "declared" else {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"}
                self.assertEqual(self.decision(case_id), expected)
                self.assertEqual(self.results[case_id]["reads"], 0 if mode == "declared" else 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], "a" * 1025)

    def test_standard_apis_accept_provider_inputs_beyond_old_trial_limits(self):
        for case_id in ("native-responses-history", "native-image-multiple", "native-image-default",
                        "native-flux-dimensions", "native-flux-multiple-output", "published-token-above-old-ceiling"):
            self.assertTrue(self.decision(case_id)["allowed"], case_id)
        self.assertEqual(self.results["native-image-multiple"]["reads"], 1)
        self.assertEqual(self.results["native-flux-multiple-output"]["reads"], 1)

    def test_unused_integer_array_and_string_settings_do_not_restrict_limits(self):
        for case_id in ("bounded-fields", "float-integer", "missing-required-integer", "documents-above-former-limit", "unlisted-choice"):
            self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"}, case_id)
            self.assertEqual(self.results[case_id]["reads"], 1, case_id)

    def test_integer_only_settings_do_not_require_fields_or_parse_json(self):
        for case_id in INTEGER_ONLY_CASES:
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_array_only_settings_do_not_limit_counts_types_or_require_json_parsing(self):
        for case_id in ARRAY_ONLY_CASES:
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_string_only_settings_do_not_limit_values_types_or_require_json_parsing(self):
        for case_id in STRING_ONLY_CASES:
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_model_boundary_and_chat_contract(self):
        for case_id in ("valid", "chat"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})

    def test_model_output_maximum_tables_do_not_restrict_limits(self):
        for case_id in ("model-small-boundary", "model-large-boundary", "model-router-auto",
                        "model-output-above-former-limit", "model-not-registered"):
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_read_only_connection_and_embedding_do_not_require_generation_tokens(self):
        for case_id in ("get-connection", "embedding"):
            with self.subTest(case_id=case_id):
                expected_code = "NO_BODY_CONTRACT" if case_id == "get-connection" else "REQUEST_WITHIN_LIMITS"
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": expected_code})

    def test_output_count_can_be_omitted_or_exceed_former_maximum_without_rewriting(self):
        for case_id in ("missing-output-limit", "output-above-former-limit", "large-output-limit"):
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_output_values_and_unused_token_setting_do_not_trigger_json_reads_or_rejections(self):
        for case_id in ("zero-output-limit", "float-output-limit", "string-output-limit", "bool-output-limit",
                        "null-output-limit", "negative-output-limit", "unused-output-token-setting",
                        "registered-output-only-invalid-json"):
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_alternate_output_fields_are_preserved_when_alone_null_or_mixed(self):
        for case_id in ("chat-alternate-output-field", "chat-null-alternate-output-field", "mixed-output-fields"):
            with self.subTest(case_id=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_missing_or_incompatible_deployment_limits_fail_closed(self):
        for case_id in ("missing-config", *MAX_BYTES_FAILURES):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": False, "status": 500, "code": "LIMITS_NOT_CONFIGURED"})
        for case_id in MAX_BYTES_FAILURES:
            self.assertEqual(self.results[case_id]["reads"], 0, case_id)
            self.assertEqual(self.results[case_id]["bodyAfter"], "SENTINEL_RAW_CLIENT_REQUEST", case_id)

    def test_internal_constraint_and_result_read_failures_are_500_before_request_body_reads(self):
        for case_id in (*CONSTRAINT_READER_FAILURES, "selection-reader-malformed"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": False, "status": 500, "code": "LIMITS_NOT_CONFIGURED"})
                self.assertEqual(self.results[case_id]["reads"], 0)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], "SENTINEL_RAW_CLIENT_REQUEST")

    def test_unexpected_limits_exceptions_propagate_without_exposing_details_or_body(self):
        # 実C#式からfixture呼出元への伝播を確認します。APIM on-errorの応答や監査はこの試験の対象外です。
        for case_id in UNEXPECTED_LIMITS_CASES:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.results[case_id], {"exceptionPropagated": True})
        self.assertEqual(self.decision("valid"), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
        self.assertEqual(self.decision("constraint-reader-malformed"), {"allowed": False, "status": 500, "code": "LIMITS_NOT_CONFIGURED"})
        self.assertEqual(self.decision("multibyte-excess"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})

    def test_limits_failure_audit_and_response_use_the_same_status_code_and_request_id(self):
        nodes = list(LIMIT_FAILURE_POLICY)
        positions = {node.get("name"): nodes.index(node) for node in LIMIT_AUDIT_NODES}
        telemetry_position = nodes.index(LIMIT_FAILURE_POLICY.find("./include-fragment[@fragment-id='core-gateway-telemetry']"))
        self.assertLess(positions["gatewayAuditStatus"], positions["gatewayAuditPhase"])
        self.assertLess(max(positions.values()), telemetry_position)
        for response in (LIMIT_RESPONSE, PII_LIMIT_RESPONSE):
            header = response.find("./set-header[@name='Cache-Control']")
            self.assertEqual(header.get("exists-action"), "override")
            self.assertEqual(header.find("./value").text, "no-store")
        request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        for identifier, status, phase, code, reason, public_code in (
            ("missing-config", 500, "policy-error", "LIMITS_NOT_CONFIGURED", "Gateway processing failed", "LIMITS_NOT_CONFIGURED"),
            ("multibyte-excess", 413, "rejected", "REQUEST_TOO_LARGE", "Request too large", "REQUEST_TOO_LARGE"),
            ("pii-wire-excess", 413, "rejected", "REQUEST_TOO_LARGE", "Invalid PII input", "PiiInputTooLarge"),
        ):
            with self.subTest(case=identifier):
                observation = self.decision("limits-audit-" + identifier)
                audit = observation["audit"]
                self.assertEqual({key: audit[key] for key in ("status", "phase", "code", "requestId")},
                                 {"status": status, "phase": phase, "code": code, "requestId": request_id})
                self.assertEqual(observation["response"], {"status": status, "reason": reason, "requestIdHeader": request_id,
                                 "body": {"error": {"code": public_code}, "requestId": request_id}})
                self.assertEqual(self.results["limits-audit-" + identifier]["reads"], self.results[identifier]["reads"])

    def test_invalid_internal_body_formats_are_500_before_request_body_reads(self):
        for case_id in BODY_FORMAT_FAILURES:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": False, "status": 500, "code": "LIMITS_NOT_CONFIGURED"})
                self.assertEqual(self.results[case_id]["reads"], 0)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], "SENTINEL_RAW_CLIENT_REQUEST")

    def test_real_utf8_bytes_and_exact_boundary(self):
        for prefix, byte_reads in (("", 1), ("measured-", 0)):
            with self.subTest(measured=bool(prefix)):
                self.assertTrue(self.decision(prefix + "byte-boundary")["allowed"])
                self.assertEqual(self.decision(prefix + "multibyte-excess"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
                self.assertEqual(self.results[prefix + "byte-boundary"]["byteReads"], byte_reads)
                self.assertEqual(self.results[prefix + "multibyte-excess"]["byteReads"], byte_reads)

    def test_pii_wire_representation_keeps_its_reserved_byte_boundary(self):
        self.assertEqual(self.decision("pii-wire-boundary"), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
        self.assertEqual(self.decision("pii-wire-excess"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
        for case_id, size in (("pii-wire-boundary", 72), ("pii-wire-excess", 73)):
            self.assertEqual(self.results[case_id]["reads"], 1)
            self.assertEqual(self.results[case_id]["byteReads"], 1)
            self.assertTrue(self.results[case_id]["preserved"])
            self.assertEqual(self.results[case_id]["bodyAfter"], PII_WIRE_BODY.ljust(size))

    def test_rerank_does_not_parse_json_while_preserving_wire_bytes(self):
        self.assertEqual(self.decision("rerank-invalid-json"), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
        self.assertEqual(self.results["rerank-invalid-json"]["reads"], 1)
        self.assertEqual(self.results["rerank-invalid-json"]["byteReads"], 1)
        self.assertTrue(self.results["rerank-invalid-json"]["preserved"])
        self.assertEqual(self.results["rerank-invalid-json"]["bodyAfter"], "{synthetic malformed JSON")

    def test_request_is_preserved_and_never_silently_rewritten(self):
        for case in CASES:
            # 例外伝播ケースは本文を観測結果に含めず、上の専用テストで確認します。
            if case["id"] in UNEXPECTED_LIMITS_CASES:
                continue
            with self.subTest(case_id=case["id"]):
                self.assertEqual(self.results[case["id"]]["bodyAfter"], case["body"])
                self.assertTrue(self.results[case["id"]]["preserved"])

    def test_rerank_top_n_values_are_forwarded_without_rewriting(self):
        for suffix in RERANK_TOP_VARIANTS:
            case_id = "rerank-" + suffix
            with self.subTest(case=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_rerank_input_shapes_and_missing_fields_are_forwarded_without_rewriting(self):
        for suffix in (*RERANK_INPUT_VARIANTS, "missing-query", "missing-documents", "empty-documents-no-top"):
            case_id = "rerank-" + suffix
            with self.subTest(case=case_id):
                original = next(item["body"] for item in CASES if item["id"] == case_id)
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results[case_id]["reads"], 1)
                self.assertEqual(self.results[case_id]["byteReads"], 1)
                self.assertTrue(self.results[case_id]["preserved"])
                self.assertEqual(self.results[case_id]["bodyAfter"], original)

    def test_rerank_body_byte_boundary_is_enforced_without_field_checks(self):
        for suffix in ("minimum", "maximum", "above-former-top-bound", "missing-top_n",
                       "above-former-document-bound", "non-js-whitespace", "preserve-surrounding-space", "bytes-65536"):
            with self.subTest(case=suffix):
                self.assertEqual(self.decision("rerank-" + suffix),
                                 {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
                self.assertEqual(self.results["rerank-" + suffix]["reads"], 1)
                self.assertEqual(self.results["rerank-" + suffix]["byteReads"], 1)
        self.assertEqual(self.decision("rerank-bytes-65537"),
                         {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})

    def test_rerank_does_not_require_gateway_count_caps(self):
        for field in ("arrayLimits", "integerLimits"):
            self.assertEqual(self.decision("rerank-unconfigured-" + field),
                             {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})

    def test_verified_caller_and_common_request_id_are_recorded(self):
        result = self.decision("audit-accepted")
        self.assertEqual(result["requestId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(result["callerId"], "subscription:subscription-a")
        self.assertEqual(result["subscriptionId"], "subscription-a")
        self.assertEqual(result["method"], "subscriptionKey")
        self.assertNotIn("verifiedPrincipalId", result)
        self.assertNotIn("verifiedTenantId", result)
        self.assertNotIn("verifiedClientId", result)
        self.assertEqual(result["authorization"], "allowed")
        self.assertEqual(result, self.decision("audit-no-token"))

    def test_common_identity_and_pre_base_fallback_preserve_log_ids_without_body_reads(self):
        for name in ('audit-accepted','audit-pre-base','audit-broken-request-context'):
            with self.subTest(case=name):
                record=self.decision(name)
                self.assertEqual(record['requestId'],'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
                self.assertEqual(record['apiId'],'models')
                self.assertEqual(record['operationId'],'responses-create')
                self.assertEqual(self.results[name]['reads'],0)
        nulls=self.decision('audit-no-api-operation')
        self.assertIsNone(nulls['apiId'])
        self.assertIsNone(nulls['operationId'])

    def test_mcp_control_operation_override_does_not_replace_the_actual_logged_operation(self):
        record=self.decision('audit-mcp-operation-override')
        self.assertEqual(record['apiId'],'synthetic-mcp')
        self.assertEqual(record['operationId'],'mcp-execute')
        self.assertEqual(record['usageStatus'],'native-log-join-required')
        self.assertEqual(self.results['audit-mcp-operation-override']['reads'],0)

    def test_usage_requires_current_control_settings_and_never_reads_request_body(self):
        for name in ('audit-mcp-operation-override','audit-alias-valid'):
            with self.subTest(case=name):
                self.assertEqual(self.decision(name+'-legacy-mixed')['usageStatus'],'none')
                self.assertEqual(self.results[name+'-legacy-mixed']['reads'],0)
                for suffix in ('malformed', 'null', 'blank', 'array', 'scalar'):
                    self.assertEqual(self.decision(name+'-'+suffix+'-new')['usageStatus'],'none')
                    self.assertEqual(self.results[name+'-'+suffix+'-new']['reads'],0)

    def test_unconfirmed_subscription_never_records_caller_attribution(self):
        for case_id in ("audit-caller-mismatch", "audit-subscription-absent"):
            with self.subTest(case_id=case_id):
                result = self.decision(case_id)
                for field in ("callerId", "subscriptionId", "method", "verifiedPrincipalId"):
                    self.assertNotIn(field, result)
                self.assertNotIn("SENTINEL", json.dumps(result))

    def test_denial_does_not_attribute_unverified_registry_data(self):
        result = self.decision("audit-denied")
        self.assertEqual(result["authorization"], "denied")
        self.assertEqual(result["status"], 403)
        self.assertNotIn("contractId", result)
        self.assertNotIn("callerId", result)

    def test_only_validated_absolute_connection_expiry_is_audited(self):
        """期限は整数の許可範囲だけを記録し、未提供や不正値から更新成功を推測しない。"""
        self.assertEqual(self.decision("audit-connection-expiry")["connectionExpiresOn"], 1792382400)
        for suffix in ("absent", "zero", "ms", "text"):
            with self.subTest(case=suffix):
                record = self.decision("audit-connection-expiry-" + suffix)
                self.assertNotIn("connectionExpiresOn", record)
                self.assertNotIn("SENTINEL", json.dumps(record))

    def test_alias_audit_records_only_configured_matching_alias_without_reading_body(self):
        record = self.decision("audit-alias-valid")
        self.assertEqual(record["requestedModel"], "synthetic-review")
        self.assertTrue(record["modelAliasApplied"])
        self.assertEqual(record["targetModel"], "synthetic-model")
        for label in ("unknown", "wrong-target", "unsafe"):
            record = self.decision("audit-alias-" + label)
            self.assertNotIn("requestedModel", record)
            self.assertNotIn("modelAliasApplied", record)
            self.assertNotIn("SENTINEL", json.dumps(record))
        for label in ("valid", "unknown", "wrong-target", "unsafe"):
            self.assertEqual(self.results["audit-alias-" + label]["reads"], 0)

    def test_audit_is_allowlisted_and_never_reads_a_body(self):
        for case_id in ("audit-accepted", "audit-no-token", "audit-caller-mismatch", "audit-subscription-absent", "audit-denied", "audit-policy-error", "audit-untrusted-values"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.results[case_id]["reads"], 0)
                self.assertNotIn("SENTINEL", json.dumps(self.decision(case_id)))
        self.assertEqual(self.decision("audit-untrusted-values")["phase"], "unknown")
        self.assertEqual(self.decision("audit-untrusted-values")["code"], "UNSPECIFIED")

    def test_trace_targets_resource_logs_without_appi_or_body_capture(self):
        root = ET.parse(policy_fixture_path(POLICIES, "core-gateway-telemetry.xml")).getroot()
        self.assertEqual(root.find("trace").attrib, {"source": "core-gateway", "severity": "information"})
        for forbidden in ("context.Request.Body", "context.Response.Body", "context.Request.Headers", "context.Request.Url", "LastError.Message"):
            self.assertNotIn(forbidden, TELEMETRY_EXPRESSION)
        self.assertNotRegex(LIMIT_EXPRESSION, r"context\.Response\.Body|set-body|set-header")


if __name__ == "__main__":
    unittest.main()
