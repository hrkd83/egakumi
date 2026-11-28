"""STEP4と標準トークン検証のエラー処理を、同名の正本から生成・完全照合する。"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .paths import REPOSITORY
from .apim_audit import render_audit_policy, _with_source_line_comments
from .credential_login import key_only_gateway_telemetry


COMMON_AUTHENTICATION_FRAGMENT_ID = "core-common-authentication"
ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID = "core-entra-authentication-error"
STAGING_ACCESS_ROLE = "Gateway.Staging.Access"
OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER = "__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__"
AUTHENTICATION_FRAGMENT_IDS = (COMMON_AUTHENTICATION_FRAGMENT_ID, ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID)
COMMON_AUTHENTICATION_SECTIONS = (
    "key-request-validation", "bearer-request-validation", "entra-identity",
    "entra-authentication-result", "entra-failure-subject",
)
AUTHENTICATION_FRAGMENT_FILES = {
    identifier: "fragments/authentication/" + identifier + "/" + identifier + ".xml"
    for identifier in AUTHENTICATION_FRAGMENT_IDS
}
COMMON_AUTHENTICATION_SOURCE = "fragments/authentication/core-common-authentication/core-common-authentication.cs"


def extract_common_authentication_sections(source: str) -> dict[str, str]:
    """BEGIN/ENDの固定5区画だけを読む。重複・未知区画・閉じ忘れや区画外コードは拒否する。"""
    if not isinstance(source, str):
        raise ValueError("Authentication expression source must be text")
    source = source.replace("\r\n", "\n")
    delimiters = re.findall(r"(?m)^// (?:BEGIN|END)\b[^\n]*$", source)
    if len(delimiters) != 2 * len(COMMON_AUTHENTICATION_SECTIONS) or any(
            re.fullmatch(r"// (?:BEGIN|END) (?:" + "|".join(COMMON_AUTHENTICATION_SECTIONS) + r")", line) is None
            for line in delimiters):
        raise ValueError("Authentication section delimiters must use only the fixed five names")
    pattern = re.compile(r"(?m)^// BEGIN ([a-z-]+)\n")
    sections = {}
    position = 0
    for match in pattern.finditer(source):
        if match.start() < position:
            raise ValueError("Authentication sections cannot be nested")
        outside = source[position:match.start()]
        if any(line.strip() and not line.lstrip().startswith("//") for line in outside.splitlines()):
            raise ValueError("Authentication code must belong to a named section")
        name = match.group(1)
        if name not in COMMON_AUTHENTICATION_SECTIONS or name in sections:
            raise ValueError("Authentication sections must have their fixed unique names")
        end = re.search(r"(?m)^// END " + re.escape(name) + r"(?:\n|$)", source[match.end():])
        if end is None:
            raise ValueError("Authentication sections need their matching END")
        end_start = match.end() + end.start()
        body = source[match.end():end_start]
        if "// BEGIN " in body or "// END " in body or not body.endswith("\n"):
            raise ValueError("Authentication sections cannot contain section delimiters")
        sections[name] = body[:-1]
        position = match.end() + end.end()
    if set(sections) != set(COMMON_AUTHENTICATION_SECTIONS):
        raise ValueError("Authentication source must contain all five fixed sections")
    if any(line.strip() and not line.lstrip().startswith("//") for line in source[position:].splitlines()):
        raise ValueError("Authentication code must belong to a named section")
    if re.search(r"(?m)^// (?:BEGIN|END) ", source[position:]):
        raise ValueError("Authentication source contains an unmatched delimiter")
    return sections


def _xml(value: str) -> ET.Element:
    if not isinstance(value, str) or any(token in value.upper() for token in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("Authentication fragments require safe XML")
    try:
        return ET.fromstring(value)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("Authentication fragments require valid XML") from None


def _structure(root):
    def content(value):
        return value if value and value.strip() else ""
    return (root.tag, tuple(sorted(root.attrib.items())), content(root.text),
            tuple((_structure(child), content(child.tail)) for child in root))


def _fragment(identifier: str, value: str) -> ET.Element:
    root = _xml(value)
    if (root.tag != "fragment" or root.attrib or len(root) != 1
            or root[0].tag != "choose" or root[0].attrib != {"id": identifier}
            or root.findall(".//include-fragment") or len(value.encode("utf-8")) > 512 * 1024):
        raise ValueError("Authentication fragments must contain only their fixed decision without nested includes")
    return root


def _telemetry() -> str:
    directory = REPOSITORY / "apps/api-gateway/policies"
    value = (directory / "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml").read_text(encoding="utf-8")
    return key_only_gateway_telemetry(value.replace("<fragment>", "").replace("</fragment>", ""), source_comments="compact")


def _render_telemetry(value: str) -> str:
    telemetry = _telemetry()
    value = value.replace("__AUTHENTICATION_TELEMETRY_XML__", telemetry)
    if "__ENTRA_FAILURE_TELEMETRY_XML__" in value:
        failure = render_audit_policy(telemetry, source_comments="compact")
        reader = '"gatewayAuthenticatedSubject"'
        if failure.count(reader) != 1:
            raise ValueError("Failure telemetry requires its fixed subject reader")
        value = value.replace("__ENTRA_FAILURE_TELEMETRY_XML__",
                              failure.replace(reader, '"gatewayEntraFailureSubject"', 1))
    return render_audit_policy(value, source_comments="compact")


def render_common_authentication_fragment(*, entra_enabled: bool, required_app_role: str = "") -> str:
    """同名XMLへ同名CSの5区画を差し込み、現在と同じ共通認証を生成する。"""
    if type(entra_enabled) is not bool:
        raise ValueError("Authentication fragment rendering requires an explicit Boolean state")
    if required_app_role and (not entra_enabled or required_app_role != STAGING_ACCESS_ROLE):
        raise ValueError("Authentication fragment requires the fixed staging access role")
    directory = REPOSITORY / "apps/api-gateway/policies"
    value = (directory / AUTHENTICATION_FRAGMENT_FILES[COMMON_AUTHENTICATION_FRAGMENT_ID]).read_text(encoding="utf-8")
    sections = extract_common_authentication_sections((directory / COMMON_AUTHENTICATION_SOURCE).read_text(encoding="utf-8"))
    for section, body in sections.items():
        marker = "__CORE_COMMON_AUTHENTICATION_" + section.upper().replace("-", "_") + "_CSHARP__"
        if value.count(marker) != 1:
            raise ValueError("Each authentication section needs exactly one XML insertion point")
        value = value.replace(marker, escape(_with_source_line_comments(body), {'"': '&quot;', "'": '&apos;'}), 1)
    start, end = "<!-- core-entra-branch-start -->", "<!-- core-entra-branch-end -->"
    if value.count(start) != 1 or value.count(end) != 1:
        raise ValueError("Authentication XML needs its fixed Entra branch boundary")
    if not entra_enabled:
        before, remaining = value.split(start)
        _, after = remaining.split(end)
        value = before + after
    value = value.replace("__ENTRA_REQUIRED_APP_ROLE__", (
        f'<claim name="roles" match="any"><value>{required_app_role}</value></claim>' if required_app_role else ""
    ))
    value = value.replace("__ENTRA_OPEN_PRODUCT_ID__", "core-entra-model-users")
    value = _render_telemetry(value)
    if "__CORE_COMMON_AUTHENTICATION_" in value:
        raise ValueError("Authentication XML has an unresolved expression")
    _fragment(COMMON_AUTHENTICATION_FRAGMENT_ID, value)
    return value


def render_entra_authentication_error_fragment() -> str:
    """標準Entra検証の失敗だけを、現行の監査と401応答へつなげる。"""
    directory = REPOSITORY / "apps/api-gateway/policies"
    value = _render_telemetry((directory / AUTHENTICATION_FRAGMENT_FILES[ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID]).read_text(encoding="utf-8"))
    _fragment(ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID, value)
    return value


def render_common_authentication_fragments(*, entra_enabled: bool, required_app_role: str = "") -> dict[str, str]:
    fragments = {COMMON_AUTHENTICATION_FRAGMENT_ID: render_common_authentication_fragment(
        entra_enabled=entra_enabled, required_app_role=required_app_role)}
    if entra_enabled:
        fragments[ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID] = render_entra_authentication_error_fragment()
    return fragments


def render_operation_entra_authentication_error(xml: str, *, entra_enabled: bool = False) -> str:
    """専用on-errorの先頭へ、有効時だけ固定のEntraエラー部品を呼び出す。"""
    if type(entra_enabled) is not bool or not isinstance(xml, str):
        raise ValueError("Operation authentication errors require an explicit Boolean state and XML")
    count = xml.count(OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER)
    if not count:
        return xml
    root = _xml(xml)
    errors = root.find("on-error")
    if (count != 1 or root.tag != "policies" or errors is None
            or re.match(r"^\s*" + re.escape(OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER) + r"(?:\s|$)",
                        errors.text or "") is None):
        raise ValueError("Operation authentication errors require one marker before their dedicated error handling")
    identifier = ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID
    include = '<include-fragment id="' + identifier + '" fragment-id="' + identifier + '" />'
    return xml.replace(OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER, include if entra_enabled else "")


def expand_operation_entra_authentication_error(xml: str) -> str:
    """実式試験用に、操作on-error先頭の固定エラー部品だけを正本から展開する。"""
    root = _xml(xml)
    includes = [node for node in root.iter("include-fragment")
                if node.get("fragment-id") in AUTHENTICATION_FRAGMENT_IDS or node.get("id") in AUTHENTICATION_FRAGMENT_IDS]
    if not includes:
        return xml
    identifier = ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID
    errors = root.find("on-error")
    if (root.tag != "policies" or errors is None or len(includes) != 1 or not len(errors)
            or errors[0] is not includes[0] or includes[0].attrib != {"id": identifier, "fragment-id": identifier}
            or len(includes[0]) or includes[0].text and includes[0].text.strip()
            or any(node is not includes[0] and node.get("id") == identifier for node in root.iter())):
        raise ValueError("Operation expansion permits only one fixed authentication error include first in on-error")
    source = _fragment(identifier, render_entra_authentication_error_fragment())
    replacement = copy.deepcopy(source[0])
    replacement.tail = includes[0].tail
    errors.remove(includes[0])
    errors.insert(0, replacement)
    return ET.tostring(root, encoding="unicode")


def expand_common_authentication_fragments(xml: str, fragments: dict[str, str], *, entra_enabled: bool,
                                           required_app_role: str = "") -> str:
    """明示された信頼済み辞書を固定生成元と完全照合してから、Globalの固定位置へ展開する。"""
    root = _xml(xml)
    if root.tag != "policies":
        if any(node.get("fragment-id") in AUTHENTICATION_FRAGMENT_IDS for node in root.iter("include-fragment")):
            raise ValueError("Authentication fragments cannot be nested in another fragment")
        return xml
    includes = [node for node in root.iter("include-fragment")
                if node.get("fragment-id") in AUTHENTICATION_FRAGMENT_IDS or node.get("id") in AUTHENTICATION_FRAGMENT_IDS]
    if not includes:
        return xml
    expected = render_common_authentication_fragments(entra_enabled=entra_enabled, required_app_role=required_app_role)
    if not isinstance(fragments, dict) or set(fragments) != set(expected):
        raise ValueError("Authentication expansion needs its complete trusted fragment dictionary")
    parsed = {}
    for identifier, value in fragments.items():
        fragment = _fragment(identifier, value)
        fixed = _fragment(identifier, expected[identifier])
        if _structure(fragment) != _structure(fixed):
            raise ValueError("Authentication fragment differs from its fixed generated source")
        parsed[identifier] = fragment
    expected_ids = list(expected)
    if [node.get("fragment-id") for node in includes] != expected_ids:
        raise ValueError("Authentication fragments must execute once in their fixed sections")
    inbound = root.find("inbound")
    sequence = ["core-request-context", "core-trusted-ingress", "core-credential-browser-exception",
                COMMON_AUTHENTICATION_FRAGMENT_ID, "core-audit-authenticated-subject"]
    if inbound is None or [node.get("id") for node in list(inbound)[:5]] != sequence:
        raise ValueError("Authentication requires the fixed common processing order")
    if entra_enabled:
        errors = root.find("on-error")
        if errors is None or [node.get("id") for node in list(errors)[:2]] != ["core-error-request-id", ENTRA_AUTHENTICATION_ERROR_FRAGMENT_ID]:
            raise ValueError("Token validation errors require request correlation before their fixed handler")
    for include in includes:
        identifier = include.get("fragment-id")
        section = root.find("inbound" if identifier == COMMON_AUTHENTICATION_FRAGMENT_ID else "on-error")
        if (section is None or include not in list(section) or include.attrib != {"id": identifier, "fragment-id": identifier}
                or len(include) or include.text and include.text.strip()
                or any(node is not include and node.get("id") == identifier for node in root.iter())):
            raise ValueError("Authentication fragments require their fixed global call sites")
        position = list(section).index(include)
        replacement = copy.deepcopy(parsed[identifier][0])
        replacement.tail = include.tail
        section.remove(include)
        section.insert(position, replacement)
    return ET.tostring(root, encoding="unicode")
