"""同じAPIのEntra公開を閉じた状態で更新するための管理プレーン準備処理。

APIの購読必須を維持し、専用open Productへの所属を最後に追加する。
Bicepの生成元XMLと実readbackを照合し、所属を最初に解除してから更新する。
設定のverified/proof値や過去checkpointを有効化の根拠にしない。
このモジュールの遷移順・競合・失敗回復は合成transportで検証する。
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from deployment.lifecycle import DeploymentError, require_success
from deployment.helpers.apim_authentication import (
    ENTRA_OPEN_PRODUCT_ID, ENTRA_NAMED_VALUES, is_closed_initial_policy,
    validate_entra_named_values, validate_entra_named_value,
)
from deployment.helpers.apim_audit import AUDIT_FRAGMENT_FILES
from deployment.helpers.apim_api_settings import (
    API_SETTINGS_ID, API_SETTINGS_SOURCE_VARIABLE, validate_api_settings_inheritance, validate_api_settings_scope,
    _source_settings,
)
from deployment.helpers.apim_api_request import validate_api_request_preparation_scope
from deployment.helpers.apim_query_observation import validate_query_observation_scope
from deployment.helpers.apim_ingress import (
    TRUSTED_INGRESS_ID, render_trusted_ingress_fragment,
)
from deployment.helpers.apim_browser_exception import BROWSER_EXCEPTION_ID, BROWSER_EXCEPTION_VARIABLE, render_browser_exception_fragment
from deployment.helpers.apim_common_authentication import (
    AUTHENTICATION_FRAGMENT_IDS, COMMON_AUTHENTICATION_FRAGMENT_ID, ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID,
    render_common_authentication_fragments,
)
from deployment.helpers.apim_samples import SAMPLE_API, is_default_echo_api
from deployment.helpers.credential_login import API_ID as CREDENTIAL_LOGIN_API_ID, OPERATIONS as CREDENTIAL_LOGIN_OPERATIONS
from deployment.helpers.credential_broker_lifecycle import broker_https_ingress
from deployment.helpers.paths import REPOSITORY

APIM_API = "2025-09-01-preview"
SECTIONS = ("inbound", "backend", "outbound", "on-error")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
OPERATION_BACKEND_IDENTIFIER = re.compile(r"op-[A-Za-z0-9]+-backend(?:-secret)?", re.IGNORECASE)
PENDING = ("Accepted", "Creating", "Updating", "InProgress")
OPEN_PRODUCT_PROPERTIES = {"displayName": "Entra model users", "description": "core-owned-entra-model-users-v1",
                           "subscriptionRequired": False, "state": "notPublished"}
OPEN_PRODUCT_POLICY = '<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
PRM_GUARD = (REPOSITORY / 'apps/api-gateway/policies/snippets/request/mcp-metadata-guard/mcp-metadata-guard.xml').read_text(encoding='utf-8')
# 読戻しで、独立した旧生成元と完全一致する従来guardだけを照合します。
# 新配備へこのXMLを差し込まず、現行正本への置換にも使いません。
PREVIOUS_PRM_GUARD = '<choose><when condition="@(context.Request.OriginalUrl.Path.IndexOf(&quot;/.well-known/oauth-protected-resource&quot;, StringComparison.OrdinalIgnoreCase) &gt;= 0)"><return-response><set-status code="404" reason="Not Found" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></when></choose>'


@dataclass(frozen=True)
class PolicyManifest:
    """コード生成元の期待値。利用者の設定・自己申告proofとして受け付けない。

    policiesはglobal/API/全operation/reachable fragmentのXML、operationsは
    対象APIの全operation ID、productsは生成元で既知のProduct ID集合。
    backends/named_valuesは参照先の期待するproperties全体（秘密本体を含めない）。
    Bicepの保護された生成元出力から作成し、試験では独立した合成値を使う。
    """
    apim_id: str
    policies: dict[str, str]
    operations: dict[str, tuple[str, ...]]
    products: dict[str, tuple[str, ...]]
    backends: dict[str, dict]
    named_values: dict[str, dict]
    api_properties: dict[str, dict] = field(default_factory=dict)
    api_children: dict[str, dict] = field(default_factory=dict)
    source_version: int = 1


class TransitionError(DeploymentError):
    """原文・IDを公開せず、社員経路と回復状態を明示する。"""
    def __init__(self, *, employee_path_closed: bool, recovery_complete: bool):
        self.employee_path_closed = employee_path_closed
        self.recovery_complete = recovery_complete
        status = "closed" if employee_path_closed else "closure could not be verified"
        recovery = "verified" if recovery_complete else "incomplete; fresh readback and operator review are required"
        super().__init__(f"Entra publication failed; employee path {status}; recovery {recovery}")


def _contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains(actual[key], value) for key, value in expected.items())
    return type(actual) is type(expected) and actual == expected


def _resource_properties_match(resource_id, actual, expected):
    """Entra NVのJSONだけを完全な型付き設定として照合し、他資源の比較は保持する。"""
    name = resource_id.rsplit("/", 1)[-1]
    if "/namedValues/" in resource_id and name in ENTRA_NAMED_VALUES:
        try:
            validate_entra_named_value({"properties": actual}, name, expected.get("value"))
        except (ValueError, TypeError, AttributeError):
            return False
        return _contains(actual, {key: value for key, value in expected.items() if key != "value"})
    return _contains(actual, expected)


def _operation_routing_matches(actual, expected):
    """Operationの既知の空既定値と、生成元未指定の表示用sampleだけを除く。"""
    actual, expected = copy.deepcopy(actual), copy.deepcopy(expected)

    def parameters(contract, name):
        if isinstance(contract, dict) and isinstance(contract.get(name), list):
            for parameter in contract[name]:
                if isinstance(parameter, dict):
                    parameter.setdefault("values", [])

    def representations(observed, source):
        if not isinstance(observed, dict) or not isinstance(source, dict):
            return
        left, right = observed.get("representations"), source.get("representations")
        if isinstance(left, list) and isinstance(right, list):
            # Length, order and every contract attribute remain in the final
            # exact list comparison. Explicit source examples remain strict.
            for item, expected_item in zip(left, right):
                if isinstance(item, dict) and isinstance(expected_item, dict):
                    for field in ("sample", "generatedSample", "examples"):
                        if field not in expected_item:
                            item.pop(field, None)

    for contract in (actual, expected):
        parameters(contract, "templateParameters")
        if not isinstance(contract, dict):
            continue
        request = contract.get("request")
        for name in ("headers", "queryParameters"):
            parameters(request, name)
        if isinstance(contract.get("responses"), list):
            for response in contract["responses"]:
                if isinstance(response, dict):
                    response.setdefault("headers", [])
                    parameters(response, "headers")
    if isinstance(actual, dict) and isinstance(expected, dict):
        representations(actual.get("request"), expected.get("request"))
        left, right = actual.get("responses"), expected.get("responses")
        if isinstance(left, list) and isinstance(right, list):
            for item, source in zip(left, right):
                representations(item, source)
    return _contains(actual, expected)


def _owned_open_product_properties(properties):
    if not _contains(properties, OPEN_PRODUCT_PROPERTIES):
        return False
    # Omit this subscription-only field from writes. Readbacks may omit it or
    # return null/false, but never coerce strings or numeric zero to false.
    approval = properties.get("approvalRequired")
    return approval is None or approval is False


def _etag(response):
    value = response.get("body", {}).get("etag") or next(
        (value for name, value in response.get("headers", {}).items() if name.lower() == "etag"), None)
    if not isinstance(value, str) or not value.strip() or value.strip('" ') == "*":
        raise ValueError("A concrete APIM ETag is required; unconditional changes are forbidden")
    return value


def _properties(response):
    properties = copy.deepcopy(response.get("body", {}).get("properties", {}))
    properties.pop("provisioningState", None)
    return properties


def _normalize_key_vault_refresh(resource, resource_id=None):
    """正常なKV-backed NamedValueだけ、自動更新時刻と付随ETagを除く。"""
    if not isinstance(resource, dict):
        return False
    identifier = resource.get("id")
    resource_type = resource.get("type")
    if (not isinstance(identifier, str) or not isinstance(resource_type, str)
            or resource_type.casefold() != "microsoft.apimanagement/service/namedvalues"
            or not re.fullmatch(r"/subscriptions/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/resourceGroups/[^/?#\r\n]+/providers/Microsoft\.ApiManagement/service/[^/?#\r\n]+/namedValues/[^/?#\r\n]+", identifier, re.IGNORECASE)
            or resource.get("name") != identifier.rsplit("/", 1)[-1]
            or (resource_id is not None and identifier.casefold() != resource_id.casefold())):
        return False
    properties = resource.get("properties")
    if not isinstance(properties, dict) or properties.get("secret") is not True:
        return False
    vault = properties.get("keyVault")
    if not isinstance(vault, dict) or not isinstance(vault.get("secretIdentifier"), str):
        return False
    client = vault.get("identityClientId")
    if client is not None and (not isinstance(client, str) or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", client, re.IGNORECASE)):
        return False
    try:
        secret = urlsplit(vault["secretIdentifier"])
        if (secret.scheme != "https" or not secret.hostname or secret.username or secret.password
                or secret.query or secret.fragment or secret.port not in (None, 443)
                or not re.fullmatch(r"/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+)?", secret.path)):
            return False
    except ValueError:
        return False
    status = vault.get("lastStatus")
    if (not isinstance(status, dict) or not isinstance(status.get("code"), str) or not status["code"]
            or not isinstance(status.get("timeStampUtc"), str)
            or ("message" in status and status["message"] is not None and not isinstance(status["message"], str))):
        return False
    timestamp = status["timeStampUtc"]
    try:
        if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", timestamp)
                or datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset().total_seconds() != 0):
            return False
    except ValueError:
        return False
    # A valid fixed representative preserves field presence/type: missing or
    # malformed timestamps must not compare equal to an accepted refresh.
    status["timeStampUtc"] = "0001-01-01T00:00:00Z"
    resource.pop("etag", None)
    return True


def _signature(response, *, resource_id=None):
    """構成とETagを照合する。KV参照の正常な自動更新情報だけを除く。"""
    body = copy.deepcopy(response.get("body", {}))
    if isinstance(body, dict) and isinstance(body.get("properties"), dict):
        body["properties"].pop("provisioningState", None)
    resource_id = resource_id.split("?", 1)[0] if isinstance(resource_id, str) else None
    refreshed = response.get("statusCode") == 200 and _normalize_key_vault_refresh(body, resource_id)
    if (response.get("statusCode") == 200 and isinstance(resource_id, str)
            and re.fullmatch(r"/subscriptions/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/resourceGroups/[^/?#\r\n]+/providers/Microsoft\.ApiManagement/service/[^/?#\r\n]+/namedValues", resource_id, re.IGNORECASE)
            and isinstance(body, dict) and set(body) <= {"value", "count", "nextLink"}
            and isinstance(body.get("value"), list)):
        for entry in body["value"]:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                _normalize_key_vault_refresh(entry, resource_id + "/" + entry["name"])
    return response.get("statusCode"), body, {name.lower(): value for name, value in response.get("headers", {}).items()
                                              if name.lower() == "etag" and not refreshed}


def _xml(value, *, fragment=False, inherited=False, previous_prm_guard=False):
    if not isinstance(value, str) or any(item in value.upper() for item in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("Raw policy XML is missing or unsafe")
    try:
        root = ET.fromstring(value)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("Raw policy XML is invalid") from None
    if fragment:
        if (root.tag != "fragment" or len(value.encode("utf-8")) > 512 * 1024
                or any(node.tag in {*SECTIONS, "base", "include-fragment"} for node in root.iter())):
            raise ValueError("Policy fragment inheritance is invalid")
        return root
    if root.tag != "policies" or [child.tag for child in root] != list(SECTIONS):
        raise ValueError("All policy sections must be present and ordered")
    if inherited:
        section = root.find("inbound")
        bases = [node for node in section.iter() if node.tag == "base"]
        if len(bases) != 1 or bases[0] not in list(section):
            raise ValueError("Inbound policy inheritance must execute exactly once")
        before = list(section)[:list(section).index(bases[0])]
        validate_api_settings_inheritance(root)
        metadata = ("gatewayAuditSettings", "gatewayControlSettings", API_SETTINGS_SOURCE_VARIABLE)
        guards = [_structure(ET.fromstring(PRM_GUARD))]
        if previous_prm_guard:
            guards.append(_structure(ET.fromstring(PREVIOUS_PRM_GUARD)))
        if any(not (node.tag == "set-variable" and node.get("name") in metadata)
               and not (node.tag == "include-fragment" and node.attrib == {"id": API_SETTINGS_ID, "fragment-id": API_SETTINGS_ID})
               and _structure(node) not in guards for node in before):
            raise ValueError("Only source-verified audit/operation metadata may precede inbound inheritance")
    return root


def _structure(root):
    def content(value):
        return value if value and value.strip() else ""
    return (root.tag, tuple(sorted(root.attrib.items())), content(root.text),
            tuple((_structure(child), content(child.tail)) for child in root))


def _same_xml(actual, expected, *, fragment=False, inherited=False):
    # 旧guardは読戻し比較だけで扱い、actualと旧／新のexpectedの完全一致を要求します。
    if _structure(_xml(actual, fragment=fragment, inherited=inherited, previous_prm_guard=inherited)) != _structure(
            _xml(expected, fragment=fragment, inherited=inherited, previous_prm_guard=inherited)):
        raise ValueError("Raw policy readback differs from the generated source; markers are not proof")


def _operation_entra_error_sites(policies, apim_id, *, api_properties=None):
    """検証済みAPI設定の操作種別と、CMの固定保護操作からエラーの引渡し先を選ぶ。"""
    sites = set()
    for resource_id, value in policies.items():
        suffix = resource_id.removeprefix(apim_id).split("/")
        if (not resource_id.startswith(apim_id + "/apis/") or len(suffix) != 5
                or suffix[3:] != ["policies", "policy"]):
            continue
        root = _xml(value)
        seed = root.find("inbound/set-variable[@name='" + API_SETTINGS_SOURCE_VARIABLE + "']")
        if seed is None:
            continue
        # 呼出し元は先にseed/callの配置・固定形式・API種別を検証している。
        # 同じdecoderで制御設定だけを取り出し、API名やモデル名では分類しない。
        settings = _source_settings(seed)
        if settings["apiType"] != "http":
            continue
        constraints = json.loads(settings["control"]).get("operationConstraints", {})
        if not isinstance(constraints, dict):
            raise ValueError("Operation error handoff requires the generated operation constraints")
        api_id = resource_id.removesuffix("/policies/policy")
        for name, constraint in constraints.items():
            if isinstance(constraint, dict) and constraint.get("kind") in ("pii", "documentAnalysis", "documentAnalysisStatus"):
                site = api_id + "/operations/" + _identifier(name) + "/policies/policy"
                if site in policies:
                    sites.add(site)
    cm_api = apim_id + "/apis/" + CREDENTIAL_LOGIN_API_ID
    cm_policy = policies.get(cm_api + "/policies/policy")
    if cm_policy is not None and (api_properties is None or cm_api in api_properties
                                 and (api_properties[cm_api].get("type") or "http") == "http"):
        # ブラウザーの4操作は含めず、生成元が公開するCM保護操作だけを扱う。
        root = _xml(cm_policy)
        if _structure(root) == _structure(_xml(OPEN_PRODUCT_POLICY)):
            sites.update(cm_api + "/operations/" + name + "/policies/policy"
                         for name in CREDENTIAL_LOGIN_OPERATIONS
                         if cm_api + "/operations/" + name + "/policies/policy" in policies)
    return sites


def _audit_fragment_scope(policies, apim_id, *, api_properties=None):
    """共通入口の部品を固定位置へ限定し、認証例外の拡大や変数上書きを防ぐ。"""
    validate_api_settings_scope(policies, apim_id, api_properties=api_properties)
    validate_api_request_preparation_scope(policies, apim_id, api_properties=api_properties)
    validate_query_observation_scope(policies, apim_id, api_properties=api_properties)
    operation_error_sites = _operation_entra_error_sites(policies, apim_id, api_properties=api_properties)
    global_id = apim_id + "/policies/policy"
    ingress_id = TRUSTED_INGRESS_ID
    common_ids = set(AUDIT_FRAGMENT_FILES) | {ingress_id, BROWSER_EXCEPTION_ID} | set(AUTHENTICATION_FRAGMENT_IDS)
    writers = {variable: identifier for identifier, (_, variable) in AUDIT_FRAGMENT_FILES.items()}
    writers[BROWSER_EXCEPTION_VARIABLE] = BROWSER_EXCEPTION_ID
    references = []
    operation_error_references = set()
    inline_ingress = []
    common_inbound = None
    for resource_id, value in policies.items():
        fragment = "/policyFragments/" in resource_id
        root = _xml(value, fragment=fragment)
        fragment_name = resource_id.rsplit("/", 1)[-1] if fragment else None
        if fragment_name in AUTHENTICATION_FRAGMENT_IDS:
            if not any(fragment_name in generated and _structure(root) == _structure(_xml(generated[fragment_name], fragment=True))
                       for generated in (render_common_authentication_fragments(entra_enabled=False),
                                         render_common_authentication_fragments(entra_enabled=True))):
                raise ValueError("Authentication fragments must match their complete fixed generated source")
        if fragment_name in AUDIT_FRAGMENT_FILES:
            children = list(root)
            variable = AUDIT_FRAGMENT_FILES[fragment_name][1]
            if (len(children) != 1 or children[0].tag != "set-variable"
                    or set(children[0].attrib) != {"id", "name", "value"}
                    or children[0].get("id") != fragment_name or children[0].get("name") != variable):
                raise ValueError("Each audit fragment must contain only its fixed variable writer")
        if fragment_name == ingress_id:
            children = list(root)
            if (root.attrib or len(children) != 1 or children[0].tag != "choose"
                    or children[0].attrib != {"id": ingress_id}):
                raise ValueError("The trusted ingress fragment must contain only its fixed ingress decision")
            if _structure(root) != _structure(_xml(render_trusted_ingress_fragment(), fragment=True)):
                raise ValueError("The trusted ingress fragment differs from its fixed current source")
        if fragment_name == BROWSER_EXCEPTION_ID:
            children = list(root)
            if (root.attrib or len(children) != 1 or children[0].tag != "set-variable"
                    or set(children[0].attrib) != {"id", "name", "value"}
                    or children[0].get("id") != BROWSER_EXCEPTION_ID
                    or children[0].get("name") != BROWSER_EXCEPTION_VARIABLE):
                raise ValueError("The browser exception fragment must contain only its fixed Boolean writer")
            if not any(_structure(root) == _structure(_xml(render_browser_exception_fragment(login), fragment=True))
                       for login in (None, {"configured": True})):
                raise ValueError("The browser exception must preserve its fixed four-operation predicate")
        inbound = root.find("inbound") if not fragment else None
        if resource_id == global_id:
            common_inbound = inbound
        for node in root.iter():
            if node.tag == "include-fragment" and node.get("fragment-id") in common_ids:
                identifier = node.get("fragment-id")
                section = root.find("on-error") if identifier == ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID else inbound
                if identifier == ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID and resource_id in operation_error_sites:
                    # 個別on-errorは親を全面継承しない。共通トークン検証の失敗だけを
                    # 先頭で引き渡し、それ以外は現在の個別エラー処理へ進める。
                    if (section is None or not len(section) or section[0] is not node
                            or node.attrib != {"id": identifier, "fragment-id": identifier}
                            or list(node) or (node.text and node.text.strip())
                            or any(child.tag == "base" for child in section.iter())
                            or resource_id in operation_error_references):
                        raise ValueError("Operation token errors require one fixed handler at the start of on-error")
                    if apim_id + "/policyFragments/" + identifier not in policies:
                        raise ValueError("The operation error reference needs its complete generated fragment source")
                    operation_error_references.add(resource_id)
                    continue
                if (resource_id != global_id or section is None or node not in list(section)
                        or node.attrib != {"id": identifier, "fragment-id": identifier}
                        or list(node) or (node.text and node.text.strip())):
                    raise ValueError("Common fragments may be invoked only at the fixed common inbound positions")
                if apim_id + "/policyFragments/" + identifier not in policies:
                    raise ValueError("The common reference needs its complete generated fragment source")
                references.append(identifier)
            if node.get("id") in AUTHENTICATION_FRAGMENT_IDS and node.tag != "include-fragment":
                identifier = node.get("id")
                section = root.find("on-error") if identifier == ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID else inbound
                owned = resource_id == apim_id + "/policyFragments/" + identifier and node in list(root)
                previous = resource_id == global_id and section is not None and node in list(section)
                if node.tag != "choose" or node.attrib != {"id": identifier} or not (owned or previous):
                    raise ValueError("Only the fixed common authentication decisions may use their owned identifiers")
            if node.tag == "include-fragment" and node.get("id") in AUTHENTICATION_FRAGMENT_IDS and node.get("id") != node.get("fragment-id"):
                raise ValueError("Authentication includes must reference their fixed source")
            if node.tag == "set-variable" and node.get("name") in (
                    "gatewayCommonAuthenticationValidated", "gatewayEntraAuthenticationValidated", "gatewaySubscriptionId",
                    "gatewayUserAuthentication", "gatewayEntraTokenValidationCompleted", "gatewayEntraIdentityResult",
                    "gatewayEntraAuthenticationResult", "gatewayEntraFailureSubject"):
                owned = resource_id == apim_id + "/policyFragments/" + COMMON_AUTHENTICATION_FRAGMENT_ID
                previous = resource_id == global_id and inbound is not None and any(
                    node in list(block.iter()) for block in inbound if block.tag == "choose" and block.get("id") == COMMON_AUTHENTICATION_FRAGMENT_ID)
                if not (owned or previous):
                    raise ValueError("Only the complete common authentication source may write authentication results")
            if node.get("id") == ingress_id and node.tag != "include-fragment":
                owned_fragment = resource_id == apim_id + "/policyFragments/" + ingress_id and node in list(root)
                previous_inline = resource_id == global_id and inbound is not None and node in list(inbound)
                if node.tag != "choose" or node.attrib != {"id": ingress_id} or not (owned_fragment or previous_inline):
                    raise ValueError("Only the fixed common ingress decision may use its owned identifier")
                if previous_inline:
                    inline_ingress.append(node)
            if node.tag == "include-fragment" and node.get("id") == ingress_id and node.get("fragment-id") != ingress_id:
                raise ValueError("The ingress include must reference its fixed fragment source")
            if node.tag == "include-fragment" and node.get("id") == BROWSER_EXCEPTION_ID and node.get("fragment-id") != BROWSER_EXCEPTION_ID:
                raise ValueError("The browser exception include must reference its fixed fragment source")
            if node.tag == "set-variable" and node.get("name") in writers:
                identifier = writers[node.get("name")]
                owned_fragment = resource_id == apim_id + "/policyFragments/" + identifier
                previous_inline = identifier != BROWSER_EXCEPTION_ID and resource_id == global_id and inbound is not None and node in list(inbound)
                if node.get("id") != identifier or not (owned_fragment or previous_inline):
                    raise ValueError("The common variables must not be written by API or unrelated fragment policies")
    if len(inline_ingress) > 1 or inline_ingress and ingress_id in references:
        raise ValueError("The common ingress decision must execute exactly once")
    if references and ingress_id not in references and not inline_ingress:
        raise ValueError("The common audit references require their fixed ingress decision")
    audit_ids = list(AUDIT_FRAGMENT_FILES)
    expected = [audit_ids[0], ingress_id, audit_ids[1]] if ingress_id in references else audit_ids
    if BROWSER_EXCEPTION_ID in references:
        expected = [audit_ids[0], ingress_id, BROWSER_EXCEPTION_ID, audit_ids[1]]
    if COMMON_AUTHENTICATION_FRAGMENT_ID in references:
        expected.insert(expected.index(audit_ids[1]), COMMON_AUTHENTICATION_FRAGMENT_ID)
    if ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID in references:
        expected.append(ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID)
    if references and references != expected:
        raise ValueError("Common fragments must be invoked exactly once in their fixed order")
    browser_condition = '@((bool)context.Variables["gatewayCredentialBrowserException"])'
    authentication = common_inbound.find("choose[@id='core-common-authentication']") if common_inbound is not None else None
    authentication_call = common_inbound.find("include-fragment[@id='core-common-authentication']") if common_inbound is not None else None
    if authentication_call is not None:
        if authentication is not None:
            raise ValueError("Common authentication cannot mix an inline block and a fragment call")
        authentication = _xml(policies[apim_id + "/policyFragments/" + COMMON_AUTHENTICATION_FRAGMENT_ID], fragment=True)[0]
        has_validator = authentication.find(".//validate-azure-ad-token") is not None
        if has_validator != (ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID in references):
            raise ValueError("The token validator and its fixed error handler must be deployed together")
        if has_validator:
            error_root = _xml(policies[global_id]).find("on-error")
            if error_root is None or [node.get("id") for node in list(error_root)[:2]] != ["core-error-request-id", ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID]:
                raise ValueError("Token validation errors require request correlation before their fixed handler")
    if operation_error_references and (authentication_call is None
            or ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID not in references
            or authentication.find(".//validate-azure-ad-token") is None):
        raise ValueError("Operation token error handlers require the active common validator and its global handler")
    first_when = authentication.find("when") if authentication is not None else None
    if BROWSER_EXCEPTION_ID in references:
        nodes = list(common_inbound)
        exception = next(node for node in nodes if node.get("id") == BROWSER_EXCEPTION_ID)
        subject = next(node for node in nodes if node.get("id") == audit_ids[1])
        if (first_when is None or first_when.get("condition") != browser_condition
                or not nodes.index(exception) < nodes.index(authentication_call if authentication_call is not None else authentication) < nodes.index(subject)):
            raise ValueError("The browser exception must be decided before common authentication")
    elif first_when is not None and first_when.get("condition") == browser_condition:
        raise ValueError("The browser exception decision needs its fixed common fragment")
    if inline_ingress and references:
        nodes = list(common_inbound)
        request = next(node for node in nodes if node.get("id") == audit_ids[0])
        subject = next(node for node in nodes if node.get("id") == audit_ids[1])
        if not nodes.index(request) < nodes.index(inline_ingress[0]) < nodes.index(subject):
            raise ValueError("The previous inline ingress decision must retain the common audit order")


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("A referenced resource must have a static canonical identifier")
    return value


def _read(transport, resource_id, snapshot, *, raw=False, missing=False, collection=False):
    path = resource_id + "?api-version=" + APIM_API + ("&format=rawxml" if raw else "")
    headers = {"Accept": "application/json"} if raw else None
    response = transport.request("GET", path, headers=headers)
    if missing and response.get("statusCode") == 404:
        snapshot[resource_id] = (path, headers, copy.deepcopy(response))
        return None
    body = require_success(response, "Read Entra publication prerequisite")
    if not isinstance(body, dict):
        raise ValueError("APIM readback is invalid")
    if collection:
        if body.get("nextLink") or not isinstance(body.get("value"), list):
            raise ValueError("APIM inventory is incomplete; reconcile pagination before publication")
    else:
        _etag(response)
        if body.get("properties", {}).get("provisioningState") in PENDING:
            raise ValueError("APIM prerequisite is still being updated")
    snapshot[resource_id] = (path, headers, copy.deepcopy(response))
    return body


def _validate_manifest_scope(manifest: PolicyManifest, scope_manifest: PolicyManifest | None = None):
    """現行配置を検査し、過去XMLとの照合では非policy構成の完全一致を要求する。"""
    scope = manifest if scope_manifest is None else scope_manifest
    if (not isinstance(scope, PolicyManifest) or any(
            getattr(scope, name) != getattr(manifest, name)
            for name in ("apim_id", "operations", "products", "api_properties", "api_children", "backends", "source_version"))):
        raise ValueError("A historical policy baseline requires the same complete current API scope")
    _audit_fragment_scope(scope.policies, manifest.apim_id, api_properties=scope.api_properties)


def _native_product_contract(snapshot, manifest):
    apim = manifest.apim_id
    roots = {apim + "/products/" + _identifier(name) for names in manifest.products.values() for name in names}
    result = {}
    for resource_id in sorted(roots):
        product = _properties(snapshot[resource_id][2])
        policy = snapshot[resource_id + "/policies/policy"][2]
        result[resource_id] = {
            "properties": {key: value for key, value in product.items() if key != "provisioningState"},
            "policy": None if policy.get("statusCode") == 404 else _properties(policy)["value"],
        }
    return result


def _verify_native_products(snapshot, manifest, expected):
    actual = _native_product_contract(snapshot, manifest)
    if not isinstance(expected, dict) or set(actual) != set(expected):
        raise ValueError("The recorded native Product inventory is incomplete")
    for resource_id, values in actual.items():
        prior = expected[resource_id]
        if (not isinstance(prior, dict) or set(prior) != {"properties", "policy"}
                or json.dumps(values["properties"], sort_keys=True) != json.dumps(prior["properties"], sort_keys=True)
                or (values["policy"] is None) != (prior["policy"] is None)):
            raise ValueError("The native Product contract changed after closed staging")
        if values["policy"] is not None:
            _same_xml(values["policy"], prior["policy"])


def read_policy_graph(manifest: PolicyManifest, transport, *, scope_manifest: PolicyManifest | None = None,
                      native_products=None):
    """global/Product/API/全operationと参照fragment/backend/NamedValueを実読取する。

    初回互換性範囲はコード生成元の既知Productだけ。購読必須Productの
    native制御を保持し、専用open Productはbase-onlyに固定する。

    scope_manifestは、直前に検証済みの過去生成元を移行前に実読取りする場合だけ使う。
    非policy構成が同じ現行候補で固定配置を検査し、過去のXML・参照先・ETagは
    manifestの期待値との完全照合を保つ。過去XMLを現行XMLへ読み替えない。
    """
    apim = manifest.apim_id
    if not isinstance(apim, str) or not apim.startswith("/subscriptions/"):
        raise ValueError("A canonical APIM resource ID is required")
    if not manifest.operations or set(manifest.operations) != set(manifest.products) or set(manifest.operations) != set(manifest.api_properties):
        raise ValueError("The complete target API manifest is required")
    _validate_manifest_scope(manifest, scope_manifest)
    snapshot, visited = {}, set()

    def dependency(kind, name, expected):
        name = _identifier(name)
        resource_id = apim + "/" + kind + "/" + name
        if resource_id not in expected:
            raise ValueError("A referenced backend or NamedValue has no generated expectation")
        if resource_id not in snapshot:
            body = _read(transport, resource_id, snapshot)
            if not _resource_properties_match(resource_id, _properties({"body": body}), expected[resource_id]):
                raise ValueError("Referenced backend or NamedValue readback differs from the generated source")
            if kind == "backends" and body.get("properties", {}).get("credentials"):
                raise ValueError("Backends must not contain unrepresented credentials")
            if kind == "namedValues" and expected[resource_id].get("keyVault"):
                kv = body.get("properties", {}).get("keyVault", {})
                if kv.get("identityClientId") is not None or kv.get("lastStatus", {}).get("code", "").lower() != "success":
                    raise ValueError("Backend secret references must resolve through the provisioned APIM system identity")

    def policy(resource_id, stack=()):
        if resource_id in stack:
            raise ValueError("Policy fragment graph contains a cycle")
        if resource_id in visited:
            return
        expected = manifest.policies.get(resource_id)
        if expected is None:
            raise ValueError("Every applicable policy must have a generated expectation")
        fragment = "/policyFragments/" in resource_id
        inherited = "/apis/" in resource_id
        body = _read(transport, resource_id, snapshot, raw=True)
        value = body.get("properties", {}).get("value")
        _same_xml(value, expected, fragment=fragment, inherited=inherited)
        root = _xml(value, fragment=fragment, inherited=inherited, previous_prm_guard=inherited)
        for node in root.iter():
            if node.tag == "include-fragment":
                policy(apim + "/policyFragments/" + _identifier(node.get("fragment-id")), (*stack, resource_id))
            if node.tag == "set-backend-service":
                dependency("backends", node.get("backend-id"), manifest.backends)
        for name in set(re.findall(r"\{\{([^{}]+)\}\}", ET.tostring(root, encoding="unicode"))):
            dependency("namedValues", name, manifest.named_values)
        visited.add(resource_id)

    policy(apim + "/policies/policy")
    for api_id, operation_names in manifest.operations.items():
        if not api_id.startswith(apim + "/apis/") or "/" in api_id[len(apim + "/apis/"):]:
            raise ValueError("Target APIs must belong to the selected APIM")
        _identifier(api_id.rsplit("/", 1)[-1])
        if (manifest.source_version == 1 and not operation_names) or len(set(operation_names)) != len(operation_names):
            raise ValueError("The complete operation manifest must contain unique operations")
        expected_operations = {api_id + "/operations/" + _identifier(name) for name in operation_names}
        api = _read(transport, api_id, snapshot)
        properties = api.get("properties", {})
        api_type = properties.get("type", properties.get("apiType", "http"))
        expected_api = manifest.api_properties[api_id]
        if api_type != expected_api.get("type") or api_type not in ("http", "websocket", "grpc", "mcp"):
            raise ValueError("The API protocol differs from the generated source")
        if manifest.source_version == 1 and api_type != "http":
            raise ValueError("The legacy source represents reviewed HTTP model APIs only")
        key_names = properties.get("subscriptionKeyParameterNames", {})
        if properties.get("subscriptionRequired") is not True:
            raise ValueError("Native API subscription requirement must remain true")
        # The latest GET is used. An omitted or null default means disabled;
        # Bicep's current type has no bearer field, so never silently write it.
        if key_names.get("bearer") not in (None, "disabled"):
            raise ValueError("Native Authorization subscription-key parsing must be disabled")
        required_api = {"type", "path", "subscriptionRequired", "subscriptionKeyParameterNames"}
        if (not required_api <= set(expected_api)
                or (manifest.source_version == 1 and set(expected_api) != required_api)
                or expected_api.get("subscriptionRequired") is not True
                or set(expected_api.get("subscriptionKeyParameterNames", {})) != {"header", "query"}):
            raise ValueError("Complete generated API routing/key properties are required")
        if manifest.source_version == 2:
            required_api.add("protocols")
            allowed_extra = {"serviceUrl", "format", "value"} if api_type == "grpc" else {"serviceUrl"} if api_type == "websocket" else {"backendId", "mcpProperties"} if api_type == "mcp" else set()
            if not required_api <= set(expected_api) or set(expected_api) - required_api - allowed_extra:
                raise ValueError("The complete protocol-specific API routing source is required")
            if (api_type in ("grpc", "websocket") and not allowed_extra <= set(expected_api)
                    or api_type == "mcp" and ("backendId" in expected_api) != ("mcpProperties" in expected_api)):
                raise ValueError("Imported and proxy API routing must have complete generated source")
            protocols = ["ws", "wss"] if api_type == "websocket" else ["https"] if api_type == "grpc" else ["http", "https"]
            if expected_api["protocols"] != protocols or (api_type == "http" and not operation_names):
                raise ValueError("The generated protocol and operation inventory must be complete")
        # Current REST omits the default HTTP type in some API readbacks; the
        # explicit non-HTTP rejection above still applies before routing checks.
        routing = {name: value for name, value in expected_api.items() if name != "type"}
        if not _contains(properties, routing):
            raise ValueError("API routing/key properties differ from the generated source")
        if manifest.source_version == 2:
            if api_type == "websocket" and operation_names != ("onHandshake",):
                raise ValueError("WebSocket source must represent its system handshake only")
            for kind in ("tools", "schemas"):
                expected_children = {resource_id: expected for resource_id, expected in manifest.api_children.items()
                                     if resource_id.startswith(api_id + "/" + kind + "/")}
                children = _read(transport, api_id + "/" + kind, snapshot, collection=True)
                observed_ids = [item.get("id") if isinstance(item, dict) else None for item in children["value"]]
                if len(observed_ids) != len(expected_children) or set(observed_ids) != set(expected_children):
                    raise ValueError("Every API tool/schema must match the generated source inventory")
                for resource_id, expected in expected_children.items():
                    child = _read(transport, resource_id, snapshot)
                    if not _contains(_properties({"body": child}), expected):
                        raise ValueError("API tool/schema differs from the generated source")
                    if kind == "tools":
                        operation_id = expected.get("operationId")
                        if not isinstance(operation_id, str) or operation_id not in {
                                target + "/operations/" + name for target, names in manifest.operations.items() for name in names}:
                            raise ValueError("Native MCP tools must reference a represented REST operation")
        revisions = _read(transport, api_id + "/revisions", snapshot, collection=True)
        # Source publication currently owns revision 1 only. Discover every
        # revision and refuse unrepresented history rather than infer its policy.
        revision = revisions["value"][0] if len(revisions["value"]) == 1 else None
        if (not isinstance(revision, dict) or revision.get("apiRevision") != "1"
                or revision.get("apiId") != "/apis/" + api_id.rsplit("/", 1)[-1] + ";rev=1"
                or revision.get("isCurrent") is not True or revision.get("isOnline") is not True):
            raise ValueError("Every target API revision must have a reviewed source manifest; only revision 1 is represented")
        products = _read(transport, api_id + "/products", snapshot, collection=True)
        allowed_products = {apim + "/products/" + _identifier(name) for name in manifest.products[api_id]}
        open_product_id = apim + "/products/" + ENTRA_OPEN_PRODUCT_ID
        allowed_products.add(open_product_id)
        # API Product inventories may return API-owned association IDs. Resolve
        # only exact aliases of known Products before reading their real policy.
        product_aliases = {product_id: product_id for product_id in allowed_products}
        product_aliases.update({api_id + "/products/" + product_id.rsplit("/", 1)[-1]: product_id
                                for product_id in allowed_products})
        seen = set()
        for item in products["value"]:
            observed_id = item.get("id") if isinstance(item, dict) else None
            product_id = product_aliases.get(observed_id) if isinstance(observed_id, str) else None
            if product_id is None or product_id in seen:
                raise ValueError("Initial activation permits only generated products")
            seen.add(product_id)
            product = _read(transport, product_id, snapshot)
            if product_id == open_product_id:
                if not _owned_open_product_properties(product.get("properties", {})):
                    raise ValueError("The dedicated open Product ownership/properties differ")
            elif product.get("properties", {}).get("subscriptionRequired") is not True:
                raise ValueError("An existing open Product conflicts with Entra activation")
            product_policy = _read(transport, product_id + "/policies/policy", snapshot, raw=True, missing=True)
            if product_id == open_product_id:
                if product_policy is None:
                    raise ValueError("The dedicated open Product must have its fixed policy")
                _same_xml(product_policy.get("properties", {}).get("value"), OPEN_PRODUCT_POLICY)
            elif product_policy is not None:
                value = product_policy.get("properties", {}).get("value")
                root = _xml(value)
                inbound = root.find("inbound")
                if not len(inbound) or inbound[0].tag != "base":
                    raise ValueError("Existing Product controls must inherit global authentication first")
                for node in root.iter():
                    if node.tag == "include-fragment":
                        policy(apim + "/policyFragments/" + _identifier(node.get("fragment-id")))
                    if node.tag == "set-backend-service":
                        dependency("backends", node.get("backend-id"), manifest.backends)
                for name in set(re.findall(r"\{\{([^{}]+)\}\}", ET.tostring(root, encoding="unicode"))):
                    dependency("namedValues", name, manifest.named_values)
        if seen - {open_product_id} != allowed_products - {open_product_id}:
            raise ValueError("All generated native Product memberships must remain present")
        operations = _read(transport, api_id + "/operations", snapshot, collection=True)
        actual_operations = [item.get("id") if isinstance(item, dict) else None for item in operations["value"]]
        if len(actual_operations) != len(expected_operations) or set(actual_operations) != expected_operations:
            raise ValueError("Every operation must be inventoried and match the generated source")
        if api_type == "websocket":
            if _read(transport, api_id + "/policies/policy", snapshot, raw=True, missing=True) is not None:
                raise ValueError("WebSocket authentication must remain on the system handshake")
        else:
            policy(api_id + "/policies/policy")
        for operation_id in sorted(expected_operations):
            if manifest.source_version == 2 and api_type != "websocket":
                expected = manifest.api_children.get(operation_id)
                if expected is None:
                    raise ValueError("Every operation routing contract must have generated source")
                operation = _read(transport, operation_id, snapshot)
                if not _operation_routing_matches(_properties({"body": operation}), expected):
                    raise ValueError("Operation routing contract differs from the generated source")
            policy(operation_id + "/policies/policy")
    if native_products is not None:
        _verify_native_products(snapshot, manifest, native_products)
    return snapshot


def verify_snapshot(snapshot, transport):
    """mutationの直前/直後に再読取し、XML/参照/集合/ETagの変化を拒否する。"""
    def verify(entry):
        path, headers, expected = entry
        observed = transport.request("GET", path, headers=headers)
        if _signature(observed, resource_id=path) != _signature(expected, resource_id=path):
            raise ValueError("APIM prerequisites drifted after preflight; no stale overwrite is allowed")
    entries = tuple(snapshot.values())
    if getattr(transport, "supports_concurrent_reads", False) is True and len(entries) > 1:
        # Only independent readbacks are parallel. The pool is fully joined
        # before returning or propagating an error, so no later write can start
        # while a snapshot check is still pending. All original checks remain.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(verify, entry) for entry in entries]
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise errors[0]
    else:
        for entry in entries:
            verify(entry)


def read_new_api_absence(apim_id, api_ids, transport):
    """Prove selected additions are absent, including non-current revisions.

    This does not recover an existing API with missing source. The caller must
    keep source-covered existing APIs in the complete policy-graph preflight.
    """
    snapshot, names = {}, set()
    prefix = apim_id + "/apis/"
    for api_id in sorted(api_ids):
        if not isinstance(api_id, str) or not api_id.casefold().startswith(prefix.casefold()):
            raise ValueError("A new API must have a canonical selected resource ID")
        names.add(_identifier(api_id[len(prefix):]).casefold())
        if _read(transport, api_id, snapshot, missing=True) is not None:
            raise ValueError("An existing API has no protected source; explicit closed migration is required")
    if not names:
        return snapshot
    inventory = _read(transport, apim_id + "/apis", snapshot, collection=True)["value"]
    seen = set()
    for item in inventory:
        resource_id = item.get("id") if isinstance(item, dict) else None
        if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(prefix.casefold())
                or resource_id.casefold() in seen):
            raise ValueError("The API inventory must contain unique canonical APIM resource IDs")
        seen.add(resource_id.casefold())
        name = resource_id[len(prefix):].casefold().split(";rev=", 1)[0]
        _identifier(name)
        if name in names:
            raise ValueError("An existing API/current revision cannot be treated as a new addition")
    return snapshot


def read_stage_shared_resources(manifest, plan, template, transport):
    """Check every staged fragment/backend/NamedValue before it can be overwritten.

    Existing resources require exact protected source; unknown resources require
    GET and complete-inventory absence. ARM uniqueString is not guessed: when
    operation backends are staged, every existing op backend in that reserved
    namespace must have protected source.
    """
    apim, snapshot = manifest.apim_id, {}
    definitions = plan['parameters']['apiDefinitions']['value']
    targets = {'policyFragments': set(), 'backends': set(), 'namedValues': set()}
    resources = template['resources']
    resources = resources.values() if isinstance(resources, dict) else resources
    for resource in resources:
        if resource.get('type') != 'Microsoft.ApiManagement/service/policyFragments':
            continue
        name = resource.get('name')
        names = re.findall(r"'(core-[A-Za-z0-9._-]+)'", name) if isinstance(name, str) else []
        if isinstance(name, str) and IDENTIFIER.fullmatch(name):
            names = [name]
        if len(names) != 1:
            raise ValueError('Every staged shared fragment must have a static canonical name')
        targets['policyFragments'].add(apim + '/policyFragments/' + _identifier(names[0]))
    operation_backends = False
    for api in definitions:
        name = _identifier(api['name'])
        targets['backends'].add(apim + '/backends/' + name + '-backend')
        targets['namedValues'].add(apim + '/namedValues/' + name + '-backend-audience')
        if api.get('backendAuthentication', {}).get('mode') == 'keyVaultHeader':
            targets['namedValues'].add(apim + '/namedValues/' + name + '-backend-secret')
        operation_backends |= any('backendOverride' in operation for operation in api.get('operations', []))
    expected = {'policyFragments': manifest.policies, 'backends': manifest.backends, 'namedValues': manifest.named_values}
    for kind in targets:
        expected_aliases = {}
        for resource_id, properties in expected[kind].items():
            alias = resource_id.casefold()
            if alias in expected_aliases:
                raise ValueError('Protected source must not contain case aliases of one shared resource')
            expected_aliases[alias] = (resource_id, properties)
        prefix = apim + '/' + kind + '/'
        rows = _read(transport, apim + '/' + kind, snapshot, collection=True)['value']
        inventory = set()
        for item in rows:
            resource_id = item.get('id') if isinstance(item, dict) else None
            if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(prefix.casefold())
                    or resource_id.casefold() in inventory):
                raise ValueError('Shared resource inventory must have unique canonical APIM IDs')
            name = _identifier(resource_id[len(prefix):])
            inventory.add(resource_id.casefold())
            if (operation_backends and kind in ('backends', 'namedValues')
                    and OPERATION_BACKEND_IDENTIFIER.fullmatch(name)):
                targets[kind].add(resource_id)
        canonical_targets = {resource_id.casefold(): expected_aliases.get(resource_id.casefold(), (resource_id, None))
                             for resource_id in targets[kind]}
        for alias, (resource_id, source) in sorted(canonical_targets.items()):
            body = _read(transport, resource_id, snapshot, raw=kind == 'policyFragments', missing=True)
            if body is None:
                if source is not None or alias in inventory:
                    raise ValueError('Staged resource absence disagrees with protected source or inventory')
                continue
            if source is None or alias not in inventory:
                raise ValueError('An existing staged shared resource has no complete protected source')
            properties = _properties({'body': body})
            if kind == 'policyFragments':
                _same_xml(properties.get('value'), source, fragment=True)
            elif not _resource_properties_match(resource_id, properties, source):
                raise ValueError('Staged shared resource differs from its protected source')
            if kind == 'backends' and properties.get('credentials'):
                raise ValueError('Staged backends must not contain unrepresented credentials')
            if kind == 'namedValues' and source.get('keyVault'):
                kv = properties.get('keyVault', {})
                if kv.get('identityClientId') is not None or kv.get('lastStatus', {}).get('code', '').lower() != 'success':
                    raise ValueError('Staged secret references must resolve through APIM system identity')
    return snapshot


def read_failed_closed_stage(current, attempted, transport, *, scope_manifest, plan,
                             previous_browser_source=None, attempted_browser_source=None,
                             native_products):
    """Read a source-known partial stage without trusting an observed source.

    The attempted manifest comes from the verified failed ARM stage and its
    independently generated source. Routing and native Product/backend contracts
    remain identical. Each expectation selected below is an independent old
    or attempted value; unknown contents never become generated expectations.
    Missing witnesses are permitted only for explicitly attempted creations.
    """
    _validate_manifest_scope(current, scope_manifest)
    _validate_manifest_scope(attempted, scope_manifest)
    for name in ("operations", "products", "api_properties", "api_children", "backends"):
        expected = json.dumps(getattr(current, name), sort_keys=True, allow_nan=False)
        if any(json.dumps(getattr(candidate, name), sort_keys=True, allow_nan=False) != expected
               for candidate in (attempted, scope_manifest)):
            raise ValueError("Failed-stage recovery must retain exact typed non-policy source contracts")
    if current.source_version != 2 or set(current.operations) != {
            resource_id for resource_id in plan["publishedApiIds"]
            if not resource_id.endswith("/credential-manager-browser")}:
        raise ValueError("Failed-stage recovery must retain every existing protected API")
    apim, snapshot = current.apim_id, {}
    global_id = apim + "/policies/policy"
    _same_xml(attempted.policies.get(global_id), current.policies.get(global_id))
    policies, named_values = {}, {}

    def check_identity(body, resource_id, *, raw=False):
        if body is not None and not (raw and "id" not in body):
            observed = body.get("id")
            if not isinstance(observed, str) or observed.casefold() != resource_id.casefold():
                raise ValueError("Failed-stage readback must identify the requested known resource")

    def known_policy(resource_id, old, new):
        body = _read(transport, resource_id, snapshot, raw=True, missing=old is None)
        check_identity(body, resource_id, raw=True)
        if body is None:
            if old is not None or new is None:
                raise ValueError("A prior known policy cannot disappear during failed-stage recovery")
            return None
        actual = body.get("properties", {}).get("value")
        for expected in (old, new):
            if expected is None:
                continue
            try:
                _same_xml(actual, expected, fragment="/policyFragments/" in resource_id,
                          inherited=resource_id.startswith(apim + "/apis/"))
                return expected
            except ValueError:
                pass
        raise ValueError("Failed-stage policy differs from both recorded old and attempted values")

    for resource_id in sorted(set(current.policies) | set(attempted.policies)):
        old, new = current.policies.get(resource_id), attempted.policies.get(resource_id)
        if resource_id == global_id:
            new = old  # API-only staging never authorizes a changed Global.
        chosen = known_policy(resource_id, old, new)
        if chosen is not None:
            policies[resource_id] = chosen
    for resource_id in sorted(set(current.named_values) | set(attempted.named_values)):
        old, new = current.named_values.get(resource_id), attempted.named_values.get(resource_id)
        if old is not None and new is not None and not (
                _resource_properties_match(resource_id, old, new) and _resource_properties_match(resource_id, new, old)):
            raise ValueError("Failed-stage recovery cannot change existing NamedValue contracts")
        body = _read(transport, resource_id, snapshot, missing=old is None)
        check_identity(body, resource_id)
        if body is None:
            if old is not None or new is None:
                raise ValueError("A prior known NamedValue cannot disappear during failed-stage recovery")
            continue
        expected = old if old is not None else new
        if not _resource_properties_match(resource_id, _properties({"body": body}), expected):
            raise ValueError("Failed-stage NamedValue differs from its recorded contract")
        named_values[resource_id] = expected
    for resource_id, expected in sorted(current.backends.items()):
        body = _read(transport, resource_id, snapshot)
        check_identity(body, resource_id)
        properties = _properties({"body": body})
        if not _resource_properties_match(resource_id, properties, expected) or properties.get("credentials"):
            raise ValueError("Failed-stage backend differs from its unchanged recorded contract")
    mixed = replace(current, policies=policies, named_values=named_values)
    snapshot.update(read_policy_graph(mixed, transport, scope_manifest=scope_manifest,
                                     native_products=native_products))
    product_snapshot, members = read_open_product(apim, transport, allowed_api_ids=(), missing=False)
    if members:
        raise ValueError("Failed-stage recovery requires the employee Product to remain empty")
    snapshot.update(product_snapshot)
    if plan.get("credentialLogin"):
        old, new = previous_browser_source, attempted_browser_source
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError("Failed-stage recovery requires both recorded browser sources")
        if {key: value for key, value in old.items() if key != "policies"} != {
                key: value for key, value in new.items() if key != "policies"}:
            raise ValueError("Failed-stage recovery cannot change browser routing or metadata")
        def browser_policies(source):
            entries = source.get("policies")
            if not isinstance(entries, list) or any(not isinstance(entry, dict)
                    or set(entry) != {"suffix", "value"} for entry in entries):
                raise ValueError("Recorded browser policies must be complete")
            result = {entry["suffix"]: entry["value"] for entry in entries}
            if len(result) != len(entries):
                raise ValueError("Recorded browser policies must not contain duplicates")
            return result
        old_policies, new_policies = browser_policies(old), browser_policies(new)
        allowed = {"/apis/credential-manager-browser/operations/" + name + "/policies/policy"
                   for name in ("launch", "launch-post", "identity-callback", "callback")}
        if set(old_policies) != allowed or set(new_policies) != allowed:
            raise ValueError("Recorded browser policies must retain the exact transaction operation set")
        browser = copy.deepcopy(new)
        browser["policies"] = [{"suffix": suffix, "value": known_policy(apim + suffix, old_policies[suffix], new_policies[suffix])}
                              for suffix in sorted(allowed)]
        snapshot.update(verify_browser_publication_source(plan, browser, transport))
    elif previous_browser_source not in (None, {}) or attempted_browser_source not in (None, {}):
        raise ValueError("Disabled browser recovery cannot include recorded browser resources")
    for resource_id, (path, _, response) in snapshot.items():
        body = response.get("body")
        if isinstance(body, dict) and "properties" in body:
            check_identity(body, resource_id, raw="&format=rawxml" in path)
    verify_snapshot(snapshot, transport)
    return mixed, snapshot


def read_fresh_publication(plan, transport):
    """初回sourceがない場合だけ、閉じた新規stageの衝突と全体policyを読む。

    対象API（非current revision含む）とstage所有子資源は未作成が条件。
    不足sourceのある既存環境を初回と誤認せず、共有fragment/backendの衝突を拒否する。
    全collectionは完全取得を要求し、読取snapshotを最初のwrite直前に再確認する。
    """
    apim, snapshot = plan["apimId"], {}
    targets = set(plan["publishedApiIds"])
    definitions = plan["parameters"]["apiDefinitions"]["value"]
    target_names = {resource_id[len(apim + "/apis/"):].casefold() for resource_id in targets}
    for resource_id in targets:
        if _read(transport, resource_id, snapshot, missing=True) is not None:
            raise ValueError("A selected API already exists; a protected source is required before activation")
    inventories = {}
    for kind in ("apis", "policyFragments", "backends", "products", "namedValues", "loggers", "diagnostics"):
        inventories[kind] = _read(transport, apim + "/" + kind, snapshot, collection=True)["value"]
        prefix, seen = apim + "/" + kind + "/", set()
        for item in inventories[kind]:
            resource_id = item.get("id") if isinstance(item, dict) else None
            if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(prefix.casefold())
                    or resource_id.casefold() in seen):
                raise ValueError("The APIM child inventory must contain unique canonical resource IDs")
            seen.add(resource_id.casefold())
            name = resource_id[len(prefix):].casefold()
            _identifier(name.split(";rev=", 1)[0] if kind == "apis" else name)
    for item in inventories["apis"]:
        resource_id = item["id"]
        if resource_id[len(apim + "/apis/"):].casefold().split(";rev=", 1)[0] in target_names:
            raise ValueError("An existing API revision cannot be treated as a fresh publication")
    if inventories["policyFragments"]:
        raise ValueError("Existing shared fragments require their protected source before initial activation")
    allowed_backend = apim + "/backends/credential-broker" if plan.get("credentialLogin") else None
    for item in inventories["backends"]:
        if allowed_backend is None or item["id"].casefold() != allowed_backend.casefold():
            raise ValueError("Existing shared backends require their protected source before initial activation")
        _read(transport, allowed_backend, snapshot)
    product_names = {api["productId"] for api in definitions} | {ENTRA_OPEN_PRODUCT_ID, "released-apis"}
    named_names = {api["name"] + suffix for api in definitions for suffix in ("-backend-audience", "-backend-secret")}
    for kind, names in (("products", product_names),
                        ("namedValues", named_names), ("loggers", {"azuremonitor"}), ("diagnostics", {"azuremonitor"})):
        names = {name.casefold() for name in names}
        for item in inventories[kind]:
            resource_id = item["id"]
            prefix = apim + "/" + kind + "/"
            name = resource_id[len(prefix):].casefold()
            if name in names or (kind == "namedValues" and OPERATION_BACKEND_IDENTIFIER.fullmatch(name)):
                raise ValueError("A stage-owned APIM child already exists; the matching saved publication source is required")
    global_id = apim + "/policies/policy"
    global_policy = _read(transport, global_id, snapshot, raw=True, missing=True)
    value = global_policy.get("properties", {}).get("value") if global_policy is not None else None
    if global_policy is None or is_closed_initial_policy(value):
        if inventories["apis"]:
            echo_id = apim + "/apis/" + SAMPLE_API
            if [item["id"].casefold() for item in inventories["apis"]] != [echo_id.casefold()]:
                raise ValueError("The initial global baseline requires an empty API inventory or the default Echo API")
            echo = _read(transport, echo_id, snapshot)
            observed_id = echo.get("id")
            if (not isinstance(observed_id, str) or observed_id.casefold() != echo_id.casefold()
                    or not is_default_echo_api(echo)):
                raise ValueError("A customized or different Echo API cannot be treated as a default sample")
            revisions = _read(transport, echo_id + "/revisions", snapshot, collection=True)["value"]
            revision = revisions[0] if len(revisions) == 1 else None
            if (not isinstance(revision, dict) or revision.get("apiId") != "/apis/" + SAMPLE_API + ";rev=1"
                    or revision.get("apiRevision") != "1" or revision.get("isCurrent") is not True):
                raise ValueError("The default Echo API must have only its initial current revision")
    else:
        root = _xml(value)
        if any(root.iter("include-fragment")):
            raise ValueError("Existing global fragments need a protected source before fresh staging")
        baseline = PolicyManifest(apim, {global_id: value}, {}, {}, {}, {})
        baseline = complete_named_value_expectations(baseline, plan, transport)
        for resources, kind in ((baseline.named_values, "NamedValue"), (baseline.backends, "backend")):
            for resource_id, expected in resources.items():
                body = _read(transport, resource_id, snapshot)
                if not _resource_properties_match(resource_id, _properties({"body": body}), expected):
                    raise ValueError("The existing global " + kind + " differs from the approved infrastructure settings")
    return snapshot


def _wait(transport, resource_id, *, sleep, deadline, raw=False):
    path = resource_id + "?api-version=" + APIM_API + ("&format=rawxml" if raw else "")
    while time.monotonic() < deadline:
        response = transport.request("GET", path, headers={"Accept": "application/json"} if raw else None)
        body = require_success(response, "Verify Entra publication write")
        state = body.get("properties", {}).get("provisioningState")
        if state in ("Failed", "Canceled", "Cancelled"):
            raise DeploymentError("APIM transition update failed")
        if state not in PENDING:
            _etag(response)
            return response
        sleep(1)
    raise DeploymentError("APIM transition update is still pending")


def _source_manifest(source, apim_id, api_names, global_policy, *, named_values=None):
    """保護生成元の構造を解析する。固定配置の検査前には適用・読戻しの根拠にしない。"""
    version = source.get("schemaVersion") if isinstance(source, dict) else None
    required = {"schemaVersion", "apis", "policies", "backends", "namedValues"}
    if version == 2:
        required.add("apiChildren")
    if type(version) is not int or version not in (1, 2) or set(source) != required:
        raise ValueError("The protected generated source is missing; reconcile the closed publication before activation")
    names = {_identifier(name) for name in api_names}
    if not names:
        raise ValueError("A source manifest target API set is required")
    for key in ("apis", "policies", "backends", "namedValues"):
        if not isinstance(source[key], list):
            raise ValueError("The protected generated source manifest is invalid")
    api_entries = {}
    for entry in source["apis"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "operations", "products", "properties"}:
            raise ValueError("The generated API source entry is invalid")
        name = _identifier(entry["name"])
        if name in api_entries:
            raise ValueError("The generated API source contains duplicates")
        api_entries[name] = entry
    if not names <= set(api_entries):
        raise ValueError("The protected source does not represent every requested API; explicit closed migration is required")
    operations, products, api_properties = {}, {}, {}
    for name in names:
        entry, api_id = api_entries[name], apim_id + "/apis/" + name
        if not isinstance(entry["operations"], list) or not isinstance(entry["products"], list):
            raise ValueError("Generated operations and Products must be complete lists")
        operations[api_id] = tuple(_identifier(value) for value in entry["operations"])
        products[api_id] = tuple(_identifier(value) for value in entry["products"])
        api_properties[api_id] = copy.deepcopy(entry["properties"])

    def entries(key, field_name):
        result = {}
        for entry in source[key]:
            if not isinstance(entry, dict) or set(entry) != {"suffix", field_name} or not isinstance(entry["suffix"], str):
                raise ValueError("The generated resource source entry is invalid")
            suffix = entry["suffix"]
            parts = suffix.split("/")
            kind = {"policies": "policyFragments", "backends": "backends", "namedValues": "namedValues"}[key]
            allowed = (len(parts) == 3 and parts[0] == "" and parts[1] == kind)
            if key == "policies" and parts[:2] == ["", "apis"]:
                allowed = ((len(parts) == 5 and parts[3:] == ["policies", "policy"])
                           or (len(parts) == 7 and parts[3] == "operations" and parts[5:] == ["policies", "policy"]))
                if allowed and parts[2] not in names:
                    continue
            if not allowed:
                raise ValueError("Generated source resource scope is invalid")
            for part in parts[1:]:
                _identifier(part)
            resource_id = apim_id + suffix
            if resource_id in result:
                raise ValueError("Generated source resource entries must be unique")
            result[resource_id] = copy.deepcopy(entry[field_name])
        return result

    policies = entries("policies", "value")
    policies[apim_id + "/policies/policy"] = global_policy
    expected_named = entries("namedValues", "properties")
    for resource_id, expected in (named_values or {}).items():
        if resource_id in expected_named and not _resource_properties_match(resource_id, expected_named[resource_id], expected):
            raise ValueError("Independent foundation NamedValue expectations conflict with API source")
        expected_named[resource_id] = copy.deepcopy(expected)
    children = {}
    if version == 2:
        if not isinstance(source["apiChildren"], list):
            raise ValueError("The generated API child source must be a complete list")
        for entry in source["apiChildren"]:
            if not isinstance(entry, dict) or set(entry) != {"suffix", "properties"} or not isinstance(entry["suffix"], str):
                raise ValueError("The generated API child source is invalid")
            parts = entry["suffix"].split("/")
            if len(parts) != 5 or parts[:2] != ["", "apis"] or parts[3] not in ("operations", "schemas", "tools"):
                raise ValueError("Generated API children must remain within their API")
            for part in parts[1:]:
                _identifier(part)
            if parts[2] not in names:
                continue
            resource_id = apim_id + entry["suffix"]
            if resource_id in children or not isinstance(entry["properties"], dict):
                raise ValueError("The generated API child source must be unique and complete")
            children[resource_id] = copy.deepcopy(entry["properties"])
    return PolicyManifest(apim_id, policies, operations, products, entries("backends", "properties"), expected_named,
                          api_properties, children, version)


def source_manifest(source, apim_id, api_names, global_policy, *, named_values=None, scope_manifest=None):
    """生成元の構造と現行固定配置を検査する。過去XMLの期待値はそのまま保持する。"""
    manifest = _source_manifest(source, apim_id, api_names, global_policy, named_values=named_values)
    _validate_manifest_scope(manifest, scope_manifest)
    return manifest


def merge_sources(previous, current):
    """今回指定されない旧対象APIの生成元を保持し、共通fragmentは新しい値で照合する。"""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ValueError("Both protected publication source manifests are required")
    version = max(previous.get("schemaVersion", 0), current.get("schemaVersion", 0))
    if previous.get("schemaVersion") not in (1, 2) or current.get("schemaVersion") not in (1, 2):
        raise ValueError("The protected publication source manifest is invalid")
    result = {"schemaVersion": version}
    for key in ("apis", "policies", "backends", "namedValues", *(("apiChildren",) if version == 2 else ())):
        identity = "name" if key == "apis" else "suffix"
        combined = {}
        for source in (previous, current):
            values = source.get(key, []) if key == "apiChildren" and source.get("schemaVersion") == 1 else source.get(key)
            if not isinstance(values, list):
                raise ValueError("The protected publication source manifest is invalid")
            for entry in values:
                if not isinstance(entry, dict) or identity not in entry:
                    raise ValueError("The protected source entry is invalid")
                combined[entry[identity]] = copy.deepcopy(entry)
        result[key] = list(combined.values())
    return result


def complete_named_value_expectations(manifest, plan, transport):
    """生成元が参照する既知の接続だけを、配備済みのメタデータで補完する。

    Portalが所有するprofile接続は型/URL/Key Vault参照を検査し、その値を保持する。
    secret本体のlistValueは呼ばない。未知の任意NamedValueを信頼しない。
    """
    apim_id = manifest.apim_id
    queue = [apim_id + "/policies/policy"]
    queue += [api_id + "/policies/policy" for api_id in manifest.operations
              if manifest.api_properties[api_id].get("type") != "websocket"]
    queue += [api_id + "/operations/" + name + "/policies/policy" for api_id, names in manifest.operations.items() for name in names]
    seen, references, backend_references = set(), set(), set()
    while queue:
        resource_id = queue.pop()
        if resource_id in seen:
            continue
        seen.add(resource_id)
        value = manifest.policies.get(resource_id)
        if value is None:
            raise ValueError("A referenced policy has no generated source")
        root = _xml(value, fragment="/policyFragments/" in resource_id)
        references.update(re.findall(r"\{\{([^{}]+)\}\}", ET.tostring(root, encoding="unicode")))
        backend_references.update(node.get("backend-id") for node in root.iter("set-backend-service"))
        queue += [apim_id + "/policyFragments/" + _identifier(node.get("fragment-id")) for node in root.iter("include-fragment")]
    expected = copy.deepcopy(manifest.named_values)
    # The trusted logical checkpoint proves the previous global structure.
    # The current configuration independently declares the foundation values
    # already prepared by settings deployment, including intentional updates.
    entra_values = plan.get("entraNamedValues") or plan.get("previousEntraNamedValues", {})
    if references.intersection(ENTRA_NAMED_VALUES):
        normalized = validate_entra_named_values(entra_values)
        for name, value in normalized.items():
            resource_id = apim_id + "/namedValues/" + name
            properties = {"displayName": name, "secret": False, "value": value}
            if resource_id in expected and not _resource_properties_match(resource_id, expected[resource_id], properties):
                raise ValueError("Protected API source must preserve foundation Entra NamedValues")
            expected[resource_id] = properties
    vault = "https://" + plan["parameters"]["names"]["value"]["keyVault"] + ".vault.azure.net/secrets/"
    secret_names = {"core-l7-ingress-secret": "core-l7-ingress-secret", "core-realtime-ticket-key": "core-realtime-ticket-key",
                    "core-credential-broker-secret": "core-credential-broker-key"}
    for name in sorted(references):
        _identifier(name)
        resource_id = apim_id + "/namedValues/" + name
        if resource_id in expected:
            continue
        if name not in {*secret_names, "core-subscription-key-header", "core-l7-ingress-enabled", "core-credential-broker-url"}:
            raise ValueError("A NamedValue reference has no generated or approved connection settings")
        body = _read(transport, resource_id, {})
        properties = body.get("properties", {})
        if properties.get("displayName") != name:
            raise ValueError("Connection NamedValue ownership differs")
        l7_enabled = bool(plan["parameters"]["l7IngressSecretIdentifier"]["value"])
        if name == "core-l7-ingress-secret" and not l7_enabled:
            if properties.get("secret") is not False or properties.get("keyVault") or properties.get("value") != "ingress-disabled":
                raise ValueError("Disabled L7 ingress must keep its nonsecret disabled placeholder")
            expected[resource_id] = {"displayName": name, "secret": False, "value": "ingress-disabled"}
        elif name in secret_names:
            if properties.get("secret") is not True:
                raise ValueError("Connection credentials must remain secret NamedValues")
            kv = properties.get("keyVault")
            if (not isinstance(kv, dict) or kv.get("secretIdentifier") != vault + secret_names[name]
                    or kv.get("identityClientId") is not None or kv.get("lastStatus", {}).get("code", "").lower() != "success"):
                raise ValueError("Connection Key Vault references must match the provisioned system identity")
            expected[resource_id] = {"displayName": name, "secret": True, "keyVault": {"secretIdentifier": vault + secret_names[name]}}
        else:
            value = properties.get("value")
            if properties.get("secret") is not False or properties.get("keyVault") or not isinstance(value, str):
                raise ValueError("Public connection settings are invalid")
            if name == "core-subscription-key-header":
                if value != plan["commonAuthentication"][name]:
                    raise ValueError("The key header differs from the deployed configuration")
            elif name == "core-l7-ingress-enabled":
                if value != ("true" if l7_enabled else "false"):
                    raise ValueError("L7 ingress activation differs from the prepared foundation")
            else:
                parsed = urlsplit(value)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
                    raise ValueError("Connection endpoints must be fixed credential-free HTTPS URLs")
                app_name = "ca-" + plan["resourceStem"] + "-credentials"
                app_id = apim_id.split("/providers/", 1)[0] + "/providers/Microsoft.App/containerApps/" + app_name
                application = require_success(transport.request("GET", app_id + "?api-version=2025-01-01"),
                                              "Read owned Credential Broker application")
                if not isinstance(application, dict) or str(application.get("id", "")).lower() != app_id.lower():
                    raise ValueError("The Credential Broker must belong to the selected service group")
                environment_id = app_id.split("/containerApps/", 1)[0] + "/managedEnvironments/cae-" + plan["resourceStem"] + "-broker"
                ingress = broker_https_ingress(application.get("properties", {}), environment_id)
                if not ingress:
                    raise ValueError("The Credential Broker must use its dedicated private environment and HTTPS ingress")
                fqdn = ingress.get("fqdn")
                if (not isinstance(fqdn, str) or not re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", fqdn)
                        or not fqdn.startswith(app_name + ".") or parsed.hostname != fqdn
                        or parsed.path or parsed.port not in (None, 443)):
                    raise ValueError("Connection endpoints must belong to the owned private application and fixed path")
            expected[resource_id] = {"displayName": name, "secret": False, "value": value}
    expected_backends = copy.deepcopy(manifest.backends)
    for name in sorted(backend_references):
        _identifier(name)
        resource_id = apim_id + "/backends/" + name
        if resource_id in expected_backends:
            continue
        if name != "credential-broker" or not plan.get("credentialLogin"):
            raise ValueError("A backend reference has no generated or approved connection settings")
        endpoint = expected.get(apim_id + "/namedValues/core-credential-broker-url", {}).get("value")
        if not endpoint:
            raise ValueError("The Credential Broker backend must bind the verified private application")
        backend = _read(transport, resource_id, {})
        properties = backend.get("properties", {})
        if properties.get("protocol") != "http" or properties.get("url") != endpoint or properties.get("credentials"):
            raise ValueError("The Credential Broker backend differs from the verified private connection")
        tls = properties.get("tls", {})
        if (set(tls) - {"validateCertificateChain", "validateCertificateName"}
                or any(value is not True for value in tls.values())):
            raise ValueError("The Credential Broker backend must preserve TLS validation")
        expected_backends[resource_id] = {"protocol": "http", "url": endpoint}
        if tls:
            expected_backends[resource_id]["tls"] = copy.deepcopy(tls)
    return PolicyManifest(manifest.apim_id, manifest.policies, manifest.operations, manifest.products,
                          expected_backends, expected, manifest.api_properties, manifest.api_children, manifest.source_version)


def read_open_product(apim_id, transport, *, allowed_api_ids, missing=True):
    """未所属でも固定IDを読む。所有衝突・独自policy・対象外/旧revの所属を拒否する。"""
    product_id, snapshot = apim_id + "/products/" + ENTRA_OPEN_PRODUCT_ID, {}
    product = _read(transport, product_id, snapshot, missing=missing)
    if product is None:
        return snapshot, set()
    if not _owned_open_product_properties(product.get("properties", {})):
        raise ValueError("The dedicated open Product has conflicting ownership or properties")
    policy = _read(transport, product_id + "/policies/policy", snapshot, raw=True)
    _same_xml(policy.get("properties", {}).get("value"), OPEN_PRODUCT_POLICY)
    members = _read(transport, product_id + "/apis", snapshot, collection=True)
    # Product API inventories can identify the association instead of the API.
    # Accept only the two exact representations of an already reviewed API;
    # preserve the raw collection in snapshot for concurrent-drift checks.
    aliases = {}
    api_prefix = apim_id + "/apis/"
    for api_id in allowed_api_ids:
        if not isinstance(api_id, str) or not api_id.startswith(api_prefix):
            raise ValueError("Allowed Product members must belong to the selected APIM")
        name = _identifier(api_id[len(api_prefix):])
        aliases[api_id] = api_id
        aliases[product_id + "/apis/" + name] = api_id
    api_ids = []
    for entry in members["value"]:
        observed_id = entry.get("id") if isinstance(entry, dict) else None
        canonical_id = aliases.get(observed_id) if isinstance(observed_id, str) else None
        if canonical_id is None or canonical_id in api_ids:
            raise ValueError("The dedicated open Product contains unreviewed or duplicate API/revision memberships")
        api_ids.append(canonical_id)
    return snapshot, set(api_ids)


def _membership_readset(snapshot, apim_id, api_id, manifest):
    """Keep shared resources, every API root/collection and related API details.

    Only the source-verified controller opts into this intermediate readset.
    Full graph checks still bracket closed staging/global installation and the
    final membership batch. Unrelated individual API descendants may therefore
    drift until their own precheck or the final full check, which must fail and
    close this transition's opened routes. Unknown shapes retain the full set.
    """
    if (not isinstance(manifest, PolicyManifest) or manifest.source_version != 2
            or manifest.apim_id != apim_id or not isinstance(snapshot, dict)):
        return snapshot
    try:
        api_ids = set(manifest.operations)
        if (api_id not in api_ids or api_ids != set(manifest.products)
                or api_ids != set(manifest.api_properties)):
            return snapshot
        prefix = apim_id + "/apis/"
        collections, owners, operation_owners = set(), {}, {}
        related = {target: set() for target in api_ids}
        for target, names in manifest.operations.items():
            if not target.startswith(prefix) or _identifier(target[len(prefix):]) != target[len(prefix):]:
                return snapshot
            kind = manifest.api_properties[target].get("type")
            if kind not in ("http", "websocket", "grpc", "mcp"):
                return snapshot
            collections.update(target + "/" + child for child in
                               ("operations", "products", "revisions", "schemas", "tools"))
            # A WebSocket API policy's verified absence is also an individual
            # witness; its handshake policy remains fully checked when related.
            owners[target + "/policies/policy"] = target
            for name in names:
                operation_id = target + "/operations/" + _identifier(name)
                operation_owners[operation_id] = target
                owners[operation_id + "/policies/policy"] = target
                if kind != "websocket":
                    owners[operation_id] = target
        for resource_id, properties in manifest.api_children.items():
            if resource_id in operation_owners:
                continue
            parts = resource_id[len(prefix):].split("/") if resource_id.startswith(prefix) else ()
            if (len(parts) != 3 or parts[1] not in ("tools", "schemas")
                    or _identifier(parts[2]) != parts[2]):
                return snapshot
            owner = prefix + parts[0]
            if owner not in api_ids or not isinstance(properties, dict):
                return snapshot
            owners[resource_id] = owner
            if parts[1] == "tools":
                dependency = operation_owners.get(properties.get("operationId"))
                if dependency is None:
                    return snapshot
                # A REST operation can also be exposed by a native MCP API.
                # Treat both directions and transitive references as related.
                related[owner].add(dependency)
                related[dependency].add(owner)
        # Do not narrow a partial or nonstandard captured graph.
        if not (api_ids | collections | set(owners)) <= set(snapshot):
            return snapshot
        connected, pending = set(), [api_id]
        while pending:
            target = pending.pop()
            if target not in connected:
                connected.add(target)
                pending.extend(related[target] - connected)
        result = {}
        for resource_id, entry in snapshot.items():
            if (not isinstance(resource_id, str) or not isinstance(entry, (tuple, list))
                    or len(entry) != 3 or not isinstance(entry[0], str)
                    or entry[0].split("?", 1)[0] != resource_id):
                return snapshot
            if resource_id.startswith(prefix):
                if entry[0] not in (resource_id + "?api-version=" + APIM_API,
                                    resource_id + "?api-version=" + APIM_API + "&format=rawxml"):
                    return snapshot
            elif resource_id.startswith(apim_id + "/"):
                shared_kind = resource_id[len(apim_id) + 1:].split("/", 1)[0]
                if (shared_kind not in ("policies", "apis", "policyFragments", "namedValues", "backends",
                                        "products", "loggers", "diagnostics")
                        or shared_kind == "apis" and resource_id != apim_id + "/apis"):
                    return snapshot
            elif ("/providers/microsoft.apimanagement/" in resource_id.casefold()
                  or "/apis/" in resource_id.casefold()):
                return snapshot
            if resource_id.startswith(prefix) and resource_id not in api_ids | collections:
                owner = owners.get(resource_id)
                if owner is None:
                    return snapshot
                if owner not in connected:
                    continue
            # Includes shared dependencies/collections, external prerequisites,
            # all API roots and all API collections, with original GET/headers.
            result[resource_id] = entry
        return result
    except (ValueError, TypeError, AttributeError):
        return snapshot


def _membership(apim_id, api_id, add, transport, snapshot, expected_members, *, sleep, deadline, manifest=None):
    """APIMのassociation操作には条件付きETagの保証がないため、専用所有範囲だけを扱う。

    global/API/product ETagを直前に確認し、所属集合を直後に完全照合する。
    広いassociationの置換や、未確認の並行変更を上書きする処理は行わない。
    """
    if time.monotonic() >= deadline:
        raise DeploymentError("Dedicated Product membership deadline expired before write")
    checked = _membership_readset(snapshot, apim_id, api_id, manifest)
    verify_snapshot(checked, transport)
    # 全依存の再照合中にも期限が進むため、変更要求の直前で再確認します。
    if time.monotonic() >= deadline:
        raise DeploymentError("Dedicated Product membership deadline expired before write")
    product_id = apim_id + "/products/" + ENTRA_OPEN_PRODUCT_ID
    path = product_id + "/apis/" + api_id.rsplit("/", 1)[-1] + "?api-version=" + APIM_API
    response = transport.request("PUT" if add else "DELETE", path)
    require_success(response, "Change dedicated Entra Product membership", allowed=(200, 201, 202, 204, 404) if not add else (200, 201, 202, 204))
    expected = set(expected_members)
    expected.add(api_id) if add else expected.discard(api_id)
    while time.monotonic() < deadline:
        fresh, members = read_open_product(apim_id, transport, allowed_api_ids=expected_members | {api_id}, missing=False)
        if members == expected:
            # Association changes may update the owning Product ETag, but must
            # not change its properties or policy. Refresh only these own writes.
            for resource_id, (new_path, headers, observed) in fresh.items():
                if resource_id.endswith("/apis"):
                    continue
                old = snapshot.get(resource_id)
                if old and _properties(observed) != _properties(old[2]):
                    raise ValueError("The dedicated Product changed concurrently with membership")
            unchanged = {resource_id: entry for resource_id, entry in checked.items()
                         if resource_id not in (product_id, product_id + "/apis", api_id + "/products")}
            verify_snapshot(unchanged, transport)
            return fresh, members
        if not members <= expected_members | {api_id}:
            raise ValueError("Dedicated Product membership drifted during the transition")
        sleep(1)
    raise DeploymentError("Dedicated Product membership update is still pending")


def _read_changed_api_products(manifest, api_id, add, transport):
    """Read only the changed memberships; retain the controller\'s full snapshot."""
    if api_id not in manifest.products:
        raise ValueError("Changed API membership must have generated native Product source")
    native = {manifest.apim_id + "/products/" + _identifier(name) for name in manifest.products[api_id]}
    employee = manifest.apim_id + "/products/" + ENTRA_OPEN_PRODUCT_ID
    if employee in native:
        raise ValueError("Native Product source must exclude the dedicated employee Product")
    allowed = native | {employee}
    aliases = {product_id: product_id for product_id in allowed}
    aliases.update({api_id + "/products/" + product_id.rsplit("/", 1)[-1]: product_id for product_id in allowed})
    snapshot = {}
    products = _read(transport, api_id + "/products", snapshot, collection=True)
    observed = set()
    for entry in products["value"]:
        resource_id = entry.get("id") if isinstance(entry, dict) else None
        product_id = aliases.get(resource_id) if isinstance(resource_id, str) else None
        if product_id is None or product_id in observed:
            raise ValueError("Changed API has unknown or duplicate native Product memberships")
        observed.add(product_id)
    expected = native | ({employee} if add else set())
    if observed != expected:
        raise ValueError("API Product memberships differ from the reviewed native set and employee change")
    return snapshot


