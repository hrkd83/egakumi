// SQL初期化後に、既存のPortal Container Appを同じ名前で更新します。
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
param publicOrigin string
@description('構築設定の公開ドメインから作る、Model Gatewayの固定HTTPSオリジンです。')
param gatewayOrigin string
param clientSecretUri string
param signingSecretUri string
param expectedEgressIp string
param apimResourceId string
param profileSecretUri string

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = { name: environmentName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
var variables = {
  NODE_ENV: 'production'
  HOSTNAME: '0.0.0.0'
  PORT: '3000'
  BETTER_AUTH_URL: publicOrigin
  MICROSOFT_CLIENT_ID: clientId
  MICROSOFT_TENANT_ID: tenantId
  PORTAL_SQL_SERVER: sqlServer
  PORTAL_SQL_DATABASE: database
  PORTAL_SQL_AUTH: 'managed-identity'
  PORTAL_SUBSCRIPTION_SYNC_ENABLED: 'true'
  PORTAL_CANONICAL_SQL_SERVER: sqlServer
  PORTAL_CANONICAL_SQL_DATABASE: database
  AZURE_TENANT_ID: tenant().tenantId
  AZURE_CLIENT_ID: runtimeIdentity.clientId
  PORTAL_APIM_AUTH: 'managed-identity'
  PORTAL_APIM_RESOURCE_ID: apimResourceId
  PORTAL_GATEWAY_URL: gatewayOrigin
  EXPECTED_EGRESS_IP: expectedEgressIp
}
resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: 'ca-${stem}-portal'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${runtimeIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 3000, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: runtimeIdentity.id }]
      secrets: [
        { name: 'auth-signing-secret', keyVaultUrl: signingSecretUri, identity: runtimeIdentity.id }
        { name: 'microsoft-client-secret', keyVaultUrl: clientSecretUri, identity: runtimeIdentity.id }
        { name: 'subscription-profile-secret', keyVaultUrl: profileSecretUri, identity: runtimeIdentity.id }
      ]
    }
    template: {
      containers: [{
        name: 'portal'
        image: '${registry.properties.loginServer}/portal@${imageDigest}'
        env: concat(map(items(variables), item => { name: item.key, value: item.value }), [
          { name: 'BETTER_AUTH_SECRET', secretRef: 'auth-signing-secret' }
          { name: 'MICROSOFT_CLIENT_SECRET', secretRef: 'microsoft-client-secret' }
          { name: 'PORTAL_SUBSCRIPTION_PROFILE_SECRET', secretRef: 'subscription-profile-secret' }
        ])
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [for type in ['Startup', 'Readiness', 'Liveness']: {
          type: type
          httpGet: { path: type == 'Readiness' ? '/api/health/ready' : '/api/health', port: 3000, scheme: 'HTTP' }
          initialDelaySeconds: 5
          periodSeconds: 10
          failureThreshold: 12
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}
output fqdn string = app.properties.configuration.ingress.fqdn
