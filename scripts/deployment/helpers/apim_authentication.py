"""API 公開用の APIM 共通ポリシーを生成・統合・照合する。"""
from __future__ import annotations

import re
import copy
import xml.etree.ElementTree as ET

from .paths import REPOSITORY
from . import credential_login


COMMON_AUTHENTICATION_ID = "core-common-authentication"
COMMON_NAMED_VALUES = ("core-subscription-key-header",)
MIGRATION_REQUIRED = (
    "APIM common authentication settings are incompatible. Check the foundation NamedValues and "
    "reconcile existing Core policy blocks with apps/gateway/apim/policies/service.xml before publishing APIs."
)
MANAGED_BLOCKS = {"inbound": ("core-trusted-ingress", COMMON_AUTHENTICATION_ID),
                  "outbound": ("core-response-request-id",),
                  "on-error": ("core-error-request-id",)}
POLICY_SECTIONS = ("inbound", "backend", "outbound", "on-error")


def common_authentication_settings(config: dict) -> dict[str, str]:
    """API 一覧から推測せず、基盤所有の入口認証を取得する。"""
    authentication = config.get("gateway", {}).get("authentication")
    if not isinstance(authentication, dict):
        raise ValueError("gateway.authentication must be configured before API publication")
    header = authentication.get("subscriptionKeyHeader", "api-key")
    if (not isinstance(header, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", header)
            or header.casefold() in ("authorization", "host", "content-type", "cookie", "x-core-l7-ingress",
                                     "x-core-connection-session", "x-core-connection-poll", "x-core-connection-transaction")):
        raise ValueError("The foundation subscription-key header must be a dedicated HTTP header")
    return {"core-subscription-key-header": header}


def render_service_policy(login: dict | None = None) -> str:
    """XML テンプレートへ監査処理と名前付きの値の参照式を埋め込む。"""
    telemetry = (REPOSITORY / "apps/gateway/apim/policies/gateway-telemetry.xml").read_text(encoding="utf-8")
    telemetry = telemetry.replace("<fragment>", "").replace("</fragment>", "")
    policy = (REPOSITORY / "apps/gateway/apim/policies/service.xml").read_text(encoding="utf-8")
    parts = {"ANONYMOUS": credential_login.anonymous_policy(login) if login else ""}
    for part, value in parts.items():
        policy = policy.replace("<!-- __CREDENTIAL_LOGIN_" + part + "__ -->", value)
    return (policy.replace("__AUTHENTICATION_TELEMETRY_XML__", telemetry)
            .replace("__L7_INGRESS_REQUIRED__", '&quot;{{core-l7-ingress-enabled}}&quot; == &quot;true&quot;')
            .replace("__L7_INGRESS_EXPECTED__", "{{core-l7-ingress-secret}}"))


def _authentication_block(policy: str) -> ET.Element:
    """実際に適用される inbound 直下の認証ブロックを一意に取り出す。"""
    if not isinstance(policy, str) or "<!DOCTYPE" in policy.upper() or "<!ENTITY" in policy.upper():
        raise ValueError(MIGRATION_REQUIRED)
    try:
        document = ET.fromstring(policy)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError(MIGRATION_REQUIRED) from None
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
                                      *, migrate_credential_broker: bool = False) -> tuple[str, bool]:
    """共通処理を各セクションの先頭に置き、既存の追加処理とコメントを保持する。

    Core の ID が付いた既存ブロックは期待値と照合する。変更されている場合は、
    管理者が差分を確認して統合できるよう公開前に停止する。
    """
    expected = _document(render_service_policy(login))
    document = _document(policy) if policy is not None else _document(
        '<policies><inbound/><backend><forward-request/></backend><outbound/><on-error/></policies>')
    changed = policy is None
    if any(node.get("id", "").startswith("core-browser-login-")
           or node.get("id") == "core-authentication-error" for node in document.iter()):
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
    if migrate_credential_broker and login and any(node.get("id") == COMMON_AUTHENTICATION_ID for node in document.iter()):
        _authentication_block(ET.tostring(document, encoding="unicode"))
        current = document.find("inbound").find("choose[@id='" + COMMON_AUTHENTICATION_ID + "']")
        wanted = _authentication_block(ET.tostring(expected, encoding="unicode"))
        prior = copy.deepcopy(wanted)
        anonymous = prior.find("when")
        condition = anonymous.get("condition", "") if anonymous is not None else ""
        clause = r' \|\| \(context\.Operation\.Id == "identity-callback"[^)]*\)'
        previous_condition, count = re.subn(clause, "", condition)
        if count == 1:
            anonymous.set("condition", previous_condition)
            if _structure(current) == _structure(prior):
                inbound = document.find("inbound")
                position = list(inbound).index(current)
                inbound.remove(current)
                inbound.insert(position, copy.deepcopy(wanted))
                changed = True
    blocks = dict(MANAGED_BLOCKS)
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
            if len(matches) > 1 or (matches and (matches[0] not in list(target) or _structure(matches[0]) != _structure(wanted))):
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


def validate_common_authentication_policy(policy: str, login: dict | None = None) -> None:
    """成功マーカーだけでなく、匿名例外・検証・監査を含むブロック全体を照合する。"""
    if _structure(_authentication_block(policy)) != _structure(_authentication_block(render_service_policy(login))):
        raise ValueError(MIGRATION_REQUIRED)
    _, changed = merge_common_authentication_policy(policy, login)
    if changed:
        raise ValueError(MIGRATION_REQUIRED)


def validate_common_named_value(body: dict, name: str, expected: str) -> None:
    """秘密の読出しをせず、基盤が所有する非秘密の NamedValue を照合する。"""
    properties = body.get("properties", {}) if isinstance(body, dict) else {}
    if (not isinstance(properties, dict) or properties.get("displayName") != name
            or properties.get("secret") is not False or properties.get("keyVault") is not None):
        raise ValueError(MIGRATION_REQUIRED)
    value = properties.get("value")
    if not isinstance(value, str) or value.casefold() != expected.casefold():
        raise ValueError(MIGRATION_REQUIRED)
