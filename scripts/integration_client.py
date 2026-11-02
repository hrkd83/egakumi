"""RESTとMCPを実際に呼び出し、本文を残さずに疎通結果を記録する。

Chat Completions・Responses・PII・接続確認のREST、および公式MCP SDKによる
OAuth/ツール呼出しを扱う。入力は固定HTTPS接続先と所有者だけが読める資格情報。
出力にはHTTP状態、許可した相関ID、応答形式・終端の成否、利用量などだけを残し、
生成本文・個人情報・トークン・認証コードは保存しない。
通信の自動再試行を無効化し、OAuthの認証チャレンジ後の再送は別に数える。
MCPは登録済み公開クライアントとlocalhost戻り先を使い、動的アプリ登録をしない。
run時には実API・本人認証への通信があり、推論・ツール実行の料金が発生し得る。

MCP公式実装例:
https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/examples/snippets/clients/oauth_client.py
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import parse_qs, urlsplit
import uuid


KNOWN_FIELDS = frozenset((
    "id", "object", "created", "created_at", "model", "choices", "output", "usage",
    "status", "error", "incomplete_details", "redactedText", "language", "detectedCount",
    "modelVersion", "warningCodes", "requestId", "content", "structuredContent", "isError",
))
PII_FIELDS = frozenset(("redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"))
USAGE_FIELDS = frozenset(("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
                          "cached_tokens", "reasoning_tokens"))
CORRELATION_HEADERS = ("apim-request-id", "request-id", "x-request-id", "x-ms-request-id", "x-ms-client-request-id")


def private_text(path: str | Path) -> str:
    """通常ファイル・所有者・600相当の権限・サイズを確認して資格情報を読む。"""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Protected file must be an owner-only regular file")
    if info.st_size > 1024 * 1024:
        raise ValueError("Protected file is too large")
    return path.read_text().strip()


def private_write(path: str | Path, value: str) -> None:
    """リンクをたどらず、所有者だけが読める権限でローカルファイルを保存する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def endpoint(value: str) -> str:
    """資格情報やfragmentのない明示HTTPS接続先を受け取る。"""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("An HTTPS endpoint without credentials or fragment is required")
    return value


def validate_oauth_config(config):
    """許可したEntraテナント・クライアント・認可/トークンURL・scopeを照合する。"""
    issuer = urlsplit(endpoint(config["issuer"]))
    parts = issuer.path.strip("/").split("/")
    if issuer.netloc != "login.microsoftonline.com" or issuer.query or len(parts) != 2 or parts[1] != "v2.0":
        raise ValueError("An explicitly approved Entra tenant issuer is required")
    uuid.UUID(parts[0])
    uuid.UUID(config["client_id"])
    for field, suffix in (("authorization_endpoint", "authorize"), ("token_endpoint", "token")):
        if config[field] != f"https://login.microsoftonline.com/{parts[0]}/oauth2/v2.0/{suffix}":
            raise ValueError("OAuth endpoints must belong to the approved tenant")
    if not isinstance(config["scope"], str) or not config["scope"].strip():
        raise ValueError("An explicitly approved Gateway scope is required")


def auth_headers(bearer_file=None, key_file=None, *, key_header="api-key") -> dict:
    """保護ファイルからBearerまたはAPIMキーを読み、HTTPヘッダーをメモリー上で作る。"""
    if not re.fullmatch(r"[A-Za-z0-9-]+", key_header) or key_header.casefold() in ("authorization", "host", "cookie", "content-type"):
        raise ValueError("A dedicated subscription-key header is required")
    headers = {}
    for filename, header, prefix in ((bearer_file, "Authorization", "Bearer "),
                                     (key_file, key_header, "")):
        if filename:
            value = private_text(filename)
            if header == "Authorization" and value.startswith("{"):
                value = json.loads(value)["access_token"]
            if not isinstance(value, str) or not value or any(char.isspace() for char in value):
                raise ValueError("Invalid credential file")
            headers[header] = prefix + value
    return headers


def correlations(headers) -> dict:
    """許可したヘッダー名とGUID形式に合う相関IDだけを証跡へ取り出す。"""
    return {name: value for name in CORRELATION_HEADERS
            if (value := headers.get(name)) and re.fullmatch(r"[0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", value)}


