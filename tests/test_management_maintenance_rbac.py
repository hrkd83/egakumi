"""構築した資源だけへの保守権限と、既存監査grantを保持する合成入力の検査。

実際にコンパイルしたARM式を評価する。Azureの認可やログ・execの実成功は別検証。
"""
import unittest

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep


# 実環境から独立した合成subscription・principal・資源名だけを使用します。
SUBSCRIPTION = '11111111-1111-4111-8111-111111111111'
PRINCIPAL = '22222222-2222-4222-8222-222222222222'
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


class ManagementMaintenanceRbacTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = compile_bicep('infra/modules/management-maintenance-roles.bicep')
        cls.aca = compile_bicep('infra/modules/management-aca-bindings.bicep')
        cls.monitoring = compile_bicep('infra/modules/management-monitoring-bindings.bicep')
        cls.audit = compile_bicep('infra/modules/audit-reader-bindings.bicep')
        cls.broker = compile_bicep('apps/gateway/deploy/broker.bicep')
        cls.gateway = compile_bicep('apps/gateway/deploy/main.bicep')
        cls.portal = compile_bicep('apps/portal/deploy/main.bicep')

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

    def test_aca_assignments_are_typed_resource_scopes_and_service_principal_only(self):
        parameters = {
            'principalId': PRINCIPAL,
            'applicationNames': ['ca-fixture-portal', 'ca-fixture-credentials'],
            'jobNames': ['job-fixture-schema', 'job-fixture-reconcile'],
            'environmentNames': ['cae-fixture'],
            'roleDefinitionIds': {key: '/subscriptions/' + SUBSCRIPTION + '/providers/Microsoft.Authorization/roleDefinitions/' + key
                                  for key in ('appLogs', 'appExec', 'environmentLogs', 'jobLogs')},
        }
        expected = {
            'applicationLogGrants': ('Microsoft.App/containerApps/', 'appLogs', 'applicationNames', 2),
            'applicationExecGrants': ('Microsoft.App/containerApps/', 'appExec', 'applicationNames', 2),
            'jobLogGrants': ('Microsoft.App/jobs/', 'jobLogs', 'jobNames', 2),
            'environmentTokenGrants': ('Microsoft.App/managedEnvironments/', 'environmentLogs', 'environmentNames', 1),
        }
        self.assertEqual(len(self.aca['resources']), len(expected))
        for resource in self.aca['resources']:
            provider, role, family, count = expected[resource['copy']['name']]
            self.assertEqual(resource['type'], 'Microsoft.Authorization/roleAssignments')
            self.assertEqual(evaluate(self.aca, resource['copy']['count'], parameters), count)
            for index in range(count):
                with self.subTest(grant=resource['copy']['name'], index=index):
                    scope = evaluate(self.aca, resource['scope'], parameters, index)
                    properties = evaluate(self.aca, resource['properties'], parameters, index)
                    self.assertEqual(scope, f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/' + provider + parameters[family][index])
                    self.assertEqual(properties, {'principalId': PRINCIPAL, 'principalType': 'ServicePrincipal', 'roleDefinitionId': parameters['roleDefinitionIds'][role]})

    def test_missing_constructed_resources_do_not_create_unscoped_grants(self):
        parameters = {'principalId': PRINCIPAL, 'roleDefinitionIds': {}, 'applicationNames': [], 'jobNames': [], 'environmentNames': []}
        for resource in self.aca['resources']:
            self.assertEqual(evaluate(self.aca, resource['copy']['count'], parameters), 0)

    def test_repeated_constructed_targets_share_one_grant_per_role(self):
        parameters = {'principalId': PRINCIPAL, 'roleDefinitionIds': {},
                      'applicationNames': ['ca-fixture', 'ca-fixture'],
                      'jobNames': ['job-fixture', 'job-fixture'],
                      'environmentNames': ['cae-fixture', 'cae-fixture']}
        for resource in self.aca['resources']:
            self.assertEqual(evaluate(self.aca, resource['copy']['count'], parameters), 1)

    def test_log_analytics_data_reader_is_at_the_workspace_and_does_not_write_it(self):
        resource, = self.monitoring['resources']
        parameters = {'principalId': PRINCIPAL, 'workspaceName': 'law-fixture'}
        self.assertEqual(resource['type'], 'Microsoft.Authorization/roleAssignments')
        self.assertEqual(evaluate(self.monitoring, resource['scope'], parameters), f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.OperationalInsights/workspaces/law-fixture')
        properties = evaluate(self.monitoring, resource['properties'], parameters)
        self.assertEqual(properties['principalType'], 'ServicePrincipal')
        self.assertEqual(properties['principalId'], PRINCIPAL)
        self.assertEqual(properties['roleDefinitionId'], '/subscriptions/' + SUBSCRIPTION + '/providers/Microsoft.Authorization/roleDefinitions/3b03c2da-16b3-4a49-8834-0f8130efdd3b')

    def test_audit_duplicate_merge_preserves_first_reader_and_old_grant_name_inputs(self):
        synthetic_alpha_principal = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        existing = {'containerName': 'insights-logs-audit', 'principalId': synthetic_alpha_principal.upper(), 'principalType': 'ServicePrincipal'}
        automatic = {**existing, 'principalId': synthetic_alpha_principal.lower()}
        other = {**existing, 'containerName': 'am-syslog'}
        parameters = {'storageAccountName': 'stfixtureaudit', 'readers': [existing, automatic, other]}
        resource, = self.audit['resources']
        self.assertEqual(evaluate(self.audit, resource['copy']['count'], parameters), 2)
        values = [evaluate(self.audit, resource['properties'], parameters, index)
                  for index in range(2)]
        self.assertEqual({value['principalId'] for value in values}, {existing['principalId']})
        names = [evaluate(self.audit, resource['name'], parameters, index) for index in range(2)]
        self.assertEqual({name[1] for name in names}, {existing['principalId']})
        self.assertEqual({name[2] for name in names}, {'Storage Blob Data Reader'})
        self.assertEqual({name[0].split('/')[-1] for name in names}, {'am-syslog', 'insights-logs-audit'})

    def test_broker_outputs_only_the_app_and_jobs_constructed_in_that_phase(self):
        parameters = {'stem': 'fixture', 'environmentName': 'cae-fixture', 'deployApplication': False}
        outputs = self.broker['outputs']
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
        self.assertEqual(evaluate(self.broker, outputs['environmentId']['value'], parameters), f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App/managedEnvironments/cae-fixture')

    def test_app_entry_points_export_all_constructed_resource_id_families(self):
        for template in (self.gateway, self.portal):
            self.assertIn('managementMaintenance', template['outputs'])
        gateway = self.gateway['outputs']['managementMaintenance']['value']
        self.assertIn('applicationIds', gateway)
        self.assertIn('jobIds', gateway)
        self.assertIn('environmentIds', gateway)
        portal = self.portal['outputs']['managementMaintenance']['value']
        self.assertEqual(set(portal), {'applicationIds', 'jobIds', 'environmentIds'})
        self.assertIn("if(parameters('deployApplication')", portal['applicationIds'])
        self.assertIn("if(parameters('deployApplication')", portal['jobIds'])
        self.assertIn("outputs.id.value", portal['jobIds'])

    def test_app_deployment_bindings_use_constructed_resources_in_each_phase(self):
        base = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/'
        roles = {key: base + 'Microsoft.Authorization/roleDefinitions/' + key
                 for key in ('appLogs', 'appExec', 'environmentLogs', 'jobLogs')}
        output = lambda values: {'outputs': {key: {'value': value} for key, value in values.items()}}
        for deploy in (False, True):
            with self.subTest(template='gateway', deployApplication=deploy):
                gateway = next(resource for resource in self.gateway['resources']
                    if resource.get('properties', {}).get('template', {}).get('parameters', {}).get('applicationNames') is not None)
                parameters = {
                    'configuration': {'resourceStem': 'fixture', 'credentialProfiles': {'fixture': {}}},
                    'foundation': {'managementMaintenance': {'principalId': PRINCIPAL, 'roleDefinitionIds': roles}},
                    'deployApplication': deploy,
                    '__references': {'fixture-gateway-broker': output({
                        'applicationIds': [base + 'Microsoft.App/containerApps/ca-fixture-credentials'] if deploy else [],
                        'jobIds': [base + 'Microsoft.App/jobs/caj-fixture-csql'] + ([base + 'Microsoft.App/jobs/caj-fixture-creconcile'] if deploy else []),
                        'environmentId': base + 'Microsoft.App/managedEnvironments/cae-fixture',
                    })},
                }
                self.assertTrue(evaluate(self.gateway, gateway['condition'], parameters))
                values = evaluate(self.gateway, gateway['properties']['parameters'], parameters)
                self.assertEqual(values['principalId']['value'], PRINCIPAL)
                self.assertEqual(values['roleDefinitionIds']['value'], roles)
                self.assertEqual(values['applicationNames']['value'], ['ca-fixture-credentials'] if deploy else [])
                self.assertEqual(values['jobNames']['value'], ['caj-fixture-csql'] + (['caj-fixture-creconcile'] if deploy else []))
                self.assertEqual(values['environmentNames']['value'], ['cae-fixture'])
                parameters['configuration']['credentialProfiles'] = {}
                self.assertFalse(evaluate(self.gateway, gateway['condition'], parameters))
            with self.subTest(template='portal', deployApplication=deploy):
                portal = next(resource for resource in self.portal['resources']
                    if resource.get('properties', {}).get('template', {}).get('parameters', {}).get('applicationNames') is not None)
                parameters = {
                    'stem': 'fixture', 'deployApplication': deploy,
                    'managementPrincipalId': PRINCIPAL, 'managementRoleDefinitionIds': roles,
                    '__references': {
                        'fixture-portal-schema-job': output({'id': base + 'Microsoft.App/jobs/caj-fixture-psql',
                                                            'environmentId': base + 'Microsoft.App/managedEnvironments/cae-fixture'}),
                        'fixture-portal-application': output({'id': base + 'Microsoft.App/containerApps/ca-fixture-portal'}),
                        'fixture-portal-group-reconciliation': output({'id': base + 'Microsoft.App/jobs/caj-fixture-preconcile'}),
                    },
                }
                self.assertNotIn('condition', portal)
                values = evaluate(self.portal, portal['properties']['parameters'], parameters)
                self.assertEqual(values['principalId']['value'], PRINCIPAL)
                self.assertEqual(values['roleDefinitionIds']['value'], roles)
                self.assertEqual(values['applicationNames']['value'], ['ca-fixture-portal'] if deploy else [])
                self.assertEqual(values['jobNames']['value'], ['caj-fixture-psql'] + (['caj-fixture-preconcile'] if deploy else []))
                self.assertEqual(values['environmentNames']['value'], ['cae-fixture'])


if __name__ == '__main__':
    unittest.main()
