"""実XML式で設定を準備し、HTTPの操作選択と元のJSON文字列の保持を確認する。"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_api_settings import render_api_settings_fragment
from test_contract_rate_policy import PWSH


SOURCE = r'''
using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Operation { public string Id; }
public class Context {
    public Operation Operation = new Operation();
    public Dictionary<string, object> Variables = new Dictionary<string, object>();
    public object Request { get { throw new Exception("Settings must not read a request"); } }
}
public static class ApiSettingsFixture {
    public static string Decode(Context context) { __DECODE__ }
    public static string Audit(Context context) { __AUDIT__ }
    public static string Control(Context context) { __CONTROL__ }
    public static string Run(string input) {
        var output = new JObject();
        foreach (JObject item in JArray.Parse(input)) {
            var context = new Context();
            context.Operation.Id = (string)item["operation"];
            var source = (string)item["source"];
            context.Variables["gatewayApiSettingsSource"] = source;
            context.Variables["gatewayCommonAuthenticationValidated"] = "unchanged";
            context.Variables["gatewayApiSettings"] = Decode(context);
            context.Variables["gatewayAuditSettings"] = Audit(context);
            context.Variables["gatewayControlSettings"] = Control(context);
            output[(string)item["name"]] = new JObject(
                new JProperty("settings", context.Variables["gatewayApiSettings"]),
                new JProperty("audit", context.Variables["gatewayAuditSettings"]),
                new JProperty("control", context.Variables["gatewayControlSettings"]),
                new JProperty("source", context.Variables["gatewayApiSettingsSource"]),
                new JProperty("authentication", context.Variables["gatewayCommonAuthenticationValidated"]));
        }
        return output.ToString(Formatting.None);
    }
}
'''


class ApiSettingsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("Local Roslyn is required")
        # 実環境と無関係の合成設定。日付に見えるモデル名も、文字列のまま渡す。
        audit = {
            "backendService": "external", "backendAuthentication": "none",
            "operationBackends": {"lookup": {
                "backendService": "graph", "backendAuthentication": "credentialManager"}},
            "measurementRole": "execution", "llmOperationIds": [],
            "websocketModel": "2026-01-01T00:00:00Z",
        }
        control = {
            "operationId": "", "tokenLimitOperationIds": ["lookup", "create"],
            "operationConstraints": {"create": {
                "kind": "bodyModel", "allowedModels": ["2026-01-01T00:00:00Z", "合成モデル"],
                "maxRequestBytes": 1024, "allowTextOnly": True, "optional": None}},
        }
        cls.control_raw = json.dumps(control, ensure_ascii=False)
        cls.inputs = {}
        cases = []
        for api_type, operation, role in (
            ("http", "lookup", "execution"), ("http", "other", "execution"),
            ("websocket", "lookup", "execution"), ("mcp", "lookup", "execution"),
            ("mcp", "lookup", "facade"),
        ):
            name = api_type + "-" + operation + "-" + role
            current_audit = {**copy.deepcopy(audit), "measurementRole": role}
            audit_raw = json.dumps(current_audit, ensure_ascii=False)
            settings_raw = "\n".join((api_type, audit_raw, cls.control_raw))
            source_value = base64.b64encode(settings_raw.encode("utf-8")).decode("ascii")
            cls.inputs[name] = {"settings": settings_raw, "audit": audit_raw,
                                "source": source_value}
            cases.append({"name": name, "operation": operation, "source": source_value})
        root = ET.fromstring(render_api_settings_fragment())
        source = SOURCE
        for marker, variable in (("__DECODE__", "gatewayApiSettings"),
                                 ("__AUDIT__", "gatewayAuditSettings"),
                                 ("__CONTROL__", "gatewayControlSettings")):
            raw = root.find(f"./set-variable[@name='{variable}']").get("value").strip()
            if raw.startswith("@{") and raw.endswith("}"):
                body = raw[2:-1]
            elif raw.startswith("@(") and raw.endswith(")"):
                body = "return " + raw[2:-1] + ";"
            else:
                raise AssertionError("Expected an actual APIM expression")
            source = source.replace(marker, body)
        with tempfile.TemporaryDirectory(prefix="core-api-settings-") as directory:
            folder = Path(directory)
            (folder / "fixture.cs").write_text(source, encoding="utf-8")
            (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            (folder / "run.ps1").write_text(
                "$ErrorActionPreference='Stop'\n"
                "$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)"
                "+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\n"
                "Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) "
                "-ReferencedAssemblies $references -CompilerOptions '/nowarn:1701,1702'\n"
                "[ApiSettingsFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n",
                encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(folder / "run.ps1")],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1",
                     "DOTNET_CLI_TELEMETRY_OPTOUT": "1"})
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.results = json.loads(result.stdout)

    def test_control_json_and_source_are_passed_without_reformatting(self):
        for name, value in self.results.items():
            with self.subTest(case=name):
                self.assertEqual(value["control"], self.control_raw)
                self.assertEqual(value["settings"], self.inputs[name]["settings"])
                self.assertEqual(value["source"], self.inputs[name]["source"])
                self.assertEqual(value["authentication"], "unchanged")

    def test_http_selects_operation_backend_only_for_a_registered_override(self):
        selected = json.loads(self.results["http-lookup-execution"]["audit"])
        self.assertEqual(selected["backendService"], "graph")
        self.assertEqual(selected["backendAuthentication"], "credentialManager")
        fallback = json.loads(self.results["http-other-execution"]["audit"])
        self.assertEqual(fallback["backendService"], "external")
        self.assertEqual(fallback["backendAuthentication"], "none")
        self.assertEqual(selected["operationBackends"], fallback["operationBackends"])

    def test_websocket_and_mcp_preserve_api_wide_audit_and_measurement_role(self):
        for name in ("websocket-lookup-execution", "mcp-lookup-execution", "mcp-lookup-facade"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["audit"], self.inputs[name]["audit"])


if __name__ == "__main__":
    unittest.main()
