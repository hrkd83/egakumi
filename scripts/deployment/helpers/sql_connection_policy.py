"""Plan and change only the constructed SQL connection-policy child.

The SQL 2023-08-01 contract does not document ETag/If-Match on this child.
Read-before-write, an outer operation lock and readback therefore do not provide
an atomic compare-and-swap against other operators. Mutations are sent once;
their protected journal must survive an interrupted or unknown response.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import re
import time
from uuid import UUID


SQL_API = '2023-08-01'
APP_API = '2025-01-01'
SQL_TYPE = 'Microsoft.Sql/servers'
POLICY_TYPE = SQL_TYPE + '/connectionPolicies'
MODES = ('Default', 'Proxy', 'Redirect')
PENDING = {'submitting', 'accepted', 'response-unknown', 'readback-unknown'}
READINESS_CHECKS = {'nativeLinux', 'managedIdentity', 'tlsHostname',
                    'runtimeProxySql', 'authAndSessions', 'jobsCompatibility'}


class SqlConnectionPolicyError(RuntimeError):
    """Stop with a fixed message, without exposing resource or response values."""


def constructed_sql_server_id(config, state):
    """Use recorded Core outputs; never infer a missing construction output."""
    expected, _, _ = _scope(config)
    try:
        if any(state[key] != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')):
            raise ValueError()
        gateway = state['outputs']['gatewayFoundation']['sqlServerId']
        portal = state['outputs']['portal']['sharedSqlServerId']
        if not isinstance(gateway, str) or gateway.casefold() != expected.casefold():
            raise ValueError()
        if not isinstance(portal, str) or portal.casefold() != expected.casefold():
            raise ValueError()
        return expected
    except (KeyError, TypeError, ValueError):
        raise SqlConnectionPolicyError('The recorded shared SQL construction identity is unavailable') from None


def _scope(config):
    try:
        for key in ('subscriptionId', 'azureTenantId'):
            if not isinstance(config[key], str): raise ValueError()
            UUID(config[key])
        stem = config['resourceStem']
        group = config['resourceGroupNames']['svc']
        for value in (stem, group):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._()-]{0,79}', value):
                raise ValueError()
        for key in ('productName', 'environmentName', 'location'):
            if not isinstance(config[key], str) or not config[key].strip(): raise ValueError()
        group_id = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + group
        server_id = group_id + '/providers/' + SQL_TYPE + '/sql-' + stem
        tags = {key: config[value] for key, value in (('product', 'productName'),
                ('environment', 'environmentName'), ('azd-env-name', 'environmentName'))}
        return server_id, group_id, tags
    except (KeyError, TypeError, ValueError):
        raise SqlConnectionPolicyError('SQL policy operations require a verified construction scope') from None


def _request(transport, method, identity, *, api=SQL_API, body=None):
    try:
        response = transport.request(method, identity + '?api-version=' + api, body=body)
    except Exception:
        raise SqlConnectionPolicyError('SQL policy response is unknown; inspect protected state before retrying') from None
    if (not isinstance(response, dict) or type(response.get('statusCode')) is not int
            or not 100 <= response['statusCode'] <= 599):
        raise SqlConnectionPolicyError('SQL policy response is unknown; inspect protected state before retrying')
    return response


def _get(transport, identity, *, api=SQL_API):
    response = _request(transport, 'GET', identity, api=api)
    if response['statusCode'] != 200 or not isinstance(response.get('body'), dict):
        raise SqlConnectionPolicyError('A required SQL policy resource could not be read')
    return response['body']


def _identity(body, identity, kind):
    if (not isinstance(body, dict) or not isinstance(body.get('id'), str)
            or body['id'].casefold() != identity.casefold()
            or str(body.get('name', '')).casefold() != identity.rsplit('/', 1)[-1].casefold()
            or str(body.get('type', '')).casefold() != kind.casefold()):
        raise SqlConnectionPolicyError('SQL policy resource identity is unverified')


def read_sql_connection_policy(config, transport, *, server_id):
    """Verify RG/server ownership and return a private, non-secret snapshot."""
    expected, group_id, tags = _scope(config)
    if not isinstance(server_id, str) or server_id.casefold() != expected.casefold():
        raise SqlConnectionPolicyError('The SQL policy target differs from the constructed server')
    group = _get(transport, group_id, api='2024-03-01')
    if (str(group.get('id', '')).casefold() != group_id.casefold()
            or not isinstance(group.get('tags'), dict)
            or any(group['tags'].get(key) != tags[key] for key in ('product', 'environment'))):
        raise SqlConnectionPolicyError('The SQL resource-group ownership is unverified')
    server = _get(transport, expected)
    _identity(server, expected, SQL_TYPE)
    props = server.get('properties')
    if (not isinstance(server.get('tags'), dict)
            or any(server['tags'].get(key) != value for key, value in tags.items())
            or str(server.get('location', '')).replace(' ', '').casefold()
               != config['location'].replace(' ', '').casefold()
            or not isinstance(props, dict) or props.get('publicNetworkAccess') != 'Disabled'
            or props.get('provisioningState', 'Succeeded') != 'Succeeded'
            or props.get('state', 'Ready') != 'Ready'):
        raise SqlConnectionPolicyError('The owned private SQL server is not ready')
    policy_id = expected + '/connectionPolicies/default'
    policy = _get(transport, policy_id)
    _identity(policy, policy_id, POLICY_TYPE)
    properties = policy.get('properties')
    if (not isinstance(properties, dict) or properties.get('connectionType') not in MODES
            or properties.get('provisioningState', 'Succeeded') != 'Succeeded'):
        raise SqlConnectionPolicyError('The SQL connection policy is incomplete or unresolved')
    # Ignore volatile ARM metadata, but retain all server configuration in the
    # protected snapshot to detect unintended parent changes during readback.
    parent = {key: copy.deepcopy(server.get(key)) for key in ('id', 'name', 'type', 'location', 'tags', 'properties')}
    return {'server': parent, 'policy': copy.deepcopy(policy),
            'connectionType': properties['connectionType'], 'policyId': policy_id}


def _record(record, event, receipt):
    if record is None:
        raise SqlConnectionPolicyError('SQL policy execution requires a persistent protected journal')
    try:
        record(event, copy.deepcopy(receipt))
    except Exception:
        raise SqlConnectionPolicyError('The protected SQL policy journal could not be saved') from None


def _portal_generations(config, transport):
    """Read current App/Jobs and every active revision; never accept image alone."""
    _, group, tags = _scope(config)
    stem = config['resourceStem']
    app = group + '/providers/Microsoft.App/containerApps/ca-' + stem + '-portal'
    identities = {'app': app, 'schemaJob': group + '/providers/Microsoft.App/jobs/caj-' + stem + '-psql',
                  'reconcileJob': group + '/providers/Microsoft.App/jobs/caj-' + stem + '-psync'}
    generations = {}
    for key, identity in identities.items():
        value = _get(transport, identity, api=APP_API)
        _identity(value, identity, 'Microsoft.App/containerApps' if key == 'app' else 'Microsoft.App/jobs')
        props = value.get('properties')
        system = value.get('systemData')
        if not isinstance(props, dict) or not isinstance(system, dict):
            raise SqlConnectionPolicyError('The current Portal deployment metadata is unavailable')
        created = system.get('createdAt')
        modified = system.get('lastModifiedAt')
        configuration = props.get('configuration')
        template = props.get('template')
        containers = template.get('containers') if isinstance(template, dict) else None
        if (props.get('provisioningState') != 'Succeeded' or not isinstance(created, str) or not created
                or modified is not None and (not isinstance(modified, str) or not modified)
                or not isinstance(configuration, dict)
                or not isinstance(value.get('tags'), dict)
                or any(value['tags'].get(k) != v for k, v in tags.items())
                or not isinstance(value.get('identity'), dict)
                or not isinstance(containers, list) or len(containers) != 1
                or not isinstance(containers[0], dict)
                or not re.fullmatch(r'[a-z0-9.-]+/portal@sha256:[a-f0-9]{64}', str(containers[0].get('image', '')))):
            raise SqlConnectionPolicyError('The current Portal deployment generation is unverified')
        # lastModifiedAt is optional in ARM and is explicitly bound as None
        # when absent. Configuration must be available for runtime acceptance,
        # even though the general ARM response schema marks it optional.
        generations[key] = {'id': identity, 'createdAt': created, 'lastModifiedAt': modified,
                            'configuration': copy.deepcopy(configuration),
                            'identity': copy.deepcopy(value['identity']), 'template': copy.deepcopy(template)}
    listing = _get(transport, app + '/revisions', api=APP_API)
    if not isinstance(listing.get('value'), list) or listing.get('nextLink'):
        raise SqlConnectionPolicyError('The active Portal revision inventory is incomplete')
    revisions = []
    seen = set()
    for item in listing['value']:
        if not isinstance(item, dict) or not isinstance(item.get('properties'), dict):
            raise SqlConnectionPolicyError('The Portal revision inventory is malformed')
        props = item['properties']
        if type(props.get('active')) is not bool:
            raise SqlConnectionPolicyError('The Portal revision activation state is unverified')
        if not props['active']: continue
        identity = item.get('id')
        if (not isinstance(identity, str) or not identity.casefold().startswith((app + '/revisions/').casefold())
                or '/' in identity[len(app + '/revisions/'):]
                or identity.casefold() in seen):
            raise SqlConnectionPolicyError('The active Portal revision identity is ambiguous')
        _identity(item, identity, 'Microsoft.App/containerApps/revisions')
        if (props.get('provisioningState') != 'Succeeded' or props.get('healthState') != 'Healthy'
                or props.get('runningState') != 'Running'
                or not isinstance(props.get('createdTime'), str) or not props['createdTime']
                or not isinstance(props.get('template'), dict)):
            raise SqlConnectionPolicyError('An active Portal revision is not ready')
        seen.add(identity.casefold())
        revisions.append({'id': identity, 'createdTime': props['createdTime'],
                          'template': copy.deepcopy(props['template'])})
    if not revisions: raise SqlConnectionPolicyError('No active Portal revision was verified')
    return {'resources': generations, 'activeRevisions': sorted(revisions, key=lambda row: row['id'].casefold())}


def verify_portal_redirect_readiness(config, transport, *, server_id, verifier, now=None):
    """Internal verifier contract; there is no public flag or proof-file bypass.

