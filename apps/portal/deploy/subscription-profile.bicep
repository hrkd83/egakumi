// Portal配備が所有する接続情報です。個々の購読の値はSQLだけに保存します。
targetScope = 'resourceGroup'
param apimName string
param profileFqdn string
param vaultName string
#disable-next-line secure-secrets-in-params
param profileSecretUri string
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource profileSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-subscription-profile-key'
}
var secretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource apimProfileKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(profileSecret.id, apim.id, secretsUser)
  scope: profileSecret
  properties: { principalId: apim.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: secretsUser }
}
resource url 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'core-subscription-profile-url'
  properties: {
    displayName: 'core-subscription-profile-url'
    secret: false
    value: 'https://${profileFqdn}/api/internal/subscription-profile'
  }
}
resource key 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'core-subscription-profile-secret'
  properties: {
    displayName: 'core-subscription-profile-secret'
    secret: true
    keyVault: { secretIdentifier: profileSecretUri }
  }
  dependsOn: [apimProfileKeyAccess]
}
