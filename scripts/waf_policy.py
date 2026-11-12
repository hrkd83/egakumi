#!/usr/bin/env python3
"""Reconcile scoped AI data WAF exclusions. Default CLI mode only plans."""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import copy
import fnmatch
import json
from pathlib import Path
import re
import time

from deployment.helpers.paths import REPOSITORY
from deployment.helpers.portal_gateway import (_etag, _ok, _writable, _writable_policy, gateway_id,
                                                NETWORK_API, portal_session_exclusions)
from deployment.helpers.waf_names import (DATA_POLICY_KEYS, GATEWAY_DATA_POLICY_KEYS,
                                           PORTAL_DATA_POLICY_KEYS, policy_id)
from deployment.helpers.waf_body import (ai_body_settings, portal_document_profile, raw_body_policy,
                                         ai_content_type_profile, ai_content_type_policy)


MANIFEST = REPOSITORY / 'apps/gateway/waf-data-exclusions.json'
PROTOCOL_POLICY_KEYS = {
    'gateway': ('g-base', 'g-auth', 'g-speech', 'g-document-upload'),
    'portal': ('p-base', 'p-auth'),
}
SCOPED_DATA_POLICY_KEYS = {'gateway': GATEWAY_DATA_POLICY_KEYS, 'portal': PORTAL_DATA_POLICY_KEYS}
RESERVED_POLICY_KEYS = frozenset(key for keys in PROTOCOL_POLICY_KEYS.values() for key in keys)

# These groups inspect application source syntax that is legitimate AI data.
# Protocol, parser, anomaly-scoring and Known-CVE groups are deliberately absent.
AI_DATA_RULE_GROUPS = frozenset({
    'REQUEST-930-APPLICATION-ATTACK-LFI', 'REQUEST-931-APPLICATION-ATTACK-RFI',
    'REQUEST-932-APPLICATION-ATTACK-RCE', 'REQUEST-933-APPLICATION-ATTACK-PHP',
    'REQUEST-941-APPLICATION-ATTACK-XSS', 'REQUEST-942-APPLICATION-ATTACK-SQLI',
})
AI_DATA_SCOPES = {
    'g-chat-completions': {
        'scope': 'gateway',
        'paths': frozenset({'/models/openai/v1/chat/completions'}),
        'values': frozenset({
            'messages.content', 'messages.content.text',
            'messages.tool_calls.function.arguments', 'messages.function_call.arguments',
            'messages.tool_calls.custom.input', 'tools.function.description',
            'functions.description', 'tools.custom.description',
            'tools.custom.format.grammar.definition',
        }),
        'schemas': frozenset({
            'response_format.json_schema.schema.', 'tools.function.parameters.',
            'functions.parameters.',
        }),
    },
    'g-responses': {
        'scope': 'gateway',
        'paths': frozenset({'/models/openai/v1/responses'}),
        'values': frozenset({
            'input', 'input.content', 'input.content.text', 'instructions',
            'input.arguments', 'input.output', 'input.output.text', 'input.input',
            'tools.description', 'input.tools.tools.description',
            'input.tools.tools.format.definition',
        }),
        'schemas': frozenset({
            'text.format.schema.', 'tools.parameters.', 'input.tools.tools.parameters.',
        }),
    },
    'g-images': {
        'scope': 'gateway',
        'paths': frozenset({
            '/models/blackforestlabs/v1/flux-2/edits',
            '/models/blackforestlabs/v1/flux-2/generations',
            '/models/openai-images/v1/deployments/gpt-image-2.5-flare/edits',
            '/models/openai-images/v1/deployments/gpt-image-2.5-sunburst/edits',
            '/models/openai-images/v1/deployments/gpt-image-2/edits',
            '/models/openai-images/v1/generations',
        }),
        'values': frozenset({'prompt'}),
        'schemas': frozenset(),
    },
    'p-images': {
        'scope': 'portal',
        'paths': frozenset({'/api/models/images'}),
        'values': frozenset({'prompt'}),
        'schemas': frozenset(),
    },
    'g-embeddings': {
        'scope': 'gateway',
        'paths': frozenset({'/models/cohere/v1/embed', '/models/openai-embeddings/v1/embeddings'}),
        'values': frozenset({'input', 'texts'}),
        'schemas': frozenset(),
    },
    'p-embeddings': {
        'scope': 'portal',
        'paths': frozenset({'/api/models/embeddings'}),
        'values': frozenset({'texts'}),
        'schemas': frozenset(),
    },
}


