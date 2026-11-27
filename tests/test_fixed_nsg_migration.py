"""Exercise NSG staging, discovery and retirement with synthetic ARM resources."""
from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers import fixed_nsg as nsg
from deployment.helpers import management_host_time as host_time


def fixture_config():
    return {'subscriptionId': '11111111-1111-4111-8111-111111111111', 'productName': 'fixture',
            'environmentName': 'test', 'resourceStem': 'test-fixture', 'location': 'japaneast',
            'foundryLocation': 'eastus2', 'services': {'apimInstanceId': 'abc123'},
            'resourceGroupNames': {k: 'rg-test-fixture-'+k for k in ('nw', 'svc', 'maint', 'monitor')},
            'network': {'subnetPrefixes': {'agw': '10.70.0.0/24', 'pep': '10.70.1.0/24',
                        'firewall': '10.70.2.0/26', 'apim': '10.70.2.64/26',
                        'management': '10.70.2.128/26', 'aca': '10.70.2.192/26'}}}


def fixture_bindings(config):
    return {'subnets': copy.deepcopy(config['network']['subnetPrefixes']),
            'ips': {'apim': '10.70.2.68', 'aca': '10.70.2.196', 'management': '10.70.2.132',
                    'firewallPublic': '192.0.2.10', 'appGatewayPublic': '198.51.100.10'},
            'ipSets': {'foundry': ['10.70.1.4', '10.70.1.5'], 'keyVault': ['10.70.1.6', '10.70.1.7'],
                       'registry': ['10.70.1.8', '10.70.1.9'], 'sql': ['10.70.1.10', '10.70.1.11'],
                       'auditStorageJapan': ['10.70.1.12', '10.70.1.13'],
                       'auditStorageFoundry': ['10.70.1.14', '10.70.1.15']}}


def writable(canonical):
    result = {k: canonical[k] for k in ('priority', 'direction', 'access', 'protocol')}
    for one, many in nsg._PAIRS:
        values = canonical[one]
        result[one if len(values) == 1 else many] = values[0] if len(values) == 1 else list(values)
    result['provisioningState'] = 'Succeeded'
    return result


