"""Exercise NSG verification and discovery with synthetic ARM resources."""
from __future__ import annotations

import copy
import ipaddress
import json
from ipaddress import ip_network
import unittest
import uuid
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment.helpers import fixed_nsg as nsg
from deployment.helpers import management_host_time as host_time


PORTAL_TERMINAL_STAGE_RULES = {
    'allow-aca-internal-ingress',
    'allow-firewall-routed-public-https-egress',
    'deny-any-ingress',
    'deny-any-egress',
}


def fixture_config():
    return {'subscriptionId': '11111111-1111-4111-8111-111111111111', 'productName': 'fixture',
            'environmentName': 'test', 'resourceStem': 'test-fixture', 'location': 'japaneast',
            'foundryLocation': 'eastus2', 'services': {'apimInstanceId': 'abc123'},
            'resourceGroupNames': {k: 'rg-test-fixture-'+k for k in ('nw', 'svc', 'maint', 'monitor')},
            'network': {'subnetPrefixes': {'agw': '10.70.0.0/24', 'pep': '10.70.1.0/24',
                        'firewall': '10.70.2.0/26', 'apim': '10.70.2.64/26',
                        'management': '10.70.2.128/26', 'portal': '10.70.2.192/26',
                        'broker': '203.0.113.0/26'}}}


