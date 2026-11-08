"""初期サンプルだけを削除し、業務API・購読を残す動作を検証する。"""
import copy
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers.apim_samples import cleanup_default_samples, cleanup_candidates

APIM = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-test-core-svc/providers/Microsoft.ApiManagement/service/apim-test-core"
ECHO = APIM + "/apis/echo-api"
STARTER = APIM + "/products/starter"
UNLIMITED = APIM + "/products/unlimited"
BUSINESS = APIM + "/products/model-users"
API_DEFINITIONS = [{"name": "model-v1", "productId": "model-users"}]


class Transport:
    def __init__(self):
        self.resources, self.collections, self.pages = {}, {}, {}
        self.calls, self.pending, self.fail_delete = [], {}, {}
        self.before_delete_read = None

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(headers)))
        resource_id = urlsplit(path).path
        if method == "DELETE":
            if resource_id in self.fail_delete:
                return {"statusCode": self.fail_delete[resource_id], "body": {"error": {"message": "SECRET_SENTINEL"}}}
            status = 202 if resource_id in self.pending else 204
            self.resources.pop(resource_id, None)
            return {"statusCode": status, "body": None}
        if method != "GET":
            raise AssertionError("Unexpected method")
        if path in self.pages:
            return copy.deepcopy(self.pages[path])
        if resource_id in self.collections:
            return {"statusCode": 200, "body": {"value": copy.deepcopy(self.collections[resource_id])}}
        if resource_id not in self.resources:
            remaining = self.pending.get(resource_id, 0)
            if remaining:
                self.pending[resource_id] = remaining - 1
                return {"statusCode": 200, "body": {"id": resource_id}}
            return {"statusCode": 404, "body": {}}
        value = copy.deepcopy(self.resources[resource_id])
        if self.before_delete_read:
            value = self.before_delete_read(resource_id, value)
        return {"statusCode": 200, "headers": {"ETag": '"entity-version"'}, "body": value}

    def mutations(self):
        return [call for call in self.calls if call[0] != "GET"]


def entity(resource_id, **properties):
    return {"id": resource_id, "properties": properties}


def populated():
    t = Transport()
    t.resources[ECHO] = entity(ECHO, path="echo", serviceUrl="https://echoapi.cloudapp.net/api", apiRevision="1")
    for product in (STARTER, UNLIMITED, BUSINESS):
        t.resources[product] = entity(product, displayName=product.rsplit("/", 1)[-1].title())
        t.collections[product + "/apis"] = [entity(product + "/apis/" + ("model-v1" if product == BUSINESS else "echo-api"))]
    t.resources[APIM + "/apis/model-v1"] = entity(APIM + "/apis/model-v1", path="models", serviceUrl="https://backend.example.com")
    t.collections[ECHO + "/products"] = [entity(STARTER), entity(UNLIMITED)]
    scopes = {"starter-sub": STARTER, "unlimited-sub": UNLIMITED, "model-sub": BUSINESS,
              "master": APIM, "all-api-sub": APIM + "/apis"}
    t.collections[APIM + "/subscriptions"] = []
    for name, scope in scopes.items():
        resource_id = APIM + "/subscriptions/" + name
        value = entity(resource_id, scope=scope, primaryKey="SECRET_SENTINEL")
        t.resources[resource_id] = value
        t.collections[APIM + "/subscriptions"].append(value)
    return t


