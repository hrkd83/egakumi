// CAEの実際のドメイン・内部VIPを使い、DNSとAppGW側の経路をネットワークRGで仕上げます。
targetScope = 'resourceGroup'
param stem string
param defaultDomain string
param staticIp string
param vnetId string
param agwRouteTableName string
param firewallIp string

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
