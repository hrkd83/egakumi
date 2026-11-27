"""Verify audit Blob PE/DNS staging with independently synthesized ARM data."""
import unittest

from bicep_helpers import compile_bicep
from test_parameter_ip_ownership import NetworkArmExpression


class AuditExpression(NetworkArmExpression):
    def resolve(self, value, parameters):
        if isinstance(value, dict): return {key: self.resolve(item, parameters) for key, item in value.items()}
        if isinstance(value, list): return [self.resolve(item, parameters) for item in value]
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            return AuditExpression(value, self.template, self.cache).evaluate(parameters, 0)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] == 'reference':
            identity = self.evaluate(parameters, index, tree[2][0], bindings)
            return parameters['references'][identity]
        if tree[0] == 'call' and tree[1] in ('split', 'last'):
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            return values[0].split(values[1]) if tree[1] == 'split' else values[0][-1]
        return super().evaluate(parameters, index, tree, bindings)


class AuditStorageNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pe = compile_bicep('infra/modules/monitoring/audit-storage-private-endpoints.bicep')
        cls.dns = compile_bicep('infra/modules/monitoring/audit-storage-private-dns.bicep')
        cls.records = compile_bicep('infra/modules/monitoring/audit-storage-private-dns-records.bicep')
        cls.link = compile_bicep('infra/modules/monitoring/audit-storage-private-dns-link.bicep')

    def parameters(self):
        return {'resourcePrefix': 'fixture-resources', 'location': 'japaneast', 'tags': {},
                'pepSubnetId': '/synthetic/network/subnets/private-endpoints',
                'auditStorage': {
                    'collectJapan': {'id': '/synthetic/monitoring/storageAccounts/auditlocal', 'name': 'auditlocal'},
                    'collectFoundry': {'id': '/synthetic/monitoring/storageAccounts/auditremote', 'name': 'auditremote'}}}

    def test_endpoint_outputs_preserve_all_nics_and_deduplicate_only_repeated_ips(self):
        parameters = self.parameters()
        parameters['references'] = {}
        expected = {}
        for key, suffix, ips in (('collectJapan', 'audit-japan-blob', ['10.70.1.12', '10.70.1.13', '10.70.1.14']),
                                 ('collectFoundry', 'audit-foundry-blob', ['10.70.1.15', '10.70.1.16', '10.70.1.17'])):
            name = 'pe-' + parameters['resourcePrefix'] + '-' + suffix
            endpoint_id = '/mock/Microsoft.Network/privateEndpoints/' + name
            # ARM assigned these opaque NIC names; the module must use the IDs.
            nic_ids = ['/synthetic/nics/' + suffix + '-first', '/synthetic/nics/' + suffix + '-second']
            parameters['references'][endpoint_id] = {'networkInterfaces': [{'id': identity} for identity in nic_ids]}
            deployment_id = '/mock/Microsoft.Resources/deployments/' + parameters['resourcePrefix'] + '-' + suffix + '-addresses'
            parameters['references'][deployment_id] = {'outputs': {
                'privateIpsByNic': {'value': [ips[:2], [ips[1], ips[2]]]}}}
            expected[key] = {'id': endpoint_id, 'storageAccountId': parameters['auditStorage'][key]['id'],
                             'networkInterfaceIds': nic_ids, 'privateIps': ips}
        output = AuditExpression('[true()]', self.pe).resolve(self.pe['outputs']['privateEndpoints']['value'], parameters)
        self.assertEqual(output, expected)
        types = {resource['type'] for resource in self.pe['resources']}
        self.assertEqual(types, {'Microsoft.Network/privateEndpoints', 'Microsoft.Resources/deployments'})
        endpoint = next(resource for resource in self.pe['resources'] if resource['type'] == 'Microsoft.Network/privateEndpoints')
        self.assertEqual(endpoint['properties']['privateLinkServiceConnections'][0]['properties']['groupIds'], ['blob'])

    def test_zone_group_owns_a_records_and_reads_actual_storage_output_names(self):
        parameters = self.parameters()
        parameters['privateEndpoints'] = {
            'collectJapan': {'id': '/synthetic/privateEndpoints/chosen-japan-endpoint'},
            'collectFoundry': {'id': '/synthetic/privateEndpoints/chosen-foundry-endpoint'}}
        parameters['references'] = {
            '/mock/Microsoft.Network/privateDnsZones/A/privatelink.blob.core.windows.net/auditlocal':
                {'aRecords': [{'ipv4Address': '10.70.1.12'}, {'ipv4Address': '10.70.1.13'}]},
            '/mock/Microsoft.Network/privateDnsZones/A/privatelink.blob.core.windows.net/auditremote':
                {'aRecords': [{'ipv4Address': '10.70.1.15'}, {'ipv4Address': '10.70.1.16'}]}}
        parameters['privateDnsZoneId'] = '/synthetic/privateDnsZones/privatelink.blob.core.windows.net'
        actual = AuditExpression('[true()]', self.records).resolve(
            self.records['outputs']['recordPrivateIps']['value'], parameters)
        expected = {'collectJapan': ['10.70.1.12', '10.70.1.13'],
                    'collectFoundry': ['10.70.1.15', '10.70.1.16']}
        self.assertEqual(actual, expected)
        reader = next(resource for resource in self.dns['resources']
                      if resource['type'] == 'Microsoft.Resources/deployments')
        expression = AuditExpression('[true()]', self.dns)
        reader_id = '/mock/Microsoft.Resources/deployments/' + expression.resolve(reader['name'], parameters)
        parameters['references'][reader_id] = {'outputs': {'recordPrivateIps': {'value': actual}}}
        output = expression.resolve(self.dns['outputs']['recordPrivateIps']['value'], parameters)
        self.assertEqual(output, expected)
        zone_groups = [resource for resource in self.dns['resources']
                       if resource['type'] == 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups']
        self.assertEqual([expression.resolve(resource['name'], parameters) for resource in zone_groups],
                         ['chosen-japan-endpoint/default', 'chosen-foundry-endpoint/default'])
        self.assertEqual([resource['type'] for resource in self.dns['resources']],
                         ['Microsoft.Network/privateDnsZones',
                          'Microsoft.Network/privateEndpoints/privateDnsZoneGroups',
                          'Microsoft.Network/privateEndpoints/privateDnsZoneGroups',
                          'Microsoft.Resources/deployments'])

    def test_a_record_reads_wait_for_both_zone_groups_and_never_write_managed_records(self):
        parameters = self.parameters()
        parameters['privateEndpoints'] = {
            'collectJapan': {'id': '/synthetic/privateEndpoints/chosen-japan-endpoint'},
            'collectFoundry': {'id': '/synthetic/privateEndpoints/chosen-foundry-endpoint'}}
        expression = AuditExpression('[true()]', self.dns)
        reader = next(resource for resource in self.dns['resources']
                      if resource['type'] == 'Microsoft.Resources/deployments')
        dependencies = expression.resolve(reader['dependsOn'], parameters)
        for name in ('chosen-japan-endpoint/default', 'chosen-foundry-endpoint/default'):
            self.assertIn('/mock/Microsoft.Network/privateEndpoints/privateDnsZoneGroups/' + name, dependencies)
        self.assertEqual(reader['properties']['expressionEvaluationOptions']['scope'], 'inner')
        self.assertEqual(reader['properties']['template']['resources'], [])
        self.assertEqual(self.records['resources'], [])
        self.assertEqual(set(self.records['parameters']), {'privateDnsZoneId', 'auditStorage'})

    def test_vnet_link_is_a_separate_validated_stage_with_public_nxdomain_fallback(self):
        self.assertEqual(self.link['parameters']['dnsRecordsValidated']['allowedValues'], [True])
        self.assertNotIn('defaultValue', self.link['parameters']['dnsRecordsValidated'])
        self.assertEqual([resource['type'] for resource in self.link['resources']],
                         ['Microsoft.Network/privateDnsZones/virtualNetworkLinks'])
        properties = self.link['resources'][0]['properties']
        self.assertFalse(properties['registrationEnabled'])
        self.assertEqual(properties['resolutionPolicy'], 'NxDomainRedirect')


if __name__ == '__main__': unittest.main()
