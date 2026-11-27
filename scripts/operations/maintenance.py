#!/usr/bin/env python3
"""構築済み環境の監査用ネットワークと保守クライアント資材を更新する。"""
from __future__ import annotations

import os
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import copy
import json
from pathlib import Path
import re
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deployment.helpers import fixed_nsg as nsg
from deployment.helpers.credential_pools import resource_names as broker_resource_names
from deployment.helpers.management_client_assets import expected_maintenance_environments, matches_maintenance_environment
from deployment.helpers.management_maintenance import management_maintenance_inputs
from deployment.helpers.paths import REPOSITORY
from deployment.helpers.portal_resources import resource_names
from deployment.lifecycle import ArmTransport, DeploymentError, run
from operations.common import OperationContext, OperationError


DEPLOYMENT_API = '2022-09-01'
COLLECTORS = ('collectJapan', 'collectFoundry')
AUDIT_RULES = {
    'management': ('allow-audit-storage-platform-https-egress', 'allow-audit-storage-foundry-https-egress'),
    'pep': ('allow-mgmt-vm-audit-storage-platform-https-ingress', 'allow-mgmt-vm-audit-storage-foundry-https-ingress'),
}
MODULES = {
    'endpoints': ('monitoring/audit-storage-private-endpoints.bicep', {'Microsoft.Network/privateEndpoints', 'Microsoft.Resources/deployments'}),
    'dns': ('monitoring/audit-storage-private-dns.bicep', {'Microsoft.Network/privateDnsZones', 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups', 'Microsoft.Resources/deployments'}),
    'link': ('monitoring/audit-storage-private-dns-link.bicep', {'Microsoft.Network/privateDnsZones/virtualNetworkLinks'}),
    'roles': ('management/management-maintenance-roles.bicep', {'Microsoft.Authorization/roleDefinitions'}),
    'client': ('management/management-maintenance-client.bicep', {'Microsoft.Compute/virtualMachines/runCommands'}),
}


def verify_existing_rules(config, transport):
    """追加前にも既存72件を全件照合し、手動変更や欠落があれば停止する。"""
    bindings = nsg.read_fixed_nsg_bindings(config, transport, permit_pending_audit_endpoints=True)
    parents = nsg._parents(config, transport)
    nsg.verify_fixed_nsg_inventory(parents, bindings)
    for group, definition in nsg._catalog('core-rules.json')['groups'].items():
        actual = nsg._inventory(parents[group])
        for name, rule in definition['rules'].items():
            if name in AUDIT_RULES.get(group, ()):
                continue
            if name not in actual or nsg._properties(actual[name]) != nsg._definition(rule, bindings):
                raise OperationError('The existing fixed NSG rules are missing or changed')
    return bindings


def checked_template(template, permitted):
    """配備moduleの変更対象を限定し、Storage/VM/APIM等の全PUTを拒否する。"""
    resources = template.get('resources') if isinstance(template, dict) else None
    if not isinstance(resources, list):
        raise OperationError('The maintenance template is incomplete')
    for resource in resources:
        if resource.get('type') not in permitted:
            raise OperationError('The maintenance template writes outside its approved resource types')
        if resource['type'] == 'Microsoft.Resources/deployments':
            properties = resource.get('properties')
            if (not isinstance(properties, dict) or properties.get('mode') != 'Incremental'
                    or 'templateLink' in properties):
                raise OperationError('Nested maintenance reads require an inline Incremental deployment')
            nested = resource.get('properties', {}).get('template')
            # The PE address module reads NICs and has no resource mutations.
            checked_template(nested, set())
    return template


def verify_role_what_if(config, document, definitions):
    """ARMが展開した4ロールだけを許し、既存定義の変更を拒否する。"""
    if not isinstance(document, dict) or document.get('status') != 'Succeeded' or document.get('error'):
        raise OperationError('Management role what-if is incomplete')
    changes = document.get('changes')
    if not isinstance(changes, list) or len(changes) != len(definitions):
        raise OperationError('Management role what-if did not expand all approved roles')
    scope = '/subscriptions/' + config['subscriptionId']
    names = {config['resourceStem'] + ' management ' + definition['name']: definition for definition in definitions}
    observed = set(); observed_ids = set()
    def permissions(value):
        if not isinstance(value, list) or len(value) != 1:
            raise OperationError('Management role what-if has incomplete permission groups')
        keys = ('actions', 'notActions', 'dataActions', 'notDataActions')
        if not isinstance(value[0], dict) or set(value[0]) - set(keys):
            raise OperationError('Management role what-if has unexpected permission fields')
        result = {}
        for key in keys:
            # ARM what-if omits empty action/exception arrays. Their absence
            # is an empty set; nonempty actions must still match exactly.
            items = value[0].get(key)
            if items is None:
                items = []
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items) or len(items) != len(set(items)):
                raise OperationError('Management role what-if has malformed actions')
            result[key] = sorted(items)
        return result
    for change in changes:
        if change.get('changeType') not in ('Create', 'NoChange', 'Modify'):
            raise OperationError('Management role what-if contains an unexpected action')
        identity = change.get('resourceId', '')
        pattern = re.escape(scope + '/providers/Microsoft.Authorization/roleDefinitions/') + r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
        if not isinstance(identity, str) or not re.fullmatch(pattern, identity, re.IGNORECASE) or identity.casefold() in observed_ids:
            raise OperationError('Management role what-if writes outside its role definitions')
        try:
            if uuid.UUID(identity.rsplit('/', 1)[-1]).int == 0:
                raise ValueError()
        except (ValueError, AttributeError):
            raise OperationError('Management role what-if contains an invalid role identity') from None
        after = change.get('after')
        if after is None and change['changeType'] == 'NoChange':
            after = change.get('before')
        if (not isinstance(after, dict) or after.get('type', '').casefold() != 'microsoft.authorization/roledefinitions'
                or after.get('id', '').casefold() != identity.casefold()):
            raise OperationError('Management role what-if did not return the exact proposed resource')
        properties = after.get('properties') if isinstance(after, dict) else None
        if not isinstance(properties, dict) or properties.get('roleName') not in names:
            raise OperationError('Management role what-if contains an unapproved role name')
        name = properties['roleName']
        definition = names[name]
        desired = {'roleName': name, 'description': definition['description'], 'type': 'CustomRole',
                   'assignableScopes': [scope], 'permissions': [{'actions': definition['actions'], 'notActions': [],
                         'dataActions': definition['dataActions'], 'notDataActions': []}]}
        if (name in observed or any(properties.get(key) != value for key, value in desired.items() if key != 'permissions')
                or permissions(properties.get('permissions')) != permissions(desired['permissions'])):
            raise OperationError('Management role what-if permissions differ from the approved definitions')
        before = change.get('before')
        if before is not None:
            if (not isinstance(before, dict) or before.get('id', '').casefold() != identity.casefold()
                    or before.get('type', '').casefold() != 'microsoft.authorization/roledefinitions'):
                raise OperationError('Management role what-if returned another existing resource')
            existing = before.get('properties', {})
            if (any(existing.get(key) != value for key, value in desired.items() if key != 'permissions')
                    or permissions(existing.get('permissions')) != permissions(desired['permissions'])):
                raise OperationError('An existing management role differs; it will not be overwritten')
        observed.add(name)
        observed_ids.add(identity.casefold())
    return {'status': 'roles-validated', 'rolesVerified': len(observed)}


class MaintenanceUpgrade:
    """前提照合→PE→NSG→DNS→ロール定義とclient→stateの順序を保護記録付きで実行する。"""

    def __init__(self, context, *, compiler=None, sleep=time.sleep, monotonic=time.monotonic):
        self.context = context
        self.config = context.config
        self.sleep, self.monotonic = sleep, monotonic
        self.compiler = compiler or self._compile
        if context.transport is None:
            context.transport = ArmTransport(self.config)
        self.transport = context.transport

    def _compile(self, filename):
        try:
            return run(['az', 'bicep', 'build', '--file', str(REPOSITORY / 'infra/modules' / filename), '--stdout'],
                       environment={'AZURE_LOGGING_ENABLE_LOG_FILE': 'false', 'PYTHONDONTWRITEBYTECODE': '1'})
        except DeploymentError:
            raise OperationError('The maintenance module could not be compiled') from None

    def _state_current(self):
        if json.loads(self.context.state_path.read_text(encoding='utf-8')) != self.context.state:
            raise OperationError('Core state changed; stop the maintenance upgrade and reconcile outputs')

    def _optional(self, identity, kind, api=nsg.API):
        result = self.context.request('GET', identity, api)
        if result.get('statusCode') == 404:
            return None
        value = result.get('body')
        if result.get('statusCode') != 200 or not isinstance(value, dict) or value.get('id', '').casefold() != identity.casefold() or value.get('type', '').casefold() != kind.casefold():
            raise OperationError('A proposed maintenance resource has an unexpected identity')
        return value

    def verify_dns(self, bindings):
        """既存の手動DNSを上書きせず、構築専用の同じ参照だけを採用する。"""
        groups, stem, _ = nsg._scope(self.config)
        nw = groups['nw'] + '/providers/Microsoft.Network/'
        zone_id = nw + 'privateDnsZones/privatelink.blob.core.windows.net'
        zone = self._optional(zone_id, 'Microsoft.Network/privateDnsZones', '2024-06-01')
        link_id = zone_id + '/virtualNetworkLinks/link-' + stem
        link = self._optional(link_id, 'Microsoft.Network/privateDnsZones/virtualNetworkLinks', '2024-06-01')
        expected_tags = nsg._initial_nsg_tags(self.config)
        if zone is not None:
            if zone.get('tags') != expected_tags or zone.get('location') != 'global':
                raise OperationError('An existing Blob private DNS zone differs from the initial owned definition')
            count = zone.get('properties', {}).get('numberOfVirtualNetworkLinks')
            if count != (1 if link is not None else 0):
                raise OperationError('The Blob private DNS zone is linked outside the initial VNet')
        if link is not None:
            properties = link.get('properties', {})
            if (zone is None or link.get('tags') != expected_tags
                    or properties.get('virtualNetwork', {}).get('id', '').casefold() != (nw+'virtualNetworks/vnet-'+stem).casefold()
                    or properties.get('registrationEnabled') is not False or properties.get('resolutionPolicy') != 'NxDomainRedirect'
                    or properties.get('provisioningState') != 'Succeeded'):
                raise OperationError('An existing Blob DNS link differs; it will not be replaced')
        for key, suffix in (('auditStorageJapan', 'audit-japan-blob'), ('auditStorageFoundry', 'audit-foundry-blob')):
            group_id = nw + 'privateEndpoints/pe-' + stem + '-' + suffix + '/privateDnsZoneGroups/default'
            group = self._optional(group_id, 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups')
            collector = 'collectJapan' if key == 'auditStorageJapan' else 'collectFoundry'
            account_name = self.context.state['outputs']['auditStorage'][collector]['name']
            record = self._optional(zone_id+'/A/'+account_name, 'Microsoft.Network/privateDnsZones/A', '2024-06-01')
            if group is not None:
                properties = group.get('properties', {})
                configs = properties.get('privateDnsZoneConfigs')
                if (zone is None or properties.get('provisioningState') != 'Succeeded'
                        or not isinstance(configs, list) or len(configs) != 1 or configs[0].get('name') != 'blob'
                        or configs[0].get('properties', {}).get('privateDnsZoneId', '').casefold() != zone_id.casefold()):
                    raise OperationError('An existing audit DNS zone group refers to another zone')
            if record is not None:
                addresses = [entry.get('ipv4Address') for entry in record.get('properties', {}).get('aRecords', [])]
                if group is None or key not in bindings['ipSets'] or sorted(addresses) != sorted(bindings['ipSets'][key]):
                    raise OperationError('An existing audit Blob DNS record is unmanaged or differs from the endpoint')

    def validate_roles(self):
        template = checked_template(self.compiler(MODULES['roles'][0]), MODULES['roles'][1])
        definitions = template.get('variables', {}).get('definitions')
        if not isinstance(definitions, list) or len(definitions) != 4:
            raise OperationError('The approved management role definitions are incomplete')
        path = self.context.workspace.root / 'generated' / ('maintenance-roles-' + uuid.uuid4().hex + '.parameters.json')
        self.context.save_private(path, {'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
                                        'contentVersion':'1.0.0.0', 'parameters': {'resourceStem': {'value': self.config['resourceStem']}}})
        try:
            response = run(['az', 'deployment', 'sub', 'what-if',
                '--location', self.config['location'], '--name', self.config['resourceStem']+'-operations-roles-check',
                '--template-file', str(REPOSITORY/'infra/modules'/MODULES['roles'][0]), '--parameters', '@'+str(path),
                '--result-format', 'FullResourcePayloads', '--no-pretty-print', '--output', 'json'],
                environment={'AZURE_LOGGING_ENABLE_LOG_FILE': 'false', 'PYTHONDONTWRITEBYTECODE': '1'})
            self.context.record('maintenance', {'status': 'role-what-if-observed', 'whatIf': response})
            proof = verify_role_what_if(self.config, response, definitions)
            self.context.record('maintenance', {**proof, 'whatIf': response})
            return proof
        except DeploymentError:
            raise OperationError('Management role what-if could not be completed') from None
        finally:
            path.unlink(missing_ok=True)

    def _request(self, method, identity, body=None):
        # Subscription deployments are limited to this operation's exact role
        # deployment. Other calls still use OperationContext's five-RG boundary.
        roles_id = (f"/subscriptions/{self.config['subscriptionId']}/providers/Microsoft.Resources/deployments/"
                    f"{self.config['resourceStem']}-operations-roles")
        if identity != roles_id:
            return self.context.request(method, identity, DEPLOYMENT_API, body=body)
        try:
            return self.transport.request(method, identity + '?api-version=' + DEPLOYMENT_API, body=body)
        except Exception:
            raise OperationError('Maintenance role deployment response is unknown; inspect before retrying') from None

    def deploy(self, stage, group, values):
        self._state_current()
        filename, permitted = MODULES[stage]
        template = checked_template(self.compiler(filename), permitted)
        scope = f"/subscriptions/{self.config['subscriptionId']}"
        if group is not None:
            scope += '/resourceGroups/' + self.config['resourceGroupNames'][group]
        identity = scope + '/providers/Microsoft.Resources/deployments/' + self.config['resourceStem'] + '-operations-' + stage
        self.context.record('maintenance', {'stage': stage, 'status': 'requesting', 'target': identity,
                                            'parameters': values})
        body = {'properties': {'mode': 'Incremental',
                'template': template, 'parameters': {key: {'value': value} for key, value in values.items()}}}
        # Subscription deployments require a location. A resource-group
        # deployment uses its group's location and rejects this top-level field.
        if group is None:
            body['location'] = self.config['location']
        result = self._request('PUT', identity, body)
        if result.get('statusCode') not in (200, 201, 202):
            self.context.record('maintenance', {'stage': stage, 'status': 'deployment-rejected', 'response': result})
            raise OperationError('The maintenance deployment was rejected')
        deadline = self.monotonic() + 1800
        while self.monotonic() < deadline:
            response = self._request('GET', identity)
            if response.get('statusCode') != 200:
                raise OperationError('The maintenance deployment status could not be read')
            properties = (response.get('body') or {}).get('properties', {})
            state = properties.get('provisioningState')
            if state == 'Succeeded':
                self._state_current()
                self.context.record('maintenance', {'stage': stage, 'status': 'deployment-succeeded'})
                return {key: value['value'] for key, value in properties.get('outputs', {}).items()}
            if state in ('Failed', 'Canceled', 'Deleted'):
                self.context.record('maintenance', {'stage': stage, 'status': 'deployment-failed', 'response': response})
                raise OperationError('The maintenance deployment failed; inspect its protected record')
            self.sleep(5)
        raise OperationError('The maintenance deployment remains pending; do not resubmit blindly')

    def preflight(self):
        """構築記録の資源を現物照合し、採用外の認可を追加しない。"""
        outputs = self.context.state['outputs']
        bindings = verify_existing_rules(self.config, self.transport)
        vm = self.context.verify_resource(outputs['managementVm']['id'], '2024-07-01', 'Microsoft.Compute/virtualMachines')
        groups, stem, _ = nsg._scope(self.config)
        expected_vm = groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + stem + '-mgmt'
        if vm['id'].casefold() != expected_vm.casefold() or vm.get('name') != outputs['managementVm'].get('name'):
            raise OperationError('The recorded management VM is outside the initial fixed construction')
        principal = vm.get('identity', {}).get('principalId')
        if not isinstance(principal, str) or vm.get('identity', {}).get('type') not in ('SystemAssigned', 'SystemAssigned, UserAssigned'):
            raise OperationError('The constructed management VM system identity is unavailable')
        if principal.casefold() != str(outputs['managementVm'].get('principalId', '')).casefold():
            raise OperationError('The management VM identity changed since the recorded construction')
        storage = outputs['auditStorage']
        for collector in COLLECTORS:
            value = self.context.verify_resource(storage[collector]['id'], '2023-05-01', 'Microsoft.Storage/storageAccounts')
            suffix = 'jp' if collector == 'collectJapan' else 'us'
            expected_account = groups['monitor'] + '/providers/Microsoft.Storage/storageAccounts/st' + stem.replace('-', '') + suffix
            if value.get('name') != storage[collector]['name'] or value['id'].casefold() != expected_account.casefold():
                raise OperationError('The constructed audit storage name differs')
        containers = storage.get('containerIds')
        if not isinstance(containers, list) or not containers or len(containers) != len(set(containers)):
            raise OperationError('The constructed audit container inventory is incomplete')
        for identity in containers:
            matching = [key for key in COLLECTORS if identity.casefold().startswith(storage[key]['id'].casefold() + '/blobservices/default/containers/')]
            if len(matching) != 1:
                raise OperationError('An audit container is outside the constructed collectors')
            self.context.verify_resource(identity, '2023-05-01', 'Microsoft.Storage/storageAccounts/blobServices/containers')
        svc = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames']['svc']}/providers/Microsoft.App/"
        try:
            environments = expected_maintenance_environments(self.config, self.context.state)
        except (KeyError, ValueError) as error:
            raise OperationError('The recorded ACA environment scopes differ from the construction') from error
        environment_ids = list(dict.fromkeys(environments.values()))
        for identity in environment_ids:
            environment = self.context.verify_resource(identity, '2025-01-01', 'Microsoft.App/managedEnvironments')
            if len(environment_ids) == 2 and environment.get('properties', {}).get('vnetConfiguration', {}).get('internal') is not True:
                raise OperationError('Both constructed ACA environments must be internal')
        broker = broker_resource_names(self.config)
        portal_app = 'ca-' + stem + '-portal'
        if outputs['portal'].get('applicationName') != portal_app:
            raise OperationError('The recorded Portal application differs from the construction')
        applications = {'portal': [portal_app], 'gateway': [broker['application']]}
        jobs = {'portal': list(resource_names(self.config).values()), 'gateway': [broker['schemaJob'], broker['reconcileJob']]}
        for kind, targets in (('containerApps', applications), ('jobs', jobs)):
            for component, names in targets.items():
                for name in names:
                    resource = self.context.verify_resource(svc + kind + '/' + name, '2025-01-01', 'Microsoft.App/' + kind)
                    if not matches_maintenance_environment(resource.get('properties'), environments[component]):
                        raise OperationError('A maintenance app or job is outside its constructed ACA environment')
        app_names = [*applications['portal'], *applications['gateway']]
        job_names = [*jobs['portal'], *jobs['gateway']]
        sql_server = groups['svc'] + '/providers/Microsoft.Sql/servers/sql-' + stem
        if str(outputs['portal'].get('sharedSqlServerId', '')).casefold() != sql_server.casefold():
            raise OperationError('The constructed SQL server output is missing or differs')
        self.context.verify_resource(sql_server, '2023-08-01', 'Microsoft.Sql/servers')
        sql_databases = []
        for alias in ('credentials', 'portal'):
            identity = sql_server + '/databases/sqldb-' + stem + '-' + alias
            value = self.context.verify_resource(identity, '2023-08-01', 'Microsoft.Sql/servers/databases')
            if value.get('properties', {}).get('status') != 'Online':
                raise OperationError('A constructed maintenance database is not online')
            sql_databases.append({'alias': alias, 'id': value['id']})
        monitoring = outputs['monitoring']
        workspace = self.context.verify_resource(monitoring['workspaceId'], '2023-09-01', 'Microsoft.OperationalInsights/workspaces')
        expected_workspace = groups['monitor'] + '/providers/Microsoft.OperationalInsights/workspaces/law-' + stem
        if (workspace.get('name') != monitoring['workspaceName'] or workspace['id'].casefold() != expected_workspace.casefold()
                or workspace.get('properties', {}).get('customerId') != monitoring['workspaceCustomerId']):
            raise OperationError('The recorded workspace name and ID differ')
        self.verify_dns(bindings)
        plan = {'status': 'verified-plan', 'principalId': principal, 'bindings': bindings, 'auditStorage': storage,
                'sqlDatabases': sql_databases,
                'applicationNames': app_names, 'jobNames': job_names,
                'environmentNames': [identity.rsplit('/', 1)[-1] for identity in environment_ids]}
        self.context.record('maintenance', plan)
        return plan

    def client_inventory(self, plan):
        """実在照合済みの構築出力だけからVM内用inventoryを生成する。"""
        outputs = self.context.state['outputs']
        group = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames']['svc']}/providers/Microsoft.App/"
        return {'schemaVersion': 1, 'subscriptionId': self.config['subscriptionId'],
                'azureTenantId': self.config['azureTenantId'], 'managementVmId': outputs['managementVm']['id'],
                'managementPrincipalId': plan['principalId'],
                'sqlDatabases': copy.deepcopy(plan['sqlDatabases']),
                'applications': [group+'containerApps/'+name for name in plan['applicationNames']],
                'jobs': [group+'jobs/'+name for name in plan['jobNames']],
                'environments': [group+'managedEnvironments/'+name for name in plan['environmentNames']],
                'workspace': {'id': outputs['monitoring']['workspaceId'], 'customerId': outputs['monitoring']['workspaceCustomerId']},
                'auditStorage': [{'id': plan['auditStorage'][key]['id'],
                                  'containers': [identity.rsplit('/',1)[-1] for identity in plan['auditStorage']['containerIds']
                                                 if identity.casefold().startswith(plan['auditStorage'][key]['id'].casefold()+'/')]} for key in COLLECTORS]}

    def apply(self):
        """全体再upをせず、限定moduleと4個のNSG子規則を適用する。"""
        plan = self.preflight()
        self.validate_roles()
        self.context.record('maintenance', {'status': 'before', 'state': self.context.state,
                                           'nsgParents': nsg._parents(self.config, self.transport)})
        tags = nsg._initial_nsg_tags(self.config)
        groups, stem, _ = nsg._scope(self.config)
        nw = groups['nw'] + '/providers/Microsoft.Network/'
        vnet = nw + 'virtualNetworks/vnet-' + stem
        endpoints = self.deploy('endpoints', 'nw', {'resourcePrefix': stem, 'location': self.config['location'],
                              'tags': tags, 'pepSubnetId': vnet + '/subnets/snet-' + stem + '-pep', 'auditStorage': plan['auditStorage']})
        bindings = nsg.read_fixed_nsg_bindings(self.config, self.transport)
        parents = nsg._parents(self.config, self.transport)
        catalog = nsg._catalog('core-rules.json')
        for group, names in AUDIT_RULES.items():
            for name in names:
                self._state_current()
                rule = nsg._definition(catalog['groups'][group]['rules'][name], bindings)
                inventory = nsg._inventory(parents[group])
                actual = inventory.get(name)
                if actual is not None:
                    if nsg._properties(actual) != rule:
                        raise OperationError('An audit NSG child changed outside this upgrade')
                    continue
                identity = parents[group]['id'] + '/securityRules/' + name
                values = {key: rule[key] for key in ('priority', 'direction', 'access', 'protocol')}
                for one, many in nsg._PAIRS:
                    array = rule[one]
                    values[one if len(array) == 1 else many] = array[0] if len(array) == 1 else array
                self.context.record('maintenance', {'stage': 'nsg', 'status': 'requesting', 'target': identity, 'properties': values})
                response = self.context.request('PUT', identity, nsg.API, body={'properties': values}, headers={'If-None-Match': '*'})
                if response.get('statusCode') not in (200, 201, 202):
                    raise OperationError('Audit NSG addition was rejected; inspect before retrying')
                # Wait for the exact child before touching the next rule.
                deadline = self.monotonic() + 300
                while self.monotonic() < deadline:
                    parents = nsg._parents(self.config, self.transport, pending=True)
                    child = next((value for value in parents[group]['properties']['securityRules'] if value['name'] == name), None)
                    if child and child['properties'].get('provisioningState') == 'Succeeded' and nsg._properties(child) == rule:
                        break
                    self.sleep(5)
                else:
                    raise OperationError('Audit NSG readback remains pending')
        nsg.verify_fixed_nsg_inventory(nsg._parents(self.config, self.transport), bindings, require_complete=True)
        dns = self.deploy('dns', 'nw', {'tags': tags, 'auditStorage': plan['auditStorage'], 'privateEndpoints': endpoints['privateEndpoints']})
        for collector, key in zip(COLLECTORS, ('auditStorageJapan', 'auditStorageFoundry')):
            expected, actual = bindings['ipSets'][key], dns['recordPrivateIps'][collector]
            if not actual or len(actual) != len(set(actual)) or set(actual) != set(expected):
                raise OperationError('Blob DNS records do not match every verified PE address; the link was not enabled')
        link = self.deploy('link', 'nw', {'resourcePrefix': stem, 'tags': tags, 'vnetId': vnet,
                         'privateDnsZoneId': dns['privateDnsZoneId'], 'dnsRecordsValidated': True})
        roles = self.deploy('roles', None, {'resourceStem': stem})
        metadata = management_maintenance_inputs(self.config, roles)
        inventory = self.client_inventory(plan)
        self.deploy('client', 'maint', {'vmName': self.context.state['outputs']['managementVm']['name'],
                                     'location': self.config['location'], 'tags': tags, 'inventory': inventory})
        state = copy.deepcopy(self.context.state)
        state['outputs']['managementMaintenance'] = metadata
        state['outputs']['managementClientInventory'] = inventory
        state['outputs']['auditStoragePrivateAccess'] = {
            'privateEndpoints': endpoints['privateEndpoints'], 'privateDnsZoneId': dns['privateDnsZoneId'],
            'privateDnsZoneGroupIds': dns['privateDnsZoneGroupIds'], 'privateDnsLinkId': link['privateDnsLinkId']}
        # Never replace an intervening deployment's Core state.
        self._state_current()
        self.context.save_private(self.context.state_path, state, overwrite=True)
        self.context.record('maintenance', {'status': 'assets-applied', 'runtimeVerified': False})
        return {'status': 'assets-applied', 'runtimeVerified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        context = OperationContext.load(args.config, runtime_root=args.runtime_root)
        operation = MaintenanceUpgrade(context)
        with context.locked():
            result = operation.apply() if args.execute else operation.preflight()
        print(json.dumps({'status': result['status'], 'runtimeVerified': False}))
        return 0
    except (OperationError, nsg.FixedNsgError, ValueError, OSError, KeyError, TypeError):
        print('Maintenance preparation stopped; inspect protected records and current resources.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
