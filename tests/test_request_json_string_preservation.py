"""Execute actual APIM body edits with synthetic JSON string and syntax cases."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import render_audit_policy
from deployment.helpers.apim_chat_store import render_chat_store_fragment


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "apps/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
REQUEST_ID = "11111111-1111-1111-1111-111111111111"
DEPLOYMENT = "synthetic-deployment"

SOURCE = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body {
  public string Raw;
  public int Reads;
  public int ConsumingReads;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    if (!preserveContent) ConsumingReads++;
    return typeof(T) == typeof(string) ? (T)(object)Raw : (T)(object)JObject.Parse(Raw);
  }
}
public class Request { public Body Body = new Body(); }
public class Context {
  public Request Request = new Request();
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Guid RequestId = Guid.Parse("11111111-1111-1111-1111-111111111111");
}
public static class JsonStringBodyFixture {
  public static string Alias(Context context) { __ALIAS_BODY__ }
  public static string Hosted(Context context) { __HOSTED_BODY__ }
  public static string Responses(Context context) { __RESPONSES_BODY__ }
  public static string Chat(Context context) { __CHAT_BODY__ }
  public static string Run(string input) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(input)) {
      var context = new Context();
      context.Request.Body.Raw = (string)item["raw"];
      context.Variables["gatewayEffectiveModel"] = "synthetic-deployment";
      context.Variables["gatewayOperationConstraint"] = "{\"kind\":\"hostedAgentPost\"}";
      context.Variables["gatewayAuditSettings"] = "{\"targetAgent\":\"synthetic-agent\"}";
      var result = new JObject();
      try {
        var kind = (string)item["kind"];
        var body = kind == "alias" ? Alias(context) : kind == "hosted" ? Hosted(context)
          : kind == "responses" ? Responses(context) : Chat(context);
        if ((bool?)item["thenResponses"] == true || (bool?)item["thenChat"] == true) {
          context.Request.Body.Raw = body;
          body = (bool?)item["thenResponses"] == true ? Responses(context) : Chat(context);
        }
        result["body"] = body;
        result["error"] = false;
      } catch { result["error"] = true; }
      result["reads"] = context.Request.Body.Reads;
      result["consumingReads"] = context.Request.Body.ConsumingReads;
      results[(string)item["name"]] = result;
    }
    return results.ToString(Formatting.None);
  }
}
'''


class RequestJsonStringPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Newtonsoft.Json is required")
        rendered = ET.fromstring(render_audit_policy(
            (POLICIES / "api-templates/gateway.xml").read_text(encoding="utf-8")))
        chat = ET.fromstring(render_chat_store_fragment())
        expressions = {
            "__ALIAS_BODY__": rendered.find("./inbound/choose[@id='core-model-alias']/when/set-body").text,
            "__HOSTED_BODY__": rendered.find("./inbound/choose[@id='core-hosted-response-metadata']/when/set-body").text,
            "__RESPONSES_BODY__": next(node.text for node in rendered.findall("./inbound/choose/when/set-body")
                                      if 'body["store"] = false' in node.text and 'gatewayAuditSettings' not in node.text),
            "__CHAT_BODY__": chat.find("./set-variable").get("value"),
        }
        source = SOURCE
        for marker, expression in expressions.items():
            expression = expression.strip()
            if not expression.startswith("@{") or not expression.endswith("}"):
                raise AssertionError("Actual body expression is not a complete APIM expression")
            source = source.replace(marker, expression[2:-1])

        strings = [
            "", "~", "~~already-prefixed", "~2000-01-02T03:04:05.000Z",
            "2000-01-02", "2000-01-02T03:04:05Z", "2000-01-02T03:04:05.000Z",
            "2000-01-02T03:04:05.0000000Z", "2000-01-02T03:04:05.1234567Z",
            "2000-01-02T03:04:05.000+09:00", "2000-01-02T03:04:05.000-07:30",
            "prefix 2000-01-02T03:04:05.000Z", "/Date(946782245000)/",
            "123", "true", "null", 'quote " slash \\ newline \n colon :',
            "日本語 😀 \u0000", '/* "comment-looking" */ // inside string',
        ]
        payload = {
            "model": "synthetic-alias",
            "store": True,
            "background": True,
            "conversation": "synthetic-conversation",
            "metadata": {str(index): value for index, value in enumerate(strings)},
            "2000-01-02T03:04:05.000Z": {"~": "2000-01-02T03:04:05.000Z"},
            "input": [{"type": "message", "id": "~item", "content": [{"text": value} for value in strings]}],
            "other": [None, False, True, 0, -3, 1.25, 9007199254740993, {"0": ["", "~", None]}],
        }
        nested = "2000-01-02T03:04:05.000Z"
        for index in range(12):
            nested = {"level": [index, nested]}
        payload["nested"] = nested
        cls.cases, cls.expected = [], {}

        def add(name, kind, raw, expected=None, *, then_responses=False, then_chat=False):
            cls.cases.append({"name": name, "kind": kind, "raw": raw,
                              "thenResponses": then_responses, "thenChat": then_chat})
            if expected is not None:
                expected = copy.deepcopy(expected)
                if kind == "alias":
                    expected["model"] = DEPLOYMENT
                if kind in ("chat", "responses", "hosted") or then_responses or then_chat:
                    expected["store"] = False
                if kind in ("responses", "hosted") or then_responses:
                    if "conversation" in expected:
                        expected["conversation"] = None
                    if "background" in expected:
                        expected["background"] = False
                if kind == "hosted":
                    expected.setdefault("metadata", {})["core_parent_request_id"] = REQUEST_ID
                cls.expected[name] = expected

        for kind in ("alias", "hosted", "responses", "chat"):
            add(kind + "-strings", kind, json.dumps(payload, ensure_ascii=False), payload)
            add(kind + "-escaped", kind, json.dumps(payload, ensure_ascii=True), payload)
            add(kind + "-empty-object", kind, "{}", {})
            # JSON.NET accepts comments before names/values and keeps the last duplicate key.
            comment_raw = r'''{
                /* "ignored quote" */ "model": /* "before value" */ "synthetic-alias",
                // "comment"
                "metadata": {"value": "2000-01-02T03:04:05.000Z", "value": "~2000-01-02T03:04:05.000Z"},
                "input": ["quote: \" x", "/* not comment */"],
                "numeric": 1.2500
            }'''
            comment_expected = {"model": "synthetic-alias",
                                "metadata": {"value": "~2000-01-02T03:04:05.000Z"},
                                "input": ['quote: " x', "/* not comment */"], "numeric": 1.25}
            add(kind + "-comments-duplicates", kind, comment_raw, comment_expected)
            add(kind + "-single-quotes", kind,
                r"{'model':'synthetic-alias','metadata':{'value':'2000-01-02T03:04:05.000Z'},'input':['it\'s','~']}",
                {"model": "synthetic-alias", "metadata": {"value": "2000-01-02T03:04:05.000Z"},
                 "input": ["it's", "~"]})
            large = {"input": "x" * 262144 + "2000-01-02T03:04:05.000Z"}
            add(kind + "-large-string", kind, json.dumps(large), large)
            for name, raw in (("broken", "{"), ("array", "[]"), ("null", "null"),
                              ("scalar", '"2000-01-02T03:04:05.000Z"'),
                              ("invalid-escape", r'{"input":"\uZZZZ"}'),
                              ("trailing", '{"input":"x"} {}'),
                              ("comment-before-colon", '{"input" /* comment */:"x"}'),
                              ("repeated-unclosed-comments", "{" + '/* " ' * 4000),
                              ("repeated-unclosed-quoted-escapes", '{"input":"' + r'\"\\' * 4000)):
                add(kind + "-invalid-" + name, kind, raw)
        add("alias-then-responses", "alias", json.dumps(payload), payload, then_responses=True)
        add("alias-then-chat", "alias", json.dumps(payload), payload, then_chat=True)
        add("escaped-date-first-character", "responses",
            r'{"metadata":{"value":"\u0032000-01-02T03:04:05.000Z"}}',
            {"metadata": {"value": "2000-01-02T03:04:05.000Z"}})

        with tempfile.TemporaryDirectory(prefix="core-json-strings-") as directory:
            path = Path(directory)
            (path / "policy.cs").write_text(source, encoding="utf-8")
            (path / "cases.json").write_text(json.dumps(cls.cases), encoding="utf-8")
            (path / "run.ps1").write_text(
                "$ErrorActionPreference='Stop'\n"
                "$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)"
                "+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\n"
                "Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs'))"
                " -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'\n"
                "[JsonStringBodyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n",
                encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if result.returncode:
                raise AssertionError("Actual APIM body expression did not execute:\n" + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_all_string_values_and_remaining_json_values_are_preserved(self):
        for name, expected in self.expected.items():
            with self.subTest(case=name):
                result = self.results[name]
                self.assertFalse(result["error"])
                self.assertEqual(json.loads(result["body"]), expected)

    def test_unreadable_and_nonobject_json_keeps_the_existing_rejection_boundary(self):
        for case in self.cases:
            if case["name"] in self.expected:
                continue
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                if case["kind"] == "chat":
                    self.assertFalse(result["error"])
                    self.assertEqual(result["body"], "")
                else:
                    self.assertTrue(result["error"])

    def test_body_read_count_and_consumption_are_unchanged(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                result = self.results[case["name"]]
                chained = case["thenResponses"] or case["thenChat"]
                self.assertEqual(result["reads"], 2 if chained else 1)
                self.assertEqual(result["consumingReads"],
                                 int(case["kind"] in ("chat", "responses")) + int(chained))
