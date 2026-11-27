// 作成済みリソースの実 IP を受け取り、ネットワークの宛先を確定します。
// IP を仮定せず、APIM・VM・Private Endpoint の出力を使って NSG と UDR を設定します。
// 許可規則を先に用意してから最終の拒否規則を適用し、必要な通信まで先に閉じない順序にします。

targetScope = 'resourceGroup'

import { allowRule, denyProperties } from './nsg/rule-properties.bicep'

@description('ネットワーク資源に付ける共通名。製品名と環境名から作ります。')
param resourcePrefix string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}
@description('親から受け取る検証済みの設定値。このモジュールが担当する項目だけを参照します。')
param config object = {}
@description('Private DNS の解決を有効にする VNet のリソース ID。')
param vnetId string
@description('用途別サブネットの CIDR。NSG と Firewall で送信元の範囲として参照します。')
param subnetPrefixes object
@description('network.bicep で作成した用途別 NSG の名前。追加規則だけを後から設定します。')
param nsgNames object
@description('network.bicep で作成したルートテーブル名。APIM 実 IP 向け UDR の追加先です。')
param routeTableNames object
@description('Firewall の確定済みプライベート IP。UDR の next hop として使います。')
param firewallPrivateIp string
@description('同じ VNet の Firewall の公開 IP。AppGW の公開入口 443 へ入る通信の送信元 IP に使います。')
param firewallPublicIpAddress string
@minLength(1)
@description('内部 APIM の作成後に割り当てられたプライベート IP。UDR・許可規則の宛先に使います。')
param apimPrivateIp string
@minLength(1)
@description('APIM 自身の完全なゲートウェイ名。azure-api.net 全体を独自 DNS で上書きしないでください。')
param apimGatewayFqdn string
@minLength(1)
@description('Foundry PE に割り当てられた全 IP。OpenAI / Cognitive / Services の通信先を漏れなく許可します。')
param foundryPrivateIps array
@minLength(1)
@description('共用Key Vault PEの全IP。APIM、ACA、管理VMの接続を許可します。')
param keyVaultPrivateIps array
@minLength(1)
@description('collectJapanの監査Blob PEに割り当てられた全NIC実IP。')
param auditStorageJapanPrivateIps array
@minLength(1)
@description('collectFoundryの監査Blob PEに割り当てられた全NIC実IP。')
param auditStorageFoundryPrivateIps array
@minLength(1)
@description('管理 VM の確定済みプライベート IP。運用操作と内部接続の通信元・宛先に使います。')
param managementPrivateIp string
@description('構築したAppGWの公開IP。ACAから通常HTTPSの入口へ戻る送信先。')
param appGatewayPublicIpAddress string

var coreVaultAddresses = map(keyVaultPrivateIps, ip => '${ip}/32')

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。
// このモジュールの末尾Denyは明示Allowの後に登録します。ACAの末尾DenyはSQL経路の準備後に配置します。
func denyRule(direction string, priority int) object => {
  name: direction == 'Inbound' ? 'deny-any-ingress' : 'deny-any-egress'
  properties: denyProperties(direction, priority)
}

var nsgPriorities = loadJsonContent('nsg/core-rules.json')
var nsgKeys = ['agw', 'apim', 'pep', 'management', 'aca', 'broker']

