"""Portal配備がDBを所有し、共通基盤を変更しないことを確認する。"""
from bicep_helpers import compile_bicep

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration, ConfigurationError
from deployment.helpers.portal_authentication import prepare_secret, remove_secret
from deployment.helpers.grpc_lifecycle import secret_path
from deployment.lifecycle import parameters
from configuration_fixtures import fixture


class PortalAuthenticationOptionsTests(unittest.TestCase):
    def test_normalization_supplies_settings_without_changing_the_input(self):
        raw = fixture()
        raw.pop("portalAuthentication", None)
        original = copy.deepcopy(raw)
        result = normalize_configuration(raw)
        self.assertEqual(raw, original)
        settings = result["portalAuthentication"]
        self.assertEqual(settings["sqlTier"], "Basic")
        self.assertEqual(settings["sqlCapacity"], 5)
        self.assertEqual(settings["credentialProviderName"], "graph-delegated")
        self.assertEqual(build_bicep_configuration(result)["portalAuthentication"], settings)

    def test_normalization_rejects_invalid_database_combinations(self):
        for key, value in (("sqlSkuName", "GP_Gen5_2"), ("sqlCapacity", 2),
                           ("sqlMaxSizeGB", 3), ("backupRetentionDays", 8),
                           ("zoneRedundant", True), ("credentialProviderName", "contains/slash")):
            with self.subTest(key=key):
                raw = fixture()
                raw["portalAuthentication"][key] = value
                with self.assertRaises(ConfigurationError):
                    normalize_configuration(raw)

    def test_test_and_production_settings_do_not_contain_tenant_specific_inputs(self):
        schemas = [json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
                   for name in ("options.schema.json", "configuration.schema.json")]
        self.assertEqual(schemas[0]["properties"]["portalAuthentication"],
                         schemas[1]["properties"]["portalAuthentication"])
        self.validator = Draft202012Validator(schemas[0]["properties"]["portalAuthentication"])
        for profile, tier in (("test", "Basic"), ("production", "GeneralPurpose")):
            settings = json.loads((ROOT / "config" / (profile + ".json")).read_text(encoding="utf-8"))["portalAuthentication"]
            self.assertEqual(list(self.validator.iter_errors(settings)), [])
            self.assertEqual(settings["sqlTier"], tier)
            self.assertEqual(settings["credentialProviderName"], "graph-delegated")
            self.assertTrue(set(settings) <= {"credentialProviderName", "sqlSkuName", "sqlTier", "sqlCapacity",
                                             "sqlMaxSizeGB", "backupRetentionDays", "zoneRedundant", "backupRedundancy"})

    def test_basic_configuration_rejects_unavailable_size_retention_and_zone_features(self):
        schema = json.loads((ROOT / "schemas/options.schema.json").read_text(encoding="utf-8"))["properties"]["portalAuthentication"]
        validator = Draft202012Validator(schema)
        basic = json.loads((ROOT / "config/test.json").read_text(encoding="utf-8"))["portalAuthentication"]
        for key, value in (("sqlSkuName", "GP_Gen5_2"), ("sqlCapacity", 2), ("sqlMaxSizeGB", 3),
                           ("backupRetentionDays", 8), ("zoneRedundant", True)):
            with self.subTest(key=key):
                invalid = copy.deepcopy(basic)
                invalid[key] = value
                self.assertTrue(list(validator.iter_errors(invalid)))


class PortalSigningSecretTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="portal-signing-key-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111",
                       "azureTenantId": "22222222-2222-4222-8222-222222222222",
                       "resourceStem": "test-fixture"}

    def write_protected(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def test_signing_key_is_generated_once_and_is_separate_from_other_credentials(self):
        result = prepare_secret(self.config, self.root, self.write_protected)
        again = prepare_secret(self.config, self.root, lambda *_: self.fail("Existing key was overwritten"))
        self.assertEqual(result, again)
        self.assertRegex(result["portalAuthenticationSecret"], r"^[a-f0-9]{128}$")
        self.assertTrue(secret_path(self.config, self.root, "portal-auth").is_file())
        self.assertFalse(secret_path(self.config, self.root, "credential-broker").exists())
        self.assertFalse((self.root / "params").exists())

    def test_other_environment_key_cannot_be_reused_or_removed(self):
        prepare_secret(self.config, self.root, self.write_protected)
        foreign = {**self.config, "azureTenantId": "33333333-3333-4333-8333-333333333333"}
        with self.assertRaisesRegex(ValueError, "different environment"):
            prepare_secret(foreign, self.root, self.write_protected)
        with self.assertRaisesRegex(ValueError, "different environment"):
            remove_secret(foreign, self.root)
        self.assertTrue(secret_path(self.config, self.root, "portal-auth").is_file())

    def test_cleanup_only_removes_the_portal_key(self):
        prepare_secret(self.config, self.root, self.write_protected)
        other = secret_path(self.config, self.root, "credential-broker")
        self.write_protected(other, {"kept": True})
        remove_secret(self.config, self.root)
        self.assertFalse(secret_path(self.config, self.root, "portal-auth").exists())
        self.assertTrue(other.is_file())


class PortalAuthenticationBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'core': compile_bicep('infra/main.bicep'),
            'portal': compile_bicep('apps/portal/deploy/main.bicep'),
        }
        cls.main = cls.templates["core"]

    def module(self, name, target="core"):
        return next(resource for resource in self.templates[target]["resources"]
                    if resource["type"] == "Microsoft.Resources/deployments"
                    and resource["name"] == "[format('{0}-" + name + "', "
                    + ("variables" if target == "core" else "parameters") + "('stem'))]")

    def test_database_is_separate_and_reuses_existing_server(self):
        module = self.module("portal-database", "portal")
        self.assertNotIn("condition", module)
        template = module["properties"]["template"]
        resources = template["resources"]
        self.assertNotIn("Microsoft.Sql/servers", {item["type"] for item in resources})
        self.assertNotIn("Microsoft.Network/privateEndpoints", {item["type"] for item in resources})
        database, = [item for item in resources if item["type"] == "Microsoft.Sql/servers/databases"]
        self.assertIn("first(split(parameters('sqlServer'), '.'))", database["name"])
        self.assertIn("format('sqldb-{0}-portal', parameters('stem'))", database["name"])
        self.assertIn("if(equals(parameters('settings').sqlTier, 'Basic'), createObject()", template["variables"]["databaseSku"])
        self.assertIn("family", template["variables"]["databaseSku"])
        self.assertNotIn("portalAuthenticationSecret", self.main["parameters"])
        self.assertFalse(any("portal-authentication" in item.get("name", "") for item in self.main["resources"]))

    def test_secret_read_rights_are_scoped_and_only_signing_and_profile_secrets_are_written(self):
        resources = self.module("portal-database", "portal")["properties"]["template"]["resources"]
        secrets = [item for item in resources if item["type"] == "Microsoft.KeyVault/vaults/secrets"]
        self.assertEqual(len(secrets), 2)
        for name, parameter in (("core-portal-auth-secret", "signingSecret"),
                                ("core-subscription-profile-key", "profileSecret")):
            secret, = [item for item in secrets if name in item["name"]]
            self.assertEqual(secret["condition"], "[not(empty(parameters('" + parameter + "')))]")
        roles = [item for item in resources if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(len(roles), 3)
        for role in roles:
            self.assertIn("Microsoft.KeyVault/vaults/secrets", role["scope"])
            self.assertEqual(role["properties"]["principalId"], "[parameters('runtimeIdentity').principalId]")
        self.assertNotIn("Microsoft.Authorization/roleDefinitions", {item["type"] for item in resources})

    def test_existing_portal_identity_and_schema_identity_are_reused(self):
        identity = self.module("portal-identity")["properties"]["template"]
        names = [item["name"] for item in identity["resources"]]
        self.assertEqual(names, ["[format('id-{0}-portal-pull', parameters('stem'))]"])
        self.assertEqual(set(identity["outputs"]["identity"]["value"]), {"id", "name", "principalId", "clientId"})
        sql = self.module("credential-sql")["properties"]["template"]
        server, = [item for item in sql["resources"] if item["type"] == "Microsoft.Sql/servers"]
        self.assertEqual(server["properties"]["publicNetworkAccess"], "Disabled")
        self.assertIs(server["properties"]["administrators"]["azureADOnlyAuthentication"], True)
        self.assertEqual(server["properties"]["administrators"]["sid"], "[parameters('schemaIdentity').principalId]")
        self.assertEqual(self.templates["portal"]["parameters"]["signingSecret"]["type"].lower(), "securestring")
        self.assertNotIn("authentication", self.main["outputs"]["portal"]["value"])
        for key in ("runtimeIdentity", "schemaIdentity", "sharedSqlServer", "sharedSqlServerId", "sharedSqlPrivateEndpointId"):
            self.assertIn(key, self.main["outputs"]["portal"]["value"])

    def test_migration_is_manual_and_application_only_follows_explicit_second_phase(self):
        portal = self.templates["portal"]
        self.assertIs(portal["parameters"]["deployApplication"]["defaultValue"], False)
        app = self.module("portal-application", "portal")
        self.assertEqual(app["condition"], "[parameters('deployApplication')]")
        schema = self.module("portal-schema-job", "portal")["properties"]["template"]
        migration, = [item for item in schema["resources"] if item["type"] == "Microsoft.App/jobs"]
        self.assertNotIn("condition", migration)
        self.assertEqual(migration["name"], "[format('caj-{0}-portal-schema', parameters('stem'))]")
        config = migration["properties"]["configuration"]
        self.assertEqual(config["triggerType"], "Manual")
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertEqual(config["manualTriggerConfig"]["parallelism"], 1)
        self.assertEqual(schema["variables"]["variables"]["AZURE_CLIENT_ID"], "[parameters('schemaIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_SQL_RUNTIME_CLIENT_ID"], "[parameters('runtimeIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_GROUPS"], "[concat(' ', string(parameters('apiKeyGroups')))]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_TENANT_ID"], "[parameters('apiKeyTenantId')]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS"], "[concat(' ', string(parameters('apiKeyVerificationSubscriptions')))]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_APIM_CLIENT_ID"], "[parameters('runtimeIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_APIM_RESOURCE_ID"], "[parameters('apimResourceId')]")
        self.assertIn("parameters('runtimeIdentity').id", json.dumps(migration["identity"]))
        container = migration["properties"]["template"]["containers"][0]
        self.assertIn("scripts/migrate-auth.ts", container["command"])
        image_access, = [item for item in schema["resources"] if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(image_access["properties"]["principalId"], "[parameters('schemaIdentity').principalId]")
        self.assertIn("Microsoft.ContainerRegistry/registries", image_access["scope"])
        self.assertIn("7f951dda-4ed3-4680-a7ca-43fe172d538d", schema["variables"]["acrPull"])

    def test_gateway_egress_only_allows_selected_gateway_from_aca(self):
        module = self.module("portal-gateway-egress", "portal")
        self.assertEqual(module["resourceGroup"], "[parameters('networkResourceGroup')]")
        rules = module["properties"]["template"]["resources"][0]
        self.assertIn("rcg-portal-gateway", rules["name"])
        collections = rules["properties"]["ruleCollections"]
        self.assertEqual(len(collections), 1)
        rule = collections[0]["rules"][0]
        self.assertEqual(rule["sourceAddresses"], ["[parameters('acaPrefix')]"])
        self.assertEqual(rule["protocols"], [{"protocolType": "Https", "port": 443}])
        self.assertEqual(rule["targetFqdns"], ["[split(parameters('gatewayOrigin'), '/')[2]]"])
        self.assertFalse(rule["terminateTLS"])

    def test_application_uses_vault_references_and_managed_identity_sql(self):
        template = self.module("portal-application", "portal")["properties"]["template"]
        app, = [item for item in template["resources"] if item["type"] == "Microsoft.App/containerApps"]
        secrets = app["properties"]["configuration"]["secrets"]
        self.assertEqual({secret['name'] for secret in secrets}, {
            'auth-signing-secret', 'microsoft-client-secret', 'subscription-profile-secret'})
        self.assertTrue(all("value" not in secret and "keyVaultUrl" in secret for secret in secrets))
        self.assertTrue(all(secret["identity"] == "[parameters('runtimeIdentity').id]" for secret in secrets))
        environment = template["variables"]["variables"]
        self.assertEqual(environment["PORTAL_SQL_AUTH"], "managed-identity")
        self.assertEqual(environment["PORTAL_SUBSCRIPTION_SYNC_ENABLED"], "true")
        self.assertEqual(environment["PORTAL_CANONICAL_SQL_SERVER"], "[parameters('sqlServer')]")
        self.assertEqual(environment["PORTAL_CANONICAL_SQL_DATABASE"], "[parameters('database')]")
        self.assertEqual(environment["AZURE_CLIENT_ID"], "[parameters('runtimeIdentity').clientId]")
        self.assertEqual(environment["MICROSOFT_TENANT_ID"], "[parameters('tenantId')]")
        self.assertEqual(environment["AZURE_TENANT_ID"], "[tenant().tenantId]")
        self.assertEqual(environment["PORTAL_APIM_AUTH"], "managed-identity")
        self.assertEqual(environment["PORTAL_APIM_RESOURCE_ID"], "[parameters('apimResourceId')]")
        self.assertEqual(environment["PORTAL_GATEWAY_URL"], "[parameters('gatewayOrigin')]")
        self.assertNotIn("PORTAL_GATEWAY_SUBSCRIPTION_KEY", environment)
        self.assertNotIn("PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE", environment)
        self.assertEqual(self.module("portal-application", "portal")["properties"]["parameters"]["gatewayOrigin"],
                         {"value": "[parameters('gatewayOrigin')]"})
        self.assertNotIn("PORTAL_API_KEY_GROUPS", environment)
        self.assertEqual(app["properties"]["configuration"]["ingress"]["targetPort"], 3000)

    def test_group_observation_is_scheduled_only_after_the_schema_phase(self):
        module = self.module("portal-group-reconciliation", "portal")
        self.assertEqual(module["condition"], "[parameters('deployApplication')]")
        resources = module["properties"]["template"]["resources"]
        job, = [item for item in resources if item["type"] == "Microsoft.App/jobs"]
        self.assertEqual(job["name"], "[format('caj-{0}-portal-groups', parameters('stem'))]")
        config = job["properties"]["configuration"]
        self.assertEqual(config["triggerType"], "Schedule")
        self.assertEqual(config["scheduleTriggerConfig"], {
            "cronExpression": "*/15 * * * *", "replicaCompletionCount": 1, "parallelism": 1})
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertEqual(config["replicaTimeout"], 300)
        secret, = config["secrets"]
        self.assertNotIn("value", secret)
        self.assertEqual(secret["keyVaultUrl"], "[parameters('clientSecretUri')]")
        self.assertEqual(secret["identity"], "[parameters('runtimeIdentity').id]")
        container, = job["properties"]["template"]["containers"]
        self.assertIn("scripts/reconcile-api-key-groups.ts", container["command"])
        environment = {item["name"]: item for item in container["env"]}
        self.assertEqual(environment["PORTAL_SQL_AUTH"]["value"], "managed-identity")
        self.assertEqual(environment["AZURE_CLIENT_ID"]["value"], "[parameters('runtimeIdentity').clientId]")
        self.assertNotIn("PORTAL_APIM_RESOURCE_ID", environment)
        self.assertFalse(any(item["type"].startswith("Microsoft.Authorization/") for item in resources))
        schema = self.module("portal-schema-job", "portal")["properties"]["template"]["variables"]["variables"]
        self.assertEqual(schema["PORTAL_API_KEY_PLATFORM_GROUP_ID"], "[parameters('apiKeyPlatformGroupId')]")
        self.assertEqual(schema["PORTAL_API_KEY_GROUP_NAME_PATTERN"], "[parameters('apiKeyGroupNamePattern')]")
        self.assertEqual(schema["PORTAL_API_KEY_CATALOG_PRODUCTS"], "[concat(' ', string(parameters('apiKeyCatalogProducts')))]")

    def test_api_key_management_uses_builtin_role_scoped_to_existing_apim(self):
        template = self.module("portal-api-keys", "portal")["properties"]["template"]
        roles, = template["resources"]
        self.assertEqual(roles["type"], "Microsoft.Authorization/roleAssignments")
        self.assertIn("Microsoft.ApiManagement/service", roles["scope"])
        self.assertIn("parameters('apimName')", roles["scope"])
        self.assertEqual(roles["properties"]["principalId"], "[parameters('runtimeIdentity').principalId]")
        self.assertIn("312a565d-c81f-4fd8-895a-4e21e48d571c", template["variables"]["contributor"])
        self.assertNotIn("Microsoft.Authorization/roleDefinitions", {item["type"] for item in template["resources"]})


if __name__ == "__main__":
    unittest.main()
