"""App GatewayのHTTPSバックエンドを確認し、Portal用WAFを管理する。"""
from __future__ import annotations

import copy
import json
import re
import time

from .paths import REPOSITORY
from .waf_names import policy_id as waf_policy_id


NETWORK_API = "2024-05-01"
CALLBACK_PATH = "/api/auth/callback/microsoft"
API_CALLBACK_PATH = "/api/api-auth/callback"
CALLBACK_PATHS = (CALLBACK_PATH, API_CALLBACK_PATH)
CALLBACK_RULE_IDS = ["942430", "942440", "942450"]


def gateway_id(config):
    """選択した環境の既存App Gatewayだけを更新対象にする。"""
    return (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
            f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/"
            f"applicationGateways/agw-{config['resourceStem']}")


def _ok(response, operation, allowed=(200,), *, require_body=True):
    if not isinstance(response, dict) or response.get("statusCode") not in allowed:
        code = response.get("statusCode") if isinstance(response, dict) else None
        raise ValueError(f"Portal gateway {operation} failed (HTTP {code})")
    body = response.get("body")
    if require_body and not isinstance(body, dict):
        raise ValueError(f"Portal gateway {operation} response is invalid")
    return body


def require_appgw_backend_tls_ready(config, transport, *, allow_new=False):
    """許可された初回と、HTTPSプローブを構築済みのApp Gatewayだけを受け付ける。"""
    expected = gateway_id(config)
    response = transport.request("GET", expected + "?api-version=" + NETWORK_API)
    if allow_new and response.get("statusCode") == 404:
        return {"newGateway": True}
    body = _ok(response, "read backend TLS configuration")
    properties = body.get("properties", {})
    if (str(body.get("id", "")).casefold() != expected.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.network/applicationgateways"
            or properties.get("provisioningState") != "Succeeded"):
        raise ValueError("App Gateway ownership or successful provisioning could not be verified")
    certificate_name = "firewall-inspection-root"
    certificate_id = (expected + "/trustedRootCertificates/" + certificate_name).casefold()
    if sum(item.get("name") == certificate_name for item in properties.get("trustedRootCertificates", [])) != 1:
        raise ValueError("Existing App Gateway requires a separate migration to the Firewall TLS inspection root")
    for backend, probe_path, statuses in (("apim", "/status-0123456789abcdef", ["200-399"]),
                                          ("portal", "/api/health", ["200"])):
        settings = [item["properties"] for item in properties.get("backendHttpSettingsCollection", [])
                    if item.get("name") == "behttp-" + backend]
        probes = [item["properties"] for item in properties.get("probes", []) if item.get("name") == "probe-" + backend]
        if len(settings) != 1 or len(probes) != 1:
            raise ValueError("App Gateway HTTPS backend or probe binding is missing or ambiguous")
        setting, probe = settings[0], probes[0]
        expected_probe = (expected + "/probes/probe-" + backend).casefold()
        if ((setting.get("protocol"), setting.get("port")) != ("Https", 443)
                or not setting.get("hostName") or setting["hostName"] != probe.get("host")
                or setting.get("pickHostNameFromBackendAddress", False) is not False
                or str(setting.get("probe", {}).get("id", "")).casefold() != expected_probe
                or [str(item.get("id", "")).casefold() for item in setting.get("trustedRootCertificates", [])] != [certificate_id]
                or (probe.get("protocol"), probe.get("port"), probe.get("path")) != ("Https", 443, probe_path)
                or probe.get("match", {}).get("statusCodes") != statuses):
            raise ValueError("Existing App Gateway requires a separate migration to the inspected HTTPS backend contract")
    return {"newGateway": False, "protocol": "Https", "port": 443, "portalProbe": "/api/health"}