The verifier must read protected runtime evidence, not fabricate proof from a
user assertion. A selected driver and current Proxy runtime acceptance must be
established by that future implementation. No production verifier is registered
yet, so the public operation cannot execute a Redirect switch.
"""
    if not callable(verifier):
        raise SqlConnectionPolicyError('Redirect execution is unavailable until the Portal runtime verifier is implemented')
    try:
        proof = verifier(config, transport, server_id=server_id)
        current = datetime.now(timezone.utc) if now is None else now
        verified = datetime.fromisoformat(proof['verifiedAt'].replace('Z', '+00:00'))
        age = (current - verified).total_seconds()
        if (not isinstance(proof, dict) or type(proof.get('schemaVersion')) is not int
                or proof.get('schemaVersion') != 1
                or proof.get('kind') != 'portal-odbc18-proxy-runtime' or proof.get('status') != 'verified'
                or proof.get('sqlServerId') != server_id or proof.get('observedPolicy') != 'Proxy'
                or not isinstance(proof.get('checks'), dict) or set(proof['checks']) != READINESS_CHECKS
                or any(value is not True for value in proof['checks'].values()) or not 0 <= age <= 86400):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SqlConnectionPolicyError('Protected Portal runtime acceptance is missing, stale or incomplete') from None
    except Exception:
        raise SqlConnectionPolicyError('The Portal runtime verifier could not complete') from None
    generations = _portal_generations(config, transport)
    if any(proof.get(key) != value for key, value in generations.items()):
        raise SqlConnectionPolicyError('The Portal deployment changed after protected runtime acceptance')
    return copy.deepcopy(proof)


def change_sql_connection_policy(config, transport, *, server_id, target_mode,
                                 expected_current, execute=False, record=None,
                                 readiness_verifier=None, prior_receipt=None,
                                 sleep=time.sleep, monotonic=time.monotonic,
                                 timeout=180, now=None):
    """Return a public summary; detailed state is sent only to protected record.

