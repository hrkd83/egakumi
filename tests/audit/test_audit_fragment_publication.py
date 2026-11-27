"""共通監査の2部品を、生成元と実読取の両方で限定した位置へ結び付ける。"""
import copy
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import AUDIT_FRAGMENT_FILES, render_audit_fragments
from deployment.helpers.apim_ingress import TRUSTED_INGRESS_ID, render_trusted_ingress_fragment
from deployment.helpers.apim_browser_exception import BROWSER_EXCEPTION_ID, render_browser_exception_fragment
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments
from deployment.helpers.apim_authentication import render_service_policy
from tests.gateway.publication.test_entra_publication import APIM, GLOBAL, fixture, response, source_fixture
import entra_publication as publication


def audit_graph():
    current, transport = fixture()
    current.policies[GLOBAL] = render_service_policy()
    current.policies.update({APIM + "/policyFragments/" + name: value
                             for name, value in render_audit_fragments().items()})
    current.policies[APIM + "/policyFragments/" + TRUSTED_INGRESS_ID] = render_trusted_ingress_fragment()
    current.policies[APIM + "/policyFragments/" + BROWSER_EXCEPTION_ID] = render_browser_exception_fragment()
    current.policies.update({APIM + "/policyFragments/" + name: value
                             for name, value in render_common_authentication_fragments(entra_enabled=False).items()})
    # Only the three shared values referenced by the generated key-path global.
    # Their values and all IDs are independent synthetic fixtures.
    values = {"core-subscription-key-header": "api-key", "core-l7-ingress-enabled": "false",
              "core-l7-ingress-secret": "synthetic-ingress-value"}
    for name, value in values.items():
        resource_id = APIM + "/namedValues/" + name
        properties = {"displayName": name, "secret": False, "value": value}
        current.named_values[resource_id] = properties
        transport.inventory[resource_id] = response(resource_id, properties)
    for resource_id, value in current.policies.items():
        transport.inventory[resource_id] = response(resource_id, {"format": "rawxml", "value": value})
    return current, transport


def changed_xml(value, mutate):
    root = ET.fromstring(value)
    mutate(root)
    return ET.tostring(root, encoding="unicode")


