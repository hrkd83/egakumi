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
CSHARP = r'''
using System;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Named { public string Id; }
public class Url { public string Path="/"; public Dictionary<string,string[]> Query=new Dictionary<string,string[]>(); }
public class Body { public string Data="{}"; public T As<T>(bool preserveContent=false) {
 if(typeof(T)==typeof(byte[]))return (T)(object)System.Text.Encoding.UTF8.GetBytes(Data);
 if(typeof(T)==typeof(string))return (T)(object)Data;
 return (T)(object)JObject.Parse(Data);
} }
public class Request { public string Method="POST"; public Url Url=new Url(), OriginalUrl=new Url(); public Body Body=new Body(); public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public interface IResponse { int StatusCode {get;} Body Body {get;} Dictionary<string,string[]> Headers {get;} }
public class Response:IResponse { public int StatusCode {get;set;} public Body Body {get;set;} public Dictionary<string,string[]> Headers {get;set;}=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public class Authorization { public string AccessToken="opaque-token"; public Dictionary<string,object> Claims=new Dictionary<string,object>(); }
public class LastError { public string Source="",Reason=""; }
public class Context { public Guid RequestId=Guid.NewGuid(); public Request Request=new Request(); public Response Response=new Response(); public LastError LastError=new LastError(); public Named Api=new Named(),Operation=new Named(),Subscription; public Dictionary<string,object> Variables=new Dictionary<string,object>(); }
public static class Extensions { public static string GetValueOrDefault(this Dictionary<string,string[]> d,string k,string fallback){return d.ContainsKey(k)?String.Join(" ",d[k]):fallback;} }
public static class CredentialFixture {
 __METHODS__
 public static string Run(string json) {
  var output=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Api.Id=(string)item["api"];c.Operation.Id=(string)item["operation"];c.Request.Method=(string)item["method"]??"POST";c.Request.OriginalUrl.Path=(string)item["path"]??"/";
   if(item["lastError"] is JObject lastError){c.LastError.Source=(string)lastError["source"];c.LastError.Reason=(string)lastError["reason"];}
   if(item["subscription"]!=null)c.Subscription=new Named{Id=(string)item["subscription"]};
   if(item["body"]!=null)c.Request.Body.Data=item["body"].Type==JTokenType.String?(string)item["body"]:item["body"].ToString(Formatting.None);
   foreach(var p in ((JObject)(item["headers"]??new JObject())).Properties())c.Request.Headers[p.Name]=p.Value is JArray?p.Value.Select(v=>(string)v).ToArray():new[]{(string)p.Value};
   foreach(var p in ((JObject)(item["query"]??new JObject())).Properties())c.Request.Url.Query[p.Name]=p.Value is JArray?p.Value.Select(v=>(string)v).ToArray():new[]{(string)p.Value};
   foreach(var p in ((JObject)(item["variables"]??new JObject())).Properties())c.Variables[p.Name]=p.Value.Type==JTokenType.String?(object)(string)p.Value:p.Value.Type==JTokenType.Boolean?(object)(bool)p.Value:p.Value.Type==JTokenType.Integer?(object)(int)p.Value:p.Value.ToString(Formatting.None);
   if(item["response"] is JObject response){var r=new Response{StatusCode=(int)response["status"],Body=new Body{Data=response["body"].Type==JTokenType.String?(string)response["body"]:response["body"].ToString(Formatting.None)}};foreach(var p in ((JObject)(response["headers"]??new JObject())).Properties())r.Headers[p.Name]=new[]{(string)p.Value};c.Variables[(string)response["name"]]=r;}
   if(item["claims"] is JObject claims){var authorization=new Authorization();foreach(var p in claims.Properties())authorization.Claims[p.Name]=p.Value.Type==JTokenType.Integer?(object)(long)p.Value:p.Value.Type==JTokenType.String?(object)(string)p.Value:(object)p.Value;c.Variables["gatewayConnectionContext"]=authorization;}
   switch((string)item["expression"]) { __SWITCH__ default:throw new Exception("Unknown expression"); }
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
     self.assertEqual(node.get("exists-action"),"override")
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
  gateway=ET.fromstring((CORE/'apps/gateway/apim/policies/gateway.xml').read_text(encoding='utf-8'))
  parent=next(node for node in gateway.iter('when') if node.find("include-fragment[@fragment-id='core-credential-connection']") is not None)
  self.assertEqual([node.get('fragment-id') for node in parent.findall('include-fragment')],
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
  cls.roots=CredentialPolicyTests.roots
  pwsh=os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
  if not pwsh:raise unittest.SkipTest("PowerShell Roslyn is required")
  methods=[];switches=[];counts={};retry_methods={}
  for filename,root in cls.roots.items():
   parents={child:parent for parent in root.iter() for child in parent}
   for node in root.iter():
    expressions=[("attribute",v) for v in node.attrib.values() if v.startswith("@")]
    if node.text and node.text.strip().startswith("@"):
     expressions.append(("text",node.text.strip()))
    for kind,value in expressions:
     method="Expression"+str(len(methods))
     methods.append("static object "+method+"(Context context) { "+expression(value)+" }")
     if node.tag=="set-variable" and kind=="attribute" and value==node.get("value"):
      name=filename+":"+node.get("name");number=counts.get(name,0);counts[name]=number+1
      switches.append('case '+json.dumps(name+":"+str(number))+':output[(string)item["id"]]=JToken.FromObject('+method+'(c));break;')
     if filename in ("start","challenge"):
      if node.tag=="set-variable" and node.get("name")==("credentialProxyRetryAfter" if filename=="start" else "gatewayConnectionChallengeRetryAfter"):
       retry_methods[(filename,"seconds")]=method
      if node.tag=="set-header" and node.get("name")=="Retry-After" and kind=="attribute":
       retry_methods[(filename,"action")]=method
      parent=parents.get(node)
      if node.tag=="value" and parent is not None and parent.tag=="set-header" and parent.get("name")=="Retry-After":
       retry_methods[(filename,"header")]=method
  # 秒の検証から実header式までを同じcontextで動かし、固定値の代用品にしません。
  for policy,variable in (("start","credentialProxyRetryAfter"),("challenge","gatewayConnectionChallengeRetryAfter")):
   if not all((policy,part) in retry_methods for part in ("seconds","action","header")):
    raise AssertionError("Rate error response must expose its actual Retry-After expressions")
   switches.append('case "retry-'+policy+'":c.Variables['+json.dumps(variable)+']='+retry_methods[(policy,"seconds")]+'(c);var retryAction'+policy+'=(string)'+retry_methods[(policy,"action")]+'(c);output[(string)item["id"]]=new JObject(new JProperty("action",retryAction'+policy+'),new JProperty("header",retryAction'+policy+'=="override"?(string)'+retry_methods[(policy,"header")]+'(c):null));break;')
  cases=[]
  def add(name,expr,**values):cases.append({"id":name,"expression":expr,"subscription":"sub-a",**values})
  for operation in credential_login.OPERATIONS:
   for label,source,reason in (("missing","authorization","SubscriptionKeyNotFound"),("invalid","authorization","SubscriptionKeyInvalid"),("limited","rate-limit-by-key","RateLimitExceeded"),("backend","forward-request","BackendConnectionFailure"),("expression","set-variable","ExpressionValueEvaluationFailure")):
    add("proxy-error-"+operation+"-"+label,operation+":credentialProxyErrorStatus:0",lastError={"source":source,"reason":reason})
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
   add(name,"connection:gatewayConnectionSelection:0",headers=changes.pop("headers",{"X-Core-Connection-Session":SESSION}),variables={"gatewayDecision":changes.pop("decision",decision),"gatewayCredentialConnection":credential_login.encode(selected)},**changes)
  preliminary("pre-valid");preliminary("pre-no-session",headers={});preliminary("pre-multi-session",headers={"X-Core-Connection-Session":[SESSION]*2});preliminary("pre-old-format",headers={"X-Core-Connection-Session":TX+"."+SECRET})
  preliminary("pre-malformed",headers={"X-Core-Connection-Session":"bad"});preliminary("pre-other-sub",subscription="sub-b");preliminary("pre-denied",decision={**decision,"allowed":False});preliminary("pre-disabled",config={**config,"enabled":False})
  selected={"allowed":True,"providerName":"graph-delegated-a","connectionId":"test.user-abcdef0123456789","ownerId":OWNER,"tenantId":TENANT,"identityEndpoint":"https://graph.microsoft.com/v1.0/me","identityIdProperty":"id"}
  def selection(name,body=None,status=200):
   add(name,"connection:gatewayConnectionSelection:1",variables={"gatewayConnectionSelection":{"profileName":"graph-delegated"}},response={"name":"gatewayConnectionSessionResponse","status":status,"body":selected if body is None else body})
  selection("selection-valid");selection("selection-forbidden",status=403);selection("selection-required",status=409);selection("selection-unavailable",status=500)
  for field,value in {"providerName":"other-provider","tenantId":OWNER,"ownerId":"not-guid","identityEndpoint":"https://attacker.example/me","connectionId":"../../other","allowed":False}.items():selection("selection-bad-"+field,{**selected,field:value})
  challenge={"status":"need_login","protocolVersion":2,"provisionalConnectionSession":SESSION,"transactionId":TX,"pollSecret":SECRET,"expiresIn":600,
   "loginUrl":"https://gateway.example.com/auth/connections/browser/launch#transactionId="+TX+"&launch="+LAUNCH,
   "statusUrl":"https://gateway.example.com/auth/connections/status?transactionId="+TX}
  def challenge_result(name,body=None,status=200):
   add(name,"challenge:gatewayConnectionChallengeResult:0",response={"name":"gatewayConnectionChallengeResponse","status":status,"body":challenge if body is None else body})
  challenge_result("challenge-valid");challenge_result("challenge-extra",{**challenge,"accessToken":"must-not-leave-broker"})
  for status in (403,429,500):challenge_result("challenge-http-"+str(status),status=status)
  for field,value in {"status":"complete","protocolVersion":1,"provisionalConnectionSession":TX+"."+SECRET,"transactionId":"bad","pollSecret":"bad","expiresIn":601,"loginUrl":"https://attacker.example/auth","statusUrl":"https://attacker.example/status"}.items():challenge_result("challenge-bad-"+field,{**challenge,field:value})
  challenge_result("challenge-extra-query",{**challenge,"loginUrl":challenge["loginUrl"]+"&pollSecret="+SECRET})
  challenge_result("challenge-long",{**challenge,"extra":"x"*16384})
  for name,body,status in (("connection-owner-valid",{"id":OWNER},200),("connection-owner-mismatch",{"id":TENANT},200),("connection-owner-reauth",{},401),("connection-owner-forbidden",{},403),("connection-owner-unavailable",{},500)):
   add(name,"connection:gatewayConnectionIdentityStatus:0",variables={"gatewayConnectionSelection":selected},response={"name":"gatewayConnectionIdentityResponse","status":status,"body":body})
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

 def test_subscription_and_session_shape_checked_before_broker_call(self):
  self.assertTrue(json.loads(self.results["pre-valid"])["allowed"])
  for name in ("pre-no-session","pre-multi-session","pre-malformed","pre-other-sub","pre-denied","pre-disabled","pre-old-format"):
   self.assertFalse(json.loads(self.results[name])["allowed"],name)

 def test_builtin_authorization_and_throttling_are_not_reported_as_broker_failure(self):
  for operation in credential_login.OPERATIONS:
   for label,expected in (("missing",401),("invalid",401),("limited",429),("backend",503),("expression",503)):
    self.assertEqual(self.results["proxy-error-"+operation+"-"+label],expected)

 def test_start_rate_policy_captures_retry_seconds_without_changing_its_counter(self):
  rate=self.roots["start"].find("inbound/rate-limit-by-key")
  self.assertEqual(rate.get("retry-after-variable-name"),"credentialLoginStartRetryAfter")
  self.assertEqual((rate.get("calls"),rate.get("renewal-period")),("10","60"))
  self.assertEqual(rate.get("counter-key"),'@("credential-login-start:" + context.Subscription.Id)')

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

 def test_identity_check_distinguishes_reauthentication_from_denial_and_service_failure(self):
  for suffix,status in (("valid",200),("mismatch",403),("reauth",409),("forbidden",403),("unavailable",503)):
   self.assertEqual(self.results["connection-owner-"+suffix],status)

 def test_broker_response_matches_fixed_configuration(self):
  self.assertTrue(json.loads(self.results["selection-valid"])["allowed"])
  for name,value in self.results.items():
   if name.startswith("selection-bad-"):self.assertFalse(json.loads(value)["allowed"],name)
  for name,status in (("selection-forbidden",403),("selection-required",409),("selection-unavailable",503)):
   self.assertEqual(json.loads(self.results[name])["status"],status)
  self.assertEqual(json.loads(self.results["selection-bad-allowed"])["status"],409)

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
