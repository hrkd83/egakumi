"""Portal configuration must preserve network and certificate boundaries."""
import unittest
from configuration_fixtures import configuration, fixture, CORE

class PortalConfigurationTests(unittest.TestCase):
    def valid(self):
        raw = fixture()
        raw['portal'] = {'publicHostName': 'portal.example.com'}
        raw['certificate']['domainNames'] = ['gateway.example.com', 'portal.example.com']
        return raw

    def validate(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "config")

    def test_required_portal(self):
        self.assertEqual(self.validate(self.valid())['portal']['publicHostName'], 'portal.example.com')

    def test_portal_cannot_be_omitted_or_disabled(self):
        raw = self.valid()
        del raw['portal']
        with self.assertRaises(configuration.ConfigurationError):
            self.validate(raw)
        for value in (False, True):
            raw = self.valid()
            raw['portal']['enabled'] = value
            with self.assertRaises(configuration.ConfigurationError):
                self.validate(raw)

    def test_invalid_network_or_hostname_is_rejected(self):
        for value in ('10.40.2.128/26', '10.99.0.0/26', '10.40.2.192/28'):
            with self.subTest(value=value):
                raw = self.valid()
                raw['network']['subnetPrefixes']['aca'] = value
                with self.assertRaisesRegex(configuration.ConfigurationError, 'network.subnetPrefixes'):
                    self.validate(raw)
        for value in ('gateway.example.com', 'console.example.com'):
            with self.subTest(host=value):
                raw = self.valid()
                raw['portal']['publicHostName'] = value
                with self.assertRaisesRegex(configuration.ConfigurationError, 'portal.publicHostName'):
                    self.validate(raw)


    def test_aca_prefix_is_required_in_network(self):
        raw = self.valid()
        del raw['network']['subnetPrefixes']['aca']
        with self.assertRaisesRegex(configuration.ConfigurationError, 'network.subnetPrefixes'):
            self.validate(raw)

    def test_aca_accepts_the_minimum_subnet_size(self):
        raw = self.valid()
        raw['network']['subnetPrefixes']['aca'] = '10.40.2.192/27'
        self.assertEqual(self.validate(raw)['network']['subnetPrefixes']['aca'], '10.40.2.192/27')

    def test_portal_cannot_override_the_network_prefix(self):
        raw = self.valid()
        raw['portal']['subnetPrefix'] = '10.40.3.0/26'
        with self.assertRaisesRegex(configuration.ConfigurationError, 'portal.*unsupported'):
            self.validate(raw)

    def test_certificate_must_cover_portal(self):
        raw = self.valid()
        raw['certificate']['domainNames'] = ['gateway.example.com']
        with self.assertRaises(configuration.ConfigurationError):
            self.validate(raw)

    def test_wildcard_certificate(self):
        raw = self.valid()
        raw['certificate']['domainNames'] = ['*.example.com']
        self.assertEqual(self.validate(raw)['portal']['publicHostName'], 'portal.example.com')
