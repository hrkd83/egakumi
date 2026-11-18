"""固定3入口のquery設定をAPI配備計画と生成XMLまで確認する。"""
import base64
import copy
import json
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import PolicyExpression, compiled_policy_value
from bicep_helpers import compile_bicep
from test_api_protocols import CORE, bound_coverage, configuration
import test_publish_apis as publication


DEFAULT = {"liveCreate": "observe", "realtimeCreate": "observe", "piiMcp": "observe"}


class QueryGuardPublicationTests(unittest.TestCase):
    def setUp(self):
        self.config, self.state = publication.publication_fixture()

    def test_omitted_modes_keep_observation_in_api_publication_plan(self):
        self.assertNotIn("queryGuards", self.config["gateway"])
        plan = publication.publish.prepare_plan(self.config, self.state)
        self.assertEqual(plan["parameters"]["queryGuards"]["value"], DEFAULT)
        self.assertNotIn("queryGuards", self.config["gateway"])

    def test_each_partial_mode_is_completed_without_changing_other_entries_or_input(self):
        for entry in DEFAULT:
            with self.subTest(entry=entry):
                self.config["gateway"]["queryGuards"] = {entry: "reject"}
                before = copy.deepcopy(self.config)
                plan = publication.publish.prepare_plan(self.config, self.state)
                self.assertEqual(plan["parameters"]["queryGuards"]["value"], {**DEFAULT, entry: "reject"})
                self.assertEqual(self.config, before)

    def test_invalid_modes_are_rejected_before_azure_publication(self):
        for modes in (None, [], "reject", {"unknown": "reject"}, {"piiMcp": True}, {"liveCreate": "disabled"}):
            with self.subTest(modes=modes):
                self.config["gateway"]["queryGuards"] = modes
                with self.assertRaises(configuration.ConfigurationError):
                    publication.publish.prepare_plan(self.config, self.state)


class GeneratedQueryGuardPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        cls.config = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")

    def parameters(self, modes):
        return {"apiDefinitions": self.config["apiDefinitions"], "credentialLogin": {},
                "subscriptionKeyHeader": self.config["gateway"]["authentication"]["subscriptionKeyHeader"],
                "entraTenantId": self.config["entraTenantId"],
                "queryGuards": modes, "rateLimits": self.config["gateway"]["rateLimits"]}

    def evaluate(self, value, modes, index=0):
        return PolicyExpression(value, self.template["variables"], functions=self.template.get("functions", [])).evaluate(self.parameters(modes), index)

    def test_module_default_preserves_observation_at_all_three_entries(self):
        self.assertEqual(self.template["parameters"]["queryGuards"]["defaultValue"], DEFAULT)

    def test_generated_http_and_native_mcp_policies_embed_exact_independent_modes(self):
        for modes in (DEFAULT, {"liveCreate": "reject", "realtimeCreate": "observe", "piiMcp": "reject"}):
            expected = base64.b64encode(json.dumps(modes, separators=(",", ":")).encode()).decode()
            for index, api in enumerate(self.config["apiDefinitions"]):
                if api["apiType"] != "http":
                    continue
                xml = compiled_policy_value(self.template, self.parameters(modes), index)
                with self.subTest(modes=modes, api=api["name"]):
                    ET.fromstring(xml)
                    self.assertNotIn("__QUERY_GUARD_MODES_BASE64__", xml)
                    self.assertIn(expected, xml)
            xml = compiled_policy_value(self.template, self.parameters(modes), resource_name="mcpPolicies")
            ET.fromstring(xml)
            self.assertNotIn("__QUERY_GUARD_MODES_BASE64__", xml)
            self.assertIn(expected, xml)

    def test_query_fragments_are_created_before_api_and_native_mcp_policies(self):
        resources = self.template["resources"]
        for resource_name, fragment_id in (("queryGuardFragment", "core-query-guard"),
                                           ("queryTelemetryFragment", "core-query-telemetry")):
            fragment = resources[resource_name]
            self.assertEqual(fragment["type"], "Microsoft.ApiManagement/service/policyFragments")
            self.assertIn(fragment_id, fragment["name"])
            xml = self.evaluate(fragment["properties"]["value"], DEFAULT)
            self.assertIsNone(ET.fromstring(xml).find(".//include-fragment"))
            for policy in ("apiPolicies", "mcpPolicies"):
                self.assertIn(resource_name, resources[policy]["dependsOn"])


if __name__ == "__main__":
    unittest.main()
