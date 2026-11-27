"""棚卸した部品はAzure名・XML・直接埋込みC#の所有先を1対1にする。"""
from policy_fixture_paths import policy_fixture_path
from pathlib import Path
import unittest

from configuration_fixtures import CORE
from deployment.helpers.apim_audit import (
    SECTION_EXPRESSION_FILES,
    extract_policy_source_sections,
    render_audit_policy,
    CORE_AUTHENTICATED_SUBJECT_MARKER, CORE_GATEWAY_TELEMETRY_MARKER,
    CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER, CORE_API_SETTINGS_MARKER, CORE_API_INPUT_VALIDATION_MARKER, CORE_API_OPERATION_SETTINGS_MARKER, CORE_API_AUTHENTICATION_RESULT_MARKER, CORE_MODEL_LIMITS_MARKER, CORE_SPEECH_SSML_GUARD_MARKER, CORE_QUERY_OBSERVATION_MARKER, CORE_QUERY_TELEMETRY_MARKER, SOURCE_EXPRESSION_FILES,
    SUBJECT_OBSERVATION_MARKER,
    CORE_DOCUMENT_INPUT_VALIDATION_MARKER,
    CORE_API_TARGET_VALIDATION_MARKER,
    CORE_CHAT_STORE_DISABLED_MARKER,
    CORE_RATE_LIMIT_MARKER,
    CORE_TOKEN_LIMITS_MARKER,
    CORE_ENTRA_USER_PROFILE_MARKER,
    CORE_CREDENTIAL_CALLER_MARKER,
    CORE_CREDENTIAL_USE_RESULT_MARKER,
    CORE_BACKEND_AUDIT_MARKER,
    CORE_AUDIT_BODY_MODEL_MARKER,
)

POLICIES = Path(__file__).resolve().parents[1] / "apps/api-gateway/policies"
PRIMARY = {
    "core-request-context": CORE_REQUEST_CONTEXT_MARKER,
    "core-audit-authenticated-subject": CORE_AUTHENTICATED_SUBJECT_MARKER,
    "core-trusted-ingress": CORE_TRUSTED_INGRESS_MARKER,
    "core-credential-browser-exception": CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER,
    "core-credential-connection": CORE_CREDENTIAL_CALLER_MARKER,
    "core-credential-connection-outbound": "__CORE_CREDENTIAL_CONNECTION_OUTBOUND_CSHARP__",
    "core-credential-session-use": CORE_CREDENTIAL_USE_RESULT_MARKER,
    "core-gateway-telemetry": CORE_GATEWAY_TELEMETRY_MARKER,
    "core-backend-audit": CORE_BACKEND_AUDIT_MARKER,
    "core-audit-body-model": CORE_AUDIT_BODY_MODEL_MARKER,
    "core-audit-context": "__CORE_AUDIT_CONTEXT_TRACE_CSHARP__",
    "core-realtime-observer": "__REALTIME_OBSERVER_OWNER_CSHARP__",
    "core-api-settings": CORE_API_SETTINGS_MARKER,
    "core-api-input-validation": CORE_API_INPUT_VALIDATION_MARKER,
    "core-api-operation-settings": CORE_API_OPERATION_SETTINGS_MARKER,
    "core-api-target-validation": CORE_API_TARGET_VALIDATION_MARKER,
    "core-chat-store-disabled": CORE_CHAT_STORE_DISABLED_MARKER,
    "core-model-limits": CORE_MODEL_LIMITS_MARKER,
    "core-rate-limit": CORE_RATE_LIMIT_MARKER,
    "core-token-limits": CORE_TOKEN_LIMITS_MARKER,
    "core-entra-user-profile": CORE_ENTRA_USER_PROFILE_MARKER,
    "core-speech-ssml-guard": CORE_SPEECH_SSML_GUARD_MARKER,
    "core-document-input-validation": CORE_DOCUMENT_INPUT_VALIDATION_MARKER,
    "core-api-authentication-result": CORE_API_AUTHENTICATION_RESULT_MARKER,
    "core-query-observation": CORE_QUERY_OBSERVATION_MARKER,
    "core-query-telemetry": CORE_QUERY_TELEMETRY_MARKER,
}
PURPOSES = {identifier: "quota" if identifier in ("core-rate-limit", "core-token-limits", "core-entra-user-profile") else "request" if identifier in ("core-query-observation", "core-api-input-validation", "core-api-operation-settings", "core-api-target-validation", "core-chat-store-disabled", "core-model-limits", "core-speech-ssml-guard", "core-document-input-validation") else "settings" if identifier == "core-api-settings" else "ingress" if identifier == "core-trusted-ingress" else "authentication" if identifier in ("core-credential-browser-exception", "core-api-authentication-result", "core-credential-connection", "core-credential-session-use") else "audit"
            for identifier in PRIMARY}
