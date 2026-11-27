"""Liveの実ポリシー式と、モデル別上流・音声接続定義の拒否条件を検査する。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.powershell import PWSH
from deployment.helpers.apim_audit import render_audit_policy

from tests.gateway.publication.test_api_protocols import bound_coverage, configuration
from tests.gateway.policies.test_realtime_policy import SOURCE as REALTIME_SOURCE, expression
from deployment.helpers.catalog_descriptions import describe_api
from tests.identity.test_credential_login import operation_error_methods
from tests import REPOSITORY

CORE = REPOSITORY


class LiveConfigurationTests(unittest.TestCase):
    def test_session_contracts_pin_endpoint_and_separate_event_model_selection(self):
        config = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / 'infra/config')
        apis = {a['name']: a for a in config['apiDefinitions']}
        for name, kind, models, query in (
            ('openai-live-ws-v1', 'live', ['gpt-live-1'], {}),
            ('openai-transcription-ws-v1', 'transcription', ['gpt-live-transcribe'], {'intent':'transcription'}),
        ):
            api = apis[name]
            self.assertEqual(api['authorization']['mode'], 'subscriptionKey')
            self.assertEqual(api['websocket'], {'queryParameters':query, 'session':{'kind':kind, 'models':models}})
            self.assertIn(models[0], describe_api(config, api))
            self.assertNotIn('allowedModels', api['websocket'])
        for mutation in ('wrongEndpoint', 'wrongIntent', 'modelQuery', 'undeployedModel', 'queryModelSelection', 'keyAuthentication'):
            raw = bound_coverage()
            api = next(a for a in raw['apiDefinitions'] if a['name'] == 'openai-transcription-ws-v1')
            if mutation == 'wrongEndpoint': api['backendUrl'] = 'wss://provider.example.com/realtime'
            if mutation == 'wrongIntent': api['websocket']['queryParameters']['intent'] = 'conversation'
            if mutation == 'modelQuery': api['websocket']['queryParameters']['model'] = 'gpt-live-transcribe'
            if mutation == 'undeployedModel': api['websocket']['session']['models'] = ['undeployed']
            if mutation == 'queryModelSelection': api['websocket']['allowedModels'] = ['gpt-live-transcribe']
            if mutation == 'keyAuthentication': api['authorization']['mode'] = 'keyOrEntra'
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw, base_directory=CORE / 'infra/config')

    def test_model_routing_is_a_complete_fixed_table(self):
        for mutation in ('foreignHost', 'policyExpression', 'credentialQuery', 'partialTable', 'operationAndModelRoute'):
            raw = bound_coverage()
            api = next(a for a in raw['apiDefinitions'] if a['name'] == 'blackforest-images-v1')
            c = api['authorization']['operationConstraints']['flux2-generate']
            setting = c['modelSettings'][c['allowedModels'][0]]
            if mutation == 'foreignHost': setting['backendUrlTemplate'] = 'https://other.example.com/provider'
            if mutation == 'policyExpression': setting['backendUrlTemplate'] = '/providers/@(context.Request.Url)'
            if mutation == 'credentialQuery': setting['backendUrlTemplate'] = '/providers/flux?api-key=synthetic'
            if mutation == 'partialTable': setting.pop('backendUrlTemplate')
            if mutation == 'operationAndModelRoute': api['operations'][0]['backendUrlTemplate'] = '/providers/other'
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw, base_directory=CORE / 'infra/config')

    def test_live_control_is_fixed_model_key_authorized_and_bounded_json(self):
        for mutation in ('undeployed', 'keyAuthentication', 'wrongRoute', 'unbounded', 'upstreamOverride'):
            raw = bound_coverage()
            api = next(a for a in raw['apiDefinitions'] if a['name'] == 'openai-live-control-v1')
            c = api['authorization']['operationConstraints']['live-create']
            if mutation == 'undeployed': c['model'] = 'undeployed'
            if mutation == 'keyAuthentication': api['authorization']['mode'] = 'keyOrEntra'
            if mutation == 'wrongRoute': api['operations'][0]['urlTemplate'] = '/calls'
            if mutation == 'voice': c['stringChoices'] = {}
            if mutation == 'unbounded': c['maxRequestBytes'] = 524288001
            if mutation == 'upstreamOverride': api['operations'][0]['backendUrlTemplate'] = '/other'
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw, base_directory=CORE / 'infra/config')


LIVE_PREFIX = REALTIME_SOURCE.split('public static class TicketFixture')[0]
LIVE_PREFIX = LIVE_PREFIX.replace('public string Text;', 'public string Text; public int Reads;').replace('return typeof(T)', 'Reads++; return typeof(T)')
LIVE_PREFIX = LIVE_PREFIX.replace('public class Context {', '''public class LastError { public string PolicyId,Source,Reason; }
public class TrackingVariables : Dictionary<string,object> {
 public int SettingsReads;
 public new object this[string key] { get { if(key=="gatewayOperationConstraint")SettingsReads++;return base[key]; } set { base[key]=value; } }
}
public class Context {
 public Guid RequestId=Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 public Named Api=new Named { Id="synthetic-live-api" },Operation=new Named { Id="live-create" };
 public LastError LastError=new LastError();''').replace('public Dictionary<string,object> Variables', 'public TrackingVariables Variables').replace('= new Dictionary<string,object>();', '= new TrackingVariables();')
LIVE_SOURCE = LIVE_PREFIX + r'''
public static class LiveFixture {
 __ERROR_METHODS__
 public static string Request(Context context) { __LIVE_REQUEST__ }
 public static string Answer(Context context) { __LIVE_ANSWER__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach(JObject test in JArray.Parse(input)) {
   var c = new Context();
   c.Variables["gatewayOperationConstraint"] = "{\"model\":\"gpt-live-1\",\"stringChoices\":{\"voice\":[\"marin\"]}}";
   if(test["settings"]!=null)c.Variables["gatewayOperationConstraint"]=test["settings"].ToString(Formatting.None);
   if(test["settingsRaw"]!=null)c.Variables["gatewayOperationConstraint"]=(string)test["settingsRaw"];
   if((bool?)test["settingsObject"]==true)c.Variables["gatewayOperationConstraint"]=new JObject();
   if((bool?)test["settingsMissing"]==true)c.Variables.Remove("gatewayOperationConstraint");
   c.Request.Body.Text = test["raw"] != null ? (string)test["raw"] : test["body"].ToString(Formatting.None);
   c.Response.Body = new Body { Text = test["answer"].ToString(Formatting.None) };
   if(test["errorId"]!=null || (string)test["mode"]=="error") {
    c.LastError.PolicyId=(string)test["errorId"];results[(string)test["id"]]=LiveError(c);continue;
   }
   var result=new JObject();
   try { var request=Request(c);result["request"]=request;result["status"]=String.IsNullOrEmpty(request)?400:200; }
   catch { c.LastError.PolicyId=__REQUEST_POLICY_ID__;c.LastError.Source="set-variable";c.LastError.Reason="ExpressionValueEvaluationFailure";result=LiveError(c); }
   result["answer"]=Answer(c);result["bodyReads"]=c.Request.Body.Reads;result["settingsReads"]=c.Variables.SettingsReads;
   results[(string)test["id"]]=result;
  }
  return results.ToString(Formatting.None);
 }
}
'''


class LivePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest('Actual C# policy execution requires PowerShell')
        body = {'sdp':'v=0\r\ns=synthetic\r\n', 'instructions':'Synthetic instruction'}
        answer = {'session':{'id':'live_synthetic'}, 'transport':{'type':'webrtc', 'sdp':'v=0\r\ns=answer\r\n'}, 'ignored':'synthetic'}
        cases = [{'id':'valid', 'body':body, 'answer':answer}]
        for name, patch in (
            ('modelInjection', {'model':'other'}),
            ('delegationInjection', {'session':{'delegation':{'type':'responses'}}}),
            ('invalidSdp', {'sdp':'not-sdp'}),
            ('hugeSdp', {'sdp':'v=0\r\n'+'x'*65537}),
            ('hugeInstructions', {'instructions':'x'*8193}),
            ('voice', {'voice':'unknown'}),
            ('wrongType', {'instructions':42}),
        ):
            cases.append({'id':name, 'body':dict(body, **patch), 'answer':answer})
        cases.append({'id':'malformed', 'raw':'{', 'body':body, 'answer':answer})
        cases.append({'id':'invalidAnswer', 'body':body, 'answer':{'session':{'id':'https://other'}, 'transport':{'sdp':'not-sdp'}}})
        for label, patch in (
            ('missing', {'settingsMissing':True}), ('raw', {'settingsRaw':'{SENTINEL_SETTINGS'}),
            ('object', {'settingsObject':True}), ('null', {'settingsRaw':'null'}), ('array', {'settingsRaw':'[]'}),
            ('choices', {'settings':{'model':'gpt-live-1','stringChoices':[]}}),
            ('choice-cast', {'settings':{'model':'gpt-live-1','stringChoices':{'voice':[{}]}}}),
            ('model-cast', {'settings':{'model':{},'stringChoices':{'voice':['marin']}}}),
        ):
            cases.append({'id':'settings-'+label,'body':body,'answer':answer,**patch})
        cases.extend([
            {'id':'body-first','raw':'{SENTINEL_BODY','body':body,'answer':answer,'settingsMissing':True},
            {'id':'input-first','body':dict(body,instructions=42),'answer':answer,'settings':{'stringChoices':[],'model':{}}},
            {'id':'blank-voice-first','body':dict(body,voice=' '),'answer':answer,'settings':{'stringChoices':{'voice':[{}]},'model':{}}},
            {'id':'matched-choice-first','body':body,'answer':answer,'settings':{'model':'gpt-live-1','stringChoices':{'voice':['marin',{}]}}},
        ])
        for label, values in (('missing',{}),('null',{'voice':None}),('scalar',{'voice':'marin'}),('empty',{'voice':[]})):
            cases.append({'id':'voices-'+label,'body':body,'answer':answer,'settings':{'model':'gpt-live-1','stringChoices':values}})
        for label, values in (('missing',{}),('null',{'model':None}),('numeric',{'model':42})):
            cases.append({'id':'model-'+label,'body':body,'answer':answer,'settings':values})
        for label, error_id in (('exact','core-live-session-request-settings'),('near','core-live-session-request-settings-extra'),('null',None),('empty','')):
            cases.append({'id':'error-'+label,'mode':'error','errorId':error_id,'body':body,'answer':answer})
        raw=policy_fixture_path(CORE/'apps/api-gateway/policies','live-create.xml').read_text(encoding='utf-8')
        tree=ET.fromstring(raw)
        # baseの再実装はせず、新しい限定chooseだけを実行して非一致の未処理を確認します。
        error_xml='<policies><on-error>'+ET.tostring(tree.find('./on-error/choose'),encoding='unicode')+'</on-error></policies>'
        source = LIVE_SOURCE.replace('__LIVE_REQUEST__', expression('live-create.xml', './inbound/set-variable[@name="liveSessionRequest"]', 'value'))
        source = source.replace('__LIVE_ANSWER__', expression('live-create.xml', './outbound/choose/when/set-variable[@name="liveSessionAnswer"]', 'value'))
        source = source.replace('__ERROR_METHODS__',operation_error_methods(error_xml,'LiveError',entra_enabled=False)).replace('__REQUEST_POLICY_ID__',json.dumps(tree.find('./inbound/set-variable[@name="liveSessionRequest"]').get('id')))
        with tempfile.TemporaryDirectory(prefix='core-live-policy-') as temporary:
            folder = Path(temporary)
            (folder/'policy.cs').write_text(source)
            (folder/'cases.json').write_text(json.dumps(cases))
            (folder/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[LiveFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result = subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(folder/'run.ps1')], capture_output=True,text=True,timeout=60,
                                    env={**os.environ,'DOTNET_BUNDLE_EXTRACT_BASE_DIR':str(folder/'dotnet')})
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.results=json.loads(result.stdout)

    def test_fixed_session_and_sdp_are_built_from_validated_input(self):
        request = json.loads(self.results['valid']['request'])
        self.assertEqual(request['session']['model'], 'gpt-live-1')
        self.assertEqual(request['session']['delegation'], {'type':'client'})
        self.assertEqual(request['session']['audio']['output']['voice'], 'marin')
        self.assertEqual(request['transport'], {'type':'webrtc','sdp':'v=0\r\ns=synthetic\r\n'})

    def test_invalid_and_injected_inputs_are_rejected_by_actual_expression(self):
        for name in ('modelInjection','delegationInjection','invalidSdp','voice','wrongType','malformed'):
            self.assertEqual(self.results[name]['request'], '', name)

    def test_request_preserves_large_native_instructions_and_sdp(self):
        for name in ('hugeSdp', 'hugeInstructions'):
            self.assertTrue(self.results[name]['request'], name)

    def test_settings_failures_use_the_exact_internal_error_branch(self):
        for name in ('missing','raw','object','null','array','choices','choice-cast','model-cast'):
            value=self.results['settings-'+name]
            self.assertEqual((value['status'],value['bodyReads'],value['settingsReads']),(500,1,1),name)
            self.assertFalse(value['hasBody'])
            self.assertEqual(value['headers'],{'apim-request-id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','Cache-Control':'no-store'})
            self.assertEqual([(a['phase'],a['code'],a['status']) for a in value['audits']],[('policy-error','OPERATION_SETTINGS_INVALID',500)])
            self.assertNotIn('SENTINEL',json.dumps(value))
        self.assertEqual(self.results['error-exact']['status'],500)
        for name in ('near','null','empty'):
            self.assertNotIn('status',self.results['error-'+name])
            self.assertEqual(self.results['error-'+name]['audits'],[])
        raw=policy_fixture_path(CORE/'apps/api-gateway/policies','live-create.xml').read_text(encoding='utf-8')
        self.assertEqual([node.tag for node in ET.fromstring(raw).find('on-error')],['choose','base'])

    def test_existing_order_short_circuit_and_silent_settings_values_are_preserved(self):
        self.assertEqual((self.results['body-first']['status'],self.results['body-first']['settingsReads']),(400,0))
        for name in ('input-first','blank-voice-first'):
            self.assertEqual(self.results[name]['status'],400,name)
        self.assertEqual(self.results['matched-choice-first']['status'],200)
        for name in ('voices-missing','voices-null','voices-scalar','voices-empty','model-missing','model-null','model-numeric','valid'):
            value=self.results[name]
            self.assertEqual((value['status'],value['bodyReads'],value['settingsReads']),(200,1,1),name)
        self.assertIsNone(json.loads(self.results['model-missing']['request'])['session']['model'])
        self.assertIsNone(json.loads(self.results['model-null']['request'])['session']['model'])
        self.assertEqual(json.loads(self.results['model-numeric']['request'])['session']['model'],'42')

    def test_answer_exposes_the_public_contract_and_rejects_invalid_service_data(self):
        self.assertEqual(json.loads(self.results['valid']['answer']), {'sessionId':'live_synthetic','sdp':'v=0\r\ns=answer\r\n'})
        self.assertEqual(self.results['invalidAnswer']['answer'], '')
        raw = policy_fixture_path(CORE / 'apps/api-gateway/policies', 'live-create.xml').read_text(encoding='utf-8')
        root = ET.fromstring(render_audit_policy(raw))
        failure = root.find('./outbound/choose/when/choose/when')
        self.assertEqual([node.tag for node in failure], ['set-variable', 'set-variable', 'set-variable', 'include-fragment', 'return-response'])
        self.assertEqual({node.get('name'): node.get('value') for node in failure.findall('set-variable')}, {
            'gatewayAuditPhase': 'policy-error', 'gatewayAuditCode': 'INVALID_LIVE_SESSION_ANSWER', 'gatewayAuditStatus': '@(502)',
        })
        self.assertEqual(failure.find('include-fragment').get('fragment-id'), 'core-gateway-telemetry')
        self.assertEqual(failure.find('return-response/set-status').get('code'), '502')
        self.assertIsNone(failure.find('return-response/set-body'))


if __name__ == '__main__':
    unittest.main()
