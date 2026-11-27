"""実XMLの式で購読キーによる共通認可を照合する。Azure通信は行わない。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tests.gateway.publication.test_api_protocols import CSHARP, actual_expression

from tests.support.configuration_fixtures import api_fixture, configuration, CORE
from tests.support.powershell import PWSH
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments

TENANT = "11111111-1111-4111-8111-111111111111"
PERSON = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class StandardAuthorizationConfigurationTests(unittest.TestCase):
    def test_common_api_authorization_is_key_based_without_token_defaults(self):
        raw=api_fixture()
        result=configuration.normalize_configuration(raw,base_directory=CORE/"infra/config")
        auth=result["apiDefinitions"][0]["authorization"]
        self.assertEqual(auth["mode"],"subscriptionKey")
        self.assertEqual(set(auth), {"mode", "operationConstraints"})

    def test_formal_configuration_still_rejects_unknown_or_missing_authorization_mode(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                raw = api_fixture()
                authorization = raw["apiDefinitions"][0]["authorization"]
                if missing:
                    authorization.pop("mode")
                else:
                    authorization["mode"] = "unknown"
                with self.assertRaisesRegex(configuration.ConfigurationError, r"authorization\.mode"):
                    configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")


class StandardAuthorizationExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required to compile actual APIM expressions")
        auth = {"mode":"subscriptionKey"}
        claims = {"ver":"2.0","tid":TENANT,"oid":PERSON,"scp":"Gateway.Invoke"}
        cases=[]
        def add(name, **changes): cases.append({"id":name,"config":copy.deepcopy(auth),"claims":copy.deepcopy(claims),**changes})
        add("key",subscription="standard-key",claims=None)
        add("other-key",subscription="another-key",claims=None)
        add("key-and-token",subscription="standard-key")
        add("key-and-unrelated-token",subscription="standard-key",claims={"oid":"untrusted"})
        add("token-only")
        add("anonymous",claims=None)
        add("empty-subscription",claims=None,subscription="")
        add("unsupported-mode",subscription="standard-key",config={"mode":"unknown"})
        add("missing-mode",subscription="standard-key",config={})
        add("unsupported-mode-anonymous",claims=None,config={"mode":"unknown"})
        add("unsupported-mode-token-only",config={"mode":"unknown"})
        base = CSHARP.split("public static class ExpandedPolicyFixture")[0]
        source = base + r'''
public static class StandardAuthFixture {
 public static string CommonKeyValidation(Context context) { __COMMON_KEY_VALIDATION__ }
 public static string Http(Context context) { __HTTP__ }
 public static string Protocol(Context context) { __PROTOCOL__ }
 public static string Mcp(Context context) { __MCP__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Variables["config"] = item["config"].ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"] = "Ocp-Apim-Subscription-Key";
   if (item["claims"] is JObject) {
    c.Request.Headers["Authorization"] = new[] {"Bearer SYNTHETIC_UNTRUSTED_TOKEN"};
   }
   if (item["subscription"]?.Type == JTokenType.String) {
    c.Subscription = new Named {Id=(string)item["subscription"]};
    c.Product = new Named {Id="standard-users"};
    c.Request.Headers["Ocp-Apim-Subscription-Key"] = new[] {"SYNTHETIC_KEY"};
   }
   var failure = CommonKeyValidation(c);
   if (!String.IsNullOrEmpty(failure)) {
    var denied = new JObject(new JProperty("allowed", false));
    results[(string)item["id"]] = new JObject(new JProperty("http", denied.DeepClone()),
       new JProperty("protocol", denied.DeepClone()), new JProperty("mcp", denied.DeepClone()));
    continue;
   }
   c.Variables["gatewayCommonAuthenticationValidated"] = true;
   // 実APIで先行する操作設定準備の成功結果を、直接式fixtureにも用意します。
   c.Variables["gatewayOperationSelection"] = new JObject(new JProperty("allowed", true),
      new JProperty("settingId", "")).ToString(Formatting.None);
   results[(string)item["id"]] = new JObject(new JProperty("http", JObject.Parse(Http(c))),
      new JProperty("protocol", JObject.Parse(Protocol(c))), new JProperty("mcp", JObject.Parse(Mcp(c))));
  }
  return results.ToString(Formatting.None);
 }
}'''
        for marker, filename in (("__HTTP__", "rest.xml"), ("__PROTOCOL__", "stream.xml"), ("__MCP__", "generated.xml")):
            source = source.replace(marker, actual_expression(filename, "gatewayDecision"))
        common = ET.fromstring(render_common_authentication_fragments(entra_enabled=False)['core-common-authentication'])
        key_validation = common.find("./choose/otherwise/set-variable[@name='gatewayAuthenticationFailure']").get('value').strip()
        self_expression = key_validation[2:-1]
        if not key_validation.startswith('@{') or not key_validation.endswith('}'):
            raise AssertionError('Expected the actual inherited key validation expression')
        source = source.replace('__COMMON_KEY_VALIDATION__', self_expression)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "policy.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[StandardAuthFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result = subprocess.run([PWSH, "-NoProfile", "-File", str(path / "run.ps1")], capture_output=True, text=True,
                                    timeout=60, env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.results = json.loads(result.stdout)

    def test_key_subject_is_identical_across_protocols_and_optional_tokens(self):
        for name in ("key","other-key","key-and-token","key-and-unrelated-token"):
            for protocol in ("http","protocol","mcp"):
                decision=self.results[name][protocol]
                self.assertTrue(decision["allowed"],(name,protocol))
                self.assertEqual(decision["method"],"subscriptionKey")
                self.assertEqual(decision["callerId"],"subscription:"+("another-key" if name=="other-key" else "standard-key"))

    def test_unverified_requests_do_not_authorize_even_if_mode_configuration_changes(self):
        for name in ("token-only","anonymous","empty-subscription","unsupported-mode-anonymous","unsupported-mode-token-only"):
            for protocol in ("http","protocol","mcp"):
                self.assertFalse(self.results[name][protocol]["allowed"],(name,protocol))

    def test_all_templates_inherit_common_authentication_without_using_the_mode_configuration(self):
        for name in ("unsupported-mode", "missing-mode"):
            for protocol in ("http", "protocol", "mcp"):
                self.assertEqual(self.results[name][protocol], self.results["key"][protocol], (name, protocol))
