// 発行は既存の管理VM certificates officerを使い、対応鍵の照合・旧版無効化を追加します。
targetScope = 'resourceGroup'
param vaultName string
param keyName string
param principalId string
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource key 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = { parent: vault, name: keyName }
resource operatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'portal-auth-key-operations')
  properties: {
    roleName: 'Portal authentication key operations ${resourceGroup().name}'
    description: 'Read the authentication public key and disable an old key version.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: [], notActions: [], dataActions: ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/write'], notDataActions: [] }]
  }
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(key.id, principalId, operatorRole.id)
  scope: key
  properties: { roleDefinitionId: operatorRole.id, principalId: principalId, principalType: 'ServicePrincipal' }
}
