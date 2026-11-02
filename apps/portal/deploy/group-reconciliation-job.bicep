// 所有グループの存在・表示名・環境別条件への適格性だけを照合します。購読やキーは変更しません。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param environmentName string
param registryName string
param runtimeIdentity object
param imageDigest string
param sqlServer string
param database string
param clientId string
param tenantId string
param clientSecretUri string

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = { name: environmentName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }

resource reconciliation 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${stem}-portal-groups'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${runtimeIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 300
      replicaRetryLimit: 0
      scheduleTriggerConfig: { cronExpression: '*/15 * * * *', replicaCompletionCount: 1, parallelism: 1 }
      registries: [{ server: registry.properties.loginServer, identity: runtimeIdentity.id }]
      secrets: [{ name: 'microsoft-client-secret', keyVaultUrl: clientSecretUri, identity: runtimeIdentity.id }]
    }
    template: { containers: [{
      name: 'reconcile'
      image: '${registry.properties.loginServer}/portal@${imageDigest}'
      command: ['node', '--conditions=react-server', '--experimental-strip-types', 'scripts/reconcile-api-key-groups.ts']
      env: [
        { name: 'NODE_ENV', value: 'production' }
        { name: 'MICROSOFT_CLIENT_ID', value: clientId }
        { name: 'MICROSOFT_TENANT_ID', value: tenantId }
        { name: 'MICROSOFT_CLIENT_SECRET', secretRef: 'microsoft-client-secret' }
        { name: 'PORTAL_SQL_SERVER', value: sqlServer }
        { name: 'PORTAL_SQL_DATABASE', value: database }
        { name: 'PORTAL_SQL_AUTH', value: 'managed-identity' }
        { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
        { name: 'AZURE_CLIENT_ID', value: runtimeIdentity.clientId }
      ]
      resources: { cpu: json('0.5'), memory: '1Gi' }
    }] }
  }
}

output name string = reconciliation.name
