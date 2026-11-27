// Private Endpoint が作成し終えた NIC を読み、実際に割り当てられた IP を返します。
// 呼出元が PE 応答の実 ID を渡すため、この module の開始前に PE 作成が完了します。
// Azure 資源の新規作成や設定変更は行わず、後続 NSG の宛先一覧を作るだけです。
targetScope = 'resourceGroup'

@description('Private Endpoint の networkInterfaces が返した実 NIC ID 一覧。名前を推測して渡しません。')
@minLength(1)
param privateEndpointNicIds array

// ID 内の Subscription・RG・名前で既存 NIC を特定します。
// NIC が一つ、IP が一つとは決めつけず、返されたすべての組を読み取ります。
resource nics 'Microsoft.Network/networkInterfaces@2024-05-01' existing = [for nicId in privateEndpointNicIds: {
  scope: resourceGroup(split(nicId, '/')[2], split(nicId, '/')[4])
  name: last(split(nicId, '/'))
}]

// 実行時の NIC 参照は output の反復で評価します。NIC ごとの配列を呼出元へ返し、
// 呼出元が平坦化します。複数 NIC・複数 IP を一つに決めつけないための形です。
output privateIpsByNic array = [for (nicId, index) in privateEndpointNicIds: map(nics[index].properties.ipConfigurations, config => config.properties.privateIPAddress)]