def fixture_bindings(config):
    return {'subnets': copy.deepcopy(config['network']['subnetPrefixes']),
            'ips': {'apim': '10.70.2.68', 'portal': '10.70.2.196', 'management': '10.70.2.132',
                    'broker': str(ip_network(config['network']['subnetPrefixes']['broker'])[4]),
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
    def __init__(self, config, *, empty=False):
        self.config = config; self.bindings = fixture_bindings(config); self.resources = {}; self.calls = []
        self.role_assignment_collections = {}
        self.serial = 1; self.get_hook = None; self.fail_after_put = False; self.hold_pending = False
        self.config = copy.deepcopy(config)
        self.groups, self.stem, self.prefixes = nsg._scope(config)
        self.tags = {'product': config['productName'], 'environment': config['environmentName'], 'azd-env-name': config['environmentName']}
        self.nw = self.groups['nw']+'/providers/Microsoft.Network/'
        self.vnet_id = self.nw+'virtualNetworks/vnet-'+self.stem
        self.portal_route_table_id = self.nw + 'routeTables/rt-' + self.stem + '-portal'
        self.firewall_id = self.nw + 'azureFirewalls/afw-' + self.stem
        self.firewall_ip = str(ipaddress.ip_network(self.prefixes['firewall'])[4])
        subnet_names = {
            'agw': 'AppGatewaySubnet', 'apim': 'ApiManagementSubnet', 'pep': 'PrivateEndpointSubnet',
            'management': 'ManagementSubnet', 'portal': 'PortalSubnet', 'broker': 'CredentialBrokerSubnet',
            'firewall': 'AzureFirewallSubnet',
        }
        self.subnets = {key: self.vnet_id + '/subnets/' + name for key, name in subnet_names.items()}
        for identity in self.groups.values():
            self.resources[identity] = {'id': identity, 'name': identity.rsplit('/', 1)[-1], 'tags': self.tags}
        subnet_properties = []
        for key, identity in self.subnets.items():
            properties = {'addressPrefix': self.prefixes[key]}
            if key == 'pep':
                properties['privateEndpointNetworkPolicies'] = 'NetworkSecurityGroupEnabled'
            if key in ('portal', 'broker'):
                route_table = self.nw + 'routeTables/rt-' + self.stem + '-' + key
                properties.update(routeTable={'id': route_table}, defaultOutboundAccess=False)
            if key != 'firewall':
                properties['networkSecurityGroup'] = {'id': self.nsg_id(key)}
            subnet_properties.append({'id': identity, 'name': identity.rsplit('/', 1)[-1], 'properties': properties})
        self.add(self.vnet_id, 'Microsoft.Network/virtualNetworks', {'subnets': subnet_properties})
        firewall_policy_id = self.nw + 'firewallPolicies/afwp-' + self.stem
        self.add(firewall_policy_id, 'Microsoft.Network/firewallPolicies',
                 {'dnsSettings': {'enableProxy': True, 'servers': []}})
        self.add(self.firewall_id, 'Microsoft.Network/azureFirewalls', {
            'firewallPolicy': {'id': firewall_policy_id},
            'ipConfigurations': [{'properties': {
                'privateIPAddress': self.firewall_ip, 'subnet': {'id': self.subnets['firewall']}}}],
        })
        self.add(self.portal_route_table_id, 'Microsoft.Network/routeTables', {
            'disableBgpRoutePropagation': True,
            'routes': [{'name': name, 'properties': {'addressPrefix': destination,
                        'nextHopType': 'VirtualAppliance', 'nextHopIpAddress': self.firewall_ip}}
                       for name, destination in (('default-egress', '0.0.0.0/0'),
                                                 ('agw-return', self.prefixes['agw']))],
        })
        manifest = nsg._catalog('core-rules.json')
        for key in nsg.GROUPS:
            rules = [] if empty else [self.child(self.nsg_id(key), name, nsg._definition(rule, self.bindings))
                    for name, rule in manifest['groups'].get(key, {}).get('rules', {}).items()]
            self.add(self.nsg_id(key), nsg.NSG_TYPE, {'securityRules': rules, 'defaultSecurityRules': self.defaults(key),
                     'subnets': [{'id': self.subnets[key]}], 'networkInterfaces': []})
        for key, suffix in (('firewallPublic', 'pip-afw-'), ('appGatewayPublic', 'pip-agw-')):
            self.add(self.nw+'publicIPAddresses/'+suffix+self.stem, 'Microsoft.Network/publicIPAddresses',
                     {'ipAddress': self.bindings['ips'][key], 'publicIPAllocationMethod': 'Static'})
        svc = self.groups['svc']+'/providers/'
        self.add(svc+'Microsoft.ApiManagement/service/apim-'+self.stem+'-abc123', 'Microsoft.ApiManagement/service',
                 {'privateIPAddresses': [self.bindings['ips']['apim']], 'virtualNetworkType': 'Internal',
                  'virtualNetworkConfiguration': {'subnetResourceId': self.subnets['apim']}})
        for key, suffix in (('portal', ''), ('broker', '-broker')):
            self.add(svc+'Microsoft.App/managedEnvironments/cae-'+self.stem+suffix, 'Microsoft.App/managedEnvironments',
                     {'staticIp': self.bindings['ips'][key], 'publicNetworkAccess': 'Disabled', 'vnetConfiguration': {
                         'internal': True, 'infrastructureSubnetId': self.subnets[key]}})
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
            self.add(target_id, kind, {},
                     location='eastus2' if key in ('foundry', 'auditStorageFoundry') else 'japaneast')
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
        manifest = nsg._catalog('core-rules.json')
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


class FixedNsgValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()

    def test_read_only_discovery_checks_all_pe_ips_and_does_not_write(self):
        transport = MemoryTransport(self.config)
        self.assertEqual(nsg.read_fixed_nsg_bindings(self.config, transport), transport.bindings)
        self.assertFalse(transport.mutations())


    def test_broker_bindings_reject_shared_route_table_or_public_access(self):
        for issue in ('route-table', 'public-access'):
            transport = MemoryTransport(self.config)
            if issue == 'route-table':
                subnet = next(item for item in transport.resources[transport.vnet_id]['properties']['subnets']
                              if item['id'] == transport.subnets['broker'])
                subnet['properties']['routeTable']['id'] = transport.nw + 'routeTables/rt-' + transport.stem + '-portal'
                expected = 'Broker subnet route table'
            else:
                environment = transport.groups['svc'] + '/providers/Microsoft.App/managedEnvironments/cae-' + transport.stem + '-broker'
                transport.resources[environment]['properties']['publicNetworkAccess'] = 'Enabled'
                expected = 'Broker ACA environment'
            with self.subTest(issue=issue), self.assertRaisesRegex(nsg.FixedNsgError, expected):
                nsg.read_fixed_nsg_bindings(self.config, transport)
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
        manifest = nsg._catalog('core-rules.json')
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


    def test_portal_terminal_rule_with_a_modified_tuple_stops_before_writes(self):
        transport = MemoryTransport(self.config)
        portal_nsg = transport.resources[transport.nsg_id('portal')]
        terminal = next(rule for rule in portal_nsg['properties']['securityRules'] if rule['name'] == 'deny-any-egress')
        terminal['properties']['priority'] = 4095
        with self.assertRaises(nsg.FixedNsgError):
            nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        self.assertEqual(transport.mutations(), [])


    def test_pending_audit_phase_cannot_ignore_a_rule_whose_binding_is_missing(self):
        transport = MemoryTransport(self.config)
        transport.resources.pop(transport.nw + 'privateEndpoints/pe-' + transport.stem + '-audit-japan-blob')
        with self.assertRaises(nsg.FixedNsgError):
            nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        self.assertFalse(transport.mutations())


    def test_unknown_manual_or_conflicting_rules_stop_before_any_mutation(self):
        for change in ('unknown', 'condition', 'operator', 'extra-property', 'priority'):
            transport = MemoryTransport(self.config)
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
                nsg.verify_fixed_nsg_before_deployment(self.config, transport)
            self.assertFalse(transport.mutations())


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
        nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        manifest = nsg._catalog('core-rules.json')
        rule = manifest['groups']['agw']['rules']['allow-client-http-ingress']
        parent = transport.resources[transport.nsg_id('agw')]
        parent['properties']['securityRules'].append(transport.child(parent['id'], 'allow-client-http-ingress', nsg._definition(rule, transport.bindings)))
        transport.resources.pop(transport.groups['svc']+'/providers/Microsoft.ApiManagement/service/apim-test-fixture-abc123', None)
        nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        self.assertFalse(transport.mutations())


    def partial_dynamic_build(self):
        transport = MemoryTransport(self.config)
        manifest = nsg._catalog('core-rules.json')
        for group in nsg.GROUPS:
            parent = transport.resources[transport.nsg_id(group)]
            parent['properties']['securityRules'] = [child for child in parent['properties']['securityRules']
                if not any(token.split(':', 1)[1] in ('portal', 'broker', 'registry', 'sql') and
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
        for _ in range(2):
            nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        self.assertFalse(set(unbuilt) & {call['path'].split('?')[0] for call in transport.calls})
        self.assertFalse(transport.mutations())
        with self.assertRaises(nsg.FixedNsgError):
            nsg.verify_fixed_nsg_after_deployment(self.config, transport)
        transport.resources.update(unbuilt)
        transport.deploy_fixed()
        nsg.verify_fixed_nsg_after_deployment(self.config, transport)
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
                nsg.verify_fixed_nsg_before_deployment(self.config, transport)
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


    def test_core_can_resume_before_audit_endpoints_and_portal_terminal_rules_exist(self):
        transport = MemoryTransport(self.config)
        for group in ('management', 'pep', 'portal'):
            parent = transport.resources[transport.nsg_id(group)]
            parent['properties']['securityRules'] = [
                rule for rule in parent['properties']['securityRules']
                if 'audit-storage-' not in rule['name']
                and not (group == 'portal' and rule['name'] in PORTAL_TERMINAL_STAGE_RULES)]
        endpoints = {}
        for suffix in ('audit-japan-blob', 'audit-foundry-blob'):
            identity = transport.nw + 'privateEndpoints/pe-' + transport.stem + '-' + suffix
            endpoints[identity] = transport.resources.pop(identity)

        nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        with self.assertRaises(nsg.FixedNsgError):
            nsg.verify_fixed_nsg_after_deployment(self.config, transport)
        transport.resources.update(endpoints)
        with self.assertRaisesRegex(nsg.FixedNsgError, 'complete fixed NSG rule set'):
            nsg.verify_fixed_nsg_after_deployment(self.config, transport)
        transport.deploy_fixed()
        nsg.verify_fixed_nsg_after_deployment(self.config, transport)
        self.assertFalse(transport.mutations())

    def test_configured_certificate_subnet_is_required_at_completion(self):
        transport = MemoryTransport(self.config)
        self.config['network']['subnetPrefixes']['certificate'] = '192.0.2.0/27'
        nsg.verify_fixed_nsg_before_deployment(self.config, transport)
        with self.assertRaisesRegex(nsg.FixedNsgError, 'dedicated certificate subnet is missing'):
            nsg.verify_fixed_nsg_after_deployment(self.config, transport)
        self.assertFalse(transport.mutations())
