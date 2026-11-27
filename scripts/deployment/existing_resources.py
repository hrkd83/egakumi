"""Coreの再実行で保持するネットワーク・APIM・Portalの既存設定を読み取る。"""
from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .arm_response import DeploymentError, require_success, same_azure_location
from .helpers.aca_egress import validate_aca_route_snapshot
from .helpers.apim_egress import APIM_EGRESS_RULE_NAME, validate_apim_egress_rule
from .helpers.ingress import apim_name_matches

if TYPE_CHECKING:
    from .lifecycle import ArmTransport


def discover_existing_network_security_groups(config: dict, transport: ArmTransport) -> list[str]:
    """今回の所有NSGだけを読み、同じ環境の既存資源を名前で返す。

    再upでNSG本体を空のpropertiesで更新すると、AppGWが必要とする既存規則を
    Azureが認識できず拒否する場合がある。本体の新規作成が必要かを先に確認し、
    規則自体の更新は従来の子リソース配備へ任せる。手動で指定する公開設定にはしない。
    同名でも所有タグや配置先が違う資源は再利用せず、確認できない応答でも停止する。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/networkSecurityGroups/")
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    existing = []
    suffixes = ("agw", "apim", "pep", "vm", "portal", "certificate", "broker")
    if config.get("auditEvidence", {}).get("enabled") is True:
        suffixes += ("audit",)
    for suffix in suffixes:
        name = f"nsg-{config['resourceStem']}-{suffix}"
        resource_id = prefix + name
        response = transport.request("GET", resource_id + "?api-version=2025-01-01")
        body = require_success(response, "Read existing core network security group", allowed=(200, 404))
        if response["statusCode"] == 404:
            continue
        tags = body.get("tags") if isinstance(body, dict) else None
        if (not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != resource_id.casefold()
                or str(body.get("name", "")).casefold() != name.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.network/networksecuritygroups"
                or not same_azure_location(body.get("location"), config["location"])
                or not isinstance(tags, dict)
                or any(tags.get(key) != value for key, value in expected_tags.items())):
            raise DeploymentError("Existing network security group does not match this environment's identity, location or ownership tags")
        existing.append(name)
    return existing


def discover_existing_route_table_routes(config: dict, transport: ArmTransport) -> dict:
    """今回の基本経路表と有効なACA経路表を読み、親の再配備へ引き継ぐ既存経路だけを返す。

    親の PUT で routes を省略すると既存 UDR が消えるため、動的経路の子が
    新しい値を上書きするまで現在の経路を保持する。ルート伝搬の設定はコピーせず、
    Bicep が今回の config を適用する。利用者が指定する公開パラメーターではない。
    経路の宛先・次ホップ・所属先を確認し、書込対象の設定だけを引き継ぐ。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/routeTables/")
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    route_properties = {"addressPrefix", "nextHopType", "nextHopIpAddress", "hasBgpOverride"}
    existing = {}
    suffixes = ("agw", "apim", "vm", "portal", "certificate", "broker")
    if config.get("auditEvidence", {}).get("enabled") is True:
        suffixes += ("audit",)
    for suffix in suffixes:
        name = f"rt-{config['resourceStem']}-{suffix}"
        resource_id = prefix + name
        response = transport.request("GET", resource_id + "?api-version=2025-01-01")
        body = require_success(response, "Read existing core route table", allowed=(200, 404))
        if response["statusCode"] == 404:
            existing[name] = []
            continue
        tags = body.get("tags") if isinstance(body, dict) else None
        if (not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != resource_id.casefold()
                or str(body.get("name", "")).casefold() != name.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.network/routetables"
                or not same_azure_location(body.get("location"), config["location"])
                or not isinstance(tags, dict)
                or any(tags.get(key) != value for key, value in expected_tags.items())):
            raise DeploymentError("Existing route table does not match this environment's identity, location or ownership tags")
        properties = body.get("properties")
        if (not isinstance(properties, dict)
                or not isinstance(properties.get("routes"), list)
                or properties.get("provisioningState", "Succeeded") != "Succeeded"):
            raise DeploymentError("Existing route table properties or current routes could not be verified")
        sanitized = []
        seen = set()
        for route in properties["routes"]:
            if (not isinstance(route, dict)
                    or not isinstance(route.get("name"), str) or not route["name"]
                    or route["name"] != route["name"].strip() or "/" in route["name"]
                    or route["name"].casefold() in seen):
                raise DeploymentError("Existing route child identity or properties could not be verified")
            route_name = route["name"]
            if (("id" in route and str(route["id"]).casefold() != (resource_id + "/routes/" + route_name).casefold())
                    or ("type" in route and str(route["type"]).casefold() != "microsoft.network/routetables/routes")):
                raise DeploymentError("Existing route child does not belong to the expected route table")
            fields = route.get("properties")
            if (not isinstance(fields, dict)
                    or any(not isinstance(fields.get(key), str) or not fields[key] for key in ("addressPrefix", "nextHopType"))
                    or (fields.get("nextHopIpAddress") is not None and not isinstance(fields["nextHopIpAddress"], str))
                    or (fields.get("hasBgpOverride") is not None and not isinstance(fields["hasBgpOverride"], bool))
                    or fields.get("provisioningState", "Succeeded") != "Succeeded"):
                raise DeploymentError("Existing route semantics could not be verified; no route values were logged")
            seen.add(route_name.casefold())
            # id/type/etag/進行状態は読取用。既存の全書込可能値だけを複写する。
            sanitized.append({"name": route_name, "properties": {
                key: copy.deepcopy(value) for key, value in fields.items() if key in route_properties}})
        if suffix in ("portal", "certificate", "broker", "audit"):
            if properties.get("disableBgpRoutePropagation") is not True:
                raise DeploymentError("ACA public HTTPS requires BGP propagation to be disabled")
            if suffix == "certificate" and any(route["name"] != "default-egress" for route in sanitized):
                raise DeploymentError("Certificate ACA uses only the owned default Firewall route")
            try:
                subnets = config["network"]["subnetPrefixes"]
                validate_aca_route_snapshot(sanitized, subnets["firewall"], subnets["agw"])
            except ValueError as error:
                raise DeploymentError(str(error)) from None
        existing[name] = sanitized
    return existing


