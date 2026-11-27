"""Portal配備がDBを所有し、共通基盤を変更しないことを確認する。"""
from bicep_helpers import compile_bicep
from test_parameter_ip_ownership import NetworkArmExpression
from test_ssml_waf import GatewayExpression

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from uuid import UUID

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
        jobs = compile_bicep('infra/certificate-jobs.bicep')
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
                         {"[parameters('authIdentity').principalId]", "[parameters('managementIdentity').principalId]"})
        self.assertTrue(all('Microsoft.KeyVault/vaults/keys' in item['scope'] for item in assignments))

    def test_certificate_worker_can_disable_a_portal_key_but_only_read_the_obo_key(self):
        storage = compile_bicep('infra/modules/certificate-storage.bicep')
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
        self.assertEqual(len(secrets), 3)
        for name, parameter in (("core-portal-auth-secret", "signingSecret"),
                                ("core-subscription-profile-key", "profileSecret"),
                                ("core-subscription-sync-key", "subscriptionSyncSecret")):
            secret, = [item for item in secrets if name in item["name"]]
            self.assertEqual(secret["condition"], "[not(empty(parameters('" + parameter + "')))]")
        roles = [item for item in resources if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(len(roles), 4)
        for secret, identity in (("core-portal-auth-secret", "runtimeIdentity"),
                                 ("core-credential-", "runtimeIdentity"),
                                 ("core-subscription-profile-key", "profileIdentity"),
                                 ("core-subscription-sync-key", "managementIdentity")):
            role, = [item for item in roles if secret in item["scope"]]
            self.assertIn("Microsoft.KeyVault/vaults/secrets", role["scope"])
            self.assertEqual(role["properties"]["principalId"], f"[parameters('{identity}').principalId]")
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
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_GROUPS"], "[concat(' ', string(parameters('apiKeyGroups')))]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_TENANT_ID"], "[parameters('apiKeyTenantId')]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS"], "[concat(' ', string(parameters('apiKeyVerificationSubscriptions')))]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_API_KEY_APIM_CLIENT_ID"], "[parameters('managementIdentity').clientId]")
        self.assertEqual(schema["variables"]["variables"]["PORTAL_APIM_RESOURCE_ID"], "[parameters('apimResourceId')]")
        self.assertIn("parameters('managementIdentity').id", json.dumps(migration["identity"]))
        self.assertNotIn("parameters('runtimeIdentity').id", json.dumps(migration["identity"]))
        for role in ('management', 'profile'):
            for suffix, property_name in (('CLIENT_ID', 'clientId'), ('USER_NAME', 'name')):
                self.assertEqual(schema["variables"]["variables"][f'PORTAL_SQL_{role.upper()}_{suffix}'],
                                 f"[parameters('{role}Identity').{property_name}]")
        container = migration["properties"]["template"]["containers"][0]
        self.assertIn("scripts/migrate-auth.ts", container["command"])
        image_access, = [item for item in schema["resources"] if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(image_access["properties"]["principalId"], "[parameters('schemaIdentity').principalId]")
        self.assertIn("Microsoft.ContainerRegistry/registries", image_access["scope"])
        self.assertIn("7f951dda-4ed3-4680-a7ca-43fe172d538d", schema["variables"]["acrPull"])

    def test_portal_ingress_keeps_the_configured_long_request_budget(self):
        ingress = compile_bicep('infra/public-ingress.bicep')
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
        self.assertIsInstance(secrets, str)
        self.assertIn("if(empty(parameters('authentication'))", secrets)
        self.assertIn("auth-signing-secret", secrets)
        self.assertNotIn("subscription-profile-secret", secrets)
        self.assertNotIn("subscription-sync-key", secrets)
        self.assertIn("microsoft-client-secret", secrets)
        self.assertIn("parameters('runtimeIdentity').id", secrets)
        environment = template["variables"]["variables"]
        for name, expression in {
            'PORTAL_SQL_AUTH': "'managed-identity'",
            'PORTAL_CANONICAL_SQL_SERVER': "parameters('sqlServer')", 'PORTAL_CANONICAL_SQL_DATABASE': "parameters('database')",
            'AZURE_CLIENT_ID': "parameters('runtimeIdentity').clientId", 'MICROSOFT_TENANT_ID': "parameters('tenantId')",
            'AZURE_TENANT_ID': 'tenant().tenantId', 'PORTAL_GATEWAY_URL': "parameters('gatewayOrigin')",
            'PORTAL_SUBSCRIPTION_MANAGER_URL': "format('https://{0}', parameters('subscriptionManagerFqdn'))",
            'PORTAL_SUBSCRIPTION_MANAGEMENT_SCOPE': "format('api://{0}/Subscription.Manage', parameters('subscriptionServiceAudience'))",
        }.items():
            self.assertIn("'" + name + "', " + expression, environment)
        certificate_variables = template['variables']['certificateVariables']
        self.assertIn("'PORTAL_AUTH_KEY_ID', parameters('authentication').keyId", certificate_variables)
        self.assertIn("'PORTAL_AUTH_CERTIFICATE_THUMBPRINT', parameters('authentication').certificateThumbprint", certificate_variables)
        self.assertNotIn("PORTAL_GATEWAY_SUBSCRIPTION_KEY", environment)
        self.assertNotIn("PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE", environment)
        self.assertNotIn("PORTAL_APIM_RESOURCE_ID", environment)
        self.assertNotIn("PORTAL_SUBSCRIPTION_SYNC_ENABLED", environment)
        self.assertEqual(self.module("portal-application", "portal")["properties"]["parameters"]["gatewayOrigin"],
                         {"value": "[parameters('gatewayOrigin')]"})
        self.assertNotIn("PORTAL_API_KEY_GROUPS", environment)
        self.assertEqual(app["properties"]["configuration"]["ingress"]["targetPort"], 3000)

    def test_group_observation_is_scheduled_only_after_the_schema_phase(self):
        module = self.module("portal-group-reconciliation", "portal")
        self.assertEqual(module["condition"], "[parameters('deployApplication')]")
        resources = module["properties"]["template"]["resources"]
        job, = [item for item in resources if item["type"] == "Microsoft.App/jobs"]
        self.assertEqual(job["name"], "[format('caj-{0}-psync', parameters('stem'))]")
        config = job["properties"]["configuration"]
        self.assertEqual(config["triggerType"], "Schedule")
        self.assertEqual(config["scheduleTriggerConfig"], {
            "cronExpression": "*/15 * * * *", "replicaCompletionCount": 1, "parallelism": 1})
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertEqual(config["replicaTimeout"], 300)
        self.assertNotIn('secrets', config)
        container, = job["properties"]["template"]["containers"]
        self.assertIn("scripts/reconcile-api-key-groups.ts", container["command"])
        environment = {item['name']: item['value'] for item in container['env']}
        self.assertEqual(environment['PORTAL_SQL_AUTH'], 'managed-identity')
        self.assertEqual(environment['AZURE_CLIENT_ID'], "[parameters('managementIdentity').clientId]")
        self.assertEqual(environment['PORTAL_AUTH_IDENTITY_CLIENT_ID'], "[parameters('authIdentity').clientId]")
        self.assertEqual(environment['PORTAL_AUTH_SIGNING_MODE'], 'managed-identity')
        self.assertIn("PORTAL_AUTH_KEY_ID", environment)
        self.assertIn("PORTAL_AUTH_CERTIFICATE_THUMBPRINT", environment)
        self.assertNotIn("PORTAL_APIM_RESOURCE_ID", environment)
        self.assertFalse(any(item["type"].startswith("Microsoft.Authorization/") for item in resources))
        schema = self.module("portal-schema-job", "portal")["properties"]["template"]["variables"]["variables"]
        self.assertEqual(schema["PORTAL_API_KEY_PLATFORM_GROUP_ID"], "[parameters('apiKeyPlatformGroupId')]")
        self.assertEqual(schema["PORTAL_API_KEY_GROUP_NAME_PATTERN"], "[parameters('apiKeyGroupNamePattern')]")
        self.assertEqual(schema["PORTAL_API_KEY_CATALOG_PRODUCTS"], "[concat(' ', string(parameters('apiKeyCatalogProducts')))]")

    def test_api_key_management_grants_only_the_six_needed_actions_to_its_identity(self):
        template = self.module("portal-api-keys", "portal")["properties"]["template"]
        definition, = [item for item in template['resources'] if item['type'].endswith('/roleDefinitions')]
        assignment, = [item for item in template['resources'] if item['type'].endswith('/roleAssignments')]
        permission, = definition['properties']['permissions']
        self.assertEqual(set(permission['actions']), {
            'Microsoft.ApiManagement/service/subscriptions/read',
            'Microsoft.ApiManagement/service/subscriptions/write',
            'Microsoft.ApiManagement/service/subscriptions/delete',
            'Microsoft.ApiManagement/service/subscriptions/listSecrets/action',
            'Microsoft.ApiManagement/service/products/read',
            'Microsoft.ApiManagement/service/apis/products/read',
        })
        self.assertEqual(permission['dataActions'], [])
        self.assertIn("Microsoft.ApiManagement/service", assignment['scope'])
        self.assertIn("parameters('apimName')", assignment['scope'])
        self.assertEqual(assignment['properties']['principalId'], "[parameters('managementIdentity').principalId]")

    def test_subscription_services_expose_only_their_intended_private_ingress(self):
        module = self.module('subscription-services', 'portal')
        self.assertEqual(module['condition'], "[parameters('deployApplication')]")
        template = module['properties']['template']
        app, = template['resources']
        self.assertEqual(app['type'], 'Microsoft.App/containerApps')
        self.assertEqual(app['properties']['environmentId'],
                         "[resourceId('Microsoft.App/managedEnvironments', parameters('environmentName'))]")
        subnet = '192.0.2.0/26'
        management_client, profile_client, portal_client = [str(UUID(int=value)) for value in range(1, 4)]
        parameters = {'apimSubnetPrefix': subnet, 'stem': 'fixture-long-name',
                      'managementIdentity': {'id': '/synthetic/management', 'clientId': management_client},
                      'profileIdentity': {'id': '/synthetic/profile', 'clientId': profile_client},
                      'subscriptionSyncSecretUri': 'https://vault.example.com/secrets/sync',
                      'profileSecretUri': 'https://vault.example.com/secrets/profile',
                      'publicOrigin': 'https://portal.example.com', 'clientId': portal_client,
                      'apimResourceId': '/synthetic/apim', 'subscriptionServiceAudience': 'subscription-api',
                      'authentication': {'keyId': 'certificate-key', 'certificateThumbprint': 'key-thumbprint'}}
        configuration = app['properties']['configuration']
        paths = NetworkArmExpression(configuration['ingress'], template)
        for index, expected in enumerate(({'external': False}, {'external': True, 'ipSecurityRestrictions': [
                {'name': 'apim', 'action': 'Allow', 'ipAddressRange': subnet}]})):
            self.assertEqual(paths.evaluate(parameters, index),
                             {'targetPort': 3000, 'transport': 'http', 'allowInsecure': False, **expected})
            name = NetworkArmExpression(app['name'], template).evaluate(parameters, index)
            self.assertLessEqual(len(name), 32)
            self.assertTrue(name.endswith(('-sub-admin', '-sub-profile')[index]))
            command = app['properties']['template']['containers'][0]['command']
            launcher = NetworkArmExpression(command[-1], template).evaluate(parameters, index)
            self.assertEqual(launcher, ('../subscription-admin/server.mjs', '../subscription-profile/server.mjs')[index])
        management, profile = template['variables']['serviceAccounts']
        self.assertEqual(management['identity'], "[parameters('managementIdentity')]")
        self.assertEqual(management['secretEnv'], 'BETTER_AUTH_SECRET')
        self.assertEqual(management['secretUri'], "[parameters('subscriptionSyncSecretUri')]")
        self.assertEqual(profile['identity'], "[parameters('profileIdentity')]")
        self.assertEqual(profile['secretUri'], "[parameters('profileSecretUri')]")
        self.assertEqual(profile['env'], {})

    def test_apim_subscription_profile_named_values_use_only_the_profile_service(self):
        module = self.module('portal-subscription-profile', 'portal')
        self.assertEqual(module['condition'], "[parameters('deployApplication')]")
        connection = module['properties']['parameters']['profileFqdn']['value']
        self.assertIn('subscription-services', connection)
        self.assertIn('outputs.profileFqdn.value', connection)
        resources = module['properties']['template']['resources']
        role, = [item for item in resources if item['type'].endswith('/roleAssignments')]
        self.assertIn('core-subscription-profile-key', role['scope'])
        self.assertIn("parameters('apimName')", role['properties']['principalId'])
        values = {item['properties']['displayName']: item['properties'] for item in resources
                  if item['type'].endswith('/namedValues')}
        self.assertEqual(values['core-subscription-profile-url']['value'],
                         "[format('https://{0}/api/internal/subscription-profile', parameters('profileFqdn'))]")
        self.assertEqual(values['core-subscription-profile-secret']['keyVault'],
                         {'secretIdentifier': "[parameters('profileSecretUri')]"})


if __name__ == "__main__":
    unittest.main()