def field_summary(value) -> dict:
    """既知フィールド名と未知フィールドの件数だけを残し、値そのものは捨てる。"""
    keys = set(value) if isinstance(value, dict) else set()
    return {"fieldNames": sorted(keys & KNOWN_FIELDS), "unknownFieldCount": len(keys - KNOWN_FIELDS)}


def usage_numbers(value) -> dict:
    """利用量の既知項目から、有限で非負の数値だけを抽出する。"""
    result = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in USAGE_FIELDS and type(item) in (int, float) and math.isfinite(item) and item >= 0:
                result[key] = item
            elif key in ("input_tokens_details", "output_tokens_details", "prompt_tokens_details", "completion_tokens_details"):
                for detail, number in usage_numbers(item).items():
                    result[key + "." + detail] = number
    return result


def json_shape(kind: str, value) -> bool:
    """試験種別ごとに必要な応答構造を調べ、HTTP成功だけで合格にしない。"""
    # Responses の正常応答にも error:null が含まれる。項目が存在するだけでは
    # 失敗ではなく、null 以外のエラー情報が返った場合にだけ拒否する。
    if not isinstance(value, dict) or value.get("error") is not None:
        return False
    if kind == "chat":
        # この短文 probe はツールを使わない。本文が返っていても、出力枠不足や
        # filter/tool 呼出しで終わった応答を、Reply OK の正常完了にしない。
        return isinstance(value.get("choices"), list) and bool(value["choices"]) and all(
            isinstance(choice, dict) and isinstance(choice.get("message"), dict)
            and choice.get("finish_reason") == "stop"
            and isinstance(choice["message"].get("content"), str) and bool(choice["message"]["content"]) for choice in value["choices"])
    if kind == "responses":
        output = value.get("output")
        # この試験は短いテキスト回答を要求する。reasoning 項目だけ・空の本文を
        # 正常な回答と誤認せず、completed と実際の output_text を両方確認する。
        return value.get("status") == "completed" and isinstance(output, list) and any(
            isinstance(item, dict) and isinstance(item.get("content"), list) and any(
                isinstance(part, dict) and part.get("type") == "output_text"
                and isinstance(part.get("text"), str) and bool(part["text"].strip())
                for part in item["content"])
            for item in output)
    if kind == "pii":
        return (set(value) == PII_FIELDS and isinstance(value.get("redactedText"), str)
                and value.get("language") in ("ja", "en") and type(value.get("detectedCount")) is int
                and value["detectedCount"] >= 0 and isinstance(value.get("warningCodes"), list)
                and isinstance(value.get("modelVersion"), str) and isinstance(value.get("requestId"), str))
    return kind == "connection" and isinstance(value.get("id"), str)


class StreamSummary:
    """SSEをイベント単位で調べ、本文を保存せず正常終端・出力有無・利用量を集計する。"""

    def __init__(self, kind: str, started: float):
        """試験種別・開始時刻と、本文を残さない集計状態を用意する。"""
        self.kind, self.started = kind, started
        self.data = []
        self.events = 0
        self.first_ms = None
        self.terminal = False
        self.failed = False
        self.finish = False
        self.output_observed = False
        self.usage = {}

    def line(self, line: str) -> None:
        """SSEのdata行を1イベントにまとめ、検査用バッファの上限を守る。"""
        if not line:
            if self.data:
                self.event("\n".join(self.data))
                self.data = []
        elif line.startswith("data:"):
            self.data.append(line[5:].lstrip(" "))
            if sum(map(len, self.data)) > 1024 * 1024:
                raise ValueError("SSE event exceeds local inspection limit")

    def event(self, data: str) -> None:
        """Chat/Responsesのイベントを読み、出力・失敗・正常終端と数値だけを更新する。"""
        self.events += 1
        if self.first_ms is None:
            self.first_ms = round((time.monotonic() - self.started) * 1000, 2)
        if data == "[DONE]":
            if self.kind == "chat":
                self.terminal = True
            return
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("SSE data is not an object")
        self.usage.update(usage_numbers(value.get("usage")))
        if self.kind == "chat":
            self.output_observed |= any(isinstance(item, dict) and isinstance(item.get("delta"), dict)
                                        and bool(item["delta"].get("content")) for item in value.get("choices", []))
            self.finish |= any(isinstance(item, dict) and item.get("finish_reason") == "stop"
                               for item in value.get("choices", []))
            # delta 中の null/省略は通常だが、終端が stop 以外なら未合格とする。
            # 他 choice の stop や後続 [DONE] で、この失敗を取り消さない。
            self.failed |= any(isinstance(item, dict) and item.get("finish_reason") is not None
                               and item.get("finish_reason") != "stop" for item in value.get("choices", []))
            self.failed |= "error" in value
        else:
            event_type = value.get("type")
            self.output_observed |= event_type == "response.output_text.delta" and isinstance(value.get("delta"), str) and bool(value["delta"])
            self.failed |= event_type in ("error", "response.failed", "response.incomplete")
            response = value.get("response", {})
            if isinstance(response, dict):
                self.usage.update(usage_numbers(response.get("usage")))
                self.terminal |= event_type == "response.completed" and response.get("status") == "completed"

    def result(self) -> dict:
        """集計した件数・初回応答時間・終端状態・利用量だけを返す。"""
        return {"eventCount": self.events, "firstEventMs": self.first_ms,
                "outputObserved": self.output_observed,
                "normalTerminal": self.terminal and not self.failed and (self.kind != "chat" or self.finish),
                "usage": self.usage}


