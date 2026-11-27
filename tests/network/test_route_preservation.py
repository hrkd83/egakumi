"""再upの親ルート表PUTが既存経路を保ち、BGP設定の変更も反映することを確認する。

実コンパイル後のroutes/BGPプロパティへ初回・途中・全表の読取snapshotを与える。
Azureへの要求や実ルートの変更は行わない。
"""
from tests.support.bicep_helpers import compile_bicep
from tests.network.test_parameter_ip_ownership import NetworkArmExpression

import copy
import unittest
from tests import REPOSITORY


CORE = REPOSITORY


class RoutePreservationTests(unittest.TestCase):
    """生成する親PUTの具体的なroutes配列とdisableBgpRoutePropagationを調べる。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'main': compile_bicep('infra/main.bicep'),
            'network': compile_bicep('infra/modules/network/network.bicep'),
        }
        cls.network = cls.templates["network"]
        cls.route_table = cls.network["resources"]["routeTables"]

    def test_every_initial_route_references_a_current_core_route_table(self):
        """Brokerの既定経路を含め、実ルートの配備先が現在のルート表を参照します。"""
        tables = self.network['variables']['routeTableDefinitions']
        self.assertEqual([table['key'] for table in tables], ['agw', 'apim', 'management', 'broker'])
        routes = self.network['variables']['initialRoutes']
        self.assertTrue(routes)
        for route in routes:
            with self.subTest(route=route['name']):
                self.assertIs(type(route['tableIndex']), int)
                self.assertGreaterEqual(route['tableIndex'], 0)
                self.assertLess(route['tableIndex'], len(tables))
        self.assertEqual({tables[route['tableIndex']]['key'] for route in routes},
                         {'apim', 'management', 'broker'})
        broker, = [route for route in routes if tables[route['tableIndex']]['key'] == 'broker']
        self.assertEqual(broker['name'], 'default-egress')
        self.assertEqual(broker['prefix'], '0.0.0.0/0')
        self.assertEqual(broker['nextHopType'], 'VirtualAppliance')

    def evaluate_property(self, expression, parameters, index):
        """既知のARM関数を使って、生成されたプロパティを評価します。"""
        if not isinstance(expression, str) or not expression.startswith('['):
            return expression
        return NetworkArmExpression(expression, self.network).evaluate(parameters, index)

    def test_internal_snapshot_defaults_empty_and_is_wired_without_public_config(self):
        for template in self.templates.values():
            self.assertEqual(template["parameters"]["existingRouteTableRoutes"]["defaultValue"], {})
        main_resources = self.templates["main"]["resources"]
        network = next(resource for resource in main_resources
                       if "existingRouteTableRoutes" in resource.get("properties", {}).get("parameters", {}))
        self.assertEqual(network["properties"]["parameters"]["existingRouteTableRoutes"],
                         {"value": "[parameters('existingRouteTableRoutes')]"})
        self.assertIn("parameters('existingRouteTableRoutes')", self.route_table["properties"]["routes"])
        self.assertEqual(self.route_table["tags"], "[parameters('tags')]")

    def test_optional_audit_subnet_gets_only_its_own_firewall_default_route(self):
        resolver = NetworkArmExpression("[createObject()]", self.network)
        parameters = {"auditEvidenceEnabled": True}
        tables = resolver.resolve(self.network["variables"]["activeRouteTableDefinitions"], parameters)
        routes = resolver.resolve(self.network["variables"]["activeInitialRoutes"], parameters)
        audit = [route for route in routes if tables[route["tableIndex"]]["key"] == "audit"]
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]["prefix"], audit[0]["nextHopType"]), ("0.0.0.0/0", "VirtualAppliance"))

    def test_initial_partial_and_all_route_snapshots_survive_either_bgp_setting(self):
        dynamic = {"name": "apim-through-firewall", "properties": {
            "addressPrefix": "10.40.2.68/32", "nextHopType": "VirtualAppliance",
            "nextHopIpAddress": "10.40.2.4", "hasBgpOverride": False}}
        defaults = {"name": "default-through-firewall", "properties": {
            "addressPrefix": "0.0.0.0/0", "nextHopType": "VirtualAppliance", "nextHopIpAddress": "10.40.2.4"}}
        internet = {"name": "apim-control-plane", "properties": {
            "addressPrefix": "ApiManagement", "nextHopType": "Internet"}}
        snapshots = [
            {},
            {"rt-test-egakumi-agw": [dynamic]},
            {"rt-test-egakumi-agw": [dynamic], "rt-test-egakumi-apim": [defaults, internet],
             "rt-test-egakumi-vm": [defaults], "rt-test-egakumi-aca": [defaults],
             "rt-test-egakumi-broker": [defaults]},
        ]
        for snapshot in snapshots:
            before = copy.deepcopy(snapshot)
            for propagation in (True, False):
                parameters = {"resourcePrefix": "test-egakumi", "existingRouteTableRoutes": snapshot,
                              "config": {"bgpRoutePropagation": {
                                  "agw": propagation, "apim": propagation, "management": propagation}}}
                tables = self.network['variables']['routeTableDefinitions']
                portal_table = self.network['resources']['portalRouteTable']
                for index, table in enumerate([*tables, {'key': 'aca', 'suffix': 'aca'}]):
                    name = f"rt-test-egakumi-{table['suffix']}"
                    with self.subTest(snapshot=list(snapshot), propagation=propagation, table=name):
                        declared = portal_table if table['key'] == 'aca' else self.route_table
                        properties = {key: self.evaluate_property(value, parameters, index)
                                      for key, value in declared['properties'].items()}
                        self.assertEqual(properties["routes"], snapshot.get(name, []))
                        self.assertIs(properties['disableBgpRoutePropagation'],
                                      table['key'] in ('aca', 'broker') or not propagation)
            self.assertEqual(snapshot, before)


if __name__ == "__main__":
    unittest.main()
