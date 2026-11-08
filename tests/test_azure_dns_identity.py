"""Azure DNSの公開ID入力を、秘密ファイルやAzureへ接続せず検査する。"""
import sys
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import configuration

SUBSCRIPTION = '11111111-1111-4111-8111-111111111111'
IDENTITY = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns'
CORE = Path(__file__).resolve().parents[1]


class AzureDnsIdentityTests(unittest.TestCase):
    def run_certificate_fixture(self, name, expected):
        runtime = os.environ.get('POWERSHELL_RUNTIME') or shutil.which('pwsh')
        if not runtime:
            candidates = sorted((CORE / '.tools/powershell').glob('*/pwsh'))
            runtime = str(candidates[-1]) if candidates else None
        if not runtime:
            self.skipTest('PowerShell runtime unavailable')
        result = subprocess.run([runtime, '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-File', str(CORE / 'tests' / name)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)
        self.assertNotIn('fixture-token-', result.stdout + result.stderr)

    def test_certificate_transport_rejects_direct_arm_and_foreign_vault_requests(self):
        self.run_certificate_fixture('certificate-network-boundary-offline.ps1', 'Certificate transport boundary PASS')

    def test_certificate_issuance_and_renewal_use_the_selected_dns_identity(self):
        self.run_certificate_fixture('certificate-azure-identity-offline.ps1', 'Azure DNS identity:')

    def test_portal_lowercase_resourcegroups_reference_is_accepted(self):
        identity = IDENTITY.replace('/resourceGroups/', '/resourcegroups/')
        configuration.validate_azure_dns_identity({'azureDnsSubscriptionId': SUBSCRIPTION,
                                                   'azureDnsManagedIdentityResourceId': identity})
        root = Path(__file__).resolve().parents[1]
        for name in ('params', 'configuration'):
            schema = json.loads((root / 'schemas' / (name + '.schema.json')).read_text(encoding='utf-8'))
            pattern = schema['properties']['certificate']['properties']['azureDnsManagedIdentityResourceId']['pattern']
            self.assertIsNotNone(re.fullmatch(pattern, identity))

    def test_invalid_or_incomplete_reference_is_rejected_without_echoing_input(self):
        for identity in ('private-input-marker', SUBSCRIPTION, IDENTITY.replace('userAssignedIdentities', 'dnsZones'),
                         IDENTITY.replace(SUBSCRIPTION, '00000000-0000-0000-0000-000000000000')):
            with self.subTest(identity=identity), self.assertRaises(configuration.ConfigurationError) as error:
                configuration.validate_azure_dns_identity({'azureDnsSubscriptionId': SUBSCRIPTION,
                                                          'azureDnsManagedIdentityResourceId': identity})
            self.assertNotIn(identity, str(error.exception))

    def test_dns_and_identity_subscriptions_can_differ(self):
        configuration.validate_azure_dns_identity({'azureDnsSubscriptionId': '22222222-2222-4222-8222-222222222222',
                                                   'azureDnsManagedIdentityResourceId': IDENTITY})

    def test_azure_deployment_rejects_old_sp_credentials_before_any_file_read(self):
        config = {'management': {'sshPublicKeyFile': 'fixture'}, 'credentialProviders': [], 'apiDefinitions': [],
                  'certificate': {'mode': 'dnsPlugin', 'plugin': 'Azure', 'pluginArgumentsFile': '/must/not/read',
                                  'azureDnsSubscriptionId': SUBSCRIPTION, 'azureDnsManagedIdentityResourceId': IDENTITY}}
        with mock.patch.object(configuration, 'load_ssh_public_key', side_effect=AssertionError('SSH key read')) as ssh, \
             mock.patch.object(configuration, '_read_protected_file', side_effect=AssertionError('legacy credentials read')) as secret:
            with self.assertRaises(configuration.ConfigurationError):
                configuration.load_secret_inputs(config)
        ssh.assert_not_called()
        secret.assert_not_called()

    def test_noninteractive_deployment_requires_both_references(self):
        config = {'management': {'sshPublicKeyFile': 'fixture'}, 'credentialProviders': [], 'apiDefinitions': [],
                  'certificate': {'mode': 'dnsPlugin', 'plugin': 'Azure'}}
        with mock.patch.object(configuration, 'load_ssh_public_key', side_effect=AssertionError('SSH key read')) as ssh:
            with self.assertRaises(configuration.ConfigurationError):
                configuration.load_secret_inputs(config)
        ssh.assert_not_called()
