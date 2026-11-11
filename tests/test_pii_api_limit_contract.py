"""共通API上限→PII操作の継承順と、実C#式の公開エラー契約を検証する。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import DiscoveryExpression
from bicep_helpers import compile_bicep
from test_gateway_limits_telemetry import PWSH, expression
import test_pii_policy as fixtures

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "apps/gateway/apim/policies"


class PiiApiLimitContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required for real C# expressions")
        template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        api = json.loads((ROOT / "apps/gateway/apim/apis/pii-v1.json").read_text())
        api.update(apiType="http", backendUrl="https://backend.example.com",
                   backendAuthentication={"mode": "none"}, backendHeaders={})
        def policy(resource):
            return DiscoveryExpression(template["resources"][resource]["properties"]["value"],
                template["variables"], functions=template.get("functions", [])).evaluate({"apiDefinitions": [api]}, 0)
        cls.api = ET.fromstring(policy("apiPolicies"))
        cls.operation = ET.fromstring(policy("operationPolicies"))
        cls.mapping = cls.api.find("./inbound/choose/when/choose[@id='core-pii-public-limit-error']/when")
        if cls.mapping is None:
            raise AssertionError("Generated PII API is missing its pre-operation limit response")
        condition = cls.mapping.get("condition")
        if not condition.startswith("@(") or not condition.endswith(")"):
            raise AssertionError("Expected a compiled PII limit condition")
        mapped_body = cls.mapping.find("./return-response/set-body").text.strip()
        if not mapped_body.startswith("@{") or not mapped_body.endswith("}"):
            raise AssertionError("Expected a compiled PII limit body")
        input_error = cls.operation.find("./inbound/choose/when/return-response/set-body").text.strip()
        methods = r'''
public static class PiiApiLimitFixture {
  static string Limits(Context context) { __LIMITS__ }
  static bool Map(Context context) { return __CONDITION__; }
  static string MapBody(Context context) { __BODY__ }
  static int Input(Context context) { __INPUT__ }
  static string Request(Context context) { __REQUEST__ }
  static string InputError(Context context) { return __INPUT_ERROR__; }
  public static string Run(string casesJson) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(casesJson)) {
      var context = new Context { Request = new Request { Body = new Body { Data = (string)item["input"] ?? "{}" } } };
      context.Request.Headers["Content-Type"] = new [] { "application/json" };
      var constraint = (JObject)item["constraint"];
      context.Variables["gatewayOperationConstraint"] = constraint.ToString(Formatting.None);
      context.Variables["piiSettings"] = constraint.ToString(Formatting.None);
      context.Variables["piiDocumentId"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
      var limit = JObject.Parse(Limits(context));
      context.Variables["gatewayAuditStatus"] = (int?)item["overrideStatus"] ?? (int)limit["status"];
      context.Variables["gatewayAuditCode"] = (string)item["overrideCode"] ?? (string)limit["code"];
      var mapped = Map(context);
      var result = new JObject(new JProperty("limit", limit), new JProperty("mapped", mapped),
        new JProperty("operationReached", false), new JProperty("requestConstructed", false));
      if (!(bool)limit["allowed"]) {
        result["body"] = mapped ? JObject.Parse(MapBody(context)) :
          new JObject(new JProperty("error", new JObject(new JProperty("code", context.Variables["gatewayAuditCode"]))),
            new JProperty("requestId", context.RequestId.ToString()));
      } else if ((string)constraint["kind"] == "pii") {
        result["operationReached"] = true;
        var status = Input(context);
        result["operationStatus"] = status;
        context.Variables["piiInputStatus"] = status;
        if (status == 200) {
          var request = JObject.Parse(Request(context));
          result["requestConstructed"] = true;
          result["forwardedTextUnchanged"] = (string)request["analysisInput"]["documents"][0]["text"] ==
            (string)JObject.Parse(context.Request.Body.Data)["text"];
          result["textElementMode"] = (string)request["parameters"]["stringIndexType"];
        }
        else result["body"] = JObject.Parse(InputError(context));
      }
      results[(string)item["id"]] = result;
    }
    return results.ToString(Formatting.None);
  }
}'''
        source = fixtures.CSHARP.split("public static class PiiPolicyFixture", 1)[0] + methods
        source = source.replace("__LIMITS__", expression(POLICIES / "model-limits.xml", "./set-variable", "value"))
        source = source.replace("__CONDITION__", condition[2:-1]).replace("__BODY__", mapped_body[2:-1])
        source = source.replace("__INPUT__", fixtures.INPUT_EXPRESSION).replace("__REQUEST__", fixtures.REQUEST_EXPRESSION)
        source = source.replace("__INPUT_ERROR__", input_error[2:-1])
        # Small explicit adopter contract keeps byte-boundary fixtures compact;
        # shipped provider-envelope boundary is exercised in test_pii_policy.
        settings = {**api["authorization"]["operationConstraints"]["pii_redact_text"], "maxRequestBytes": 32768, "allowedLanguages": ["ja", "en"]}
        cls.cases = []
        for size in (32767, 32768, 32769):
            body = json.dumps({"text": "x" * (size - 27), "language": "en"}, separators=(",", ":"))
            if len(body.encode()) != size:
                raise AssertionError("Independent fixture byte length differs")
            cls.cases.append({"id": "pii-" + str(size), "input": body, "constraint": settings})
        # Equivalent JSON representations keep one logical budget. Escaping a
        # printable ASCII character as \u0061 is the six-byte worst case.
        escaped_ascii = '{"text":"' + r"\u0061" * (32768 - 27) + '","language":"en"}'
        compact_ascii = json.dumps(json.loads(escaped_ascii), separators=(",", ":"))
        if len(compact_ascii.encode()) != 32768 or len(escaped_ascii.encode()) <= 32768:
            raise AssertionError("Escaped ASCII fixture does not cross the old raw-byte gate")
        cls.cases.append({"id": "escaped-ascii", "input": escaped_ascii, "constraint": settings})
        for name, text in (("escaped-ja", "か\u3099" * 5120),
                           ("unicode-values-unchanged", " \ne\u0301\u00e9\uff21🙂\n ")):
            cls.cases.append({"id": name, "input": json.dumps({"text": text, "language": "ja"}, ensure_ascii=True),
                              "constraint": settings})
        small = '{"text":"synthetic","language":"en"}'
        for size in (196608, 196609):
            body = small + " " * (size - len(small.encode()))
            if len(body.encode()) != size:
                raise AssertionError("Raw wire fixture length differs")
            cls.cases.append({"id": "wire-" + str(size), "input": body, "constraint": settings})
        cls.cases.append({"id": "wire-over-malformed", "input": "{" + " " * 196608, "constraint": settings})
        # The input policy deliberately delegates TextElements_v8 counting to
        # Language. Both sizes reach that fixed API without UTF-16 truncation;
        # service rejection/truncation mapping is tested in test_pii_policy.
        for count in (5120, 5121):
            for name, text in (("ascii", "a" * count), ("combining", "か\u3099" * count)):
                cls.cases.append({"id": f"elements-{name}-{count}",
                                  "input": json.dumps({"text": text, "language": "ja"}, ensure_ascii=False),
                                  "constraint": settings})
        for name, body in (("malformed", "{bad-json"), ("language", '{"text":"synthetic","language":"unknown"}'),
                           ("extra", '{"text":"synthetic","language":"en","extra":true}')):
            cls.cases.append({"id": name, "input": body, "constraint": settings})
        cls.cases.append({"id": "other-kind", "input": "x" * 32769,
                          "constraint": {"kind": "registeredOperation", "maxRequestBytes": 32768}})
        for name, status, code in (("other-status", 400, "REQUEST_TOO_LARGE"),
                                   ("other-code", 413, "INVALID_REQUEST"),
                                   ("auth-denial", 403, "AUTHORIZATION_DENIED")):
            cls.cases.append({"id": name, "input": "x" * 196609, "constraint": settings,
                              "overrideStatus": status, "overrideCode": code})
        with tempfile.TemporaryDirectory(prefix="pii-api-inheritance-") as directory:
            path = Path(directory)
            (path / "fixture.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cls.cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701,1702'
[PiiApiLimitFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                    capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError("Generated PII limit C# compilation/execution failed:\n" + result.stderr)
        cls.results = json.loads(result.stdout)

    def test_generated_api_auth_and_common_byte_guard_run_before_operation_schema(self):
        inbound = list(self.api.find("inbound"))
        base = next(i for i, item in enumerate(inbound) if item.tag == "base")
        limits = next(i for i, item in enumerate(inbound) if item.get("fragment-id") == "core-model-limits")
        self.assertLess(base, limits)
        self.assertEqual(list(self.operation.find("inbound"))[0].tag, "base")
        self.assertIn("apiPolicies", compile_bicep("apps/gateway/apim/api-publication.bicep")["resources"]["operationPolicies"]["dependsOn"])
        response = self.mapping.find("return-response")
        self.assertEqual(response.find("set-status").get("code"), "413")
        self.assertEqual(response.find("./set-header[@name='Cache-Control']/value").text, "no-store")

    def test_compact_limit_is_inclusive_and_excess_stops_before_language(self):
        for size in (32767, 32768):
            result = self.results["pii-" + str(size)]
            self.assertTrue(result["limit"]["allowed"])
            self.assertTrue(result["operationReached"])
            self.assertTrue(result["requestConstructed"])
        result = self.results["pii-32769"]
        self.assertTrue(result["limit"]["allowed"])
        self.assertFalse(result["mapped"])
        self.assertTrue(result["operationReached"])
        self.assertEqual(result["operationStatus"], 413)
        self.assertFalse(result["requestConstructed"])
        self.assertEqual(result["body"]["error"], {"code": "PiiInputTooLarge"})
        self.assertEqual(set(result["body"]), {"error", "requestId"})

    def test_escaped_representation_and_unicode_values_keep_the_same_budget_and_text(self):
        for name in ("escaped-ascii", "escaped-ja", "unicode-values-unchanged"):
            with self.subTest(name=name):
                result = self.results[name]
                self.assertTrue(result["limit"]["allowed"])
                self.assertEqual(result["operationStatus"], 200)
                self.assertTrue(result["forwardedTextUnchanged"])

    def test_raw_wire_cap_is_inclusive_and_precedes_json_parsing(self):
        result = self.results["wire-196608"]
        self.assertTrue(result["limit"]["allowed"])
        self.assertEqual(result["operationStatus"], 200)
        for name in ("wire-196609", "wire-over-malformed"):
            with self.subTest(name=name):
                result = self.results[name]
                self.assertEqual(result["limit"], {"allowed": False, "status": 413, "code": "REQUEST_TOO_LARGE"})
                self.assertTrue(result["mapped"])
                self.assertFalse(result["operationReached"])
                self.assertFalse(result["requestConstructed"])
                self.assertEqual(result["body"]["error"], {"code": "PiiInputTooLarge"})

    def test_5120_and_5121_element_inputs_are_not_utf16_normalized_or_truncated(self):
        for count in (5120, 5121):
            for name in ("ascii", "combining"):
                with self.subTest(count=count, name=name):
                    result = self.results[f"elements-{name}-{count}"]
                    self.assertEqual(result["operationStatus"], 200)
                    self.assertTrue(result["forwardedTextUnchanged"])
                    self.assertEqual(result["textElementMode"], "TextElements_v8")

    def test_under_limit_input_errors_keep_the_operation_400_contract(self):
        for name in ("malformed", "language", "extra"):
            result = self.results[name]
            self.assertTrue(result["limit"]["allowed"])
            self.assertTrue(result["operationReached"])
            self.assertEqual(result["operationStatus"], 400)
            self.assertEqual(result["body"]["error"], {"code": "PiiInvalidInput"})
            self.assertFalse(result["requestConstructed"])

    def test_mapping_does_not_change_other_kinds_statuses_codes_or_auth_denials(self):
        for name in ("other-kind", "other-status", "other-code", "auth-denial"):
            self.assertFalse(self.results[name]["mapped"])
        self.assertEqual(self.results["other-kind"]["body"]["error"], {"code": "REQUEST_TOO_LARGE"})


if __name__ == "__main__":
    unittest.main()
