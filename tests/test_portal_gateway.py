"""Portalの認証不要ヘルスAPIへの切替で、他のGateway設定を変えないことを検証する。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.portal_gateway import configure_portal_probe, gateway_id, _writable


CONFIG = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "resourceStem": "test-fixture",
          "resourceGroupNames": {"nw": "rg-test-fixture-nw"}, "services": {"apimInstanceId": "abc123"}}


def fixture(codes=None, path="/"):
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
                "backendHttpSettingsCollection": [{"name": "behttp-portal", "properties": {"port": 443, "protocol": "Https", "probe": {"id": target + "/probes/probe-portal"}}}],
                "requestRoutingRules": [{"name": "portal", "properties": {"backendHttpSettings": {"id": target + "/backendHttpSettingsCollection/behttp-portal"}}}],
                "probes": [
                    {"name": "probe-apim", "properties": {"path": "/status", "match": {"statusCodes": ["200-399"]}}},
                    {"name": "probe-portal", "id": target + "/probes/probe-portal", "etag": '"p1"',
                     "properties": {"path": path, "minServers": 0, "provisioningState": "Succeeded",
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
    def test_only_probe_path_and_status_range_change_and_readonly_metadata_is_removed(self):
        current = fixture()
        original = copy.deepcopy(current)
        desired = fixture(["200"], "/api/health")
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 202, "body": None},
                               {"statusCode": 200, "body": desired}])
        result = configure_portal_probe(CONFIG, transport, sleep=lambda _: None)
        self.assertTrue(result["updated"])
        self.assertEqual(current, original)
        request = transport.calls[1]
        expected = _writable(original)
        expected["properties"]["probes"][1]["properties"]["path"] = "/api/health"
        expected["properties"]["probes"][1]["properties"]["match"]["statusCodes"] = ["200"]
        self.assertEqual(request["body"], expected)
        self.assertEqual(request["headers"], {"If-Match": '"v1"'})
        self.assertEqual(request["body"]["properties"]["requestRoutingRules"], original["properties"]["requestRoutingRules"])
        self.assertNotIn("backendHttpSettings", request["body"]["properties"]["probes"][1]["properties"])
        self.assertNotIn("publicCertData", request["body"]["properties"]["sslCertificates"][0]["properties"])

    def test_already_configured_has_no_write(self):
        transport = Transport([{"statusCode": 200, "body": fixture(["200"], "/api/health")}])
        self.assertFalse(configure_portal_probe(CONFIG, transport)["updated"])
        self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_root_redirect_probe_switches_to_health_api(self):
        transport = Transport([{"statusCode": 200, "body": fixture(["200-399"])},
                               {"statusCode": 202, "body": None},
                               {"statusCode": 200, "body": fixture(["200"], "/api/health")}])
        receipt = configure_portal_probe(CONFIG, transport, sleep=lambda _: None)
        self.assertEqual(receipt["path"], "/api/health")
        self.assertEqual(receipt["statusCodes"], ["200"])
        self.assertTrue(receipt["updated"])

    def test_unexpected_target_or_binding_stops_before_write(self):
        for issue in ("id", "name", "type", "path", "binding", "codes", "state", "etag"):
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
            else:
                body.pop("etag")
            transport = Transport([{"statusCode": 200, "body": body}])
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                configure_portal_probe(CONFIG, transport)
            self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_failed_or_stale_update_is_not_reported_as_success(self):
        current, failed = fixture(), fixture(["200"], "/api/health")
        failed["properties"]["provisioningState"] = "Failed"
        transport = Transport([{"statusCode": 200, "body": current}, {"statusCode": 202, "body": {}},
                               {"statusCode": 200, "body": failed}])
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            configure_portal_probe(CONFIG, transport)


if __name__ == "__main__":
    unittest.main()
