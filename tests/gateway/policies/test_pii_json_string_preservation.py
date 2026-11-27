"""Keep JSON string types through the actual PII and generated MCP expressions.

The fixture reads raw request/response JSON with JObject.Parse, like the APIM
body reader. DateParseHandling.None is used only for fixture cases and observed
output, so the fixture cannot hide date coercion in the expressions under test.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import render_audit_policy
from tests.support.powershell import PWSH
from tests import REPOSITORY


ROOT = REPOSITORY
POLICIES = ROOT / "apps/api-gateway/policies"
SETTINGS = {"modelVersion": "2025-11-01", "maxRequestBytes": 32768,
            "allowedLanguages": ["en", "ja"], "allowedWarningCodes": ["LongWordsInDocument"]}
DOCUMENT_ID = "11111111111111111111111111111111"
REQUEST_ID = "11111111-1111-1111-1111-111111111111"

SOURCE = r'''
using System;using System.Collections.Generic;using System.Linq;using System.Text;
using Newtonsoft.Json;using Newtonsoft.Json.Linq;
public class Body {
  public string Raw;public int Reads;public int ConsumingReads;
  public T As<T>(bool preserveContent=false) {
    Reads++;if(!preserveContent)ConsumingReads++;
    if(typeof(T)==typeof(byte[]))return (T)(object)Encoding.UTF8.GetBytes(Raw);
    if(typeof(T)==typeof(string))return (T)(object)Raw;
    return (T)(object)JObject.Parse(Raw);
  }
}
public class Url {public Dictionary<string,string[]> Query=new Dictionary<string,string[]>();}
public class Request {
  public Body Body=new Body();public Url Url=new Url();
  public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>();
}
public class Response {
  public int StatusCode=200;public Body Body=new Body();
  public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>();
}
public class Context {
  public Request Request=new Request();public Response Response=new Response();
  public Dictionary<string,object> Variables=new Dictionary<string,object>();
  public Guid RequestId=Guid.Parse("11111111-1111-1111-1111-111111111111");
}
public static class Extensions {
  public static string GetValueOrDefault(this Dictionary<string,string[]> source,string name,string fallback) {
    return source.ContainsKey(name)?String.Join(" ",source[name]):fallback;
  }
}
public static class PiiStringFixture {
  static int Input(Context context){__INPUT__}
  static string RequestSettings(Context context){__REQUEST_SETTINGS__}
  static string RequestBody(Context context){__REQUEST__}
  static string Result(Context context){__RESULT__}
  static string OutputBody(Context context){__OUTPUT__}
  static string McpArguments(Context context){__MCP__}
  static JObject PlainJson(string raw) {
    return JsonConvert.DeserializeObject<JObject>(raw,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None});
  }
  public static string Run(string rawCases) {
    var output=new JObject();
    foreach(JObject item in JsonConvert.DeserializeObject<JArray>(rawCases,new JsonSerializerSettings {DateParseHandling=DateParseHandling.None})) {
      var c=new Context();var result=new JObject();var mode=(string)item["mode"];
      c.Request.Body.Raw=(string)item["raw"];c.Response.Body.Raw=(string)item["raw"];
      c.Request.Headers["Content-Type"]=new[]{(string)item["contentType"]??"application/json"};
      c.Variables["piiSettings"]=__SETTINGS__;
      c.Variables["piiDocumentId"]="11111111111111111111111111111111";
      c.Variables["piiLanguage"]=(string)item["language"]??"en";
      if((bool?)item["query"]==true)c.Request.Url.Query["extra"]=new[]{"synthetic"};
      try {
        if(mode=="input" || mode=="request") {
          var status=mode=="request"?200:Input(c);
          result["status"]=status;result["inputReads"]=c.Request.Body.Reads;
          result["requestConstructed"]=status==200;
          if(status==200) {
            c.Variables["piiRequestModelVersion"]=RequestSettings(c);
            var start=c.Request.Body.Reads;
            var request=PlainJson(RequestBody(c));
            result["requestReads"]=c.Request.Body.Reads-start;
            result["text"]=request["analysisInput"]["documents"][0]["text"];
            result["language"]=request["analysisInput"]["documents"][0]["language"];
            result["loggingOptOut"]=request["parameters"]["loggingOptOut"];
            result["stringIndexType"]=request["parameters"]["stringIndexType"];
          }
        } else if(mode=="response" || mode=="output") {
          c.Response.StatusCode=(int?)item["upstreamStatus"]??200;
          var raw=mode=="output"?(string)item["raw"]:Result(c);
          var parsed=PlainJson(raw);result["status"]=parsed["status"];
          c.Variables["piiResult"]=raw;
          result["body"]=PlainJson(OutputBody(c));
          result["responseReads"]=c.Response.Body.Reads;
        } else {
          c.Variables["gatewayControlSettings"]="{\"operationConstraints\":{\"pii_redact_text\":{\"kind\":\"pii\",\"allowedLanguages\":[\"en\",\"ja\"],\"maxRequestBytes\":32768}}}";
          var error=McpArguments(c);
          result["rejected"]=!String.IsNullOrEmpty(error);
          if(!String.IsNullOrEmpty(error))result["error"]=PlainJson(error);
        }
        result["exception"]=false;
      } catch {result["exception"]=true;}
      result["requestReadsTotal"]=c.Request.Body.Reads;
      result["requestConsumingReads"]=c.Request.Body.ConsumingReads;
      result["responseConsumingReads"]=c.Response.Body.ConsumingReads;
      result["rawRequestUnchanged"]=c.Request.Body.Raw==(string)item["raw"];
      output[(string)item["name"]]=result;
    }
    return output.ToString(Formatting.None);
  }
}
'''


def expression(node, attribute=None):
    raw = (node.get(attribute) if attribute else node.text).strip()
    if raw.startswith("@{") and raw.endswith("}"):
        return raw[2:-1]
    if raw.startswith("@(") and raw.endswith(")"):
        return "return " + raw[2:-1] + ";"
    raise AssertionError("Expected a complete actual APIM expression")


def current_expressions():
    pii = ET.fromstring(render_audit_policy(
        (POLICIES / "operation-policies/pii/pii.xml").read_text(encoding="utf-8")))
    mcp = ET.fromstring(render_audit_policy(
        (POLICIES / "api-templates/mcp/generated.xml").read_text(encoding="utf-8")))
    return {
        "__INPUT__": expression(pii.find("./inbound/set-variable[@name='piiInputStatus']"), "value"),
        "__REQUEST_SETTINGS__": expression(pii.find("./inbound/set-variable[@name='piiRequestModelVersion']"), "value"),
        "__REQUEST__": expression(pii.find("./inbound/set-body")),
        "__RESULT__": expression(pii.find("./outbound/set-variable[@name='piiResult']"), "value"),
        "__OUTPUT__": expression(pii.find("./outbound/set-body")),
        "__MCP__": expression(mcp.find("./inbound/set-variable[@name='gatewayMcpArgumentError']"), "value"),
    }


def build_fixture(expressions=None):
    source = SOURCE.replace("__SETTINGS__", json.dumps(json.dumps(SETTINGS, separators=(",", ":"))))
    for marker, value in (current_expressions() if expressions is None else expressions).items():
        source = source.replace(marker, value)
    return source


def upstream(text, **patch):
    value = {"kind": "PiiEntityRecognitionResults", "results": {
        "documents": [{"id": DOCUMENT_ID, "redactedText": text, "entities": [], "warnings": []}],
        "errors": [], "modelVersion": SETTINGS["modelVersion"]}}
    value.update(patch)
    return value


def build_cases():
    cases = []
    texts = [
        ("ordinary", "Synthetic ordinary text"),
        ("date-only", "2000-01-02"),
        ("iso", "2000-01-02T03:04:05Z"),
        ("fraction", "2000-01-02T03:04:05.000Z"),
        ("ticks", "2000-01-02T03:04:05.1234567Z"),
        ("offset", "2000-01-02T03:04:05.000+09:00"),
        ("microsoft", "/Date(946782245000)/"),
        ("prefix", "~2000-01-02T03:04:05.000Z"),
        ("control", "prefix 2000-01-02T03:04:05.000Z"),
        ("unicode", '日本語 😀 "quoted" \\ slash\n'),
        ("number-string", "123"), ("bool-string", "true"), ("null-string", "null"),
    ]
    def add(name, mode, raw, **expected):
        cases.append({"name": name, "mode": mode, "raw": raw, **expected})
    for label, text in texts:
        request = json.dumps({"text": text, "language": "en"}, ensure_ascii=False)
        for mode in ("input", "request"):
            add(mode + "-" + label, mode, request, expectedStatus=200, expectedText=text)
        add("response-" + label, "response", json.dumps(upstream(text)), expectedStatus=200, expectedText=text)
        add("output-" + label, "output", json.dumps({"status": 200, "body": {"redactedText": text}}),
            expectedStatus=200, expectedText=text)
        rpc = {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
               "params": {"name": "pii_redact_text", "arguments": {"text": text, "language": "en"}}}
        add("mcp-" + label, "mcp", json.dumps(rpc), expectedRejected=False)

    for label, text in (("empty", ""), ("space", " "), ("number", 1), ("bool", False),
                        ("null", None), ("array", []), ("object", {})):
        add("input-invalid-" + label, "input", json.dumps({"text": text, "language": "en"}), expectedStatus=400)
        invalid_rpc = {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
                       "params": {"name": "pii_redact_text", "arguments": {"text": text, "language": "en"}}}
        add("mcp-invalid-" + label, "mcp", json.dumps(invalid_rpc), expectedRejected=True)
        if label not in ("empty", "space"):
            add("response-invalid-" + label, "response", json.dumps(upstream(text)),
                expectedStatus=502, expectedCode="PiiInvalidUpstreamResult")
        else:
            add("response-valid-" + label, "response", json.dumps(upstream(text)),
                expectedStatus=200, expectedText=text)
    for label, raw in (("broken", "{"), ("array", "[]"), ("null", "null"),
                       ("extra", '{"text":"synthetic","language":"en","extra":1}'),
                       ("language", '{"text":"synthetic","language":"unknown"}')):
        add("input-invalid-root-" + label, "input", raw, expectedStatus=400)
    add("input-query", "input", '{"text":"synthetic","language":"en"}', query=True, expectedStatus=400)
    add("input-content-type", "input", '{"text":"synthetic","language":"en"}',
        contentType="text/plain", expectedStatus=400)
    add("input-size-boundary", "input", json.dumps({"text": "x" * 32741, "language": "en"}),
        expectedStatus=200, expectedText="x" * 32741)
    add("input-size-over", "input", json.dumps({"text": "x" * 32742, "language": "en"}), expectedStatus=413)
    add("input-escaped", "input", r'{"text":"\u0032000-01-02T03:04:05.000Z","language":"en"}',
        expectedStatus=200, expectedText="2000-01-02T03:04:05.000Z")
    duplicate = '{"text":"ignored","text":"2000-01-02T03:04:05.000Z","language":"en"}'
    add("input-duplicate", "input", duplicate, expectedStatus=200, expectedText="2000-01-02T03:04:05.000Z")
    truncated = upstream("Synthetic")
    truncated["results"]["documents"][0]["warnings"] = [{"code": "DocumentTruncated"}]
    add("response-truncated", "response", json.dumps(truncated), expectedStatus=413, expectedCode="PiiInputTooLarge")
    invalid_id = upstream("Synthetic")
    invalid_id["results"]["documents"][0]["id"] = "another-synthetic-document"
    add("response-wrong-id", "response", json.dumps(invalid_id), expectedStatus=502, expectedCode="PiiInvalidUpstreamResult")
    invalid_model = upstream("Synthetic")
    invalid_model["results"]["modelVersion"] = "another-synthetic-version"
    add("response-wrong-model", "response", json.dumps(invalid_model),
        expectedStatus=502, expectedCode="PiiInvalidUpstreamResult")
    add("response-wrong-kind", "response", json.dumps(upstream("Synthetic", kind="another-synthetic-kind")),
        expectedStatus=502, expectedCode="PiiInvalidUpstreamResult")
    rejected = {"kind": "PiiEntityRecognitionResults", "results": {
        "documents": [], "errors": [{"id": DOCUMENT_ID, "error": {
            "code": "InvalidDocument", "message": "SYNTHETIC_DIAGNOSTIC"}}]}}
    add("response-rejected-document", "response", json.dumps(rejected),
        expectedStatus=422, expectedCode="PiiDocumentRejected")
    for name, status, code in (("throttled", 429, "PiiThrottled"), ("too-large", 413, "PiiInputTooLarge"),
                               ("timeout", 504, "PiiTimeout")):
        add("response-" + name, "response", '{"error":{"message":"SYNTHETIC_DIAGNOSTIC"}}',
            upstreamStatus=status, expectedStatus=status, expectedCode=code)
    rpc = {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
           "params": {"name": "pii_redact_text", "arguments": {"text": "2000-01-02T03:04:05.000Z", "language": "en"}}}
    for label, patch in (("string-id", {"id": "request-example"}), ("null-id", {"id": None}),
                         ("float-id", {"id": 1.5}), ("list-method", {"method": "tools/list"})):
        add("mcp-scope-" + label, "mcp", json.dumps({**rpc, **patch}), expectedRejected=False)
    notification = copy.deepcopy(rpc); notification.pop("id")
    add("mcp-scope-notification", "mcp", json.dumps(notification), expectedRejected=False)
    for label, patch in (("extra", {"extra": True}), ("language", {"language": "unknown"})):
        invalid = copy.deepcopy(rpc); invalid["params"]["arguments"].update(patch)
        add("mcp-invalid-" + label, "mcp", json.dumps(invalid), expectedRejected=True)
    return cases


def run_fixture(source, cases):
    with tempfile.TemporaryDirectory(prefix="core-pii-json-strings-") as directory:
        path = Path(directory)
        (path / "fixture.cs").write_text(source, encoding="utf-8")
        (path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        (path / "run.ps1").write_text(
            "$ErrorActionPreference='Stop'\n"
            "$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)"
            "+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\n"
            "Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs'))"
            " -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'\n"
            "[PiiStringFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n", encoding="utf-8")
        result = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
        if result.returncode:
            raise AssertionError("Actual PII string expressions did not execute:\n" + result.stderr)
        return json.loads(result.stdout)


class PiiJsonStringPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Newtonsoft.Json is required")
        cls.cases = build_cases()
        if len({case["name"] for case in cls.cases}) != len(cls.cases):
            raise AssertionError("Synthetic PII case names must be unique")
        cls.results = run_fixture(build_fixture(), cls.cases)

    def test_input_and_fixed_native_request_keep_legal_strings_and_rejections(self):
        for case in self.cases:
            if case["mode"] not in ("input", "request"):
                continue
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                self.assertFalse(result["exception"])
                self.assertEqual(result["status"], case["expectedStatus"])
                self.assertEqual(result["requestConstructed"], case["expectedStatus"] == 200)
                if case["expectedStatus"] == 200:
                    self.assertEqual(result["text"], case["expectedText"])
                    self.assertEqual(result["language"], "en")
                    self.assertTrue(result["loggingOptOut"])
                    self.assertEqual(result["stringIndexType"], "TextElements_v8")

    def test_result_and_final_output_keep_strings_and_existing_error_codes(self):
        for case in self.cases:
            if case["mode"] not in ("response", "output"):
                continue
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                self.assertFalse(result["exception"])
                self.assertEqual(result["status"], case["expectedStatus"])
                if case["expectedStatus"] == 200:
                    self.assertEqual(result["body"]["redactedText"], case["expectedText"])
                else:
                    self.assertEqual(result["body"]["error"]["code"], case["expectedCode"])
                    self.assertNotIn("SYNTHETIC_DIAGNOSTIC", json.dumps(result["body"]))

    def test_integer_id_mcp_keeps_string_arguments_and_the_current_scope(self):
        for case in self.cases:
            if case["mode"] != "mcp":
                continue
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                self.assertFalse(result["exception"])
                self.assertEqual(result["rejected"], case["expectedRejected"])
                if case["expectedRejected"]:
                    self.assertEqual(result["error"], {"jsonrpc": "2.0", "id": 17,
                        "error": {"code": -32602, "message": "Invalid tool arguments"}})
                self.assertEqual(result["requestReadsTotal"], 1)
                self.assertEqual(result["requestConsumingReads"], 0)
                self.assertTrue(result["rawRequestUnchanged"])

    def test_component_body_reads_and_consumption_are_unchanged(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                if case["mode"] in ("input", "request"):
                    expected_input = 0 if case["mode"] == "request" else 1 if case.get("query") or case.get("contentType") else 2
                    constructed = case["expectedStatus"] == 200
                    self.assertEqual(result["inputReads"], expected_input)
                    self.assertEqual(result["requestConsumingReads"], int(constructed))
                    if constructed:
                        self.assertEqual(result["requestReads"], 1)
                elif case["mode"] in ("response", "output"):
                    expected_response = int(case["mode"] == "response" and case.get("upstreamStatus", 200) == 200)
                    self.assertEqual(result["responseReads"], expected_response)
                    self.assertEqual(result["responseConsumingReads"], expected_response)
