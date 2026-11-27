"""リリース解除が検証用所属・購読・指定外APIへ波及しないことを確認する。"""
import copy
import unittest

from deployment.helpers.released_apis import reconcile_unreleased_apis, selected_api_names


APIM = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-test/providers/Microsoft.ApiManagement/service/apim-test"


class Transport:
    def __init__(self, memberships, *, failure=None, retained=False, nested=False):
        self.memberships = copy.deepcopy(memberships)
        self.calls = []
        self.failure = failure
        self.retained = retained
        self.nested = nested

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, headers))
        resource = path.split("?", 1)[0]
        if method == "GET":
            name = resource.removesuffix("/products").rsplit("/", 1)[-1]
            if self.failure == "read":
                return {"statusCode": 403}
            return {"statusCode": 200, "body": {"value": [
                {"id": APIM + ("/apis/" + name if self.nested else "") + "/products/" + product}
                for product in self.memberships.get(name, [])]}}
        if method == "DELETE":
            name = resource.rsplit("/", 1)[-1]
            if self.failure == "delete":
                return {"statusCode": 409}
            if not self.retained:
                self.memberships[name].remove("released-apis")
            return {"statusCode": 204}
        raise AssertionError("Unexpected operation")


class ReleasedApiTests(unittest.TestCase):
    def test_only_false_or_missing_targets_are_unlinked_and_other_products_are_preserved(self):
        definitions = [
            {"name": "released-api", "released": True},
            {"name": "withdrawn-api", "released": False, "mcp": {"name": "withdrawn-mcp"}},
            {"name": "custom-api"},
        ]
        transport = Transport({
            "released-api": ["released-apis", "model-users"],
            "withdrawn-api": ["released-apis", "pii-users"],
            "withdrawn-mcp": ["released-apis", "pii-users"],
            "custom-api": ["model-users"],
            "not-selected-api": ["released-apis", "connection-users"],
        })
        result = reconcile_unreleased_apis(APIM, definitions, transport)
        self.assertEqual(result["removedApiNames"], ["withdrawn-api", "withdrawn-mcp"])
        self.assertEqual(transport.memberships["withdrawn-api"], ["pii-users"])
        self.assertEqual(transport.memberships["withdrawn-mcp"], ["pii-users"])
        self.assertEqual(transport.memberships["released-api"], ["released-apis", "model-users"])
        self.assertEqual(transport.memberships["not-selected-api"], ["released-apis", "connection-users"])
        writes = [path.split("?", 1)[0] for method, path, _ in transport.calls if method != "GET"]
        self.assertEqual(writes, [APIM + "/products/released-apis/apis/withdrawn-api", APIM + "/products/released-apis/apis/withdrawn-mcp"])
        self.assertFalse(any("/subscriptions/" in path[len(APIM):] for _, path, _ in transport.calls))
        before = len(writes)
        repeated = reconcile_unreleased_apis(APIM, definitions, transport)
        self.assertEqual(repeated["removedApiNames"], [])
        self.assertEqual(sum(method == "DELETE" for method, _, _ in transport.calls), before)

    def test_generated_mcp_uses_its_parent_release_state(self):
        definitions = [{"name": "pii", "released": True, "mcp": {"name": "pii-mcp"}}, {"name": "custom"}]
        self.assertEqual(selected_api_names(definitions, released=True), ["pii", "pii-mcp"])
        self.assertEqual(selected_api_names(definitions, released=False), ["custom"])

    def test_nested_memberships_remove_only_selected_api_and_its_generated_mcp(self):
        definitions = [{"name": "withdrawn-api", "released": False, "mcp": {"name": "withdrawn-mcp"}}]
        transport = Transport({
            "withdrawn-api": ["released-apis", "model-users"],
            "withdrawn-mcp": ["released-apis", "pii-users"],
            "not-selected-api": ["released-apis", "connection-users"],
        }, nested=True)
        result = reconcile_unreleased_apis(APIM, definitions, transport)
        self.assertEqual(result["removedApiNames"], ["withdrawn-api", "withdrawn-mcp"])
        self.assertEqual(transport.memberships["withdrawn-api"], ["model-users"])
        self.assertEqual(transport.memberships["withdrawn-mcp"], ["pii-users"])
        self.assertEqual(transport.memberships["not-selected-api"], ["released-apis", "connection-users"])
        self.assertEqual(sum(method == "DELETE" for method, _, _ in transport.calls), 2)
        again = reconcile_unreleased_apis(APIM, definitions, transport)
        self.assertEqual(again["removedApiNames"], [])
        self.assertEqual(sum(method == "DELETE" for method, _, _ in transport.calls), 2)

    def test_other_api_product_or_scope_ids_do_not_select_a_membership(self):
        class ListedIds(Transport):
            def __init__(self, ids):
                super().__init__({})
                self.ids = ids

            def request(self, method, path, body=None, headers=None):
                self.calls.append((method, path, headers))
                if method != "GET":
                    raise AssertionError("Unrelated membership must not be deleted")
                return {"statusCode": 200, "body": {"value": [{"id": value} for value in self.ids]}}

        unrelated = [
            APIM + "/apis/other-api/products/released-apis",
            APIM + "/apis/withdrawn-api/products/released-apis-extra",
            APIM + "/apis/withdrawn-api/products/model-users",
            APIM + "/products/released-apis-extra",
            APIM.replace("/resourceGroups/rg-test/", "/resourceGroups/rg-other/") + "/products/released-apis",
            APIM.replace("/service/apim-test", "/service/apim-other") + "/apis/withdrawn-api/products/released-apis",
            "https://example.invalid" + APIM + "/products/released-apis",
            "malformed-independent-fixture",
        ]
        for resource_id in unrelated:
            with self.subTest(resource_id=resource_id):
                transport = ListedIds([resource_id])
                result = reconcile_unreleased_apis(APIM, [{"name": "withdrawn-api", "released": False}], transport)
                self.assertEqual(result["removedApiNames"], [])
                self.assertTrue(all(method == "GET" for method, _, _ in transport.calls))
        for invalid_id in (None, 1, {}, []):
            with self.subTest(invalid_id=invalid_id):
                transport = ListedIds([invalid_id])
                with self.assertRaises(ValueError):
                    reconcile_unreleased_apis(APIM, [{"name": "withdrawn-api", "released": False}], transport)
                self.assertTrue(all(method == "GET" for method, _, _ in transport.calls))

    def test_failed_inventory_causes_no_delete_and_delete_errors_are_not_hidden(self):
        for failure in ("read", "delete"):
            with self.subTest(failure=failure):
                transport = Transport({"api": ["released-apis"]}, failure=failure)
                with self.assertRaises(ValueError):
                    reconcile_unreleased_apis(APIM, [{"name": "api", "released": False}], transport)
                if failure == "read":
                    self.assertTrue(all(method == "GET" for method, _, _ in transport.calls))

    def test_removal_must_be_confirmed_before_success(self):
        transport = Transport({"api": ["released-apis"]}, retained=True)
        with self.assertRaisesRegex(ValueError, "pending"):
            reconcile_unreleased_apis(APIM, [{"name": "api"}], transport, timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