// Dynamic addresses always refer to resources built in this VNet. No presence flags are used.
var rulesByNsg = [
  [
    allowRule('allow-firewall-public-https-ingress', nsgPriorities.groups.agw.rules['allow-firewall-public-https-ingress'].priority, 'Inbound', 'Tcp', ['${firewallPublicIpAddress}/32'], [subnetPrefixes.agw], ['443'])
    allowRule('allow-apim-https-egress', nsgPriorities.groups.agw.rules['allow-apim-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.agw], ['${apimPrivateIp}/32'], ['443'])
    allowRule('allow-key-vault-https-egress', nsgPriorities.groups.agw.rules['allow-key-vault-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.agw], coreVaultAddresses, ['443'])
  ]
  [
    allowRule('allow-mgmt-vm-https-ingress', nsgPriorities.groups.apim.rules['allow-mgmt-vm-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], [subnetPrefixes.apim], ['443'])
    allowRule('allow-foundry-https-egress', nsgPriorities.groups.apim.rules['allow-foundry-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], map(foundryPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-key-vault-https-egress', nsgPriorities.groups.apim.rules['allow-key-vault-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], coreVaultAddresses, ['443'])
  ]
  [
    allowRule('allow-apim-foundry-https-ingress', nsgPriorities.groups.pep.rules['allow-apim-foundry-https-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.apim], map(foundryPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-appgw-key-vault-https-ingress', nsgPriorities.groups.pep.rules['allow-appgw-key-vault-https-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.agw], map(keyVaultPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-apim-key-vault-https-ingress', nsgPriorities.groups.pep.rules['allow-apim-key-vault-https-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.apim], coreVaultAddresses, ['443'])
    allowRule('allow-aca-key-vault-https-ingress', nsgPriorities.groups.pep.rules['allow-aca-key-vault-https-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.aca, subnetPrefixes.broker], coreVaultAddresses, ['443'])
    allowRule('allow-mgmt-vm-key-vault-https-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-key-vault-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], coreVaultAddresses, ['443'])
    allowRule('allow-mgmt-vm-foundry-https-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-foundry-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(foundryPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-mgmt-vm-audit-storage-platform-https-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-audit-storage-platform-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(auditStorageJapanPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-mgmt-vm-audit-storage-foundry-https-ingress', nsgPriorities.groups.pep.rules['allow-mgmt-vm-audit-storage-foundry-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], map(auditStorageFoundryPrivateIps, ip => '${ip}/32'), ['443'])
  ]
  [
    allowRule('allow-key-vault-https-egress', nsgPriorities.groups.management.rules['allow-key-vault-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], coreVaultAddresses, ['443'])
    allowRule('allow-apim-https-egress', nsgPriorities.groups.management.rules['allow-apim-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], [subnetPrefixes.apim], ['443'])
    allowRule('allow-foundry-https-egress', nsgPriorities.groups.management.rules['allow-foundry-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], map(foundryPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-audit-storage-platform-https-egress', nsgPriorities.groups.management.rules['allow-audit-storage-platform-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], map(auditStorageJapanPrivateIps, ip => '${ip}/32'), ['443'])
    allowRule('allow-audit-storage-foundry-https-egress', nsgPriorities.groups.management.rules['allow-audit-storage-foundry-https-egress'].priority, 'Outbound', 'Tcp', ['${managementPrivateIp}/32'], map(auditStorageFoundryPrivateIps, ip => '${ip}/32'), ['443'])
  ]
  [
    allowRule('allow-mgmt-vm-https-ingress', nsgPriorities.groups.aca.rules['allow-mgmt-vm-https-ingress'].priority, 'Inbound', 'Tcp', ['${managementPrivateIp}/32'], [subnetPrefixes.aca], ['443', '31443'])
    allowRule('allow-appgw-https-egress', nsgPriorities.groups.aca.rules['allow-appgw-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['${appGatewayPublicIpAddress}/32'], ['443'])
    allowRule('allow-key-vault-https-egress', nsgPriorities.groups.aca.rules['allow-key-vault-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], coreVaultAddresses, ['443'])
  ]
  [
    allowRule('allow-key-vault-https-egress', nsgPriorities.groups.broker.rules['allow-key-vault-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.broker], coreVaultAddresses, ['443'])
  ]
]

// 前段で作成済みの NSG を参照するだけで、本体を作り直しません。
resource nsgs 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = [for key in nsgKeys: {
  name: nsgNames[key]
}]

var dynamicRules = flatten(map(range(0, length(nsgKeys)), i => map(rulesByNsg[i], rule => { nsgIndex: i, rule: rule })))

// 各資材の全PE IPを/32で登録し、管理VMの保守送信元も割当IP/32へ限定します。
@batchSize(1)
resource dynamicSecurityRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for entry in dynamicRules: {
  parent: nsgs[entry.nsgIndex]
  name: entry.rule.name
  properties: entry.rule.properties
}]

var terminalRules = [
  { nsgIndex: 0, rule: denyRule('Inbound', nsgPriorities.groups.agw.rules['deny-any-ingress'].priority) }
  { nsgIndex: 1, rule: denyRule('Inbound', nsgPriorities.groups.apim.rules['deny-any-ingress'].priority) }
  { nsgIndex: 2, rule: denyRule('Inbound', nsgPriorities.groups.pep.rules['deny-any-ingress'].priority) }
  { nsgIndex: 3, rule: denyRule('Inbound', nsgPriorities.groups.management.rules['deny-any-ingress'].priority) }
  { nsgIndex: 1, rule: denyRule('Outbound', nsgPriorities.groups.apim.rules['deny-any-egress'].priority) }
  { nsgIndex: 3, rule: denyRule('Outbound', nsgPriorities.groups.management.rules['deny-any-egress'].priority) }
]

// 必要な Allow が完成してから、その他通信の拒否を追加します。AppGW の必須 Internet 出口は閉じません。
@batchSize(1)
resource terminalSecurityRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for entry in terminalRules: {
  parent: nsgs[entry.nsgIndex]
  name: entry.rule.name
  dependsOn: [dynamicSecurityRules]
  properties: entry.rule.properties
}]

// AppGW サブネットに関連付け済みの UDR を参照します。
resource agwRouteTable 'Microsoft.Network/routeTables@2025-01-01' existing = {
  name: routeTableNames.agw
}

// APIM の VIP /32 だけ Firewall を経由させます。AppGW のデフォルトルート全体は変更しません。
resource agwToApim 'Microsoft.Network/routeTables/routes@2025-01-01' = {
  parent: agwRouteTable
  name: 'apim-via-firewall'
  properties: {
    addressPrefix: '${apimPrivateIp}/32'
    nextHopType: 'VirtualAppliance'
    nextHopIpAddress: firewallPrivateIp
  }
}




// APIM のホスト名１個分の DNS Zone。他の APIM 名を隠す azure-api.net 全体の Zone は作りません。
resource apimGatewayZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: apimGatewayFqdn
  location: 'global'
  tags: tags
}

// 今回の VNet 内から APIM の内部 VIP を引けるようにします。
resource apimGatewayLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: apimGatewayZone
  name: 'link-${resourcePrefix}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    // この Zone は APIM の通常 FQDN 用です。resolutionPolicy は Private Link
    // Zone 専用のため指定せず、既定の名前解決を使います。
    virtualNetwork: { id: vnetId }
  }
}

// APIM 名を実際の内部 VIP へ対応付ける A レコードです。
resource apimGatewayRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: apimGatewayZone
  name: '@'
  properties: {
    ttl: config.?dnsTtl ?? 300
    aRecords: [{ ipv4Address: apimPrivateIp }]
  }
}
