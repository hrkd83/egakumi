// L7 proof and the Realtime owner ticket belong to the application vault and are
// independent of the public certificate issuer. The orchestrator uses the same
// protected source values across retry/redeploy.
targetScope = 'resourceGroup'
param vaultName string
param ownerTicketEnabled bool
@secure()
param registration object
@secure()
param l7IngressSecret string
@secure()
param realtimeTicketKey string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource l7 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = if (!empty(l7IngressSecret) && contains(registration, 'l7IngressSecret')) {
  parent: vault
  name: 'core-l7-ingress-secret'
  tags: { 'egakumi-ingress-generation': registration.l7IngressSecret }
  properties: { value: l7IngressSecret, attributes: { enabled: true } }
}
resource ticket 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = if (ownerTicketEnabled && !empty(realtimeTicketKey) && contains(registration, 'realtimeTicketKey')) {
  parent: vault
  name: 'core-realtime-ticket-key'
  tags: { 'egakumi-ingress-generation': registration.realtimeTicketKey }
  properties: { value: realtimeTicketKey, attributes: { enabled: true } }
  dependsOn: [l7]
}
