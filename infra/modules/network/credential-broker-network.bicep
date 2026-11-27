// Portalと認証窓口が共用するSQL・Vault経路を対象サブネットに限定します。
targetScope = 'resourceGroup'

import { allowRule, denyProperties } from './nsg/rule-properties.bicep'
param stem string
param location string
param tags object
param sqlServerId string
param vnetId string
param pepSubnetId string
param pepNsgName string
param portalPrefix string
param brokerPrefix string
param brokerNsgName string
param apimPrefix string
param apimNsgName string
param brokerPrivateIp string
param firewallPolicyName string
param managementPrefix string
param managementPrivateIp string
param managementNsgName string
param portalNsgName string
var nsgPriorities = loadJsonContent('nsg/core-rules.json')

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。
resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.database.windows.net'
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
  name: 'pe-${stem}-sql'
  location: location
  tags: tags
  properties: {
    subnet: { id: pepSubnetId }
    privateLinkServiceConnections: [{ name: 'sql', properties: { privateLinkServiceId: sqlServerId, groupIds: ['sqlServer'] } }]
  }
}
resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  parent: endpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'sql', properties: { privateDnsZoneId: zone.id } }] }
}
module addresses 'private-endpoint-addresses.bicep' = {
  name: '${stem}-sql-addresses'
  params: { privateEndpointNicIds: map(endpoint.properties.networkInterfaces, nic => nic.id) }
}
var ips = union([], flatten(addresses.outputs.privateIpsByNic))
resource pepNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: pepNsgName }
resource apimNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: apimNsgName }
resource managementNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: managementNsgName }
resource portalNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: portalNsgName }
resource brokerNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: brokerNsgName }
resource sqlIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-aca-sql-1433-ingress'
  properties: allowRule('allow-aca-sql-1433-ingress', nsgPriorities.groups.pep.rules['allow-aca-sql-1433-ingress'].priority, 'Inbound', 'Tcp', [portalPrefix, brokerPrefix], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource managementSqlIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  dependsOn: [sqlIn]
  name: 'allow-mgmt-vm-sql-1433-ingress'
  properties: allowRule('allow-mgmt-vm-sql-1433-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-sql-1433-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource brokerOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: apimNsg
  name: 'allow-broker-https-egress'
  properties: allowRule('allow-broker-https-egress', nsgPriorities.groups.apim.rules['allow-broker-https-egress'].priority, 'Outbound', 'Tcp', [apimPrefix], ['${brokerPrivateIp}/32'], ['443']).properties
}
resource portalSqlOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: portalNsg
  name: 'allow-sql-1433-egress'
  properties: allowRule('allow-sql-1433-egress', nsgPriorities.groups.portal.rules['allow-sql-1433-egress'].priority, 'Outbound', 'Tcp', [portalPrefix], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource brokerSqlOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: brokerNsg
  name: 'allow-sql-1433-egress'
  properties: allowRule('allow-sql-1433-egress', nsgPriorities.groups.broker.rules['allow-sql-1433-egress'].priority, 'Outbound', 'Tcp', [brokerPrefix], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource managementSqlOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  name: 'allow-sql-1433-egress'
  properties: allowRule('allow-sql-1433-egress', nsgPriorities.groups.management.rules['allow-sql-1433-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource firewall 'Microsoft.Network/firewallPolicies@2025-01-01' existing = { name: firewallPolicyName }
resource rules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewall
  name: 'rcg-credential-broker'
  properties: {
    priority: 360
    ruleCollections: [
      // APIM→Broker、PortalとBroker→SQL/Key Vault PE は VNet 内の直接経路です。
      // Firewall を通過しないため、この規則群には登録せず各サブネットの NSG で許可します。
      { name: 'credential-broker-azure', priority: 200, ruleCollectionType: 'FirewallPolicyFilterRuleCollection', action: { type: 'Allow' }, rules: [
        { name: 'allow-aca-entra-login', ruleType: 'ApplicationRule', sourceAddresses: [portalPrefix, brokerPrefix], protocols: [{ protocolType: 'Https', port: 443 }], targetFqdns: ['login.microsoftonline.com'], terminateTLS: false }
        { name: 'allow-broker-arm', ruleType: 'ApplicationRule', sourceAddresses: [brokerPrefix], protocols: [{ protocolType: 'Https', port: 443 }], targetFqdns: ['management.azure.com'], terminateTLS: false }
        { name: 'allow-mgmt-vm-python-build', ruleType: 'ApplicationRule', sourceAddresses: [managementPrefix], protocols: [{ protocolType: 'Https', port: 443 }], targetFqdns: ['pypi.org', 'files.pythonhosted.org', 'packages.microsoft.com', 'deb.debian.org', 'security.debian.org'], terminateTLS: false }
      ] }
    ]
  }
}
// ACR・Key Vault・基盤向けの許可は先行モジュールで用意済みです。
// 両向きのSQL PE規則とFirewall許可を登録してから、ACAの末尾Denyを順に配置します。
resource portalIngressDeny 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: portalNsg
  name: 'deny-any-ingress'
  properties: denyProperties('Inbound', nsgPriorities.groups.portal.rules['deny-any-ingress'].priority)
  dependsOn: [portalSqlOut, sqlIn, rules]
}
resource portalEgressDeny 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: portalNsg
  name: 'deny-any-egress'
  properties: denyProperties('Outbound', nsgPriorities.groups.portal.rules['deny-any-egress'].priority)
  dependsOn: [portalIngressDeny]
}

resource brokerIngressDeny 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: brokerNsg
  name: 'deny-any-ingress'
  properties: denyProperties('Inbound', nsgPriorities.groups.broker.rules['deny-any-ingress'].priority)
  dependsOn: [brokerSqlOut, sqlIn, rules, portalEgressDeny]
}
resource brokerEgressDeny 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: brokerNsg
  name: 'deny-any-egress'
  properties: denyProperties('Outbound', nsgPriorities.groups.broker.rules['deny-any-egress'].priority)
  dependsOn: [brokerIngressDeny]
}

output endpointId string = endpoint.id
