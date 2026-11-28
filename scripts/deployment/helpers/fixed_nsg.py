"""Verify Core-owned NSG rules and their resource bindings during deployment."""
from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
import re
from uuid import UUID

from .aca_egress import validate_aca_route_snapshot
from .apim_naming import apim_name
from .log_privacy import ARCHIVE_COLLECTORS, audit_storage_account_name
from .subnet_names import SUBNET_NAMES, subnet_resource_id


API = '2025-01-01'
ROOT = Path(__file__).resolve().parents[3]
CATALOG_DIRECTORY = ROOT / 'infra/modules/network/nsg'
NSG_TYPE = 'Microsoft.Network/networkSecurityGroups'
GROUPS = ('agw', 'apim', 'pep', 'management', 'portal', 'broker')
SUFFIXES = {'agw': 'agw', 'apim': 'apim', 'pep': 'pep', 'management': 'vm', 'portal': 'portal', 'broker': 'broker'}
_PAIRS = (('sourcePortRange', 'sourcePortRanges'), ('destinationPortRange', 'destinationPortRanges'),
          ('sourceAddressPrefix', 'sourceAddressPrefixes'), ('destinationAddressPrefix', 'destinationAddressPrefixes'))


class FixedNsgError(RuntimeError):
    """Stop without including live configuration, ARM bodies or response URLs."""


def _catalog(name):
    try:
        value = json.loads((CATALOG_DIRECTORY / name).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('groups'), dict): raise ValueError()
        return value
    except (OSError, ValueError):
        raise FixedNsgError('The fixed NSG manifest is invalid') from None


def _active_groups(config):
    return GROUPS if 'broker' in config['network']['subnetPrefixes'] else GROUPS[:-1]


def _fixed_catalog(*, certificate=False):
    groups = _catalog('core-rules.json')['groups']
    if certificate:
        for key, rules in _catalog('certificate-rules.json')['groups'].items():
            if key not in groups or set(groups[key]['rules']) & set(rules):
                raise FixedNsgError('The certificate NSG manifest overlaps the fixed rules')
            groups[key]['rules'].update(rules)
    return groups


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
                    for key in (*_active_groups(config), 'firewall')}
        certificate = config['network']['subnetPrefixes'].get('certificate')
        if certificate is not None:
            prefix = ipaddress.ip_network(certificate, strict=True)
            if prefix.prefixlen > 27:
                raise ValueError()
            prefixes['certificate'] = str(prefix)
        if any(ipaddress.ip_network(v).version != 4 for v in prefixes.values()): raise ValueError()
        expected = {key: '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames'][key]
                    for key in ('nw', 'svc', 'maint', 'monitor')}
        apim_name(config)
        return expected, stem, prefixes
    except (AttributeError, KeyError, TypeError, ValueError):
        raise FixedNsgError('Fixed NSG validation requires a verified deployment scope') from None


def _request(transport, identity, *, api=API):
    try:
        result = transport.request('GET', identity + '?api-version=' + api)
    except Exception:
        raise FixedNsgError('Could not read the owned NSG resource') from None
    if not isinstance(result, dict) or type(result.get('statusCode')) is not int or result['statusCode'] not in (200, 404):
        raise FixedNsgError('Could not verify the NSG resource response')
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
        raise FixedNsgError('NSG validation resource ownership, identity or location is unverified')
    if state and body['properties'].get('provisioningState') not in (('Succeeded', 'Updating') if pending else ('Succeeded',)):
        raise FixedNsgError('NSG validation resource has an unresolved operation')
    return body


def _read(config, transport, identity, kind, *, required=True, api=API, **options):
    response = _request(transport, identity, api=api)
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
    for key in _active_groups(config):
        identity = groups['nw'] + '/providers/' + NSG_TYPE + '/nsg-' + stem + '-' + SUFFIXES[key]
        parents[key] = _read(config, transport, identity, NSG_TYPE, required=False, pending=pending)
        if not pending:
            _inventory(parents[key]); _defaults(parents[key])
    return parents


def _verify_group(config, transport, identity):
    response = _request(transport, identity, api='2024-03-01'); body = response.get('body')
    if (response['statusCode'] != 200 or not isinstance(body, dict) or _ref(body) != identity.casefold()
            or not isinstance(body.get('tags'), dict)
            or any(body['tags'].get(k) != config[v] for k, v in (('product', 'productName'), ('environment', 'environmentName')))):
        raise FixedNsgError('Network resource group ownership is unverified')


