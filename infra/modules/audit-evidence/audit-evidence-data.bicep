targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param relayPrincipalId string
param workerPrincipalId string
@allowed(['Standard', 'Premium'])
param eventHubsSku string
@minValue(1)
@maxValue(146000)
param payloadRetentionDays int
@allowed(['Disabled', 'Locked'])
param payloadImmutabilityMode string = 'Disabled'
param manageAuditImmutabilityPolicies bool = true
param retainedAuditImmutabilityPolicyIds array = []

var compactStem = replace(stem, '-', '')
var senderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2b629674-e913-4c01-ae53-ef4638d8f975')
var receiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde')
var checkpointRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: 'ehns-${stem}-audit'
  location: location
  tags: tags
  sku: { name: eventHubsSku, tier: eventHubsSku, capacity: 1 }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
  }
}

resource namespaceRules 'Microsoft.EventHub/namespaces/networkRuleSets@2024-01-01' = {
  parent: namespace
  name: 'default'
  properties: {
    defaultAction: 'Deny'
    publicNetworkAccess: 'Disabled'
    trustedServiceAccessEnabled: false
    ipRules: []
    virtualNetworkRules: []
  }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: 'payload'
  properties: {
    partitionCount: 4
    retentionDescription: { cleanupPolicy: 'Delete', retentionTimeInHours: 168 }
  }
}

resource consumerGroup 'Microsoft.EventHub/namespaces/eventhubs/consumerGroups@2024-01-01' = {
  parent: hub
  name: 'archive'
}

resource relayPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(hub.id, relayPrincipalId, senderRole)
  scope: hub
  properties: { principalId: relayPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: senderRole }
}

// The SDK queries partition properties at the hub scope as well as receiving.
resource workerSubscriber 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(hub.id, workerPrincipalId, receiverRole)
  scope: hub
  properties: { principalId: workerPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: receiverRole }
}

var privateStorage = {
  supportsHttpsTrafficOnly: true
  minimumTlsVersion: 'TLS1_2'
  allowBlobPublicAccess: false
  allowSharedKeyAccess: false
  defaultToOAuthAuthentication: true
  publicNetworkAccess: 'Disabled'
  networkAcls: { defaultAction: 'Deny', bypass: 'None', ipRules: [], virtualNetworkRules: [] }
}

resource evidence 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${compactStem}evid'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  tags: tags
  properties: privateStorage
}

resource checkpoints 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${compactStem}ckpt'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  tags: tags
  properties: privateStorage
}

resource evidenceBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: evidence
  name: 'default'
  properties: { isVersioningEnabled: false }
}

resource checkpointBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: checkpoints
  name: 'default'
  properties: { isVersioningEnabled: false, deleteRetentionPolicy: { enabled: false } }
}

resource evidenceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: evidenceBlob
  name: 'payload'
  properties: { publicAccess: 'None' }
}

resource checkpointContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: checkpointBlob
  name: 'checkpoints'
  properties: { publicAccess: 'None' }
}

resource evidenceRetention 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = if (payloadImmutabilityMode == 'Disabled') {
  parent: evidence
  name: 'default'
  properties: {
    policy: {
      rules: [{
        name: 'expire-evidence'
        enabled: true
        type: 'Lifecycle'
        definition: {
          filters: { blobTypes: ['blockBlob'], prefixMatch: ['${evidenceContainer.name}/'] }
          actions: { baseBlob: { delete: { daysAfterCreationGreaterThan: payloadRetentionDays } } }
        }
      }]
    }
  }
}

var evidencePolicyId = '${evidenceContainer.id}/immutabilityPolicies/default'

// Lock is a separate ETag-protected action in the Core lifecycle. Existing
// Locked policies are discovered and excluded from subsequent policy PUTs.
resource evidenceImmutability 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = if (payloadImmutabilityMode == 'Locked' && manageAuditImmutabilityPolicies && !contains(map(retainedAuditImmutabilityPolicyIds, policyId => toLower(policyId)), toLower(evidencePolicyId))) {
  parent: evidenceContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: payloadRetentionDays
    allowProtectedAppendWrites: false
  }
}

// Single, conditional Put Blob. This role deliberately lacks overwrite, read and delete.
resource evidenceCreatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'audit-evidence-blob-create')
  properties: {
    roleName: '${stem} Audit Evidence Blob Creator'
    description: 'Create audit evidence blobs without read, overwrite or delete permissions.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ dataActions: ['Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action'] }]
  }
}

resource workerEvidenceWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidenceContainer.id, workerPrincipalId, evidenceCreatorRole.id)
  scope: evidenceContainer
  properties: { principalId: workerPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: evidenceCreatorRole.id }
}

// The SDK's mutable ownership and checkpoint state is isolated from final evidence.
resource workerCheckpointWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(checkpointContainer.id, workerPrincipalId, checkpointRole)
  scope: checkpointContainer
  properties: { principalId: workerPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: checkpointRole }
}

output data object = {
  eventHubsNamespaceId: namespace.id
  eventHubsNamespaceFqdn: split(namespace.properties.serviceBusEndpoint, '/')[2]
  eventHubName: hub.name
  consumerGroupName: consumerGroup.name
  evidenceStorageId: evidence.id
  evidenceBlobEndpoint: evidence.properties.primaryEndpoints.blob
  evidenceContainerName: evidenceContainer.name
  evidenceContainerId: evidenceContainer.id
  payloadImmutability: {
    policyId: evidencePolicyId
    mode: payloadImmutabilityMode
    retentionInDays: payloadRetentionDays
  }
  checkpointStorageId: checkpoints.id
  checkpointBlobEndpoint: checkpoints.properties.primaryEndpoints.blob
  checkpointContainerName: checkpointContainer.name
}
