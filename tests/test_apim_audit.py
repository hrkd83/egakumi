"""共通主体と監査の正規生成時展開を合成入力で確認する。"""
from policy_fixture_paths import policy_fixture_path
from pathlib import Path
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers.apim_audit import CORE_AUTHENTICATED_SUBJECT_MARKER, CORE_GATEWAY_TELEMETRY_MARKER, _with_source_line_comments, render_audit_policy


class AuditRenderingTests(unittest.TestCase):
    def test_inline_telemetry_source_round_trips_through_xml_text(self):
        marker = CORE_GATEWAY_TELEMETRY_MARKER
        filename = "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs"
        source = policy_fixture_path(CORE / "apps/gateway/apim/policies", filename).read_text(encoding="utf-8")
        source = _with_source_line_comments(source)
        template = '<fragment><trace><message>@{\n' + marker + '\n}</message></trace></fragment>'
        rendered = render_audit_policy(template)
        self.assertEqual(ET.fromstring(rendered).find('trace/message').text, '@{\n' + source.rstrip('\n') + '\n}')
        self.assertNotIn('&quot;', rendered)
        self.assertNotIn(marker, rendered)

    def test_fixed_previous_templates_do_not_load_current_subject_source(self):
        previous = '<fragment><trace><message>@{ return "fixed"; }</message></trace></fragment>'
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("Loaded current audit source")):
            self.assertEqual(render_audit_policy(previous), previous)

    def test_connection_render_expands_raw_and_embedded_audit_sources(self):
        from deployment.helpers import credential_login
        from test_credential_login import settings
        rendered = credential_login.render_connection("credential-connection-outbound.xml", settings(), "enabled")
        self.assertNotIn(CORE_AUTHENTICATED_SUBJECT_MARKER, rendered)
        self.assertNotIn(CORE_GATEWAY_TELEMETRY_MARKER, rendered)
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
