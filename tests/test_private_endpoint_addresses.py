"""PE 完了後に実 NIC の IP を読む依存順を、コンパイル済み ARM で検査する。

Azure へは接続しない。NIC 名を静的に参照したことで PE 作成前に output の
読み取りが走る不具合を防ぐため、PE → 読み取り module → 最終 output を照合する。
"""
from bicep_helpers import compile_bicep

from pathlib import Path
import unittest

INFRA = Path(__file__).resolve().parents[1] / "infra"


class PrivateEndpointAddressTests(unittest.TestCase):
    """NIC/IP の推測や単一 IP の決めつけを避けた ARM 構造を確認する。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/modules/services-private-endpoints.bicep')

    def readers(self):
        """新規設備ではなく、PE の後段に置いた読み取り deployment を返す。"""
        return {item["name"]: item for item in self.template["resources"]
                if item["type"] == "Microsoft.Resources/deployments"}

    def test_readers_depend_on_created_endpoint_and_take_actual_nic_ids(self):
        readers = self.readers()
        self.assertEqual(set(readers), {"foundry-private-endpoint-addresses", "vault-private-endpoint-addresses"})
        for name, suffix in (("foundry-private-endpoint-addresses", "foundry"),
                             ("vault-private-endpoint-addresses", "kv")):
            with self.subTest(endpoint=suffix):
                reader = readers[name]
                endpoint = "resourceId('Microsoft.Network/privateEndpoints', format('pe-{0}-" + suffix + "', parameters('resourcePrefix')))"
                self.assertEqual(reader["dependsOn"], ["[" + endpoint + "]"])
                value = reader["properties"]["parameters"]["privateEndpointNicIds"]["value"]
                self.assertIn("reference(" + endpoint + ", '2024-05-01').networkInterfaces", value)
                self.assertIn("lambdaVariables('nic').id", value)
                self.assertNotIn("-nic", value)

    def test_reader_only_reads_every_nic_and_every_ip_configuration(self):
        for reader in self.readers().values():
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

    def test_public_outputs_use_completed_readers_and_keep_existing_contract(self):
        outputs = self.template["outputs"]
        self.assertEqual(set(outputs), {"privateEndpointIds", "privateEndpointNicIds", "foundryPrivateIps", "keyVaultPrivateIps"})
        for output_name, reader_name in (("foundryPrivateIps", "foundry-private-endpoint-addresses"),
                                         ("keyVaultPrivateIps", "vault-private-endpoint-addresses")):
            value = outputs[output_name]["value"]
            self.assertIn("union(createArray(), flatten(", value)
            self.assertIn("resourceId('Microsoft.Resources/deployments', '" + reader_name + "')", value)
            self.assertIn(".outputs.privateIpsByNic.value", value)
            self.assertNotIn("Microsoft.Network/networkInterfaces", value)


if __name__ == "__main__":
    unittest.main()
