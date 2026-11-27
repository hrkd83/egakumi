// 既存SQLサーバーにPortal専用DBを作成し、実行IDへ必要な秘密だけの参照権限を渡します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param sqlServer string
param settings object
param vaultName string
param runtimeIdentity object
param profileIdentity object
param managementIdentity object
param credentialProviderName string
param certificateAuthentication bool = false
@secure()
param signingSecret string = ''
@secure()
param profileSecret string = ''
@secure()
param subscriptionSyncSecret string = ''

resource server 'Microsoft.Sql/servers@2023-08-01' existing = { name: first(split(sqlServer, '.')) }
var databaseSku = union({
  name: settings.sqlSkuName
  tier: settings.sqlTier
  capacity: settings.sqlCapacity
}, settings.sqlTier == 'Basic' ? {} : { family: 'Gen5' })

resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: server
  name: 'sqldb-${stem}-portal'
  location: location
  tags: tags
  sku: databaseSku
  properties: {
    collation: 'Latin1_General_100_BIN2'
    maxSizeBytes: settings.sqlMaxSizeGB * 1073741824
    zoneRedundant: settings.zoneRedundant
    requestedBackupStorageRedundancy: settings.backupRedundancy
    readScale: 'Disabled'
  }
}
resource retention 'Microsoft.Sql/servers/databases/backupShortTermRetentionPolicies@2023-08-01' = {
  parent: database
  name: 'default'
  properties: { retentionDays: settings.backupRetentionDays }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource signingKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(signingSecret)) {
  parent: vault
  name: 'core-portal-auth-secret'
  properties: { value: signingSecret, attributes: { enabled: true } }
}
resource signingKeyReference 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-portal-auth-secret'
}
resource profileKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(profileSecret)) {
  parent: vault
  name: 'core-subscription-profile-key'
  properties: { value: profileSecret, attributes: { enabled: true } }
}
resource profileKeyReference 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-subscription-profile-key'
}
resource syncKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(subscriptionSyncSecret)) {
  parent: vault
  name: 'core-subscription-sync-key'
  properties: { value: subscriptionSyncSecret, attributes: { enabled: true } }
}
resource syncKeyReference 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-subscription-sync-key'
}
resource credentialSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-credential-${credentialProviderName}'
}
var keyVaultSecretUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource signingKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(signingKeyReference.id, runtimeIdentity.principalId, keyVaultSecretUser)
  scope: signingKeyReference
  properties: { roleDefinitionId: keyVaultSecretUser, principalId: runtimeIdentity.principalId, principalType: 'ServicePrincipal' }
  dependsOn: [signingKey]
}
resource credentialSecretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!certificateAuthentication) {
  name: guid(credentialSecret.id, runtimeIdentity.principalId, keyVaultSecretUser)
  scope: credentialSecret
  properties: { roleDefinitionId: keyVaultSecretUser, principalId: runtimeIdentity.principalId, principalType: 'ServicePrincipal' }
}
resource profileKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(profileKeyReference.id, profileIdentity.principalId, keyVaultSecretUser)
  scope: profileKeyReference
  properties: { roleDefinitionId: keyVaultSecretUser, principalId: profileIdentity.principalId, principalType: 'ServicePrincipal' }
  dependsOn: [profileKey]
}
resource syncKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(syncKeyReference.id, managementIdentity.principalId, keyVaultSecretUser)
  scope: syncKeyReference
  properties: { roleDefinitionId: keyVaultSecretUser, principalId: managementIdentity.principalId, principalType: 'ServicePrincipal' }
  dependsOn: [syncKey]
}
output sqlServerId string = server.id
output database string = database.name
output databaseId string = database.id
output signingSecretUri string = '${vault.properties.vaultUri}secrets/core-portal-auth-secret'
output clientSecretUri string = certificateAuthentication ? '' : '${vault.properties.vaultUri}secrets/${credentialSecret.name}'
output profileSecretUri string = '${vault.properties.vaultUri}secrets/core-subscription-profile-key'
output subscriptionSyncSecretUri string = '${vault.properties.vaultUri}secrets/core-subscription-sync-key'
