"""構築したApp Gatewayへ、Azure公開DNSのAエイリアスを接続する。

証明書用のTXTとは別に、GatewayとPortalの恒久的な入口を設定する。
DNSゾーンは指定されたDNS用Subscriptionから探し、公開名に最も長く
一致するゾーンを使う。利用者のパラメーターや他サービスのレコードは変更しない。
"""
from __future__ import annotations

import copy
import ipaddress
import re
from urllib.parse import parse_qsl, urlparse


DNS_API = "2018-05-01"
NETWORK_API = "2025-01-01"
DEFAULT_TTL = 300
MAX_PAGES = 100
_GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class PublicDnsError(ValueError):
    """公開DNSを照合できず、追加変更を止めたことを表す。"""


def _error(message: str) -> PublicDnsError:
    # Azureの応答本文には他資源の情報が含まれるため、そのまま表示しない。
    return PublicDnsError("Public DNS: " + message)


def _hostname(value) -> str:
    if not isinstance(value, str):
        raise _error("Gateway and Portal hostnames are required")
    name = value.lower()
    if (len(name) > 253 or "." not in name
            or any(not _LABEL.fullmatch(label) for label in name.split("."))):
        raise _error("a configured hostname or DNS zone name is invalid")
    return name


def _ok(response: dict, operation: str, allowed=(200,)):
    if not isinstance(response, dict) or response.get("statusCode") not in allowed:
        status = response.get("statusCode") if isinstance(response, dict) else None
        raise _error(f"{operation} failed (HTTP {status})")
    return response.get("body")


def _public_ip(config: dict, outputs: dict, transport) -> tuple[str, str]:
    """今回のNW RGにある固定名のPublic IPを、実配備出力と実資源の両方で照合する。"""
    expected = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
                f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/"
                f"publicIPAddresses/pip-agw-{config['resourceStem']}")
    gateway = outputs.get("gateway")
    if (not isinstance(gateway, dict) or not isinstance(gateway.get("publicIpId"), str)
            or gateway["publicIpId"].casefold() != expected.casefold()):
        raise _error("deployment output does not identify this environment's App Gateway Public IP")
    body = _ok(transport.request("GET", expected + "?api-version=" + NETWORK_API),
               "read App Gateway Public IP")
    props = body.get("properties") if isinstance(body, dict) else None
    if (not isinstance(body, dict) or not isinstance(props, dict)
            or str(body.get("id", "")).casefold() != expected.casefold()
            or str(body.get("name", "")).casefold() != ("pip-agw-" + config["resourceStem"]).casefold()
            or str(body.get("type", "")).casefold() != "microsoft.network/publicipaddresses"
            or props.get("provisioningState") != "Succeeded"
            or props.get("publicIPAllocationMethod") != "Static"
            or props.get("publicIPAddressVersion") != "IPv4"):
        raise _error("App Gateway Public IP identity, state or allocation could not be confirmed")
    try:
        if not isinstance(props.get("ipAddress"), str):
            raise ValueError("IPv4 address must be a string")
        address = str(ipaddress.IPv4Address(props.get("ipAddress")))
    except (ValueError, TypeError):
        raise _error("App Gateway Public IP has no valid IPv4 address") from None
    if gateway.get("publicIp") != address:
        raise _error("App Gateway Public IP differs from the deployment output")
    return expected, address


