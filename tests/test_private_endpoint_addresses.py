"""PE 完了後に実 NIC の IP を読む依存順を、コンパイル済み ARM で検査する。

Azure へは接続しない。NIC 名を静的に参照したことで PE 作成前に output の
読み取りが走る不具合を防ぐため、PE → 読み取り module → 最終 output を照合する。
"""
from bicep_helpers import compile_bicep

import unittest


class PrivateEndpointAddressTests(unittest.TestCase):
    """NIC/IP の推測や単一 IP の決めつけを避けた ARM 構造を確認する。"""

    @classmethod
    def setUpClass(cls):
        core = compile_bicep('infra/main.bicep')
        deployments = {item['name']: item['properties'] for item in core['resources']
                       if item['type'] == 'Microsoft.Resources/deployments'}
        cls.services = deployments["[format('{0}-private-endpoints', variables('stem'))]"]
        cls.certificate = deployments["[format('{0}-certificate-network', variables('stem'))]"]

    @staticmethod
    def readers(resources):
        """新規設備ではなく、PE の後段に置いた読み取り deployment を返す。"""
        return {item["name"]: item for item in resources
                if item["type"] == "Microsoft.Resources/deployments"}

    def test_readers_depend_on_created_endpoint_and_take_actual_nic_ids(self):
        service_readers = self.readers(self.services['template']['resources'])
        for reader_name, suffix in (('foundry-private-endpoint-addresses', 'foundry'),
                                    ('vault-private-endpoint-addresses', 'kv')):
            with self.subTest(reader=reader_name):
                reader = service_readers[reader_name]
                endpoint_id = ("resourceId('Microsoft.Network/privateEndpoints', "
                               f"format('pe-{{0}}-{suffix}', parameters('resourcePrefix')))")
                self.assertEqual(reader["dependsOn"], [f"[{endpoint_id}]"])
                value = reader["properties"]["parameters"]["privateEndpointNicIds"]["value"]
                self.assertIn(f"reference({endpoint_id}, '2024-05-01').networkInterfaces", value)
                self.assertIn("lambdaVariables('nic').id", value)
                self.assertNotIn("-nic", value)
        certificate_resources = self.certificate['template']['resources']
        for reader_name, endpoint_name in (('vaultIps', 'tlsVaultPe'), ('blobIps', 'blobPe')):
            with self.subTest(reader=reader_name):
                reader = certificate_resources[reader_name]
                self.assertEqual(certificate_resources[endpoint_name]['type'], 'Microsoft.Network/privateEndpoints')
                self.assertEqual(reader['dependsOn'], [endpoint_name])
                value = reader['properties']['parameters']['privateEndpointNicIds']['value']
                self.assertEqual(value, f"[map(reference('{endpoint_name}').networkInterfaces, "
                                        "lambda('nic', lambdaVariables('nic').id))]")

    def test_reader_only_reads_every_nic_and_every_ip_configuration(self):
        readers = (self.readers(self.services['template']['resources'])
                   | self.readers(self.certificate['template']['resources'].values()))
        for reader in readers.values():
            child = reader["properties"]["template"]
            self.assertEqual(child["resources"], [])
            self.assertEqual(child["parameters"]["privateEndpointNicIds"]["minLength"], 1)
            output = child["outputs"]["privateIpsByNic"]["copy"]
            self.assertEqual(output["count"], "[length(parameters('privateEndpointNicIds'))]")
            value = output["input"]
            self.assertIn("parameters('privateEndpointNicIds')[copyIndex()]", value)
            self.assertIn("'Microsoft.Network/networkInterfaces'", value)
            self.assertIn("last(split(parameters('privateEndpointNicIds')[copyIndex()], '/'))", value)
            self.assertIn(".ipConfigurations", value)
            self.assertIn("lambdaVariables('config').properties.privateIPAddress", value)

    def test_output_and_tls_network_rules_use_completed_readers(self):
        outputs = self.services['template']['outputs']
        self.assertEqual(set(outputs), {"privateEndpointIds", "privateEndpointNicIds", "foundryPrivateIps", "keyVaultPrivateIps"})
        for output_name, reader_name in (("foundryPrivateIps", "foundry-private-endpoint-addresses"),
                                         ("keyVaultPrivateIps", "vault-private-endpoint-addresses")):
            value = outputs[output_name]["value"]
            self.assertIn("union(createArray(), flatten(", value)
            self.assertIn("resourceId('Microsoft.Resources/deployments', '" + reader_name + "')", value)
            self.assertIn(".outputs.privateIpsByNic.value", value)
            self.assertNotIn("Microsoft.Network/networkInterfaces", value)
        certificate_resources = self.certificate['template']['resources']
        for rule_name in ('sharedRulesOnNsg', 'allow'):
            with self.subTest(rule=rule_name):
                rule = certificate_resources[rule_name]
                self.assertIn('vaultIps', rule['dependsOn'])
                self.assertIn("map(union(createArray(), flatten(reference('vaultIps').outputs.privateIpsByNic.value)), "
                              "lambda('ip', format('{0}/32', lambdaVariables('ip'))))", rule['properties'])

    def test_tls_vault_endpoint_shares_the_private_dns_zone_with_a_distinct_vault_target(self):
        bindings = (
            (self.services['template']['resources'], 'kv', "[parameters('serviceResourceIds').keyVault]",
             "[parameters('privateDnsZoneIds').keyVault]"),
            (self.certificate['template']['resources'].values(), 'tls-vault', "[parameters('tlsVaultId')]",
             "[parameters('vaultPrivateDnsZoneId')]"),
        )
        for resources, suffix, target, zone_id in bindings:
            key = f"format('pe-{{0}}-{suffix}',"
            endpoint, = [resource for resource in resources if resource['type'] == 'Microsoft.Network/privateEndpoints'
                         and key in resource['name']]
            connection, = endpoint['properties']['privateLinkServiceConnections']
            self.assertEqual(connection['properties']['privateLinkServiceId'], target)
            self.assertEqual(connection['properties']['groupIds'], ['vault'])
            zone, = [resource for resource in resources if resource['type'].endswith('/privateDnsZoneGroups')
                     and key in resource['name']]
            self.assertEqual(zone['properties']['privateDnsZoneConfigs'], [
                {'name': 'vault', 'properties': {'privateDnsZoneId': zone_id}}])
        service_dns_zones = self.services['parameters']['privateDnsZoneIds']['value']
        self.assertEqual(self.certificate['parameters']['vaultPrivateDnsZoneId']['value'],
                         service_dns_zones[:-1] + '.keyVault]')
        self.assertIn("format('{0}-certificate-storage', variables('stem'))",
                      self.certificate['parameters']['tlsVaultId']['value'])
        self.assertIn('.outputs.vaultId.value', self.certificate['parameters']['tlsVaultId']['value'])


if __name__ == "__main__":
    unittest.main()
