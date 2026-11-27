"""Entraユーザーの計数免除と安全な429の判定・配置を、実ポリシーから検証する。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.powershell import PWSH
from tests import REPOSITORY

CORE = REPOSITORY
from deployment.helpers.apim_audit import render_audit_policy

POLICIES = CORE / "apps/api-gateway/policies"


def expression(filename, xpath, attribute="value"):
    """実XMLの式を取り出す。"""
    raw = policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")
    node = ET.fromstring(render_audit_policy(raw)).find(xpath)
    if node is None:
        raise AssertionError(f"Missing actual policy expression: {filename} {xpath}")
    value = (node.get(attribute) if attribute else node.text).strip()
    if value.startswith("@{") and value.endswith("}"):
        return value[2:-1]
    if value.startswith("@(") and value.endswith(")"):
        return "return " + value[2:-1] + ";"
    raise AssertionError("Expected an actual APIM expression")


def fragment_body(filename):
    return render_audit_policy(policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")).replace("<fragment>", "").replace("</fragment>", "")


SOURCE = r"""
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body {
  public string Text;
  public T As<T>(bool preserveContent = false) { return (T)(object)JObject.Parse(Text); }
}
public class Request { public string Method; public Body Body = new Body(); }
public class LastError { public string PolicyId; public string Reason; }
public class Response { public int StatusCode; }
public class Context {
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Request Request = new Request();
  public LastError LastError = new LastError();
  public Response Response = new Response();
}
public static class RateFixture {
  public static bool McpCleanup(Context context) { __MCP_CLEANUP__ }
  public static bool HttpCleanup(Context context) { __HTTP_CLEANUP__ }
  public static bool RateError(Context context) { __RATE_ERROR__ }
  public static string RetryAfter(Context context) { __RETRY_AFTER__ }
  static object Value(JToken token) { return token is JValue ? ((JValue)token).Value : (object)token; }
  public static string Run(string input) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(input)) {
      var context = new Context();
      context.Request.Method = (string)item["method"] ?? "POST";
      context.Request.Body.Text = (string)item["body"] ?? "{}";
      var result = new JObject();
      switch ((string)item["group"]) {
          case "mcp": result["exempt"] = McpCleanup(context); break;
          case "http": {
            context.Variables["gatewayOperationConstraint"] = new JObject(new JProperty("kind", (string)item["kind"])).ToString();
            result["exempt"] = HttpCleanup(context); break;
          }
          case "error": {
            context.LastError.PolicyId = (string)item["policyId"];
            context.LastError.Reason = (string)item["reason"];
            context.Response.StatusCode = (int?)item["status"] ?? 429;
            result["matches"] = RateError(context); break;
          }
          case "retry": {
            if ((bool?)item["includeRetry"] == true) context.Variables["gatewayRateRetryAfter"] = Value(item["retry"]);
            if (item["fallback"] != null) context.Variables["gatewayRateDecision"] = new JObject(new JProperty("renewalPeriodSeconds", (int)item["fallback"])).ToString();
            result["retry"] = RetryAfter(context); break;
          }
          default: throw new Exception("Unknown fixture group");
        }

      results[(string)item["name"]] = result;
    }
    return results.ToString(Formatting.None);
  }
}
"""


class ContractRateExpressionTests(unittest.TestCase):
    """計数免除と429の実XML式を検証する。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        source = SOURCE
        for marker, filename, xpath, attribute in [
            ("__MCP_CLEANUP__", "mcp-rate-cleanup.xml", './set-variable', "value"),
            ("__HTTP_CLEANUP__", "rest.xml", './inbound/set-variable[@name="gatewayRateLimitExempt"]', "value"),
            ("__RATE_ERROR__", "rate-limit-error.xml", './choose/when', "condition"),
            ("__RETRY_AFTER__", "rate-limit-error.xml", './/set-variable[@name="gatewayRateSafeRetryAfter"]', "value"),
        ]:
            source = source.replace(marker, expression(filename, xpath, attribute))
        cls.cases = []

        def add(group, name, **values):
            cls.cases.append({"group": group, "name": name, **values})

        cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "request-1"}}
        for name, method, body in [
            ("delete", "DELETE", "{}"), ("get", "GET", json.dumps(cancel)), ("put", "PUT", json.dumps(cancel)),
            ("cancel-string", "POST", json.dumps(cancel)),
            ("cancel-integer", "POST", json.dumps({**cancel, "params": {"requestId": 0}})),
            ("cancel-reason", "POST", json.dumps({**cancel, "params": {"requestId": -1, "reason": "x"*1024}})),
            ("cancel-id-boundary", "POST", json.dumps({**cancel, "params": {"requestId": "x"*512}})),
            ("tool-call", "POST", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sample"}})),
            ("malformed", "POST", "{PRIVATE_MALFORMED"), ("array", "POST", "[]"), ("null", "POST", "null"),
            ("wrong-version", "POST", json.dumps({**cancel, "jsonrpc": "1.0"})),
            ("cancel-with-id", "POST", json.dumps({**cancel, "id": 1})),
            ("cancel-with-null-id", "POST", json.dumps({**cancel, "id": None})),
            ("extra-body", "POST", json.dumps({**cancel, "tool": "run"})),
            ("missing-params", "POST", json.dumps({k:v for k,v in cancel.items() if k != "params"})),
            ("array-params", "POST", json.dumps({**cancel, "params": []})),
            ("extra-params", "POST", json.dumps({**cancel, "params": {"requestId": 1, "tool": "run"}})),
        ]: add("mcp", "mcp-"+name, method=method, body=body)
        for name, request_id in [("missing", None), ("null", None), ("blank", " \t"), ("oversized", "x"*513), ("boolean", True), ("fraction", 1.5), ("object", {}), ("array", [])]:
            params = {} if name == "missing" else {"requestId": request_id}
            add("mcp", "mcp-id-"+name, body=json.dumps({**cancel, "params": params}))
        for name, reason in [("oversized", "x"*1025), ("null", None), ("boolean", False), ("object", {})]:
            add("mcp", "mcp-reason-"+name, body=json.dumps({**cancel, "params": {"requestId": 1, "reason": reason}}))
        for name, kind, body in [
            ("hangup", "realtimeHangup", "{}"), ("a2a-cancel", "a2aJsonRpc", '{"jsonrpc":"2.0","id":1,"method":"tasks/cancel"}'),
            ("a2a-send", "a2aJsonRpc", '{"method":"message/send"}'), ("a2a-stream", "a2aJsonRpc", '{"method":"message/stream"}'),
            ("a2a-get", "a2aJsonRpc", '{"method":"tasks/get"}'), ("create", "realtimeCreate", '{"method":"tasks/cancel"}'),
            ("registered", "registeredOperation", '{"method":"tasks/cancel"}'), ("pii", "pii", '{"method":"tasks/cancel"}'),
            ("unknown-kind", "realtimeHangupFake", '{}'),
        ]: add("http", "http-"+name, kind=kind, body=body)
        for name, policy_id, reason, status in [
            ("own-rate", "core-rate-limit", "RateLimitExceeded", 429),
            ("other-rate", "other-policy", "RateLimitExceeded", 429), ("no-id", None, "RateLimitExceeded", 429),
            ("own-other-error", "core-rate-limit", "ExpressionValueEvaluationFailure", 500),
            ("backend429", None, None, 429), ("auth", "validate-token", "TokenExpired", 401),
            ("case-id", "CORE-RATE-LIMIT", "RateLimitExceeded", 429),
            ("case-reason", "core-rate-limit", "ratelimitexceeded", 429),
        ]: add("error", "error-"+name, policyId=policy_id, reason=reason, status=status)
        for value in [1, 2, 299, 300, "1", "300"]:
            add("retry", "retry-valid-"+str(value)+"-"+type(value).__name__, includeRetry=True, retry=value, fallback=73)
        for name, value in [("zero", 0), ("negative", -1), ("over", 301), ("huge", 2**40), ("empty", ""),
                            ("decimal", "1.5"), ("fraction", 1.5), ("nan", "NaN"), ("url", "https://private.invalid"), ("newline", "1\r\nX-Private: value"),
                            ("boolean", True), ("null", None), ("object", {"secret":"PRIVATE_RETRY_VALUE"})]:
            add("retry", "retry-invalid-"+name, includeRetry=True, retry=value, fallback=73)
        add("retry", "retry-missing", fallback=73)
        add("retry", "retry-no-context")
        add("retry", "retry-fallback-boundary", includeRetry=True, retry=301, fallback=300)
        with tempfile.TemporaryDirectory(prefix="core-contract-rate-policy-") as directory:
            folder=Path(directory)
            (folder/"fixture.cs").write_text(source)
            (folder/"cases.json").write_text(json.dumps(cls.cases))
            (folder/"runner.ps1").write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'
[RateFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            result=subprocess.run([PWSH,"-NoLogo","-NoProfile","-NonInteractive","-File",str(folder/"runner.ps1")],
                capture_output=True,text=True,timeout=60,
                env={**os.environ,"POWERSHELL_TELEMETRY_OPTOUT":"1","DOTNET_CLI_TELEMETRY_OPTOUT":"1","XDG_CACHE_HOME":str(folder/"cache")})
            if result.returncode: raise AssertionError("Actual scoped rate XML expressions failed: "+result.stderr)
            for private in ("SYNTHETIC_PRIVATE", "PRIVATE_PROVIDER", "PRIVATE_RETRY", "private.invalid"):
                if private in result.stdout: raise AssertionError("Fixture leaked synthetic private data")
            cls.results=json.loads(result.stdout)

    def test_mcp_cleanup_accepts_only_delete_and_well_formed_cancel_notification(self):
        accepted={"mcp-delete","mcp-cancel-string","mcp-cancel-integer","mcp-cancel-reason","mcp-cancel-id-boundary"}
        for case in self.cases:
            if case["group"] == "mcp":
                with self.subTest(name=case["name"]): self.assertEqual(self.results[case["name"]],{"exempt":case["name"] in accepted})

    def test_http_cleanup_does_not_trust_a_cancel_name_on_another_api_kind(self):
        for case in self.cases:
            if case["group"] == "http":
                with self.subTest(name=case["name"]): self.assertEqual(self.results[case["name"]],{"exempt":case["name"] in ("http-hangup","http-a2a-cancel")})

    def test_error_requires_both_the_fixed_policy_id_and_rate_limit_reason(self):
        for case in self.cases:
            if case["group"] == "error":
                with self.subTest(name=case["name"]): self.assertEqual(self.results[case["name"]],{"matches":case["name"] == "error-own-rate"})

    def test_retry_after_is_bounded_and_invalid_values_use_the_registered_fallback(self):
        for case in self.cases:
            if case["group"] != "retry": continue
            expected=str(case["retry"]) if case["name"].startswith("retry-valid-") else str(case.get("fallback",60))
            with self.subTest(name=case["name"]):
                self.assertEqual(self.results[case["name"]],{"retry":expected})
                self.assertTrue(1 <= int(self.results[case["name"]]["retry"]) <= 300)


class ContractRateStructureTests(unittest.TestCase):
    """配置と安全な応答を確認する。実APIMのcounter精度や429実発生は検証しない。"""
    def test_rate_is_inbound_immediate_without_response_based_increment_or_backend_calls(self):
        root=ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, "rate-limit.xml").read_text(encoding="utf-8")))
        policies=root.findall('.//rate-limit-by-key')
        self.assertEqual(len(policies),1)
        self.assertEqual(policies[0].get('id'),'core-rate-limit')
        self.assertEqual(policies[0].get('retry-after-variable-name'),'gatewayRateRetryAfter')
        self.assertIsNone(policies[0].get('increment-condition'))
        self.assertIsNone(policies[0].get('increment-count'))
        for tag in ('send-request','authentication-managed-identity','get-authorization-context','set-body','cache-store-value'):
            self.assertIsNone(root.find('.//'+tag))
        self.assertNotIn('context.Request',ET.tostring(root,encoding='unicode'))
        self.assertNotIn('context.Response',ET.tostring(root,encoding='unicode'))

    def test_authorization_and_target_checks_precede_rate_then_cm_or_mi(self):
        for filename in ('rest.xml','stream.xml'):
            root=ET.parse(policy_fixture_path(POLICIES, filename)).getroot(); inbound=root.find('inbound'); children=list(inbound)
            rate=inbound.find('include-fragment[@fragment-id="core-rate-limit"]')
            # HTTPの認証情報引渡しは、同名フラグメントの呼出しを実行段階として扱います。
            decision=inbound.find('include-fragment[@id="core-api-authentication-result"][@fragment-id="core-api-authentication-result"]'
                                  if filename == 'rest.xml' else 'set-variable[@name="gatewayDecision"]')
            # 監査の原因分類ではなく、認証情報の引渡し結果を確認する分岐で配置を照合します。
            authorization=next(node for node in inbound.findall('choose')
                               if any('context.Variables["gatewayDecision"]' in branch.get('condition', '')
                                      for branch in node.findall('when')))
            with self.subTest(policy=filename):
                self.assertIsNotNone(decision)
                self.assertLess(children.index(decision),children.index(authorization))
                self.assertLess(children.index(authorization),children.index(rate))
                text=(policy_fixture_path(POLICIES, filename)).read_text(); pos=text.index('fragment-id="core-rate-limit"')
                # HTTPの正本はCMを直接呼び、非CMの下流認証はBicep生成後の試験で確認します。
                self.assertLess(pos,text.index('__CREDENTIAL_CONNECTION_INBOUND_XML__' if filename == 'stream.xml' else 'fragment-id="core-credential-connection"'))
                backend_marker = '__DEFAULT_BACKEND_XML__' if filename == 'rest.xml' else '__STREAM_BACKEND_XML__'
                self.assertLess(pos,text.index(backend_marker))
                if filename == 'rest.xml':
                    self.assertLess(children.index(inbound.find('choose[@id="core-api-target-validation-selection"]')),children.index(rate))
                    self.assertLess(children.index(inbound.find('set-variable[@name="gatewayRateLimitExempt"]')),children.index(rate))
                    self.assertLess(pos,text.index('fragment-id="core-credential-connection"'))
                else:
                    self.assertLess(text.index('__STREAM_HANDSHAKE_SESSION_XML__'),pos)
                    self.assertLess(text.index('__RATE_LIMIT_CLEANUP_XML__'),pos)
        # 生成MCPは参照先PII RESTで数え、表口で同じ契約を二重に数えない。
        native=ET.parse(POLICIES / 'api-templates' / 'mcp/generated.xml').getroot()
        self.assertIsNone(native.find('.//rate-limit-by-key'))
        self.assertIsNone(native.find('.//include-fragment[@fragment-id="core-rate-limit"]'))

    def test_error_is_body_free_for_websocket_and_never_reflects_raw_diagnostics(self):
        root=ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, 'rate-limit-error.xml').read_text(encoding="utf-8")).replace('__GATEWAY_TELEMETRY_XML__',fragment_body('core-gateway-telemetry.xml')))
        branch=root.find('choose/when'); response=branch.find('return-response')
        self.assertIsNone(response.find('set-body'))
        self.assertIsNone(response.get('response-variable-name'))
        self.assertEqual(response.find('set-header[@name="Cache-Control"]/value').text,'no-store')
        self.assertEqual(response.find('set-header[@name="X-Core-Error-Code"]/value').text,'RATE_LIMIT_EXCEEDED')
        self.assertEqual({node.get('name') for node in response.findall('set-header')},{'Retry-After','apim-request-id','X-Core-Error-Code','Cache-Control'})
        self.assertEqual(branch.find('set-variable[@name="gatewayAuditPhase"]').get('value'),'rejected')
        self.assertEqual(branch.find('set-variable[@name="gatewayAuditStatus"]').get('value'),'@(429)')
        self.assertEqual(len(branch.findall('trace[@source="core-gateway"]')),1)
        self.assertIsNone(root.find('.//include-fragment'))
        for raw in ('LastError.Message','context.Request.Body','context.Response.Body'):
            self.assertNotIn(raw,ET.tostring(root,encoding='unicode'))


if __name__ == '__main__': unittest.main()
