"""実APIM式で文書の非同期受付・所有者チケット・結果の安全な取得を検証する。"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote
import xml.etree.ElementTree as ET

from test_gateway_limits_telemetry import PWSH
from test_standard_apis import standard_fixture
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.apim_document_contract import DOCUMENT_API_VERSION, DOCUMENT_API_VERSION_MARKER
from deployment.helpers.apim_audit import render_audit_policy
from test_credential_login import operation_error_methods,operation_error_cases,assert_shared_operation_error

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / 'apps/apim/policies/api-templates/rest/rest.xml'
START = ROOT / 'apps/apim/policies/operation-policies/document-analysis/document-analysis.xml'
STATUS = ROOT / 'apps/apim/policies/operation-policies/document-analysis/document-analysis-status.xml'
AUTHENTICATION_RESULT = ROOT / 'apps/apim/policies/fragments/authentication/core-api-authentication-result/core-api-authentication-result.xml'
API = json.loads((ROOT / 'apps/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8'))
SETTINGS = {**API['authorization']['operationConstraints']['analyze_document'],
            'apiVersion': DOCUMENT_API_VERSION, 'maxRequestBytes': 2 * 1024 * 1024, 'maxResponseBytes': 32768}
BASE = 'https://documents.example.com'
JOB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
LOCATION = BASE + '/documentintelligence/documentModels/prebuilt-layout/analyzeResults/' + JOB + '?api-version=' + DOCUMENT_API_VERSION
TEST_KEY = 'synthetic-document-signing-key-for-offline-tests'
TEST_MODELS = ('prebuilt-invoice', 'custom-model', 'a/b', 'a?x=y', 'a#b', 'a%2Fb',
               'synthetic custom/model?version=preview#name')


def legacy_scope(owner):
    """旧チケット契約の合成scope。購読の本人表記には接頭辞を含めません。"""
    value = BASE + '\nsynthetic-document-api\n' + owner
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


def statement(path, xpath, attribute=None):
    """実XMLの単文・複文式を、テスト用C#メソッドの本体として読み込む。"""
    node = (path if isinstance(path, ET.Element) else ET.fromstring(render_audit_policy(path.read_text(encoding='utf-8')))).find(xpath)
    value = (node.get(attribute) if attribute else node.text).strip().replace(DOCUMENT_API_VERSION_MARKER, DOCUMENT_API_VERSION)
    if value.startswith('@{') and value.endswith('}'):
        return value[2:-1]
    if value.startswith('@(') and value.endswith(')'):
        return 'return ' + value[2:-1] + ';'
    raise AssertionError('Expected a real APIM expression')


