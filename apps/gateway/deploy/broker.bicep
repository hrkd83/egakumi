// 検証済みの同一イメージを、SQL初期化ジョブ・認証窓口・定期照合に使います。
targetScope = 'resourceGroup'
param stem string
param location string = resourceGroup().location
param environmentName string
param registryName string
param imageDigest string
param runtimeIdentity object
param schemaIdentity object
param sqlServer string
param database string
param profiles object
param publicOrigin string
param apimPrincipalId string
param apimName string
param brokerKeySecretUri string
param oidcSecrets array
param idleTimeoutSeconds int
param absoluteLifetimeSeconds int
param rotationIntervalSeconds int
param rotationGraceSeconds int
param rotationHandoffSeconds int
param rotationCandidateTtlSeconds int
param transactionLifetimeSeconds int
param loginCallsPerMinute int = 10
param minReplicas int = 2
param maxReplicas int = 4
param deployApplication bool = false
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = { name: environmentName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
var image = '${registry.properties.loginServer}/credential-broker@${imageDigest}'
var commonEnvironment = {
  AZURE_SQL_SERVER: sqlServer
  AZURE_SQL_DATABASE: database
  AZURE_SUBSCRIPTION_ID: subscription().subscriptionId
  AZURE_RESOURCE_GROUP: resourceGroup().name
  AZURE_TENANT_ID: tenant().tenantId
  APIM_NAME: apimName
  APIM_PRINCIPAL_ID: apimPrincipalId
  BROKER_PUBLIC_ORIGIN: publicOrigin
  BROKER_PROFILES_JSON: string(profiles)
  BROKER_IDLE_TIMEOUT_SECONDS: string(idleTimeoutSeconds)
  BROKER_ABSOLUTE_LIFETIME_SECONDS: string(absoluteLifetimeSeconds)
  BROKER_ROTATION_INTERVAL_SECONDS: string(rotationIntervalSeconds)
  BROKER_ROTATION_GRACE_SECONDS: string(rotationGraceSeconds)
  BROKER_ROTATION_HANDOFF_SECONDS: string(rotationHandoffSeconds)
  BROKER_ROTATION_CANDIDATE_TTL_SECONDS: string(rotationCandidateTtlSeconds)
  BROKER_TRANSACTION_TTL_SECONDS: string(transactionLifetimeSeconds)
  BROKER_LOGIN_CALLS_PER_MINUTE: string(loginCallsPerMinute)
}
var runtimeEnvironment = union(commonEnvironment, { AZURE_CLIENT_ID: runtimeIdentity.clientId })
var schemaEnvironment = union(commonEnvironment, {
  AZURE_CLIENT_ID: schemaIdentity.clientId
  SQL_RUNTIME_CLIENT_ID: runtimeIdentity.clientId
  SQL_RUNTIME_USER_NAME: runtimeIdentity.name
})
var oidcEnvironment = [for item in oidcSecrets: { name: item.env, secretRef: item.name }]
var commonSecrets = concat([{ name: 'broker-key', keyVaultUrl: brokerKeySecretUri, identity: runtimeIdentity.id }],
  map(oidcSecrets, item => { name: item.name, keyVaultUrl: item.uri, identity: runtimeIdentity.id }))
resource migration 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${stem}-schema'
  location: location
  tags: resourceGroup().tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${schemaIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { replicaCompletionCount: 1, parallelism: 1 }
      registries: [{ server: registry.properties.loginServer, identity: schemaIdentity.id }]
    }
    template: { containers: [{
      name: 'schema'
      image: image
      command: ['python', '-m', 'credential_broker.cli']
      args: ['migrate']
      env: [for item in items(schemaEnvironment): { name: item.key, value: item.value }]
      resources: { cpu: json('0.5'), memory: '1Gi' }
    }] }
  }
}
resource app 'Microsoft.App/containerApps@2025-01-01' = if (deployApplication) {
  name: 'ca-${stem}-credentials'
  location: location
  tags: resourceGroup().tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${runtimeIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: runtimeIdentity.id }]
      secrets: commonSecrets
    }
    template: {
      containers: [{
        name: 'credential-broker'
        image: image
        env: concat(map(items(runtimeEnvironment), item => { name: item.key, value: item.value }), [{ name: 'BROKER_SHARED_KEY', secretRef: 'broker-key' }], oidcEnvironment)
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [for type in ['Startup', 'Readiness', 'Liveness']: {
          type: type
          httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
          initialDelaySeconds: 10
          periodSeconds: 15
          failureThreshold: 20
        }]
      }]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}
// 接続が消えた場合は台帳の利用を止めます。CMの削除・容量解放は実行しません。
resource reconciliation 'Microsoft.App/jobs@2025-01-01' = if (deployApplication) {
  name: 'caj-${stem}-recon'
  location: location
  tags: resourceGroup().tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${runtimeIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: { cronExpression: '17 * * * *', replicaCompletionCount: 1, parallelism: 1 }
      registries: [{ server: registry.properties.loginServer, identity: runtimeIdentity.id }]
    }
    template: { containers: [{
      name: 'reconcile'
      image: image
      command: ['python', '-m', 'credential_broker.cli']
      args: ['reconcile', '--execute']
      env: [for item in items(runtimeEnvironment): { name: item.key, value: item.value }]
      resources: { cpu: json('0.5'), memory: '1Gi' }
    }] }
  }
}
output migrationJob string = migration.name
output applicationName string = 'ca-${stem}-credentials'
