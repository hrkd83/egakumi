// GatewayのAPI設定・Credential Manager・SQL接続台帳・Brokerを配備する入口です。
// 共有APIM本体・SQLサーバー・ネットワーク・ACR・CAEはCoreの出力から参照します。
targetScope = 'resourceGroup'
param configuration object
param foundation object
@secure()
@description('物理CMプロバイダーの作成に使う、論理プロバイダー名ごとのclient secret。')
param credentialProviderSecrets object = {}
@secure()
@description('独立したOIDC本人確認アプリのsecret。CM providerのsecretとは別の入力です。')
param credentialIdentitySecret string = ''
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
var login = configuration.gateway.authentication.?credentialLogin ?? { providerNames: [] }
var session = configuration.credentialBrokerSession
var brokerUrl = 'https://ca-${stem}-credentials.${foundation.environmentDomain}'
var identityConfigured = contains(login, 'identity')
var legacyOidcSecrets = [for name in objectKeys(profiles): { env: profiles[name].clientSecretEnv, uri: 'https://${foundation.vaultName}${environment().suffixes.keyvaultDns}/secrets/core-credential-${name}', name: 'oidc-${name}' }]
var oidcSecrets = identityConfigured
  ? [{ env: 'OIDC_SECRET_IDENTITY', uri: 'https://${foundation.vaultName}${environment().suffixes.keyvaultDns}/secrets/core-oidc-identity', name: 'oidc-identity' }]
  : legacyOidcSecrets

module runtime 'identity.bicep' = {
  name: '${stem}-gateway-identity'
  scope: resourceGroup(foundation.identityResourceGroup)
  params: { stem: stem, location: location, tags: tags }
}
module database 'database.bicep' = {
  name: '${stem}-gateway-database'
  params: { stem: stem, location: location, tags: tags, sqlServer: foundation.sqlServer, settings: configuration.credentialBroker }
}
module access 'broker-access.bicep' = {
  name: '${stem}-gateway-access'
  params: {
    vaultName: foundation.vaultName
    registryName: foundation.registryName
    runtimeIdentity: runtime.outputs.identity
    schemaIdentity: foundation.schemaIdentity
    brokerKey: brokerKey
    credentialIdentitySecret: credentialIdentitySecret
    identityConfigured: identityConfigured
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
module brokerAccess 'credential-access.bicep' = [for provider in configuration.credentialPhysicalProviders: if (contains(login.providerNames, provider.sourceName)) {
  name: 'cm-broker-${uniqueString(stem, provider.name)}'
  params: { apimName: foundation.apimName, providerName: provider.name, apimPrincipalId: runtime.outputs.identity.principalId }
  dependsOn: [providers]
}]

module broker 'broker.bicep' = {
  name: '${stem}-gateway-broker'
  params: {
    stem: stem
    location: location
    environmentName: foundation.environmentName
    registryName: foundation.registryName
    imageDigest: imageDigest
    runtimeIdentity: runtime.outputs.identity
    schemaIdentity: foundation.schemaIdentity
    managementSqlClientId: foundation.managementMaintenance.sqlClientId
    sqlServer: foundation.sqlServer
    database: database.outputs.database
    profiles: profiles
    publicOrigin: 'https://${configuration.gateway.publicHostName}'
    apimPrincipalId: foundation.apimPrincipalId
    apimName: foundation.apimName
    brokerKeySecretUri: access.outputs.keySecretUri
    oidcSecrets: oidcSecrets
    idleTimeoutSeconds: session.idleTimeoutSeconds
    absoluteLifetimeSeconds: session.absoluteLifetimeSeconds
    rotationIntervalSeconds: session.rotationIntervalSeconds
    rotationGraceSeconds: session.rotationGraceSeconds
    rotationHandoffSeconds: session.rotationHandoffSeconds
    rotationCandidateTtlSeconds: session.rotationCandidateTtlSeconds
    transactionLifetimeSeconds: session.transactionLifetimeSeconds
    loginCallsPerMinute: session.loginCallsPerMinute
    minReplicas: configuration.credentialBroker.minReplicas
    maxReplicas: configuration.credentialBroker.maxReplicas
    deployApplication: deployApplication
  }
  dependsOn: [brokerAccess]
}
// Brokerの各配備段階で実際に作成したApp/Jobと、その環境だけに保守権限を付けます。
module managementAccess '../../../infra/modules/management-aca-bindings.bicep' = {
  name: '${stem}-gateway-management-access'
  params: {
    principalId: foundation.managementMaintenance.principalId
    roleDefinitionIds: foundation.managementMaintenance.roleDefinitionIds
    applicationNames: map(broker.outputs.applicationIds, id => last(split(id, '/')))
    jobNames: map(broker.outputs.jobIds, id => last(split(id, '/')))
    environmentNames: [last(split(broker.outputs.environmentId, '/'))]
  }
}
module settings 'apim-settings.bicep' = if (deployApplication) {
  name: '${stem}-gateway-apim-settings'
  params: {
    names: { apim: foundation.apimName }
    authentication: configuration.gateway.authentication
    entraTenantId: configuration.?entraTenantId ?? configuration.azureTenantId
    l7IngressSecretIdentifier: foundation.l7IngressSecretIdentifier
    realtimeTicketSecretIdentifier: foundation.realtimeTicketSecretIdentifier
    credentialBrokerUrl: brokerUrl
    credentialBrokerSecretIdentifier: access.outputs.keySecretUri
  }
  dependsOn: [broker]
}

output migrationJob string = broker.outputs.migrationJob
output applicationName string = broker.outputs.applicationName
output databaseId string = database.outputs.databaseId
output managementMaintenance object = {
  sqlDatabaseIds: [database.outputs.databaseId]
  applicationIds: broker.outputs.applicationIds
  jobIds: broker.outputs.jobIds
  environmentIds: [broker.outputs.environmentId]
}
output credentialBroker object = {
  applicationName: broker.outputs.applicationName
  apimPrincipalId: foundation.apimPrincipalId
  resourceGroup: resourceGroup().name
  url: brokerUrl
  environmentName: foundation.environmentName
  registryName: foundation.registryName
  sqlServer: foundation.sqlServer
  database: database.outputs.database
  databaseId: database.outputs.databaseId
  runtimeIdentity: runtime.outputs.identity
  schemaIdentity: foundation.schemaIdentity
  brokerKeySecretUri: access.outputs.keySecretUri
  oidcSecrets: oidcSecrets
  privateEndpointId: foundation.sqlPrivateEndpointId
}
