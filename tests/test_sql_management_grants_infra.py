"""Verify that the constructed databases use dedicated schema and runtime identities."""
import unittest

from bicep_helpers import compile_bicep


class SqlSchemaIdentityInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        cls.gateway = compile_bicep('apps/api-gateway/deploy/main.bicep')
        cls.portal = compile_bicep('apps/portal/deploy/main.bicep')
        cls.portal_schema = compile_bicep('apps/portal/deploy/schema-job.bicep')

    def test_broker_schema_job_grants_the_runtime_principal_using_its_schema_identity(self):
        job = next(resource for resource in self.broker['resources']
                   if resource['type'] == 'Microsoft.App/jobs'
                   and resource['properties']['configuration']['triggerType'] == 'Manual')
        app = next(resource for resource in self.broker['resources']
                   if resource['type'] == 'Microsoft.App/containerApps')
        self.assertEqual(job['identity']['userAssignedIdentities'], {
            "[format('{0}', parameters('schemaIdentity').id)]": {}
        })
        self.assertEqual(app['identity']['userAssignedIdentities'], {
            "[format('{0}', parameters('runtimeIdentity').id)]": {}
        })
        schema_environment = self.broker['variables']['schemaEnvironment']
        self.assertIn("'AZURE_CLIENT_ID', parameters('schemaIdentity').clientId", schema_environment)
        self.assertIn("'SQL_RUNTIME_CLIENT_ID', parameters('runtimeIdentity').clientId", schema_environment)
        self.assertEqual(job['properties']['template']['containers'][0]['args'], ["[parameters('schemaAction')]"])

    def test_portal_schema_job_uses_only_the_schema_identity_to_grant_runtime_sql_access(self):
        job = next(resource for resource in self.portal_schema['resources']
                   if resource['type'] == 'Microsoft.App/jobs')
        self.assertEqual(job['identity']['userAssignedIdentities'], {
            "[format('{0}', parameters('schemaIdentity').id)]": {},
        })
        variables = self.portal_schema['variables']['variables']
        self.assertEqual(variables['PORTAL_SQL_AUTH'], 'managed-identity')
        self.assertEqual(variables['AZURE_CLIENT_ID'], "[parameters('schemaIdentity').clientId]")
        self.assertEqual(variables['PORTAL_SQL_RUNTIME_CLIENT_ID'], "[parameters('runtimeIdentity').clientId]")
        self.assertEqual(variables['PORTAL_SQL_DATABASE'], "[parameters('database')]")

    def test_app_entrypoints_pass_the_constructed_database_and_schema_identity(self):
        for template, expected_schema in (
            (self.gateway, "[parameters('foundation').schemaIdentity]"),
            (self.portal, "[parameters('schemaIdentity')]"),
        ):
            parameters = next(resource['properties']['parameters'] for resource in template['resources']
                              if {'database', 'schemaIdentity', 'runtimeIdentity'}
                              <= resource.get('properties', {}).get('parameters', {}).keys())
            self.assertEqual(parameters['schemaIdentity']['value'], expected_schema)
            self.assertIn('outputs.database.value', parameters['database']['value'])

    def test_each_app_exports_only_its_constructed_database_for_maintenance_inventory(self):
        for template in (self.gateway, self.portal):
            values = template['outputs']['managementMaintenance']['value']['sqlDatabaseIds']
            self.assertEqual(len(values), 1)
            self.assertIn('outputs.databaseId.value', values[0])


if __name__ == '__main__':
    unittest.main()
