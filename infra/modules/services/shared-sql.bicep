// PortalとGatewayが共用するSQLサーバーを配置します。各DBはアプリ配備工程が所有します。
// 通信はPrivate Endpoint、認証はEntraを使います。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param tenantId string
param schemaIdentity object
param diagnosticSettingProperties object
resource server 'Microsoft.Sql/servers@2023-08-01' = {
  name: 'sql-${stem}'
  location: location
  tags: tags
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Application'
      login: schemaIdentity.name
      sid: schemaIdentity.principalId
      tenantId: tenantId
      azureADOnlyAuthentication: true
    }
  }
}
// Proxy接続でSQL通信を1433番に固定し、必要な宛先だけをネットワークで許可します。
resource connectionPolicy 'Microsoft.Sql/servers/connectionPolicies@2023-08-01' = {
  parent: server
  name: 'default'
  properties: { connectionType: 'Proxy' }
}

// サーバー監査の診断は組み込みmasterを介して、PortalとBroker両方のDBに適用します。
resource master 'Microsoft.Sql/servers/databases@2023-08-01' existing = {
  parent: server
  name: 'master'
}
resource sqlDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: master
  name: 'core-audit'
  properties: diagnosticSettingProperties['sql-logs']
  dependsOn: [server]
}
resource auditing 'Microsoft.Sql/servers/auditingSettings@2025-01-01' = {
  parent: server
  name: 'default'
  properties: {
    state: 'Enabled'
    isAzureMonitorTargetEnabled: true
    auditActionsAndGroups: [
      'SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP'
      'FAILED_DATABASE_AUTHENTICATION_GROUP'
      'DATABASE_PRINCIPAL_CHANGE_GROUP'
      'DATABASE_ROLE_MEMBER_CHANGE_GROUP'
      'DATABASE_PERMISSION_CHANGE_GROUP'
    ]
  }
  dependsOn: [sqlDiagnostics]
}
output id string = server.id
output host string = server.properties.fullyQualifiedDomainName
