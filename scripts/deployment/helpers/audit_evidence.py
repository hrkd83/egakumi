"""本文監査の配備、公開、撤去で同じ保存先のResource IDを使う。"""


def evidence_container_id(config: dict) -> str:
    account_name = 'st' + config['resourceStem'].replace('-', '') + 'evid'
    return (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['monitor']}"
            f'/providers/Microsoft.Storage/storageAccounts/{account_name}/blobServices/default/containers/payload')


def evidence_policy_id(config: dict) -> str:
    return evidence_container_id(config) + '/immutabilityPolicies/default'
