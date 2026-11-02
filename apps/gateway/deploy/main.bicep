// GatewayのAPI設定・Credential Manager・SQL接続台帳・Brokerを配備する入口です。
// 共有APIM本体・SQLサーバー・ネットワーク・ACR・CAEはCoreの出力から参照します。
targetScope = 'resourceGroup'
param configuration object
param foundation object
@secure()
@description('物理CMプロバイダーの作成に使う、論理プロバイダー名ごとのclient secret。')
param credentialProviderSecrets object = {}
@secure()
@description('初回だけ生成する内部通信の共有鍵。空の場合は既存の鍵を保持します。')
param brokerKey string = ''
param imageDigest string = ''
@description('SQL初期化成功後の2回目の配備でBroker本体・定期照合・APIM設定を反映します。')
param deployApplication bool = false

var stem = configuration.resourceStem
var location = configuration.location
var tags = union(configuration.tags, { product: configuration.productName, environment: configuration.environmentName, 'azd-env-name': configuration.environmentName })
var profiles = configuration.credentialProfiles
var brokerEnabled = !empty(profiles)
var login = configuration.gateway.authentication.?credentialLogin ?? { providerNames: [] }
var brokerUrl = 'https://ca-${stem}-credential-broker.${foundation.environmentDomain}'
var oidcSecrets = [for name in objectKeys(profiles): { env: profiles[name].clientSecretEnv, uri: 'https://${foundation.vaultName}${environment().suffixes.keyvaultDns}/secrets/core-credential-${name}', name: 'oidc-${name}' }]

module runtime 'identity.bicep' = if (brokerEnabled) {
  name: '${stem}-gateway-identity'
  scope: resourceGroup(foundation.identityResourceGroup)
  params: { stem: stem, location: location, tags: tags }
}
module database 'database.bicep' = if (brokerEnabled) {
  name: '${stem}-gateway-database'
  params: { stem: stem, location: location, tags: tags, sqlServer: foundation.sqlServer, settings: configuration.credentialBroker }
}
module access 'broker-access.bicep' = if (brokerEnabled) {
  name: '${stem}-gateway-access'
  params: {
    vaultName: foundation.vaultName
    registryName: foundation.registryName
    runtimeIdentity: runtime!.outputs.identity
    schemaIdentity: foundation.schemaIdentity
    brokerKey: brokerKey
  }
}

// 同じAPIMへのCM更新は直列にし、既存の本人接続は削除・再作成しません。
@batchSize(1)
module providers 'credential-manager.bicep' = [for provider in configuration.credentialPhysicalProviders: {
  name: 'cm-provider-${uniqueString(stem, provider.name)}'
  params: {
    apimName: foundation.apimName
    apimPrincipalId: foundation.apimPrincipalId
    providerName: provider.name
    displayName: provider.displayName
    identityProvider: provider.identityProvider
    clientId: provider.clientId
    clientSecret: credentialProviderSecrets[provider.sourceName]
    tenantId: configuration.azureTenantId
    oauthTenantId: provider.oauthTenantId
    scopes: provider.scopes
    resourceUri: provider.?resourceUri ?? ''
    authorizationUrl: provider.?authorizationUrl ?? ''
    tokenUrl: provider.?tokenUrl ?? ''
    refreshUrl: provider.?refreshUrl ?? ''
    connectionNames: provider.connectionNames
  }
}]
module loginAccess 'credential-access.bicep' = [for provider in configuration.credentialPhysicalProviders: if (contains(login.providerNames, provider.sourceName)) {
  name: 'cm-login-${uniqueString(stem, provider.name)}'
  params: { apimName: foundation.apimName, providerName: provider.name, apimPrincipalId: foundation.apimPrincipalId }
  dependsOn: [providers]
}]
module brokerAccess 'credential-access.bicep' = [for provider in configuration.credentialPhysicalProviders: if (brokerEnabled && contains(login.providerNames, provider.sourceName)) {
  name: 'cm-broker-${uniqueString(stem, provider.name)}'
  params: { apimName: foundation.apimName, providerName: provider.name, apimPrincipalId: runtime!.outputs.identity.principalId }
  dependsOn: [providers]
}]

module broker 'broker.bicep' = if (brokerEnabled) {
  name: '${stem}-gateway-broker'
  params: {
    stem: stem
    location: location
    environmentName: foundation.environmentName
    registryName: foundation.registryName
    imageDigest: imageDigest
    runtimeIdentity: runtime!.outputs.identity
    schemaIdentity: foundation.schemaIdentity
    sqlServer: foundation.sqlServer
    database: database!.outputs.database
    profiles: profiles
    publicOrigin: 'https://${configuration.gateway.publicHostName}'
    apimPrincipalId: foundation.apimPrincipalId
    apimName: foundation.apimName
    brokerKeySecretUri: access!.outputs.keySecretUri
    oidcSecrets: oidcSecrets
    idleTimeoutSeconds: login.idleTimeoutSeconds
    absoluteLifetimeSeconds: login.absoluteLifetimeSeconds
    rotationIntervalSeconds: login.rotationIntervalSeconds
    rotationGraceSeconds: login.rotationGraceSeconds
    rotationHandoffSeconds: login.rotationHandoffSeconds
    rotationCandidateTtlSeconds: login.rotationCandidateTtlSeconds
    transactionLifetimeSeconds: login.transactionLifetimeSeconds
    loginCallsPerMinute: login.loginCallsPerMinute
    minReplicas: configuration.credentialBroker.minReplicas
    maxReplicas: configuration.credentialBroker.maxReplicas
    deployApplication: deployApplication
  }
  dependsOn: [brokerAccess]
}
module settings 'apim-settings.bicep' = if (deployApplication) {
  name: '${stem}-gateway-apim-settings'
  params: {
    names: { apim: foundation.apimName }
    authentication: configuration.gateway.authentication
    l7IngressSecretIdentifier: foundation.l7IngressSecretIdentifier
    realtimeTicketSecretIdentifier: foundation.realtimeTicketSecretIdentifier
    credentialBrokerUrl: brokerEnabled ? brokerUrl : ''
    credentialBrokerSecretIdentifier: brokerEnabled ? access!.outputs.keySecretUri : ''
  }
  dependsOn: [broker]
}

output migrationJob string = brokerEnabled ? broker!.outputs.migrationJob : ''
output applicationName string = brokerEnabled ? broker!.outputs.applicationName : ''
output databaseId string = brokerEnabled ? database!.outputs.databaseId : ''
output credentialBroker object = brokerEnabled ? {
  applicationName: broker!.outputs.applicationName
  apimPrincipalId: foundation.apimPrincipalId
  resourceGroup: resourceGroup().name
  url: brokerUrl
  environmentName: foundation.environmentName
  registryName: foundation.registryName
  sqlServer: foundation.sqlServer
  database: database!.outputs.database
  databaseId: database!.outputs.databaseId
  runtimeIdentity: runtime!.outputs.identity
  schemaIdentity: foundation.schemaIdentity
  brokerKeySecretUri: access!.outputs.keySecretUri
  oidcSecrets: oidcSecrets
  privateEndpointId: foundation.sqlPrivateEndpointId
} : {}
