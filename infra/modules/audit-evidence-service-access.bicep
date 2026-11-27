targetScope = 'resourceGroup'

param relayPrincipalId string
param workerPrincipalId string
param registryName string
param foundryName string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: foundryName }

var pullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var inferenceRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

resource imagePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in [relayPrincipalId, workerPrincipalId]: {
  name: guid(registry.id, principalId, pullRole)
  scope: registry
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: pullRole }
}]

resource relayInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, relayPrincipalId, inferenceRole)
  scope: foundry
  properties: { principalId: relayPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: inferenceRole }
}
