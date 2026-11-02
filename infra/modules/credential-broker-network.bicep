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
param keyVaultPrivateIps array
@description('CM未使用時はAPIMから認証窓口への経路を追加しません。')
param enableCredentialBroker bool = true
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
resource sqlIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-credential-broker-sql'
  properties: { priority: 175, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: acaPrefix, destinationAddressPrefixes: ips, sourcePortRange: '*', destinationPortRange: '1433' }
}
// 認証窓口のIDが、Private Endpoint経由でKey Vaultの秘密参照を解決します。
resource vaultIn 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: pepNsg
  name: 'allow-credential-broker-vault'
  properties: { priority: 177, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: acaPrefix, destinationAddressPrefixes: keyVaultPrivateIps, sourcePortRange: '*', destinationPortRange: '443' }
}
resource apimNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: apimNsgName }
resource brokerOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = if (enableCredentialBroker) {
  parent: apimNsg
  name: 'allow-credential-broker'
  properties: { priority: 176, direction: 'Outbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: apimPrefix, destinationAddressPrefix: acaPrivateIp, sourcePortRange: '*', destinationPortRange: '443' }
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
