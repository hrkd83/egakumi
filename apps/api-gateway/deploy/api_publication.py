#!/usr/bin/env python3
"""Gateway配備から呼び出す、APIMのAPI定義とポリシーの公開処理。

公開入口scripts/deployment/deploy_gateway.pyが配備順序と実行指定を管理する。
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
import hashlib
import json
import re
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from deployment.helpers.configuration import API_NAME, entra_app_separation_enabled, entra_tenant_id
from deployment.helpers.apim_naming import apim_instance_id, apim_name
from deployment.helpers.apim_egress import APIM_EGRESS_RULE_NAME, validate_apim_egress_rule
from deployment.helpers.firewall_destinations import generated_apim_service_fqdns
from deployment.helpers.catalog_descriptions import with_catalog_descriptions
from deployment.helpers.apim_authentication import (
    CLOSED_INITIAL_POLICY, COMMON_NAMED_VALUES, ENTRA_NAMED_VALUES, MIGRATION_REQUIRED,
    common_authentication_settings, entra_authentication_settings, is_closed_initial_policy,
    entra_named_value_settings, entra_authentication_named_values, validate_entra_named_values, validate_entra_named_value,
    validate_common_authentication_policy, validate_common_named_value, merge_common_authentication_policy,
    read_managed_entra_authentication, transition_common_authentication_policy, ENTRA_OPEN_PRODUCT_ID,
)
from deployment.helpers.apim_common_authentication import AUTHENTICATION_FRAGMENT_IDS
from deployment.helpers import ingress
from deployment.helpers import credential_login
from deployment.helpers.credential_pools import physical_providers, resource_names
from deployment.helpers.credential_broker_lifecycle import broker_environment_id, broker_https_ingress
from deployment.helpers.apim_samples import cleanup_candidates, cleanup_default_samples
from deployment.helpers.released_apis import RELEASED_PRODUCT_ID, selected_api_names, reconcile_unreleased_apis
from deployment.helpers.subnet_names import subnet_resource_id
from deployment.lifecycle import DeploymentError, PROJECT_NAME, ROOT, require_success, state_from_outputs
import entra_publication
from publication_templates import (
    canonical_publication_template, publication_parameters, split_publication_templates,
    source_generation_id, validate_source_generation_id,
)

APIM_API = "2024-05-01"
NETWORK_API = "2025-01-01"
DEPLOYMENT_API = "2022-09-01"
MONITOR_API = "2021-05-01-preview"
ARM_TEMPLATE_FUNCTION_LIMIT = 64
PUBLICATION_API_TYPES = ("http", "websocket", "mcp")
MODULE = ROOT / "apps/api-gateway/api-publication.bicep"
LAYERS = ("id", "nw", "monitor", "svc", "maint")
SOURCE_GENERATION_OUTPUT = "publicationSourceGenerationId"
SOURCE_PRINCIPAL_OUTPUT = "publicationSourcePrincipalId"
SOURCE_PRINCIPAL_EXPRESSION = "[reference('apim', '2024-05-01', 'full').identity.principalId]"


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


def _foundry_openai_origin(foundry_name):
    return "https://" + foundry_name + ".openai.azure.com"


def prepare_plan(config: dict, state: dict) -> dict:
    """設定と配備記録から公開計画を作る。認証・通信・Azure変更は行わない。

    内部サービスの実URLを解決し、外部バックエンドは資格情報を含まない固定TLS宛先に
    制限する。構築済みの5RG・APIM・MIと一致しない入力はここで拒否する。
    """
    entra = entra_authentication_settings(config)
    expected_groups = {layer: f"rg-{config['resourceStem']}-{layer}" for layer in LAYERS}
    if config.get("resourceGroupNames") != expected_groups or state.get("resourceGroupNames") != expected_groups:
        raise ValueError("Exactly the five selected infrastructure resource groups are required")
    for key in ("subscriptionId", "azureTenantId", "resourceStem"):
        _assert_same(state.get(key), config[key], "Deployment state")
    if state.get("apimInstanceId") != apim_instance_id(config):
        raise ValueError("Deployment state belongs to a different APIM construction instance")
    outputs = state.get("outputs", {})
    if outputs.get("resourceGroups") != expected_groups:
        raise ValueError("Infrastructure deployment groups differ from the selected environment")
    output_names = outputs.get("names")
    # 公開処理が使う資源名を現在の設定から導出し、基盤の配備出力と照合する。
    stem = config["resourceStem"]
    names = {"apim": apim_name(config), "keyVault": "kv-" + stem,
        "foundry": "aif-" + stem, "project": "proj-" + stem}
    if not isinstance(output_names, dict) or any(output_names.get(key) != name for key, name in names.items()):
        raise ValueError("Infrastructure service names differ from the selected naming policy")
    service_types = {"apim": "Microsoft.ApiManagement/service", "keyVault": "Microsoft.KeyVault/vaults",
                     "foundry": "Microsoft.CognitiveServices/accounts"}
    resource_ids = {key: _scope(config, "svc") + "/providers/" + kind + "/" + names[key]
                    for key, kind in service_types.items()}
    for key, expected in resource_ids.items():
        _assert_same(state.get("serviceResourceIds", {}).get(key), expected, "Cleanup resource")
        _assert_same(outputs.get("cleanup", {}).get("serviceResourceIds", {}).get(key), expected, "Infrastructure cleanup output")
        _assert_same(outputs.get("services", {}).get(key), expected, "Infrastructure service output")
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
    ingress.require_shared_ingress(config, outputs)
    ticket_on = ingress.owner_ticket_enabled(config)
    features = outputs.get("ingressSecrets", {})
    if (features.get("enabled") is not True or features.get("l7IngressEnabled") is not True or
            (ticket_on and features.get("ownerTicketEnabled", features.get("realtimeEnabled")) is not True)):
        raise ValueError("Ingress/ticket features must be prepared by the foundation deployment")
    if not config.get("apiDefinitions"):
        raise ValueError("API publication requires resolved API definitions")
    authentication = common_authentication_settings(config)
    for api in config["apiDefinitions"]:
        if api.get("apiType") not in PUBLICATION_API_TYPES:
            raise ValueError("API publication supports HTTP, WebSocket and MCP API types")
        if not _same(api.get("subscriptionKeyHeader"), authentication["core-subscription-key-header"]):
            raise ValueError("API key headers must match the foundation gateway.authentication settings")
    # 基盤の初回構築とAPI再公開が、同じ業務設定とTLS/FQDN検査から宛先を生成します。
    resolved, external_hosts = [], generated_apim_service_fqdns(config)
    bases = {"foundryOpenAi": _foundry_openai_origin(names["foundry"]) + "/openai/v1",
             "foundryCognitive": "https://" + names["foundry"] + ".cognitiveservices.azure.com",
             "foundryServices": "https://" + foundry_host}
    def resolve_backend(api):
        if api["backendService"] != "external":
            api["backendUrl"] = bases[api["backendService"]] + api.get("backendPath", "")

    for source in config["apiDefinitions"]:
        api = copy.deepcopy(source)
        resolve_backend(api)
        for operation in api.get("operations", []):
            if "backendOverride" in operation:
                resolve_backend(operation["backendOverride"])
        resolved.append(api)
    resolved = with_catalog_descriptions(config, resolved)
    vault_base = "https://" + names["keyVault"] + ".vault.azure.net/secrets/"
    params = {"names": names,
        "apiDefinitions": resolved, "publishApis": True,
        "subscriptionKeyHeader": authentication["core-subscription-key-header"],
        "credentialLogin": credential_login.settings(config) or {},
        "l7IngressSecretIdentifier": vault_base + ingress.SECRET_NAME,
        "realtimeTicketSecretIdentifier": vault_base + ingress.TICKET_NAME if ticket_on else ""}
    if entra["enabled"]:
        params["entraAuthentication"] = entra
        obo_configured = entra_app_separation_enabled(config) or config.get("gateway", {}).get("authentication", {}).get("entra", {}).get("obo")
        if obo_configured and any(api["name"] == "graph-profile-v1" for api in resolved):
            if "graph-delegated" not in params["credentialLogin"].get("providerNames", ()):
                raise ValueError("Graph OBO also requires the personal Graph Credential Manager route for API keys")
            params["graphOboEnabled"] = True
    policy_id = _scope(config, "nw") + "/providers/Microsoft.Network/firewallPolicies/afwp-" + stem
    result = {"schemaVersion": 1, "subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
        "entraTenantId": entra_tenant_id(config),
        "resourceStem": stem, "resourceGroupNames": expected_groups, "azureRequests": 0,
        "commonAuthentication": authentication,
        "entraAuthentication": entra,
        "entraNamedValues": entra_named_value_settings(config),
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
    saved = state.get("apiPublicationSourceCheckpoint")
    if saved is not None:
        expected = checkpoint_authentication(saved, result)
        if expected is not None:
            result.update(previousEntraAuthentication=expected["entraAuthentication"],
                          previousEntraNamedValues=expected["entraNamedValues"],
                          previousGlobalPolicy=expected["globalPolicy"])
    return result


def _authentication_fragment_source(source):
    """保護された生成元の2部品を明示的に取り出す。重複や欠落は後の完全照合で拒否する。"""
    result = {}
    for entry in source.get("policies", []) if isinstance(source, dict) else []:
        name = entry.get("suffix", "").rsplit("/", 1)[-1]
        if name in AUTHENTICATION_FRAGMENT_IDS and "/policyFragments/" in entry.get("suffix", ""):
            if name in result or not isinstance(entry.get("value"), str):
                raise ValueError("The authentication fragment source is not unique and complete")
            result[name] = entry["value"]
    return result


def _read_authentication_fragments(xml, apim_id, transport):
    """実Globalが呼ぶ認証部品をraw XMLで取得する。IDだけを検証済み本文の代わりにしない。"""
    result = {}
    from deployment.helpers.apim_authentication import _document
    root = _document(xml)
    for node in root.iter("include-fragment"):
        name = node.get("fragment-id")
        if name in AUTHENTICATION_FRAGMENT_IDS:
            if name in result:
                raise ValueError("Authentication fragments must be called once")
            body = require_success(transport.request("GET", apim_id + "/policyFragments/" + name
                + "?api-version=" + APIM_API + "&format=rawxml", headers={"Accept": "application/json"}),
                "Read common authentication fragment")
            result[name] = body.get("properties", {}).get("value")
    return result


def checkpoint_authentication(saved, plan):
    """API生成元と別に保護したglobal/NV期待値を再検査する。旧checkpointは維持する。"""
    if (not isinstance(saved, dict) or saved.get("schemaVersion") != 1
            or not _same(saved.get("apimId"), plan["apimId"])
            or not _same(saved.get("deploymentId"), plan["deploymentId"])):
        raise ValueError("The protected API source checkpoint belongs to a different publication")
    if "sourceGenerationId" in saved:
        validate_source_generation_id(plan, saved["sourceGenerationId"])
    fields = {"globalPolicy", "entraAuthentication", "entraNamedValues"}
    if not fields.intersection(saved):
        return None
    if not fields <= saved.keys() or not isinstance(saved.get("source"), dict):
        raise ValueError("The protected authentication source checkpoint is incomplete")
    entra = saved["entraAuthentication"]
    expected = entra_authentication_named_values(entra)
    observed = saved["entraNamedValues"]
    if expected:
        if validate_entra_named_values(observed) != expected:
            raise ValueError("The protected authentication NamedValue expectations are inconsistent")
    elif observed != {}:
        raise ValueError("The disabled authentication source must not declare Entra NamedValues")
    parsed = read_managed_entra_authentication(saved["globalPolicy"], plan.get("credentialLogin"), expected_entra=entra,
                                              fragments=_authentication_fragment_source(saved["source"]))
    if (parsed or {"enabled": False}) != entra:
        raise ValueError("The protected global authentication source is inconsistent")
    names = {entry.get("name") for entry in saved["source"].get("apis", []) if isinstance(entry, dict)}
    if not set((parsed or {}).get("apiNames", [])) <= names:
        raise ValueError("The protected authentication source authorizes an unrepresented API")
    return {"globalPolicy": saved["globalPolicy"], "entraAuthentication": copy.deepcopy(entra),
            "entraNamedValues": copy.deepcopy(expected)}


def source_checkpoint(plan, source, global_policy, *, entra_authentication=None, browser_source=None, generation_id=None):
    """API再配備を要せず、独立した共通policy/NV期待値を保護checkpointへ保存できる形にする。"""
    entra = plan.get("entraAuthentication") if entra_authentication is None else entra_authentication
    expected = entra_authentication_named_values(entra)
    fragments = _authentication_fragment_source(source)
    parsed = read_managed_entra_authentication(global_policy, plan.get("credentialLogin"), expected_entra=entra, fragments=fragments)
    validate_common_authentication_policy(global_policy, plan.get("credentialLogin"), entra=entra, fragments=fragments)
    if (parsed or {"enabled": False}) != entra:
        raise ValueError("The generated global authentication source is inconsistent")
    names = [entry.get("name") for entry in source.get("apis", []) if isinstance(entry, dict)] if isinstance(source, dict) else []
    if not set((parsed or {}).get("apiNames", [])) <= set(names):
        raise ValueError("The generated authentication source authorizes an unrepresented API")
    named = {plan["apimId"] + "/namedValues/" + name: {"displayName": name, "secret": False, "value": value}
             for name, value in expected.items()}
    entra_publication.source_manifest(source, plan["apimId"], names, global_policy, named_values=named)
    result = {"schemaVersion": 1, "apimId": plan["apimId"], "deploymentId": plan["deploymentId"],
              "source": copy.deepcopy(source), "globalPolicy": global_policy,
              "entraAuthentication": copy.deepcopy(entra), "entraNamedValues": expected}
    if browser_source is not None:
        result["browserSource"] = copy.deepcopy(browser_source)
    if generation_id is not None:
        result["sourceGenerationId"] = validate_source_generation_id(plan, generation_id)
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
    """APIMのHTTPS宛先を現在の規則へ追加し、他のFirewall規則を保持する。
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
    selected_rules = [rule for rule in rules if rule.get("name") == APIM_EGRESS_RULE_NAME]
    if len(selected_rules) > 1:
        raise ValueError("Firewall APIM FQDN rule is duplicated")
    hosts = plan["firewall"]["externalFqdns"]
    if not selected_rules and not hosts:
        return {"properties": properties}, False, etag
    if selected_rules:
        rule = selected_rules[0]
        validate_apim_egress_rule(rule, plan["firewall"]["sourcePrefix"])
        old = rule["targetFqdns"]
        missing = [host for host in hosts if host.lower() not in {entry.lower() for entry in old}]
        rule["targetFqdns"] += missing
        changed = bool(missing)
    else:
        rules.append({"name": APIM_EGRESS_RULE_NAME, "ruleType": "ApplicationRule",
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


def _publication_parameter_values(parameters):
    """ARMの読戻しで付くtype情報を除き、指定した入力値そのものを比較できる形にする。"""
    if (not isinstance(parameters, dict) or not parameters or any(
            not isinstance(item, dict) or "value" not in item or set(item) - {"value", "type"}
            for item in parameters.values())):
        raise ValueError("Publication source generation parameters must contain the reviewed literal inputs")
    return {name: item["value"] for name, item in parameters.items()}



def _checked_source_job(body, config, plan, generation_id, parameters, *, require_complete=True):
    """生成専用の記録を照合する。別環境・別入力・資源変更を伴う記録は採用しない。"""
    if not isinstance(body, dict):
        raise ValueError("Publication source generation record must be an object")
    validate_source_generation_id(plan, generation_id)
    _assert_same(body.get("id"), generation_id, "Publication source generation")
    _assert_same(body.get("type"), "Microsoft.Resources/deployments", "Publication source generation type")
    tags, properties = body.get("tags"), body.get("properties")
    if (not isinstance(tags, dict) or tags.get("azd-env-name") != config["environmentName"]
            or tags.get("azd-project-name") != PROJECT_NAME or not isinstance(properties, dict)
            or properties.get("mode") != "Incremental"
            or _publication_parameter_values(properties.get("parameters")) != _publication_parameter_values(parameters)):
        raise ValueError("Publication source generation does not match its owner and inputs")
    if properties.get("outputResources") not in (None, []):
        raise ValueError("Publication source generation must not deploy any resources")
    if not require_complete:
        return
    if properties.get("provisioningState") != "Succeeded":
        raise ValueError("Publication source generation has not succeeded")
    outputs = properties.get("outputs")
    binding = outputs.get(SOURCE_GENERATION_OUTPUT) if isinstance(outputs, dict) else None
    if not isinstance(binding, dict) or binding.get("value") != generation_id:
        raise ValueError("Publication source generation binding is missing or inconsistent")
    source_entry, browser_entry = outputs.get("entraPublicationSource"), outputs.get("credentialBrowserPublicationSource")
    source = source_entry.get("value") if isinstance(source_entry, dict) else None
    browser = browser_entry.get("value") if isinstance(browser_entry, dict) else None
    if not isinstance(source, dict) or not isinstance(browser, dict):
        raise ValueError("Publication source generation outputs are incomplete")
    return source, browser


def _export_publication_template(transport, resource_id):
    """登録された配備テンプレートを読み出す。資源の作成・更新は実行しない。"""
    response = transport.request("POST", resource_id + "/exportTemplate?api-version=" + DEPLOYMENT_API)
    template = require_success(response, "Export the recorded publication template").get("template")
    if not isinstance(template, dict):
        raise ValueError("The recorded publication template could not be exported")
    return canonical_publication_template(template)


def _deployment_signature(body):
    """テンプレート読取りの途中に記録が上書きされていないか、入力・結果を照合する。"""
    properties = body.get("properties", {})
    return ({name: body.get(name) for name in ("id", "name", "type", "tags")},
            {name: properties.get(name) for name in
             ("provisioningState", "mode", "templateHash", "correlationId", "parameters", "outputs")})


def _verify_generated_template(transport, generation_id, observed, expected_template, identity_principal_id,
                               *, recorded_template=None):
    # 世代IDは入力のハッシュで作りますが、Azure上の同名記録自体は変更できます。
    # 名前やタグだけを証明にせず、保存されたコードを完全一致で確認します。
    actual = _export_publication_template(transport, generation_id) if recorded_template is None else recorded_template
    if actual != canonical_publication_template(expected_template):
        raise ValueError("Publication source generation template differs from its reviewed code")
    entry = observed.get("properties", {}).get("outputs", {}).get(SOURCE_PRINCIPAL_OUTPUT)
    if (not isinstance(entry, dict) or not isinstance(entry.get("value"), str)
            or entry["value"].casefold() != (identity_principal_id or "").casefold()):
        raise ValueError("API Management identity changed while publication source was being generated")
    latest = require_success(transport.request("GET", generation_id + "?api-version=" + DEPLOYMENT_API),
                             "Confirm unchanged publication source generation")
    if _deployment_signature(latest) != _deployment_signature(observed):
        raise ValueError("Publication source generation changed while its template was being verified")


def generate_publication_source(config, plan, template, transport, *, identity_principal_id, sleep=time.sleep,
                                timeout_seconds=1800):
    """同じBicep式を資源変更のない別要求で評価し、完成XMLの期待値を得る。

    実配備の記録は上書きしない。コード・入力・既存APIMの本人情報から決めたIDを使い、
    成功済みの同じ生成は再利用する。生成結果だけでは適用済みと判定しない。
    """
    validate_publication_template(template)
    publication_template, source_template = split_publication_templates(template)
    if source_template is None:
        return publication_template, None, None, None
    generation_id = source_generation_id(plan, template, identity_principal_id=identity_principal_id)
    generation_parameters = publication_parameters(template, plan["parameters"])
    for value in (publication_template, source_template):
        value.setdefault("outputs", {})[SOURCE_GENERATION_OUTPUT] = {"type": "string", "value": generation_id}
    # Bicepの生成式が実際に参照したMIを記録し、事前確認後の再作成も検出します。
    source_template["outputs"][SOURCE_PRINCIPAL_OUTPUT] = {"type": "string", "value": SOURCE_PRINCIPAL_EXPRESSION}
    path = generation_id + "?api-version=" + DEPLOYMENT_API
    response = transport.request("GET", path)
    deadline = time.monotonic() + timeout_seconds
    if response.get("statusCode") != 404:
        observed = require_success(response, "Read publication source generation")
        _checked_source_job(observed, config, plan, generation_id, generation_parameters, require_complete=False)
        status = observed["properties"].get("provisioningState")
        if status not in ("Succeeded", "Failed", "Canceled", "Cancelled", "Accepted", "Running", "Creating", "Updating"):
            raise ValueError("Publication source generation has an unsupported state")
        if status in ("Accepted", "Running", "Creating", "Updating"):
            observed = _wait_resource(transport, generation_id, DEPLOYMENT_API, sleep=sleep, deadline=deadline)
        if observed["properties"].get("provisioningState") == "Succeeded":
            source, browser = _checked_source_job(observed, config, plan, generation_id, generation_parameters)
            _verify_generated_template(transport, generation_id, observed, source_template, identity_principal_id)
            return publication_template, source, browser, generation_id
    deployment = {"tags": {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME},
                  "properties": {"mode": "Incremental", "template": source_template, "parameters": generation_parameters}}
    response = transport.request("PUT", path, deployment)
    require_success(response, "Generate publication source without changing API resources", allowed=(200, 201, 202))
    observed = _wait_resource(transport, generation_id, DEPLOYMENT_API, sleep=sleep, deadline=deadline)
    source, browser = _checked_source_job(observed, config, plan, generation_id, generation_parameters)
    _verify_generated_template(transport, generation_id, observed, source_template, identity_principal_id)
    return publication_template, source, browser, generation_id


def _prior_publication_source(prior_response, config, plan, transport):
    """適用成功の記録に結び付いた生成元だけを読む。最新の生成記録を探索しない。"""
    properties = prior_response.get("body", {}).get("properties", {})
    outputs = properties.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ValueError("Previous publication outputs are invalid")
    binding = outputs.get(SOURCE_GENERATION_OUTPUT)
    if binding is None:
        return None
    if properties.get("provisioningState") != "Succeeded" or not isinstance(binding, dict):
        raise ValueError("Only a successful publication may supply its source generation binding")
    source, _, _, _ = _read_bound_publication_source(prior_response["body"], binding.get("value"), config, plan, transport)
    return source


def _read_bound_publication_source(publication, generation_id, config, plan, transport):
    generation_id = validate_source_generation_id(plan, generation_id)
    properties = publication["properties"]
    parameters = properties.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("Previous publication inputs are required for its source generation binding")
    response = transport.request("GET", generation_id + "?api-version=" + DEPLOYMENT_API)
    observed = require_success(response, "Read source generation bound to the successful publication")
    source, browser = _checked_source_job(observed, config, plan, generation_id, parameters)
    source_template = _export_publication_template(transport, generation_id)
    publication_template = _export_publication_template(transport, plan["deploymentId"])
    principal_entry = source_template.get("outputs", {}).get(SOURCE_PRINCIPAL_OUTPUT)
    if principal_entry != {"type": "string", "value": SOURCE_PRINCIPAL_EXPRESSION}:
        raise ValueError("Recorded publication source identity binding is missing")
    principal_result = observed.get("properties", {}).get("outputs", {}).get(SOURCE_PRINCIPAL_OUTPUT)
    principal = principal_result.get("value") if isinstance(principal_result, dict) else None
    if not isinstance(principal, str):
        raise ValueError("Recorded publication source identity output is missing")
    # 二つの保存テンプレートから分割前のコードを復元し、世代IDとの一致を調べます。
    # 「最新の生成に見える」だけの記録や、別コードで上書きされた同名記録は採用しません。
    combined = copy.deepcopy(publication_template)
    combined["outputs"] = {name: value for name, value in combined.get("outputs", {}).items()
                           if name != SOURCE_GENERATION_OUTPUT}
    expected_source_outputs = {name: value for name, value in source_template.get("outputs", {}).items()
                               if name not in (SOURCE_GENERATION_OUTPUT, SOURCE_PRINCIPAL_OUTPUT)}
    if set(expected_source_outputs) != {"entraPublicationSource", "credentialBrowserPublicationSource"}:
        raise ValueError("Recorded publication source template contains unexpected outputs")
    combined["outputs"].update(expected_source_outputs)
    validate_publication_template(combined)
    expected_publication, expected_source = split_publication_templates(combined)
    for value in (expected_publication, expected_source):
        value["outputs"][SOURCE_GENERATION_OUTPUT] = {"type": "string", "value": generation_id}
    expected_source["outputs"][SOURCE_PRINCIPAL_OUTPUT] = principal_entry
    recorded_plan = {**plan, "parameters": parameters}
    # ARMの読戻しにだけ付くparameter typeは、元の送信形式へ戻してからIDを再計算します。
    recorded_plan["parameters"] = {name: {"value": value} for name, value in _publication_parameter_values(parameters).items()}
    if (source_generation_id(recorded_plan, combined, identity_principal_id=principal or None) != generation_id
            or canonical_publication_template(expected_publication) != publication_template
            or canonical_publication_template(expected_source) != source_template):
        raise ValueError("Successful publication and its source generation do not share the same recorded code")
    _verify_generated_template(transport, generation_id, observed, expected_source, principal,
                               recorded_template=source_template)
    _confirm_publication_record(plan["deploymentId"], publication, transport)
    return source, browser, observed, combined


def _confirm_publication_record(resource_id, expected, transport):
    observed = require_success(transport.request("GET", resource_id + "?api-version=" + DEPLOYMENT_API),
                               "Confirm unchanged API publication record")
    if _deployment_signature(observed) != _deployment_signature(expected):
        raise ValueError("API publication changed while its source generation was being verified")


def _checkpoint_hash(checkpoint):
    return hashlib.sha256(json.dumps(checkpoint, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _api_source_hash(source):
    """Compare complete source despite the array order used by incremental merges."""
    normalized = dict(source)
    for section, identity in (("apis", "name"), ("policies", "suffix"), ("backends", "suffix"),
                              ("namedValues", "suffix"), ("apiChildren", "suffix")):
        entries = source.get(section)
        if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get(identity), str) for item in entries):
            raise ValueError("The current publication source sections must be complete")
        normalized[section] = sorted(entries, key=lambda item: item[identity])
    return _checkpoint_hash(normalized)


def _read_closed_stage_attempt(attempt, prior_response, saved, config, plan, apim, transport):
    """Verify a failed current ARM stage against its protected local write intent."""
    fields = {"schemaVersion", "checkpointSha256", "sourceGenerationId", "previousCorrelationId", "nativeProducts"}
    if (not isinstance(attempt, dict) or set(attempt) != fields or attempt["schemaVersion"] != 1
            or not isinstance(saved, dict) or attempt["checkpointSha256"] != _checkpoint_hash(saved)
            or not isinstance(attempt["nativeProducts"], dict)):
        raise ValueError("Resuming needs the unchanged Gateway checkpoint and its protected closed stage record")
    if prior_response.get("statusCode") != 200:
        raise ValueError("The recorded failed API publication is missing")
    prior = prior_response.get("body", {})
    properties = prior.get("properties", {})
    correlation = properties.get("correlationId")
    if (properties.get("provisioningState") != "Failed" or not isinstance(correlation, str)
            or not correlation or correlation == attempt["previousCorrelationId"]):
        raise ValueError("Resume requires a new terminal Failed publication matching the recorded closed stage")
    _prior_published_ids(prior, config, plan)
    generation_id = validate_source_generation_id(plan, attempt["sourceGenerationId"])
    source, browser, observed, template = _read_bound_publication_source(prior, generation_id, config, plan, transport)
    parameters = publication_parameters(template, plan["parameters"])
    expected_parameter_hash = _checkpoint_hash(_publication_parameter_values(parameters))
    if any(_checkpoint_hash(_publication_parameter_values(record["properties"]["parameters"])) != expected_parameter_hash
           for record in (prior, observed)):
        raise ValueError("Resume requires the same publication inputs used for the failed stage")
    principal = apim.get("identity", {}).get("principalId")
    if not isinstance(principal, str) or not _same(observed["properties"]["outputs"][SOURCE_PRINCIPAL_OUTPUT]["value"], principal):
        raise ValueError("The APIM identity differs from the recorded closed stage")
    previous_id = validate_source_generation_id(plan, saved.get("sourceGenerationId"))
    previous_path = previous_id + "?api-version=" + DEPLOYMENT_API
    previous_response = transport.request("GET", previous_path)
    previous = require_success(previous_response, "Read the source bound to the prior Gateway checkpoint")
    previous_parameters = previous.get("properties", {}).get("parameters")
    previous_source, previous_browser = _checked_source_job(previous, config, plan, previous_id, previous_parameters)
    if (_api_source_hash(previous_source) != _api_source_hash(saved["source"])
            or _checkpoint_hash(previous_browser) != _checkpoint_hash(saved.get("browserSource"))
            or not _same(previous["properties"]["outputs"].get(SOURCE_PRINCIPAL_OUTPUT, {}).get("value"), principal)):
        raise ValueError("The prior Gateway checkpoint differs from its owned source generation")
    snapshot = read_failed_stage_operations(prior, template, source, browser, plan, transport)
    snapshot[previous_id] = (previous_path, None, previous_response)
    return source, browser, generation_id, observed, snapshot


def read_failed_stage_operations(prior, template, source, browser, plan, transport):
    """Require complete terminal operations within this recorded template's targets."""
    apim, deployment_id = plan["apimId"], plan["deploymentId"]
    prefix = "Microsoft.ApiManagement/service"
    resources = template["resources"]
    declared = {entry["type"].casefold() for entry in
                (resources.values() if isinstance(resources, dict) else resources)}
    targets = {}

    def add(resource_id, kind):
        if (prefix + kind).casefold() in declared:
            targets[resource_id.casefold()] = (prefix + kind).casefold()

    for entry in source["apis"]:
        api_id = apim + "/apis/" + entra_publication._identifier(entry["name"])
        add(api_id, "/apis")
        add(api_id + "/diagnostics/azuremonitor", "/apis/diagnostics")
        for name in entry["operations"]:
            add(api_id + "/operations/" + entra_publication._identifier(name), "/apis/operations")
        for product in entry["products"]:
            product_id = apim + "/products/" + entra_publication._identifier(product)
            add(product_id, "/products")
            add(product_id + "/apis/" + entry["name"], "/products/apis")
    for key, kind in (("backends", "/backends"), ("namedValues", "/namedValues")):
        for entry in source[key]:
            add(apim + entry["suffix"], kind)
    for entry in source["policies"]:
        suffix = entry["suffix"]
        kind = "/policyFragments" if suffix.startswith("/policyFragments/") else (
            "/apis/operations/policies" if "/operations/" in suffix else "/apis/policies")
        add(apim + suffix, kind)
    for entry in source["apiChildren"]:
        add(apim + entry["suffix"], "/apis/" + entry["suffix"].split("/")[3])
    if plan.get("credentialLogin"):
        api_id = apim + "/apis/" + browser["name"]
        add(api_id, "/apis")
        add(api_id + "/diagnostics/azuremonitor", "/apis/diagnostics")
        for entry in browser["operations"]:
            add(api_id + "/operations/" + entry["name"], "/apis/operations")
        for entry in browser["policies"]:
            add(apim + entry["suffix"], "/apis/operations/policies")
    add(apim + "/products/" + RELEASED_PRODUCT_ID, "/products")
    add(apim + "/products/" + ENTRA_OPEN_PRODUCT_ID, "/products")
    add(apim + "/products/" + ENTRA_OPEN_PRODUCT_ID + "/policies/policy", "/products/policies")
    add(apim + "/loggers/azuremonitor", "/loggers")
    add(apim + "/diagnostics/azuremonitor", "/diagnostics")
    collection = deployment_id + "/operations"
    path, visited, snapshot, failed, operation_ids = collection + "?api-version=" + DEPLOYMENT_API, set(), {}, False, set()
    while path is not None:
        if path in visited:
            raise ValueError("Failed-stage operation pagination repeated a page")
        visited.add(path)
        response = transport.request("GET", path)
        body = require_success(response, "Read complete recorded failed-stage operations")
        if not isinstance(body, dict) or not isinstance(body.get("value"), list):
            raise ValueError("Failed-stage operations inventory is incomplete")
        snapshot[path] = (path, None, copy.deepcopy(response))
        for operation in body["value"]:
            operation_id = operation.get("id") if isinstance(operation, dict) else None
            properties = operation.get("properties") if isinstance(operation, dict) else None
            if (not isinstance(operation_id, str) or not operation_id.casefold().startswith(collection.casefold() + "/")
                    or not re.fullmatch(r"[A-Za-z0-9-]+", operation_id[len(collection) + 1:])
                    or operation_id.casefold() in operation_ids or not isinstance(properties, dict)
                    or properties.get("provisioningState") not in ("Succeeded", "Failed")):
                raise ValueError("Failed-stage operations must have unique owned IDs and terminal states")
            operation_ids.add(operation_id.casefold())
            target = properties.get("targetResource")
            action = properties.get("provisioningOperation")
            if target is None:
                if action != "EvaluateDeploymentOutput" or properties["provisioningState"] != "Succeeded":
                    raise ValueError("Failed-stage non-resource operation is unknown or incomplete")
                continue
            if not isinstance(target, dict):
                raise ValueError("Failed-stage operation target is incomplete")
            resource_id, kind = target.get("id"), target.get("resourceType")
            if (isinstance(resource_id, str) and resource_id.casefold() == apim.casefold()
                    and isinstance(kind, str) and kind.casefold() == prefix.casefold() and action == "Read"
                    and properties["provisioningState"] == "Succeeded"):
                continue
            if (not isinstance(resource_id, str) or not isinstance(kind, str)
                    or targets.get(resource_id.casefold()) != kind.casefold() or action != "Create"):
                raise ValueError("Failed-stage operation targets a resource outside the recorded API stage")
            failed |= properties["provisioningState"] == "Failed"
        next_link = body.get("nextLink")
        if next_link is None:
            path = None
        else:
            parsed = urlsplit(next_link) if isinstance(next_link, str) else None
            if (parsed is None or parsed.scheme not in ("", "https") or parsed.netloc not in ("", "management.azure.com")
                    or parsed.fragment or parsed.path.casefold() != collection.casefold()
                    or parse_qs(parsed.query).get("api-version") != [DEPLOYMENT_API]):
                raise ValueError("Failed-stage operation pagination left the recorded deployment")
            path = next_link
    if not operation_ids or not failed or prior["properties"].get("provisioningState") != "Failed":
        raise ValueError("The recorded failed stage must contain a completed failed resource operation")
    return snapshot


def validate_publication_template(template):
    """配備できる型をAPIMの子定義だけに限定し、ARMの配列・シンボル形式を検査する。

    BicepのlanguageVersion 2.0ではresourcesが名前付きobjectになり、既存APIMの
    参照もexisting:trueとして残る。この正確な読取参照だけを例外とし、APIM本体を
    更新する宣言や別の資源型を許可しない。空の計画や不正な要素も変更前に拒否する。
    """
    message = "Only the API publication module may be deployed"
    if not isinstance(template, dict):
        raise ValueError(message)
    # Count members across namespaces. A compiler success alone does not
    # establish that Azure can accept the template's user-defined functions.
    functions = template.get("functions", [])
    if (not isinstance(functions, list) or any(
            not isinstance(group, dict) or not isinstance(group.get("members"), dict)
            for group in functions)):
        raise ValueError("API publication requires valid ARM function groups")
    if sum(len(group["members"]) for group in functions) > ARM_TEMPLATE_FUNCTION_LIMIT:
        raise ValueError("API publication exceeds the ARM template function limit")
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
        if resource_type == "microsoft.apimanagement/service/apis" and isinstance(resource.get("properties"), dict):
            api_type = resource["properties"].get("type")
            # パラメーターから決まるAPI型は、配備プランの入力で検査する。
            if (isinstance(api_type, str) and api_type not in PUBLICATION_API_TYPES
                    and not (api_type.startswith("[") and api_type.endswith("]"))):
                raise ValueError("API publication template requires a supported API type")
        if resource_type == "microsoft.apimanagement/service/policies":
            raise ValueError("API publication must preserve the APIM global policy")
        if (resource_type == "microsoft.apimanagement/service/namedvalues"
                and resource.get("existing") is not True
                and any(name in json.dumps(resource).lower() for name in (*COMMON_NAMED_VALUES, *ENTRA_NAMED_VALUES))):
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
    products = {}
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
            observed = product.get("id") if isinstance(product, dict) else None
            if not isinstance(observed, str):
                raise ValueError("API Product associations must have canonical identities")
            name = observed.rsplit("/", 1)[-1]
            if not API_NAME.fullmatch(name):
                raise ValueError("API Product association identifier is invalid")
            canonical = plan["apimId"] + "/products/" + name
            if not any(_same(observed, alias) for alias in (canonical, api_id + "/products/" + name)):
                raise ValueError("API Product association belongs to a different API Management scope")
            if canonical not in products:
                actual = require_success(transport.request("GET", canonical + "?api-version=" + APIM_API),
                                         "Read canonical API Product authorization")
                _assert_same(actual.get("id"), canonical, "API Product")
                products[canonical] = actual.get("properties", {})
            properties = products[canonical]
            if name == ENTRA_OPEN_PRODUCT_ID:
                # Its exact ownership/policy/full memberships are checked by the
                # Entra transition preflight even when currently unlinked.
                if not entra_publication._owned_open_product_properties(properties):
                    raise ValueError("The dedicated employee Product has conflicting ownership/properties")
                continue
            if properties.get("subscriptionRequired") is not True:
                raise ValueError("Remove the reviewed API association with subscription-free Products before key authorization publication; no changes were attempted")


def verify_backend_secret_access(config, definitions, vault_id, apim, transport):
    """構築済み資格情報Vaultを参照する明示バックエンドの個別読取権限を確認する。"""
    core_host = vault_id.rsplit("/", 1)[-1].lower() + ".vault.azure.net"
    core_names = set()
    for api in definitions:
        backends = [api, *(operation["backendOverride"] for operation in api.get("operations", []) if "backendOverride" in operation)]
        for backend in backends:
            authentication = backend.get("backendAuthentication", {})
            if authentication.get("mode") == "keyVaultHeader":
                identifier = authentication.get("secretIdentifier")
                if not isinstance(identifier, str):
                    raise ValueError("A Key Vault backend requires a registered secret reference")
                uri = urlsplit(identifier)
                object_path = re.fullmatch(r"/secrets/([A-Za-z0-9-]+)(?:/[A-Za-z0-9]+)?", uri.path)
                if uri.scheme != "https" or not uri.hostname or object_path is None:
                    raise ValueError("A Key Vault backend requires a registered secret reference")
                if uri.hostname == core_host:
                    core_names.add(object_path.group(1).lower())
    if not core_names:
        return
    principal = apim.get("identity", {}).get("principalId")
    try:
        uuid.UUID(principal)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("API Management system identity is not ready") from None
    role_id = ("/subscriptions/" + config["subscriptionId"]
               + "/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6")
    for name in sorted(core_names):
        scope = vault_id + "/secrets/" + name
        assignments = require_success(transport.request("GET", scope
            + "/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&$filter=atScope()"),
            "Read scoped APIM backend secret access")
        if (assignments.get("nextLink") or not isinstance(assignments.get("value"), list)
                or not any(_same(properties.get("principalId"), principal)
                           and _same(properties.get("scope"), scope)
                           and _same(properties.get("roleDefinitionId"), role_id)
                           for item in assignments["value"] if isinstance(item, dict)
                           for properties in (item.get("properties") or {},) if isinstance(properties, dict))):
            raise ValueError("Grant the APIM system identity Key Vault Secrets User at each configured backend secret before API publication")


def verify_common_authentication(plan, transport):
    """基盤の名前付きの値を照合し、共通ポリシーの変更計画を読み取りだけで作る。"""
    path = plan["apimId"] + "/policies/policy?api-version=" + APIM_API
    response = transport.request("GET", path + "&format=rawxml", headers={"Accept": "application/json"})
    missing = response.get("statusCode") == 404
    policy = None if missing else require_success(response, "Read APIM global policy")
    properties = policy.get("properties", {}) if isinstance(policy, dict) else {}
    current_xml = properties.get("value") if isinstance(properties, dict) else None
    if not missing and not isinstance(current_xml, str):
        raise ValueError(MIGRATION_REQUIRED)
    if plan.get("previousGlobalPolicy") is not None:
        entra_publication._same_xml(current_xml, plan["previousGlobalPolicy"])
    prior_entra = None
    fragments = _read_authentication_fragments(current_xml, plan["apimId"], transport) if current_xml is not None else {}
    closed_initial = (not missing and plan.get("entraAuthentication", {}).get("enabled")
                      and is_closed_initial_policy(current_xml))
    if missing or closed_initial:
        merged, changed = merge_common_authentication_policy(None, plan.get("credentialLogin"),
                                                            entra=plan.get("entraAuthentication"))
    else:
        prior_entra = read_managed_entra_authentication(current_xml, plan.get("credentialLogin"),
                                                       expected_entra=plan.get("previousEntraAuthentication"), fragments=fragments)
        merged, changed = transition_common_authentication_policy(current_xml, plan.get("credentialLogin"),
                                                                  previous_entra=prior_entra, entra=plan.get("entraAuthentication"),
                                                                  previous_fragments=fragments)
    headers = {"If-None-Match": "*"} if missing else ({"If-Match": _etag(response)} if changed else {})
    for name, expected in plan["commonAuthentication"].items():
        response = transport.request("GET", plan["apimId"] + "/namedValues/" + name + "?api-version=" + APIM_API)
        if response.get("statusCode") == 404:
            raise ValueError(MIGRATION_REQUIRED)
        named = require_success(response, "Read foundation common authentication NamedValue")
        validate_common_named_value(named, name, expected)
    # Settings deployment owns writes. The prior logical source verifies the
    # old global structure; the explicitly prepared configuration owns these
    # current values, including an intentional settings update before staging.
    entra_values = plan.get("entraNamedValues") or plan.get("previousEntraNamedValues", {})
    for name, expected in entra_values.items():
        response = transport.request("GET", plan["apimId"] + "/namedValues/" + name + "?api-version=" + APIM_API)
        if response.get("statusCode") == 404:
            raise ValueError(MIGRATION_REQUIRED)
        named = require_success(response, "Read foundation Entra authentication NamedValue")
        validate_entra_named_value(named, name, expected)
    return {"path": path, "value": merged, "changed": changed, "headers": headers,
            "previousEntra": prior_entra, "previousValue": current_xml,
            "previousFragments": copy.deepcopy(fragments),
            "credentialLogin": plan.get("credentialLogin"), "entraAuthentication": plan.get("entraAuthentication"),
            "entraNamedValues": copy.deepcopy(entra_values)}


def _has_gateway_caller_probe(containers):
    """対応版BrokerのReadiness probeがSQL能力確認を要求しているか調べる。"""
    if not isinstance(containers, list) or len(containers) != 1:
        return False
    container = containers[0]
    if not isinstance(container, dict):
        return False
    probes = container.get("probes", [])
    if not isinstance(probes, list):
        return False
    readiness = [probe for probe in probes
                 if isinstance(probe, dict)
                 and probe.get("type") == "Readiness"]
    if len(readiness) != 1:
        return False
    http = readiness[0].get("httpGet")
    return (isinstance(http, dict)
            and http.get("path") == "/health/gateway-caller-v1"
            and http.get("port") == 8000
            and http.get("scheme") == "HTTP")


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
    app_properties = app.get("properties", {})
    host = broker_https_ingress(app_properties, broker_environment_id(config)).get("fqdn")
    if (not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", host)
            or not host.startswith(names["application"] + ".")):
        raise ValueError("Credential broker internal application address is invalid")
    containers = app_properties.get("template", {}).get("containers", [])
    revision_name = app_properties.get("latestRevisionName")
    if (app_properties.get("provisioningState") != "Succeeded"
            or not isinstance(revision_name, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", revision_name)
            or app_properties.get("latestReadyRevisionName") != revision_name
            or not _has_gateway_caller_probe(containers)
            or not re.fullmatch(r"[a-z0-9]+\.azurecr\.io/credential-broker@sha256:[a-f0-9]{64}", containers[0].get("image", ""))
            or not any(_same(identity_id, broker_identity_id)
                       for identity_id in app.get("identity", {}).get("userAssignedIdentities", {}))):
        raise ValueError("Deploy the credential broker image and database schema successfully before publishing its APIs")
    revision_id = app_id + "/revisions/" + revision_name
    revision = require_success(transport.request(
        "GET", revision_id + "?api-version=2025-01-01"
    ), "Read credential broker caller capability readiness")
    _assert_same(revision.get("id"), revision_id,
                 "Credential broker ready revision")
    ready = revision.get("properties", {})
    ready_containers = ready.get("template", {}).get("containers", [])
    if (ready.get("active") is not True
            or ready.get("healthState") != "Healthy"
            or ready.get("provisioningState") != "Provisioned"
            or ready.get("runningState") != "Running"
            or not _has_gateway_caller_probe(ready_containers)
            or ready_containers[0].get("image") != containers[0]["image"]):
        raise ValueError(
            "Credential broker caller capability is not healthy; "
            "complete the matching SQL and broker deployment before "
            "publishing APIs"
        )
    if plan.get("parameters", {}).get("graphOboEnabled", {}).get("value") is True:
        from deployment.helpers.gateway_obo_certificate import deployment_settings, runtime_environment
        from deployment.helpers.paths import local_paths

        obo_environment = runtime_environment(deployment_settings(config, local_paths(config, ROOT)))
        if not obo_environment:
            raise ValueError("Prepare the Graph OBO signing certificate before publishing the Graph API")
        for container in (containers[0], ready_containers[0]):
            rows = [row for row in container.get("env", []) if row.get("name", "").startswith("BROKER_OBO_")]
            if len(rows) != len(obo_environment) or any(row.get("secretRef") for row in rows) or {
                    row["name"]: row.get("value") for row in rows} != obo_environment:
                raise ValueError("Deploy the exact Graph OBO certificate binding to the ready Broker revision before publishing")
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
            validate_common_authentication_policy(properties.get("value"), prepared.get("credentialLogin"),
                                                  entra=prepared.get("entraAuthentication"),
                                                  fragments=_read_authentication_fragments(properties.get("value"), prepared["path"].split("/policies/policy", 1)[0], transport))
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
        raise ValueError("Audit diagnostic categories are invalid")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled"), bool):
            raise ValueError("Audit diagnostic categories are invalid")
        keys = [key for key in ("category", "categoryGroup") if entry.get(key)]
        if len(keys) != 1 or not isinstance(entry[keys[0]], str):
            raise ValueError("Audit diagnostic categories are invalid")
        key = (keys[0], entry[keys[0]].casefold())
        if key in result:
            raise ValueError("Audit diagnostic categories are duplicated")
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
                           "Read existing audit diagnostics")
    _assert_same(body.get("id"), resource_id, "Audit diagnostic setting")
    properties = body.get("properties")
    # このAPIの書込契約にある項目をすべて保持する。未知の項目を黙って捨てない。
    # https://learn.microsoft.com/rest/api/monitor/diagnostic-settings/create-or-update
    writable = {"workspaceId", "storageAccountId", "eventHubAuthorizationRuleId", "eventHubName",
                "serviceBusRuleId", "marketplacePartnerId", "logAnalyticsDestinationType", "logs", "metrics"}
    if not isinstance(properties, dict) or set(properties) - writable - {"provisioningState"}:
        raise ValueError("Audit diagnostic properties require review")
    outputs = state.get("outputs", {})
    workspace_id = outputs.get("monitoring", {}).get("workspaceId")
    storage_id = outputs.get("auditStorage", {}).get("collectPlatform", {}).get("id")
    if not workspace_id or not storage_id:
        raise ValueError("Audit destinations are missing from the deployment state")
    _assert_same(properties.get("workspaceId"), workspace_id, "Audit workspace")
    _assert_same(properties.get("storageAccountId"), storage_id, "Audit storage")
    if not isinstance(properties.get("logs"), list):
        raise ValueError("Audit diagnostic categories are invalid")
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
    raise DeploymentError("WebSocket diagnostic update could not be confirmed; check the audit settings before retrying")


def execute_plan(config, state, plan, template, transport, *, sleep=time.sleep, timeout_seconds=7200, checkpoint=None,
                 stage_record=None, record_stage=None):
    """全所有資源と事前条件を読み合わせてから、FW宛先追加とAPI公開を実行する。

    既存モデルの作成・変更はここでは行わず、必要なRBACや秘密参照も準備済みか検査する。
    以前の配備は所有者とAPIの所属先を照合し、指定したAPIだけを増分更新する。
    FWはETag付き更新後に読み直し、確認できてからAPIMの子定義だけを配備する。
    閉鎖・配備・再公開は最大2時間の有限予算を共有し、確認が完了した時点で終了する。
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
    verify_subscription_products(plan, transport)
    authentication_policy = verify_common_authentication(plan, transport)
    verify_credential_login(config, plan, apim, transport)
    verify_websocket_diagnostics(plan, state, transport)
    identity = require_success(transport.request("GET", plan["identityId"] + "?api-version=2023-01-31"), "Read existing certificate identity")
    _owned(identity, plan["identityId"], config, "Certificate identity")
    if identity.get("properties", {}).get("clientId") != state["outputs"]["certificateIdentity"]["clientId"]:
        raise ValueError("The certificate identity was recreated; reconcile its infrastructure bindings first")
    fw = plan["firewall"]
    policy = require_success(transport.request("GET", fw["policyId"] + "?api-version=" + NETWORK_API), "Read owned Firewall policy")
    _owned(policy, fw["policyId"], config, "Firewall policy")
    subnet_id = subnet_resource_id(config, "apim")
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
            raise ValueError("API Management system identity is not ready") from None
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
        # Capacity is operated independently after initial provisioning (for
        # example in Foundry's GUI). API-only publication never deploys models
        # and must preserve that allocation while verifying the model contract.
        if (properties.get("provisioningState") != "Succeeded" or not _contains(properties.get("model"), expected_model)
            or actual.get("sku", {}).get("name") != model["skuName"]):
            raise ValueError("API-only publication cannot create or change model deployments; reconcile the model contract first")
    prior_response = transport.request("GET", plan["deploymentId"] + "?api-version=" + DEPLOYMENT_API)
    # 前回配備の所有者・状態だけを照合し、全 API 一覧の再提出は要求しない。
    # Incremental 配備は指定しない API を削除せず、全体 policy も変更しない。
    # 基盤構築は API を登録しないため、初回の404には過去配備がない。
    if prior_response.get("statusCode") != 404:
        prior = require_success(prior_response, "Read prior API publication")
        _prior_published_ids(prior, config, plan)
    parameters = {name: item["value"] for name, item in plan["parameters"].items()}
    verify_backend_secret_access(config, parameters["apiDefinitions"], state["serviceResourceIds"]["keyVault"], apim, transport)
    for prefix in ("l7Ingress", "realtimeTicket"):
        secret_id = parameters[prefix + "SecretIdentifier"]
        if not secret_id:
            continue
        # APIM の KV 参照は system MI。AppGW の証明書用 UAMI の有無で判定しない。
        managed = apim.get("identity", {})
        try:
            uuid.UUID(managed.get("principalId"))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("API Management system identity is not ready") from None
        if "SystemAssigned" not in {value.strip() for value in str(managed.get("type", "")).split(",")}:
            raise ValueError("API Management system identity is not ready")
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
    transition_needed = bool(plan["entraAuthentication"].get("enabled") or authentication_policy["previousEntra"])
    prior_source = None
    current_manifest = None
    preflight_scope_manifest = None
    fresh_snapshot = None
    new_api_ids = set()
    stage_prerequisites = None
    old_names = set((authentication_policy["previousEntra"] or {}).get("apiNames", []))
    new_names = set(plan["entraAuthentication"].get("apiNames", [])) if plan["entraAuthentication"].get("enabled") else set()
    selected_names = {resource_id.rsplit("/", 1)[-1] for resource_id in plan["publishedApiIds"]} - {"credential-manager-browser"}
    if stage_record is not None and (not transition_needed or changed or not callable(checkpoint)):
        raise ValueError("Resume requires a protected checkpoint and unchanged Firewall and Entra publication scope")
    resume = None
    native_products = None
    saved = state.get("apiPublicationSourceCheckpoint")
    if transition_needed:
        checkpoint_present = "apiPublicationSourceCheckpoint" in state
        if checkpoint_present:
            if (not isinstance(saved, dict) or saved.get("schemaVersion") != 1
                    or not _same(saved.get("apimId"), plan["apimId"])
                    or not _same(saved.get("deploymentId"), plan["deploymentId"])):
                raise ValueError("The protected API source checkpoint belongs to a different publication")
            prior_source = saved.get("source")
            if not isinstance(prior_source, dict):
                raise ValueError("The protected API source checkpoint is incomplete; restore the matching saved source before publication")
        else:
            prior_source = _prior_publication_source(prior_response, config, plan, transport)
        if prior_source is not None and (type(prior_source.get("schemaVersion")) is not int or prior_source["schemaVersion"] != 2):
            raise ValueError("Gateway publication requires current v2 API source")
        if plan["entraAuthentication"].get("enabled") and isinstance(prior_source, dict):
            # Incremental publication does not revoke existing business APIs.
            # Preserve all source-represented owned APIs, then validate the full
            # source graph before treating any of these names as authorized.
            entries = prior_source.get("apis", [])
            if isinstance(entries, list):
                new_names |= {entry["name"] for entry in entries
                              if isinstance(entry, dict) and isinstance(entry.get("name"), str)}
            new_names |= old_names
            if new_names != set(plan["entraAuthentication"].get("apiNames", [])):
                effective_entra = {**plan["entraAuthentication"], "apiNames": sorted(new_names)}
                merged, policy_changed = transition_common_authentication_policy(
                    authentication_policy["previousValue"], plan.get("credentialLogin"),
                    previous_entra=authentication_policy["previousEntra"], entra=effective_entra,
                    previous_fragments=authentication_policy.get("previousFragments", {}))
                authentication_policy.update(value=merged, changed=policy_changed, entraAuthentication=effective_entra)
        if prior_source is None and prior_response.get("statusCode") == 404 and not checkpoint_present and not authentication_policy["previousEntra"]:
            fresh_snapshot = entra_publication.read_fresh_publication(plan, transport)
            global_id = plan["apimId"] + "/policies/policy"
            # Existing global content is preserved. A missing global is created
            # conditionally as the closed key baseline before any API stage.
            if authentication_policy["previousValue"] is not None:
                observed_global = fresh_snapshot[global_id][2].get("body", {}).get("properties", {}).get("value")
                if observed_global != authentication_policy["previousValue"]:
                    raise ValueError("The global policy changed during fresh publication preflight")
        else:
            entries = prior_source.get("apis", []) if isinstance(prior_source, dict) else []
            represented_names = {entry["name"] for entry in entries
                                 if isinstance(entry, dict) and isinstance(entry.get("name"), str)} if isinstance(entries, list) else set()
            # Old authorized targets always require their existing source. Only
            # APIs actually selected for this stage can be proven new additions.
            addition_names = new_names - represented_names - old_names
            new_api_ids = {plan["apimId"] + "/apis/" + name for name in addition_names}
            if not new_api_ids <= set(plan["publishedApiIds"]):
                raise ValueError("An API without protected source must be selected for explicit closed publication")
            current_manifest = entra_publication.source_manifest(
                prior_source, plan["apimId"], old_names | (new_names & represented_names),
                authentication_policy["previousValue"])
            current_manifest = entra_publication.complete_named_value_expectations(current_manifest, plan, transport)
            if stage_record is None:
                stage_prerequisites = entra_publication.read_stage_shared_resources(current_manifest, plan, template, transport)
            elif old_names == new_names == represented_names == selected_names and not new_api_ids:
                resume = _read_closed_stage_attempt(stage_record, prior_response, saved, config, plan, apim, transport)
        if stage_record is not None and resume is None:
            raise ValueError("Resume requires a current checkpoint covering the same complete active API scope")
    # ここではAPIMの子資源を変更せず、同じ式から期待値を先に生成します。
    # 生成が失敗しても共通認証や社員Productは切り替わらず、既存通信を維持します。
    principal = apim.get("identity", {}).get("principalId")
    if resume is not None:
        generated_source, generated_browser, generation_id, attempted_job, proof = resume
        if source_generation_id(plan, template, identity_principal_id=principal) != generation_id:
            raise ValueError("Resume requires the same compiled API publication as the recorded failed stage")
        publication_template, _ = split_publication_templates(template)
        publication_template["outputs"][SOURCE_GENERATION_OUTPUT] = {"type": "string", "value": generation_id}
    else:
        publication_template, generated_source, generated_browser, generation_id = generate_publication_source(
            config, plan, template, transport, identity_principal_id=principal,
            sleep=sleep, timeout_seconds=max(0, deadline - time.monotonic()))
    if generation_id is not None:
        names = {entry.get("name") for entry in generated_source.get("apis", []) if isinstance(entry, dict)}
        expected_names = {rid.rsplit("/", 1)[-1] for rid in plan["publishedApiIds"]} - {"credential-manager-browser"}
        if generated_source.get("schemaVersion") != 2 or names != expected_names:
            raise ValueError("Generated publication source must cover exactly the reviewed protected APIs")
        entra_publication.source_manifest(generated_source, plan["apimId"], names, authentication_policy["value"])
    if resume is not None:
        attempted = entra_publication.source_manifest(generated_source, plan["apimId"], old_names, authentication_policy["previousValue"])
        preflight_scope_manifest = entra_publication.source_manifest(generated_source, plan["apimId"], new_names, authentication_policy["value"])
        attempted = entra_publication.complete_named_value_expectations(attempted, plan, transport)
        preflight_scope_manifest = entra_publication.complete_named_value_expectations(preflight_scope_manifest, plan, transport)
        native_products = stage_record["nativeProducts"]
        current_manifest, partial = entra_publication.read_failed_closed_stage(
            current_manifest, attempted, transport, scope_manifest=preflight_scope_manifest, plan=plan,
            previous_browser_source=saved.get("browserSource"), attempted_browser_source=generated_browser,
            native_products=native_products)
        stage_prerequisites = entra_publication.read_stage_shared_resources(current_manifest, plan, template, transport)
        stage_prerequisites.update(partial)
        stage_prerequisites.update(proof)
    stage_result = {}

    class StageTransport:
        # Helpers keep their existing readback/polling behavior. Only normal
        # stage writes share this transition's deadline; recovery stays outside.
        inner = transport
        supports_concurrent_reads = getattr(transport, "supports_concurrent_reads", False)
        def request(self, method, path, body=None, headers=None):
            if method != "GET" and time.monotonic() >= deadline:
                raise DeploymentError("API publication deadline expired before normal stage write")
            return self.inner.request(method, path, body=body, headers=headers)

    stage_transport = StageTransport()
    native_before = None

    def remember_native_products(value):
        nonlocal native_before
        native_before = value

    def publish_closed_stage():
        if changed:
            if time.monotonic() >= deadline:
                raise DeploymentError("API publication deadline expired before Firewall write")
            response = stage_transport.request("PUT", fw["groupId"] + "?api-version=" + NETWORK_API, body, headers={"If-Match": etag})
            require_success(response, "Update APIM-only Firewall FQDNs", allowed=(200, 201, 202))
            observed = _wait_resource(stage_transport, fw["groupId"], NETWORK_API, sleep=sleep, deadline=deadline)
            if not _contains(observed.get("properties"), body["properties"]):
                raise ValueError("Firewall readback did not preserve the approved rules; API publication was not attempted")
        deployment = {"tags": {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME},
            "properties": {"mode": "Incremental", "template": publication_template,
                           "parameters": publication_parameters(template, plan["parameters"]) if generation_id is not None else plan["parameters"]}}
        if time.monotonic() >= deadline:
            raise DeploymentError("API publication deadline expired before API write")
        if resume is not None:
            _confirm_publication_record(plan["deploymentId"], prior_response["body"], stage_transport)
            _confirm_publication_record(generation_id, attempted_job, stage_transport)
        elif (record_stage is not None and generation_id is not None and native_before is not None
              and isinstance(saved, dict) and saved.get("sourceGenerationId")
              and old_names == new_names == selected_names and not new_api_ids
              and prior_response.get("statusCode") == 200
              and prior_response["body"].get("properties", {}).get("provisioningState") == "Succeeded"):
            _confirm_publication_record(plan["deploymentId"], prior_response["body"], stage_transport)
            record_stage({"schemaVersion": 1, "checkpointSha256": _checkpoint_hash(saved),
                          "sourceGenerationId": generation_id, "nativeProducts": native_before,
                          "previousCorrelationId": prior_response["body"]["properties"].get("correlationId")})
        response = stage_transport.request("PUT", plan["deploymentId"] + "?api-version=" + DEPLOYMENT_API, deployment)
        require_success(response, "Start API-only publication", allowed=(200, 201, 202))
        observed = _wait_resource(stage_transport, plan["deploymentId"], DEPLOYMENT_API, sleep=sleep, deadline=deadline)
        published = observed.get("properties", {}).get("outputs", {}).get("publishedApiResourceIds", {}).get("value", [])
        # 指定した業務APIとCM接続用APIのすべてが配備されたことを確認する。
        expected = plan["publishedApiIds"]
        if not isinstance(published, list) or {value.lower() for value in published} != {value.lower() for value in expected}:
            raise ValueError("API publication output did not confirm the reviewed API set")
        outputs = observed.get("properties", {}).get("outputs", {})
        if generation_id is not None:
            binding = outputs.get(SOURCE_GENERATION_OUTPUT) if isinstance(outputs, dict) else None
            if not isinstance(binding, dict) or binding.get("value") != generation_id:
                raise ValueError("API publication did not confirm the reviewed source generation binding")
        source = generated_source if generation_id is not None else outputs.get("entraPublicationSource", {}).get("value")
        browser_source = generated_browser if generation_id is not None else outputs.get("credentialBrowserPublicationSource", {}).get("value")
        browser_snapshot = entra_publication.verify_browser_publication_source(plan, browser_source, stage_transport)
        stage_result.update(browserSource=browser_source or {}, browserSnapshot=browser_snapshot)
        # Incremental配備では消えない関連を、今回明示した未リリースAPIに限って解除する。
        # API本体・検証用Product・購読は保持し、指定外のAPIへは触れない。
        released_product = reconcile_unreleased_apis(plan["apimId"], parameters["apiDefinitions"], stage_transport, sleep=sleep)
        websocket_diagnostics = enable_websocket_diagnostics(plan, state, stage_transport, sleep=sleep, deadline=deadline)
        # API側のキー必須設定と出力を確認してから全体policyを切り替えます。
        # 途中失敗時は共通入口を切り替えず、失敗配備記録から同じ計画を再実行できます。
        if not transition_needed:
            install_common_authentication(authentication_policy, stage_transport, sleep=sleep, deadline=deadline)
        # 正式APIの公開が完了してから、APIMが初期作成するサンプルだけを整理する。
        # 未登録・削除済みは正常。業務用に転用した同名定義や、その購読は保持する。
        try:
            sample_cleanup = cleanup_default_samples(plan["apimId"], parameters["apiDefinitions"], stage_transport, sleep=sleep)
        except (ValueError, DeploymentError) as error:
            raise DeploymentError("APIs were published, but default APIM sample cleanup did not finish: " + str(error)) from error
        stage_result.update(published=published, released_product=released_product, websocket_diagnostics=websocket_diagnostics,
                            sample_cleanup=sample_cleanup, source=source)
        if transition_needed:
            source_entries = source.get("apis") if isinstance(source, dict) else None
            if (not isinstance(source_entries, list) or type(source.get("schemaVersion")) is not int
                    or source["schemaVersion"] != 2 or any(
                        not isinstance(entry, dict) or entry.get("name") not in selected_names for entry in source_entries)):
                raise ValueError("Closed staging requires complete v2 source for the reviewed selected APIs")
            staged_names = {entry["name"] for entry in source_entries}
            if staged_names != selected_names:
                raise ValueError("Closed staging source must represent every selected protected API")
            # Validate this generation independently before merging; a prior
            # source must not hide duplicates or missing current source fields.
            entra_publication.source_manifest(source, plan["apimId"], staged_names, authentication_policy["value"])
            merged_source = source if prior_source is None else entra_publication.merge_sources(prior_source, source)
            stage_result["source"] = merged_source
            desired = entra_publication.source_manifest(merged_source, plan["apimId"], old_names | new_names, authentication_policy["value"])
            return entra_publication.complete_named_value_expectations(desired, plan, stage_transport)
        return None

    def record_source():
        if stage_result.get("browserSnapshot"):
            entra_publication.verify_snapshot(stage_result["browserSnapshot"], transport)
        if checkpoint is not None and stage_result.get("source") is not None:
            binding = {"generation_id": generation_id} if generation_id is not None else {}
            value = source_checkpoint(plan, stage_result["source"], authentication_policy["value"],
                                      entra_authentication=authentication_policy.get("entraAuthentication", plan["entraAuthentication"]),
                                      browser_source=stage_result.get("browserSource"), **binding)
            checkpoint(value)

    entra_result = None
    if transition_needed:
        if fresh_snapshot is not None:
            entra_publication.verify_snapshot(fresh_snapshot, transport)
            global_id = plan["apimId"] + "/policies/policy"
            if authentication_policy["previousValue"] is None:
                closed_xml = CLOSED_INITIAL_POLICY
                response = transport.request("PUT", authentication_policy["path"],
                    {"properties": {"format": "rawxml", "value": closed_xml}}, headers={"If-None-Match": "*"})
                require_success(response, "Create the closed initial global policy", allowed=(200, 201, 202))
                observed = entra_publication._wait(transport, global_id, sleep=sleep, deadline=deadline, raw=True)
                entra_publication._same_xml(observed.get("body", {}).get("properties", {}).get("value"), closed_xml)
                authentication_policy["previousValue"] = closed_xml
                # Refresh only this conditional creation; all other absence and
                # dependency witnesses must still match immediately before stage.
                fresh_snapshot[global_id] = (global_id + "?api-version=" + entra_publication.APIM_API + "&format=rawxml",
                                             {"Accept": "application/json"}, observed)
                entra_publication.verify_snapshot(fresh_snapshot, transport)
            try:
                desired = publish_closed_stage()
                current_manifest = entra_publication.PolicyManifest(desired.apim_id,
                    {**desired.policies, global_id: authentication_policy["previousValue"]}, desired.operations,
                    desired.products, desired.backends, desired.named_values, desired.api_properties,
                    desired.api_children, desired.source_version)
                # The new ARM source becomes trustworthy only after exact graph
                # readback, unchanged old global and an empty employee Product.
                staged_snapshot = entra_publication.read_policy_graph(current_manifest, transport, scope_manifest=desired)
                if entra_publication._signature(staged_snapshot[global_id][2]) != entra_publication._signature(fresh_snapshot[global_id][2]):
                    raise ValueError("The global policy drifted during the closed initial stage")
                product_snapshot, members = entra_publication.read_open_product(plan["apimId"], transport, allowed_api_ids=(), missing=False)
                if members:
                    raise ValueError("The initial API stage must leave employee membership empty")
                staged_snapshot.update(product_snapshot)
                entra_publication.verify_snapshot(staged_snapshot, transport)
            except (ValueError, OSError, DeploymentError):
                closed = False
                try:
                    _, members = entra_publication.read_open_product(plan["apimId"], transport, allowed_api_ids=(), missing=True)
                    closed = not members
                except (ValueError, DeploymentError):
                    pass
                raise entra_publication.TransitionError(employee_path_closed=closed, recovery_complete=False) from None
            preflight_scope_manifest = desired
            stage_callback = lambda: desired
        else:
            stage_callback = publish_closed_stage
        def share_prepared_deadline(value):
            # 閉鎖から再公開まで、read-only preflight後に始まる同じ予算を使います。
            nonlocal deadline
            deadline = value

        entra_result = entra_publication.transition(current_manifest, authentication_policy["value"], transport, stage_callback,
            previous_open_api_ids={plan["apimId"] + "/apis/" + name for name in old_names},
            desired_open_api_ids={plan["apimId"] + "/apis/" + name for name in new_names},
            new_api_ids=new_api_ids,
            stage_prerequisites=stage_prerequisites,
            preflight_scope_manifest=preflight_scope_manifest,
            on_staged=record_source, on_prepared=share_prepared_deadline,
            on_stage_ready=remember_native_products if record_stage is not None else None,
            native_products=native_products,
            sleep=sleep, timeout_seconds=timeout_seconds)
    else:
        publish_closed_stage()
        # Preserve the legacy key-only publication contract (custom Products,
        # revisions, etc.). This code-generated baseline is not activation proof;
        # a later enable performs the complete restrictive graph audit first.
        record_source()
    published = stage_result["published"]
    released_product = stage_result["released_product"]
    websocket_diagnostics = stage_result["websocket_diagnostics"]
    sample_cleanup = stage_result["sample_cleanup"]
    # 通常構築と同じ状態形式を使い、APIだけの更新でも撤去用IDが変わらないことを確認する。
    preserved_state = state_from_outputs(config, state["outputs"])
    return {"schemaVersion": 1, "subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
        "entraTenantId": entra_tenant_id(config),
        "resourceStem": config["resourceStem"], "deploymentId": plan["deploymentId"], "apimId": plan["apimId"],
        "firewallUpdated": changed, "commonAuthenticationPolicyUpdated": authentication_policy["changed"],
        "webSocketDiagnostics": websocket_diagnostics,
        "entraAuthentication": entra_result, "entraPublicationSource": stage_result["source"],
        **({"sourceGenerationId": generation_id} if generation_id is not None else {}),
        "credentialBrowserPublicationSource": stage_result.get("browserSource", {}),
        "releasedProduct": released_product,
        "publishedApiIds": published, "status": "management-plane-published",
        "runtimeVerified": False,
        "defaultSampleCleanup": sample_cleanup,
        "cleanupIdsPreserved": preserved_state["serviceResourceIds"] == state["serviceResourceIds"]}
