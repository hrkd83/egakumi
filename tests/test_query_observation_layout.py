"""query判定部品の名前・正本・日本語説明が、Azureへの生成物と対応することを確認する。"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers import apim_query_observation
from deployment.helpers.apim_audit import CORE_QUERY_OBSERVATION_MARKER, SOURCE_EXPRESSION_FILES, _with_source_line_comments
from deployment.helpers.apim_query_observation import QUERY_OBSERVATION_FRAGMENT_ID, render_query_observation_fragment


POLICIES = Path(__file__).resolve().parents[1] / "apps/apim/policies"
DIRECTORY = POLICIES / "fragments/request" / QUERY_OBSERVATION_FRAGMENT_ID


class QueryObservationLayoutTests(unittest.TestCase):
    def test_xml_csharp_and_azure_id_have_one_canonical_same_named_owner(self):
        xml = DIRECTORY / (QUERY_OBSERVATION_FRAGMENT_ID + ".xml")
        cs = DIRECTORY / (QUERY_OBSERVATION_FRAGMENT_ID + ".cs")
        self.assertTrue(xml.is_file())
        self.assertTrue(cs.is_file())
        self.assertEqual(xml.stem, cs.stem)
        self.assertEqual(xml.stem, QUERY_OBSERVATION_FRAGMENT_ID)
        self.assertEqual(SOURCE_EXPRESSION_FILES[CORE_QUERY_OBSERVATION_MARKER], cs.relative_to(POLICIES).as_posix())
        self.assertEqual(list(POLICIES.rglob(xml.name)), [xml])
        self.assertEqual(list(POLICIES.rglob(cs.name)), [cs])
        self.assertEqual(list(POLICIES.glob("query-guard.xml")), [])
        owners = [path for path in POLICIES.rglob("*.xml") if CORE_QUERY_OBSERVATION_MARKER in path.read_text(encoding="utf-8")]
        self.assertEqual(owners, [xml])
        self.assertEqual(xml.read_text(encoding="utf-8").count(CORE_QUERY_OBSERVATION_MARKER), 1)

    def test_renderer_embeds_explanations_without_modes_or_rejection(self):
        rendered = render_query_observation_fragment()
        root = ET.fromstring(rendered)
        self.assertEqual([node.get("name") for node in root], ["gatewayQueryObservation"])
        self.assertNotIn("gatewayQueryGuardModes", rendered)
        self.assertNotIn("core-query-guard-modes", rendered)
        self.assertNotIn(CORE_QUERY_OBSERVATION_MARKER, rendered)
        source = (DIRECTORY / (QUERY_OBSERVATION_FRAGMENT_ID + ".cs")).read_text(encoding="utf-8")
        explanations = [line.strip() for line in _with_source_line_comments(source).splitlines() if line.lstrip().startswith("/*")]
        self.assertGreaterEqual(len(explanations), 4)
        for line in explanations:
            self.assertIn(line, root[0].get("value"))
        self.assertFalse(root.findall(".//include-fragment"))
        self.assertFalse(root.findall(".//base"))

    def test_partial_marker_or_additional_flow_cannot_be_rendered_as_the_fixed_leaf(self):
        template = (DIRECTORY / (QUERY_OBSERVATION_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
        changes = (
            template.replace(CORE_QUERY_OBSERVATION_MARKER, "return false;"),
            template.replace(CORE_QUERY_OBSERVATION_MARKER, CORE_QUERY_OBSERVATION_MARKER + "\n" + CORE_QUERY_OBSERVATION_MARKER),
            template.replace("</fragment>", '<send-request mode="new" />\n</fragment>'),
            template.replace("gatewayQueryObservation", "unreviewed-observation"),
        )
        with tempfile.TemporaryDirectory(prefix="core-query-layout-") as folder:
            temporary = Path(folder)
            target = temporary / (QUERY_OBSERVATION_FRAGMENT_ID + ".xml")
            for changed in changes:
                with self.subTest(template=changed):
                    target.write_text(changed, encoding="utf-8")
                    with mock.patch.object(apim_query_observation, "_DIRECTORY", temporary), self.assertRaises(ValueError):
                        render_query_observation_fragment()


if __name__ == "__main__":
    unittest.main()
