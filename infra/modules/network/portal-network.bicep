// ACRへのPE・Private DNS・NSG・Firewall規則をネットワークRGへ配置します。
targetScope = 'resourceGroup'

import { allowRule } from './nsg/rule-properties.bicep'
param stem string
param location string
param tags object
param registryId string
param vnetId string
param pepSubnetId string
param managementPrefix string
param managementNsgName string
param managementPrivateIp string
param portalNsgName string
param portalPrefix string
param brokerPrefix string
param brokerNsgName string
param firewallPolicyName string
param pepNsgName string
// 環境の認証URLからホスト部分を取り出して、Firewallの宛先に使います。
var entraLoginHost = split(az.environment().authentication.loginEndpoint, '/')[2]
var nsgPriorities = loadJsonContent('nsg/core-rules.json')

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。

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
resource managementNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: managementNsgName }
resource portalNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: portalNsgName }
resource brokerNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: brokerNsgName }
resource registryIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-aca-acr-https-ingress'
  properties: allowRule('allow-aca-acr-https-ingress', nsgPriorities.groups.pep.rules['allow-aca-acr-https-ingress'].priority, 'Inbound', 'Tcp', [portalPrefix, brokerPrefix], map(registryPrivateIps, ip => '${ip}/32'), ['443']).properties
}
resource managementRegistryIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  dependsOn: [registryIn]
  name: 'allow-mgmt-vm-acr-https-ingress'
  properties: allowRule('allow-mgmt-vm-acr-https-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-acr-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(registryPrivateIps, ip => '${ip}/32'), ['443']).properties
}
resource runnerRegistryAccess 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  name: 'allow-acr-https-egress'
  properties: allowRule('allow-acr-https-egress', nsgPriorities.groups.management.rules['allow-acr-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], map(registryPrivateIps, ip => '${ip}/32'), ['443']).properties
}
resource portalRegistryOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: portalNsg
  name: 'allow-acr-https-egress'
  properties: allowRule('allow-acr-https-egress', nsgPriorities.groups.portal.rules['allow-acr-https-egress'].priority, 'Outbound', 'Tcp', [portalPrefix], map(registryPrivateIps, ip => '${ip}/32'), ['443']).properties
}
resource brokerRegistryOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: brokerNsg
  name: 'allow-acr-https-egress'
  properties: allowRule('allow-acr-https-egress', nsgPriorities.groups.broker.rules['allow-acr-https-egress'].priority, 'Outbound', 'Tcp', [brokerPrefix], map(registryPrivateIps, ip => '${ip}/32'), ['443']).properties
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
        rules: [
          {
            name: 'allow-aca-monitor'
            ruleType: 'NetworkRule'
            sourceAddresses: [portalPrefix, brokerPrefix]
            destinationAddresses: ['AzureMonitor']
            destinationPorts: ['443']
            ipProtocols: ['TCP']
          }
          {
            name: 'allow-aca-entra'
            ruleType: 'NetworkRule'
            sourceAddresses: [portalPrefix, brokerPrefix]
            destinationAddresses: ['AzureActiveDirectory']
            destinationPorts: ['443']
            ipProtocols: ['TCP']
          }
        ]
      }
      {
        name: 'portal-runner'
        priority: 300
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [{
          name: 'allow-mgmt-vm-github-docker-node-build'
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
        name: 'portal-platform-and-probe'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [
          {
            name: 'allow-aca-mcr'
            ruleType: 'ApplicationRule'
            sourceAddresses: [portalPrefix, brokerPrefix]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: ['mcr.microsoft.com', '*.data.mcr.microsoft.com']
            terminateTLS: false
          }
          {
            name: 'allow-aca-platform-packages'
            ruleType: 'ApplicationRule'
            sourceAddresses: [portalPrefix, brokerPrefix]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: ['packages.aks.azure.com', 'acs-mirror.azureedge.net']
            terminateTLS: false
          }
          {
            name: 'allow-aca-managed-identity'
            ruleType: 'ApplicationRule'
            sourceAddresses: [portalPrefix, brokerPrefix]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: [
              '*.identity.azure.net'
              entraLoginHost
              '*.${entraLoginHost}'
              '*.login.microsoft.com'
              'login.microsoft.com'
            ]
            terminateTLS: false
          }
        ]
      }
    ]
  }
}

output registryPrivateIps array = registryPrivateIps
output registryPrivateEndpointId string = endpoint.id
