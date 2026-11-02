"""Gatewayの公開契約に必要なWAF調整を、所有する完全パスだけへ関連付ける。"""
from __future__ import annotations

import copy
import fnmatch
import json
import re
import time

from deployment.helpers.portal_gateway import _etag, _writable, _writable_policy
from deployment.helpers.paths import REPOSITORY


NETWORK_API = '2024-05-01'
CALLBACK_PATH = '/auth/connections/browser/identity-callback'
CALLBACK_RULE_IDS = ['942430', '942440', '942450']
CALLBACK_RULE_NAME = 'credential-identity-callback'
SPEECH_RULE_NAME = 'custom-content-type'
SPEECH_RULE_ID = '920420'
DOCUMENT_PATH = '/tools/documents/v1/analyze'
DOCUMENT_RULE_NAME = 'document-analysis-upload'
RAW_CONTROLS = REPOSITORY / 'apps/gateway/waf-raw-body-controls.json'


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


def _speech_policy(source):
    """共通の保護を維持し、SSML/WAVを拒否するMIME許可リスト規則だけを調整する。"""
    desired = _writable_policy(source)
    properties = desired.get('properties', {})
    if properties.get('policySettings', {}).get('state') != 'Enabled':
        raise ValueError('Gateway Speech requires the existing enabled WAF policy')
    selected = [item for item in properties.get('managedRules', {}).get('managedRuleSets', [])
                if item.get('ruleSetType') == 'OWASP' and item.get('ruleSetVersion') == '3.2']
    if len(selected) != 1:
        raise ValueError('Gateway Speech requires the existing OWASP 3.2 rule set')
    groups = selected[0].setdefault('ruleGroupOverrides', [])
    matching = [group for group in groups if group.get('ruleGroupName') == 'REQUEST-920-PROTOCOL-ENFORCEMENT']
    if len(matching) > 1:
        raise ValueError('Gateway Speech protocol enforcement overrides are duplicated')
    if matching:
        group = matching[0]
        # グループ全体の無効化を引き継ぐと920470等も失われるため、明示的な規則一覧を必要とします。
        if not isinstance(group.get('rules'), list) or not group['rules']:
            raise ValueError('Gateway Speech requires individual protocol enforcement rules')
    else:
        group = {'ruleGroupName': 'REQUEST-920-PROTOCOL-ENFORCEMENT', 'rules': []}
        groups.append(group)
    rules = group['rules']
    if any(rule.get('ruleId') == '920470' and rule.get('state') == 'Disabled' for rule in rules):
        raise ValueError('Gateway Speech must retain Content-Type syntax inspection (920470)')
    matching = [rule for rule in rules if rule.get('ruleId') == SPEECH_RULE_ID]
    if len(matching) > 1:
        raise ValueError('Gateway Speech content type override is duplicated')
    if matching:
        matching[0]['state'] = 'Disabled'
    else:
        rules.append({'ruleId': SPEECH_RULE_ID, 'state': 'Disabled'})
    return desired


def _raw_body_policy(source):
    """Allow opaque body bytes while retaining traversal checks outside the body.

    XML/raw body field exclusions are unsupported. Only 930110 is adjusted;
    930100 and every other managed rule remain enabled. These controls cover
    literal and common percent-encoded traversal, not every CRS normalization.
    """
    desired = _speech_policy(source)
    properties = desired['properties']
    settings = properties.get('policySettings', {})
    if settings.get('mode') != 'Prevention' or settings.get('requestBodyCheck') is not True:
        raise ValueError('Raw data WAF requires Prevention mode and body inspection')
    rule_set = next(item for item in properties['managedRules']['managedRuleSets'] if item.get('ruleSetType') == 'OWASP')
    groups = rule_set.setdefault('ruleGroupOverrides', [])
    selected = [group for group in groups if group.get('ruleGroupName') == 'REQUEST-930-APPLICATION-ATTACK-LFI']
    if len(selected) > 1 or (selected and not selected[0].get('rules')):
        raise ValueError('Raw data WAF requires individual LFI rule overrides')
    if selected:
        group = selected[0]
    else:
        group = {'ruleGroupName': 'REQUEST-930-APPLICATION-ATTACK-LFI', 'rules': []}
        groups.append(group)
    if any(rule.get('ruleId') == '930100' and rule.get('state') == 'Disabled' for rule in group['rules']):
        raise ValueError('Raw data WAF must retain encoded traversal rule 930100')
    matching = [rule for rule in group['rules'] if rule.get('ruleId') == '930110']
    if len(matching) > 1:
        raise ValueError('Raw data WAF traversal override is duplicated')
    if matching:
        matching[0]['state'] = 'Disabled'
    else:
        group['rules'].append({'ruleId': '930110', 'state': 'Disabled'})
    controls = json.loads(RAW_CONTROLS.read_text(encoding='utf-8'))
    custom = properties.setdefault('customRules', [])
    specs = list(controls['rules'])
    names = {item['name'] for item in specs}
    if any(item.get('name') in names for item in custom):
        raise ValueError('Raw data WAF custom rule name is already owned')
    used = {item.get('priority') for item in custom}
    available = [value for value in range(1, 101) if value not in used]
    if len(available) < len(specs):
        raise ValueError('Raw data WAF has insufficient custom rule priorities')
    for spec, priority in zip(specs, available):
        conditions = [{
            'matchVariables': [{'variableName': spec['variableName']}], 'operator': 'Regex',
            'negationConditon': False, 'matchValues': [spec.get('pattern', controls['pattern'])],
            'transforms': spec.get('transforms', controls['transforms'])}]
        custom.append({'name': spec['name'], 'priority': priority, 'ruleType': 'MatchRule',
            'state': 'Enabled', 'action': 'Block', 'matchConditions': conditions})
    return desired


