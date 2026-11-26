"""過去XMLの完全照合を保ち、正式生成・閉鎖・再配備の順序を合成ARMで確認する。"""
import base64
import copy
from dataclasses import replace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import test_publication_source_generation as generation
from deployment.helpers.apim_api_settings import render_api_settings_fragment

publish = generation.publish


class HistoricalPublicationTests(unittest.TestCase):
    def execute(self, base, plan, authentication, transport, checkpoints):
        return generation.SplitPublicationOrderingTests("runTest").execute(base, plan, authentication, transport, checkpoints)

    def fixture(self):
        base, plan, authentication, transport = generation.SplitPublicationOrderingTests("runTest").setup_split()
        fragment = {"suffix": "/policyFragments/core-api-settings", "value": render_api_settings_fragment()}
        transport.source["policies"].append(fragment)
        seed = base64.b64encode(b"http\n{}\n{}").decode()
        for entry in transport.source["policies"]:
            if entry["suffix"].endswith("/policies/policy") and "/operations/" not in entry["suffix"]:
                entry["value"] = ('<policies><inbound><set-variable name="gatewayApiSettingsSource" value="' + seed
                    + '" /><include-fragment id="core-api-settings" fragment-id="core-api-settings" />'
                    '<base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')
        base.template["outputs"]["entraPublicationSource"]["value"] = copy.deepcopy(transport.source)
        base.template["resources"]["settings"] = {"type": "Microsoft.ApiManagement/service/policyFragments",
                                                  "name": "core-api-settings", "apiVersion": publish.APIM_API}
        checkpoints = []
        self.execute(base, plan, authentication, transport, checkpoints)
        base.state["apiPublicationSourceCheckpoint"] = copy.deepcopy(checkpoints[-1])
        authentication.update(previousValue=authentication["value"], previousEntra=copy.deepcopy(plan["entraAuthentication"]), changed=False)
        resource_id = transport.apim + fragment["suffix"]
        root = ET.fromstring(fragment["value"])
        variable = root.find("set-variable[@name='gatewayAuditSettings']")
        variable.set("value", variable.get("value").replace("@{", "@{ /* synthetic historical generation */", 1))
        old_xml = ET.tostring(root, encoding="unicode")
        old_fragment = next(entry for entry in base.state["apiPublicationSourceCheckpoint"]["source"]["policies"]
                            if entry["suffix"] == fragment["suffix"])
        old_fragment["value"] = old_xml
        transport.refresh(resource_id, {"format": "rawxml", "value": old_xml})
        transport.refresh(transport.apim + "/policyFragments", value=[{"id": resource_id}])
        transport.calls.clear()
        checkpoints.clear()
        return base, plan, authentication, transport, checkpoints, resource_id, old_xml

    def test_normal_update_reads_old_xml_before_closing_then_checks_current_source(self):
        base, plan, authentication, transport, checkpoints, resource_id, old_xml = self.fixture()
        baseline = copy.deepcopy(base.state["apiPublicationSourceCheckpoint"])
        result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(base.state["apiPublicationSourceCheckpoint"], baseline)
        reads = [i for i, call in enumerate(transport.calls) if call[0] == "GET" and call[1].split("?", 1)[0] == resource_id]
        close = next(i for i, call in enumerate(transport.calls) if call[0] == "DELETE" and call[1].startswith(transport.open_id + "/apis/"))
        stage = next(i for i, call in enumerate(transport.calls) if call[0] == "PUT" and call[1] == plan["deploymentId"])
        saved = next(i for i, call in enumerate(transport.calls) if call[0] == "CHECKPOINT")
        self.assertTrue(any(i < close for i in reads))
        self.assertTrue(any(stage < i < saved for i in reads))
        self.assertLess(close, stage)
        self.assertLess(stage, saved)
        self.assertEqual(len(checkpoints), 1)
        final_xml = next(entry["value"] for entry in checkpoints[0]["source"]["policies"]
                         if entry["suffix"] == "/policyFragments/core-api-settings")
        self.assertNotEqual(final_xml, old_xml)
        self.assertEqual(transport.inventory[resource_id]["body"]["properties"]["value"], final_xml)

    def test_current_xml_cannot_replace_the_historical_readback(self):
        base, plan, authentication, transport, checkpoints, resource_id, _ = self.fixture()
        transport.refresh(resource_id, {"format": "rawxml", "value": render_api_settings_fragment()})
        before = copy.deepcopy(transport.inventory)
        with self.assertRaisesRegex(ValueError, "Raw policy readback differs"):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(checkpoints, [])
        self.assertTrue(all(call[0] in ("GET", "POST") for call in transport.calls))

    def test_normal_update_resumes_after_one_prior_employee_membership_close(self):
        base, plan, authentication, transport, checkpoints, resource_id, old_xml = self.fixture()
        expected_members = set(transport.members)
        self.assertEqual(len(expected_members), 2)
        already_closed = sorted(expected_members)[0]
        remaining = expected_members - {already_closed}
        association = transport.open_id + "/apis/" + already_closed.rsplit("/", 1)[-1]
        accepted = transport.request("DELETE", association + "?api-version=" + publish.APIM_API)
        self.assertEqual(accepted["statusCode"], 202)
        self.assertEqual(transport.members, remaining)
        transport.calls.clear()
        baseline = copy.deepcopy(base.state["apiPublicationSourceCheckpoint"])
        native_id = transport.apim + "/products/tool-users"
        native = copy.deepcopy(transport.inventory[native_id])
        prior_global = copy.deepcopy(transport.inventory[transport.global_id])
        original_stage = transport.install_stage

        def closed_stage():
            self.assertEqual(transport.members, set())
            self.assertEqual(transport.inventory[native_id], native)
            self.assertEqual(transport.inventory[transport.global_id], prior_global)
            self.assertEqual(transport.inventory[resource_id]["body"]["properties"]["value"], old_xml)
            original_stage()

        transport.install_stage = closed_stage
        result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["managementPlaneVerified"])
        self.assertTrue(result["entraAuthentication"]["employeePathInterrupted"])
        self.assertEqual(transport.members, expected_members)
        self.assertEqual(base.state["apiPublicationSourceCheckpoint"], baseline)
        self.assertEqual(transport.inventory[native_id]["body"]["properties"], native["body"]["properties"])
        closes = [(i, call[1]) for i, call in enumerate(transport.calls)
                  if call[0] == "DELETE" and call[1].startswith(transport.open_id + "/apis/")]
        self.assertEqual([path for _, path in closes], [transport.open_id + "/apis/" + next(iter(remaining)).rsplit("/", 1)[-1]])
        stage = next(i for i, call in enumerate(transport.calls) if call[0] == "PUT" and call[1] == plan["deploymentId"])
        saved = next(i for i, call in enumerate(transport.calls) if call[0] == "CHECKPOINT")
        old_reads = [i for i, call in enumerate(transport.calls)
                     if call[0] == "GET" and call[1].split("?", 1)[0] == resource_id]
        self.assertTrue(any(i < closes[0][0] for i in old_reads))
        self.assertLess(closes[0][0], stage)
        self.assertLess(stage, saved)
        reopen = [(i, call[1]) for i, call in enumerate(transport.calls)
                  if call[0] == "PUT" and call[1].startswith(transport.open_id + "/apis/")]
        self.assertEqual({path for _, path in reopen}, {transport.open_id + "/apis/" + member.rsplit("/", 1)[-1]
                                                       for member in expected_members})
        self.assertTrue(all(saved < i for i, _ in reopen))
        self.assertEqual(len(checkpoints), 1)

    def test_partial_employee_membership_with_an_unreviewed_member_is_not_resumed(self):
        base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
        already_closed = sorted(transport.members)[0]
        association = transport.open_id + "/apis/" + already_closed.rsplit("/", 1)[-1]
        transport.request("DELETE", association + "?api-version=" + publish.APIM_API)
        transport.members.add(transport.apim + "/apis/unreviewed")
        transport.sync_members()
        transport.calls.clear()
        before = copy.deepcopy(transport.inventory)
        baseline = copy.deepcopy(base.state["apiPublicationSourceCheckpoint"])
        with self.assertRaisesRegex(ValueError, "unreviewed or duplicate API/revision memberships"):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(base.state["apiPublicationSourceCheckpoint"], baseline)
        self.assertEqual(checkpoints, [])
        self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))

    def test_nonpolicy_change_cannot_be_used_as_a_historical_scope(self):
        base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
        current = base.template["outputs"]["entraPublicationSource"]["value"]
        current["apis"][0]["properties"]["path"] += "-changed"
        before = copy.deepcopy(transport.inventory)
        with self.assertRaisesRegex(ValueError, "same complete current API scope"):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(checkpoints, [])
        self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))

    def test_response_definition_is_added_only_after_the_historical_policy_migration(self):
        base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
        old_statuses = (200, 400, 413, 422, 429, 502, 504)
        response = lambda status: {"statusCode": status, "description": "Synthetic response contract.",
                                   "representations": [{"contentType": "application/json"}]}
        old_responses = [response(status) for status in old_statuses]
        final_responses = [response(status) for status in (200, 400, 413, 422, 429, 500, 502, 504)]
        old_child = base.state["apiPublicationSourceCheckpoint"]["source"]["apiChildren"][0]
        old_child["properties"]["responses"] = copy.deepcopy(old_responses)
        operation_id = transport.apim + old_child["suffix"]
        transport.refresh(operation_id, old_child["properties"])
        current = base.template["outputs"]["entraPublicationSource"]["value"]
        current_child = next(entry for entry in current["apiChildren"] if entry["suffix"] == old_child["suffix"])
        current_child["properties"]["responses"] = copy.deepcopy(final_responses)
        transport.source = copy.deepcopy(current)

        before = copy.deepcopy(transport.inventory)
        with self.assertRaisesRegex(ValueError, "same complete current API scope"):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(checkpoints, [])

        current_child["properties"]["responses"] = copy.deepcopy(old_responses)
        transport.source = copy.deepcopy(current)
        transport.calls.clear()
        first = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(first["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(transport.inventory[operation_id]["body"]["properties"]["responses"], old_responses)
        self.assertEqual(len(checkpoints), 1)
        base.state["apiPublicationSourceCheckpoint"] = copy.deepcopy(checkpoints[-1])
        authentication.update(previousValue=authentication["value"],
                              previousEntra=copy.deepcopy(plan["entraAuthentication"]), changed=False)

        current_child["properties"]["responses"] = copy.deepcopy(final_responses)
        transport.source = copy.deepcopy(current)
        transport.calls.clear()
        checkpoints.clear()
        second = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(second["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(transport.inventory[operation_id]["body"]["properties"]["responses"], final_responses)
        self.assertEqual(len(checkpoints), 1)
        final_child = next(entry for entry in checkpoints[0]["source"]["apiChildren"]
                           if entry["suffix"] == old_child["suffix"])
        self.assertEqual(final_child["properties"]["responses"], final_responses)

    def test_completed_core_backend_expectations_are_kept_in_the_historical_scope(self):
        base, plan, authentication, transport, checkpoints, resource_id, old_xml = self.fixture()
        foundation = {transport.apim + "/backends/verified-foundation": {
            "protocol": "http", "url": "https://model.example.com/core"}}
        completed_policies = []

        def complete(manifest, *_):
            completed_policies.append(manifest.policies[resource_id])
            return replace(manifest, backends={**manifest.backends, **foundation})

        with mock.patch.object(publish.entra_publication, "complete_named_value_expectations", side_effect=complete):
            result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(len(checkpoints), 1)
        self.assertIn(old_xml, completed_policies)
        self.assertIn(render_api_settings_fragment(), completed_policies)

    def test_different_completed_core_backends_do_not_close_or_update_the_api(self):
        base, plan, authentication, transport, checkpoints, resource_id, old_xml = self.fixture()
        before = copy.deepcopy(transport.inventory)

        def complete(manifest, *_):
            endpoint = "https://model.example.com/old" if manifest.policies[resource_id] == old_xml else "https://model.example.com/current"
            foundation = {transport.apim + "/backends/verified-foundation": {"protocol": "http", "url": endpoint}}
            return replace(manifest, backends={**manifest.backends, **foundation})

        with mock.patch.object(publish.entra_publication, "complete_named_value_expectations", side_effect=complete):
            with self.assertRaisesRegex(ValueError, "same complete current API scope"):
                self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(checkpoints, [])
        self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))

    def test_partial_generation_does_not_supply_a_scope_for_unrepresented_old_apis(self):
        base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
        selected = plan["parameters"]["apiDefinitions"]["value"][0]["name"]
        plan["parameters"]["apiDefinitions"]["value"] = [plan["parameters"]["apiDefinitions"]["value"][0]]
        plan["publishedApiIds"] = [transport.apim + "/apis/" + selected]
        current = base.template["outputs"]["entraPublicationSource"]["value"]
        current["apis"] = [entry for entry in current["apis"] if entry["name"] == selected]
        for key in ("policies", "apiChildren"):
            current[key] = [entry for entry in current[key]
                            if not entry["suffix"].startswith("/apis/") or entry["suffix"].startswith("/apis/" + selected + "/")]
        base.template["outputs"]["publishedApiResourceIds"]["value"] = plan["publishedApiIds"]
        before = copy.deepcopy(transport.inventory)
        with self.assertRaises(ValueError):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertEqual(checkpoints, [])
        self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))

    def test_new_api_and_its_backend_remain_outside_the_historical_scope(self):
        base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
        current = base.template["outputs"]["entraPublicationSource"]["value"]
        existing = current["apis"][0]["name"]
        added = "added"
        api = copy.deepcopy(current["apis"][0])
        api.update(name=added)
        api["properties"]["path"] = added
        current["apis"].append(api)
        for key in ("policies", "apiChildren"):
            additions = [copy.deepcopy(entry) for entry in current[key]
                         if entry["suffix"].startswith("/apis/" + existing + "/")]
            for entry in additions:
                entry["suffix"] = entry["suffix"].replace("/apis/" + existing + "/", "/apis/" + added + "/", 1)
                if key == "policies" and "/operations/" not in entry["suffix"]:
                    entry["value"] = entry["value"].replace("</inbound>", '<set-backend-service backend-id="added-backend" /></inbound>')
            current[key].extend(additions)
        backend = {"suffix": "/backends/added-backend", "properties": {"protocol": "http", "url": "https://model.example.com/openai/v1"}}
        current["backends"].append(backend)
        definition = copy.deepcopy(plan["parameters"]["apiDefinitions"]["value"][0])
        definition.update(name=added, path=added)
        plan["parameters"]["apiDefinitions"]["value"].append(definition)
        plan["publishedApiIds"].append(transport.apim + "/apis/" + added)
        plan["entraAuthentication"]["apiNames"].append(added)
        base.template["outputs"]["publishedApiResourceIds"]["value"] = plan["publishedApiIds"]
        transport.source = copy.deepcopy(current)
        original = transport.install_stage
        def install():
            original()
            transport.refresh(transport.apim + backend["suffix"], backend["properties"])
        transport.install_stage = install
        result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        self.assertEqual({api["name"] for api in checkpoints[0]["source"]["apis"]}, {existing, "sample", added})
        added_id = transport.apim + "/apis/" + added
        absent_reads = [i for i, call in enumerate(transport.calls) if call[0] == "GET" and call[1].split("?", 1)[0] == added_id]
        stage = next(i for i, call in enumerate(transport.calls) if call[0] == "PUT" and call[1] == plan["deploymentId"])
        self.assertTrue(any(i < stage for i in absent_reads))

    def test_incomplete_history_and_unknown_dependencies_do_not_close_or_update_apis(self):
        for fault in ("source-shape", "malformed-xml", "unknown-named-value", "unknown-fragment", "missing-fragment"):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
                source = base.state["apiPublicationSourceCheckpoint"]["source"]
                if fault == "source-shape":
                    source.pop("apiChildren")
                elif fault == "missing-fragment":
                    source["policies"] = [entry for entry in source["policies"] if entry["suffix"] != "/policyFragments/core-api-settings"]
                else:
                    entry = next(entry for entry in source["policies"] if entry["suffix"] == "/apis/model/policies/policy")
                    if fault == "malformed-xml":
                        entry["value"] = "<policies>"
                    else:
                        root = ET.fromstring(entry["value"])
                        if fault == "unknown-fragment":
                            ET.SubElement(root.find("inbound"), "include-fragment", {"fragment-id": "unknown-fragment"})
                        else:
                            header = ET.SubElement(root.find("inbound"), "set-header", {"name": "synthetic", "exists-action": "override"})
                            ET.SubElement(header, "value").text = "{{unknown-foundation}}"
                        entry["value"] = ET.tostring(root, encoding="unicode")
                    transport.refresh(transport.apim + entry["suffix"], {"format": "rawxml", "value": entry["value"]})
                before = copy.deepcopy(transport.inventory)
                with self.assertRaises(ValueError):
                    self.execute(base, plan, authentication, transport, checkpoints)
                self.assertEqual(transport.inventory, before)
                self.assertEqual(checkpoints, [])
                self.assertTrue(all(call[0] in ("GET", "POST") for call in transport.calls))

    def test_missing_or_failed_current_generation_preserves_the_old_route(self):
        for fault in ("missing", "failed"):
            with self.subTest(fault=fault):
                base, plan, authentication, transport, checkpoints, _, _ = self.fixture()
                before = copy.deepcopy(transport.inventory)
                if fault == "missing":
                    with mock.patch.object(publish, "generate_publication_source", return_value=(base.template, None, None, None)):
                        with self.assertRaises(ValueError):
                            self.execute(base, plan, authentication, transport, checkpoints)
                else:
                    base.template["variables"]["value"] = "new-synthetic-generation"
                    transport.generation.fail_create = True
                    with self.assertRaises(generation.DeploymentError):
                        self.execute(base, plan, authentication, transport, checkpoints)
                self.assertEqual(transport.inventory, before)
                self.assertEqual(checkpoints, [])
                self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))


if __name__ == "__main__":
    unittest.main()
