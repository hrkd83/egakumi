#!/usr/bin/env python3
"""公開情報ガードのGitフックを、このクローンだけへ設定する。

既存のフック設定や独自フックは上書きしない。フックからの内部呼出しでは、
引数・Gitの標準入力・検査出力をファイルやログへ転載しない。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
from pathlib import Path
import stat
import subprocess


HOOK_NAMES = ("pre-commit", "commit-msg", "pre-push")
HOOKS_PATH = ".githooks"
MAX_PUSH_INPUT_BYTES = 4 * 1024 * 1024
PROCESS_TIMEOUT_SECONDS = 300


class HookError(Exception):
    """実値を含まないフックの起動・設定エラーを表す。"""


class SafeArgumentParser(argparse.ArgumentParser):
    """不正な引数をエラー本文へ再掲しない。"""

    def error(self, message: str) -> None:
        """解析失敗を、入力値を含まない固定メッセージへ変換する。"""
        raise HookError("Public-data guard: invalid hook invocation.")


def git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Gitを実行し、値を含み得る出力を呼出し側のメモリーへ閉じ込める。"""
    try:
        return subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True,
            timeout=PROCESS_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError("Public-data guard: Git is unavailable.") from error


def repository_root() -> Path:
    """このインストーラーを所有するGitルートだけを使用する。"""
    root = Path(__file__).resolve().parents[1]
    result = git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise HookError("Public-data guard: repository validation failed.")
    try:
        actual = Path(os.fsdecode(result.stdout).strip()).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise HookError(
            "Public-data guard: repository validation failed."
        ) from error
    if actual != root:
        raise HookError("Public-data guard: repository validation failed.")
    return root


def readable_file(path: Path) -> bool:
    """外部へのシンボリックリンクを受け入れず、通常ファイルを確認する。"""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            stream.read(1)
    except OSError:
        return False
    return True


def require_guard(root: Path) -> Path:
    """検査プログラムとポリシーが読める場合だけ起動を認める。"""
    scanner = root / "scripts/check_public_data.py"
    policy = root / "scripts/public-data-policy.json"
    if (not readable_file(scanner) or not readable_file(policy)
            or scanner.resolve() != scanner or policy.resolve() != policy):
        raise HookError(
            "Public-data guard: required guard files are unavailable."
        )
    return scanner


def run_check(
    root: Path, scanner: Path, arguments: list[str], kind: str,
    input_bytes: bytes | None = None,
) -> int:
    """検査の成否だけを公開し、元データや検査出力をログへ流さない。"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(scanner), *arguments], cwd=root,
            input=input_bytes, capture_output=True, env=environment,
            timeout=PROCESS_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HookError(
            "Public-data guard: scanner startup failed."
        ) from error
    if result.returncode != 0:
        print(
            f"Public-data guard: {kind} check did not pass.", file=sys.stderr,
        )
        return 1
    return 0


def run_hook(root: Path, hook: str, arguments: list[str]) -> int:
    """Gitが渡す入力を検査し、未検査・読取失敗の場合は処理を止める。"""
    scanner = require_guard(root)
    if hook == "pre-commit":
        if arguments:
            raise HookError("Public-data guard: invalid hook invocation.")
        return run_check(
            root, scanner, ["--staged", "--worktree"], "commit content",
        )
    if hook == "commit-msg":
        if len(arguments) != 1 or not readable_file(Path(arguments[0])):
            raise HookError(
                "Public-data guard: commit message is unavailable."
            )
        return run_check(
            root, scanner, ["--commit-message", arguments[0]],
            "commit metadata",
        )
    if hook == "pre-push":
        if len(arguments) != 2:
            raise HookError("Public-data guard: invalid hook invocation.")
        try:
            updates = sys.stdin.buffer.read(MAX_PUSH_INPUT_BYTES + 1)
        except OSError as error:
            raise HookError(
                "Public-data guard: push input is unavailable."
            ) from error
        if len(updates) > MAX_PUSH_INPUT_BYTES:
            raise HookError("Public-data guard: push input exceeds the limit.")
        # 削除前のコミットも検査する。明示OIDのpushは標準入力の検査で補う。
        if run_check(root, scanner, ["--all-history"], "repository history"):
            return 1
        return run_check(
            root, scanner, ["--pre-push-refs"], "push references", updates,
        )
    raise HookError("Public-data guard: invalid hook invocation.")


def configured_hooks_path(root: Path) -> str | None:
    """継承された設定も含めて、有効なhooksPathを確認する。"""
    result = git(root, "config", "--get", "core.hooksPath")
    if result.returncode == 1 and not result.stdout:
        return None
    if result.returncode != 0:
        raise HookError("Public-data guard: hook configuration is unreadable.")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeError as error:
        raise HookError(
            "Public-data guard: hook configuration is unreadable."
        ) from error


def install_hooks(root: Path) -> int:
    """既存フックを保ち、相対hooksPathをローカル設定へ保存する。"""
    require_guard(root)
    for name in HOOK_NAMES:
        hook = root / HOOKS_PATH / name
        if not readable_file(hook) or hook.resolve() != hook:
            raise HookError(
                "Public-data guard: required hook files are unavailable."
            )
        if os.name != "nt" and not hook.stat().st_mode & stat.S_IXUSR:
            raise HookError(
                "Public-data guard: hook files must be executable."
            )

    configured = configured_hooks_path(root)
    if configured not in (None, HOOKS_PATH):
        raise HookError(
            "Public-data guard: existing hooks require manual review."
        )
    if configured is None:
        result = git(root, "rev-parse", "--git-path", "hooks")
        if result.returncode != 0:
            raise HookError(
                "Public-data guard: existing hooks are unreadable."
            )
        directory = Path(os.fsdecode(result.stdout).strip())
        if not directory.is_absolute():
            directory = root / directory
        try:
            if directory.is_symlink() or (
                directory.exists() and not directory.is_dir()
            ):
                raise HookError(
                    "Public-data guard: existing hooks require manual review."
                )
            if directory.exists() and any(
                not entry.name.endswith(".sample")
                for entry in directory.iterdir()
            ):
                raise HookError(
                    "Public-data guard: existing hooks require manual review."
                )
        except OSError as error:
            raise HookError(
                "Public-data guard: existing hooks are unreadable."
            ) from error

    result = git(root, "config", "--local", "core.hooksPath", HOOKS_PATH)
    if result.returncode != 0 or configured_hooks_path(root) != HOOKS_PATH:
        raise HookError("Public-data guard: hook installation failed.")
    print("Public-data guard: hooks installed for this clone.")
    return 0


def main(arguments: list[str] | None = None) -> int:
    """設定またはフックを実行し、失敗時には実値を含まない通知だけを返す。"""
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-hook", choices=HOOK_NAMES, help=argparse.SUPPRESS,
    )
    parser.add_argument("hook_arguments", nargs="*", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(arguments)
        if sys.version_info < (3, 11):
            raise HookError(
                "Public-data guard: Python 3.11 or later is required."
            )
        root = repository_root()
        if args.run_hook:
            return run_hook(root, args.run_hook, args.hook_arguments)
        if args.hook_arguments:
            raise HookError("Public-data guard: invalid hook invocation.")
        return install_hooks(root)
    except HookError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Public-data guard: operation interrupted.", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError):
        print("Public-data guard: operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
