"""購読ごとの回数制限の実XML式を、無料の合成APIMコンテキストで検証する。

PowerShell同梱RoslynでC#式をコンパイルする。APIMの分散counterを再現する試験ではなく、
Azureへ接続せず、購読の計数枠・停止免除・安全な429分類と配置だけを確認する。
"""
import base64
import copy
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
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.is_file() else None


def expression(filename, xpath, attribute="value"):
    """実XMLの式を取り出す。設定placeholder以外の業務式は書き換えない。"""
    node = ET.parse(POLICIES / filename).find(xpath)
    if node is None:
        raise AssertionError(f"Missing actual policy expression: {filename} {xpath}")
    value = (node.get(attribute) if attribute else node.text).strip()
    if value.startswith("@{") and value.endswith("}"):
        return value[2:-1]
    if value.startswith("@(") and value.endswith(")"):
        return "return " + value[2:-1] + ";"
    raise AssertionError("Expected an actual APIM expression")


def fragment_body(filename):
    return (POLICIES / filename).read_text().replace("<fragment>", "").replace("</fragment>", "")


TENANT = "11111111-aaaa-4111-8111-111111111111"
PERSON_A = "22222222-bbbb-4222-8222-222222222222"
PERSON_B = "33333333-cccc-4333-8333-333333333333"
DEFAULT = {
    "user": {"enabled": True, "calls": 60, "renewalPeriodSeconds": 60},
    "subscriptionKey": {"enabled": True, "calls": 60, "renewalPeriodSeconds": 60, "overrides": [
        {"callerId": "subscription:subscription-override", "calls": 7, "renewalPeriodSeconds": 30}]},
    "application": {"enabled": True, "calls": 60, "renewalPeriodSeconds": 60, "overrides": [
        {"callerId": "contract-override", "calls": 9, "renewalPeriodSeconds": 90}]},
}

