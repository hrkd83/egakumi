#!/usr/bin/env python3
"""公開境界をGitオブジェクト・作業ツリー・メタデータから検査する。

Python 3.11以上の標準ライブラリーだけを使う。検出値や入力由来の
パスを出力しない。終了値0は定義済み検査の通過、1は検出、2は検査不能。
自然言語や画像の意味を含む機密性の完全な判定は人による確認が必要。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlsplit


UUID = re.compile(
    r"(?i)(?<![a-f0-9])[a-f0-9]{8}(?:-[a-f0-9]{4}){3}"
    r"-[a-f0-9]{12}(?![a-f0-9])"
)
EMAIL = re.compile(r"[\w.+%-]+@(?:[\w-]+\.)+[a-zA-Z]{2,63}")
URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://([^\s<>\"'`{}\\]+)")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])(?:[a-fA-F0-9]{0,4}:){2,}"
                  r"[a-fA-F0-9:]{0,40}(?![\w:])")
CLOUD_HOST = re.compile(
    r"(?i)(?<![\w.-])(?:[a-z0-9-]+\.)+(?:onmicrosoft\.com|"
    r"azure-api\.net|azurewebsites\.net|azurecontainerapps\.io|"
    r"openai\.azure\.com|cognitiveservices\.azure\.com|"
    r"vault\.azure\.net|database\.windows\.net|"
    r"(?:blob|file|queue|table)\.core\.windows\.net)(?![\w.-])"
)
LOCAL_PATH = re.compile(r"(?:/(?:Users|home)/|(?i:[A-Z]:[\\/]Users[\\/]))"
                        r"[^\s/\\<>\"'`{}]+")
TOKEN = re.compile(
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})|"
    r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|"
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
ASSIGNMENT = re.compile(
    r"(?i)\b(?:subscription[_-]?id|tenant[_-]?id|object[_-]?id|"
    r"client[_-]?id|principal[_-]?id|application[_-]?id|"
    r"account[_-]?id|project[_-]?id|project[_-]?number|"
    r"billing[_-]?(?:account|id))\b[\"']?\s*[:=]\s*"
    r"[\"']([^\"'\n]{1,200})[\"']"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|client[_-]?secret|api[_-]?key|accountkey|"
    r"access[_-]?token|refresh[_-]?token|sas[_-]?token|"
    r"connection[_-]?string)\b[\"']?\s*[:=]\s*[\"']"
    r"([^\"'\n]{8,})[\"']"
)
UNQUOTED_CONFIG = re.compile(
    r"(?im)^\s*(?:-\s*)?(subscription[_-]?id|tenant[_-]?id|object[_-]?id|"
    r"client[_-]?id|principal[_-]?id|project[_-]?id|account[_-]?id|"
    r"password|client[_-]?secret|api[_-]?key|access[_-]?token)"
    r"\s*:\s*([a-zA-Z0-9][a-zA-Z0-9_.!+-]{2,})\s*(?:#.*)?$"
)
STANDARD_ENV_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+|\$env:)?(GOOGLE_CLOUD_PROJECT|GCLOUD_PROJECT|"
    r"AWS_ACCOUNT_ID|AZURE_SUBSCRIPTION_ID|AZURE_TENANT_ID|AZURE_CLIENT_ID|"
    r"AZURE_OBJECT_ID|AZURE_CLIENT_SECRET|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"OPENAI_API_KEY|GITHUB_TOKEN)\s*[:=]\s*"
    r"(?:\"([^\"\r\n]*)\"|'([^'\r\n]*)'|([a-zA-Z0-9][a-zA-Z0-9_.!+-]*))"
    r"\s*(?:#.*)?$"
)
BASE64 = re.compile(r"(?<![\w+/])[A-Za-z0-9+/]{24,}={0,2}(?![\w+/=])")
SENSITIVE_PATH = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\..+)?|\.azure|\.aws|\.state|secrets)(?:/|$)|"
    r"(?i:.*\.(?:pfx|p12|pem|key|token|local\.json))$"
)
DOCUMENTATION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "2001:db8::/32", "127.0.0.0/8", "::1/128",
))
MAX_TEXT_BYTES = 8 * 1024 * 1024
POLICY_PATH = "scripts/public-data-policy.json"
GENERATED_DEPENDENCY_ROOTS = {
    ".venv", "apps/portal/node_modules", "apps/portal/.next",
}


class InspectionError(Exception):
    """値を出さずに検査不能を通知する。"""


class SafeParser(argparse.ArgumentParser):
    """不正なCLI引数をエラーログへ再掲しない。"""

    def error(self, _message: str) -> None:
        """引数の内容にかかわらず固定したエラーで止める。"""
        self.exit(2, "public-data-guard: ERROR invalid-arguments; operation refused\n")


def git(repository: Path, *args: str, input_data: bytes | None = None) -> bytes:
    """固定した引数境界でGitを実行し、失敗時の生ログは外へ出さない。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args], input=input_data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            check=False, env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InspectionError("git-unavailable") from error
    if result.returncode:
        raise InspectionError("git-object-unavailable")
    return result.stdout


