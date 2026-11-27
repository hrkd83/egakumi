"""STEP2部品化で入口条件・拒否監査・認証前の実行順が変わらないことを検証する。"""
import copy
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import expand_audit_fragments
from deployment.helpers.apim_authentication import (
    _structure, read_managed_entra_authentication, render_service_policy,
)
from deployment.helpers.apim_ingress import (
    TRUSTED_INGRESS_ID, expand_trusted_ingress_fragment, render_trusted_ingress_fragment,
)
from tests.identity.test_entra_gateway_authentication import settings as entra_settings


INCLUDE = '<include-fragment id="core-trusted-ingress" fragment-id="core-trusted-ingress" />'
SERVICE = '<policies><inbound>' + INCLUDE + '</inbound></policies>'


def expanded_ingress_prefix(xml):
    """要求情報の準備から入口確認まで、STEP2が担当する全構造を返す。

    入口条件・拒否応答・監査本文と、入口より前の処理順をまとめて比較する。
    後続の認証と認証済み利用者情報は、共通認証・主体の実契約で別に検査する。
    """
    expanded = expand_audit_fragments(expand_trusted_ingress_fragment(xml))
    root = ET.fromstring(expanded)
    inbounds = root.findall("inbound")
    if len(inbounds) != 1:
        raise AssertionError("Expected the single common inbound section")
    nodes = list(inbounds[0])
    positions = [index for index, node in enumerate(nodes)
                 if node.tag == "choose" and node.get("id") == TRUSTED_INGRESS_ID]
    if len(positions) != 1:
        raise AssertionError("Expected one direct trusted ingress block")
    return tuple(_structure(node) for node in nodes[:positions[0] + 1])


