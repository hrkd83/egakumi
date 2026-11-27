"""B2の配備順序、曖昧な開始応答の再照合、非公開結果取得の回帰。"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest import TestCase, mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import certificate_image, certificate_jobs as jobs
from deployment.helpers.paths import LocalPaths


def synthetic_id(index):
    return str(uuid.UUID(int=index))


def arm(status, value=None, **headers):
    return {'statusCode': status, 'body': value or {}, 'headers': headers}


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CertificateJobTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = LocalPaths(Path(temporary.name).resolve(), 'fixture')
        self.workspace.ensure()
        self.config = {'subscriptionId': synthetic_id(7), 'azureTenantId': synthetic_id(8),
                       'location': 'testregion', 'resourceStem': 'fixture',
                       'resourceGroupNames': {'svc': 'rg-fixture-svc', 'maint': 'rg-fixture-maint'},
                       'certificate': {'certificateName': 'public-tls', 'contactEmail': 'build@example.com'},
                       'gateway': {'publicHostName': 'gateway.example.com'},
                       'portal': {'certificate': {'certificateName': 'portal-signing'},
                                  'publicHostName': 'portal.example.com'}}
        self.subscription = '/subscriptions/' + self.config['subscriptionId']
        svc = self.subscription + '/resourceGroups/rg-fixture-svc'
        self.vm = self.subscription + '/resourceGroups/rg-fixture-maint/providers/Microsoft.Compute/virtualMachines/vm-fixture-mgmt'
        env = svc + '/providers/Microsoft.App/managedEnvironments/cae-fixture-certificate'
        registry = svc + '/providers/Microsoft.ContainerRegistry/registries/acrfixture'
        storage_name, vault_name = 'certfixture', 'kv-public-fixture'
        storage = svc + '/providers/Microsoft.Storage/storageAccounts/' + storage_name
        self.vault_origin = f'https://{vault_name}.vault.azure.net'
        id_scope = svc + '/providers/Microsoft.ManagedIdentity/userAssignedIdentities/'
        bootstrap = {'resourceGroup': 'rg-fixture-svc', 'location': 'testregion',
                     'environmentId': env, 'environmentName': 'cae-fixture-certificate',
                     'registryId': registry, 'registryName': 'acrfixture', 'registryServer': 'acrfixture.azurecr.io',
                     'jobName': 'caj-fixture-tls', 'portalJobName': 'caj-fixture-auth-cert',
                     'receiptContainerName': 'certificate-jobs', 'storageAccountName': storage_name,
                     'storageAccountId': storage, 'storageUrl': f'https://{storage_name}.blob.core.windows.net',
                     'certificateVaultName': vault_name,
                     'certificateSecretUri': self.vault_origin + '/secrets/public-tls',
                     'imageDigest': 'sha256:' + 'd' * 64}
        bootstrap['jobPlan'] = {**{key: bootstrap[key] for key in ('environmentId', 'registryServer', 'jobName',
                              'portalJobName', 'storageUrl', 'receiptContainerName')},
                              'pullIdentityId': id_scope + 'pull', 'tlsIdentity': {'id': id_scope + 'tls', 'clientId': synthetic_id(1)},
                              'portalIdentity': {'id': id_scope + 'portal', 'clientId': synthetic_id(2)},
                              'dnsIdentityId': id_scope + 'dns', 'dnsClientId': synthetic_id(3),
                              'tlsConfig': {'keyVaultName': vault_name, 'certificateName': 'public-tls'},
                              'portalConfig': {'certificateName': 'portal-signing'},
                              'portalStateContainerName': 'portal-certificate-state'}
        self.outputs = {'managementVm': {'id': self.vm}, 'certificateBootstrap': bootstrap}
        self.target = jobs._targets(self.config, self.outputs)
        self.job = self.target['tlsJobId']
        self.image = jobs._job_image(self.target, bootstrap['imageDigest'])
        self.exec = self.job + '/executions/caj-fixture-tls-repeat'
        self.time = Clock()

    @staticmethod
    def save(path, state):
        path.touch(mode=0o600, exist_ok=True)
        path.write_text(json.dumps(state), encoding='utf-8')

    def job_definition(self, mode='tls'):
        plan = self.target['jobPlan']
        runtime = plan[mode + 'Identity']
        identity = {plan['pullIdentityId']: 'None', runtime['id']: 'Main'}
        environment = {'CERTIFICATE_MODE': mode, 'CERTIFICATE_ACTION': 'renew' if mode == 'tls' else 'initial',
                       'CERTIFICATE_STORAGE_URL': self.target['storageUrl'], 'CERTIFICATE_STORAGE_AUDIENCE': jobs.STORAGE_AUDIENCE,
                       'CERTIFICATE_RECEIPT_CONTAINER': self.target['receiptContainers'][mode],
                       'CERTIFICATE_CONFIG_JSON': json.dumps(plan[mode + 'Config']), 'CERTIFICATE_CLIENT_ID': runtime['clientId'],
                       'CERTIFICATE_DNS_CLIENT_ID': plan['dnsClientId'] if mode == 'tls' else '',
                       'CERTIFICATE_PORTAL_STATE_CONTAINER': plan['portalStateContainerName'] if mode == 'portal' else ''}
        if mode == 'tls':
            identity[plan['dnsIdentityId']] = 'Main'
            environment['CERTIFICATE_ACCOUNT_EMAIL'] = None
        items = [{'name': name, 'value': value} if value is not None
                 else {'name': name, 'secretRef': 'acme-account-contact'} for name, value in environment.items()]
        return {'id': self.target[mode + 'JobId'], 'identity': {'type': 'UserAssigned', 'userAssignedIdentities': dict.fromkeys(identity, {})},
                'properties': {'environmentId': self.target['environmentId'], 'provisioningState': 'Succeeded',
                    'configuration': {'triggerType': 'Schedule' if mode == 'tls' else 'Manual', 'registries': [
                        {'server': self.target['registryServer'], 'identity': plan['pullIdentityId']}],
                        'identitySettings': [{'identity': key, 'lifecycle': phase} for key, phase in identity.items()]},
                    'template': {'containers': [{'name': 'certificate-worker', 'image': self.image, 'env': items}]}}}

    def receipt(self, attempt, *, dns_names=None):
        der = b'0' + b'z' * 200
        return {'attemptId': attempt, 'mode': 'tls', 'action': 'initial',
                'result': {'certificateId': self.vault_origin + '/certificates/public-tls/' + 'c' * 32,
                           'secretId': self.target['certificateSecretUri'] + '/' + 'c' * 32,
                           'certificateThumbprint': hashlib.sha1(der).hexdigest(), 'notBefore': 1,
                           'expires': 4102444800, 'publicCertificate': base64.b64encode(der).decode(),
                           'dnsNames': dns_names or ['*.example.com']}}

    def test_lost_start_response_adopts_only_matching_attempt(self):
        transport = mock.Mock()
        sent = {}
        calls = []
        def request(method, path, body=None):
            calls.append((method, path))
            if method == 'POST':
                sent.update(body)
                raise TimeoutError()
            if '/executions' not in path:
                return arm(200, self.job_definition())
            exact = {'name': self.exec.rsplit('/', 1)[-1],
                     'properties': {'template': sent, 'status': 'Succeeded'}}
            other = {'id': self.job + '/executions/different',
                     'properties': {'template': self.job_definition()['properties']['template'], 'status': 'Succeeded'}}
            return arm(200, {'value': [other, exact]}) if '/executions?' in path else arm(200, exact)
        transport.request.side_effect = request
        with mock.patch.object(jobs, 'read_private_receipt', side_effect=lambda _, __, record, *_args, **_kw: self.receipt(record['attemptId'])):
            result = jobs.run_certificate_action(self.config, self.outputs, transport, self.save, workspace=self.workspace,
                mode='tls', action='initial', monotonic=self.time, sleep=self.time.sleep, timeout=20, operation_id='a' * 32)
            saved = json.loads((self.workspace.state / 'certificate-job-tls-initial.json').read_text())
            self.assertEqual(saved['executionId'], self.exec)
            self.assertEqual(result, self.receipt(saved['attemptId'])['result'])
            before = len(calls)
            jobs.run_certificate_action(self.config, self.outputs, transport, self.save, workspace=self.workspace,
                mode='tls', action='initial', monotonic=self.time, sleep=self.time.sleep, timeout=20, operation_id='a' * 32)
            self.assertEqual(len(calls), before)
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)

    def test_accepted_is_not_success_and_opaque_location_not_followed(self):
        transport = mock.Mock()
        transport.request.return_value = arm(200, {'value': []})
        with self.assertRaisesRegex(ValueError, 'remains unconfirmed'):
            jobs.wait_execution(transport, {'jobId': self.job, 'image': self.image, 'attemptId': 'a' * 32}, mock.Mock(),
                first_response=arm(202, Location='https://foreign.example.com/executions/unknown', **{'Retry-After': '3'}),
                monotonic=self.time, sleep=self.time.sleep, timeout=12)
        self.assertTrue(all(call.args[:2] == ('GET', self.job + '/executions?api-version=' + jobs.APP_API)
                            for call in transport.request.call_args_list))

    def test_name_and_id_must_describe_the_same_execution_within_the_job(self):
        name = self.exec.rsplit('/', 1)[-1]
        self.assertEqual(jobs._response_execution_id({'name': name}, self.job), self.exec)
        self.assertEqual(jobs._response_execution_id({'id': self.exec, 'name': name}, self.job), self.exec)
        for fields in ({'id': self.exec, 'name': 'another-run'},
                       {'id': self.target['portalJobId'] + '/executions/' + name, 'name': name},
                       {'id': None, 'name': name}, {'name': '../another-run'}):
            self.assertIsNone(jobs._response_execution_id(fields, self.job))

    def test_name_only_get_requires_exact_nonce_and_digest_before_recording_terminal_status(self):
        transport = mock.Mock()
        attempt = 'a' * 32
        name = self.exec.rsplit('/', 1)[-1]
        record = {'jobId': self.job, 'image': self.image, 'attemptId': attempt,
                  'executionId': self.exec, 'status': 'requested'}
        template = {'containers': [{'image': self.image,
                                   'env': [{'name': 'CERTIFICATE_ATTEMPT_ID', 'value': 'b' * 32}]}]}
        reply = {'name': name, 'properties': {'template': template, 'status': 'Failed'}}
        transport.request.return_value = arm(200, reply)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            jobs.wait_execution(transport, record, mock.Mock(), monotonic=self.time,
                                sleep=self.time.sleep, timeout=10)
        self.assertEqual(record['status'], 'requested')
        template['containers'][0]['env'][0]['value'] = attempt
        with self.assertRaisesRegex(ValueError, 'identified certificate job execution failed'):
            jobs.wait_execution(transport, record, mock.Mock(), monotonic=self.time,
                                sleep=self.time.sleep, timeout=10)
        self.assertEqual(record['status'], 'failed')

    def test_unknown_start_remains_nonreplayable_on_explicit_retry(self):
        transport = mock.Mock()
        def request(method, path, body=None):
            if method == 'POST':
                raise TimeoutError()
            if '/executions?' in path:
                return arm(200, {'value': []})
            return arm(200, self.job_definition())
        transport.request.side_effect = request
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'remains unconfirmed'):
                jobs.run_certificate_action(self.config, self.outputs, transport, self.save,
                                            workspace=self.workspace, mode='tls', action='initial',
                                            monotonic=self.time, sleep=self.time.sleep, timeout=5,
                                            retry_failed=True)
        self.assertEqual(sum(call.args[0] == 'POST' for call in transport.request.call_args_list), 1)

    def test_template_rejects_mismatch_and_leaf_hostnames_are_verified(self):
        changed = self.job_definition()
        changed['identity']['userAssignedIdentities']['/a/different/identity'] = {}
        with self.assertRaisesRegex(ValueError, 'configuration differs'):
            jobs._template(changed, self.target, 'tls', 'initial', {}, 'a' * 32, self.image)
        with self.assertRaisesRegex(ValueError, 'does not attest'):
            jobs._result(self.config, self.target, 'tls', 'initial', {}, 'a' * 32,
                         self.receipt('a' * 32, dns_names=['gateway.example.com']), now=12)

    def test_tls_job_rejects_a_different_worker_vault_or_certificate_before_start(self):
        settings = self.outputs['certificateBootstrap']['jobPlan']['tlsConfig']
        for key, value in (('keyVaultName', 'kv-fixture'), ('certificateName', 'different-certificate')):
            transport, save = mock.Mock(), mock.Mock()
            with self.subTest(setting=key), mock.patch.dict(settings, {key: value}), \
                    self.assertRaisesRegex(ValueError, 'certificate job outputs differ'):
                jobs.run_certificate_action(self.config, self.outputs, transport, save,
                    workspace=self.workspace, mode='tls', action='renew')
            transport.request.assert_not_called()
            save.assert_not_called()

    def test_private_receipt_chunks_are_bound_and_temp_role_and_commands_removed(self):
        for mode, container in (('tls', 'certificate-jobs'), ('portal', 'portal-certificate-state')):
            with self.subTest(mode=mode):
                self.check_private_receipt(mode, container)

    def check_private_receipt(self, mode, container):
        transport = mock.Mock()
        payload = self.receipt('a' * 32)
        payload['mode'] = mode
        payload['result']['publicCertificate'] += 'A' * 3500
        encoded = json.dumps(payload).encode()
        scope = self.target['storageAccountId'] + '/blobServices/default/containers/' + container
        cloud = {}
        role = None
        def request(method, path, body=None):
            nonlocal role
            resource = path.split('?', 1)[0]
            if resource == self.vm:
                return arm(200, {'id': self.vm, 'identity': {'principalId': synthetic_id(13)}, 'location': 'testregion'})
            if method == 'GET':
                return arm(200, cloud[resource]) if resource in cloud else arm(404)
            if method == 'DELETE':
                cloud.pop(resource)
                return arm(204)
            if '/roleAssignments/' in path:
                role = resource
                cloud[resource] = {'id': resource, 'properties': {**body['properties'], 'scope': scope}}
            else:
                offset = int(resource.rsplit('-', 1)[1], 16)
                self.assertIn(json.dumps(container), body['properties']['source']['script'])
                result = jobs.VM_RESULT + json.dumps({'offset': offset, 'length': len(encoded),
                    'chunk': base64.b64encode(encoded[offset:offset + 1800]).decode(),
                    'sha256': hashlib.sha256(encoded).hexdigest()})
                cloud[resource] = {'id': resource, 'properties': {**body['properties'],
                    'instanceView': {'executionState': 'Succeeded', 'exitCode': 0, 'output': result}}}
            return arm(201)
        transport.request.side_effect = request
        self.assertEqual(jobs.read_private_receipt(self.target, transport, {'mode': mode, 'attemptId': 'a' * 32}, mock.Mock(),
                                                   monotonic=self.time, sleep=self.time.sleep), payload)
        self.assertTrue(role.startswith(scope + '/providers/Microsoft.Authorization/roleAssignments/'))
        self.assertEqual(cloud, {})
        self.assertGreaterEqual(sum(call.args[0] == 'PUT' for call in transport.request.call_args_list), 3)

    def test_portal_execution_rejects_tls_receipt_container(self):
        portal_job = self.job_definition('portal')
        jobs._template(portal_job, self.target, 'portal', 'initial', {}, 'a' * 32, self.image)
        containers = portal_job['properties']['template']['containers']
        receipt = next(item for item in containers[0]['env'] if item['name'] == 'CERTIFICATE_RECEIPT_CONTAINER')
        receipt['value'] = 'certificate-jobs'
        with self.assertRaisesRegex(ValueError, 'runtime environment differs'):
            jobs._template(portal_job, self.target, 'portal', 'initial', {}, 'a' * 32, self.image)

    def test_private_registry_requirements_are_verified_without_enabling_bypass(self):
        value = {'sku': {'name': 'Premium'}, 'properties': {'adminUserEnabled': False,
            'publicNetworkAccess': 'Disabled', 'networkRuleBypassOptions': 'None', 'dataEndpointEnabled': True,
            'roleAssignmentMode': 'LegacyRegistryPermissions', 'policies': {'azureADAuthenticationAsArmPolicy': {'status': 'enabled'}}}}
        certificate_image.ensure_private_registry(value)
        for key, setting in (('networkRuleBypassOptions', 'AzureServices'),
                             ('roleAssignmentMode', 'AbacRepositoryPermissions')):
            with self.subTest(key=key):
                original = value['properties'][key]
                value['properties'][key] = setting
                with self.assertRaisesRegex(ValueError, 'private registry configuration'):
                    certificate_image.ensure_private_registry(value)
                value['properties'][key] = original

    def test_interrupted_build_recovers_only_exact_finished_command_and_role(self):
        target = certificate_image.targets(self.config, {'outputs': self.outputs})
        command = target['commandId'] + '-' + 'a' * 12
        role = target['registryId'] + '/providers/Microsoft.Authorization/roleAssignments/' + synthetic_id(15)
        record = {'commandId': command, 'status': 'building', 'sourceSha256': 'b' * 64,
                  'temporaryRoleId': role, 'temporaryResourcesRemoved': False}
        cloud = {
            self.vm: {'id': self.vm, 'identity': {'principalId': synthetic_id(13)}},
            command: {'id': command, 'properties': {'instanceView': {'executionState': 'Succeeded', 'exitCode': 0,
                       'output': certificate_image.MARKER + json.dumps({'status': 'built',
                                    'sourceSha256': 'b' * 64, 'imageDigest': 'sha256:' + 'd' * 64})}}},
            role: {'id': role, 'properties': {'scope': target['registryId'], 'principalId': synthetic_id(13),
                   'roleDefinitionId': self.subscription + '/providers/Microsoft.Authorization/roleDefinitions/' +
                   certificate_image.credential_image.ACR_PUSH}},
        }
        transport = mock.Mock()
        def request(method, path, _=None):
            resource = path.split('?', 1)[0]
            if method == 'DELETE':
                cloud.pop(resource)
                return arm(204)
            return arm(200, cloud[resource]) if resource in cloud else arm(404)
        transport.request.side_effect = request
        result = certificate_image._reconcile_build(self.config, target, record, transport, mock.Mock(),
                                                     monotonic=self.time, sleep=self.time.sleep)
        self.assertEqual(set(cloud), {self.vm})
        self.assertEqual(result['imageDigest'], 'sha256:' + 'd' * 64)
        self.assertTrue(result['temporaryResourcesRemoved'])
        self.assertTrue(all(call.args[0] in ('GET', 'DELETE') for call in transport.request.call_args_list))

    def test_initial_orchestration_deploys_only_after_private_image_is_approved(self):
        with mock.patch.object(certificate_image, 'build_or_reuse', return_value='sha256:' + 'd' * 64) as build, \
             mock.patch.object(jobs, 'deploy_jobs') as deploy, \
             mock.patch.object(jobs, 'run_certificate_action', side_effect=[{'tls': True}, {'portal': True}]) as run:
            root = mock.Mock()
            root.attach_mock(build, 'build')
            root.attach_mock(deploy, 'deploy')
            root.attach_mock(run, 'action')
            result = jobs.complete_initial_certificates(self.config, self.outputs, mock.Mock(), self.save,
                                                        workspace=self.workspace)
        self.assertEqual([call[0] for call in root.mock_calls], ['build', 'deploy', 'action', 'action'])
        self.assertEqual(deploy.call_args.kwargs['account_email'], 'build@example.com')
        self.assertEqual(result, {'tls': {'tls': True}, 'portal': {'portal': True}, 'imageDigest': 'sha256:' + 'd' * 64})
