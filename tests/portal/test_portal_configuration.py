"""Portal configuration must preserve network and certificate boundaries."""
from ipaddress import ip_network
import unittest
from tests.support.configuration_fixtures import configuration, fixture, CORE

class PortalConfigurationTests(unittest.TestCase):
    def valid(self):
        raw = fixture()
        raw['portal'] = {'publicHostName': 'portal.example.com'}
        raw['certificate']['domainNames'] = ['gateway.example.com', 'portal.example.com']
        return raw

    def validate(self, raw):
        return configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")

    def test_portal_hostname_is_loaded(self):
        self.assertEqual(self.validate(self.valid())['portal']['publicHostName'], 'portal.example.com')

    def test_portal_configuration_is_required(self):
        raw = self.valid()
        del raw['portal']
        with self.assertRaises(configuration.ConfigurationError):
            self.validate(raw)

    def test_invalid_network_or_hostname_is_rejected(self):
        management = self.valid()['network']['subnetPrefixes']['management']
        for value in (management, '10.99.0.0/26', '10.40.2.192/28'):
            with self.subTest(value=value):
                raw = self.valid()
                raw['network']['subnetPrefixes']['portal'] = value
                with self.assertRaisesRegex(configuration.ConfigurationError, 'network.subnetPrefixes'):
                    self.validate(raw)
        for value in ('gateway.example.com', 'console.example.com'):
            with self.subTest(host=value):
                raw = self.valid()
                raw['portal']['publicHostName'] = value
                with self.assertRaisesRegex(configuration.ConfigurationError, 'portal.publicHostName'):
                    self.validate(raw)


    def test_portal_prefix_is_required_in_network(self):
        raw = self.valid()
        del raw['network']['subnetPrefixes']['portal']
        with self.assertRaisesRegex(configuration.ConfigurationError, 'network.subnetPrefixes'):
            self.validate(raw)

    def test_portal_accepts_the_minimum_subnet_size(self):
        raw = self.valid()
        prefixes = raw['network']['subnetPrefixes']
        minimum = str(next(ip_network(prefixes['portal']).subnets(new_prefix=27)))
        prefixes['portal'] = minimum
        self.assertEqual(self.validate(raw)['network']['subnetPrefixes']['portal'], minimum)

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

    def test_certificate_names_keep_automation_out_of_other_vault_objects(self):
        for public_name, portal_name in (("TLS-PUBLIC", "tls-public"), ("gateway-obo", "portal-auth"),
                                         ("core-l7-ingress-secret", "portal-auth"), ("tls-public", "tls-public-acme-state")):
            with self.subTest(public_name=public_name, portal_name=portal_name):
                raw = self.valid()
                raw['certificate']['certificateName'] = public_name
                raw['portal']['certificate'] = {'certificateName': portal_name}
                with self.assertRaisesRegex(configuration.ConfigurationError, 'certificateName'):
                    self.validate(raw)

    def test_wildcard_certificate(self):
        raw = self.valid()
        raw['certificate']['domainNames'] = ['*.example.com']
        self.assertEqual(self.validate(raw)['portal']['publicHostName'], 'portal.example.com')
