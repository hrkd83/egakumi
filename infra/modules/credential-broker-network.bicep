// Portalと認証窓口が共用するSQL・Vault経路を対象サブネットに限定します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param sqlServerId string
param vnetId string
param pepSubnetId string
param pepNsgName string
param acaPrefix string
param apimPrefix string
param apimNsgName string
param acaPrivateIp string
param firewallPolicyName string
param managementPrefix string
param managementPrivateIp string
param managementNsgName string
param acaNsgName string
param keyVaultPrivateIps array
@description('CM未使用時はAPIMから認証窓口への経路を追加しません。')
param enableCredentialBroker bool = true
var nsgPriorities = loadJsonContent('../nsg-rule-priorities.json')

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。
func allowRule(ruleName string, priority int, direction string, protocol string, sources array, destinations array, ports array) object => {
  name: ruleName
  properties: union({
    description: 'Initial fixed communication: ${ruleName}'
    priority: priority
    direction: direction
    access: 'Allow'
    protocol: protocol
    sourcePortRange: '*'
  }, length(sources) == 1 ? {
    sourceAddressPrefix: sources[0]
  } : {
    sourceAddressPrefixes: sources
  }, length(destinations) == 1 ? {
    destinationAddressPrefix: destinations[0]
  } : {
    destinationAddressPrefixes: destinations
  }, length(ports) == 1 ? {
    destinationPortRange: ports[0]
  } : {
    destinationPortRanges: ports
  })
}

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
resource acaNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: acaNsgName }
resource sqlIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-aca-sql-1433-ingress'
  properties: allowRule('allow-aca-sql-1433-ingress', nsgPriorities.groups.pep.rules['allow-aca-sql-1433-ingress'].priority, 'Inbound', 'Tcp', [acaPrefix], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource managementSqlIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  dependsOn: [sqlIn]
  name: 'allow-mgmt-vm-sql-1433-ingress'
  properties: allowRule('allow-mgmt-vm-sql-1433-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-sql-1433-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(ips, ip => '${ip}/32'), ['1433']).properties
}
resource brokerOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: apimNsg
  name: 'allow-aca-https-egress'
  properties: allowRule('allow-aca-https-egress', nsgPriorities.groups.apim.rules['allow-aca-https-egress'].priority, 'Outbound', 'Tcp', [apimPrefix], ['${acaPrivateIp}/32'], ['443']).properties
}
resource acaSqlOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: acaNsg
  name: 'allow-sql-1433-egress'
  properties: allowRule('allow-sql-1433-egress', nsgPriorities.groups.aca.rules['allow-sql-1433-egress'].priority, 'Outbound', 'Tcp', [acaPrefix], map(ips, ip => '${ip}/32'), ['1433']).properties
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
      { name: 'credential-broker-internal', priority: 100, ruleCollectionType: 'FirewallPolicyFilterRuleCollection', action: { type: 'Allow' }, rules: concat(enableCredentialBroker ? [
        { name: 'apim-broker', ruleType: 'NetworkRule', sourceAddresses: [apimPrefix], destinationAddresses: [acaPrivateIp], destinationPorts: ['443'], ipProtocols: ['TCP'] }
      ] : [], [
        { name: 'broker-sql', ruleType: 'NetworkRule', sourceAddresses: [acaPrefix], destinationAddresses: ips, destinationPorts: ['1433'], ipProtocols: ['TCP'] }
        { name: 'broker-vault', ruleType: 'NetworkRule', sourceAddresses: [acaPrefix], destinationAddresses: keyVaultPrivateIps, destinationPorts: ['443'], ipProtocols: ['TCP'] }
      ]) }
      { name: 'credential-broker-azure', priority: 200, ruleCollectionType: 'FirewallPolicyFilterRuleCollection', action: { type: 'Allow' }, rules: [
        { name: 'broker-identity-management', ruleType: 'ApplicationRule', sourceAddresses: [acaPrefix], protocols: [{ protocolType: 'Https', port: 443 }], targetFqdns: ['login.microsoftonline.com', 'management.azure.com'], terminateTLS: false }
        { name: 'broker-image-build', ruleType: 'ApplicationRule', sourceAddresses: [managementPrefix], protocols: [{ protocolType: 'Https', port: 443 }], targetFqdns: ['pypi.org', 'files.pythonhosted.org', 'packages.microsoft.com', 'deb.debian.org', 'security.debian.org'], terminateTLS: false }
      ] }
    ]
  }
}
output endpointId string = endpoint.id

output sqlPrivateIps array = ips
