"""実監査XMLのモデル名取得を合成C#コンテキストで検証する。

登録一覧を理由に新しいモデルの監査情報を落とさないことと、既存の文字数・
文字種の条件、alias後のモデル、本文を読まない共通監査を確認する。
Azureへの適用やログ到達を再現する試験ではない。
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from policy_fixture_paths import policy_fixture_path
from deployment.helpers.apim_audit import render_audit_policy


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")


def expression(filename, xpath, attribute="value"):
    node = ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8"))).find(xpath)
    if node is None:
        raise AssertionError("Expected the actual audit or alias policy expression")
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

public class Body {
  public string Text;
  public int Reads;
  public bool Preserved = true;
  public T As<T>(bool preserveContent = false) {
    Reads++;
    Preserved &= preserveContent;
    return typeof(T)==typeof(string) ? (T)(object)Text : (T)(object)JObject.Parse(Text);
  }
}
public class Parameters : Dictionary<string,string> {
  public string GetValueOrDefault(string key, string fallback) {
    return ContainsKey(key) ? this[key] : fallback;
  }
}
public class Request {
  public Body Body = new Body();
  public Parameters MatchedParameters = new Parameters();
}
public class Operation { public string Id = "invoke"; }
public class Context {
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Request Request = new Request();
  public Operation Operation = new Operation();
}
public static class AuditModelFixture {
  public static string BodyModel(Context context) { __BODY_MODEL__ }
  public static string AuditModel(Context context) { __AUDIT_MODEL__ }
  public static bool AliasRequired(Context context) { __ALIAS_REQUIRED__ }
  public static string AliasBody(Context context) { __ALIAS_BODY__ }
  public static string Run(string input) {
    var results = new JObject();
    foreach (JObject item in JArray.Parse(input)) {
      var context = new Context();
      var constraint = (JObject)item["constraint"];
      var selectedOperation = (string)item["selectedOperation"] ?? "invoke";
      context.Operation.Id = (string)item["operation"] ?? "invoke";
      var controls = new JObject(new JProperty("operationConstraints",
        new JObject(new JProperty(selectedOperation, constraint))));
      if (item["selectedOperation"] != null) controls["operationId"] = selectedOperation;
      context.Variables["gatewayControlSettings"] = item["rawControls"] != null
        ? (string)item["rawControls"] : controls.ToString(Formatting.None);
      // HTTP操作設定の先行選択が渡す準備済み制約。rawControlsの異常は共通監査側だけへ渡します。
      context.Variables["gatewayOperationConstraint"] = constraint.ToString(Formatting.None);
      context.Variables["gatewayAuditSettings"] = item["rawAudit"] != null
        ? (string)item["rawAudit"] : (item["audit"] as JObject ?? new JObject()).ToString(Formatting.None);
      context.Request.Body.Text = item["rawBody"] != null ? (string)item["rawBody"]
        : (item["body"] ?? new JObject()).ToString(Formatting.None);
      if (item["parameters"] is JObject) {
        foreach (var parameter in ((JObject)item["parameters"]).Properties())
          context.Request.MatchedParameters[parameter.Name] = (string)parameter.Value;
      }
      var aliasReads = 0;
      if (item["selection"] is JObject) {
        context.Variables["gatewayOperationSelection"] = item["selection"].ToString(Formatting.None);
        context.Variables["gatewayEffectiveModel"] = (string)item["selection"]["effectiveModel"];
        if (AliasRequired(context)) context.Request.Body.Text = AliasBody(context);
        aliasReads = context.Request.Body.Reads;
      }
      if ((bool?)item["bodyReader"] == true)
        context.Variables["gatewayAuditBodyModel"] = BodyModel(context);
      else if (item["preparedBodyModel"] != null)
        context.Variables["gatewayAuditBodyModel"] = (string)item["preparedBodyModel"];
      var beforeCommon = context.Request.Body.Reads;
      var model = AuditModel(context);
      results[(string)item["name"]] = new JObject(
        new JProperty("model", model),
        new JProperty("bodyModel", context.Variables.ContainsKey("gatewayAuditBodyModel")
          ? (string)context.Variables["gatewayAuditBodyModel"] : null),
        new JProperty("aliasReads", aliasReads),
        new JProperty("bodyReads", beforeCommon - aliasReads),
        new JProperty("commonReads", context.Request.Body.Reads - beforeCommon),
        new JProperty("preserved", context.Request.Body.Preserved),
        new JProperty("bodyAfter", context.Request.Body.Text));
    }
    return results.ToString(Formatting.None);
  }
}
'''


class AuditModelExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with bundled Roslyn is required")
        cls.cases = []
        cls.groups = {}
        cls.expected = {}

        def add(group, name, expected, **values):
            cls.cases.append({"name": name, **values})
            cls.groups.setdefault(group, []).append(name)
            cls.expected[name] = expected

        catalogs = (
            ("registered", ["registered-model"]),
            ("new", ["future-model"]),
            ("empty", []),
            ("missing", ...),
            ("null", None),
            ("object", {"unexpected": True}),
            ("scalar", 7),
        )
        for kind in ("bodyModel", "textEmbeddings", "rerank", "statelessResponses", "pathModel"):
            for catalog_name, catalog in catalogs:
                for model in ("registered-model", "future-model"):
                    constraint = {"kind": kind, "parameter": "deployment"}
                    if catalog is not ...:
                        constraint["allowedModels"] = catalog
                    add("catalog", f"{kind}-{catalog_name}-{model}", model,
                        constraint=constraint, bodyReader=kind != "pathModel",
                        body={"model": model, "input": "SYNTHETIC_INPUT"},
                        parameters={"deployment": model})

        invalid_models = ("", "model/other", "model with space", "model\nother", "model\tother",
                          "model?query", "model%2Fother", "合成model", "x" * 161)
        for index, model in enumerate(invalid_models):
            for kind in ("bodyModel", "pathModel"):
                add("invalid", f"invalid-{kind}-{index}", "",
                    constraint={"kind": kind, "parameter": "deployment"},
                    bodyReader=kind != "pathModel", body={"model": model},
                    parameters={"deployment": model})
        for index, model in enumerate(("x" * 160, "model-v6.2_sol", "MODEL-1")):
            add("valid-format", f"valid-{index}", model,
                constraint={"kind": "bodyModel"}, bodyReader=True, body={"model": model})

        for name, body in (("missing", {}), ("null", {"model": None}),
                           ("array", {"model": ["future-model"]}),
                           ("object", {"model": {"value": "future-model"}})):
            add("unreadable", "body-model-" + name, "",
                constraint={"kind": "bodyModel"}, bodyReader=True, body=body)
        for name, raw in (("broken", "{"), ("scalar", "7"), ("null", "null"), ("missing", "")):
            add("unreadable", "raw-body-" + name, "",
                constraint={"kind": "bodyModel"}, bodyReader=True, rawBody=raw)
        add("unreadable", "numeric-model-existing-conversion", "42",
            constraint={"kind": "bodyModel"}, bodyReader=True, body={"model": 42})

        for kind in ("bodyModel", "pathModel", "registeredOperation"):
            add("common", "websocket-" + kind, "future-model",
                constraint={"kind": kind, "parameter": "deployment"},
                audit={"websocketModel": "future-model"},
                parameters={"deployment": "future-model"}, rawBody="SYNTHETIC_OPAQUE_BODY")
        add("common", "selected-operation", "future-model",
            operation="unselected-operation", selectedOperation="configured-operation",
            constraint={"kind": "pathModel", "parameter": "deployment"},
            parameters={"deployment": "future-model"}, rawBody="SYNTHETIC_OPAQUE_BODY")
        add("common", "prepared-model-precedence", "prepared-model",
            constraint={"kind": "bodyModel"}, audit={"websocketModel": "websocket-model"},
            preparedBodyModel="prepared-model", rawBody="SYNTHETIC_OPAQUE_BODY")

        for kind in ("bodyModel", "pathModel", "registeredOperation", "liveCreate"):
            add("fixed", "fixed-" + kind, "fixed-model",
                constraint={"kind": kind, "parameter": "deployment", "model": "fixed-model"},
                bodyReader=kind != "registeredOperation", body={"model": "body-model"},
                parameters={"deployment": "path-model"}, audit={"websocketModel": "websocket-model"})
        add("fixed", "invalid-fixed-model", "",
            constraint={"kind": "bodyModel", "model": "fixed/model"},
            bodyReader=True, body={"model": "valid-model"})

        for name, overrides in (("controls-broken", {"rawControls": "{"}),
                                ("controls-missing", {"rawControls": "{}"}),
                                ("audit-broken", {"rawAudit": "{"})):
            add("settings", name, "", constraint={"kind": "bodyModel"},
                bodyReader=True, body={"model": "future-model"}, **overrides)

        for name, requested, effective in (("rewritten", "synthetic-alias", "future-model"),
                                            ("direct", "future-model", "future-model"),
                                            ("no-effective", "future-model", "")):
            add("alias", "alias-" + name, effective or requested,
                constraint={"kind": "statelessResponses", "allowedModels": ["registered-model"]},
                bodyReader=True, body={"model": requested, "input": "SYNTHETIC_INPUT", "stream": True},
                selection={"requestedModel": requested, "effectiveModel": effective})

        source = SOURCE
        for marker, filename, xpath, attribute in (
            ("__BODY_MODEL__", "audit-body-model.xml", './set-variable[@name="gatewayAuditBodyModel"]', "value"),
            ("__AUDIT_MODEL__", "audit-context.xml", './set-variable[@name="gatewayAuditModel"]', "value"),
            ("__ALIAS_REQUIRED__", "gateway.xml", './inbound/choose[@id="core-model-alias"]/when', "condition"),
            ("__ALIAS_BODY__", "gateway.xml", './inbound/choose[@id="core-model-alias"]/when/set-body', None),
        ):
            source = source.replace(marker, expression(filename, xpath, attribute))
        with tempfile.TemporaryDirectory(prefix="core-audit-model-") as temporary:
            path = Path(temporary)
            (path / "policy.cs").write_text(source, encoding="utf-8")
            (path / "cases.json").write_text(json.dumps(cls.cases), encoding="utf-8")
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[AuditModelFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if run.returncode:
                raise AssertionError("Actual audit model policy execution failed:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def assert_group(self, group):
        for name in self.groups[group]:
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["model"], self.expected[name])

    def test_registered_and_new_models_do_not_depend_on_model_catalog(self):
        self.assert_group("catalog")
        original = {case["name"]: case for case in self.cases}
        for name in self.groups["catalog"]:
            self.assertEqual(self.results[name]["bodyReads"], int(original[name]["bodyReader"]))

    def test_existing_length_and_character_conditions_are_retained(self):
        self.assert_group("invalid")
        self.assert_group("valid-format")

    def test_missing_or_unreadable_model_is_omitted_without_interrupting_request(self):
        self.assert_group("unreadable")

    def test_common_audit_uses_prepared_protocol_path_and_selected_operation(self):
        self.assert_group("common")

    def test_fixed_model_overrides_other_model_sources(self):
        self.assert_group("fixed")

    def test_internal_audit_read_failures_keep_empty_model(self):
        self.assert_group("settings")

    def test_alias_normalization_preserves_other_input_and_audits_actual_model(self):
        self.assert_group("alias")
        original = {case["name"]: case for case in self.cases}
        for name in self.groups["alias"]:
            result = self.results[name]
            expected = copy.deepcopy(original[name]["body"])
            expected["model"] = self.expected[name]
            self.assertEqual(json.loads(result["bodyAfter"]), expected)
            self.assertEqual(result["aliasReads"], int(name == "alias-rewritten"))

    def test_common_audit_never_reads_body_and_http_reader_preserves_it(self):
        original = {case["name"]: case for case in self.cases}
        for name, result in self.results.items():
            with self.subTest(case=name):
                self.assertEqual(result["commonReads"], 0)
                self.assertTrue(result["preserved"])
                case = original[name]
                if name not in self.groups["alias"]:
                    expected_body = case.get("rawBody", json.dumps(case.get("body", {}), separators=(",", ":"), ensure_ascii=False))
                    self.assertEqual(result["bodyAfter"], expected_body)
                if not case.get("bodyReader"):
                    self.assertEqual(result["bodyReads"], 0)
