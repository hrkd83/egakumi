"""APIM 監査式の共通主体検査を、公開XMLの生成時だけ展開する。"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .paths import REPOSITORY


AUTHENTICATED_SUBJECT_MARKER = "__AUDIT_AUTHENTICATED_SUBJECT_CSHARP__"
SUBJECT_OBSERVATION_MARKER = "__AUDIT_SUBJECT_OBSERVATION_CSHARP__"
REQUEST_CONTEXT_MARKER = "__AUDIT_REQUEST_CONTEXT_CSHARP__"
CORE_REQUEST_CONTEXT_MARKER = "__CORE_REQUEST_CONTEXT_CSHARP__"
CORE_AUTHENTICATED_SUBJECT_MARKER = "__CORE_AUTHENTICATED_SUBJECT_CSHARP__"
CORE_GATEWAY_TELEMETRY_MARKER = "__CORE_GATEWAY_TELEMETRY_CSHARP__"
CORE_TRUSTED_INGRESS_MARKER = "__CORE_TRUSTED_INGRESS_CSHARP__"
CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER = "__CORE_CREDENTIAL_BROWSER_EXCEPTION_CSHARP__"
CORE_API_SETTINGS_MARKER = "__CORE_API_SETTINGS_CSHARP__"
CORE_API_INPUT_VALIDATION_MARKER = "__CORE_API_INPUT_VALIDATION_CSHARP__"
CORE_API_OPERATION_SETTINGS_MARKER = "__CORE_API_OPERATION_SETTINGS_CSHARP__"
CORE_API_AUTHENTICATION_RESULT_MARKER = "__CORE_API_AUTHENTICATION_RESULT_CSHARP__"
CORE_MODEL_LIMITS_MARKER = "__CORE_MODEL_LIMITS_CSHARP__"
CORE_SPEECH_SSML_GUARD_MARKER = "__CORE_SPEECH_SSML_GUARD_CSHARP__"
CORE_QUERY_OBSERVATION_MARKER = "__CORE_QUERY_OBSERVATION_CSHARP__"
CORE_QUERY_TELEMETRY_MARKER = "__CORE_QUERY_TELEMETRY_CSHARP__"
CORE_QUERY_TELEMETRY_PREDICATE_MARKER = "__CORE_QUERY_TELEMETRY_PREDICATE_CSHARP__"
QUERY_TELEMETRY_SECTIONS = {
    "predicate": CORE_QUERY_TELEMETRY_PREDICATE_MARKER,
    "message": CORE_QUERY_TELEMETRY_MARKER,
}
AUDIT_FRAGMENT_FILES = {
    "core-request-context": ("fragments/audit/core-request-context/core-request-context.xml", "gatewayRequestContext"),
    "core-audit-authenticated-subject": ("fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml", "gatewayAuthenticatedSubject"),
}
SOURCE_EXPRESSION_FILES = {
    CORE_REQUEST_CONTEXT_MARKER: "fragments/audit/core-request-context/core-request-context.cs",
    CORE_AUTHENTICATED_SUBJECT_MARKER: "fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs",
    CORE_GATEWAY_TELEMETRY_MARKER: "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs",
    CORE_TRUSTED_INGRESS_MARKER: "fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs",
    CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER: "fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs",
    CORE_API_SETTINGS_MARKER: "fragments/settings/core-api-settings/core-api-settings.cs",
    CORE_API_INPUT_VALIDATION_MARKER: "fragments/request/core-api-input-validation/core-api-input-validation.cs",
    CORE_API_OPERATION_SETTINGS_MARKER: "fragments/request/core-api-operation-settings/core-api-operation-settings.cs",
    CORE_API_AUTHENTICATION_RESULT_MARKER: "fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs",
    CORE_MODEL_LIMITS_MARKER: "fragments/request/core-model-limits/core-model-limits.cs",
    CORE_SPEECH_SSML_GUARD_MARKER: "fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs",
    CORE_QUERY_OBSERVATION_MARKER: "fragments/request/core-query-observation/core-query-observation.cs",
    CORE_QUERY_TELEMETRY_MARKER: "fragments/audit/core-query-telemetry/core-query-telemetry.cs",
    AUTHENTICATED_SUBJECT_MARKER: "expressions/legacy/audit-authenticated-subject.cs",
    SUBJECT_OBSERVATION_MARKER: "expressions/legacy/audit-subject-observation.cs",
    REQUEST_CONTEXT_MARKER: "expressions/legacy/request-context.cs",
}
COMPACT_SOURCE_COMMENT = "/* 監査の説明はフラグメント core-gateway-telemetry を参照。 */"


def _policy_xml(xml: str) -> ET.Element:
    if not isinstance(xml, str) or any(mark in xml.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("Audit fragments require safe policy XML")
    try:
        return ET.fromstring(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("Audit fragments require valid policy XML") from None


def _fragment_node(identifier: str, xml: str) -> ET.Element:
    root = _policy_xml(xml)
    children = [node for node in root if isinstance(node.tag, str)]
    variable = AUDIT_FRAGMENT_FILES[identifier][1]
    if (root.tag != "fragment" or root.attrib or len(children) != 1
            or children[0].tag != "set-variable" or list(children[0])
            or set(children[0].attrib) != {"id", "name", "value"}
            or children[0].get("id") != identifier or children[0].get("name") != variable
            or not children[0].get("value", "").strip().startswith("@{")
            or not children[0].get("value", "").strip().endswith("}")
            or len(xml.encode("utf-8")) > 512 * 1024):
        raise ValueError("Audit fragments must contain only their fixed variable writer")
    return root


def _without_source_line_comments(source: str) -> str:
    """旧生成元の照合専用。直前の配備で除外していた説明行だけを除く。"""
    return "\n".join(line for line in source.split("\n") if not line.lstrip().startswith("//"))


def _with_source_line_comments(source: str) -> str:
    """XML属性の改行正規化でも後続コードを隠さないよう、説明の//を閉じた/* */へ変換する。"""
    lines = []
    for line in source.split("\n"):
        text = line.lstrip()
        if text.startswith("//"):
            indent = line[:len(line) - len(text)]
            line = indent + "/*" + text[2:].replace("*/", "* /") + " */"
        lines.append(line)
    return "\n".join(lines)


