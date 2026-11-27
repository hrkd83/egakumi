"""PII処理の入力変換と、安全な応答だけを返すpolicyを検証する。

合成した日本語・英語・絵文字・下流エラーを実C#式へ渡す。原文や検出した個人情報を応答へ混入させず、モデル版と文字数境界を守ることを確認する。
"""
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.powershell import PWSH
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tests.support.policy_expressions import expression
from tests.gateway.publication.test_api_protocols import actual_expression
from deployment.helpers.apim_common_authentication import (
    expand_common_authentication_fragments, render_common_authentication_fragments,
)
from deployment.helpers.apim_audit import render_audit_policy
from tests.identity.test_credential_login import operation_error_methods,operation_error_cases,assert_shared_operation_error
from tests import REPOSITORY

CORE = REPOSITORY
POLICIES = CORE / "apps/api-gateway/policies"
PII = policy_fixture_path(POLICIES, "pii.xml")
SETTINGS = {"kind": "pii", "modelVersion": "2025-11-01", "maxRequestBytes": 32768,
            "allowedLanguages": ["ja", "en"], "allowedWarningCodes": ["LongWordsInDocument"]}
ID = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
INPUT_EXPRESSION = expression(PII, "./inbound/set-variable[@name='piiInputStatus']", "value")
REQUEST_EXPRESSION = expression(PII, "./inbound/set-body")
REQUEST_SETTINGS_EXPRESSION = expression(PII, "./inbound/set-variable[@name='piiRequestModelVersion']", "value")
AUDIT_SETTINGS_EXPRESSION = expression(PII, "./outbound/set-variable[@name='piiAuditModelVersion']", "value")
AUDIT_EXPRESSION = expression(PII, "./outbound/trace/message")
_native_size = ET.fromstring(render_audit_policy(PII.read_text(encoding='utf-8'))).find("./inbound/choose[@id='core-pii-native-request-size']/when").get("condition")
NATIVE_SIZE_EXPRESSION = _native_size[2:-1]
RESULT_EXPRESSION = expression(PII, "./outbound/set-variable[@name='piiResult']", "value")
RETRY_EXPRESSION = expression(PII, "./outbound/set-variable[@name='piiRetryAfter']", "value")
MCP_AUTH_EXPRESSION = expression(POLICIES / "api-templates" / "mcp/generated.xml", "./inbound/set-variable[@name='gatewayDecision']", "value")
REST_AUTH_EXPRESSION = actual_expression("rest.xml", "gatewayDecision")

