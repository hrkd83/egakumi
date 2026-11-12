"""Embedding JSON MIME tuning keeps the remaining WAF and route boundaries."""
import copy
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.waf_body import embedding_json_content_policy, embedding_json_profile
from deployment.helpers.portal_gateway import _writable_policy
from waf_policy import desired_policy, load_manifest
from test_gateway_callback_waf import CONFIG, policy


class EmbeddingContentTypeWafTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controls = json.loads((ROOT / "apps/gateway/waf-json-content-controls.json").read_text())
        cls.profiles = {p["name"]: p for p in load_manifest()["profiles"]}

    def json_block(self, value):
        name = self.controls["rule"]["name"]
        selected = [r for r in value["properties"]["customRules"] if r.get("name") == name]
        self.assertEqual(len(selected), 1)
        return selected[0]

    def allowed_header_value(self, value):
        condition, = self.json_block(embedding_json_content_policy(policy()))["matchConditions"]
        self.assertEqual(condition["transforms"], ["Lowercase"])
        pattern, = condition["matchValues"]
        # Azure's end-of-text \z is Python 3.12's \Z. Keep search semantics:
        # fullmatch would conceal an unsafe dollar anchor before a final LF.
        pattern = pattern.replace(r"\z", r"\Z")
        return re.search(pattern, value.lower()) is not None

    def test_media_type_case_and_rfc_parameters_are_accepted(self):
        values = [
            "application/json", "Application/Json", "APPLICATION/JSON",
            " \tApplication/JSON\t ", "application/json;charset=UTF-8",
            "Application/Json; Charset=UTF-8", "application/json ; charset=utf-8 ; x-token=a_b-1",
            'application/json; charset="UTF-8"', 'application/json; empty=""',
            'application/json; note="a,b;c"', 'application/json; note="a\\\"b"',
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(self.allowed_header_value(value))

    def test_non_json_prefixes_duplicate_mime_and_malformed_headers_are_rejected(self):
        values = [
            "", " ", "text/plain", "text/json", "application/problem+json", "application/jsonp",
            "application/json/extra", "application /json", "application/%6ason",
            "application/json,application/json", "Application/Json, text/plain",
            "text/plain, application/json", "application/json; charset=utf-8,application/json",
            "application/json;", "application/json; charset", "application/json; charset=",
            'application/json; charset="unterminated', 'application/json; charset="utf-8"junk',
            "application/json\n", "application/json\r", "application/json\r\nX-Injected: yes",
            "\napplication/json", "application/json; x=ok\n", "application/json\x00",
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(self.allowed_header_value(value))

    def test_only_canonical_embedding_profiles_and_published_subsets_are_selected(self):
        for name in ("g-embeddings", "p-embeddings"):
            profile = self.profiles[name]
            self.assertTrue(embedding_json_profile(profile))
            for path in profile["paths"]:
                with self.subTest(profile=name, publishedPath=path):
                    self.assertTrue(embedding_json_profile({**profile, "paths": [path]}))
            changes = [
                {"name": "g-responses"},
                {"scope": "portal" if profile["scope"] == "gateway" else "gateway"},
                {"paths": []}, {"paths": [profile["paths"][0] + "/neighbor"]},
                {"paths": [profile["paths"][0] + "/*"]},
                {"paths": [*profile["paths"], "/unrelated/extra"]},
            ]
            for change in changes:
                with self.subTest(profile=name, change=change):
                    self.assertFalse(embedding_json_profile({**profile, **change}))
        for name, profile in self.profiles.items():
            if name not in ("g-embeddings", "p-embeddings"):
                self.assertFalse(embedding_json_profile(profile))

    def test_tuning_changes_only_920420_and_adds_a_header_block_without_mutating_base(self):
        source = policy()
        source["properties"]["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"] = [
            {"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [
                {"ruleId": "920470", "state": "Enabled"}, {"ruleId": "920420", "state": "Enabled"}]},
            {"ruleGroupName": "REQUEST-930-APPLICATION-ATTACK-LFI", "rules": [
                {"ruleId": "930110", "state": "Enabled"}]},
        ]
        before = copy.deepcopy(source)
        configured = embedding_json_content_policy(source)
        self.assertEqual(source, before)
        block = self.json_block(configured)
        self.assertEqual((block["ruleType"], block["state"], block["action"]), ("MatchRule", "Enabled", "Block"))
        condition, = block["matchConditions"]
        self.assertEqual(condition["matchVariables"], [{"variableName": "RequestHeaders", "selector": "Content-Type"}])
        self.assertEqual(condition["operator"], "Regex")
        self.assertIs(condition["negationConditon"], True)
        self.assertEqual(condition["transforms"], ["Lowercase"])
        expected = _writable_policy(before)
        expected["properties"]["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"][0]["rules"][1]["state"] = "Disabled"
        configured["properties"]["customRules"].remove(block)
        self.assertEqual(configured, expected)

    def test_existing_priorities_are_preserved_and_owned_name_or_capacity_collision_stops(self):
        source = policy()
        source["properties"]["customRules"] = [
            {"name": "fixture-" + str(priority), "priority": priority, "action": "Block"}
            for priority in (1, 3, 100)
        ]
        configured = embedding_json_content_policy(source)
        self.assertEqual(configured["properties"]["customRules"][:-1], source["properties"]["customRules"])
        self.assertEqual(self.json_block(configured)["priority"], 2)
        collision = copy.deepcopy(source)
        collision["properties"]["customRules"][0]["name"] = self.controls["rule"]["name"]
        exhausted = copy.deepcopy(source)
        exhausted["properties"]["customRules"] = [
            {"name": "fixture-" + str(priority), "priority": priority, "action": "Block"}
            for priority in range(1, 101)
        ]
        for value in (collision, exhausted):
            with self.subTest(kind="name" if value is collision else "capacity"):
                before = copy.deepcopy(value)
                with self.assertRaises(ValueError):
                    embedding_json_content_policy(value)
                self.assertEqual(value, before)

    def test_disabling_content_type_syntax_or_ambiguous_protocol_overrides_is_rejected(self):
        for groups in (
            [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": []}],
            [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [
                {"ruleId": "920470", "state": "Disabled"}]}],
            [{"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [
                {"ruleId": "920420", "state": "Enabled"}, {"ruleId": "920420", "state": "Disabled"}]}],
        ):
            source = policy()
            source["properties"]["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"] = groups
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                embedding_json_content_policy(source)

    def test_desired_policy_upgrade_and_repeated_generation_keep_exclusions_and_one_guard(self):
        source = policy()
        for name in ("g-embeddings", "p-embeddings"):
            profile = self.profiles[name]
            original = copy.deepcopy(source)
            first = desired_policy(source, profile, CONFIG, sources=[source])
            again = desired_policy(source, profile, CONFIG, sources=[first])
            self.assertEqual(first, again)
            self.assertEqual(source, original)
            self.json_block(first)
            self.assertEqual(first["properties"]["policySettings"]["mode"], "Prevention")
            self.assertIs(first["properties"]["policySettings"]["requestBodyCheck"], True)
            for entry in profile["exclusions"]:
                self.assertIn(entry, first["properties"]["managedRules"]["exclusions"])
            altered = copy.deepcopy(first)
            self.json_block(altered)["matchConditions"][0]["negationConditon"] = False
            with self.assertRaisesRegex(ValueError, "differs"):
                desired_policy(source, profile, CONFIG, sources=[altered])

    def test_other_data_profiles_keep_their_previous_content_type_and_custom_controls(self):
        source = policy()
        name = self.controls["rule"]["name"]
        for profile in self.profiles.values():
            if profile["name"] in ("g-embeddings", "p-embeddings"):
                continue
            with self.subTest(profile=profile["name"]):
                configured = desired_policy(source, profile, CONFIG)
                self.assertFalse(any(r.get("name") == name for r in configured["properties"]["customRules"]))
                if profile["name"] != "p-document-upload":
                    self.assertEqual(configured["properties"]["customRules"], source["properties"]["customRules"])
                    self.assertEqual(configured["properties"]["managedRules"]["managedRuleSets"], source["properties"]["managedRules"]["managedRuleSets"])


if __name__ == "__main__":
    unittest.main()
