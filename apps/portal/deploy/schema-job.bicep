// 配備時だけ手動実行するSQL初期化ジョブです。アプリにはDDL権限を付けません。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param environmentName string
param registryName string
param imageDigest string
param runtimeIdentity object
param schemaIdentity object
param sqlServer string
param database string
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = { name: environmentName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
// CMを使わない環境でもジョブが取得できます。同じIDの既存割り当ては再利用します。
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
resource schemaImageAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, schemaIdentity.principalId, acrPull)
  scope: registry
  properties: { roleDefinitionId: acrPull, principalId: schemaIdentity.principalId, principalType: 'ServicePrincipal' }
}
var variables = {
  NODE_ENV: 'production'
  PORTAL_SQL_SERVER: sqlServer
  PORTAL_SQL_DATABASE: database
  PORTAL_SQL_AUTH: 'managed-identity'
  PORTAL_CANONICAL_SQL_SERVER: sqlServer
  PORTAL_CANONICAL_SQL_DATABASE: database
  AZURE_TENANT_ID: tenant().tenantId
  AZURE_CLIENT_ID: schemaIdentity.clientId
  PORTAL_SQL_RUNTIME_CLIENT_ID: runtimeIdentity.clientId
  PORTAL_SQL_RUNTIME_USER_NAME: runtimeIdentity.name
}
resource migration 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${stem}-psql'
  location: location
  tags: tags
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
      image: '${registry.properties.loginServer}/portal@${imageDigest}'
      command: ['node', '--conditions=react-server', '--experimental-strip-types', 'scripts/migrate-auth.ts']
      env: [for item in items(variables): { name: item.key, value: item.value }]
      resources: { cpu: json('0.5'), memory: '1Gi' }
    }] }
  }
  dependsOn: [schemaImageAccess]
}
output name string = migration.name
output id string = migration.id
output environmentId string = environment.id
