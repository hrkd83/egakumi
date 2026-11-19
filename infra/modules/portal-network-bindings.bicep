// CAEの実際のドメイン・内部VIPを使い、DNSとAppGW側の経路をネットワークRGで仕上げます。
targetScope = 'resourceGroup'
param stem string
param defaultDomain string
param staticIp string
param vnetId string
param agwRouteTableName string
param firewallIp string
param agwNsgName string
param managementNsgName string
param agwPrefix string
param managementPrivateIp string

module appDns 'portal-dns.bicep' = {
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

resource agwNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: agwNsgName }
resource managementNsg 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = { name: managementNsgName }
resource appgwPortalOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: agwNsg
  name: 'allow-aca-http-egress'
  properties: allowRule('allow-aca-http-egress', nsgPriorities.groups.agw.rules['allow-aca-http-egress'].priority, 'Outbound', 'Tcp', [agwPrefix], ['${staticIp}/32'], ['80']).properties
}
resource managementPortalHttpOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  name: 'allow-aca-http-egress'
  properties: allowRule('allow-aca-http-egress', nsgPriorities.groups.management.rules['allow-aca-http-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], ['${staticIp}/32'], ['80']).properties
}
resource managementPortalHttpsOut 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = {
  parent: managementNsg
  dependsOn: [managementPortalHttpOut]
  name: 'allow-aca-https-egress'
  properties: allowRule('allow-aca-https-egress', nsgPriorities.groups.management.rules['allow-aca-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], ['${staticIp}/32'], ['443']).properties
}