def _allows_ai_group(profile, item):
    scope = AI_DATA_SCOPES.get(profile['name'])
    if (not scope or profile['scope'] != scope['scope'] or frozenset(profile['paths']) != scope['paths']
            or set(item) != {'matchVariable', 'selectorMatchOperator', 'selector', 'exclusionManagedRuleSets'}):
        return False
    variable, operator, selector = (item[key] for key in
                                    ('matchVariable', 'selectorMatchOperator', 'selector'))
    return ((variable == 'RequestArgValues' and operator == 'Equals' and selector in scope['values'])
            or (variable in ('RequestArgValues', 'RequestArgKeys') and operator == 'StartsWith'
                and selector in scope['schemas']))


def exclusion(selector, group, rules, variable='RequestArgValues'):
    return {'matchVariable': variable, 'selectorMatchOperator': 'Equals', 'selector': selector,
            'exclusionManagedRuleSets': [{'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2',
                'ruleGroups': [{'ruleGroupName': group, 'rules': [{'ruleId': rule} for rule in rules]}]}]}


def load_manifest(path=MANIFEST):
    return _validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))


def _validate_manifest(data):
    if not isinstance(data, dict) or data.get('version') not in (1, 2) or not isinstance(data.get('profiles'), list):
        raise ValueError('WAF manifest requires version 1 or 2 and profiles')
    names, paths = set(), set()
    for profile in data['profiles']:
        if (not isinstance(profile, dict) or not re.fullmatch(r'[gp]-[a-z][a-z0-9]*(?:-[a-z0-9]+)*', str(profile.get('name', '')))
                or len(profile['name']) > 42
                or profile.get('scope') not in ('gateway', 'portal') or profile['name'] in names
                or not profile['name'].startswith('g-' if profile['scope'] == 'gateway' else 'p-')
                or profile['name'] in RESERVED_POLICY_KEYS
                or profile['name'] not in DATA_POLICY_KEYS
                or not isinstance(profile.get('paths'), list) or not profile['paths']
                or not isinstance(profile.get('exclusions'), list) or not profile['exclusions']):
            raise ValueError('Invalid WAF manifest profile')
        names.add(profile['name'])
        for path_value in profile['paths']:
            key = (profile['scope'], str(path_value).casefold())
            if (not isinstance(path_value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path_value)
                    or any(part in ('', '.', '..') for part in path_value[1:].rstrip('/').split('/')) or key in paths):
                raise ValueError('WAF manifest paths must be unique exact paths')
            paths.add(key)
        selectors = set()
        for item in profile['exclusions']:
            if (not isinstance(item, dict) or item.get('matchVariable') not in ('RequestArgValues', 'RequestArgKeys')
                    or item.get('selectorMatchOperator') not in ('Equals', 'StartsWith')
                    or not isinstance(item.get('selector'), str) or not item['selector']
                    or item['selector'] == '*'
                    or (item.get('selectorMatchOperator') == 'StartsWith' and not item['selector'].endswith('.'))
                    or not isinstance(item.get('exclusionManagedRuleSets'), list)
                    or len(item['exclusionManagedRuleSets']) != 1):
                raise ValueError('WAF exclusions must select an explicit argument and scoped rules')
            selector_key = (item['matchVariable'], item['selectorMatchOperator'], item['selector'])
            if selector_key in selectors:
                raise ValueError('WAF exclusions require unique variable, operator and selector scopes')
            selectors.add(selector_key)
            rule_set = item['exclusionManagedRuleSets'][0]
            if (not isinstance(rule_set, dict)
                    or set(rule_set) != {'ruleSetType', 'ruleSetVersion', 'ruleGroups'}
                    or rule_set.get('ruleSetType') != 'OWASP' or rule_set.get('ruleSetVersion') != '3.2'
                    or not isinstance(rule_set.get('ruleGroups'), list) or not rule_set['ruleGroups']):
                raise ValueError('WAF manifest supports only rule-scoped OWASP 3.2 exclusions')
            groups = set()
            for group in rule_set['ruleGroups']:
                if not isinstance(group, dict) or set(group) not in ({'ruleGroupName'}, {'ruleGroupName', 'rules'}):
                    raise ValueError('Invalid WAF rule group shape')
                group_name = str(group.get('ruleGroupName', ''))
                if (group_name in groups or not
                        (re.fullmatch(r'REQUEST-9[0-9]{2}-[A-Z0-9-]+', group_name) or group_name == 'Known-CVEs')):
                    raise ValueError('Invalid WAF rule group')
                groups.add(group_name)
                if 'rules' not in group:
                    if (data['version'] != 2 or group_name not in AI_DATA_RULE_GROUPS
                            or not _allows_ai_group(profile, item)):
                        raise ValueError('Whole WAF groups require an approved AI data field, exact route and manifest version 2')
                    continue
                rules = group.get('rules')
                if not isinstance(rules, list) or not rules or any(
                        not isinstance(rule, dict)
                        or set(rule) != {'ruleId'}
                        or not (re.fullmatch(r'9[0-9]{5}', str(rule.get('ruleId', ''))) or rule.get('ruleId') == '800100')
                        or rule['ruleId'] in ('949110', '980130')
                        or ((rule['ruleId'] == '800100') != (group_name == 'Known-CVEs')) for rule in rules):
                    raise ValueError('WAF exclusions require individual contributing rule IDs')
                if len({rule['ruleId'] for rule in rules}) != len(rules):
                    raise ValueError('WAF exclusion rule IDs must be unique within their group')
    return data


