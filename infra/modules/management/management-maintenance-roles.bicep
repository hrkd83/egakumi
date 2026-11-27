// 担当者が保守時に使うカスタムロールを定義します。
// 割当は作業ごとに、対象資源のスコープで担当者本人に行います。
targetScope = 'subscription'

@minLength(1)
@description('構築した製品名と環境名の共通名。ロール定義の安定した名前に使う内部入力です。')
param resourceStem string

// Appログとexecは別の権限です。exec側はログ用のread/token権限を重複させません。
// 環境tokenはDev API用で、ログ専用の利用範囲を保証するものではありません。
var definitions = [
  {
    key: 'appLogs'
    name: 'ACA app log reader'
    description: 'Read constructed Container App metadata and stream its console or system logs.'
    actions: [
      'Microsoft.App/containerApps/read'
      'Microsoft.App/containerApps/revisions/read'
      'Microsoft.App/containerApps/revisions/replicas/read'
      'Microsoft.App/containerApps/getAuthToken/action'
    ]
    dataActions: ['Microsoft.App/containerApps/logstream/action']
  }
  {
    key: 'appExec'
    name: 'ACA app exec operator'
    description: 'Execute commands in a constructed Container App. Metadata and token access use the separate log reader role.'
    actions: []
    dataActions: ['Microsoft.App/containerApps/exec/action']
  }
  {
    key: 'environmentLogs'
    name: 'ACA environment token reader'
    description: 'Read a constructed Container Apps environment and obtain its Dev API token. The token is not guaranteed to be log-only.'
    actions: [
      'Microsoft.App/managedEnvironments/read'
      'Microsoft.App/managedEnvironments/getAuthToken/action'
    ]
    dataActions: []
  }
  {
    key: 'jobLogs'
    name: 'ACA job log reader'
    description: 'Read a constructed Container Apps Job and its child metadata, obtain its token and stream execution logs.'
    actions: [
      'Microsoft.App/jobs/read'
      'Microsoft.App/jobs/*/read'
      'Microsoft.App/jobs/getAuthToken/action'
    ]
    dataActions: ['Microsoft.App/jobs/logstream/action']
  }
]

resource roles 'Microsoft.Authorization/roleDefinitions@2022-04-01' = [for definition in definitions: {
  name: guid(subscription().id, resourceStem, 'management-maintenance', definition.key)
  properties: {
    roleName: '${resourceStem} management ${definition.name}'
    description: definition.description
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [{
      actions: definition.actions
      notActions: []
      dataActions: definition.dataActions
      notDataActions: []
    }]
  }
}]

output roleDefinitionIds object = {
  appLogs: roles[0].id
  appExec: roles[1].id
  environmentLogs: roles[2].id
  jobLogs: roles[3].id
}
