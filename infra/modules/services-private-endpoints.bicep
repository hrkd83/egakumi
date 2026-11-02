// Foundry と Key Vault に、VNet 内のプライベート IP を持つ入口を作成します。
// DNS の名前を Private Endpoint の IP に解決できるよう、サービス別の Private DNS Zone を関連付けます。
// 実際に割り当てられた全 IP を出力し、後続の NSG が一部の IP だけ許可する抜けを防ぎます。

targetScope = 'resourceGroup'

@description('ネットワーク資源に付ける共通名。製品名と環境名から作ります。')
param resourcePrefix string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object
@description('Foundry と Key Vault の Private Endpoint 専用サブネット ID。')
param privateEndpointSubnetId string
@description('サービスごとの Private DNS Zone ID。PE の DNS レコード登録先に使います。')
param privateDnsZoneIds object
@description('接続する Foundry・Key Vault 等の完全なリソース ID。名前から推測せず出力を受け取ります。')
param serviceResourceIds object

// Foundry のプライベート入口を作成。複数 API 群の IP を１個と決めつけないでください。
resource foundryPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${resourcePrefix}-foundry'
  location: location
  tags: tags
  properties: {
    customNetworkInterfaceName: 'pe-${resourcePrefix}-foundry-nic'
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [{
      name: 'foundry'
      properties: { privateLinkServiceId: serviceResourceIds.foundry, groupIds: ['account'] }
    }]
  }
}
// Foundry の各 API 群用 Private DNS Zone に、PE のレコードを関連付けます。
resource foundryPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: foundryPe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cognitive', properties: { privateDnsZoneId: privateDnsZoneIds.foundryCognitive } }
      { name: 'openai', properties: { privateDnsZoneId: privateDnsZoneIds.foundryOpenAi } }
      { name: 'services', properties: { privateDnsZoneId: privateDnsZoneIds.foundryServices } }
    ]
  }
}
// 証明書・secret の読書きに使う Key Vault のプライベート入口。
resource vaultPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${resourcePrefix}-kv'
  location: location
  tags: tags
  properties: {
    customNetworkInterfaceName: 'pe-${resourcePrefix}-kv-nic'
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [{
      name: 'vault'
      properties: { privateLinkServiceId: serviceResourceIds.keyVault, groupIds: ['vault'] }
    }]
  }
}
// Key Vault の標準 Private Link DNS Zone と関連付けます。
resource vaultPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: vaultPe
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'vault', properties: { privateDnsZoneId: privateDnsZoneIds.keyVault } }] }
}

// PE が返す実 NIC の ID を使います。NIC 名や IP を subnet 名等から推測しません。
// NIC は PE 作成中に Azure が生成するため、後段 module の入力として渡します。
// 同じ deployment の静的 existing NIC 参照は、PE 完了前に評価され得るため使いません。
var foundryNicIds = map(foundryPe.properties.networkInterfaces, nic => nic.id)
var vaultNicIds = map(vaultPe.properties.networkInterfaces, nic => nic.id)
// 実行時に判明する ID を module 引数にすることで、PE 完了後に NIC を読みます。
// module は既存 NIC の参照だけを行い、新しい NIC や Private Endpoint は作りません。
module foundryAddresses './private-endpoint-addresses.bicep' = {
  name: 'foundry-private-endpoint-addresses'
  params: { privateEndpointNicIds: foundryNicIds }
}
module vaultAddresses './private-endpoint-addresses.bicep' = {
  name: 'vault-private-endpoint-addresses'
  params: { privateEndpointNicIds: vaultNicIds }
}

output privateEndpointIds object = { foundry: foundryPe.id, keyVault: vaultPe.id }
output privateEndpointNicIds object = { foundry: foundryNicIds, keyVault: vaultNicIds }
// 複数 NIC の IP 配列を平坦化し、重複を除いて後続 NSG にすべて渡します。
output foundryPrivateIps array = union([], flatten(foundryAddresses.outputs.privateIpsByNic))
output keyVaultPrivateIps array = union([], flatten(vaultAddresses.outputs.privateIpsByNic))
