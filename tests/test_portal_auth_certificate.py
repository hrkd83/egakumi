"""合成応答で証明書の保存境界と段階管理・不明応答の再開を確認する。"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers import portal_auth_certificate as material
from deployment.helpers.configuration import normalize_configuration, ConfigurationError
from operations import portal_auth_certificate as operation
from operations.common import OperationError
from configuration_fixtures import fixture
from deployment.helpers.paths import LocalPaths

# 独立した合成metadata。証明書の暗号検証はPowerShell offline試験で行います。
DER = b'\x30' + b'x' * 160


def record(config, version='a'):
    origin = 'https://' + 'kv-' + config['resourceStem'] + '.vault.azure.net'
    name = config['portal']['certificate']['certificateName']
    return {'certificateId': origin + '/certificates/' + name + '/' + version * 32,
            'keyId': origin + '/keys/' + name + '/' + version * 32,
            'certificateThumbprint': hashlib.sha1(DER).hexdigest(), 'publicCertificate': base64.b64encode(DER).decode(),
            'notBefore': int(time.time()) - 100, 'expires': int(time.time()) + 86400}


class CertificateMaterialTests(unittest.TestCase):
    def setUp(self):
        self.config = normalize_configuration(fixture())
        self.record = record(self.config)

    def test_initial_config_does_not_require_portal_app(self):
        self.assertNotIn('authentication', self.config['portal'])
        self.assertEqual(self.config['portal']['certificate'], {'certificateName': 'portal-auth', 'validityMonths': 12, 'renewBeforeDays': 60})

    def test_registration_can_use_only_tenant_and_client(self):
        raw = fixture()
        raw['portal']['authentication'] = {'tenantId': '11111111-1111-1111-1111-111111111111', 'clientId': '22222222-2222-2222-2222-222222222222'}
        self.assertEqual(normalize_configuration(raw)['portal']['authentication'], raw['portal']['authentication'])
        raw['portal']['authentication']['certificateThumbprint'] = 'a'*40
        with self.assertRaises(ConfigurationError):
            normalize_configuration(raw)

    def test_rejects_wrong_vault_unpinned_key_thumbprint_and_expiry(self):
        for field, value in [('keyId', 'https://example.invalid/keys/portal-auth/' + 'a'*32), ('keyId', self.record['keyId'].rsplit('/', 1)[0]),
                             ('certificateThumbprint', '0'*40), ('expires', 1), ('expires', True), ('publicCertificate', 'bad-base64')]:
            altered = {**self.record, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                material.certificate_record(altered, self.config)

    def test_duplicate_or_truncated_worker_output_is_rejected(self):
        line = material.MARKER + json.dumps(self.record)
        self.assertEqual(material.parse_worker_output(line, self.config), self.record)
        for output in ('', line + '\n' + line, line[:-20]):
            with self.assertRaises(ValueError):
                material.parse_worker_output(output, self.config)

    def test_deployment_refuses_absent_app_and_unconfirmed_candidate(self):
        with self.assertRaises(ValueError):
            material.deployment_authentication(self.config, None)
        self.config['portal']['authentication'] = {'tenantId': '11111111-1111-1111-1111-111111111111', 'clientId': '22222222-2222-2222-2222-222222222222'}
        with mock.patch.object(material, 'read_certificate_state', return_value={'current': self.record, 'candidate': self.record, 'phase': 'prepared'}):
            with self.assertRaises(ValueError):
                material.deployment_authentication(self.config, None, 'candidate')

    def test_initial_issuance_collects_public_certificate_and_reuses_current(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = LocalPaths(Path(directory), self.config['environmentName'])
            vm = ('/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/' + self.config['resourceGroupNames']['maint']
                  + '/providers/Microsoft.Compute/virtualMachines/vm-' + self.config['resourceStem'] + '-mgmt')
            resource = vm + '/runCommands/initialize-portal-auth-certificate'
            outputs = {'managementVm': {'id': vm}, 'portal': {'authCertificateRunCommandId': resource}}
            response = {'statusCode': 200, 'body': {'id': resource, 'properties': {'instanceView': {
                'executionState': 'Succeeded', 'exitCode': 0, 'output': material.MARKER + json.dumps(self.record)}}}}
            transport = SimpleNamespace(request=mock.Mock(return_value=response))
            def save_json(path, state):
                material.private_parent(path, workspace.repository)
                path.write_text(json.dumps(state), encoding='utf-8')
                path.chmod(0o600)
            with mock.patch.object(material, 'local_paths', return_value=workspace), mock.patch('deployment.helpers.private_permissions._windows_owner_acl'):
                material.collect_initial_certificate(self.config, outputs, transport, save_json)
                public_path = workspace.root / 'generated/portal-auth-public.cer'
                self.assertIn('BEGIN CERTIFICATE', public_path.read_text())
                self.assertNotIn('PRIVATE KEY', public_path.read_text())
                material.collect_initial_certificate(self.config, outputs, transport, save_json)
                self.assertEqual(json.loads((workspace.state/'portal-auth-certificate.json').read_text())['current'], self.record)
                response['body']['properties']['instanceView']['output'] = material.MARKER + json.dumps(record(self.config, 'b'))
                with self.assertRaisesRegex(ValueError, 'differs'):
                    material.collect_initial_certificate(self.config, outputs, transport, save_json)

    def test_failed_initial_worker_does_not_create_state(self):
        vm = ('/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/' + self.config['resourceGroupNames']['maint']
              + '/providers/Microsoft.Compute/virtualMachines/vm-' + self.config['resourceStem'] + '-mgmt')
        outputs = {'managementVm': {'id': vm}, 'portal': {'authCertificateRunCommandId': vm + '/runCommands/initialize-portal-auth-certificate'}}
        transport = SimpleNamespace(request=mock.Mock(return_value={'statusCode':200,'body':{'id':outputs['portal']['authCertificateRunCommandId'],'properties':{'instanceView':{'executionState':'Failed','exitCode':1}}}}))
        save = mock.Mock()
        with self.assertRaises(ValueError):
            material.collect_initial_certificate(self.config, outputs, transport, save)
        save.assert_not_called()


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.config = normalize_configuration(fixture())
        self.config['portal']['authentication'] = {'tenantId': '11111111-1111-1111-1111-111111111111', 'clientId': '22222222-2222-2222-2222-222222222222'}
        self.current = record(self.config)
        self.candidate = record(self.config, 'b')
        self.state = {key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        self.state.update(current=self.current, phase='initial')
        self.ctx = SimpleNamespace(config=self.config, workspace=SimpleNamespace(state=Path('unused')), save_private=mock.Mock())
        self.args = SimpleNamespace(entra_registration_confirmed=False, entra_old_certificate_removed=False, login_verified=False, graph_verified=False, job_verified=False)

    def test_preparation_does_not_switch_current_and_reuses_completed_candidate(self):
        with mock.patch.object(operation, 'run_worker', return_value=self.candidate) as worker, mock.patch.object(operation, 'save_public_certificate'):
            operation.perform(self.ctx, self.state, 'prepare', self.args)
            operation.perform(self.ctx, self.state, 'prepare', self.args)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(self.state['current'], self.current)
        self.assertEqual(self.state['phase'], 'prepared')

    def test_manual_registration_is_required_before_switch(self):
        self.state.update(candidate=self.candidate, phase='prepared')
        with self.assertRaises(OperationError):
            operation.perform(self.ctx, self.state, 'confirm-registration', self.args)
        self.args.entra_registration_confirmed = True
        operation.perform(self.ctx, self.state, 'confirm-registration', self.args)
        self.assertEqual(self.state['phase'], 'registered')
        self.config['portal']['authentication']['clientId'] = '33333333-3333-3333-3333-333333333333'
        with self.assertRaises(OperationError):
            operation.perform(self.ctx, self.state, 'switch', self.args)

    def test_health_or_configuration_verification_does_not_claim_login_acceptance(self):
        self.state.update(phase='switched', previous=self.current, imageDigest='sha256:'+'a'*64)
        with mock.patch.object(operation, 'verify_runtime') as verify:
            with self.assertRaises(OperationError):
                operation.perform(self.ctx, self.state, 'verify', self.args)
            verify.assert_not_called()
            self.args.login_verified = self.args.graph_verified = self.args.job_verified = True
            operation.perform(self.ctx, self.state, 'verify', self.args)
            self.assertEqual(self.state['phase'], 'verified')

    def test_retirement_requires_acceptance_and_old_entra_removal(self):
        self.state.update(phase='verified', previous=self.current, imageDigest='sha256:'+'a'*64)
        with mock.patch.object(operation, 'run_worker') as worker:
            with self.assertRaises(OperationError):
                operation.perform(self.ctx, self.state, 'retire', self.args)
            worker.assert_not_called()

    def test_switch_saves_previous_and_updates_current_only_after_runtime_and_vm_checks(self):
        self.state.update(candidate=self.candidate, previous=self.current, phase='switching', registration=dict(self.config['portal']['authentication']), imageDigest='sha256:'+'a'*64)
        with mock.patch.object(operation, 'deploy') as deploy, mock.patch.object(operation, 'verify_runtime'), mock.patch.object(operation, 'run_worker', return_value=self.candidate), mock.patch.object(operation, 'save_public_certificate'):
            operation.perform(self.ctx, self.state, 'switch', self.args)
        deploy.assert_called_once_with(self.ctx, 'candidate', self.state['imageDigest'])
        self.assertEqual(self.state['current'], self.candidate)
        self.assertEqual(self.state['previous'], self.current)
        self.assertEqual(self.state['phase'], 'switched')

    def test_failed_runtime_check_keeps_old_current_for_retry_and_rollback(self):
        self.state.update(candidate=self.candidate, previous=self.current, phase='switching', registration=dict(self.config['portal']['authentication']), imageDigest='sha256:'+'a'*64)
        with mock.patch.object(operation, 'deploy'), mock.patch.object(operation, 'verify_runtime', side_effect=OperationError('Unverified runtime')), mock.patch.object(operation, 'run_worker') as worker:
            with self.assertRaises(OperationError):
                operation.perform(self.ctx, self.state, 'switch', self.args)
            worker.assert_not_called()
        self.assertEqual(self.state['current'], self.current)
        self.assertEqual(self.state['phase'], 'switching')

    def test_unknown_vm_put_is_not_repeated_on_resume(self):
        self.ctx.request = mock.Mock(side_effect=OperationError('Unknown response'))
        self.ctx.run_cli = mock.Mock(side_effect=OperationError('Still unknown'))
        with mock.patch.object(operation, 'targets', return_value='/synthetic-vm'):
            for _ in range(2):
                with self.assertRaises(OperationError):
                    operation.run_worker(self.ctx, self.state, 'Prepare', {'requestId':'b'*32, 'currentKeyId':self.current['keyId']})
        self.assertEqual(self.ctx.request.call_count, 1)
        self.assertIn('vmRequest', self.state)


if __name__ == '__main__':
    unittest.main()