def verify_browser_publication_source(plan, source, transport):
    """Verify generated transaction-authenticated browser APIs without opening them.

    These operations intentionally have no subscription requirement and do not
    belong to the employee Product or the protected business source manifest.
    Their separate current Bicep output is still checked before activation.
    """
    if not plan.get("credentialLogin"):
        if source not in (None, {}):
            raise ValueError("Disabled browser publication must not contain generated browser resources")
        return {}
    required = {"schemaVersion", "name", "properties", "apiPolicyAbsent", "operations", "policies"}
    if (not isinstance(source, dict) or set(source) != required or type(source.get("schemaVersion")) is not int
            or source["schemaVersion"] != 1 or source.get("name") != "credential-manager-browser"
            or source.get("apiPolicyAbsent") is not True):
        raise ValueError("Complete generated browser publication source is required")
    expected_api = {"type": "http", "path": "auth/connections/browser", "protocols": ["http", "https"], "subscriptionRequired": False}
    if source["properties"] != expected_api:
        raise ValueError("Browser publication must retain its independent transaction-authenticated routing")
    routes = {"launch": ("GET", "/launch"), "launch-post": ("POST", "/launch"),
              "identity-callback": ("GET", "/identity-callback"), "callback": ("GET", "/callback")}
    operations = {}
    if not isinstance(source["operations"], list) or not isinstance(source["policies"], list):
        raise ValueError("Generated browser operations and policies must be complete lists")
    for entry in source["operations"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "properties"} or entry.get("name") not in routes:
            raise ValueError("Generated browser operation metadata is invalid")
        name, properties = entry["name"], entry["properties"]
        if (name in operations or not isinstance(properties, dict)
                or set(properties) != {"displayName", "method", "urlTemplate", "responses"}
                or (properties["method"], properties["urlTemplate"]) != routes[name]
                or not isinstance(properties["displayName"], str) or properties["responses"] != []):
            raise ValueError("Generated browser operation routing is incomplete or duplicated")
        operations[name] = properties
    if set(operations) != set(routes):
        raise ValueError("Every browser transaction operation must have current generated source")
    apim, snapshot = plan["apimId"], {}
    api_id = apim + "/apis/credential-manager-browser"

    def read(resource_id, *, raw=False, missing=False, collection=False):
        body = _read(transport, resource_id, snapshot, raw=raw, missing=missing, collection=collection)
        if body is not None and not collection:
            # Native rawxml replies contain XML only and are wrapped by the ARM
            # transport. JSON replies must identify the requested resource; an
            # explicit foreign ID cannot be accepted merely because XML matches.
            if not (raw and "id" not in body):
                observed = body.get("id")
                if not isinstance(observed, str) or observed.casefold() != resource_id.casefold():
                    raise ValueError("Browser readback does not identify the requested generated resource")
        return body

    policies = {}
    for entry in source["policies"]:
        if not isinstance(entry, dict) or set(entry) != {"suffix", "value"}:
            raise ValueError("Generated browser policy metadata is invalid")
        suffix = entry["suffix"]
        allowed = {"/apis/credential-manager-browser/operations/" + name + "/policies/policy" for name in routes}
        if suffix not in allowed or suffix in policies:
            raise ValueError("Browser source must represent exactly its transaction operation policies")
        _xml(entry["value"])
        policies[suffix] = entry["value"]
    if len(policies) != len(routes):
        raise ValueError("Every browser transaction policy must have generated source")
    api = read(api_id)
    properties = api.get("properties", {})
    normalized = {**properties, "type": properties.get("type", properties.get("apiType", "http"))}
    if not _contains(normalized, expected_api):
        raise ValueError("Browser API readback differs from its generated transaction contract")
    if read(api_id + "/policies/policy", raw=True, missing=True) is not None:
        raise ValueError("Browser API-level policy must remain absent as generated")
    revision_rows = read(api_id + "/revisions", collection=True)["value"]
    revision = revision_rows[0] if len(revision_rows) == 1 else {}
    if (revision.get("apiRevision") != "1" or revision.get("apiId") != "/apis/credential-manager-browser;rev=1"
            or revision.get("isCurrent") is not True or revision.get("isOnline") is not True):
        raise ValueError("Browser API revision inventory differs from its generated source")
    for kind in ("products", "tools", "schemas"):
        if read(api_id + "/" + kind, collection=True)["value"]:
            raise ValueError("Browser APIs must not gain employee/native Product membership or unrepresented children")
    rows = read(api_id + "/operations", collection=True)["value"]
    ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
    expected_ids = {api_id + "/operations/" + name for name in routes}
    if len(ids) != len(expected_ids) or set(ids) != expected_ids:
        raise ValueError("Browser operation inventory differs from the complete generated source")
    for name, expected in operations.items():
        operation_id = api_id + "/operations/" + name
        body = read(operation_id)
        if not _contains(_properties({"body": body}), expected):
            raise ValueError("Browser operation routing readback differs from generated source")
        policy = read(operation_id + "/policies/policy", raw=True)
        _same_xml(policy.get("properties", {}).get("value"), policies["/apis/credential-manager-browser/operations/" + name + "/policies/policy"])
    verify_snapshot(snapshot, transport)
    return snapshot


