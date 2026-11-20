"""認証窓口専用の秘密と既存アプリを、構築対象の環境に結び付けて扱う。"""
from .credential_pools import resource_names
from .grpc_lifecycle import _prepare_credential


def prepare_secret(config, root, write_protected):
    return {"credentialBrokerKey": _prepare_credential(config, root, "credential-broker", write_protected)}


def discover_existing_application(config, transport):
    names = resource_names(config)
    prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.App/"
    target = prefix + "containerApps/" + names["application"]
    response = transport.request("GET", target + "?api-version=2025-01-01")
    if response.get("statusCode") == 404:
        return False
    body = response.get("body") or {}
    if response.get("statusCode") != 200 or str(body.get("id", "")).lower() != target.lower():
        raise ValueError("Credential broker application could not be verified")
    properties = body.get("properties", {})
    environment = prefix + "managedEnvironments/cae-" + config["resourceStem"]
    if str(properties.get("managedEnvironmentId", "")).lower() != environment.lower() or properties.get("provisioningState") != "Succeeded":
        raise ValueError("Credential broker application is not ready in the expected environment")
    return True
