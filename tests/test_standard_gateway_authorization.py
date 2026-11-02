"""実XMLの式で購読キーによる共通認可を照合する。Azure通信は行わない。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_api_protocols import CSHARP, actual_expression
from test_gateway_limits_telemetry import PWSH
from configuration_fixtures import api_fixture, configuration, CORE

TENANT = "11111111-1111-4111-8111-111111111111"
PERSON = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class StandardAuthorizationConfigurationTests(unittest.TestCase):
    def test_common_api_authorization_is_key_based_without_token_defaults(self):
        raw=api_fixture()
        result=configuration.normalize_configuration(raw,base_directory=CORE/"config")
        auth=result["apiDefinitions"][0]["authorization"]
        self.assertEqual(auth["mode"],"subscriptionKey")
        self.assertEqual(set(auth), {"mode", "operationConstraints"})


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
        base = CSHARP.split("public static class ExpandedPolicyFixture")[0]
        source = base + r'''
public static class StandardAuthFixture {
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
   if (item["subscription"] != null) {
    c.Subscription = new Named {Id=(string)item["subscription"]};
    c.Request.Headers["Ocp-Apim-Subscription-Key"] = new[] {"SYNTHETIC_KEY"};
   }
   results[(string)item["id"]] = new JObject(new JProperty("http", JObject.Parse(Http(c))),
      new JProperty("protocol", JObject.Parse(Protocol(c))), new JProperty("mcp", JObject.Parse(Mcp(c))));
  }
  return results.ToString(Formatting.None);
 }
}'''
        for marker, filename in (("__HTTP__", "gateway.xml"), ("__PROTOCOL__", "protocol.xml"), ("__MCP__", "mcp.xml")):
            source = source.replace(marker, actual_expression(filename, "gatewayDecision"))
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

    def test_tokens_alone_empty_subscription_and_unknown_modes_do_not_authorize(self):
        for name in ("token-only","anonymous","empty-subscription","unsupported-mode"):
            for protocol in ("http","protocol","mcp"):
                self.assertFalse(self.results[name][protocol]["allowed"],(name,protocol))
