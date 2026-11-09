"""GatewayのOAuth callback調整で、転送先と既存の保護を維持することを検証する。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'apps/gateway/deploy'))
from gateway_ingress import (
    CALLBACK_PATH, CALLBACK_RULE_NAME, configure_gateway_identity_callback_waf,
    gateway_id, _callback_gateway, _callback_policy, _writable, _writable_policy,
)
from test_portal_gateway import Transport
from deployment.helpers.apim_naming import apim_name
from deployment.helpers.waf_names import policy_id, policy_name


CONFIG = {'subscriptionId': '11111111-1111-4111-8111-111111111111', 'resourceStem': 'test-fixture',
          'resourceGroupNames': {'nw': 'rg-test-fixture-nw'}, 'services': {'apimInstanceId': 'abc123'},
          'gateway': {'publicHostName': 'gateway.example.test', 'grpc': {'enabled': True}}}
GATEWAY_ID = gateway_id(CONFIG)
BASE_ID = policy_id(CONFIG, 'g-base')
POLICY_ID = policy_id(CONFIG, 'g-auth')
REWRITE = {'id': GATEWAY_ID + '/rewriteRuleSets/trusted-l7-ingress'}
POOL = {'id': GATEWAY_ID + '/backendAddressPools/bepool-apim'}
BACKEND = {'id': GATEWAY_ID + '/backendHttpSettingsCollection/behttp-apim'}


def gateway(*, path_based=False):
    properties = {
        'provisioningState': 'Succeeded', 'sku': {'name': 'WAF_v2'}, 'firewallPolicy': {'id': BASE_ID},
        'httpListeners': [{'name': 'listener-https', 'properties': {'protocol': 'Https', 'hostName': 'gateway.example.test',
                            'sslCertificate': {'id': GATEWAY_ID + '/sslCertificates/gateway-tls'}}}],
        'backendAddressPools': [{'name': 'bepool-apim', 'properties': {'backendAddresses': [{'ipAddress': '10.0.1.4'}]}}],
        'backendHttpSettingsCollection': [{'name': 'behttp-apim', 'properties': {'hostName': apim_name(CONFIG) + '.azure-api.net',
                      'protocol': 'Http', 'port': 80, 'requestTimeout': 600}}],
        'rewriteRuleSets': [{'name': 'trusted-l7-ingress', 'properties': {'rewriteRules': [
            {'name': 'replace-untrusted-edge-proof', 'actionSet': {'requestHeaderConfigurations': [
                {'headerName': 'X-Core-L7-Ingress', 'headerValue': 'fixture-server-only-edge-proof'}]}}]}}],
        'requestRoutingRules': [
            {'name': 'rule-https-to-apim', 'properties': {'ruleType': 'Basic', 'priority': 200,
             'httpListener': {'id': GATEWAY_ID + '/httpListeners/listener-https'},
             'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE}},
            {'name': 'rule-portal-https', 'properties': {'ruleType': 'PathBasedRouting', 'priority': 210,
             'urlPathMap': {'id': GATEWAY_ID + '/urlPathMaps/pathmap-portal-auth'}}}],
        'urlPathMaps': [{'name': 'pathmap-portal-auth', 'properties': {'pathRules': [
            {'name': 'portal-callback', 'properties': {'paths': ['/api/auth/callback/microsoft'],
             'firewallPolicy': {'id': policy_id(CONFIG, 'p-auth')}}}]}}],
    }
    if path_based:
        properties['requestRoutingRules'][0]['properties'] = {'ruleType': 'PathBasedRouting', 'priority': 200,
            'httpListener': {'id': GATEWAY_ID + '/httpListeners/listener-https'},
            'urlPathMap': {'id': GATEWAY_ID + '/urlPathMaps/pathmap-protocols'}}
        properties['urlPathMaps'].append({'name': 'pathmap-protocols', 'properties': {
            'defaultBackendAddressPool': POOL, 'defaultBackendHttpSettings': BACKEND, 'defaultRewriteRuleSet': REWRITE,
            'pathRules': [{'name': 'existing-api', 'properties': {'paths': ['/auth/*'], 'backendAddressPool': POOL,
                          'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE, 'firewallPolicy': {'id': policy_id(CONFIG, 'g-chat-completions')}}}]}})
    return {'id': GATEWAY_ID, 'name': 'agw-test-fixture', 'type': 'Microsoft.Network/applicationGateways',
            'etag': '"gateway-v1"', 'location': 'japaneast', 'properties': copy.deepcopy(properties)}


def policy():
    return {'id': BASE_ID, 'name': policy_name(CONFIG, 'g-base'), 'etag': '"base-v1"', 'location': 'japaneast',
        'type': 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies', 'tags': {'fixture': 'keep'},
        'properties': {'provisioningState': 'Succeeded', 'applicationGateways': [{'id': GATEWAY_ID}],
            'policySettings': {'state': 'Enabled', 'mode': 'Prevention', 'requestBodyCheck': True,
                               'maxRequestBodySizeInKb': 128, 'logScrubbing': {'state': 'Enabled'}},
            'managedRules': {'managedRuleSets': [{'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2', 'ruleGroupOverrides': []}],
                             'exclusions': [{'matchVariable': 'RequestHeaderValues', 'selectorMatchOperator': 'Equals', 'selector': 'Existing'}]},
            'customRules': [{'name': 'enterprise-block', 'priority': 1, 'action': 'Block'}]}}


def ready_policy(desired):
    result = copy.deepcopy(desired)
    result.update({'id': POLICY_ID, 'name': policy_name(CONFIG, 'g-auth'),
                   'type': 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies', 'etag': '"policy-v2"'})
    result['properties']['provisioningState'] = 'Succeeded'
    return result


def ready_gateway(desired):
    result = copy.deepcopy(desired)
    result.update({'id': GATEWAY_ID, 'name': 'agw-test-fixture', 'type': 'Microsoft.Network/applicationGateways', 'etag': '"gateway-v2"'})
    result['properties']['provisioningState'] = 'Succeeded'
    return result


def api_mapping(body):
    return next(item['properties'] for item in body['properties']['urlPathMaps'] if item['name'] == 'pathmap-protocols')


class GatewayCallbackWafTests(unittest.TestCase):
    def test_basic_route_keeps_host_portal_and_trusted_rewrite_while_only_adding_callback(self):
        source = gateway()
        snapshot = copy.deepcopy(source)
        updated = _callback_gateway(CONFIG, source, GATEWAY_ID, POLICY_ID)
        self.assertEqual(source, snapshot)
        route = updated['properties']['requestRoutingRules'][0]['properties']
        self.assertEqual(route['ruleType'], 'PathBasedRouting')
        self.assertNotIn('rewriteRuleSet', route)
        self.assertNotIn('backendHttpSettings', route)
        mapping = api_mapping(updated)
        self.assertEqual(mapping['defaultRewriteRuleSet'], REWRITE)
        self.assertEqual(mapping['defaultBackendAddressPool'], POOL)
        self.assertEqual(mapping['defaultBackendHttpSettings'], BACKEND)
        self.assertEqual(mapping['pathRules'][0]['properties'], {'paths': [CALLBACK_PATH], 'firewallPolicy': {'id': POLICY_ID},
            'backendAddressPool': POOL, 'backendHttpSettings': BACKEND, 'rewriteRuleSet': REWRITE})
        for key in ('backendHttpSettingsCollection', 'backendAddressPools', 'httpListeners', 'rewriteRuleSets'):
            self.assertEqual(updated['properties'][key], source['properties'][key])
        self.assertEqual(updated['properties']['urlPathMaps'][0], source['properties']['urlPathMaps'][0])
        self.assertEqual(updated['properties']['requestRoutingRules'][1], source['properties']['requestRoutingRules'][1])

    def test_existing_api_map_and_its_other_rules_are_kept_with_exact_callback_first(self):
        source = gateway(path_based=True)
        existing = copy.deepcopy(api_mapping(source))
        updated = _callback_gateway(CONFIG, source, GATEWAY_ID, POLICY_ID)
        actual = api_mapping(updated)
        self.assertEqual(actual['pathRules'][1:], existing['pathRules'])
        self.assertEqual(actual['pathRules'][0]['name'], CALLBACK_RULE_NAME)
        self.assertEqual(actual['pathRules'][0]['properties']['paths'], [CALLBACK_PATH])
        actual['pathRules'] = actual['pathRules'][1:]
        self.assertEqual(actual, existing)

    def test_clones_all_common_protection_and_excludes_only_code_from_the_three_rules(self):
        base = policy()
        desired = _callback_policy(base)
        expected = _writable_policy(base)
        exclusion = desired['properties']['managedRules']['exclusions'].pop()
        self.assertEqual(desired, expected)
        self.assertEqual(exclusion, {'matchVariable': 'RequestArgValues', 'selectorMatchOperator': 'Equals', 'selector': 'code',
            'exclusionManagedRuleSets': [{'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2', 'ruleGroups': [
                {'ruleGroupName': 'REQUEST-942-APPLICATION-ATTACK-SQLI', 'rules': [
                    {'ruleId': '942430'}, {'ruleId': '942440'}, {'ruleId': '942450'}]}]}]})

    def test_create_then_repeated_execution_is_idempotent_and_uses_etags(self):
        original, base = gateway(), policy()
        desired_gateway = ready_gateway(_callback_gateway(CONFIG, original, GATEWAY_ID, POLICY_ID))
        desired_policy = ready_policy(_callback_policy(base))
        transport = Transport([{'statusCode': 200, 'body': original}, {'statusCode': 200, 'body': base},
            {'statusCode': 404}, {'statusCode': 202}, {'statusCode': 200, 'body': desired_policy},
            {'statusCode': 202}, {'statusCode': 200, 'body': desired_gateway}])
        receipt = configure_gateway_identity_callback_waf(CONFIG, transport, sleep=lambda _: None)
        self.assertTrue(receipt['policyUpdated'])
        self.assertTrue(receipt['gatewayUpdated'])
        writes = [call for call in transport.calls if call['method'] == 'PUT']
        self.assertEqual(writes[0]['headers'], {'If-None-Match': '*'})
        self.assertEqual(writes[1]['headers'], {'If-Match': '"gateway-v1"'})
        again = Transport([{'statusCode': 200, 'body': desired_gateway}, {'statusCode': 200, 'body': base},
                           {'statusCode': 200, 'body': desired_policy}])
        receipt = configure_gateway_identity_callback_waf(CONFIG, again)
        self.assertFalse(receipt['policyUpdated'])
        self.assertFalse(receipt['gatewayUpdated'])
        self.assertEqual([call['method'] for call in again.calls], ['GET', 'GET', 'GET'])

    def test_update_existing_callback_policy_uses_its_etag_without_rewriting_the_gateway(self):
        current_gateway = ready_gateway(_callback_gateway(CONFIG, gateway(), GATEWAY_ID, POLICY_ID))
        old_policy = ready_policy(_callback_policy(policy()))
        old_policy['properties']['managedRules']['exclusions'][-1]['exclusionManagedRuleSets'][0]['ruleGroups'][0]['rules'].pop()
        transport = Transport([{'statusCode': 200, 'body': current_gateway}, {'statusCode': 200, 'body': policy()},
            {'statusCode': 200, 'body': old_policy}, {'statusCode': 202},
            {'statusCode': 200, 'body': ready_policy(_callback_policy(policy()))}])
        receipt = configure_gateway_identity_callback_waf(CONFIG, transport, sleep=lambda _: None)
        self.assertFalse(receipt['gatewayUpdated'])
        writes = [call for call in transport.calls if call['method'] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['headers'], {'If-Match': '"policy-v2"'})

    def test_foreign_resource_or_route_is_rejected_before_any_write(self):
        for case in ('id', 'policy', 'host', 'listener', 'pool', 'backend', 'backend-host', 'map', 'rewrite'):
            current = gateway()
            props = current['properties']
            route = props['requestRoutingRules'][0]['properties']
            if case == 'id': current['id'] = '/other/gateway'
            elif case == 'policy': props['firewallPolicy']['id'] = '/external/policy'
            elif case == 'host': props['httpListeners'][0]['properties']['hostName'] = 'another.example'
            elif case == 'listener': route['httpListener']['id'] = '/other/listener'
            elif case == 'pool': route['backendAddressPool']['id'] = '/other/pool'
            elif case == 'backend': route['backendHttpSettings']['id'] = '/other/backend'
            elif case == 'backend-host': props['backendHttpSettingsCollection'][0]['properties']['hostName'] = 'another.azure-api.net'
            elif case == 'rewrite': route['rewriteRuleSet']['id'] = '/other/rewrite'
            else:
                route['ruleType'] = 'PathBasedRouting'
                route['urlPathMap'] = {'id': '/other/map'}
            transport = Transport([{'statusCode': 200, 'body': current}])
            with self.subTest(case=case), self.assertRaises(ValueError):
                configure_gateway_identity_callback_waf(CONFIG, transport)
            self.assertEqual([call['method'] for call in transport.calls], ['GET'])

    def test_missing_etag_and_concurrent_update_stop_before_routing_write(self):
        current = gateway()
        current.pop('etag')
        transport = Transport([{'statusCode': 200, 'body': current}, {'statusCode': 200, 'body': policy()}, {'statusCode': 404}])
        with self.assertRaisesRegex(ValueError, 'ETag'):
            configure_gateway_identity_callback_waf(CONFIG, transport)
        self.assertFalse(any(call['method'] == 'PUT' for call in transport.calls))
        transport = Transport([{'statusCode': 200, 'body': gateway()}, {'statusCode': 200, 'body': policy()},
                               {'statusCode': 404}, {'statusCode': 412}])
        with self.assertRaisesRegex(ValueError, 'HTTP 412'):
            configure_gateway_identity_callback_waf(CONFIG, transport)
        self.assertEqual(len([call for call in transport.calls if call['method'] == 'PUT']), 1)

    def test_owned_name_does_not_authorize_an_external_policy_or_another_purpose(self):
        config = copy.deepcopy(CONFIG)
        config['gateway']['existingWafPolicyId'] = BASE_ID
        transport = Transport([])
        with self.assertRaisesRegex(ValueError, 'external WAF'):
            configure_gateway_identity_callback_waf(config, transport)
        self.assertEqual(transport.calls, [])
        with self.assertRaisesRegex(ValueError, 'owned purpose'):
            _callback_gateway(CONFIG, gateway(), GATEWAY_ID, policy_id(CONFIG, 'g-speech'))
        current = ready_gateway(_callback_gateway(CONFIG, gateway(), GATEWAY_ID, POLICY_ID))
        selected = next(item for item in api_mapping(current)['pathRules'] if item['name'] == CALLBACK_RULE_NAME)
        selected['properties']['firewallPolicy'] = {'id': '/enterprise/callback-policy'}
        transport = Transport([{'statusCode': 200, 'body': current}])
        with self.assertRaisesRegex(ValueError, 'another WAF policy'):
            configure_gateway_identity_callback_waf(CONFIG, transport)
        self.assertFalse(any(call['method'] == 'PUT' for call in transport.calls))

    def test_failed_async_update_is_not_reported_as_success(self):
        failed = ready_policy(_callback_policy(policy()))
        failed['properties']['provisioningState'] = 'Failed'
        transport = Transport([{'statusCode': 200, 'body': gateway()}, {'statusCode': 200, 'body': policy()},
            {'statusCode': 404}, {'statusCode': 202}, {'statusCode': 200, 'body': failed}])
        with self.assertRaisesRegex(ValueError, 'did not succeed'):
            configure_gateway_identity_callback_waf(CONFIG, transport)
        self.assertEqual(len([call for call in transport.calls if call['method'] == 'PUT']), 1)

    def test_standard_gateway_has_no_waf_to_modify(self):
        current = gateway()
        current['properties']['sku']['name'] = 'Standard_v2'
        transport = Transport([{'statusCode': 200, 'body': current}])
        self.assertFalse(configure_gateway_identity_callback_waf(CONFIG, transport)['required'])
        self.assertEqual([call['method'] for call in transport.calls], ['GET'])


if __name__ == '__main__':
    unittest.main()
