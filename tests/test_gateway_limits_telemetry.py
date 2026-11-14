"""APIMの入力上限・生成token上限・安全な利用量記録を検証する。

policyからC#式を抽出してローカル実行する。操作別の制限と帰属を確認するもので、実APIMへの適用やログ到達の証明ではない。
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.exists() else None


def expression(path, xpath, attribute=None):
    """XML内の指定位置からAPIMの実C#式を取り出す。想定外の形式なら試験を止める。"""
    element = ET.parse(path).find(xpath)
    value = element.get(attribute) if attribute else element.text
    if not value or not value.strip().startswith("@{") or not value.strip().endswith("}"):
        raise AssertionError("Expected an APIM multi-statement expression")
    return value.strip()[2:-1]


LIMIT_EXPRESSION = expression(POLICIES / "model-limits.xml", "./set-variable", "value")
TELEMETRY_EXPRESSION = expression(POLICIES / "gateway-telemetry.xml", "./trace/message")

# 本番XMLから例外時のstatus式を抽出します。C#で実行し、既定200を成功と誤記しないことを確認します。
ERROR_STATUS_EXPRESSIONS = {
    name: ET.parse(POLICIES / name).find("./on-error/set-variable[@name='gatewayAuditStatus']").get("value")
    for name in ("gateway.xml", "protocol.xml", "mcp.xml")
}
ERROR_STATUS_EXPRESSION = ERROR_STATUS_EXPRESSIONS["gateway.xml"]
if not ERROR_STATUS_EXPRESSION.startswith("@(") or not ERROR_STATUS_EXPRESSION.endswith(")"):
    raise AssertionError("Expected a single-statement APIM error status expression")

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
  public bool AllReadsPreserved = true;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    AllReadsPreserved &= preserveContent;
    object value;
    if (typeof(T) == typeof(byte[])) value = Data;
    else if (typeof(T) == typeof(JObject)) value = JObject.Parse(Encoding.UTF8.GetString(Data));
    else throw new Exception("Fixture does not support this message conversion");
    return (T)value;
  }
}
public sealed class Request { public Body Body; public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(); }
public sealed class Response {
  public int StatusCode;
  public Body Body { get { throw new Exception("Response body must remain untouched"); } }
}
public sealed class NamedContext { public string Id; }
public sealed class Jwt { public Dictionary<string, string[]> Claims = new Dictionary<string, string[]>(); }
public static class ClaimExtensions {
  public static string GetValueOrDefault(this Dictionary<string, string[]> claims, string key, string fallback) {
    return claims.ContainsKey(key) ? String.Join(" ", claims[key]) : fallback;
  }
}
public sealed class Context {
  public Dictionary<string, object> Variables = new Dictionary<string, object>();
  public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  public Request Request;
  public Response Response;
  public NamedContext Api = new NamedContext { Id = "models" };
  public NamedContext Operation = new NamedContext { Id = "responses-create" };
  public NamedContext Subscription = new NamedContext { Id = "subscription-a" };
}
public static class PolicyFixture {
  public static string Limits(Context context) { __LIMITS__ }
  public static string Telemetry(Context context) { __TELEMETRY__ }
  public static int ErrorStatus(Context context) { return __ERROR_STATUS__; }
  public static string Run(string casesJson) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(casesJson)) {
      var context = new Context {
        Request = new Request { Body = new Body { Data = Encoding.UTF8.GetBytes((string)item["body"] ?? "") } },
        Response = new Response { StatusCode = (int?)item["responseStatus"] ?? 200 }
      };
      if ((bool?)item["responseNull"] == true) context.Response = null;
      if ((bool?)item["nullSubscription"] == true) context.Subscription = null;
      if (item["headers"] != null) foreach (var entry in ((JObject)item["headers"]).Properties()) context.Request.Headers[entry.Name] = new string[] { (string)entry.Value };
      context.Variables["gatewayOperationConstraint"] = (item["constraint"] ?? new JObject()).ToString(Formatting.None);
      foreach (var field in new string[] { "gatewayAuditPhase", "gatewayAuditCode" }) {
        if (item[field] != null) context.Variables[field] = (string)item[field];
      }
      if (item["decision"] != null) context.Variables["gatewayDecision"] = item["decision"].Type == JTokenType.String ? (string)item["decision"] : item["decision"].ToString(Formatting.None);
      if (item["settings"] != null) context.Variables["gatewayAuditSettings"] = item["settings"].ToString(Formatting.None);
      if (item["selection"] != null) context.Variables["gatewayProfileSelection"] = item["selection"].ToString(Formatting.None);
      if (item["auditModel"] != null) context.Variables["gatewayAuditModel"] = (string)item["auditModel"];
      if (item["connectionExpiry"] != null) context.Variables["gatewayConnectionExpiresOn"] = ((JValue)item["connectionExpiry"]).Value;
      if (item["claims"] != null) {
        var jwt = new Jwt();
        foreach (var claim in ((JObject)item["claims"]).Properties()) jwt.Claims[claim.Name] = new string[] { (string)claim.Value };
        context.Variables["gatewayValidatedToken"] = jwt;
      }
      var mode = (string)item["mode"];
      if (mode == "on-error") context.Variables["gatewayAuditStatus"] = ErrorStatus(context);
      string raw = mode == "telemetry" || mode == "on-error" ? Telemetry(context) : Limits(context);
      results[(string)item["id"]] = new JObject(
        new JProperty("result", JObject.Parse(raw)),
        new JProperty("reads", context.Request.Body.Reads),
        new JProperty("preserved", context.Request.Body.AllReadsPreserved),
        new JProperty("bodyAfter", Encoding.UTF8.GetString(context.Request.Body.Data)));
    }
    return results.ToString(Formatting.None);
  }
}
""".replace("__LIMITS__", LIMIT_EXPRESSION).replace("__TELEMETRY__", TELEMETRY_EXPRESSION).replace("__ERROR_STATUS__", ERROR_STATUS_EXPRESSION[2:-1])


def limit_case(case_id, body, constraint=None):
    """要求と上限を変えた1ケースを組み立て、境界値と拒否理由を比較できるようにする。"""
    return {"id": case_id, "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")) if not isinstance(body, str) else body,
            "constraint": constraint or {"kind": "statelessResponses", "maxRequestBytes": 1024,
                                        "maxOutputTokens": 100, "outputTokenParameter": "max_output_tokens"}}


CASES = [
    limit_case("valid", {"input": "synthetic", "max_output_tokens": 100}),
    limit_case("missing-output-limit", {"input": "synthetic"}),
    limit_case("excess-output-limit", {"input": "synthetic", "max_output_tokens": 101}),
    limit_case("zero-output-limit", {"input": "synthetic", "max_output_tokens": 0}),
    limit_case("float-output-limit", {"input": "synthetic", "max_output_tokens": 10.0}),
    limit_case("string-output-limit", {"input": "synthetic", "max_output_tokens": "10"}),
    limit_case("bool-output-limit", {"input": "synthetic", "max_output_tokens": True}),
    limit_case("alias-ambiguity", {"input": "synthetic", "max_output_tokens": 10, "max_tokens": 10}),
    limit_case("chat", {"messages": [], "max_completion_tokens": 25},
               {"kind": "bodyModel", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_completion_tokens"}),
    limit_case("chat-wrong-field", {"messages": [], "max_output_tokens": 25},
               {"kind": "bodyModel", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_completion_tokens"}),
    limit_case("get-connection", "", {"kind": "registeredOperation", "maxRequestBytes": 32768}),
    limit_case("embedding", {"model": "synthetic-embedding", "input": "synthetic text"},
               {"kind": "bodyModel", "maxRequestBytes": 32768, "allowedModels": ["synthetic-embedding"]}),
    limit_case("invalid-json", "{synthetic malformed JSON"),
    limit_case("missing-config", {}, {"kind": "statelessResponses"}),
    limit_case("invalid-responses-alias", {"max_tokens": 10},
               {"kind": "statelessResponses", "maxRequestBytes": 1024, "maxOutputTokens": 25, "outputTokenParameter": "max_tokens"}),
    limit_case("byte-boundary", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 30}),
    limit_case("multibyte-excess", "あ" * 10, {"kind": "registeredOperation", "maxRequestBytes": 29}),
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
]

# Regression cases use the shipped contracts, including optional native fields.
for _id, _api, _operation, _body in (
    ("native-responses-history", "openai-inference-v1", "responses-create", {"model": "gpt-6.1-sol", "input": "a" * 98304}),
    ("native-image-multiple", "openai-images-v1", "image-generate", {"model": "gpt-image-2", "n": 10, "size": "2048x1024", "quality": "high"}),
    ("native-image-default", "openai-images-v1", "image-generate", {"model": "gpt-image-2", "prompt": "synthetic"}),
    ("native-flux-dimensions", "blackforest-images-v1", "flux2-generate", {"model": "FLUX.2-flex", "width": 2048, "height": 1024}),
    ("native-flux-one-output", "blackforest-images-v1", "flux2-generate", {"model": "FLUX.2-flex", "num_images": 2}),
):
    _constraint = json.loads((CORE / "apps/gateway/apim/apis" / (_api + ".json")).read_text())["authorization"]["operationConstraints"][_operation]
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
    limit_case("model-small-excess", {"model": "small", "max_tokens": 128001}, MODEL_LIMITS),
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
                               ("wrong-type", {"Content-Type": "application/json", "Content-Length": "12"})):
        _case = limit_case(_format + "-" + _suffix, "not JSON!", {"kind": "registeredOperation", "bodyFormat": _format, "maxRequestBytes": 1024, "contentTypes": [_media]})
        _case["headers"] = _headers
        CASES.append(_case)
for _suffix, _headers in (("lengthless", {}), ("understated", {"Content-Length": "1"}), ("chunked", {"Transfer-Encoding": "chunked"})):
    _case = limit_case("actual-upload-" + _suffix, "a" * 1025,
                       {"kind": "registeredOperation", "bodyFormat": "binary", "maxRequestBytes": 1024, "contentTypes": ["audio/wav"]})
    _case["headers"] = {"Content-Type": "audio/wav", **_headers}
    CASES.append(_case)
CASES.append(limit_case("native-rerank-default", {"query": "query", "documents": ["document"] * 17},
                       {"kind": "rerank", "maxRequestBytes": 65536}))
CASES.append(limit_case("protocol-unread", "opaque protocol frames", {"kind": "registeredOperation", "bodyFormat": "none", "maxRequestBytes": 1024}))
for _classification in ("platform", "sample", "untrusted-value"):
    CASES.append({"id": "audit-class-" + _classification, "mode": "telemetry", "body": "SENTINEL_RAW_PROMPT",
                  "settings": {"serviceClass": _classification, "backendService": "foundryOpenAi", "backendAuthentication": "managedIdentity",
                               "measurementRole": "execution", "targetAgent": "", "knownAgents": [], "llmOperationIds": []}})
for _name, _body in (("bounded-fields", {"documents": ["a", "b"], "top_n": 2, "quality": "low"}),
                     ("too-many-documents", {"documents": ["a", "b", "c"], "top_n": 2}),
                     ("float-integer", {"top_n": 2.0}), ("missing-required-integer", {}),
                     ("unapproved-choice", {"top_n": 1, "quality": "high"})):
    CASES.append(limit_case(_name, _body, {"kind": "bodyModel", "bodyFormat": "json", "maxRequestBytes": 1024,
         "integerLimits": {"top_n": {"minimum": 1, "maximum": 2, "required": True}},
         "arrayLimits": {"documents": 2}, "stringChoices": {"quality": ["low"]}}))

RERANK_LIMITS = {"kind": "rerank", "bodyFormat": "json", "maxRequestBytes": 65536,
                 "arrayLimits": {"documents": 8},
                 "integerLimits": {"top_n": {"minimum": 1, "maximum": 3, "required": True}}}
RERANK_BODY = {"model": "synthetic-ranker", "query": "synthetic query", "documents": ["synthetic document"], "top_n": 1}
RERANK_REJECTIONS = {
    "cross-field": {"top_n": 2}, "empty-documents": {"documents": []},
    "too-many-documents": {"documents": ["document"] * 9},
    "null-documents": {"documents": None}, "string-documents": {"documents": "document"},
    "object-document": {"documents": [{"text": "document"}]},
    "null-document": {"documents": [None]}, "number-document": {"documents": [1]},
    "empty-document": {"documents": [""]}, "blank-document": {"documents": [" \t\n"]},
    "bom-document": {"documents": ["\ufeff"]}, "empty-query": {"query": ""},
    "blank-query": {"query": " \t\n"}, "unicode-blank-query": {"query": "\u00a0\u2003\u2028\u3000\ufeff"},
    "null-query": {"query": None}, "number-query": {"query": 1}, "array-query": {"query": ["query"]},
    "zero-top": {"top_n": 0}, "excess-top": {"documents": ["document"] * 8, "top_n": 4},
    "float-top": {"top_n": 1.0}, "string-top": {"top_n": "1"}, "bool-top": {"top_n": True},
}
for _name, _patch in RERANK_REJECTIONS.items():
    CASES.append(limit_case("rerank-" + _name, {**RERANK_BODY, **_patch}, RERANK_LIMITS))
for _field in ("query", "documents", "top_n"):
    CASES.append(limit_case("rerank-missing-" + _field,
                           {k: v for k, v in RERANK_BODY.items() if k != _field}, RERANK_LIMITS))
for _name, _patch in (("minimum", {}), ("maximum", {"documents": ["document"] * 8, "top_n": 3}),
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

    def test_service_class_is_explicitly_allowlisted_without_reading_request_body(self):
        for classification in ("platform", "sample"):
            case_id = "audit-class-" + classification
            self.assertEqual(self.decision(case_id)["serviceClass"], classification)
            self.assertEqual(self.results[case_id]["reads"], 0)
        self.assertNotIn("serviceClass", self.decision("audit-class-untrusted-value"))

    def test_binary_multipart_and_protocol_frames_are_never_read_as_json(self):
        for body_format in ("binary", "multipart", "text"):
            for suffix, status in (("valid", 200), ("unknown", 200), ("too-large", 413), ("chunked", 200), ("wrong-type", 415)):
                case_id = body_format + "-" + suffix
                with self.subTest(case_id=case_id):
                    self.assertEqual(self.decision(case_id)["status"], status)
                    self.assertEqual(self.results[case_id]["reads"], 1 if status == 200 else 0)
        self.assertTrue(self.decision("protocol-unread")["allowed"])
        self.assertEqual(self.results["protocol-unread"]["reads"], 0)

    def test_native_optional_counts_and_actual_chunked_byte_bounds(self):
        self.assertTrue(self.decision("native-rerank-default")["allowed"])
        for suffix in ("lengthless", "understated", "chunked"):
            self.assertEqual(self.decision("actual-upload-" + suffix)["status"], 413)
            self.assertEqual(self.results["actual-upload-" + suffix]["reads"], 1)

    def test_standard_apis_accept_provider_inputs_beyond_old_trial_limits(self):
        for case_id in ("native-responses-history", "native-image-multiple", "native-image-default",
                        "native-flux-dimensions", "published-token-above-old-ceiling"):
            self.assertTrue(self.decision(case_id)["allowed"], case_id)
        self.assertFalse(self.decision("native-flux-one-output")["allowed"])

    def test_declared_json_cost_fields_are_bounded(self):
        self.assertTrue(self.decision("bounded-fields")["allowed"])
        for case_id in ("too-many-documents", "float-integer", "missing-required-integer", "unapproved-choice"):
            self.assertFalse(self.decision(case_id)["allowed"], case_id)

    def test_model_boundary_and_chat_contract(self):
        for case_id in ("valid", "chat"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})

    def test_output_limits_are_selected_per_model_and_router_allows_provider_default(self):
        for case_id in ("model-small-boundary", "model-large-boundary", "model-router-auto"):
            self.assertTrue(self.decision(case_id)["allowed"], case_id)
        self.assertEqual(self.decision("model-small-excess")["code"], "OUTPUT_LIMIT_EXCEEDED")
        self.assertFalse(self.decision("model-not-registered")["allowed"])

    def test_read_only_connection_and_embedding_do_not_require_generation_tokens(self):
        for case_id in ("get-connection", "embedding"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})

    def test_output_limit_is_optional_and_uses_provider_maximum_when_supplied(self):
        self.assertTrue(self.decision("missing-output-limit")["allowed"])
        for case_id, code in (("excess-output-limit", "OUTPUT_LIMIT_EXCEEDED"),):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": False, "status": 400, "code": code})

    def test_token_limit_type_and_alias_are_not_coerced(self):
        for case_id in ("zero-output-limit", "float-output-limit", "string-output-limit", "bool-output-limit", "alias-ambiguity", "chat-wrong-field"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id)["code"], "INVALID_OUTPUT_LIMIT")
                self.assertFalse(self.decision(case_id)["allowed"])

    def test_missing_or_incompatible_deployment_limits_fail_closed(self):
        for case_id in ("missing-config", "invalid-responses-alias"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.decision(case_id), {"allowed": False, "status": 500, "code": "LIMITS_NOT_CONFIGURED"})

    def test_real_utf8_bytes_and_exact_boundary(self):
        self.assertTrue(self.decision("byte-boundary")["allowed"])
        self.assertEqual(self.decision("multibyte-excess"), {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})

    def test_invalid_json_has_safe_error_without_input(self):
        self.assertEqual(self.decision("invalid-json"), {"allowed": False, "status": 400, "code": "INVALID_REQUEST"})

    def test_request_is_preserved_and_never_silently_rewritten(self):
        for case in CASES:
            with self.subTest(case_id=case["id"]):
                self.assertEqual(self.results[case["id"]]["bodyAfter"], case["body"])
                self.assertTrue(self.results[case["id"]]["preserved"])

    def test_rerank_rejects_invalid_types_blanks_and_cross_field_counts_before_upstream(self):
        for suffix in (*RERANK_REJECTIONS, "missing-query", "missing-documents", "missing-top_n"):
            with self.subTest(case=suffix):
                self.assertEqual(self.decision("rerank-" + suffix),
                                 {"allowed": False, "status": 400, "code": "INVALID_REQUEST"})

    def test_rerank_accepts_its_count_and_wire_byte_boundaries_without_clamping(self):
        for suffix in ("minimum", "maximum", "non-js-whitespace", "preserve-surrounding-space", "bytes-65536"):
            with self.subTest(case=suffix):
                self.assertEqual(self.decision("rerank-" + suffix),
                                 {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
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
        root = ET.parse(POLICIES / "gateway-telemetry.xml").getroot()
        self.assertEqual(root.find("trace").attrib, {"source": "core-gateway", "severity": "information"})
        for forbidden in ("context.Request.Body", "context.Response.Body", "context.Request.Headers", "context.Request.Url", "LastError.Message"):
            self.assertNotIn(forbidden, TELEMETRY_EXPRESSION)
        self.assertNotRegex(LIMIT_EXPRESSION, r"context\.Response\.Body|set-body|set-header")


if __name__ == "__main__":
    unittest.main()
