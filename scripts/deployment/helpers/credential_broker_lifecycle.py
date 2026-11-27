"""認証窓口専用の秘密と既存アプリを、構築対象の環境に結び付けて扱う。"""
from .credential_pools import resource_names
from .grpc_lifecycle import _prepare_credential


def prepare_secret(config, root, write_protected):
    return {"credentialBrokerKey": _prepare_credential(config, root, "credential-broker", write_protected)}


def broker_environment_id(config):
    """Broker専用内部CAEの固定IDです。PortalのCAEは参照しません。"""
    return (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
            f"/providers/Microsoft.App/managedEnvironments/cae-{config['resourceStem']}-broker")


def uses_broker_environment(properties, expected):
    """ARMが両方の環境参照を返す場合も、そのすべてを照合します。"""
    if not isinstance(properties, dict):
        return False
    references = [properties[key] for key in ("managedEnvironmentId", "environmentId") if properties.get(key) not in (None, "")]
    return bool(references) and all(isinstance(value, str) and value.casefold() == expected.casefold() for value in references)


def broker_https_ingress(properties, expected_environment):
    """専用内部CAEのILBへ公開するHTTPS ingressだけを返します。"""
    if not uses_broker_environment(properties, expected_environment):
        return {}
    configuration = properties.get("configuration") or {}
    ingress = configuration.get("ingress") if isinstance(configuration, dict) else None
    return ingress if (isinstance(ingress, dict) and ingress.get("external") is True
                       and ingress.get("allowInsecure", False) is False) else {}


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
    if not uses_broker_environment(properties, broker_environment_id(config)) or properties.get("provisioningState") != "Succeeded":
        raise ValueError("Credential broker application is not ready in the expected environment")
    return True
