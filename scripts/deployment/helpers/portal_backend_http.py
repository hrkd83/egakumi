"""Portal後段HTTP80の既存環境移行を、固定構築契約と同じ状態へ収束させる。"""
from __future__ import annotations

import copy
import ipaddress
import re
import time

from .portal_gateway import (
    NETWORK_API, _etag, _ok, _portal_backend, _writable, gateway_id,
    portal_http_configured,
)

APP_API = '2025-01-01'
FW_API = '2025-01-01'
HTTP_RULE_NAME = 'allow-appgw-aca-http'
LEGACY_HTTP_RULE_NAME = 'agw-aca-http'
HTTPS_RULE_NAME = 'agw-aca-https'


def portal_ids(config):
    """選択済み構築設定の資源だけを移行対象にする。"""
    root = f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
    service = root + config['resourceGroupNames']['svc'] + '/providers/Microsoft.App/'
    network = root + config['resourceGroupNames']['nw'] + '/providers/Microsoft.Network/'
    stem = config['resourceStem']
    return {
        'app': service + f'containerApps/ca-{stem}-portal',
        'environment': service + f'managedEnvironments/cae-{stem}',
        'subnet': network + f'virtualNetworks/vnet-{stem}/subnets/snet-{stem}-aca',
        'firewall': network + f'firewallPolicies/afwp-{stem}/ruleCollectionGroups/rcg-portal',
        'gateway': gateway_id(config),
    }


def _read(transport, target, api, kind):
    response = transport.request('GET', target + '?api-version=' + api)
    body = _ok(response, 'read HTTP migration target')
    if (str(body.get('id', '')).casefold() != target.casefold()
            or str(body.get('type', '')).casefold() != kind.casefold()
            or body.get('properties', {}).get('provisioningState') != 'Succeeded'):
        raise ValueError('Portal HTTP migration target identity or state is invalid')
    return response, body


def _environment(config, transport):
    ids = portal_ids(config)
    _, environment = _read(transport, ids['environment'], APP_API, 'Microsoft.App/managedEnvironments')
    properties = environment['properties']
    vnet = properties.get('vnetConfiguration', {})
    if (vnet.get('internal') is not True
            or str(vnet.get('infrastructureSubnetId', '')).casefold() != ids['subnet'].casefold()):
        raise ValueError('Portal HTTP requires its owned internal ACA subnet')
    try:
        address = ipaddress.IPv4Address(properties.get('staticIp', ''))
        if address not in ipaddress.IPv4Network(config['network']['subnetPrefixes']['aca']):
            raise ValueError('Outside ACA subnet')
    except (ValueError, TypeError, KeyError):
        raise ValueError('Portal HTTP environment VIP is invalid') from None
    domain = properties.get('defaultDomain')
    if not isinstance(domain, str) or not re.fullmatch(r'[a-z0-9.-]+', domain):
        raise ValueError('Portal HTTP environment domain is invalid')
    return str(address), f"ca-{config['resourceStem']}-portal.{domain}"


def _ingress(config, transport, *, bootstrap=False):
    ids = portal_ids(config)
    _, app = _read(transport, ids['app'], APP_API, 'Microsoft.App/containerApps')
    properties = app['properties']
    ingress = properties.get('configuration', {}).get('ingress', {})
    allowed_ports = (80, 3000) if bootstrap else (3000,)
    if (str(properties.get('managedEnvironmentId', '')).casefold() != ids['environment'].casefold()
            or ingress.get('external') is not True or ingress.get('allowInsecure') is not True
            or not isinstance(ingress.get('transport'), str) or ingress.get('transport') not in ('http', 'Http', 'auto', 'Auto')
            or ingress.get('targetPort') not in allowed_ports):
        raise ValueError('Portal HTTP ingress is not ready; retain the existing HTTPS backend')
    return app


def _rule(config, port):
    return {
        'name': HTTP_RULE_NAME if port == '80' else HTTPS_RULE_NAME,
        'ruleType': 'NetworkRule',
        'sourceAddresses': [config['network']['subnetPrefixes']['agw']],
        'destinationAddresses': [config['network']['subnetPrefixes']['aca']],
        'destinationPorts': [port], 'ipProtocols': ['TCP'],
    }


