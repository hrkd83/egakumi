"""Gatewayの公開契約に必要なWAF調整を、所有する完全パスだけへ関連付ける。"""
from __future__ import annotations

import copy
import fnmatch
import re
import time

from deployment.helpers.portal_gateway import _etag, _writable, _writable_policy
from deployment.helpers.apim_naming import apim_name
from deployment.helpers.waf_names import policy_id as waf_policy_id
from deployment.helpers.waf_body import (raw_body_policy as _raw_body_policy,
                                         speech_content_policy as _speech_policy)


NETWORK_API = '2024-05-01'
CALLBACK_PATH = '/auth/connections/browser/identity-callback'
CALLBACK_RULE_IDS = ['942430', '942440', '942450']
CALLBACK_RULE_NAME = 'credential-identity-callback'
SPEECH_RULE_NAME = 'speech'
SPEECH_RULE_ID = '920420'
DOCUMENT_PATH = '/tools/documents/v1/analyze'
DOCUMENT_RULE_NAME = 'document-analysis-upload'


def gateway_id(config):
    """構築設定で選択した環境のApplication Gatewayだけを対象にする。"""
    return (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
            f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/"
            f"applicationGateways/agw-{config['resourceStem']}")


def _ok(response, operation, allowed=(200,), *, require_body=True):
    if not isinstance(response, dict) or response.get('statusCode') not in allowed:
        status = response.get('statusCode') if isinstance(response, dict) else None
        raise ValueError(f'Gateway WAF {operation} failed (HTTP {status})')
    value = response.get('body')
    if require_body and not isinstance(value, dict):
        raise ValueError(f'Gateway WAF {operation} response is invalid')
    return value


def _same_ref(value, expected):
    return isinstance(value, dict) and str(value.get('id', '')).casefold() == expected.casefold()


def _one(items, name, label):
    selected = [item for item in items if isinstance(item, dict) and item.get('name') == name]
    if len(selected) != 1:
        raise ValueError('Gateway callback requires the existing ' + label)
    return selected[0]


def _callback_policy(source):
    """現在の共通保護を複製し、code値の実測3規則だけを除外する。"""
    desired = _writable_policy(source)
    properties = desired.get('properties', {})
    if properties.get('policySettings', {}).get('state') != 'Enabled':
        raise ValueError('Gateway callback requires the existing enabled WAF policy')
    managed = properties.get('managedRules', {})
    selected = [item for item in managed.get('managedRuleSets', [])
                if item.get('ruleSetType') in ('OWASP', 'Microsoft_DefaultRuleSet')]
    if len(selected) != 1 or not isinstance(selected[0].get('ruleSetVersion'), str):
        raise ValueError('Gateway callback WAF rule set cannot be identified')
    exclusion = {
        'matchVariable': 'RequestArgValues', 'selectorMatchOperator': 'Equals', 'selector': 'code',
        'exclusionManagedRuleSets': [{
            'ruleSetType': selected[0]['ruleSetType'], 'ruleSetVersion': selected[0]['ruleSetVersion'],
            'ruleGroups': [{'ruleGroupName': 'REQUEST-942-APPLICATION-ATTACK-SQLI',
                            'rules': [{'ruleId': rule} for rule in CALLBACK_RULE_IDS]}],
        }],
    }
    exclusions = managed.setdefault('exclusions', [])
    if not isinstance(exclusions, list):
        raise ValueError('Gateway callback WAF exclusions are invalid')
    if exclusion not in exclusions:
        exclusions.append(exclusion)
    return desired


