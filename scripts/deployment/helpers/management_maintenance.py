"""構築した管理VMと保守ロールの出力を、アプリ配備の内部入力へ渡す。"""
from __future__ import annotations

import re


ROLE_KEYS = ('appLogs', 'appExec', 'environmentLogs', 'jobLogs')
GUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


def management_maintenance_inputs(config, metadata):
    """不足・別subscriptionの出力では停止し、IDの推定や代替を行わない。"""
    if not isinstance(metadata, dict):
        raise ValueError('Current Core management maintenance output is required; complete the operations upgrade or current Core deployment')
    principal = metadata.get('principalId')
    roles = metadata.get('roleDefinitionIds')
    if (not isinstance(principal, str) or not re.fullmatch(GUID, principal)
            or principal.lower() == '00000000-0000-0000-0000-000000000000'
            or not isinstance(roles, dict)):
        raise ValueError('Core management maintenance identity or role output is incomplete')
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
    result = {'principalId': principal, 'roleDefinitionIds': selected}
    if 'sqlClientId' in metadata:
        client_id = metadata['sqlClientId']
        if (not isinstance(client_id, str) or not re.fullmatch(GUID, client_id)
                or client_id.lower() == '00000000-0000-0000-0000-000000000000'):
            raise ValueError('The verified management VM SQL client identity is invalid')
        result['sqlClientId'] = client_id
    return result
