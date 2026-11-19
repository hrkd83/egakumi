"""APIM Product の subscription 設定を、配備する ARM テンプレートで確認する。"""

from bicep_helpers import compile_bicep

from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class ProductSubscriptionDeploymentTests(unittest.TestCase):
    """キーを使わない Product に subscription の承認項目を送らない。"""

    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        resources = cls.template["resources"]
        cls.products = [item for item in (resources.values() if isinstance(resources, dict) else resources)
                        if item["type"] == "Microsoft.ApiManagement/service/products"]

    def test_every_business_product_requires_approved_subscription(self):
        product = self.template['resources']['protectedProducts']
        self.assertEqual([item for item in self.products if item['properties']['subscriptionRequired']], [product])
        props = product["properties"]
        self.assertTrue(props["subscriptionRequired"])
        self.assertTrue(props["approvalRequired"])
        self.assertEqual(props["state"], "published")
        self.assertNotIn("tokenEntryProduct", self.template["resources"])
        self.assertNotIn("tokenEntryApiLinks", self.template["resources"])

    def test_employee_product_is_closed_and_omits_subscription_approval(self):
        product = self.template['resources']['entraOpenProduct']
        self.assertEqual([item for item in self.products if not item['properties']['subscriptionRequired']], [product])
        self.assertIn('entraAuthentication', product['condition'])
        self.assertIn('enabled', product['condition'])
        self.assertEqual(product['properties']['state'], 'notPublished')
        self.assertNotIn('approvalRequired', product['properties'])
        # Employee membership is activated by the checked controller only after
        # the complete authentication policy graph has been verified.
        self.assertNotIn('entraOpenProductApiLinks', self.template['resources'])

    def test_personal_connection_protected_and_browser_apis_have_distinct_admission(self):
        resources = self.template["resources"]
        self.assertTrue(resources["credentialLoginApi"]["properties"]["subscriptionRequired"])
        self.assertFalse(resources["credentialBrowserApi"]["properties"]["subscriptionRequired"])
        links = resources["credentialLoginProductLinks"]
        self.assertIn("credential-manager-login", links["name"])
        self.assertNotIn("credential-manager-browser", links["name"])
        self.assertIn("protectedProducts", links["dependsOn"])


if __name__ == "__main__":
    unittest.main()
