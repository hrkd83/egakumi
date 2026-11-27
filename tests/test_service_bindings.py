"""APIM・Foundry・ProjectのAzure RBAC割当を検証する。

Bicepコンパイル結果のrole ID・scope・principalを照合し、組み込みロールを必要な対象へ付ける構造を確認する。Entraの管理ロールは作成しない。
"""

from bicep_helpers import compile_bicep

from pathlib import Path
import unittest
from uuid import UUID

from configuration_fixtures import fixture
from deployment.helpers import diagnostics
from deployment.helpers.configuration import normalize_configuration
from test_parameter_ip_ownership import NetworkArmExpression


CORE = Path(__file__).resolve().parents[1]
LANGUAGE_READER = "7628b7b8-a8b2-4cdc-b46f-e9b35248918e"


class ServiceBindingDeploymentTests(unittest.TestCase):
    """APIM・Foundry・ProjectのAzure RBAC割当を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'bindings': compile_bicep('infra/modules/services/service-bindings.bicep'),
            'main': compile_bicep('infra/main.bicep'),
            'services': compile_bicep('infra/modules/services/services.bicep'),
            'public_ingress': compile_bicep('infra/modules/ingress/public-ingress.bicep'),
            'certificate_storage': compile_bicep('infra/modules/certificates/certificate-storage.bicep'),
            'registry': compile_bicep('infra/modules/container-apps/portal-registry.bicep'),
            'gateway': compile_bicep('apps/apim/deploy/main.bicep'),
        }

    def test_pii_uses_builtin_language_reader_at_exact_foundry_account_for_apim(self):
        template = self.templates["bindings"]
        resources = template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        self.assertIn(LANGUAGE_READER, template["variables"]["languageReaderRoleId"])
        grants = [resource for resource in resources if resource.get("properties", {}).get("roleDefinitionId") == "[variables('languageReaderRoleId')]"]
        self.assertEqual(len(grants), 1)
        grant = grants[0]
        self.assertEqual(grant["type"], "Microsoft.Authorization/roleAssignments")
        self.assertEqual(grant["scope"], "[resourceId('Microsoft.CognitiveServices/accounts', parameters('names').foundry)]")
        self.assertEqual(grant["properties"]["principalId"], "[parameters('principalIds').apim]")
        self.assertEqual(grant["properties"]["principalType"], "ServicePrincipal")

    def test_certificate_worker_is_scoped_to_the_isolated_vault(self):
        template = self.templates["certificate_storage"]
        self.assertNotIn("managementPrincipalId", template["parameters"])
        self.assertNotIn("managementPrincipalId", self.templates["bindings"]["parameters"])
        grants = [resource for resource in template["resources"]
                  if resource["type"] == "Microsoft.Authorization/roleAssignments"
                  and resource.get("properties", {}).get("principalId") == "[parameters('tlsPrincipalId')]"
                  and "Microsoft.KeyVault/vaults" in resource.get("scope", "")]
        self.assertEqual(len(grants), 1)
        self.assertIn("resourceId('Microsoft.KeyVault/vaults'", grants[0]["scope"])
        self.assertIn("kvtls-", grants[0]["scope"])
        self.assertEqual(grants[0]["properties"]["principalType"], "ServicePrincipal")

    def test_public_tls_and_portal_cannot_write_each_others_receipts(self):
        grants = [row for row in self.templates["certificate_storage"]["resources"]
                  if row["type"] == "Microsoft.Authorization/roleAssignments"
                  and "certificate-state-blob-operator" in row["properties"]["roleDefinitionId"]]
        self.assertEqual(len(grants), 2)
        self.assertTrue(all("storageAccounts/blobServices/containers" in row["scope"] for row in grants))
        scopes = {row["properties"]["principalId"]: row["scope"] for row in grants}
        self.assertIn("'certificate-jobs'", scopes["[parameters('tlsPrincipalId')]"])
        self.assertNotIn("'portal-certificate-state'", scopes["[parameters('tlsPrincipalId')]"])
        self.assertIn("'portal-certificate-state'", scopes["[parameters('portalPrincipalId')]"])
        self.assertNotIn("'certificate-jobs'", scopes["[parameters('portalPrincipalId')]"])

    def test_private_registry_preserves_restrictions_with_managed_identity_pull(self):
        registry, = [row for row in self.templates["registry"]["resources"]
                     if row["type"] == "Microsoft.ContainerRegistry/registries"]
        self.assertEqual(registry["apiVersion"], "2025-11-01")
        self.assertEqual(registry["sku"]["name"], "Premium")
        self.assertEqual(registry["properties"], {
            "adminUserEnabled": False,
            "publicNetworkAccess": "Disabled",
            "networkRuleBypassOptions": "None",
            "networkRuleBypassAllowedForTasks": False,
            "dataEndpointEnabled": True,
            "anonymousPullEnabled": False,
            "roleAssignmentMode": "LegacyRegistryPermissions",
            "policies": {"azureADAuthenticationAsArmPolicy": {"status": "enabled"}},
        })

    def test_cm_uses_builtin_apim_contributor_only_at_the_provider_scope(self):
        roles = []
        login_templates = []
        def walk(value):
            if isinstance(value, dict):
                if value.get("type") == "Microsoft.Authorization/roleDefinitions":
                    roles.append(value)
                if "apiManagementContributorRoleId" in value.get("variables", {}):
                    login_templates.append(value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(self.templates["main"])
        self.assertEqual(login_templates, [])
        # Coreの保守用カスタムロールと、CM用provider権限を分けて確認する。
        roles.clear()
        walk(self.templates["gateway"])
        signer, = roles
        self.assertEqual(signer["condition"], "[not(empty(parameters('oboKeyName')))]")
        permission, = signer["properties"]["permissions"]
        self.assertEqual(permission["actions"], [])
        self.assertEqual(permission["dataActions"], ["Microsoft.KeyVault/vaults/keys/sign/action"])
        # APIMがトークンを取得し、brokerが接続を作成するため、それぞれ同じ限定ロールを使います。
        self.assertEqual(len(login_templates), 2)
        self.assertEqual(login_templates[0], login_templates[1])
        template = login_templates[0]
        self.assertEqual(template["variables"]["apiManagementContributorRoleId"],
                         "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '312a565d-c81f-4fd8-895a-4e21e48d571c')]")
        self.assertEqual(len(template["resources"]), 1)
        grant = template["resources"][0]
        self.assertEqual(grant["type"], "Microsoft.Authorization/roleAssignments")
        provider_scope = "resourceId('Microsoft.ApiManagement/service/authorizationProviders', parameters('apimName'), parameters('providerName'))"
        self.assertEqual(grant["scope"], "[" + provider_scope + "]")
        self.assertEqual(grant["name"], "[guid(" + provider_scope + ", parameters('apimPrincipalId'), variables('apiManagementContributorRoleId'))]")
        self.assertEqual(grant["properties"], {
            "roleDefinitionId": "[variables('apiManagementContributorRoleId')]",
            "principalId": "[parameters('apimPrincipalId')]",
            "principalType": "ServicePrincipal"})
        self.assertNotIn("piiRoleDefinitionId", self.templates["main"]["outputs"]["cleanup"]["value"])
        self.assertNotIn("piiRoleDefinitionResourceId", self.templates["bindings"].get("outputs", {}))


    def test_core_bindings_exclude_tool_host_roles_and_provision_only_owned_app_platforms(self):
        """共用ACRと各専用ACA環境をCore、Broker本体はGatewayで構築する。"""
        template = self.templates["bindings"]
        self.assertNotIn("containerAppOperators", template["parameters"])
        self.assertNotIn("containerAppsOperatorRoleId", template.get("variables", {}))
        self.assertNotIn("containerAppOperators", self.templates["main"]["parameters"])

        def hosts(value):
            result = []
            if isinstance(value, dict):
                if value.get("type", "").startswith(("Microsoft.App/", "Microsoft.ContainerRegistry/")):
                    result.append((value["type"], value.get("name")))
                for item in value.values():
                    result.extend(hosts(item))
            elif isinstance(value, list):
                for item in value:
                    result.extend(hosts(item))
            return sorted(result)

        self.assertEqual(hosts(template), [])
        portal_modules = [resource for resource in self.templates["main"]["resources"]
                          if resource.get("name") in {
                              "[format('{0}-portal', variables('stem'))]",
                              "[format('{0}-portal-registry', variables('stem'))]",
                          }]
        self.assertEqual(len(portal_modules), 2)
        portal_hosts = hosts(portal_modules)
        self.assertEqual({resource_type for resource_type, _ in portal_hosts}, {
            "Microsoft.App/containerApps",
            "Microsoft.App/managedEnvironments",
            "Microsoft.ContainerRegistry/registries",
        })
        dedicated_modules = {
            "certificate-environment": "certificate",
            "broker-platform": "broker",
            "audit-evidence-environment": "audit",
        }
        dedicated_hosts = []
        for module_name, environment_name in dedicated_modules.items():
            with self.subTest(platform=environment_name):
                dedicated, = [
                    resource for resource in self.templates["main"]["resources"]
                    if resource.get("name") == f"[format('{{0}}-{module_name}', variables('stem'))]"
                ]
                expected = ("Microsoft.App/managedEnvironments",
                            f"[format('cae-{{0}}-{environment_name}', parameters('stem'))]")
                self.assertEqual(hosts(dedicated), [expected])
                dedicated_hosts.append(expected)
        self.assertEqual(hosts(self.templates["main"]), sorted(portal_hosts + dedicated_hosts))
        self.assertIn(("Microsoft.App/containerApps", "[format('ca-{0}-credentials', parameters('stem'))]"),
                      hosts(self.templates["gateway"]))

    def test_api_center_source_uses_workspace_relative_environment_and_waits_for_it(self):
        """連携先の環境は API Center 内の相対パスとし、環境作成より先に登録しない。"""
        resources = self.templates["bindings"]["resources"]
        environment, = [item for item in resources
                        if item["type"] == "Microsoft.ApiCenter/services/workspaces/environments"]
        source, = [item for item in resources
                   if item["type"] == "Microsoft.ApiCenter/services/workspaces/apiSources"]
        self.assertEqual(source["properties"]["targetEnvironmentId"],
                         "[format('/workspaces/{0}/environments/{1}', 'default', 'core-gateway')]")
        # 環境名の参照で生まれる依存関係も確認し、初回構築の順序を維持する。
        environment_id = "[resourceId('Microsoft.ApiCenter/services/workspaces/environments', parameters('names').apiCenter, 'default', 'core-gateway')]"
        self.assertIn(environment_id, source["dependsOn"])
        self.assertEqual(environment["condition"], source["condition"])
        self.assertEqual(source["properties"]["azureApiManagementSource"], {
            "resourceId": "[resourceId('Microsoft.ApiManagement/service', parameters('names').apim)]",
        })

    def test_api_center_source_sends_official_cli_discriminator_without_changing_target_scope(self):
        """同じsourceを更新し、CLI契約の種別と現在選択したAPIMだけを送る。"""
        template = self.templates["bindings"]
        sources = [item for item in template["resources"]
                   if item["type"] == "Microsoft.ApiCenter/services/workspaces/apiSources"]
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source["apiVersion"], "2024-06-01-preview")
        self.assertEqual(source["name"],
                         "[format('{0}/{1}/{2}', parameters('names').apiCenter, 'default', 'core-apim')]")
        self.assertEqual(source["condition"], "[parameters('enableApiCenterIntegration')]")
        self.assertEqual(source["properties"], {
            "apiSourceType": "AzureApiManagement",
            "azureApiManagementSource": {
                "resourceId": "[resourceId('Microsoft.ApiManagement/service', parameters('names').apim)]",
            },
            "importSpecification": "always",
            "targetEnvironmentId": "[format('/workspaces/{0}/environments/{1}', 'default', 'core-gateway')]",
            "targetLifecycleStage": "[parameters('catalogLifecycleStage')]",
        })
        # GETで見える旧互換フィールドや読取専用の同期状態を推測してPUTへ加えない。
        self.assertNotIn("apimSource", source["properties"])
        self.assertNotIn("linkState", source["properties"])
        # API CenterとAPIMは同じsvc配置先。別RGの新API Centerは作らない。
        modules = self.templates["main"]["resources"]
        binding, = [item for item in modules
                    if "enableApiCenterIntegration" in item.get("properties", {}).get("parameters", {})]
        self.assertEqual(binding["resourceGroup"], "[format('rg-{0}-svc', variables('stem'))]")
        self.assertEqual(binding["properties"]["parameters"]["names"]["value"], "[variables('serviceNames')]")

    def test_application_and_public_tls_vaults_remain_separate_and_private(self):
        """両Vaultの公開アクセスを無効にし、管理面例外とRBACの設定を確認する。"""
        services_template = self.templates["services"]
        tls_template = self.templates["certificate_storage"]
        self.assertFalse(services_template["parameters"]["allowApimKeyVaultControlPlane"]["defaultValue"])
        self.assertNotIn("allowApimKeyVaultControlPlane", tls_template["parameters"])
        options = {'tenantId': str(UUID(int=1)), 'vaultSkuName': 'standard', 'vaultRetentionDays': 90,
                   'vaultPurgeProtection': True}
        for template, name in (
            (services_template, "[parameters('names').keyVault]"),
            (tls_template, "[format('kvtls-{0}', parameters('stem'))]"),
        ):
            vault, = [item for item in template["resources"] if item["type"] == "Microsoft.KeyVault/vaults"]
            self.assertEqual(vault["name"], name)
            properties = NetworkArmExpression(vault["properties"], template).evaluate(
                {**options, "allowApimKeyVaultControlPlane": True})
            self.assertEqual(properties['publicNetworkAccess'], 'Disabled')
            self.assertEqual(properties['networkAcls'], {
                'defaultAction': 'Deny', 'bypass': 'AzureServices', 'ipRules': [], 'virtualNetworkRules': []})
            self.assertTrue(properties['enableRbacAuthorization'])
            self.assertTrue(properties['enablePurgeProtection'])
        foundry, = [item for item in services_template["resources"] if item["type"] == "Microsoft.CognitiveServices/accounts"]
        self.assertEqual(foundry["properties"]["publicNetworkAccess"], "Disabled")
        self.assertEqual(foundry["properties"]["networkAcls"]["bypass"], "None")
        services, = [item for item in self.templates["main"]["resources"]
                     if "allowApimKeyVaultControlPlane" in item.get("properties", {}).get("parameters", {})]
        self.assertEqual(services["properties"]["parameters"]["allowApimKeyVaultControlPlane"]["value"],
                         True)

    def test_public_tls_vault_delivers_audit_events_to_the_existing_sinks(self):
        template = self.templates['certificate_storage']
        config = normalize_configuration(fixture())
        destinations = diagnostics.canonical_settings(config, 'core')['vault-logs']['properties']
        setting, = [item for item in template['resources'] if item['type'] == 'Microsoft.Insights/diagnosticSettings']
        self.assertEqual(setting['scope'], "[resourceId('Microsoft.KeyVault/vaults', format('kvtls-{0}', parameters('stem')))]")
        self.assertEqual(setting['name'], 'core-audit')
        self.assertEqual(setting['properties'], "[parameters('diagnosticSettingProperties')['vault-logs']]")
        self.assertEqual(destinations['logs'], [{'category': 'AuditEvent', 'enabled': True}])
        self.assertIn('/workspaces/', destinations['workspaceId'])
        self.assertIn('/storageAccounts/', destinations['storageAccountId'])

    def test_apim_and_appgw_use_distinct_managed_identities(self):
        """コンパイル済みの RBAC と本体を照合し、APIM と AppGW の ID を混同しない。"""
        bindings = self.templates["bindings"]
        resources = bindings["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        grants = [item for item in resources
            if item.get("properties", {}).get("roleDefinitionId") == "[variables('secretsUserRoleId')]"
            and item.get("properties", {}).get("principalId") == "[parameters('principalIds').apim]"]
        self.assertEqual(len(grants), 1)
        self.assertIn("4633458b-17de-408a-b874-0445c86b69e6", bindings["variables"]["secretsUserRoleId"])
        self.assertEqual(grants[0]["scope"], "[resourceId('Microsoft.KeyVault/vaults', parameters('names').keyVault)]")
        self.assertEqual(grants[0]["properties"]["principalType"], "ServicePrincipal")
        ingress = self.templates["public_ingress"]
        certificate_reader, = [item for item in ingress["resources"]
                               if item.get("name") == "[format('{0}-public-certificate-reader', parameters('gatewayPlan').resourceStem)]"]
        reader_parameters = certificate_reader["properties"]["parameters"]
        self.assertEqual(reader_parameters["vaultName"]["value"], "[parameters('gatewayPlan').certificateVaultName]")
        self.assertEqual(reader_parameters["certificateName"]["value"], "[parameters('gatewayPlan').certificateName]")
        self.assertEqual(reader_parameters["principalId"]["value"], "[parameters('gatewayPlan').certificateReaderPrincipalId]")
        certificate_grant, = certificate_reader["properties"]["template"]["resources"]
        self.assertEqual(certificate_grant["scope"],
                         "[resourceId('Microsoft.KeyVault/vaults/secrets', parameters('vaultName'), parameters('certificateName'))]")
        self.assertEqual(certificate_grant["properties"]["principalId"], "[parameters('principalId')]")

        found = []
        def walk(value):
            if isinstance(value, dict):
                if value.get("type") in ("Microsoft.ApiManagement/service", "Microsoft.Network/applicationGateways"):
                    found.append(value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(self.templates["main"])
        walk(ingress)
        # Bicep の existing APIM 参照も type を持つ。実際に identity を配備する本体を対象にする。
        apim = [item for item in found if item["type"] == "Microsoft.ApiManagement/service" and "identity" in item]
        gateways = [item for item in found if item["type"] == "Microsoft.Network/applicationGateways"]
        self.assertEqual(len(apim), 1)
        self.assertEqual(apim[0]["identity"], {"type": "SystemAssigned"})
        self.assertEqual(len(gateways), 1)
        self.assertEqual(gateways[0]["identity"]["type"], "UserAssigned")
        self.assertTrue(gateways[0]["identity"]["userAssignedIdentities"])


if __name__ == "__main__":
    unittest.main()
