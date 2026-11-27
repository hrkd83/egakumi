"""Portal配備がDBを所有し、共通基盤を変更しないことを確認する。"""
from tests.support.bicep_helpers import compile_bicep
from tests.network.test_parameter_ip_ownership import NetworkArmExpression
from tests.waf.test_ssml_waf import GatewayExpression

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from jsonschema import Draft202012Validator
from tests import REPOSITORY


ROOT = REPOSITORY
from deployment.helpers.configuration import normalize_configuration, build_bicep_configuration, ConfigurationError
from deployment.helpers.portal_authentication import prepare_secret, remove_secret
from deployment.helpers.ingress import secret_path
from deployment.lifecycle import parameters
from tests.support.configuration_fixtures import fixture


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
        self.assertEqual(build_bicep_configuration(result)["portalAuthentication"], settings)

    def test_normalization_rejects_invalid_database_combinations(self):
        for key, value in (("sqlSkuName", "GP_Gen5_2"), ("sqlCapacity", 2),
                           ("sqlMaxSizeGB", 3), ("backupRetentionDays", 8),
                           ("zoneRedundant", True)):
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
            self.assertTrue(set(settings) <= {"sqlSkuName", "sqlTier", "sqlCapacity",
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
        self.root = Path(self.folder.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}))
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111",
                       "azureTenantId": "22222222-2222-4222-8222-222222222222",
                       "resourceStem": "test-fixture", "environmentName": "test"}

    def write_protected(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def test_signing_key_is_generated_once_and_is_separate_from_other_credentials(self):
        result = prepare_secret(self.config, self.root, self.write_protected)
        again = prepare_secret(self.config, self.root, lambda *_: self.fail("Existing key was overwritten"))
        self.assertEqual(result, again)
        self.assertRegex(result["portalAuthenticationSecret"], r"^[a-f0-9]{128}$")
        expected = self.root / ".local/test/secrets/portal-auth-test-fixture.json"
        self.assertEqual(secret_path(self.config, self.root, "portal-auth"), expected)
        self.assertTrue(expected.is_file())
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

    def test_portal_certificate_job_pulls_private_image_with_the_dedicated_identity(self):
        jobs = compile_bicep('infra/modules/certificates/certificate-jobs.bicep')
        resources = jobs['resources']
        if isinstance(resources, dict):
            resources = resources.values()
        job, = [resource for resource in resources if resource['type'] == 'Microsoft.App/jobs']
        parameters = jobs['parameters']
        self.assertEqual(parameters['accountEmail']['type'].casefold(), 'securestring')
        self.assertIn('imageDigest', parameters)
        self.assertIn('identitySettings', json.dumps(job['properties']['configuration']))
        self.assertIn('Manual', json.dumps(job['properties']['configuration']))
        self.assertIn('CERTIFICATE_MODE', json.dumps(job['properties']['template']))

    def test_dedicated_signer_has_only_public_read_and_sign_on_the_key_scope(self):
        resources = self.module('portal-auth-key-access', 'portal')['properties']['template']['resources']
        role = next(item for item in resources if item['type'].endswith('/roleDefinitions'))
        self.assertEqual(role['properties']['permissions'][0]['dataActions'], ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/sign/action'])
        assignments = [item for item in resources if item['type'].endswith('/roleAssignments')]
        self.assertEqual({item['properties']['principalId'] for item in assignments},
                         {"[parameters('authIdentity').principalId]"})
        self.assertTrue(all('Microsoft.KeyVault/vaults/keys' in item['scope'] for item in assignments))

    def test_certificate_worker_can_disable_a_portal_key_but_only_read_the_obo_key(self):
        storage = compile_bicep('infra/modules/certificates/certificate-storage.bicep')
        modules = storage['resources']
        if isinstance(modules, dict):
            modules = modules.values()
        key_module = next(item for item in modules if '-portal-job-key-operations' in item.get('name', ''))
        self.assertEqual(key_module['properties']['parameters']['principalId'], {'value': "[parameters('portalPrincipalId')]"})
        template = key_module['properties']['template']
        resources = template['resources']
        if isinstance(resources, dict):
            resources = resources.values()
        role = next(item for item in resources if item['type'].endswith('/roleDefinitions'))
        permission, = role['properties']['permissions']
        self.assertEqual(permission['actions'], [])
        granted = GatewayExpression(permission['dataActions'], template['variables'])
        self.assertIs(template['parameters']['allowDisable']['defaultValue'], True)
        self.assertEqual(granted.evaluate({'allowDisable': True}), [
            'Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/update/action'])
        obo = next(item for item in modules if '-gateway-obo-key-reader' in item.get('name', ''))
        self.assertEqual(obo['properties']['parameters']['keyName']['value'], 'gateway-obo')
        configured = obo['properties']['parameters']['allowDisable']['value']
        self.assertIs(configured, False)
        self.assertEqual(granted.evaluate({'allowDisable': configured}), ['Microsoft.KeyVault/vaults/keys/read'])
        assignment = next(item for item in resources if item['type'].endswith('/roleAssignments'))
        self.assertIn('Microsoft.KeyVault/vaults/keys', assignment['scope'])
        self.assertEqual(assignment['properties']['principalType'], 'ServicePrincipal')

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

    def test_secret_read_rights_are_scoped_to_each_workload(self):
        resources = self.module("portal-database", "portal")["properties"]["template"]["resources"]
        secrets = [item for item in resources if item["type"] == "Microsoft.KeyVault/vaults/secrets"]
        secret, = secrets
        self.assertIn("core-portal-auth-secret", secret["name"])
        self.assertEqual(secret["condition"], "[not(empty(parameters('signingSecret')))]")
        role, = [item for item in resources if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertIn("Microsoft.KeyVault/vaults/secrets", role["scope"])
        self.assertIn("core-portal-auth-secret", role["scope"])
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
        self.assertEqual(migration["name"], "[format('caj-{0}-psql', parameters('stem'))]")
        config = migration["properties"]["configuration"]
        self.assertEqual(config["triggerType"], "Manual")
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertEqual(config["manualTriggerConfig"]["parallelism"], 1)
        self.assertEqual(schema["variables"]["variables"]["AZURE_CLIENT_ID"], "[parameters('schemaIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_SQL_RUNTIME_CLIENT_ID"], "[parameters('runtimeIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_SQL_RUNTIME_USER_NAME"],
                         "[parameters('runtimeIdentity').name]")
        self.assertEqual(migration["identity"]["userAssignedIdentities"],
                         {"[format('{0}', parameters('schemaIdentity').id)]": {}})
        container = migration["properties"]["template"]["containers"][0]
        self.assertIn("scripts/migrate-auth.ts", container["command"])
        image_access, = [item for item in schema["resources"] if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(image_access["properties"]["principalId"], "[parameters('schemaIdentity').principalId]")
        self.assertIn("Microsoft.ContainerRegistry/registries", image_access["scope"])
        self.assertIn("7f951dda-4ed3-4680-a7ca-43fe172d538d", schema["variables"]["acrPull"])

    def test_portal_ingress_keeps_the_configured_long_request_budget(self):
        ingress = compile_bicep('infra/modules/ingress/public-ingress.bicep')
        resources = ingress['resources']
        if isinstance(resources, dict):
            resources = resources.values()
        gateway = next(item for item in resources if "{0}-gateway" in item.get('name', ''))
        self.assertEqual(gateway["properties"]["parameters"]["backendRequestTimeoutSeconds"],
                         {"value": "[parameters('gatewayPlan').backendRequestTimeoutSeconds]"})
        self.assertIn("variables('gatewayConfig').backendRequestTimeoutSeconds",
                      self.main['variables']['gatewayPlan']['backendRequestTimeoutSeconds'])
        template = gateway["properties"]["template"]
        app_gateway, = [item for item in template["resources"]
                        if item["type"] == "Microsoft.Network/applicationGateways"]
        properties = app_gateway["properties"]
        # Both HTTP paths must preserve the configured budget. Health probes keep
        # their shorter failure-detection timers and are not inference requests.
        self.assertEqual(properties.count("'requestTimeout', parameters('backendRequestTimeoutSeconds')"), 2)
        self.assertNotIn("'requestTimeout', 30", properties)
        self.assertIn("'timeout', 10", properties)

    def test_gateway_egress_only_allows_selected_gateway_from_aca(self):
        module = self.module("portal-gateway-egress", "portal")
        self.assertNotIn("condition", module)
        self.assertEqual(module["resourceGroup"], "[parameters('networkResourceGroup')]")
        rules = module["properties"]["template"]["resources"][0]
        self.assertNotIn("condition", rules)
        self.assertIn("rcg-portal-gateway", rules["name"])
        self.assertEqual(rules["properties"]["priority"], 370)
        collections = rules["properties"]["ruleCollections"]
        self.assertEqual(len(collections), 1)
        self.assertEqual(collections[0]["name"], "portal-gateway")
        self.assertEqual(collections[0]["priority"], 100)
        self.assertEqual(collections[0]["ruleCollectionType"], "FirewallPolicyFilterRuleCollection")
        self.assertEqual(collections[0]["action"], {"type": "Allow"})
        self.assertEqual(len(collections[0]["rules"]), 1)
        rule = collections[0]["rules"][0]
        self.assertEqual(rule["name"], "allow-aca-appgw-https")
        self.assertEqual(rule["ruleType"], "ApplicationRule")
        self.assertEqual(rule["sourceAddresses"], ["[parameters('acaPrefix')]"])
        self.assertEqual(rule["protocols"], [{"protocolType": "Https", "port": 443}])
        self.assertEqual(rule["targetFqdns"], ["[split(parameters('gatewayOrigin'), '/')[2]]"])
        self.assertFalse(rule["terminateTLS"])

    def test_application_uses_vault_references_and_managed_identity_sql(self):
        template = self.module("portal-application", "portal")["properties"]["template"]
        app, = [item for item in template["resources"] if item["type"] == "Microsoft.App/containerApps"]
        secrets = app["properties"]["configuration"]["secrets"]
        self.assertEqual(secrets, [{
            "name": "auth-signing-secret", "identity": "[parameters('runtimeIdentity').id]",
            "keyVaultUrl": "[parameters('signingSecretUri')]",
        }])
        environment = template["variables"]["variables"]
        for name, expression in {
            'PORTAL_SQL_AUTH': "'managed-identity'",
            'PORTAL_CANONICAL_SQL_SERVER': "parameters('sqlServer')", 'PORTAL_CANONICAL_SQL_DATABASE': "parameters('database')",
            'AZURE_CLIENT_ID': "parameters('runtimeIdentity').clientId", 'MICROSOFT_TENANT_ID': "parameters('tenantId')",
            'AZURE_TENANT_ID': 'tenant().tenantId', 'PORTAL_GATEWAY_URL': "parameters('gatewayOrigin')",
        }.items():
            self.assertIn("'" + name + "', " + expression, environment)
        certificate_variables = template['variables']['certificateVariables']
        self.assertEqual(certificate_variables["PORTAL_AUTH_KEY_ID"], "[parameters('authentication').keyId]")
        self.assertEqual(certificate_variables["PORTAL_AUTH_CERTIFICATE_THUMBPRINT"],
                         "[parameters('authentication').certificateThumbprint]")
        self.assertEqual(self.module("portal-application", "portal")["properties"]["parameters"]["gatewayOrigin"],
                         {"value": "[parameters('gatewayOrigin')]"})
        self.assertEqual(app["properties"]["configuration"]["ingress"]["targetPort"], 3000)

if __name__ == "__main__":
    unittest.main()
