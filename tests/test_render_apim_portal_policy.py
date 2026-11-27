"""Check Portal-only policy assets against the published scope and shared contracts."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers.paths import LocalPaths, private_parent
from deployment.helpers.private_permissions import protect_new_path
import render_apim_portal_policy as portal


EXAMPLES = CORE / "apps/api-gateway/policies/portal-examples"


def load_example(name):
    return json.loads((EXAMPLES / (name + ".example.json")).read_text(encoding="utf-8"))


class ManualPortalPolicyTests(unittest.TestCase):
    def test_claude_sse_inherits_core_auth_and_only_sends_approved_operation_to_foundry(self):
        config = load_example("claude-messages")
        xml = portal.render_portal_policy(config)
        self.assertEqual(xml, (EXAMPLES / "claude-messages.xml").read_text(encoding="utf-8"))
        root = ET.fromstring(xml)
        inbound = root.find("inbound")
        self.assertEqual([node.tag for node in list(inbound)[:4]], ["set-variable", "include-fragment", "base", "include-fragment"])
        self.assertEqual([node.get("fragment-id") for node in list(inbound)[3:6]],
                         ["core-api-request-preparation", "core-api-operation-settings", "core-api-authentication-result"])
        seed, audit, controls = base64.b64decode(inbound[0].get("value")).decode().splitlines()
        self.assertEqual(seed, "http")
        self.assertEqual(json.loads(audit)["backendAuthentication"], "managedIdentity")
        self.assertEqual(json.loads(audit)["llmOperationIds"], [])
        self.assertEqual(json.loads(controls)["operationConstraints"], {
            "messages": {"kind": "registeredOperation", "bodyFormat": "json", "maxRequestBytes": 2048000},
        })
        for profile in ("test", "production"):
            gateway = json.loads((CORE / f"config/{profile}.json").read_text(encoding="utf-8"))["gateway"]
            self.assertEqual(config["operations"][0]["maxRequestBytes"], gateway["wafMaxRequestBodySizeInKb"] * 1024)
        routes = list(inbound.iter("rewrite-uri"))
        self.assertEqual([node.attrib for node in routes], [{"template": "/anthropic/v1/messages", "copy-unmatched-params": "true"}])
        self.assertEqual({node.get("name") for node in inbound.findall("set-query-parameter[@exists-action='delete']")},
                         {"api-key", "subscription-key", "x-api-key"})
        self.assertIn('context.Operation.Id == "messages" && context.Request.Method == "POST"',
                      next(node.get("condition") for node in inbound.iter("when") if node.find("rewrite-uri") is not None))
        product_gate = next(node.get("condition") for node in inbound.iter("when") if "context.Api.Id" in node.get("condition", ""))
        for expected in ('context.Api.Id == "manual-claude-messages"', 'context.Product.Id == "manual-model-users"',
                         'context.Product.Id == "manual-entra-users"'):
            self.assertIn(expected, product_gate)
        self.assertEqual(inbound.find("set-backend-service").attrib, {"backend-id": "core-audit-relay"})
        mi = inbound.find("authentication-managed-identity")
        self.assertEqual(mi.attrib, {"resource": "{{core-audit-relay-audience}}", "ignore-error": "false"})
        for names in ("Authorization", "{{core-subscription-key-header}}", "x-api-key", "api-key", "X-Core-L7-Ingress", "Cookie"):
            header = inbound.find(f"set-header[@name='{names}']")
            self.assertEqual(header.get("exists-action"), "delete")
            self.assertLess(list(inbound).index(header), list(inbound).index(mi))
        self.assertIsNone(root.find(".//set-body"))
        self.assertIsNone(inbound.find("set-header[@name='anthropic-version']"))
        self.assertIsNone(inbound.find("set-header[@name='anthropic-beta']"))
        self.assertEqual([node.attrib for node in root.iter("forward-request")], [{"timeout": "240", "buffer-response": "false"}])
        self.assertIsNotNone(root.find("outbound/include-fragment[@fragment-id='core-backend-audit']"))
        self.assertIsNotNone(root.find("on-error/base"))

    def test_rest_reuses_quota_and_emits_scoped_fallback_only_when_shared_rate_is_disabled(self):
        config = load_example("rest")
        root = ET.fromstring(portal.render_portal_policy(config))
        self.assertIsNone(root.find(".//authentication-managed-identity"))
        self.assertEqual(root.find("backend/forward-request").get("buffer-response"), "true")
        inbound = root.find("inbound")
        peer_profile = inbound.find(".//send-request/set-url")
        self.assertEqual(peer_profile.text, "{{core-subscription-profile-url}}")
        self.assertIsNotNone(inbound.find("include-fragment[@fragment-id='core-rate-limit']"))
        when = next(node for node in inbound.iter("when") if node.find("rate-limit-by-key") is not None)
        self.assertIn('"subscriptionKey"', when.get("condition"))
        self.assertIn('"gatewayRateDecision"', when.get("condition"))
        limit = when.find("rate-limit-by-key")
        self.assertIn("context.Api.Id", limit.get("counter-key"))
        self.assertIn("context.Subscription.Id", limit.get("counter-key"))
        handler = root.find("on-error/choose/when")
        self.assertIn('context.LastError.PolicyId == "core-rate-limit"', handler.get("condition"))
        self.assertIn('context.LastError.PolicyId == "manual-key-rate-limit"', handler.get("condition"))
        self.assertEqual(handler.find("return-response/set-status").get("code"), "429")
        owned_fragments = {item.stem for item in (portal.POLICIES / "fragments").glob("*/*/*.xml")}
        used_fragments = {item.get("fragment-id") for item in root.iter("include-fragment")}
        self.assertLessEqual(used_fragments, owned_fragments)
        self.assertEqual([node.get("value") for node in inbound.findall("set-variable[@name='gatewayEvidenceDirection']")],
                         ["snapshot", "request"])
        self.assertIsNotNone(root.find("outbound/include-fragment[@fragment-id='core-http-body-evidence']"))

    def test_invalid_routes_owned_products_and_backend_credentials_fail_closed_in_renderer(self):
        sample = load_example("claude-messages")
        for settings in ({"apiId": "openai-inference-v1"}, {"keyProductId": "core-entra-model-users"},
                         {"keyProductId": "manual-entra-users"}, {"backendId": "openai-inference-v1-backend"},
                         {"backendAuthentication": []}, {"backendAuthentication": {"mode": "managedIdentity", "resource": 1}},
                         {"backendAuthentication": {"mode": "managedIdentity", "resource": "https://example.com/?credential=synthetic"}},
                         {"responseTransport": "websocket"}, {"responseTransport": "rest"}, {"bodyAudit": True},
                         {"keyFallbackRate": {"calls": 0, "renewalPeriodSeconds": 60}},
                         {"operations": [{**sample["operations"][0], "backendPath": "//example.com"}]},
                         {"operations": [{**sample["operations"][0], "backendPath": "/v1/../private"}]},
                         {"operations": [{**sample["operations"][0], "backendPath": "/v1/{destination}"}]},
                         {"operations": [{**sample["operations"][0], "backendPath": "/other/resource"}]},
                         {"operations": [{**sample["operations"][0], "backendPath": "/" + "x" * 1025}]},
                         {"operations": [copy.deepcopy(sample["operations"][0])] * 2}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                portal.render_portal_policy({**sample, **settings})

    def test_manual_policy_respects_the_apim_document_size_limit(self):
        sample = load_example("rest")
        operation = sample["operations"][0]
        sample["operations"] = [
            {**operation, "operationId": f"read-{index}", "backendPath": ("/" + "x" * 1000) * 8}
            for index in range(80)
        ]
        with self.assertRaisesRegex(ValueError, "policy document size limit"):
            portal.render_portal_policy(sample)

    def test_managed_identity_requires_a_concrete_https_audience(self):
        approved = "https://example.com:443/synthetic-resource/v1"
        authentication = portal.backend_auth_xml({"mode": "managedIdentity", "resource": approved})
        self.assertEqual(ET.fromstring(authentication).get("resource"), approved)
        for audience in ("https://{{example-audience}}", "https://example.com/{{example-path}}",
                         "https://example.com:444/synthetic-resource", "https://example.com:/synthetic-resource",
                         "https://example.com/../synthetic-resource", "https://example.com/%7Bexample%7D",
                         "https://example.com/synthetic-resource#example", "https://example.com/" + "x" * 2048):
            with self.subTest(audience=audience[:80]), self.assertRaises(ValueError):
                portal.backend_auth_xml({"mode": "managedIdentity", "resource": audience})

    def test_scope_map_matches_initial_api_paths_operations_and_generated_mcp(self):
        table = (EXAMPLES / "scope-map.md").read_text(encoding="utf-8")
        rows = re.findall(r"\[([a-z0-9-]+)\]\(../../apis/\1\.json\) \| `([^`]+)` \| ([^|]+) \|", table)
        mapped = {name: (path, re.findall(r"`([^`]+)`", operations)) for name, path, operations in rows}
        definitions = [json.loads(path.read_text(encoding="utf-8")) for path in (CORE / "apps/api-gateway/apis").glob("*.json")]
        self.assertEqual(len(rows), len(definitions))
        self.assertEqual(mapped, {item["name"]: (item["path"], [operation["name"] for operation in item["operations"]])
                                  for item in definitions})
        for reference in re.findall(r"\]\(([^)]+)\)", table):
            self.assertTrue((EXAMPLES / reference.split("#", 1)[0]).exists(), reference)
        for item in definitions:
            if "mcp" in item:
                operations = "、".join(f"`{operation}`" for operation in item["mcp"]["operationNames"])
                self.assertIn(f'`{item["mcp"]["name"]}` | `{item["mcp"]["path"]}` | {operations} |', table)

    def test_cli_preserves_existing_output_and_writes_only_to_selected_protected_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = LocalPaths(Path(temporary), "test")
            source = private_parent(runtime.root / "generated" / "manual-spec.json", runtime.repository)
            with source.open("x", encoding="utf-8") as stream:
                protect_new_path(source)
                json.dump(load_example("rest"), stream)
            output = runtime.root / "generated" / "manual-policy.xml"
            command = [sys.executable, "-B", str(CORE / "scripts/render_apim_portal_policy.py"),
                       "--runtime-root", str(runtime.repository), "--environment", "test", "--config", str(source), "--output"]
            self.assertEqual(subprocess.run([*command, str(runtime.repository / "unsafe.xml")], capture_output=True).returncode, 1)
            self.assertFalse((runtime.repository / "unsafe.xml").exists())
            self.assertEqual(subprocess.run([*command, str(output)], capture_output=True).returncode, 0)
            self.assertEqual(ET.parse(output).getroot().tag, "policies")
            original = output.read_bytes()
            self.assertEqual(subprocess.run([*command, str(output)], capture_output=True).returncode, 1)
            self.assertEqual(output.read_bytes(), original)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