class TrustedIngressFragmentTests(unittest.TestCase):
    def test_generated_leaf_contains_the_existing_rejection_trace(self):
        xml = render_trusted_ingress_fragment()
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "fragment")
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0].tag, "choose")
        self.assertEqual(root[0].attrib, {"id": TRUSTED_INGRESS_ID})
        self.assertFalse(root.findall(".//include-fragment"))
        self.assertFalse(root.findall(".//base"))
        self.assertNotIn("__AUTHENTICATION_", xml)
        self.assertNotIn("__AUDIT_", xml)
        self.assertNotIn("__L7_INGRESS_", xml)
        self.assertLessEqual(len(xml.encode("utf-8")), 512 * 1024)
        branch = root[0].find("when")
        self.assertEqual(len(branch.findall("trace")), 1)
        self.assertEqual(branch.find("set-variable[@name='gatewayAuditCode']").get("value"), "TRUSTED_INGRESS_REQUIRED")
        self.assertEqual(branch.find("return-response/set-status").get("code"), "403")
        self.assertIn("{{core-l7-ingress-enabled}}", branch.get("condition"))
        self.assertIn("{{core-l7-ingress-secret}}", branch.get("condition"))


    def test_ingress_prefix_rejects_changed_condition_status_audit_and_execution_order(self):
        expected = expanded_ingress_prefix(render_service_policy())
        current = expand_audit_fragments(expand_trusted_ingress_fragment(render_service_policy()))
        self.assertEqual(expanded_ingress_prefix(current), expected)
        for mode in ("condition", "status", "audit", "execution-order"):
            root = ET.fromstring(current)
            inbound = root.find("inbound")
            ingress = inbound.find("choose[@id='core-trusted-ingress']")
            branch = ingress.find("when")
            if mode == "condition":
                branch.set("condition", "@(true)")
            elif mode == "status":
                branch.find("return-response/set-status").set("code", "200")
            elif mode == "audit":
                branch.find("trace/message").text = '@("synthetic altered audit")'
            else:
                request_context = inbound.find("set-variable[@id='core-request-context']")
                self.assertIsNotNone(request_context)
                inbound.remove(request_context)
                inbound.insert(list(inbound).index(ingress) + 1, request_context)
            with self.subTest(mode=mode):
                self.assertNotEqual(expanded_ingress_prefix(ET.tostring(root, encoding="unicode")), expected)


    def test_existing_inline_and_unrelated_includes_are_not_rewritten(self):
        inline = '<policies><inbound><choose id="synthetic-unrelated"><when condition="@(false)" /></choose></inbound></policies>'
        self.assertEqual(expand_trusted_ingress_fragment(inline), inline)
        unrelated = '<policies><inbound><include-fragment fragment-id="core-gateway-telemetry" /></inbound></policies>'
        self.assertEqual(expand_trusted_ingress_fragment(unrelated), unrelated)

    def test_supplied_leaf_must_match_its_executable_source(self):
        generated = render_trusted_ingress_fragment()
        self.assertEqual(expand_trusted_ingress_fragment(SERVICE, generated), expand_trusted_ingress_fragment(SERVICE))
        for change in (
            lambda root: ET.SubElement(root, "include-fragment", {"fragment-id": "core-gateway-telemetry"}),
            lambda root: ET.SubElement(root[0], "base"),
            lambda root: ET.SubElement(root[0], "inbound"),
            lambda root: root[0].set("id", "copied-ingress"),
            lambda root: root[0].find("when").set("condition", "@(false)"),
            lambda root: root[0].find("when/return-response/set-status").set("code", "200"),
        ):
            root = ET.fromstring(generated)
            change(root)
            with self.subTest(change=change), self.assertRaises(ValueError):
                expand_trusted_ingress_fragment(SERVICE, ET.tostring(root, encoding="unicode"))

    def test_include_identity_uniqueness_and_common_inbound_placement_are_required(self):
        for candidate in (
            SERVICE.replace('id="core-trusted-ingress" ', 'id="copied" ', 1),
            SERVICE.replace('fragment-id="core-trusted-ingress"', 'fragment-id="unowned"'),
            SERVICE.replace("</inbound>", INCLUDE + "</inbound>"),
            SERVICE.replace("<inbound>", "<outbound>").replace("</inbound>", "</outbound>"),
            SERVICE.replace(INCLUDE, "<choose><when condition='@(true)'>" + INCLUDE + "</when></choose>"),
            SERVICE.replace(INCLUDE, INCLUDE.replace(" />", ">unexpected</include-fragment>")),
            SERVICE.replace(INCLUDE, INCLUDE.replace(" />", "><trace /></include-fragment>")),
            SERVICE.replace("</inbound>", '<choose id="core-trusted-ingress" /></inbound>'),
            SERVICE.replace("</inbound>", "</inbound><inbound/>"),
            "<!DOCTYPE policies [<!ENTITY test 'x'>]>" + SERVICE,
        ):
            with self.subTest(policy=candidate), self.assertRaises(ValueError):
                expand_trusted_ingress_fragment(candidate)

    def test_common_policy_rejects_duplicate_forged_moved_and_late_ingress_references(self):
        for kind in ("duplicate", "forged", "wrong-section", "late", "missing", "body", "inline"):
            root = ET.fromstring(render_service_policy(entra=entra_settings()))
            inbound = root.find("inbound")
            include = inbound.find("include-fragment[@id='core-trusted-ingress']")
            if kind == "duplicate":
                inbound.append(copy.deepcopy(include))
            elif kind == "forged":
                include.set("id", "copied-ingress")
            elif kind == "wrong-section":
                inbound.remove(include); root.find("outbound").append(include)
            elif kind == "late":
                inbound.remove(include); inbound.append(include)
            elif kind == "missing":
                inbound.remove(include)
            elif kind == "body":
                include.text = "unexpected"
            else:
                include.tag = "choose"; include.attrib.pop("fragment-id")
            policy = ET.tostring(root, encoding="unicode")
            with self.subTest(change=kind), self.assertRaises(ValueError):
                read_managed_entra_authentication(policy, expected_entra=entra_settings())


if __name__ == "__main__":
    unittest.main()
