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
import stat
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

# ファイルを直接実行しても、同じdeployment.helpersパッケージから読み込む。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "deployment.helpers"

from .catalog_descriptions import with_catalog_descriptions
from .apim_naming import apim_instance_id
from .credential_login import SESSION_POLICY_DEFAULTS, SESSION_POLICY_BOUNDS, MAX_TRANSACTION_LIFETIME_SECONDS


class ConfigurationError(ValueError):
    """設定の欠落・矛盾を、秘密値を含まないメッセージで伝える例外。"""


class DnsProviderMismatchError(ConfigurationError):
    """JSONの破損と区別して、選択したDNS事業者と資格情報形式の不一致を示す。"""


RG_KINDS = ("id", "nw", "monitor", "svc", "maint")
SUBNET_KINDS = ("agw", "firewall", "apim", "pep", "management", "aca")
TOP_KEYS = {
    "$schema", "$comment",
    "schemaVersion", "productName", "environmentName", "subscriptionId",
    "azureTenantId", "entraTenantId", "location", "foundryLocation", "apiCenterLocation", "tags", "network",
    "services", "gateway", "management", "monitoring", "certificate", "authorization",
    "apiDefinitions", "publishApis", "credentialProviders", "portal", "credentialBroker", "portalAuthentication", "portalApiKeys",
}
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
API_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+\Z")
REGION = re.compile(r"[a-z][a-z0-9]+\Z")
SECRET_VALUE_KEYS = {"adminPassword", "password", "pfxPassword", "cloudflareToken", "apiToken", "clientSecret", "accessToken", "primaryKey", "secondaryKey"}

# paramsは各企業が確認する入力とIP・IP条件を含む規則に絞る。基盤動作は参照先configへ置き、
# 大量の設定から変更必須項目を探さずに済むようにする。
PARAMETER_KEYS = {
    "$schema", "$comment", "configFile", "schemaVersion", "productName",
    "environmentName", "subscriptionId", "azureTenantId", "entraTenantId", "services", "gateway",
    "management", "monitoring", "certificate", "authorization", "apiDefinitions",
    "credentialProviders", "publishApis", "portal", "portalApiKeys", "network", "location", "foundryLocation", "apiCenterLocation", "tags",
}
# IP条件と不可分な規則配列も、環境ごとにparamsで全体を選びます。
PARAMETER_NETWORK_IP_KEYS = {
    "addressSpaces", "subnetPrefixes", "clientAddressPrefixes", "operatorAddressPrefixes",
    "dnsServers", "ntpAddressPrefixes", "managementAdditionalSecurityRules",
}
PARAMETER_FIREWALL_IP_KEYS = {
    "dnsServers", "snatPrivateRanges", "idpsPrivateRanges", "bypassTrafficSettings",
    "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
    "apimAdditionalApplicationRules", "managementAdditionalApplicationRules",
}
PARAMETER_SECTION_KEYS = {
    "portal": {"publicHostName"},
    "network": PARAMETER_NETWORK_IP_KEYS | {"firewall", "ntpFqdns"},
    "services": {"publisherName", "publisherEmail", "apimInstanceId"},
    "gateway": {"publicHostName", "existingWafPolicyId", "authentication", "grpc", "rateLimits"},
    "management": {"adminUsername", "sshPublicKeyFile", "loginAssignments"},
    "monitoring": {"operatorEmailAddresses", "auditAllowedIpAddresses", "auditReaders"},
    "certificate": {"contactEmail", "domainNames", "plugin", "azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"},
    "authorization": {"projectAdministrators", "projectDevelopers", "foundryReaders", "catalogReaders"},
}


