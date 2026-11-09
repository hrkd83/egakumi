#!/usr/bin/env python3
"""既存のGateway／Portal個別配備を使う。既定は計画、--executeで反映する。"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import re

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.common import OperationContext, OperationError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--component", choices=("portal", "gateway", "all"), required=True)
    parser.add_argument("--image-digest", help="単体の既存イメージをsha256形式で指定する")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.image_digest is not None and (
        args.component == "all" or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_digest)
    ):
        parser.error("--image-digest requires one component and an immutable sha256 digest")

    context = None
    completed = []
    current = None
    try:
        context = OperationContext.load(args.config)
        names = ("gateway", "portal") if args.component == "all" else (args.component,)
        with context.locked() if args.execute else nullcontext():
            for current in names:
                command = [sys.executable, "-B", str(context.workspace.repository / "scripts" / f"deploy_{current}.py"),
                           "--config", str(context.config_path), "--state-file", str(context.state_path)]
                if args.image_digest:
                    command.extend(("--image-digest", args.image_digest))
                if args.execute:
                    command.append("--execute")
                context.record("release", {"status": "started", "component": current,
                                          "execute": args.execute, "completedComponents": completed.copy()})
                print(f"{'Applying' if args.execute else 'Planning'} {current} release...",
                      file=sys.stderr, flush=True)
                # 子CLI自身が保存する配備結果を正本とする。秘密を含み得るstdoutは表示しない。
                context.run_cli(command)
                completed.append(current)
            context.record("release", {"status": "completed" if args.execute else "planned",
                                      "component": args.component, "execute": args.execute,
                                      "completedComponents": completed})
        print(json.dumps({"status": "completed" if args.execute else "planned", "component": args.component}))
        return 0
    except (OperationError, OSError, ValueError, TypeError, KeyError) as error:
        code = getattr(error, "exit_code", 1)
        code = code if type(code) is int and 1 <= code <= 125 else 1
        if context is not None:
            try:
                context.record("release", {"status": "failed", "component": current,
                                          "execute": args.execute, "completedComponents": completed,
                                          "exitCode": code})
            except (OperationError, OSError, ValueError, TypeError):
                pass
        print("Application release stopped; inspect the protected operation and deployment records.", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
