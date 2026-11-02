// Portalの実行IDです。ACR取得とSQL接続に共用し、既存のID名は維持します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object = resourceGroup().tags

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-portal-pull'
  location: location
  tags: tags
}
output id string = pullIdentity.id
output principalId string = pullIdentity.properties.principalId
output identity object = {
  id: pullIdentity.id
  name: pullIdentity.name
  principalId: pullIdentity.properties.principalId
  clientId: pullIdentity.properties.clientId
}