PURPOSES["core-realtime-observer"] = "authentication"
PURPOSES["core-credential-connection-outbound"] = "authentication"


class FragmentSourceOwnershipTests(unittest.TestCase):
    def test_each_primary_has_one_same_named_xml_and_direct_expression_owner(self):
        xml_files = list(POLICIES.rglob("*.xml"))
        for identifier, marker in PRIMARY.items():
            with self.subTest(fragment=identifier):
                expected_directory = POLICIES / "fragments" / PURPOSES[identifier] / identifier
                expected_xml = expected_directory / (identifier + ".xml")
                expected_cs = expected_directory / (identifier + ".cs")
                self.assertTrue(expected_xml.is_file())
                self.assertTrue(expected_cs.is_file())
                self.assertEqual(SOURCE_EXPRESSION_FILES[marker], expected_cs.relative_to(POLICIES).as_posix())
                self.assertFalse((POLICIES / expected_xml.name).exists())
                self.assertFalse((POLICIES / "expressions" / expected_cs.name).exists())
                references = [path for path in xml_files if marker in path.read_text(encoding="utf-8")]
                self.assertEqual(references, [expected_xml])
                self.assertEqual(expected_xml.read_text(encoding="utf-8").count(marker), 1)

        for identifier, folder, marker in (
            ("realtime-create", "operation-policies/realtime", "__REALTIME_CREATE_TICKET_CSHARP__"),
            ("realtime-hangup", "operation-policies/realtime", "__REALTIME_HANGUP_OWNER_CSHARP__"),
            ("realtime-owner-context", "snippets/authentication/realtime-owner-context", "__REALTIME_OWNER_CONTEXT_CSHARP__"),
            ("credential-challenge", "snippets/authentication/credential-challenge", "__CREDENTIAL_CHALLENGE_RESULT_CSHARP__"),
            ("rate-limit-error", "snippets/quota/rate-limit-error", "__RATE_LIMIT_ERROR_RETRY_AFTER_CSHARP__"),
            ("mcp-rate-cleanup", "snippets/quota/mcp-rate-cleanup", "__MCP_RATE_CLEANUP_CSHARP__"),
            ("credential-telemetry", "snippets/audit/credential-telemetry", "__CREDENTIAL_TELEMETRY_CSHARP__"),
            ("start", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_START_CALLER_CSHARP__"),
            ("status", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_STATUS_CALLER_CSHARP__"),
            ("disconnect", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_DISCONNECT_CALLER_CSHARP__"),
            ("logout-all", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_CSHARP__"),
            ("logout", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_LOGOUT_CALLER_CSHARP__"),
            ("session-commit", "operation-policies/credential-login", "__CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_CSHARP__"),
        ):
            with self.subTest(component=identifier):
                directory = POLICIES / folder
                xml, cs = directory / (identifier + ".xml"), directory / (identifier + ".cs")
                self.assertTrue(xml.is_file())
                self.assertTrue(cs.is_file())
                self.assertEqual(SOURCE_EXPRESSION_FILES[marker], cs.relative_to(POLICIES).as_posix())
                self.assertEqual(policy_fixture_path(POLICIES, "credential-login/" + identifier + ".xml" if identifier in ("start", "status", "session-commit", "logout", "logout-all", "disconnect") else xml.name), xml)
                self.assertFalse((POLICIES / xml.name).exists())
                if identifier in ("start", "status", "session-commit", "logout", "logout-all", "disconnect"): self.assertFalse((POLICIES / ("credential-login/" + identifier + ".xml")).exists())
                references = [path for path in xml_files if marker in path.read_text(encoding="utf-8")]
                self.assertEqual(references, [xml])
                self.assertEqual(xml.read_text(encoding="utf-8").count(marker), 1)

    def test_each_sectioned_policy_has_fixed_markers_and_one_xml_consumer(self):
        for filename, markers in SECTION_EXPRESSION_FILES.items():
            cs = POLICIES / filename
            # WebSocket expressions occupy slots at fixed positions in the
            # shared stream flow. The ws overlay selects the transport pieces.
            xml = (POLICIES / "api-templates/shared/stream.xml"
                   if filename == "api-templates/ws/ws.cs" else cs.with_suffix(".xml"))
            self.assertTrue(cs.is_file())
            self.assertTrue(xml.is_file())
            self.assertEqual(tuple(extract_policy_source_sections(cs.read_text(encoding="utf-8"), tuple(markers))), tuple(markers))
            for marker in markers.values():
                self.assertEqual(SOURCE_EXPRESSION_FILES[marker], filename)
                self.assertEqual(xml.read_text(encoding="utf-8").count(marker), 1)
                self.assertEqual([path for path in POLICIES.rglob("*.xml") if marker in path.read_text(encoding="utf-8")], [xml])

    def test_credential_legacy_fixture_names_resolve_only_to_canonical_sources(self):
        filename = "operation-policies/credential-login/callback.cs"
        cs = POLICIES / filename
        xml = cs.with_suffix(".xml")
        markers = SECTION_EXPRESSION_FILES[filename]
        sections = extract_policy_source_sections(cs.read_text(encoding="utf-8"), tuple(markers))
        self.assertEqual(tuple(sections), ("input", "transaction", "owner"))
        for suffix in (".xml", ".cs"):
            self.assertEqual(policy_fixture_path(POLICIES, "credential-login/callback" + suffix), cs.with_suffix(suffix))
        self.assertFalse((POLICIES / "credential-login/callback.xml").exists())
        for marker in markers.values():
            self.assertEqual(SOURCE_EXPRESSION_FILES[marker], filename)
            self.assertEqual(xml.read_text(encoding="utf-8").count(marker), 1)
            self.assertEqual([path for path in POLICIES.rglob("*.xml") if marker in path.read_text(encoding="utf-8")], [xml])
        for operation in ("launch", "launch-post", "identity-callback"):
            xml = POLICIES / "operation-policies/credential-login" / (operation + ".xml")
            self.assertTrue(xml.is_file())
            self.assertEqual(policy_fixture_path(POLICIES, "credential-login/" + operation + ".xml"), xml)
            self.assertFalse(xml.with_suffix(".cs").exists())
            self.assertFalse((POLICIES / "credential-login" / xml.name).exists())
        for name in ("credential-connection", "credential-session-use"):
            identifier = "core-" + name
            directory = POLICIES / "fragments/authentication" / identifier
            for suffix in (".xml", ".cs"):
                self.assertEqual(policy_fixture_path(POLICIES, identifier + suffix),
                                 directory / (identifier + suffix))
            self.assertEqual(policy_fixture_path(POLICIES, name + ".xml"),
                             directory / (identifier + ".xml"))
            self.assertFalse((POLICIES / (name + ".xml")).exists())

    def test_sectioned_sources_reject_unmatched_sections_and_xml_insertion_points(self):
        filename = "operation-policies/credential-login/callback.cs"
        names = tuple(SECTION_EXPRESSION_FILES[filename])
        source = (POLICIES / filename).read_text(encoding="utf-8")
        for malformed in (
            source.replace("// END input", "// END owner", 1),
            source.replace("// BEGIN input", "// BEGIN other", 1),
            source.replace("// END owner", "", 1),
            source + "\nreturn null;",
            source + "\n// BEGIN input\nreturn null;\n// END input\n",
        ):
            with self.subTest(source=malformed[:30]):
                with self.assertRaises(ValueError):
                    extract_policy_source_sections(malformed, names)
        xml = (POLICIES / filename).with_suffix(".xml").read_text(encoding="utf-8")
        marker = SECTION_EXPRESSION_FILES[filename]["input"]
        for malformed in (xml.replace(marker, ""), xml.replace(marker, marker + marker), xml.replace(" " + marker, '"' + marker)):
            with self.assertRaises(ValueError):
                render_audit_policy(malformed)

    def test_each_audit_consumer_owns_its_projection_without_the_legacy_source(self):
        self.assertNotIn(SUBJECT_OBSERVATION_MARKER, SOURCE_EXPRESSION_FILES)
        self.assertFalse((POLICIES / "expressions/legacy/audit-subject-observation.cs").exists())
        for name in ("core-gateway-telemetry.cs", "core-query-telemetry.cs", "credential-telemetry.cs", "core-credential-connection-outbound.cs"):
            source = policy_fixture_path(POLICIES, name).read_text(encoding="utf-8")
            self.assertNotIn(SUBJECT_OBSERVATION_MARKER, source)
            self.assertEqual(source.count('context.Variables.TryGetValue("gatewayAuthenticatedSubject"'), 1)
            self.assertIn('obs.Properties().Count()==obsN.Length', source)
            self.assertIn('"inconsistent"', source)


if __name__ == "__main__":
    unittest.main()
