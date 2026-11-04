"""本人接続のクライアント状態を、利用者単位の排他・比較更新で保存する。

CLI用ファイルは単一ホストの検証用である。複数インスタンスのアプリでは、同じ
インターフェースを共有DBのトランザクション・暗号化保存で実装する。
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from deployment.helpers.paths import private_parent


class SessionStateConflict(RuntimeError):
    """読み取り後に別処理が保存したため、古い状態を上書きできない。"""


class MemorySessionStore:
    """テスト・単一プロセスの例。再起動に耐える保存先としては使用しない。"""

    def __init__(self, initial=None):
        self._state = copy.deepcopy(initial or {"revision": 0})
        self._lock = threading.RLock()

    @contextmanager
    def locked(self):
        """同じアプリ利用者への要求・更新を直列化する。"""
        with self._lock:
            yield self

    def read(self):
        """呼出側の変更が保存先へ漏れないように状態のコピーを返す。"""
        with self._lock:
            return copy.deepcopy(self._state)

    def write(self, state, *, expected_revision):
        """世代が一致したときだけ更新し、単調増加する保存版数を返す。"""
        with self._lock:
            if self._state.get("revision", 0) != expected_revision:
                raise SessionStateConflict("Private session state changed")
            self._state = copy.deepcopy({**state, "revision": expected_revision + 1})
            return self.read()


class FileSessionStore:
    """CLI専用の所有者限定ファイル。ロック・原子的置換・fsyncで途中保存を防ぐ。"""

    def __init__(self, path):
        if os.name == "nt":
            raise ValueError("Use the owner-only diagnostic file store in WSL or another POSIX environment")
        self.path = Path(path)
        self._thread_lock = threading.RLock()
        self._lock_depth = 0

    @contextmanager
    def locked(self):
        """OSロックはプロセス終了時に解放され、古いロックファイルでも停止しない。"""
        with self._thread_lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield self
                finally:
                    self._lock_depth -= 1
                return
            self.path = private_parent(self.path)
            lock_path = self.path.with_name(self.path.name + ".lock")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            try:
                self._check_regular(os.fstat(descriptor))
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield self
                finally:
                    self._lock_depth = 0
            finally:
                os.close(descriptor)

    @staticmethod
    def _check_regular(info):
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError("Invalid protected session file")
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("Protected session file must be owner-only")

    def read(self):
        """リンクをたどらず、小さな所有者限定JSONだけを読み取る。"""
        if not self._lock_depth:
            with self.locked():
                return self.read()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {"revision": 0}
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            self._check_regular(os.fstat(stream.fileno()))
            value = json.load(stream)
        if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 0:
            raise ValueError("Invalid protected session state")
        return value

    def write(self, state, *, expected_revision):
        """旧版による上書きを拒否し、同一ディレクトリ内の一時ファイルを置換する。"""
        if not self._lock_depth:
            with self.locked():
                return self.write(state, expected_revision=expected_revision)
        if self.read().get("revision", 0) != expected_revision:
            raise SessionStateConflict("Private session state changed")
        # 既存のリンクを置換して想定外のファイルを変更することも拒否する。
        if self.path.is_symlink():
            raise ValueError("Protected session file cannot be a symbolic link")
        saved = {**state, "revision": expected_revision + 1}
        descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name + ".", dir=self.path.parent)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(saved, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            # renameの永続化も完了してから返す。確定要求後の電源断で候補だけ消失させない。
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return copy.deepcopy(saved)
