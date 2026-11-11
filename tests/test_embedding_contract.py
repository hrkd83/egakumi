"""埋め込みの次元・入力意味制約を実APIM式で検査する。外部通信はしない。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep
from configuration_fixtures import CORE, configuration
from schema_helpers import local_schema_validator
from test_api_protocols import CSHARP, actual_expression, bound_coverage
from test_gateway_limits_telemetry import LIMIT_EXPRESSION, PWSH

def embedding_cases(apis):
    """配備設定と無関係な合成本文で、入力型・次元・既存契約の差を検査します。"""
    cases = []

    def add(id, provider, body, valid, *, kind=None, wire=None):
        api = copy.deepcopy(apis['openai-embeddings-v1' if provider == 'openai' else 'cohere-inference-v1'])
        op = 'embeddings' if provider == 'openai' else 'cohere-embeddings'
        if kind:
            api['authorization']['operationConstraints'][op]['kind'] = kind
            api['authorization']['operationConstraints'][op].pop('embeddingDimensionsByModel', None)
        case = {'id': id, 'operation': op, 'config': api['authorization'], 'body': body, 'publicExpectationAllowed': valid}
        if wire is not None:
            case['wire'] = wire
        cases.append(case)
    oa = {'model': 'text-embedding-3-small', 'input': ['independent'], 'encoding_format': 'float', 'dimensions': 256}
    co = {'model': 'embed-v-4-0', 'texts': ['independent'], 'input_type': 'search_query', 'embedding_types': ['float'], 'output_dimension': 256}
    for provider, body in [('openai', oa), ('cohere', co)]:
        add(provider + '-valid', provider, body, True)
        field = 'input' if provider == 'openai' else 'texts'
        for label, value in [('empty', []), ('missing', None), ('string', 'independent'), ('null-item', [None]), ('integer-item', [1]), ('object-item', [{}]), ('blank', [' \t\u3000']), ('BOM', ['\ufeff']), ('sixteen', ['a'] * 16), ('seventeen', ['a'] * 17), ('u0085-is-not-ECMAScript-whitespace', ['\x85'])]:
            b = {**body, field: value}
            add(provider + '-' + label, provider, b, label in ('sixteen', 'seventeen', 'u0085-is-not-ECMAScript-whitespace') or (provider == 'openai' and label in ('string', 'integer-item')))
        dimension = 'dimensions' if provider == 'openai' else 'output_dimension'
        for label, value in [('zero', 0), ('negative', -1), ('float', 1.5), ('true', True), ('null', None), ('string', '256'), ('object', {})]:
            add(provider + '-dim-' + label, provider, {**body, dimension: value}, False)
        b = copy.deepcopy(body)
        b.pop(dimension)
        add(provider + '-default', provider, b, True)
        add(provider + '-unknown-extension', provider, {**body, 'vendor_extension': {'synthetic': True}}, True)
        add(provider + '-unknown-model', provider, {**body, 'model': 'unapproved-synthetic-model'}, False)
        add(provider + '-wrong-model-type', provider, {**body, 'model': 1}, False)
    for dim in (1, 256, 1536):
        add('small-valid-' + str(dim), 'openai', {**oa, 'dimensions': dim}, True)
    add('small-max-plus1', 'openai', {**oa, 'dimensions': 1537}, False)
    add('large-max', 'openai', {**oa, 'model': 'text-embedding-3-large', 'dimensions': 3072}, True)
    add('large-max-plus1', 'openai', {**oa, 'model': 'text-embedding-3-large', 'dimensions': 3073}, False)
    add('openai-native-base64-not-newly-prohibited', 'openai', {**oa, 'encoding_format': 'base64'}, True)
    for f in ('texts', 'input_type', 'output_dimension', 'embedding_types'):
        add('openai-wrong-provider-' + f, 'openai', {**oa, f: None}, False)
    for f in ('input', 'dimensions', 'encoding_format'):
        add('cohere-wrong-provider-' + f, 'cohere', {**co, f: None}, False)
    for dim in (256, 512, 1024, 1536):
        add('cohere-dim-' + str(dim), 'cohere', {**co, 'output_dimension': dim}, True)
    add('cohere-invalid-dimension', 'cohere', {**co, 'output_dimension': 768}, False)
    for purpose in ('search_document', 'search_query', 'classification', 'clustering'):
        add('cohere-purpose-' + purpose, 'cohere', {**co, 'input_type': purpose}, True)
    for purpose in (None, 'arbitrary', 0):
        add('cohere-invalid-purpose-' + str(purpose), 'cohere', {**co, 'input_type': purpose}, False)
    add('direct-does-not-inherit-Portal-P', 'openai', {**oa, 'input': ['東京' + ' ' * 32000]}, True)
    add('other-bodyModel-unchanged', 'openai', {**oa, 'input': [], 'dimensions': 0}, True, kind='bodyModel')
    add('openai-token-batches', 'openai', {**oa, 'input': [[1, 2], [3]]}, True)
    compact = json.dumps(oa, separators=(',', ':'))
    add('raw-L-exact', 'openai', oa, True, wire=compact + ' ' * (262144 - len(compact.encode())))
    add('raw-L-plus1', 'openai', oa, True, wire=compact + ' ' * (262145 - len(compact.encode())))
    return cases

class EmbeddingConfigurationTests(unittest.TestCase):
    def test_shipped_contract_and_editor_schemas_use_one_dimension_map(self):
        for name, operation in (("openai-embeddings-v1", "embeddings"), ("cohere-inference-v1", "cohere-embeddings")):
            api = json.loads((CORE / f"apps/gateway/apim/apis/{name}.json").read_text())
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
        base=CSHARP.split('public static class ExpandedPolicyFixture')[0].replace('return (T)(object)JObject.Parse(Data);','return typeof(T)==typeof(byte[]) ? (T)(object)Encoding.UTF8.GetBytes(Data) : (T)(object)JObject.Parse(Data);')
        source=base+r'''
        public static class EmbeddingDirectFixture {
         public static string Profile(Context context) { __PROFILE__ }
         public static string Limits(Context context) { __LIMITS__ }
         public static bool Target(Context context) { __TARGET__ }
         public static string Run(string input) {
          var result=new JObject();foreach(JObject item in JArray.Parse(input)) {
           var c=new Context();c.Operation.Id=(string)item["operation"];c.Request.Body=new Body{Data=item["wire"] == null ? item["body"].ToString(Formatting.None) : (string)item["wire"]};c.Variables["config"]=item["config"].ToString(Formatting.None);var before=c.Request.Body.Data;
           var profile=JObject.Parse(Profile(c));bool p=(bool)profile["allowed"],l=false,t=false;if(p){c.Variables["gatewayOperationConstraint"]=profile["constraint"].ToString(Formatting.None);l=(bool)JObject.Parse(Limits(c))["allowed"];t=Target(c);}
           result[(string)item["id"]]=new JObject(new JProperty("profileAllowed",p),new JProperty("limitsAllowed",l),new JProperty("targetAllowed",t),new JProperty("requestNotRewritten",before==c.Request.Body.Data));
          }return result.ToString(Formatting.None);
         }
        }'''.replace('__PROFILE__',actual_expression('gateway.xml','gatewayProfileSelection')).replace('__LIMITS__',LIMIT_EXPRESSION).replace('__TARGET__',actual_expression('gateway.xml','gatewayTargetAllowed'))
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
                self.assertTrue(value["requestNotRewritten"])

    def test_unknown_models_keep_the_target_refusal(self):
        for name in ("openai-unknown-model", "cohere-unknown-model"):
            self.assertTrue(self.observed[name]["limitsAllowed"])
            self.assertFalse(self.observed[name]["targetAllowed"])

    def test_compiled_publication_preserves_dimension_map_without_llm_token_counting(self):
        template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        apis = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]
        for api in apis:
            if api["name"] not in ("openai-embeddings-v1", "cohere-inference-v1"):
                continue
            parameters = {"apiDefinitions": [api]}
            settings = PolicyExpression("[__bicep.auditSettings(parameters('apiDefinitions')[0], '', false())]",
                                        template["variables"], functions=template["functions"]).evaluate(parameters)
            self.assertEqual(settings["operationConstraints"], api["authorization"]["operationConstraints"])
            self.assertEqual(settings["llmOperationIds"], [])
            self.assertEqual(settings["tokenLimitOperationIds"], [])


if __name__ == "__main__":
    unittest.main()
