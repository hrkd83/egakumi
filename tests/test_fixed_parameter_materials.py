"""初期資材の固定値と、構築資源からの導出を実コンパイル結果で確認する。

合成入力だけを使うオフライン試験。Azureの受入・疎通は検証しない。
"""
import copy
import json
import unittest

from arm_helpers import PolicyExpression
from bicep_helpers import compile_bicep
from test_ssml_waf import GatewayExpression


class MaterialExpression(PolicyExpression):
    """資材の値を読むために必要なARM関数だけを追加する。"""

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == "call":
            _, name, arguments = tree
            if name in {"shallowMerge", "toLower", "concat", "resourceId", "subscriptionResourceId"}:
                values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
                if name == "shallowMerge":
                    return {key: value for item in values[0] for key, value in item.items()}
                if name == "toLower":
                    return values[0].lower()
                if name == "concat":
                    return ([item for group in values for item in group]
                            if isinstance(values[0], list) else "".join(values))
                return "/synthetic/" + "/".join(values)
        return super().evaluate(parameters, index, tree, bindings)


def resources(template):
    collection = template["resources"]
    return list(collection.values()) if isinstance(collection, dict) else collection


def expression(template):
    return MaterialExpression("[true()]", template.get("variables", {}),
                              functions=template.get("functions", []))


class FixedParameterMaterialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {name: compile_bicep(path) for name, path in {
            "network": "infra/modules/network.bicep",
            "monitoring": "infra/modules/monitoring.bicep",
            "gateway": "infra/modules/application-gateway.bicep",
            "bindings": "infra/modules/service-bindings.bicep",
            "main": "infra/main.bicep",
        }.items()}

    def test_firewall_uses_generated_idps_ranges_and_keeps_snat_ranges(self):
        template = self.templates["network"]
        policy, = [item for item in resources(template)
                   if item["type"] == "Microsoft.Network/firewallPolicies"]
        app_gateway = "192.0.2.0/24"
        spaces = ["10.0.0.0/8", app_gateway, "198.51.100.0/24"]
        generated = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "198.51.100.0/24"]
        parameters = {"firewallTlsInspection": {"managedIdentityResourceId": "/synthetic/uami", "intermediateCaSecretId": "https://fixture.example.com/secrets/ca"},
                      "config": {"addressSpaces": spaces, "subnetPrefixes": {"agw": app_gateway},
                                 "firewall": {"sku": "Premium", "idpsPrivateRanges": ["0.0.0.0/0"]}}}
        with self.assertRaisesRegex(KeyError, "generatedIdpsPrivateRanges"):
            expression(template).resolve(policy["properties"], parameters)
        parameters["config"]["generatedIdpsPrivateRanges"] = generated
        actual = expression(template).resolve(policy["properties"], parameters)
        self.assertEqual(actual["snat"]["privateRanges"], ["IANAPrivateRanges", *spaces])
        self.assertEqual(actual["intrusionDetection"]["configuration"], {
            "privateRanges": generated,
            "bypassTrafficSettings": [],
        })
        self.assertEqual(actual["dnsSettings"], {"enableProxy": True, "servers": []})

    def test_premium_firewall_uses_reviewed_signatures_and_the_inspection_ca(self):
        template = self.templates["network"]
        policy, = [item for item in resources(template)
                   if item["type"] == "Microsoft.Network/firewallPolicies"]
        overrides = [{"id": "2030000", "mode": "Alert"}]
        ca = {"managedIdentityResourceId": "/synthetic/uami", "intermediateCaSecretId": "https://fixture.example.com/secrets/ca"}
        parameters = {"firewallTlsInspection": ca, "config": {
            "generatedIdpsPrivateRanges": ["192.0.2.0/24"],
            "firewall": {"signatureOverrides": overrides},
        }}
        actual = expression(template).resolve(policy["properties"], parameters)
        self.assertEqual(actual["sku"]["tier"], "Premium")
        self.assertEqual(actual["intrusionDetection"]["mode"], "Deny")
        self.assertEqual(actual["intrusionDetection"]["configuration"]["signatureOverrides"], overrides)
        self.assertEqual(actual["transportSecurity"]["certificateAuthority"]["keyVaultSecretId"], ca["intermediateCaSecretId"])

    def test_both_audit_storage_accounts_fix_empty_public_ip_rules_and_keep_diagnostic_ingestion(self):
        template = self.templates["monitoring"]
        self.assertNotIn("auditAllowedIpAddresses", template["parameters"])
        account, = [item for item in resources(template)
                    if item["type"] == "Microsoft.Storage/storageAccounts"]
        parameters = {"resourceStem": "synthetic-test", "location": "japaneast",
                      "foundryLocation": "eastus", "auditStorageSku": "Standard_GRS"}
        reader = expression(template)
        self.assertEqual(reader.resolve(account["copy"]["count"], parameters), 2)
        for index in range(2):
            properties = reader.resolve(account["properties"], parameters, index)
            self.assertEqual(properties["networkAcls"], {
                "defaultAction": "Deny", "bypass": "AzureServices", "ipRules": [],
                "virtualNetworkRules": [],
            })
            self.assertEqual(properties["publicNetworkAccess"], "Enabled")
            self.assertTrue(properties["supportsHttpsTrafficOnly"])
            self.assertFalse(properties["allowBlobPublicAccess"])

    def test_waf_v2_attaches_owned_policy_and_standard_v2_has_no_policy(self):
        template = self.templates["gateway"]
        self.assertNotIn("existingWafPolicyId", template["parameters"])
        gateway, = [item for item in resources(template)
                    if item["type"] == "Microsoft.Network/applicationGateways"]
        policy = next(item for item in resources(template)
                      if item["name"] == "[format('wafpol-g-base-{0}', parameters('wafPolicyIdentifier'))]")
        defaults = {key: copy.deepcopy(value["defaultValue"])
                    for key, value in template["parameters"].items() if "defaultValue" in value}
        for sku in ("WAF_v2", "Standard_v2"):
            parameters = {**defaults, "skuName": sku, "wafPolicyIdentifier": "abc123",
                          "resourceStem": "synthetic-test", "apimPrivateIp": "192.0.2.68",
                          "apimHostName": "synthetic-apim.example.com", "publicHostName": "api.example.com",
                          "portalHostName": "portal.example.com", "portalBackendIp": "192.0.2.200",
                          "portalBackendHost": "synthetic-aca.example.com",
                          "subnetId": "/synthetic-subnet", "publicIpId": "/synthetic-public-ip",
                          "keyVaultCertificateSecretId": "https://synthetic-vault.example.com/secrets/tls",
                          "firewallRootCaData": "synthetic-public-der",
                          "certificateIdentityId": "/synthetic-certificate-mi"}
            reader = expression(template)
            with self.subTest(sku=sku):
                enabled = reader.resolve(policy["condition"], parameters)
                self.assertEqual(enabled, sku == "WAF_v2")
                self.assertEqual(reader.resolve(template["variables"]["createWafPolicy"], parameters), enabled)
                properties = GatewayExpression(gateway["properties"], template["variables"]).evaluate(parameters)
                if sku == "WAF_v2":
                    output = reader.resolve(template["outputs"]["applicationGateway"]["value"]["wafPolicyId"], parameters)
                    self.assertEqual(output, "/synthetic/Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies/wafpol-g-base-abc123")
                    self.assertTrue(properties["firewallPolicy"]["id"].endswith("/wafpol-g-base-abc123"))
                else:
                    self.assertEqual(reader.resolve(template["outputs"]["applicationGateway"]["value"]["wafPolicyId"], parameters), "")
                    self.assertNotIn("firewallPolicy", properties)

    def test_enterprise_grants_are_removed_but_constructed_service_id_grants_remain(self):
        template = self.templates["bindings"]
        self.assertFalse(set(template["parameters"]) & {
            "projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders"})
        grants = [item for item in resources(template)
                  if item["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertFalse(any(item.get("copy") for item in grants))
        parameters = {"names": {"keyVault": "synthetic-kv", "foundry": "synthetic-ai",
                                "apim": "synthetic-apim", "apiCenter": "synthetic-catalog"},
                      "principalIds": {"apim": "11111111-1111-4111-8111-111111111111",
                                       "project": "22222222-2222-4222-8222-222222222222",
                                       "apiCenter": "33333333-3333-4333-8333-333333333333"},
                      "certificateIdentityPrincipalId": "44444444-4444-4444-8444-444444444444",
                      "managementPrincipalId": "55555555-5555-4555-8555-555555555555",
                      "grantManagementCertificateAccess": True, "grantApimSecretAccess": True,
                      "speechEnabled": True, "grantApimFoundryModelsAccess": True,
                      "enableApiCenterIntegration": True}
        reader = expression(template)
        actual = [reader.resolve(item["properties"], parameters) for item in grants]
        expected = {
            parameters["principalIds"]["apim"]: {"4633458b-17de-408a-b874-0445c86b69e6",
                                                 "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd",
                                                 "7628b7b8-a8b2-4cdc-b46f-e9b35248918e",
                                                 "f2dc8367-1007-4938-bd23-fe263f013447",
                                                 "a97b65f3-24c7-4388-baec-2e87135dc908"},
            parameters["principalIds"]["project"]: {"53ca6127-db72-4b80-b1b0-d745d6d5456d"},
            parameters["principalIds"]["apiCenter"]: {"71522526-b88f-4d52-b57f-d31fc3546d0d"},
        }
        roles = {}
        for item in actual:
            self.assertEqual(item["principalType"], "ServicePrincipal")
            roles.setdefault(item["principalId"], set()).add(item["roleDefinitionId"].split("/")[-1])
        self.assertEqual(roles, expected)

    def test_manual_maintenance_exposes_scopes_without_assigning_audit_reader_to_vm(self):
        template = self.templates["main"]
        supplied = template["outputs"]["ingressMonitoringPlan"]["value"]
        self.assertIn("monitoring", supplied)
        self.assertIn("storage", supplied)
        self.assertNotIn(".outputs.managementVm.value.principalId", json.dumps(supplied))
        outputs = template["outputs"]
        self.assertIn(".outputs.roleDefinitionIds.value", outputs["managementMaintenance"]["value"])
        self.assertIn(".outputs.managementVm.value.principalId",
                      json.dumps(outputs["managementClientInventory"]["value"]))


if __name__ == "__main__":
    unittest.main()
