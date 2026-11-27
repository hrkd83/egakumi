// 対向NSGの完成後にBlobのPrivate DNS Zone Groupを登録します。
// 自動管理されるAレコードは既存参照として実際に読み、VNetリンクの前段へ返します。
targetScope = 'resourceGroup'

param tags object = {}
param auditStorage object
param privateEndpoints object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource japanZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  name: '${last(split(privateEndpoints.collectJapan.id, '/'))}/default'
  properties: {
    privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: zone.id } }]
  }
}

resource foundryZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-01-01' = {
  name: '${last(split(privateEndpoints.collectFoundry.id, '/'))}/default'
  properties: {
    privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: zone.id } }]
  }
  // 同一ZoneのAレコード登録は直列で完了させます。
  dependsOn: [japanZoneGroup]
}

// existing A参照自体はZone Groupへの依存を作らないため、読取専用の
// 子配備を両Groupの完了後に開始します。自動管理AへのPUTは行いません。
module records 'audit-storage-private-dns-records.bicep' = {
  name: '${last(split(privateEndpoints.collectJapan.id, '/'))}-dns-records'
  params: {
    privateDnsZoneId: zone.id
    auditStorage: auditStorage
  }
  dependsOn: [japanZoneGroup, foundryZoneGroup]
}

output privateDnsZoneId string = zone.id
output privateDnsZoneGroupIds array = [japanZoneGroup.id, foundryZoneGroup.id]
output recordPrivateIps object = records.outputs.recordPrivateIps
