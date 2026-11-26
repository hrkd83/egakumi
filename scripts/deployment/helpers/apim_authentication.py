"""API 公開用の APIM 共通ポリシーを生成・統合・照合する。"""
from __future__ import annotations

import re
import copy
import json
import xml.etree.ElementTree as ET
from uuid import UUID

from .paths import REPOSITORY
from . import credential_login
from .apim_audit import render_audit_policy
from .apim_ingress import TRUSTED_INGRESS_ID
from .apim_browser_exception import BROWSER_EXCEPTION_ID, BROWSER_EXCEPTION_VARIABLE
from .apim_common_authentication import (
    COMMON_AUTHENTICATION_FRAGMENT_ID, ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID,
    render_common_authentication_fragments, expand_common_authentication_fragments,
    expand_previous_common_authentication_fragments, _previous_authentication_source_matches,
)


COMMON_AUTHENTICATION_ID = "core-common-authentication"
REQUEST_CONTEXT_ID = "core-request-context"
AUTHENTICATED_SUBJECT_ID = "core-audit-authenticated-subject"
ENTRA_OPEN_PRODUCT_ID = "core-entra-model-users"
COMMON_NAMED_VALUES = ("core-subscription-key-header",)
ENTRA_NAMED_VALUES = ("core-entra-tenant-id", "core-entra-api-audience",
                      "core-entra-required-scope", "core-entra-user-limits")
MIGRATION_REQUIRED = (
    "APIM common authentication settings are incompatible. Check the foundation NamedValues and "
    "reconcile existing Core policy blocks with apps/gateway/apim/policies/service.xml before publishing APIs."
)
MANAGED_BLOCKS = {"inbound": (REQUEST_CONTEXT_ID, TRUSTED_INGRESS_ID, BROWSER_EXCEPTION_ID, COMMON_AUTHENTICATION_ID, AUTHENTICATED_SUBJECT_ID),
                  "outbound": ("core-response-request-id",),
                  "on-error": ("core-error-request-id",)}
POLICY_SECTIONS = ("inbound", "backend", "outbound", "on-error")


def common_authentication_settings(config: dict) -> dict[str, str]:
    """API 一覧から推測せず、基盤所有の入口認証を取得する。"""
    authentication = config.get("gateway", {}).get("authentication")
    if not isinstance(authentication, dict):
        raise ValueError("gateway.authentication must be configured before API publication")
    header = authentication.get("subscriptionKeyHeader", "api-key")
    # 基盤が書き換える相関・本人接続用ヘッダーを、購読キーの送信先には使わせない。
    if (not isinstance(header, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", header)
            or header.casefold().startswith("x-core-connection-")
            or header.casefold() in ("authorization", "host", "content-type", "cookie", "x-core-l7-ingress",
                                     "x-ms-client-request-id", "x-appgw-trace-id",
                                     "x-core-caller-id", "x-core-subscription-id")):
        raise ValueError("The foundation subscription-key header must be a dedicated HTTP header")
    return {"core-subscription-key-header": header}


def entra_authentication_settings(config: dict) -> dict:
    """公開設定を検査し、配備段階だけが使う内部状態と認証tenantを追加する。"""
    gateway = config.get("gateway", {})
    if not isinstance(gateway, dict):
        raise ValueError("gateway must be an object")
    authentication = gateway.get("authentication", {})
    if not isinstance(authentication, dict):
        raise ValueError("gateway.authentication must be an object")
    if "entra" not in authentication:
        # Entra設定を持たない既存キー構成だけは互換性を維持する。
        return {"enabled": False}
    from .configuration import _entra_authentication, entra_tenant_id
    settings = _entra_authentication(copy.deepcopy(authentication["entra"]))
    login = authentication.get("credentialLogin")
    if login is not None and not isinstance(login, dict):
        raise ValueError("gateway.authentication.credentialLogin must be an object")
    settings.update(enabled=True, tenantId=entra_tenant_id(config),
                    apiNames=_entra_api_names(config.get("apiDefinitions", []), credential_login_configured=login is not None))
    return _entra_policy_settings(settings)


def _entra_api_names(definitions: list[dict], *, credential_login_configured: bool = False) -> list[str]:
    """接続先を解決せず、業務API正本・生成MCP・保護CM入口の対象を求める。"""
    if not isinstance(definitions, list):
        raise ValueError("Entra authentication API definitions must be an array")
    if not definitions:
        from standard_apis import API_DEFINITION_DIRECTORY, load_api_definitions
        definitions = load_api_definitions(API_DEFINITION_DIRECTORY)
    names, seen = [], set()
    reserved = {credential_login.API_ID.casefold(), credential_login.BROWSER_API_ID.casefold()}
    for api in definitions:
        if not isinstance(api, dict):
            raise ValueError("Entra authentication API definitions must contain objects")
        candidates = [api.get("name")]
        if "mcp" in api:
            if not isinstance(api["mcp"], dict):
                raise ValueError("Entra authentication generated MCP definition must be an object")
            candidates.append(api["mcp"].get("name"))
        for name in candidates:
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name)
                    or name.casefold() in seen or credential_login_configured and name.casefold() in reserved):
                raise ValueError("Entra authentication requires unique canonical business API IDs")
            seen.add(name.casefold())
            names.append(name)
    if credential_login_configured:
        names.append(credential_login.API_ID)
    return names


