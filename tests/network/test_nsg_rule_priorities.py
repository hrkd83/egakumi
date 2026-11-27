"""Compile the fixed NSG rules with synthetic addresses; no Azure calls."""
import copy
import json
from ipaddress import ip_address, ip_network
import unittest

from tests.support.bicep_helpers import compile_bicep
from tests.network.test_parameter_ip_ownership import NetworkArmExpression
from tests import REPOSITORY

ROOT = REPOSITORY
GROUPS = ('agw', 'apim', 'pep', 'management', 'aca', 'broker')
MODULES = ('network', 'network-bindings', 'portal-network', 'portal-network-bindings', 'credential-broker-network')


class FixedExpression(NetworkArmExpression):
    """Evaluate compiled UDFs with synthetic NIC and public-cloud inputs."""
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
        if tree[0] == 'call' and tree[1] == 'environment':
            return {'suffixes': {'storage': 'core.windows.net'},
                    'authentication': {'loginEndpoint': 'https://' + parameters['entraLoginHost'] + '/'}}
        if tree[0] == 'call' and tree[1] == 'split':
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            return values[0].split(values[1])
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
        cls.contract = json.loads((ROOT / 'infra/modules/network/nsg/core-rules.json').read_text())
        cls.templates = {name: compile_bicep(f'infra/modules/network/{name}.bicep') for name in MODULES}

    def parameters(self, multiple=True, prefix='10.70'):
        subnets = {'agw': prefix+'.0.0/24', 'pep': prefix+'.1.0/24', 'firewall': prefix+'.2.0/26',
                   'apim': prefix+'.2.64/26', 'management': prefix+'.2.128/26', 'aca': prefix+'.2.192/26',
                   'broker': prefix+'.3.0/26'}
        sets = {k: [prefix+'.1.'+str(i), prefix+'.1.'+str(i+1)] for k, i in
                (('foundry', 4), ('keyVault', 6), ('registry', 8), ('sql', 10),
                 ('auditStorageJapan', 12), ('auditStorageFoundry', 14))}
        if not multiple: sets = {k: v[:1] for k, v in sets.items()}
        ips = {'apim': prefix+'.2.68', 'aca': prefix+'.2.196', 'management': prefix+'.2.132', 'broker': prefix+'.3.4',
               'firewallPublic': '192.0.2.10', 'appGatewayPublic': '198.51.100.10'}
        return {'config': {'subnetPrefixes': subnets}, 'resourcePrefix': 'test-fixture', 'subnetPrefixes': subnets,
                'foundryPrivateIps': sets['foundry'], 'keyVaultPrivateIps': sets['keyVault'],
                'auditStorageJapanPrivateIps': sets['auditStorageJapan'],
                'auditStorageFoundryPrivateIps': sets['auditStorageFoundry'],
                'managementPrivateIp': ips['management'], 'firewallPublicIpAddress': ips['firewallPublic'],
                'appGatewayPublicIpAddress': ips['appGatewayPublic'], 'apimPrivateIp': ips['apim'],
                'staticIp': ips['aca'], 'acaPrefix': subnets['aca'],
                'brokerPrivateIp': ips['broker'], 'brokerPrefix': subnets['broker'],
                'apimPrefix': subnets['apim'], 'agwPrefix': subnets['agw'], 'managementPrefix': subnets['management'],
                'entraLoginHost': 'login.microsoftonline.com',
                'firewallTlsInspection': {'intermediateCaSecretId': 'synthetic-ca-reference'},
                'existingNetworkSecurityGroupNames': [], 'auditEvidenceEnabled': False,
                '__ips': ips, '__sets': sets}

    def resolve(self, module, value, parameters):
        p = copy.deepcopy(parameters)
        if module in ('portal-network', 'credential-broker-network'):
            p['referencePrivateIps'] = p['__sets']['registry' if module == 'portal-network' else 'sql']
        return FixedExpression('[true()]', self.templates[module]).resolve(value, p)

    def test_firewall_ingress_inspects_only_the_app_gateway_backend_hosts(self):
        parameters = self.parameters()
        hosts = {
            'allow-appgw-apim-https': 'gateway-backend.example.com',
            'allow-appgw-portal-https': 'portal-backend.example.com',
        }
        parameters.update(apimGatewayFqdn=hosts['allow-appgw-apim-https'],
                          portalBackendHost=hosts['allow-appgw-portal-https'])
        group = self.templates['portal-network-bindings']['resources']['ingressRules']
        collection, = group['properties']['ruleCollections']
        rules = self.resolve('portal-network-bindings', collection['rules'], parameters)
        self.assertEqual({rule['name']: rule['targetFqdns'] for rule in rules},
                         {name: [host] for name, host in hosts.items()})
        for rule in rules:
            self.assertEqual(rule['ruleType'], 'ApplicationRule')
            self.assertTrue(rule['terminateTLS'])
            self.assertEqual(rule['protocols'], [{'protocolType': 'Https', 'port': 443}])
            self.assertEqual(rule['sourceAddresses'], [parameters['agwPrefix']])

    def test_aca_firewall_monitor_and_entra_cover_both_aca_subnets(self):
        resource = self.templates['portal-network']['resources']['rules']
        collection, = [c for c in resource['properties']['ruleCollections']
                       if c['name'] == 'portal-azure-monitor']
        for prefix in ('10.40', '10.70'):
            with self.subTest(prefix=prefix):
                parameters = self.parameters(prefix=prefix)
                actual = self.resolve('portal-network', collection, parameters)
                self.assertEqual(resource['properties']['priority'], 350)
                self.assertEqual(actual['priority'], 150)
                self.assertEqual(actual['action'], {'type': 'Allow'})
                self.assertEqual(actual['ruleCollectionType'], 'FirewallPolicyFilterRuleCollection')
                self.assertEqual(len(actual['rules']), 2)
                self.assertEqual({r['name'] for r in actual['rules']},
                                 {'allow-aca-monitor', 'allow-aca-entra'})
                observed = set()
                for rule in actual['rules']:
                    self.assertEqual(rule['ruleType'], 'NetworkRule')
                    self.assertNotIn('destinationFqdns', rule)
                    observed.update((source, destination, protocol, port)
                                    for source in rule['sourceAddresses']
                                    for destination in rule['destinationAddresses']
                                    for protocol in rule['ipProtocols']
                                    for port in rule['destinationPorts'])
                expected = {(source, tag, 'TCP', '443') for source in (parameters['acaPrefix'], parameters['brokerPrefix'])
                            for tag in ('AzureMonitor', 'AzureActiveDirectory')}
                self.assertEqual(observed, expected)

    def test_management_aca_dev_firewall_allow_is_constant_and_region_scoped(self):
        resource = self.templates['network']['resources']['egressRules']
        self.assertNotIn('condition', resource)
        self.assertEqual(resource['properties']['priority'], 400)
        collection, = [c for c in resource['properties']['ruleCollections']
                       if c['name'] == 'allow-vm-egress']
        self.assertEqual(collection['priority'], 210)
        self.assertEqual(collection['action'], {'type': 'Allow'})
        rule, = [r for r in collection['rules'] if r['name'] == 'allow-mgmt-vm-aca-dev-https']
        self.assertNotIn('condition', rule)
        for location, host in (('japaneast', 'japaneast.azurecontainerapps.dev'),
                               ('Japan East', 'japaneast.azurecontainerapps.dev'),
                               ('EAST US 2', 'eastus2.azurecontainerapps.dev')):
            for prefix in ('10.40', '10.70'):
                with self.subTest(location=location, prefix=prefix):
                    parameters = {**self.parameters(prefix=prefix), 'location': location}
                    actual = self.resolve('network', rule, parameters)
                    self.assertEqual(actual, {
                        'name': 'allow-mgmt-vm-aca-dev-https',
                        'ruleType': 'ApplicationRule',
                        'sourceAddresses': [parameters['subnetPrefixes']['management']],
                        'protocols': [{'protocolType': 'Https', 'port': 443}],
                        'targetFqdns': [host], 'terminateTLS': False})

    def _application_rule_tuples(self, rules, purposes, sources):
        self.assertEqual(len(rules), len(purposes))
        self.assertEqual({r['name'] for r in rules}, set(purposes))
        observed = set()
        for rule in rules:
            self.assertNotIn('condition', rule)
            self.assertEqual(rule['ruleType'], 'ApplicationRule')
            self.assertEqual(rule['sourceAddresses'], sources[rule['name']])
            self.assertEqual(rule['protocols'], [{'protocolType': 'Https', 'port': 443}])
            self.assertIs(rule['terminateTLS'], False)
            self.assertEqual(rule['targetFqdns'], purposes[rule['name']])
            observed.update((address, destination, protocol['protocolType'], protocol['port'],
                             rule['terminateTLS'])
                            for address in rule['sourceAddresses']
                            for destination in rule['targetFqdns']
                            for protocol in rule['protocols'])
        return observed

    def test_aca_firewall_platform_rules_cover_only_required_hosts_in_both_subnets(self):
        resource = self.templates['portal-network']['resources']['rules']
        self.assertNotIn('condition', resource)
        self.assertEqual(resource['properties']['priority'], 350)
        collection, = [c for c in resource['properties']['ruleCollections']
                       if c['name'] == 'portal-platform-and-probe']
        for prefix in ('10.40', '10.70'):
            with self.subTest(prefix=prefix):
                parameters = self.parameters(prefix=prefix)
                actual = self.resolve('portal-network', collection, parameters)
                self.assertEqual(actual['priority'], 200)
                self.assertEqual(actual['action'], {'type': 'Allow'})
                self.assertEqual(actual['ruleCollectionType'], 'FirewallPolicyFilterRuleCollection')
                login_host = parameters['entraLoginHost']
                purposes = {
                    'allow-aca-mcr': ['mcr.microsoft.com', '*.data.mcr.microsoft.com'],
                    'allow-aca-platform-packages': ['packages.aks.azure.com', 'acs-mirror.azureedge.net'],
                    'allow-aca-managed-identity': [
                        '*.identity.azure.net', login_host, '*.' + login_host,
                        '*.login.microsoft.com', 'login.microsoft.com'],
                }
                sources = {name: [parameters['acaPrefix'], parameters['brokerPrefix']] for name in purposes}
                self._application_rule_tuples(actual['rules'], purposes, sources)

    def test_aca_firewall_arm_allows_only_broker(self):
        resource = self.templates['credential-broker-network']['resources']['rules']
        self.assertNotIn('condition', resource)
        self.assertEqual(resource['properties']['priority'], 360)
        collection, = [c for c in resource['properties']['ruleCollections']
                       if c['name'] == 'credential-broker-azure']
        for prefix in ('10.40', '10.70'):
            with self.subTest(prefix=prefix):
                parameters = self.parameters(prefix=prefix)
                actual = self.resolve('credential-broker-network', collection, parameters)
                self.assertEqual(actual['priority'], 200)
                self.assertEqual(actual['action'], {'type': 'Allow'})
                self.assertEqual(actual['ruleCollectionType'], 'FirewallPolicyFilterRuleCollection')
                purposes = {
                    'allow-aca-entra-login': ['login.microsoftonline.com'],
                    'allow-broker-arm': ['management.azure.com'],
                }
                self.assertEqual(len(actual['rules']), 3)
                self.assertEqual({r['name'] for r in actual['rules']},
                                 set(purposes) | {'allow-mgmt-vm-python-build'})
                owned_rules = [r for r in actual['rules'] if r['name'] in purposes]
                self._application_rule_tuples(owned_rules, purposes, {
                    'allow-aca-entra-login': [parameters['acaPrefix'], parameters['brokerPrefix']],
                    'allow-broker-arm': [parameters['brokerPrefix']],
                })
                nsgs = self.compiled_rules(parameters)
                broker_arm = canonical(nsgs['broker']['allow-arm-https-egress'])
                self.assertEqual(broker_arm['source'], [parameters['brokerPrefix']])
                self.assertEqual(broker_arm['destination'], ['AzureResourceManager'])
                self.assertFalse(any('AzureResourceManager' in canonical(rule)['destination']
                                     for rule in nsgs['aca'].values()))

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
            ('portal-network', 'brokerRegistryOut', 'broker', 'allow-acr-https-egress'),
            ('portal-network-bindings', 'appgwPortalOut', 'agw', 'allow-aca-https-egress'),
            ('portal-network-bindings', 'managementPortalHttpsOut', 'management', 'allow-aca-https-egress'),
            ('credential-broker-network', 'sqlIn', 'pep', 'allow-aca-sql-1433-ingress'),
            ('credential-broker-network', 'managementSqlIn', 'pep', 'allow-mgmt-vm-sql-1433-ingress'),
            ('credential-broker-network', 'brokerOut', 'apim', 'allow-broker-https-egress'),
            ('credential-broker-network', 'acaSqlOut', 'aca', 'allow-sql-1433-egress'),
            ('credential-broker-network', 'brokerSqlOut', 'broker', 'allow-sql-1433-egress'),
            ('credential-broker-network', 'managementSqlOut', 'management', 'allow-sql-1433-egress'),
            ('credential-broker-network', 'acaIngressDeny', 'aca', 'deny-any-ingress'),
            ('credential-broker-network', 'acaEgressDeny', 'aca', 'deny-any-egress'),
            ('credential-broker-network', 'brokerIngressDeny', 'broker', 'deny-any-ingress'),
            ('credential-broker-network', 'brokerEgressDeny', 'broker', 'deny-any-egress'))
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

    def test_contract_has_unique_priorities_and_expected_terminal_denials(self):
        self.assertEqual(set(self.contract['groups']), set(GROUPS))
        design_ids = set()
        for group, definition in self.contract['groups'].items():
            priorities = set()
            terminal_directions = set()
            for name, rule in definition['rules'].items():
                self.assertNotIn(rule['designRecordId'], design_ids)
                design_ids.add(rule['designRecordId'])
                identity = rule['direction'], rule['priority']
                self.assertNotIn(identity, priorities)
                priorities.add(identity)
                if rule['access'] == 'Deny':
                    terminal_directions.add(rule['direction'])
                    self.assertEqual(rule['priority'], 4096)
                    suffix = 'ingress' if rule['direction'] == 'Inbound' else 'egress'
                    self.assertEqual(name, 'deny-any-' + suffix)
                else:
                    self.assertLess(rule['priority'], 4096)
            expected = {'Inbound'} if group in ('agw', 'pep') else {'Inbound', 'Outbound'}
            self.assertEqual(terminal_directions, expected, group)

    def test_compiled_tuples_match_fixed_manifest_with_single_and_multiple_ips(self):
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

    def test_broker_listener_and_vnet_accept_only_apim_and_aca_platform_paths(self):
        parameters = self.parameters()
        rules = self.compiled_rules(parameters)
        subnet = parameters['subnetPrefixes']
        broker = [canonical(rule) for rule in rules['broker'].values()]
        inbound = {(tuple(rule['source']), tuple(rule['destinationPorts'])) for rule in broker
                   if rule['direction'] == 'Inbound' and rule['access'] == 'Allow'}
        self.assertEqual(inbound, {
            ((subnet['apim'],), ('443', '31443')),
            (('AzureLoadBalancer',), ('30000-32767',)),
            ((subnet['broker'],), ('*',)),
        })
        self.assertEqual(canonical(rules['apim']['allow-broker-https-egress'])['destination'],
                         [parameters['__ips']['broker'] + '/32'])
        portal_ingress = [canonical(rule) for rule in rules['aca'].values() if rule['direction'] == 'Inbound']
        self.assertFalse(any(subnet['apim'] in rule['source'] for rule in portal_ingress))
        self.assertTrue(all(subnet['aca'] not in rule['destination'] for rule in broker if rule['direction'] == 'Outbound'))

    def test_retired_flags_and_external_address_overrides_cannot_change_initial_rules(self):
        p = self.parameters(); expected = self.compiled_rules(p)
        p['config'].update(clientAddressPrefixes=['203.0.113.10/32'], certificatePublicProbeEnabled=False,
                           apimKeyVaultAccessEnabled=False, operatorAddressPrefixes=['203.0.113.20/32'],
                           operatorPorts=['22'], dnsServers=['192.0.2.53'], ntpAddressPrefixes=['192.0.2.123/32'],
                           managementAdditionalSecurityRules=[{'name': 'not-created'}])
        self.assertEqual(self.compiled_rules(p), expected)
        for m in MODULES:
            self.assertFalse({'grpcEnabled', 'grpcPublicPort', 'grpcBackendPort'} & set(self.templates[m]['parameters']))

    def test_management_host_phc_has_no_network_time_allow_and_apim_keeps_internet_ntp(self):
        p = self.parameters(); actual = self.compiled_rules(p)
        management = actual['management']
        self.assertNotIn('allow-time-sync-egress', management)
        for name, properties in management.items():
            rule = canonical(properties)
            if rule['direction'] == 'Outbound' and rule['access'] == 'Allow':
                self.assertEqual(rule['protocol'], 'Tcp', name)
        deny = canonical(management['deny-any-egress'])
        self.assertEqual({key: deny[key] for key in ('priority', 'direction', 'access', 'protocol',
                                                   'source', 'destination', 'destinationPorts')}, {
            'priority': 4096, 'direction': 'Outbound', 'access': 'Deny', 'protocol': '*',
            'source': ['*'], 'destination': ['*'], 'destinationPorts': ['*'],
        })
        ntp = canonical(actual['apim']['allow-time-sync-egress'])
        self.assertEqual({key: ntp[key] for key in ('priority', 'direction', 'access', 'protocol',
                                                  'source', 'destination', 'destinationPorts')}, {
            'priority': 328, 'direction': 'Outbound', 'access': 'Allow', 'protocol': 'Udp',
            'source': [p['subnetPrefixes']['apim']], 'destination': ['Internet'], 'destinationPorts': ['123'],
        })

    def test_aca_allows_runtime_dependencies_and_denies_other_flows(self):
        parameters = self.parameters()
        rules = sorted((canonical(rule) for rule in self.compiled_rules(parameters)['aca'].values()),
                       key=lambda rule: rule['priority'])
        public_ip = '203.0.113.10'
        load_balancer_ip = '192.0.2.10'
        service_tags = {'Internet': {public_ip}, 'AzureLoadBalancer': {load_balancer_ip}}
        aca = parameters['__ips']['aca']
        other_replica = str(ip_address(aca) + 1)
        firewall = str(ip_network(parameters['subnetPrefixes']['firewall'])[4])
        other_subnet = str(ip_network(parameters['subnetPrefixes']['agw'])[4])

        def matches_address(address, prefixes):
            for prefix in prefixes:
                if prefix == '*' or address in service_tags.get(prefix, set()):
                    return True
                if '/' in prefix and ip_address(address) in ip_network(prefix):
                    return True
            return False

        def matches_port(port, ranges):
            for limits in ranges:
                if limits == '*':
                    return True
                first, _, last = limits.partition('-')
                if int(first) <= port <= int(last or first):
                    return True
            return False

        cases = (
            ('Inbound', 'Tcp', firewall, aca, 31443, 'Allow'),
            ('Inbound', 'Tcp', parameters['apimPrivateIp'], aca, 443, 'Deny'),
            ('Inbound', 'Tcp', parameters['managementPrivateIp'], aca, 443, 'Allow'),
            ('Inbound', 'Tcp', load_balancer_ip, aca, 32123, 'Allow'),
            ('Inbound', 'Tcp', load_balancer_ip, aca, 1433, 'Deny'),
            ('Inbound', 'Tcp', other_subnet, aca, 443, 'Deny'),
            ('Inbound', 'Udp', other_replica, aca, 8080, 'Allow'),
            ('Outbound', 'Udp', aca, other_replica, 8080, 'Allow'),
            ('Outbound', 'Tcp', aca, parameters['__sets']['keyVault'][1], 443, 'Allow'),
            ('Outbound', 'Tcp', aca, parameters['__sets']['registry'][1], 443, 'Allow'),
            ('Outbound', 'Tcp', aca, parameters['__sets']['sql'][1], 1433, 'Allow'),
            ('Outbound', 'Tcp', aca, parameters['__sets']['sql'][1], 443, 'Deny'),
            ('Outbound', 'Tcp', aca, other_subnet, 443, 'Deny'),
            ('Outbound', 'Tcp', aca, public_ip, 443, 'Allow'),
            ('Outbound', 'Tcp', aca, public_ip, 80, 'Deny'),
            ('Outbound', 'Udp', aca, public_ip, 443, 'Deny'),
        )
        for direction, protocol, source, destination, port, allowed in cases:
            with self.subTest(direction=direction, protocol=protocol, source=source,
                              destination=destination, port=port):
                matching = next(rule for rule in rules if rule['direction'] == direction
                                and rule['protocol'] in (protocol, '*')
                                and matches_address(source, rule['source'])
                                and matches_address(destination, rule['destination'])
                                and matches_port(port, rule['destinationPorts']))
                self.assertEqual(matching['access'], allowed)

    def test_defaults_parent_preservation_and_fixed_dns_are_maintained(self):
        p = self.parameters(); network = self.templates['network']; actual = self.compiled_rules(p)
        for g, directions in (('agw', ('Outbound',)), ('pep', ('Outbound',))):
            self.assertFalse(any(r['access']=='Deny' and r['direction'] in directions for r in actual[g].values()))
        names = self.resolve('network', "[variables('nsgResourceNames')]", p)
        self.assertEqual(names, ['nsg-test-fixture-'+self.contract['groups'][g]['suffix'] for g in GROUPS])
        p['existingNetworkSecurityGroupNames'] = names
        self.assertEqual(self.resolve('network', "[variables('newNsgResourceNames')]", p), [])
        self.assertEqual(network['resources']['createdNsgs']['properties'], {})
        self.assertEqual(network['resources']['nsgTagUpdates']['type'], 'Microsoft.Resources/tags')
        p['config'].update(dnsServers=['192.0.2.53'], firewall={'dnsServers': ['192.0.2.54']},
                           generatedIdpsPrivateRanges=['192.0.2.0/24'])
        self.assertEqual(self.resolve('network', network['resources']['vnet']['properties']['dhcpOptions'], p), {'dnsServers': []})
        firewall_dns = network['resources']['firewallPolicy']['properties']['dnsSettings']
        self.assertEqual(self.resolve('network', firewall_dns, p), {'enableProxy': True, 'servers': []})
        for m in MODULES:
            self.assertFalse(any(r['type'].endswith('/defaultSecurityRules') for r in self.templates[m]['resources'].values()))


if __name__ == '__main__': unittest.main()
