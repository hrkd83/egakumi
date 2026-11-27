"""構築済み保守メタデータの欠落・取り違えを、Azure要求前に検出する。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from deployment.helpers.management_maintenance import management_maintenance_inputs
from management_fixtures import maintenance_metadata


class ManagementMaintenanceInputTests(unittest.TestCase):
    subscription = '11111111-1111-4111-8111-111111111111'

    def test_only_four_constructed_roles_are_copied_without_mutating_state(self):
        metadata = maintenance_metadata(self.subscription)
        metadata['unrelated'] = 'synthetic-unused-metadata'
        original = copy.deepcopy(metadata)
        values = management_maintenance_inputs({'subscriptionId': self.subscription}, metadata)
        self.assertEqual(values, {'roleDefinitionIds': metadata['roleDefinitionIds']})
        values['roleDefinitionIds'].clear()
        self.assertEqual(metadata, original)

    def test_missing_role_metadata_has_no_fallback(self):
        for value in (None, {}, [], {'roleDefinitionIds': None}):
            with self.subTest(metadata=value), self.assertRaisesRegex(ValueError, 'Core management maintenance'):
                management_maintenance_inputs({'subscriptionId': self.subscription}, value)

    def test_each_role_requires_its_constructed_definition_in_the_selected_subscription(self):
        for key in ('appLogs', 'appExec', 'environmentLogs', 'jobLogs'):
            for bad in (None, '', '/another/role',
                        '/subscriptions/22222222-2222-4222-8222-222222222222/providers/Microsoft.Authorization/roleDefinitions/33333333-3333-4333-8333-333333333333',
                        '/subscriptions/' + self.subscription + '/resourceGroups/rg-fixture/providers/Microsoft.Authorization/roleDefinitions/33333333-3333-4333-8333-333333333333',
                        '/subscriptions/' + self.subscription + '/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000000'):
                metadata = maintenance_metadata(self.subscription)
                metadata['roleDefinitionIds'][key] = bad
                with self.subTest(role=key, invalid=bad), self.assertRaisesRegex(ValueError, 'role output'):
                    management_maintenance_inputs({'subscriptionId': self.subscription}, metadata)

    def test_log_and_exec_roles_cannot_be_replaced_by_one_definition(self):
        metadata = maintenance_metadata(self.subscription)
        metadata['roleDefinitionIds']['appExec'] = metadata['roleDefinitionIds']['appLogs']
        with self.assertRaisesRegex(ValueError, 'distinct approved definitions'):
            management_maintenance_inputs({'subscriptionId': self.subscription}, metadata)

if __name__ == '__main__':
    unittest.main()