class AuditFragmentPublicationTests(unittest.TestCase):
    def test_generated_source_and_get_graph_accept_current_fragments(self):
        current, transport = audit_graph()
        source = source_fixture(current)
        manifest = publication.source_manifest(source, APIM, ["model-a", "model-b"], current.policies[GLOBAL])
        self.assertEqual(manifest, current)
        snapshot = publication.read_policy_graph(manifest, transport)
        for identifier in AUDIT_FRAGMENT_FILES:
            self.assertIn(APIM + "/policyFragments/" + identifier, snapshot)
        self.assertTrue(transport.calls)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_api_and_unrelated_fragment_cannot_write_or_call_common_audit_parts(self):
        for variable, identifier in (("gatewayRequestContext", "core-request-context"),
                                     ("gatewayAuthenticatedSubject", "core-audit-authenticated-subject")):
            for mode in ("api-writer", "other-fragment-writer", "api-include", "fragment-include"):
                with self.subTest(variable=variable, mode=mode):
                    current, _ = audit_graph()
                    if mode in ("api-writer", "api-include"):
                        resource_id = APIM + "/apis/model-a/policies/policy"
                        tag = "set-variable" if mode == "api-writer" else "include-fragment"
                        attributes = ({"id": identifier, "name": variable, "value": "untrusted"}
                                      if tag == "set-variable" else {"id": identifier, "fragment-id": identifier})
                        current.policies[resource_id] = changed_xml(current.policies[resource_id],
                            lambda root: ET.SubElement(root.find("inbound"), tag, attributes))
                    else:
                        tag = "set-variable" if mode == "other-fragment-writer" else "include-fragment"
                        attributes = ({"id": identifier, "name": variable, "value": "untrusted"}
                                      if tag == "set-variable" else {"id": identifier, "fragment-id": identifier})
                        root = ET.Element("fragment")
                        ET.SubElement(root, tag, attributes)
                        current.policies[APIM + "/policyFragments/unrelated"] = ET.tostring(root, encoding="unicode")
                    with self.assertRaises(ValueError):
                        publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])

    def test_missing_duplicated_moved_and_nonempty_common_references_are_rejected(self):
        def request(root):
            return root.find("inbound/include-fragment[@fragment-id='core-request-context']")

        def remove(root):
            root.find("inbound").remove(request(root))

        def duplicate(root):
            root.find("inbound").append(copy.deepcopy(request(root)))

        def move_outbound(root):
            node = request(root)
            root.find("inbound").remove(node)
            root.find("outbound").append(node)

        def move_nested(root):
            node = request(root)
            root.find("inbound").remove(node)
            branch = ET.SubElement(root.find("inbound"), "choose")
            ET.SubElement(branch, "when", {"condition": "@(true)"}).append(node)

        def reverse(root):
            inbound = root.find("inbound")
            node = request(root)
            inbound.remove(node)
            inbound.append(node)

        mutations = {
            "missing": remove, "duplicate": duplicate, "outbound": move_outbound,
            "nested": move_nested, "order": reverse,
            "wrong-id": lambda root: request(root).set("id", "copied-audit-id"),
            "extra-attribute": lambda root: request(root).set("unexpected", "value"),
            "child": lambda root: ET.SubElement(request(root), "set-variable", {"name": "hidden", "value": "true"}),
            "text": lambda root: setattr(request(root), "text", "unexpected text"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                current, _ = audit_graph()
                current.policies[GLOBAL] = changed_xml(current.policies[GLOBAL], mutate)
                with self.assertRaises(ValueError):
                    publication._audit_fragment_scope(current.policies, APIM)

    def test_references_require_complete_leaf_source_and_fixed_writer_attributes(self):
        for mode in ("absent-source", "wrong-variable", "wrong-writer-id", "extra-writer-attribute", "multiple-writers"):
            with self.subTest(mode=mode):
                current, _ = audit_graph()
                resource_id = APIM + "/policyFragments/core-request-context"
                if mode == "absent-source":
                    del current.policies[resource_id]
                else:
                    def mutate(root):
                        if mode == "wrong-variable": root[0].set("name", "otherVariable")
                        elif mode == "wrong-writer-id": root[0].set("id", "otherWriter")
                        elif mode == "extra-writer-attribute": root[0].set("extra", "unexpected")
                        else: root.append(copy.deepcopy(root[0]))
                    current.policies[resource_id] = changed_xml(current.policies[resource_id], mutate)
                with self.assertRaises(ValueError):
                    publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])

    def test_fragment_sections_base_and_nested_includes_are_rejected(self):
        for tag in (*publication.SECTIONS, "base", "include-fragment"):
            with self.subTest(tag=tag):
                value = '<fragment><' + tag + (' fragment-id="unrelated"' if tag == "include-fragment" else '') + ' /></fragment>'
                with self.assertRaises(ValueError):
                    publication._xml(value, fragment=True)

    def test_fragment_limit_is_measured_as_utf8_bytes_at_512_kib(self):
        prefix, suffix = '<fragment><!--', '--><set-variable name="audit" value="ok" /></fragment>'
        budget = 512 * 1024 - len((prefix + suffix).encode("utf-8"))
        padding = "\u3042" * (budget // 3) + "x" * (budget % 3)
        boundary = prefix + padding + suffix
        self.assertEqual(len(boundary.encode("utf-8")), 512 * 1024)
        self.assertEqual(publication._xml(boundary, fragment=True).tag, "fragment")
        with self.assertRaises(ValueError):
            publication._xml(prefix + padding + "x" + suffix, fragment=True)

    def test_graph_readback_rejects_changed_fragment_expression(self):
        current, transport = audit_graph()
        fragment_id = APIM + "/policyFragments/core-request-context"
        actual = transport.inventory[fragment_id]["body"]["properties"]
        actual["value"] = changed_xml(actual["value"], lambda root: root[0].set("value", '@{ return "{}"; }'))
        with self.assertRaises(ValueError):
            publication.read_policy_graph(current, transport)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))


if __name__ == "__main__":
    unittest.main()