def _entra_policy_settings(settings: dict | None) -> dict | None:
    """内部設定を検査する。公開入力にenabledやAPI一覧を追加しない。"""
    if settings is None:
        return None
    if not isinstance(settings, dict) or type(settings.get("enabled", False)) is not bool:
        raise ValueError("Entra authentication enabled must be a boolean")
    if set(settings) - {"enabled", "tenantId", "audience", "requiredScope", "apiNames", "userLimits"}:
        raise ValueError("Entra authentication contains unsupported settings")
    if not settings.get("enabled", False):
        return None
    result = copy.deepcopy(settings)
    def identifier(value):
        try:
            parsed = UUID(value)
            if not parsed.int:
                raise ValueError()
            return str(parsed)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Entra authentication requires nonzero identity GUIDs") from None
    result["tenantId"] = identifier(result.get("tenantId"))
    result["audience"] = identifier(result.get("audience"))
    names = result.get("apiNames")
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name) for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Entra authentication requires unique canonical API IDs")
    scope = result.setdefault("requiredScope", "Gateway.Invoke")
    if not isinstance(scope, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,127}", scope):
        raise ValueError("Entra authentication requires a delegated scope name")
    limits = result.get("userLimits")
    def bounded(value, maximum):
        return type(value) is int and 1 <= value <= maximum
    if not isinstance(limits, dict) or set(limits) != {"requestRate", "tokensPerMinute", "tokenQuota"}:
        raise ValueError("Entra authentication requires all user limit fields; explicit null disables a limit")
    rate, tpm, quota = limits["requestRate"], limits["tokensPerMinute"], limits["tokenQuota"]
    if (rate is not None and (not isinstance(rate, dict) or set(rate) != {"calls", "renewalPeriodSeconds"}
                             or not bounded(rate.get("calls"), 1000000)
                             or not bounded(rate.get("renewalPeriodSeconds"), 300))
            or tpm is not None and not bounded(tpm, 2147483647)
            or quota is not None and (not isinstance(quota, dict) or set(quota) != {"tokens", "period"}
                                      or not bounded(quota.get("tokens"), 2147483647)
                                      or quota.get("period") not in ("Hourly", "Daily", "Weekly", "Monthly", "Yearly"))):
        raise ValueError("Entra authentication user limits must be explicit null or complete bounded values")
    return result


def entra_authentication_named_values(entra: dict | None) -> dict[str, str]:
    """検証済み内部設定から、共通入口が所有する非秘密4値だけを生成する。"""
    settings = _entra_policy_settings(entra)
    if settings is None:
        return {}
    return dict(zip(ENTRA_NAMED_VALUES, (
        settings["tenantId"], settings["audience"], settings["requiredScope"],
        json.dumps(settings["userLimits"], sort_keys=True, separators=(",", ":")),
    )))


def entra_named_value_settings(config: dict) -> dict[str, str]:
    """正式な構築入力とAPI配備が、同じ4値の生成・検査を使用する。"""
    return entra_authentication_named_values(entra_authentication_settings(config))


