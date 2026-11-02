"""PII処理の入力変換と、安全な応答だけを返すpolicyを検証する。

合成した日本語・英語・絵文字・下流エラーを実C#式へ渡す。原文や検出した個人情報を応答へ混入させず、モデル版と文字数境界を守ることを確認する。
"""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_gateway_limits_telemetry import PWSH, expression

CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
PII = POLICIES / "pii.xml"
SETTINGS = {"kind": "pii", "modelVersion": "2025-11-01", "maxRequestBytes": 32768,
            "allowedLanguages": ["ja", "en"], "allowedWarningCodes": ["LongWordsInDocument"]}
ID = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
INPUT_EXPRESSION = expression(PII, "./inbound/set-variable[@name='piiInputStatus']", "value")
REQUEST_EXPRESSION = expression(PII, "./inbound/set-body")
RESULT_EXPRESSION = expression(PII, "./outbound/set-variable[@name='piiResult']", "value")
RETRY_EXPRESSION = expression(PII, "./outbound/set-variable[@name='piiRetryAfter']", "value")
MCP_AUTH_EXPRESSION = expression(POLICIES / "mcp.xml", "./inbound/set-variable[@name='gatewayDecision']", "value")
REST_AUTH_EXPRESSION = expression(POLICIES / "gateway.xml", "./inbound/set-variable[@name='gatewayDecision']", "value")

