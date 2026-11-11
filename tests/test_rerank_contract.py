"""Rerankの意味制約・モデル認可を実設定と実APIM式で検査する。外部通信はしない。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep
from configuration_fixtures import CORE, api_fixture, configuration
from schema_helpers import local_schema_validator
from test_api_protocols import CSHARP, actual_expression
from test_gateway_limits_telemetry import PWSH


def rerank_fixture():
    """配備済みの合成モデルを参照する独立したRerank API。"""
    raw = api_fixture()
    api = raw["apiDefinitions"][0]
    # Reuse only the public provider audience, never an adopter's configuration.
    provider = json.loads((CORE / "apps/gateway/apim/apis/cohere-inference-v1.json").read_text())
    api.update(backendService="foundryServices", backendAudience=provider["backendAudience"])
    api["operations"] = [{"name": "rerank", "displayName": "Synthetic rerank", "method": "POST",
                          "urlTemplate": "/rerank", "templateParameters": [],
                          "backendUrlTemplate": "/providers/synthetic/v1/rerank"}]
    api["authorization"]["operationConstraints"] = {"rerank": {
        "kind": "rerank", "bodyFormat": "json", "maxRequestBytes": 65536,
        "allowedModels": ["gpt-approved"], "arrayLimits": {"documents": 8},
        "integerLimits": {"top_n": {"minimum": 1, "maximum": 3, "required": True}},
    }}
    return raw


class RerankConfigurationTests(unittest.TestCase):
    def normalize(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_shipped_rerank_and_embedding_keep_distinct_semantic_contracts(self):
        source = json.loads((CORE / "apps/gateway/apim/apis/cohere-inference-v1.json").read_text())
        constraints = source["authorization"]["operationConstraints"]
        self.assertEqual(constraints["rerank"]["kind"], "rerank")
        self.assertEqual(constraints["cohere-embeddings"]["kind"], "textEmbeddings")
        result = self.normalize(rerank_fixture())["apiDefinitions"][0]
        self.assertEqual(result["authorization"]["operationConstraints"]["rerank"]["kind"], "rerank")
        self.assertEqual(result["operations"][0]["backendUrlTemplate"], "/providers/synthetic/v1/rerank")

    def test_both_editor_schemas_accept_only_the_registered_kind(self):
        for name in ("configuration", "params"):
            schema = json.loads((CORE / f"schemas/{name}.schema.json").read_text())
            validator = local_schema_validator({"$ref": f"{name}.schema.json#/$defs/operationConstraint"})
            constraint = rerank_fixture()["apiDefinitions"][0]["authorization"]["operationConstraints"]["rerank"]
            with self.subTest(schema=name):
                self.assertEqual(list(validator.iter_errors(constraint)), [])
                self.assertTrue(list(validator.iter_errors({**constraint, "kind": "unregisteredRerank"})))

    def test_provider_defaults_do_not_require_gateway_count_limits(self):
        raw = rerank_fixture()
        constraint = raw["apiDefinitions"][0]["authorization"]["operationConstraints"]["rerank"]
        constraint.pop("arrayLimits")
        constraint.pop("integerLimits")
        self.assertEqual(self.normalize(raw)["apiDefinitions"][0]["authorization"]["operationConstraints"]["rerank"]["arrayLimits"], {})

    def test_incompatible_routes_formats_and_missing_count_bounds_fail_before_deployment(self):
        for change in ("backend", "route", "method", "format", "extra-array",
                       "extra-integer", "zero-minimum", "top-above-document-bound",
                       "undeployed-model", "no-models", "profiles", "output-tokens"):
            raw = rerank_fixture()
            api = raw["apiDefinitions"][0]
            operation = api["operations"][0]
            constraint = api["authorization"]["operationConstraints"]["rerank"]
            if change == "backend": api["backendService"] = "foundryOpenAi"
            if change == "route": operation["urlTemplate"] = "/other"
            if change == "method": operation["method"] = "PUT"
            if change == "format": constraint["bodyFormat"] = "none"
            if change == "no-documents": constraint.pop("arrayLimits")
            if change == "extra-array": constraint["arrayLimits"]["other"] = 1
            if change == "no-top": constraint.pop("integerLimits")
            if change == "extra-integer": constraint["integerLimits"]["other"] = {"minimum": 1, "maximum": 1}
            if change == "optional-top": constraint["integerLimits"]["top_n"]["required"] = False
            if change == "zero-minimum": constraint["integerLimits"]["top_n"]["minimum"] = 0
            if change == "top-above-document-bound": constraint["integerLimits"]["top_n"]["maximum"] = 9
            if change == "undeployed-model": constraint["allowedModels"] = ["not-deployed"]
            if change == "no-models": constraint.pop("allowedModels")
            if change == "profiles": constraint.update(defaultProfile="synthetic", profiles=[{**copy.deepcopy(constraint), "profileId": "synthetic", "modalities": ["text"]}])
            if change == "output-tokens": constraint.update(maxOutputTokens=1, outputTokenParameter="max_tokens")
            with self.subTest(change=change), self.assertRaises(configuration.ConfigurationError):
                self.normalize(raw)


class RerankTargetExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required for actual APIM C# expressions")
        base = CSHARP.split("public static class ExpandedPolicyFixture")[0]
        source = base + r'''
public static class RerankTargetFixture {
 public static bool Target(Context context) { __TARGET__ }
 public static string Run(string input) {
  var result = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Request.Body = new Body { Data=item["body"].ToString(Formatting.None) };
   c.Variables["config"] = new JObject(new JProperty("operationConstraints", new JObject(
     new JProperty(c.Operation.Id, new JObject(new JProperty("kind", item["kind"]),
       new JProperty("allowedModels", new JArray("synthetic-ranker"))))))).ToString(Formatting.None);
   var before = c.Request.Body.Data;
   var allowed = Target(c);
   result[(string)item["id"]] = new JObject(new JProperty("allowed", allowed),
      new JProperty("unchanged", before == c.Request.Body.Data));
  }
  return result.ToString(Formatting.None);
 }
}'''
        source = source.replace("__TARGET__", actual_expression("gateway.xml", "gatewayTargetAllowed"))
        cases = [{"id": kind + "-" + name, "kind": kind, "body": body}
                 for kind in ("rerank", "bodyModel")
                 for name, body in (("approved", {"model": "synthetic-ranker"}),
                                    ("unapproved", {"model": "other-ranker"}), ("missing", {}),
                                    ("number", {"model": 1}), ("array", {"model": ["synthetic-ranker"]}))]
        with tempfile.TemporaryDirectory(prefix="rerank-policy-") as directory:
            path = Path(directory)
            (path / "policy.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[RerankTargetFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            completed = subprocess.run([PWSH, "-NoProfile", "-File", str(path / "run.ps1")],
                                       capture_output=True, text=True, timeout=60,
                                       env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if completed.returncode:
                raise AssertionError(completed.stderr)
            cls.results = json.loads(completed.stdout)

    def test_rerank_and_existing_body_model_keep_the_same_allowlist_without_rewriting(self):
        for kind in ("rerank", "bodyModel"):
            for name in ("approved", "unapproved", "missing", "number", "array"):
                with self.subTest(kind=kind, model=name):
                    self.assertEqual(self.results[kind + "-" + name],
                                     {"allowed": name == "approved", "unchanged": True})


class RerankPublicationTests(unittest.TestCase):
    def test_compiled_policy_preserves_semantic_limits_routing_and_audit_without_llm_counting(self):
        template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        api = configuration.normalize_configuration(rerank_fixture(), base_directory=CORE / "config")["apiDefinitions"][0]
        # The deployment stage resolves Foundry's owned endpoint before Bicep.
        api["backendUrl"] = "https://provider.example.com"
        parameters = {"apiDefinitions": [api], "publicHostName": "gateway.example.com", "names": {"apim": "apim-fixture"}}

        def evaluate(value):
            return PolicyExpression(value, template["variables"], functions=template["functions"]).evaluate(parameters)

        settings = evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[0], '', false())]")
        self.assertEqual(settings["operationConstraints"]["rerank"], api["authorization"]["operationConstraints"]["rerank"])
        self.assertEqual(settings["llmOperationIds"], [])
        self.assertEqual(settings["tokenLimitOperationIds"], [])
        root = ET.fromstring(evaluate("[variables('policyValues')]")[0])
        inbound = list(root.find("inbound"))
        limit = root.find('./inbound/include-fragment[@fragment-id="core-model-limits"]')
        target = root.find('./inbound/set-variable[@name="gatewayTargetAllowed"]')
        backend = root.find('./inbound/set-backend-service')
        self.assertLess(inbound.index(limit), inbound.index(target))
        self.assertLess(inbound.index(target), inbound.index(backend))
        operation = ET.fromstring(evaluate(template["resources"]["operationPolicies"]["properties"]["value"]))
        rewrite = operation.find("./inbound/rewrite-uri")
        self.assertEqual(rewrite.attrib, {"template": "/providers/synthetic/v1/rerank", "copy-unmatched-params": "false"})
        self.assertIsNotNone(operation.find("./inbound/base"))
        self.assertIsNone(root.find('.//azure-openai-token-limit'))


if __name__ == "__main__":
    unittest.main()