Proxy changes affect the policy only. Physical-connection draining and NSG
rollback remain separate operations. Neither readback nor reconciliation proves
the application's Redirect runtime behavior.
"""
    if target_mode not in ('Proxy', 'Redirect') or expected_current not in MODES:
        raise SqlConnectionPolicyError('Select an explicit SQL policy target and expected current mode')
    if type(execute) is not bool or type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        raise SqlConnectionPolicyError('The SQL policy operation bounds are invalid')
    before = read_sql_connection_policy(config, transport, server_id=server_id)
    receipt = {'schemaVersion': 1, 'operation': 'sql-connection-policy-child',
               'policyId': before['policyId'], 'targetMode': target_mode,
               'expectedCurrent': expected_current, 'before': before,
               'conditionalPutSupported': False,
               'concurrencyProtection': 'local-operation-lock-and-readback',
               'runtimeVerified': False}
    if prior_receipt is not None:
        if (not isinstance(prior_receipt, dict) or type(prior_receipt.get('schemaVersion')) is not int
                or prior_receipt.get('schemaVersion') != 1
                or prior_receipt.get('operation') != receipt['operation']
                or prior_receipt.get('policyId') != receipt['policyId']
                or prior_receipt.get('targetMode') != target_mode
                or prior_receipt.get('expectedCurrent') != expected_current
                or not isinstance(prior_receipt.get('before'), dict)
                or prior_receipt['before'].get('policyId') != before['policyId']
                or prior_receipt['before'].get('connectionType') != expected_current
                or prior_receipt['before'].get('server') != before['server']):
            raise SqlConnectionPolicyError('The saved SQL policy operation belongs to a different target or generation')
        status = prior_receipt.get('status')
        if status not in PENDING | {'verified', 'reconciled', 'rejected'}:
            raise SqlConnectionPolicyError('The saved SQL policy operation has an unsupported state')
        if before['connectionType'] == target_mode and status in PENDING | {'verified', 'reconciled'}:
            if execute:
                _record(record, 'reconciled', {**prior_receipt, 'status': 'reconciled', 'after': before})
            return {'status': 'reconciled' if execute else 'reconciliation-planned', 'targetMode': target_mode,
                    'azureWrites': 0, 'runtimeVerified': False, 'connectionDrainRequired': True}
        if status in PENDING:
            raise SqlConnectionPolicyError('A submitted SQL policy operation is unresolved; no mutation will be resent')
    if before['connectionType'] != expected_current:
        raise SqlConnectionPolicyError('The SQL connection policy differs from the expected current mode')
    if not execute:
        if record is not None: _record(record, 'planned', {**receipt, 'status': 'planned'})
        return {'status': 'planned', 'targetMode': target_mode,
                'azureWrites': 0, 'runtimeVerified': False,
                'redirectExecutionAvailable': callable(readiness_verifier)}
    if record is None:
        raise SqlConnectionPolicyError('SQL policy execution requires a persistent protected journal')
    if target_mode == 'Redirect' and not callable(readiness_verifier):
        raise SqlConnectionPolicyError('Redirect execution is unavailable until the Portal runtime verifier is implemented')
    if target_mode == expected_current:
        return {'status': 'unchanged', 'targetMode': target_mode, 'azureWrites': 0,
                'runtimeVerified': False, 'connectionDrainRequired': target_mode == 'Proxy'}
    if target_mode == 'Redirect':
        if expected_current != 'Proxy':
            raise SqlConnectionPolicyError('Redirect migration requires verified Proxy runtime acceptance first')
        proof = verify_portal_redirect_readiness(config, transport, server_id=server_id,
                                                 verifier=readiness_verifier, now=now)
        from .fixed_nsg import verify_sql_nsg_rules
        try:
            network = verify_sql_nsg_rules(config, transport, target_mode='Redirect', require_retired=True)
        except Exception:
            raise SqlConnectionPolicyError('The four Redirect SQL NSG rules could not be verified') from None
        if not isinstance(network, dict) or network.get('sqlRulesVerified') != 4 or network.get('targetMode') != 'Redirect':
            raise SqlConnectionPolicyError('The four Redirect SQL NSG rules are unverified')
        receipt.update(portalAcceptance=proof, networkAcceptance=network)
    # Re-read all target ownership/configuration after acceptance checks.
    fresh = read_sql_connection_policy(config, transport, server_id=server_id)
    if fresh != before:
        raise SqlConnectionPolicyError('The SQL policy target changed during preflight')
    if target_mode == 'Redirect':
        generations = _portal_generations(config, transport)
        if any(proof.get(key) != value for key, value in generations.items()):
            raise SqlConnectionPolicyError('The Portal deployment changed during SQL policy preflight')
    receipt['status'] = 'submitting'
    _record(record, 'submitting', receipt)  # Persist before the one mutation.
    try:
        response = _request(transport, 'PUT', before['policyId'],
                            body={'properties': {'connectionType': target_mode}})
    except SqlConnectionPolicyError:
        _record(record, 'response-unknown', {**receipt, 'status': 'response-unknown'})
        raise
    if response['statusCode'] not in (200, 202):
        uncertain = not 400 <= response['statusCode'] < 500 or response['statusCode'] in (408, 429)
        status = 'response-unknown' if uncertain else 'rejected'
        _record(record, status, {**receipt, 'status': status, 'httpStatus': response['statusCode']})
        raise SqlConnectionPolicyError('The SQL policy mutation was not verified; inspect protected state')
    _record(record, 'accepted', {**receipt, 'status': 'accepted', 'httpStatus': response['statusCode']})
    deadline = monotonic() + timeout
    while True:
        try:
            after = read_sql_connection_policy(config, transport, server_id=server_id)
            if after['server'] != before['server']:
                raise SqlConnectionPolicyError('The SQL parent changed during policy readback')
            if after['connectionType'] == target_mode:
                _record(record, 'verified', {**receipt, 'status': 'verified', 'after': after})
                return {'status': 'verified', 'targetMode': target_mode, 'azureWrites': 1,
                        'runtimeVerified': False, 'connectionDrainRequired': True}
            if after['connectionType'] != expected_current:
                raise SqlConnectionPolicyError('SQL policy readback detected an unexpected mode')
            if monotonic() >= deadline:
                raise SqlConnectionPolicyError('SQL policy readback did not finish; no mutation will be resent')
            sleep(min(5, max(0, deadline - monotonic())))
        except SqlConnectionPolicyError:
            _record(record, 'readback-unknown', {**receipt, 'status': 'readback-unknown'})
            raise