def _apis(config):
    if config.get('apiDefinitions') == []:
        from standard_apis import infrastructure_api_definitions
        return infrastructure_api_definitions([])
    return config.get('apiDefinitions', [])


def published_paths(config):
    """Exclude templated/unpublished endpoints rather than widening a finite manifest."""
    result = set()
    for api in _apis(config):
        mcp_path = api.get('mcp', {}).get('path')
        if isinstance(mcp_path, str) and re.fullmatch(r'[A-Za-z0-9_./-]+', mcp_path):
            result.add('/' + mcp_path.strip('/') + '/mcp')
        prefix = str(api.get('path', '')).strip('/')
        for operation in api.get('operations', []):
            path = '/' + prefix + str(operation.get('urlTemplate', ''))
            candidates = [path]
            if '{deployment}' in path:
                constraint = api.get('authorization', {}).get('operationConstraints', {}).get(operation.get('name'), {})
                candidates = [path.replace('{deployment}', name) for name in constraint.get('allowedModels', [])
                              if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name)]
            result.update(value for value in candidates if re.fullmatch(r'/[A-Za-z0-9_./-]+', value))
    return result


def selected_profiles(config, scopes=('gateway', 'portal'), manifest=None):
    data = load_manifest() if manifest is None else _validate_manifest(manifest)
    available = published_paths(config)
    selected = []
    for profile in data['profiles']:
        if profile['scope'] not in scopes:
            continue
        paths = [path for path in profile['paths'] if profile['scope'] == 'portal' or path in available]
        if paths:
            selected.append({**copy.deepcopy(profile), 'paths': paths})
    return selected


