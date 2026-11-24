// 公開証明書を先行登録した専用鍵へ、署名と鍵metadata読取りだけを付与します。
targetScope = 'resourceGroup'
param vaultName string
param keyName string
param authIdentity object
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource key 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = { parent: vault, name: keyName }
resource signerRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'portal-auth-key-sign')
  properties: {
    roleName: 'Portal authentication key signer ${resourceGroup().name}'
    description: 'Portal authentication RSA signing only; no secret or private-key export access.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: [], notActions: [], dataActions: ['Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/sign/action'], notDataActions: [] }]
  }
}
resource signerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(key.id, authIdentity.principalId, signerRole.id)
  scope: key
  properties: { roleDefinitionId: signerRole.id, principalId: authIdentity.principalId, principalType: 'ServicePrincipal' }
}