CSHARP = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public sealed class Body {
  public string Data;
  public int Reads;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    object result = typeof(T) == typeof(byte[]) ? (object)Encoding.UTF8.GetBytes(Data) : typeof(T) == typeof(string) ? (object)Data : (object)JObject.Parse(Data);
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
public sealed class LastError { public string Source="",Reason="",PolicyId=""; }
public sealed class TrackingVariables : Dictionary<string,object> {
  public int SettingsReads;
  public new object this[string key] {
    get { if (key == "piiSettings") SettingsReads++; return base[key]; }
    set { base[key] = value; }
  }
}
public static class Extensions {
  public static string GetValueOrDefault(this Dictionary<string,string[]> source, string key, string fallback) {
    return source.ContainsKey(key) ? String.Join(" ", source[key]) : fallback;
  }
}
public sealed class Context {
  public TrackingVariables Variables = new TrackingVariables();
  public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  public Request Request;
  public Response Response;
  public NamedContext Operation = new NamedContext { Id = "generated-protocol-operation" };
  public NamedContext Api = new NamedContext { Id = "synthetic-pii-api" };
  public LastError LastError = new LastError();
  public NamedContext Product;
  public NamedContext Subscription;
}
public static class PiiPolicyFixture {
  __OPERATION_ERROR_METHODS__
  public static int Input(Context context) { __INPUT__ }
  public static string Request(Context context) { __REQUEST__ }
  public static string RequestSettings(Context context) { __REQUEST_SETTINGS__ }
  public static string AuditSettings(Context context) { __AUDIT_SETTINGS__ }
  public static string Audit(Context context) { __AUDIT__ }
  public static bool NativeTooLarge(Context context) { return __NATIVE_SIZE__; }
  public static string Result(Context context) { __RESULT__ }
  public static string McpAuth(Context context) { __MCP_AUTH__ }
  public static string RestAuth(Context context) { __REST_AUTH__ }
  public static string CommonKeyValidation(Context context) { __COMMON_KEY_VALIDATION__ }
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
      if (item["settingsRaw"] != null) context.Variables["piiSettings"] = (string)item["settingsRaw"];
      if ((bool?)item["settingsObject"] == true) context.Variables["piiSettings"] = new JObject();
      if ((bool?)item["settingsMissing"] == true) context.Variables.Remove("piiSettings");
      context.Variables["piiDocumentId"] = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa";
      context.Variables["piiLanguage"] = "ja";
      var mode = (string)item["mode"] ?? "input";
      if (mode == "error") {
        var error=(JObject)item["lastError"];context.LastError.Source=(string)error["source"];context.LastError.Reason=(string)error["reason"];context.LastError.PolicyId=(string)error["policyId"];
        result[(string)item["id"]]=(bool?)item["entraEnabled"]==false?PiiErrorDisabled(context):PiiError(context);
      } else if (mode == "retry") {
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
        // Execute the actual inherited key gate before either API identity
        // handoff. Unverified or malformed keys cannot reach those expressions.
        var failure=CommonKeyValidation(context);
        if (!String.IsNullOrEmpty(failure)) {
          result[(string)item["id"]]=new JObject(new JProperty("allowed",false));
          continue;
        }
        context.Variables["gatewayCommonAuthenticationValidated"]=true;
        // 実際のHTTP処理では、操作設定の選択が本人情報の引渡しより先に完了します。
        // この単体fixtureでは、その最小の成功結果だけを用意します。
        context.Variables["gatewayOperationSelection"]=new JObject(
          new JProperty("allowed",true), new JProperty("settingId","")).ToString(Formatting.None);
        var decision = mode == "mcp" ? McpAuth(context) : RestAuth(context);
        context.Variables["gatewayDecision"] = decision;
        var decisionResult = JObject.Parse(decision);
        result[(string)item["id"]] = decisionResult;
      } else if (mode == "audit-probe") {
        JObject value;
        context.Variables["piiResult"]=(string)item["piiResultRaw"] ?? "{\"status\":200,\"body\":{\"detectedCount\":1}}";
        context.Variables["piiInputBytes"]=(object)(64);
        if(item["inputBytesRaw"]!=null)context.Variables["piiInputBytes"]=(string)item["inputBytesRaw"];
        if((bool?)item["inputBytesMissing"]==true)context.Variables.Remove("piiInputBytes");
        try { context.Variables["piiAuditModelVersion"]=AuditSettings(context); }
        catch {
          context.LastError.Source="set-variable";context.LastError.Reason="ExpressionValueEvaluationFailure";context.LastError.PolicyId=__AUDIT_SETTINGS_POLICY_ID__;
          value=PiiError(context);value["bodyReads"]=context.Response.Body.Reads;value["settingsReads"]=context.Variables.SettingsReads;value["auditBuilt"]=false;
          result[(string)item["id"]]=value;continue;
        }
        try { value=new JObject(new JProperty("status",200),new JProperty("audit",JObject.Parse(Audit(context))),new JProperty("auditBuilt",true)); }
        catch { context.LastError.Source="trace";context.LastError.Reason="ExpressionValueEvaluationFailure";context.LastError.PolicyId="";value=PiiError(context);value["auditBuilt"]=false; }
        value["bodyReads"]=context.Response.Body.Reads;value["settingsReads"]=context.Variables.SettingsReads;result[(string)item["id"]]=value;
      } else if (mode == "request-probe") {
        JObject value;
        try {
          context.Variables["piiRequestModelVersion"] = RequestSettings(context);
        } catch {
          context.LastError.Source="set-variable";context.LastError.Reason="ExpressionValueEvaluationFailure";context.LastError.PolicyId=__REQUEST_SETTINGS_POLICY_ID__;
          value=PiiError(context);value["inputBodyReads"]=context.Request.Body.Reads;value["settingsReads"]=context.Variables.SettingsReads;value["requestBuilt"]=false;
          result[(string)item["id"]]=value;continue;
        }
        try { value=new JObject(new JProperty("status",200),new JProperty("request",JObject.Parse(Request(context))),new JProperty("requestBuilt",true)); }
        catch { context.LastError.Source="set-body";context.LastError.Reason="ExpressionValueEvaluationFailure";context.LastError.PolicyId="";value=PiiError(context);value["requestBuilt"]=false; }
        value["inputBodyReads"]=context.Request.Body.Reads;value["settingsReads"]=context.Variables.SettingsReads;result[(string)item["id"]]=value;
      } else if (mode == "response-probe") {
        JObject value;
        try { value=JObject.Parse(Result(context)); }
        catch { context.LastError.Source="set-variable";context.LastError.Reason="ExpressionValueEvaluationFailure";context.LastError.PolicyId=__RESULT_POLICY_ID__;value=PiiError(context); }
        value["responseBodyReads"]=context.Response.Body.Reads;value["settingsReads"]=context.Variables.SettingsReads;result[(string)item["id"]]=value;
      } else if (mode == "response") {
        result[(string)item["id"]] = JObject.Parse(Result(context));
      } else {
        var probe = mode == "input-probe";
        int status;
        try { status = Input(context); }
        catch {
          if (!probe) throw;
          context.LastError.Source = "set-variable";
          context.LastError.Reason = "ExpressionValueEvaluationFailure";
          context.LastError.PolicyId = __INPUT_POLICY_ID__;
          var failure = PiiError(context);
          failure["inputBodyReads"] = context.Request.Body.Reads;
          failure["settingsReads"] = context.Variables.SettingsReads;
          failure["requestBuilt"] = false;
          result[(string)item["id"]] = failure;
          continue;
        }
        var inputReads = context.Request.Body.Reads;
        var settingsReads = context.Variables.SettingsReads;
        var value = new JObject(new JProperty("status", status));
        if (status == 200) {
          context.Variables["piiRequestModelVersion"] = RequestSettings(context);
          var request = Request(context);
          context.Request.Body.Data = request;
          if (NativeTooLarge(context)) value["status"] = 413;
          else value["request"] = JObject.Parse(request);
        }
        if (probe) {
          value["inputBodyReads"] = inputReads;
          value["settingsReads"] = settingsReads;
          value["requestBuilt"] = status == 200;
        }
        result[(string)item["id"]] = value;
      }
    }
    return result.ToString(Formatting.None);
  }
}
'''.replace("__INPUT__", INPUT_EXPRESSION).replace("__REQUEST__", REQUEST_EXPRESSION).replace("__REQUEST_SETTINGS__", REQUEST_SETTINGS_EXPRESSION).replace("__AUDIT_SETTINGS__", AUDIT_SETTINGS_EXPRESSION).replace("__AUDIT__", AUDIT_EXPRESSION).replace("__RESULT__", RESULT_EXPRESSION).replace("__RETRY__", RETRY_EXPRESSION).replace("__NATIVE_SIZE__", NATIVE_SIZE_EXPRESSION)


def response(**document):
    """PIIの下流サービスが返すdocument応答を合成する。実PII解析は実行しない。"""
    doc = {"id": ID, "redactedText": "email: *******************", "entities": [{"text": "SENTINEL_RAW_PII", "category": "Email"}],
           "warnings": [{"code": "LongWordsInDocument", "message": "SENTINEL_WARNING_TEXT"}, {"code": "UnregisteredWarning", "message": "SENTINEL_DIAGNOSTIC"}]}
    doc.update(document)
    return {"kind": "PiiEntityRecognitionResults", "results": {"documents": [doc], "errors": [], "modelVersion": SETTINGS["modelVersion"]}}


def response_case(name, body=None, status=200):
    """下流の正常・欠落・エラー応答を、同じ公開応答policyで評価する1ケースにまとめる。"""
    return {"id": name, "mode": "response", "response": json.dumps(body if body is not None else response()), "status": status}


class PiiExpressionPlatformTests(unittest.TestCase):
    def test_internal_failures_use_apim_supported_exception_constructors(self):
        # A general C# compiler accepts exception types that APIM expressions
        # reject. Keep this platform boundary separate from behavior tests.
        for code in (REQUEST_SETTINGS_EXPRESSION, RESULT_EXPRESSION):
            with self.subTest(section=code[:32]):
                constructors = re.findall(r"\bthrow\s+new\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\(", code)
                self.assertTrue(constructors)
                self.assertTrue(set(constructors) <= {"Exception", "System.Exception"})


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
        # 内部設定の取得と実input式から実on-errorまでを接続し、本文を読まず停止することを確認します。
        private = "SENTINEL_PRIVATE_SETTINGS"
        for label, patch in (
            ("missing", {"settingsMissing": True}),
            ("malformed", {"settingsRaw": "{" + private}),
            ("null", {"settingsRaw": "null"}),
            ("array", {"settingsRaw": "[]"}),
            ("scalar", {"settingsRaw": "true"}),
            ("object-value", {"settingsObject": True}),
            ("max-missing", {"settings": {k:v for k,v in SETTINGS.items() if k != "maxRequestBytes"}}),
            ("max-null", {"settings": dict(SETTINGS, maxRequestBytes=None)}),
            ("max-invalid", {"settings": dict(SETTINGS, maxRequestBytes=private)}),
        ):
            cls.cases.append({"id": "settings-" + label, "mode": "input-probe", "input": "SENTINEL_INPUT_TEXT", **patch})
        for label, patch in (("null", None), ("empty", []), ("scalar", "unchanged")):
            cls.cases.append({"id": "languages-" + label, "mode": "input-probe", "input": json.dumps({"text": "synthetic", "language": "fr"}), "settings": dict(SETTINGS, allowedLanguages=patch)})
        request_input=json.dumps({'text':'synthetic','language':'ja'})
        for label,patch in (
            ('missing',{'settingsMissing':True}),('raw',{'settingsRaw':'{SENTINEL_SETTINGS'}),
            ('object',{'settingsObject':True}),('null',{'settingsRaw':'null'}),('array',{'settingsRaw':'[]'}),
            ('model-object',{'settings':dict(SETTINGS,modelVersion={})}),('model-array',{'settings':dict(SETTINGS,modelVersion=[])}),
        ):
            cls.cases.append({'id':'request-settings-'+label,'mode':'request-probe','input':request_input,**patch})
        for label,patch in (('missing',{k:v for k,v in SETTINGS.items() if k!='modelVersion'}),('null',dict(SETTINGS,modelVersion=None)),('numeric',dict(SETTINGS,modelVersion=42)),('valid',SETTINGS)):
            cls.cases.append({'id':'request-model-'+label,'mode':'request-probe','input':request_input,'settings':patch})
        cls.cases.append({'id':'request-body-failure','mode':'request-probe','input':'{SENTINEL_BODY'})
        for label,version in (('empty',''),('blank',' ')):
            cls.cases.append({'id':'request-model-'+label,'mode':'request-probe','input':request_input,'settings':dict(SETTINGS,modelVersion=version)})
        for label,patch in (
            ('missing',{'settingsMissing':True}),('raw',{'settingsRaw':'{SENTINEL_SETTINGS'}),('object',{'settingsObject':True}),
            ('null',{'settingsRaw':'null'}),('array',{'settingsRaw':'[]'}),
            ('model-object',{'settings':dict(SETTINGS,modelVersion={})}),('model-array',{'settings':dict(SETTINGS,modelVersion=[])}),
        ):
            cls.cases.append({'id':'audit-settings-'+label,'mode':'audit-probe',**patch})
        for label,patch in (('missing',{k:v for k,v in SETTINGS.items() if k!='modelVersion'}),('null',dict(SETTINGS,modelVersion=None)),('numeric',dict(SETTINGS,modelVersion=42)),('valid',SETTINGS)):
            cls.cases.append({'id':'audit-model-'+label,'mode':'audit-probe','settings':patch})
        for label,patch in (
            ('result',{'piiResultRaw':'{SENTINEL_RESULT'}),('bytes',{'inputBytesRaw':'SENTINEL_BYTES'}),('bytes-missing',{'inputBytesMissing':True}),
            ('detected',{'piiResultRaw':'{"status":200,"body":{"detectedCount":{}}}'}),
        ):
            cls.cases.append({'id':'audit-other-'+label,'mode':'audit-probe',**patch})
        for label,policy_id in (('exact','core-pii-request-settings'),('near','core-pii-request-settings-extra'),('null',None),('empty','')):
            cls.cases.append({'id':'request-error-'+label,'mode':'error','lastError':{'source':'set-variable','reason':'ExpressionValueEvaluationFailure','policyId':policy_id}})
        for label,policy_id in (('exact','core-pii-audit-settings'),('near','core-pii-audit-settings-extra'),('null',None),('empty','')):
            cls.cases.append({'id':'audit-error-'+label,'mode':'error','lastError':{'source':'set-variable','reason':'ExpressionValueEvaluationFailure','policyId':policy_id}})
        for label,patch in (('missing',{'settingsMissing':True}),('raw',{'settingsRaw':'{SENTINEL_SETTINGS'}),('null',{'settingsRaw':'null'}),('array',{'settingsRaw':'[]'}),('object',{'settingsObject':True})):
            cls.cases.append({**response_case('result-settings-'+label),'mode':'response-probe',**patch})
        for label,patch in (('missing',{k:v for k,v in SETTINGS.items() if k!='modelVersion'}),('null',dict(SETTINGS,modelVersion=None)),('numeric',dict(SETTINGS,modelVersion=42)),('empty',dict(SETTINGS,modelVersion='')),('blank',dict(SETTINGS,modelVersion=' ')),('object',dict(SETTINGS,modelVersion={}))):
            for external in ('present','missing'):
                document=response()
                if external=='missing':del document['results']['modelVersion']
                cls.cases.append({**response_case('result-model-'+label+'-'+external,document),'mode':'response-probe','settings':patch})
        for label,patch in (('missing',{k:v for k,v in SETTINGS.items() if k!='allowedWarningCodes'}),('null',dict(SETTINGS,allowedWarningCodes=None)),('scalar',dict(SETTINGS,allowedWarningCodes='SENTINEL_SETTINGS')),('object',dict(SETTINGS,allowedWarningCodes={}))):
            cls.cases.append({**response_case('result-allowed-'+label),'mode':'response-probe','settings':patch})
        doc=response();del doc['results']['modelVersion']
        cls.cases.extend([
            {**response_case('result-normal'),'mode':'response-probe'},
            {**response_case('result-external-missing',doc),'mode':'response-probe'},
            {**response_case('result-error-priority',doc_error),'mode':'response-probe','settings':dict(SETTINGS,modelVersion={},allowedWarningCodes={})},
            {**response_case('result-truncated-priority',response(warnings=[{'code':'DocumentTruncated'}])),'mode':'response-probe','settings':dict(SETTINGS,allowedWarningCodes={})},
            {**response_case('result-wrong-id-priority',response(id='unrelated')),'mode':'response-probe','settings':dict(SETTINGS,modelVersion={})},
            {**response_case('result-non200',status=429),'mode':'response-probe','settingsMissing':True},
            {'id':'result-malformed','mode':'response-probe','response':'{SENTINEL_BODY'},
        ])
        for label,value in (('object',{}),('array',[])):
            document=response();document['results']['modelVersion']=value
            cls.cases.append({**response_case('result-external-cast-'+label,document),'mode':'response-probe','settings':dict(SETTINGS,modelVersion={})})
        for label,policy_id in (('exact','core-pii-result-settings'),('near','core-pii-result-settings-extra'),('null',None),('empty','')):
            cls.cases.append({'id':'result-error-'+label,'mode':'error','lastError':{'source':'set-variable','reason':'ExpressionValueEvaluationFailure','policyId':policy_id}})
        for label, body, patch in (
            ("valid", '{"text":"synthetic","language":"ja"}', {}),
            ("malformed", "{SENTINEL_INPUT_TEXT", {}),
            ("missing-text", '{"other":"synthetic","language":"ja"}', {}),
            ("missing-language", '{"text":"synthetic","other":"ja"}', {}),
            ("wire-large", "{" + " " * 196608, {}),
            ("compact-large", '{"text":"synthetic","language":"ja"}', {"settings": dict(SETTINGS, maxRequestBytes=10)}),
        ):
            cls.cases.append({"id": "probe-" + label, "mode": "input-probe", "input": body, **patch})
        # 操作を単独で評価しても、解析前の受信上限を独立して守ります。
        small = '{"text":"synthetic","language":"en"}'
        for size in (196608, 196609):
            cls.cases.append({"id": "operation-wire-" + str(size), "input": small + " " * (size - len(small.encode()))})
        cls.cases.append({"id": "operation-wire-over-malformed", "input": "{" + " " * 196608})
        # 本番API定義のモデル版・空のwarning allowlistでも同じ式を検査します。
        adopted = json.loads((POLICIES.parent / "apis/pii-v1.json").read_text())["authorization"]["operationConstraints"]["pii_redact_text"]
        # Native Language counts the constructed envelope; combining marks show
        # why UTF-16 length and the old compact 32 KiB budget are not native limits.
        cls.cases.append({"id": "adopted-combining-long", "input": json.dumps({"text": ("e" + "\u0301" * 10) * 5000, "language": "fr"}, ensure_ascii=False), "settings": adopted})
        envelope = {"kind": "PiiEntityRecognition", "analysisInput": {"documents": [{"id": ID, "language": "fr", "text": ""}]},
                    "parameters": {"loggingOptOut": True, "modelVersion": adopted["modelVersion"], "stringIndexType": "TextElements_v8"}}
        overhead = len(json.dumps(envelope, separators=(",", ":")).encode())
        for excess in (0, 1):
            text = "a" * (1000000 - overhead + excess)
            cls.cases.append({"id": "adopted-native-byte-" + str(excess), "input": json.dumps({"text": text, "language": "fr"}), "settings": adopted})
        for name, document in (
            ("adopted-unknown-warning", response(entities=[], redactedText="synthetic harmless content",
                warnings=[{"code": "UnregisteredWarning", "message": "SENTINEL_DIAGNOSTIC"}])),
            ("adopted-truncated", response(warnings=[{"code": "DocumentTruncated", "message": "SENTINEL_RAW_PII"}])),
            ("adopted-wrong-model", response()),
        ):
            document["results"]["modelVersion"] = "different-version" if name.endswith("wrong-model") else adopted["modelVersion"]
            cls.cases.append({**response_case(name, document), "settings": adopted})
        cls.cases += [response_case("entities-object", response(entities={})),
                      response_case("warnings-object", response(warnings={})),
                      response_case("untrusted-detected-count", response(detectedCount=-1))]
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
        common = ET.fromstring(render_common_authentication_fragments(entra_enabled=False)['core-common-authentication'])
        key_validation = common.find("./choose/otherwise/set-variable[@name='gatewayAuthenticationFailure']").get('value').strip()
        if not key_validation.startswith('@{') or not key_validation.endswith('}'):
            raise AssertionError('Expected the actual common key validation expression')
        source = CSHARP.replace("__MCP_AUTH__", mcp).replace("__REST_AUTH__", rest).replace("__SETTINGS__", json.dumps(json.dumps(SETTINGS)))
        source = source.replace('__COMMON_KEY_VALIDATION__', key_validation[2:-1])
        error_methods=operation_error_methods(PII.read_text(encoding='utf-8'),'PiiError')+'\n'+operation_error_methods(PII.read_text(encoding='utf-8'),'PiiErrorDisabled',entra_enabled=False)
        source=source.replace('__OPERATION_ERROR_METHODS__',error_methods)
        input_policy_id = ET.parse(PII).find("inbound/set-variable[@name='piiInputStatus']").get('id')
        source=source.replace('__INPUT_POLICY_ID__',json.dumps(input_policy_id))
        source=source.replace('__REQUEST_SETTINGS_POLICY_ID__',json.dumps(ET.parse(PII).find("inbound/set-variable[@name='piiRequestModelVersion']").get('id')))
        source=source.replace('__AUDIT_SETTINGS_POLICY_ID__',json.dumps(ET.parse(PII).find("outbound/set-variable[@name='piiAuditModelVersion']").get('id')))
        source=source.replace('__RESULT_POLICY_ID__',json.dumps(ET.parse(PII).find("outbound/set-variable[@name='piiResult']").get('id')))
        for label,last_error in operation_error_cases():
            cls.cases.append({'id':'operation-error-'+label,'mode':'error','lastError':last_error,'input':'SENTINEL_ERROR_DETAIL'})
        cls.cases.append({'id':'operation-error-disabled','mode':'error','entraEnabled':False,'lastError':operation_error_cases()[0][1]})
        cls.cases.append({'id':'operation-error-own-jwt','mode':'error','lastError':{'policyId':'pii-user-token-validation','source':'validate-jwt','reason':'TokenValidationFailed'}})
        for label, policy_id in (("exact", input_policy_id), ("similar", input_policy_id + "-extra"), ("null", None), ("empty", "")):
            cls.cases.append({'id':'input-error-'+label,'mode':'error','lastError':{'policyId':policy_id,'source':'set-variable','reason':'ExpressionValueEvaluationFailure'}})
        for label,policy_id in (('exact','core-audit-body-model-read'),('similar','core-audit-body-model-read-extra'),('null',None),('empty','')):
            cls.cases.append({'id':'body-model-error-'+label,'mode':'error','lastError':{'policyId':policy_id,'source':'set-variable','reason':'ExpressionValueEvaluationFailure'}})
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

    def test_pii_shared_entra_error_stops_before_dedicated_error_and_audit(self):
        assert_shared_operation_error(self,self.results['operation-error-entra'])
        for label,_ in operation_error_cases()[1:]:
            with self.subTest(error=label):
                result=self.results['operation-error-'+label]
                expected=401 if label in ('subscription','other-id','null-id','empty-id') else 504 if label=='timeout' else 429 if label=='rate' else 502
                self.assertFalse(result['sharedHandled'])
                self.assertTrue(result['legacyReached'])
                self.assertEqual(result['status'],expected)
                self.assertEqual(len(result['audits']),1)
                self.assertEqual(result['audits'][0]['code'],'RATE_LIMIT_EXCEEDED' if label=='rate' else 'POLICY_ERROR')
                self.assertNotIn('WWW-Authenticate',result['headers'])
                if label=='rate':self.assertFalse(result['hasBody'])
                else:self.assertEqual(result['body']['error']['code'],'PiiAuthenticationFailed' if expected==401 else 'PiiTimeout' if expected==504 else 'PiiUpstreamFailure')
        for label in ('disabled','own-jwt'):
            result=self.results['operation-error-'+label]
            self.assertFalse(result['sharedHandled'])
            self.assertTrue(result['legacyReached'])
            self.assertEqual(result['status'],401)
            self.assertEqual(result['body']['error']['code'],'PiiAuthenticationFailed')
            self.assertEqual(result['audits'][0]['code'],'POLICY_ERROR')
        for label in ('exact','similar','null','empty'):
            result=self.results['body-model-error-'+label]
            self.assertEqual(result['status'],500 if label=='exact' else 502)
            self.assertEqual(result['audits'][0]['code'],'OPERATION_SETTINGS_INVALID' if label=='exact' else 'POLICY_ERROR')
            self.assertEqual(len(result['audits']),1)
            if label=='exact':
                self.assertFalse(result['hasBody'])
                self.assertEqual(result['headers']['Cache-Control'],'no-store')

    def test_public_input_is_converted_to_fixed_native_contract(self):
        result = self.results["valid-ja"]
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["request"], {"kind": "PiiEntityRecognition", "analysisInput": {"documents": [{"id": ID, "text": "SENTINEL_INPUT_TEXT", "language": "ja"}]},
                                              "parameters": {"loggingOptOut": True, "modelVersion": SETTINGS["modelVersion"], "stringIndexType": "TextElements_v8"}})

    def test_request_settings_are_separate_from_request_body_failures(self):
        for label in ('missing','raw','object','null','array','model-object','model-array'):
            value=self.results['request-settings-'+label]
            self.assertEqual((value['status'],value['inputBodyReads'],value['settingsReads'],value['requestBuilt']),(500,0,1,False),label)
            self.assertEqual(value['body']['error']['code'],'PiiProcessingFailed')
            self.assertEqual([(a['phase'],a['code'],a['status']) for a in value['audits']],[('policy-error','PII_SETTINGS_INVALID',500)])
            self.assertEqual(value['headers']['Cache-Control'],'no-store')
            self.assertNotIn('SENTINEL',json.dumps(value))
        for label in ('missing','null','numeric','empty','blank'):
            value=self.results['request-model-'+label]
            self.assertEqual((value['status'],value['inputBodyReads'],value['settingsReads'],value['requestBuilt']),(500,0,1,False),label)
        value=self.results['request-model-valid']
        self.assertEqual((value['status'],value['inputBodyReads'],value['settingsReads'],value['requestBuilt']),(200,1,1,True))
        value=self.results['request-body-failure']
        self.assertEqual((value['status'],value['inputBodyReads'],value['settingsReads']),(502,1,1))
        for label in ('exact','near','null','empty'):
            value=self.results['request-error-'+label]
            self.assertEqual(value['status'],500 if label=='exact' else 502)
            self.assertEqual(value['audits'][0]['code'],'PII_SETTINGS_INVALID' if label=='exact' else 'POLICY_ERROR')

    def test_audit_settings_are_separate_from_result_and_counter_failures(self):
        for label in ('missing','raw','object','null','array','model-object','model-array'):
            value=self.results['audit-settings-'+label]
            self.assertEqual((value['status'],value['bodyReads'],value['settingsReads'],value['auditBuilt']),(500,0,1,False),label)
            self.assertEqual(value['body']['error']['code'],'PiiProcessingFailed')
            self.assertEqual([(a['phase'],a['code'],a['status']) for a in value['audits']],[('policy-error','PII_SETTINGS_INVALID',500)])
            self.assertNotIn('SENTINEL',json.dumps(value))
        for label in ('missing','null','numeric','valid'):
            value=self.results['audit-model-'+label]
            self.assertEqual((value['status'],value['bodyReads'],value['settingsReads'],value['auditBuilt']),(200,0,1,True),label)
        self.assertIsNone(self.results['audit-model-missing']['audit']['modelVersion'])
        self.assertIsNone(self.results['audit-model-null']['audit']['modelVersion'])
        self.assertEqual(self.results['audit-model-numeric']['audit']['modelVersion'],'42')
        for label in ('result','bytes','bytes-missing','detected'):
            value=self.results['audit-other-'+label]
            self.assertEqual((value['status'],value['bodyReads'],value['settingsReads']),(502,0,1),label)
            self.assertNotIn('SENTINEL',json.dumps(value))
        for label in ('exact','near','null','empty'):
            value=self.results['audit-error-'+label]
            self.assertEqual(value['status'],500 if label=='exact' else 502)
            self.assertEqual(value['audits'][0]['code'],'PII_SETTINGS_INVALID' if label=='exact' else 'POLICY_ERROR')

    def test_result_settings_keep_external_short_circuit_and_required_internal_model(self):
        for label in ('missing','raw','null','array','object'):
            value=self.results['result-settings-'+label]
            self.assertEqual((value['status'],value['responseBodyReads'],value['settingsReads']),(500,0,1),label)
        for label in ('missing','null','numeric','empty','blank','object'):
            for external in ('present','missing'):
                value=self.results['result-model-'+label+'-'+external]
                self.assertEqual((value['status'],value['responseBodyReads'],value['settingsReads']),(500,1,1),(label,external))
                self.assertEqual(value['body']['error']['code'],'PiiProcessingFailed')
                self.assertEqual([(a['phase'],a['code'],a['status']) for a in value['audits']],[('policy-error','PII_SETTINGS_INVALID',500)])
        for label in ('scalar','object','null'):
            self.assertEqual(self.results['result-allowed-'+label]['status'],500,label)
        for label in ('object','array'):
            value=self.results['result-external-cast-'+label]
            self.assertEqual((value['status'],value['responseBodyReads'],value['settingsReads']),(502,1,1),label)
        for label in ('missing',):
            self.assertEqual(self.results['result-allowed-'+label]['status'],502,label)
        for label,status,reads,settings_reads in (('normal',200,1,1),('external-missing',502,1,1),('error-priority',422,1,1),('truncated-priority',413,1,1),('wrong-id-priority',502,1,1),('non200',429,0,0),('malformed',502,1,1)):
            value=self.results['result-'+label]
            self.assertEqual((value['status'],value['responseBodyReads'],value['settingsReads']),(status,reads,settings_reads),label)
        for label in ('exact','near','null','empty'):
            value=self.results['result-error-'+label]
            self.assertEqual(value['status'],500 if label=='exact' else 502)
            self.assertEqual(value['audits'][0]['code'],'PII_SETTINGS_INVALID' if label=='exact' else 'POLICY_ERROR')
        for name,value in self.results.items():
            if name.startswith(('result-model-','result-settings-','result-allowed-')):
                self.assertNotIn('SENTINEL',json.dumps(value),name)

    def test_internal_input_settings_stop_before_body_and_only_the_fixed_id_is_500(self):
        for label in ("missing", "malformed", "null", "array", "scalar", "object-value", "max-missing", "max-null", "max-invalid"):
            with self.subTest(settings=label):
                result = self.results["settings-" + label]
                self.assertEqual((result["status"], result["inputBodyReads"], result["settingsReads"], result["requestBuilt"]), (500, 0, 1, False))
                self.assertEqual(result["body"]["error"]["code"], "PiiProcessingFailed")
                self.assertEqual(result["headers"]["Cache-Control"], "no-store")
                self.assertEqual(result["headers"]["apim-request-id"], result["requestId"])
                self.assertEqual([(a["phase"],a["code"],a["status"]) for a in result["audits"]], [("policy-error","PII_SETTINGS_INVALID",500)])
                self.assertNotIn("SENTINEL_", json.dumps(result))
        for label in ("null", "empty", "scalar"):
            self.assertEqual(self.results["languages-" + label]["status"], 200)
        for label, expected, reads in (("valid",200,2),("malformed",400,2),("missing-text",400,2),("missing-language",400,2),("wire-large",413,1),("compact-large",413,2)):
            result = self.results["probe-" + label]
            self.assertEqual((result["status"],result["inputBodyReads"],result["settingsReads"],result["requestBuilt"]),(expected,reads,1,expected==200))
        for label in ("exact", "similar", "null", "empty"):
            result = self.results["input-error-" + label]
            self.assertEqual(result["status"], 500 if label == "exact" else 502)
            self.assertEqual(result["body"]["error"]["code"], "PiiProcessingFailed" if label == "exact" else "PiiUpstreamFailure")
            self.assertEqual(result["audits"][0]["code"], "PII_SETTINGS_INVALID" if label == "exact" else "POLICY_ERROR")
            self.assertEqual(len(result["audits"]), 1)

    def test_valid_unicode_is_not_rejected_by_utf16_length(self):
        for name in ("emoji-boundary", "combining-boundary"):
            self.assertEqual(self.results[name]["status"], 200)

    def test_provider_size_includes_envelope_without_trial_language_or_unicode_limits(self):
        self.assertEqual(self.results["adopted-combining-long"]["status"], 200)
        self.assertEqual(self.results["adopted-combining-long"]["request"]["analysisInput"]["documents"][0]["language"], "fr")
        self.assertEqual(self.results["adopted-native-byte-0"]["status"], 200)
        self.assertEqual(self.results["adopted-native-byte-1"], {"status": 413})

    def test_schema_and_byte_rejections_prevent_upstream_request(self):
        for name in ("unsupported", "empty", "extra-field", "wrong-type", "native-rejected", "malformed-json", "query-rejected", "media-type"):
            self.assertEqual(self.results[name], {"status": 400}, name)
        self.assertEqual(self.results["too-many-bytes"], {"status": 413})

    def test_operation_independently_caps_wire_bytes_before_parsing(self):
        self.assertEqual(self.results["operation-wire-196608"]["status"], 200)
        for name in ("operation-wire-196609", "operation-wire-over-malformed"):
            self.assertEqual(self.results[name], {"status": 413})

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

    def test_adopted_model_and_warning_contract_never_accepts_truncation(self):
        result = self.results["adopted-unknown-warning"]
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["warningCodes"], [])
        self.assertEqual(result["body"]["detectedCount"], 0)
        self.assertNotIn("SENTINEL", json.dumps(result))
        for name, status, code in (("adopted-truncated", 413, "PiiInputTooLarge"),
                                   ("adopted-wrong-model", 502, "PiiInvalidUpstreamResult")):
            value = self.results[name]
            self.assertEqual(value["status"], status)
            self.assertEqual(value["body"]["error"], {"code": code})
            self.assertEqual(set(value["body"]), {"error", "requestId"})
            self.assertNotIn("SENTINEL", json.dumps(value))

    def test_entity_warning_shapes_and_detected_count_are_not_trusted(self):
        for name in ("entities-object", "warnings-object"):
            result = self.results[name]
            self.assertEqual(result["status"], 502)
            self.assertEqual(result["body"]["error"], {"code": "PiiInvalidUpstreamResult"})
            self.assertNotIn("SENTINEL", json.dumps(result))
        # 公開検出数は検証済みentities配列のCountから生成し、上流の同名追加欄を使いません。
        self.assertEqual(self.results["untrusted-detected-count"]["body"]["detectedCount"], 1)


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
        common = ET.fromstring(expand_common_authentication_fragments(render_service_policy(),
            render_common_authentication_fragments(entra_enabled=False), entra_enabled=False))
        anonymous = common.find("./inbound/choose[@id='core-common-authentication']")
        self.assertIsNotNone(anonymous)
        self.assertEqual(common.findall(".//validate-azure-ad-token"), [])
        self.assertEqual(common.findall(".//set-header[@name='WWW-Authenticate']"), [])
        for filename in ("generated.xml", "rest.xml"):
            tree = ET.fromstring(render_audit_policy(
                policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")))
            self.assertEqual(tree.findall(".//set-header[@name='WWW-Authenticate']"), [])
            if filename == "rest.xml":
                self.assertIsNotNone(tree.find("./inbound/include-fragment[@fragment-id='core-api-authentication-result']"))
                decision = REST_AUTH_EXPRESSION
            else:
                decision = tree.find("./inbound/set-variable[@name='gatewayDecision']").get("value")
            self.assertIn("context.Subscription", decision)

    def test_generated_mcp_never_buffers_protocol_response(self):
        text = (POLICIES / "api-templates" / "mcp/generated.xml").read_text()
        self.assertNotIn("context.Response.Body", text)
        tree = ET.fromstring(text)
        self.assertIsNone(tree.find("./outbound/set-body"))
        self.assertIsNone(tree.find("./inbound/set-body"))
        # 不正引数への固定JSON-RPCエラーだけは入口で生成し、正常の要求・応答は書き換えません。
        argument_rejections = [branch for branch in tree.findall('./inbound/choose/when')
                               if 'gatewayMcpArgumentError' in branch.get('condition', '')]
        self.assertEqual(len(argument_rejections), 1)
        early = argument_rejections[0].find('return-response/set-body')
        self.assertIsNotNone(early)
        self.assertIn("gatewayMcpArgumentError", early.text)
        publication = (POLICIES.parent / "api-publication.bicep").read_text()
        self.assertIn("apis/tools@2025-09-01-preview", publication)
        self.assertIn("operationId: '${apim.id}/apis/${api.name}/operations/${api.mcp.operationNames[0]}'", publication)
        self.assertIn("kind == 'pii' ? pii", publication)
        self.assertNotIn("Microsoft.Azure.ApiManagement.Gateway", text)
        schema = json.loads((POLICIES.parent / "schemas/pii.json").read_text())["schemas"]
        self.assertFalse(schema["PiiRedactRequest"]["additionalProperties"])
        self.assertNotIn("maxLength", schema["PiiRedactRequest"]["properties"]["text"])
        self.assertEqual(set(schema["PiiRedactResult"]["properties"]), {"redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"})


if __name__ == "__main__":
    unittest.main()