def synthetic_body(kind: str, model: str | None, tokens: int, stream=False) -> dict | None:
    """実データを使わず、短い固定入力とトークン上限の試験要求を作る。"""
    if kind == "chat":
        return {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_completion_tokens": tokens, "stream": stream, **({"stream_options": {"include_usage": True}} if stream else {})}
    if kind == "responses":
        return {"model": model, "input": "Reply with OK.", "max_output_tokens": tokens, "store": False, "stream": stream}
    if kind == "pii":
        return {"text": "連絡先は alice@example.com です。", "language": "ja"}
    return None


def expected_subject(kind: str, value: str | None) -> str | None:
    """本人接続の照合先だけを公開 GUID として検査し、秘密や別 API の入力と混同しない。"""
    if value is None:
        return None
    if kind != "connection":
        raise ValueError("Expected subject ID applies only to the connection probe")
    try:
        identifier = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Expected subject ID must be a nonzero GUID") from None
    if not identifier.int:
        raise ValueError("Expected subject ID must be a nonzero GUID")
    return str(identifier)


async def rest_probe(url, kind, body, headers, *, stream=False, timeout=120, stop_after_events=0,
                     transport=None, expected_subject_id=None) -> dict:
    """固定HTTPS先を自動再試行なしで1回呼び、応答構造と終端の合否を返す。

    SSEの途中切断は正常終端と区別し、PIIでは合成連絡先がマスクされたか確認する。
    応答本文・資格情報・生の例外文は証跡に保存しない。
    """
    import httpx2
    subject = expected_subject(kind, expected_subject_id)
    started = time.monotonic()
    result = {"kind": kind, "status": None, "normalTerminal": False, "succeeded": False,
              "startedAt": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4()), "httpAttempts": 0}
    if subject is not None:
        result["subjectMatched"] = False
    parser = StreamSummary(kind, started)
    # 相関IDを要求ごとに付けてAppGW/FW/APIM側の証跡と突き合わせる。
    # 本文を保存しなくても、どの要求を確認したか辿れるようにする。
    request_headers = {**headers, "x-ms-client-request-id": result["requestId"], "Accept": "text/event-stream" if stream else "application/json"}
    try:
        async with httpx2.AsyncClient(transport=transport or httpx2.AsyncHTTPTransport(retries=0),
                                     follow_redirects=False, timeout=timeout, trust_env=False) as client:
            result["httpAttempts"] += 1
            async with client.stream("GET" if kind == "connection" else "POST", endpoint(url),
                                     json=body, headers=request_headers) as response:
                result.update(status=response.status_code, correlationIds=correlations(response.headers))
                # SSEでは最初の200だけで合格にしない。出力を観測し、製品が定める
                # 完了イベントまで受信できたことを確認する。
                if stream and response.status_code == 200 and response.headers.get("content-type", "").startswith("text/event-stream"):
                    async for line in response.aiter_lines():
                        parser.line(line)
                        if stop_after_events and parser.events >= stop_after_events:
                            result["interruptedByClient"] = True
                            break
                    result.update(parser.result())
                    result["normalTerminal"] &= not result.get("interruptedByClient", False)
                    result["succeeded"] = result["normalTerminal"] and result["outputObserved"]
                else:
                    raw = await response.aread()
                    if len(raw) > 4 * 1024 * 1024:
                        raise ValueError("Response exceeds local inspection limit")
                    try:
                        value = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        value = None
                    result.update(field_summary(value))
                    result["usage"] = usage_numbers(value.get("usage")) if isinstance(value, dict) else {}
                    result["contractShapeValid"] = json_shape(kind, value)
                    result["normalTerminal"] = not stream and 200 <= response.status_code < 300 and result["contractShapeValid"]
                    result["succeeded"] = result["normalTerminal"]
                    if subject is not None:
                        # Graph の本人 ID はメモリー内だけで比較し、応答本文や ID 自体を証跡へ保存しません。
                        try:
                            result["subjectMatched"] = result["normalTerminal"] and str(uuid.UUID(value["id"])) == subject
                        except (ValueError, TypeError, AttributeError, KeyError):
                            result["subjectMatched"] = False
                        result["normalTerminal"] = result["normalTerminal"] and result["subjectMatched"]
                        result["succeeded"] = result["normalTerminal"]
                    if kind == "pii" and isinstance(value, dict):
                        result["unexpectedPiiFields"] = bool(set(value) - PII_FIELDS)
                        result["syntheticContactMasked"] = isinstance(value.get("redactedText"), str) and "alice@example.com" not in value["redactedText"]
    except Exception as error:
        result.update(parser.result() if stream else {})
        result.update(failureClass=type(error).__name__, succeeded=False, normalTerminal=False)
    result["elapsedMs"] = round((time.monotonic() - started) * 1000, 2)
    return result


