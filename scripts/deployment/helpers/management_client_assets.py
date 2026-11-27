"""App配備の実出力を管理VMの保守inventoryへ加え、client資材だけを更新する。"""
from __future__ import annotations

from copy import deepcopy
from functools import cache
import json
import re
import time

from management_client import validate_inventory
from .paths import REPOSITORY, local_paths
from .portal_resources import subscription_app_names
from .private_permissions import check_private_permissions


ARM_API = '2022-09-01'
COMPUTE_API = '2024-07-01'
APP_API = '2025-01-01'
SQL_API = '2023-08-01'
FIXED_KEYS = ('schemaVersion', 'subscriptionId', 'azureTenantId', 'managementVmId',
              'managementPrincipalId', 'workspace', 'auditStorage')
FAMILIES = {'applicationIds': 'applications', 'jobIds': 'jobs', 'environmentIds': 'environments',
            'sqlDatabaseIds': 'sqlDatabases'}
SQL_ALIASES = {'gateway': 'credentials', 'portal': 'portal'}


def expected_maintenance_environments(config, state):
    """構築記録にあるBroker環境と、Portal環境の固定IDを対応付ける。"""
    stem = config['resourceStem']
    portal = ('/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['svc']
              + '/providers/Microsoft.App/managedEnvironments/cae-' + stem)
    outputs = state['outputs']
    portal_output = outputs.get('portal')
    if portal_output is not None and (not isinstance(portal_output, dict)
            or portal_output.get('environmentName') != 'cae-' + stem):
        raise ValueError('The Portal maintenance environment differs from its constructed scope')
    if 'brokerPlatform' not in outputs:
        return {'portal': portal, 'gateway': portal}
    broker = outputs['brokerPlatform']
    gateway = portal + '-broker'
    if (not isinstance(broker, dict) or broker.get('environmentName') != 'cae-' + stem + '-broker'
            or str(broker.get('environmentId', '')).casefold() != gateway.casefold()):
        raise ValueError('The Broker maintenance environment differs from its constructed scope')
    return {'portal': portal, 'gateway': gateway}


def matches_maintenance_environment(properties, environment):
    """App／Jobの親環境の別名フィールドも、全て同じIDであることを確認する。"""
    if not isinstance(properties, dict):
        return False
    references = [properties[key] for key in ('environmentId', 'managedEnvironmentId') if properties.get(key)]
    return bool(references) and all(str(value).casefold() == environment.casefold() for value in references)


def _expected_targets(config, *, broker_separated=False):
    """期待名は取り違え検出だけに使い、出力がないIDを補って配備しない。"""
    stem = config['resourceStem']
    base = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['svc'] + '/providers/Microsoft.App/'
    sql = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['svc'] + '/providers/Microsoft.Sql/servers/sql-' + stem + '/databases/sqldb-' + stem + '-'
    return {
        'gateway': {
            'applicationIds': [base + 'containerApps/ca-' + stem + '-credentials'],
            'jobIds': [base + 'jobs/caj-' + stem + '-schema', base + 'jobs/caj-' + stem + '-recon'],
            'environmentIds': [base + 'managedEnvironments/cae-' + stem + ('-broker' if broker_separated else '')],
            'sqlDatabaseIds': [sql + 'credentials'],
        },
        'portal': {
            'applicationIds': [base + 'containerApps/' + name
                               for name in ('ca-' + stem + '-portal', *subscription_app_names(config).values())],
            'jobIds': [base + 'jobs/caj-' + stem + '-psql', base + 'jobs/caj-' + stem + '-psync'],
            'environmentIds': [base + 'managedEnvironments/cae-' + stem],
            'sqlDatabaseIds': [sql + 'portal'],
        },
    }


