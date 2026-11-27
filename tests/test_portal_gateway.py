"""App GatewayのHTTPS後段と、WAF更新で使用する安全な書込形式の試験。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.portal_gateway import require_appgw_backend_tls_ready, gateway_id, _writable, _etag


CONFIG = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "resourceStem": "test-fixture",
          "resourceGroupNames": {"nw": "rg-test-fixture-nw"}, "services": {"apimInstanceId": "abc123"}}


def fixture(codes=None, path="/", protocol='Https', port=443):
    identity = "/subscriptions/fixture/resourceGroups/id/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cert"
    target = gateway_id(CONFIG)
    return {"id": target, "name": "agw-test-fixture", "type": "Microsoft.Network/applicationGateways", "etag": '"v1"',
            "location": "japaneast", "tags": {"layer": "nw"},
            "identity": {"type": "UserAssigned", "principalId": "readonly", "tenantId": "readonly",
                         "userAssignedIdentities": {identity: {"principalId": "readonly", "clientId": "readonly"}}},
            "properties": {"provisioningState": "Succeeded", "resourceGuid": "readonly", "operationalState": "Running",
                "sku": {"name": "WAF_v2", "tier": "WAF_v2"},
                "sslCertificates": [{"name": "cert", "properties": {"keyVaultSecretId": "https://example.vault.azure.net/secrets/cert", "publicCertData": "readonly"}}],
                "httpListeners": [{"name": "portal", "properties": {"hostName": "portal.example.test"}}],
                "backendHttpSettingsCollection": [{"name": "behttp-portal", "properties": {"port": port, "protocol": protocol, "hostName": "ca-fixture.internal.example.invalid", "probe": {"id": target + "/probes/probe-portal"}}}],
                "requestRoutingRules": [{"name": "portal", "properties": {"backendHttpSettings": {"id": target + "/backendHttpSettingsCollection/behttp-portal"}}}],
                "probes": [
                    {"name": "probe-apim", "properties": {"path": "/status", "match": {"statusCodes": ["200-399"]}}},
                    {"name": "probe-portal", "id": target + "/probes/probe-portal", "etag": '"p1"',
                     "properties": {"path": path, "protocol": protocol, "port": port, "minServers": 0, "provisioningState": "Succeeded",
                                    "backendHttpSettings": [{"id": target + "/backendHttpSettingsCollection/behttp-portal"}],
                                    "match": {"statusCodes": codes or ["200"]}}}
                ]}}


class Transport:
    def __init__(self, bodies):
        self.responses = list(bodies)
        self.calls = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": copy.deepcopy(body), "headers": headers})
        return self.responses.pop(0)


class PortalGatewayTests(unittest.TestCase):
    def tls_fixture(self):
        body = fixture(path="/api/health")
        properties = body["properties"]
        target = gateway_id(CONFIG)
        root_name = "firewall-inspection-root"
        properties["trustedRootCertificates"] = [{"name": root_name}]
        properties["backendHttpSettingsCollection"].append({
            "name": "behttp-apim", "properties": {"protocol": "Https", "port": 443,
                "hostName": "apim.example.com", "probe": {"id": target + "/probes/probe-apim"}}})
        for setting in properties["backendHttpSettingsCollection"]:
            setting["properties"]["trustedRootCertificates"] = [{"id": target + "/trustedRootCertificates/" + root_name}]
        properties["probes"][0]["properties"].update(
            path="/status-0123456789abcdef", protocol="Https", port=443, host="apim.example.com")
        properties["probes"][1]["properties"]["host"] = properties["backendHttpSettingsCollection"][0]["properties"]["hostName"]
        return body

    def test_https_settings_are_verified_without_write(self):
        transport = Transport([{"statusCode": 200, "body": self.tls_fixture()}])
        self.assertEqual(require_appgw_backend_tls_ready(CONFIG, transport), {
            "newGateway": False, "protocol": "Https", "port": 443, "portalProbe": "/api/health"})
        self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_only_an_explicitly_new_gateway_may_be_missing(self):
        self.assertEqual(require_appgw_backend_tls_ready(CONFIG, Transport([{"statusCode": 404}]), allow_new=True),
                         {"newGateway": True})
        with self.assertRaisesRegex(ValueError, "HTTP 404"):
            require_appgw_backend_tls_ready(CONFIG, Transport([{"statusCode": 404}]))

    def test_legacy_or_untrusted_backend_does_not_pass(self):
        for issue in ("certificate", "protocol", "probe-host"):
            body = self.tls_fixture()
            properties = body["properties"]
            backend = properties["backendHttpSettingsCollection"][0]["properties"]
            if issue == "certificate":
                backend["trustedRootCertificates"] = []
            elif issue == "protocol":
                backend.update(protocol="Http", port=80)
            else:
                properties["probes"][1]["properties"]["host"] = "different.example.com"
            transport = Transport([{"statusCode": 200, "body": body}])
            with self.subTest(issue=issue), self.assertRaisesRegex(ValueError, "requires a separate migration"):
                require_appgw_backend_tls_ready(CONFIG, transport, allow_new=True)
            self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_get_reverse_references_are_removed_only_at_documented_collection_positions(self):
        current = fixture()
        target = gateway_id(CONFIG)
        grpc_listener = {"id": target + "/listeners/listener-grpc-tcp"}
        grpc_route = {"id": target + "/routingRules/rule-grpc-tcp"}
        properties = current["properties"]
        properties["frontendIPConfigurations"] = [{"name": "public", "properties": {
            "publicIPAddress": {"id": target + "/frontendIPConfigurations/public"},
            "httpListeners": [{"id": target + "/httpListeners/portal"}], "listeners": [grpc_listener]}}]
        properties["frontendPorts"] = [{"name": "port-grpc", "properties": {
            "port": 8443, "httpListeners": [], "listeners": [grpc_listener]}}]
        properties["backendAddressPools"] = [{"name": "apim", "properties": {
            "backendAddresses": [{"ipAddress": "192.0.2.10"}], "routingRules": [grpc_route],
            "urlPathMaps": [{"id": target + "/urlPathMaps/preserved"}]}}]
        properties["listeners"] = [{"name": "listener-grpc-tcp", "properties": {
            "protocol": "Tcp", "frontendPort": {"id": target + "/frontendPorts/port-grpc"}}}]
        properties["routingRules"] = [{"name": "rule-grpc-tcp", "properties": {"listener": grpc_listener}}]
        original = copy.deepcopy(current)
        normalized = _writable(current)
        self.assertEqual(current, original)
        clean = normalized["properties"]
        for collection, names in (("frontendIPConfigurations", ("httpListeners", "listeners")),
                                  ("frontendPorts", ("httpListeners", "listeners")),
                                  ("backendAddressPools", ("routingRules",))):
            for name in names:
                self.assertNotIn(name, clean[collection][0]["properties"])
        for collection in ("listeners", "routingRules", "requestRoutingRules", "httpListeners"):
            self.assertEqual(clean[collection], original["properties"][collection])
        self.assertEqual(clean["frontendPorts"][0]["properties"]["port"], 8443)
        self.assertEqual(clean["backendAddressPools"][0]["properties"]["backendAddresses"],
                         original["properties"]["backendAddressPools"][0]["properties"]["backendAddresses"])
        self.assertEqual(clean["backendAddressPools"][0]["properties"]["urlPathMaps"],
                         original["properties"]["backendAddressPools"][0]["properties"]["urlPathMaps"])

    def test_invalid_or_unowned_inverse_references_cannot_be_silently_discarded(self):
        for references in (None, {}, [{"id": "another"}], [{"id": "another", "name": "extra"}],
                           [{"id": gateway_id(CONFIG) + "/httpListeners/portal"}] * 2):
            current = fixture()
            current["properties"]["frontendPorts"] = [{"name": "port-443", "properties": {
                "port": 443, "httpListeners": references}}]
            original = copy.deepcopy(current)
            with self.subTest(references=references), self.assertRaisesRegex(ValueError, "inverse reference"):
                _writable(current)
            self.assertEqual(current, original)

    def test_only_one_concrete_etag_can_be_used_for_a_gateway_update(self):
        for etag in ('*', '"*"', 'W/"*"', '""', 'W/""', '"one", "two"',
                     'one,two', '"bad\x7f"', '"bad\n"', '"' + 'a' * 513 + '"', True):
            with self.subTest(etag=etag), self.assertRaisesRegex(ValueError, 'ETag'):
                _etag({'body': {'etag': etag}})
        for etag in ('"owned-version"', 'W/"owned-version"', 'owned-version'):
            self.assertEqual(_etag({'body': {'etag': etag}}), etag)
        self.assertEqual(_etag({'body': {}, 'headers': {'ETag': '"header-version"'}}), '"header-version"')


if __name__ == "__main__":
    unittest.main()
