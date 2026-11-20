"""C#説明を安全なblockコメントとしてAzureへ残し、実行行と旧生成元を保持する。"""
from policy_fixture_paths import policy_fixture_path
from pathlib import Path
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import (
    AUTHENTICATED_SUBJECT_MARKER, COMPACT_SOURCE_COMMENT, CORE_AUTHENTICATED_SUBJECT_MARKER,
    CORE_GATEWAY_TELEMETRY_MARKER, CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, SOURCE_EXPRESSION_FILES,
    SUBJECT_OBSERVATION_MARKER, _with_source_line_comments, _without_source_line_comments,
    portal_source_explanation_blocks, render_audit_fragments, render_audit_policy,
)
from deployment.helpers.apim_authentication import (
    _render_omitted_explanations_service_policy, merge_common_authentication_policy,
    read_managed_entra_authentication, transition_common_authentication_policy,
    validate_common_authentication_policy,
)
from deployment.helpers.apim_ingress import render_trusted_ingress_fragment
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments
from test_entra_gateway_authentication import settings

POLICIES = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies"


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
        compact = render_audit_policy(SUBJECT_OBSERVATION_MARKER, source_comments="compact")
        self.assertTrue(compact.startswith(COMPACT_SOURCE_COMMENT + "\n"))
        for block in all_blocks[SUBJECT_OBSERVATION_MARKER]:
            self.assertNotIn(block, compact)
        source = (POLICIES / "expressions/legacy/audit-subject-observation.cs").read_text(encoding="utf-8")
        from xml.sax.saxutils import escape
        self.assertEqual(compact, escape(COMPACT_SOURCE_COMMENT + "\n" + _without_source_line_comments(source),
                                         {'"': "&quot;", "'": "&apos;"}))
        ingress = render_trusted_ingress_fragment()
        self.assertNotIn(COMPACT_SOURCE_COMMENT, ingress)
        for block in all_blocks[CORE_TRUSTED_INGRESS_MARKER]:
            self.assertIn(block, ET.fromstring(ingress).find("choose/when").get("condition"))
        for block in all_blocks[CORE_GATEWAY_TELEMETRY_MARKER]:
            self.assertIn(block, ET.fromstring(ingress).find("choose/when/trace/message").text)
        self.assertIn(COMPACT_SOURCE_COMMENT, render_trusted_ingress_fragment(source_comments="compact"))
        self.assertNotIn(COMPACT_SOURCE_COMMENT, render_trusted_ingress_fragment(source_comments="omit"))
        with self.assertRaises(ValueError):
            render_audit_policy(SUBJECT_OBSERVATION_MARKER, source_comments="unknown")

    def test_previous_omitted_common_source_requires_verified_explicit_transition(self):
        previous = _render_omitted_explanations_service_policy(entra=settings())
        self.assertEqual(read_managed_entra_authentication(previous, expected_entra=settings()), settings())
        with self.assertRaises(ValueError):
            merge_common_authentication_policy(previous, entra=settings())
        current, changed = transition_common_authentication_policy(previous, previous_entra=settings(), entra=settings())
        self.assertTrue(changed)
        validate_common_authentication_policy(current, entra=settings())
        self.assertIn(COMPACT_SOURCE_COMMENT, "".join(render_common_authentication_fragments(entra_enabled=True).values()))
        changed_code = previous.replace("Invalid subscription key", "Manual subscription rule", 1)
        self.assertNotEqual(changed_code, previous)
        with self.assertRaises(ValueError):
            read_managed_entra_authentication(changed_code, expected_entra=settings())

    def test_omitted_source_does_not_follow_current_service_or_new_browser_sources(self):
        previous = _render_omitted_explanations_service_policy(entra=settings())
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.name in ("service.xml", "core-credential-browser-exception.xml", "core-credential-browser-exception.cs"):
                raise AssertionError("Fixed omitted source followed a current browser source")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read):
            self.assertEqual(_render_omitted_explanations_service_policy(entra=settings()), previous)


if __name__ == "__main__":
    unittest.main()
