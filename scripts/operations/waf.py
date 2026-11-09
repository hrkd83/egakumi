#!/usr/bin/env python3
"""データ項目WAFの保守を計画し、明示指定時だけ既存reconcilerで反映する。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import waf_policy
from operations.common import OperationContext, OperationError


def run(context: OperationContext, *, scope: str = "all",
        execute: bool = False, manifest: Path | None = None) -> dict:
    """所有する経路を照合し、既存の限定除外・ETag保護をそのまま利用する。"""
    scopes = ("gateway", "portal") if scope == "all" else (scope,)
    if scope not in {"all", "gateway", "portal"}:
        raise OperationError("Select a Gateway or Portal WAF scope")
    data = waf_policy.load_manifest(manifest or waf_policy.MANIFEST)
    if execute:
        resource_id = waf_policy.gateway_id(context.config)
        context.verify_resource(resource_id, "2024-05-01", "Microsoft.Network/applicationGateways")
    result = waf_policy.reconcile(context.config, context.transport if execute else None,
                                  scopes=scopes, manifest=data, execute=execute,
                                  progress=lambda *args, **kwargs: None)
    context.record("waf", {"execute": execute, "result": result})
    return result


def main(argv=None) -> int:
    """通常の計画と、ローカル排他を伴う反映を区別する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scope", choices=("gateway", "portal", "all"), default="all")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config)
        if args.execute:
            with context.locked():
                result = run(context, scope=args.scope, execute=True, manifest=args.manifest)
        else:
            result = run(context, scope=args.scope, manifest=args.manifest)
        print(json.dumps({"status": result["status"], "azureWrites": result.get("azureWrites", 0)}))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "message": "WAF operation stopped; inspect the protected record and current state"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
