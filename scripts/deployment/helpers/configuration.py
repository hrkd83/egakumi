#!/usr/bin/env python3
"""企業ごとのparamsと標準configを読み、Azureへ渡せる設定に整える。

このファイルが、設定の読込・合成・型検査・ファイル参照解決の入口となる。
paramsのconfigFileを基準に標準設定を読み、企業固有の入力を上書きしてから、
ネットワーク、認証、モデル、証明書などの組合せをローカルで検査する。
公開設定の出力には秘密の内容を含めない。秘密ファイルを開く処理は
load_secret_inputsに分け、Bicepへ渡す直前にだけ呼び出す。
Azureへの通信、資源作成、課金操作は行わない。資源名は環境名・製品名と
保存済みのAPIM識別子からBicepと同じ規則で決め、実行時に変更しない。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import base64
import copy
import ipaddress
import json
import re
import ssl
import stat
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

# ファイルを直接実行しても、同じdeployment.helpersパッケージから読み込む。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "deployment.helpers"

from .catalog_descriptions import with_catalog_descriptions
from .apim_naming import apim_instance_id
from .apim_input_contract import validate_input_contract
from .apim_document_contract import DOCUMENT_API_VERSION
from .credential_login import SESSION_POLICY_DEFAULTS, SESSION_POLICY_BOUNDS, MAX_TRANSACTION_LIFETIME_SECONDS


class ConfigurationError(ValueError):
    """設定の欠落・矛盾を、秘密値を含まないメッセージで伝える例外。"""


class DnsProviderMismatchError(ConfigurationError):
    """JSONの破損と区別して、選択したDNS事業者と資格情報形式の不一致を示す。"""


RG_KINDS = ("id", "nw", "monitor", "svc", "maint")
SUBNET_KINDS = ("agw", "firewall", "apim", "pep", "management", "aca", "broker")
RFC1918_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
# 観測対象の操作・受付へ付けるラベルです。サービスやモデルの種類を列挙しません。
QUERY_OBSERVATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
TOP_KEYS = {
    "$schema", "$comment",
    "schemaVersion", "productName", "environmentName", "subscriptionId",
    "azureTenantId", "entraTenantId", "location", "foundryLocation", "apiCenterLocation", "tags", "network",
    "services", "gateway", "management", "monitoring", "certificate", "firewallTlsInspection", "authorization",
    "apiDefinitions", "publishApis", "credentialProviders", "portal", "credentialBroker", "portalAuthentication", "portalApiKeys", "codex", "auditEvidence",
}
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
API_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+\Z")
REGION = re.compile(r"[a-z][a-z0-9]+\Z")
SECRET_VALUE_KEYS = {"adminPassword", "password", "pfxPassword", "cloudflareToken", "apiToken", "clientSecret", "accessToken", "primaryKey", "secondaryKey"}

# 導入先の識別・配置・登録情報だけをparamsへ残す。共通動作はconfig、
# 初期構成の定数と構築情報からの導出は資材が所有する。
PARAMETER_KEYS = {
    "$schema", "$comment", "configFile", "schemaVersion", "productName",
    "environmentName", "subscriptionId", "azureTenantId", "entraTenantId", "services", "gateway",
    "management", "monitoring", "certificate", "firewallTlsInspection",
    "credentialProviders", "portal", "codex", "portalApiKeys", "network", "location", "foundryLocation", "apiCenterLocation", "tags",
}
# このVNet内で構築する資材のアドレスだけをNSGへ動的に渡します。
# VNet・Firewall DNSとNSG初期集合はBicep内で固定し、公開入力では変更しません。
PARAMETER_NETWORK_IP_KEYS = {
    "addressSpaces", "subnetPrefixes",
}
RETIRED_NETWORK_INPUTS = {
    "clientAddressPrefixes", "certificatePublicProbeEnabled", "dnsServers",
    "operatorAddressPrefixes", "operatorProtocol", "operatorPorts",
    "ntpAddressPrefixes", "ntpFqdns", "managementAdditionalSecurityRules",
    "apimKeyVaultAccessEnabled",
}
PARAMETER_FIREWALL_IP_KEYS = set()
RETIRED_FIREWALL_INPUTS = {
    "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
    "apimAdditionalApplicationRules", "managementAdditionalApplicationRules",
    "apimAllowedFqdns", "managementAllowedFqdns",
}
PARAMETER_SECTION_KEYS = {
    "portal": {"publicHostName", "authentication", "certificate", "apiAuthentication"},
    "codex": {"clientId"},
    "network": PARAMETER_NETWORK_IP_KEYS,
    "services": {"publisherName", "publisherEmail", "apimInstanceId"},
    "gateway": {"publicHostName", "authentication"},
    "management": {"adminUsername", "sshPublicKeyFile"},
    "monitoring": {"operatorEmailAddresses"},
    "certificate": {"contactEmail", "azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"},
}


# 企業固有の情報はparamsだけが所有します。共通設定と同じ親を持つ欄は
# 最下層のパスで示し、親全体をparams所有にはしません。
# 空配列や空文字でもconfigへ置けば拒否し、他企業の値を黙って継承させません。
PARAMETER_FIREWALL_KEYS = PARAMETER_FIREWALL_IP_KEYS
PARAMETER_GATEWAY_NESTED_KEYS = {
    "authentication": {"credentialLogin", "entra"},
}
PARAMETER_OWNED_PATHS = (
    *((key,) for key in ("schemaVersion", "productName", "environmentName", "subscriptionId", "azureTenantId", "entraTenantId",
                         "location", "foundryLocation", "apiCenterLocation", "tags",
                         )),
    *(("network", key) for key in PARAMETER_NETWORK_IP_KEYS),
    *(("network", "firewall", key) for key in PARAMETER_FIREWALL_KEYS),
    *((section, key) for section in ("services", "management", "monitoring", "certificate", "portal")
      for key in PARAMETER_SECTION_KEYS[section]),
    ("firewallTlsInspection",),
    ("gateway", "publicHostName"),
    ("portal",), ("codex",),
    ("gateway", "authentication", "entra", "audience"),
    ("gateway", "authentication", "entra", "obo"),
    *(("gateway", "authentication", "credentialLogin", "identity", key) for key in ("clientId", "clientSecretFile")),
    *(("credentialProviders", "graphDelegated", key) for key in ("clientId", "clientSecretFile")),
    *(("portalApiKeys", key) for key in ("platformGroupId", "groupNamePattern")),
)

# 旧入力は空値でも受け付けず、値の意味を変える自動移行をしません。
FIXED_INITIAL_PATHS = (
    ("authorization",), ("apiDefinitions",), ("publishApis",),
    ("management", "loginAssignments"),
    *(("network", "firewall", key) for key in ("snatPrivateRanges", "idpsPrivateRanges", "bypassTrafficSettings")),
    ("gateway", "existingWafPolicyId"),
    ("gateway", "authentication", "subscriptionKeyHeader"),
    ("gateway", "authentication", "entra", "requiredScope"),
    ("gateway", "authentication", "credentialLogin", "identity", "tenantId"),
    *(("gateway", "rateLimits", kind, "overrides") for kind in ("subscriptionKey", "application")),
    *(("monitoring", key) for key in ("auditAllowedIpAddresses", "auditReaders")),
    *(("certificate", key) for key in ("domainNames", "plugin")),
    *(("portalApiKeys", key) for key in ("groups", "verificationSubscriptions")),
)


def _fail(path: str, message: str) -> None:
    # 秘密値を誤って設定へ貼り付けても、エラーメッセージから漏らさない。
    """問題のある項目名と理由だけを例外にし、入力値をエラー出力へ漏らさない。"""
    raise ConfigurationError(f"{path}: {message}")


def _object(value: Any, path: str, allowed: set[str] | None = None) -> dict:
    """辞書型であることと許可したキーだけであることを検査する。"""
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    if allowed is not None:
        unknown = set(value) - allowed
        if unknown:
            _fail(path, "contains unsupported field(s): " + ", ".join(sorted(unknown)))
    return value


def _string(value: Any, path: str, *, maximum: int = 512) -> str:
    """空白・空文字・長さ超過のない文字列を受け取る。"""
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        _fail(path, "must be a non-empty trimmed string")
    return value


def _backend_url_template(value: Any, path: str, parameter_names: set[str]) -> str:
    """公開APIの相対パス書換えを検査する。認証情報、別ホスト、APIM式の混入を拒否する。"""
    template = _string(value, path, maximum=1000)
    decoded = unquote(template)
    if (not template.startswith("/") or template.startswith("//") or decoded.startswith("//") or re.search(r"%(?![0-9A-Fa-f]{2})", template)
            or "://" in decoded or "\\" in decoded or "#" in decoded
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in decoded)
            or any(character in decoded for character in ('<', '>', '"', "'", '*'))
            or any(marker in decoded for marker in ("@(", "@{", "{{", "}}"))):
        _fail(path, "must be a public relative path/query without authority, policy syntax or unsafe characters")
    try:
        parsed = urlparse(template)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        _fail(path, "must be a valid public relative path/query")
    if parsed.scheme or parsed.netloc or parsed.fragment or "@" in unquote(parsed.path):
        _fail(path, "must not contain a scheme, authority, user information or fragment")
    # 公開設定のURLに秘密を入れない。資格情報は保護したヘッダーや接続設定で渡す。
    credential_names = {re.sub(r"[-_]", "", key.lower()) for key in SECRET_VALUE_KEYS}
    credential_names |= {"authorization", "credential", "credentials", "sig", "signature", "apikey", "subscriptionkey", "ocpapimsubscriptionkey", "refreshtoken", "idtoken"}
    if any(re.sub(r"[-_]", "", name.lower()) in credential_names for name, _ in query):
        _fail(path, "must not contain credential query parameters")
    rewritten_parameters = re.findall(r"\{([^{}]+)\}", template)
    remaining = re.sub(r"\{[^{}]+\}", "", template)
    if set(rewritten_parameters) - parameter_names or "{" in remaining or "}" in remaining:
        _fail(path, "may use only parameters declared by the public operation route")
    return template


def _bool(value: Any, path: str) -> bool:
    """true/falseを受け取り、数値や文字列による曖昧な指定を拒否する。"""
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")
    return value


def _integer(value: Any, path: str, minimum: int, maximum: int) -> int:
    """boolを数値として認めず、指定範囲の整数だけを受け取る。"""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(path, f"must be an integer from {minimum} to {maximum}")
    return value


def _choice(value: Any, path: str, choices: tuple[str, ...]) -> str:
    """Azureや本資材が対応する選択肢の中から1つを受け取る。"""
    if value not in choices:
        _fail(path, "must be one of " + ", ".join(choices))
    return value


def _query_observation_reference(value: Any, path: str) -> str:
    """操作・受付の観測ラベルを検査する。URLやサービス名から推測しない。"""
    identifier = _string(value, path, maximum=64)
    if not QUERY_OBSERVATION_ID.fullmatch(identifier) or identifier in ("none", "unknown"):
        _fail(path, "must be an ASCII observation label of at most 64 characters, excluding none and unknown")
    return identifier


def _guid(value: Any, path: str) -> str:
    """プレースホルダーの全ゼロIDを拒否し、正規化した実GUIDを返す。"""
    value = _string(value, path, maximum=36)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _fail(path, "must be a GUID")
    if str(parsed) != value.lower() or parsed.int == 0:
        _fail(path, "must be a non-zero canonical GUID")
    return str(parsed)


def _list(value: Any, path: str, *, minimum: int = 0) -> list:
    """必要な要素数を満たす配列だけを受け取る。"""
    if not isinstance(value, list) or len(value) < minimum:
        _fail(path, f"must be an array with at least {minimum} item(s)")
    return value


def _strings(value: Any, path: str, *, minimum: int = 0) -> list[str]:
    """重複のない文字列配列を検査し、要素位置を示してエラーにする。"""
    items = _list(value, path, minimum=minimum)
    result = [_string(item, f"{path}[{i}]") for i, item in enumerate(items)]
    if len(set(result)) != len(result):
        _fail(path, "must not contain duplicates")
    return result


def _entra_authentication(value: Any) -> dict:
    """同じAPIの委任認証を完全検査する。公開設定に有効・無効の切替を持たせない。"""
    path = "gateway.authentication.entra"
    settings = _object(value, path, {
        "audience", "requiredScope", "userLimits", "obo",
    })
    for name in ("audience", "requiredScope", "userLimits"):
        if name not in settings:
            _fail(path + "." + name, "must be specified for delegated authentication")
    settings["audience"] = _guid(settings.get("audience"), path + ".audience")
    if "obo" in settings:
        obo = _object(settings["obo"], path + ".obo", {"keyId", "certificateThumbprint"})
        _string(obo.get("keyId"), path + ".obo.keyId")
        thumbprint = _string(obo.get("certificateThumbprint"), path + ".obo.certificateThumbprint")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", thumbprint):
            _fail(path + ".obo.certificateThumbprint", "must be the SHA-256 hex digest of the public certificate")
    scope = _string(settings.get("requiredScope"), path + ".requiredScope", maximum=128)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,127}", scope):
        _fail(path + ".requiredScope", "must be one delegated scope name without a resource prefix")
    limits_path = path + ".userLimits"
    limits = _object(settings.get("userLimits"), limits_path, {"requestRate", "tokensPerMinute", "tokenQuota"})
    for name in ("requestRate", "tokensPerMinute", "tokenQuota"):
        if name not in limits:
            _fail(limits_path + "." + name, "must be specified; use null to disable this limit")
    if limits["requestRate"] is not None:
        rate = _object(limits["requestRate"], limits_path + ".requestRate", {"calls", "renewalPeriodSeconds"})
        _integer(rate.get("calls"), limits_path + ".requestRate.calls", 1, 1000000)
        _integer(rate.get("renewalPeriodSeconds"), limits_path + ".requestRate.renewalPeriodSeconds", 1, 300)
    if limits["tokensPerMinute"] is not None:
        _integer(limits["tokensPerMinute"], limits_path + ".tokensPerMinute", 1, 2147483647)
    if limits["tokenQuota"] is not None:
        quota = _object(limits["tokenQuota"], limits_path + ".tokenQuota", {"tokens", "period"})
        _integer(quota.get("tokens"), limits_path + ".tokenQuota.tokens", 1, 2147483647)
        _choice(quota.get("period"), limits_path + ".tokenQuota.period", ("Hourly", "Daily", "Weekly", "Monthly", "Yearly"))
    return settings


def _cidrs(value: Any, path: str, *, minimum: int = 0) -> list[str]:
    """IPv4のネットワーク範囲を検査する。CIDRのホスト部に値がある曖昧な指定を拒否する。"""
    result = _strings(value, path, minimum=minimum)
    for i, item in enumerate(result):
        try:
            parsed = ipaddress.ip_network(item, strict=True)
        except ValueError:
            _fail(f"{path}[{i}]", "must be an IPv4 address or network without host bits")
        if parsed.version != 4:
            _fail(f"{path}[{i}]", "must be IPv4")
    return result


def _host(value: Any, path: str, *, wildcard: bool = False) -> str:
    """スキーム・ポート・パスを含まないDNS名を検査し、小文字へ統一する。"""
    value = _string(value, path, maximum=253).lower()
    check = value[2:] if wildcard and value.startswith("*.") else value
    labels = check.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        _fail(path, "must be a DNS hostname without scheme, path or port")
    try:
        ipaddress.ip_address(check)
    except ValueError:
        pass
    else:
        _fail(path, "must be a DNS hostname rather than an IP address")
    return value


def _https(value: Any, path: str) -> str:
    """資格情報・クエリ・XMLを混ぜ込めないHTTPS URLを受け取る。"""
    value = _string(value, path)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or any(ch in value for ch in '<>"\'&{}') or re.search(r"\s", value):
        _fail(path, "must be an HTTPS URL without credentials, query, fragment or XML metacharacters")
    return value


def _audience(value: Any, path: str) -> str:
    """トークンの受信先を示すGUIDまたはapi/https URIを検査する。"""
    value = _string(value, path)
    if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        return _guid(value, path)
    parsed = urlparse(value)
    if parsed.scheme not in ("api", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or any(ch in value for ch in '<>"\'&{}') or re.search(r"\s", value):
        _fail(path, "must be an application GUID or api:// / https:// audience without XML metacharacters")
    return value


def _secret_path(value: Any, path: str, base: Path) -> str:
    """設定を宣言したファイルの場所を基準に、参照先を絶対パスへ揃える。"""
    raw = _string(value, path, maximum=4096)
    expanded = Path(raw).expanduser()
    return str((base / expanded).resolve() if not expanded.is_absolute() else expanded.resolve())


def _no_secrets(value: Any, path: str = "configuration") -> None:
    """入れ子を含めて公開設定を調べ、秘密値の直書きに使うキーを拒否する。"""
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SECRET_VALUE_KEYS:
                _fail(f"{path}.{key}", "secret values must be supplied through protected files")
            _no_secrets(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for i, nested in enumerate(value):
            _no_secrets(nested, f"{path}[{i}]")


def _principals(value: Any, path: str) -> list[dict]:
    """RBAC対象の公開ID・種類・重複を検査する。"""
    result = _list(value, path)
    seen = set()
    for i, principal in enumerate(result):
        p = f"{path}[{i}]"
        _object(principal, p, {"principalId", "principalType"})
        principal["principalId"] = _guid(principal.get("principalId"), p + ".principalId")
        _choice(principal.get("principalType"), p + ".principalType", ("User", "Group", "ServicePrincipal"))
        if principal["principalId"] in seen:
            _fail(path, "contains duplicate principals")
        seen.add(principal["principalId"])
    return result


def _network(raw: Any, *, audit_evidence_enabled: bool = False) -> dict:
    """VNet・サブネット・NSG・ルート・FW設定を検査し、省略可能な標準値を補う。

    アドレス範囲の包含と重なり、ゾーン、DNS、Premiumの検査設定など、
    個々の値だけでは分からない組合せもここで確認する。
    """
    n = _object(copy.deepcopy(raw), "network", {
        "addressSpaces", "subnetPrefixes", "bgpRoutePropagation", "firewall",
        "appGatewayZones", "dnsTtl",
    })
    spaces = _cidrs(n.get("addressSpaces"), "network.addressSpaces", minimum=1)
    subnet_kinds = (*SUBNET_KINDS, "certificate", "audit")
    prefixes = _object(n.get("subnetPrefixes"), "network.subnetPrefixes", set(subnet_kinds))
    if not set(SUBNET_KINDS[:-1]) <= prefixes.keys():
        _fail("network.subnetPrefixes", "must define agw, firewall, apim, pep, management and aca")
    if audit_evidence_enabled and "audit" not in prefixes:
        _fail("network.subnetPrefixes.audit", "must specify a dedicated /27 or larger for audit evidence")
    # まずVNet内の住所の区切りを検査する。各サブネットが個別に正しいCIDRでも、
    # 互いに重なっていたりVNet外だったりすれば、後続リソースを配置できない。
    networks = [ipaddress.ip_network(item) for item in spaces]
    if any(left.overlaps(right) for i, left in enumerate(networks) for right in networks[i + 1:]):
        _fail("network.addressSpaces", "address spaces overlap")
    subnet_networks = []
    for key in (kind for kind in subnet_kinds if kind in prefixes):
        item = _cidrs([prefixes[key]], "network.subnetPrefixes." + key, minimum=1)[0]
        subnet = ipaddress.ip_network(item)
        maximum_prefix = {"agw": 24, "pep": 24, "aca": 27, "broker": 27, "certificate": 27, "audit": 27}.get(key, 26)
        if subnet.prefixlen > maximum_prefix or not any(subnet.subnet_of(parent) for parent in networks):
            _fail("network.subnetPrefixes." + key, "must fit the address space and meet the designed subnet size")
        subnet_networks.append(subnet)
    if any(left.overlaps(right) for i, left in enumerate(subnet_networks) for right in subnet_networks[i + 1:]):
        _fail("network.subnetPrefixes", "subnets overlap")
    for key in ("certificate", "broker"):
        if key in prefixes:
            continue
        free = networks
        for used in subnet_networks:
            free = [part for candidate in free for part in
                    (candidate.address_exclude(used) if used.subnet_of(candidate) else [candidate])]
        available = sorted(part for part in free if part.prefixlen <= 27)
        if not available:
            _fail("network.subnetPrefixes." + key, "requires a dedicated /27 or larger inside the VNet address space")
        if key == "broker":
            block = next((item for item in available if item.prefixlen <= 26), available[0])
            prefix_len = max(26, block.prefixlen)
        else:
            block = available[0]
            prefix_len = 27
        selected = next(block.subnets(new_prefix=prefix_len))
        subnet_networks.append(selected)
        prefixes[key] = str(selected)
    # 公開設定ではtrue=伝播、false=非伝播と読む。ARMのdisable指定への反転は
    # Bicep側で行い、利用者が否定の否定を読み解く必要をなくす。
    routes = _object(n.get("bgpRoutePropagation", {}), "network.bgpRoutePropagation", {"agw", "apim", "management"})
    n["bgpRoutePropagation"] = {key: _bool(routes.get(key, True), "network.bgpRoutePropagation." + key) for key in ("agw", "apim", "management")}
    _integer(n.setdefault("dnsTtl", 300), "network.dnsTtl", 1, 86400)
    _strings(n.setdefault("appGatewayZones", ["1", "2", "3"]), "network.appGatewayZones")
    f = _object(n.setdefault("firewall", {}), "network.firewall", {
        "sku", "idpsMode", "threatIntelMode", "zones", "idpsPrivateRanges", "signatureOverrides",
        "bypassTrafficSettings", "snatPrivateRanges",
    })
    _choice(f.setdefault("sku", "Premium"), "network.firewall.sku", ("Premium",))
    _choice(f.setdefault("threatIntelMode", "Alert"), "network.firewall.threatIntelMode", ("Alert", "Deny", "Off"))
    _choice(f.setdefault("idpsMode", "Deny"), "network.firewall.idpsMode", ("Alert", "Deny"))
    _strings(f.setdefault("zones", []), "network.firewall.zones")
    for key in ("idpsPrivateRanges", "signatureOverrides", "bypassTrafficSettings", "snatPrivateRanges"):
        if key in f:
            _list(f[key], "network.firewall." + key)
    return n


def _normalize_api_backend(api: dict, p: str, api_type: str, entra_tenant_id: str):
    """Validate fixed downstream routing and authentication without reading credentials."""
    backend = _choice(api.get("backendService"), p + ".backendService", ("foundryOpenAi", "foundryCognitive", "foundryServices", "external"))
    if "backendPath" in api:
        _fail(p + ".backendPath", "external services use backendUrl with its fixed base path")
    if backend == "external":
        backend_url = api.get("backendUrl")
        if api_type == "websocket" and isinstance(backend_url, str) and backend_url.startswith("wss://"):
            _https("https://" + backend_url[6:], p + ".backendUrl")
        else:
            _https(backend_url, p + ".backendUrl")
    elif "backendUrl" in api:
        _fail(p + ".backendUrl", "is derived from the deployed service endpoint; omit it")
    _audience(api.get("backendAudience"), p + ".backendAudience")
    if backend in ("foundryOpenAi", "foundryCognitive") and api["backendAudience"] != "https://cognitiveservices.azure.com":
        _fail(p + ".backendAudience", "Foundry Models/Language uses the Cognitive Services resource audience")
    # 入口での利用者認証と、APIMからバックエンドへの認証は別である。
    # FoundryはMI認証を基本にし、外部のキー認証先はKey Vault参照だけを許す。
    backend_auth = _object(api.setdefault("backendAuthentication", {"mode": "managedIdentity"}), p + ".backendAuthentication", {"mode", "headerName", "secretIdentifier", "resourceId", "headerValuePrefix"})
    auth_mode = _choice(backend_auth.get("mode"), p + ".backendAuthentication.mode", ("managedIdentity", "none", "keyVaultHeader", "speechEntra"))
    if auth_mode == "none" and backend != "external":
        _fail(p + ".backendAuthentication.mode", "anonymous backend mode requires an explicitly registered external service")
    if auth_mode == "keyVaultHeader":
        if backend != "external":
            _fail(p + ".backendAuthentication.mode", "core Foundry disables local key authentication; a registered external key backend is required")
        header_name = _string(backend_auth.get("headerName"), p + ".backendAuthentication.headerName", maximum=128)
        if not re.fullmatch(r"[A-Za-z0-9-]+", header_name) or header_name.lower() in ("host", "content-length", "content-type", "x-core-l7-ingress"):
            _fail(p + ".backendAuthentication.headerName", "must be a dedicated backend credential header")
        _choice(backend_auth.setdefault("headerValuePrefix", ""), p + ".backendAuthentication.headerValuePrefix", ("", "Bearer "))
        uri = _https(backend_auth.get("secretIdentifier"), p + ".backendAuthentication.secretIdentifier")
        if not urlparse(uri).hostname.endswith(".vault.azure.net") or not re.fullmatch(r"/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9]+)?", urlparse(uri).path):
            _fail(p + ".backendAuthentication.secretIdentifier", "must reference a Key Vault secret URI, never a secret value")
    elif auth_mode == "speechEntra":
        if api["backendAudience"] != "https://cognitiveservices.azure.com":
            _fail(p + ".backendAudience", "Speech Entra requires the fixed Cognitive Services token audience")
        rid = _string(backend_auth.get("resourceId"), p + ".backendAuthentication.resourceId")
        if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.CognitiveServices/accounts/[A-Za-z0-9-]+", rid):
            _fail(p + ".backendAuthentication.resourceId", "must identify the target Speech/Foundry resource")
    allowed_auth_fields = {"mode"} | ({"headerName", "secretIdentifier", "headerValuePrefix"} if auth_mode == "keyVaultHeader" else {"resourceId"} if auth_mode == "speechEntra" else set())
    if set(backend_auth) != allowed_auth_fields:
        _fail(p + ".backendAuthentication", "contains missing or mode-incompatible fields")
    headers = _object(api.setdefault("backendHeaders", {}), p + ".backendHeaders")
    for header_name, header_value in headers.items():
        if (not re.fullmatch(r"[A-Za-z0-9-]+", header_name) or re.search(r"authorization|cookie|key|token|secret|^host$|content-length|x-core-l7-ingress", header_name, re.I)
                or not isinstance(header_value, str) or not header_value or len(header_value) > 1024 or re.search(r"[\r\n]|\{\{|@\(", header_value)):
            _fail(p + ".backendHeaders", "must contain only fixed non-secret provider headers without expressions")
    return backend, backend_auth, auth_mode


def _normalize_operation_constraint(constraint: dict, api: dict, operation: dict,
                                    deployed_models: set[str], q: str, *, allow_model_settings: bool = True):
    """操作の基本設定とモデル別の差分を、同じ契約で検査する。"""
    p, op_name = q, operation["name"]
    backend, api_type, mode = api["backendService"], api["apiType"], api["authorization"]["mode"]
    _object(constraint, q, {"kind", "parameter", "allowedModels", "maxRequestBytes", "maxOutputTokens", "maxOutputTokensByModel", "outputTokenLimitKinds", "initialOutputTokens", "outputTokenParameter", "modelVersion", "allowedLanguages", "allowedWarningCodes", "bodyFormat", "requestBodySizeCheck", "contentTypes", "allowedMethods", "integerLimits", "arrayLimits", "stringChoices", "model", "sessionTtlSeconds", "publicCallPath", "modelSettings", "backendUrlTemplate", "maxResponseBytes", "apiVersion", "timeoutSeconds", "embeddingDimensionsByModel", "embeddingInputField", "modelAliases"})
    kind = _choice(constraint.get("kind"), q + ".kind", ("registeredOperation", "pathModel", "bodyModel", "textEmbeddings", "rerank", "statelessResponses", "hostedAgentPost", "a2aJsonRpc", "realtimeCreate", "realtimeHangup", "liveCreate", "pii", "documentAnalysis", "documentAnalysisStatus"))
    # モデル別の上流URIは、登録時に検査した固定表としてBicepへ渡します。
    # 本文のモデル名から、そのモデルに対応する転送先を一意に選びます。
    if "backendUrlTemplate" in constraint:
        if allow_model_settings or kind != "bodyModel" or backend != "foundryServices" or "backendUrlTemplate" in operation:
            _fail(q + ".backendUrlTemplate", "requires a Foundry provider JSON model setting with model-owned routing")
        _backend_url_template(constraint["backendUrlTemplate"], q + ".backendUrlTemplate", set())
    if kind == "statelessResponses" and (operation["method"] != "POST" or operation["urlTemplate"] != "/responses" or backend != "foundryOpenAi"):
        _fail(q, "statelessResponses permits only resource Models POST /responses on foundryOpenAi")
    if "backendUrlTemplate" in operation and not ((backend in ("external") and kind in ("registeredOperation", "bodyModel", "pathModel", "hostedAgentPost", "a2aJsonRpc")) or (backend == "foundryOpenAi" and kind == "pathModel") or (backend == "foundryServices" and kind in ("bodyModel", "textEmbeddings", "rerank", "hostedAgentPost"))):
        _fail(p + ".operations." + op_name + ".backendUrlTemplate", "requires an explicit external or Foundry model-provider contract")
    body_format = _choice(constraint.setdefault("bodyFormat", "none" if operation["method"] in ("GET", "HEAD", "DELETE", "OPTIONS") or api_type != "http" else "json"), q + ".bodyFormat", ("json", "binary", "multipart", "text", "none"))
    # 未指定は既存の実サイズ確認を使い、配備データへ既定値を追加しません。
    # サイズ確認のための本文読取りを省くdeclaredは、binary／multipart／textの3形式に限定します。
    if "requestBodySizeCheck" in constraint:
        size_check = _choice(constraint["requestBodySizeCheck"], q + ".requestBodySizeCheck", ("actual", "declared"))
        if size_check == "declared" and body_format not in ("binary", "multipart", "text"):
            _fail(q + ".requestBodySizeCheck", "declared requires a binary, multipart or text body format")
    if kind in ("bodyModel", "textEmbeddings", "rerank", "statelessResponses", "hostedAgentPost", "a2aJsonRpc", "realtimeCreate", "liveCreate", "pii") and body_format != "json":
        _fail(q + ".bodyFormat", "this operation contract requires JSON")
    if api_type != "http" and (kind != "registeredOperation" or body_format != "none"):
        _fail(q, "native protocol authorization must not inspect or transform streamed bodies")
    if body_format in ("binary", "multipart", "text"):
        types = _strings(constraint.get("contentTypes"), q + ".contentTypes", minimum=1)
        if any(not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value) for value in types):
            _fail(q + ".contentTypes", "must list explicit media types without wildcards or parameters")
        if body_format == "multipart" and types != ["multipart/form-data"]:
            _fail(q + ".contentTypes", "multipart uploads require multipart/form-data")
    if kind == "a2aJsonRpc":
        _strings(constraint.get("allowedMethods"), q + ".allowedMethods", minimum=1)
    if kind == "liveCreate":
        if (backend != "foundryOpenAi" or api_type != "http"
                or api["backendAuthentication"]["mode"] != "managedIdentity"
                or operation["method"] != "POST" or operation["urlTemplate"] != "/sessions"
                or operation["templateParameters"] or "backendUrlTemplate" in operation):
            _fail(q, "Live creation requires POST /sessions on the managed-identity Foundry OpenAI backend")
        if constraint.get("model") not in deployed_models:
            _fail(q + ".model", "must reference an explicitly configured Live deployment")
    if kind in ("realtimeCreate", "realtimeHangup"):
        if backend != "foundryOpenAi" or api_type != "http" or operation["method"] != "POST":
            _fail(q, "Realtime session control requires HTTP POST on the registered Foundry OpenAI backend")
        _integer(constraint.get("sessionTtlSeconds"), q + ".sessionTtlSeconds", 60, 3600)
        public_path = _string(constraint.get("publicCallPath"), q + ".publicCallPath")
        if public_path != "/" + api["path"] + "/calls":
            _fail(q + ".publicCallPath", "must equal this API's explicit public /calls route")
        if kind == "realtimeCreate":
            if operation["urlTemplate"] != "/calls" or operation["templateParameters"]:
                _fail(q, "Realtime create must use POST /calls without URL parameters")
            if constraint.get("model") not in deployed_models:
                _fail(q + ".model", "must reference an explicitly configured Realtime model deployment")
            _integer(constraint.get("maxOutputTokens"), q + ".maxOutputTokens", 1, 9223372036854775807)
            if "initialOutputTokens" in constraint:
                _integer(constraint["initialOutputTokens"], q + ".initialOutputTokens", 1, constraint["maxOutputTokens"])
        elif operation["urlTemplate"] != "/calls/{callId}/hangup" or [item["name"] for item in operation["templateParameters"]] != ["callId"] or body_format != "none":
            _fail(q, "Realtime hangup requires POST /calls/{callId}/hangup with no request body")

    for field in ("integerLimits", "arrayLimits", "stringChoices"):
        limits = _object(constraint.setdefault(field, {}), q + "." + field)
        if limits and body_format != "json":
            _fail(q + "." + field, "JSON field limits cannot validate binary, multipart or protocol frames")
        for field_name, limit in limits.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field_name):
                _fail(q + "." + field, "must name top-level JSON fields")
            if field == "integerLimits":
                _object(limit, q + ".integerLimits", {"minimum", "maximum", "required"})
                low = _integer(limit.get("minimum"), q + ".integerLimits.minimum", 0, 9223372036854775807)
                _integer(limit.get("maximum"), q + ".integerLimits.maximum", low, 9223372036854775807)
                _bool(limit.setdefault("required", False), q + ".integerLimits.required")
            elif field == "arrayLimits":
                _integer(limit, q + ".arrayLimits", 1, 2147483647)
            else:
                _strings(limit, q + ".stringChoices", minimum=1)
    if operation["method"] not in ("POST", "PUT", "PATCH"):
        constraint.setdefault("maxRequestBytes", 32768)
    _integer(constraint.get("maxRequestBytes"), q + ".maxRequestBytes", 1, 524288000)
    if kind == "rerank":
        if (backend != "foundryServices" or api_type != "http" or mode != "subscriptionKey"
                or operation["method"] != "POST" or operation["urlTemplate"] != "/rerank"
                or operation["templateParameters"]):
            _fail(q, "rerank requires the registered Foundry JSON POST /rerank model contract")
        documents = constraint["arrayLimits"].get("documents")
        top_n = constraint["integerLimits"].get("top_n", {})
        if (set(constraint["arrayLimits"]) - {"documents"}
                or set(constraint["integerLimits"]) - {"top_n"}
                or (top_n and top_n.get("minimum") != 1)
                or (top_n and documents is not None and top_n.get("maximum", 0) > documents)):
            _fail(q, "rerank permits provider document bounds and optional positive top_n")
        if any(name in constraint for name in ("maxOutputTokens", "maxOutputTokensByModel", "outputTokenLimitKinds", "outputTokenParameter", "initialOutputTokens")):
            _fail(q, "rerank has result counts, not generation output-token limits")
    if kind == "textEmbeddings":
        openai = backend == "foundryOpenAi" and operation["urlTemplate"] == "/embeddings" and "backendUrlTemplate" not in operation
        cohere = backend == "foundryServices" and operation["urlTemplate"] == "/embed" and operation.get("backendUrlTemplate") == "/providers/cohere/v2/embed"
        if (not (openai or cohere) or api_type != "http" or mode != "subscriptionKey"
                or operation["method"] != "POST" or operation["templateParameters"]):
            _fail(q, "textEmbeddings requires a registered text embedding provider POST contract")
        text_field = "input" if openai else "texts"
        constraint.setdefault("embeddingInputField", text_field)
        if constraint["embeddingInputField"] != text_field:
            _fail(q + ".embeddingInputField", "must match the registered embedding provider")
        if (set(constraint["arrayLimits"]) - ({"input"} if openai else {"texts", "embedding_types"})
                or constraint["integerLimits"]):
            _fail(q, "textEmbeddings permits only provider input array bounds")
        if (openai and constraint["stringChoices"]) or (cohere and constraint["stringChoices"] != {"input_type": ["search_document", "search_query", "classification", "clustering"]}):
            _fail(q, "textEmbeddings requires the registered provider purpose choices")
        dimensions = _object(constraint.get("embeddingDimensionsByModel"), q + ".embeddingDimensionsByModel")
        allowed = _strings(constraint.get("allowedModels"), q + ".allowedModels", minimum=1)
        if set(dimensions) != set(allowed):
            _fail(q + ".embeddingDimensionsByModel", "must cover exactly the approved deployment aliases")
        for name, bounds in dimensions.items():
            path = q + ".embeddingDimensionsByModel." + name
            _object(bounds, path, {"default", "maximum", "allowed"})
            maximum = _integer(bounds.get("maximum"), path + ".maximum", 1, 9223372036854775807)
            default = _integer(bounds.get("default"), path + ".default", 1, maximum)
            if cohere and "allowed" not in bounds:
                _fail(path + ".allowed", "Cohere requires explicit evaluated dimension choices")
            if "allowed" in bounds:
                values = _list(bounds["allowed"], path + ".allowed", minimum=1)
                for value in values:
                    _integer(value, path + ".allowed", 1, maximum)
                if len(set(values)) != len(values) or default not in values or maximum not in values:
                    _fail(path + ".allowed", "must contain distinct dimensions including the default and maximum")
        if any(name in constraint for name in ("maxOutputTokens", "maxOutputTokensByModel", "outputTokenLimitKinds", "outputTokenParameter", "initialOutputTokens")):
            _fail(q, "textEmbeddings has vector dimensions, not generation output-token limits")
    elif "embeddingDimensionsByModel" in constraint or "embeddingInputField" in constraint:
        _fail(q + ".embeddingDimensionsByModel", "belongs only to the textEmbeddings contract")
    model_limits = constraint.get("maxOutputTokensByModel")
    if model_limits is not None:
        _object(model_limits, q + ".maxOutputTokensByModel")
        kinds = _object(constraint.get("outputTokenLimitKinds"), q + ".outputTokenLimitKinds")
        websocket = api.get("websocket", {})
        models = constraint.get("allowedModels") or ([constraint["model"]] if constraint.get("model") else websocket.get("allowedModels") or websocket.get("session", {}).get("models") or [websocket.get("queryParameters", {}).get("model")])
        if not model_limits or set(model_limits) != set(models) or set(kinds) != set(model_limits):
            _fail(q + ".maxOutputTokensByModel", "must cover exactly the operation's approved models")
        for name, maximum in model_limits.items():
            limit_kind = _choice(kinds[name], q + ".outputTokenLimitKinds", ("documented", "dynamic", "unsupported", "unverified"))
            if limit_kind == "documented":
                _integer(maximum, q + ".maxOutputTokensByModel", 1, 9223372036854775807)
            elif maximum is not None:
                _fail(q + ".maxOutputTokensByModel", "variable or unsupported limits must not invent a numeric maximum")
        maxima = [value for value in model_limits.values() if value is not None]
        if constraint.get("maxOutputTokens") != (max(maxima) if maxima else None):
            _fail(q + ".maxOutputTokens", "must equal the model-specific envelope")
    elif "outputTokenLimitKinds" in constraint:
        _fail(q + ".outputTokenLimitKinds", "requires model-specific limits")
    if "initialOutputTokens" in constraint and kind != "realtimeCreate":
        _fail(q + ".initialOutputTokens", "is reserved for the initial Realtime session setting")
    if kind == "statelessResponses" or "outputTokenParameter" in constraint or (kind in ("bodyModel", "pathModel") and "chat/completions" in operation["urlTemplate"]):
        if model_limits is None or constraint.get("maxOutputTokens") is not None:
            _integer(constraint.get("maxOutputTokens"), q + ".maxOutputTokens", 1, 9223372036854775807)
        token_parameter = _choice(constraint.get("outputTokenParameter"), q + ".outputTokenParameter", ("max_output_tokens", "max_completion_tokens", "max_tokens"))
        if kind == "statelessResponses" and token_parameter != "max_output_tokens":
            _fail(q + ".outputTokenParameter", "Responses requires max_output_tokens")
    response_route = re.search(r"(?:^|/)responses(?:/|\?|$)", operation["urlTemplate"], flags=re.IGNORECASE)
    if response_route and kind not in ("statelessResponses", "hostedAgentPost"):
        _fail(q + ".kind", "Responses requires the explicit statelessResponses contract")
    if kind == "hostedAgentPost" and (operation["method"] != "POST" or not operation["urlTemplate"].endswith("/responses") or backend not in ("external", "foundryServices")):
        _fail(q, "Hosted Agent initial contract permits only a registered POST responses endpoint")
    if kind == "hostedAgentPost" and (api["backendAudience"] != "https://ai.azure.com"):
        _fail(q, "Hosted Agent requires the registered Agent backend audience contract")
    if kind in ("pathModel", "bodyModel", "textEmbeddings", "rerank", "statelessResponses"):
        allowed = _strings(constraint.get("allowedModels"), q + ".allowedModels", minimum=1)
        if backend in ("foundryOpenAi", "foundryServices") and not set(allowed) <= deployed_models:
            _fail(q + ".allowedModels", "must reference explicitly configured model deployments")
    # Aliases are operation-owned names for existing deployments, never new
    # permissions, deployments or a chain of routing instructions.
    if "modelAliases" in constraint:
        if not allow_model_settings or api_type != "http" or backend != "foundryOpenAi" or kind not in ("bodyModel", "statelessResponses") or body_format != "json":
            _fail(q + ".modelAliases", "requires a Foundry OpenAI JSON model operation")
        aliases = _object(constraint["modelAliases"], q + ".modelAliases")
        if not 1 <= len(aliases) <= 32:
            _fail(q + ".modelAliases", "must contain 1 through 32 exact aliases")
        for alias, target in aliases.items():
            if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", alias):
                _fail(q + ".modelAliases", "must use exact safe model identifiers")
            if alias in deployed_models or alias in allowed:
                _fail(q + ".modelAliases", "aliases must not shadow deployed or allowed models")
            if not isinstance(target, str) or target not in allowed or target not in deployed_models or target in aliases:
                _fail(q + ".modelAliases", "targets must be directly allowed existing deployments; alias chains are prohibited")
    if kind == "pathModel":
        param = _string(constraint.get("parameter"), q + ".parameter")
        if param not in {item["name"] for item in operation["templateParameters"]}:
            _fail(q + ".parameter", "must name a URL template parameter")
        if body_format == "multipart" and ("backendUrlTemplate" not in operation or "{" + param + "}" not in operation["backendUrlTemplate"]):
            _fail(q, "multipart model calls must bind the approved deployment in the upstream route")
    if kind == "pii":
        if backend != "foundryCognitive" or operation["name"] != "pii_redact_text" or operation["method"] != "POST" or operation["urlTemplate"] != "/redact" or api["path"] != "tools/pii/v1":
            _fail(q, "PII requires the fixed Tool Gateway POST /tools/pii/v1/redact contract")
        if constraint.get("modelVersion") in (None, "", "latest"):
            _fail(q + ".modelVersion", "must pin an evaluated PII model version")
        _string(constraint["modelVersion"], q + ".modelVersion")
        _strings(constraint.setdefault("allowedLanguages", []), q + ".allowedLanguages")
        if constraint["maxRequestBytes"] > 1000000:
            _fail(q + ".maxRequestBytes", "must fit the Language provider's 1 MB native request ceiling")
        _strings(constraint.setdefault("allowedWarningCodes", []), q + ".allowedWarningCodes")

    if kind in ("documentAnalysis", "documentAnalysisStatus"):
        if (backend != "foundryCognitive" or api_type != "http" or mode != "subscriptionKey"
                or api["backendAuthentication"]["mode"] != "managedIdentity"
                or api["name"] != "document-analysis-v1" or api["path"] != "tools/documents/v1"
                or "backendUrlTemplate" in operation or "backendOverride" in operation):
            _fail(q, "Document Intelligence requires the fixed managed-identity document API")
        # Documentのモデル利用可否は提供元へ任せ、Gatewayの受付一覧を要求しません。
        # 公開スキーマの対応版を両操作へ補完し、明示された別版は設定不一致にします。
        if constraint.setdefault("apiVersion", DOCUMENT_API_VERSION) != DOCUMENT_API_VERSION:
            _fail(q + ".apiVersion", "must use the published Document Intelligence GA API")
        if kind == "documentAnalysis":
            if (operation["name"] != "analyze_document" or operation["method"] != "POST"
                    or operation["urlTemplate"] != "/analyze" or operation["templateParameters"] or body_format != "binary"):
                _fail(q, "Document submission requires POST /analyze with binary size-check handling")
            # binaryはサイズ確認の処理区分です。原本と提供元のJSON入力を
            # 解釈せず転送し、contentTypesはAPI仕様用の形式情報として扱います。
            _integer(constraint.get("maxRequestBytes"), q + ".maxRequestBytes", 1, 500000000)
        elif (operation["name"] != "get_document_operation" or operation["method"] != "GET"
                or operation["urlTemplate"] != "/operations/{operationId}"
                or [item["name"] for item in operation["templateParameters"]] != ["operationId"] or body_format != "none"):
            _fail(q, "Document status requires the owner-verified GET /operations/{operationId} contract")
        _integer(constraint.get("maxResponseBytes"), q + ".maxResponseBytes", 1, 524288000)
        if "timeoutSeconds" in constraint:
            _fail(q + ".timeoutSeconds", "document analysis is asynchronous; no Gateway processing deadline is applied")
    elif any(field in constraint for field in ("maxResponseBytes", "apiVersion", "timeoutSeconds")):
        _fail(q, "document response bounds and API version belong only to documentAnalysis")

    if "modelSettings" not in constraint:
        return
    if not allow_model_settings or api_type != "http" or kind not in ("bodyModel", "statelessResponses") or body_format != "json":
        _fail(q + ".modelSettings", "requires a non-recursive JSON model operation")
    settings = _object(constraint["modelSettings"], q + ".modelSettings")
    # 許可モデルは基本設定が管理します。この表は設定の差分だけを持ち、
    # 未指定モデルへは基本設定を使います。入れ子の制限表は項目単位で置換します。
    override_fields = {"kind", "parameter", "maxRequestBytes", "maxOutputTokens", "initialOutputTokens", "outputTokenParameter", "modelVersion", "allowedLanguages", "allowedWarningCodes", "bodyFormat", "contentTypes", "allowedMethods", "integerLimits", "arrayLimits", "stringChoices", "model", "sessionTtlSeconds", "publicCallPath", "backendUrlTemplate", "settingId"}
    base = {key: copy.deepcopy(value) for key, value in constraint.items() if key not in ("modelSettings", "modelAliases")}
    base_bytes = constraint["maxRequestBytes"]
    for model, entry in settings.items():
        if not isinstance(model, str) or model not in constraint["allowedModels"]:
            _fail(q + ".modelSettings", "must identify an allowed model in this operation")
        label = q + ".modelSettings." + model
        _object(entry, label, override_fields)
        if "settingId" in entry:
            setting_id = _string(entry["settingId"], label + ".settingId", maximum=64)
            if not API_NAME.fullmatch(setting_id):
                _fail(label + ".settingId", "must be a setting identifier")
        item = {**copy.deepcopy(base), **{key: copy.deepcopy(value) for key, value in entry.items() if key != "settingId"}}
        if item.get("kind") != kind or item.get("bodyFormat", "json") != "json":
            _fail(label, "must retain this operation's JSON model contract")
        # 検査用だけに能力表を当該モデルへ絞ります。配備データには基本表を
        # 一度だけ保存し、モデル別差分へ全モデルの表を複製しません。
        item["allowedModels"] = [model]
        if "maxOutputTokensByModel" in item:
            item["maxOutputTokensByModel"] = {model: item["maxOutputTokensByModel"][model]}
            item["outputTokenLimitKinds"] = {model: item["outputTokenLimitKinds"][model]}
            if "maxOutputTokens" not in entry:
                item["maxOutputTokens"] = item["maxOutputTokensByModel"][model]
        _normalize_operation_constraint(item, api, operation, deployed_models, label, allow_model_settings=False)
        for field in set(entry) - {"settingId"}:
            entry[field] = copy.deepcopy(item[field])
    if any("backendUrlTemplate" in entry for entry in settings.values()) and any(
            "backendUrlTemplate" not in settings.get(model, {}) for model in constraint["allowedModels"]):
        _fail(q + ".modelSettings", "each allowed model must specify its fixed upstream route when model routing is used")
    # モデル選択前には最大の本文上限を使います。拡大後も、もとの基本上限を
    # 使うモデルには差分を補い、モデルごとの受付上限を変えません。
    constraint["maxRequestBytes"] = max([base_bytes] + [entry.get("maxRequestBytes", base_bytes) for entry in settings.values()])
    if constraint["maxRequestBytes"] != base_bytes:
        for model in constraint["allowedModels"]:
            settings.setdefault(model, {}).setdefault("maxRequestBytes", base_bytes)


def _apis(raw: Any, entra_tenant_id: str, deployed_models: set[str], gateway_origin: str,
          gateway_authentication: dict) -> list[dict]:
    """APIの経路・プロトコル・バックエンド・認証定義をまとめて検査する。

    重複する公開パス、未定義モデル、権限や認証方式の矛盾を、Azureへ公開する前に
    検出する。戻り値には後続Bicepで使う公開情報だけを含める。
    """
    result = _list(copy.deepcopy(raw), "apiDefinitions")
    # 空配列はAPI登録コマンドが標準一式へ展開する。構築用の検査では空のまま扱う。
    names, paths, subscription_ids = set(), set(), {}
    for i, api in enumerate(result):
        p = f"apiDefinitions[{i}]"
        _object(api, p, {"name", "displayName", "path", "backendService", "backendUrl", "backendPath", "backendAudience", "productId", "released", "subscriptionKeyHeader", "operations", "keySubscriptions", "authorization", "mcp", "credentialConnection", "apiType", "backendAuthentication", "backendHeaders", "mcpProxy", "websocket", "serviceClass"})
        _bool(api.setdefault("released", False), p + ".released")
        api_type = _choice(api.setdefault("apiType", "http"), p + ".apiType", ("http", "websocket", "mcp"))
        name = _string(api.get("name"), p + ".name", maximum=80)
        if not API_NAME.fullmatch(name) or name.casefold() in names or name.casefold() in ("credential-manager-login", "credential-manager-browser"):
            _fail(p + ".name", "must be a unique API identifier")
        names.add(name.casefold())
        service_class = _choice(api.setdefault("serviceClass", "sample" if name.startswith("sample-") else "platform"), p + ".serviceClass", ("platform", "sample"))
        if name.startswith("sample-") and service_class != "sample":
            _fail(p + ".serviceClass", "sample-prefixed APIs must be classified as sample")
        _string(api.get("displayName"), p + ".displayName")
        path = api.get("path")
        path = _string(path, p + ".path", maximum=400)
        if path.startswith("/") or "?" in path or path.casefold() in paths or any(segment in (".", "..") for segment in path.split("/")):
            _fail(p + ".path", "must be a unique relative API prefix")
        if path.casefold() == "auth" or path.casefold().startswith("auth/") or path.casefold() == ".well-known/oauth-protected-resource" or path.casefold().startswith(".well-known/oauth-protected-resource/"):
            _fail(p + ".path", "is reserved for the built-in authentication routes")
        paths.add(path.casefold())
        backend, backend_auth, auth_mode = _normalize_api_backend(api, p, api_type, entra_tenant_id)
        product = _string(api.get("productId"), p + ".productId", maximum=80)
        if not API_NAME.fullmatch(product):
            _fail(p + ".productId", "must be a valid API product identifier")
        if product.casefold() == "released-apis":
            _fail(p + ".productId", "released-apis is reserved; use released=true to publish this API")
        header = _string(api.setdefault("subscriptionKeyHeader", gateway_authentication["subscriptionKeyHeader"]), p + ".subscriptionKeyHeader", maximum=128)
        if not re.fullmatch(r"[A-Za-z0-9-]+", header) or header.lower() in ("authorization", "host", "content-type"):
            _fail(p + ".subscriptionKeyHeader", "must be a dedicated HTTP header name")
        if header.casefold() != gateway_authentication["subscriptionKeyHeader"].casefold():
            _fail(p + ".subscriptionKeyHeader", "must match gateway.authentication.subscriptionKeyHeader")
        api["subscriptionKeyHeader"] = gateway_authentication["subscriptionKeyHeader"]
        # APIの各操作は経路・HTTP方式を明示する。ワイルドカードですべて中継せず、
        # 公開したい操作と、その後の認可条件を1対1で照合できるようにする。
        operations = _list(api.get("operations"), p + ".operations", minimum=1)
        op_names, op_routes = set(), set()
        for j, operation in enumerate(operations):
            q = f"{p}.operations[{j}]"
            _object(operation, q, {"name", "displayName", "method", "urlTemplate", "templateParameters", "request", "backendUrlTemplate", "staticResponse", "backendOverride", "queryObservation", "inputContract"})
            if "queryObservation" in operation:
                if api_type != "http":
                    _fail(q + ".queryObservation", "belongs to an HTTP operation; native MCP uses its endpoint setting")
                _query_observation_reference(operation["queryObservation"], q + ".queryObservation")
            op_name = _string(operation.get("name"), q + ".name", maximum=80)
            if not API_NAME.fullmatch(op_name) or op_name.casefold() in {value.casefold() for value in op_names}:
                _fail(q + ".name", "must be a unique operation identifier")
            op_names.add(op_name)
            _string(operation.get("displayName"), q + ".displayName")
            _choice(operation.get("method"), q + ".method", ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"))
            template = _string(operation.get("urlTemplate"), q + ".urlTemplate")
            if not template.startswith("/") or "*" in template or "://" in template:
                _fail(q + ".urlTemplate", "must be an explicit relative operation route without wildcard")
            route_key = (operation["method"], template)
            if route_key in op_routes:
                _fail(q + ".urlTemplate", "duplicates an operation method and route")
            op_routes.add(route_key)
            if "backendOverride" in operation:
                override = _object(operation["backendOverride"], q + ".backendOverride", {"backendService", "backendUrl", "backendAudience", "backendAuthentication", "backendHeaders"})
                if not override or api_type != "http" or "credentialConnection" in api or auth_mode == "keyVaultHeader":
                    _fail(q + ".backendOverride", "requires an explicit HTTP backend without API-scoped credential/secret exchange")
                effective = copy.deepcopy(api)
                effective.update(copy.deepcopy(override))
                if effective["backendService"] != "external":
                    if "backendUrl" in override:
                        _fail(q + ".backendOverride.backendUrl", "is derived from the selected Core service")
                    effective.pop("backendUrl", None)
                _, effective_auth, effective_mode = _normalize_api_backend(effective, q + ".backendOverride", "http", entra_tenant_id)
                if effective_mode not in ("managedIdentity", "speechEntra", "none"):
                    _fail(q + ".backendOverride", "operation overrides do not support secret-bearing authentication")
                operation["backendOverride"] = {key: effective[key] for key in ("backendService", "backendAudience", "backendAuthentication", "backendHeaders")}
                if effective["backendService"] == "external":
                    operation["backendOverride"]["backendUrl"] = effective["backendUrl"]
            params = _list(operation.get("templateParameters"), q + ".templateParameters")
            param_names = [_string(_object(item, q + ".templateParameters").get("name"), q + ".templateParameters.name") for item in params]
            if len(set(param_names)) != len(param_names) or set(param_names) != set(re.findall(r"\{([^{}]+)\}", template)):
                _fail(q + ".templateParameters", "must match all URL template parameters exactly")
            if "backendUrlTemplate" in operation:
                _backend_url_template(operation["backendUrlTemplate"], q + ".backendUrlTemplate", set(param_names))
            if "staticResponse" in operation:
                response = _object(operation["staticResponse"], q + ".staticResponse", {"body", "contentType"})
                if operation["method"] != "GET" or response.get("contentType") != "application/json" or not isinstance(response.get("body"), dict):
                    _fail(q + ".staticResponse", "requires a GET operation with a fixed JSON object (for example an Agent Card)")
                if any(marker in json.dumps(response["body"]) for marker in ("{{", "@(", "@{")):
                    _fail(q + ".staticResponse", "must not embed policy expressions or named-value references")
        # 共通の購読キーで認可した後、APIごとの操作制約を検査する。
        auth = _object(api.get("authorization"), p + ".authorization",
                       {"mode", "operationConstraints"})
        mode = _choice(auth.get("mode"), p + ".authorization.mode", ("subscriptionKey",))
        # 操作ごとの入力サイズ・モデル・出力量などを検査する。認可済みの相手でも、
        # 別モデルや想定外の操作へ抜けられないよう全操作に制約を必須にする。
        constraints = _object(auth.get("operationConstraints"), p + ".authorization.operationConstraints")
        if set(constraints) != op_names:
            _fail(p + ".authorization.operationConstraints", "must constrain every operation exactly")
        for op_name, constraint in constraints.items():
            operation = next(item for item in operations if item["name"] == op_name)
            effective_api = dict(api, **operation.get("backendOverride", {}))
            _normalize_operation_constraint(constraint, effective_api, operation, deployed_models,
                                            p + ".authorization.operationConstraints." + op_name)
            try:
                validate_input_contract(operation, api_type)
            except ValueError as error:
                _fail(p + ".operations." + op_name + ".inputContract", str(error))
        # 通話の作成と終了は同じ経路・期限の対にする。作成だけ公開して
        # 終了操作が欠ける設定は受け付けない。
        realtime = [item for item in constraints.values() if item["kind"] in ("realtimeCreate", "realtimeHangup")]
        if realtime and (sorted(item["kind"] for item in realtime) != ["realtimeCreate", "realtimeHangup"] or len({(item["sessionTtlSeconds"], item["publicCallPath"]) for item in realtime}) != 1):
            _fail(p + ".authorization.operationConstraints", "Realtime requires one create and one hangup contract with identical lifetime and public path")
        if "mcp" in api:
            if api_type != "http":
                _fail(p + ".mcp", "REST-generated MCP belongs to an HTTP API, not a protocol proxy")
            mcp = _object(api["mcp"], p + ".mcp", {"name", "path", "displayName", "operationNames", "queryObservation"})
            if "queryObservation" in mcp:
                _query_observation_reference(mcp["queryObservation"], p + ".mcp.queryObservation")
            _string(mcp.get("displayName"), p + ".mcp.displayName")
            if not API_NAME.fullmatch(_string(mcp.get("name"), p + ".mcp.name", maximum=80)) or mcp["name"].casefold() in names or mcp["name"].casefold() in ("credential-manager-login", "credential-manager-browser"):
                _fail(p + ".mcp.name", "must be a distinct API identifier")
            names.add(mcp["name"].casefold())
            if mcp.get("path") != "tools/pii-v1-mcp" or mcp.get("operationNames") != ["pii_redact_text"] or constraints.get("pii_redact_text", {}).get("kind") != "pii":
                _fail(p + ".mcp", "the initial MCP facade exposes only the registered PII operation")
            if mcp["path"].casefold() in paths:
                _fail(p + ".mcp.path", "must be a distinct API path")
            paths.add(mcp["path"].casefold())
        # 下流OAuth接続は、接続を作成・同意した本人だけに結び付ける。
        # 別の本人やapp-only呼出しへ、保存済みの本人資格情報を貸し出さない。
        if "credentialConnection" in api:
            connection = _object(api["credentialConnection"], p + ".credentialConnection", {"providerName", "mode", "connectionName", "enabled"})
            if not API_NAME.fullmatch(_string(connection.get("providerName"), p + ".credentialConnection.providerName")):
                _fail(p + ".credentialConnection.providerName", "must identify a registered provider")
            connection_mode = _choice(connection.get("mode"), p + ".credentialConnection.mode", ("personal", "service"))
            _bool(connection.get("enabled"), p + ".credentialConnection.enabled")
            if backend != "external" or api_type not in ("http", "mcp") or "mcp" in api or auth_mode != "none":
                _fail(p + ".credentialConnection", "requires an HTTP or MCP external backend with Credential Manager owning backend authentication")
            if connection_mode == "service":
                if not API_NAME.fullmatch(_string(connection.get("connectionName"), p + ".credentialConnection.connectionName")):
                    _fail(p + ".credentialConnection.connectionName", "must identify a fixed service connection")
            elif "connectionName" in connection:
                _fail(p + ".credentialConnection.connectionName", "personal connections are selected by the verified connection session")
        # WebSocket/MCPのフレームへHTTPのJSON検査を適用しない。
        # 接続時の認可と、各プロトコル固有の契約を分けて検査する。
        if api_type != "http":
            if len(operations) != 1 or operations[0]["name"] != "invoke":
                _fail(p + ".operations", "native protocol APIs authorize one synthetic invoke operation; backend protocol definitions own their methods")
            if "mcp" in api or ("credentialConnection" in api and api_type != "mcp"):
                _fail(p, "this protocol does not support a Credential Manager HTTP connection")
        if api_type == "websocket":
            websocket = _object(api.get("websocket"), p + ".websocket", {"queryParameters", "realtimeObserver", "allowedModels", "defaultModel", "session"})
            if set(websocket) not in ({"queryParameters"}, {"realtimeObserver"}, {"queryParameters", "allowedModels", "defaultModel"}, {"queryParameters", "session"}):
                _fail(p + ".websocket", "requires exactly one fixed query or signed Realtime observer contract")
            if backend != "external" or not api["backendUrl"].startswith("wss://"):
                _fail(p + ".backendUrl", "a WebSocket API requires an explicitly registered WSS backend")
            if "queryParameters" in websocket:
                query = _object(websocket["queryParameters"], p + ".websocket.queryParameters")
                if "session" in websocket:
                    # Live/文字起こしのmodel指定は接続後のイベントに含まれます。
                    # この情報はクライアント契約・カタログ用で、queryへ注入しません。
                    session = _object(websocket["session"], p + ".websocket.session", {"kind", "models"})
                    session_kind = _choice(session.get("kind"), p + ".websocket.session.kind", ("live", "transcription"))
                    session_models = _strings(session.get("models"), p + ".websocket.session.models", minimum=1)
                    if not set(session_models) <= deployed_models:
                        _fail(p + ".websocket.session.models", "must reference explicitly configured deployments")
                    expected_path = "/openai/v1/live/sessions" if session_kind == "live" else "/openai/v1/realtime"
                    expected_query = {} if session_kind == "live" else {"intent": "transcription"}
                    if (urlparse(api["backendUrl"]).path != expected_path or query != expected_query
                            or auth_mode != "managedIdentity"
                            or api["backendAudience"] not in ("https://cognitiveservices.azure.com", "https://ai.azure.com")):
                        _fail(p + ".websocket.session", "requires the fixed Azure session endpoint, query and Entra/managed-identity authentication")
                if "allowedModels" in websocket:
                    selected_models = _strings(websocket["allowedModels"], p + ".websocket.allowedModels", minimum=1)
                    if not set(selected_models) <= deployed_models or websocket.get("defaultModel") not in selected_models or "model" in query:
                        _fail(p + ".websocket", "requires deployed allowed models, an approved default and no fixed model query")

                for query_name, query_value in query.items():
                    if query_name not in ("api-version", "model", "language", "voice", "intent") or not isinstance(query_value, str) or not query_value or len(query_value) > 128 or re.search(r"[^A-Za-z0-9_.-]", query_value):
                        _fail(p + ".websocket.queryParameters", "must contain only fixed, public provider parameters; credentials and dynamic targets are forbidden")
            else:
                observer = _object(websocket["realtimeObserver"], p + ".websocket.realtimeObserver", {"sessionTtlSeconds", "publicCallPath"})
                _integer(observer.get("sessionTtlSeconds"), p + ".websocket.realtimeObserver.sessionTtlSeconds", 60, 3600)
                _backend_url_template(observer.get("publicCallPath"), p + ".websocket.realtimeObserver.publicCallPath", set())
                if auth_mode != "managedIdentity" or api["backendAudience"] != "https://cognitiveservices.azure.com":
                    _fail(p + ".websocket.realtimeObserver", "requires the independent Entra-only caller gate and fixed Cognitive Services managed-identity backend")
        elif "websocket" in api:
            _fail(p + ".websocket", "is allowed only on a WebSocket API")
        # Streamable HTTPはmessage、従来SSEはsseとmessageを必要とする。
        # 別の通信方式を同じ入口数で扱わず、公式の接続先の組合せを検査する。
        if api_type == "mcp":
            proxy = _object(api.get("mcpProxy"), p + ".mcpProxy", {"transport", "endpoints"})
            transport = _choice(proxy.get("transport"), p + ".mcpProxy.transport", ("streamable", "sse"))
            endpoints = _list(proxy.get("endpoints"), p + ".mcpProxy.endpoints", minimum=1)
            endpoint_names = set()
            for endpoint in endpoints:
                _object(endpoint, p + ".mcpProxy.endpoints", {"name", "uriTemplate", "queryObservation"})
                if "queryObservation" in endpoint:
                    _query_observation_reference(endpoint["queryObservation"], p + ".mcpProxy.endpoints.queryObservation")
                endpoint_names.add(endpoint.get("name"))
                endpoint_path = _backend_url_template(endpoint.get("uriTemplate"), p + ".mcpProxy.endpoints.uriTemplate", set())
                # 同じ値をAPIMの公開POST先・転送先・WAFの完全一致へ使うため、
                # URL解釈によって別経路になる表記を受け付けない。session_id等の
                # 実行時queryは接続後に別途付くもので、ここへ固定値として書かない。
                if (not re.fullmatch(r"/[A-Za-z0-9._~/-]*", endpoint_path)
                        or "//" in endpoint_path or any(part in (".", "..") for part in endpoint_path.split("/"))):
                    _fail(p + ".mcpProxy.endpoints.uriTemplate", "must be a canonical fixed path without query, encoded segments or traversal")
            expected_names = {"message"} if transport == "streamable" else {"sse", "message"}
            if endpoint_names != expected_names or len(endpoints) != len(expected_names):
                _fail(p + ".mcpProxy.endpoints", "must match the official transport endpoint names without duplicates")
        elif "mcpProxy" in api:
            _fail(p + ".mcpProxy", "is allowed only on an existing MCP proxy")
        subscriptions = _list(api.get("keySubscriptions"), p + ".keySubscriptions")
        actual = set()
        for j, entry in enumerate(subscriptions):
            q = f"{p}.keySubscriptions[{j}]"
            _object(entry, q, {"subscriptionId", "displayName", "enabled"})
            sid = _string(entry.get("subscriptionId"), q + ".subscriptionId")
            if sid in actual or (sid in subscription_ids and subscription_ids[sid] != (product, entry)):
                _fail(q + ".subscriptionId", "shared subscriptions must have identical settings and the same Product")
            actual.add(sid)
            subscription_ids[sid] = (product, entry)
            _string(entry.get("displayName"), q + ".displayName")
            _bool(entry.get("enabled"), q + ".enabled")
    # 各APIの入口audienceとkey headerは、上で基盤の共通認証設定と照合済みです。
    # 下流サービスのbackendAudienceとscopeのHTTPS URLは共通認証とは別に扱います。
    for i, api in enumerate(result):
        observer = api.get("websocket", {}).get("realtimeObserver")
        if observer and not any(item["kind"] == "realtimeCreate" and item["publicCallPath"] == observer["publicCallPath"] and item["sessionTtlSeconds"] == observer["sessionTtlSeconds"] for owner_api in result for item in owner_api["authorization"]["operationConstraints"].values()):
            _fail(f"apiDefinitions[{i}].websocket.realtimeObserver", "must reference the configured create/hangup contract with the same lifetime")
    return result


def entra_tenant_id(configuration: dict) -> str:
    """認証用テナントを返す。省略時はAzureリソース用テナントを使う。"""
    return configuration.get("entraTenantId", configuration.get("azureTenantId"))


def entra_app_separation_enabled(configuration: dict) -> bool:
    """API、Graph用Tools、本人確認用Credentialを分けた基盤を判定する。"""
    authentication = configuration.get("gateway", {}).get("authentication", {})
    login = authentication.get("credentialLogin") or {}
    providers = login.get("providerNames")
    return bool(authentication.get("entra") and login.get("identity")
                and isinstance(providers, list) and "graph-delegated" in providers)


def _portal_authentication(value: Any) -> dict:
    """ポータル専用DBの設定を補い、SQLのSKUに矛盾する組合せを拒否する。

    CMのDB設定や企業固有のEntraアプリIDを複製せず、ポータルの性能と
    バックアップ条件だけを扱う。省略時は開発向けのBasicを選ぶ。
    """
    defaults = {
        "credentialProviderName": "graph-delegated", "sqlSkuName": "Basic",
        "sqlTier": "Basic", "sqlCapacity": 5, "sqlMaxSizeGB": 2,
        "backupRetentionDays": 7, "zoneRedundant": False, "backupRedundancy": "Local",
    }
    settings = _object(value, "portalAuthentication", set(defaults))
    for key, default in defaults.items():
        settings.setdefault(key, default)
    provider = _string(settings["credentialProviderName"], "portalAuthentication.credentialProviderName")
    if not API_NAME.fullmatch(provider):
        _fail("portalAuthentication.credentialProviderName", "must be a logical credential provider name")
    tier = _choice(settings["sqlTier"], "portalAuthentication.sqlTier", ("Basic", "GeneralPurpose", "BusinessCritical"))
    _string(settings["sqlSkuName"], "portalAuthentication.sqlSkuName")
    _integer(settings["sqlCapacity"], "portalAuthentication.sqlCapacity", 2, 128)
    _integer(settings["sqlMaxSizeGB"], "portalAuthentication.sqlMaxSizeGB", 1, 4096)
    _integer(settings["backupRetentionDays"], "portalAuthentication.backupRetentionDays", 1, 35)
    _bool(settings["zoneRedundant"], "portalAuthentication.zoneRedundant")
    _choice(settings["backupRedundancy"], "portalAuthentication.backupRedundancy", ("Local", "Zone", "Geo", "GeoZone"))
    if tier == "Basic":
        if settings["sqlSkuName"] != "Basic" or settings["sqlCapacity"] != 5:
            _fail("portalAuthentication", "Basic requires sqlSkuName Basic and sqlCapacity 5")
        if settings["sqlMaxSizeGB"] > 2 or settings["backupRetentionDays"] > 7 or settings["zoneRedundant"]:
            _fail("portalAuthentication", "Basic supports up to 2 GB, 7-day retention and no zone redundancy")
    elif settings["backupRetentionDays"] < 7:
        _fail("portalAuthentication.backupRetentionDays", "vCore databases require at least 7 days")
    return settings


def _portal_api_keys(value: Any) -> dict:
    """環境ごとの所有グループと許可製品を検査する。未設定でも基盤構築は妨げない。"""
    settings = _object(value, "portalApiKeys", {"groups", "platformGroupId", "groupNamePattern", "verificationSubscriptions", "verificationMaxSubscriptions"})
    _integer(settings.setdefault("verificationMaxSubscriptions", 5), "portalApiKeys.verificationMaxSubscriptions", 1, 1000)
    pattern = settings.setdefault("groupNamePattern", "")
    if not isinstance(pattern, str) or len(pattern) > 512 or re.search(r"[\x00-\x1f\x7f]", pattern):
        _fail("portalApiKeys.groupNamePattern", "must be a regular expression of at most 512 characters without control characters")
    groups = _list(settings.setdefault("groups", []), "portalApiKeys.groups")
    if len(groups) > 1000:
        _fail("portalApiKeys.groups", "must contain no more than 1000 groups")
    seen_groups = set()
    for index, group in enumerate(groups):
        path = f"portalApiKeys.groups[{index}]"
        _object(group, path, {"groupId", "products"})
        group["groupId"] = _guid(group.get("groupId"), path + ".groupId")
        if group["groupId"] in seen_groups:
            _fail(path + ".groupId", "duplicates an owner group")
        seen_groups.add(group["groupId"])
        products = _list(group.get("products"), path + ".products")
        if not 1 <= len(products) <= 100:
            _fail(path + ".products", "must contain from 1 to 100 approved products")
        seen_products = set()
        for product_index, product in enumerate(products):
            product_path = f"{path}.products[{product_index}]"
            _object(product, product_path, {"productId", "maxSubscriptions"})
            identifier = _string(product.get("productId"), product_path + ".productId", maximum=80)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier):
                _fail(product_path + ".productId", "must be an APIM product identifier")
            if identifier in seen_products:
                _fail(product_path + ".productId", "duplicates an approved product")
            seen_products.add(identifier)
            _integer(product.setdefault("maxSubscriptions", 5), product_path + ".maxSubscriptions", 1, 1000)
    if "platformGroupId" in settings:
        settings["platformGroupId"] = _guid(settings["platformGroupId"], "portalApiKeys.platformGroupId")
        if settings["platformGroupId"] not in seen_groups:
            _fail("portalApiKeys.platformGroupId", "must identify a configured foundation verification owner group")
    subscriptions = _list(settings.setdefault("verificationSubscriptions", []), "portalApiKeys.verificationSubscriptions")
    if len(subscriptions) > 100:
        _fail("portalApiKeys.verificationSubscriptions", "must contain at most 100 subscriptions")
    seen_subscriptions = set()
    platform = next((group for group in groups if group["groupId"] == settings.get("platformGroupId")), None)
    for index, entry in enumerate(subscriptions):
        path = f"portalApiKeys.verificationSubscriptions[{index}]"
        _object(entry, path, {"subscriptionId", "productId", "displayName"})
        identifier = _string(entry.get("subscriptionId"), path + ".subscriptionId", maximum=256)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", identifier) or identifier.lower() == "master":
            _fail(path + ".subscriptionId", "must identify a product-scoped subscription; master is not allowed")
        if identifier.lower() in seen_subscriptions:
            _fail(path + ".subscriptionId", "duplicates a verification subscription")
        seen_subscriptions.add(identifier.lower())
        product = _string(entry.get("productId"), path + ".productId", maximum=80)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", product):
            _fail(path + ".productId", "must be an APIM product identifier")
        if platform and product not in {item["productId"] for item in platform["products"]}:
            _fail(path + ".productId", "must be approved for the platform group")
        _string(entry.get("displayName"), path + ".displayName", maximum=80)
    return settings


def normalize_configuration(raw: dict, *, base_directory: Path | str = ".") -> dict:
    """合成済みの入力をコピーし、標準値と派生名を持つ検査済み設定を返す。

    元の辞書を書き換えず、名前・ネットワーク・モデル・認証・証明書の関係を検査する。
    ファイル参照は宣言元を基準に解決するが、この関数では秘密ファイルを開かず、
    Azureへも接続しない。矛盾があればConfigurationErrorで処理を止める。
    """
    c = copy.deepcopy(_object(raw, "configuration", TOP_KEYS))
    _no_secrets(c)
    # 旧paramsのCodex設定はクライアント専用CLIだけが読む。基盤設定には含めない。
    c.pop("codex", None)
    if "firewallTlsInspection" in c:
        c["firewallTlsInspection"] = _firewall_tls_inspection(c["firewallTlsInspection"], Path(base_directory))
    c["portalAuthentication"] = _portal_authentication(c.get("portalAuthentication", {}))
    c["portalApiKeys"] = _portal_api_keys(c.get("portalApiKeys", {}))
    if "$schema" in c:
        _string(c["$schema"], "$schema", maximum=1024)
    if "$comment" in c:
        _string(c["$comment"], "$comment", maximum=4096)
    if c.get("schemaVersion") != 1 or isinstance(c.get("schemaVersion"), bool):
        _fail("schemaVersion", "must be 1 for the new core configuration")
    for key, limit in (("productName", 24), ("environmentName", 12)):
        value = _string(c.get(key), key, maximum=limit)
        if not NAME.fullmatch(value):
            _fail(key, "must use lowercase letters, digits and single internal hyphens")
    c["subscriptionId"] = _guid(c.get("subscriptionId"), "subscriptionId")
    c["azureTenantId"] = _guid(c.get("azureTenantId"), "azureTenantId")
    c["entraTenantId"] = _guid(entra_tenant_id(c), "entraTenantId")
    for key in ("location", "foundryLocation", "apiCenterLocation"):
        # 配置先は企業のデータ所在地・利用枠に関わるため、暗黙の別地域へ配備しません。
        value = c.get(key)
        if not isinstance(value, str) or not REGION.fullmatch(value):
            _fail(key, "must be an Azure region code")
        c[key] = value
    # 名前は「種類-環境名-製品名」で一貫して派生させます。世界で一意な名前も
    # 勝手に切り詰めず、名前の最も厳しい制約を構築前にまとめて確認します。
    # Broker CA の ca-<stem>-credentials が最大32文字のため、stem は17文字までです。
    # この上限なら Key Vault は20文字以下、Storage は20文字以下に収まります。
    # 環境名test/dev/prodではSKU等を切り替えず、実際の動作はconfigで指定します。
    stem = f"{c['environmentName']}-{c['productName']}"
    if len(stem) > 17:
        _fail("productName/environmentName", "environmentName-productName must fit within 17 characters so ca-<environment>-<product>-credentials stays within the Container App 32-character limit; shorten the product or environment name")
    c["resourceStem"] = stem
    c["resourceGroupNames"] = {kind: f"rg-{stem}-{kind}" for kind in RG_KINDS}
    c["certificateIdentityName"] = f"id-{stem}-certificate"
    tags = _object(c.get("tags", {}), "tags")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in tags.items()):
        _fail("tags", "keys and values must be strings")
    c["tags"] = {**tags, "product": c["productName"], "environment": c["environmentName"]}
    audit_evidence = _object(c.setdefault("auditEvidence", {}), "auditEvidence", {
        "enabled", "eventHubsSku", "payloadImmutabilityMode", "payloadRetentionDays",
    })
    enabled = _bool(audit_evidence.setdefault("enabled", False), "auditEvidence.enabled")
    _choice(audit_evidence.setdefault("eventHubsSku", "Standard"), "auditEvidence.eventHubsSku", ("Standard", "Premium"))
    immutability = _choice(audit_evidence.setdefault("payloadImmutabilityMode", "Disabled"),
                           "auditEvidence.payloadImmutabilityMode", ("Disabled", "Locked"))
    retention_default, retention_maximum = (2557, 146000) if immutability == "Locked" else (30, 365)
    _integer(audit_evidence.setdefault("payloadRetentionDays", retention_default),
             "auditEvidence.payloadRetentionDays", 1, retention_maximum)
    c["network"] = _network(c.get("network"), audit_evidence_enabled=enabled)
    s = _object(c.get("services"), "services", {"publisherName", "publisherEmail", "apimInstanceId", "apimSkuName", "apimCapacity", "apiCenterSkuName", "vaultSkuName", "vaultRetentionDays", "vaultPurgeProtection", "modelDeployments", "projectDisplayName", "projectDescription", "enableApiCenterIntegration", "catalogEnvironmentKind", "catalogLifecycleStage", "grantApimSecretAccess", "speechEnabled", "grantApimFoundryModelsAccess"})
    try:
        apim_instance_id(c)
    except ValueError:
        _fail("services.apimInstanceId", "must contain exactly 6 lowercase letters or digits; prepare the local deployment identifier explicitly")
    _string(s.get("publisherName"), "services.publisherName")
    if not EMAIL.fullmatch(_string(s.get("publisherEmail"), "services.publisherEmail")):
        _fail("services.publisherEmail", "must be an email address")
    _choice(s.setdefault("apimSkuName", "Premium"), "services.apimSkuName", ("Developer", "Premium"))
    _choice(s.setdefault("apiCenterSkuName", "Free"), "services.apiCenterSkuName", ("Free", "Standard"))
    _integer(s.setdefault("apimCapacity", 1), "services.apimCapacity", 1, 12)
    if s["apimSkuName"] == "Developer" and s["apimCapacity"] != 1:
        _fail("services.apimCapacity", "Developer supports a single unit")
    _choice(s.setdefault("vaultSkuName", "standard"), "services.vaultSkuName", ("standard", "premium"))
    _integer(s.setdefault("vaultRetentionDays", 90), "services.vaultRetentionDays", 7, 90)
    _bool(s.setdefault("vaultPurgeProtection", True), "services.vaultPurgeProtection")
    _bool(s.setdefault("enableApiCenterIntegration", True), "services.enableApiCenterIntegration")
    _bool(s.setdefault("grantApimSecretAccess", False), "services.grantApimSecretAccess")
    _bool(s.setdefault("speechEnabled", False), "services.speechEnabled")
    _bool(s.setdefault("grantApimFoundryModelsAccess", False), "services.grantApimFoundryModelsAccess")
    _choice(s.setdefault("catalogEnvironmentKind", "production"), "services.catalogEnvironmentKind", ("development", "production", "staging", "testing"))
    _choice(s.setdefault("catalogLifecycleStage", "production"), "services.catalogLifecycleStage", ("deprecated", "design", "development", "preview", "production", "retired", "testing"))
    for key, default in (("projectDisplayName", "core-platform"), ("projectDescription", "Core platform project for shared model, agent, and tool capabilities.")):
        _string(s.setdefault(key, default), "services." + key)
    models = _list(s.setdefault("modelDeployments", []), "services.modelDeployments")
    # この基盤のFoundryは1リソースです。配備できるモデル数の上限を
    # Azure作成前に検査し、34番目の配備等で初めて失敗する状態を避けます。
    # https://learn.microsoft.com/azure/foundry/foundry-models/quotas-limits
    if len(models) > 32:
        _fail("services.modelDeployments", "must contain at most 32 deployments for the single Foundry resource")
    model_names = set()
    for i, model in enumerate(models):
        p = f"services.modelDeployments[{i}]"
        _object(model, p, {"name", "modelName", "modelVersion", "modelFormat", "skuName", "capacity", "versionUpgradeOption", "quotaUsageName"})
        for key in ("name", "modelName", "modelVersion", "modelFormat", "skuName", "versionUpgradeOption"):
            _string(model.get(key), p + "." + key)
        # Claudeは構築後にGUIで追加する。設定でazd配備を再有効化する経路は持たない。
        if (model["modelFormat"].casefold() == "anthropic"
                or any(model[key].casefold().startswith("claude-") for key in ("name", "modelName"))):
            _fail(p, "Claude deployments are outside azd provisioning; add them manually after deployment")
        if model["name"] in model_names:
            _fail(p + ".name", "must be unique")
        model_names.add(model["name"])
        # Microsoft の正式モデル名には Azure の配備名で予約される語が含まれる
        # 場合があります。正式 modelName は保持し、Microsoft 形式だけ別名を
        # 許します。API の model 許可と Bicep の子資源名には実配備名 name を使い、
        # modelName/版/形式の実照合は既存 publisher が別々に行います。
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", model["name"]):
            _fail(p + ".name", "must be a safe model deployment identifier of 1 through 64 characters")
        if model["name"] != model["modelName"] and model["modelFormat"] != "Microsoft":
            _fail(p, "deployment name must equal the formal model name except for Microsoft format")
        if model["versionUpgradeOption"] != "NoAutoUpgrade":
            _fail(p, "upgrades must be pinned with NoAutoUpgrade")
        _integer(model.get("capacity"), p + ".capacity", 1, 100000)
        if "quotaUsageName" in model:
            _string(model["quotaUsageName"], p + ".quotaUsageName", maximum=256)
    c["services"] = s
    # AppGW本体とPublic IPのゾーンは一致させる。WAFの本文上限と検査上限も整合させる。
    g = _object(c.get("gateway", {}), "gateway", {"publicHostName", "authentication", "skuName", "autoscaleMinCapacity", "autoscaleMaxCapacity", "zones", "existingWafPolicyId", "wafMode", "wafCustomRules", "wafExclusions", "backendRequestTimeoutSeconds", "wafMaxRequestBodySizeInKb", "wafRequestBodyInspectLimitInKb", "wafFileUploadLimitInMb", "realtimeControlEnabled", "rateLimits"})
    g["publicHostName"] = _host(g.get("publicHostName"), "gateway.publicHostName")
    # 共通入口は購読キーの提示を優先し、キーなしBearer要求にはEntra認証を使います。
    authentication = _object(g.get("authentication"), "gateway.authentication", {"subscriptionKeyHeader", "credentialLogin", "entra"})
    key_header = _string(authentication.setdefault("subscriptionKeyHeader", "api-key"), "gateway.authentication.subscriptionKeyHeader", maximum=128)
    # 相関・本人接続用ヘッダーと同じ名前では、基盤の書き換えでキーの値が変わるため拒否する。
    if (not re.fullmatch(r"[A-Za-z0-9-]+", key_header)
            or key_header.casefold().startswith("x-core-connection-")
            or key_header.casefold() in ("authorization", "host", "content-type", "cookie", "x-core-l7-ingress",
                                        "x-ms-client-request-id", "x-appgw-trace-id",
                                        "x-core-caller-id", "x-core-subscription-id")):
        _fail("gateway.authentication.subscriptionKeyHeader", "must be a dedicated HTTP header name")
    if "entra" in authentication:
        authentication["entra"] = _entra_authentication(authentication["entra"])
    login = authentication.get("credentialLogin")
    if login is not None:
        q = "gateway.authentication.credentialLogin"
        _object(login, q, {"providerNames", "identity", "transactionLifetimeSeconds", "loginCallsPerMinute", *SESSION_POLICY_DEFAULTS})
        selected = _strings(login.get("providerNames"), q + ".providerNames", minimum=1)
        if len(set(selected)) != len(selected) or any(not API_NAME.fullmatch(name) for name in selected):
            _fail(q + ".providerNames", "must identify unique Credential Manager providers")
        if "identity" in login:
            identity_path = q + ".identity"
            identity = _object(login["identity"], identity_path, {"tenantId", "clientId", "clientSecretFile"})
            if entra_app_separation_enabled(c):
                identity.setdefault("tenantId", c["entraTenantId"])
            for field in ("tenantId", "clientId"):
                identity[field] = _guid(identity.get(field), identity_path + "." + field)
            identity["clientSecretFile"] = _secret_path(identity.get("clientSecretFile"), identity_path + ".clientSecretFile", Path(base_directory).resolve())
        for field, default in SESSION_POLICY_DEFAULTS.items():
            minimum, maximum = SESSION_POLICY_BOUNDS[field]
            _integer(login.setdefault(field, default), q + "." + field, minimum, maximum)
        _integer(login.setdefault("transactionLifetimeSeconds", 300), q + ".transactionLifetimeSeconds", 60, MAX_TRANSACTION_LIFETIME_SECONDS)
        _integer(login.setdefault("loginCallsPerMinute", 10), q + ".loginCallsPerMinute", 1, 1000)
        if login["idleTimeoutSeconds"] > login["absoluteLifetimeSeconds"]:
            _fail(q + ".idleTimeoutSeconds", "must not exceed the absolute session lifetime")
        if login["rotationIntervalSeconds"] >= login["absoluteLifetimeSeconds"]:
            _fail(q + ".rotationIntervalSeconds", "must be shorter than the absolute session lifetime")
        if login["rotationGraceSeconds"] >= login["rotationIntervalSeconds"]:
            _fail(q + ".rotationGraceSeconds", "must be shorter than the rotation interval")
        if login["rotationCandidateTtlSeconds"] > login["rotationHandoffSeconds"]:
            _fail(q + ".rotationCandidateTtlSeconds", "must not exceed the rotation handoff period")
    _choice(g.setdefault("skuName", "WAF_v2"), "gateway.skuName", ("WAF_v2", "Standard_v2"))
    low = _integer(g.setdefault("autoscaleMinCapacity", 2), "gateway.autoscaleMinCapacity", 0, 100)
    high = _integer(g.setdefault("autoscaleMaxCapacity", 5), "gateway.autoscaleMaxCapacity", 2, 125)
    if low > high:
        _fail("gateway", "autoscale minimum exceeds maximum")
    _strings(g.setdefault("zones", ["1", "2", "3"]), "gateway.zones")
    if g["zones"] != c["network"]["appGatewayZones"]:
        _fail("gateway.zones", "must match network.appGatewayZones for its Public IP")
    _choice(g.setdefault("wafMode", "Prevention"), "gateway.wafMode", ("Prevention", "Detection"))
    waf_max = _integer(g.setdefault("wafMaxRequestBodySizeInKb", 128), "gateway.wafMaxRequestBodySizeInKb", 8, 2000)
    _integer(g.setdefault("wafRequestBodyInspectLimitInKb", 128), "gateway.wafRequestBodyInspectLimitInKb", 8, waf_max)
    _integer(g.setdefault("wafFileUploadLimitInMb", 100), "gateway.wafFileUploadLimitInMb", 1, 4000)
    _integer(g.setdefault("backendRequestTimeoutSeconds", 600), "gateway.backendRequestTimeoutSeconds", 1, 86400)
    for key in ("wafCustomRules", "wafExclusions"):
        _list(g.setdefault(key, []), "gateway." + key)
    g.setdefault("existingWafPolicyId", "")
    _bool(g.setdefault("realtimeControlEnabled", False), "gateway.realtimeControlEnabled")
    # 本人とアプリ単独は検証済み tid+oid、共有キーは APIM 購読 ID ごとに計数します。
    # overrides はこの計数枠の上限を個別調整します。
    limits_path = "gateway.rateLimits"
    limits = _object(g.setdefault("rateLimits", {}), limits_path, {"user", "subscriptionKey", "application"})
    for kind in ("user", "subscriptionKey", "application"):
        rate_path = limits_path + "." + kind
        fields = {"enabled", "calls", "renewalPeriodSeconds"} | ({"overrides"} if kind != "user" else set())
        rate = _object(limits.setdefault(kind, {}), rate_path, fields)
        # 既定では要求回数を制限せず、運用者が明示した場合だけ適用します。
        _bool(rate.setdefault("enabled", False), rate_path + ".enabled")
        if kind != "subscriptionKey" and rate["enabled"]:
            _fail(rate_path + ".enabled", "common request limits use the verified subscription; enable subscriptionKey limits")
        _integer(rate.setdefault("calls", 60), rate_path + ".calls", 1, 1000000)
        _integer(rate.setdefault("renewalPeriodSeconds", 60), rate_path + ".renewalPeriodSeconds", 1, 300)
        if kind == "user":
            continue
        overrides = _list(rate.setdefault("overrides", []), rate_path + ".overrides")
        caller_ids = set()
        for index, entry in enumerate(overrides):
            entry_path = f"{rate_path}.overrides[{index}]"
            _object(entry, entry_path, {"callerId", "calls", "renewalPeriodSeconds"})
            # 監査に記録される callerId を、最大512字の文字列で指定します。
            caller_id = _string(entry.get("callerId"), entry_path + ".callerId")
            if caller_id in caller_ids:
                _fail(entry_path + ".callerId", "duplicates a caller rate override")
            caller_ids.add(caller_id)
            _integer(entry.get("calls"), entry_path + ".calls", 1, 1000000)
            _integer(entry.get("renewalPeriodSeconds"), entry_path + ".renewalPeriodSeconds", 1, 300)
    c["gateway"] = g
    # VMはサイズとOSイメージだけでなく、ディスク方式・SSH鍵・管理者名も検査する。
    # latestの自動選択は使わず、検証した固定版で再現可能にする。
    m = _object(c.get("management"), "management", {"vmSize", "imageReference", "diskControllerType", "osDiskSizeGB", "osDiskSku", "enableAcceleratedNetworking", "encryptionAtHost", "adminUsername", "sshPublicKeyFile", "patchAssessmentMode", "patchMode"})
    _string(m.get("vmSize"), "management.vmSize")
    controller = _choice(m.get("diskControllerType"), "management.diskControllerType", ("SCSI", "NVMe"))
    if m["vmSize"] == "Standard_D2as_v6" and controller != "NVMe":
        _fail("management.diskControllerType", "Standard_D2as_v6 requires NVMe")
    image = _object(m.get("imageReference"), "management.imageReference", {"id", "publisher", "offer", "sku", "version"})
    if "id" in image:
        if set(image) != {"id"} or not _string(image["id"], "management.imageReference.id").startswith("/subscriptions/"):
            _fail("management.imageReference", "an explicit image resource ID cannot be combined with marketplace fields")
    else:
        for key in ("publisher", "offer", "sku", "version"):
            _string(image.get(key), "management.imageReference." + key)
        if not re.fullmatch(r"\d+\.\d+\.\d+", image["version"]):
            _fail("management.imageReference.version", "must be a fixed numeric version; latest is not allowed")
    _integer(m.setdefault("osDiskSizeGB", 128), "management.osDiskSizeGB", 128, 4095)
    _choice(m.setdefault("osDiskSku", "StandardSSD_LRS"), "management.osDiskSku", ("Standard_LRS", "StandardSSD_LRS", "Premium_LRS"))
    for key in ("enableAcceleratedNetworking", "encryptionAtHost"):
        _bool(m.setdefault(key, True), "management." + key)
    username = _string(m.get("adminUsername"), "management.adminUsername", maximum=32)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", username) or username.lower() in {"administrator", "admin", "root", "guest"}:
        _fail("management.adminUsername", "must be a non-reserved Linux administrator name")
    base = Path(base_directory).resolve()
    m["sshPublicKeyFile"] = _secret_path(m.get("sshPublicKeyFile"), "management.sshPublicKeyFile", base)
    _choice(m.setdefault("patchAssessmentMode", "ImageDefault"), "management.patchAssessmentMode", ("ImageDefault", "AutomaticByPlatform"))
    _choice(m.setdefault("patchMode", "ImageDefault"), "management.patchMode", ("ImageDefault", "AutomaticByPlatform"))
    c["management"] = m
    # 監査ログの保持は検証用の便宜で暗黙に弱めない。Locked/Unlocked/Disabledと
    # 保持日数を明示した設定として扱い、撤去時も実状態との照合に使う。
    mon = _object(c.get("monitoring", {}), "monitoring", {"retentionInDays", "workspaceDailyQuotaGb", "auditStorageSku", "auditAllowedIpAddresses", "auditImmutabilityMode", "auditRetentionDays", "operatorEmailAddresses", "appInsightsIngestionWarningGb", "enableLogAlerts", "auditReaders"})
    _integer(mon.setdefault("retentionInDays", 90), "monitoring.retentionInDays", 90, 730)
    _integer(mon.setdefault("workspaceDailyQuotaGb", -1), "monitoring.workspaceDailyQuotaGb", -1, 100000)
    _choice(mon.setdefault("auditStorageSku", "Standard_GRS"), "monitoring.auditStorageSku", ("Standard_LRS", "Standard_GRS", "Standard_RAGRS", "Standard_ZRS", "Standard_GZRS", "Standard_RAGZRS"))
    _choice(mon.setdefault("auditImmutabilityMode", "Locked"), "monitoring.auditImmutabilityMode", ("Disabled", "Unlocked", "Locked"))
    _integer(mon.setdefault("auditRetentionDays", 2557), "monitoring.auditRetentionDays", 1, 146000)
    _cidrs(mon.setdefault("auditAllowedIpAddresses", []), "monitoring.auditAllowedIpAddresses")
    seen_readers = set()
    for reader in _list(mon.setdefault("auditReaders", []), "monitoring.auditReaders"):
        _object(reader, "monitoring.auditReaders", {"principalId", "principalType", "collector", "containerName"})
        _principals([{key: reader.get(key) for key in ("principalId", "principalType")}], "monitoring.auditReaders")
        collector = _choice(reader.get("collector"), "monitoring.auditReaders.collector", ("collectJapan", "collectFoundry"))
        allowed_containers = {"insights-logs-audit"} if collector == "collectFoundry" else {
            "insights-activity-logs", "insights-logs-audit", "insights-logs-vmprotectionalerts", "insights-logs-applicationgatewayaccesslog",
            "insights-logs-applicationgatewayfirewalllog", "insights-logs-azfwnetworkrule", "insights-logs-azfwapplicationrule",
            "insights-logs-azfwidpssignature", "insights-logs-azfwthreatintel", "insights-logs-gatewaylogs",
            "insights-logs-gatewayllmlogs", "insights-logs-websocketconnectionlogs", "insights-logs-auditevent",
            "am-apprequests", "am-appdependencies", "am-appexceptions", "am-apptraces", "am-appevents", "am-syslog", "am-agwaccesslogs",
        }
        if reader.get("containerName") not in allowed_containers:
            _fail("monitoring.auditReaders.containerName", "must identify an owned audit container in the selected regional collector")
        key = (reader["principalId"], collector, reader["containerName"])
        if key in seen_readers:
            _fail("monitoring.auditReaders", "contains a duplicate container grant")
        seen_readers.add(key)
    for address in _strings(mon.get("operatorEmailAddresses"), "monitoring.operatorEmailAddresses", minimum=1):
        if not EMAIL.fullmatch(address):
            _fail("monitoring.operatorEmailAddresses", "must contain email addresses")
    _integer(mon.setdefault("appInsightsIngestionWarningGb", 80), "monitoring.appInsightsIngestionWarningGb", 1, 100000)
    _bool(mon.setdefault("enableLogAlerts", False), "monitoring.enableLogAlerts")
    c["monitoring"] = mon
    portal = _object(c.get("portal"), "portal", PARAMETER_SECTION_KEYS["portal"])
    portal_cert = _object(portal.setdefault("certificate", {}), "portal.certificate", {"certificateName", "validityMonths", "renewBeforeDays"})
    portal_cert_name = _string(portal_cert.setdefault("certificateName", "portal-auth"),
                               "portal.certificate.certificateName", maximum=127)
    if not re.fullmatch(r"[a-z0-9-]+", portal_cert_name):
        _fail("portal.certificate.certificateName", "must use lowercase letters, digits and hyphens")
    _integer(portal_cert.setdefault("validityMonths", 12), "portal.certificate.validityMonths", 1, 24)
    _integer(portal_cert.setdefault("renewBeforeDays", 60), "portal.certificate.renewBeforeDays", 7, 90)
    if "authentication" in portal:
        auth_path = "portal.authentication"
        auth = _object(portal["authentication"], auth_path, {"tenantId", "clientId", "keyId", "certificateThumbprint"})
        if entra_app_separation_enabled(c):
            auth.setdefault("tenantId", c["entraTenantId"])
        for field in ("tenantId", "clientId"):
            auth[field] = _guid(auth.get(field), auth_path + "." + field)
        if ("keyId" in auth) != ("certificateThumbprint" in auth):
            _fail(auth_path, "explicit key version and thumbprint must be supplied together")
        if "keyId" in auth:
            key = _string(auth["keyId"], auth_path + ".keyId")
            # URLの正規表現を独立させ、公開データ検査でもURLとして誤読させません。
            if not re.fullmatch(re.escape("https://") + r"[a-z0-9-]+\.vault\.azure\.net/keys/[a-z0-9-]+/[a-f0-9]{32}", key, re.I):
                _fail(auth_path + ".keyId", "must pin an Azure Key Vault key version")
            thumbprint = _string(auth["certificateThumbprint"], auth_path + ".certificateThumbprint")
            if not re.fullmatch(r"[a-fA-F0-9]{40}", thumbprint):
                _fail(auth_path + ".certificateThumbprint", "must be the public certificate SHA-1 thumbprint paired with the key version")
            auth["certificateThumbprint"] = thumbprint.lower()
    if "apiAuthentication" in portal:
        path = "portal.apiAuthentication"
        trial = _object(portal["apiAuthentication"], path, {"enabled", "keyVersion", "previousKeyVersions"})
        _bool(trial.setdefault("enabled", False), path + ".enabled")
        previous = _strings(trial.get("previousKeyVersions", []), path + ".previousKeyVersions")
        versions = previous + ([trial["keyVersion"]] if "keyVersion" in trial else [])
        if any(not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{32}", version) for version in versions):
            _fail(path, "storage key versions must be exact lowercase Key Vault versions")
        if len(previous) > 4 or len(set(versions)) != len(versions):
            _fail(path, "storage key versions must be distinct and include at most four previous versions")
    portal_host = _host(portal.get("publicHostName"), "portal.publicHostName")
    cert = c.get("certificate")
    validate_supported_certificate(cert)
    # 指定がなければGatewayとPortalの実ホスト名をSANへ入れます。ワイルドカードを使う場合は
    # paramsへ明示します。*.example.comは直下の一階層だけで、apexや深い階層を含みません。
    domain_names = _strings(cert.setdefault("domainNames", [g["publicHostName"], portal_host]),
                            "certificate.domainNames", minimum=1)
    if len(domain_names) > 100:
        _fail("certificate.domainNames", "must contain at most 100 certificate DNS names")
    cert["domainNames"] = [_host(value, f"certificate.domainNames[{index}]", wildcard=True)
                           for index, value in enumerate(domain_names)]
    if len(set(cert["domainNames"])) != len(cert["domainNames"]):
        _fail("certificate.domainNames", "must not contain duplicate DNS names")
    for hostname in (g["publicHostName"],):
        if not any(name == hostname or name.startswith("*.") and hostname.split(".", 1)[1] == name[2:]
                   for name in cert["domainNames"]):
            _fail("certificate.domainNames", "must cover the Gateway hostname")
    https_cert_name = _string(cert.setdefault("certificateName", "tls-public"),
                              "certificate.certificateName", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", https_cert_name):
        _fail("certificate.certificateName", "must be a Key Vault certificate identifier")
    public_cert_name = https_cert_name.casefold()
    if portal_cert_name == public_cert_name:
        _fail("portal.certificate.certificateName", "must differ from the HTTPS certificate name")
    if public_cert_name == "gateway-obo" or public_cert_name.startswith("core-"):
        _fail("certificate.certificateName", "must not use a reserved Key Vault object name")
    if portal_cert_name == "gateway-obo" or portal_cert_name.startswith(("core-", public_cert_name + "-acme-")):
        _fail("portal.certificate.certificateName", "must not use a reserved Key Vault object name")
    _integer(cert.setdefault("renewBeforeDays", 30), "certificate.renewBeforeDays", 1, 60)
    _integer(cert.setdefault("checkEveryHours", 12), "certificate.checkEveryHours", 1, 24)
    _integer(cert.setdefault("dnsSleepSeconds", 60), "certificate.dnsSleepSeconds", 10, 600)
    _integer(cert.setdefault("validationTimeoutSeconds", 120), "certificate.validationTimeoutSeconds", 30, 1800)
    _integer(cert.setdefault("minimumValidDays", 7), "certificate.minimumValidDays", 1, 60)
    if cert["minimumValidDays"] > cert["renewBeforeDays"]:
        _fail("certificate.minimumValidDays", "must not exceed renewBeforeDays")
    if cert.setdefault("moduleVersion", "4.34.0") != "4.34.0":
        _fail("certificate.moduleVersion", "the certificate worker currently supports pinned Posh-ACME 4.34.0")
    _choice(cert.setdefault("directory", "production"), "certificate.directory", ("production", "staging"))
    if not EMAIL.fullmatch(_string(cert.get("contactEmail"), "certificate.contactEmail")):
        _fail("certificate.contactEmail", "must be an ACME account email address")
    # Azure Public DNS is the only supported issuance backend. Other DNS
    # plugin converters remain internal and cannot widen this deployment path.
    c["certificate"] = cert
    auth = _object(c.get("authorization", {}), "authorization", {"projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders"})
    for key in ("projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders"):
        _principals(auth.setdefault(key, []), "authorization." + key)
    c["authorization"] = auth
    # OAuth接続は公開IDと秘密ファイル参照だけを持つ。公開設定やrender結果に
    # プロバイダーの秘密そのものを含めない。
    providers = _list(c.setdefault("credentialProviders", []), "credentialProviders")
    provider_names = set()
    for i, provider in enumerate(providers):
        q = f"credentialProviders[{i}]"
        _object(provider, q, {"name", "displayName", "identityProvider", "clientId", "clientSecretFile", "oauthTenantId", "scopes", "resourceUri", "authorizationUrl", "tokenUrl", "refreshUrl", "connectionNames", "identityEndpoint", "identityIdProperty", "pool"})
        name = _string(provider.get("name"), q + ".name")
        if not API_NAME.fullmatch(name) or name.casefold() in provider_names:
            _fail(q + ".name", "must be a unique resource identifier")
        provider_names.add(name.casefold())
        if "pool" in provider:
            pool = _object(provider["pool"], q + ".pool", {"count", "capacity"})
            _integer(pool.get("count"), q + ".pool.count", 1, 26)
            _integer(pool.get("capacity"), q + ".pool.capacity", 1, 8000)
            if len(name) > 78 or provider.get("connectionNames"):
                _fail(q + ".pool", "requires a short logical name and dynamically allocated connections")
        _string(provider.get("displayName"), q + ".displayName")
        kind = _choice(provider.get("identityProvider"), q + ".identityProvider", ("aad", "oauth2"))
        _string(provider.get("clientId"), q + ".clientId")
        if provider["name"] == "graph-delegated" and kind == "aad":
            # source読込は未入力テンプレートも扱う。実配備用の正規化では
            # 標準GraphのゼロIDを拒否し、登録済みIDの表記は変更しない。
            _guid(provider["clientId"], q + ".clientId")
        provider["clientSecretFile"] = _secret_path(provider.get("clientSecretFile"), q + ".clientSecretFile", base)
        provider["oauthTenantId"] = _guid(provider.setdefault("oauthTenantId", c["entraTenantId"]), q + ".oauthTenantId")
        _string(provider.get("scopes"), q + ".scopes")
        if kind == "aad":
            _https(provider.get("resourceUri"), q + ".resourceUri")
        else:
            for key in ("authorizationUrl", "tokenUrl", "refreshUrl"):
                _https(provider.get(key), q + "." + key)
        for name in _strings(provider.get("connectionNames"), q + ".connectionNames", minimum=0 if login and provider["name"] in login["providerNames"] else 1):
            if not API_NAME.fullmatch(name):
                _fail(q + ".connectionNames", "must contain explicit connection resource identifiers")
    if login:
        q = "gateway.authentication.credentialLogin"
        if s["apimSkuName"] == "Consumption":
            _fail(q, "requires an APIM tier supporting Credential Manager")
        for name in login["providerNames"]:
            provider = next((p for p in providers if p["name"] == name), None)
            if not provider or provider["identityProvider"] != "aad":
                _fail(q + ".providerNames", "personal sign-in requires a registered Entra provider")
            identity = login.get("identity")
            if identity and identity["tenantId"] != provider["oauthTenantId"]:
                _fail(q + ".identity.tenantId", "must match every selected personal connection provider tenant")
            endpoint = _https(provider.get("identityEndpoint"), "credentialProviders.identityEndpoint")
            identity = urlparse(endpoint)
            resource = urlparse(provider["resourceUri"])
            if identity.hostname != resource.hostname or identity.query or identity.fragment or identity.username or identity.password or identity.port not in (None, 443):
                _fail("credentialProviders.identityEndpoint", "must be a fixed HTTPS identity endpoint on the provider resource host")
            prop = provider.setdefault("identityIdProperty", "id")
            if not isinstance(prop, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", prop):
                _fail("credentialProviders.identityIdProperty", "must be an explicit identity response property")
        if login.get("identity"):
            identity = login["identity"]
            for provider in providers:
                if Path(provider["clientSecretFile"]) == Path(identity["clientSecretFile"]) and (
                        provider["oauthTenantId"] != identity["tenantId"]
                        or provider["clientId"].lower() != identity["clientId"]):
                    _fail(q + ".identity.clientSecretFile", "must not share a credential file with a different Entra application")
    # SQL台帳と認証窓口は常設。業務の本人接続設定がなくても構築設定を検査します。
    broker = _object(c.setdefault("credentialBroker", {}), "credentialBroker", {
        "sqlSkuName", "sqlTier", "sqlCapacity", "sqlMaxSizeGB", "backupRetentionDays",
        "zoneRedundant", "backupRedundancy", "minReplicas", "maxReplicas"})
    _choice(broker.setdefault("sqlTier", "GeneralPurpose"), "credentialBroker.sqlTier", ("GeneralPurpose", "BusinessCritical"))
    _string(broker.setdefault("sqlSkuName", "GP_Gen5_2"), "credentialBroker.sqlSkuName")
    _integer(broker.setdefault("sqlCapacity", 2), "credentialBroker.sqlCapacity", 2, 128)
    _integer(broker.setdefault("sqlMaxSizeGB", 32), "credentialBroker.sqlMaxSizeGB", 1, 4096)
    _integer(broker.setdefault("backupRetentionDays", 14), "credentialBroker.backupRetentionDays", 7, 35)
    _bool(broker.setdefault("zoneRedundant", False), "credentialBroker.zoneRedundant")
    _choice(broker.setdefault("backupRedundancy", "Geo"), "credentialBroker.backupRedundancy", ("Local", "Zone", "Geo", "GeoZone"))
    minimum = _integer(broker.setdefault("minReplicas", 2), "credentialBroker.minReplicas", 1, 30)
    _integer(broker.setdefault("maxReplicas", 4), "credentialBroker.maxReplicas", minimum, 30)
    from .credential_pools import physical_providers
    physical = physical_providers(c)
    names = [item["name"].casefold() for item in physical]
    if len(names) != len(set(names)) or len(names) > 1000:
        _fail("credentialProviders", "physical provider names must be unique within the APIM provider limit")
    publish = _bool(c.get("publishApis"), "publishApis")
    c["apiDefinitions"] = _apis(c.get("apiDefinitions"), c["entraTenantId"], model_names, "https://" + g["publicHostName"], authentication)
    if g["skuName"] == "WAF_v2" and not g["existingWafPolicyId"]:
        from .waf_body import managed_ai_paths
        large_body_paths = managed_ai_paths(c)
        for i, api in enumerate(c["apiDefinitions"]):
            for name, constraint in api["authorization"]["operationConstraints"].items():
                operation = next(item for item in api["operations"] if item["name"] == name)
                public_path = "/" + api["path"].strip("/") + operation["urlTemplate"]
                # Only the exact managed AI paths receive size-enforcement relaxation.
                if api["apiType"] == "http" and constraint["bodyFormat"] not in ("multipart", "none") and public_path not in large_body_paths and constraint["maxRequestBytes"] > g["wafMaxRequestBodySizeInKb"] * 1024:
                    _fail(f"apiDefinitions[{i}].authorization.operationConstraints.{name}.maxRequestBytes", "must not exceed the managed WAF request-body limit")
    registered_backends = [backend for api in c["apiDefinitions"] for backend in
                           [api, *(operation["backendOverride"] for operation in api["operations"] if "backendOverride" in operation)]]
    if any(backend["backendAuthentication"]["mode"] == "speechEntra" for backend in registered_backends) and not s["speechEnabled"]:
        _fail("services.speechEnabled", "must be enabled for registered Speech Entra backends; external resource RBAC remains an explicit prerequisite")
    key_vault_backends = [backend["backendAuthentication"] for backend in registered_backends
                          if backend["backendAuthentication"]["mode"] == "keyVaultHeader"]
    if key_vault_backends and not s["grantApimSecretAccess"]:
        _fail("services.grantApimSecretAccess", "must be enabled for registered backend Key Vault credentials")
    tls_vault_host = ".".join((f"kv-{stem}-tls", "vault.azure.net"))
    if any(urlparse(authentication["secretIdentifier"]).hostname == tls_vault_host
           for authentication in key_vault_backends):
        _fail("apiDefinitions.backendAuthentication.secretIdentifier", "must not reference the public TLS certificate vault")
    for api in c["apiDefinitions"]:
        connection = api.get("credentialConnection")
        if connection:
            provider = next((p for p in providers if p["name"] == connection["providerName"]), None)
            if not provider:
                _fail("apiDefinitions.credentialConnection", "references an undefined credential provider")
            if connection["mode"] == "personal" and (not login or connection["providerName"] not in login["providerNames"]):
                _fail("apiDefinitions.credentialConnection", "personal connections require the shared credentialLogin provider")
            if connection["mode"] == "service" and connection["connectionName"] not in provider["connectionNames"]:
                _fail("apiDefinitions.credentialConnection", "references an undefined fixed service connection")
    c["apiPublicationStatus"] = "configured" if publish else "not-published"
    # カタログ用の説明も無料の設定検査で作れることを先に確認する。
    # 説明自体はBicep入力時にだけ追加し、利用者の設定ファイルへは混ぜない。
    with_catalog_descriptions(c)
    host = _host(portal.get("publicHostName"), "portal.publicHostName")
    if not host.startswith("portal.") or host == g["publicHostName"]:
        _fail("portal.publicHostName", "must be a distinct portal subdomain")
    if not any(name == host or (name.startswith("*.") and host.split(".", 1)[1] == name[2:]) for name in cert["domainNames"]):
        _fail("certificate.domainNames", "must cover the Portal hostname")
    _validate_separated_entra_apps(c, portal, authentication, providers)
    # Coreの最初の変更より前に、API公開と同じFW表現可能性を検査します。
    # 内部生成値を公開設定へ保存せず、秘密やURLをエラーへ含めません。
    from .firewall_destinations import generated_apim_service_fqdns
    try:
        generated_apim_service_fqdns(c)
    except ValueError:
        _fail("apiDefinitions", "external destinations require HTTPS/WSS on port 443 and DNS hosts without embedded credentials; private input suppressed")
    return c


def _validate_separated_entra_apps(config, portal, authentication, providers):
    """基盤のアプリ登録をPortalの登録前後で照合する。"""
    api = authentication.get("entra", {})
    obo = api.get("obo")
    if obo:
        if api.get("requiredScope") != "Gateway.Invoke":
            _fail("gateway.authentication.entra.obo", "requires the Gateway.Invoke delegated scope")
        from .portal_auth_certificate import application_vault_origin
        prefix = application_vault_origin(config["resourceStem"]) + "/keys/gateway-obo/"
        if not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{32}", obo["keyId"]):
            _fail("gateway.authentication.entra.obo.keyId", "must pin the dedicated key in this foundation")
    separated = entra_app_separation_enabled(config)
    if "apiAuthentication" in portal:
        trial = _object(portal["apiAuthentication"], "portal.apiAuthentication", {"enabled", "keyVersion", "previousKeyVersions"})
        _bool(trial.get("enabled"), "portal.apiAuthentication.enabled")
        if trial["enabled"] and not separated:
            _fail("portal.apiAuthentication", "requires the dedicated API, Tools and Credential registrations")
    if not separated:
        return
    login = authentication.get("credentialLogin", {})
    credential = login.get("identity", {})
    tools = next((p for p in providers if p["name"] == "graph-delegated"), {})
    portal_app = portal.get("authentication", {})
    if (api.get("requiredScope") != "Gateway.Invoke" or tools.get("identityProvider") != "aad"
            or tools.get("oauthTenantId") != config["entraTenantId"]
            or credential.get("tenantId") != config["entraTenantId"] or not credential.get("clientId")):
        _fail("gateway.authentication", "requires the dedicated API, Tools and Credential registrations in the authentication tenant")
    clients = [api["audience"], tools["clientId"], credential["clientId"]]
    if portal_app:
        if portal_app["tenantId"] != config["entraTenantId"]:
            _fail("portal.authentication.tenantId", "must match the authentication tenant")
        clients.append(portal_app["clientId"])
    if len(set(value.casefold() for value in clients)) != len(clients):
        _fail("gateway.authentication", "requires a distinct client ID for each application")
    if tools["clientSecretFile"] == credential["clientSecretFile"]:
        _fail("credentialProviders.graphDelegated.clientSecretFile", "Tools and Credential must use separate protected files")
    if portal["certificate"]["certificateName"] == "gateway-obo":
        _fail("portal.certificate.certificateName", "must differ from the dedicated Gateway signing key")
    portal.setdefault("apiAuthentication", {"enabled": True})


def _read_configuration_source(source: Path) -> dict:
    """JSONファイルを読み、オブジェクト形式であることを確認する。"""
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("configuration", "cannot read a valid JSON configuration file")
    return _object(raw, "configuration")


def _file_reference_fields(raw: dict):
    """設定でファイル入力と定めた欄だけを列挙する。URLやAzureのIDは含めない。"""
    for section, keys in (
        ("management", ("sshPublicKeyFile",)),
        ("firewallTlsInspection", ("rootCaCertificateFile",)),
    ):
        settings = raw.get(section)
        if isinstance(settings, dict):
            for key in keys:
                if key in settings:
                    yield settings, key, f"{section}.{key}"
    providers = raw.get("credentialProviders")
    if isinstance(providers, dict):
        standard = providers.get("graphDelegated")
        if isinstance(standard, dict) and "clientSecretFile" in standard:
            yield standard, "clientSecretFile", "credentialProviders.graphDelegated.clientSecretFile"
        additional = providers.get("additional", [])
        if isinstance(additional, list):
            for i, provider in enumerate(additional):
                if isinstance(provider, dict) and "clientSecretFile" in provider:
                    yield provider, "clientSecretFile", f"credentialProviders.additional[{i}].clientSecretFile"
    if isinstance(providers, list):
        for i, provider in enumerate(providers):
            if isinstance(provider, dict) and "clientSecretFile" in provider:
                yield provider, "clientSecretFile", f"credentialProviders[{i}].clientSecretFile"
    identity = raw
    for section in ("gateway", "authentication", "credentialLogin", "identity"):
        identity = identity.get(section) if isinstance(identity, dict) else None
    if isinstance(identity, dict) and "clientSecretFile" in identity:
        yield identity, "clientSecretFile", "gateway.authentication.credentialLogin.identity.clientSecretFile"


def _resolve_file_references(raw: dict, base_directory: Path) -> dict:
    """各JSONで宣言されたファイル参照を、そのJSONの親フォルダー基準で解決する。"""
    result = copy.deepcopy(raw)
    for parent, key, label in _file_reference_fields(result):
        parent[key] = _secret_path(parent[key], label, base_directory)
    return result


def configuration_for_file(raw: dict, *, base_directory: Path, output_directory: Path) -> dict:
    """合成設定を保存する前に、ファイル参照を出力JSONの場所からの相対パスに戻す。

    読込・検査中は絶対パスを使い、入力元が違う設定を取り違えないようにする。
    そのままJSONへ保存すると利用者の端末名まで残るため、保存するコピーだけを
    相対化する。元の辞書、APIのURL、AzureのリソースID、秘密ファイルの内容は
    変更・読出ししない。存在しないSSH鍵の参照先も文字列として扱い、生成しない。
    """
    source = Path(base_directory).expanduser().resolve()
    destination = Path(output_directory).expanduser().resolve()
    result = _resolve_file_references(raw, source)
    for parent, key, label in _file_reference_fields(result):
        try:
            parent[key] = Path(os.path.relpath(parent[key], destination)).as_posix()
        except ValueError:
            _fail(label, "must be on the same filesystem drive as the output configuration")
    # エディター用schemaとparamsのconfig参照も、ローカルファイルなら同じ規則。
    # 公開JSON Schemaのhttps URLはファイルパスへ変換しない。
    for key in ("$schema", "configFile"):
        value = result.get(key)
        if isinstance(value, str) and value and "://" not in value:
            target = _secret_path(value, key, source)
            try:
                result[key] = Path(os.path.relpath(target, destination)).as_posix()
            except ValueError:
                _fail(key, "must be on the same filesystem drive as the output configuration")
    return result


def _merge_configuration(base: dict, overrides: dict) -> dict:
    """標準configへ企業paramsを再帰的に重ね、配列は部分追記せず全体を置き換える。"""
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_configuration(result[key], value)
        else:
            # 配列は利用者の選択全体を表す。[]で標準の要素を明示的に無効化できる。
            result[key] = copy.deepcopy(value)
    return result


def _reject_config_environment_inputs(standard: dict) -> None:
    """分離configの企業固有項目とBicep固定項目を、値やparams上書きによらず拒否する。

    合成済み内部JSONは分離前の全体を持つため、この境界はconfigFileから
    読む標準configだけに適用します。実値はエラーへ含めません。
    """
    _reject_fixed_initial_inputs(standard, "config")
    # 共有する親が不正型だと、paramsのobjectによる置換で標準動作が消える。
    # マージ前に各sourceの型を検査し、暗黙の既定値への切替を防ぐ。
    parents = {path[:length] for path in PARAMETER_OWNED_PATHS + FIXED_INITIAL_PATHS
               for length in range(1, len(path))}
    for path in sorted(parents, key=lambda item: (len(item), item)):
        parent = standard
        for key in path[:-1]:
            if key not in parent:
                break
            parent = parent[key]
        else:
            if path[-1] in parent:
                _object(parent[path[-1]], "config." + ".".join(path))
    for key in ("$schema", "$comment"):
        if key in standard:
            _string(standard[key], "config." + key, maximum=4096)
    for path in sorted(PARAMETER_OWNED_PATHS):
        parent = standard
        for key in path[:-1]:
            if not isinstance(parent, dict) or key not in parent:
                break
            parent = parent[key]
        else:
            if isinstance(parent, dict) and path[-1] in parent:
                field = ".".join(path)
                _fail("config." + field, "move this environment-specific setting to params." + field)
    authentication = standard.get("gateway", {}).get("authentication", {})
    _object(authentication, "config.gateway.authentication", {"entra", "credentialLogin"})
    if "entra" in authentication:
        _object(authentication["entra"], "config.gateway.authentication.entra", {"userLimits"})
    if "credentialLogin" in authentication:
        _object(authentication["credentialLogin"], "config.gateway.authentication.credentialLogin",
                {"providerNames", "transactionLifetimeSeconds", "loginCallsPerMinute", *SESSION_POLICY_DEFAULTS})
    if "portalApiKeys" in standard:
        _object(standard["portalApiKeys"], "config.portalApiKeys", {"verificationMaxSubscriptions"})
    gateway = standard.get("gateway", {})
    if isinstance(gateway, dict) and "grpc" in gateway:
        _fail("config.gateway", "contains unsupported field(s): grpc")
    network = standard.get("network", {})
    if isinstance(network, dict):
        unsupported = RETIRED_NETWORK_INPUTS.intersection(network)
        if unsupported:
            _fail("config.network", "contains unsupported field(s): " + ", ".join(sorted(unsupported)))
        firewall = network.get("firewall", {})
        if isinstance(firewall, dict):
            unsupported = (RETIRED_FIREWALL_INPUTS | {"dnsServers"}).intersection(firewall)
            if unsupported:
                _fail("config.network.firewall", "contains unsupported field(s): " + ", ".join(sorted(unsupported)))


def _reject_fixed_initial_inputs(value: dict, label: str) -> None:
    """廃止した初期入力を拒否し、旧値を読み飛ばした構築を防ぐ。"""
    for path in FIXED_INITIAL_PATHS:
        parent = value
        for key in path[:-1]:
            if not isinstance(parent, dict) or key not in parent:
                break
            parent = parent[key]
        else:
            if isinstance(parent, dict) and path[-1] in parent:
                _fail(label + "." + ".".join(path), "retired initial input; review the prior setting before using fixed material")


def load_configuration_source(path: Path | str) -> dict:
    """paramsが参照するconfigと企業固有入力を合成し、未正規化の設定を返す。

    configFileはparamsの場所を基準に読む。各ファイル内の相対パスは合成前に
    宣言元基準で解決するため、出力先や実行ディレクトリーが変わっても参照先がぶれない。
    従来の合成済みJSONも読めるが、企業paramsの入力範囲は小さく制限する。
    """
    source = Path(path).expanduser().resolve()
    return _compose_configuration_source(source, _read_configuration_source(source))


def _compose_configuration_source(source: Path, raw: dict) -> dict:
    if "configFile" not in raw:
        return _resolve_file_references(raw, source.parent)
    _reject_fixed_initial_inputs(raw, "params")
    _object(raw, "parameters", PARAMETER_KEYS)
    for key in ("$schema", "$comment"):
        if key in raw:
            _string(raw[key], "params." + key, maximum=4096)
    for section, allowed in PARAMETER_SECTION_KEYS.items():
        if section in raw:
            _object(raw[section], section, allowed)
    if isinstance(raw.get("network"), dict) and "firewall" in raw["network"]:
        _object(raw["network"]["firewall"], "network.firewall", PARAMETER_FIREWALL_KEYS)
    gateway = raw.get("gateway", {})
    if isinstance(gateway, dict):
        for key, allowed in PARAMETER_GATEWAY_NESTED_KEYS.items():
            if key in gateway:
                _object(gateway[key], "gateway." + key, allowed)
        authentication = gateway.get("authentication", {})
        if isinstance(authentication, dict):
            if "entra" in authentication:
                _object(authentication["entra"], "gateway.authentication.entra", {"audience", "obo"})
            if "credentialLogin" in authentication:
                login = _object(authentication["credentialLogin"], "gateway.authentication.credentialLogin", {"identity"})
                if "identity" in login:
                    _object(login["identity"], "gateway.authentication.credentialLogin.identity", {"clientId", "clientSecretFile"})
    if "portalApiKeys" in raw:
        _object(raw["portalApiKeys"], "portalApiKeys", {"platformGroupId", "groupNamePattern"})
    reference = _string(raw["configFile"], "configFile", maximum=4096)
    if "://" in reference or "\x00" in reference or Path(reference).suffix.lower() != ".json":
        _fail("configFile", "must reference a local JSON config file")
    config_source = (source.parent / Path(reference).expanduser()).resolve()
    if config_source == source:
        _fail("configFile", "cannot reference the parameters file itself")
    standard = _read_configuration_source(config_source)
    if "configFile" in standard:
        _fail("configFile", "referenced config must not include another config file")
    _object(standard, "config", TOP_KEYS)
    _reject_config_environment_inputs(standard)
    parameters = copy.deepcopy(raw)
    parameters.pop("configFile")
    # 相対参照を宣言元ごとに先に解決する。合成後に1つの基準で解決すると、
    # configとparamsが別フォルダーにある場合に違う秘密ファイルを指し得る。
    resolved_standard = _resolve_file_references(standard, config_source.parent)
    resolved_parameters = _resolve_file_references(parameters, source.parent)
    from .credential_provider_inputs import compose_credential_providers
    provider_options = resolved_standard.pop("credentialProviders", None)
    provider_registrations = resolved_parameters.pop("credentialProviders", None)
    effective_tenant = entra_tenant_id(resolved_parameters)
    # 未正規化の部分設定も読める契約を保ち、片側だけの登録は専用合成で拒否する。
    providers = (compose_credential_providers(
        provider_options, provider_registrations,
        effective_tenant, fail=_fail,
    ) if provider_options is not None or provider_registrations is not None else [])
    combined = _merge_configuration(resolved_standard, resolved_parameters)
    if effective_tenant is not None or "entraTenantId" in resolved_parameters:
        combined["entraTenantId"] = effective_tenant
    combined["credentialProviders"] = providers
    from .initial_configuration import apply_initial_configuration
    return apply_initial_configuration(combined, fail=_fail)


def load_configuration(path: Path | str, *, validate_parameters: Callable[[dict], None] | None = None) -> dict:
    """ファイル読込・params/config合成・全体検査を1つの入口として実行する。"""
    source = Path(path).expanduser().resolve()
    raw = _read_configuration_source(source)
    config = normalize_configuration(_compose_configuration_source(source, raw))
    if validate_parameters is not None and "configFile" in raw:
        validate_parameters(config)
    return config


def _reject_ignored_material_overrides(configuration: dict) -> None:
    """旧内部JSONの独自資材設定を、固定Bicepが黙って無視することを防ぐ。"""
    def reject(path):
        _fail(path, "retired material override; review the prior setting before using fixed Bicep material")

    gateway = configuration.get("gateway", {})
    if gateway.get("existingWafPolicyId", "") != "":
        reject("gateway.existingWafPolicyId")
    for section, keys in (
        ("monitoring", ("auditAllowedIpAddresses", "auditReaders")),
        ("authorization", ("projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders")),
    ):
        source = configuration.get(section, {})
        for key in keys:
            if key in source and source[key] != []:
                reject(section + "." + key)
    network = configuration.get("network", {})
    firewall = network.get("firewall", {})
    if "bypassTrafficSettings" in firewall and firewall["bypassTrafficSettings"] != []:
        reject("network.firewall.bypassTrafficSettings")
    spaces = network.get("addressSpaces", [])
    snat = ["IANAPrivateRanges"]
    for key, accepted in (
        ("snatPrivateRanges", (snat, [*snat, *spaces])),
        ("idpsPrivateRanges", ([], RFC1918_CIDRS, [*RFC1918_CIDRS, *spaces])),
    ):
        if key not in firewall:
            continue
        value = firewall[key]
        # アドレス集合の順序・重複だけの違いは、独自通信条件として扱いません。
        if (not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                or not any(set(value) == set(candidate) for candidate in accepted)):
            reject("network.firewall." + key)


def _idps_private_ranges(network: dict) -> list[str]:
    """App GatewayのアドレスだけをIDPSの内部分類から外す。"""
    app_gateway = ipaddress.IPv4Network(network["subnetPrefixes"]["agw"])
    internal_ranges = ipaddress.collapse_addresses(
        ipaddress.IPv4Network(cidr) for cidr in (*RFC1918_CIDRS, *network["addressSpaces"])
    )
    remaining = []
    for prefix in internal_ranges:
        if prefix.subnet_of(app_gateway):
            continue
        if app_gateway.subnet_of(prefix):
            remaining.extend(prefix.address_exclude(app_gateway))
        else:
            remaining.append(prefix)
    return [str(prefix) for prefix in ipaddress.collapse_addresses(remaining)]


def build_bicep_configuration(configuration: dict) -> dict:
    """Bicepへ渡す公開設定を作り、秘密ファイルの参照先を取り除く。

    秘密の内容は別のsecure入力として渡す。この分離により、設定の表示や
    配備状態の保存へ秘密値・ローカル秘密パスが紛れ込むのを防ぐ。
    """
    _reject_ignored_material_overrides(configuration)
    result = copy.deepcopy(configuration)
    network = result.get("network", {})
    firewall = network.get("firewall", {})
    firewall.pop("idpsPrivateRanges", None)
    if firewall.get("sku") == "Premium":
        network["generatedIdpsPrivateRanges"] = _idps_private_ranges(network)
    result["apiDefinitions"] = with_catalog_descriptions(configuration)
    # 標準APIの接続先はGateway配備時に解決する。基盤は公開経路と本文形式だけを
    # 先に受け取り、空の公開API配列でも適切なWAF・監査設定を準備する。
    from standard_apis import infrastructure_api_definitions
    result['infrastructureApiDefinitions'] = infrastructure_api_definitions(configuration['apiDefinitions'])
    # エディター用のschema/commentはローカルに保持し、取得やARMへの送信はしない。
    result.pop("$schema", None)
    result.pop("$comment", None)
    result.pop("codex", None)
    if entra_app_separation_enabled(configuration):
        result["entraRolloutEnabled"] = True
    result["management"].pop("sshPublicKeyFile", None)
    result.pop("firewallTlsInspection", None)
    for provider in result["credentialProviders"]:
        provider.pop("clientSecretFile", None)
    identity = result.get("gateway", {}).get("authentication", {}).get("credentialLogin", {}).get("identity")
    if identity:
        identity.pop("clientSecretFile", None)
    from .credential_pools import physical_providers, logical_profiles, broker_session_settings
    result["credentialPhysicalProviders"] = physical_providers(result)
    result["credentialProfiles"] = logical_profiles(result)
    result["credentialBrokerSession"] = broker_session_settings(result)
    from .firewall_destinations import generated_apim_service_fqdns
    result["generatedApimServiceFqdns"] = generated_apim_service_fqdns(result)
    return result


def _read_protected_file(raw_path: str, label: str, *, binary: bool = False) -> bytes | str:
    """所有者だけが読める通常ファイルを開き、共有権限やリンクによる参照を拒否する。"""
    path = Path(raw_path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            _fail(label, "must reference a regular protected file")
        if os.name != "nt" and info.st_mode & 0o077:
            _fail(label, "file must not be accessible to group or others (use chmod 600)")
        return path.read_bytes() if binary else path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        _fail(label, "cannot read the protected input file")


def load_ssh_public_key(raw_path: str) -> str:
    """OpenSSH公開鍵の形式・鍵長を検査する。秘密鍵は読み込む対象にしない。"""
    label = "management.sshPublicKeyFile"
    path = Path(raw_path)
    try:
        if not path.is_file():
            _fail(label, "must reference a regular public-key file")
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        _fail(label, "cannot read the SSH public-key file")
    fields = value.split()
    if len(value.splitlines()) != 1 or len(fields) < 2 or fields[0] not in ("ssh-rsa", "ssh-ed25519"):
        _fail(label, "must contain one RSA (2048 bits or more) or Ed25519 OpenSSH public key; private keys are not accepted")
    try:
        blob = base64.b64decode(fields[1], validate=True)
        offset = 0
        def part() -> bytes:
            """OpenSSHの長さ付きバイナリー項目を1つ読み、境界超過を拒否する。"""
            nonlocal offset
            if offset + 4 > len(blob):
                raise ValueError
            size = int.from_bytes(blob[offset:offset + 4], "big")
            offset += 4
            if size > len(blob) - offset:
                raise ValueError
            result = blob[offset:offset + size]
            offset += size
            return result
        algorithm = part().decode("ascii")
        if algorithm != fields[0]:
            raise ValueError
        if algorithm == "ssh-rsa":
            exponent, modulus = part(), part()
            if int.from_bytes(exponent, "big") < 3 or int.from_bytes(modulus, "big").bit_length() < 2048:
                raise ValueError
        elif len(part()) != 32:
            raise ValueError
        if offset != len(blob):
            raise ValueError
    except (ValueError, UnicodeError):
        _fail(label, "must contain a valid RSA (2048 bits or more) or Ed25519 OpenSSH public key")
    return " ".join(fields)


def validate_supported_certificate(certificate: Any) -> None:
    """Azure Public DNSのDNS-01発行に使う入力だけを受け付ける。"""
    _object(certificate, "certificate", {
        "mode", "certificateName", "contactEmail", "directory", "plugin", "renewBeforeDays",
        "checkEveryHours", "moduleVersion", "dnsSleepSeconds", "validationTimeoutSeconds",
        "minimumValidDays", "domainNames", "azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId",
    })
    if certificate.get("mode") != "dnsPlugin":
        _fail("certificate.mode", "DNS-01 issuance requires dnsPlugin mode")
    if certificate.get("plugin") != "Azure":
        _fail("certificate.plugin", "Azure Public DNS is required; prepare its public authoritative zone before azd up")
    validate_azure_dns_identity(certificate)


def validate_azure_dns_identity(certificate: dict, *, require_complete: bool = True) -> None:
    """Azure DNSは事前作成したUAMIと公開Subscription参照だけを使う。"""
    if require_complete or "azureDnsSubscriptionId" in certificate:
        _guid(certificate.get("azureDnsSubscriptionId"), "certificate.azureDnsSubscriptionId")
    if not require_complete and "azureDnsManagedIdentityResourceId" not in certificate:
        return
    _user_assigned_identity_id(certificate.get("azureDnsManagedIdentityResourceId"),
                              "certificate.azureDnsManagedIdentityResourceId")


def _user_assigned_identity_id(value: Any, path: str) -> str:
    identity = _string(value, path, maximum=2048)
    match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
                         r"Microsoft\.ManagedIdentity/userAssignedIdentities/([^/]+)", identity, re.IGNORECASE)
    if not match or not re.fullmatch(r"[\w().-]{1,90}", match[2]) or not re.fullmatch(r"[\w-]{3,128}", match[3]):
        _fail(path, "must identify an existing user-assigned managed identity")
    _guid(match[1], path + ".subscriptionId")
    return identity


def _firewall_tls_inspection(value: Any, base: Path) -> dict:
    label = "firewallTlsInspection"
    settings = _object(value, label, {"managedIdentityResourceId", "intermediateCaSecretId", "rootCaCertificateFile"})
    return {
        "managedIdentityResourceId": _string(settings.get("managedIdentityResourceId"),
                                             label + ".managedIdentityResourceId", maximum=2048),
        "intermediateCaSecretId": _string(settings.get("intermediateCaSecretId"), label + ".intermediateCaSecretId"),
        "rootCaCertificateFile": _secret_path(settings.get("rootCaCertificateFile"), label + ".rootCaCertificateFile", base),
    }


def load_firewall_tls_inspection(configuration: dict) -> dict:
    """BicepへFirewallの参照と公開ルートCAを渡す。秘密鍵は読み込まない。"""
    label = "firewallTlsInspection.rootCaCertificateFile"
    settings = _firewall_tls_inspection(configuration.get("firewallTlsInspection"), Path.cwd())
    _user_assigned_identity_id(settings["managedIdentityResourceId"], "firewallTlsInspection.managedIdentityResourceId")
    secret = settings["intermediateCaSecretId"]
    try:
        uri = urlparse(secret)
        valid = (uri.scheme == "https" and uri.geturl() == secret and not (uri.params or uri.query or uri.fragment)
                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,22}[A-Za-z0-9]\.vault\.azure\.net", uri.netloc)
                 and re.fullmatch(r"/secrets/[A-Za-z0-9-]{1,127}/[A-Fa-f0-9]{32}", uri.path))
    except ValueError:
        valid = False
    if not valid:
        _fail("firewallTlsInspection.intermediateCaSecretId", "must be a versioned Azure Key Vault secrets URI")
    certificate = _read_protected_file(settings.pop("rootCaCertificateFile"), label, binary=True)
    try:
        if certificate.startswith(b"-----BEGIN CERTIFICATE-----"):
            pem = certificate.decode("ascii").strip()
            if not re.fullmatch(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----", pem):
                raise ValueError()
            certificate = ssl.PEM_cert_to_DER_cert(pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=certificate)
        authorities = context.get_ca_certs()
        if len(authorities) != 1 or context.get_ca_certs(binary_form=True) != [certificate]:
            raise ValueError()
        authority = authorities[0]
        if (authority["issuer"] != authority["subject"]
                or not ssl.cert_time_to_seconds(authority["notBefore"]) <= time.time()
                < ssl.cert_time_to_seconds(authority["notAfter"])):
            raise ValueError()
    except (ValueError, ssl.SSLError, KeyError):
        _fail(label, "must contain one currently valid public root CA certificate in DER or PEM")
    return {**settings, "rootCaData": base64.b64encode(certificate).decode("ascii")}


def _validate_dns_provider_signature(plugin: str, arguments: dict) -> None:
    """標準3事業者の認証形式を検査し、別事業者の古い入力を再利用しない。

    一般的な型検査が済んだ後だけ呼びます。未知のプラグインは従来どおり
    公式仕様に従う型付きJSONを受け取り、ここで他社用と推測しません。
    標準事業者では公式の複数認証方式を保ち、既知の別事業者の引数混入と
    必須認証情報の不足・型違いを専用例外にします。秘密値は例外に含めません。
    仕様: https://poshac.me/docs/v4/Plugins/{Cloudflare,Route53,Azure}/
    """
    if plugin not in ("Cloudflare", "Route53", "Azure"):
        return
    # PowerShellの引数名は大文字小文字を区別しません。同名の二重指定は
    # 手書きJSONの不備として扱い、自動再入力の対象にはしません。
    by_name = {key.lower(): value for key, value in arguments.items()}
    if len(by_name) != len(arguments):
        _fail("certificate.pluginArgumentsFile", "must not repeat DNS argument names with different letter casing")
    known = {
        "Cloudflare": {"cftoken", "cftokeninsecure", "cfauthemail", "cfauthkeysecure", "cfauthkey"},
        "Route53": {"r53accesskey", "r53secretkey", "r53secretkeyinsecure", "r53profilename", "r53useiamrole"},
        "Azure": {"azsubscriptionid", "aztenantid", "azappcred", "azappusername", "azapppasswordinsecure",
                  "azcertthumbprint", "azcertpfx", "azpfxpasssecure", "azpfxpass", "azaccesstoken",
                  "azaccesstokensecure", "azuseimds", "azarcagentapiversion", "azenvironment"},
    }

    def mismatch() -> None:
        raise DnsProviderMismatchError("certificate.pluginArgumentsFile: saved DNS authentication arguments do not match the selected DNS provider; prepare credentials for that provider") from None

    if any(set(by_name) & keys for name, keys in known.items() if name != plugin):
        mismatch()

    def typed(key: str, kind: str) -> bool:
        return by_name.get(key, {}).get("type") == kind

    def nonzero_uuid(value: str) -> bool:
        try:
            return bool(uuid.UUID(value).int)
        except (ValueError, AttributeError, TypeError):
            return False

    def matches(types: dict[str, str]) -> bool:
        # 必須となる認証形式を確認します。公式pluginの任意引数は版によって
        # 増えるため、同じ事業者の追加引数があるだけでは推測で拒否しません。
        return all(typed(key, kind) for key, kind in types.items())

    if plugin == "Cloudflare":
        forms = ({"cftoken": "secureString"},
                 {"cfauthemail": "string", "cfauthkeysecure": "secureString"},
                 {"cftokeninsecure": "string"}, {"cfauthemail": "string", "cfauthkey": "string"})
        if not any(matches(form) for form in forms):
            mismatch()
        return
    if plugin == "Route53":
        forms = ({"r53accesskey": "string", "r53secretkey": "secureString"},
                 {"r53accesskey": "string", "r53secretkeyinsecure": "string"},
                 {"r53profilename": "string"})
        iam_role = typed("r53useiamrole", "boolean") and by_name["r53useiamrole"]["value"] is True
        if not (any(matches(form) for form in forms) or iam_role):
            mismatch()
        return
    # Azureは全認証方式でDNSゾーンのSubscriptionを指定します。SPの方式では
    # Tenantと資格情報の型を検査します。MIのIMDS方式にclient secretは要求しません。
    if not typed("azsubscriptionid", "string") or not nonzero_uuid(by_name["azsubscriptionid"]["value"]):
        mismatch()
    forms = ({"aztenantid": "string", "azappcred": "credential"},
             {"azaccesstoken": "string"}, {"azaccesstokensecure": "secureString"},
             {"aztenantid": "string", "azappusername": "string", "azcertthumbprint": "string"},
             {"aztenantid": "string", "azappusername": "string", "azcertpfx": "string"},
             {"aztenantid": "string", "azappusername": "string", "azapppasswordinsecure": "string"})
    imds = typed("azuseimds", "boolean") and by_name["azuseimds"]["value"] is True
    if not (any(matches(form) for form in forms) or imds):
        mismatch()
    if "aztenantid" in by_name and (not typed("aztenantid", "string") or not nonzero_uuid(by_name["aztenantid"]["value"])):
        mismatch()
    if "azenvironment" in by_name and (not typed("azenvironment", "string") or by_name["azenvironment"]["value"] not in ("AzureCloud", "AzureUSGovernment", "AzureGermanCloud", "AzureChinaCloud")):
        mismatch()


def load_dns_plugin_arguments(certificate: dict) -> str:
    """DNS資格情報だけを検査し、VMへ安全に渡すための内部JSONを返す。

    初回入力の準備コマンドからも使えるよう、SSH鍵や他の秘密ファイルを読みません。
    プラグイン固有の型付き値を保ち、通常の文字列だけをstring型へ揃えます。
    返り値は秘密を含むため、画面・ログ・状態ファイルへ出してはいけません。
    不正な入力のエラーにも、資格情報の値やJSON本文を含めません。
    """
    protected = _read_protected_file(certificate["pluginArgumentsFile"], "certificate.pluginArgumentsFile")
    try:
        args = json.loads(protected)
    except json.JSONDecodeError:
        _fail("certificate.pluginArgumentsFile", "must contain a JSON object")
    if not isinstance(args, dict) or not args:
        _fail("certificate.pluginArgumentsFile", "must contain non-empty DNS plugin arguments")
    for key, value in args.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key):
            _fail("certificate.pluginArgumentsFile", "plugin argument names must be identifiers")
        if isinstance(value, str):
            if not value:
                _fail("certificate.pluginArgumentsFile", "plugin argument values must not be empty")
            args[key] = {"type": "string", "value": value}
            continue
        if not isinstance(value, dict) or value.get("type") not in ("secureString", "credential", "string", "boolean", "integer", "stringArray"):
            _fail("certificate.pluginArgumentsFile", "arguments must be typed DNS plugin values")
        fields = {"type", "username", "password"} if value["type"] == "credential" else {"type", "value"}
        if set(value) != fields:
            _fail("certificate.pluginArgumentsFile", "typed plugin arguments have missing or unsupported fields")
        if value["type"] in ("secureString", "credential", "string"):
            valid = all(isinstance(value[field], str) and value[field] for field in fields - {"type"})
        elif value["type"] == "boolean":
            valid = isinstance(value["value"], bool)
        elif value["type"] == "integer":
            valid = isinstance(value["value"], int) and not isinstance(value["value"], bool)
        else:
            valid = isinstance(value["value"], list) and all(isinstance(item, str) and item for item in value["value"])
        if not valid:
            _fail("certificate.pluginArgumentsFile", "DNS plugin argument has an invalid typed value")
    _validate_dns_provider_signature(certificate["plugin"], args)
    return json.dumps(args, separators=(",", ":"))


def load_credential_provider_secrets(configuration: dict) -> dict:
    """CM用OAuthクライアントのsecretだけを読み、本人確認用資格情報には触れない。"""
    result = {}
    for provider in configuration["credentialProviders"]:
        value = _read_protected_file(provider["clientSecretFile"], "credentialProviders.clientSecretFile")
        if not value:
            _fail("credentialProviders.clientSecretFile", "must not be empty")
        result[provider["name"]] = value
    return result


def load_credential_identity_secret(configuration: dict) -> str:
    """独立したOIDC本人確認secretだけを実行直前に読み、CM資格情報へ混ぜない。"""
    identity = configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin", {}).get("identity")
    if not identity:
        return ""
    value = _read_protected_file(identity["clientSecretFile"], "credentialLogin.identity.clientSecretFile")
    if not value:
        _fail("credentialLogin.identity.clientSecretFile", "must not be empty")
    return value


def load_secret_inputs(configuration: dict) -> dict:
    """構築直前に必要な秘密・SSH公開鍵を読み、メモリー内のsecure入力として返す。

    返り値には接続用の秘密が含まれるため、ログ・標準出力・状態記録へ
    そのまま保存してはいけない。公開設定のrenderとは別の呼出しにしている。
    """
    # ここから先だけが秘密ファイルを開く段階。返り値をJSON renderや状態記録へ
    # 直接出さず、構築hookからsecureパラメーターとして渡す。
    validate_supported_certificate(configuration["certificate"])
    public_key = load_ssh_public_key(configuration["management"]["sshPublicKeyFile"])
    secrets: dict[str, Any] = {"sshPublicKey": public_key,
        "firewallTlsInspection": load_firewall_tls_inspection(configuration),
        "credentialProviderSecrets": load_credential_provider_secrets(configuration)}
    # Azure DNS is authenticated by the dedicated certificate Job identity.
    return secrets


def main(argv: list[str] | None = None) -> int:
    """CLIのvalidate/renderを実行する。出力するのは公開設定と検査結果だけである。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("--config", required=True, type=Path, help="Parameters JSON with configFile, or an internally generated combined JSON.")
    parser.add_argument("--output", type=Path, help="Public Bicep configuration JSON destination (render only).")
    args = parser.parse_args(argv)
    try:
        config = load_configuration(args.config)
        if args.command == "render":
            if args.output is None:
                parser.error("render requires --output")
            rendered = json.dumps(build_bicep_configuration(config), ensure_ascii=False, indent=2) + "\n"
            from .paths import private_parent
            try:
                output = private_parent(args.output)
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                    os.fchmod(destination.fileno(), 0o600)
                    destination.write(rendered)
            except (OSError, ValueError):
                _fail("output", "cannot write a protected runtime output")
        print(json.dumps({"valid": True, "resourceGroupNames": config["resourceGroupNames"], "apiPublicationStatus": config["apiPublicationStatus"]}, ensure_ascii=False))
        return 0
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