def plan(config, scopes=('gateway', 'portal'), manifest=None):
    profiles = selected_profiles(config, scopes, manifest)
    external = bool(config.get('gateway', {}).get('existingWafPolicyId'))
    return {'status': 'external-policy-skipped' if external else 'planned', 'azureWrites': 0,
            'profiles': [{'name': item['name'], 'scope': item['scope'], 'paths': item['paths'],
                          'exclusions': item['exclusions']} for item in profiles]}


def _unique(items):
    result = []
    for item in items:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result


def canonical_gateway(body):
    """Ignore ARM-generated identity metadata on named inline subresources only.

    Reference objects such as firewallPolicy/backendAddressPool keep their IDs,
    so comparisons still detect changed policies, backends and rewrite bindings.
    The PUT body is unchanged; this normalization is only for comparison.
    """
    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            named_resource = isinstance(value.get('name'), str) and isinstance(value.get('properties'), dict)
            return {key: clean(item) for key, item in value.items()
                    if not (named_resource and key in ('id', 'type'))}
        return value
    return clean(_writable(body))


def canonical_policy(body):
    """Compare Azure's empty whole-group rule lists with omitted rule lists.

    This is comparison-only: manifest validation and the PUT body retain their
    explicit forms. Do not normalize overrides, nonempty lists, other rule sets
    or group objects with unknown properties.
    """
    result = _writable_policy(body)
    exclusions = result.get('properties', {}).get('managedRules', {}).get('exclusions', [])
    if isinstance(exclusions, list):
        for item in exclusions:
            rule_sets = item.get('exclusionManagedRuleSets', []) if isinstance(item, dict) else []
            if not isinstance(rule_sets, list):
                continue
            for rule_set in rule_sets:
                if (not isinstance(rule_set, dict)
                        or (rule_set.get('ruleSetType'), rule_set.get('ruleSetVersion')) != ('OWASP', '3.2')):
                    continue
                groups = rule_set.get('ruleGroups', [])
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if (isinstance(group, dict) and set(group) == {'ruleGroupName', 'rules'}
                            and isinstance(group['ruleGroupName'], str)
                            and group['ruleGroupName'] in AI_DATA_RULE_GROUPS
                            and isinstance(group['rules'], list) and not group['rules']):
                        group.pop('rules')
    return result


def data_scope_exclusions(profile):
    """Only the common Portal cookie is implicit; data exceptions are explicit."""
    return portal_session_exclusions() if profile['scope'] == 'portal' else []


