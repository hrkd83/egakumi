"""統合APIの実C#ポリシーを実行し、購読と操作別の入力制約を確認する。

ネットワークやAzureを呼ばず、操作・モデル設定の選択・購読の認可・WSモデル選択と
選択後の既存上限検査を、本番XMLから取り出した式で検証する。
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression, compiled_policy_value
from bicep_helpers import compile_bicep
from test_api_protocols import (
    CSHARP as EXISTING_CSHARP, actual_expression, bound_coverage, configuration,
    a2a_json_rpc_target_cases, dedicated_target_route_cases, hosted_agent_target_cases, json_object_body_failure_cases,
    responses_code_interpreter_input_cases, responses_file_id_null_cases, responses_include_cases,
    responses_item_reference_cases, responses_tool_choice_cases, responses_untyped_item_reference_cases,
)
from test_gateway_limits_telemetry import LIMIT_EXPRESSION, PWSH, single_expression
from deployment.helpers.apim_input_contract import input_contracts, render_input_validation_fragment
from deployment.helpers.apim_operation_settings import render_operation_settings_fragment
from deployment.helpers.apim_chat_store import chat_store_disabled_operations, render_chat_store_fragment
from deployment.helpers.apim_audit import render_audit_policy
from test_input_contract_selection import STANDARD_OPERATIONS


CORE = Path(__file__).resolve().parents[1]
INPUT_TABLES = {}
CHAT_APIS = {}
CLASSES = EXISTING_CSHARP.split("public static class ExpandedPolicyFixture")[0].replace(
    'public class Body { public string Data; public int Reads; public bool Unavailable; public bool ReturnsNull; public T As<T>(bool preserveContent=false) { Reads++; if (Unavailable) throw new Exception("Synthetic body read unavailable"); return ReturnsNull ? default(T) : (T)(object)JObject.Parse(Data); } }',
    '''public class Body {
 public string Data; public int Reads; public int ByteReads; public bool AllowConsumption; public int ConsumingReads;
 public bool Unavailable; public bool ReturnsNull;
 public T As<T>(bool preserveContent=false) {
  Reads++;
  if (!preserveContent) {
   if (!AllowConsumption) throw new Exception("Request body was consumed");
   ConsumingReads++;
   AllowConsumption=false;
  }
  if (Unavailable) throw new Exception("Synthetic body read unavailable");
  if (ReturnsNull) return default(T);
  if (typeof(T)==typeof(byte[])) ByteReads++;
  return typeof(T)==typeof(byte[]) ? (T)(object)Encoding.UTF8.GetBytes(Data)
    : typeof(T)==typeof(string) ? (T)(object)Data : (T)(object)JObject.Parse(Data);
 }
}''')

CLASSES = CLASSES.replace(
    'public class Url { public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }',
    'public class Url { public int Reads; public bool Unavailable; public Dictionary<string,string[]> Values=new Dictionary<string,string[]>(); public Dictionary<string,string[]> Query { get { Reads++; if(Unavailable) throw new Exception("Synthetic query access is unavailable"); return Values; } } }')

# 入力部品がHTTPメソッドを再検査しないことを、実C#のアクセス回数で確認します。
CLASSES = CLASSES.replace(
    'public string Method="POST";',
    'public int MethodReads; private string method="POST"; public string Method { get { MethodReads++; return method; } set { method=value; } }')

SOURCE = CLASSES + r'''
public static class IntegratedProfileFixture {
 public static bool InferenceRequest(Context context) { __INFERENCE_REQUEST__ }
 public static string Profile(Context context) { __PROFILE__ }
 public static string Auth(Context context) { __AUTH__ }
 public static string OperationConstraint(Context context) { __OPERATION_CONSTRAINT__ }
 public static string Limits(Context context) { __LIMITS__ }
 public static bool SpeechRequired(Context context) { __SPEECH_REQUIRED__ }
 public static string SpeechGuard(Context context) { __SPEECH_GUARD__ }
 public static string TargetDecision(Context context) { __TARGET__ }
 public static bool TargetRejected(Context context) { return __TARGET_REJECTION_CONDITION__; }
 public static JObject TargetRejection(Context context) {
  // 呼出元の実XML式を、監査変数の設定から失敗応答まで元の順序で評価します。
  __TARGET_AUDIT_ASSIGNMENTS__
  return new JObject(new JProperty("phase", context.Variables["gatewayAuditPhase"]),
   new JProperty("code", context.Variables["gatewayAuditCode"]),
   new JProperty("auditStatus", context.Variables["gatewayAuditStatus"]),
   new JProperty("status", __TARGET_RESPONSE_STATUS__), new JProperty("reason", __TARGET_RESPONSE_REASON__),
   new JProperty("requestIdHeader", __TARGET_RESPONSE_REQUEST_ID__));
 }
 public static int BodyReadCount(Context context) { return context.Request.Body == null ? 0 : context.Request.Body.Reads; }
 public static int BodyByteReadCount(Context context) { return context.Request.Body == null ? 0 : context.Request.Body.ByteReads; }
 public static string BodyData(Context context) { return context.Request.Body == null ? null : context.Request.Body.Data; }
 public static string Model(Context context) { __MODEL__ }
 public static string NormalizeModel(Context context) { __NORMALIZE_MODEL__ }
 public static bool ChatStoreDisabled(Context context) { __CHAT_REQUIRED__ }
 public static string NormalizeChatRequest(Context context) { __CHAT_BODY__ }
 public static bool ChatRequestInvalid(Context context) { return __CHAT_INVALID__; }
 public static string ApplyChatBody(Context context) { return __CHAT_APPLY__; }
 public static JObject ChatRejection(Context context) {
  context.Variables["gatewayAuditPhase"] = __CHAT_AUDIT_PHASE__;
  context.Variables["gatewayAuditCode"] = __CHAT_AUDIT_CODE__;
  context.Variables["gatewayAuditStatus"] = __CHAT_AUDIT_STATUS__;
  return new JObject(new JProperty("phase", context.Variables["gatewayAuditPhase"]),
   new JProperty("code", context.Variables["gatewayAuditCode"]),
   new JProperty("status", __CHAT_RESPONSE_STATUS__), new JProperty("reason", __CHAT_RESPONSE_REASON__),
   new JProperty("requestId", __CHAT_REQUEST_ID__), new JProperty("cacheControl", __CHAT_CACHE_CONTROL__));
 }
 public static bool StatelessResponses(Context context) { __STATELESS_REQUIRED__ }
 public static string NormalizeStatelessResponse(Context context) { __STATELESS_BODY__ }
 public static bool HostedResponseRequired(Context context) { return __HOSTED_REQUIRED__; }
 public static string NormalizeHostedResponse(Context context) { __HOSTED_BODY__ }
 public static string ProtocolAuth(Context context) { __PROTOCOL_AUTH__ }
 public static bool Query(Context context) { __QUERY__ }
 public static JObject ProfileRejection(Context context) {
  var code = __PROFILE_AUDIT_CODE__;
  context.Variables["gatewayAuditCode"] = code;
  var status = __PROFILE_AUDIT_STATUS__;
  context.Variables["gatewayAuditStatus"] = status;
  context.Variables["gatewayAuditPhase"] = __PROFILE_AUDIT_PHASE__;
  return new JObject(new JProperty("code", code), new JProperty("status", status), new JProperty("reason", __PROFILE_RESPONSE_REASON__));
 }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Api.Id = (string)item["api"] ?? "synthetic-api";
   c.Operation.Id = (string)item["operation"] ?? "chat-completions";
   c.Operation.UrlTemplate = (string)item["path"] ?? "/responses";
   c.Request.Method = (string)item["method"] ?? "POST";
   c.Request.Body = new Body {Data=item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None)};
   if ((bool?)item["bodyAbsent"] == true) c.Request.Body=null;
   c.Variables["config"] = item["config"].ToString(Formatting.None);
   c.Variables["websocket"] = (item["websocket"]??new JObject()).ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"] = "api-key";
   c.Variables["gatewayAuditSettings"] = new JObject(new JProperty("targetAgent", (string)item["targetAgent"] ?? "")).ToString(Formatting.None);
   foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties()) c.Request.Headers[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties()) c.Request.Url.Values[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["parameters"]??new JObject())).Properties()) c.Request.MatchedParameters[entry.Name]=(string)entry.Value;
   if(item["key"]!=null && item["key"].Type!=JTokenType.Null) { c.Subscription=new Named {Id=(string)item["key"]};c.Product=new Named {Id="tool-users"};c.Request.Headers["api-key"]=new[] {"fixture-value"}; }
   c.Variables["gatewayControlSettings"] = new JObject(
    new JProperty("inputContracts", item["inputContracts"] ?? new JObject()),
    new JProperty("speechSsmlOperationIds", item["speechSsmlOperationIds"] ?? new JArray()),
    new JProperty("chatStoreDisabledOperationIds", item["chatStoreDisabledOperationIds"] ?? new JArray()),
    new JProperty("operationConstraints", item["controlConstraints"] ?? item["config"]["operationConstraints"])).ToString(Formatting.None);
   if (item["controlsRaw"] != null) c.Variables["gatewayControlSettings"] = (string)item["controlsRaw"];
   if ((bool?)item["controlsMissing"] == true) c.Variables.Remove("gatewayControlSettings");
   if ((bool?)item["controlsNonString"] == true) c.Variables["gatewayControlSettings"] = 1;
   c.Request.Url.Unavailable = (bool?)item["queryUnavailable"] == true;
   var result=new JObject();
   if ((bool?)item["applyHostedNormalization"] == true) result["rateLimitReached"]=false;
   if ((bool?)item["profileOnly"] == true) {
    var selection=JObject.Parse(Profile(c));c.Variables["gatewayOperationSelection"]=selection.ToString(Formatting.None);
    result["profile"]=selection;
    if (!(bool)selection["allowed"]) {
     result["rejection"]=ProfileRejection(c);
     result["rejectionPhase"]=(string)c.Variables["gatewayAuditPhase"];
    }
    result["reads"]=BodyReadCount(c);result["byteReads"]=BodyByteReadCount(c);result["bodyAfter"]=BodyData(c);
    results[(string)item["id"]]=result;continue;
   }
   if((string)item["mode"]=="ws") {
    var model=Model(c);c.Variables["gatewayProtocolModel"]=model;
    result["model"]=model;
    result["decision"]=JObject.Parse(ProtocolAuth(c));
    result["queryAllowed"]=Query(c);
   } else {
    try { result["requestAllowed"]=InferenceRequest(c); }
    catch (Exception error) {
     result["inputError"]=error.GetType().Name; result["reads"]=BodyReadCount(c);
     result["inputBodyReads"]=BodyReadCount(c); result["inputQueryReads"]=c.Request.Url.Reads; result["inputMethodReads"]=c.Request.MethodReads;
     result["inputHeadersAfter"]=JObject.FromObject(c.Request.Headers);
     result["queryAfter"]=JObject.FromObject(c.Request.Url.Values); result["bodyAfter"]=BodyData(c);
     results[(string)item["id"]]=result; continue;
    }
    result["inputBodyReads"]=BodyReadCount(c); result["inputQueryReads"]=c.Request.Url.Reads; result["inputMethodReads"]=c.Request.MethodReads;
    result["inputHeadersAfter"]=JObject.FromObject(c.Request.Headers);
    result["queryAfter"]=JObject.FromObject(c.Request.Url.Values);
    if (!(bool)result["requestAllowed"] || (bool?)item["inputOnly"] == true) {
     result["reads"]=BodyReadCount(c);
     result["bodyAfter"]=BodyData(c);
     results[(string)item["id"]]=result;
     continue;
    }
    var selection=JObject.Parse(Profile(c));c.Variables["gatewayOperationSelection"]=selection.ToString(Formatting.None);
    result["profile"]=selection;
    result["profileByteReads"]=BodyByteReadCount(c);
    if (!(bool)selection["allowed"]) {
     result["rejection"]=ProfileRejection(c);
     result["rejectionPhase"]=(string)c.Variables["gatewayAuditPhase"];
    }
    if((bool)selection["allowed"]) {
     result["decision"]=JObject.Parse(Auth(c));
     c.Variables["gatewayOperationConstraint"]=OperationConstraint(c);
     result["operationConstraint"]=JObject.Parse((string)c.Variables["gatewayOperationConstraint"]);
     c.Variables["gatewayEffectiveModel"]=(string)selection["effectiveModel"]??"";
     result["limits"]=JObject.Parse(Limits(c));
     result["limitBodyReads"]=BodyReadCount(c);
     result["limitByteReads"]=BodyByteReadCount(c);
     if ((bool)result["limits"]["allowed"] && SpeechRequired(c)) {
      result["speech"]=JObject.Parse(SpeechGuard(c));
      if (!(bool)result["speech"]["allowed"]) {
       result["reads"]=BodyReadCount(c); result["bodyAfter"]=BodyData(c);
       results[(string)item["id"]]=result; continue;
      }
     }
     // JSON readerの例外/nullだけを、先行する本文バイト数検査の後に注入します。
     if (c.Request.Body != null) {
      c.Request.Body.Unavailable=(bool?)item["bodyReadUnavailable"] == true;
      c.Request.Body.ReturnsNull=(bool?)item["bodyReadReturnsNull"] == true;
     }
     if ((bool?)item["targetConstraintMissing"] == true) c.Variables.Remove("gatewayOperationConstraint");
     if (item.Property("targetConstraintRaw") != null) c.Variables["gatewayOperationConstraint"]=(string)item["targetConstraintRaw"];
     var targetReadsBefore=BodyReadCount(c);
     var targetMethodReadsBefore=c.Request.MethodReads;
     var targetDecision=TargetDecision(c);c.Variables["gatewayTargetDecision"]=targetDecision;
     result["targetDecision"]=JObject.Parse(targetDecision);
     result["targetAllowed"]=result["targetDecision"]["allowed"];
     result["targetBodyReads"]=BodyReadCount(c)-targetReadsBefore;
     result["targetMethodReads"]=c.Request.MethodReads-targetMethodReadsBefore;
     result["bodyBeforeNormalization"]=BodyData(c);
     if (TargetRejected(c)) result["targetRejection"]=TargetRejection(c);
     if ((bool)result["decision"]["allowed"] && (bool)result["limits"]["allowed"] && (bool)result["targetAllowed"]
         && !String.IsNullOrEmpty((string)selection["effectiveModel"]) && (string)selection["requestedModel"]!=(string)selection["effectiveModel"]) {
      c.Request.Body.Data=NormalizeModel(c);
     }
     if ((bool?)item["applyHostedNormalization"] == true && (bool)result["decision"]["allowed"]
         && (bool)result["limits"]["allowed"] && (bool)result["targetAllowed"]) {
      var before=BodyReadCount(c);
      result["hostedNormalizationRequired"]=HostedResponseRequired(c);
      result["hostedSelectionBodyReads"]=BodyReadCount(c)-before;
      if ((bool)result["hostedNormalizationRequired"]) {
       c.Request.Body.Data=NormalizeHostedResponse(c);
       result["hostedBodyReads"]=BodyReadCount(c)-before;
       result["hostedConsumingReads"]=c.Request.Body.ConsumingReads;
      }
      // APIMの計数自体は実行せず、実XMLで編集の後にある利用枠ステージへの到達だけを観測します。
      result["rateLimitReached"]=true;
     }
     if (((bool?)item["applyChatNormalization"] == true || (bool?)item["applyStatelessNormalization"] == true)
         && (bool)result["decision"]["allowed"] && (bool)result["limits"]["allowed"] && (bool)result["targetAllowed"]) {
      result["chatStoreDisabled"]=ChatStoreDisabled(c);
      if ((bool)result["chatStoreDisabled"]) {
       var before=BodyReadCount(c);
       c.Request.Body.AllowConsumption=true;
       c.Request.Body.Unavailable=(bool?)item["chatBodyReadUnavailable"] == true;
       c.Request.Body.ReturnsNull=(bool?)item["chatBodyReadReturnsNull"] == true;
       c.Variables["gatewayChatRequestBody"]=NormalizeChatRequest(c);
       result["chatBodyReads"]=BodyReadCount(c)-before;
       result["chatConsumingReads"]=c.Request.Body.ConsumingReads;
       if (ChatRequestInvalid(c)) {
        result["chatRejection"]=ChatRejection(c);
        result["chatBodyApplied"]=false;
        result["reads"]=BodyReadCount(c); result["bodyAfter"]=BodyData(c);
        results[(string)item["id"]]=result; continue;
       }
       var preparedReads=BodyReadCount(c);
       c.Request.Body.Data=ApplyChatBody(c);
       result["chatApplyBodyReads"]=BodyReadCount(c)-preparedReads;
       result["chatBodyApplied"]=true;
      }
     }
     if ((bool?)item["applyStatelessNormalization"] == true && (bool)result["decision"]["allowed"]
         && (bool)result["limits"]["allowed"] && (bool)result["targetAllowed"] && StatelessResponses(c)) {
      var before=BodyReadCount(c);
      c.Request.Body.AllowConsumption=true;
      c.Request.Body.Data=NormalizeStatelessResponse(c);
      result["statelessBodyReads"]=BodyReadCount(c)-before;
      result["statelessConsumingReads"]=c.Request.Body.ConsumingReads;
     }
    }
   }
   result["reads"]=BodyReadCount(c);
   result["byteReads"]=BodyByteReadCount(c);
   result["bodyAfter"]=BodyData(c);
   results[(string)item["id"]]=result;
  }
  return results.ToString(Formatting.None);
 }
}
'''


def case(name, **patch):
    constraint = {"kind": "bodyModel", "bodyFormat": "json", "maxRequestBytes": 2048,
                  "allowedModels": ["shared", "audio"], "maxOutputTokens": 32,
                  "outputTokenParameter": "max_completion_tokens",
                  "modelSettings": {"shared": {"settingId": "chat", "maxRequestBytes": 512},
                                    "audio": {"settingId": "audio-chat"}}}
    config = {"mode": "subscriptionKey", "operationConstraints": {"chat-completions": constraint}}
    item = {"id": name, "config": config, "key": "subscription-fixture",
            "body": {"model": "shared", "messages": [{"role": "user", "content": "synthetic"}], "max_completion_tokens": 16}}
    item.update(patch)
    if "inputContracts" not in item:
        api = str(item.get("api", "")).split(";rev=", 1)[0]
        item["inputContracts"] = copy.deepcopy(INPUT_TABLES.get(api, {}))
    # Bicepが用意する対象表の合成fixtureです。選択は実APIポリシーの条件式で行います。
    item.setdefault("speechSsmlOperationIds", ["tts"] if str(item.get("api", "")).split(";rev=", 1)[0] == "azure-speech-v1" else [])
    if "chatStoreDisabledOperationIds" not in item:
        definition = item.get("chatApiDefinition") or CHAT_APIS.get(str(item.get("api", "")).split(";rev=", 1)[0])
        if definition is not None:
            definition = copy.deepcopy(definition)
            definition["authorization"] = item["config"]
            definition["operations"] = [operation for operation in definition["operations"]
                                        if operation["name"] in item["config"]["operationConstraints"]]
        item["chatStoreDisabledOperationIds"] = chat_store_disabled_operations(definition) if definition else []
    return item


class ProfilePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("Actual C# policy execution requires PowerShell")
        cases = [case("default"), case("explicit", headers={"X-Egakumi-Profile": "vision"}),
                 case("unknown", headers={"X-Egakumi-Profile": "unknown"}),
                 case("duplicate", headers={"X-Egakumi-Profile": ["chat", "vision"]}),
                 case("empty-profile-header", headers={"X-Egakumi-Profile": ""}),
                 case("zero-profile-values", headers={"X-Egakumi-Profile": []}),
                 case("model-only-audio", body={"model": "audio"}),
                 case("wrong-model", headers={"X-Egakumi-Profile": "vision"}, body={"model": "audio"}),
                 case("unsupported-model", body={"model": "unregistered-synthetic-model"}),
                 case("unsupported-model-with-profile", headers={"X-Egakumi-Profile": "chat"},
                      body={"model": "unregistered-synthetic-model"}),
                 case("huge-malformed", rawBody="{" + "x" * 2049)]
        # 選択前の計測値は、JSONの再整形前の空白とUTF-8本文全体を数えます。
        cls.preserved_wire_body = " \t\n" + json.dumps(
            {"model": "shared", "messages": [{"content": "合成🧪"}], "max_completion_tokens": 16},
            ensure_ascii=False) + "  "
        cases.append(case("preserved-wire-bytes", rawBody=cls.preserved_wire_body))
        # 準備済みのモデル設定だけを変え、操作の制御設定を読むことを確認します。
        prepared = case("prepared-controls")
        prepared["controlConstraints"] = copy.deepcopy(prepared["config"]["operationConstraints"])
        prepared["controlConstraints"]["chat-completions"]["modelSettings"]["shared"].update(
            settingId="vision", maxRequestBytes=2048)
        cases.append(prepared)
        # 操作の登録は存在するが、準備済みの操作設定が欠落・破損した状況を再現します。
        cls.operation_settings_errors = []
        for label, patch in (
            ("missing-table", {"controlsRaw": '{"inputContracts":{}}'}),
            ("null-table", {"controlConstraints": None}),
            ("array-table", {"controlConstraints": []}),
            ("string-table", {"controlConstraints": "invalid"}),
            ("empty-table", {"controlConstraints": {}}),
            ("null-operation", {"controlConstraints": {"chat-completions": None}}),
            ("array-operation", {"controlConstraints": {"chat-completions": []}}),
            ("string-operation", {"controlConstraints": {"chat-completions": "invalid"}}),
            ("gui-added-operation", {"operation": "gui-added-operation"}),
            # 式単体の保護と、先行する入力部品の実フローを混同しません。
            ("malformed-json-direct", {"controlsRaw": "{", "profileOnly": True}),
            ("array-json-direct", {"controlsRaw": "[]", "profileOnly": True}),
            ("missing-variable-direct", {"controlsMissing": True, "profileOnly": True}),
            ("nonstring-variable-direct", {"controlsNonString": True, "profileOnly": True})):
            identifier = "operation-settings-" + label
            cls.operation_settings_errors.append(identifier)
            cases.append(case(identifier, rawBody="{", **patch))
        cases.extend((
            case("profile-malformed-json-through-input", controlsRaw="{", rawBody="{"),
            case("profile-empty-operation", controlConstraints={"chat-completions": {}}, profileOnly=True, rawBody="{"),
            case("profile-empty-operation-with-header", controlConstraints={"chat-completions": {}}, profileOnly=True,
                 headers={"X-Egakumi-Profile": "chat"}, rawBody="{"),
            case("profile-malformed-body", rawBody="{")))
        # JSON解析とmodelの形式だけを要求不備に分類し、本文はそのまま保持します。
        cls.profile_body_errors = {}
        for label, raw in (('empty', ''), ('blank', ' \t'), ('malformed', '{'), ('array', '[]'),
                           ('string', '"synthetic"'), ('number', '1'), ('null', 'null')):
            identifier = 'profile-body-invalid-' + label
            cls.profile_body_errors[identifier] = ('INVALID_REQUEST_BODY', 'Invalid request body', raw)
            cases.append(case(identifier, rawBody=raw))
        cls.profile_model_errors = {}
        for label, body in (('missing', {}), ('null', {'model': None}), ('number', {'model': 1}),
                            ('boolean', {'model': True}), ('array', {'model': ['shared']}),
                            ('object', {'model': {'name': 'shared'}})):
            identifier = 'profile-model-invalid-' + label
            cls.profile_model_errors[identifier] = body
            cases.append(case(identifier, body=body))
        for label, value in (('empty', ''), ('blank', ' \t')):
            cases.append(case('profile-model-unresolved-' + label, body={'model': value}, profileOnly=True))
        # モデル別設定を使わない追加操作は、サイズ確認後もmodel形式のためだけにJSONを読みません。
        cls.unparsed_model_cases = {}
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            constraint = copy.deepcopy(case('unparsed-fixture')['config']['operationConstraints']['chat-completions'])
            constraint['kind'] = kind
            constraint.pop('modelSettings')
            for label, raw in (('malformed', '{synthetic'), ('scalar', '"synthetic"'), ('json-null', 'null'),
                               ('missing-model', '{}'), ('null-model', '{"model":null}'),
                               ('number-model', '{"model":1}'), ('array-model', '{"model":["shared"]}'),
                               ('object-model', '{"model":{"name":"shared"}}')):
                identifier = 'generic-unparsed-' + kind + '-' + label
                cls.unparsed_model_cases[identifier] = raw
                cases.append(case(identifier, controlConstraints={'chat-completions': constraint}, rawBody=raw))
        # モデル表・alias表の型不備は、本文解析より前の設定不備です。
        for field in ('modelSettings', 'modelAliases'):
            for label, value in (('null', None), ('number', 1), ('boolean', True),
                                 ('string', 'invalid'), ('array', [])):
                identifier = 'operation-' + field + '-' + label
                cls.operation_settings_errors.append(identifier)
                constraint = copy.deepcopy(case('settings-fixture')['config']['operationConstraints']['chat-completions'])
                constraint[field] = value
                cases.append(case(identifier, controlConstraints={'chat-completions': constraint}, profileOnly=True, rawBody='{'))
        # 先頭の上限は、候補一覧ではなく配備時に集約した操作の値を読みます。
        for label, value in (('null', None), ('array', []), ('object', {}), ('unparseable', 'invalid'),
                             ('long-overflow', 9223372036854775808), ('zero', 0), ('negative', -1),
                             ('over-ceiling', 524288001)):
            identifier = 'operation-size-' + label
            cls.operation_settings_errors.append(identifier)
            constraint = copy.deepcopy(case('settings-fixture')['config']['operationConstraints']['chat-completions'])
            constraint['maxRequestBytes'] = value
            cases.append(case(identifier, controlConstraints={'chat-completions': constraint}, profileOnly=True, rawBody='{'))
        missing_size = copy.deepcopy(case('size-fixture')['config']['operationConstraints']['chat-completions'])
        missing_size.pop('maxRequestBytes')
        cls.operation_settings_errors.append('operation-size-missing')
        cases.append(case('operation-size-missing', controlConstraints={'chat-completions': missing_size}, profileOnly=True, rawBody='{'))
        for label, maximum in (('minimum', 1), ('ceiling', 524288000), ('convertible-string', '512')):
            constraint = copy.deepcopy(case('size-fixture')['config']['operationConstraints']['chat-completions'])
            constraint['maxRequestBytes'] = maximum
            cases.append(case('profile-size-existing-' + label, controlConstraints={'chat-completions': constraint},
                              profileOnly=True, **({'rawBody': '{'} if label == 'minimum' else {})))
        cases.append(case('profile-size-existing-maximum', profileOnly=True))
        # 選択したentryの型・alias対象の読取り不備は、JSON確認後の設定不備です。
        cls.selected_settings_errors = []
        for label, patch in (('entry-null', {'modelSettings': {'shared': None}}),
                             ('entry-array', {'modelSettings': {'shared': []}}),
                             ('entry-string', {'modelSettings': {'shared': 'invalid'}}),
                             ('alias-target', {'modelAliases': {'shared': {}}})):
            identifier = 'selected-settings-' + label
            cls.selected_settings_errors.append(identifier)
            constraint = copy.deepcopy(case('settings-fixture')['config']['operationConstraints']['chat-completions'])
            constraint.update(patch)
            cases.append(case(identifier, controlConstraints={'chat-completions': constraint}, profileOnly=True))
        # 設定の選択は登録モデル一覧を読みません。欠落や不正な型も、この部品では判定に使いません。
        for label in ('missing', 'object'):
            constraint = copy.deepcopy(case('catalog-fixture')['config']['operationConstraints']['chat-completions'])
            if label == 'missing':
                constraint.pop('allowedModels')
            else:
                constraint['allowedModels'] = {}
            cases.append(case('operation-catalog-' + label, controlConstraints={'chat-completions': constraint}, profileOnly=True))
        cls.inheritance_cases = {}
        for label, patch, body in (
                ('entry-missing', {'modelSettings': {}}, {'model': 'shared'}),
                ('entry-empty', {'modelSettings': {'shared': {}}}, {'model': 'shared'}),
                ('alias-only', {'modelSettings': None, 'modelAliases': {'synthetic-alias': 'shared'}}, {'model': 'synthetic-alias'}),
                ('entry-override', {'integerLimits': {'n': {'minimum': 1, 'maximum': 2}},
                                    'modelSettings': {'shared': {'settingId': 'custom', 'maxRequestBytes': 1024,
                                        'integerLimits': {'x': {'minimum': 5, 'maximum': 6}}}}}, {'model': 'shared'})):
            identifier = 'operation-inheritance-' + label
            constraint = copy.deepcopy(case('inheritance-fixture')['config']['operationConstraints']['chat-completions'])
            constraint.update(patch)
            if label == 'alias-only':
                constraint.pop('modelSettings')
            cls.inheritance_cases[identifier] = constraint
            cases.append(case(identifier, controlConstraints={'chat-completions': constraint}, body=body, profileOnly=True))
        # 実際の登録例から、モデルと出力項目の組を実行検査します。
        apis = {a["name"]: a for a in configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]}
        INPUT_TABLES.clear()
        INPUT_TABLES.update({name: input_contracts(api) for name, api in apis.items()})
        CHAT_APIS.clear()
        CHAT_APIS.update(apis)
        inference = apis["openai-inference-v1"]["authorization"]
        chat_entry = {'config': inference, 'api': 'openai-inference-v1', 'operation': 'chat-completions',
                      'path': '/chat/completions', 'headers': {'Content-Type': 'application/json'}}
        cls.chat_store_cases = {}
        for label,fields in (('absent',{}), ('true',{'store':True}), ('false',{'store':False}),
                             ('null',{'store':None}), ('wrong-type',{'store':{'synthetic':True}})):
            body={'model':'gpt-6.1-sol','messages':[{'role':'user','content':'synthetic'}],
                  'stream':True,'metadata':{'synthetic':'unchanged'},'temperature':0.2,**fields}
            identifier='chat-store-'+label
            cls.chat_store_cases[identifier]=(body,{**copy.deepcopy(body),'store':False})
            cases.append(case(identifier,**chat_entry,body=body,applyChatNormalization=True))
        for model in ('DeepSeek-V4-Flash','Mistral-Large-3','grok-4.7','model-router'):
            body={'model':model,'messages':[{'role':'user','content':'synthetic'}],'stream':False,'store':True}
            identifier='chat-store-model-'+model
            cls.chat_store_cases[identifier]=(body,{**copy.deepcopy(body),'store':False})
            cases.append(case(identifier,**chat_entry,body=body,applyChatNormalization=True))
        alias_config=copy.deepcopy(inference)
        alias_config['operationConstraints']['chat-completions']['modelAliases']={'synthetic-chat-alias':'gpt-future-synthetic'}
        alias_body={'model':'synthetic-chat-alias','messages':[{'role':'user','content':'synthetic'}],'stream':True,'store':True}
        cls.chat_store_cases['chat-store-synthetic-alias']=(alias_body,{**copy.deepcopy(alias_body),'model':'gpt-future-synthetic','store':False})
        cases.append(case('chat-store-synthetic-alias',**{**chat_entry,'config':alias_config},body=alias_body,applyChatNormalization=True))
        generic_api={'name':'synthetic-added-chat-v1','apiType':'http','backendService':'foundryOpenAi',
                     'operations':[{'name':'invoke-chat','method':'POST','urlTemplate':'/chat/completions'}]}
        generic_config={'mode':'subscriptionKey','operationConstraints':{'invoke-chat':
                        {'kind':'bodyModel','bodyFormat':'json','maxRequestBytes':1024}}}
        generic_chat={'config':generic_config,'api':'synthetic-added-chat-v1','operation':'invoke-chat',
                      'path':'/chat/completions','headers':{'Content-Type':'application/json'},
                      'chatApiDefinition':generic_api,'applyChatNormalization':True}
        generic_body={'model':'future-synthetic-model','messages':[{'role':'user','content':'synthetic'}],'store':True}
        cls.chat_store_cases['chat-store-additional-api']=(generic_body,{**copy.deepcopy(generic_body),'store':False})
        cases.append(case('chat-store-additional-api',**generic_chat,body=generic_body))
        cls.chat_invalid_cases=[]
        for label,raw in (('malformed','{'),('array','[]'),('number','1'),('null','null'),('empty','')):
            identifier='chat-store-invalid-'+label
            cls.chat_invalid_cases.append(identifier)
            cases.append(case(identifier,**generic_chat,rawBody=raw))
        for label,flag in (('unreadable','chatBodyReadUnavailable'),('returned-null','chatBodyReadReturnsNull')):
            identifier='chat-store-invalid-'+label
            cls.chat_invalid_cases.append(identifier)
            cases.append(case(identifier,**{**generic_chat,flag:True},body=generic_body))
        cases.append(case('chat-store-auth-rejected',**generic_chat,key=None,body=generic_body))
        cases.append(case('chat-store-wire-overflow',**generic_chat,rawBody=' '*1024+json.dumps(generic_body)))
        cls.chat_excluded_cases={}
        for label,api_patch,operation_patch,kind in (
                ('other-backend',{'backendService':'external'}, {},'bodyModel'),
                ('method',{}, {'method':'GET'},'bodyModel'),
                ('path',{}, {'urlTemplate':'/other'},'bodyModel'),
                ('kind',{}, {},'textEmbeddings')):
            definition=copy.deepcopy(generic_api)
            definition.update(api_patch)
            definition['operations'][0].update(operation_patch)
            configured=copy.deepcopy(generic_config)
            configured['operationConstraints']['invoke-chat']['kind']=kind
            identifier='chat-store-excluded-'+label
            cls.chat_excluded_cases[identifier]=generic_body
            cases.append(case(identifier,**{**generic_chat,'chatApiDefinition':definition,'config':configured},body=generic_body))
        for model in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7"):
            cases.append(case(model, **chat_entry, body={"model": model, "max_tokens": 64}))
        cases.append(case("compatible-alternate-token-field", config=inference, headers={"X-Egakumi-Profile": "compatible"}, body={"model": "grok-4.7", "max_completion_tokens": 64}))
        cases.append(case("router", **chat_entry, body={"model": "model-router", "max_completion_tokens": 64}))
        # 実際の操作IDで、FLUXの両モデル・生成/編集を旧profileヘッダーなしで選択します。
        for operation in ('flux2-generate', 'flux2-edit'):
            for variant in ('flex', 'pro'):
                cases.append(case('flux-' + operation + '-' + variant, config=copy.deepcopy(apis['blackforest-images-v1']['authorization']),
                                  api='blackforest-images-v1', operation=operation,
                                  headers={'Content-Type': 'application/json'}, body={'model': 'FLUX.2-' + variant, 'prompt': 'synthetic'}))
        flux = apis["blackforest-images-v1"]["authorization"]
        flux = dict(flux, operationConstraints={"chat-completions": flux["operationConstraints"]["flux2-generate"]})
        cases.append(case("flux-pro", config=flux, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        cases.append(case("flux-wrong-profile", config=flux, headers={"X-Egakumi-Profile": "flex"}, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        # Use a small synthetic bound to exercise equality/overflow without allocating
        # the actual Classic APIM 500 MiB buffer ceiling in a unit fixture.
        flux["operationConstraints"]["chat-completions"]["maxRequestBytes"] = 2048
        for settings in flux["operationConstraints"]["chat-completions"].get("modelSettings", {}).values():
            settings["maxRequestBytes"] = 2048
        maximum = 2048
        for excess in (0, 1):
            body = json.dumps({"model": "FLUX.2-flex", "prompt": ""}, separators=(",", ":"))
            body = body.replace('"prompt":""', '"prompt":"' + "a" * (maximum + excess - len(body.encode("utf-8"))) + '"')
            cases.append(case("flux-byte-limit-" + str(excess), config=flux, headers={"X-Egakumi-Profile": "flex"}, rawBody=body))
        image = {"model": "shared", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}], "max_completion_tokens": 16}
        for name, headers in (("image-default", {}), ("image-authorized", {"X-Egakumi-Profile": "vision"})):
            cases.append(case(name, headers=headers, body=image))
        cases.append(case("audio-default", body={"model": "shared", "modalities": ["text", "audio"], "max_completion_tokens": 16}))
        cases.append(case("file-default", body={"model": "shared", "messages": [{"content": [{"type": "file", "file": {"file_data": "AA=="}}]}]}))
        # 旧ヘッダーの有無・内容・値数にかかわらず、モデルから同じ設定を選びます。
        cls.shared_model_cases = {}
        for operation, default, alternate, body in (
            ('chat-completions', 'chat', 'vision', {'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'synthetic'}]}),
            ('responses-create', 'response', 'pdf', {'model': 'gpt-6.1-sol', 'input': 'synthetic', 'stream': True})):
            for label, legacy in (('absent', None), ('alternate', alternate), ('unknown', 'unregistered'), ('multiple', [alternate, 'router'])):
                identifier = 'shared-' + operation + '-' + label
                headers = {'Content-Type': 'application/json'}
                if legacy is not None:
                    headers['X-Egakumi-Profile'] = legacy
                cases.append(case(identifier, config=inference, api='openai-inference-v1', operation=operation, headers=headers, body=body))
                cls.shared_model_cases[identifier] = (default, body)
            reversed_config = copy.deepcopy(inference)
            entries = reversed_config['operationConstraints'][operation].get('modelSettings', {})
            reversed_config['operationConstraints'][operation]['modelSettings'] = dict(reversed(list(entries.items())))
            identifier = 'shared-' + operation + '-reversed'
            cases.append(case(identifier, config=reversed_config, api='openai-inference-v1', operation=operation,
                              headers={'Content-Type': 'application/json'}, body=body))
            cls.shared_model_cases[identifier] = (default, body)
        # Use the shipped default Responses profile, with no special profile
        # header, to reproduce Codex tool schemas and native multimodal content.
        responses = dict(inference, operationConstraints={"chat-completions": inference["operationConstraints"]["responses-create"]})
        tool_body = {"model": "gpt-6.1-sol", "input": "synthetic text", "tools": [{"type": "function", "name": "synthetic_tool",
            "parameters": {"type": "object", "properties": {"image": {"type": "string"}, "audio": {"type": "string"},
                "file": {"type": "object", "properties": {"type": {"const": "input_image"}}}}}}]}
        cases.append(case("responses-tool-schema", config=responses, body=tool_body))
        image_body = {"model": "gpt-6.1-sol", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}]}
        cases.append(case("responses-default-image", config=responses, body=image_body))
        cases.append(case("responses-image-unknown-model", config=responses, body={**image_body, "model": "unapproved-synthetic-model"}))
        # 同一OpenAI接続先への将来GPTは、登録一覧とモデル差分を追加せず操作の基本設定を使います。
        cls.future_gpt_cases = {}
        for operation, path, payload in (
            ("chat-completions", "/chat/completions", {"messages": [{"role": "user", "content": "synthetic"}], "max_completion_tokens": 16}),
            ("responses-create", "/responses", {"input": "synthetic", "max_output_tokens": 16}),
        ):
            identifier = "future-gpt-" + operation
            body = {"model": "gpt-future-synthetic", **payload}
            original = copy.deepcopy(inference["operationConstraints"][operation])
            if body["model"] in original["allowedModels"] or body["model"] in original.get("modelSettings", {}):
                raise AssertionError("The future-model fixture must be absent from both model tables")
            expected = copy.deepcopy(original)
            expected.pop("modelSettings", None)
            expected.pop("settingId", None)
            cls.future_gpt_cases[identifier] = (body, expected)
            cases.append(case(identifier, config=inference, api="openai-inference-v1", operation=operation,
                              path=path, headers={"Content-Type": "application/json"}, body=body,
                              applyChatNormalization=operation == 'chat-completions'))
        # Execute the shipped alias through profile, auth, original-byte limits,
        # target checks and the actual model-only normalization expression.
        alias_body = {"model": "codex-auto-review", "input": [{"type": "reasoning", "encrypted_content": "synthetic opaque"},
            {"type": "function_call_output", "call_id": "synthetic_call", "output": "synthetic output"}],
            "stream": True, "max_output_tokens": 32, "text": {"format": {"type": "json_schema", "name": "synthetic_review",
            "schema": {"type": "object", "properties": {"approved": {"type": "boolean"}}, "required": ["approved"], "additionalProperties": False}}}}
        cls.alias_body = alias_body
        alias_base = {"inputContracts": INPUT_TABLES["openai-inference-v1"], "config": inference, "api": "openai-inference-v1", "operation": "responses-create", "path": "/responses",
            "headers": {"Content-Type": "application/json"}, "body": alias_body}
        cases.append(case("alias-default", **alias_base))
        cases.append(case("alias-explicit", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": "response"}}))
        cases.append(case("alias-direct", **{**alias_base, "body": {**alias_body, "model": "gpt-5.6-luna"}}))
        cases.append(case("alias-unknown", **{**alias_base, "body": {**alias_body, "model": "unknown-alias"}}))
        cases.append(case("alias-pdf", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": "pdf"}}))
        cases.append(case("alias-unknown-header", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": "unregistered"}}))
        cases.append(case("alias-multiple-header", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": ["pdf", "compatible"]}}))
        cases.append(case("alias-chat", config=inference, api="openai-inference-v1", path="/chat/completions",
            headers={"Content-Type": "application/json"}, body={"model": "codex-auto-review", "messages": []}))
        cases.append(case("alias-no-key", **{**alias_base, "key": None}))
        cases.append(case("alias-saved-file-preserved", **{**alias_base, "body": {**alias_body,
                          "input": [{"type": "input_file", "file_id": "synthetic-file"}]},
                          "applyStatelessNormalization": True}))
        cases.append(case("alias-reject-forbidden-tools", **{**alias_base, "body": {**alias_body,
                          "tools": [{"type": "web_search"}]}}))
        cls.stateless_reference_cases = {}
        reference_variants = (
            ("absent", False, None, {}), ("string", True, "synthetic-reference", {"store": True}),
            ("empty-string", True, "", {"store": False}), ("null", True, None, {"store": None}),
            ("number", True, 1, {"store": 1}), ("boolean", True, True, {"store": "synthetic"}),
            ("array", True, ["synthetic-reference"], {"store": []}),
            ("object", True, {"id": "synthetic-reference"}, {"store": {"synthetic": True}}),
        )
        reference_inputs = []
        for field in ('previous_response_id', 'conversation'):
            for label, present, value, store_patch in reference_variants:
                fields = {field: value} if present else {}
                reference_inputs.append((field + '-' + label, fields, store_patch))
        for label, previous, conversation in (
            ('both-string', 'synthetic-response', 'synthetic-conversation'),
            ('both-null', None, None), ('previous-null', None, 'synthetic-conversation'),
            ('conversation-null', 'synthetic-response', None),
        ):
            reference_inputs.append((label, {'previous_response_id': previous, 'conversation': conversation}, {'store': True}))
        for label, fields, store_patch in reference_inputs:
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), "model": requested, **store_patch, **fields}
                identifier = "responses-reference-normalization-" + model_label + "-" + label
                expected = {**copy.deepcopy(body), "model": effective, "store": False}
                if 'conversation' in fields:
                    expected['conversation'] = None
                cls.stateless_reference_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, "body": body, "applyStatelessNormalization": True}))
        cls.conversation_id_cases = {}
        for label, present, value, store_patch in reference_variants:
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), "model": requested, **store_patch,
                        "previous_response_id": "synthetic-response", "conversation": {"id": "synthetic-conversation"}}
                if present:
                    body['conversation_id'] = value
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False,
                            'conversation': None}
                identifier = 'responses-conversation-id-preserved-' + model_label + '-' + label
                cls.conversation_id_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.prompt_normalization_cases = {}
        template = {'id': 'synthetic-template', 'version': '1', 'variables': {
            'document': {'type': 'input_file', 'file_id': 'synthetic-file'}, 'label': 'synthetic'}}
        prompt_inputs = []
        all_references = {'previous_response_id': 'synthetic-response',
                          'conversation': {'id': 'synthetic-conversation'},
                          'conversation_id': {'id': 'synthetic-legacy-conversation'}}
        for label, present, value, store_patch in reference_variants:
            prompt_inputs.append((label, present, value, all_references, store_patch))
        prompt_inputs.append(('saved-file-variables', True, template, all_references, {'store': True}))
        for label, other_fields in (
            ('prompt-only', {}), ('prompt-previous', {'previous_response_id': 'synthetic-response'}),
            ('prompt-conversation', {'conversation': 'synthetic-conversation'}),
            ('prompt-conversation-id', {'conversation_id': 'synthetic-legacy-conversation'}),
        ):
            prompt_inputs.append((label, True, template, other_fields, {'store': True}))
        for label, present, value, other_fields, store_patch in prompt_inputs:
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, **store_patch, **copy.deepcopy(other_fields)}
                if present:
                    body['prompt'] = copy.deepcopy(value)
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False}
                if 'conversation' in expected:
                    expected['conversation'] = None
                identifier = 'responses-prompt-normalization-' + model_label + '-' + label
                cls.prompt_normalization_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.background_normalization_cases = {}
        for label, present, value in (('absent', False, None), ('true', True, True), ('false', True, False),
                                     ('null', True, None), ('string', True, 'synthetic'), ('number', True, 1),
                                     ('array', True, [True]), ('object', True, {'enabled': True})):
            for stream in (False, True):
                for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                          ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                    body = {**copy.deepcopy(alias_body), 'model': requested, 'stream': stream, 'store': True,
                            **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template)}
                    if present:
                        body['background'] = value
                    expected = {**copy.deepcopy(body), 'model': effective, 'store': False,
                                'conversation': None}
                    if present:
                        expected['background'] = False
                    identifier = 'responses-background-normalization-' + model_label + '-' + str(stream) + '-' + label
                    cls.background_normalization_cases[identifier] = (body, expected)
                    cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.responses_input_preservation_cases = {}
        for label, present, value in (
            ('absent', False, None), ('null', True, None), ('number', True, 1), ('boolean', True, True),
            ('object', True, {'text': 'synthetic'}),
            ('object-saved-file', True, {'type': 'input_file', 'file_id': 'synthetic-file'}),
            ('string', True, 'synthetic'), ('empty-string', True, ''),
            ('array', True, [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic'}]}]),
            ('empty-array', True, []),
        ):
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'store': True, 'background': True,
                        **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template)}
                if present:
                    body['input'] = copy.deepcopy(value)
                else:
                    body.pop('input')
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False, 'background': False,
                            'conversation': None}
                identifier = 'responses-input-preserved-' + model_label + '-' + label
                cls.responses_input_preservation_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.item_reference_normalization_cases = {}
        for label, original_input, expected_input in responses_item_reference_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'input': original_input,
                        'store': True, 'background': True, **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template)}
                expected = {**copy.deepcopy(body), 'model': effective, 'input': expected_input, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-item-reference-normalization-' + model_label + '-' + label
                cls.item_reference_normalization_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.untyped_item_reference_cases = {}
        for label, original_input, expected_input in responses_untyped_item_reference_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'input': original_input,
                        'store': True, 'background': True, **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template)}
                expected = {**copy.deepcopy(body), 'model': effective, 'input': expected_input, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-untyped-item-reference-' + model_label + '-' + label
                cls.untyped_item_reference_cases[identifier] = (body, expected)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        cls.responses_file_id_cases = {}
        cls.responses_include_cases = {}
        for label, fields, status in responses_include_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'store': True, 'background': True,
                        **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template), **fields}
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-include-' + model_label + '-' + label
                cls.responses_include_cases[identifier] = (body, expected, status == 200, 1)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            configured = copy.deepcopy(inference)
            configured['operationConstraints']['chat-completions']['kind'] = kind
            body = {'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'synthetic'}],
                    'input': 'synthetic', 'include': [None, 'synthetic.include'], 'store': True}
            identifier = 'other-kind-include-scope-' + kind
            expected = {**body, 'store': False} if kind == 'bodyModel' else body
            cls.responses_include_cases[identifier] = (body, expected, True, 0)
            cases.append(case(identifier, config=configured, api='openai-inference-v1', operation='chat-completions',
                              path='/chat/completions', headers={'Content-Type': 'application/json'}, body=body,
                              applyStatelessNormalization=True))
        cls.responses_tool_choice_cases = {}
        for label, fields, status in responses_tool_choice_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'store': True, 'background': True,
                        **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template),
                        'include': ['synthetic.include'], **fields}
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-tool-choice-' + model_label + '-' + label
                cls.responses_tool_choice_cases[identifier] = (body, expected, status == 200, 1)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            configured = copy.deepcopy(inference)
            configured['operationConstraints']['chat-completions']['kind'] = kind
            body = {'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'synthetic'}],
                    'input': 'synthetic', 'tool_choice': {'type': 'allowed_tools', 'tools': [{'type': 'mcp'}]},
                    'store': True}
            identifier = 'other-kind-tool-choice-scope-' + kind
            expected = {**body, 'store': False} if kind == 'bodyModel' else body
            cls.responses_tool_choice_cases[identifier] = (body, expected, True, 0)
            cases.append(case(identifier, config=configured, api='openai-inference-v1', operation='chat-completions',
                              path='/chat/completions', headers={'Content-Type': 'application/json'}, body=body,
                              applyStatelessNormalization=True))
        cls.responses_code_interpreter_cases = {}
        for label, fields, status in responses_code_interpreter_input_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'store': True, 'background': True,
                        **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template), **fields}
                expected = {**copy.deepcopy(body), 'model': effective, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-code-interpreter-' + model_label + '-' + label
                cls.responses_code_interpreter_cases[identifier] = (body, expected, status == 200, 1)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            configured = copy.deepcopy(inference)
            configured['operationConstraints']['chat-completions']['kind'] = kind
            body = {'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'synthetic'}],
                    'input': [{'type': 'code_interpreter_call', 'id': 'synthetic-code-call',
                               'container_id': 'synthetic-container'}], 'store': True}
            identifier = 'other-kind-code-interpreter-scope-' + kind
            expected = {**body, 'store': False} if kind == 'bodyModel' else body
            cls.responses_code_interpreter_cases[identifier] = (body, expected, True, 0)
            cases.append(case(identifier, config=configured, api='openai-inference-v1', operation='chat-completions',
                              path='/chat/completions', headers={'Content-Type': 'application/json'}, body=body,
                              applyStatelessNormalization=True))
        for label, fields, status, expected_fields in responses_file_id_null_cases():
            for model_label, requested, effective in (("alias", "codex-auto-review", "gpt-5.6-luna"),
                                                      ("direct", "gpt-5.6-luna", "gpt-5.6-luna")):
                body = {**copy.deepcopy(alias_body), 'model': requested, 'store': True, 'background': True,
                        **copy.deepcopy(all_references), 'prompt': copy.deepcopy(template), **fields}
                expected = {**copy.deepcopy(body), 'model': effective, **expected_fields, 'store': False,
                            'background': False, 'conversation': None}
                identifier = 'responses-file-id-null-' + model_label + '-' + label
                cls.responses_file_id_cases[identifier] = (body, expected, status == 200, 1)
                cases.append(case(identifier, **{**alias_base, 'body': body, 'applyStatelessNormalization': True}))
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            configured = copy.deepcopy(inference)
            configured['operationConstraints']['chat-completions']['kind'] = kind
            body = {'model': 'gpt-6.1-sol', 'messages': [{'role': 'user', 'content': 'synthetic'}],
                    'input': [{'file_id': 'synthetic-file', 'file_ids': None, 'vector_store_ids': []}],
                    'store': True, 'background': True}
            identifier = 'other-kind-file-id-scope-' + kind
            cls.responses_file_id_cases[identifier] = (body, {**body, 'store': False} if kind == 'bodyModel' else body, True, 0)
            cases.append(case(identifier, config=configured, api='openai-inference-v1', operation='chat-completions',
                              path='/chat/completions', headers={'Content-Type': 'application/json'}, body=body,
                              applyStatelessNormalization=True))
        cls.hosted_target_cases = {}
        hosted_operation = 'synthetic-hosted-invoke'
        hosted_base = {'api': 'synthetic-hosted-api', 'operation': hosted_operation, 'path': '/responses',
                       'headers': {'Content-Type': 'application/json'}, 'applyStatelessNormalization': True,
                       'applyHostedNormalization': True}
        hosted_config = {'mode': 'subscriptionKey', 'operationConstraints': {hosted_operation: {
            'kind': 'hostedAgentPost', 'bodyFormat': 'json', 'maxRequestBytes': 2048}}}
        hosted_valid = {'input': 'synthetic', 'store': False, 'metadata': {'sample_mode': 'fixed'}}
        hosted_cases = [('hosted-' + label, 'hostedAgentPost', patch, status, reads)
                        for label, patch, status, reads in hosted_agent_target_cases()]
        # 親フローでは、SDKの入力Items・ID・任意contentは保存指定以外の原値を保ちます。
        inline_messages = [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}
                           for text in ('合成 first 🧪', ' \t', '合成 third')]
        last_id = copy.deepcopy(inline_messages)
        last_id[-1]['id'] = 'synthetic-saved-item'
        last_file = copy.deepcopy(inline_messages)
        last_file[-1]['content'][0]['file_id'] = 'synthetic-saved-file'
        multiple_content = copy.deepcopy(inline_messages)
        for message, text in zip(multiple_content, ('', '合成 second', '合成 last 🧪')):
            message['content'].append({'type': 'input_text', 'text': text})
        last_part_id = copy.deepcopy(multiple_content)
        last_part_id[-1]['content'][-1]['id'] = 'synthetic-saved-item'
        last_part_file = copy.deepcopy(multiple_content)
        last_part_file[-1]['content'][-1]['file_id'] = 'synthetic-saved-file'
        last_part_reference = copy.deepcopy(multiple_content)
        last_part_reference[-1]['content'][-1] = {'type': 'input_file', 'file_id': 'synthetic-saved-file'}
        empty_message = {'role': 'user', 'content': []}
        for label, messages, status in (
            ('multiple-input-wire', inline_messages, 200),
            ('multiple-input-wire-last-id', last_id, 200),
            ('multiple-input-wire-last-file', last_file, 200),
            ('multiple-content-wire', multiple_content, 200),
            ('multiple-content-wire-last-part-id', last_part_id, 200),
            ('multiple-content-wire-last-part-file', last_part_file, 200),
            ('multiple-content-wire-last-part-reference', last_part_reference, 200),
            ('empty-input-wire', [], 200),
            ('empty-content-wire', [empty_message], 200),
            ('empty-content-wire-next-id', [empty_message, {**empty_message, 'id': 'synthetic-saved-item'}], 200),
            ('empty-content-wire-next-file', [empty_message, {'role': 'user', 'content': [
                {'type': 'input_file', 'file_id': 'synthetic-saved-file'}]}], 200),
            ('empty-content-wire-next-invalid-part', [empty_message, {'role': 'user', 'content': [None]}], 200),
            ('multiple-input-wire-last-code-interpreter', [*inline_messages,
                {'type': 'code_interpreter_call', 'id': 'synthetic-call', 'container_id': 'synthetic-container',
                 'code': 'print(1)', 'outputs': []}], 403),
            ('code-interpreter-deep-content-wire', [{'role': 'user', 'content': [
                {'type': 'code_interpreter_call', 'id': 'synthetic-call'}]}], 200),
            ('code-interpreter-deep-metadata-wire', [{'role': 'user', 'content': 'synthetic',
                'metadata': {'type': 'code_interpreter_call'}}], 200),
            ('code-interpreter-non-array-input-wire', {'type': 'code_interpreter_call', 'id': 'synthetic-call'}, 200),
            ('code-interpreter-non-string-type-wire', [{'type': {'value': 'code_interpreter_call'},
                'id': 'synthetic-item'}], 200),
            ('mixed-primitive-input-wire', [None, 'synthetic', 1, False, []], 200),
        ):
            body = {**copy.deepcopy(hosted_valid), 'input': messages, 'stream': True}
            raw = ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n  '
            hosted_cases.append(('hosted-' + label, 'hostedAgentPost', {'body': body, 'rawBody': raw}, status, 1))
        for kind, status in (('statelessResponses', 400),):
            for label, patch, reads in json_object_body_failure_cases():
                hosted_cases.append((kind + '-' + label, kind, {'body': hosted_valid, **patch}, status, reads))
        hosted_cases.extend(('a2a-' + label, 'a2aJsonRpc', patch, status, reads)
                            for label, patch, status, reads in a2a_json_rpc_target_cases())
        for kind in ('bodyModel', 'textEmbeddings', 'rerank'):
            hosted_cases.append((kind + '-malformed-scope', kind, {'body': hosted_valid, 'rawBody': '{'}, 200, 0))
        for label, patch in (('missing-settings', {'targetConstraintMissing': True}),
                             ('malformed-settings', {'targetConstraintRaw': '{'})):
            hosted_cases.append(('hosted-' + label, 'hostedAgentPost', {'body': hosted_valid, **patch}, 500, 0))
        for label, kind, patch, status, reads in hosted_cases:
            identifier = 'parent-target-' + label
            configured = copy.deepcopy(hosted_config)
            configured['operationConstraints'][hosted_operation]['kind'] = kind
            if kind == 'a2aJsonRpc':
                configured['operationConstraints'][hosted_operation]['allowedMethods'] = ['message/send']
            original = None if patch.get('bodyAbsent') else patch.get('rawBody', json.dumps(patch['body'], separators=(',', ':')))
            cls.hosted_target_cases[identifier] = (original, status, reads, kind)
            cases.append(case(identifier, **{**hosted_base, 'config': configured, **patch}))
        cls.hosted_normalization_cases = {}

        def hosted_normalization_case(label, kind, body, agent='', status=200, **patch):
            configured = copy.deepcopy(hosted_config)
            configured['operationConstraints'][hosted_operation]['kind'] = kind
            if patch.pop('largeBodyLimit', False):
                configured['operationConstraints'][hosted_operation]['maxRequestBytes'] = 32
            raw = patch.get('rawBody', ' \t\n' + json.dumps(body, ensure_ascii=False, indent=2) + '\n ')
            expected = copy.deepcopy(body)
            selected = kind == 'hostedAgentPost' or bool(agent)
            if status == 200:
                if kind == 'hostedAgentPost':
                    expected['store'] = False
                    if 'conversation' in expected:
                        expected['conversation'] = None
                    if 'background' in expected:
                        expected['background'] = False
                if agent:
                    metadata = copy.deepcopy(expected.get('metadata'))
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata['core_parent_request_id'] = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
                    expected['metadata'] = metadata
            identifier = 'hosted-normalization-' + label
            cls.hosted_normalization_cases[identifier] = (raw, body, expected, status, selected, kind, agent)
            cases.append(case(identifier, **{**hosted_base, 'config': configured, 'body': body,
                'rawBody': raw, 'targetAgent': agent, **patch}))

        # Agent名に依存せず、保存指定の省略と全JSON型を同じHosted本文編集へ渡します。
        stores = [('omitted', {}), ('false', {'store': False}), ('true', {'store': True}),
                  ('null', {'store': None}), ('string-true', {'store': 'true'}),
                  ('string-false', {'store': 'false'}), ('number', {'store': 1}),
                  ('array', {'store': []}), ('object', {'store': {}})]
        complete_items = [
            {'type': 'reasoning', 'id': 'synthetic-reasoning',
             'summary': [{'type': 'summary_text', 'text': '合成 summary'}], 'encrypted_content': 'synthetic-encrypted'},
            {'type': 'function_call', 'id': 'synthetic-function', 'call_id': 'synthetic-call',
             'name': 'synthetic_tool', 'arguments': '{"value":1}', 'status': 'completed'},
            {'type': 'function_call_output', 'call_id': 'synthetic-call', 'output': '合成 result', 'status': 'completed'},
        ]
        hosted_options = {
            'max_output_tokens': 0, 'temperature': 'synthetic', 'text': {'format': {'type': 'json_object'}},
            'reasoning': {'effort': 'synthetic'}, 'tool_choice': {'type': 'function', 'name': 'registered_tool'},
            'future_option': {'instructions': 'nested-retained', 'tools': [None],
                              'previous_response_id': 'nested-response', 'agent_session_id': 'nested-session'},
        }
        state_values = [('omitted', None), ('null', None), ('false', False), ('true', True),
                        ('string', 'synthetic'), ('number', 1.5), ('array', ['synthetic']),
                        ('object', {'id': 'synthetic-conversation'})]
        for agent_label, agent in (('without-agent', ''), ('with-agent', 'synthetic-agent')):
            for label, fields in stores:
                hosted_normalization_case(agent_label + '-store-' + label, 'hostedAgentPost',
                    {'input': inline_messages, 'stream': True, **fields}, agent)
            for label, fields in (
                ('omitted', {}), ('null', {'metadata': None}), ('array', {'metadata': ['synthetic']}),
                ('string', {'metadata': 'synthetic'}), ('number', {'metadata': 1}),
                ('object', {'metadata': {'client_note': '合成 metadata'}}),
                ('client-parent', {'metadata': {'client_note': 'retained', 'core_parent_request_id': 'client-parent'}}),
            ):
                hosted_normalization_case(agent_label + '-metadata-' + label, 'hostedAgentPost',
                    {'input': inline_messages, 'stream': None, 'store': True, **fields}, agent)
            for label, input_value, stream_fields in (
                ('input-string-stream-omitted', '合成 question 🧪', {}),
                ('input-empty-stream-false', '', {'stream': False}),
                ('input-blank-stream-string', ' \t', {'stream': 'true'}),
                ('input-empty-array-stream-number', [], {'stream': 1}),
                ('input-empty-content-stream-array', [{'role': 'user', 'content': []}], {'stream': []}),
                ('input-multiple-stream-object', multiple_content, {'stream': {}}),
                ('input-null', None, {}),
                ('input-boolean', False, {}),
                ('input-number', 1, {}),
                ('input-object', {'role': 'user', 'content': 'synthetic'}, {}),
                ('input-mixed-primitives', [None, 'synthetic', 1, False, []], {}),
                ('input-text-messages', [{'role': 'user', 'content': '合成 question'},
                                         {'role': 'assistant', 'content': '合成 answer'}], {}),
                ('input-complete-items', complete_items, {}),
            ):
                hosted_normalization_case(agent_label + '-' + label, 'hostedAgentPost',
                    {'input': input_value, 'store': None, **stream_fields}, agent)
            hosted_normalization_case(agent_label + '-input-omitted', 'hostedAgentPost',
                {'store': True, 'metadata': {'client_note': 'retained'}}, agent)
            # 各JSON型を独立に指定し、もう一方を省略したまま同じ本文編集で状態指定だけを無効化します。
            for field in ('conversation', 'background'):
                for label, value in state_values:
                    fields = {} if label == 'omitted' else {field: value}
                    hosted_normalization_case(agent_label + '-' + field + '-' + label, 'hostedAgentPost',
                        {'input': complete_items, 'stream': None, 'store': True,
                         **copy.deepcopy(hosted_options), **fields}, agent)
            for label, fields, input_value, stream_value in (
                ('active', {'conversation': {'id': 'synthetic-conversation'}, 'background': True}, complete_items, True),
                ('null', {'conversation': None, 'background': None}, None, False),
                ('other-types', {'conversation': ['synthetic'], 'background': {'enabled': True}},
                 {'content': 'synthetic'}, 'true'),
            ):
                hosted_normalization_case(agent_label + '-combined-state-' + label, 'hostedAgentPost',
                    {'input': input_value, 'stream': stream_value, 'store': True,
                     'metadata': {'client_note': 'retained'}, **copy.deepcopy(hosted_options), **fields}, agent)
        # 既存metadata対象の非Hostedではstoreを補完・変更せず、両条件falseならwireも読取りも保持します。
        for kind in ('bodyModel', 'textEmbeddings', 'rerank', 'registeredOperation'):
            for agent_label, agent in (('without-agent', ''), ('with-agent', 'synthetic-agent')):
                for label, fields in (('omitted', {}), ('true', {'store': True}), ('null', {'store': None})):
                    hosted_normalization_case(kind + '-' + agent_label + '-store-' + label, kind,
                        {'input': inline_messages, 'stream': 'true', 'metadata': {'client_note': 'retained'}, **fields}, agent)
                hosted_normalization_case(kind + '-' + agent_label + '-state-options', kind,
                    {'input': '合成 neighbor', 'stream': 'true', 'store': True,
                     'metadata': {'client_note': 'retained'}, 'conversation': {'id': 'synthetic-conversation'},
                     'background': {'enabled': True}, **copy.deepcopy(hosted_options)}, agent)
        # 固定Agent定義と状態selectorは、指定値がnullでも本文編集・利用枠の前で拒否します。
        fixed_or_state_fields = (
            ('instructions', 'synthetic override'), ('tools', []),
            ('agent_reference', {'name': 'synthetic-other-agent', 'version': '2'}),
            ('prompt', {'id': 'synthetic-prompt', 'variables': {'value': 'synthetic'}}),
            ('previous_response_id', 'synthetic-response'), ('agent_session_id', 'synthetic-session'),
        )
        for agent_label, agent in (('without-agent', ''), ('with-agent', 'synthetic-agent')):
            for field, value in fixed_or_state_fields:
                for label, selected_value in (('specified', value), ('null', None)):
                    hosted_normalization_case(agent_label + '-rejected-' + field + '-' + label, 'hostedAgentPost',
                        {'input': '合成 rejected', 'store': True, 'conversation': {'id': 'synthetic-conversation'},
                         'background': True, **copy.deepcopy(hosted_options), field: selected_value}, agent, 403)
        # 先行する本文サイズ・本文読取り・内部設定・CI直接参照拒否では編集と利用枠へ進みません。
        for agent_label, agent in (('without-agent', ''), ('with-agent', 'synthetic-agent')):
            for label, body, status, patch in (
                ('input-code-interpreter', {'input': [{'type': 'code_interpreter_call',
                    'container_id': 'synthetic-container', 'id': 'synthetic-call'}], 'store': True}, 403, {}),
                ('malformed-body', {'input': 'synthetic', 'store': True}, 400, {'rawBody': '{'}),
                ('missing-settings', {'input': 'synthetic', 'store': True}, 500, {'targetConstraintMissing': True}),
                ('too-large', {'input': 'synthetic' * 20, 'store': True}, 413, {'largeBodyLimit': True}),
            ):
                hosted_normalization_case(agent_label + '-rejected-' + label, 'hostedAgentPost', body, agent, status, **patch)
        cls.dedicated_target_cases = {}
        for kind, label, patch in dedicated_target_route_cases():
            identifier = 'parent-dedicated-' + kind + '-' + label
            constraint = {'kind': kind, 'bodyFormat': 'json', 'maxRequestBytes': 2048}
            if kind in ('realtimeHangup', 'documentAnalysisStatus'):
                constraint['bodyFormat'] = 'none'
            elif kind == 'documentAnalysis':
                constraint.update(bodyFormat='binary', requestBodySizeCheck='declared')
            configured = {'mode': 'subscriptionKey', 'operationConstraints': {patch['operation']: constraint}}
            original = None if patch.get('bodyAbsent') else patch.get('rawBody', json.dumps(patch['body'], separators=(',', ':')))
            cls.dedicated_target_cases[identifier] = original
            cases.append(case(identifier, api='synthetic-dedicated-api', config=configured,
                              headers={'Content-Type': 'application/json'}, applyStatelessNormalization=True, **patch))
        cases.append(case("alias-large-output-limit", **{**alias_base, "body": {**alias_body, "max_output_tokens": 9223372036854775807}}))
        cases.append(case("alias-mixed-token-fields", **{**alias_base, "body": {**alias_body, "max_tokens": 1}}))
        bounded = json.loads(json.dumps(inference))
        bounded["operationConstraints"]["responses-create"]["maxRequestBytes"] = 2048
        for settings in bounded["operationConstraints"]["responses-create"].get("modelSettings", {}).values():
            settings["maxRequestBytes"] = 2048
        # Whitespace must count before any JSON compaction/model rewrite.
        cases.append(case("alias-wire-overflow", **{**alias_base, "config": bounded, "rawBody": " " * 2048 + json.dumps(alias_body)}))
        bounded["operationConstraints"]["responses-create"]["modelSettings"]["gpt-5.6-luna"]["maxRequestBytes"] = 256
        cases.append(case("alias-profile-overflow", **{**alias_base, "config": bounded}))
        cases.append(case("selected-size", body={"model": "shared", "messages": [{"content": "x" * 600}], "max_completion_tokens": 16}))
        native_body = {"model": "gpt-6.1-sol", "stream": True,
            "provider_extension": {"arbitrary": ["SELECT * FROM synthetic;", "<?php $function(); ?>"]},
            "tools": [{"type": "function", "name": "synthetic_tool", "parameters": {
                "type": "object", "properties": {"arbitrary-code-key": {"type": "string"}},
                "additionalProperties": True}}]}
        for label, operation, path, data in (
            ("chat", "chat-completions", "/chat/completions", {"messages": [{"role": "tool", "content": "synthetic code"}]}),
            ("responses", "responses-create", "/responses", {"input": [
                {"type": "function_call_output", "call_id": "synthetic_call", "output": "synthetic code"},
                {"type": "reasoning", "encrypted_content": "synthetic opaque value"}]})):
            base = {"inputContracts": INPUT_TABLES["openai-inference-v1"], "config": inference, "api": "openai-inference-v1", "operation": operation,
                "path": path, "body": {**native_body, **data}, "headers": {"Content-Type": "application/json"}}
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-json-charset", **{**base, "headers": {"Content-Type": "Application/JSON; charset=utf-8"}}))
            cases.append(case(label + "-revision-query", **{**base, "api": "openai-inference-v1;rev=2", "query": {"input.input": "synthetic"}}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("same-name-query", {"query": {"input.output.text": "synthetic"}}),
                ("duplicate-query", {"query": {"messages.content": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}})):
                identifier = label + "-different-method" if suffix == "method" else label + "-reject-" + suffix
                cases.append(case(identifier, **{**base, **patch}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"provider-option": "synthetic"}, "headers": {"Content-Type": "text/plain"}}))
            cases.append(case(label + "-other-operation", **{**base, "path": "/different-operation", "query": {"provider-option": "synthetic"}}))
        cls.responses_call_id_body = {**native_body, "input": [
            {"type": "function_call", "call_id": "call_synthetic_0x41424344", "name": "synthetic_tool", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_synthetic_0x41424344", "output": "Synthetic result"}]}
        correlation = {"inputContracts": INPUT_TABLES["openai-inference-v1"], "config": inference, "api": "openai-inference-v1", "operation": "responses-create",
                       "path": "/responses", "body": cls.responses_call_id_body,
                       "headers": {"Content-Type": "application/json"}}
        cases.append(case("responses-call-id-native", **correlation))
        for suffix, patch in (
            ("query", {"query": {"input.call_id": "call_synthetic_0x41424344"}}),
            ("duplicate-query", {"query": {"input.call_id": ["call_synthetic_0x41424344", "call_synthetic_other"]}}),
            ("query-before-body", {"query": {"input.call_id": "call_synthetic_0x41424344"}, "rawBody": "{"}),
        ):
            cases.append(case("responses-call-id-reject-" + suffix, **{**correlation, **patch}))
        multipart = ('--synthetic-image-boundary\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n'
            'Keep the synthetic <script> example as text.\r\n--synthetic-image-boundary\r\n'
            'Content-Disposition: form-data; name="image"; filename="synthetic.png"\r\n'
            'Content-Type: image/png\r\n\r\nsynthetic image bytes\r\n--synthetic-image-boundary--\r\n')
        image_contracts = [
            ("openai-image", "openai-images-v1", "image-generate", "/generations", "json", None),
            ("flux-generate", "blackforest-images-v1", "flux2-generate", "/flux-2/generations", "json", None),
            ("flux-edit", "blackforest-images-v1", "flux2-edit", "/flux-2/edits", "json", None),
        ]
        for deployment in apis["openai-images-v1"]["authorization"]["operationConstraints"]["image-edit"]["allowedModels"]:
            image_contracts.append(("openai-edit-" + deployment, "openai-images-v1", "image-edit", "/deployments/{deployment}/edits", "multipart", deployment))
        cls.image_cases = []
        cls.image_bodies = {}
        cls.image_count_cases = {}
        for label, api, operation, path, body_format, deployment in image_contracts:
            cls.image_cases.append(label)
            media = "multipart/form-data; boundary=synthetic-image-boundary" if body_format == "multipart" else "application/json; charset=utf-8"
            body = ({"model": "gpt-image-2", "prompt": "Draw the synthetic SELECT and <script> examples.", "n": 11,
                "provider_extension": {"synthetic": True}} if api == "openai-images-v1" else {
                "model": "FLUX.2-flex", "prompt": "Draw the synthetic SELECT example.", "input_image": "c3ludGhldGlj",
                "input_image_2": "c2Vjb25k", "n": 2, "num_images": 2, "provider_extension": {"synthetic": True}})
            cls.image_bodies[label] = body
            base = {"inputContracts": INPUT_TABLES[api], "config": apis[api]["authorization"], "api": api, "operation": operation,
                "path": path, "headers": {"Content-Type": media}, "body": body}
            if deployment is not None:
                base.update(rawBody=multipart, parameters={"deployment": deployment})
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            if label == "openai-image":
                for suffix, count in (("zero", 0), ("null", None), ("string", "synthetic")):
                    identifier = label + "-count-" + suffix
                    count_body = {**body, "n": count}
                    cls.image_count_cases[identifier] = count_body
                    cases.append(case(identifier, **{**base, "body": count_body}))
            elif label == "flux-generate":
                cases.append(case("flux-generate-unlisted-output-format", **{**base, "body": {**body, "output_format": "synthetic"}}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("same-name-query", {"query": {"prompt": "synthetic"}}),
                ("base64-query", {"query": {"input_image": "synthetic"}}),
                ("api-version", {"query": {"api-version": "preview"}}),
                ("duplicate-query", {"query": {"prompt": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"prompt": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "application/json" if body_format == "multipart" else "multipart/form-data; boundary=synthetic"}}),
                ("duplicate-type", {"headers": {"Content-Type": [media, media]}}),
                ("wrong-route", {"path": "/different-operation"})):
                identifier = label + "-different-method" if suffix == "method" else label + "-reject-" + suffix
                cases.append(case(identifier, **{**base, **patch}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"provider-option": "synthetic"}}))
        cls.embedding_cases = []
        cls.embedding_bodies = {}
        for label, api, operation, path, body in (
            ("openai-embedding", "openai-embeddings-v1", "embeddings", "/embeddings", {
                "model": "text-embedding-3-small", "input": ["Synthetic SELECT and <script> examples."],
                "dimensions": 256, "encoding_format": "base64", "provider_extension": {"synthetic": True}}),
            ("cohere-embedding", "cohere-inference-v1", "cohere-embeddings", "/embed", {
                "model": "embed-v-4-0", "texts": ["Synthetic SELECT and <script> examples."],
                "input_type": "search_query", "output_dimension": 256, "embedding_types": ["float"],
                "truncate": "END", "provider_extension": {"synthetic": True}})):
            cls.embedding_cases.append(label)
            cls.embedding_bodies[label] = body
            base = {"inputContracts": INPUT_TABLES[api], "config": apis[api]["authorization"], "api": api, "operation": operation,
                "path": path, "headers": {"Content-Type": "application/json"}, "body": body}
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-json-charset", **{**base, "headers": {"Content-Type": "Application/JSON; charset=utf-8"}}))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("input-query", {"query": {"input": "synthetic"}}),
                ("texts-query", {"query": {"texts": "synthetic"}}),
                ("duplicate-query", {"query": {"texts": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}}),
                ("wrong-route", {"path": "/different-operation"})):
                identifier = label + "-different-method" if suffix == "method" else label + "-reject-" + suffix
                cases.append(case(identifier, **{**base, **patch}))
            cases.append(case(label + "-revision-query", **{**base, "api": api + ";rev=2", "query": {"texts": "synthetic"}}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"input": "synthetic"}, "headers": {"Content-Type": "text/plain"}}))
            if label == "openai-embedding":
                for version in ("v1", "preview"):
                    cases.append(case(label + "-version-" + version, **{**base, "query": {"api-version": version}}))
                    cases.append(case(label + "-revision-version-" + version, **{**base, "api": api + ";rev=2", "query": {"api-version": version}}))
                for suffix, query in (
                    ("both-versions", {"api-version": ["v1", "preview"]}),
                    ("duplicate-version", {"api-version": ["v1", "v1"]}),
                    ("empty-version", {"api-version": ""}),
                    ("unknown-version", {"api-version": "synthetic-version"}),
                    ("uppercase-name", {"API-VERSION": "v1"}),
                    ("uppercase-value", {"api-version": "V1"}),
                    ("version-with-input", {"api-version": "v1", "input": "synthetic"})):
                    cases.append(case(label + "-reject-" + suffix, **{**base, "query": query}))
                for suffix, native_input in (("scalar", "Synthetic text"), ("tokens", [1, 2]), ("token-batches", [[1, 2], [3]])):
                    cases.append(case(label + "-input-" + suffix, **{**base, "body": {**body, "input": native_input}}))
            else:
                cases.append(case(label + "-reject-api-version", **{**base, "query": {"api-version": "v1"}}))
        cls.native_contract_cases = []
        cls.native_contract_bodies = {}
        transcription_body = ('--synthetic-audio-boundary\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n'
            'Transcribe the synthetic SELECT and <script> examples.\r\n--synthetic-audio-boundary\r\n'
            'Content-Disposition: form-data; name="file"; filename="synthetic.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\nRIFF synthetic audio\r\n--synthetic-audio-boundary--\r\n')
        for label, api, operation, path, kind, body_format, media, raw in (
            ("rerank", "cohere-inference-v1", "rerank", "/rerank", "rerank", "json", "application/json", None),
            ("transcription", "openai-audio-v1", "audio-transcriptions", "/deployments/{deployment}/transcriptions",
             "pathModel", "multipart", "multipart/form-data; boundary=synthetic-audio-boundary", transcription_body),
            ("speech-tts", "azure-speech-v1", "tts", "/synthesize", "registeredOperation", "text",
             "application/ssml+xml", '<speak version="1.0"><voice name="synthetic">Synthetic &amp; text.</voice></speak>'),
            ("speech-stt", "azure-speech-v1", "stt", "/recognize", "registeredOperation", "binary",
             "audio/wav; codecs=audio/pcm; samplerate=16000", "RIFF synthetic audio")):
            cls.native_contract_cases.append(label)
            config = apis[api]["authorization"]
            body = {"model": "Cohere-rerank-v4.0-fast", "query": "Synthetic SELECT and <script> examples.",
                    "documents": ["Synthetic document"], "provider_extension": {"synthetic": True}}
            base = {"inputContracts": INPUT_TABLES[api], "config": config, "api": api, "operation": operation, "path": path,
                    "headers": {"Content-Type": media}, "body": body}
            if raw is not None:
                base["rawBody"] = raw
            if label == "transcription":
                base["parameters"] = {"deployment": config["operationConstraints"][operation]["allowedModels"][0]}
            cls.native_contract_bodies[label] = raw if raw is not None else json.dumps(body, separators=(",", ":"))
            cases.append(case(label + "-native-request", **base))
            mixed_media = media.replace(media.split(";", 1)[0], media.split(";", 1)[0].upper(), 1)
            cases.append(case(label + "-mixed-media", **{**base, "headers": {"Content-Type": mixed_media}}))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("excluded-field-query", {"query": {"documents" if label == "rerank" else "prompt": "synthetic"}}),
                ("duplicate-query", {"query": {"input": ["synthetic", "second"]}}),
                ("api-version", {"query": {"api-version": "preview"}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": [media, media]}}),
                ("combined-type", {"headers": {"Content-Type": media.split(";")[0] + ", " + media.split(";")[0]}}),
                ("wrong-route", {"path": path + "/neighbor"})):
                identifier = label + "-different-method" if suffix == "method" else label + "-reject-" + suffix
                cases.append(case(identifier, **{**base, **patch}))
            cases.append(case(label + "-revision-query", **{**base, "api": api + ";rev=2", "query": {"input": "synthetic"}}))
            for suffix, patch in (("other-api", {"api": "another-api"}),
                                  ("other-operation", {"operation": "synthetic-other", "path": "/synthetic-other",
                                   "config": {**config, "operationConstraints": {"synthetic-other": config["operationConstraints"][operation]}}})):
                cases.append(case(label + "-" + suffix, **{**base, **patch,
                                  "headers": {"Content-Type": "text/plain"}, "query": {"provider-option": "synthetic"}}))
            if label in ("rerank", "transcription"):
                patch = {"body": {**body, "model": "synthetic-future-model"}} if label == "rerank" else {"parameters": {"deployment": "synthetic-future-model"}}
                cases.append(case(label + "-future-model", **{**base, **patch}))
        # 標準の転送専用4操作で、操作設定が実際の引渡し式を通って上限部品へ届くことを確認します。
        # 申告超過の合成ヘッダーは、HTTP層の本文フレーミングや実転送を再現するものではありません。
        cls.declared_size_cases = {}
        for label, api, operation, path, media, raw in (
            ("speech-stt", "azure-speech-v1", "stt", "/recognize", "audio/wav", cls.native_contract_bodies["speech-stt"]),
            ("transcription", "openai-audio-v1", "audio-transcriptions", "/deployments/{deployment}/transcriptions",
             "multipart/form-data; boundary=synthetic-audio-boundary", transcription_body),
            ("image-edit", "openai-images-v1", "image-edit", "/deployments/{deployment}/edits",
             "multipart/form-data; boundary=synthetic-image-boundary", multipart),
            ("document", "document-analysis-v1", "analyze_document", "/analyze",
             "application/pdf", "%PDF-synthetic document")):
            config = apis[api]["authorization"]
            constraint = config["operationConstraints"][operation]
            base = {"inputContracts": INPUT_TABLES[api], "config": config, "api": api,
                    "operation": operation, "path": path, "rawBody": raw}
            if "parameter" in constraint:
                base["parameters"] = {constraint["parameter"]: constraint["allowedModels"][0]}
            for suffix, length in (("lengthless", None), ("declared-within-bound", str(len(raw.encode("utf-8")))),
                                   ("declared-over-bound", str(constraint["maxRequestBytes"] + 1))):
                identifier = "declared-upload-" + label + "-" + suffix
                headers = {"Content-Type": media}
                if length is not None:
                    headers["Content-Length"] = length
                item = case(identifier, **base, headers=headers)
                cases.append(item)
                cls.declared_size_cases[identifier] = (item, suffix == "declared-over-bound")
        # TTSの共有サイズ部品は本文を読まず、専用部品がサイズと宣言を1回の読取りで確認します。
        cls.speech_guard_cases = {}
        normal_ssml = '<speak>ok</speak>'
        declaration = '<!DOCTYPE speak SYSTEM "https://example.invalid/never-request.dtd"><speak>ok</speak>'
        for label, raw, maximum, status in (
            ("exact", normal_ssml, len(normal_ssml.encode()), 200),
            ("over", normal_ssml, len(normal_ssml.encode()) - 1, 413),
            ("declaration", declaration, len(declaration.encode()), 400),
            ("size-before-declaration", declaration, len(normal_ssml.encode()), 413)):
            identifier = "integrated-tts-" + label
            constraint = copy.deepcopy(apis["azure-speech-v1"]["authorization"]["operationConstraints"]["tts"])
            constraint["maxRequestBytes"] = maximum
            item = case(identifier, config=apis["azure-speech-v1"]["authorization"], api="azure-speech-v1",
                        operation="tts", path="/synthesize", controlConstraints={"tts": constraint},
                        rawBody=raw, headers={"Content-Type": "application/ssml+xml"})
            cases.append(item)
            cls.speech_guard_cases[identifier] = (item, status)
        literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
        cls.decision_body = {"model": "decision-1", "state": {literal: {"text": "Synthetic code example"}},
                             "questions": {literal: {"type": "choice", "instructions": "Review the inert source text.",
                                                     "criteria": {literal: "A synthetic source label", "other": "Other"}}}}
        decision_config = apis["microsoft-decision-v1"]["authorization"]
        decision = {"inputContracts": INPUT_TABLES["microsoft-decision-v1"], "config": decision_config, "api": "microsoft-decision-v1", "operation": "systemone",
                    "path": "/systemone", "headers": {"Content-Type": "application/json"}, "body": cls.decision_body}
        cases.append(case("decision-native-request", **decision))
        cases.append(case("decision-json-charset", **{**decision, "headers": {"Content-Type": "Application/JSON; charset=UTF-8"}}))
        cases.append(case("decision-revision-request", **{**decision, "api": "microsoft-decision-v1;rev=2"}))
        for suffix, patch in (
            ("query", {"query": {"arbitrary": "synthetic"}}),
            ("state-query", {"query": {"state.message": literal}}),
            ("question-key-query", {"query": {"questions." + literal: "synthetic"}}),
            ("duplicate-query", {"query": {"state.message": ["one", "two"]}}),
            ("api-version", {"query": {"api-version": "v1"}}),
            ("query-before-body", {"query": {"state.message": literal}, "rawBody": "{"}),
            ("method", {"method": "GET"}),
            ("missing-type", {"headers": {}}),
            ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
            ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}}),
            ("combined-type", {"headers": {"Content-Type": "application/json, application/json"}}),
            ("wrong-route", {"path": "/systemone/neighbor"})):
            identifier = "decision-different-method" if suffix == "method" else "decision-reject-" + suffix
            cases.append(case(identifier, **{**decision, **patch}))
        cases.append(case("decision-revision-query", **{**decision, "api": "microsoft-decision-v1;rev=2",
                          "query": {"questions.synthetic.instructions": literal}}))
        cases.append(case("decision-wrong-model", **{**decision, "body": {**cls.decision_body, "model": "unapproved-synthetic-model"}}))
        cases.append(case("decision-other-api", **{**decision, "api": "another-api", "query": {"state.message": literal},
                          "headers": {"Content-Type": "text/plain"}}))
        other_config = {**decision_config, "operationConstraints": {
            "synthetic-other": decision_config["operationConstraints"]["systemone"]}}
        cases.append(case("decision-other-operation", **{**decision, "operation": "synthetic-other", "path": "/synthetic-other",
                          "config": other_config, "query": {"state.message": literal}, "headers": {"Content-Type": "text/plain"}}))
        # 入力部品だけを実行し、名前変更・未指定・破損設定と本文読取りの境界を確認します。
        cls.input_only_cases = {}
        cls.input_error_cases = []
        selected = {"synthetic-invoke": {}}
        generic = {"api": "synthetic-vendor-api", "operation": "synthetic-invoke", "path": "/messages",
                   "method": "PUT", "headers": {"Content-Type": "Application/Vnd.Example+Json; charset=utf-8"},
                   "query": {"workspace": "synthetic"}, "inputContracts": selected, "inputOnly": True, "rawBody": "{"}
        for label, patch, expected in (
            ("generic", {}, True), ("renamed-api", {"api": "synthetic-other-api"}, True),
            ("revision", {"api": "synthetic-vendor-api;rev=2"}, True),
            ("renamed-path", {"path": "/another/provider/path"}, True),
            ("renamed-operation", {"operation": "new-invoke", "inputContracts": {"new-invoke": selected["synthetic-invoke"]}}, True),
            ("unconfigured-operation", {"operation": "unconfigured", "headers": {}, "query": {"unrestricted": "synthetic"}}, True),
            ("explicit-empty-table", {"inputContracts": {}, "headers": {}, "query": {"unrestricted": "synthetic"}}, True),
            ("unknown-query", {"query": {"unexpected": "synthetic"}}, True),
            ("duplicate-query", {"query": {"workspace": ["synthetic", "synthetic"]}}, True),
            ("unavailable-query", {"queryUnavailable": True}, True),
            ("different-method", {"method": "GET"}, True),
            ("missing-type", {"headers": {}}, False),
            ("empty-type", {"headers": {"Content-Type": ""}}, False),
            ("blank-type", {"headers": {"Content-Type": " \t"}}, False),
            ("empty-base-type", {"headers": {"Content-Type": "; charset=utf-8"}}, False),
            ("blank-base-type", {"headers": {"Content-Type": " \t; boundary=synthetic"}}, False),
            ("null-type", {"headers": {"Content-Type": None}}, False),
            ("zero-values", {"headers": {"Content-Type": []}}, False),
            ("duplicate-type", {"headers": {"Content-Type": ["application/vnd.example+json", "application/vnd.example+json"]}}, False),
            # この部品はMIMEの許可一覧や宣言構文を検査せず、元の文字列を維持します。
            ("combined-type", {"headers": {"Content-Type": "application/vnd.example+json, application/vnd.example+json"}}, True),
            ("json-type", {"headers": {"Content-Type": "application/json"}}, True),
            ("unknown-type", {"headers": {"Content-Type": "application/vnd.future+json; charset=utf-8"}}, True)):
            identifier = "input-only-" + label
            cls.input_only_cases[identifier] = expected
            cases.append(case(identifier, **{**generic, **patch}))
        for label, controls in (("missing-table", "{}"), ("malformed-json", "{"),
                                ("array-table", '{"inputContracts":[]}'),
                                ("null-table", '{"inputContracts":null}'),
                                ("null-operation", '{"inputContracts":{"synthetic-invoke":null}}'),
                                ("scalar-operation", '{"inputContracts":{"synthetic-invoke":"invalid"}}'),
                                ("array-operation", '{"inputContracts":{"synthetic-invoke":[]}}')):
            identifier = "input-error-" + label
            cls.input_error_cases.append(identifier)
            cases.append(case(identifier, **{**generic, "controlsRaw": controls}))
        cls.input_case_source = {item["id"]: item for item in cases if item.get("inputOnly")}
        cls.standard_method_cases = {item["id"]: item for item in cases
                                     if item["id"].endswith("-different-method")
                                     and (item.get("api"), item.get("operation")) in STANDARD_OPERATIONS}
        cls.standard_media_cases = {item["id"]: item for item in cases
                                    if item["id"].endswith(("-reject-wrong-type", "-reject-combined-type"))
                                    and (item.get("api"), item.get("operation")) in STANDARD_OPERATIONS}
        cls.standard_query_cases = {item["id"]: item for item in cases
                                    if item.get("query") and item["id"].endswith(("-query", "-api-version", "-version", "-versions", "-value", "-name", "-with-input", "-before-body"))
                                    and item.get("headers", {}).get("Content-Type") not in (None, "text/plain")
                                    and (str(item.get("api", "")).split(";rev=", 1)[0], item.get("operation", "")) in STANDARD_OPERATIONS
                                    and item.get("operation", "") in item["inputContracts"]}

        for name, query, models in (("ws-default", {}, ["standard"]), ("ws-mini", {"model": "mini"}, ["standard", "mini"]),
                                    ("ws-unlisted", {"model": "mini"}, ["standard"]),
                                    ("ws-unknown", {"model": "unknown"}, ["standard", "mini"]),
                                    ("ws-duplicate", {"model": ["standard", "mini"]}, ["standard", "mini"]),
                                    ("ws-unknown-query", {"model": "mini", "backend": "other"}, ["mini"])):
            item = case(name, mode="ws", query=query, websocket={"allowedModels": models, "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}})
            cases.append(item)
        for name, catalog in (("ws-null-catalog", None), ("ws-object-catalog", {}), ("ws-empty-catalog", [])):
            cases.append(case(name, mode="ws", query={"model": "future-synthetic-model"},
                              websocket={"allowedModels": catalog, "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}}))
        cases.append(case("ws-missing-catalog", mode="ws", query={"model": "future-synthetic-model"},
                          websocket={"defaultModel": "standard", "queryParameters": {"api-version": "fixed"}}))
        cases.append(case("ws-default-outside-catalog", mode="ws",
                          websocket={"allowedModels": ["mini"], "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}}))
        for name, query in (("ws-fixed-default", {}), ("ws-fixed-model", {"model": "fixed-synthetic-model"}),
                            ("ws-fixed-other-model", {"model": "other-synthetic-model"}),
                            ("ws-fixed-duplicate", {"model": ["fixed-synthetic-model", "fixed-synthetic-model"]})):
            cases.append(case(name, mode="ws", query=query, websocket={"queryParameters": {"model": "fixed-synthetic-model"}}))
        selection_source = (CORE / "apps/gateway/apim/policies/fragments/request/core-api-operation-settings/core-api-operation-settings.cs").read_text(encoding="utf-8")
        source = SOURCE.replace("__PROFILE__", selection_source)
        policy = ET.ElementTree(ET.fromstring(render_audit_policy((CORE / "apps/gateway/apim/policies/api-templates/gateway.xml").read_text(encoding="utf-8"))))
        rejection = next(node for node in policy.findall('./inbound/choose/when')
                         if 'gatewayOperationSelection' in node.get('condition', ''))
        for marker, value in (
            ('__PROFILE_AUDIT_CODE__', rejection.find("set-variable[@name='gatewayAuditCode']").get('value')),
            ('__PROFILE_AUDIT_STATUS__', rejection.find("set-variable[@name='gatewayAuditStatus']").get('value')),
            ('__PROFILE_AUDIT_PHASE__', rejection.find("set-variable[@name='gatewayAuditPhase']").get('value')),
            ('__PROFILE_RESPONSE_REASON__', rejection.find('return-response/set-status').get('reason'))):
            if not value.startswith('@(') or not value.endswith(')'):
                raise AssertionError('Expected the actual profile rejection expression')
            source = source.replace(marker, value[2:-1])
        normalize = policy.find("./inbound/choose[@id='core-model-alias']/when/set-body").text.strip()
        source = source.replace("__NORMALIZE_MODEL__", normalize[2:-1])
        chat = policy.find("./inbound/choose[@id='core-chat-store-disabled']/when")
        chat_failure = chat.find('choose/when')
        chat_body = ET.fromstring(render_chat_store_fragment()).find('set-variable').get('value').strip()
        source = source.replace('__CHAT_BODY__', chat_body[2:-1])
        source = source.replace('__CHAT_REQUIRED__', policy.find("./inbound/set-variable[@name='gatewayChatStoreDisabled']").get('value').strip()[2:-1])
        for marker, value in (
            ('__CHAT_INVALID__', chat_failure.get('condition')),
            ('__CHAT_APPLY__', chat.find('set-body').text.strip()),
            ('__CHAT_AUDIT_PHASE__', chat_failure.find("set-variable[@name='gatewayAuditPhase']").get('value')),
            ('__CHAT_AUDIT_CODE__', chat_failure.find("set-variable[@name='gatewayAuditCode']").get('value')),
            ('__CHAT_AUDIT_STATUS__', chat_failure.find("set-variable[@name='gatewayAuditStatus']").get('value')),
            ('__CHAT_RESPONSE_STATUS__', chat_failure.find('return-response/set-status').get('code')),
            ('__CHAT_RESPONSE_REASON__', chat_failure.find('return-response/set-status').get('reason')),
            ('__CHAT_REQUEST_ID__', chat_failure.find("return-response/set-header[@name='apim-request-id']/value").text),
            ('__CHAT_CACHE_CONTROL__', chat_failure.find("return-response/set-header[@name='Cache-Control']/value").text),
        ):
            expression = value[2:-1] if value.startswith('@(') and value.endswith(')') else value if value.isdecimal() else json.dumps(value)
            source = source.replace(marker, expression)
        stateless = next(node for node in policy.findall('./inbound/choose/when')
                         if 'gatewayStatelessResponses' in node.get('condition', ''))
        stateless_body = stateless.find('set-body').text.strip()
        source = source.replace('__STATELESS_BODY__', stateless_body[2:-1])
        stateless_required = policy.find("./inbound/set-variable[@name='gatewayStatelessResponses']").get('value').strip()
        source = source.replace('__STATELESS_REQUIRED__', stateless_required[2:-1])
        cls.hosted_normalization = policy.find("./inbound/choose[@id='core-hosted-response-metadata']")
        hosted_when = cls.hosted_normalization.find('when')
        source = source.replace('__HOSTED_REQUIRED__', single_expression(hosted_when.get('condition')))
        hosted_body = hosted_when.find('set-body').text.strip()
        if not hosted_body.startswith('@{') or not hosted_body.endswith('}'):
            raise AssertionError('Expected the actual Hosted metadata/body expression')
        source = source.replace('__HOSTED_BODY__', hosted_body[2:-1])
        validation = ET.fromstring(render_input_validation_fragment()).find("set-variable").get("value").strip()
        source = source.replace("__INFERENCE_REQUEST__", validation[2:-1])
        constraint = policy.find("./inbound/set-variable[@name='gatewayOperationConstraint']").get("value").strip()
        if not constraint.startswith("@{") or not constraint.endswith("}"):
            raise AssertionError("Expected the actual operation constraint handoff expression")
        source = source.replace("__OPERATION_CONSTRAINT__", constraint[2:-1])
        speech = next(node for node in policy.findall('./inbound/choose/when')
                      if node.find("include-fragment[@fragment-id='core-speech-ssml-guard']") is not None)
        speech_condition = speech.get('condition').strip()
        if speech_condition.startswith('@{') and speech_condition.endswith('}'):
            speech_body = speech_condition[2:-1]
        elif speech_condition.startswith('@(') and speech_condition.endswith(')'):
            speech_body = 'return ' + speech_condition[2:-1] + ';'
        else:
            raise AssertionError('Expected the actual Speech guard selection expression')
        source = source.replace('__SPEECH_REQUIRED__', speech_body)
        source = source.replace('__SPEECH_GUARD__', (CORE / 'apps/gateway/apim/policies/fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs').read_text(encoding='utf-8'))
        source = source.replace("__AUTH__", actual_expression("gateway.xml", "gatewayDecision")).replace("__LIMITS__", LIMIT_EXPRESSION).replace("__TARGET__", actual_expression("gateway.xml", "gatewayTargetDecision"))
        cls.target_rejection = next(node for node in policy.findall('./inbound/choose/when')
                                    if 'gatewayTargetDecision' in node.get('condition', ''))
        assignments = '\n'.join(f'context.Variables[{json.dumps(node.get("name"))}] = {single_expression(node.get("value"))};'
                                for node in cls.target_rejection.findall('./set-variable'))
        for marker, value in {
            '__TARGET_AUDIT_ASSIGNMENTS__': assignments,
            '__TARGET_REJECTION_CONDITION__': single_expression(cls.target_rejection.get('condition')),
            '__TARGET_RESPONSE_STATUS__': single_expression(cls.target_rejection.find('./return-response/set-status').get('code')),
            '__TARGET_RESPONSE_REASON__': single_expression(cls.target_rejection.find('./return-response/set-status').get('reason')),
            '__TARGET_RESPONSE_REQUEST_ID__': single_expression(cls.target_rejection.find("./return-response/set-header[@name='apim-request-id']/value").text),
        }.items():
            source = source.replace(marker, value)
        model = actual_expression("protocol.xml", "gatewayProtocolModel").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])').replace('var encoded = "__WEBSOCKET_SETTINGS_BASE64__";', 'var encoded = "fixture";')
        query = actual_expression("protocol.xml", "gatewayProtocolQueryAllowed").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])["queryParameters"] as JObject').replace('var encoded = "__WEBSOCKET_QUERY_BASE64__";', 'var encoded = "fixture";')
        source = source.replace("__MODEL__", model).replace("__PROTOCOL_AUTH__", actual_expression("protocol.xml", "gatewayDecision")).replace("__QUERY__", query)
        with tempfile.TemporaryDirectory(prefix="core-profile-policy-") as temporary:
            path = Path(temporary)
            (path / "policy.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[IntegratedProfileFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if run.returncode:
                raise AssertionError("Actual integrated policy execution failed:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_operation_model_alias_resolves_while_ignoring_every_legacy_header_value(self):
        for name in ("alias-default", "alias-explicit", "alias-pdf", "alias-unknown-header", "alias-multiple-header"):
            result = self.results[name]
            self.assertEqual(result["profile"]["requestedModel"], "codex-auto-review")
            self.assertEqual(result["profile"]["effectiveModel"], "gpt-5.6-luna")
            for stage in ("profile", "decision", "limits"):
                self.assertTrue(result[stage]["allowed"], (name, stage))
            self.assertEqual(result["profile"]["settingId"], "response")
            self.assertTrue(result["targetAllowed"])
            self.assertEqual(json.loads(result["bodyBeforeNormalization"]), self.alias_body)
            self.assertEqual(json.loads(result["bodyAfter"]), {**self.alias_body, "model": "gpt-5.6-luna"})
        direct = self.results["alias-direct"]
        self.assertNotIn("status", direct["profile"])
        self.assertNotIn("code", direct["profile"])
        self.assertEqual(direct["bodyBeforeNormalization"], direct["bodyAfter"])
        for name in ("alias-unknown", "alias-chat"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)
            self.assertTrue(self.results[name]["limits"]["allowed"], name)
            self.assertEqual(self.results[name]["profile"]["settingId"], "", name)
            self.assertTrue(self.results[name]["targetAllowed"], name)
            self.assertEqual(json.loads(self.results[name]["bodyAfter"])["model"], "unknown-alias" if name == "alias-unknown" else "codex-auto-review")

    def test_previous_response_id_is_preserved_and_conversation_is_cleared_in_the_body_rewrite(self):
        for identifier, (original, expected) in self.stateless_reference_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                if 'previous_response_id' in original:
                    self.assertEqual(actual['previous_response_id'], original['previous_response_id'])
                else:
                    self.assertNotIn('previous_response_id', actual)
                if 'conversation' in original:
                    self.assertIsNone(actual['conversation'])
                else:
                    self.assertNotIn('conversation', actual)

    def test_conversation_id_and_previous_response_id_are_preserved_while_conversation_is_cleared(self):
        for identifier, (original, expected) in self.conversation_id_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                self.assertEqual(json.loads(result['bodyAfter']), expected)

    def test_prompt_and_all_its_variables_are_preserved_without_changing_other_fields(self):
        for identifier, (original, expected) in self.prompt_normalization_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                if 'prompt' in original:
                    self.assertEqual(actual['prompt'], original['prompt'])
                    if isinstance(original['prompt'], dict) and 'variables' in original['prompt']:
                        self.assertEqual(actual['prompt']['variables'], original['prompt']['variables'])
                else:
                    self.assertNotIn('prompt', actual)

    def test_background_is_false_when_present_and_stream_is_preserved(self):
        for identifier, (original, expected) in self.background_normalization_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                self.assertIs(actual['stream'], original['stream'])
                if 'background' in original:
                    self.assertIs(actual['background'], False)
                else:
                    self.assertNotIn('background', actual)

    def test_responses_input_presence_and_type_are_delegated_and_values_are_preserved(self):
        for identifier, (original, expected) in self.responses_input_preservation_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                if 'input' in original:
                    self.assertEqual(actual['input'], original['input'])
                else:
                    self.assertNotIn('input', actual)

    def test_direct_reference_items_and_complete_items_are_preserved(self):
        for identifier, (original, expected) in self.item_reference_normalization_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                self.assertEqual(actual['input'], original['input'])

    def test_untyped_id_only_items_and_content_items_keep_id_type_and_content(self):
        for identifier, (original, expected) in self.untyped_item_reference_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 1)
                self.assertEqual(result['statelessBodyReads'], 1)
                self.assertEqual(result['statelessConsumingReads'], 1)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual, expected)
                self.assertEqual(actual['input'], original['input'])

    def test_chat_store_is_false_after_alias_with_one_consuming_read_and_other_fields_preserved(self):
        for identifier,(original,expected) in self.chat_store_cases.items():
            with self.subTest(case=identifier):
                result=self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile','decision','limits'):
                    self.assertTrue(result[stage]['allowed'],stage)
                self.assertTrue(result['targetAllowed'])
                self.assertTrue(result['chatStoreDisabled'])
                self.assertTrue(result['chatBodyApplied'])
                self.assertEqual(result['chatBodyReads'],1)
                self.assertEqual(result['chatConsumingReads'],1)
                self.assertEqual(result['chatApplyBodyReads'],0)
                self.assertNotIn('chatRejection',result)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']),original)
                self.assertEqual(json.loads(result['bodyAfter']),expected)
                self.assertIs(json.loads(result['bodyAfter'])['store'],False)

    def test_chat_invalid_body_is_audited_as_400_and_earlier_rejections_skip_normalization(self):
        for identifier in self.chat_invalid_cases:
            with self.subTest(case=identifier):
                result=self.results[identifier]
                for stage in ('profile','decision','limits'):
                    self.assertTrue(result[stage]['allowed'],stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['chatBodyReads'],1)
                self.assertEqual(result['chatConsumingReads'],1)
                self.assertFalse(result['chatBodyApplied'])
                self.assertNotIn('chatApplyBodyReads',result)
                self.assertEqual(result['chatRejection'],{
                    'phase':'rejected','code':'INVALID_REQUEST_BODY','status':400,
                    'reason':'Invalid request body','requestId':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                    'cacheControl':'no-store'})
        self.assertFalse(self.results['chat-store-auth-rejected']['decision']['allowed'])
        self.assertEqual(self.results['chat-store-wire-overflow']['limits']['status'],413)
        for identifier in ('chat-store-auth-rejected','chat-store-wire-overflow'):
            self.assertNotIn('chatBodyReads',self.results[identifier])
            self.assertNotIn('chatRejection',self.results[identifier])
        for identifier,original in self.chat_excluded_cases.items():
            with self.subTest(case=identifier):
                result=self.results[identifier]
                self.assertFalse(result['chatStoreDisabled'])
                self.assertNotIn('chatBodyReads',result)
                self.assertEqual(json.loads(result['bodyAfter']),original)

    def test_chat_body_processing_follows_limits_and_quota_and_precedes_backend_and_acceptance(self):
        source=(CORE/'apps/gateway/apim/policies/api-templates/gateway.xml').read_text(encoding='utf-8')
        inbound=ET.fromstring(source).find('inbound')
        nodes=list(inbound)
        chat=inbound.find("choose[@id='core-chat-store-disabled']")
        for earlier in (inbound.find("include-fragment[@fragment-id='core-model-limits']"),
                        inbound.find("choose[@id='core-model-alias']"),
                        inbound.find("include-fragment[@fragment-id='core-rate-limit']")):
            self.assertLess(nodes.index(earlier),nodes.index(chat))
        self.assertLess(source.index('__TOKEN_LIMIT_XML__'),source.index('<choose id="core-chat-store-disabled">'))
        self.assertLess(source.index('<choose id="core-chat-store-disabled">'),source.index('fragment-id="core-credential-connection"'))
        self.assertLess(nodes.index(chat),nodes.index(inbound.find("set-variable[@name='gatewayStatelessResponses']")))
        self.assertLess(nodes.index(chat),nodes.index(inbound.find("set-variable[@name='gatewayAuditPhase'][@value='accepted']")))
        failure=chat.find('when/choose/when')
        self.assertEqual(len(failure.findall("include-fragment[@fragment-id='core-gateway-telemetry']")),1)
        self.assertIsNone(failure.find('return-response/set-body'))
        self.assertEqual(failure.find('return-response/set-status').attrib,{'code':'400','reason':'Invalid request body'})
        self.assertEqual(chat.find('when/set-body').text.strip(),'@((string)context.Variables["gatewayChatRequestBody"])')

    def test_file_references_and_include_are_preserved_with_existing_tool_rejections_and_scope(self):
        for identifier, (original, expected, allowed, target_reads) in self.responses_file_id_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertEqual(result['targetAllowed'], allowed)
                self.assertEqual(result['targetBodyReads'], target_reads)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                if allowed and target_reads:
                    self.assertEqual(result['statelessBodyReads'], 1)
                    self.assertEqual(result['statelessConsumingReads'], 1)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)
                else:
                    self.assertNotIn('statelessBodyReads', result)
                    if identifier == 'other-kind-file-id-scope-bodyModel':
                        self.assertTrue(result['chatBodyApplied'])
                        self.assertEqual(json.loads(result['bodyAfter']), expected)
                        self.assertEqual(result['chatBodyReads'], 1)
                        self.assertEqual(result['chatConsumingReads'], 1)
                        self.assertEqual(result['chatApplyBodyReads'], 0)
                    else:
                        self.assertEqual(json.loads(result['bodyAfter']), original)

    def test_include_is_preserved_through_alias_and_store_rewrites_and_other_rejections_skip_edits(self):
        for identifier, (original, expected, allowed, target_reads) in self.responses_include_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertEqual(result['targetAllowed'], allowed)
                self.assertEqual(result['targetBodyReads'], target_reads)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                if allowed and target_reads:
                    self.assertEqual(result['statelessBodyReads'], 1)
                    self.assertEqual(result['statelessConsumingReads'], 1)
                    actual = json.loads(result['bodyAfter'])
                    self.assertEqual(actual, expected)
                    if 'include' in original:
                        self.assertEqual(actual['include'], original['include'])
                    else:
                        self.assertNotIn('include', actual)
                elif not allowed:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertNotIn('chatBodyReads', result)
                    self.assertEqual(result['bodyAfter'], result['bodyBeforeNormalization'])
                else:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)

    def test_code_interpreter_rejection_preserves_wire_body_and_skips_alias_and_storage_rewrites(self):
        for identifier, (original, expected, allowed, target_reads) in self.responses_code_interpreter_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertEqual(result['targetAllowed'], allowed)
                self.assertEqual(result['targetBodyReads'], target_reads)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                if allowed and target_reads:
                    self.assertEqual(result['statelessBodyReads'], 1)
                    self.assertEqual(result['statelessConsumingReads'], 1)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)
                elif not allowed:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertNotIn('chatBodyReads', result)
                    self.assertEqual(json.loads(result['bodyAfter']), original)
                    self.assertEqual(result['bodyAfter'], result['bodyBeforeNormalization'])
                else:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)
        inbound = ET.parse(CORE / 'apps/gateway/apim/policies/api-templates/gateway.xml').find('inbound')
        nodes = list(inbound)
        target = inbound.find("include-fragment[@fragment-id='core-api-target-validation']")
        self.assertLess(nodes.index(inbound.find("include-fragment[@fragment-id='core-model-limits']")), nodes.index(target))
        self.assertLess(nodes.index(target), nodes.index(inbound.find("choose[@id='core-model-alias']")))
        self.assertLess(nodes.index(target), nodes.index(inbound.find("include-fragment[@fragment-id='core-rate-limit']")))
        self.assertLess(nodes.index(target), nodes.index(inbound.find("set-variable[@name='gatewayAuditPhase'][@value='accepted']")))

    def test_allowed_tool_choices_survive_alias_and_storage_rewrites_while_rejections_skip_edits(self):
        for identifier, (original, expected, allowed, target_reads) in self.responses_tool_choice_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertEqual(result['targetAllowed'], allowed)
                self.assertEqual(result['targetBodyReads'], target_reads)
                self.assertEqual(json.loads(result['bodyBeforeNormalization']), original)
                if allowed and target_reads:
                    self.assertEqual(result['statelessBodyReads'], 1)
                    self.assertEqual(result['statelessConsumingReads'], 1)
                    actual = json.loads(result['bodyAfter'])
                    self.assertEqual(actual, expected)
                    if 'tool_choice' in original:
                        self.assertEqual(actual['tool_choice'], original['tool_choice'])
                    else:
                        self.assertNotIn('tool_choice', actual)
                elif not allowed:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertNotIn('chatBodyReads', result)
                    self.assertEqual(result['bodyAfter'], result['bodyBeforeNormalization'])
                else:
                    self.assertNotIn('statelessBodyReads', result)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)

    def test_hosted_and_a2a_json_readers_keep_input_errors_distinct_from_readable_rejections(self):
        for identifier, (original, status, reads, kind) in self.hosted_target_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                code = ('TARGET_ALLOWED' if status == 200 else 'INVALID_REQUEST_BODY' if status == 400
                        else 'OPERATION_SETTINGS_INVALID' if status == 500 else 'AUTHORIZATION_DENIED')
                self.assertEqual(result['targetDecision'], {'allowed': status == 200, 'status': status, 'code': code})
                self.assertEqual(result['targetAllowed'], status == 200)
                self.assertEqual(result['targetBodyReads'], reads)
                self.assertEqual(result['targetMethodReads'], 0)
                self.assertEqual(result['bodyBeforeNormalization'], original)
                if status == 200 and kind == 'hostedAgentPost':
                    expected = {**json.loads(original), 'store': False}
                    if 'conversation' in expected:
                        expected['conversation'] = None
                    if 'background' in expected:
                        expected['background'] = False
                    self.assertEqual(json.loads(result['bodyAfter']), expected)
                    self.assertTrue(result['hostedNormalizationRequired'])
                    self.assertEqual(result['hostedBodyReads'], 1)
                    self.assertEqual(result['hostedConsumingReads'], 0)
                else:
                    self.assertEqual(result['bodyAfter'], original)
                    self.assertNotIn('hostedBodyReads', result)
                self.assertEqual(result['rateLimitReached'], status == 200)
                self.assertNotIn('chatBodyReads', result)
                self.assertNotIn('statelessBodyReads', result)
                if status == 200:
                    self.assertNotIn('targetRejection', result)
                else:
                    self.assertEqual(result['targetRejection'], {'phase': 'policy-error' if status == 500 else 'rejected',
                        'code': code, 'auditStatus': status, 'status': status,
                        'reason': 'Invalid request body' if status == 400 else 'Gateway processing failed' if status == 500
                                  else 'API target is not authorized',
                        'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
        response = self.target_rejection.find('./return-response')
        self.assertIsNone(response.find('set-body'))
        self.assertEqual(response.find("set-header[@name='Cache-Control']/value").text, 'no-store')

    def test_hosted_store_and_parent_metadata_share_one_body_read_with_operation_scope(self):
        for identifier, (raw, original, expected, status, selected, kind, agent) in self.hosted_normalization_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                self.assertTrue(result['profile']['allowed'])
                self.assertTrue(result['decision']['allowed'])
                if status == 413:
                    self.assertFalse(result['limits']['allowed'])
                    self.assertEqual(result['limits']['status'], 413)
                else:
                    self.assertTrue(result['limits']['allowed'])
                    self.assertEqual(result['targetDecision']['status'], status)
                    self.assertEqual(result['targetAllowed'], status == 200)
                    self.assertEqual(result['bodyBeforeNormalization'], raw)
                self.assertEqual(result['rateLimitReached'], status == 200)
                if status != 200:
                    self.assertEqual(result['bodyAfter'], raw)
                    if status == 403:
                        self.assertEqual(result['targetDecision'], {'allowed': False, 'status': 403, 'code': 'AUTHORIZATION_DENIED'})
                        self.assertEqual(result['targetBodyReads'], 1)
                        self.assertEqual(result['targetRejection'], {'phase': 'rejected', 'code': 'AUTHORIZATION_DENIED',
                            'auditStatus': 403, 'status': 403, 'reason': 'API target is not authorized',
                            'requestIdHeader': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
                    self.assertNotIn('hostedNormalizationRequired', result)
                    self.assertNotIn('hostedBodyReads', result)
                    self.assertNotIn('chatBodyReads', result)
                    self.assertNotIn('statelessBodyReads', result)
                    continue
                self.assertEqual(result['hostedNormalizationRequired'], selected)
                self.assertEqual(result['hostedSelectionBodyReads'], 0)
                if selected:
                    self.assertEqual(result['hostedBodyReads'], 1)
                    self.assertEqual(result['hostedConsumingReads'], 0)
                    self.assertEqual(json.loads(result['bodyAfter']), expected)
                else:
                    self.assertNotIn('hostedBodyReads', result)
                    self.assertEqual(result['bodyAfter'], raw)
                actual = json.loads(result['bodyAfter'])
                self.assertEqual(actual.get('input'), original.get('input'))
                self.assertEqual('input' in actual, 'input' in original)
                self.assertEqual(actual.get('stream'), original.get('stream'))
                self.assertEqual('stream' in actual, 'stream' in original)
                if kind == 'hostedAgentPost':
                    self.assertIs(actual['store'], False)
                    for field in ('conversation', 'background'):
                        self.assertEqual(field in actual, field in original, field)
                    if 'conversation' in original:
                        self.assertIsNone(actual['conversation'])
                    if 'background' in original:
                        self.assertIs(actual['background'], False)
                    self.assertEqual(result['targetBodyReads'], 1)
                    self.assertEqual(result['reads'] - result['limitBodyReads'], 2)
                else:
                    self.assertEqual(actual.get('store'), original.get('store'))
                    self.assertEqual('store' in actual, 'store' in original)
                    for field in ('conversation', 'background'):
                        self.assertEqual(actual.get(field), original.get(field), field)
                        self.assertEqual(field in actual, field in original, field)
                    self.assertEqual(result['targetBodyReads'], 0)
                    self.assertEqual(result['reads'] - result['limitBodyReads'], int(selected))
                if not agent:
                    self.assertEqual(actual.get('metadata'), original.get('metadata'))
                    self.assertEqual('metadata' in actual, 'metadata' in original)

    def test_hosted_rewrite_follows_target_rejection_and_precedes_rate_limit_counting(self):
        inbound = ET.parse(CORE / 'apps/gateway/apim/policies/api-templates/gateway.xml').find('inbound')
        nodes = list(inbound)
        target = inbound.find("include-fragment[@fragment-id='core-api-target-validation']")
        failure = next(node for node in inbound.findall('choose')
                       if any(when.get('condition') == self.target_rejection.get('condition') for when in node.findall('when')))
        hosted = inbound.find("choose[@id='core-hosted-response-metadata']")
        rate = inbound.find("include-fragment[@fragment-id='core-rate-limit']")
        self.assertIsNotNone(failure.find('when/return-response'))
        self.assertLess(nodes.index(target), nodes.index(failure))
        self.assertLess(nodes.index(failure), nodes.index(hosted))
        self.assertLess(nodes.index(hosted), nodes.index(rate))
        self.assertEqual(len(hosted.findall('when/set-body')), 1)

    def test_dedicated_kinds_preserve_body_without_repeating_route_matching(self):
        for identifier, original in self.dedicated_target_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertEqual(result['targetDecision'], {'allowed': True, 'status': 200, 'code': 'TARGET_ALLOWED'})
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['targetBodyReads'], 0)
                self.assertEqual(result['targetMethodReads'], 0)
                self.assertEqual(result['bodyBeforeNormalization'], original)
                self.assertEqual(result['bodyAfter'], original)
                for skipped in ('targetRejection', 'chatBodyReads', 'statelessBodyReads'):
                    self.assertNotIn(skipped, result)

    def test_alias_cannot_bypass_auth_original_wire_bounds_or_independent_target_checks(self):
        self.assertFalse(self.results["alias-no-key"]["decision"]["allowed"])
        self.assertFalse(self.results["alias-reject-forbidden-tools"]["targetAllowed"])
        self.assertEqual(self.results["alias-wire-overflow"]["profile"]["status"], 413)
        self.assertEqual(self.results["alias-profile-overflow"]["limits"]["status"], 413)
        for name in ("alias-no-key", "alias-reject-forbidden-tools", "alias-profile-overflow"):
            self.assertEqual(json.loads(self.results[name]["bodyAfter"])["model"], "codex-auto-review", name)

    def test_output_token_fields_survive_model_settings_and_model_only_alias_normalization(self):
        for name, original, effective in (
                ("compatible-alternate-token-field", {"model": "grok-4.7", "max_completion_tokens": 64}, "grok-4.7"),
                ("alias-mixed-token-fields", {**self.alias_body, "max_tokens": 1}, "gpt-5.6-luna"),
                ("alias-large-output-limit", {**self.alias_body, "max_output_tokens": 9223372036854775807}, "gpt-5.6-luna")):
            with self.subTest(case=name):
                result = self.results[name]
                self.assertTrue(result["requestAllowed"])
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], stage)
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(json.loads(result["bodyBeforeNormalization"]), original)
                self.assertEqual(json.loads(result["bodyAfter"]), {**original, "model": effective})
                if original["model"] == effective:
                    self.assertEqual(result["bodyBeforeNormalization"], result["bodyAfter"])

    def test_alias_normalization_order_preserves_original_byte_and_token_checks(self):
        inbound = ET.parse(CORE / "apps/gateway/apim/policies/api-templates/gateway.xml").getroot().find("inbound")
        nodes = list(inbound)
        normalize = inbound.find("choose[@id='core-model-alias']")
        self.assertLess(nodes.index(inbound.find("include-fragment[@fragment-id='core-model-limits']")), nodes.index(normalize))
        self.assertLess(nodes.index(inbound.find("include-fragment[@fragment-id='core-api-target-validation']")), nodes.index(normalize))
        self.assertLess(nodes.index(normalize), nodes.index(inbound.find("include-fragment[@fragment-id='core-audit-body-model']")))

    def test_profile_selection_uses_prepared_settings_and_preserves_the_authenticated_caller(self):
        self.assertTrue(self.results["default"]["decision"]["allowed"])
        self.assertTrue(self.results["explicit"]["decision"]["allowed"])
        self.assertEqual(self.results["explicit"]["decision"]["profileId"], "chat")
        self.assertEqual(self.results["default"]["decision"]["callerId"], self.results["explicit"]["decision"]["callerId"])
        prepared = self.results["prepared-controls"]
        self.assertEqual(prepared["profile"]["settingId"], "vision")
        self.assertEqual(prepared["decision"]["callerId"], self.results["default"]["decision"]["callerId"])
        self.assertTrue(prepared["limits"]["allowed"])
        self.assertTrue(prepared["targetAllowed"])
        self.assertEqual(prepared["bodyAfter"], self.results["default"]["bodyAfter"])

    def test_legacy_profile_headers_do_not_select_or_reject_requests(self):
        baseline = self.results["default"]
        for name in ("explicit", "unknown", "duplicate", "empty-profile-header", "zero-profile-values"):
            with self.subTest(case=name):
                result = self.results[name]
                for stage in ("profile", "decision", "limits"):
                    self.assertEqual(result[stage], baseline[stage])
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["bodyAfter"], baseline["bodyAfter"])
        audio = self.results["wrong-model"]
        self.assertEqual(audio["profile"], self.results["model-only-audio"]["profile"])
        self.assertEqual(audio["profile"]["settingId"], "audio-chat")
        self.assertTrue(audio["limits"]["allowed"])
        self.assertTrue(audio["targetAllowed"])

    def test_missing_or_invalid_operation_settings_are_audited_as_server_errors_before_body_reads(self):
        for identifier in self.operation_settings_errors:
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertFalse(result['profile']['allowed'])
                self.assertEqual(result['profile']['status'], 500)
                self.assertEqual(result['profile']['code'], 'OPERATION_SETTINGS_INVALID')
                self.assertEqual(result['rejection'], {'status': 500, 'code': 'OPERATION_SETTINGS_INVALID',
                                                     'reason': 'API operation settings are invalid'})
                self.assertEqual(result['rejectionPhase'], 'policy-error')
                self.assertEqual(result['reads'], 0)
                self.assertEqual(result['bodyAfter'], '{')
                self.assertNotIn('decision', result)

    def test_operation_settings_errors_do_not_reclassify_existing_profile_body_or_input_failures(self):
        result = self.results['huge-malformed']
        self.assertEqual(result['profile']['status'], 413)
        self.assertEqual(result['profile']['code'], 'REQUEST_TOO_LARGE')
        self.assertEqual(result['rejection'], {'status': 413, 'code': 'REQUEST_TOO_LARGE',
                                             'reason': 'Request too large'})
        self.assertEqual(result['rejectionPhase'], 'rejected')
        for name in ('profile-empty-operation', 'profile-empty-operation-with-header'):
            direct = self.results[name]
            self.assertTrue(direct['profile']['allowed'])
            self.assertEqual(direct['profile']['constraint'], {})
            self.assertEqual(direct['reads'], 0)
            self.assertEqual(direct['bodyAfter'], '{')
        upstream = self.results['profile-malformed-json-through-input']
        self.assertIn('inputError', upstream)
        self.assertNotIn('profile', upstream)
        self.assertNotIn('rejection', upstream)
        self.assertEqual(upstream['reads'], 0)

    def test_models_without_settings_use_the_base_and_do_not_read_the_registered_model_list(self):
        for identifier, model in (('unsupported-model', 'unregistered-synthetic-model'),
                                  ('unsupported-model-with-profile', 'unregistered-synthetic-model'),
                                  ('profile-model-unresolved-empty', ''),
                                  ('profile-model-unresolved-blank', ' \t')):
            with self.subTest(case=identifier):
                result = self.results[identifier]
                selection = result['profile']
                self.assertTrue(selection['allowed'])
                self.assertEqual(selection['settingId'], '')
                self.assertEqual(selection['requestedModel'], model)
                self.assertEqual(selection['effectiveModel'], model)
                self.assertEqual(selection['constraint']['maxRequestBytes'], 2048)
                self.assertEqual(selection['constraint']['outputTokenParameter'], 'max_completion_tokens')
                self.assertNotIn('modelSettings', selection['constraint'])
                self.assertNotIn('rejection', result)
                if identifier.startswith('profile-model-'):
                    self.assertEqual(result['reads'], 1)
                self.assertEqual(json.loads(result['bodyAfter'])['model'], model)
        for label in ('missing', 'object'):
            result = self.results['operation-catalog-' + label]
            self.assertTrue(result['profile']['allowed'], label)
            self.assertEqual(result['profile']['settingId'], 'chat', label)
            self.assertEqual(result['profile']['effectiveModel'], 'shared', label)
            self.assertEqual(result['reads'], 1, label)

    def test_unreadable_json_and_invalid_model_fields_have_distinct_400_reasons(self):
        for identifier, (code, reason, original) in self.profile_body_errors.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertFalse(result['profile']['allowed'])
                self.assertEqual(result['rejection'], {'status': 400, 'code': code, 'reason': reason})
                self.assertEqual(result['rejectionPhase'], 'rejected')
                self.assertEqual(result['profile']['code'], code)
                self.assertEqual(result['profile']['status'], 400)
                self.assertEqual(result['reads'], 1)
                self.assertEqual(result['bodyAfter'], original)
                self.assertNotIn('decision', result)
                self.assertNotIn('targetAllowed', result)
        for identifier, original in self.profile_model_errors.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertFalse(result['profile']['allowed'])
                self.assertEqual(result['rejection'], {'status': 400, 'code': 'INVALID_REQUEST_MODEL',
                                                     'reason': 'Invalid request model'})
                self.assertEqual(result['rejectionPhase'], 'rejected')
                self.assertEqual(result['profile']['code'], 'INVALID_REQUEST_MODEL')
                self.assertEqual(result['profile']['status'], 400)
                self.assertEqual(result['reads'], 1)
                self.assertEqual(json.loads(result['bodyAfter']), original)
                self.assertNotIn('decision', result)
                self.assertNotIn('targetAllowed', result)
        self.assertEqual(self.results['profile-malformed-body']['rejection'],
                         {'status': 400, 'code': 'INVALID_REQUEST_BODY', 'reason': 'Invalid request body'})

    def test_generic_target_does_not_reread_body_and_preserves_upstream_and_responses_conditions(self):
        for name, result in self.results.items():
            if 'targetBodyReads' in result and result['operationConstraint']['kind'] in ('bodyModel', 'textEmbeddings', 'rerank'):
                self.assertEqual(result['targetBodyReads'], 0, name)
        for name, original in self.unparsed_model_cases.items():
            with self.subTest(case=name):
                result = self.results[name]
                self.assertTrue(result['requestAllowed'])
                for stage in ('profile', 'decision', 'limits'):
                    self.assertTrue(result[stage]['allowed'], stage)
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(result['profileByteReads'], 0)
                self.assertEqual(result['limitByteReads'], 1)
                self.assertEqual(result['targetBodyReads'], 0)
                self.assertEqual(result['bodyAfter'], original)
        for name in ('alias-default', 'responses-tool-schema', 'future-gpt-responses-create'):
            self.assertEqual(self.results[name]['targetBodyReads'], 1, name)
        self.assertTrue(self.results['alias-saved-file-preserved']['targetAllowed'])
        self.assertEqual(json.loads(self.results['alias-saved-file-preserved']['bodyAfter']),
                         {**self.alias_body, 'model':'gpt-5.6-luna', 'store':False,
                          'input':[{'type':'input_file','file_id':'synthetic-file'}]})
        self.assertFalse(self.results['alias-reject-forbidden-tools']['targetAllowed'])
        self.assertEqual(self.results['alias-wire-overflow']['profile']['status'], 413)
        self.assertEqual(self.results['alias-profile-overflow']['limits']['status'], 413)

    def test_selected_setting_errors_are_500_and_basic_settings_are_inherited(self):
        for name in self.selected_settings_errors:
            result = self.results[name]
            self.assertEqual(result['profile']['status'], 500, name)
            self.assertEqual(result['rejection']['code'], 'OPERATION_SETTINGS_INVALID')
            self.assertEqual(result['reads'], 1)
            self.assertNotIn('decision', result)
        for label in ('entry-missing', 'entry-empty', 'alias-only'):
            result = self.results['operation-inheritance-' + label]
            self.assertTrue(result['profile']['allowed'], label)
            self.assertEqual(result['profile']['settingId'], '')
            self.assertEqual(result['profile']['constraint']['maxRequestBytes'], 2048)
            self.assertEqual(result['profile']['constraint']['outputTokenParameter'], 'max_completion_tokens')
            self.assertNotIn('modelSettings', result['profile']['constraint'])
            self.assertEqual(result['profile']['effectiveModel'], 'shared')
        override = self.results['operation-inheritance-entry-override']['profile']
        self.assertTrue(override['allowed'])
        self.assertEqual(override['settingId'], 'custom')
        self.assertEqual(override['constraint']['maxRequestBytes'], 1024)
        self.assertEqual(override['constraint']['integerLimits'], {'x': {'minimum': 5, 'maximum': 6}})
        self.assertEqual(override['constraint']['outputTokenParameter'], 'max_completion_tokens')

    def test_shared_real_models_always_use_the_same_model_settings(self):
        for name, (expected, original) in self.shared_model_cases.items():
            with self.subTest(case=name):
                result = self.results[name]
                self.assertTrue(result['profile']['allowed'])
                self.assertEqual(result['profile']['settingId'], expected)
                self.assertTrue(result['decision']['allowed'])
                self.assertTrue(result['limits']['allowed'])
                self.assertTrue(result['targetAllowed'])
                self.assertEqual(json.loads(result['bodyAfter']), original)

    def test_compiled_flux_operations_use_the_model_selected_profile_after_api_inheritance(self):
        template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        apis = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / 'config')['apiDefinitions']
        api = next(item for item in apis if item['name'] == 'blackforest-images-v1')
        # 正式な配備入力の組立てが補う接続元を、独立した試験用URLで渡します。
        api['backendUrl'] = 'https://provider.example.com'
        parameters = {'apiDefinitions': [api], 'publicHostName': 'gateway.example.com', 'names': {'apim': 'apim-fixture'}}
        policy = ET.fromstring(compiled_policy_value(template, parameters))
        source = ET.parse(CORE / 'apps/gateway/apim/policies/api-templates/gateway.xml')
        self.assertEqual(policy.find("inbound/include-fragment[@fragment-id='core-api-operation-settings']").attrib,
                         source.find("inbound/include-fragment[@fragment-id='core-api-operation-settings']").attrib)
        # Azureへ登録する部品にも、同名XML/C#から展開した説明と実行コードが入ることを確認します。
        resources = template['resources']
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        fragment = next(item for item in resources if item['type'] == 'Microsoft.ApiManagement/service/policyFragments'
                        and 'core-api-operation-settings' in item['name'])
        rendered = PolicyExpression(fragment['properties']['value'], template['variables'], functions=template['functions']).evaluate(parameters)
        self.assertEqual(ET.tostring(ET.fromstring(rendered)), ET.tostring(ET.fromstring(render_operation_settings_fragment())))
        # 旧ヘッダーは選択処理で参照せず、旧クライアントの申告を下流へも渡しません。
        self.assertNotIn('X-Egakumi-Profile', (CORE / 'apps/gateway/apim/policies/fragments/request/core-api-operation-settings/core-api-operation-settings.cs').read_text(encoding='utf-8'))
        self.assertEqual(policy.find("inbound/set-header[@name='X-Egakumi-Profile']").get('exists-action'), 'delete')
        for index, operation in enumerate(api['operations']):
            actual = ET.fromstring(compiled_policy_value(template, parameters, index=index, resource_name='operationPolicies'))
            self.assertEqual(list(actual.find('inbound'))[0].tag, 'base')
            for variant in ('flex', 'pro'):
                result = self.results['flux-' + operation['name'] + '-' + variant]
                self.assertTrue(result['profile']['allowed'])
                self.assertTrue(result['decision']['allowed'])
                self.assertTrue(result['limits']['allowed'])
                self.assertTrue(result['targetAllowed'])
                selected = result['profile']['settingId']
                self.assertEqual(selected, variant)
                self.assertEqual(result['profile']['effectiveModel'], 'FLUX.2-' + variant)
                condition = '@((string)JObject.Parse((string)context.Variables["gatewayOperationSelection"])["effectiveModel"] == "FLUX.2-' + variant + '")'
                branch = next(node for node in actual.findall('inbound/choose/when') if node.get('condition') == condition)
                self.assertEqual(branch.find('rewrite-uri').attrib,
                                 {'template': '/providers/blackforestlabs/v1/flux-2-' + variant + '?api-version=preview',
                                  'copy-unmatched-params': 'false'})

    def test_profile_rejection_preserves_audit_order_and_request_id_without_a_response_body(self):
        root = ET.parse(CORE / "apps/gateway/apim/policies/api-templates/gateway.xml").getroot()
        rejection = next(node for node in root.findall('inbound/choose/when')
                         if 'gatewayOperationSelection' in node.get('condition', ''))
        nodes = list(rejection)
        telemetry = rejection.find("include-fragment[@fragment-id='core-gateway-telemetry']")
        phase = rejection.find("set-variable[@name='gatewayAuditPhase']")
        self.assertLess(nodes.index(rejection.find("set-variable[@name='gatewayAuditStatus']")), nodes.index(phase))
        for name in ('gatewayAuditCode', 'gatewayAuditStatus', 'gatewayAuditPhase'):
            self.assertLess(nodes.index(rejection.find("set-variable[@name='" + name + "']")), nodes.index(telemetry))
        response = rejection.find('return-response')
        self.assertLess(nodes.index(telemetry), nodes.index(response))
        self.assertEqual(response.find('set-status').get('code'), '@((int)context.Variables["gatewayAuditStatus"])')
        request_id = response.find("set-header[@name='apim-request-id']")
        self.assertEqual(request_id.get('exists-action'), 'override')
        self.assertEqual(request_id.find('value').text, '@(context.RequestId.ToString())')
        cache = response.find("set-header[@name='Cache-Control']")
        self.assertEqual(cache.get('exists-action'), 'override')
        self.assertEqual(cache.find('value').text, 'no-store')
        self.assertIsNone(response.find('set-body'))

    def test_openai_inference_requires_one_content_type_before_reading_body(self):
        for label in ("chat", "responses"):
            for suffix in ("missing-type", "duplicate-type"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertTrue(self.results[label + "-revision-query"]["requestAllowed"])

    def test_openai_inference_guard_preserves_native_data_and_other_api_contracts(self):
        for label in ("chat", "responses"):
            # method/pathはAPIMの操作照合を通さず設定済みの操作として直接評価します。
            # 正式なResponses登録がPOST /responsesだけであることは設定検査で確認します。
            for suffix in ("native-request", "json-charset", "different-method", "other-operation"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                self.assertTrue(result["profile"]["allowed"], name)
                self.assertTrue(result["decision"]["allowed"], name)
                self.assertTrue(result["limits"]["allowed"], name)
                self.assertTrue(result["targetAllowed"], name)
                self.assertEqual(result["targetBodyReads"], 1 if label == "responses" else 0, name)
                self.assertIn("provider_extension", json.loads(result["bodyAfter"]))

    def test_response_call_ids_and_same_name_query_remain_unchanged(self):
        result = self.results["responses-call-id-native"]
        self.assertTrue(result["requestAllowed"])
        for stage in ("profile", "decision", "limits"):
            self.assertTrue(result[stage]["allowed"], stage)
        self.assertTrue(result["targetAllowed"])
        body = json.loads(result["bodyAfter"])
        self.assertEqual(body, self.responses_call_id_body)
        self.assertEqual(body["input"][0]["call_id"], body["input"][1]["call_id"])
        for suffix in ("query", "duplicate-query", "query-before-body"):
            rejected = self.results["responses-call-id-reject-" + suffix]
            with self.subTest(case=suffix):
                self.assertTrue(rejected["requestAllowed"])
                self.assertEqual(rejected["inputBodyReads"], 0)
                self.assertEqual(rejected["bodyAfter"], "{" if suffix == "query-before-body"
                                 else json.dumps(self.responses_call_id_body, separators=(",", ":")))

    def test_content_type_header_rejection_uses_the_fragment_result_and_is_audited(self):
        root = ET.parse(CORE / "apps/gateway/apim/policies/api-templates/gateway.xml").getroot()
        inbound = list(root.find("inbound"))
        guard = next(node for node in inbound if node.tag == "include-fragment" and node.get("fragment-id") == "core-api-input-validation")
        profile = next(node for node in inbound if node.tag == "include-fragment" and node.get("fragment-id") == "core-api-operation-settings")
        self.assertLess(next(i for i, node in enumerate(inbound) if node.tag == "base"), inbound.index(guard))
        self.assertLess(inbound.index(guard), inbound.index(profile))
        fragment = ET.fromstring(render_input_validation_fragment())
        output = fragment.find('set-variable').get('name')
        self.assertEqual(output, 'gatewayApiInputValidationPassed')
        rejection = inbound[inbound.index(guard) + 1].find("when")
        self.assertEqual(rejection.get('condition'), '@(!(bool)context.Variables["' + output + '"])')
        self.assertEqual(rejection.find("set-variable[@name='gatewayAuditPhase']").get("value"), "rejected")
        self.assertEqual(rejection.find("set-variable[@name='gatewayAuditCode']").get("value"), "INVALID_CONTENT_TYPE_HEADER")
        self.assertEqual(rejection.find("set-variable[@name='gatewayAuditStatus']").get("value"), "@(400)")
        self.assertIsNotNone(rejection.find("include-fragment[@fragment-id='core-gateway-telemetry']"))
        response = rejection.find("return-response")
        self.assertEqual(response.find("set-status").get("code"), "400")
        self.assertEqual(response.find("set-status").get("reason"), "Invalid Content-Type header")
        request_id = response.find("set-header[@name='apim-request-id']")
        self.assertEqual(request_id.get("exists-action"), "override")
        self.assertEqual(request_id.find("value").text, "@(context.RequestId.ToString())")
        cache = response.find("set-header[@name='Cache-Control']")
        self.assertEqual(cache.get("exists-action"), "override")
        self.assertEqual(cache.find("value").text, "no-store")
        self.assertIsNone(response.find("set-body"))

    def test_explicit_contract_selection_is_generic_and_unconfigured_operations_pass(self):
        for identifier, expected in self.input_only_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertEqual(result["requestAllowed"], expected)
                self.assertEqual(result["reads"], 0)
                self.assertEqual(result["inputMethodReads"], 0)
                self.assertEqual(result["inputQueryReads"], 0)
                self.assertEqual(result["bodyAfter"], "{")
                item = self.input_case_source[identifier]
                headers = {name: value if isinstance(value, list) else [value]
                           for name, value in item.get("headers", {}).items()}
                headers["api-key"] = ["fixture-value"]
                self.assertEqual(result["inputHeadersAfter"], headers)
                self.assertEqual(result["queryAfter"], {name: value if isinstance(value, list) else [value]
                                                      for name, value in item["query"].items()})

    def test_missing_or_broken_control_tables_raise_before_body_reads(self):
        for identifier in self.input_error_cases:
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertIn("inputError", result)
                self.assertNotIn("requestAllowed", result)
                self.assertEqual(result["reads"], 0)
                self.assertEqual(result["inputMethodReads"], 0)
                self.assertEqual(result["bodyAfter"], "{")

    def test_all_13_standard_operations_preserve_queries_without_inspecting_them(self):
        exercised = set()
        for identifier, item in self.standard_query_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                self.assertEqual(result["inputBodyReads"], 0)
                self.assertEqual(result["inputQueryReads"], 0)
                self.assertEqual(result["queryAfter"], {name: value if isinstance(value, list) else [value]
                                                      for name, value in item["query"].items()})
                exercised.add((item["api"].split(";rev=", 1)[0], item["operation"]))
        self.assertEqual(exercised, STANDARD_OPERATIONS)

    def test_all_13_standard_operations_do_not_recheck_the_http_method(self):
        # APIMの操作選択は再現しません。選択済み操作の入力部品だけを検証します。
        exercised = set()
        for identifier, item in self.standard_method_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                self.assertEqual(result["inputMethodReads"], 0)
                self.assertEqual(result["inputBodyReads"], 0)
                self.assertEqual(result["inputQueryReads"], 0)
                exercised.add((item["api"], item["operation"]))
        self.assertEqual(exercised, STANDARD_OPERATIONS)

    def test_nonempty_media_passes_entry_and_upload_limits_without_changing_body(self):
        # 入力部品とアップロード上限はMIMEの許可一覧を使いません。WAF・サービスの受付は別です。
        exercised = set()
        for identifier, item in self.standard_media_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                self.assertEqual(result["inputMethodReads"], 0)
                self.assertEqual(result["inputBodyReads"], 0)
                self.assertEqual(result["inputQueryReads"], 0)
                self.assertEqual(result["inputHeadersAfter"]["Content-Type"], [item["headers"]["Content-Type"]])
                if identifier.startswith(("openai-edit-", "transcription-", "speech-tts-", "speech-stt-")):
                    self.assertTrue(result["limits"]["allowed"])
                    self.assertEqual(result["limits"]["status"], 200)
                    declared = (item["api"], item["operation"]) in {
                        ("openai-images-v1", "image-edit"), ("openai-audio-v1", "audio-transcriptions"), ("azure-speech-v1", "stt"),
                        ("azure-speech-v1", "tts")}
                    self.assertEqual(result["limits"]["code"], "DECLARED_SIZE_ONLY" if declared else "BOUNDED_UPLOAD")
                    self.assertEqual(result["reads"], 1 if item["operation"] == "tts" else 0 if declared else 1)
                    self.assertEqual(result["bodyAfter"], item["rawBody"])
                exercised.add((item["api"], item["operation"]))
        self.assertEqual(exercised, STANDARD_OPERATIONS)

    def test_image_operations_require_one_content_type_before_body_reads(self):
        for label in self.image_cases:
            for suffix in ("missing-type", "duplicate-type"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)

    def test_image_operations_preserve_json_extensions_multipart_and_model_binding(self):
        for label in self.image_cases:
            for suffix in ("native-request", "revision-request"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                if label.startswith("openai-edit-"):
                    self.assertTrue(result["bodyAfter"].startswith("--synthetic-image-boundary\r\n"), name)
                    self.assertEqual(result["reads"], 0, name)
                else:
                    self.assertEqual(json.loads(result["bodyAfter"]), self.image_bodies[label], name)
                    # OpenAI画像生成はサイズ確認の1回だけ、FLUXは先行処理の計測値を使い、この部品では本文を読みません。
                    self.assertEqual(result["limitBodyReads"], 1, name)

    def test_image_count_and_format_settings_do_not_restrict_limits(self):
        for name, original in self.image_count_cases.items():
            with self.subTest(case=name):
                result = self.results[name]
                self.assertTrue(result["requestAllowed"])
                self.assertTrue(result["limits"]["allowed"])
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["limitBodyReads"], 1)
                self.assertEqual(result["bodyBeforeNormalization"], result["bodyAfter"])
                self.assertEqual(json.loads(result["bodyAfter"]), original)
        unlisted_choice = self.results["flux-generate-unlisted-output-format"]
        self.assertEqual(unlisted_choice["limits"], {"allowed": True, "status": 200, "code": "REQUEST_WITHIN_LIMITS"})
        self.assertTrue(unlisted_choice["targetAllowed"])
        self.assertEqual(unlisted_choice["limitBodyReads"], 1)
        self.assertEqual(unlisted_choice["profileByteReads"], 1)
        self.assertEqual(unlisted_choice["limitByteReads"], 1)
        self.assertEqual(unlisted_choice["bodyBeforeNormalization"], unlisted_choice["bodyAfter"])
        self.assertEqual(json.loads(unlisted_choice["bodyAfter"]), {**self.image_bodies["flux-generate"], "output_format": "synthetic"})

    def test_embedding_requires_one_content_type_and_queries_are_unchanged(self):
        for label in self.embedding_cases:
            for suffix in ("missing-type", "duplicate-type"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertTrue(self.results[label + "-revision-query"]["requestAllowed"])
            self.assertEqual(self.results[label + "-revision-query"]["inputBodyReads"], 0)
        for suffix in ("both-versions", "duplicate-version", "empty-version", "unknown-version", "uppercase-name", "uppercase-value", "version-with-input"):
            name = "openai-embedding-reject-" + suffix
            self.assertTrue(self.results[name]["requestAllowed"], name)
            self.assertEqual(self.results[name]["inputBodyReads"], 0, name)
        self.assertTrue(self.results["cohere-embedding-reject-api-version"]["requestAllowed"])
        self.assertEqual(self.results["cohere-embedding-reject-api-version"]["inputBodyReads"], 0)

    def test_embedding_guard_preserves_body_options_versions_and_other_operations(self):
        for label in self.embedding_cases:
            for suffix in ("native-request", "json-charset", "revision-request"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                self.assertEqual(json.loads(result["bodyAfter"]), self.embedding_bodies[label], name)
        for version in ("v1", "preview"):
            for prefix in ("openai-embedding-version-", "openai-embedding-revision-version-"):
                result = self.results[prefix + version]
                self.assertTrue(result["requestAllowed"])
                self.assertTrue(result["limits"]["allowed"])
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["queryAfter"], {"api-version": [version]})
                self.assertEqual(json.loads(result["bodyAfter"]), self.embedding_bodies["openai-embedding"])
        for suffix, native_input in (("scalar", "Synthetic text"), ("tokens", [1, 2]), ("token-batches", [[1, 2], [3]])):
            result = self.results["openai-embedding-input-" + suffix]
            self.assertTrue(result["requestAllowed"])
            self.assertTrue(result["limits"]["allowed"])
            self.assertTrue(result["targetAllowed"])
            self.assertEqual(json.loads(result["bodyAfter"])["input"], native_input)

    def test_rerank_and_http_audio_require_one_content_type_before_body_reads(self):
        for label in self.native_contract_cases:
            for suffix in ("missing-type", "duplicate-type"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertTrue(self.results[label + "-revision-query"]["requestAllowed"])
            self.assertEqual(self.results[label + "-revision-query"]["inputBodyReads"], 0)

    def test_rerank_and_http_audio_preserve_native_bodies_and_future_models(self):
        for label in self.native_contract_cases:
            for suffix in ("native-request", "mixed-media", "revision-request"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                if label == "rerank":
                    self.assertEqual(json.loads(result["bodyAfter"]), json.loads(self.native_contract_bodies[label]), name)
                else:
                    self.assertEqual(result["bodyAfter"], self.native_contract_bodies[label], name)
                    self.assertEqual(result["reads"], 0 if label in ("transcription", "speech-stt") else 1, name)
                self.assertEqual(result["queryAfter"], {}, name)
            self.assertTrue(self.results[label + "-other-api"]["requestAllowed"])
            # この操作は入力契約未指定です。本文用のprofile設定だけでは入力契約を推測しません。
            self.assertTrue(self.results[label + "-other-operation"]["requestAllowed"])
        for label in ("rerank", "transcription"):
            result = self.results[label + "-future-model"]
            self.assertTrue(result["requestAllowed"])
            self.assertTrue(result["limits"]["allowed"])
            self.assertTrue(result["targetAllowed"])

    def test_standard_declared_upload_settings_reach_limits_without_reading_or_changing_the_body(self):
        exercised = set()
        for identifier, (item, over_bound) in self.declared_size_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                self.assertTrue(result["profile"]["allowed"])
                self.assertTrue(result["decision"]["allowed"])
                source = item["config"]["operationConstraints"][item["operation"]]
                self.assertEqual(source["requestBodySizeCheck"], "declared")
                self.assertEqual(result["profile"]["constraint"], source)
                self.assertEqual(result["operationConstraint"], source)
                self.assertEqual(result["limits"], {"allowed": not over_bound,
                    "status": 413 if over_bound else 200, "code": "REQUEST_TOO_LARGE" if over_bound else "DECLARED_SIZE_ONLY"})
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["inputBodyReads"], 0)
                self.assertEqual(result["reads"], 0)
                self.assertNotIn("speech", result)
                self.assertEqual(result["bodyAfter"], item["rawBody"])
                exercised.add((item["api"], item["operation"]))
        self.assertEqual(exercised, {("azure-speech-v1", "stt"), ("openai-audio-v1", "audio-transcriptions"),
                                   ("openai-images-v1", "image-edit"), ("document-analysis-v1", "analyze_document")})

    def test_standard_tts_combines_size_and_markup_checks_in_one_preserved_body_read(self):
        for identifier, (item, status) in self.speech_guard_cases.items():
            with self.subTest(case=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                self.assertTrue(result["profile"]["allowed"])
                self.assertEqual(result["operationConstraint"]["requestBodySizeCheck"], "declared")
                self.assertEqual(result["limits"], {"allowed": True, "status": 200, "code": "DECLARED_SIZE_ONLY"})
                self.assertEqual(result["limitBodyReads"], 0)
                self.assertEqual(result["speech"], {"allowed": status == 200, "status": status,
                    "code": "REQUEST_TOO_LARGE" if status == 413 else "INVALID_SPEECH_MARKUP" if status == 400 else "SPEECH_MARKUP_ALLOWED"})
                self.assertEqual(result["reads"], 1)
                self.assertEqual(result["bodyAfter"], item["rawBody"])
                if status != 200:
                    self.assertNotIn("targetAllowed", result)

    def test_decision_contract_requires_one_content_type_before_reading_body(self):
        for suffix in ("missing-type", "duplicate-type"):
            name = "decision-reject-" + suffix
            self.assertFalse(self.results[name]["requestAllowed"], name)
            self.assertEqual(self.results[name]["reads"], 0, name)
        self.assertTrue(self.results["decision-revision-query"]["requestAllowed"])
        self.assertEqual(self.results["decision-revision-query"]["inputBodyReads"], 0)

    def test_decision_native_data_and_unrelated_contracts_keep_their_existing_behavior(self):
        for suffix in ("native-request", "json-charset", "revision-request"):
            name = "decision-" + suffix
            result = self.results[name]
            self.assertTrue(result["requestAllowed"], name)
            for stage in ("profile", "decision", "limits"):
                self.assertTrue(result[stage]["allowed"], (name, stage))
            self.assertNotIn("requestBodyBytes", result["profile"])
            self.assertEqual(result["profileByteReads"], 0)
            self.assertEqual(result["limitByteReads"], 1)
            self.assertTrue(result["targetAllowed"], name)
            self.assertEqual(json.loads(result["bodyAfter"]), self.decision_body, name)
            self.assertEqual(result["queryAfter"], {}, name)
        self.assertTrue(self.results["decision-other-api"]["requestAllowed"])
        self.assertTrue(self.results["decision-other-operation"]["requestAllowed"])
        future_model = self.results["decision-wrong-model"]
        self.assertTrue(future_model["requestAllowed"])
        self.assertTrue(future_model["targetAllowed"])
        self.assertEqual(json.loads(future_model["bodyAfter"])["model"], "unapproved-synthetic-model")

    def test_operation_settings_use_the_base_and_http_target_accepts_unknown_models(self):
        for name in ("unsupported-model", "unsupported-model-with-profile", "responses-image-unknown-model"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)
            self.assertTrue(self.results[name]["limits"]["allowed"], name)
            self.assertEqual(self.results[name]["profile"]["settingId"], "", name)
            self.assertTrue(self.results[name]["targetAllowed"], name)
        for name in ("image-default", "image-authorized", "audio-default", "file-default"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)

    def test_future_gpt_on_the_same_endpoint_uses_base_settings_without_apim_model_registration(self):
        for identifier, (body, expected_constraint) in self.future_gpt_cases.items():
            with self.subTest(operation=identifier):
                result = self.results[identifier]
                self.assertTrue(result["requestAllowed"])
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], stage)
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["profile"]["requestedModel"], body["model"])
                self.assertEqual(result["profile"]["effectiveModel"], body["model"])
                self.assertEqual(result["profile"]["settingId"], "")
                self.assertEqual(result["operationConstraint"], expected_constraint)
                self.assertEqual(json.loads(result["bodyAfter"]), {**body, 'store': False} if identifier == 'future-gpt-chat-completions' else body)

    def test_tool_schema_keys_and_native_responses_images_do_not_require_special_profile(self):
        for name in ("responses-tool-schema", "responses-default-image"):
            result = self.results[name]
            self.assertTrue(result["profile"]["allowed"], name)
            self.assertEqual(result["profile"]["settingId"], "response")
            self.assertTrue(result["limits"]["allowed"], name)
            self.assertTrue(result["targetAllowed"], name)

    def test_bytes_are_bounded_before_json_and_again_for_selected_profile(self):
        self.assertEqual(self.results["huge-malformed"]["profile"]["status"], 413)
        self.assertEqual(self.results["huge-malformed"]["reads"], 1)
        self.assertNotIn("requestBodyBytes", self.results["huge-malformed"]["profile"])
        self.assertTrue(self.results["selected-size"]["profile"]["allowed"])
        self.assertEqual(self.results["selected-size"]["limits"]["status"], 413)
        for name in ("selected-size", "alias-profile-overflow"):
            with self.subTest(selected_limit=name):
                result = self.results[name]
                self.assertEqual(result["limits"]["status"], 413)
                self.assertEqual(result["limits"]["code"], "REQUEST_TOO_LARGE")
                self.assertGreater(result["profile"]["requestBodyBytes"], result["operationConstraint"]["maxRequestBytes"])
                self.assertEqual(result["profileByteReads"], 1)
                self.assertEqual(result["limitByteReads"], 1)
        self.assertEqual(self.results["alias-wire-overflow"]["profile"]["status"], 413)
        self.assertNotIn("requestBodyBytes", self.results["alias-wire-overflow"]["profile"])

    def test_model_operations_reuse_original_wire_bytes_without_a_second_byte_read(self):
        for name in ("shared-chat-completions-absent", "shared-responses-create-absent",
                     "flux-flux2-generate-flex", "flux-flux2-edit-pro", "preserved-wire-bytes"):
            with self.subTest(operation=name):
                result = self.results[name]
                self.assertTrue(result["profile"]["allowed"])
                self.assertTrue(result["limits"]["allowed"])
                self.assertEqual(result["profile"]["requestBodyBytes"], len(result["bodyBeforeNormalization"].encode("utf-8")))
                self.assertEqual(result["profileByteReads"], 1)
                self.assertEqual(result["limitByteReads"], 1)
                # 汎用項目検査の専用JSON読取りもなくなり、ここまでの本文読取りは先行処理の計測1回だけです。
                self.assertEqual(result["limitBodyReads"], 1)
        preserved = self.results["preserved-wire-bytes"]
        self.assertEqual(preserved["bodyAfter"], self.preserved_wire_body)
        compact = json.dumps(json.loads(self.preserved_wire_body), ensure_ascii=False, separators=(",", ":"))
        self.assertGreater(preserved["profile"]["requestBodyBytes"], len(compact.encode("utf-8")))
        self.assertGreater(preserved["profile"]["requestBodyBytes"], len(self.preserved_wire_body))

    def test_profile_size_maximum_and_existing_range_boundaries_are_preserved(self):
        minimum = self.results['profile-size-existing-minimum']
        self.assertEqual(minimum['profile']['status'], 400)
        self.assertEqual(minimum['profile']['code'], 'INVALID_REQUEST_BODY')
        self.assertEqual(minimum['reads'], 1)
        for label in ('ceiling', 'convertible-string', 'maximum'):
            result = self.results['profile-size-existing-' + label]
            self.assertTrue(result['profile']['allowed'], label)
            self.assertEqual(result['profile']['settingId'], 'chat')
            self.assertEqual(result['reads'], 1)
        over_limit = self.results['huge-malformed']
        self.assertEqual(over_limit['rejection']['status'], 413)
        self.assertEqual(over_limit['rejection']['code'], 'REQUEST_TOO_LARGE')

    def test_ws_selection_accepts_unlisted_models_and_keeps_query_and_fixed_backend_contracts(self):
        for name, model in (("ws-default", "standard"), ("ws-mini", "mini"), ("ws-unlisted", "mini"),
                            ("ws-unknown", "unknown"), ("ws-default-outside-catalog", "standard"),
                            ("ws-null-catalog", "future-synthetic-model"), ("ws-object-catalog", "future-synthetic-model"),
                            ("ws-empty-catalog", "future-synthetic-model"), ("ws-missing-catalog", "future-synthetic-model")):
            self.assertTrue(self.results[name]["decision"]["allowed"], name)
            self.assertTrue(self.results[name]["queryAllowed"], name)
            self.assertEqual(self.results[name]["model"], model, name)
        self.assertEqual(self.results["ws-duplicate"]["model"], "!invalid-model!")
        self.assertFalse(self.results["ws-duplicate"]["queryAllowed"])
        self.assertFalse(self.results["ws-unknown-query"]["queryAllowed"])
        for name in ("ws-fixed-default", "ws-fixed-model", "ws-fixed-other-model", "ws-fixed-duplicate"):
            self.assertEqual(self.results[name]["model"], "", name)
            self.assertEqual(self.results[name]["queryAllowed"], name != "ws-fixed-duplicate", name)
        for name, result in self.results.items():
            if name.startswith("ws-"):
                self.assertEqual(result["reads"], 0)

    def test_added_chat_and_flux_models_use_their_own_profile_contracts(self):
        for name in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7", "router", "flux-pro"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)
            self.assertTrue(self.results[name]["limits"]["allowed"], name)
        self.assertEqual(self.results["flux-wrong-profile"]["profile"], self.results["flux-pro"]["profile"])
        self.assertTrue(self.results["flux-wrong-profile"]["limits"]["allowed"])
        self.assertTrue(self.results["flux-wrong-profile"]["targetAllowed"])

    def test_ai_requests_use_the_classic_buffer_ceiling_independently_of_waf_inspection(self):
        accepted = self.results["flux-byte-limit-0"]
        self.assertTrue(accepted["profile"]["allowed"])
        self.assertTrue(accepted["limits"]["allowed"])
        self.assertEqual(self.results["flux-byte-limit-1"]["profile"]["status"], 413)
        for name in ("blackforest-images-v1", "openai-inference-v1"):
            definition = json.loads((CORE / "apps/gateway/apim/apis" / (name + ".json")).read_text())
            def inspect(value):
                if isinstance(value, dict):
                    if value.get("maxRequestBytes", 0) > 32768:
                        self.assertEqual(value["maxRequestBytes"], 524288000)
                    for child in value.values(): inspect(child)
                elif isinstance(value, list):
                    for child in value: inspect(child)
            inspect(definition)


if __name__ == "__main__":
    unittest.main()
