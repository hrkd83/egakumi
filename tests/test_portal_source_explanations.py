"""正規C#の説明を安全なblockコメントとして保持し、実行行を変えない。"""
from policy_fixture_paths import policy_fixture_path
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import (
    COMPACT_SOURCE_COMMENT, CORE_AUTHENTICATED_SUBJECT_MARKER,
    CORE_GATEWAY_TELEMETRY_MARKER, CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, SOURCE_EXPRESSION_FILES,
    _with_source_line_comments, _without_source_line_comments,
    portal_source_explanation_blocks, render_audit_fragments, render_audit_policy,
)
from deployment.helpers.apim_ingress import render_trusted_ingress_fragment

POLICIES = Path(__file__).resolve().parents[1] / "apps/apim/policies"


class PortalSourceExplanationTests(unittest.TestCase):
    def test_each_source_explanation_is_retained_and_existing_lines_stay_exact(self):
        blocks = portal_source_explanation_blocks()
        self.assertEqual(set(blocks), set(SOURCE_EXPRESSION_FILES))
        for marker, filename in SOURCE_EXPRESSION_FILES.items():
            source = (policy_fixture_path(POLICIES, filename)).read_text(encoding="utf-8")
            original = source.split("\n")
            rendered = _with_source_line_comments(source).split("\n")
            self.assertEqual(len(original), len(rendered))
            explanation_count = 0
            for before, after in zip(original, rendered):
                if before.lstrip().startswith("//"):
                    explanation_count += 1
                    self.assertIn(after.strip(), blocks[marker])
                    self.assertEqual(before[:len(before) - len(before.lstrip())], after[:len(after) - len(after.lstrip())])
                else:
                    self.assertEqual(before, after)
            self.assertGreater(explanation_count, 0)
            self.assertEqual(len(blocks[marker]), explanation_count)

    def test_line_comment_conversion_remains_closed_after_xml_attribute_normalization(self):
        source = '  // explanation with */ and "quotes"\nreturn true;\n'
        expected = '  /* explanation with * / and "quotes" */\nreturn true;\n'
        self.assertEqual(_with_source_line_comments(source), expected)
        from xml.sax.saxutils import escape
        encoded = escape(_with_source_line_comments(source), {'"': "&quot;"})
        value = ET.fromstring('<set-variable value="@{' + encoded + '}" />').get("value")
        self.assertIn('/* explanation with * / and "quotes" */ return true;', value)
        self.assertNotIn("//", value)

    def test_canonical_fragments_keep_all_details_and_inline_telemetry_points_to_its_source(self):
        sources = render_audit_fragments()
        all_blocks = portal_source_explanation_blocks()
        for identifier, marker in (("core-request-context", CORE_REQUEST_CONTEXT_MARKER),
                                   ("core-audit-authenticated-subject", CORE_AUTHENTICATED_SUBJECT_MARKER)):
            value = ET.fromstring(sources[identifier])[0].get("value")
            for block in all_blocks[marker]:
                self.assertIn(block, value)
        gateway = render_audit_policy((POLICIES / "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml").read_text(encoding="utf-8"))
        for block in all_blocks[CORE_GATEWAY_TELEMETRY_MARKER]:
            self.assertIn(block, ET.fromstring(gateway).find("trace/message").text)
        compact = render_audit_policy(CORE_GATEWAY_TELEMETRY_MARKER, source_comments="compact")
        self.assertTrue(compact.startswith(COMPACT_SOURCE_COMMENT + "\n"))
        for block in all_blocks[CORE_GATEWAY_TELEMETRY_MARKER]:
            self.assertNotIn(block, compact)
        source = (POLICIES / "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs").read_text(encoding="utf-8")
        from xml.sax.saxutils import escape
        self.assertEqual(compact, escape(COMPACT_SOURCE_COMMENT + "\n" + _without_source_line_comments(source).rstrip("\n")))
        ingress = render_trusted_ingress_fragment()
        self.assertNotIn(COMPACT_SOURCE_COMMENT, ingress)
        for block in all_blocks[CORE_TRUSTED_INGRESS_MARKER]:
            self.assertIn(block, ET.fromstring(ingress).find("choose/when").get("condition"))
        # Ingress uses the canonical key-only projection, which intentionally
        # removes the unused employee authorization branch and its explanations.
        from deployment.helpers import credential_login
        expected_ingress_telemetry = ET.fromstring(credential_login.key_only_gateway_telemetry(
            gateway, source_comments="full")).find("trace/message").text
        self.assertEqual(ET.fromstring(ingress).find("choose/when/trace/message").text, expected_ingress_telemetry)
        self.assertIn(COMPACT_SOURCE_COMMENT, render_trusted_ingress_fragment(source_comments="compact"))
        self.assertNotIn(COMPACT_SOURCE_COMMENT, render_trusted_ingress_fragment(source_comments="omit"))
        with self.assertRaises(ValueError):
            render_audit_policy(CORE_GATEWAY_TELEMETRY_MARKER, source_comments="unknown")


if __name__ == "__main__":
    unittest.main()