def exclusion_is_covered(existing, desired):
    """Accept an existing scope only when every old rule remains excluded.

    A whole group covers its individual IDs. A delimited schema prefix also
    covers exact descendants or narrower prefixes of the same variable. A
    sibling field, another variable, an omitted rule set or an unknown object
    shape is not silently treated as equivalent.
    """
    if existing == desired:
        return True
    scope_keys = ('matchVariable', 'selectorMatchOperator', 'selector')
    expected_keys = set(scope_keys) | {'exclusionManagedRuleSets'}
    if (not isinstance(existing, dict) or not isinstance(desired, dict)
            or set(existing) != expected_keys or set(desired) != expected_keys
            or existing['matchVariable'] != desired['matchVariable']):
        return False
    old_operator, new_operator = existing['selectorMatchOperator'], desired['selectorMatchOperator']
    old_selector, new_selector = existing['selector'], desired['selector']
    if not isinstance(old_selector, str) or not isinstance(new_selector, str):
        return False
    if new_operator == 'Equals':
        if old_operator != 'Equals' or old_selector != new_selector:
            return False
    elif new_operator == 'StartsWith':
        if (old_operator not in ('Equals', 'StartsWith') or not new_selector.endswith('.')
                or not old_selector.startswith(new_selector)):
            return False
    else:
        return False
    old_sets, new_sets = existing['exclusionManagedRuleSets'], desired['exclusionManagedRuleSets']
    if not isinstance(old_sets, list) or not old_sets or not isinstance(new_sets, list):
        return False
    for old in old_sets:
        if (not isinstance(old, dict) or set(old) != {'ruleSetType', 'ruleSetVersion', 'ruleGroups'}
                or not isinstance(old['ruleGroups'], list) or not old['ruleGroups']):
            return False
        matches = [new for new in new_sets if isinstance(new, dict)
                   and (new.get('ruleSetType'), new.get('ruleSetVersion')) ==
                   (old['ruleSetType'], old['ruleSetVersion'])]
        if len(matches) != 1 or not isinstance(matches[0].get('ruleGroups'), list):
            return False
        for old_group in old['ruleGroups']:
            if (not isinstance(old_group, dict)
                    or set(old_group) not in ({'ruleGroupName'}, {'ruleGroupName', 'rules'})):
                return False
            groups = [new for new in matches[0]['ruleGroups'] if isinstance(new, dict)
                      and new.get('ruleGroupName') == old_group['ruleGroupName']]
            if len(groups) != 1:
                return False
            new_group = groups[0]
            if set(new_group) == {'ruleGroupName'}:
                continue
            old_rules, new_rules = old_group.get('rules'), new_group.get('rules')
            if (set(new_group) != {'ruleGroupName', 'rules'} or not old_rules
                    or not isinstance(old_rules, list) or not isinstance(new_rules, list)
                    or any(not isinstance(rule, dict) or set(rule) != {'ruleId'}
                           for rule in old_rules + new_rules)):
                return False
            if not {rule['ruleId'] for rule in old_rules} <= {rule['ruleId'] for rule in new_rules}:
                return False
    return True


def desired_policy(base, profile, config, sources=()):
    baseline = _writable_policy(base)
    if portal_document_profile(profile):
        desired = raw_body_policy(base)
    elif ai_content_type_profile(profile):
        desired = ai_content_type_policy(base, profile)
    else:
        desired = copy.deepcopy(baseline)
    properties = desired['properties']
    settings = baseline['properties'].get('policySettings', {})
    if (settings.get('state') != 'Enabled' or settings.get('mode') != 'Prevention'
            or settings.get('requestBodyCheck') is not True):
        raise ValueError('Data WAF requires enabled Prevention mode with request body inspection')
    properties['policySettings'] = ai_body_settings(settings)
    managed = properties.get('managedRules', {})
    sets = managed.get('managedRuleSets', [])
    if len([item for item in sets if item.get('ruleSetType') == 'OWASP' and item.get('ruleSetVersion') == '3.2']) != 1:
        raise ValueError('Data WAF requires the existing OWASP 3.2 rule set')
    known = _unique(managed.get('exclusions', []) + data_scope_exclusions(profile) + profile['exclusions'])
    for source in sources:
        current = _writable_policy(source)['properties']
        if (current.get('policySettings') not in (settings, properties['policySettings'])
                or current.get('customRules', []) not in (baseline['properties'].get('customRules', []), properties.get('customRules', []))):
            raise ValueError('Existing path WAF protection differs; reconcile its settings explicitly before combining paths')
        for item in current.get('managedRules', {}).get('exclusions', []):
            if not any(exclusion_is_covered(item, candidate) for candidate in known):
                raise ValueError('Represent the existing path-specific WAF exclusion in the manifest before consolidation')
        for rule_set in current.get('managedRules', {}).get('managedRuleSets', []):
            matches = [item for item in sets if (item.get('ruleSetType'), item.get('ruleSetVersion')) ==
                       (rule_set.get('ruleSetType'), rule_set.get('ruleSetVersion'))]
            if len(matches) != 1:
                raise ValueError('Existing path WAF uses another rule set')
            original = [item for item in baseline['properties'].get('managedRules', {}).get('managedRuleSets', [])
                        if (item.get('ruleSetType'), item.get('ruleSetVersion')) ==
                           (rule_set.get('ruleSetType'), rule_set.get('ruleSetVersion'))]
            if rule_set.get('ruleGroupOverrides', []) not in (matches[0].get('ruleGroupOverrides', []),
                    original[0].get('ruleGroupOverrides', []) if len(original) == 1 else None):
                raise ValueError('Existing path WAF overrides differ; preserve them in a separate protocol policy')
    managed['exclusions'] = known
    return desired


