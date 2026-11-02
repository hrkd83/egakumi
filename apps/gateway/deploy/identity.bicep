// Gatewayアプリだけが使用する実行IDです。共有スキーマ管理IDはCoreから参照します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
resource runtime 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-credential-broker'
  location: location
  tags: tags
}
output identity object = { id: runtime.id, name: runtime.name, principalId: runtime.properties.principalId, clientId: runtime.properties.clientId }
