"""Schema jobs receive constructed VM client IDs; Apps keep runtime identities."""
import unittest

from bicep_helpers import compile_bicep


class SqlManagementGrantInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = compile_bicep('apps/gateway/deploy/broker.bicep')
        cls.gateway = compile_bicep('apps/gateway/deploy/main.bicep')
        cls.portal = compile_bicep('apps/portal/deploy/main.bicep')
        cls.portal_schema = compile_bicep('apps/portal/deploy/schema-job.bicep')

    def test_internal_client_id_is_mandatory_not_a_user_creation_flag(self):
        for template in (self.broker, self.portal, self.portal_schema):
            parameter = template['parameters']['managementSqlClientId']
            self.assertEqual(parameter['type'], 'string')
            self.assertNotIn('defaultValue', parameter)
            self.assertEqual((parameter['minLength'], parameter['maxLength']), (36, 36))

    def test_schema_jobs_receive_client_ids_while_runtime_uses_only_its_own_identity(self):
        schema_environment = self.broker['variables']['schemaEnvironment']
        self.assertIn("'SQL_MANAGEMENT_CLIENT_ID', parameters('managementSqlClientId')", schema_environment)
        self.assertNotIn('SQL_MANAGEMENT_CLIENT_ID', self.broker['variables']['runtimeEnvironment'])
        portal_variables = self.portal_schema['variables']['variables']
        self.assertEqual(portal_variables['PORTAL_SQL_MANAGEMENT_CLIENT_ID'], "[parameters('managementSqlClientId')]")
        self.assertEqual(portal_variables['AZURE_CLIENT_ID'], "[parameters('schemaIdentity').clientId]")
        self.assertEqual(portal_variables['PORTAL_SQL_RUNTIME_CLIENT_ID'], "[parameters('runtimeIdentity').clientId]")
        broker_job = next(resource for resource in self.broker['resources']
            if resource['type'] == 'Microsoft.App/jobs' and resource['properties']['configuration']['triggerType'] == 'Manual')
        self.assertEqual(broker_job['properties']['template']['containers'][0]['args'], ["[parameters('schemaAction')]"])

    def test_app_entrypoints_handoff_only_constructed_metadata_to_schema_modules(self):
        broker_module = next(resource for resource in self.gateway['resources']
            if 'managementSqlClientId' in resource.get('properties', {}).get('parameters', {}))
        self.assertEqual(broker_module['properties']['parameters']['managementSqlClientId']['value'],
            "[parameters('foundation').managementMaintenance.sqlClientId]")
        schema_module = next(resource for resource in self.portal['resources']
            if 'managementSqlClientId' in resource.get('properties', {}).get('parameters', {}))
        self.assertEqual(schema_module['properties']['parameters']['managementSqlClientId']['value'],
            "[parameters('managementSqlClientId')]")

    def test_each_app_exports_only_its_constructed_database_for_maintenance_inventory(self):
        for template in (self.gateway, self.portal):
            values = template['outputs']['managementMaintenance']['value']['sqlDatabaseIds']
            self.assertEqual(len(values), 1)
            self.assertIn('outputs.databaseId.value', values[0])


if __name__ == '__main__':
    unittest.main()
