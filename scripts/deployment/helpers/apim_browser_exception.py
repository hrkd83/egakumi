"""接続ログインのブラウザー受付を、共通認証の限定例外として判定する。"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from .apim_audit import render_audit_policy
from .paths import REPOSITORY


BROWSER_EXCEPTION_ID = "core-credential-browser-exception"
BROWSER_EXCEPTION_VARIABLE = "gatewayCredentialBrowserException"
_SECTIONS = {"policies", "inbound", "backend", "outbound", "on-error"}


def _policy_xml(xml: str) -> ET.Element:
    if not isinstance(xml, str) or any(mark in xml.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("Browser exception requires safe policy XML")
    try:
        return ET.fromstring(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("Browser exception requires valid policy XML") from None


def _fragment_node(xml: str) -> ET.Element:
    root = _policy_xml(xml)
    children = [node for node in root if isinstance(node.tag, str)]
    if (root.tag != "fragment" or root.attrib or len(children) != 1
            or children[0].tag != "set-variable" or list(children[0])
            or set(children[0].attrib) != {"id", "name", "value"}
            or children[0].get("id") != BROWSER_EXCEPTION_ID
            or children[0].get("name") != BROWSER_EXCEPTION_VARIABLE
            or not children[0].get("value", "").strip().startswith("@{")
            or not children[0].get("value", "").strip().endswith("}")
            or any(node.tag in _SECTIONS | {"base", "include-fragment", "fragment"}
                   for node in children[0].iter())
            or len(xml.encode("utf-8")) > 512 * 1024):
        raise ValueError("Browser exception must contain only its fixed Boolean writer")
    return root


def _structure(element: ET.Element):
    def content(text):
        return text if text and text.strip() else ""
    return (element.tag, tuple(sorted(element.attrib.items())), content(element.text),
            tuple((_structure(child), content(child.tail)) for child in element if isinstance(child.tag, str)))


def render_browser_exception_fragment(login: dict | None = None) -> str:
    """CM設定の有無を内部の真偽値へ展開する。本人確認やAPIの利用許可は行わない。"""
    directory = REPOSITORY / "apps/apim/policies/fragments/authentication" / BROWSER_EXCEPTION_ID
    template = (directory / (BROWSER_EXCEPTION_ID + ".xml")).read_text(encoding="utf-8")
    rendered = render_audit_policy(template).replace("__CREDENTIAL_BROWSER_CONFIGURED__", "true" if login else "false")
    _fragment_node(rendered)
    return rendered


def expand_browser_exception_fragment(xml: str, fragment: str | None = None, *, login: dict | None = None) -> str:
    """実式試験では、確認済みの固定leafだけを共通inboundの呼出位置へ展開する。"""
    root = _policy_xml(xml)
    includes = [node for node in root.iter() if node.tag == "include-fragment"
                and (node.get("fragment-id") == BROWSER_EXCEPTION_ID or node.get("id") == BROWSER_EXCEPTION_ID)]
    if not includes:
        return xml
    inbound = root.findall("inbound")
    if (root.tag != "policies" or len(inbound) != 1 or len(includes) != 1
            or includes[0] not in list(inbound[0])
            or includes[0].attrib != {"id": BROWSER_EXCEPTION_ID, "fragment-id": BROWSER_EXCEPTION_ID}
            or list(includes[0]) or includes[0].text and includes[0].text.strip()
            or any(node is not includes[0] and (node.get("id") == BROWSER_EXCEPTION_ID
                   or node.tag == "set-variable" and node.get("name") == BROWSER_EXCEPTION_VARIABLE)
                   for node in root.iter())):
        raise ValueError("Browser exception requires one fixed include directly under common inbound")
    expected = _fragment_node(render_browser_exception_fragment(login))
    parsed = expected if fragment is None else _fragment_node(fragment)
    if _structure(parsed) != _structure(expected):
        raise ValueError("Browser exception fragment differs from its fixed source")
    include = includes[0]
    position = list(inbound[0]).index(include)
    replacement = [copy.deepcopy(node) for node in parsed]
    inbound[0].remove(include)
    for offset, node in enumerate(replacement):
        inbound[0].insert(position + offset, node)
    replacement[-1].tail = include.tail
    return ET.tostring(root, encoding="unicode")
