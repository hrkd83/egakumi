"""実生成protocol式の入力条件を保ち、内部設定不備だけを固定500へ分ける。"""
import base64
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import compiled_policy_value
from bicep_helpers import compile_bicep
from test_api_protocols import bound_external_coverage, configuration
from test_common_authentication import compile_inbound
from test_gateway_limits_telemetry import PWSH
from test_mcp_argument_validation import ERROR_CSHARP, mcp_error_pipeline


MODEL_ID='core-protocol-model-selection'
QUERY_ID='core-protocol-query-validation'


def generated_protocols():
    core=Path(__file__).resolve().parents[1]
    definitions=configuration.normalize_configuration(bound_external_coverage(),base_directory=core/'config')['apiDefinitions']
    selected={
        'dynamic':next(api for api in definitions if api['apiType']=='websocket' and 'defaultModel' in api['websocket']),
        'fixed':next(api for api in definitions if api['apiType']=='websocket' and 'defaultModel' not in api['websocket'] and 'model' in api['websocket'].get('queryParameters',{})),
        'mcp':next(api for api in definitions if api['apiType']=='mcp'),
    }
    selected['dynamic-single']=copy.deepcopy(selected['dynamic'])
    selected['dynamic-single']['websocket']['allowedModels']=[selected['dynamic-single']['websocket']['defaultModel']]
    template=compile_bicep('apps/apim/api-publication.bicep')
    result={}
    for label,api in selected.items():
        api.setdefault('backendUrl','https://provider.example.com'+api.get('backendPath',''))
        parameters={'apiDefinitions':[api]}
        policy=ET.fromstring(compiled_policy_value(template,parameters))
        result[label]=(api,policy,template,parameters)
    return result


def query_pipeline(policy, template, parameters, fault=None):
    """実setter・拒否分岐・固定query writerを抽出し、標準query上書きだけを模擬する。"""
    root=ET.Element('policies');inbound=ET.SubElement(root,'inbound')
    writers={}
    for name in ('gatewayProtocolModel','gatewayProtocolQueryAllowed'):
        setter=copy.deepcopy(policy.find('./inbound/set-variable[@name="'+name+'"]'))
        ET.SubElement(inbound,'set-variable',{'name':'fixtureCurrentPolicyId','value':setter.get('id')})
        if fault is not None and fault[0]==name:
            value=setter.get('value')
            encoded=re.search(r'var encoded = "([^\"]*)";',value)
            if encoded is None:raise AssertionError('Missing generated internal Base64 literal')
            setter.set('value',value[:encoded.start(1)]+fault[1]+value[encoded.end(1):])
        inbound.append(setter)
    ET.SubElement(inbound,'set-variable',{'name':'fixtureCurrentPolicyId','value':''})
    section=policy.find('inbound');children=list(section)
    rejection=next(node for node in children if node.tag=='choose' and
                   'gatewayProtocolQueryAllowed' in (node.find('when').get('condition','') if node.find('when') is not None else ''))
    audit_context=section.find("include-fragment[@fragment-id='core-audit-context']")
    if children.index(rejection)>=children.index(audit_context):
        raise AssertionError('Audit context must follow protocol query validation')
    rejection_value=copy.deepcopy(rejection)
    when=rejection_value.find('when')
    audit=when.find("include-fragment[@fragment-id='core-gateway-telemetry']")
    if audit is not None:
        fragment=ET.fromstring(compiled_policy_value(template,parameters,resource_name='gatewayTelemetryFragment'))
        position=list(when).index(audit)
        when.remove(audit)
        for offset,node in enumerate(fragment):
            when.insert(position+offset,node)
    inbound.append(rejection_value)
    for node in children[children.index(rejection)+1:]:
        if node.tag!='set-query-parameter':break
        if node.get('exists-action')!='override':raise AssertionError('Unexpected protocol query writer')
        name='fixtureQueryWriter'+str(len(writers));writers[name]=node.get('name')
        ET.SubElement(inbound,'set-variable',{'name':name,'value':node.find('value').text})
    def after_variable(name):
        if name not in writers:return ''
        return 'context.Request.Url.Query['+json.dumps(writers[name])+']=new [] {(string)context.Variables['+json.dumps(name)+']};'
    return compile_inbound(root,after_variable=after_variable,trace_messages=True)


