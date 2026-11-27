// 構築PEの全IPとZone Groupの実Aレコードが一致した後に常設リンクを作成します。
// 検証値は親が構築出力から算出し、falseでは配備を拒否します。利用者入力へは公開しません。
targetScope = 'resourceGroup'

param resourcePrefix string
param tags object = {}
param vnetId string
param privateDnsZoneId string
@allowed([true])
@description('構築PEの全IPと実Aレコードが空でなく一致することを親が検証した内部値。')
param dnsRecordsValidated bool

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: last(split(privateDnsZoneId, '/'))
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-${resourcePrefix}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    resolutionPolicy: 'NxDomainRedirect'
    virtualNetwork: { id: vnetId }
  }
}

output privateDnsLinkId string = link.id
// 配備履歴に、親が検証したDNSレコードの照合結果を残します。
output recordsValidated bool = dnsRecordsValidated
