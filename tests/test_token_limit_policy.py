"""トークン制限の実XML式を実行し、数値の戻り型と設定値の維持を検証します。

実APIMの属性検証や分散カウンターは再現しません。型の暗黙変換による見逃しを防ぐため、
式の戻り値をobjectで受け、boxedされた実型を確認します。
"""
import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


CORE = Path(__file__).resolve().parents[1]
POLICY = CORE / "apps/gateway/apim/policies/token-limit.xml"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
PERIODS = ("Hourly", "Daily", "Weekly", "Monthly", "Yearly")


def expression_body(value):
    """XMLから取得した実式を、型を変えずにC#メソッドの本文へ移します。"""
    if value.startswith("@{") and value.endswith("}"):
        return value[2:-1]
    if value.startswith("@(") and value.endswith(")"):
        return "return " + value[2:-1] + ";"
    raise AssertionError("Expected an APIM policy expression")


class TokenLimitExpressionTests(unittest.TestCase):
    """TPMをInt32、累積quotaをInt64で渡し、SQLの値と期間を保持することを確認します。"""

    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        root = ET.parse(POLICY).getroot()
        methods, assignments, calls = [], [], []
        for variable in ("gatewayTokensPerMinute", "gatewayTokenQuota", "gatewayTokenQuotaPeriod"):
            body = expression_body(root.find(f'.//set-variable[@name="{variable}"]').get("value"))
            method = "Read" + variable
            methods.append(f"static object {method}(Context context) {{ {body} }}")
            assignments.append(f'context.Variables["{variable}"] = {method}(context);')
        cls.numeric_attributes = []
        cls.period_attributes = []
        for policy in root.findall(".//llm-token-limit"):
            for attribute in ("tokens-per-minute", "token-quota", "token-quota-period"):
                if policy.get(attribute) is None:
                    continue
                key = policy.get("id") + "/" + attribute
                method = "Attribute" + str(len(calls))
                methods.append(f"static object {method}(Context context) {{ {expression_body(policy.get(attribute))} }}")
                calls.append(f'Add(rows, "{key}", limit, period, {method}(context));')
                (cls.period_attributes if attribute == "token-quota-period" else cls.numeric_attributes).append(key)
        source = r'''
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
public class Context { public Dictionary<string,object> Variables = new Dictionary<string,object>(); }
public static class TokenLimitFixture {
  __METHODS__
  static void Add(JArray rows, string attribute, int limit, string period, object value) {
    rows.Add(new JObject(new JProperty("attribute", attribute), new JProperty("limit", limit),
      new JProperty("period", period), new JProperty("type", value.GetType().FullName), new JProperty("value", value)));
  }
  public static string Run() {
    var rows = new JArray();
    foreach (int limit in new [] {1, Int32.MaxValue}) {
      foreach (string period in new [] {"Hourly", "Daily", "Weekly", "Monthly", "Yearly"}) {
        var context = new Context();
        context.Variables["gatewaySubscriptionProfile"] = new JObject(new JProperty("tokensPerMinute", limit),
          new JProperty("tokenQuota", new JObject(new JProperty("tokens", limit), new JProperty("period", period)))).ToString();
        __ASSIGNMENTS__
        __CALLS__
      }
    }
    return rows.ToString(Newtonsoft.Json.Formatting.None);
  }
}
'''.replace("__METHODS__", "\n".join(methods)).replace("__ASSIGNMENTS__", "\n".join(assignments)).replace("__CALLS__", "\n".join(calls))
        with tempfile.TemporaryDirectory(prefix="core-token-limit-policy-") as directory:
            folder = Path(directory)
            (folder / "fixture.cs").write_text(source, encoding="utf-8")
            runner = folder / "runner.ps1"
            runner.write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[TokenLimitFixture]::Run()
""", encoding="utf-8")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("Actual token-limit XML expressions failed: " + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_numeric_attributes_return_their_required_types_without_changing_boundary_values(self):
        self.assertEqual(len(self.numeric_attributes), 4)
        for attribute in self.numeric_attributes:
            rows = [row for row in self.results if row["attribute"] == attribute]
            self.assertEqual({row["limit"] for row in rows}, {1, 2_147_483_647})
            for row in rows:
                with self.subTest(attribute=attribute, limit=row["limit"], period=row["period"]):
                    expected_type = "System.Int32" if attribute.endswith("/tokens-per-minute") else "System.Int64"
                    self.assertEqual(row["type"], expected_type)
                    self.assertEqual(row["value"], row["limit"])

    def test_quota_period_expressions_preserve_all_supported_profile_periods(self):
        self.assertEqual(len(self.period_attributes), 2)
        for attribute in self.period_attributes:
            rows = [row for row in self.results if row["attribute"] == attribute]
            self.assertEqual({row["period"] for row in rows}, set(PERIODS))
            for row in rows:
                with self.subTest(attribute=attribute, period=row["period"]):
                    self.assertEqual(row["type"], "System.String")
                    self.assertEqual(row["value"], row["period"])


if __name__ == "__main__":
    unittest.main()
