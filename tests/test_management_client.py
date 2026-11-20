"""管理VM保守clientの対象境界・固定wire・秘密非出力を合成データで確認する。"""
import base64
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import management_client as client


def fixture():
    """Independent synthetic GUIDs and resource names, never environment outputs."""
    subscription = '11111111-1111-4111-8111-111111111111'
    base = '/subscriptions/' + subscription + '/resourceGroups/rg-fixture-svc/providers/'
    return {
        'schemaVersion': 1, 'subscriptionId': subscription,
        'azureTenantId': '22222222-2222-4222-8222-222222222222',
        'managementPrincipalId': '33333333-3333-4333-8333-333333333333',
        'managementVmId': base + 'Microsoft.Compute/virtualMachines/vm-fixture-mgmt',
        'applications': [base + 'Microsoft.App/containerApps/ca-fixture-portal'],
        'jobs': [base + 'Microsoft.App/jobs/caj-fixture-psql'],
        'environments': [base + 'Microsoft.App/managedEnvironments/cae-fixture'],
        'workspace': {'id': base + 'Microsoft.OperationalInsights/workspaces/law-fixture',
                      'customerId': '44444444-4444-4444-8444-444444444444'},
        'auditStorage': [{'id': base + 'Microsoft.Storage/storageAccounts/stfixtureaudit',
                          'containers': ['insights-logs-audit']}],
    }


def operation_token():
    return {'properties': {'token': 'synthetic-operation-token'}}


def app_replica(target):
    suffix = '/revisions/ca-fixture--revision/replicas/replica-fixture'
    return {'id': target + suffix, 'properties': {'containers': [{
        'name': 'portal', 'logStreamEndpoint': 'https://eastus.azurecontainerapps.dev/proxy'
            + client._dev_path(target, 'containerApps') + '/logstream',
    }]}}


