// 共通SQLサーバーのEntra管理者となるスキーマ更新用IDです。アプリ実行用IDとは分離します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
resource schema 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-credential-schema'
  location: location
  tags: tags
}
output schema object = { id: schema.id, name: schema.name, principalId: schema.properties.principalId, clientId: schema.properties.clientId }
