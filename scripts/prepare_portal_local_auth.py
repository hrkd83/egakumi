#!/usr/bin/env python3
"""Portalのローカル認証設定を準備する。既定は確認のみで、Azureは変更しない。"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

from deployment.helpers.configuration import load_configuration, _read_protected_file
from deployment.helpers.paths import REPOSITORY, local_paths, private_parent


class PreparationError(ValueError):
    """秘密の値を含めず、手元で見直す必要がある状態を伝える。"""


class PreparationArgumentParser(argparse.ArgumentParser):
    """入力間違いでもコマンドラインの値をエラーへ転載しない。"""

    def error(self, _message: str) -> None:
        """引数の内容にかかわらず固定した説明で停止する。"""
        self.exit(2, "Local Portal command stopped: invalid arguments; no command was started.\n")


def encode_environment(values: dict[str, str]) -> str:
    """dotenvの展開対象になる文字をエスケープし、値を1行ずつ保存する。"""
    lines = ["# Generated for local Portal authentication. Do not commit or share."]
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in value for c in "\r\n\0"):
            raise PreparationError("Environment values must be single-line strings")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + "\n"


def read_environment(path: Path) -> dict[str, str]:
    """このコマンドが生成した書式だけを読み、任意のシェル式を実行しない。"""
    if path.is_symlink() or not path.is_file():
        raise PreparationError("Existing local settings must be regular files")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise PreparationError("Existing local settings require owner-only permissions (chmod 600)")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        matched = re.fullmatch(r'([A-Z][A-Z0-9_]*)="((?:\\[\\"$]|[^"\\])*)"', line)
        if not matched or matched[1] in values:
            raise PreparationError("Existing local settings use an unexpected format; no files were changed")
        values[matched[1]] = re.sub(r'\\([\\"$])', r'\1', matched[2])
    return values


def protect_new_file(path: Path) -> None:
    """新規ファイルを所有者限定にする。Windowsでは継承ACLも外す。"""
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    if os.name != "nt":
        return
    identity = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], check=True,
        capture_output=True, text=True, timeout=10,
    )
    rows = list(csv.reader(io.StringIO(identity.stdout.strip())))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-[0-9-]+", rows[0][1]):
        raise PreparationError("Unable to determine the current user's file permissions")
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{rows[0][1]}:(F)"],
        check=True, capture_output=True, text=True, timeout=10,
    )


def write_new_pair(documents: dict[Path, dict[str, str]], root: Path) -> None:
    """既存ファイルは上書きせず、途中で失敗した場合は今回作った分だけ取り除く。"""
    created: list[Path] = []
    try:
        for path, values in documents.items():
            private_parent(path, root)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            try:
                protect_new_file(path)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    fd = -1
                    stream.write(encode_environment(values))
            finally:
                if fd >= 0:
                    os.close(fd)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def expected_settings(configuration: dict) -> dict[str, str]:
    """既存のEntraアプリ参照からローカル用の公開設定だけを組み立てる。"""
    profile = configuration["portalAuthentication"]["credentialProviderName"]
    providers = [item for item in configuration["credentialProviders"] if item["name"] == profile]
    if len(providers) != 1:
        raise PreparationError("The Portal Entra credential profile must match exactly one configured provider")
    provider = providers[0]
    return {
        "BETTER_AUTH_URL": "http://localhost:3000",
        "MICROSOFT_CLIENT_ID": provider["clientId"],
        "MICROSOFT_TENANT_ID": provider["oauthTenantId"],
        "PORTAL_SQL_AUTH": "local-sql",
        "PORTAL_SQL_SERVER": "127.0.0.1",
        "PORTAL_SQL_HOST": "127.0.0.1",
        "PORTAL_SQL_PORT": "14330",
        "PORTAL_SQL_DATABASE": "egakumi_portal",
        "PORTAL_SQL_USER": "portal_app",
        "PORTAL_SQL_TRUST_LOCAL_CERTIFICATE": "true",
    }


def environment_paths(configuration: dict, root: Path) -> tuple[Path, Path]:
    """ローカルPortalの実行用とSQL管理用の保存先を同じ環境へ限定する。"""
    paths = local_paths(configuration, root)
    return (paths.checked(paths.root / "portal/runtime.env"),
            paths.checked(paths.secrets / "portal-local-sql-admin.env"))


def reject_legacy_settings(root: Path) -> None:
    """Next.jsが自動読込する旧設定は読まず、明示的な移行を求める。"""
    portal = root / "apps/portal"
    names = (".env", ".env.local", ".env.development", ".env.development.local",
             ".env.production", ".env.production.local", ".env.test", ".env.test.local")
    if any((portal / name).exists() or (portal / name).is_symlink() for name in names):
        raise PreparationError("Legacy Portal environment settings exist; migrate them to .local/<environment>/portal/runtime.env before continuing. No legacy settings were read or changed")


def prepare(configuration: dict, root: Path, *, execute: bool, accept_sql_eula: bool) -> dict:
    """実行時だけ既存Entra secretを読み、管理用・アプリ用を分けて保護保存する。"""
    reject_legacy_settings(root)
    runtime_path, admin_path = environment_paths(configuration, root)
    paths = (runtime_path, admin_path)
    existing = [path.exists() or path.is_symlink() for path in paths]
    result = {
        "mode": "execute" if execute else "plan",
        "action": "reuse" if all(existing) else "create" if not any(existing) else "review-existing-files",
        "runtimeEnvironmentFile": str(runtime_path),
        "sqlAdminEnvironmentFile": str(admin_path),
        "sqlHost": "127.0.0.1:14330",
        "sqlDatabase": "egakumi_portal",
        "sqlEdition": "Developer (local development only)",
        "sqlEulaAccepted": accept_sql_eula,
        "parameterFileChanged": False,
        "azureWrites": 0,
        "containerStarted": False,
    }
    if not execute:
        return result
    if not accept_sql_eula:
        raise PreparationError("SQL Server Developer EULA consent is required: review it, then pass --accept-sql-eula")
    if any(existing) and not all(existing):
        raise PreparationError("Only one local settings file exists; review it before creating the pair. No files were changed")
    expected = expected_settings(configuration)
    profile = configuration["portalAuthentication"]["credentialProviderName"]
    provider = next(item for item in configuration["credentialProviders"] if item["name"] == profile)
    client_secret = _read_protected_file(provider["clientSecretFile"], "Portal Entra client secret")
    if not client_secret or any(c in client_secret for c in "\r\n\0"):
        raise PreparationError("The existing Entra client secret must be a non-empty, single-line value")
    expected["MICROSOFT_CLIENT_SECRET"] = client_secret
    if all(existing):
        for path in paths:
            private_parent(path, root)
        runtime = read_environment(runtime_path)
        admin = read_environment(admin_path)
        required_runtime = set(expected) | {"BETTER_AUTH_SECRET", "PORTAL_SQL_PASSWORD"}
        # 後から手元で追加したGateway接続設定だけを許容し、認証設定とともに変更せず再利用する。
        optional_runtime = {"PORTAL_GATEWAY_URL", "PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE"}
        if not required_runtime <= set(runtime) <= required_runtime | optional_runtime:
            raise PreparationError("Existing Portal settings differ from the generated local format; no files were changed")
        if any(runtime.get(key) != value for key, value in expected.items()):
            raise PreparationError("Existing Portal settings differ from the selected configuration; no files were changed")
        if len(runtime["BETTER_AUTH_SECRET"]) < 32 or len(runtime["PORTAL_SQL_PASSWORD"]) < 32:
            raise PreparationError("Existing local credentials are incomplete; no files were changed")
        required_admin = admin_settings(runtime, admin.get("MSSQL_SA_PASSWORD", ""))
        if admin != required_admin or len(admin["MSSQL_SA_PASSWORD"]) < 32:
            raise PreparationError("Existing administrator and runtime settings do not match; no files were changed")
        return result
    runtime = {
        **expected,
        "BETTER_AUTH_SECRET": secrets.token_urlsafe(32),
        "PORTAL_SQL_PASSWORD": "aA9_" + secrets.token_urlsafe(36),
    }
    admin = admin_settings(runtime, "aA9_" + secrets.token_urlsafe(36))
    write_new_pair({runtime_path: runtime, admin_path: admin}, root)
    return result


def admin_settings(runtime: dict[str, str], admin_password: str) -> dict[str, str]:
    """SQL初期化だけで使う管理者設定を作り、アプリの資格情報とは分離する。"""
    return {
        "ACCEPT_EULA": "Y",
        "MSSQL_PID": "Developer",
        "MSSQL_SA_PASSWORD": admin_password,
        "PORTAL_SQL_AUTH": "local-sql",
        "PORTAL_SQL_SERVER": runtime["PORTAL_SQL_SERVER"],
        "PORTAL_SQL_HOST": runtime["PORTAL_SQL_HOST"],
        "PORTAL_SQL_PORT": runtime["PORTAL_SQL_PORT"],
        "PORTAL_SQL_DATABASE": runtime["PORTAL_SQL_DATABASE"],
        "PORTAL_SQL_USER": "sa",
        "PORTAL_SQL_PASSWORD": admin_password,
        "PORTAL_SQL_TRUST_LOCAL_CERTIFICATE": "true",
        "PORTAL_SQL_RUNTIME_USER": runtime["PORTAL_SQL_USER"],
        "PORTAL_SQL_RUNTIME_PASSWORD": runtime["PORTAL_SQL_PASSWORD"],
    }


def main(argv: list[str] | None = None) -> int:
    """既定は計画表示。明示実行でもファイル作成だけでコンテナを起動しない。"""
    parser = PreparationArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--accept-sql-eula", action="store_true", help="SQL Server Developerのライセンス条項に同意した場合だけ指定")
    args = parser.parse_args(argv)
    result = prepare(load_configuration(args.config), REPOSITORY,
                     execute=args.execute, accept_sql_eula=args.accept_sql_eula)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreparationError as error:
        raise SystemExit(f"Local Portal preparation stopped: {error}") from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise SystemExit("Local Portal preparation stopped: configuration or protected file preparation failed. Secret values were not displayed.") from None
