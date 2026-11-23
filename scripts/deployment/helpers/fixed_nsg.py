"""Migrate owned NSG children to the fixed Bicep set without a permit gap.

Read-only discovery supplies every address. Legacy child priorities move in
descending order, preserving their relative order and conditions; terminal
denies remain ahead of Azure defaults. Only after all new tuples are verified
may the exact legacy children be deleted. NSG parents are never PUT or flushed.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import math
from pathlib import Path
import re
import time
from uuid import UUID

from .apim_naming import apim_name


API = '2025-01-01'
ROOT = Path(__file__).resolve().parents[3]
NSG_TYPE = 'Microsoft.Network/networkSecurityGroups'
GROUPS = ('agw', 'apim', 'pep', 'management', 'aca')
SUFFIXES = {'agw': 'agw', 'apim': 'apim', 'pep': 'pep', 'management': 'vm', 'aca': 'aca'}
_PAIRS = (('sourcePortRange', 'sourcePortRanges'), ('destinationPortRange', 'destinationPortRanges'),
          ('sourceAddressPrefix', 'sourceAddressPrefixes'), ('destinationAddressPrefix', 'destinationAddressPrefixes'))


class FixedNsgError(RuntimeError):
    """Stop without including live configuration, ARM bodies or response URLs."""


def _catalog(name):
    try:
        value = json.loads((ROOT / 'infra' / name).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('groups'), dict): raise ValueError()
        return value
    except (OSError, ValueError):
        raise FixedNsgError('The fixed NSG manifest is invalid') from None


def _scope(config):
    try:
        if not isinstance(config['subscriptionId'], str): raise ValueError()
        UUID(config['subscriptionId'])
        stem = config['resourceStem']
        for value in (stem, *config['resourceGroupNames'].values()):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._()-]{0,79}', value): raise ValueError()
        if any(not isinstance(config[k], str) or not config[k] for k in ('productName', 'environmentName', 'location')):
            raise ValueError()
        prefixes = {key: str(ipaddress.ip_network(config['network']['subnetPrefixes'][key], strict=True))
                    for key in (*GROUPS, 'firewall')}
        if any(ipaddress.ip_network(v).version != 4 for v in prefixes.values()): raise ValueError()
        expected = {key: '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames'][key]
                    for key in ('nw', 'svc', 'maint', 'monitor')}
        apim_name(config)
        return expected, stem, prefixes
    except (AttributeError, KeyError, TypeError, ValueError):
        raise FixedNsgError('Fixed NSG migration requires a verified deployment scope') from None


def _request(transport, method, identity, *, api=API, body=None, headers=None):
    if method not in ('GET', 'PUT', 'DELETE'): raise FixedNsgError('Unexpected NSG migration method')
    try:
        result = transport.request(method, identity + '?api-version=' + api, body=body, headers=headers)
    except Exception:
        raise FixedNsgError('NSG migration response is unknown; inspect protected state before retrying') from None
    allowed = (200, 404) if method == 'GET' else (200, 201, 202) if method == 'PUT' else (200, 202, 204, 404)
    if not isinstance(result, dict) or type(result.get('statusCode')) is not int or result['statusCode'] not in allowed:
        raise FixedNsgError('NSG migration could not verify the ARM response')
    return result


def _identity(body, identity, kind, config, *, location=None, tags=True, state=True, pending=False):
    expected_tags = {'product': config['productName'], 'environment': config['environmentName'],
                     'azd-env-name': config['environmentName']}
    if (not isinstance(body, dict) or str(body.get('id', '')).casefold() != identity.casefold()
            or str(body.get('name', '')).casefold() != identity.rsplit('/', 1)[-1].casefold()
            or str(body.get('type', '')).casefold() != kind.casefold()
            or not isinstance(body.get('location'), str)
            or body['location'].replace(' ', '').casefold() != (location or config['location']).replace(' ', '').casefold()
            or not isinstance(body.get('properties'), dict)
            or tags and (not isinstance(body.get('tags'), dict)
                         or any(body['tags'].get(k) != v for k, v in expected_tags.items()))):
        raise FixedNsgError('NSG migration resource ownership, identity or location is unverified')
    if state and body['properties'].get('provisioningState') not in (('Succeeded', 'Updating') if pending else ('Succeeded',)):
        raise FixedNsgError('NSG migration resource has an unresolved operation')
    return body


def _read(config, transport, identity, kind, *, required=True, api=API, **options):
    response = _request(transport, 'GET', identity, api=api)
    if response['statusCode'] == 404:
        if required: raise FixedNsgError('A required owned network resource is unavailable')
        return None
    return _identity(response.get('body'), identity, kind, config, **options)


def _ref(value):
    return str(value.get('id', '')).casefold() if isinstance(value, dict) else ''


def _address(value, prefix=None):
    try:
        ip = ipaddress.ip_address(value)
        if ip.version != 4 or prefix and ip not in ipaddress.ip_network(prefix): raise ValueError()
        return str(ip)
    except (TypeError, ValueError):
        raise FixedNsgError('An owned resource address is outside its expected subnet') from None


def _properties(rule):
    if not isinstance(rule, dict) or not isinstance(rule.get('properties'), dict):
        raise FixedNsgError('NSG child conditions are unavailable')
    p = copy.deepcopy(rule['properties'])
    p.pop('provisioningState', None)
    description = p.pop('description', None)
    if description is not None and (not isinstance(description, str) or len(description) > 140):
        raise FixedNsgError('NSG child description is malformed')
    for one, many in _PAIRS:
        if p.get(many) == []: p.pop(many)
        if one in p and p[one] in (None, ''): p.pop(one)
        if (one in p) == (many in p): raise FixedNsgError('NSG child conditions are ambiguous')
        values = p.pop(many) if many in p else [p.pop(one)]
        if (not isinstance(values, list) or not values
                or any(not isinstance(v, str) or not v for v in values) or len(values) != len(set(values))):
            raise FixedNsgError('NSG child address or port set is malformed')
        p[one] = sorted(values)
    if isinstance(p.get('protocol'), str): p['protocol'] = p['protocol'].capitalize()
    expected = {'priority', 'direction', 'access', 'protocol', *[one for one, _ in _PAIRS]}
    if (set(p) != expected or p['direction'] not in ('Inbound', 'Outbound') or p['access'] not in ('Allow', 'Deny')
            or p['protocol'] not in ('Tcp', 'Udp', '*') or type(p['priority']) is not int):
        raise FixedNsgError('NSG child has unsupported or incomplete conditions')
    return p


def _inventory(parent):
    if parent is None: return {}
    if parent['properties'].get('flushConnection', False) is not False:
        raise FixedNsgError('NSG connection flushing is not permitted')
    rules = parent['properties'].get('securityRules')
    if not isinstance(rules, list): raise FixedNsgError('NSG child inventory is unavailable')
    result = {}; priorities = set()
    for rule in rules:
        name = rule.get('name') if isinstance(rule, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', name) or name.casefold() in result:
            raise FixedNsgError('NSG child identities are ambiguous')
        expected_id = parent['id'] + '/securityRules/' + name
        if (not isinstance(rule.get('properties'), dict)
                or str(rule.get('id', '')).casefold() != expected_id.casefold()
                or str(rule.get('type', '')).casefold() != (NSG_TYPE + '/securityRules').casefold()
                or rule['properties'].get('provisioningState') != 'Succeeded'):
            raise FixedNsgError('NSG child identity or completion state is unverified')
        p = _properties(rule); identity = p['direction'], p['priority']
        if not 100 <= p['priority'] <= 4096 or identity in priorities:
            raise FixedNsgError('NSG child priorities are ambiguous')
        priorities.add(identity); result[name.casefold()] = rule
    return result


def _defaults(parent):
    if parent is None: return
    defaults = parent['properties'].get('defaultSecurityRules')
    if not isinstance(defaults, list): raise FixedNsgError('Azure NSG default rules are unavailable')
    expected = (
        ('AllowVnetInBound', 'Inbound', 65000, 'Allow', 'VirtualNetwork', 'VirtualNetwork'),
        ('AllowAzureLoadBalancerInBound', 'Inbound', 65001, 'Allow', 'AzureLoadBalancer', '*'),
        ('DenyAllInBound', 'Inbound', 65500, 'Deny', '*', '*'),
        ('AllowVnetOutBound', 'Outbound', 65000, 'Allow', 'VirtualNetwork', 'VirtualNetwork'),
        ('AllowInternetOutBound', 'Outbound', 65001, 'Allow', '*', 'Internet'),
        ('DenyAllOutBound', 'Outbound', 65500, 'Deny', '*', '*'))
    if len(defaults) != len(expected): raise FixedNsgError('Azure NSG default rule inventory differs')
    for name, direction, priority, access, source, destination in expected:
        match = [r for r in defaults if isinstance(r, dict) and str(r.get('name', '')).casefold() == name.casefold()]
        properties = {'priority': priority, 'direction': direction, 'access': access, 'protocol': '*',
                      'sourcePortRange': ['*'], 'destinationPortRange': ['*'],
                      'sourceAddressPrefix': [source], 'destinationAddressPrefix': [destination]}
        if (len(match) != 1 or _properties(match[0]) != properties or _ref(match[0])
                != (parent['id'] + '/defaultSecurityRules/' + name).casefold()):
            raise FixedNsgError('Azure NSG default rules cannot be verified')


def _parents(config, transport, *, pending=False):
    groups, stem, _ = _scope(config)
    parents = {}
    for key in GROUPS:
        identity = groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-' + SUFFIXES[key]
        parents[key] = _read(config, transport, identity, NSG_TYPE, required=False, pending=pending)
        if not pending:
            _inventory(parents[key]); _defaults(parents[key])
    return parents


def _verify_group(config, transport, identity):
    response = _request(transport, 'GET', identity, api='2024-03-01'); body = response.get('body')
    if (response['statusCode'] != 200 or not isinstance(body, dict) or _ref(body) != identity.casefold()
            or not isinstance(body.get('tags'), dict)
            or any(body['tags'].get(k) != config[v] for k, v in (('product', 'productName'), ('environment', 'environmentName')))):
        raise FixedNsgError('Network resource group ownership is unverified')


def read_fixed_nsg_bindings(config, transport, *, permit_unassociated_aca=False, permit_pending_audit_endpoints=False,
                            _required_bindings=None):
    """Read and verify resource identities, subnet links, PE targets and all NIC IPs."""
    if type(permit_unassociated_aca) is not bool or type(permit_pending_audit_endpoints) is not bool:
        raise FixedNsgError('The internal ACA association phase is invalid')
    allowed_bindings = {('ips', key) for key in ('firewallPublic', 'appGatewayPublic', 'apim', 'aca', 'management')}
    allowed_bindings.update(('ipSets', key) for key in
                            ('foundry', 'keyVault', 'registry', 'sql', 'auditStorageJapan', 'auditStorageFoundry'))
    if (_required_bindings is not None and (not isinstance(_required_bindings, frozenset)
                                           or not _required_bindings <= allowed_bindings)):
        raise FixedNsgError('The internal partial-build binding inventory is invalid')
    def required(kind, key):
        return _required_bindings is None or (kind, key) in _required_bindings
    groups, stem, prefixes = _scope(config)
    for identity in groups.values(): _verify_group(config, transport, identity)
    nw = groups['nw'] + '/providers/Microsoft.Network/'
    vnet_id = nw + 'virtualNetworks/vnet-' + stem
    vnet = _read(config, transport, vnet_id, 'Microsoft.Network/virtualNetworks')
    dhcp = vnet['properties'].get('dhcpOptions', {})
    if not isinstance(dhcp, dict) or dhcp.get('dnsServers') not in (None, []):
        raise FixedNsgError('Fixed initial NSG rules require live Azure-provided VNet DNS')
    policy = _read(config, transport, nw + 'firewallPolicies/afwp-' + stem, 'Microsoft.Network/firewallPolicies')
    dns = policy['properties'].get('dnsSettings', {})
    if (not isinstance(dns, dict) or dns.get('servers') not in (None, []) or dns.get('enableProxy') is not True):
        raise FixedNsgError('Fixed initial NSG rules require live Azure-provided Firewall DNS and its proxy')
    raw_subnets = vnet['properties'].get('subnets')
    if not isinstance(raw_subnets, list) or len(raw_subnets) != 6:
        raise FixedNsgError('The owned VNet subnet inventory differs')
    subnet_ids = {}
    parents = _parents(config, transport)
    for key in (*GROUPS, 'firewall'):
        name = 'AzureFirewallSubnet' if key == 'firewall' else 'snet-' + stem + '-' + SUFFIXES[key]
        identity = vnet_id + '/subnets/' + name
        matches = [s for s in raw_subnets if isinstance(s, dict) and str(s.get('name', '')).casefold() == name.casefold()]
        if len(matches) != 1 or _ref(matches[0]) != identity.casefold(): raise FixedNsgError('The owned subnet identity differs')
        p = matches[0].get('properties')
        if not isinstance(p, dict): raise FixedNsgError('The owned subnet properties are unavailable')
        address_prefixes = p.get('addressPrefixes') or ([p['addressPrefix']] if p.get('addressPrefix') else [])
        if address_prefixes != [prefixes[key]]: raise FixedNsgError('The owned subnet CIDR differs')
        if key == 'pep' and p.get('privateEndpointNetworkPolicies') != 'NetworkSecurityGroupEnabled':
            raise FixedNsgError('The initial Private Endpoint subnet NSG enforcement policy differs')
        associated = _ref(p.get('networkSecurityGroup'))
        if key == 'firewall':
            if associated: raise FixedNsgError('AzureFirewallSubnet must not have a customer NSG')
        else:
            parent = parents[key]
            transitional = (key == 'aca' and permit_unassociated_aca and parent is not None
                            and not associated and parent['properties'].get('subnets') in (None, []))
            if key == 'aca' and parent is None and not associated: pass
            elif transitional: pass
            elif parent is None or associated != parent['id'].casefold(): raise FixedNsgError('The owned subnet NSG association differs')
            if parent:
                references = parent['properties'].get('subnets')
                if (not transitional and (not isinstance(references, list) or len(references) != 1 or _ref(references[0]) != identity.casefold())
                        or parent['properties'].get('networkInterfaces') not in (None, [])):
                    raise FixedNsgError('The owned NSG is associated outside its subnet')
        subnet_ids[key] = identity
    ips = {}
    for key, name in (('firewallPublic', 'pip-afw-'), ('appGatewayPublic', 'pip-agw-')):
        if not required('ips', key): continue
        public = _read(config, transport, nw + 'publicIPAddresses/' + name + stem, 'Microsoft.Network/publicIPAddresses')
        if public['properties'].get('publicIPAllocationMethod') != 'Static': raise FixedNsgError('The owned public IP allocation differs')
        ips[key] = _address(public['properties'].get('ipAddress'))
    service_prefix = groups['svc'] + '/providers/'
    if required('ips', 'apim'):
        apim_id = service_prefix + 'Microsoft.ApiManagement/service/' + apim_name(config)
        apim = _read(config, transport, apim_id, 'Microsoft.ApiManagement/service', api='2024-05-01')
        p = apim['properties']; addresses = p.get('privateIPAddresses')
        if (p.get('virtualNetworkType') != 'Internal' or not isinstance(p.get('virtualNetworkConfiguration'), dict)
                or str(p['virtualNetworkConfiguration'].get('subnetResourceId', '')).casefold() != subnet_ids['apim'].casefold()
                or not isinstance(addresses, list) or not addresses): raise FixedNsgError('The owned APIM network differs')
        checked = [_address(v, prefixes['apim']) for v in addresses]
        if len(set(checked)) != len(checked): raise FixedNsgError('The owned APIM address inventory is ambiguous')
        ips['apim'] = checked[0]
    if required('ips', 'aca'):
        environment = _read(config, transport, service_prefix + 'Microsoft.App/managedEnvironments/cae-' + stem,
                            'Microsoft.App/managedEnvironments')
        configuration = environment['properties'].get('vnetConfiguration')
        if (not isinstance(configuration, dict) or configuration.get('internal') is not True
                or str(configuration.get('infrastructureSubnetId', '')).casefold() != subnet_ids['aca'].casefold()):
            raise FixedNsgError('The owned ACA environment subnet differs')
        ips['aca'] = _address(environment['properties'].get('staticIp'), prefixes['aca'])
    if required('ips', 'management'):
        vm_id = groups['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + stem + '-mgmt'
        vm = _read(config, transport, vm_id, 'Microsoft.Compute/virtualMachines', api='2024-07-01')
        nic_id = groups['maint'] + '/providers/Microsoft.Network/networkInterfaces/nic-' + stem + '-mgmt'
        nics = vm['properties'].get('networkProfile', {}).get('networkInterfaces')
        if not isinstance(nics, list) or len(nics) != 1 or _ref(nics[0]) != nic_id.casefold():
            raise FixedNsgError('The owned management VM NIC differs')
        nic = _read(config, transport, nic_id, 'Microsoft.Network/networkInterfaces')
        configurations = nic['properties'].get('ipConfigurations')
        if (not isinstance(configurations, list) or len(configurations) != 1
                or _ref(nic['properties'].get('virtualMachine')) != vm_id.casefold()):
            raise FixedNsgError('The owned management NIC configuration differs')
        primary = configurations[0].get('properties', {})
        if _ref(primary.get('subnet')) != subnet_ids['management'].casefold() or primary.get('publicIPAddress'):
            raise FixedNsgError('The owned management NIC subnet or public exposure differs')
        ips['management'] = _address(primary.get('privateIPAddress'), prefixes['management'])
    targets = {
        'foundry': ('foundry', service_prefix+'Microsoft.CognitiveServices/accounts/aif-'+stem, ['account'], 'Microsoft.CognitiveServices/accounts', '2025-06-01'),
        'keyVault': ('kv', service_prefix+'Microsoft.KeyVault/vaults/kv-'+stem, ['vault'], 'Microsoft.KeyVault/vaults', '2024-11-01'),
        'registry': ('acr', service_prefix+'Microsoft.ContainerRegistry/registries/acr'+stem.replace('-', ''), ['registry'], 'Microsoft.ContainerRegistry/registries', '2023-07-01'),
        'sql': ('sql', service_prefix+'Microsoft.Sql/servers/sql-'+stem, ['sqlServer'], 'Microsoft.Sql/servers', '2023-08-01'),
        'auditStorageJapan': ('audit-japan-blob', groups['monitor']+'/providers/Microsoft.Storage/storageAccounts/st'+stem.replace('-', '')+'jp', ['blob'], 'Microsoft.Storage/storageAccounts', '2023-05-01'),
        'auditStorageFoundry': ('audit-foundry-blob', groups['monitor']+'/providers/Microsoft.Storage/storageAccounts/st'+stem.replace('-', '')+'us', ['blob'], 'Microsoft.Storage/storageAccounts', '2023-05-01'),
    }
    sets = {}; seen_ips = set()
    for key, (suffix, target_id, group_ids, kind, api) in targets.items():
        if not required('ipSets', key): continue
        pe_id = nw + 'privateEndpoints/pe-' + stem + '-' + suffix
        pe = _read(config, transport, pe_id, 'Microsoft.Network/privateEndpoints',
                   required=not (_required_bindings is None and permit_pending_audit_endpoints and key.startswith('auditStorage')))
        if pe is None:
            # Core preparation precedes creation of the two audit endpoints.
            # No missing binding is supplied to a rule or completion check.
            continue
        _read(config, transport, target_id, kind, api=api, state=False,
              location=config.get('foundryLocation') if key in ('foundry', 'auditStorageFoundry') else None)
        p = pe['properties']; connections = p.get('privateLinkServiceConnections')
        if (p.get('manualPrivateLinkServiceConnections', []) not in ([], None)
                or _ref(p.get('subnet')) != subnet_ids['pep'].casefold()
                or not isinstance(connections, list) or len(connections) != 1):
            raise FixedNsgError('The owned Private Endpoint configuration differs')
        connection = connections[0].get('properties', {})
        if (str(connection.get('privateLinkServiceId', '')).casefold() != target_id.casefold()
                or connection.get('groupIds') != group_ids
                or connection.get('privateLinkServiceConnectionState', {}).get('status') != 'Approved'):
            raise FixedNsgError('The owned Private Endpoint service connection is unverified')
        references = p.get('networkInterfaces'); addresses = []; seen_nics = set()
        if not isinstance(references, list) or not references: raise FixedNsgError('The owned endpoint NIC inventory is unavailable')
        for reference in references:
            identity = reference.get('id') if isinstance(reference, dict) else None
            nic_prefix = nw + 'networkInterfaces/'
            if (not isinstance(identity, str) or not identity.casefold().startswith(nic_prefix.casefold())
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._()-]{0,79}', identity[len(nic_prefix):])
                    or identity.casefold() in seen_nics): raise FixedNsgError('The owned endpoint NIC identity differs')
            seen_nics.add(identity.casefold())
            nic = _read(config, transport, identity, 'Microsoft.Network/networkInterfaces', tags=False)
            if _ref(nic['properties'].get('privateEndpoint')) != pe_id.casefold(): raise FixedNsgError('The endpoint NIC backlink differs')
            entries = nic['properties'].get('ipConfigurations')
            if not isinstance(entries, list) or not entries: raise FixedNsgError('The endpoint NIC addresses are unavailable')
            for entry in entries:
                current = entry.get('properties', {})
                if _ref(current.get('subnet')) != subnet_ids['pep'].casefold(): raise FixedNsgError('The endpoint NIC subnet differs')
                address = _address(current.get('privateIPAddress'), prefixes['pep'])
                if address in seen_ips: raise FixedNsgError('The endpoint IP inventory is ambiguous')
                seen_ips.add(address); addresses.append(address)
        sets[key] = sorted(addresses)
    return {'subnets': prefixes, 'ips': ips, 'ipSets': sets}


def _bind(tokens, bindings):
    result = []
    for token in tokens:
        kind, value = token.split(':', 1)
        if kind == 'subnet': result.append(bindings['subnets'][value])
        elif kind in ('ip', 'address'): result.append(bindings['ips'][value] + ('/32' if kind == 'ip' else ''))
        elif kind in ('ips', 'addresses'): result.extend(ip + ('/32' if kind == 'ips' else '') for ip in bindings['ipSets'][value])
        elif kind in ('literal', 'tag'): result.append(value)
        else: raise FixedNsgError('The fixed manifest has an unsupported address token')
    return sorted(set(result))


def _definition(rule, bindings):
    try:
        return {'priority': rule['priority'], 'direction': rule['direction'], 'access': rule['access'], 'protocol': rule['protocol'],
                'sourcePortRange': [rule['sourcePort']], 'destinationPortRange': sorted(rule['destinationPorts']),
                'sourceAddressPrefix': _bind(rule['source'], bindings), 'destinationAddressPrefix': _bind(rule['destination'], bindings)}
    except (KeyError, TypeError, ValueError):
        raise FixedNsgError('The fixed manifest cannot bind to owned resource addresses') from None


def _temporary_priorities(group):
    result = {}
    for direction in ('Inbound', 'Outbound'):
        rules = sorted(((name, r) for name, r in group['rules'].items() if r['direction'] == direction),
                       key=lambda pair: (pair[1]['priority'], pair[0]))
        for index, (name, rule) in enumerate(rules):
            result[name] = 4080 if rule['priority'] == 4096 else 1000 + index
    return result


def _classify(parents, bindings, *, require_complete=False):
    fixed = _catalog('nsg-rule-priorities.json'); legacy = _catalog('nsg-legacy-rule-definitions.json')
    sql_retirement = _catalog('sql-proxy-nsg-retirement.json')
    plan = []
    for key in GROUPS:
        current = _inventory(parents[key]); desired = fixed['groups'][key]['rules']
        older = legacy['groups'].get(key, {'rules': {}}); temporary = _temporary_priorities(older)
        for name, child in current.items():
            actual = _properties(child)
            if name in desired and actual == _definition(desired[name], bindings): continue
            old = older['rules'].get(name)
            temporary_priority = temporary.get(name)
            if old is None:
                old = sql_retirement['groups'].get(key, {'rules': {}})['rules'].get(name)
                temporary_priority = old['temporaryPriority'] if old is not None else None
            if old is None: raise FixedNsgError('An NSG has an unknown, manually modified or conflicting child rule')
            candidates = [old] + [{**old, **alternative} for alternative in old.get('alternatives', [])]
            matched = next((r for r in candidates if actual in (_definition(r, bindings), {**_definition(r, bindings), 'priority': temporary_priority})), None)
            if matched is None: raise FixedNsgError('An NSG child differs from the exact owned legacy definition')
            plan.append({'group': key, 'name': name, 'child': child, 'originalPriority': old['priority'],
                          'temporaryPriority': temporary_priority, 'properties': actual})
        if require_complete:
            if parents[key] is None or any(name not in current or _properties(current[name]) != _definition(rule, bindings) for name, rule in desired.items()):
                raise FixedNsgError('The complete fixed NSG rule set has not been deployed and verified')
    return sorted(plan, key=lambda row: (GROUPS.index(row['group']), row['properties']['direction'], -row['originalPriority'], row['name']))


def _snapshot(parent):
    if parent is None: return None
    value = copy.deepcopy(parent)
    for key in ('etag', 'systemData'): value.pop(key, None)
    p = value['properties']; p.pop('provisioningState', None); p.setdefault('flushConnection', False)
    for field in ('securityRules', 'defaultSecurityRules'):
        for child in p.get(field, []):
            child.pop('etag', None); child['properties'].pop('provisioningState', None)
        if field in p: p[field].sort(key=lambda child: child['name'].casefold())
    return value


def _etag(value):
    tag = value.get('etag') if isinstance(value, dict) else None
    if (not isinstance(tag, str) or len(tag) > 512
            or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', tag)
            or tag in ('*', '"*"', 'W/"*"')):
        raise FixedNsgError('A concrete owned NSG child ETag is required')
    return tag


def fixed_nsg_upgrade_parameters(config, transport):
    """Supply the NSG-only Bicep from owned live addresses; no public switches."""
    groups, stem, _ = _scope(config)
    bindings = read_fixed_nsg_bindings(config, transport, permit_unassociated_aca=True)
    parents = _parents(config, transport)
    if any(parents[key] is None for key in GROUPS if key != 'aca'):
        raise FixedNsgError('The NSG-only upgrade requires all four existing base NSGs')
    _classify(parents, bindings)
    return {'resourcePrefix': stem, 'location': config['location'],
            'tags': _initial_nsg_tags(config),
            'bindings': bindings, 'existingNetworkSecurityGroupNames': [
                'nsg-' + stem + '-' + SUFFIXES[key] for key in GROUPS if parents[key] is not None]}


def _initial_nsg_tags(config):
    extra = config.get('tags', {})
    if not isinstance(extra, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in extra.items()):
        raise FixedNsgError('Initial NSG construction tags are invalid')
    return {**copy.deepcopy(extra), 'product': config['productName'], 'environment': config['environmentName'],
            'azd-env-name': config['environmentName']}


def verify_fixed_nsg_upgrade_what_if(config, document, bindings):
    """Require every fixed child to expand and reject writes outside this set."""
    groups, stem, _ = _scope(config)
    # The REST operation result puts changes under properties; the CLI/SDK
    # also exposes a flattened form. Never resolve conflicting result lists.
    if isinstance(document, dict):
        document = copy.deepcopy(document)
        properties = document.get('properties')
        if isinstance(properties, dict):
            if (properties.get('error') is not None or properties.get('diagnostics') not in (None, [])
                    or properties.get('potentialChanges') not in (None, [])):
                raise FixedNsgError('NSG-only what-if contains nested expansion diagnostics')
            if 'changes' in properties:
                if 'changes' in document and document['changes'] != properties['changes']:
                    raise FixedNsgError('NSG-only what-if contains conflicting change lists')
                document['changes'] = properties['changes']
        document.setdefault('diagnostics', [])
    if (not isinstance(document, dict) or document.get('status') != 'Succeeded'
            or document.get('error') is not None or document.get('diagnostics') != []
            or document.get('potentialChanges') not in (None, []) or not isinstance(document.get('changes'), list)):
        raise FixedNsgError('NSG-only what-if is incomplete or contains expansion diagnostics')
    catalog = _catalog('nsg-rule-priorities.json')
    expected = {groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-' + SUFFIXES[key]
                + '/securityRules/' + name: _definition(rule, bindings)
                for key in GROUPS for name, rule in catalog['groups'][key]['rules'].items()}
    if len(expected) != 76: raise FixedNsgError('The NSG-only manifest must contain exactly 76 children')
    expected = {identity.casefold(): value for identity, value in expected.items()}
    aca = (groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-aca').casefold()
    observed = set()
    seen_resources = set()
    for change in document['changes']:
        if not isinstance(change, dict) or not isinstance(change.get('resourceId'), str):
            raise FixedNsgError('NSG-only what-if resource identity is unavailable')
        identity, action = change['resourceId'].casefold(), change.get('changeType')
        if identity in seen_resources: raise FixedNsgError('NSG-only what-if resource identity is duplicated')
        seen_resources.add(identity)
        if identity in expected:
            if identity in observed or action not in ('Create', 'Modify', 'NoChange'):
                raise FixedNsgError('A fixed NSG child was duplicated, ignored or deleted by what-if')
            body = change.get('after')
            if action == 'NoChange' and body is None:
                body = change.get('before')
            if not isinstance(body, dict):
                raise FixedNsgError('A fixed NSG child has no predicted post-deployment state')
            if _properties(body) != expected[identity]:
                raise FixedNsgError('NSG-only what-if differs from the exact fixed child tuple')
            observed.add(identity)
        elif identity == aca and action == 'Create':
            after = change.get('after')
            if (change.get('before') is not None or not isinstance(after, dict)
                    or not isinstance(after.get('properties'), dict) or not isinstance(after.get('location'), str)
                    or not isinstance(after.get('properties', {}).get('securityRules'), list)
                    or set(after.get('properties', {})) - {'securityRules'}
                    or after.get('location', '').replace(' ', '').casefold() != config['location'].replace(' ', '').casefold()
                    or after.get('tags') != _initial_nsg_tags(config)):
                raise FixedNsgError('The new ACA NSG parent is outside the fixed creation contract')
            # FullResourcePayloads merges the separately declared ACA children
            # into the new parent's predicted state. They must still be the
            # exact fixed ACA set; the actual parent-create template is empty.
            children = after['properties']['securityRules']
            if children:
                aca_expected = {name.casefold(): _definition(rule, bindings)
                                for name, rule in catalog['groups']['aca']['rules'].items()}
                if any(not isinstance(child, dict) or not isinstance(child.get('name'), str) for child in children):
                    raise FixedNsgError('The predicted ACA parent child identities are invalid')
                names = [child['name'].casefold() for child in children]
                if (len(names) != len(children) or len(names) != len(set(names)) or set(names) != set(aca_expected)
                        or any(_properties(child) != aca_expected[child['name'].casefold()] for child in children)):
                    raise FixedNsgError('The predicted ACA parent children differ from the fixed set')
        elif action not in ('Ignore', 'NoChange'):
            raise FixedNsgError('NSG-only what-if would write an unrelated resource or an existing parent')
    if observed != set(expected): raise FixedNsgError('NSG-only what-if did not expand all fixed children')
    return {'fixedChildrenVerified': 76, 'expansionDiagnostics': 0, 'unrelatedWrites': 0}


def _subnet_reference(value):
    if (not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']
            or set(value) - {'id', 'name', 'type', 'etag', 'resourceGroup'}):
        raise FixedNsgError('A subnet reference contains unsupported writable values')
    return {'id': value['id']}


def _writable_aca_subnet(body, identity):
    """2025-01-01 documented read-only fields only; unknown writable input stops."""
    if (not isinstance(body, dict) or _ref(body) != identity.casefold()
            or body.get('name') != identity.rsplit('/', 1)[-1]
            or str(body.get('type', '')).casefold() != 'microsoft.network/virtualnetworks/subnets'
            or not isinstance(body.get('properties'), dict)
            or body['properties'].get('provisioningState') != 'Succeeded'):
        raise FixedNsgError('The ACA subnet identity or state is unverified')
    p = copy.deepcopy(body['properties'])
    for field in ('privateEndpoints', 'ipConfigurations', 'ipConfigurationProfiles', 'resourceNavigationLinks',
                  'serviceAssociationLinks', 'purpose', 'provisioningState'):
        p.pop(field, None)
    allowed = {'addressPrefix', 'addressPrefixes', 'networkSecurityGroup', 'routeTable', 'natGateway',
               'serviceEndpoints', 'serviceEndpointPolicies', 'ipAllocations', 'delegations',
               'privateEndpointNetworkPolicies', 'privateLinkServiceNetworkPolicies', 'sharingScope', 'defaultOutboundAccess'}
    if set(p) - allowed:
        raise FixedNsgError('ACA subnet contains an unsupported writable property; no value was omitted')
    for field in ('networkSecurityGroup', 'routeTable', 'natGateway'):
        if field in p and p[field] is not None: p[field] = _subnet_reference(p[field])
    for field in ('serviceEndpointPolicies', 'ipAllocations'):
        if field in p:
            if not isinstance(p[field], list): raise FixedNsgError('ACA subnet reference inventory is invalid')
            p[field] = [_subnet_reference(value) for value in p[field]]
    for field in ('delegations', 'serviceEndpoints'):
        if field not in p: continue
        if not isinstance(p[field], list): raise FixedNsgError('ACA subnet service inventory is invalid')
        cleaned = []
        for value in p[field]:
            if not isinstance(value, dict): raise FixedNsgError('ACA subnet service properties are invalid')
            if field == 'delegations':
                if set(value) - {'id', 'name', 'type', 'etag', 'properties'} or not isinstance(value.get('name'), str):
                    raise FixedNsgError('ACA subnet delegation contains unsupported writable values')
                properties = copy.deepcopy(value.get('properties', {}))
                if not isinstance(properties, dict): raise FixedNsgError('ACA subnet delegation is invalid')
                properties.pop('actions', None); properties.pop('provisioningState', None)
                if set(properties) != {'serviceName'} or not isinstance(properties['serviceName'], str):
                    raise FixedNsgError('ACA subnet delegation properties cannot be preserved')
                cleaned.append({'name': value['name'], 'properties': properties})
            else:
                value.pop('provisioningState', None)
                if set(value) - {'service', 'locations', 'networkIdentifier'}:
                    raise FixedNsgError('ACA subnet service endpoint contains unsupported writable values')
                if 'networkIdentifier' in value: value['networkIdentifier'] = _subnet_reference(value['networkIdentifier'])
                cleaned.append(value)
        p[field] = cleaned
    return p


def associate_fixed_aca_nsg(config, transport, *, bindings, execute=False, record=None,
                            max_checks=30, wait_seconds=2, sleep=time.sleep):
    """After all fixed children exist, change only the owned ACA subnet NSG link."""
    if (execute and not callable(record)) or (record is not None and not callable(record)):
        raise FixedNsgError('ACA NSG association requires a protected per-attempt journal')
    _limits(max_checks, wait_seconds)
    groups, stem, _ = _scope(config)
    if read_fixed_nsg_bindings(config, transport, permit_unassociated_aca=True) != bindings:
        raise FixedNsgError('Owned resource addresses changed before ACA NSG association')
    _classify(_parents(config, transport), bindings, require_complete=True)
    identity = groups['nw'] + '/providers/Microsoft.Network/virtualNetworks/vnet-' + stem + '/subnets/snet-' + stem + '-aca'
    target = groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-aca'
    before = _request(transport, 'GET', identity).get('body')
    properties = _writable_aca_subnet(before, identity)
    existing = _ref(properties.get('networkSecurityGroup'))
    if existing and existing != target.casefold(): raise FixedNsgError('ACA subnet has a different NSG owner')
    if existing: return {'associationRequired': False, 'associationVerified': True}
    desired = {**properties, 'networkSecurityGroup': {'id': target}}
    plan = {'subnetId': identity, 'propertiesBefore': properties, 'propertiesAfter': desired,
            'associationRequired': True, 'etag': _etag(before)}
    if record: record('aca-association-planned', plan)
    if not execute: return plan
    fresh = _request(transport, 'GET', identity).get('body')
    if _writable_aca_subnet(fresh, identity) != properties or _etag(fresh) != plan['etag']:
        raise FixedNsgError('ACA subnet changed before its conditional association')
    record('aca-association-attempt', plan)
    _request(transport, 'PUT', identity, body={'properties': desired}, headers={'If-Match': plan['etag']})
    for index in range(max_checks):
        response = _request(transport, 'GET', identity); body = response.get('body')
        state = body.get('properties', {}).get('provisioningState') if isinstance(body, dict) else None
        if state == 'Succeeded':
            if _writable_aca_subnet(body, identity) != desired:
                raise FixedNsgError('ACA subnet readback differs from the exact planned association')
            record('aca-association-verified', {'subnetId': identity, 'associationVerified': True})
            return {'associationRequired': True, 'associationVerified': True}
        if state not in ('Updating',) or index == max_checks - 1:
            raise FixedNsgError('ACA subnet association is unresolved; no additional PUT was requested')
        sleep(wait_seconds)


def _limits(max_checks, wait_seconds):
    if (type(max_checks) is not int or not 1 <= max_checks <= 120 or type(wait_seconds) not in (int, float)
            or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 60):
        raise FixedNsgError('NSG migration polling limits are invalid')


def _wait_change(config, transport, before, expected, child_id, target_properties, *, deleting, max_checks, wait_seconds, sleep):
    for attempt in range(max_checks):
        child = _request(transport, 'GET', child_id)
        parent = _read(config, transport, before['id'], NSG_TYPE, pending=True)
        if child['statusCode'] == 200:
            body = child.get('body')
            if (not isinstance(body, dict) or _ref(body) != child_id.casefold()
                    or _properties(body) not in (_properties(next(r for r in before['properties']['securityRules'] if _ref(r) == child_id.casefold())), target_properties)):
                raise FixedNsgError('The NSG child changed outside the requested migration')
        if _snapshot(parent) == _snapshot(expected) and parent['properties']['provisioningState'] == 'Succeeded':
            if deleting and child['statusCode'] == 404 or not deleting and child['statusCode'] == 200 and _properties(child['body']) == target_properties and child['body']['properties'].get('provisioningState') == 'Succeeded':
                _inventory(parent); _defaults(parent); return parent
        elif _snapshot(parent) not in (_snapshot(before), _snapshot(expected)):
            raise FixedNsgError('NSG settings changed outside the requested child')
        if attempt + 1 < max_checks: sleep(wait_seconds)
    raise FixedNsgError('NSG migration remains pending; inspect current state before retrying')


def _apply(config, transport, parents, plan, *, deleting, record, max_checks, wait_seconds, sleep, before_change=None):
    if not callable(record):
        raise FixedNsgError('NSG mutation requires a protected record callback')
    writes = 0; emit = record
    for item in plan:
        key, name = item['group'], item['name']; parent = parents[key]
        if not deleting and item['properties']['priority'] == item['temporaryPriority']: continue
        if before_change is not None: before_change()
        fresh = _read(config, transport, parent['id'], NSG_TYPE)
        if _snapshot(fresh) != _snapshot(parent) or _etag(fresh) != _etag(parent):
            raise FixedNsgError('An NSG changed before the requested child migration')
        child_id = parent['id'] + '/securityRules/' + name
        response = _request(transport, 'GET', child_id); child = response.get('body')
        if (response['statusCode'] != 200 or not isinstance(child, dict) or _ref(child) != child_id.casefold()
                or str(child.get('name', '')).casefold() != name
                or str(child.get('type', '')).casefold() != (NSG_TYPE+'/securityRules').casefold()
                or child.get('properties', {}).get('provisioningState') != 'Succeeded'
                or _properties(child) != item['properties']):
            raise FixedNsgError('The NSG parent and child inventories differ')
        matching = next(r for r in parent['properties']['securityRules'] if r['name'].casefold() == name)
        if _etag(child) != _etag(matching): raise FixedNsgError('An NSG child changed before its conditional update')
        expected = copy.deepcopy(parent)
        expected['properties']['securityRules'] = [r for r in expected['properties']['securityRules'] if r['name'].casefold() != name]
        target = {**item['properties'], 'priority': item['temporaryPriority']}
        body = None
        if not deleting:
            moved = copy.deepcopy(child); moved['properties']['priority'] = item['temporaryPriority']
            expected['properties']['securityRules'].append(moved)
            writable = copy.deepcopy(child['properties']); writable.pop('provisioningState', None)
            writable['priority'] = item['temporaryPriority']; body = {'properties': writable}
        emit('attempt', {'child': child_id, 'method': 'DELETE' if deleting else 'PUT', 'ordinal': writes + 1})
        _request(transport, 'DELETE' if deleting else 'PUT', child_id, body=body, headers={'If-Match': _etag(child)})
        writes += 1
        parents[key] = _wait_change(config, transport, parent, expected, child_id, target, deleting=deleting,
                                   max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        emit('removed' if deleting else 'staged', {'child': child_id, 'azureWrites': writes})
    fresh_parents = _parents(config, transport)
    if any(_snapshot(fresh_parents[key]) != _snapshot(parents[key]) for key in GROUPS):
        raise FixedNsgError('An NSG changed outside the verified migration')
    return writes


def prepare_fixed_nsg_rules(config, transport, *, execute=False, record=None, max_checks=30, wait_seconds=2, sleep=time.sleep):
    """Audit all subnets first, then preserve owned legacy children at temporary priorities."""
    if execute and not callable(record):
        raise FixedNsgError('NSG mutation requires a protected record callback')
    _limits(max_checks, wait_seconds)
    parents = _parents(config, transport)
    if all(not _inventory(parent) for parent in parents.values()):
        return {'status': 'new-or-empty-build', 'plannedMoves': 0, 'azureWrites': 0}
    # A first build can stop after static rules, before APIM/CAE/PE creation.
    # Exact desired static children need no priority migration or live IP read.
    _, _, prefixes = _scope(config)
    static_bindings = {'subnets': prefixes, 'ips': {}, 'ipSets': {}}
    fixed = _catalog('nsg-rule-priorities.json')
    static_only = True
    for key, parent in parents.items():
        for name, child in _inventory(parent).items():
            rule = fixed['groups'][key]['rules'].get(name)
            if (rule is None or any(token.split(':', 1)[0] not in ('subnet', 'tag', 'literal')
                                    for token in rule['source'] + rule['destination'])
                    or _properties(child) != _definition(rule, static_bindings)):
                static_only = False; break
    if static_only:
        return {'status': 'fixed-static-build', 'plannedMoves': 0, 'azureWrites': 0}
    # Initial construction can also stop after dynamic children, before the
    # later ACA/SQL/ACR stages exist. Validate every existing child's actual
    # dependencies; completion and legacy migration still require the full set.
    required_bindings = set(); fixed_names_only = True
    for key, parent in parents.items():
        for name in _inventory(parent):
            rule = fixed['groups'][key]['rules'].get(name)
            if rule is None:
                fixed_names_only = False; break
            for token in rule['source'] + rule['destination']:
                kind, value = token.split(':', 1)
                if kind in ('ip', 'address'): required_bindings.add(('ips', value))
                elif kind in ('ips', 'addresses'): required_bindings.add(('ipSets', value))
    required_bindings = frozenset(required_bindings) if fixed_names_only else None
    bindings = read_fixed_nsg_bindings(config, transport, permit_unassociated_aca=True,
                                      permit_pending_audit_endpoints=True, _required_bindings=required_bindings)
    if required_bindings is not None and any(
            _properties(child) != _definition(fixed['groups'][key]['rules'][name], bindings)
            for key, parent in parents.items() for name, child in _inventory(parent).items()):
        # A matching name alone never bypasses the legacy/modified-rule audit.
        required_bindings = None
        bindings = read_fixed_nsg_bindings(config, transport, permit_unassociated_aca=True,
                                          permit_pending_audit_endpoints=True)
    plan = _classify(parents, bindings)
    emit = record if callable(record) else (lambda event, value: None)
    emit('plan', {'operation': 'stage', 'parents': parents, 'bindings': bindings, 'targets': plan, 'execute': execute})
    writes = _apply(config, transport, parents, plan, deleting=False, record=record, max_checks=max_checks,
                    wait_seconds=wait_seconds, sleep=sleep) if execute else 0
    if execute:
        current_bindings = read_fixed_nsg_bindings(config, transport, permit_unassociated_aca=True,
                                                  permit_pending_audit_endpoints=True, _required_bindings=required_bindings)
        if current_bindings != bindings: raise FixedNsgError('Resource addresses changed during NSG migration')
        remaining = _classify(_parents(config, transport), bindings)
        if any(r['properties']['priority'] != r['temporaryPriority'] for r in remaining):
            raise FixedNsgError('Legacy NSG priorities were not fully staged')
    result = {'status': 'staged' if execute else 'planned', 'plannedMoves': sum(r['properties']['priority'] != r['temporaryPriority'] for r in plan), 'azureWrites': writes}
    emit('result', result); return result


def complete_fixed_nsg_rules(config, transport, *, execute=False, record=None, max_checks=30, wait_seconds=2, sleep=time.sleep):
    """Verify all fixed desired tuples before deleting any exact owned legacy child."""
    if execute and not callable(record):
        raise FixedNsgError('NSG mutation requires a protected record callback')
    _limits(max_checks, wait_seconds)
    parents = _parents(config, transport); bindings = read_fixed_nsg_bindings(config, transport)
    plan = _classify(parents, bindings, require_complete=True)
    emit = record if callable(record) else (lambda event, value: None)
    emit('plan', {'operation': 'complete', 'parents': parents, 'bindings': bindings, 'targets': plan, 'execute': execute})
    writes = _apply(config, transport, parents, plan, deleting=True, record=record, max_checks=max_checks,
                    wait_seconds=wait_seconds, sleep=sleep) if execute else 0
    if execute:
        if read_fixed_nsg_bindings(config, transport) != bindings: raise FixedNsgError('Resource addresses changed during NSG completion')
        if _classify(_parents(config, transport), bindings, require_complete=True): raise FixedNsgError('Owned legacy NSG children remain after completion')
    result = {'status': 'completed' if execute else 'planned', 'plannedDeletes': len(plan), 'azureWrites': writes}
    emit('result', result); return result


def _sql_mode(target_mode):
    if target_mode not in ('Redirect', 'Proxy'):
        raise FixedNsgError('The internal SQL NSG migration phase is invalid')
    return target_mode


def _sql_rows(bindings, target_mode):
    """Keep old definitions and temporary slots independent of the historic catalog."""
    _sql_mode(target_mode)
    fixed = _catalog('nsg-rule-priorities.json')
    retired = _catalog('sql-proxy-nsg-retirement.json')
    rows = []
    for group, definition in retired['groups'].items():
        for old_name, old in definition['rules'].items():
            new_name = old['replacementName']
            new = fixed['groups'][group]['rules'][new_name]
            source_name, source, target_name, target = ((old_name, old, new_name, new)
                if target_mode == 'Redirect' else (new_name, new, old_name, old))
            rows.append({'group': group, 'name': source_name, 'targetName': target_name,
                         'source': _definition(source, bindings), 'target': _definition(target, bindings),
                         'originalPriority': source['priority'], 'temporaryPriority': old['temporaryPriority']})
    if (len(rows) != 4 or {r['group'] for r in rows} != {'pep', 'management', 'aca'}
            or any(r['source']['access'] != 'Allow' or r['target']['access'] != 'Allow'
                   or r['source']['protocol'] != 'Tcp' or r['target']['protocol'] != 'Tcp'
                   or r['source']['priority'] != r['target']['priority'] for r in rows)):
        raise FixedNsgError('The fixed SQL NSG migration catalog is invalid')
    return rows


def _sql_without_rules(parents, bindings):
    names = {}
    for row in _sql_rows(bindings, 'Redirect'):
        names.setdefault(row['group'], set()).update((row['name'], row['targetName']))
    result = copy.deepcopy(parents)
    for group, parent in result.items():
        if parent is not None:
            parent['properties']['securityRules'] = [r for r in parent['properties']['securityRules']
                if r['name'].casefold() not in names.get(group, set())]
    return result


def _sql_binding_view(bindings):
    return {'subnets': bindings['subnets'], 'ips': {'management': bindings['ips']['management']},
            'ipSets': {'sql': bindings['ipSets']['sql']}}


def _sql_context(config, transport):
    bindings = read_fixed_nsg_bindings(config, transport)
    parents = _parents(config, transport)
    if any(parents[group] is None for group in GROUPS):
        raise FixedNsgError('The SQL-only migration requires all existing fixed NSGs')
    unrelated = _sql_without_rules(parents, bindings)
    if _classify(unrelated, bindings):
        raise FixedNsgError('Unrelated legacy NSG rules require their separate migration')
    fixed = _catalog('nsg-rule-priorities.json')
    excluded = {(r['group'], r['targetName']) for r in _sql_rows(bindings, 'Redirect')}
    for group in GROUPS:
        current = _inventory(unrelated[group])
        if any(name not in current for name in fixed['groups'][group]['rules']
               if (group, name) not in excluded):
            raise FixedNsgError('The unrelated fixed NSG inventory is incomplete')
    return bindings, parents


def _sql_plan(parents, bindings, target_mode, *, require_target=False, require_retired=False):
    plan = []
    for row in _sql_rows(bindings, target_mode):
        current = _inventory(parents[row['group']])
        source = current.get(row['name']); target = current.get(row['targetName'])
        if target is not None and _properties(target) != row['target']:
            raise FixedNsgError('A target SQL NSG rule differs from the fixed definition')
        if require_target and target is None:
            raise FixedNsgError('All four target SQL NSG rules must be verified before retirement')
        if require_retired and source is not None:
            raise FixedNsgError('Owned source SQL NSG rules remain after migration')
        if source is None:
            if target is None: raise FixedNsgError('A required owned SQL NSG rule is unavailable')
            continue
        actual = _properties(source)
        staged = {**row['source'], 'priority': row['temporaryPriority']}
        if actual not in (row['source'], staged):
            raise FixedNsgError('An owned SQL NSG rule was manually changed')
        for name, child in current.items():
            if name in (row['name'], row['targetName']): continue
            properties = _properties(child)
            if (properties['direction'] == row['source']['direction']
                    and properties['priority'] in (row['temporaryPriority'], row['target']['priority'])):
                raise FixedNsgError('A SQL NSG migration priority is occupied by another rule')
        plan.append({**row, 'child': source, 'properties': actual})
    return sorted(plan, key=lambda r: (GROUPS.index(r['group']), -r['originalPriority']))


def _sql_proxy_guard(config, transport, proxy_connections_verified):
    if proxy_connections_verified is not True:
        raise FixedNsgError('Proxy NSG rollback requires verified physical connection drain')
    groups, stem, _ = _scope(config)
    identity = groups['svc'] + '/providers/Microsoft.Sql/servers/sql-' + stem + '/connectionPolicies/default'
    response = _request(transport, 'GET', identity, api='2023-08-01')
    body = response.get('body')
    properties = body.get('properties') if isinstance(body, dict) else None
    if (response['statusCode'] != 200 or not isinstance(body, dict) or _ref(body) != identity.casefold()
            or not isinstance(properties, dict) or properties.get('connectionType') != 'Proxy'):
        raise FixedNsgError('SQL NSG rollback requires the current Proxy connection policy')


def _sql_baseline(baseline, bindings, parents, target_mode):
    if (not isinstance(baseline, dict) or baseline.get('targetMode') != target_mode
            or baseline.get('bindings') != bindings or not isinstance(baseline.get('parents'), dict)
            or set(baseline['parents']) != set(GROUPS)):
        raise FixedNsgError('The protected SQL NSG migration baseline differs')
    before = _sql_without_rules(baseline['parents'], bindings)
    after = _sql_without_rules(parents, bindings)
    if any(_snapshot(before[group]) != _snapshot(after[group]) for group in GROUPS):
        raise FixedNsgError('An unrelated NSG setting changed during the SQL-only migration')


def _sql_before_change(config, transport, bindings, target_mode, proxy_connections_verified):
    fresh = read_fixed_nsg_bindings(config, transport,
        _required_bindings=frozenset({('ips', 'management'), ('ipSets', 'sql')}))
    if _sql_binding_view(fresh) != _sql_binding_view(bindings):
        raise FixedNsgError('SQL PE or management addresses changed during migration')
    if target_mode == 'Proxy': _sql_proxy_guard(config, transport, proxy_connections_verified)


def prepare_sql_nsg_rules(config, transport, *, target_mode='Redirect', execute=False, record=None,
                          proxy_connections_verified=False, max_checks=30, wait_seconds=2, sleep=time.sleep):
    """Stage only the four exact SQL source children; never update NSG parents.

    Return a protected baseline for parameters/readback/retirement. Proxy is an
    internal rollback phase entered only after callers verify physical drain.
    """
    _sql_mode(target_mode); _limits(max_checks, wait_seconds)
    if type(execute) is not bool: raise FixedNsgError('SQL NSG execution mode must be an explicit boolean')
    if execute and not callable(record): raise FixedNsgError('SQL NSG mutation requires a protected record callback')
    if target_mode == 'Proxy': _sql_proxy_guard(config, transport, proxy_connections_verified)
    bindings, parents = _sql_context(config, transport)
    plan = _sql_plan(parents, bindings, target_mode)
    emit = record if callable(record) else (lambda event, value: None)
    emit('sql-nsg-plan', {'targetMode': target_mode, 'parents': parents, 'bindings': bindings, 'targets': plan})
    writes = _apply(config, transport, parents, plan, deleting=False, record=record,
        max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep,
        before_change=lambda: _sql_before_change(config, transport, bindings, target_mode,
                                                 proxy_connections_verified)) if execute else 0
    fresh_bindings, fresh_parents = _sql_context(config, transport)
    baseline = {'targetMode': target_mode, 'bindings': bindings, 'parents': parents}
    _sql_baseline(baseline, fresh_bindings, fresh_parents, target_mode)
    remaining = _sql_plan(fresh_parents, fresh_bindings, target_mode)
    if execute and any(r['properties']['priority'] != r['temporaryPriority'] for r in remaining):
        raise FixedNsgError('Owned SQL NSG source priorities were not fully staged')
    result = {'status': 'staged' if execute else 'planned', 'targetMode': target_mode,
              'plannedMoves': sum(r['properties']['priority'] != r['temporaryPriority'] for r in plan),
              'azureWrites': writes, 'bindings': fresh_bindings, 'parents': fresh_parents}
    emit('sql-nsg-result', result)
    return result


def sql_nsg_upgrade_parameters(config, transport, *, baseline, target_mode='Redirect',
                               proxy_connections_verified=False):
    """Bind the four-child Bicep only after all conflicting source priorities move."""
    _sql_mode(target_mode)
    if target_mode == 'Proxy': _sql_proxy_guard(config, transport, proxy_connections_verified)
    bindings, parents = _sql_context(config, transport)
    _sql_baseline(baseline, bindings, parents, target_mode)
    if any(r['properties']['priority'] != r['temporaryPriority']
           for r in _sql_plan(parents, bindings, target_mode)):
        raise FixedNsgError('SQL NSG source priorities must be staged before the four-child deployment')
    return {'resourcePrefix': config['resourceStem'], 'targetMode': target_mode,
            'bindings': _sql_binding_view(bindings)}


def verify_sql_nsg_rules(config, transport, *, target_mode='Redirect', baseline=None, require_retired=False):
    """Read-only four-tuple gate. It does not certify SQL connections or pool drain."""
    _sql_mode(target_mode)
    if type(require_retired) is not bool: raise FixedNsgError('SQL NSG retirement verification is invalid')
    bindings, parents = _sql_context(config, transport)
    if baseline is not None: _sql_baseline(baseline, bindings, parents, target_mode)
    plan = _sql_plan(parents, bindings, target_mode, require_target=True, require_retired=require_retired)
    return {'targetMode': target_mode, 'sqlRulesVerified': 4, 'legacySqlRules': len(plan), 'runtimeVerified': False}


def verify_sql_nsg_upgrade_what_if(config, document, bindings, *, target_mode='Redirect'):
    """Reject incomplete expansion or any predicted write outside four SQL children."""
    _sql_mode(target_mode)
    groups, stem, _ = _scope(config)
    if isinstance(document, dict):
        document = copy.deepcopy(document)
        properties = document.get('properties')
        if isinstance(properties, dict):
            if (properties.get('error') is not None or properties.get('diagnostics') not in (None, [])
                    or properties.get('potentialChanges') not in (None, [])):
                raise FixedNsgError('SQL NSG what-if has unresolved nested diagnostics')
            if 'changes' in properties:
                if 'changes' in document and document['changes'] != properties['changes']:
                    raise FixedNsgError('SQL NSG what-if has conflicting change lists')
                document['changes'] = properties['changes']
        document.setdefault('diagnostics', [])
    if (not isinstance(document, dict) or document.get('status') != 'Succeeded'
            or document.get('error') is not None or document.get('diagnostics') != []
            or document.get('potentialChanges') not in (None, []) or not isinstance(document.get('changes'), list)):
        raise FixedNsgError('SQL NSG what-if is incomplete or has unresolved diagnostics')
    expected = {(groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-' + SUFFIXES[row['group']]
                 + '/securityRules/' + row['targetName']).casefold(): row['target']
                for row in _sql_rows(bindings, target_mode)}
    seen = set(); observed = set()
    for change in document['changes']:
        if not isinstance(change, dict) or not isinstance(change.get('resourceId'), str):
            raise FixedNsgError('SQL NSG what-if resource identity is unavailable')
        identity = change['resourceId'].casefold(); action = change.get('changeType')
        if identity in seen: raise FixedNsgError('SQL NSG what-if resource identity is duplicated')
        seen.add(identity)
        if identity not in expected:
            if action not in ('NoChange', 'Ignore'):
                raise FixedNsgError('SQL NSG what-if would write an unrelated resource or NSG parent')
            continue
        if action not in ('Create', 'Modify', 'NoChange'):
            raise FixedNsgError('SQL NSG what-if ignores or deletes a target child')
        body = change.get('after')
        if action == 'NoChange' and body is None: body = change.get('before')
        if (not isinstance(body, dict) or body.get('id') is not None and _ref(body) != identity
                or body.get('type') is not None and str(body['type']).casefold() != (NSG_TYPE + '/securityRules').casefold()
                or _properties(body) != expected[identity]):
            raise FixedNsgError('SQL NSG what-if differs from an exact target tuple')
        observed.add(identity)
    if observed != set(expected): raise FixedNsgError('SQL NSG what-if did not expand all four target children')
    return {'sqlChildrenVerified': 4, 'expansionDiagnostics': 0, 'unrelatedWrites': 0}


def complete_sql_nsg_rules(config, transport, *, baseline, target_mode='Redirect', execute=False, record=None,
                           proxy_connections_verified=False, max_checks=30, wait_seconds=2, sleep=time.sleep):
    """After four target readbacks, remove only the exact four source SQL children."""
    _sql_mode(target_mode); _limits(max_checks, wait_seconds)
    if type(execute) is not bool: raise FixedNsgError('SQL NSG execution mode must be an explicit boolean')
    if execute and not callable(record): raise FixedNsgError('SQL NSG mutation requires a protected record callback')
    if target_mode == 'Proxy': _sql_proxy_guard(config, transport, proxy_connections_verified)
    bindings, parents = _sql_context(config, transport)
    _sql_baseline(baseline, bindings, parents, target_mode)
    plan = _sql_plan(parents, bindings, target_mode, require_target=True)
    emit = record if callable(record) else (lambda event, value: None)
    emit('sql-nsg-retirement-plan', {'targetMode': target_mode, 'parents': parents, 'bindings': bindings, 'targets': plan})
    writes = _apply(config, transport, parents, plan, deleting=True, record=record,
        max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep,
        before_change=lambda: _sql_before_change(config, transport, bindings, target_mode,
                                                 proxy_connections_verified)) if execute else 0
    fresh_bindings, fresh_parents = _sql_context(config, transport)
    _sql_baseline(baseline, fresh_bindings, fresh_parents, target_mode)
    _sql_plan(fresh_parents, fresh_bindings, target_mode, require_target=True, require_retired=execute)
    if target_mode == 'Proxy': _sql_proxy_guard(config, transport, proxy_connections_verified)
    result = {'status': 'completed' if execute else 'planned', 'targetMode': target_mode,
              'plannedDeletes': len(plan), 'azureWrites': writes, 'sqlRulesVerified': 4, 'runtimeVerified': False}
    emit('sql-nsg-result', result)
    return result
