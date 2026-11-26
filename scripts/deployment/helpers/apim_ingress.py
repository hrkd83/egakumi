"""信頼したHTTP入口の確認を、固定のAPIM leaf fragmentとして生成・展開する。"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from . import credential_login
from .apim_audit import render_audit_policy
from .paths import REPOSITORY


TRUSTED_INGRESS_ID = "core-trusted-ingress"
_SECTIONS = {"policies", "inbound", "backend", "outbound", "on-error"}


def _policy_xml(xml: str) -> ET.Element:
    if not isinstance(xml, str) or any(mark in xml.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("Trusted ingress requires safe policy XML")
    try:
        return ET.fromstring(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("Trusted ingress requires valid policy XML") from None


def _fragment_node(xml: str) -> ET.Element:
    root = _policy_xml(xml)
    children = [node for node in root if isinstance(node.tag, str)]
    if (root.tag != "fragment" or root.attrib or len(children) != 1
            or children[0].tag != "choose" or children[0].attrib != {"id": TRUSTED_INGRESS_ID}
            or any(node.tag in _SECTIONS | {"base", "include-fragment", "fragment"}
                   for node in children[0].iter())
            or len(xml.encode("utf-8")) > 512 * 1024):
        raise ValueError("Trusted ingress must contain only its fixed leaf choose")
    return root


def _structure(element: ET.Element):
    def content(text):
        return text if text and text.strip() else ""
    return (element.tag, tuple(sorted(element.attrib.items())), content(element.text),
            tuple((_structure(child), content(child.tail)) for child in element if isinstance(child.tag, str)))


def render_trusted_ingress_fragment(*, source_comments: str = "full") -> str:
    """入口条件と拒否監査の説明を保持する。compact指定は旧生成元の照合に使う。"""
    return _render_trusted_ingress_fragment("fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml", source_comments)


def _render_trusted_ingress_fragment(filename: str, source_comments: str) -> str:
    directory = REPOSITORY / "apps/gateway/apim/policies"
    telemetry = (directory / "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml").read_text(encoding="utf-8")
    telemetry = telemetry.replace("<fragment>", "").replace("</fragment>", "")
    telemetry = credential_login.key_only_gateway_telemetry(telemetry, source_comments=source_comments)
    template = (directory / filename).read_text(encoding="utf-8")
    rendered = render_audit_policy(template.replace("__AUTHENTICATION_TELEMETRY_XML__", telemetry), source_comments=source_comments)
    rendered = rendered.replace("__L7_INGRESS_REQUIRED__", '&quot;{{core-l7-ingress-enabled}}&quot; == &quot;true&quot;')
    rendered = rendered.replace("__L7_INGRESS_EXPECTED__", "{{core-l7-ingress-secret}}")
    _fragment_node(rendered)
    return rendered


def expand_trusted_ingress_fragment(xml: str, fragment: str | None = None) -> str:
    """実式試験と移行照合で、既知の入口部品だけをcommon inboundの元の位置へ展開する。"""
    root = _policy_xml(xml)
    includes = [node for node in root.iter() if node.tag == "include-fragment"
                and (node.get("fragment-id") == TRUSTED_INGRESS_ID or node.get("id") == TRUSTED_INGRESS_ID)]
    if not includes:
        return xml
    inbound = root.findall("inbound")
    if (root.tag != "policies" or len(inbound) != 1 or len(includes) != 1
            or includes[0] not in list(inbound[0])
            or includes[0].attrib != {"id": TRUSTED_INGRESS_ID, "fragment-id": TRUSTED_INGRESS_ID}
            or list(includes[0]) or includes[0].text and includes[0].text.strip()
            or any(node is not includes[0] and node.get("id") == TRUSTED_INGRESS_ID for node in root.iter())):
        raise ValueError("Trusted ingress requires one fixed include directly under common inbound")
    expected = _fragment_node(render_trusted_ingress_fragment())
    parsed = expected if fragment is None else _fragment_node(fragment)
    if _structure(parsed) != _structure(expected):
        raise ValueError("Trusted ingress fragment differs from its fixed source")
    include = includes[0]
    position = list(inbound[0]).index(include)
    replacement = [copy.deepcopy(node) for node in parsed]
    inbound[0].remove(include)
    for offset, node in enumerate(replacement):
        inbound[0].insert(position + offset, node)
    replacement[-1].tail = include.tail
    return ET.tostring(root, encoding="unicode")
