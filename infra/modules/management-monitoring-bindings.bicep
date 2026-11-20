// 管理VMから構築したWorkspaceの保存ログを読むための割当だけを作成します。
// 収集設定・保持・access mode・保護テーブルの設定は変更しません。
targetScope = 'resourceGroup'

@minLength(1)
@description('構築された管理VM自身のSystemAssigned principalId。')
param principalId string
@minLength(1)
@description('このRGに構築されたLog Analytics Workspace名。customerIdではありません。')
param workspaceName string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = { name: workspaceName }
var logAnalyticsDataReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3b03c2da-16b3-4a49-8834-0f8130efdd3b')

resource grant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, principalId, logAnalyticsDataReader)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logAnalyticsDataReader
  }
}
