"""Shared synthetic App Gateway state and a queued ARM response recorder."""
import copy

from deployment.helpers.portal_gateway import gateway_id


PORTAL_GATEWAY_CONFIG = {
    "subscriptionId": "11111111-1111-4111-8111-111111111111",
    "resourceStem": "test-fixture",
    "resourceGroupNames": {"nw": "rg-test-fixture-nw"},
    "services": {"apimInstanceId": "abc123"},
}


def app_gateway_fixture():
    identity = "/subscriptions/fixture/resourceGroups/id/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cert"
    target = gateway_id(PORTAL_GATEWAY_CONFIG)
    root_name = "firewall-inspection-root"
    root = {"id": target + "/trustedRootCertificates/" + root_name}
    portal_host = "ca-fixture.internal.example.invalid"
    apim_host = "apim.example.com"
    portal_settings = {"id": target + "/backendHttpSettingsCollection/behttp-portal"}
    return {
        "id": target,
        "name": "agw-test-fixture",
        "type": "Microsoft.Network/applicationGateways",
        "etag": '"v1"',
        "location": "japaneast",
        "tags": {"layer": "nw"},
        "identity": {
            "type": "UserAssigned", "principalId": "readonly", "tenantId": "readonly",
            "userAssignedIdentities": {identity: {"principalId": "readonly", "clientId": "readonly"}},
        },
        "properties": {
            "provisioningState": "Succeeded",
            "resourceGuid": "readonly",
            "operationalState": "Running",
            "sku": {"name": "WAF_v2", "tier": "WAF_v2"},
            "sslCertificates": [{"name": "cert", "properties": {
                "keyVaultSecretId": "https://example.vault.azure.net/secrets/cert", "publicCertData": "readonly",
            }}],
            "trustedRootCertificates": [{"name": root_name}],
            "httpListeners": [{"name": "portal", "properties": {"hostName": "portal.example.test"}}],
            "backendHttpSettingsCollection": [
                {"name": "behttp-portal", "properties": {
                    "port": 443, "protocol": "Https", "hostName": portal_host,
                    "probe": {"id": target + "/probes/probe-portal"}, "trustedRootCertificates": [root],
                }},
                {"name": "behttp-apim", "properties": {
                    "port": 443, "protocol": "Https", "hostName": apim_host,
                    "probe": {"id": target + "/probes/probe-apim"}, "trustedRootCertificates": [root.copy()],
                }},
            ],
            "requestRoutingRules": [{"name": "portal", "properties": {"backendHttpSettings": portal_settings}}],
            "probes": [
                {"name": "probe-apim", "properties": {
                    "path": "/status-0123456789abcdef", "protocol": "Https", "port": 443,
                    "host": apim_host, "match": {"statusCodes": ["200-399"]},
                }},
                {"name": "probe-portal", "id": target + "/probes/probe-portal", "etag": '"p1"', "properties": {
                    "path": "/api/health", "protocol": "Https", "port": 443, "host": portal_host,
                    "minServers": 0, "provisioningState": "Succeeded",
                    "backendHttpSettings": [portal_settings.copy()], "match": {"statusCodes": ["200"]},
                }},
            ],
        },
    }


class AppGatewayTransport:
    def __init__(self, bodies):
        self.responses = list(bodies)
        self.calls = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": copy.deepcopy(body), "headers": headers})
        return self.responses.pop(0)
