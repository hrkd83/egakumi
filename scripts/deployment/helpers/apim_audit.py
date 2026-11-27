"""APIM 監査式の共通主体検査を、公開XMLの生成時だけ展開する。"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .paths import REPOSITORY


# 旧observerの入れ子を生成時に拒否するため、名前だけを保持します。sourceへの対応はありません。
SUBJECT_OBSERVATION_MARKER = "__AUDIT_SUBJECT_OBSERVATION_CSHARP__"
CORE_REQUEST_CONTEXT_MARKER = "__CORE_REQUEST_CONTEXT_CSHARP__"
CORE_AUTHENTICATED_SUBJECT_MARKER = "__CORE_AUTHENTICATED_SUBJECT_CSHARP__"
CORE_GATEWAY_TELEMETRY_MARKER = "__CORE_GATEWAY_TELEMETRY_CSHARP__"
CORE_BACKEND_AUDIT_MARKER = "__CORE_BACKEND_AUDIT_CSHARP__"
CORE_AUDIT_BODY_MODEL_MARKER = "__CORE_AUDIT_BODY_MODEL_CSHARP__"
CORE_TRUSTED_INGRESS_MARKER = "__CORE_TRUSTED_INGRESS_CSHARP__"
CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER = "__CORE_CREDENTIAL_BROWSER_EXCEPTION_CSHARP__"
CORE_CREDENTIAL_CALLER_MARKER = "__CORE_CREDENTIAL_CALLER_CSHARP__"
CORE_CREDENTIAL_USE_RESULT_MARKER = "__CORE_CREDENTIAL_USE_RESULT_CSHARP__"
CREDENTIAL_CHALLENGE_RESULT_MARKER = "__CREDENTIAL_CHALLENGE_RESULT_CSHARP__"
CREDENTIAL_LOGIN_START_CALLER_MARKER = "__CREDENTIAL_LOGIN_START_CALLER_CSHARP__"
CREDENTIAL_LOGIN_STATUS_CALLER_MARKER = "__CREDENTIAL_LOGIN_STATUS_CALLER_CSHARP__"
CREDENTIAL_LOGIN_DISCONNECT_CALLER_MARKER = "__CREDENTIAL_LOGIN_DISCONNECT_CALLER_CSHARP__"
CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_MARKER = "__CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_CSHARP__"
CREDENTIAL_LOGIN_LOGOUT_CALLER_MARKER = "__CREDENTIAL_LOGIN_LOGOUT_CALLER_CSHARP__"
CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_MARKER = "__CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_CSHARP__"
REALTIME_OWNER_CONTEXT_MARKER = "__REALTIME_OWNER_CONTEXT_CSHARP__"
REALTIME_CREATE_TICKET_MARKER = "__REALTIME_CREATE_TICKET_CSHARP__"
REALTIME_HANGUP_OWNER_MARKER = "__REALTIME_HANGUP_OWNER_CSHARP__"
REALTIME_OBSERVER_OWNER_MARKER = "__REALTIME_OBSERVER_OWNER_CSHARP__"
REALTIME_OWNER_CONTEXT_XML_MARKER = "__REALTIME_OWNER_CONTEXT_XML__"
CORE_API_SETTINGS_MARKER = "__CORE_API_SETTINGS_CSHARP__"
CORE_API_INPUT_VALIDATION_MARKER = "__CORE_API_INPUT_VALIDATION_CSHARP__"
CORE_API_OPERATION_SETTINGS_MARKER = "__CORE_API_OPERATION_SETTINGS_CSHARP__"
CORE_API_AUTHENTICATION_RESULT_MARKER = "__CORE_API_AUTHENTICATION_RESULT_CSHARP__"
CORE_API_TARGET_VALIDATION_MARKER = "__CORE_API_TARGET_VALIDATION_CSHARP__"
CORE_CHAT_STORE_DISABLED_MARKER = "__CORE_CHAT_STORE_DISABLED_CSHARP__"
CORE_MODEL_LIMITS_MARKER = "__CORE_MODEL_LIMITS_CSHARP__"
CORE_RATE_LIMIT_MARKER = "__CORE_RATE_LIMIT_CSHARP__"
CORE_TOKEN_LIMITS_MARKER = "__CORE_TOKEN_LIMITS_CSHARP__"
CORE_ENTRA_USER_PROFILE_MARKER = "__CORE_ENTRA_USER_PROFILE_CSHARP__"
RATE_LIMIT_ERROR_RETRY_AFTER_MARKER = "__RATE_LIMIT_ERROR_RETRY_AFTER_CSHARP__"
MCP_RATE_CLEANUP_MARKER = "__MCP_RATE_CLEANUP_CSHARP__"
CREDENTIAL_TELEMETRY_MARKER = "__CREDENTIAL_TELEMETRY_CSHARP__"
CORE_CREDENTIAL_CONNECTION_OUTBOUND_MARKER = "__CORE_CREDENTIAL_CONNECTION_OUTBOUND_CSHARP__"
CORE_SPEECH_SSML_GUARD_MARKER = "__CORE_SPEECH_SSML_GUARD_CSHARP__"
CORE_DOCUMENT_INPUT_VALIDATION_MARKER = "__CORE_DOCUMENT_INPUT_VALIDATION_CSHARP__"
CORE_QUERY_OBSERVATION_MARKER = "__CORE_QUERY_OBSERVATION_CSHARP__"
CORE_QUERY_TELEMETRY_MARKER = "__CORE_QUERY_TELEMETRY_CSHARP__"
CORE_QUERY_TELEMETRY_PREDICATE_MARKER = "__CORE_QUERY_TELEMETRY_PREDICATE_CSHARP__"
QUERY_TELEMETRY_SECTIONS = {
    "predicate": CORE_QUERY_TELEMETRY_PREDICATE_MARKER,
    "message": CORE_QUERY_TELEMETRY_MARKER,
}
SECTION_TEXT_MARKERS = {"__GATEWAY_MODEL_ALIAS_BODY_CSHARP__","__GATEWAY_HOSTED_BODY_CSHARP__","__GATEWAY_RESPONSES_BODY_CSHARP__","__REALTIME_CREATE_CLIENT_SECRETS_REQUEST_CSHARP__","__CREDENTIAL_CHALLENGE_RESPONSE_BODY_CSHARP__","__PII_OUTPUT_CSHARP__"}
SECTION_EXPRESSION_FILES = {
    "api-templates/shared/stream.cs": {
        "authentication-result": "__PROTOCOL_AUTHENTICATION_RESULT_CSHARP__",
    },
    "api-templates/ws/ws.cs": {
        "model-selection": "__PROTOCOL_MODEL_SELECTION_CSHARP__",
        "audit-model-setting": "__PROTOCOL_AUDIT_MODEL_SETTING_CSHARP__",
        "query-validation": "__PROTOCOL_QUERY_VALIDATION_CSHARP__",
    },
    "api-templates/mcp/generated.cs": {
        "gateway-decision": "__MCP_GATEWAY_DECISION_CSHARP__",
        "pii-arguments": "__MCP_PII_ARGUMENTS_CSHARP__",
    },
    "api-templates/rest/rest.cs": {
        "model-alias-body": "__GATEWAY_MODEL_ALIAS_BODY_CSHARP__",
        "hosted-body": "__GATEWAY_HOSTED_BODY_CSHARP__",
        "responses-body": "__GATEWAY_RESPONSES_BODY_CSHARP__",
    },
    "operation-policies/realtime/realtime-create.cs": {
        "sdp": "__REALTIME_CREATE_SDP_CSHARP__",
        "client-secrets-request": "__REALTIME_CREATE_CLIENT_SECRETS_REQUEST_CSHARP__",
        "ephemeral-token": "__REALTIME_CREATE_EPHEMERAL_TOKEN_CSHARP__",
        "call-id": "__REALTIME_CREATE_CALL_ID_CSHARP__",
        "ticket": "__REALTIME_CREATE_TICKET_CSHARP__",
        "answer-status": "__REALTIME_CREATE_ANSWER_STATUS_CSHARP__",
    },
    "operation-policies/credential-login/disconnect.cs": {
        "caller": "__CREDENTIAL_LOGIN_DISCONNECT_CALLER_CSHARP__",
        "session-request": "__CREDENTIAL_LOGIN_DISCONNECT_SESSION_REQUEST_CSHARP__",
    },
    "operation-policies/credential-login/logout-all.cs": {
        "caller": "__CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_CSHARP__",
        "session-request": "__CREDENTIAL_LOGIN_LOGOUT_ALL_SESSION_REQUEST_CSHARP__",
    },
    "operation-policies/credential-login/logout.cs": {
        "caller": "__CREDENTIAL_LOGIN_LOGOUT_CALLER_CSHARP__",
        "session-request": "__CREDENTIAL_LOGIN_LOGOUT_SESSION_REQUEST_CSHARP__",
    },
    "operation-policies/credential-login/session-commit.cs": {
        "caller": "__CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_CSHARP__",
        "session-request": "__CREDENTIAL_LOGIN_SESSION_COMMIT_SESSION_REQUEST_CSHARP__",
    },
    "snippets/authentication/credential-challenge/credential-challenge.cs": {
        "result": "__CREDENTIAL_CHALLENGE_RESULT_CSHARP__",
        "retry-after": "__CREDENTIAL_CHALLENGE_RETRY_AFTER_CSHARP__",
        "response-body": "__CREDENTIAL_CHALLENGE_RESPONSE_BODY_CSHARP__",
    },
    "fragments/quota/core-token-limits/core-token-limits.cs": {
        "enabled": "__CORE_TOKEN_LIMITS_ENABLED_CSHARP__",
        "counter": "__CORE_TOKEN_LIMITS_CSHARP__",
    },
    "fragments/authentication/core-credential-session-use/core-credential-session-use.cs": {
        "use-result": "__CORE_CREDENTIAL_USE_RESULT_CSHARP__",
        "expires-on": "__CORE_CREDENTIAL_USE_EXPIRES_ON_CSHARP__",
    },
    "fragments/authentication/core-credential-connection/core-credential-connection.cs": {
        "caller": "__CORE_CREDENTIAL_CALLER_CSHARP__",
        "selection": "__CORE_CREDENTIAL_CONNECTION_SELECTION_CSHARP__",
        "authorize-result": "__CORE_CREDENTIAL_CONNECTION_AUTHORIZE_RESULT_CSHARP__",
        "refresh-unavailable": "__CORE_CREDENTIAL_CONNECTION_REFRESH_UNAVAILABLE_CSHARP__",
        "claims-required": "__CORE_CREDENTIAL_CONNECTION_CLAIMS_REQUIRED_CSHARP__",
        "identity-status": "__CORE_CREDENTIAL_CONNECTION_IDENTITY_STATUS_CSHARP__",
    },
    "operation-policies/document-analysis/document-analysis-status.cs": {
        "owner": "__DOCUMENT_ANALYSIS_STATUS_OWNER_CSHARP__",
        "owner-status": "__DOCUMENT_ANALYSIS_STATUS_OWNER_STATUS_CSHARP__",
        "poll-evaluation": "__DOCUMENT_ANALYSIS_STATUS_POLL_EVALUATION_CSHARP__",
    },
    "operation-policies/document-analysis/document-analysis.cs": {
        "operation-id": "__DOCUMENT_ANALYSIS_OPERATION_ID_CSHARP__",
        "accepted": "__DOCUMENT_ANALYSIS_ACCEPTED_CSHARP__",
        "status": "__DOCUMENT_ANALYSIS_STATUS_CSHARP__",
        "retry-after": "__DOCUMENT_ANALYSIS_RETRY_AFTER_CSHARP__",
    },
    "operation-policies/pii/pii.cs": {
        "input": "__PII_INPUT_STATUS_CSHARP__",
        "request-settings": "__PII_REQUEST_SETTINGS_CSHARP__",
        "request": "__PII_REQUEST_CSHARP__",
        "retry": "__PII_RETRY_AFTER_CSHARP__",
        "result": "__PII_RESULT_CSHARP__",
        "output": "__PII_OUTPUT_CSHARP__",
        "audit-settings": "__PII_AUDIT_SETTINGS_CSHARP__",
        "audit": "__PII_AUDIT_CSHARP__",
    },
    "operation-policies/live/live-create.cs": {
        "request": "__LIVE_CREATE_REQUEST_CSHARP__",
        "answer": "__LIVE_CREATE_ANSWER_CSHARP__",
    },
    "fragments/audit/core-audit-context/core-audit-context.cs": {
        "trace": "__CORE_AUDIT_CONTEXT_TRACE_CSHARP__",
        "model": "__CORE_AUDIT_CONTEXT_MODEL_CSHARP__",
    },
    "operation-policies/credential-login/callback.cs": {
        "input": "__CREDENTIAL_LOGIN_CALLBACK_INPUT_CSHARP__",
        "transaction": "__CREDENTIAL_LOGIN_CALLBACK_TRANSACTION_CSHARP__",
        "owner": "__CREDENTIAL_LOGIN_CALLBACK_OWNER_CSHARP__",
    },
}
AUDIT_FRAGMENT_FILES = {
    "core-request-context": ("fragments/audit/core-request-context/core-request-context.xml", "gatewayRequestContext"),
    "core-audit-authenticated-subject": ("fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml", "gatewayAuthenticatedSubject"),
}
SOURCE_EXPRESSION_FILES = {
    CORE_REQUEST_CONTEXT_MARKER: "fragments/audit/core-request-context/core-request-context.cs",
    CORE_AUTHENTICATED_SUBJECT_MARKER: "fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs",
    CORE_GATEWAY_TELEMETRY_MARKER: "fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs",
    CORE_BACKEND_AUDIT_MARKER: "fragments/audit/core-backend-audit/core-backend-audit.cs",
    CORE_AUDIT_BODY_MODEL_MARKER: "fragments/audit/core-audit-body-model/core-audit-body-model.cs",
    CORE_TRUSTED_INGRESS_MARKER: "fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs",
    CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER: "fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs",
    CORE_CREDENTIAL_CALLER_MARKER: "fragments/authentication/core-credential-connection/core-credential-connection.cs",
    CORE_CREDENTIAL_USE_RESULT_MARKER: "fragments/authentication/core-credential-session-use/core-credential-session-use.cs",
    CREDENTIAL_CHALLENGE_RESULT_MARKER: "snippets/authentication/credential-challenge/credential-challenge.cs",
    CREDENTIAL_LOGIN_START_CALLER_MARKER: "operation-policies/credential-login/start.cs",
    CREDENTIAL_LOGIN_STATUS_CALLER_MARKER: "operation-policies/credential-login/status.cs",
    CREDENTIAL_LOGIN_DISCONNECT_CALLER_MARKER: "operation-policies/credential-login/disconnect.cs",
    CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_MARKER: "operation-policies/credential-login/logout-all.cs",
    CREDENTIAL_LOGIN_LOGOUT_CALLER_MARKER: "operation-policies/credential-login/logout.cs",
    CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_MARKER: "operation-policies/credential-login/session-commit.cs",
    REALTIME_OWNER_CONTEXT_MARKER: "snippets/authentication/realtime-owner-context/realtime-owner-context.cs",
    REALTIME_CREATE_TICKET_MARKER: "operation-policies/realtime/realtime-create.cs",
    REALTIME_HANGUP_OWNER_MARKER: "operation-policies/realtime/realtime-hangup.cs",
    REALTIME_OBSERVER_OWNER_MARKER: "fragments/authentication/core-realtime-observer/core-realtime-observer.cs",
    CORE_API_SETTINGS_MARKER: "fragments/settings/core-api-settings/core-api-settings.cs",
    CORE_API_INPUT_VALIDATION_MARKER: "fragments/request/core-api-input-validation/core-api-input-validation.cs",
    CORE_API_OPERATION_SETTINGS_MARKER: "fragments/request/core-api-operation-settings/core-api-operation-settings.cs",
    CORE_API_AUTHENTICATION_RESULT_MARKER: "fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs",
    CORE_API_TARGET_VALIDATION_MARKER: "fragments/request/core-api-target-validation/core-api-target-validation.cs",
    CORE_CHAT_STORE_DISABLED_MARKER: "fragments/request/core-chat-store-disabled/core-chat-store-disabled.cs",
    CORE_MODEL_LIMITS_MARKER: "fragments/request/core-model-limits/core-model-limits.cs",
    CORE_RATE_LIMIT_MARKER: "fragments/quota/core-rate-limit/core-rate-limit.cs",
    CORE_TOKEN_LIMITS_MARKER: "fragments/quota/core-token-limits/core-token-limits.cs",
    CORE_ENTRA_USER_PROFILE_MARKER: "fragments/quota/core-entra-user-profile/core-entra-user-profile.cs",
    RATE_LIMIT_ERROR_RETRY_AFTER_MARKER: "snippets/quota/rate-limit-error/rate-limit-error.cs",
    MCP_RATE_CLEANUP_MARKER: "snippets/quota/mcp-rate-cleanup/mcp-rate-cleanup.cs",
    CREDENTIAL_TELEMETRY_MARKER: "snippets/audit/credential-telemetry/credential-telemetry.cs",
    CORE_CREDENTIAL_CONNECTION_OUTBOUND_MARKER: "fragments/authentication/core-credential-connection-outbound/core-credential-connection-outbound.cs",
    CORE_SPEECH_SSML_GUARD_MARKER: "fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs",
    CORE_DOCUMENT_INPUT_VALIDATION_MARKER: "fragments/request/core-document-input-validation/core-document-input-validation.cs",
    CORE_QUERY_OBSERVATION_MARKER: "fragments/request/core-query-observation/core-query-observation.cs",
    CORE_QUERY_TELEMETRY_MARKER: "fragments/audit/core-query-telemetry/core-query-telemetry.cs",
}
COMPACT_SOURCE_COMMENT = "/* 監査の説明はフラグメント core-gateway-telemetry を参照。 */"
SOURCE_EXPRESSION_FILES.update({marker: filename
                                for filename, sections in SECTION_EXPRESSION_FILES.items()
                                for marker in sections.values()})


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
    raise ValueError("Audit source comments require full, compact or omit mode")


def _without_telemetry_explanation_lines(xml: str) -> str:
    """複製時だけ監査の正本へ追加した説明行を除き、既存コメントとコードを保つ。"""
    return "\n".join(line for line in xml.split("\n") if not line.lstrip().startswith("/* 説明："))


def portal_source_explanation_blocks() -> dict[str, tuple[str, ...]]:
    """移行照合が認識できる、現在の正本から生成した既知の説明ブロックだけを返す。"""
    directory = REPOSITORY / "apps/api-gateway/policies"
    blocks = {}
    for marker, filename in SOURCE_EXPRESSION_FILES.items():
        source = (directory / filename).read_text(encoding="utf-8")
        explanation = "\n".join(line for line in source.split("\n") if line.lstrip().startswith("//"))
        blocks[marker] = tuple(line.strip() for line in _with_source_line_comments(explanation).split("\n") if line.strip())
    return blocks


def extract_policy_source_sections(source: str, names: tuple[str, ...]) -> dict[str, str]:
    """固定区画の順・一意性・閉じ方を確認し、区画外のコードを拒否する。"""
    if not isinstance(source, str):
        raise ValueError("Sectioned policy expression source must be text")
    sections, active, lines = {}, None, []
    for line in source.replace("\r\n", "\n").split("\n"):
        delimiter = re.fullmatch(r"// (BEGIN|END) ([a-z][a-z-]*)", line)
        if delimiter:
            action, name = delimiter.groups()
            if name not in names:
                raise ValueError("Policy source sections must use their fixed names")
            if action == "BEGIN":
                if active is not None or name in sections:
                    raise ValueError("Policy source sections must be unique and cannot be nested")
                active, lines = name, []
            else:
                if active != name or not any(item.strip() and not item.lstrip().startswith("//") for item in lines):
                    raise ValueError("Policy source sections need matching END and executable content")
                sections[name] = "\n".join(lines)
                active, lines = None, []
        elif re.match(r"// (?:BEGIN|END)(?:\s|$)", line.lstrip()):
            raise ValueError("Policy source section delimiters must use their fixed format")
        elif active is not None:
            lines.append(line)
        elif line.strip() and not line.lstrip().startswith("//"):
            raise ValueError("Policy source code must belong to a named section")
    if active is not None or tuple(sections) != names:
        raise ValueError("Policy source must contain its sections in the fixed order")
    return sections


def _render_policy_source_sections(xml: str, source_comments: str) -> str:
    """同名C#の固定区画を各XML式へ展開してから、通常markerを処理する。"""
    for filename, markers in SECTION_EXPRESSION_FILES.items():
        if not any(marker in xml for marker in markers.values()):
            continue
        if any(xml.count(marker) != 1 for marker in markers.values()):
            raise ValueError("Each policy source section needs exactly one XML insertion point")
        source = (REPOSITORY / "apps/api-gateway/policies" / filename).read_text(encoding="utf-8")
        sections = extract_policy_source_sections(source, tuple(markers))
        mode = "omit" if source_comments == "omit" else "full"
        for name, marker in markers.items():
            encoded = escape(_source_expression(sections[name], mode).rstrip("\n"), {} if marker in SECTION_TEXT_MARKERS else {'"': "&quot;", "'": "&apos;"})
            xml = re.sub(r"(?m)^([ \t]*)" + marker,
                         lambda match: match[1] + encoded.replace("\n", "\n" + match[1]), xml)
            if marker in xml:
                raise ValueError("Sectioned C# expression markers must occupy their own line")
    return xml


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
    """同名C#が所有する2区画をXMLへ展開する。主体投影もmessage自身が所有する。"""
    if not any(marker in xml for marker in QUERY_TELEMETRY_SECTIONS.values()):
        return xml
    if any(xml.count(marker) != 1 for marker in QUERY_TELEMETRY_SECTIONS.values()):
        raise ValueError("Each query telemetry section needs exactly one XML insertion point")
    directory = REPOSITORY / "apps/api-gateway/policies"
    source = (directory / SOURCE_EXPRESSION_FILES[CORE_QUERY_TELEMETRY_MARKER]).read_text(encoding="utf-8")
    sections = extract_query_telemetry_sections(source)
    if any(SUBJECT_OBSERVATION_MARKER in body for body in sections.values()):
        raise ValueError("Query telemetry sections must own their subject projection without nested legacy markers")
    # compactは他部品への複製用です。この正本では共通の利用者情報の説明も保持します。
    mode = "omit" if source_comments == "omit" else "full"
    for name, marker in QUERY_TELEMETRY_SECTIONS.items():
        body = sections[name]
        encoded = escape(_source_expression(body, mode), {'"': "&quot;", "'": "&apos;"})
        xml = re.sub(r"(?m)^([ \t]*)" + marker,
                     lambda match: match[1] + encoded.replace("\n", "\n" + match[1]), xml)
        if marker in xml:
            raise ValueError("Query telemetry C# insertion points must occupy their own line")
    return xml


