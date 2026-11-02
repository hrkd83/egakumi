"""Run the actual APIM SSML declaration expression, then verify its IaC scope/order.

No XML parser, entity resolver, network request, authentication or backend runs in
this guard. These local checks do not replace an authenticated APIM replay.
"""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep
from test_gateway_limits_telemetry import PWSH, expression

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'apps/gateway/apim/policies/speech-ssml-guard.xml'
SPEECH = json.loads((ROOT / 'apps/gateway/apim/apis/azure-speech-v1.json').read_text(encoding='utf-8'))

CSHARP = r'''
using System;
using System.Text;
using Newtonsoft.Json.Linq;
public sealed class Body {
  public byte[] Data;
  public int Reads;
  public bool Preserved = true;
  public T As<T>(bool preserveContent=false) {
    Reads++; Preserved &= preserveContent;
    if (typeof(T) != typeof(byte[])) throw new Exception("XML/JSON parsing is forbidden");
    return (T)(object)Data;
  }
}
public sealed class Request { public Body Body = new Body(); }
public sealed class Context { public Request Request = new Request(); }
public static class SpeechGuardFixture {
  public static bool Reject(Context context) { __GUARD__ }
  public static string Run(string cases) {
    var results = new JObject();
    foreach(JObject item in JArray.Parse(cases)) {
      var context = new Context();
      context.Request.Body.Data = Convert.FromBase64String((string)item["data"]);
      if ((bool?)item["noBody"] == true) context.Request.Body = null;
      var rejected = Reject(context);
      results[(string)item["id"]] = new JObject(
        new JProperty("rejected", rejected), new JProperty("reads", context.Request.Body == null ? 0 : context.Request.Body.Reads),
        new JProperty("preserved", context.Request.Body == null || context.Request.Body.Preserved),
        new JProperty("data", context.Request.Body == null ? "" : Convert.ToBase64String(context.Request.Body.Data)));
    }
    return results.ToString();
  }
}
'''


class SpeechSsmlDeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest('PowerShell is required for offline C# execution')
        inputs = {
            'normal': ('<speak xmlns="http://www.w3.org/2001/10/synthesis"><voice name="ja-JP-NanamiNeural"><prosody rate="1.1">日本語 ../templates/readme.txt 㰀℀</prosody></voice></speak>', False),
            'escaped': ('<speak><voice>&lt;!DOCTYPE speak&gt; and &lt;!ENTITY explanation</voice></speak>', False),
            'cdata': ('<speak><voice><![CDATA[XML tutorial <!DOCTYPE speak> and <!ENTITY example>]]></voice></speak>', False),
            'comment': ('<!-- XML tutorial <!DOCTYPE speak> and <!ENTITY example> --><speak>ok</speak>', False),
            'pi': ('<?tutorial value="<!DOCTYPE example>"?><speak>ok</speak>', False),
            'quoted': ('<speak attr="&lt;!DOCTYPE example >"><voice>ok</voice></speak>', False),
            'mixed-literals': ('<speak><!-- <![CDATA[ <!DOCTYPE sample> --><![CDATA[<!-- <!ENTITY sample> -->]]></speak>', False),
            'cdata-escaped-end': ('<speak><![CDATA[ ]]&gt; <!DOCTYPE example> ]]></speak>', False),
            'xml-declaration': ('<?xml version="1.0"?><speak>ok</speak>', False),
            'unused-dtd': ('<!DOCTYPE speak SYSTEM "https://example.invalid/never-request.dtd"><speak>ok</speak>', True),
            'internal-entity': ('<!DOCTYPE speak [<!ENTITY x "SENTINEL_NOT_EXPANDED">]><speak>&x;</speak>', True),
            'external-entity': ('<!DOCTYPE speak [<!ENTITY x SYSTEM "file:///SENTINEL_NOT_READ">]><speak>&x;</speak>', True),
            'standalone-entity': ('<!ENTITY x SYSTEM "https://example.invalid/never-request"><speak>ok</speak>', True),
            'comment-then-dtd': ('<!-- safe <!DOCTYPE example> --><!DOCTYPE speak><speak>ok</speak>', True),
            'cdata-then-entity': ('<speak><![CDATA[<!ENTITY tutorial>]]><!ENTITY x "bad"></speak>', True),
            'pi-then-dtd': ('<?tutorial value="<!DOCTYPE example>"?><!DOCTYPE speak><speak>ok</speak>', True),
            'declaration-whitespace': ('<! \r\n\tDOCTYPE speak><speak>ok</speak>', True),
            'empty': ('', False),
        }
        cls.cases = []
        for encoding, bom in [('utf-8', b''), ('utf-8', b'\xef\xbb\xbf'),
                              ('utf-16-le', b''), ('utf-16-le', b'\xff\xfe'),
                              ('utf-16-be', b''), ('utf-16-be', b'\xfe\xff'),
                              ('utf-32-le', b''), ('utf-32-le', b'\xff\xfe\x00\x00'),
                              ('utf-32-be', b''), ('utf-32-be', b'\x00\x00\xfe\xff')]:
            for name, (text, rejected) in inputs.items():
                data = bom + text.encode(encoding)
                cls.cases.append({'id': f'{encoding}-{bool(bom)}-{name}',
                    'data': base64.b64encode(data).decode(), 'expected': rejected})
            for prefix in (' \t\r\n', '<?xml version="1.0"?>'):
                data = bom + (prefix + inputs['unused-dtd'][0]).encode(encoding)
                cls.cases.append({'id': f'{encoding}-{bool(bom)}-prefix-{len(prefix)}',
                    'data': base64.b64encode(data).decode(), 'expected': True})
        for index, data in enumerate((b'\x4c\x6f\xa7\x94', b'\x00\x00\xff\xfe', b'\xfe\xff\x00\x00', b'\x00\x00\x3c\x00', b'\x00\x3c\x00\x00')):
            cls.cases.append({'id': 'unsupported-xml-encoding-' + str(index), 'data': base64.b64encode(data).decode(), 'expected': True})
        cls.cases.append({'id': 'absent-body', 'data': '', 'expected': False, 'noBody': True})
        source = CSHARP.replace('__GUARD__', expression(POLICY, './set-variable', 'value'))
        with tempfile.TemporaryDirectory(prefix='core-speech-guard-') as directory:
            folder = Path(directory)
            (folder / 'fixture.cs').write_text(source, encoding='utf-8')
            (folder / 'cases.json').write_text(json.dumps(cls.cases), encoding='utf-8')
            (folder / 'run.ps1').write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName) + @([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[SpeechGuardFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding='utf-8')
            result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(folder / 'run.ps1')],
                capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError('Speech guard C# compilation/execution failed:\n' + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_declarations_reject_and_literal_examples_pass_with_all_utf_encodings(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                self.assertEqual(self.results[case['id']]['rejected'], case['expected'])

    def test_only_preserved_bytes_are_read_and_no_xml_parser_or_logging_runs(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                result = self.results[case['id']]
                self.assertEqual(result['reads'], 0 if case.get('noBody') else 1)
                self.assertTrue(result['preserved'])
                self.assertEqual(result['data'], case['data'])
        source = POLICY.read_text(encoding='utf-8')
        for forbidden in ('XDocument', 'XmlReader', 'XmlDocument', '<trace', '<send-request', '<set-backend-service'):
            self.assertNotIn(forbidden, source)

    def test_rejection_is_safe_400_with_machine_readable_code(self):
        root = ET.parse(POLICY).getroot()
        response = root.find('./choose/when/return-response')
        self.assertEqual(response.find('set-status').get('code'), '400')
        self.assertEqual(json.loads(response.find('set-body').text)['error']['code'], 'InvalidSpeechMarkup')
        self.assertNotIn('context.Request.Body', response.find('set-body').text)


class SpeechSsmlGuardPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        resources = cls.template['resources']
        cls.policy = next(item for item in (resources.values() if isinstance(resources, dict) else resources)
            if item.get('copy', {}).get('name') == 'operationPolicies')

    def evaluate(self, entry):
        return PolicyExpression(self.policy['properties']['value'], self.template['variables'],
            cache={'operationEntries': [entry]}, functions=self.template['functions']).evaluate({})

    def entry(self):
        return {'apiName': SPEECH['name'], 'operation': next(operation for operation in SPEECH['operations'] if operation['name'] == 'tts'),
            'constraint': copy.deepcopy(SPEECH['authorization']['operationConstraints']['tts'])}

    def test_deployed_guard_is_before_base_and_is_the_only_policy_difference(self):
        entry = self.entry()
        rendered = self.evaluate(entry)
        fragment = POLICY.read_text(encoding='utf-8').replace('<fragment>', '').replace('</fragment>', '')
        self.assertIn('<inbound>' + fragment + '<base />', rendered)
        root = ET.fromstring(rendered)
        self.assertEqual([child.tag for child in root.find('inbound')][:3], ['set-variable', 'choose', 'base'])
        entry['apiName'] = 'unrelated-api'
        self.assertEqual(rendered.replace(fragment, ''), self.evaluate(entry))
        self.assertIn('copy-unmatched-params="false"', rendered)
        self.assertIn('<validate-content ', rendered)

    def test_scope_is_exact_canonical_tts_contract_not_other_xml_or_stt_operations(self):
        entry = self.entry()
        variations = [
            ('api', 'other-speech'), ('name', 'stt'), ('method', 'GET'), ('urlTemplate', '/other'),
            ('kind', 'documentAnalysis'), ('bodyFormat', 'binary'), ('contentTypes', ['text/xml']),
            ('contentTypes', ['application/ssml+xml', 'application/xml']), ('bodyFormat', None), ('contentTypes', None),
        ]
        condition = next(item for item in self.template['functions'] if item['namespace'] == '__bicep')['members']['requiresSpeechSsmlGuard']['output']['value']
        for field, value in variations:
            other = copy.deepcopy(entry)
            if field == 'api': other['apiName'] = value
            elif field in ('name', 'method', 'urlTemplate'): other['operation'][field] = value
            elif value is None: other['constraint'].pop(field)
            else: other['constraint'][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(PolicyExpression(condition, {}).evaluate({'entry': other}))


if __name__ == '__main__':
    unittest.main()