SOURCE = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body {
  public string Text = "{}";
  public bool Preserved;
  public T As<T>(bool preserveContent = false) { Preserved = preserveContent; return (T)(object)JObject.Parse(Text); }
}
public class Request {
  public string Method = "POST";
  public Body Body = new Body();
  public Dictionary<string,string> Headers = new Dictionary<string,string>();
}
public class Api { public string Id; }
public class ClaimBag : Dictionary<string,string[]> {
  public string GetValueOrDefault(string key, string fallback) { return ContainsKey(key) ? String.Join(" ",this[key]) : fallback; }
}
public class Jwt { public ClaimBag Claims = new ClaimBag(); }
public class LastError { public string PolicyId; public string Reason; public string Source; public string Message = "PRIVATE_PROVIDER_DIAGNOSTIC"; }
public class Response { public int StatusCode; }
public class Context {
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Request Request = new Request();
  public Api Api = new Api();
  public Api Subscription = new Api();
  public LastError LastError = new LastError();
  public Response Response = new Response();
}
public static class RateFixture {
  public static string DefaultRate(Context context) { __RATE_DEFAULT__ }
  public static string DisabledRate(Context context) { __RATE_DISABLED__ }
  public static string OtherRate(Context context) { __RATE_OTHER__ }
  public static string KeyEnabledRate(Context context) { __RATE_KEY_ENABLED__ }
  public static string ProfileRate(Context context) { __RATE_PROFILE__ }
  public static bool RateEnabled(Context context) { __RATE_ENABLED__ }
  public static int Calls(Context context) { __CALLS__ }
  public static int Period(Context context) { __PERIOD__ }
  public static string Counter(Context context) { __COUNTER__ }
  public static bool McpCleanup(Context context) { __MCP_CLEANUP__ }
  public static bool HttpCleanup(Context context) { __HTTP_CLEANUP__ }
  public static bool RateError(Context context) { __RATE_ERROR__ }
  public static string RetryAfter(Context context) { __RETRY_AFTER__ }
  static object Value(JToken token) { return token is JValue ? ((JValue)token).Value : (object)token; }
  public static string Run(string input) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(input)) {
      var context = new Context();
      context.Api.Id = (string)item["api"] ?? "api-a";
      context.Request.Method = (string)item["method"] ?? "POST";
      context.Request.Body.Text = (string)item["body"] ?? "{}";
      context.Subscription.Id = (string)item["subscriptionId"] ?? "subscription-a";
      if ((bool?)item["nullSubscription"] == true) context.Subscription = null;
      var method = (string)item["decision"]?["method"] ?? "subscriptionKey";
      var hasBearer = (bool?)item["hasBearer"] ?? false;
      if (hasBearer) context.Request.Headers["Authorization"] = (string)item["bearer"] ?? "Bearer SYNTHETIC_PRIVATE_TOKEN";
      context.Request.Headers["X-Core-Rate-Limit-Exempt"] = "true";
      if (method == "subscriptionKey" || (bool?)item["withKey"] == true)
        context.Request.Headers["Ocp-Apim-Subscription-Key"] = (string)item["subscriptionKey"] ?? "SYNTHETIC_PRIVATE_KEY";
      context.Variables["gatewayKeyHeader"] = "Ocp-Apim-Subscription-Key";
      try {
        var result = new JObject();
        switch ((string)item["group"]) {
          case "rate": {
            context.Variables["gatewayDecision"] = ((JObject)item["decision"]).ToString(Formatting.None);
            context.Variables["gatewayRateLimitExempt"] = (bool?)item["exempt"] ?? false;
            context.Variables["gatewaySubscriptionProfile"] = (item["profile"] as JObject ?? JObject.Parse("{\"requestRate\":null,\"tokensPerMinute\":null,\"tokenQuota\":null}")).ToString();
            if (item["claims"] is JObject) {
              var jwt = new Jwt();
              foreach (var claim in ((JObject)item["claims"]).Properties()) {
                jwt.Claims[claim.Name] = claim.Value is JArray ? ((JArray)claim.Value).Select(value => (string)value).ToArray() : new string[] {(string)claim.Value};
              }
              context.Variables["gatewayValidatedToken"] = jwt;
            }
            if ((bool?)item["nullValidatedToken"] == true) context.Variables["gatewayValidatedToken"] = null;
            // 下流の接続方式も入力し、検証済み購読に基づく計数を確認します。
            context.Variables["fixtureBackendAuthentication"] = (string)item["backendAuthentication"] ?? "managedIdentity";
            var setting = (string)item["setting"];
            var rate = setting == "profile" ? ProfileRate(context) : setting == "disabled" ? DisabledRate(context) : setting == "other" ? OtherRate(context) : setting == "key-enabled" ? KeyEnabledRate(context) : DefaultRate(context);
            context.Variables["gatewayRateDecision"] = rate;
            result["scope"] = (string)JObject.Parse(rate)["scope"];
            result["enabled"] = RateEnabled(context);
            result["calls"] = Calls(context); result["period"] = Period(context); result["counter"] = Counter(context);
            break;
          }
          case "mcp": result["exempt"] = McpCleanup(context); break;
          case "http": {
            context.Variables["gatewayOperationConstraint"] = new JObject(new JProperty("kind", (string)item["kind"])).ToString();
            result["exempt"] = HttpCleanup(context); break;
          }
          case "error": {
            context.LastError.PolicyId = (string)item["policyId"];
            context.LastError.Reason = (string)item["reason"];
            context.LastError.Source = (string)item["source"];
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
      } catch { results[(string)item["name"]] = new JObject(new JProperty("threw", true)); }
    }
    return results.ToString(Formatting.None);
  }
}
'''


class ContractRateExpressionTests(unittest.TestCase):
    """APIM検証済み購読を使い、購読枠・計数停止・429判定を確認する。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        rate = expression("rate-limit.xml", './set-variable[@name="gatewayRateDecision"]')
        disabled = copy.deepcopy(DEFAULT); disabled["subscriptionKey"]["enabled"] = False
        other = copy.deepcopy(DEFAULT); other["subscriptionKey"].update(calls=3, renewalPeriodSeconds=300)
        key_enabled = copy.deepcopy(DEFAULT); key_enabled["subscriptionKey"]["enabled"] = True
        source = SOURCE
        for marker, settings in (("__RATE_DEFAULT__", DEFAULT), ("__RATE_DISABLED__", disabled), ("__RATE_OTHER__", other), ("__RATE_KEY_ENABLED__", key_enabled)):
            encoded = base64.b64encode(json.dumps(settings).encode()).decode()
            source = source.replace(marker, rate.replace("__RATE_LIMITS_BASE64__", encoded))
        source = source.replace("__RATE_PROFILE__", rate.replace("__RATE_LIMITS_BASE64__", base64.b64encode(json.dumps(DEFAULT).encode()).decode()).replace("{{core-subscription-profile-enabled}}", "true"))
        for marker, filename, xpath, attribute in [
            ("__RATE_ENABLED__", "rate-limit.xml", './choose/when', "condition"),
            ("__CALLS__", "rate-limit.xml", './/rate-limit-by-key', "calls"),
            ("__PERIOD__", "rate-limit.xml", './/rate-limit-by-key', "renewal-period"),
            ("__COUNTER__", "rate-limit.xml", './/rate-limit-by-key', "counter-key"),
            ("__MCP_CLEANUP__", "mcp-rate-cleanup.xml", './set-variable', "value"),
            ("__HTTP_CLEANUP__", "gateway.xml", './inbound/set-variable[@name="gatewayRateLimitExempt"]', "value"),
            ("__RATE_ERROR__", "rate-limit-error.xml", './choose/when', "condition"),
            ("__RETRY_AFTER__", "rate-limit-error.xml", './/set-variable[@name="gatewayRateSafeRetryAfter"]', "value"),
        ]:
            source = source.replace(marker, expression(filename, xpath, attribute))
        cls.cases = []
        def add(group, name, **values): cls.cases.append({"group": group, "name": name, **values})
        decision = {"allowed": True, "callerId": "subscription:subscription-a", "method": "subscriptionKey"}
        person = {"tid": TENANT, "oid": PERSON_A, "scp": "Gateway.Invoke", "idtyp": "user", "ver": "2.0"}
        application = {"tid": TENANT, "oid": PERSON_B, "idtyp": "app", "ver": "2.0", "roles": ["Gateway.Invoke.App"]}
        variants = [
            ("base", {}),
            ("other-api", {"api": "api-b"}),
            ("rotated-key", {"subscriptionKey": "SYNTHETIC_PRIVATE_NEW_KEY"}),
            ("person-token", {"claims": person, "hasBearer": True}),
            ("other-person-token", {"claims": {**person, "oid": PERSON_B}, "hasBearer": True}),
            ("application-token", {"claims": application, "hasBearer": True}),
            ("invalid-api-local-claims", {"claims": {"oid": "invalid"}}),
            ("null-api-local-token", {"nullValidatedToken": True}),
            ("credential-connection", {"backendAuthentication": "credentialConnection", "claims": person, "hasBearer": True}),
            ("other-settings", {"setting": "other"}),
            ("disabled", {"setting": "disabled"}),
            ("profile-unlimited", {"setting": "profile"}),
            ("profile-limited", {"setting": "profile", "profile": {"requestRate": {"calls": 6000, "renewalPeriodSeconds": 60}, "tokensPerMinute": None, "tokenQuota": None}}),
            ("disabled-with-token", {"setting": "disabled", "claims": person, "hasBearer": True}),
            ("exempt", {"exempt": True}),
            ("override", {"subscriptionId": "subscription-override", "decision": {**decision, "callerId": "subscription:subscription-override"}}),
            ("other-subscription", {"subscriptionId": "subscription-b", "decision": {**decision, "callerId": "subscription:subscription-b"}}),
            ("unauthorized", {"decision": {**decision, "allowed": False}}),
            ("disabled-unauthorized", {"setting": "disabled", "decision": {**decision, "allowed": False}}),
            ("empty-caller", {"decision": {**decision, "callerId": ""}}),
            ("blank-caller", {"decision": {**decision, "callerId": " "}}),
            ("null-caller", {"decision": {**decision, "callerId": None}}),
            ("missing-caller", {"decision": {k:v for k,v in decision.items() if k != "callerId"}}),
            ("caller-mismatch", {"decision": {**decision, "callerId": "subscription:other"}}),
            ("empty-subscription", {"subscriptionId": ""}),
            ("blank-subscription", {"subscriptionId": " "}),
            ("null-subscription", {"nullSubscription": True}),
            ("entra-method", {"decision": {**decision, "method": "entra"}, "claims": person}),
            ("unknown-method", {"decision": {**decision, "method": "unregistered"}}),
        ]
        for name, changes in variants:
            add("rate", "rate-" + name, **({"decision": decision} | changes))
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

    def test_subscription_shares_one_counter_across_api_key_rotation_and_optional_tokens(self):
        base = self.results["rate-base"]["counter"]
        self.assertEqual(base, "ai-gateway-rate-v3:subscriptionKey:" + base64.b64encode(b"subscription-a").decode())
        for name in ("other-api", "rotated-key", "person-token", "other-person-token", "application-token",
                     "invalid-api-local-claims", "null-api-local-token", "credential-connection", "other-settings"):
            with self.subTest(name=name):
                self.assertEqual(self.results["rate-" + name]["counter"], base)
                self.assertEqual(self.results["rate-" + name]["scope"], "subscriptionKey")
        self.assertNotEqual(self.results["rate-other-subscription"]["counter"], base)

    def test_subscription_defaults_override_disable_and_cleanup_exemption(self):
        self.assertEqual({k:self.results["rate-base"][k] for k in ("scope", "enabled", "calls", "period")},
                         {"scope":"subscriptionKey", "enabled":True, "calls":60, "period":60})
        for name in ("disabled", "disabled-with-token", "exempt"):
            with self.subTest(name=name):
                self.assertFalse(self.results["rate-" + name]["enabled"])
                self.assertEqual(self.results["rate-" + name]["scope"], "subscriptionKey")
        self.assertEqual((self.results["rate-other-settings"]["calls"], self.results["rate-other-settings"]["period"]), (3,300))
        self.assertEqual((self.results["rate-override"]["calls"], self.results["rate-override"]["period"]), (7,30))

    def test_sql_profile_selects_limits_without_rekeying_counters(self):
        self.assertFalse(self.results["rate-profile-unlimited"]["enabled"])
        limited = self.results["rate-profile-limited"]
        self.assertTrue(limited["enabled"])
        self.assertEqual((limited["calls"], limited["period"]), (6000, 60))
        self.assertEqual(limited["counter"], self.results["rate-base"]["counter"])

    def test_unverified_or_mismatched_subscription_never_produces_a_counter(self):
        for name in ("unauthorized", "disabled-unauthorized", "empty-caller", "blank-caller", "null-caller", "missing-caller",
                     "caller-mismatch", "empty-subscription", "blank-subscription", "null-subscription", "entra-method", "unknown-method"):
            with self.subTest(name=name):
                self.assertEqual(self.results["rate-" + name], {"threw":True})

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
        root=ET.parse(POLICIES/"rate-limit.xml").getroot()
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
        for filename in ('gateway.xml','protocol.xml'):
            root=ET.parse(POLICIES/filename).getroot(); inbound=root.find('inbound'); children=list(inbound)
            rate=inbound.find('include-fragment[@fragment-id="core-rate-limit"]')
            decision=inbound.find('set-variable[@name="gatewayDecision"]')
            authorization=[node for node in inbound.findall('choose') if node.find('when/set-variable[@name="gatewayAuditCode"][@value="AUTHORIZATION_DENIED"]') is not None][0]
            with self.subTest(policy=filename):
                self.assertLess(children.index(decision),children.index(authorization))
                self.assertLess(children.index(authorization),children.index(rate))
                text=(POLICIES/filename).read_text(); pos=text.index('fragment-id="core-rate-limit"')
                self.assertLess(pos,text.index('__CREDENTIAL_CONNECTION_INBOUND_XML__' if filename == 'protocol.xml' else '__BACKEND_AUTHENTICATION_XML__'))
                backend_marker = '__DEFAULT_BACKEND_XML__' if filename == 'gateway.xml' else '<set-backend-service'
                self.assertLess(pos,text.index(backend_marker))
                if filename == 'gateway.xml':
                    self.assertLess(children.index(inbound.find('set-variable[@name="gatewayTargetAllowed"]')),children.index(rate))
                    self.assertLess(children.index(inbound.find('set-variable[@name="gatewayRateLimitExempt"]')),children.index(rate))
                    self.assertLess(pos,text.index('fragment-id="core-credential-connection"'))
                else:
                    self.assertLess(text.index('__PROTOCOL_SESSION_XML__'),pos)
                    self.assertLess(text.index('__RATE_LIMIT_CLEANUP_XML__'),pos)
        # 生成MCPは参照先PII RESTで数え、表口で同じ契約を二重に数えない。
        native=ET.parse(POLICIES/'mcp.xml').getroot()
        self.assertIsNone(native.find('.//rate-limit-by-key'))
        self.assertIsNone(native.find('.//include-fragment[@fragment-id="core-rate-limit"]'))

    def test_safe_429_precedes_pii_and_other_generic_error_handling(self):
        telemetry=fragment_body('gateway-telemetry.xml')
        error=fragment_body('rate-limit-error.xml').replace('__GATEWAY_TELEMETRY_XML__',telemetry)
        for filename in ('gateway.xml','protocol.xml','mcp.xml','pii.xml'):
            text=(POLICIES/filename).read_text().replace('__RATE_LIMIT_ERROR_XML__',error)
            root=ET.fromstring(text); handler=root.find('on-error'); children=list(handler)
            first=0 if filename == 'pii.xml' else 1
            with self.subTest(policy=filename):
                if first: self.assertEqual(children[0].tag,'base')
                self.assertEqual(children[first].tag,'choose')
                branch=children[first].find('when')
                self.assertIn('core-rate-limit',branch.get('condition'))
                self.assertEqual(branch.find('return-response/set-status').get('code'),'429')
                self.assertEqual(branch.find('set-variable[@name="gatewayAuditCode"]').get('value'),'RATE_LIMIT_EXCEEDED')
                generic=handler.find('set-variable[@name="gatewayAuditCode"][@value="POLICY_ERROR"]')
                self.assertGreater(children.index(generic),first)

    def test_error_is_body_free_for_websocket_and_never_reflects_raw_diagnostics(self):
        root=ET.fromstring((POLICIES/'rate-limit-error.xml').read_text().replace('__GATEWAY_TELEMETRY_XML__',fragment_body('gateway-telemetry.xml')))
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
