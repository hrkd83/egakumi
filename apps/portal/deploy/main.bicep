// PortalのDB・SQL初期化ジョブ・アプリと、専用のGateway向け通信規則を配備します。
targetScope = 'resourceGroup'

param stem string
param location string = resourceGroup().location
param tags object = resourceGroup().tags
param environmentName string
param registryName string
param apimName string
param apiKeyGroups array = []
param apiKeyPlatformGroupId string = ''
param apiKeyGroupNamePattern string = ''
param apiKeyCatalogProducts array = []
param apiKeyVerificationSubscriptions array = []
param runtimeIdentity object
param schemaIdentity object
param sqlServer string
param databaseSettings object
param vaultName string
param credentialProviderName string
param authentication object = {}
param authIdentity object
param apiAuthentication object = {}
param clientId string
param tenantId string
param publicOrigin string
@description('公開Gatewayの接続先だけを渡します。利用する購読キーは本人の選択に従い実行時に解決します。')
param gatewayOrigin string
param networkResourceGroup string
param acaPrefix string
param apimSubnetPrefix string
param subscriptionServiceAudience string
param expectedEgressIp string
@secure()
@description('Portal専用セッション署名鍵。空の場合は既存のKey Vault値を更新しません。')
param signingSecret string = ''
@secure()
@description('Gatewayのプロファイル照会専用鍵。空の場合は既存のKey Vault値を維持します。')
param profileSecret string = ''
@secure()
@description('購読同期計画の署名鍵。空の場合は既存のKey Vault値を維持します。')
param subscriptionSyncSecret string = ''
@description('SQL初期化成功後の2回目の配備だけでtrueにします。')
param deployApplication bool = false
param imageDigest string

module subscriptionIdentities 'subscription-identities.bicep' = {
  name: '${stem}-subscription-identities'
  params: { stem: stem, location: location, tags: tags, registryName: registryName }
}

module authKeyAccess 'auth-key-access.bicep' = if (!empty(authentication)) {
  name: '${stem}-portal-auth-key-access'
  params: {
    vaultName: vaultName
    keyName: split(authentication.keyId, '/')[4]
    authIdentity: authIdentity
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
  }
}
var authRuntimeIdentity = empty(authentication) ? {} : authIdentity
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource apiAuthSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'core-portal-api-auth-key'
}
var apiAuthReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource apiAuthReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(apiAuthentication)) {
  scope: apiAuthSecret
  name: guid(apiAuthSecret.id, apiAuthentication.identity.principalId, apiAuthReaderRole)
  properties: { roleDefinitionId: apiAuthReaderRole, principalId: apiAuthentication.identity.principalId, principalType: 'ServicePrincipal' }
}

module gatewayEgress 'gateway-egress.bicep' = {
  name: '${stem}-portal-gateway-egress'
  scope: resourceGroup(networkResourceGroup)
  params: { stem: stem, acaPrefix: acaPrefix, gatewayOrigin: gatewayOrigin }
}

module apiKeys 'api-keys.bicep' = {
  name: '${stem}-portal-api-keys'
  params: {
    apimName: apimName
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
  }
}

module database 'database.bicep' = {
  name: '${stem}-portal-database'
  params: {
    stem: stem
    location: location
    tags: tags
    sqlServer: sqlServer
    settings: databaseSettings
    vaultName: vaultName
    runtimeIdentity: runtimeIdentity
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
    profileIdentity: subscriptionIdentities.outputs.profileIdentity
    credentialProviderName: credentialProviderName
    certificateAuthentication: !empty(authentication)
    signingSecret: signingSecret
    profileSecret: profileSecret
    subscriptionSyncSecret: subscriptionSyncSecret
  }
}

module schema 'schema-job.bicep' = {
  name: '${stem}-portal-schema-job'
  params: {
    stem: stem
    location: location
    tags: tags
    environmentName: environmentName
    registryName: registryName
    imageDigest: imageDigest
    runtimeIdentity: runtimeIdentity
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
    profileIdentity: subscriptionIdentities.outputs.profileIdentity
    schemaIdentity: schemaIdentity
    sqlServer: sqlServer
    database: database.outputs.database
    apiKeyGroups: apiKeyGroups
    apiKeyPlatformGroupId: apiKeyPlatformGroupId
    apiKeyGroupNamePattern: apiKeyGroupNamePattern
    apiKeyCatalogProducts: apiKeyCatalogProducts
    apiKeyVerificationSubscriptions: apiKeyVerificationSubscriptions
    apimResourceId: apiKeys.outputs.apimResourceId
    apiKeyTenantId: tenantId
  }
}

