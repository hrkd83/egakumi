// Private data planes for the isolated certificate environment. Configure the
// exact PE addresses and end-of-list NSG denial before creating the CAE.
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param subnetIds object
param subnetPrefixes object
param nsgNames object
param firewallPolicyName string
param managementPrivateIp string
param keyVaultPrivateIps array
param registryPrivateIps array
param tlsVaultId string
param storageAccountId string
param vaultPrivateDnsZoneId string
param blobPrivateDnsZoneId string
param acmeIssuerHost string

resource tlsVaultPe 'Microsoft.Network/privateEndpoints@2025-01-01' = {
  name: 'pe-${stem}-tls-vault'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetIds.pep }
    privateLinkServiceConnections: [{
      name: 'vault'
      properties: { privateLinkServiceId: tlsVaultId, groupIds: ['vault'] }
    }]
  }
}
resource blobPe 'Microsoft.Network/privateEndpoints@2025-01-01' = {
  name: 'pe-${stem}-certificate-blob'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetIds.pep }
    privateLinkServiceConnections: [{
      name: 'blob'
      properties: { privateLinkServiceId: storageAccountId, groupIds: ['blob'] }
    }]
  }
  dependsOn: [tlsVaultPe]
}
resource vaultDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  parent: tlsVaultPe
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'vault', properties: { privateDnsZoneId: vaultPrivateDnsZoneId } }] }
}
resource blobDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  parent: blobPe
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: blobPrivateDnsZoneId } }] }
}

module vaultIps 'private-endpoint-addresses.bicep' = {
  name: '${stem}-tls-vault-addresses'
  params: { privateEndpointNicIds: map(tlsVaultPe.properties.networkInterfaces, nic => nic.id) }
}
module blobIps 'private-endpoint-addresses.bicep' = {
  name: '${stem}-certificate-blob-addresses'
  params: { privateEndpointNicIds: map(blobPe.properties.networkInterfaces, nic => nic.id) }
}

var vaultCidrs = map(union([], flatten(vaultIps.outputs.privateIpsByNic)), ip => '${ip}/32')
var blobCidrs = map(union([], flatten(blobIps.outputs.privateIpsByNic)), ip => '${ip}/32')
var regionalRegistryStorage = 'Storage.${replace(location, ' ', '')}'
var certificateDestinations = union(vaultCidrs, blobCidrs, map(union(keyVaultPrivateIps, registryPrivateIps), ip => '${ip}/32'))

func prefixProperties(sources array, destinations array) object => union(
  length(sources) == 1 ? { sourceAddressPrefix: sources[0] } : { sourceAddressPrefixes: sources },
  length(destinations) == 1 ? { destinationAddressPrefix: destinations[0] } : { destinationAddressPrefixes: destinations }
)

var knownTargets = {
  'subnet:agw': [subnetPrefixes.agw]
  'subnet:certificate': [subnetPrefixes.certificate]
  'ip:management': ['${managementPrivateIp}/32']
  'ips:certificateVault': vaultCidrs
  'ips:certificateBlob': blobCidrs
  'ips:keyVault': map(keyVaultPrivateIps, ip => '${ip}/32')
  'ips:registry': map(registryPrivateIps, ip => '${ip}/32')
}
var boundary = loadJsonContent('../certificate-nsg-rules.json').groups
var sharedRules = flatten(map(items(boundary), group => map(items(group.value), rule => {
  name: '${nsgNames[group.key]}/${rule.key}'
  definition: rule.value
})))
@batchSize(1)
resource sharedRulesOnNsg 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for rule in sharedRules: {
  name: rule.name
  properties: union({
    priority: rule.definition.priority
    direction: rule.definition.direction
    access: rule.definition.access
    protocol: rule.definition.protocol
    sourcePortRange: rule.definition.sourcePort
    destinationPortRange: first(rule.definition.destinationPorts)
  }, prefixProperties(flatten(map(rule.definition.source, token => knownTargets[token])), flatten(map(rule.definition.destination, token => knownTargets[token]))))
}]

