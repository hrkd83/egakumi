"""AppGW keeps API responses and rewrites only the trusted ingress request header."""
import unittest

import test_ssml_waf as waf


class McpGatewayChallengeTests(unittest.TestCase):
    """Check compiled HTTP/TCP routes and the L7 ingress proof."""

    @classmethod
    def setUpClass(cls):
        waf.SsmlWafTests.setUpClass()
        cls.main, cls.module = waf.SsmlWafTests.main, waf.SsmlWafTests.module
        cls.template, cls.resources = waf.SsmlWafTests.template, waf.SsmlWafTests.resources
        cls.gateway = next(item for item in cls.resources if item['type'] == 'Microsoft.Network/applicationGateways')

    def parameters(self, **changes):
        return waf.SsmlWafTests.parameters(self, **changes)

    def properties(self, **changes):
        parameters = self.parameters(**changes)
        return waf.GatewayExpression('[true()]', self.template['variables']).resolve(self.gateway['properties'], parameters)

    def test_oauth_rewrite_configuration_is_absent_from_both_templates(self):
        for template in (self.main, self.template):
            self.assertNotIn('mcpOAuthChallenges', template['parameters'])
            self.assertNotIn('mcpOAuthChallenges', template['variables'])

    def test_gateway_only_rewrites_trusted_ingress_request_header(self):
        for enabled in (False, True):
            with self.subTest(grpc=enabled):
                properties = self.properties(grpcEnabled=enabled)
                rules = [rule for group in properties.get('rewriteRuleSets', [])
                         for rule in group['properties']['rewriteRules']]
                self.assertEqual(len(rules), 1 if enabled else 0)
                for rule in rules:
                    self.assertEqual(rule['name'], 'replace-untrusted-edge-proof')
                    self.assertEqual(rule['actionSet'], {'requestHeaderConfigurations': [
                        {'headerName': 'X-Core-L7-Ingress', 'headerValue': 'SYNTHETIC_PRIVATE_INPUT'}]})
                self.assertTrue(properties['sslCertificates'])
                self.assertTrue(properties['requestRoutingRules'])
                self.assertTrue(properties['backendAddressPools'])


if __name__ == '__main__':
    unittest.main()
