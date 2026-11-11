"""Keep WAF inspection independent from AI request acceptance on finite paths."""
from __future__ import annotations

import copy
import json
import re

from .paths import REPOSITORY
from .portal_gateway import _writable_policy


AI_BODY_SETTINGS = {
    "requestBodyCheck": True,
    "maxRequestBodySizeInKb": 2000,
    "requestBodyInspectLimitInKB": 2000,
    "requestBodyEnforcement": False,
    "fileUploadEnforcement": False,
}


def ai_body_settings(settings):
    """Inspect the supported prefix without making it an AI payload size cap."""
    return {**copy.deepcopy(settings), **AI_BODY_SETTINGS}


def portal_document_profile(profile):
    """Raw MIME/body adjustments belong only to the one Portal upload path."""
    return (profile.get("name") == "p-document-upload" and profile.get("scope") == "portal"
            and profile.get("paths") == ["/api/tools/documents"])


def managed_ai_paths(config):
    """Return only published exact Gateway paths with managed AI body policies.

    This helper uses raw API contracts and has no configuration-loader dependency.
    Authentication, administration, custom and unexpanded routes never receive the
    standard AI exception merely because they share an API prefix.
    """
    manifest = json.loads((REPOSITORY / "apps/gateway/waf-data-exclusions.json").read_text(encoding="utf-8"))
    standard = {path for profile in manifest["profiles"] if profile["scope"] == "gateway"
                for path in profile["paths"]}
    selected = set()
    for api in config.get("apiDefinitions", []):
        prefix = api.get("path", "")
        if (not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", prefix)
                or any(part in ("", ".", "..") for part in prefix.split("/"))):
            continue
        mcp_path = api.get("mcp", {}).get("path")
        if isinstance(mcp_path, str):
            mcp_path = "/" + mcp_path.strip("/") + "/mcp"
            if mcp_path in standard:
                selected.add(mcp_path)
        constraints = api.get("authorization", {}).get("operationConstraints", {})
        for operation in api.get("operations", []):
            route = operation.get("urlTemplate", "")
            constraint = constraints.get(operation.get("name"), {})
            path = "/" + prefix + route
            candidates = [path]
            if "{deployment}" in path:
                candidates = [path.replace("{deployment}", model) for model in constraint.get("allowedModels", [])
                              if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", model)]
            selected.update(value for value in candidates
                            if re.fullmatch(r"/[A-Za-z0-9_./-]+", value) and value in standard)
            if operation.get("method") != "POST":
                continue
            if (api.get("name") == "document-analysis-v1" and prefix == "tools/documents/v1"
                    and operation.get("name") == "analyze_document" and route == "/analyze"
                    and constraint.get("kind") == "documentAnalysis" and constraint.get("bodyFormat") == "binary"):
                selected.add(path)
            speech = {"tts": ("/synthesize", "text", "application/ssml+xml"),
                      "stt": ("/recognize", "binary", "audio/wav")}
            expected = speech.get(operation.get("name"))
            if (api.get("name") == "azure-speech-v1" and expected
                    and (route, constraint.get("bodyFormat"), constraint.get("contentTypes"))
                    == (expected[0], expected[1], [expected[2]])
                    and constraint.get("kind") == "registeredOperation"):
                selected.add(path)
    return selected


SPEECH_RULE_ID = "920420"
RAW_CONTROLS = REPOSITORY / "apps/gateway/waf-raw-body-controls.json"


def speech_content_policy(source):
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


def raw_body_policy(source):
    """Allow opaque body bytes while retaining traversal checks outside the body.

    XML/raw body field exclusions are unsupported. Only 930110 is adjusted;
    930100 and every other managed rule remain enabled. These controls cover
    literal and common percent-encoded traversal, not every CRS normalization.
    """
    desired = speech_content_policy(source)
    properties = desired['properties']
    settings = properties.get('policySettings', {})
    if settings.get('mode') != 'Prevention' or settings.get('requestBodyCheck') is not True:
        raise ValueError('Raw data WAF requires Prevention mode and body inspection')
    properties['policySettings'] = ai_body_settings(settings)
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
