"""paramsとconfigの分離入力を、実際のローカルCLIで通して検証する。

異なるフォルダーに置いたファイルを使い、モデルと秘密ファイル参照が出力先変更で壊れないことを調べる。PATHからAzure CLIを外し、意図しないクラウド接続を防ぐ。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support.configuration_fixtures import bind_credential_fixture_id
from tests import REPOSITORY

CORE = REPOSITORY
SCRIPTS = CORE / "scripts"
sys.path.insert(0, str(SCRIPTS))
from deployment.helpers.paths import private_parent
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "99999999-9999-4999-8999-999999999999"
CONNECTION = "44444444-4444-4444-8444-444444444444"
DNS_IDENTITY = ("/subscriptions/" + SUBSCRIPTION +
                "/resourceGroups/rg-dns-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi-dns-fixture")


class SplitCliTests(unittest.TestCase):
    """paramsとconfigの分離入力を、実際のローカルCLIで通して検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workforce-split-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.parameter_directory = self.root / ".local" / "test"
        self.config_directory = self.root / "platform" / "config"
        self.output_directory = self.parameter_directory / "generated"
        self.config_directory.mkdir(parents=True)
        private_parent(self.output_directory / "synthetic.json", self.root)
        # lifecycleの実行ROOTも合成repoへ揃える。公開資材だけをコピーし、実値領域を含めない。
        shutil.copytree(SCRIPTS, self.root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(CORE / "apps/api-gateway", self.root / "apps/api-gateway",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for relative in ("infra/modules/monitoring/log-privacy.json", "infra/modules/network/nsg/core-rules.json"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CORE / relative, target)
        self.options = json.loads((CORE / "config/test.json").read_text())
        self.parameters = json.loads((CORE / "params/test.example.json").read_text())
        bind_credential_fixture_id(self.parameters)
        self.parameters.update(subscriptionId=SUBSCRIPTION, azureTenantId=TENANT, entraTenantId=TENANT,
                               configFile="../../platform/config/options.json")
        self.assertNotIn("subscriptionKeyHeader", self.parameters["gateway"]["authentication"])
        self.assertEqual(self.options["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        self.assertNotIn("oauthTenantId", self.parameters["credentialProviders"]["graphDelegated"])
        self.parameters["credentialProviders"]["graphDelegated"]["clientId"] = CONNECTION
        self.parameters["services"]["apimInstanceId"] = "abc123"
        self.parameters["certificate"]["azureDnsSubscriptionId"] = SUBSCRIPTION
        self.parameters["certificate"]["azureDnsManagedIdentityResourceId"] = DNS_IDENTITY
        # 企業固有の参照はparamsだけに置きます。標準configの配置や合成先が
        # 異なるフォルダーでも、同じSSH・CM参照とDNS用IDを保持することを調べます。
        self.parameters["management"]["sshPublicKeyFile"] = "secrets/nonexistent-protected/management.pub"
        self.parameters["credentialProviders"]["graphDelegated"]["clientSecretFile"] = "secrets/nonexistent-protected/connection.txt"
        self.config = self.config_directory / "options.json"
        self.source = self.parameter_directory / "params.local.json"
        self.config.write_text(json.dumps(self.options))
        self.source.write_text(json.dumps(self.parameters))
        self.source.chmod(0o600)

    def run_cli(self, script, *arguments):
        result = subprocess.run([sys.executable, str(self.root / "scripts" / script), *map(str, arguments)],
                                cwd=self.root, capture_output=True, text=True,
                                env={"PATH": str(self.root / "no-external-tools"),
                                     "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_native_lifecycle_validation_accepts_the_same_split_source(self):
        result = self.run_cli("deployment/lifecycle.py", "validate", "--config", self.source)
        self.assertTrue(result["valid"])
        self.assertEqual(result["resourceGroups"]["id"], "rg-test-egakumi-id")
        self.assertEqual(len(result["resourceGroups"]), 5)

    def test_bicep_render_omits_loader_metadata_and_local_input_paths(self):
        output = self.output_directory / "arm.json"
        self.run_cli("deployment/helpers/configuration.py", "render", "--config", self.source, "--output", output)
        rendered = json.loads(output.read_text())
        self.assertNotIn("configFile", rendered)
        self.assertNotIn("$schema", rendered)
        self.assertNotIn("sshPublicKeyFile", rendered["management"])
        self.assertNotIn("pluginArgumentsFile", rendered["certificate"])
        self.assertEqual(rendered["certificate"]["azureDnsSubscriptionId"], SUBSCRIPTION)
        self.assertEqual(rendered["certificate"]["azureDnsManagedIdentityResourceId"], DNS_IDENTITY)
        self.assertNotIn("clientSecretFile", rendered["credentialProviders"][0])
        self.assertEqual(rendered["credentialProviders"][0]["clientId"], CONNECTION)
        self.assertEqual(rendered["credentialProviders"][0]["oauthTenantId"], TENANT)
        self.assertNotIn("nonexistent-protected", json.dumps(rendered))
        self.assertEqual(rendered["services"]["modelDeployments"],
                         json.loads((CORE / "config/test.json").read_text())["services"]["modelDeployments"])


if __name__ == "__main__":
    unittest.main()