class LoopbackLogin:
    """localhostでOAuthの戻りを受け、認証URLは保護ファイルだけで引き渡す。"""

    def __init__(self, config, url_file, timeout):
        """登録済みloopback戻り先だけを受ける短時間のローカルHTTPサーバーを用意する。"""
        self.config, self.url_file, self.timeout = config, url_file, timeout
        uri = urlsplit(config["redirect_uri"])
        if uri.scheme != "http" or uri.hostname not in ("localhost", "127.0.0.1") or not uri.port or uri.query or uri.fragment:
            raise ValueError("Registered loopback HTTP redirect URI is required")
        self.response = None
        parent = self

        class Handler(BaseHTTPRequestHandler):
            """認証コードをアクセスログへ出さず、決められた戻り先だけを受け付ける。"""
            def log_message(self, *args):
                """URL内の認証コードを通常のHTTPアクセスログへ出さない。"""
                pass

            def do_GET(self):
                """必要なcode/stateが1件ずつある戻りだけを一時メモリーへ受け取る。"""
                parsed = urlsplit(self.path)
                params = parse_qs(parsed.query)
                valid = parsed.path == (uri.path or "/") and all(len(params.get(key, [])) == 1 for key in ("code", "state")) and "error" not in params
                if valid:
                    parent.response = {key: params[key][0] for key in ("code", "state", "iss") if key in params and len(params[key]) == 1}
                self.send_response(200 if valid else 400)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Authentication received. You may close this tab." if valid else b"Invalid callback.")

        self.server = HTTPServer(("127.0.0.1", uri.port), Handler)
        self.server.timeout = 0.5

    async def redirect(self, url):
        """SDKが示した認可URLを承認済みEntra先と照合して保護ファイルへ保存する。"""
        expected = urlsplit(self.config["authorization_endpoint"])
        actual = urlsplit(url)
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError("Unexpected authorization endpoint")
        private_write(self.url_file, url)

    async def callback(self):
        """期限内に本人認証の戻りを待ち、引渡しファイルを回収してSDKへ返す。"""
        from mcp.client.auth import AuthorizationCodeResult
        expires = time.monotonic() + self.timeout
        while self.response is None and time.monotonic() < expires:
            await asyncio.to_thread(self.server.handle_request)
        if self.response is None:
            raise TimeoutError("Loopback authentication timed out")
        Path(self.url_file).unlink(missing_ok=True)
        return AuthorizationCodeResult(**self.response)

    def close(self):
        """ローカル受信口を閉じ、残った認証URLファイルを削除する。"""
        self.server.server_close()
        Path(self.url_file).unlink(missing_ok=True)


