"""Azure 資源の基本名と、保存済み構築識別子を加えたAPIM名を確認する。

実際に配布する Bicep を公式コンパイラーで ARM に変換し、Core 本体と各
モジュールの既定値を照合する。Azure への接続や資源の作成は行わない。
名前の切り詰めや実行ごとの識別子変更は、Entra に事前登録する URI と実資源の
不整合を招くため、設定検査とは別に生成結果の契約も確認する。
"""
from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class ResourceNamingTests(unittest.TestCase):
    """公開名、RG 所有範囲、Storage の例外表記を一つの命名規則で検査する。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/main.bicep')
        cls.variables = cls.template["variables"]

    @classmethod
    def module(cls, name):
        """main から実際に呼び出される埋め込み ARM モジュールを取得する。"""
        resource = next(item for item in cls.template["resources"]
                        if item["type"] == "Microsoft.Resources/deployments"
                        and item["name"] == "[format('{0}-" + name + "', variables('stem'))]")
        return resource["properties"]["template"]

    def test_service_names_and_owned_groups_use_environment_product(self):
        self.assertEqual(self.variables["stem"],
                         "[format('{0}-{1}', variables('environmentName'), variables('product'))]")
        expected_names = {
            key: "[format('" + prefix + "-{0}', variables('stem'))]"
            for key, prefix in (("keyVault", "kv"), ("foundry", "aif"),
                                ("project", "proj"), ("apiCenter", "apic"))
        }
        expected_names["apim"] = "[format('apim-{0}-{1}', variables('stem'), parameters('configuration').services.apimInstanceId)]"
        self.assertEqual(self.variables["serviceNames"], expected_names)
        groups = [item for item in self.template["resources"]
                  if item["type"] == "Microsoft.Resources/resourceGroups"]
        self.assertEqual({item["name"] for item in groups}, {
            "[format('rg-{0}-" + kind + "', variables('stem'))]"
            for kind in ("id", "nw", "monitor", "svc", "maint")
        })

    def test_module_defaults_keep_the_same_environment_product_order(self):
        expected = "[format('{0}-{1}', parameters('environmentName'), parameters('productName'))]"
        for name in ("monitoring", "management", "gateway"):
            with self.subTest(module=name):
                self.assertEqual(self.module(name)["parameters"]["resourceStem"]["defaultValue"], expected)

    def test_storage_names_remove_only_hyphens_and_retain_collector_purpose(self):
        monitoring = self.module("monitoring")
        self.assertEqual(monitoring["variables"]["compactName"],
                         "[replace(parameters('resourceStem'), '-', '')]")
        self.assertEqual([entry["name"] for entry in monitoring["variables"]["auditAccounts"]],
                         ["[format('st{0}jp', variables('compactName'))]",
                          "[format('st{0}us', variables('compactName'))]"])

    def test_linux_host_name_remains_recognizable_without_hash(self):
        self.assertEqual(self.module("management")["variables"]["computerName"],
                         "[format('mgmt-{0}', parameters('resourceStem'))]")


if __name__ == "__main__":
    unittest.main()
