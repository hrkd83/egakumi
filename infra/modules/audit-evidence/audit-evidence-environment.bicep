targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param subnetId string
param diagnosticSettingProperties object

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: 'cae-${stem}-audit'
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
  scope: environment
  name: 'audit-evidence-runtime'
  properties: diagnosticSettingProperties
}

output platform object = {
  environmentId: environment.id
  environmentName: environment.name
  environmentDomain: environment.properties.defaultDomain
  privateIp: environment.properties.staticIp
}
