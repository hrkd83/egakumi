targetScope = 'resourceGroup'

param foundation object
param registryServer string
param image string
param relayEnvironment array
param workerEnvironment array = []

var connectionEnvironment = [
  { name: 'AUDIT_EVENTHUB_NAMESPACE', value: foundation.eventHubsNamespaceFqdn }
  { name: 'AUDIT_EVENTHUB_NAME', value: foundation.eventHubName }
]

var relayFixedEnvironment = concat(connectionEnvironment, [
  { name: 'AZURE_CLIENT_ID', value: foundation.relay.clientId }
  { name: 'AUDIT_FOUNDRY_OPENAI_ENDPOINT', value: foundation.foundryOpenAiEndpoint }
])

var workerFixedEnvironment = concat(connectionEnvironment, [
  { name: 'AZURE_CLIENT_ID', value: foundation.worker.clientId }
  { name: 'AUDIT_EVENTHUB_CONSUMER_GROUP', value: foundation.consumerGroupName }
  { name: 'AUDIT_STORAGE_ACCOUNT_URL', value: foundation.evidenceBlobEndpoint }
  { name: 'AUDIT_BLOB_CONTAINER', value: foundation.evidenceContainerName }
  { name: 'AUDIT_CHECKPOINT_STORAGE_ACCOUNT_URL', value: foundation.checkpointBlobEndpoint }
  { name: 'AUDIT_CHECKPOINT_CONTAINER', value: foundation.checkpointContainerName }
])

var probes = [
  { type: 'Startup', httpGet: { port: 8000, path: '/health' }, periodSeconds: 5, failureThreshold: 24 }
  { type: 'Liveness', httpGet: { port: 8000, path: '/health' }, periodSeconds: 15, failureThreshold: 3 }
  { type: 'Readiness', httpGet: { port: 8000, path: '/ready' }, periodSeconds: 5, timeoutSeconds: 4, failureThreshold: 3 }
]

resource relay 'Microsoft.App/containerApps@2025-01-01' = {
  name: foundation.relay.name
  location: foundation.location
  tags: foundation.tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${foundation.relay.identityResourceId}': {} } }
  properties: {
    managedEnvironmentId: foundation.environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: foundation.relay.identityResourceId }]
      // External at app scope uses the private load balancer of this internal environment.
      ingress: {
        external: true
        allowInsecure: false
        transport: 'auto'
        targetPort: 8000
        ipSecurityRestrictions: [{ name: 'allow-apim-subnet', action: 'Allow', ipAddressRange: foundation.apimSubnetPrefix }]
      }
    }
    template: {
      scale: { minReplicas: 2, maxReplicas: 8, rules: [{ name: 'requests', http: { metadata: { concurrentRequests: '10' } } }] }
      containers: [{
        name: 'relay'
        image: image
        env: concat(relayFixedEnvironment, filter(relayEnvironment, entry => !contains(map(relayFixedEnvironment, entry => entry.name), entry.name)))
        probes: probes
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}

resource worker 'Microsoft.App/containerApps@2025-01-01' = {
  name: foundation.worker.name
  location: foundation.location
  tags: foundation.tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${foundation.worker.identityResourceId}': {} } }
  properties: {
    managedEnvironmentId: foundation.environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: foundation.worker.identityResourceId }]
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [{
        name: 'worker'
        image: image
        command: ['python', '-B', '-m', 'audit_relay', 'worker']
        env: concat(workerFixedEnvironment, filter(workerEnvironment, entry => !contains(map(workerFixedEnvironment, entry => entry.name), entry.name)))
        probes: probes
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}

output relay object = { name: relay.name, id: relay.id, fqdn: relay.properties.configuration.ingress.fqdn }
output worker object = { name: worker.name, id: worker.id }
