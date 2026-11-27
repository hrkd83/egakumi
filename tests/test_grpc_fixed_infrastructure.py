"""gRPC専用入口撤去後のHTTP/L7固定ARMを合成入力で検証する。Azureには接続しない。"""
import copy
import json
import unittest

from arm_helpers import DiscoveryExpression
from bicep_helpers import compile_bicep
from test_parameter_ip_ownership import NetworkArmExpression
from test_ssml_waf import GatewayExpression


class RetiredGrpcInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            "main": compile_bicep("infra/main.bicep"),
            "gateway": compile_bicep("infra/modules/ingress/application-gateway.bicep"),
            "network": compile_bicep("infra/modules/network/network-bindings.bicep"),
            "initial_network": compile_bicep("infra/modules/network/network.bicep"),
            "services": compile_bicep("infra/modules/services/services.bicep"),
        }

    def main_value(self, value, config):
        return DiscoveryExpression("[true()]", self.templates["main"]["variables"]).resolve(value, {"configuration": config})

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
                          firewallRootCaData="SYNTHETIC_PUBLIC_ROOT_CA",
                          l7IngressSecret="SYNTHETIC_PRIVATE_INPUT")
        parameters.update(changes)
        gateway = next(item for item in self.resources("gateway") if item["type"] == "Microsoft.Network/applicationGateways")
        return GatewayExpression("[true()]", template["variables"]).resolve(gateway["properties"], parameters)

    def test_no_module_reintroduces_grpc_switches_or_ports(self):
        for module, template in self.templates.items():
            with self.subTest(module=module):
                self.assertFalse(any("grpc" in name.lower() for name in template["parameters"]))
                self.assertNotIn("grpcConfig", template.get("variables", {}))
                for resource in self.resources(module):
                    properties = resource.get("properties", {})
                    if isinstance(properties, dict):
                        self.assertFalse(any("grpc" in name.lower() for name in properties.get("parameters", {})))

    def test_application_gateway_has_https_l7_backends(self):
        properties = self.gateway_properties()
        self.assertEqual({item["properties"]["port"] for item in properties["frontendPorts"]}, {80, 443})
        self.assertTrue(all(item["properties"]["protocol"] in ("Http", "Https") for item in properties["httpListeners"]))
        for collection in ("listeners", "backendSettingsCollection", "routingRules"):
            self.assertEqual(properties[collection], [])
        self.assertIs(properties["enableHttp2"], True)
        backends = {item["name"]: item["properties"] for item in properties["backendHttpSettingsCollection"]}
        for name, host in (("behttp-apim", "apim.example.com"), ("behttp-portal", "backend.example.com")):
            self.assertEqual((backends[name]["protocol"], backends[name]["port"]), ("Https", 443))
            self.assertEqual(backends[name]["hostName"], host)
            root, = backends[name]["trustedRootCertificates"]
            self.assertTrue(root["id"].endswith("/trustedRootCertificates/firewall-inspection-root"))

    def test_all_apim_gateway_routes_keep_trusted_rewrite_with_each_waf_mode(self):
        self.assertNotIn("existingWafPolicyId", self.templates["gateway"]["parameters"])
        for changes in ({}, {"skuName": "Standard_v2"}):
            with self.subTest(changes=changes):
                properties = self.gateway_properties(**changes)
                rewrite, = properties["rewriteRuleSets"]
                header, = rewrite["properties"]["rewriteRules"][0]["actionSet"]["requestHeaderConfigurations"]
                self.assertEqual(header, {"headerName": "X-Core-L7-Ingress", "headerValue": "SYNTHETIC_PRIVATE_INPUT"})
                rule = next(item["properties"] for item in properties["requestRoutingRules"] if item["name"] == "rule-https-to-apim")
                if rule["ruleType"] == "Basic":
                    self.assertTrue(rule["rewriteRuleSet"]["id"].endswith("/trusted-l7-ingress"))
                else:
                    pathmap = next(item["properties"] for item in properties["urlPathMaps"] if item["name"] == "pathmap-protocols")
                    self.assertTrue(pathmap["defaultRewriteRuleSet"]["id"].endswith("/trusted-l7-ingress"))
                    for path in pathmap["pathRules"]:
                        self.assertEqual(path["properties"]["rewriteRuleSet"], pathmap["defaultRewriteRuleSet"])

    def test_nsg_bindings_have_no_grpc_rules_but_keep_https_maintenance(self):
        template = self.templates["network"]
        parameters = self.defaults("network")
        parameters.update(config={}, subnetPrefixes={"agw": "192.0.2.0/26", "apim": "198.51.100.0/26", "management": "203.0.113.0/26", "pep": "192.0.2.128/26", "aca": "198.51.100.128/26", "broker": "203.0.113.128/26"},
                          foundryPrivateIps=["192.0.2.132"], keyVaultPrivateIps=["192.0.2.133"],
                          publicTlsVaultPrivateIps=["192.0.2.134"],
                          managementPrivateIp="203.0.113.4", firewallPublicIpAddress="192.0.2.250", appGatewayPublicIpAddress="192.0.2.251", apimPrivateIp="198.51.100.4")
        resolver = NetworkArmExpression("[true()]", template)
        groups = resolver.resolve("[variables('rulesByNsg')]", parameters)
        rules = {item["name"]: item["properties"] for group in groups for item in group}
        self.assertFalse(any("grpc" in name for name in rules))
        for index, name in ((1, "allow-mgmt-vm-https-ingress"), (3, "allow-apim-https-egress")):
            rule = next(item["properties"] for item in groups[index] if item["name"] == name)
            self.assertEqual(rule["access"], "Allow")
            self.assertEqual(rule["destinationPortRange"], "443")
        self.assertNotIn('"8443"', json.dumps(groups))

    def test_apim_server_http2_is_string_false_and_l7_is_always_enabled(self):
        template = self.templates["services"]
        apim = next(item for item in self.resources("services") if item["type"] == "Microsoft.ApiManagement/service")
        parameters = self.defaults("services")
        parameters.update(names={"apim": "apim-test-fixture"},
                          existingApimCustomProperties={"apim-test-fixture": {
                              "KeepSetting": "true", "Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2": "true"}})
        custom = GatewayExpression("[true()]", template.get("variables", {})).resolve(apim["properties"]["customProperties"], parameters)
        self.assertEqual(custom["Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2"], "false")
        self.assertEqual(custom["KeepSetting"], "true")
        for override in (None, {"enabled": True, "publicPort": 8443, "backendPort": 443}):
            config = {"gateway": {"publicHostName": "api.example.com"}, "apiDefinitions": []}
            if override is not None:
                config["gateway"]["grpc"] = override
            with self.subTest(direct_arm_override=override):
                self.assertIs(self.main_value(self.templates["main"]["variables"]["useIngressSecrets"], config), True)

    def test_owner_tickets_still_follow_realtime_and_document_contracts(self):
        document = {"authorization": {"operationConstraints": {"status": {"kind": "documentAnalysisStatus"}}}}
        realtime = {"authorization": {"operationConstraints": {"create": {"kind": "realtimeCreate"}}}}
        for definitions, metadata, expected_realtime, expected_owner in (([], [], False, False), ([], [document], False, True), ([realtime], [], True, True)):
            config = {"gateway": {"publicHostName": "api.example.com"}, "apiDefinitions": definitions, "infrastructureApiDefinitions": metadata}
            variables = self.templates["main"]["variables"]
            with self.subTest(realtime=expected_realtime, owner=expected_owner):
                self.assertIs(self.main_value(variables["realtimeEnabled"], config), expected_realtime)
                self.assertIs(self.main_value(variables["ownerTicketEnabled"], config), expected_owner)
                self.assertIs(self.main_value(variables["useIngressSecrets"], config), True)


if __name__ == "__main__":
    unittest.main()
