// イメージを保管するACRと、そのACRを読むための権限をサービスRGへ配置します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object = resourceGroup().tags
param pullIdentityId string
param pullIdentityPrincipalId string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  #disable-next-line BCP334
  name: 'acr${replace(stem, '-', '')}'
  location: location
  tags: tags
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Disabled'
    networkRuleBypassOptions: 'None'
    dataEndpointEnabled: true
  }
}
// ID用RGで作成したIDに、このACRのイメージを取得するAcrPull権限を付与します。
resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, pullIdentityId, 'AcrPull')
  scope: registry
  properties: {
    principalId: pullIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

output id string = registry.id
output name string = registry.name
output server string = registry.properties.loginServer