def _path_gateway(config, source, expected, policy_id, *, paths, rule_name, policy_suffix):
    """APIの転送先・他のパス・L7入口証明を保持し、指定した完全パスだけを追加する。"""
    if (str(source.get('id', '')).casefold() != expected.casefold()
            or source.get('name') != expected.rsplit('/', 1)[-1]
            or str(source.get('type', '')).casefold() != 'microsoft.network/applicationgateways'):
        raise ValueError('Gateway callback response identifies another resource')
    updated = _writable(source)
    properties = updated['properties']
    base_id = policy_id.removesuffix(policy_suffix)
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
    if settings.get('hostName') != 'apim-' + config['resourceStem'] + '.azure-api.net':
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
        current['firewallPolicy'] = {'id': policy_id}
        current['paths'] = list(paths)
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
                         rule_name=CALLBACK_RULE_NAME, policy_suffix='-gateway-auth')


def _speech_path_gateway(config, source, expected, policy_id, *, paths, rule_name, policy_suffix):
    """Split only owned Speech paths from a mixed MIME policy and preserve peers."""
    original = copy.deepcopy(source)
    mappings = original.get('properties', {}).get('urlPathMaps', [])
    mapping = next((item['properties'] for item in mappings if item.get('name') == 'pathmap-protocols'), None)
    position = None
    if mapping:
        kept = []
        base_id = policy_id.removesuffix(policy_suffix)
        for index, rule in enumerate(mapping.get('pathRules', [])):
            props = rule['properties']
            overlap = set(props.get('paths', [])) & set(paths)
            if rule.get('name') == rule_name:
                position = index
            if overlap and rule.get('name') in (SPEECH_RULE_NAME, 'speech-raw-body') and rule.get('name') != rule_name:
                if (not _same_ref(props.get('backendAddressPool'), expected + '/backendAddressPools/bepool-apim')
                        or not _same_ref(props.get('backendHttpSettings'), expected + '/backendHttpSettingsCollection/behttp-apim')
                        or str(props.get('firewallPolicy', {}).get('id', '')).casefold() not in
                        {(base_id + suffix).casefold() for suffix in ('-custom-mime', '-speech-raw-body')}
                        or (props.get('rewriteRuleSet') and props['rewriteRuleSet'] != mapping.get('defaultRewriteRuleSet'))):
                    raise ValueError('Speech raw path has another backend, policy or rewrite')
                remaining = [path for path in props['paths'] if path not in paths]
                if remaining:
                    props['paths'] = remaining
                    kept.append(rule)
            else:
                kept.append(rule)
        mapping['pathRules'] = kept
    updated = _path_gateway(config, original, expected, policy_id, paths=paths,
                            rule_name=rule_name, policy_suffix=policy_suffix)
    # A valid existing exact rule does not need to move above unrelated exact rules.
    if position is not None and mapping and not any('*' in pattern and any(
            fnmatch.fnmatchcase(path.casefold(), pattern.casefold()) for path in paths)
            for item in mapping['pathRules'][:position] for pattern in item['properties'].get('paths', [])):
        rules = next(item['properties']['pathRules'] for item in updated['properties']['urlPathMaps'] if item['name'] == 'pathmap-protocols')
        selected = next(item for item in rules if item['name'] == rule_name)
        rules.remove(selected)
        rules.insert(position, selected)
    return updated


