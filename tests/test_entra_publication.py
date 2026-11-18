"""合成ARMでopen Product切替のreadback/競合/閉鎖/回復を検証する。実接続はしない。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"),
               str(Path(__file__).resolve().parents[1] / "apps/gateway/deploy")]
import entra_publication as publication

APIM = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-fixture/providers/Microsoft.ApiManagement/service/fixture"
GLOBAL = APIM + "/policies/policy"
OPEN = APIM + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID
NATIVE = APIM + "/products/model-users"


def xml(label):
    return '<policies><inbound><set-variable name="mode" value="' + label + '" /></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'


def response(resource_id, properties=None, *, value=None, status=200, etag='"fixture-1"'):
    body = {"id": resource_id, "etag": etag}
    if properties is not None:
        body["properties"] = copy.deepcopy(properties)
    if value is not None:
        body["value"] = copy.deepcopy(value)
    return {"statusCode": status, "body": body, "headers": {"ETag": etag}}


class SyntheticTransport:
    def __init__(self, inventory):
        self.inventory = inventory
        self.calls, self.members, self.serial = [], set(), 1
        self.hook = None

    def refresh(self, resource_id, properties=None, value=None):
        self.serial += 1
        self.inventory[resource_id] = response(resource_id, properties, value=value, etag=f'"fixture-{self.serial}"')

    def sync_members(self):
        self.refresh(OPEN, self.inventory[OPEN]["body"]["properties"])
        self.refresh(OPEN + "/apis", value=[{"id": api_id} for api_id in sorted(self.members)])
        for api_id in (APIM + "/apis/model-a", APIM + "/apis/model-b"):
            values = [{"id": NATIVE}]
            if api_id in self.members:
                values.append({"id": OPEN})
            if self.inventory[api_id + "/products"]["body"]["value"] != values:
                self.refresh(api_id + "/products", value=values)

    def request(self, method, path, body=None, headers=None):
        resource_id = path.split("?", 1)[0]
        self.calls.append((method, resource_id, copy.deepcopy(body), copy.deepcopy(headers)))
        if self.hook:
            result = self.hook(method, resource_id, body, headers)
            if result is not None:
                return result
        if method == "GET":
            return copy.deepcopy(self.inventory.get(resource_id, {"statusCode": 404, "body": {}, "headers": {}}))
        if resource_id.startswith(OPEN + "/apis/") and method in ("PUT", "DELETE"):
            api_id = APIM + "/apis/" + resource_id.rsplit("/", 1)[-1]
            self.members.add(api_id) if method == "PUT" else self.members.discard(api_id)
            self.sync_members()
            return response(resource_id, {}, status=202)
        if resource_id == GLOBAL and method == "PUT":
            current = self.inventory[resource_id]["body"]["etag"]
            if headers != {"If-Match": current}:
                return {"statusCode": 412, "body": {}, "headers": {}}
            self.refresh(resource_id, body["properties"])
            return copy.deepcopy(self.inventory[resource_id])
        raise AssertionError("Unexpected synthetic mutation")


def fixture():
    api_names = ["model-a", "model-b"]
    policies = {GLOBAL: xml("old"), APIM + "/policyFragments/model-audit": '<fragment><set-variable name="audit" value="ok" /></fragment>'}
    operations, products, api_properties, backends, named = {}, {}, {}, {}, {}
    inventory = {GLOBAL: response(GLOBAL, {"format": "rawxml", "value": policies[GLOBAL]}),
                 NATIVE: response(NATIVE, {"subscriptionRequired": True}),
                 NATIVE + "/policies/policy": {"statusCode": 404, "body": {}, "headers": {}},
                 APIM + "/policyFragments/model-audit": response(APIM + "/policyFragments/model-audit", {"value": policies[APIM + "/policyFragments/model-audit"]}),
                 OPEN: response(OPEN, publication.OPEN_PRODUCT_PROPERTIES),
                 OPEN + "/policies/policy": response(OPEN + "/policies/policy", {"format": "rawxml", "value": publication.OPEN_PRODUCT_POLICY}),
                 OPEN + "/apis": response(OPEN + "/apis", value=[])}
    for name in api_names:
        api_id = APIM + "/apis/" + name
        api_properties[api_id] = {"type": "http", "path": "models/" + name, "subscriptionRequired": True,
                                  "subscriptionKeyParameterNames": {"header": "api-key", "query": "subscription-key"}}
        operations[api_id] = ("responses",)
        products[api_id] = ("model-users",)
        backends[APIM + "/backends/" + name + "-backend"] = {"protocol": "http", "url": "https://model.example.com/openai/v1", "tls": {"validateCertificateChain": True, "validateCertificateName": True}}
        named[APIM + "/namedValues/" + name + "-audience"] = {"displayName": name + "-audience", "secret": False, "value": "https://cognitiveservices.azure.com"}
        policies[api_id + "/policies/policy"] = ('<policies><inbound><set-variable name="gatewayAuditSettings" value="source" />'
            '<base /><include-fragment fragment-id="model-audit" />'
            '<set-backend-service backend-id="' + name + '-backend" /><authentication-managed-identity resource="{{' + name + '-audience}}" />'
            '</inbound><backend><forward-request buffer-response="false" /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')
        policies[api_id + "/operations/responses/policies/policy"] = publication.OPEN_PRODUCT_POLICY
        inventory[api_id] = response(api_id, api_properties[api_id])
        inventory[api_id + "/products"] = response(api_id + "/products", value=[{"id": NATIVE}])
        inventory[api_id + "/operations"] = response(api_id + "/operations", value=[{"id": api_id + "/operations/responses"}])
        inventory[api_id + "/revisions"] = response(api_id + "/revisions", value=[{"apiId": "/apis/" + name + ";rev=1", "apiRevision": "1", "isCurrent": True, "isOnline": True}])
    for resource_id, value in {**backends, **named}.items():
        inventory[resource_id] = response(resource_id, value)
    for resource_id, value in policies.items():
        inventory[resource_id] = response(resource_id, {"format": "rawxml", "value": value})
    current = publication.PolicyManifest(APIM, policies, operations, products, backends, named, api_properties)
    return current, SyntheticTransport(inventory)


def source_fixture(current):
    return {"schemaVersion": 1,
            "apis": [{"name": resource_id.rsplit("/", 1)[-1], "operations": list(names),
                      "products": list(current.products[resource_id]), "properties": current.api_properties[resource_id]}
                     for resource_id, names in current.operations.items()],
            "policies": [{"suffix": resource_id[len(APIM):], "value": value}
                         for resource_id, value in current.policies.items() if resource_id != GLOBAL],
            "backends": [{"suffix": resource_id[len(APIM):], "properties": value} for resource_id, value in current.backends.items()],
            "namedValues": [{"suffix": resource_id[len(APIM):], "properties": value} for resource_id, value in current.named_values.items()]}


class SourceTests(unittest.TestCase):
    def test_protected_source_restores_complete_graph_and_rejects_wrong_resource_scopes(self):
        current, transport = fixture()
        source = source_fixture(current)
        restored = publication.source_manifest(source, APIM, ["model-a", "model-b"], current.policies[GLOBAL])
        self.assertEqual(restored, current)
        publication.read_policy_graph(restored, transport)
        for key, suffix in (("policies", "/namedValues/wrong"), ("backends", "/policyFragments/wrong"),
                            ("namedValues", "/backends/wrong"), ("policies", "/apis/unselected/operations/bad/extra")):
            with self.subTest(key=key, suffix=suffix):
                invalid = copy.deepcopy(source)
                invalid[key][0]["suffix"] = suffix
                with self.assertRaises(ValueError):
                    publication.source_manifest(invalid, APIM, ["model-a"], current.policies[GLOBAL])
        with self.assertRaises(ValueError):
            publication.source_manifest(source, APIM, ["not-staged"], current.policies[GLOBAL])

    def test_merge_keeps_unselected_api_but_new_shared_fragment_must_match_old_api_graph(self):
        current, transport = fixture()
        previous = source_fixture(current)
        latest = copy.deepcopy(previous)
        latest["apis"] = latest["apis"][:1]
        fragment = next(entry for entry in latest["policies"] if entry["suffix"].startswith("/policyFragments/"))
        fragment["value"] = '<fragment><set-variable name="audit" value="new" /></fragment>'
        merged = publication.merge_sources(previous, latest)
        manifest = publication.source_manifest(merged, APIM, ["model-a", "model-b"], current.policies[GLOBAL])
        self.assertEqual(set(manifest.operations), set(current.operations))
        with self.assertRaises(ValueError):
            publication.read_policy_graph(manifest, transport)

    def connection_fixture(self, *, enabled=True):
        current, transport = fixture()
        host = "ca-test-fixture-portal.internal-fixture.azurecontainerapps.io"
        app_id = APIM.split("/providers/", 1)[0] + "/providers/Microsoft.App/containerApps/ca-test-fixture-portal"
        values = {"core-l7-ingress-enabled": "false", "core-l7-ingress-secret": "ingress-disabled",
                  "core-subscription-profile-enabled": "true" if enabled else "false",
                  "core-subscription-profile-url": "https://" + host + "/api/internal/subscription-profile" if enabled else "https://profile.invalid"}
        # The template comment contains literal {{...}}; it is not a dependency.
        current.policies[GLOBAL] = xml("old").replace("<inbound>", '<!-- {{...}} is explanatory --><inbound>'
            + ''.join('<set-variable name="fixture" value="{{' + name + '}}" />' for name in values))
        transport.inventory[GLOBAL] = response(GLOBAL, {"value": current.policies[GLOBAL]})
        for name, value in values.items():
            resource_id = APIM + "/namedValues/" + name
            transport.inventory[resource_id] = response(resource_id, {"displayName": name, "secret": False, "value": value})
        transport.inventory[app_id] = response(app_id, {"configuration": {"ingress": {"fqdn": host}}})
        plan = {"resourceStem": "test-fixture", "parameters": {"names": {"value": {"keyVault": "kv-test-fixture"}},
                "l7IngressSecretIdentifier": {"value": ""}}, "commonAuthentication": {"core-subscription-key-header": "api-key"}}
        return current, transport, plan, app_id

    def test_core_profile_endpoint_binds_exact_owned_app_readback_and_disabled_l7_placeholder(self):
        current, transport, plan, app_id = self.connection_fixture()
        manifest = publication.complete_named_value_expectations(current, plan, transport)
        publication.read_policy_graph(manifest, transport)
        self.assertTrue(any(method == "GET" and resource_id == app_id for method, resource_id, *_ in transport.calls))
        for mutation in ("same-prefix-other-environment", "wrong-owned-resource", "l7-secret"):
            with self.subTest(mutation=mutation):
                current, transport, plan, app_id = self.connection_fixture()
                if mutation == "same-prefix-other-environment":
                    transport.inventory[APIM + "/namedValues/core-subscription-profile-url"]["body"]["properties"]["value"] = (
                        "https://ca-test-fixture-portal.other-fixture.azurecontainerapps.io/api/internal/subscription-profile")
                elif mutation == "wrong-owned-resource":
                    transport.inventory[app_id]["body"]["id"] = app_id.replace("rg-fixture", "rg-outside-fixture")
                else:
                    transport.inventory[APIM + "/namedValues/core-l7-ingress-secret"]["body"]["properties"]["secret"] = True
                with self.assertRaises(ValueError):
                    publication.complete_named_value_expectations(current, plan, transport)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_disabled_profile_placeholder_needs_no_app_but_cannot_be_enabled(self):
        current, transport, plan, app_id = self.connection_fixture(enabled=False)
        publication.complete_named_value_expectations(current, plan, transport)
        self.assertFalse(any(resource_id == app_id for _, resource_id, *_ in transport.calls))
        transport.inventory[APIM + "/namedValues/core-subscription-profile-enabled"]["body"]["properties"]["value"] = "true"
        with self.assertRaises(ValueError):
            publication.complete_named_value_expectations(current, plan, transport)


class GraphTests(unittest.TestCase):
    def test_full_source_graph_accepts_only_narrow_prebase_metadata_and_preserves_native_products(self):
        current, transport = fixture()
        snapshot = publication.read_policy_graph(current, transport)
        publication.verify_snapshot(snapshot, transport)
        self.assertTrue(any(resource_id.endswith("/revisions") for resource_id in snapshot))
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_http_default_type_and_null_bearer_metadata_keep_relative_revision_scope(self):
        current, transport = fixture()
        for api_id in current.operations:
            properties = transport.inventory[api_id]["body"]["properties"]
            properties.pop("type")
            properties.update(protocols=["http", "https"], apiRevision="1", isCurrent=True, isAgent=False, agent=None)
            properties["subscriptionKeyParameterNames"]["bearer"] = None
        publication.read_policy_graph(current, transport)
        api_id = APIM + "/apis/model-a"
        transport.inventory[api_id + "/revisions"]["body"]["value"][0]["apiId"] = "/apis/model-b;rev=1"
        with self.assertRaises(ValueError):
            publication.read_policy_graph(current, transport)

    def test_policy_drift_copied_marker_and_all_control_dependency_fail_closed(self):
        for mutation in ("marker", "base", "preheader", "revision", "pagination", "native-product", "bearer", "backend-credential", "named", "etag"):
            with self.subTest(mutation=mutation):
                current, transport = fixture()
                api_id = APIM + "/apis/model-a"
                if mutation in ("marker", "base", "preheader"):
                    properties = transport.inventory[api_id + "/policies/policy"]["body"]["properties"]
                    if mutation == "marker":
                        properties["value"] = properties["value"].replace('value="source"', 'value="changed"')
                    elif mutation == "base":
                        properties["value"] = properties["value"].replace('<base />', '', 1)
                    else:
                        properties["value"] = properties["value"].replace('<base />', '<set-header name="Authorization" exists-action="delete" /><base />', 1)
                elif mutation == "revision":
                    transport.inventory[api_id + "/revisions"]["body"]["value"].append({"apiRevision": "2"})
                elif mutation == "pagination":
                    transport.inventory[api_id + "/operations"]["body"]["nextLink"] = "https://management.azure.com/next"
                elif mutation == "native-product":
                    transport.inventory[api_id + "/products"]["body"]["value"] = []
                elif mutation == "bearer":
                    transport.inventory[api_id]["body"]["properties"]["subscriptionKeyParameterNames"]["bearer"] = "enabled"
                elif mutation == "backend-credential":
                    transport.inventory[APIM + "/backends/model-a-backend"]["body"]["properties"]["credentials"] = {"header": {"Authorization": ["unrepresented"]}}
                elif mutation == "named":
                    transport.inventory[APIM + "/namedValues/model-a-audience"]["body"]["properties"]["value"] = "https://wrong.example.com"
                else:
                    transport.inventory[api_id]["body"]["etag"] = "*"
                    transport.inventory[api_id]["headers"]["ETag"] = "*"
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(current, transport)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_fragment_cycle_unknown_source_and_unlinked_product_collision_are_rejected(self):
        for mutation in ("cycle", "unknown", "product", "outside"):
            current, transport = fixture()
            if mutation in ("cycle", "unknown"):
                resource_id = APIM + "/policyFragments/model-audit"
                value = '<fragment><include-fragment fragment-id="' + ("model-audit" if mutation == "cycle" else "unknown") + '" /></fragment>'
                current.policies[resource_id] = value
                transport.inventory[resource_id]["body"]["properties"]["value"] = value
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(current, transport)
            else:
                if mutation == "product":
                    transport.inventory[OPEN]["body"]["properties"]["description"] = "foreign-owner"
                else:
                    transport.inventory[OPEN + "/apis"]["body"]["value"] = [{"id": APIM + "/apis/unreviewed"}]
                with self.assertRaises(ValueError):
                    publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
            self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_snapshot_detects_same_xml_etag_and_collection_drift(self):
        current, transport = fixture()
        snapshot = publication.read_policy_graph(current, transport)
        transport.refresh(GLOBAL, transport.inventory[GLOBAL]["body"]["properties"])
        with self.assertRaisesRegex(ValueError, "drifted"):
            publication.verify_snapshot(snapshot, transport)

    def test_open_product_accepts_missing_null_and_strict_false_approval_in_both_read_paths(self):
        for label, approval in (("missing", None), ("null", None), ("false", False)):
            with self.subTest(approval=label):
                current, transport = fixture()
                api_id = APIM + "/apis/model-a"
                transport.members = {api_id}
                transport.sync_members()
                properties = copy.deepcopy(publication.OPEN_PRODUCT_PROPERTIES)
                if label != "missing":
                    properties["approvalRequired"] = approval
                transport.refresh(OPEN, properties)
                snapshot = publication.read_policy_graph(current, transport)
                product_snapshot, members = publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
                self.assertEqual(members, {api_id})
                self.assertEqual(snapshot[OPEN][2]["body"]["properties"], properties)
                self.assertEqual(product_snapshot[OPEN][2]["body"]["properties"], properties)
                publication.verify_snapshot(snapshot, transport)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_open_product_rejects_invalid_approval_and_missing_fixed_properties_in_both_read_paths(self):
        invalid = []
        for value in (True, "false", "true", 0, 1, 0.0, "", [], {}):
            invalid.append({**publication.OPEN_PRODUCT_PROPERTIES, "approvalRequired": value})
        for key in publication.OPEN_PRODUCT_PROPERTIES:
            properties = copy.deepcopy(publication.OPEN_PRODUCT_PROPERTIES)
            properties.pop(key)
            invalid.append(properties)
        invalid.extend(({**publication.OPEN_PRODUCT_PROPERTIES, "subscriptionRequired": 0},
                        {**publication.OPEN_PRODUCT_PROPERTIES, "state": "published"},
                        {**publication.OPEN_PRODUCT_PROPERTIES, "description": "foreign-owner"}))
        for index, properties in enumerate(invalid):
            for read_graph in (False, True):
                with self.subTest(index=index, read_graph=read_graph):
                    current, transport = fixture()
                    transport.members = {APIM + "/apis/model-a"}
                    transport.sync_members()
                    transport.refresh(OPEN, properties)
                    with self.assertRaises(ValueError):
                        if read_graph:
                            publication.read_policy_graph(current, transport)
                        else:
                            publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
                    self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_open_product_approval_representation_drift_remains_visible_in_raw_snapshot(self):
        current, transport = fixture()
        snapshot, _ = publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
        transport.inventory[OPEN]["body"]["properties"]["approvalRequired"] = None
        publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
        with self.assertRaisesRegex(ValueError, "drifted"):
            publication.verify_snapshot(snapshot, transport)

    def test_open_product_accepts_only_owned_api_aliases_and_keeps_raw_snapshot(self):
        current, transport = fixture()
        api_a, api_b = APIM + "/apis/model-a", APIM + "/apis/model-b"
        for observed_ids in ([api_a, api_b], [OPEN + "/apis/model-a", OPEN + "/apis/model-b"],
                             [api_a, OPEN + "/apis/model-b"]):
            with self.subTest(observed_ids=observed_ids):
                values = [{"id": resource_id} for resource_id in observed_ids]
                transport.refresh(OPEN + "/apis", value=values)
                snapshot, members = publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
                self.assertEqual(members, {api_a, api_b})
                self.assertEqual(snapshot[OPEN + "/apis"][2]["body"]["value"], values)
                publication.verify_snapshot(snapshot, transport)
        transport.refresh(OPEN + "/apis", value=[{"id": OPEN + "/apis/model-a"}, {"id": api_b}])
        with self.assertRaisesRegex(ValueError, "drifted"):
            publication.verify_snapshot(snapshot, transport)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_api_products_accept_owned_aliases_and_read_canonical_product_policy(self):
        for alias_native in (True, False):
            with self.subTest(alias_native=alias_native):
                current, transport = fixture()
                api_a = APIM + "/apis/model-a"
                transport.members = {api_a}
                transport.sync_members()
                values = [{"id": api_a + "/products/" + NATIVE.rsplit("/", 1)[-1] if alias_native else NATIVE},
                          {"id": api_a + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID}]
                transport.refresh(api_a + "/products", value=values)
                snapshot = publication.read_policy_graph(current, transport)
                self.assertEqual(snapshot[api_a + "/products"][2]["body"]["value"], values)
                self.assertIn(NATIVE + "/policies/policy", snapshot)
                self.assertIn(OPEN + "/policies/policy", snapshot)
                publication.verify_snapshot(snapshot, transport)
                transport.refresh(api_a + "/products", value=[{"id": NATIVE}, {"id": OPEN}])
                with self.assertRaisesRegex(ValueError, "drifted"):
                    publication.verify_snapshot(snapshot, transport)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_api_products_reject_unknown_foreign_revision_and_duplicate_aliases(self):
        api_a, api_b = APIM + "/apis/model-a", APIM + "/apis/model-b"
        native_name = NATIVE.rsplit("/", 1)[-1]
        alias_native = api_a + "/products/" + native_name
        invalid = ([NATIVE, alias_native], [alias_native, alias_native],
                   [api_a + "/products/unreviewed"], [api_b + "/products/" + native_name],
                   [api_a + ";rev=2/products/" + native_name], [alias_native + "/extra"],
                   [APIM + "/other/products/" + native_name], [None], [[alias_native]])
        for observed_ids in invalid:
            with self.subTest(observed_ids=observed_ids):
                current, transport = fixture()
                transport.refresh(api_a + "/products", value=[{"id": resource_id} for resource_id in observed_ids])
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(current, transport)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_open_product_rejects_unknown_foreign_revision_and_duplicate_aliases(self):
        api_a = APIM + "/apis/model-a"
        alias_a = OPEN + "/apis/model-a"
        invalid = ([api_a, alias_a], [alias_a, alias_a], [OPEN + "/apis/unreviewed"],
                   [APIM + "/products/other/apis/model-a"],
                   [APIM + "/other-service/products/" + publication.ENTRA_OPEN_PRODUCT_ID + "/apis/model-a"],
                   [OPEN + "/apis/model-a;rev=2"], [api_a + ";rev=2"], [alias_a + "/extra"],
                   [None], [[alias_a]])
        for observed_ids in invalid:
            with self.subTest(observed_ids=observed_ids):
                current, transport = fixture()
                transport.refresh(OPEN + "/apis", value=[{"id": resource_id} for resource_id in observed_ids])
                with self.assertRaises(ValueError):
                    publication.read_open_product(APIM, transport, allowed_api_ids=current.operations)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_open_product_rejects_allowed_ids_outside_selected_api_scope(self):
        for api_id in (APIM + "/other/apis/model-a", OPEN + "/apis/model-a",
                       APIM + "/apis/model-a;rev=2", APIM + "/apis/model-a/extra"):
            with self.subTest(api_id=api_id):
                _, transport = fixture()
                with self.assertRaises(ValueError):
                    publication.read_open_product(APIM, transport, allowed_api_ids=[api_id])
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.current, self.transport = fixture()
        self.api_a, self.api_b = APIM + "/apis/model-a", APIM + "/apis/model-b"
        self.desired = copy.deepcopy(self.current)
        self.desired.policies[GLOBAL] = xml("new")

    def stage(self):
        self.transport.calls.append(("STAGE", "source", None, None))
        return self.desired

    def run_transition(self, old=(), new=(), stage=None):
        return publication.transition(self.current, self.desired.policies[GLOBAL], self.transport, stage or self.stage,
                                      previous_open_api_ids=old, desired_open_api_ids=new, sleep=lambda _: None)

    def test_activation_adds_membership_after_global_full_postflight_and_never_patches_api(self):
        result = self.run_transition(new=[self.api_a])
        writes = [(method, path, headers) for method, path, _, headers in self.transport.calls if method != "GET"]
        self.assertEqual([method for method, *_ in writes], ["STAGE", "PUT", "PUT"])
        self.assertEqual(writes[1][1], GLOBAL)
        self.assertIn("If-Match", writes[1][2])
        self.assertEqual(writes[-1][1], OPEN + "/apis/model-a")
        self.assertTrue(result["employeePathOpen"])
        self.assertFalse(result["runtimeVerified"])
        self.assertTrue(all(self.transport.inventory[api_id]["body"]["properties"]["subscriptionRequired"] for api_id in self.current.operations))

    def test_activation_preserves_subscription_free_approval_readback_and_native_product(self):
        for label, approval in (("missing", None), ("null", None), ("false", False)):
            with self.subTest(approval=label):
                self.setUp()
                properties = copy.deepcopy(publication.OPEN_PRODUCT_PROPERTIES)
                if label != "missing":
                    properties["approvalRequired"] = approval
                self.transport.refresh(OPEN, properties)
                native = copy.deepcopy(self.transport.inventory[NATIVE])
                result = self.run_transition(new=[self.api_a])
                self.assertTrue(result["employeePathOpen"])
                self.assertEqual(self.transport.inventory[OPEN]["body"]["properties"], properties)
                self.assertEqual(self.transport.inventory[NATIVE], native)
                self.assertEqual(self.transport.inventory[OPEN + "/policies/policy"]["body"]["properties"]["value"], publication.OPEN_PRODUCT_POLICY)
                self.assertTrue(all(self.transport.inventory[api_id]["body"]["properties"]["subscriptionRequired"] is True
                                    for api_id in self.current.operations))

    def test_membership_rejects_concurrent_approval_representation_change_and_closes_path(self):
        def mutate_approval(method, resource_id, *_):
            if method == "PUT" and resource_id == OPEN + "/apis/model-a":
                self.transport.inventory[OPEN]["body"]["properties"]["approvalRequired"] = None
        self.transport.hook = mutate_approval
        with self.assertRaises(publication.TransitionError) as error:
            self.run_transition(new=[self.api_a])
        self.assertTrue(error.exception.employee_path_closed)
        self.assertEqual(self.transport.members, set())

    def test_update_closes_actual_old_members_first_and_disable_does_not_use_unpublish(self):
        self.transport.members = {self.api_a}
        self.transport.sync_members()
        result = self.run_transition(old=[self.api_a], new=[self.api_b])
        writes = [(method, path) for method, path, *_ in self.transport.calls if method != "GET"]
        self.assertEqual(writes[0], ("DELETE", OPEN + "/apis/model-a"))
        self.assertEqual(writes[1][0], "STAGE")
        self.assertEqual(self.transport.members, {self.api_b})
        self.assertTrue(result["employeePathInterrupted"])
        self.current = self.desired
        self.desired = copy.deepcopy(self.current)
        self.desired.policies[GLOBAL] = xml("disabled")
        result = self.run_transition(old=[self.api_b])
        self.assertFalse(result["employeePathOpen"])
        self.assertEqual(self.transport.members, set())

    def test_transition_resolves_both_owned_association_id_shapes(self):
        self.transport.members = {self.api_a}
        self.transport.sync_members()

        def association_ids(method, resource_id, *_):
            if method != "GET":
                return None
            if resource_id == OPEN + "/apis":
                response = copy.deepcopy(self.transport.inventory[resource_id])
                for item in response["body"]["value"]:
                    item["id"] = OPEN + "/apis/" + item["id"].rsplit("/", 1)[-1]
                return response
            if resource_id in {self.api_a + "/products", self.api_b + "/products"}:
                response = copy.deepcopy(self.transport.inventory[resource_id])
                for item in response["body"]["value"]:
                    item["id"] = resource_id + "/" + item["id"].rsplit("/", 1)[-1]
                return response
            return None

        self.transport.hook = association_ids
        result = self.run_transition(old=[self.api_a], new=[self.api_b])
        self.assertTrue(result["employeePathOpen"])
        self.assertEqual(self.transport.members, {self.api_b})
        writes = [(method, path) for method, path, *_ in self.transport.calls if method != "GET"]
        self.assertEqual(writes, [("DELETE", OPEN + "/apis/model-a"), ("STAGE", "source"),
                                  ("PUT", GLOBAL), ("PUT", OPEN + "/apis/model-b")])

    def test_preflight_drift_never_starts_closed_staging(self):
        self.transport.inventory[self.api_a]["body"]["properties"]["path"] = "manual"
        with self.assertRaises(ValueError):
            self.run_transition(new=[self.api_a])
        self.assertTrue(all(method == "GET" for method, *_ in self.transport.calls))

    def test_checkpoint_occurs_only_after_verified_closed_stage_before_global_and_open_membership(self):
        def checkpoint():
            self.assertEqual(self.transport.members, set())
            self.assertEqual(self.transport.inventory[GLOBAL]["body"]["properties"]["value"], self.current.policies[GLOBAL])
            self.transport.calls.append(("CHECKPOINT", "protected-source", None, None))
        publication.transition(self.current, self.desired.policies[GLOBAL], self.transport, self.stage,
            desired_open_api_ids=[self.api_a], on_staged=checkpoint, sleep=lambda _: None)
        writes = [method for method, *_ in self.transport.calls if method != "GET"]
        self.assertEqual(writes, ["STAGE", "CHECKPOINT", "PUT", "PUT"])
        self.current, self.transport = fixture()
        def drift():
            self.transport.inventory[self.api_a]["body"]["properties"]["path"] = "partially-deployed"
            return self.desired
        with self.assertRaises(publication.TransitionError):
            publication.transition(self.current, self.desired.policies[GLOBAL], self.transport, drift,
                desired_open_api_ids=[self.api_a], on_staged=checkpoint, sleep=lambda _: None)
        self.assertFalse(any(method == "CHECKPOINT" for method, *_ in self.transport.calls))

    def test_stage_global_etag_drift_is_not_overwritten_and_old_route_stays_closed(self):
        self.transport.members = {self.api_a}
        self.transport.sync_members()
        def drift():
            self.transport.refresh(GLOBAL, self.transport.inventory[GLOBAL]["body"]["properties"])
            return self.desired
        with self.assertRaises(publication.TransitionError) as failure:
            self.run_transition(old=[self.api_a], new=[self.api_a], stage=drift)
        self.assertTrue(failure.exception.employee_path_closed)
        self.assertFalse(any(method == "PUT" and path == GLOBAL for method, path, *_ in self.transport.calls))

    def test_failed_second_open_closes_first_before_restoring_previous_global(self):
        def failure(method, resource_id, *_):
            if method == "PUT" and resource_id == OPEN + "/apis/model-b":
                return {"statusCode": 500, "body": {}, "headers": {}}
        self.transport.hook = failure
        with self.assertRaises(publication.TransitionError) as error:
            self.run_transition(new=[self.api_a, self.api_b])
        self.assertTrue(error.exception.employee_path_closed)
        self.assertTrue(error.exception.recovery_complete)
        self.assertEqual(self.transport.members, set())
        self.assertEqual(self.transport.inventory[GLOBAL]["body"]["properties"]["value"], self.current.policies[GLOBAL])
        writes = [(method, path) for method, path, *_ in self.transport.calls if method != "GET"]
        self.assertEqual(writes[-2:], [("DELETE", OPEN + "/apis/model-a"), ("PUT", GLOBAL)])

    def test_412_and_concurrent_global_change_do_not_authorize_stale_restore(self):
        def failure(method, resource_id, *_):
            if method == "PUT" and resource_id == OPEN + "/apis/model-b":
                self.transport.refresh(GLOBAL, {"format": "rawxml", "value": xml("concurrent")})
                return {"statusCode": 412, "body": {}, "headers": {}}
        self.transport.hook = failure
        with self.assertRaises(publication.TransitionError) as error:
            self.run_transition(new=[self.api_a, self.api_b])
        self.assertTrue(error.exception.employee_path_closed)
        self.assertFalse(error.exception.recovery_complete)
        self.assertEqual(self.transport.inventory[GLOBAL]["body"]["properties"]["value"], xml("concurrent"))

    def test_resume_from_closed_state_repeats_real_readback_and_does_not_need_old_membership(self):
        self.run_transition(old=[self.api_a], new=[self.api_a])
        self.current = self.desired
        self.desired = copy.deepcopy(self.current)
        self.desired.policies[GLOBAL] = xml("next")
        self.transport.members.clear()
        self.transport.sync_members()
        result = self.run_transition(old=[self.api_a], new=[self.api_b])
        self.assertFalse(result["employeePathInterrupted"])
        self.assertEqual(self.transport.members, {self.api_b})


if __name__ == "__main__":
    unittest.main()
