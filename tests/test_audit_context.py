"""実APIM XML式のAgent相関/下流ID/モデル監査を、ローカルRoslynで検証する。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_contract_rate_policy import expression, PWSH

CORE=Path(__file__).resolve().parents[1]
TENANT='11111111-1111-4111-8111-111111111111'
PRINCIPAL='22222222-2222-4222-8222-222222222222'
CLIENT='33333333-3333-4333-8333-333333333333'
PARENT='44444444-4444-4444-8444-444444444444'
BACKEND='55555555-5555-4555-8555-555555555555'
BIND={'agentName':'approved-agent','callerId':'agent:approved-agent','tenantId':TENANT,
      'principalId':PRINCIPAL,'clientId':CLIENT,'requiredRoles':['Gateway.Invoke.App']}
SETTINGS={'backendService':'foundryOpenAi','backendAuthentication':'managedIdentity','operationId':'',
 'operationConstraints':{'responses-create':{'kind':'statelessResponses','allowedModels':['approved-model']}},
 'websocketModel':'','targetAgent':'','llmOperationIds':[],'knownAgents':[BIND],'measurementRole':'execution'}
CLAIMS={'ver':'2.0','tid':TENANT,'oid':PRINCIPAL,'azp':CLIENT,'idtyp':'app','roles':['Gateway.Invoke.App']}
DECISION={'allowed':True,'method':'subscriptionKey','callerId':'subscription:subscription-a'}
SOURCE=r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Headers : Dictionary<string,string[]> {
 public string GetValueOrDefault(string key,string fallback) { return ContainsKey(key)?String.Join(",",this[key]):fallback; }
}
public class Claims : Dictionary<string,string[]> {
 public string GetValueOrDefault(string key,string fallback) { return ContainsKey(key)?String.Join(" ",this[key]):fallback; }
}
public class Jwt { public Claims Claims=new Claims(); }
public class Body {
 public string Text="{}"; public int Reads=0; public bool BlockReads=false; public bool AllReadsPreserved=true;
 public T As<T>(bool preserveContent=false) {
  Reads++; AllReadsPreserved &= preserveContent;
  if(BlockReads) throw new Exception("Native protocol body must remain unread");
  if(!preserveContent) throw new Exception(); return (T)(object)JObject.Parse(Text);
 }
}
public class Request { public Headers Headers=new Headers(); public Body Body=new Body(); public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>(); }
public class Response { public Headers Headers=new Headers(); public int StatusCode=200; public Body Body {get {throw new Exception("Do not read response body");}} }
public class Named { public string Id="responses-create"; }
public class Context {
 public Guid RequestId=Guid.Parse("66666666-6666-4666-8666-666666666666");
 public Request Request=new Request(); public Response Response=new Response();
 public Named Api=new Named(); public Named Operation=new Named(); public Named Subscription=new Named(); public Dictionary<string,object> Variables=new Dictionary<string,object>();
}
public static class AuditFixture {
 public static string Claim(Context context) { __CLAIM__ }
 public static string Inject(Context context) { __INJECT__ }
 public static string BodyModel(Context context) { __BODY_MODEL__ }
 public static string Model(Context context) { __MODEL__ }
 public static string Backend(Context context) { __BACKEND__ }
 public static string Telemetry(Context context) { __TELEMETRY__ }
 public static string Run(string input) {
  var output=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var c=new Context();
   c.Subscription.Id=(string)item["subscriptionId"]??"subscription-a";
   if((bool?)item["nullSubscription"]==true) c.Subscription=null;
   c.Variables["gatewayAuditSettings"]=item["settings"].ToString(Formatting.None);
   c.Variables["gatewayDecision"]=item["decision"].ToString(Formatting.None);
   c.Variables["gatewayAuditPhase"]="backend-response"; c.Variables["gatewayAuditCode"]="BACKEND_RESPONSE";
   c.Request.Body.Text=(string)item["body"]??"{}";
   c.Request.Body.BlockReads=(bool?)item["httpModel"] == false;
   if(item["pathModel"]!=null) c.Request.MatchedParameters["deployment"]=(string)item["pathModel"];
   c.Operation.Id=(string)item["operation"]??"responses-create";
   if(item["parent"]!=null) c.Request.Headers["X-Core-Agent-Parent-Id"]=item["parent"] is JArray?((JArray)item["parent"]).Select(x=>(string)x).ToArray():new string[]{(string)item["parent"]};
   if(item["claims"] is JObject) {
    var jwt=new Jwt();
    foreach(var entry in ((JObject)item["claims"]).Properties()) jwt.Claims[entry.Name]=entry.Value is JArray?((JArray)entry.Value).Select(x=>(string)x).ToArray():new string[]{(string)entry.Value};
    c.Variables["gatewayValidatedToken"]=jwt;
   }
   foreach(var entry in ((JObject)item["headers"]).Properties()) c.Response.Headers[entry.Name]=entry.Value is JArray?((JArray)entry.Value).Select(x=>(string)x).ToArray():new string[]{(string)entry.Value};
   var before=(string)c.Variables["gatewayDecision"];
   c.Variables["gatewayAgentTrace"]=Claim(c);
   if(!c.Request.Body.BlockReads) c.Variables["gatewayAuditBodyModel"]=BodyModel(c);
   c.Variables["gatewayAuditModel"]=Model(c);
   c.Variables["gatewayBackendResponseIds"]=Backend(c);
   /* 親serviceが下流IDを上書きした後でも退避値を監査できることを検査します。 */
   c.Response.Headers["apim-request-id"]=new string[]{c.RequestId.ToString()};
   var record=JObject.Parse(Telemetry(c));
   record["injectedMetadata"]=c.Request.Body.BlockReads || (bool?)item["skipInjection"] == true?null:JObject.Parse(Inject(c))["metadata"];
   record["requestBodyReads"]=c.Request.Body.Reads;
   record["allReadsPreserved"]=c.Request.Body.AllReadsPreserved;
   if(before!=(string)c.Variables["gatewayDecision"]) throw new Exception("Attribution changed");
   output[(string)item["name"]]=record;
  }
  return output.ToString(Formatting.None);
 }
}
'''

class AuditContextTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  if not PWSH: raise unittest.SkipTest('Local Roslyn is required')
  cases=[]
  def add(name,**changes):
   value={'name':name,'settings':copy.deepcopy(SETTINGS),'claims':copy.deepcopy(CLAIMS),
     'decision':copy.deepcopy(DECISION),'parent':PARENT,'headers':{'apim-request-id':BACKEND,
       'Set-Cookie':'PRIVATE_COOKIE','Authorization':'PRIVATE_TOKEN'},
     'body':json.dumps({'model':'approved-model','input':'PRIVATE_TEXT','metadata':{'sample_mode':'fixed','core_parent_request_id':PARENT}})}
   value.update(changes); cases.append(value)
  add('valid')
  settings=copy.deepcopy(SETTINGS); settings['llmOperationIds']=['chat-create']; settings['operationConstraints']['chat-create']={'kind':'bodyModel','allowedModels':['approved-model']}
  add('chat',settings=settings,operation='chat-create')
  rerank=copy.deepcopy(SETTINGS); rerank['backendService']='foundryServices'
  rerank['operationConstraints']={'rerank':{'kind':'rerank','allowedModels':['approved-model']}}
  add('rerank',settings=rerank,operation='rerank')
  add('rerank-unapproved',settings=rerank,operation='rerank',body=json.dumps({'model':'PRIVATE_UNAPPROVED_MODEL'}))
  for name,changed in [('person',{**CLAIMS,'idtyp':'user','scp':'Gateway.Invoke'}),
      ('wrong-client',{**CLAIMS,'azp':PARENT}),('wrong-principal',{**CLAIMS,'oid':PARENT}),
      ('wrong-role',{**CLAIMS,'roles':['Other.Role']}),('multiple-oid',{**CLAIMS,'oid':[PRINCIPAL,PRINCIPAL]})]: add(name,claims=changed)
  add('key',claims=None,decision={**DECISION,'method':'subscriptionKey'})
  add('wrong-caller',decision={**DECISION,'callerId':'other-agent'})
  add('wrong-method',decision={**DECISION,'method':'entra'})
  add('denied',decision={**DECISION,'allowed':False})
  add('missing-subscription',nullSubscription=True)
  add('empty-subscription',subscriptionId='')
  add('invalid-parent',parent='not-a-guid PRIVATE_TEXT')
  add('duplicate-parent',parent=[PARENT,PARENT])
  add('zero-parent',parent='00000000-0000-0000-0000-000000000000')
  add('missing-parent',parent=None)
  settings=copy.deepcopy(SETTINGS); settings['knownAgents']=[BIND,{**BIND,'requiredRoles':['Gateway.Invoke.App','Extra.Role']}]
  add('ambiguous-agent',settings=settings,claims={**CLAIMS,'roles':['Gateway.Invoke.App','Extra.Role']})
  for name,bindings in [('target',[BIND]),('target-none',[]),('target-ambiguous',[BIND,{**BIND,'principalId':PARENT}])]:
   settings=copy.deepcopy(SETTINGS); settings['targetAgent']='approved-agent'; settings['knownAgents']=bindings
   add(name,settings=settings,parent=None)
  add('bad-header',headers={'apim-request-id':'PRIVATE_TOKEN','x-ms-request-id':[BACKEND,BACKEND], 'Set-Cookie':'PRIVATE_COOKIE'})
  add('alternate-id',headers={'x-request-id':'req_abcdefgh12345678'})
  add('unapproved-model',body=json.dumps({'model':'PRIVATE_UNAPPROVED_MODEL'}))
  settings=copy.deepcopy(SETTINGS); settings['backendService']='foundryServices'; settings['llmOperationIds']=[]
  add('no-metrics-source',settings=settings)
  # HTTPモデルの抽出だけを先行し、opaqueなprotocolには本文を読む式を実行しません。
  native=copy.deepcopy(SETTINGS); native['backendService']='external'; native['operationConstraints']={'native':{'kind':'registeredOperation'}}
  add('native-unread',settings=native,operation='native',httpModel=False,body='opaque PRIVATE_PROTOCOL_FRAME')
  websocket=copy.deepcopy(native); websocket['websocketModel']='approved-model'
  add('websocket-unread',settings=websocket,operation='native',httpModel=False,body='opaque PRIVATE_PROTOCOL_FRAME')
  path=copy.deepcopy(SETTINGS); path['operationConstraints']={'path':{'kind':'pathModel','parameter':'deployment','allowedModels':['approved-model']}}
  add('http-path-model',settings=path,operation='path',pathModel='approved-model')
  add('http-malformed-model',body='{malformed PRIVATE_TEXT',skipInjection=True)
  source=SOURCE
  for marker,filename,xpath,attr in [
    ('__CLAIM__','audit-context.xml','./set-variable[@name="gatewayAgentTrace"]','value'),
    ('__INJECT__','gateway.xml','./inbound/choose/when/set-body',None),
    ('__BODY_MODEL__','audit-body-model.xml','./set-variable[@name="gatewayAuditBodyModel"]','value'),
    ('__MODEL__','audit-context.xml','./set-variable[@name="gatewayAuditModel"]','value'),
    ('__BACKEND__','backend-audit.xml','./set-variable','value'),
    ('__TELEMETRY__','gateway-telemetry.xml','./trace/message',None)]:
   source=source.replace(marker,expression(filename,xpath,attr))
  with tempfile.TemporaryDirectory(prefix='core-agent-audit-') as folder:
   p=Path(folder); (p/'fixture.cs').write_text(source); (p/'cases.json').write_text(json.dumps(cases))
   (p/'run.ps1').write_text("$ErrorActionPreference='Stop'\n$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\nAdd-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'\n[AuditFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n")
   result=subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(p/'run.ps1')],capture_output=True,text=True,timeout=60,
     env={**os.environ,'POWERSHELL_TELEMETRY_OPTOUT':'1','DOTNET_CLI_TELEMETRY_OPTOUT':'1','DOTNET_BUNDLE_EXTRACT_BASE_DIR':str(p/'dotnet'),'XDG_CACHE_HOME':str(p/'cache')})
   if result.returncode: raise AssertionError(result.stderr)
   if 'PRIVATE_' in result.stdout: raise AssertionError('Synthetic private data leaked')
   cls.results=json.loads(result.stdout)

 def test_verified_subscription_records_only_a_claimed_parent_candidate(self):
  record=self.results['valid']; self.assertEqual(record['parentLinkStatus'],'candidate')
  self.assertEqual(record['claimedParentId'],PARENT); self.assertEqual(record['parentClaimActorId'],DECISION['callerId'])
  self.assertEqual(record['callerId'],DECISION['callerId']); self.assertEqual(record['subscriptionId'],'subscription-a')
  for name in ['wrong-client','wrong-principal','wrong-role','ambiguous-agent','person','multiple-oid','key']:
   self.assertEqual(self.results[name]['parentLinkStatus'],'candidate')
   self.assertEqual(self.results[name]['parentClaimActorId'],DECISION['callerId'])
   self.assertNotIn('verifiedPrincipalId',self.results[name])
   self.assertNotIn('verifiedTenantId',self.results[name])
   self.assertNotIn('verifiedClientId',self.results[name])
  for name in ['wrong-caller','wrong-method','denied','missing-subscription','empty-subscription','invalid-parent','duplicate-parent','zero-parent','missing-parent']:
   with self.subTest(name=name): self.assertNotIn('claimedParentId',self.results[name])

 def test_target_agent_name_comes_from_the_backend_definition(self):
  for name in ['target','target-none','target-ambiguous']:
   self.assertEqual(self.results[name]['targetAgent'],'approved-agent')
   self.assertNotIn('targetAgentPrincipalId',self.results[name])

 def test_backend_ids_are_saved_before_global_rewrite_without_body_reads(self):
  self.assertEqual(self.results['valid']['backendResponseIds'],{'apim-request-id':BACKEND})
  self.assertNotEqual(BACKEND,self.results['valid']['requestId'])
  self.assertEqual(self.results['bad-header']['backendResponseIds'],{})
  self.assertEqual(self.results['alternate-id']['backendResponseIds'],{'x-request-id':'req_abcdefgh12345678'})

 def test_model_and_usage_source_never_invent_usage(self):
  self.assertEqual(self.results['valid']['targetModel'],'approved-model')
  self.assertEqual(self.results['valid']['usageStatus'],'none')
  self.assertEqual(self.results['chat']['usageStatus'],'native-log-join-required')
  self.assertNotIn('usage',self.results['valid'])
  self.assertNotIn('targetModel',self.results['unapproved-model'])
  self.assertEqual(self.results['no-metrics-source']['usageStatus'],'none')

 def test_rerank_model_remains_auditable_without_llm_token_usage_or_unapproved_names(self):
  self.assertEqual(self.results['rerank']['targetModel'],'approved-model')
  self.assertEqual(self.results['rerank']['usageStatus'],'none')
  self.assertTrue(self.results['rerank']['allReadsPreserved'])
  self.assertNotIn('targetModel',self.results['rerank-unapproved'])
  self.assertNotIn('PRIVATE',json.dumps(self.results['rerank-unapproved']))

 def test_http_model_reader_preserves_the_body_but_native_common_audit_never_reads_it(self):
  """分割後の実C#でモデル名・usage方式を保ち、nativeは読取0・秘密非記録を確かめる。"""
  self.assertEqual(self.results['valid']['targetModel'],'approved-model')
  self.assertEqual(self.results['chat']['targetModel'],'approved-model')
  self.assertEqual(self.results['chat']['usageStatus'],'native-log-join-required')
  self.assertGreater(self.results['valid']['requestBodyReads'],0)
  self.assertTrue(self.results['valid']['allReadsPreserved'])
  self.assertEqual(self.results['http-path-model']['targetModel'],'approved-model')
  self.assertNotIn('targetModel',self.results['http-malformed-model'])
  for name in ('native-unread','websocket-unread'):
   self.assertEqual(self.results[name]['requestBodyReads'],0)
   self.assertEqual(self.results[name]['usageStatus'],'none')
   self.assertEqual(self.results[name]['parentLinkStatus'],'candidate')
   self.assertEqual(self.results[name]['callerId'],DECISION['callerId'])
  self.assertNotIn('targetModel',self.results['native-unread'])
  self.assertEqual(self.results['websocket-unread']['targetModel'],'approved-model')

 def test_all_protocols_strip_claims_and_capture_backend_before_base(self):
  for name in ['gateway.xml','protocol.xml','mcp.xml']:
   root=ET.parse(CORE/'apps/gateway/apim/policies'/name).getroot()
   self.assertIsNotNone(root.find('./inbound/include-fragment[@fragment-id="core-audit-context"]'))
   first=list(root.find('outbound'))[0]
   self.assertEqual(first.attrib.get('fragment-id'),'core-backend-audit')
  fragment=ET.parse(CORE/'apps/gateway/apim/policies/audit-context.xml').getroot()
  self.assertEqual(fragment.find('./set-header').attrib['exists-action'],'delete')
  self.assertIsNone(fragment.find('./choose/when/set-header'))
  self.assertEqual(self.results['valid']['injectedMetadata'],{'sample_mode':'fixed','core_parent_request_id':'66666666-6666-4666-8666-666666666666'})
  self.assertIsNone(fragment.find('.//set-body'))

if __name__=='__main__': unittest.main()
