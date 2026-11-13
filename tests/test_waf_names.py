"""WAF名は保存済みIDを再利用し、GatewayとPortalの所有範囲を区別する。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers.waf_names import DATA_POLICY_KEYS, POLICY_KEYS, policy_id, policy_name


class WafNamesTests(unittest.TestCase):
    def configuration(self, identifier="abc123"):
        return {"subscriptionId": "00000000-0000-0000-0000-000000000000",
                "resourceGroupNames": {"nw": "rg-test-fixture-nw"},
                "services": {"apimInstanceId": identifier}}

    def test_names_are_stable_and_scoped_to_the_purpose_and_prepared_id(self):
        config = self.configuration()
        for key in ("g-base", "p-base", "g-chat-completions", "g-pii-redact", "p-speech"):
            with self.subTest(key=key):
                self.assertEqual(policy_name(config, key), f"wafpol-{key}-abc123")
                self.assertEqual(policy_name(config, key), policy_name(config, key))
                self.assertTrue(policy_id(config, key).endswith("/" + policy_name(config, key)))
                self.assertIn("/resourceGroups/rg-test-fixture-nw/providers/", policy_id(config, key))
                self.assertNotEqual(policy_name(config, key), policy_name(self.configuration("xyz987"), key))
        self.assertNotEqual(policy_name(config, "g-speech"), policy_name(config, "p-speech"))

    def test_missing_or_invalid_id_is_rejected_without_generating_a_fallback(self):
        for value in (None, "", "abc12", "abc1234", "ABC123", "abc-12", "../bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy_name(self.configuration(value), "g-base")
        with self.assertRaises(ValueError):
            policy_name({}, "g-base")

    def test_resource_keys_cannot_escape_or_use_unowned_scope(self):
        for key in (None, "", "gateway-base", "portal-base", "x-base", "g-Base", "g-a/b", "p-a?b", "g--base", "g-a-", "g-" + "a" * 41):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy_name(self.configuration(), key)

    def test_only_the_defined_28_purposes_can_produce_a_policy_name(self):
        self.assertEqual(len(POLICY_KEYS), 28)
        self.assertEqual(len(DATA_POLICY_KEYS), 22)
        self.assertEqual(sum(key.startswith("g-") for key in POLICY_KEYS), 16)
        self.assertEqual(sum(key.startswith("p-") for key in POLICY_KEYS), 12)
        generated = {policy_name(self.configuration(), key) for key in POLICY_KEYS}
        self.assertEqual(len(generated), 28)
        for key in ("g-other", "p-other", "g-unlisted-purpose", "p-unlisted-purpose"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy_name(self.configuration(), key)


if __name__ == "__main__":
    unittest.main()
