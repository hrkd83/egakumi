// CAEの実際のドメイン・内部VIPを使い、DNSとAppGW側の経路をネットワークRGで仕上げます。
targetScope = 'resourceGroup'

import { allowRule } from './nsg/rule-properties.bicep'
param stem string
param defaultDomain string
param staticIp string
param vnetId string
param agwRouteTableName string
param firewallIp string
param firewallPolicyName string
param apimGatewayFqdn string
param portalBackendHost string
param agwNsgName string
param managementNsgName string
param agwPrefix string
param managementPrivateIp string

module appDns '../container-apps/container-app-private-dns.bicep' = {
  name: '${stem}-portal-dns'
  params: { defaultDomain: defaultDomain, staticIp: staticIp, stem: stem, vnetId: vnetId }
}
resource agwRoutes 'Microsoft.Network/routeTables@2025-01-01' existing = { name: agwRouteTableName }
resource ingressRoute 'Microsoft.Network/routeTables/routes@2025-01-01' = {
  parent: agwRoutes
  name: 'portal-via-firewall'
  properties: {
    addressPrefix: '${staticIp}/32'
    nextHopType: 'VirtualAppliance'
    nextHopIpAddress: firewallIp
  }
}

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = {
  name: firewallPolicyName
}

// AppGW再暗号化後の通信だけを、宛先FQDNで検査して再暗号化します。
// 同じ通信に一致する先行Network Ruleは置きません。
resource ingressRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewallPolicy
  name: 'rcg-ingress'
  properties: {
    priority: 200
    ruleCollections: [{
      name: 'allow-agw-to-backends'
      priority: 200
      ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
      action: { type: 'Allow' }
      rules: map([
        { name: 'allow-appgw-apim-https', fqdn: apimGatewayFqdn }
        { name: 'allow-appgw-portal-https', fqdn: portalBackendHost }
      ], backend => {
        name: backend.name
        ruleType: 'ApplicationRule'
        sourceAddresses: [agwPrefix]
        protocols: [{ protocolType: 'Https', port: 443 }]
        targetFqdns: [backend.fqdn]
        terminateTLS: true
      })
    }]
  }
  dependsOn: [appDns, ingressRoute]
}

var nsgPriorities = loadJsonContent('nsg/core-rules.json')

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。
resource agwNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: agwNsgName }
resource managementNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: managementNsgName }
resource appgwPortalOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: agwNsg
  name: 'allow-aca-https-egress'
  properties: allowRule('allow-aca-https-egress', nsgPriorities.groups.agw.rules['allow-aca-https-egress'].priority, 'Outbound', 'Tcp', [agwPrefix], ['${staticIp}/32'], ['443']).properties
}
resource managementPortalHttpsOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  name: 'allow-aca-https-egress'
  properties: allowRule('allow-aca-https-egress', nsgPriorities.groups.management.rules['allow-aca-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], ['${staticIp}/32'], ['443']).properties
}
