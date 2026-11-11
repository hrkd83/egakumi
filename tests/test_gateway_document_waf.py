"""文書受付の本文上限を完全パスへ限定し、共通WAF規則を保持する。"""
import copy
import json
from pathlib import Path
import unittest

from test_gateway_callback_waf import CONFIG, GATEWAY_ID, BASE_ID, gateway, policy, api_mapping, ready_gateway, _writable_policy
from deployment.helpers.waf_names import policy_id, policy_name
from deployment.helpers.waf_body import ai_body_settings
from test_portal_gateway import Transport
from gateway_ingress import DOCUMENT_PATH, DOCUMENT_RULE_NAME, document_upload_limit, _document_policy, _raw_body_policy, _path_gateway, configure_gateway_document_waf
from bicep_helpers import compile_bicep
from test_ssml_waf import GatewayExpression
import test_ssml_waf as waf_fixtures

ROOT = Path(__file__).resolve().parents[1]
POLICY_ID = policy_id(CONFIG, 'g-document-upload')


def configuration():
    """標準の文書APIと、既存の合成Gateway構成を組み合わせます。"""
    return {**copy.deepcopy(CONFIG), 'apiDefinitions': [json.loads((ROOT/'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8'))]}


class GatewayDocumentWafTests(unittest.TestCase):
    def test_only_the_canonical_document_post_receives_the_api_upload_limit(self):
        original = configuration()
        expected = original['apiDefinitions'][0]['authorization']['operationConstraints']['analyze_document']['maxRequestBytes']
        self.assertEqual(document_upload_limit(original), expected)
        for maximum in (1024, 1048577, 500000000):
            changed = configuration()
            changed['apiDefinitions'][0]['authorization']['operationConstraints']['analyze_document']['maxRequestBytes'] = maximum
            self.assertEqual(document_upload_limit(changed), maximum)
        for target,value in [('path','other/api'),('kind','registeredOperation'),('limit',0),('limit',True),('route','/analyze/*')]:
            config=configuration()
            api=config['apiDefinitions'][0]
            if target=='path': api['path']=value
            elif target=='route': api['operations'][0]['urlTemplate']=value
            else: api['authorization']['operationConstraints']['analyze_document']['kind' if target=='kind' else 'maxRequestBytes']=value
            with self.subTest(target=target),self.assertRaises(ValueError):
                document_upload_limit(config)

    def test_document_acceptance_is_independent_of_waf_inspection_prefix(self):
        base=policy()
        desired=_document_policy(base,1048576)
        expected=_raw_body_policy(base)
        expected['properties']['policySettings'] = ai_body_settings(base['properties']['policySettings'])
        self.assertEqual(desired,expected)
        self.assertEqual(_document_policy(base, 500000000), expected)
        self.assertEqual(base,policy())

    def test_syntax_and_sqli_rules_are_retained_and_syntax_disable_is_rejected(self):
        base=policy()
        groups=[{'ruleGroupName':'REQUEST-920-PROTOCOL-ENFORCEMENT','rules':[{'ruleId':'920470','state':'Enabled'}]},
                {'ruleGroupName':'REQUEST-942-APPLICATION-ATTACK-SQLI','rules':[{'ruleId':'942100','state':'Enabled'}]}]
        base['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides']=groups
        desired=_document_policy(base,1048576)
        actual=desired['properties']['managedRules']['managedRuleSets'][0]['ruleGroupOverrides']
        self.assertEqual(actual[0]['rules'],[{'ruleId':'920470','state':'Enabled'},{'ruleId':'920420','state':'Disabled'}])
        self.assertEqual(actual[1],groups[1])
        groups[0]['rules'][0]['state']='Disabled'
        with self.assertRaisesRegex(ValueError,'920470'):
            _document_policy(base,1048576)

    def test_exact_path_routing_keeps_existing_routes_and_backend(self):
        source=gateway(path_based=True)
        desired=_path_gateway(configuration(),source,GATEWAY_ID,POLICY_ID,paths=[DOCUMENT_PATH],rule_name=DOCUMENT_RULE_NAME,policy_key='g-document-upload')
        mapping=api_mapping(desired)
        self.assertEqual(mapping['pathRules'][0]['properties']['paths'],[DOCUMENT_PATH])
        self.assertEqual(mapping['pathRules'][0]['properties']['firewallPolicy'],{'id':POLICY_ID})
        self.assertEqual(mapping['pathRules'][1:],api_mapping(source)['pathRules'])
        for key in ('httpListeners','backendAddressPools','backendHttpSettingsCollection','rewriteRuleSets'):
            self.assertEqual(desired['properties'][key],source['properties'][key])

    def test_redeployment_is_readonly_when_policy_and_route_match(self):
        base=policy()
        ready=ready_gateway(_path_gateway(configuration(),gateway(path_based=True),GATEWAY_ID,POLICY_ID,paths=[DOCUMENT_PATH],rule_name=DOCUMENT_RULE_NAME,policy_key='g-document-upload'))
        maximum = document_upload_limit(configuration())
        assigned=_document_policy(base,maximum)
        assigned.update({'id':POLICY_ID,'etag':'"document-v1"'})
        assigned['properties']['provisioningState']='Succeeded'
        transport=Transport([{'statusCode':200,'body':ready},{'statusCode':200,'body':base},{'statusCode':200,'body':assigned}])
        receipt=configure_gateway_document_waf(configuration(),transport)
        self.assertEqual(receipt['maxRequestBytes'],maximum)
        self.assertEqual(receipt['excludedRuleIds'],[])
        self.assertEqual(receipt['disabledRuleIds'],['920420','930110'])
        self.assertEqual(receipt['retainedRuleIds'],['920470','930100'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertFalse(receipt['policyUpdated'])
        self.assertEqual([call['method'] for call in transport.calls],['GET','GET','GET'])

    def test_unpublished_document_api_does_not_read_azure(self):
        transport=Transport([])
        self.assertFalse(configure_gateway_document_waf(CONFIG,transport)['required'])
        self.assertEqual(transport.calls,[])


class GatewayDocumentWafBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = compile_bicep('infra/main.bicep')
        resources = cls.main['resources']
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        cls.module = next(item for item in resources if 'documentUploadPaths' in item.get('properties', {}).get('parameters', {}))
        cls.template = cls.module['properties']['template']
        resources = cls.template['resources']
        cls.resources = list(resources.values()) if isinstance(resources, dict) else resources

    def parameters(self, **changes):
        values = {key: copy.deepcopy(item.get('defaultValue', 'fixture'))
                  for key, item in self.template['parameters'].items()}
        values.update(resourceStem='test-fixture', wafPolicyIdentifier='abc123', location='japaneast', documentUploadPaths=[DOCUMENT_PATH],
                      wafMaxRequestBodySizeInKb=2000, wafRequestBodyInspectLimitInKb=2000,
                      wafCustomRules=[{'name': 'retain-custom', 'priority': 1, 'action': 'Block'}],
                      wafExclusions=[{'matchVariable': 'RequestHeaderValues', 'selectorMatchOperator': 'Equals', 'selector': 'Existing'}],
                      grpcEnabled=True)
        values.update(changes)
        return values

    def value(self, expression, parameters):
        return GatewayExpression(expression, self.template['variables']).evaluate(parameters)

    resource = waf_fixtures.SsmlWafTests.resource

    def test_main_derives_only_the_canonical_binary_document_post_independent_of_size(self):
        from standard_apis import infrastructure_api_definitions
        original = configuration()['apiDefinitions'][0]
        expression = self.main['variables']['documentUploadPaths']
        def paths(api):
            config = {'apiDefinitions': [], 'infrastructureApiDefinitions': infrastructure_api_definitions([api])}
            return GatewayExpression(expression, self.main['variables']).evaluate({'configuration': config})
        self.assertEqual(paths(original), [DOCUMENT_PATH])
        for maximum in (1024, 1048577, 500000000):
            changed = copy.deepcopy(original)
            changed['authorization']['operationConstraints']['analyze_document']['maxRequestBytes'] = maximum
            self.assertEqual(paths(changed), [DOCUMENT_PATH])
        for field, value in [('path', 'tools/other/v1'), ('method', 'GET'), ('urlTemplate', '/analyze/*'),
                             ('kind', 'registeredOperation'), ('bodyFormat', 'json'), ('name', 'other-api')]:
            changed = copy.deepcopy(original)
            if field in ('path', 'name'): changed[field] = value
            elif field in ('method', 'urlTemplate'): changed['operations'][0][field] = value
            else: changed['authorization']['operationConstraints']['analyze_document'][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(paths(changed), [])
        self.assertEqual(self.module['properties']['parameters']['documentUploadPaths'],
                         {'value': "[variables('documentUploadPaths')]"})

    def test_compiled_document_policy_matches_python_and_keeps_every_common_protection(self):
        values = self.parameters()
        common = self.resource("g-base")
        document = self.resource("g-document-upload")
        baseline = self.value(common['properties'], values)
        configured = self.value(document['properties'], values)
        expected = _document_policy({'properties': baseline}, 1048576)['properties']
        self.assertEqual(configured, expected)
        self.assertEqual(configured['policySettings']['mode'], 'Prevention')
        self.assertTrue(configured['policySettings']['requestBodyCheck'])
        self.assertFalse(configured['policySettings']['requestBodyEnforcement'])
        self.assertFalse(configured['policySettings']['fileUploadEnforcement'])
        self.assertEqual(configured['policySettings']['maxRequestBodySizeInKb'], 2000)
        self.assertEqual(configured['policySettings']['requestBodyInspectLimitInKB'], 2000)
        self.assertEqual(configured['policySettings']['logScrubbing'], baseline['policySettings']['logScrubbing'])
        self.assertEqual(configured['customRules'][:len(baseline['customRules'])], baseline['customRules'])
        self.assertEqual(len(configured['customRules']), len(baseline['customRules']) + 4)
        self.assertEqual(configured['managedRules']['exclusions'], baseline['managedRules']['exclusions'])
        self.assertEqual(configured['managedRules']['managedRuleSets'][0]['ruleGroupOverrides'], [{
            'ruleGroupName': 'REQUEST-920-PROTOCOL-ENFORCEMENT', 'rules': [{'ruleId': '920420', 'state': 'Disabled'}]}, {
            'ruleGroupName': 'REQUEST-930-APPLICATION-ATTACK-LFI', 'rules': [{'ruleId': '930110', 'state': 'Disabled'}]}])

    def test_document_path_keeps_the_apim_backend_and_optional_trusted_rewrite(self):
        gateway_resource = next(item for item in self.resources if item['type'] == 'Microsoft.Network/applicationGateways')
        for grpc in (False, True):
            properties = self.value(gateway_resource['properties'], self.parameters(grpcEnabled=grpc))
            mapping = next(item['properties'] for item in properties['urlPathMaps'] if item['name'] == 'pathmap-protocols')
            selected = [item for item in mapping['pathRules'] if item['name'] == DOCUMENT_RULE_NAME]
            self.assertEqual(len(selected), 1)
            rule = selected[0]['properties']
            self.assertEqual(rule['paths'], [DOCUMENT_PATH])
            self.assertTrue(rule['firewallPolicy']['id'].endswith('/wafpol-g-document-upload-abc123'))
            self.assertEqual(rule['backendAddressPool'], mapping['defaultBackendAddressPool'])
            self.assertEqual(rule['backendHttpSettings'], mapping['defaultBackendHttpSettings'])
            if grpc:
                self.assertEqual(rule['rewriteRuleSet'], mapping['defaultRewriteRuleSet'])
            else:
                self.assertNotIn('rewriteRuleSet', rule)

    def test_external_policy_standard_sku_and_unpublished_paths_do_not_create_document_policy(self):
        document = self.resource("g-document-upload")
        gateway_resource = next(item for item in self.resources if item['type'] == 'Microsoft.Network/applicationGateways')
        for changes in ({'existingWafPolicyId': '/external/policy'}, {'skuName': 'Standard_v2'},
                        {'documentUploadPaths': []}, {'documentUploadPaths': ['/tools/documents/v1/analyze/other']}):
            with self.subTest(changes=changes):
                values = self.parameters(**changes)
                self.assertFalse(self.value(document['condition'], values))
                properties = self.value(gateway_resource['properties'], values)
                self.assertFalse(any(rule['name'] == DOCUMENT_RULE_NAME
                    for mapping in properties.get('urlPathMaps', [])
                    for rule in mapping['properties']['pathRules']))


if __name__ == '__main__':
    unittest.main()
