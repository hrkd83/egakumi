"""クエリ監査の2式を同名C#から展開し、説明・所有先・安全な区画境界を検証する。"""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers.apim_audit import (
    CORE_QUERY_TELEMETRY_MARKER, CORE_QUERY_TELEMETRY_PREDICATE_MARKER,
    SOURCE_EXPRESSION_FILES, SUBJECT_OBSERVATION_MARKER,
    _with_source_line_comments, extract_query_telemetry_sections, render_audit_policy,
)


POLICIES = Path(__file__).resolve().parents[1] / "apps/apim/policies"
DIRECTORY = POLICIES / "fragments/audit/core-query-telemetry"


class QueryTelemetryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.template = (DIRECTORY / "core-query-telemetry.xml").read_text(encoding="utf-8")
        self.source = (DIRECTORY / "core-query-telemetry.cs").read_text(encoding="utf-8")

    def test_two_expressions_have_one_same_named_source_and_xml_owner(self):
        self.assertEqual(SOURCE_EXPRESSION_FILES[CORE_QUERY_TELEMETRY_MARKER],
                         (DIRECTORY / "core-query-telemetry.cs").relative_to(POLICIES).as_posix())
        self.assertFalse((POLICIES / "query-telemetry.xml").exists())
        for marker in (CORE_QUERY_TELEMETRY_MARKER, CORE_QUERY_TELEMETRY_PREDICATE_MARKER):
            with self.subTest(marker=marker):
                owners = [path for path in POLICIES.rglob("*.xml") if marker in path.read_text(encoding="utf-8")]
                self.assertEqual(owners, [DIRECTORY / "core-query-telemetry.xml"])
                self.assertEqual(self.template.count(marker), 1)
        sections = extract_query_telemetry_sections(self.source)
        self.assertEqual(tuple(sections), ("predicate", "message"))
        self.assertNotIn(SUBJECT_OBSERVATION_MARKER, sections["predicate"])
        self.assertNotIn(SUBJECT_OBSERVATION_MARKER, sections["message"])
        self.assertEqual(sections["message"].count('context.Variables.TryGetValue("gatewayAuthenticatedSubject"'), 1)

    def test_rendering_preserves_both_explanations_and_shared_subject_details(self):
        sections = extract_query_telemetry_sections(self.source)
        rendered = render_audit_policy(self.template)
        self.assertEqual(render_audit_policy(self.template, source_comments="compact"), rendered)
        root = ET.fromstring(rendered)
        predicate = root.find("choose/when").get("condition")
        message = root.find("choose/when/trace/message").text
        self.assertEqual(root.find("choose/when/trace").attrib,
                         {"source": "core-query-observation", "severity": "information"})
        for source, expression in ((sections["predicate"], predicate), (sections["message"], message)):
            for line in source.splitlines():
                if line.lstrip().startswith("//"):
                    self.assertIn(_with_source_line_comments(line).strip(), expression)
        self.assertIn('new JProperty("schemaVersion", 2)', message)
        self.assertIn('new JProperty("auditSchemaVersion", 2)', message)
        self.assertIn('new JProperty("event", "query-guard")', message)
        self.assertNotIn('"countBucket"', message)
        for expression in (predicate, message):
            self.assertNotIn("//", expression)
            for token in ("context.Request.Body", "context.Request.Headers", "context.Request.Url.Query",
                          "context.Request.OriginalUrl", "__CORE_", SUBJECT_OBSERVATION_MARKER):
                self.assertNotIn(token, expression)

    def test_sections_reject_missing_duplicate_nested_unknown_and_outside_code(self):
        sections = extract_query_telemetry_sections(self.source)
        malformed = (
            self.source.replace("// END message", "// END predicate"),
            self.source.replace("// BEGIN message", "// BEGIN unknown"),
            self.source.replace("// BEGIN message", " // BEGIN message"),
            self.source.replace("// END predicate", "// BEGIN message\n// END predicate"),
            self.source + "\n// BEGIN message\nreturn true;\n// END message\n",
            self.source + "\nreturn true;\n",
            self.source.replace(sections["predicate"], "// only an explanation"),
        )
        for value in malformed:
            with self.subTest(source=value[:40]), self.assertRaises(ValueError):
                extract_query_telemetry_sections(value)
        self.assertEqual(extract_query_telemetry_sections(self.source.replace("\n", "\r\n")), sections)

    def test_xml_requires_both_sections_once_at_their_own_line(self):
        malformed = (
            self.template.replace(CORE_QUERY_TELEMETRY_PREDICATE_MARKER, "return true;"),
            self.template.replace(CORE_QUERY_TELEMETRY_MARKER, "return true;"),
            self.template.replace(CORE_QUERY_TELEMETRY_MARKER,
                                  CORE_QUERY_TELEMETRY_MARKER + "\n" + CORE_QUERY_TELEMETRY_MARKER),
            self.template.replace(CORE_QUERY_TELEMETRY_MARKER, "return " + CORE_QUERY_TELEMETRY_MARKER),
        )
        for value in malformed:
            with self.subTest(template=value[:40]), self.assertRaises(ValueError):
                render_audit_policy(value)


if __name__ == "__main__":
    unittest.main()
