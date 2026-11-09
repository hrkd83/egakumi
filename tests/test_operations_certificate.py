"""Existing certificate maintenance rejects uncertain work and accepts only observed public TLS."""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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

    def test_location_pending_empty_then_owned_resource_completes_without_status(self):
        url = 'https://management.azure.com' + APIM + '/operationresults/test'
        self.ctx.transport.request.side_effect = [
            {'statusCode': 202, 'body': None, 'headers': {'Retry-After': '3'}},
            {'statusCode': 200, 'body': {'id': APIM, 'type': 'Microsoft.ApiManagement/service',
                                       'properties': {'hostnameConfigurations': []}}}]
        delays = []
        cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url, 'Retry-After': '7'}},
                          timeout=20, sleep=delays.append, clock=lambda: 0)
        self.assertEqual(delays, [7, 3])
        self.assertEqual(self.ctx.transport.request.call_args_list,
                         [mock.call('GET', url), mock.call('GET', url)])
        self.assertEqual(self.ctx.calls, [])
        self.assertEqual(self.ctx.records, [])

    def test_location_resource_completion_rejects_foreign_errors_and_failure(self):
        url = 'https://management.azure.com' + APIM + '/operationresults/test'
        resource = {'id': APIM, 'type': 'Microsoft.ApiManagement/service',
                    'properties': {'provisioningState': 'Succeeded'}}
        foreign = copy.deepcopy(resource); foreign['id'] += '-foreign'
        wrong_type = copy.deepcopy(resource); wrong_type['type'] = 'Microsoft.Network/applicationGateways'
        errored = copy.deepcopy(resource); errored['error'] = {'code': 'SyntheticFailure'}
        failed = copy.deepcopy(resource); failed['status'] = 'Succeeded'; failed['properties']['provisioningState'] = 'Failed'
        wrong_properties = copy.deepcopy(resource); wrong_properties['properties'] = []
        for body in (foreign, wrong_type, errored, failed, wrong_properties, [{'status': 'Succeeded'}]):
            self.ctx.transport.request.reset_mock()
            self.ctx.transport.request.return_value = {'statusCode': 200, 'body': body}
            with self.subTest(body=body), self.assertRaises(OperationError):
                cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url}}, sleep=lambda _: None)
            self.ctx.transport.request.assert_called_once_with('GET', url)
        self.assertEqual(self.ctx.calls, [])

    def test_location_empty_pending_has_finite_budget_and_only_gets(self):
        url = 'https://management.azure.com' + APIM + '/operationresults/test'
        self.ctx.transport.request.return_value = {'statusCode': 202, 'body': None,
                                                   'headers': {'Retry-After': '2'}}
        elapsed = [0]
        def sleep(delay):
            elapsed[0] += delay
        with self.assertRaises(OperationError):
            cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url, 'Retry-After': '2'}},
                              timeout=7, sleep=sleep, clock=lambda: elapsed[0])
        self.assertEqual(elapsed, [6])
        self.assertEqual(self.ctx.transport.request.call_args_list, [mock.call('GET', url)] * 3)
        self.assertEqual(self.ctx.calls, [])

    def test_default_refresh_budget_waits_one_lro_past_twenty_minutes(self):
        url = 'https://management.azure.com' + APIM + '/operationresults/test'
        self.ctx.transport.request.side_effect = [
            {'statusCode': 202, 'body': None, 'headers': {'Retry-After': '1200'}},
            {'statusCode': 200, 'body': {'id': APIM, 'type': 'Microsoft.ApiManagement/service'}}]
        elapsed = [0]
        cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': url, 'Retry-After': '1200'}},
                          sleep=lambda delay: elapsed.__setitem__(0, elapsed[0] + delay),
                          clock=lambda: elapsed[0])
        self.assertEqual(elapsed, [2400])
        self.assertEqual(self.ctx.transport.request.call_args_list, [mock.call('GET', url)] * 2)
        self.assertEqual(self.ctx.calls, [])

    def test_location_wait_does_not_retry_initial_conflict_or_expand_relative_urls(self):
        with self.assertRaises(OperationError):
            cert.wait_refresh(self.ctx, {'statusCode': 409, 'body': {'error': {'code': 'ServiceLocked'}}},
                              sleep=lambda _: None)
        with self.assertRaises(OperationError):
            cert.wait_refresh(self.ctx, {'statusCode': 202, 'headers': {'Location': APIM + '/operationresults/test'}},
                              sleep=lambda _: None)
        self.ctx.transport.request.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

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

    def test_refresh_rejection_records_only_status_and_bounded_code_without_retry(self):
        original_request = self.ctx.request
        for code, expected in [('ServiceLocked', 'ServiceLocked'), ('not a code\nSECRET', None),
                               ('x' * 129, None), (None, None)]:
            with self.subTest(code=code):
                self.ctx.calls.clear(); self.ctx.records.clear()
                def rejected(method, resource_id, api, body=None, headers=None):
                    response = original_request(method, resource_id, api, body, headers)
                    if method == 'POST':
                        return {'statusCode': 409, 'headers': {'Location': 'https://example.invalid/PRIVATE_TOKEN'},
                                'body': {'error': {'code': code, 'message': 'PRIVATE_ERROR_MESSAGE',
                                                   'details': ['PRIVATE_DETAILS']}}}
                    return response
                with mock.patch.object(self.ctx, 'request', side_effect=rejected), \
                        mock.patch.object(cert, 'run_worker', return_value=metadata()) as worker, \
                        mock.patch.object(cert, 'refresh_gateway') as gateway, \
                        mock.patch.object(cert, 'tls_leaf', return_value={'thumbprint': 'B' * 40,
                            'trustedAndHostnameVerified': True}) as tls:
                    with self.assertRaises(OperationError):
                        cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
                worker.assert_called_once_with(self.ctx, VM, 'kv-test-fixture', 'public-gateway')
                gateway.assert_not_called()
                tls.assert_called_once_with('gateway.example.com', 8443)
                self.assertEqual(len(self.ctx.calls), 1)
                self.assertEqual(self.ctx.calls[0][:2], ('POST', APIM + '/refreshHostnames'))
                responses = [v for k, v in self.ctx.records if k == 'certificate-apim-refresh-response']
                self.assertEqual(responses, [{'httpStatus': 409, 'azureErrorCode': expected}])
                recorded = json.dumps(self.ctx.records)
                for private in ('PRIVATE_TOKEN', 'PRIVATE_ERROR_MESSAGE', 'PRIVATE_DETAILS', 'SECRET'):
                    self.assertNotIn(private, recorded)

    def test_sync_matching_current_apim_leaf_skips_post_but_refreshes_gateway_and_observes_all_hosts(self):
        def leaf(host, port):
            return {'host': host, 'port': port, 'thumbprint': THUMB,
                    'trustedAndHostnameVerified': True}
        with mock.patch.object(cert, 'run_worker', return_value=metadata()) as worker, \
                mock.patch.object(cert, 'wait_refresh') as wait, \
                mock.patch.object(cert, 'tls_leaf', side_effect=leaf) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        worker.assert_called_once_with(self.ctx, VM, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(result['status'], 'public-tls-verified')
        self.assertFalse(any(call[0] == 'POST' for call in self.ctx.calls))
        self.assertEqual(len([call for call in self.ctx.calls if call[0] == 'PATCH']), 2)
        self.assertEqual(self.ctx.gateway['tags'], {'existing': 'keep'})
        wait.assert_not_called()
        self.assertEqual(tls.call_args_list, [mock.call('gateway.example.com', 8443),
            mock.call('gateway.example.com', 443), mock.call('portal.example.com', 443),
            mock.call('gateway.example.com', 8443)])
        skipped = [value for kind, value in self.ctx.records if kind == 'certificate-apim-refresh-skipped']
        self.assertEqual(skipped, [{'basis': 'trusted-public-tls-matches-current-key-vault',
            'postSkipped': True, 'lroCompletionObserved': False,
            'publicTls': leaf('gateway.example.com', 8443)}])
        self.assertFalse(any(kind == 'certificate-apim-refresh-requested' for kind, _ in self.ctx.records))

    def test_sync_trusted_old_apim_leaf_keeps_one_post_and_existing_wait(self):
        with mock.patch.object(cert, 'run_worker', return_value=metadata()), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'wait_refresh') as wait, \
                mock.patch.object(cert, 'tls_leaf', return_value={'thumbprint': 'B' * 40,
                    'trustedAndHostnameVerified': True}) as tls, \
                mock.patch.object(cert, 'observe', return_value=[]) as observe:
            result = cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        self.assertEqual(result['status'], 'public-tls-verified')
        self.assertEqual([call[:2] for call in self.ctx.calls], [('POST', APIM + '/refreshHostnames')])
        tls.assert_called_once_with('gateway.example.com', 8443)
        wait.assert_called_once()
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        observe.assert_called_once()
        self.assertFalse(any(kind == 'certificate-apim-refresh-skipped' for kind, _ in self.ctx.records))

    def test_sync_tls_observation_failure_stops_without_post_or_gateway_update(self):
        with mock.patch.object(cert, 'run_worker', return_value=metadata()), \
                mock.patch.object(cert, 'tls_leaf', side_effect=OperationError('Synthetic TLS failure')), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'observe') as observe:
            with self.assertRaises(OperationError):
                cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        self.assertEqual(self.ctx.calls, [])
        gateway.assert_not_called(); observe.assert_not_called()
        self.assertFalse(any(kind in ('certificate-apim-refresh-skipped', 'certificate-result')
                             for kind, _ in self.ctx.records))

    def test_sync_skip_cannot_bypass_versionless_reference_verification(self):
        self.ctx.apim['properties']['hostnameConfigurations'][0]['keyVaultId'] += '/' + 'a' * 32
        with mock.patch.object(cert, 'run_worker') as worker, mock.patch.object(cert, 'tls_leaf') as tls:
            with self.assertRaises(OperationError):
                cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        worker.assert_not_called(); tls.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

    def test_sync_skip_is_not_success_when_remaining_gateway_or_public_tls_work_fails(self):
        for failed_stage in ('refresh_gateway', 'observe'):
            with self.subTest(stage=failed_stage):
                ctx = Context()
                with mock.patch.object(cert, 'run_worker', return_value=metadata()), \
                        mock.patch.object(cert, 'tls_leaf', return_value={'thumbprint': THUMB,
                            'trustedAndHostnameVerified': True}), \
                        mock.patch.object(cert, 'refresh_gateway') as gateway, \
                        mock.patch.object(cert, 'observe', return_value=[]) as observe:
                    {'refresh_gateway': gateway, 'observe': observe}[failed_stage].side_effect = OperationError('Synthetic failure')
                    with self.assertRaises(OperationError):
                        cert.operate(ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
                self.assertFalse(any(call[0] == 'POST' for call in ctx.calls))
                self.assertFalse(any(kind == 'certificate-result' for kind, _ in ctx.records))

    def test_success_requires_kv_and_every_public_tls_leaf_to_match(self):
        current = metadata()
        changed = metadata(); changed['thumbprint'] = 'B' * 40
        with mock.patch.object(cert, 'run_worker', side_effect=[current, changed]), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'wait_refresh') as refresh, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {'host': h, 'port': p, 'thumbprint': 'B' * 40}) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        self.assertEqual(result['status'], 'public-tls-verified')
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        refresh.assert_called_once()
        self.assertEqual(tls.call_count, 3)  # renew retains its POST; no sync-only pre-observation.
        self.assertEqual(len([call for call in self.ctx.calls if call[0] == 'POST']), 1)
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

    def test_vm_show_requires_named_identifiers_and_accepts_flat_instance_view(self):
        calls, cli_calls = [], []
        def request(method, resource_id, api, body=None, headers=None):
            calls.append((method, resource_id))
            return {'statusCode': {'PUT': 202, 'DELETE': 204, 'GET': 404}[method]}
        def show(arguments):
            cli_calls.append(arguments)
            # The CLI also handles location-level commands. Its VM selector
            # requires these names; --ids alone does not select that branch.
            required = {'--subscription': SUB, '--resource-group': 'rg-test-maint',
                        '--vm-name': 'vm-test-fixture-mgmt',
                        '--run-command-name': calls[0][1].rsplit('/', 1)[-1]}
            if ('--ids' in arguments or any(option not in arguments
                    or arguments[arguments.index(option) + 1] != value
                    for option, value in required.items())):
                raise OperationError('Specify the VM resource group, VM and run-command name')
            return json.dumps({'id': calls[0][1], 'instanceView': {
                'executionState': 'Succeeded', 'exitCode': 0,
                'output': cert.MARKER + json.dumps(metadata())}})
        self.ctx.request, self.ctx.run_cli = request, show
        result = cert.run_worker(self.ctx, VM, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(result['thumbprint'], THUMB)
        self.assertEqual(len(cli_calls), 1)
        self.assertEqual([method for method, _ in calls], ['PUT', 'DELETE', 'GET'])
        self.assertEqual(len({resource_id for _, resource_id in calls}), 1)

    def test_existing_command_reader_cannot_start_or_reclaim_a_command(self):
        resource_id = VM + '/runCommands/operations-certificate-' + 'a' * 12
        self.ctx.run_cli = mock.Mock(return_value=json.dumps({'id': resource_id,
            'instanceView': {'executionState': 'Succeeded', 'exitCode': 0}}))
        properties, view = cert.read_worker_command(self.ctx, VM, resource_id)
        self.assertEqual(properties['id'], resource_id)
        self.assertEqual(view['executionState'], 'Succeeded')
        self.assertEqual(self.ctx.calls, [])
        self.assertEqual(self.ctx.records, [])
        self.ctx.run_cli.reset_mock()
        for foreign in (resource_id.replace('/vm-test-fixture-mgmt/', '/vm-other/'),
                        VM + '/runCommands/unrelated-command'):
            with self.subTest(resource_id=foreign), self.assertRaises(OperationError):
                cert.read_worker_command(self.ctx, VM, foreign)
        self.ctx.run_cli.assert_not_called()

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'A POSIX shell is required')
    def test_generated_renewal_uses_os_login_environment_and_reclaims_only_its_transient_unit(self):
        fake = r'''import json, os, pathlib, pwd, subprocess, sys
name=pathlib.Path(sys.argv[0]).name; args=sys.argv[1:]; root=pathlib.Path(os.environ['FIXTURE_ROOT'])
def record(value):
    with (root/'events.jsonl').open('a') as output: output.write(json.dumps(value)+'\n')
if name=='systemctl':
    record({'command':name,'args':args})
    if args[0]=='is-active': sys.exit(1)
    if args[0]=='show': print('not-found')
    elif args[0]=='stop':
        assert len(args)==2 and '-operations-' in args[1] and args[1].endswith('.service')
    else: raise AssertionError('Unexpected systemctl operation')
elif name=='systemd-run':
    for flag in ['--system','--quiet','--wait','--pipe','--collect','--service-type=exec',
                 '--uid=root','--gid=root','--property=SetLoginEnvironment=yes','--property=UMask=0077',
                 '--property=NoNewPrivileges=yes','--property=PrivateTmp=yes','--property=ProtectHome=yes',
                 '--property=ProtectSystem=strict','--property=ReadWritePaths='+str(root/'state'),
                 '--property=RuntimeMaxSec=3300','--property=TimeoutStopSec=30','--expand-environment=no']:
        assert flag in args
    assert not any(x.startswith(('--setenv=HOME','--property=Environment=HOME=')) for x in args)
    unit=[x.split('=',1)[1] for x in args if x.startswith('--unit=')]
    assert len(unit)==1 and '-operations-' in unit[0] and unit[0].endswith('.service')
    child={key:os.environ[key] for key in ['PATH','FIXTURE_ROOT','FIXTURE_FAIL']}
    child['HOME']=pwd.getpwuid(os.getuid()).pw_dir
    for item in args:
        if item.startswith('--property=Environment='):
            key,value=item[len('--property=Environment='):].split('=',1)
            assert key in ('XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME')
            child[key]=value
    worker=str(root/'bin/pwsh'); record({'command':name,'unit':unit[0]})
    sys.exit(subprocess.run(args[args.index(worker):],env=child,check=False).returncode)
elif name=='pwsh':
    if os.environ.get('HOME')!=pwd.getpwuid(os.getuid()).pw_dir:
        raise SystemExit('Worker did not receive the OS login HOME')
    for key,leaf in [('XDG_CACHE_HOME','cache'),('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data')]:
        assert os.environ.get(key)==str(root/'state'/leaf)
    assert 'CALLER_ONLY_VALUE' not in os.environ
    assert args==['-NoLogo','-NoProfile','-NonInteractive','-File',str(root/'state/certificate.ps1'),
                  '-Action','Renew','-ConfigFile',str(root/'state/config.json'),'-RotateFromThumbprint','A'*40]
    record({'command':name,'osLoginEnvironment':True})
    sys.exit(9 if os.environ['FIXTURE_FAIL']=='1' else 0)
else: raise AssertionError('Unexpected fixture command')
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'guest').mkdir(); (root / 'state').mkdir(); (root / 'bin').mkdir()
            for leaf in ('cache', 'config', 'data'):
                (root / 'state' / leaf).mkdir()
            worker = '# synthetic certificate worker\n'
            (root / 'guest/certificate.ps1').write_text(worker)
            (root / 'state/certificate.ps1').write_text(worker)
            (root / 'state/config.json').write_text('{}')
            for command in ('systemctl', 'systemd-run', 'pwsh'):
                executable = root / 'bin' / command
                executable.write_text('#!' + sys.executable + '\n' + fake)
                executable.chmod(0o700)
            with mock.patch.object(cert, 'ROOT', root), mock.patch.object(
                    cert, 'metadata_program', return_value="print('SYNTHETIC_METADATA_REACHED')"):
                source = cert.remote_script('kv-test-fixture', 'public-gateway', renew=True, thumbprint=THUMB)
            source = source.replace('/var/lib/egakumi-certificates/kv-test-fixture-public-gateway', str(root / 'state'))
            source = source.replace('/usr/bin/pwsh', str(root / 'bin/pwsh'))
            script = root / 'run.sh'; script.write_text(source)
            environment = {**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
                           'FIXTURE_ROOT': str(root), 'CALLER_ONLY_VALUE': 'synthetic caller setting'}
            environment.pop('HOME', None)
            for failure in (False, True):
                with self.subTest(worker_failure=failure):
                    events = root / 'events.jsonl'
                    if events.exists():
                        events.unlink()
                    result = subprocess.run([shutil.which('bash'), str(script)], env={**environment,
                        'FIXTURE_FAIL': '1' if failure else '0'}, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 9 if failure else 0, result.stderr)
                    records = [json.loads(line) for line in events.read_text().splitlines()]
                    launches = [r for r in records if r['command'] == 'systemd-run']
                    self.assertEqual(len(launches), 1)
                    stops = [r for r in records if r['command'] == 'systemctl' and r['args'][0] == 'stop']
                    self.assertEqual([r['args'][1] for r in stops], [launches[0]['unit']] if failure else [])
                    self.assertEqual('SYNTHETIC_METADATA_REACHED' in result.stdout, not failure)


if __name__ == '__main__':
    unittest.main()
