"""Compile all 76 fixed NSG rules with synthetic addresses; no Azure calls."""
import copy
import json
from pathlib import Path
import unittest

from bicep_helpers import compile_bicep
from test_parameter_ip_ownership import NetworkArmExpression

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ('agw', 'apim', 'pep', 'management', 'aca')
MODULES = ('network', 'network-bindings', 'portal-network', 'portal-network-bindings', 'credential-broker-network')


class FixedExpression(NetworkArmExpression):
    """Evaluate compiled UDFs, supplying only synthetic NIC discovery outputs."""
    def resolve(self, value, parameters):
        if isinstance(value, dict): return {k: self.resolve(v, parameters) for k, v in value.items()}
        if isinstance(value, list): return [self.resolve(v, parameters) for v in value]
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            return FixedExpression(value, self.template, self.cache).evaluate(parameters, 0)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] == 'reference':
            return {'outputs': {'privateIpsByNic': {'value': [parameters['referencePrivateIps']]}}}
        if tree[0] == 'call' and tree[1] == 'contains':
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            return values[1] in values[0]
        return super().evaluate(parameters, index, tree, bindings)


def canonical(p):
    def values(one, many): return [p[one]] if one in p else p[many]
    return {**{k: p[k] for k in ('direction', 'priority', 'access', 'protocol')},
            'sourcePort': p['sourcePortRange'],
            'source': values('sourceAddressPrefix', 'sourceAddressPrefixes'),
            'destination': values('destinationAddressPrefix', 'destinationAddressPrefixes'),
            'destinationPorts': values('destinationPortRange', 'destinationPortRanges')}


class NsgRulePriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / 'infra/nsg-rule-priorities.json').read_text())
        cls.templates = {m: compile_bicep('infra/modules/' + m + '.bicep') for m in MODULES}

    def parameters(self, multiple=True, prefix='10.70'):
        subnets = {'agw': prefix+'.0.0/24', 'pep': prefix+'.1.0/24', 'firewall': prefix+'.2.0/26',
                   'apim': prefix+'.2.64/26', 'management': prefix+'.2.128/26', 'aca': prefix+'.2.192/26'}
        sets = {k: [prefix+'.1.'+str(i), prefix+'.1.'+str(i+1)] for k, i in
                (('foundry', 4), ('keyVault', 6), ('registry', 8), ('sql', 10),
                 ('auditStorageJapan', 12), ('auditStorageFoundry', 14))}
        if not multiple: sets = {k: v[:1] for k, v in sets.items()}
        ips = {'apim': prefix+'.2.68', 'aca': prefix+'.2.196', 'management': prefix+'.2.132',
               'firewallPublic': '192.0.2.10', 'appGatewayPublic': '198.51.100.10'}
        return {'config': {'subnetPrefixes': subnets}, 'resourcePrefix': 'test-fixture', 'subnetPrefixes': subnets,
                'foundryPrivateIps': sets['foundry'], 'keyVaultPrivateIps': sets['keyVault'],
                'auditStorageJapanPrivateIps': sets['auditStorageJapan'],
                'auditStorageFoundryPrivateIps': sets['auditStorageFoundry'],
                'managementPrivateIp': ips['management'], 'firewallPublicIpAddress': ips['firewallPublic'],
                'appGatewayPublicIpAddress': ips['appGatewayPublic'], 'apimPrivateIp': ips['apim'],
                'staticIp': ips['aca'], 'acaPrivateIp': ips['aca'], 'acaPrefix': subnets['aca'],
                'apimPrefix': subnets['apim'], 'agwPrefix': subnets['agw'], 'managementPrefix': subnets['management'],
                'existingNetworkSecurityGroupNames': [], 'enableCredentialBroker': False,
                '__ips': ips, '__sets': sets}

    def resolve(self, module, value, parameters):
        p = copy.deepcopy(parameters)
        if module in ('portal-network', 'credential-broker-network'):
            p['referencePrivateIps'] = p['__sets']['registry' if module == 'portal-network' else 'sql']
        return FixedExpression('[true()]', self.templates[module]).resolve(value, p)

    def compiled_rules(self, parameters):
        result = {g: {} for g in GROUPS}
        for module, variable in (('network', 'staticNsgRules'), ('network-bindings', 'dynamicRules'),
                                 ('network-bindings', 'terminalRules')):
            for e in self.resolve(module, "[variables('"+variable+"')]", parameters):
                g = GROUPS[e['nsgIndex']]; r = e['rule']
                self.assertNotIn(r['name'], result[g]); result[g][r['name']] = r['properties']
        additional = (
            ('portal-network', 'acaRegistryIn', 'pep', 'allow-aca-acr-https-ingress'),
            ('portal-network', 'managementRegistryIn', 'pep', 'allow-mgmt-vm-acr-https-ingress'),
            ('portal-network', 'runnerRegistryAccess', 'management', 'allow-acr-https-egress'),
            ('portal-network', 'acaRegistryOut', 'aca', 'allow-acr-https-egress'),
            ('portal-network-bindings', 'appgwPortalOut', 'agw', 'allow-aca-http-egress'),
            ('portal-network-bindings', 'managementPortalHttpOut', 'management', 'allow-aca-http-egress'),
            ('portal-network-bindings', 'managementPortalHttpsOut', 'management', 'allow-aca-https-egress'),
            ('credential-broker-network', 'sqlIn', 'pep', 'allow-aca-sql-1433-ingress'),
            ('credential-broker-network', 'managementSqlIn', 'pep', 'allow-mgmt-vm-sql-1433-ingress'),
            ('credential-broker-network', 'brokerOut', 'apim', 'allow-aca-https-egress'),
            ('credential-broker-network', 'acaSqlOut', 'aca', 'allow-sql-1433-egress'),
            ('credential-broker-network', 'managementSqlOut', 'management', 'allow-sql-1433-egress'))
        for m, symbol, g, name in additional:
            item = self.templates[m]['resources'][symbol]
            self.assertNotIn('condition', item, (m, symbol)); self.assertNotIn(name, result[g])
            result[g][name] = self.resolve(m, item['properties'], parameters)
        return result

    def bind(self, tokens, p):
        result = []
        for token in tokens:
            kind, value = token.split(':', 1)
            if kind == 'subnet': result.append(p['subnetPrefixes'][value])
            elif kind == 'ip': result.append(p['__ips'][value]+'/32')
            elif kind == 'ips': result.extend(ip+'/32' for ip in p['__sets'][value])
            elif kind in ('tag', 'literal'): result.append(value)
            else: self.fail('Unknown fixed token: '+token)
        return result

    def test_complete_contract_has_unique_contiguous_bands_and_no_grpc_rules(self):
        self.assertEqual(self.contract['version'], 2)
        self.assertEqual(set(self.contract['groups']), set(GROUPS))
        expected_counts = {'agw': 9, 'apim': 22, 'aca': 15, 'pep': 13, 'management': 17}
        record_ids = set(); terminal = 0
        for g, group in self.contract['groups'].items():
            self.assertEqual(len(group['rules']), expected_counts[g]); identities = set(); bands = {}
            for name, rule in group['rules'].items():
                self.assertNotIn(rule['designRecordId'], record_ids); record_ids.add(rule['designRecordId'])
                identity = rule['direction'], rule['priority']
                self.assertNotIn(identity, identities); identities.add(identity); self.assertNotIn('grpc', name)
                if rule['access'] == 'Deny':
                    terminal += 1; self.assertEqual(rule['priority'], 4096)
                    self.assertEqual(name, 'deny-any-ingress' if rule['direction']=='Inbound' else 'deny-any-egress')
                else:
                    start = rule['priority']//100*100+20; self.assertIn(start, (120, 220, 320))
                    bands.setdefault((rule['direction'], start), []).append(rule['priority'])
            for (_, start), priorities in bands.items():
                self.assertEqual(sorted(priorities), list(range(start, start+len(priorities))))
        self.assertEqual((len(record_ids), terminal), (76, 6))

    def test_all_76_compiled_tuples_match_fixed_manifest_with_single_and_multiple_ips(self):
        for multiple, prefix in ((False, '10.70'), (True, '10.80')):
            p = self.parameters(multiple, prefix); actual = self.compiled_rules(p)
            for g, rules in actual.items():
                manifest = self.contract['groups'][g]['rules']; self.assertEqual(set(rules), set(manifest), g)
                for name, properties in rules.items():
                    r = manifest[name]
                    expected = {**{k: r[k] for k in ('direction', 'priority', 'access', 'protocol', 'sourcePort', 'destinationPorts')},
                                'source': self.bind(r['source'], p), 'destination': self.bind(r['destination'], p)}
                    with self.subTest(group=g, rule=name, multiple=multiple):
                        self.assertEqual(canonical(properties), expected)

    def test_retired_flags_and_external_address_overrides_cannot_change_initial_rules(self):
        p = self.parameters(); expected = self.compiled_rules(p)
        p['config'].update(clientAddressPrefixes=['203.0.113.10/32'], certificatePublicProbeEnabled=False,
                           apimKeyVaultAccessEnabled=False, operatorAddressPrefixes=['203.0.113.20/32'],
                           operatorPorts=['22'], dnsServers=['192.0.2.53'], ntpAddressPrefixes=['192.0.2.123/32'],
                           managementAdditionalSecurityRules=[{'name': 'not-created'}])
        self.assertEqual(self.compiled_rules(p), expected)
        for m in MODULES:
            self.assertFalse({'grpcEnabled', 'grpcPublicPort', 'grpcBackendPort'} & set(self.templates[m]['parameters']))

    def test_defaults_parent_preservation_and_fixed_dns_are_maintained(self):
        p = self.parameters(); network = self.templates['network']; actual = self.compiled_rules(p)
        for g, directions in (('agw', ('Outbound',)), ('aca', ('Inbound', 'Outbound')), ('pep', ('Outbound',))):
            self.assertFalse(any(r['access']=='Deny' and r['direction'] in directions for r in actual[g].values()))
        self.assertNotIn('allow-internet-egress', actual['aca']); self.assertNotIn('allow-storage-egress', actual['aca'])
        names = self.resolve('network', "[variables('nsgResourceNames')]", p)
        self.assertEqual(names, ['nsg-test-fixture-'+self.contract['groups'][g]['suffix'] for g in GROUPS])
        p['existingNetworkSecurityGroupNames'] = names
        self.assertEqual(self.resolve('network', "[variables('newNsgResourceNames')]", p), [])
        self.assertEqual(network['resources']['createdNsgs']['properties'], {})
        self.assertEqual(network['resources']['nsgTagUpdates']['type'], 'Microsoft.Resources/tags')
        p['config'].update(dnsServers=['192.0.2.53'], firewall={'dnsServers': ['192.0.2.54']})
        self.assertEqual(self.resolve('network', network['resources']['vnet']['properties']['dhcpOptions'], p), {'dnsServers': []})
        firewall = self.resolve('network', network['resources']['firewallPolicy']['properties'], p)
        self.assertEqual(firewall['dnsSettings'], {'enableProxy': True, 'servers': []})
        for m in MODULES:
            self.assertFalse(any(r['type'].endswith('/defaultSecurityRules') for r in self.templates[m]['resources'].values()))


if __name__ == '__main__': unittest.main()
