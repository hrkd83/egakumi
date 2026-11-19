"""gRPC専用カタログPATCHの撤去と、旧契約の明示拒否を通信せず検証する。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from catalog_publication import finalize_descriptions
from test_catalog_descriptions import public_contract


class NoNetwork:
    def request(self, *args, **kwargs):
        raise AssertionError("Catalog finalization must not call Azure")


class CatalogPublicationTests(unittest.TestCase):
    def test_normal_descriptions_are_provisioned_without_followup_patch(self):
        config = public_contract()
        before = copy.deepcopy(config)
        expected = {"enabled": False, "checked": 0, "updated": 0, "apiIds": []}
        self.assertEqual(finalize_descriptions(config, {}, NoNetwork()), expected)
        self.assertEqual(finalize_descriptions(config, {}, NoNetwork()), expected)
        self.assertEqual(config, before)

    def test_retired_grpc_inputs_are_rejected_even_when_publication_is_disabled(self):
        for field in ("apiType", "grpc"):
            for publish in (True, False):
                config = public_contract()
                config["publishApis"] = publish
                config["apiDefinitions"][0][field] = "grpc" if field == "apiType" else {}
                with self.subTest(field=field, publish=publish), self.assertRaisesRegex(ValueError, "retired"):
                    finalize_descriptions(config, {}, NoNetwork())


if __name__ == "__main__":
    unittest.main()
