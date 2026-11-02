"""Gatewayの配備境界、既存名の維持とDB初期化前のアプリ更新防止を確認する。"""
from bicep_helpers import compile_bicep

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def resources(template):
    """入れ子の配備を含めて、ARMテンプレートの実リソース定義を列挙する。"""
    entries = template.get("resources", [])
    for item in entries.values() if isinstance(entries, dict) else entries:
        yield item
        properties = item.get("properties", {})
        if isinstance(properties, dict):
            nested = properties.get("template", {})
            if isinstance(nested, dict):
                yield from resources(nested)


class GatewayInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'core': compile_bicep('infra/main.bicep'),
            'gateway': compile_bicep('apps/gateway/deploy/main.bicep'),
        }

    def module(self, suffix):
        return next(item for item in self.templates["gateway"]["resources"]
                    if item["name"] == "[format('{0}-" + suffix + "', variables('stem'))]")

    def test_core_does_not_own_gateway_application_resources(self):
        core = self.templates["core"]
        all_resources = list(resources(core))
        types = {item["type"].lower() for item in all_resources}
        self.assertNotIn("microsoft.sql/servers/databases", types)
        self.assertFalse(any("authorizationproviders" in item for item in types))
        self.assertFalse(any(item["type"].lower() == "microsoft.app/containerapps"
                             and "credential-broker" in item["name"] for item in all_resources))
        self.assertFalse(any(item["type"].lower() == "microsoft.managedidentity/userassignedidentities"
                             and "credential-broker" in item["name"] for item in all_resources))
        self.assertNotIn("credentialBrokerKey", core["parameters"])
        self.assertNotIn("existingCredentialBrokerApp", core["parameters"])
        self.assertNotIn("credentialBroker", core["outputs"])
        self.assertIn("gatewayFoundation", core["outputs"])

    def test_core_keeps_shared_private_sql_and_entra_secret(self):
        all_resources = list(resources(self.templates["core"]))
        server, = [item for item in all_resources if item["type"] == "Microsoft.Sql/servers"]
        self.assertEqual(server["name"], "[format('sql-{0}', parameters('stem'))]")
        self.assertEqual(server["properties"]["publicNetworkAccess"], "Disabled")
        self.assertIs(server["properties"]["administrators"]["azureADOnlyAuthentication"], True)
        self.assertEqual(server["properties"]["administrators"]["sid"], "[parameters('schemaIdentity').principalId]")
        shared_secrets = [item for item in all_resources if item["type"] == "Microsoft.KeyVault/vaults/secrets"
                          and "core-credential-" in item["name"]]
        self.assertEqual(len(shared_secrets), 1)
        self.assertNotIn("broker-key", shared_secrets[0]["name"])

    def test_gateway_creates_database_but_never_recreates_common_server_or_network(self):
        types = {item["type"].lower() for item in resources(self.templates["gateway"]) if not item.get("existing")}
        self.assertIn("microsoft.sql/servers/databases", types)
        self.assertIn("microsoft.apimanagement/service/authorizationproviders", types)
        self.assertNotIn("microsoft.sql/servers", types)
        self.assertNotIn("microsoft.apimanagement/service", types)
        self.assertNotIn("microsoft.app/managedenvironments", types)
        self.assertNotIn("microsoft.network/privateendpoints", types)
        self.assertNotIn("microsoft.authorization/roledefinitions", types)
        database = self.module("gateway-database")["properties"]["template"]
        db, = [item for item in database["resources"] if item["type"] == "Microsoft.Sql/servers/databases"]
        self.assertIn("sqldb-{0}-credentials", db["name"])
        self.assertEqual(db["properties"]["collation"], "Latin1_General_100_BIN2")

    def test_application_and_apim_settings_require_second_phase(self):
        gateway = self.templates["gateway"]
        self.assertIs(gateway["parameters"]["deployApplication"]["defaultValue"], False)
        settings = self.module("gateway-apim-settings")
        self.assertEqual(settings["condition"], "[parameters('deployApplication')]")
        broker = self.module("gateway-broker")["properties"]["template"]
        app, = [item for item in broker["resources"] if item["type"] == "Microsoft.App/containerApps"]
        self.assertEqual(app["condition"], "[parameters('deployApplication')]")
        jobs = [item for item in broker["resources"] if item["type"] == "Microsoft.App/jobs"]
        manual, = [item for item in jobs if item["properties"]["configuration"]["triggerType"] == "Manual"]
        self.assertNotIn("condition", manual)
        self.assertEqual(manual["name"], "[format('caj-{0}-schema', parameters('stem'))]")
        scheduled, = [item for item in jobs if item["properties"]["configuration"]["triggerType"] == "Schedule"]
        self.assertEqual(scheduled["condition"], "[parameters('deployApplication')]")

    def test_existing_key_and_resource_identity_names_are_preserved(self):
        gateway = self.templates["gateway"]
        for key in ("brokerKey", "credentialProviderSecrets"):
            self.assertTrue(gateway["parameters"][key]["type"].lower().startswith("secure"))
        access = self.module("gateway-access")["properties"]["template"]
        key, = [item for item in access["resources"] if item["type"] == "Microsoft.KeyVault/vaults/secrets"]
        self.assertEqual(key["condition"], "[not(empty(parameters('brokerKey')))]")
        self.assertIn("core-credential-broker-key", key["name"])
        identity = self.module("gateway-identity")["properties"]["template"]
        self.assertEqual(identity["resources"][0]["name"], "[format('id-{0}-credential-broker', parameters('stem'))]")
        self.assertNotIn("credentialProviderSecrets", json.dumps(gateway["outputs"]))


if __name__ == "__main__":
    unittest.main()
