"""単一Foundryの配備数制限を、課金資源の作成前に検出する。"""
import unittest

from configuration_fixtures import configuration, fixture


class FoundryDeploymentLimitTests(unittest.TestCase):
    """32件は受理し、上限超過はAzureへ送信する前に拒否する。"""

    def models(self, count):
        """実モデルの提供状況と独立した、配備数の検証用定義を作る。"""
        return [{'name': f'model-{index}', 'modelName': f'model-{index}',
                 'modelFormat': 'OpenAI', 'modelVersion': '1',
                 'skuName': 'GlobalStandard', 'capacity': 1,
                 'versionUpgradeOption': 'NoAutoUpgrade'} for index in range(count)]

    def test_exact_limit_is_accepted_and_next_deployment_rejected(self):
        for count in (0, 32, 33):
            raw = fixture()
            raw['services']['modelDeployments'] = self.models(count)
            with self.subTest(count=count):
                if count <= 32:
                    self.assertEqual(len(configuration.normalize_configuration(raw)
                                         ['services']['modelDeployments']), count)
                else:
                    with self.assertRaisesRegex(configuration.ConfigurationError, 'at most 32'):
                        configuration.normalize_configuration(raw)

if __name__ == '__main__':
    unittest.main()
