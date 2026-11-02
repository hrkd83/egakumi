"""文書受付の本文上限を完全パスへ限定し、共通WAF規則を保持する。"""
import copy
import json
from pathlib import Path
import unittest

from test_gateway_callback_waf import CONFIG, GATEWAY_ID, BASE_ID, gateway, policy, api_mapping, ready_gateway, _writable_policy
from test_portal_gateway import Transport
from gateway_ingress import DOCUMENT_PATH, DOCUMENT_RULE_NAME, document_upload_limit, _document_policy, _raw_body_policy, _path_gateway, configure_gateway_document_waf
from bicep_helpers import compile_bicep
from test_ssml_waf import GatewayExpression

ROOT = Path(__file__).resolve().parents[1]
POLICY_ID = BASE_ID + '-document-upload'


def configuration():
    """標準の文書APIと、既存の合成Gateway構成を組み合わせます。"""
    return {**copy.deepcopy(CONFIG), 'apiDefinitions': [json.loads((ROOT/'apps/gateway/apim/apis/document-analysis-v1.json').read_text(encoding='utf-8'))]}


class GatewayDocumentWafTests(unittest.TestCase):
    def test_only_the_fixed_document_contract_receives_one_mib_upload(self):
        self.assertEqual(document_upload_limit(configuration()),1048576)
        for target,value in [('path','other/api'),('kind','registeredOperation'),('limit',1048577),('limit',True),('route','/analyze/*')]:
            config=configuration()
            api=config['apiDefinitions'][0]
            if target=='path': api['path']=value
            elif target=='route': api['operations'][0]['urlTemplate']=value
            else: api['authorization']['operationConstraints']['analyze_document']['kind' if target=='kind' else 'maxRequestBytes']=value
            with self.subTest(target=target),self.assertRaises(ValueError):
                document_upload_limit(config)

    def test_policy_changes_only_observed_mime_rule_and_keeps_full_body_inspection(self):
        base=policy()
        desired=_document_policy(base,1048576)
        expected=_raw_body_policy(base)
        expected['properties']['policySettings'].update({'requestBodyCheck':True,'requestBodyEnforcement':True,'maxRequestBodySizeInKb':1025,'requestBodyInspectLimitInKB':1025})
        self.assertEqual(desired,expected)
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
        desired=_path_gateway(configuration(),source,GATEWAY_ID,POLICY_ID,paths=[DOCUMENT_PATH],rule_name=DOCUMENT_RULE_NAME,policy_suffix='-document-upload')
        mapping=api_mapping(desired)
        self.assertEqual(mapping['pathRules'][0]['properties']['paths'],[DOCUMENT_PATH])
        self.assertEqual(mapping['pathRules'][0]['properties']['firewallPolicy'],{'id':POLICY_ID})
        self.assertEqual(mapping['pathRules'][1:],api_mapping(source)['pathRules'])
        for key in ('httpListeners','backendAddressPools','backendHttpSettingsCollection','rewriteRuleSets'):
            self.assertEqual(desired['properties'][key],source['properties'][key])

    def test_redeployment_is_readonly_when_policy_and_route_match(self):
        base=policy()
        ready=ready_gateway(_path_gateway(configuration(),gateway(path_based=True),GATEWAY_ID,POLICY_ID,paths=[DOCUMENT_PATH],rule_name=DOCUMENT_RULE_NAME,policy_suffix='-document-upload'))
        assigned=_document_policy(base,1048576)
        assigned.update({'id':POLICY_ID,'etag':'"document-v1"'})
        assigned['properties']['provisioningState']='Succeeded'
        transport=Transport([{'statusCode':200,'body':ready},{'statusCode':200,'body':base},{'statusCode':200,'body':assigned}])
        receipt=configure_gateway_document_waf(configuration(),transport)
        self.assertEqual(receipt['maxRequestBytes'],1048576)
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
        values.update(resourceStem='test-fixture', location='japaneast', documentUploadPaths=[DOCUMENT_PATH],
                      wafMaxRequestBodySizeInKb=2000, wafRequestBodyInspectLimitInKb=2000,
                      wafCustomRules=[{'name': 'retain-custom', 'priority': 1, 'action': 'Block'}],
                      wafExclusions=[{'matchVariable': 'RequestHeaderValues', 'selectorMatchOperator': 'Equals', 'selector': 'Existing'}],
                      grpcEnabled=True)
        values.update(changes)
        return values

    def value(self, expression, parameters):
        return GatewayExpression(expression, self.template['variables']).evaluate(parameters)

    def resource(self, suffix):
        return next(item for item in self.resources if item['name'] == suffix)

    def test_main_derives_only_the_fixed_binary_one_mib_document_contract(self):
        from standard_apis import infrastructure_api_definitions
        original = configuration()['apiDefinitions'][0]
        expression = self.main['variables']['documentUploadPaths']
        def paths(api):
            config = {'apiDefinitions': [], 'infrastructureApiDefinitions': infrastructure_api_definitions([api])}
            return GatewayExpression(expression, self.main['variables']).evaluate({'configuration': config})
        self.assertEqual(paths(original), [DOCUMENT_PATH])
        for field, value in [('path', 'tools/other/v1'), ('method', 'GET'), ('urlTemplate', '/analyze/*'),
                             ('kind', 'registeredOperation'), ('bodyFormat', 'json'), ('maxRequestBytes', 1048577),
                             ('maxRequestBytes', 1024)]:
            changed = copy.deepcopy(original)
            if field == 'path': changed[field] = value
            elif field in ('method', 'urlTemplate'): changed['operations'][0][field] = value
            else: changed['authorization']['operationConstraints']['analyze_document'][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(paths(changed), [])
        self.assertEqual(self.module['properties']['parameters']['documentUploadPaths'],
                         {'value': "[variables('documentUploadPaths')]"})

    def test_compiled_document_policy_matches_python_and_keeps_every_common_protection(self):
        values = self.parameters()
        common = self.resource("[format('wafpol-agw-{0}', parameters('resourceStem'))]")
        document = self.resource("[format('wafpol-agw-{0}-document-upload', parameters('resourceStem'))]")
        baseline = self.value(common['properties'], values)
        configured = self.value(document['properties'], values)
        expected = _document_policy({'properties': baseline}, 1048576)['properties']
        self.assertEqual(configured, expected)
        self.assertEqual(configured['policySettings']['mode'], 'Prevention')
        self.assertTrue(configured['policySettings']['requestBodyCheck'])
        self.assertTrue(configured['policySettings']['requestBodyEnforcement'])
        self.assertEqual(configured['policySettings']['maxRequestBodySizeInKb'], 1025)
        self.assertEqual(configured['policySettings']['requestBodyInspectLimitInKB'], 1025)
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
            self.assertTrue(rule['firewallPolicy']['id'].endswith('-document-upload'))
            self.assertEqual(rule['backendAddressPool'], mapping['defaultBackendAddressPool'])
            self.assertEqual(rule['backendHttpSettings'], mapping['defaultBackendHttpSettings'])
            if grpc:
                self.assertEqual(rule['rewriteRuleSet'], mapping['defaultRewriteRuleSet'])
            else:
                self.assertNotIn('rewriteRuleSet', rule)

    def test_external_policy_standard_sku_and_unpublished_paths_do_not_create_document_policy(self):
        document = self.resource("[format('wafpol-agw-{0}-document-upload', parameters('resourceStem'))]")
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
