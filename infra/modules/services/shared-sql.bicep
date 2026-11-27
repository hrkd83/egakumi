// PortalとGatewayが共用するSQLサーバーを配置します。各DBはアプリ配備工程が所有します。
// 通信はPrivate Endpoint、認証はEntraを使います。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param tenantId string
param schemaIdentity object
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
output id string = server.id
output host string = server.properties.fullyQualifiedDomainName
