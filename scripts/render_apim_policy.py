#!/usr/bin/env python3
"""現在の APIM 全体ポリシーを、確認・手動統合用 XML へオフライン出力する。

Azure へ接続・更新しない。既存の全体ポリシーへ手動統合する場合は、独自の
設定を保持して core-common-authentication ブロックと関連処理を反映する。
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

from deployment.helpers.apim_authentication import render_service_policy
from deployment.helpers.configuration import load_configuration
from deployment.helpers.credential_login import settings
from deployment.helpers.paths import private_parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Reviewable global policy XML; existing files are not overwritten")
    parser.add_argument("--config", type=Path, help="Include the configured API Credential Manager connection login")
    args = parser.parse_args(argv)
    output = private_parent(args.output)
    policy = render_service_policy(settings(load_configuration(args.config)) if args.config else None)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
        os.fchmod(destination.fileno(), 0o600)
        destination.write(policy)
    print("Rendered APIM policy XML for review. No Azure settings were changed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        raise SystemExit("APIM policy rendering stopped: protected output or configuration validation failed.") from None