def oauth_provider(url, config, redirect_handler, callback_handler, token_file=None):
    """既存の公開クライアント情報と保護ストレージを、公式MCP OAuth処理へ渡す。"""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

    validate_oauth_config(config)
    parsed_endpoint = urlsplit(endpoint(url))
    shared_resource = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
    metadata = OAuthClientMetadata(client_name="Workforce integration client", redirect_uris=[config["redirect_uri"]],
                                   scope=config["scope"], token_endpoint_auth_method="none")

    async def approved_redirect(authorization_url):
        """resourceを差し替えず、本人を承認済みtenantのPKCE認証だけへ案内する。

        EntraにはGateway全体の共通resourceを一度登録します。MCP接続先の
        子パスは登録しません。SDKがPRMから得た原文を使い、末尾/も追加しません。
        SDKが生成したstate/PKCEはそのまま使い、追加権限や別tenantへの誘導を拒否します。
        """
        actual = urlsplit(authorization_url)
        expected = urlsplit(config["authorization_endpoint"])
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path) or actual.fragment:
            raise ValueError("Unexpected authorization endpoint")
        params = parse_qs(actual.query, keep_blank_values=True)
        for key, value in (("resource", shared_resource), ("client_id", config["client_id"]),
                           ("redirect_uri", config["redirect_uri"]), ("response_type", "code"),
                           ("code_challenge_method", "S256")):
            if params.get(key) != [value]:
                raise ValueError("OAuth authorization request does not match the approved MCP client")
        if len(params.get("state", [])) != 1 or not params["state"][0] or len(params.get("code_challenge", [])) != 1:
            raise ValueError("OAuth authorization request lacks its unique state or PKCE challenge")
        if len(params.get("scope", [])) != 1:
            raise ValueError("OAuth authorization scope is ambiguous")
        scopes = set(params["scope"][0].split())
        approved = set(config["scope"].split())
        # SDKはrefresh_tokenを使うためoffline_accessを追加します。他APIの権限は許しません。
        if not approved <= scopes or not scopes <= approved | {"offline_access"}:
            raise ValueError("OAuth authorization scope is not approved")
        await redirect_handler(authorization_url)

    class ProtectedTokenStorage:
        """MCP用トークンを接続先・issuer・clientと結び付けて保存する。"""
        tokens = None
        client_info = OAuthClientInformationFull(**metadata.model_dump(), client_id=config["client_id"], issuer=config["issuer"])

        async def get_tokens(self):
            """保存済みトークンが今回の接続先とアプリに一致する場合だけ再利用する。"""
            if token_file and Path(token_file).exists():
                saved = json.loads(private_text(token_file))
                if any(saved.get(key) != value for key, value in (
                    ("resource", shared_resource), ("endpoint", url), ("issuer", config["issuer"]), ("clientId", config["client_id"]))):
                    raise ValueError("SDK token file belongs to a different registered client or resource")
                return OAuthToken.model_validate(saved["tokens"])
            return self.tokens

        async def set_tokens(self, tokens):
            """SDKの新トークンをメモリーと必要時だけ所有者限定ファイルへ保存する。"""
            self.tokens = tokens
            if token_file:
                private_write(token_file, json.dumps({"resource": shared_resource, "endpoint": url, "issuer": config["issuer"], "clientId": config["client_id"],
                                                       "tokens": tokens.model_dump(mode="json")}) + "\n")

        async def get_client_info(self):
            """事前登録済み公開クライアントの情報をSDKへ返す。"""
            return self.client_info
        async def set_client_info(self, info):
            """動的クライアント登録によるアプリ新設を拒否する。"""
            raise ValueError("Dynamic client registration is disabled")

    return OAuthClientProvider(server_url=url, client_metadata=metadata, storage=ProtectedTokenStorage(),
                               redirect_handler=approved_redirect, callback_handler=callback_handler)