def _zones(subscription: str, transport) -> list[dict]:
    """全ページを読み、同じSubscriptionの公開DNSゾーンだけを候補にする。"""
    collection = f"/subscriptions/{subscription}/providers/Microsoft.Network/dnsZones"
    path = collection + "?api-version=" + DNS_API
    visited, identities, zones = set(), set(), []
    zone_id = re.compile(r"/subscriptions/" + re.escape(subscription)
                         + r"/resourceGroups/([^/]+)/providers/Microsoft\.Network/dnsZones/([^/]+)\Z", re.I)
    while path:
        if len(path) > 8192 or any(ord(char) <= 32 or ord(char) == 127 for char in path):
            raise _error("DNS zone pagination could not be verified")
        try:
            uri = urlparse(path if path.startswith("https://") else "https://management.azure.com" + path)
            pairs = parse_qsl(uri.query, keep_blank_values=True)
            valid_uri = (uri.scheme == "https" and uri.hostname == "management.azure.com"
                         and uri.username is None and uri.password is None and uri.port in (None, 443)
                         and not uri.fragment and not uri.params
                         and uri.path.casefold() == collection.casefold()
                         and len({key for key, _ in pairs}) == len(pairs)
                         and dict(pairs).get("api-version") == DNS_API
                         and set(dict(pairs)) <= {"api-version", "$skiptoken", "$top"})
        except (ValueError, TypeError):
            valid_uri = False
        if not valid_uri or uri.geturl() in visited or len(visited) >= MAX_PAGES:
            raise _error("DNS zone pagination could not be verified")
        visited.add(uri.geturl())
        body = _ok(transport.request("GET", uri.geturl()), "list Azure public DNS zones")
        entries = body.get("value") if isinstance(body, dict) else None
        if not isinstance(entries, list):
            raise _error("DNS zone list is malformed")
        for entry in entries:
            resource_id = entry.get("id") if isinstance(entry, dict) else None
            match = zone_id.fullmatch(resource_id) if isinstance(resource_id, str) else None
            if (not match or str(entry.get("type", "")).casefold() != "microsoft.network/dnszones"
                    or str(entry.get("name", "")).casefold() != match[2].casefold()
                    or resource_id.casefold() in identities):
                raise _error("DNS zone identity could not be confirmed")
            identities.add(resource_id.casefold())
            zones.append({"id": resource_id, "name": _hostname(match[2])})
        path = body.get("nextLink")
        if path is not None and not isinstance(path, str):
            raise _error("DNS zone continuation is malformed")
    return zones


def _select_zone(hostname: str, zones: list[dict]) -> tuple[dict, str]:
    candidates = [zone for zone in zones if hostname == zone["name"] or hostname.endswith("." + zone["name"])]
    if not candidates:
        raise _error(f"no accessible Azure public DNS zone matches {hostname}")
    longest = max(len(zone["name"]) for zone in candidates)
    selected = [zone for zone in candidates if len(zone["name"]) == longest]
    if len(selected) != 1:
        raise _error(f"more than one Azure public DNS zone matches {hostname}; select a unique zone before retrying")
    zone = selected[0]
    relative = "@" if hostname == zone["name"] else hostname[:-(len(zone["name"]) + 1)]
    return zone, relative


def _record_properties(body, record_id: str) -> dict:
    props = body.get("properties") if isinstance(body, dict) else None
    if (not isinstance(body, dict) or not isinstance(props, dict)
            or str(body.get("id", "")).casefold() != record_id.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.network/dnszones/a"):
        raise _error("existing A record identity could not be confirmed")
    ttl = props.get("TTL")
    if type(ttl) is not int or not 1 <= ttl <= 2147483647:
        raise _error("existing A record TTL is invalid")
    metadata = props.get("metadata", {})
    if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()):
        raise _error("existing A record metadata is invalid")
    return props


def _etag(response: dict) -> str:
    body = response.get("body") or {}
    headers = response.get("headers") or {}
    if not isinstance(headers, dict) or any(not isinstance(key, str) for key in headers):
        raise _error("existing A record ETag could not be confirmed")
    values = [value for key, value in headers.items() if key.casefold() == "etag"]
    if body.get("etag") is not None:
        values.append(body["etag"])
    if (not values or any(not isinstance(value, str) or not value or len(value) > 512
                          or any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values)
            or len(set(values)) != 1):
        raise _error("existing A record ETag could not be confirmed")
    return values[0]


