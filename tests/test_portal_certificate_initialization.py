"""合成ARMだけで限定追加・再開・保存境界と全体構築の証明書取得を確認する。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment import lifecycle
from deployment.helpers import portal_auth_certificate as material
from deployment.helpers import private_permissions
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import LocalPaths, private_parent
from operations import portal_auth_certificate as cli
from operations import portal_certificate_initialization as initial
from operations.common import OperationContext, OperationError
from configuration_fixtures import fixture
from test_portal_auth_certificate import record
from bicep_helpers import compile_bicep
from cleanup_fixtures import CleanupFixture


class SyntheticArm:
    """今回の3部品と既存依存だけを扱い、呼出しと受理済み配備を記録する。"""

    def __init__(self, ctx):
        self.ctx = ctx
        config = ctx.config
        self.groups = {k: '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames'][k]
                       for k in ('id', 'svc', 'maint')}
        self.vm = self.groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt'
        self.vault = self.groups['svc'] + '/providers/Microsoft.KeyVault/vaults/kv-' + config['resourceStem']
        self.principal = '33333333-3333-4333-8333-333333333333'
        self.calls = []
        self.stages = {}
        self.accepted = {}
        self.objects = {}
        self.unknown_put = None
        self.failure = None
        self.certificate = record(config)
        tags = {'product': config['productName'], 'environment': config['environmentName']}
        for group in self.groups.values():
            self.objects[group] = {'id': group, 'tags': tags}
        self.objects[self.vm] = {'id': self.vm, 'type': 'Microsoft.Compute/virtualMachines',
            'identity': {'principalId': self.principal}, 'properties': {'provisioningState': 'Succeeded'}}
        self.objects[self.vault] = {'id': self.vault, 'type': 'Microsoft.KeyVault/vaults',
            'properties': {'provisioningState': 'Succeeded', 'enableRbacAuthorization': True}}
        portal = self.groups['svc'] + '/providers/Microsoft.App/containerApps/ca-' + config['resourceStem'] + '-portal'
        self.objects[portal] = {'id': portal, 'type': 'Microsoft.App/containerApps', 'properties': {
            'provisioningState': 'Succeeded', 'managedEnvironmentId': self.groups['svc'] + '/providers/Microsoft.App/managedEnvironments/cae-' + config['resourceStem']}}
        tls = self.vm + '/runCommands/configure-public-certificate'
        self.objects[tls] = {'id': tls, 'properties': {'source': {'script': 'PowerShell 7.6.7'},
            'instanceView': {'executionState': 'Succeeded', 'exitCode': 0}}}

    def request(self, method, url, body=None, headers=None):
        path = url.split('?', 1)[0]
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == 'POST' and path.endswith('/whatIf'):
            stage = self.stages[path[:-7]]
            changes = [{'resourceId': resource, 'changeType': 'Create', 'after': {'id': resource, **copy.deepcopy(desired)}}
                       for resource, desired in stage['resources'].items()]
            return {'statusCode': 200, 'body': {'status': 'Succeeded', 'properties': {'changes': changes}}}
        if method == 'PUT' and path in self.stages:
            stage = self.stages[path]
            self.accepted[path] = {'id': path, 'properties': {'provisioningState': 'Succeeded'}}
            self.objects.update({resource: {'id': resource, **copy.deepcopy(desired)} for resource, desired in stage['resources'].items()})
            if stage['name'] == self.unknown_put:
                self.unknown_put = None
                raise RuntimeError('synthetic private response must not be printed')
            return {'statusCode': 202}
        if method == 'GET' and path in self.accepted:
            observed = copy.deepcopy(self.accepted[path])
            if self.stages[path]['name'] == self.failure:
                observed['properties']['provisioningState'] = 'Failed'
            return {'statusCode': 200, 'body': observed}
        if method == 'GET' and path == self.vault + '/providers/Microsoft.Authorization/roleAssignments':
            return {'statusCode': 200, 'body': {'value': [{'properties': {'principalId': self.principal,
                'roleDefinitionId': '/providers/Microsoft.Authorization/roleDefinitions/' + initial.CERTIFICATES_OFFICER}}]}}
        if method == 'GET' and path in self.objects:
            observed = copy.deepcopy(self.objects[path])
            if path.endswith('/runCommands/initialize-portal-auth-certificate'):
                observed['properties']['instanceView'] = {'executionState': 'Succeeded', 'exitCode': 0,
                    'output': material.MARKER + json.dumps(self.certificate)}
            return {'statusCode': 200, 'body': observed}
        if method == 'GET':
            return {'statusCode': 404}
        raise AssertionError('Unexpected synthetic mutation')


class InitializationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='portal-init-fixture-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = normalize_configuration(fixture())
        self.workspace = LocalPaths(self.root, self.config['environmentName'])
        self.ctx = OperationContext(self.workspace.params, self.config, {}, self.workspace)
        self.arm = SyntheticArm(self.ctx)
        self.ctx.transport = self.arm
        self.ctx.state = {'outputs': {'managementVm': {'id': self.arm.vm}}, 'serviceResourceIds': {'keyVault': self.arm.vault}}
        self.original_stages = initial.stages
        self.enterContext(mock.patch.object(initial, 'stages', side_effect=self.capture_stages))
        self.enterContext(mock.patch.dict(os.environ, {'PYTHONDONTWRITEBYTECODE': '1'}))
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def compiler(self, source):
        kinds = {'apps/portal/deploy/auth-identity.bicep': ['Microsoft.ManagedIdentity/userAssignedIdentities'],
            'infra/modules/portal-auth-key-operations.bicep': ['Microsoft.Authorization/roleDefinitions', 'Microsoft.Authorization/roleAssignments'],
            'infra/modules/portal-auth-certificate.bicep': ['Microsoft.Compute/virtualMachines/runCommands']}
        return {'resources': [{'type': kind} for kind in kinds[source]]}

    def capture_stages(self, *args):
        stages = self.original_stages(*args)
        self.arm.stages.update({stage['deploymentId']: stage for stage in stages})
        return stages

    def run_initial(self, execute=False):
        return initial.initialize(self.ctx, execute=execute, compiler=self.compiler)

    def receipt(self):
        return json.loads((self.workspace.state / 'portal-auth-initialization.json').read_text(encoding='utf-8'))

    def writes(self):
        return [call for call in self.arm.calls if call[0] in ('PUT', 'PATCH', 'DELETE')]

    def test_plan_has_no_put_and_execute_collects_with_no_entra_input(self):
        plan = self.run_initial()
        self.assertEqual(plan['resourcesPlanned'], 4)
        self.assertEqual(self.writes(), [])
        saved_id = self.receipt()['executionId']
        result = self.run_initial(True)
        self.assertEqual(result['phase'], 'collected')
        self.assertEqual(self.receipt()['executionId'], saved_id)
        self.assertEqual(len(self.writes()), 3)
        self.assertEqual(material.read_certificate_state(self.config, self.workspace)['current'], self.arm.certificate)
        public = (self.workspace.root / 'generated/portal-auth-public.cer').read_text(encoding='ascii')
        self.assertIn('BEGIN CERTIFICATE', public)
        self.assertNotIn('PRIVATE KEY', public)
        self.run_initial(True)
        self.assertEqual(len(self.writes()), 3)

    def test_unknown_accepted_put_is_observed_without_resubmitting(self):
        self.arm.unknown_put = 'key-operations'
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.assertEqual(len(self.writes()), 2)
        self.assertEqual(self.receipt()['deployments']['key-operations']['status'], 'submitted')
        self.run_initial(True)
        self.assertEqual(len(self.writes()), 3)
        self.assertEqual(len({call[1] for call in self.writes()}), 3)

    def test_failed_deployment_stays_stopped_without_new_put(self):
        self.arm.failure = 'certificate'
        with self.assertRaises(OperationError):
            self.run_initial(True)
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.assertEqual(len(self.writes()), 3)
        self.assertFalse((self.workspace.state / 'portal-auth-certificate.json').exists())

    def test_public_save_failure_resumes_collection_without_new_put(self):
        with mock.patch.object(material, 'save_public_certificate', side_effect=OSError('synthetic save failure')):
            with self.assertRaises(OperationError):
                self.run_initial(True)
        self.assertEqual(self.receipt()['phase'], 'planned')
        self.assertTrue((self.workspace.state / 'portal-auth-certificate.json').exists())
        self.run_initial(True)
        self.assertEqual(self.receipt()['phase'], 'collected')
        self.assertEqual(len(self.writes()), 3)

    def test_checkpoint_refuses_input_source_and_identity_changes(self):
        self.run_initial()
        original = self.config['portal']['certificate']['validityMonths']
        self.config['portal']['certificate']['validityMonths'] = original + 1
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.config['portal']['certificate']['validityMonths'] = original
        self.arm.objects[self.arm.vm]['identity']['principalId'] = '44444444-4444-4444-8444-444444444444'
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.arm.objects[self.arm.vm]['identity']['principalId'] = self.arm.principal
        with mock.patch.object(initial, '_compiler', side_effect=AssertionError('Not used')):
            def changed(source):
                return {**self.compiler(source), 'metadata': {'syntheticSourceChange': True}}
            with self.assertRaises(OperationError):
                initial.initialize(self.ctx, execute=True, compiler=changed)
        self.assertEqual(self.writes(), [])

    def test_invalid_checkpoint_and_rotation_do_not_mutate(self):
        self.run_initial()
        original = self.receipt()
        for change in ({'deployments': []}, {'phase': 'unknown'}, {'deployments': {'unknown': {}}}, {'phase': 'collected'}):
            value = {**original, **change}
            self.ctx.save_private(self.workspace.state / 'portal-auth-initialization.json', value, overwrite=True)
            with self.assertRaises(OperationError):
                self.run_initial(True)
        self.ctx.save_private(self.workspace.state / 'portal-auth-initialization.json', original, overwrite=True)
        state = {key: self.config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        self.ctx.save_private(self.workspace.state / 'portal-auth-certificate.json', {**state, 'current': self.arm.certificate, 'phase': 'prepared'})
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.assertEqual(self.writes(), [])

    def test_unready_runtime_or_wrong_vault_rbac_stops_before_what_if(self):
        tls = self.arm.objects[self.arm.vm + '/runCommands/configure-public-certificate']
        tls['properties']['instanceView']['executionState'] = 'Failed'
        with self.assertRaises(OperationError):
            self.run_initial(True)
        tls['properties']['instanceView']['executionState'] = 'Succeeded'
        self.arm.objects[self.arm.vault]['properties']['enableRbacAuthorization'] = False
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.assertTrue(all(call[0] == 'GET' for call in self.arm.calls))

    def test_changed_existing_current_is_not_overwritten(self):
        self.run_initial(True)
        original = material.read_certificate_state(self.config, self.workspace)['current']
        self.arm.certificate = record(self.config, 'b')
        with self.assertRaises(OperationError):
            self.run_initial(True)
        self.assertEqual(material.read_certificate_state(self.config, self.workspace)['current'], original)
        self.assertEqual(len(self.writes()), 3)

    def test_private_lock_and_explicit_runtime_refuse_links_and_unsafe_files(self):
        private_parent(self.workspace.state / 'latest.json', self.root)
        lock = self.workspace.root / '.operations.lock'
        with initial.locked(self.ctx):
            private_permissions.check_private_permissions(lock)
            with self.assertRaises(OperationError):
                with initial.locked(self.ctx):
                    self.fail('Concurrent operation entered')
        if os.name != 'nt':
            lock.chmod(0o644)
            with self.assertRaises(ValueError):
                with initial.locked(self.ctx):
                    self.fail('Unsafe existing lock was accepted')
            alias = self.root / 'runtime-link'
            alias.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError), mock.patch.object(initial, 'load_configuration') as load:
                initial.load_context(self.workspace.params, alias)
            load.assert_not_called()
        self.workspace.params.write_text('{}', encoding='utf-8')
        self.workspace.params.chmod(0o644)
        with mock.patch.object(initial, 'check_private_permissions', side_effect=ValueError('Unsafe input')), \
                mock.patch.object(initial, 'load_configuration') as load:
            with self.assertRaises(ValueError):
                initial.load_context(self.workspace.params, self.root)
            load.assert_not_called()

    def test_initial_cli_rejects_unrelated_acceptance_flags_before_loading(self):
        for flag in ('--login-verified', '--resume-failed-worker'):
            with mock.patch.object(initial, 'load_context') as load, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['initialize', '--config', 'synthetic-params', flag]), 1)
            load.assert_not_called()


class WhatIfTests(unittest.TestCase):
    def setUp(self):
        self.resource = '/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture/providers/Microsoft.Authorization/roleAssignments/22222222-2222-4222-8222-222222222222'
        self.desired = {'type': 'Microsoft.Authorization/roleAssignments', 'properties': {
            'principalId': '33333333-3333-4333-8333-333333333333', 'principalType': 'ServicePrincipal', 'roleDefinitionId': self.resource.replace('roleAssignments', 'roleDefinitions')}}
        self.change = {'resourceId': self.resource, 'changeType': 'Create', 'after': {'id': self.resource, **copy.deepcopy(self.desired)}}
        self.document = {'status': 'Succeeded', 'properties': {'changes': [self.change]}}

    def verify(self, document):
        return initial.verify_what_if(document, {self.resource: self.desired})

    def test_nested_payload_accepts_exact_create_and_nochange_only(self):
        self.assertEqual(self.verify(self.document), {'resourcesVerified': 1})
        self.change['changeType'] = 'NoChange'
        self.change['before'] = self.change.pop('after')
        self.verify(self.document)

    def test_extra_write_modify_delete_duplicate_missing_and_diagnostics_are_rejected(self):
        bad = []
        for action in ('Modify', 'Delete', 'Deploy', 'Unsupported'):
            bad.append({'status': 'Succeeded', 'properties': {'changes': [{**self.change, 'changeType': action}]}})
        bad += [{'status': 'Succeeded', 'changes': []}, {'status': 'Succeeded', 'changes': [self.change, self.change]},
                {'status': 'Succeeded', 'changes': [self.change, {**self.change, 'resourceId': self.resource + '-other'}]},
                {**self.document, 'diagnostics': ['synthetic']},
                {'status': 'Succeeded', 'changes': [{**self.change, 'unsupportedReason': 'synthetic'}]}]
        for document in bad:
            with self.subTest(document=document), self.assertRaises(OperationError):
                self.verify(document)
        self.change['after']['properties']['principalId'] = '44444444-4444-4444-8444-444444444444'
        with self.assertRaises(OperationError):
            self.verify(self.document)

    def test_worker_json_format_is_allowed_but_other_worker_and_hidden_inputs_are_rejected(self):
        desired = {'type': 'Microsoft.Compute/virtualMachines/runCommands', 'properties': {
            'source': {'script': 'synthetic\nworker'}, 'parameters': [{'name': 'PortalAuthConfigJson', 'value': '{"a":1,"b":2}'}]}}
        body = {'id': self.resource, **copy.deepcopy(desired)}
        body['properties']['source']['script'] = 'synthetic\r\nworker'
        body['properties']['parameters'][0]['value'] = '{ "b": 2, "a": 1 }'
        self.assertTrue(initial._matches(body, self.resource, desired))
        body['properties']['protectedParameters'] = [{'name': 'other', 'value': 'synthetic'}]
        self.assertFalse(initial._matches(body, self.resource, desired))
        body['properties'].pop('protectedParameters')
        body['properties']['source']['script'] += '\nother'
        self.assertFalse(initial._matches(body, self.resource, desired))

    def test_wider_key_permissions_are_rejected(self):
        desired = {'type': 'Microsoft.Authorization/roleDefinitions', 'properties': {'assignableScopes': [self.resource],
            'permissions': [{'actions': [], 'notActions': [], 'dataActions': ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/update/action'], 'notDataActions': []}]}}
        body = {'id': self.resource, **copy.deepcopy(desired)}
        self.assertTrue(initial._matches(body, self.resource, desired))
        body['properties']['permissions'][0]['dataActions'].append('Microsoft.KeyVault/vaults/secrets/*')
        self.assertFalse(initial._matches(body, self.resource, desired))

    def test_null_empty_permissions_and_missing_principal_type_do_not_relax_real_readback(self):
        role = {'type': 'Microsoft.Authorization/roleDefinitions', 'properties': {'permissions': [{
            'actions': [], 'notActions': [], 'dataActions': ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/update/action'], 'notDataActions': []}]}}
        body = {'id': self.resource, **copy.deepcopy(role)}
        for field in ('actions', 'notActions', 'notDataActions'):
            body['properties']['permissions'][0][field] = None
        self.assertTrue(initial._matches(body, self.resource, role))
        body['properties']['permissions'][0]['actions'] = ['*']
        self.assertFalse(initial._matches(body, self.resource, role))
        self.change['after']['properties'].pop('principalType')
        self.verify(self.document)
        self.assertFalse(initial._matches(self.change['after'], self.resource, self.desired))
        self.change['after']['properties']['principalType'] = 'User'
        with self.assertRaises(OperationError):
            self.verify(self.document)

    def test_polling_refuses_external_or_other_subscription_urls_and_hides_errors(self):
        config = {'subscriptionId': '11111111-1111-4111-8111-111111111111'}
        stage = {'name': 'identity', 'deploymentId': self.resource, 'body': {'properties': {}}, 'resources': {self.resource: self.desired}}
        for url in ('https://example.invalid/result?api-version=2022-09-01',
                    'https://management.azure.com/subscriptions/22222222-2222-4222-8222-222222222222/providers/Microsoft.Resources/locations/test/operationresults/abc?api-version=2022-09-01',
                    'https://management.azure.com/subscriptions/22222222-2222-4222-8222-222222222222/operationresults/abc?api-version=2022-09-01',
                    'https://management.azure.com/subscriptions/' + config['subscriptionId'] + '/resourceGroups/rg-fixture/operationresults/abc?api-version=2022-09-01',
                    'https://management.azure.com/subscriptions/' + config['subscriptionId'] + '/operationresults/abc/other?api-version=2022-09-01'):
            ctx = mock.Mock(config=config, workspace=SimpleNamespace(root=Path('unused')))
            ctx.request.return_value = {'statusCode': 202, 'headers': {'Location': url}}
            with mock.patch.object(initial, '_save'), self.assertRaises(OperationError):
                initial.what_if(ctx, stage, sleep=lambda _: None)
            ctx.transport.request.assert_not_called()
        ctx.request.return_value['headers']['Location'] = 'https://management.azure.com/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Resources/locations/test/operationresults/abc?api-version=2022-09-01'
        ctx.transport.request.side_effect = RuntimeError('synthetic private response')
        with self.assertRaisesRegex(OperationError, '^The initialization what-if response is unknown$'):
            initial.what_if(ctx, stage, sleep=lambda _: None)

    def test_arm_polling_context_is_replayed_only_on_verified_result_url(self):
        config = {'subscriptionId': '11111111-1111-4111-8111-111111111111'}
        prefix = 'https://management.azure.com/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Resources/locations/test/operationresults/fixture?'
        context = {'api-version': initial.ARM_API, 'c': 'fixture', 'h': 'fixture', 's': '{"fixture": "opaque context"}', 't': '123'}
        stage = {'name': 'identity', 'deploymentId': self.resource, 'body': {'properties': {}}, 'resources': {self.resource: self.desired}}
        ctx = SimpleNamespace(config=config, workspace=SimpleNamespace(root=Path('unused')),
            request=mock.Mock(return_value={'statusCode': 202, 'headers': {}}),
            transport=SimpleNamespace(request=mock.Mock(return_value={'statusCode': 200, 'body': self.document})))
        for prefix in (prefix, 'https://management.azure.com/subscriptions/' + config['subscriptionId'] + '/operationresults/fixture?'):
            url = prefix + urlencode(context)
            ctx.request.return_value['headers']['Location'] = url
            ctx.transport.request.reset_mock()
            with mock.patch.object(initial, '_save'):
                self.assertEqual(initial.what_if(ctx, stage, sleep=lambda _: None), {'resourcesVerified': 1})
            ctx.transport.request.assert_called_once_with('GET', url)
        for query in ({**context, 'other': 'fixture'}, {**context, 'c': ''}, {**context, 'h': 'x' * 4097}, {**context, 's': '\n'}):
            ctx.request.return_value['headers']['Location'] = prefix + urlencode(query)
            ctx.transport.request.reset_mock()
            with mock.patch.object(initial, '_save'), self.assertRaises(OperationError):
                initial.what_if(ctx, stage, sleep=lambda _: None)
            ctx.transport.request.assert_not_called()
        ctx.request.return_value['headers']['Location'] = prefix + urlencode(context) + '&c=fixture'
        ctx.transport.request.reset_mock()
        with mock.patch.object(initial, '_save'), self.assertRaises(OperationError):
            initial.what_if(ctx, stage, sleep=lambda _: None)
        ctx.transport.request.assert_not_called()


class ExplicitRuntimeTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.workspace = LocalPaths(Path(self.temp.name), 'test')
        self.workspace.ensure()
        for path, value in ((self.workspace.params, self.raw), (self.workspace.state / 'latest.json', self.state)):
            path.write_text(json.dumps(value), encoding='utf-8')
            path.chmod(0o600)
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def test_explicit_runtime_loads_real_configuration_and_state_without_copy_or_authentication(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('operations.common.ArmTransport') as arm:
            ctx = initial.load_context(self.workspace.params, self.workspace.repository)
        self.assertEqual(ctx.config_path, self.workspace.params)
        self.assertEqual(ctx.workspace.repository, self.workspace.repository)
        self.assertEqual(ctx.state, self.state)
        self.assertEqual(sorted(path.name for path in self.workspace.state.iterdir()), ['latest.json'])
        arm.assert_not_called()

    def test_saved_azd_selection_must_match_explicit_runtime(self):
        selection = self.workspace.repository / '.azure/config.json'
        private_parent(selection, self.workspace.repository)
        selection.write_text('{"defaultEnvironment":"other"}', encoding='utf-8')
        selection.chmod(0o600)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('operations.common.ArmTransport') as arm:
            with self.assertRaises(OperationError):
                initial.load_context(self.workspace.params, self.workspace.repository)
        arm.assert_not_called()


class FullConstructionTests(unittest.TestCase):
    """正式postprovisionからcollectorを置換せず通し、構築・保存・失敗を照合する。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='portal-core-fixture-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = normalize_configuration(fixture())
        self.paths = LocalPaths(self.root, self.config['environmentName'])
        self.certificate = record(self.config)
        vm = '/subscriptions/' + self.config['subscriptionId'] + '/resourceGroups/' + self.config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + self.config['resourceStem'] + '-mgmt'
        self.command = vm + '/runCommands/initialize-portal-auth-certificate'
        self.outputs = {'managementVm': {'id': vm}, 'portal': {'authCertificateRunCommandId': self.command},
                        'resourceGroups': self.config['resourceGroupNames'], 'endpoints': {'public': 'https://fixture.example.invalid'}}
        self.response = {'statusCode': 200, 'body': {'id': self.command, 'properties': {'instanceView': {
            'executionState': 'Succeeded', 'exitCode': 0, 'output': material.MARKER + json.dumps(self.certificate)}}}}
        self.transport = mock.Mock()
        self.transport.request.side_effect = lambda method, path: copy.deepcopy(self.response)
        self.enterContext(mock.patch.object(lifecycle, 'ROOT', self.root))
        self.enterContext(mock.patch.object(material, 'local_paths', return_value=self.paths))
        self.enterContext(mock.patch.object(lifecycle, 'ArmTransport', return_value=self.transport))
        self.enterContext(mock.patch.object(lifecycle, '_save_deployment_state', side_effect=lambda config, outputs: lifecycle.protected_json(self.paths.state / 'latest.json', outputs)))
        self.enterContext(mock.patch.object(lifecycle, '_read_initial_serial_deployment_input', return_value=''))
        self.enterContext(mock.patch.object(lifecycle, '_bind_management_serial_password'))
        self.enterContext(mock.patch.object(lifecycle, 'complete_core_network_upgrade'))
        self.ingress = self.enterContext(mock.patch.object(lifecycle, 'configure_grpc_endpoint', return_value={'enabled': False}))
        self.enterContext(mock.patch.object(lifecycle, 'configure_public_dns', return_value={'status': 'configured'}))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        if os.name == 'nt':
            self.enterContext(mock.patch.object(private_permissions, '_windows_owner_acl'))

    def postprovision(self):
        deployment = {'properties': {'provisioningState': 'Succeeded', 'outputs': {key: {'value': value} for key, value in self.outputs.items()}}}
        with mock.patch.object(lifecycle, 'run', side_effect=[{'CORE_DEPLOYMENT_NAME': 'synthetic-core'}, deployment]):
            lifecycle.postprovision(self.config)

    def test_full_construction_collects_and_reexecution_preserves_current(self):
        self.assertNotIn('authentication', self.config['portal'])
        self.postprovision()
        original = material.read_certificate_state(self.config, self.paths)
        self.postprovision()
        self.assertEqual(material.read_certificate_state(self.config, self.paths), original)
        self.assertTrue((self.paths.root / 'generated/portal-auth-public.cer').exists())
        self.assertEqual(self.transport.request.call_args_list, [mock.call('GET', self.command + '?api-version=' + material.COMPUTE_API + '&$expand=instanceView')] * 2)
        self.assertIn('"deployed": true', self.output.getvalue())

    def test_failed_initial_issuance_retains_core_state_and_stops_success_and_cleans_input(self):
        lifecycle.protected_json(self.paths.parameters, {'syntheticInput': 'synthetic'})
        self.response['body']['properties']['instanceView'].update(executionState='Failed', exitCode=1)
        with self.assertRaises(ValueError):
            self.postprovision()
        self.assertTrue((self.paths.state / 'latest.json').exists())
        self.assertFalse((self.paths.state / 'portal-auth-certificate.json').exists())
        self.assertFalse(self.paths.parameters.exists())
        self.ingress.assert_not_called()
        self.assertNotIn('"deployed": true', self.output.getvalue())

    def test_reconstruction_refuses_different_current_without_replacing_it(self):
        self.postprovision()
        original = material.read_certificate_state(self.config, self.paths)
        self.response['body']['properties']['instanceView']['output'] = material.MARKER + json.dumps(record(self.config, 'b'))
        with self.assertRaises(ValueError):
            self.postprovision()
        self.assertEqual(material.read_certificate_state(self.config, self.paths), original)