def discover_existing_apim_egress_fqdns(config: dict, transport: ArmTransport) -> list[str]:
    """公開済みAPIのHTTPS宛先を読み、次の基盤配備へ引き継ぐ。

    API公開処理は同じFirewall規則へ宛先を追加する。親の規則群を再配備すると
    配列全体が置き換わるため、既存の許可先を生成入力にだけ含める。
    所有者・送信元・HTTPS境界が一致した規則だけを採用し、取得失敗は停止する。
    """
    name = "afwp-" + config["resourceStem"]
    policy_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
                 f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/firewallPolicies/{name}")
    response = transport.request("GET", policy_id + "?api-version=2025-01-01")
    policy = require_success(response, "Read existing API egress policy", allowed=(200, 404))
    if response["statusCode"] == 404:
        return []
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    if (not isinstance(policy, dict)
            or str(policy.get("id", "")).casefold() != policy_id.casefold()
            or str(policy.get("name", "")).casefold() != name.casefold()
            or str(policy.get("type", "")).casefold() != "microsoft.network/firewallpolicies"
            or not same_azure_location(policy.get("location"), config["location"])
            or not isinstance(policy.get("tags"), dict)
            or any(policy["tags"].get(key) != value for key, value in expected_tags.items())):
        raise DeploymentError("Existing API egress policy does not match this environment's identity, location or ownership tags")
    group_id = policy_id + "/ruleCollectionGroups/rcg-egress"
    response = transport.request("GET", group_id + "?api-version=2025-01-01")
    group = require_success(response, "Read existing API egress rules", allowed=(200, 404))
    if response["statusCode"] == 404:
        return []
    properties = group.get("properties") if isinstance(group, dict) else None
    if (not isinstance(properties, dict)
            or str(group.get("id", "")).casefold() != group_id.casefold()
            or properties.get("provisioningState", "Succeeded") != "Succeeded"
            or not isinstance(properties.get("ruleCollections"), list)
            or any(not isinstance(item, dict) for item in properties["ruleCollections"])):
        raise DeploymentError("Existing API egress rule inventory could not be verified")
    collections = [item for item in properties["ruleCollections"] if item.get("name") == "allow-apim-egress"]
    if not collections:
        return []
    if (len(collections) != 1
            or collections[0].get("ruleCollectionType") != "FirewallPolicyFilterRuleCollection"
            or collections[0].get("action") != {"type": "Allow"}
            or not isinstance(collections[0].get("rules"), list)
            or any(not isinstance(item, dict) for item in collections[0]["rules"])):
        raise DeploymentError("Existing API egress collection has a different permission boundary")
    rules = [item for item in collections[0]["rules"] if item.get("name") == APIM_EGRESS_RULE_NAME]
    if not rules:
        return []
    if len(rules) != 1:
        raise DeploymentError("Existing API egress rule is duplicated")
    rule = rules[0]
    try:
        validate_apim_egress_rule(rule, config["network"]["subnetPrefixes"]["apim"])
    except ValueError as exc:
        raise DeploymentError(str(exc)) from None
    hosts = []
    for host in rule["targetFqdns"]:
        if host.casefold() not in {value.casefold() for value in hosts}:
            hosts.append(host)
    return hosts


