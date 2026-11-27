"""Recorded failed-stage repair without adopting arbitrary live policy/source."""
import copy
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'),
               str(Path(__file__).resolve().parents[1] / 'apps/apim/deploy')]
import test_historical_publication as historical
import test_publication_source_generation as generation
from test_publish_apis import ok

publish = generation.publish


class RecordedFailedStageTests(unittest.TestCase):
    def fixture(self, *, new_fragment=False, mcp_tool=False):
        base, plan, authentication, transport, checkpoints, fragment_id, old_xml = historical.HistoricalPublicationTests('runTest').fixture()
        if mcp_tool:
            name = 'synthetic-mcp'
            api_id = plan['apimId'] + '/apis/' + name
            tool_id = api_id + '/tools/synthetic-tool'
            properties = {'type': 'mcp', 'path': name, 'protocols': ['http', 'https'], 'subscriptionRequired': True,
                          'subscriptionKeyParameterNames': {'header': 'api-key', 'query': 'subscription-key'}}
            tool = {'operationId': plan['publishedApiIds'][0] + '/operations/invoke', 'displayName': 'Synthetic tool'}
            seed = base64.b64encode(b'mcp\n{}\n{}').decode()
            policy = ('<policies><inbound><set-variable name="gatewayApiSettingsSource" value="' + seed + '" />'
                      '<include-fragment id="core-api-settings" fragment-id="core-api-settings" /><base /></inbound>'
                      '<backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')
            for source in (base.state['apiPublicationSourceCheckpoint']['source'],
                           base.template['outputs']['entraPublicationSource']['value'], transport.source):
                source['apis'].append({'name': name, 'operations': [], 'products': ['tool-users'], 'properties': copy.deepcopy(properties)})
                source['apiChildren'].append({'suffix': '/apis/' + name + '/tools/synthetic-tool', 'properties': copy.deepcopy(tool)})
                source['policies'].append({'suffix': '/apis/' + name + '/policies/policy', 'value': policy})
            plan['parameters']['apiDefinitions']['value'][0]['mcp'] = {'name': name, 'path': name}
            plan['publishedApiIds'].append(api_id)
            plan['entraAuthentication']['apiNames'].append(name)
            plan['parameters']['entraAuthentication']['value'] = copy.deepcopy(plan['entraAuthentication'])
            authentication['previousEntra']['apiNames'].append(name)
            transport.refresh(api_id, properties)
            transport.refresh(api_id + '/policies/policy', {'format': 'rawxml', 'value': policy})
            transport.refresh(api_id + '/tools', value=[{'id': tool_id}])
            transport.refresh(tool_id, tool)
            transport.refresh(api_id + '/schemas', value=[])
            transport.refresh(api_id + '/operations', value=[])
            transport.refresh(api_id + '/products', value=[{'id': plan['apimId'] + '/products/tool-users'}, {'id': transport.open_id}])
            transport.refresh(api_id + '/revisions', value=[{'apiId': '/apis/' + name + ';rev=1', 'apiRevision': '1',
                                                          'isCurrent': True, 'isOnline': True}])
            transport.members.add(api_id)
            base.template['resources']['mcp_tool'] = {'type': 'Microsoft.ApiManagement/service/apis/tools',
                                                     'name': 'synthetic/synthetic-mcp/synthetic-tool', 'apiVersion': publish.APIM_API}
        native_before = {}
        native_ids = {plan['apimId'] + '/products/' + name for api in base.state['apiPublicationSourceCheckpoint']['source']['apis']
                      for name in api['products']}
        for resource_id in sorted(native_ids | {rid + '/policies/policy' for rid in native_ids}):
            raw = resource_id.endswith('/policies/policy')
            native_before[resource_id] = {
                'path': resource_id + '?api-version=' + publish.entra_publication.APIM_API + ('&format=rawxml' if raw else ''),
                'headers': {'Accept': 'application/json'} if raw else None,
                'response': copy.deepcopy(transport.inventory.get(resource_id, {'statusCode': 404, 'headers': {}, 'body': {}}))}
        attempted_template = copy.deepcopy(base.template)
        source = attempted_template['outputs']['entraPublicationSource']['value']
        operation = next(entry for entry in source['policies'] if '/operations/' in entry['suffix'])
        operation['value'] = operation['value'].replace('<outbound><base /></outbound>',
            '<outbound><base /><set-header name="x-synthetic-recovery" exists-action="override"><value>attempted</value></set-header></outbound>')
        attempted_template['resources']['operation_policy'] = {
            'type': 'Microsoft.ApiManagement/service/apis/operations/policies', 'apiVersion': publish.APIM_API,
            'name': 'synthetic/model/invoke/policy'}
        if new_fragment:
            source['policies'].append({'suffix': '/policyFragments/core-known-created',
                                      'value': '<fragment><set-variable name="syntheticCreated" value="known" /></fragment>'})
            attempted_template['resources']['created_fragment'] = {
                'type': 'Microsoft.ApiManagement/service/policyFragments', 'apiVersion': publish.APIM_API,
                'name': 'core-known-created'}
        principal = transport.inventory[plan['apimId']]['body']['identity']['principalId']
        publication, attempted_source, browser, generation_id = publish.generate_publication_source(
            base.config, plan, attempted_template, transport, identity_principal_id=principal, sleep=lambda _: None)
        parameters = publish.publication_parameters(attempted_template, plan['parameters'])
        tags = {'azd-env-name': base.config['environmentName'], 'azd-project-name': publish.PROJECT_NAME}
        path = plan['deploymentId'] + '?api-version=' + publish.DEPLOYMENT_API
        intent = {'method': 'PUT', 'path': path, 'headers': None,
                  'body': {'tags': tags, 'properties': {'mode': 'Incremental', 'template': publication, 'parameters': parameters}}}
        evidence = {'template': attempted_template, 'intent': intent, 'nativeProductsBefore': native_before,
                    'response': {'method': 'PUT', 'path': path, 'response': {'statusCode': 202, 'headers': {}, 'body': None}}}
        failed = {'id': plan['deploymentId'], 'name': plan['deploymentId'].rsplit('/', 1)[-1],
                  'type': 'Microsoft.Resources/deployments', 'tags': tags,
                  'properties': {'provisioningState': 'Failed', 'mode': 'Incremental', 'parameters': parameters,
                                 'outputs': {}, 'templateHash': 'synthetic-attempted-code', 'correlationId': 'synthetic-failed-job'}}
        transport.inventory[plan['deploymentId']] = ok(failed)
        operations_id = plan['deploymentId'] + '/operations'
        operation_id = plan['apimId'] + operation['suffix']
        operations = [{'id': operations_id + '/synthetic-policy-operation',
                       'properties': {'provisioningState': 'Failed', 'provisioningOperation': 'Create',
                                      'targetResource': {'id': operation_id,
                                          'resourceType': 'Microsoft.ApiManagement/service/apis/operations/policies'}}}]
        if mcp_tool:
            operations.append({'id': operations_id + '/synthetic-mcp-parent', 'properties': {
                'provisioningState': 'Succeeded', 'provisioningOperation': 'Create',
                'targetResource': {'id': api_id, 'resourceType': 'Microsoft.ApiManagement/service/apis'}}})
        transport.inventory[operations_id] = ok({'value': operations})
        original = transport.request

        def request(method, request_path, body=None, headers=None):
            if method == 'POST' and request_path.split('?', 1)[0] == plan['deploymentId'] + '/exportTemplate':
                transport.calls.append((method, request_path, copy.deepcopy(body), copy.deepcopy(headers)))
                return ok({'template': copy.deepcopy(publication)})
            return original(method, request_path, body, headers)

        transport.request = request
        transport.members.clear()
        transport.sync_members()
        if mcp_tool:
            transport.refresh(api_id + '/tools', value=[])
            transport.inventory.pop(tool_id)
        # The successful fragment is already current; the failed operation
        # policy remains exactly old. No actual policy becomes an expectation.
        attempted_fragment = next(entry['value'] for entry in attempted_source['policies']
                                  if plan['apimId'] + entry['suffix'] == fragment_id)
        transport.refresh(fragment_id, {'format': 'rawxml', 'value': attempted_fragment})
        fixed_template = copy.deepcopy(attempted_template)
        fixed_operation = next(entry for entry in fixed_template['outputs']['entraPublicationSource']['value']['policies']
                               if entry['suffix'] == operation['suffix'])
        fixed_operation['value'] = fixed_operation['value'].replace('<value>attempted</value>', '<value>corrected</value>')
        base.template = fixed_template
        transport.source = copy.deepcopy(fixed_template['outputs']['entraPublicationSource']['value'])
        if mcp_tool:
            original_stage = transport.install_stage
            def stage_with_tool():
                original_stage()
                transport.refresh(api_id + '/tools', value=[{'id': tool_id}])
            transport.install_stage = stage_with_tool
        transport.calls.clear()
        return base, plan, authentication, transport, checkpoints, evidence, fragment_id, operation_id, generation_id

    def execute(self, base, plan, authentication, transport, checkpoints, evidence=None, *, restore_recorded_mcp_tool=False):
        return generation.SplitPublicationOrderingTests('runTest').execute(
            base, plan, authentication, transport, checkpoints, failed_closed_stage_attempt=evidence,
            restore_recorded_mcp_tool=restore_recorded_mcp_tool)

    def assert_no_resource_write(self, transport, checkpoints):
        self.assertEqual(checkpoints, [])
        self.assertTrue(all('-src-' in call[1] for call in transport.calls if call[0] not in ('GET', 'POST')))

    def test_explicit_recorded_recovery_stages_then_verifies_and_reopens_last(self):
        base, plan, authentication, transport, checkpoints, evidence, fragment_id, operation_id, _ = self.fixture()
        baseline = copy.deepcopy(base.state['apiPublicationSourceCheckpoint'])
        original_stage = transport.install_stage

        def stage():
            self.assertEqual(transport.members, set())
            self.assertEqual(base.state['apiPublicationSourceCheckpoint'], baseline)
            original_stage()

        transport.install_stage = stage
        result = self.execute(base, plan, authentication, transport, checkpoints, evidence)
        self.assertTrue(result['entraAuthentication']['managementPlaneVerified'])
        self.assertEqual(base.state['apiPublicationSourceCheckpoint'], baseline)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(transport.members, set(plan['publishedApiIds']))
        self.assertIn('<value>corrected</value>', transport.inventory[operation_id]['body']['properties']['value'])
        stage_at = next(i for i, call in enumerate(transport.calls) if call[0] == 'PUT' and call[1] == plan['deploymentId'])
        saved_at = next(i for i, call in enumerate(transport.calls) if call[0] == 'CHECKPOINT')
        self.assertLess(stage_at, saved_at)
        self.assertTrue(all(i > saved_at for i, call in enumerate(transport.calls)
                            if call[0] == 'PUT' and call[1].startswith(transport.open_id + '/apis/')))
        self.assertTrue(any(i < stage_at for i, call in enumerate(transport.calls)
                            if call[0] == 'GET' and call[1].split('?', 1)[0] == fragment_id))

    def test_normal_execution_still_rejects_mixed_policies_without_the_explicit_evidence(self):
        base, plan, authentication, transport, checkpoints, _, _, _, _ = self.fixture()
        with self.assertRaises(ValueError):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assert_no_resource_write(transport, checkpoints)

    def test_recorded_intent_parameters_template_receipt_and_source_job_tamper_stop_recovery(self):
        for fault in ('parameters', 'bool-as-int', 'template', 'receipt', 'job-state', 'generation-template', 'generation-state', 'generation-input-type'):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, evidence, _, _, generation_id = self.fixture()
                if fault == 'parameters':
                    evidence['intent']['body']['properties']['parameters']['publishApis']['value'] = False
                elif fault == 'bool-as-int':
                    evidence['intent']['body']['properties']['parameters']['publishApis']['value'] = 1
                elif fault == 'template':
                    evidence['intent']['body']['properties']['template']['variables']['synthetic-tamper'] = 'unrecorded'
                elif fault == 'receipt':
                    evidence['response']['response']['statusCode'] = 500
                elif fault == 'job-state':
                    transport.inventory[plan['deploymentId']]['body']['properties']['provisioningState'] = 'Running'
                elif fault == 'generation-template':
                    transport.generation.templates[generation_id]['variables']['synthetic-tamper'] = 'unrecorded'
                elif fault == 'generation-input-type':
                    transport.generation.records[generation_id]['properties']['parameters']['publishApis']['value'] = 1
                else:
                    transport.generation.records[generation_id]['properties']['provisioningState'] = 'Failed'
                with self.assertRaises(ValueError):
                    self.execute(base, plan, authentication, transport, checkpoints, evidence)
                self.assert_no_resource_write(transport, checkpoints)

    def test_unknown_policy_routing_backend_global_or_employee_member_stops_recovery(self):
        for fault in ('policy', 'routing', 'backend', 'global', 'member', 'foreign-policy-id', 'native-product'):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, evidence, fragment_id, _, _ = self.fixture()
                if fault == 'policy':
                    transport.refresh(fragment_id, {'value': '<fragment><set-variable name="synthetic" value="unknown" /></fragment>'})
                elif fault == 'routing':
                    resource = next(iter(transport.members), plan['publishedApiIds'][0])
                    transport.inventory[resource]['body']['properties']['path'] = 'unrecorded-route'
                elif fault == 'backend':
                    backend_id = plan['apimId'] + '/backends/' + plan['publishedApiIds'][0].rsplit('/', 1)[-1] + '-backend'
                    transport.refresh(backend_id, {'protocol': 'http', 'url': 'https://example.com/unrecorded'})
                    transport.refresh(plan['apimId'] + '/backends', value=[{'id': backend_id}])
                elif fault == 'global':
                    transport.refresh(transport.global_id, {'value': '<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'})
                elif fault == 'member':
                    transport.members.add(plan['publishedApiIds'][0])
                    transport.sync_members()
                elif fault == 'native-product':
                    transport.inventory[plan['apimId'] + '/products/tool-users']['body']['properties']['subscriptionRequired'] = False
                else:
                    transport.inventory[fragment_id]['body']['id'] = plan['apimId'] + '/policyFragments/unrecorded'
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    self.execute(base, plan, authentication, transport, checkpoints, evidence)
                self.assert_no_resource_write(transport, checkpoints)

    def test_known_old_absent_fragment_is_created_but_unknown_present_contents_are_rejected(self):
        for tampered in (False, True):
            with self.subTest(tampered=tampered):
                base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture(new_fragment=True)
                resource_id = plan['apimId'] + '/policyFragments/core-known-created'
                if tampered:
                    transport.refresh(resource_id, {'value': '<fragment><set-variable name="syntheticCreated" value="unknown" /></fragment>'})
                    with self.assertRaises(ValueError):
                        self.execute(base, plan, authentication, transport, checkpoints, evidence)
                    self.assert_no_resource_write(transport, checkpoints)
                else:
                    result = self.execute(base, plan, authentication, transport, checkpoints, evidence)
                    self.assertTrue(result['entraAuthentication']['managementPlaneVerified'])
                    self.assertIn('value="known"', transport.inventory[resource_id]['body']['properties']['value'])

    def test_failed_job_state_only_race_is_rechecked_before_any_stage_write(self):
        base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture()
        original, reads = transport.request, [0]
        def request(method, path, body=None, headers=None):
            if method == 'GET' and path.split('?', 1)[0] == plan['deploymentId']:
                reads[0] += 1
                if reads[0] == 3:
                    transport.inventory[plan['deploymentId']]['body']['properties']['provisioningState'] = 'Running'
            return original(method, path, body, headers)
        transport.request = request
        with self.assertRaises((ValueError, publish.DeploymentError)):
            self.execute(base, plan, authentication, transport, checkpoints, evidence)
        self.assertGreaterEqual(reads[0], 3)
        self.assert_no_resource_write(transport, checkpoints)

    def test_unrecorded_native_product_policy_is_rejected_before_and_after_stage(self):
        for after_stage in (False, True):
            with self.subTest(after_stage=after_stage):
                base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture()
                product_id = plan['apimId'] + '/products/tool-users'
                value = ('<policies><inbound><base /><set-variable name="gatewayCallerId" value="synthetic-other" /></inbound>'
                         '<backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')
                original = transport.install_stage
                def inject():
                    if after_stage:
                        original()
                    transport.refresh(product_id + '/policies/policy', {'format': 'rawxml', 'value': value})
                if after_stage:
                    transport.install_stage = inject
                else:
                    inject()
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    self.execute(base, plan, authentication, transport, checkpoints, evidence)
                self.assertEqual(checkpoints, [])
                self.assertEqual(transport.members, set())
                if not after_stage:
                    self.assert_no_resource_write(transport, checkpoints)

    def test_recorded_native_product_policy_is_preserved(self):
        base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture()
        resource_id = plan['apimId'] + '/products/tool-users/policies/policy'
        value = ('<policies><inbound><base /><set-variable name="syntheticKnown" value="before" /></inbound>'
                 '<backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')
        transport.refresh(resource_id, {'format': 'rawxml', 'value': value})
        evidence['nativeProductsBefore'][resource_id]['response'] = copy.deepcopy(transport.inventory[resource_id])
        result = self.execute(base, plan, authentication, transport, checkpoints, evidence)
        self.assertTrue(result['entraAuthentication']['managementPlaneVerified'])
        self.assertEqual(transport.inventory[resource_id]['body']['properties']['value'], value)

    def test_missing_recorded_mcp_tool_requires_second_optin_and_full_poststage_restoration(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture(mcp_tool=True)
                if not enabled:
                    with self.assertRaisesRegex(ValueError, 'tool/schema'):
                        self.execute(base, plan, authentication, transport, checkpoints, evidence)
                    self.assert_no_resource_write(transport, checkpoints)
                else:
                    result = self.execute(base, plan, authentication, transport, checkpoints, evidence, restore_recorded_mcp_tool=True)
                    self.assertTrue(result['entraAuthentication']['managementPlaneVerified'])
                    self.assertEqual(len(checkpoints), 1)
                    self.assertIn(plan['apimId'] + '/apis/synthetic-mcp/tools/synthetic-tool', transport.inventory)

    def test_missing_mcp_tool_poststage_failure_never_checkpoints_or_reopens(self):
        base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture(mcp_tool=True)
        original = transport.install_stage
        def still_missing():
            original()
            tool_id = plan['apimId'] + '/apis/synthetic-mcp/tools/synthetic-tool'
            transport.refresh(plan['apimId'] + '/apis/synthetic-mcp/tools', value=[])
            transport.inventory.pop(tool_id)
        transport.install_stage = still_missing
        with self.assertRaises(publish.DeploymentError):
            self.execute(base, plan, authentication, transport, checkpoints, evidence, restore_recorded_mcp_tool=True)
        self.assertEqual(checkpoints, [])
        self.assertEqual(transport.members, set())

    def test_missing_mcp_tool_ambiguous_source_parent_operation_or_inventory_is_rejected(self):
        for fault in ('properties', 'parent', 'multiple-tools', 'parent-not-updated', 'tool-operation', 'extra-tool', 'direct-present'):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture(mcp_tool=True)
                api_id = plan['apimId'] + '/apis/synthetic-mcp'
                tool_id = api_id + '/tools/synthetic-tool'
                source = base.template['outputs']['entraPublicationSource']['value']
                child = next(entry for entry in source['apiChildren'] if entry['suffix'].endswith('/tools/synthetic-tool'))
                operations = transport.inventory[plan['deploymentId'] + '/operations']['body']['value']
                if fault == 'properties':
                    child['properties']['displayName'] = 'unrecorded'
                elif fault == 'parent':
                    next(api for api in source['apis'] if api['name'] == 'synthetic-mcp')['properties']['path'] = 'unrecorded'
                elif fault == 'multiple-tools':
                    old = base.state['apiPublicationSourceCheckpoint']['source']
                    old['apiChildren'].append({'suffix': '/apis/synthetic-mcp/tools/another', 'properties': child['properties']})
                elif fault == 'parent-not-updated':
                    operations[:] = operations[:1]
                elif fault == 'tool-operation':
                    operations.append({'id': plan['deploymentId'] + '/operations/synthetic-tool-operation', 'properties': {
                        'provisioningState': 'Succeeded', 'provisioningOperation': 'Create',
                        'targetResource': {'id': tool_id, 'resourceType': 'Microsoft.ApiManagement/service/apis/tools'}}})
                elif fault == 'extra-tool':
                    transport.refresh(api_id + '/tools', value=[{'id': api_id + '/tools/unrecorded'}])
                else:
                    transport.refresh(tool_id, child['properties'])
                    transport.inventory[tool_id]['body']['id'] = api_id + '/tools/wrong-alias'
                with self.assertRaises((ValueError, publish.DeploymentError)):
                    self.execute(base, plan, authentication, transport, checkpoints, evidence, restore_recorded_mcp_tool=True)
                self.assert_no_resource_write(transport, checkpoints)

    def test_unknown_or_nonterminal_failed_operation_cannot_authorize_recovery(self):
        for fault in ('foreign-target', 'unknown-target', 'running', 'skipped', 'wrong-action', 'duplicate', 'foreign-page'):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, evidence, _, _, _ = self.fixture()
                body = transport.inventory[plan['deploymentId'] + '/operations']['body']
                entry = body['value'][0]
                if fault == 'foreign-target':
                    entry['properties']['targetResource']['id'] = plan['apimId'] + '-foreign/apis/model/operations/invoke/policies/policy'
                elif fault == 'unknown-target':
                    entry['properties']['targetResource']['id'] = plan['apimId'] + '/apis/unrecorded/operations/invoke/policies/policy'
                elif fault in ('running', 'skipped'):
                    entry['properties']['provisioningState'] = fault.title()
                elif fault == 'wrong-action':
                    entry['properties']['provisioningOperation'] = 'Delete'
                elif fault == 'duplicate':
                    body['value'].append(copy.deepcopy(entry))
                else:
                    body['nextLink'] = 'https://example.com/unrecorded'
                with self.assertRaises(ValueError):
                    self.execute(base, plan, authentication, transport, checkpoints, evidence)
                self.assert_no_resource_write(transport, checkpoints)


class ProtectedEvidenceLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        with mock.patch.dict(os.environ, {'GW_PHASE12_SOURCE_ROOT': str(root), 'GW_PHASE12_RUNTIME_ROOT': str(root),
                                         'BICEP_CLI': 'synthetic-bicep'}):
            spec = importlib.util.spec_from_file_location('synthetic_phase12_runner', root / 'scripts/operations/apim_phase12.py')
            cls.runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.runner)

    def fixture(self, folder):
        from deployment.helpers.paths import LocalPaths
        base, plan, _, transport, _, evidence, _, _, _ = RecordedFailedStageTests('runTest').fixture()
        workspace = LocalPaths(folder, 'synthetic')
        ctx = SimpleNamespace(workspace=workspace, output_path=workspace.checked)
        run = workspace.root / 'evidence/synthetic-failed-stage'
        run.mkdir(parents=True)
        before = {'input': json.dumps(base.config).encode(), 'core-state': json.dumps(base.state).encode(),
                  'checkpoint': json.dumps(base.state['apiPublicationSourceCheckpoint']).encode()}
        for name, value in before.items():
            (run / (name + '-before.json')).write_text(value.decode(), encoding='utf-8')
        for name, value in (('validated-template.json', evidence['template']),
                            ('native-product-before.json', evidence['nativeProductsBefore']),
                            ('00123-write-intent.json', evidence['intent']), ('00123-response.json', evidence['response']),
                            ('run-failure.json', {'stage': 'applying', 'lastMutationAttempt':
                                                 {'method': 'PUT', 'path': evidence['intent']['path']}})):
            (run / name).write_text(json.dumps(value), encoding='utf-8')
        return ctx, run, plan, before, evidence, transport

    def test_explicit_protected_record_is_loaded_without_any_arm_calls(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(self.runner, 'check_private_permissions'):
            ctx, run, plan, before, evidence, transport = self.fixture(Path(folder))
            hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()}
            (run / 'immutable-before-sha256.json').write_text(json.dumps(hashes), encoding='utf-8')
            self.assertEqual(self.runner.load_failed_stage_evidence(ctx, run, plan, before), evidence)
            self.assertEqual(transport.calls, [])

    def test_changed_snapshots_bytes_checkpoint_foreign_mutation_or_duplicate_intent_are_rejected(self):
        for fault in ('input', 'json-type', 'byte-hash', 'checkpoint', 'foreign-write', 'duplicate', 'receipt'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder, \
                    mock.patch.object(self.runner, 'check_private_permissions'):
                ctx, run, plan, before, evidence, _ = self.fixture(Path(folder))
                if fault == 'input':
                    (run / 'input-before.json').write_text('{"synthetic":"different"}', encoding='utf-8')
                elif fault == 'json-type':
                    previous = json.loads(before['input'])
                    previous['publishApis'] = 1
                    (run / 'input-before.json').write_text(json.dumps(previous), encoding='utf-8')
                elif fault == 'byte-hash':
                    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()}
                    (run / 'immutable-before-sha256.json').write_text(json.dumps(hashes), encoding='utf-8')
                    before['input'] += b'\n'
                elif fault == 'checkpoint':
                    (run / 'staged-source-checkpoint.json').write_text('{}', encoding='utf-8')
                elif fault == 'foreign-write':
                    foreign = {'method': 'PUT', 'path': plan['apimId'] + '/policies/policy', 'body': {}, 'headers': None}
                    (run / '00124-write-intent.json').write_text(json.dumps(foreign), encoding='utf-8')
                elif fault == 'duplicate':
                    (run / '00124-write-intent.json').write_text(json.dumps(evidence['intent']), encoding='utf-8')
                else:
                    (run / '00123-response.json').unlink()
                with self.assertRaises((ValueError, OSError)):
                    self.runner.load_failed_stage_evidence(ctx, run, plan, before)

    def test_evidence_must_be_in_the_selected_runtime_evidence_directory(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(self.runner, 'check_private_permissions'):
            ctx, _, plan, before, _, _ = self.fixture(Path(folder))
            for target in (ctx.workspace.root / 'state', Path(folder) / '.local/another-environment/evidence'):
                with self.subTest(target_kind=target.name), self.assertRaises(ValueError):
                    self.runner.load_failed_stage_evidence(ctx, target, plan, before)


class BrowserFailedStageReadTests(unittest.TestCase):
    def fixture(self):
        base, plan, authentication, transport, _, evidence, _, _, _ = RecordedFailedStageTests('runTest').fixture()
        publication = publish.entra_publication
        names = {rid.rsplit('/', 1)[-1] for rid in plan['publishedApiIds']}
        current = publication._source_manifest(base.state['apiPublicationSourceCheckpoint']['source'], plan['apimId'], names,
                                              authentication['previousValue'])
        attempted = publication._source_manifest(evidence['template']['outputs']['entraPublicationSource']['value'], plan['apimId'], names,
                                                authentication['previousValue'])
        scope = publication.source_manifest(base.template['outputs']['entraPublicationSource']['value'], plan['apimId'], names,
                                            authentication['value'])
        routes = {'launch': ('GET', '/launch'), 'launch-post': ('POST', '/launch'),
                  'identity-callback': ('GET', '/identity-callback'), 'callback': ('GET', '/callback')}
        properties = {'type': 'http', 'path': 'auth/connections/browser', 'protocols': ['http', 'https'], 'subscriptionRequired': False}
        browser_id = plan['apimId'] + '/apis/credential-manager-browser'
        old = {'schemaVersion': 1, 'name': 'credential-manager-browser', 'properties': properties, 'apiPolicyAbsent': True,
               'operations': [{'name': name, 'properties': {'displayName': name, 'method': method, 'urlTemplate': path, 'responses': []}}
                              for name, (method, path) in routes.items()],
               'policies': [{'suffix': '/apis/credential-manager-browser/operations/' + name + '/policies/policy',
                             'value': '<policies><inbound><base /></inbound><backend><base /></backend><outbound><base />'
                                      '<set-header name="x-synthetic-browser" exists-action="override"><value>old</value></set-header>'
                                      '</outbound><on-error><base /></on-error></policies>'} for name in routes]}
        new = copy.deepcopy(old)
        for entry in new['policies']:
            entry['value'] = entry['value'].replace('<value>old</value>', '<value>attempted</value>')
        transport.refresh(browser_id, properties)
        transport.refresh(browser_id + '/revisions', value=[{'apiId': '/apis/credential-manager-browser;rev=1', 'apiRevision': '1',
                                                           'isCurrent': True, 'isOnline': True}])
        for kind in ('products', 'tools', 'schemas'):
            transport.refresh(browser_id + '/' + kind, value=[])
        transport.refresh(browser_id + '/operations', value=[{'id': browser_id + '/operations/' + name} for name in routes])
        for entry in old['operations']:
            transport.refresh(browser_id + '/operations/' + entry['name'], entry['properties'])
        for entry in old['policies']:
            transport.refresh(plan['apimId'] + entry['suffix'], {'format': 'rawxml', 'value': entry['value']})
        transport.refresh(plan['apimId'] + new['policies'][0]['suffix'], {'format': 'rawxml', 'value': new['policies'][0]['value']})
        plan['credentialLogin'] = {'syntheticEnabled': True}
        plan['publishedApiIds'].append(browser_id)
        transport.calls.clear()
        return publication, current, attempted, scope, plan, transport, old, new, browser_id, evidence['nativeProductsBefore']

    def test_mixed_known_browser_policies_are_read_without_writes_or_source_adoption(self):
        publication, current, attempted, scope, plan, transport, old, new, browser_id, native_before = self.fixture()
        mixed, snapshot = publication.read_failed_closed_stage(current, attempted, transport, scope_manifest=scope, plan=plan,
                                                              previous_browser_source=old, attempted_browser_source=new,
                                                              native_product_before=native_before)
        self.assertEqual(mixed.policies[plan['apimId'] + '/policies/policy'], current.policies[plan['apimId'] + '/policies/policy'])
        self.assertIn(browser_id + '/operations/launch/policies/policy', snapshot)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_browser_unknown_policy_routing_metadata_membership_identity_and_missing_baseline_are_rejected(self):
        for fault in ('policy', 'routing', 'metadata', 'membership', 'identity', 'missing-old'):
            with self.subTest(fault=fault):
                publication, current, attempted, scope, plan, transport, old, new, browser_id, native_before = self.fixture()
                if fault == 'policy':
                    transport.inventory[plan['apimId'] + old['policies'][0]['suffix']]['body']['properties']['value'] = old['policies'][0]['value'].replace('old', 'unknown')
                elif fault == 'routing':
                    transport.inventory[browser_id]['body']['properties']['path'] = 'unrecorded-route'
                elif fault == 'metadata':
                    new['operations'][0]['properties']['displayName'] = 'unrecorded-metadata'
                elif fault == 'membership':
                    transport.refresh(browser_id + '/products', value=[{'id': plan['apimId'] + '/products/tool-users'}])
                elif fault == 'identity':
                    transport.inventory[browser_id]['body']['id'] = plan['apimId'] + '/apis/unrecorded'
                else:
                    old = None
                with self.assertRaises(ValueError):
                    publication.read_failed_closed_stage(current, attempted, transport, scope_manifest=scope, plan=plan,
                                                         previous_browser_source=old, attempted_browser_source=new,
                                                         native_product_before=native_before)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))


if __name__ == '__main__':
    unittest.main()
