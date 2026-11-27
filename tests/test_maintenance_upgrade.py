"""Exercise the maintenance patch with synthetic resources and protected state."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from deployment.helpers.paths import LocalPaths
from deployment.helpers import private_permissions
from operations.common import OperationContext, OperationError
from operations import maintenance
from test_fixed_nsg_migration import MemoryTransport, fixture_config


PRINCIPAL = '11111111-1111-4111-8111-111111111112'
ROLE_IDS = {
    key: '/subscriptions/11111111-1111-4111-8111-111111111111/providers/Microsoft.Authorization/roleDefinitions/' + value
    for key, value in (('appLogs', '11111111-1111-4111-8111-111111111121'),
                       ('appExec', '11111111-1111-4111-8111-111111111122'),
                       ('environmentLogs', '11111111-1111-4111-8111-111111111123'),
                       ('jobLogs', '11111111-1111-4111-8111-111111111124'))}


def compiler(filename):
    stage = next(stage for stage, (source, _) in maintenance.MODULES.items() if source == filename)
    kind = next(iter(maintenance.MODULES[stage][1] - {'Microsoft.Resources/deployments'}))
    return {'resources': [{'type': kind}]}


class PatchTransport(MemoryTransport):
    def __init__(self, config):
        super().__init__(config)
        self.resources[self.vm_id]['identity'] = {'type': 'SystemAssigned', 'principalId': PRINCIPAL}
        self.resources[self.vm_id]['properties']['osProfile'] = {'linuxConfiguration': {'disablePasswordAuthentication': True}}
        self.endpoints = {}
        for key, suffix in (('collectJapan', 'audit-japan-blob'), ('collectFoundry', 'audit-foundry-blob')):
            identity = self.nw + 'privateEndpoints/pe-' + self.stem + '-' + suffix
            self.endpoints[key] = self.resources.pop(identity)
        for group, names in maintenance.AUDIT_RULES.items():
            parent = self.resources[self.nsg_id(group)]
            parent['properties']['securityRules'] = [child for child in parent['properties']['securityRules']
                                                     if child['name'] not in names]
        self.dns_mismatch = False
        self.fail_deployment = None
        self.lost_response = None
        self.pending_child = False
        self.on_write = None
        self.completed_stages = []
        self.zone_id = self.nw + 'privateDnsZones/privatelink.blob.core.windows.net'

    def endpoint_outputs(self):
        result = {}
        for collector, key in (('collectJapan', 'auditStorageJapan'), ('collectFoundry', 'auditStorageFoundry')):
            endpoint = self.endpoints[collector]
            result[collector] = {'id': endpoint['id'],
                'storageAccountId': endpoint['properties']['privateLinkServiceConnections'][0]['properties']['privateLinkServiceId'],
                'networkInterfaceIds': [nic['id'] for nic in endpoint['properties']['networkInterfaces']],
                'privateIps': self.bindings['ipSets'][key]}
        return result

    def request(self, method, path, body=None, headers=None):
        identity = path.split('?', 1)[0]
        if method == 'PUT' and ('/deployments/' in identity or '/securityRules/' in identity):
            if self.on_write: self.on_write(self, identity)
            self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': headers})
            if '/securityRules/' in identity:
                parent_id, name = identity.split('/securityRules/', 1)
                parent = self.resources[parent_id]
                if any(child['name'] == name for child in parent['properties']['securityRules']):
                    return {'statusCode': 412, 'body': None}
                if headers != {'If-None-Match': '*'}: raise AssertionError('A missing child needs create-only conditions')
                child = {'id': identity, 'name': name, 'type': maintenance.nsg.NSG_TYPE + '/securityRules',
                         'etag': '"new-child"', 'properties': {**body['properties'],
                             'provisioningState': 'Updating' if self.pending_child else 'Succeeded'}}
                parent['properties']['securityRules'].append(child)
                return {'statusCode': 201, 'body': copy.deepcopy(child)}
            stage = identity.rsplit('-operations-', 1)[1]
            # ARM rejects a top-level location at resource-group scope, while
            # the subscription role deployment requires it.
            if '/resourceGroups/' in identity:
                if 'location' in body: return {'statusCode': 400, 'body': {'error': {'code': 'InvalidDeployment'}}}
            elif body.get('location') != self.config['location']:
                return {'statusCode': 400, 'body': {'error': {'code': 'InvalidDeploymentLocation'}}}
            outputs = {}
            if stage == 'endpoints':
                self.resources.update({value['id']: copy.deepcopy(value) for value in self.endpoints.values()})
                outputs = {'privateEndpoints': self.endpoint_outputs()}
            elif stage == 'dns':
                records = {'collectJapan': self.bindings['ipSets']['auditStorageJapan'],
                           'collectFoundry': self.bindings['ipSets']['auditStorageFoundry']}
                if self.dns_mismatch: records['collectFoundry'] = ['10.70.1.99']
                outputs = {'privateDnsZoneId': self.zone_id, 'recordPrivateIps': records,
                           'privateDnsZoneGroupIds': [value['id'] + '/privateDnsZoneGroups/default' for value in self.endpoints.values()]}
            elif stage == 'link': outputs = {'privateDnsLinkId': self.zone_id + '/virtualNetworkLinks/link-' + self.stem}
            elif stage == 'roles': outputs = {'roleDefinitionIds': ROLE_IDS}
            state = 'Failed' if stage == self.fail_deployment else 'Succeeded'
            self.resources[identity] = {'id': identity, 'properties': {'provisioningState': state,
                'outputs': {key: {'value': value} for key, value in outputs.items()}}}
            if state == 'Succeeded': self.completed_stages.append(stage)
            if stage == self.lost_response: raise OSError('Synthetic dropped deployment response')
            return {'statusCode': 201, 'body': self.resources[identity]}
        return super().request(method, path, body=body, headers=headers)


class RecordingContext(OperationContext):
    def record(self, kind, payload):
        self.events.append(copy.deepcopy(payload))
        return super().record(kind, payload)


class MaintenanceUpgradeTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            # Native DACL inspection has dedicated tests. This unit keeps the
            # real path, link and atomic-save checks on synthesized files.
            acl = mock.patch.object(private_permissions, '_windows_owner_acl')
            acl.start()
            self.addCleanup(acl.stop)
        self.directory = tempfile.TemporaryDirectory(prefix='synthetic-maintenance-')
        self.addCleanup(self.directory.cleanup)
        config = fixture_config()
        config['azureTenantId'] = '11111111-1111-4111-8111-111111111141'
        config.update(apiDefinitions=[], management={}, credentialProviders=[],
                      gateway={'authentication': {}}, monitoring={'auditReaders': []})
        self.transport = PatchTransport(config)
        groups, stem, _ = maintenance.nsg._scope(config)
        storage = {}
        containers = []
        for collector, suffix in (('collectJapan', 'jp'), ('collectFoundry', 'us')):
            identity = groups['monitor'] + '/providers/Microsoft.Storage/storageAccounts/st' + stem.replace('-', '') + suffix
            storage[collector] = {'id': identity, 'name': identity.rsplit('/', 1)[1]}
            container_id = identity + '/blobServices/default/containers/insights-logs-audit'
            containers.append(container_id)
            self.transport.add(container_id, 'Microsoft.Storage/storageAccounts/blobServices/containers', {})
        storage['containerIds'] = containers
        svc = groups['svc'] + '/providers/Microsoft.App/'
        application = 'ca-' + stem + '-portal'
        environment = 'cae-' + stem
        sql_server = groups['svc'] + '/providers/Microsoft.Sql/servers/sql-' + stem
        self.transport.add(sql_server, 'Microsoft.Sql/servers', {})
        for alias in ('credentials', 'portal'):
            self.transport.add(sql_server + '/databases/sqldb-' + stem + '-' + alias,
                               'Microsoft.Sql/servers/databases', {'status': 'Online'})
        broker_application = 'ca-' + stem + '-credentials'
        broker_jobs = ['caj-' + stem + '-schema', 'caj-' + stem + '-recon']
        for kind, names in (('containerApps', [application, broker_application]),
                            ('jobs', [*maintenance.resource_names(config).values(), *broker_jobs])):
            for name in names: self.transport.add(svc + kind + '/' + name, 'Microsoft.App/' + kind, {})
        workspace_id = groups['monitor'] + '/providers/Microsoft.OperationalInsights/workspaces/law-' + stem
        self.transport.add(workspace_id, 'Microsoft.OperationalInsights/workspaces',
                           {'customerId': '11111111-1111-4111-8111-111111111142'})
        state = {'outputs': {'managementVm': {'id': self.transport.vm_id, 'name': self.transport.vm_id.rsplit('/', 1)[1], 'principalId': PRINCIPAL},
                 'auditStorage': storage, 'portal': {'applicationName': application, 'environmentName': environment,
                                                   'sharedSqlServerId': sql_server},
                 'gatewayFoundation': {}, 'monitoring': {'workspaceId': workspace_id, 'workspaceName': 'law-' + stem,
                    'workspaceCustomerId': '11111111-1111-4111-8111-111111111142'}}}
        workspace = LocalPaths(Path(self.directory.name), config['environmentName'])
        self.context = RecordingContext(workspace.params, config, state, workspace, self.transport)
        self.context.events = []
        self.context.save_private(self.context.state_path, state)
        self.ticks = 0
        def clock():
            self.ticks += 10
            return self.ticks
        self.operation = maintenance.MaintenanceUpgrade(self.context, compiler=compiler, sleep=lambda _: None, monotonic=clock)
        # This suite simulates mutations. Role what-if verification has its
        # own synthetic tests and must never invoke a live CLI from here.
        self.operation.validate_roles = mock.Mock()

    def test_preflight_is_read_only_and_records_only_in_selected_private_runtime(self):
        self.assertEqual(self.operation.preflight()['status'], 'verified-plan')
        self.assertFalse(self.transport.mutations())
        files = [path for path in Path(self.directory.name).rglob('*') if path.is_file()]
        self.assertTrue(files)
        self.assertTrue(all(path.resolve().is_relative_to(self.context.workspace.root) for path in files),
                        [str(path.relative_to(Path(self.directory.name))) for path in files])
        for candidate in (Path(self.directory.name) / 'published.json',
                          self.context.workspace.root.parent / 'other' / 'state.json'):
            with self.assertRaises(OperationError): self.context.save_private(candidate, {'synthetic': True})

    def test_unavailable_constructed_database_stops_upgrade_without_writes(self):
        target = self.context.state['outputs']['portal']['sharedSqlServerId'] + '/databases/sqldb-' + self.context.config['resourceStem'] + '-credentials'
        for state in ('Paused', None):
            with self.subTest(state=state):
                self.transport.resources[target]['properties']['status'] = state
                with self.assertRaisesRegex(OperationError, 'not online'):
                    self.operation.preflight()
                self.assertFalse(self.transport.mutations())

    def test_apply_creates_allow_pairs_before_dns_link_and_never_puts_service_parents(self):
        original = copy.deepcopy(self.transport.resources)
        result = self.operation.apply()
        self.assertEqual(result, {'status': 'assets-applied', 'runtimeVerified': False})
        writes = self.transport.mutations()
        stages = [call['path'].split('?', 1)[0].rsplit('-operations-', 1)[1]
                  if '/deployments/' in call['path'] else 'nsg' for call in writes]
        self.assertEqual(stages, ['endpoints', 'nsg', 'nsg', 'nsg', 'nsg', 'dns', 'link', 'roles', 'aca', 'monitoring', 'readers', 'readers', 'client'])
        self.assertTrue(all('/deployments/' in call['path'] or '/securityRules/' in call['path'] for call in writes))
        for identity, value in original.items():
            if any(marker in identity for marker in ('/storageAccounts/', '/virtualMachines/', '/Microsoft.ApiManagement/service/')):
                self.assertEqual(self.transport.resources[identity], value)
        saved = json.loads(self.context.state_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['outputs']['managementMaintenance']['principalId'], PRINCIPAL)
        self.assertEqual(saved['outputs']['portal']['managementMaintenance'], saved['outputs']['gatewayFoundation']['managementMaintenance'])

    def test_apply_keeps_exact_previous_platform_rules_and_creates_missing_foundry_rules(self):
        self.transport.deploy_fixed()
        previous = self.transport.use_previous_platform_audit_rule_names()
        self.transport.resources.update({endpoint['id']: endpoint for endpoint in self.transport.endpoints.values()})
        for group in ('management', 'pep'):
            parent = self.transport.resources[self.transport.nsg_id(group)]
            parent['properties']['securityRules'] = [child for child in parent['properties']['securityRules']
                                                     if 'audit-storage-foundry' not in child['name']]
        expected = {child['id']: copy.deepcopy(child) for child in previous}
        self.assertEqual(self.operation.apply(), {'status': 'assets-applied', 'runtimeVerified': False})
        nsg_requests = [call for call in self.transport.mutations() if '/securityRules/' in call['path']]
        self.assertEqual(len(nsg_requests), 2)
        self.assertTrue(all('audit-storage-foundry' in call['path'] and call['method'] == 'PUT' for call in nsg_requests))
        for child in previous:
            self.assertEqual(child, expected[child['id']])

    def test_dns_record_mismatch_stops_before_link_roles_and_state_update(self):
        self.transport.dns_mismatch = True
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertEqual(self.transport.completed_stages, ['endpoints', 'dns'])
        self.assertEqual(json.loads(self.context.state_path.read_text(encoding='utf-8')), self.context.state)

    def test_dropped_deployment_response_is_recorded_once_and_does_not_repeat_put(self):
        self.transport.lost_response = 'endpoints'
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertEqual(len(self.transport.mutations()), 1)
        self.assertTrue(any(event.get('stage') == 'endpoints' and event.get('status') == 'requesting'
                            for event in self.context.events))

    def test_failed_deployment_does_not_advance_to_nsg_or_dns(self):
        self.transport.fail_deployment = 'endpoints'
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertEqual(len(self.transport.mutations()), 1)

    def test_template_guard_rejects_parent_service_writes_and_nested_mutations(self):
        for kind in ('Microsoft.Storage/storageAccounts', 'Microsoft.Compute/virtualMachines', 'Microsoft.ApiManagement/service'):
            with self.subTest(kind=kind), self.assertRaises(OperationError):
                maintenance.checked_template({'resources': [{'type': kind}]}, maintenance.MODULES['endpoints'][1])
        nested = {'resources': [{'type': 'Microsoft.Resources/deployments', 'properties': {'mode': 'Incremental',
            'template': {'resources': [{'type': 'Microsoft.Network/networkInterfaces'}]}}}]}
        with self.assertRaises(OperationError): maintenance.checked_template(nested, maintenance.MODULES['endpoints'][1])

    def test_dns_reader_nested_template_is_allowed_only_when_it_has_zero_resource_writes(self):
        nested = {'resources': [{'type': 'Microsoft.Resources/deployments', 'properties': {'mode': 'Incremental',
            'template': {'resources': [], 'outputs': {'recordPrivateIps': {'type': 'object', 'value': {}}}}}}]}
        self.assertEqual(maintenance.checked_template(nested, maintenance.MODULES['dns'][1]), nested)
        for kind in ('Microsoft.Network/privateDnsZones/A', 'Microsoft.Network/virtualNetworks',
                     'Microsoft.Storage/storageAccounts', 'Microsoft.Compute/virtualMachines',
                     'Microsoft.Authorization/roleAssignments', 'Microsoft.Resources/deployments'):
            template = copy.deepcopy(nested)
            template['resources'][0]['properties']['template']['resources'] = [{'type': kind}]
            with self.subTest(kind=kind), self.assertRaises(OperationError):
                maintenance.checked_template(template, maintenance.MODULES['dns'][1])
        for mode in ('Complete', None, '', False):
            template = copy.deepcopy(nested)
            properties = template['resources'][0]['properties']
            if mode is None: properties.pop('mode')
            else: properties['mode'] = mode
            with self.subTest(mode=mode), self.assertRaises(OperationError):
                maintenance.checked_template(template, maintenance.MODULES['dns'][1])
        for link in (None, {'uri': 'https://example.invalid/unreviewed-template.json'}):
            template = copy.deepcopy(nested)
            template['resources'][0]['properties']['templateLink'] = link
            with self.subTest(link=link), self.assertRaises(OperationError):
                maintenance.checked_template(template, maintenance.MODULES['dns'][1])

    def test_existing_rule_drift_prevents_the_first_mutation(self):
        parent = self.transport.resources[self.transport.nsg_id('management')]
        parent['properties']['securityRules'][0]['properties']['destinationAddressPrefix'] = '192.0.2.99/32'
        with self.assertRaises((OperationError, maintenance.nsg.FixedNsgError)): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def test_state_race_never_overwrites_an_intervening_deployment(self):
        newer = {**self.context.state, 'syntheticInterveningDeployment': True}
        def replace_state(transport, identity):
            self.context.save_private(self.context.state_path, newer, overwrite=True)
            transport.on_write = None
        self.transport.on_write = replace_state
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertEqual(json.loads(self.context.state_path.read_text(encoding='utf-8')), newer)
        self.assertEqual(len(self.transport.mutations()), 1)

    def test_state_changed_before_apply_prevents_the_first_mutation(self):
        newer = {**self.context.state, 'syntheticInterveningDeployment': True}
        self.context.save_private(self.context.state_path, newer, overwrite=True)
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def test_pending_nsg_creation_stops_before_the_next_allow_or_dns(self):
        self.transport.pending_child = True
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertEqual(len(self.transport.mutations()), 2)
        self.assertEqual(self.transport.completed_stages, ['endpoints'])

    def test_existing_audit_reader_principal_spelling_is_kept_without_adding_other_principals(self):
        principal = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        self.transport.resources[self.transport.vm_id]['identity']['principalId'] = principal
        self.context.state['outputs']['managementVm']['principalId'] = principal
        existing = {'collector': 'collectJapan', 'containerName': 'insights-logs-audit',
                    'principalId': principal.upper(), 'principalType': 'ServicePrincipal'}
        self.context.config['monitoring']['auditReaders'] = [existing,
            {**existing, 'principalId': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'principalType': 'Group'}]
        plan = self.operation.preflight()
        self.assertEqual(plan['readers']['collectJapan'], [existing])
        self.assertEqual(plan['readers']['collectFoundry'][0]['principalId'], principal)

    def reader_assignment(self, scope, role, *, principal=PRINCIPAL, suffix='11111111-1111-4111-8111-111111111111'):
        identity = scope + '/providers/Microsoft.Authorization/roleAssignments/' + suffix
        return {'id': identity, 'name': suffix, 'type': 'Microsoft.Authorization/roleAssignments',
                'properties': {'scope': scope, 'principalId': principal,
                               'roleDefinitionId': '/subscriptions/' + self.context.config['subscriptionId']
                                                   + '/providers/Microsoft.Authorization/roleDefinitions/' + role}}

    def set_reader_assignments(self, scope, values, **fields):
        identity = scope + '/providers/Microsoft.Authorization/roleAssignments'
        self.transport.role_assignment_collections[identity] = {'value': values, **fields}

    def test_exact_existing_blob_and_workspace_readers_are_kept_without_duplicate_creation(self):
        storage = self.context.state['outputs']['auditStorage']
        container = storage['containerIds'][0]
        workspace = self.context.state['outputs']['monitoring']['workspaceId']
        # ARM casing is not an identity distinction.
        blob = self.reader_assignment(container.upper(), '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1', principal=PRINCIPAL.upper())
        law = self.reader_assignment(workspace, '3b03c2da-16b3-4a49-8834-0f8130efdd3b')
        self.set_reader_assignments(container, [blob])
        self.set_reader_assignments(workspace, [law])
        plan = self.operation.preflight()
        self.assertEqual(plan['retainedAuditReaders'], [container])
        self.assertTrue(plan['existingWorkspaceReader'])
        self.assertEqual(plan['readers']['collectJapan'], [])
        self.assertEqual(len(plan['readers']['collectFoundry']), 1)
        inventory = self.operation.client_inventory(plan)
        self.assertEqual(inventory['auditStorage'], [
            {'id': storage[key]['id'], 'containers': ['insights-logs-audit']}
            for key in maintenance.COLLECTORS])
        self.operation.apply()
        self.assertNotIn('monitoring', self.transport.completed_stages)
        reader_writes = [call for call in self.transport.mutations() if '-operations-readers' in call['path']]
        self.assertEqual(len(reader_writes), 2)
        self.assertEqual([call['body']['properties']['parameters']['readers']['value'] for call in reader_writes],
                         [[], plan['readers']['collectFoundry']])
        client_write = next(call for call in self.transport.mutations() if '-operations-client' in call['path'])
        self.assertEqual(client_write['body']['properties']['parameters']['inventory']['value']['auditStorage'],
                         inventory['auditStorage'])

    def test_other_principal_scope_or_role_does_not_add_foreign_reader_or_suppress_the_vm_reader(self):
        container = self.context.state['outputs']['auditStorage']['containerIds'][0]
        role = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
        for change in ('principal', 'scope', 'role'):
            row = self.reader_assignment(container, role)
            if change == 'principal': row['properties']['principalId'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            elif change == 'scope': row['properties']['scope'] = container.rsplit('/containers/', 1)[0]
            else: row['properties']['roleDefinitionId'] = row['properties']['roleDefinitionId'].rsplit('/', 1)[0] + '/3b03c2da-16b3-4a49-8834-0f8130efdd3b'
            self.set_reader_assignments(container, [row])
            plan = self.operation.preflight()
            with self.subTest(change=change):
                self.assertEqual(plan['retainedAuditReaders'], [])
                self.assertFalse(plan['existingWorkspaceReader'])
                self.assertEqual(plan['readers']['collectJapan'], [
                    {'containerName': 'insights-logs-audit', 'principalId': PRINCIPAL, 'principalType': 'ServicePrincipal'}])
                self.assertFalse(self.transport.mutations())

    def test_unread_reader_collection_page_stops_before_any_mutation(self):
        container = self.context.state['outputs']['auditStorage']['containerIds'][0]
        workspace = self.context.state['outputs']['monitoring']['workspaceId']
        for scope in (container, workspace):
            self.transport.role_assignment_collections.clear()
            self.transport.calls.clear()
            self.set_reader_assignments(scope, [], nextLink='https://management.azure.com/synthetic/next-page')
            with self.subTest(scope=scope), self.assertRaisesRegex(OperationError, 'fully inspected'):
                self.operation.apply()
            self.assertFalse(self.transport.mutations())

    def test_duplicate_exact_reader_assignments_stop_before_any_mutation(self):
        container = self.context.state['outputs']['auditStorage']['containerIds'][0]
        row = self.reader_assignment(container, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
        duplicate = self.reader_assignment(container, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1',
                                           suffix='11111111-1111-4111-8111-111111111112')
        self.set_reader_assignments(container, [row, duplicate])
        with self.assertRaisesRegex(OperationError, 'ambiguous'): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def test_broker_resources_are_always_selected_independently_of_business_profiles(self):
        initial = self.operation.preflight()
        self.assertIn('ca-' + self.transport.stem + '-credentials', initial['applicationNames'])
        self.assertTrue({'caj-' + self.transport.stem + '-schema', 'caj-' + self.transport.stem + '-recon'} <= set(initial['jobNames']))
        self.context.config['credentialProviders'] = [{'name': 'synthetic-provider', 'displayName': 'Synthetic provider',
            'oauthTenantId': '11111111-1111-4111-8111-111111111131',
            'clientId': '11111111-1111-4111-8111-111111111132',
            'identityEndpoint': 'https://example.invalid/user', 'identityIdProperty': 'id'}]
        self.context.config['gateway']['authentication']['credentialLogin'] = {'providerNames': ['synthetic-provider']}
        plan = self.operation.preflight()
        for family in ('applicationNames', 'jobNames', 'environmentNames'):
            self.assertEqual(plan[family], initial[family])
        self.assertFalse(self.transport.mutations())

    def test_missing_always_on_broker_stops_maintenance_before_mutations(self):
        app = self.transport.groups['svc'] + '/providers/Microsoft.App/containerApps/ca-' + self.transport.stem + '-credentials'
        self.transport.resources.pop(app)
        with self.assertRaises(OperationError):
            self.operation.preflight()
        self.assertFalse(self.transport.mutations())

    def test_workspace_name_and_recorded_id_mismatch_stops_before_writes(self):
        self.context.state['outputs']['monitoring']['workspaceName'] = 'law-synthetic-other'
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def test_recorded_vm_name_cannot_redirect_the_client_to_a_different_vm(self):
        self.context.state['outputs']['managementVm']['name'] = 'vm-synthetic-other'
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def test_workspace_customer_id_mismatch_stops_before_writes(self):
        self.context.state['outputs']['monitoring']['workspaceCustomerId'] = '11111111-1111-4111-8111-111111111143'
        with self.assertRaises(OperationError): self.operation.apply()
        self.assertFalse(self.transport.mutations())

    def add_existing_dns(self):
        self.transport.resources.update({value['id']: copy.deepcopy(value) for value in self.transport.endpoints.values()})
        self.transport.add(self.transport.zone_id, 'Microsoft.Network/privateDnsZones',
                           {'numberOfVirtualNetworkLinks': 1}, location='global')
        link_id = self.transport.zone_id + '/virtualNetworkLinks/link-' + self.transport.stem
        self.transport.add(link_id, 'Microsoft.Network/privateDnsZones/virtualNetworkLinks', {
            'virtualNetwork': {'id': self.transport.vnet_id}, 'registrationEnabled': False,
            'resolutionPolicy': 'NxDomainRedirect'}, location='global')
        for collector, key in (('collectJapan', 'auditStorageJapan'), ('collectFoundry', 'auditStorageFoundry')):
            endpoint = self.transport.endpoints[collector]
            self.transport.add(endpoint['id'] + '/privateDnsZoneGroups/default',
                'Microsoft.Network/privateEndpoints/privateDnsZoneGroups', {'privateDnsZoneConfigs': [
                    {'name': 'blob', 'properties': {'privateDnsZoneId': self.transport.zone_id}}]})
            account = self.context.state['outputs']['auditStorage'][collector]['name']
            self.transport.add(self.transport.zone_id + '/A/' + account, 'Microsoft.Network/privateDnsZones/A',
                {'aRecords': [{'ipv4Address': ip} for ip in self.transport.bindings['ipSets'][key]]}, location='global')
        return link_id

    def test_existing_matching_initial_dns_is_adopted_for_a_safe_retry(self):
        self.add_existing_dns()
        self.assertEqual(self.operation.preflight()['status'], 'verified-plan')
        self.assertFalse(self.transport.mutations())

    def test_manual_or_partial_dns_changes_stop_before_the_first_mutation(self):
        self.add_existing_dns()
        original = copy.deepcopy(self.transport.resources)
        link_id = self.transport.zone_id + '/virtualNetworkLinks/link-' + self.transport.stem
        group_id = self.transport.endpoints['collectFoundry']['id'] + '/privateDnsZoneGroups/default'
        account = self.context.state['outputs']['auditStorage']['collectFoundry']['name']
        record_id = self.transport.zone_id + '/A/' + account
        for change in ('zone-tags', 'link-vnet', 'zone-extra-links', 'zonegroup-zone',
                       'record-ips', 'record-unmanaged', 'link-resolution', 'group-unresolved'):
            self.transport.resources = copy.deepcopy(original)
            self.transport.calls.clear()
            if change == 'zone-tags': self.transport.resources[self.transport.zone_id]['tags']['product'] = 'foreign'
            elif change == 'link-vnet': self.transport.resources[link_id]['properties']['virtualNetwork']['id'] = '/synthetic/foreign-vnet'
            elif change == 'zone-extra-links': self.transport.resources[self.transport.zone_id]['properties']['numberOfVirtualNetworkLinks'] = 2
            elif change == 'zonegroup-zone': self.transport.resources[group_id]['properties']['privateDnsZoneConfigs'][0]['properties']['privateDnsZoneId'] = '/synthetic/foreign-zone'
            elif change == 'record-ips': self.transport.resources[record_id]['properties']['aRecords'] = [{'ipv4Address': '10.70.1.99'}]
            elif change == 'record-unmanaged': self.transport.resources.pop(group_id)
            elif change == 'link-resolution': self.transport.resources[link_id]['properties']['resolutionPolicy'] = 'Default'
            else: self.transport.resources[group_id]['properties']['provisioningState'] = 'Updating'
            with self.subTest(change=change), self.assertRaises(OperationError): self.operation.apply()
            self.assertFalse(self.transport.mutations())


class RoleWhatIfTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()
        self.definitions = [{'name': name, 'description': 'Synthetic role definition ' + name,
                             'actions': ['Microsoft.App/containerApps/read'], 'dataActions': []}
                            for name in ('app logs', 'app exec', 'environment token', 'job logs')]
        scope = '/subscriptions/' + self.config['subscriptionId']
        self.document = {'status': 'Succeeded', 'changes': []}
        for index, definition in enumerate(self.definitions, 1):
            identity = scope + '/providers/Microsoft.Authorization/roleDefinitions/11111111-1111-4111-8111-1111111111' + f'{index:02d}'
            properties = {'roleName': self.config['resourceStem'] + ' management ' + definition['name'],
                'description': definition['description'], 'type': 'CustomRole', 'assignableScopes': [scope],
                'permissions': [{'actions': definition['actions'], 'notActions': [],
                                 'dataActions': [], 'notDataActions': []}]}
            self.document['changes'].append({'changeType': 'Create', 'resourceId': identity,
                'after': {'id': identity, 'type': 'Microsoft.Authorization/roleDefinitions', 'properties': properties}})

    def test_all_four_expanded_approved_roles_are_accepted(self):
        for action in ('Create', 'NoChange', 'Modify'):
            document = copy.deepcopy(self.document)
            for change in document['changes']:
                change['changeType'] = action
                if action != 'Create': change['before'] = copy.deepcopy(change['after'])
                if action == 'NoChange': change.pop('after')
            with self.subTest(action=action):
                self.assertEqual(maintenance.verify_role_what_if(self.config, document, self.definitions),
                                 {'status': 'roles-validated', 'rolesVerified': 4})

    def test_arm_omitted_or_null_empty_permission_arrays_are_normalized(self):
        for omitted in (True, False):
            document = copy.deepcopy(self.document)
            for change in document['changes']:
                permissions = change['after']['properties']['permissions'][0]
                for key in ('notActions', 'dataActions', 'notDataActions'):
                    if omitted: permissions.pop(key)
                    else: permissions[key] = None
            with self.subTest(omitted=omitted):
                self.assertEqual(maintenance.verify_role_what_if(self.config, document, self.definitions),
                                 {'status': 'roles-validated', 'rolesVerified': 4})

    def test_empty_permission_arrays_do_not_accept_other_false_values(self):
        for value in (False, '', 0, {}, 'Microsoft.App/containerApps/read'):
            document = copy.deepcopy(self.document)
            document['changes'][0]['after']['properties']['permissions'][0]['notActions'] = value
            with self.subTest(value=value), self.assertRaises(OperationError):
                maintenance.verify_role_what_if(self.config, document, self.definitions)

    def test_role_expansion_cannot_add_permissions_scopes_types_or_nested_ids(self):
        for change in ('scope', 'action', 'data-action', 'type', 'nested-id', 'duplicate', 'missing'):
            document = copy.deepcopy(self.document)
            row = document['changes'][0]
            properties = row['after']['properties']
            if change == 'scope': properties['assignableScopes'].append('/subscriptions/22222222-2222-4222-8222-222222222222')
            elif change == 'action': properties['permissions'][0]['actions'].append('Microsoft.App/containerApps/write')
            elif change == 'data-action': properties['permissions'][0]['dataActions'].append('Microsoft.App/containerApps/exec/action')
            elif change == 'type': row['after']['type'] = 'Microsoft.Storage/storageAccounts'
            elif change == 'nested-id': row['resourceId'] = row['resourceId'].replace('/roleDefinitions/', '/roleDefinitions/nested/')
            elif change == 'duplicate': document['changes'][1] = copy.deepcopy(row)
            else: document['changes'].pop()
            with self.subTest(change=change), self.assertRaises(OperationError):
                maintenance.verify_role_what_if(self.config, document, self.definitions)

    def test_existing_different_role_and_unexpanded_modified_state_are_rejected(self):
        for change in ('old-permission', 'after-missing'):
            document = copy.deepcopy(self.document)
            row = document['changes'][0]
            row['changeType'] = 'Modify'
            row['before'] = copy.deepcopy(row['after'])
            if change == 'old-permission': row['before']['properties']['permissions'][0]['actions'].append('Microsoft.App/containerApps/write')
            else: row.pop('after')
            with self.subTest(change=change), self.assertRaises(OperationError):
                maintenance.verify_role_what_if(self.config, document, self.definitions)


if __name__ == '__main__': unittest.main()
