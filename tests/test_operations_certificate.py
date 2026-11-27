"""Existing certificate maintenance rejects uncertain work and accepts only observed public TLS."""
import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from operations import certificate as cert
from operations.common import OperationError

SUB = '11111111-1111-1111-1111-111111111111'
PREFIX = f'/subscriptions/{SUB}/resourceGroups/'
GATEWAY = PREFIX + 'rg-test-nw/providers/Microsoft.Network/applicationGateways/agw-test-fixture'
APIM = PREFIX + 'rg-test-svc/providers/Microsoft.ApiManagement/service/apim-test-fixture-abc123'
TLS_VAULT_NAME = 'kvtls-test-fixture'
TLS_VAULT = PREFIX + 'rg-test-svc/providers/Microsoft.KeyVault/vaults/' + TLS_VAULT_NAME
CREDENTIAL_VAULT = PREFIX + 'rg-test-svc/providers/Microsoft.KeyVault/vaults/kv-test-fixture'
TLS_VAULT_HOST = f'{TLS_VAULT_NAME}.vault.azure.net'
TLS_VAULT_URI = f'https://{TLS_VAULT_HOST}'
THUMB = 'A' * 40


class Context:
    def __init__(self):
        self.config = {'subscriptionId': SUB, 'resourceStem': 'test-fixture', 'location': 'japaneast',
                       'productName': 'fixture', 'environmentName': 'test',
                       'resourceGroupNames': {'maint': 'rg-test-maint', 'nw': 'rg-test-nw', 'svc': 'rg-test-svc'},
                       'certificate': {'certificateName': 'public-gateway'},
                       'gateway': {'publicHostName': 'gateway.example.com'},
                       'portal': {'publicHostName': 'portal.example.com'}}
        self.state = {'outputs': {'gateway': {'id': GATEWAY}},
                      'serviceResourceIds': {'certificateVault': TLS_VAULT, 'keyVault': CREDENTIAL_VAULT}}
        self.calls, self.records = [], []
        self.transport = mock.Mock()
        self.gateway = {'id': GATEWAY, 'etag': '"etag-1"', 'tags': {'existing': 'keep'},
                        'properties': {'provisioningState': 'Succeeded', 'sslCertificates': [
                            {'name': 'gateway-tls', 'properties': {'keyVaultSecretId': TLS_VAULT_URI + '/secrets/public-gateway/'}}],
                            'httpListeners': [{'name': n, 'properties': {'protocol': 'Https', 'hostName': h,
                                'sslCertificate': {'id': GATEWAY + '/sslCertificates/gateway-tls'}}}
                                for n, h in [('listener-https', 'gateway.example.com'), ('portal-https', 'portal.example.com')]]}}
        self.apim = {'id': APIM, 'properties': {'hostnameConfigurations': [
            {'type': 'Proxy', 'hostName': 'gateway.example.com', 'keyVaultId': TLS_VAULT_URI + '/secrets/public-gateway'}]}}
        self.patch_status = 200
        self.use_certificate_job()

    def use_certificate_job(self):
        bootstrap = {'certificateVaultId': TLS_VAULT, 'certificateVaultName': TLS_VAULT_NAME,
                     'certificateSecretUri': TLS_VAULT_URI + '/secrets/public-gateway/'}
        self.state['outputs']['certificateBootstrap'] = bootstrap
        return bootstrap

    def service_id(self, name):
        if name != 'certificateVault':
            raise AssertionError('Certificate operations must select only the public TLS vault')
        try:
            return self.state['serviceResourceIds'][name]
        except KeyError:
            raise OperationError('The service is missing from Core state') from None

    def record(self, kind, value):
        self.records.append((kind, value))

    def verify_resource(self, resource_id, api, expected_type=None):
        return copy.deepcopy(self.gateway if resource_id == GATEWAY else self.apim)

    def request(self, method, resource_id, api, body=None, headers=None):
        self.calls.append((method, resource_id, api, copy.deepcopy(body), headers))
        if method == 'PATCH':
            if self.patch_status not in (200, 201, 202):
                return {'statusCode': self.patch_status}
            self.gateway['tags'] = body['tags']
            self.gateway['etag'] = '"etag-updated"'
            return {'statusCode': 200}
        return {'statusCode': 200, 'body': copy.deepcopy(self.gateway)}