class DefaultSampleCleanupTests(unittest.TestCase):
    def cleanup(self, transport, definitions=API_DEFINITIONS, **kwargs):
        return cleanup_default_samples(APIM, definitions, transport, sleep=lambda _: None, **kwargs)

    def test_absent_samples_are_a_successful_noop(self):
        t = Transport()
        result = self.cleanup(t)
        self.assertEqual(result["status"], "complete")
        self.assertFalse(t.mutations())
        self.assertEqual(len(t.calls), 3)

    def test_only_sample_scopes_are_deleted_in_dependency_order(self):
        t = populated()
        t.pending[ECHO] = 2
        result = self.cleanup(t)
        deleted = [urlsplit(call[1]).path for call in t.mutations()]
        self.assertEqual(deleted, [APIM + "/subscriptions/starter-sub", APIM + "/subscriptions/unlimited-sub", STARTER, UNLIMITED, ECHO])
        self.assertEqual(result["deletedApiIds"], [ECHO])
        self.assertEqual(len(result["deletedSubscriptionIds"]), 2)
        for name in ("model-sub", "master", "all-api-sub"):
            self.assertIn(APIM + "/subscriptions/" + name, t.resources)
        self.assertIn(APIM + "/apis/model-v1", t.resources)
        self.assertTrue(all(call[3] == {"If-Match": '"entity-version"'} for call in t.mutations()))
        self.assertNotIn("SECRET_SENTINEL", str(result))
        for collection in t.collections:
            t.collections[collection] = []
        again = self.cleanup(t)
        self.assertEqual(again["deletedApiIds"], [])
        self.assertEqual(len(t.mutations()), 5)

    def test_named_echo_with_another_backend_or_revision_is_preserved_with_its_products(self):
        for changes in ({"serviceUrl": "https://business.example.com/api"}, {"apiRevision": "2"}, {"path": "business"}):
            with self.subTest(changes=changes):
                t = populated()
                t.resources[ECHO]["properties"].update(changes)
                result = self.cleanup(t)
                self.assertFalse(t.mutations())
                self.assertEqual(len(result["skipped"]), 3)

    def test_current_azure_echo_host_with_canonical_or_nested_links_is_cleaned(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                t = populated()
                t.resources[ECHO]["properties"]["serviceUrl"] = "https://echo.playground.azure-api.net/api"
                if nested:
                    t.collections[ECHO + "/products"] = [entity(ECHO + "/products/" + name)
                                                          for name in ("starter", "unlimited")]
                result = self.cleanup(t)
                self.assertEqual([urlsplit(call[1]).path for call in t.mutations()], [
                    APIM + "/subscriptions/starter-sub", APIM + "/subscriptions/unlimited-sub",
                    STARTER, UNLIMITED, ECHO])
                self.assertEqual(result["skipped"], [])
                self.assertTrue(all(call[3] == {"If-Match": '\"entity-version\"'} for call in t.mutations()))
                for name in ("model-sub", "master", "all-api-sub"):
                    self.assertIn(APIM + "/subscriptions/" + name, t.resources)
                self.assertIn(APIM + "/apis/model-v1", t.resources)
        for url in ("http://echo.playground.azure-api.net/api", "https://echo.playground.azure-api.net:8443/api",
                    "https://echo.playground.azure-api.net.evil.invalid/api", "https://fixture!@echo.playground.azure-api.net/api",
                    "https://echo.playground.azure-api.net/api?other=value", "https://echo.playground.azure-api.net/api#fragment",
                    "https://echo.playground.azure-api.net/other"):
            with self.subTest(url=url):
                t = populated()
                t.resources[ECHO]["properties"]["serviceUrl"] = url
                self.cleanup(t)
                self.assertFalse(t.mutations())

    def test_nested_echo_product_ids_are_exact_owned_eligible_aliases(self):
        t = populated()
        t.collections[ECHO + "/products"] = [entity(ECHO + "/products/" + name)
                                              for name in ("starter", "unlimited")]
        self.assertEqual(self.cleanup(t)["deletedApiIds"], [ECHO])
        foreign_apim = APIM.replace("apim-test-core", "apim-other")
        for link in (foreign_apim + "/apis/echo-api/products/starter", APIM + "/apis/other-api/products/starter",
                     ECHO + "/products/starter-other", ECHO + "/products/starter/child",
                     ECHO + "/products/starter?extra=value", ECHO + "/products/model-users"):
            with self.subTest(link=link):
                t = populated()
                t.collections[ECHO + "/products"] = [entity(ECHO + "/products/starter"),
                                                      entity(ECHO + "/products/unlimited"), entity(link)]
                result = self.cleanup(t)
                self.assertEqual(result["deletedApiIds"], [])
                self.assertIn(ECHO, t.resources)
                self.assertIn(BUSINESS, t.resources)
                self.assertIn(APIM + "/apis/model-v1", t.resources)
                for name in ("model-sub", "master", "all-api-sub"):
                    self.assertIn(APIM + "/subscriptions/" + name, t.resources)
                self.assertTrue(all(urlsplit(call[1]).path != link for call in t.mutations()))

    def test_configured_api_and_products_are_preserved(self):
        t = populated()
        definitions = [{"name": "echo-api", "productId": "starter"}]
        self.cleanup(t, definitions)
        self.assertFalse(t.mutations())
        plan = cleanup_candidates(definitions)
        self.assertEqual(plan["apiNames"], [])
        self.assertEqual(plan["productNames"], ["unlimited"])
        self.assertFalse(plan["inventoryChecked"])

    def test_sample_product_with_a_business_or_mcp_api_keeps_its_subscription(self):
        t = populated()
        t.collections[STARTER + "/apis"].append(entity(STARTER + "/apis/pii-v1-mcp"))
        result = self.cleanup(t)
        self.assertIn(STARTER, t.resources)
        self.assertIn(APIM + "/subscriptions/starter-sub", t.resources)
        self.assertIn(ECHO, t.resources)
        self.assertNotIn(UNLIMITED, t.resources)
        self.assertEqual(result["deletedProductIds"], [UNLIMITED])

    def test_echo_used_by_a_business_product_is_kept(self):
        t = populated()
        t.collections[ECHO + "/products"].append(entity(BUSINESS))
        result = self.cleanup(t)
        self.assertIn(ECHO, t.resources)
        self.assertEqual(result["deletedApiIds"], [])
        self.assertEqual(len(result["deletedProductIds"]), 2)

    def test_subscriptions_are_read_through_all_pages_and_api_scopes_are_cleaned(self):
        t = populated()
        api_sub = entity(APIM + "/subscriptions/echo-sub", scope=ECHO)
        t.resources[api_sub["id"]] = api_sub
        collection = APIM + "/subscriptions"
        next_link = "https://management.azure.com" + collection + "?api-version=2024-05-01&$skipToken=opaque"
        t.pages[collection + "?api-version=2024-05-01"] = {"statusCode": 200, "body": {"value": t.collections[collection][:1], "nextLink": next_link}}
        t.pages[next_link] = {"statusCode": 200, "body": {"value": t.collections[collection][1:] + [api_sub], "nextLink": ""}}
        result = self.cleanup(t)
        self.assertEqual(len(result["deletedSubscriptionIds"]), 3)
        self.assertIn(APIM + "/subscriptions/model-sub", t.resources)

    def test_changed_subscription_scope_and_foreign_next_link_do_not_delete_business_keys(self):
        t = populated()
        t.before_delete_read = lambda rid, value: entity(rid, scope=BUSINESS) if rid.endswith("/starter-sub") else value
        result = self.cleanup(t)
        self.assertIn(APIM + "/subscriptions/starter-sub", t.resources)
        self.assertTrue(any(item["reason"] == "changed-before-deletion" for item in result["skipped"]))
        t = populated()
        t.pages[APIM + "/subscriptions?api-version=2024-05-01"] = {"statusCode": 200, "body": {"value": [], "nextLink": "https://other.example.com/elsewhere?api-version=2024-05-01"}}
        with self.assertRaisesRegex(ValueError, "APIM collection"):
            self.cleanup(t)
        self.assertFalse(t.mutations())
        self.assertFalse(any("other.example.com" in call[1] for call in t.calls))

    def test_failed_or_pending_deletion_is_not_reported_as_success(self):
        t = populated()
        t.fail_delete[APIM + "/subscriptions/starter-sub"] = 403
        with self.assertRaisesRegex(ValueError, "HTTP 403") as context:
            self.cleanup(t)
        self.assertNotIn("SECRET_SENTINEL", str(context.exception))
        self.assertIn(STARTER, t.resources)
        t = populated()
        with self.assertRaisesRegex(ValueError, "pending"):
            self.cleanup(t, timeout_seconds=0)
        self.assertFalse(t.mutations())

    def test_unreadable_product_membership_never_becomes_an_empty_product(self):
        for status in (403, 404):
            with self.subTest(status=status):
                t = populated()
                path = STARTER + "/apis?api-version=2025-09-01-preview"
                t.pages[path] = {"statusCode": status, "body": {}}
                with self.assertRaisesRegex(ValueError, "inventory could not be read"):
                    self.cleanup(t)
                self.assertFalse(t.mutations())


if __name__ == "__main__":
    unittest.main()
