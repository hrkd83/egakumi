"""有効な構築入力から作るACA名を、配備・実行待機・撤去のPython参照へ照合する。"""
from pathlib import Path
import sys
import unittest
from unittest import mock

from arm_helpers import ArmExpression
from bicep_helpers import compile_bicep
from configuration_fixtures import fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_portal
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.credential_pools import resource_names as broker_names
from deployment.helpers.portal_resources import resource_names as portal_names


class ApplicationResourceNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = compile_bicep('apps/gateway/deploy/broker.bicep')
        cls.portal_sql = compile_bicep('apps/portal/deploy/schema-job.bicep')
        cls.portal_sync = compile_bicep('apps/portal/deploy/group-reconciliation-job.bicep')
        cls.portal = compile_bicep('infra/modules/portal-platform.bicep')

    def configurations(self):
        for product, environment, length in (('sample', 'dev', 10), ('sample', 'staging', 14),
                                             ('egakumi', 'prod', 12), ('egakumi', 'test', 12), ('p' * 12, 'test', 17)):
            raw = fixture()
            raw['productName'], raw['environmentName'] = product, environment
            config = normalize_configuration(raw)
            self.assertEqual(len(config['resourceStem']), length)
            self.assertLessEqual(len(config['resourceStem'].replace('-', '')), 16)
            yield config

    def test_compiled_names_match_python_and_stay_below_the_cli_job_limit(self):
        """既定productionと最大長を含む入力を、そのまま固定の用途名へ連結する。"""
        for config in self.configurations():
            stem = config['resourceStem']
            with self.subTest(stem=stem):
                parameters = {'stem': stem}
                broker = broker_names(config)
                portal = portal_names(config)
                expected_broker = {broker['application'], broker['schemaJob'], broker['reconcileJob']}
                actual_broker = {ArmExpression(item['name']).evaluate(parameters, 0)
                                 for item in self.broker['resources']
                                 if item['type'].lower() in ('microsoft.app/containerapps', 'microsoft.app/jobs')}
                self.assertEqual(actual_broker, expected_broker)
                actual_portal_jobs = []
                for template in (self.portal_sql, self.portal_sync):
                    job, = [item for item in template['resources'] if item['type'].lower() == 'microsoft.app/jobs']
                    actual_portal_jobs.append(ArmExpression(job['name']).evaluate(parameters, 0))
                self.assertEqual(actual_portal_jobs, [portal['schemaJob'], portal['reconcileJob']])
                application = ArmExpression(self.portal['variables']['applicationName']).evaluate(parameters, 0)
                app, = [item for item in self.portal['resources'] if item['type'].lower() == 'microsoft.app/containerapps']
                self.assertEqual(ArmExpression(app['name']).evaluate({'__variables': {'applicationName': application}}, 0), application)
                self.assertEqual(application, 'ca-' + stem + '-portal')
                for name in {*actual_broker, *actual_portal_jobs, application}:
                    self.assertLessEqual(len(name), 32)
                    self.assertRegex(name, r'^[a-z][a-z0-9-]*[a-z0-9]$')
                    self.assertNotIn('--', name)
                for name in (broker['schemaJob'], broker['reconcileJob'], *actual_portal_jobs):
                    self.assertLess(len(name), 32)

    def test_portal_execution_wait_reads_the_same_schema_job_name(self):
        for config in self.configurations():
            with self.subTest(stem=config['resourceStem']):
                transport = mock.Mock()
                transport.request.return_value = {'statusCode': 200, 'body': {'properties': {'status': 'Succeeded'}}}
                deploy_portal.wait_schema(config, 'synthetic-execution', transport)
                target = (deploy_portal.group_scope(config) + '/providers/Microsoft.App/jobs/'
                          + portal_names(config)['schemaJob'] + '/executions/synthetic-execution?api-version=' + deploy_portal.APP_API)
                transport.request.assert_called_once_with('GET', target, None)


if __name__ == '__main__':
    unittest.main()
