// 監査StorageのBlob接続用PEを常設します。Storage本体のACL・監査・保持設定は変更しません。
// 全NICの割当IPを後続NSGへ渡し、DNSのVNetリンクはNSGとAレコードの準備後に別モジュールで有効化します。
targetScope = 'resourceGroup'

param resourcePrefix string
param location string
param tags object = {}
@description('構築したPrivate EndpointサブネットのID。')
param pepSubnetId string
@description('構築した監査Storageの出力。collectPlatform/collectFoundryの実IDを使います。')
param auditStorage object

var archive = loadJsonContent('audit-archive.json')
var collectors = [
  { key: 'collectPlatform', suffix: archive.collectors.collectPlatform.endpointSuffix }
  { key: 'collectFoundry', suffix: archive.collectors.collectFoundry.endpointSuffix }
]

// 同じサブネットを参照するPEの作成は順に行います。
@batchSize(1)
resource endpoints 'Microsoft.Network/privateEndpoints@2025-01-01' = [for collector in collectors: {
  name: 'pe-${resourcePrefix}-${collector.suffix}'
  location: location
  tags: tags
  properties: {
    subnet: { id: pepSubnetId }
    privateLinkServiceConnections: [{
      name: 'blob'
      properties: {
        privateLinkServiceId: auditStorage[collector.key].id
        groupIds: ['blob']
      }
    }]
  }
}]

// PEが返したNICを全件読み、複数のNIC/IPを切り捨てません。
@batchSize(1)
module addresses '../network/private-endpoint-addresses.bicep' = [for (collector, index) in collectors: {
  name: '${resourcePrefix}-${collector.suffix}-addresses'
  params: { privateEndpointNicIds: map(endpoints[index].properties.networkInterfaces, nic => nic.id) }
}]

output privateEndpoints object = {
  collectPlatform: {
    id: endpoints[0].id
    storageAccountId: auditStorage.collectPlatform.id
    networkInterfaceIds: map(endpoints[0].properties.networkInterfaces, nic => nic.id)
    privateIps: union([], flatten(addresses[0].outputs.privateIpsByNic))
  }
  collectFoundry: {
    id: endpoints[1].id
    storageAccountId: auditStorage.collectFoundry.id
    networkInterfaceIds: map(endpoints[1].properties.networkInterfaces, nic => nic.id)
    privateIps: union([], flatten(addresses[1].outputs.privateIpsByNic))
  }
}
