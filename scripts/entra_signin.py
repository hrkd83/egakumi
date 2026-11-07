#!/usr/bin/env python3
"""既存Entraアプリで試験用トークンを取得し、保護ファイルへ保存する。

user-tokenは登録済みlocalhost戻り先で本人認証し、app-tokenは既存クライアントの
秘密ファイルを使う。認証URLは保護した引渡しファイルへ書き、ブラウザーを
勝手に開かない。認証コード・アクセストークン・SDK例外本文は画面に出さない。
トークン、MSALキャッシュ、秘密ファイルの保存先を分け、シンボリックリンクや
他ユーザーが読める権限を拒否する。失敗時に古いトークンが成功結果として
残らないようにする。Entraへの認証通信は行うが、アプリ作成、権限追加、
Azure資源の配備やモデル推論は行わない。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import stat
import tempfile
import time
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from deployment.helpers.paths import LocalPaths, private_output, private_parent


CORE = Path(__file__).resolve().parents[1]


class SigninError(RuntimeError):
    """認証失敗を、秘密を含まない固定コードだけで呼出元へ返す例外。"""


def checked_auth_path(path: Path, *, private_input: bool = False) -> Path:
    """ローカル専用領域または明示した外部保護先を検査し、リンクを拒否する。"""
    try:
        target = private_output(path if path.is_absolute() else CORE / path, CORE)
        if target.exists():
            info = target.stat()
            if (not stat.S_ISREG(info.st_mode) or (private_input and info.st_mode & 0o077)
                    or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
                raise SigninError("AUTH_UNSAFE_FILE")
        return target
    except ValueError:
        raise SigninError("AUTH_UNSAFE_FILE") from None


def signin_paths(environment_name: str | None) -> LocalPaths:
    """実paramsを開かず、明示環境、AZURE_ENV_NAME、azd既定環境の順に選ぶ。"""
    try:
        selected = environment_name or os.environ.get("AZURE_ENV_NAME")
        if not selected:
            selection = private_output(CORE / ".azure/config.json", CORE)
            if selection.exists():
                selected = json.loads(selection.read_text(encoding="utf-8")).get("defaultEnvironment")
        if not selected:
            raise ValueError("Select an environment")
        return LocalPaths(CORE, selected)
    except (ValueError, OSError, AttributeError):
        raise SigninError("AUTH_INVALID_ARGUMENTS") from None


def protected_text(path: Path, value: str) -> None:
    """所有者限定の一時ファイルへ書いて置換し、リンクや中途半端な出力を避ける。"""
    path = checked_auth_path(path)
    try:
        private_parent(path, CORE)
    except ValueError:
        raise SigninError("AUTH_UNSAFE_FILE") from None
    handle, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_protected_text(path: Path) -> str:
    """通常ファイル・所有者・専用権限を検査して認証入力を読む。"""
    try:
        path = checked_auth_path(path, private_input=True)
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(handle, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise SigninError("AUTH_UNSAFE_FILE")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise SigninError("AUTH_UNSAFE_FILE")
            return stream.read()
    except (OSError, UnicodeError):
        raise SigninError("AUTH_INPUT_UNAVAILABLE") from None


def clear_output(path: Path) -> None:
    """失敗後に古いトークンを誤って再利用しないよう、前の出力を回収する。"""
    path = checked_auth_path(path)
    path.unlink(missing_ok=True)


def validated_redirect(value: str) -> tuple[str, int, str]:
    """登録済みlocalhostのHTTP戻り先を検査し、任意の外部受信先を拒否する。"""
    try:
        uri = urlsplit(value)
        port = uri.port
    except ValueError:
        raise SigninError("AUTH_INVALID_REDIRECT") from None
    if (uri.scheme != "http" or uri.hostname not in ("localhost", "127.0.0.1")
            or uri.username or uri.password or not port or not 1024 <= port <= 65535
            or uri.query or uri.fragment or "%" in uri.path or uri.path.startswith("//")):
        raise SigninError("AUTH_INVALID_REDIRECT")
    return uri.hostname, port, uri.path or "/"


class CallbackServer(HTTPServer):
    """本人認証のcode/stateをlocalhostだけで受信する一時サーバー。"""
    allow_reuse_address = True

    def __init__(self, redirect_uri: str, expected_state: str):
        """認証フローと戻り先を固定し、短時間の受信口を作る。"""
        host, port, path = validated_redirect(redirect_uri)
        self.expected_host = host
        self.expected_port = port
        self.expected_path = path
        self.expected_state = expected_state
        self.authorization_response: dict[str, str] | None = None
        super().__init__(("127.0.0.1", port), CallbackHandler)

    def get_request(self):
        """受信接続へタイムアウトを付け、認証待ちのまま無制限に保持しない。"""
        connection, address = super().get_request()
        # localhostの接続が止まっても、本人認証全体の待機上限を超えないようにする。
        connection.settimeout(1)
        return connection, address

    def handle_error(self, request, client_address):
        # HTTPServer標準の例外表示には戻りURLの認証コードが入り得るため表示しない。
        """コードなどを含み得るHTTP処理例外をログへ出さない。"""
        pass


class CallbackHandler(BaseHTTPRequestHandler):
    """認証コードをログに出さず、期待した戻り先の要求だけを処理する。"""
    server: CallbackServer

    def log_message(self, format, *arguments):
        """認証コードやstateが入るURLのアクセスログを抑止する。"""
        pass

    def send_safe(self, status: int, message: str) -> None:
        """キャッシュされない固定の応答を返し、認証内容を画面へ転載しない。"""
        body = message.encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """戻り先・state・コードの形式を確認し、MSALへ渡す認証結果を一時保存する。"""
        try:
            target = urlsplit(self.path)
            host = urlsplit("http://" + self.headers.get("Host", ""))
            if (target.scheme or target.netloc or target.fragment
                    or target.path != self.server.expected_path
                    or host.hostname != self.server.expected_host
                    or host.port != self.server.expected_port):
                self.send_safe(404, "AUTH_CALLBACK_NOT_FOUND")
                return
            if len(target.query) > 8192:
                self.send_safe(400, "AUTH_CALLBACK_INVALID")
                return
            pairs = parse_qsl(target.query, keep_blank_values=True,
                              strict_parsing=True, max_num_fields=20)
            response = dict(pairs)
            if len(response) != len(pairs):
                self.send_safe(400, "AUTH_CALLBACK_INVALID")
                return
            state = response.get("state", "")
            if not state or not hmac.compare_digest(state, self.server.expected_state):
                self.send_safe(400, "AUTH_CALLBACK_INVALID")
                return
            if bool(response.get("code")) == bool(response.get("error")):
                self.send_safe(400, "AUTH_CALLBACK_INVALID")
                return
            # 認証結果の原文はメモリー内だけに保持する。PKCEコードの交換時に、
            # MSALもstate/nonceを再検査する。
            self.server.authorization_response = response
            self.send_safe(200, "AUTH_CALLBACK_RECEIVED. You may close this tab.")
        except (ValueError, UnicodeError, TypeError):
            self.send_safe(400, "AUTH_CALLBACK_INVALID")

    def do_POST(self):
        """認証で使わないPOSTを拒否し、受信口の用途を限定する。"""
        self.send_safe(405, "AUTH_CALLBACK_METHOD_NOT_ALLOWED")


def wait_for_callback(server: CallbackServer, timeout_seconds: float) -> dict[str, str]:
    """期限まで本人認証の戻りを待ち、受信サーバーを確実に閉じる。"""
    deadline = time.monotonic() + timeout_seconds
    while server.authorization_response is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SigninError("AUTH_SIGNIN_TIMEOUT")
        server.timeout = min(0.2, remaining)
        server.handle_request()
    return server.authorization_response


def load_cache(msal_module, path: Path | None):
    """所有者限定ファイルからMSALキャッシュを読み、なければ空のキャッシュを用意する。"""
    cache = msal_module.SerializableTokenCache()
    if path and path.exists():
        try:
            cache.deserialize(read_protected_text(path))
        except SigninError:
            raise
        except Exception:
            raise SigninError("AUTH_CACHE_INVALID") from None
    return cache


def save_result(result: dict, token_file: Path, cache, cache_file: Path | None) -> None:
    """アクセストークンと認証キャッシュを別の保護ファイルへ保存する。"""
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise SigninError("AUTH_TOKEN_NOT_ACQUIRED")
    if cache_file and cache.has_state_changed:
        protected_text(cache_file, cache.serialize())
    # API試験用の出力にはaccess tokenだけを書く。refresh/ID tokenやJSONは混ぜない。
    protected_text(token_file, token)


def user_token(arguments, msal_module) -> None:
    """PKCE付き本人認証を行い、成功後にだけ試験用トークンを保存する。

    認証URLは保護した引渡しファイルへ書き、利用者のブラウザー操作を待つ。
    アプリ作成・追加権限の要求処理はここでは行わない。
    """
    cache = load_cache(msal_module, arguments.cache_file)
    app = msal_module.PublicClientApplication(
        arguments.client_id, authority=f"https://login.microsoftonline.com/{arguments.tenant_id}",
        token_cache=cache, enable_pii_log=False, timeout=30,
    )
    flow = app.initiate_auth_code_flow(
        scopes=arguments.scope, redirect_uri=arguments.redirect_uri, response_mode="query",
    )
    if (not isinstance(flow, dict) or not isinstance(flow.get("auth_uri"), str)
            or not isinstance(flow.get("state"), str) or not flow["state"]):
        raise SigninError("AUTH_FLOW_NOT_CREATED")
    uri = urlsplit(flow["auth_uri"])
    if uri.scheme != "https" or uri.hostname != "login.microsoftonline.com":
        raise SigninError("AUTH_FLOW_NOT_CREATED")
    published_url = False
    try:
        # 受信口を開いてからURLを渡し、ブラウザーの戻りがサーバー起動を追い越さないようにする。
        with CallbackServer(arguments.redirect_uri, flow["state"]) as server:
            clear_output(arguments.token_file)
            protected_text(arguments.login_url_file, flow["auth_uri"])
            published_url = True
            print("AUTH_URL_READY", flush=True)
            response = wait_for_callback(server, arguments.timeout_seconds)
        if "error" in response:
            raise SigninError("AUTH_CONSENT_OR_SIGNIN_REJECTED")
        try:
            result = app.acquire_token_by_auth_code_flow(flow, response)
        except ValueError:
            raise SigninError("AUTH_CALLBACK_REJECTED") from None
        save_result(result, arguments.token_file, cache, arguments.cache_file)
    finally:
        if published_url:
            clear_output(arguments.login_url_file)


def app_token(arguments, msal_module) -> None:
    """既存アプリの秘密ファイルを読み、client credentialsで試験用トークンを取得する。"""
    secret = read_protected_text(arguments.client_secret_file).rstrip("\r\n")
    if not secret:
        raise SigninError("AUTH_SECRET_EMPTY")
    cache = load_cache(msal_module, arguments.cache_file)
    app = msal_module.ConfidentialClientApplication(
        arguments.client_id, authority=f"https://login.microsoftonline.com/{arguments.tenant_id}",
        client_credential=secret, token_cache=cache, enable_pii_log=False, timeout=30,
    )
    clear_output(arguments.token_file)
    result = app.acquire_token_for_client(scopes=arguments.scope)
    save_result(result, arguments.token_file, cache, arguments.cache_file)


class SafeParser(argparse.ArgumentParser):
    """認証用引数を含むパーサーエラーを固定コードへ変換する。"""
    def error(self, message):
        """生の引数値を表示せず、AUTH_INVALID_ARGUMENTSとして停止する。"""
        raise SigninError("AUTH_INVALID_ARGUMENTS")


def parse_arguments(argv=None):
    """認証方式・ID・scope・戻り先とファイルの分離を、認証開始前に検査する。"""
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True, parser_class=SafeParser)
    for name in ("user-token", "app-token"):
        command = commands.add_parser(name)
        command.add_argument("--tenant-id", required=True)
        command.add_argument("--client-id", required=True)
        command.add_argument("--scope", action="append", required=True)
        command.add_argument("--environment", help="Local environment; default: AZURE_ENV_NAME or saved azd selection")
        command.add_argument("--token-file", type=Path, help="Protected token output; default: .local/<environment>/secrets")
        command.add_argument("--cache-file", type=Path)
        if name == "user-token":
            command.add_argument("--redirect-uri", "--redirect", default="http://localhost:8400")
            command.add_argument("--timeout-seconds", type=int, default=600)
        else:
            command.add_argument("--client-secret-file", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        arguments.tenant_id = str(UUID(arguments.tenant_id))
        arguments.client_id = str(UUID(arguments.client_id))
    except (ValueError, AttributeError):
        raise SigninError("AUTH_INVALID_ARGUMENTS") from None
    if (any(not scope or any(char.isspace() for char in scope) for scope in arguments.scope)
            or len(set(arguments.scope)) != len(arguments.scope)):
        raise SigninError("AUTH_INVALID_ARGUMENTS")
    if arguments.mode == "user-token":
        validated_redirect(arguments.redirect_uri)
        if not 1 <= arguments.timeout_seconds <= 3600:
            raise SigninError("AUTH_INVALID_ARGUMENTS")
    elif len(arguments.scope) != 1 or not arguments.scope[0].endswith("/.default"):
        raise SigninError("AUTH_INVALID_ARGUMENTS")
    runtime = signin_paths(arguments.environment)
    arguments.login_url_file = checked_auth_path(runtime.state / "gateway-login-url.txt")
    arguments.token_file = checked_auth_path(arguments.token_file if arguments.token_file else runtime.secrets / (
        "gateway-access-token.txt" if arguments.mode == "user-token" else "service-access-token.txt"))
    if arguments.cache_file is not None:
        arguments.cache_file = checked_auth_path(arguments.cache_file, private_input=True)
    if arguments.mode == "app-token":
        arguments.client_secret_file = checked_auth_path(arguments.client_secret_file, private_input=True)
    paths = [arguments.token_file, arguments.cache_file]
    if arguments.mode == "app-token":
        paths.append(arguments.client_secret_file)
    paths = [path for path in paths if path is not None]
    try:
        for path in paths:
            if path.is_relative_to(CORE.resolve()):
                runtime.checked(path)
    except ValueError:
        raise SigninError("AUTH_UNSAFE_FILE") from None
    if len(set(paths + [arguments.login_url_file])) != len(paths) + 1:
        raise SigninError("AUTH_INVALID_ARGUMENTS")
    return arguments


def main(argv=None, *, msal_module=None) -> int:
    """本人/app-onlyを振り分け、成功・取消・失敗を秘密のない固定メッセージで返す。"""
    try:
        arguments = parse_arguments(argv)
        if msal_module is None:
            try:
                import msal as msal_module
            except ImportError:
                raise SigninError("AUTH_DEPENDENCY_MISSING") from None
        if arguments.mode == "user-token":
            user_token(arguments, msal_module)
        else:
            app_token(arguments, msal_module)
        print("AUTH_COMPLETE")
        return 0
    except SigninError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("AUTH_CANCELLED", file=sys.stderr)
        return 130
    except Exception:
        # SDK/HTTP例外にはトークンや認証コードが入り得るため、生の内容を表示しない。
        print("AUTH_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
