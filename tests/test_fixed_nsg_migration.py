"""Exercise NSG staging, discovery and retirement with synthetic ARM resources."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers import fixed_nsg as nsg


def fixture_config():
    return {'subscriptionId': '11111111-1111-4111-8111-111111111111', 'productName': 'fixture',
            'environmentName': 'test', 'resourceStem': 'test-fixture', 'location': 'japaneast',
            'foundryLocation': 'eastus2', 'services': {'apimInstanceId': 'abc123'},
            'resourceGroupNames': {k: 'rg-test-fixture-'+k for k in ('nw', 'svc', 'maint')},
            'network': {'subnetPrefixes': {'agw': '10.70.0.0/24', 'pep': '10.70.1.0/24',
                        'firewall': '10.70.2.0/26', 'apim': '10.70.2.64/26',
                        'management': '10.70.2.128/26', 'aca': '10.70.2.192/26'}}}


def fixture_bindings(config):
    return {'subnets': copy.deepcopy(config['network']['subnetPrefixes']),
            'ips': {'apim': '10.70.2.68', 'aca': '10.70.2.196', 'management': '10.70.2.132',
                    'firewallPublic': '192.0.2.10', 'appGatewayPublic': '198.51.100.10'},
            'ipSets': {'foundry': ['10.70.1.4', '10.70.1.5'], 'keyVault': ['10.70.1.6', '10.70.1.7'],
                       'registry': ['10.70.1.8', '10.70.1.9'], 'sql': ['10.70.1.10', '10.70.1.11']}}


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
            if key != 'firewall' and not (legacy and key=='aca'):
                properties['networkSecurityGroup'] = {'id': self.nsg_id(key)}
            subnet_properties.append({'id': identity, 'name': identity.rsplit('/', 1)[-1], 'properties': properties})
        self.add(self.vnet_id, 'Microsoft.Network/virtualNetworks', {'subnets': subnet_properties})
        self.add(self.nw + 'firewallPolicies/afwp-' + self.stem, 'Microsoft.Network/firewallPolicies',
                 {'dnsSettings': {'enableProxy': True, 'servers': []}})
        manifest = nsg._catalog('nsg-legacy-rule-definitions.json' if legacy else 'nsg-rule-priorities.json')
        for key in nsg.GROUPS:
            if legacy and key=='aca': continue
            rules = [] if empty else [self.child(self.nsg_id(key), name, nsg._definition(rule, self.bindings))
                    for name, rule in manifest['groups'][key]['rules'].items()]
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
        self.add(self.vm_id, 'Microsoft.Compute/virtualMachines', {'networkProfile': {'networkInterfaces': [{'id': self.nic_id}]}})
        self.add(self.nic_id, 'Microsoft.Network/networkInterfaces', {'virtualMachine': {'id': self.vm_id},
                 'ipConfigurations': [{'properties': {'subnet': {'id': self.subnets['management']}, 'privateIPAddress': self.bindings['ips']['management']}}]})
        targets = (('foundry', 'foundry', 'Microsoft.CognitiveServices/accounts/aif-'+self.stem, 'Microsoft.CognitiveServices/accounts', ['account']),
                   ('keyVault', 'kv', 'Microsoft.KeyVault/vaults/kv-'+self.stem, 'Microsoft.KeyVault/vaults', ['vault']),
                   ('registry', 'acr', 'Microsoft.ContainerRegistry/registries/acr'+self.stem.replace('-', ''), 'Microsoft.ContainerRegistry/registries', ['registry']),
                   ('sql', 'sql', 'Microsoft.Sql/servers/sql-'+self.stem, 'Microsoft.Sql/servers', ['sqlServer']))
        for key, suffix, target, kind, group_ids in targets:
            target_id = svc+target
            self.add(target_id, kind, {}, location='eastus2' if key=='foundry' else 'japaneast')
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


class FixedNsgMigrationTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()
        self.events = []

    def record(self, event, value):
        # All bodies in this test are independently synthesized, never live ARM output.
        self.events.append((event, copy.deepcopy(value)))

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
        self.assertEqual(sum(len(transport.resources[transport.nsg_id(g)]['properties']['securityRules']) for g in nsg.GROUPS), 72)
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
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record)['azureWrites'], 0)
        manifest = nsg._catalog('nsg-rule-priorities.json')
        rule = manifest['groups']['agw']['rules']['allow-client-http-ingress']
        parent = transport.resources[transport.nsg_id('agw')]
        parent['properties']['securityRules'].append(transport.child(parent['id'], 'allow-client-http-ingress', nsg._definition(rule, transport.bindings)))
        transport.resources.pop(transport.groups['svc']+'/providers/Microsoft.ApiManagement/service/apim-test-fixture-abc123')
        self.assertEqual(nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record)['status'], 'fixed-static-build')
        self.assertFalse(transport.mutations())


if __name__ == '__main__': unittest.main()