FIXTURE=ERROR_CSHARP.split('public static class McpFixture {',1)[0].replace(
    'public class Request {public Body Body=new Body();}',
    'public class Url {public Dictionary<string,string[]> Query=new Dictionary<string,string[]>();}\n'
    'public class Request {public Body Body=new Body();public Url Url=new Url();}')+r'''
public static class ProtocolFixture {
 __METHODS__
 static int Evaluate(Context context,string name) {__SELECTORS__ throw new Exception("Unknown protocol fixture");}
 static int Error(Context context,string name) {__ERROR_SELECTORS__ throw new Exception("Unknown protocol error fixture");}
 public static string Run(string json) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Request.Body.Locked=true;c.Api.Id="fixture-protocol";c.Operation.Id="onHandshake";
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties())
    c.Request.Url.Query[entry.Name]=entry.Value is JArray?entry.Value.Select(v=>(string)v).ToArray():new [] {(string)entry.Value};
   c.Variables["gatewayAuthenticatedSubject"]=new JObject(new JProperty("status","verified"),new JProperty("kind","apimSubscription"),
    new JProperty("method","subscriptionKey"),new JProperty("callerId","subscription:fixture-subscription"),new JProperty("subscriptionId","fixture-subscription")).ToString(Formatting.None);
   c.Variables["gatewayDecision"]=new JObject(new JProperty("allowed",true),new JProperty("method","subscriptionKey"),
    new JProperty("callerId","subscription:fixture-subscription")).ToString(Formatting.None);
   // WSにも有効な観測を故障注入するが、既存MCP限定のquery監査を増やさない。
   c.Variables["gatewayQueryObservation"]=new JObject(new JProperty("route","mcp"),new JProperty("httpMethod","GET"),
    new JProperty("presence","none"),new JProperty("duplicates","no")).ToString(Formatting.None);
   c.Response.StatusCode=(int?)item["responseStatus"]??200;
   if((bool?)item["responseNull"]==true)c.Response=null;
   var propagated=false;var status=0;
   if(item["error"] is JObject error) {
    c.LastError.PolicyId=(string)error["policyId"];c.LastError.Reason=(string)error["reason"];c.LastError.Source="set-variable";
    status=Error(c,(string)item["protocol"]);
   } else {
    try {status=Evaluate(c,(string)item["pipeline"]);}
    catch {
     propagated=true;c.LastError.PolicyId=(string)c.Variables["fixtureCurrentPolicyId"];
     c.LastError.Reason="ExpressionValueEvaluationFailure";c.LastError.Source="set-variable";
     status=Error(c,(string)item["protocol"]);
    }
   }
   results[(string)item["id"]]=new JObject(new JProperty("propagated",propagated),new JProperty("status",status),
    new JProperty("policyId",c.LastError.PolicyId),new JProperty("requestId",c.RequestId.ToString()),
    new JProperty("model",c.Variables.ContainsKey("gatewayProtocolModel")?c.Variables["gatewayProtocolModel"]:null),
    new JProperty("queryAllowed",c.Variables.ContainsKey("gatewayProtocolQueryAllowed")?c.Variables["gatewayProtocolQueryAllowed"]:null),
    new JProperty("queryAfter",JObject.FromObject(c.Request.Url.Query)),new JProperty("bodyReads",c.Request.Body.Reads),
    new JProperty("reason",c.Variables.ContainsKey("fixtureResponseReason")?c.Variables["fixtureResponseReason"]:null),
    new JProperty("hasBody",c.Variables.ContainsKey("fixtureResponseHasBody")&&(bool)c.Variables["fixtureResponseHasBody"]),
    new JProperty("headers",JObject.FromObject(c.ResponseHeaders)),new JProperty("audits",new JArray(c.TraceMessages.Select(JObject.Parse))));
  }
  return results.ToString(Formatting.None);
 }
}'''


