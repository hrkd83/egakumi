"""所属変更の影響集合だけを更新し、全依存資源の競合検出を維持する。"""
import copy
import unittest
from unittest import mock

from test_entra_publication import APIM, GLOBAL, NATIVE, OPEN, response, xml
from test_entra_publication_protocols import protocol_fixture
import entra_publication as publication


class MembershipSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.current, self.transport = protocol_fixture()
        self.api_a = APIM + "/apis/model-a"
        self.api_b = APIM + "/apis/model-b"
        self.desired = copy.deepcopy(self.current)
        self.desired.policies[GLOBAL] = xml("new")

    def stage(self):
        self.transport.calls.append(("STAGE", "source", None, None))
        return self.desired

    def transition(self, *, old=(), new=()):
        return publication.transition(self.current, self.desired.policies[GLOBAL], self.transport, self.stage,
            previous_open_api_ids=old, desired_open_api_ids=new, sleep=lambda _: None)

    def test_close_and_open_reuse_verified_graph_and_read_only_the_changed_api_collection(self):
        self.transport.members = {self.api_a, self.api_b}
        self.transport.sync_members()
        with mock.patch.object(publication, "read_policy_graph", wraps=publication.read_policy_graph) as graph:
            with mock.patch.object(publication, "_read_changed_api_products",
                                   wraps=publication._read_changed_api_products) as changed:
                result = self.transition(old=[self.api_a, self.api_b], new=[self.api_a, self.api_b])
        self.assertTrue(result["employeePathOpen"])
        self.assertEqual(self.transport.members, {self.api_a, self.api_b})
        # Initial graph, complete closed stage, and final desired global are
        # independently validated. Membership count does not add full scans.
        self.assertEqual(graph.call_count, 3)
        self.assertEqual([(call.args[1], call.args[2]) for call in changed.call_args_list],
                         [(self.api_a, False), (self.api_b, False), (self.api_a, True), (self.api_b, True)])

    def test_changed_collection_accepts_both_exact_owned_id_shapes(self):
        for add in (False, True):
            for native_alias in (False, True):
                for employee_alias in (False, True):
                    with self.subTest(add=add, native=native_alias, employee=employee_alias):
                        ids = [self.api_a + "/products/model-users" if native_alias else NATIVE]
                        if add:
                            ids.append(self.api_a + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID
                                       if employee_alias else OPEN)
                        self.transport.refresh(self.api_a + "/products", value=[{"id": rid} for rid in ids])
                        self.transport.calls.clear()
                        snapshot = publication._read_changed_api_products(self.current, self.api_a, add, self.transport)
                        self.assertEqual(set(snapshot), {self.api_a + "/products"})
                        self.assertEqual([(method, rid) for method, rid, *_ in self.transport.calls],
                                         [("GET", self.api_a + "/products")])

    def test_changed_collection_rejects_unknown_duplicate_foreign_and_wrong_membership_sets(self):
        for add, ids in (
            (True, [NATIVE]), (False, [NATIVE, OPEN]), (True, [OPEN]),
            (True, [NATIVE, OPEN, OPEN]),
            (True, [NATIVE, OPEN, self.api_a + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID]),
            (True, [NATIVE, OPEN, APIM + "/products/unreviewed"]),
            (True, [NATIVE, OPEN, self.api_b + "/products/model-users"]),
            (True, [NATIVE, OPEN, self.api_a + ";rev=2/products/model-users"]),
            (True, [NATIVE, OPEN, None]),
        ):
            with self.subTest(add=add, ids=ids):
                self.transport.refresh(self.api_a + "/products", value=[{"id": rid} for rid in ids])
                with self.assertRaises(ValueError):
                    publication._read_changed_api_products(self.current, self.api_a, add, self.transport)
        self.transport.refresh(self.api_a + "/products", value=[{"id": NATIVE}, {"id": OPEN}])
        self.transport.inventory[self.api_a + "/products"]["body"]["nextLink"] = "https://management.example/next"
        with self.assertRaises(ValueError):
            publication._read_changed_api_products(self.current, self.api_a, True, self.transport)

    def test_dependency_etag_changes_after_an_association_write_cannot_be_adopted(self):
        resources = (
            GLOBAL, self.api_b, self.api_a + "/operations/responses",
            self.api_a + "/operations/responses/policies/policy",
            APIM + "/policyFragments/model-audit", APIM + "/backends/model-a-backend",
            APIM + "/namedValues/model-a-audience", NATIVE,
            self.api_a + "/schemas/public-contract", APIM + "/apis/native-tool/tools/redact",
            self.api_b + "/operations", self.api_b + "/revisions",
        )
        for resource in resources:
            with self.subTest(resource=resource):
                self.setUp()

                def drift(method, resource_id, *_):
                    if method == "PUT" and resource_id == OPEN + "/apis/model-a":
                        self.transport.inventory[resource]["body"]["etag"] = '"concurrent"'
                        self.transport.inventory[resource]["headers"]["ETag"] = '"concurrent"'

                self.transport.hook = drift
                with self.assertRaises(publication.TransitionError):
                    self.transition(new=[self.api_a, self.api_b])
                self.assertEqual(self.transport.members, set())
                self.assertFalse(any(method == "PUT" and rid == OPEN + "/apis/model-b"
                                     for method, rid, *_ in self.transport.calls))

    def test_unexpected_native_membership_after_write_closes_the_opened_route(self):
        seen = {"write": False, "injected": False}

        def drift(method, resource_id, *_):
            if method == "PUT" and resource_id == OPEN + "/apis/model-a":
                seen["write"] = True
            if (method == "GET" and resource_id == self.api_a + "/products"
                    and seen["write"] and not seen["injected"]):
                seen["injected"] = True
                return response(resource_id, value=[{"id": OPEN}])

        self.transport.hook = drift
        with self.assertRaises(publication.TransitionError) as failure:
            self.transition(new=[self.api_a, self.api_b])
        self.assertTrue(seen["injected"])
        self.assertTrue(failure.exception.employee_path_closed)
        self.assertEqual(self.transport.members, set())

    def test_dependency_content_drift_is_rejected_even_when_its_etag_is_unchanged(self):
        def drift(method, resource_id, *_):
            if method == "PUT" and resource_id == OPEN + "/apis/model-a":
                backend = self.transport.inventory[APIM + "/backends/model-a-backend"]
                backend["body"]["properties"]["url"] = "https://changed.example.com/openai/v1"

        self.transport.hook = drift
        with self.assertRaises(publication.TransitionError):
            self.transition(new=[self.api_a, self.api_b])
        self.assertEqual(self.transport.members, set())


if __name__ == "__main__":
    unittest.main()
