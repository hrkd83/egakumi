"""native MCPの誤った成功包装より前に引数不正を拒否するC#式を検証します。"""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_gateway_limits_telemetry import PWSH
from arm_helpers import compiled_policy_value
from bicep_helpers import compile_bicep
from test_common_authentication import compile_inbound, csharp_expression


def generated_pii_policy():
    """厳格な合成設定から、実配備と同じMCPポリシーと制御seedを取得する。"""
    root=Path(__file__).resolve().parents[1]
    api=json.loads((root/'apps/api-gateway/apis/pii-v1.json').read_text(encoding='utf-8'))
    # Exercise configured restrictions, rather than mistaking the canonical
    # unrestricted language set and 1 MB default for these strict limits.
    api['authorization']['operationConstraints']['pii_redact_text'].update(
        maxRequestBytes=32768, allowedLanguages=['en', 'ja'])
    api.update(apiType='http', backendUrl='https://backend.example.com',
               backendAuthentication={'mode':'none'}, backendHeaders={})
    template=compile_bicep('apps/api-gateway/api-publication.bicep')
    parameters={'apiDefinitions':[api]}
    policy=ET.fromstring(compiled_policy_value(template, parameters, resource_name='mcpPolicies'))
    seed=base64.b64decode(policy.find("./inbound/set-variable[@name='gatewayApiSettingsSource']").get('value'),validate=True).decode('utf-8').split('\n')
    return policy, seed[2], template, parameters


