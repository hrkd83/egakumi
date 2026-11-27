"""実認証・引渡し・監査式で親要求IDの候補作成と停止順を検証する。

標準JWT検証後の合成本人snapshotとAPIM購読の合成結果を使用する。
JWT検証器、Azureへの適用、実際の親子関係は再現しない。
"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from tests import REPOSITORY

CORE = REPOSITORY
from deployment.helpers.apim_audit import render_audit_policy
from deployment.helpers.apim_common_authentication import extract_common_authentication_sections
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.powershell import PWSH

POLICIES = CORE / "apps/api-gateway/policies"
TENANT = "11111111-1111-4111-8111-111111111111"
PERSON = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
REQUEST = "66666666-6666-4666-8666-666666666666"
PARENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER = "user:" + TENANT + ":" + PERSON
IDENTITY = {"schemaVersion": 1, "requestId": REQUEST, "proof": "core-entra-token-validation",
            "callerId": USER, "allocationId": USER, "tenantId": TENANT,
            "principalId": PERSON, "clientId": CLIENT}


def actual_expression(filename, xpath, attribute="value"):
    raw = policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8")
    node = ET.fromstring(render_audit_policy(raw)).find(xpath)
    if node is None:
        raise AssertionError("Expected the actual authentication or trace expression")
    value = (node.get(attribute) if attribute else node.text).strip()
    if value.startswith("@{") and value.endswith("}"):
        return value[2:-1]
    if value.startswith("@(") and value.endswith(")"):
        return "return " + value[2:-1] + ";"
    raise AssertionError("Expected an actual APIM expression")


SOURCE = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Variables : Dictionary<string,object> {
  public bool Locked;
  public bool HandoffLocked;
  public int ForbiddenReads;
  public int HandoffKeyHeaderReads;
  public int HandoffCommonMarkerReads;
  void Check(string key) {
    if (HandoffLocked && key == "gatewayKeyHeader") { HandoffKeyHeaderReads++; throw new Exception("Handoff attempted to select authentication from a header setting"); }
    if (HandoffLocked && key == "gatewayCommonAuthenticationValidated") { HandoffCommonMarkerReads++; throw new Exception("Handoff attempted to repeat common authentication completion check"); }
    if (Locked && key != "gatewayDecision") { ForbiddenReads++; throw new Exception("Trace attempted an unrelated variable read"); }
  }
  public new object this[string key] { get { Check(key); return base[key]; } set { base[key] = value; } }
  public new bool ContainsKey(string key) { Check(key); return base.ContainsKey(key); }
  public new bool TryGetValue(string key, out object value) { Check(key); return base.TryGetValue(key, out value); }
}
public class Headers : Dictionary<string,string[]> {
  public bool HandoffLocked;
  public int HandoffReads;
  void Check() { if (HandoffLocked) { HandoffReads++; throw new Exception("Handoff attempted a request header read"); } }
  public new bool ContainsKey(string key) { Check(); return base.ContainsKey(key); }
  public new string[] this[string key] { get { Check(); return base[key]; } set { base[key] = value; } }
  public string GetValueOrDefault(string key, string fallback) { Check(); return base.ContainsKey(key) ? String.Join(",", base[key]) : fallback; }
}
public class Body { public T As<T>(bool preserveContent=false) { throw new Exception("Trace must not read body"); } }
public class Url { public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }
public class Request {
  public Headers Headers = new Headers();
  public Url Url = new Url();
  public int BodyReads;
  public Body Body { get { BodyReads++; throw new Exception("Trace attempted a body read"); } }
}
public class Subscription { public string Id; }
public class Context {
  public Variables Variables = new Variables();
  public Request Request = new Request();
  public Guid RequestId = Guid.Parse("66666666-6666-4666-8666-666666666666");
  public Subscription SubscriptionValue;
  public bool Locked;
  public int SubscriptionReads;
  public Subscription Subscription {
    get { if (Locked) { SubscriptionReads++; throw new Exception("Trace attempted a subscription read"); } return SubscriptionValue; }
  }
}
public static class AgentTraceFixture {
  public static string KeyCheck(Context context) { __KEY_CHECK__ }
  public static string UserResult(Context context) { __USER_RESULT__ }
  public static string UserError(Context context) { __USER_ERROR__ }
  public static bool CommonFailure(Context context) { __COMMON_FAILURE__ }
  public static bool PreparationFailure(Context context) { __PREPARATION_FAILURE__ }
  public static string HttpDecision(Context context) { __HTTP_DECISION__ }
  public static string ProtocolDecision(Context context) { __PROTOCOL_DECISION__ }
  public static string McpDecision(Context context) { __MCP_DECISION__ }
  __CONFIGURATION_VARIANTS__
  public static bool HttpFailure(Context context) { __HTTP_FAILURE__ }
  public static bool ProtocolFailure(Context context) { __PROTOCOL_FAILURE__ }
  public static bool McpFailure(Context context) { __MCP_FAILURE__ }
  public static string Trace(Context context) { __TRACE__ }
  public static string Run(string input) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(input)) {
      var context = new Context();
      var surface = (string)item["surface"] ?? "http";
      var result = new JObject(new JProperty("traceRan", false));
      context.Variables["gatewayKeyHeader"] = "Ocp-Apim-Subscription-Key";
      context.Variables["gatewayOperationSelection"] = "{\"settingId\":\"\"}";
      if ((string)item["method"] == "user" || (bool?)item["withBearer"] == true)
        context.Request.Headers["Authorization"] = new string[] { "Bearer SYNTHETIC_TOKEN" };
      if (item["parent"] is JArray)
        context.Request.Headers["X-Core-Agent-Parent-Id"] = ((JArray)item["parent"]).Select(value => (string)value).ToArray();
      try {
        if ((bool?)item["direct"] != true) {
          if ((string)item["method"] == "user") {
            context.Variables["gatewayEntraIdentityResult"] = item["identityRaw"] != null
              ? (string)item["identityRaw"] : item["identity"].ToString(Formatting.None);
            context.Variables["gatewayEntraTokenValidationCompleted"] = true;
            context.Variables["gatewayEntraTenantId"] = "11111111-1111-4111-8111-111111111111";
            context.Variables["gatewayEntraUserLimits"] = "{\"requestRate\":null,\"tokensPerMinute\":null,\"tokenQuota\":null}";
            context.Variables["gatewayEntraAuthenticationResult"] = UserResult(context);
            context.Variables["gatewayAuthenticationFailure"] = UserError(context);
            if (CommonFailure(context)) { result["blocked"] = "common"; results[(string)item["name"]] = result; continue; }
            context.Variables["gatewayUserAuthentication"] = context.Variables["gatewayEntraAuthenticationResult"];
            context.Variables["gatewayEntraAuthenticationValidated"] = true;
          } else {
            if ((bool?)item["missingSubscription"] != true)
              context.SubscriptionValue = new Subscription { Id = (string)item["subscription"] ?? "subscription-a" };
            context.Request.Headers["Ocp-Apim-Subscription-Key"] = item["keyValues"] is JArray
              ? ((JArray)item["keyValues"]).Select(value => (string)value).ToArray() : new string[] { "SYNTHETIC_KEY" };
            context.Variables["gatewayAuthenticationFailure"] = KeyCheck(context);
            if (CommonFailure(context)) { result["blocked"] = "common"; results[(string)item["name"]] = result; continue; }
          }
          context.Variables["gatewayCommonAuthenticationValidated"] = (bool?)item["commonCompleted"] ?? true;
          if (PreparationFailure(context)) { result["blocked"] = "preparation"; results[(string)item["name"]] = result; continue; }
          if ((bool?)item["entraCompleted"] == false) context.Variables["gatewayEntraAuthenticationValidated"] = false;
          if (item["userRaw"] != null) context.Variables["gatewayUserAuthentication"] = (string)item["userRaw"];
          // 入口でキーを実検査した後の引渡しだけ、ヘッダーを使う方式再選択を禁止します。
          if ((bool?)item["dropKeyAfterCommon"] == true) context.Request.Headers.Remove("Ocp-Apim-Subscription-Key");
          context.Variables.HandoffLocked = true;
          context.Request.Headers.HandoffLocked = true;
          string decision;
          try {
            decision = surface == "http" ? HttpDecision(context)
              : ConfigurationDecision(context, surface, (int?)item["configurationVariant"] ?? -1);
          } finally {
            context.Variables.HandoffLocked = false;
            context.Request.Headers.HandoffLocked = false;
          }
          context.Variables["gatewayDecision"] = decision;
          if (item["decision"] is JObject) context.Variables["gatewayDecision"] = item["decision"].ToString(Formatting.None);
          if (surface == "http" ? HttpFailure(context) : surface == "protocol" ? ProtocolFailure(context) : McpFailure(context)) {
            result["blocked"] = "handoff"; results[(string)item["name"]] = result; continue;
          }
        } else if ((bool?)item["missingDecision"] != true) {
          context.Variables["gatewayDecision"] = item["decisionRaw"] != null
            ? (object)(string)item["decisionRaw"] : item["decision"].ToString(Formatting.None);
        }
        object before;
        context.Variables.TryGetValue("gatewayDecision", out before);
        context.Variables.Locked = true;
        context.Locked = true;
        result["trace"] = JObject.Parse(Trace(context));
        result["traceRan"] = true;
        context.Variables.Locked = false;
        context.Locked = false;
        object after;
        context.Variables.TryGetValue("gatewayDecision", out after);
        result["decisionUnchanged"] = Object.Equals(before, after);
        result["forbiddenVariableReads"] = context.Variables.ForbiddenReads;
        result["subscriptionReads"] = context.SubscriptionReads;
        result["bodyReads"] = context.Request.BodyReads;
        result["handoffHeaderReads"] = context.Request.Headers.HandoffReads;
        result["handoffKeyHeaderReads"] = context.Variables.HandoffKeyHeaderReads;
        result["handoffCommonMarkerReads"] = context.Variables.HandoffCommonMarkerReads;
        context.Request.Headers.Remove("__DELETE_HEADER__");
        result["parentHeaderPresent"] = context.Request.Headers.ContainsKey("X-Core-Agent-Parent-Id");
      } catch { result["blocked"] = "execution"; }
      results[(string)item["name"]] = result;
    }
    return results.ToString(Formatting.None);
  }
}
'''


class AgentTraceExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        cls.cases = []
        cls.expected = {}
        cls.groups = {}

        def add(group, name, expected, **values):
            cls.cases.append({"name": name, "parent": [PARENT], "identity": copy.deepcopy(IDENTITY), **values})
            cls.groups.setdefault(group, []).append(name)
            cls.expected[name] = expected

        for surface in ("http", "protocol", "mcp"):
            for method, actor in (("key", "subscription:subscription-a"), ("user", USER)):
                add("confirmed", surface + "-" + method, {"status": "candidate", "claimedParentId": PARENT, "actorId": actor},
                    surface=surface, method=method)
            for name, changes in (("key-header-removed-after-common", {"dropKeyAfterCommon": True}),
                                   ("key-and-bearer-with-invalid-user-json", {"withBearer": True, "userRaw": "{"})):
                add("priority", surface + "-" + name,
                    {"status": "candidate", "claimedParentId": PARENT, "actorId": "subscription:subscription-a"},
                    surface=surface, method="key", **changes)
            headers = (("missing", None, "none"), ("empty", [], "invalid"),
                       ("duplicate", [PARENT, PARENT], "invalid"), ("blank", [""], "invalid"),
                       ("malformed", ["SYNTHETIC_INVALID_PARENT"], "invalid"),
                       ("zero", ["00000000-0000-0000-0000-000000000000"], "invalid"),
                       ("uppercase", [PARENT.upper()], "candidate"), ("braced", ["{" + PARENT + "}"], "candidate"))
            for name, parent, status in headers:
                expected = {"status": status}
                if status == "candidate": expected.update(claimedParentId=PARENT, actorId="subscription:subscription-a")
                add("headers", surface + "-parent-" + name, expected, surface=surface, method="key", parent=parent)
            for name, values in (("missing-subscription", {"missingSubscription": True}),
                                 ("empty-subscription", {"subscription": ""}),
                                 ("multiple-key", {"keyValues": ["SYNTHETIC_KEY", "SYNTHETIC_KEY"]}),
                                 ("blank-key", {"keyValues": [""]})):
                add("common", surface + "-" + name, "common", surface=surface, method="key", **values)
            for name, identity in (("wrong-caller", {**IDENTITY, "callerId": "user:other"}),
                                   ("wrong-allocation", {**IDENTITY, "allocationId": "user:other"}),
                                   ("wrong-request", {**IDENTITY, "requestId": PARENT})):
                add("common", surface + "-" + name, "common", surface=surface, method="user", identity=identity)
            add("common", surface + "-broken-identity", "common", surface=surface, method="user", identityRaw="{")
            for name, stage, changes in (("common-incomplete", "preparation", {"commonCompleted": False}),
                                         ("entra-incomplete", "handoff", {"entraCompleted": False}),
                                         ("broken-user-handoff", "handoff", {"userRaw": "{"}),
                                         ("denied-decision", "handoff", {"decision": {"allowed": False, "callerId": USER}})):
                add("handoff", surface + "-" + name, stage, surface=surface, method="user", **changes)

        cls.configuration_variants = (
            ("broken-base64", "%%%"),
            ("broken-json", base64.b64encode(b"{").decode()),
            ("unknown-mode", base64.b64encode(b'{"mode":"unknown"}').decode()),
            ("missing-mode", base64.b64encode(b"{}").decode()),
            ("null-json", base64.b64encode(b"null").decode()),
        )
        for surface in ("protocol", "mcp"):
            for index, (name, _) in enumerate(cls.configuration_variants):
                for method, actor in (("key", "subscription:subscription-a"), ("user", USER)):
                    add("configuration", surface + "-" + name + "-" + method,
                        {"status": "candidate", "claimedParentId": PARENT, "actorId": actor},
                        surface=surface, method=method, configurationVariant=index)

        for name, decision in (("missing-actor", {}), ("null-actor", {"callerId": None}),
                               ("empty-actor", {"callerId": ""}), ("blank-actor", {"callerId": " \t"})):
            add("fallback", name, {"status": "none"}, direct=True, decision=decision)
        add("fallback", "broken-decision", {"status": "invalid"}, direct=True, decisionRaw="{")
        add("fallback", "missing-decision", {"status": "invalid"}, direct=True, missingDecision=True)
        add("fallback", "wrong-actor-type", {"status": "invalid"}, direct=True, decision={"callerId": ["value"]})
        add("fallback", "prepared-actor-only", {"status": "candidate", "claimedParentId": PARENT, "actorId": USER},
            direct=True, decision={"callerId": USER})
        add("fallback", "missing-parent-broken-decision", {"status": "none"}, direct=True, parent=None, decisionRaw="{")
        add("fallback", "invalid-parent-missing-decision", {"status": "invalid"}, direct=True, parent=["invalid"], missingDecision=True)

        sections = extract_common_authentication_sections(policy_fixture_path(POLICIES, "core-common-authentication.cs").read_text(encoding="utf-8"))
        source = SOURCE.replace("__KEY_CHECK__", sections["key-request-validation"]).replace("__USER_RESULT__", sections["entra-authentication-result"])
        variant_methods = []
        selector = ['public static string ConfigurationDecision(Context context, string surface, int variant) {']
        selector.append('if (variant < 0) return surface == "protocol" ? ProtocolDecision(context) : McpDecision(context);')
        for surface, label, filename in (("protocol", "Protocol", "stream.xml"), ("mcp", "Mcp", "generated.xml")):
            decision = actual_expression(filename, './inbound/set-variable[@name="gatewayDecision"]')
            selector.append('if (surface == "' + surface + '") { switch (variant) {')
            for index, (_, encoded) in enumerate(cls.configuration_variants):
                method = label + 'Configuration' + str(index)
                # 方式設定の処理が誤って戻れば、同じ実式の不正Base64・JSON・mode対照が失敗します。
                variant_methods.append('public static string ' + method + '(Context context) {'
                                       + decision.replace("__AUTHORIZATION_BASE64__", encoded) + '}')
                selector.append('case ' + str(index) + ': return ' + method + '(context);')
            selector.append('} }')
        selector.append('throw new Exception("Unknown synthetic configuration variant"); }')
        source = source.replace("__CONFIGURATION_VARIANTS__", '\n'.join(variant_methods + selector))
        authorization = base64.b64encode(json.dumps({"mode": "subscriptionKey"}).encode()).decode()
        for marker, filename, xpath, attribute in (
            ("__USER_ERROR__", "core-common-authentication.xml", None, "value"),
            ("__COMMON_FAILURE__", "core-common-authentication.xml", './choose/otherwise/choose/when', "condition"),
            ("__PREPARATION_FAILURE__", "core-api-request-preparation.xml", './choose/when', "condition"),
            ("__HTTP_DECISION__", "core-api-authentication-result.xml", './set-variable', "value"),
            ("__PROTOCOL_DECISION__", "stream.xml", './inbound/set-variable[@name="gatewayDecision"]', "value"),
            ("__MCP_DECISION__", "generated.xml", './inbound/set-variable[@name="gatewayDecision"]', "value"),
            ("__HTTP_FAILURE__", "rest.xml", None, "condition"),
            ("__TRACE__", "audit-context.xml", './set-variable[@name="gatewayAgentTrace"]', "value"),
        ):
            if marker == "__USER_ERROR__":
                root = ET.parse(policy_fixture_path(POLICIES, filename)).getroot()
                node = next(item for item in root.findall('.//set-variable[@name="gatewayAuthenticationFailure"]')
                            if 'gatewayEntraAuthenticationResult' in item.get('value', ''))
                value = node.get("value").strip()
                replacement = "return " + value[2:-1] + ";"
            elif marker == "__HTTP_FAILURE__":
                # XPathは引用符を含む式で照合せず、正式なdecision直後の拒否枝を一意に選びます。
                root = ET.parse(policy_fixture_path(POLICIES, filename)).getroot()
                node = next(branch for branch in root.findall('./inbound/choose/when') if 'gatewayDecision' in branch.get('condition', ''))
                value = node.get("condition").strip()
                replacement = "return " + value[2:-1] + ";"
            else:
                replacement = actual_expression(filename, xpath, attribute)
            source = source.replace(marker, replacement.replace("__AUTHORIZATION_BASE64__", authorization))
        for marker, filename in (("__PROTOCOL_FAILURE__", "stream.xml"), ("__MCP_FAILURE__", "generated.xml")):
            root = ET.parse(policy_fixture_path(POLICIES, filename)).getroot()
            node = next(branch for branch in root.findall('./inbound/choose/when') if 'gatewayDecision' in branch.get('condition', ''))
            value = node.get("condition").strip()
            source = source.replace(marker, "return " + value[2:-1] + ";")
        delete = ET.parse(policy_fixture_path(POLICIES, "audit-context.xml")).find('./set-header')
        if delete.get("exists-action") != "delete": raise AssertionError("Expected actual parent-header deletion")
        source = source.replace("__DELETE_HEADER__", delete.get("name"))
        with tempfile.TemporaryDirectory(prefix="core-agent-trace-") as temporary:
            path = Path(temporary)
            (path / "policy.cs").write_text(source, encoding="utf-8")
            (path / "cases.json").write_text(json.dumps(cls.cases), encoding="utf-8")
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[AgentTraceFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if run.returncode: raise AssertionError("Actual agent trace flow execution failed:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_confirmed_key_and_user_caller_is_preserved_for_every_surface(self):
        for name in self.groups["confirmed"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["trace"], self.expected[name])

    def test_parent_header_forms_keep_existing_candidate_none_and_invalid_results(self):
        for name in self.groups["headers"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["trace"], self.expected[name])

    def test_actual_common_authentication_failures_stop_before_trace(self):
        for name in self.groups["common"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name], {"traceRan": False, "blocked": self.expected[name]})

    def test_actual_preparation_and_handoff_failure_guards_stop_before_trace(self):
        for name in self.groups["handoff"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name], {"traceRan": False, "blocked": self.expected[name]})

    def test_trace_internal_fallbacks_preserve_status_without_revalidating_actor(self):
        for name in self.groups["fallback"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["trace"], self.expected[name])

    def test_trace_never_reads_authentication_token_subscription_or_body(self):
        for name, result in self.results.items():
            if result["traceRan"]:
                with self.subTest(case=name):
                    self.assertEqual(result["forbiddenVariableReads"], 0)
                    self.assertEqual(result["subscriptionReads"], 0)
                    self.assertEqual(result["bodyReads"], 0)
                    self.assertTrue(result["decisionUnchanged"])
                    self.assertFalse(result["parentHeaderPresent"])

    def test_protocol_and_mcp_ignore_broken_or_unused_authorization_configuration_after_common_authentication(self):
        for name in self.groups["configuration"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["trace"], self.expected[name])

    def test_verified_subscription_selects_key_identity_without_handoff_header_reads(self):
        for name in self.groups["priority"]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["trace"], self.expected[name])
        for name, result in self.results.items():
            if result["traceRan"]:
                with self.subTest(case=name):
                    self.assertEqual(result["handoffHeaderReads"], 0)
                    self.assertEqual(result["handoffKeyHeaderReads"], 0)
                    self.assertEqual(result["handoffCommonMarkerReads"], 0)


class AgentTracePlacementTests(unittest.TestCase):
    def test_all_formal_templates_authenticate_and_stop_failures_before_trace(self):
        for filename in ("rest.xml", "stream.xml", "generated.xml"):
            with self.subTest(template=filename):
                inbound = ET.parse(policy_fixture_path(POLICIES, filename)).find("inbound")
                nodes = list(inbound)
                base = inbound.find("base")
                preparation = inbound.find("include-fragment[@fragment-id='core-api-request-preparation']")
                trace = inbound.find("include-fragment[@fragment-id='core-audit-context']")
                if filename == "rest.xml":
                    writer = inbound.find("include-fragment[@fragment-id='core-api-authentication-result']")
                else:
                    writer = inbound.find("set-variable[@name='gatewayDecision']")
                guard = next(node for node in inbound.findall("choose")
                             if any("gatewayDecision" in branch.get("condition", "") for branch in node.findall("when")))
                self.assertLess(nodes.index(base), nodes.index(preparation))
                self.assertLess(nodes.index(preparation), nodes.index(writer))
                self.assertLess(nodes.index(writer), nodes.index(guard))
                self.assertLess(nodes.index(guard), nodes.index(trace))
                branch = next(node for node in guard.findall("when") if "gatewayDecision" in node.get("condition", ""))
                response = branch.find("return-response")
                self.assertEqual(response.find("set-status").attrib,
                                 {"code": "500", "reason": "Gateway processing failed"})
                self.assertIsNone(response.find("set-body"))
                self.assertEqual(response.find("set-header[@name='apim-request-id']/value").text,
                                 "@(context.RequestId.ToString())")
                cache = response.find("set-header[@name='Cache-Control']")
                self.assertEqual(cache.get("exists-action"), "override")
                self.assertEqual(cache.find("value").text, "no-store")
                self.assertEqual(len(branch.findall("include-fragment[@fragment-id='core-gateway-telemetry']")), 1)
                for node in nodes[nodes.index(guard) + 1:nodes.index(trace)]:
                    for descendant in node.iter():
                        self.assertFalse(descendant.tag == "set-variable" and descendant.get("name") == "gatewayDecision")
                        self.assertNotIn('context.Variables["gatewayDecision"] =', descendant.text or "")
