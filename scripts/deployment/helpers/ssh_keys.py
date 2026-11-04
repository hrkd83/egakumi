"""構築hookだけで管理VM用SSH鍵を用意する。Azureや秘密入力の変換は行わない。

既存の公開鍵は検証して再利用する。公開鍵がない場合だけ、Git外のsecretsへ
RSA 4096bitの鍵を生成する。秘密鍵は保護ファイルに留め、返り値やログへ出さない。
設定のvalidate/render/load処理からは呼ばず、構築直前の副作用として分離する。
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from .configuration import load_ssh_public_key


class SshKeyError(ValueError):
    """鍵の内容や外部コマンドの生出力を含めない準備エラー。"""


def _fail(message: str) -> None:
    """公開設定の項目名と、安全な対処理由だけを利用者へ返す。"""
    raise SshKeyError("management.sshPublicKeyFile: " + message) from None


def _managed_target(public: Path, root: Path) -> Path:
    """自動生成先をsecrets配下へ限定し、信頼するrootより下のリンクを拒否する。"""
    canonical_root = root.resolve()
    # /tmp -> /private/tmp等、呼出元が指定したroot自体のOSリンクだけを許す。
    for base in (canonical_root, root.absolute()):
        try:
            relative = public.relative_to(base)
            break
        except ValueError:
            continue
    else:
        _fail("automatic generation requires a .pub file under the selected environment's secrets directory")
    if (not relative.parts or relative.parts[0] != "secrets" or ".." in relative.parts
            or public.suffix != ".pub" or len(relative.parts) < 2):
        _fail("automatic generation requires a .pub file under the selected environment's secrets directory")
    cursor = public
    while cursor != base:
        if cursor.is_symlink():
            _fail("automatic generation must not use symbolic links; existing files were preserved")
        cursor = cursor.parent
    return canonical_root / relative


def _private_directory(path: Path, root: Path) -> None:
    """生成先の各ディレクトリーを700で用意し、既存の共有権限を勝手に変更しない。"""
    chain = [path, *path.parents]
    for directory in reversed(chain[:chain.index(root)]):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700)):
            _fail("generation directories must be private regular directories (use chmod 700)")


def _remove_owned(path: Path, identity: tuple[int, int]) -> None:
    """途中失敗では今回作ったinodeだけを消し、競合して置かれた別ファイルを保持する。"""
    try:
        info = path.lstat()
        if (info.st_dev, info.st_ino) == identity:
            path.unlink()
    except FileNotFoundError:
        pass


def ensure_management_ssh_key(config: dict, root: Path) -> dict:
    """有効な既存公開鍵を再利用し、欠損時だけ安全な保存先へ鍵pairを生成する。

    対応秘密鍵の有無は既存公開鍵の利用条件にしない。新規生成は無人実行のため
    パスフレーズなし。返り値には状態とパスだけを入れ、鍵の値を含めない。
    """
    raw = config.get("management", {}).get("sshPublicKeyFile")
    if not isinstance(raw, str) or not raw.strip():
        _fail("must specify a normalized public-key file path")
    public = Path(raw).expanduser()
    if not public.is_absolute():
        _fail("must specify a normalized absolute public-key file path")
    try:
        if public.exists():
            load_ssh_public_key(str(public))
            return {"created": False, "publicKeyFile": str(public)}
        public = _managed_target(public, Path(root))
        private = public.with_suffix("")
        if os.path.lexists(private):
            _fail("the private companion already exists; restore or select its public key without overwriting it")
        executable = shutil.which("ssh-keygen")
        if executable is None:
            _fail("install ssh-keygen before automatic generation, or supply an existing public key")
        _private_directory(public.parent, Path(root).resolve())
        with tempfile.TemporaryDirectory(prefix=".management-ssh-", dir=public.parent) as staging:
            staged_private = Path(staging) / "key"
            staged_public = staged_private.with_suffix(".pub")
            try:
                result = subprocess.run(
                    [executable, "-q", "-t", "rsa", "-b", "4096", "-N", "", "-C", "management-access", "-f", str(staged_private)],
                    shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                _fail("ssh-keygen failed or timed out; private output was suppressed")
            if result.returncode != 0:
                _fail("ssh-keygen failed; private output was suppressed")
            for generated in (staged_private, staged_public):
                info = generated.lstat()
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    _fail("ssh-keygen did not produce a regular key pair")
                generated.chmod(0o600)
            load_ssh_public_key(str(staged_public))
            # 生成中に親や対象が差し替わっていれば、公開前にも停止する。
            _managed_target(public, Path(root))
            _private_directory(public.parent, Path(root).resolve())
            published = []
            try:
                # linkは既存ファイルを置換しない。rename/replaceで上書きしない。
                for source, destination in ((staged_private, private), (staged_public, public)):
                    identity = source.stat()
                    os.link(source, destination, follow_symlinks=False)
                    published.append((destination, (identity.st_dev, identity.st_ino)))
            except OSError:
                for destination, identity in reversed(published):
                    _remove_owned(destination, identity)
                _fail("key publication failed or another process created the target; existing files were preserved")
        return {"created": True, "publicKeyFile": str(public), "privateKeyFile": str(private)}
    except OSError:
        _fail("cannot prepare the private key files; check paths and permissions (private output suppressed)")
