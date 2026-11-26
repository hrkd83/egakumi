"""入口確認の部品を共通inboundへ限定し、配備前と実読取で欠落・改変を拒否する。"""
import copy
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_ingress import (
    TRUSTED_INGRESS_ID, render_trusted_ingress_fragment,
)
from test_audit_fragment_publication import audit_graph, changed_xml
from test_entra_publication import APIM, GLOBAL, response, source_fixture
import entra_publication as publication


INGRESS = APIM + "/policyFragments/" + TRUSTED_INGRESS_ID


def ingress_reference(root):
    return root.find("inbound/include-fragment[@fragment-id='" + TRUSTED_INGRESS_ID + "']")


def source_rejected(test, current):
    with test.assertRaises(ValueError):
        publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])


class TrustedIngressPublicationTests(unittest.TestCase):

    def test_current_fragment_source_has_a_get_only_graph(self):
        current, transport = audit_graph()
        restored = publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])
        self.assertEqual(restored, current)
        snapshot = publication.read_policy_graph(restored, transport)
        self.assertIn(INGRESS, snapshot)
        self.assertTrue(transport.calls)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_missing_duplicate_moved_nonempty_or_changed_include_is_rejected(self):
        def remove(root):
            root.find("inbound").remove(ingress_reference(root))

        def duplicate(root):
            root.find("inbound").append(copy.deepcopy(ingress_reference(root)))

        def outbound(root):
            node = ingress_reference(root)
            root.find("inbound").remove(node)
            root.find("outbound").append(node)

        def nested(root):
            node = ingress_reference(root)
            root.find("inbound").remove(node)
            branch = ET.SubElement(root.find("inbound"), "choose")
            ET.SubElement(branch, "when", {"condition": "@(true)"}).append(node)

        def reverse(root):
            inbound = root.find("inbound")
            node = ingress_reference(root)
            inbound.remove(node)
            inbound.append(node)

        mutations = {
            "missing": remove, "duplicate": duplicate, "outbound": outbound,
            "nested": nested, "order": reverse,
            "wrong-id": lambda root: ingress_reference(root).set("id", "copied-ingress-id"),
            "wrong-reference": lambda root: ingress_reference(root).set("fragment-id", "unrelated"),
            "extra-attribute": lambda root: ingress_reference(root).set("unexpected", "value"),
            "child": lambda root: ET.SubElement(ingress_reference(root), "set-variable", {"name": "hidden", "value": "true"}),
            "text": lambda root: setattr(ingress_reference(root), "text", "unexpected text"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                current, _ = audit_graph()
                current.policies[GLOBAL] = changed_xml(current.policies[GLOBAL], mutate)
                source_rejected(self, current)

    def test_fixed_include_requires_complete_leaf_source_and_fixed_leaf_shape(self):
        for mode in ("absent-source", "root-attribute", "wrong-tag", "wrong-id", "extra-attribute", "duplicate", "nested-owned-id"):
            with self.subTest(mode=mode):
                current, _ = audit_graph()
                if mode == "absent-source":
                    del current.policies[INGRESS]
                else:
                    def mutate(root):
                        if mode == "root-attribute": root.set("unexpected", "value")
                        elif mode == "wrong-tag": root[0].tag = "set-variable"
                        elif mode == "wrong-id": root[0].set("id", "otherIngress")
                        elif mode == "extra-attribute": root[0].set("unexpected", "value")
                        elif mode == "duplicate": root.append(copy.deepcopy(root[0]))
                        else: ET.SubElement(root[0][0], "choose", {"id": TRUSTED_INGRESS_ID})
                    current.policies[INGRESS] = changed_xml(current.policies[INGRESS], mutate)
                source_rejected(self, current)

    def test_api_and_other_fragments_cannot_call_or_own_common_ingress(self):
        for scope in ("api", "other-fragment"):
            for tag in ("include-fragment", "choose"):
                with self.subTest(scope=scope, tag=tag):
                    current, _ = audit_graph()
                    attributes = {"id": TRUSTED_INGRESS_ID}
                    if tag == "include-fragment": attributes["fragment-id"] = TRUSTED_INGRESS_ID
                    if scope == "api":
                        identifier = APIM + "/apis/model-a/policies/policy"
                        current.policies[identifier] = changed_xml(current.policies[identifier],
                            lambda root: ET.SubElement(root.find("inbound"), tag, attributes))
                    else:
                        root = ET.Element("fragment")
                        ET.SubElement(root, tag, attributes)
                        current.policies[APIM + "/policyFragments/unrelated"] = ET.tostring(root, encoding="unicode")
                    source_rejected(self, current)

    def test_inline_and_fragment_mixture_is_rejected(self):
        current, _ = audit_graph()
        leaf = ET.fromstring(render_trusted_ingress_fragment())[0]
        current.policies[GLOBAL] = changed_xml(current.policies[GLOBAL],
            lambda root: root.find("inbound").append(leaf))
        source_rejected(self, current)

    def test_readback_rejects_changed_condition_and_rejection_trace(self):
        for mode in ("condition", "trace"):
            with self.subTest(mode=mode):
                current, transport = audit_graph()
                actual = transport.inventory[INGRESS]["body"]["properties"]
                def mutate(root):
                    if mode == "condition": root.find("choose/when").set("condition", "@(false)")
                    else: root.find(".//trace").set("source", "untrusted-trace")
                actual["value"] = changed_xml(actual["value"], mutate)
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(current, transport)
                self.assertTrue(transport.calls)
                self.assertTrue(all(method == "GET" for method, *_ in transport.calls))


if __name__ == "__main__":
    unittest.main()
