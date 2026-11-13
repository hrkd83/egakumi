"""AI MIME case tuning keeps native formats and the remaining WAF boundaries."""
import copy
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.waf_body import ai_content_type_policy, ai_content_type_profile
from deployment.helpers.portal_gateway import _writable_policy
from waf_policy import desired_policy, load_manifest
from test_gateway_callback_waf import CONFIG, policy


class AiContentTypeWafTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controls = json.loads((ROOT / "apps/gateway/waf-json-content-controls.json").read_text())
        cls.profiles = {p["name"]: p for p in load_manifest()["profiles"]}
        cls.selected = {p["name"]: p for p in cls.controls["profiles"]}

    def content_policy(self, source, name="g-embeddings"):
        return ai_content_type_policy(source, self.profiles[name])

    def json_block(self, value):
        name = self.controls["rule"]["name"]
        selected = [r for r in value["properties"]["customRules"] if r.get("name") == name]
        self.assertEqual(len(selected), 1)
        return selected[0]

    def header_is_blocked(self, value, name="g-embeddings"):
        conditions = self.json_block(self.content_policy(policy(), name))["matchConditions"]
        self.assertEqual(len(conditions), 2)
        matches = []
        for condition in conditions:
            self.assertEqual(condition["transforms"], ["Lowercase"])
            # Azure's end-of-text \z is Python 3.12's \Z. Keep search
            # semantics so fullmatch cannot conceal an unsafe dollar anchor.
            result = value is not None and any(re.search(pattern.replace(r"\z", r"\Z"), value.lower())
                                               for pattern in condition["matchValues"])
            matches.append(not result if condition["negationConditon"] else bool(result))
        return all(matches)

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
                self.assertFalse(self.header_is_blocked(value))

    def test_non_json_prefixes_duplicate_mime_and_malformed_headers_are_rejected(self):
        values = [
            " ", "text/plain", "text/json", "application/problem+json", "application/jsonp",
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
                self.assertTrue(self.header_is_blocked(value))

    def test_missing_content_type_is_not_newly_required_for_bodyless_mcp_methods(self):
        for name in self.selected:
            for value in (None, ""):
                with self.subTest(profile=name, header=value):
                    self.assertFalse(self.header_is_blocked(value, name))
        condition, _ = self.json_block(self.content_policy(policy(), "g-pii-redact"))["matchConditions"]
        self.assertFalse(condition["negationConditon"])
        self.assertEqual(condition["matchValues"], [r"[\s\S]"])

    def test_only_image_and_speech_mixed_policies_accept_multipart_beside_json(self):
        multipart = ["multipart/form-data", "Multipart/Form-Data; Boundary=fixture-boundary",
                     'MULTIPART/FORM-DATA; boundary="fixture boundary"']
        self.assertEqual({name for name, p in self.selected.items() if p["format"] == "json-or-multipart"},
                         {"g-images", "p-speech"})
        for name in self.selected:
            self.assertFalse(self.header_is_blocked("Application/Json; Charset=UTF-8", name))
            for value in multipart:
                with self.subTest(profile=name, header=value):
                    self.assertEqual(self.header_is_blocked(value, name), name not in ("g-images", "p-speech"))
            self.assertTrue(self.header_is_blocked("audio/wav", name))

    def test_only_the_18_canonical_profiles_and_published_subsets_are_selected(self):
        self.assertEqual(set(self.selected), set(self.profiles) - {"g-transcribe", "p-document-upload", "g-document-status", "g-realtime-ticket"})
        self.assertEqual(sum(len(p["paths"]) for p in self.selected.values()), 26)
        for name in self.selected:
            profile = self.profiles[name]
            self.assertTrue(ai_content_type_profile(profile))
            for path in profile["paths"]:
                with self.subTest(profile=name, publishedPath=path):
                    self.assertTrue(ai_content_type_profile({**profile, "paths": [path]}))
            changes = [
                {"name": "g-unlisted"},
                {"scope": "portal" if profile["scope"] == "gateway" else "gateway"},
                {"paths": []}, {"paths": [profile["paths"][0] + "/neighbor"]},
                {"paths": [profile["paths"][0] + "/*"]},
                {"paths": [*profile["paths"], "/unrelated/extra"]},
            ]
            for change in changes:
                with self.subTest(profile=name, change=change):
                    self.assertFalse(ai_content_type_profile({**profile, **change}))
        for name, profile in self.profiles.items():
            if name not in self.selected:
                self.assertFalse(ai_content_type_profile(profile))

    def test_tuning_changes_only_920420_and_adds_a_header_block_without_mutating_base(self):
        source = policy()
        source["properties"]["managedRules"]["managedRuleSets"][0]["ruleGroupOverrides"] = [
            {"ruleGroupName": "REQUEST-920-PROTOCOL-ENFORCEMENT", "rules": [
                {"ruleId": "920470", "state": "Enabled"}, {"ruleId": "920420", "state": "Enabled"}]},
            {"ruleGroupName": "REQUEST-930-APPLICATION-ATTACK-LFI", "rules": [
                {"ruleId": "930110", "state": "Enabled"}]},
        ]
        before = copy.deepcopy(source)
        configured = self.content_policy(source)
        self.assertEqual(source, before)
        block = self.json_block(configured)
        self.assertEqual((block["ruleType"], block["state"], block["action"]), ("MatchRule", "Enabled", "Block"))
        present, condition = block["matchConditions"]
        self.assertFalse(present["negationConditon"])
        self.assertEqual(present["matchValues"], [r"[\s\S]"])
        for entry in (present, condition):
            self.assertEqual(entry["matchVariables"], [{"variableName": "RequestHeaders", "selector": "Content-Type"}])
            self.assertEqual(entry["operator"], "Regex")
            self.assertEqual(entry["transforms"], ["Lowercase"])
        self.assertIs(condition["negationConditon"], True)
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
        configured = self.content_policy(source)
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
                    self.content_policy(value)
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
                self.content_policy(source)

    def test_desired_policy_upgrade_and_repeated_generation_keep_exclusions_and_one_guard(self):
        source = policy()
        for name in self.selected:
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
            self.json_block(altered)["matchConditions"][1]["negationConditon"] = False
            with self.assertRaisesRegex(ValueError, "differs"):
                desired_policy(source, profile, CONFIG, sources=[altered])

    def test_other_data_profiles_keep_their_previous_content_type_and_custom_controls(self):
        source = policy()
        name = self.controls["rule"]["name"]
        for profile in self.profiles.values():
            if profile["name"] in self.selected:
                continue
            with self.subTest(profile=profile["name"]):
                configured = desired_policy(source, profile, CONFIG)
                self.assertFalse(any(r.get("name") == name for r in configured["properties"]["customRules"]))
                if profile["name"] != "p-document-upload":
                    self.assertEqual(configured["properties"]["customRules"], source["properties"]["customRules"])
                    self.assertEqual(configured["properties"]["managedRules"]["managedRuleSets"], source["properties"]["managedRules"]["managedRuleSets"])


if __name__ == "__main__":
    unittest.main()
