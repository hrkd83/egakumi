"""共有SQLの作成とPE後の固定接続policyが別の配備単位であることを確認する。"""
import unittest

from arm_helpers import ArmExpression
from bicep_helpers import compile_bicep


def resources(template):
    values = template['resources']
    return list(values.values()) if isinstance(values, dict) else values


class SharedSqlConnectionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = compile_bicep('infra/modules/shared-sql.bicep')
        cls.policy = compile_bicep('infra/modules/shared-sql-connection-policy.bicep')

    def test_server_creation_does_not_set_an_early_connection_policy(self):
        deployed = [resource for resource in resources(self.server) if not resource.get('existing')]
        self.assertEqual([resource['type'] for resource in deployed], ['Microsoft.Sql/servers'])
        self.assertEqual(deployed[0]['properties']['publicNetworkAccess'], 'Disabled')
        self.assertTrue(deployed[0]['properties']['administrators']['azureADOnlyAuthentication'])
        self.assertEqual(ArmExpression(self.server['outputs']['name']['value']).evaluate({'stem': 'test-fixture'}, 0),
                         'sql-test-fixture')

    def test_policy_module_changes_only_the_existing_server_child_to_fixed_redirect(self):
        deployed = [resource for resource in resources(self.policy) if not resource.get('existing')]
        self.assertEqual(len(deployed), 1)
        policy = deployed[0]
        self.assertEqual(policy['type'], 'Microsoft.Sql/servers/connectionPolicies')
        self.assertEqual(policy['properties'], {'connectionType': 'Redirect'})
        self.assertEqual(set(self.policy['parameters']), {'sqlServerName'})

    def test_policy_targets_the_explicit_constructed_server_without_creating_another_server(self):
        policy, = [resource for resource in resources(self.policy) if not resource.get('existing')]
        for server_name in ('sql-test-fixture', 'sql-prod-example'):
            with self.subTest(server_name=server_name):
                self.assertEqual(ArmExpression(policy['name']).evaluate({'sqlServerName': server_name}, 0),
                                 server_name + '/default')


if __name__ == '__main__':
    unittest.main()