CSHARP = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public sealed class Body {
 public byte[] Data; public int Reads; public bool Preserved=true;
 public T As<T>(bool preserveContent=false) {
  Reads++; Preserved &= preserveContent;
  return (T)(typeof(T)==typeof(byte[]) ? (object)Data : typeof(T)==typeof(string) ? (object)Encoding.UTF8.GetString(Data) : (object)JObject.Parse(Encoding.UTF8.GetString(Data)));
 }
}
public sealed class Url { public Dictionary<string,string[]> Query=new Dictionary<string,string[]>(); }
public sealed class Request {
 public Body Body; public Url Url=new Url();
 public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
 public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>();
}
public sealed class Response {
 public int StatusCode; public Body Body;
 public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
}
public sealed class Named { public string Id; }
public sealed class LastError { public string Source="",Reason="",PolicyId=""; }
public sealed class Context {
 public Dictionary<string,object> Variables=new Dictionary<string,object>();
 public Request Request=new Request(); public Response Response;
 public Named Api=new Named{Id="synthetic-document-api"}; public Named Subscription=new Named{Id="synthetic-owner"};
 public Named Operation=new Named{Id="synthetic-document-operation"}; public LastError LastError=new LastError();
 public Guid RequestId=Guid.Parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
}
public static class Extensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> source,string key,string fallback) { return source.ContainsKey(key)?String.Join(" ",source[key]):fallback; }
 public static string GetValueOrDefault(this Dictionary<string,string> source,string key,string fallback) { return source.ContainsKey(key)?source[key]:fallback; }
}
public static class DocumentFixture {
 __OPERATION_ERROR_METHODS__
 static string AuthenticationResult(Context context) { __AUTHENTICATION_RESULT__ }
 static bool AuthenticationFailed(Context context) { __AUTHENTICATION_FAILED__ }
 static string AuthenticationRequestId(Context context) { __AUTHENTICATION_REQUEST_ID__ }
 static JObject AuthenticationFailure(Context context) {
  return new JObject(new JProperty("status",__AUTHENTICATION_HTTP_STATUS__),new JProperty("reason",__AUTHENTICATION_HTTP_REASON__),
    new JProperty("requestId",AuthenticationRequestId(context)),new JProperty("authenticationFailure",true),new JProperty("ownerReached",false));
 }
 static int Input(Context context) { __INPUT__ }
 static bool InputRejected(Context context) { __INPUT_REJECTED__ }
 static string InputAuditPhase(Context context) { __INPUT_AUDIT_PHASE__ }
 static string InputAuditCode(Context context) { __INPUT_AUDIT_CODE__ }
 static int InputAuditStatus(Context context) { __INPUT_AUDIT_STATUS__ }
 static int InputHttpStatus(Context context) { __INPUT_HTTP_STATUS__ }
 static string InputReason(Context context) { __INPUT_REASON__ }
 static string InputBody(Context context) { __INPUT_BODY__ }
 static string InputRequestId(Context context) { __INPUT_REQUEST_ID__ }
 static string StartUri(Context context) { __START_URI__ }
 static string PollUri(Context context) { __POLL_URI__ }
 static string OperationId(Context context) { __OPERATION_ID__ }
 static string Accepted(Context context) { __ACCEPTED__ }
 static int StartStatus(Context context) { __START_STATUS__ }
 static int StartRetry(Context context) { __START_RETRY__ }
 static string StartBody(Context context) { __START_BODY__ }
 static string StartReason(Context context) { __START_REASON__ }
 static bool StartProcessingError(Context context) { __START_PROCESSING_ERROR__ }
 static int StartProcessingAuditStatus(Context context) { __START_PROCESSING_AUDIT_STATUS__ }
 static string Owner(Context context) { __OWNER__ }
 static int OwnerStatus(Context context) { __OWNER_STATUS__ }
 static bool OwnerRejected(Context context) { __OWNER_REJECTED__ }
 static string OwnerErrorBody(Context context) { __OWNER_ERROR_BODY__ }
 static string OwnerReason(Context context) { __OWNER_REASON__ }
 static string OwnerAuditPhase(Context context) { __OWNER_AUDIT_PHASE__ }
 static string OwnerAuditCode(Context context) { __OWNER_AUDIT_CODE__ }
 static int OwnerAuditStatus(Context context) { __OWNER_AUDIT_STATUS__ }
 static bool StatusBodyClearRequested(Context context) { __STATUS_BODY_CLEAR_REQUESTED__ }
 static string StatusEmptyBody(Context context) { __STATUS_EMPTY_BODY__ }
 static string Evaluate(Context context) { __EVALUATE__ }
 static bool PollSettingsError(Context context) { __POLL_SETTINGS_ERROR__ }
 static int PollAuditStatus(Context context) { __POLL_AUDIT_STATUS__ }
 static string Encode(string value) { return Convert.ToBase64String(Encoding.UTF8.GetBytes(value)).TrimEnd('=').Replace('+','-').Replace('/','_'); }
 static JObject Claims(string ticket) {
  var encoded=ticket.Split('.')[0].Replace('-','+').Replace('_','/'); encoded+=new string('=',(4-encoded.Length%4)%4);
  return JObject.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
 }
 static string Sign(JObject payload,string domain="document-analysis-v1:") {
  var encoded=Encode(payload.ToString(Formatting.None));
  using(var hmac=new System.Security.Cryptography.HMACSHA256(Encoding.UTF8.GetBytes("__TEST_KEY__"))) {
   return encoded+"."+Convert.ToBase64String(hmac.ComputeHash(Encoding.UTF8.GetBytes(domain+encoded))).TrimEnd('=').Replace('+','-').Replace('/','_');
  }
 }
 static Context New(JObject item) {
  var c=new Context();
  c.Variables["gatewayOperationSelection"]="{\"settingId\":\"\"}";
  SetKey(c);
  if ((bool?)item["user"]==true) SetUser(c);
  c.Variables["gatewayDecision"]=AuthenticationResult(c);
  c.Variables["gatewayOperationConstraint"]=__SETTINGS__;
  if ((bool?)item["settingsMissing"]==true) c.Variables.Remove("gatewayOperationConstraint");
  else if (item.Property("settingsValue")!=null) c.Variables["gatewayOperationConstraint"]=item["settingsValue"].Type==JTokenType.String?(object)(string)item["settingsValue"]:item["settingsValue"].ToObject<object>();
  c.Variables["documentBaseUrl"]=__BASE__;
  c.Variables["documentModel"]=(string)item["model"]??"prebuilt-layout";
  if ((bool?)item["noDocumentModel"]==true) c.Variables.Remove("documentModel");
  else if (item.Property("documentModelValue")!=null) c.Variables["documentModel"]=item["documentModelValue"].ToObject<object>();
  c.Variables["documentOperationId"]=__JOB__;
  if(item["requestBody"]!=null)c.Request.Body=new Body{Data=Convert.FromBase64String((string)item["requestBody"])};
  c.Response=new Response{StatusCode=(int?)item["status"]??202,Body=new Body{Data=Encoding.UTF8.GetBytes(item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None))}};
  c.Response.Headers["Operation-Location"]=new[]{(string)item["location"]??__BASE__+"/documentintelligence/documentModels/"+Uri.EscapeDataString((string)item["model"]??"prebuilt-layout")+"/analyzeResults/"+__JOB__+"?api-version="+__API_VERSION__};
  c.Response.Headers["Retry-After"]=new[]{(string)item["retry"]??""};
  if ((bool?)item["nullResponse"]==true) c.Response=null;
  return c;
 }
 static void SetKey(Context c) {
  c.Subscription=new Named{Id="synthetic-owner"};
 }
 static void SetUser(Context c,string tenant="11111111-1111-4111-8111-111111111111",string principal="22222222-2222-4222-8222-222222222222") {
  // 共通認証済みの合成社員情報を準備します。トークン検証は共通認証の実式試験が担当します。
  c.Subscription=null; var caller="user:"+tenant+":"+principal;
  c.Variables["gatewayEntraAuthenticationValidated"]=true;
  c.Variables["gatewayUserAuthentication"]=new JObject(new JProperty("tenantId",tenant),new JProperty("principalId",principal),new JProperty("callerId",caller),new JProperty("allocationId",caller)).ToString();
 }
 static void InjectDecisionFault(Context context,JObject item) {
  var fault=(string)item["decisionFault"];
  if(fault=="missing")context.Variables.Remove("gatewayDecision");
  else if(fault=="malformed")context.Variables["gatewayDecision"]="{SENTINEL_INTERNAL_DECISION";
  else if(fault=="type")context.Variables["gatewayDecision"]=123;
  else if(fault=="method")context.Variables["gatewayDecision"]=new JObject(new JProperty("method","synthetic-unknown"),new JProperty("callerId","SENTINEL_INTERNAL_CALLER")).ToString();
  else if(fault=="short-caller")context.Variables["gatewayDecision"]=new JObject(new JProperty("method","subscriptionKey"),new JProperty("callerId","x")).ToString();
  else if(fault=="user-caller-missing")context.Variables["gatewayDecision"]=new JObject(new JProperty("method","user")).ToString();
  else if(fault=="user-caller-empty")context.Variables["gatewayDecision"]=new JObject(new JProperty("method","user"),new JProperty("callerId","")).ToString();
  else if(fault=="key-owner-empty")context.Variables["gatewayDecision"]=new JObject(new JProperty("method","subscriptionKey"),new JProperty("callerId","subscription:")).ToString();
 }
 public static string Run(string input) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var c=New(item); var mode=(string)item["mode"]??"input"; var name=(string)item["id"];
   if(mode=="error") {
    var error=(JObject)item["lastError"];c.LastError.Source=(string)error["source"];c.LastError.Reason=(string)error["reason"];c.LastError.PolicyId=(string)error["policyId"];
    var enabled=(bool?)item["entraEnabled"]!=false;
    results[name]=(string)item["errorOperation"]=="start"?(enabled?DocumentStartError(c):DocumentStartErrorDisabled(c)):(enabled?DocumentStatusError(c):DocumentStatusErrorDisabled(c));
   } else if(mode=="input") {
    var bytes=Convert.FromBase64String((string)item["data"]??"");
    if(item["requestSize"]!=null) { bytes=Enumerable.Repeat((byte)32,(int)item["requestSize"]).ToArray(); Encoding.ASCII.GetBytes("%PDF-").CopyTo(bytes,0); }
    c.Request.Body=(bool?)item["noBody"]==true?null:new Body{Data=bytes};
    if ((bool?)item["noMime"]!=true) c.Request.Headers["Content-Type"]=((JArray)(item["mimes"]??new JArray((string)item["mime"]??"application/pdf"))).Select(v=>(string)v).ToArray();
    if(item["encoding"]!=null)c.Request.Headers["Content-Encoding"]=new[]{(string)item["encoding"]};
    c.Request.Url.Query["model"]=((JArray)(item["models"]??new JArray((string)item["model"]??"prebuilt-layout"))).Select(v=>(string)v).ToArray();
    if((bool?)item["noModelQuery"]==true)c.Request.Url.Query.Clear();
    if(item["extraQuery"]!=null)c.Request.Url.Query[(string)item["extraQuery"]]=new[]{"untrusted"};
    if(item["additionalQueries"] is JObject)foreach(var query in ((JObject)item["additionalQueries"]).Properties())c.Request.Url.Query[query.Name]=((JArray)query.Value).Select(v=>(string)v).ToArray();
    var originalQuery=JObject.FromObject(c.Request.Url.Query);
    var decision=Input(c);
    c.Variables["documentInputStatus"]=decision;
    results[name]=new JObject(new JProperty("status",decision),new JProperty("reads",c.Request.Body==null?0:c.Request.Body.Reads),new JProperty("preserved",c.Request.Body==null||c.Request.Body.Preserved),new JProperty("bodyAfter",c.Request.Body==null?null:Convert.ToBase64String(c.Request.Body.Data)));
    results[name]["rejection"]=InputRejected(c)?new JObject(
      new JProperty("auditPhase",InputAuditPhase(c)),new JProperty("auditCode",InputAuditCode(c)),new JProperty("auditStatus",InputAuditStatus(c)),
      new JProperty("status",InputHttpStatus(c)),new JProperty("reason",InputReason(c)),new JProperty("body",JObject.Parse(InputBody(c))),new JProperty("requestId",InputRequestId(c))):null;
    if(decision==200)results[name]["uri"]=StartUri(c);
    results[name]["queryBefore"]=originalQuery;
    results[name]["queryAfter"]=JObject.FromObject(c.Request.Url.Query);
   } else if(mode=="location") results[name]=OperationId(c);
   else if(mode=="start") {
    if((bool?)item["anonymous"]==true)c.Subscription=null;
    c.Variables["gatewayDecision"]=AuthenticationResult(c);
    if(AuthenticationFailed(c)) { results[name]=AuthenticationFailure(c); continue; }
    InjectDecisionFault(c,item);
    c.Variables["documentOperationId"]=OperationId(c);
    var acceptedBefore=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    c.Variables["documentAccepted"]=Accepted(c);
    var acceptedAfter=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    c.Variables["documentStatus"]=StartStatus(c);
    results[name]=new JObject(new JProperty("status",c.Variables["documentStatus"]),new JProperty("reason",StartReason(c)),new JProperty("retryAfter",StartRetry(c)),new JProperty("body",JObject.Parse(StartBody(c))),new JProperty("rawBody",StartBody(c)),
      new JProperty("acceptedBefore",acceptedBefore),new JProperty("acceptedAfter",acceptedAfter),new JProperty("hasInboundTimestamp",c.Variables.ContainsKey("documentStartedAt")));
    results[name]["processingAudit"]=StartProcessingError(c)?new JObject(new JProperty("phase",__START_PROCESSING_AUDIT_PHASE__),
      new JProperty("code",__START_PROCESSING_AUDIT_CODE__),new JProperty("status",StartProcessingAuditStatus(c))):null;
   } else if(mode=="owner") {
    var accepted=JObject.Parse(Accepted(c)); var ticket=(string)accepted["ticket"];
    var mutation=(string)item["mutation"]??""; var claims=Claims(ticket); var now=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    if(mutation=="caller")c.Subscription.Id="other-owner";
    if(mutation=="api")c.Api.Id="other-api";
    if(mutation=="backend")c.Variables["documentBaseUrl"]="https://other.example.com";
    if(mutation=="anonymous")c.Subscription=null;
    if(mutation=="other-user")SetUser(c,principal:"33333333-3333-4333-8333-333333333333");
    if(mutation=="other-tenant")SetUser(c,tenant:"44444444-4444-4444-8444-444444444444");
    if(mutation=="user-to-key")SetKey(c);
    if(mutation=="key-to-user")SetUser(c);
    if(mutation=="unvalidated-user")c.Variables["gatewayEntraAuthenticationValidated"]=false;
    c.Variables["gatewayDecision"]=AuthenticationResult(c);
    if(AuthenticationFailed(c)) { results[name]=AuthenticationFailure(c); continue; }
    InjectDecisionFault(c,item);
    if(mutation=="altered"||mutation=="invalid-ticket-with-query")ticket=ticket.Substring(0,ticket.Length-1)+(ticket.EndsWith("A")?"B":"A");
    if(mutation=="model-unresigned") { claims["model"]="prebuilt-read"; ticket=Encode(claims.ToString(Formatting.None))+"."+ticket.Split('.')[1]; }
    if(mutation=="model-empty") { claims["model"]=""; ticket=Sign(claims); }
    if(mutation=="model-whitespace") { claims["model"]=" \t "; ticket=Sign(claims); }
    if(mutation=="model-dot") { claims["model"]="."; ticket=Sign(claims); }
    if(mutation=="model-dot-dot") { claims["model"]=".."; ticket=Sign(claims); }
    if(mutation=="audience") { claims["aud"]="realtime"; ticket=Sign(claims); }
    if(mutation=="domain")ticket=Sign(claims,"");
    if(mutation=="future") { claims["iat"]=now+120; claims["exp"]=now+120+86400; ticket=Sign(claims); }
    if(mutation=="lifetime") { claims["exp"]=(long)claims["iat"]+86401; ticket=Sign(claims); }
    if(mutation=="expired") { claims["iat"]=now-86401; claims["exp"]=now-1; ticket=Sign(claims); }
    if(mutation=="malformed")ticket="malformed";
    if(mutation=="oversize")ticket=new string('a',4097);
    if(item["legacyScope"]!=null) {
     claims=new JObject(new JProperty("v",1),new JProperty("aud","document-analysis-v1"),new JProperty("operationId",__JOB__),
       new JProperty("model","prebuilt-layout"),new JProperty("scope",(string)item["legacyScope"]),new JProperty("iat",now),new JProperty("exp",now+86400));
     ticket=Sign(claims);
    }
    if(mutation=="ticket-empty-array")c.Request.Headers["X-Core-Document-Ticket"]=new string[0];
    else if(mutation=="ticket-two-values")c.Request.Headers["X-Core-Document-Ticket"]=new[]{ticket,ticket};
    else if(mutation=="ticket-empty")c.Request.Headers["X-Core-Document-Ticket"]=new[]{""};
    else if(mutation!="ticket-missing")c.Request.Headers["X-Core-Document-Ticket"]=new[]{ticket};
    c.Request.MatchedParameters["operationId"]=mutation=="job"?"cccccccc-cccc-4ccc-8ccc-cccccccccccc":__JOB__;
    var queryBaselineOwner=mutation=="query"?Owner(c):null;
    var queryBaselineUri=mutation=="query"?PollUri(c):null;
    if(mutation=="query"||mutation=="invalid-ticket-with-query")c.Request.Url.Query["backend"]=new[]{"untrusted"};
    var owner=Owner(c); c.Variables["documentOwner"]=owner;
    var ownerStatus=OwnerStatus(c); c.Variables["documentOwnerStatus"]=ownerStatus;
    results[name]=new JObject(new JProperty("status",ownerStatus),new JProperty("owner",ownerStatus==200||ownerStatus==410?JObject.Parse(owner):null));
    if(ownerStatus==200||ownerStatus==410) {
     c.Variables["documentModel"]=(string)JObject.Parse(owner)["model"];
     results[name]["uri"]=PollUri(c);
    }
    var ownerRejected=OwnerRejected(c);
    results[name]["publicBody"]=ownerRejected?JObject.Parse(OwnerErrorBody(c)):null;
    if(ownerRejected)results[name]["reason"]=OwnerReason(c);
    results[name]["operationAudit"]=ownerRejected?new JObject(new JProperty("phase",OwnerAuditPhase(c)),
      new JProperty("code",OwnerAuditCode(c)),new JProperty("status",OwnerAuditStatus(c))):null;
    results[name]["auditCount"]=ownerRejected?__OWNER_AUDIT_INCLUDE_COUNT__:0;
    if(!ownerRejected) {
     // APIM標準set-bodyの転送処理は再実装せず、実際の条件式と空本文の値式だけを評価します。
     var clearBody=StatusBodyClearRequested(c);
     results[name]["bodyClear"]=new JObject(new JProperty("requested",clearBody),new JProperty("value",clearBody?StatusEmptyBody(c):null),
       new JProperty("reads",c.Request.Body==null?0:c.Request.Body.Reads));
    }
    if(mutation=="query") {
     results[name]["queryBaselineOwner"]=JObject.Parse(queryBaselineOwner);
     results[name]["queryBaselineUri"]=queryBaselineUri;
    }
   } else {
    if(item["publicBytes"]!=null||item["nativeBytes"]!=null) {
     var analysis=new JObject(new JProperty("apiVersion",__API_VERSION__),new JProperty("modelId","prebuilt-layout"),new JProperty("content",""),new JProperty("pages",new JArray()));
     var native=new JObject(new JProperty("status","succeeded"),new JProperty("analyzeResult",analysis));
     var projected=new JObject(new JProperty("analyzeResult",analysis),new JProperty("requestId",c.RequestId.ToString()));
     var target=item["publicBytes"]??item["nativeBytes"]; var envelope=item["publicBytes"]!=null?projected:native;
     analysis["content"]=new string('x',(int)target-Encoding.UTF8.GetByteCount(envelope.ToString(Formatting.None)));
     native["analyzeResult"]=analysis; c.Response.Body.Data=Encoding.UTF8.GetBytes(native.ToString(Formatting.None));
    }
    var evaluation=Evaluate(c);
    c.Variables["documentPollEvaluation"]=evaluation;
    results[name]=JObject.Parse(evaluation);
    results[name]["responseReads"]=c.Response==null||c.Response.Body==null?0:c.Response.Body.Reads;
    results[name]["settingsAudit"]=PollSettingsError(c)?new JObject(
      new JProperty("phase",__POLL_AUDIT_PHASE__),new JProperty("code",__POLL_AUDIT_CODE__),new JProperty("status",PollAuditStatus(c))):null;
   }
  }
  return results.ToString(Formatting.None);
 }
}
'''


def analysis_result(**changes):
    analysis = {'apiVersion': DOCUMENT_API_VERSION, 'modelId': 'prebuilt-layout', 'content': 'synthetic OCR', 'pages': [{'pageNumber': 1}], 'tables': []}
    analysis.update(changes)
    return {'status': 'succeeded', 'analyzeResult': analysis, 'untrustedTopLevel': 'SENTINEL_PRIVATE'}


class DocumentAnalysisPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest('PowerShell is required for real C# expressions')
        pdf = base64.b64encode(b'%PDF-1.7\nsynthetic').decode()
        cls.cases = [{'id': 'valid', 'data': pdf}, {'id': 'above-old-size', 'requestSize': 1048577},
            {'id': 'exact-size', 'requestSize': SETTINGS['maxRequestBytes']}, {'id': 'oversize', 'requestSize': SETTINGS['maxRequestBytes'] + 1},
            {'id': 'empty'}, {'id': 'no-body', 'noBody': True}, {'id': 'wrong-mime', 'data': pdf, 'mime': 'image/png'},
            {'id': 'unknown-mime', 'data': pdf, 'mime': 'text/plain'}, {'id': 'custom-model', 'data': pdf, 'model': 'custom-model'},
            {'id': 'invoice-model', 'data': pdf, 'model': 'prebuilt-invoice'},
            {'id': 'escaped-model', 'data': pdf, 'model': 'synthetic custom/model?version=preview#name'},
            {'id': 'empty-model', 'data': pdf, 'model': ''}, {'id': 'whitespace-model', 'data': pdf, 'model': ' \t '},
            {'id': 'dot-model', 'data': pdf, 'model': '.'}, {'id': 'dot-dot-model', 'data': pdf, 'model': '..'},
            {'id': 'duplicate-model', 'data': pdf, 'models': ['prebuilt-layout', 'prebuilt-read']}, {'id': 'missing-model', 'data': pdf, 'noModelQuery': True},
            {'id': 'extra-query', 'data': pdf, 'extraQuery': 'urlSource'},
            {'id': 'provider-queries', 'data': pdf, 'additionalQueries': {'pages': ['1-3'], 'features': ['ocrHighResolution', 'barcodes']}},
            {'id': 'fixed-query-input', 'data': pdf, 'additionalQueries': {'api-version': ['synthetic-client-version'],
             'stringIndexType': ['utf16CodeUnit'], 'pages': ['1', '3'], 'features': ['ocrHighResolution', 'barcodes']}},
            {'id': 'missing-model-with-query', 'data': pdf, 'noModelQuery': True, 'extraQuery': 'pages'},
            {'id': 'duplicate-model-with-query', 'data': pdf, 'models': ['prebuilt-layout', 'prebuilt-read'], 'extraQuery': 'pages'},
            {'id': 'custom-model-with-query', 'data': pdf, 'model': 'custom-model', 'extraQuery': 'pages'},
            {'id': 'compressed', 'data': pdf, 'encoding': 'gzip'},
            {'id': 'missing-content-type', 'data': pdf, 'noMime': True}, {'id': 'empty-content-type', 'data': pdf, 'mime': ''},
            {'id': 'duplicate-content-type', 'data': pdf, 'mimes': ['application/pdf', 'application/json']}]
        for name, body in (
                ('native-json-url', json.dumps({'urlSource': 'https://documents.example.com/synthetic.pdf'})),
                ('native-json-base64', json.dumps({'base64Source': pdf})),
                ('native-json-malformed', '{invalid-json')):
            cls.cases.append({'id': name, 'mime': 'application/json', 'data': base64.b64encode(body.encode()).decode()})
        cls.cases.append({'id': 'internal-model-missing', 'data': pdf, 'noDocumentModel': True})
        for name, value in (('number', 123), ('object', {'syntheticDetail': 'SENTINEL_INTERNAL_MODEL'})):
            cls.cases.append({'id': 'internal-model-' + name, 'data': pdf, 'documentModelValue': value})
        cls.cases.append({'id': 'unused-settings-not-read', 'data': pdf, 'settingsValue': '{SENTINEL_UNUSED_SETTINGS'})
        for model in TEST_MODELS:
            cls.cases.append({'id': 'model-' + model, 'data': pdf, 'model': model})
        signatures = {'application/pdf': b'%PDF-1.7', 'image/jpeg': b'\xff\xd8\xff' + b'\x00' * 5,
            'image/png': b'\x89PNG\r\n\x1a\n', 'image/tiff': b'II*\x00' + b'\x00' * 4, 'image/bmp': b'BM' + b'\x00' * 6,
            'image/heif': b'\x00\x00\x00\x18ftypheic', 'image/heic': b'\x00\x00\x00\x18ftypheic', 'text/html': b'<p>'}
        for mime in SETTINGS['contentTypes']:
            data = signatures.get(mime, b'PK\x03\x04' + b'\x00' * 4)
            cls.cases.append({'id': 'format-' + mime, 'mime': mime, 'data': base64.b64encode(data).decode()})
        for name, location in [('valid', LOCATION), ('host', LOCATION.replace('documents.example.com', 'other.example.com')),
                ('port', LOCATION.replace('.com/', '.com:444/')), ('http', LOCATION.replace('https:', 'http:')),
                ('user', LOCATION.replace('https://', 'https://user@')), ('model', LOCATION.replace('prebuilt-layout', 'prebuilt-read')),
                ('id', LOCATION.replace(JOB, 'secret/path')), ('query', LOCATION + '&secret=bad'),
                ('version', LOCATION.replace(DOCUMENT_API_VERSION, DOCUMENT_API_VERSION + '.invalid')),
                ('missing-query', LOCATION.split('?')[0]), ('fragment', LOCATION + '#private')]:
            cls.cases.append({'id': 'location-' + name, 'mode': 'location', 'location': location})
        cls.cases.extend([
            {'id': 'location-encoded-case', 'mode': 'location', 'model': 'a/b',
             'location': BASE + '/documentintelligence/documentModels/a%2fb/analyzeResults/' + JOB + '?api-version=' + DOCUMENT_API_VERSION},
            {'id': 'location-double-encoded-mismatch', 'mode': 'location', 'model': 'a/b',
             'location': BASE + '/documentintelligence/documentModels/a%252Fb/analyzeResults/' + JOB + '?api-version=' + DOCUMENT_API_VERSION},
            {'id': 'location-literal-percent-model', 'mode': 'location', 'model': 'a%2Fb',
             'location': BASE + '/documentintelligence/documentModels/a%252Fb/analyzeResults/' + JOB + '?api-version=' + DOCUMENT_API_VERSION},
        ])
        for model in TEST_MODELS:
            cls.cases.append({'id': 'location-model-' + model, 'mode': 'location', 'model': model})
        for status in (202, 400, 413, 415, 422, 429, 500):
            cls.cases.append({'id': 'start-' + str(status), 'mode': 'start', 'status': status, 'retry': '17', 'body': {'error': {'message': 'SENTINEL_PRIVATE'}}})
        cls.cases.extend([{'id': 'start-null', 'mode': 'start', 'nullResponse': True},
            {'id': 'start-invalid-location', 'mode': 'start', 'location': LOCATION + '&secret=bad'},
            {'id': 'start-anonymous', 'mode': 'start', 'anonymous': True},
            {'id': 'start-user', 'mode': 'start', 'user': True}])
        for mutation in ('', 'caller', 'api', 'backend', 'anonymous', 'job', 'altered', 'model-unresigned', 'model-empty', 'model-whitespace', 'model-dot', 'model-dot-dot', 'audience', 'domain', 'future', 'lifetime', 'expired', 'malformed', 'oversize', 'query',
                         'ticket-missing', 'ticket-empty-array', 'ticket-two-values', 'ticket-empty', 'invalid-ticket-with-query'):
            cls.cases.append({'id': 'owner-' + (mutation or 'valid'), 'mode': 'owner', 'mutation': mutation})
        for model in TEST_MODELS:
            cls.cases.extend([
                {'id': 'owner-model-' + model, 'mode': 'owner', 'model': model},
                {'id': 'poll-model-' + model, 'mode': 'poll', 'model': model, 'body': analysis_result(modelId=model), 'status': 200},
            ])
        cls.cases.append({'id': 'poll-model-mismatch', 'mode': 'poll', 'model': 'prebuilt-invoice',
                          'body': analysis_result(modelId='custom-model'), 'status': 200})
        for mutation in ('', 'other-user', 'other-tenant', 'user-to-key', 'unvalidated-user'):
            cls.cases.append({'id': 'user-owner-' + (mutation or 'valid'), 'mode': 'owner', 'user': True, 'mutation': mutation})
        cls.cases.append({'id': 'key-to-user', 'mode': 'owner', 'mutation': 'key-to-user'})
        cls.cases.append({'id': 'owner-with-body', 'mode': 'owner',
                          'requestBody': base64.b64encode(b'\x00\xffsynthetic arbitrary bytes\xfe').decode()})
        cls.cases.extend([
            {'id': 'owner-legacy-key', 'mode': 'owner', 'legacyScope': legacy_scope('synthetic-owner')},
            {'id': 'owner-legacy-user', 'mode': 'owner', 'user': True,
             'legacyScope': legacy_scope('user:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222')},
        ])
        for fault in ('missing', 'malformed', 'type', 'method', 'short-caller', 'user-caller-missing', 'user-caller-empty', 'key-owner-empty'):
            for mode in ('start', 'owner'):
                case = {'id': mode + '-decision-fault-' + fault, 'mode': mode, 'status': 202, 'decisionFault': fault}
                if fault.startswith('user-'):
                    case['user'] = True
                cls.cases.append(case)
        for name, body, status in [('success', analysis_result(), 200), ('running', {'status': 'running'}, 200),
                ('not-started', {'status': 'notStarted'}, 200), ('failed', {'status': 'failed', 'error': {'message': 'SENTINEL_PRIVATE'}}, 200),
                ('canceled', {'status': 'canceled'}, 200), ('wrong-version', analysis_result(apiVersion='preview'), 200),
                ('wrong-model-result', analysis_result(modelId='prebuilt-read'), 200), ('wrong-shape', analysis_result(pages='private'), 200),
                ('missing', {'error': {'message': 'SENTINEL_PRIVATE'}}, 404), ('expired', {}, 410), ('throttle', {}, 429), ('server-error', {}, 500)]:
            cls.cases.append({'id': 'poll-' + name, 'mode': 'poll', 'body': body, 'status': status, 'retry': '23'})
        cls.cases.extend([{'id': 'poll-null', 'mode': 'poll', 'nullResponse': True}, {'id': 'poll-malformed', 'mode': 'poll', 'status': 200, 'rawBody': '{bad-json'}])
        cls.cases.append({'id': 'poll-selected-limit', 'mode': 'poll', 'status': 200, 'body': analysis_result(),
                          'settingsValue': json.dumps({**SETTINGS, 'maxResponseBytes': 1})})
        cls.cases.extend([
            {'id': 'poll-settings-missing', 'mode': 'poll', 'status': 200, 'body': analysis_result(), 'settingsMissing': True},
            {'id': 'poll-settings-type', 'mode': 'poll', 'status': 200, 'body': analysis_result(), 'settingsValue': 123},
            {'id': 'poll-settings-json', 'mode': 'poll', 'status': 200, 'body': analysis_result(), 'settingsValue': '{SENTINEL_INTERNAL_SETTINGS'},
        ])
        for label,value in (('missing',{k:v for k,v in SETTINGS.items() if k!='maxResponseBytes'}),
                ('null',{**SETTINGS,'maxResponseBytes':None}),('type',{**SETTINGS,'maxResponseBytes':'SENTINEL_INTERNAL_LIMIT'}),
                ('overflow',{**SETTINGS,'maxResponseBytes':2147483648})):
            cls.cases.append({'id':'poll-settings-limit-'+label,'mode':'poll','status':200,'body':analysis_result(),'settingsValue':json.dumps(value)})
        for label,value in (('missing',{k:v for k,v in SETTINGS.items() if k!='apiVersion'}),
                ('null',{**SETTINGS,'apiVersion':None}),('type',{**SETTINGS,'apiVersion':20241130}),
                ('empty',{**SETTINGS,'apiVersion':''}),('blank',{**SETTINGS,'apiVersion':' '})):
            for upstream_version in ('present','missing'):
                response=analysis_result()
                if upstream_version=='missing':response['analyzeResult'].pop('apiVersion')
                cls.cases.append({'id':'poll-settings-version-'+label+'-'+upstream_version,'mode':'poll','status':200,'body':response,'settingsValue':json.dumps(value)})
        no_version=analysis_result();no_version['analyzeResult'].pop('apiVersion')
        cls.cases.append({'id':'poll-upstream-version-missing','mode':'poll','status':200,'body':no_version})
        for delta in (-1, 0, 1):
            cls.cases.append({'id': 'public-bytes-' + str(delta), 'mode': 'poll', 'status': 200, 'publicBytes': SETTINGS['maxResponseBytes'] + delta})
        cls.cases.append({'id': 'native-oversize', 'mode': 'poll', 'status': 200, 'nativeBytes': SETTINGS['maxResponseBytes'] + 1})
        from deployment.helpers.apim_document_input import render_document_input_validation_fragment
        from deployment.helpers.apim_audit import render_audit_policy
        input_fragment = ET.fromstring(render_document_input_validation_fragment())
        authentication_fragment = ET.fromstring(render_audit_policy(AUTHENTICATION_RESULT.read_text(encoding='utf-8')))
        authentication_failure = next(node for node in ET.parse(GATEWAY).findall('./inbound/choose/when')
                                      if node.find('./set-variable[@name="gatewayAuditCode"][@value="AUTHENTICATION_PROCESSING_ERROR"]') is not None)
        rejection = ET.parse(GATEWAY).find('./inbound/choose[@id="core-document-input-validation"]/when/choose/when')
        poll_settings_error = ET.parse(STATUS).find('./outbound/choose[@id="document-settings-read-error"]/when')
        start_processing_error = ET.parse(START).find('./outbound/choose[@id="document-authentication-processing-error"]/when')
        owner_rejection = ET.parse(STATUS).find('./inbound/choose[@id="document-operation-rejection"]/when')
        methods = {'INPUT': (input_fragment, './set-variable[@name="documentInputStatus"]', 'value'),
            'AUTHENTICATION_RESULT': (authentication_fragment, './set-variable[@name="gatewayDecision"]', 'value'),
            'AUTHENTICATION_FAILED': (authentication_failure, '.', 'condition'),
            'AUTHENTICATION_REQUEST_ID': (authentication_failure, './return-response/set-header[@name="apim-request-id"]/value', None),
            'INPUT_REJECTED': (rejection, '.', 'condition'),
            'INPUT_AUDIT_PHASE': (rejection, './set-variable[@name="gatewayAuditPhase"]', 'value'),
            'INPUT_AUDIT_CODE': (rejection, './set-variable[@name="gatewayAuditCode"]', 'value'),
            'INPUT_AUDIT_STATUS': (rejection, './set-variable[@name="gatewayAuditStatus"]', 'value'),
            'INPUT_HTTP_STATUS': (rejection, './return-response/set-status', 'code'),
            'INPUT_REASON': (rejection, './return-response/set-status', 'reason'),
            'INPUT_BODY': (rejection, './return-response/set-body', None),
            'INPUT_REQUEST_ID': (rejection, './return-response/set-header[@name="apim-request-id"]/value', None),
            'START_URI': (START, './inbound/rewrite-uri', 'template'),
            'POLL_URI': (STATUS, './inbound/rewrite-uri', 'template'),
            'OPERATION_ID': (START, './outbound/set-variable[@name="documentOperationId"]', 'value'),
            'ACCEPTED': (START, './outbound/set-variable[@name="documentAccepted"]', 'value'),
            'START_STATUS': (START, './outbound/set-variable[@name="documentStatus"]', 'value'),
            'START_RETRY': (START, './outbound/set-variable[@name="documentRetryAfter"]', 'value'),
            'START_BODY': (START, './outbound/return-response/set-body', None),
            'START_REASON': (START, './outbound/return-response/set-status', 'reason'),
            'START_PROCESSING_ERROR': (start_processing_error, '.', 'condition'),
            'START_PROCESSING_AUDIT_STATUS': (start_processing_error, './set-variable[@name="gatewayAuditStatus"]', 'value'),
            'OWNER': (STATUS, './inbound/set-variable[@name="documentOwner"]', 'value'),
            'OWNER_STATUS': (STATUS, './inbound/set-variable[@name="documentOwnerStatus"]', 'value'),
            'OWNER_REJECTED': (owner_rejection, '.', 'condition'),
            'OWNER_ERROR_BODY': (owner_rejection, './return-response/set-body', None),
            'OWNER_REASON': (owner_rejection, './return-response/set-status', 'reason'),
            'OWNER_AUDIT_PHASE': (owner_rejection, './set-variable[@name="gatewayAuditPhase"]', 'value'),
            'OWNER_AUDIT_CODE': (owner_rejection, './set-variable[@name="gatewayAuditCode"]', 'value'),
            'OWNER_AUDIT_STATUS': (owner_rejection, './set-variable[@name="gatewayAuditStatus"]', 'value'),
            'STATUS_BODY_CLEAR_REQUESTED': (STATUS, './inbound/choose[@id="document-status-body-clear"]/when', 'condition'),
            'STATUS_EMPTY_BODY': (STATUS, './inbound/choose[@id="document-status-body-clear"]/when/set-body', None),
            'EVALUATE': (STATUS, './outbound/set-variable[@name="documentPollEvaluation"]', 'value'),
            'POLL_SETTINGS_ERROR': (poll_settings_error, '.', 'condition'),
            'POLL_AUDIT_STATUS': (poll_settings_error, './set-variable[@name="gatewayAuditStatus"]', 'value')}
        error_methods=[]
        for operation,path in (('start',START),('status',STATUS)):
            method='Document'+operation.title()+'Error'
            error_methods.append(operation_error_methods(path.read_text(encoding='utf-8'),method))
            error_methods.append(operation_error_methods(path.read_text(encoding='utf-8'),method+'Disabled',entra_enabled=False))
            for label,last_error in operation_error_cases():
                cls.cases.append({'id':'operation-error-'+operation+'-'+label,'mode':'error','errorOperation':operation,
                                  'lastError':last_error,'requestBody':base64.b64encode(b'SENTINEL_ERROR_DETAIL').decode()})
            cls.cases.append({'id':'operation-error-'+operation+'-disabled','mode':'error','errorOperation':operation,
                              'entraEnabled':False,'lastError':operation_error_cases()[0][1]})
            for label,policy_id in (('exact','core-audit-body-model-read'),('similar','core-audit-body-model-read-extra'),('null',None),('empty','')):
                cls.cases.append({'id':'body-model-error-'+operation+'-'+label,'mode':'error','errorOperation':operation,
                                  'lastError':{'policyId':policy_id,'source':'set-variable','reason':'ExpressionValueEvaluationFailure'}})
        source = CSHARP.replace('__OPERATION_ERROR_METHODS__','\n'.join(error_methods))
        for name, args in methods.items():
            source = source.replace('__' + name + '__', statement(*args))
        auth_status = authentication_failure.find('./return-response/set-status')
        source = source.replace('__AUTHENTICATION_HTTP_STATUS__', str(int(auth_status.get('code'))))
        source = source.replace('__AUTHENTICATION_HTTP_REASON__', json.dumps(auth_status.get('reason')))
        for name, variable in (('POLL_AUDIT_PHASE', 'gatewayAuditPhase'), ('POLL_AUDIT_CODE', 'gatewayAuditCode')):
            source = source.replace('__' + name + '__', json.dumps(poll_settings_error.find('./set-variable[@name="' + variable + '"]').get('value')))
        for suffix, variable in (('PHASE', 'gatewayAuditPhase'), ('CODE', 'gatewayAuditCode')):
            source = source.replace('__START_PROCESSING_AUDIT_' + suffix + '__',
                                    json.dumps(start_processing_error.find('./set-variable[@name="' + variable + '"]').get('value')))
        source = source.replace('__OWNER_AUDIT_INCLUDE_COUNT__',
                                str(len(owner_rejection.findall('./include-fragment[@fragment-id="core-gateway-telemetry"]'))))
        source = source.replace('{{core-realtime-ticket-key}}', TEST_KEY).replace('__TEST_KEY__', TEST_KEY)
        for name, value in (('SETTINGS', json.dumps(SETTINGS)), ('BASE', BASE), ('JOB', JOB), ('LOCATION', LOCATION),
                            ('API_VERSION', DOCUMENT_API_VERSION)):
            source = source.replace('__' + name + '__', json.dumps(value))
        with tempfile.TemporaryDirectory(prefix='document-native-async-') as directory:
            folder = Path(directory)
            (folder / 'fixture.cs').write_text(source, encoding='utf-8')
            (folder / 'cases.json').write_text(json.dumps(cls.cases), encoding='utf-8')
            (folder / 'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701,1702'
[DocumentFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding='utf-8')
            run = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(folder / 'run.ps1')],
                                 capture_output=True, text=True, encoding='utf-8', timeout=60,
                                 env={**os.environ, 'DOTNET_BUNDLE_EXTRACT_BASE_DIR': str(folder / 'dotnet')})
            if run.returncode:
                raise AssertionError('Actual document policy C# compilation/execution failed:\n' + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_document_operations_handle_shared_entra_failures_before_dedicated_errors(self):
        for operation in ('start','status'):
            assert_shared_operation_error(self,self.results['operation-error-'+operation+'-entra'])
            for label,_ in operation_error_cases()[1:]:
                with self.subTest(operation=operation,error=label):
                    result=self.results['operation-error-'+operation+'-'+label]
                    expected=401 if label=='subscription' else 504 if label=='timeout' else 429 if label=='rate' else 502
                    self.assertFalse(result['sharedHandled'])
                    self.assertTrue(result['legacyReached'])
                    self.assertEqual(result['status'],expected)
                    if label=='rate':
                        self.assertFalse(result['hasBody'])
                        self.assertEqual(result['headers']['X-Core-Error-Code'],'RATE_LIMIT_EXCEEDED')
                        self.assertEqual(len(result['audits']),1)
                        self.assertEqual(result['audits'][0]['code'],'RATE_LIMIT_EXCEEDED')
                    else:
                        self.assertEqual(result['body']['error']['code'],'AuthenticationRequired' if expected==401 else 'DocumentAnalysisUnavailable')
                        self.assertEqual(result['audits'],[])
                    self.assertNotIn('WWW-Authenticate',result['headers'])
            disabled=self.results['operation-error-'+operation+'-disabled']
            self.assertFalse(disabled['sharedHandled'])
            self.assertTrue(disabled['legacyReached'])
            self.assertEqual(disabled['status'],502)
            for label in ('exact','similar','null','empty'):
                result=self.results['body-model-error-'+operation+'-'+label]
                self.assertEqual(result['status'],500 if label=='exact' else 502)
                if label=='exact':
                    self.assertFalse(result['hasBody'])
                    self.assertEqual(result['headers']['Cache-Control'],'no-store')
                    self.assertEqual([(a['phase'],a['code'],a['status']) for a in result['audits']],[('policy-error','OPERATION_SETTINGS_INVALID',500)])
                else:self.assertEqual(result['audits'],[])

    def assert_decision_faults(self, mode):
        for item in self.cases:
            if item.get('mode') != mode:
                continue
            result = self.results[item['id']]
            with self.subTest(internal_decision=item['id']):
                if mode == 'owner':
                    if result.get('authenticationFailure'):
                        self.assertFalse(result['ownerReached'])
                        self.assertIsNone(result.get('operationAudit'))
                        self.assertEqual(result.get('auditCount', 0), 0)
                        continue
                    status = result['status']
                    if status == 200:
                        self.assertIsNone(result['operationAudit'])
                        self.assertEqual(result['auditCount'], 0)
                        self.assertIsNone(result['publicBody'])
                    else:
                        audit_codes = {403: 'DOCUMENT_OPERATION_DENIED', 410: 'DOCUMENT_OPERATION_EXPIRED',
                                       500: 'AUTHENTICATION_PROCESSING_ERROR'}
                        public_codes = {403: 'DocumentOperationDenied', 410: 'DocumentOperationExpired',
                                        500: 'DocumentAnalysisUnavailable'}
                        self.assertIn(status, audit_codes)
                        self.assertEqual(result['operationAudit'],
                                         {'phase': 'policy-error' if status == 500 else 'rejected',
                                          'code': audit_codes[status], 'status': status})
                        self.assertEqual(result['auditCount'], 1)
                        self.assertEqual(result['reason'],
                                         'Document analysis unavailable' if status == 500 else 'Document operation access denied')
                        self.assertEqual(result['publicBody'],
                                         {'error': {'code': public_codes[status]},
                                          'requestId': self.results['start-202']['body']['requestId']})
                        self.assertNotIn('SENTINEL', json.dumps(result['publicBody']))
                        self.assertNotIn('Exception', json.dumps(result['publicBody']))
                if item.get('decisionFault'):
                    self.assertEqual(result['status'], 500)
                    self.assertEqual(result['reason'], 'Document analysis unavailable')
                    self.assertEqual(result['processingAudit'] if mode == 'start' else result['operationAudit'],
                                     {'phase': 'policy-error', 'code': 'AUTHENTICATION_PROCESSING_ERROR', 'status': 500})
                    public = result['body'] if mode == 'start' else result['publicBody']
                    self.assertEqual(set(public), {'error', 'requestId'})
                    self.assertEqual(public['error'], {'code': 'DocumentAnalysisUnavailable'})
                    self.assertEqual(public['requestId'], self.results['start-202']['body']['requestId'])
                    self.assertNotIn('AUTHENTICATION_PROCESSING_ERROR', json.dumps(public))
                    self.assertNotIn('SENTINEL', json.dumps(result))
                    self.assertNotIn('Exception', json.dumps(result))
                    if mode == 'owner':
                        self.assertIsNone(result['owner'])
                elif mode == 'start':
                    self.assertIsNone(result.get('processingAudit'))

    def test_document_gate_delegates_body_format_and_size_without_reading(self):
        for name in ('valid', 'above-old-size', 'exact-size', 'oversize', 'empty', 'no-body', 'wrong-mime',
                     'unknown-mime', 'compressed', 'missing-content-type', 'empty-content-type', 'duplicate-content-type',
                     *('format-' + mime for mime in SETTINGS['contentTypes'])):
            self.assertEqual(self.results[name]['status'], 200, name)
            self.assertTrue(self.results[name]['preserved'])
        # 共通の申告サイズによる413は別部品です。Document個別ゲートは全量を読みません。
        for item in self.cases:
            if item.get('mode', 'input') == 'input':
                self.assertEqual(self.results[item['id']]['reads'], 0, item['id'])
        for name in ('empty-model', 'whitespace-model', 'dot-model', 'dot-dot-model', 'duplicate-model', 'missing-model'):
            self.assertEqual(self.results[name]['status'], 400, name)

    def test_additional_query_parameters_do_not_reject_or_relax_model_checks(self):
        for name in ('extra-query', 'provider-queries', 'fixed-query-input', 'custom-model-with-query'):
            self.assertEqual(self.results[name]['status'], 200, name)
            self.assertEqual(self.results[name]['reads'], 0, name)
            item = next(item for item in self.cases if item['id'] == name)
            self.assertEqual(self.results[name]['bodyAfter'], item['data'], name)
            self.assertEqual(self.results[name]['queryAfter'], self.results[name]['queryBefore'], name)
        for name in ('missing-model-with-query', 'duplicate-model-with-query'):
            self.assertEqual(self.results[name]['status'], 400, name)

    def test_provider_and_custom_models_pass_without_a_gateway_model_list(self):
        for name in ('invoice-model', 'custom-model', 'escaped-model', 'unused-settings-not-read'):
            self.assertEqual(self.results[name]['status'], 200, name)
            self.assertEqual(self.results[name]['reads'], 0, name)
        for model in TEST_MODELS:
            self.assertEqual(self.results['model-' + model]['status'], 200, model)
            owner = self.results['owner-model-' + model]
            self.assertEqual(owner['status'], 200, model)
            self.assertEqual(owner['owner']['model'], model)
            poll = self.results['poll-model-' + model]
            self.assertEqual(poll['status'], 200, model)
            self.assertEqual(json.loads(poll['result'])['analyzeResult']['modelId'], model)
        self.assertEqual(self.results['poll-model-mismatch']['status'], 502)

    def test_model_is_escaped_as_one_uri_segment_for_submit_and_poll(self):
        for model in TEST_MODELS:
            encoded = quote(model, safe='')
            self.assertEqual(self.results['model-' + model]['uri'],
                             '/documentintelligence/documentModels/' + encoded + ':analyze', model)
            self.assertEqual(self.results['owner-model-' + model]['uri'],
                             '/documentintelligence/documentModels/' + encoded + '/analyzeResults/' + JOB + '?api-version=' + DOCUMENT_API_VERSION, model)

    def test_provider_json_inputs_are_forwarded_without_parsing_or_conversion(self):
        for name in ('native-json-url', 'native-json-base64', 'native-json-malformed'):
            item = next(item for item in self.cases if item['id'] == name)
            self.assertEqual(self.results[name]['status'], 200, name)
            self.assertEqual(self.results[name]['reads'], 0, name)
            self.assertEqual(self.results[name]['bodyAfter'], item['data'], name)

    def test_internal_model_handoff_failures_are_500_without_body_reads(self):
        for item in self.cases:
            if item['id'].startswith('internal-model-'):
                with self.subTest(internal_model=item['id']):
                    result = self.results[item['id']]
                    self.assertEqual(result['status'], 500)
                    self.assertEqual(result['reads'], 0)
                    self.assertEqual(result['bodyAfter'], item['data'])

    def test_input_failure_response_and_audit_use_cause_specific_safe_values(self):
        for item in self.cases:
            if item.get('mode', 'input') != 'input':
                continue
            result = self.results[item['id']]
            with self.subTest(case=item['id']):
                if result['status'] == 200:
                    self.assertIsNone(result['rejection'])
                    continue
                is_settings_error = item['id'].startswith('internal-model-')
                expected_status = 500 if is_settings_error else 400
                public = result['rejection']
                self.assertEqual(public['status'], expected_status)
                self.assertEqual(public['auditStatus'], expected_status)
                self.assertEqual(public['auditPhase'], 'policy-error' if is_settings_error else 'rejected')
                self.assertEqual(public['auditCode'], 'OPERATION_SETTINGS_INVALID' if is_settings_error else 'INVALID_REQUEST')
                self.assertEqual(public['reason'], 'Document operation settings are invalid' if is_settings_error else 'Invalid document input')
                self.assertEqual(set(public['body']), {'error', 'requestId'})
                self.assertEqual(public['body']['error'], {'code': 'DocumentAnalysisUnavailable' if is_settings_error else 'DocumentInvalidInput'})
                self.assertEqual(public['body']['requestId'], public['requestId'])
                self.assertNotIn('SENTINEL', json.dumps(public))
                self.assertNotIn('Exception', json.dumps(public))
                self.assertNotIn('gatewayOperationConstraint', json.dumps(public))
        response = ET.parse(GATEWAY).find('./inbound/choose[@id="core-document-input-validation"]/when/choose/when/return-response')
        self.assertEqual(response.find('./set-header[@name="Content-Type"]/value').text, 'application/json')
        self.assertEqual(response.find('./set-header[@name="Cache-Control"]/value').text, 'no-store')

    def test_operation_location_is_fixed_to_the_provider_model_uuid_and_version(self):
        self.assertEqual(self.results['location-valid'], JOB)
        for name in ('host', 'port', 'http', 'user', 'model', 'id', 'query', 'version', 'missing-query', 'fragment'):
            self.assertEqual(self.results['location-' + name], '', name)
        for model in TEST_MODELS:
            self.assertEqual(self.results['location-model-' + model], JOB, model)
        self.assertEqual(self.results['location-encoded-case'], JOB)
        self.assertEqual(self.results['location-literal-percent-model'], JOB)
        self.assertEqual(self.results['location-double-encoded-mismatch'], '')

    def test_start_accepts_once_and_issues_owner_ticket_for_24_hours(self):
        accepted = self.results['start-202']
        self.assertEqual(accepted['status'], 202)
        self.assertEqual(accepted['retryAfter'], 17)
        body = json.loads(accepted['rawBody'])
        self.assertEqual(body['operationId'], JOB)
        self.assertEqual(body['statusPath'], '/tools/documents/v1/operations/' + JOB)
        self.assertRegex(body['expiresAt'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z$')
        self.assertNotIn(BASE, json.dumps(body))
        claims = json.loads(base64.urlsafe_b64decode(body['ticket'].split('.')[0] + '==='))
        self.assertGreaterEqual(claims['iat'], accepted['acceptedBefore'])
        self.assertLessEqual(claims['iat'], accepted['acceptedAfter'])
        self.assertEqual(claims['exp'] - claims['iat'], 86400)
        self.assertFalse(accepted['hasInboundTimestamp'])
        self.assertEqual(claims['aud'], 'document-analysis-v1')
        self.assertEqual(claims['scope'], legacy_scope('synthetic-owner'))
        user_start = self.results['start-user']
        self.assertEqual(user_start['status'], 202)
        user_claims = json.loads(base64.urlsafe_b64decode(user_start['body']['ticket'].split('.')[0] + '==='))
        self.assertEqual(user_claims['scope'], legacy_scope('user:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222'))
        for name, status in [('400', 422), ('413', 413), ('415', 422), ('422', 422), ('429', 429), ('500', 502), ('null', 504), ('invalid-location', 502)]:
            result = self.results['start-' + name]
            self.assertEqual(result['status'], status, name)
            self.assertEqual(set(result['body']), {'error', 'requestId'})
            self.assertNotIn('SENTINEL', json.dumps(result))
        self.assertEqual(self.results['start-429']['retryAfter'], 17)
        self.assertEqual(self.results['start-null']['retryAfter'], 2)
        failure = self.results['start-anonymous']
        self.assertEqual(failure['status'], 500)
        self.assertTrue(failure['authenticationFailure'])
        self.assertFalse(failure['ownerReached'])
        self.assertEqual(failure['requestId'], body['requestId'])
        self.assert_decision_faults('start')

    def test_job_ticket_checks_owner_api_backend_model_job_signature_domain_and_lifetime(self):
        self.assertEqual(self.results['owner-valid']['status'], 200)
        self.assertEqual(self.results['owner-expired']['status'], 410)
        self.assertEqual(self.results['owner-legacy-key']['status'], 200)
        self.assertEqual(self.results['owner-legacy-key']['owner']['scope'], legacy_scope('synthetic-owner'))
        anonymous = self.results['owner-anonymous']
        self.assertEqual(anonymous['status'], 500)
        self.assertTrue(anonymous['authenticationFailure'])
        self.assertFalse(anonymous['ownerReached'])
        for mutation in ('caller', 'api', 'backend', 'job', 'altered', 'model-unresigned', 'model-empty', 'model-whitespace', 'model-dot', 'model-dot-dot', 'audience', 'domain', 'future', 'lifetime', 'malformed', 'oversize',
                         'ticket-missing', 'ticket-empty-array', 'ticket-two-values', 'ticket-empty', 'invalid-ticket-with-query'):
            self.assertEqual(self.results['owner-' + mutation]['status'], 403, mutation)
        query = self.results['owner-query']
        self.assertEqual(query['status'], 200)
        self.assertEqual(query['owner'], query['queryBaselineOwner'])
        self.assertEqual(query['uri'], query['queryBaselineUri'])
        self.assertEqual(query['uri'], self.results['owner-valid']['uri'])
        for name in ('v', 'aud', 'operationId', 'model', 'scope'):
            self.assertEqual(query['owner'][name], self.results['owner-valid']['owner'][name])
        self.assert_decision_faults('owner')

    def test_polling_returns_safe_running_result_or_fixed_error(self):
        success = self.results['poll-success']
        self.assertEqual(success['status'], 200)
        self.assertEqual(success['responseReads'],2)
        self.assertEqual(self.results['poll-upstream-version-missing']['status'],502)
        self.assertEqual(self.results['poll-upstream-version-missing']['responseReads'],2)
        body = json.loads(success['result'])
        self.assertEqual(set(body), {'analyzeResult', 'requestId'})
        self.assertEqual(body['analyzeResult']['content'], 'synthetic OCR')
        self.assertEqual(body['analyzeResult']['apiVersion'], DOCUMENT_API_VERSION)
        self.assertNotIn('SENTINEL', success['result'])
        for name in ('running', 'not-started'):
            running = self.results['poll-' + name]
            self.assertEqual(running['status'], 202)
            self.assertEqual(json.loads(running['result'])['operationId'], JOB)
        for name, status in [('failed', 422), ('canceled', 422), ('wrong-version', 502), ('wrong-model-result', 502), ('wrong-shape', 502), ('missing', 404), ('expired', 410), ('throttle', 429), ('server-error', 502), ('null', 504), ('malformed', 502)]:
            result = self.results['poll-' + name]
            self.assertEqual(result['status'], status, name)
            self.assertEqual(set(json.loads(result['result'])), {'error', 'requestId'})
            self.assertNotIn('SENTINEL', result['result'])
        self.assertEqual(self.results['poll-throttle']['retryAfter'], 23)
        for item in self.cases:
            if item.get('mode') != 'poll':
                continue
            result = self.results[item['id']]
            with self.subTest(poll=item['id']):
                self.assertEqual(set(result), {'status', 'result', 'retryAfter', 'settingsAudit', 'responseReads'})
                if item['id'].startswith('poll-settings-'):
                    self.assertEqual(result['status'], 500)
                    self.assertEqual(result['responseReads'],0)
                    self.assertEqual(result['settingsAudit'], {'phase': 'policy-error', 'code': 'OPERATION_SETTINGS_INVALID', 'status': 500})
                    public = json.loads(result['result'])
                    self.assertEqual(set(public), {'error', 'requestId'})
                    self.assertEqual(public['error'], {'code': 'DocumentAnalysisUnavailable'})
                    self.assertEqual(public['requestId'], body['requestId'])
                    self.assertNotIn('SENTINEL', json.dumps(result))
                    self.assertNotIn('Exception', json.dumps(result))
                else:
                    self.assertIsNone(result['settingsAudit'])
        response = ET.parse(STATUS).find('./outbound/return-response')
        self.assertEqual(self.results['poll-selected-limit']['responseReads'],1)
        self.assertEqual(self.results['native-oversize']['responseReads'],1)
        self.assertEqual(response.find('./set-header[@name="Cache-Control"]/value').text, 'no-store')

    def test_user_job_is_bound_to_tenant_and_person_across_token_refresh(self):
        """本人だけが結果を取得でき、別人・別tenant・認証方式変更を拒否する。"""
        self.assertEqual(self.results['user-owner-valid']['status'], 200)
        self.assertEqual(self.results['owner-legacy-user']['status'], 200)
        self.assertEqual(self.results['owner-legacy-user']['owner']['scope'],
                         legacy_scope('user:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222'))
        for mutation in ('other-user', 'other-tenant', 'user-to-key'):
            self.assertEqual(self.results['user-owner-' + mutation]['status'], 403)
        self.assertEqual(self.results['key-to-user']['status'], 403)
        failure = self.results['user-owner-unvalidated-user']
        self.assertEqual(failure['status'], 500)
        self.assertTrue(failure['authenticationFailure'])
        self.assertFalse(failure['ownerReached'])

    def test_response_budget_includes_native_and_final_public_envelopes(self):
        for delta in (-1, 0):
            result = self.results['public-bytes-' + str(delta)]
            self.assertEqual(result['status'], 200)
            self.assertEqual(len(result['result'].encode()), SETTINGS['maxResponseBytes'] + delta)
        for name in ('public-bytes-1', 'native-oversize', 'poll-selected-limit'):
            self.assertEqual(self.results[name]['status'], 502)
            self.assertEqual(set(json.loads(self.results[name]['result'])), {'error', 'requestId'})

    def test_native_http_transport_has_no_internal_retry_resubmit_or_base64_file_wrapper(self):
        for path in (START, STATUS):
            tree = ET.parse(path)
            self.assertIsNone(tree.find('.//retry'))
            self.assertIsNone(tree.find('.//send-request'))
            self.assertIsNone(tree.find('.//trace'))
            self.assertIsNotNone(tree.find('./backend/base'))
            self.assertEqual(list(tree.find('inbound'))[0].tag, 'base')
            self.assertEqual(tree.find('./inbound/rewrite-uri').get('copy-unmatched-params'),
                             'true' if path == START else 'false')
        self.assertIsNone(ET.parse(START).find('./inbound/set-body'))
        self.assertNotIn('base64Source', START.read_text())
        self.assertIsNotNone(ET.parse(STATUS).find('./inbound/set-header[@name="X-Core-Document-Ticket"][@exists-action="delete"]'))
        for name, has_body in (('owner-valid', False), ('owner-with-body', True)):
            with self.subTest(status_request_body=name):
                result = self.results[name]
                self.assertEqual(result['status'], 200)
                self.assertEqual(result['bodyClear'], {'requested': has_body, 'value': '' if has_body else None, 'reads': 0})
                self.assertEqual(result['uri'], self.results['owner-valid']['uri'])
                for field in ('operationId', 'model', 'scope'):
                    self.assertEqual(result['owner'][field], self.results['owner-valid']['owner'][field])

    def test_submit_uses_standard_query_copy_after_deleting_client_model(self):
        """XML配線と実式のみを検証する。APIM標準コピーの実転送はAzureで別途確認する。"""
        inbound = ET.parse(START).find('inbound')
        rewrite = inbound.find('rewrite-uri')
        model_delete = inbound.find('./set-query-parameter[@name="model"][@exists-action="delete"]')
        self.assertIsNotNone(model_delete)
        self.assertEqual(list(inbound).index(model_delete) + 1, list(inbound).index(rewrite))
        self.assertEqual(rewrite.get('copy-unmatched-params'), 'true')
        self.assertIsNone(model_delete.find('value'))
        self.assertEqual([node.get('name') for node in inbound.findall('set-query-parameter')],
                         ['model', 'api-version', 'stringIndexType'])
        query_settings = inbound.findall('set-query-parameter')[1:]
        self.assertEqual([(node.get('name'), node.get('exists-action')) for node in query_settings],
                         [('api-version', 'override'), ('stringIndexType', 'skip')])
        for index, (node, value) in enumerate(zip(query_settings, (DOCUMENT_API_VERSION_MARKER, 'unicodeCodePoint')), start=1):
            self.assertEqual(list(inbound).index(rewrite) + index, list(inbound).index(node))
            self.assertEqual([item.text for item in node.findall('value')], [value])
        # 実式はパスだけを作ります。コピー後のAPI版固定と未指定の形式補完を標準XMLへ定義します。
        result = self.results['fixed-query-input']
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['uri'], '/documentintelligence/documentModels/prebuilt-layout:analyze')
        self.assertEqual(result['queryAfter']['pages'], ['1', '3'])
        self.assertEqual(result['queryAfter']['features'], ['ocrHighResolution', 'barcodes'])
        self.assertEqual(result['queryAfter'], result['queryBefore'])
        self.assertNotIn('synthetic-client-version', result['uri'])
        self.assertNotIn('utf16CodeUnit', result['uri'])
        # 状態取得は追加クエリをコピーせず、同じチケットと操作IDから固定URIを作ります。
        self.assertEqual(ET.parse(STATUS).find('./inbound/rewrite-uri').get('copy-unmatched-params'), 'false')
        self.assertEqual(self.results['owner-query']['status'], 200)

    def test_document_gate_precedes_rate_limits_backend_authentication_and_acceptance(self):
        inbound = ET.parse(GATEWAY).find('inbound')
        blocks = list(inbound)
        target_validation = inbound.find("choose[@id='core-api-target-validation-selection']")
        gate = inbound.find('./choose[@id="core-document-input-validation"]')
        self.assertIsNotNone(gate)
        constraint = inbound.find('./set-variable[@name="gatewayOperationConstraint"]')
        authentication = inbound.find('./include-fragment[@fragment-id="core-api-authentication-result"]')
        self.assertLess(blocks.index(authentication), blocks.index(gate))
        self.assertLess(blocks.index(inbound.find('./include-fragment[@fragment-id="core-api-operation-settings"]')), blocks.index(constraint))
        self.assertIn('gatewayOperationSelection', constraint.get('value'))
        self.assertIn('["constraint"]', constraint.get('value'))
        self.assertLess(blocks.index(inbound.find('./include-fragment[@fragment-id="core-model-limits"]')), blocks.index(gate))
        self.assertLess(blocks.index(target_validation), blocks.index(gate))
        self.assertLess(blocks.index(gate), blocks.index(inbound.find('./include-fragment[@fragment-id="core-rate-limit"]')))
        self.assertLess(blocks.index(gate), blocks.index(inbound.find('./set-variable[@name="gatewayAuditPhase"][@value="accepted"]')))
        serialized = ET.tostring(inbound, encoding='unicode')
        self.assertLess(serialized.index('id="core-document-input-validation"'), serialized.index('__DEFAULT_BACKEND_XML__'))
        self.assertLess(serialized.index('id="core-document-input-validation"'), serialized.index('fragment-id="core-credential-connection"'))
        self.assertIn('== "documentAnalysis"', gate.find('when').get('condition'))
        self.assertIsNotNone(gate.find('./when/include-fragment[@fragment-id="core-document-input-validation"]'))
        self.assertIsNotNone(gate.find('./when/choose/when/return-response/set-status'))
        self.assertIsNone(ET.parse(START).find('./inbound/set-variable[@name="documentInputStatus"]'))
        self.assertNotIn('Body.As', START.read_text(encoding='utf-8'))


class DocumentAnalysisPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bicep_helpers import compile_bicep
        cls.template = compile_bicep('apps/apim/api-publication.bicep')
        resources = cls.template['resources']
        cls.resources = list(resources.values() if isinstance(resources, dict) else resources)
        cls.policy = next(item for item in cls.resources if item.get('copy', {}).get('name') == 'operationPolicies')
        raw, _ = standard_fixture()
        raw['apiDefinitions'] = [copy.deepcopy(API)]
        cls.api = normalize_configuration(raw)['apiDefinitions'][0]

    def evaluate(self, operation_name):
        from arm_helpers import PolicyExpression
        entry = {'apiName': self.api['name'], 'backendUrl': BASE,
                 'operation': next(operation for operation in self.api['operations'] if operation['name'] == operation_name),
                 'constraint': copy.deepcopy(self.api['authorization']['operationConstraints'][operation_name])}
        return PolicyExpression(self.policy['properties']['value'], self.template['variables'],
            cache={'operationEntries': [entry]}, functions=self.template['functions']).evaluate({'entraAuthentication': {}})

    def test_generated_submit_policy_retains_query_copy_version_override_and_index_default(self):
        """生成XMLの配線を検証する。APIM標準コピーの実転送はAzureで別途確認する。"""
        rendered = self.evaluate('analyze_document')
        inbound = ET.fromstring(rendered).find('inbound')
        parameters = inbound.findall('set-query-parameter')
        self.assertEqual([(node.get('name'), node.get('exists-action'), [value.text for value in node.findall('value')])
                          for node in parameters],
                         [('model', 'delete', []), ('api-version', 'override', [DOCUMENT_API_VERSION]),
                          ('stringIndexType', 'skip', ['unicodeCodePoint'])])
        rewrite = inbound.find('rewrite-uri')
        start = list(inbound).index(parameters[0])
        self.assertEqual(list(inbound)[start:start + 4], [parameters[0], rewrite, parameters[1], parameters[2]])
        self.assertEqual(rewrite.get('copy-unmatched-params'), 'true')
        self.assertIn('Uri.EscapeDataString', rewrite.get('template'))
        self.assertNotIn('?', rewrite.get('template'))
        self.assertNotIn('Body.As', rendered)
        self.assertNotIn('documentStartedAt', rendered)
        self.assertIsNone(inbound.find('set-body'))

    def test_generated_submit_status_and_public_schema_use_the_canonical_version(self):
        from arm_helpers import PolicyExpression
        rendered_start = self.evaluate('analyze_document')
        rendered_status = self.evaluate('get_document_operation')
        for rendered in (rendered_start, rendered_status):
            self.assertNotIn(DOCUMENT_API_VERSION_MARKER, rendered)
        start = ET.fromstring(rendered_start)
        status = ET.fromstring(rendered_status)
        for tree, stage, status_name, audit_id in (
                (start, 'outbound', 'documentStatus', 'document-authentication-processing-error'),
                (status, 'inbound', 'documentOwnerStatus', 'document-operation-rejection')):
            section = tree.find(stage)
            audits = section.findall('./choose[@id="' + audit_id + '"]')
            self.assertEqual(len(audits), 1)
            audit = audits[0]
            response_block = next(node for node in section if node.tag == 'return-response' or node.find('./when/return-response') is not None)
            self.assertLess(list(section).index(section.find('./set-variable[@name="' + status_name + '"]')), list(section).index(audit))
            branch = audit.find('when')
            self.assertIn(status_name, branch.get('condition'))
            if stage == 'outbound':
                self.assertLess(list(section).index(audit), list(section).index(response_block))
                self.assertIn('== 500', branch.get('condition'))
                self.assertEqual([(node.get('name'), node.get('value')) for node in branch.findall('set-variable')],
                                 [('gatewayAuditPhase', 'policy-error'), ('gatewayAuditCode', 'AUTHENTICATION_PROCESSING_ERROR'),
                                  ('gatewayAuditStatus', '@(500)')])
            else:
                self.assertIsNone(section.find('./choose[@id="document-authentication-processing-error"]'))
                self.assertIs(response_block, audit)
                self.assertIn('!= 200', branch.get('condition'))
                self.assertEqual([node.tag for node in branch],
                                 ['set-variable', 'set-variable', 'set-variable', 'include-fragment', 'return-response'])
                variables = branch.findall('set-variable')
                self.assertEqual([node.get('name') for node in variables],
                                 ['gatewayAuditPhase', 'gatewayAuditCode', 'gatewayAuditStatus'])
                for node in variables:
                    self.assertIn(status_name, node.get('value'))
                self.assertIn('policy-error', variables[0].get('value'))
                self.assertIn('rejected', variables[0].get('value'))
                for code in ('AUTHENTICATION_PROCESSING_ERROR', 'DOCUMENT_OPERATION_EXPIRED', 'DOCUMENT_OPERATION_DENIED'):
                    self.assertIn(code, variables[1].get('value'))
            self.assertEqual([node.get('fragment-id') for node in branch.findall('include-fragment')], ['core-gateway-telemetry'])
            response = response_block if response_block.tag == 'return-response' else response_block.find('./when/return-response')
            self.assertEqual(response.find('./set-header[@name="Cache-Control"]/value').text, 'no-store')
            self.assertEqual(response.find('./set-header[@name="apim-request-id"]/value').text, '@(context.RequestId.ToString())')
        self.assertEqual(start.find('./inbound/set-query-parameter[@name="api-version"]/value').text, DOCUMENT_API_VERSION)
        location_check = statement(start, './outbound/set-variable[@name="documentOperationId"]', 'value')
        comparison = re.search(r'location\.Query\s*!=\s*"\?api-version=([^"]+)"', location_check)
        self.assertIsNotNone(comparison)
        self.assertEqual(comparison.group(1), DOCUMENT_API_VERSION)
        self.assertIn('?api-version=' + DOCUMENT_API_VERSION, status.find('./inbound/rewrite-uri').get('template'))
        self.assertNotIn('documentSettings', rendered_status)
        self.assertNotIn('__DOCUMENT_SETTINGS_BASE64__', rendered_status)
        owner_check = status.find('./inbound/set-variable[@name="documentOwner"]').get('value')
        self.assertEqual(owner_check.count('Headers.TryGetValue("X-Core-Document-Ticket"'), 1)
        self.assertIn('ticketValues.Length != 1', owner_check)
        self.assertNotIn('Headers.GetValueOrDefault("X-Core-Document-Ticket"', owner_check)
        self.assertNotIn('context.Request.Url.Query.Count', owner_check)
        self.assertEqual(status.find('./inbound/rewrite-uri').get('copy-unmatched-params'), 'false')
        inbound = status.find('inbound')
        body_clear = inbound.findall('./choose[@id="document-status-body-clear"]')
        self.assertEqual(len(body_clear), 1)
        branch = body_clear[0].find('when')
        self.assertEqual(branch.get('condition'), '@(context.Request.Body != null)')
        self.assertEqual([node.tag for node in branch], ['set-body'])
        self.assertEqual(branch.find('set-body').text, '@("")')
        self.assertIsNone(inbound.find('set-body'))
        operation_id = inbound.find('./set-variable[@name="documentOperationId"]')
        ticket_delete = inbound.find('./set-header[@name="X-Core-Document-Ticket"][@exists-action="delete"]')
        self.assertIsNotNone(ticket_delete)
        self.assertEqual(list(inbound)[list(inbound).index(operation_id):],
                         [operation_id, inbound.find('./set-variable[@name="documentModel"]'),
                          inbound.find('rewrite-uri'), ticket_delete, body_clear[0]])
        evaluation = status.find('./outbound/set-variable[@name="documentPollEvaluation"]').get('value')
        self.assertIn('context.Variables["gatewayOperationConstraint"]', evaluation)
        outbound = status.find('outbound')
        audit = outbound.find('./choose[@id="document-settings-read-error"]')
        self.assertIsNotNone(audit)
        self.assertLess(list(outbound).index(outbound.find('./set-variable[@name="documentPollEvaluation"]')), list(outbound).index(audit))
        self.assertLess(list(outbound).index(audit), list(outbound).index(outbound.find('return-response')))
        branch = audit.find('when')
        self.assertIn('documentPollEvaluation', branch.get('condition'))
        self.assertIn('== 500', branch.get('condition'))
        self.assertEqual([(node.get('name'), node.get('value')) for node in branch.findall('set-variable')],
                         [('gatewayAuditPhase', 'policy-error'), ('gatewayAuditCode', 'OPERATION_SETTINGS_INVALID'),
                          ('gatewayAuditStatus', '@(500)')])
        self.assertEqual([node.get('fragment-id') for node in branch.findall('include-fragment')], ['core-gateway-telemetry'])
        settings = self.api['authorization']['operationConstraints']['get_document_operation']
        self.assertEqual(settings['apiVersion'], DOCUMENT_API_VERSION)
        self.assertEqual(settings['maxResponseBytes'], API['authorization']['operationConstraints']['get_document_operation']['maxResponseBytes'])
        schema = next(item for item in self.resources if item.get('copy', {}).get('name') == 'documentSchemas')
        components = PolicyExpression(schema['properties']['document']['components'], self.template['variables'],
                                      functions=self.template['functions']).evaluate({})
        versions = components['schemas']['DocumentAnalysisResult']['properties']['analyzeResult']['properties']['apiVersion']['enum']
        self.assertEqual(versions, [DOCUMENT_API_VERSION])


class DocumentAnalysisConfigurationTests(unittest.TestCase):
    def raw(self):
        raw, _ = standard_fixture()
        raw['apiDefinitions'] = [copy.deepcopy(API)]
        return raw

    def test_canonical_version_is_completed_and_an_explicit_match_is_accepted(self):
        for constraint in API['authorization']['operationConstraints'].values():
            self.assertNotIn('apiVersion', constraint)
        for explicit in (False, True):
            raw = self.raw()
            if explicit:
                for constraint in raw['apiDefinitions'][0]['authorization']['operationConstraints'].values():
                    constraint['apiVersion'] = DOCUMENT_API_VERSION
            constraints = normalize_configuration(raw)['apiDefinitions'][0]['authorization']['operationConstraints']
            for name, constraint in constraints.items():
                with self.subTest(explicit=explicit, operation=name):
                    self.assertEqual(constraint['apiVersion'], DOCUMENT_API_VERSION)

    def test_a_mismatched_version_is_rejected_for_each_document_operation(self):
        for name in API['authorization']['operationConstraints']:
            raw = self.raw()
            raw['apiDefinitions'][0]['authorization']['operationConstraints'][name]['apiVersion'] = DOCUMENT_API_VERSION + '.invalid'
            with self.subTest(operation=name), self.assertRaisesRegex(ValueError, 'apiVersion'):
                normalize_configuration(raw)

    def test_provider_capacity_and_native_job_contract_replace_trial_limits(self):
        raw = self.raw()
        raw['gateway'].update(wafMaxRequestBodySizeInKb=128, wafRequestBodyInspectLimitInKb=128)
        api = normalize_configuration(raw)['apiDefinitions'][0]
        constraints = api['authorization']['operationConstraints']
        self.assertEqual(constraints['analyze_document']['maxRequestBytes'], 500000000)
        self.assertEqual(constraints['analyze_document']['maxResponseBytes'], 524288000)
        self.assertEqual(constraints['get_document_operation']['kind'], 'documentAnalysisStatus')
        self.assertEqual(constraints['analyze_document']['requestBodySizeCheck'], 'declared')
        self.assertIn('application/pdf', constraints['analyze_document']['contentTypes'])
        self.assertIn('application/json', constraints['analyze_document']['contentTypes'])
        self.assertNotIn('timeoutSeconds', constraints['analyze_document'])
        for constraint in constraints.values():
            self.assertNotIn('allowedModels', constraint)

    def test_document_declared_size_format_is_internal_and_content_types_are_metadata(self):
        raw = self.raw()
        source = raw['apiDefinitions'][0]
        source['authorization']['operationConstraints']['analyze_document']['contentTypes'] = ['application/x-synthetic-document']
        api = normalize_configuration(raw)['apiDefinitions'][0]
        self.assertEqual(api['authorization']['operationConstraints']['analyze_document']['bodyFormat'], 'binary')
        self.assertEqual(api['authorization']['operationConstraints']['analyze_document']['requestBodySizeCheck'], 'declared')
        self.assertEqual(api['authorization']['operationConstraints']['analyze_document']['contentTypes'], ['application/x-synthetic-document'])
        self.assertNotIn('inputContract', api['operations'][0])
        self.assertEqual(api['operations'][1]['method'], 'GET')
        self.assertEqual(api['authorization']['operationConstraints']['get_document_operation']['bodyFormat'], 'none')
        self.assertNotIn('requestBodySizeCheck', api['authorization']['operationConstraints']['get_document_operation'])

    def test_document_request_metadata_retains_generic_media_type_validation(self):
        for values in ([], ['application/json; charset=utf-8'], ['application/*'], ['not-a-media-type']):
            raw = self.raw()
            raw['apiDefinitions'][0]['authorization']['operationConstraints']['analyze_document']['contentTypes'] = values
            with self.subTest(content_types=values), self.assertRaisesRegex(ValueError, 'contentTypes'):
                normalize_configuration(raw)

    def test_document_only_foundation_prepares_the_existing_server_owner_key(self):
        from deployment.helpers.grpc_lifecycle import enabled, owner_ticket_enabled, prepare_secret, realtime_enabled
        raw = self.raw()
        raw['gateway']['realtimeControlEnabled'] = False
        config = normalize_configuration(raw)
        self.assertTrue(enabled(config))
        self.assertFalse(realtime_enabled(config))
        self.assertTrue(owner_ticket_enabled(config))
        writer = mock.Mock()
        with mock.patch('deployment.helpers.grpc_lifecycle._prepare_credential',
                        side_effect=['synthetic-ingress-key', 'synthetic-document-ticket-key']) as prepare:
            self.assertEqual(prepare_secret(config, ROOT, writer), {
                'l7IngressSecret': 'synthetic-ingress-key',
                'realtimeTicketKey': 'synthetic-document-ticket-key',
            })
            self.assertEqual(prepare.call_args_list, [
                mock.call(config, ROOT, 'l7-ingress', writer, record_registration=True),
                mock.call(config, ROOT, 'realtime-ticket', writer, record_registration=True),
            ])
        writer.assert_not_called()

    def test_standard_foundation_requires_owner_key_access_before_api_expansion(self):
        """空配列から標準APIを後続公開する場合も、鍵への通信を事前検査する。"""
        raw = self.raw()
        raw['apiDefinitions'] = []
        raw['gateway']['realtimeControlEnabled'] = False
        from deployment.helpers.grpc_lifecycle import owner_ticket_enabled, realtime_enabled
        config = normalize_configuration(raw)
        self.assertFalse(realtime_enabled(config))
        self.assertTrue(owner_ticket_enabled(config))
        self.assertNotIn('grpc', config['gateway'])
        self.assertNotIn('apimKeyVaultAccessEnabled', config['network'])
        # Key Vault access is a fixed NSG requirement, so neither flag value is an input.
        for value in (False, True):
            raw['network']['apimKeyVaultAccessEnabled'] = value
            with self.subTest(retired_flag=value), self.assertRaisesRegex(ValueError, 'apimKeyVaultAccessEnabled'):
                normalize_configuration(raw)

    def test_canonical_paths_auth_and_provider_bounds_remain_required(self):
        for mutation in ('bytes', 'response', 'timeout', 'path', 'backend', 'status-method', 'status-path', 'status-parameters'):
            raw = self.raw()
            api = raw['apiDefinitions'][0]
            start = api['authorization']['operationConstraints']['analyze_document']
            if mutation == 'bytes': start['maxRequestBytes'] = 500000001
            if mutation == 'response': start['maxResponseBytes'] = 524288001
            if mutation == 'timeout': start['timeoutSeconds'] = 60
            if mutation == 'path': api['path'] = 'tools/other/v1'
            if mutation == 'backend': api['backendService'] = 'foundryOpenAi'
            if mutation == 'status-method': api['operations'][1]['method'] = 'POST'
            if mutation == 'status-path': api['operations'][1]['urlTemplate'] = '/other/{operationId}'
            if mutation == 'status-parameters': api['operations'][1]['templateParameters'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                normalize_configuration(raw)


if __name__ == '__main__':
    unittest.main()
