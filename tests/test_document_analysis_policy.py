"""実APIM式で文書の非同期受付・所有者チケット・結果の安全な取得を検証する。"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from test_gateway_limits_telemetry import PWSH
from test_standard_apis import standard_fixture
from deployment.helpers.configuration import normalize_configuration

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / 'apps/gateway/apim/policies/document-analysis.xml'
STATUS = ROOT / 'apps/gateway/apim/policies/document-analysis-status.xml'
API = json.loads((ROOT / 'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8'))
SETTINGS = {**API['authorization']['operationConstraints']['analyze_document'],
            'maxRequestBytes': 2 * 1024 * 1024, 'maxResponseBytes': 32768}
BASE = 'https://documents.example.com'
JOB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
LOCATION = BASE + '/documentintelligence/documentModels/prebuilt-layout/analyzeResults/' + JOB + '?api-version=2024-11-30'
TEST_KEY = 'synthetic-document-signing-key-for-offline-tests'


def statement(path, xpath, attribute=None):
    """実XMLの単文・複文式を、テスト用C#メソッドの本体として読み込む。"""
    node = ET.parse(path).find(xpath)
    value = (node.get(attribute) if attribute else node.text).strip()
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
public sealed class Context {
 public Dictionary<string,object> Variables=new Dictionary<string,object>();
 public Request Request=new Request(); public Response Response;
 public Named Api=new Named{Id="synthetic-document-api"}; public Named Subscription=new Named{Id="synthetic-owner"};
 public Guid RequestId=Guid.Parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
}
public static class Extensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> source,string key,string fallback) { return source.ContainsKey(key)?String.Join(" ",source[key]):fallback; }
 public static string GetValueOrDefault(this Dictionary<string,string> source,string key,string fallback) { return source.ContainsKey(key)?source[key]:fallback; }
}
public static class DocumentFixture {
 static int Input(Context context) { __INPUT__ }
 static string OperationId(Context context) { __OPERATION_ID__ }
 static string Accepted(Context context) { __ACCEPTED__ }
 static int StartStatus(Context context) { __START_STATUS__ }
 static int StartRetry(Context context) { __START_RETRY__ }
 static string StartBody(Context context) { __START_BODY__ }
 static string Owner(Context context) { __OWNER__ }
 static int OwnerStatus(Context context) { __OWNER_STATUS__ }
 static string Evaluate(Context context) { __EVALUATE__ }
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
  c.Variables["documentSettings"]=__SETTINGS__;
  c.Variables["documentBaseUrl"]=__BASE__;
  c.Variables["documentModel"]=(string)item["model"]??"prebuilt-layout";
  c.Variables["documentOperationId"]=__JOB__;
  c.Variables["documentStartedAt"]=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
  c.Response=new Response{StatusCode=(int?)item["status"]??202,Body=new Body{Data=Encoding.UTF8.GetBytes(item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None))}};
  c.Response.Headers["Operation-Location"]=new[]{(string)item["location"]??__LOCATION__};
  c.Response.Headers["Retry-After"]=new[]{(string)item["retry"]??""};
  if ((bool?)item["nullResponse"]==true) c.Response=null;
  return c;
 }
 public static string Run(string input) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var c=New(item); var mode=(string)item["mode"]??"input"; var name=(string)item["id"];
   if(mode=="input") {
    var bytes=Convert.FromBase64String((string)item["data"]??"");
    if(item["requestSize"]!=null) { bytes=Enumerable.Repeat((byte)32,(int)item["requestSize"]).ToArray(); Encoding.ASCII.GetBytes("%PDF-").CopyTo(bytes,0); }
    c.Request.Body=(bool?)item["noBody"]==true?null:new Body{Data=bytes};
    c.Request.Headers["Content-Type"]=new[]{(string)item["mime"]??"application/pdf"};
    if(item["encoding"]!=null)c.Request.Headers["Content-Encoding"]=new[]{(string)item["encoding"]};
    c.Request.Url.Query["model"]=((JArray)(item["models"]??new JArray(c.Variables["documentModel"]))).Select(v=>(string)v).ToArray();
    if((bool?)item["noModelQuery"]==true)c.Request.Url.Query.Clear();
    if(item["extraQuery"]!=null)c.Request.Url.Query[(string)item["extraQuery"]]=new[]{"untrusted"};
    var decision=Input(c);
    results[name]=new JObject(new JProperty("status",decision),new JProperty("reads",c.Request.Body==null?0:c.Request.Body.Reads),new JProperty("preserved",c.Request.Body==null||c.Request.Body.Preserved));
   } else if(mode=="location") results[name]=OperationId(c);
   else if(mode=="start") {
    c.Variables["documentOperationId"]=OperationId(c);
    if((bool?)item["anonymous"]==true)c.Subscription=null;
    c.Variables["documentAccepted"]=Accepted(c);
    c.Variables["documentStatus"]=StartStatus(c);
    results[name]=new JObject(new JProperty("status",c.Variables["documentStatus"]),new JProperty("retryAfter",StartRetry(c)),new JProperty("body",JObject.Parse(StartBody(c))),new JProperty("rawBody",StartBody(c)));
   } else if(mode=="owner") {
    var accepted=JObject.Parse(Accepted(c)); var ticket=(string)accepted["ticket"];
    var mutation=(string)item["mutation"]??""; var claims=Claims(ticket); var now=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    if(mutation=="caller")c.Subscription.Id="other-owner";
    if(mutation=="api")c.Api.Id="other-api";
    if(mutation=="backend")c.Variables["documentBaseUrl"]="https://other.example.com";
    if(mutation=="anonymous")c.Subscription=null;
    if(mutation=="altered")ticket=ticket.Substring(0,ticket.Length-1)+(ticket.EndsWith("A")?"B":"A");
    if(mutation=="model-unresigned") { claims["model"]="prebuilt-read"; ticket=Encode(claims.ToString(Formatting.None))+"."+ticket.Split('.')[1]; }
    if(mutation=="model") { claims["model"]="unapproved-model"; ticket=Sign(claims); }
    if(mutation=="audience") { claims["aud"]="realtime"; ticket=Sign(claims); }
    if(mutation=="domain")ticket=Sign(claims,"");
    if(mutation=="future") { claims["iat"]=now+120; claims["exp"]=now+120+86400; ticket=Sign(claims); }
    if(mutation=="lifetime") { claims["exp"]=(long)claims["iat"]+86401; ticket=Sign(claims); }
    if(mutation=="expired") { claims["iat"]=now-86401; claims["exp"]=now-1; ticket=Sign(claims); }
    if(mutation=="malformed")ticket="malformed";
    if(mutation=="oversize")ticket=new string('a',4097);
    c.Request.Headers["X-Core-Document-Ticket"]=new[]{ticket};
    c.Request.MatchedParameters["operationId"]=mutation=="job"?"cccccccc-cccc-4ccc-8ccc-cccccccccccc":__JOB__;
    if(mutation=="query")c.Request.Url.Query["backend"]=new[]{"untrusted"};
    var owner=Owner(c); c.Variables["documentOwner"]=owner;
    results[name]=new JObject(new JProperty("status",OwnerStatus(c)),new JProperty("owner",String.IsNullOrEmpty(owner)?null:JObject.Parse(owner)));
   } else {
    if(item["publicBytes"]!=null||item["nativeBytes"]!=null) {
     var analysis=new JObject(new JProperty("apiVersion","2024-11-30"),new JProperty("modelId","prebuilt-layout"),new JProperty("content",""),new JProperty("pages",new JArray()));
     var native=new JObject(new JProperty("status","succeeded"),new JProperty("analyzeResult",analysis));
     var projected=new JObject(new JProperty("analyzeResult",analysis),new JProperty("requestId",c.RequestId.ToString()));
     var target=item["publicBytes"]??item["nativeBytes"]; var envelope=item["publicBytes"]!=null?projected:native;
     analysis["content"]=new string('x',(int)target-Encoding.UTF8.GetByteCount(envelope.ToString(Formatting.None)));
     native["analyzeResult"]=analysis; c.Response.Body.Data=Encoding.UTF8.GetBytes(native.ToString(Formatting.None));
    }
    results[name]=JObject.Parse(Evaluate(c));
   }
  }
  return results.ToString(Formatting.None);
 }
}
'''


def analysis_result(**changes):
    analysis = {'apiVersion': '2024-11-30', 'modelId': 'prebuilt-layout', 'content': 'synthetic OCR', 'pages': [{'pageNumber': 1}], 'tables': []}
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
            {'id': 'unknown-mime', 'data': pdf, 'mime': 'text/plain'}, {'id': 'wrong-model', 'data': pdf, 'model': 'custom-model'},
            {'id': 'duplicate-model', 'data': pdf, 'models': ['prebuilt-layout', 'prebuilt-read']}, {'id': 'missing-model', 'data': pdf, 'noModelQuery': True},
            {'id': 'url-input', 'data': pdf, 'extraQuery': 'urlSource'}, {'id': 'compressed', 'data': pdf, 'encoding': 'gzip'}]
        signatures = {'application/pdf': b'%PDF-1.7', 'image/jpeg': b'\xff\xd8\xff' + b'\x00' * 5,
            'image/png': b'\x89PNG\r\n\x1a\n', 'image/tiff': b'II*\x00' + b'\x00' * 4, 'image/bmp': b'BM' + b'\x00' * 6,
            'image/heif': b'\x00\x00\x00\x18ftypheic', 'image/heic': b'\x00\x00\x00\x18ftypheic', 'text/html': b'<p>'}
        for mime in SETTINGS['contentTypes']:
            data = signatures.get(mime, b'PK\x03\x04' + b'\x00' * 4)
            cls.cases.append({'id': 'format-' + mime, 'mime': mime, 'data': base64.b64encode(data).decode()})
        for name, location in [('valid', LOCATION), ('host', LOCATION.replace('documents.example.com', 'other.example.com')),
                ('port', LOCATION.replace('.com/', '.com:444/')), ('http', LOCATION.replace('https:', 'http:')),
                ('user', LOCATION.replace('https://', 'https://user@')), ('model', LOCATION.replace('prebuilt-layout', 'prebuilt-read')),
                ('id', LOCATION.replace(JOB, 'secret/path')), ('query', LOCATION + '&secret=bad'), ('missing-query', LOCATION.split('?')[0]), ('fragment', LOCATION + '#private')]:
            cls.cases.append({'id': 'location-' + name, 'mode': 'location', 'location': location})
        for status in (202, 400, 413, 415, 422, 429, 500):
            cls.cases.append({'id': 'start-' + str(status), 'mode': 'start', 'status': status, 'retry': '17', 'body': {'error': {'message': 'SENTINEL_PRIVATE'}}})
        cls.cases.extend([{'id': 'start-null', 'mode': 'start', 'nullResponse': True},
            {'id': 'start-invalid-location', 'mode': 'start', 'location': LOCATION + '&secret=bad'},
            {'id': 'start-anonymous', 'mode': 'start', 'anonymous': True}])
        for mutation in ('', 'caller', 'api', 'backend', 'anonymous', 'job', 'altered', 'model-unresigned', 'model', 'audience', 'domain', 'future', 'lifetime', 'expired', 'malformed', 'oversize', 'query'):
            cls.cases.append({'id': 'owner-' + (mutation or 'valid'), 'mode': 'owner', 'mutation': mutation})
        for name, body, status in [('success', analysis_result(), 200), ('running', {'status': 'running'}, 200),
                ('not-started', {'status': 'notStarted'}, 200), ('failed', {'status': 'failed', 'error': {'message': 'SENTINEL_PRIVATE'}}, 200),
                ('canceled', {'status': 'canceled'}, 200), ('wrong-version', analysis_result(apiVersion='preview'), 200),
                ('wrong-model-result', analysis_result(modelId='prebuilt-read'), 200), ('wrong-shape', analysis_result(pages='private'), 200),
                ('missing', {'error': {'message': 'SENTINEL_PRIVATE'}}, 404), ('expired', {}, 410), ('throttle', {}, 429), ('server-error', {}, 500)]:
            cls.cases.append({'id': 'poll-' + name, 'mode': 'poll', 'body': body, 'status': status, 'retry': '23'})
        cls.cases.extend([{'id': 'poll-null', 'mode': 'poll', 'nullResponse': True}, {'id': 'poll-malformed', 'mode': 'poll', 'status': 200, 'rawBody': '{bad-json'}])
        for delta in (-1, 0, 1):
            cls.cases.append({'id': 'public-bytes-' + str(delta), 'mode': 'poll', 'status': 200, 'publicBytes': SETTINGS['maxResponseBytes'] + delta})
        cls.cases.append({'id': 'native-oversize', 'mode': 'poll', 'status': 200, 'nativeBytes': SETTINGS['maxResponseBytes'] + 1})
        methods = {'INPUT': (START, './inbound/set-variable[@name="documentInputStatus"]', 'value'),
            'OPERATION_ID': (START, './outbound/set-variable[@name="documentOperationId"]', 'value'),
            'ACCEPTED': (START, './outbound/set-variable[@name="documentAccepted"]', 'value'),
            'START_STATUS': (START, './outbound/set-variable[@name="documentStatus"]', 'value'),
            'START_RETRY': (START, './outbound/set-variable[@name="documentRetryAfter"]', 'value'),
            'START_BODY': (START, './outbound/return-response/set-body', None),
            'OWNER': (STATUS, './inbound/set-variable[@name="documentOwner"]', 'value'),
            'OWNER_STATUS': (STATUS, './inbound/set-variable[@name="documentOwnerStatus"]', 'value'),
            'EVALUATE': (STATUS, './outbound/set-variable[@name="documentPollEvaluation"]', 'value')}
        source = CSHARP
        for name, args in methods.items():
            source = source.replace('__' + name + '__', statement(*args))
        source = source.replace('{{core-realtime-ticket-key}}', TEST_KEY).replace('__TEST_KEY__', TEST_KEY)
        for name, value in (('SETTINGS', json.dumps(SETTINGS)), ('BASE', BASE), ('JOB', JOB), ('LOCATION', LOCATION)):
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

    def test_native_files_above_old_cap_and_exact_injected_boundary_are_accepted(self):
        for name in ('valid', 'above-old-size', 'exact-size', *('format-' + mime for mime in SETTINGS['contentTypes'])):
            self.assertEqual(self.results[name]['status'], 200, name)
            self.assertTrue(self.results[name]['preserved'])
        self.assertEqual(self.results['oversize']['status'], 413)
        for name in ('empty', 'no-body', 'wrong-mime', 'wrong-model', 'duplicate-model', 'missing-model', 'url-input'):
            self.assertEqual(self.results[name]['status'], 400, name)
        for name in ('unknown-mime', 'compressed'):
            self.assertEqual(self.results[name]['status'], 415)

    def test_operation_location_is_fixed_to_the_provider_model_uuid_and_version(self):
        self.assertEqual(self.results['location-valid'], JOB)
        for name in ('host', 'port', 'http', 'user', 'model', 'id', 'query', 'missing-query', 'fragment'):
            self.assertEqual(self.results['location-' + name], '', name)

    def test_start_accepts_once_and_returns_owner_ticket_with_provider_retention(self):
        accepted = self.results['start-202']
        self.assertEqual(accepted['status'], 202)
        self.assertEqual(accepted['retryAfter'], 17)
        body = json.loads(accepted['rawBody'])
        self.assertEqual(body['operationId'], JOB)
        self.assertEqual(body['statusPath'], '/tools/documents/v1/operations/' + JOB)
        self.assertRegex(body['expiresAt'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z$')
        self.assertNotIn(BASE, json.dumps(body))
        claims = json.loads(base64.urlsafe_b64decode(body['ticket'].split('.')[0] + '==='))
        self.assertEqual(claims['exp'] - claims['iat'], 86400)
        self.assertEqual(claims['aud'], 'document-analysis-v1')
        for name, status in [('400', 422), ('413', 413), ('415', 422), ('422', 422), ('429', 429), ('500', 502), ('null', 504), ('invalid-location', 502), ('anonymous', 502)]:
            result = self.results['start-' + name]
            self.assertEqual(result['status'], status, name)
            self.assertEqual(set(result['body']), {'error', 'requestId'})
            self.assertNotIn('SENTINEL', json.dumps(result))
        self.assertEqual(self.results['start-429']['retryAfter'], 17)
        self.assertEqual(self.results['start-null']['retryAfter'], 2)

    def test_job_ticket_checks_owner_api_backend_model_job_signature_domain_and_lifetime(self):
        self.assertEqual(self.results['owner-valid']['status'], 200)
        self.assertEqual(self.results['owner-expired']['status'], 410)
        for mutation in ('caller', 'api', 'backend', 'anonymous', 'job', 'altered', 'model-unresigned', 'model', 'audience', 'domain', 'future', 'lifetime', 'malformed', 'oversize', 'query'):
            self.assertEqual(self.results['owner-' + mutation]['status'], 403, mutation)

    def test_polling_returns_safe_running_result_or_fixed_error(self):
        success = self.results['poll-success']
        self.assertEqual(success['status'], 200)
        body = json.loads(success['result'])
        self.assertEqual(set(body), {'analyzeResult', 'requestId'})
        self.assertEqual(body['analyzeResult']['content'], 'synthetic OCR')
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

    def test_response_budget_includes_native_and_final_public_envelopes(self):
        for delta in (-1, 0):
            result = self.results['public-bytes-' + str(delta)]
            self.assertEqual(result['status'], 200)
            self.assertEqual(len(result['result'].encode()), SETTINGS['maxResponseBytes'] + delta)
        for name in ('public-bytes-1', 'native-oversize'):
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
            self.assertNotIn('copy-unmatched-params="true"', path.read_text())
        self.assertIsNone(ET.parse(START).find('./inbound/set-body'))
        self.assertNotIn('base64Source', START.read_text())
        self.assertIsNotNone(ET.parse(STATUS).find('./inbound/set-header[@name="X-Core-Document-Ticket"][@exists-action="delete"]'))


class DocumentAnalysisConfigurationTests(unittest.TestCase):
    def raw(self):
        raw, _ = standard_fixture()
        raw['network']['apimKeyVaultAccessEnabled'] = True
        raw['apiDefinitions'] = [copy.deepcopy(API)]
        return raw

    def test_provider_capacity_and_native_job_contract_replace_trial_limits(self):
        raw = self.raw()
        raw['gateway'].update(wafMaxRequestBodySizeInKb=128, wafRequestBodyInspectLimitInKb=128)
        api = normalize_configuration(raw)['apiDefinitions'][0]
        constraints = api['authorization']['operationConstraints']
        self.assertEqual(constraints['analyze_document']['maxRequestBytes'], 500000000)
        self.assertEqual(constraints['analyze_document']['maxResponseBytes'], 524288000)
        self.assertEqual(constraints['get_document_operation']['kind'], 'documentAnalysisStatus')
        self.assertEqual(len(constraints['analyze_document']['contentTypes']), 11)
        self.assertNotIn('timeoutSeconds', constraints['analyze_document'])

    def test_document_only_foundation_prepares_the_existing_server_owner_key(self):
        from deployment.helpers.grpc_lifecycle import owner_ticket_enabled, prepare_secret, realtime_enabled
        raw = self.raw()
        raw['gateway']['realtimeControlEnabled'] = False
        raw['gateway']['grpc'] = {'enabled': False}
        config = normalize_configuration(raw)
        self.assertFalse(realtime_enabled(config))
        self.assertTrue(owner_ticket_enabled(config))
        writer = mock.Mock()
        with mock.patch('deployment.helpers.grpc_lifecycle._prepare_credential', return_value='synthetic-test-key') as prepare:
            self.assertEqual(prepare_secret(config, ROOT, writer), {'realtimeTicketKey': 'synthetic-test-key'})
            prepare.assert_called_once_with(config, ROOT, 'realtime-ticket', writer)
        writer.assert_not_called()

    def test_standard_foundation_requires_owner_key_access_before_api_expansion(self):
        """空配列から標準APIを後続公開する場合も、鍵への通信を事前検査する。"""
        raw = self.raw()
        raw['apiDefinitions'] = []
        raw['gateway']['realtimeControlEnabled'] = False
        raw['gateway']['grpc'] = {'enabled': False}
        raw['network']['apimKeyVaultAccessEnabled'] = False
        with self.assertRaisesRegex(ValueError, 'owner-bound operation ticket'):
            normalize_configuration(raw)

    def test_canonical_paths_models_auth_provider_bounds_and_owner_key_remain_required(self):
        for mutation in ('model', 'bytes', 'response', 'timeout', 'version', 'path', 'backend', 'status-method', 'status-path', 'status-parameters', 'owner-key'):
            raw = self.raw()
            api = raw['apiDefinitions'][0]
            start = api['authorization']['operationConstraints']['analyze_document']
            if mutation == 'model': start['allowedModels'] = ['other']
            if mutation == 'bytes': start['maxRequestBytes'] = 500000001
            if mutation == 'response': start['maxResponseBytes'] = 524288001
            if mutation == 'timeout': start['timeoutSeconds'] = 60
            if mutation == 'version': start['apiVersion'] = 'preview'
            if mutation == 'path': api['path'] = 'tools/other/v1'
            if mutation == 'backend': api['backendService'] = 'foundryOpenAi'
            if mutation == 'status-method': api['operations'][1]['method'] = 'POST'
            if mutation == 'status-path': api['operations'][1]['urlTemplate'] = '/other/{operationId}'
            if mutation == 'status-parameters': api['operations'][1]['templateParameters'] = []
            if mutation == 'owner-key': raw['network']['apimKeyVaultAccessEnabled'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                normalize_configuration(raw)


if __name__ == '__main__':
    unittest.main()