class MemoryTransport:
    """Store synthetic resources and implement conditional child-only mutations."""
    def __init__(self, config, *, legacy=False, empty=False):
        self.config = config; self.bindings = fixture_bindings(config); self.resources = {}; self.calls = []
        self.role_assignment_collections = {}
        self.serial = 1; self.get_hook = None; self.fail_after_put = False; self.hold_pending = False
        self.config = copy.deepcopy(config)
        self.groups, self.stem, self.prefixes = nsg._scope(config)
        self.tags = {'product': config['productName'], 'environment': config['environmentName'], 'azd-env-name': config['environmentName']}
        self.nw = self.groups['nw']+'/providers/Microsoft.Network/'
        self.vnet_id = self.nw+'virtualNetworks/vnet-'+self.stem
        self.subnets = {k: self.vnet_id+'/subnets/'+('AzureFirewallSubnet' if k=='firewall' else 'snet-'+self.stem+'-'+nsg.SUFFIXES[k])
                        for k in (*nsg.GROUPS, 'firewall')}
        for identity in self.groups.values():
            self.resources[identity] = {'id': identity, 'name': identity.rsplit('/', 1)[-1], 'tags': self.tags}
        subnet_properties = []
        for key, identity in self.subnets.items():
            properties = {'addressPrefix': self.prefixes[key]}
            if key == 'pep':
                properties['privateEndpointNetworkPolicies'] = 'NetworkSecurityGroupEnabled'
            if key != 'firewall' and not (legacy and key=='aca'):
                properties['networkSecurityGroup'] = {'id': self.nsg_id(key)}
            subnet_properties.append({'id': identity, 'name': identity.rsplit('/', 1)[-1], 'properties': properties})
        self.add(self.vnet_id, 'Microsoft.Network/virtualNetworks', {'subnets': subnet_properties})
        self.add(self.nw + 'firewallPolicies/afwp-' + self.stem, 'Microsoft.Network/firewallPolicies',
                 {'dnsSettings': {'enableProxy': True, 'servers': []}})
        manifest = nsg._catalog('nsg-legacy-rule-definitions.json' if legacy else 'nsg-rule-priorities.json')
        for key in nsg.GROUPS:
            if legacy and key=='aca': continue
            # The historical pre-fixed fixture predates the v2 management NTP child.
            # v2 retirement is covered independently rather than changing its old 45-rule history.
            rules = [] if empty else [self.child(self.nsg_id(key), name, nsg._definition(rule, self.bindings))
                    for name, rule in manifest['groups'][key]['rules'].items()
                    if not (legacy and key == 'management' and name == 'allow-time-sync-egress')]
            self.add(self.nsg_id(key), nsg.NSG_TYPE, {'securityRules': rules, 'defaultSecurityRules': self.defaults(key),
                     'subnets': [{'id': self.subnets[key]}], 'networkInterfaces': []})
        for key, suffix in (('firewallPublic', 'pip-afw-'), ('appGatewayPublic', 'pip-agw-')):
            self.add(self.nw+'publicIPAddresses/'+suffix+self.stem, 'Microsoft.Network/publicIPAddresses',
                     {'ipAddress': self.bindings['ips'][key], 'publicIPAllocationMethod': 'Static'})
        svc = self.groups['svc']+'/providers/'
        self.add(svc+'Microsoft.ApiManagement/service/apim-'+self.stem+'-abc123', 'Microsoft.ApiManagement/service',
                 {'privateIPAddresses': [self.bindings['ips']['apim']], 'virtualNetworkType': 'Internal',
                  'virtualNetworkConfiguration': {'subnetResourceId': self.subnets['apim']}})
        self.add(svc+'Microsoft.App/managedEnvironments/cae-'+self.stem, 'Microsoft.App/managedEnvironments',
                 {'staticIp': self.bindings['ips']['aca'], 'vnetConfiguration': {'internal': True, 'infrastructureSubnetId': self.subnets['aca']}})
        self.vm_id = self.groups['maint']+'/providers/Microsoft.Compute/virtualMachines/vm-'+self.stem+'-mgmt'
        self.nic_id = self.groups['maint']+'/providers/Microsoft.Network/networkInterfaces/nic-'+self.stem+'-mgmt'
        self.add(self.vm_id, 'Microsoft.Compute/virtualMachines', {
            'vmId': str(uuid.UUID(int=2)), 'timeCreated': '2026-11-01T09:00:00Z',
            'networkProfile': {'networkInterfaces': [{'id': self.nic_id}]}})
        self.host_time_command_id = self.vm_id + '/runCommands/configure-linux-audit'
        self.add(self.host_time_command_id, 'Microsoft.Compute/virtualMachines/runCommands', {
            'source': {'script': host_time.GUEST_SCRIPT.read_text(encoding='utf-8')},
            'instanceView': {'executionState': 'Succeeded', 'exitCode': 0,
                'startTime': '2026-11-01T10:00:00Z', 'endTime': '2026-11-01T10:01:00Z',
                'output': json.dumps({'status': 'configured', 'timeSource': 'azure-host-phc',
                                     'timeSynchronized': True, 'timeService': 'chrony.service'}) + '\n'}})
        self.add(self.nic_id, 'Microsoft.Network/networkInterfaces', {'virtualMachine': {'id': self.vm_id},
                 'ipConfigurations': [{'properties': {'subnet': {'id': self.subnets['management']}, 'privateIPAddress': self.bindings['ips']['management']}}]})
        mon = self.groups['monitor'] + '/providers/Microsoft.Storage/storageAccounts/'
        targets = (('foundry', 'foundry', svc+'Microsoft.CognitiveServices/accounts/aif-'+self.stem, 'Microsoft.CognitiveServices/accounts', ['account']),
                   ('keyVault', 'kv', svc+'Microsoft.KeyVault/vaults/kv-'+self.stem, 'Microsoft.KeyVault/vaults', ['vault']),
                   ('registry', 'acr', svc+'Microsoft.ContainerRegistry/registries/acr'+self.stem.replace('-', ''), 'Microsoft.ContainerRegistry/registries', ['registry']),
                   ('sql', 'sql', svc+'Microsoft.Sql/servers/sql-'+self.stem, 'Microsoft.Sql/servers', ['sqlServer']),
                   ('auditStorageJapan', 'audit-japan-blob', mon+'st'+self.stem.replace('-', '')+'jp', 'Microsoft.Storage/storageAccounts', ['blob']),
                   ('auditStorageFoundry', 'audit-foundry-blob', mon+'st'+self.stem.replace('-', '')+'us', 'Microsoft.Storage/storageAccounts', ['blob']))
        for key, suffix, target_id, kind, group_ids in targets:
            self.add(target_id, kind, {}, location='eastus2' if key in ('foundry', 'auditStorageFoundry') else 'japaneast')
            pe_id = self.nw+'privateEndpoints/pe-'+self.stem+'-'+suffix
            nic_id = self.nw+'networkInterfaces/nic-'+suffix+'-synthetic'
            self.add(pe_id, 'Microsoft.Network/privateEndpoints', {'subnet': {'id': self.subnets['pep']},
                     'privateLinkServiceConnections': [{'properties': {'privateLinkServiceId': target_id, 'groupIds': group_ids,
                         'privateLinkServiceConnectionState': {'status': 'Approved'}}}], 'networkInterfaces': [{'id': nic_id}]})
            self.add(nic_id, 'Microsoft.Network/networkInterfaces', {'privateEndpoint': {'id': pe_id},
                     'ipConfigurations': [{'properties': {'subnet': {'id': self.subnets['pep']}, 'privateIPAddress': ip}}
                                           for ip in self.bindings['ipSets'][key]]})

    def nsg_id(self, group): return self.nw+'networkSecurityGroups/nsg-'+self.stem+'-'+nsg.SUFFIXES[group]

    def add(self, identity, kind, properties, *, location='japaneast'):
        self.resources[identity] = {'id': identity, 'name': identity.rsplit('/', 1)[-1], 'type': kind,
            'location': location, 'tags': copy.deepcopy(self.tags), 'etag': '"parent-1"',
            'properties': {'provisioningState': 'Succeeded', **copy.deepcopy(properties)}}

    def child(self, parent_id, name, canonical):
        return {'id': parent_id+'/securityRules/'+name, 'name': name, 'type': nsg.NSG_TYPE+'/securityRules',
                'etag': '"child-1"', 'properties': writable(canonical)}

    def defaults(self, group):
        result = []
        for name, direction, priority, access, src, dst in (
            ('AllowVnetInBound', 'Inbound', 65000, 'Allow', 'VirtualNetwork', 'VirtualNetwork'),
            ('AllowAzureLoadBalancerInBound', 'Inbound', 65001, 'Allow', 'AzureLoadBalancer', '*'),
            ('DenyAllInBound', 'Inbound', 65500, 'Deny', '*', '*'),
            ('AllowVnetOutBound', 'Outbound', 65000, 'Allow', 'VirtualNetwork', 'VirtualNetwork'),
            ('AllowInternetOutBound', 'Outbound', 65001, 'Allow', '*', 'Internet'),
            ('DenyAllOutBound', 'Outbound', 65500, 'Deny', '*', '*')):
            result.append({'id': self.nsg_id(group)+'/defaultSecurityRules/'+name, 'name': name,
                'properties': {'direction': direction, 'priority': priority, 'access': access, 'protocol': '*',
                    'sourcePortRange': '*', 'destinationPortRange': '*', 'sourceAddressPrefix': src, 'destinationAddressPrefix': dst}})
        return result

    def mutations(self): return [c for c in self.calls if c['method'] != 'GET']

    def request(self, method, path, body=None, headers=None):
        identity = path.split('?', 1)[0]
        self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': copy.deepcopy(headers)})
        if method=='GET' and self.get_hook: self.get_hook(self, identity)
        if method == 'GET' and identity.endswith('/providers/Microsoft.Authorization/roleAssignments'):
            return {'statusCode': 200, 'body': copy.deepcopy(
                self.role_assignment_collections.get(identity, {'value': []}))}
        if '/securityRules/' in identity:
            parent_id, name = identity.split('/securityRules/', 1); parent = self.resources.get(parent_id)
            child = next((r for r in parent['properties']['securityRules'] if r['name']==name), None) if parent else None
            if method=='GET': return {'statusCode': 200 if child else 404, 'body': copy.deepcopy(child)}
            if not child: return {'statusCode': 404, 'body': None}
            if headers != {'If-Match': child['etag']}: return {'statusCode': 412, 'body': None}
            self.serial += 1
            if method=='PUT':
                child['properties'] = {**copy.deepcopy(body['properties']), 'provisioningState': 'Updating' if self.hold_pending else 'Succeeded'}
                child['etag'] = '"child-'+str(self.serial)+'"'
            elif method=='DELETE': parent['properties']['securityRules'].remove(child)
            else: raise AssertionError('Only child PUT/DELETE is permitted')
            parent['etag'] = '"parent-'+str(self.serial)+'"'
            parent['properties']['provisioningState'] = 'Updating' if self.hold_pending else 'Succeeded'
            if self.fail_after_put and method=='PUT': self.fail_after_put=False; raise OSError('Synthetic dropped response')
            return {'statusCode': 200 if method=='PUT' else 204, 'body': copy.deepcopy(child)}
        if method != 'GET': raise AssertionError('No parent or topology mutation is permitted')
        value = self.resources.get(identity)
        return {'statusCode': 200 if value else 404, 'body': copy.deepcopy(value)}

    def deploy_fixed(self):
        manifest = nsg._catalog('nsg-rule-priorities.json')
        for group in nsg.GROUPS:
            identity = self.nsg_id(group)
            if identity not in self.resources:
                self.add(identity, nsg.NSG_TYPE, {'securityRules': [], 'defaultSecurityRules': self.defaults(group),
                         'subnets': [{'id': self.subnets[group]}], 'networkInterfaces': []})
                subnet = next(s for s in self.resources[self.vnet_id]['properties']['subnets'] if s['id']==self.subnets[group])
                subnet['properties']['networkSecurityGroup'] = {'id': identity}
            parent = self.resources[identity]
            for name, rule in manifest['groups'][group]['rules'].items():
                parent['properties']['securityRules'] = [r for r in parent['properties']['securityRules'] if r['name']!=name]
                parent['properties']['securityRules'].append(self.child(identity, name, nsg._definition(rule, self.bindings)))

    def use_previous_platform_audit_rule_names(self):
        previous = []
        for group, name in (('management', 'allow-audit-storage-platform-https-egress'),
                            ('pep', 'allow-mgmt-vm-audit-storage-platform-https-ingress')):
            child = next(rule for rule in self.resources[self.nsg_id(group)]['properties']['securityRules']
                         if rule['name'] == name)
            old_name = name.replace('storage-platform', 'storage-japan')
            child['id'] = self.nsg_id(group) + '/securityRules/' + old_name
            child['name'] = old_name
            previous.append(child)
        return previous


class FixedNsgMigrationTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()
        self.events = []

    def record(self, event, value):
        # All bodies in this test are independently synthesized, never live ARM output.
        self.events.append((event, copy.deepcopy(value)))

    @staticmethod
    def add_v2_management_ntp(transport):
        """Recreate the independently specified v2 child, including its original priority."""
        parent = transport.resources[transport.nsg_id('management')]
        child = transport.child(parent['id'], 'allow-time-sync-egress', {
            'priority': 325, 'direction': 'Outbound', 'access': 'Allow', 'protocol': 'Udp',
            'sourcePortRange': ['*'], 'destinationPortRange': ['123'],
            'sourceAddressPrefix': [transport.prefixes['management']], 'destinationAddressPrefix': ['Internet'],
        })
        parent['properties']['securityRules'].append(child)
        return child

    def test_v2_management_ntp_retires_conditionally_without_changing_any_other_rule(self):
        transport = MemoryTransport(self.config)
        before = {group: nsg._snapshot(transport.resources[transport.nsg_id(group)]) for group in nsg.GROUPS}
        old = self.add_v2_management_ntp(transport)
        prepared = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual((prepared['plannedMoves'], prepared['azureWrites']), (1, 1))
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True,
            record=self.record, wait_seconds=0)['azureWrites'], 0)
        transport.deploy_fixed()
        completed = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual((completed['plannedDeletes'], completed['azureWrites']), (1, 1))
        self.assertEqual(nsg.complete_fixed_nsg_rules(self.config, transport, execute=True,
            record=self.record, wait_seconds=0)['azureWrites'], 0)
        self.assertEqual([call['method'] for call in transport.mutations()], ['PUT', 'DELETE'])
        self.assertTrue(all(call['path'].split('?')[0] == old['id'] and call['headers']['If-Match'] != '*'
                            for call in transport.mutations()))
        after = {group: nsg._snapshot(transport.resources[transport.nsg_id(group)]) for group in nsg.GROUPS}
        self.assertEqual(after, before)

    def test_v2_ntp_is_classified_when_it_is_the_only_existing_child(self):
        transport = MemoryTransport(self.config, empty=True)
        old = self.add_v2_management_ntp(transport)
        plan = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual((plan['plannedMoves'], plan['azureWrites']), (1, 1))
        self.assertEqual(transport.mutations()[0]['path'].split('?')[0], old['id'])
        transport.deploy_fixed()
        result = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual((result['plannedDeletes'], result['azureWrites']), (1, 1))
        self.assertNotIn('allow-time-sync-egress', nsg._inventory(transport.resources[transport.nsg_id('management')]))
        self.assertIn('allow-time-sync-egress', nsg._inventory(transport.resources[transport.nsg_id('apim')]))

    def test_modified_v2_ntp_stops_before_any_mutation(self):
        for field, value in (('priority', 326), ('protocol', 'Tcp'), ('destinationPortRange', '319'),
                             ('destinationAddressPrefix', '192.0.2.123/32'), ('sourceAddressPrefix', '*')):
            transport = MemoryTransport(self.config)
            child = self.add_v2_management_ntp(transport)
            child['properties'][field] = value
            before = copy.deepcopy(transport.resources)
            with self.subTest(field=field), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertFalse(transport.mutations())
            self.assertEqual(transport.resources, before)

    def test_ntp_retirement_gate_failure_keeps_all_old_rules_before_the_first_delete(self):
        for legacy in (False, True):
            transport = MemoryTransport(self.config, legacy=legacy)
            if not legacy:
                self.add_v2_management_ntp(transport)
            nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            transport.deploy_fixed(); transport.calls.clear()
            before = copy.deepcopy(transport.resources)
            with self.subTest(legacy=legacy), mock.patch.object(nsg, 'require_management_host_time_core_ready',
                    side_effect=host_time.ManagementHostTimeError('PHC preparation required')) as gate:
                with self.assertRaisesRegex(nsg.FixedNsgError, 'PHC preparation required'):
                    nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                gate.assert_called_once_with(self.config, transport)
            self.assertFalse(transport.mutations())
            self.assertEqual(transport.resources, before)

    def test_missing_phc_proof_blocks_direct_ns_only_retirement(self):
        transport = MemoryTransport(self.config)
        self.add_v2_management_ntp(transport)
        nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_fixed(); transport.calls.clear()
        transport.resources.pop(transport.host_time_command_id)
        with self.assertRaises(nsg.FixedNsgError):
            nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertFalse(transport.mutations())
        self.assertIn('allow-time-sync-egress', nsg._inventory(transport.resources[transport.nsg_id('management')]))

    def test_new_vm_readiness_result_allows_owned_ntp_retirement(self):
        transport = MemoryTransport(self.config)
        self.add_v2_management_ntp(transport)
        nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_fixed(); transport.calls.clear()
        with mock.patch.object(nsg, 'require_management_host_time_core_ready', return_value={
                'newManagementVm': True, 'hostTimePreparationRequired': False}) as gate:
            result = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        gate.assert_called_once_with(self.config, transport)
        self.assertEqual((result['plannedDeletes'], result['azureWrites']), (1, 1))

    def test_ns_only_retirement_without_management_ntp_does_not_read_host_time(self):
        transport = MemoryTransport(self.config, legacy=True)
        parent = transport.resources[transport.nsg_id('management')]
        parent['properties']['securityRules'] = [row for row in parent['properties']['securityRules']
                                                 if row['name'] != 'allow-time-sync']
        nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_fixed(); transport.calls.clear()
        with mock.patch.object(nsg, 'require_management_host_time_core_ready',
                               side_effect=AssertionError('No management time-source retirement')) as gate:
            result = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        gate.assert_not_called()
        self.assertEqual((result['plannedDeletes'], result['azureWrites']), (44, 44))

    def test_execute_requires_a_callable_journal_before_transport_use(self):
        for operation in (nsg.prepare_fixed_nsg_rules, nsg.complete_fixed_nsg_rules):
            for record in (None, [], {}, False, 'not-a-callback'):
                transport = MemoryTransport(self.config, legacy=True)
                with self.subTest(operation=operation.__name__, record=record), self.assertRaisesRegex(
                        nsg.FixedNsgError, 'protected record callback'):
                    operation(self.config, transport, execute=True, record=record)
                self.assertFalse(transport.calls)
        transport = MemoryTransport(self.config, legacy=True)
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport)['status'], 'planned')
        self.assertFalse(transport.mutations())
        transport = MemoryTransport(self.config)
        self.assertEqual(nsg.complete_fixed_nsg_rules(self.config, transport)['status'], 'planned')
        self.assertFalse(transport.mutations())

    def test_failed_plan_or_write_ahead_journal_prevents_the_first_mutation(self):
        for operation in (nsg.prepare_fixed_nsg_rules, nsg.complete_fixed_nsg_rules):
            for failing_event in ('plan', 'attempt'):
                transport = MemoryTransport(self.config, legacy=True)
                if operation is nsg.complete_fixed_nsg_rules:
                    nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                    transport.deploy_fixed(); transport.calls.clear()
                def fail(event, value):
                    if event == failing_event:
                        raise OSError('Synthetic protected journal failure')
                with self.subTest(operation=operation.__name__, event=failing_event), self.assertRaises(OSError):
                    operation(self.config, transport, execute=True, record=fail, wait_seconds=0)
                self.assertFalse(transport.mutations())

    def test_read_only_discovery_checks_all_pe_ips_and_does_not_write(self):
        transport = MemoryTransport(self.config)
        self.assertEqual(nsg.read_fixed_nsg_bindings(self.config, transport), transport.bindings)
        self.assertFalse(transport.mutations())

    def test_audit_blob_discovery_keeps_every_ip_across_every_endpoint_nic(self):
        transport = MemoryTransport(self.config)
        for key, suffix, address in (('auditStorageJapan', 'audit-japan-blob', '10.70.1.16'),
                                     ('auditStorageFoundry', 'audit-foundry-blob', '10.70.1.17')):
            pe_id = transport.nw + 'privateEndpoints/pe-' + transport.stem + '-' + suffix
            nic_id = transport.nw + 'networkInterfaces/nic-' + suffix + '-additional'
            transport.resources[pe_id]['properties']['networkInterfaces'].append({'id': nic_id})
            transport.add(nic_id, 'Microsoft.Network/networkInterfaces', {
                'privateEndpoint': {'id': pe_id}, 'ipConfigurations': [{'properties': {
                    'subnet': {'id': transport.subnets['pep']}, 'privateIPAddress': address}}]})
            transport.bindings['ipSets'][key].append(address)
        discovered = nsg.read_fixed_nsg_bindings(self.config, transport)
        self.assertEqual(discovered, transport.bindings)
        manifest = nsg._catalog('nsg-rule-priorities.json')
        for key, suffix in (('auditStorageJapan', 'platform'), ('auditStorageFoundry', 'foundry')):
            allowed = [ip + '/32' for ip in discovered['ipSets'][key]]
            for group, name in (('management', 'allow-audit-storage-' + suffix + '-https-egress'),
                                ('pep', 'allow-mgmt-vm-audit-storage-' + suffix + '-https-ingress')):
                with self.subTest(group=group, storage=key):
                    rule = nsg._definition(manifest['groups'][group]['rules'][name], discovered)
                    self.assertEqual(rule['sourceAddressPrefix'], [discovered['ips']['management'] + '/32'])
                    self.assertEqual(rule['destinationAddressPrefix'], allowed)
                    self.assertEqual(rule['destinationPortRange'], ['443'])
        self.assertFalse(transport.mutations())

    def test_audit_blob_foreign_or_incomplete_endpoint_stops_before_mutation(self):
        for change in ('target', 'group', 'approval', 'subnet', 'backlink', 'duplicate-ip', 'foreign-rg', 'missing-pe'):
            transport = MemoryTransport(self.config)
            pe_id = transport.nw + 'privateEndpoints/pe-' + transport.stem + '-audit-foundry-blob'
            nic_id = transport.nw + 'networkInterfaces/nic-audit-foundry-blob-synthetic'
            endpoint = transport.resources[pe_id]['properties']
            connection = endpoint['privateLinkServiceConnections'][0]['properties']
            if change == 'target': connection['privateLinkServiceId'] = '/synthetic/foreign-storage'
            elif change == 'group': connection['groupIds'] = ['file']
            elif change == 'approval': connection['privateLinkServiceConnectionState']['status'] = 'Pending'
            elif change == 'subnet': endpoint['subnet']['id'] = transport.subnets['management']
            elif change == 'backlink': transport.resources[nic_id]['properties']['privateEndpoint']['id'] = '/synthetic/foreign-pe'
            elif change == 'duplicate-ip':
                transport.resources[nic_id]['properties']['ipConfigurations'][0]['properties']['privateIPAddress'] = transport.bindings['ipSets']['auditStorageJapan'][0]
            elif change == 'foreign-rg': transport.resources[transport.groups['monitor']]['tags'] = {'product': 'foreign'}
            else: transport.resources.pop(pe_id)
            with self.subTest(change=change), self.assertRaises(nsg.FixedNsgError):
                nsg.read_fixed_nsg_bindings(self.config, transport)
            self.assertFalse(transport.mutations())

    def test_core_preparation_resumes_before_audit_pe_creation_but_completion_requires_them(self):
        for legacy, expected_moves in ((False, 1), (True, 45)):
            transport = MemoryTransport(self.config, legacy=legacy)
            # The old fixed catalog had 72 children and no audit Blob endpoint.
            if not legacy:
                self.add_v2_management_ntp(transport)
            for group in ('management', 'pep'):
                parent = transport.resources[transport.nsg_id(group)]
                parent['properties']['securityRules'] = [
                    rule for rule in parent['properties']['securityRules'] if 'audit-storage-' not in rule['name']]
            if not legacy:
                self.assertEqual(sum(len(nsg._inventory(transport.resources[transport.nsg_id(group)]))
                                     for group in nsg.GROUPS), 70)
            saved_endpoints = {}
            for suffix in ('audit-japan-blob', 'audit-foundry-blob'):
                identity = transport.nw + 'privateEndpoints/pe-' + transport.stem + '-' + suffix
                saved_endpoints[identity] = transport.resources.pop(identity)
            with self.subTest(legacy=legacy):
                prepared = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                self.assertEqual(prepared['azureWrites'], expected_moves)
                self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True,
                    record=self.record, wait_seconds=0)['azureWrites'], 0)
                transport.calls.clear()
                with self.assertRaises(nsg.FixedNsgError):
                    nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                with self.assertRaises(nsg.FixedNsgError):
                    nsg.fixed_nsg_upgrade_parameters(self.config, transport)
                self.assertFalse(transport.mutations())
                transport.resources.update(saved_endpoints)
                transport.deploy_fixed()
                completed = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                self.assertEqual(completed['azureWrites'], expected_moves)
                self.assertEqual(sum(len(nsg._inventory(transport.resources[transport.nsg_id(group)]))
                                     for group in nsg.GROUPS), 73)

    def test_pending_audit_phase_cannot_ignore_a_rule_whose_binding_is_missing(self):
        transport = MemoryTransport(self.config)
        transport.resources.pop(transport.nw + 'privateEndpoints/pe-' + transport.stem + '-audit-japan-blob')
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertFalse(transport.mutations())

    def test_legacy_priority_staging_then_fixed_deploy_then_owned_retirement(self):
        transport = MemoryTransport(self.config, legacy=True)
        before = copy.deepcopy(transport.resources)
        prepared = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(prepared['azureWrites'], 45)
        self.assertEqual(self.events[0][0], 'plan')
        self.assertEqual(self.events[-1][0], 'result')
        self.assertEqual(sum(event == 'attempt' for event, _ in self.events), 45)
        self.assertEqual(sum(event == 'staged' for event, _ in self.events), 45)
        writes = transport.mutations(); self.assertTrue(all(c['method']=='PUT' for c in writes))
        self.assertEqual(writes[0]['body']['properties']['priority'], 4080)
        for group in nsg.GROUPS[:-1]:
            current = transport.resources[transport.nsg_id(group)]
            old = before[transport.nsg_id(group)]
            self.assertEqual(current['properties']['defaultSecurityRules'], old['properties']['defaultSecurityRules'])
            for original in old['properties']['securityRules']:
                moved = next(r for r in current['properties']['securityRules'] if r['name']==original['name'])
                self.assertEqual({**nsg._properties(moved), 'priority': original['properties']['priority']}, nsg._properties(original))
                self.assertLess(moved['properties']['priority'], 65000)
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)['azureWrites'], 0)
        transport.deploy_fixed()
        completed = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(completed['azureWrites'], 45)
        self.assertEqual(sum(len(transport.resources[transport.nsg_id(g)]['properties']['securityRules']) for g in nsg.GROUPS), 73)
        self.assertEqual(nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)['azureWrites'], 0)
        self.assertTrue(all('/securityRules/' in c['path'] and c['headers']['If-Match']!='*' for c in transport.mutations()))

    def test_unknown_manual_or_conflicting_rules_stop_before_any_mutation(self):
        for change in ('unknown', 'condition', 'operator', 'extra-property', 'priority'):
            transport = MemoryTransport(self.config, legacy=True)
            parent = transport.resources[transport.nsg_id('management')]
            if change in ('unknown', 'operator'):
                name = 'allow-operator-management' if change=='operator' else 'manual-enterprise-rule'
                properties = {'priority': 777, 'direction': 'Outbound', 'access': 'Allow', 'protocol': 'Tcp',
                              'sourcePortRange': ['*'], 'destinationPortRange': ['443'], 'sourceAddressPrefix': ['10.70.2.128/26'], 'destinationAddressPrefix': ['192.0.2.25/32']}
                parent['properties']['securityRules'].append(transport.child(parent['id'], name, properties))
            else:
                p = parent['properties']['securityRules'][0]['properties']
                if change=='condition': p['destinationAddressPrefix']='192.0.2.40/32'; p.pop('destinationAddressPrefixes', None)
                elif change=='extra-property': p['sourceApplicationSecurityGroups']=[{'id': '/synthetic/asg'}]
                elif change=='priority': p['priority']=230
            with self.subTest(change=change), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertFalse(transport.mutations())

    def test_all_fixed_tuples_are_required_before_first_delete(self):
        transport = MemoryTransport(self.config, legacy=True)
        nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_fixed(); transport.calls.clear()
        transport.resources[transport.nsg_id('aca')]['properties']['securityRules'].pop()
        with self.assertRaises(nsg.FixedNsgError): nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record)
        self.assertFalse(transport.mutations())

    def test_missing_audit_blob_allow_stops_legacy_retirement_before_any_delete(self):
        for group, name in (('management', 'allow-audit-storage-platform-https-egress'),
                            ('management', 'allow-audit-storage-foundry-https-egress'),
                            ('pep', 'allow-mgmt-vm-audit-storage-platform-https-ingress'),
                            ('pep', 'allow-mgmt-vm-audit-storage-foundry-https-ingress')):
            transport = MemoryTransport(self.config, legacy=True)
            nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            transport.deploy_fixed()
            parent = transport.resources[transport.nsg_id(group)]
            parent['properties']['securityRules'] = [rule for rule in parent['properties']['securityRules'] if rule['name'] != name]
            transport.calls.clear()
            with self.subTest(group=group, name=name), self.assertRaises(nsg.FixedNsgError):
                nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertFalse(transport.mutations())

    def test_platform_audit_rename_keeps_owned_allows_until_new_rules_are_verified(self):
        transport = MemoryTransport(self.config)
        before = {group: nsg._snapshot(transport.resources[transport.nsg_id(group)]) for group in nsg.GROUPS}
        old_rules = transport.use_previous_platform_audit_rule_names()
        expected = {rule['id']: nsg._properties(rule) for rule in old_rules}
        plan = nsg.prepare_fixed_nsg_rules(self.config, transport)
        self.assertEqual((plan['plannedMoves'], plan['azureWrites']), (2, 0))
        prepared = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(prepared['azureWrites'], 2)
        for rule in old_rules:
            original = expected[rule['id']]
            actual = nsg._properties(rule)
            self.assertGreater(actual['priority'], original['priority'])
            self.assertLess(actual['priority'], 4096)
            self.assertEqual({**actual, 'priority': original['priority']}, original)
        with self.assertRaisesRegex(nsg.FixedNsgError, 'complete fixed NSG rule set'):
            nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record,
                                                     wait_seconds=0)['azureWrites'], 0)
        transport.deploy_fixed()
        completed = nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(completed['azureWrites'], 2)
        for group, original in before.items():
            self.assertEqual(nsg._snapshot(transport.resources[transport.nsg_id(group)]), original)
        mutations = transport.mutations()
        self.assertEqual([call['method'] for call in mutations], ['PUT', 'PUT', 'DELETE', 'DELETE'])
        self.assertEqual({call['path'].split('?')[0] for call in mutations}, expected.keys())

    def test_platform_audit_rename_rejects_a_modified_previous_rule(self):
        transport = MemoryTransport(self.config)
        previous, _ = transport.use_previous_platform_audit_rule_names()
        previous['properties']['destinationPortRange'] = '80'
        with self.assertRaisesRegex(nsg.FixedNsgError, 'exact previous rule definition'):
            nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertFalse(transport.mutations())

    def test_dropped_put_response_is_not_retried_and_can_resume_after_readback(self):
        transport = MemoryTransport(self.config, legacy=True); transport.fail_after_put=True
        with self.assertRaises(nsg.FixedNsgError): nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(len(transport.mutations()), 1)
        transport.calls.clear()
        result = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(result['azureWrites'], 44)

    def test_pending_write_timeout_stops_without_another_write(self):
        transport = MemoryTransport(self.config, legacy=True); transport.hold_pending=True
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, max_checks=2, wait_seconds=0)
        self.assertEqual(len(transport.mutations()), 1)

    def test_parent_race_and_child_etag_race_stop_before_write(self):
        for race in ('parent', 'child'):
            transport = MemoryTransport(self.config, legacy=True)
            staged = {'ready': False}
            original_classify = nsg._classify
            def ready(*args, **kwargs):
                result = original_classify(*args, **kwargs); staged['ready']=True; return result
            def hook(t, identity):
                if not staged['ready']: return
                parent = t.resources[t.nsg_id('agw')]
                if race=='parent' and identity==parent['id']:
                    parent['tags']['manual-note']='changed'; parent['etag']='"raced-parent"'; t.get_hook=None
                elif race=='child' and '/securityRules/' in identity:
                    name = identity.rsplit('/', 1)[-1]
                    next(r for r in parent['properties']['securityRules'] if r['name']==name)['etag']='"raced-child"'; t.get_hook=None
            transport.get_hook=hook
            with self.subTest(race=race), mock.patch.object(nsg, '_classify', side_effect=ready), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertFalse(transport.mutations())

    def test_invalid_or_wildcard_etags_stop_before_the_first_mutation(self):
        invalid = (None, '', '""', '*', '"*"', 'W/"*"', 'W/*', '"one","two"',
                   '"line\r\nbreak"', '"delete\x7f"', '" padded "', 'x' * 513)
        for value in invalid:
            for scope in ('parent', 'child'):
                transport = MemoryTransport(self.config, legacy=True)
                parent = transport.resources[transport.nsg_id('agw')]
                target = parent if scope == 'parent' else next(
                    r for r in parent['properties']['securityRules'] if r['properties']['priority'] == 4096)
                target['etag'] = value
                with self.subTest(etag=value, scope=scope), self.assertRaisesRegex(nsg.FixedNsgError, 'concrete'):
                    nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
                self.assertFalse(transport.mutations())
        self.assertEqual(nsg._etag({'etag': 'W/"opaque"'}), 'W/"opaque"')

    def test_wrong_pe_target_nic_backlink_subnet_or_group_ownership_stops_read_only(self):
        for change in ('target', 'backlink', 'subnet', 'ownership', 'firewall-nsg'):
            transport = MemoryTransport(self.config)
            if change=='target':
                pe = transport.resources[transport.nw+'privateEndpoints/pe-test-fixture-foundry']
                pe['properties']['privateLinkServiceConnections'][0]['properties']['privateLinkServiceId']='/synthetic/foreign-account'
            elif change=='backlink': transport.resources[transport.nw+'networkInterfaces/nic-foundry-synthetic']['properties']['privateEndpoint']['id']='/synthetic/foreign-pe'
            elif change=='subnet': transport.resources[transport.nic_id]['properties']['ipConfigurations'][0]['properties']['subnet']['id']=transport.subnets['apim']
            elif change=='ownership': transport.resources[transport.groups['svc']]['tags']={'product':'foreign'}
            else:
                subnet = next(s for s in transport.resources[transport.vnet_id]['properties']['subnets'] if s['name']=='AzureFirewallSubnet')
                subnet['properties']['networkSecurityGroup']={'id':transport.nsg_id('agw')}
            with self.subTest(change=change), self.assertRaises(nsg.FixedNsgError): nsg.read_fixed_nsg_bindings(self.config, transport)
            self.assertFalse(transport.mutations())

    def test_new_empty_and_static_only_builds_do_not_require_uncreated_ip_resources(self):
        transport = MemoryTransport(self.config, empty=True)
        for identity, value in list(transport.resources.items()):
            if value.get('type') and value['type'] not in (nsg.NSG_TYPE, 'Microsoft.Network/virtualNetworks'):
                transport.resources.pop(identity)
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record)['azureWrites'], 0)
        manifest = nsg._catalog('nsg-rule-priorities.json')
        rule = manifest['groups']['agw']['rules']['allow-client-http-ingress']
        parent = transport.resources[transport.nsg_id('agw')]
        parent['properties']['securityRules'].append(transport.child(parent['id'], 'allow-client-http-ingress', nsg._definition(rule, transport.bindings)))
        transport.resources.pop(transport.groups['svc']+'/providers/Microsoft.ApiManagement/service/apim-test-fixture-abc123', None)
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record)['status'], 'fixed-static-build')
        self.assertFalse(transport.mutations())

    def partial_dynamic_build(self):
        transport = MemoryTransport(self.config)
        manifest = nsg._catalog('nsg-rule-priorities.json')
        for group in nsg.GROUPS:
            parent = transport.resources[transport.nsg_id(group)]
            parent['properties']['securityRules'] = [child for child in parent['properties']['securityRules']
                if not any(token.split(':', 1)[1] in ('aca', 'registry', 'sql') and
                           token.split(':', 1)[0] in ('ip', 'address', 'ips', 'addresses')
                           for token in manifest['groups'][group]['rules'][child['name']]['source'] +
                                        manifest['groups'][group]['rules'][child['name']]['destination'])]
        unbuilt = {}
        for identity, value in list(transport.resources.items()):
            if (value.get('type') in ('Microsoft.App/managedEnvironments', 'Microsoft.Sql/servers',
                                      'Microsoft.ContainerRegistry/registries')
                    or identity.endswith(('-acr', '-sql', '/nic-acr-synthetic', '/nic-sql-synthetic'))):
                unbuilt[identity] = transport.resources.pop(identity)
        return transport, unbuilt

    def test_partial_dynamic_build_retries_without_reading_uncreated_later_stages(self):
        transport, unbuilt = self.partial_dynamic_build()
        for attempt in range(2):
            result = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertEqual((result['plannedMoves'], result['azureWrites']), (0, 0))
        self.assertFalse(set(unbuilt) & {call['path'].split('?')[0] for call in transport.calls})
        self.assertFalse(transport.mutations())
        with self.assertRaises(nsg.FixedNsgError):
            nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        with self.assertRaises(nsg.FixedNsgError):
            nsg.fixed_nsg_upgrade_parameters(self.config, transport)
        transport.resources.update(unbuilt)
        transport.deploy_fixed()
        self.assertEqual(nsg.complete_fixed_nsg_rules(self.config, transport, execute=True,
                                                   record=self.record, wait_seconds=0)['status'], 'completed')
        self.assertFalse(transport.mutations())

    def test_partial_dynamic_build_still_rejects_missing_dependencies_modified_rules_and_drift(self):
        for change in ('required-pe', 'altered-tuple', 'later-drift'):
            transport, _ = self.partial_dynamic_build()
            if change == 'required-pe':
                transport.resources.pop(transport.nw + 'privateEndpoints/pe-' + transport.stem + '-foundry')
            elif change == 'altered-tuple':
                child = next(child for child in transport.resources[transport.nsg_id('management')]['properties']['securityRules']
                             if child['name'] == 'allow-foundry-https-egress')
                child['properties']['destinationPortRange'] = '8443'
            else:
                reads = []
                def drift(current, identity):
                    if identity == current.nic_id:
                        reads.append(identity)
                        if len(reads) == 2:
                            current.resources[identity]['properties']['ipConfigurations'][0]['properties']['privateIPAddress'] = str(
                                ipaddress.ip_address(current.bindings['ips']['management']) + 1)
                transport.get_hook = drift
            with self.subTest(change=change), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            self.assertFalse(transport.mutations())

    def test_private_endpoint_subnet_requires_initial_nsg_enforcement(self):
        for policy in (None, 'Disabled', 'Enabled', 'RouteTableEnabled'):
            transport = MemoryTransport(self.config)
            subnet = next(value for value in transport.resources[transport.vnet_id]['properties']['subnets']
                          if value['id'] == transport.subnets['pep'])
            subnet['properties']['privateEndpointNetworkPolicies'] = policy
            with self.subTest(policy=policy), self.assertRaisesRegex(nsg.FixedNsgError, 'NSG enforcement'):
                nsg.read_fixed_nsg_bindings(self.config, transport)
            self.assertFalse(transport.mutations())


if __name__ == '__main__': unittest.main()