def _render_realtime_owner_context(xml: str) -> str:
    """確認済みownerの受取りを生成時にinlineし、Azure fragmentの入れ子を増やさない。"""
    if REALTIME_OWNER_CONTEXT_XML_MARKER not in xml:
        return xml
    path = REPOSITORY / "apps/api-gateway/policies/snippets/authentication/realtime-owner-context/realtime-owner-context.xml"
    body = path.read_text(encoding="utf-8").replace("<fragment>", "").replace("</fragment>", "")
    # snippetの2文字の外側字下げを除き、親XMLの挿入位置へ揃えます。
    body = "\n".join(line[2:] if line.startswith("  ") else line for line in body.splitlines()).strip()
    xml = re.sub(r"(?m)^([ \t]*)" + REALTIME_OWNER_CONTEXT_XML_MARKER,
                 lambda match: match[1] + body.replace("\n", "\n" + match[1]), xml)
    if REALTIME_OWNER_CONTEXT_XML_MARKER in xml:
        raise ValueError("Realtime owner XML insertion points must occupy their own line")
    return xml


def render_audit_policy(xml: str, *, source_comments: str = "full") -> str:
    """C#をXMLへ埋め込み、展開先に応じて説明コメントの量を調整する。"""
    if source_comments not in {"full", "compact", "omit"}:
        raise ValueError("Audit source comments require full, compact or omit mode")
    marker = "__AUDIT_BODY_MODEL_ERROR_XML__"
    if marker in xml:
        source = (REPOSITORY / "apps/api-gateway/policies/snippets/audit/audit-body-model-error/audit-body-model-error.xml").read_text(encoding="utf-8").strip()
        root = _policy_xml(source)
        children = [node for node in root if isinstance(node.tag, str)]
        if root.tag != "fragment" or root.attrib or len(children) != 1 or children[0].tag != "choose" or children[0].get("id") != "core-audit-body-model-error":
            raise ValueError("Body model error snippet must contain its fixed decision")
        body = source[len("<fragment>"): -len("</fragment>")].strip()
        xml = re.sub(r"(?m)^([ \t]*)" + marker + r"[ \t]*$",
                     lambda match: match[1] + body.replace("\n", "\n" + match[1]), xml)
        if marker in xml:
            raise ValueError("Body model error snippet marker must occupy its own line")
    xml = _render_realtime_owner_context(xml)
    xml = _render_policy_source_sections(xml, source_comments)
    xml = _render_query_telemetry_sections(xml, source_comments)
    if source_comments in {"compact", "omit"} and CORE_GATEWAY_TELEMETRY_MARKER in xml:
        xml = _without_telemetry_explanation_lines(xml)
    indented_markers = {CORE_REQUEST_CONTEXT_MARKER, CORE_TRUSTED_INGRESS_MARKER, CORE_CREDENTIAL_BROWSER_EXCEPTION_MARKER, CORE_API_SETTINGS_MARKER, CORE_API_INPUT_VALIDATION_MARKER, CORE_API_OPERATION_SETTINGS_MARKER, CORE_API_AUTHENTICATION_RESULT_MARKER, CORE_API_TARGET_VALIDATION_MARKER, CORE_CHAT_STORE_DISABLED_MARKER, CORE_MODEL_LIMITS_MARKER, CORE_RATE_LIMIT_MARKER, CORE_TOKEN_LIMITS_MARKER, CORE_ENTRA_USER_PROFILE_MARKER, CORE_SPEECH_SSML_GUARD_MARKER, CORE_DOCUMENT_INPUT_VALIDATION_MARKER, CORE_QUERY_OBSERVATION_MARKER}
    indented_markers.update({CORE_CREDENTIAL_CALLER_MARKER, CORE_CREDENTIAL_USE_RESULT_MARKER, CREDENTIAL_CHALLENGE_RESULT_MARKER, CREDENTIAL_LOGIN_START_CALLER_MARKER, CREDENTIAL_LOGIN_STATUS_CALLER_MARKER, CREDENTIAL_LOGIN_DISCONNECT_CALLER_MARKER, CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_MARKER, CREDENTIAL_LOGIN_LOGOUT_CALLER_MARKER, CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_MARKER, CORE_BACKEND_AUDIT_MARKER})
    indented_markers.add(RATE_LIMIT_ERROR_RETRY_AFTER_MARKER)
    indented_markers.add(MCP_RATE_CLEANUP_MARKER)
    indented_markers.add(CREDENTIAL_TELEMETRY_MARKER)
    indented_markers.add(CORE_GATEWAY_TELEMETRY_MARKER)
    indented_markers.add(CORE_CREDENTIAL_CONNECTION_OUTBOUND_MARKER)
    indented_markers.update({REALTIME_OWNER_CONTEXT_MARKER, REALTIME_CREATE_TICKET_MARKER,
                             REALTIME_HANGUP_OWNER_MARKER, REALTIME_OBSERVER_OWNER_MARKER,
                             CORE_AUDIT_BODY_MODEL_MARKER})
    observation_markers = {CORE_GATEWAY_TELEMETRY_MARKER}
    for marker, filename in SOURCE_EXPRESSION_FILES.items():
        if marker in indented_markers:
            continue
        if marker in xml:
            source = (REPOSITORY / "apps/api-gateway/policies" / filename).read_text(encoding="utf-8")
            mode = "full" if source_comments == "compact" and marker not in observation_markers else source_comments
            xml = xml.replace(marker, escape(_source_expression(source, mode), {'"': "&quot;", "'": "&apos;"}))
    for marker in indented_markers:
        if marker not in xml:
            continue
        mode = source_comments if marker in {CORE_GATEWAY_TELEMETRY_MARKER, CREDENTIAL_TELEMETRY_MARKER, CORE_CREDENTIAL_CONNECTION_OUTBOUND_MARKER} else "omit" if source_comments == "omit" else "full"
        source = _source_expression((REPOSITORY / "apps/api-gateway/policies" / SOURCE_EXPRESSION_FILES[marker]).read_text(encoding="utf-8"), mode).rstrip("\n")
        # These markers are XML text; quote entities add bytes without
        # changing the decoded C# and are only needed for attribute markers.
        encoded = (escape(source) if marker in {CORE_GATEWAY_TELEMETRY_MARKER, CREDENTIAL_TELEMETRY_MARKER, CORE_CREDENTIAL_CONNECTION_OUTBOUND_MARKER}
                   else escape(source, {'"': "&quot;", "'": "&apos;"}))
        xml = re.sub(r"(?m)^([ \t]*)" + marker,
                     lambda match: match[1] + encoded.replace("\n", "\n" + match[1]), xml)
        if marker in xml:
            raise ValueError("Indented C# expression markers must occupy their own line")
    return xml


def render_audit_fragments(*, source_comments: str = "full") -> dict[str, str]:
    """監査2部品の説明を保持する。omit指定は既知の旧生成元照合だけに使う。"""
    fragments = {}
    for identifier, (filename, _) in AUDIT_FRAGMENT_FILES.items():
        rendered = render_audit_policy((REPOSITORY / "apps/api-gateway/policies" / filename).read_text(encoding="utf-8"),
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
