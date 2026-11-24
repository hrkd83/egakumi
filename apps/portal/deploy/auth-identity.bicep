// SQL/APIM/ACR用の既存identityから、Portal認証鍵の署名主体だけを分離します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-portal-auth'
  location: location
  tags: tags
}
output identity object = { id: identity.id, clientId: identity.properties.clientId, principalId: identity.properties.principalId }
