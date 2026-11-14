#!/usr/bin/env python3
"""Render reviewed wire types into the static Bicep WAF data manifest.

The catalog classifies input *types*, independently of model identifiers. Arrays
use the index-free dotted names emitted by Application Gateway CRS 3.2. Provider
acceptance and HTTP/WSS application validation are deliberately separate tests.
"""
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

ROOT = Path(__file__).resolve().parents[1]
CATALOG = 'apps/gateway/waf-input-contracts.json'
MANIFEST = 'apps/gateway/waf-data-exclusions.json'
DATA_TYPES = frozenset({'text', 'schema-json', 'tool-arguments', 'json-data', 'opaque', 'media-url', 'control'})
APP_GROUPS = frozenset({
    'REQUEST-930-APPLICATION-ATTACK-LFI', 'REQUEST-931-APPLICATION-ATTACK-RFI',
    'REQUEST-932-APPLICATION-ATTACK-RCE', 'REQUEST-933-APPLICATION-ATTACK-PHP',
    'REQUEST-941-APPLICATION-ATTACK-XSS', 'REQUEST-942-APPLICATION-ATTACK-SQLI',
})


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_contracts(root=ROOT):
    """Read a public, environment-independent catalog and validate every type."""
    data = _read(Path(root) / CATALOG)
    if data.get('version') != 1 or not isinstance(data.get('profiles'), list):
        raise ValueError('WAF input catalog requires version 1 and profiles')
    if set(data.get('groupSets', {}).get('ai-data', [])) != APP_GROUPS:
        raise ValueError('Semantic AI types may exclude only the six application attack groups')
    names = set()
    for profile in data['profiles']:
        if profile['name'] in names or profile['scope'] not in ('gateway', 'portal'):
            raise ValueError('Duplicate or invalid WAF input profile')
        names.add(profile['name'])
        if not profile.get('sources') or any(x not in data['sources'] for x in profile['sources']):
            raise ValueError('Every WAF input profile needs reviewed sources')
        list(_walk(profile['shape'], data))
        for value in profile.get('headers', {}).values():
            list(_walk(value, data))
        for override in profile.get('legacy', []):
            if override['rules'] not in data['legacyRuleSets']:
                raise ValueError('Unknown reviewed legacy rule set')
    for value in data['types'].values():
        list(_walk(value, data))
    return data


def _walk(node, catalog, prefix='', stack=()):
    """Expand object/array/union/ref composition, rejecting unclassified leaves."""
    if not isinstance(node, dict):
        raise ValueError('WAF wire type must be an object')
    if 'ref' in node:
        ref = node['ref']
        if set(node) != {'ref'} or ref in stack or ref not in catalog['types']:
            raise ValueError('Invalid or recursive WAF wire type reference')
        yield from _walk(catalog['types'][ref], catalog, prefix, (*stack, ref))
        return
    kind = node.get('kind')
    if kind == 'object':
        if not isinstance(node.get('properties'), dict):
            raise ValueError('WAF object needs explicit properties')
        for name, value in node['properties'].items():
            if not isinstance(name, str) or not name or '.' in name:
                # Literal dotted metadata keys are allowed only when explicitly
                # reviewed; the WAF emits the same dotted selector either way.
                if name != 'x-codex-turn-metadata':
                    raise ValueError('Invalid WAF wire property')
            yield from _walk(value, catalog, prefix + ('.' if prefix else '') + name, stack)
    elif kind == 'array':
        yield from _walk(node.get('items'), catalog, prefix, stack)
    elif kind == 'union':
        if not isinstance(node.get('variants'), list) or not node['variants']:
            raise ValueError('WAF union needs explicit alternatives')
        for value in node['variants']:
            yield from _walk(value, catalog, prefix, stack)
    elif kind == 'leaf':
        if node.get('data_type') not in DATA_TYPES or node.get('groups') not in (None, 'ai-data'):
            raise ValueError('Unclassified WAF input leaf')
        if node['data_type'] == 'control' and node.get('groups'):
            raise ValueError('Control fields cannot acquire semantic data exclusions')
        yield prefix, node
    else:
        raise ValueError('Unknown WAF wire type kind')


