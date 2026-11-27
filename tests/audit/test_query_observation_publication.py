"""観測専用のAPI配備計画と生成XMLを、合成設定・オフラインARM展開で確認する。"""
import base64
import copy
import json
import unittest
import xml.etree.ElementTree as ET

from tests.support.arm_helpers import DiscoveryExpression, compiled_policy_value
from tests.support.bicep_helpers import compile_bicep
from tests.gateway.publication.test_api_protocols import CORE, bound_coverage, bound_external_coverage, configuration
from deployment.helpers.apim_query_observation import (
    QUERY_OBSERVATION_FRAGMENT_ID, render_query_observation_fragment,
    validate_query_observation_scope,
)
from deployment.helpers.apim_api_request import render_api_request_preparation_fragment
from tests.gateway.publication.test_entra_publication import APIM
import tests.gateway.publication.test_publish_apis as publication


class QueryObservationPublicationPlanTests(unittest.TestCase):
    def setUp(self):
        self.config, self.state = publication.publication_fixture()

    def test_plan_needs_no_observation_mode_and_preserves_the_input(self):
        self.config["apiDefinitions"][0]["operations"][0]["queryObservation"] = "vendorMessages"
        before = copy.deepcopy(self.config)
        plan = publication.publish.prepare_plan(self.config, self.state)
        self.assertNotIn("queryGuards", plan["parameters"])
        self.assertNotIn("queryObservations", plan["parameters"])
        self.assertEqual(plan["parameters"]["apiDefinitions"]["value"][0]["operations"][0]["queryObservation"],
                         "vendorMessages")
        self.assertEqual(self.config, before)


class GeneratedQueryObservationPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/api-gateway/api-publication.bicep")
        cls.config = configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "infra/config")

    def parameters(self, config=None):
        config = self.config if config is None else config
        return {"apiDefinitions": config["apiDefinitions"], "credentialLogin": {},
                "subscriptionKeyHeader": config["gateway"]["authentication"]["subscriptionKeyHeader"],
                "entraTenantId": config["entraTenantId"]}

    def evaluate(self, value, config=None, index=0):
        supplied = {name: definition["defaultValue"]
                    for name, definition in self.template["parameters"].items()
                    if "defaultValue" in definition}
        supplied.update(self.parameters(config))
        evaluator = DiscoveryExpression("[true()]", self.template["variables"],
                                        functions=self.template.get("functions", []))
        return evaluator.resolve(value, supplied, index)

    def assert_observation_order(self, value, name):
        root = ET.fromstring(value)
        calls = root.findall("inbound/include-fragment[@fragment-id='core-query-observation']")
        self.assertEqual(len(calls), 1)
        preparation = root.find("inbound/include-fragment[@fragment-id='core-api-request-preparation']")
        inbound = list(root.find("inbound"))
        self.assertLess(inbound.index(preparation), inbound.index(calls[0]))
        self.assertIsNone(root.find(".//choose[@id='core-query-rejection']"))
        self.assertIsNone(root.find(".//set-variable[@name='gatewayQueryGuardModes']"))
        self.assertNotIn("QUERY_PARAMETERS_NOT_ALLOWED", value)
        self.assertNotIn("QUERY_GUARD_NOT_CONFIGURED", value)
        validate_query_observation_scope({
            APIM + "/apis/" + name + "/policies/policy": value,
            APIM + "/policyFragments/core-api-request-preparation": render_api_request_preparation_fragment(),
        }, APIM)

    def test_query_observation_fragment_has_one_publication_entry(self):
        source = self.template["outputs"]["entraPublicationSource"]["value"]
        # 部品の公開対象だけを確認する。API本文は各APIの順序・経路testで展開する。
        fragment_config = copy.deepcopy(self.config)
        fragment_config["apiDefinitions"] = []
        policies = self.evaluate(source["policies"], fragment_config)
        matching = [entry for entry in policies
                    if entry["suffix"] == "/policyFragments/core-query-observation"]
        self.assertEqual(len(matching), 1)

    def test_http_and_generated_mcp_policies_observe_without_a_rejection_branch(self):
        for index, api in enumerate(self.config["apiDefinitions"]):
            if api["apiType"] != "http":
                continue
            with self.subTest(api=api["name"]):
                value = compiled_policy_value(self.template, self.parameters(), index)
                self.assert_observation_order(value, api["name"])
        value = compiled_policy_value(self.template, self.parameters(), resource_name="mcpPolicies")
        self.assert_observation_order(value, "generated-mcp")

    def test_custom_http_and_native_mcp_labels_follow_public_registration(self):
        raw = bound_external_coverage()
        http = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-rest-v1")
        http["operations"][0].update(queryObservation="vendorMessages", method="POST", urlTemplate="/messages")
        streamable = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-mcp-v1")
        streamable["mcpProxy"]["endpoints"][0].update(queryObservation="workiqSearch", uriTemplate="/downstream/workiq")
        sse = next(api for api in raw["apiDefinitions"] if api["name"] == "sample-mcp-legacy")
        for endpoint in sse["mcpProxy"]["endpoints"]:
            endpoint["queryObservation"] = "boxBrowse" if endpoint["name"] == "message" else "workiqEvents"
        config = configuration.normalize_configuration(raw, base_directory=CORE / "infra/config")
        expected = {
            "sample-rest-v1": ({"echo-get": "vendorMessages"}, {}),
            "sample-mcp-v1": ({}, {method + " /mcp": "workiqSearch" for method in ("POST", "GET", "DELETE")}),
            "sample-mcp-legacy": ({}, {"GET /sse": "workiqEvents", "POST /legacy/messages/": "boxBrowse"}),
        }
        for index, api in enumerate(config["apiDefinitions"]):
            if api["name"] not in expected and api["apiType"] != "websocket":
                continue
            value = compiled_policy_value(self.template, self.parameters(config), index)
            root = ET.fromstring(value)
            encoded = root.find("inbound/set-variable[@name='gatewayApiSettingsSource']").get("value")
            lines = base64.b64decode(encoded, validate=True).decode("utf-8").split("\n")
            controls = json.loads(lines[2])
            with self.subTest(api=api["name"]):
                self.assertEqual((controls["queryObservations"], controls["mcpQueryObservations"]),
                                 expected.get(api["name"], ({}, {})))
                if api["apiType"] == "websocket":
                    self.assertIsNone(root.find("inbound/include-fragment[@fragment-id='core-query-observation']"))
                else:
                    self.assert_observation_order(value, api["name"])

    def test_observation_fragment_has_only_one_writer_and_precedes_api_policies(self):
        resources = self.template["resources"]
        fragment = resources["queryObservationFragment"]
        self.assertEqual(fragment["type"], "Microsoft.ApiManagement/service/policyFragments")
        self.assertIn(QUERY_OBSERVATION_FRAGMENT_ID, fragment["name"])
        value = self.evaluate(fragment["properties"]["value"])
        root = ET.fromstring(value)
        self.assertEqual(root.tag, "fragment")
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0].tag, "set-variable")
        self.assertEqual(root[0].get("name"), "gatewayQueryObservation")
        self.assertEqual(root[0].get("value"), ET.fromstring(render_query_observation_fragment())[0].get("value"))
        self.assertNotIn("{{core-query-guard-modes}}", value)
        self.assertNotIn('new JProperty("mode"', root[0].get("value"))
        self.assertNotIn('new JProperty("allowed"', root[0].get("value"))
        self.assertNotIn('new JProperty("status"', root[0].get("value"))
        self.assertIn("観測", fragment["properties"]["description"])
        for policy in ("apiPolicies", "mcpPolicies"):
            self.assertIn("queryObservationFragment", resources[policy]["dependsOn"])
            self.assertIn("queryTelemetryFragment", resources[policy]["dependsOn"])

    def test_telemetry_stays_a_leaf_with_new_observation_contract(self):
        fragment = self.template["resources"]["queryTelemetryFragment"]
        value = self.evaluate(fragment["properties"]["value"])
        root = ET.fromstring(value)
        self.assertIsNone(root.find(".//include-fragment"))
        self.assertEqual(root.find(".//trace").get("source"), "core-query-observation")
        message = root.find(".//message").text
        self.assertIn('new JProperty("event", "query-guard")', message)
        self.assertIn('new JProperty("schemaVersion", 2)', message)
        self.assertIn('new JProperty("auditSchemaVersion", 2)', message)
        self.assertIn("gatewayQueryObservation", message)
        for removed in ('"mode"', '"countBucket"', '"query-rejected"', '"guard-error"'):
            self.assertNotIn(removed, message)


if __name__ == "__main__":
    unittest.main()
