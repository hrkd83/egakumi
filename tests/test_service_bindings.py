"""APIM・Foundry・ProjectのAzure RBAC割当を検証する。

Bicepコンパイル結果のrole ID・scope・principalを照合し、組み込みロールを必要な対象へ付ける構造を確認する。Entraの管理ロールは作成しない。
"""

from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]
LANGUAGE_READER = "7628b7b8-a8b2-4cdc-b46f-e9b35248918e"


class ServiceBindingDeploymentTests(unittest.TestCase):
    """APIM・Foundry・ProjectのAzure RBAC割当を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'bindings': compile_bicep('infra/modules/service-bindings.bicep'),
            'main': compile_bicep('infra/main.bicep'),
            'services': compile_bicep('infra/modules/services.bicep'),
            'gateway': compile_bicep('apps/gateway/deploy/main.bicep'),
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

    def test_management_vm_receives_only_certificate_and_secret_roles_at_the_target_vault(self):
        """証明書VMの付与先を実ARMで限定し、APIM管理権限の混入を拒否する。"""
        template = self.templates["bindings"]
        resources = template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        grants = [resource for resource in resources
                  if resource["type"] == "Microsoft.Authorization/roleAssignments"
                  and resource.get("properties", {}).get("principalId") == "[parameters('managementPrincipalId')]"]
        self.assertEqual(len(grants), 2)
        expected_roles = {
            "[variables('certificatesOfficerRoleId')]": "a4417e6f-fecd-4de8-b567-7b0420556985",
            "[variables('secretsOfficerRoleId')]": "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
        }
        self.assertEqual({grant["properties"]["roleDefinitionId"] for grant in grants}, set(expected_roles))
        for expression, role_id in expected_roles.items():
            variable = expression.split("'")[1]
            self.assertEqual(template["variables"][variable],
                             "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '" + role_id + "')]")
        for grant in grants:
            with self.subTest(role=grant["properties"]["roleDefinitionId"]):
                self.assertEqual(grant["scope"], "[resourceId('Microsoft.KeyVault/vaults', parameters('names').keyVault)]")
                self.assertEqual(grant["properties"]["principalType"], "ServicePrincipal")
                self.assertEqual(grant["condition"],
                                 "[and(parameters('grantManagementCertificateAccess'), not(empty(parameters('managementPrincipalId'))))]")

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
        walk(self.templates["gateway"])
        self.assertEqual(roles, [])
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


    def test_core_bindings_exclude_tool_host_roles_and_portal_owns_its_hosts(self):
        """必須PortalのACA/ACRを認め、外部ツールの運用権限とは分離する。"""
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
        self.assertEqual(hosts(self.templates["main"]), portal_hosts)
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

    def test_apim_control_plane_exception_keeps_vault_private_and_foundry_unchanged(self):
        """証明書の管理面例外だけを条件付きにし、通常の公開許可へ広げない。"""
        template = self.templates["services"]
        self.assertFalse(template["parameters"]["allowApimKeyVaultControlPlane"]["defaultValue"])
        vault, = [item for item in template["resources"] if item["type"] == "Microsoft.KeyVault/vaults"]
        # vault properties は purge 保護の条件を union するため、ARM 式へ展開される。
        properties = vault["properties"]
        self.assertIn("'publicNetworkAccess', 'Disabled'", properties)
        self.assertIn("'defaultAction', 'Deny'", properties)
        self.assertIn("'bypass', if(parameters('allowApimKeyVaultControlPlane'), 'AzureServices', 'None')", properties)
        self.assertIn("'ipRules', createArray()", properties)
        self.assertIn("'virtualNetworkRules', createArray()", properties)
        self.assertIn("'enableRbacAuthorization', true()", properties)
        foundry, = [item for item in template["resources"] if item["type"] == "Microsoft.CognitiveServices/accounts"]
        self.assertEqual(foundry["properties"]["publicNetworkAccess"], "Disabled")
        self.assertEqual(foundry["properties"]["networkAcls"]["bypass"], "None")
        services, = [item for item in self.templates["main"]["resources"]
                     if "allowApimKeyVaultControlPlane" in item.get("properties", {}).get("parameters", {})]
        self.assertEqual(services["properties"]["parameters"]["allowApimKeyVaultControlPlane"]["value"],
                         "[or(or(variables('servicesConfig').grantApimSecretAccess, variables('useIngressSecrets')), not(empty(parameters('configuration').credentialProfiles)))]")

    def test_apim_secret_reader_is_scoped_to_vault_and_appgw_keeps_its_own_identity(self):
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
