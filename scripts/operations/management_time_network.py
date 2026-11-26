#!/usr/bin/env python3
"""Retire only management NTP networking after verified PHC preparation."""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.helpers.management_time_network import (
    COMPUTE_API, GROUP_API, NETWORK_API, ManagementTimeNetworkError,
    operation_ids, retire_management_time_network,
)
from deployment.lifecycle import ArmTransport
from operations.common import OperationContext, OperationError


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, '{"status":"stopped","invalidArguments":true}\n')


class _Transport:
    """Restrict writes to the two reviewed network targets and queries to known GETs."""

    def __init__(self, context):
        self.context = context
        self.ids = operation_ids(context.config)

    def request(self, method, path, body=None, headers=None):
        parsed = urlsplit(path)
        try:
            entries = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise OperationError("Select a fixed management time network query") from None
        if parsed.scheme or parsed.netloc or parsed.fragment or len(entries) != len(dict(entries)):
            raise OperationError("Select an owned management time network path")
        query = dict(entries)
        target = parsed.path
        if target not in self.ids.values():
            raise OperationError("The management time operation targeted an unrelated resource")
        self.context._resource_path(target)
        api = GROUP_API if target in (self.ids["nwGroup"], self.ids["maintGroup"]) else COMPUTE_API if target in (
            self.ids["vm"], self.ids["command"], self.ids["hostTimeCommand"]) else NETWORK_API
        if query.get("api-version") != api:
            raise OperationError("Select the fixed management time API version")
        if "$expand" in query:
            if (method != "GET" or target not in (self.ids["command"], self.ids["hostTimeCommand"])
                    or query != {"api-version": COMPUTE_API, "$expand": "instanceView"}
                    or body is not None or headers is not None):
                raise OperationError("Only the fixed managed command instance view may be expanded")
            os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"
            if self.context.transport is None:
                self.context.transport = ArmTransport(self.context.config)
            return self.context.transport.request("GET", target + "?api-version=" + COMPUTE_API + "&$expand=instanceView")
        if query != {"api-version": api}:
            raise OperationError("The management time query has unexpected parameters")
        if (method not in {"GET", "DELETE", "PUT"}
                or method == "DELETE" and target not in (self.ids["allow-time-sync-egress"], self.ids["allow-time-sync"])
                or method == "PUT" and target != self.ids["rcg"]
                or method == "GET" and (body is not None or headers is not None)
                or method == "DELETE" and body is not None):
            raise OperationError("Only the exact management NTP child and rule collection can be changed")
        return self.context.request(method, target, api, body=body, headers=headers)


def run(context, *, execute=False):
    """Keep the selected Core state untouched and save private per-attempt evidence."""
    def record(event, payload):
        current = json.loads(context.state_path.read_text(encoding="utf-8"))
        if current != context.state:
            raise OperationError("The selected Core state changed; inspect before continuing")
        return context.record("management-time-network-" + event, payload)

    try:
        return retire_management_time_network(context.config, _Transport(context), execute=execute, record=record)
    except ManagementTimeNetworkError:
        context.record("management-time-network-stopped", {"status": "stopped", "responseMayBeUnknown": execute})
        raise OperationError("Management time networking stopped; inspect protected records and current resources") from None


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config, runtime_root=args.runtime_root)
        with context.locked():
            result = run(context, execute=args.execute)
        print(json.dumps(result))
        return 0
    except (OperationError, ManagementTimeNetworkError, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "stopped", "message": "Inspect protected management time network records"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