def _fields(profile, catalog):
    fields = []
    for header, shape in [(None, profile['shape']), *profile.get('headers', {}).items()]:
        for path, leaf in _walk(shape, catalog, header or ''):
            if not path:
                raise ValueError('Root-level unbounded data exclusions are not allowed')
            groups = catalog['groupSets'].get(leaf.get('groups'), [])
            variables = ('RequestArgValues', 'RequestArgKeys') if leaf.get('descendants') else (
                'RequestHeaderValues' if header else 'RequestArgValues',)
            for variable in variables:
                entry = {
                    'selector': path + ('.' if leaf.get('descendants') else ''),
                    'matchVariable': variable,
                    'selectorMatchOperator': 'StartsWith' if leaf.get('descendants') else 'Equals',
                    'data_type': leaf['data_type'], 'groups': list(groups),
                    'sources': list(profile['sources']), 'acceptance': profile.get('acceptance'),
                    'waf_applicable': True,
                }
                if entry not in fields:
                    fields.append(entry)
    return fields


def contract_fields(profile_name, root=ROOT):
    """Return classified WAF selectors, including controls with empty groups.

    Consumers must filter ``groups`` when checking generated data exclusions.
    Classification itself is not evidence that a provider accepts a feature.
    """
    catalog = load_contracts(root)
    profile = next((p for p in catalog['profiles'] if p['name'] == profile_name), None)
    if profile is None:
        raise ValueError('Unknown WAF input profile')
    return _fields(profile, catalog)


def _apis(root):
    return [_read(p) for p in sorted((Path(root) / 'apps/gateway/apim/apis').glob('*.json'))]


def _operation(api, binding):
    if (api.get('name') != binding['api'] or api.get('path') != binding.get('path')
            or api.get('apiType', 'http') != binding.get('apiType', 'http')):
        return None
    for op in api.get('operations', []):
        constraint = api.get('authorization', {}).get('operationConstraints', {}).get(op['name'], {})
        # API normalization makes the documented omitted JSON default explicit.
        # Accept that one canonical transformation, never an arbitrary MIME or
        # operation-kind change on a custom API sharing this public path.
        body_format = constraint.get('bodyFormat')
        if binding.get('bodyFormat') is None and body_format == 'json':
            body_format = None
        if (op['name'], op.get('method'), op.get('urlTemplate'), constraint.get('kind'),
                body_format, tuple(constraint.get('contentTypes') or [])) == (
                binding['operation'], binding['method'], binding['urlTemplate'], binding.get('kind'),
                binding.get('bodyFormat'), tuple(binding.get('contentTypes') or [])):
            return op, constraint
    return None


