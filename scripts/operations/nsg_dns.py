#!/usr/bin/env python3
"""不要な DNS 用 NSG 子規則 6 件だけを計画し、指定時に回収する。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.helpers.dns_nsg import DnsNsgError, retire_dns_rules
from operations.common import OperationContext, OperationError


class _Transport:
    def __init__(self, context):
        self.context = context

    def request(self, method, path, headers=None):
        resource_id, separator, version = path.partition("?api-version=")
        if not separator or method not in {"GET", "DELETE"}:
            raise OperationError("Select an exact DNS child operation")
        return self.context.request(method, resource_id, version, headers=headers)


def run(context: OperationContext, *, execute: bool = False) -> dict:
    """所有・DNS・全規則を照合し、計画と各削除の状態を保護保存する。"""
    try:
        return retire_dns_rules(
            context.config, _Transport(context), execute=execute,
            record=lambda event, value: context.record("nsg-dns-" + event, value))
    except DnsNsgError:
        context.record("nsg-dns-failed", {"status": "stopped",
                                         "responseMayBeUnknown": execute})
        raise OperationError("DNS NSG retirement stopped; inspect the protected record and current state") from None


def main(argv=None) -> int:
    """既定は Azure 読取だけ、反映は環境別ロックの内側で実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config)
        if args.execute:
            with context.locked():
                result = run(context, execute=True)
        else:
            result = run(context)
        print(json.dumps(result))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "message":
                          "DNS NSG retirement stopped; inspect protected records"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
