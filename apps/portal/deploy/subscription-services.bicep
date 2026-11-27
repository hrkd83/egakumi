// 購読管理の入口は同一ACA環境内、プロファイル読取の入口はAPIMのサブネットに限定します。
targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param environmentName string
param registryName string
param imageDigest string
param managementIdentity object
param profileIdentity object
param apimSubnetPrefix string
param apimResourceId string
param subscriptionServiceAudience string
param sqlServer string
param database string
param clientId string
param tenantId string
param publicOrigin string
param authentication object
param profileSecretUri string
param subscriptionSyncSecretUri string

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = { name: environmentName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-01-01' existing = { name: registryName }

var commonEnv = {
  NODE_ENV: 'production'
  HOSTNAME: '0.0.0.0'
  PORT: '3000'
  MICROSOFT_TENANT_ID: tenantId
  PORTAL_SQL_SERVER: sqlServer
  PORTAL_SQL_DATABASE: database
  PORTAL_SQL_AUTH: 'managed-identity'
  PORTAL_CANONICAL_SQL_SERVER: sqlServer
  PORTAL_CANONICAL_SQL_DATABASE: database
  AZURE_TENANT_ID: tenant().tenantId
}
var serviceAccounts = [
  {
    name: 'sub-admin'
    role: 'management'
    entrypoint: '../subscription-admin/server.mjs'
    identity: managementIdentity
    secretName: 'subscription-sync-key'
    secretUri: subscriptionSyncSecretUri
    secretEnv: 'BETTER_AUTH_SECRET'
    env: {
      BETTER_AUTH_URL: publicOrigin
      MICROSOFT_CLIENT_ID: clientId
      PORTAL_AUTH_KEY_ID: authentication.keyId
      PORTAL_AUTH_CERTIFICATE_THUMBPRINT: authentication.certificateThumbprint
      PORTAL_AUTH_SIGNING_MODE: 'managed-identity'
      PORTAL_AUTH_IDENTITY_CLIENT_ID: managementIdentity.clientId
      PORTAL_APIM_AUTH: 'managed-identity'
      PORTAL_APIM_RESOURCE_ID: apimResourceId
      PORTAL_SUBSCRIPTION_SYNC_ENABLED: 'true'
      PORTAL_SUBSCRIPTION_SERVICE_AUDIENCE: subscriptionServiceAudience
    }
  }
  {
    name: 'sub-profile'
    role: 'profile'
    entrypoint: '../subscription-profile/server.mjs'
    identity: profileIdentity
    secretName: 'subscription-profile-secret'
    secretUri: profileSecretUri
    secretEnv: 'PORTAL_SUBSCRIPTION_PROFILE_SECRET'
    env: {}
  }
]

resource services 'Microsoft.App/containerApps@2025-01-01' = [for service in serviceAccounts: {
  name: 'ca-${stem}-${service.name}'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${service.identity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: union({
        external: service.role == 'profile'
        targetPort: 3000
        transport: 'http'
        allowInsecure: false
      }, service.role == 'profile' ? {
        ipSecurityRestrictions: [{ name: 'apim', action: 'Allow', ipAddressRange: apimSubnetPrefix }]
      } : {})
      registries: [{ server: registry.properties.loginServer, identity: service.identity.id }]
      secrets: [{ name: service.secretName, keyVaultUrl: service.secretUri, identity: service.identity.id }]
    }
    template: {
      containers: [{
        name: service.name
        image: '${registry.properties.loginServer}/portal@${imageDigest}'
        command: ['node', '--conditions=react-server', '--experimental-strip-types', service.entrypoint]
        env: concat(
          map(items(union(commonEnv, service.env, {
            PORTAL_SUBSCRIPTION_SERVICE_ROLE: service.role
          })), item => { name: item.key, value: item.value }),
          [
            { name: 'AZURE_CLIENT_ID', value: service.identity.clientId }
            { name: service.secretEnv, secretRef: service.secretName }
          ]
        )
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
}]

output managementFqdn string = services[0].properties.configuration.ingress.fqdn
output profileFqdn string = services[1].properties.configuration.ingress.fqdn
