"""APIM が C# より厳しく要求する制御文の波括弧をローカルで検査する。

XML から実際の式を取り出し、PowerShell 同梱の Roslyn で構文木にする。
通常の C# コンパイルでは合法な ``if (...) foreach (...)`` も、APIM の
Razor では拒否されるため、コンパイル試験とは別に各本文の Block を確認する。
APIM の完全な検証器ではなく、この既知の構文制約を Azure へ送る前に検出する試験。
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.is_file() else None


def policy_expressions():
    """コメントや JSON 本文を除き、XML 属性と要素本文の APIM 式を集める。"""
    cases = []
    for path in sorted(set([*POLICIES.glob("*.xml"),
                            *(POLICIES / "credential-login").glob("*.xml"), SERVICE_POLICY])):
        root = ET.fromstring("<root>" + path.read_text(encoding="utf-8") + "</root>")
        for index, element in enumerate(root.iter()):
            values = [("text", element.text), *element.attrib.items()]
            for field, raw in values:
                value = (raw or "").strip()
                if value.startswith("@{") and value.endswith("}"):
                    body = value[2:-1]
                elif value.startswith("@(") and value.endswith(")"):
                    body = "return " + value[2:-1] + ";"
                else:
                    continue
                cases.append({"id": f"{path.name}:{index}:{field}", "body": body})
    return cases


# 構文木を使うため、文字列中の "if" やコメント、括弧を含む条件式を誤検出しない。
# else if は Razor が認める連鎖なので、else 本文には Block と IfStatement を認める。
AUDIT = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName Microsoft.CodeAnalysis.CSharp
$results=@()
foreach ($case in (Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json') | ConvertFrom-Json)) {
    $source='class Fixture { object Run() {'+$case.body+'} }'
    $tree=[Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree]::ParseText($source)
    $failures=@($tree.GetDiagnostics() | Where-Object Severity -eq Error | ForEach-Object ToString)
    foreach ($node in $tree.GetRoot().DescendantNodes()) {
        $kind=$node.Kind().ToString()
        if ($kind -in @('IfStatement','ForEachStatement','ForEachVariableStatement',
                       'ForStatement','WhileStatement','DoStatement','UsingStatement','LockStatement')) {
            if ($node.Statement.Kind().ToString() -ne 'Block') {
                $failures+='Razor requires a block body for '+$kind
            }
        } elseif ($kind -eq 'ElseClause') {
            if ($node.Statement.Kind().ToString() -notin @('Block','IfStatement')) {
                $failures+='Razor requires a block body for ElseClause'
            }
        }
    }
    $results+=@{id=$case.id;failures=@($failures)}
}
ConvertTo-Json -InputObject @($results) -Depth 5 -Compress
"""


class PolicyRazorSyntaxTests(unittest.TestCase):
    """全ポリシーと既知の不正例を同じ構文検査に通し、取りこぼしを防ぐ。"""

    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with Roslyn is required; no Azure checks were performed")
        cls.cases = policy_expressions()
        # C# としては合法でも Razor では使えない形を、検査器自身が検出するか確認する。
        fixtures = [
            {"id": "invalid-if-foreach", "body": "if (true) foreach (var x in new int[0]) { } return null;"},
            {"id": "invalid-loop", "body": "foreach (var x in new int[0]) if (true) { } return null;"},
            {"id": "invalid-else", "body": "if (true) { } else return null; return null;"},
            {"id": "valid-nested", "body": "if (true) { foreach (var x in new int[0]) { } } else if (false) { } return null;"},
        ]
        with tempfile.TemporaryDirectory(prefix="core-policy-razor-") as temp:
            base = Path(temp)
            (base / "cases.json").write_text(json.dumps(cls.cases + fixtures))
            script = base / "audit.ps1"
            script.write_text(AUDIT)
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode:
                raise AssertionError("Policy syntax audit could not run:\n" + result.stderr)
            cls.results = {item["id"]: item["failures"] for item in json.loads(result.stdout)}

    def test_all_policy_expressions_use_razor_block_bodies(self):
        self.assertTrue(self.cases, "No real policy expressions were checked")
        for case in self.cases:
            with self.subTest(expression=case["id"]):
                self.assertEqual(self.results[case["id"]], [])

    def test_credential_login_operation_policies_are_included(self):
        for name in ("start", "status", "launch-post", "callback", "logout"):
            with self.subTest(operation=name):
                self.assertTrue(any(case["id"].startswith(name + ".xml:") for case in self.cases))

    def test_audit_detects_legal_csharp_that_razor_rejects(self):
        for case_id in ("invalid-if-foreach", "invalid-loop", "invalid-else"):
            with self.subTest(case=case_id):
                self.assertTrue(self.results[case_id])

    def test_nested_blocks_and_else_if_are_accepted(self):
        self.assertEqual(self.results["valid-nested"], [])


if __name__ == "__main__":
    unittest.main()