def _verify_certificate_egress_path(config, transport, subnet):
    groups, stem, prefixes = _scope(config)
    network = groups['nw'] + '/providers/Microsoft.Network/'
    route_table_id = network + 'routeTables/rt-' + stem + '-certificate'
    if (_ref(subnet.get('routeTable')) != route_table_id.casefold()
            or subnet.get('defaultOutboundAccess') is not False or subnet.get('natGateway')
            or subnet.get('serviceEndpoints') not in (None, [])
            or subnet.get('serviceEndpointPolicies') not in (None, [])):
        raise FixedNsgError('The ACA subnet egress boundary does not match the fixed Firewall route')
    firewall = _read(config, transport, network + 'azureFirewalls/afw-' + stem,
                     'Microsoft.Network/azureFirewalls')['properties']
    expected_subnet = subnet_resource_id(config, 'firewall')
    ip_configs = firewall.get('ipConfigurations')
    if (not isinstance(ip_configs, list) or len(ip_configs) != 1 or not isinstance(ip_configs[0], dict)
            or not isinstance(ip_configs[0].get('properties'), dict)
            or _ref(ip_configs[0]['properties'].get('subnet')) != expected_subnet.casefold()
            or _ref(firewall.get('firewallPolicy')) != (network + 'firewallPolicies/afwp-' + stem).casefold()):
        raise FixedNsgError('The ACA egress Firewall ownership and subnet are unverified')
    firewall_ip = _address(ip_configs[0]['properties'].get('privateIPAddress'), prefixes['firewall'])
    table = _read(config, transport, route_table_id, 'Microsoft.Network/routeTables')['properties']
    if table.get('disableBgpRoutePropagation') is not True:
        raise FixedNsgError('The ACA route table must disable BGP propagation')
    try:
        if not isinstance(table.get('routes'), list) or len(table['routes']) != 1:
            raise ValueError()
        validate_aca_route_snapshot(table.get('routes'), prefixes['firewall'], prefixes['agw'],
                                    firewall_ip=firewall_ip, require_default=True)
    except ValueError:
        raise FixedNsgError('The ACA route table does not prove that public traffic uses the owned Firewall') from None