def discover_existing_portal_app(config: dict, transport: ArmTransport) -> bool:
    """対象 CA の実在と配置先を照合し、初回作成が必要かを基盤配備へ伝える。

    初回は Bicep が初期イメージで作成し、Actions が Portal 本体へ更新する。
    再 up で本体のイメージ・Ingress・環境変数を初期設定へ戻さないよう、
    同じ CAE 内の既存 CA は基盤からの PUT を省く。設定や秘密値は複写しない。
    実在確認の 404 だけを未作成とし、権限エラーや状態不明の応答では停止する。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['svc']}/providers/Microsoft.App/")
    application_name = f"ca-{config['resourceStem']}-portal"
    resource_id = prefix + "containerApps/" + application_name
    expected_environment = prefix + f"managedEnvironments/cae-{config['resourceStem']}"
    response = transport.request("GET", resource_id + "?api-version=2025-01-01")
    body = require_success(response, "Read existing Portal Container App", allowed=(200, 404))
    if response["statusCode"] == 404:
        return False
    if (not isinstance(body, dict)
            or str(body.get("id", "")).casefold() != resource_id.casefold()
            or str(body.get("name", "")).casefold() != application_name.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.app/containerapps"
            or not same_azure_location(body.get("location"), config["location"])):
        raise DeploymentError("Existing Portal Container App does not match the configured identity or location")
    properties = body.get("properties")
    if (not isinstance(properties, dict)
            or str(properties.get("managedEnvironmentId", "")).casefold() != expected_environment.casefold()):
        raise DeploymentError("Existing Portal Container App belongs to a different managed environment")
    if properties.get("provisioningState") != "Succeeded":
        raise DeploymentError("Existing Portal Container App is not ready; check its deployment before retrying")
    return True


def discover_existing_apim_hostnames(config: dict, transport: ArmTransport, *, custom_properties_out: dict | None = None) -> dict:
    """同じ環境の APIM を読み、再 PUT で消してはいけない公開ドメイン設定を引き継ぐ。

    証明書の初回発行は APIM 作成後なので、ドメインは postprovision が設定する。
    次の up では本体 PUT でもその設定を明示する必要がある。Bicep が決める名前末尾を
    Python で再計算せず、所有 RG の一覧から名前・配置・タグを照合して本体を読み直す。
    戻り値は実 APIM 名ごとの公開情報だけで、秘密証明書や観測した証明書情報・状態は渡さない。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service")
    listed = transport.request("GET", prefix + "?api-version=2024-05-01")
    inventory = require_success(listed, "Read existing core API Management inventory", allowed=(200, 404))
    if listed["statusCode"] == 404:
        return {}
    if not isinstance(inventory, dict) or not isinstance(inventory.get("value"), list) or inventory.get("nextLink"):
        raise DeploymentError("Existing API Management inventory could not be verified")
    candidates = []
    for item in inventory["value"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise DeploymentError("Existing API Management inventory could not be verified")
        if apim_name_matches(config, item["name"]):
            candidates.append(item)
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise DeploymentError("Existing API Management identity is ambiguous; no domain values were logged")
    name = candidates[0]["name"]
    resource_id = prefix + "/" + name
    response = transport.request("GET", resource_id + "?api-version=2024-05-01")
    body = require_success(response, "Read existing API Management hostname configuration", allowed=(200,))
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    tags = body.get("tags") if isinstance(body, dict) else None
    if (not isinstance(body, dict) or str(body.get("id", "")).casefold() != resource_id.casefold()
            or str(body.get("name", "")).casefold() != name.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.apimanagement/service"
            or not same_azure_location(body.get("location"), config["location"])
            or not isinstance(tags, dict) or any(tags.get(key) != value for key, value in expected_tags.items())):
        raise DeploymentError("Existing API Management does not match this environment's identity, location or ownership tags")
    properties = body.get("properties")
    if (not isinstance(properties, dict) or properties.get("provisioningState") != "Succeeded"
            or not isinstance(properties.get("hostnameConfigurations"), list)):
        raise DeploymentError("Existing API Management hostname configuration is not ready for a new deployment")
    custom = properties.get("customProperties", {})
    if (not isinstance(custom, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]{0,255}", key)
            or not isinstance(value, str) or value.lower() not in ("true", "false")
            for key, value in custom.items())):
        raise DeploymentError("Existing API Management protocol settings cannot be safely preserved")
    sanitized, seen = [], set()
    writable = {"type", "hostName", "keyVaultId", "identityClientId", "defaultSslBinding",
                "negotiateClientCertificate", "certificateSource"}
    for domain in properties["hostnameConfigurations"]:
        # ARM GETは証明書本文/パスワードの欄をnullで返すため、nullだけ許容して捨てます。
        # 非nullの値が返った場合は停止し、公開設定の保持処理へ秘密値をコピーしません。
        private_response_fields = {"encodedCertificate", "certificatePassword"}
        if (not isinstance(domain, dict)
                or any(domain.get(key) is not None for key in private_response_fields)
                or domain.get("type") not in ("Proxy", "Portal", "Management", "Scm", "DeveloperPortal", "ConfigurationApi")
                or not isinstance(domain.get("hostName"), str) or not domain["hostName"]
                or len(domain["hostName"]) > 253
                or not re.fullmatch(r"(?:\*\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", domain["hostName"])
                or any(key in domain and not isinstance(domain[key], bool) for key in ("defaultSslBinding", "negotiateClientCertificate"))
                or (domain.get("identityClientId") is not None and not re.fullmatch(r"[0-9a-fA-F-]{36}", str(domain["identityClientId"])))):
            raise DeploymentError("Existing API Management domain cannot be preserved without unsupported or private input")
        identity = (domain["type"], domain["hostName"].casefold())
        if identity in seen:
            raise DeploymentError("Existing API Management domain identity is ambiguous")
        seen.add(identity)
        source, vault_uri = domain.get("certificateSource"), domain.get("keyVaultId")
        if vault_uri:
            # 参照 URL だけを保持し、Key Vault の証明書・Secret 本文を読むことはない。
            if not isinstance(vault_uri, str) or any(ord(char) <= 32 for char in vault_uri):
                raise DeploymentError("Existing API Management certificate reference could not be verified")
            uri = urlparse(vault_uri)
            if (source not in (None, "KeyVault") or uri.scheme != "https"
                    or not re.fullmatch(r"[a-z0-9-]+\.vault\.azure\.net(?::443)?", uri.netloc, re.IGNORECASE)
                    or uri.query or uri.fragment or uri.params
                    or not re.fullmatch(r"/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+)?/?", uri.path)):
                raise DeploymentError("Existing API Management certificate reference could not be verified")
        elif (source not in (None, "BuiltIn")
                or domain["hostName"].casefold() != (name + ".azure-api.net").casefold()
                or domain["type"] != "Proxy"):
            # Managed/inline Custom 証明書の再 PUT を秘密情報なしで保証できないため停止する。
            raise DeploymentError("Existing API Management certificate requires explicit review before redeployment")
        sanitized.append({key: copy.deepcopy(value) for key, value in domain.items()
                          if key in writable and (value is not None or key == "identityClientId")})
    if custom_properties_out is not None:
        custom_properties_out[name.casefold()] = copy.deepcopy(custom)
    return {name.casefold(): sanitized}
