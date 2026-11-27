"""APIMのquery検査と記録項目を限定した観測をローカルで実行する。

実XMLから抽出した式で本文読取り、要求の維持、適用順を検証する。
APIMのURL解析や実Azureの下流呼出し停止を実測した結果とは区別する。
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_gateway_limits_telemetry import PWSH
from test_api_request_preparation import compile_preparation
from deployment.helpers.apim_api_request import render_api_request_preparation_fragment
from deployment.helpers.apim_audit import render_audit_policy
from deployment.helpers.apim_api_request import expand_api_request_preparation_fragment
from deployment.helpers.apim_query_observation import render_query_observation_fragment
from policy_fixture_paths import policy_fixture_path


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/api-gateway/policies"
PRIVATE = "SYNTHETIC_PRIVATE_QUERY_MARKER"
CONSTRAINTS = {
    "live-create": {"kind": "liveCreate", "bodyFormat": "json"},
    "realtime-create": {"kind": "realtimeCreate", "bodyFormat": "json"},
    "pii_redact_text": {"kind": "pii", "bodyFormat": "json"},
}
MODES = {name: "observe" for name in ("liveCreate", "realtimeCreate", "piiMcp")}
SHAPE_KEYS = {
    "route", "httpMethod", "presence", "duplicates",
}
RECORD_KEYS = {
    "event", "schemaVersion", "route", "httpMethod", "phase",
    "presence", "duplicates", "statusClass",
    "auditSchemaVersion", "requestId", "apiId", "operationId", "requestContext", "authenticatedSubject",
}


def expression(value):
    """実XMLの式をそのまま使い、Pythonで判定を再実装しない。"""
    value = (value or "").strip()
    if value.startswith("@{") and value.endswith("}"):
        return value[2:-1]
    if value.startswith("@(") and value.endswith(")"):
        return "return " + value[2:-1] + ";"
    raise AssertionError("Expected an actual APIM expression")


def mcp_rules(path, rule):
    """生成/Streamable MCPが公開する3メソッドの、独立した合成metadata。"""
    return {method + " " + path: rule for method in ("POST", "GET", "DELETE")}


def query_case(identifier, route="liveCreate", **patch):
    api, operation, template, path = {
        "liveCreate": ("openai-live-control-v1", "live-create", "/sessions", "/models/openai-live/v1/sessions"),
        "realtimeCreate": ("openai-realtime-control-v1", "realtime-create", "/calls", "/models/openai-realtime/v1/calls"),
        "piiMcp": ("pii-v1-mcp", "mcp", "/mcp", "/tools/pii-v1-mcp/mcp"),
    }[route]
    result = {
        "id": identifier, "api": api, "operation": operation,
        "template": template, "path": path, "method": "POST",
        "apiType": "mcp" if route == "piiMcp" else "http",
        "apiPath": path.rsplit("/", 1)[0].lstrip("/"),
        "queryObservations": {"live-create": "liveCreate", "realtime-create": "realtimeCreate"},
        "mcpQueryObservations": mcp_rules("/mcp", "piiMcp") if route == "piiMcp" else {},
        "authenticated": True, "query": {}, "constraints": copy.deepcopy(CONSTRAINTS),
        "body": '{"sdp":"v=0\\r\\ns=synthetic\\r\\n","text":"' + PRIVATE + '"}',
        "phase": "received", "responseStatus": 200,
    }
    result.update(patch)
    return result


SOURCE = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body {
 public string Data;
 public int Reads;
 public T As<T>(bool preserveContent=false) {
  Reads++;
  throw new Exception("SYNTHETIC_PRIVATE_BODY_READ");
 }
}
public class Url {
 public string Path;
 public bool ThrowOnQuery;
 public int QueryReads;
 public Dictionary<string,string[]> Values = new Dictionary<string,string[]>();
 public Dictionary<string,string[]> Query {
  get {
   QueryReads++;
   if (ThrowOnQuery) { throw new Exception("SYNTHETIC_PRIVATE_QUERY_EXCEPTION"); }
   return Values;
  }
 }
}
public class Named { public string Id; public string UrlTemplate; public string Path; }
public class Request {
 public string Method;
 public Body Body = new Body();
 public Url Url = new Url();
 public Url OriginalUrl = new Url();
 public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
}
public class Response { public int StatusCode; }
public class Context {
 public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 public Named Api = new Named();
 public Named Operation = new Named();
 public Named Subscription;
 public Request Request = new Request();
 public Response Response = new Response();
 public Dictionary<string,object> Variables = new Dictionary<string,object>();
 public string NamedQueryModes;
 public string Reason;
 public int TelemetryComputedStatus;
 public Dictionary<string,string[]> ResponseHeaders = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
}
public static class HeaderExtensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> values,string key,string fallback) {
  return values.ContainsKey(key) ? String.Join(",",values[key]) : fallback;
 }
}
public static class QueryFixture {
 static int Prepare(Context context) { __PREPARATION__ return 200; }
 static string Guard(Context context) { __GUARD__ }
 static string Record(Context context) { __RECORD__ }
 static bool Emit(Context context) { __EMIT__ }
 static Context CopyContext(Context c) {
  return new Context {
   RequestId = c.RequestId, Api = c.Api, Operation = c.Operation, NamedQueryModes = c.NamedQueryModes,
   Variables = new Dictionary<string,object>(c.Variables),
   Request = new Request { Method = c.Request.Method, Body = new Body { Data = c.Request.Body.Data },
    OriginalUrl = new Url { Path = c.Request.OriginalUrl.Path },
    Url = new Url { Path = c.Request.Url.Path, ThrowOnQuery = c.Request.Url.ThrowOnQuery,
     Values = new Dictionary<string,string[]>(c.Request.Url.Values) },
    Headers = new Dictionary<string,string[]>(c.Request.Headers, StringComparer.OrdinalIgnoreCase) }
  };
 }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Api.Id = (string)item["api"];
   c.Api.Path = (string)item["apiPath"];
   if ((bool?)item["nullApi"] == true) { c.Api = null; }
   c.Operation.Id = (string)item["operation"];
   c.Operation.UrlTemplate = (string)item["template"];
   if ((bool?)item["nullOperation"] == true) { c.Operation = null; }
   c.Request.Method = (string)item["method"];
   c.Request.OriginalUrl.Path = (string)item["path"];
   c.Request.Url.Path = (string)item["path"];
   c.Request.Body.Data = (string)item["body"];
   c.Request.Url.ThrowOnQuery = (bool?)item["queryThrows"] == true;
   foreach (var p in ((JObject)item["query"]).Properties()) {
    c.Request.Url.Values[p.Name] = p.Value.Type == JTokenType.Null ? null :
     p.Value is JArray ? p.Value.Select(v => (string)v).ToArray() : new [] {(string)p.Value};
   }
   c.Request.Headers["User-Agent"] = new [] {"SYNTHETIC_PRIVATE_USER_AGENT"};
   c.Request.Headers["api-key"] = new [] {"SYNTHETIC_PRIVATE_KEY"};
   if (item["authenticated"] != null && item["authenticated"].Type != JTokenType.Null) {
    c.Variables["gatewayCommonAuthenticationValidated"] = (bool)item["authenticated"];
   }
   c.Variables["gatewayControlSettings"] = new JObject(
    new JProperty("operationConstraints", item["constraints"]),
    new JProperty("queryObservations", item["queryObservations"]),
    new JProperty("mcpQueryObservations", item["mcpQueryObservations"]),
    new JProperty("callerId", "SYNTHETIC_PRIVATE_CALLER"),
    new JProperty("subscriptionId", "SYNTHETIC_PRIVATE_SUBSCRIPTION")
   ).ToString(Formatting.None);
   c.Variables["gatewayApiSettings"] = (string)item["apiType"] + "\n{}\n" + (string)c.Variables["gatewayControlSettings"];
   if (item["auditRaw"] != null) { c.Variables["gatewayControlSettings"] = (string)item["auditRaw"]; }
   if ((bool?)item["legacySettings"] == true) {
    c.Variables["gatewayAuditSettings"] = c.Variables["gatewayControlSettings"];
    c.Variables.Remove("gatewayControlSettings");
   }
   if (item.Property("controlRaw") != null) { c.Variables["gatewayControlSettings"] =
     item["controlRaw"].Type == JTokenType.Null ? null : (string)item["controlRaw"]; }
   c.NamedQueryModes = "{}";
   if (item["modes"] != null) {
    c.NamedQueryModes = item["modes"].Type == JTokenType.String ?
     (string)item["modes"] : item["modes"].ToString(Formatting.None);
   }
   if (item["legacyModes"] != null) { c.Variables["gatewayQueryGuardModes"] = item["legacyModes"].ToString(Formatting.None); }
   c.Variables["gatewayQueryPhase"] = (string)item["phase"];
   c.Response.StatusCode = (int)item["responseStatus"];
   if ((bool?)item["omitAuditStatus"] != true) { c.Variables["gatewayAuditStatus"] = (int)item["responseStatus"]; }
   var pipeline = CopyContext(c);
   var preparedStatus = Prepare(pipeline);
   var preparedDecision = preparedStatus == 200 ? JObject.Parse(Guard(pipeline)) : null;
   var decision = JObject.Parse(Guard(c));
   var originalDecision = decision.DeepClone();
   var preparedEquivalent = preparedDecision != null && JToken.DeepEquals(preparedDecision, decision);
   c.Variables["gatewayQueryObservation"] = decision.ToString(Formatting.None);
   if (item["recordPatch"] != null) {
    foreach (var p in ((JObject)item["recordPatch"]).Properties()) { decision[p.Name] = p.Value; }
    c.Variables["gatewayQueryObservation"] = decision.ToString(Formatting.None);
   }
   if (item["recordRaw"] != null) { c.Variables["gatewayQueryObservation"] = (string)item["recordRaw"]; }
   var record = (bool?)item["directRecord"] == true || Emit(c) ? Record(c) : "";
   results[(string)item["id"]] = new JObject(
    new JProperty("decision", decision), new JProperty("record", record),
    new JProperty("originalDecision", originalDecision),
    new JProperty("telemetryComputedStatus", c.TelemetryComputedStatus),
    new JProperty("bodyReads", c.Request.Body.Reads),
    new JProperty("bodyAfter", c.Request.Body.Data),
    new JProperty("queryAfter", JObject.FromObject(c.Request.Url.Values)),
    new JProperty("queryReads", c.Request.Url.QueryReads),
    new JProperty("responseStatus", c.Response.StatusCode),
    new JProperty("preparedStatus", preparedStatus), new JProperty("preparedQueryReads", pipeline.Request.Url.QueryReads),
    new JProperty("preparedEquivalent", preparedEquivalent)
   );
  }
  return results.ToString(Formatting.None);
 }
}
'''


class GatewayQueryExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell Roslyn is required; no Azure checks were performed")
        guard = ET.fromstring(render_query_observation_fragment())
        telemetry = ET.fromstring(render_audit_policy((POLICIES / "fragments/audit/core-query-telemetry/core-query-telemetry.xml").read_text(encoding="utf-8")))
        current_body = expression(guard.find("./set-variable[@name='gatewayQueryObservation']").get("value"))
        source = SOURCE.replace("__GUARD__", current_body)
        source = source.replace("__PREPARATION__", compile_preparation(ET.fromstring(render_api_request_preparation_fragment())))
        source = source.replace("__RECORD__", expression(telemetry.find("./choose/when/trace/message").text))
        source = source.replace("__EMIT__", expression(telemetry.find("./choose/when").get("condition")))
        cases = []
        cls.route_cases = {}
        cls.invalid_settings = set()
        for route in MODES:
            methods = ("POST", "GET", "DELETE") if route == "piiMcp" else ("POST",)
            for method in methods:
                prefix = route + "-" + method
                cls.route_cases[prefix] = (route, method)
                base = {"method": method}
                cases.append(query_case(prefix + "-default", route, **base))
                cases.append(query_case(prefix + "-revision", route, **base,
                                        api=query_case("unused", route)["api"] + ";rev=2"))
                variants = {
                    "single": {"synthetic": "value"},
                    "same-name": {"sdp" if route != "piiMcp" else "params.arguments.text": PRIVATE},
                    "empty-name": {"": ""},
                    "null-values": {"synthetic": None},
                    "empty-values": {"synthetic": []},
                    "duplicate": {"synthetic": ["one", "two"]},
                    "four": {"a": "1", "b": "2", "c": "3", "d": "4"},
                    "five": {str(n): str(n) for n in range(5)},
                    "late-duplicate": {**{str(n): str(n) for n in range(10)}, "tail": ["one", "two"]},
                    "private": {PRIVATE + "\r\n%61": PRIVATE + "\r\n%3F"},
                }
                for variant, query in variants.items():
                    cases.append(query_case(prefix + "-observe-" + variant, route, **base, query=query))
                    cases.append(query_case(prefix + "-reject-" + variant, route, **base,
                                            modes={**MODES, route: "reject"}, query=query, body="{"))
                cases.append(query_case(prefix + "-reject-empty", route, **base, modes={**MODES, route: "reject"}))
                for mode in ("observe", "reject"):
                    cases.append(query_case(prefix + "-" + mode + "-query-error", route, **base,
                                            modes={**MODES, route: mode}, queryThrows=True))
                for suffix, modes in (
                    ("invalid-mode", {**MODES, route: PRIVATE}),
                    ("invalid-mode-type", {**MODES, route: 1}),
                    ("invalid-mode-null", {**MODES, route: None}),
                    ("invalid-mode-boolean", {**MODES, route: True}),
                    ("invalid-mode-array", {**MODES, route: []}),
                    ("invalid-mode-object", {**MODES, route: {}}),
                    ("invalid-modes-json", "{"),
                    ("invalid-modes-array", "[]"),
                    ("invalid-modes-scalar", "42"),
                ):
                    identifier = prefix + "-" + suffix
                    cls.invalid_settings.add(identifier)
                    cases.append(query_case(identifier, route, **base, modes=modes, body="{", queryThrows=True))
                cases.append(query_case(prefix + "-unauthenticated", route, **base, authenticated=False, queryThrows=True))
                cases.append(query_case(prefix + "-missing-authentication", route, **base, authenticated=None, queryThrows=True))
        cases.append(query_case("mcp-trailing-slash", "piiMcp", path="/tools/pii-v1-mcp/mcp/", method="DELETE"))
        for method in ("GET", "DELETE"):
            cases.append(query_case("mcp-null-operation-" + method, "piiMcp", method=method, nullOperation=True))
            cases.append(query_case("mcp-null-operation-reject-" + method, "piiMcp", method=method,
                                    nullOperation=True, modes={**MODES, "piiMcp": "reject"}, query={"params": PRIVATE}))
        cls.renamed = []
        for route in MODES:
            methods = ("POST", "GET", "DELETE") if route == "piiMcp" else ("POST",)
            changes = ("api",) if route == "piiMcp" else ("api", "operation", "both")
            for method in methods:
                for change in changes:
                    for revision in (False, True):
                        for present in (False, True):
                            identifier = f"renamed-{route}-{method}-{change}-{revision}-{present}"
                            case = query_case(identifier, route, method=method,
                                              modes={**MODES, route: "reject"},
                                              query={"synthetic": "value"} if present else {})
                            if change in ("api", "both"):
                                case["api"] = "synthetic-renamed-api"
                            if revision:
                                case["api"] += ";rev=2"
                            if change in ("operation", "both"):
                                constraint = case["constraints"].pop(case["operation"])
                                rule = case["queryObservations"].pop(case["operation"])
                                case["operation"] = "synthetic-renamed-create"
                                case["constraints"][case["operation"]] = constraint
                                case["queryObservations"][case["operation"]] = rule
                            cls.renamed.append((identifier, route, method, present))
                            cases.append(case)
        cls.unrelated = []
        for label, patch in (
            ("other-api", {"api": "unrelated-api", "path": "/models/synthetic/v1/sessions", "queryObservations": {}}),
            ("other-operation", {"operation": "another-operation"}),
            ("other-template", {"template": "/other", "queryObservations": {}}),
            ("other-method", {"method": "GET", "queryObservations": {}}),
            ("http-null-operation", {"nullOperation": True}),
            ("voice-live", {"api": "azure-voice-live-webrtc-v1", "apiType": "websocket", "path": "/models/azure-voice-live/v1/realtime", "query": {"api-version": "fixed", "model": "synthetic-model"}}),
            ("embedding", {"api": "openai-embeddings-v1", "path": "/models/openai-embeddings/v1/embeddings", "queryObservations": {}, "query": {"api-version": "v1"}}),
            ("observer", {"api": "openai-realtime-observer-v1", "apiType": "websocket", "path": "/models/openai-realtime-observer/v1/realtime", "query": {"call_id": "rtc_synthetic"}}),
            ("legacy-mcp", {"api": "synthetic-legacy-mcp", "path": "/tools/synthetic-legacy-mcp/mcp", "queryObservations": {}, "query": {"session_id": "SYNTHETIC_SESSION"}}),
        ):
            cls.unrelated.append(label)
            cases.append(query_case(label, query={"provider-option": PRIVATE}, **patch) if "query" not in patch else query_case(label, **patch))
        for label, patch in (
            ("mcp-other-api", {"api": "other-mcp", "path": "/tools/synthetic-other/mcp"}),
            ("mcp-other-path", {"path": "/tools/pii-v1-mcp/other"}),
            ("mcp-nested-path", {"path": "/tools/pii-v1-mcp/mcp/extra"}),
            ("mcp-other-method", {"method": "PATCH"}),
        ):
            cls.unrelated.append(label)
            cases.append(query_case(label, "piiMcp", query={"params": PRIVATE}, **patch))
        for route in MODES:
            original_path = query_case("unused", route)["path"]
            boundaries = [
                ("other-public-path", {"path": "/synthetic" + original_path}),
                ("nested-public-path", {"path": original_path + "/extra"}),
            ]
            if route != "piiMcp":
                boundaries.append(("other-selected-template", {"template": "/other"}))
            for suffix, patch in boundaries:
                if route != "piiMcp":
                    patch["queryObservations"] = {}
                label = route + "-" + suffix
                cls.unrelated.append(label)
                cases.append(query_case(label, route, query={"synthetic": "value"}, **patch))
        for route, operation in (("liveCreate", "live-create"), ("realtimeCreate", "realtime-create"), ("piiMcp", "pii_redact_text")):
            constraints = copy.deepcopy(CONSTRAINTS)
            constraints[operation]["kind"] = "unrelated-kind"
            label = route + "-other-kind"
            cls.unrelated.append(label)
            cases.append(query_case(label, route, constraints=constraints, queryObservations={}, mcpQueryObservations={}, query={"sdp": PRIVATE}))
        for case in cases:
            if case["id"] in cls.unrelated:
                case["modes"] = {route: "reject" for route in MODES}
        for route in MODES:
            cases.append(query_case(route + "-missing-nv-route", route, query={"synthetic": "value"},
                                    modes={name: "reject" for name in MODES if name != route}))
            cases.append(query_case(route + "-shared-nv-overrides-old-seed", route, query={"synthetic": "value"},
                                    modes={name: "reject" for name in MODES}, legacyModes=MODES))
            cases.append(query_case(route + "-null-api", route, nullApi=True, modes={name: "reject" for name in MODES}, queryThrows=True))
        cases.append(query_case("unmatched-invalid-nv", path="/models/synthetic/v1/other", queryObservations={}, modes="{", queryThrows=True))
        cls.phases = ("received", "query-rejected", "guard-error", "backend-response", "policy-error")
        for phase in cls.phases:
            cases.append(query_case("phase-" + phase, phase=phase, responseStatus=503 if phase == "policy-error" else 200))
        cases.append(query_case("untrusted-record", phase=PRIVATE, directRecord=True, recordPatch={
            "route": PRIVATE + "\r\n", "httpMethod": PRIVATE, "mode": PRIVATE,
            "presence": PRIVATE, "countBucket": PRIVATE, "duplicates": PRIVATE,
            "unexpected": PRIVATE, "callerId": PRIVATE,
        }))
        cases.append(query_case("invalid-record-json", recordRaw="{", directRecord=True))
        cases.append(query_case("invalid-record-types", directRecord=True, recordPatch={"httpMethod": {"private": PRIVATE}}))
        cases.append(query_case("record-extra-fields", recordPatch={"unexpected": PRIVATE, "callerId": PRIVATE, "countBucket": PRIVATE}))
        for status in (0, 199, 200, 299, 300, 399, 400, 499, 500, 599, 600):
            cases.append(query_case("status-" + str(status), phase="backend-response", responseStatus=status))
        cases.append(query_case("status-not-confirmed", phase="backend-response", omitAuditStatus=True))
        # ③で承認した追加範囲。旧固定3入口との比較対象と混ぜず、設定だけで追加できることを検証します。
        cls.generic = {}
        def generic(identifier, rule, *, api_type="http", mode="observe", **patch):
            defaults = {"api": "synthetic-custom-api", "operation": "custom-operation", "apiType": api_type,
                        "apiPath": "tools/custom", "path": "/tools/custom/invoke", "template": "/invoke",
                        "queryObservations": {"custom-operation": rule} if api_type == "http" else {},
                        "mcpQueryObservations": mcp_rules("/mcp", rule) if api_type == "mcp" and rule else {},
                        "constraints": {"custom-operation": {"kind": "registeredOperation"}},
                        "query": {"synthetic": "value"}, "modes": {**MODES, rule: mode} if rule else MODES}
            defaults.update(patch)
            case = query_case(identifier, **defaults)
            cases.append(case)
            cls.generic[identifier] = (rule or "none", case["method"], mode, bool(case["query"]))
        for model in ("vendor-messages", "claude-messages"):
            for method in ("POST", "GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                generic(model + "-" + method, "vendorMessages", method=method,
                        apiPath="models/" + model, path="/models/" + model + "/messages", template="/messages")
        for method in ("GET", "POST", "DELETE"):
            for trailing in (False, True):
                for tool in ("box", "workiq", "internal-service"):
                    rule = tool.replace("-", "") + "Query"
                    generic(f"{tool}-mcp-{method}-{trailing}", rule, api_type="mcp", method=method,
                            apiPath="tools/" + tool, path="/tools/" + tool + "/mcp" + ("/" if trailing else ""),
                            nullOperation=True, mode="reject")
        generic("custom-rule-64-characters", "x" * 64, mode="reject")
        generic("http-operation-is-independent-of-kind", "customRequest", constraints={})
        generic("http-selected-operation-is-independent-of-public-path", "customRequest", path="/another/public/prefix", template="/other")
        generic("mcp-empty-api-suffix", "rootMcp", api_type="mcp", apiPath="", path="/mcp")
        for method in ("POST", "GET", "DELETE"):
            generic("native-workiq-arbitrary-path-" + method, "workiqSearch", api_type="mcp", method=method,
                    path="/tools/custom/workiq/search", mcpQueryObservations={method + " /workiq/search": "workiqSearch"}, nullOperation=True)
            generic("native-box-arbitrary-path-" + method, "boxBrowse", api_type="mcp", method=method,
                    path="/tools/custom/box/browse", mcpQueryObservations={method + " /box/browse": "boxBrowse"}, mode="reject")
        for method, rule in (("GET", "customEvents"), ("POST", "customMessage")):
            generic("native-mcp-same-path-different-method-" + method, rule, api_type="mcp", method=method,
                    path="/tools/custom/sse", mcpQueryObservations={"GET /sse": "customEvents", "POST /sse": "customMessage"})
        generic("custom-rule-missing-nv-entry", "customRequest", modes=MODES)
        generic("custom-rule-ignores-other-rules-invalid-mode", "customRequest",
                modes={"customRequest": "observe", "anotherRule": True})
        for label, modes in (("json", "{"), ("mode", {"customRequest": "disabled"}),
                             ("type", {"customRequest": False}), ("null", {"customRequest": None})):
            identifier = "custom-rule-invalid-configuration-" + label
            generic(identifier, "customRequest", mode="unknown", modes=modes, queryThrows=True)
            cls.invalid_settings.add(identifier)
        generic("http-unconfigured-pii-rest", "", operation="pii_redact_text", apiPath="tools/pii/v1", path="/tools/pii/v1/redact", template="/redact")
        generic("http-unconfigured-mcp-shaped-path", "", path="/tools/custom/mcp", template="/mcp")
        generic("http-unconfigured-other-operation", "", queryObservations={"another-operation": "customRequest"})
        for api_type in ("websocket", "grpc", "unrecognized", ""):
            generic("unrecognized-type-" + (api_type or "empty"), "", api_type=api_type,
                    queryObservations={"custom-operation": "customRequest"}, mcpQueryObservations=mcp_rules("/mcp", "customMcp"))
        for label, patch in (("other-path", {"path": "/tools/custom/other"}),
                             ("nested-path", {"path": "/tools/custom/mcp/extra"}),
                             ("prefix-boundary", {"path": "/tools/custom-extra/mcp"}),
                             ("other-method", {"path": "/tools/custom/mcp", "method": "PATCH"})):
            generic("custom-mcp-" + label, "", api_type="mcp", mcpQueryObservations=mcp_rules("/mcp", "customMcp"), **patch)
        for label, value in (("null", None), ("number", 1), ("boolean", True), ("array", []), ("object", {}),
                             ("blank", ""), ("overlong", "x" * 65), ("non-ascii", "ルール"),
                             ("control", "rule\n"), ("reserved-none", "none"), ("reserved-unknown", "unknown")):
            generic("http-invalid-rule-" + label, "", queryObservations={"custom-operation": value})
            generic("mcp-invalid-rule-" + label, "", api_type="mcp", path="/tools/custom/mcp", mcpQueryObservations=mcp_rules("/mcp", value))
        generic("http-null-operation-custom", "", queryObservations={"custom-operation": "customRequest"}, nullOperation=True)
        cls.rolling_cases=[]
        for route in MODES:
            name='rolling-'+route
            cases.append(query_case(name,route,modes={**MODES,route:'reject'},query={'synthetic':'value'}))
            cases.append(query_case(name+'-legacy-mixed',route,modes={**MODES,route:'reject'},query={'synthetic':'value'},legacySettings=True))
            for label, raw in (('malformed', '{broken'), ('null', None), ('blank', ''), ('array', '[]'), ('scalar', '42')):
                cases.append(query_case(name+'-'+label+'-new',route,modes={**MODES,route:'reject'},query={'synthetic':'value'},legacySettings=True,controlRaw=raw))
            cls.rolling_cases.append(name)
        cls.cases = {item["id"]: item for item in cases}
        with tempfile.TemporaryDirectory(prefix="core-query-policy-") as directory:
            folder = Path(directory)
            (folder / "fixture.cs").write_text(source, encoding="utf-8")
            (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            (folder / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[QueryFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(folder / "run.ps1")],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(folder / "dotnet")},
            )
            if result.returncode:
                raise AssertionError("Actual query policy execution failed:\n" + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_registered_standard_entrances_and_revisions_collect_only_observation(self):
        for prefix, (route, method) in self.route_cases.items():
            for suffix in ("default", "revision", "reject-empty"):
                result=self.results[prefix+"-"+suffix]
                self.assertEqual(result["originalDecision"], {"route":route,"httpMethod":method,"presence":"none","duplicates":"no"})
        self.assertEqual(self.results["mcp-trailing-slash"]["originalDecision"]["route"], "piiMcp")

    def test_query_presence_and_duplicates_do_not_reject_or_expose_values(self):
        for prefix in self.route_cases:
            for mode in ("observe", "reject"):
                for suffix, duplicate in (("single","no"),("same-name","no"),("empty-name","no"),("duplicate","yes"),
                                          ("null-values","no"),("empty-values","no"),("four","no"),("five","no"),("late-duplicate","yes"),("private","no")):
                    result=self.results[prefix+"-"+mode+"-"+suffix]
                    self.assertEqual(result["originalDecision"]["presence"], "present")
                    self.assertEqual(result["originalDecision"]["duplicates"], duplicate)
                    self.assertEqual(result["responseStatus"], self.cases[prefix+"-"+mode+"-"+suffix]["responseStatus"])

    def test_retired_modes_are_not_read_and_inspection_failure_is_only_unknown(self):
        for prefix in self.route_cases:
            for mode in ("observe","reject"):
                result=self.results[prefix+"-"+mode+"-query-error"]
                self.assertEqual((result["originalDecision"]["presence"],result["originalDecision"]["duplicates"]),("unknown","unknown"))
            for identifier in self.invalid_settings:
                result=self.results[identifier]
                self.assertEqual(result["responseStatus"],self.cases[identifier]["responseStatus"])
                self.assertEqual(result["queryReads"],1)
        for route in MODES:
            result=self.results[route+"-shared-nv-overrides-old-seed"]
            self.assertEqual(result["originalDecision"]["presence"],"present")
            self.assertEqual(result["responseStatus"],200)

    def test_generic_registration_selects_http_and_mcp_without_service_specific_code(self):
        for identifier,(route,method,old_mode,present) in self.generic.items():
            result=self.results[identifier]
            self.assertEqual(result["originalDecision"]["route"],route,identifier)
            self.assertEqual(result["queryReads"],0 if route=="none" else 1,identifier)
            if route!="none": self.assertEqual(result["originalDecision"]["httpMethod"],method)
        for identifier,route,method,present in self.renamed:
            result=self.results[identifier]
            self.assertEqual(result["originalDecision"]["route"],route)
            self.assertEqual(result["originalDecision"]["presence"],"present" if present else "none")

    def test_current_control_settings_are_required_and_unselected_entrances_are_not_read(self):
        for name in self.rolling_cases:
            for suffix in ('legacy-mixed',*(part+'-new' for part in ('malformed','null','blank','array','scalar'))):
                result=self.results[name+'-'+suffix]
                self.assertEqual(result["originalDecision"]["route"],"none")
                self.assertEqual(result["queryReads"],0)
        for identifier in self.unrelated:
            self.assertEqual(self.results[identifier]["originalDecision"]["route"],"none",identifier)
            self.assertEqual(self.results[identifier]["queryReads"],0,identifier)

    def test_preparation_stops_unverified_requests_without_duplicate_authentication_in_observer(self):
        for prefix in self.route_cases:
            for suffix in ("unauthenticated","missing-authentication"):
                result=self.results[prefix+"-"+suffix]
                self.assertNotEqual(result["originalDecision"]["route"],"none")
                self.assertEqual(result["preparedStatus"],503)
                self.assertEqual(result["preparedQueryReads"],0)

    def test_all_cases_preserve_inputs_and_responses(self):
        for identifier,result in self.results.items():
            item=self.cases[identifier]
            self.assertEqual(result["bodyReads"],0,identifier)
            self.assertEqual(set(result["originalDecision"]),SHAPE_KEYS,identifier)
            self.assertEqual(result["bodyAfter"],item["body"],identifier)
            self.assertEqual(result["queryAfter"],{name:value if value is None or isinstance(value,list) else [value] for name,value in item["query"].items()},identifier)
            self.assertNotIn(PRIVATE,json.dumps(result["originalDecision"]),identifier)
            if item.get("authenticated") is True:
                self.assertEqual(result["preparedStatus"],200,identifier)
                self.assertTrue(result["preparedEquivalent"],identifier)

    def test_telemetry_fixed_schema_does_not_output_retired_fields_or_sensitive_data(self):
        for identifier,result in self.results.items():
            if not result["record"]: continue
            record=json.loads(result["record"])
            self.assertEqual(set(record),RECORD_KEYS,identifier)
            self.assertEqual((record["event"],record["schemaVersion"],record["auditSchemaVersion"]),("query-guard",2,2))
            self.assertNotIn(PRIVATE,result["record"])
            for key,allowed in {"httpMethod":{"POST","GET","PUT","PATCH","DELETE","HEAD","OPTIONS","unknown"},
                                "phase":{"received","backend-response","policy-error","unknown"},
                                "presence":{"none","present","unknown"},"duplicates":{"yes","no","unknown"},
                                "statusClass":{"2xx","3xx","4xx","5xx","unknown"}}.items():
                self.assertIn(record[key],allowed,(identifier,key))
        for phase in ("query-rejected","guard-error"):
            self.assertEqual(json.loads(self.results["phase-"+phase]["record"])["phase"],"unknown")

    def test_untrusted_records_and_http_status_classes_do_not_claim_model_success(self):
        for identifier in ("untrusted-record","invalid-record-json","invalid-record-types"):
            record=json.loads(self.results[identifier]["record"])
            for key in ("route","httpMethod","phase","presence","duplicates","statusClass"):
                self.assertEqual(record[key],"unknown",(identifier,key))
        for status in (0,199,200,299,300,399,400,499,500,599,600):
            record=json.loads(self.results["status-"+str(status)]["record"])
            self.assertEqual(record["statusClass"],str(status//100)+"xx" if 200<=status<600 else "unknown")
            self.assertNotIn("success",record)
        self.assertEqual(json.loads(self.results["status-not-confirmed"]["record"])["statusClass"],"unknown")


class GatewayQueryStructureTests(unittest.TestCase):
    def test_guard_is_after_authentication_and_before_parent_body_processing(self):
        for filename, body_boundary in (("rest.xml", "core-model-limits"), ("generated.xml", "gatewayMcpArgumentError")):
            with self.subTest(policy=filename):
                policy = ET.fromstring(expand_api_request_preparation_fragment(
                    policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")))
                inbound = list(policy.find("./inbound"))
                guard_index = next(i for i, node in enumerate(inbound)
                                   if node.tag == "include-fragment" and node.get("fragment-id") == "core-query-observation")
                base_index = next(i for i, node in enumerate(inbound) if node.tag == "base")
                settings_index = next(i for i, node in enumerate(inbound)
                                      if node.tag == "include-fragment" and node.get("fragment-id") == "core-api-settings")
                source_index = next(i for i, node in enumerate(inbound)
                                    if node.tag == "set-variable" and node.get("name") == "gatewayApiSettingsSource")
                self.assertEqual(settings_index, source_index + 1)
                self.assertLess(settings_index, base_index)
                auth_index = next(i for i, node in enumerate(inbound)
                                  if node.tag == "choose" and "gatewayCommonAuthenticationValidated" in ET.tostring(node, encoding="unicode"))
                boundary_index = next(i for i, node in enumerate(inbound)
                                      if node.get("fragment-id") == body_boundary or node.get("name") == body_boundary)
                self.assertLess(base_index, guard_index)
                self.assertLess(auth_index, guard_index)
                self.assertEqual(inbound[auth_index].find("./when/return-response/set-status").get("code"), "503")
                self.assertLess(guard_index, boundary_index)
                for node in inbound[:guard_index]:
                    self.assertNotIn("context.Request.Body", ET.tostring(node, encoding="unicode"))
                if filename == "generated.xml":
                    handoff = next(i for i, node in enumerate(inbound)
                                   if node.tag == "choose" and "gatewayDecision" in node.find("when").get("condition", ""))
                    self.assertLess(handoff, guard_index)
                    self.assertEqual(inbound[handoff].find("./when/return-response/set-status").get("code"), "500")

    def test_observation_is_a_leaf_and_no_query_rejection_or_modes_remain_in_callers(self):
        observation=ET.fromstring(render_query_observation_fragment())
        self.assertEqual([node.tag for node in observation],["set-variable"])
        self.assertEqual(observation[0].get("name"),"gatewayQueryObservation")
        for token in ("context.Request.Body","gatewayQueryGuardModes",'result["allowed"]','result["status"]'):
            self.assertNotIn(token,observation[0].get("value"))
        for filename in ("rest.xml", "generated.xml", "query-observation-inbound.xml"):
            path = policy_fixture_path(POLICIES, filename)
            root=ET.parse(path).getroot()
            self.assertIsNone(root.find(".//choose[@id='core-query-rejection']"))
            self.assertNotIn("QUERY_PARAMETERS_NOT_ALLOWED",ET.tostring(root,encoding="unicode"))
            self.assertNotIn("QUERY_GUARD_NOT_CONFIGURED",ET.tostring(root,encoding="unicode"))


if __name__ == "__main__":
    unittest.main()