def _probe(body, expected):
    """名前・リソースID・既存の紐付けを確認し、WAF更新前にPortalのプローブを照合する。"""
    if (str(body.get("id", "")).casefold() != expected.casefold()
            or body.get("name") != expected.rsplit("/", 1)[-1]
            or str(body.get("type", "")).casefold() != "microsoft.network/applicationgateways"):
        raise ValueError("Portal gateway response identifies another resource")
    properties = body.get("properties", {})
    probes = properties.get("probes", [])
    settings = properties.get("backendHttpSettingsCollection", [])
    if not isinstance(probes, list) or not isinstance(settings, list):
        raise ValueError("Portal gateway probe inventory is invalid")
    selected = [item for item in probes if isinstance(item, dict) and item.get("name") == "probe-portal"]
    backends = [item for item in settings if isinstance(item, dict) and item.get("name") == "behttp-portal"]
    if (len(selected) != 1 or len(backends) != 1
            or selected[0].get("properties", {}).get("path") not in ("/", "/api/health")
            or str(backends[0].get("properties", {}).get("probe", {}).get("id", "")).casefold()
               != (expected + "/probes/probe-portal").casefold()):
        raise ValueError("Portal gateway probe or backend binding differs from the expected configuration")
    probe_path = selected[0].get("properties", {}).get("path")
    codes = selected[0].get("properties", {}).get("match", {}).get("statusCodes")
    if (probe_path, codes) not in (("/", ["200"]), ("/", ["200-399"]), ("/api/health", ["200"])):
        raise ValueError("Portal gateway has an unexpected health response range")
    return selected[0]


def _writable(body):
    """GET専用メタデータだけを落とし、証明書参照・リスナー・他の経路を保つ。"""
    read_only = {"etag", "provisioningState", "resourceGuid", "operationalState", "publicCertData", "backendIPConfigurations"}

    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in read_only
                    and not (key == "backendHttpSettings" and isinstance(item, list))}
        return value

    result = clean(copy.deepcopy(body))
    # The 2025-01-01 request schema and official SDK serializer omit these
    # GET-only inverse references. Remove only validated, owned references at
    # these positions; top-level listeners/routingRules remain writable.
    properties = result.get("properties", {})
    if isinstance(properties, dict):
        reverse_references = {
            "frontendIPConfigurations": ("httpListeners", "listeners"),
            "frontendPorts": ("httpListeners", "listeners"),
            "backendAddressPools": ("routingRules",),
        }
        gateway = body.get("id")
        for collection, names in reverse_references.items():
            items = properties.get(collection, [])
            if not isinstance(items, list):
                continue
            for item in items:
                child = item.get("properties") if isinstance(item, dict) else None
                if isinstance(child, dict):
                    for name in names:
                        if name not in child:
                            continue
                        references = child[name]
                        targets = properties.get(name, [])
                        if (not isinstance(references, list) or not isinstance(targets, list)
                                or not isinstance(gateway, str) or not gateway):
                            raise ValueError("App Gateway GET inverse references have an invalid shape")
                        owned = {gateway.casefold() + "/" + name.casefold() + "/" + target["name"].casefold()
                                 for target in targets if isinstance(target, dict) and isinstance(target.get("name"), str)}
                        seen = set()
                        for reference in references:
                            identifier = reference.get("id") if isinstance(reference, dict) else None
                            if (not isinstance(identifier, str) or set(reference) != {"id"}
                                    or identifier.casefold() not in owned or identifier.casefold() in seen):
                                raise ValueError("App Gateway GET inverse reference identifies an unexpected resource")
                            seen.add(identifier.casefold())
                        child.pop(name)
    for name in ("id", "name", "type", "systemData"):
        result.pop(name, None)
    identity = result.get("identity")
    if isinstance(identity, dict):
        identity.pop("principalId", None)
        identity.pop("tenantId", None)
        if isinstance(identity.get("userAssignedIdentities"), dict):
            identity["userAssignedIdentities"] = {key: {} for key in identity["userAssignedIdentities"]}
    return result


def _etag(response):
    value = response.get("body", {}).get("etag") or next((value for key, value in response.get("headers", {}).items() if key.casefold() == "etag"), None)
    if (not isinstance(value, str) or len(value) > 512
            or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', value)
            or value in ('*', '"*"', 'W/"*"')):
        raise ValueError("Portal gateway configuration ETag is required before updating")
    return value


def _writable_policy(body):
    result = _writable(body)
    # WAFポリシーの逆参照はGET専用です。Gateway側の経路一覧は削除しません。
    for name in ("applicationGateways", "httpListeners", "pathBasedRules"):
        result.get("properties", {}).pop(name, None)
    # ARM may return this newer optional field as [], even when it was omitted.
    # Both mean "skip no managed rules". Never discard nonempty/unknown values:
    # they change the protection applied after a custom rule matches.
    custom_rules = result.get("properties", {}).get("customRules", [])
    if isinstance(custom_rules, list):
        for rule in custom_rules:
            if isinstance(rule, dict) and isinstance(rule.get("skippedManagedRuleSets"), list) and not rule["skippedManagedRuleSets"]:
                rule.pop("skippedManagedRuleSets")
    return result


