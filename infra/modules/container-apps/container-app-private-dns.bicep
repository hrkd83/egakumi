param defaultDomain string
param staticIp string
param stem string
param vnetId string
resource appZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: defaultDomain
  location: 'global'
}
resource appLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: appZone
  name: 'link-${stem}'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } }
}
resource appRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: appZone
  name: '*'
  properties: { ttl: 300, aRecords: [{ ipv4Address: staticIp }] }
}
