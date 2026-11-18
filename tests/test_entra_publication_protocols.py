"""全protocolの生成元照合と初回stageの衝突拒否。合成transportだけを使う。"""
import copy
import unittest
from pathlib import Path
from policy_fixture_paths import policy_fixture_path

from test_entra_publication import APIM, GLOBAL, NATIVE, fixture, response, source_fixture
import entra_publication as publication


def protocol_fixture():
    current, transport = fixture()
    children = {}
    for api_id, names in current.operations.items():
        current.api_properties[api_id]["protocols"] = ["http", "https"]
        transport.inventory[api_id]["body"]["properties"]["protocols"] = ["http", "https"]
        for name in names:
            child_id = api_id + "/operations/" + name
            children[child_id] = {"method": "POST", "urlTemplate": "/responses", "templateParameters": []}
            transport.inventory[child_id] = response(child_id, children[child_id])
    for name, kind in (("socket", "websocket"), ("rpc", "grpc"), ("proxy", "mcp"), ("native-tool", "mcp"), ("credential-manager-login", "http")):
        api_id = APIM + "/apis/" + name
        properties = {"type": kind, "path": "tools/" + name, "protocols": ["http", "https"],
                      "subscriptionRequired": True, "subscriptionKeyParameterNames": {"header": "api-key", "query": "subscription-key"}}
        names = ("onHandshake",) if kind == "websocket" else ("start",) if kind == "http" else ()
        if kind == "websocket":
            properties.update(serviceUrl="wss://provider.example.com/stream", protocols=["ws", "wss"])
        elif kind == "grpc":
            properties.update(serviceUrl="https://provider.example.com", protocols=["https"], format="grpc",
                              value='syntax = "proto3"; service Probe { rpc Echo (X) returns (X); }')
        elif name == "proxy":
            properties.update(backendId="model-a-backend", mcpProperties={"transportType": "streamable", "endpoints": {"mcp": {"uriTemplate": "/mcp"}}})
        current.api_properties[api_id] = properties
        current.operations[api_id] = names
        current.products[api_id] = ("model-users",)
        transport.inventory[api_id] = response(api_id, properties)
        transport.inventory[api_id + "/products"] = response(api_id + "/products", value=[{"id": NATIVE}])
        transport.inventory[api_id + "/operations"] = response(api_id + "/operations", value=[{"id": api_id + "/operations/" + item} for item in names])
        transport.inventory[api_id + "/revisions"] = response(api_id + "/revisions", value=[{"apiId": "/apis/" + name + ";rev=1", "apiRevision": "1", "isCurrent": True, "isOnline": True}])
        if kind != "websocket":
            current.policies[api_id + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
        for operation in names:
            operation_id = api_id + "/operations/" + operation
            current.policies[operation_id + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
            if kind != "websocket":
                children[operation_id] = {"method": "POST", "urlTemplate": "/start", "responses": []}
                transport.inventory[operation_id] = response(operation_id, children[operation_id])
    tool_id = APIM + "/apis/native-tool/tools/redact"
    children[tool_id] = {"displayName": "redact", "operationId": APIM + "/apis/model-a/operations/responses"}
    schema_id = APIM + "/apis/model-a/schemas/public-contract"
    children[schema_id] = {"contentType": "application/vnd.oai.openapi.components+json", "document": {"components": {"schemas": {"Input": {"type": "object"}}}}}
    for api_id in current.operations:
        for kind in ("tools", "schemas"):
            inventory = [{"id": resource_id} for resource_id in children if resource_id.startswith(api_id + "/" + kind + "/")]
            transport.inventory[api_id + "/" + kind] = response(api_id + "/" + kind, value=inventory)
    for resource_id, properties in children.items():
        transport.inventory[resource_id] = response(resource_id, properties)
    for resource_id, value in current.policies.items():
        transport.inventory[resource_id] = response(resource_id, {"format": "rawxml", "value": value})
    manifest = publication.PolicyManifest(current.apim_id, current.policies, current.operations, current.products,
                                         current.backends, current.named_values, current.api_properties, children, 2)
    return manifest, transport


class ProtocolGraphTests(unittest.TestCase):
    def test_all_protocols_and_protected_connection_match_the_exact_source(self):
        manifest, transport = protocol_fixture()
        publication.read_policy_graph(manifest, transport)
        source = source_fixture(manifest)
        source.update(schemaVersion=2, apiChildren=[{"suffix": resource_id[len(APIM):], "properties": properties}
                                                  for resource_id, properties in manifest.api_children.items()])
        restored = publication.source_manifest(source, APIM, [resource_id.rsplit("/", 1)[-1] for resource_id in manifest.operations], manifest.policies[GLOBAL])
        self.assertEqual(restored, manifest)

    def test_protocol_routing_tools_schemas_and_operations_cannot_drift(self):
        for resource_id, field, value in (
                (APIM + "/apis/socket", "serviceUrl", "wss://other.example.com"),
                (APIM + "/apis/proxy", "backendId", "other"),
                (APIM + "/apis/rpc", "value", 'syntax = "proto3"; service Other {}'),
                (APIM + "/apis/native-tool/tools/redact", "operationId", APIM + "/apis/model-b/operations/responses"),
                (APIM + "/apis/model-a/schemas/public-contract", "document", {}),
                (APIM + "/apis/model-a/operations/responses", "urlTemplate", "/other")):
            with self.subTest(resource_id=resource_id, field=field):
                manifest, transport = protocol_fixture()
                transport.inventory[resource_id]["body"]["properties"][field] = value
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(manifest, transport)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_unrepresented_children_and_websocket_api_policy_are_rejected(self):
        for suffix in ("/apis/rpc/operations", "/apis/proxy/tools", "/apis/model-a/schemas", "/apis/socket/policies/policy"):
            with self.subTest(suffix=suffix):
                manifest, transport = protocol_fixture()
                resource_id = APIM + suffix
                transport.inventory[resource_id] = response(resource_id, {"value": publication.OPEN_PRODUCT_POLICY}) if suffix.endswith("policy") else response(resource_id, value=[{"id": resource_id + "/unreviewed"}])
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(manifest, transport)

    def test_native_tool_must_reference_an_operation_in_the_same_source_graph(self):
        manifest, transport = protocol_fixture()
        resource_id = APIM + "/apis/native-tool/tools/redact"
        manifest.api_children[resource_id]["operationId"] = APIM + "/apis/unreviewed/operations/invoke"
        transport.inventory[resource_id]["body"]["properties"] = copy.deepcopy(manifest.api_children[resource_id])
        with self.assertRaisesRegex(ValueError, "represented REST"):
            publication.read_policy_graph(manifest, transport)

    def test_only_the_fixed_preinheritance_metadata_guard_is_allowed(self):
        manifest, transport = protocol_fixture()
        resource_id = APIM + "/apis/proxy/policies/policy"
        for guard, allowed in ((publication.PRM_GUARD, True), (publication.PRM_GUARD.replace('code="404"', 'code="200"'), False),
                               ('<set-header name="Authorization" exists-action="delete" />', False)):
            with self.subTest(allowed=allowed):
                value = publication.OPEN_PRODUCT_POLICY.replace("<inbound>", "<inbound>" + guard)
                manifest.policies[resource_id] = value
                transport.inventory[resource_id] = response(resource_id, {"value": value})
                if allowed:
                    publication.read_policy_graph(manifest, transport)
                else:
                    with self.assertRaises(ValueError):
                        publication.read_policy_graph(manifest, transport)

    def test_grpc_missing_import_readback_is_not_assumed_compatible(self):
        manifest, transport = protocol_fixture()
        properties = transport.inventory[APIM + "/apis/rpc"]["body"]["properties"]
        properties.pop("format")
        properties.pop("value")
        with self.assertRaisesRegex(ValueError, "routing/key"):
            publication.read_policy_graph(manifest, transport)


def fresh_fixture():
    plan = {"apimId": APIM, "publishedApiIds": [APIM + "/apis/new-api"], "parameters": {
        "apiDefinitions": {"value": [{"name": "new-api", "productId": "model-users", "keySubscriptions": []}]},
        "names": {"value": {"keyVault": "kv-fixture"}}, "l7IngressSecretIdentifier": {"value": ""}}}
    _, transport = fixture()
    transport.inventory = {GLOBAL: response(GLOBAL, {"value": '<policies><inbound /><backend /><outbound /><on-error /></policies>'})}
    for kind in ("apis", "policyFragments", "backends", "products", "namedValues", "subscriptions", "loggers", "diagnostics"):
        transport.inventory[APIM + "/" + kind] = response(APIM + "/" + kind, value=[])
    return plan, transport


class FreshPreflightTests(unittest.TestCase):
    def test_fresh_plan_preserves_unrelated_api_keys_products_and_global(self):
        plan, transport = fresh_fixture()
        for kind, name in (("apis", "unrelated;rev=2"), ("products", "unrelated"), ("subscriptions", "unrelated")):
            transport.inventory[APIM + "/" + kind]["body"]["value"] = [{"id": APIM + "/" + kind + "/" + name}]
        snapshot = publication.read_fresh_publication(plan, transport)
        publication.verify_snapshot(snapshot, transport)
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_existing_api_revision_or_shared_child_is_not_a_fresh_install(self):
        for kind, name in (("apis", "new-api;rev=2"), ("policyFragments", "shared"), ("backends", "shared"),
                           ("products", "released-apis"), ("namedValues", "new-api-backend-audience"),
                           ("loggers", "azuremonitor"), ("diagnostics", "azuremonitor")):
            with self.subTest(kind=kind):
                plan, transport = fresh_fixture()
                transport.inventory[APIM + "/" + kind]["body"]["value"] = [{"id": APIM + "/" + kind + "/" + name}]
                with self.assertRaises(ValueError):
                    publication.read_fresh_publication(plan, transport)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_pagination_or_global_drift_prevents_the_first_write(self):
        plan, transport = fresh_fixture()
        transport.inventory[APIM + "/apis"]["body"]["nextLink"] = "https://management.azure.com/next"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            publication.read_fresh_publication(plan, transport)
        plan, transport = fresh_fixture()
        snapshot = publication.read_fresh_publication(plan, transport)
        transport.refresh(GLOBAL, {"value": publication.OPEN_PRODUCT_POLICY})
        with self.assertRaisesRegex(ValueError, "drifted"):
            publication.verify_snapshot(snapshot, transport)


class GeneratedSourceTests(unittest.TestCase):
    def test_compiled_source_represents_the_real_assignments_for_every_protocol(self):
        from bicep_helpers import compile_bicep
        from test_api_protocols import bound_external_coverage, configuration
        from test_api_publication_size import SizeExpression, expand
        from deployment.helpers import credential_login
        core = Path(__file__).resolve().parents[1]
        config = configuration.normalize_configuration(bound_external_coverage(), base_directory=core / "config")
        definitions = config["apiDefinitions"]
        for api in definitions:
            api.setdefault("backendUrl", "https://provider.example.com" + api.get("backendPath", ""))
            for operation in api["operations"]:
                if "backendOverride" in operation:
                    operation["backendOverride"].setdefault("backendUrl", "https://provider.example.com")
        template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        parameters = {"apiDefinitions": definitions, "names": {"apim": "apim-test-fixture"}, "publishApis": True,
                      "rateLimits": config["gateway"]["rateLimits"], "credentialLogin": credential_login.settings(config)}
        expanded = expand(template, parameters)
        reader = SizeExpression("[true()]", template["variables"], {}, template.get("functions", []))
        source = reader.resolve(template["outputs"]["entraPublicationSource"]["value"], expanded["parameters"])
        self.assertEqual(source["schemaVersion"], 2)
        expected_names = {api["name"] for api in definitions} | {api["mcp"]["name"] for api in definitions if "mcp" in api} | {"credential-manager-login"}
        self.assertEqual({entry["name"] for entry in source["apis"]}, expected_names)
        self.assertNotIn("credential-manager-browser", expected_names)
        actual = {}
        for resource in expanded["resources"]:
            if not resource["type"].startswith("Microsoft.ApiManagement/service/") or "properties" not in resource:
                continue
            kinds = resource["type"].split("/")[2:]
            names = resource["name"].split("/")[1:]
            suffix = "/" + "/".join(part for pair in zip(kinds, names) for part in pair)
            actual[suffix] = resource["properties"]
        for entry in source["apis"]:
            self.assertTrue(publication._contains(actual["/apis/" + entry["name"]], entry["properties"]), entry["name"])
        for key, field in (("policies", "value"), ("backends", "properties"), ("namedValues", "properties"), ("apiChildren", "properties")):
            for entry in source[key]:
                observed = actual[entry["suffix"]]
                self.assertEqual(observed["value"] if field == "value" else observed, entry[field], entry["suffix"])
        # Compare deployment XML against the independent Python renderer, not
        # just two fields emitted by the same compiled ARM template.
        from deployment.helpers.apim_audit import render_audit_fragments, render_audit_policy
        from deployment.helpers.apim_ingress import render_trusted_ingress_fragment
        from deployment.helpers.apim_browser_exception import render_browser_exception_fragment
        source_policies = {entry["suffix"]: entry["value"] for entry in source["policies"]}
        common_fragments = {**render_audit_fragments(), "core-trusted-ingress": render_trusted_ingress_fragment(),
                            "core-credential-browser-exception": render_browser_exception_fragment(parameters["credentialLogin"]),
                            "core-gateway-telemetry": render_audit_policy(policy_fixture_path(core / "apps/gateway/apim/policies", "core-gateway-telemetry.xml").read_text(encoding="utf-8"))}
        for identifier, expected in common_fragments.items():
            suffix = "/policyFragments/" + identifier
            self.assertEqual(actual[suffix]["value"].replace("\r\n", "\n"), expected, identifier)
            self.assertEqual(source_policies[suffix].replace("\r\n", "\n"), expected, identifier)
        import xml.etree.ElementTree as ET
        # Every source explanation must remain visible in its canonical Azure
        # fragment, even when repeated inline copies point readers here.
        for identifier, filename in (("core-request-context", "core-request-context.cs"),
                                     ("core-audit-authenticated-subject", "core-audit-authenticated-subject.cs"),
                                     ("core-credential-browser-exception", "core-credential-browser-exception.cs"),
                                     ("core-gateway-telemetry", "core-gateway-telemetry.cs")):
            expression_source = policy_fixture_path(core / "apps/gateway/apim/policies", filename).read_text(encoding="utf-8")
            comments = ["/*" + line.lstrip()[2:].replace("*/", "* /") + " */"
                        for line in expression_source.splitlines() if line.lstrip().startswith("//")]
            root = ET.fromstring(actual["/policyFragments/" + identifier]["value"])
            deployed_text = "\n".join(value for node in root.iter() for value in (*node.attrib.values(), node.text or ""))
            self.assertTrue(comments, filename)
            for comment in comments:
                self.assertIn(comment, deployed_text, identifier)
        cm_suffix = '/apis/credential-manager-login/policies/policy'
        cm_source = next(entry['value'] for entry in source['policies'] if entry['suffix'] == cm_suffix)
        cm_inbound = ET.fromstring(cm_source).find('inbound')
        self.assertEqual([node.tag for node in cm_inbound], ['base'])
        self.assertEqual(cm_source, actual[cm_suffix]['value'])
        self.assertTrue(all('/credential-manager-browser' not in entry['suffix']
                            for key in ('policies', 'apiChildren') for entry in source[key]))
        browser = actual['/apis/credential-manager-browser']
        self.assertIs(browser['subscriptionRequired'], False)
        browser_operations = [resource for suffix, resource in actual.items()
                              if suffix.startswith('/apis/credential-manager-browser/operations/') and suffix.endswith('/policies/policy')]
        self.assertTrue(browser_operations)
        for operation in ('callback', 'identity-callback'):
            browser_policy = actual['/apis/credential-manager-browser/operations/' + operation + '/policies/policy']['value']
            self.assertNotIn('gatewayEntraOperationSupported', browser_policy)
        callback = actual['/apis/credential-manager-browser/operations/callback/policies/policy']['value']
        self.assertIn('credentialTransaction', callback)
        identity = ET.fromstring(actual['/apis/credential-manager-browser/operations/identity-callback/policies/policy']['value'])
        self.assertEqual(identity.find('./inbound/rewrite-uri').get('template'), '/v1/identity-callback')
        self.assertEqual(identity.find('./inbound/set-backend-service').get('backend-id'), 'credential-broker')
        self.assertIsNotNone(identity.find('./inbound/set-header[@name="X-Core-Broker-Key"]'))
        browser_source = reader.resolve(template['outputs']['credentialBrowserPublicationSource']['value'], expanded['parameters'])
        self.assertEqual(browser_source['schemaVersion'], 1)
        self.assertEqual(browser_source['name'], 'credential-manager-browser')
        self.assertIs(browser_source['apiPolicyAbsent'], True)
        self.assertNotIn('/apis/credential-manager-browser/policies/policy', actual)
        self.assertTrue(publication._contains(browser, browser_source['properties']))
        self.assertEqual({entry['name'] for entry in browser_source['operations']},
                         {'launch', 'launch-post', 'identity-callback', 'callback'})
        for entry in browser_source['operations']:
            self.assertEqual(actual['/apis/credential-manager-browser/operations/' + entry['name']], entry['properties'])
        self.assertEqual(len(browser_source['policies']), 4)
        for entry in browser_source['policies']:
            self.assertEqual(actual[entry['suffix']]['value'], entry['value'])