CSHARP = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public sealed class Body {
  public string Data;
  public T As<T>(bool preserveContent = false) {
    object result = typeof(T) == typeof(byte[]) ? (object)Encoding.UTF8.GetBytes(Data) : (object)JObject.Parse(Data);
    return (T)result;
  }
}
public sealed class Url { public string Path = "/.well-known/oauth-protected-resource"; public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }
public sealed class Request {
  public Body Body;
  public string Method = "GET";
  public Url Url = new Url();
  public Url OriginalUrl = new Url();
  public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
}
public sealed class Response { public int StatusCode; public Body Body; public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public sealed class NamedContext { public string Id; }
public sealed class Jwt { public Dictionary<string,string[]> Claims = new Dictionary<string,string[]>(); }
public static class Extensions {
  public static string GetValueOrDefault(this Dictionary<string,string[]> source, string key, string fallback) {
    return source.ContainsKey(key) ? String.Join(" ", source[key]) : fallback;
  }
}
public sealed class Context {
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  public Request Request;
  public Response Response;
  public NamedContext Operation = new NamedContext { Id = "generated-protocol-operation" };
  public NamedContext Product;
  public NamedContext Subscription;
}
public static class PiiPolicyFixture {
  public static int Input(Context context) { __INPUT__ }
  public static string Request(Context context) { __REQUEST__ }
  public static string Result(Context context) { __RESULT__ }
  public static string McpAuth(Context context) { __MCP_AUTH__ }
  public static string RestAuth(Context context) { __REST_AUTH__ }
  public static string RetryAfter(Context context) { __RETRY__ }
  public static string Run(string casesJson) {
    var result = new JObject();
    foreach (JObject item in JArray.Parse(casesJson)) {
      var context = new Context {
        Request = new Request { Body = new Body { Data = (string)item["input"] ?? "{}" } },
        Response = new Response { StatusCode = (int?)item["status"] ?? 200, Body = new Body { Data = (string)item["response"] ?? "{}" } }
      };
      context.Request.Headers["Content-Type"] = new [] { (string)item["contentType"] ?? "application/json" };
      if ((bool?)item["query"] == true) context.Request.Url.Query["api-version"] = new [] {"caller-controlled"};
      context.Variables["piiSettings"] = (item["settings"] ?? JObject.Parse(__SETTINGS__)).ToString(Formatting.None);
      context.Variables["piiDocumentId"] = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa";
      context.Variables["piiLanguage"] = "ja";
      var mode = (string)item["mode"] ?? "input";
      if (mode == "retry") {
        context.Response.Headers["Retry-After"] = new [] { (string)item["retryAfter"] };
        result[(string)item["id"]] = RetryAfter(context);
      } else if (mode == "mcp" || mode == "rest-auth") {
        if (item["claims"] != null) {
          var jwt = new Jwt();
          foreach (var claim in ((JObject)item["claims"]).Properties()) jwt.Claims[claim.Name] = new [] { (string)claim.Value };
          context.Variables["gatewayValidatedToken"] = jwt;
        }
        context.Variables["gatewayHasAuthorization"] = item["claims"] != null;
        context.Variables["gatewayKeyHeader"] = "api-key";
        if (item["keySubscription"] != null) {
          context.Request.Headers["api-key"] = new [] { "fixture-key-not-a-credential" };
          if ((bool?)item["verified"] != false) context.Subscription = new NamedContext { Id = (string)item["keySubscription"] };
          if ((bool?)item["duplicateKey"] == true) context.Request.Headers["api-key"] = new [] { "first", "second" };
          context.Product = new NamedContext { Id = (string)item["product"] ?? "pii-users" };
        }
        context.Operation.Id = (string)item["operation"] ?? "pii_redact_text";
        var decision = mode == "mcp" ? McpAuth(context) : RestAuth(context);
        context.Variables["gatewayDecision"] = decision;
        var decisionResult = JObject.Parse(decision);
        result[(string)item["id"]] = decisionResult;
      } else if (mode == "response") {
        result[(string)item["id"]] = JObject.Parse(Result(context));
      } else {
        var status = Input(context);
        var value = new JObject(new JProperty("status", status));
        if (status == 200) value["request"] = JObject.Parse(Request(context));
        result[(string)item["id"]] = value;
      }
    }
    return result.ToString(Formatting.None);
  }
}
'''.replace("__INPUT__", INPUT_EXPRESSION).replace("__REQUEST__", REQUEST_EXPRESSION).replace("__RESULT__", RESULT_EXPRESSION).replace("__RETRY__", RETRY_EXPRESSION)


def response(**document):
    """PIIの下流サービスが返すdocument応答を合成する。実PII解析は実行しない。"""
    doc = {"id": ID, "redactedText": "email: *******************", "entities": [{"text": "SENTINEL_RAW_PII", "category": "Email"}],
           "warnings": [{"code": "LongWordsInDocument", "message": "SENTINEL_WARNING_TEXT"}, {"code": "UnregisteredWarning", "message": "SENTINEL_DIAGNOSTIC"}]}
    doc.update(document)
    return {"kind": "PiiEntityRecognitionResults", "results": {"documents": [doc], "errors": [], "modelVersion": SETTINGS["modelVersion"]}}


def response_case(name, body=None, status=200):
    """下流の正常・欠落・エラー応答を、同じ公開応答policyで評価する1ケースにまとめる。"""
    return {"id": name, "mode": "response", "response": json.dumps(body if body is not None else response()), "status": status}


class PiiPolicyTests(unittest.TestCase):
    """PII処理の入力変換と、安全な応答だけを返すpolicyを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required for real C# expression evaluation; no Azure test was run")
        invalid_result = response()
        del invalid_result["results"]["documents"][0]["redactedText"]
        wrong_model = response()
        wrong_model["results"]["modelVersion"] = "different-version"
        doc_error = {"kind": "PiiEntityRecognitionResults", "results": {"documents": [], "errors": [{"id": ID, "error": {"code": "InvalidDocument", "message": "SENTINEL_RAW_PII"}}]}}
        cls.cases = [
            {"id": "valid-ja", "input": json.dumps({"text": "SENTINEL_INPUT_TEXT", "language": "ja"})},
            {"id": "emoji-boundary", "input": json.dumps({"text": "😀" * 5120, "language": "en"}, ensure_ascii=False)},
            {"id": "combining-boundary", "input": json.dumps({"text": "e\u0301" * 5120, "language": "en"}, ensure_ascii=False)},
            {"id": "unsupported", "input": json.dumps({"text": "synthetic", "language": "auto"})},
            {"id": "empty", "input": json.dumps({"text": " ", "language": "ja"})},
            {"id": "extra-field", "input": json.dumps({"text": "synthetic", "language": "en", "modelVersion": "caller-choice"})},
            {"id": "wrong-type", "input": json.dumps({"text": 1, "language": "ja"})},
            {"id": "native-rejected", "input": json.dumps({"kind": "PiiEntityRecognition", "analysisInput": {"documents": []}})},
            {"id": "malformed-json", "input": "{bad-json"},
            {"id": "query-rejected", "input": json.dumps({"text": "synthetic", "language": "ja"}), "query": True},
            {"id": "media-type", "input": json.dumps({"text": "synthetic", "language": "ja"}), "contentType": "text/plain"},
            {"id": "too-many-bytes", "input": json.dumps({"text": "x" * 32768, "language": "ja"})},
            response_case("success"), response_case("no-detection", response(redactedText="synthetic harmless content", entities=[], warnings=[])), response_case("missing-redacted", invalid_result), response_case("wrong-id", response(id="unrelated")),
            response_case("wrong-model", wrong_model), response_case("document-error", doc_error),
            response_case("truncated", response(warnings=[{"code": "DocumentTruncated", "message": "SENTINEL_RAW_PII"}])),
            response_case("throttled", {"error": {"message": "SENTINEL_RAW_PII"}}, 429),
            response_case("upstream-size", {"error": {"message": "SENTINEL_RAW_PII"}}, 413),
            response_case("upstream-timeout", {"error": {"message": "SENTINEL_RAW_PII"}}, 504),
            response_case("upstream-unauthorized", {"error": {"message": "SENTINEL_RAW_PII"}}, 401),
            {"id": "malformed-upstream", "mode": "response", "response": "{SENTINEL_RAW_PII"},
            {"id": "retry-seconds", "mode": "retry", "status": 429, "retryAfter": "30"},
            {"id": "retry-date", "mode": "retry", "status": 429, "retryAfter": "Mon, 19 Oct 2026 12:00:00 GMT"},
            {"id": "retry-private", "mode": "retry", "status": 429, "retryAfter": "SENTINEL_PRIVATE_DIAGNOSTIC"},
            {"id": "retry-success", "mode": "retry", "status": 200, "retryAfter": "30"},
        ]
        # context.SubscriptionはAPIM標準のキー検証後の値です。
        # 不正・停止・範囲外のキーはSubscriptionが無い状態として検査します。
        auth = {"mode": "subscriptionKey"}
        claims = {"ver":"2.0", "tid":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                  "oid":"cccccccc-cccc-4ccc-8ccc-cccccccccccc", "scp":"Gateway.Invoke"}
        for mode in ("mcp", "rest-auth"):
            for name, patch in (
                ("key", {"keySubscription":"pii-system"}),
                ("other-subscription", {"keySubscription":"other-system"}),
                ("key-with-token", {"keySubscription":"pii-system", "claims":claims}),
                ("key-with-other-token", {"keySubscription":"pii-system", "claims":dict(claims,oid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",scp="Other.Scope")}),
                ("token-only", {"claims":claims}),
                ("unverified-key", {"keySubscription":"pii-system", "verified":False}),
                ("blank-subscription", {"keySubscription":""}),
                ("duplicate-key", {"keySubscription":"pii-system", "duplicateKey":True}),
                ("anonymous", {}),
            ):
                cls.cases.append({"id":mode+"-"+name,"mode":mode,**patch})
        encoded_auth = base64.b64encode(json.dumps(auth).encode()).decode()
        mcp = MCP_AUTH_EXPRESSION.replace("__AUTHORIZATION_BASE64__", encoded_auth).replace("__MCP_OPERATION_ID__", "pii_redact_text")
        rest = REST_AUTH_EXPRESSION.replace("__AUTHORIZATION_BASE64__", encoded_auth)
        source = CSHARP.replace("__MCP_AUTH__", mcp).replace("__REST_AUTH__", rest).replace("__SETTINGS__", json.dumps(json.dumps(SETTINGS)))
        with tempfile.TemporaryDirectory(prefix="core-pii-policy-") as directory:
            path = Path(directory)
            (path / "fixture.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cls.cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName) + @([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[PiiPolicyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")], text=True, capture_output=True, timeout=60)
            if run.returncode:
                raise AssertionError("Actual PII expression failed offline C# compilation/execution:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_public_input_is_converted_to_fixed_native_contract(self):
        result = self.results["valid-ja"]
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["request"], {"kind": "PiiEntityRecognition", "analysisInput": {"documents": [{"id": ID, "text": "SENTINEL_INPUT_TEXT", "language": "ja"}]},
                                              "parameters": {"loggingOptOut": True, "modelVersion": SETTINGS["modelVersion"], "stringIndexType": "TextElements_v8"}})

    def test_valid_unicode_is_not_rejected_by_utf16_length(self):
        for name in ("emoji-boundary", "combining-boundary"):
            self.assertEqual(self.results[name]["status"], 200)

    def test_schema_and_byte_rejections_prevent_upstream_request(self):
        for name in ("unsupported", "empty", "extra-field", "wrong-type", "native-rejected", "malformed-json", "query-rejected", "media-type"):
            self.assertEqual(self.results[name], {"status": 400}, name)
        self.assertEqual(self.results["too-many-bytes"], {"status": 413})

    def test_success_is_rebuilt_from_safe_fields_only(self):
        body = self.results["success"]["body"]
        self.assertEqual(set(body), {"redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"})
        self.assertEqual(body["detectedCount"], 1)
        self.assertEqual(body["warningCodes"], ["LongWordsInDocument"])
        self.assertEqual(body["language"], "ja")
        self.assertNotIn("SENTINEL", json.dumps(body))
        self.assertEqual(self.results["no-detection"]["body"]["detectedCount"], 0)
        self.assertEqual(self.results["no-detection"]["body"]["redactedText"], "synthetic harmless content")

    def test_failures_never_return_original_or_diagnostics(self):
        expected = {"missing-redacted": 502, "wrong-id": 502, "wrong-model": 502, "document-error": 422, "truncated": 413,
                    "throttled": 429, "upstream-size": 413, "upstream-timeout": 504, "upstream-unauthorized": 502, "malformed-upstream": 502}
        for name, status in expected.items():
            with self.subTest(name=name):
                result = self.results[name]
                self.assertEqual(result["status"], status)
                self.assertEqual(set(result["body"]), {"error", "requestId"})
                self.assertEqual(set(result["body"]["error"]), {"code"})
                self.assertNotIn("SENTINEL", json.dumps(result))




    def test_pii_mcp_and_rest_use_the_same_verified_subscription(self):
        for mode in ("mcp", "rest-auth"):
            for name in ("key", "key-with-token", "key-with-other-token"):
                result = self.results[mode+"-"+name]
                self.assertTrue(result["allowed"], mode+"-"+name)
                self.assertEqual(result["callerId"], "subscription:pii-system")
                self.assertEqual(result["method"], "subscriptionKey")
            self.assertEqual(self.results[mode+"-other-subscription"]["callerId"], "subscription:other-system")

    def test_pii_mcp_and_rest_reject_token_only_or_unverified_keys(self):
        for mode in ("mcp", "rest-auth"):
            for name in ("token-only", "unverified-key", "blank-subscription", "duplicate-key", "anonymous"):
                self.assertFalse(self.results[mode+"-"+name]["allowed"], mode+"-"+name)

    def test_retry_after_preserves_seconds_or_http_date_without_free_text(self):
        self.assertEqual(self.results["retry-seconds"], "30")
        self.assertEqual(self.results["retry-date"], "Mon, 19 Oct 2026 12:00:00 GMT")
        self.assertEqual(self.results["retry-private"], "")
        self.assertEqual(self.results["retry-success"], "")

    def test_pii_key_mcp_does_not_require_anonymous_oauth_metadata(self):
        from deployment.helpers.apim_authentication import render_service_policy
        common = ET.fromstring(render_service_policy())
        anonymous = common.find("./inbound/choose[@id='core-common-authentication']")
        self.assertIsNotNone(anonymous)
        self.assertEqual(common.findall(".//validate-azure-ad-token"), [])
        self.assertEqual(common.findall(".//set-header[@name='WWW-Authenticate']"), [])
        for filename in ("mcp.xml", "gateway.xml"):
            tree = ET.parse(POLICIES / filename)
            self.assertEqual(tree.findall(".//set-header[@name='WWW-Authenticate']"), [])
            decision = tree.find("./inbound/set-variable[@name='gatewayDecision']").get("value")
            self.assertIn("context.Subscription", decision)

    def test_generated_mcp_never_buffers_protocol_response(self):
        text = (POLICIES / "mcp.xml").read_text()
        self.assertNotIn("context.Response.Body", text)
        tree = ET.fromstring(text)
        self.assertIsNone(tree.find("./outbound/set-body"))
        self.assertIsNone(tree.find("./inbound/set-body"))
        # 不正引数への固定JSON-RPCエラーだけは入口で生成し、正常の要求・応答は書き換えません。
        early = tree.find("./inbound/choose/when/return-response/set-body")
        self.assertIsNotNone(early)
        self.assertIn("gatewayMcpArgumentError", early.text)
        publication = (POLICIES.parent / "api-publication.bicep").read_text()
        self.assertIn("apis/tools@2025-09-01-preview", publication)
        self.assertIn("operationId: '${apim.id}/apis/${api.name}/operations/${api.mcp.operationNames[0]}'", publication)
        self.assertIn("value: entry.constraint.kind == 'pii'", publication)
        self.assertNotIn("Microsoft.Azure.ApiManagement.Gateway", text)
        schema = json.loads((POLICIES.parent / "schemas/pii.json").read_text())["schemas"]
        self.assertFalse(schema["PiiRedactRequest"]["additionalProperties"])
        self.assertNotIn("maxLength", schema["PiiRedactRequest"]["properties"]["text"])
        self.assertEqual(set(schema["PiiRedactResult"]["properties"]), {"redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"})


if __name__ == "__main__":
    unittest.main()
