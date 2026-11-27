"""埋め込みの配備設定と、本文を提供元に引き渡す実APIM式を検査する。"""
import copy
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression, compiled_policy_value
from bicep_helpers import compile_bicep
from configuration_fixtures import CORE, configuration
from deployment.helpers.apim_input_contract import render_input_validation_fragment
from schema_helpers import local_schema_validator
from test_api_protocols import CSHARP, actual_expression, bound_coverage
from test_gateway_limits_telemetry import LIMIT_EXPRESSION, PWSH

def embedding_cases(apis):
    """提供元が検査する合成本文と、Gatewayが検査する受信サイズを準備します。"""
    cases = []

    def add(id, provider, body, valid, *, kind=None, wire=None, max_bytes=None):
        api = copy.deepcopy(apis['openai-embeddings-v1' if provider == 'openai' else 'cohere-inference-v1'])
        op = 'embeddings' if provider == 'openai' else 'cohere-embeddings'
        if kind:
            api['authorization']['operationConstraints'][op]['kind'] = kind
            api['authorization']['operationConstraints'][op].pop('embeddingDimensionsByModel', None)
        if max_bytes is not None:
            api['authorization']['operationConstraints'][op]['maxRequestBytes'] = max_bytes
        case = {'id': id, 'operation': op, 'config': api['authorization'], 'body': body, 'publicExpectationAllowed': valid}
        if wire is not None:
            case['wire'] = wire
        cases.append(case)
    oa = {'model': 'text-embedding-3-small', 'input': ['independent'], 'encoding_format': 'float', 'dimensions': 256}
    co = {'model': 'embed-v-4-0', 'texts': ['independent'], 'input_type': 'search_query', 'embedding_types': ['float'], 'output_dimension': 256}
    for provider, body in [('openai', oa), ('cohere', co)]:
        add(provider + '-valid', provider, body, True)
        field = 'input' if provider == 'openai' else 'texts'
        for label, value in [('empty', []), ('null', None), ('string', 'independent'), ('null-item', [None]), ('integer-item', [1]), ('object-item', [{}]), ('blank', [' \t\u3000']), ('BOM', ['\ufeff']), ('sixteen', ['a'] * 16), ('seventeen', ['a'] * 17), ('u0085-is-not-ECMAScript-whitespace', ['\x85'])]:
            b = {**body, field: value}
            add(provider + '-' + label, provider, b, True)
        add(provider + '-input-missing', provider, {k: v for k, v in body.items() if k != field}, True)
        dimension = 'dimensions' if provider == 'openai' else 'output_dimension'
        for label, value in [('zero', 0), ('negative', -1), ('float', 1.5), ('true', True), ('null', None), ('string', '256'), ('object', {})]:
            add(provider + '-dim-' + label, provider, {**body, dimension: value}, True)
        b = copy.deepcopy(body)
        b.pop(dimension)
        add(provider + '-default', provider, b, True)
        add(provider + '-unknown-extension', provider, {**body, 'vendor_extension': {'synthetic': True}}, True)
        add(provider + '-unknown-model', provider, {**body, 'model': 'provider-selected-model'}, True)
        add(provider + '-wrong-model-type', provider, {**body, 'model': 1}, True)
    for dim in (1, 256, 1536):
        add('small-valid-' + str(dim), 'openai', {**oa, 'dimensions': dim}, True)
    add('small-max-plus1', 'openai', {**oa, 'dimensions': 1537}, True)
    add('large-max', 'openai', {**oa, 'model': 'text-embedding-3-large', 'dimensions': 3072}, True)
    add('large-max-plus1', 'openai', {**oa, 'model': 'text-embedding-3-large', 'dimensions': 3073}, True)
    add('openai-native-base64-not-newly-prohibited', 'openai', {**oa, 'encoding_format': 'base64'}, True)
    for f, value in (('texts', None), ('input_type', None), ('output_dimension', 0), ('embedding_types', ['synthetic'])):
        add('openai-mixed-provider-' + f, 'openai', {**oa, f: value}, True)
    for f, value in (('input', None), ('dimensions', 0), ('encoding_format', 'synthetic')):
        add('cohere-mixed-provider-' + f, 'cohere', {**co, f: value}, True)
    for dim in (256, 512, 1024, 1536):
        add('cohere-dim-' + str(dim), 'cohere', {**co, 'output_dimension': dim}, True)
    add('cohere-unlisted-dimension', 'cohere', {**co, 'output_dimension': 768}, True)
    for purpose in ('search_document', 'search_query', 'classification', 'clustering'):
        add('cohere-purpose-' + purpose, 'cohere', {**co, 'input_type': purpose}, True)
    for purpose in (None, 'arbitrary', 0):
        add('cohere-purpose-value-' + str(purpose), 'cohere', {**co, 'input_type': purpose}, True)
    add('direct-does-not-inherit-Portal-P', 'openai', {**oa, 'input': ['東京' + ' ' * 32000]}, True)
    add('other-bodyModel-unchanged', 'openai', {**oa, 'input': [], 'dimensions': 0}, True, kind='bodyModel')
    add('openai-token-batches', 'openai', {**oa, 'input': [[1, 2], [3]]}, True)
    add('openai-negative-token', 'openai', {**oa, 'input': [-1]}, True)
    add('openai-mixed-token', 'openai', {**oa, 'input': [1, 'synthetic']}, True)
    co_without_texts = {k: v for k, v in co.items() if k != 'texts'}
    # 独立した合成バイト列からURL形式を作ります。この試験は画像内容を検査しません。
    synthetic_image_url = 'data:image/png;base64,' + base64.b64encode(b'synthetic').decode('ascii')
    add('cohere-images-without-texts', 'cohere', {**co_without_texts, 'images': [synthetic_image_url]}, True)
    add('cohere-inputs-without-texts', 'cohere', {**co_without_texts, 'inputs': [{'content': [{'type': 'text', 'text': 'synthetic'}]}]}, True)
    compact = json.dumps(oa, separators=(',', ':'))
    add('raw-L-exact', 'openai', oa, True, wire=compact + ' ' * (262144 - len(compact.encode())))
    add('raw-L-plus1', 'openai', oa, True, wire=compact + ' ' * (262145 - len(compact.encode())))
    add('malformed-json', 'openai', oa, True, wire='{synthetic malformed JSON')
    # Azureの実上限まで確保せず、独立した小さな合成枠で本文サイズの境界を確認します。
    for size in (1024, 1025):
        add('synthetic-byte-' + str(size), 'openai', oa, size == 1024,
            wire=compact + ' ' * (size - len(compact.encode('utf-8'))), max_bytes=1024)
    return cases

