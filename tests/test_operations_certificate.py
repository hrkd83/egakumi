"""Existing certificate maintenance rejects uncertain work and accepts only observed public TLS."""
import ast
import base64
import contextlib
import copy
from email.message import Message
import io
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
import urllib.request
from urllib.response import addinfourl

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
                       'gateway': {'publicHostName': 'gateway.example.com'},
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
        self.assertEqual(result['certificateConsumer'], 'application-gateway')
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

    def metadata_transport(self, redirect=None):
        """Run real urllib proxy/redirect handlers; intercept before any socket opens."""
        requests = []
        public = metadata()
        certificate = {'id': public['certificateId'], 'sid': public['secretVersionUri'],
                       'attributes': {'enabled': True, 'exp': public['expires']},
                       'cer': base64.b64encode(b'independent synthetic public leaf').decode()}
        def offline_open(handler, connection, request, **options):
            kind = 'imds' if request.full_url.startswith('http://169.254.169.254/') else 'vault'
            requests.append({'kind': kind, 'host': request.host, 'tunnelHost': request._tunnel_host,
                             'url': request.full_url, 'headers': dict(request.header_items())})
            headers = Message()
            status = 302 if kind == redirect else 200
            if status == 302:
                headers['Location'] = 'https://redirect-target.invalid/untrusted'
            value = {'access_token': 'placeholder'} if kind == 'imds' else certificate
            response = addinfourl(io.BytesIO(json.dumps(value).encode()), headers, request.full_url, status)
            response.msg = 'Found' if status == 302 else 'OK'
            return response
        proxy = 'http://untrusted-proxy.invalid:8888'
        environment = {'http_proxy': proxy, 'https_proxy': proxy, 'HTTP_PROXY': proxy,
                       'HTTPS_PROXY': proxy, 'no_proxy': '', 'NO_PROXY': ''}
        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(urllib.request.AbstractHTTPHandler, 'do_open', new=offline_open), \
                mock.patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                contextlib.redirect_stdout(output):
            if redirect:
                with self.assertRaises(SystemExit) as stopped:
                    exec(compile(cert.metadata_program('kv-test-fixture', 'public-gateway', 'unit'),
                                 '<synthetic-certificate-metadata>', 'exec'), {})
                self.assertEqual(stopped.exception.code, 1)
            else:
                exec(compile(cert.metadata_program('kv-test-fixture', 'public-gateway', 'unit'),
                             '<synthetic-certificate-metadata>', 'exec'), {})
        return requests, output.getvalue()

    def test_imds_bypasses_environment_proxy_while_vault_retains_its_proxy(self):
        requests, output = self.metadata_transport()
        self.assertEqual([r['kind'] for r in requests], ['imds', 'vault'])
        self.assertEqual((requests[0]['host'], requests[0]['tunnelHost']), ('169.254.169.254', None))
        self.assertEqual(requests[0]['headers']['Metadata'], 'true')
        self.assertEqual(requests[0]['url'],
                         'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net')
        self.assertNotIn('Authorization', requests[0]['headers'])
        self.assertEqual((requests[1]['host'], requests[1]['tunnelHost']),
                         ('untrusted-proxy.invalid:8888', 'kv-test-fixture.vault.azure.net'))
        self.assertEqual(requests[1]['headers']['Authorization'], 'Bearer placeholder')
        self.assertIn('/certificates/public-gateway?', requests[1]['url'])
        self.assertNotIn('/secrets/', requests[1]['url'])
        self.assertTrue(output.startswith(cert.MARKER))
        self.assertNotIn('placeholder', output)

    def test_imds_and_vault_both_reject_redirects_without_forwarding_credentials(self):
        for source in ('imds', 'vault'):
            with self.subTest(source=source):
                requests, output = self.metadata_transport(redirect=source)
                self.assertEqual([r['kind'] for r in requests], ['imds'] if source == 'imds' else ['imds', 'vault'])
                self.assertFalse(any('redirect-target.invalid' in r['url'] for r in requests))
                self.assertNotIn(cert.MARKER, output)
                self.assertNotIn('placeholder', output)

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








    def test_sync_uses_only_appgw_certificates_and_public_https_hosts(self):
        original_verify = self.ctx.verify_resource
        def gateway_only(resource_id, api, expected_type=None):
            self.assertNotEqual(resource_id, APIM)
            return original_verify(resource_id, api, expected_type)
        self.ctx.verify_resource = gateway_only
        with mock.patch.object(cert, 'run_worker', return_value=metadata()) as worker, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {
                    'host': h, 'port': p, 'thumbprint': THUMB,
                    'trustedAndHostnameVerified': True}) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        self.assertEqual(result['status'], 'public-tls-verified')
        worker.assert_called_once_with(self.ctx, VM, 'kv-test-fixture', 'public-gateway')
        self.assertEqual(tls.call_args_list, [mock.call('gateway.example.com', 443), mock.call('portal.example.com', 443)])
        self.assertFalse(any(call[0] == 'POST' or call[1].startswith(APIM) for call in self.ctx.calls))
        self.assertEqual(len([call for call in self.ctx.calls if call[0] == 'PATCH']), 2)
        self.assertEqual(self.ctx.gateway['tags'], {'existing': 'keep'})
        self.assertFalse(any(kind.startswith('certificate-apim') for kind, _ in self.ctx.records))

    def test_sync_succeeds_with_no_custom_apim_certificate(self):
        self.ctx.apim['properties']['hostnameConfigurations'] = []
        with mock.patch.object(cert, 'run_worker', return_value=metadata()), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'observe', return_value=[]):
            cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        self.assertEqual(self.ctx.calls, [])

    def test_gateway_snapshot_failure_stops_before_any_patch(self):
        with mock.patch.object(self.ctx, 'record', side_effect=OperationError('Synthetic record failure')):
            with self.assertRaises(OperationError):
                cert.refresh_gateway(self.ctx, GATEWAY)
        self.assertEqual(self.ctx.calls, [])

    def test_sync_retains_versionless_appgw_reference_validation(self):
        self.ctx.gateway['properties']['sslCertificates'][0]['properties']['keyVaultSecretId'] += 'a' * 32
        with mock.patch.object(cert, 'run_worker') as worker:
            with self.assertRaises(OperationError):
                cert.operate(self.ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
        worker.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

    def test_sync_is_not_success_when_gateway_or_public_tls_work_fails(self):
        for stage in ('refresh_gateway', 'observe'):
            with self.subTest(stage=stage):
                ctx = Context()
                with mock.patch.object(cert, 'run_worker', return_value=metadata()), \
                        mock.patch.object(cert, 'refresh_gateway') as gateway, \
                        mock.patch.object(cert, 'observe', return_value=[]) as observe:
                    {'refresh_gateway': gateway, 'observe': observe}[stage].side_effect = OperationError('Synthetic failure')
                    with self.assertRaises(OperationError):
                        cert.operate(ctx, SimpleNamespace(stage='sync', execute=True, thumbprint=None))
                self.assertFalse(any(kind == 'certificate-result' for kind, _ in ctx.records))
                self.assertFalse(any(call[0] == 'POST' for call in ctx.calls))

    def test_renew_requires_kv_and_every_public_https_leaf_to_match(self):
        changed = metadata(); changed['thumbprint'] = 'B' * 40
        with mock.patch.object(cert, 'run_worker', side_effect=[metadata(), changed]), \
                mock.patch.object(cert, 'refresh_gateway') as gateway, \
                mock.patch.object(cert, 'tls_leaf', side_effect=lambda h, p: {'host': h, 'port': p, 'thumbprint': 'B' * 40}) as tls:
            result = cert.operate(self.ctx, SimpleNamespace(stage='renew', execute=True, thumbprint=THUMB))
        self.assertEqual(result['status'], 'public-tls-verified')
        gateway.assert_called_once_with(self.ctx, GATEWAY)
        self.assertEqual(tls.call_count, 2)
        self.assertFalse(any(call[0] == 'POST' for call in self.ctx.calls))
        observed = self.ctx.records[-1][1]['publicTls']
        self.assertEqual({(v['host'], v['port']) for v in observed}, {('gateway.example.com', 443), ('portal.example.com', 443)})

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
            (root / 'infra/guest').mkdir(parents=True); (root / 'state').mkdir(); (root / 'bin').mkdir()
            for leaf in ('cache', 'config', 'data'):
                (root / 'state' / leaf).mkdir()
            worker = '# synthetic certificate worker\n'
            (root / 'infra/guest/certificate.ps1').write_text(worker)
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