def _scope_mapping(body, expected, scope):
    properties = body['properties']
    listener_name, route_name, map_name, pool_name, backend_name = (
        ('listener-https', 'rule-https-to-apim', 'pathmap-protocols', 'bepool-apim', 'behttp-apim') if scope == 'gateway'
        else ('portal-https', 'rule-portal-https', 'pathmap-portal-auth', 'bepool-portal', 'behttp-portal'))
    def one(items, name):
        selected = [item for item in items if item.get('name') == name]
        if len(selected) != 1:
            raise ValueError('Data WAF requires the owned listener, route and backend')
        return selected[0]['properties']
    listener = one(properties.get('httpListeners', []), listener_name)
    if listener.get('protocol') != 'Https':
        raise ValueError('Data WAF requires the HTTPS listener')
    route = one(properties.get('requestRoutingRules', []), route_name)
    if str(route.get('httpListener', {}).get('id', '')).casefold() != (expected + '/httpListeners/' + listener_name).casefold():
        raise ValueError('Data WAF routing rule uses another listener')
    pool, backend = {'id': expected + '/backendAddressPools/' + pool_name}, {'id': expected + '/backendHttpSettingsCollection/' + backend_name}
    maps = properties.setdefault('urlPathMaps', [])
    if route.get('ruleType') == 'Basic':
        if route.get('backendAddressPool') != pool or route.get('backendHttpSettings') != backend:
            raise ValueError('Data WAF basic route uses another backend')
        mapping = {'defaultBackendAddressPool': pool, 'defaultBackendHttpSettings': backend, 'pathRules': []}
        if route.get('rewriteRuleSet'):
            mapping['defaultRewriteRuleSet'] = route['rewriteRuleSet']
        if any(item.get('name') == map_name for item in maps):
            raise ValueError('Data WAF path map already exists on a basic route')
        maps.append({'name': map_name, 'properties': mapping})
        route.update(ruleType='PathBasedRouting', urlPathMap={'id': expected + '/urlPathMaps/' + map_name})
        for key in ('backendAddressPool', 'backendHttpSettings', 'rewriteRuleSet'):
            route.pop(key, None)
    else:
        if (route.get('ruleType') != 'PathBasedRouting' or str(route.get('urlPathMap', {}).get('id', '')).casefold() !=
                (expected + '/urlPathMaps/' + map_name).casefold()):
            raise ValueError('Data WAF route uses another path map')
        mapping = one(maps, map_name)
        if mapping.get('defaultBackendAddressPool') != pool or mapping.get('defaultBackendHttpSettings') != backend:
            raise ValueError('Data WAF path map uses another default backend')
    return mapping, pool, backend


