"""入口確認の分離前後を、同じXML/C#とHTTP・gRPCの合成要求で比較する。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_authentication import (
    _render_inline_trusted_ingress_service_policy, _structure, render_service_policy,
)
from deployment.helpers.apim_ingress import (
    expand_trusted_ingress_fragment, render_trusted_ingress_fragment,
)
from test_common_authentication import CSHARP, compile_inbound
from test_gateway_limits_telemetry import PWSH
from test_trusted_ingress_fragments import with_expected_observation_explanation
from browser_exception_projection import project_browser_exception_to_previous


class TrustedIngressStructureTests(unittest.TestCase):
    def test_leaf_expansion_matches_the_entire_previous_executable_policy(self):
        previous = ET.fromstring(with_expected_observation_explanation(_render_inline_trusted_ingress_service_policy()))
        current = ET.fromstring(project_browser_exception_to_previous(
            expand_trusted_ingress_fragment(render_service_policy())))
        self.assertEqual(_structure(current), _structure(previous))
        fragment = ET.fromstring(render_trusted_ingress_fragment())
        self.assertEqual(_structure(fragment[0]), _structure(previous.find("inbound/choose[@id='core-trusted-ingress']")))
        self.assertFalse(fragment.findall('.//include-fragment'))


class TrustedIngressBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest('PowerShell with Roslyn is required; Azure was not tested')
        key = {'X-Gateway-Key': 'SYNTHETIC_KEY'}
        cls.cases = [
            {'id': 'http-valid', 'headers': {**key, 'X-Core-L7-Ingress': 'fixture-ingress'}},
            {'id': 'http-missing', 'headers': key},
            {'id': 'http-wrong', 'headers': {**key, 'X-Core-L7-Ingress': 'incorrect-ingress'}},
            {'id': 'http-empty', 'headers': {**key, 'X-Core-L7-Ingress': ''}},
            {'id': 'http-duplicate', 'headers': {**key, 'X-Core-L7-Ingress': ['fixture-ingress', 'fixture-ingress']}},
            {'id': 'http-no-key', 'headers': {'X-Core-L7-Ingress': 'fixture-ingress'}},
            {'id': 'grpc-missing', 'headers': key, 'grpc': True},
            {'id': 'grpc-wrong', 'headers': {**key, 'X-Core-L7-Ingress': 'incorrect-ingress'}, 'grpc': True},
            {'id': 'grpc-duplicate', 'headers': {**key, 'X-Core-L7-Ingress': ['first', 'second']}, 'grpc': True},
        ]
        for case in cls.cases:
            case['subscription'] = True
        cls.results = {}
        for required in ('true', 'false'):
            for label, renderer in (('inline', _render_inline_trusted_ingress_service_policy), ('fragment', render_service_policy)):
                policy = expand_trusted_ingress_fragment(renderer())
                policy = policy.replace('{{core-l7-ingress-enabled}}', required)
                policy = policy.replace('{{core-l7-ingress-secret}}', 'fixture-ingress')
                methods, inbound = compile_inbound(ET.fromstring(policy))
                methods += '\nstatic bool Guard(Context context, string name) { throw new Exception("Unused guard"); }'
                with tempfile.TemporaryDirectory(prefix='core-trusted-ingress-') as directory:
                    folder = Path(directory)
                    (folder / 'policy.cs').write_text(CSHARP.replace('__METHODS__', methods).replace('__INBOUND__', inbound), encoding='utf-8')
                    (folder / 'cases.json').write_text(json.dumps(cls.cases), encoding='utf-8')
                    (folder / 'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[CommonAuthenticationFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding='utf-8')
                    result = subprocess.run([PWSH, '-NoProfile', '-File', str(folder / 'run.ps1')], capture_output=True, text=True, timeout=60)
                    if result.returncode:
                        raise AssertionError(result.stderr)
                    cls.results[(required, label)] = json.loads(result.stdout)

    def test_all_request_and_authentication_results_match_before_and_after(self):
        for required in ('true', 'false'):
            with self.subTest(required=required):
                self.assertEqual(self.results[(required, 'fragment')], self.results[(required, 'inline')])

    def test_bad_http_ingress_is_rejected_before_authentication(self):
        results = self.results[('true', 'fragment')]
        for name in ('http-missing', 'http-wrong', 'http-empty', 'http-duplicate'):
            with self.subTest(case=name):
                self.assertEqual(results[name]['status'], 403)
                self.assertFalse(results[name]['authenticated'])
                self.assertIsNotNone(results[name]['requestContext'])

    def test_registered_grpc_and_valid_http_still_reach_authentication(self):
        results = self.results[('true', 'fragment')]
        for name in ('http-valid', 'grpc-missing', 'grpc-wrong', 'grpc-duplicate'):
            with self.subTest(case=name):
                self.assertEqual(results[name]['status'], 200)
                self.assertTrue(results[name]['authenticated'])
        self.assertEqual(results['http-no-key']['status'], 401)

    def test_disabling_the_ingress_requirement_does_not_disable_authentication(self):
        results = self.results[('false', 'fragment')]
        for case in self.cases:
            name = case['id']
            with self.subTest(case=name):
                self.assertEqual(results[name]['status'], 401 if name == 'http-no-key' else 200)
                self.assertEqual(results[name]['authenticated'], name != 'http-no-key')


if __name__ == '__main__':
    unittest.main()
