// ACRへのPE・Private DNS・NSG・Firewall規則をネットワークRGへ配置します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param registryId string
param vnetId string
param pepSubnetId string
param managementPrefix string
param managementNsgName string
param acaPrefix string
param agwPrefix string
param firewallPolicyName string
param pepNsgName string
// 環境の認証URLからホスト部分を取り出して、Firewallの宛先に使います。
var entraLoginHost = split(az.environment().authentication.loginEndpoint, '/')[2]

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.azurecr.io'
  location: 'global'
  tags: tags
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-${stem}'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2025-01-01' = {
  name: 'pe-${stem}-acr'
  location: location
  tags: tags
  properties: {
    subnet: { id: pepSubnetId }
    privateLinkServiceConnections: [{
      name: 'registry'
      properties: { privateLinkServiceId: registryId, groupIds: ['registry'] }
    }]
  }
}
resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  parent: endpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'registry', properties: { privateDnsZoneId: zone.id } }] }
}
module registryAddresses 'private-endpoint-addresses.bicep' = {
  name: '${stem}-acr-addresses'
  params: { privateEndpointNicIds: map(endpoint.properties.networkInterfaces, nic => nic.id) }
}
var registryPrivateIps = union([], flatten(registryAddresses.outputs.privateIpsByNic))
resource pepNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: pepNsgName }
resource allowPull 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-aca-acr'
  properties: {
    priority: 160
    direction: 'Inbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourceAddressPrefixes: [acaPrefix, managementPrefix]
    destinationAddressPrefixes: registryPrivateIps
    sourcePortRange: '*'
    destinationPortRange: '443'
  }
}
resource managementNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: managementNsgName }
resource runnerRegistryAccess 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  name: 'allow-runner-acr'
  properties: {
    priority: 170
    direction: 'Outbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourceAddressPrefix: managementPrefix
    destinationAddressPrefixes: registryPrivateIps
    sourcePortRange: '*'
    destinationPortRange: '443'
  }
}
resource policy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = { name: firewallPolicyName }
// Coreの通信規則が揃った後、Portal用の独立した規則グループを設定します。
resource rules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: policy
  name: 'rcg-portal'
  properties: {
    priority: 350
    ruleCollections: [
      {
        name: 'portal-azure-monitor'
        priority: 150
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'aca-monitor-https'
          ruleType: 'NetworkRule'
          sourceAddresses: [acaPrefix]
          destinationAddresses: ['AzureMonitor', 'AzureActiveDirectory']
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        }]
      }
      {
        name: 'portal-runner'
        priority: 300
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'github-docker-node-build'
          ruleType: 'ApplicationRule'
          sourceAddresses: [managementPrefix]
          protocols: [{ protocolType: 'Https', port: 443 }]
          targetFqdns: [
            'github.com'
            '*.github.com'
            '*.githubusercontent.com'
            'registry.npmjs.org'
            'nodejs.org'
            'auth.docker.io'
            'registry-1.docker.io'
            'production.cloudflare.docker.com'
            'production.cloudfront.docker.com'
            '*.r2.cloudflarestorage.com'
          ]
          terminateTLS: false
        }]
      }
      {
        name: 'portal-ingress'
        priority: 100
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'agw-aca-https'
          ruleType: 'NetworkRule'
          sourceAddresses: [agwPrefix]
          destinationAddresses: [acaPrefix]
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        }]
      }
      {
        name: 'portal-platform-and-probe'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'platform-and-fixed-egress-check'
          ruleType: 'ApplicationRule'
          sourceAddresses: [acaPrefix]
          protocols: [{ protocolType: 'Https', port: 443 }]
          targetFqdns: [
            'mcr.microsoft.com'
            '*.data.mcr.microsoft.com'
            'packages.aks.azure.com'
            'acs-mirror.azureedge.net'
            '*.identity.azure.net'
            entraLoginHost
            '*.${entraLoginHost}'
            '*.login.microsoft.com'
            'login.microsoft.com'
            'api.ipify.org'
          ]
          terminateTLS: false
        }]
      }
    ]
  }
}

output registryPrivateIps array = registryPrivateIps
output registryPrivateEndpointId string = endpoint.id
