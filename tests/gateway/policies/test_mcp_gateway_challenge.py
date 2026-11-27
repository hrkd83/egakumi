"""AppGW keeps API responses and rewrites only the trusted ingress request header."""
import unittest

import tests.waf.test_ssml_waf as waf


class McpGatewayChallengeTests(unittest.TestCase):
    """Check compiled HTTP routes and the permanent L7 ingress proof."""

    @classmethod
    def setUpClass(cls):
        waf.SsmlWafTests.setUpClass()
        cls.template, cls.resources = waf.SsmlWafTests.template, waf.SsmlWafTests.resources
        cls.gateway = next(item for item in cls.resources if item['type'] == 'Microsoft.Network/applicationGateways')

    def parameters(self, **changes):
        return waf.SsmlWafTests.parameters(self, **changes)

    def properties(self, **changes):
        parameters = self.parameters(**changes)
        return waf.GatewayExpression('[true()]', self.template['variables']).resolve(self.gateway['properties'], parameters)

    def test_gateway_only_rewrites_trusted_ingress_request_header(self):
        properties = self.properties()
        rules = [rule for group in properties.get('rewriteRuleSets', [])
                 for rule in group['properties']['rewriteRules']]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['name'], 'replace-untrusted-edge-proof')
        self.assertEqual(rules[0]['actionSet'], {'requestHeaderConfigurations': [
            {'headerName': 'X-Core-L7-Ingress', 'headerValue': 'SYNTHETIC_PRIVATE_INPUT'}]})
        self.assertTrue(properties['sslCertificates'])
        self.assertTrue(properties['requestRoutingRules'])
        self.assertTrue(properties['backendAddressPools'])


if __name__ == '__main__':
    unittest.main()
