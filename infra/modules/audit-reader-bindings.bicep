// 監査ログの閲覧者へ、指定された Blob コンテナーだけの参照権限を割り当てます。
// Storage アカウントやログ本体は作成せず、既存の保存先を参照します。

targetScope = 'resourceGroup'
@description('既存の監査ログ保存先 Storage アカウント名。')
param storageAccountName string
@description('閲覧対象コンテナーと閲覧者 principalId の一覧。書込み・削除権限は付けません。')
param readers array
// 対象 Storage 本体を参照。新しい保存先は作成しません。
resource account 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }
// 対象 Storage の Blob サービスを参照します。
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = { parent: account, name: 'default' }
// 閲覧対象に指定されたコンテナーだけを参照します。
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for reader in readers: { parent: blobs, name: reader.containerName }]
// Storage Blob Data Reader をコンテナーのスコープで付与します。
resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (reader, i) in readers: {
  scope: containers[i]
  name: guid(containers[i].id, reader.principalId, 'Storage Blob Data Reader')
  properties: {
    principalId: reader.principalId
    principalType: reader.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}]
