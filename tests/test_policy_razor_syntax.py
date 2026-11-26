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
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from policy_fixture_paths import policy_fixture_path


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.apim_audit import SOURCE_EXPRESSION_FILES, render_audit_policy

POLICIES = CORE / "apps/gateway/apim/policies"
SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.is_file() else None


def policy_expressions():
    """配備と同じ監査rendererでC#正本を展開し、XML内のAPIM式を集める。"""
    cases = []
    for path in sorted(set([*POLICIES.glob("*.xml"),
                            *(POLICIES / "api-templates").glob("*.xml"),
                            *(POLICIES / "credential-login").glob("*.xml"), SERVICE_POLICY,
                            POLICIES / "fragments/request/core-query-observation/core-query-observation.xml",
                            POLICIES / "fragments/request/core-api-input-validation/core-api-input-validation.xml",
                            policy_fixture_path(POLICIES, "core-api-target-validation.xml"),
                            policy_fixture_path(POLICIES, "core-entra-user-profile.xml"),
                            policy_fixture_path(POLICIES, "core-credential-connection.xml"),
                            policy_fixture_path(POLICIES, "core-credential-connection-outbound.xml"),
                            policy_fixture_path(POLICIES, "core-credential-session-use.xml"),
                            policy_fixture_path(POLICIES, "core-backend-audit.xml"),
                            policy_fixture_path(POLICIES, "core-audit-body-model.xml"),
                            policy_fixture_path(POLICIES, "core-audit-context.xml"),
                            policy_fixture_path(POLICIES, "realtime-create.xml"),
                            policy_fixture_path(POLICIES, "live-create.xml"),
                            policy_fixture_path(POLICIES, "pii.xml"),
                            policy_fixture_path(POLICIES, "document-analysis.xml"),
                            policy_fixture_path(POLICIES, "document-analysis-status.xml"),
                            policy_fixture_path(POLICIES, "credential-telemetry.xml"),
                            policy_fixture_path(POLICIES, "realtime-hangup.xml"),
                            policy_fixture_path(POLICIES, "core-realtime-observer.xml"),
                            policy_fixture_path(POLICIES, "realtime-owner-context.xml"),
                            policy_fixture_path(POLICIES, "credential-challenge.xml"),
                            policy_fixture_path(POLICIES, "credential-login/start.xml"),
                            policy_fixture_path(POLICIES, "credential-login/launch.xml"),
                            policy_fixture_path(POLICIES, "credential-login/launch-post.xml"),
                            policy_fixture_path(POLICIES, "credential-login/identity-callback.xml"),
                            policy_fixture_path(POLICIES, "credential-login/callback.xml"),
                            policy_fixture_path(POLICIES, "credential-login/status.xml"),
                            policy_fixture_path(POLICIES, "credential-login/disconnect.xml"),
                            policy_fixture_path(POLICIES, "credential-login/logout-all.xml"),
                            policy_fixture_path(POLICIES, "credential-login/logout.xml"),
                            policy_fixture_path(POLICIES, "credential-login/session-commit.xml"),
                            POLICIES / "snippets/authentication/credential-caller-error/credential-caller-error.xml",
                            policy_fixture_path(POLICIES, "subscription-profile.xml"),
                            policy_fixture_path(POLICIES, "rate-limit-error.xml"),
                            policy_fixture_path(POLICIES, "mcp-rate-cleanup.xml"),
                            policy_fixture_path(POLICIES, "core-model-limits.xml")])):
        rendered = render_audit_policy(path.read_text(encoding="utf-8"))
        if any(marker in rendered for marker in SOURCE_EXPRESSION_FILES):
            raise AssertionError(f"Audit source markers remain unexpanded in {path.name}")
        root = ET.fromstring("<root>" + rendered + "</root>")
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
        for name in ("start", "status", "launch-post", "callback", "logout", "core-credential-connection", "core-credential-session-use"):
            with self.subTest(operation=name):
                self.assertTrue(any(case["id"].startswith(name + ".xml:") for case in self.cases))

    def test_core_query_guard_expression_is_checked_from_its_canonical_fragment(self):
        cases = [case for case in self.cases if case["id"].startswith("core-query-observation.xml:")]
        self.assertEqual(len(cases), 1)
        self.assertEqual(self.results[cases[0]["id"]], [])

    def test_core_input_validation_expression_is_checked_from_its_canonical_fragment(self):
        cases = [case for case in self.cases if case["id"].startswith("core-api-input-validation.xml:")]
        self.assertEqual(len(cases), 1)
        self.assertEqual(self.results[cases[0]["id"]], [])

    def test_employee_profile_expression_is_checked_from_its_canonical_fragment(self):
        cases = [case for case in self.cases if case["id"].startswith("core-entra-user-profile.xml:")]
        self.assertEqual(len(cases), 1)
        self.assertEqual(self.results[cases[0]["id"]], [])

    def test_subscription_profile_expressions_are_checked_from_the_canonical_snippet(self):
        cases = [case for case in self.cases if case["id"].startswith("subscription-profile.xml:")]
        self.assertTrue(any('gatewayProfileResponse' in case['body'] for case in cases))
        self.assertTrue(any(case['body'].strip() == 'return 503;' for case in cases))
        for case in cases:
            self.assertEqual(self.results[case['id']], [])

    def test_audit_detects_legal_csharp_that_razor_rejects(self):
        for case_id in ("invalid-if-foreach", "invalid-loop", "invalid-else"):
            with self.subTest(case=case_id):
                self.assertTrue(self.results[case_id])

    def test_nested_blocks_and_else_if_are_accepted(self):
        self.assertEqual(self.results["valid-nested"], [])


if __name__ == "__main__":
    unittest.main()
