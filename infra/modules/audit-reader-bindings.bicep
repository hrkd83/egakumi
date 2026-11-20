// 監査ログの閲覧者へ、指定された Blob コンテナーだけの参照権限を割り当てます。
// Storage アカウントやログ本体は作成せず、既存の保存先を参照します。

targetScope = 'resourceGroup'
@description('既存の監査ログ保存先 Storage アカウント名。')
param storageAccountName string
@description('閲覧対象コンテナーと閲覧者 principalId の一覧。書込み・削除権限は付けません。')
param readers array
// 利用者の閲覧者に管理VM自身のMIを合成したときも、同じコンテナー・principalへ
// 同じ割当を重複作成しません。GUIDは従来と同じ式で、既存の割当を保持します。
// 呼出側は既存の閲覧者を先に置きます。同じGUIDの表記の大文字・小文字も
// 既存側を保ち、従来のguid入力が変わって別の割当名になることを避けます。
var reversedReaders = map(range(0, length(readers)), index => readers[length(readers) - index - 1])
var uniqueReaders = map(items(toObject(reversedReaders, reader => '${toLower(reader.containerName)}|${toLower(reader.principalId)}', reader => reader)), entry => entry.value)
// 対象 Storage 本体を参照。新しい保存先は作成しません。
resource account 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }
// 対象 Storage の Blob サービスを参照します。
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = { parent: account, name: 'default' }
// 閲覧対象に指定されたコンテナーだけを参照します。
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for reader in uniqueReaders: { parent: blobs, name: reader.containerName }]
// Storage Blob Data Reader をコンテナーのスコープで付与します。
resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (reader, i) in uniqueReaders: {
  scope: containers[i]
  name: guid(containers[i].id, reader.principalId, 'Storage Blob Data Reader')
  properties: {
    principalId: reader.principalId
    principalType: reader.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}]