def synthetic(value: str) -> bool:
    """実環境から独立した明示的プレースホルダーだけを認める。"""
    value = value.strip()
    if not value:
        return True
    if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        compact = value.replace("-", "").lower()
        return len(set(compact)) == 1
    return bool(
        re.fullmatch(r"<[^<>]+>|\$\{[^{}]+\}|\$\([^()]+\)|\{[^{}]+\}", value)
        or re.fullmatch(r"\$(?:env:)?[A-Za-z_][A-Za-z0-9_]*", value)
        or value in {"common", "organizations", "consumers", "123456789012",
                     "egakumi-example", "undefined", "null", "example",
                     "dummy", "test", "fake", "sample", "placeholder",
                     "redacted", "not-a-real", "your"}
    )


class Guard:
    """信頼したポリシーを使って入力を静的に検査する。"""

    def __init__(self, repository: Path, policy: Path) -> None:
        self.repository = repository.resolve()
        try:
            self.policy = json.loads(policy.read_text(encoding="utf-8"))
            if self.policy["version"] != 1:
                raise ValueError
            self.constants = set(self.policy["public_uuids"])
            self.hosts = set(self.policy["public_hosts"])
            self.emails = set(self.policy["public_emails"])
            self.authors = set(self.policy.get("public_author_names", []))
            self.exceptions = self.policy["literal_exceptions"]
            self.binary = self.policy["reviewed_binary_assets"]
            self.exception_keys: set[tuple[str, str, str]] = set()
            self.policy_values: set[tuple[str, str]] = set()
            for entry in self.exceptions:
                if not all(isinstance(entry[key], str) and entry[key]
                           for key in ("rule", "value", "reason")):
                    raise ValueError
                paths = entry.get("paths", [entry.get("path")])
                if not isinstance(paths, list) or not paths:
                    raise ValueError
                for path in paths:
                    if not isinstance(path, str) or not path:
                        raise ValueError
                    self.exception_keys.add((path, entry["rule"], entry["value"]))
                self.policy_values.add((entry["rule"], entry["value"]))
            self.binary_keys: set[tuple[str, str]] = set()
            for entry in self.binary:
                if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    raise ValueError
                if not entry["path"] or not entry["reason"]:
                    raise ValueError
                self.binary_keys.add((entry["path"], entry["sha256"]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InspectionError("policy-unavailable-or-invalid") from error
        self.findings: set[tuple[str, str, int]] = set()
        self.locations: dict[str, str] = {}
        self.checked: set[tuple[str, str, str]] = set()
        self.text_checked: set[tuple[str, str, int, bool]] = set()
        self.blobs: dict[str, bytes] = {}
        self.files = 0
        self.commits = 0
        self.private_terms: list[str] = []
        private_file = os.environ.get("EGAKUMI_PRIVATE_TERMS_FILE")
        if private_file:
            term_path = Path(private_file).resolve()
            if term_path.is_relative_to(self.repository):
                raise InspectionError("private-terms-must-stay-outside-checkout")
            try:
                self.private_terms = [line.casefold() for line in
                                      term_path.read_text("utf-8").splitlines()
                                      if line.strip() and not line.startswith("#")]
                if not self.private_terms:
                    raise ValueError
            except (OSError, UnicodeError, ValueError) as error:
                raise InspectionError("private-terms-unavailable") from error

    def record(self, path: str, rule: str, value: str,
               text: str = "", offset: int = 0) -> None:
        """例外は完全一致だけとし、出力用には値を保持しない。"""
        if (path, rule, value) in self.exception_keys:
            return
        if path == POLICY_PATH and (rule, value) in self.policy_values:
            return
        if path not in self.locations:
            self.locations[path] = f"item-{len(self.locations) + 1}"
        line = text.count("\n", 0, offset) + 1 if text else 0
        self.findings.add((rule, self.locations[path], line))

    @staticmethod
    def reserved_host(host: str) -> bool:
        """文書・合成例のために予約されたドメインだけを判定する。"""
        host = host.lower().rstrip(".")
        return (
            host in {"localhost", "example.com", "example.org", "example.net"}
            or host.endswith((".example.com", ".example.org", ".example.net",
                              ".invalid", ".localhost", ".test"))
        )

    def allowed_host(self, host: str) -> bool:
        """共通URLホストの承認を、個々のメールアドレスへ拡張しない。"""
        return host.lower().rstrip(".") in self.hosts or self.reserved_host(host)

    def scan_text(self, path: str, text: str, depth: int = 0,
                  approved_asset: bool = False) -> None:
        """本文と代表的なエンコードを上限付きで検査する。"""
        identity = (path, hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(), depth, approved_asset)
        if identity in self.text_checked:
            return
        self.text_checked.add(identity)
        for carrier in re.finditer(r"(?i)data:[^\s\"'<>]{0,120};base64,[A-Za-z0-9+/=]+", text):
            if not approved_asset:
                value = carrier.group() if len(carrier.group()) <= 128 else ""
                self.record(path, "embedded-data-review-required", value)
        for term in self.private_terms:
            if term in text.casefold():
                self.record(path, "private-term", "", text)
        for match in UUID.finditer(text):
            value = match.group().lower()
            if value not in self.constants and not synthetic(value):
                self.record(path, "unknown-uuid", match.group(), text,
                            match.start())
        for match in EMAIL.finditer(text):
            value = match.group()
            if value not in self.emails and not self.reserved_host(value.split("@")[-1]):
                self.record(path, "nonpublic-email", value, text, match.start())
        for match in URL.finditer(text):
            value = match.group()
            try:
                parsed = urlsplit(value.rstrip(".,;)"))
                host = parsed.hostname
                if parsed.username or parsed.password:
                    self.record(path, "url-credentials", value, text, match.start())
                if host:
                    host = host.encode("idna").decode("ascii")
                    if (not self.allowed_host(host) and not self.safe_ip(host)
                            and re.fullmatch(r"[a-zA-Z0-9.-]+", host)):
                        self.record(path, "nonpublic-host", host, text, match.start())
                    if host in {"login.microsoftonline.com", "sts.windows.net"}:
                        tenant = parsed.path.strip("/").split("/", 1)[0]
                        if (tenant and tenant not in {"common", "organizations", "consumers"}
                                and not tenant.startswith(("$", "<", "{"))
                                and not synthetic(tenant)):
                            self.record(path, "tenant-url", tenant, text, match.start())
                if re.search(r"(?i)[?&](?:sig|token|code|key)=[^&\s]{12,}", value):
                    self.record(path, "signed-url", value, text, match.start())
            except (ValueError, UnicodeError):
                self.record(path, "unparseable-url", value, text, match.start())
        for match in IPV4.finditer(text):
            value = match.group()
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            if not self.safe_ip(value):
                self.record(path, "nonpublic-ip", value, text, match.start())
        for match in IPV6.finditer(text):
            value = match.group()
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            if not self.safe_ip(value):
                self.record(path, "nonpublic-ip", value, text, match.start())
        for match in CLOUD_HOST.finditer(text):
            if not self.allowed_host(match.group()):
                self.record(path, "nonpublic-host", match.group(), text,
                            match.start())
        for pattern, rule in ((LOCAL_PATH, "local-user-path"),
                              (TOKEN, "credential-pattern")):
            for match in pattern.finditer(text):
                self.record(path, rule, match.group(), text, match.start())
        for pattern, rule in ((ASSIGNMENT, "environment-identifier"),
                              (SECRET_ASSIGNMENT, "credential-assignment")):
            for match in pattern.finditer(text):
                value = match.group(1)
                if (not synthetic(value) and value.lower() not in self.constants
                        and not UUID.fullmatch(value)):
                    self.record(path, rule, value, text, match.start())
        for match in STANDARD_ENV_ASSIGNMENT.finditer(text):
            value = next(group for group in match.groups()[1:] if group is not None)
            if (not synthetic(value) and value.lower() not in self.constants
                    and not UUID.fullmatch(value)):
                self.record(path, "environment-variable-literal", value, text,
                            match.start())
        for match in UNQUOTED_CONFIG.finditer(text):
            if Path(path).suffix.lower() in {
                ".py", ".ts", ".tsx", ".js", ".mjs", ".bicep",
                ".bicepparam", ".ps1", ".sh",
            }:
                continue
            value = match.group(2)
            if (value not in {"str", "string", "int", "bool", "dict", "list",
                              "None", "number", "object", "true", "false"}
                    and not synthetic(value)
                    and value.lower() not in self.constants):
                self.record(path, "unquoted-sensitive-setting", value, text,
                            match.start())
        if depth >= 3:
            if BASE64.search(text) and not approved_asset:
                self.record(path, "encoding-depth-review-required", "")
            return
        normalized = unicodedata.normalize("NFKC", html.unescape(unquote(text)))
        normalized = re.sub(r"\\u([0-9a-fA-F]{4})",
                            lambda m: chr(int(m.group(1), 16)), normalized)
        if normalized != text:
            self.scan_text(path, normalized, depth + 1, approved_asset)
        for match in BASE64.finditer(text):
            value = match.group()
            if re.fullmatch(r"[0-9a-fA-F]{32,128}", value):
                continue
            if len(value) > MAX_TEXT_BYTES * 2:
                self.record(path, "encoded-data-review-required", "")
                continue
            try:
                decoded = base64.b64decode(value + "=" * (-len(value) % 4),
                                           validate=True)
                binary_marker = decoded.startswith((
                    b"\x89PNG", b"\xff\xd8", b"PK\x03\x04", b"RIFF", b"ID3",
                    b"%PDF", b"\x1f\x8b",
                ))
                if binary_marker:
                    if not approved_asset:
                        self.record(path, "embedded-binary-review-required", "")
                elif decoded:
                    if b"\x00" in decoded and decoded.count(b"\x00") >= len(decoded) // 4:
                        # BOMのないUTF-16も、表示可能なら文字列として調べる。
                        try:
                            byte_order = ("utf-16-be" if decoded[::2].count(0)
                                          > decoded[1::2].count(0) else "utf-16-le")
                            decoded_text = decoded.decode(byte_order)
                            if any(ord(c) < 32 and c not in "\r\n\t" for c in decoded_text):
                                raise UnicodeError
                        except UnicodeError:
                            if not approved_asset:
                                self.record(path, "encoded-binary-review-required", "")
                            continue
                    else:
                        decoded_text = decoded.decode("utf-8")
                    self.scan_text(path, decoded_text, depth + 1, approved_asset)
            except (ValueError, UnicodeError):
                context = text[max(0, match.start() - 80):match.start()]
                if re.search(r"(?i)(?:b64decode|frombase64string)\s*\(\s*b?[\"']$", context):
                    if not approved_asset:
                        self.record(path, "encoded-binary-review-required", "")
                continue

    @staticmethod
    def safe_ip(value: str) -> bool:
        """文書用・ループバック・未指定アドレスだけを共通で許可する。"""
        try:
            address = ipaddress.ip_address(value)
            return address.is_unspecified or any(
                address.version == network.version and address in network
                for network in DOCUMENTATION_NETWORKS
            )
        except ValueError:
            return False

    def binary_approved(self, path: str, data: bytes) -> bool:
        """既に内容を目視確認した配布素材との完全一致だけを認める。"""
        digest = hashlib.sha256(data).hexdigest()
        return (path, digest) in self.binary_keys

    def scan_blob(self, path: str, data: bytes, mode: str = "100644") -> None:
        """ファイル名・種別・本文を調べ、未知のバイナリーは拒否する。"""
        identity = (path, hashlib.sha256(data).hexdigest(), mode)
        if identity in self.checked:
            return
        self.checked.add(identity)
        self.files += 1
        self.scan_text("@path", path)
        if SENSITIVE_PATH.search(path) and not path.endswith(".example"):
            self.record(path, "sensitive-path", path)
        if mode not in {"100644", "100755"}:
            self.record(path, "unsupported-file-mode", "")
            return
        if len(data) > MAX_TEXT_BYTES:
            if not self.binary_approved(path, data):
                self.record(path, "oversize-review-required", "")
            return
        try:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            else:
                text = data.decode("utf-8-sig")
            if "\x00" in text:
                raise UnicodeError
        except UnicodeError:
            if not self.binary_approved(path, data):
                self.record(path, "binary-review-required", "")
            return
        self.scan_text(path, text, approved_asset=self.binary_approved(path, data))

    def index(self) -> None:
        """作業ファイルではなくステージされた実際のバイト列を調べる。"""
        records = git(self.repository, "ls-files", "--stage", "-z")
        for record in records.split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, oid, stage = header.split()
            if stage != b"0":
                raise InspectionError("unmerged-index")
            self.scan_blob(os.fsdecode(name), self.blob(oid.decode()),
                           mode.decode())
        self.current_metadata()

    def tracked(self) -> None:
        """追跡対象の現在の作業ファイルを調べる。"""
        for name in git(self.repository, "ls-files", "-z").split(b"\0"):
            if name:
                self.work_file(os.fsdecode(name))

    def work_file(self, name: str) -> None:
        """リンクは追跡せず、読み取り不能も安全側で止める。"""
        path = self.repository / name
        if path.is_symlink():
            self.record(name, "symlink-review-required", "")
            return
        try:
            if path.exists():
                self.scan_blob(name, path.read_bytes())
        except OSError as error:
            raise InspectionError("worktree-unreadable") from error

    def worktree(self, strict: bool = False) -> None:
        """ignore対象も調べ、通常時は明記した依存生成先だけを省く。"""
        def failed(_error: OSError) -> None:
            raise InspectionError("worktree-unreadable")

        for root, directories, files in os.walk(
                self.repository, followlinks=False, onerror=failed):
            if Path(root) == self.repository:
                directories[:] = [item for item in directories if item != ".git"]
                files = [item for item in files if item != ".git"]
            for item in directories[:]:
                path = Path(root) / item
                if path.is_symlink():
                    self.work_file(path.relative_to(self.repository).as_posix())
                    directories.remove(item)
                elif (not strict and path.relative_to(self.repository).as_posix()
                      in GENERATED_DEPENDENCY_ROOTS):
                    directories.remove(item)
            for item in files:
                self.work_file((Path(root) / item).relative_to(
                    self.repository).as_posix())

    def current_metadata(self) -> None:
        """現在のブランチと公開用著者・コミッター情報を検査する。"""
        self.scan_text("@refs", "\n".join(self.public_refs()))
        for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            identity = git(self.repository, "var", kind)
            self.scan_text("@identity", identity.decode("utf-8", "strict"))
            name = identity.decode("utf-8", "strict").split(" <", 1)[0]
            if name not in self.authors:
                self.record("@identity", "unapproved-author-name", "")

    def public_refs(self) -> list[str]:
        """通常の公開対象となるブランチ・追跡参照・タグ名を取得する。"""
        refs = git(self.repository, "for-each-ref", "--format=%(refname)",
                   "refs/heads/", "refs/remotes/", "refs/tags/")
        return refs.decode("utf-8", "strict").splitlines()

    def blob(self, oid: str) -> bytes:
        """同じGitオブジェクトは再読込せず、内容を外へ表示しない。"""
        if oid not in self.blobs:
            self.blobs[oid] = git(self.repository, "cat-file", "blob", oid)
        return self.blobs[oid]

    def scan_tag(self, oid: str) -> None:
        """注釈付きタグのメッセージとtaggerを、参照外オブジェクトでも調べる。"""
        tag = git(self.repository, "cat-file", "tag", oid).decode("utf-8", "strict")
        self.scan_text("@tag", tag)
        for line in tag.splitlines():
            if line.startswith("tagger "):
                tagger_name = line[7:].split(" <", 1)[0]
                if tagger_name not in self.authors:
                    self.record("@tag", "unapproved-author-name", "")

    def history(self, revisions: list[str] | None = None) -> None:
        """公開参照の全履歴、または明示された任意の対象を検査する。"""
        if git(self.repository, "rev-parse", "--is-shallow-repository").strip() != b"false":
            raise InspectionError("incomplete-shallow-history")
        if revisions:
            resolved = []
            for revision in revisions:
                self.scan_text("@requested-refs", revision)
                object_id = git(self.repository, "rev-parse", "--verify",
                                "--end-of-options", revision + "^{object}").strip().decode()
                for _ in range(16):
                    kind = git(self.repository, "cat-file", "-t", object_id).strip()
                    if kind == b"commit":
                        break
                    if kind != b"tag":
                        raise InspectionError("unsupported-push-object")
                    self.scan_tag(object_id)
                    header = git(self.repository, "cat-file", "tag", object_id).splitlines()[0]
                    if not re.fullmatch(rb"object [0-9a-f]{40,64}", header):
                        raise InspectionError("invalid-tag-object")
                    object_id = header.split()[1].decode()
                else:
                    raise InspectionError("tag-nesting-review-required")
                resolved.append(git(self.repository, "rev-parse", "--verify",
                                    "--end-of-options", revision + "^{commit}").strip().decode())
            revision_args = resolved
        else:
            revision_args = ["HEAD", *self.public_refs()]
        commits = git(self.repository, "rev-list", *revision_args).splitlines()
        for commit in commits:
            oid = commit.decode("ascii")
            self.commits += 1
            metadata = git(self.repository, "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", oid)
            metadata_text = metadata.decode("utf-8", "strict")
            self.scan_text("@commit", metadata_text)
            fields = metadata_text.splitlines()
            if len(fields) < 4:
                raise InspectionError("invalid-commit-metadata")
            for name in (fields[0], fields[2]):
                if name not in self.authors:
                    self.record("@commit", "unapproved-author-name", "")
            for record in git(self.repository, "ls-tree", "-r", "-z", oid).split(b"\0"):
                if not record:
                    continue
                header, name = record.split(b"\t", 1)
                mode, object_type, blob = header.split()
                path = os.fsdecode(name)
                if object_type != b"blob":
                    self.record(path, "unsupported-git-object", "")
                    continue
                self.scan_blob(path, self.blob(blob.decode()), mode.decode())
        for name in self.public_refs():
            self.scan_text("@refs", name)
            kind = git(self.repository, "cat-file", "-t", name).strip()
            if kind == b"tag":
                self.scan_tag(name)

    def pre_push_refs(self, data: str) -> None:
        """push先の名前と参照外SHA指定も送信前に検査する。"""
        revisions = []
        for line in data.splitlines():
            fields = line.split()
            if len(fields) != 4:
                raise InspectionError("invalid-pre-push-input")
            local_ref, local_oid, remote_ref, remote_oid = fields
            if not all(re.fullmatch(r"[0-9a-f]{40,64}", oid)
                       for oid in (local_oid, remote_oid)):
                raise InspectionError("invalid-pre-push-input")
            self.scan_text("@push-refs", local_ref + "\n" + remote_ref)
            if set(local_oid) != {"0"}:
                revisions.append(local_oid)
        if revisions:
            self.history(revisions)


def main() -> int:
    """CLI入力を検査し、結果は値を含まない固定形式で出力する。"""
    parser = SafeParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).with_name("public-data-policy.json"))
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--tracked", action="store_true")
    parser.add_argument("--worktree", action="store_true")
    parser.add_argument("--worktree-strict", action="store_true")
    parser.add_argument("--all-history", action="store_true")
    parser.add_argument("--revisions", nargs="+")
    parser.add_argument("--commit-message", type=Path)
    parser.add_argument("--pre-push-refs", action="store_true")
    args = parser.parse_args()
    try:
        guard = Guard(args.repository, args.policy)
        if args.staged:
            guard.index()
        if args.worktree or args.worktree_strict:
            guard.worktree(strict=args.worktree_strict)
        if args.all_history:
            guard.history()
        if args.revisions:
            guard.history(args.revisions)
        if args.commit_message:
            guard.scan_text("@pending-message", args.commit_message.read_text("utf-8"))
            guard.current_metadata()
        if args.pre_push_refs:
            guard.pre_push_refs(sys.stdin.read())
        if args.tracked or not any((
                args.staged, args.worktree, args.worktree_strict,
                args.all_history, args.revisions, args.commit_message,
                args.pre_push_refs)):
            guard.tracked()
        if guard.findings:
            for rule, location, line in sorted(guard.findings)[:100]:
                print(f"public-data-guard: BLOCK {rule} location={location} line={line}")
            print(f"public-data-guard: BLOCK findings={len(guard.findings)}; "
                  "values and paths withheld")
            return 1
        print(f"public-data-guard: PASS files={guard.files} commits={guard.commits}")
        return 0
    except (InspectionError, OSError, UnicodeError, ValueError) as error:
        code = str(error) if isinstance(error, InspectionError) else "inspection-failed"
        print(f"public-data-guard: ERROR {code}; operation refused")
        return 2


if __name__ == "__main__":
    sys.exit(main())
