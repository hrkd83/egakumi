"""実コンパイルした監査保存先・条件を評価し、既存Locked環境への追加を確認する。"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_ssml_waf import GatewayExpression

CORE = Path(__file__).resolve().parents[1]
APIM_CONTAINERS = {'insights-logs-gatewaylogs', 'insights-logs-gatewayllmlogs',
                   'insights-logs-websocketconnectionlogs'}


class AuditExpression(GatewayExpression):
    """既存ARM評価器に文字列の正規化を追加する。配備条件自体は実ARMから読む。"""
    def resolve(self, item, parameters, index=0):
        if isinstance(item, str) and item.startswith('[') and item.endswith(']'):
            return AuditExpression(item, self.variables).evaluate(parameters, index)
        if isinstance(item, dict):
            return {key: self.resolve(value, parameters, index) for key, value in item.items()}
        if isinstance(item, list):
            return [self.resolve(value, parameters, index) for value in item]
        return item

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] in ('toLower', 'replace'):
            values = [self.evaluate(parameters, index, argument, bindings) for argument in tree[2]]
            return values[0].lower() if tree[1] == 'toLower' else values[0].replace(values[1], values[2])
        return super().evaluate(parameters, index, tree, bindings)


class ApimAuditRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bicep = os.environ.get('BICEP_CLI') or shutil.which('bicep') or str(Path.home() / '.azure/bin/bicep')
        if not Path(bicep).is_file():
            raise unittest.SkipTest('A local Bicep compiler is required')
        with tempfile.TemporaryDirectory(prefix='core-audit-retention-') as folder:
            result = subprocess.run([bicep, 'build', str(CORE / 'infra/main.bicep'), '--stdout', '--no-restore'],
                                    env={**os.environ, 'DOTNET_BUNDLE_EXTRACT_BASE_DIR': folder},
                                    capture_output=True, text=True, check=False)
            if result.returncode:
                raise AssertionError(result.stderr)
            main = json.loads(result.stdout)
        cls.module = next(resource for resource in main['resources']
                          if resource.get('name') == "[format('{0}-monitoring', variables('stem'))]")
        cls.template = cls.module['properties']['template']
        cls.policy = next(resource for resource in cls.template['resources']
                          if resource['type'].endswith('/immutabilityPolicies'))

    def parameters(self, environment='production', **overrides):
        result = {key: copy.deepcopy(value['defaultValue'])
                  for key, value in self.template['parameters'].items() if 'defaultValue' in value}
        result.update(productName='fixture', environmentName=environment, resourceStem=environment + '-fixture',
                      location='japaneast', foundryLocation='eastus2')
        options = json.loads((CORE / f'config/{environment}.json').read_text())['monitoring']
        result.update({key: options[key] for key in ('auditImmutabilityMode', 'auditRetentionDays', 'auditStorageSku')})
        result.update(overrides)
        return result

    def evaluate(self, value, parameters, index=0):
        return AuditExpression(value, self.template['variables']).evaluate(parameters, index)

    def definitions(self, parameters):
        return self.evaluate("[variables('containerDefinitions')]", parameters)

    def policy_ids(self, parameters):
        expression = next(value['input'] for value in self.template['variables']['copy']
                          if value['name'] == 'policyIds')
        return [self.evaluate(expression, parameters, index)
                for index in range(len(self.definitions(parameters)))]

    def selected(self, parameters):
        return [item['name'] for index, item in enumerate(self.definitions(parameters))
                if self.evaluate(self.policy['condition'], parameters, index)]

    def test_production_protects_all_three_apim_categories_in_the_japan_collector(self):
        params = self.parameters()
        definitions = self.definitions(params)
        self.assertEqual(len(definitions), 20)
        japan = {item['name'] for item in definitions if item['accountIndex'] == 0}
        self.assertEqual(len(japan), 19)
        self.assertTrue(APIM_CONTAINERS <= japan)
        self.assertTrue(APIM_CONTAINERS <= set(self.selected(params)))
        self.assertEqual(self.evaluate(self.policy['properties']['immutabilityPeriodSinceCreationInDays'], params), 2557)
        self.assertTrue(self.policy['properties']['allowProtectedAppendWrites'])

    def test_test_environment_does_not_create_immutability_policies(self):
        self.assertEqual(self.selected(self.parameters('test')), [])

    def test_existing_eighteen_locked_containers_only_add_the_two_new_policies(self):
        params = self.parameters()
        definitions = self.definitions(params)
        added = APIM_CONTAINERS - {'insights-logs-gatewaylogs'}
        params['retainedAuditImmutabilityPolicyIds'] = [policy_id.upper()
            for definition, policy_id in zip(definitions, self.policy_ids(params))
            if definition['name'] not in added]
        self.assertEqual(set(self.selected(params)), added)
        self.assertEqual(self.module['properties']['parameters']['retainedAuditImmutabilityPolicyIds'],
                         {'value': "[parameters('retainedAuditImmutabilityPolicyIds')]"})

    def test_repeat_preserves_all_locks_and_native_core_phase_does_not_put_policies(self):
        params = self.parameters()
        params['retainedAuditImmutabilityPolicyIds'] = self.policy_ids(params)
        self.assertEqual(self.selected(params), [])
        self.assertEqual(self.selected(self.parameters(manageAuditImmutabilityPolicies=False)), [])


if __name__ == '__main__':
    unittest.main()
