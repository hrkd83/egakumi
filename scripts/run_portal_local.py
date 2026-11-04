#!/usr/bin/env python3
"""環境別の保護設定を子プロセスだけへ渡してローカルPortal操作を実行する。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import REPOSITORY, private_parent
from prepare_portal_local_auth import (
    PreparationArgumentParser, PreparationError, environment_paths, read_environment,
    reject_legacy_settings,
)


def secret_values(values: dict[str, str]) -> tuple[str, ...]:
    """子へ注入する既知の秘密値だけを、長い値からログの置換対象にする。"""
    return tuple(sorted({value for key, value in values.items()
                         if value and re.search(r"SECRET|PASSWORD|TOKEN|(?:^|_)KEY(?:_|$)", key)},
                        key=len, reverse=True))


def run(configuration: dict, root: Path, command: list[str], *, admin: bool = False) -> int:
    """シェルを使わず実行し、既知の秘密をマスクした行だけを中継する。"""
    if not command or any(not isinstance(value, str) or "\0" in value for value in command):
        raise PreparationError("Pass a command after --; no command was started")
    reject_legacy_settings(root)
    runtime_path, admin_path = environment_paths(configuration, root)
    selected = admin_path if admin else runtime_path
    if selected.exists():
        private_parent(selected, root)
    values = read_environment(selected)
    environment = {**os.environ, **values}
    if admin:
        environment["PORTAL_LOCAL_SQL_ENV_FILE"] = str(admin_path)
    hidden = secret_values(values)
    with subprocess.Popen(command, cwd=root / "apps/portal", env=environment,
                          shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as child:
        assert child.stdout is not None
        for line in child.stdout:
            for value in hidden:
                line = line.replace(value, "[REDACTED]")
            sys.stdout.write(line)
            sys.stdout.flush()
        return child.wait()


def main(argv: list[str] | None = None) -> int:
    """--configで保存済み入力を選び、--adminでSQL管理用設定を選ぶ。"""
    parser = PreparationArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--admin", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return run(load_configuration(args.config), REPOSITORY, command, admin=args.admin)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreparationError as error:
        raise SystemExit(f"Local Portal command stopped: {error}") from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise SystemExit("Local Portal command stopped: protected settings or command startup failed. Secret values were not displayed.") from None
