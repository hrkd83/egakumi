"""固定 gRPC 入口の ARM を合成入力で検証する。Azure には接続しない。"""
import copy
import unittest

from arm_helpers import DiscoveryExpression
from bicep_helpers import compile_bicep
from test_parameter_ip_ownership import NetworkArmExpression
from test_ssml_waf import GatewayExpression


class FixedGrpcInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            "main": compile_bicep("infra/main.bicep"),
            "gateway": compile_bicep("infra/modules/application-gateway.bicep"),
            "network": compile_bicep("infra/modules/network-bindings.bicep"),
            "services": compile_bicep("infra/modules/services.bicep"),
        }

    def main_value(self, value, config):
        template = self.templates["main"]
        return DiscoveryExpression("[true()]", template["variables"]).resolve(value, {"configuration": config})

    def defaults(self, module):
        return {key: copy.deepcopy(value.get("defaultValue", "fixture"))
                for key, value in self.templates[module]["parameters"].items()}

    def resources(self, module):
        resources = self.templates[module]["resources"]
        return list(resources.values()) if isinstance(resources, dict) else resources

    def gateway_properties(self, **changes):
        template = self.templates["gateway"]
        parameters = self.defaults("gateway")
        parameters.update(resourceStem="test-fixture", wafPolicyIdentifier="abc123", location="japaneast",
                          publicHostName="api.example.com", portalHostName="portal.example.com",
                          apimPrivateIp="192.0.2.11", portalBackendIp="192.0.2.12",
                          apimHostName="apim.example.com", portalBackendHost="backend.example.com",
                          l7IngressSecret="SYNTHETIC_PRIVATE_INPUT")
        parameters.update(changes)
        gateway = next(item for item in self.resources("gateway")
                       if item["type"] == "Microsoft.Network/applicationGateways")
        return GatewayExpression("[true()]", template["variables"]).resolve(gateway["properties"], parameters)

    def test_child_modules_restrict_grpc_to_the_permanent_fixed_contract(self):
        for module, boolean in (("gateway", "grpcEnabled"), ("network", "grpcEnabled"), ("services", "enableGrpc")):
            fixed = {boolean: True}
            if module != "services":
                fixed.update(grpcPublicPort=8443, grpcBackendPort=443)
            for name, value in fixed.items():
                with self.subTest(module=module, parameter=name):
                    parameter = self.templates[module]["parameters"][name]
                    self.assertEqual(parameter["defaultValue"], value)
                    self.assertEqual(parameter["allowedValues"], [value])

    def test_main_cannot_override_fixed_values_even_with_direct_arm_input(self):
        template = self.templates["main"]
        modules = [item["properties"]["parameters"] for item in template["resources"]
                   if "grpcEnabled" in item.get("properties", {}).get("parameters", {})
                   or "enableGrpc" in item.get("properties", {}).get("parameters", {})]
        self.assertEqual(len(modules), 3)
        # 公開入力ではこれらのキーを拒否する。ARMへ直接渡しても固定値は変わらない。
        for override in (None, {"enabled": False, "publicPort": 9443, "backendPort": 80,
                           "publicHostName": "old.example.com"}):
            config = {"gateway": {"publicHostName": "api.example.com"}, "apiDefinitions": []}
            if override is not None:
                config["gateway"]["grpc"] = override
            with self.subTest(override=override):
                self.assertEqual(self.main_value(template["variables"]["grpcConfig"], config), {
                    "enabled": True, "publicPort": 8443, "backendPort": 443, "publicHostName": "api.example.com"})
                for parameters in modules:
                    boolean = "enableGrpc" if "enableGrpc" in parameters else "grpcEnabled"
                    self.assertIs(self.main_value(parameters[boolean]["value"], config), True)
                    for name, expected in (("grpcPublicPort", 8443), ("grpcBackendPort", 443)):
                        if name in parameters:
                            self.assertEqual(self.main_value(parameters[name]["value"], config), expected)

    def test_tcp_listener_preserves_fixed_ports_and_existing_https_backend(self):
        properties = self.gateway_properties()
        ports = {item["name"]: item["properties"]["port"] for item in properties["frontendPorts"]}
        self.assertEqual(ports, {"port-80": 80, "port-443": 443, "port-grpc": 8443})
        self.assertEqual(properties["listeners"][0]["properties"]["protocol"], "Tcp")
        backend = properties["backendSettingsCollection"][0]["properties"]
        self.assertEqual((backend["protocol"], backend["port"]), ("Tcp", 443))
        self.assertEqual(properties["routingRules"][0]["properties"]["priority"], 300)
        self.assertIs(properties["enableHttp2"], True)
        self.assertEqual(properties["backendHttpSettingsCollection"][0]["properties"]["port"], 80)
        self.assertTrue(any(item["name"] == "listener-https" for item in properties["httpListeners"]))

    def test_all_gateway_routes_keep_trusted_rewrite_with_each_waf_mode(self):
        for changes in ({}, {"existingWafPolicyId": "/fixture/existing-waf"}, {"skuName": "Standard_v2"}):
            with self.subTest(changes=changes):
                properties = self.gateway_properties(**changes)
                rewrite, = properties["rewriteRuleSets"]
                header, = rewrite["properties"]["rewriteRules"][0]["actionSet"]["requestHeaderConfigurations"]
                self.assertEqual(header, {"headerName": "X-Core-L7-Ingress", "headerValue": "SYNTHETIC_PRIVATE_INPUT"})
                rule = next(item["properties"] for item in properties["requestRoutingRules"]
                            if item["name"] == "rule-https-to-apim")
                if rule["ruleType"] == "Basic":
                    self.assertTrue(rule["rewriteRuleSet"]["id"].endswith("/trusted-l7-ingress"))
                else:
                    pathmap = next(item["properties"] for item in properties["urlPathMaps"]
                                   if item["name"] == "pathmap-protocols")
                    self.assertTrue(pathmap["defaultRewriteRuleSet"]["id"].endswith("/trusted-l7-ingress"))
                    for path in pathmap["pathRules"]:
                        self.assertEqual(path["properties"]["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])

    def test_valid_client_sources_keep_public_and_internal_grpc_ingress_allowed(self):
        template = self.templates["network"]
        for clients in (None, ["0.0.0.0/0"], ["203.0.113.10/32"]):
            parameters = self.defaults("network")
            parameters.update(config={} if clients is None else {"clientAddressPrefixes": clients},
                              subnetPrefixes={"agw": "192.0.2.0/26", "apim": "198.51.100.0/26", "management": "203.0.113.0/26"},
                              foundryPrivateIps=["192.0.2.132"], keyVaultPrivateIps=["192.0.2.133"],
                              managementPrivateIp="203.0.113.4", firewallPublicIpAddress="192.0.2.250")
            resolver = NetworkArmExpression("[true()]", template)
            groups = resolver.resolve("[variables('rulesByNsg')]", parameters)
            rules = {item["name"]: item["properties"] for group in groups for item in group}
            with self.subTest(clients=clients):
                self.assertEqual((rules["allow-client-grpc"]["priority"], rules["allow-client-grpc"]["access"]), (125, "Allow"))
                self.assertEqual(rules["allow-client-grpc"]["sourceAddressPrefix"],
                                 (clients or ["0.0.0.0/0"])[0])
                self.assertEqual(rules["allow-client-grpc"]["destinationPortRange"], "8443")
                self.assertEqual(rules["allow-apim-grpc"]["access"], "Allow")
                for name in ("allow-apim-grpc", "allow-agw-grpc", "allow-management-grpc", "allow-apim-grpc-comparison"):
                    self.assertEqual(rules[name]["access"], "Allow")
                    self.assertEqual(rules[name]["destinationPortRange"], "443")
                    self.assertEqual(rules[name]["destinationAddressPrefix"], "198.51.100.0/26")
                group = next(item for item in self.resources("network")
                             if item["type"] == "Microsoft.Network/firewallPolicies/ruleCollectionGroups")
                firewall = resolver.resolve(group["properties"], parameters)
                self.assertEqual(firewall["ruleCollections"][0]["rules"][0]["destinationPorts"], ["80", "443"])

    def test_http2_is_enabled_while_owner_tickets_follow_realtime_and_documents(self):
        template = self.templates["services"]
        apim = next(item for item in self.resources("services") if item["type"] == "Microsoft.ApiManagement/service")
        custom = GatewayExpression("[true()]", template.get("variables", {})).resolve(
            apim["properties"]["customProperties"], self.defaults("services"))
        self.assertEqual(custom["Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2"], "true")
        document = {"authorization": {"operationConstraints": {"status": {"kind": "documentAnalysisStatus"}}}}
        realtime = {"authorization": {"operationConstraints": {"create": {"kind": "realtimeCreate"}}}}
        for definitions, metadata, expected_realtime, expected_owner in (
            ([], [], False, False), ([], [document], False, True), ([realtime], [], True, True),
        ):
            config = {"gateway": {"publicHostName": "api.example.com"}, "apiDefinitions": definitions,
                      "infrastructureApiDefinitions": metadata}
            with self.subTest(realtime=expected_realtime, owner=expected_owner):
                variables = self.templates["main"]["variables"]
                self.assertIs(self.main_value(variables["realtimeEnabled"], config), expected_realtime)
                self.assertIs(self.main_value(variables["ownerTicketEnabled"], config), expected_owner)
                self.assertIs(self.main_value(variables["useIngressSecrets"], config), True)


if __name__ == "__main__":
    unittest.main()
