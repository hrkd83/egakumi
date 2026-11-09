"""Existing certificate maintenance rejects uncertain work and accepts only observed public TLS."""
import ast
import copy
import json
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
VM = PREFIX + 'rg-test-maint/providers/Microsoft.Compute/virtualMachines/vm-test-fixture-mgmt'
GATEWAY = PREFIX + 'rg-test-nw/providers/Microsoft.Network/applicationGateways/agw-test-fixture'
APIM = PREFIX + 'rg-test-svc/providers/Microsoft.ApiManagement/service/apim-test-fixture-abc123'
THUMB = 'A' * 40


class Context:
    def __init__(self):
        self.config = {'subscriptionId': SUB, 'resourceStem': 'test-fixture', 'location': 'japaneast',
                       'productName': 'fixture', 'environmentName': 'test',
                       'resourceGroupNames': {'maint': 'rg-test-maint', 'nw': 'rg-test-nw'},
                       'certificate': {'certificateName': 'public-gateway'},
                       'gateway': {'publicHostName': 'gateway.example.com',
                                   'grpc': {'enabled': True, 'publicHostName': 'gateway.example.com', 'publicPort': 8443}},
                       'portal': {'publicHostName': 'portal.example.com'}}
        self.state = {'outputs': {'managementVm': {'id': VM}, 'gateway': {'id': GATEWAY}}}
        self.calls, self.records = [], []
        self.transport = mock.Mock()
        self.gateway = {'id': GATEWAY, 'etag': '"etag-1"', 'tags': {'existing': 'keep'},
                        'properties': {'provisioningState': 'Succeeded', 'sslCertificates': [
                            {'name': 'gateway-tls', 'properties': {'keyVaultSecretId': 'https://kv-test-fixture.vault.azure.net/secrets/public-gateway/'}}],
                            'httpListeners': [{'name': n, 'properties': {'protocol': 'Https', 'hostName': h,
                                'sslCertificate': {'id': GATEWAY + '/sslCertificates/gateway-tls'}}}
                                for n, h in [('listener-https', 'gateway.example.com'), ('portal-https', 'portal.example.com')]]}}
        self.apim = {'id': APIM, 'properties': {'hostnameConfigurations': [
            {'type': 'Proxy', 'hostName': 'gateway.example.com', 'keyVaultId': 'https://kv-test-fixture.vault.azure.net/secrets/public-gateway'}]}}
        self.patch_status = 200

    def service_id(self, name):
        return APIM if name == 'apim' else PREFIX + 'rg-test-svc/providers/Microsoft.KeyVault/vaults/kv-test-fixture'

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
    return {'certificateId': 'https://kv-test-fixture.vault.azure.net/certificates/public-gateway/' + 'a' * 32,
            'secretVersionUri': 'https://kv-test-fixture.vault.azure.net/secrets/public-gateway/' + 'a' * 32,
            'enabled': True, 'expires': int(time.time()) + 86400, 'thumbprint': THUMB, 'timerEnabled': True}


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context()

    def test_plan_has_no_arm_vm_or_public_network_activity(self):
        with mock.patch.object(cert, 'run_worker') as worker, mock.patch.object(cert, 'tls_leaf') as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=False, thumbprint=None))
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['apimRefreshApi'], '2025-09-01-preview')
        self.assertEqual(self.ctx.calls, [])
        worker.assert_not_called()
        tls.assert_not_called()

    def test_foreign_output_and_invalid_thumbprint_stop_before_writes(self):
        self.ctx.state['outputs']['managementVm']['id'] += '-foreign'
        with self.assertRaises(OperationError):
            cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=None))
        self.assertEqual(self.ctx.calls, [])

    def test_remote_script_uses_existing_worker_and_never_reads_secret_endpoint(self):
        text = cert.remote_script('kv-test-fixture', 'public-gateway', renew=True, thumbprint=THUMB)
        self.assertIn('-Action Renew', text)
        self.assertNotIn('-Action Install', text)
        self.assertIn('-RotateFromThumbprint ' + THUMB, text)
        self.assertIn('sha256sum', text)
        self.assertIn('/certificates/public-gateway?', text)
        self.assertNotIn('/secrets/', text)
        self.assertNotIn('az login', text)
        program = cert.metadata_program('kv-test-fixture', 'public-gateway', 'unit')
        ast.parse(program)

    def test_only_current_public_metadata_is_accepted(self):
        raw = cert.MARKER + json.dumps(metadata())
        self.assertEqual(cert.public_metadata(raw, 'kv-test-fixture', 'public-gateway')['thumbprint'], THUMB)
        for field, value in [('enabled', False), ('timerEnabled', False), ('expires', 0),
                             ('certificateId', 'https://foreign.example.com/certificates/public-gateway/' + 'a' * 32)]:
            bad = metadata(); bad[field] = value
            with self.subTest(field=field), self.assertRaises(OperationError):
                cert.public_metadata(cert.MARKER + json.dumps(bad), 'kv-test-fixture', 'public-gateway')
        with self.assertRaises(OperationError):
            cert.public_metadata(raw + '\n' + raw, 'kv-test-fixture', 'public-gateway')

    def test_existing_version_pinned_reference_is_not_silently_rewritten(self):
        self.ctx.gateway['properties']['sslCertificates'][0]['properties']['keyVaultSecretId'] += 'a' * 32
        with self.assertRaises(OperationError):
            cert.verify_references(self.ctx, GATEWAY, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(self.ctx.calls, [])

    def test_public_hosts_must_match_the_owned_certificate_listeners(self):
        self.ctx.gateway['properties']['httpListeners'][0]['properties']['hostName'] = 'other.example.com'
        with self.assertRaises(OperationError):
            cert.verify_references(self.ctx, GATEWAY, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(self.ctx.calls, [])

    def test_lro_accepts_only_same_apim_or_provider_region_and_keeps_url_in_ram(self):
        url = APIM + '/operationresults/test?api-version=2025-09-01-preview&sig=SYNTHETIC_SIGNATURE'
        self.ctx.transport.request.return_value = {'statusCode': 200, 'body': {'status': 'Succeeded'}}
        cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': 'https://management.azure.com' + url}}, sleep=lambda _: None)
        self.ctx.transport.request.assert_called_once_with('GET', 'https://management.azure.com' + url)
        self.assertEqual(self.ctx.records, [])
        self.assertEqual(self.ctx.calls, [])

    def test_foreign_signed_lro_urls_are_rejected_before_get(self):
        paths = ['http://management.azure.com' + APIM + '/operations/x',
                 'https://management.azure.com.evil.invalid' + APIM + '/operations/x',
                 'https://management.azure.com' + APIM.replace(SUB, 'foreign') + '/operations/x',
                 f'https://management.azure.com/subscriptions/{SUB}/providers/Microsoft.ApiManagement/locations/westus/operations/x']
        for url in paths:
            with self.subTest(url=url), self.assertRaises(OperationError):
                cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Azure-AsyncOperation': url}}, sleep=lambda _: None)

    def test_location_completion_can_be_empty_but_status_endpoint_cannot(self):
        url = 'https://management.azure.com' + APIM + '/operations/x'
        for code in (200, 204):
            self.ctx.transport.request.return_value = {'statusCode': code, 'body': None}
            cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url}}, sleep=lambda _: None)
            with self.assertRaises(OperationError):
                cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Azure-AsyncOperation': url}}, sleep=lambda _: None)

    def test_retry_after_is_respected_before_initial_and_subsequent_status_reads(self):
        url = 'https://management.azure.com' + APIM + '/operations/x'
        self.ctx.transport.request.side_effect = [
            {'statusCode': 200, 'body': {'status': 'InProgress'}, 'headers': {'Retry-After': '3'}},
            {'statusCode': 200, 'body': {'status': 'Succeeded'}}]
        delays = []
        cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url, 'Retry-After': '7'}},
                          timeout=20, sleep=delays.append, clock=lambda: 0)
        self.assertEqual(delays, [7, 3])
        self.assertEqual(self.ctx.transport.request.call_count, 2)

    def test_retry_after_longer_than_budget_or_unsupported_does_not_poll_early(self):
        url = 'https://management.azure.com' + APIM + '/operations/x'
        for hint in ('120', 'invalid', '99999999999999999999999999', ''):
            with self.subTest(hint=hint), self.assertRaises(OperationError):
                cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url, 'Retry-After': hint}},
                                  timeout=30, sleep=lambda _: None, clock=lambda: 0)
        self.ctx.transport.request.assert_not_called()

    def test_lro_404_or_unknown_is_not_success(self):
        url = 'https://management.azure.com' + APIM + '/operations/x'
        for response in ({'statusCode': 404}, {'statusCode': 200, 'body': {}},
                         {'statusCode': 200, 'body': {'status': 'Failed'}}):
            self.ctx.transport.request.return_value = response
            with self.subTest(response=response), self.assertRaises(OperationError):
                cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Azure-AsyncOperation': url}}, sleep=lambda _: None)

    def test_lro_pending_has_bounded_wait_and_no_mutation_retry(self):
        url = 'https://management.azure.com' + APIM + '/operations/x'
        self.ctx.transport.request.return_value = {'statusCode': 200, 'body': {'status': 'InProgress'}}
        with self.assertRaises(OperationError):
            cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url}},
                              timeout=1, clock=mock.Mock(side_effect=[0, 0, 0, 2]), sleep=lambda _: None)
        self.assertEqual(self.ctx.calls, [])
        self.assertTrue(all(call.args[0] == 'GET' for call in self.ctx.transport.request.call_args_list))

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
        with mock.patch.object(cert, 'run_worker', side_effect=[metadata(), metadata()]), \
                mock.patch.object(cert, 'refresh_gateway') as refresh:
            with self.assertRaises(OperationError):
                cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        refresh.assert_not_called()
        self.assertFalse(any(call[0] == 'POST' for call in self.ctx.calls))

    def test_success_requires_kv_and_every_public_tls_leaf_to_match(self):
        current = metadata()
        changed = metadata(); changed['thumbprint'] = 'B' * 40
        with mock.patch.object(cert, 'run_worker', side_effect=[current, changed]), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'wait_refresh') as refresh, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {'host': h, 'port': p, 'thumbprint': 'B' * 40}):
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        self.assertEqual(result['status'], 'public-tls-verified')
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        refresh.assert_called_once()
        observed = self.ctx.records[-1][1]['publicTls']
        self.assertEqual({(v['host'], v['port']) for v in observed},
                         {('gateway.example.com', 443), ('portal.example.com', 443), ('gateway.example.com', 8443)})

    def test_vm_command_is_not_repeated_on_pending_timeout(self):
        self.ctx.run_cli = mock.Mock(side_effect=lambda args: json.dumps({
            'id': self.ctx.calls[0][1], 'properties': {'instanceView': {'executionState': 'Running'}}}))
        with self.assertRaises(OperationError):
            cert.run_worker(self.ctx, VM, 'kv-test-fixture', 'public-gateway', timeout=1,
                            clock=mock.Mock(side_effect=[0, 0, 0, 2]), sleep=lambda _: None)
        self.assertEqual([v[0] for v in self.ctx.calls], ['PUT'])
        self.assertIn('--instance-view', self.ctx.run_cli.call_args.args[0])

    def test_only_completed_exact_vm_command_is_reclaimed_and_404_verified(self):
        calls = []
        def request(method, resource_id, api, body=None, headers=None):
            calls.append((method, resource_id))
            return {'statusCode': {'PUT': 202, 'DELETE': 204, 'GET': 404}[method]}
        self.ctx.request = request
        self.ctx.run_cli = lambda args: json.dumps({'id': calls[0][1], 'properties': {'instanceView': {
            'executionState': 'Succeeded', 'exitCode': 0, 'output': cert.MARKER + json.dumps(metadata())}}})
        result = cert.run_worker(self.ctx, VM, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(result['thumbprint'], THUMB)
        self.assertEqual([c[0] for c in calls], ['PUT', 'DELETE', 'GET'])
        self.assertEqual(len({c[1] for c in calls}), 1)
        self.assertTrue(calls[0][1].startswith(VM + '/runCommands/operations-certificate-'))


if __name__ == '__main__':
    unittest.main()