def read_fixed_nsg_bindings(config, transport, *, permit_unassociated_portal=False, permit_pending_bindings=False,
                            _required_bindings=None):
    """Read and verify resource identities, subnet links, PE targets and all NIC IPs."""
    if type(permit_unassociated_portal) is not bool or type(permit_pending_bindings) is not bool:
        raise FixedNsgError('The internal NSG binding phase is invalid')
    nsg_groups = _active_groups(config)
    allowed_bindings = {('ips', key) for key in ('firewallPublic', 'appGatewayPublic', 'apim', 'portal', 'broker', 'management')}
    allowed_bindings.update(('ipSets', key) for key in
                            ('foundry', 'keyVault', 'registry', 'sql', 'auditPlatformBlob', 'auditFoundryBlob',
                             'certificateVault', 'certificateBlob'))
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
    if not isinstance(raw_subnets, list):
        raise FixedNsgError('The owned VNet subnet inventory differs')
    certificate_name = SUBNET_NAMES['certificate'].casefold()
    certificate_subnets = [subnet for subnet in raw_subnets if isinstance(subnet, dict)
                           and str(subnet.get('name', '')).casefold() == certificate_name]
    parents = _parents(config, transport)
    found = {str(subnet.get('name', '')).casefold() for subnet in raw_subnets if isinstance(subnet, dict)}
    subnet_groups = tuple(key for key in nsg_groups
        if not (permit_pending_bindings and key == 'broker'
                and SUBNET_NAMES[key].casefold() not in found
                and not _inventory(parents[key])))
    if len(certificate_subnets) > 1 or len(raw_subnets) != len(subnet_groups) + 1 + len(certificate_subnets):
        raise FixedNsgError('The owned VNet subnet inventory differs')
    if certificate_subnets:
        certificate = certificate_subnets[0]
        p = certificate.get('properties')
        certificate_nsg = nw + NSG_TYPE.removeprefix('Microsoft.Network/') + '/nsg-' + stem + '-certificate'
        certificate_route = nw + 'routeTables/rt-' + stem + '-certificate'
        if (not isinstance(p, dict) or 'certificate' not in prefixes
                or _ref(certificate) != subnet_resource_id(config, 'certificate').casefold()
                or (p.get('addressPrefixes') or [p.get('addressPrefix')]) != [prefixes['certificate']]
                or _ref(p.get('networkSecurityGroup')) != certificate_nsg.casefold()
                or _ref(p.get('routeTable')) != certificate_route.casefold()
                or p.get('defaultOutboundAccess') is not False):
            raise FixedNsgError('The dedicated certificate subnet does not match the deployment scope')
        if not permit_unassociated_portal:
            _verify_certificate_egress_path(config, transport, p)
    else:
        # The certificate subnet can be absent during initial provisioning.
        prefixes.pop('certificate', None)
    subnet_ids = {}
    for key in (*subnet_groups, 'firewall'):
        name = SUBNET_NAMES[key].casefold()
        identity = subnet_resource_id(config, key)
        matches = [s for s in raw_subnets if isinstance(s, dict) and str(s.get('name', '')).casefold() == name]
        if len(matches) != 1 or _ref(matches[0]) != identity.casefold(): raise FixedNsgError('The owned subnet identity differs')
        p = matches[0].get('properties')
        if not isinstance(p, dict): raise FixedNsgError('The owned subnet properties are unavailable')
        address_prefixes = p.get('addressPrefixes') or ([p['addressPrefix']] if p.get('addressPrefix') else [])
        if address_prefixes != [prefixes[key]]: raise FixedNsgError('The owned subnet CIDR differs')
        if key == 'pep' and p.get('privateEndpointNetworkPolicies') != 'NetworkSecurityGroupEnabled':
            raise FixedNsgError('The initial Private Endpoint subnet NSG enforcement policy differs')
        if key == 'broker' and _ref(p.get('routeTable')) != (nw + 'routeTables/rt-' + stem + '-broker').casefold():
            raise FixedNsgError('The dedicated ' + key.title() + ' subnet route table differs')
        associated = _ref(p.get('networkSecurityGroup'))
        if key == 'firewall':
            if associated: raise FixedNsgError('AzureFirewallSubnet must not have a customer NSG')
        else:
            parent = parents[key]
            transitional = (key == 'portal' and permit_unassociated_portal and parent is not None
                            and not associated and parent['properties'].get('subnets') in (None, []))
            if key == 'portal' and parent is None and not associated: pass
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
    environments = [(key, '' if key == 'portal' else '-' + key)
                    for key in ('portal', 'broker') if key in subnet_groups]
    for key, suffix in environments:
        if not required('ips', key): continue
        environment = _read(config, transport, service_prefix + 'Microsoft.App/managedEnvironments/cae-' + stem + suffix,
                            'Microsoft.App/managedEnvironments')
        configuration = environment['properties'].get('vnetConfiguration')
        if (not isinstance(configuration, dict) or configuration.get('internal') is not True
                or str(configuration.get('infrastructureSubnetId', '')).casefold() != subnet_ids[key].casefold()):
            raise FixedNsgError('The owned ACA environment subnet differs')
        if key == 'broker' and environment['properties'].get('publicNetworkAccess') != 'Disabled':
            raise FixedNsgError('The Broker ACA environment does not have public access disabled')
        ips[key] = _address(environment['properties'].get('staticIp'), prefixes[key])
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
    }
    audit_collectors = {'auditPlatformBlob': 'collectPlatform', 'auditFoundryBlob': 'collectFoundry'}
    storage_prefix = groups['monitor'] + '/providers/Microsoft.Storage/storageAccounts/'
    for key, collector in audit_collectors.items():
        targets[key] = (ARCHIVE_COLLECTORS[collector]['endpointSuffix'],
                        storage_prefix + audit_storage_account_name(stem, collector),
                        ['blob'], 'Microsoft.Storage/storageAccounts', '2023-05-01')
    if 'certificate' in prefixes:
        targets.update({
            'certificateVault': ('tls-vault', service_prefix + 'Microsoft.KeyVault/vaults/kvtls-' + stem,
                                 ['vault'], 'Microsoft.KeyVault/vaults', '2024-11-01'),
            'certificateBlob': ('certificate-blob', service_prefix + 'Microsoft.Storage/storageAccounts/st'
                                + stem.replace('-', '') + 'cert', ['blob'], 'Microsoft.Storage/storageAccounts', '2025-01-01'),
        })
    sets = {}; seen_ips = set()
    for key, (suffix, target_id, group_ids, kind, api) in targets.items():
        if not required('ipSets', key): continue
        pe_id = nw + 'privateEndpoints/pe-' + stem + '-' + suffix
        pe = _read(config, transport, pe_id, 'Microsoft.Network/privateEndpoints',
                   required=not (_required_bindings is None and permit_pending_bindings
                                 and (key in audit_collectors or key.startswith('certificate'))))
        if pe is None:
            # Infrastructure preparation precedes creation of the two audit endpoints.
            # No missing binding is supplied to a rule or completion check.
            continue
        _read(config, transport, target_id, kind, api=api, state=False,
              location=config.get('foundryLocation') if key in ('foundry', 'auditFoundryBlob') else None)
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


