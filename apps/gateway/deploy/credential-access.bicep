// 共通ログイン窓口が、指定した Credential Manager provider 内に本人接続を作る権限です。
targetScope = 'resourceGroup'
param apimName string
param providerName string
param apimPrincipalId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
resource provider 'Microsoft.ApiManagement/service/authorizationProviders@2024-05-01' existing = {
  parent: apim
  name: providerName
}
// 接続作成・利用権限の設定・ログイン URL の発行に、組み込みの API Management Service Contributor を使います。
// このロールは対象の Credential Manager provider だけに割り当てます。
var apiManagementContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '312a565d-c81f-4fd8-895a-4e21e48d571c')
resource loginRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: provider
  name: guid(provider.id, apimPrincipalId, apiManagementContributorRoleId)
  properties: { roleDefinitionId: apiManagementContributorRoleId, principalId: apimPrincipalId, principalType: 'ServicePrincipal' }
}
