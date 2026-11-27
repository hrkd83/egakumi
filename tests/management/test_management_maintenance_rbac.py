"""手動付与する保守ロールの権限と対象資源の出力を検査する。

実際にコンパイルしたARM式を評価する。Azureの認可やログ・execの実成功は別検証。
"""
import unittest

from tests.support.arm_helpers import PolicyExpression
from tests.support.bicep_helpers import compile_bicep


# 実環境から独立した合成subscription・principal・資源名だけを使用します。
SUBSCRIPTION = '11111111-1111-4111-8111-111111111111'
GROUP = 'rg-fixture-svc'


class MaintenanceExpression(PolicyExpression):
    """既存ARM読取器へ資源ID・大小文字・整数範囲の関数だけを追加する。"""

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        if tree[0] == 'call':
            _, name, arguments = tree
            if name in {'toLower', 'range', 'sub', 'subscription', 'resourceGroup', 'resourceId', 'subscriptionResourceId', 'guid', 'reference'}:
                values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
                if name == 'toLower':
                    return values[0].lower()
                if name == 'range':
                    return list(range(values[0], values[0] + values[1]))
                if name == 'sub':
                    return values[0] - values[1]
                if name == 'subscription':
                    return {'id': '/subscriptions/' + SUBSCRIPTION, 'subscriptionId': SUBSCRIPTION}
                if name == 'resourceGroup':
                    return {'id': f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}', 'name': GROUP}
                if name == 'resourceId':
                    provider, *names = values
                    segments = provider.split('/')
                    suffix = segments[0] + '/' + '/'.join(part for pair in zip(segments[1:], names) for part in pair)
                    return f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/' + suffix
                if name == 'subscriptionResourceId':
                    return '/subscriptions/' + SUBSCRIPTION + '/providers/' + '/'.join(values)
                if name == 'guid':
                    # hashの再実装は不要。割当名を決める入力tupleの保持を検証します。
                    return tuple(values)
                if name == 'reference':
                    return parameters['__references'][values[0].rsplit('/', 1)[-1]]
        return super().evaluate(parameters, index, tree, bindings)


def evaluate(template, value, parameters, index=0):
    reader = MaintenanceExpression('[true()]', template.get('variables', {}))
    return reader.resolve(value, parameters, index)


def assignments(template):
    resources = template.get('resources', [])
    for resource in resources.values() if isinstance(resources, dict) else resources:
        if resource['type'] == 'Microsoft.Authorization/roleAssignments':
            yield resource
        if resource['type'] == 'Microsoft.Resources/deployments':
            nested = resource['properties'].get('template')
            if nested is not None:
                yield from assignments(nested)


class ManagementMaintenanceRbacTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = compile_bicep('infra/modules/management/management-maintenance-roles.bicep')
        cls.core = compile_bicep('infra/main.bicep')
        cls.vm = compile_bicep('infra/modules/management/management.bicep')
        cls.broker = compile_bicep('apps/credential-broker/deploy/broker.bicep')
        cls.gateway = compile_bicep('apps/api-gateway/deploy/main.bicep')
        cls.portal = compile_bicep('apps/portal/deploy/main.bicep')

    def test_initial_core_and_apps_do_not_assign_aca_maintenance_roles(self):
        families = ('containerapps/', 'jobs/', 'managedenvironments/')
        for template in (self.core, self.gateway, self.portal):
            scopes = [grant.get('scope', '').casefold() for grant in assignments(template)]
            self.assertFalse(any('microsoft.app/' + family in scope for family in families for scope in scopes))

    def test_core_does_not_assign_workspace_permissions_to_the_management_identity(self):
        resources = self.core['resources']
        for resource in resources.values() if isinstance(resources, dict) else resources:
            if resource['type'] != 'Microsoft.Resources/deployments':
                continue
            properties = resource['properties']
            principal = properties.get('parameters', {}).get('principalId', {}).get('value', '')
            if 'outputs.managementVm.value.principalId' not in principal:
                continue
            grants = assignments(properties['template'])
            self.assertFalse(any('microsoft.operationalinsights/workspaces' in grant.get('scope', '').casefold()
                                 for grant in grants))

    def test_management_vm_supports_named_operator_ssh(self):
        resources = self.vm['resources']
        resources = resources.values() if isinstance(resources, dict) else resources
        machine, = [item for item in resources if item['type'] == 'Microsoft.Compute/virtualMachines']
        self.assertIs(machine['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication'], True)
        self.assertEqual(machine['identity']['type'], 'SystemAssigned')
        extensions = [item['properties'] for item in resources
                      if item['type'] == 'Microsoft.Compute/virtualMachines/extensions']
        login, = [item for item in extensions if item['type'] == 'AADSSHLoginForLinux']
        self.assertEqual(login['publisher'], 'Microsoft.Azure.ActiveDirectory')

    def test_custom_roles_have_exact_approved_control_and_data_permissions(self):
        expected = {
            'appLogs': ([
                'Microsoft.App/containerApps/read',
                'Microsoft.App/containerApps/revisions/read',
                'Microsoft.App/containerApps/revisions/replicas/read',
                'Microsoft.App/containerApps/getAuthToken/action',
            ], ['Microsoft.App/containerApps/logstream/action']),
            'appExec': ([], ['Microsoft.App/containerApps/exec/action']),
            'environmentLogs': ([
                'Microsoft.App/managedEnvironments/read',
                'Microsoft.App/managedEnvironments/getAuthToken/action',
            ], []),
            'jobLogs': ([
                'Microsoft.App/jobs/read',
                'Microsoft.App/jobs/*/read',
                'Microsoft.App/jobs/getAuthToken/action',
            ], ['Microsoft.App/jobs/logstream/action']),
        }
        resource, = self.roles['resources']
        self.assertEqual(resource['type'], 'Microsoft.Authorization/roleDefinitions')
        self.assertNotIn('condition', resource)
        parameters = {'resourceStem': 'fixture'}
        count = evaluate(self.roles, resource['copy']['count'], parameters)
        self.assertEqual(count, len(expected))
        for index, definition in enumerate(self.roles['variables']['definitions']):
            with self.subTest(role=definition['key']):
                properties = evaluate(self.roles, resource['properties'], parameters, index)
                permission, = properties['permissions']
                actions, data_actions = expected[definition['key']]
                self.assertEqual(permission, {'actions': actions, 'notActions': [], 'dataActions': data_actions, 'notDataActions': []})
                self.assertEqual(properties['assignableScopes'], ['/subscriptions/' + SUBSCRIPTION])
                self.assertEqual(properties['type'], 'CustomRole')

    def test_broker_outputs_only_the_app_and_jobs_constructed_in_that_phase(self):
        parameters = {'stem': 'fixture', 'deployApplication': False}
        outputs = self.broker['outputs']
        environment = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App/managedEnvironments/cae-fixture-broker'
        self.assertEqual(evaluate(self.broker, outputs['environmentId']['value'], parameters), environment)
        self.assertEqual(evaluate(self.broker, outputs['applicationIds']['value'], parameters), [])
        # Job名は実コンパイルした資源から評価するため、予定名だけの出力は通しません。
        jobs = [resource for resource in self.broker['resources'] if resource['type'] == 'Microsoft.App/jobs']
        migration = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App/jobs/' + evaluate(self.broker, jobs[0]['name'], parameters)
        self.assertEqual(evaluate(self.broker, outputs['jobIds']['value'], parameters), [migration])
        parameters['deployApplication'] = True
        constructed = [f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App/jobs/' + evaluate(self.broker, job['name'], parameters) for job in jobs]
        self.assertEqual(evaluate(self.broker, outputs['jobIds']['value'], parameters), constructed)
        applications = evaluate(self.broker, outputs['applicationIds']['value'], parameters)
        self.assertEqual(len(applications), 1)
        self.assertTrue(applications[0].endswith('/Microsoft.App/containerApps/ca-fixture-credentials'))
        application, = [resource for resource in self.broker['resources'] if resource['type'] == 'Microsoft.App/containerApps']
        self.assertEqual(evaluate(self.broker, application['properties']['managedEnvironmentId'], parameters), environment)
        for job in jobs:
            self.assertEqual(evaluate(self.broker, job['properties']['environmentId'], parameters), environment)

    def test_app_entry_points_export_all_constructed_resource_id_families(self):
        for template in (self.gateway, self.portal):
            self.assertIn('managementMaintenance', template['outputs'])
        gateway = self.gateway['outputs']['managementMaintenance']['value']
        self.assertIn('applicationIds', gateway)
        self.assertIn('jobIds', gateway)
        self.assertIn('environmentIds', gateway)
        portal = self.portal['outputs']['managementMaintenance']['value']
        self.assertEqual(set(portal), {'applicationIds', 'jobIds', 'environmentIds', 'sqlDatabaseIds'})
        self.assertIn('sqlDatabaseIds', gateway)
        self.assertIn('outputs.databaseId.value', gateway['sqlDatabaseIds'][0])
        self.assertIn('outputs.databaseId.value', portal['sqlDatabaseIds'][0])
        self.assertIn("if(parameters('deployApplication')", portal['applicationIds'])
        job_id, = portal["jobIds"]
        self.assertIn("portal-schema-job", job_id)
        self.assertIn("outputs.id.value", job_id)

if __name__ == '__main__':
    unittest.main()
