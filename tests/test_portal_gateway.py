"""Portalの認証不要ヘルスAPIへの切替で、他のGateway設定を変えないことを検証する。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.portal_gateway import configure_portal_probe, gateway_id, _writable, _etag


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
    def test_backend_and_probe_http_cutover_preserves_unrelated_settings_and_removes_readonly_metadata(self):
        current = fixture()
        original = copy.deepcopy(current)
        desired = fixture(["200"], "/api/health", 'Http', 80)
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 202, "body": None},
                               {"statusCode": 200, "body": desired}])
        records = []
        result = configure_portal_probe(CONFIG, transport, sleep=lambda _: None, record=lambda e, v: records.append((e, v)))
        self.assertTrue(result["updated"])
        self.assertEqual(current, original)
        request = transport.calls[1]
        expected = _writable(original)
        expected["properties"]["probes"][1]["properties"]["path"] = "/api/health"
        expected["properties"]["probes"][1]["properties"]["match"]["statusCodes"] = ["200"]
        expected['properties']['probes'][1]['properties'].update(protocol='Http', port=80)
        expected['properties']['backendHttpSettingsCollection'][0]['properties'].update(protocol='Http', port=80)
        self.assertEqual(request["body"], expected)
        self.assertEqual(request["headers"], {"If-Match": '"v1"'})
        self.assertEqual(request["body"]["properties"]["requestRoutingRules"], original["properties"]["requestRoutingRules"])
        self.assertNotIn("backendHttpSettings", request["body"]["properties"]["probes"][1]["properties"])
        self.assertNotIn("publicCertData", request["body"]["properties"]["sslCertificates"][0]["properties"])
        self.assertEqual(records[0][0], 'gateway-before')
        self.assertEqual(records[0][1]['configuration'], _writable(original))
        self.assertEqual(records[1][0], 'gateway-after')

    def test_already_configured_has_no_write(self):
        transport = Transport([{"statusCode": 200, "body": fixture(["200"], "/api/health", 'Http', 80)}])
        self.assertFalse(configure_portal_probe(CONFIG, transport)["updated"])
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

    def test_root_redirect_probe_switches_to_health_api(self):
        transport = Transport([{"statusCode": 200, "body": fixture(["200-399"])},
                               {"statusCode": 202, "body": None},
                               {"statusCode": 200, "body": fixture(["200"], "/api/health", 'Http', 80)}])
        receipt = configure_portal_probe(CONFIG, transport, sleep=lambda _: None, record=lambda *_: None)
        self.assertEqual(receipt["path"], "/api/health")
        self.assertEqual(receipt["statusCodes"], ["200"])
        self.assertTrue(receipt["updated"])

    def test_unexpected_target_or_binding_stops_before_write(self):
        for issue in ("id", "name", "type", "path", "binding", "codes", "state", "etag", "backend-port", "probe-protocol"):
            body = fixture()
            if issue in ("id", "name", "type"):
                body[issue] = "another"
            elif issue == "path":
                body["properties"]["probes"][1]["properties"]["path"] = "/other"
            elif issue == "binding":
                body["properties"]["backendHttpSettingsCollection"][0]["properties"]["probe"]["id"] += "-other"
            elif issue == "codes":
                body["properties"]["probes"][1]["properties"]["match"]["statusCodes"] = ["200-599"]
            elif issue == "state":
                body["properties"]["provisioningState"] = "Updating"
            elif issue == 'backend-port':
                body['properties']['backendHttpSettingsCollection'][0]['properties']['port'] = 444
            elif issue == 'probe-protocol':
                body['properties']['probes'][1]['properties']['protocol'] = 'Http'
            else:
                body.pop("etag")
            transport = Transport([{"statusCode": 200, "body": body}])
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                configure_portal_probe(CONFIG, transport)
            self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_failed_or_stale_update_is_not_reported_as_success(self):
        current, failed = fixture(), fixture(["200"], "/api/health", 'Http', 80)
        failed["properties"]["provisioningState"] = "Failed"
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 202, "body": {}},
                               {"statusCode": 200, "body": failed}])
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            configure_portal_probe(CONFIG, transport, record=lambda *_: None)

    def test_existing_health_path_still_requires_the_https_backend_to_migrate(self):
        current = fixture(['200'], '/api/health')
        desired = fixture(['200'], '/api/health', 'Http', 80)
        transport = Transport([{'statusCode': 200, 'body': current}, {'statusCode': 202, 'body': None},
                               {'statusCode': 200, 'body': desired}])
        result = configure_portal_probe(CONFIG, transport, sleep=lambda _: None, record=lambda *_: None)
        self.assertTrue(result['updated'])
        self.assertEqual(result['port'], 80)
        self.assertEqual(transport.calls[1]['body']['properties']['backendHttpSettingsCollection'][0]['properties']['hostName'],
                         current['properties']['backendHttpSettingsCollection'][0]['properties']['hostName'])

    def test_missing_prechange_record_stops_before_gateway_write(self):
        for recorder in (None, False, True, {}, [], 'recorder'):
            transport = Transport([{'statusCode': 200, 'body': fixture()}])
            with self.subTest(recorder=recorder), self.assertRaisesRegex(ValueError, 'protected pre-change record'):
                configure_portal_probe(CONFIG, transport, record=recorder)
            self.assertEqual([call['method'] for call in transport.calls], ['GET'])

    def test_only_one_concrete_etag_can_be_used_for_a_gateway_update(self):
        for etag in ('*', '"*"', 'W/"*"', '""', 'W/""', '"one", "two"',
                     'one,two', '"bad\x7f"', '"bad\n"', '"' + 'a' * 513 + '"', True):
            current = fixture()
            current['etag'] = etag
            transport = Transport([{'statusCode': 200, 'body': current}])
            with self.subTest(etag=etag), self.assertRaisesRegex(ValueError, 'ETag'):
                configure_portal_probe(CONFIG, transport, record=lambda *_: None)
            self.assertEqual([call['method'] for call in transport.calls], ['GET'])
        for etag in ('"owned-version"', 'W/"owned-version"', 'owned-version'):
            self.assertEqual(_etag({'body': {'etag': etag}}), etag)
        self.assertEqual(_etag({'body': {}, 'headers': {'ETag': '"header-version"'}}), '"header-version"')


if __name__ == "__main__":
    unittest.main()