def management_client_inventory(config, state):
    """旧stateを推定で補わず、今回の構築出力と一致した内部inventoryだけを返す。"""
    if (not isinstance(state, dict) or any(state.get(key) != config[key]
            for key in ('subscriptionId', 'azureTenantId', 'resourceStem'))):
        raise ValueError('Management client Core state differs from the selected configuration')
    value = state.get('outputs', {}).get('managementClientInventory')
    if not isinstance(value, dict) or not value:
        raise ValueError('Current Core management client inventory is required; complete the operations upgrade or current Core deployment')
    try:
        value = deepcopy(validate_inventory(value))
    except (ValueError, RuntimeError, TypeError, KeyError):
        raise ValueError('The constructed Core management client inventory is invalid') from None
    vm = ('/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['maint']
          + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt')
    if (value['subscriptionId'] != config['subscriptionId'] or value['azureTenantId'] != config['azureTenantId']
            or value['managementVmId'].casefold() != vm.casefold()):
        raise ValueError('The constructed management client identity differs from the selected environment')
    environments = expected_maintenance_environments(config, state)
    broker_separated = environments['portal'] != environments['gateway']
    if broker_separated and {item.casefold() for item in value['environments']} != {
            item.casefold() for item in environments.values()}:
        raise ValueError('The constructed Portal and Broker environments are both required in the maintenance inventory')
    allowed = _expected_targets(config, broker_separated=broker_separated)
    for output, family in FAMILIES.items():
        expected = {target.casefold() for component in allowed.values() for target in component[output]}
        ids = [item['id'] for item in value[family]] if family == 'sqlDatabases' else value[family]
        if any(target.casefold() not in expected for target in ids):
            raise ValueError('The management client inventory includes an unrelated constructed target')
        if family == 'sqlDatabases' and any(item['id'].casefold() != allowed[
                next(component for component, alias in SQL_ALIASES.items() if alias == item['alias'])][output][0].casefold()
                for item in value[family]):
            raise ValueError('The SQL database alias does not match its exact constructed database')
    monitor = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['monitor'] + '/providers/'
    if (value['workspace']['id'].casefold() != (monitor + 'Microsoft.OperationalInsights/workspaces/law-' + config['resourceStem']).casefold()
            or any(not account['id'].casefold().startswith((monitor + 'Microsoft.Storage/storageAccounts/').casefold())
                   for account in value['auditStorage'])):
        raise ValueError('The management client inventory differs from the constructed monitoring group')
    outputs = state['outputs']
    constructed_vm = outputs.get('managementVm', {})
    monitoring = outputs.get('monitoring', {})
    storage = outputs.get('auditStorage', {})
    if (not isinstance(constructed_vm, dict) or not isinstance(monitoring, dict) or not isinstance(storage, dict)
            or str(constructed_vm.get('id', '')).casefold() != value['managementVmId'].casefold()
            or str(constructed_vm.get('principalId', '')).casefold() != value['managementPrincipalId'].casefold()
            or str(monitoring.get('workspaceId', '')).casefold() != value['workspace']['id'].casefold()
            or monitoring.get('workspaceCustomerId') != value['workspace']['customerId']
            or not isinstance(storage.get('containerIds'), list)):
        raise ValueError('Management client identity and monitoring targets require their matching constructed Core outputs')
    expected_accounts = {}
    for collector, suffix in (('collectJapan', 'jp'), ('collectFoundry', 'us')):
        account_id = monitor + 'Microsoft.Storage/storageAccounts/st' + config['resourceStem'].replace('-', '') + suffix
        actual = storage.get(collector, {})
        if not isinstance(actual, dict) or str(actual.get('id', '')).casefold() != account_id.casefold():
            raise ValueError('The audit Storage output differs from its exact constructed resource')
        expected_accounts[actual['id'].casefold()] = set()
    for container_id in storage['containerIds']:
        if not isinstance(container_id, str):
            raise ValueError('Constructed audit container output is invalid')
        matched = [account for account in expected_accounts if container_id.casefold().startswith(account + '/blobservices/default/containers/')]
        if len(matched) != 1 or '/' in container_id.split('/containers/', 1)[-1]:
            raise ValueError('An audit container output refers to another constructed Storage scope')
        expected_accounts[matched[0]].add(container_id.rsplit('/', 1)[-1])
    observed_accounts = {account['id'].casefold(): set(account['containers']) for account in value['auditStorage']}
    if len(value['auditStorage']) != 2 or observed_accounts != expected_accounts:
        raise ValueError('The management client audit inventory differs from its exact constructed containers')
    return value


def _merge_outputs(config, inventory, outputs, component):
    if component not in SQL_ALIASES:
        raise ValueError('Select the Gateway or Portal management client construction phase')
    metadata = outputs.get('managementMaintenance') if isinstance(outputs, dict) else None
    if not isinstance(metadata, dict) or set(metadata) != set(FAMILIES):
        raise ValueError('Successful app deployment management maintenance outputs are required')
    inventory = deepcopy(validate_inventory(inventory))
    separated = _expected_targets(config, broker_separated=True)
    broker_environment, = separated['gateway']['environmentIds']
    broker_separated = broker_environment.casefold() in {item.casefold() for item in inventory['environments']}
    expected = (separated if broker_separated else _expected_targets(config))[component]
    if component == 'portal' and any(key in outputs for key in subscription_app_names(config)):
        service_ids = dict(zip(subscription_app_names(config), expected['applicationIds'][1:]))
        endpoint_states = set()
        for key, target in service_ids.items():
            service = outputs.get(key)
            if (not isinstance(service, dict) or str(service.get('id', '')).casefold() != target.casefold()
                    or str(service.get('name', '')).casefold() != target.rsplit('/', 1)[-1].casefold()
                    or not isinstance(service.get('fqdn'), str)):
                raise ValueError('A subscription service output differs from its exact constructed app')
            endpoint_states.add(bool(service['fqdn']))
        expected_apps = expected['applicationIds'] if endpoint_states == {True} else []
        if (len(endpoint_states) != 1 or not isinstance(metadata['applicationIds'], list)
                or sorted(str(item).casefold() for item in metadata['applicationIds']) != sorted(
                    target.casefold() for target in expected_apps)):
            raise ValueError('The subscription service maintenance output is incomplete')
    additions = []
    for output, family in FAMILIES.items():
        ids = metadata[output]
        if not isinstance(ids, list):
            raise ValueError('App maintenance target output must be a constructed-resource array')
        if output == 'environmentIds' and (len(ids) != 1 or not isinstance(ids[0], str)
                or ids[0].casefold() != expected[output][0].casefold()):
            raise ValueError('An app maintenance output must identify its exact constructed ACA environment')
        if output == 'sqlDatabaseIds' and (len(ids) != 1 or not isinstance(ids[0], str)
                or ids[0].casefold() != expected[output][0].casefold()):
            raise ValueError('The app SQL output must identify its exact constructed user database')
        for target in ids:
            if (not isinstance(target, str) or target.casefold() not in {item.casefold() for item in expected[output]}):
                raise ValueError('An app maintenance output refers to another constructed scope')
            if family == 'sqlDatabases':
                alias = SQL_ALIASES[component]
                existing = [item for item in inventory[family] if item['alias'] == alias]
                if existing and any(item['id'].casefold() != target.casefold() for item in existing):
                    raise ValueError('A prior SQL alias points to a different construction target')
                if not existing:
                    inventory[family].append({'alias': alias, 'id': target})
            elif target.casefold() not in {item.casefold() for item in inventory[family]}:
                inventory[family].append(target)
            additions.append(target)
    return validate_inventory(inventory), list(dict.fromkeys(additions))


@cache
def _client_template():
    """Windows側の公開CLIでローカルBicepだけをcompileし、エラー本文を出さない。"""
    # The common runner selects the native Bicep executable on Windows and
    # keeps Azure authentication on the Windows CLI without relying on az.cmd.
    from ..lifecycle import run
    return run(['az', 'bicep', 'build', '--file', str(REPOSITORY / 'infra/modules/management/management-maintenance-client.bicep'), '--stdout'])


def update_management_client_assets(config, state, outputs, transport, *, component, template=None,
                                    repository=REPOSITORY, timeout=1800, sleep=time.sleep, monotonic=time.monotonic):
    """実構築確認→client単独Incremental→runCommand読戻しを行い、本文を公開しない。"""
    # Local imports keep the shared lifecycle free of a helper import cycle.
    from ..lifecycle import protected_json
    from .credential_image import checked
    inventory = management_client_inventory(config, state)
    workspace = local_paths(config, repository)
    checkpoint = workspace.checked(workspace.state / 'management-client-inventory.json')
    if checkpoint.exists():
        check_private_permissions(checkpoint.parent, directory=True)
        check_private_permissions(checkpoint)
        try:
            previous = json.loads(checkpoint.read_text(encoding='utf-8'))
            prior = previous['inventory']
            validate_inventory(prior)
            if any(prior.get(key) != inventory.get(key) for key in FIXED_KEYS):
                raise ValueError('Prior maintenance inventory belongs to another construction instance')
            prior_state = {**state, 'outputs': {**state['outputs'], 'managementClientInventory': prior}}
            inventory = management_client_inventory(config, prior_state)
        except (ValueError, TypeError, KeyError, RuntimeError):
            raise ValueError('The prior protected management client inventory could not be reconciled') from None
    inventory, additions = _merge_outputs(config, inventory, outputs, component)
    tags = {'product': config['productName'], 'environment': config['environmentName'], 'azd-env-name': config['environmentName']}
    expected_environment = expected_maintenance_environments(config, state)[component]

    def owned(body, resource_id, resource_type):
        if (not isinstance(body, dict) or str(body.get('id', '')).casefold() != resource_id.casefold()
                or str(body.get('type', '')).casefold() != resource_type.casefold()
                or not isinstance(body.get('tags'), dict) or any(body['tags'].get(key) != value for key, value in tags.items())):
            raise ValueError('The management client target ownership could not be verified')

    for target in additions:
        is_sql = '/providers/microsoft.sql/' in target.casefold()
        body = checked(transport, 'GET', target + '?api-version=' + (SQL_API if is_sql else APP_API),
                       label='Verify constructed maintenance target')
        owned(body, target, 'Microsoft.Sql/servers/databases' if is_sql else 'Microsoft.App/' + target.rsplit('/', 2)[-2])
        if is_sql:
            if body.get('properties', {}).get('status') != 'Online':
                raise ValueError('A constructed SQL maintenance database is not online')
            server = target.rsplit('/databases/', 1)[0]
            policy = checked(transport, 'GET', server + '/connectionPolicies/default?api-version=' + SQL_API,
                             label='Verify fixed SQL maintenance connection policy')
            if (str(policy.get('id', '')).casefold() != (server + '/connectionPolicies/default').casefold()
                    or policy.get('properties', {}).get('connectionType') != 'Proxy'):
                raise ValueError('SQL maintenance requires the constructed TCP1433 Proxy policy')
        else:
            properties = body.get('properties', {})
            if properties.get('provisioningState') != 'Succeeded':
                raise ValueError('A constructed ACA maintenance target is not ready')
            if '/managedenvironments/' not in target.casefold() and not matches_maintenance_environment(properties, expected_environment):
                raise ValueError('The app or job is outside the constructed ACA environment')
    vm = inventory['managementVmId']
    body = checked(transport, 'GET', vm + '?api-version=' + COMPUTE_API, label='Verify management client VM identity')
    owned(body, vm, 'Microsoft.Compute/virtualMachines')
    if (body.get('properties', {}).get('provisioningState') != 'Succeeded'
            or body.get('identity', {}).get('principalId', '').casefold() != inventory['managementPrincipalId'].casefold()):
        raise ValueError('The management VM instance identity differs from the constructed client inventory')
    command = vm + '/runCommands/prepare-maintenance-client'
    prior_response = transport.request('GET', command + '?api-version=' + COMPUTE_API + '&$expand=instanceView')
    if prior_response.get('statusCode') == 200:
        prior = prior_response.get('body')
        owned(prior, command, 'Microsoft.Compute/virtualMachines/runCommands')
        if prior.get('properties', {}).get('instanceView', {}).get('executionState') not in ('Succeeded', 'Failed', 'Canceled'):
            raise ValueError('A management client preparation operation is still active or unrecognized')
    elif prior_response.get('statusCode') != 404:
        raise ValueError('The existing management client operation could not be inspected')
    template = _client_template() if template is None else deepcopy(template)
    resources = template.get('resources') if isinstance(template, dict) else None
    if (not isinstance(resources, list) or len(resources) != 1
            or resources[0].get('type') != 'Microsoft.Compute/virtualMachines/runCommands'
            or str(template.get('parameters', {}).get('inventory', {}).get('type', '')).lower() != 'secureobject'):
        raise ValueError('The management client template must contain only its protected runCommand resource')
    group = re.split('/providers/', vm, flags=re.IGNORECASE)[0]
    deployment = group + '/providers/Microsoft.Resources/deployments/' + config['resourceStem'] + '-management-client-' + component
    path = deployment + '?api-version=' + ARM_API
    parameters = {'vmName': vm.rsplit('/', 1)[-1], 'location': config['location'],
                  'tags': {**config.get('tags', {}), **tags}, 'inventory': inventory}
    record = workspace.checked(workspace.root / 'evidence' / ('management-client-' + component + '.json'))
    receipt = {'status': 'planned', 'component': component, 'managementVmId': vm, 'inventory': inventory, 'runtimeVerified': False}
    protected_json(record, receipt)
    checked(transport, 'PUT', path, {'properties': {'mode': 'Incremental', 'template': template,
        'parameters': {key: {'value': value} for key, value in parameters.items()}}}, label='Deploy management maintenance client only')
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        response = checked(transport, 'GET', path, label='Read management maintenance client deployment')
        status = response.get('properties', {}).get('provisioningState')
        if status in ('Failed', 'Canceled'):
            raise ValueError('Management client asset deployment failed; retain its protected planned record')
        if status == 'Succeeded':
            observed = checked(transport, 'GET', command + '?api-version=' + COMPUTE_API + '&$expand=instanceView', label='Read management client preparation result')
            owned(observed, command, 'Microsoft.Compute/virtualMachines/runCommands')
            view = observed.get('properties', {}).get('instanceView', {})
            lines = str(view.get('output', '')).splitlines()
            if (view.get('executionState') != 'Succeeded' or view.get('exitCode') != 0
                    or not any(line.strip() == '{"status":"client-prepared","runtimeVerified":false}' for line in lines)):
                raise ValueError('The management client guest preparation has not been verified')
            protected_json(checkpoint, {'schemaVersion': 1, 'inventory': inventory, 'runtimeVerified': False})
            protected_json(record, {**receipt, 'status': 'client-prepared'})
            return {'clientPrepared': True, 'runtimeVerified': False}
        sleep(10)
    raise ValueError('Management client asset deployment is still pending; retain its protected planned record')