class ManagementClientTests(unittest.TestCase):
    def setUp(self):
        self.inventory = fixture()
        self.identity = mock.Mock()
        self.identity.token.return_value = 'synthetic-vm-token'
        self.transport = mock.Mock()
        self.transport.open.return_value = io.BytesIO(b'synthetic-log-body\n')
        self.output = io.BytesIO()

    def test_imds_uses_only_system_identity_with_distinct_audiences_and_memory_cache(self):
        inventory = self.inventory
        claims = {'oid': inventory['managementPrincipalId'], 'tid': inventory['azureTenantId']}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        # Deliberately unsigned synthetic structure; no real token is recorded.
        synthetic = 'synthetic-header.' + payload + '.synthetic-signature'
        self.transport.json.side_effect = [{'resourceId': inventory['managementVmId']}] + [
            {'access_token': synthetic, 'expires_on': str(int(time.time()) + 3600),
             'resource': audience, 'token_type': 'Bearer'}
            for audience in (client.ARM_AUDIENCE, client.STORAGE_AUDIENCE, client.LOGS_AUDIENCE)]
        identity = client.VmIdentity(inventory, self.transport)
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            for audience in (client.ARM_AUDIENCE, client.STORAGE_AUDIENCE, client.LOGS_AUDIENCE):
                self.assertEqual(identity.token(audience), synthetic)
                self.assertEqual(identity.token(audience), synthetic)
        self.assertEqual(self.transport.json.call_count, 4)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')
        for call in self.transport.json.call_args_list[1:]:
            method, url = call.args
            self.assertEqual(method, 'GET')
            self.assertEqual(urlsplit(url).hostname, '169.254.169.254')
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(set(query), {'api-version', 'resource'})
            self.assertEqual(call.kwargs['headers'], {'Metadata': 'true'})

    def test_other_vm_or_identity_stops_without_leaking_imds_response(self):
        identity = client.VmIdentity(self.inventory, self.transport)
        self.transport.json.return_value = {'resourceId': '/foreign/synthetic-vm'}
        with self.assertRaisesRegex(client.MaintenanceError, 'constructed management VM'):
            identity.token(client.ARM_AUDIENCE)
        self.assertEqual(self.transport.json.call_count, 1)
        self.transport.reset_mock()
        identity.verified = True
        self.transport.json.return_value = {'access_token': '<MALFORMED_IMDS_RESPONSE>'}
        with self.assertRaises(client.MaintenanceError) as failure:
            identity.token(client.ARM_AUDIENCE)
        self.assertNotIn('MALFORMED_IMDS_RESPONSE', str(failure.exception))

    def test_arbitrary_resources_and_url_selectors_are_rejected_before_token_requests(self):
        for target in ('https://foreign.invalid', '/subscriptions/foreign/resourceGroups/other/providers/Microsoft.App/containerApps/ca-other'):
            with self.subTest(target=target), self.assertRaises(client.MaintenanceError):
                client.app_logs(self.inventory, target, 'system', None, None, None, 50,
                                self.identity, self.transport, self.output)
        self.identity.assert_not_called()
        self.transport.json.assert_not_called()
        for value in ('../other', 'https://foreign.invalid', None, '..'):
            with self.subTest(selector=value), self.assertRaises(client.MaintenanceError):
                client.selector(value)

    def test_blob_read_is_container_scoped_and_uses_storage_audience(self):
        account = self.inventory['auditStorage'][0]
        client.blob_read(self.inventory, account['id'], account['containers'][0], 'folder/audit record.json',
                         self.identity, self.transport, self.output)
        method, url = self.transport.open.call_args.args
        self.assertEqual(method, 'GET')
        self.assertEqual(url, 'https://stfixtureaudit.blob.core.windows.net/insights-logs-audit/folder/audit%20record.json')
        self.assertEqual(self.transport.open.call_args.kwargs['headers']['x-ms-version'], client.STORAGE_API)
        self.identity.token.assert_called_once_with(client.STORAGE_AUDIENCE)
        self.assertEqual(self.output.getvalue(), b'synthetic-log-body\n')

    def test_blob_container_escape_has_no_network_effect(self):
        for blob in ('../another/blob', '/another/blob', 'folder/../../blob', 'folder\\blob'):
            with self.subTest(blob=blob), self.assertRaises(client.MaintenanceError):
                client.blob_read(self.inventory, self.inventory['auditStorage'][0]['id'], 'insights-logs-audit', blob,
                                 self.identity, self.transport, self.output)
        self.transport.open.assert_not_called()
        self.identity.token.assert_not_called()

    def test_saved_logs_use_registered_audience_independently_of_new_http_endpoint(self):
        claims = {'oid': self.inventory['managementPrincipalId'], 'tid': self.inventory['azureTenantId']}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        synthetic = 'synthetic-header.' + payload + '.synthetic-signature'
        query_url = ('https://api.loganalytics.azure.com/v1/workspaces/'
                     + self.inventory['workspace']['customerId'] + '/query')

        def response(method, url, **kwargs):
            if urlsplit(url).hostname == '169.254.169.254':
                self.assertEqual(method, 'GET')
                if '/metadata/instance/compute?' in url:
                    return {'resourceId': self.inventory['managementVmId']}
                # The synthetic issuer requires the SDK's registered resource;
                # do not derive the IMDS audience from the new HTTP hostname.
                self.assertEqual(parse_qs(urlsplit(url).query)['resource'], ['https://api.loganalytics.io'])
                self.assertEqual(kwargs['headers'], {'Metadata': 'true'})
                return {'access_token': synthetic, 'expires_on': str(int(time.time()) + 3600),
                        'resource': 'https://api.loganalytics.io', 'token_type': 'Bearer'}
            self.assertEqual(method, 'POST')
            self.assertEqual(url, query_url)
            self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer ' + synthetic,
                                                'Content-Type': 'application/json'})
            self.assertEqual(json.loads(kwargs['body']), {'query': 'ContainerAppSystemLogs | take 1'})
            return {'tables': [{'name': 'PrimaryResult', 'rows': [[1]]}]}

        self.transport.json.side_effect = response
        identity = client.VmIdentity(self.inventory, self.transport)
        client.workspace_query(self.inventory, self.inventory['workspace']['id'], 'ContainerAppSystemLogs | take 1',
                               identity, self.transport, self.output)
        self.assertEqual(self.transport.json.call_count, 3)
        self.assertEqual(json.loads(self.output.getvalue())['tables'][0]['rows'], [[1]])

    def test_partial_log_query_failure_is_not_reported_as_success(self):
        self.transport.json.return_value = {'tables': [], 'error': {'message': 'synthetic-private-details'}}
        with self.assertRaises(client.MaintenanceError) as failure:
            client.workspace_query(self.inventory, self.inventory['workspace']['id'], 'print fixture=1',
                                   self.identity, self.transport, self.output)
        self.assertEqual(self.output.getvalue(), b'')
        self.assertNotIn('synthetic-private-details', str(failure.exception))

    def test_app_console_logs_keep_official_prefix_and_explicit_replica(self):
        target = self.inventory['applications'][0]
        endpoint = 'https://eastus.azurecontainerapps.dev/proxy' + client._dev_path(target, 'containerApps') + '/eventstream'
        self.transport.json.side_effect = [app_replica(target), {'id': target, 'properties': {'eventStreamEndpoint': endpoint}}, operation_token()]
        client.app_logs(self.inventory, target, 'console', 'ca-fixture--revision', 'replica-fixture', 'portal', 50,
                        self.identity, self.transport, self.output)
        url = self.transport.open.call_args.args[1]
        self.assertIn('/proxy' + client._dev_path(target, 'containerApps') + '/revisions/ca-fixture--revision/replicas/replica-fixture/containers/portal/logstream?', url)
        self.assertEqual(parse_qs(urlsplit(url).query), {'follow': ['false'], 'output': ['json'], 'tailLines': ['50']})
        self.assertEqual(self.transport.open.call_args.kwargs['headers'], {'Authorization': 'Bearer synthetic-operation-token'})
        self.assertTrue(all('api-version=' + client.APP_API in call.args[1] for call in self.transport.json.call_args_list))

    def test_app_system_logs_reject_console_selectors_and_invalid_tail_before_network(self):
        target = self.inventory['applications'][0]
        for revision, tail in (('explicit-revision', 50), (None, 301), (None, -1)):
            with self.subTest(revision=revision, tail=tail), self.assertRaises(client.MaintenanceError):
                client.app_logs(self.inventory, target, 'system', revision, None, None, tail,
                                self.identity, self.transport, self.output)
        self.transport.json.assert_not_called()

    def test_environment_full_endpoint_is_preserved_and_fallback_only_for_none(self):
        target = self.inventory['environments'][0]
        endpoint = 'https://eastus.azurecontainerapps.dev/proxy' + client._dev_path(target, 'managedEnvironments') + '/eventstream'
        self.transport.json.side_effect = [{'id': target, 'properties': {'eventStreamEndpoint': endpoint}}, operation_token()]
        client.environment_logs(self.inventory, target, 50, self.identity, self.transport, self.output)
        self.assertEqual(self.transport.open.call_args.args[1].split('?')[0], endpoint)
        self.transport.open.return_value = io.BytesIO(b'fallback-synthetic-log')
        self.transport.json.side_effect = [{'id': target, 'location': 'East US', 'properties': {}}, operation_token()]
        client.environment_logs(self.inventory, target, 50, self.identity, self.transport, self.output)
        self.assertEqual(self.transport.open.call_args.args[1].split('?')[0],
                         'https://eastus.azurecontainerapps.dev' + client._dev_path(target, 'managedEnvironments') + '/eventstream')

    def test_job_logs_use_job_token_and_distinct_official_metadata_and_replica_versions(self):
        target = self.inventory['jobs'][0]
        endpoint = 'https://eastus.azurecontainerapps.dev/proxy' + client._dev_path(target, 'jobs') + '/eventstream'
        self.transport.json.side_effect = [
            {'id': target, 'properties': {'eventStreamEndpoint': endpoint, 'template': {'containers': [{'name': 'migration'}]}}},
            {'value': [{'name': 'replica-fixture'}]}, operation_token()]
        client.job_logs(self.inventory, target, 'execution-fixture', 'replica-fixture', 'migration', 50,
                        self.identity, self.transport, self.output)
        urls = [call.args[1] for call in self.transport.json.call_args_list]
        self.assertTrue(urls[0].endswith('?api-version=2025-10-02-preview'))
        self.assertTrue(urls[1].endswith('/executions/execution-fixture/replicas?api-version=2023-11-02-preview'))
        self.assertTrue(urls[2].endswith('/getAuthToken?api-version=2023-11-02-preview'))
        self.assertIn('/jobs/caj-fixture-psql/executions/execution-fixture/replicas/replica-fixture/containers/migration/logstream?', self.transport.open.call_args.args[1])
        self.assertFalse(any('/start' in url or '/listSecrets' in url or '/managedEnvironments/' in url for url in urls))

    def test_exec_uses_official_binary_resize_and_only_explicit_command(self):
        target = self.inventory['applications'][0]
        self.transport.json.side_effect = [app_replica(target), operation_token()]
        connection = mock.Mock()
        connection.recv.side_effect = [b'\x00\x01synthetic-stdout', b'\x00\x02synthetic-stderr', b'']
        connect = mock.Mock()
        connect.return_value.__enter__ = mock.Mock(return_value=connection)
        connect.return_value.__exit__ = mock.Mock(return_value=False)
        client.app_exec(self.inventory, target, 'ca-fixture--revision', 'replica-fixture', 'portal',
                        'printf synthetic-output', 60, self.identity, self.transport, self.output, connect=connect)
        self.assertEqual(parse_qs(urlsplit(connect.call_args.args[0]).query)['command'], ['printf synthetic-output'])
        self.assertEqual(urlsplit(connect.call_args.args[0]).scheme, 'wss')
        self.assertIsNone(connect.call_args.kwargs['proxy'])
        self.assertEqual(connect.call_args.kwargs['additional_headers'], {'Authorization': 'Bearer synthetic-operation-token'})
        connection.send.assert_called_once_with(b'\x00\x04{"Width":80,"Height":24}')
        self.assertEqual(self.output.getvalue(), b'synthetic-stdoutsynthetic-stderr')

    def test_exec_proxy_error_is_protected_and_not_success(self):
        target = self.inventory['applications'][0]
        self.transport.json.side_effect = [app_replica(target), operation_token()]
        connection = mock.Mock()
        connection.recv.return_value = b'\x02synthetic-private-proxy-error'
        connect = mock.MagicMock()
        connect.return_value.__enter__.return_value = connection
        with self.assertRaises(client.MaintenanceError) as failure:
            client.app_exec(self.inventory, target, 'ca-fixture--revision', 'replica-fixture', 'portal',
                            'printf fixture', 60, self.identity, self.transport, self.output, connect=connect)
        self.assertEqual(self.output.getvalue(), b'synthetic-private-proxy-error')
        self.assertNotIn('synthetic-private-proxy-error', str(failure.exception))

    def test_dev_endpoint_rejects_arbitrary_hosts_credentials_and_path_escape(self):
        for endpoint in ('https://foreign.invalid/subscriptions/fixture',
                         'https://eastus.azurecontainerapps.dev.foreign.invalid/subscriptions/fixture',
                         'https://<USER>:<PASSWORD>@eastus.azurecontainerapps.dev/subscriptions/fixture',
                         'https://eastus.azurecontainerapps.dev/../subscriptions/fixture',
                         'https://eastus.azurecontainerapps.dev/subscriptions/fixture?token=synthetic',
                         'http://eastus.azurecontainerapps.dev/subscriptions/fixture'):
            with self.subTest(endpoint=endpoint), self.assertRaises(client.MaintenanceError):
                client._dev_prefix(endpoint)

    def test_http_redirect_is_rejected_before_reusing_authorization(self):
        with self.assertRaises(client.MaintenanceError):
            client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://foreign.invalid')

    @unittest.skipUnless(os.name == 'posix', 'Management VM POSIX permissions are tested on Linux')
    def test_inventory_and_output_require_owner_only_files_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            source = root / 'inventory.json'
            source.write_text(json.dumps(self.inventory), encoding='utf-8')
            source.chmod(0o600)
            self.assertEqual(client.load_inventory(source), self.inventory)
            output = root / 'output.bin'
            with client.protected_output(output) as stream:
                stream.write(b'synthetic-private-body')
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                with client.protected_output(output):
                    pass
            self.assertEqual(output.read_bytes(), b'synthetic-private-body')
            source.chmod(0o644)
            with self.assertRaises(client.MaintenanceError):
                client.load_inventory(source)

    @unittest.skipUnless(os.name == 'posix', 'Management VM POSIX permissions are tested on Linux')
    def test_public_checkout_output_and_linked_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            (root / 'infra').mkdir()
            (root / 'AGENTS.md').write_text('synthetic-checkout')
            with self.assertRaises(client.MaintenanceError):
                with client.protected_output(root / 'public-output.bin'):
                    pass
            local = root / '.local'
            local.mkdir(mode=0o700)
            target = local / 'target'
            target.write_bytes(b'synthetic')
            target.chmod(0o600)
            link = local / 'link'
            link.symlink_to(target)
            with self.assertRaises(client.MaintenanceError):
                client.read_protected(link)


if __name__ == '__main__':
    unittest.main()
