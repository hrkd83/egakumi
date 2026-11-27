// Public TLS certificate and ACME state are isolated from the application vault.
// Blob storage only carries exclusive leases and public certificate metadata.
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param tenantId string
param vaultSkuName string
param vaultRetentionDays int
param vaultPurgeProtection bool
param portalVaultName string
param portalCertificateName string
param registryName string
param pullPrincipalId string
param tlsPrincipalId string
param portalPrincipalId string
param diagnosticSettingProperties object

resource tlsVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kvtls-${stem}'
  location: location
  tags: tags
  properties: union({
    tenantId: tenantId
    sku: { family: 'A', name: vaultSkuName }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: vaultRetentionDays
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'AzureServices', ipRules: [], virtualNetworkRules: [] }
    accessPolicies: []
  }, vaultPurgeProtection ? { enablePurgeProtection: true } : {})
}

resource state 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  name: 'st${replace(stem, '-', '')}cert'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { bypass: 'None', defaultAction: 'Deny', ipRules: [], virtualNetworkRules: [] }
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' = {
  parent: state
  name: 'default'
}
resource records 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobService
  name: 'certificate-jobs'
  properties: { publicAccess: 'None' }
}
resource portalCheckpoint 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobService
  name: 'portal-certificate-state'
  properties: { publicAccess: 'None' }
  dependsOn: [records]
}
// Blob leases require blobs/write. Containers themselves stay outside this role.
resource blobOperator 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(state.id, 'certificate-state-blob-operator')
  properties: {
    roleName: 'Certificate state blob operator ${resourceGroup().name}'
    description: 'Read or write existing certificate job state blobs and acquire their leases.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: []
      notActions: []
      dataActions: [
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write'
      ]
      notDataActions: []
    }]
  }
}
resource portalStateRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: portalCheckpoint
  name: guid(portalCheckpoint.id, portalPrincipalId, 'certificate-job-state')
  properties: {
    roleDefinitionId: blobOperator.id
    principalId: portalPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource tlsStateRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: records
  name: guid(records.id, tlsPrincipalId, 'certificate-job-state')
  properties: {
    roleDefinitionId: blobOperator.id
    principalId: tlsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, pullPrincipalId, 'AcrPull')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: pullPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource tlsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleId in [
  'a4417e6f-fecd-4de8-b567-7b0420556985'
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
]: {
  scope: tlsVault
  name: guid(tlsVault.id, tlsPrincipalId, roleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    principalId: tlsPrincipalId
    principalType: 'ServicePrincipal'
  }
}]

// Portal's worker can create/read its managed certificate but has no secrets permissions.
resource portalVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: portalVaultName }
resource portalKeyOperations 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(portalVault.id, 'portal-certificate-job')
  properties: {
    roleName: 'Portal certificate generation ${resourceGroup().name}'
    description: 'Create and read certificates in the application signing vault.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: []
      notActions: []
      dataActions: ['Microsoft.KeyVault/vaults/certificates/read', 'Microsoft.KeyVault/vaults/certificates/create/action']
      notDataActions: []
    }]
  }
}
resource portalOperationGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: portalVault
  name: guid(portalVault.id, portalPrincipalId, portalKeyOperations.id)
  properties: {
    roleDefinitionId: portalKeyOperations.id
    principalId: portalPrincipalId
    principalType: 'ServicePrincipal'
  }
}
module portalSigningKey 'portal-auth-key-operations.bicep' = {
  name: '${stem}-portal-job-key-operations'
  params: {
    vaultName: portalVaultName
    keyName: portalCertificateName
    principalId: portalPrincipalId
  }
}

resource vaultLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: tlsVault
  properties: diagnosticSettingProperties['vault-logs']
}

output vaultId string = tlsVault.id
output vaultName string = tlsVault.name
output storageAccountId string = state.id
output storageAccountName string = state.name
output storageUrl string = 'https://${state.name}.blob.${environment().suffixes.storage}'
output receiptContainerName string = records.name
output portalStateContainerName string = portalCheckpoint.name
