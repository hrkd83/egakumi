"""棚卸した部品はAzure名・XML・直接埋込みC#の所有先を1対1にする。"""
from policy_fixture_paths import policy_fixture_path
from pathlib import Path
import unittest

from configuration_fixtures import CORE
from deployment.helpers.apim_audit import (
    CORE_AUTHENTICATED_SUBJECT_MARKER, CORE_GATEWAY_TELEMETRY_MARKER,
    CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER, CORE_API_SETTINGS_MARKER, CORE_API_INPUT_VALIDATION_MARKER, CORE_API_OPERATION_SETTINGS_MARKER, CORE_API_AUTHENTICATION_RESULT_MARKER, CORE_MODEL_LIMITS_MARKER, CORE_SPEECH_SSML_GUARD_MARKER, CORE_QUERY_OBSERVATION_MARKER, CORE_QUERY_TELEMETRY_MARKER, SOURCE_EXPRESSION_FILES,
    SUBJECT_OBSERVATION_MARKER,
    CORE_DOCUMENT_INPUT_VALIDATION_MARKER,
)

POLICIES = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies"
PRIMARY = {
    "core-request-context": CORE_REQUEST_CONTEXT_MARKER,
    "core-audit-authenticated-subject": CORE_AUTHENTICATED_SUBJECT_MARKER,
    "core-trusted-ingress": CORE_TRUSTED_INGRESS_MARKER,
    "core-credential-browser-exception": CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER,
    "core-gateway-telemetry": CORE_GATEWAY_TELEMETRY_MARKER,
    "core-api-settings": CORE_API_SETTINGS_MARKER,
    "core-api-input-validation": CORE_API_INPUT_VALIDATION_MARKER,
    "core-api-operation-settings": CORE_API_OPERATION_SETTINGS_MARKER,
    "core-model-limits": CORE_MODEL_LIMITS_MARKER,
    "core-speech-ssml-guard": CORE_SPEECH_SSML_GUARD_MARKER,
    "core-document-input-validation": CORE_DOCUMENT_INPUT_VALIDATION_MARKER,
    "core-api-authentication-result": CORE_API_AUTHENTICATION_RESULT_MARKER,
    "core-query-observation": CORE_QUERY_OBSERVATION_MARKER,
    "core-query-telemetry": CORE_QUERY_TELEMETRY_MARKER,
}
PURPOSES = {identifier: "request" if identifier in ("core-query-observation", "core-api-input-validation", "core-api-operation-settings", "core-model-limits", "core-speech-ssml-guard", "core-document-input-validation") else "settings" if identifier == "core-api-settings" else "ingress" if identifier == "core-trusted-ingress" else "authentication" if identifier in ("core-credential-browser-exception", "core-api-authentication-result") else "audit"
            for identifier in PRIMARY}


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

    def test_consumers_not_yet_reviewed_use_only_the_separate_legacy_expression(self):
        self.assertEqual(SOURCE_EXPRESSION_FILES[SUBJECT_OBSERVATION_MARKER], "expressions/legacy/audit-subject-observation.cs")
        legacy = POLICIES / "expressions/legacy/audit-subject-observation.cs"
        self.assertTrue(legacy.is_file())
        self.assertEqual(legacy.read_bytes(), (POLICIES / "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs").read_bytes())
        for name in ("credential-telemetry.xml", "credential-connection-outbound.xml"):
            source = (policy_fixture_path(POLICIES, name)).read_text(encoding="utf-8")
            self.assertEqual(source.count(SUBJECT_OBSERVATION_MARKER), 1)
            self.assertFalse(any(marker in source for marker in PRIMARY.values()))


if __name__ == "__main__":
    unittest.main()