def portal_session_exclusions():
    """HTTPSセッション値の実測1規則だけを、Bicepと同じ正本から読む。"""
    return json.loads((REPOSITORY / "apps/portal/waf-session-exclusions.json").read_text(encoding="utf-8"))


def _portal_policy(source):
    """共通保護を保ち、Portal専用のセッション値除外を合成する。"""
    desired = _writable_policy(source)
    properties = desired.get("properties", {})
    if properties.get("policySettings", {}).get("state") != "Enabled":
        raise ValueError("Portal requires the existing enabled WAF policy")
    managed = properties.get("managedRules", {})
    sets = managed.get("managedRuleSets", [])
    if len([item for item in sets if item.get("ruleSetType") == "OWASP" and item.get("ruleSetVersion") == "3.2"]) != 1:
        raise ValueError("Portal session WAF requires the owned OWASP 3.2 rule set")
    exclusions = managed.setdefault("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("Portal WAF exclusions are invalid")
    for exclusion in portal_session_exclusions():
        if exclusion not in exclusions:
            exclusions.append(exclusion)
    return desired


def _callback_policy(source):
    """Portalセッション保護に、callback認可コードの実測3規則だけを合成する。"""
    desired = _portal_policy(source)
    properties = desired["properties"]
    managed = properties.get("managedRules", {})
    sets = managed.get("managedRuleSets", [])
    selected = [item for item in sets if item.get("ruleSetType") in ("OWASP", "Microsoft_DefaultRuleSet")]
    if len(selected) != 1 or not isinstance(selected[0].get("ruleSetVersion"), str):
        raise ValueError("Portal callback WAF rule set cannot be identified")
    exclusion = {
        "matchVariable": "RequestArgValues", "selectorMatchOperator": "Equals", "selector": "code",
        "exclusionManagedRuleSets": [{
            "ruleSetType": selected[0]["ruleSetType"], "ruleSetVersion": selected[0]["ruleSetVersion"],
            "ruleGroups": [{"ruleGroupName": "REQUEST-942-APPLICATION-ATTACK-SQLI",
                            "rules": [{"ruleId": rule} for rule in CALLBACK_RULE_IDS]}],
        }],
    }
    exclusions = managed.setdefault("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("Portal callback WAF exclusions are invalid")
    if exclusion not in exclusions:
        exclusions.append(exclusion)
    return desired


def _callback_gateway(config, body, expected, policy_id, *, portal_host=None):
    """Portal HTTPSとcallbackへ専用policyを結び、Gateway側の経路は保持する。"""
    _probe(body, expected)
    updated = _writable(body)
    properties = updated["properties"]
    if properties.get("sku", {}).get("name") != "WAF_v2":
        raise ValueError("Portal WAF requires the owned WAF_v2 gateway")
    listeners = [item for item in properties.get("httpListeners", []) if item.get("name") == "portal-https"]
    routes = [item for item in properties.get("requestRoutingRules", []) if item.get("name") == "rule-portal-https"]
    if len(listeners) != 1 or len(routes) != 1:
        raise ValueError("Portal HTTPS listener or routing rule is missing")
    listener = listeners[0].get("properties", {})
    if listener.get("protocol") != "Https" or (portal_host and listener.get("hostName") != portal_host):
        raise ValueError("Portal HTTPS listener uses another protocol or public host")
    base_policy = waf_policy_id(config, "g-base")
    if policy_id.casefold() != waf_policy_id(config, "p-auth").casefold():
        raise ValueError("Portal callback WAF target is not the owned purpose policy")
    if str(properties.get("firewallPolicy", {}).get("id", "")).casefold() != base_policy.casefold():
        raise ValueError("Portal callback WAF must use the owned common policy")
    portal_policy = waf_policy_id(config, "p-base")
    listener_policy = listener.get("firewallPolicy", {}).get("id")
    if listener_policy and listener_policy.casefold() not in {base_policy.casefold(), portal_policy.casefold()}:
        raise ValueError("Portal HTTPS listener uses another WAF policy")
    if not listener_policy or listener_policy.casefold() != portal_policy.casefold():
        listener["firewallPolicy"] = {"id": portal_policy}
    route = routes[0].get("properties", {})
    pool = {"id": expected + "/backendAddressPools/bepool-portal"}
    backend = {"id": expected + "/backendHttpSettingsCollection/behttp-portal"}
    mapping = {"id": expected + "/urlPathMaps/pathmap-portal-auth"}
    if str(route.get("httpListener", {}).get("id", "")).casefold() != (expected + "/httpListeners/portal-https").casefold():
        raise ValueError("Portal routing rule has another listener")
    def same_ref(value, reference):
        return isinstance(value, dict) and str(value.get("id", "")).casefold() == reference["id"].casefold()

    if route.get("ruleType") == "Basic":
        if not same_ref(route.get("backendAddressPool"), pool) or not same_ref(route.get("backendHttpSettings"), backend):
            raise ValueError("Portal routing rule has another backend")
    elif route.get("ruleType") != "PathBasedRouting" or not same_ref(route.get("urlPathMap"), mapping):
        raise ValueError("Portal routing rule has another path map")
    maps = properties.setdefault("urlPathMaps", [])
    selected = [item for item in maps if item.get("name") == "pathmap-portal-auth"]
    if len(selected) > 1:
        raise ValueError("Portal callback path map is duplicated")
    if selected:
        path_properties = selected[0]["properties"]
        if not same_ref(path_properties.get("defaultBackendAddressPool"), pool) or not same_ref(path_properties.get("defaultBackendHttpSettings"), backend):
            raise ValueError("Portal path map defaults use another backend")
    else:
        path_properties = {"defaultBackendAddressPool": pool, "defaultBackendHttpSettings": backend, "pathRules": []}
        maps.append({"name": "pathmap-portal-auth", "properties": path_properties})
    rules = path_properties.setdefault("pathRules", [])
    matches = [item for item in rules if item.get("name") == "microsoft-auth-callback"
               or any(path in item.get("properties", {}).get("paths", []) for path in CALLBACK_PATHS)]
    desired_rule = {"name": "microsoft-auth-callback", "properties": {
        "paths": list(CALLBACK_PATHS), "firewallPolicy": {"id": policy_id},
        "backendAddressPool": pool, "backendHttpSettings": backend,
    }}
    if len(matches) > 1 or (matches and matches[0].get("name") != desired_rule["name"]):
        raise ValueError("Portal callback path has another owner")
    if matches:
        current = matches[0]["properties"]
        if (current.get("paths") not in ([CALLBACK_PATH], list(CALLBACK_PATHS)) or not same_ref(current.get("backendAddressPool"), pool)
                or not same_ref(current.get("backendHttpSettings"), backend)):
            raise ValueError("Portal callback path has another backend or path")
        current_policy = current.get("firewallPolicy", {}).get("id")
        if current_policy and current_policy.casefold() not in {
                base_policy.casefold(), portal_policy.casefold(), policy_id.casefold()}:
            raise ValueError("Portal callback path uses another WAF policy")
        current["paths"] = list(CALLBACK_PATHS)
        if not same_ref(current.get("firewallPolicy"), {"id": policy_id}):
            current["firewallPolicy"] = {"id": policy_id}
    else:
        rules.insert(0, desired_rule)
    route["ruleType"] = "PathBasedRouting"
    route.pop("backendAddressPool", None)
    route.pop("backendHttpSettings", None)
    if not same_ref(route.get("urlPathMap"), mapping):
        route["urlPathMap"] = mapping
    return updated


def configure_portal_callback_waf(config, transport, *, timeout=1200, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """Portalのセッション値とcallbackを調整し、Gateway側のWAF検査は維持する。"""
    if config.get("gateway", {}).get("existingWafPolicyId"):
        raise ValueError("Portal WAF must not replace an enterprise-managed policy")
    expected = gateway_id(config)
    gateway_path = expected + "?api-version=" + NETWORK_API
    response = transport.request("GET", gateway_path)
    gateway = _ok(response, "read callback routing")
    if gateway.get("properties", {}).get("provisioningState") != "Succeeded":
        raise ValueError("Portal gateway is not ready for callback routing")
    base_id = waf_policy_id(config, "g-base")
    policy_id = waf_policy_id(config, "p-auth")
    portal_policy_id = waf_policy_id(config, "p-base")
    portal_host = config.get("portal", {}).get("publicHostName")
    update = _callback_gateway(config, gateway, expected, policy_id, portal_host=portal_host)
    base = _ok(transport.request("GET", base_id + "?api-version=" + NETWORK_API), "read common WAF policy")
    if str(base.get("id", "")).casefold() != base_id.casefold():
        raise ValueError("Portal common WAF response identifies another policy")
    if base.get("properties", {}).get("provisioningState") != "Succeeded":
        raise ValueError("Portal common WAF policy is not ready")
    changes = []
    for identifier, desired in ((portal_policy_id, _portal_policy(base)), (policy_id, _callback_policy(base))):
        policy_path = identifier + "?api-version=" + NETWORK_API
        policy_response = transport.request("GET", policy_path)
        exists = policy_response.get("statusCode") == 200
        if not exists and policy_response.get("statusCode") != 404:
            _ok(policy_response, "read Portal WAF policy")
        existing = _ok(policy_response, "read Portal WAF policy") if exists else None
        if exists and str(existing.get("id", "")).casefold() != identifier.casefold():
            raise ValueError("Portal WAF response identifies another policy")
        if exists and existing.get("properties", {}).get("provisioningState") != "Succeeded":
            raise ValueError("Portal WAF policy is not ready")
        if not exists or _writable_policy(existing) != desired:
            headers = {"If-Match": _etag(policy_response)} if exists else {"If-None-Match": "*"}
            changes.append((identifier, desired, headers))
    changed_gateway = update != _writable(gateway)
    gateway_headers = {"If-Match": _etag(response)} if changed_gateway else None
    deadline, notice = monotonic() + timeout, monotonic() + 45
    for identifier, desired, headers in changes:
        policy_path = identifier + "?api-version=" + NETWORK_API
        _ok(transport.request("PUT", policy_path, desired, headers=headers), "update callback WAF policy", (200, 201, 202), require_body=False)
        while monotonic() < deadline:
            observed = _ok(transport.request("GET", policy_path), "read callback WAF update")
            status = observed.get("properties", {}).get("provisioningState")
            if status == "Succeeded" and _writable_policy(observed) == desired:
                break
            if status in ("Failed", "Canceled", "Deleting"):
                raise ValueError("Portal callback WAF policy update did not succeed")
            sleep(10)
        else:
            raise ValueError("Portal callback WAF policy update is still pending")
    if changed_gateway:
        # WAF policy updates can change the referencing gateway's ETag. Refresh
        # it only after those writes, while refusing any concurrent body change.
        fresh_response = transport.request("GET", gateway_path)
        fresh_gateway = _ok(fresh_response, "read callback route before update")
        _probe(fresh_gateway, expected)
        if (fresh_gateway.get("properties", {}).get("provisioningState") != "Succeeded"
                or _writable(fresh_gateway) != _writable(gateway)):
            raise ValueError("Portal gateway changed before callback routing update")
        gateway_headers = {"If-Match": _etag(fresh_response)}
        _ok(transport.request("PUT", gateway_path, update, headers=gateway_headers), "update callback route", (200, 201, 202), require_body=False)
        while monotonic() < deadline:
            observed = _ok(transport.request("GET", gateway_path), "read callback route update")
            status = observed.get("properties", {}).get("provisioningState")
            if status == "Succeeded" and _callback_gateway(config, observed, expected, policy_id, portal_host=portal_host) == _writable(observed):
                break
            if status in ("Failed", "Canceled", "Deleting"):
                raise ValueError("Portal callback routing update did not succeed")
            if monotonic() >= notice:
                progress("ポータル: 認証callback用WAF設定の反映を待っています。", flush=True)
                notice = monotonic() + 45
            sleep(10)
        else:
            raise ValueError("Portal callback routing update is still pending")
    return {"resourceId": expected, "policyId": policy_id, "path": CALLBACK_PATH, "paths": list(CALLBACK_PATHS),
            "excludedArgument": "code", "excludedRuleIds": list(CALLBACK_RULE_IDS),
            "sessionPolicyId": portal_policy_id,
            "sessionPolicyUpdated": any(identifier == portal_policy_id for identifier, _, _ in changes),
            "policyUpdated": any(identifier == policy_id for identifier, _, _ in changes), "gatewayUpdated": changed_gateway}
