"""実環境から独立した、構築stateの管理VM保守メタデータ。"""


def maintenance_metadata(subscription_id):
    scope = '/subscriptions/' + subscription_id + '/providers/Microsoft.Authorization/roleDefinitions/'
    return {
        'principalId': '55555555-5555-4555-8555-555555555555',
        'roleDefinitionIds': {key: scope + f'{index}0000000-0000-4000-8000-000000000000'
                              for index, key in enumerate(('appLogs', 'appExec', 'environmentLogs', 'jobLogs'), 1)},
    }


def client_inventory(config):
    """基盤の配備出力に相当する独立した合成inventory。"""
    base = '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/'
    service = base + config['resourceGroupNames']['svc'] + '/providers/'
    monitor = base + config['resourceGroupNames']['monitor'] + '/providers/'
    return {
        'schemaVersion': 1, 'subscriptionId': config['subscriptionId'], 'azureTenantId': config['azureTenantId'],
        'managementPrincipalId': maintenance_metadata(config['subscriptionId'])['principalId'],
        'managementVmId': base + config['resourceGroupNames']['maint'] + '/providers/Microsoft.Compute/virtualMachines/vm-' + config['resourceStem'] + '-mgmt',
        'applications': [service + 'Microsoft.App/containerApps/ca-' + config['resourceStem'] + '-portal'],
        'jobs': [], 'sqlDatabases': [],
        'environments': [service + 'Microsoft.App/managedEnvironments/cae-' + config['resourceStem'] + suffix
                         for suffix in ('', '-broker')],
        'workspace': {'id': monitor + 'Microsoft.OperationalInsights/workspaces/law-' + config['resourceStem'],
                      'customerId': '66666666-6666-4666-8666-666666666666'},
        'auditStorage': [{'id': monitor + 'Microsoft.Storage/storageAccounts/st' + config['resourceStem'].replace('-', '') + suffix,
                          'containers': ['insights-logs-audit']} for suffix in ('plat', 'fndry')],
    }


def client_core_outputs(config):
    """inventoryと同じ構築資源を返す、監視・VM・監査の合成配備出力。"""
    inventory = client_inventory(config)
    accounts = inventory['auditStorage']
    broker = inventory['environments'][1]
    return {
        'managementClientInventory': inventory,
        'brokerPlatform': {'environmentId': broker, 'environmentName': broker.rsplit('/', 1)[-1]},
        'managementVm': {'id': inventory['managementVmId'], 'principalId': inventory['managementPrincipalId']},
        'monitoring': {'workspaceId': inventory['workspace']['id'], 'workspaceCustomerId': inventory['workspace']['customerId']},
        'auditStorage': {'collectPlatform': {'id': accounts[0]['id']}, 'collectFoundry': {'id': accounts[1]['id']},
                         'containerIds': [account['id'] + '/blobServices/default/containers/' + name
                                          for account in accounts for name in account['containers']]},
    }
