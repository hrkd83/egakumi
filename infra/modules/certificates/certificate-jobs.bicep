// Core creates the private environment and its dependencies first. Apply this
// template only after a digest was pushed and the temporary writer was removed.
targetScope = 'resourceGroup'
param jobPlan object
@secure()
param accountEmail string
@minLength(71)
@maxLength(71)
param imageDigest string
param storageAudience string

var image = '${jobPlan.registryServer}/certificate-jobs@${imageDigest}'
var tlsInterval = jobPlan.tlsConfig.renewIntervalHours
var cron = tlsInterval == 24 ? '0 0 * * *' : (24 % tlsInterval == 0 ? '0 */${tlsInterval} * * *' : '0 * * * *')

resource jobs 'Microsoft.App/jobs@2025-07-01' = [for job in [
  { name: jobPlan.jobName, mode: 'tls', identity: jobPlan.tlsIdentity, config: jobPlan.tlsConfig }
  { name: jobPlan.portalJobName, mode: 'portal', identity: jobPlan.portalIdentity, config: jobPlan.portalConfig }
]: {
  name: job.name
  location: jobPlan.location
  tags: jobPlan.tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: union({
      '${jobPlan.pullIdentityId}': {}
      '${job.identity.id}': {}
    }, job.mode == 'tls' ? { '${jobPlan.dnsIdentityId}': {} } : {})
  }
  properties: {
    environmentId: jobPlan.environmentId
    workloadProfileName: 'Consumption'
    configuration: union({
      triggerType: job.mode == 'tls' ? 'Schedule' : 'Manual'
      replicaTimeout: job.mode == 'tls' ? 3300 : 900
      replicaRetryLimit: 0
      registries: [{ server: jobPlan.registryServer, identity: jobPlan.pullIdentityId }]
      identitySettings: concat([
        { identity: jobPlan.pullIdentityId, lifecycle: 'None' }
        { identity: job.identity.id, lifecycle: 'Main' }
      ], job.mode == 'tls' ? [{ identity: jobPlan.dnsIdentityId, lifecycle: 'Main' }] : [])
    }, job.mode == 'tls' ? {
      secrets: [{ name: 'acme-account-contact', value: accountEmail }]
      scheduleTriggerConfig: { cronExpression: cron, parallelism: 1, replicaCompletionCount: 1 }
    } : {
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
    })
    template: {
      containers: [{
        name: 'certificate-worker'
        image: image
        resources: { cpu: 1, memory: '2Gi' }
        env: concat([
          { name: 'CERTIFICATE_MODE', value: job.mode }
          { name: 'CERTIFICATE_ACTION', value: job.mode == 'tls' ? 'renew' : 'initial' }
          { name: 'CERTIFICATE_CONFIG_JSON', value: string(job.config) }
          { name: 'CERTIFICATE_CLIENT_ID', value: job.identity.clientId }
          { name: 'CERTIFICATE_DNS_CLIENT_ID', value: job.mode == 'tls' ? jobPlan.dnsClientId : '' }
          { name: 'CERTIFICATE_STORAGE_URL', value: jobPlan.storageUrl }
          { name: 'CERTIFICATE_STORAGE_AUDIENCE', value: storageAudience }
          { name: 'CERTIFICATE_RECEIPT_CONTAINER', value: job.mode == 'tls' ? jobPlan.receiptContainerName : jobPlan.portalStateContainerName }
          { name: 'CERTIFICATE_PORTAL_STATE_CONTAINER', value: job.mode == 'portal' ? jobPlan.portalStateContainerName : '' }
        ], job.mode == 'tls' ? [{ name: 'CERTIFICATE_ACCOUNT_EMAIL', secretRef: 'acme-account-contact' }] : [])
      }]
    }
  }
}]

output tlsJobId string = jobs[0].id
output portalJobId string = jobs[1].id
