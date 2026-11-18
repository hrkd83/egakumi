"""監査の説明を正本に保持し、複製時もC#の実行トークンを変えないことを確認する。"""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import (
    COMPACT_SOURCE_COMMENT, CORE_GATEWAY_TELEMETRY_MARKER, render_audit_policy,
)
from policy_fixture_paths import policy_fixture_path


POLICIES = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies"
_TOKENS = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\r\n]*|/\*[\s\S]*?\*/|[^\s]')


def executable_tokens(expression):
    """文字列・文字リテラルを保持し、コメントと式の空白だけを比較から外す。"""
    return [match.group() for match in _TOKENS.finditer(expression)
            if not match.group().startswith(("//", "/*"))]


class TelemetrySourceExplanationTests(unittest.TestCase):
    def test_canonical_explanations_remain_visible_and_copies_keep_the_reference(self):
        raw = policy_fixture_path(POLICIES, "core-gateway-telemetry.xml").read_text(encoding="utf-8")
        self.assertIn(CORE_GATEWAY_TELEMETRY_MARKER, raw)
        descriptions = [line.strip() for line in raw.splitlines() if line.lstrip().startswith("/* 説明：")]
        self.assertTrue(descriptions)
        full = render_audit_policy(raw)
        for description in descriptions:
            self.assertIn(description, full)
        compact = render_audit_policy(raw, source_comments="compact")
        self.assertNotIn("/* 説明：", compact)
        self.assertIn(COMPACT_SOURCE_COMMENT, compact)
        omitted = render_audit_policy(raw, source_comments="omit")
        self.assertNotIn("/* 説明：", omitted)
        self.assertNotIn(COMPACT_SOURCE_COMMENT, omitted)

    def test_description_removal_preserves_actual_csharp_tokens_in_both_copy_modes(self):
        raw = policy_fixture_path(POLICIES, "core-gateway-telemetry.xml").read_text(encoding="utf-8")
        original = ET.fromstring(render_audit_policy(raw)).find("trace/message").text
        for mode in ("compact", "omit"):
            with self.subTest(mode=mode):
                copied = ET.fromstring(render_audit_policy(raw, source_comments=mode)).find("trace/message").text
                self.assertEqual(executable_tokens(copied), executable_tokens(original))

    def test_unrelated_explanations_and_string_literals_are_not_removed(self):
        unrelated = '<fragment><trace><message>@{\n/* 説明：別の部品の既存説明。 */\nreturn "/* 説明：文字列の値 */";\n}</message></trace></fragment>'
        for mode in ("compact", "omit"):
            with self.subTest(mode=mode):
                self.assertEqual(render_audit_policy(unrelated, source_comments=mode), unrelated)


if __name__ == "__main__":
    unittest.main()