var certificateRules = [
  { name: 'allow-aca-health-ingress', priority: 320, direction: 'Inbound', protocol: 'Tcp', source: ['AzureLoadBalancer'], target: [subnetPrefixes.certificate], ports: ['30000-32767'] }
  { name: 'allow-aca-internal-ingress', priority: 321, direction: 'Inbound', protocol: '*', source: [subnetPrefixes.certificate], target: [subnetPrefixes.certificate], ports: ['*'] }
  { name: 'allow-private-https-egress', priority: 220, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: [], ports: ['443'] }
  { name: 'allow-aca-internal-egress', priority: 320, direction: 'Outbound', protocol: '*', source: [subnetPrefixes.certificate], target: [subnetPrefixes.certificate], ports: ['*'] }
  { name: 'allow-entra-egress', priority: 321, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['AzureActiveDirectory'], ports: ['443'] }
  { name: 'allow-monitor-egress', priority: 322, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['AzureMonitor'], ports: ['443'] }
  { name: 'allow-arm-egress', priority: 323, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['AzureResourceManager'], ports: ['443'] }
  { name: 'allow-mcr-egress', priority: 324, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['MicrosoftContainerRegistry'], ports: ['443'] }
  { name: 'allow-afd-first-party-egress', priority: 325, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['AzureFrontDoor.FirstParty'], ports: ['443'] }
  { name: 'allow-firewall-routed-https-egress', priority: 326, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: ['Internet'], ports: ['443'] }
  { name: 'allow-platform-dns-egress', priority: 327, direction: 'Outbound', protocol: '*', source: [subnetPrefixes.certificate], target: ['AzurePlatformDNS'], ports: ['53'] }
  { name: 'allow-regional-registry-storage-egress', priority: 328, direction: 'Outbound', protocol: 'Tcp', source: [subnetPrefixes.certificate], target: [regionalRegistryStorage], ports: ['443'] }
]

resource certificateNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: nsgNames.certificate }
@batchSize(1)
resource allow 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for rule in certificateRules: {
  parent: certificateNsg
  name: rule.name
  properties: union({
    priority: rule.priority
    direction: rule.direction
    access: 'Allow'
    protocol: rule.protocol
    sourcePortRange: '*'
    destinationPortRange: first(rule.ports)
  }, prefixProperties(rule.source, rule.name == 'allow-private-https-egress' ? certificateDestinations : rule.target))
}]
@batchSize(1)
resource deny 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for direction in ['Inbound', 'Outbound']: {
  parent: certificateNsg
  name: 'deny-any-${direction == 'Inbound' ? 'ingress' : 'egress'}'
  properties: {
    priority: 4096
    direction: direction
    access: 'Deny'
    protocol: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
  }
  dependsOn: [allow]
}]

// The UDR sends every public destination through Azure Firewall. The NSG's
// HTTPS allowance does not bypass this fixed service-tag and FQDN policy.
resource policy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = { name: firewallPolicyName }
resource firewallRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: policy
  name: 'rcg-certificate'
  properties: {
    priority: 370
    ruleCollections: [
      {
        name: 'certificate-controls'
        priority: 100
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'allow-certificate-azure-control-plane'
          ruleType: 'NetworkRule'
          sourceAddresses: [subnetPrefixes.certificate]
          destinationAddresses: ['AzureResourceManager', 'AzureActiveDirectory', 'AzureMonitor', 'MicrosoftContainerRegistry', 'AzureFrontDoor.FirstParty', regionalRegistryStorage]
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        }]
      }
      {
        name: 'certificate-runtime'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [
          {
            name: 'allow-certificate-acme'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.certificate]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: [acmeIssuerHost]
            terminateTLS: false
          }
          {
            name: 'allow-certificate-aca-platform'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.certificate]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: ['packages.aks.azure.com', 'acs-mirror.azureedge.net', '*.identity.azure.net']
            terminateTLS: false
          }
        ]
      }
    ]
  }
}
