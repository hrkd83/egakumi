"""保守時に使用するロール定義を構築したサブスクリプションと照合する。"""
from __future__ import annotations

import re


ROLE_KEYS = ('appLogs', 'appExec', 'environmentLogs', 'jobLogs')
GUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


def management_maintenance_inputs(config, metadata):
    """不足・別subscriptionの出力では停止し、IDの推定や代替を行わない。"""
    if not isinstance(metadata, dict):
        raise ValueError('Current Core management maintenance output is required; complete the operations upgrade or current Core deployment')
    roles = metadata.get('roleDefinitionIds')
    if not isinstance(roles, dict):
        raise ValueError('Core management maintenance role definitions are required')
    scope = '/subscriptions/' + config['subscriptionId'] + '/providers/Microsoft.Authorization/roleDefinitions/'
    pattern = re.escape(scope) + GUID
    selected = {}
    for key in ROLE_KEYS:
        role = roles.get(key)
        if (not isinstance(role, str) or not re.fullmatch(pattern, role, re.IGNORECASE)
                or role.rsplit('/', 1)[-1].lower() == '00000000-0000-0000-0000-000000000000'):
            raise ValueError('Core management maintenance role output is missing or outside the selected subscription: ' + key)
        selected[key] = role
    if len({role.lower() for role in selected.values()}) != len(ROLE_KEYS):
        raise ValueError('Core management maintenance roles must identify their distinct approved definitions')
    return {'roleDefinitionIds': selected}