def run_mcp_fixture(source, cases, control_settings):
    """実C#式をRoslynで実行する。APIMへの接続や入力条件の別実装は行わない。"""
    with tempfile.TemporaryDirectory()as folder:
        p=Path(folder);(p/'fixture.cs').write_text(source,encoding='utf-8')
        (p/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
        (p/'controls.json').write_text(control_settings,encoding='utf-8')
        (p/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[McpFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')),(Get-Content -Raw (Join-Path $PSScriptRoot 'controls.json')))
""",encoding='utf-8')
        return subprocess.run([PWSH,'-NoProfile','-File',str(p/'run.ps1')],text=True,capture_output=True,encoding='utf-8',timeout=60)


def mcp_error_pipeline(policy, template, parameters):
    """実on-errorのbase・rate・専用500・通常監査を、その順序で評価する。"""
    root=Path(__file__).resolve().parents[1]
    # baseには実サービスのon-errorを展開する。共通Entra分岐も実Bicep生成式を使う。
    fragments={name:ET.fromstring(compiled_policy_value(template, parameters, resource_name=resource))
               for name,resource in (
                   ('core-gateway-telemetry','gatewayTelemetryFragment'),
                   ('core-query-telemetry','queryTelemetryFragment'),
                   ('core-entra-authentication-error','entraAuthenticationErrorFragment'))}
    service=ET.fromstring((root/'apps/api-gateway/policies/service.xml').read_text(encoding='utf-8'))
    adapted=ET.Element('policies');inbound=ET.SubElement(adapted,'inbound')
    for node in policy.find('on-error'):
        if node.tag=='base':
            for child in service.find('on-error'):inbound.append(copy.deepcopy(child))
        else:inbound.append(copy.deepcopy(node))

    def expand(container):
        for position,node in reversed(list(enumerate(list(container)))):
            if node.tag=='include-fragment':
                identifier=node.get('fragment-id')
                if identifier not in fragments:raise AssertionError('Unexpected MCP error fragment: '+identifier)
                container.remove(node)
                for offset,child in enumerate(fragments[identifier]):
                    replacement=copy.deepcopy(child);expand(replacement)
                    container.insert(position+offset,replacement)
            else:expand(node)
    expand(adapted)
    # 標準return-responseの理由文・本文有無だけを合成Contextへ写す。
    # choose条件、監査式、状態とヘッダーは既存アダプターが実XMLのまま評価する。
    for container in adapted.iter():
        for position,node in reversed(list(enumerate(list(container)))):
            if node.tag=='return-response':
                container.insert(position,ET.Element('set-variable',{
                    'name':'fixtureResponseReason','value':node.find('set-status').get('reason')}))
                container.insert(position+1,ET.Element('set-variable',{
                    'name':'fixtureResponseHasBody','value':'@(true)' if node.find('set-body') is not None else '@(false)'}))
    return compile_inbound(adapted,trace_messages=True)


ERROR_CSHARP=r'''
using System;using System.Collections.Generic;using System.Linq;using System.Text;using Newtonsoft.Json;using Newtonsoft.Json.Linq;
public class Variables:Dictionary<string,object> {
 public int SettingsReads;
 public new object this[string key] {
  get {if(key=="gatewayControlSettings")SettingsReads++;return base[key];}
  set {base[key]=value;}
 }
}
public class Body {
 public string Data;public int Reads;public bool Preserved;public bool Locked;
 public T As<T>(bool preserveContent=false) {
  if(Locked)throw new Exception("SENTINEL_BODY_READ_AFTER_VALIDATION");
  Reads++;Preserved=preserveContent;return typeof(T)==typeof(string)?(T)(object)Data:(T)(object)JObject.Parse(Data);
 }
}
public class Request {public Body Body=new Body();}
public class Response {public int StatusCode=200;}
public class Named {public string Id;}
public class LastError {public string PolicyId;public string Reason;public string Source;public string Message="SENTINEL_ERROR_DETAIL";}
public class Context {
 public Guid RequestId=Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 public Request Request=new Request();public Response Response=new Response();
 public Named Api=new Named {Id="fixture-mcp"};public Named Operation=new Named {Id="pii_redact_text"};
 public Named Subscription=new Named {Id="fixture-subscription"};public LastError LastError=new LastError();
 public Variables Variables=new Variables();public Dictionary<string,string> ResponseHeaders=new Dictionary<string,string>();
 public List<string> TraceMessages=new List<string>();
}
public static class McpFixture {
 __METHODS__
 static string Check(Context context){__EXPRESSION__}
 static int Error(Context context){__ON_ERROR__ return 0;}
 public static string Run(string json,string controlSettings) {
  var output=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Request.Body.Data=(string)item["body"]??"";
   if((bool?)item["settingsMissing"]!=true) {
    var setting=item["settings"];
    c.Variables["gatewayControlSettings"]=setting==null?controlSettings:setting.Type==JTokenType.Null?null:
      setting.Type==JTokenType.String?(object)(string)setting:(object)setting;
   }
   c.Variables["gatewayAuthenticatedSubject"]=new JObject(
    new JProperty("status","verified"),new JProperty("kind","apimSubscription"),
    new JProperty("method","subscriptionKey"),new JProperty("callerId","subscription:fixture-subscription"),
    new JProperty("subscriptionId","fixture-subscription")).ToString(Formatting.None);
   c.Variables["gatewayDecision"]=new JObject(new JProperty("allowed",true),
    new JProperty("method","subscriptionKey"),new JProperty("callerId","subscription:fixture-subscription")).ToString(Formatting.None);
   c.Variables["gatewayQueryObservation"]=new JObject(new JProperty("route","mcp"),
    new JProperty("httpMethod","POST"),new JProperty("presence","present"),new JProperty("duplicates","no")).ToString(Formatting.None);
   c.Response.StatusCode=(int?)item["responseStatus"]??200;
   if((bool?)item["responseNull"]==true)c.Response=null;
   var result=new JObject(new JProperty("propagated",false),new JProperty("status",0),new JProperty("argumentError",null));
   if(item["error"] is JObject error) {
    c.LastError.PolicyId=(string)error["policyId"];c.LastError.Reason=(string)error["reason"];c.LastError.Source=(string)error["source"];
    c.Request.Body.Locked=true;result["status"]=Error(c);
   } else {
    try {result["argumentError"]=Check(c);}
    catch {
     result["propagated"]=true;
     // APIM標準engineがsetterのIDをLastErrorへ渡す箇所だけを再現する。
     c.LastError.PolicyId=__SETTER_ID__;c.LastError.Reason="ExpressionValueEvaluationFailure";c.LastError.Source="set-variable";
     c.Request.Body.Locked=true;result["status"]=Error(c);
    }
   }
   result["reason"]=c.Variables.ContainsKey("fixtureResponseReason")?(string)c.Variables["fixtureResponseReason"]:null;
   result["hasBody"]=c.Variables.ContainsKey("fixtureResponseHasBody")?(bool)c.Variables["fixtureResponseHasBody"]:false;
   result["headers"]=JObject.FromObject(c.ResponseHeaders);result["audits"]=new JArray(c.TraceMessages.Select(JObject.Parse));
   result["requestId"]=c.RequestId.ToString();result["settingsReads"]=c.Variables.SettingsReads;
   result["bodyReads"]=c.Request.Body.Reads;result["preserved"]=c.Request.Body.Preserved;
   output[(string)item["case"]]=result;
  }
  return output.ToString(Formatting.None);
 }
}'''

class McpArgumentValidationTests(unittest.TestCase):
    def test_public_argument_schema_without_reflecting_sensitive_input(self):
        if not PWSH:self.skipTest('PowerShell Roslyn is required')
        policy,control_settings,_,_=generated_pii_policy()
        value=policy.find("./inbound/set-variable[@name='gatewayMcpArgumentError']").get('value')
        expression=value[2:-1]
        def call(arguments):return {'jsonrpc':'2.0','id':17,'method':'tools/call','params':{'name':'pii_redact_text','arguments':arguments}}
        valid=call({'text':'hello','language':'en'})
        cases=[valid,call({'text':1,'language':'en'}),call({'text':'SECRET_INPUT','language':'unknown'}),call({'text':'','language':'en'}),call({'text':'hello','language':'en','extra':'SECRET_INPUT'}),call({'language':'ja'}),call({'text':'x'*32768,'language':'en'}),{'jsonrpc':'2.0','id':1,'method':'tools/list'},call({'text':'😀'*5120,'language':'en'}),call({'text':'e\u0301'*5120,'language':'en'}),call({'text':'x'*32741,'language':'en'}),call({'text':'x'*32742,'language':'en'})]
        source=r'''
using System;using System.Collections.Generic;using System.Linq;using System.Text;using Newtonsoft.Json;using Newtonsoft.Json.Linq;
public class Body {public string Data;public T As<T>(bool preserveContent=false){return typeof(T)==typeof(string)?(T)(object)Data:(T)(object)JObject.Parse(Data);}}
public class Request {public Body Body=new Body();}
public class Context {public Request Request=new Request();public Dictionary<string,object> Variables=new Dictionary<string,object>();}
public static class McpFixture {
 static string Check(Context context){__EXPRESSION__}
 public static string Run(string json,string controlSettings){var result=new JArray();foreach(var item in JArray.Parse(json)){var c=new Context();c.Variables["gatewayControlSettings"]=controlSettings;c.Request.Body.Data=item.ToString(Formatting.None);result.Add(Check(c));}return result.ToString(Formatting.None);}
}'''.replace('__EXPRESSION__',expression)
        result=run_mcp_fixture(source,cases,control_settings)
        self.assertEqual(result.returncode,0,result.stderr)
        results=json.loads(result.stdout)
        for i in [0,7,8,9,10]:self.assertEqual(results[i],'')
        for i in [1,2,3,4,5,6,11]:
            message=json.loads(results[i]);self.assertEqual(message,{'jsonrpc':'2.0','id':17,'error':{'code':-32602,'message':'Invalid tool arguments'}})
            self.assertNotIn('SECRET_INPUT',results[i])

    def test_internal_settings_failure_is_limited_safe_500(self):
        if not PWSH:self.skipTest('PowerShell Roslyn is required')
        policy,control_settings,template,parameters=generated_pii_policy()
        setter=policy.find("./inbound/set-variable[@name='gatewayMcpArgumentError']")
        self.assertEqual(setter.get('id'),'core-mcp-argument-validation')
        branch=policy.find("./on-error/choose[@id='core-mcp-argument-validation-error']/when")
        self.assertIsNotNone(branch)
        self.assertIsNone(branch.find('return-response/set-body'))
        methods,on_error=mcp_error_pipeline(policy,template,parameters)
        source=ERROR_CSHARP.replace('__METHODS__',methods).replace('__ON_ERROR__',on_error)
        source=source.replace('__EXPRESSION__',csharp_expression(setter.get('value'))).replace('__SETTER_ID__',json.dumps(setter.get('id')))
        def call(**changes):
            body={'jsonrpc':'2.0','id':17,'method':'tools/call','params':{'name':'pii_redact_text','arguments':{'text':'SECRET_INPUT','language':'en'}}}
            body.update(changes);return json.dumps(body)
        normal=call()
        cases=[
            {'case':'missing','body':normal,'settingsMissing':True},
            {'case':'null','body':normal,'settings':None},
            {'case':'object','body':normal,'settings':{'SECRET_SETTINGS':'unused'}},
            {'case':'integer','body':normal,'settings':42},
            {'case':'invalid-json','body':normal,'settings':'{SECRET_SETTINGS'},
            {'case':'json-null','body':normal,'settings':'null'},
            {'case':'json-array','body':normal,'settings':'[]'},
            {'case':'missing-null-response','body':normal,'settingsMissing':True,'responseNull':True},
            {'case':'missing-error-response','body':normal,'settingsMissing':True,'responseStatus':503},
            {'case':'invalid-input','body':'{SECRET_INPUT','settingsMissing':True},
            {'case':'array-input','body':'[]','settingsMissing':True},
            {'case':'null-input','body':'null','settingsMissing':True},
            {'case':'other-method','body':call(method='tools/list'),'settingsMissing':True},
            {'case':'notification','body':json.dumps({'jsonrpc':'2.0','method':'tools/call','params':{'name':'pii_redact_text'}}),'settingsMissing':True},
            {'case':'string-id','body':call(id='request-example'),'settingsMissing':True},
            {'case':'null-id','body':call(id=None),'settingsMissing':True},
            {'case':'float-id','body':call(id=17.5),'settingsMissing':True},
            {'case':'invalid-name','body':call(params={'name':{'SECRET_INPUT':'value'}}),'settingsMissing':True},
            {'case':'valid','body':normal},
            {'case':'unknown-tool','body':call(params={'name':'unknown_tool','arguments':{}})},
            {'case':'invalid-arguments','body':call(params={'name':'pii_redact_text','arguments':{'text':'SECRET_INPUT','language':'unknown'}})},
        ]
        malformed=json.loads(control_settings)
        malformed['operationConstraints']['pii_redact_text']['allowedLanguages']={}
        cases.append({'case':'remaining-catch','body':normal,'settings':json.dumps(malformed)})
        for name,policy_id,reason,status in (
            ('matching','core-mcp-argument-validation','ExpressionValueEvaluationFailure',200),
            ('other-id','other-argument-validation','ExpressionValueEvaluationFailure',200),
            ('null-policy-id',None,'ExpressionValueEvaluationFailure',200),
            ('empty-policy-id','','ExpressionValueEvaluationFailure',200),
            ('other-id-error-response','other-argument-validation','ExpressionValueEvaluationFailure',502),
            ('entra','core-entra-token-validation','TokenValidationFailed',200),
            ('rate','core-rate-limit','RateLimitExceeded',200),
            ('rate-other-reason','core-rate-limit','ExpressionValueEvaluationFailure',200),
        ):
            cases.append({'case':name,'error':{'policyId':policy_id,'reason':reason,'source':'set-variable'},'responseStatus':status})
        result=run_mcp_fixture(source,cases,control_settings)
        self.assertEqual(result.returncode,0,result.stderr)
        results=json.loads(result.stdout)
        failures=['missing','null','object','integer','invalid-json','json-null','json-array','missing-null-response','missing-error-response']
        for name in failures+['matching']:
            with self.subTest(case=name):
                value=results[name]
                self.assertEqual(value['propagated'],name!='matching')
                self.assertEqual((value['status'],value['reason']),(500,'Gateway processing failed'))
                self.assertFalse(value['hasBody'])
                self.assertEqual(value['headers'],{'apim-request-id':value['requestId'],'Cache-Control':'no-store'})
                gateway=[audit for audit in value['audits'] if audit['event']=='gateway-request']
                query=[audit for audit in value['audits'] if audit['event']=='query-guard']
                self.assertEqual(len(gateway),1);self.assertEqual(len(query),1)
                self.assertEqual((gateway[0]['phase'],gateway[0]['code'],gateway[0]['status']),('policy-error','OPERATION_SETTINGS_INVALID',500))
                self.assertEqual(gateway[0]['callerId'],'subscription:fixture-subscription')
                self.assertEqual((query[0]['phase'],query[0]['statusClass']),('policy-error','5xx'))
                for audit in value['audits']:
                    self.assertEqual(audit['requestId'],value['requestId'])
                    self.assertEqual(audit['authenticatedSubject']['callerId'],'subscription:fixture-subscription')
                self.assertEqual(value['settingsReads'],1 if name!='matching' else 0)
                self.assertEqual(value['bodyReads'],1 if name!='matching' else 0)
                if name!='matching':self.assertTrue(value['preserved'])
        early=['invalid-input','array-input','null-input','other-method','notification','string-id','null-id','float-id','invalid-name']
        for name in early+['valid','unknown-tool','remaining-catch']:
            with self.subTest(case=name):
                value=results[name]
                self.assertFalse(value['propagated']);self.assertEqual(value['status'],0)
                self.assertEqual(value['argumentError'],'');self.assertEqual(value['audits'],[])
                self.assertEqual(value['settingsReads'],0 if name in early else 1)
                self.assertEqual(value['bodyReads'],1);self.assertTrue(value['preserved'])
        self.assertEqual(json.loads(results['invalid-arguments']['argumentError']),
                         {'jsonrpc':'2.0','id':17,'error':{'code':-32602,'message':'Invalid tool arguments'}})
        self.assertFalse(results['invalid-arguments']['propagated'])
        for name in ['other-id','null-policy-id','empty-policy-id','other-id-error-response','rate-other-reason']:
            with self.subTest(case=name):
                value=results[name];self.assertEqual(value['status'],0)
                gateway=[audit for audit in value['audits'] if audit['event']=='gateway-request']
                self.assertEqual(len(gateway),1);self.assertEqual(gateway[0]['code'],'POLICY_ERROR')
                self.assertEqual(gateway[0]['status'],502 if name=='other-id-error-response' else 0)
                self.assertEqual(value['bodyReads'],0)
        for name,status,code in [('entra',401,'INVALID_AUTHENTICATION'),('rate',429,'RATE_LIMIT_EXCEEDED')]:
            with self.subTest(case=name):
                value=results[name];self.assertEqual(value['status'],status);self.assertFalse(value['hasBody'])
                self.assertEqual(len(value['audits']),1)
                self.assertEqual((value['audits'][0]['phase'],value['audits'][0]['code'],value['audits'][0]['status']),('rejected',code,status))
                self.assertEqual(value['headers']['Cache-Control'],'no-store')
                self.assertEqual(value['headers']['apim-request-id'],value['requestId'])
                self.assertEqual(value['bodyReads'],0)
        self.assertEqual(results['entra']['headers']['WWW-Authenticate'],'Bearer')
        self.assertEqual(results['rate']['headers']['Retry-After'],'60')
        self.assertEqual(results['rate']['headers']['X-Core-Error-Code'],'RATE_LIMIT_EXCEEDED')
        for sentinel in ['SECRET_INPUT','SECRET_SETTINGS','SENTINEL_ERROR_DETAIL','SENTINEL_BODY_READ_AFTER_VALIDATION']:
            self.assertNotIn(sentinel,json.dumps(results))

if __name__=='__main__':unittest.main()
