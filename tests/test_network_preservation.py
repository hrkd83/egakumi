"""azdの各段階を正しい順で実行し、失敗時に止まることを検証する。

偽のCLIとARM transportで事前確認・監視準備・構築後設定・撤去の境界を再現する。秘密の一時入力や保持設定を扱うため、成功系だけでなく中断時の挙動も調べる。
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import ssl
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class ExistingNetworkSecurityGroupTests(LifecycleFixture, unittest.TestCase):
    """初回・途中再開・再upで、同じ環境のNSGだけを再利用することを確認する。"""

    def nsg_response(self, suffix):
        name = f"nsg-{self.config['resourceStem']}-{suffix}"
        resource_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                       f"{self.config['resourceGroupNames']['nw']}/providers/Microsoft.Network/networkSecurityGroups/{name}")
        return {"statusCode": 200, "body": {
            "id": resource_id, "name": name, "type": "Microsoft.Network/networkSecurityGroups",
            "location": self.config["location"],
            "tags": {"product": self.config["productName"], "environment": self.config["environmentName"],
                     "azd-env-name": self.config["environmentName"], "custom-owner": "preserved"},
            "properties": {"securityRules": [{"name": "existing-dynamic-rule", "properties": {"priority": 150}}]},
        }}

    def test_first_partial_and_repeated_up_only_read_the_four_exact_nsgs(self):
        for present in ((), ("agw", "pep"), ("agw", "apim", "pep", "vm")):
            responses = [self.nsg_response(suffix) if suffix in present else {"statusCode": 404, "body": None}
                         for suffix in ("agw", "apim", "pep", "vm")]
            transport = ScriptedTransport(responses)
            before = copy.deepcopy(transport.responses)
            with self.subTest(present=present):
                names = lifecycle.discover_existing_network_security_groups(self.config, transport)
                self.assertEqual(names, [f"nsg-{self.config['resourceStem']}-{suffix}" for suffix in present])
                self.assertEqual(len(transport.calls), 4)
                self.assertFalse(transport.mutations())
                self.assertEqual(transport.responses, [])
                for index, suffix in enumerate(("agw", "apim", "pep", "vm")):
                    expected = self.nsg_response(suffix)["body"]["id"] + "?api-version=2025-01-01"
                    self.assertEqual(transport.calls[index]["path"], expected)
                # 読取結果にある動的規則は書換えたりARM入力へ複写したりしない。
                values = lifecycle.parameters(self.config, "core", existing_nsg_names=names)["parameters"]
                self.assertEqual(values["existingNetworkSecurityGroupNames"]["value"], names)
                self.assertNotIn("existing-dynamic-rule", json.dumps(values))
                self.assertEqual(responses, before)

    def test_unverified_identity_location_type_or_owner_stops_without_writes(self):
        changes = [
            ("id", self.nsg_response("agw")["body"]["id"].replace(self.config["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")),
            ("id", self.nsg_response("agw")["body"]["id"].replace(self.config["resourceGroupNames"]["nw"], "rg-other-nw")),
            ("name", "nsg-other-prod-agw"), ("type", "Microsoft.Network/routeTables"),
            ("location", "westus"), ("tags", {}),
            ("tags", {"product": "other", "environment": "test", "azd-env-name": "test"}),
            ("tags", {"product": self.config["productName"], "environment": "prod", "azd-env-name": "test"}),
            ("tags", {"product": self.config["productName"], "environment": "test", "azd-env-name": "prod"}),
        ]
        for field, value in changes:
            response = self.nsg_response("agw")
            response["body"][field] = value
            transport = ScriptedTransport([response])
            with self.subTest(field=field, value=value), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_network_security_groups(self.config, transport)
            self.assertFalse(transport.mutations())

    def test_unknown_http_status_or_malformed_success_never_means_missing(self):
        for response in ({"statusCode": 403, "body": {}}, {"statusCode": 429, "body": {}},
                         {"statusCode": 500, "body": {}}, {"statusCode": 200, "body": None},
                         {"statusCode": 200, "body": []}, {"statusCode": 200, "body": "bad"}):
            transport = ScriptedTransport([response])
            with self.subTest(response=response), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_network_security_groups(self.config, transport)
            self.assertEqual(len(transport.calls), 1)
            self.assertFalse(transport.mutations())

    def test_pure_parameter_builder_defaults_empty_and_copies_discovered_names(self):
        with mock.patch.object(lifecycle, "ArmTransport") as transport:
            self.assertEqual(lifecycle.parameters(self.config, "monitoring")["parameters"]["existingNetworkSecurityGroupNames"]["value"], [])
            names = ["nsg-test-egakumi-agw"]
            values = lifecycle.parameters(self.config, "core", existing_nsg_names=names)
            names.clear()
            self.assertEqual(values["parameters"]["existingNetworkSecurityGroupNames"]["value"], ["nsg-test-egakumi-agw"])
            transport.assert_not_called()

    def test_direct_core_deployment_discovers_before_put(self):
        transport = ScriptedTransport([self.nsg_response(suffix) for suffix in ("agw", "apim", "pep", "vm")]
            + [{"statusCode": 404, "body": None}] * 6 + [
            {"statusCode": 201, "body": {}},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}},
        ])
        with mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "run", return_value={"resources": []}):
            self.assertEqual(lifecycle.deploy_phase(self.config, "core"), {})
        self.assertEqual([call["method"] for call in transport.calls], ["GET"] * 10 + ["PUT", "GET"])
        values = transport.mutations()[0]["body"]["properties"]["parameters"]
        self.assertEqual(values["existingNetworkSecurityGroupNames"]["value"],
                         [f"nsg-{self.config['resourceStem']}-{suffix}" for suffix in ("agw", "apim", "pep", "vm")])

    def test_preup_generates_discovered_internal_parameter_after_authentication(self):
        """初回でもtenant照合→RP登録→環境設定・所有資源読取の順を守る。"""
        expected = [f"nsg-{self.config['resourceStem']}-agw"]
        routes = {f"rt-{self.config['resourceStem']}-agw": [{"name": "actual-vip", "properties": {
            "addressPrefix": "10.40.2.68/32", "nextHopType": "VirtualAppliance", "nextHopIpAddress": "10.40.2.4"}}]}
        observed_order = []
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                mock.patch.object(lifecycle, "local_preflight"), \
                mock.patch.object(lifecycle, "azure_preflight", side_effect=lambda _: observed_order.append("tenant")) as login, \
                mock.patch.object(lifecycle, "register_providers", side_effect=lambda _: observed_order.append("register")) as register, \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
                mock.patch.object(lifecycle, "run", side_effect=lambda _: observed_order.append("environment") or {}), \
                mock.patch.object(lifecycle, "ArmTransport") as arm, \
                mock.patch.object(lifecycle, "discover_existing_network_security_groups", side_effect=lambda *_: observed_order.append("nsg") or expected) as discover, \
                mock.patch.object(lifecycle, "discover_existing_route_table_routes", side_effect=lambda *_: observed_order.append("routes") or routes) as discover_routes, \
                mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False), \
                mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}):
            self.assertEqual(lifecycle.main(["preup"]), 0)
            login.assert_called_once_with(self.config)
            register.assert_called_once_with(self.config)
            discover.assert_called_once_with(self.config, arm.return_value)
            discover_routes.assert_called_once_with(self.config, arm.return_value)
        self.assertEqual(observed_order, ["tenant", "register", "environment", "environment", "environment", "nsg", "routes"])
        self.assertEqual(json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["existingNetworkSecurityGroupNames"]["value"], expected)
        self.assertEqual(json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["existingRouteTableRoutes"]["value"], routes)

    def test_preup_authentication_mismatch_stops_before_discovery_or_env_updates(self):
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                mock.patch.object(lifecycle, "local_preflight"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "azure_preflight", side_effect=lifecycle.DeploymentError("Wrong login")), \
                mock.patch.object(lifecycle, "register_providers") as register, \
                mock.patch.object(lifecycle, "run") as run, \
                mock.patch.object(lifecycle, "discover_existing_network_security_groups") as discover, \
                mock.patch.object(lifecycle, "discover_existing_route_table_routes") as discover_routes, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.main(["preup"]), 1)
        register.assert_not_called()
        discover.assert_not_called()
        discover_routes.assert_not_called()
        run.assert_not_called()
        self.assertFalse(lifecycle.PARAMETERS.exists())

    def test_preup_registration_failure_stops_before_env_or_resource_reads(self):
        """RP登録失敗で止まり、azd設定やARM資源読取へ進まない。"""
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                mock.patch.object(lifecycle, "local_preflight"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "azure_preflight") as login, \
                mock.patch.object(lifecycle, "register_providers", side_effect=lifecycle.DeploymentError("Registration pending")) as register, \
                mock.patch.object(lifecycle, "run") as run, \
                mock.patch.object(lifecycle, "ArmTransport") as arm, \
                mock.patch.object(lifecycle, "discover_existing_network_security_groups") as discover, \
                mock.patch.object(lifecycle, "discover_existing_route_table_routes") as routes, \
                mock.patch.object(lifecycle, "discover_existing_apim_hostnames") as domains, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.main(["preup"]), 1)
        login.assert_called_once_with(self.config)
        register.assert_called_once_with(self.config)
        run.assert_not_called()
        arm.assert_not_called()
        discover.assert_not_called()
        routes.assert_not_called()
        domains.assert_not_called()
        self.assertFalse(lifecycle.PARAMETERS.exists())

    def test_final_preprovision_generates_current_discovered_internal_parameter(self):
        expected = [f"nsg-{self.config['resourceStem']}-agw"]
        transport = ScriptedTransport([{"statusCode": 404, "body": None}]
                                     + [self.nsg_response("agw")]
                                     + [{"statusCode": 404, "body": None}] * 12)
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "capacity_preflight"), \
                mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                mock.patch.object(lifecycle, "lock_and_verify_policies"), mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        self.assertEqual(json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["existingNetworkSecurityGroupNames"]["value"], expected)
        self.assertEqual(json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["existingRouteTableRoutes"]["value"],
                         {f"rt-{self.config['resourceStem']}-{suffix}": [] for suffix in ("agw", "apim", "vm", "aca")})


class ExistingRouteTableRoutesTests(LifecycleFixture, unittest.TestCase):
    """再upで動的UDRを消さず、同じ環境の読取済み経路だけ引き継ぐことを確認する。"""

    def route_table_response(self, suffix):
        name = f"rt-{self.config['resourceStem']}-{suffix}"
        resource_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                       f"{self.config['resourceGroupNames']['nw']}/providers/Microsoft.Network/routeTables/{name}")
        return {"statusCode": 200, "body": {
            "id": resource_id, "name": name, "type": "Microsoft.Network/routeTables", "etag": "read-only-table-etag",
            "location": self.config["location"],
            "tags": {"product": self.config["productName"], "environment": self.config["environmentName"],
                     "azd-env-name": self.config["environmentName"]},
            "properties": {"disableBgpRoutePropagation": True, "provisioningState": "Succeeded",
                "subnets": [{"id": "read-only-subnet"}], "resourceGuid": "read-only-table-guid",
                "routes": [{"id": resource_id + "/routes/apim-vip", "name": "apim-vip",
                    "type": "Microsoft.Network/routeTables/routes", "etag": "read-only-route-etag",
                    "properties": {"addressPrefix": "10.40.2.68/32", "nextHopType": "VirtualAppliance",
                        "nextHopIpAddress": "10.40.2.4", "hasBgpOverride": False, "provisioningState": "Succeeded"}},
                    {"name": "direct-default", "properties": {"addressPrefix": "0.0.0.0/0", "nextHopType": "Internet"}}]},
        }}

    def test_first_partial_and_repeated_up_snapshot_only_the_four_exact_tables(self):
        suffixes = ("agw", "apim", "vm", "aca")
        for present in ((), ("agw",), suffixes):
            responses = [self.route_table_response(suffix) if suffix in present else {"statusCode": 404, "body": None}
                         for suffix in suffixes]
            unchanged = copy.deepcopy(responses)
            transport = ScriptedTransport(responses)
            with self.subTest(present=present):
                snapshots = lifecycle.discover_existing_route_table_routes(self.config, transport)
                self.assertEqual(set(snapshots), {f"rt-{self.config['resourceStem']}-{suffix}" for suffix in suffixes})
                self.assertEqual(len(transport.calls), 4)
                self.assertFalse(transport.mutations())
                for index, suffix in enumerate(suffixes):
                    expected = self.route_table_response(suffix)["body"]
                    self.assertEqual(transport.calls[index]["path"], expected["id"] + "?api-version=2025-01-01")
                    actual = snapshots[expected["name"]]
                    if suffix in present:
                        self.assertEqual(actual, [{"name": route["name"], "properties": {
                            key: value for key, value in route["properties"].items() if key != "provisioningState"}}
                            for route in expected["properties"]["routes"]])
                        self.assertFalse(actual[0]["properties"]["hasBgpOverride"])
                    else:
                        self.assertEqual(actual, [])
                self.assertEqual(responses, unchanged)
                self.assertNotIn("read-only", json.dumps(snapshots))
                self.assertNotIn("disableBgpRoutePropagation", json.dumps(snapshots))

    def test_foreign_identity_type_location_and_owner_are_rejected_before_any_write(self):
        changes = [("id", self.route_table_response("agw")["body"]["id"].replace(self.config["subscriptionId"], "foreign")),
                   ("id", self.route_table_response("agw")["body"]["id"].replace(self.config["resourceGroupNames"]["nw"], "rg-other-nw")),
                   ("name", "rt-other-prod-agw"), ("type", "Microsoft.Network/networkSecurityGroups"),
                   ("location", "westus"), ("tags", {}),
                   ("tags", {"product": "other", "environment": "test", "azd-env-name": "test"}),
                   ("tags", {"product": self.config["productName"], "environment": "prod", "azd-env-name": "test"}),
                   ("tags", {"product": self.config["productName"], "environment": "test", "azd-env-name": "prod"})]
        for key, value in changes:
            response = self.route_table_response("agw")
            response["body"][key] = value
            transport = ScriptedTransport([response])
            with self.subTest(field=key), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_route_table_routes(self.config, transport)
            self.assertFalse(transport.mutations())

    def test_unknown_http_status_or_malformed_current_inventory_never_means_empty(self):
        for response in ({"statusCode": 403, "body": {}}, {"statusCode": 429, "body": {}},
                         {"statusCode": 500, "body": {}}, {"statusCode": 200, "body": None},
                         {"statusCode": 200, "body": []}, {"statusCode": 200, "body": "private-response"}):
            transport = ScriptedTransport([response])
            with self.subTest(response=response), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_route_table_routes(self.config, transport)
            self.assertEqual(len(transport.calls), 1)
            self.assertFalse(transport.mutations())

    def test_malformed_duplicate_or_foreign_route_semantics_stop_without_logging_values(self):
        source = self.route_table_response("agw")
        mutations = [
            lambda body: body["properties"].update(routes=None),
            lambda body: body["properties"].pop("routes"),
            lambda body: body["properties"].update(provisioningState="Updating"),
            lambda body: body["properties"]["routes"].append(copy.deepcopy(body["properties"]["routes"][0])),
            lambda body: body["properties"]["routes"][0].update(id="PRIVATE_ROUTE_FIXTURE"),
            lambda body: body["properties"]["routes"][0].update(type="Microsoft.Network/routeTables"),
            lambda body: body["properties"]["routes"][0].update(name="PRIVATE_ROUTE_FIXTURE/slash"),
            lambda body: body["properties"]["routes"][0]["properties"].pop("addressPrefix"),
            lambda body: body["properties"]["routes"][0]["properties"].update(nextHopType=None),
            lambda body: body["properties"]["routes"][0]["properties"].update(nextHopIpAddress=[]),
            lambda body: body["properties"]["routes"][0]["properties"].update(hasBgpOverride="PRIVATE_ROUTE_FIXTURE"),
            lambda body: body["properties"]["routes"][0]["properties"].update(provisioningState="Updating"),
        ]
        for index, mutate in enumerate(mutations):
            response = copy.deepcopy(source)
            mutate(response["body"])
            transport = ScriptedTransport([response])
            with self.subTest(case=index), self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_route_table_routes(self.config, transport)
            self.assertNotIn("PRIVATE_ROUTE_FIXTURE", str(error.exception))
            self.assertFalse(transport.mutations())

    def test_extra_route_response_metadata_is_not_copied_into_deployment_inputs(self):
        response = self.route_table_response("agw")
        properties = response["body"]["properties"]
        properties["responseMetadata"] = "READ_METADATA_FIXTURE"
        properties["routes"][0]["responseMetadata"] = "READ_METADATA_FIXTURE"
        properties["routes"][0]["properties"]["responseMetadata"] = "READ_METADATA_FIXTURE"
        transport = ScriptedTransport([response] + [{"statusCode": 404, "body": None}] * 3)
        observed = lifecycle.discover_existing_route_table_routes(self.config, transport)
        self.assertEqual(observed[response["body"]["name"]][0]["properties"]["addressPrefix"], "10.40.2.68/32")
        self.assertNotIn("READ_METADATA_FIXTURE", json.dumps(observed))
        self.assertFalse(transport.mutations())

    def test_parameter_builder_defaults_empty_and_deep_copies_nested_route_snapshot(self):
        with mock.patch.object(lifecycle, "ArmTransport") as transport:
            self.assertEqual(lifecycle.parameters(self.config, "monitoring")["parameters"]["existingRouteTableRoutes"]["value"], {})
            routes = {"rt-test-egakumi-agw": [{"name": "vip", "properties": {"addressPrefix": "10.40.2.68/32", "nextHopType": "Internet"}}]}
            values = lifecycle.parameters(self.config, "core", existing_route_table_routes=routes)
            routes["rt-test-egakumi-agw"][0]["properties"]["nextHopType"] = "None"
            self.assertEqual(values["parameters"]["existingRouteTableRoutes"]["value"]["rt-test-egakumi-agw"][0]["properties"]["nextHopType"], "Internet")
            transport.assert_not_called()

    def test_direct_core_deployment_reads_full_routes_before_its_only_put(self):
        snapshots = [self.route_table_response(suffix) for suffix in ("agw", "apim", "vm", "aca")]
        transport = ScriptedTransport([{"statusCode": 404, "body": None}] * 4 + snapshots + [{"statusCode": 404, "body": None}] * 2 + [
            {"statusCode": 201, "body": {}},
            {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
        with mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "run", return_value={"resources": []}):
            self.assertEqual(lifecycle.deploy_phase(self.config, "core"), {})
        self.assertEqual([call["method"] for call in transport.calls], ["GET"] * 10 + ["PUT", "GET"])
        routes = transport.mutations()[0]["body"]["properties"]["parameters"]["existingRouteTableRoutes"]["value"]
        self.assertEqual(len(routes), 4)
        for snapshot in snapshots:
            self.assertEqual(routes[snapshot["body"]["name"]][0]["properties"]["addressPrefix"], "10.40.2.68/32")

    def test_final_preprovision_route_discovery_failure_clears_secret_input_but_keeps_state(self):
        transport = ScriptedTransport([{"statusCode": 404, "body": None}] * 5 + [{"statusCode": 403, "body": {}}])
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
                mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                mock.patch.object(lifecycle, "capacity_preflight"), \
                mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                mock.patch.object(lifecycle, "lock_and_verify_policies"), mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                self.assertRaises(lifecycle.DeploymentError):
            lifecycle.preprovision(self.config)
        self.assertFalse(lifecycle.PARAMETERS.exists())
        self.assertTrue(lifecycle.STATE.exists())
        self.assertFalse(transport.mutations())


if __name__ == "__main__":
    unittest.main()