def _plan_record(hostname: str, zone: dict, relative: str, target: str, address: str, transport) -> dict:
    record_id = zone["id"] + "/A/" + relative
    path = record_id + "?api-version=" + DNS_API
    response = transport.request("GET", path)
    body = _ok(response, "read public A record", allowed=(200, 404))
    # AとCNAMEは同じ公開名へ共存できないため、既存CNAMEも書込み前に確認する。
    cname = transport.request("GET", zone["id"] + "/CNAME/" + relative + "?api-version=" + DNS_API)
    _ok(cname, "check conflicting CNAME", allowed=(200, 404))
    if cname["statusCode"] == 200:
        raise _error(f"a CNAME already uses {hostname}; review it before replacing the record")
    if response["statusCode"] == 404:
        props, existing, headers, method = {"TTL": DEFAULT_TTL}, False, {"If-None-Match": "*"}, "PUT"
    else:
        props = _record_properties(body, record_id)
        headers = {"If-Match": _etag(response)}
        target_ref = props.get("targetResource")
        if target_ref is None:
            target_ref = {}
        if not isinstance(target_ref, dict) or set(target_ref) - {"id"}:
            raise _error("existing A alias target is malformed")
        current_target = target_ref.get("id")
        if current_target is not None and (not isinstance(current_target, str) or current_target.casefold() != target.casefold()):
            raise _error(f"{hostname} points to another Azure resource; review it before replacing the alias")
        addresses = props.get("ARecords", [])
        if not isinstance(addresses, list) or (addresses and addresses != [{"ipv4Address": address}]):
            raise _error(f"{hostname} points to another IPv4 address; review it before replacing the record")
        if current_target and addresses:
            raise _error("existing A record combines an alias with an IPv4 address")
        existing, method = True, "PATCH"
        if current_target and not addresses:
            method = None
    # FQDNなどのAzureが生成する属性は送らず、利用者のTTL・metadataを引き継ぐ。
    properties = {"TTL": props["TTL"], "targetResource": {"id": target}, "ARecords": []}
    if "metadata" in props:
        properties["metadata"] = copy.deepcopy(props["metadata"])
    return {"hostname": hostname, "zoneId": zone["id"], "recordSetId": record_id,
            "ttl": props["TTL"], "status": "unchanged" if method is None else "updated" if existing else "created",
            "path": path, "method": method, "headers": headers, "body": {"properties": properties}}


def configure_public_dns(config: dict, outputs: dict, core_transport, dns_transport) -> dict:
    """Azure DNSの場合に公開Aエイリアスを設定し、保存された参照先を読み直す。"""
    hosts = [_hostname(config.get(section, {}).get("publicHostName")) for section in ("gateway", "portal")]
    if len(set(hosts)) != len(hosts):
        raise _error("Gateway and Portal must have distinct hostnames")
    certificate = config.get("certificate", {})
    if certificate.get("mode") != "dnsPlugin" or str(certificate.get("plugin", "")).casefold() != "azure":
        return {"status": "external", "provider": certificate.get("plugin", certificate.get("mode", "external")),
                "hostnames": hosts}
    subscription = certificate.get("azureDnsSubscriptionId")
    if not isinstance(subscription, str) or not _GUID.fullmatch(subscription):
        raise _error("the Azure DNS subscription ID is required")
    target, address = _public_ip(config, outputs, core_transport)
    zones = _zones(subscription, dns_transport)
    # 片方の競合を検出する前にもう片方を変更しないよう、2件とも先に検査する。
    plans = [_plan_record(host, *_select_zone(host, zones), target, address, dns_transport) for host in hosts]
    for plan in plans:
        if plan["method"] is None:
            continue
        _ok(dns_transport.request(plan["method"], plan["path"], body=plan["body"], headers=plan["headers"]),
            "save public A alias", allowed=(200, 201))
        actual = _record_properties(_ok(dns_transport.request("GET", plan["path"]), "verify saved public A alias"),
                                    plan["recordSetId"])
        expected = plan["body"]["properties"]
        if (not isinstance(actual.get("targetResource"), dict)
                or str(actual["targetResource"].get("id", "")).casefold() != target.casefold()
                or actual.get("ARecords", []) != [] or actual.get("TTL") != expected["TTL"]
                or actual.get("metadata", {}) != expected.get("metadata", {})):
            raise _error("saved public A alias differs from the requested target, TTL or metadata")
    return {"status": "configured", "provider": "Azure", "dnsSubscriptionId": subscription,
            "targetResourceId": target, "publicIp": address,
            "records": [{key: plan[key] for key in ("hostname", "zoneId", "recordSetId", "ttl", "status")} for plan in plans]}