# 企業固有の情報はparamsだけが所有します。親の一部だけがparamsの項目
# （grpcの公開名、rateLimitsの契約別上書き等）は、最下層のパスで明示します。
# 空配列や空文字でもconfigへ置けば拒否し、他企業の値を黙って継承させません。
PARAMETER_FIREWALL_KEYS = PARAMETER_FIREWALL_IP_KEYS | {"apimAllowedFqdns", "managementAllowedFqdns"}
PARAMETER_GATEWAY_NESTED_KEYS = {
    "authentication": {"subscriptionKeyHeader", "credentialLogin"},
    "grpc": {"publicHostName"},
    "rateLimits": {"subscriptionKey", "application"},
}
PARAMETER_OWNED_PATHS = (
    *((key,) for key in ("schemaVersion", "productName", "environmentName", "subscriptionId", "azureTenantId", "entraTenantId",
                         "location", "foundryLocation", "apiCenterLocation", "tags",
                         "authorization", "credentialProviders", "apiDefinitions", "publishApis", "portalApiKeys")),
    *(("network", key) for key in PARAMETER_NETWORK_IP_KEYS | {"ntpFqdns"}),
    *(("network", "firewall", key) for key in PARAMETER_FIREWALL_KEYS),
    *((section, key) for section in ("services", "management", "monitoring", "certificate", "portal")
      for key in PARAMETER_SECTION_KEYS[section]),
    ("gateway", "publicHostName"), ("gateway", "existingWafPolicyId"), ("gateway", "authentication"),
    ("gateway", "grpc", "publicHostName"),
    *(("gateway", "rateLimits", kind, "overrides") for kind in ("subscriptionKey", "application")),
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


def _principals(value: Any, path: str, *, login: bool = False) -> list[dict]:
    """RBAC対象の公開ID・種類・重複を検査し、VMログイン時だけ役割も確認する。"""
    result = _list(value, path)
    seen = set()
    for i, principal in enumerate(result):
        p = f"{path}[{i}]"
        _object(principal, p, {"principalId", "principalType", "role"} if login else {"principalId", "principalType"})
        principal["principalId"] = _guid(principal.get("principalId"), p + ".principalId")
        _choice(principal.get("principalType"), p + ".principalType", ("User", "Group", "ServicePrincipal"))
        if login:
            _choice(principal.get("role"), p + ".role", ("User", "Administrator"))
        if principal["principalId"] in seen:
            _fail(path, "contains duplicate principals")
        seen.add(principal["principalId"])
    return result


def _network(raw: Any) -> dict:
    """VNet・サブネット・NSG・ルート・FW設定を検査し、省略可能な標準値を補う。

    アドレス範囲の包含と重なり、ゾーン、DNS、Standard/Premiumの機能差など、
    個々の値だけでは分からない組合せもここで確認する。
    """
    n = _object(copy.deepcopy(raw), "network", {
        "addressSpaces", "subnetPrefixes", "clientAddressPrefixes", "operatorAddressPrefixes",
        "operatorProtocol", "operatorPorts", "dnsServers", "bgpRoutePropagation", "firewall",
        "appGatewayZones", "ntpAddressPrefixes", "ntpFqdns", "dnsTtl", "apimKeyVaultAccessEnabled",
        "certificatePublicProbeEnabled", "managementAdditionalSecurityRules",
    })
    spaces = _cidrs(n.get("addressSpaces"), "network.addressSpaces", minimum=1)
    prefixes = _object(n.get("subnetPrefixes"), "network.subnetPrefixes", set(SUBNET_KINDS))
    if set(prefixes) != set(SUBNET_KINDS):
        _fail("network.subnetPrefixes", "must define exactly agw, firewall, apim, pep, management and aca")
    # まずVNet内の住所の区切りを検査する。各サブネットが個別に正しいCIDRでも、
    # 互いに重なっていたりVNet外だったりすれば、後続リソースを配置できない。
    networks = [ipaddress.ip_network(item) for item in spaces]
    if any(left.overlaps(right) for i, left in enumerate(networks) for right in networks[i + 1:]):
        _fail("network.addressSpaces", "address spaces overlap")
    subnet_networks = []
    for key in SUBNET_KINDS:
        item = _cidrs([prefixes[key]], "network.subnetPrefixes." + key, minimum=1)[0]
        subnet = ipaddress.ip_network(item)
        maximum_prefix = {"agw": 24, "pep": 24, "aca": 27}.get(key, 26)
        if subnet.prefixlen > maximum_prefix or not any(subnet.subnet_of(parent) for parent in networks):
            _fail("network.subnetPrefixes." + key, "must fit the address space and meet the designed subnet size")
        subnet_networks.append(subnet)
    if any(left.overlaps(right) for i, left in enumerate(subnet_networks) for right in subnet_networks[i + 1:]):
        _fail("network.subnetPrefixes", "subnets overlap")
    # 利用者向けの省略値は全IPv4許可。空配列[]は許可元なしの明示指定であり、
    # 「省略と同じ」と扱わない。IP許可を広げてもAPIの認証は別途必要となる。
    for key in ("clientAddressPrefixes", "operatorAddressPrefixes"):
        default = ["0.0.0.0/0"] if key == "clientAddressPrefixes" else []
        n[key] = _cidrs(n.get(key, default), "network." + key)
    n["dnsServers"] = _strings(n.get("dnsServers", []), "network.dnsServers")
    for address in n["dnsServers"]:
        try:
            if ipaddress.ip_address(address).version != 4:
                raise ValueError
        except ValueError:
            _fail("network.dnsServers", "must contain IPv4 server addresses")
    # 公開設定ではtrue=伝播、false=非伝播と読む。ARMのdisable指定への反転は
    # Bicep側で行い、利用者が否定の否定を読み解く必要をなくす。
    routes = _object(n.get("bgpRoutePropagation", {}), "network.bgpRoutePropagation", {"agw", "apim", "management"})
    n["bgpRoutePropagation"] = {key: _bool(routes.get(key, True), "network.bgpRoutePropagation." + key) for key in ("agw", "apim", "management")}
    _choice(n.setdefault("operatorProtocol", "Tcp"), "network.operatorProtocol", ("Tcp", "Udp", "*"))
    n["operatorPorts"] = _strings(n.get("operatorPorts", ["22"]), "network.operatorPorts", minimum=1)
    for key, default in (("apimKeyVaultAccessEnabled", True), ("certificatePublicProbeEnabled", True)):
        _bool(n.setdefault(key, default), "network." + key)
    _integer(n.setdefault("dnsTtl", 300), "network.dnsTtl", 1, 86400)
    _strings(n.setdefault("appGatewayZones", ["1", "2", "3"]), "network.appGatewayZones")
    _strings(n.setdefault("ntpAddressPrefixes", ["Internet"]), "network.ntpAddressPrefixes", minimum=1)
    for fqdn in _strings(n.setdefault("ntpFqdns", ["ntp.ubuntu.com"]), "network.ntpFqdns", minimum=1):
        _host(fqdn, "network.ntpFqdns")
    f = _object(n.setdefault("firewall", {}), "network.firewall", {
        "sku", "idpsMode", "threatIntelMode", "zones", "dnsServers", "idpsPrivateRanges", "signatureOverrides",
        "bypassTrafficSettings", "snatPrivateRanges", "apimAllowedFqdns",
        "managementAllowedFqdns", "apimAdditionalNetworkRules", "managementAdditionalNetworkRules",
        "apimAdditionalApplicationRules", "managementAdditionalApplicationRules",
    })
    tier = _choice(f.setdefault("sku", "Premium"), "network.firewall.sku", ("Standard", "Premium"))
    _choice(f.setdefault("threatIntelMode", "Alert"), "network.firewall.threatIntelMode", ("Alert", "Deny", "Off"))
    # IDPSはPremium専用。Standardで機能しない設定を黙って無視せず、
    # Off/空の標準値だけを受け付け、有効なIDPS設定の混入はエラーにする。
    if tier == "Premium":
        _choice(f.setdefault("idpsMode", "Alert"), "network.firewall.idpsMode", ("Alert", "Deny"))
    elif "idpsMode" in f and f["idpsMode"] not in (None, "Off"):
        _fail("network.firewall.idpsMode", "Standard does not support IDPS; omit this setting")
    else:
        f.pop("idpsMode", None)
    if tier == "Standard" and any(f.get(key) for key in ("idpsPrivateRanges", "signatureOverrides", "bypassTrafficSettings")):
        _fail("network.firewall", "IDPS configuration requires Premium")
    _strings(f.setdefault("zones", []), "network.firewall.zones")
    for key in ("apimAllowedFqdns", "managementAllowedFqdns"):
        if key in f:
            for fqdn in _strings(f[key], "network.firewall." + key):
                _host(fqdn, "network.firewall." + key, wildcard=True)
    for key in ("idpsPrivateRanges", "signatureOverrides", "bypassTrafficSettings", "snatPrivateRanges", "dnsServers", "apimAdditionalNetworkRules", "managementAdditionalNetworkRules", "apimAdditionalApplicationRules", "managementAdditionalApplicationRules"):
        if key in f:
            _list(f[key], "network.firewall." + key)
    _list(n.setdefault("managementAdditionalSecurityRules", []), "network.managementAdditionalSecurityRules")
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
                                    deployed_models: set[str], q: str, *, allow_profiles: bool = True):
    """Validate each JSON profile with the same limits as an ordinary operation."""
    p, op_name = q, operation["name"]
    backend, api_type, mode = api["backendService"], api["apiType"], api["authorization"]["mode"]
    _object(constraint, q, {"kind", "parameter", "allowedModels", "maxRequestBytes", "maxOutputTokens", "maxOutputTokensByModel", "outputTokenLimitKinds", "initialOutputTokens", "outputTokenParameter", "modelVersion", "allowedLanguages", "allowedWarningCodes", "bodyFormat", "contentTypes", "allowedMethods", "integerLimits", "arrayLimits", "stringChoices", "model", "sessionTtlSeconds", "publicCallPath", "profiles", "defaultProfile", "backendUrlTemplate", "maxResponseBytes", "apiVersion", "timeoutSeconds", "embeddingDimensionsByModel", "embeddingInputField"})
    kind = _choice(constraint.get("kind"), q + ".kind", ("registeredOperation", "pathModel", "bodyModel", "textEmbeddings", "rerank", "statelessResponses", "hostedAgentPost", "a2aJsonRpc", "realtimeCreate", "realtimeHangup", "liveCreate", "pii", "documentAnalysis", "documentAnalysisStatus"))
    # profileの上流URIは、登録時に検査した固定表としてBicepへ渡します。
    # 選択済みprofileのモデルと転送先を一体で扱います。
    if "backendUrlTemplate" in constraint:
        if allow_profiles or kind != "bodyModel" or backend != "foundryServices" or "backendUrlTemplate" in operation:
            _fail(q + ".backendUrlTemplate", "requires a Foundry provider JSON profile with profile-owned routing")
        _backend_url_template(constraint["backendUrlTemplate"], q + ".backendUrlTemplate", set())
    if kind == "statelessResponses" and (operation["method"] != "POST" or operation["urlTemplate"] != "/responses" or backend != "foundryOpenAi"):
        _fail(q, "statelessResponses permits only resource Models POST /responses on foundryOpenAi")
    if "backendUrlTemplate" in operation and not ((backend in ("external") and kind in ("registeredOperation", "bodyModel", "pathModel", "hostedAgentPost", "a2aJsonRpc")) or (backend == "foundryOpenAi" and kind == "pathModel") or (backend == "foundryServices" and kind in ("bodyModel", "textEmbeddings", "rerank", "hostedAgentPost"))):
        _fail(p + ".operations." + op_name + ".backendUrlTemplate", "requires an explicit external or Foundry model-provider contract")
    body_format = _choice(constraint.setdefault("bodyFormat", "none" if operation["method"] in ("GET", "HEAD", "DELETE", "OPTIONS") or api_type != "http" else "json"), q + ".bodyFormat", ("json", "binary", "multipart", "text", "none"))
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
        if constraint.get("allowedModels") != ["prebuilt-read", "prebuilt-layout"]:
            _fail(q + ".allowedModels", "must reference the published OCR and layout models")
        if constraint.get("apiVersion") != "2024-11-30":
            _fail(q + ".apiVersion", "must use the published Document Intelligence GA API")
        if kind == "documentAnalysis":
            if (operation["name"] != "analyze_document" or operation["method"] != "POST"
                    or operation["urlTemplate"] != "/analyze" or operation["templateParameters"] or body_format != "binary"):
                _fail(q, "Document submission requires POST /analyze with a binary body")
            types = set(constraint.get("contentTypes", []))
            supported = {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/bmp", "image/heif", "image/heic", "text/html",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
            if not types or not types <= supported:
                _fail(q + ".contentTypes", "must use supported Document Intelligence file formats")
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

    if "profiles" not in constraint:
        if "defaultProfile" in constraint:
            _fail(q + ".defaultProfile", "requires explicit profiles")
        return
    if not allow_profiles or api_type != "http" or kind not in ("bodyModel", "statelessResponses") or body_format != "json":
        _fail(q + ".profiles", "requires a non-recursive JSON model operation")
    profiles = _list(constraint["profiles"], q + ".profiles", minimum=1)
    profile_ids = set()
    normalized = []
    # Individual profiles may carry the complete ordinary constraint contract.
    ordinary_fields = {"kind", "parameter", "allowedModels", "maxRequestBytes", "maxOutputTokens", "maxOutputTokensByModel", "outputTokenLimitKinds", "initialOutputTokens", "outputTokenParameter", "modelVersion", "allowedLanguages", "allowedWarningCodes", "bodyFormat", "contentTypes", "allowedMethods", "integerLimits", "arrayLimits", "stringChoices", "model", "sessionTtlSeconds", "publicCallPath", "backendUrlTemplate"}
    for index, entry in enumerate(profiles):
        label = f"{q}.profiles[{index}]"
        _object(entry, label, ordinary_fields | {"profileId", "modalities"})
        profile_id = _string(entry.get("profileId"), label + ".profileId", maximum=64)
        if not API_NAME.fullmatch(profile_id) or profile_id in profile_ids:
            _fail(label + ".profileId", "must be a unique profile identifier")
        profile_ids.add(profile_id)
        modalities = _strings(entry.get("modalities"), label + ".modalities", minimum=1)
        if not set(modalities) <= {"text", "image", "file", "audio"}:
            _fail(label + ".modalities", "must explicitly allow supported text/image/file/audio capabilities")
        item = {key: copy.deepcopy(value) for key, value in entry.items() if key not in ("profileId", "modalities")}
        if item.get("kind") != kind or item.get("bodyFormat", "json") != "json":
            _fail(label, "must retain this operation's JSON model contract")
        _normalize_operation_constraint(item, api, operation, deployed_models, label, allow_profiles=False)
        item.update(profileId=profile_id, modalities=modalities)
        normalized.append(item)
    if constraint.get("defaultProfile") not in profile_ids:
        _fail(q + ".defaultProfile", "must reference an explicit profile")
    if any("backendUrlTemplate" in entry for entry in normalized) and not all("backendUrlTemplate" in entry for entry in normalized):
        _fail(q + ".profiles", "each profile must specify its fixed upstream route when profile routing is used")
    constraint["profiles"] = normalized
    # This outer envelope bounds pre-parsing only. Authorization selects a profile;
    # each selected profile retains its original models, bytes and output limits.
    constraint["allowedModels"] = list(dict.fromkeys(model for entry in normalized for model in entry["allowedModels"]))
    constraint["maxRequestBytes"] = max(entry["maxRequestBytes"] for entry in normalized)


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
        _object(api, p, {"name", "displayName", "path", "backendService", "backendUrl", "backendPath", "backendAudience", "productId", "released", "subscriptionKeyHeader", "operations", "keySubscriptions", "authorization", "mcp", "credentialConnection", "apiType", "backendAuthentication", "backendHeaders", "grpc", "mcpProxy", "websocket", "serviceClass"})
        _bool(api.setdefault("released", False), p + ".released")
        api_type = _choice(api.setdefault("apiType", "http"), p + ".apiType", ("http", "websocket", "grpc", "mcp"))
        name = _string(api.get("name"), p + ".name", maximum=80)
        if not API_NAME.fullmatch(name) or name.casefold() in names or name.casefold() in ("credential-manager-login", "credential-manager-browser"):
            _fail(p + ".name", "must be a unique API identifier")
        names.add(name.casefold())
        service_class = _choice(api.setdefault("serviceClass", "sample" if name.startswith("sample-") else "platform"), p + ".serviceClass", ("platform", "sample"))
        if name.startswith("sample-") and service_class != "sample":
            _fail(p + ".serviceClass", "sample-prefixed APIs must be classified as sample")
        _string(api.get("displayName"), p + ".displayName")
        path = api.get("path")
        if not (api_type == "grpc" and path == ""):
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
            _object(operation, q, {"name", "displayName", "method", "urlTemplate", "templateParameters", "request", "backendUrlTemplate", "staticResponse", "backendOverride"})
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
        # 通話の作成と終了は同じ経路・期限の対にする。作成だけ公開して
        # 終了操作が欠ける設定は受け付けない。
        realtime = [item for item in constraints.values() if item["kind"] in ("realtimeCreate", "realtimeHangup")]
        if realtime and (sorted(item["kind"] for item in realtime) != ["realtimeCreate", "realtimeHangup"] or len({(item["sessionTtlSeconds"], item["publicCallPath"]) for item in realtime}) != 1):
            _fail(p + ".authorization.operationConstraints", "Realtime requires one create and one hangup contract with identical lifetime and public path")
        if "mcp" in api:
            if api_type != "http":
                _fail(p + ".mcp", "REST-generated MCP belongs to an HTTP API, not a protocol proxy")
            mcp = _object(api["mcp"], p + ".mcp", {"name", "path", "displayName", "operationNames"})
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
        # WebSocket/gRPC/MCPのフレームへHTTPのJSON検査を適用しない。
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
        # gRPC定義は単独で解決できるprotoに限定する。未取得のimportを
        # 配備時に探す仕様にせず、公開するサービスとRPCを事前に確定する。
        if api_type == "grpc":
            grpc = _object(api.get("grpc"), p + ".grpc", {"definition"})
            definition = grpc.get("definition")
            proto = _string(definition.strip() if isinstance(definition, str) else definition, p + ".grpc.definition", maximum=1000000)
            grpc["definition"] = proto
            if "service " not in proto or "rpc " not in proto or re.search(r'\bimport\s+(?:(?:public|weak)\s+)?["\']', re.sub(r"/\*.*?\*/|//[^\n]*", "", proto, flags=re.S)):
                _fail(p + ".grpc.definition", "requires one self-contained proto with service and RPC methods; imported proto files are unsupported")
        elif "grpc" in api:
            _fail(p + ".grpc", "is allowed only on a gRPC API")
        # Streamable HTTPはmessage、従来SSEはsseとmessageを必要とする。
        # 別の通信方式を同じ入口数で扱わず、公式の接続先の組合せを検査する。
        if api_type == "mcp":
            proxy = _object(api.get("mcpProxy"), p + ".mcpProxy", {"transport", "endpoints"})
            transport = _choice(proxy.get("transport"), p + ".mcpProxy.transport", ("streamable", "sse"))
            endpoints = _list(proxy.get("endpoints"), p + ".mcpProxy.endpoints", minimum=1)
            endpoint_names = set()
            for endpoint in endpoints:
                _object(endpoint, p + ".mcpProxy.endpoints", {"name", "uriTemplate"})
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
    return configuration.get("entraTenantId", configuration["azureTenantId"])


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
    settings = _object(value, "portalApiKeys", {"groups", "platformGroupId", "groupNamePattern", "verificationSubscriptions"})
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
    c["network"] = _network(c.get("network"))
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
    # AppGW本体とPublic IPのゾーンは一致させる。WAFの本文上限と検査上限、
    # gRPCで使う証明書のホスト名も、独立した値のまま矛盾させない。
    g = _object(c.get("gateway", {}), "gateway", {"publicHostName", "authentication", "skuName", "autoscaleMinCapacity", "autoscaleMaxCapacity", "zones", "existingWafPolicyId", "wafMode", "wafCustomRules", "wafExclusions", "backendRequestTimeoutSeconds", "grpc", "wafMaxRequestBodySizeInKb", "wafRequestBodyInspectLimitInKb", "wafFileUploadLimitInMb", "realtimeControlEnabled", "rateLimits"})
    g["publicHostName"] = _host(g.get("publicHostName"), "gateway.publicHostName")
    # 共通入口はサーバーが保持する購読キーで認可します。
    authentication = _object(g.get("authentication"), "gateway.authentication", {"subscriptionKeyHeader", "credentialLogin"})
    key_header = _string(authentication.setdefault("subscriptionKeyHeader", "api-key"), "gateway.authentication.subscriptionKeyHeader", maximum=128)
    if not re.fullmatch(r"[A-Za-z0-9-]+", key_header) or key_header.casefold().startswith("x-core-connection-") or key_header.casefold() in ("authorization", "host", "content-type", "cookie", "x-core-l7-ingress"):
        _fail("gateway.authentication.subscriptionKeyHeader", "must be a dedicated HTTP header name")
    login = authentication.get("credentialLogin")
    if login is not None:
        q = "gateway.authentication.credentialLogin"
        _object(login, q, {"providerNames", "transactionLifetimeSeconds", "loginCallsPerMinute", *SESSION_POLICY_DEFAULTS})
        selected = _strings(login.get("providerNames"), q + ".providerNames", minimum=1)
        if len(set(selected)) != len(selected) or any(not API_NAME.fullmatch(name) for name in selected):
            _fail(q + ".providerNames", "must identify unique Credential Manager providers")
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
    grpc = _object(g.setdefault("grpc", {}), "gateway.grpc", {"enabled", "publicPort", "publicHostName", "backendPort"})
    _bool(grpc.setdefault("enabled", False), "gateway.grpc.enabled")
    _integer(grpc.setdefault("publicPort", 8443), "gateway.grpc.publicPort", 1024, 64999)
    if grpc["publicPort"] in (3389, 3443, 6390):
        _fail("gateway.grpc.publicPort", "must not conflict with platform or management ports")
    grpc["publicHostName"] = _host(grpc.setdefault("publicHostName", g["publicHostName"]), "gateway.grpc.publicHostName")
    _integer(grpc.setdefault("backendPort", 443), "gateway.grpc.backendPort", 443, 443)
    if grpc["publicHostName"] != g["publicHostName"]:
        _fail("gateway.grpc", "requires the existing certificate hostname and APIM HTTPS port 443")
    if grpc["enabled"] and not c["network"]["apimKeyVaultAccessEnabled"]:
        _fail("network.apimKeyVaultAccessEnabled", "must be enabled for the gRPC certificate and L7 ingress Key Vault references")
    c["gateway"] = g
    # VMはサイズとOSイメージだけでなく、ディスク方式・SSH鍵・管理者名も検査する。
    # latestの自動選択は使わず、検証した固定版で再現可能にする。
    m = _object(c.get("management"), "management", {"vmSize", "imageReference", "diskControllerType", "osDiskSizeGB", "osDiskSku", "enableAcceleratedNetworking", "encryptionAtHost", "adminUsername", "sshPublicKeyFile", "loginAssignments", "patchAssessmentMode", "patchMode"})
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
    _principals(m.setdefault("loginAssignments", []), "management.loginAssignments", login=True)
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
    portal = _object(c.get("portal"), "portal", {"publicHostName"})
    portal_host = _host(portal.get("publicHostName"), "portal.publicHostName")
    cert = _object(c.get("certificate"), "certificate", {"mode", "certificateName", "contactEmail", "directory", "plugin", "renewBeforeDays", "checkEveryHours", "moduleVersion", "dnsSleepSeconds", "validationTimeoutSeconds", "minimumValidDays", "domainNames", "azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"})
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
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", _string(cert.setdefault("certificateName", "tls-public"), "certificate.certificateName", maximum=80)):
        _fail("certificate.certificateName", "must be a Key Vault certificate identifier")
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
    allowed = c["network"]["firewall"].setdefault("managementAllowedFqdns", [])
    ca_host = "acme-v02.api.letsencrypt.org" if cert["directory"] == "production" else "acme-staging-v02.api.letsencrypt.org"
    for host in [ca_host, "management.azure.com", g["publicHostName"], "cdn.oneget.org", "cdn.powershellgallery.com", "*.powershellgallery.com", "go.microsoft.com", "aka.ms"]:
        if host not in allowed:
            allowed.append(host)
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
            endpoint = _https(provider.get("identityEndpoint"), "credentialProviders.identityEndpoint")
            identity = urlparse(endpoint)
            resource = urlparse(provider["resourceUri"])
            if identity.hostname != resource.hostname or identity.query or identity.fragment or identity.username or identity.password or identity.port not in (None, 443):
                _fail("credentialProviders.identityEndpoint", "must be a fixed HTTPS identity endpoint on the provider resource host")
            prop = provider.setdefault("identityIdProperty", "id")
            if not isinstance(prop, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", prop):
                _fail("credentialProviders.identityIdProperty", "must be an explicit identity response property")
        allowed = c["network"]["firewall"].setdefault("apimAllowedFqdns", [])
        if "management.azure.com" not in allowed:
            allowed.append("management.azure.com")
        # SQL台帳と認証窓口は構築側の設定で管理し、企業固有IDは既存providerから渡します。
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
    if any(api["backendAuthentication"]["mode"] == "keyVaultHeader" for api in c["apiDefinitions"]) and not s["grantApimSecretAccess"]:
        _fail("services.grantApimSecretAccess", "must be enabled for registered backend Key Vault credentials")
    if not c["network"]["apimKeyVaultAccessEnabled"]:
        from .grpc_lifecycle import owner_ticket_enabled
        if owner_ticket_enabled(c):
            _fail("network.apimKeyVaultAccessEnabled", "must be enabled for owner-bound operation ticket Key Vault references")
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
    c["network"]["apimKeyVaultAccessEnabled"] = c["network"]["apimKeyVaultAccessEnabled"] or s["grantApimSecretAccess"] or bool(login)
    # カタログ用の説明も無料の設定検査で作れることを先に確認する。
    # 説明自体はBicep入力時にだけ追加し、利用者の設定ファイルへは混ぜない。
    with_catalog_descriptions(c)
    portal = _object(c.get("portal"), "portal", {"publicHostName"})
    host = _host(portal.get("publicHostName"), "portal.publicHostName")
    if not host.startswith("portal.") or host == g["publicHostName"]:
        _fail("portal.publicHostName", "must be a distinct portal subdomain")
    if not any(name == host or (name.startswith("*.") and host.split(".", 1)[1] == name[2:]) for name in cert["domainNames"]):
        _fail("certificate.domainNames", "must cover the Portal hostname")
    # 管理サブネットのActions runnerが公開入口を検証するための固定宛先。
    management_fqdns = c["network"]["firewall"].setdefault("managementAllowedFqdns", [])
    if host not in management_fqdns:
        management_fqdns.append(host)
    return c


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
    ):
        settings = raw.get(section)
        if isinstance(settings, dict):
            for key in keys:
                if key in settings:
                    yield settings, key, f"{section}.{key}"
    providers = raw.get("credentialProviders")
    if isinstance(providers, list):
        for i, provider in enumerate(providers):
            if isinstance(provider, dict) and "clientSecretFile" in provider:
                yield provider, "clientSecretFile", f"credentialProviders[{i}].clientSecretFile"


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
    """分離configへ混入した企業固有項目を、空値やparams上書きがあっても拒否する。

    合成済み内部JSONは分離前の全体を持つため、この境界はconfigFileから
    読む標準configだけに適用します。実値はエラーへ含めません。
    """
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


def load_configuration_source(path: Path | str) -> dict:
    """paramsが参照するconfigと企業固有入力を合成し、未正規化の設定を返す。

    configFileはparamsの場所を基準に読む。各ファイル内の相対パスは合成前に
    宣言元基準で解決するため、出力先や実行ディレクトリーが変わっても参照先がぶれない。
    従来の合成済みJSONも読めるが、企業paramsの入力範囲は小さく制限する。
    """
    source = Path(path).expanduser().resolve()
    raw = _read_configuration_source(source)
    if "configFile" not in raw:
        return _resolve_file_references(raw, source.parent)
    _object(raw, "parameters", PARAMETER_KEYS)
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
        limits = gateway.get("rateLimits", {})
        if isinstance(limits, dict):
            for kind in ("subscriptionKey", "application"):
                if kind in limits:
                    _object(limits[kind], "gateway.rateLimits." + kind, {"overrides"})
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
    return _merge_configuration(
        _resolve_file_references(standard, config_source.parent),
        _resolve_file_references(parameters, source.parent),
    )


def load_configuration(path: Path | str) -> dict:
    """ファイル読込・params/config合成・全体検査を1つの入口として実行する。"""
    return normalize_configuration(load_configuration_source(path))


def build_bicep_configuration(configuration: dict) -> dict:
    """Bicepへ渡す公開設定を作り、秘密ファイルの参照先を取り除く。

    秘密の内容は別のsecure入力として渡す。この分離により、設定の表示や
    配備状態の保存へ秘密値・ローカル秘密パスが紛れ込むのを防ぐ。
    """
    result = copy.deepcopy(configuration)
    result["apiDefinitions"] = with_catalog_descriptions(configuration)
    # 標準APIの接続先はGateway配備時に解決する。基盤は公開経路と本文形式だけを
    # 先に受け取り、空の公開API配列でも適切なWAF・監査設定を準備する。
    from standard_apis import infrastructure_api_definitions
    result['infrastructureApiDefinitions'] = infrastructure_api_definitions(configuration['apiDefinitions'])
    # エディター用のschema/commentはローカルに保持し、取得やARMへの送信はしない。
    result.pop("$schema", None)
    result.pop("$comment", None)
    result["management"].pop("sshPublicKeyFile", None)
    for provider in result["credentialProviders"]:
        provider.pop("clientSecretFile", None)
    from .credential_pools import physical_providers, logical_profiles
    result["credentialPhysicalProviders"] = physical_providers(result)
    result["credentialProfiles"] = logical_profiles(result)
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


def validate_supported_certificate(certificate: dict) -> None:
    """構築入口はAzure Public DNSによるDNS-01発行だけを受け付ける。"""
    if not isinstance(certificate, dict) or certificate.get("mode") != "dnsPlugin":
        _fail("certificate.mode", "DNS-01 issuance requires dnsPlugin mode")
    if certificate.get("plugin") != "Azure":
        _fail("certificate.plugin", "Azure Public DNS is required; prepare its public authoritative zone before azd up")
    if any(key in certificate for key in ("pluginArgumentsFile", "preparedPfxFile", "preparedPfxPasswordFile")):
        _fail("certificate", "use the Azure DNS subscription and managed identity references")
    validate_azure_dns_identity(certificate)


def validate_azure_dns_identity(certificate: dict, *, require_complete: bool = True) -> None:
    """Azure DNSは事前作成したUAMIと公開Subscription参照だけを使う。"""
    if require_complete or "azureDnsSubscriptionId" in certificate:
        _guid(certificate.get("azureDnsSubscriptionId"), "certificate.azureDnsSubscriptionId")
    if not require_complete and "azureDnsManagedIdentityResourceId" not in certificate:
        return
    identity = _string(certificate.get("azureDnsManagedIdentityResourceId"),
                       "certificate.azureDnsManagedIdentityResourceId", maximum=2048)
    match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
                         r"Microsoft\.ManagedIdentity/userAssignedIdentities/([^/]+)", identity, re.IGNORECASE)
    if not match or not re.fullmatch(r"[\w().-]{1,90}", match[2]) or not re.fullmatch(r"[\w-]{3,128}", match[3]):
        _fail("certificate.azureDnsManagedIdentityResourceId", "must identify an existing user-assigned managed identity")
    _guid(match[1], "certificate.azureDnsManagedIdentityResourceId.subscriptionId")


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
    """CMで共有するEntra secretだけを読み、他の基盤資格情報には触れない。"""
    result = {}
    for provider in configuration["credentialProviders"]:
        value = _read_protected_file(provider["clientSecretFile"], "credentialProviders.clientSecretFile")
        if not value:
            _fail("credentialProviders.clientSecretFile", "must not be empty")
        result[provider["name"]] = value
    return result


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
        "credentialProviderSecrets": load_credential_provider_secrets(configuration)}
    # Azure DNS authenticates on the VM through its assigned identity. No
    # DNS credential file or caller-supplied PFX is read or forwarded.
    secrets["dnsPluginArgumentsJson"] = ""
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
            from .paths import private_parent
            try:
                output = private_parent(args.output)
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                    os.fchmod(destination.fileno(), 0o600)
                    destination.write(json.dumps(build_bicep_configuration(config), ensure_ascii=False, indent=2) + "\n")
            except (OSError, ValueError):
                _fail("output", "cannot write a protected runtime output")
        print(json.dumps({"valid": True, "resourceGroupNames": config["resourceGroupNames"], "apiPublicationStatus": config["apiPublicationStatus"]}, ensure_ascii=False))
        return 0
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
