// Distinct identities for registry pull, public TLS and Portal's Entra key lifecycle.
targetScope = 'resourceGroup'
param stem string
param location string
param tags object

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-cert-pull'
  location: location
  tags: tags
}
resource tlsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-cert-tls'
  location: location
  tags: tags
}
resource portalIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-cert-portal'
  location: location
  tags: tags
}

output pull object = { id: pullIdentity.id, principalId: pullIdentity.properties.principalId }
output tls object = { id: tlsIdentity.id, clientId: tlsIdentity.properties.clientId, principalId: tlsIdentity.properties.principalId }
output portal object = { id: portalIdentity.id, clientId: portalIdentity.properties.clientId, principalId: portalIdentity.properties.principalId }
