// Private Endpointの構築後に、既存の共有SQLサーバーの接続policyだけを設定します。
targetScope = 'resourceGroup'

param sqlServerName string

resource server 'Microsoft.Sql/servers@2023-08-01' existing = {
  name: sqlServerName
}

resource connectionPolicy 'Microsoft.Sql/servers/connectionPolicies@2023-08-01' = {
  parent: server
  name: 'default'
  properties: { connectionType: 'Redirect' }
}

output id string = connectionPolicy.id
