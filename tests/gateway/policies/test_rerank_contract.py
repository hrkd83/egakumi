"""Rerankの意味制約・モデル認可を実設定と実APIM式で検査する。外部通信はしない。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tests.support.arm_helpers import PolicyExpression, compiled_policy_value
from tests.support.bicep_helpers import compile_bicep
from tests.support.configuration_fixtures import CORE, api_fixture, configuration
from tests.support.schema_helpers import local_schema_validator, read_schema
from tests.support.powershell import PWSH
from tests.gateway.publication.test_api_protocols import CSHARP, actual_expression


def rerank_fixture():
    """配備済みの合成モデルを参照する独立したRerank API。"""
    raw = api_fixture()
    api = raw["apiDefinitions"][0]
    # Reuse only the public provider audience, never an adopter's configuration.
    provider = json.loads((CORE / "apps/api-gateway/apis/cohere-inference-v1.json").read_text())
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
        return configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")

    def test_shipped_rerank_and_embedding_keep_distinct_semantic_contracts(self):
        source = json.loads((CORE / "apps/api-gateway/apis/cohere-inference-v1.json").read_text())
        constraints = source["authorization"]["operationConstraints"]
        self.assertEqual(constraints["rerank"]["kind"], "rerank")
        self.assertEqual(constraints["cohere-embeddings"]["kind"], "textEmbeddings")
        result = self.normalize(rerank_fixture())["apiDefinitions"][0]
        self.assertEqual(result["authorization"]["operationConstraints"]["rerank"]["kind"], "rerank")
        self.assertEqual(result["operations"][0]["backendUrlTemplate"], "/providers/synthetic/v1/rerank")

    def test_both_editor_schemas_accept_only_the_registered_kind(self):
        for name in ("configuration", "params"):
            schema = read_schema(name)
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
 public static string OperationKind(Context context) { __KIND__ }
 public static bool TargetCheckRequired(Context context) { __REQUIRED__ }
 public static string TargetDecision(Context context) { __TARGET__ }
 public static string Run(string input) {
  var result = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Request.Body = new Body { Data=(string)item["body"], Unavailable=true };
   var constraint = new JObject();
   if (item.Property("kind") != null) constraint["kind"] = item["kind"];
   c.Variables["gatewayOperationConstraint"] = constraint.ToString(Formatting.None);
   c.Variables["gatewayOperationKind"] = OperationKind(c);
   var before = c.Request.Body.Data;
   var invoked = TargetCheckRequired(c);
   var allowed = !invoked || (bool)JObject.Parse(TargetDecision(c))["allowed"];
   result[(string)item["id"]] = new JObject(new JProperty("invoked", invoked), new JProperty("allowed", allowed),
      new JProperty("unchanged", before == c.Request.Body.Data), new JProperty("bodyReads", c.Request.Body.Reads));
  }
  return result.ToString(Formatting.None);
 }
}'''
        policy = ET.parse(CORE / "apps/api-gateway/policies/api-templates/rest/rest.xml")
        required = policy.find("./inbound/choose[@id='core-api-target-validation-selection']/when").get('condition')
        source = (source.replace("__TARGET__", actual_expression("rest.xml", "gatewayTargetDecision"))
                  .replace("__KIND__", actual_expression("rest.xml", "gatewayOperationKind"))
                  .replace("__REQUIRED__", required[2:-1]))
        bodies = {
            "model": json.dumps({"model": "provider-selected-model"}),
            "missing": "{}",
            "non-string": json.dumps({"model": ["synthetic-ranker"]}),
            "non-json": "opaque provider input",
        }
        cases = [{"id": kind + "-" + name, "kind": kind, "body": body}
                 for kind in ("rerank", "bodyModel", "textEmbeddings") for name, body in bodies.items()]
        cls.forwarded = [case["id"] for case in cases]
        for name, kind in (("registered", "registeredOperation"), ("wrong-case", "Rerank"),
                           ("unknown", "newOperation"), ("null", None), ("number", 7), ("array", ["rerank"])):
            cases.append({"id": name, "kind": kind, "body": bodies["model"]})
        cases.append({"id": "missing-kind", "body": bodies["model"]})
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

    def test_transparent_operations_skip_target_validation_and_preserve_provider_input(self):
        for case in self.forwarded:
            with self.subTest(case=case):
                self.assertEqual(self.results[case], {"invoked": False, "allowed": True, "unchanged": True, "bodyReads": 0})

    def test_other_and_invalid_kinds_still_reach_the_target_policy(self):
        for case in ("registered", "wrong-case", "unknown", "null", "number", "array", "missing-kind"):
            with self.subTest(case=case):
                self.assertEqual(self.results[case], {"invoked": True, "allowed": case == "registered",
                                                       "unchanged": True, "bodyReads": 0})


class RerankPublicationTests(unittest.TestCase):
    def test_compiled_policy_preserves_semantic_limits_routing_and_audit_without_llm_counting(self):
        template = compile_bicep("apps/api-gateway/api-publication.bicep")
        api = configuration.normalize_configuration(rerank_fixture(), base_directory=CORE / "infra/config")["apiDefinitions"][0]
        # The deployment stage resolves Foundry's owned endpoint before Bicep.
        api["backendUrl"] = "https://provider.example.com"
        parameters = {name: definition["defaultValue"] for name, definition in template["parameters"].items()
                      if "defaultValue" in definition}
        parameters.update(apiDefinitions=[api], publicHostName="gateway.example.com", names={"apim": "apim-fixture"})

        def evaluate(value):
            return PolicyExpression(value, template["variables"], functions=template["functions"]).evaluate(parameters)

        settings = evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[0], '', false())]")
        controls = evaluate("[__bicep.controlSettings(parameters('apiDefinitions')[0], '', false())]")
        self.assertEqual(controls["operationConstraints"]["rerank"], api["authorization"]["operationConstraints"]["rerank"])
        self.assertEqual(settings["llmOperationIds"], [])
        self.assertEqual(controls["tokenLimitOperationIds"], [])
        root = ET.fromstring(compiled_policy_value(template, parameters))
        inbound = list(root.find("inbound"))
        limit = root.find('./inbound/include-fragment[@fragment-id="core-model-limits"]')
        target = root.find("./inbound/choose[@id='core-api-target-validation-selection']")
        backend = root.find('./inbound/set-backend-service')
        self.assertIsNotNone(target.find("when/include-fragment[@fragment-id='core-api-target-validation']"))
        header = root.find('./inbound/include-fragment[@fragment-id="core-api-input-validation"]')
        self.assertLess(inbound.index(header), inbound.index(limit))
        self.assertLess(inbound.index(limit), inbound.index(target))
        self.assertLess(inbound.index(target), inbound.index(backend))
        operation = ET.fromstring(evaluate(template["resources"]["operationPolicies"]["properties"]["value"]))
        rewrite = operation.find("./inbound/rewrite-uri")
        self.assertEqual(rewrite.attrib, {"template": "/providers/synthetic/v1/rerank", "copy-unmatched-params": "false"})
        self.assertIsNotNone(operation.find("./inbound/base"))
        self.assertIsNone(root.find('.//azure-openai-token-limit'))


if __name__ == "__main__":
    unittest.main()
