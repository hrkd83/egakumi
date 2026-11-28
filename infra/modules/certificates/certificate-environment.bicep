targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param subnetId string
param diagnosticSettingProperties object

resource certificateEnvironment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-${stem}-certificate'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { infrastructureSubnetId: subnetId, internal: true }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: { destination: 'azure-monitor' }
  }
}
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'certificate-jobs'
  scope: certificateEnvironment
  properties: diagnosticSettingProperties['certificate-logs']
}

output id string = certificateEnvironment.id
output name string = certificateEnvironment.name
