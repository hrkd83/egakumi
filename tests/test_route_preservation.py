"""再upの親ルート表PUTが既存経路を保ち、BGP設定の変更も反映することを確認する。

実コンパイル後のroutes/BGPプロパティへ初回・途中・全表の読取snapshotを与える。
下記の小さい評価器は、この2式に現れるARM関数だけをローカルで扱う。
Azureへの要求や実ルートの変更は行わない。
"""
from bicep_helpers import compile_bicep

import copy
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]


class RoutePreservationTests(unittest.TestCase):
    """生成する親PUTの具体的なroutes配列とdisableBgpRoutePropagationを調べる。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'main': compile_bicep('infra/main.bicep'),
            'network': compile_bicep('infra/modules/network.bicep'),
        }
        cls.network = cls.templates["network"]
        cls.route_table = cls.network["resources"]["routeTables"]

    def test_every_initial_route_references_a_current_core_route_table(self):
        """区画を除去した後も、実ルートが存在しない表番号を参照しない。"""
        tables = self.network['variables']['routeTableDefinitions']
        self.assertEqual([table['key'] for table in tables], ['agw', 'apim', 'management'])
        routes = self.network['variables']['initialRoutes']
        self.assertTrue(routes)
        for route in routes:
            with self.subTest(route=route['name']):
                self.assertIs(type(route['tableIndex']), int)
                self.assertGreaterEqual(route['tableIndex'], 0)
                self.assertLess(route['tableIndex'], len(tables))
        self.assertEqual({tables[route['tableIndex']]['key'] for route in routes},
                         {'apim', 'management'})

    def evaluate_property(self, expression, parameters, index):
        """信頼するコンパイラーが生成した2式だけを、既知の関数と値で評価する。"""
        def try_get(value, *keys):
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    return None
                value = value[key]
            return value
        functions = {
            "parameters": lambda name: parameters[name],
            "variables": lambda name: self.network["variables"][name],
            "copyIndex": lambda: index,
            "tryGet": try_get,
            "coalesce": lambda *values: next(value for value in values if value is not None),
            "format": lambda pattern, *values: pattern.format(*values),
            "createArray": lambda: [], "true": lambda: True,
        }
        python_expression = expression[1:-1].replace(".suffix", "['suffix']").replace(".key", "['key']")
        return eval(python_expression, {"__builtins__": {}}, functions)

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
             "rt-test-egakumi-vm": [defaults]},
        ]
        for snapshot in snapshots:
            before = copy.deepcopy(snapshot)
            for propagation in (True, False):
                parameters = {"resourcePrefix": "test-egakumi", "existingRouteTableRoutes": snapshot,
                              "config": {"bgpRoutePropagation": {
                                  "agw": propagation, "apim": propagation, "management": propagation}}}
                for index, table in enumerate(self.network["variables"]["routeTableDefinitions"]):
                    name = f"rt-test-egakumi-{table['suffix']}"
                    with self.subTest(snapshot=list(snapshot), propagation=propagation, table=name):
                        properties = {key: self.evaluate_property(value, parameters, index)
                                      for key, value in self.route_table["properties"].items()}
                        self.assertEqual(properties["routes"], snapshot.get(name, []))
                        self.assertIs(properties["disableBgpRoutePropagation"], not propagation)
            self.assertEqual(snapshot, before)


if __name__ == "__main__":
    unittest.main()