def _normalized_owned_rule(rule):
    """Azureが補う未使用アドレス配列と無効なIPv6の既定値だけを正規化する。"""
    actual = copy.deepcopy(rule)
    for key in ('sourceIpGroups', 'destinationIpGroups', 'destinationFqdns'):
        if actual.get(key) == []:
            actual.pop(key)
    if actual.get('ipv6Rule') is False:
        actual.pop('ipv6Rule')
    return actual


def _collection(config, body):
    properties = body.get('properties', {})
    collections = properties.get('ruleCollections')
    if properties.get('priority') != 350 or not isinstance(collections, list):
        raise ValueError('Portal HTTP firewall group differs from the owned group')
    matches = [item for item in collections if isinstance(item, dict) and item.get('name') == 'portal-ingress']
    if len(matches) != 1:
        raise ValueError('Portal HTTP firewall collection is missing or ambiguous')
    collection = matches[0]
    if (collection.get('priority') != 100
            or collection.get('ruleCollectionType') != 'FirewallPolicyFilterRuleCollection'
            or collection.get('action') != {'type': 'Allow'} or not isinstance(collection.get('rules'), list)):
        raise ValueError('Portal HTTP firewall collection identity is invalid')
    found = {}
    for port in ('80', '443'):
        expected = _rule(config, port)
        names = (HTTP_RULE_NAME, LEGACY_HTTP_RULE_NAME) if port == '80' else (HTTPS_RULE_NAME,)
        selected = [item for item in collection['rules'] if isinstance(item, dict) and item.get('name') in names]
        if len(selected) > 1:
            raise ValueError('Portal HTTP firewall rule is duplicated')
        if selected:
            expected['name'] = selected[0]['name']
            actual = _normalized_owned_rule(selected[0])
            if actual != expected:
                raise ValueError('Portal HTTP firewall rule has another source, destination or owner')
            found[port] = selected[0]
    if not found:
        raise ValueError('Portal HTTP migration requires an existing owned ingress rule')
    return collection, found


def _firewall(config, transport):
    target = portal_ids(config)['firewall']
    response, body = _read(transport, target, FW_API, 'Microsoft.Network/firewallPolicies/ruleCollectionGroups')
    collection, found = _collection(config, body)
    return response, body, collection, found


def _firewall_writable(body):
    """RCGの計算サイズだけを除き、規則とその他の設定を保持する。"""
    writable = _writable(body)
    # FirewallPolicyRuleCollectionGroupProperties.size is read-only in the
    # Network API. Adding a rule changes it; it is neither an input nor drift.
    writable.get('properties', {}).pop('size', None)
    return writable


def _firewall_contract(config, body):
    """所有HTTP／HTTPS規則の既定値だけを正規化し、その他の差分を保持する。"""
    writable = _firewall_writable(body)
    _, found = _collection(config, writable)
    for rule in found.values():
        normalized = _normalized_owned_rule(rule)
        rule.clear()
        rule.update(normalized)
    return writable


def _gateway_environment(config, body, address, host):
    backend, probe = _portal_backend(body, portal_ids(config)['gateway'])
    pools = body['properties'].get('backendAddressPools', [])
    selected = [item for item in pools if item.get('name') == 'bepool-portal']
    if (len(selected) != 1 or selected[0].get('properties', {}).get('backendAddresses') != [{'ipAddress': address}]
            or backend['properties'].get('hostName') != host or probe['properties'].get('host') != host
            or backend['properties'].get('pickHostNameFromBackendAddress') is not False
            or probe['properties'].get('pickHostNameFromBackendHttpSettings') is not False):
        raise ValueError('Portal HTTP gateway destination or Host differs from the owned ACA environment')


