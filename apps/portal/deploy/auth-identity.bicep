// Portalの署名とAPI保存鍵の読取りに、それぞれ専用のIDを用意します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
@allowed(['portal-auth', 'portal-api-auth'])
param purpose string = 'portal-auth'
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-${purpose}'
  location: location
  tags: tags
}
output identity object = { id: identity.id, clientId: identity.properties.clientId, principalId: identity.properties.principalId }