def reconcile(config, transport, *, scopes=('gateway', 'portal'), manifest=None, execute=True,
              timeout=1200, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    data = load_manifest() if manifest is None else _validate_manifest(manifest)
    profiles = selected_profiles(config, scopes, data)
    receipt = plan(config, scopes, data)
    if not execute or receipt['status'] == 'external-policy-skipped' or not profiles:
        return receipt
    expected = gateway_id(config)
    target = expected + '?api-version=' + NETWORK_API
    response = transport.request('GET', target)
    source = _ok(response, 'read data WAF routing')
    if str(source.get('id', '')).casefold() != expected.casefold() or source.get('properties', {}).get('provisioningState') != 'Succeeded':
        raise ValueError('Selected Application Gateway is not ready')
    if source['properties'].get('sku', {}).get('name') == 'Standard_v2':
        return {**receipt, 'status': 'waf-not-enabled-skipped'}
    if source['properties'].get('sku', {}).get('name') != 'WAF_v2':
        raise ValueError('Data WAF requires WAF_v2')
    base_id = policy_id(config, 'g-base')
    if str(source['properties'].get('firewallPolicy', {}).get('id', '')).casefold() != base_id.casefold():
        return {**receipt, 'status': 'external-policy-skipped'}
    update, cache, changes = _writable(source), {}, []
    def read_policy(identifier, optional=False):
        if identifier not in cache:
            observed = transport.request('GET', identifier + '?api-version=' + NETWORK_API)
            if optional and observed.get('statusCode') == 404:
                return observed, None
            value = _ok(observed, 'read data WAF policy')
            if str(value.get('id', '')).casefold() != identifier.casefold():
                raise ValueError('WAF response identifies another policy')
            cache[identifier] = (observed, value)
        return cache[identifier]
    _, base = read_policy(base_id)
    for profile in profiles:
        identifier = policy_id(config, profile['name'])
        owned_keys = ['g-base', *PROTOCOL_POLICY_KEYS[profile['scope']],
                      *SCOPED_DATA_POLICY_KEYS[profile['scope']]]
        owned_ids = {policy_id(config, key).casefold() for key in owned_keys}
        mapping, pool, backend = _scope_mapping(update, expected, profile['scope'])
        listener_name = 'listener-https' if profile['scope'] == 'gateway' else 'portal-https'
        listener = next(item['properties'] for item in update['properties']['httpListeners'] if item['name'] == listener_name)
        configured_host = config.get('gateway' if profile['scope'] == 'gateway' else 'portal', {}).get('publicHostName')
        if configured_host and listener.get('hostName') != configured_host:
            raise ValueError('Data WAF listener has another public host')
        listener_policy = listener.get('firewallPolicy', {}).get('id')
        allowed_listener_policies = {base_id.casefold()}
        if profile['scope'] == 'portal':
            allowed_listener_policies.add(policy_id(config, 'p-base').casefold())
        if listener_policy and listener_policy.casefold() not in allowed_listener_policies:
            raise ValueError('Data WAF listener uses another policy')
        sources, kept, existing_slot = [], [], None
        wanted = {path.casefold() for path in profile['paths']}
        for rule in mapping.setdefault('pathRules', []):
            current = rule['properties']
            if rule.get('name') == 'waf-data-' + profile['name']:
                if str(current.get('firewallPolicy', {}).get('id', '')).casefold() != identifier.casefold():
                    raise ValueError('Data WAF path rule name has another owner')
                if current.get('backendAddressPool') != pool or current.get('backendHttpSettings') != backend:
                    raise ValueError('Data WAF owned path has another backend')
                if current.get('rewriteRuleSet') and current['rewriteRuleSet'] != mapping.get('defaultRewriteRuleSet'):
                    raise ValueError('Data WAF owned path has a distinct rewrite')
                existing_slot = len(kept)
                continue
            overlap = [path for path in current.get('paths', []) if path.casefold() in wanted]
            if overlap:
                if current.get('backendAddressPool') != pool or current.get('backendHttpSettings') != backend:
                    raise ValueError('Data WAF exact path uses another backend')
                if current.get('rewriteRuleSet') and current['rewriteRuleSet'] != mapping.get('defaultRewriteRuleSet'):
                    raise ValueError('Data WAF exact path has a distinct rewrite; preserve it in a separate profile')
                current_id = current.get('firewallPolicy', {}).get('id', base_id)
                if current_id.casefold() != identifier.casefold():
                    if current_id.casefold() not in owned_ids:
                        raise ValueError('Data WAF exact path uses an external policy')
                    sources.append(read_policy(current_id)[1])
                remaining = [path for path in current['paths'] if path.casefold() not in wanted]
                if remaining:
                    kept.append({**rule, 'properties': {**current, 'paths': remaining}})
            else:
                kept.append(rule)
        observed, existing = read_policy(identifier, optional=True)
        desired = desired_policy(base, profile, config, sources + ([existing] if existing else []))
        # Idempotent data policies are generated from the manifest, never accumulated from themselves.
        if existing is None or canonical_policy(existing) != canonical_policy(desired):
            changes.append((identifier, desired, {'If-Match': _etag(observed)} if existing else {'If-None-Match': '*'}))
        props = {'paths': profile['paths'], 'firewallPolicy': {'id': identifier},
                 'backendAddressPool': pool, 'backendHttpSettings': backend}
        if mapping.get('defaultRewriteRuleSet'):
            props['rewriteRuleSet'] = copy.deepcopy(mapping['defaultRewriteRuleSet'])
        # Keep independent rules in place, but never leave an owned exact path
        # behind a wildcard that would intercept it. App Gateway paths ignore case.
        position = existing_slot if existing_slot is not None else 0
        for index, rule in enumerate(kept[:position]):
            if any('*' in pattern and any(fnmatch.fnmatchcase(path.casefold(), pattern.casefold())
                                         for path in profile['paths'])
                   for pattern in rule['properties'].get('paths', [])):
                position = index
                break
        kept.insert(position,
                    {'name': 'waf-data-' + profile['name'], 'properties': props})
        mapping['pathRules'] = kept
    deadline = monotonic() + timeout
    def apply(identifier, desired, headers, policy):
        path = identifier + '?api-version=' + NETWORK_API
        _ok(transport.request('PUT', path, desired, headers=headers), 'update data WAF', (200, 201, 202), require_body=False)
        expected_state = canonical_policy(desired) if policy else canonical_gateway(desired)
        notice = monotonic() + 45
        while monotonic() < deadline:
            value = _ok(transport.request('GET', path), 'read data WAF update')
            state = value.get('properties', {}).get('provisioningState')
            if state == 'Succeeded' and (canonical_policy(value) if policy else canonical_gateway(value)) == expected_state:
                return
            if state in ('Failed', 'Canceled', 'Deleting'):
                raise ValueError('Data WAF update failed')
            if monotonic() >= notice:
                progress('WAF: パス別ポリシーの反映を待っています。', flush=True)
                notice = monotonic() + 45
            sleep(10)
        # 期限直後の収束は同じ完全一致条件で一度だけ確認し、PUTは再送しません。
        value = _ok(transport.request('GET', path), 'read data WAF final update')
        state = value.get('properties', {}).get('provisioningState')
        matched = (canonical_policy(value) if policy else canonical_gateway(value)) == expected_state
        if state == 'Succeeded' and matched:
            return
        state_label = state if state in ('Succeeded', 'Updating', 'Creating', 'Failed', 'Canceled', 'Deleting') else 'Unknown'
        mismatch = 'false' if matched else 'true'
        outcome = 'failed' if state in ('Failed', 'Canceled', 'Deleting') else 'pending'
        raise ValueError(f'Data WAF update is {outcome}; provisioningState={state_label}; configurationMismatch={mismatch}')
    gateway_changed = canonical_gateway(update) != canonical_gateway(source)
    gateway_headers = {'If-Match': _etag(response)} if gateway_changed else None
    for identifier, desired, headers in changes:
        apply(identifier, desired, headers, True)
    if gateway_changed:
        apply(expected, update, gateway_headers, False)
    return {**receipt, 'status': 'reconciled', 'azureWrites': len(changes) + int(gateway_changed),
            'policyUpdates': len(changes), 'gatewayUpdated': gateway_changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--scope', choices=('gateway', 'portal', 'all'), default='all')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    from deployment.helpers.configuration import load_configuration
    from deployment.lifecycle import ArmTransport
    config = load_configuration(args.config)
    scopes = ('gateway', 'portal') if args.scope == 'all' else (args.scope,)
    manifest = load_manifest(args.manifest)
    result = reconcile(config, ArmTransport(config) if args.execute else None,
                       scopes=scopes, manifest=manifest, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
