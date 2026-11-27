// 購読管理とプロファイル読取は、アプリごとに独立した実行IDを使います。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param registryName string

resource registry 'Microsoft.ContainerRegistry/registries@2023-01-01' existing = { name: registryName }
var services = ['sub-admin', 'sub-profile']
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for service in services: {
  name: 'id-${stem}-${service}'
  location: location
  tags: tags
}]

resource imageAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in range(0, length(services)): {
  name: guid(registry.id, identities[index].id, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: acrPull
    principalId: identities[index].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

output managementIdentity object = {
  id: identities[0].id
  name: identities[0].name
  clientId: identities[0].properties.clientId
  principalId: identities[0].properties.principalId
}
output profileIdentity object = {
  id: identities[1].id
  name: identities[1].name
  clientId: identities[1].properties.clientId
  principalId: identities[1].properties.principalId
}