def verify_portal_http_ready(config, transport, *, expected_image_digest):
    """FW80と今回の期待イメージ・正常リビジョンが成立した後だけ切替を許す。"""
    if not isinstance(expected_image_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_image_digest):
        raise ValueError('Portal HTTP cutover requires the expected immutable image digest')
    address, host = _environment(config, transport)
    app = _ingress(config, transport)
    expected_image = f"acr{config['resourceStem'].replace('-', '')}.azurecr.io/portal@{expected_image_digest}"
    containers = app['properties'].get('template', {}).get('containers', [])
    if len(containers) != 1 or containers[0].get('name') != 'portal' or containers[0].get('image') != expected_image:
        raise ValueError('Portal HTTP cutover has not received the expected application image')
    revision = app['properties'].get('latestRevisionName')
    if (not isinstance(revision, str) or not re.fullmatch(r'[a-z0-9-]+', revision)
            or revision != app['properties'].get('latestReadyRevisionName')):
        raise ValueError('Portal HTTP cutover is waiting for the expected ready revision')
    response = transport.request('GET', portal_ids(config)['app'] + '/revisions/' + revision + '?api-version=' + APP_API)
    ready = _ok(response, 'read expected Portal HTTP revision')
    if str(ready.get('id', '')).casefold() != (portal_ids(config)['app'] + '/revisions/' + revision).casefold():
        raise ValueError('Portal HTTP revision response identifies another resource')
    properties = ready.get('properties', {})
    containers = properties.get('template', {}).get('containers', [])
    if (properties.get('healthState') != 'Healthy' or properties.get('provisioningState') != 'Provisioned'
            or properties.get('runningState') != 'Running' or len(containers) != 1
            or containers[0].get('name') != 'portal' or containers[0].get('image') != expected_image):
        raise ValueError('Portal HTTP cutover requires the expected healthy application revision')
    _, _, _, found = _firewall(config, transport)
    if '80' not in found:
        raise ValueError('Prepare the Portal firewall TCP80 rule before the HTTP backend cutover')
    _, gateway = _read(transport, portal_ids(config)['gateway'], NETWORK_API, 'Microsoft.Network/applicationGateways')
    _gateway_environment(config, gateway, address, host)
    return {'httpIngressReady': True, 'expectedImageVerified': True, 'revision': revision,
            'firewallHttpReady': True, 'protocol': 'Http', 'port': 80}


def require_portal_http_core_ready(config, transport):
    """初回は許可し、旧HTTPS環境への最終80-only Core先行適用を止める。"""
    target = portal_ids(config)['gateway']
    response = transport.request('GET', target + '?api-version=' + NETWORK_API)
    if response.get('statusCode') == 404:
        return {'newGateway': True, 'httpMigrationRequired': False}
    body = _ok(response, 'read Core Portal HTTP gate')
    if body.get('properties', {}).get('provisioningState') != 'Succeeded':
        raise ValueError('Portal HTTP gateway operation is pending; inspect it before Core deployment')
    if not portal_http_configured(body, target):
        raise ValueError('Existing Portal HTTPS backend must migrate before Core deployment: prepare HTTP firewall, deploy Portal, verify HTTP cutover, then retry Core')
    address, host = _environment(config, transport)
    _gateway_environment(config, body, address, host)
    _ingress(config, transport, bootstrap=True)
    _, _, _, found = _firewall(config, transport)
    if '80' not in found:
        raise ValueError('Portal HTTP firewall rule is missing; do not apply final Core settings yet')
    return {'newGateway': False, 'httpMigrationRequired': False, 'httpBackendVerified': True}


def ensure_portal_firewall_port(config, transport, *, port='80', execute=False,
                                record=None, timeout=900, sleep=time.sleep, monotonic=time.monotonic):
    """対象ポートを確保し、旧HTTP名だけを新名へ揃えて他規則と通信条件を保持する。"""
    if record is not None and not callable(record):
        raise ValueError('Portal HTTP migration requires a callable protected recorder')
    if port not in ('80', '443'):
        raise ValueError('Select a fixed Portal migration or rollback port')
    response, body, _, found = _firewall(config, transport)
    update = _firewall_writable(body)
    collection, update_found = _collection(config, update)
    added = port not in found
    renamed = port == '80' and not added and found[port]['name'] == LEGACY_HTTP_RULE_NAME
    changed = added or renamed
    if added:
        collection['rules'].append(_rule(config, port))
    elif renamed:
        update_found[port]['name'] = HTTP_RULE_NAME
    receipt = {'status': 'planned' if not execute else 'unchanged', 'port': int(port),
               'ruleAdded': False, 'ruleRenamed': False,
               'existingOtherPortRetained': ('443' if port == '80' else '80') in found}
    if not changed:
        return receipt
    etag = _etag(response)
    if callable(record):
        record('firewall-before', {'resourceId': portal_ids(config)['firewall'], 'etag': etag,
                                  'properties': copy.deepcopy(body['properties']), 'plannedPort': port})
    if not execute:
        return receipt
    if not callable(record):
        raise ValueError('Portal HTTP migration requires a protected pre-change record')
    target = portal_ids(config)['firewall'] + '?api-version=' + FW_API
    _ok(transport.request('PUT', target, update, headers={'If-Match': etag}), 'add Portal HTTP firewall port',
        (200, 201, 202), require_body=False)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        observed = _ok(transport.request('GET', target), 'read Portal HTTP firewall update')
        status = observed.get('properties', {}).get('provisioningState')
        if status == 'Succeeded':
            _collection(config, observed)
            if _firewall_contract(config, observed) != _firewall_contract(config, update):
                raise ValueError('Portal HTTP firewall readback differs; inspect concurrent changes')
            result = {**receipt, 'status': 'updated', 'ruleAdded': added, 'ruleRenamed': renamed}
            record('firewall-after', result)
            return result
        if status in ('Failed', 'Canceled', 'Deleting'):
            raise ValueError('Portal HTTP firewall update failed; retain and inspect the previous backend')
        sleep(10)
    raise ValueError('Portal HTTP firewall update is pending; inspect before retrying')