def run_cases(protocols, pipelines, cases):
    methods=[];selectors=[];error_selectors=[]
    def rename(value,prefix):
        # アダプターのExpression番号だけを変え、RegularExpressions等の実式を保持します。
        return re.sub(r'\bExpression(\d+)\b',lambda match:prefix+match[1],value)
    for index,(name,protocol,fault) in enumerate(pipelines):
        _,policy,template,parameters=protocols[protocol]
        actual,body=query_pipeline(policy,template,parameters,fault)
        prefix='Protocol'+str(index)+'Expression'
        methods.append(rename(actual,prefix))
        selectors.append('if(name=='+json.dumps(name)+') {'+rename(body,prefix)+' return 200;}')
    for index,(label,(_,policy,template,parameters)) in enumerate(protocols.items()):
        actual,body=mcp_error_pipeline(policy,template,parameters)
        prefix='ProtocolError'+str(index)+'Expression'
        methods.append(rename(actual,prefix))
        error_selectors.append('if(name=='+json.dumps(label)+') {'+rename(body,prefix)+' return 0;}')
    source=FIXTURE.replace('__METHODS__','\n'.join(methods)).replace('__SELECTORS__','\n'.join(selectors)).replace('__ERROR_SELECTORS__','\n'.join(error_selectors))
    with tempfile.TemporaryDirectory()as directory:
        folder=Path(directory);(folder/'fixture.cs').write_text(source,encoding='utf-8')
        (folder/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
        (folder/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[ProtocolFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding='utf-8')
        return subprocess.run([PWSH,'-NoProfile','-File',str(folder/'run.ps1')],capture_output=True,text=True,encoding='utf-8',timeout=60)


class ProtocolSettingsErrorTests(unittest.TestCase):
    def test_generated_model_selection_and_fixed_query_results_are_preserved(self):
        if not PWSH:self.skipTest('PowerShell Roslyn is required')
        protocols=generated_protocols();cases=[];expected={}
        def add(label,name,query,model,status,query_after):
            identifier=label+'-'+name;cases.append({'id':identifier,'protocol':label,'pipeline':label,'query':query})
            expected[identifier]=(model,status,query_after)
        default=protocols['dynamic'][0]['websocket']['defaultModel']
        models=protocols['dynamic'][0]['websocket']['allowedModels']
        self.assertGreater(len(models),1)
        add('dynamic','default',{},default,200,{'model':[default]})
        add('dynamic','listed-model',{'model':models[-1]},models[-1],200,{'model':[models[-1]]})
        add('dynamic','future',{'model':'future-synthetic-model'},'future-synthetic-model',200,{'model':['future-synthetic-model']})
        add('dynamic','empty',{'model':''},'',403,{'model':['']})
        add('dynamic','blank',{'model':' '},' ',200,{'model':[' ']})
        add('dynamic','duplicate',{'model':['one','two']},'!invalid-model!',403,{'model':['one','two']})
        add('dynamic','unknown-query',{'unknown':'synthetic'},default,403,{'unknown':['synthetic']})
        fixed=protocols['fixed'][0]['websocket']['queryParameters'];fixed_after={key:[value] for key,value in fixed.items()}
        add('fixed','default',{},'',200,fixed_after)
        add('fixed','overwrite',{'model':'future-synthetic-model'},'',200,fixed_after)
        add('fixed','empty',{'model':''},'',200,fixed_after)
        add('fixed','duplicate',{'model':['one','two']},'',403,{'model':['one','two']})
        add('fixed','unknown-query',{'unknown':'synthetic'},'',403,{'unknown':['synthetic']})
        key=next(iter(fixed));add('fixed','fixed-value',{key:'client-value'},'',200,fixed_after)
        add('mcp','none',{},'',200,{})
        add('mcp','unrestricted',{'session_id':['one','two'],'future':'synthetic'},'',200,{'session_id':['one','two'],'future':['synthetic']})
        add('dynamic-single','default',{},default,200,{'model':[default]})
        add('dynamic-single','future',{'model':'future-synthetic-model'},'future-synthetic-model',200,{'model':['future-synthetic-model']})
        pipelines=[(label,label,None) for label in protocols]
        for label in protocols:
            for variable in ('gatewayProtocolModel','gatewayProtocolQueryAllowed'):
                identifier=label+'-empty-'+variable
                pipelines.append((identifier,label,(variable,'')))
                cases.append({'id':identifier,'protocol':label,'pipeline':identifier})
                model='' if variable=='gatewayProtocolModel' or label in ('fixed','mcp') else default
                query_after=fixed_after if label=='fixed' else {} if label=='mcp' else {'model':[model]}
                expected[identifier]=(model,200,query_after)
        result=run_cases(protocols,pipelines,cases)
        self.assertEqual(result.returncode,0,result.stderr)
        values=json.loads(result.stdout)
        for identifier,(model,status,query_after) in expected.items():
            with self.subTest(case=identifier):
                value=values[identifier];self.assertFalse(value['propagated'])
                self.assertEqual((value['model'],value['status']),(model,status))
                self.assertEqual(value['queryAllowed'],status==200);self.assertEqual(value['queryAfter'],query_after)
                self.assertEqual(value['bodyReads'],0)
                if status==403:
                    self.assertEqual(value['headers'],{'apim-request-id':value['requestId']})
                    audit,=value['audits']
                    self.assertEqual((audit['phase'],audit['code'],audit['status']),('rejected','INVALID_PROTOCOL_QUERY',403))
                    self.assertEqual(audit['requestId'],value['requestId'])
                    self.assertNotIn('targetModel',audit)
                else:
                    self.assertEqual(value['audits'],[])

    def test_internal_base64_json_errors_and_only_two_policy_ids_use_safe_500(self):
        if not PWSH:self.skipTest('PowerShell Roslyn is required')
        protocols=generated_protocols();pipelines=[];cases=[];failures={};direct={}
        invalid=[('base64','!invalid-base64!')]+[(name,base64.b64encode(value.encode()).decode())
            for name,value in [('json','{SECRET_INTERNAL_SETTINGS'),('json-null','null'),('json-array','[]')]]
        for label,(_,policy,_,_) in protocols.items():
            for variable,policy_id in [('gatewayProtocolModel',MODEL_ID),('gatewayProtocolQueryAllowed',QUERY_ID)]:
                self.assertEqual(policy.find('./inbound/set-variable[@name="'+variable+'"]').get('id'),policy_id)
                for name,encoded in invalid:
                    identifier=label+'-'+variable+'-'+name
                    pipelines.append((identifier,label,(variable,encoded)))
                    cases.append({'id':identifier,'protocol':label,'pipeline':identifier})
                    failures[identifier]=(label,policy_id,True)
            for name,policy_id,reason,status in (
                ('model-id',MODEL_ID,'ExpressionValueEvaluationFailure',200),
                ('query-id',QUERY_ID,'ExpressionValueEvaluationFailure',200),
                ('similar-id',MODEL_ID+'-other','ExpressionValueEvaluationFailure',200),
                ('mcp-id','core-mcp-argument-validation','ExpressionValueEvaluationFailure',200),
                ('null-id',None,'ExpressionValueEvaluationFailure',200),
                ('empty-id','','ExpressionValueEvaluationFailure',200),
                ('other-status','other-policy','ExpressionValueEvaluationFailure',502),
                ('entra','core-entra-token-validation','TokenValidationFailed',200),
                ('rate','core-rate-limit','RateLimitExceeded',200),
                ('rate-other-reason','core-rate-limit','ExpressionValueEvaluationFailure',200),
            ):
                identifier=label+'-'+name
                cases.append({'id':identifier,'protocol':label,'responseStatus':status,'error':{'policyId':policy_id,'reason':reason}})
                if policy_id in (MODEL_ID,QUERY_ID):failures[identifier]=(label,policy_id,False)
                else:direct[identifier]=(label,name,status)
            for name,patch in [('null-response',{'responseNull':True}),('error-response',{'responseStatus':503})]:
                identifier=label+'-'+name
                cases.append({'id':identifier,'protocol':label,'error':{'policyId':QUERY_ID,'reason':'ExpressionValueEvaluationFailure'},**patch})
                failures[identifier]=(label,QUERY_ID,False)
        result=run_cases(protocols,pipelines,cases)
        self.assertEqual(result.returncode,0,result.stderr);values=json.loads(result.stdout)
        for identifier,(label,policy_id,propagated) in failures.items():
            with self.subTest(case=identifier):
                value=values[identifier]
                self.assertEqual((value['propagated'],value['policyId']),(propagated,policy_id))
                self.assertEqual((value['status'],value['reason']),(500,'Gateway processing failed'));self.assertFalse(value['hasBody'])
                self.assertEqual(value['headers'],{'apim-request-id':value['requestId'],'Cache-Control':'no-store'})
                gateway=[audit for audit in value['audits'] if audit['event']=='gateway-request']
                query=[audit for audit in value['audits'] if audit['event']=='query-guard']
                self.assertEqual(len(gateway),1);self.assertEqual(len(query),1 if label=='mcp' else 0)
                self.assertEqual((gateway[0]['phase'],gateway[0]['code'],gateway[0]['status']),('policy-error','OPERATION_SETTINGS_INVALID',500))
                self.assertEqual(gateway[0]['callerId'],'subscription:fixture-subscription')
                if query:self.assertEqual((query[0]['phase'],query[0]['statusClass']),('policy-error','5xx'))
                for audit in value['audits']:self.assertEqual(audit['requestId'],value['requestId'])
        for identifier,(label,name,response_status) in direct.items():
            with self.subTest(case=identifier):
                value=values[identifier];self.assertFalse(value['propagated'])
                gateway=[audit for audit in value['audits'] if audit['event']=='gateway-request']
                self.assertEqual(len(gateway),1)
                if name in ('entra','rate'):
                    status=401 if name=='entra' else 429;code='INVALID_AUTHENTICATION' if name=='entra' else 'RATE_LIMIT_EXCEEDED'
                    self.assertEqual(value['status'],status);self.assertFalse(value['hasBody']);self.assertEqual(len(value['audits']),1)
                    self.assertEqual((gateway[0]['phase'],gateway[0]['code'],gateway[0]['status']),('rejected',code,status))
                    self.assertEqual(value['headers']['Cache-Control'],'no-store')
                    self.assertEqual(value['headers']['apim-request-id'],value['requestId'])
                    self.assertEqual(value['headers']['WWW-Authenticate'] if name=='entra' else value['headers']['Retry-After'],'Bearer' if name=='entra' else '60')
                else:
                    self.assertEqual(value['status'],0);self.assertEqual(gateway[0]['code'],'POLICY_ERROR')
                    self.assertEqual(gateway[0]['status'],response_status if response_status>=400 else 0)
        for value in values.values():self.assertEqual(value['bodyReads'],0)
        for sentinel in ('SECRET_INTERNAL_SETTINGS','SENTINEL_ERROR_DETAIL','SENTINEL_BODY_READ_AFTER_VALIDATION'):
            self.assertNotIn(sentinel,json.dumps(values))


if __name__=='__main__':unittest.main()
