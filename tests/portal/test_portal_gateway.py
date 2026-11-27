"""App GatewayのHTTPS後段と、WAF更新で使用する安全な書込形式の試験。"""
import copy
import unittest
from tests import REPOSITORY

ROOT = REPOSITORY
from deployment.helpers.portal_gateway import require_appgw_backend_tls_ready, gateway_id, _writable, _etag
from tests.support.app_gateway_fixtures import AppGatewayTransport, PORTAL_GATEWAY_CONFIG, app_gateway_fixture


class PortalGatewayTests(unittest.TestCase):
    def test_https_settings_are_verified_without_write(self):
        transport = AppGatewayTransport([{"statusCode": 200, "body": app_gateway_fixture()}])
        self.assertEqual(require_appgw_backend_tls_ready(PORTAL_GATEWAY_CONFIG, transport), {
            "newGateway": False, "protocol": "Https", "port": 443, "portalProbe": "/api/health"})
        self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_only_an_explicitly_new_gateway_may_be_missing(self):
        self.assertEqual(require_appgw_backend_tls_ready(PORTAL_GATEWAY_CONFIG, AppGatewayTransport([{"statusCode": 404}]), allow_new=True),
                         {"newGateway": True})
        with self.assertRaisesRegex(ValueError, "HTTP 404"):
            require_appgw_backend_tls_ready(PORTAL_GATEWAY_CONFIG, AppGatewayTransport([{"statusCode": 404}]))

    def test_backend_requires_https_trust_root_and_matching_probe_host(self):
        for issue in ("certificate", "protocol", "probe-host"):
            body = app_gateway_fixture()
            properties = body["properties"]
            backend = properties["backendHttpSettingsCollection"][0]["properties"]
            if issue == "certificate":
                backend["trustedRootCertificates"] = []
            elif issue == "protocol":
                backend.update(protocol="Http", port=80)
            else:
                properties["probes"][1]["properties"]["host"] = "different.example.com"
            transport = AppGatewayTransport([{"statusCode": 200, "body": body}])
            with self.subTest(issue=issue), self.assertRaisesRegex(ValueError, "inspected HTTPS backend"):
                require_appgw_backend_tls_ready(PORTAL_GATEWAY_CONFIG, transport, allow_new=True)
            self.assertEqual([item["method"] for item in transport.calls], ["GET"])

    def test_get_reverse_references_are_removed_only_at_documented_collection_positions(self):
        current = app_gateway_fixture()
        target = gateway_id(PORTAL_GATEWAY_CONFIG)
        listener = {"id": target + "/httpListeners/portal"}
        properties = current["properties"]
        properties["frontendIPConfigurations"] = [{"name": "public", "properties": {
            "publicIPAddress": {"id": target + "/frontendIPConfigurations/public"},
            "httpListeners": [listener], "listeners": []}}]
        properties["frontendPorts"] = [{"name": "port-443", "properties": {
            "port": 443, "httpListeners": [listener], "listeners": []}}]
        properties["backendAddressPools"] = [{"name": "apim", "properties": {
            "backendAddresses": [{"ipAddress": "192.0.2.10"}], "routingRules": [],
            "urlPathMaps": [{"id": target + "/urlPathMaps/preserved"}]}}]
        original = copy.deepcopy(current)
        normalized = _writable(current)
        self.assertEqual(current, original)
        clean = normalized["properties"]
        for collection, names in (("frontendIPConfigurations", ("httpListeners", "listeners")),
                                  ("frontendPorts", ("httpListeners", "listeners")),
                                  ("backendAddressPools", ("routingRules",))):
            for name in names:
                self.assertNotIn(name, clean[collection][0]["properties"])
        for collection in ("requestRoutingRules", "httpListeners"):
            self.assertEqual(clean[collection], original["properties"][collection])
        self.assertEqual(clean["frontendPorts"][0]["properties"]["port"], 443)
        self.assertEqual(clean["backendAddressPools"][0]["properties"]["backendAddresses"],
                         original["properties"]["backendAddressPools"][0]["properties"]["backendAddresses"])
        self.assertEqual(clean["backendAddressPools"][0]["properties"]["urlPathMaps"],
                         original["properties"]["backendAddressPools"][0]["properties"]["urlPathMaps"])

    def test_invalid_or_unowned_inverse_references_cannot_be_silently_discarded(self):
        for references in (None, {}, [{"id": "another"}], [{"id": "another", "name": "extra"}],
                           [{"id": gateway_id(PORTAL_GATEWAY_CONFIG) + "/httpListeners/portal"}] * 2):
            current = app_gateway_fixture()
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
