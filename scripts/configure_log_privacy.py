#!/usr/bin/env python3
"""既存Coreのアクセス監査をquery非保持へ移行する。既定はGETだけの計画。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path
from deployment.helpers.configuration import load_configuration
from deployment.helpers.log_privacy import LogPrivacyError, plan, apply_steps
from deployment.lifecycle import ArmTransport, protected_json


def main() -> int:
    """prepare→実変換確認→export→Blob確認→routeの段階を明示して更新する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stage", choices=("prepare", "export", "route"), help="実行時に更新する段階。未指定は計画だけを表示")
    parser.add_argument("--plan-file", type=Path, help="詳細な変更前後をGit管理外の保護ファイルへ記録")
    args = parser.parse_args()
    if args.execute and not args.stage:
        parser.error("--execute requires --stage; verify fresh transformed data and export arrival before the route stage")
    config = load_configuration(args.config)
    transport = ArmTransport(config)
    proposal = plan(config, transport)
    if args.plan_file:
        protected_json(args.plan_file, proposal)
    result = apply_steps(proposal, transport, stages=(args.stage,)) if args.execute else {"azureWrites": 0}
    print(json.dumps({"mode": "execute" if args.execute else "plan", "stage": args.stage, "steps": [
        {key: value[key] for key in ("label", "stage", "method", "id", "required")} for value in proposal["steps"]
    ], "activationVerificationRequired": proposal["activationVerificationRequired"],
    "externalBoundary": proposal["externalBoundary"], **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, LogPrivacyError) as error:
        raise SystemExit(f"Log privacy configuration stopped: {error}") from None
