// アプリ実行IDには既存Key Vault、実行・初期化IDには既存ACRの組み込み権限を付与します。
targetScope = 'resourceGroup'
param vaultName string
param registryName string
param runtimeIdentity object
param schemaIdentity object
@secure()
@description('初回に生成した内部認証鍵。空の場合は既存値を更新しません。')
param brokerKey string = ''
@secure()
param credentialIdentitySecret string = ''
param identityConfigured bool = false
param oboKeyName string = ''
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource key 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(brokerKey)) {
  parent: vault
  name: 'core-credential-broker-key'
  properties: { value: brokerKey, attributes: { enabled: true } }
}
// CM用core-credential-*と異なる名前空間で、本人確認の資格情報を保持します。
resource identityKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (identityConfigured) {
  parent: vault
  name: 'core-oidc-identity'
  properties: { value: credentialIdentitySecret, attributes: { enabled: true } }
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
resource oboKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = { parent: vault, name: oboKeyName }
resource oboSigner 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (!empty(oboKeyName)) {
  name: guid(resourceGroup().id, 'gateway-graph-obo-signer')
  properties: {
    roleName: 'Gateway Graph OBO signer ${resourceGroup().name}'
    description: 'Sign Entra client assertions with the Gateway Graph OBO key.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: [], notActions: [], dataActions: ['Microsoft.KeyVault/vaults/keys/sign/action'], notDataActions: [] }]
  }
}
resource oboAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(oboKeyName)) {
  scope: oboKey
  name: guid(oboKey.id, runtimeIdentity.principalId, oboSigner!.id)
  properties: { roleDefinitionId: oboSigner!.id, principalId: runtimeIdentity.principalId, principalType: 'ServicePrincipal' }
}
output keySecretUri string = '${vault.properties.vaultUri}secrets/core-credential-broker-key'
