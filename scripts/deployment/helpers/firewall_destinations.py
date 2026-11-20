"""検証済みの業務API・本人接続設定からAPIMの内部宛先入力を生成する。"""
import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit


def _tls_fqdn(url: str) -> str:
    """API公開と同じTLS/FQDN境界で停止し、私値を例外へ含めない。"""
    try:
        if not isinstance(url, str) or any(ord(char) < 33 or ord(char) == 127 for char in url):
            raise ValueError
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("External backend URL is invalid; private input suppressed") from None
    if (parsed.scheme not in ("https", "wss") or port not in (None, 443) or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or any(re.search(r"key|token|secret|password|^sig$", key, re.I)
                   for key, _ in parse_qsl(parsed.query))):
        raise ValueError("External backends require fixed TLS FQDNs without credential-bearing URLs")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        hostname = parsed.hostname.lower()
        labels = hostname.split(".")
        if len(hostname) > 253 or len(labels) < 2 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels):
            raise ValueError("External Firewall application rules require a fixed DNS hostname")
        return hostname
    raise ValueError("External Firewall application rules require an explicit DNS name")


def generated_apim_service_fqdns(configuration: dict) -> list[str]:
    """公開FW配列や実環境の規則を読まず、同じ業務設定から同じ宛先を返す。"""
    hosts = set()
    for api in configuration.get("apiDefinitions", []):
        backends = [api, *(operation["backendOverride"] for operation in api.get("operations", [])
                           if "backendOverride" in operation)]
        for backend in backends:
            if backend.get("backendService") == "external":
                hosts.add(_tls_fqdn(backend["backendUrl"]))
    login = configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin")
    if login:
        # 現行の本人接続APIのARM互換到達性と本人情報取得先を保持します。
        hosts.add("management.azure.com")
        selected = set(login["providerNames"])
        hosts.update(_tls_fqdn(provider["identityEndpoint"])
                     for provider in configuration.get("credentialProviders", [])
                     if provider["name"] in selected)
    return sorted(hosts)
