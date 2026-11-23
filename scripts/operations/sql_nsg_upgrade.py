#!/usr/bin/env python3
"""既存 SQL NSG 4 子だけを journal 付きで計画・段階移行する。"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.helpers import fixed_nsg as nsg
from deployment.helpers.private_permissions import check_private_permissions
from deployment.helpers.sql_connection_policy import constructed_sql_server_id, SqlConnectionPolicyError
from operations.common import OperationContext, OperationError, poll_delay


REPOSITORY = Path(__file__).resolve().parents[2]
MODULE = REPOSITORY / 'infra/modules/sql-redirect-nsg-upgrade.bicep'
DEPLOYMENT_API = '2025-04-01'
DRAIN_CHECKS = frozenset({'portalPoolDrained', 'brokerPoolDrained', 'jobsDrained',
                          'newProxyConnectionsVerified'})


@dataclass(frozen=True)
class VerifiedProxyPhase:
    """内部 runtime verifier の結果。CLI/JSON の確認フラグでは生成しない。

    実際に pool を閉じ Job を drain した内部呼出元だけが作成する。
    公開 CLI にはこの verifier を実装していないので Proxy execute は拒否する。
    """
    sql_server_id: str
    core_state: dict
    bindings: dict
    verified_at: datetime
    checks: dict


def checked_template(value):
    """親・nested deployment・条件付き資源を実行前に拒否する。

    実展開の 4 tuple は、stage 後の FullResourcePayloads what-if が検証する。
    そこで確認した同じ template/parameters を journal に pin して PUT する。
    """
    if not isinstance(value, dict) or set(value.get('parameters', {})) != {'resourcePrefix', 'bindings', 'targetMode'}:
        raise OperationError('The SQL-only compiled template parameters differ')
    resources = value.get('resources')
    if isinstance(resources, dict): resources = list(resources.values())
    if not isinstance(resources, list) or len(resources) != 1:
        raise OperationError('The SQL-only template must contain one four-child resource loop')
    child = resources[0]
    if (not isinstance(child, dict) or child.get('type') != nsg.NSG_TYPE + '/securityRules'
            or child.get('apiVersion') != nsg.API or set(child) - {'type', 'apiVersion', 'copy', 'name', 'properties'}
            or child.get('copy') != {'name': 'sqlRules', 'count': '[length(range(0, 4))]',
                                      'mode': 'serial', 'batchSize': 1}
            or value.get('outputs') or value.get('extensions')):
        raise OperationError('The SQL-only template contains an unapproved resource or expansion')
    return copy.deepcopy(value)


class _Transport:
    """helper の直接変更を四つの source child と If-Match に限定する。"""
    def __init__(self, operation):
        self.operation = operation

    def request(self, method, path, body=None, headers=None):
        identity, separator, api = path.partition('?api-version=')
        if not separator or method not in {'GET', 'PUT', 'DELETE'}:
            raise OperationError('An explicit scoped SQL NSG request is required')
        if method != 'GET':
            self.operation.before_mutation()
            journal = self.operation.journal
            rows = nsg._sql_rows(journal['baseline']['bindings'], self.operation.target_mode)
            parents = journal['baseline']['parents']
            permitted = {parents[r['group']]['id'] + '/securityRules/' + r['name']: r for r in rows}
            row = permitted.get(identity)
            if (api != nsg.API or row is None or not isinstance(headers, dict)
                    or set(headers) != {'If-Match'} or not isinstance(headers['If-Match'], str)
                    or headers['If-Match'] == '*' or not headers['If-Match']):
                raise OperationError('Only an exact SQL source child conditional mutation is permitted')
            if journal.get('pendingChild') is not None:
                raise OperationError('The previous child request remains unresolved; no resend is allowed')
            before = nsg._read(self.operation.context.config, self, parents[row['group']]['id'], nsg.NSG_TYPE)
            children = nsg._inventory(before)
            child = children.get(row['name'])
            if child is None or nsg._etag(child) != headers['If-Match']:
                raise OperationError('The conditional SQL child changed before its journal checkpoint')
            target = {**row['source'], 'priority': row['temporaryPriority']}
            if (method == 'PUT' and (not isinstance(body, dict) or set(body) != {'properties'}
                    or nsg._properties(body) != target) or method == 'DELETE' and body is not None):
                raise OperationError('The SQL source child mutation differs from the approved phase')
            expected = copy.deepcopy(before)
            expected['properties']['securityRules'] = [r for r in expected['properties']['securityRules']
                                                       if r['name'] != row['name']]
            if method == 'PUT':
                updated = copy.deepcopy(child); updated['properties'] = {**body['properties'], 'provisioningState': 'Succeeded'}
                expected['properties']['securityRules'].append(updated)
            journal['pendingChild'] = {'method': method, 'identity': identity, 'group': row['group'],
                                       'before': before, 'expected': expected, 'target': target}
            self.operation.checkpoint('child-submitting')
        return self.operation.context.request(method, identity, api, body=body, headers=headers)


class SqlNsgUpgrade:
    """再開時も journal の不明要求を盲目的に再送しない限定移行。"""
    def __init__(self, context, *, target_mode='Redirect', rollback_phase=None,
                 compiler=None, what_if=None, sleep=time.sleep, monotonic=time.monotonic,
                 now=None, max_checks=30, wait_seconds=2):
        if target_mode not in {'Redirect', 'Proxy'}:
            raise OperationError('Select a SQL NSG migration mode')
        nsg._limits(max_checks, wait_seconds)
        self.context = context
        self.target_mode = target_mode
        self.rollback_phase = rollback_phase
        self.compiler = compiler or self.compile
        self.what_if = what_if or self.live_what_if
        self.sleep, self.monotonic = sleep, monotonic
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_checks, self.wait_seconds = max_checks, wait_seconds
        self.path = context.output_path(context.workspace.state / 'sql-nsg-upgrade' / (target_mode.lower() + '.json'))
        self.journal = None
        self.transport = _Transport(self)

    def state_current(self):
        path = self.context.output_path(self.context.state_path)
        check_private_permissions(path.parent, directory=True)
        check_private_permissions(path)
        if json.loads(path.read_text(encoding='utf-8')) != self.context.state:
            raise OperationError('The selected Core state changed; stop this SQL-only migration')

    def proxy_guard(self):
        phase = self.rollback_phase
        if not isinstance(phase, VerifiedProxyPhase):
            raise OperationError('Proxy NSG execution requires an internal verified physical-drain phase')
        identity = constructed_sql_server_id(self.context.config, self.context.state)
        if (phase.sql_server_id != identity or phase.core_state != self.context.state
                or phase.bindings != nsg._sql_binding_view(self.journal['baseline']['bindings'])
                or not isinstance(phase.checks, dict) or set(phase.checks) != DRAIN_CHECKS
                or any(v is not True for v in phase.checks.values())
                or not isinstance(phase.verified_at, datetime) or phase.verified_at.tzinfo is None):
            raise OperationError('The Proxy physical-drain phase differs from the selected construction')
        age = (self.now() - phase.verified_at).total_seconds()
        if not 0 <= age <= 300:
            raise OperationError('The Proxy physical-drain verification has expired')
        nsg._sql_proxy_guard(self.context.config, self.transport, True)

    def before_mutation(self):
        self.state_current()
        bindings, parents = nsg._sql_context(self.context.config, self.transport)
        nsg._sql_baseline(self.journal['baseline'], bindings, parents, self.target_mode)
        rows = nsg._sql_plan(parents, bindings, self.target_mode)
        if (self.journal['phase'] not in {'staging', 'completed'}
                and any(r['properties']['priority'] != r['temporaryPriority'] for r in rows)):
            raise OperationError('The SQL source priorities changed after staging')
        if self.target_mode == 'Proxy': self.proxy_guard()

    def checkpoint(self, event):
        self.context.save_private(self.path, self.journal, overwrite=self.path.exists())
        self.context.record('sql-nsg-' + event, {'operationId': self.journal['operationId'],
                            'phase': self.journal['phase'], 'pendingChild': self.journal.get('pendingChild')})

    def record_helper(self, event, value):
        if event in {'staged', 'removed'}:
            pending = self.journal.get('pendingChild')
            if pending is None or pending['identity'] != value.get('child'):
                raise OperationError('The SQL child confirmation differs from its saved request')
            self.journal['pendingChild'] = None
            self.journal['directWritesVerified'] += 1
            self.checkpoint('child-verified')
        self.context.record('sql-nsg-helper', {'event': event, 'details': value})

    def compile(self):
        if os.name != 'nt':
            raise OperationError('Use the Windows Azure CLI for this operation')
        return json.loads(self.context.run_cli(['az', 'bicep', 'build', '--file', str(MODULE), '--stdout']))

    def live_what_if(self, template_path, parameters_path):
        if os.name != 'nt':
            raise OperationError('Use the Windows Azure CLI for this operation')
        return json.loads(self.context.run_cli(['az', 'deployment', 'group', 'what-if',
            '--subscription', self.context.config['subscriptionId'],
            '--resource-group', self.context.config['resourceGroupNames']['nw'],
            '--name', self.journal['deploymentName'], '--mode', 'Incremental',
            '--template-file', str(template_path), '--parameters', '@' + str(parameters_path),
            '--result-format', 'FullResourcePayloads', '--no-pretty-print', '--output', 'json']))

    def load_journal(self):
        if not self.path.exists(): return
        check_private_permissions(self.path.parent, directory=True)
        check_private_permissions(self.path)
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                or value.get('targetMode') != self.target_mode or value.get('coreState') != self.context.state
                or not isinstance(value.get('operationId'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', value['operationId'])
                or value.get('deploymentName') != 'sql-nsg-' + value['operationId']
                or value.get('phase') not in {'staging', 'staged', 'what-if-verified', 'deployment-submitting',
                                            'deployment-verified', 'retiring', 'completed'}
                or type(value.get('directWritesVerified')) is not int
                or not 0 <= value['directWritesVerified'] <= 8):
            raise OperationError('The protected SQL NSG journal differs from this operation')
        checked_template(value.get('template'))
        self.journal = value

    def reconcile_child(self):
        pending = self.journal.get('pendingChild')
        if pending is None: return
        if not isinstance(pending, dict) or pending.get('method') not in {'PUT', 'DELETE'}:
            raise OperationError('The saved SQL child request is invalid')
        identity = pending['identity']
        rows = nsg._sql_rows(self.journal['baseline']['bindings'], self.target_mode)
        permitted = {self.journal['baseline']['parents'][r['group']]['id'] + '/securityRules/' + r['name'] for r in rows}
        if identity not in permitted:
            raise OperationError('The saved child request is outside the four SQL source children')
        child = nsg._request(self.transport, 'GET', identity)
        parent = nsg._read(self.context.config, self.transport, pending['before']['id'], nsg.NSG_TYPE, pending=True)
        matched = nsg._snapshot(parent) == nsg._snapshot(pending['expected']) and parent['properties']['provisioningState'] == 'Succeeded'
        if pending['method'] == 'PUT':
            body = child.get('body')
            matched = (matched and child['statusCode'] == 200 and isinstance(body, dict)
                       and nsg._ref(body) == identity.casefold() and nsg._properties(body) == pending['target']
                       and body['properties'].get('provisioningState') == 'Succeeded')
        else:
            matched = matched and child['statusCode'] == 404
        if not matched:
            raise OperationError('The previous SQL child request is unresolved; it will not be resent')
        self.journal['pendingChild'] = None
        self.journal['directWritesVerified'] += 1
        self.checkpoint('child-reconciled')

    def deployment_id(self):
        groups, _, _ = nsg._scope(self.context.config)
        return groups['nw'] + '/providers/Microsoft.Resources/deployments/' + self.journal['deploymentName']

    def check_deployment(self, body):
        identity = self.deployment_id()
        if (not isinstance(body, dict) or str(body.get('id', '')).casefold() != identity.casefold()
                or not isinstance(body.get('properties'), dict)):
            raise OperationError('The saved SQL deployment identity is unverified')
        properties = body['properties']
        parameters = properties.get('parameters')
        if (properties.get('mode') != 'Incremental' or not isinstance(parameters, dict)
                or set(parameters) != set(self.journal['parameters'])
                or any(not isinstance(v, dict) or set(v) - {'type', 'value'} or 'value' not in v
                       for v in parameters.values())
                or {k: v['value'] for k, v in parameters.items()} != self.journal['parameters']):
            raise OperationError('The saved SQL deployment parameters or mode differ')
        template = properties.get('template')
        if template is None:
            # exportTemplate is a read-only action on this saved deployment.
            response = self.context.request('POST', identity + '/exportTemplate', DEPLOYMENT_API)
            if response.get('statusCode') != 200 or not isinstance(response.get('body'), dict):
                raise OperationError('The saved SQL deployment template could not be inspected')
            template = response['body'].get('template')
        if template != self.journal['template']:
            raise OperationError('The saved SQL deployment template differs from its verified artifact')
        return properties.get('provisioningState')

    def wait_deployment(self):
        deadline = self.monotonic() + 1800
        while self.monotonic() < deadline:
            self.state_current()
            response = self.context.request('GET', self.deployment_id(), DEPLOYMENT_API)
            if response.get('statusCode') != 200:
                raise OperationError('The saved SQL deployment request is unresolved; it will not be resent')
            state = self.check_deployment(response.get('body'))
            if state == 'Succeeded':
                self.before_mutation()
                nsg.verify_sql_nsg_rules(self.context.config, self.transport,
                    target_mode=self.target_mode, baseline=self.journal['baseline'])
                self.journal['phase'] = 'deployment-verified'
                self.checkpoint('deployment-verified')
                return
            if state in {'Failed', 'Canceled', 'Deleted'}:
                raise OperationError('The saved SQL deployment failed; source children were retained')
            if state not in {'Accepted', 'Running', 'Creating', 'Updating'}:
                raise OperationError('The saved SQL deployment status is unknown; source children were retained')
            delay = poll_delay(response)
            if delay > 60 or self.monotonic() + delay >= deadline:
                raise OperationError('The SQL deployment remains pending; do not resubmit')
            self.sleep(delay)
        raise OperationError('The SQL deployment remains pending; do not resubmit')

    def artifacts(self):
        root = self.context.workspace.root / 'generated/sql-nsg-upgrade' / self.journal['operationId']
        contents = {'template.json': self.journal['template'], 'parameters.json': {
            '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
            'contentVersion': '1.0.0.0',
            'parameters': {k: {'value': v} for k, v in self.journal['parameters'].items()}}}
        paths = []
        for name, value in contents.items():
            path = self.context.output_path(root / name)
            if path.exists():
                check_private_permissions(path.parent, directory=True)
                check_private_permissions(path)
                if json.loads(path.read_text(encoding='utf-8')) != value:
                    raise OperationError('A pinned SQL deployment artifact changed')
            else: self.context.save_private(path, value)
            paths.append(path)
        return paths

    def apply(self):
        self.state_current()
        self.load_journal()
        bindings, parents = nsg._sql_context(self.context.config, self.transport)
        plan = nsg._sql_plan(parents, bindings, self.target_mode)
        if self.journal is None:
            template = checked_template(self.compiler())
            operation_id = uuid.uuid4().hex
            self.journal = {'schemaVersion': 1, 'operationId': operation_id, 'targetMode': self.target_mode,
                'coreState': copy.deepcopy(self.context.state), 'phase': 'staging',
                'baseline': {'targetMode': self.target_mode, 'bindings': bindings, 'parents': parents},
                'template': template, 'deploymentName': 'sql-nsg-' + operation_id,
                'pendingChild': None, 'directWritesVerified': 0}
            self.before_mutation()
            if not plan:
                nsg.verify_sql_nsg_rules(self.context.config, self.transport,
                    target_mode=self.target_mode, require_retired=True)
                return self.summary(azure_writes=0)
            self.checkpoint('initialized')
        else:
            self.before_mutation()
            self.reconcile_child()
        if self.journal['phase'] == 'completed':
            nsg.verify_sql_nsg_rules(self.context.config, self.transport, target_mode=self.target_mode,
                baseline=self.journal['baseline'], require_retired=True)
            return self.summary(azure_writes=0)
        writes_before = self.journal['directWritesVerified']
        deployed_now = False
        if self.journal['phase'] == 'staging':
            nsg.prepare_sql_nsg_rules(self.context.config, self.transport, target_mode=self.target_mode,
                execute=True, record=self.record_helper, proxy_connections_verified=self.target_mode == 'Proxy',
                max_checks=self.max_checks, wait_seconds=self.wait_seconds, sleep=self.sleep)
            self.before_mutation()
            self.journal['parameters'] = nsg.sql_nsg_upgrade_parameters(self.context.config, self.transport,
                baseline=self.journal['baseline'], target_mode=self.target_mode,
                proxy_connections_verified=self.target_mode == 'Proxy')
            self.journal['phase'] = 'staged'
            self.checkpoint('staged')
        if self.journal['phase'] in {'staged', 'what-if-verified'}:
            self.before_mutation()
            paths = self.artifacts()
            observation = self.what_if(*paths)
            self.context.save_private(self.context.workspace.root / 'evidence/sql-nsg-upgrade'
                                      / (self.journal['operationId'] + '-what-if.json'), observation,
                                      overwrite=True)
            nsg.verify_sql_nsg_upgrade_what_if(self.context.config, observation,
                self.journal['baseline']['bindings'], target_mode=self.target_mode)
            self.journal['phase'] = 'what-if-verified'
            self.checkpoint('what-if-verified')
            self.before_mutation()
            # Detect replacement of protected CLI files before the saved body is PUT.
            self.artifacts()
            response = self.context.request('GET', self.deployment_id(), DEPLOYMENT_API)
            if response.get('statusCode') != 404:
                raise OperationError('The new SQL deployment name is occupied; no PUT was sent')
            self.journal['phase'] = 'deployment-submitting'
            self.checkpoint('deployment-submitting')
            body = {'properties': {'mode': 'Incremental', 'template': self.journal['template'],
                    'parameters': {k: {'value': v} for k, v in self.journal['parameters'].items()}}}
            response = self.context.request('PUT', self.deployment_id(), DEPLOYMENT_API, body=body)
            if response.get('statusCode') not in {200, 201, 202}:
                raise OperationError('The SQL deployment response is unresolved; no resend is allowed')
            deployed_now = True
        if self.journal['phase'] == 'deployment-submitting': self.wait_deployment()
        if self.journal['phase'] in {'deployment-verified', 'retiring'}:
            self.before_mutation()
            nsg.verify_sql_nsg_rules(self.context.config, self.transport, target_mode=self.target_mode,
                                    baseline=self.journal['baseline'])
            self.journal['phase'] = 'retiring'
            self.checkpoint('retiring')
            nsg.complete_sql_nsg_rules(self.context.config, self.transport, baseline=self.journal['baseline'],
                target_mode=self.target_mode, execute=True, record=self.record_helper,
                proxy_connections_verified=self.target_mode == 'Proxy', max_checks=self.max_checks,
                wait_seconds=self.wait_seconds, sleep=self.sleep)
            self.before_mutation()
            nsg.verify_sql_nsg_rules(self.context.config, self.transport, target_mode=self.target_mode,
                                    baseline=self.journal['baseline'], require_retired=True)
            self.journal['phase'] = 'completed'
            self.checkpoint('completed')
        return self.summary(azure_writes=self.journal['directWritesVerified'] - writes_before + int(deployed_now))

    def summary(self, *, azure_writes):
        return {'status': 'completed', 'targetMode': self.target_mode, 'sqlRulesVerified': 4,
                'legacySqlRules': 0, 'azureWrites': azure_writes, 'runtimeVerified': False}

    def plan(self):
        self.state_current()
        checked_template(self.compiler())
        bindings, parents = nsg._sql_context(self.context.config, self.transport)
        rows = nsg._sql_plan(parents, bindings, self.target_mode)
        return {'status': 'planned', 'targetMode': self.target_mode, 'targetSqlChildren': 4,
                'sourceChildrenToStage': sum(r['properties']['priority'] != r['temporaryPriority'] for r in rows),
                'sourceChildrenToRetire': len(rows), 'azureWrites': 0,
                'whatIfRequiredAfterStage': bool(rows), 'runtimeVerified': False}


def run(context: OperationContext, *, target_mode='Redirect', execute=False, rollback_phase=None, **options):
    """公開 CLI の Proxy rollback は runtime verifier がないため実行しない。"""
    if type(execute) is not bool:
        raise OperationError('SQL NSG execution mode must be an explicit boolean')
    if execute and target_mode == 'Proxy' and not isinstance(rollback_phase, VerifiedProxyPhase):
        raise OperationError('Public Proxy NSG rollback is unavailable without physical-drain verification')
    try:
        with context.locked():
            operation = SqlNsgUpgrade(context, target_mode=target_mode, rollback_phase=rollback_phase, **options)
            return operation.apply() if execute else operation.plan()
    except (nsg.FixedNsgError, SqlConnectionPolicyError, OSError, ValueError, TypeError, KeyError):
        raise OperationError('SQL NSG migration stopped; inspect protected records and current resources') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--runtime-root', required=True, type=Path,
                        help='Explicit checkout containing the selected protected input and Core state')
    parser.add_argument('--target-mode', choices=('Redirect', 'Proxy'), default='Redirect')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config, runtime_root=args.runtime_root)
        print(json.dumps(run(context, target_mode=args.target_mode, execute=args.execute)))
        return 0
    except (OperationError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'failed', 'message': 'SQL NSG migration stopped; inspect protected records'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
