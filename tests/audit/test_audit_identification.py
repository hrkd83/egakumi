"""監査識別の実C#式を合成データで実行する。実APIM性能の証明とは区別する。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import expand_audit_fragments, render_audit_policy
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.powershell import PWSH
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from tests.gateway.policies.test_contract_rate_policy import POLICIES

TENANT = "11111111-1111-4111-8111-111111111111"
PERSON = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
TRACE = "44444444-4444-4444-8444-444444444444"
CALLER = "user:" + TENANT + ":" + PERSON
FIELDS = {"requestId", "apiId", "operationId", "requestReceivedAt", "httpMethod",
          "operationTemplate", "apimPeerIp", "reportedUserAgent", "appGatewayTraceId"}
QUERY_INNER_ERROR = json.dumps({"route": "liveCreate", "httpMethod": "POST", "mode": "reject",
                              "presence": {}, "duplicates": "no"})


def actual_expression(filename, xpath, attribute=None):
    root = ET.fromstring(expand_audit_fragments(expand_trusted_ingress_fragment(render_audit_policy(policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")))))
    node = root.find(xpath)
    raw = (node.get(attribute) if attribute else node.text).strip()
    if not raw.startswith("@{") or not raw.endswith("}"):
        raise AssertionError("Expected an actual multi-statement APIM expression")
    return raw[2:-1]


SOURCE = r'''
using System;
using System.Diagnostics;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Named { public string Id; public string UrlTemplate; }
public class Request {
 public string Method="POST"; public string IpAddress="192.0.2.10";
 public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
 public object Body { get {throw new Exception("Request body read");} }
 public object Url { get {throw new Exception("Actual URL/query read");} }
}
public class Response {
 public int StatusCode=400;
 public object Body { get {throw new Exception("Response body read");} }
 public object Headers { get {throw new Exception("Response headers read");} }
}
public class Context {
 public Guid RequestId=Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 public DateTime Timestamp=DateTime.Parse("2026-01-02T03:04:05Z",null,System.Globalization.DateTimeStyles.RoundtripKind);
 public Named Api=new Named {Id="synthetic-api"};
 public Named Operation=new Named {Id="invoke",UrlTemplate="/tools/{tool}/invoke?fixed={value}"};
 public Named Subscription; public Request Request=new Request(); public Response Response=new Response();
 public Dictionary<string,object> Variables=new Dictionary<string,object>();
}
public static class VariableExtensions {
 // APIMと同様に、欠落時は既定値、存在時は要求した型へ変換します。型不一致は呼出側のcatchへ渡します。
 public static T GetValueOrDefault<T>(this Dictionary<string,object> variables,string name,T fallback) {
  object value; return variables.TryGetValue(name,out value) ? (T)value : fallback;
 }
}
public static class IdentificationFixture {
 static string Snapshot(Context context) { __SNAPSHOT__ }
 static string Subject(Context context) { __SUBJECT__ }
 static string Gateway(Context context) { __GATEWAY__ }
 static string Credential(Context context) { __CREDENTIAL__ }
 static string Query(Context context) { __QUERY__ }
 static bool QueryGate(Context context) { __QUERY_GATE__ }
 static string Claims(Context context) { __CLAIMS__ }
 static JObject ReadRecord(string text) { return JsonConvert.DeserializeObject<JObject>(text,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None}); }
 static Context Create(JObject item) {
  var c=new Context(); c.Request.Headers["Authorization"]=new [] {"PRIVATE_TOKEN"};
  c.Request.Headers["api-key"]=new [] {"PRIVATE_KEY"}; c.Request.Headers["Cookie"]=new [] {"PRIVATE_COOKIE"};
  c.Request.Headers["User-Agent"]=new [] {"SyntheticClient/1.0"};
  c.Request.Headers["x-appgw-trace-id"]=new [] {"44444444444444448444444444444444"};
  c.Variables["gatewayAuditPhase"]="rejected"; c.Variables["gatewayAuditCode"]="SYNTHETIC_DENIAL";
  c.Variables["gatewayAuditStatus"]=400;
  c.Variables["gatewayQueryPhase"]="backend-response";
  c.Variables["gatewayQueryObservation"]="{\"route\":\"liveCreate\",\"httpMethod\":\"POST\",\"mode\":\"reject\",\"presence\":\"present\",\"duplicates\":\"no\"}";
  foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties())
   c.Request.Headers[entry.Name]=entry.Value is JArray?entry.Value.Select(v=>(string)v).ToArray():new [] {(string)entry.Value};
  if(item["peer"]!=null) c.Request.IpAddress=(string)item["peer"];
  if(item["method"]!=null) c.Request.Method=(string)item["method"];
  if(item["template"]!=null) c.Operation.UrlTemplate=(string)item["template"];
  if((bool?)item["nullApi"]==true) c.Api=null;
  if((bool?)item["nullOperation"]==true) c.Operation=null;
  if((string)item["auth"]=="key") {
   c.Subscription=new Named {Id="synthetic-subscription"};
   c.Variables["gatewaySubscriptionId"]=c.Subscription.Id; c.Variables["gatewayCommonAuthenticationValidated"]=true;
  } else if((string)item["auth"]=="user") {
   c.Variables["gatewayCommonAuthenticationValidated"]=true; c.Variables["gatewayEntraAuthenticationValidated"]=true;
   c.Variables["gatewayEntraTenantId"]="11111111-1111-4111-8111-111111111111";
   c.Variables["gatewayUserAuthentication"]="{\"schemaVersion\":1,\"tenantId\":\"11111111-1111-4111-8111-111111111111\",\"principalId\":\"22222222-2222-4222-8222-222222222222\",\"clientId\":\"33333333-3333-4333-8333-333333333333\",\"callerId\":\"user:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222\",\"allocationId\":\"user:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222\"}";
  }
  // この入力は試験用の内部状態だけを変えます。実リクエストや運用ポリシーへ故障注入は追加しません。
  if(item.Property("subscriptionId")!=null)
   c.Subscription=item["subscriptionId"].Type==JTokenType.Null?null:new Named {Id=(string)item["subscriptionId"]};
  foreach(var entry in ((JObject)(item["variables"]??new JObject())).Properties())
   c.Variables[entry.Name]=entry.Value is JValue?((JValue)entry.Value).Value:entry.Value.DeepClone();
  foreach(var name in (JArray)(item["missingVariables"]??new JArray())) c.Variables.Remove((string)name);
  return c;
 }
 public static string SubjectCases(string json) {
  var output=new JObject();
  foreach(JObject item in JsonConvert.DeserializeObject<JArray>(json,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None})) {
   var c=Create(item); var current=Subject(c);
   c.Variables["gatewayRequestContext"]=Snapshot(c); c.Variables["gatewayAuthenticatedSubject"]=current;
   output[(string)item["id"]]=new JObject(new JProperty("current",ReadRecord(current)),
    new JProperty("responseStatus",c.Response.StatusCode),
    new JProperty("emitters",new JObject(new JProperty("gateway",ReadRecord(Gateway(c))),
     new JProperty("credential",ReadRecord(Credential(c))),new JProperty("query",ReadRecord(Query(c))),
     new JProperty("claims",ReadRecord(Claims(c))))));
  }
  return output.ToString(Formatting.None);
 }
 public static string GateCases(string json) {
  var output=new JObject();
  foreach(JObject item in JsonConvert.DeserializeObject<JArray>(json,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None})) {
   var c=Create(item);
   if(item["queryRaw"]!=null) c.Variables["gatewayQueryObservation"]=(string)item["queryRaw"];
   output[(string)item["id"]]=QueryGate(c);
  }
  return output.ToString(Formatting.None);
 }
 public static string Run(string json) {
  var output=new JObject();
  foreach(JObject item in JsonConvert.DeserializeObject<JArray>(json,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None})) {
   var c=Create(item); c.Variables["gatewayRequestContext"]=Snapshot(c);
   c.Variables["gatewayAuthenticatedSubject"]=Subject(c);
   if(item["contextRaw"]!=null) c.Variables["gatewayRequestContext"]=(string)item["contextRaw"];
   if((bool?)item["missingContext"]==true) c.Variables.Remove("gatewayRequestContext");
   if(item["subjectRaw"]!=null) c.Variables["gatewayAuthenticatedSubject"]=(string)item["subjectRaw"];
   if((bool?)item["missingSubject"]==true) c.Variables.Remove("gatewayAuthenticatedSubject");
   if(item["queryRaw"]!=null) c.Variables["gatewayQueryObservation"]=(string)item["queryRaw"];
   output[(string)item["id"]]=new JObject(new JProperty("gateway",ReadRecord(Gateway(c))),
     new JProperty("credential",ReadRecord(Credential(c))),new JProperty("query",ReadRecord(Query(c))),
     new JProperty("claims",ReadRecord(Claims(c))));
  }
  return output.ToString(Formatting.None);
 }

}
'''


def compiled_source():
    source = SOURCE
    for marker, filename, xpath, attribute in (
        ("__SNAPSHOT__", "service.xml", "./inbound/set-variable[@name='gatewayRequestContext']", "value"),
        ("__SUBJECT__", "service.xml", "./inbound/set-variable[@name='gatewayAuthenticatedSubject']", "value"),
        ("__GATEWAY__", "core-gateway-telemetry.xml", "./trace/message", None),
        ("__CREDENTIAL__", "credential-telemetry.xml", "./trace/message", None),
        ("__QUERY__", "core-query-telemetry.xml", "./choose/when/trace/message", None),
        ("__QUERY_GATE__", "core-query-telemetry.xml", "./choose/when", "condition"),
        ("__CLAIMS__", "credential-connection-outbound.xml", "./choose/when/trace/message", None),
    ):
        source = source.replace(marker, actual_expression(filename, xpath, attribute))
    return source


def run_fixture(cases, *, query_gate_only=False, subjects_only=False):
    if not PWSH:
        raise unittest.SkipTest("Roslyn is required; Azure has not been tested")
    with tempfile.TemporaryDirectory(prefix="synthetic-audit-identification-") as folder:
        base = Path(folder)
        (base / "fixture.cs").write_text(compiled_source(), encoding="utf-8")
        (base / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        command = "[IdentificationFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))"
        if query_gate_only:
            command = "[IdentificationFixture]::GateCases((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))"
        if subjects_only:
            command = "[IdentificationFixture]::SubjectCases((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))"
        (base / "run.ps1").write_text("$ErrorActionPreference='Stop'\n"
            "$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\n"
            "Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701,1702'\n"
            + command + "\n", encoding="utf-8")
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(base / "run.ps1")],
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"})
        if result.returncode:
            raise AssertionError(result.stderr)
        if "PRIVATE_" in result.stdout:
            raise AssertionError("Synthetic credentials or query data leaked")
        return json.loads(result.stdout)


class AuditIdentificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = run_fixture([
            {"id": "key", "auth": "key"}, {"id": "user", "auth": "user"}, {"id": "unverified"},
            # messageの直接呼出だけの異常値。実ポリシーのouter gateはfalseになる。
            {"id": "query-error", "auth": "user", "queryRaw": "{"},
            {"id": "query-inner-error", "auth": "user", "queryRaw": QUERY_INNER_ERROR},
            {"id": "missing", "missingContext": True, "missingSubject": True},
            {"id": "malformed", "contextRaw": "{", "subjectRaw": "{"},
            {"id": "extra", "contextRaw": json.dumps({"requestId": "PRIVATE_ID", "Authorization": "PRIVATE_TOKEN", "body": "PRIVATE_TEXT"})},
            {"id": "oversize-context", "contextRaw": json.dumps({"reportedUserAgent": "z" * 5000})},
            {"id": "bounds", "auth": "key", "template": "/" + "t" * 600 + "?PRIVATE_QUERY=value", "headers": {"User-Agent": "u" * 100000}},
            {"id": "sanitized", "headers": {"User-Agent": "Synthetic\r\nClient\u0000/1.0", "x-appgw-trace-id": "invalid PRIVATE_TRACE"}, "peer": "PRIVATE_PEER"},
            {"id": "duplicates", "headers": {"User-Agent": ["one", "two"], "x-appgw-trace-id": [TRACE, TRACE]}},
            {"id": "date-agent", "headers": {"User-Agent": "2026-01-02T03:04:05.123+09:00"}},
            {"id": "null-routing", "nullApi": True, "nullOperation": True},
        ])

    def test_common_snapshot_contains_only_selected_bounded_context(self):
        for record in self.results["key"].values():
            self.assertEqual(record["auditSchemaVersion"], 2)
            context = record["requestContext"]
            self.assertEqual(set(context), FIELDS)
            self.assertEqual(context["requestReceivedAt"], "2026-01-02T03:04:05.0000000Z")
            self.assertEqual(context["httpMethod"], "POST")
            self.assertEqual(context["operationTemplate"], "/tools/{tool}/invoke")
            self.assertEqual(context["apimPeerIp"], "192.0.2.10")
            self.assertEqual(context["appGatewayTraceId"], TRACE)

    def test_query_and_gateway_events_use_their_current_schemas(self):
        for name in ("key", "user"):
            query = self.results[name]["query"]
            self.assertEqual(query["event"], "query-guard")
            self.assertEqual(query["schemaVersion"], 2)
            self.assertEqual(query["auditSchemaVersion"], 2)
            self.assertEqual(query["presence"], "present")
            self.assertEqual(query["duplicates"], "no")
            gateway = self.results[name]["gateway"]
            self.assertEqual(gateway["event"], "gateway-request")
            self.assertEqual(gateway["schemaVersion"], 1)
            self.assertEqual(gateway["auditSchemaVersion"], 2)

    def test_early_denial_does_not_erase_verified_subject(self):
        for record in self.results["user"].values():
            subject = record["authenticatedSubject"]
            self.assertEqual(subject["status"], "verified")
            self.assertEqual(subject["callerId"], CALLER)
            self.assertEqual(subject["clientId"], CLIENT)
        for record in self.results["key"].values():
            self.assertEqual(record["authenticatedSubject"]["subscriptionId"], "synthetic-subscription")
        self.assertEqual(self.results["query-inner-error"]["query"]["authenticatedSubject"]["callerId"], CALLER)
        self.assertEqual(self.results["query-inner-error"]["query"]["route"], "unknown")

    def test_query_fallback_keeps_identity_when_the_real_outer_gate_allows_emission(self):
        gates = run_fixture([{"id": "query-inner-error", "queryRaw": QUERY_INNER_ERROR},
                             {"id": "query-error", "queryRaw": "{"}], query_gate_only=True)
        self.assertEqual(gates, {"query-inner-error": True, "query-error": False})
        record = self.results["query-inner-error"]["query"]
        self.assertEqual(record["requestId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(record["authenticatedSubject"]["callerId"], CALLER)
        for field in ("route", "httpMethod", "phase", "presence", "duplicates", "statusClass"):
            self.assertEqual(record[field], "unknown")

    def test_unverified_and_malformed_context_are_not_promoted(self):
        for name in ("missing", "unverified"):
            for record in self.results[name].values():
                self.assertEqual(record["authenticatedSubject"], {"status": "unverified"})
        for record in self.results["malformed"].values():
            self.assertEqual(record["authenticatedSubject"], {"status": "inconsistent"})
        for name in ("extra", "oversize-context", "malformed", "missing"):
            for record in self.results[name].values():
                self.assertEqual(record["requestId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
                self.assertNotIn("Authorization", record["requestContext"])

    def test_user_supplied_metadata_is_bounded_and_not_an_identity(self):
        for record in self.results["bounds"].values():
            self.assertEqual(len(record["requestContext"]["reportedUserAgent"]), 512)
            self.assertEqual(len(record["requestContext"]["operationTemplate"]), 512)
        for record in self.results["sanitized"].values():
            self.assertEqual(record["requestContext"]["reportedUserAgent"], "SyntheticClient/1.0")
            self.assertIsNone(record["requestContext"]["apimPeerIp"])
            self.assertIsNone(record["requestContext"]["appGatewayTraceId"])
        for record in self.results["duplicates"].values():
            self.assertIsNone(record["requestContext"]["reportedUserAgent"])
            self.assertIsNone(record["requestContext"]["appGatewayTraceId"])
        for record in self.results["date-agent"].values():
            self.assertEqual(record["requestContext"]["reportedUserAgent"], "2026-01-02T03:04:05.123+09:00")
        for record in self.results["null-routing"].values():
            self.assertIsNone(record["apiId"])
            self.assertIsNone(record["operationId"])


if __name__ == "__main__":
    unittest.main()
