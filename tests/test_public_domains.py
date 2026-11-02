"""GatewayのFQDNと証明書対象を、Azure作成前に同じ設定から検査する。"""
import unittest

from configuration_fixtures import configuration, fixture


class PublicDomainTests(unittest.TestCase):
    """ワイルドカードの一階層制約を守り、別システムの証明書を要求しない。"""

    def test_omitted_certificate_names_cover_gateway_and_portal(self):
        config = configuration.normalize_configuration(fixture())
        self.assertEqual(config['certificate']['domainNames'], ['gateway.example.com', 'portal.example.com'])
        self.assertNotIn('managementApplication', config)

    def test_wildcard_covers_gateway_and_keeps_requested_san(self):
        raw = fixture()
        raw['certificate']['domainNames'] = ['*.EXAMPLE.com']
        config = configuration.normalize_configuration(raw)
        self.assertEqual(config['certificate']['domainNames'], ['*.example.com'])
        allowed = config['network']['firewall']['managementAllowedFqdns']
        self.assertIn('gateway.example.com', allowed)
        self.assertIn('portal.example.com', allowed)

    def test_certificate_must_cover_gateway_without_apex_or_deeper_wildcard_expansion(self):
        for api_host, names in (
            ('example.com', ['*.example.com']),
            ('api.region.example.com', ['*.example.com']),
            ('gateway.other.example', ['*.example.com']),
        ):
            with self.subTest(api=api_host):
                raw = fixture()
                raw['gateway']['publicHostName'] = api_host
                raw['gateway']['grpc']['publicHostName'] = api_host
                raw['certificate']['domainNames'] = names
                with self.assertRaisesRegex(configuration.ConfigurationError, 'certificate.domainNames'):
                    configuration.normalize_configuration(raw)

    def test_gateway_host_and_certificate_names_are_valid(self):
        for host in (None, 'https://gateway.example.com', '*.example.com', '192.0.2.10'):
            raw = fixture()
            raw['gateway']['publicHostName'] = host
            with self.subTest(host=host), self.assertRaisesRegex(
                    configuration.ConfigurationError, 'gateway.publicHostName'):
                configuration.normalize_configuration(raw)
        for names in ([], ['*.*.example.com'], ['*.com'], ['https://gateway.example.com'],
                      ['*.example.com', '*.EXAMPLE.com'], ['*.192.0.2.10']):
            raw = fixture()
            raw['certificate']['domainNames'] = names
            with self.subTest(names=names), self.assertRaisesRegex(
                    configuration.ConfigurationError, 'certificate.domainNames'):
                configuration.normalize_configuration(raw)


if __name__ == '__main__':
    unittest.main()
