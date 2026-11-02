// Portalがグループ所有の購読を管理するための、既存APIM単体への権限です。
// 組み込みロールを使用します。このロールには購読以外のAPIM設定変更権限も含まれます。
targetScope = 'resourceGroup'

param apimName string
param runtimeIdentity object

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
var contributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '312a565d-c81f-4fd8-895a-4e21e48d571c')

resource subscriptionManagement 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, runtimeIdentity.principalId, contributor)
  scope: apim
  properties: {
    roleDefinitionId: contributor
    principalId: runtimeIdentity.principalId
    principalType: 'ServicePrincipal'
  }
}

output apimResourceId string = apim.id
