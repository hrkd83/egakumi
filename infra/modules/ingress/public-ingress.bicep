// Only called after the initial certificate execution and its receipt were verified.
targetScope = 'subscription'

param gatewayPlan object
param ingressMonitoringPlan object
@secure()
param l7IngressSecret string
@secure()
param firewallRootCaData string
@secure()
param monitoringContact object

module certificateReader 'application-gateway-certificate-access.bicep' = {
  name: '${gatewayPlan.resourceStem}-public-certificate-reader'
  scope: resourceGroup(gatewayPlan.vaultResourceGroup)
  params: {
    vaultName: gatewayPlan.certificateVaultName
    certificateName: gatewayPlan.certificateName
    principalId: gatewayPlan.certificateReaderPrincipalId
  }
}

module gateway 'application-gateway.bicep' = {
  name: '${gatewayPlan.resourceStem}-gateway'
  scope: resourceGroup(gatewayPlan.networkResourceGroup)
  params: {
    diagnosticSettingProperties: gatewayPlan.diagnosticSettingProperties
    productName: gatewayPlan.productName
    environmentName: gatewayPlan.environmentName
    resourceStem: gatewayPlan.resourceStem
    wafPolicyIdentifier: gatewayPlan.wafPolicyIdentifier
    location: gatewayPlan.location
    tags: gatewayPlan.tags
    subnetId: gatewayPlan.subnetId
    publicIpId: gatewayPlan.publicIpId
    publicIpAddress: gatewayPlan.publicIpAddress
    apimPrivateIp: gatewayPlan.apimPrivateIp
    portalHostName: gatewayPlan.portalHostName
    portalBackendIp: gatewayPlan.portalBackendIp
    portalBackendHost: gatewayPlan.portalBackendHost
    apimHostName: gatewayPlan.apimHostName
    publicHostName: gatewayPlan.publicHostName
    keyVaultCertificateSecretId: gatewayPlan.keyVaultCertificateSecretId
    certificateIdentityId: gatewayPlan.certificateIdentityId
    skuName: gatewayPlan.skuName
    autoscaleMinCapacity: gatewayPlan.autoscaleMinCapacity
    autoscaleMaxCapacity: gatewayPlan.autoscaleMaxCapacity
    zones: gatewayPlan.zones
    wafMode: gatewayPlan.wafMode
    wafMaxRequestBodySizeInKb: gatewayPlan.wafMaxRequestBodySizeInKb
    wafRequestBodyInspectLimitInKb: gatewayPlan.wafRequestBodyInspectLimitInKb
    wafFileUploadLimitInMb: gatewayPlan.wafFileUploadLimitInMb
    wafCustomRules: gatewayPlan.wafCustomRules
    wafExclusions: gatewayPlan.wafExclusions
    publishedApiPaths: gatewayPlan.publishedApiPaths
    documentUploadPaths: gatewayPlan.documentUploadPaths
    speechRawBodyPaths: gatewayPlan.speechRawBodyPaths
    backendRequestTimeoutSeconds: gatewayPlan.backendRequestTimeoutSeconds
    l7IngressSecret: l7IngressSecret
    firewallRootCaData: firewallRootCaData
  }
  dependsOn: [certificateReader]
}

module monitoringBindings '../monitoring/monitoring-bindings.bicep' = {
  name: '${gatewayPlan.resourceStem}-monitoring-bindings'
  scope: resourceGroup(ingressMonitoringPlan.resourceGroup)
  params: {
    resourceStem: ingressMonitoringPlan.resourceStem
    location: ingressMonitoringPlan.location
    tags: ingressMonitoringPlan.tags
    monitoring: ingressMonitoringPlan.monitoring
    storage: ingressMonitoringPlan.storage
    apimId: ingressMonitoringPlan.apimId
    firewallId: ingressMonitoringPlan.firewallId
    networkResourceIds: ingressMonitoringPlan.networkResourceIds
    resourceGroupIds: ingressMonitoringPlan.resourceGroupIds
    enableLogAlerts: ingressMonitoringPlan.enableLogAlerts
    apimSkuName: ingressMonitoringPlan.apimSkuName
    appInsightsIngestionWarningGb: ingressMonitoringPlan.appInsightsIngestionWarningGb
    telemetryPublisherPrincipalIds: ingressMonitoringPlan.telemetryPublisherPrincipalIds
    certificateMonitoring: ingressMonitoringPlan.certificateMonitoring
    appGatewayId: gateway.outputs.applicationGateway.id
    operatorEmailAddresses: monitoringContact.operatorEmailAddresses
  }
}

output gateway object = gateway.outputs.applicationGateway