def _source_expression(source: str, mode: str) -> str:
    if mode == "full":
        return _with_source_line_comments(source)
    if mode == "compact":
        return COMPACT_SOURCE_COMMENT + "\n" + _without_source_line_comments(source)
    if mode == "omit":
        return _without_source_line_comments(source)
    raise ValueError("Audit source comments require full, compact or legacy omit mode")


def _without_telemetry_explanation_lines(xml: str) -> str:
    """複製時だけ監査の正本へ追加した説明行を除き、既存コメントとコードを保つ。"""
    return "\n".join(line for line in xml.split("\n") if not line.lstrip().startswith("/* 説明："))


def portal_source_explanation_blocks() -> dict[str, tuple[str, ...]]:
    """移行照合が認識できる、現在の正本から生成した既知の説明ブロックだけを返す。"""
    directory = REPOSITORY / "apps/gateway/apim/policies"
    blocks = {}
    for marker, filename in SOURCE_EXPRESSION_FILES.items():
        source = (directory / filename).read_text(encoding="utf-8")
        explanation = "\n".join(line for line in source.split("\n") if line.lstrip().startswith("//"))
        blocks[marker] = tuple(line.strip() for line in _with_source_line_comments(explanation).split("\n") if line.strip())
    return blocks


def extract_query_telemetry_sections(source: str) -> dict[str, str]:
    """同名C#のpredicate/messageだけを読み、区画外コード・重複・閉じ忘れを拒否する。"""
    if not isinstance(source, str):
        raise ValueError("Query telemetry expression source must be text")
    source = source.replace("\r\n", "\n")
    sections, active, lines = {}, None, []
    for line in source.split("\n"):
        delimiter = re.fullmatch(r"// (BEGIN|END) (predicate|message)", line)
        if delimiter:
            action, name = delimiter.groups()
            if action == "BEGIN":
                if active is not None or name in sections:
                    raise ValueError("Query telemetry sections must be unique and cannot be nested")
                active, lines = name, []
            else:
                if active != name or not lines or not any(
                        item.strip() and not item.lstrip().startswith("//") for item in lines):
                    raise ValueError("Query telemetry sections need matching END and executable content")
                sections[name] = "\n".join(lines)
                active, lines = None, []
        elif re.match(r"// (?:BEGIN|END)(?:\s|$)", line.lstrip()):
            raise ValueError("Query telemetry section delimiters must use only the fixed two names")
        elif active is not None:
            lines.append(line)
        elif line.strip() and not line.lstrip().startswith("//"):
            raise ValueError("Query telemetry code must belong to a named section")
    if active is not None or tuple(sections) != tuple(QUERY_TELEMETRY_SECTIONS):
        raise ValueError("Query telemetry source must contain predicate and message in their fixed order")
    return sections


def _render_query_telemetry_sections(xml: str, source_comments: str) -> str:
    """同名XMLの2式へ区画を埋め込み、利用者情報の共通式も説明付きで展開する。"""
    if not any(marker in xml for marker in QUERY_TELEMETRY_SECTIONS.values()):
        return xml
    if any(xml.count(marker) != 1 for marker in QUERY_TELEMETRY_SECTIONS.values()):
        raise ValueError("Each query telemetry section needs exactly one XML insertion point")
    directory = REPOSITORY / "apps/gateway/apim/policies"
    source = (directory / SOURCE_EXPRESSION_FILES[CORE_QUERY_TELEMETRY_MARKER]).read_text(encoding="utf-8")
    sections = extract_query_telemetry_sections(source)
    if sections["predicate"].count(SUBJECT_OBSERVATION_MARKER) or sections["message"].count(SUBJECT_OBSERVATION_MARKER) != 1:
        raise ValueError("Query telemetry must read the shared subject only once in its message section")
    # compactは他部品への複製用です。この正本では共通の利用者情報の説明も保持します。
    mode = "omit" if source_comments == "omit" else "full"
    subject = (directory / SOURCE_EXPRESSION_FILES[SUBJECT_OBSERVATION_MARKER]).read_text(encoding="utf-8")
    for name, marker in QUERY_TELEMETRY_SECTIONS.items():
        body = sections[name].replace(SUBJECT_OBSERVATION_MARKER, _source_expression(subject, mode))
        encoded = escape(_source_expression(body, mode), {'"': "&quot;", "'": "&apos;"})
        xml = re.sub(r"(?m)^([ \t]*)" + marker,
                     lambda match: match[1] + encoded.replace("\n", "\n" + match[1]), xml)
        if marker in xml:
            raise ValueError("Query telemetry C# insertion points must occupy their own line")
    return xml