module subscriptionServices 'subscription-services.bicep' = if (deployApplication) {
  name: '${stem}-subscription-services'
  params: {
    stem: stem
    location: location
    tags: tags
    environmentName: environmentName
    registryName: registryName
    imageDigest: imageDigest
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
    profileIdentity: subscriptionIdentities.outputs.profileIdentity
    apimSubnetPrefix: apimSubnetPrefix
    apimResourceId: apiKeys.outputs.apimResourceId
    subscriptionServiceAudience: subscriptionServiceAudience
    sqlServer: sqlServer
    database: database.outputs.database
    clientId: clientId
    tenantId: tenantId
    publicOrigin: publicOrigin
    authentication: authentication
    profileSecretUri: database.outputs.profileSecretUri
    subscriptionSyncSecretUri: database.outputs.subscriptionSyncSecretUri
  }
  dependsOn: [authKeyAccess]
}

module application 'application.bicep' = if (deployApplication) {
  name: '${stem}-portal-application'
  params: {
    stem: stem
    location: location
    tags: tags
    environmentName: environmentName
    registryName: registryName
    runtimeIdentity: runtimeIdentity
    imageDigest: imageDigest
    sqlServer: sqlServer
    database: database.outputs.database
    clientId: clientId
    tenantId: tenantId
    publicOrigin: publicOrigin
    gatewayOrigin: gatewayOrigin
    clientSecretUri: database.outputs.clientSecretUri
    authentication: authentication
    authIdentity: authRuntimeIdentity
    apiAuthentication: apiAuthentication
    signingSecretUri: database.outputs.signingSecretUri
    expectedEgressIp: expectedEgressIp
    subscriptionManagerFqdn: subscriptionServices!.outputs.managementFqdn
    subscriptionServiceAudience: subscriptionServiceAudience
  }
  dependsOn: [apiAuthReader]
}

// SQL初期化が成功した後のアプリ更新工程でだけ、定期照合を配備します。
module groupReconciliation 'group-reconciliation-job.bicep' = if (deployApplication) {
  name: '${stem}-portal-group-reconciliation'
  params: {
    stem: stem
    location: location
    tags: tags
    environmentName: environmentName
    registryName: registryName
    managementIdentity: subscriptionIdentities.outputs.managementIdentity
    imageDigest: imageDigest
    sqlServer: sqlServer
    database: database.outputs.database
    clientId: clientId
    tenantId: tenantId
    authentication: authentication
    authIdentity: authRuntimeIdentity
  }
}

// URLと鍵参照は配備時だけ更新。有効化はアプリ疎通確認後のスクリプトが行います。
module profileConnection 'subscription-profile.bicep' = if (deployApplication) {
  name: '${stem}-portal-subscription-profile'
  params: {
    apimName: apimName
    profileFqdn: subscriptionServices!.outputs.profileFqdn
    profileSecretUri: database.outputs.profileSecretUri
    vaultName: vaultName
  }
}

var adminAppName = 'ca-${stem}-sub-admin'
var profileAppName = 'ca-${stem}-sub-profile'
var adminAppId = resourceId('Microsoft.App/containerApps', adminAppName)
var profileAppId = resourceId('Microsoft.App/containerApps', profileAppName)

output migrationJob string = schema.outputs.name
output applicationName string = 'ca-${stem}-portal'
output databaseId string = database.outputs.databaseId
output apimResourceId string = apiKeys.outputs.apimResourceId
output managementMaintenance object = {
  sqlDatabaseIds: [database.outputs.databaseId]
  applicationIds: deployApplication ? [application!.outputs.id, adminAppId, profileAppId] : []
  jobIds: deployApplication ? [schema.outputs.id, groupReconciliation!.outputs.id] : [schema.outputs.id]
  environmentIds: [schema.outputs.environmentId]
}
output subscriptionAdmin object = {
  name: adminAppName
  id: adminAppId
  fqdn: deployApplication ? subscriptionServices!.outputs.managementFqdn : ''
  identity: subscriptionIdentities.outputs.managementIdentity
}
output subscriptionProfileReader object = {
  name: profileAppName
  id: profileAppId
  fqdn: deployApplication ? subscriptionServices!.outputs.profileFqdn : ''
  identity: subscriptionIdentities.outputs.profileIdentity
}
output authentication object = {
  sqlServer: sqlServer
  sqlServerId: database.outputs.sqlServerId
  database: database.outputs.database
  databaseId: database.outputs.databaseId
  runtimeIdentity: runtimeIdentity
  schemaIdentity: schemaIdentity
  certificateAuthentication: authentication
  authIdentity: authRuntimeIdentity
  credentialProviderName: credentialProviderName
  tenantId: tenantId
  clientId: clientId
  clientSecretUri: database.outputs.clientSecretUri
  signingSecretUri: database.outputs.signingSecretUri
  publicUrl: publicOrigin
}
