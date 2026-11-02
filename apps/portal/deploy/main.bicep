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
param clientId string
param tenantId string
param publicOrigin string
@description('公開Gatewayの接続先だけを渡します。利用する購読キーは本人の選択に従い実行時に解決します。')
param gatewayOrigin string
param networkResourceGroup string
param acaPrefix string
param expectedEgressIp string
@secure()
@description('Portal専用セッション署名鍵。空の場合は既存のKey Vault値を更新しません。')
param signingSecret string = ''
@secure()
@description('Gatewayのプロファイル照会専用鍵。空の場合は既存のKey Vault値を維持します。')
param profileSecret string = ''
@description('SQL初期化成功後の2回目の配備だけでtrueにします。')
param deployApplication bool = false
param imageDigest string

module gatewayEgress 'gateway-egress.bicep' = {
  name: '${stem}-portal-gateway-egress'
  scope: resourceGroup(networkResourceGroup)
  params: { stem: stem, acaPrefix: acaPrefix, gatewayOrigin: gatewayOrigin }
}

module apiKeys 'api-keys.bicep' = {
  name: '${stem}-portal-api-keys'
  params: {
    apimName: apimName
    runtimeIdentity: runtimeIdentity
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
    credentialProviderName: credentialProviderName
    signingSecret: signingSecret
    profileSecret: profileSecret
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
    apiKeyGroups: apiKeyGroups
    apiKeyPlatformGroupId: apiKeyPlatformGroupId
    apiKeyGroupNamePattern: apiKeyGroupNamePattern
    apiKeyCatalogProducts: apiKeyCatalogProducts
    apiKeyVerificationSubscriptions: apiKeyVerificationSubscriptions
    apimResourceId: apiKeys.outputs.apimResourceId
    apiKeyTenantId: tenantId
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
    clientSecretUri: database.outputs.clientSecretUri
    signingSecretUri: database.outputs.signingSecretUri
    expectedEgressIp: expectedEgressIp
    apimResourceId: apiKeys.outputs.apimResourceId
    profileSecretUri: database.outputs.profileSecretUri
  }
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
    runtimeIdentity: runtimeIdentity
    imageDigest: imageDigest
    sqlServer: sqlServer
    database: database.outputs.database
    clientId: clientId
    tenantId: tenantId
    clientSecretUri: database.outputs.clientSecretUri
  }
}

// URLと鍵参照は配備時だけ更新。有効化はアプリ疎通確認後のスクリプトが行います。
module profileConnection 'subscription-profile.bicep' = {
  name: '${stem}-portal-subscription-profile'
  params: {
    stem: stem
    apimName: apimName
    environmentName: environmentName
    profileSecretUri: database.outputs.profileSecretUri
    vaultName: vaultName
  }
}

output migrationJob string = schema.outputs.name
output applicationName string = 'ca-${stem}-portal'
output databaseId string = database.outputs.databaseId
output apimResourceId string = apiKeys.outputs.apimResourceId
output authentication object = {
  sqlServer: sqlServer
  sqlServerId: database.outputs.sqlServerId
  database: database.outputs.database
  databaseId: database.outputs.databaseId
  runtimeIdentity: runtimeIdentity
  schemaIdentity: schemaIdentity
  credentialProviderName: credentialProviderName
  tenantId: tenantId
  clientId: clientId
  clientSecretUri: database.outputs.clientSecretUri
  signingSecretUri: database.outputs.signingSecretUri
  publicUrl: publicOrigin
}
