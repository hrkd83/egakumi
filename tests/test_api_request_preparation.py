"""APIのbase直後の準備を部品化し、旧処理との同等性を実XMLとC#式で確認する。

ヘッダー操作と認証完了の確認をローカルで実行する。APIMの購読検証や
Entra認証を再実装せず、実Azureでの動作確認とも区別する。
"""
from policy_fixture_paths import policy_fixture_path
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_api_request import (
    API_REQUEST_PREPARATION_ID, expand_api_request_preparation_fragment,
    render_api_request_preparation_fragment, validate_api_request_preparation_scope,
)
from test_gateway_limits_telemetry import PWSH


POLICIES = Path(__file__).resolve().parents[1] / "apps/api-gateway/policies"
# 実環境から独立した合成Resource IDと要求IDだけを使う。
APIM = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture/providers/Microsoft.ApiManagement/service/fixture"
FRAGMENT = APIM + "/policyFragments/" + API_REQUEST_PREPARATION_ID
REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CALL = '<include-fragment id="core-api-request-preparation" fragment-id="core-api-request-preparation" />'

# 移設前のAPI入口。判定条件を簡潔化せず、この既存の処理順と式を比較基準にする。
PREVIOUS = '''<set-header name="X-Core-Caller-Id" exists-action="delete" />
<set-header name="X-Core-Subscription-Id" exists-action="delete" />
<set-header name="x-ms-client-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header>
<choose><when condition="@(!context.Variables.ContainsKey(&quot;gatewayCommonAuthenticationValidated&quot;) || !(bool)context.Variables[&quot;gatewayCommonAuthenticationValidated&quot;])"><return-response><set-status code="503" reason="Common authentication is not configured" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></when></choose>'''


def policy(preparation=CALL, *, before="", after=""):
    return ('<policies><inbound>' + before + '<base />' + preparation + after
            + '</inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')


def shape(node):
    """空白と説明コメント以外を比較し、元の式・属性・順番を保持したことを確認する。"""
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(),
            tuple(shape(child) for child in node if isinstance(child.tag, str)))