def rollback_portal_backend(config, transport, *, execute=False, record=None,
                            timeout=1200, sleep=time.sleep, monotonic=time.monotonic):
    """旧443を先に確保し、現在の他設定を保って後段・probeだけHTTPSへ戻す。"""
    if (record is not None and not callable(record)) or execute and not callable(record):
        raise ValueError('Portal HTTP rollback requires a callable protected recorder')
    address, host = _environment(config, transport)
    _, app = _read(transport, portal_ids(config)['app'], APP_API, 'Microsoft.App/containerApps')
    if str(app['properties'].get('managedEnvironmentId', '')).casefold() != portal_ids(config)['environment'].casefold():
        raise ValueError('Portal HTTP rollback app belongs to another environment')
    _, before_gateway = _read(transport, portal_ids(config)['gateway'], NETWORK_API, 'Microsoft.Network/applicationGateways')
    _gateway_environment(config, before_gateway, address, host)
    ensure_portal_firewall_port(config, transport, port='443', execute=execute, record=record,
                                timeout=timeout, sleep=sleep, monotonic=monotonic)
    response, body = _read(transport, portal_ids(config)['gateway'], NETWORK_API, 'Microsoft.Network/applicationGateways')
    _gateway_environment(config, body, address, host)
    backend, probe = _portal_backend(body, portal_ids(config)['gateway'])
    receipt = {'status': 'planned' if not execute else 'unchanged', 'protocol': 'Https', 'port': 443, 'updated': False}
    if backend['properties']['protocol'] == 'Https' and probe['properties']['protocol'] == 'Https':
        return receipt
    etag = _etag(response)
    if callable(record):
        record('gateway-before-rollback', {'resourceId': portal_ids(config)['gateway'], 'etag': etag,
               'backend': copy.deepcopy(backend), 'probe': copy.deepcopy(probe)})
    if not execute:
        return receipt
    if not callable(record):
        raise ValueError('Portal HTTP rollback requires a protected pre-change record')
    update = _writable(body)
    selected_backend, selected_probe = _portal_backend(update | {'id': body['id'], 'name': body['name'], 'type': body['type']}, portal_ids(config)['gateway'])
    selected_backend['properties'].update(protocol='Https', port=443)
    selected_probe['properties'].update(protocol='Https', port=443)
    target = portal_ids(config)['gateway'] + '?api-version=' + NETWORK_API
    _ok(transport.request('PUT', target, update, headers={'If-Match': etag}), 'rollback Portal backend',
        (200, 201, 202), require_body=False)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        observed = _ok(transport.request('GET', target), 'read Portal backend rollback')
        status = observed.get('properties', {}).get('provisioningState')
        if status == 'Succeeded':
            _gateway_environment(config, observed, address, host)
            if _writable(observed) != update:
                raise ValueError('Portal HTTPS rollback readback differs from the planned update')
            result = {**receipt, 'status': 'updated', 'updated': True}
            record('gateway-after-rollback', result)
            return result
        if status in ('Failed', 'Canceled', 'Deleting'):
            raise ValueError('Portal HTTPS rollback failed; inspect its current state')
        sleep(10)
    raise ValueError('Portal HTTPS rollback is pending; inspect before retrying')