def _configure_gateway_path_waf(config, transport, *, paths, rule_name, policy_suffix, policy_builder, speech_raw=False,
                                timeout=1200, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    """ETagで並行変更を検出し、専用WAFと完全パスの関連付けが反映されるまで待つ。"""
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
    route_builder = _path_gateway
    if speech_raw:
        mime_paths = []
        for api in config.get('apiDefinitions', []):
            for operation in api.get('operations', []):
                constraint = api.get('authorization', {}).get('operationConstraints', {}).get(operation.get('name'), {})
                if operation.get('method') == 'POST' and set(constraint.get('contentTypes', [])) & {'application/ssml+xml', 'audio/wav'}:
                    mime_paths.append('/' + api.get('path', '') + operation.get('urlTemplate', ''))
        live_mime_paths = [path for mapping in properties.get('urlPathMaps', []) if mapping.get('name') == 'pathmap-protocols'
                           for rule in mapping['properties'].get('pathRules', []) if rule.get('name') == SPEECH_RULE_NAME
                           for path in rule['properties'].get('paths', [])]
        if (set(mime_paths) | set(live_mime_paths)) - set(paths):
            rule_name, policy_suffix = 'speech-raw-body', '-speech-raw-body'
        route_builder = _speech_path_gateway
    base_id = expected.rsplit('/applicationGateways/', 1)[0] + '/ApplicationGatewayWebApplicationFirewallPolicies/wafpol-agw-' + config['resourceStem']
    policy_id = base_id + policy_suffix
    desired_gateway = route_builder(config, source, expected, policy_id, paths=paths,
                                    rule_name=rule_name, policy_suffix=policy_suffix)
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
            if status == 'Succeeded' and route_builder(config, current, expected, policy_id, paths=paths,
                    rule_name=rule_name, policy_suffix=policy_suffix) == _writable(current):
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
        rule_name=CALLBACK_RULE_NAME, policy_suffix='-gateway-auth', policy_builder=_callback_policy, **options)
    if receipt['required']:
        receipt.update({'path': CALLBACK_PATH, 'excludedArgument': 'code', 'excludedRuleIds': list(CALLBACK_RULE_IDS)})
    return receipt


def configure_gateway_speech_waf(config, transport, **options):
    """既存のcustom-mimeポリシーを再利用し、公開するSpeechの完全パスへ関連付ける。"""
    paths = speech_content_type_paths(config)
    if not paths:
        return {'resourceId': gateway_id(config), 'required': False, 'reason': 'Speech API is not published',
                'policyUpdated': False, 'gatewayUpdated': False, 'paths': []}
    receipt = _configure_gateway_path_waf(config, transport, paths=paths, rule_name=SPEECH_RULE_NAME,
        policy_suffix='-custom-mime', policy_builder=_raw_body_policy, speech_raw=True, **options)
    if receipt['required']:
        receipt.update({'disabledRuleIds': [SPEECH_RULE_ID, '930110'], 'retainedRuleIds': ['920470', '930100'],
                        'rawBodyTraversalAdjustment': True})
    return receipt


def document_upload_limit(config):
    """固定Document APIのファイル上限を取得し、別の公開パスへ広げない。"""
    definitions = [api for api in config.get('apiDefinitions', []) if api.get('name') == 'document-analysis-v1']
    if not definitions:
        return None
    if len(definitions) != 1:
        raise ValueError('Document analysis API definition is duplicated')
    api = definitions[0]
    operations = api.get('operations', [])
    constraint = api.get('authorization', {}).get('operationConstraints', {}).get('analyze_document', {})
    limit = constraint.get('maxRequestBytes')
    if (api.get('path') != 'tools/documents/v1' or len(operations) != 1
            or operations[0].get('name') != 'analyze_document' or operations[0].get('method') != 'POST'
            or operations[0].get('urlTemplate') != '/analyze' or operations[0].get('templateParameters')
            or constraint.get('kind') != 'documentAnalysis' or constraint.get('bodyFormat') != 'binary'
            or type(limit) is not int or not 1 <= limit <= 1048576):
        raise ValueError('Document WAF requires the fixed bounded binary analysis contract')
    return limit


def _document_policy(source, maximum):
    """正規文書のMIME/本文誤検知を調整し、非本文のtraversalと本文上限を維持する。"""
    desired = _raw_body_policy(source) if maximum == 1048576 else _speech_policy(source)
    settings = desired.get('properties', {}).get('policySettings', {})
    if settings.get('state') != 'Enabled':
        raise ValueError('Document upload requires the existing enabled WAF policy')
    # App Gateway rejects an entity at its exact byte boundary. Keep one byte
    # of rounded-up inspection headroom; APIM still enforces the public limit.
    limit_kb = max(8, (maximum + 1024) // 1024)
    settings.update({'requestBodyCheck': True, 'requestBodyEnforcement': True,
                     'maxRequestBodySizeInKb': limit_kb, 'requestBodyInspectLimitInKB': limit_kb})
    return desired


def configure_gateway_document_waf(config, transport, **options):
    """固定文書受付へ、実測したMIME/raw本文の調整と非本文Blockを関連付ける。"""
    maximum = document_upload_limit(config)
    if maximum is None:
        return {'resourceId': gateway_id(config), 'required': False, 'reason': 'Document API is not published',
                'policyUpdated': False, 'gatewayUpdated': False, 'paths': []}
    receipt = _configure_gateway_path_waf(config, transport, paths=[DOCUMENT_PATH],
        rule_name=DOCUMENT_RULE_NAME, policy_suffix='-document-upload',
        policy_builder=lambda source: _document_policy(source, maximum), **options)
    if receipt['required']:
        receipt.update({'maxRequestBytes': maximum, 'excludedRuleIds': [],
                        'disabledRuleIds': [SPEECH_RULE_ID, '930110'] if maximum == 1048576 else [SPEECH_RULE_ID],
                        'retainedRuleIds': ['920470', '930100']})
    return receipt
