"""実C#式で、購読拒否とToolの上流障害が混同されないことを検証します。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_gateway_limits_telemetry import PWSH

ROOT = Path(__file__).resolve().parents[1]

class ToolAuthorizationErrorTests(unittest.TestCase):
    def test_native_subscription_rejection_is_401_and_upstream_error_is_preserved(self):
        if not PWSH:
            self.skipTest("PowerShell Roslyn is required")
        expressions = {}
        for file, variable in (("pii.xml", "gatewayAuditStatus"), ("document-analysis.xml", "documentPolicyErrorStatus")):
            root = ET.parse(ROOT / "apps/gateway/apim/policies" / file)
            value = root.find("./on-error/set-variable[@name='" + variable + "']").get("value")
            expressions[file] = value[2:-1]
        source = r'''
using System;
public class LastError { public string Source,Reason; }
public class Context { public LastError LastError=new LastError(); }
public static class ToolErrorFixture {
 public static int Pii(Context context) { return __PII__; }
 public static int Document(Context context) { return __DOCUMENT__; }
 public static string Run() {
  var cases=new[]{new[]{"authorization","SubscriptionKeyNotFound"},new[]{"authorization","SubscriptionKeyInvalid"},new[]{"validate-jwt","TokenExpired"},new[]{"forward-request","BackendConnectionFailure"},new[]{"forward-request","BackendConnectionTimeout"},new[]{"set-variable","ExpressionValueEvaluationFailure"}};
  var result=new System.Collections.Generic.List<int[]>();
  foreach(var pair in cases){var c=new Context();c.LastError.Source=pair[0];c.LastError.Reason=pair[1];result.Add(new[]{Pii(c),Document(c)});}
  return Newtonsoft.Json.JsonConvert.SerializeObject(result);
 }
}'''.replace("__PII__",expressions["pii.xml"]).replace("__DOCUMENT__",expressions["document-analysis.xml"])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/"fixture.cs").write_text(source,encoding="utf-8")
            (path/"run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[ToolErrorFixture]::Run()
""",encoding="utf-8")
            result=subprocess.run([PWSH,"-NoProfile","-File",str(path/"run.ps1")],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[[401,401],[401,401],[401,502],[502,502],[504,502],[502,502]])

if __name__ == "__main__": unittest.main()
