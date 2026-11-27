targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param serviceResourceGroupName string
param registryName string
param foundryName string

resource relay 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-audit-relay'
  location: location
  tags: tags
}

resource worker 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-audit-worker'
  location: location
  tags: tags
}

module access './audit-evidence-service-access.bicep' = {
  name: '${stem}-audit-evidence-service-access'
  scope: resourceGroup(serviceResourceGroupName)
  params: {
    relayPrincipalId: relay.properties.principalId
    workerPrincipalId: worker.properties.principalId
    registryName: registryName
    foundryName: foundryName
  }
}

output identities object = {
  relay: { identityResourceId: relay.id, clientId: relay.properties.clientId, principalId: relay.properties.principalId }
  worker: { identityResourceId: worker.id, clientId: worker.properties.clientId, principalId: worker.properties.principalId }
}