class ApiRequestPreparationStructureTests(unittest.TestCase):
    def test_fixed_leaf_preserves_the_entire_previous_entry_behavior(self):
        current = ET.fromstring(render_api_request_preparation_fragment())
        previous = ET.fromstring("<fragment>" + PREVIOUS + "</fragment>")
        self.assertEqual(shape(current), shape(previous))
        self.assertFalse(current.findall(".//include-fragment"))
        self.assertFalse(current.findall(".//base"))
        self.assertFalse(current.findall(".//set-variable"))

    def test_each_api_template_calls_once_immediately_after_inbound_base(self):
        for filename in ("rest.xml", "stream.xml", "generated.xml"):
            with self.subTest(policy=filename):
                value = (policy_fixture_path(POLICIES, filename)).read_text(encoding="utf-8")
                root = ET.fromstring(value)
                inbound = list(root.find("inbound"))
                calls = root.findall('.//include-fragment[@fragment-id="' + API_REQUEST_PREPARATION_ID + '"]')
                self.assertEqual(len(calls), 1)
                base = next(node for node in inbound if node.tag == "base")
                self.assertIs(inbound[inbound.index(base) + 1], calls[0])
                expanded = ET.fromstring(expand_api_request_preparation_fragment(value))
                nodes = list(expanded.find("inbound"))
                start = next(i for i, node in enumerate(nodes) if node.tag == "base") + 1
                self.assertEqual(tuple(shape(node) for node in nodes[start:start + 4]),
                                 tuple(shape(node) for node in ET.fromstring("<fragment>" + PREVIOUS + "</fragment>")))

    def test_expansion_preserves_settings_base_and_all_following_processing(self):
        before = '<set-variable name="gatewayControlSettings" value="synthetic-settings" />'
        after = '<set-header name="X-Core-Caller-Id" exists-action="override"><value>verified-later</value></set-header>'
        value = policy(before=before, after=after)
        actual = ET.fromstring(expand_api_request_preparation_fragment(value))
        self.assertEqual(shape(actual), shape(ET.fromstring(policy(PREVIOUS, before=before, after=after))))
        self.assertEqual(expand_api_request_preparation_fragment(policy("")), policy(""))
        self.assertEqual(expand_api_request_preparation_fragment(policy(PREVIOUS)), policy(PREVIOUS))

    def test_only_api_entry_and_its_system_handshake_may_call_the_leaf(self):
        for suffix, api_type in (
            ("/apis/model/policies/policy", "http"),
            ("/apis/native-mcp/policies/policy", "mcp"),
            ("/apis/socket/operations/onHandshake/policies/policy", "websocket"),
        ):
            with self.subTest(suffix=suffix):
                api_id = APIM + "/apis/" + suffix.split("/")[2]
                validate_api_request_preparation_scope({APIM + suffix: policy(),
                                                       FRAGMENT: render_api_request_preparation_fragment()}, APIM,
                                                      api_properties={api_id: {"type": api_type}})
        for suffix, value in (
            ("/policies/policy", policy()),
            ("/products/model-users/policies/policy", policy()),
            ("/apis/model/operations/responses/policies/policy", policy()),
            ("/policyFragments/unrelated", "<fragment>" + CALL + "</fragment>"),
        ):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                validate_api_request_preparation_scope({APIM + suffix: value,
                                                       FRAGMENT: render_api_request_preparation_fragment()}, APIM)

    def test_missing_or_modified_fragment_cannot_be_used_as_the_fixed_source(self):
        with self.assertRaises(ValueError):
            validate_api_request_preparation_scope({APIM + "/apis/model/policies/policy": policy()}, APIM)
        mutations = (
            lambda root: root.remove(root[0]),
            lambda root: root[0].set("exists-action", "override"),
            lambda root: setattr(root[2].find("value"), "text", '@(context.Request.Headers.GetValueOrDefault("request-id", ""))'),
            lambda root: root.find("choose/when").set("condition", "@(false)"),
            lambda root: root.find("choose/when/return-response/set-status").set("code", "200"),
            lambda root: ET.SubElement(root, "include-fragment", {"fragment-id": "unrelated"}),
        )
        for mutate in mutations:
            root = ET.fromstring(render_api_request_preparation_fragment())
            mutate(root)
            changed = ET.tostring(root, encoding="unicode")
            with self.subTest(mutation=mutate):
                with self.assertRaises(ValueError):
                    expand_api_request_preparation_fragment(policy(), changed)
                with self.assertRaises(ValueError):
                    validate_api_request_preparation_scope({APIM + "/apis/model/policies/policy": policy(),
                                                           FRAGMENT: changed}, APIM)

    def test_duplicate_nested_moved_or_modified_calls_are_rejected(self):
        for value in (
            policy(CALL + CALL),
            policy("", before=CALL),
            policy('<set-variable name="unexpected" value="before-call" />' + CALL),
            policy('<choose><when condition="@(true)">' + CALL + '</when></choose>'),
            policy(CALL.replace('id="core-api-request-preparation"', 'id="unreviewed"', 1)),
            policy(CALL.replace(' />', '>hidden</include-fragment>')),
            policy(CALL.replace(' />', '><base /></include-fragment>')),
            policy("").replace('<outbound><base />', '<outbound><base />' + CALL),
        ):
            with self.subTest(policy=value), self.assertRaises(ValueError):
                expand_api_request_preparation_fragment(value)


def expression(value):
    value = value.strip()
    if value.startswith("@(") and value.endswith(")"):
        return value[2:-1]
    if value.startswith("@{") and value.endswith("}"):
        raise AssertionError("The fixed preparation must not acquire unrelated C# processing")
    return json.dumps(value)