def metadata():
    return {'certificateId': TLS_VAULT_URI + '/certificates/public-gateway/' + 'a' * 32,
            'notBefore': int(time.time()) - 86400, 'notAfter': int(time.time()) + 86400, 'thumbprint': THUMB}


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context()

    def test_plan_has_no_arm_or_public_network_activity(self):
        with mock.patch.object(cert, 'job_metadata') as job, mock.patch.object(cert, 'tls_leaf') as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=False, thumbprint=None))
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['certificateConsumer'], 'application-gateway')
        self.assertEqual(result['execution'], 'container-app-job')
        self.assertEqual(self.ctx.calls, [])
        job.assert_not_called()
        tls.assert_not_called()

    def test_foreign_output_and_invalid_thumbprint_stop_before_writes(self):
        for foreign_gateway, thumbprint in ((True, None), (False, 'not-a-thumbprint')):
            ctx = Context()
            if foreign_gateway:
                ctx.state['outputs']['gateway']['id'] += '-foreign'
            with self.subTest(foreign_gateway=foreign_gateway), mock.patch.object(ctx, 'verify_resource') as verify, \
                    self.assertRaises(OperationError):
                cert.operate(ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=thumbprint))
            verify.assert_not_called()
            self.assertEqual(ctx.calls, [])

    def test_credential_vault_state_is_rejected_before_any_operations(self):
        self.ctx.state['serviceResourceIds']['certificateVault'] = CREDENTIAL_VAULT
        with self.assertRaisesRegex(OperationError, 'recorded public TLS vault differs'):
            cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=None))
        self.assertEqual(self.ctx.calls, [])

    def test_bootstrap_mismatch_never_starts_a_certificate_job(self):
        for field, unsafe_value in [('certificateVaultId', CREDENTIAL_VAULT),
                                    ('certificateVaultName', 'kv-test-fixture'),
                                    ('certificateSecretUri', TLS_VAULT_URI + '/secrets/public-gateway/' + 'a' * 32)]:
            ctx = Context()
            ctx.use_certificate_job()[field] = unsafe_value
            with self.subTest(field=field), mock.patch.object(cert, 'job_metadata') as job, \
                    mock.patch.object(ctx, 'verify_resource') as verify, \
                    self.assertRaisesRegex(OperationError, 'recorded certificate job'):
                cert.operate(ctx, SimpleNamespace(stage='check', execute=True, thumbprint=None))
            job.assert_not_called()
            verify.assert_not_called()
            self.assertEqual(ctx.calls, [])

    def test_record_without_dedicated_vault_cannot_start_a_job(self):
        self.ctx.state['serviceResourceIds'].pop('certificateVault')
        with mock.patch.object(cert, 'job_metadata') as job, self.assertRaises(OperationError):
            cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=None))
        job.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

    def test_certificate_job_checks_renews_and_synchronizes_owned_public_leaves(self):
        for stage in ('check', 'renew', 'sync'):
            ctx = Context()
            changed = {**metadata(), 'thumbprint': 'B' * 40}
            sequence = [metadata(), changed] if stage == 'renew' else [metadata()]
            thumbprint = sequence[-1]['thumbprint']
            with self.subTest(stage=stage), mock.patch.object(cert, 'job_metadata', side_effect=sequence) as job, \
                    mock.patch.object(ctx, 'verify_resource', wraps=ctx.verify_resource) as verify, \
                    mock.patch.object(cert, 'tls_leaf', side_effect=lambda host, port: {
                        'host': host, 'port': port, 'thumbprint': thumbprint, 'trustedAndHostnameVerified': True,
                    }) as tls:
                args = SimpleNamespace(stage=stage, execute=True, thumbprint=THUMB if stage == 'renew' else None)
                result = cert.operate(ctx, args)
            self.assertEqual(result['status'], 'public-tls-verified')
            expected = [mock.call(ctx)]
            if stage == 'renew':
                expected.append(mock.call(ctx, renew=True, thumbprint=THUMB))
            self.assertEqual(job.call_args_list, expected)
            self.assertIn(mock.call(GATEWAY, cert.NETWORK_API, 'Microsoft.Network/applicationGateways'), verify.call_args_list)
            self.assertTrue(all(call.args[0] == GATEWAY for call in verify.call_args_list))
            self.assertEqual(tls.call_args_list, [mock.call('gateway.example.com', 443), mock.call('portal.example.com', 443)])
            self.assertEqual([call[0] for call in ctx.calls].count('PATCH'), 0 if stage == 'check' else 2)

    def test_existing_version_pinned_reference_is_not_silently_rewritten(self):
        self.ctx.gateway['properties']['sslCertificates'][0]['properties']['keyVaultSecretId'] += 'a' * 32
        with self.assertRaises(OperationError):
            cert.verify_references(self.ctx, GATEWAY, TLS_VAULT_NAME, 'public-gateway')
        self.assertEqual(self.ctx.calls, [])

    def test_public_hosts_must_match_the_owned_certificate_listeners(self):
        self.ctx.gateway['properties']['httpListeners'][0]['properties']['hostName'] = 'other.example.com'
        with self.assertRaises(OperationError):
            cert.verify_references(self.ctx, GATEWAY, TLS_VAULT_NAME, 'public-gateway')
        self.assertEqual(self.ctx.calls, [])

    def test_gateway_refresh_preserves_other_tags_and_restores_only_its_tag(self):
        cert.refresh_gateway(self.ctx, GATEWAY)
        patches = [call for call in self.ctx.calls if call[0] == 'PATCH']
        self.assertEqual(len(patches), 2)
        self.assertEqual(set(patches[0][3]), {'tags'})
        self.assertEqual(patches[0][3]['tags']['existing'], 'keep')
        self.assertEqual(patches[1][3], {'tags': {'existing': 'keep'}})
        self.assertEqual(patches[0][4], {'If-Match': '"etag-1"'})
        self.assertEqual(patches[1][4], {'If-Match': '"etag-updated"'})

    def test_gateway_conflict_or_missing_etag_does_not_overwrite(self):
        self.ctx.patch_status = 412
        with self.assertRaises(OperationError):
            cert.refresh_gateway(self.ctx, GATEWAY)
        self.assertEqual(len(self.ctx.calls), 1)
        self.ctx.calls.clear(); self.ctx.gateway.pop('etag')
        with self.assertRaises(OperationError):
            cert.refresh_gateway(self.ctx, GATEWAY)
        self.assertEqual(self.ctx.calls, [])

    def test_malformed_or_wildcard_gateway_etags_stop_before_patch(self):
        for value in ('', '""', '*', '"*"', 'W/*', 'W/"*"', '"one","two"', '"line\r\nbreak"', '" space "', 'x' * 513):
            with self.subTest(value=value):
                self.ctx.gateway['etag'] = value
                with self.assertRaises(OperationError):
                    cert.refresh_gateway(self.ctx, GATEWAY)
                self.assertEqual(self.ctx.calls, [])
        self.assertEqual(cert.etag({'etag': 'W/"opaque"'}), 'W/"opaque"')

    def test_explicit_rotation_requires_a_new_leaf_before_sync(self):
        with mock.patch.object(cert, 'job_metadata', side_effect=[metadata(), metadata()]), \
                mock.patch.object(cert, 'refresh_gateway') as refresh:
            with self.assertRaises(OperationError):
                cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        refresh.assert_not_called()
        self.assertFalse(any(call[0] == 'POST' for call in self.ctx.calls))

    def test_sync_uses_only_appgw_certificates_and_public_https_hosts(self):
        original_verify = self.ctx.verify_resource
        def gateway_only(resource_id, api, expected_type=None):
            self.assertNotEqual(resource_id, APIM)
            return original_verify(resource_id, api, expected_type)
        self.ctx.verify_resource = gateway_only
        with mock.patch.object(cert, 'job_metadata', return_value=metadata()) as job, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {
                    'host': h, 'port': p, 'thumbprint': THUMB,
                    'trustedAndHostnameVerified': True}) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        self.assertEqual(result['status'], 'public-tls-verified')
        job.assert_called_once_with(self.ctx)
        self.assertEqual(tls.call_args_list, [mock.call('gateway.example.com', 443), mock.call('portal.example.com', 443)])
        self.assertFalse(any(call[0] == 'POST' or call[1].startswith(APIM) for call in self.ctx.calls))
        self.assertEqual(len([call for call in self.ctx.calls if call[0] == 'PATCH']), 2)
        self.assertEqual(self.ctx.gateway['tags'], {'existing': 'keep'})
        self.assertFalse(any(kind.startswith('certificate-apim') for kind, _ in self.ctx.records))

    def test_gateway_snapshot_failure_stops_before_any_patch(self):
        with mock.patch.object(self.ctx, 'record', side_effect=OperationError('Synthetic record failure')):
            with self.assertRaises(OperationError):
                cert.refresh_gateway(self.ctx, GATEWAY)
        self.assertEqual(self.ctx.calls, [])

    def test_sync_retains_versionless_appgw_reference_validation(self):
        self.ctx.gateway['properties']['sslCertificates'][0]['properties']['keyVaultSecretId'] += 'a' * 32
        with mock.patch.object(cert, 'job_metadata') as job, self.assertRaises(OperationError):
            cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        job.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

    def test_sync_is_not_success_when_gateway_or_public_tls_work_fails(self):
        for stage in ('refresh_gateway', 'observe'):
            with self.subTest(stage=stage):
                ctx = Context()
                with mock.patch.object(cert, 'job_metadata', return_value=metadata()), \
                        mock.patch.object(cert, 'refresh_gateway') as gateway, \
                        mock.patch.object(cert, 'observe', return_value=[]) as observe:
                    {'refresh_gateway': gateway, 'observe': observe}[stage].side_effect = OperationError('Synthetic failure')
                    with self.assertRaises(OperationError):
                        cert.operate(ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
                self.assertFalse(any(kind == 'certificate-result' for kind, _ in ctx.records))
                self.assertFalse(any(call[0] == 'POST' for call in ctx.calls))

    def test_renew_requires_kv_and_every_public_https_leaf_to_match(self):
        changed = metadata(); changed['thumbprint'] = 'B' * 40
        with mock.patch.object(cert, 'job_metadata', side_effect=[metadata(), changed]), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {'host': h, 'port': p, 'thumbprint': 'B' * 40}) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        self.assertEqual(result['status'], 'public-tls-verified')
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        self.assertEqual(tls.call_count, 2)
        self.assertFalse(any(call[0] == 'POST' for call in self.ctx.calls))
        observed = self.ctx.records[-1][1]['publicTls']
        self.assertEqual({(v['host'], v['port']) for v in observed}, {('gateway.example.com', 443), ('portal.example.com', 443)})


if __name__ == '__main__':
    unittest.main()
