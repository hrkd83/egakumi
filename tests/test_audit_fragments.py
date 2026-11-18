"""監査の2部品だけを生成・展開し、元の処理順と変数の契約を守る。"""
import copy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import (
    AUDIT_FRAGMENT_FILES, _with_source_line_comments, _without_source_line_comments, expand_audit_fragments, render_audit_fragments, render_audit_policy,
)
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from deployment.helpers.apim_browser_exception import expand_browser_exception_fragment
from deployment.helpers.apim_authentication import render_service_policy
from deployment.helpers.apim_common_authentication import expand_common_authentication_fragments, render_common_authentication_fragments

POLICIES = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies"


class AuditFragmentTests(unittest.TestCase):
    def test_generated_fragments_are_fixed_leaf_variable_writers(self):
        fragments = render_audit_fragments()
        self.assertEqual(set(fragments), set(AUDIT_FRAGMENT_FILES))
        for identifier, value in fragments.items():
            with self.subTest(fragment=identifier):
                root = ET.fromstring(value)
                self.assertEqual(root.tag, "fragment")
                self.assertEqual(len(root), 1)
                self.assertEqual(root[0].tag, "set-variable")
                self.assertEqual(root[0].get("id"), identifier)
                self.assertEqual(root[0].get("name"), AUDIT_FRAGMENT_FILES[identifier][1])
                self.assertEqual(set(root[0].attrib), {"id", "name", "value"})
                self.assertNotIn("__AUDIT_", value)
                self.assertFalse(root.findall(".//include-fragment"))
                self.assertLessEqual(len(value.encode("utf-8")), 512 * 1024)

    def test_expansion_retains_the_common_flow_and_subject_expression(self):
        service = expand_common_authentication_fragments(render_service_policy(),
            render_common_authentication_fragments(entra_enabled=False), entra_enabled=False)
        expanded = ET.fromstring(expand_audit_fragments(
            expand_browser_exception_fragment(expand_trusted_ingress_fragment(service))))
        previous = ET.fromstring(render_audit_policy((POLICIES / "service-inline-audit-v2.xml").read_text(encoding="utf-8")))
        inbound = expanded.find("inbound")
        self.assertEqual([node.get("id") for node in inbound],
            ["core-request-context", "core-trusted-ingress", "core-credential-browser-exception",
             "core-common-authentication", "core-audit-authenticated-subject"])
        # 主体の式は棚卸で整理するため、固定旧式とはwriterの契約を比較します。
        # 実処理の同値性と追加SID境界はtest_audit_subject_contractで旧／新両式を実行します。
        self.assertEqual({key: value for key, value in inbound[-1].attrib.items() if key != "value"},
                         {key: value for key, value in previous.find("inbound")[-1].attrib.items() if key != "value"})
        current = ET.fromstring(render_audit_fragments()["core-audit-authenticated-subject"])[0]
        self.assertEqual(inbound[-1].attrib, current.attrib)
        self.assertFalse(expanded.findall(".//include-fragment"))

    def test_existing_inline_and_unrelated_fragments_are_not_rewritten(self):
        inline = (POLICIES / "service-inline-audit-v2.xml").read_text(encoding="utf-8")
        self.assertEqual(expand_audit_fragments(inline), inline)
        unrelated = '<policies><inbound><include-fragment fragment-id="core-gateway-telemetry" /></inbound></policies>'
        self.assertEqual(expand_audit_fragments(unrelated), unrelated)

    def test_missing_or_tampered_leaf_shapes_and_include_identity_are_rejected(self):
        service = '<policies><inbound><include-fragment id="core-request-context" fragment-id="core-request-context" /></inbound></policies>'
        generated = render_audit_fragments()
        with self.assertRaises(ValueError):
            expand_audit_fragments(service, {})
        for change in (
            lambda root: ET.SubElement(root, "include-fragment", {"fragment-id": "core-gateway-telemetry"}),
            lambda root: ET.SubElement(root, "base"),
            lambda root: ET.SubElement(root, "inbound"),
            lambda root: root[0].set("name", "unexpectedVariable"),
            lambda root: root[0].set("id", "unexpectedId"),
        ):
            root = ET.fromstring(generated["core-request-context"])
            change(root)
            altered = copy.deepcopy(generated)
            altered["core-request-context"] = ET.tostring(root, encoding="unicode")
            with self.assertRaises(ValueError):
                expand_audit_fragments(service, altered)
        with self.assertRaises(ValueError):
            expand_audit_fragments(service.replace('id="core-request-context" ', 'id="copied" ', 1))
        with self.assertRaises(ValueError):
            expand_audit_fragments(service.replace("<policies><inbound>", "<fragment>").replace("</inbound></policies>", "</fragment>"))

    def test_request_source_indentation_is_generated_without_reformatting_old_markers(self):
        fragment = render_audit_fragments()["core-request-context"]
        self.assertIn("\n      var snapshot = new JObject", fragment)
        self.assertIn("\n          [&quot;requestId&quot;]", fragment)
        from xml.sax.saxutils import escape
        for marker, filename in (
            ("__CORE_AUTHENTICATED_SUBJECT_CSHARP__", "fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs"),
            ("__AUDIT_AUTHENTICATED_SUBJECT_CSHARP__", "expressions/legacy/audit-authenticated-subject.cs"),
        ):
            source = (POLICIES / filename).read_text(encoding="utf-8")
            with self.subTest(marker=marker):
                self.assertEqual(render_audit_policy(marker),
                                 escape(_with_source_line_comments(source), {'"': "&quot;", "'": "&apos;"}))
                self.assertEqual(render_audit_policy(marker, source_comments="omit"),
                                 escape(_without_source_line_comments(source), {'"': "&quot;", "'": "&apos;"}))
        self.assertEqual(_without_source_line_comments("// explanation\n  // explanation\n/* keep */\n\nreturn true; // keep\n"),
                         "/* keep */\n\nreturn true; // keep\n")


if __name__ == "__main__":
    unittest.main()
