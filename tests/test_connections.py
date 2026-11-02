"""APIM Credential Managerの配備定義をローカルで検証する。

Bicepのコンパイル結果からprovider・connection・Access policyの結び付きを調べる。資源の作成と本人による同意を同じ完了条件にしない。
"""

from bicep_helpers import compile_bicep

import json
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class ConnectionDeploymentTests(unittest.TestCase):
    """APIM Credential Managerの配備定義をローカルで検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/deploy/credential-manager.bicep')
        resources = cls.template["resources"]
        cls.resources = list(resources.values()) if isinstance(resources, dict) else resources

    def resource(self, suffix):
        matches = [r for r in self.resources if r["type"] == "Microsoft.ApiManagement/service/" + suffix]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_only_connection_resources_are_created(self):
        created = {r["type"] for r in self.resources if not r.get("existing")}
        self.assertEqual(created, {
            "Microsoft.ApiManagement/service/authorizationProviders",
            "Microsoft.ApiManagement/service/authorizationProviders/authorizations",
            "Microsoft.ApiManagement/service/authorizationProviders/authorizations/accessPolicies",
        })
        self.assertTrue(all(r["apiVersion"] == "2024-05-01" for r in self.resources))

    def test_secret_is_required_secure_and_never_returned(self):
        parameter = self.template["parameters"]["clientSecret"]
        self.assertEqual(parameter["type"].lower(), "securestring")
        self.assertNotIn("defaultValue", parameter)
        self.assertNotIn("clientSecret", json.dumps(self.template["outputs"]))
        self.assertNotIn("authorizationCodeParameters", json.dumps(self.template["outputs"]))

    def test_personal_connections_cannot_be_replaced_by_client_credentials(self):
        provider = self.resource("authorizationProviders")
        self.assertEqual(set(provider["properties"]["oauth2"]["grantTypes"]), {"authorizationCode"})
        connection = self.resource("authorizationProviders/authorizations")
        self.assertEqual(connection["properties"], {
            "authorizationType": "OAuth2", "oauth2grantType": "AuthorizationCode",
        })
        self.assertNotIn("status", connection["properties"])
        self.assertEqual(self.template["parameters"]["connectionNames"]["defaultValue"], [])

    def test_only_apim_system_identity_gets_credential_access(self):
        access = self.resource("authorizationProviders/authorizations/accessPolicies")
        self.assertEqual(access["properties"], {
            "objectId": "[parameters('apimPrincipalId')]", "tenantId": "[parameters('tenantId')]",
        })
        self.assertIn("parameters('apimPrincipalId')", access["name"])
        self.assertIn("dependsOn", access)
        self.assertNotIn("appIds", access["properties"])


if __name__ == "__main__":
    unittest.main()
