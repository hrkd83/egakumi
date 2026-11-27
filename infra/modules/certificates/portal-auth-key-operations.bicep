// Portal証明書Jobによる対応鍵の照合・旧版無効化を、指定した鍵に限定します。
targetScope = 'resourceGroup'
param vaultName string
param keyName string
param principalId string
param allowDisable bool = true
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource key 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = { parent: vault, name: keyName }
var purpose = allowDisable ? 'portal-auth-key-operations' : 'gateway-obo-public-key-reader'
resource operatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, purpose)
  properties: {
    roleName: allowDisable ? 'Portal authentication key operations ${resourceGroup().name}' : 'Gateway OBO public key reader ${resourceGroup().name}'
    description: allowDisable ? 'Read the authentication public key and disable an old key version.' : 'Read the public key for Gateway OBO certificate issuance.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: [], notActions: [], dataActions: concat(['Microsoft.KeyVault/vaults/keys/read'], allowDisable ? ['Microsoft.KeyVault/vaults/keys/update/action'] : []), notDataActions: [] }]
  }
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(key.id, principalId, operatorRole.id)
  scope: key
  properties: { roleDefinitionId: operatorRole.id, principalId: principalId, principalType: 'ServicePrincipal' }
}
