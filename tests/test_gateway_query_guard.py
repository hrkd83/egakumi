"""APIMのquery検査と記録項目を限定した観測をローカルで実行する。

実XMLから抽出した式で本文読取り、要求の維持、適用順を検証する。
APIMのURL解析や実Azureの下流呼出し停止を実測した結果とは区別する。
"""
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep
from test_gateway_limits_telemetry import PWSH
from deployment.helpers.apim_audit import render_audit_policy


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
PRIVATE = "SYNTHETIC_PRIVATE_QUERY_MARKER"
CONSTRAINTS = {
    "live-create": {"kind": "liveCreate", "bodyFormat": "json"},
    "realtime-create": {"kind": "realtimeCreate", "bodyFormat": "json"},
    "pii_redact_text": {"kind": "pii", "bodyFormat": "json"},
}
MODES = {name: "observe" for name in ("liveCreate", "realtimeCreate", "piiMcp")}
SHAPE_KEYS = {
    "route", "mode", "httpMethod", "presence", "countBucket", "duplicates",
    "allowed", "status",
}
RECORD_KEYS = {
    "event", "schemaVersion", "route", "httpMethod", "mode", "phase",
    "presence", "countBucket", "duplicates", "statusClass",
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


def query_case(identifier, route="liveCreate", **patch):
    api, operation, template, path = {
        "liveCreate": ("openai-live-control-v1", "live-create", "/sessions", "/models/openai-live/v1/sessions"),
        "realtimeCreate": ("openai-realtime-control-v1", "realtime-create", "/calls", "/models/openai-realtime/v1/calls"),
        "piiMcp": ("pii-v1-mcp", "mcp", "/mcp", "/tools/pii-v1-mcp/mcp"),
    }[route]
    result = {
        "id": identifier, "api": api, "operation": operation,
        "template": template, "path": path, "method": "POST",
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
public class Named { public string Id; public string UrlTemplate; }
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
}
public static class HeaderExtensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> values,string key,string fallback) {
  return values.ContainsKey(key) ? String.Join(",",values[key]) : fallback;
 }
}
public static class QueryFixture {
 static string Guard(Context context) { __GUARD__ }
 static string Record(Context context) { __RECORD__ }
 static bool Emit(Context context) { __EMIT__ }
 static bool Rejected(Context context) { __REJECTED__ }
 static int RejectionStatus(Context context) { __REJECTION_STATUS__ }
 static int McpRejectionStatus(Context context) { __MCP_REJECTION_STATUS__ }
 static string PublicError(Context context) { __PUBLIC_ERROR__ }
 static string McpPublicError(Context context) { __MCP_PUBLIC_ERROR__ }
 static string RejectedPhase(Context context) { __REJECTED_PHASE__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Api.Id = (string)item["api"];
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
    new JProperty("callerId", "SYNTHETIC_PRIVATE_CALLER"),
    new JProperty("subscriptionId", "SYNTHETIC_PRIVATE_SUBSCRIPTION")
   ).ToString(Formatting.None);
   if (item["auditRaw"] != null) { c.Variables["gatewayControlSettings"] = (string)item["auditRaw"]; }
   if ((bool?)item["legacySettings"] == true) {
    c.Variables["gatewayAuditSettings"] = c.Variables["gatewayControlSettings"];
    c.Variables.Remove("gatewayControlSettings");
   }
   if (item.Property("controlRaw") != null) { c.Variables["gatewayControlSettings"] =
     item["controlRaw"].Type == JTokenType.Null ? null : (string)item["controlRaw"]; }
   if (item["modes"] != null) {
    c.Variables["gatewayQueryGuardModes"] = item["modes"].Type == JTokenType.String ?
     (string)item["modes"] : item["modes"].ToString(Formatting.None);
   }
   c.Variables["gatewayQueryPhase"] = (string)item["phase"];
   c.Response.StatusCode = (int)item["responseStatus"];
   if ((bool?)item["omitAuditStatus"] != true) { c.Variables["gatewayAuditStatus"] = (int)item["responseStatus"]; }
   var decision = JObject.Parse(Guard(c));
   c.Variables["gatewayQueryGuardDecision"] = decision.ToString(Formatting.None);
   var rejected = Rejected(c);
   var responseStatus = rejected ? RejectionStatus(c) : 0;
   var mcpStatus = rejected ? McpRejectionStatus(c) : 0;
   var publicError = rejected ? PublicError(c) : "";
   var mcpPublicError = rejected ? McpPublicError(c) : "";
   if (rejected) { c.Variables["gatewayQueryPhase"] = RejectedPhase(c); }
   if (item["recordPatch"] != null) {
    foreach (var p in ((JObject)item["recordPatch"]).Properties()) { decision[p.Name] = p.Value; }
    c.Variables["gatewayQueryGuardDecision"] = decision.ToString(Formatting.None);
   }
   if (item["recordRaw"] != null) { c.Variables["gatewayQueryGuardDecision"] = (string)item["recordRaw"]; }
   var record = (bool?)item["directRecord"] == true || Emit(c) ? Record(c) : "";
   results[(string)item["id"]] = new JObject(
    new JProperty("decision", decision), new JProperty("record", record),
    new JProperty("bodyReads", c.Request.Body.Reads),
    new JProperty("bodyAfter", c.Request.Body.Data),
    new JProperty("queryAfter", JObject.FromObject(c.Request.Url.Values)),
    new JProperty("queryReads", c.Request.Url.QueryReads),
    new JProperty("rejected", rejected), new JProperty("responseStatus", responseStatus),
    new JProperty("mcpResponseStatus", mcpStatus), new JProperty("publicError", publicError),
    new JProperty("mcpPublicError", mcpPublicError)
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
        guard = ET.parse(POLICIES / "query-guard.xml").getroot()
        telemetry = ET.fromstring(render_audit_policy((POLICIES / "query-telemetry.xml").read_text(encoding="utf-8")))
        rejection = ET.parse(POLICIES / "gateway.xml").find("./inbound/choose[@id='core-query-rejection']/when")
        mcp_rejection = ET.parse(POLICIES / "mcp.xml").find("./inbound/choose[@id='core-query-rejection']/when")
        if rejection is None:
            raise AssertionError("The query guard must have an actual early response")
        status = rejection.find("./return-response/set-status").get("code")
        status_body = "return " + status + ";" if status.isdecimal() else expression(status)
        source = SOURCE.replace("__GUARD__", expression(guard.find("./set-variable[@name='gatewayQueryGuardDecision']").get("value")))
        source = source.replace("__RECORD__", expression(telemetry.find("./choose/when/trace/message").text))
        source = source.replace("__EMIT__", expression(telemetry.find("./choose/when").get("condition")))
        source = source.replace("__REJECTED__", expression(rejection.get("condition")))
        source = source.replace("__REJECTION_STATUS__", status_body)
        source = source.replace("__MCP_REJECTION_STATUS__", expression(mcp_rejection.find("./return-response/set-status").get("code")))
        source = source.replace("__PUBLIC_ERROR__", expression(rejection.find("./return-response/set-body").text))
        source = source.replace("__MCP_PUBLIC_ERROR__", expression(mcp_rejection.find("./return-response/set-body").text))
        source = source.replace("__REJECTED_PHASE__", expression(rejection.find("./set-variable[@name='gatewayQueryPhase']").get("value")))
        cases = []
        cls.route_cases = {}
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
                cases.append(query_case(prefix + "-invalid-mode", route, **base, modes={**MODES, route: PRIVATE}))
                cases.append(query_case(prefix + "-invalid-mode-type", route, **base, modes={**MODES, route: 1}))
                cases.append(query_case(prefix + "-invalid-modes-json", route, **base, modes="{"))
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
                                case["operation"] = "synthetic-renamed-create"
                                case["constraints"][case["operation"]] = constraint
                            cls.renamed.append((identifier, route, method, present))
                            cases.append(case)
        cls.unrelated = []
        for label, patch in (
            ("other-api", {"api": "unrelated-api", "path": "/models/synthetic/v1/sessions"}),
            ("other-operation", {"operation": "another-operation"}),
            ("other-template", {"template": "/other"}),
            ("other-method", {"method": "GET"}),
            ("http-null-operation", {"nullOperation": True}),
            ("voice-live", {"api": "azure-voice-live-webrtc-v1", "path": "/models/azure-voice-live/v1/realtime", "query": {"api-version": "fixed", "model": "synthetic-model"}}),
            ("embedding", {"api": "openai-embeddings-v1", "path": "/models/openai-embeddings/v1/embeddings", "query": {"api-version": "v1"}}),
            ("observer", {"api": "openai-realtime-observer-v1", "path": "/models/openai-realtime-observer/v1/realtime", "query": {"call_id": "rtc_synthetic"}}),
            ("legacy-mcp", {"api": "synthetic-legacy-mcp", "path": "/tools/synthetic-legacy-mcp/mcp", "query": {"session_id": "SYNTHETIC_SESSION"}}),
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
                label = route + "-" + suffix
                cls.unrelated.append(label)
                cases.append(query_case(label, route, query={"synthetic": "value"}, **patch))
        for route, operation in (("liveCreate", "live-create"), ("realtimeCreate", "realtime-create"), ("piiMcp", "pii_redact_text")):
            constraints = copy.deepcopy(CONSTRAINTS)
            constraints[operation]["kind"] = "unrelated-kind"
            label = route + "-other-kind"
            cls.unrelated.append(label)
            cases.append(query_case(label, route, constraints=constraints, query={"sdp": PRIVATE}))
        for case in cases:
            if case["id"] in cls.unrelated:
                case["modes"] = {route: "reject" for route in MODES}
        cls.phases = ("received", "query-rejected", "guard-error", "backend-response", "policy-error")
        for phase in cls.phases:
            cases.append(query_case("phase-" + phase, phase=phase, responseStatus=503 if phase == "policy-error" else 200))
        cases.append(query_case("untrusted-record", phase=PRIVATE, directRecord=True, recordPatch={
            "route": PRIVATE, "httpMethod": PRIVATE, "mode": PRIVATE,
            "presence": PRIVATE, "countBucket": PRIVATE, "duplicates": PRIVATE,
            "unexpected": PRIVATE, "callerId": PRIVATE,
        }))
        cases.append(query_case("invalid-record-json", recordRaw="{", directRecord=True))
        cases.append(query_case("invalid-record-types", directRecord=True, recordPatch={"httpMethod": {"private": PRIVATE}}))
        cases.append(query_case("record-extra-fields", recordPatch={"unexpected": PRIVATE, "callerId": PRIVATE}))
        for status in (0, 199, 200, 299, 300, 399, 400, 499, 500, 599, 600):
            cases.append(query_case("status-" + str(status), phase="backend-response", responseStatus=status))
        cases.append(query_case("status-not-confirmed", phase="backend-response", omitAuditStatus=True))
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

    def test_registered_entrances_and_revisions_default_to_observe(self):
        for prefix, (route, method) in self.route_cases.items():
            for suffix in ("default", "revision", "reject-empty"):
                with self.subTest(case=prefix + "-" + suffix):
                    result = self.results[prefix + "-" + suffix]
                    decision = result["decision"]
                    self.assertEqual(set(decision), SHAPE_KEYS)
                    self.assertEqual(decision["route"], route)
                    self.assertEqual(decision["httpMethod"], method)
                    self.assertEqual(decision["mode"], "reject" if suffix == "reject-empty" else "observe")
                    self.assertEqual(decision["presence"], "none")
                    self.assertEqual(decision["countBucket"], "0")
                    self.assertEqual(decision["duplicates"], "no")
                    self.assertTrue(decision["allowed"])
                    self.assertEqual(decision["status"], 0)
        self.assertEqual(self.results["mcp-trailing-slash"]["decision"]["route"], "piiMcp")
        for method in ("GET", "DELETE"):
            self.assertEqual(self.results["mcp-null-operation-" + method]["decision"]["route"], "piiMcp")
            result = self.results["mcp-null-operation-reject-" + method]
            self.assertEqual(result["decision"]["route"], "piiMcp")
            self.assertEqual(result["decision"]["httpMethod"], method)
            self.assertEqual(result["responseStatus"], 400)

    def test_query_guard_requires_current_control_settings_without_reading_unavailable_routes(self):
        for name in self.rolling_cases:
            with self.subTest(case=name):
                for case in (name+'-legacy-mixed', *(name+'-'+suffix+'-new' for suffix in ('malformed', 'null', 'blank', 'array', 'scalar'))):
                    invalid=self.results[case]
                    self.assertEqual(invalid['decision']['route'],'none')
                    self.assertEqual(invalid['queryReads'],0)
                    self.assertEqual(invalid['bodyReads'],0)

    def test_observation_buckets_count_values_without_recording_names_or_values(self):
        for prefix in self.route_cases:
            for suffix, bucket, duplicate in (("single", "1", "no"), ("same-name", "1", "no"),
                                               ("empty-name", "1", "no"), ("duplicate", "2-4", "yes"),
                                               ("null-values", "1", "no"), ("empty-values", "1", "no"),
                                               ("four", "2-4", "no"), ("five", "5+", "no"), ("private", "1", "no")):
                with self.subTest(case=prefix + "-observe-" + suffix):
                    decision = self.results[prefix + "-observe-" + suffix]["decision"]
                    self.assertTrue(decision["allowed"])
                    self.assertEqual(decision["status"], 0)
                    self.assertEqual(decision["presence"], "present")
                    self.assertEqual(decision["countBucket"], bucket)
                    self.assertEqual(decision["duplicates"], duplicate)

    def test_internal_names_do_not_change_the_fixed_public_entry_contract(self):
        for identifier, route, method, present in self.renamed:
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertEqual(result["decision"]["route"], route)
                self.assertEqual(result["decision"]["httpMethod"], method)
                self.assertEqual(result["decision"]["mode"], "reject")
                self.assertEqual(result["decision"]["presence"], "present" if present else "none")
                self.assertEqual(result["decision"]["allowed"], not present)
                self.assertEqual(result["responseStatus"], 400 if present else 0)
                self.assertEqual(result["mcpResponseStatus"], 400 if present else 0)
                self.assertEqual(result["bodyReads"], 0)

    def test_rejection_returns_the_actual_400_before_touching_the_body(self):
        for prefix in self.route_cases:
            for suffix in ("single", "same-name", "empty-name", "null-values", "empty-values", "duplicate", "four", "five", "private", "query-error"):
                identifier = prefix + "-reject-" + suffix
                with self.subTest(case=identifier):
                    result = self.results[identifier]
                    self.assertFalse(result["decision"]["allowed"])
                    self.assertEqual(result["decision"]["status"], 400)
                    self.assertTrue(result["rejected"])
                    self.assertEqual(result["responseStatus"], 400)
                    self.assertEqual(result["mcpResponseStatus"], 400)
                    for field in ("publicError", "mcpPublicError"):
                        response = json.loads(result[field])
                        self.assertEqual(set(response), {"error", "requestId"})
                        self.assertEqual(response["error"], {"code": "QUERY_PARAMETERS_NOT_ALLOWED"})
                        self.assertNotIn(PRIVATE, result[field])
                    self.assertEqual(result["bodyReads"], 0)

    def test_query_inspection_failure_is_unknown_and_does_not_change_observe_behavior(self):
        for prefix in self.route_cases:
            result = self.results[prefix + "-observe-query-error"]
            self.assertTrue(result["decision"]["allowed"])
            self.assertEqual(result["decision"]["status"], 0)
            for field in ("presence", "countBucket", "duplicates"):
                self.assertEqual(result["decision"][field], "unknown")

    def test_invalid_modes_are_explicit_configuration_errors(self):
        for prefix in self.route_cases:
            for suffix in ("invalid-mode", "invalid-mode-type", "invalid-modes-json"):
                with self.subTest(case=prefix + "-" + suffix):
                    result = self.results[prefix + "-" + suffix]
                    self.assertFalse(result["decision"]["allowed"])
                    self.assertEqual(result["decision"]["status"], 503)
                    self.assertEqual(result["responseStatus"], 503)
                    self.assertEqual(json.loads(result["publicError"])["error"], {"code": "QUERY_GUARD_NOT_CONFIGURED"})
                    self.assertEqual(result["bodyReads"], 0)

    def test_fragment_does_not_observe_before_parent_authentication_succeeds(self):
        for prefix in self.route_cases:
            for suffix in ("unauthenticated", "missing-authentication"):
                result = self.results[prefix + "-" + suffix]
                self.assertEqual(result["decision"]["route"], "none")
                self.assertEqual(result["queryReads"], 0)
                self.assertEqual(result["record"], "")

    def test_unrelated_operations_and_existing_query_contracts_are_untouched(self):
        for identifier in self.unrelated:
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertEqual(result["decision"]["route"], "none")
                self.assertTrue(result["decision"]["allowed"])
                self.assertEqual(result["decision"]["status"], 0)
                self.assertEqual(result["queryReads"], 0)
                self.assertEqual(result["record"], "")

    def test_all_observation_and_rejection_paths_preserve_request_data(self):
        for identifier, result in self.results.items():
            with self.subTest(case=identifier):
                self.assertEqual(result["bodyReads"], 0)
                self.assertEqual(result["bodyAfter"], self.cases[identifier]["body"])
                expected = {name: value if value is None or isinstance(value, list) else [value]
                            for name, value in self.cases[identifier]["query"].items()}
                self.assertEqual(result["queryAfter"], expected)

    def test_telemetry_has_only_fixed_schema_and_no_sensitive_context(self):
        for identifier, result in self.results.items():
            if not result["record"]:
                continue
            with self.subTest(case=identifier):
                self.assertNotIn("SYNTHETIC_PRIVATE", result["record"])
                record = json.loads(result["record"])
                self.assertEqual(set(record), RECORD_KEYS)
                self.assertEqual(record["event"], "query-guard")
                self.assertEqual(record["schemaVersion"], 1)
                for key, allowed in {
                    "route": {"liveCreate", "realtimeCreate", "piiMcp", "unknown"},
                    "httpMethod": {"POST", "GET", "DELETE", "unknown"},
                    "mode": {"observe", "reject", "unknown"},
                    "phase": {*self.phases, "unknown"},
                    "presence": {"none", "present", "unknown"},
                    "countBucket": {"0", "1", "2-4", "5+", "unknown"},
                    "duplicates": {"yes", "no", "unknown"},
                    "statusClass": {"2xx", "3xx", "4xx", "5xx", "unknown"},
                }.items():
                    self.assertIn(record[key], allowed, key)
        self.assertTrue(self.results["record-extra-fields"]["record"])
        for phase in self.phases:
            self.assertEqual(json.loads(self.results["phase-" + phase]["record"])["phase"], phase)

    def test_untrusted_record_fields_and_serializer_errors_use_only_unknown_enums(self):
        for identifier in ("untrusted-record", "invalid-record-json", "invalid-record-types"):
            record = json.loads(self.results[identifier]["record"])
            self.assertEqual(set(record), RECORD_KEYS)
            self.assertNotIn(PRIVATE, self.results[identifier]["record"])
            for key in ("route", "httpMethod", "mode", "phase", "presence", "countBucket", "duplicates", "statusClass"):
                self.assertEqual(record[key], "unknown", (identifier, key))

    def test_http_status_classes_never_claim_functional_success(self):
        for status in (0, 199, 200, 299, 300, 399, 400, 499, 500, 599, 600):
            expected = str(status // 100) + "xx" if 200 <= status < 600 else "unknown"
            record = json.loads(self.results["status-" + str(status)]["record"])
            self.assertEqual(record["statusClass"], expected)
            self.assertNotIn("success", record)
        self.assertEqual(json.loads(self.results["status-not-confirmed"]["record"])["statusClass"], "unknown")


class GatewayQueryStructureTests(unittest.TestCase):
    def test_guard_is_after_authentication_and_before_parent_body_processing(self):
        for filename, body_boundary in (("gateway.xml", "core-model-limits"), ("mcp.xml", "gatewayMcpArgumentError")):
            with self.subTest(policy=filename):
                inbound = list(ET.parse(POLICIES / filename).find("./inbound"))
                guard_index = next(i for i, node in enumerate(inbound)
                                   if node.tag == "include-fragment" and node.get("fragment-id") == "core-query-guard")
                base_index = next(i for i, node in enumerate(inbound) if node.tag == "base")
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
                if filename == "mcp.xml":
                    operation_authorization = next(i for i, node in enumerate(inbound)
                                                   if node.tag == "choose" and node.find(".//set-status[@code='403']") is not None)
                    self.assertLess(operation_authorization, guard_index)

    def test_early_rejection_neither_reads_inputs_nor_adds_transport(self):
        guard = ET.parse(POLICIES / "query-guard.xml").getroot()
        for filename in ("gateway.xml", "mcp.xml"):
            rejection = ET.parse(POLICIES / filename).find("./inbound/choose[@id='core-query-rejection']/when")
            self.assertIsNotNone(rejection.find("./return-response/set-status"))
            self.assertIsNotNone(rejection.find("./include-fragment[@fragment-id='core-query-telemetry']"))
            self.assertIsNotNone(rejection.find("./return-response/set-header[@name='Cache-Control']/value[.='no-store']"))
            for tag in ("send-request", "forward-request", "rewrite-uri", "set-query-parameter"):
                self.assertIsNone(rejection.find(".//" + tag))
            self.assertNotIn("context.Request.", ET.tostring(rejection, encoding="unicode"))
        for tag in ("send-request", "forward-request", "set-body", "rewrite-uri", "set-query-parameter"):
            self.assertIsNone(guard.find(".//" + tag))
        self.assertNotIn("context.Request.Body", ET.tostring(guard, encoding="unicode"))


class GatewayQueryPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        cls.resources = cls.template["resources"]

    def test_compiled_guard_and_telemetry_fragments_contain_the_real_xml(self):
        self.fragment_names = {}
        for name in ("query-guard", "query-telemetry"):
            matches = [(symbol, resource) for symbol, resource in self.resources.items()
                       if resource["type"] == "Microsoft.ApiManagement/service/policyFragments"
                       and "core-" + name in resource["name"]]
            self.assertEqual(len(matches), 1, name)
            symbol, resource = matches[0]
            self.fragment_names[name] = symbol
            self.assertEqual(resource["properties"]["format"], "rawxml")
            source = ET.fromstring(render_audit_policy(
                (POLICIES / (name + ".xml")).read_text(encoding="utf-8"), source_comments="compact"))
            resolver = PolicyExpression("[true()]", self.template["variables"], functions=self.template.get("functions"))
            generated = ET.fromstring(resolver.resolve(resource["properties"]["value"], {"queryGuards": MODES}))
            self.assertEqual(ET.tostring(generated), ET.tostring(source))
        for policies in ("apiPolicies", "mcpPolicies"):
            for symbol in self.fragment_names.values():
                self.assertIn(symbol, self.resources[policies]["dependsOn"])

    def test_compiled_templates_default_to_observe_and_preserve_per_route_reject_settings(self):
        self.assertEqual(self.template["parameters"]["queryGuards"]["defaultValue"], MODES)
        for modes in (MODES, {**MODES, "liveCreate": "reject"}, {**MODES, "piiMcp": "reject"}):
            expressions = {
                "gatewayPolicyTemplate": "[__bicep.configuredApiTemplate(true(), variables('gatewayPolicyTemplate'), variables('protocolPolicyTemplate'), variables('subscriptionProfileXml'), parameters('queryGuards'))]",
                "mcpPolicyTemplate": "[__bicep.configuredMcpTemplate(variables('mcpPolicyTemplate'), parameters('queryGuards'))]",
            }
            for variable, configured_expression in expressions.items():
                with self.subTest(modes=modes, template=variable):
                    rendered = PolicyExpression(
                        configured_expression, self.template["variables"],
                        functions=self.template.get("functions"),
                    ).evaluate({"queryGuards": modes})
                    root = ET.fromstring(rendered)
                    value = root.find("./inbound/set-variable[@name='gatewayQueryGuardModes']").get("value")
                    encoded = re.search(r'FromBase64String\("([A-Za-z0-9+/=]+)"\)', value).group(1)
                    self.assertEqual(json.loads(base64.b64decode(encoded)), modes)
                    self.assertIsNotNone(root.find("./inbound/include-fragment[@fragment-id='core-query-guard']"))


if __name__ == "__main__":
    unittest.main()