def profile_paths(profile, root=ROOT, apis=None):
    """Resolve finite model-bearing URLs only from canonical operation contracts."""
    if profile['scope'] == 'portal':
        return list(profile['portalPaths'])
    definitions = _apis(root) if apis is None else apis
    paths = []
    for binding in profile['bindings']:
        for api in definitions:
            if 'mcpPath' in binding:
                if api.get('name') == binding['api'] and api.get('mcp', {}).get('path') == binding['mcpPath']:
                    paths.append('/' + binding['mcpPath'].strip('/') + '/mcp')
                continue
            matched = _operation(api, binding)
            if not matched:
                continue
            _, constraint = matched
            path = '/' + api['path'].strip('/') + binding['urlTemplate']
            if binding.get('signedPattern'):
                paths.append(binding['signedPattern'])
            elif binding.get('wafPath'):
                paths.append(binding['wafPath'])
            elif '{deployment}' in path:
                paths.extend(path.replace('{deployment}', model) for model in constraint.get('allowedModels', [])
                             if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', model))
            elif re.fullmatch(r'/[A-Za-z0-9_./-]+', path):
                paths.append(path)
    return sorted(set(paths))


def _merge_groups(target, additions):
    for group in additions:
        existing = next((g for g in target if g['ruleGroupName'] == group['ruleGroupName']), None)
        if existing is None:
            target.append(copy.deepcopy(group))
        elif 'rules' not in existing or 'rules' not in group:
            existing.pop('rules', None)
        else:
            existing['rules'] = [{'ruleId': rule} for rule in sorted(
                {r['ruleId'] for r in existing['rules']} | {r['ruleId'] for r in group['rules']})]
    target.sort(key=lambda g: g['ruleGroupName'])


def generated_manifest(root=ROOT, apis=None):
    catalog = load_contracts(root)
    result = {'version': 2, 'profiles': []}
    for source in catalog['profiles']:
        entries = {}
        for field in _fields(source, catalog):
            if field['groups']:
                key = (field['matchVariable'], field['selectorMatchOperator'], field['selector'])
                entries.setdefault(key, [])
                _merge_groups(entries[key], [{'ruleGroupName': group} for group in field['groups']])
        # Compatibility exceptions are explicit, finite and independent of the
        # semantic six-group decision. They never turn control fields into data.
        for override in source.get('legacy', []):
            key = (override['variable'], override['operator'], override['selector'])
            entries.setdefault(key, [])
            _merge_groups(entries[key], catalog['legacyRuleSets'][override['rules']])
        profile = {'name': source['name'], 'scope': source['scope'],
                   'paths': profile_paths(source, root, apis), 'exclusions': []}
        for (variable, operator, selector), groups in sorted(entries.items()):
            profile['exclusions'].append({
                'matchVariable': variable, 'selectorMatchOperator': operator, 'selector': selector,
                'exclusionManagedRuleSets': [{'ruleSetType': 'OWASP', 'ruleSetVersion': '3.2',
                                             'ruleGroups': groups}],
            })
        if profile['paths']:
            if len(profile['exclusions']) > 200:
                raise ValueError('Rendered WAF policy exceeds the 200 exclusion limit')
            result['profiles'].append(profile)
    return result


def require_source_routes(apis, root=ROOT):
    """Keep parameter overrides consistent with the static Bicep route asset.

    Removing/subsetting models is safe. A new deployment-bearing route must be
    declared in the canonical API source and regenerated, rather than quietly
    leaving Bicep on a protocol-only policy while Python selects a data policy.
    This is a routing check; semantic exclusions never branch on model IDs.
    """
    canonical = {p['name']: set(p['paths']) for p in generated_manifest(root)['profiles']}
    selected = generated_manifest(root, apis=apis)
    for profile in selected['profiles']:
        if not set(profile['paths']) <= canonical.get(profile['name'], set()):
            raise ValueError('Declare new deployment routes in the canonical API definition and regenerate the WAF manifest')
    tuned = {path for paths in canonical.values() for path in paths if '*' not in path}
    matched = {path for profile in selected['profiles'] for path in profile['paths']}
    for api in apis:
        for operation in api.get('operations', []):
            path = '/' + str(api.get('path', '')).strip('/') + str(operation.get('urlTemplate', ''))
            constraint = api.get('authorization', {}).get('operationConstraints', {}).get(operation.get('name'), {})
            paths = [path.replace('{deployment}', m) for m in constraint.get('allowedModels', [])
                     if isinstance(m, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', m)] if '{deployment}' in path else [path]
            if any(value in tuned and value not in matched for value in paths):
                raise ValueError('A WAF-tuned route has a changed API input contract; review its canonical operation and MIME before deployment')
    return selected


def approved_group_scopes(root=ROOT):
    """Validation permissions derive from exactly the types used for rendering."""
    catalog = load_contracts(root)
    return {p['name']: {'scope': p['scope'], 'paths': frozenset(profile_paths(p, root)),
                        'fields': frozenset((f['matchVariable'], f['selectorMatchOperator'], f['selector'])
                                           for f in _fields(p, catalog) if f['groups'])}
            for p in catalog['profiles']}


def contract_inventory(root=ROOT):
    """All current model × operation × transport rows; model IDs are test inputs."""
    catalog = load_contracts(root)
    public_bindings = _read(Path(root) / 'apps/gateway/apim/public-bindings.json')['bindings']
    bindings = [(p['name'], b) for p in catalog['profiles'] for b in p.get('bindings', [])]
    result = []
    for api in _apis(root):
        for op in api.get('operations', []):
            constraint = api.get('authorization', {}).get('operationConstraints', {}).get(op['name'], {})
            profiles = sorted({name for name, b in bindings if 'operation' in b and _operation(api, b)
                               and b['operation'] == op['name']})
            transport = 'websocket' if api.get('apiType') == 'websocket' else 'http'
            frame = next((t for t in catalog['transportContracts'] if t['api'] == api['name']), None)
            protocol = any(_operation(api, b) and b['operation'] == op['name']
                           for b in catalog.get('protocolOperations', []))
            declared = bool(profiles or transport == 'websocket' and frame or protocol)
            if not declared:
                raise ValueError('Unreviewed published operation: ' + api['name'] + '/' + op['name'])
            models = list(constraint.get('allowedModels', []))
            if constraint.get('model'):
                models.append(constraint['model'])
            models.extend(model for profile in constraint.get('profiles', [])
                          for model in profile.get('allowedModels', []))
            models.extend(api.get('websocket', {}).get('allowedModels', []))
            models.extend(api.get('websocket', {}).get('session', {}).get('models', []))
            bound_model = api.get('websocket', {}).get('queryParameters', {}).get('model')
            if isinstance(bound_model, str) and bound_model.startswith('__') and bound_model.endswith('__'):
                bound_model = public_bindings.get(bound_model[2:-2])
            if bound_model:
                models.append(bound_model)
            models = sorted(set(models)) or [None]
            for model in models:
                result.append({'api': api['name'], 'operation': op['name'], 'model': model,
                               'method': op['method'], 'urlTemplate': op['urlTemplate'],
                               'transport': transport, 'bodyFormat': constraint.get('bodyFormat'),
                               'kind': constraint.get('kind'), 'profiles': profiles,
                               'api_version': constraint.get('apiVersion'),
                               'contentTypes': copy.deepcopy(constraint.get('contentTypes', [])),
                               'backendUrlTemplate': op.get('backendUrlTemplate'),
                               'waf_applicable': transport == 'http' or bool(profiles)})
    return result


def check_legacy_coverage(previous, current):
    """Return omitted route/selector/rule scopes (None means a complete group)."""
    omissions = []
    profiles = {p['name']: p for p in current['profiles']}
    for before in previous['profiles']:
        after = profiles.get(before['name'])
        if not after or before['scope'] != after['scope'] or not set(before['paths']) <= set(after['paths']):
            omissions.append(before['name'] + ': route scope')
            continue
        for old in before['entries']:
            candidates = [e for e in after['exclusions']
                          if e['matchVariable'] == old['variable'] and (
                              e['selectorMatchOperator'] == old['operator'] and e['selector'] == old['selector']
                              or e['selectorMatchOperator'] == 'StartsWith' and old['selector'].startswith(e['selector']))]
            groups = {}
            for entry in candidates:
                for group in entry['exclusionManagedRuleSets'][0]['ruleGroups']:
                    name = group['ruleGroupName']
                    if 'rules' not in group:
                        groups[name] = None
                    elif name not in groups or groups[name] is not None:
                        groups.setdefault(name, set()).update(r['ruleId'] for r in group['rules'])
            for name, rules in old['groups'].items():
                if name not in groups or (groups[name] is not None and (
                        rules is None or not set(rules) <= groups[name])):
                    omissions.append(before['name'] + ':' + old['selector'] + ':' + name)
    return omissions


def render(root=ROOT, *, check=False):
    # Inventory completeness is a separate gate from exclusion generation.
    contract_inventory(root)
    value = generated_manifest(root)
    path = Path(root) / MANIFEST
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    if check:
        if path.read_text(encoding='utf-8') != rendered:
            raise ValueError('WAF manifest drift: run scripts/waf_contracts.py --write and review the diff')
    else:
        path.write_text(rendered, encoding='utf-8')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    try:
        value = render(check=args.check)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps({'status': 'checked' if args.check else 'rendered', 'profiles': len(value['profiles'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
