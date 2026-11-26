"""購読ごとの回数制限の実XML式を、無料の合成APIMコンテキストで検証する。

PowerShell同梱RoslynでC#式をコンパイルする。APIMの分散counterを再現する試験ではなく、
Azureへ接続せず、購読の計数枠・停止免除・安全な429分類と配置だけを確認する。
"""
import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from policy_fixture_paths import policy_fixture_path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.apim_audit import render_audit_policy
from test_common_authentication import compile_inbound

POLICIES = CORE / "apps/gateway/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.is_file() else None


def expression(filename, xpath, attribute="value"):
    """実XMLの式を取り出す。設定placeholder以外の業務式は書き換えない。"""
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


def profile_pipeline(root, label, enabled):
    """実XMLの分岐・代入・503監査を実行し、標準cache/sendだけを合成アダプターへ置き換える。"""
    policy = ET.Element("policies")
    inbound = ET.SubElement(policy, "inbound")
    for child in root:
        inbound.append(copy.deepcopy(child))
    cache = root.find(".//cache-lookup-value")
    fetched = root.find(".//cache-store-value")
    telemetry = ET.fromstring(render_audit_policy(
        policy_fixture_path(POLICIES, "core-gateway-telemetry.xml").read_text(encoding="utf-8")))
    for container in policy.iter():
        for position, node in enumerate(list(container)):
            if node.tag == "cache-lookup-value":
                container[position] = ET.Element("set-variable", {
                    "name": node.get("variable-name"), "value": "@(LookupProfile(context))"})
            elif node.tag == "cache-store-value":
                container[position] = ET.Element("set-variable", {
                    "name": "fixtureCacheStored", "value": "@(StoreProfile(context))"})
            elif node.tag == "send-request":
                container[position] = ET.Element("set-variable", {
                    "name": node.get("response-variable-name"), "value": "@(SendProfile(context))"})
            elif node.tag == "include-fragment" and node.get("fragment-id") == "core-gateway-telemetry":
                container.remove(node)
                for offset, child in enumerate(telemetry):
                    container.insert(position + offset, copy.deepcopy(child))
    # 実際の取得後は本文へのアクセスを禁止し、監査が入力・SQL本文を読まないことを確認する。
    def after_variable(name):
        if name == "gatewayCachedProfile":
            return 'if (context.Variables["gatewayCachedProfile"] == null) context.Variables.Remove("gatewayCachedProfile");'
        if name == "gatewayProfileResponse":
            return 'if (context.OmitProfileResponse) context.Variables.Remove("gatewayProfileResponse");'
        if name == "gatewayFetchedProfile":
            return 'context.Request.Body.ReadLocked = true; if (context.ProfileResponse?.Body != null) context.ProfileResponse.Body.ReadLocked = true;'
        return ""
    xml = ET.tostring(policy, encoding="unicode").replace("{{core-subscription-profile-enabled}}", str(enabled).lower())
    methods, body = compile_inbound(ET.fromstring(xml), after_variable=after_variable, trace_messages=True)
    methods = re.sub(r"\bExpression(\d+)\b", label + r"Expression\1", methods)
    body = re.sub(r"\bExpression(\d+)\b", label + r"Expression\1", body)
    return methods + "\nstatic int " + label + "(Context context) { " + body + " return 200; }", {
        "lookupKey": cache.get("key"), "storedKey": fetched.get("key"),
        "storedValue": fetched.get("value"), "duration": int(fetched.get("duration")),
    }


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
public class Variables : Dictionary<string,object> {
  public bool RateLocked;
  public int UserAuthenticationReads;
  public int EntraMarkerReads;
  public int UserLimitsReads;
  void Check(string key) {
    if (RateLocked && key == "gatewayEntraAuthenticationValidated") {
      EntraMarkerReads++;
      throw new Exception("Rate attempted to repeat employee authentication completion check");
    }
    if (RateLocked && key == "gatewayUserAuthentication") {
      UserAuthenticationReads++;
      throw new Exception("Rate attempted to reopen verified employee JSON");
    }
    if (RateLocked && key == "gatewayEntraUserLimits") {
      UserLimitsReads++;
      throw new Exception("Rate attempted to repeat employee profile limits comparison");
    }
  }
  public new object this[string key] { get { Check(key); return base[key]; } set { base[key] = value; } }
  public new bool ContainsKey(string key) { Check(key); return base.ContainsKey(key); }
  public new bool TryGetValue(string key, out object value) { Check(key); return base.TryGetValue(key, out value); }
  public T GetValueOrDefault<T>(string key, T fallback) { object value; return TryGetValue(key, out value) ? (T)value : fallback; }
}
public class Body {
  public string Text = "{}";
  public bool Preserved;
  public bool ReadLocked;
  public int Reads;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    if (ReadLocked) throw new Exception("Audit attempted to read a private body");
    Preserved = preserveContent; return (T)(object)JObject.Parse(Text);
  }
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
public interface IResponse { int StatusCode { get; } Body Body { get; } }
public class Response : IResponse { public int StatusCode { get; set; } public Body Body { get; set; } = new Body(); }
public class Context {
  public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  public Variables Variables = new Variables();
  public Request Request = new Request();
  public Api Api = new Api();
  public Api Operation = new Api { Id = "responses-create" };
  public Api Subscription = new Api();
  public Deployment Deployment = new Deployment();
  public LastError LastError = new LastError();
  public Response Response = new Response();
  public Response ProfileResponse;
  public bool OmitProfileResponse;
  public string CachedProfile;
  public int CacheLookups, ProfileRequests, CacheStores;
  public string LookedUpKey, StoredKey, StoredProfile;
  public Dictionary<string,string> ResponseHeaders = new Dictionary<string,string>();
  public List<string> TraceMessages = new List<string>();
}
public class Deployment { public string ServiceName = "fixture-service"; }
public static class RateFixture {
  __PROFILE_PIPELINES__
  static string LookupProfile(Context context) {
    context.CacheLookups++; context.LookedUpKey = ProfileLookupKey(context); return context.CachedProfile;
  }
  static IResponse SendProfile(Context context) { context.ProfileRequests++; return context.ProfileResponse; }
  static bool StoreProfile(Context context) {
    context.CacheStores++; context.StoredKey = ProfileStoredKey(context); context.StoredProfile = ProfileStoredValue(context); return true;
  }
  public static string ProfileLookupKey(Context context) { __PROFILE_LOOKUP_KEY__ }
  public static string ProfileStoredKey(Context context) { __PROFILE_STORED_KEY__ }
  public static string ProfileStoredValue(Context context) { __PROFILE_STORED_VALUE__ }
  public static string DefaultRate(Context context) { __RATE_DEFAULT__ }
  public static string DisabledRate(Context context) { __RATE_DISABLED__ }
  public static string OtherRate(Context context) { __RATE_OTHER__ }
  public static string KeyEnabledRate(Context context) { __RATE_KEY_ENABLED__ }
  public static string ProfileRate(Context context) { __RATE_PROFILE__ }
  public static string InvalidSettingsRate(Context context) { __RATE_INVALID_SETTINGS__ }
  public static string InvalidProfileSettingsRate(Context context) { __RATE_INVALID_PROFILE_SETTINGS__ }
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
            context.Variables["gatewaySubscriptionProfile"] = item["profile"] is JObject ? item["profile"].ToString() : __PROFILE_INITIAL__;
            if ((bool?)item["dropProfile"] == true) context.Variables.Remove("gatewaySubscriptionProfile");
            if (item["userAuthentication"] is JObject) {
              context.Variables["gatewayUserAuthentication"] = item["userAuthentication"].ToString();
              context.Variables["gatewayEntraAuthenticationValidated"] = (bool?)item["employeeValidated"] ?? true;
            }
            if ((bool?)item["dropUserAuthentication"] == true) context.Variables.Remove("gatewayUserAuthentication");
            if (item["userLimits"] is JObject) context.Variables["gatewayEntraUserLimits"] = item["userLimits"].ToString();
            if (item["cleanupKind"] != null) {
              context.Variables["gatewayOperationConstraint"] = new JObject(new JProperty("kind", (string)item["cleanupKind"])).ToString();
              context.Variables["gatewayRateLimitExempt"] = HttpCleanup(context);
            }
            if ((bool?)item["mcpCleanup"] == true) context.Variables["gatewayRateLimitExempt"] = McpCleanup(context);
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
            string rate;
            // 計数式の評価中だけ社員JSON・照合元の枠・認証完了印の再読取りを禁止します。
            context.Variables.RateLocked = true;
            try {
              rate = setting == "invalid-profile" ? InvalidProfileSettingsRate(context) : setting == "invalid" ? InvalidSettingsRate(context) : setting == "profile" ? ProfileRate(context) : setting == "disabled" ? DisabledRate(context) : setting == "other" ? OtherRate(context) : setting == "key-enabled" ? KeyEnabledRate(context) : DefaultRate(context);
            } finally { context.Variables.RateLocked = false; }
            context.Variables["gatewayRateDecision"] = rate;
            result["scope"] = (string)JObject.Parse(rate)["scope"];
            result["enabled"] = RateEnabled(context);
            result["calls"] = Calls(context); result["period"] = Period(context); result["counter"] = Counter(context);
            result["userAuthenticationReads"] = context.Variables.UserAuthenticationReads;
            result["entraMarkerReads"] = context.Variables.EntraMarkerReads;
            result["userLimitsReads"] = context.Variables.UserLimitsReads;
            break;
          }
          case "profile-fetch": {
            // 実XMLのcache miss・取得・503監査を先に実行し、成功した場合だけ計数へ進みます。
            context.Variables["gatewayDecision"] = item["decision"].ToString(Formatting.None);
            context.Variables["gatewayRateLimitExempt"] = (bool?)item["exempt"] ?? false;
            context.Variables["gatewayAuthenticatedSubject"] = new JObject(
              new JProperty("status", "verified"), new JProperty("kind", "apimSubscription"),
              new JProperty("method", "subscriptionKey"), new JProperty("callerId", "subscription:" + context.Subscription.Id),
              new JProperty("subscriptionId", context.Subscription.Id)).ToString(Formatting.None);
            context.Request.Body.Text = "{\"input\":\"PRIVATE_REQUEST_BODY\"}";
            context.Request.Body.ReadLocked = true;
            context.OmitProfileResponse = (bool?)item["omitResponse"] ?? false;
            if ((bool?)item["nullResponse"] != true) context.ProfileResponse = new Response {
              StatusCode = (int?)item["providerStatus"] ?? 200,
              Body = (bool?)item["nullResponseBody"] == true ? null : new Body { Text = (string)item["body"] }
            };
            if (item["cachedProfile"] != null) context.CachedProfile = item["cachedProfile"].ToString(Formatting.None);
            if ((bool?)item["lockResponseBody"] == true && context.ProfileResponse?.Body != null) context.ProfileResponse.Body.ReadLocked = true;
            var enabled = (bool?)item["profileEnabled"] ?? true;
            var status = enabled ? ProfilePipelineEnabled(context) : ProfilePipelineDisabled(context);
            result["rateReached"] = false;
            result["status"] = status;
            result["auditCount"] = context.TraceMessages.Count;
            result["headers"] = JObject.FromObject(context.ResponseHeaders);
            if (context.TraceMessages.Count > 0) result["audit"] = JObject.Parse(context.TraceMessages.Last());
            result["cacheLookups"] = context.CacheLookups; result["profileRequests"] = context.ProfileRequests;
            result["cacheStores"] = context.CacheStores; result["cacheKey"] = context.LookedUpKey;
            result["requestBodyReads"] = context.Request.Body.Reads;
            result["responseBodyReads"] = context.ProfileResponse?.Body?.Reads ?? 0;
            if (context.CacheStores > 0) {
              result["storedKey"] = context.StoredKey;
              result["storedProfile"] = JObject.Parse(context.StoredProfile);
              result["cacheDuration"] = __PROFILE_CACHE_DURATION__;
            }
            if (status == 200) {
              result["profile"] = JObject.Parse((string)context.Variables["gatewaySubscriptionProfile"]);
              result["rateReached"] = true;
              result["rate"] = JObject.Parse(enabled ? ProfileRate(context) : DefaultRate(context));
            }
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
        source = source.replace("__RATE_INVALID_SETTINGS__", rate.replace("__RATE_LIMITS_BASE64__", "SYNTHETIC_INVALID_RATE_BASE64!"))
        source = source.replace("__RATE_INVALID_PROFILE_SETTINGS__", rate.replace("__RATE_LIMITS_BASE64__", "SYNTHETIC_INVALID_RATE_BASE64!").replace("{{core-subscription-profile-enabled}}", "true"))
        profile_root = ET.fromstring(render_audit_policy(
            policy_fixture_path(POLICIES, "subscription-profile.xml").read_text(encoding="utf-8")))
        profile_initial = profile_root.find('./set-variable[@name="gatewaySubscriptionProfile"]').get("value")
        source = source.replace("__PROFILE_INITIAL__", json.dumps(profile_initial))
        enabled_pipeline, cache = profile_pipeline(profile_root, "ProfilePipelineEnabled", True)
        disabled_pipeline, _ = profile_pipeline(profile_root, "ProfilePipelineDisabled", False)
        source = source.replace("__PROFILE_PIPELINES__", enabled_pipeline + "\n" + disabled_pipeline)
        source = source.replace("__PROFILE_CACHE_DURATION__", str(cache["duration"]))
        for marker, filename, xpath, attribute in [
            ("__RATE_ENABLED__", "rate-limit.xml", './choose/when', "condition"),
            ("__CALLS__", "rate-limit.xml", './/rate-limit-by-key', "calls"),
            ("__PERIOD__", "rate-limit.xml", './/rate-limit-by-key', "renewal-period"),
            ("__COUNTER__", "rate-limit.xml", './/rate-limit-by-key', "counter-key"),
            ("__MCP_CLEANUP__", "mcp-rate-cleanup.xml", './set-variable', "value"),
            ("__HTTP_CLEANUP__", "gateway.xml", './inbound/set-variable[@name="gatewayRateLimitExempt"]', "value"),
            ("__RATE_ERROR__", "rate-limit-error.xml", './choose/when', "condition"),
            ("__RETRY_AFTER__", "rate-limit-error.xml", './/set-variable[@name="gatewayRateSafeRetryAfter"]', "value"),
            ("__PROFILE_LOOKUP_KEY__", "subscription-profile.xml", './/cache-lookup-value', "key"),
            ("__PROFILE_STORED_KEY__", "subscription-profile.xml", './/cache-store-value', "key"),
            ("__PROFILE_STORED_VALUE__", "subscription-profile.xml", './/cache-store-value', "value"),
        ]:
            source = source.replace(marker, expression(filename, xpath, attribute))
        cls.cases = []
        def add(group, name, **values): cls.cases.append({"group": group, "name": name, **values})
        decision = {"allowed": True, "callerId": "subscription:subscription-a", "method": "subscriptionKey"}
        person = {"tid": TENANT, "oid": PERSON_A, "scp": "Gateway.Invoke", "idtyp": "user", "ver": "2.0"}
        application = {"tid": TENANT, "oid": PERSON_B, "idtyp": "app", "ver": "2.0", "roles": ["Gateway.Invoke.App"]}
        sql_limits = {"requestRate": {"calls": 6000, "renewalPeriodSeconds": 60}, "tokensPerMinute": None, "tokenQuota": None}
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
            ("profile-limited", {"setting": "profile", "profile": sql_limits}),
            ("profile-disabled-with-object", {"profile": sql_limits}),
            ("profile-limited-invalid-settings", {"setting": "invalid-profile", "profile": sql_limits}),
            ("profile-limited-exempt-invalid-settings", {"setting": "invalid-profile", "profile": sql_limits, "exempt": True}),
            ("profile-null-invalid-settings", {"setting": "invalid-profile"}),
            ("invalid-settings", {"setting": "invalid"}),
            ("profile-missing", {"dropProfile": True}),
            ("profile-rate-missing", {"profile": {"tokensPerMinute": None, "tokenQuota": None}}),
            ("disabled-with-token", {"setting": "disabled", "claims": person, "hasBearer": True}),
            ("exempt", {"exempt": True}),
            ("override", {"subscriptionId": "subscription-override", "decision": {**decision, "callerId": "subscription:subscription-override"}}),
            ("other-subscription", {"subscriptionId": "subscription-b", "decision": {**decision, "callerId": "subscription:subscription-b"}}),
            ("unauthorized", {"decision": {**decision, "allowed": False}}),
            ("disabled-unauthorized", {"setting": "disabled", "decision": {**decision, "allowed": False}}),
            ("allowed-missing", {"decision": {key: value for key, value in decision.items() if key != "allowed"}}),
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
        fetched_profile = {"schemaVersion": 1, "subscriptionId": "subscription-a",
                           "requestRate": None, "tokensPerMinute": None, "tokenQuota": None}
        add("profile-fetch", "profile-fetch-rate-missing", decision=decision,
            body=json.dumps({key: value for key, value in fetched_profile.items() if key != "requestRate"}))
        add("profile-fetch", "profile-fetch-unlimited", decision=decision, body=json.dumps(fetched_profile))
        cls.profile_failures = ["profile-fetch-rate-missing"]
        def failed_profile(name, **changes):
            case_name = "profile-fetch-" + name
            cls.profile_failures.append(case_name)
            add("profile-fetch", case_name, **({"decision": decision, "body": json.dumps(fetched_profile)} | changes))
        for name, changes in (
            ("response-missing", {"omitResponse": True}),
            ("response-null", {"nullResponse": True}),
            ("response-body-null", {"nullResponseBody": True}),
            ("response-204", {"providerStatus": 204}),
            ("response-400", {"providerStatus": 400}),
            ("response-503", {"providerStatus": 503}),
        ):
            failed_profile(name, **changes)
        for name, body in (("json-malformed", '{"PRIVATE_SQL_BODY":'), ("json-array", "[]"),
                           ("json-null", "null"), ("json-scalar", "123")):
            failed_profile(name, body=body)
        invalid_profiles = [
            ("schema-missing", {key: value for key, value in fetched_profile.items() if key != "schemaVersion"}),
            ("schema-string", {**fetched_profile, "schemaVersion": "1"}),
            ("schema-fraction", {**fetched_profile, "schemaVersion": 1.5}),
            ("schema-version", {**fetched_profile, "schemaVersion": 2}),
            ("subscription-missing", {key: value for key, value in fetched_profile.items() if key != "subscriptionId"}),
            ("subscription-mismatch", {**fetched_profile, "subscriptionId": "subscription-other"}),
            ("subscription-object", {**fetched_profile, "subscriptionId": {"private": "PRIVATE_SQL_BODY"}}),
            ("rate-array", {**fetched_profile, "requestRate": []}),
            ("rate-scalar", {**fetched_profile, "requestRate": 1}),
            ("tpm-missing", {key: value for key, value in fetched_profile.items() if key != "tokensPerMinute"}),
            ("quota-missing", {key: value for key, value in fetched_profile.items() if key != "tokenQuota"}),
            ("quota-array", {**fetched_profile, "tokenQuota": []}),
        ]
        normal_rate = {"calls": 60, "renewalPeriodSeconds": 60}
        for field in ("calls", "renewalPeriodSeconds"):
            invalid_profiles.append(("rate-" + field + "-missing", {**fetched_profile,
                "requestRate": {key: value for key, value in normal_rate.items() if key != field}}))
            for label, value in (("null", None), ("boolean", True), ("fraction", 1.5),
                                 ("zero", 0), ("over", 1000001 if field == "calls" else 301)):
                invalid_profiles.append(("rate-" + field + "-" + label,
                    {**fetched_profile, "requestRate": {**normal_rate, field: value}}))
        for label, value in (("string", "1"), ("boolean", True), ("fraction", 1.5), ("zero", 0), ("overflow", 2**40)):
            invalid_profiles.append(("tpm-" + label, {**fetched_profile, "tokensPerMinute": value}))
        normal_quota = {"tokens": 100, "period": "Monthly"}
        invalid_profiles.append(("quota-tokens-missing", {**fetched_profile, "tokenQuota": {"period": "Monthly"}}))
        for label, value in (("null", None), ("string", "1"), ("fraction", 1.5), ("zero", 0), ("overflow", 2**40)):
            invalid_profiles.append(("quota-tokens-" + label, {**fetched_profile, "tokenQuota": {**normal_quota, "tokens": value}}))
        invalid_profiles.append(("quota-period-missing", {**fetched_profile, "tokenQuota": {"tokens": 100}}))
        for label, value in (("null", None), ("unknown", "Forever")):
            invalid_profiles.append(("quota-period-" + label, {**fetched_profile, "tokenQuota": {**normal_quota, "period": value}}))
        for name, profile in invalid_profiles:
            failed_profile(name, body=json.dumps({**profile, "diagnostic": "PRIVATE_SQL_BODY"}))
        failed_profile("other-subscription-failure", subscriptionId="subscription-b",
                       decision={**decision, "callerId": "subscription:subscription-b"},
                       body=json.dumps({**fetched_profile, "requestRate": "PRIVATE_SQL_BODY"}))
        failed_profile("key-with-bearer-failure", hasBearer=True,
                       body=json.dumps({**fetched_profile, "requestRate": "PRIVATE_SQL_BODY"}))
        cls.profile_successes = {}
        finite = {**fetched_profile, **sql_limits, "tokensPerMinute": 1, "tokenQuota": {"tokens": 1, "period": "Hourly"}}
        for name, profile in (("limited", finite),
                              ("minimum", {**finite, "requestRate": {"calls": 1, "renewalPeriodSeconds": 1}}),
                              ("maximum", {**finite, "requestRate": {"calls": 1000000, "renewalPeriodSeconds": 300},
                                           "tokensPerMinute": 2147483647, "tokenQuota": {"tokens": 2147483647, "period": "Yearly"}})):
            case_name = "profile-fetch-" + name
            cls.profile_successes[case_name] = profile
            add("profile-fetch", case_name, decision=decision, body=json.dumps(profile))
        for period in ("Daily", "Weekly", "Monthly"):
            profile = {**finite, "tokenQuota": {"tokens": 1, "period": period}}
            case_name = "profile-fetch-quota-" + period.lower()
            cls.profile_successes[case_name] = profile
            add("profile-fetch", case_name, decision=decision, body=json.dumps(profile))
        other_profile = {**finite, "subscriptionId": "subscription-b"}
        cls.profile_successes["profile-fetch-other-subscription"] = other_profile
        add("profile-fetch", "profile-fetch-other-subscription", decision={**decision, "callerId": "subscription:subscription-b"},
            subscriptionId="subscription-b", body=json.dumps(other_profile))
        for name, cached in (("limited", finite), ("unlimited", fetched_profile)):
            add("profile-fetch", "profile-cache-" + name, decision=decision, cachedProfile=cached,
                body="PRIVATE_SQL_BODY", lockResponseBody=True)
        add("profile-fetch", "profile-cache-exempt", decision=decision, exempt=True,
            cachedProfile=finite, body="PRIVATE_SQL_BODY", lockResponseBody=True)
        add("profile-fetch", "profile-cache-disabled", decision=decision, profileEnabled=False,
            cachedProfile=finite, body="PRIVATE_SQL_BODY", lockResponseBody=True)
        user_caller = "user:" + TENANT + ":" + PERSON_A
        user_authentication = {"tenantId": TENANT, "principalId": PERSON_A, "callerId": user_caller}
        user_decision = {"allowed": True, "method": "user", "callerId": user_caller, "allocationId": user_caller,
                         "verifiedTenantId": TENANT, "verifiedPrincipalId": PERSON_A}
        user_limits = {"requestRate": {"calls": 11, "renewalPeriodSeconds": 50}, "tokensPerMinute": None, "tokenQuota": None}
        user_values = {"decision": user_decision, "userAuthentication": user_authentication, "profile": user_limits,
                       "userLimits": user_limits, "hasBearer": True, "nullSubscription": True}
        for scope, values in (("user", user_values), ("subscription", {"decision": decision})):
            for name, changes in (
                ("normal", {}),
                ("hangup", {"cleanupKind": "realtimeHangup"}),
                ("a2a-cancel", {"cleanupKind": "a2aJsonRpc", "body": '{"method":"tasks/cancel"}'}),
                ("a2a-send", {"cleanupKind": "a2aJsonRpc", "body": '{"method":"message/send"}'}),
                ("mcp-cancel", {"mcpCleanup": True, "body": '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}'}),
                ("mcp-delete", {"mcpCleanup": True, "method": "DELETE"}),
            ):
                add("rate", "cleanup-" + scope + "-" + name, **(values | changes))
        unlimited = {**user_limits, "requestRate": None}
        add("rate", "cleanup-user-unlimited", **(user_values | {"profile": unlimited, "userLimits": unlimited}))
        for name, changes in (
            ("unverified", {"employeeValidated": False}),
            ("mismatched-caller", {"decision": {**user_decision, "callerId": "user:synthetic-other"}}),
            ("mismatched-limits", {"userLimits": {**user_limits, "requestRate": None}}),
        ):
            add("rate", "cleanup-user-" + name, **(user_values | {"cleanupKind": "realtimeHangup"} | changes))
        add("rate", "cleanup-user-no-employee-json", **(user_values | {"dropUserAuthentication": True}))
        add("rate", "cleanup-user-invalid-key-settings", **(user_values | {"setting": "invalid"}))
        for label, changed in (
            ("allowed-false", {**user_decision, "allowed": False}),
            ("caller-blank", {**user_decision, "callerId": " \t"}),
            ("caller-missing", {key: value for key, value in user_decision.items() if key != "callerId"}),
        ):
            add("rate", "cleanup-user-" + label, **(user_values | {"decision": changed}))
        other_caller = "user:" + TENANT + ":" + PERSON_B
        # 単体fixtureでdecisionだけを別社員へ変更します。元JSONの再照合を計数部品へ戻さない対照です。
        add("rate", "cleanup-user-other-person", **(user_values | {"decision": {**user_decision,
            "callerId": other_caller, "allocationId": other_caller, "verifiedPrincipalId": PERSON_B}}))
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
                self.assertEqual((self.results["rate-" + name]["calls"], self.results["rate-" + name]["period"]), (60, 60))
        self.assertEqual((self.results["rate-other-settings"]["calls"], self.results["rate-other-settings"]["period"]), (3,300))
        self.assertEqual((self.results["rate-override"]["calls"], self.results["rate-override"]["period"]), (7,30))
        # rate単体は引渡しの成否を再確認しません。allowedを読めば欠落対照も失敗します。
        self.assertEqual(self.results["rate-unauthorized"], self.results["rate-base"])
        self.assertEqual(self.results["rate-allowed-missing"], self.results["rate-base"])
        self.assertEqual(self.results["rate-disabled-unauthorized"], self.results["rate-disabled"])

    def test_sql_profile_selects_limits_without_rekeying_counters(self):
        base = self.results["rate-base"]
        self.assertEqual(self.results["rate-profile-unlimited"], {**base, "enabled": False})
        self.assertEqual(self.results["rate-profile-disabled-with-object"], base)
        limited = self.results["rate-profile-limited"]
        self.assertTrue(limited["enabled"])
        self.assertEqual((limited["calls"], limited["period"]), (6000, 60))
        self.assertEqual(limited["counter"], self.results["rate-base"]["counter"])

    def test_sql_object_skips_embedded_settings_but_null_preserves_the_existing_fallback(self):
        limited = self.results["rate-profile-limited"]
        self.assertEqual(self.results["rate-profile-limited-invalid-settings"], limited)
        # 正式入口は免除時のSQL取得を省きます。これは準備済みobjectを持つ単体の免除対照です。
        self.assertEqual(self.results["rate-profile-limited-exempt-invalid-settings"], {**limited, "enabled": False})
        self.assertEqual(self.results["rate-profile-null-invalid-settings"], {"threw": True})

    def test_verified_user_and_subscription_share_cleanup_exemptions_without_changing_limits_or_counter(self):
        for scope in ("user", "subscription"):
            base = self.results["cleanup-" + scope + "-normal"]
            self.assertTrue(base["enabled"])
            self.assertEqual((base["calls"], base["period"]), (11, 50) if scope == "user" else (60, 60))
            for name in ("hangup", "a2a-cancel", "a2a-send", "mcp-cancel", "mcp-delete"):
                with self.subTest(scope=scope, name=name):
                    result = self.results["cleanup-" + scope + "-" + name]
                    self.assertEqual(result["enabled"], name == "a2a-send")
                    self.assertEqual({key: result[key] for key in ("scope", "calls", "period", "counter")},
                                     {key: base[key] for key in ("scope", "calls", "period", "counter")})
        self.assertFalse(self.results["cleanup-user-unlimited"]["enabled"])
        # 単体fixtureの照合元だけを変更しても、計数部品は準備済みprofileを再検査しません。
        self.assertEqual(self.results["cleanup-user-mismatched-limits"], self.results["cleanup-user-hangup"])
        # 単体fixtureで引渡し済みdecisionとfalseの印を組み合わせます。実入口は先行処理で停止します。
        self.assertEqual(self.results["cleanup-user-unverified"], self.results["cleanup-user-hangup"])

    def test_user_counter_uses_handed_off_identity_without_reopening_employee_json(self):
        base = self.results["cleanup-user-normal"]
        self.assertEqual(base["counter"], "ai-gateway-rate-v3:user:" + base64.b64encode((TENANT + ":" + PERSON_A).encode()).decode())
        self.assertEqual(self.results["cleanup-user-no-employee-json"], base)
        for label in ("allowed-false", "caller-blank", "caller-missing"):
            with self.subTest(isolated_decision=label):
                self.assertEqual(self.results["cleanup-user-" + label], base)
        other = self.results["cleanup-user-other-person"]
        self.assertEqual(other["counter"], "ai-gateway-rate-v3:user:" + base64.b64encode((TENANT + ":" + PERSON_B).encode()).decode())
        self.assertNotEqual(other["counter"], base["counter"])
        # 人工的なcaller変更だけで計数を拒否せず、共通処理から引き継いだtid+oidを使います。
        self.assertEqual(self.results["cleanup-user-mismatched-caller"], self.results["cleanup-user-hangup"])
        for case in self.cases:
            if case["group"] == "rate" and "threw" not in self.results[case["name"]]:
                with self.subTest(case=case["name"]):
                    self.assertEqual(self.results[case["name"]]["userAuthenticationReads"], 0)
                    self.assertEqual(self.results[case["name"]]["entraMarkerReads"], 0)
                    self.assertEqual(self.results[case["name"]]["userLimitsReads"], 0)

    def test_rate_uses_handed_off_subscription_without_repeating_identity_checks(self):
        base = self.results["rate-base"]
        # 認証済み引渡し後の単体fixtureだけを変更します。実共通認証による401とは別の対照です。
        for name in ("empty-caller", "blank-caller", "null-caller", "missing-caller",
                     "caller-mismatch", "entra-method", "unknown-method"):
            with self.subTest(name=name):
                self.assertEqual(self.results["rate-" + name], base)
        for name, subscription_id in (("empty-subscription", ""), ("blank-subscription", " ")):
            with self.subTest(isolated_subscription=name):
                self.assertEqual(self.results["rate-" + name], {**base,
                    "counter": "ai-gateway-rate-v3:subscriptionKey:" + base64.b64encode(subscription_id.encode()).decode()})
        # null購読は直接読取りの実行時例外になります。実共通認証は計数へ進む前に拒否します。
        self.assertEqual(self.results["rate-null-subscription"], {"threw": True})

    def test_employee_does_not_decode_subscription_settings_but_key_still_does(self):
        self.assertEqual(self.results["cleanup-user-invalid-key-settings"], self.results["cleanup-user-normal"])
        self.assertEqual(self.results["rate-invalid-settings"], {"threw": True})

    def test_rate_requires_the_prepared_profile_and_request_rate_field(self):
        # 正式入口の初期化を外した単体内部不備です。欠落を無制限や既定枠へ読み替えません。
        for name in ("profile-missing", "profile-rate-missing"):
            with self.subTest(isolated_profile=name):
                self.assertEqual(self.results["rate-" + name], {"threw": True})

    def test_sql_profile_contract_failure_stops_before_rate_and_explicit_null_is_unlimited(self):
        rejected = self.results["profile-fetch-rate-missing"]
        self.assertEqual((rejected["rateReached"], rejected["status"]), (False, 503))
        unlimited = self.results["profile-fetch-unlimited"]
        self.assertEqual(unlimited["status"], 200)
        self.assertTrue(unlimited["rateReached"])
        self.assertIsNone(unlimited["profile"]["requestRate"])
        self.assertFalse(unlimited["rate"]["enabled"])
        self.assertEqual(unlimited["rate"]["counterKey"], self.results["rate-base"]["counter"])
        self.assertEqual((unlimited["auditCount"], unlimited["cacheLookups"], unlimited["profileRequests"], unlimited["cacheStores"]), (0, 1, 1, 1))
        self.assertEqual(unlimited["headers"], {})
        self.assertEqual(unlimited["storedProfile"], unlimited["profile"])

    def test_sql_profile_acquisition_failures_emit_one_correlated_503_audit_and_stop_before_rate(self):
        cases = {case["name"]: case for case in self.cases}
        for name in self.profile_failures:
            with self.subTest(acquisition=name):
                row = self.results[name]
                subscription = cases[name].get("subscriptionId", "subscription-a")
                self.assertEqual(row["status"], 503)
                self.assertFalse(row["rateReached"])
                self.assertNotIn("rate", row)
                self.assertNotIn("profile", row)
                self.assertEqual((row["cacheLookups"], row["profileRequests"], row["cacheStores"]), (1, 1, 0))
                self.assertEqual(row["requestBodyReads"], 0)
                self.assertLessEqual(row["responseBodyReads"], 1)
                self.assertEqual(row["auditCount"], 1)
                audit = row["audit"]
                self.assertEqual({key: audit[key] for key in ("phase", "code", "status", "authorization")},
                                 {"phase": "policy-error", "code": "LIMIT_PROFILE_UNAVAILABLE", "status": 503, "authorization": "allowed"})
                subject = audit["authenticatedSubject"]
                self.assertEqual(subject, {"status": "verified", "kind": "apimSubscription", "method": "subscriptionKey",
                                           "callerId": "subscription:" + subscription, "subscriptionId": subscription})
                self.assertEqual((audit["callerId"], audit["subscriptionId"], audit["method"]),
                                 ("subscription:" + subscription, subscription, "subscriptionKey"))
                self.assertEqual(audit["apiId"], "api-a")
                self.assertEqual(audit["operationId"], "responses-create")
                self.assertEqual(row["headers"], {
                    "Content-Type": "application/json", "Cache-Control": "no-store", "Retry-After": "5",
                    "apim-request-id": audit["requestId"], "X-Core-Error": "LimitProfileUnavailable"})
                self.assertEqual(audit["requestId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
                self.assertNotIn("PRIVATE_", json.dumps(row))

    def test_sql_profile_success_preserves_valid_limits_and_populates_the_existing_short_cache(self):
        for name, profile in self.profile_successes.items():
            with self.subTest(profile=name):
                row = self.results[name]
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["rateReached"])
                self.assertEqual(row["profile"], profile)
                self.assertEqual(row["storedProfile"], profile)
                self.assertEqual((row["cacheLookups"], row["profileRequests"], row["cacheStores"]), (1, 1, 1))
                self.assertEqual((row["requestBodyReads"], row["responseBodyReads"]), (0, 1))
                self.assertEqual(row["cacheDuration"], 30)
                expected_key = "core-profile-v1:fixture-service:" + profile["subscriptionId"]
                self.assertEqual(row["cacheKey"], expected_key)
                self.assertEqual(row["storedKey"], expected_key)
                self.assertEqual(row["auditCount"], 0)
                self.assertEqual(row["headers"], {})
                self.assertTrue(row["rate"]["enabled"])
                self.assertEqual((row["rate"]["calls"], row["rate"]["renewalPeriodSeconds"]),
                                 (profile["requestRate"]["calls"], profile["requestRate"]["renewalPeriodSeconds"]))
                expected_counter = "ai-gateway-rate-v3:subscriptionKey:" + base64.b64encode(profile["subscriptionId"].encode()).decode()
                self.assertEqual(row["rate"]["counterKey"], expected_counter)

    def test_sql_cache_hits_and_profile_bypasses_do_not_fetch_or_emit_failure_audits(self):
        for suffix in ("limited", "unlimited"):
            with self.subTest(cache=suffix):
                row = self.results["profile-cache-" + suffix]
                source = self.results["profile-fetch-" + suffix]
                self.assertEqual((row["status"], row["cacheLookups"], row["profileRequests"], row["cacheStores"]), (200, 1, 0, 0))
                self.assertEqual(row["profile"], source["profile"])
                self.assertEqual(row["rate"], source["rate"])
                self.assertEqual((row["requestBodyReads"], row["responseBodyReads"], row["auditCount"]), (0, 0, 0))
                self.assertEqual(row["headers"], {})
        for suffix in ("exempt", "disabled"):
            with self.subTest(bypass=suffix):
                row = self.results["profile-cache-" + suffix]
                self.assertEqual((row["status"], row["cacheLookups"], row["profileRequests"], row["cacheStores"]), (200, 0, 0, 0))
                self.assertEqual(row["profile"], {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None})
                self.assertEqual((row["requestBodyReads"], row["responseBodyReads"], row["auditCount"]), (0, 0, 0))
                self.assertEqual(row["headers"], {})
                self.assertEqual(row["rate"]["enabled"], suffix == "disabled")
                self.assertEqual(row["rate"]["counterKey"], self.results["rate-base"]["counter"])
                if suffix == "disabled":
                    self.assertEqual((row["rate"]["calls"], row["rate"]["renewalPeriodSeconds"]), (60, 60))

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
    def test_sql_profile_snippet_keeps_cache_in_xml_and_owns_only_the_fetch_expression_in_csharp(self):
        path = policy_fixture_path(POLICIES, "subscription-profile.xml")
        self.assertEqual(path, POLICIES / "snippets/quota/subscription-profile/subscription-profile.xml")
        self.assertFalse((POLICIES / "subscription-profile.xml").exists())
        self.assertTrue(path.with_suffix(".cs").is_file())
        raw = path.read_text(encoding="utf-8")
        self.assertEqual(raw.count("__SUBSCRIPTION_PROFILE_CSHARP__"), 1)
        root = ET.fromstring(render_audit_policy(raw))
        fetch = root.find('.//set-variable[@name="gatewayFetchedProfile"]')
        self.assertIn('GetValueOrDefault<IResponse>("gatewayProfileResponse", null)', fetch.get("value"))
        self.assertIn('response.Body.As<JObject>()', fetch.get("value"))
        sql = root.find("choose/when[2]")
        self.assertIn("core-subscription-profile-enabled", sql.get("condition"))
        self.assertIn('!(bool)context.Variables["gatewayRateLimitExempt"]', sql.get("condition"))
        lookup = sql.find("cache-lookup-value")
        self.assertEqual((lookup.get("variable-name"), lookup.get("caching-type")), ("gatewayCachedProfile", "internal"))
        miss = sql.find("choose/when")
        self.assertEqual(miss.get("condition"), '@(!context.Variables.ContainsKey("gatewayCachedProfile"))')
        self.assertEqual([node.tag for node in miss], ["send-request", "set-variable", "choose", "set-variable", "cache-store-value"])
        request = miss.find("send-request")
        self.assertEqual({key: request.get(key) for key in ("mode", "response-variable-name", "timeout", "ignore-error")},
                         {"mode": "new", "response-variable-name": "gatewayProfileResponse", "timeout": "5", "ignore-error": "true"})
        self.assertEqual(request.find("set-method").text, "GET")
        self.assertEqual(request.find("set-url").text, "{{core-subscription-profile-url}}")
        self.assertEqual({node.get("name") for node in request.findall("set-header")},
                         {"X-Core-Profile-Key", "X-Core-Profile-Subscription"})
        self.assertEqual(request.find('set-header[@name="X-Core-Profile-Subscription"]/value').text, "@(context.Subscription.Id)")
        store = miss.find("cache-store-value")
        self.assertEqual((store.get("duration"), store.get("caching-type")), ("30", "internal"))
        self.assertEqual(store.get("key"), lookup.get("key"))
        self.assertEqual(store.get("value"), '@((string)context.Variables["gatewayFetchedProfile"])')
        self.assertEqual(sql.find('set-variable[@name="gatewaySubscriptionProfile"]').get("value"),
                         '@((string)context.Variables["gatewayCachedProfile"])')

    def test_sql_profile_failure_audit_precedes_the_existing_body_free_503_response(self):
        root = ET.fromstring(render_audit_policy(
            policy_fixture_path(POLICIES, "subscription-profile.xml").read_text(encoding="utf-8")))
        failure = root.find(".//choose/when[return-response]")
        self.assertEqual([node.tag for node in failure], ["set-variable", "set-variable", "set-variable", "include-fragment", "return-response"])
        self.assertEqual({node.get("name"): node.get("value") for node in failure.findall("set-variable")}, {
            "gatewayAuditPhase": "policy-error", "gatewayAuditCode": "LIMIT_PROFILE_UNAVAILABLE", "gatewayAuditStatus": "@(503)"})
        self.assertEqual(failure.find("include-fragment").get("fragment-id"), "core-gateway-telemetry")
        self.assertEqual(len(root.findall('.//include-fragment[@fragment-id="core-gateway-telemetry"]')), 1)
        response = failure.find("return-response")
        self.assertEqual(response.find("set-status").attrib, {"code": "503", "reason": "Service Unavailable"})
        self.assertIsNone(response.find("set-body"))
        self.assertIsNone(response.get("response-variable-name"))
        self.assertEqual({node.get("name") for node in response.findall("set-header")},
                         {"Content-Type", "Cache-Control", "Retry-After", "apim-request-id", "X-Core-Error"})
        telemetry = ET.fromstring("<fragment>" + fragment_body("core-gateway-telemetry.xml") + "</fragment>")
        self.assertEqual(len(telemetry.findall("trace")), 1)
        for forbidden in ("context.Request.Body", "context.Response.Body", "LastError.Message", "gatewayFetchedProfile", "gatewayProfileResponse"):
            self.assertNotIn(forbidden, ET.tostring(telemetry, encoding="unicode"))

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
        for filename in ('gateway.xml','protocol.xml'):
            root=ET.parse(policy_fixture_path(POLICIES, filename)).getroot(); inbound=root.find('inbound'); children=list(inbound)
            rate=inbound.find('include-fragment[@fragment-id="core-rate-limit"]')
            # HTTPの認証情報引渡しは、同名フラグメントの呼出しを実行段階として扱います。
            decision=inbound.find('include-fragment[@id="core-api-authentication-result"][@fragment-id="core-api-authentication-result"]'
                                  if filename == 'gateway.xml' else 'set-variable[@name="gatewayDecision"]')
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
                self.assertLess(pos,text.index('__CREDENTIAL_CONNECTION_INBOUND_XML__' if filename == 'protocol.xml' else 'fragment-id="core-credential-connection"'))
                backend_marker = '__DEFAULT_BACKEND_XML__' if filename == 'gateway.xml' else '<set-backend-service'
                self.assertLess(pos,text.index(backend_marker))
                if filename == 'gateway.xml':
                    self.assertLess(children.index(inbound.find('include-fragment[@fragment-id="core-api-target-validation"]')),children.index(rate))
                    self.assertLess(children.index(inbound.find('set-variable[@name="gatewayRateLimitExempt"]')),children.index(rate))
                    self.assertLess(pos,text.index('fragment-id="core-credential-connection"'))
                else:
                    self.assertLess(text.index('__PROTOCOL_SESSION_XML__'),pos)
                    self.assertLess(text.index('__RATE_LIMIT_CLEANUP_XML__'),pos)
        # 生成MCPは参照先PII RESTで数え、表口で同じ契約を二重に数えない。
        native=ET.parse(POLICIES / 'api-templates' / 'mcp.xml').getroot()
        self.assertIsNone(native.find('.//rate-limit-by-key'))
        self.assertIsNone(native.find('.//include-fragment[@fragment-id="core-rate-limit"]'))

    def test_safe_429_precedes_pii_and_other_generic_error_handling(self):
        telemetry=fragment_body('core-gateway-telemetry.xml')
        error=fragment_body('rate-limit-error.xml').replace('__GATEWAY_TELEMETRY_XML__',telemetry)
        for filename in ('gateway.xml','protocol.xml','mcp.xml','pii.xml'):
            text=(policy_fixture_path(POLICIES, filename)).read_text().replace('__RATE_LIMIT_ERROR_XML__',error)
            root=ET.fromstring(text); handler=root.find('on-error'); children=list(handler)
            first=0 if filename == 'pii.xml' else children.index(handler.find('base'))+1
            with self.subTest(policy=filename):
                if first: self.assertEqual(children[first-1].tag,'base')
                self.assertEqual(children[first].tag,'choose')
                branch=children[first].find('when')
                self.assertIn('core-rate-limit',branch.get('condition'))
                self.assertEqual(branch.find('return-response/set-status').get('code'),'429')
                self.assertEqual(branch.find('set-variable[@name="gatewayAuditCode"]').get('value'),'RATE_LIMIT_EXCEEDED')
                generic=handler.find('set-variable[@name="gatewayAuditCode"][@value="POLICY_ERROR"]')
                self.assertGreater(children.index(generic),first)

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
