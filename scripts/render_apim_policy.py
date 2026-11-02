#!/usr/bin/env python3
"""現在の APIM 全体ポリシーを、確認・手動統合用 XML へオフライン出力する。

Azure へ接続・更新しない。既存の全体ポリシーへ手動統合する場合は、独自の
設定を保持して core-common-authentication ブロックと関連処理を反映する。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from deployment.helpers.apim_authentication import render_service_policy
from deployment.helpers.configuration import load_configuration
from deployment.helpers.credential_login import settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Reviewable global policy XML; existing files are not overwritten")
    parser.add_argument("--config", type=Path, help="Include the configured API Credential Manager connection login")
    args = parser.parse_args(argv)
    with args.output.open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(render_service_policy(settings(load_configuration(args.config)) if args.config else None))
    print("Rendered APIM policy XML for review. No Azure settings were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