def _canonical_entra_named_value(name: str, value: str) -> str:
    if name not in ENTRA_NAMED_VALUES or not isinstance(value, str):
        raise ValueError(MIGRATION_REQUIRED)
    if name in ENTRA_NAMED_VALUES[:2]:
        try:
            identifier = UUID(value)
            if not identifier.int:
                raise ValueError()
            return str(identifier)
        except (ValueError, TypeError, AttributeError):
            raise ValueError(MIGRATION_REQUIRED) from None
    if name == "core-entra-required-scope":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,127}", value):
            raise ValueError(MIGRATION_REQUIRED)
        return value
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(MIGRATION_REQUIRED)
            result[key] = item
        return result
    try:
        limits = json.loads(value, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        raise ValueError(MIGRATION_REQUIRED) from None
    if not isinstance(limits, dict) or set(limits) != {"requestRate", "tokensPerMinute", "tokenQuota"}:
        raise ValueError(MIGRATION_REQUIRED)
    def bounded(item, maximum):
        return type(item) is int and 1 <= item <= maximum
    rate, tpm, quota = limits["requestRate"], limits["tokensPerMinute"], limits["tokenQuota"]
    if (rate is not None and (not isinstance(rate, dict) or set(rate) != {"calls", "renewalPeriodSeconds"}
                             or not bounded(rate.get("calls"), 1000000)
                             or not bounded(rate.get("renewalPeriodSeconds"), 300))
            or tpm is not None and not bounded(tpm, 2147483647)
            or quota is not None and (not isinstance(quota, dict) or set(quota) != {"tokens", "period"}
                                      or not bounded(quota.get("tokens"), 2147483647)
                                      or quota.get("period") not in ("Hourly", "Daily", "Weekly", "Monthly", "Yearly"))):
        raise ValueError(MIGRATION_REQUIRED)
    return json.dumps(limits, sort_keys=True, separators=(",", ":"))


def validate_entra_named_values(values: dict[str, str]) -> dict[str, str]:
    """4値の欠落・余分値・型を拒否し、GUIDとJSONの代表形を返す。"""
    if not isinstance(values, dict) or set(values) != set(ENTRA_NAMED_VALUES):
        raise ValueError(MIGRATION_REQUIRED)
    return {name: _canonical_entra_named_value(name, values[name]) for name in ENTRA_NAMED_VALUES}


def validate_entra_named_value(body: dict, name: str, expected: str) -> None:
    """実NamedValueの所有属性と型を検査し、信頼済み期待値と照合する。"""
    properties = body.get("properties", {}) if isinstance(body, dict) else {}
    if (not isinstance(properties, dict) or properties.get("displayName") != name
            or properties.get("secret") is not False or properties.get("keyVault") is not None
            or _canonical_entra_named_value(name, properties.get("value"))
               != _canonical_entra_named_value(name, expected)):
        raise ValueError(MIGRATION_REQUIRED)


def render_service_policy(login: dict | None = None, *, entra: dict | None = None) -> str:
    """最新方式だけを生成する。旧client制限付き設定はdesiredへ受け入れない。"""
    return _render_service_policy(login, entra=entra)


def _render_service_policy(login: dict | None = None, *, entra: dict | None = None,
                           inline_common_authentication: bool = False) -> str:
    """正規の共通部品から、現在のGlobalと初期構築用inlineを生成する。"""
    directory = REPOSITORY / "apps/gateway/apim/policies"
    settings = _entra_policy_settings(entra)
    value = (directory / "service.xml").read_text(encoding="utf-8")
    error_call = '<include-fragment id="core-entra-authentication-error" fragment-id="core-entra-authentication-error" />'
    if not settings:
        value = value.replace(error_call, "")
    if inline_common_authentication:
        fragments = render_common_authentication_fragments(entra_enabled=bool(settings), source_comments="omit")
        for identifier, fragment in fragments.items():
            call = '<include-fragment id="' + identifier + '" fragment-id="' + identifier + '" />'
            value = value.replace(call, fragment.replace("<fragment>", "").replace("</fragment>", ""), 1)
    return render_audit_policy(value, source_comments="compact")


def _render_inline_common_authentication_service_policy(login: dict | None = None, *, entra: dict | None = None) -> str:
    """現在の共通認証をinlineへ展開し、閉じたGlobalの初期構築にも使う。"""
    return _render_service_policy(login, entra=entra, inline_common_authentication=True)


def _authentication_block(policy: str, *, entra: dict | None = None, fragments: dict[str, str] | None = None,
                          allow_previous_explanations: bool = False) -> ET.Element:
    """実際に適用される inbound 直下の認証ブロックを一意に取り出す。"""
    if not isinstance(policy, str) or "<!DOCTYPE" in policy.upper() or "<!ENTITY" in policy.upper():
        raise ValueError(MIGRATION_REQUIRED)
    try:
        document = ET.fromstring(policy)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError(MIGRATION_REQUIRED) from None
    if any(node.get("fragment-id") == COMMON_AUTHENTICATION_FRAGMENT_ID for node in document.iter("include-fragment")):
        enabled = bool(entra and entra.get("enabled", True))
        if allow_previous_explanations and fragments is None:
            raise ValueError("Previous authentication explanations require the actual complete fragment source")
        trusted = render_common_authentication_fragments(entra_enabled=enabled) if fragments is None else fragments
        expand = expand_previous_common_authentication_fragments if allow_previous_explanations else expand_common_authentication_fragments
        document = ET.fromstring(expand(policy, trusted, entra_enabled=enabled))
    inbound = document.findall("inbound")
    matches = [node for node in document.iter() if node.get("id") == COMMON_AUTHENTICATION_ID]
    if (document.tag != "policies" or len(inbound) != 1 or len(matches) != 1
            or matches[0].tag != "choose" or matches[0] not in list(inbound[0])):
        raise ValueError(MIGRATION_REQUIRED)
    return matches[0]


def _structure(element: ET.Element):
    """コメント・要素間の整形だけを除外し、条件式と処理順はすべて比較する。"""
    def content(text):
        return text if text and text.strip() else ""
    return (element.tag, tuple(sorted(element.attrib.items())), content(element.text),
            tuple((_structure(child), content(child.tail)) for child in element if isinstance(child.tag, str)))


def _document(policy: str) -> ET.Element:
    if not isinstance(policy, str) or any(value in policy.upper() for value in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError(MIGRATION_REQUIRED)
    try:
        root = ET.fromstring(policy, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError(MIGRATION_REQUIRED) from None
    sections = [node.tag for node in root if isinstance(node.tag, str)]
    # APIM は未設定のセクションを省略した XML も返します。既存の順序と一意性は
    # 検査し、共通処理が必要なセクションだけを統合時に補います。
    if root.tag != "policies" or sections != [section for section in POLICY_SECTIONS if section in sections]:
        raise ValueError(MIGRATION_REQUIRED)
    return root


def merge_common_authentication_policy(policy: str | None, login: dict | None = None,
                                      *, entra: dict | None = None, migrate_credential_broker: bool = False,
                                      migrate_entra_authentication: bool = False) -> tuple[str, bool]:
    """最新方式を統合する。旧生成元は明示遷移での検査だけに分離する。"""
    return _merge_common_authentication_policy(policy, login, entra=entra,
                                              migrate_credential_broker=migrate_credential_broker,
                                              migrate_entra_authentication=migrate_entra_authentication)


def _merge_common_authentication_policy(policy: str | None, login: dict | None = None,
                                       *, entra: dict | None = None, migrate_credential_broker: bool = False,
                                       migrate_entra_authentication: bool = False,
                                       inline_common_authentication: bool = False,
                                       previous_explanations: bool = False) -> tuple[str, bool]:
    """共通処理を各セクションの先頭に置き、既存の追加処理とコメントを保持する。

    Core の ID が付いた既存ブロックは期待値と照合する。変更されている場合は、
    管理者が差分を確認して統合できるよう公開前に停止する。
    """
    expected = _document(_render_service_policy(login, entra=entra,
                                               inline_common_authentication=inline_common_authentication))
    entra_active = bool(_entra_policy_settings(entra))
    document = _document(policy) if policy is not None else _document(
        '<policies><inbound/><backend><forward-request/></backend><outbound/><on-error/></policies>')
    changed = policy is None
    # 監査の部品は、共通inboundの決めた位置から各1回だけ呼び出します。
    # fragment名だけをコピーした呼出しや、別の分岐・段階からの再実行を受け入れません。
    inbound = document.find("inbound")
    authentication_inline = inline_common_authentication
    for identifier, section_name in ((COMMON_AUTHENTICATION_ID, "inbound"), (ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID, "on-error")):
        section = document.find(section_name)
        references = [node for node in document.iter() if node.tag == "include-fragment"
                      and (node.get("fragment-id") == identifier or node.get("id") == identifier)]
        if (authentication_inline and references or len(references) > 1 or any(
                node.attrib != {"id": identifier, "fragment-id": identifier} or len(node)
                or node.text and node.text.strip() or section is None or node not in list(section) for node in references)):
            raise ValueError(MIGRATION_REQUIRED)
        if (not authentication_inline and any(node.tag == "choose" and node.get("id") == identifier for node in document.iter())
                and not (migrate_credential_broker and login and identifier == COMMON_AUTHENTICATION_ID)):
            raise ValueError(MIGRATION_REQUIRED)
    common_call = document.find("inbound/include-fragment[@id='" + COMMON_AUTHENTICATION_ID + "']")
    error_call = document.find("on-error/include-fragment[@id='" + ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID + "']")
    if not authentication_inline and entra_active and common_call is not None and error_call is None and not migrate_entra_authentication:
        # 既存キー専用構成からの有効化は、明示された移行でだけ許可します。
        # include名が同じでも、その背後の認証状態を暗黙に変更しません。
        raise ValueError(MIGRATION_REQUIRED)
    for identifier in (REQUEST_CONTEXT_ID, AUTHENTICATED_SUBJECT_ID):
        references = [node for node in document.iter()
                      if node.tag == "include-fragment" and node.get("fragment-id") == identifier]
        if (len(references) > 1 or any(
                node.attrib != {"id": identifier, "fragment-id": identifier}
                or len(node) or node.text and node.text.strip()
                or inbound is None or node not in list(inbound) for node in references)):
            raise ValueError(MIGRATION_REQUIRED)
    references = [node for node in document.iter() if node.tag == "include-fragment"
                  and (node.get("fragment-id") == TRUSTED_INGRESS_ID or node.get("id") == TRUSTED_INGRESS_ID)]
    if (len(references) > 1 or any(
            node.attrib != {"id": TRUSTED_INGRESS_ID, "fragment-id": TRUSTED_INGRESS_ID}
            or len(node) or node.text and node.text.strip()
            or inbound is None or node not in list(inbound) for node in references)):
        raise ValueError(MIGRATION_REQUIRED)
    if any(node.tag == "choose" and node.get("id") == TRUSTED_INGRESS_ID
                                  for node in document.iter()):
        # 共通入口は現在の固定部品を呼び出す形式だけを受け入れます。
        raise ValueError(MIGRATION_REQUIRED)
    references = [node for node in document.iter() if node.tag == "include-fragment"
                  and (node.get("fragment-id") == BROWSER_EXCEPTION_ID or node.get("id") == BROWSER_EXCEPTION_ID)]
    if (len(references) > 1 or any(
            node.attrib != {"id": BROWSER_EXCEPTION_ID, "fragment-id": BROWSER_EXCEPTION_ID}
            or len(node) or node.text and node.text.strip()
            or inbound is None or node not in list(inbound) for node in references)
            or any(node.tag == "set-variable" and (node.get("name") == BROWSER_EXCEPTION_VARIABLE
                   or node.get("id") == BROWSER_EXCEPTION_ID) for node in document.iter())):
        raise ValueError(MIGRATION_REQUIRED)
    request_context = [node for node in document.iter()
                       if node.tag == "set-variable" and node.get("name") == "gatewayRequestContext"]
    if request_context:
        # 共通側が取得した識別情報を、後続の独自処理で上書きしません。
        raise ValueError(MIGRATION_REQUIRED)
    subjects = [node for node in document.iter()
                if node.tag == "set-variable" and node.get("name") == "gatewayAuthenticatedSubject"]
    if subjects:
        raise ValueError(MIGRATION_REQUIRED)
    if any(node.get("id", "").startswith("core-browser-login-")
           or node.get("id") == "core-authentication-error" for node in document.iter()):
        raise ValueError(MIGRATION_REQUIRED)
    if not entra_active and any(
            node.get("id") == "core-entra-authentication-error" for node in document.iter()):
        # 無効時のowned IDは未知の改変。明示遷移で検証済み旧ブロックを除く場合だけ許可します。
        raise ValueError(MIGRATION_REQUIRED)
    retired = {"core-credential-login-cache-value": "set-variable",
               "core-credential-login-cache-key": "set-variable",
               "core-credential-login-cache": "choose"}
    old_cache = [node for node in document.iter() if node.get("id") in retired]
    if old_cache:
        inbound = document.find("inbound")
        if (not migrate_credential_broker or not login or inbound is None
                or len(old_cache) != len(retired) or {node.get("id") for node in old_cache} != set(retired)
                or any(node not in list(inbound) or node.tag != retired[node.get("id")] for node in old_cache)):
            raise ValueError(MIGRATION_REQUIRED)
        cache = next(node for node in old_cache if node.tag == "choose")
        lookup = cache.find("./when/cache-lookup-value")
        # 実際の配備は、キーがある場合だけ lookup する choose で包まれています。
        # この範囲に別の処理が混ざっている場合は、まとめて削除しません。
        if (len(cache) != 1 or cache[0].tag != "when" or len(cache[0]) != 1 or lookup is None
                or lookup.get("caching-type") != "internal"
                or lookup.get("variable-name") != "gatewayCredentialCached"
                or {node.get("name") for node in old_cache if node.tag == "set-variable"}
                   != {"gatewayCredentialCached", "gatewayCredentialCacheKey"}):
            raise ValueError(MIGRATION_REQUIRED)
        for node in old_cache:
            inbound.remove(node)
        changed = True
    # 既知の旧匿名経路だけを、新しいEntra戻り先を加えた経路へ置き換えます。
    # 手動追加のポリシーや、本文が変更された認可ブロックはこの移行で上書きしません。
    if migrate_credential_broker and login and any(node.tag == "choose" and node.get("id") == COMMON_AUTHENTICATION_ID for node in document.iter()):
        _authentication_block(ET.tostring(document, encoding="unicode"), entra=entra)
        current = document.find("inbound").find("choose[@id='" + COMMON_AUTHENTICATION_ID + "']")
        # 旧キー認証へ匿名callbackを先に移行し、その後の明示Entra移行と合成できる。
        wanted = _authentication_block(_render_inline_common_authentication_service_policy(
            login, entra=entra if current.find(".//validate-azure-ad-token") is not None else None), entra=entra)
        prior = copy.deepcopy(wanted)
        anonymous = prior.find("when")
        condition = ET.fromstring(credential_login.anonymous_policy(login)).get("condition")
        clause = r' \|\| \(context\.Operation\.Id == "identity-callback"[^)]*\)'
        previous_condition, count = re.subn(clause, "", condition)
        if count == 1:
            anonymous.set("condition", previous_condition)
            # 現在のchooseでは不要なfalse分岐を、旧構成との照合候補だけへ戻します。
            # 既存の旧生成方式に含まれている場合は、重複して追加しません。
            dummy = ET.fromstring('<when condition="@(false)"><set-variable name="gatewayCommonAuthenticationAnonymous" value="@(true)" /></when>')
            branches = list(prior)
            if len(branches) < 2 or _structure(branches[1]) != _structure(dummy):
                prior.insert(1, dummy)
            if _structure(current) == _structure(prior):
                inbound = document.find("inbound")
                position = list(inbound).index(current)
                inbound.remove(current)
                replacement = next(node for node in expected.find("inbound") if node.get("id") == COMMON_AUTHENTICATION_ID)
                inbound.insert(position, copy.deepcopy(replacement))
                changed = True
    # 既知の従来キー認証だけを明示移行できる。手動変更を同じIDで上書きしない。
    if migrate_entra_authentication and entra_active:
        current_policy = ET.tostring(document, encoding="unicode")
        if any(node.tag == "choose" and node.get("id") == COMMON_AUTHENTICATION_ID for node in document.iter()):
            _authentication_block(current_policy, entra=entra)
            current = document.find("inbound").find("choose[@id='" + COMMON_AUTHENTICATION_ID + "']")
            prior = _authentication_block(render_service_policy(login))
            wanted = _authentication_block(ET.tostring(expected, encoding="unicode"), entra=entra)
            if _structure(current) == _structure(prior):
                inbound = document.find("inbound")
                position = list(inbound).index(current)
                inbound.remove(current)
                inbound.insert(position, copy.deepcopy(wanted))
                changed = True
    blocks = dict(MANAGED_BLOCKS)
    if entra_active:
        blocks["on-error"] += ("core-entra-authentication-error",)
    for section, identifiers in blocks.items():
        target, source = document.find(section), expected.find(section)
        if target is None:
            target = ET.Element(section)
            later = POLICY_SECTIONS[POLICY_SECTIONS.index(section) + 1:]
            following = next((node for node in document if node.tag in later), None)
            if following is None:
                document.append(target)
            else:
                document.insert(list(document).index(following), target)
            changed = True
        existing = []
        for identifier in identifiers:
            wanted = next(node for node in source if node.get("id") == identifier)
            matches = [node for node in document.iter() if node.get("id") == identifier]
            same = (not matches or (_previous_authentication_source_matches(matches[0], wanted) if previous_explanations
                                    else _structure(matches[0]) == _structure(wanted)))
            if len(matches) > 1 or (matches and (matches[0] not in list(target) or not same)):
                raise ValueError(MIGRATION_REQUIRED)
            existing.append(matches[0] if matches else None)
        # 認証より前に独自の return-response 等がある設定も、処理順の確認を求める。
        elements = [node for node in target if isinstance(node.tag, str)]
        present = [node for node in existing if node is not None]
        if elements[:len(present)] != present:
            raise ValueError(MIGRATION_REQUIRED)
        if any(node is None for node in existing):
            for node in present:
                target.remove(node)
            for identifier in reversed(identifiers):
                wanted = next(node for node in source if node.get("id") == identifier)
                target.insert(0, copy.deepcopy(wanted))
                position = list(source).index(wanted)
                if position and source[position - 1].tag is ET.Comment:
                    target.insert(0, copy.deepcopy(source[position - 1]))
            changed = True
    return (ET.tostring(document, encoding="unicode") if changed else policy), changed


def validate_common_authentication_policy(policy: str, login: dict | None = None, *, entra: dict | None = None,
                                          fragments: dict[str, str] | None = None) -> None:
    """成功マーカーだけでなく、匿名例外・検証・監査を含むブロック全体を照合する。"""
    if _structure(_authentication_block(policy, entra=entra, fragments=fragments)) != _structure(_authentication_block(render_service_policy(login, entra=entra), entra=entra)):
        raise ValueError(MIGRATION_REQUIRED)
    _, changed = merge_common_authentication_policy(policy, login, entra=entra)
    if changed:
        raise ValueError(MIGRATION_REQUIRED)


def _validate_previous_common_authentication_policy(policy: str, login: dict | None = None,
                                                     *, entra: dict | None = None,
                                                     fragments: dict[str, str] | None = None,
                                                     allow_previous_explanations: bool = False) -> None:
    """現在の正本・inlineと、明示された説明差分の旧部品だけを完全照合する。"""
    # Only the previous fragment-based generation can opt in. The raw dictionary
    # is checked and expanded, never replaced with a current dictionary. Existing
    # canonical inline bootstrap retains its complete fixed checks.
    document = _document(policy)
    if allow_previous_explanations and document.find(
            "inbound/include-fragment[@id='" + COMMON_AUTHENTICATION_FRAGMENT_ID + "']") is not None:
        current = _authentication_block(policy, entra=entra, fragments=fragments, allow_previous_explanations=True)
        expected = _authentication_block(render_service_policy(login, entra=entra), entra=entra)
        if not _previous_authentication_source_matches(current, expected):
            raise ValueError(MIGRATION_REQUIRED)
        _, changed = _merge_common_authentication_policy(policy, login, entra=entra, previous_explanations=True)
        if changed:
            raise ValueError(MIGRATION_REQUIRED)
        return
    if document.find("inbound/choose[@id='" + COMMON_AUTHENTICATION_FRAGMENT_ID + "']") is not None:
        expected = _render_service_policy(login, entra=entra, inline_common_authentication=True)
        if _structure(_authentication_block(policy, entra=entra)) != _structure(
                _authentication_block(expected, entra=entra)):
            raise ValueError(MIGRATION_REQUIRED)
        _, changed = _merge_common_authentication_policy(
            policy, login, entra=entra, inline_common_authentication=True)
        if changed:
            raise ValueError(MIGRATION_REQUIRED)
        return
    validate_common_authentication_policy(policy, login, entra=entra, fragments=fragments)


def read_managed_entra_authentication(policy: str, login: dict | None = None, *,
                                      expected_entra: dict | None = None, fragments: dict[str, str] | None = None,
                                      allow_previous_explanations: bool = False) -> dict | None:
    """現在のNV版を、明示された信頼済み論理設定と正本の構造で照合する。

    NV参照XMLは実値の証明にならない。呼出側は実4NVもtyped検査し、独立した
    期待値と照合する。任意の現行XMLを旧生成元として受け入れない。
    """
    if type(allow_previous_explanations) is not bool:
        raise ValueError("Previous authentication explanations require an explicit Boolean opt-in")
    block = _authentication_block(policy, entra=expected_entra, fragments=fragments,
                                  allow_previous_explanations=allow_previous_explanations)
    validators = block.findall(".//validate-azure-ad-token")
    if not validators:
        _validate_previous_common_authentication_policy(policy, login, fragments=fragments,
                                                       allow_previous_explanations=allow_previous_explanations)
        return None
    variables = block.findall(".//set-variable[@name='gatewayUserAuthentication']")
    if len(validators) != 1 or len(variables) != 1:
        raise ValueError(MIGRATION_REQUIRED)
    if validators[0].get("tenant-id") == "{{core-entra-tenant-id}}":
        settings = _entra_policy_settings(expected_entra)
        if not settings:
            raise ValueError(MIGRATION_REQUIRED)
        _validate_previous_common_authentication_policy(policy, login, entra=settings, fragments=fragments,
                                                       allow_previous_explanations=allow_previous_explanations)
        return settings
    raise ValueError(MIGRATION_REQUIRED)


def transition_common_authentication_policy(policy: str, login: dict | None = None, *,
                                            previous_entra: dict | None, entra: dict | None,
                                            previous_fragments: dict[str, str] | None = None,
                                            allow_previous_explanations: bool = False) -> tuple[str, bool]:
    """検証済み旧設定から明示遷移する。無効化時も独自のglobal処理を保持する。"""
    if type(allow_previous_explanations) is not bool:
        raise ValueError("Previous authentication explanations require an explicit Boolean opt-in")
    _validate_previous_common_authentication_policy(policy, login, entra=previous_entra, fragments=previous_fragments,
                                                   allow_previous_explanations=allow_previous_explanations)
    desired = _document(render_service_policy(login, entra=entra))
    document = _document(policy)
    # 認証に加え、既知旧版の要求snapshotとingress拒否監査も明示更新する。
    # 旧版の完全照合後なので、同じIDを持つ未知の編集を巻き込まない。
    changed = False
    blocks = dict(MANAGED_BLOCKS)
    blocks["on-error"] += ("core-entra-authentication-error",)
    for section, identifiers in blocks.items():
        target, source = document.find(section), desired.find(section)
        if target is None:
            continue
        for identifier in identifiers:
            current = next((node for node in target if node.get("id") == identifier), None)
            wanted = next((node for node in source if node.get("id") == identifier), None)
            if current is not None and (wanted is None or _structure(current) != _structure(wanted)):
                position = list(target).index(current)
                target.remove(current)
                if wanted is not None:
                    target.insert(position, copy.deepcopy(wanted))
                changed = True
    candidate = ET.tostring(document, encoding="unicode") if changed else policy
    result, merged = merge_common_authentication_policy(candidate, login, entra=entra,
                                                       migrate_entra_authentication=True)
    validate_common_authentication_policy(result, login, entra=entra)
    return result, changed or merged


def validate_common_named_value(body: dict, name: str, expected: str) -> None:
    """秘密の読出しをせず、基盤が所有する非秘密の NamedValue を照合する。"""
    properties = body.get("properties", {}) if isinstance(body, dict) else {}
    if (not isinstance(properties, dict) or properties.get("displayName") != name
            or properties.get("secret") is not False or properties.get("keyVault") is not None):
        raise ValueError(MIGRATION_REQUIRED)
    value = properties.get("value")
    if not isinstance(value, str) or value.casefold() != expected.casefold():
        raise ValueError(MIGRATION_REQUIRED)