def verify_fixed_nsg_inventory(parents, bindings, *, require_complete=False):
    """Accept only exact current rule tuples; completion also requires every rule."""
    fixed = _fixed_catalog(certificate='certificate' in bindings['subnets'])
    for group, parent in parents.items():
        current = _inventory(parent)
        expected = fixed[group]['rules']
        for name, rule in current.items():
            if name not in expected or _properties(rule) != _definition(expected[name], bindings):
                raise FixedNsgError('An NSG has an unknown, manually modified or conflicting child rule')
        if require_complete and (parent is None or current.keys() != expected.keys()):
            raise FixedNsgError('The complete fixed NSG rule set has not been deployed and verified')


def _etag(value):
    tag = value.get('etag') if isinstance(value, dict) else None
    if (not isinstance(tag, str) or len(tag) > 512
            or not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f,]+"|[A-Za-z0-9._:-]+', tag)
            or tag in ('*', '"*"', 'W/"*"')):
        raise FixedNsgError('A concrete owned NSG child ETag is required')
    return tag


def verify_fixed_nsg_before_deployment(config, transport):
    """Use only resources already required by present rules when resuming infrastructure deployment."""
    parents = _parents(config, transport)
    _, _, prefixes = _scope(config)
    fixed = _fixed_catalog(certificate='certificate' in prefixes)
    required_bindings = set()
    for group, parent in parents.items():
        for name in _inventory(parent):
            rule = fixed[group]['rules'].get(name)
            if rule is None:
                raise FixedNsgError('An NSG has an unknown, manually modified or conflicting child rule')
            for token in rule['source'] + rule['destination']:
                kind, value = token.split(':', 1)
                if kind in ('ip', 'address'):
                    required_bindings.add(('ips', value))
                elif kind in ('ips', 'addresses'):
                    required_bindings.add(('ipSets', value))

    if not required_bindings:
        verify_fixed_nsg_inventory(parents, {'subnets': prefixes, 'ips': {}, 'ipSets': {}})
        return

    options = {'permit_unassociated_portal': True, 'permit_pending_bindings': True,
               '_required_bindings': frozenset(required_bindings)}
    bindings = read_fixed_nsg_bindings(config, transport, **options)
    verify_fixed_nsg_inventory(parents, bindings)
    if bindings != read_fixed_nsg_bindings(config, transport, **options):
        raise FixedNsgError('Resource addresses changed before infrastructure deployment')
    verify_fixed_nsg_inventory(_parents(config, transport), bindings)


def verify_fixed_nsg_after_deployment(config, transport):
    """Confirm the complete NSG catalog and its live bindings after infrastructure deployment."""
    parents = _parents(config, transport)
    bindings = read_fixed_nsg_bindings(config, transport)
    if 'certificate' in config['network']['subnetPrefixes'] and 'certificate' not in bindings['subnets']:
        raise FixedNsgError('The dedicated certificate subnet is missing after infrastructure deployment')
    verify_fixed_nsg_inventory(parents, bindings, require_complete=True)
    if bindings != read_fixed_nsg_bindings(config, transport):
        raise FixedNsgError('Resource addresses changed during NSG verification')
    verify_fixed_nsg_inventory(_parents(config, transport), bindings, require_complete=True)
