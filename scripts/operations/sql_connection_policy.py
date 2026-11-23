#!/usr/bin/env python3
"""共有SQLのconnection policy子だけを計画・照合・限定変更する。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.helpers.sql_connection_policy import (
    SqlConnectionPolicyError, change_sql_connection_policy, constructed_sql_server_id,
)
from deployment.helpers.private_permissions import check_private_permissions
from operations.common import OperationContext, OperationError


class _Transport:
    def __init__(self, context):
        self.context = context

    def request(self, method, path, body=None, headers=None):
        identity, separator, api = path.partition('?api-version=')
        expected = constructed_sql_server_id(self.context.config, self.context.state) + '/connectionPolicies/default'
        if not separator or method not in {'GET', 'PUT'} or method == 'PUT' and identity.casefold() != expected.casefold():
            raise OperationError('Select only the constructed SQL connection-policy child mutation')
        if method == 'PUT' and headers not in (None, {}):
            raise OperationError('SQL policy conditional headers are not supported by the documented API')
        options = {'body': body}
        if headers is not None:
            options['headers'] = headers
        return self.context.request(method, identity, api, **options)


def run(context: OperationContext, *, target_mode: str, expected_current: str,
        execute: bool = False) -> dict:
    """読取が既定。未実装のPortal検証を公開引数で迂回させない。"""
    if target_mode not in {'Proxy', 'Redirect'} or expected_current not in {'Default', 'Proxy', 'Redirect'}:
        raise OperationError('Select an explicit SQL policy target and expected current mode')
    try:
        server_id = constructed_sql_server_id(context.config, context.state)
        journal = context.output_path(context.workspace.state / 'sql-connection-policy'
                                      / (expected_current.lower() + '-to-' + target_mode.lower() + '.json'))
        previous = None
        if journal.exists():
            check_private_permissions(journal.parent, directory=True)
            check_private_permissions(journal)
            previous = json.loads(journal.read_text(encoding='utf-8'))

        def record(event, value):
            # A crash after the save and before PUT is deliberately unresolved:
            # retries inspect the real child and never repeat the mutation.
            if event != 'planned':
                context.save_private(journal, value, overwrite=journal.exists())
            context.record('sql-policy-' + event, value)

        return change_sql_connection_policy(
            context.config, _Transport(context), server_id=server_id,
            target_mode=target_mode, expected_current=expected_current,
            execute=execute, record=record, prior_receipt=previous,
            readiness_verifier=None)
    except (SqlConnectionPolicyError, OSError, ValueError, TypeError):
        raise OperationError('SQL policy operation stopped; inspect protected records and the current child') from None


def main(argv=None) -> int:
    """Proxyへの変更もpolicyだけ。物理接続drainとNSG復旧は別工程。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--runtime-root', type=Path,
                        help='Explicit checkout containing the already selected protected input and Core state')
    parser.add_argument('--target-mode', required=True, choices=('Proxy', 'Redirect'))
    parser.add_argument('--expected-current', required=True, choices=('Default', 'Proxy', 'Redirect'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config, runtime_root=args.runtime_root)
        if args.execute:
            with context.locked():
                result = run(context, target_mode=args.target_mode,
                             expected_current=args.expected_current, execute=True)
        else:
            result = run(context, target_mode=args.target_mode,
                         expected_current=args.expected_current)
        print(json.dumps(result))
        return 0
    except (OperationError, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'failed', 'message':
                          'SQL policy operation stopped; inspect protected records'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