class FullConstructionBicepTests(unittest.TestCase):
    def test_core_uses_same_three_modules_and_passes_initial_certificate_config_without_entra(self):
        core = compile_bicep('infra/main.bicep')
        sources = {'portal-auth-identity': 'apps/portal/deploy/auth-identity.bicep',
                   'portal-auth-key-operations': 'infra/modules/portal-auth-key-operations.bicep',
                   'portal-auth-certificate': 'infra/modules/portal-auth-certificate.bicep'}
        count = 0
        for name, source in sources.items():
            module = next(resource for resource in core['resources'] if resource['name'] == "[format('{0}-" + name + "', variables('stem'))]")
            self.assertEqual(module['condition'], "[variables('deployCore')]")
            compiled = compile_bicep(source)
            self.assertEqual(module['properties']['template'], compiled)
            resources = compiled['resources']
            count += len(resources)
        self.assertEqual(count, 4)
        command = next(resource for resource in core['resources'] if resource['name'] == "[format('{0}-portal-auth-certificate', variables('stem'))]")
        parameters = command['properties']['parameters']
        self.assertEqual(set(parameters['config']['value']), {'vaultName', 'certificateName', 'validityMonths'})
        self.assertEqual(parameters['config']['value']['certificateName'], "[variables('portalConfig').certificate.certificateName]")
        self.assertIn('authCertificateRunCommandId', core['outputs']['portal']['value'])
        dependencies = json.dumps(command['dependsOn'])
        for prerequisite in ('certificate', 'portal-auth-key-operations', 'network-bindings', 'service-bindings', 'management-audit'):
            self.assertIn(prerequisite, dependencies)


if __name__ == '__main__':
    unittest.main()
