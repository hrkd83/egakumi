// 購読管理サービスが構築済みAPIMの購読を扱うための権限です。
targetScope = 'resourceGroup'

param apimName string
param managementIdentity object

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
resource subscriptionRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'portal-subscription-management')
  properties: {
    roleName: 'Portal subscription management ${resourceGroup().name}'
    description: 'Manage subscriptions and check product membership in the deployed API Management service.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: [
        'Microsoft.ApiManagement/service/subscriptions/read'
        'Microsoft.ApiManagement/service/subscriptions/write'
        'Microsoft.ApiManagement/service/subscriptions/delete'
        'Microsoft.ApiManagement/service/subscriptions/listSecrets/action'
        'Microsoft.ApiManagement/service/products/read'
        'Microsoft.ApiManagement/service/apis/products/read'
      ]
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}

resource subscriptionManagement 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, managementIdentity.principalId, subscriptionRole.id)
  scope: apim
  properties: {
    roleDefinitionId: subscriptionRole.id
    principalId: managementIdentity.principalId
    principalType: 'ServicePrincipal'
  }
}

output apimResourceId string = apim.id
