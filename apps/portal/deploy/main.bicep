// PortalのDB・SQL初期化ジョブ・アプリと、専用のGateway向け通信規則を配備します。
targetScope = 'resourceGroup'

param stem string
param location string = resourceGroup().location
param tags object = resourceGroup().tags
param environmentName string
param registryName string
param runtimeIdentity object
param schemaIdentity object
param sqlServer string
param databaseSettings object
param vaultName string
param authentication object
param authIdentity object
param apiAuthentication object = {}
param clientId string
param tenantId string
param publicOrigin string
@description('Gateway.InvokeのBearer試用に使う公開Gatewayの固定HTTPSオリジンです。')
param gatewayOrigin string
param networkResourceGroup string
param portalPrefix string
param expectedEgressIp string
@secure()
@description('Portal専用セッション署名鍵。空の場合は既存のKey Vault値を更新しません。')
param signingSecret string = ''
@description('SQL初期化成功後の2回目の配備だけでtrueにします。')
param deployApplication bool = false
param imageDigest string

module authKeyAccess 'auth-key-access.bicep' = {
  name: '${stem}-portal-auth-key-access'
  params: {
    vaultName: vaultName
    keyName: split(authentication.keyId, '/')[4]
    authIdentity: authIdentity
  }
}
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
  params: { stem: stem, portalPrefix: portalPrefix, gatewayOrigin: gatewayOrigin }
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
    signingSecret: signingSecret
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
    schemaIdentity: schemaIdentity
    sqlServer: sqlServer
    database: database.outputs.database
  }
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
    authentication: authentication
    authIdentity: authIdentity
    apiAuthentication: apiAuthentication
    signingSecretUri: database.outputs.signingSecretUri
    expectedEgressIp: expectedEgressIp
  }
  dependsOn: [authKeyAccess, apiAuthReader]
}

output migrationJob string = schema.outputs.name
output applicationName string = 'ca-${stem}-portal'
output databaseId string = database.outputs.databaseId
output managementMaintenance object = {
  sqlDatabaseIds: [database.outputs.databaseId]
  applicationIds: deployApplication ? [application!.outputs.id] : []
  jobIds: [schema.outputs.id]
  environmentIds: [schema.outputs.environmentId]
}
output authentication object = {
  sqlServer: sqlServer
  sqlServerId: database.outputs.sqlServerId
  database: database.outputs.database
  databaseId: database.outputs.databaseId
  runtimeIdentity: runtimeIdentity
  schemaIdentity: schemaIdentity
  certificateAuthentication: authentication
  authIdentity: authIdentity
  tenantId: tenantId
  clientId: clientId
  signingSecretUri: database.outputs.signingSecretUri
  publicUrl: publicOrigin
}