def transition(current: PolicyManifest, desired_global_policy, transport, stage_closed_apis,
               *, previous_open_api_ids=(), desired_open_api_ids=(), new_api_ids=(), stage_prerequisites=None,
               preflight_scope_manifest=None, on_staged=None, on_prepared=None, on_stage_ready=None,
               native_products=None, sleep=time.sleep, timeout_seconds=300):
    """所属解除 → staging/source readback → global/ETag/readback → 最後の所属追加。

    stage callbackは全APIのsubscriptionRequired=trueを保ち、更新後source manifestを返す。
    更新時はBearer経路を短時間停止する。失敗時は今回追加した所属を先に解除し、
    この処理のXML/ETagがまだ一致するときだけglobalを戻す。再実行は新規readbackから。
    """
    previous_open, desired_open = set(previous_open_api_ids), set(desired_open_api_ids)
    additions = set(new_api_ids)
    existing = set(current.operations)
    if (not previous_open <= existing or not desired_open <= existing | additions
            or additions & existing):
        raise ValueError("Existing targets need generated source; additions need explicit absence proof")
    snapshot = read_policy_graph(current, transport, scope_manifest=preflight_scope_manifest, native_products=native_products)
    additions_snapshot = read_new_api_absence(current.apim_id, additions, transport)
    snapshot.update(additions_snapshot)
    snapshot.update(stage_prerequisites or {})
    product_snapshot, members = read_open_product(current.apim_id, transport, allowed_api_ids=previous_open)
    snapshot.update(product_snapshot)
    verify_snapshot(snapshot, transport)
    deadline = time.monotonic() + timeout_seconds
    if on_prepared is not None:
        on_prepared(deadline)
    global_id = current.apim_id + "/policies/policy"
    prior_global = snapshot[global_id][2]
    opened, written_global, global_write_accepted = set(), None, False
    interrupted = bool(members)
    try:
        for api_id in sorted(members):
            fresh, members = _membership(current.apim_id, api_id, False, transport, snapshot, members,
                                         sleep=sleep, deadline=deadline, manifest=current)
            # Refresh only this association. Intermediate checks retain every
            # root/collection/shared resource and related API detail; the full
            # snapshot is checked again before staging and after activation.
            products = _read_changed_api_products(current, api_id, False, transport)
            snapshot.update(fresh)
            snapshot.update(products)
        verify_snapshot(snapshot, transport)
        if on_stage_ready is not None:
            on_stage_ready(_native_product_contract(snapshot, current))
        desired = stage_closed_apis()
        if (not isinstance(desired, PolicyManifest) or desired.apim_id != current.apim_id
                or set(desired.operations) != existing | additions):
            raise ValueError("Closed staging must return the complete updated generated source manifest")
        _same_xml(desired.policies.get(global_id), desired_global_policy)
        staged_policies = {**desired.policies, global_id: current.policies[global_id]}
        staged = PolicyManifest(desired.apim_id, staged_policies, desired.operations, desired.products,
                                desired.backends, desired.named_values, desired.api_properties, desired.api_children, desired.source_version)
        # 現行の固定配置をdesiredで検査し、実資材と旧globalはstagedの期待値へ照合する。
        ready = read_policy_graph(staged, transport, scope_manifest=desired, native_products=native_products)
        fresh, members = read_open_product(current.apim_id, transport, allowed_api_ids=(), missing=not bool(desired_open))
        if members:
            raise ValueError("Staging must leave the employee route closed")
        ready.update(fresh)
        if _signature(ready[global_id][2]) != _signature(prior_global):
            raise ValueError("Global policy drifted during closed staging")
        verify_snapshot(ready, transport)
        if on_staged is not None:
            on_staged()
        if _structure(_xml(desired_global_policy)) != _structure(_xml(current.policies[global_id])):
            if time.monotonic() >= deadline:
                raise DeploymentError("Entra publication deadline expired before global policy write")
            response = transport.request("PUT", global_id + "?api-version=" + APIM_API,
                                         {"properties": {"format": "rawxml", "value": desired_global_policy}},
                                         headers={"If-Match": _etag(ready[global_id][2])})
            require_success(response, "Install reviewed Entra global policy", allowed=(200, 201, 202, 204))
            global_write_accepted = True
            candidate = _wait(transport, global_id, sleep=sleep, deadline=deadline, raw=True)
            _same_xml(_properties(candidate).get("value"), desired_global_policy)
            written_global = candidate
        ready = read_policy_graph(desired, transport, native_products=native_products)
        fresh, members = read_open_product(current.apim_id, transport, allowed_api_ids=(), missing=not bool(desired_open))
        ready.update(fresh)
        for api_id in sorted(desired_open):
            # Track intent before an accepted association write/postflight can fail.
            opened.add(api_id)
            fresh, members = _membership(current.apim_id, api_id, True, transport, ready, members,
                                         sleep=sleep, deadline=deadline, manifest=desired)
            products = _read_changed_api_products(desired, api_id, True, transport)
            ready.update(fresh)
            ready.update(products)
        verify_snapshot(ready, transport)
        return {"employeePathOpen": bool(desired_open), "employeePathInterrupted": interrupted,
                "managementPlaneVerified": True, "runtimeVerified": False}
    except (ValueError, OSError, DeploymentError):
        recovered, closed = True, False
        try:
            recovery, members = read_open_product(current.apim_id, transport,
                                                  allowed_api_ids=previous_open | desired_open, missing=True)
            # If a global/API/Product drift is observed, do not blindly mutate a
            # concurrently controlled association. Only fixed Product invariants
            # and this operation's known membership set are accepted for closure.
            for api_id in sorted(members & opened, reverse=True):
                recovery, members = _membership(current.apim_id, api_id, False, transport, recovery, members,
                                                 sleep=sleep, deadline=deadline)
            closed = not members
        except (ValueError, DeploymentError):
            recovered = False
        if global_write_accepted and written_global is None:
            recovered = False
        if written_global is not None and closed:
            try:
                actual = _wait(transport, global_id, sleep=sleep, deadline=deadline, raw=True)
                if _signature(actual) != _signature(written_global):
                    raise ValueError("Cannot restore a concurrently changed global policy")
                response = transport.request("PUT", global_id + "?api-version=" + APIM_API,
                                             {"properties": {"format": "rawxml", "value": _properties(prior_global)["value"]}},
                                             headers={"If-Match": _etag(actual)})
                require_success(response, "Restore this transition's previous global policy", allowed=(200, 201, 202, 204))
                _same_xml(_properties(_wait(transport, global_id, sleep=sleep, deadline=deadline, raw=True)).get("value"),
                          _properties(prior_global)["value"])
            except (ValueError, DeploymentError):
                recovered = False
        raise TransitionError(employee_path_closed=closed, recovery_complete=recovered and closed) from None
