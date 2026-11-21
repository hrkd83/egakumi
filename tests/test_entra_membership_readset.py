"""Bounded intermediate reads retain final full auditing in synthetic ARM."""
import copy
import unittest
from unittest import mock

from test_entra_publication import APIM, GLOBAL, NATIVE, OPEN, SyntheticTransport, response, xml
from test_entra_publication_protocols import protocol_fixture
import entra_publication as publication


class AllApiTransport(SyntheticTransport):
    def sync_members(self):
        self.refresh(OPEN, self.inventory[OPEN]["body"]["properties"])
        self.refresh(OPEN + "/apis", value=[{"id": target} for target in sorted(self.members)])
        for resource_id in tuple(self.inventory):
            if not resource_id.startswith(APIM + "/apis/") or not resource_id.endswith("/products"):
                continue
            target = resource_id.removesuffix("/products")
            old = self.inventory[resource_id]["body"]["value"]
            aliases = {OPEN, target + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID}
            values = [entry for entry in old if entry.get("id") not in aliases]
            if target in self.members:
                values.append({"id": OPEN})
            if old != values:
                self.refresh(resource_id, value=values)


def many_api_fixture(count=8, operations=4):
    """Independent synthetic API/operation counts; no deployed inventory."""
    manifest, original = protocol_fixture()
    template = APIM + "/apis/model-b"
    for index in range(count):
        target = APIM + "/apis/synthetic-api-" + str(index).zfill(2)
        properties = copy.deepcopy(manifest.api_properties[template])
        properties["path"] = "synthetic/" + str(index)
        manifest.api_properties[target] = properties
        names = tuple("invoke-" + str(number) for number in range(operations))
        manifest.operations[target], manifest.products[target] = names, ("model-users",)
        original.inventory[target] = response(target, properties)
        for kind in ("tools", "schemas"):
            original.inventory[target + "/" + kind] = response(target + "/" + kind, value=[])
        original.inventory[target + "/products"] = response(target + "/products", value=[{"id": NATIVE}])
        original.inventory[target + "/revisions"] = response(target + "/revisions", value=[{
            "apiId": "/apis/" + target.rsplit("/", 1)[-1] + ";rev=1",
            "apiRevision": "1", "isCurrent": True, "isOnline": True}])
        original.inventory[target + "/operations"] = response(target + "/operations", value=[
            {"id": target + "/operations/" + name} for name in names])
        manifest.policies[target + "/policies/policy"] = manifest.policies[template + "/policies/policy"]
        original.inventory[target + "/policies/policy"] = response(
            target + "/policies/policy", {"value": manifest.policies[target + "/policies/policy"]})
        for name in names:
            operation = target + "/operations/" + name
            manifest.api_children[operation] = {"method": "POST", "urlTemplate": "/" + name, "responses": []}
            original.inventory[operation] = response(operation, manifest.api_children[operation])
            manifest.policies[operation + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
            original.inventory[operation + "/policies/policy"] = response(
                operation + "/policies/policy", {"value": publication.OPEN_PRODUCT_POLICY})
    return manifest, AllApiTransport(copy.deepcopy(original.inventory))


class MembershipReadsetTests(unittest.TestCase):
    def setUp(self):
        self.current, self.transport = many_api_fixture()
        self.api_a = APIM + "/apis/synthetic-api-00"
        self.api_b = APIM + "/apis/synthetic-api-01"
        self.api_c = APIM + "/apis/synthetic-api-02"
        self.desired = copy.deepcopy(self.current)
        self.desired.policies[GLOBAL] = xml("new")
        self.snapshot = publication.read_policy_graph(self.current, self.transport)
        product, _ = publication.read_open_product(APIM, self.transport, allowed_api_ids=())
        self.snapshot.update(product)

    def transition(self, *, previous=(), desired=None, prerequisites=None):
        return publication.transition(self.current, self.desired.policies[GLOBAL], self.transport,
            lambda: self.desired, previous_open_api_ids=previous,
            desired_open_api_ids=[self.api_a, self.api_b] if desired is None else desired,
            stage_prerequisites=prerequisites, sleep=lambda _: None)

    def test_intermediate_readset_keeps_all_roots_collections_shared_and_exact_external_reads(self):
        external = APIM.split("/providers/", 1)[0] + "/providers/Microsoft.App/containerApps/synthetic-app"
        entry = (external + "?api-version=2025-01-01", {"Accept": "application/json"},
                 response(external, {"synthetic": True}))
        self.snapshot[external] = entry
        checked = publication._membership_readset(self.snapshot, APIM, self.api_a, self.current)
        self.assertLess(len(checked), len(self.snapshot))
        self.assertIs(checked[external], entry)
        for target in self.current.operations:
            self.assertIs(checked[target], self.snapshot[target])
            for kind in ("operations", "products", "revisions", "schemas", "tools"):
                self.assertIs(checked[target + "/" + kind], self.snapshot[target + "/" + kind])
        for resource_id, original in self.snapshot.items():
            if not resource_id.startswith(APIM + "/apis/"):
                self.assertIs(checked[resource_id], original)
            elif resource_id.startswith(self.api_a + "/"):
                self.assertIs(checked[resource_id], original)
        self.assertNotIn(self.api_b + "/operations/invoke-0", checked)
        self.assertNotIn(self.api_b + "/policies/policy", checked)
        self.transport.inventory[external] = copy.deepcopy(entry[2])
        self.transport.calls.clear()
        publication.verify_snapshot(checked, self.transport)
        self.assertIn(("GET", external, None, entry[1]), self.transport.calls)
        self.assertIn(self.api_b + "/policies/policy", self.snapshot)

    def test_native_mcp_operation_references_keep_bidirectional_transitive_api_details(self):
        native = APIM + "/apis/native-tool"
        rest_a, rest_b = APIM + "/apis/model-a", APIM + "/apis/model-b"
        tool = native + "/tools/another"
        expected = {"displayName": "another", "operationId": rest_b + "/operations/responses"}
        self.current.api_children[tool] = expected
        self.transport.inventory[tool] = response(tool, expected)
        self.transport.inventory[native + "/tools"]["body"]["value"].append({"id": tool})
        snapshot = publication.read_policy_graph(self.current, self.transport)
        for start in (rest_a, rest_b, native):
            with self.subTest(start=start):
                checked = publication._membership_readset(snapshot, APIM, start, self.current)
                for target in (rest_a, rest_b, native):
                    for resource_id in snapshot:
                        if resource_id.startswith(target + "/"):
                            self.assertIn(resource_id, checked)
                self.assertNotIn(self.api_a + "/operations/invoke-0", checked)

    def test_unknown_foreign_incomplete_or_nonstandard_graphs_use_full_snapshot(self):
        cases = ("unknown-child", "foreign-api", "unknown-apim-scope", "incomplete",
                 "mismatched-get", "unknown-query", "invalid-entry", "wrong-manifest",
                 "legacy-source", "missing-tool-reference")
        for case in cases:
            with self.subTest(case=case):
                snapshot, manifest = copy.deepcopy(self.snapshot), copy.deepcopy(self.current)
                if case in ("unknown-child", "foreign-api", "unknown-apim-scope"):
                    resource_id = (self.api_b + "/diagnostics" if case == "unknown-child"
                                   else APIM + "-foreign/apis/other" if case == "foreign-api"
                                   else APIM + "/workspaces/unknown")
                    snapshot[resource_id] = (resource_id + "?api-version=" + publication.APIM_API,
                                             None, response(resource_id, {}))
                elif case == "incomplete":
                    del snapshot[self.api_b + "/schemas"]
                elif case == "mismatched-get":
                    path, headers, body = snapshot[self.api_b]
                    snapshot[self.api_b] = (path.replace("model", "other").replace("synthetic", "other"), headers, body)
                elif case == "unknown-query":
                    path, headers, body = snapshot[self.api_b]
                    snapshot[self.api_b] = (path + "&unknown=true", headers, body)
                elif case == "invalid-entry":
                    snapshot[self.api_b] = (None, None, {})
                elif case == "wrong-manifest":
                    manifest = None
                elif case == "legacy-source":
                    object.__setattr__(manifest, "source_version", 1)
                else:
                    manifest.api_children[APIM + "/apis/native-tool/tools/redact"].pop("operationId")
                self.assertIs(publication._membership_readset(snapshot, APIM, self.api_a, manifest), snapshot)

    def test_direct_membership_default_keeps_full_reads(self):
        self.transport.calls.clear()
        publication._membership(APIM, self.api_a, True, self.transport, self.snapshot, set(),
                                sleep=lambda _: None, deadline=float("inf"))
        reads = {resource_id for method, resource_id, *_ in self.transport.calls if method == "GET"}
        self.assertIn(self.api_b + "/operations/invoke-0", reads)
        self.assertIn(self.api_b + "/policies/policy", reads)

    def test_shared_api_root_and_collection_drift_stops_before_the_next_open(self):
        resources = (GLOBAL, APIM + "/policyFragments/model-audit", NATIVE,
                     APIM + "/backends/model-a-backend", APIM + "/namedValues/model-a-audience",
                     self.api_c, self.api_c + "/operations", self.api_c + "/products",
                     self.api_c + "/revisions", self.api_c + "/schemas", self.api_c + "/tools")
        for resource_id in resources:
            with self.subTest(resource_id=resource_id):
                self.setUp()
                def drift(method, target, *_):
                    if method == "PUT" and target == OPEN + "/apis/" + self.api_a.rsplit("/", 1)[-1]:
                        item = self.transport.inventory[resource_id]
                        item["body"]["etag"] = item["headers"]["ETag"] = '"concurrent"'
                self.transport.hook = drift
                with self.assertRaises(publication.TransitionError):
                    self.transition()
                self.assertEqual(self.transport.members, set())
                self.assertFalse(any(method == "PUT" and target == OPEN + "/apis/" + self.api_b.rsplit("/", 1)[-1]
                                     for method, target, *_ in self.transport.calls))

    def test_current_api_content_drift_with_same_etag_stops_before_the_next_open(self):
        for suffix, field, value in (("/operations/invoke-0", "urlTemplate", "/changed"),
                                     ("/policies/policy", "value", xml("unreviewed"))):
            with self.subTest(suffix=suffix):
                self.setUp()
                def drift(method, target, *_):
                    if method == "PUT" and target == OPEN + "/apis/" + self.api_a.rsplit("/", 1)[-1]:
                        self.transport.inventory[self.api_a + suffix]["body"]["properties"][field] = value
                self.transport.hook = drift
                with self.assertRaises(publication.TransitionError):
                    self.transition()
                self.assertEqual(self.transport.members, set())
                self.assertFalse(any(method == "PUT" and target == OPEN + "/apis/" + self.api_b.rsplit("/", 1)[-1]
                                     for method, target, *_ in self.transport.calls))

    def test_omitted_individual_drift_fails_final_full_audit_and_closes_both_opened_routes(self):
        # Includes an unrelated API, and A changing while B is being opened.
        for changed, trigger in ((self.api_c, self.api_a), (self.api_a, self.api_b)):
            with self.subTest(changed=changed, trigger=trigger):
                self.setUp()
                def drift(method, target, *_):
                    if method == "PUT" and target == OPEN + "/apis/" + trigger.rsplit("/", 1)[-1]:
                        self.transport.inventory[changed + "/operations/invoke-0"]["body"]["properties"]["urlTemplate"] = "/changed"
                self.transport.hook = drift
                with self.assertRaises(publication.TransitionError) as error:
                    self.transition()
                self.assertTrue(error.exception.employee_path_closed)
                self.assertTrue(error.exception.recovery_complete)
                self.assertEqual(self.transport.members, set())
                self.assertTrue(any(method == "PUT" and target == OPEN + "/apis/" + self.api_b.rsplit("/", 1)[-1]
                                    for method, target, *_ in self.transport.calls))
                deletions = [target for method, target, *_ in self.transport.calls if method == "DELETE"]
                self.assertEqual(deletions, [OPEN + "/apis/" + target.rsplit("/", 1)[-1]
                                             for target in (self.api_b, self.api_a)])
                self.assertEqual(self.transport.inventory[GLOBAL]["body"]["properties"]["value"],
                                 self.current.policies[GLOBAL])

    def test_final_failure_rollback_preserves_concurrently_reopened_prior_scope(self):
        self.transport.members = {self.api_c}
        self.transport.sync_members()
        state = {"b_written": False, "b_products": False, "injected": False}
        def drift(method, target, *_):
            if method == "PUT" and target == OPEN + "/apis/" + self.api_b.rsplit("/", 1)[-1]:
                state["b_written"] = True
            if method == "GET" and target == self.api_b + "/products" and state["b_written"]:
                state["b_products"] = True
            if method == "GET" and target == GLOBAL and state["b_products"] and not state["injected"]:
                state["injected"] = True
                self.transport.members.add(self.api_c)
                self.transport.sync_members()
        self.transport.hook = drift
        with self.assertRaises(publication.TransitionError) as error:
            self.transition(previous=[self.api_c])
        self.assertTrue(state["injected"])
        self.assertFalse(error.exception.employee_path_closed)
        self.assertEqual(self.transport.members, {self.api_c})
        writes = [(method, target) for method, target, *_ in self.transport.calls if method != "GET"]
        self.assertEqual([target for method, target in writes if method == "DELETE"],
                         [OPEN + "/apis/" + target.rsplit("/", 1)[-1]
                          for target in (self.api_c, self.api_b, self.api_a)])
        self.assertEqual(self.transport.inventory[GLOBAL]["body"]["properties"]["value"],
                         self.desired.policies[GLOBAL])

    def test_public_multi_api_transition_reduces_get_count_with_identical_mutation_sequence(self):
        selected = sorted(target for target in self.current.operations if "/synthetic-api-" in target)
        self.transport.members = set(selected)
        self.transport.sync_members()
        initial = copy.deepcopy(self.transport.inventory)
        self.transport.calls.clear()
        optimized = self.transition(previous=selected, desired=selected)
        optimized_calls = copy.deepcopy(self.transport.calls)
        # Start from identical independent synthetic ARM state and exercise the
        # public controller again with its conservative full-snapshot fallback.
        self.transport = AllApiTransport(initial)
        self.transport.members = set(selected)
        with mock.patch.object(publication, "_membership_readset", side_effect=lambda snapshot, *_: snapshot):
            full = self.transition(previous=selected, desired=selected)
        self.assertEqual(optimized, full)
        self.assertEqual(self.transport.members, set(selected))
        self.assertEqual([(m, r, body) for m, r, body, _ in optimized_calls if m in ("PUT", "DELETE")],
                         [(m, r, body) for m, r, body, _ in self.transport.calls if m in ("PUT", "DELETE")])
        optimized_gets = sum(method == "GET" for method, *_ in optimized_calls)
        full_gets = sum(method == "GET" for method, *_ in self.transport.calls)
        self.assertLess(optimized_gets, full_gets * 0.75)
        self.assertLess(optimized_gets, 12000)
        self.assertFalse(optimized["runtimeVerified"])


if __name__ == "__main__":
    unittest.main()