class EmbeddingConfigurationTests(unittest.TestCase):
    def test_shipped_contract_and_editor_schemas_use_one_dimension_map(self):
        for name, operation in (("openai-embeddings-v1", "embeddings"), ("cohere-inference-v1", "cohere-embeddings")):
            api = json.loads((CORE / f"apps/api-gateway/apis/{name}.json").read_text())
            constraint = api["authorization"]["operationConstraints"][operation]
            self.assertEqual(constraint["kind"], "textEmbeddings")
            self.assertEqual(set(constraint["allowedModels"]), set(constraint["embeddingDimensionsByModel"]))
            for schema in ("configuration", "params"):
                validator = local_schema_validator({"$ref": f"{schema}.schema.json#/$defs/operationConstraint"})
                self.assertEqual(list(validator.iter_errors(constraint)), [])
                self.assertTrue(list(validator.iter_errors({**constraint, "kind": "unknownEmbedding"})))

    def test_missing_incompatible_or_invalid_dimension_contracts_fail_before_deployment(self):
        raw = bound_coverage()
        for label, mutate in [('missing-map', lambda c: c.pop('embeddingDimensionsByModel')), ('extra-model-map', lambda c: c['embeddingDimensionsByModel'].update(other={'default': 256, 'maximum': 256})), ('missing-model-map', lambda c: c['embeddingDimensionsByModel'].pop(next(iter(c['embeddingDimensionsByModel'])))), ('zero-maximum', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(maximum=0)), ('bool-default', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(default=True)), ('default-above', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(default=999999)), ('zero-enum', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(allowed=[0, 1536])), ('duplicate-enum', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(allowed=[1536, 1536])), ('missing-default-enum', lambda c: c['embeddingDimensionsByModel'][next(iter(c['embeddingDimensionsByModel']))].update(allowed=[256])), ('token-limits', lambda c: c.update(maxOutputTokens=512)), ('wrong-kind', lambda c: c.update(kind='bodyModel'))]:
            v = copy.deepcopy(raw)
            a = next((x for x in v['apiDefinitions'] if x['name'] == 'openai-embeddings-v1'))
            c = a['authorization']['operationConstraints']['embeddings']
            mutate(c)
            try:
                configuration.normalize_configuration(v, base_directory=CORE / 'config')
            except configuration.ConfigurationError:
                pass
            else:
                self.fail(label)
        v = copy.deepcopy(raw)
        a = next((x for x in v['apiDefinitions'] if x['name'] == 'cohere-inference-v1'))
        a['authorization']['operationConstraints']['cohere-embeddings']['embeddingDimensionsByModel']['embed-v-4-0'].pop('allowed')
        try:
            configuration.normalize_configuration(v, base_directory=CORE / 'config')
        except configuration.ConfigurationError:
            pass
        else:
            self.fail('cohere enum')

class EmbeddingExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell is required for actual APIM C# expressions")
        apis = {api["name"]: api for api in configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]}
        cases = embedding_cases(apis)
        base = CSHARP.split('public static class ExpandedPolicyFixture', 1)[0]
        body_start = base.index('public class Body {')
        body_end = base.index('public class Url {', body_start)
        base = base[:body_start] + '''public class Body {
 public string Data; public int Reads; public int ByteReads;
 public T As<T>(bool preserveContent=false) {
  Reads++;
  if (!preserveContent) throw new Exception("Request body was consumed");
  if (typeof(T)==typeof(byte[])) { ByteReads++; return (T)(object)Encoding.UTF8.GetBytes(Data); }
  return (T)(object)JObject.Parse(Data);
 }
}
''' + base[body_end:]
        source=base+r'''
        public static class EmbeddingDirectFixture {
         public static string Profile(Context context) { __PROFILE__ }
         public static string Limits(Context context) { __LIMITS__ }
         public static string TargetDecision(Context context) { __TARGET__ }
         public static bool Target(Context context) { return (bool)JObject.Parse(TargetDecision(context))["allowed"]; }
         public static string Run(string input) {
          var result=new JObject();foreach(JObject item in JArray.Parse(input)) {
           var c=new Context();c.Operation.Id=(string)item["operation"];c.Request.Body=new Body{Data=item["wire"] == null ? item["body"].ToString(Formatting.None) : (string)item["wire"]};c.Variables["config"]=item["config"].ToString(Formatting.None);var before=c.Request.Body.Data;
           c.Variables["gatewayControlSettings"]=new JObject(new JProperty("operationConstraints", item["config"]["operationConstraints"])).ToString(Formatting.None);
           var selected=Profile(c);c.Variables["gatewayOperationSelection"]=selected;
           var profile=JObject.Parse(selected);bool p=(bool)profile["allowed"],l=false,t=false;int limitsStatus=0,limitBodyReads=0,limitByteReads=0,targetBodyReads=0;
           if(p){
            c.Variables["gatewayOperationConstraint"]=profile["constraint"].ToString(Formatting.None);
            var beforeLimitReads=c.Request.Body.Reads;var beforeLimitByteReads=c.Request.Body.ByteReads;
            var limits=JObject.Parse(Limits(c));l=(bool)limits["allowed"];limitsStatus=(int)limits["status"];
            limitBodyReads=c.Request.Body.Reads-beforeLimitReads;limitByteReads=c.Request.Body.ByteReads-beforeLimitByteReads;
            var beforeTargetReads=c.Request.Body.Reads;t=Target(c);targetBodyReads=c.Request.Body.Reads-beforeTargetReads;
           }
           result[(string)item["id"]]=new JObject(new JProperty("profileAllowed",p),new JProperty("limitsAllowed",l),new JProperty("limitsStatus",limitsStatus),new JProperty("limitBodyReads",limitBodyReads),new JProperty("limitByteReads",limitByteReads),new JProperty("targetBodyReads",targetBodyReads),new JProperty("targetAllowed",t),new JProperty("requestNotRewritten",before==c.Request.Body.Data));
          }return result.ToString(Formatting.None);
         }
        }'''.replace('__PROFILE__',actual_expression('rest.xml','gatewayOperationSelection')).replace('__LIMITS__',LIMIT_EXPRESSION).replace('__TARGET__',actual_expression('rest.xml','gatewayTargetDecision'))
        with tempfile.TemporaryDirectory(prefix='embedding-direct-contract-')as temporary:
         p=Path(temporary);(p/'policy.cs').write_text(source);(p/'cases.json').write_text(json.dumps(cases));(p/'run.ps1').write_text("$ErrorActionPreference='Stop'\n$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\nAdd-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'\n[EmbeddingDirectFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n")
         for q in p.iterdir():q.chmod(0o600)
         c=subprocess.run([PWSH,'-NoProfile','-NonInteractive','-File',str(p/'run.ps1')],capture_output=True,text=True,timeout=90,env={**os.environ,'DOTNET_BUNDLE_EXTRACT_BASE_DIR':str(p/'dotnet'),'PSModuleAnalysisCachePath':str(p/'module-cache')})
         if c.returncode:raise AssertionError(c.stderr)
         observed=json.loads(c.stdout)

        cls.cases = cases
        cls.observed = observed

    def test_all_request_semantics_match_without_rewriting(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                value = self.observed[case["id"]]
                actual = value["profileAllowed"] and value["limitsAllowed"] and value["targetAllowed"]
                self.assertEqual(actual, case["publicExpectationAllowed"])
                if case["publicExpectationAllowed"]:
                    self.assertEqual(value["limitsStatus"], 200)
                self.assertTrue(value["requestNotRewritten"])

    def test_mixed_provider_fields_do_not_reject_valid_native_inputs_or_rewrite_the_body(self):
        for provider, fields in (("openai", ("texts", "input_type", "output_dimension", "embedding_types")),
                                 ("cohere", ("input", "dimensions", "encoding_format"))):
            for field in fields:
                name = provider + "-mixed-provider-" + field
                with self.subTest(case=name):
                    self.assertEqual(self.observed[name]["limitsStatus"], 200)
                    self.assertTrue(self.observed[name]["limitsAllowed"])
                    self.assertTrue(self.observed[name]["targetAllowed"])
                    self.assertTrue(self.observed[name]["requestNotRewritten"])

    def test_purpose_input_and_dimension_values_are_forwarded_without_rewriting(self):
        for name in ("cohere-purpose-value-None", "cohere-purpose-value-arbitrary", "cohere-purpose-value-0",
                     "cohere-empty", "cohere-null-item"):
            with self.subTest(case=name):
                self.assertEqual(self.observed[name]["limitsStatus"], 200)
                self.assertTrue(self.observed[name]["limitsAllowed"])
                self.assertTrue(self.observed[name]["targetAllowed"])
                self.assertTrue(self.observed[name]["requestNotRewritten"])
        for name in ("cohere-dim-zero", "cohere-dim-negative", "cohere-dim-float", "cohere-dim-true",
                     "cohere-dim-null", "cohere-dim-string", "cohere-dim-object"):
            with self.subTest(case=name):
                self.assertEqual(self.observed[name]["limitsStatus"], 200)
                self.assertTrue(self.observed[name]["limitsAllowed"])
                self.assertTrue(self.observed[name]["targetAllowed"])
                self.assertTrue(self.observed[name]["requestNotRewritten"])

    def test_body_size_checks_remain_while_malformed_json_is_left_to_target(self):
        for name, status in (("malformed-json", 200), ("synthetic-byte-1024", 200), ("synthetic-byte-1025", 413)):
            with self.subTest(case=name):
                self.assertEqual(self.observed[name]["limitsStatus"], status)
                self.assertEqual(self.observed[name]["limitsAllowed"], status == 200)
                self.assertTrue(self.observed[name]["requestNotRewritten"])
        self.assertTrue(self.observed["malformed-json"]["targetAllowed"])

    def test_limits_measure_bytes_and_target_leaves_body_interpretation_to_provider(self):
        for name in ("openai-valid", "cohere-valid", "malformed-json", "openai-unknown-model", "cohere-wrong-model-type"):
            with self.subTest(case=name):
                self.assertEqual(self.observed[name]["limitBodyReads"], 1)
                self.assertEqual(self.observed[name]["limitByteReads"], 1)
                self.assertEqual(self.observed[name]["targetBodyReads"], 0)

    def test_compiled_publication_preserves_dimension_map_without_llm_token_counting(self):
        template = compile_bicep("apps/api-gateway/api-publication.bicep")
        apis = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]
        for api in apis:
            if api["name"] not in ("openai-embeddings-v1", "cohere-inference-v1"):
                continue
            parameters = {"apiDefinitions": [api]}
            settings = PolicyExpression("[__bicep.auditSettings(parameters('apiDefinitions')[0], '', false())]",
                                        template["variables"], functions=template["functions"]).evaluate(parameters)
            controls = PolicyExpression("[__bicep.controlSettings(parameters('apiDefinitions')[0], '', false())]",
                                        template["variables"], functions=template["functions"]).evaluate(parameters)
            self.assertEqual(controls["operationConstraints"], api["authorization"]["operationConstraints"])
            self.assertEqual(settings["llmOperationIds"], [])
            self.assertEqual(controls["tokenLimitOperationIds"], [])

    def test_compiled_embedding_policies_publish_the_guard_without_rewriting_native_queries(self):
        template = compile_bicep("apps/api-gateway/api-publication.bicep")
        apis = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]
        source_guard = ET.fromstring(render_input_validation_fragment())
        resources = template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        operation_policy = next(resource for resource in resources
            if resource["type"] == "Microsoft.ApiManagement/service/apis/operations/policies"
            and resource.get("copy", {}).get("name") == "operationPolicies")
        for api in apis:
            if api["name"] not in ("openai-embeddings-v1", "cohere-inference-v1"):
                continue
            api["backendUrl"] = "https://provider.example.com/openai/v1" if api["name"] == "openai-embeddings-v1" else "https://provider.example.com"
            parameters = {name: definition["defaultValue"] for name, definition in template["parameters"].items()
                          if "defaultValue" in definition}
            parameters.update(apiDefinitions=[api], publicHostName="gateway.example.com", names={"apim": "apim-fixture"})

            def evaluate(value):
                return PolicyExpression(value, template["variables"], functions=template["functions"]).evaluate(parameters)

            root = ET.fromstring(compiled_policy_value(template, parameters))
            fragment = next(resource for resource in resources if resource["type"] == "Microsoft.ApiManagement/service/policyFragments"
                            and "core-api-input-validation" in resource["name"])
            actual_guard = ET.fromstring(evaluate(fragment["properties"]["value"]))
            self.assertEqual(ET.tostring(actual_guard), ET.tostring(source_guard))
            output = actual_guard.find('set-variable').get('name')
            self.assertEqual(output, 'gatewayApiInputValidationPassed')
            self.assertIsNone(root.find('./inbound/set-variable[@name="gatewayApiInputValidationPassed"]'))
            inbound = list(root.find("inbound"))
            guard = root.find('./inbound/include-fragment[@fragment-id="core-api-input-validation"]')
            rejection = inbound[inbound.index(guard) + 1].find('when')
            self.assertEqual(rejection.get('condition'), '@(!(bool)context.Variables["' + output + '"])')
            self.assertEqual(rejection.find('set-variable[@name="gatewayAuditCode"]').get('value'), 'INVALID_CONTENT_TYPE_HEADER')
            self.assertEqual(rejection.find('return-response/set-status').attrib,
                             {'code': '400', 'reason': 'Invalid Content-Type header'})
            limit = root.find('./inbound/include-fragment[@fragment-id="core-model-limits"]')
            self.assertLess(inbound.index(guard), inbound.index(limit))
            self.assertEqual({node.get("name") for node in root.findall(".//set-query-parameter")}, {"api-key", "subscription-key"})
            operation = ET.fromstring(evaluate(operation_policy["properties"]["value"]))
            rewrite = operation.find("./inbound/rewrite-uri")
            if api["name"] == "openai-embeddings-v1":
                self.assertIsNone(rewrite)
            else:
                self.assertEqual(rewrite.attrib, {"template": "/providers/cohere/v2/embed", "copy-unmatched-params": "false"})


if __name__ == "__main__":
    unittest.main()
