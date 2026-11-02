"""展開済み CM ポリシーを APIM と同系統の Razor パーサーで検査する。

Microsoft.AspNet.Razor 3.2.9 の公式パッケージに含まれる System.Web.Razor.dll を
WORKFORCE_RAZOR_DLL で指定して実行する。通常の C# コンパイルだけでは検出できない
raw XML の &quot; と URL 中の // の組み合わせを、実際のパーサーで再現する。
依存 DLL がない環境はこの追加検査をスキップし、通常の XML/C# 試験を利用する。
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from deployment.helpers import credential_login
from test_credential_login import settings


AUDIT = r"""
$ErrorActionPreference='Stop'
Add-Type -Path $env:WORKFORCE_RAZOR_DLL
$hostSettings=[System.Web.Razor.RazorEngineHost]::new([System.Web.Razor.CSharpRazorCodeLanguage]::new())
$engine=[System.Web.Razor.RazorTemplateEngine]::new($hostSettings)
$results=@()
foreach ($case in (Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json') | ConvertFrom-Json)) {
    $parsed=$engine.ParseTemplate([System.IO.StringReader]::new($case.xml))
    $results+=@{id=$case.id;errors=@($parsed.ParserErrors | ForEach-Object { $_.Message })}
}
ConvertTo-Json -InputObject @($results) -Depth 5 -Compress
"""


class CredentialRawXmlRazorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assembly = os.environ.get("WORKFORCE_RAZOR_DLL")
        pwsh = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
        if not assembly or not Path(assembly).is_file() or not pwsh:
            raise unittest.SkipTest("Set WORKFORCE_RAZOR_DLL to the official System.Web.Razor.dll for raw XML parsing")
        login = settings()
        cls.names = sorted({**credential_login.OPERATIONS, **credential_login.BROWSER_OPERATIONS})
        cases = [{"id": name, "xml": credential_login.render(name + ".xml", login)} for name in cls.names]
        for filename in ('credential-connection.xml','credential-session-use.xml','credential-connection-outbound.xml','credential-challenge.xml'):
            cls.names.append(filename)
            cases.append({'id':filename,'xml':credential_login.render_connection(filename,login)})
        lookup = '(string)JObject.Parse((string)context.Variables[&quot;credentialLoginSettings&quot;])[&quot;publicOrigin&quot;]'
        rendered = next(case["xml"] for case in cases if case["id"] == "launch-post")
        if lookup not in rendered:
            raise AssertionError("The regression fixture must replace the actual origin comparison")
        cls.origin_is_configuration = lookup in rendered
        with tempfile.TemporaryDirectory(prefix="core-cm-raw-razor-") as temp:
            path = Path(temp)
            (path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            script = path / "audit.ps1"
            script.write_text(AUDIT, encoding="utf-8")
            result = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("Raw policy Razor audit failed:\n" + result.stderr)
            cls.results = {case["id"]: case["errors"] for case in json.loads(result.stdout)}

    def test_rendered_raw_policies_have_no_razor_parse_errors(self):
        for name in self.names:
            with self.subTest(operation=name):
                self.assertEqual(self.results[name], [])

    def test_origin_comparison_reads_encoded_configuration(self):
        self.assertTrue(self.origin_is_configuration)


if __name__ == "__main__":
    unittest.main()