def compile_preparation(root):
    """ヘッダーの標準操作を再現し、選択条件と要求IDの値は実XMLの式をそのまま使う。"""
    def emit(node, *, response=False):
        if node.tag == "set-header":
            target = "context.ResponseHeaders" if response else "context.Request.Headers"
            name = json.dumps(node.get("name"))
            if node.get("exists-action") == "delete":
                return target + ".Remove(" + name + ");"
            if node.get("exists-action") != "override":
                raise AssertionError("Unsupported header operation")
            values = ", ".join(expression(child.text) for child in node.findall("value"))
            return target + "[" + name + "] = new string[] { " + values + " };"
        if node.tag == "choose":
            if len(node) != 1 or node[0].tag != "when":
                raise AssertionError("The authentication guard must retain its one existing branch")
            return "if (" + expression(node[0].get("condition")) + ") { " + "\n".join(emit(child) for child in node[0]) + " }"
        if node.tag == "return-response":
            status = node.find("set-status")
            return "\n".join(emit(child, response=True) for child in node if child.tag == "set-header") + (
                "\ncontext.Reason = " + json.dumps(status.get("reason")) + "; return " + status.get("code") + ";")
        raise AssertionError("Unreviewed request preparation operation: " + node.tag)
    return "\n".join(emit(node) for node in root)


