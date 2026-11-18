"""共通主体の生成時展開と旧XMLの独立性を合成入力で確認する。"""
from pathlib import Path
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers.apim_audit import AUTHENTICATED_SUBJECT_MARKER, SUBJECT_OBSERVATION_MARKER, _with_source_line_comments, render_audit_policy


class AuditRenderingTests(unittest.TestCase):
    def test_inline_source_round_trips_through_xml_attributes_and_text(self):
        for marker, filename in ((AUTHENTICATED_SUBJECT_MARKER, "expressions/legacy/audit-authenticated-subject.cs"),
                                 (SUBJECT_OBSERVATION_MARKER, "expressions/legacy/audit-subject-observation.cs")):
            with self.subTest(source=filename):
                source = (CORE / "apps/gateway/apim/policies" / filename).read_text(encoding="utf-8")
                source = _with_source_line_comments(source)
                template = '<fragment><trace><message>@{' + marker + '}</message></trace>'
                template += '<set-variable value="@{' + marker + '}"/></fragment>'
                rendered = render_audit_policy(template)
                root = ET.fromstring(rendered)
                self.assertNotIn(marker, rendered)
                self.assertEqual(root.find("trace/message").text, "@{" + source + "}")
                self.assertEqual(root.find("set-variable").get("value"), "@{" + source.replace("\n", " ") + "}")

    def test_fixed_previous_templates_do_not_load_current_subject_source(self):
        previous = '<fragment><trace><message>@{ return "fixed"; }</message></trace></fragment>'
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("Loaded current audit source")):
            self.assertEqual(render_audit_policy(previous), previous)

    def test_connection_render_expands_raw_and_embedded_audit_sources(self):
        from deployment.helpers import credential_login
        from test_credential_login import settings
        rendered = credential_login.render_connection("credential-connection-outbound.xml", settings(), "enabled")
        self.assertNotIn(AUTHENTICATED_SUBJECT_MARKER, rendered)
        self.assertNotIn(SUBJECT_OBSERVATION_MARKER, rendered)
        root = ET.fromstring(rendered)
        branch = next(node for node in root.findall("choose/when") if node.find(
            "set-variable[@name='gatewayAuditCode'][@value='CONNECTION_CLAIMS_REQUIRED']") is not None)
        tiny = branch.findall("trace")
        self.assertEqual(len(tiny), 1)
        self.assertIn("gatewayAuthenticatedSubject", tiny[0].find("message").text)

    def test_subject_validation_never_reads_tokens_headers_bodies_or_authorization_decision(self):
        source = (CORE / "apps/gateway/apim/policies/fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs").read_text(encoding="utf-8")
        for forbidden in ("context.Request", "context.Response", "gatewayValidatedToken", "gatewayDecision", "send-request", "return "):
            with self.subTest(expression=forbidden): self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
