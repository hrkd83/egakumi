targetScope = 'resourceGroup'

param stem string
param relayName string
param defaultDomain string
param privateIp string
param vnetId string
param tags object
param nsgNames object
param prefixes object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: defaultDomain
  location: 'global'
  tags: tags
}

resource zoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-${stem}-audit'
  location: 'global'
  tags: tags
  properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } }
}

resource relayDns 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: relayName
  properties: { ttl: 300, aRecords: [{ ipv4Address: privateIp }] }
}

module rules './audit-evidence-nsg.bicep' = {
  name: '${stem}-audit-evidence-apim-nsg'
  params: {
    nsgNames: nsgNames
    groups: ['apim']
    prefixes: prefixes
    ips: { audit: privateIp }
    ipSets: {}
  }
}
