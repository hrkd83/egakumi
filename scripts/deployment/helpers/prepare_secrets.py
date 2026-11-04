#!/usr/bin/env python3
"""新規clone後に、証明書のDNS検証とEntra連携で使う秘密ファイルを用意する。

利用者はJSONを手書きせず、選んだDNS事業者の資格情報を端末へ入力する。
値は画面へ表示せず、Git管理外のsecretsへ所有者だけが読める形で保存する。
有効な既存ファイルは検査して再利用する。DNS事業者を変更した場合だけ、
新しい入力の検証後にDNS資格情報を置換する。鍵生成・Azureへの通信はしない。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid
import warnings

# ファイルを直接実行しても、同じdeployment.helpersパッケージから読み込む。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "deployment.helpers"

from .configuration import ConfigurationError, DnsProviderMismatchError, _read_protected_file, load_configuration, load_dns_plugin_arguments, validate_azure_dns_identity
from .paths import REPOSITORY, local_paths


ROOT = REPOSITORY


class SecretPreparationError(ValueError):
    """秘密値・外部処理の生出力を含めず、利用者に対処を案内するエラー。"""


def _fail(message: str) -> None:
    """入力した値を補間せず、固定の理由だけで安全に停止する。"""
    raise SecretPreparationError(message) from None


def _checked_path(raw: str | Path) -> Path:
    """正規化済みの参照先を受け取り、可視のリンクや不正な親を拒否する。"""
    if not isinstance(raw, (str, Path)) or not str(raw) or not Path(raw).is_absolute():
        _fail("Secret input must reference a normalized absolute file path.")
    path = Path(raw)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            _fail("Secret input must not use a visible symbolic link; existing files were preserved.")
        if parent != path and parent.exists() and not parent.is_dir():
            _fail("A secret input parent is not a directory; existing files were preserved.")
    if path.exists() and not path.is_file():
        _fail("Secret input must reference a regular file; existing files were preserved.")
    return path


def _new_target(path: Path, root: Path) -> Path:
    """新規保存は選択した環境のsecrets配下に限定する。既存の外部参照は変更しない。"""
    canonical_root = Path(root).resolve()
    try:
        relative = path.relative_to(canonical_root)
    except ValueError:
        _fail("Missing secret files can only be created under the selected environment's secrets directory; provide an existing protected external file or correct the configured path.")
    if len(relative.parts) < 2 or relative.parts[0] != "secrets" or ".." in relative.parts:
        _fail("Missing secret files can only be created under the selected environment's secrets directory.")
    return _checked_path(path)


def _private_directory(path: Path, root: Path) -> None:
    """新規cloneにsecretsがなくても700で作る。既存の共有権限は勝手に変更しない。"""
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            cursor.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = cursor.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700)):
            _fail("Secret directories must be private regular directories (use chmod 700); existing files were preserved.")


def _read_input(prompt: str, *, guid: bool = False) -> str:
    """対話端末だけから非表示で受け取る。CLI引数やパイプで秘密を渡させない。"""
    if not sys.stdin.isatty():
        _fail("Missing secret inputs require an interactive terminal; do not pass credentials as command-line arguments or pipe them into this command.")
    # getpassが非表示入力を確保できない場合、echo付きfallbackを始める前に停止する。
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass(prompt)
    except getpass.GetPassWarning:
        _fail("A hidden terminal input could not be established; no value was read or written.")
    if (not value or value != value.strip() or any(character in value for character in "\r\n\x00")):
        _fail("Input must be a non-empty single value without surrounding whitespace; no input value was written.")
    if guid:
        try:
            if not uuid.UUID(value).int:
                raise ValueError
        except (ValueError, AttributeError):
            _fail("The requested Azure identifier must be a non-zero UUID; no input value was written.")
    return value


def _dns_value(plugin: str) -> str:
    """DNS事業者の入力だけを、固定版Posh-ACMEが読む型付きJSONへ組み立てる。"""
    if plugin == "Cloudflare":
        arguments = {"CFToken": {"type": "secureString", "value": _read_input("Cloudflare DNS API token（入力非表示）: ")}}
    elif plugin == "Route53":
        arguments = {
            "R53AccessKey": {"type": "string", "value": _read_input("Route53 Access key ID（入力非表示）: ")},
            "R53SecretKey": {"type": "secureString", "value": _read_input("Route53 Secret access key（入力非表示）: ")},
        }
    else:
        _fail("This DNS plugin has no interactive input template. Follow its official Posh-ACME plugin guide and provide a protected pluginArgumentsFile; no file was created.")
    return json.dumps(arguments, ensure_ascii=False, indent=2) + "\n"


def _specifications(config: dict) -> list[dict]:
    """DNS入力とCMの参照・用途を集め、同じファイルへの重複入力をなくす。"""
    specifications = []
    certificate = config.get("certificate", {})
    if certificate.get("mode") == "dnsPlugin" and certificate.get("plugin") != "Azure":
        specifications.append({"kind": "dns", "path": certificate["pluginArgumentsFile"], "certificate": certificate, "purposes": []})
    for provider in config.get("credentialProviders", []):
        specifications.append({"kind": "entra", "path": provider["clientSecretFile"],
                               "purposes": [{"kind": "cm", "name": provider.get("name", ""), "clientId": provider.get("clientId", "")} ]})
    unique = {}
    for specification in specifications:
        path = _checked_path(specification["path"])
        key = str(path)
        if key in unique and unique[key]["kind"] != specification["kind"]:
            _fail("DNS and Entra inputs must use different files; no file was created.")
        if key not in unique:
            unique[key] = {**specification, "path": path, "purposes": []}
        for purpose in specification["purposes"]:
            if purpose not in unique[key]["purposes"]:
                unique[key]["purposes"].append(purpose)
    # Gatewayアプリのsecretを先に、続いて選択DNS事業者の入力へ進む。
    return sorted(unique.values(), key=lambda item: item["kind"] == "dns")


def _public_label(value: str) -> str:
    """設定に含まれる公開名から、端末の制御文字を除く。"""
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value if character.isprintable()).replace("|", "/")[:120]


def _purpose_label(specification: dict) -> str:
    """同じsecretを使う複数のCM設定をまとめ、利用者が対応するアプリを選べるようにする。"""
    if specification["kind"] == "dns":
        return _public_label(specification["certificate"]["plugin"]) + "のDNS資格情報"
    purposes = []
    for purpose in specification["purposes"]:
        title = "CM接続"
        name = _public_label(purpose["name"])
        if name:
            title += ": " + name
        if title not in purposes:
            purposes.append(title)
    return " / ".join(purposes)


def _guide_lines(specification: dict) -> list[str]:
    """必要な値と取得先を、端末で読める短い行で案内する。"""
    if specification["kind"] == "entra":
        lines = ["Entraアプリのシークレットの「値」を用意してください。",
                 "  " + _purpose_label(specification)]
        application_ids = list(dict.fromkeys(_public_label(purpose["clientId"])
                               for purpose in specification["purposes"] if purpose["clientId"]))
        if application_ids:
            lines.append("  アプリID: " + ", ".join(application_ids))
        lines.append("  取得先: Entra > アプリの登録 > 証明書とシークレット >「値」")
        return lines
    plugin = specification["certificate"]["plugin"]
    if plugin == "Cloudflare":
        return ["Cloudflare API tokenを用意してください。",
                "  取得先: Cloudflare > My Profile > API Tokens（DNS Edit / Zone Read）"]
    if plugin == "Route53":
        return ["Route53 Access key IDとSecret access keyを用意してください。",
                "  取得先: AWS IAM > DNS更新用ユーザー > Security credentials > Access keys",
                "  権限: 対象のPublic Hosted ZoneのDNS更新"]
    return []


def _storage_guide(config: dict, root: Path, specifications: list[dict]) -> list[str]:
    """入力する秘密ごとに、端末と構築時の保存先を一行で示す。"""
    lines = []
    for specification in specifications:
        try:
            local = specification["path"].relative_to(root).as_posix()
        except ValueError:
            local = "設定で指定した保護ファイル"
        cloud = "Key Vault" if specification["kind"] == "dns" else ("APIMと認証窓口用Key Vault" if config.get("gateway", {}).get("authentication", {}).get("credentialLogin") else "APIM")
        lines.append("  保存先: " + _public_label(local) + "（構築時に" + cloud + "へ設定）")
    return lines


def _show_guide(config: dict, root: Path, specifications: list[dict], missing: list[dict]) -> None:
    """端末では今回の入力に必要な案内だけを表示する。"""
    reused = [item for item in specifications if item not in missing]
    lines = []
    if reused:
        labels = list(dict.fromkeys(_purpose_label(item) for item in reused))
        lines.append("秘密情報は準備済みです（" + " / ".join(labels) + "を再利用）。")
    if missing:
        lines.extend(["", "秘密情報の入力"])
        replacements = [item for item in missing if "replacementSnapshot" in item]
        if replacements:
            lines.append("DNS事業者が変わったため、資格情報を更新します。")
            for item in replacements:
                destination = item["path"].relative_to(root).as_posix()
                lines.append("置換対象: " + _public_label(destination) + "（" + _public_label(item["certificate"]["plugin"]) + "）")
            lines.append("検査が成功するまでは既存DNSファイルを保持します。")
        for specification in missing:
            lines.extend(_guide_lines(specification))
            lines.extend(_storage_guide(config, root, [specification]))
        lines.append("次の非表示欄へ入力してください。値は端末に保護して保存します。")
    elif not specifications:
        lines.append("秘密情報の追加入力はありません。")
    print("\n".join(lines), flush=True)


def _confirm_ready() -> None:
    """一覧を読んで準備できたことだけを確認する。費用・権限への同意操作ではない。"""
    answer = input("Enterで入力欄へ進む（qで中止）: ")
    if answer.strip().lower() in ("q", "quit"):
        _fail("秘密情報の準備を中止しました。資格情報の入力・ファイル作成・置換は行っていません。")
    if answer.strip():
        _fail("準備確認は Enter で続行、q で中止を選んでください。秘密の値はこの確認欄へ入力しないでください。")


def _validate_file(specification: dict, path: Path) -> None:
    """構築時と同じ読込関数を使い、秘密の値を返さず形式・権限を検査する。"""
    _checked_path(str(path))
    if specification["kind"] == "dns":
        certificate = {**specification["certificate"], "pluginArgumentsFile": str(path)}
        load_dns_plugin_arguments(certificate)
    else:
        value = _read_protected_file(str(path), "Entra client secret")
        if not value.strip():
            _fail("Entra client secret file must not be empty; existing files were preserved.")


def _restrict_staged_permissions(stream, staged: Path) -> None:
    """POSIXはdescriptorで600を保証する。Windowsにないfchmodは呼ばない。"""
    if os.name == "nt":
        # Windowsのアクセス制御は保存先ACLにも従う。POSIXの権限保証とは区別する。
        staged.chmod(0o600)
    else:
        os.fchmod(stream.fileno(), 0o600)


def _publish(specification: dict, value: str, root: Path) -> None:
    """600の一時ファイルを検査後、排他的に公開する。競合先を上書きしない。"""
    target = _new_target(specification["path"], root)
    _private_directory(target.parent, root)
    descriptor, temporary = tempfile.mkstemp(prefix=".secret-input-", dir=target.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _restrict_staged_permissions(stream, staged)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_file(specification, staged)
        _checked_path(str(target))
        _private_directory(target.parent, root)
        try:
            # hard linkは既存の通常ファイルもsymlinkも置換しない。
            os.link(staged, target, follow_symlinks=False)
        except OSError:
            _fail("Secret publication failed or another process created the target; existing files were preserved.")
    finally:
        staged.unlink(missing_ok=True)


def _dns_file_snapshot(path: Path) -> tuple:
    """ファイルの識別情報とhashだけを取り、対話中の編集・置換を検出する。

    秘密の本文・hash・statはログにも戻り値にも公開しません。可視リンクを
    拒否し、読んだdescriptorと現在のパスが同じ通常ファイルかも確認します。
    """
    _checked_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            _fail("DNS secret changed while being checked; existing files were preserved.")
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
        digest = hasher.digest()
        after = os.fstat(stream.fileno())
    current = path.lstat()
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                info.st_mode, info.st_uid, info.st_gid)
    if identity(before) != identity(after) or identity(after) != identity(current):
        _fail("DNS secret changed while being checked; existing files were preserved.")
    return (*identity(after), digest)


def _replace_dns(specification: dict, value: str, root: Path) -> None:
    """検証済み新DNS入力だけを原子的に置換し、対話中の並行更新は上書きしない。

    選択した環境のsecretsに限定し、古いDNSは新しい一時ファイルの型・権限・事業者検査が
    全て成功するまで保持します。同じ処理の並行実行は短時間の排他ファイルで止め、
    置換直前に元のinode・更新時刻・hashを再照合します。中止や不備は旧入力を残します。
    """
    if specification["kind"] != "dns" or "replacementSnapshot" not in specification:
        _fail("Only a confirmed DNS provider mismatch may replace an existing input.")
    target = _new_target(specification["path"], root)
    _private_directory(target.parent, root)
    descriptor, temporary = tempfile.mkstemp(prefix=".secret-input-", dir=target.parent)
    staged = Path(temporary)
    lock = target.with_name("." + target.name + ".replace-lock")
    lock_identity = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _restrict_staged_permissions(stream, staged)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_file(specification, staged)
        try:
            lock_descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            _fail("Another DNS input update is in progress; existing files were preserved.")
        with os.fdopen(lock_descriptor, "wb") as stream:
            info = os.fstat(stream.fileno())
            lock_identity = (info.st_dev, info.st_ino)
        _checked_path(target)
        _private_directory(target.parent, root)
        if _dns_file_snapshot(target) != specification["replacementSnapshot"]:
            _fail("DNS secret changed during input; existing files were preserved. Rerun to check the latest input.")
        # os.replaceは同じディレクトリーの検証済みファイルを一度に切り替えます。
        # コピー途中の空ファイルや不完全なJSONを、構築処理へ渡しません。
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
        if lock_identity is not None:
            try:
                info = lock.lstat()
                if (info.st_dev, info.st_ino) == lock_identity:
                    lock.unlink()
            except FileNotFoundError:
                pass


def prepare_secrets(config: dict, root: Path = ROOT) -> dict:
    """既存入力を検査し、不足分とDNS事業者変更分だけを案内して対話入力する。"""
    certificate = config.get("certificate", {})
    if certificate.get("mode") == "dnsPlugin" and certificate.get("plugin") == "Azure":
        validate_azure_dns_identity(certificate)
    root = Path(root).resolve()
    specifications = _specifications(config)
    pending = []
    result = {"created": [], "reused": [], "replaced": []}
    for specification in specifications:
        path = specification["path"]
        if path.exists():
            before = _dns_file_snapshot(path) if specification["kind"] == "dns" else None
            try:
                _validate_file(specification, path)
            except DnsProviderMismatchError:
                # 破損・権限不備・Entraの不備は自動置換しません。この専用例外
                # だけが、選択済みDNS事業者の資格情報の取り直しへ進めます。
                if specification["kind"] != "dns":
                    raise
                _new_target(path, root)
                if _dns_file_snapshot(path) != before:
                    _fail("DNS secret changed while being checked; existing files were preserved.")
                specification["replacementSnapshot"] = before
                pending.append(specification)
            else:
                if before is not None and _dns_file_snapshot(path) != before:
                    _fail("DNS secret changed while being checked; existing files were preserved.")
                result["reused"].append(str(path))
        else:
            _new_target(path, root)
            if specification["kind"] == "dns" and specification["certificate"].get("plugin") not in ("Cloudflare", "Route53", "Azure"):
                _fail("This DNS plugin has no interactive input template. Follow its official Posh-ACME plugin guide and provide a protected pluginArgumentsFile; no file was created.")
            pending.append(specification)
    _show_guide(config, root, specifications, pending)
    # 非TTYは新規作成・置換・最初の入力より前に停止します。表示した用途と
    # 保存先を確認できる対話端末でだけ、既存DNSの取り直しを許可します。
    if pending and not sys.stdin.isatty():
        _fail("不足またはDNS事業者が変わった秘密情報の入力には対話端末（interactive terminal）が必要です。既存ファイルは保持しています。端末からazd upを実行してください。秘密値を引数やパイプへ渡さないでください。")
    if pending:
        _confirm_ready()
    for specification in pending:
        if len(pending) > 1:
            print("入力: " + _purpose_label(specification), flush=True)
        value = (_dns_value(specification["certificate"]["plugin"]) if specification["kind"] == "dns"
                 else _read_input("Entraアプリのシークレットの「値」（入力非表示）: ") + "\n")
        if "replacementSnapshot" in specification:
            _replace_dns(specification, value, root)
            result["replaced"].append(str(specification["path"]))
        else:
            _publish(specification, value, root)
            result["created"].append(str(specification["path"]))
    return result


class SafeArguments(argparse.ArgumentParser):
    """誤って引数へ貼った秘密を、argparseのエラーにも表示させない。"""
    def error(self, message):
        """入力値を含む標準エラーを固定文へ置き換える。"""
        self.exit(2, "Invalid arguments. Use --help; enter credentials only at the hidden interactive prompts.\n")


def main(argv: list[str] | None = None) -> int:
    """企業paramsを読んでローカル入力を用意する。AzureログインやSSH生成はしない。"""
    parser = SafeArguments(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Organization params JSON; credential values must never be passed as arguments.")
    arguments = parser.parse_args(argv)
    try:
        config = load_configuration(arguments.config)
        workspace = local_paths(config, ROOT)
        workspace.checked(arguments.config)
        workspace.check_selection(config)
        workspace.ensure()
        result = prepare_secrets(config, workspace.root)
        print(f"Local secret inputs ready: {len(result['created'])} created, {len(result['reused'])} reused, {len(result['replaced'])} replaced. Values were not displayed; no Azure operations were performed.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Secret preparation interrupted. Previously completed files were retained; rerun to reuse them.", file=sys.stderr)
        return 1
    except (SecretPreparationError, ConfigurationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Cannot prepare local protected files. Check paths and private permissions; input values were suppressed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
