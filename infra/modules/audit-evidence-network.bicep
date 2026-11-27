targetScope = 'resourceGroup'

param stem string
param location string
param tags object
param data object
param vnetId string
param pepSubnetId string
param nsgNames object
param prefixes object
param managementPrivateIp string
param foundryPrivateIps array
param registryPrivateIps array
param blobZoneId string
param firewallPolicyName string

resource eventHubZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.${join(skip(split(data.eventHubsNamespaceFqdn, '.'), 1), '.')}'
  location: 'global'
  tags: tags
}

resource eventHubZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: eventHubZone
  name: 'link-${stem}'
  location: 'global'
  tags: tags
  properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } }
}

var definitions = [
  { suffix: 'audit-eventhubs', id: data.eventHubsNamespaceId, groupId: 'namespace' }
  { suffix: 'audit-evidence', id: data.evidenceStorageId, groupId: 'blob' }
  { suffix: 'audit-checkpoint', id: data.checkpointStorageId, groupId: 'blob' }
]

@batchSize(1)
resource endpoints 'Microsoft.Network/privateEndpoints@2025-01-01' = [for item in definitions: {
  name: 'pe-${stem}-${item.suffix}'
  location: location
  tags: tags
  properties: {
    customNetworkInterfaceName: 'pe-${stem}-${item.suffix}-nic'
    subnet: { id: pepSubnetId }
    privateLinkServiceConnections: [{ name: item.groupId, properties: { privateLinkServiceId: item.id, groupIds: [item.groupId] } }]
  }
}]

@batchSize(1)
resource zoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = [for (item, i) in definitions: {
  parent: endpoints[i]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [{ name: item.groupId, properties: { privateDnsZoneId: item.groupId == 'namespace' ? eventHubZone.id : blobZoneId } }]
  }
}]

module addresses './private-endpoint-addresses.bicep' = [for (item, i) in definitions: {
  name: '${stem}-${item.suffix}-addresses'
  params: { privateEndpointNicIds: map(endpoints[i].properties.networkInterfaces, nic => nic.id) }
}]

module rules './audit-evidence-nsg.bicep' = {
  name: '${stem}-audit-evidence-nsg'
  params: {
    nsgNames: nsgNames
    groups: ['audit', 'pep', 'management']
    prefixes: prefixes
    ips: { management: managementPrivateIp }
    ipSets: {
      foundry: foundryPrivateIps
      registry: registryPrivateIps
      evidenceEventHubs: union([], flatten(addresses[0].outputs.privateIpsByNic))
      evidenceBlob: union([], flatten(addresses[1].outputs.privateIpsByNic))
      evidenceCheckpoint: union([], flatten(addresses[2].outputs.privateIpsByNic))
    }
  }
}

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = { name: firewallPolicyName }
var loginHost = split(environment().authentication.loginEndpoint, '/')[2]

// PrivateLink endpoints use the VNet route. The default UDR brings only these
// explicit platform flows to Firewall; no general public internet egress.
resource platformRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewallPolicy
  name: 'rcg-audit-evidence'
  properties: {
    priority: 375
    ruleCollections: [
      {
        name: 'audit-platform-tags'
        priority: 100
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'allow-audit-arm-monitor-entra'
          ruleType: 'NetworkRule'
          sourceAddresses: [prefixes.audit]
          destinationAddresses: ['AzureResourceManager', 'AzureMonitor', 'AzureActiveDirectory']
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        }]
      }
      {
        name: 'audit-platform-hosts'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'allow-audit-platform-hosts'
          ruleType: 'ApplicationRule'
          sourceAddresses: [prefixes.audit]
          protocols: [{ protocolType: 'Https', port: 443 }]
          targetFqdns: ['mcr.microsoft.com', '*.data.mcr.microsoft.com', 'packages.aks.azure.com', 'acs-mirror.azureedge.net', '*.identity.azure.net', loginHost, '*.${loginHost}', '*.login.microsoft.com', 'login.microsoft.com']
          terminateTLS: false
        }]
      }
    ]
  }
}
