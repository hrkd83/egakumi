// アプリ実行IDには既存Key Vault、実行・初期化IDには既存ACRの組み込み権限を付与します。
targetScope = 'resourceGroup'
param vaultName string
param registryName string
param runtimeIdentity object
param schemaIdentity object
@secure()
@description('初回に生成した内部認証鍵。空の場合は既存値を更新しません。')
param brokerKey string = ''
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource key 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(brokerKey)) {
  parent: vault
  name: 'core-credential-broker-key'
  properties: { value: brokerKey, attributes: { enabled: true } }
}
var kvSecretUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
// 既存配備の割り当て名を維持し、同じ権限を重複作成しません。
resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, runtimeIdentity.principalId, kvSecretUser)
  properties: { roleDefinitionId: kvSecretUser, principalId: runtimeIdentity.principalId, principalType: 'ServicePrincipal' }
}
resource registryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for identity in [runtimeIdentity, schemaIdentity]: {
  scope: registry
  name: guid(registry.id, identity.principalId, acrPull)
  properties: { roleDefinitionId: acrPull, principalId: identity.principalId, principalType: 'ServicePrincipal' }
}]
output keySecretUri string = '${vault.properties.vaultUri}secrets/core-credential-broker-key'
