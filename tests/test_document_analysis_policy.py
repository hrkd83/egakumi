"""文書APIのC#ポリシーを実行し、ファイル境界・内部URL・安全な応答を検証する。"""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_gateway_limits_telemetry import PWSH, expression
from test_standard_apis import standard_fixture
from deployment.helpers.configuration import normalize_configuration

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'apps/gateway/apim/policies/document-analysis.xml'
API = json.loads((ROOT / 'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8'))
SETTINGS = API['authorization']['operationConstraints']['analyze_document']
BASE = 'https://aif-test.cognitiveservices.azure.com'
LOCATION = BASE + '/documentintelligence/documentModels/prebuilt-layout/analyzeResults/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?api-version=2024-11-30'

CSHARP = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public sealed class Body {
  public byte[] Data;
  public T As<T>(bool preserveContent=false) {
    return (T)(typeof(T)==typeof(byte[]) ? (object)Data : (object)JObject.Parse(Encoding.UTF8.GetString(Data)));
  }
}
public sealed class Request { public Body Body; public Url Url=new Url(); public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public sealed class Url { public Dictionary<string,string[]> Query=new Dictionary<string,string[]>(); }
public interface IResponse { int StatusCode {get;set;} Body Body {get;set;} Dictionary<string,string[]> Headers {get;set;} }
public sealed class Response:IResponse { public int StatusCode {get;set;} public Body Body {get;set;} public Dictionary<string,string[]> Headers {get;set;}=new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public static class Extensions { public static string GetValueOrDefault(this Dictionary<string,string[]> source,string key,string fallback) { return source.ContainsKey(key)?String.Join(" ",source[key]):fallback; } }
public sealed class Context { public Dictionary<string,object> Variables=new Dictionary<string,object>(); public Request Request=new Request(); public Guid RequestId=Guid.Parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"); }
public static class DocumentPolicyFixture {
  public static int Input(Context context) { __INPUT__ }
  public static string PollUrl(Context context) { __URL__ }
  public static string Evaluate(Context context) { __EVALUATE__ }
  public static string Run(string casesJson) {
    var results=new JObject();
    foreach(JObject item in JArray.Parse(casesJson)) {
      var context=new Context();
      context.Variables["documentSettings"]=__SETTINGS__;
      context.Variables["documentBaseUrl"]=__BASE__;
      context.Variables["documentModel"]=(string)item["model"]??"prebuilt-layout";
      var mode=(string)item["mode"]??"input";
      if(mode=="input") {
        context.Request.Body=new Body{Data=Convert.FromBase64String((string)item["data"]??"")};
        context.Request.Headers["Content-Type"]=new[]{(string)item["mime"]??"application/pdf"};
        if(item["encoding"]!=null) context.Request.Headers["Content-Encoding"]=new[]{(string)item["encoding"]};
        context.Request.Url.Query["model"]=((JArray)(item["models"]??new JArray(context.Variables["documentModel"]))).Select(v=>(string)v).ToArray();
        if(item["extraQuery"]!=null) context.Request.Url.Query[(string)item["extraQuery"]]=new[]{"untrusted"};
        results[(string)item["id"]]=Input(context);
      } else {
        var response=new Response{StatusCode=(int?)item["status"]??200,Body=new Body{Data=Encoding.UTF8.GetBytes((string)item["body"]??"{}")}};
        response.Headers["Operation-Location"]=new[]{(string)item["location"]??""};
        if(item["publicBytes"]!=null || item["nativeBytes"]!=null) {
          var analysis=new JObject(new JProperty("apiVersion","2024-11-30"),new JProperty("modelId","prebuilt-layout"),new JProperty("content",""),new JProperty("pages",new JArray(new JObject(new JProperty("pageNumber",1)))));
          var native=new JObject(new JProperty("status","succeeded"),new JProperty("analyzeResult",analysis));
          var projected=new JObject(new JProperty("analyzeResult",analysis),new JProperty("requestId",context.RequestId.ToString()));
          var budget=item["publicBytes"]??item["nativeBytes"];
          var envelope=item["publicBytes"]!=null?projected:native;
          int overhead=Encoding.UTF8.GetByteCount(envelope.ToString(Formatting.None));
          analysis["content"]=new string('x',(int)budget-overhead);
          // JObjectは子を複製するため、両envelopeへ更新した同じ解析値を設定します。
          native["analyzeResult"]=analysis;
          response.Body.Data=Encoding.UTF8.GetBytes(native.ToString(Formatting.None));
        }
        context.Variables[mode=="url"?"documentStartResponse":"documentPollResponse"]=(bool?)item["nullResponse"]==true?null:response;
        results[(string)item["id"]]=mode=="url"?new JValue(PollUrl(context)):(JToken)JObject.Parse(Evaluate(context));
      }
    }
    return results.ToString(Formatting.None);
  }
}
'''


def analysis_result(**changes):
    """OCR結果だけを含む最小の合成した上流レスポンスです。"""
    value = {'apiVersion': '2024-11-30', 'modelId': 'prebuilt-layout', 'content': 'synthetic OCR', 'pages': [{'pageNumber': 1}], 'tables': []}
    value.update(changes)
    return {'status': 'succeeded', 'analyzeResult': value, 'untrustedTopLevel': 'SENTINEL_PRIVATE'}


class DocumentAnalysisPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest('PowerShell is required for offline C# execution')
        pdf = base64.b64encode(b'%PDF-1.7\nsynthetic').decode()
        cls.cases = [{'id': 'valid', 'data': pdf},
            {'id': 'oversize', 'data': base64.b64encode(b'%PDF-' + b' ' * 1048576).decode()},
            {'id': 'empty'}, {'id': 'wrong-mime', 'data': pdf, 'mime': 'image/png'},
            {'id': 'unknown-mime', 'data': pdf, 'mime': 'text/plain'},
            {'id': 'wrong-model', 'data': pdf, 'model': 'custom-user-model'},
            {'id': 'duplicate-model', 'data': pdf, 'models': ['prebuilt-layout', 'prebuilt-read']},
            {'id': 'url-input', 'data': pdf, 'extraQuery': 'urlSource'},
            {'id': 'compressed', 'data': pdf, 'encoding': 'gzip'}]
        for name, location in [('url-valid', LOCATION), ('url-host', LOCATION.replace('aif-test.', 'other.')),
                ('url-port', LOCATION.replace('.azure.com/', '.azure.com:444/')),
                ('url-user', LOCATION.replace('https://', 'https://user@')),
                ('url-model', LOCATION.replace('prebuilt-layout', 'prebuilt-read')),
                ('url-id', LOCATION.replace('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'secret/path')),
                ('url-query', LOCATION + '&secret=bad'), ('url-fragment', LOCATION + '#private')]:
            cls.cases.append({'id': name, 'mode': 'url', 'status': 202, 'location': location})
        for name, body, status in [('success', analysis_result(), 200), ('running', {'status':'running'}, 200),
                ('failed', {'status':'failed','error':{'message':'SENTINEL_PRIVATE'}}, 200),
                ('wrong-version', analysis_result(apiVersion='2025-01-01'), 200),
                ('wrong-model-result', analysis_result(modelId='prebuilt-read'), 200),
                ('wrong-shape', analysis_result(pages='private'), 200),
                ('throttle', {'error':{'message':'SENTINEL_PRIVATE'}}, 429),
                ('server-error', {'error':{'message':'SENTINEL_PRIVATE'}}, 500)]:
            cls.cases.append({'id':name,'mode':'result','body':json.dumps(body),'status':status})
        cls.cases.append({'id':'null-result','mode':'result','nullResponse':True})
        for delta in (-1, 0, 1):
            cls.cases.append({'id':f'public-bytes-{delta}', 'mode':'result', 'publicBytes':SETTINGS['maxResponseBytes']+delta})
        cls.cases.append({'id':'native-oversize','mode':'result','nativeBytes':SETTINGS['maxResponseBytes']+1})
        source = CSHARP.replace('__INPUT__', expression(POLICY, './inbound/set-variable[@name="documentInputStatus"]', 'value'))
        source = source.replace('__URL__', expression(POLICY, './inbound/set-variable[@name="documentPollUrl"]', 'value'))
        source = source.replace('__EVALUATE__', expression(POLICY, './/set-variable[@name="documentPollEvaluation"]', 'value'))
        source = source.replace('__SETTINGS__', json.dumps(json.dumps(SETTINGS))).replace('__BASE__', json.dumps(BASE))
        with tempfile.TemporaryDirectory(prefix='core-document-policy-') as directory:
            folder=Path(directory)
            (folder/'fixture.cs').write_text(source,encoding='utf-8')
            (folder/'cases.json').write_text(json.dumps(cls.cases),encoding='utf-8')
            (folder/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName) + @([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[DocumentPolicyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding='utf-8')
            run=subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(folder/'run.ps1')],capture_output=True,text=True,timeout=60)
            if run.returncode:
                raise AssertionError('Document policy C# compilation/execution failed:\n'+run.stderr)
            cls.results=json.loads(run.stdout)

    def test_binary_and_model_inputs_are_validated_before_native_post(self):
        self.assertEqual(self.results['valid'],200)
        for name in ('empty','wrong-mime','wrong-model','duplicate-model','url-input'):
            self.assertEqual(self.results[name],400,name)
        self.assertEqual(self.results['oversize'],413)
        self.assertEqual(self.results['unknown-mime'],415)
        self.assertEqual(self.results['compressed'],415)

    def test_operation_location_cannot_point_to_other_resource_or_arbitrary_path(self):
        self.assertEqual(self.results['url-valid'],LOCATION)
        for name in ('url-host','url-port','url-user','url-model','url-id','url-query','url-fragment'):
            self.assertEqual(self.results[name],'',name)

    def test_only_validated_native_analysis_result_is_returned(self):
        result=self.results['success']
        self.assertEqual(result['status'],200)
        body=json.loads(result['result'])
        self.assertEqual(set(body),{'analyzeResult','requestId'})
        self.assertEqual(body['analyzeResult']['content'],'synthetic OCR')
        self.assertNotIn('SENTINEL',result['result'])
        for name,status in [('failed',422),('wrong-version',502),('wrong-model-result',502),('wrong-shape',502),('throttle',429),('server-error',502),('null-result',504)]:
            self.assertEqual(self.results[name]['status'],status,name)
            self.assertEqual(self.results[name]['result'],'',name)
        self.assertEqual(self.results['running']['state'],'running')

    def test_polling_never_repeats_billable_post_or_exposes_result_handle(self):
        tree=ET.parse(POLICY)
        self.assertEqual([node.text for node in tree.findall('.//retry//set-method')],['GET'])
        self.assertEqual([node.text for node in tree.findall('./inbound/send-request/set-method')],['POST'])
        self.assertIsNone(tree.find('.//trace'))
        self.assertIsNone(tree.find('.//set-header[@name="Operation-Location"]'))
        self.assertIn('documentStartedAt',tree.find('.//retry').get('condition'))
        self.assertLessEqual(int(tree.find('.//retry').get('count')),50)

    def test_response_budget_includes_the_final_public_envelope(self):
        for delta in (-1, 0):
            result = self.results[f'public-bytes-{delta}']
            self.assertEqual(result['status'], 200)
            self.assertEqual(len(result['result'].encode('utf-8')), SETTINGS['maxResponseBytes'] + delta)
        for name in ('public-bytes-1', 'native-oversize'):
            self.assertEqual(self.results[name]['status'], 502)
            self.assertEqual(self.results[name]['result'], '')


class DocumentAnalysisConfigurationTests(unittest.TestCase):
    def test_document_contract_uses_existing_foundry_and_exact_path_waf_allowance(self):
        raw, _ = standard_fixture()
        raw['gateway']['wafMaxRequestBodySizeInKb'] = 128
        raw['gateway']['wafRequestBodyInspectLimitInKb'] = 128
        raw['apiDefinitions'] = [copy.deepcopy(API)]
        normalized = normalize_configuration(raw)
        self.assertEqual(normalized['apiDefinitions'][0]['backendService'],'foundryCognitive')
        self.assertEqual(normalized['gateway']['wafMaxRequestBodySizeInKb'],128)
        self.assertEqual(normalized['apiDefinitions'][0]['authorization']['operationConstraints']['analyze_document']['maxRequestBytes'],1048576)

    def test_model_route_auth_and_bounds_cannot_drift(self):
        for target,value in [('model',['other']),('bytes',1048577),('response',4194305),('timeout',61),('version','preview'),('path','tools/other/v1'),('backend','foundryOpenAi')]:
            raw,_=standard_fixture()
            api=copy.deepcopy(API)
            constraint=api['authorization']['operationConstraints']['analyze_document']
            field={'model':'allowedModels','bytes':'maxRequestBytes','response':'maxResponseBytes','timeout':'timeoutSeconds','version':'apiVersion'}.get(target)
            if field: constraint[field]=value
            else: api['path' if target=='path' else 'backendService']=value
            raw['apiDefinitions']=[api]
            with self.subTest(target=target),self.assertRaises(ValueError):
                normalize_configuration(raw)


if __name__ == '__main__':
    unittest.main()
