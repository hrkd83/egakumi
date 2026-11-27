// Credential Brokerの専用内部Container Apps環境を配置します。
targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param subnetId string
param diagnosticSettingProperties object

resource brokerEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: 'cae-${stem}-broker'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { infrastructureSubnetId: subnetId, internal: true }
    publicNetworkAccess: 'Disabled'
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: { destination: 'azure-monitor' }
  }
}

resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: brokerEnvironment
  name: 'broker-logs'
  properties: diagnosticSettingProperties
}

output platform object = {
  environmentName: brokerEnvironment.name
  environmentId: brokerEnvironment.id
  infrastructureSubnetId: subnetId
  privateIp: brokerEnvironment.properties.staticIp
  defaultDomain: brokerEnvironment.properties.defaultDomain
}
