// The receipt-confirmed backing secret already exists when this module runs.
targetScope = 'resourceGroup'
param vaultName string
param certificateName string
param principalId string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource certificateSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: certificateName
}
var roleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource appGatewayReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: certificateSecret
  name: guid(certificateSecret.id, principalId, roleId)
  properties: { roleDefinitionId: roleId, principalId: principalId, principalType: 'ServicePrincipal' }
}