CSHARP = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Request {
 public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
 public object Body { get { throw new Exception("Preparation read the request body"); } }
}
public class Context {
 public Guid RequestId; public Request Request = new Request(); public string Reason;
 public Dictionary<string,object> Variables = new Dictionary<string,object>();
 public Dictionary<string,string[]> ResponseHeaders = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
 public object Subscription { get { throw new Exception("Preparation rechecked subscription authentication"); } }
}
public static class RequestPreparationFixture {
 static int Current(Context context) { __CURRENT__ return 200; }
 static int Previous(Context context) { __PREVIOUS__ return 200; }
 static Context ContextFor(JObject item) {
  var c = new Context { RequestId = Guid.Parse((string)item["requestId"]) };
  foreach (var property in ((JObject)item["headers"]).Properties()) {
   c.Request.Headers[property.Name] = property.Value is JArray ? property.Value.Select(value => (string)value).ToArray() : new [] { (string)property.Value };
  }
  c.Variables["unrelated"] = "unchanged";
  if ((bool)item["setMarker"]) c.Variables["gatewayCommonAuthenticationValidated"] = ((JValue)item["marker"]).Value;
  return c;
 }
 static JObject Execute(JObject item, bool previous) {
  var c = ContextFor(item); int? status = null; string error = null;
  try { status = previous ? Previous(c) : Current(c); }
  catch (Exception ex) { error = ex.GetType().Name; }
  return new JObject(new JProperty("status", status), new JProperty("error", error),
   new JProperty("reason", c.Reason), new JProperty("headers", JObject.FromObject(c.Request.Headers)),
   new JProperty("responseHeaders", JObject.FromObject(c.ResponseHeaders)), new JProperty("variables", JObject.FromObject(c.Variables)));
 }
 public static string Run(string json) {
  var output = new JObject();
  foreach (JObject item in JArray.Parse(json)) output[(string)item["id"]] = new JObject(
   new JProperty("current", Execute(item, false)), new JProperty("previous", Execute(item, true)));
  return output.ToString(Formatting.None);
 }
}
'''


class ApiRequestPreparationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with Roslyn is required; Azure was not tested")
        cls.cases = []
        for label, set_marker, marker in (("true", True, True), ("false", True, False), ("missing", False, None),
                                         ("string", True, "true"), ("integer", True, 1), ("null", True, None)):
            for spoof in ("caller", "subscription", "both", "neither"):
                headers = {"Authorization": "Bearer <SYNTHETIC_ACCESS_TOKEN>", "api-key": "<SYNTHETIC_API_KEY>",
                           "x-ms-client-request-id": ["SYNTHETIC_FORGED_REQUEST", "duplicate"], "X-Unrelated": ["unchanged"]}
                if spoof in ("caller", "both"):
                    headers["x-CoRe-CaLlEr-Id"] = ["SYNTHETIC_FORGED_CALLER", "duplicate"]
                if spoof in ("subscription", "both"):
                    headers["X-CORE-SUBSCRIPTION-ID"] = "SYNTHETIC_FORGED_SUBSCRIPTION"
                cls.cases.append({"id": label + "-" + spoof, "setMarker": set_marker, "marker": marker,
                                  "requestId": REQUEST_ID, "headers": headers})
        cls.cases.append({"id": "different-request-id", "setMarker": True, "marker": True,
                          "requestId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "headers": {}})
        source = CSHARP.replace("__CURRENT__", compile_preparation(ET.fromstring(render_api_request_preparation_fragment())))
        source = source.replace("__PREVIOUS__", compile_preparation(ET.fromstring("<fragment>" + PREVIOUS + "</fragment>")))
        with tempfile.TemporaryDirectory(prefix="core-api-preparation-") as folder:
            base = Path(folder)
            (base / "policy.cs").write_text(source, encoding="utf-8")
            (base / "cases.json").write_text(json.dumps(cls.cases), encoding="utf-8")
            (base / "run.ps1").write_text('''$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[RequestPreparationFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
''', encoding="utf-8")
            result = subprocess.run([PWSH, "-NoProfile", "-File", str(base / "run.ps1")],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.results = json.loads(result.stdout)

    def test_current_and_previous_results_match_including_invalid_marker_types(self):
        for case in self.cases:
            result = self.results[case["id"]]
            with self.subTest(case=case["id"]):
                self.assertEqual(result["current"], result["previous"])

    def test_both_and_single_spoofed_identity_headers_are_removed_before_the_guard(self):
        for case in self.cases:
            result = self.results[case["id"]]["current"]
            with self.subTest(case=case["id"]):
                names = {name.lower() for name in result["headers"]}
                self.assertNotIn("x-core-caller-id", names)
                self.assertNotIn("x-core-subscription-id", names)
                self.assertEqual(result["headers"]["x-ms-client-request-id"], [case["requestId"]])

    def test_credentials_and_existing_variables_are_not_reauthenticated_or_changed(self):
        for case in self.cases:
            result = self.results[case["id"]]["current"]
            with self.subTest(case=case["id"]):
                for name in ("Authorization", "api-key", "X-Unrelated"):
                    if name in case["headers"]:
                        expected = case["headers"][name]
                        self.assertEqual(result["headers"][name], expected if isinstance(expected, list) else [expected])
                expected = {"unrelated": "unchanged"}
                if case["setMarker"]:
                    expected["gatewayCommonAuthenticationValidated"] = case["marker"]
                self.assertEqual(result["variables"], expected)

    def test_missing_or_false_markers_stop_with_the_existing_503_and_genuine_request_id(self):
        for label in ("missing", "false"):
            result = self.results[label + "-both"]["current"]
            with self.subTest(marker=label):
                self.assertEqual(result["status"], 503)
                self.assertEqual(result["reason"], "Common authentication is not configured")
                self.assertEqual(result["responseHeaders"], {"apim-request-id": [REQUEST_ID]})
                self.assertIsNone(result["error"])
        result = self.results["true-both"]["current"]
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["responseHeaders"], {})

    def test_invalid_marker_types_retain_expression_errors_instead_of_becoming_503(self):
        for label, error in (("string", "InvalidCastException"), ("integer", "InvalidCastException"),
                             ("null", "NullReferenceException")):
            result = self.results[label + "-both"]["current"]
            with self.subTest(marker=label):
                self.assertIsNone(result["status"])
                self.assertEqual(result["error"], error)
                self.assertEqual(result["responseHeaders"], {})


if __name__ == "__main__":
    unittest.main()
