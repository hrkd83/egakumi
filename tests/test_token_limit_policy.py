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



class SubscriptionProfileFetchExpressionTests(unittest.TestCase):
    """取得した不正値を整数へ丸めず拒否し、正規の無制限と区別する。"""

    POLICY = CORE / "apps/gateway/apim/policies/subscription-profile.xml"

    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        node = ET.parse(cls.POLICY).find('.//set-variable[@name="gatewayFetchedProfile"]')
        body = expression_body(node.get("value"))
        source = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public static class ProfileFetchFixture {
  public class Variables : Dictionary<string,object> {
    public T GetValueOrDefault<T>(string key, T fallback) { return ContainsKey(key) ? (T)this[key] : fallback; }
  }
  public class Body { public string Text; public T As<T>() { return (T)(object)JObject.Parse(Text); } }
  public interface IResponse { int StatusCode { get; } Body Body { get; } }
  public class Response : IResponse { public int StatusCode { get; set; } public Body Body { get; set; } }
  public class Named { public string Id = "<SUBSCRIPTION_ID>"; }
  public class Context { public Variables Variables = new Variables(); public Named Subscription = new Named(); }
  static object Fetch(Context context) { __BODY__ }
  public static string Run(string input) {
    var result = new JObject();
    foreach (JObject row in JArray.Parse(input)) {
      var context = new Context();
      context.Variables["gatewayProfileResponse"] = row["status"].Type == JTokenType.Null ? null :
        new Response { StatusCode = (int)row["status"], Body = new Body { Text = (string)row["body"] } };
      result[(string)row["id"]] = (string)Fetch(context);
    }
    return result.ToString(Formatting.None);
  }
}
'''.replace("__BODY__", body)
        profile = {"schemaVersion": 1, "subscriptionId": "<SUBSCRIPTION_ID>",
                   "requestRate": None, "tokensPerMinute": None, "tokenQuota": None}
        cases = [{"id": "null-response", "status": None, "body": "{}"},
                 {"id": "upstream-503", "status": 503, "body": "{}"},
                 {"id": "broken-json", "status": 200, "body": "{broken"},
                 {"id": "valid-unlimited", "status": 200, "body": json.dumps(profile)}]
        for field in ("calls", "renewalPeriodSeconds", "tokens"):
            for kind, value in (("fraction", 1.5), ("string", "2"), ("boolean", True)):
                value_profile = dict(profile)
                if field == "tokens":
                    value_profile["tokenQuota"] = {"tokens": value, "period": "Hourly"}
                else:
                    value_profile["requestRate"] = {"calls": 2, "renewalPeriodSeconds": 60, field: value}
                cases.append({"id": field + "-" + kind, "status": 200, "body": json.dumps(value_profile)})
        for kind, value in (("fraction", 1.1), ("string", "1"), ("boolean", True)):
            cases.append({"id": "schema-" + kind, "status": 200,
                          "body": json.dumps({**profile, "schemaVersion": value})})
        for name, calls, period, tokens in (("minimum", 1, 1, 1), ("maximum", 1_000_000, 300, 2_147_483_647)):
            value_profile = {**profile, "requestRate": {"calls": calls, "renewalPeriodSeconds": period},
                             "tokensPerMinute": tokens, "tokenQuota": {"tokens": tokens, "period": "Yearly"}}
            cases.append({"id": "valid-" + name, "status": 200, "body": json.dumps(value_profile)})
        with tempfile.TemporaryDirectory(prefix="core-profile-fetch-") as directory:
            folder = Path(directory)
            (folder / "fixture.cs").write_text(source, encoding="utf-8")
            (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            runner = folder / "runner.ps1"
            runner.write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[ProfileFetchFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("Actual profile-fetch XML expression failed: " + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_upstream_failure_and_malformed_body_are_not_unlimited_profiles(self):
        for name in ("null-response", "upstream-503", "broken-json", "schema-fraction", "schema-string", "schema-boolean"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name], "")

    def test_non_integer_numeric_fields_are_refused_before_caching(self):
        # The fetch expression must return empty: its caller returns503 before cache-store.
        for field in ("calls", "renewalPeriodSeconds", "tokens"):
            for kind in ("fraction", "string", "boolean"):
                with self.subTest(field=field, kind=kind):
                    self.assertEqual(self.results[field + "-" + kind], "")

    def test_valid_null_and_integer_boundaries_preserve_the_profile(self):
        unlimited = json.loads(self.results["valid-unlimited"])
        self.assertIsNone(unlimited["requestRate"])
        self.assertIsNone(unlimited["tokensPerMinute"])
        self.assertIsNone(unlimited["tokenQuota"])
        for name, calls, period, tokens in (("minimum", 1, 1, 1), ("maximum", 1_000_000, 300, 2_147_483_647)):
            with self.subTest(case=name):
                profile = json.loads(self.results["valid-" + name])
                self.assertEqual(profile["requestRate"], {"calls": calls, "renewalPeriodSeconds": period})
                self.assertEqual(profile["tokensPerMinute"], tokens)
                self.assertEqual(profile["tokenQuota"], {"tokens": tokens, "period": "Yearly"})


if __name__ == "__main__":
    unittest.main()
