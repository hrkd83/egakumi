#!/usr/bin/env python3
"""Gateway配備から呼び出す、APIMのAPI定義とポリシーの公開処理。

公開入口scripts/deploy_gateway.pyが配備順序と実行指定を管理する。
設定と配備記録から公開先を確定し、既存の5RG・APIM・
マネージドID・Foundryモデル・RBAC・秘密参照をすべて読み合わせてから更新する。
外部バックエンドへの通信は、APIM送信元のHTTPS/FQDN許可だけを既存FW規則へ
追加する。他のFW規則を削除せず、同時更新はETagで検出する。
WebSocket APIを公開するときは、既存の監査先を維持して接続ログも有効にする。
公開成功後、APIMの初期サンプル（Echo API・Starter/Unlimited製品と対応購読）を整理する。
モデル本体の追加・変更、業務APIの削除、コア基盤の再配備、Graph操作は行わない。
公開設定の成功と実APIの疎通成功は分けて記録する。実行モードはAzure変更を伴い、
公開後のAPI利用や既存資源の稼働には料金が発生し得る。
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit
import uuid

from deployment.helpers.configuration import API_NAME, entra_tenant_id
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.catalog_descriptions import with_catalog_descriptions
from deployment.helpers.apim_authentication import (
    COMMON_NAMED_VALUES, MIGRATION_REQUIRED, common_authentication_settings,
    validate_common_authentication_policy, validate_common_named_value, merge_common_authentication_policy,
)
from deployment.helpers.subscription_profiles import initialize_profile_settings
from catalog_publication import finalize_descriptions
from deployment.helpers import grpc_lifecycle
from deployment.helpers import credential_login
from deployment.helpers.credential_pools import physical_providers, resource_names
from deployment.helpers.apim_samples import cleanup_candidates, cleanup_default_samples
from deployment.helpers.released_apis import RELEASED_PRODUCT_ID, selected_api_names, reconcile_unreleased_apis
from deployment.lifecycle import DeploymentError, PROJECT_NAME, ROOT, require_success, state_from_outputs

APIM_API = "2024-05-01"
NETWORK_API = "2025-01-01"
DEPLOYMENT_API = "2022-09-01"
MONITOR_API = "2021-05-01-preview"
MODULE = ROOT / "apps/gateway/apim/api-publication.bicep"
LAYERS = ("id", "nw", "monitor", "svc", "maint")


def _same(left, right):
    """公開Resource IDを大小文字の差を除いて比較する。"""
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def _scope(config, layer):
    """対象環境の指定用途RGの完全Resource IDを組み立てる。"""
    return f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames'][layer]}"


def _assert_same(value, expected, label):
    """期待した所有IDと異なる場合、変更前に処理を止める。"""
    if not _same(value, expected):
        raise ValueError(label + " does not belong to the selected environment")


def _published_ids(apis, apim_id):
    """明示APIと必要な補助APIから、今回所有するAPIM子リソースIDを求める。"""
    names = [api["name"] for api in apis]
    names += [api["mcp"]["name"] for api in apis if "mcp" in api]
    return [apim_id + "/apis/" + name for name in names]


def prepare_plan(config: dict, state: dict) -> dict:
    """設定と配備記録から公開計画を作る。認証・通信・Azure変更は行わない。

    内部サービスの実URLを解決し、外部バックエンドは資格情報を含まない固定TLS宛先に
    制限する。構築済みの5RG・APIM・MIと一致しない入力はここで拒否する。
    """
    expected_groups = {layer: f"rg-{config['resourceStem']}-{layer}" for layer in LAYERS}
    if config.get("resourceGroupNames") != expected_groups or state.get("resourceGroupNames") != expected_groups:
        raise ValueError("Exactly the five selected core groups are required")
    for key in ("subscriptionId", "azureTenantId", "resourceStem"):
        _assert_same(state.get(key), config[key], "Deployment state")
    if state.get("apimInstanceId") != apim_instance_id(config):
        raise ValueError("Deployment state belongs to a different APIM construction instance")
    outputs = state.get("outputs", {})
    if outputs.get("resourceGroups") != expected_groups:
        raise ValueError("Core output groups differ from the selected environment")
    names = outputs.get("names", {})
    # 全サービスで「種別-環境名-プロダクト名」を使う。配備出力を名前の
    # 根拠にせず、現在の設定から期待値を作って照合し、別名の資源を公開しない。
    stem = config["resourceStem"]
    expected_names = {"apim": apim_name(config), "keyVault": "kv-" + stem,
        "foundry": "aif-" + stem, "project": "proj-" + stem,
        "apiCenter": "apic-" + stem}
    if names != expected_names:
        raise ValueError("Core service names differ from the selected naming policy")
    service_types = {"apim": "Microsoft.ApiManagement/service", "keyVault": "Microsoft.KeyVault/vaults",
                     "foundry": "Microsoft.CognitiveServices/accounts"}
    resource_ids = {key: _scope(config, "svc") + "/providers/" + kind + "/" + names[key]
                    for key, kind in service_types.items()}
    for key, expected in resource_ids.items():
        _assert_same(state.get("serviceResourceIds", {}).get(key), expected, "Cleanup resource")
        _assert_same(outputs.get("cleanup", {}).get("serviceResourceIds", {}).get(key), expected, "Core cleanup output")
        _assert_same(outputs.get("services", {}).get(key), expected, "Core service output")
    public_url = "https://" + config["gateway"]["publicHostName"]
    _assert_same(outputs.get("endpoints", {}).get("public", "").rstrip("/"), public_url, "Public Gateway URL")
    foundry_host = names["foundry"] + ".services.ai.azure.com"
    _assert_same(outputs.get("endpoints", {}).get("foundry"), "https://" + foundry_host, "Foundry endpoint")
    identity = outputs.get("certificateIdentity", {})
    identity_id = _scope(config, "id") + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-" + stem + "-certificate"
    _assert_same(identity.get("id"), identity_id, "Certificate identity")
    try:
        uuid.UUID(identity["clientId"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("Certificate identity client ID is invalid") from None
    grpc_on = grpc_lifecycle.enabled(config)
    ticket_on = grpc_lifecycle.owner_ticket_enabled(config)
    if grpc_on != outputs.get("grpc", {}).get("enabled", False):
        raise ValueError("Changing the TCP ingress requires core provisioning, not API-only publication")
    features = outputs.get("ingressSecrets", {})
    if (grpc_on or ticket_on) and (features.get("enabled") is not True or
            (ticket_on and features.get("ownerTicketEnabled", features.get("realtimeEnabled")) is not True)):
        raise ValueError("Ingress/ticket features must be prepared by the foundation deployment")
    if not config.get("apiDefinitions"):
        raise ValueError("API publication requires resolved API definitions")
    authentication = common_authentication_settings(config)
    for api in config["apiDefinitions"]:
        if not _same(api.get("subscriptionKeyHeader"), authentication["core-subscription-key-header"]):
            raise ValueError("API key headers must match the foundation gateway.authentication settings")
    resolved, external_hosts = [], set()
    bases = {"foundryOpenAi": "https://" + names["foundry"] + ".openai.azure.com/openai/v1",
             "foundryCognitive": "https://" + names["foundry"] + ".cognitiveservices.azure.com",
             "foundryServices": "https://" + foundry_host}
    def resolve_backend(api):
        if api["backendService"] == "external":
            try:
                parsed = urlsplit(api["backendUrl"])
                port = parsed.port
            except ValueError:
                raise ValueError("External backend URL is invalid; private input suppressed") from None
            if (parsed.scheme not in ("https", "wss") or port not in (None, 443) or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
                or any(re.search(r"key|token|secret|password|^sig$", key, re.I) for key, _ in parse_qsl(parsed.query))):
                raise ValueError("External backends require fixed TLS FQDNs without credential-bearing URLs")
            try:
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass
            else:
                raise ValueError("External Firewall application rules require an explicit DNS name")
            external_hosts.add(parsed.hostname.lower())
        else:
            api["backendUrl"] = bases[api["backendService"]] + api.get("backendPath", "")

    for source in config["apiDefinitions"]:
        api = copy.deepcopy(source)
        resolve_backend(api)
        for operation in api.get("operations", []):
            if "backendOverride" in operation:
                resolve_backend(operation["backendOverride"])
        resolved.append(api)
    login = credential_login.settings(config)
    if login:
        external_hosts.add("management.azure.com")
        external_hosts.update(urlsplit(p["identityEndpoint"]).hostname for p in login["providers"])
    resolved = with_catalog_descriptions(config, resolved)
    vault_base = "https://" + names["keyVault"] + ".vault.azure.net/secrets/"
    params = {"names": names,
        "apiDefinitions": resolved, "publishApis": True,
        "subscriptionKeyHeader": authentication["core-subscription-key-header"],
        "credentialLogin": credential_login.settings(config) or {},
        "rateLimits": config["gateway"].get("rateLimits", {
            "user": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60},
            "subscriptionKey": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60, "overrides": []},
            "application": {"enabled": False, "calls": 60, "renewalPeriodSeconds": 60, "overrides": []}}),
        "l7IngressSecretIdentifier": vault_base + grpc_lifecycle.SECRET_NAME if grpc_on else "",
        "realtimeTicketSecretIdentifier": vault_base + grpc_lifecycle.TICKET_NAME if ticket_on else ""}
    policy_id = _scope(config, "nw") + "/providers/Microsoft.Network/firewallPolicies/afwp-" + stem
    result = {"schemaVersion": 1, "subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
        "entraTenantId": entra_tenant_id(config),
        "resourceStem": stem, "resourceGroupNames": expected_groups, "azureRequests": 0,
        "commonAuthentication": authentication,
        "defaultSampleCleanup": cleanup_candidates(resolved),
        "releasedProduct": {"productId": RELEASED_PRODUCT_ID,
            "releasedApiNames": selected_api_names(resolved, released=True),
            "unreleasedApiNames": selected_api_names(resolved, released=False)},
        "credentialLogin": credential_login.settings(config),
        "apimId": resource_ids["apim"], "identityId": identity_id,
        "deploymentId": _scope(config, "svc") + "/providers/Microsoft.Resources/deployments/" + stem + "-api-publication",
        "parameters": {key: {"value": value} for key, value in params.items()},
        "webSocketDiagnostics": {
            "resourceId": resource_ids["apim"] + "/providers/Microsoft.Insights/diagnosticSettings/core-audit",
            "required": any(api.get("apiType") == "websocket" for api in resolved)},
        "publishedApiIds": _published_ids(resolved, resource_ids["apim"]) +
            ([resource_ids["apim"] + "/apis/" + name for name in (credential_login.API_ID, "credential-manager-browser")] if credential_login.settings(config) else []),
        "firewall": {"policyId": policy_id, "groupId": policy_id + "/ruleCollectionGroups/rcg-egress",
            "sourcePrefix": config["network"]["subnetPrefixes"]["apim"], "externalFqdns": sorted(external_hosts)},
        "note": "Publishes the selected APIs incrementally, verifies outputs, installs common key authorization, then removes unused default APIM samples. Preserves business APIs, custom global policy processing and existing Firewall rules."}
    return result


def _owned(body, resource_id, config, label, *, layer=None):
    """実資源のIDと所有タグを検査し、別環境や別用途の資源への変更を防ぐ。"""
    _assert_same(body.get("id"), resource_id, label)
    tags = body.get("tags", {})
    if any(tags.get(key) != config[value] for key, value in (("product", "productName"), ("environment", "environmentName"))):
        raise ValueError(label + " ownership tags differ from the selected environment")
    if layer and (tags.get("layer") != layer or tags.get("azd-env-name") != config["environmentName"]):
        raise ValueError("Resource-group layer/azd ownership tags differ")


def _etag(response):
    """同時変更を検出する具体的なETagを取り出す。無条件上書きの値は受け付けない。"""
    value = response.get("body", {}).get("etag") or next((v for k, v in response.get("headers", {}).items() if k.lower() == "etag"), None)
    if not isinstance(value, str) or not value.strip() or value.strip('" ') == "*":
        raise ValueError("Firewall ETag is required; no update was attempted")
    return value


def merge_firewall(response: dict, plan: dict) -> tuple[dict, bool, str]:
    """APIM送信元のHTTPS宛先FQDNだけを追加し、他のFW規則をすべて保持する。

    同名の既存規則でも送信元やプロトコルが異なれば、勝手に置換せず停止する。
    """
    body = require_success(response, "Read existing Firewall egress rules")
    _assert_same(body.get("id"), plan["firewall"]["groupId"], "Firewall rule group")
    etag = _etag(response)
    properties = copy.deepcopy(body.get("properties", {}))
    properties.pop("provisioningState", None)
    # size は Azure が規則内容から再計算する読取専用値。旧サイズをPUTや設定保持の
    # 比較へ含めると、正しく宛先を追加できても再読取で不一致と誤判定してしまう。
    # 除外はこの既知のメタデータだけとし、優先度・action・全既存規則は従来どおり比較する。
    # https://learn.microsoft.com/rest/api/firewall/firewall-policy-rule-collection-groups/get
    properties.pop("size", None)
    collections = properties.get("ruleCollections")
    if not isinstance(collections, list) or any(not isinstance(item, dict) for item in collections):
        raise ValueError("Firewall egress inventory is invalid")
    selected = [item for item in collections if item.get("name") == "allow-apim-egress"]
    if len(selected) != 1 or selected[0].get("action", {}).get("type") != "Allow" or selected[0].get("ruleCollectionType") != "FirewallPolicyFilterRuleCollection":
        raise ValueError("The owned APIM application rule collection is missing or changed")
    collection = selected[0]
    rules = collection.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("Firewall APIM rules are invalid")
    selected_rules = [rule for rule in rules if rule.get("name") == "apim-approved-services"]
    if len(selected_rules) > 1:
        raise ValueError("Firewall APIM FQDN rule is duplicated")
    hosts = plan["firewall"]["externalFqdns"]
    if not selected_rules and not hosts:
        return {"properties": properties}, False, etag
    if selected_rules:
        rule = selected_rules[0]
        if (rule.get("ruleType") != "ApplicationRule" or rule.get("sourceAddresses") != [plan["firewall"]["sourcePrefix"]]
            or rule.get("protocols") != [{"protocolType": "Https", "port": 443}]
            or rule.get("terminateTLS", False) is not False or not isinstance(rule.get("targetFqdns"), list)
            or any(not isinstance(host, str) for host in rule.get("targetFqdns", []))):
            raise ValueError("The existing APIM FQDN rule has a different source/protocol boundary")
        old = rule["targetFqdns"]
        missing = [host for host in hosts if host.lower() not in {entry.lower() for entry in old}]
        rule["targetFqdns"] += missing
        changed = bool(missing)
    else:
        rules.append({"name": "apim-approved-services", "ruleType": "ApplicationRule",
            "sourceAddresses": [plan["firewall"]["sourcePrefix"]], "protocols": [{"protocolType": "Https", "port": 443}],
            "targetFqdns": hosts, "terminateTLS": False})
        changed = True
    return {"properties": properties}, changed, etag


def _contains(actual, expected):
    """読み直した設定が要求した入れ子の値を保持しているか、構造に沿って比較する。"""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_contains(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _wait_resource(transport, resource_id, api, *, sleep, deadline):
    """配備・FW更新の状態を読み、成功が確定するまで次の変更を始めない。"""
    while time.monotonic() < deadline:
        body = require_success(transport.request("GET", resource_id + "?api-version=" + api), "Read owned deployment operation")
        state = body.get("properties", {}).get("provisioningState")
        if state == "Succeeded":
            return body
        if state in ("Failed", "Canceled", "Cancelled", "Deleted"):
            raise DeploymentError("Owned publication operation did not succeed; inspect its status before retrying")
        sleep(5)
    raise DeploymentError("Owned publication operation is still pending; no redeployment or rollback was attempted")


def validate_publication_template(template):
    """配備できる型をAPIMの子定義だけに限定し、ARMの配列・シンボル形式を検査する。

    BicepのlanguageVersion 2.0ではresourcesが名前付きobjectになり、既存APIMの
    参照もexisting:trueとして残る。この正確な読取参照だけを例外とし、APIM本体を
    更新する宣言や別の資源型を許可しない。空の計画や不正な要素も変更前に拒否する。
    """
    message = "Only the API publication module may be deployed"
    if not isinstance(template, dict):
        raise ValueError(message)
    resources = template.get("resources")
    symbolic = isinstance(resources, dict)
    if symbolic:
        if (template.get("languageVersion") != "2.0" or not resources
                or any(not isinstance(name, str) or not name for name in resources)):
            raise ValueError(message)
        entries = resources.values()
    elif isinstance(resources, list) and resources:
        entries = resources
    else:
        raise ValueError(message)
    deployed_children = 0
    for resource in entries:
        if (not isinstance(resource, dict) or not isinstance(resource.get("type"), str)
                or "resources" in resource
                or ("existing" in resource and type(resource["existing"]) is not bool)):
            raise ValueError(message)
        resource_type = resource["type"].lower()
        if resource_type == "microsoft.apimanagement/service/policies":
            raise ValueError("API publication must preserve the APIM global policy")
        if (resource_type == "microsoft.apimanagement/service/namedvalues"
                and resource.get("existing") is not True
                and any(name in json.dumps(resource).lower() for name in COMMON_NAMED_VALUES)):
            raise ValueError("API publication must preserve the foundation authentication NamedValues")
        if resource_type == "microsoft.apimanagement/service":
            if (not symbolic or resource.get("existing") is not True
                    or set(resource) != {"existing", "type", "apiVersion", "name"}
                    or resource["apiVersion"] != APIM_API
                    or resource["name"] != "[parameters('names').apim]"):
                raise ValueError(message)
        elif re.fullmatch(r"microsoft\.apimanagement/service/[a-z][a-z0-9]*(?:/[a-z][a-z0-9]*)*", resource_type):
            if resource.get("existing") is not True:
                deployed_children += 1
        else:
            raise ValueError(message)
    if not deployed_children:
        raise ValueError(message)


def _checked_prior_ids(values, apim_id):
    """記録されたIDが今回のAPIMのAPIを指すことだけを確認する。"""
    prefix = apim_id.casefold() + "/apis/"
    if (not isinstance(values, list) or any(not isinstance(value, str)
            or not value.casefold().startswith(prefix)
            or not API_NAME.fullmatch(value[len(prefix):]) for value in values)):
        raise ValueError("Previous publication API scope could not be verified")
    return values


def _prior_published_ids(prior, config, plan):
    """成功済みの公開一覧、または所有を確認した失敗配備の公開対象を読み取る。

    API 公開は Incremental なので、一部の子リソースが成功した後で失敗することがある。
    Failed には成功時 outputs がない場合もあるため、同じ配備に保存された公開入力から
    保存済み ID の所属を確認する。次回の要求に全 ID を含める必要はない。
    実行中・未知の配備を失敗とみなしたり、別環境の保存入力を流用したりはしない。
    """
    properties = prior.get("properties") if isinstance(prior, dict) else None
    if not isinstance(properties, dict):
        raise ValueError("Previous publication state is invalid")
    status = properties.get("provisioningState")
    outputs = properties.get("outputs")
    if outputs is None:
        outputs = {}
    if not isinstance(outputs, dict):
        raise ValueError("Previous publication output is invalid")
    if status == "Succeeded":
        observed = outputs.get("publishedApiResourceIds")
        previous = observed.get("value") if isinstance(observed, dict) else None
        if not isinstance(previous, list) or any(not isinstance(value, str) for value in previous):
            raise ValueError("Previous publication output is invalid")
        return _checked_prior_ids(previous, plan["apimId"])
    if status != "Failed":
        raise ValueError("The existing publication deployment must be reconciled before an API-only update")
    _assert_same(prior.get("id"), plan["deploymentId"], "Failed publication deployment")
    _assert_same(prior.get("name"), plan["deploymentId"].rsplit("/", 1)[-1], "Failed publication name")
    _assert_same(prior.get("type"), "Microsoft.Resources/deployments", "Failed publication type")
    tags = prior.get("tags")
    if (not isinstance(tags, dict) or tags.get("azd-env-name") != config["environmentName"]
            or tags.get("azd-project-name") != PROJECT_NAME or properties.get("mode") != "Incremental"):
        raise ValueError("Failed publication ownership or deployment mode could not be verified")
    if "outputResources" in properties:
        resources = properties["outputResources"]
        if (not isinstance(resources, list) or any(not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"].casefold().startswith(plan["apimId"].casefold() + "/")
                or any(segment in (".", "..") for segment in item["id"].split("/"))
                or any(char in item["id"] for char in "?#\r\n") for item in resources)):
            raise ValueError("Failed publication resource output is outside the selected API Management service")
    saved = properties.get("parameters")
    if not isinstance(saved, dict):
        raise ValueError("Failed publication parameters could not be verified")

    def value(name):
        item = saved.get(name)
        if not isinstance(item, dict) or "value" not in item:
            raise ValueError("Failed publication parameters could not be verified")
        return item["value"]

    names = value("names")
    expected_names = plan["parameters"]["names"]["value"]
    if (not isinstance(names, dict) or set(names) != set(expected_names)
            or any(not _same(names[key], expected) for key, expected in expected_names.items())
            or not _same(value("subscriptionKeyHeader"), plan["parameters"]["subscriptionKeyHeader"]["value"])
            or value("publishApis") is not True):
        raise ValueError("Failed publication service names or subscription key header differ from the selected environment")
    apis = value("apiDefinitions")
    if not isinstance(apis, list) or not apis:
        raise ValueError("Failed publication API definitions could not be verified")
    seen = set()
    for api in apis:
        if not isinstance(api, dict):
            raise ValueError("Failed publication API identity could not be verified")
        entries = [api]
        if "mcp" in api:
            entries.append(api["mcp"])
        for entry in entries:
            name = entry.get("name") if isinstance(entry, dict) else None
            if (not isinstance(name, str) or not API_NAME.fullmatch(name)
                    or name.casefold() in seen):
                raise ValueError("Failed publication API identity could not be verified")
            seen.add(name.casefold())
    previous = _published_ids(apis, plan["apimId"])
    if isinstance(saved.get("credentialLogin"), dict) and saved["credentialLogin"].get("value"):
        previous.extend(plan["apimId"] + "/apis/" + name for name in (credential_login.API_ID, "credential-manager-browser"))
    # 成功出力が残っている場合も、今回のAPIMのIDであることを確認する。
    if "publishedApiResourceIds" in outputs:
        observed = outputs["publishedApiResourceIds"]
        if not isinstance(observed, dict) or not isinstance(observed.get("value"), list) or any(not isinstance(item, str) for item in observed["value"]):
            raise ValueError("Previous publication output is invalid")
        previous.extend(observed["value"])
    return _checked_prior_ids(previous, plan["apimId"])


def verify_subscription_products(plan, transport):
    """対象APIに購読不要Productが残る場合、共通キーへ移行する前に停止する。"""
    for api_id in plan["publishedApiIds"]:
        if api_id.endswith("/credential-manager-browser"):
            continue
        response = transport.request("GET", api_id + "/products?api-version=" + APIM_API)
        if response.get("statusCode") == 404:
            continue
        body = require_success(response, "Read API subscription product membership")
        if body.get("nextLink") or not isinstance(body.get("value"), list):
            raise ValueError("API product membership must be reconciled before key authorization publication")
        for product in body["value"]:
            if not isinstance(product, dict) or product.get("properties", {}).get("subscriptionRequired") is not True:
                raise ValueError("Remove the reviewed API association with subscription-free Products before key authorization publication; no changes were attempted")


def verify_common_authentication(plan, transport):
    """基盤の名前付きの値を照合し、共通ポリシーの変更計画を読み取りだけで作る。"""
    path = plan["apimId"] + "/policies/policy?api-version=" + APIM_API
    response = transport.request("GET", path + "&format=rawxml", headers={"Accept": "application/json"})
    missing = response.get("statusCode") == 404
    policy = None if missing else require_success(response, "Read APIM global policy")
    properties = policy.get("properties", {}) if isinstance(policy, dict) else {}
    merged, changed = merge_common_authentication_policy(None if missing else properties.get("value"), plan.get("credentialLogin"),
                                                       migrate_credential_broker=bool(plan.get("credentialLogin")))
    headers = {"If-None-Match": "*"} if missing else ({"If-Match": _etag(response)} if changed else {})
    for name, expected in plan["commonAuthentication"].items():
        response = transport.request("GET", plan["apimId"] + "/namedValues/" + name + "?api-version=" + APIM_API)
        if response.get("statusCode") == 404:
            raise ValueError(MIGRATION_REQUIRED)
        named = require_success(response, "Read foundation common authentication NamedValue")
        validate_common_named_value(named, name, expected)
    return {"path": path, "value": merged, "changed": changed, "headers": headers, "credentialLogin": plan.get("credentialLogin")}


def verify_credential_login(config, plan, apim, transport):
    """本人接続brokerと全CMプールを読み合わせ、準備不足のままAPIを公開しない。"""
    login = plan.get("credentialLogin")
    if not login:
        return
    names = resource_names(config)
    broker_identity_id = _scope(config, "id") + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/" + names["runtimeIdentity"]
    identity = require_success(transport.request("GET", broker_identity_id + "?api-version=2023-01-31"), "Read credential broker identity")
    _assert_same(identity.get("id"), broker_identity_id, "Credential broker identity")
    broker_principal = identity.get("properties", {}).get("principalId")
    if not isinstance(broker_principal, str) or not broker_principal:
        raise ValueError("Credential broker identity is not provisioned")
    app_id = _scope(config, "svc") + "/providers/Microsoft.App/containerApps/" + names["application"]
    app = require_success(transport.request("GET", app_id + "?api-version=2025-01-01"), "Read credential broker application")
    _assert_same(app.get("id"), app_id, "Credential broker application")
    host = app.get("properties", {}).get("configuration", {}).get("ingress", {}).get("fqdn")
    if (not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", host)
            or not host.startswith(names["application"] + ".")):
        raise ValueError("Credential broker internal application address is invalid")
    app_properties = app.get("properties", {})
    containers = app_properties.get("template", {}).get("containers", [])
    if (app_properties.get("provisioningState") != "Succeeded"
            or not app_properties.get("latestReadyRevisionName")
            or app_properties.get("latestReadyRevisionName") != app_properties.get("latestRevisionName")
            or len(containers) != 1
            or not re.fullmatch(r"[a-z0-9]+\.azurecr\.io/credential-broker@sha256:[a-f0-9]{64}", containers[0].get("image", ""))
            or not any(_same(identity_id, broker_identity_id)
                       for identity_id in app.get("identity", {}).get("userAssignedIdentities", {}))):
        raise ValueError("Deploy the credential broker image and database schema successfully before publishing its APIs")
    broker_url = "https://" + host
    backend = require_success(transport.request("GET", plan["apimId"] + "/backends/credential-broker?api-version=" + APIM_API), "Read credential broker backend")
    if backend.get("properties", {}).get("url") != broker_url:
        raise ValueError("Credential broker backend does not match the provisioned application")
    named = require_success(transport.request("GET", plan["apimId"] + "/namedValues/core-credential-broker-url?api-version=" + APIM_API), "Read credential broker address")
    validate_common_named_value(named, "core-credential-broker-url", broker_url)
    secret = require_success(transport.request("GET", plan["apimId"] + "/namedValues/core-credential-broker-secret?api-version=" + APIM_API), "Read credential broker secret reference")
    properties = secret.get("properties", {})
    expected_secret = "https://kv-" + config["resourceStem"] + ".vault.azure.net/secrets/core-credential-broker-key"
    if (properties.get("secret") is not True or properties.get("value")
            or properties.get("keyVault", {}).get("secretIdentifier") != expected_secret):
        raise ValueError("Credential broker must use the provisioned Key Vault secret reference")
    for expected in physical_providers(config):
        if expected["sourceName"] not in login["providerNames"]:
            continue
        provider_name = expected["name"]
        provider_id = plan["apimId"] + "/authorizationProviders/" + provider_name
        provider = require_success(transport.request("GET", provider_id + "?api-version=" + APIM_API), "Read prepared Gateway login provider")
        properties = provider.get("properties", {})
        oauth = properties.get("oauth2", {}).get("grantTypes", {}).get("authorizationCode", {})
        if (not _same(provider.get("id"), provider_id) or properties.get("identityProvider") != "aad"
                or any(oauth.get(key) != expected[key] for key in ("clientId", "resourceUri", "scopes"))
                or not _same(oauth.get("tenantId"), expected["oauthTenantId"])):
            raise ValueError("Gateway login provider differs from the prepared Entra configuration")
        principal = apim.get("identity", {}).get("principalId")
        assignments = require_success(transport.request("GET", provider_id + "/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01"), "Read Gateway login access")
        if assignments.get("nextLink"):
            raise ValueError("Gateway login access listing must be reconciled before publication")
        # API Management Service Contributorを、対象providerだけに割り当てます。
        role_id = ("/subscriptions/" + config["subscriptionId"]
                   + "/providers/Microsoft.Authorization/roleDefinitions/312a565d-c81f-4fd8-895a-4e21e48d571c")
        for required_principal in (principal, broker_principal):
            if not any(_same(item.get("properties", {}).get("principalId"), required_principal)
                       and _same(item.get("properties", {}).get("scope"), provider_id)
                       and _same(item.get("properties", {}).get("roleDefinitionId"), role_id)
                       for item in assignments.get("value", [])):
                raise ValueError("Assign API Management Service Contributor to both APIM and broker identities at each Credential Manager provider scope")


def install_common_authentication(prepared, transport, *, sleep, deadline):
    """ETag 付きで共通ポリシーを保存し、反映を照合してから API 登録へ進む。"""
    if prepared["changed"]:
        response = transport.request("PUT", prepared["path"], {"properties": {
            "format": "rawxml", "value": prepared["value"]}}, headers=prepared["headers"])
        require_success(response, "Install common authentication", allowed=(200, 201, 202))
    while time.monotonic() < deadline:
        body = require_success(transport.request("GET", prepared["path"] + "&format=rawxml", headers={"Accept": "application/json"}),
                               "Confirm common authentication")
        properties = body.get("properties", {})
        if properties.get("provisioningState") in ("Failed", "Canceled", "Cancelled"):
            raise DeploymentError("Common authentication policy update failed")
        if properties.get("provisioningState") not in ("Accepted", "Creating", "Updating", "InProgress"):
            validate_common_authentication_policy(properties.get("value"), prepared.get("credentialLogin"))
            # 保存した XML 全体を照合し、既存の追加処理も保持されたことを確認する。
            from deployment.helpers.apim_authentication import _document, _structure
            if _structure(_document(properties["value"])) != _structure(_document(prepared["value"])):
                raise ValueError("Global policy readback differs from the reviewed merge")
            return
        sleep(5)
    raise DeploymentError("Common authentication policy update is still pending")


def _diagnostic_entries(entries):
    """ログ・メトリックをカテゴリ別に照合し、Azureの返却順に依存しない。"""
    if entries is None:
        return {}
    if not isinstance(entries, list):
        raise ValueError("Core audit diagnostic categories are invalid")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled"), bool):
            raise ValueError("Core audit diagnostic categories are invalid")
        keys = [key for key in ("category", "categoryGroup") if entry.get(key)]
        if len(keys) != 1 or not isinstance(entry[keys[0]], str):
            raise ValueError("Core audit diagnostic categories are invalid")
        key = (keys[0], entry[keys[0]].casefold())
        if key in result:
            raise ValueError("Core audit diagnostic categories are duplicated")
        result[key] = entry
    return result


def _diagnostic_entry_matches(actual, expected):
    """省略可能なnullとAzureが補う既定値を、収集設定の変更と区別する。"""
    def normalized(entry):
        entry = copy.deepcopy(entry)
        for key in ("category", "categoryGroup"):
            if entry.get(key) is None:
                entry.pop(key, None)
        if entry.get("retentionPolicy") is None:
            entry["retentionPolicy"] = {"enabled": False, "days": 0}
        return entry
    return _contains(normalized(actual), normalized(expected))


def _read_websocket_diagnostics(plan, state, transport):
    """構築済みの監査設定と保存先を確認する。秘密値や別の診断設定は読まない。"""
    resource_id = plan["webSocketDiagnostics"]["resourceId"]
    body = require_success(transport.request("GET", resource_id + "?api-version=" + MONITOR_API),
                           "Read existing Core audit diagnostics")
    _assert_same(body.get("id"), resource_id, "Core audit diagnostic setting")
    properties = body.get("properties")
    # このAPIの書込契約にある項目をすべて保持する。未知の項目を黙って捨てない。
    # https://learn.microsoft.com/rest/api/monitor/diagnostic-settings/create-or-update
    writable = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "eventHubName",
                "serviceBusRuleId", "marketplacePartnerId", "logAnalyticsDestinationType", "logs", "metrics"}
    if not isinstance(properties, dict) or set(properties) - writable - {"provisioningState"}:
        raise ValueError("Core audit diagnostic properties require review")
    outputs = state.get("outputs", {})
    workspace_id = outputs.get("monitoring", {}).get("workspaceId")
    storage_id = outputs.get("auditStorage", {}).get("collectJapan", {}).get("id")
    if not workspace_id or not storage_id:
        raise ValueError("Core audit destinations are missing from the deployment state")
    _assert_same(properties.get("workspaceId"), workspace_id, "Core audit workspace")
    _assert_same(properties.get("storageAccountId"), storage_id, "Core audit storage")
    if not isinstance(properties.get("logs"), list):
        raise ValueError("Core audit diagnostic categories are invalid")
    _diagnostic_entries(properties["logs"])
    if "metrics" in properties:
        _diagnostic_entries(properties["metrics"])
    return {key: copy.deepcopy(value) for key, value in properties.items() if key in writable}


def verify_websocket_diagnostics(plan, state, transport):
    """WebSocket公開に必要な既存監査設定を、API変更前に読み合わせる。"""
    if plan["webSocketDiagnostics"]["required"]:
        _read_websocket_diagnostics(plan, state, transport)


def enable_websocket_diagnostics(plan, state, transport, *, sleep, deadline):
    """公開後にWebSocket接続ログを有効化し、他の監査設定の保持を確認する。"""
    setting = plan["webSocketDiagnostics"]
    result = {"required": setting["required"], "updated": False, "status": "not-required"}
    if not setting["required"]:
        return result
    result.update(resourceId=setting["resourceId"], status="enabled")
    # 診断設定APIにはETagの条件付き更新契約がないため、更新直前の現値を使う。
    # 事前検査以降に加わったカテゴリや保存先の追加設定も、この読取結果から保持する。
    properties = _read_websocket_diagnostics(plan, state, transport)
    logs = _diagnostic_entries(properties["logs"])
    websocket = logs.get(("category", "websocketconnectionlogs"))
    all_logs = logs.get(("categoryGroup", "alllogs"))
    if (websocket and websocket["enabled"]) or (all_logs and all_logs["enabled"]):
        return result
    if websocket is None:
        properties["logs"].append({"category": "WebSocketConnectionLogs", "enabled": True})
    else:
        websocket["enabled"] = True
    path = setting["resourceId"] + "?api-version=" + MONITOR_API
    require_success(transport.request("PUT", path, {"properties": properties}),
                    "Enable WebSocket connection logs", allowed=(200, 201, 202))
    # 小さな診断設定の読戻しで、API配備全体の待機上限まで無応答にならないようにする。
    readback_deadline = min(deadline, time.monotonic() + 60)
    while time.monotonic() < readback_deadline:
        observed = _read_websocket_diagnostics(plan, state, transport)
        retained = True
        for key, expected in properties.items():
            if key in ("logs", "metrics"):
                actual = _diagnostic_entries(observed.get(key))
                retained = retained and all(name in actual and _diagnostic_entry_matches(actual[name], value)
                                           for name, value in _diagnostic_entries(expected).items())
            elif key in ("workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "serviceBusRuleId", "marketplacePartnerId") and expected:
                retained = retained and _same(observed.get(key), expected)
            else:
                retained = retained and _contains(observed.get(key), expected)
        if retained:
            result["updated"] = True
            return result
        sleep(5)
    raise DeploymentError("WebSocket diagnostic update could not be confirmed; check Core audit settings before retrying")


def execute_plan(config, state, plan, template, transport, *, sleep=time.sleep, timeout_seconds=1800):
    """全所有資源と事前条件を読み合わせてから、FW宛先追加とAPI公開を実行する。

    既存モデルの作成・変更はここでは行わず、必要なRBACや秘密参照も準備済みか検査する。
    以前の配備は所有者とAPIの所属先を照合し、指定したAPIだけを増分更新する。
    FWはETag付き更新後に読み直し、確認できてからAPIMの子定義だけを配備する。
    公開と共通設定の成功後に、業務利用されていないAPIM初期サンプルを整理する。
    """
    if config.get("publishApis") is not True:
        raise ValueError("Set publishApis=true before executing API publication")
    if prepare_plan(config, state) != plan:
        raise ValueError("The publication plan differs from the validated inputs")
    validate_publication_template(template)
    for layer in LAYERS:
        rid = _scope(config, layer)
        body = require_success(transport.request("GET", rid + "?api-version=2024-03-01"), "Read owned resource group")
        _owned(body, rid, config, "Resource group", layer=layer)
    apim = require_success(transport.request("GET", plan["apimId"] + "?api-version=" + APIM_API), "Read owned API Management")
    _owned(apim, plan["apimId"], config, "API Management")
    if apim.get("properties", {}).get("virtualNetworkType") != "Internal":
        raise ValueError("The existing internal API Management deployment is required")
    if grpc_lifecycle.enabled(config) and str(apim.get("properties", {}).get("customProperties", {}).get(grpc_lifecycle.HTTP2, "")).lower() != "true":
        raise ValueError("Foundation must enable HTTP/2 before publishing native gRPC")
    verify_subscription_products(plan, transport)
    authentication_policy = verify_common_authentication(plan, transport)
    verify_credential_login(config, plan, apim, transport)
    verify_websocket_diagnostics(plan, state, transport)
    identity = require_success(transport.request("GET", plan["identityId"] + "?api-version=2023-01-31"), "Read existing certificate identity")
    _owned(identity, plan["identityId"], config, "Certificate identity")
    if identity.get("properties", {}).get("clientId") != state["outputs"]["certificateIdentity"]["clientId"]:
        raise ValueError("The certificate identity was recreated; core bindings must be reconciled first")
    fw = plan["firewall"]
    policy = require_success(transport.request("GET", fw["policyId"] + "?api-version=" + NETWORK_API), "Read owned Firewall policy")
    _owned(policy, fw["policyId"], config, "Firewall policy")
    subnet_id = _scope(config, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-" + config["resourceStem"] + "/subnets/snet-" + config["resourceStem"] + "-apim"
    subnet = require_success(transport.request("GET", subnet_id + "?api-version=" + NETWORK_API), "Read APIM subnet")
    _assert_same(subnet.get("id"), subnet_id, "APIM subnet")
    if subnet.get("properties", {}).get("addressPrefix") != fw["sourcePrefix"]:
        raise ValueError("API-only publication cannot change the APIM subnet prefix")
    # モデルやSpeechの実行権限は基盤構築で付いていることを先に読む。
    # API公開の途中で権限やモデル本体まで作り替える責務を持たせない。
    role_requirements = []
    settings = config.get("services", {})
    if settings.get("speechEnabled"):
        role_requirements.append("f2dc8367-1007-4938-bd23-fe263f013447")
    if settings.get("grantApimFoundryModelsAccess"):
        role_requirements.append("a97b65f3-24c7-4388-baec-2e87135dc908")
    models = settings.get("modelDeployments", [])
    if role_requirements or models:
        foundry_id = state["serviceResourceIds"]["foundry"]
        foundry = require_success(transport.request("GET", foundry_id + "?api-version=2025-06-01"), "Read owned Foundry model resource")
        _owned(foundry, foundry_id, config, "Foundry")
        if foundry.get("properties", {}).get("disableLocalAuth") is not True:
            raise ValueError("Foundry local-key authentication must remain disabled")
    if role_requirements:
        principal = apim.get("identity", {}).get("principalId")
        try:
            uuid.UUID(principal)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Core API Management system identity is not prepared") from None
        assignments = require_success(transport.request("GET", foundry_id + "/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&$filter=principalId%20eq%20%27" + principal + "%27"), "Read prepared model invocation roles")
        if assignments.get("nextLink") or not isinstance(assignments.get("value"), list):
            raise ValueError("Model role inventory is incomplete; no API changes were attempted")
        active_roles = {item.get("properties", {}).get("roleDefinitionId", "").rsplit("/", 1)[-1].lower()
            for item in assignments["value"] if _same(item.get("properties", {}).get("principalId"), principal)
            and _same(item.get("properties", {}).get("scope"), foundry_id)}
        if any(role not in active_roles for role in role_requirements):
            raise ValueError("Required Speech/Foundry model roles must be prepared by foundation provisioning")
    for model in models:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", model["name"]):
            raise ValueError("Model deployment identifier is invalid")
        rid = foundry_id + "/deployments/" + model["name"]
        actual = require_success(transport.request("GET", rid + "?api-version=2025-06-01"), "Read prepared model deployment")
        _assert_same(actual.get("id"), rid, "Model deployment")
        properties = actual.get("properties", {})
        expected_model = {"name": model["modelName"], "version": model["modelVersion"], "format": model["modelFormat"]}
        if (properties.get("provisioningState") != "Succeeded" or not _contains(properties.get("model"), expected_model)
            or actual.get("sku", {}).get("name") != model["skuName"] or actual.get("sku", {}).get("capacity") != model["capacity"]):
            raise ValueError("API-only publication cannot create or change model deployments; reconcile foundation first")
    prior_response = transport.request("GET", plan["deploymentId"] + "?api-version=" + DEPLOYMENT_API)
    # 前回配備の所有者・状態だけを照合し、全 API 一覧の再提出は要求しない。
    # Incremental 配備は指定しない API を削除せず、全体 policy も変更しない。
    # 基盤構築は API を登録しないため、初回の404には過去配備がない。
    if prior_response.get("statusCode") != 404:
        prior = require_success(prior_response, "Read prior API publication")
        _prior_published_ids(prior, config, plan)
    parameters = {name: item["value"] for name, item in plan["parameters"].items()}
    for prefix in ("l7Ingress", "realtimeTicket"):
        secret_id = parameters[prefix + "SecretIdentifier"]
        if not secret_id:
            continue
        # APIM の KV 参照は system MI。AppGW の証明書用 UAMI の有無で判定しない。
        managed = apim.get("identity", {})
        try:
            uuid.UUID(managed.get("principalId"))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Core API Management system identity is not prepared") from None
        if "SystemAssigned" not in {value.strip() for value in str(managed.get("type", "")).split(",")}:
            raise ValueError("Core API Management system identity is not prepared")
        name = secret_id.rsplit("/", 1)[-1]
        named = require_success(transport.request("GET", plan["apimId"] + "/namedValues/" + name + "?api-version=" + APIM_API), "Read prepared ingress secret reference")
        kv = named.get("properties", {}).get("keyVault", {})
        if (kv.get("secretIdentifier", "").rstrip("/") != secret_id or kv.get("identityClientId") is not None
            or kv.get("lastStatus", {}).get("code", "").lower() != "success"):
            raise ValueError("Ingress/ticket Key Vault references must be initialized by foundation provisioning")
    # ここまでの処理はすべて読取。全前提を確認できてから初めてFWとAPIMを更新する。
    # FWを変更した場合は再読取で保持を確認し、その後にAPI公開を始める。
    snapshot = transport.request("GET", fw["groupId"] + "?api-version=" + NETWORK_API)
    body, changed, etag = merge_firewall(snapshot, plan)
    deadline = time.monotonic() + timeout_seconds
    if changed:
        response = transport.request("PUT", fw["groupId"] + "?api-version=" + NETWORK_API, body, headers={"If-Match": etag})
        require_success(response, "Update APIM-only Firewall FQDNs", allowed=(200, 201, 202))
        observed = _wait_resource(transport, fw["groupId"], NETWORK_API, sleep=sleep, deadline=deadline)
        if not _contains(observed.get("properties"), body["properties"]):
            raise ValueError("Firewall readback did not preserve the approved rules; API publication was not attempted")
    # 初回の接続だけ無効で準備します。Portalが設定したURL・KV参照は保持します。
    initialize_profile_settings(plan["apimId"], transport)
    deployment = {"tags": {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME},
        "properties": {"mode": "Incremental", "template": template, "parameters": plan["parameters"]}}
    response = transport.request("PUT", plan["deploymentId"] + "?api-version=" + DEPLOYMENT_API, deployment)
    require_success(response, "Start API-only publication", allowed=(200, 201, 202))
    observed = _wait_resource(transport, plan["deploymentId"], DEPLOYMENT_API, sleep=sleep, deadline=deadline)
    published = observed.get("properties", {}).get("outputs", {}).get("publishedApiResourceIds", {}).get("value", [])
    # 指定した業務APIとCM接続用APIのすべてが配備されたことを確認する。
    expected = plan["publishedApiIds"]
    if not isinstance(published, list) or {value.lower() for value in published} != {value.lower() for value in expected}:
        raise ValueError("API publication output did not confirm the reviewed API set")
    # Incremental配備では消えない関連を、今回明示した未リリースAPIに限って解除する。
    # API本体・検証用Product・購読は保持し、指定外のAPIへは触れない。
    released_product = reconcile_unreleased_apis(plan["apimId"], parameters["apiDefinitions"], transport, sleep=sleep)
    websocket_diagnostics = enable_websocket_diagnostics(plan, state, transport, sleep=sleep, deadline=deadline)
    # API側のキー必須設定と出力を確認してから全体policyを切り替えます。
    # 途中失敗時は共通入口を切り替えず、失敗配備記録から同じ計画を再実行できます。
    install_common_authentication(authentication_policy, transport, sleep=sleep, deadline=deadline)
    # 通常 up と同じ仕上げを行い、proto 再取り込み後も公開接続説明を保つ。
    catalog_descriptions = finalize_descriptions(config, state["outputs"], transport)
    # 正式APIの公開が完了してから、APIMが初期作成するサンプルだけを整理する。
    # 未登録・削除済みは正常。業務用に転用した同名定義や、その購読は保持する。
    try:
        sample_cleanup = cleanup_default_samples(plan["apimId"], parameters["apiDefinitions"], transport, sleep=sleep)
    except (ValueError, DeploymentError) as error:
        raise DeploymentError("APIs were published, but default APIM sample cleanup did not finish: " + str(error)) from error
    # 通常構築と同じ状態形式を使い、APIだけの更新でも撤去用IDが変わらないことを確認する。
    preserved_state = state_from_outputs(config, state["outputs"])
    return {"schemaVersion": 1, "subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
        "entraTenantId": entra_tenant_id(config),
        "resourceStem": config["resourceStem"], "deploymentId": plan["deploymentId"], "apimId": plan["apimId"],
        "firewallUpdated": changed, "commonAuthenticationPolicyUpdated": authentication_policy["changed"],
        "webSocketDiagnostics": websocket_diagnostics,
        "releasedProduct": released_product,
        "publishedApiIds": published, "status": "management-plane-published",
        "runtimeVerified": False, "catalogDescriptions": catalog_descriptions,
        "defaultSampleCleanup": sample_cleanup,
        "cleanupIdsPreserved": preserved_state["serviceResourceIds"] == state["serviceResourceIds"]}
