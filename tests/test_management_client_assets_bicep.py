"""Compile management-client assets and compare initial/App/upgrade inventories."""
import base64
import copy
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from bicep_helpers import compile_bicep
from test_management_maintenance_rbac import MaintenanceExpression, SUBSCRIPTION
from test_nsg_deployment_order import ancestors, dependency_graph, same_template
from test_fixed_nsg_migration import fixture_config
from deployment.helpers import management_client_assets as assets
from operations.maintenance import MaintenanceUpgrade


ROOT = Path(__file__).resolve().parents[1]


class ClientExpression(MaintenanceExpression):
    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] == 'extensionResourceId':
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            return '/'.join(values)
        return super().evaluate(parameters, index, tree, bindings)


def resolve(template, value, parameters):
    return ClientExpression('[true()]', template.get('variables', {})).resolve(value, parameters)


class ManagementClientBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = compile_bicep('infra/modules/management-maintenance-client.bicep')
        cls.core = compile_bicep('infra/main.bicep')
        cls.network = compile_bicep('infra/modules/network.bicep')
        cls.credential_network = compile_bicep('infra/modules/credential-broker-network.bicep')

    def test_aca_dev_firewall_and_environment_use_the_same_constructed_region(self):
        _, nodes = dependency_graph(self.core)
        def constructs(node, resource_type):
            resources = node.get('properties', {}).get('template', {}).get('resources', {})
            return any(resource['type'].casefold() == resource_type.casefold()
                       for resource in (resources.values() if isinstance(resources, dict) else resources))
        network, = [node for node in nodes.values()
                    if constructs(node, 'Microsoft.Network/virtualNetworks')]
        portal, = [node for node in nodes.values()
                   if constructs(node, 'Microsoft.App/managedEnvironments')
                   and "-portal'" in node.get('name', '')]
        network_region = network['properties']['parameters']['location']['value']
        portal_region = portal['properties']['parameters']['location']['value']
        self.assertEqual(network_region, "[variables('location')]")
        self.assertEqual(portal_region, network_region)
        self.assertEqual(self.core['variables']['location'], "[parameters('configuration').location]")
        resources = portal['properties']['template']['resources']
        environment, = [node for node in (resources.values() if isinstance(resources, dict) else resources)
                        if node['type'].casefold() == 'microsoft.app/managedenvironments']
        self.assertEqual(environment['location'], "[parameters('location')]")

    def fixture(self):
        config = fixture_config()
        config['azureTenantId'] = '22222222-2222-4222-8222-222222222222'
        prefix = '/subscriptions/' + SUBSCRIPTION + '/resourceGroups/'
        vm_id = prefix + config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-test-fixture-mgmt'
        svc = prefix + config['resourceGroupNames']['svc'] + '/providers/Microsoft.App/'
        monitor = prefix + config['resourceGroupNames']['monitor'] + '/providers/'
        management = {'id': vm_id, 'name': 'vm-test-fixture-mgmt', 'principalId': '33333333-3333-4333-8333-333333333333'}
        platform = {'applicationId': svc + 'containerApps/ca-test-fixture-portal',
                    'environmentId': svc + 'managedEnvironments/cae-test-fixture'}
        monitoring = {'workspaceId': monitor + 'Microsoft.OperationalInsights/workspaces/law-test-fixture',
                      'workspaceCustomerId': '44444444-4444-4444-8444-444444444444'}
        storage = {key: {'id': monitor + 'Microsoft.Storage/storageAccounts/sttestfixture' + suffix,
                         'name': 'sttestfixture' + suffix}
                   for key, suffix in (('collectJapan', 'jp'), ('collectFoundry', 'us'))}
        storage['containerIds'] = [storage[key]['id'] + '/blobServices/default/containers/insights-logs-audit'
                                   for key in ('collectJapan', 'collectFoundry')]
        references = {'test-fixture-management': {'outputs': {'managementVm': {'value': management}}},
                      'test-fixture-portal': {'outputs': {'platform': {'value': platform}}},
                      'test-fixture-monitoring': {'outputs': {'monitoring': {'value': monitoring}, 'storage': {'value': storage}}}}
        # Symbolic compiler versions may reference the module declaration key.
        references.update({key: references['test-fixture-' + key] for key in ('management', 'portal', 'monitoring')})
        parameters = {'configuration': config, 'phase': 'core', '__references': references}
        expected = {'schemaVersion': 1, 'subscriptionId': SUBSCRIPTION, 'azureTenantId': config['azureTenantId'],
            'managementVmId': vm_id, 'managementPrincipalId': management['principalId'],
            'applications': [platform['applicationId']], 'jobs': [], 'sqlDatabases': [], 'environments': [platform['environmentId']],
            'workspace': {'id': monitoring['workspaceId'], 'customerId': monitoring['workspaceCustomerId']},
            'auditStorage': [{'id': storage[key]['id'], 'containers': ['insights-logs-audit']}
                             for key in ('collectJapan', 'collectFoundry')]}
        state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
        state['outputs'] = {'managementVm': management, 'monitoring': monitoring, 'auditStorage': storage,
                            'managementClientInventory': expected}
        return config, parameters, expected, state

    def test_client_embeds_current_public_source_and_exact_constructed_inventory(self):
        _, _, inventory, _ = self.fixture()
        self.assertEqual(self.client['parameters']['inventory']['type'], 'secureObject')
        resource, = self.client['resources']
        self.assertEqual(resource['type'], 'Microsoft.Compute/virtualMachines/runCommands')
        properties = resolve(self.client, resource['properties'],
                             {'vmName': 'vm-fixture-mgmt', 'location': 'japaneast', 'tags': {}, 'inventory': inventory})
        script = properties['source']['script']
        embedded = dict(re.findall(r"export (CORE_MAINTENANCE_[A-Z_]+_B64)='([^']*)'", script))
        self.assertEqual(base64.b64decode(embedded['CORE_MAINTENANCE_SOURCE_B64']).decode(),
                         (ROOT / 'scripts/management_client.py').read_text(encoding='utf-8'))
        self.assertEqual(base64.b64decode(embedded['CORE_MAINTENANCE_REQUIREMENTS_B64']).decode().replace('\r\n', '\n'),
                         (ROOT / 'scripts/maintenance-requirements.txt').read_text(encoding='utf-8'))
        import json
        self.assertEqual(json.loads(base64.b64decode(embedded['CORE_MAINTENANCE_INVENTORY_B64'])), inventory)
        self.assertFalse(properties['asyncExecution'])
        self.assertTrue(properties['treatFailureAsDeploymentFailure'])
        self.assertEqual(properties['timeoutInSeconds'], 1800)

    def test_initial_core_inventory_uses_only_actual_outputs_and_no_uncreated_jobs(self):
        _, parameters, expected, _ = self.fixture()
        actual = resolve(self.core, self.core['outputs']['managementClientInventory']['value'], parameters)
        self.assertEqual(actual, expected)
        parameters['phase'] = 'monitoring'
        self.assertEqual(resolve(self.core, self.core['outputs']['managementClientInventory']['value'], parameters), {})

    def test_core_client_waits_for_network_dns_and_guest_bootstrap(self):
        graph, nodes = dependency_graph(self.core)
        selected = [key for key, node in nodes.items()
                    if same_template(node.get('properties', {}).get('template'), self.client)]
        self.assertEqual(len(selected), 1)
        predecessors = ancestors(graph, selected[0])
        required = ('credential-network', 'audit-private-dns-link')
        names = [node['name'] for key, node in nodes.items() if key in predecessors]
        for suffix in required:
            with self.subTest(suffix=suffix): self.assertTrue(any(suffix in name for name in names))

    def test_core_after_both_apps_and_existing_upgrade_produce_identical_inventory(self):
        config, parameters, _, state = self.fixture()
        core = resolve(self.core, self.core['outputs']['managementClientInventory']['value'], parameters)
        combined = copy.deepcopy(core)
        for component in ('portal', 'gateway'):
            outputs = {'managementMaintenance': assets._expected_targets(config)[component]}
            combined, _ = assets._merge_outputs(config, combined, outputs, component)
        plan = {'principalId': core['managementPrincipalId'], 'auditStorage': state['outputs']['auditStorage'],
                'sqlDatabases': combined['sqlDatabases'],
                'applicationNames': [value.rsplit('/', 1)[-1] for value in combined['applications']],
                'jobNames': [value.rsplit('/', 1)[-1] for value in combined['jobs']],
                'environmentNames': [value.rsplit('/', 1)[-1] for value in combined['environments']],
                'readers': {key: [{'containerName': 'insights-logs-audit'}] for key in ('collectJapan', 'collectFoundry')}}
        context = SimpleNamespace(config=config, state=state, transport=mock.Mock())
        upgraded = MaintenanceUpgrade(context).client_inventory(plan)
        self.assertEqual(upgraded, combined)

    def test_broker_firewall_has_only_routed_azure_and_package_traffic(self):
        template = self.credential_network
        resource = template['resources']['rules']
        parameters = {'managementPrefix': '10.70.2.128/26',
                      'acaPrefix': '10.70.2.192/26', 'apimPrefix': '10.70.2.64/26',
                      'acaPrivateIp': '10.70.2.196',
                      'stem': 'test-fixture', 'privateEndpointNicIds': [], 'referencePrivateIps': ['10.70.1.10']}
        from test_nsg_rule_priorities import FixedExpression
        rules = FixedExpression('[true()]', template).resolve(resource['properties']['ruleCollections'], parameters)
        self.assertEqual([collection['name'] for collection in rules], ['credential-broker-azure'])
        self.assertEqual({rule['ruleType'] for collection in rules for rule in collection['rules']}, {'ApplicationRule'})
        self.assertNotIn('keyVaultPrivateIps', template['parameters'])
        self.assertNotIn('enableCredentialBroker', template['parameters'])
        build = next(rule for collection in rules for rule in collection['rules'] if rule['name'] == 'allow-mgmt-vm-python-build')
        self.assertEqual(build['sourceAddresses'], [parameters['managementPrefix']])
        self.assertTrue({'pypi.org', 'files.pythonhosted.org'} <= set(build['targetFqdns']))
        self.assertEqual(build['protocols'], [{'protocolType': 'Https', 'port': 443}])
        self.assertNotIn('condition', resource)
        self.assertTrue({'azure.archive.ubuntu.com', 'archive.ubuntu.com', 'security.ubuntu.com'}
                        <= set(self.network['variables']['linuxPackageFqdns']))


if __name__ == '__main__': unittest.main()
