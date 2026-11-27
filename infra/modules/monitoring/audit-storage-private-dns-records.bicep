// Zone Groupの完了後だけに実行する読取専用配備です。
// 自動管理されるAレコードへPUTせず、欠落時は後続DNSリンクを止めます。
targetScope = 'resourceGroup'

param privateDnsZoneId string
param auditStorage object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: last(split(privateDnsZoneId, '/'))
}

resource japanRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' existing = {
  parent: zone
  name: auditStorage.collectJapan.name
}

resource foundryRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' existing = {
  parent: zone
  name: auditStorage.collectFoundry.name
}

output recordPrivateIps object = {
  collectJapan: map(japanRecord.properties.aRecords, record => record.ipv4Address)
  collectFoundry: map(foundryRecord.properties.aRecords, record => record.ipv4Address)
}
