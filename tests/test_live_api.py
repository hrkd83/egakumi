"""Liveの実ポリシー式と、モデル別上流・音声接続定義の拒否条件を検査する。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_api_protocols import bound_coverage, configuration
from test_realtime_policy import SOURCE as REALTIME_SOURCE, PWSH, expression
from deployment.helpers.catalog_descriptions import describe_api

CORE = Path(__file__).resolve().parents[1]


class LiveConfigurationTests(unittest.TestCase):
    def test_session_contracts_pin_endpoint_and_separate_event_model_selection(self):
        config = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / 'config')
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
                configuration.normalize_configuration(raw, base_directory=CORE / 'config')

    def test_profile_routing_is_a_complete_fixed_table(self):
        for mutation in ('foreignHost', 'policyExpression', 'credentialQuery', 'partialTable', 'operationAndProfileRoute'):
            raw = bound_coverage()
            api = next(a for a in raw['apiDefinitions'] if a['name'] == 'blackforest-images-v1')
            c = api['authorization']['operationConstraints']['flux2-generate']
            if mutation == 'foreignHost': c['profiles'][0]['backendUrlTemplate'] = 'https://other.example.com/provider'
            if mutation == 'policyExpression': c['profiles'][0]['backendUrlTemplate'] = '/providers/@(context.Request.Url)'
            if mutation == 'credentialQuery': c['profiles'][0]['backendUrlTemplate'] = '/providers/flux?api-key=synthetic'
            if mutation == 'partialTable': c['profiles'][0].pop('backendUrlTemplate')
            if mutation == 'operationAndProfileRoute': api['operations'][0]['backendUrlTemplate'] = '/providers/other'
            with self.subTest(mutation=mutation), self.assertRaises(configuration.ConfigurationError):
                configuration.normalize_configuration(raw, base_directory=CORE / 'config')

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
                configuration.normalize_configuration(raw, base_directory=CORE / 'config')


LIVE_SOURCE = REALTIME_SOURCE.split('public static class TicketFixture')[0] + r'''
public static class LiveFixture {
 public static string Request(Context context) { __LIVE_REQUEST__ }
 public static string Answer(Context context) { __LIVE_ANSWER__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach(JObject test in JArray.Parse(input)) {
   var c = new Context();
   c.Variables["gatewayOperationConstraint"] = "{\"model\":\"gpt-live-1\",\"stringChoices\":{\"voice\":[\"marin\"]}}";
   c.Request.Body.Text = test["raw"] != null ? (string)test["raw"] : test["body"].ToString(Formatting.None);
   c.Response.Body = new Body { Text = test["answer"].ToString(Formatting.None) };
   results[(string)test["id"]] = new JObject(new JProperty("request", Request(c)), new JProperty("answer", Answer(c)));
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
        source = LIVE_SOURCE.replace('__LIVE_REQUEST__', expression('live-create.xml', './inbound/set-variable[@name="liveSessionRequest"]', 'value'))
        source = source.replace('__LIVE_ANSWER__', expression('live-create.xml', './outbound/choose/when/set-variable[@name="liveSessionAnswer"]', 'value'))
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

    def test_native_instructions_and_sdp_can_exceed_old_trial_limits(self):
        for name in ('hugeSdp', 'hugeInstructions'):
            self.assertTrue(self.results[name]['request'], name)

    def test_answer_exposes_the_public_contract_and_rejects_invalid_service_data(self):
        self.assertEqual(json.loads(self.results['valid']['answer']), {'sessionId':'live_synthetic','sdp':'v=0\r\ns=answer\r\n'})
        self.assertEqual(self.results['invalidAnswer']['answer'], '')


if __name__ == '__main__':
    unittest.main()