def render_audit_policy(xml: str, *, source_comments: str = "full") -> str:
    """生C#と説明をXMLへ埋め込む。compactは複製する主体監査だけ、omitは旧生成元照合だけに使う。"""
    if source_comments not in {"full", "compact", "omit"}:
        raise ValueError("Audit source comments require full, compact or legacy omit mode")
    xml = _render_query_telemetry_sections(xml, source_comments)
    if source_comments in {"compact", "omit"} and CORE_GATEWAY_TELEMETRY_MARKER in xml:
        xml = _without_telemetry_explanation_lines(xml)
    indented_markers = {REQUEST_CONTEXT_MARKER, CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER, CORE_API_SETTINGS_MARKER, CORE_API_INPUT_VALIDATION_MARKER, CORE_API_OPERATION_SETTINGS_MARKER, CORE_API_AUTHENTICATION_RESULT_MARKER, CORE_MODEL_LIMITS_MARKER, CORE_SPEECH_SSML_GUARD_MARKER, CORE_QUERY_OBSERVATION_MARKER}
    observation_markers = {SUBJECT_OBSERVATION_MARKER, CORE_GATEWAY_TELEMETRY_MARKER}
    for marker, filename in SOURCE_EXPRESSION_FILES.items():
        if marker in indented_markers:
            continue
        if marker in xml:
            source = (REPOSITORY / "apps/gateway/apim/policies" / filename).read_text(encoding="utf-8")
            mode = "full" if source_comments == "compact" and marker not in observation_markers else source_comments
            xml = xml.replace(marker, escape(_source_expression(source, mode), {'"': "&quot;", "'": "&apos;"}))
    for marker in indented_markers:
        if marker not in xml:
            continue
        mode = "omit" if source_comments == "omit" else "full"
        source = _source_expression((REPOSITORY / "apps/gateway/apim/policies" / SOURCE_EXPRESSION_FILES[marker]).read_text(encoding="utf-8"), mode).rstrip("\n")
        encoded = escape(source, {'"': "&quot;", "'": "&apos;"})
        # The new request expression follows its marker's column. Existing audit
        # markers retain their original bytes for fixed old-source readback.
        xml = re.sub(r"(?m)^([ \t]*)" + marker,
                     lambda match: match[1] + encoded.replace("\n", "\n" + match[1]), xml)
        if marker in xml:
            raise ValueError("Indented C# expression markers must occupy their own line")
    return xml


def render_audit_fragments(*, source_comments: str = "full") -> dict[str, str]:
    """監査2部品の説明を保持する。omit指定は既知の旧生成元照合だけに使う。"""
    fragments = {}
    for identifier, (filename, _) in AUDIT_FRAGMENT_FILES.items():
        rendered = render_audit_policy((REPOSITORY / "apps/gateway/apim/policies" / filename).read_text(encoding="utf-8"),
                                      source_comments=source_comments)
        _fragment_node(identifier, rendered)
        fragments[identifier] = rendered
    return fragments


def expand_audit_fragments(xml: str, fragments: dict[str, str] | None = None) -> str:
    """実式試験と生成元照合で、既知の監査2部品だけを元の位置へ展開する。"""
    root = _policy_xml(xml)
    includes = [node for node in root.iter()
                if node.tag == "include-fragment" and node.get("fragment-id") in AUDIT_FRAGMENT_FILES]
    if not includes:
        return xml
    if root.tag == "fragment":
        raise ValueError("Audit fragments cannot be nested in another fragment")
    fragments = render_audit_fragments() if fragments is None else fragments
    if not isinstance(fragments, dict) or set(fragments) != set(AUDIT_FRAGMENT_FILES):
        raise ValueError("Both fixed audit fragment sources are required")
    parsed = {identifier: _fragment_node(identifier, value) for identifier, value in fragments.items()}
    for parent in list(root.iter()):
        for include in list(parent):
            identifier = include.get("fragment-id") if include.tag == "include-fragment" else None
            if identifier not in parsed:
                continue
            if (set(include.attrib) != {"id", "fragment-id"} or include.get("id") != identifier
                    or list(include) or (include.text and include.text.strip())):
                raise ValueError("Audit fragments require their fixed include identity")
            position = list(parent).index(include)
            replacement = [copy.deepcopy(node) for node in parsed[identifier]]
            parent.remove(include)
            for offset, node in enumerate(replacement):
                parent.insert(position + offset, node)
            replacement[-1].tail = include.tail
    return ET.tostring(root, encoding="unicode")
