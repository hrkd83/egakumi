// 作成済みリソースの実 IP を受け取り、ネットワークの宛先を確定します。
// IP を仮定せず、APIM・VM・Private Endpoint の出力を使って NSG と UDR を設定します。
// 許可規則を先に用意してから最終の拒否規則を適用し、必要な通信まで先に閉じない順序にします。

targetScope = 'resourceGroup'

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
@description('同じ VNet の Firewall が使うポリシー名。既存規則と別の子リソースへ入口許可を追加します。')
param firewallPolicyName string
@description('Firewall の確定済みプライベート IP。UDR の next hop として使います。')
param firewallPrivateIp string
@description('Firewall の公開 IP。管理 VM から公開入口を確認した際の送信元 IP に使います。')
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
@description('Key Vault PE に割り当てられた全 IP。AppGW・APIM・管理 VM の必要な読取経路に使います。')
param keyVaultPrivateIps array
@minLength(1)
@description('管理 VM の確定済みプライベート IP。運用操作と内部接続の通信元・宛先に使います。')
param managementPrivateIp string
@allowed([true])
@description('通常 HTTPS とは別に常設する native gRPC 用 TCP/TLS 経路。無効化できません。')
param grpcEnabled bool = true
@allowed([8443])
@description('利用者が gRPC で接続する固定公開 TCP ポート。8443 だけを使います。')
param grpcPublicPort int = 8443
@allowed([443])
@description('AppGW が内部 APIM の gRPC 入口へ接続する固定 TCP ポート。TLS の 443 だけを使います。')
param grpcBackendPort int = 443

// 単一 IP と IP 配列のどちらにも同じ方針で NSG 規則を組み立てる共通関数です。
func allowRule(ruleName string, priority int, direction string, protocol string, sources array, destinations array, ports array) object => {
  name: ruleName
  properties: union({
    priority: priority
    direction: direction
    access: 'Allow'
    protocol: protocol
    sourcePortRange: '*'
  }, length(sources) == 1 ? {
    sourceAddressPrefix: sources[0]
  } : {
    sourceAddressPrefixes: sources
  }, length(destinations) == 1 ? {
    destinationAddressPrefix: destinations[0]
  } : {
    destinationAddressPrefixes: destinations
  }, length(ports) == 1 ? {
    destinationPortRange: ports[0]
  } : {
    destinationPortRanges: ports
  })
}

// 最後に評価される 4096 番で未許可通信を拒否。Allow の設定前に適用しないよう dependsOn で順序を固定します。
func denyRule(direction string, priority int) object => {
  name: direction == 'Inbound' ? 'deny-other-inbound' : 'deny-other-outbound'
  properties: {
    priority: priority
    direction: direction
    access: 'Deny'
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '*'
  }
}

func withAccess(rule object, access string) object => union(rule, { properties: union(rule.properties, { access: access }) })

var nsgPriorities = loadJsonContent('../nsg-rule-priorities.json')