def speech_content_type_paths(config):
    """公開するAzure SpeechのSSML/WAV契約から、調整対象の完全パスを決める。"""
    definitions = [api for api in config.get('apiDefinitions', []) if api.get('name') == 'azure-speech-v1']
    if len(definitions) > 1:
        raise ValueError('Gateway Speech API definition is duplicated')
    paths = []
    for api in definitions:
        prefix = api.get('path', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', prefix) or any(part in ('', '.', '..') for part in prefix.split('/')):
            raise ValueError('Gateway Speech WAF requires an exact API prefix')
        constraints = api.get('authorization', {}).get('operationConstraints', {})
        expected = {'tts': ('/synthesize', 'text', 'application/ssml+xml'),
                    'stt': ('/recognize', 'binary', 'audio/wav')}
        for operation in api.get('operations', []):
            if operation.get('name') not in expected:
                continue
            route, body_format, mime = expected[operation['name']]
            constraint = constraints.get(operation['name'], {})
            if (operation.get('method') != 'POST' or operation.get('urlTemplate') != route
                    or constraint.get('kind') != 'registeredOperation'
                    or constraint.get('bodyFormat') != body_format or constraint.get('contentTypes') != [mime]):
                raise ValueError('Gateway Speech WAF requires the canonical SSML/WAV POST contract')
            path = '/' + prefix + route
            if path in paths:
                raise ValueError('Gateway Speech WAF path is duplicated')
            paths.append(path)
    return sorted(paths)


def _path_gateway(config, source, expected, policy_id, *, paths, rule_name, policy_key):
    """APIの転送先・他のパス・L7入口証明を保持し、指定した完全パスだけを追加する。"""
    if (str(source.get('id', '')).casefold() != expected.casefold()
            or source.get('name') != expected.rsplit('/', 1)[-1]
            or str(source.get('type', '')).casefold() != 'microsoft.network/applicationgateways'):
        raise ValueError('Gateway callback response identifies another resource')
    updated = _writable(source)
    properties = updated['properties']
    base_id = waf_policy_id(config, 'g-base')
    if policy_id.casefold() != waf_policy_id(config, policy_key).casefold():
        raise ValueError('Gateway callback WAF target is not the owned purpose policy')
    if not _same_ref(properties.get('firewallPolicy'), base_id):
        raise ValueError('Gateway callback WAF must use the owned common policy; configure external WAF separately')
    listener = _one(properties.get('httpListeners', []), 'listener-https', 'API HTTPS listener')['properties']
    if listener.get('protocol') != 'Https' or listener.get('hostName') != config['gateway']['publicHostName']:
        raise ValueError('Gateway callback API listener has another host or protocol')
    if listener.get('firewallPolicy') and not _same_ref(listener['firewallPolicy'], base_id):
        raise ValueError('Gateway callback API listener uses another WAF policy')
    route = _one(properties.get('requestRoutingRules', []), 'rule-https-to-apim', 'API routing rule')['properties']
    if not _same_ref(route.get('httpListener'), expected + '/httpListeners/listener-https'):
        raise ValueError('Gateway callback API route has another listener')
    pool = expected + '/backendAddressPools/bepool-apim'
    backend = expected + '/backendHttpSettingsCollection/behttp-apim'
    _one(properties.get('backendAddressPools', []), 'bepool-apim', 'APIM backend pool')
    settings = _one(properties.get('backendHttpSettingsCollection', []), 'behttp-apim', 'APIM HTTP settings')['properties']
    if settings.get('hostName') != apim_name(config) + '.azure-api.net':
        raise ValueError('Gateway callback backend host identifies another APIM')
    mapping_id = expected + '/urlPathMaps/pathmap-protocols'
    if route.get('ruleType') == 'Basic':
        if not _same_ref(route.get('backendAddressPool'), pool) or not _same_ref(route.get('backendHttpSettings'), backend):
            raise ValueError('Gateway callback API route has another backend')
    elif route.get('ruleType') != 'PathBasedRouting' or not _same_ref(route.get('urlPathMap'), mapping_id):
        raise ValueError('Gateway callback API route has another path map')

    maps = properties.setdefault('urlPathMaps', [])
    selected = [item for item in maps if item.get('name') == 'pathmap-protocols']
    if len(selected) > 1:
        raise ValueError('Gateway callback API path map is duplicated')
    if selected:
        mapping = selected[0]['properties']
        if not _same_ref(mapping.get('defaultBackendAddressPool'), pool) or not _same_ref(mapping.get('defaultBackendHttpSettings'), backend):
            raise ValueError('Gateway callback path map defaults use another backend')
    else:
        if route.get('ruleType') != 'Basic':
            raise ValueError('Gateway callback API path map is missing')
        mapping = {'defaultBackendAddressPool': {'id': pool}, 'defaultBackendHttpSettings': {'id': backend}, 'pathRules': []}
        maps.append({'name': 'pathmap-protocols', 'properties': mapping})

    # Basicのrewriteをpath mapへ移す。既存mapのdefaultを変更せず、新パスでも同じ入口証明を維持する。
    rewrite = mapping.get('defaultRewriteRuleSet') or route.get('rewriteRuleSet')
    expected_rewrite = expected + '/rewriteRuleSets/trusted-l7-ingress'
    if rewrite and not _same_ref(rewrite, expected_rewrite):
        raise ValueError('Gateway callback API route has another rewrite rule set')
    if mapping.get('defaultRewriteRuleSet') and route.get('rewriteRuleSet') and not _same_ref(route['rewriteRuleSet'], expected_rewrite):
        raise ValueError('Gateway callback API route has conflicting rewrite rule sets')
    if config.get('gateway', {}).get('grpc', {}).get('enabled') and not rewrite:
        raise ValueError('Gateway callback requires the existing trusted L7 ingress rewrite')
    if rewrite:
        mapping['defaultRewriteRuleSet'] = copy.deepcopy(rewrite)
    desired = {'name': rule_name, 'properties': {
        'paths': list(paths), 'firewallPolicy': {'id': policy_id},
        'backendAddressPool': {'id': pool}, 'backendHttpSettings': {'id': backend},
        **({'rewriteRuleSet': copy.deepcopy(rewrite)} if rewrite else {}),
    }}
    rules = mapping.setdefault('pathRules', [])
    matches = [item for item in rules if item.get('name') == rule_name
               or any(path in item.get('properties', {}).get('paths', []) for path in paths)]
    if len(matches) > 1 or (matches and matches[0].get('name') != rule_name):
        raise ValueError('Gateway callback path has another owner')
    if matches:
        current = matches[0]['properties']
        if (sorted(current.get('paths', [])) != sorted(paths) or not _same_ref(current.get('backendAddressPool'), pool)
                or not _same_ref(current.get('backendHttpSettings'), backend)
                or (current.get('rewriteRuleSet') and not _same_ref(current['rewriteRuleSet'], expected_rewrite))):
            raise ValueError('Gateway callback path has another backend, rewrite or path')
        current_policy = current.get('firewallPolicy', {}).get('id')
        if current_policy and str(current_policy).casefold() not in {base_id.casefold(), policy_id.casefold()}:
            raise ValueError('Gateway callback path uses another WAF policy')
        current['firewallPolicy'] = {'id': policy_id}
        # Azureは同一ルール内の完全パスの順序を変えて返すことがある。同じ集合の検証後は
        # 既存順を保ち、反映待機や再実行で不要な差分を作らない。ルール間の優先順は別に扱う。
        if rewrite:
            current['rewriteRuleSet'] = copy.deepcopy(rewrite)
        # 関係のない完全パスとの順序は保ち、実際に先取りするwildcardがある時だけ移す。
        position = rules.index(matches[0])
        if any('*' in pattern and any(fnmatch.fnmatchcase(path.casefold(), pattern.casefold()) for path in paths)
               for rule in rules[:position] for pattern in rule.get('properties', {}).get('paths', [])):
            rules.remove(matches[0])
            rules.insert(0, matches[0])
    else:
        rules.insert(0, desired)
    route['ruleType'] = 'PathBasedRouting'
    route['urlPathMap'] = {'id': mapping_id}
    for name in ('backendAddressPool', 'backendHttpSettings', 'rewriteRuleSet'):
        route.pop(name, None)
    return updated


def _callback_gateway(config, source, expected, policy_id):
    """本人確認callbackだけを、共通の完全パス更新処理へ渡す。"""
    return _path_gateway(config, source, expected, policy_id, paths=[CALLBACK_PATH],
                         rule_name=CALLBACK_RULE_NAME, policy_key='g-auth')


def _configure_gateway_path_waf(config, transport, *, paths, rule_name, policy_key, policy_builder,
                                timeout=1200, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """ETagで並行変更を検出し、専用WAFと完全パスの関連付けが反映されるまで待つ。"""
    if config.get('gateway', {}).get('existingWafPolicyId'):
        raise ValueError('Gateway WAF must not replace an external WAF policy')
    expected = gateway_id(config)
    path = expected + '?api-version=' + NETWORK_API
    response = transport.request('GET', path)
    source = _ok(response, 'read routing')
    properties = source.get('properties', {})
    if str(source.get('id', '')).casefold() != expected.casefold():
        raise ValueError('Gateway callback response identifies another resource')
    if properties.get('provisioningState') != 'Succeeded':
        raise ValueError('Gateway is not ready for callback routing')
    if properties.get('sku', {}).get('name') == 'Standard_v2':
        return {'resourceId': expected, 'required': False, 'reason': 'WAF is not enabled',
                'policyUpdated': False, 'gatewayUpdated': False}
    if properties.get('sku', {}).get('name') != 'WAF_v2':
        raise ValueError('Gateway callback requires the expected WAF_v2 configuration')
    base_id = waf_policy_id(config, 'g-base')
    policy_id = waf_policy_id(config, policy_key)
    desired_gateway = _path_gateway(config, source, expected, policy_id, paths=paths,
                                    rule_name=rule_name, policy_key=policy_key)
    base = _ok(transport.request('GET', base_id + '?api-version=' + NETWORK_API), 'read common WAF policy')
    if str(base.get('id', '')).casefold() != base_id.casefold():
        raise ValueError('Gateway common WAF response identifies another policy')
    desired_policy = policy_builder(base)
    policy_path = policy_id + '?api-version=' + NETWORK_API
    policy_response = transport.request('GET', policy_path)
    exists = policy_response.get('statusCode') == 200
    if not exists and policy_response.get('statusCode') != 404:
        _ok(policy_response, 'read callback WAF policy')
    existing = _ok(policy_response, 'read callback WAF policy') if exists else None
    if exists and str(existing.get('id', '')).casefold() != policy_id.casefold():
        raise ValueError('Gateway callback WAF response identifies another policy')
    changed_policy = not exists or _writable_policy(existing) != desired_policy
    changed_gateway = desired_gateway != _writable(source)
    gateway_etag = _etag(response) if changed_gateway else None
    deadline, notice = monotonic() + timeout, monotonic() + 45
    if changed_policy:
        headers = {'If-Match': _etag(policy_response)} if exists else {'If-None-Match': '*'}
        _ok(transport.request('PUT', policy_path, desired_policy, headers=headers), 'update WAF policy', (200, 201, 202), require_body=False)
        while monotonic() < deadline:
            current = _ok(transport.request('GET', policy_path), 'read WAF update')
            status = current.get('properties', {}).get('provisioningState')
            if status == 'Succeeded' and _writable_policy(current) == desired_policy:
                break
            if status in ('Failed', 'Canceled', 'Deleting'):
                raise ValueError('Gateway callback WAF update did not succeed')
            if monotonic() >= notice:
                progress('Gateway: API用WAFポリシーの反映を待っています。', flush=True)
                notice = monotonic() + 45
            sleep(10)
        else:
            raise ValueError('Gateway callback WAF update is still pending; inspect it before retrying')
    if changed_gateway:
        _ok(transport.request('PUT', path, desired_gateway, headers={'If-Match': gateway_etag}), 'update routing', (200, 201, 202), require_body=False)
        while monotonic() < deadline:
            current = _ok(transport.request('GET', path), 'read routing update')
            status = current.get('properties', {}).get('provisioningState')
            if status == 'Succeeded' and _path_gateway(config, current, expected, policy_id, paths=paths,
                    rule_name=rule_name, policy_key=policy_key) == _writable(current):
                break
            if status in ('Failed', 'Canceled', 'Deleting'):
                raise ValueError('Gateway callback routing update did not succeed')
            if monotonic() >= notice:
                progress('Gateway: API用WAF経路の反映を待っています。', flush=True)
                notice = monotonic() + 45
            sleep(10)
        else:
            raise ValueError('Gateway callback routing update is still pending; inspect it before retrying')
    return {'resourceId': expected, 'required': True, 'policyId': policy_id, 'paths': list(paths),
            'policyUpdated': changed_policy, 'gatewayUpdated': changed_gateway}


def configure_gateway_identity_callback_waf(config, transport, **options):
    """認可コードを受ける本人確認callbackへ、実測した3規則だけの除外を設定する。"""
    receipt = _configure_gateway_path_waf(config, transport, paths=[CALLBACK_PATH],
        rule_name=CALLBACK_RULE_NAME, policy_key='g-auth', policy_builder=_callback_policy, **options)
    if receipt['required']:
        receipt.update({'path': CALLBACK_PATH, 'excludedArgument': 'code', 'excludedRuleIds': list(CALLBACK_RULE_IDS)})
    return receipt


def configure_gateway_speech_waf(config, transport, **options):
    """Speech専用ポリシーを、公開するSpeechの完全パスへ関連付ける。"""
    paths = speech_content_type_paths(config)
    if not paths:
        return {'resourceId': gateway_id(config), 'required': False, 'reason': 'Speech API is not published',
                'policyUpdated': False, 'gatewayUpdated': False, 'paths': []}
    receipt = _configure_gateway_path_waf(config, transport, paths=paths, rule_name=SPEECH_RULE_NAME,
        policy_key='g-speech', policy_builder=_raw_body_policy, **options)
    if receipt['required']:
        receipt.update({'disabledRuleIds': [SPEECH_RULE_ID, '930110'], 'retainedRuleIds': ['920470', '930100'],
                        'rawBodyTraversalAdjustment': True})
    return receipt


def document_upload_limit(config):
    """正本文書POSTの受付上限を取得し、別の公開パスへ広げない。"""
    definitions = [api for api in config.get('apiDefinitions', []) if api.get('name') == 'document-analysis-v1']
    if not definitions:
        return None
    if len(definitions) != 1:
        raise ValueError('Document analysis API definition is duplicated')
    api = definitions[0]
    operations = [operation for operation in api.get('operations', [])
                  if operation.get('name') == 'analyze_document']
    constraint = api.get('authorization', {}).get('operationConstraints', {}).get('analyze_document', {})
    limit = constraint.get('maxRequestBytes')
    if (api.get('path') != 'tools/documents/v1' or len(operations) != 1
            or operations[0].get('name') != 'analyze_document' or operations[0].get('method') != 'POST'
            or operations[0].get('urlTemplate') != '/analyze' or operations[0].get('templateParameters')
            or constraint.get('kind') != 'documentAnalysis' or constraint.get('bodyFormat') != 'binary'
            or type(limit) is not int or limit < 1):
        raise ValueError('Document WAF requires the canonical binary analysis POST contract')
    return limit


def _document_policy(source, maximum):
    """検査可能な先頭範囲と受付容量を分離し、非本文のtraversalを維持する。"""
    if type(maximum) is not int or maximum < 1:
        raise ValueError('Document upload requires a positive numeric API acceptance limit')
    return _raw_body_policy(source)


def configure_gateway_document_waf(config, transport, **options):
    """固定文書受付へ、実測したMIME/raw本文の調整と非本文Blockを関連付ける。"""
    maximum = document_upload_limit(config)
    if maximum is None:
        return {'resourceId': gateway_id(config), 'required': False, 'reason': 'Document API is not published',
                'policyUpdated': False, 'gatewayUpdated': False, 'paths': []}
    receipt = _configure_gateway_path_waf(config, transport, paths=[DOCUMENT_PATH],
        rule_name=DOCUMENT_RULE_NAME, policy_key='g-document-upload',
        policy_builder=lambda source: _document_policy(source, maximum), **options)
    if receipt['required']:
        receipt.update({'maxRequestBytes': maximum, 'excludedRuleIds': [],
                        'disabledRuleIds': [SPEECH_RULE_ID, '930110'],
                        'retainedRuleIds': ['920470', '930100']})
    return receipt
