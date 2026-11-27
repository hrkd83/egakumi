"""APIMのbroker境界・固定接続先・本人照合を実際のC#式で検証する。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from deployment.helpers import credential_login
from deployment.helpers.apim_audit import render_audit_policy
from policy_fixture_paths import policy_fixture_path

CORE=Path(__file__).resolve().parents[1]
TX,SECRET,LAUNCH,STATE,COOKIE=(c*64 for c in "abcdf")
SESSION="cs2."+TX+"."+SECRET
TENANT="11111111-1111-4111-8111-111111111111"
OWNER="22222222-2222-4222-8222-222222222222"
def digest(value):return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
def settings():
 return {"providerNames":["graph-delegated"],**credential_login.SESSION_POLICY_DEFAULTS,"transactionLifetimeSeconds":600,"loginCallsPerMinute":10,
  "publicOrigin":"https://gateway.example.com","keyHeader":"api-key","azureTenantId":TENANT,
  "profiles":[{"name":"graph-delegated","tenantId":TENANT}],
  "providers":[{"name":"graph-delegated-a","sourceName":"graph-delegated","oauthTenantId":TENANT,"identityEndpoint":"https://graph.microsoft.com/v1.0/me","identityIdProperty":"id"}]}
def expression(value):
 value=value.strip()
 return value[2:-1] if value.startswith("@{") else "return "+value[2:-1]+";"

def operation_error_methods(xml,method,*,entra_enabled=True):
 """実on-errorの条件・監査・応答を順に評価する。ネットワークやAPIM自体は再実装しない。"""
 from deployment.helpers.apim_common_authentication import (
  render_operation_entra_authentication_error,expand_operation_entra_authentication_error,
 )
 policies=CORE/'apps/apim/policies'
 telemetry=render_audit_policy(policy_fixture_path(policies,'core-gateway-telemetry.xml').read_text(encoding='utf-8'))
 rate=render_audit_policy(policy_fixture_path(policies,'rate-limit-error.xml').read_text(encoding='utf-8')).replace('__GATEWAY_TELEMETRY_XML__',telemetry.replace('<fragment>','').replace('</fragment>',''))
 value=render_operation_entra_authentication_error(render_audit_policy(xml),entra_enabled=entra_enabled)
 value=expand_operation_entra_authentication_error(value).replace('__RATE_LIMIT_ERROR_XML__',rate.replace('<fragment>','').replace('</fragment>',''))
 root=ET.fromstring(value);methods=[]
 def actual(value):
  value=(value or '').strip()
  if not value.startswith('@'):return json.dumps(value)
  name=method+'Expression'+str(len(methods))
  methods.append('static object '+name+'(Context context) { '+expression(value)+' }')
  return name+'(context)'
 def sequence(nodes):
  result=[]
  for node in nodes:
   if node.tag=='set-variable':
    result.append('context.Variables['+json.dumps(node.get('name'))+']='+actual(node.get('value'))+';')
   elif node.tag=='choose':
    for index,branch in enumerate(node):
     if branch.tag=='when':
      result.append(('if' if index==0 else 'else if')+'((bool)'+actual(branch.get('condition'))+') { ')
     elif branch.tag=='otherwise':result.append('else { ')
     else:raise AssertionError('Unsupported on-error choose branch')
     if node.get('id')=='core-entra-authentication-error':result.append('flow["sharedHandled"]=true;')
     result.append(sequence(branch));result.append('}')
   elif node.tag=='trace':
    result.append('audits.Add(JObject.Parse((string)'+actual(node.find('message').text)+'));')
   elif node.tag=='include-fragment':
    if node.get('fragment-id')=='core-gateway-telemetry':value=telemetry
    elif node.get('fragment-id')=='core-query-telemetry':value=render_audit_policy(policy_fixture_path(policies,'core-query-telemetry.xml').read_text(encoding='utf-8'))
    else:raise AssertionError('Unexpected error fragment')
    result.append(sequence(ET.fromstring(value)))
   elif node.tag=='return-response':
    status=node.find('set-status')
    result.append('flow["status"]=Convert.ToInt32('+actual(status.get('code'))+');flow["reason"]=Convert.ToString('+actual(status.get('reason'))+');')
    result.append('flow["headers"]=new JObject();')
    for header in node.findall('set-header'):
     name=json.dumps(header.get('name'));action=actual(header.get('exists-action'))
     values=','.join('Convert.ToString('+actual(value.text)+')' for value in header.findall('value'))
     result.append('if(Convert.ToString('+action+')=="override")((JObject)flow["headers"])['+name+']=String.Join(",",new string[]{'+values+'});')
    body=node.find('set-body')
    if body is None:result.append('flow["hasBody"]=false;flow["body"]=null;')
    else:result.append('flow["hasBody"]=true;flow["body"]=JToken.Parse(Convert.ToString('+actual(body.text)+'));')
    result.append('return flow;')
   else:raise AssertionError('Unsupported on-error policy: '+node.tag)
  return ''.join(result)
 code=[]
 for node in root.find('on-error'):
  if node.get('id')!='core-entra-authentication-error':code.append('flow["legacyReached"]=true;')
  code.append(sequence([node]))
 tail='' if list(root.find('on-error'))[-1].tag=='return-response' else 'return flow;'
 body='static JObject '+method+'(Context context) { var audits=new JArray();var flow=new JObject(new JProperty("audits",audits),new JProperty("sharedHandled",false),new JProperty("legacyReached",false),new JProperty("requestId",context.RequestId.ToString()));'+''.join(code)+tail+' }'
 return '\n'.join(methods+[body])

def operation_error_cases():
 """同一Sourceの別IDと、個別分類を持つ実行時エラーを代表させる。"""
 return (
  ('entra',{'policyId':'core-entra-token-validation','source':'validate-azure-ad-token','reason':'TokenValidationFailed'}),
  ('other-id',{'policyId':'another-token-validation','source':'validate-azure-ad-token','reason':'TokenValidationFailed'}),
  ('null-id',{'policyId':None,'source':'validate-azure-ad-token','reason':'TokenValidationFailed'}),
  ('empty-id',{'policyId':'','source':'validate-azure-ad-token','reason':'TokenValidationFailed'}),
  ('subscription',{'policyId':'','source':'authorization','reason':'SubscriptionKeyInvalid'}),
  ('timeout',{'policyId':'','source':'forward-request','reason':'Timeout'}),
  ('rate',{'policyId':'core-rate-limit','source':'rate-limit-by-key','reason':'RateLimitExceeded'}),
  ('connection',{'policyId':'','source':'forward-request','reason':'BackendConnectionFailure'}),
  ('expression',{'policyId':'','source':'set-variable','reason':'ExpressionValueEvaluationFailure'}),
 )

def assert_shared_operation_error(test,result):
 test.assertTrue(result['sharedHandled'])
 test.assertFalse(result['legacyReached'])
 test.assertEqual((result['status'],result['reason']),(401,'Invalid authentication'))
 test.assertEqual(result['headers'],{'WWW-Authenticate':'Bearer','apim-request-id':result['requestId'],'Cache-Control':'no-store'})
 test.assertFalse(result['hasBody'])
 test.assertIsNone(result['body'])
 test.assertEqual(len(result['audits']),1)
 audit=result['audits'][0]
 test.assertEqual((audit['phase'],audit['code'],audit['status']),('rejected','INVALID_AUTHENTICATION',401))
 test.assertEqual(audit['requestId'],result['requestId'])
 test.assertNotIn('SENTINEL_ERROR_DETAIL',json.dumps(result))

CSHARP = r'''
using System;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Named { public string Id; }
public class Url { public string Path="/"; public Dictionary<string,string[]> Query=new Dictionary<string,string[]>(); }
public class Body { public string Data="{}"; public bool Locked; public int Reads; public T As<T>(bool preserveContent=false) {
 Reads++;if(Locked)throw new Exception("SENTINEL_EXTERNAL_BODY_READ");
 if(typeof(T)==typeof(byte[]))return (T)(object)System.Text.Encoding.UTF8.GetBytes(Data);
 if(typeof(T)==typeof(string))return (T)(object)Data;
 return (T)(object)JObject.Parse(Data);
} }
public class Request { public string Method="POST"; public Url Url=new Url(), OriginalUrl=new Url(); public Body Body=new Body(); public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public interface IResponse { int StatusCode {get;} Body Body {get;} Dictionary<string,string[]> Headers {get;} }
public class Response:IResponse { public int StatusCode {get;set;} public Body Body {get;set;} public Dictionary<string,string[]> Headers {get;set;}=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public class Authorization { public string AccessToken="opaque-token"; public Dictionary<string,object> Claims=new Dictionary<string,object>(); }
public class LastError { public string Source="",Reason="",PolicyId=""; }
public class GuardedVariables:Dictionary<string,object> {
 public bool IdentityReadsLocked; public int IdentityReads,DecisionReads;
 private void Read(string key) {
  if(key=="gatewayDecision")DecisionReads++;
  if(IdentityReadsLocked && (key=="gatewayCommonAuthenticationValidated" || key=="gatewayEntraAuthenticationValidated" || key=="gatewayUserAuthentication")) {
   IdentityReads++;throw new Exception("CM repeated authentication identity access");
  }
 }
 public new object this[string key] { get { Read(key);return base[key]; } set { base[key]=value; } }
 public new bool ContainsKey(string key) { Read(key);return base.ContainsKey(key); }
}
public class Context { public int CMCallerCalls,CMSelectionCalls; public Guid RequestId=Guid.NewGuid(); public Request Request=new Request(); public Response Response=new Response(); public LastError LastError=new LastError(); public Named Api=new Named(),Operation=new Named(),Subscription; public GuardedVariables Variables=new GuardedVariables(); }
public static class Extensions { public static string GetValueOrDefault(this Dictionary<string,string[]> d,string k,string fallback){return d.ContainsKey(k)?String.Join(" ",d[k]):fallback;} }
public static class CredentialFixture {
 __METHODS__
 public static string Run(string json) {
  var output=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Api.Id=(string)item["api"];c.Operation.Id=(string)item["operation"];c.Request.Method=(string)item["method"]??"POST";c.Request.OriginalUrl.Path=(string)item["path"]??"/";
   if(item["lastError"] is JObject lastError){c.LastError.Source=(string)lastError["source"];c.LastError.Reason=(string)lastError["reason"];c.LastError.PolicyId=(string)lastError["policyId"];}
   if(item["subscription"]!=null && item["subscription"].Type!=JTokenType.Null)c.Subscription=new Named{Id=(string)item["subscription"]};
   if(item["body"]!=null)c.Request.Body.Data=item["body"].Type==JTokenType.String?(string)item["body"]:item["body"].ToString(Formatting.None);
   foreach(var p in ((JObject)(item["headers"]??new JObject())).Properties())c.Request.Headers[p.Name]=p.Value is JArray?p.Value.Select(v=>(string)v).ToArray():new[]{(string)p.Value};
   foreach(var p in ((JObject)(item["query"]??new JObject())).Properties())c.Request.Url.Query[p.Name]=p.Value is JArray?p.Value.Select(v=>(string)v).ToArray():new[]{(string)p.Value};
   foreach(var p in ((JObject)(item["variables"]??new JObject())).Properties())c.Variables[p.Name]=p.Value.Type==JTokenType.String?(object)(string)p.Value:p.Value.Type==JTokenType.Boolean?(object)(bool)p.Value:p.Value.Type==JTokenType.Integer?(object)(int)p.Value:p.Value.ToString(Formatting.None);
   var responses=item["responses"] as JArray ?? new JArray();if(item["response"] is JObject singleResponse)responses.Add(singleResponse);
   foreach(JObject response in responses){var r=new Response{StatusCode=(int)response["status"],Body=(bool?)response["nullResponseBody"]==true?null:new Body{Locked=(bool?)response["lockedResponseBody"]==true,Data=response["body"].Type==JTokenType.String?(string)response["body"]:response["body"].ToString(Formatting.None)}};foreach(var p in ((JObject)(response["headers"]??new JObject())).Properties())r.Headers[p.Name]=new[]{(string)p.Value};c.Variables[(string)response["name"]]=r;}
   if(item["claims"] is JObject claims){var authorization=new Authorization();foreach(var p in claims.Properties())authorization.Claims[p.Name]=p.Value.Type==JTokenType.Integer?(object)(long)p.Value:p.Value.Type==JTokenType.String?(object)(string)p.Value:(object)p.Value;c.Variables["gatewayConnectionContext"]=authorization;}
   switch((string)item["expression"]) { __SWITCH__ default:throw new Exception("Unknown expression"); }
   if((bool?)item["observeResponseBodies"]==true) {
    var reads=new JObject();
    foreach(JObject response in responses) {
     var name=(string)response["name"];var actual=c.Variables[name] as IResponse;
     reads[name]=actual.Body==null?-1:actual.Body.Reads;
    }
    output[(string)item["id"]+"-bodyReads"]=reads;
   }
  }
  return output.ToString(Formatting.None);
 }
}
'''


class CredentialPolicyTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.login=settings()
  cls.roots={name:ET.fromstring(credential_login.render(name+".xml",cls.login)) for name in {**credential_login.OPERATIONS,**credential_login.BROWSER_OPERATIONS}}
  for name,filename in {"connection":"credential-connection.xml","session-use":"credential-session-use.xml","connection-outbound":"credential-connection-outbound.xml","challenge":"credential-challenge.xml"}.items():
   cls.roots[name]=ET.fromstring(credential_login.render_connection(filename,cls.login))

 def test_state_is_owned_by_broker_and_no_policy_cache_is_used(self):
  for name,root in self.roots.items():
   for tag in ("cache-lookup-value","cache-store-value","cache-remove-value"):
    self.assertEqual(root.findall(".//"+tag),[],name)
   self.assertNotIn("management.azure.com",ET.tostring(root,encoding="unicode"))

 def test_proxy_replaces_trusted_headers_and_uses_fixed_backend(self):
  for name,path in {"start":"/v1/start","status":"/v1/status","logout":"/v1/logout","session-commit":"/v2/session/commit","logout-all":"/v2/session/logout-all","disconnect":"/v2/session/disconnect","launch-post":"/v1/launch","identity-callback":"/v1/identity-callback"}.items():
   with self.subTest(operation=name):
    root=self.roots[name]
    self.assertEqual(root.find("inbound/set-backend-service").get("backend-id"),"credential-broker")
    self.assertEqual(root.find("inbound/rewrite-uri").get("template"),path)
    for header in ("X-Core-Broker-Key","X-Core-Subscription-Id"):
     node=root.find("inbound/set-header[@name='"+header+"']")
     self.assertTrue(node.get("exists-action")=="override" or node.get("exists-action").startswith("@"))
     self.assertNotIn("context.Request",node.find("value").text)
    self.assertIsNotNone(root.find("inbound/set-header[@name='api-key'][@exists-action='delete']"))

 def test_callback_keeps_cm_token_inside_apim_and_confirms_expected_owner(self):
  root=self.roots["callback"]
  steps=list(root.find("inbound"))
  context=next(n for n in steps if n.get("response-variable-name")=="credentialContextResponse")
  token=next(n for n in steps if n.tag=="get-authorization-context")
  identity=next(n for n in steps if n.get("response-variable-name")=="credentialIdentityResponse")
  complete=next(n for n in steps if n.get("response-variable-name")=="credentialCompleteResponse")
  self.assertLess(steps.index(context),steps.index(token));self.assertLess(steps.index(token),steps.index(identity));self.assertLess(steps.index(identity),steps.index(complete))
  self.assertEqual(token.get("identity-type"),"managed")
  self.assertNotIn("context.Request",token.get("authorization-id"))
  self.assertNotIn("AccessToken",ET.tostring(complete,encoding="unicode"))
  self.assertIn("verifiedOwnerId",complete.find("set-body").text)

 def test_browser_callbacks_are_narrowly_anonymous(self):
  value=ET.fromstring(credential_login.anonymous_policy(self.login)).get("condition")
  self.assertIn('context.Api.Id == "credential-manager-browser"',value)
  self.assertIn('context.Operation.Id == "identity-callback"',value)
  for name in ("start","status","logout"):
   self.assertNotIn('context.Operation.Id == "'+name+'"',value)
  # ブラウザー取引と管理操作は、モデルAPI用の設定入力を追加せず共通認証を継承します。
  for name in {**credential_login.OPERATIONS,**credential_login.BROWSER_OPERATIONS}:
   root=self.roots[name]
   with self.subTest(operation=name):
    self.assertIsNotNone(root.find('inbound/base'))
    self.assertIsNone(root.find('.//set-variable[@name="gatewayApiSettingsSource"]'))
    self.assertIsNone(root.find('.//include-fragment[@fragment-id="core-api-settings"]'))

 def test_result_pages_are_static_and_explain_closing_the_window(self):
  for response in self.roots["callback"].findall(".//return-response"):
   body=response.find("set-body").text
   self.assertIn("この画面は閉じてかまいません。",body)
   self.assertNotIn("<script",body)
   self.assertNotIn("pollSecret",body)
   import re
   css=re.search(r"<style>(.*?)</style>",body).group(1)
   self.assertIn("sha256-"+digest(css),response.find("set-header[@name='Content-Security-Policy']/value").text)

 def test_responses_obey_apim_policy_order(self):
  ranks={"set-status":0,"set-header":1,"set-body":2}
  for root in self.roots.values():
   for response in root.iter("return-response"):
    self.assertEqual([ranks[n.tag] for n in response],sorted(ranks[n.tag] for n in response))

 def test_business_use_is_a_separate_step_after_identity_proof(self):
  connection=self.roots['connection'];use=self.roots['session-use']
  self.assertEqual(connection.find(".//send-request[@response-variable-name='gatewayConnectionSessionResponse']/set-url").text,
                   '{{core-credential-broker-url}}/v2/session/authorize')
  self.assertIsNotNone(connection.find(".//send-request[@response-variable-name='gatewayConnectionIdentityResponse']"))
  self.assertIsNone(connection.find(".//send-request[@response-variable-name='gatewayConnectionUseResponse']"))
  call=use.find(".//send-request[@response-variable-name='gatewayConnectionUseResponse']")
  self.assertEqual(call.find('set-url').text,'{{core-credential-broker-url}}/v2/session/use')
  body=call.find('set-body').text
  self.assertIn('selection["ownerId"]',body)
  self.assertIn('context.Api.Id',body);self.assertIn('context.Operation.Id',body)
  self.assertNotIn('context.Request.Headers',body)
  gateway=ET.fromstring((CORE/'apps/apim/policies/api-templates/rest/rest.xml').read_text(encoding='utf-8'))
  parent=gateway.find('inbound')
  self.assertEqual([node.get('fragment-id') for node in parent.findall('include-fragment')
                   if node.get('fragment-id').startswith('core-credential-')],
                   ['core-credential-connection','core-credential-session-use'])

 def test_only_verified_gateway_headers_are_relayed_and_no_business_body_is_rewritten(self):
  outbound=self.roots['connection-outbound']
  names=('X-Core-Connection-Update','X-Core-Connection-Update-Id','X-Core-Connection-Generation')
  for name in names:
   self.assertIsNotNone(outbound.find("set-header[@name='"+name+"'][@exists-action='delete']"))
   override=outbound.find(".//set-header[@name='"+name+"'][@exists-action='override']")
   self.assertIn('gatewayConnectionUseResult',override.find('value').text)
  self.assertIsNone(outbound.find('set-body'))

class CredentialExpressionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  CredentialPolicyTests.setUpClass()
  cls.roots=dict(CredentialPolicyTests.roots)
  cls.roots["telemetry"]=ET.fromstring(render_audit_policy(policy_fixture_path(CORE/"apps/apim/policies","credential-telemetry.xml").read_text(encoding="utf-8")))
  policies=CORE/"apps/apim/policies"
  cls.roots["caller-error"]=ET.parse(policies/"snippets/authentication/credential-caller-error/credential-caller-error.xml").getroot()
  cls.roots["common-telemetry"]=ET.fromstring(render_audit_policy(policy_fixture_path(policies,"core-gateway-telemetry.xml").read_text(encoding="utf-8")))
  cls.login=CredentialPolicyTests.login
  pwsh=os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
  if not pwsh:raise unittest.SkipTest("PowerShell Roslyn is required")
  methods=[];switches=[];counts={};retry_methods={};caller_methods={};selection_methods={};compiled={}
  for filename,root in cls.roots.items():
   parents={child:parent for parent in root.iter() for child in parent}
   for node in root.iter():
    expressions=[("attribute",v) for v in node.attrib.values() if v.startswith("@")]
    if node.text and node.text.strip().startswith("@"):
     expressions.append(("text",node.text.strip()))
    for kind,value in expressions:
     method="Expression"+str(len(methods))
     count='context.CMCallerCalls++;' if filename=='connection' and node.tag=='set-variable' and node.get('name')=='credentialCallerId' else ''
     if filename=='connection' and node.tag=='set-variable' and node.get('name')=='gatewayConnectionSelection' and 'connection' not in selection_methods:
      count='context.CMSelectionCalls++;'
     methods.append("static object "+method+"(Context context) { "+count+expression(value)+" }")
     compiled[(node,kind,value)]=method
     if node.tag=="set-variable" and kind=="attribute" and value==node.get("value"):
      name=filename+":"+node.get("name");number=counts.get(name,0);counts[name]=number+1
      if node.get("name")=="credentialCallerId":caller_methods[filename]=method
      if filename=="connection" and node.get("name")=="gatewayConnectionSelection" and number==0:selection_methods[filename]=method
      prepare=''
      if filename=="connection" and node.get("name")=="gatewayConnectionSelection" and number==1:
       prepare+='c.Variables["gatewayConnectionProviderSettings"]='+json.dumps(root.find(".//set-variable[@name='gatewayConnectionProviderSettings']").get("value"))+';'
      if not (filename=="connection" and node.get("name")=="gatewayConnectionSelection" and number==0):
       switches.append('case '+json.dumps(name+":"+str(number))+':'+prepare+'output[(string)item["id"]]=JToken.FromObject('+method+'(c));break;')
     if filename=="start" and node.tag=="set-header" and node.get("name") in ("X-Core-Caller-Id","X-Core-Subscription-Id") and kind=="attribute":
      switches.append('case '+json.dumps('caller-action:'+node.get("name"))+':output[(string)item["id"]]=JToken.FromObject('+method+'(c));break;')
     if filename=="telemetry" and node.tag=="message" and kind=="text":
      switches.append('case "credential-telemetry":output[(string)item["id"]]=JToken.FromObject('+method+'(c));break;')
     if filename in ("start","challenge"):
      if node.tag=="set-variable" and node.get("name")==("credentialProxyRetryAfter" if filename=="start" else "gatewayConnectionChallengeRetryAfter"):
       retry_methods[(filename,"seconds")]=method
      if node.tag=="set-header" and node.get("name")=="Retry-After" and kind=="attribute":
       retry_methods[(filename,"action")]=method
      parent=parents.get(node)
      if node.tag=="value" and parent is not None and parent.tag=="set-header" and parent.get("name")=="Retry-After":
       retry_methods[(filename,"header")]=method
  # CMの実入口からAPI側の実500応答までをつなぎ、外部処理の条件は親から短絡評価します。
  connection=cls.roots['connection'];parents={child:parent for parent in connection.iter() for child in parent}
  def actual(node,attribute=None):
   value=node.get(attribute) if attribute else node.text.strip()
   return compiled[(node,'attribute' if attribute else 'text',value)]+'(context)' if value.startswith('@') else json.dumps(value)
  external=[]
  for label,node in (('broker',connection.find(".//send-request[@response-variable-name='gatewayConnectionSessionResponse']")),
                     ('cm',connection.find('.//get-authorization-context')),
                     ('graph',connection.find(".//send-request[@response-variable-name='gatewayConnectionIdentityResponse']"))):
   conditions=[];parent=parents[node]
   while parent is not connection:
    if parent.tag=='when':conditions.append('(bool)'+actual(parent,'condition'))
    parent=parents[parent]
   external.append('new JProperty('+json.dumps(label)+','+' && '.join(reversed(conditions))+')')
  rejected=cls.roots['caller-error'].find("choose[@id='core-credential-caller-required']/when")
  response=rejected.find('return-response')
  audit_assignments=''.join('context.Variables['+json.dumps(node.get('name'))+']='+actual(node,'value')+';' for node in rejected.findall('set-variable'))
  audit_message=cls.roots['common-telemetry'].find('trace/message')
  headers=','.join('new JProperty('+json.dumps(node.get('name'))+','+actual(node.find('value'))+')' for node in response.findall('set-header'))
  methods.append('static JObject ConnectionEntry(Context context) { '
   'var decisionReadsBefore=context.Variables.DecisionReads;'
   'context.Variables["credentialCallerId"]='+caller_methods['connection']+'(context);'
   'context.Variables["gatewayConnectionSelection"]='+selection_methods['connection']+'(context);'
   'context.Variables["gatewayConnectionRequestSession"]='+actual(connection.find("set-variable[@name='gatewayConnectionRequestSession']"),'value')+';'
   'var flow=new JObject(new JProperty("callerId",(string)context.Variables["credentialCallerId"]),'
   'new JProperty("selection",JObject.Parse((string)context.Variables["gatewayConnectionSelection"])),'
   'new JProperty("initialExternalGuards",new JObject('+','.join(external)+')),'
   'new JProperty("guardRejected",(bool)'+actual(rejected,'condition')+'),new JProperty("postGuardReached",false));'
   'flow["cmCallerCalls"]=context.CMCallerCalls;flow["cmSelectionCalls"]=context.CMSelectionCalls;'
   'flow["cmIdentityReads"]=context.Variables.IdentityReads;flow["cmDecisionReads"]=context.Variables.DecisionReads-decisionReadsBefore;'
   'if((bool)flow["guardRejected"]) { '
   # 読取り回数は監査より前で確定します。監査自身の既存主体観測は別の処理です。
   'var wasLocked=context.Variables.IdentityReadsLocked;context.Variables.IdentityReadsLocked=false;'+audit_assignments+
   'flow["audit"]=JObject.Parse((string)'+actual(audit_message)+');context.Variables.IdentityReadsLocked=wasLocked;'
   'flow["status"]='+str(int(response.find('set-status').get('code')))+';'
   'flow["reason"]=JToken.FromObject('+actual(response.find('set-status'),'reason')+');'
   'flow["headers"]=new JObject('+headers+');flow["requestId"]=context.RequestId.ToString();'
   'flow["hasBody"]='+str(response.find('set-body') is not None).lower()+';return flow; }'
   'flow["postGuardReached"]=true;return flow; }')
  switches.append('case "connection:gatewayConnectionSelection:0":output[(string)item["id"]]=ConnectionEntry(c);break;')
  switches.append('case "connection:caller-error":output[(string)item["id"]]=JToken.FromObject('+actual(rejected,'condition').replace('(context)','(c)')+');break;')
  # CM成功の合成応答を用い、Broker選択とGraph結果がSQL利用確認の入口へ渡る実条件をつなぎます。
  broker=connection.find(".//send-request[@response-variable-name='gatewayConnectionSessionResponse']")
  broker_gate=parents[broker]
  cm=connection.find('.//get-authorization-context');cm_gate=parents[cm]
  cm_token_failure=cm_gate.find('choose/when')
  graph=connection.find(".//send-request[@response-variable-name='gatewayConnectionIdentityResponse']")
  graph_gate=parents[graph]
  identity_status=connection.find(".//set-variable[@name='gatewayConnectionIdentityStatus']")
  identity_rejected=next(node for node in connection.findall('.//choose/when') if 'gatewayConnectionIdentityStatus' in node.get('condition','') and '!= 200' in node.get('condition'))
  use=cls.roots['session-use'];use_gate=use.find('choose/when')
  use_request=use.find(".//send-request[@response-variable-name='gatewayConnectionUseResponse']")
  broker_selection=connection.findall(".//set-variable[@name='gatewayConnectionSelection']")[1]
  provider_settings=connection.find(".//set-variable[@name='gatewayConnectionProviderSettings']")
  methods.append('static JObject GraphUseEligibility(Context context,JObject item) { '
   'var flow=ConnectionEntry(context);flow["graphProcessed"]=false;flow["sqlUseEligible"]=false;'
   'if((bool)flow["guardRejected"])return flow;'
   'if(item.Property("sessionHeaderAfter")!=null) { if(item["sessionHeaderAfter"].Type==JTokenType.Null)context.Request.Headers.Remove("X-Core-Connection-Session");'
   'else context.Request.Headers["X-Core-Connection-Session"]=new[]{(string)item["sessionHeaderAfter"]}; }'
   'flow["savedSession"]=(string)context.Variables["gatewayConnectionRequestSession"];'
   'if((bool)'+actual(broker_gate,'condition')+') { '
   'flow["authorizeRequest"]=JObject.Parse((string)'+actual(broker.find('set-body'))+');'
   'context.Variables["gatewayConnectionProviderSettings"]='+actual(provider_settings,'value')+';'
   'context.Variables["gatewayConnectionSelection"]='+actual(broker_selection,'value')+'; }'
   'if((bool)'+actual(cm_gate,'condition')+') { '
   'if((bool)'+actual(cm_token_failure,'condition')+')throw new Exception("This case requires successful CM acquisition");'
   'if((bool)'+actual(graph_gate,'condition')+') { flow["graphProcessed"]=true;'
   'context.Variables["gatewayConnectionIdentityStatus"]='+actual(identity_status,'value')+';'
   'flow["identityStatus"]=JToken.FromObject(context.Variables["gatewayConnectionIdentityStatus"]);'
   'if((bool)'+actual(identity_rejected,'condition')+')context.Variables["gatewayConnectionSelection"]='+actual(identity_rejected.find("set-variable[@name='gatewayConnectionSelection']"),'value')+'; } }'
   'flow["selectedConnection"]=JObject.Parse((string)context.Variables["gatewayConnectionSelection"]);'
   'flow["sqlUseEligible"]=(bool)'+actual(use_gate,'condition')+';'
   'if((bool)flow["sqlUseEligible"])flow["useRequest"]=JObject.Parse((string)'+actual(use_request.find('set-body'))+');'
   'return flow; }')
  switches.append('case "connection:graph-use":output[(string)item["id"]]=GraphUseEligibility(c,item);break;')
  # 秒の検証から実header式までを同じcontextで動かし、固定値の代用品にしません。
  for policy,variable in (("start","credentialProxyRetryAfter"),("challenge","gatewayConnectionChallengeRetryAfter")):
   if not all((policy,part) in retry_methods for part in ("seconds","action","header")):
    raise AssertionError("Rate error response must expose its actual Retry-After expressions")
   switches.append('case "retry-'+policy+'":c.Variables['+json.dumps(variable)+']='+retry_methods[(policy,"seconds")]+'(c);var retryAction'+policy+'=(string)'+retry_methods[(policy,"action")]+'(c);output[(string)item["id"]]=new JObject(new JProperty("action",retryAction'+policy+'),new JProperty("header",retryAction'+policy+'=="override"?(string)'+retry_methods[(policy,"header")]+'(c):null));break;')
  # HTTP/外部MCPの実引渡し式と直後の実拒否条件を、CMの実caller/selection式へ接続します。
  cls.handoff_policies={name:ET.fromstring(render_audit_policy(policy_fixture_path(policies, filename).read_text(encoding='utf-8')))
                        for name,filename in (('http','rest.xml'),('mcp','stream.xml'))}
  http_handoff=(CORE/'apps/apim/policies/fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs').read_text(encoding='utf-8')
  for name,root in cls.handoff_policies.items():
   handoff='AuthenticationHandoff'+name
   source=http_handoff if name=='http' else expression(root.find("inbound/set-variable[@name='gatewayDecision']").get('value'))
   methods.append('static object '+handoff+'(Context context) { '+source+' }')
   rejected=next(node for node in root.findall('inbound/choose/when') if 'gatewayDecision' in node.get('condition','') and '["allowed"]' in node.get('condition',''))
   failure='AuthenticationHandoffRejected'+name
   methods.append('static object '+failure+'(Context context) { '+expression(rejected.get('condition'))+' }')
   status=int(rejected.find('return-response/set-status').get('code'))
   code=rejected.find("set-variable[@name='gatewayAuditCode']").get('value')
   phase=rejected.find("set-variable[@name='gatewayAuditPhase']").get('value')
   switches.append('case "handoff-'+name+'": { '
    'var handed=(string)'+handoff+'(c);c.Variables["gatewayDecision"]=handed;'
    'var flow=new JObject(new JProperty("decision",JObject.Parse(handed)),new JProperty("cmReached",false));'
    'if((bool)'+failure+'(c)) { flow["status"]='+str(status)+';flow["code"]='+json.dumps(code)+';flow["phase"]='+json.dumps(phase)+';'
    'flow["cmIdentityReads"]=c.Variables.IdentityReads;flow["cmDecisionReads"]=0;flow["cmCallerCalls"]=c.CMCallerCalls;output[(string)item["id"]]=flow;break; }'
    'if(item["afterHandoffVariables"] is JObject after)foreach(var p in after.Properties())c.Variables[p.Name]=p.Value.Type==JTokenType.String?(object)(string)p.Value:p.Value.Type==JTokenType.Boolean?(object)(bool)p.Value:p.Value.ToString(Formatting.None);'
    'c.Variables.IdentityReadsLocked=true;'
    'flow["cmReached"]=true;flow.Merge(ConnectionEntry(c));'
    'output[(string)item["id"]]=flow;break; }')
  for operation in credential_login.OPERATIONS:
   for enabled in (True,False):
    method='CredentialError'+operation.replace('-','')+('Enabled' if enabled else 'Disabled')
    xml=credential_login.render(operation+'.xml',cls.login,entra_enabled=enabled)
    methods.append(operation_error_methods(xml,method,entra_enabled=enabled))
    switches.append('case '+json.dumps('operation-error:'+operation+':'+str(enabled))+':output[(string)item["id"]]='+method+'(c);break;')
  cases=[]
  def add(name,expr,**values):
   subscription=values.pop("subscription","sub-a")
   variables={"gatewayCommonAuthenticationValidated":True,"credentialCallerId":"subscription:"+(subscription or ""),**values.pop("variables",{})}
   cases.append({"id":name,"expression":expr,"subscription":subscription,"variables":variables,**values})
  for operation in credential_login.OPERATIONS:
   for label,source,reason in (("missing","authorization","SubscriptionKeyNotFound"),("invalid","authorization","SubscriptionKeyInvalid"),("limited","rate-limit-by-key","RateLimitExceeded"),("backend","forward-request","BackendConnectionFailure"),("expression","set-variable","ExpressionValueEvaluationFailure")):
    add("proxy-error-"+operation+"-"+label,operation+":credentialProxyErrorStatus:0",lastError={"source":source,"reason":reason})
   for label,last_error in operation_error_cases():
    add('operation-error-'+operation+'-'+label,'operation-error:'+operation+':True',lastError=last_error,body='SENTINEL_ERROR_DETAIL')
   add('operation-error-'+operation+'-disabled','operation-error:'+operation+':False',lastError=operation_error_cases()[0][1])
  # 境界・欠落・注入を代表させ、正常429と他statusのheader有無を分けます。
  for label,retry in (("minimum","1"),("maximum","300"),("missing",None),("invalid","2\r\nInjected: value")):
   for policy in ("start","challenge"):
    variables={"credentialProxyErrorStatus":429} if policy=="start" else {"gatewayAuditStatus":429,"gatewayConnectionChallengeResult":{"status":429}}
    if policy=="start" and retry is not None:variables["credentialLoginStartRetryAfter"]=retry
    response={"response":{"name":"gatewayConnectionChallengeResponse","status":429,"body":{},"headers":{} if retry is None else {"Retry-After":retry}}} if policy=="challenge" else {}
    add("retry-"+policy+"-"+label,"retry-"+policy,variables=variables,**response)
  for policy,statuses in (("start",(401,503)),("challenge",(403,409,503))):
   for status in statuses:
    variables={"credentialProxyErrorStatus":status,"credentialLoginStartRetryAfter":"300"} if policy=="start" else {"gatewayAuditStatus":status,"gatewayConnectionChallengeResult":{"status":status}}
    response={"response":{"name":"gatewayConnectionChallengeResponse","status":429,"body":{},"headers":{"Retry-After":"300"}}} if policy=="challenge" else {}
    add("retry-"+policy+"-status-"+str(status),"retry-"+policy,variables=variables,**response)
  decision={"allowed":True,"method":"subscriptionKey","callerId":"subscription:sub-a"}
  config={"enabled":True,"mode":"personal","providerName":"graph-delegated"}
  def preliminary(name,**changes):
   selected=changes.pop("config",config)
   variables={"gatewayDecision":changes.pop("decision",decision),"gatewayCredentialConnection":credential_login.encode(selected),**changes.pop("variables",{})}
   if changes.pop('missingDecision',False):variables.pop('gatewayDecision',None)
   add(name,"connection:gatewayConnectionSelection:0",headers=changes.pop("headers",{"X-Core-Connection-Session":SESSION}),variables=variables,**changes)
  preliminary("pre-valid");preliminary("pre-no-session",headers={});preliminary("pre-multi-session",headers={"X-Core-Connection-Session":[SESSION]*2});preliminary("pre-old-format",headers={"X-Core-Connection-Session":TX+"."+SECRET})
  preliminary("pre-malformed",headers={"X-Core-Connection-Session":"bad"});preliminary("pre-subscription-b",subscription="sub-b",decision={**decision,"callerId":"subscription:sub-b"})
  preliminary("pre-missing-caller",decision={"allowed":True,"method":"subscriptionKey"});preliminary("pre-disabled",config={**config,"enabled":False})
  preliminary("pre-missing-decision",missingDecision=True);preliminary("pre-malformed-decision",decision="{")
  preliminary("pre-nonobject-decision",decision=[])
  preliminary("pre-null-caller",decision={**decision,"callerId":None});preliminary("pre-empty-caller",decision={**decision,"callerId":""})
  for status in (200,403,409,503):
   add('caller-error-status-'+str(status),'connection:caller-error',variables={"gatewayConnectionSelection":{"allowed":status==200,"status":status}})
  caller="user:"+TENANT+":"+OWNER
  user={"schemaVersion":1,"callerId":caller,"allocationId":caller,"tenantId":TENANT,"principalId":OWNER,"clientId":""}
  user_variables={"gatewayEntraAuthenticationValidated":True,"gatewayUserAuthentication":user}
  user_decision={"allowed":True,"method":"user","callerId":caller,"allocationId":caller}
  audit={"gatewayAuditPhase":"rejected","gatewayAuditCode":"CREDENTIAL_CONNECTION_DENIED","gatewayAuditStatus":403,"gatewayAuditSettings":{"backendService":"graph","backendAuthentication":"credentialManager","measurementRole":"entry","serviceClass":"platform"}}
  for label,values,subscription in (
   ("user",{**user_variables,"credentialCallerId":caller,"gatewayDecision":user_decision},None),
   ("key",{"gatewayDecision":decision},"sub-a"),
   ("mismatch",{**user_variables,"credentialCallerId":caller,"gatewayDecision":{**user_decision,"callerId":"user:"+TENANT+":"+TENANT}},None),
  ):
   add("audit-"+label,"credential-telemetry",subscription=subscription,api="graph-me",operation="get-me",variables={**audit,**values})
  preliminary("pre-user-valid",subscription=None,decision=user_decision,variables=user_variables)
  broken_legacy={"gatewayCommonAuthenticationValidated":False,"gatewayEntraAuthenticationValidated":"invalid","gatewayUserAuthentication":"{"}
  preliminary("pre-user-legacy-fields-broken",subscription=None,decision=user_decision,variables=broken_legacy)
  preliminary("pre-user-marker-ignored",subscription=None,decision=user_decision,variables={**user_variables,"gatewayEntraAuthenticationValidated":False})
  preliminary("pre-user-stale-json",subscription=None,decision=user_decision,variables={**user_variables,"gatewayUserAuthentication":{**user,"schemaVersion":99,"callerId":"user:"+TENANT+":"+TENANT,"allocationId":"stale"}})
  for label,variables in (
   ("key",{"gatewayDecision":decision}),
   ("user",{**user_variables,"gatewayDecision":user_decision}),
   ("legacy-markers",{**broken_legacy,"gatewayDecision":user_decision}),
   ("missing-decision",{}), ("malformed-decision",{"gatewayDecision":"{"}),
   ("non-string-decision",{"gatewayDecision":False}), ("missing-caller",{"gatewayDecision":{"allowed":True}}),
  ):
   add("caller-"+label,"connection:credentialCallerId:0",subscription=None if label in ("user","legacy-markers") else "sub-a",variables=variables)
  # 親の引渡し成功後に旧情報を壊し、CMが読まないことをアクセス回数と実式で確認します。
  for protocol in ('http','mcp'):
   handoff_variables={"gatewayOperationSelection":{"settingId":""},"gatewayCredentialConnection":credential_login.encode(config)}
   for label,subscription,identity,after in (
    ('key','sub-a',{},broken_legacy), ('user',None,user_variables,broken_legacy),
    ('user-unvalidated',None,{**user_variables,"gatewayEntraAuthenticationValidated":False},{}),
    ('user-malformed',None,{**user_variables,"gatewayUserAuthentication":"{"},{}),
    ('caller-lost',None,user_variables,{"gatewayDecision":{"allowed":True,"method":"user","callerId":None}}),
   ):
    add('handoff-'+protocol+'-'+label,'handoff-'+protocol,subscription=subscription,
        variables={**handoff_variables,**identity},afterHandoffVariables=after,headers={"X-Core-Connection-Session":SESSION})
  # start/statusは保護操作の親base-only経路。業務API用decisionを認可元に使いません。
  protected_identity={"gatewayDecision":"INVALID_BUSINESS_DECISION_MUST_NOT_BE_READ"}
  for policy in ('start','status','session-commit','logout','logout-all','disconnect'):
   for label,subscription,extra,expected in (
    ('key','sub-a',{},'subscription:sub-a'),
    ('other-key','sub-b',{},'subscription:sub-b'),
    ('user',None,user_variables,caller),
    ('unvalidated-user',None,{**user_variables,"gatewayEntraAuthenticationValidated":False},''),
    ('common-failed',None,{**user_variables,"gatewayCommonAuthenticationValidated":False},''),
    ('unexpected-subscription','sub-a',user_variables,''),
    ('bad-json',None,{**user_variables,"gatewayUserAuthentication":"{"},''),
    ('missing-caller',None,{**user_variables,"gatewayUserAuthentication":{}},''),
    ('null-caller',None,{**user_variables,"gatewayUserAuthentication":{**user,"callerId":None}},''),
    ('empty-caller',None,{**user_variables,"gatewayUserAuthentication":{**user,"callerId":""}},''),
   ):
    add(policy+'-caller-'+label,policy+':credentialCallerId:0',subscription=subscription,variables={**protected_identity,**extra})
   for field,value in (('schemaVersion',99),('tenantId','invalid'),('principalId','invalid'),('allocationId','stale')):
    add(policy+'-caller-legacy-'+field,policy+':credentialCallerId:0',subscription=None,
        variables={**protected_identity,**user_variables,"gatewayUserAuthentication":{**user,field:value}})
  for label,identity in (("key","subscription:sub-a"),("user",caller)):
   for header in ("X-Core-Caller-Id","X-Core-Subscription-Id"):
    add("caller-header-"+label+"-"+header,"caller-action:"+header,variables={"credentialCallerId":identity})
  selected={"allowed":True,"providerName":"graph-delegated-a","connectionId":"test.user-abcdef0123456789","ownerId":OWNER,"tenantId":TENANT,"identityEndpoint":"https://graph.microsoft.com/v1.0/me","identityIdProperty":"id"}
  def graph_use(name,body=None,status=200,subscription="sub-a",broker_status=200,selected_config=None,**values):
   add(name,'connection:graph-use',subscription=subscription,api='graph-me',operation='get-me',claims={},
       headers={"X-Core-Connection-Session":SESSION},
       variables={"gatewayDecision":user_decision if subscription is None else decision,
                  "gatewayCredentialConnection":credential_login.encode(selected_config or config)},
       responses=[{"name":"gatewayConnectionSessionResponse","status":broker_status,"body":selected},
                  {"name":"gatewayConnectionIdentityResponse","status":status,"body":{"id":OWNER} if body is None else body}],**values)
  graph_use('graph-use-key',sessionHeaderAfter='cs2.'+SECRET+'.'+STATE)
  graph_use('graph-use-user',subscription=None,sessionHeaderAfter=None)
  graph_use('graph-use-owner-mismatch',body={"id":TENANT})
  graph_use('graph-use-reauth',body={},status=401)
  graph_use('graph-use-unavailable',body={},status=503)
  graph_use('graph-use-broker-forbidden',broker_status=403)
  graph_use('graph-use-service',selected_config={**config,"mode":"service","connectionName":"service-fixture"})
  def selection(name,body=None,status=200,profile="graph-delegated",expr="connection:gatewayConnectionSelection:1"):
   add(name,expr,variables={"gatewayConnectionSelection":{"profileName":profile}},response={"name":"gatewayConnectionSessionResponse","status":status,"body":selected if body is None else body})
  add("selection-user-valid","connection:gatewayConnectionSelection:1",subscription=None,variables={"credentialCallerId":caller,"gatewayConnectionSelection":{"profileName":"graph-delegated"}},response={"name":"gatewayConnectionSessionResponse","status":200,"body":selected})
  add("selection-user-wrong-owner","connection:gatewayConnectionSelection:1",subscription=None,variables={"credentialCallerId":"user:"+TENANT+":"+TENANT,"gatewayConnectionSelection":{"profileName":"graph-delegated"}},response={"name":"gatewayConnectionSessionResponse","status":200,"body":selected})
  selection("selection-valid");selection("selection-forbidden",status=403);selection("selection-required",status=409);selection("selection-unavailable",status=500)
  for status in (200,403,409,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    selection('selection-body-'+body_mode+'-'+str(status),status=status)
    cases[-1]['response'][body_mode+'ResponseBody']=True
    cases[-1]['observeResponseBodies']=True
  add('selection-missing-response','connection:gatewayConnectionSelection:1',variables={"gatewayConnectionSelection":{"profileName":"graph-delegated"}})
  for response_name,statuses in (('broker',(403,409)),('graph',(401,403,503))):
   for status in statuses:
    graph_use('graph-use-null-'+response_name+'-'+str(status),**({'broker_status':status} if response_name=='broker' else {'status':status}))
    cases[-1]['responses'][0 if response_name=='broker' else 1]['nullResponseBody']=True
    cases[-1]['observeResponseBodies']=True
  for field,value in {"providerName":"other-provider","tenantId":OWNER,"ownerId":"not-guid","identityEndpoint":"https://attacker.example/me","identityIdProperty":"other-id","connectionId":"../../other","allowed":False}.items():selection("selection-bad-"+field,{**selected,field:value})
  selection("selection-bad-profile",profile="other-profile")
  # 構築時に埋め込む設定を変え、候補なし・重複・不正要素も実際のC#式で確認します。
  for label,providers in (
   ("empty",[]),
   ("duplicate",cls.login["providers"]*2),
   ("malformed",cls.login["providers"]+["not-a-provider"]),
   ("other-source",[{**cls.login["providers"][0],"sourceName":"other-profile"}]),
   ("unique",[{**cls.login["providers"][0],"name":"other-provider"}]+cls.login["providers"]),
  ):
   login={**cls.login,"providers":providers}
   root=ET.fromstring(credential_login.render_connection("credential-connection.xml",login))
   value=root.findall(".//set-variable[@name='gatewayConnectionSelection']")[1].get("value")
   method="Expression"+str(len(methods));expr="selection-pool:"+label
   methods.append("static object "+method+"(Context context) { "+expression(value)+" }")
   provider_settings=root.find(".//set-variable[@name='gatewayConnectionProviderSettings']").get("value")
   switches.append('case '+json.dumps(expr)+':c.Variables["gatewayConnectionProviderSettings"]='+json.dumps(provider_settings)+';output[(string)item["id"]]=JToken.FromObject('+method+'(c));break;')
   selection("selection-pool-"+label,expr=expr)
  challenge={"status":"need_login","protocolVersion":2,"provisionalConnectionSession":SESSION,"transactionId":TX,"pollSecret":SECRET,"expiresIn":600,
   "loginUrl":"https://gateway.example.com/auth/connections/browser/launch#transactionId="+TX+"&launch="+LAUNCH,
   "statusUrl":"https://gateway.example.com/auth/connections/status?transactionId="+TX}
  def challenge_result(name,body=None,status=200):
   add(name,"challenge:gatewayConnectionChallengeResult:0",response={"name":"gatewayConnectionChallengeResponse","status":status,"body":challenge if body is None else body})
  challenge_result("challenge-valid");challenge_result("challenge-extra",{**challenge,"accessToken":"must-not-leave-broker"})
  for status in (403,429,500):challenge_result("challenge-http-"+str(status),status=status)
  for status in (200,403,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    challenge_result('challenge-body-'+body_mode+'-'+str(status),status=status)
    cases[-1]['response'][body_mode+'ResponseBody']=True
    cases[-1]['observeResponseBodies']=True
  add('challenge-missing-response','challenge:gatewayConnectionChallengeResult:0')
  for field,value in {"status":"complete","protocolVersion":1,"provisionalConnectionSession":TX+"."+SECRET,"transactionId":"bad","pollSecret":"bad","expiresIn":601,"loginUrl":"https://attacker.example/auth","statusUrl":"https://attacker.example/status"}.items():challenge_result("challenge-bad-"+field,{**challenge,field:value})
  challenge_result("challenge-extra-query",{**challenge,"loginUrl":challenge["loginUrl"]+"&pollSecret="+SECRET})
  challenge_result("challenge-long",{**challenge,"extra":"x"*16384})
  for name,body,status in (("connection-owner-valid",{"id":OWNER},200),("connection-owner-mismatch",{"id":TENANT},200),("connection-owner-reauth",{},401),("connection-owner-forbidden",{},403),("connection-owner-unavailable",{},500)):
   add(name,"connection:gatewayConnectionIdentityStatus:0",variables={"gatewayConnectionSelection":selected},response={"name":"gatewayConnectionIdentityResponse","status":status,"body":body})
  for status in (200,401,403,409,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    add('identity-body-'+body_mode+'-'+str(status),'connection:gatewayConnectionIdentityStatus:0',
        variables={"gatewayConnectionSelection":selected},observeResponseBodies=True,
        response={"name":"gatewayConnectionIdentityResponse","status":status,"body":{},body_mode+'ResponseBody':True})
  add('identity-missing-response','connection:gatewayConnectionIdentityStatus:0',variables={"gatewayConnectionSelection":selected})
  for name,body,status in (("cm-confirmed-reauth",{"status":"reauthentication_required","reauthenticationRequired":True},200),
                           ("cm-still-connected",{"status":"unavailable","reauthenticationRequired":False},200),
                           ("cm-unknown",{},503)):
   add(name,"connection:gatewayConnectionSelection:2",variables={"gatewayConnectionSelection":selected},response={"name":"gatewayConnectionStateResponse","status":status,"body":body})
  use={"allowed":True,"status":200,"protocolVersion":2,"generation":4,"idleExpiresAt":1794000000,"absoluteExpiresAt":1795000000,"nextRotationAt":1793500000}
  rotation={"rotationId":LAUNCH,"candidateSession":"cs2."+TX+"."+STATE,"fromGeneration":4,"generation":5,"expiresAt":1793400000}
  def use_result(name,body,status=200):
   add(name,"session-use:gatewayConnectionUseResult:0",variables={"gatewayConnectionRequestSession":SESSION},response={"name":"gatewayConnectionUseResponse","status":status,"body":body})
  use_result("use-valid",use);use_result("use-rotation",{**use,"rotation":{**rotation,"accessToken":"not-exposed"}})
  use_result("use-handoff",{**use,"allowed":False,"status":409,"errorCode":"SessionUpdateRequired","rotation":rotation})
  use_result("use-expired",{"allowed":False,"status":409,"errorCode":"ConnectionRequired"})
  use_result("use-unavailable",{},503)
  for field,value in {"protocolVersion":1,"idleExpiresAt":1796000000,"generation":0}.items():use_result("use-invalid-"+field,{**use,field:value})
  for field,value in {"candidateSession":"cs2."+SECRET+"."+STATE,"fromGeneration":3,"generation":8,"rotationId":"bad"}.items():use_result("use-invalid-rotation-"+field,{**use,"rotation":{**rotation,field:value}})
  for name,body,headers,expected in (("claims-body",{"error":{"code":"insufficient_claims"}},{},True),
       ("claims-header",{}, {"WWW-Authenticate":'Bearer error="insufficient_claims", claims="opaque"'},True),
       ("claims-permission",{"error":{"code":"Authorization_RequestDenied"}},{},False)):
   add(name,"connection:gatewayConnectionClaimsRequired:0",response={"name":"gatewayConnectionIdentityResponse","status":403,"body":body,"headers":headers})
  def commit_request(name,**changes):
   add(name,"session-commit:credentialSessionRequest:0",headers=changes.pop("headers",{"X-Core-Connection-Session":SESSION}),body=changes.pop("body",{"rotationId":LAUNCH,"candidateSession":"cs2."+TX+"."+STATE,"fromGeneration":4}),**changes)
  commit_request("commit-request-valid")
  commit_request("commit-request-spoof",body={"rotationId":LAUNCH,"candidateSession":SESSION,"fromGeneration":4,"subscriptionId":"other"})
  commit_request("commit-request-double-header",headers={"X-Core-Connection-Session":[SESSION,SESSION]})
  commit_request("commit-request-old-format",headers={"X-Core-Connection-Session":TX+"."+SECRET})
  def callback_input(name,**changes):
   add(name,"callback:credentialCallbackInput:0",query=changes.pop("query",{"transactionId":TX,"state":STATE}),headers=changes.pop("headers",{"Cookie":"__Host-CoreCredentialLogin="+TX+"."+COOKIE}),**changes)
  callback_input("callback-input-valid");callback_input("callback-missing-cookie",headers={});callback_input("callback-wrong-tx",headers={"Cookie":"__Host-CoreCredentialLogin="+SECRET+"."+COOKIE})
  callback_input("callback-duplicate-cookie",headers={"Cookie":"__Host-CoreCredentialLogin="+TX+"."+COOKIE+"; __Host-CoreCredentialLogin="+TX+"."+COOKIE})
  callback_input("callback-duplicate-query",query={"transactionId":[TX,TX],"state":STATE})
  for name,url in {"origin-valid":"https://gateway.example.com","origin-other":"https://attacker.example","origin-suffix":"https://gateway.example.com.attacker.example"}.items():
   add(name,"launch-post:credentialOriginValid:0",headers={"Origin":url},variables={"credentialLoginSettings":settings()})
  for name,body,status in (("owner-valid",{"id":OWNER},200),("owner-wrong",{"id":TENANT},200),("owner-forged",{"id":"invalid"},200),("owner-failed",{"id":OWNER},403)):
   add(name,"callback:credentialOwnerId:0",variables={"credentialTransaction":selected},response={"name":"credentialIdentityResponse","status":status,"body":body})
  source=CSHARP.replace("__METHODS__","\n".join(methods)).replace("__SWITCH__","\n".join(switches))
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory);(path/"fixture.cs").write_text(source,encoding="utf-8");(path/"cases.json").write_text(json.dumps(cases),encoding="utf-8")
   (path/"run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[CredentialFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding="utf-8")
   process=subprocess.run([pwsh,"-NoProfile","-File",str(path/"run.ps1")],capture_output=True,text=True,timeout=60)
   if process.returncode:raise AssertionError(process.stderr)
   cls.results=json.loads(process.stdout)

 def test_caller_availability_and_session_shape_checked_before_broker_call(self):
  for name in ('pre-valid','pre-subscription-b'):
   self.assertTrue(self.results[name]['selection']["allowed"],name)
   self.assertFalse(self.results[name]['guardRejected'],name)
  for name,status in (("pre-no-session",409),("pre-multi-session",403),("pre-malformed",409),("pre-disabled",403),("pre-old-format",409)):
   result=self.results[name]
   self.assertEqual((result['selection']['allowed'],result['selection']['status']),(False,status),name)
   self.assertFalse(result['guardRejected'],name)

  for policy in ('start','status','session-commit','logout','logout-all','disconnect'):
   for label,expected in (('key','subscription:sub-a'),('other-key','subscription:sub-b'),
                          ('user','user:'+TENANT+':'+OWNER),('unvalidated-user',''),('common-failed',''),
                          ('unexpected-subscription',''),('bad-json',''),('missing-caller',''),('null-caller',''),('empty-caller','')):
    self.assertEqual(self.results[policy+'-caller-'+label],expected)
   for field in ('schemaVersion','tenantId','principalId','allocationId'):
    self.assertEqual(self.results[policy+'-caller-legacy-'+field],'user:'+TENANT+':'+OWNER)

 def test_internal_caller_failures_skip_external_stages_and_return_the_actual_500_audit(self):
  for name in ('pre-missing-caller','pre-missing-decision','pre-malformed-decision','pre-nonobject-decision','pre-null-caller','pre-empty-caller'):
   with self.subTest(failure=name):
    result=self.results[name]
    self.assertEqual(result['callerId'],'')
    self.assertEqual(result['selection'],{'allowed':False,'status':500})
    self.assertEqual(result['initialExternalGuards'],{'broker':False,'cm':False,'graph':False})
    self.assertEqual((result['cmCallerCalls'],result['cmSelectionCalls']),(1,1))
    self.assertTrue(result['guardRejected'])
    self.assertFalse(result['postGuardReached'])
    self.assertEqual((result['status'],result['reason']),(500,'Gateway processing failed'))
    self.assertEqual(result['headers'],{'apim-request-id':result['requestId'],'Cache-Control':'no-store'})
    self.assertFalse(result['hasBody'])
    audit=result['audit']
    self.assertEqual((audit['phase'],audit['code'],audit['status']),('policy-error','AUTHENTICATION_PROCESSING_ERROR',500))
    self.assertEqual(audit['requestId'],result['requestId'])
    self.assertNotIn(SESSION,json.dumps(audit))
    self.assertNotIn('gatewayDecision',audit)
  for status in (200,403,409,503):
   self.assertFalse(self.results['caller-error-status-'+str(status)],status)

 def test_verified_caller_is_reused_and_personal_connection_owner_cannot_change(self):
  self.assertEqual(self.results["caller-key"],"subscription:sub-a")
  self.assertEqual(self.results["caller-user"],"user:"+TENANT+":"+OWNER)
  self.assertEqual(self.results["caller-legacy-markers"],"user:"+TENANT+":"+OWNER)
  for label in ('missing-decision','malformed-decision','non-string-decision','missing-caller'):
   self.assertEqual(self.results['caller-'+label],'',label)
  for label in ('valid','legacy-fields-broken','marker-ignored','stale-json'):
   self.assertTrue(self.results['pre-user-'+label]['selection']["allowed"],label)
   self.assertFalse(self.results['pre-user-'+label]['guardRejected'],label)
  self.assertTrue(json.loads(self.results["selection-user-valid"])["allowed"])
  self.assertFalse(json.loads(self.results["selection-user-wrong-owner"])["allowed"])
  for label in ("key","user"):
   for header in ("X-Core-Caller-Id","X-Core-Subscription-Id"):
    expected="override" if (label=="user")== (header=="X-Core-Caller-Id") else "delete"
    self.assertEqual(expected,self.results["caller-header-"+label+"-"+header])

 def test_http_and_external_mcp_authentication_handoffs_precede_cm_selection(self):
  for name,root in self.handoff_policies.items():
   with self.subTest(api=name):
    inbound=root.find('inbound');nodes=list(inbound)
    handoff=inbound.find("include-fragment[@fragment-id='core-api-authentication-result']") if name=='http' else inbound.find("set-variable[@name='gatewayDecision']")
    rejection=next(node for node in inbound.findall('choose') if any('gatewayDecision' in when.get('condition','') and '["allowed"]' in when.get('condition','') for when in node.findall('when')))
    self.assertLess(nodes.index(handoff),nodes.index(rejection))
    self.assertEqual(rejection.find('when/return-response/set-status').get('code'),'500')
    if name=='http':
     cm=inbound.find("include-fragment[@fragment-id='core-credential-connection']")
     self.assertLess(nodes.index(rejection),nodes.index(cm))
    else:
     macro=next(index for index,node in enumerate(nodes) if '__CREDENTIAL_CONNECTION_INBOUND_XML__' in (node.tail or ''))
     self.assertLess(nodes.index(rejection),macro)
     self.assertEqual(len(inbound.findall("set-variable[@name='gatewayDecision']")),1)

 def test_actual_handoffs_stop_before_cm_or_reuse_the_same_caller_without_identity_rereads(self):
  for protocol in ('http','mcp'):
   for label,caller,method in (('key','subscription:sub-a','subscriptionKey'),('user','user:'+TENANT+':'+OWNER,'user')):
    with self.subTest(api=protocol,caller=label):
     result=self.results['handoff-'+protocol+'-'+label]
     self.assertTrue(result['decision']['allowed'])
     self.assertEqual(result['decision']['callerId'],caller)
     self.assertEqual(result['decision']['method'],method)
     self.assertTrue(result['cmReached'])
     self.assertEqual(result['callerId'],caller)
     self.assertTrue(result['selection']['allowed'])
     self.assertEqual(result['cmCallerCalls'],1)
     self.assertEqual(result['cmSelectionCalls'],1)
     self.assertFalse(result['guardRejected'])
     self.assertTrue(result['postGuardReached'])
     self.assertEqual(result['cmIdentityReads'],0)
     self.assertEqual(result['cmDecisionReads'],1)
   for label in ('user-unvalidated','user-malformed'):
    with self.subTest(api=protocol,failure=label):
     result=self.results['handoff-'+protocol+'-'+label]
     self.assertEqual(result['decision'],{'allowed':False})
     self.assertEqual((result['status'],result['phase'],result['code']),(500,'policy-error','AUTHENTICATION_PROCESSING_ERROR'))
     self.assertFalse(result['cmReached'])
     self.assertEqual(result['cmCallerCalls'],0)
     self.assertEqual(result['cmIdentityReads'],0)
     self.assertEqual(result['cmDecisionReads'],0)
     self.assertNotIn('callerId',result)
     self.assertNotIn('selection',result)
   result=self.results['handoff-'+protocol+'-caller-lost']
   self.assertTrue(result['cmReached'])
   self.assertEqual(result['selection'],{'allowed':False,'status':500})
   self.assertEqual(result['initialExternalGuards'],{'broker':False,'cm':False,'graph':False})
   self.assertTrue(result['guardRejected'])
   self.assertFalse(result['postGuardReached'])
   self.assertEqual((result['cmCallerCalls'],result['cmSelectionCalls']),(1,1))
   self.assertEqual((result['status'],result['audit']['code']),(500,'AUTHENTICATION_PROCESSING_ERROR'))
   self.assertEqual(result['cmIdentityReads'],0)
   self.assertEqual(result['cmDecisionReads'],1)

 def test_builtin_authorization_and_throttling_are_not_reported_as_broker_failure(self):
  for operation in credential_login.OPERATIONS:
   for label,expected in (("missing",401),("invalid",401),("limited",429),("backend",503),("expression",503)):
    self.assertEqual(self.results["proxy-error-"+operation+"-"+label],expected)

 def test_cm_operation_errors_use_the_shared_entra_guard_before_their_own_responses(self):
  for operation in credential_login.OPERATIONS:
   with self.subTest(operation=operation):
    assert_shared_operation_error(self,self.results['operation-error-'+operation+'-entra'])
   for label,_ in operation_error_cases()[1:]:
    with self.subTest(operation=operation,error=label):
     result=self.results['operation-error-'+operation+'-'+label]
     expected=401 if label=='subscription' else 429 if label=='rate' else 503
     self.assertFalse(result['sharedHandled'])
     self.assertTrue(result['legacyReached'])
     self.assertEqual(result['status'],expected)
     self.assertEqual(result['body']['error']['code'],'AuthenticationRequired' if expected==401 else 'RateLimitExceeded' if expected==429 else 'ConnectionUnavailable')
     self.assertEqual(result['audits'],[])
     self.assertNotIn('WWW-Authenticate',result['headers'])
   disabled=self.results['operation-error-'+operation+'-disabled']
   self.assertFalse(disabled['sharedHandled'])
   self.assertTrue(disabled['legacyReached'])
   self.assertEqual(disabled['status'],503)

 def test_cm_refusal_audit_retains_verified_user_without_a_subscription(self):
  user=json.loads(self.results["audit-user"])
  self.assertEqual(user["callerId"],"user:"+TENANT+":"+OWNER)
  self.assertEqual((user["verifiedTenantId"],user["verifiedPrincipalId"]),(TENANT,OWNER))
  self.assertEqual(user["method"],"user")
  self.assertNotIn("subscriptionId",user)
  key=json.loads(self.results["audit-key"])
  self.assertEqual(key["subscriptionId"],"sub-a")
  self.assertEqual(key["method"],"subscriptionKey")
  mismatch=json.loads(self.results["audit-mismatch"])
  self.assertEqual(mismatch["authorization"],"unconfirmed")
  self.assertNotIn("callerId",mismatch)

 def test_start_rate_policy_captures_retry_seconds_without_changing_its_counter(self):
  rate=self.roots["start"].find("inbound/rate-limit-by-key")
  self.assertEqual(rate.get("retry-after-variable-name"),"credentialLoginStartRetryAfter")
  self.assertEqual((rate.get("calls"),rate.get("renewal-period")),("10","60"))
  self.assertIn('credentialCallerId',rate.get("counter-key"))

 def test_rate_error_headers_use_bounded_canonical_retry_seconds(self):
  for policy in ("start","challenge"):
   for label,seconds in (("minimum","1"),("maximum","300"),("missing","60"),("invalid","60")):
    with self.subTest(policy=policy,input=label):
     self.assertEqual(self.results["retry-"+policy+"-"+label],{"action":"override","header":seconds})

 def test_other_connection_errors_do_not_emit_retry_after(self):
  for policy,statuses in (("start",(401,503)),("challenge",(403,409,503))):
   for status in statuses:
    with self.subTest(policy=policy,status=status):
     self.assertEqual(self.results["retry-"+policy+"-status-"+str(status)],{"action":"delete","header":None})

 def test_authentication_challenge_only_returns_valid_gateway_urls_and_allowlisted_fields(self):
  value=json.loads(self.results["challenge-valid"])
  self.assertEqual(value["status"],409)
  self.assertEqual(value["authentication"]["transactionId"],TX)
  self.assertEqual(value["authentication"]["pollSecret"],SECRET)
  self.assertEqual(value["authentication"]["protocolVersion"],2)
  self.assertEqual(value["authentication"]["provisionalConnectionSession"],SESSION)
  self.assertNotIn("accessToken",json.loads(self.results["challenge-extra"])["authentication"])
  for name,result in self.results.items():
   if name.startswith("challenge-bad-") or name in ("challenge-extra-query","challenge-long"):
    self.assertEqual(json.loads(result),{"status":503},name)
  for status,expected in ((403,403),(429,429),(500,503)):
   self.assertEqual(json.loads(self.results["challenge-http-"+str(status)]),{"status":expected})

  for status in (200,403,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    name='challenge-body-'+body_mode+'-'+str(status)
    self.assertEqual(json.loads(self.results[name]),{"status":status if status in (403,429) else 503})
    self.assertEqual(self.results[name+'-bodyReads']['gatewayConnectionChallengeResponse'],-1 if body_mode=='null' else 0)
  self.assertEqual(json.loads(self.results['challenge-missing-response']),{"status":503})

 def test_identity_check_distinguishes_reauthentication_from_denial_and_service_failure(self):
  for suffix,status in (("valid",200),("mismatch",403),("reauth",409),("forbidden",403),("unavailable",503)):
   self.assertEqual(self.results["connection-owner-"+suffix],status)

  for status in (200,401,403,409,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    name='identity-body-'+body_mode+'-'+str(status)
    self.assertEqual(self.results[name],409 if status==401 else 403 if status==403 else 503)
    self.assertEqual(self.results[name+'-bodyReads']['gatewayConnectionIdentityResponse'],-1 if body_mode=='null' else 0)
  self.assertEqual(self.results['identity-missing-response'],503)

 def test_authorize_and_sql_use_keep_the_session_saved_before_header_changes(self):
  for label,identity_field,identity in (('key','subscriptionId','sub-a'),('user','callerId','user:'+TENANT+':'+OWNER)):
   with self.subTest(caller=label):
    result=self.results['graph-use-'+label]
    self.assertEqual(result['savedSession'],SESSION)
    self.assertTrue(result['graphProcessed'])
    self.assertEqual(result['identityStatus'],200)
    self.assertTrue(result['sqlUseEligible'])
    for name in ('authorizeRequest','useRequest'):
     self.assertEqual(result[name]['connectionSession'],SESSION)
     self.assertEqual(result[name][identity_field],identity)
    self.assertEqual(result['useRequest']['verifiedOwnerId'],OWNER)
    self.assertEqual((result['useRequest']['apiId'],result['useRequest']['operationId']),('graph-me','get-me'))

 def test_graph_and_broker_refusals_stop_sql_use_through_the_outer_selection_gate(self):
  for label,status in (('owner-mismatch',403),('reauth',409),('unavailable',503)):
   with self.subTest(graph=label):
    result=self.results['graph-use-'+label]
    self.assertTrue(result['graphProcessed'])
    self.assertEqual(result['identityStatus'],status)
    self.assertEqual((result['selectedConnection']['allowed'],result['selectedConnection']['status']),(False,status))
    self.assertFalse(result['sqlUseEligible'])
    self.assertNotIn('useRequest',result)
  broker=self.results['graph-use-broker-forbidden']
  self.assertFalse(broker['graphProcessed'])
  self.assertFalse(broker['sqlUseEligible'])
  self.assertEqual(broker['selectedConnection']['status'],403)
  self.assertNotIn('identityStatus',broker)
  self.assertNotIn('useRequest',broker)
  for response_name,statuses in (('broker',(403,409)),('graph',(401,403,503))):
   for status in statuses:
    name='graph-use-null-'+response_name+'-'+str(status);result=self.results[name]
    self.assertFalse(result['sqlUseEligible']);self.assertNotIn('useRequest',result)
    self.assertEqual(result['selectedConnection']['status'],409 if status==401 else status)
    self.assertEqual(result['graphProcessed'],response_name=='graph')
    self.assertEqual(self.results[name+'-bodyReads']['gatewayConnection'+('Session' if response_name=='broker' else 'Identity')+'Response'],-1)
  service=self.results['graph-use-service']
  self.assertTrue(service['selectedConnection']['allowed'])
  self.assertFalse(service['graphProcessed'])
  self.assertFalse(service['sqlUseEligible'])
  self.assertNotIn('authorizeRequest',service)
  self.assertNotIn('useRequest',service)

 def test_broker_response_matches_fixed_configuration(self):
  self.assertTrue(json.loads(self.results["selection-valid"])["allowed"])
  for name,value in self.results.items():
   if name.startswith("selection-bad-"):self.assertFalse(json.loads(value)["allowed"],name)
  for name,status in (("selection-forbidden",403),("selection-required",409),("selection-unavailable",503)):
   self.assertEqual(json.loads(self.results[name])["status"],status)
  self.assertEqual(json.loads(self.results["selection-bad-allowed"])["status"],409)

  for status in (200,403,409,429,500):
   for body_mode in ('null','locked'):
    if status==200 and body_mode=='locked':continue
    name='selection-body-'+body_mode+'-'+str(status)
    self.assertEqual(json.loads(self.results[name])['status'],status if status in (403,409) else 503)
    self.assertEqual(self.results[name+'-bodyReads']['gatewayConnectionSessionResponse'],-1 if body_mode=='null' else 0)
  self.assertEqual(json.loads(self.results['selection-missing-response'])['status'],503)

 def test_broker_provider_match_is_unique_and_malformed_pools_fail_closed(self):
  for label in ("empty","duplicate","malformed","other-source"):
   with self.subTest(pool=label):
    result=json.loads(self.results["selection-pool-"+label])
    self.assertFalse(result["allowed"])
    self.assertEqual(result["status"],503)
  unique=json.loads(self.results["selection-pool-unique"])
  self.assertTrue(unique["allowed"])
  self.assertEqual(unique["providerName"],"graph-delegated-a")

 def test_callback_input_and_owner_verification(self):
  self.assertTrue(self.results["callback-input-valid"])
  for name in ("callback-missing-cookie","callback-wrong-tx","callback-duplicate-cookie","callback-duplicate-query"):
   self.assertEqual(self.results[name],"",name)
  self.assertEqual(self.results["owner-valid"],OWNER)
  for name in ("owner-wrong","owner-forged","owner-failed"):self.assertEqual(self.results[name],"",name)

 def test_launch_requires_exact_origin(self):
  self.assertTrue(self.results["origin-valid"])
  self.assertFalse(self.results["origin-other"]);self.assertFalse(self.results["origin-suffix"])

 def test_cm_token_failure_requires_confirmed_connection_state_before_reauthentication(self):
  self.assertEqual(json.loads(self.results['cm-confirmed-reauth'])['status'],409)
  for name in ('cm-still-connected','cm-unknown'):
   self.assertEqual(json.loads(self.results[name])['status'],503)

 def test_final_business_validation_keeps_rotation_conflicts_out_of_login_flow(self):
  self.assertTrue(json.loads(self.results['use-valid'])['allowed'])
  updated=json.loads(self.results['use-rotation'])
  self.assertEqual(updated['rotation']['generation'],5)
  self.assertNotIn('accessToken',updated['rotation'])
  handoff=json.loads(self.results['use-handoff'])
  self.assertFalse(handoff['allowed']);self.assertEqual(handoff['errorCode'],'SessionUpdateRequired')
  self.assertEqual(json.loads(self.results['use-expired'])['status'],409)
  self.assertEqual(json.loads(self.results['use-unavailable'])['status'],503)
  for name,result in self.results.items():
   if name.startswith('use-invalid-'):self.assertEqual(json.loads(result)['status'],503,name)

 def test_claims_requirements_are_distinct_from_graph_acl_denials(self):
  self.assertTrue(self.results['claims-body']);self.assertTrue(self.results['claims-header'])
  self.assertFalse(self.results['claims-permission'])

 def test_public_commit_trusts_gateway_subscription_and_session_header(self):
  result=json.loads(self.results['commit-request-valid'])
  self.assertEqual(result['subscriptionId'],'sub-a');self.assertEqual(result['connectionSession'],SESSION)
  for name in ('commit-request-spoof','commit-request-double-header','commit-request-old-format'):
   self.assertEqual(self.results[name],'',name)