var nsgKeys = ['agw', 'apim', 'pep', 'management']
var permitApimKeyVault = config.?apimKeyVaultAccessEnabled ?? true
var operatorAddressPrefixes = config.?operatorAddressPrefixes ?? []
var clientAddressPrefixes = config.?clientAddressPrefixes ?? ['0.0.0.0/0']
// 配列の順番は agw / apim / pep / management。宛先は Azure の実出力を使い、PE の複数 IP を全て扱います。
var rulesByNsg = [
  [
    // 内部ロードバランサーの背後で変わる APIM ノードも含め、送受信 NSG の宛先を専用サブネットへ限定します。
    // 接続先 URL、Firewall の許可先、Firewall へ送る UDR は引き続き APIM の VIP を使います。
    allowRule('allow-apim-http', nsgPriorities.groups.agw.rules['allow-apim-http'].priority, 'Outbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.apim], ['80'])
    allowRule('allow-key-vault', nsgPriorities.groups.agw.rules['allow-key-vault'].priority, 'Outbound', 'Tcp', [subnetPrefixes.agw], keyVaultPrivateIps, ['443'])
    withAccess(allowRule('allow-certificate-observer', nsgPriorities.groups.agw.rules['allow-certificate-observer'].priority, 'Inbound', 'Tcp', ['${firewallPublicIpAddress}/32'], [subnetPrefixes.agw], ['443']), config.?certificatePublicProbeEnabled ?? true ? 'Allow' : 'Deny')
    // gRPC は常設し、入力検査を通った1件以上の利用者許可範囲から接続を受けます。
    allowRule('allow-client-grpc', nsgPriorities.groups.agw.rules['allow-client-grpc'].priority, 'Inbound', 'Tcp', clientAddressPrefixes, [subnetPrefixes.agw], [string(grpcPublicPort)])
    allowRule('allow-apim-grpc', nsgPriorities.groups.agw.rules['allow-apim-grpc'].priority, 'Outbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.apim], [string(grpcBackendPort)])
  ]
  [
    allowRule('allow-private-backends', nsgPriorities.groups.apim.rules['allow-private-backends'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], foundryPrivateIps, ['443'])
    withAccess(allowRule('allow-key-vault-pe', nsgPriorities.groups.apim.rules['allow-key-vault-pe'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], keyVaultPrivateIps, ['443']), permitApimKeyVault ? 'Allow' : 'Deny')
    // APIM は内部ロードバランサーの VIP で受けます。NSG の許可範囲は APIM 専用サブネットに揃えます。
    // 宛先は送信側 NSG と同じ APIM 専用サブネットへ限定し、送信元と TCP ポートは広げません。
    // https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview
    withAccess(allowRule('allow-agw-grpc', nsgPriorities.groups.apim.rules['allow-agw-grpc'].priority, 'Inbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.apim], [string(grpcBackendPort)]), grpcEnabled ? 'Allow' : 'Deny')
    withAccess(allowRule('allow-management-grpc', nsgPriorities.groups.apim.rules['allow-management-grpc'].priority, 'Inbound', 'Tcp', [managementPrivateIp], [subnetPrefixes.apim], [string(grpcBackendPort)]), grpcEnabled ? 'Allow' : 'Deny')
  ]
  [
    allowRule('allow-apim-foundry', nsgPriorities.groups.pep.rules['allow-apim-foundry'].priority, 'Inbound', 'Tcp', [subnetPrefixes.apim], foundryPrivateIps, ['443'])
    allowRule('allow-key-vault', nsgPriorities.groups.pep.rules['allow-key-vault'].priority, 'Inbound', 'Tcp', permitApimKeyVault ? [subnetPrefixes.agw, subnetPrefixes.apim] : [subnetPrefixes.agw], keyVaultPrivateIps, ['443'])
    allowRule('allow-vm-https', nsgPriorities.groups.pep.rules['allow-vm-https'].priority, 'Inbound', 'Tcp', [managementPrivateIp], union(foundryPrivateIps, keyVaultPrivateIps), ['443'])
  ]
  concat([
    allowRule('allow-private-services', nsgPriorities.groups.management.rules['allow-private-services'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], union(foundryPrivateIps, keyVaultPrivateIps), ['443'])
    withAccess(allowRule('allow-apim-grpc-comparison', nsgPriorities.groups.management.rules['allow-apim-grpc-comparison'].priority, 'Outbound', 'Tcp', [managementPrivateIp], [subnetPrefixes.apim], [string(grpcBackendPort)]), grpcEnabled ? 'Allow' : 'Deny')
  ], empty(operatorAddressPrefixes) ? [] : [
    allowRule('allow-operator-management', nsgPriorities.groups.management.rules['allow-operator-management'].priority, 'Inbound', config.?operatorProtocol ?? 'Tcp', operatorAddressPrefixes, [managementPrivateIp], config.?operatorPorts ?? ['22'])
  ])

]

// 前段で作成済みの NSG を参照するだけで、本体を作り直しません。
resource nsgs 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = [for key in nsgKeys: {
  name: nsgNames[key]
}]

var dynamicRules = flatten(map(range(0, length(nsgKeys)), i => map(rulesByNsg[i], rule => { nsgIndex: i, rule: rule })))

// APIM・PE・VM の実 IP で通信を許可。管理接続元なしなら管理者規則を生成せず、設定を外した場合は構築前処理で現行Allow・220を削除します。
// その他のオプションを無効にした場合は同名規則を Deny へ更新して旧許可を回収します。
@batchSize(1)
resource dynamicSecurityRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for entry in dynamicRules: {
  parent: nsgs[entry.nsgIndex]
  name: entry.rule.name
  properties: entry.rule.properties
}]

var terminalRules = [
  { nsgIndex: 0, rule: denyRule('Inbound', nsgPriorities.groups.agw.rules['deny-other-inbound'].priority) }
  { nsgIndex: 1, rule: denyRule('Inbound', nsgPriorities.groups.apim.rules['deny-other-inbound'].priority) }
  { nsgIndex: 2, rule: denyRule('Inbound', nsgPriorities.groups.pep.rules['deny-other-inbound'].priority) }
  { nsgIndex: 3, rule: denyRule('Inbound', nsgPriorities.groups.management.rules['deny-other-inbound'].priority) }
  { nsgIndex: 1, rule: denyRule('Outbound', nsgPriorities.groups.apim.rules['deny-other-outbound'].priority) }
  { nsgIndex: 3, rule: denyRule('Outbound', nsgPriorities.groups.management.rules['deny-other-outbound'].priority) }
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




// 同じ Firewall Policy を参照し、先行モジュールとは別の規則集へ設定を追加します。
resource firewallPolicy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = {
  name: firewallPolicyName
}

// network.bicep completes the platform/egress groups and Firewall before this
// module runs. This separate write cannot overlap those policy mutations.
// AppGW→APIMの必要ポートだけを許可します。
resource ingressRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewallPolicy
  name: 'rcg-ingress'
  properties: {
    priority: 200
    ruleCollections: [{
      name: 'allow-agw-to-apim'
      priority: 200
      ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
      action: { type: 'Allow' }
      rules: concat([{
        name: 'agw-to-apim-http'
        ruleType: 'NetworkRule'
        sourceAddresses: [subnetPrefixes.agw]
        destinationAddresses: [apimPrivateIp]
        destinationPorts: grpcEnabled ? ['80', string(grpcBackendPort)] : ['80']
        ipProtocols: ['TCP']
      }])
    }]
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

output apimGatewayPrivateDnsZoneId string = apimGatewayZone.id
output apimGatewayDnsRecord object = { fqdn: apimGatewayFqdn, privateIps: [apimPrivateIp] }
output ingressRuleCollectionGroupId string = ingressRules.id
output boundPrivateIps object = { foundry: foundryPrivateIps, keyVault: keyVaultPrivateIps, management: [managementPrivateIp], apim: [apimPrivateIp] }
