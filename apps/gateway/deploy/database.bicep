// 共通SQLサーバーにGateway専用の接続台帳DBを配備します。テーブルは初期化ジョブで更新します。
targetScope = 'resourceGroup'
param stem string
param location string
param tags object
param sqlServer string
param settings object
resource server 'Microsoft.Sql/servers@2023-08-01' existing = { name: first(split(sqlServer, '.')) }
var databaseSku = union({ name: settings.sqlSkuName, tier: settings.sqlTier, capacity: settings.sqlCapacity }, settings.sqlTier == 'Basic' ? {} : { family: 'Gen5' })
resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: server
  name: 'sqldb-${stem}-credentials'
  location: location
  tags: tags
  sku: databaseSku
  properties: {
    collation: 'Latin1_General_100_BIN2'
    maxSizeBytes: settings.sqlMaxSizeGB * 1073741824
    zoneRedundant: settings.zoneRedundant
    requestedBackupStorageRedundancy: settings.backupRedundancy
    readScale: 'Disabled'
  }
}
resource retention 'Microsoft.Sql/servers/databases/backupShortTermRetentionPolicies@2023-08-01' = {
  parent: database
  name: 'default'
  properties: { retentionDays: settings.backupRetentionDays }
}
output database string = database.name
output databaseId string = database.id