async def mcp_probe(url, arguments, headers, *, oauth_config=None, login=None, token_file=None, timeout=120, transport=None) -> dict:
    """公式SDKでMCPの接続とツール実行を確認し、本文を除いた証跡を返す。

    認証のチャレンジと再送は記録するが、ツールの自動再実行やストリーム再開は拒否する。
    外部メタデータURLとPOST先を制限し、資格情報を別ホストへ送らない。
    """
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    started = time.monotonic()
    result = {"kind": "mcp", "status": None, "normalTerminal": False, "succeeded": False,
              "startedAt": datetime.now(timezone.utc).isoformat(),
              "authenticationMode": "sdk-oauth-pkce" if oauth_config else "manual-credential",
              "oauthFlowVerified": False, "httpSteps": [], "toolPostCount": 0,
              "diagnosticsVersion": 2, "mcpPhase": "connecting", "toolsListReceived": False}
    url = endpoint(url)
    resource = urlsplit(url)
    shared_resource = f"{resource.scheme}://{resource.netloc}"
    oauth = oauth_provider(url, oauth_config, login.redirect, login.callback, token_file) if oauth_config else None

    def tool_descriptor(item):
        """任意のツール名・説明・schema本文を出さず、比較に必要な構造だけ返す。"""
        schema = item.input_schema
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required", [])
        required_valid = isinstance(required, list) and all(isinstance(name, str) for name in required)
        return {"nameSha256": hashlib.sha256(item.name.encode()).hexdigest(),
                "nameLength": len(item.name), "expectedNameEquals": item.name == "pii_redact_text",
                "expectedNameSuffix": item.name.endswith("pii_redact_text"),
                "inputSchemaObject": schema.get("type") == "object", "inputPropertyCount": len(properties),
                "textPropertyPresent": "text" in properties, "languagePropertyPresent": "language" in properties,
                "requiredFieldsMatch": required_valid and set(required) == {"text", "language"}}

    async def request_hook(request):
        """送信前に接続先・POST操作・再開ヘッダーを検査し、ツールの二重実行を防ぐ。"""
        target = str(request.url)
        parsed = urlsplit(target)
        if request.method == "POST" and target != url and (not oauth_config or target != oauth_config["token_endpoint"]):
            raise ValueError("Unapproved POST endpoint; dynamic registration is disabled")
        if parsed.scheme != "https" or (parsed.netloc != resource.netloc and (not oauth_config or parsed.netloc != urlsplit(oauth_config["issuer"]).netloc)):
            raise ValueError("Unapproved metadata endpoint")
        if oauth_config and parsed.netloc == urlsplit(oauth_config["issuer"]).netloc:
            # 同じlogin.microsoftonline.comでも別tenantには接続しません。
            # MCPのOIDC discovery順序で試す3種類のURLと、既存token endpointだけを許可します。
            issuer = urlsplit(oauth_config["issuer"])
            authority = f"{issuer.scheme}://{issuer.netloc}"
            suffix = issuer.path.rstrip("/")
            discovery = {authority + "/.well-known/oauth-authorization-server" + suffix,
                         authority + "/.well-known/openid-configuration" + suffix,
                         oauth_config["issuer"].rstrip("/") + "/.well-known/openid-configuration"}
            if target not in discovery and target != oauth_config["token_endpoint"]:
                raise ValueError("OAuth discovery targeted an unapproved tenant endpoint")
            if request.method == "POST":
                form = parse_qs(request.content.decode(), keep_blank_values=True)
                if form.get("resource") != [shared_resource] or form.get("client_id") != [oauth_config["client_id"]]:
                    raise ValueError("OAuth token request does not match the approved MCP resource/client")
        if request.headers.get("Last-Event-ID"):
            raise ValueError("Automatic MCP stream resumption is disabled for this probe")
        if request.method == "POST" and target == url:
            message = json.loads(request.content)
            # 通信本文や任意のツール名は保存しない。固定のプロトコル段階だけを
            # HTTP状態と結び付け、認証後にどこで止まったかを区別できるようにする。
            method = message.get("method")
            request.extensions["workforce_mcp_method"] = method if method in (
                "initialize", "notifications/initialized", "tools/list", "tools/call",
                "notifications/cancelled") else "other"
            if message.get("method") == "tools/call":
                result["toolPostCount"] += 1
                if result["toolPostCount"] > 1:
                    raise ValueError("Automatic tool replay is disabled")

    async def response_hook(response):
        """HTTP段階の状態を記録し、認証に伴う通信とツール本体を区別する。"""
        target = str(response.request.url)
        role = "mcp" if target == url else "token" if oauth_config and target == oauth_config["token_endpoint"] else "metadata"
        step = {"role": role, "status": response.status_code,
                "correlationIds": correlations(response.headers)}
        if role == "mcp":
            step["httpMethod"] = response.request.method
            step["rpcMethod"] = response.request.extensions.get("workforce_mcp_method", "receive")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            step["contentType"] = content_type if content_type in ("application/json", "text/event-stream") else "other"
            step["sessionHeaderPresent"] = bool(response.headers.get("Mcp-Session-Id"))
        result["httpSteps"].append(step)
        if oauth_config and role == "metadata" and response.status_code == 200:
            # PRMはGateway全体を指します。接続先MCPと認証対象を混同せず、
            # SDKの検査に加えて共通resource・tenantの完全一致を確認します。
            raw = await response.aread()
            if len(raw) > 65536:
                raise ValueError("OAuth metadata exceeds the local inspection limit")
            metadata = json.loads(raw)
            if "resource" in metadata:
                advertised_scopes = metadata.get("scopes_supported")
                if (metadata.get("resource") != shared_resource
                        or metadata.get("authorization_servers") != [oauth_config["issuer"]]
                        or not isinstance(advertised_scopes, list)
                        or any(not isinstance(scope, str) for scope in advertised_scopes)
                        or set(advertised_scopes) != set(oauth_config["scope"].split())):
                    raise ValueError("Protected resource metadata does not match the approved MCP resource/tenant")
            elif any(metadata.get(key) != oauth_config[key] for key in ("issuer", "authorization_endpoint", "token_endpoint")):
                raise ValueError("OAuth server metadata does not match the approved tenant endpoints")
        if target == url:
            result["status"] = response.status_code

    try:
        async with httpx2.AsyncClient(transport=transport or httpx2.AsyncHTTPTransport(retries=0), auth=oauth,
                                     headers=headers, timeout=timeout, trust_env=False, follow_redirects=False,
                                     event_hooks={"request": [request_hook], "response": [response_hook]}) as client:
            async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
                    result["mcpPhase"] = "initializing"
                    initialized = await session.initialize()
                    result["initialized"] = True
                    result["toolsCapabilityAdvertised"] = initialized.capabilities.tools is not None
                    result["mcpPhase"] = "listing-tools"
                    tools = await session.list_tools()
                    result["toolsListReceived"] = True
                    result["toolsAdvertisedCount"] = len(tools.tools)
                    result["toolsListMorePages"] = bool(tools.next_cursor)
                    # ツール名や説明文はサーバー由来の任意文字列なので出力しない。
                    # 件数・名前の一致・schema形状だけを残し、名前違い/空一覧/包装された
                    # 入力schemaを区別する。PIIや認証値を失敗本文から転載しない。
                    result["toolDescriptors"] = [tool_descriptor(item) for item in tools.tools[:16]]
                    result["mcpPhase"] = "checking-tool-schema"
                    tool = next((item for item in tools.tools if item.name == "pii_redact_text"), None)
                    result["toolPresent"] = tool is not None
                    result["toolInputFieldsValid"] = tool is not None and set(tool.input_schema.get("properties", {})) == {"text", "language"}
                    if not result["toolPresent"] or not result["toolInputFieldsValid"]:
                        raise ValueError("Expected PII tool schema was not advertised")
                    result["mcpPhase"] = "calling-tool"
                    called = await session.call_tool("pii_redact_text", arguments)
                    result["mcpPhase"] = "inspecting-tool-result"
                    value = called.structured_content
                    if not isinstance(value, dict):
                        text = [item.text for item in called.content if getattr(item, "type", None) == "text"]
                        value = json.loads(text[0]) if len(text) == 1 else None
                    result.update(field_summary(value))
                    result["contractShapeValid"] = json_shape("pii", value)
                    result["normalTerminal"] = not called.is_error and result["contractShapeValid"]
                    result["succeeded"] = result["normalTerminal"]
                    if oauth_config:
                        steps = result["httpSteps"]
                        result["oauthFlowVerified"] = bool(result["succeeded"] and any(item["role"] == "mcp" and item["status"] == 401 for item in steps)
                            and any(item["role"] == "metadata" and item["status"] == 200 for item in steps)
                            and any(item["role"] == "token" and item["status"] == 200 for item in steps))
                    if result["succeeded"]:
                        result["mcpPhase"] = "complete"
    except Exception as error:
        result.update(failureClass=type(error).__name__, succeeded=False, normalTerminal=False, oauthFlowVerified=False)
        # anyioの後始末は元の例外をExceptionGroupへ包む。本文を含むstr(error)は
        # 保存せず、末端のクラスとMCP数値コードだけを取り出す。
        pending, leaves, rpc_errors = [error], [], []
        while pending and len(leaves) < 16:
            current = pending.pop()
            children = getattr(current, "exceptions", None)
            if children:
                pending.extend(reversed(children))
            else:
                leaves.append(type(current).__name__)
                code = getattr(getattr(current, "error", None), "code", None)
                if type(code) is int:
                    rpc_errors.append(code)
        result["failureLeaves"] = leaves
        result["rpcErrorCodes"] = rpc_errors
    steps = result["httpSteps"]
    result["oauthResourceChallengeObserved"] = bool(oauth_config and any(item["role"] == "mcp" and item["status"] == 401 for item in steps))
    result["oauthTokenExchangeSucceeded"] = bool(oauth_config and any(item["role"] == "token" and item["status"] == 200 for item in steps))
    result["elapsedMs"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main():
    """REST/MCP試験の入力を検査し、限定した結果を保護ファイルと標準出力へ保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("chat", "responses", "pii", "connection", "mcp"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", "--bearer-file", dest="bearer_file", help="Owner-only plaintext bearer token file")
    parser.add_argument("--key-file")
    parser.add_argument("--model")
    parser.add_argument("--expected-subject-id", help="Expected public Graph user GUID for connection probes; only the match boolean is recorded")
    parser.add_argument("--body-file", help="JSON with synthetic data only")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--stop-after-events", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--oauth-client-file", help="Existing public client JSON, owner-only; no dynamic registration")
    parser.add_argument("--oauth-token-file", help="Separate owner-only SDK token JSON; never a REST token file")
    parser.add_argument("--login-url-file", help="Owner-only browser handoff URL; deleted after callback")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # SDK error strings can include OAuth response bodies.
    login = None
    try:
        if not 1 <= args.timeout <= 600 or not 1 <= args.max_output_tokens <= 512 or args.stop_after_events < 0:
            raise ValueError("Invalid probe limits")
        if args.stream and args.kind not in ("chat", "responses") or args.stop_after_events and not args.stream:
            raise ValueError("SSE options apply only to model streams")
        if args.kind in ("chat", "responses") and not args.model and not args.body_file:
            raise ValueError("An explicit model deployment is required")
        subject = expected_subject(args.kind, args.expected_subject_id)
        headers = auth_headers(args.bearer_file, args.key_file)
        body = json.loads(Path(args.body_file).read_text()) if args.body_file else synthetic_body(args.kind, args.model, args.max_output_tokens, args.stream)
        if args.kind == "mcp":
            config = json.loads(private_text(args.oauth_client_file)) if args.oauth_client_file else None
            if not config and (args.oauth_token_file or args.login_url_file):
                raise ValueError("SDK token/login files require an existing OAuth client file")
            if config:
                if headers or not args.login_url_file or not args.oauth_token_file:
                    raise ValueError("SDK OAuth requires separate login URL/token files and no manual credential")
                validate_oauth_config(config)
                login = LoopbackLogin(config, args.login_url_file, 300)
            result = asyncio.run(mcp_probe(args.url, body or synthetic_body("pii", None, 0), headers,
                                          oauth_config=config, login=login, token_file=args.oauth_token_file, timeout=args.timeout))
        else:
            if args.oauth_client_file or args.oauth_token_file or args.login_url_file:
                raise ValueError("OAuth provider mode is MCP-only; REST reads a protected bearer file")
            result = asyncio.run(rest_probe(args.url, args.kind, body, headers, stream=args.stream,
                                           timeout=args.timeout, stop_after_events=args.stop_after_events,
                                           expected_subject_id=subject))
    except Exception as error:
        result = {"kind": args.kind, "succeeded": False, "normalTerminal": False, "failureClass": type(error).__name__}
    finally:
        if login:
            login.close()
    private_write(args.output, json.dumps(result, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
