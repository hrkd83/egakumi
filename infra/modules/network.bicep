// 独立した VNet と、作成前から宛先が分かる通信規則を用意します。
// NSG は通信の許可・拒否、UDR は次に送る機器、Firewall は中継時の検査を担当します。
// APIM や Private Endpoint の IP は作成後に決まるため、該当規則は network-bindings.bicep で仕上げます。
// 通常の API は AppGW → Firewall → APIM、APIM → Foundry は Private Endpoint へ送ります。

targetScope = 'resourceGroup'

@minLength(3)
@maxLength(40)
@description('ネットワーク資源に付ける共通名。製品名と環境名から作ります。')
param resourcePrefix string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}

@description('親から受け取る検証済みの設定値。このモジュールが担当する項目だけを参照します。')
param config object = {}
@description('検証済みの業務APIと本人情報取得設定だけから生成したAPIM宛先。公開FW追加入力ではありません。')
param generatedApimServiceFqdns array = []
@minLength(1)
@description('Gateway構築と共用する公開ホスト名。証明書取得前から初期経路を用意します。')
param gatewayPublicHostName string
@minLength(1)
@description('Portal構築と共用する公開ホスト名。独立したFW宛先選択ではありません。')
param portalPublicHostName string
@allowed(['production', 'staging'])
@description('証明書業務設定と共用するACME directory。固定の公式CA宛先を選択します。')
param certificateDirectory string = 'production'
@description('azd の構築前処理が実在を確認した、この基盤の NSG 名。公開 config では指定しません。')
param existingNetworkSecurityGroupNames array = []
@description('azd の読取結果から渡す実ルート表名と既存経路の対応。初回は空、再 up は後続段階で作った経路も保持します。')
param existingRouteTableRoutes object = {}

// Design authority: infrastructure/{virtual-network,network-security-group,
// route-table,private-dns-zone,azure-firewall,firewall-policy}. The core owns
// six subnets, five NSGs and three base route tables. The required
// core sample portal adds a delegated ACA subnet and its own route table.
var nsgPriorities = loadJsonContent('../nsg-rule-priorities.json')

var addressSpaces = config.?addressSpaces ?? ['10.40.0.0/24', '10.40.1.0/24', '10.40.2.0/24', '10.40.3.0/24']
var subnetPrefixes = config.?subnetPrefixes ?? {
  agw: '10.40.0.0/24'
  pep: '10.40.1.0/24'
  firewall: '10.40.2.0/26'
  apim: '10.40.2.64/26'
  management: '10.40.2.128/26'
  aca: '10.40.2.192/26'
}
// Initial VNet and Firewall upstream DNS use Azure-provided resolution.
var dnsServers = []
var firewallSettings = config.?firewall ?? {}
var firewallTier = firewallSettings.?sku ?? 'Premium'
var zones = firewallSettings.?zones ?? []
// 初期の非SNAT範囲とIDPSの内部分類は、標準範囲と構築VNetから生成します。
// SNAT用の共通タグはIDPSに使えないため、IDPSにはRFC1918のCIDRを明示します。
var snatPrivateRanges = union(['IANAPrivateRanges'], addressSpaces)
var idpsPrivateRanges = union(['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'], addressSpaces)
// Azureの環境定義からStorage suffixとEntraの標準ログイン先を使います。
// 認証先を利用者の要求ヘッダーや動的な外部URLから選ぶことはありません。

// NSGには固定の初期集合を登録し、構築資材のCIDR・割当IPだけを宛先に差し込みます。
func allowRule(ruleName string, priority int, direction string, protocol string, sources array, destinations array, ports array) object => {
  name: ruleName
  properties: union({
    description: 'Initial fixed communication: ${ruleName}'
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

// AppGWとACAはAzure既定送信を保持。ACAは受信の末尾Any Denyも作成しません。
// AzureFirewallSubnetに顧客NSGを作成・関連付けしません。
var nsgDefinitions = [
  {
    key: 'agw'
    suffix: 'agw'
    rules: [
      allowRule('allow-client-http-ingress', nsgPriorities.groups.agw.rules['allow-client-http-ingress'].priority, 'Inbound', 'Tcp', ['0.0.0.0/0'], [subnetPrefixes.agw], ['80'])
      allowRule('allow-client-https-ingress', nsgPriorities.groups.agw.rules['allow-client-https-ingress'].priority, 'Inbound', 'Tcp', ['0.0.0.0/0'], [subnetPrefixes.agw], ['443'])
      allowRule('allow-gateway-manager-ingress', nsgPriorities.groups.agw.rules['allow-gateway-manager-ingress'].priority, 'Inbound', 'Tcp', ['GatewayManager'], ['*'], ['65200-65535'])
      allowRule('allow-azure-load-balancer-ingress', nsgPriorities.groups.agw.rules['allow-azure-load-balancer-ingress'].priority, 'Inbound', '*', ['AzureLoadBalancer'], ['*'], ['*'])
    ]
  }
  {
    key: 'apim'
    suffix: 'apim'
    rules: [
      allowRule('allow-appgw-http-ingress', nsgPriorities.groups.apim.rules['allow-appgw-http-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.apim], ['80'])
      allowRule('allow-api-management-ingress', nsgPriorities.groups.apim.rules['allow-api-management-ingress'].priority, 'Inbound', 'Tcp', ['ApiManagement'], [subnetPrefixes.apim], ['3443'])
      allowRule('allow-apim-ratelimits-counter-sync-ingress', nsgPriorities.groups.apim.rules['allow-apim-ratelimits-counter-sync-ingress'].priority, 'Inbound', 'Udp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['4290'])
      allowRule('allow-apim-internal-cache-ingress', nsgPriorities.groups.apim.rules['allow-apim-internal-cache-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['6381-6383'])
      allowRule('allow-azure-load-balancer-ingress', nsgPriorities.groups.apim.rules['allow-azure-load-balancer-ingress'].priority, 'Inbound', '*', ['AzureLoadBalancer'], ['*'], ['*'])
      allowRule('allow-storage-egress', nsgPriorities.groups.apim.rules['allow-storage-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Storage'], ['443'])
      allowRule('allow-sql-egress', nsgPriorities.groups.apim.rules['allow-sql-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Sql'], ['1433'])
      allowRule('allow-key-vault-egress', nsgPriorities.groups.apim.rules['allow-key-vault-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureKeyVault'], ['443'])
      allowRule('allow-monitor-egress', nsgPriorities.groups.apim.rules['allow-monitor-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureMonitor'], ['1886', '443'])
      allowRule('allow-event-hubs-egress', nsgPriorities.groups.apim.rules['allow-event-hubs-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['EventHub'], ['443', '5671', '5672'])
      allowRule('allow-entra-egress', nsgPriorities.groups.apim.rules['allow-entra-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureActiveDirectory'], ['443'])
      allowRule('allow-credential-manager-egress', nsgPriorities.groups.apim.rules['allow-credential-manager-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureConnectors'], ['443'])
      allowRule('allow-internet-egress', nsgPriorities.groups.apim.rules['allow-internet-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Internet'], ['80', '443'])
      allowRule('allow-time-sync-egress', nsgPriorities.groups.apim.rules['allow-time-sync-egress'].priority, 'Outbound', 'Udp', [subnetPrefixes.apim], ['Internet'], ['123'])
      allowRule('allow-apim-ratelimits-counter-sync-egress', nsgPriorities.groups.apim.rules['allow-apim-ratelimits-counter-sync-egress'].priority, 'Outbound', 'Udp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['4290'])
      allowRule('allow-apim-internal-cache-egress', nsgPriorities.groups.apim.rules['allow-apim-internal-cache-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['6381-6383'])
    ]
  }
  {
    key: 'pep'
    suffix: 'pep'
    rules: [
    ]
  }
  {
    key: 'management'
    suffix: 'vm'
    rules: [
      allowRule('allow-azure-resource-manager-egress', nsgPriorities.groups.management.rules['allow-azure-resource-manager-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureResourceManager'], ['443'])
      allowRule('allow-entra-egress', nsgPriorities.groups.management.rules['allow-entra-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureActiveDirectory'], ['443'])
      allowRule('allow-monitor-egress', nsgPriorities.groups.management.rules['allow-monitor-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureMonitor'], ['443'])
      allowRule('allow-storage-egress', nsgPriorities.groups.management.rules['allow-storage-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], ['Storage'], ['443'])
      allowRule('allow-internet-egress', nsgPriorities.groups.management.rules['allow-internet-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.management], ['Internet'], ['80', '443'])
      // Host PHC time reaches the guest over VMBus; no UDP123 or network PTP allow is required.
    ]
  }
  {
    key: 'aca'
    suffix: 'aca'
    rules: [
      allowRule('allow-appgw-http-ingress', nsgPriorities.groups.aca.rules['allow-appgw-http-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.aca], ['80', '31080'])
      allowRule('allow-apim-https-ingress', nsgPriorities.groups.aca.rules['allow-apim-https-ingress'].priority, 'Inbound', 'Tcp', [subnetPrefixes.apim], [subnetPrefixes.aca], ['443', '31443'])
      allowRule('allow-azure-load-balancer-ingress', nsgPriorities.groups.aca.rules['allow-azure-load-balancer-ingress'].priority, 'Inbound', 'Tcp', ['AzureLoadBalancer'], [subnetPrefixes.aca], ['30000-32767'])
      allowRule('allow-arm-https-egress', nsgPriorities.groups.aca.rules['allow-arm-https-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['AzureResourceManager'], ['443'])
      allowRule('allow-aca-internal-egress', nsgPriorities.groups.aca.rules['allow-aca-internal-egress'].priority, 'Outbound', '*', [subnetPrefixes.aca], [subnetPrefixes.aca], ['*'])
      allowRule('allow-entra-egress', nsgPriorities.groups.aca.rules['allow-entra-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['AzureActiveDirectory'], ['443'])
      allowRule('allow-monitor-egress', nsgPriorities.groups.aca.rules['allow-monitor-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['AzureMonitor'], ['443'])
      allowRule('allow-mcr-egress', nsgPriorities.groups.aca.rules['allow-mcr-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['MicrosoftContainerRegistry'], ['443'])
      allowRule('allow-afd-first-party-egress', nsgPriorities.groups.aca.rules['allow-afd-first-party-egress'].priority, 'Outbound', 'Tcp', [subnetPrefixes.aca], ['AzureFrontDoor.FirstParty'], ['443'])
    ]
  }
]

// 同じNSGを「作成」と「既存参照」の両方へ宣言しないよう、先に名前を分けます。
// 各ループの宣言名を重複させず、初回・再up・途中再開を同じ仕組みで扱います。
var nsgResourceNames = map(nsgDefinitions, group => 'nsg-${resourcePrefix}-${group.suffix}')
var newNsgResourceNames = filter(nsgResourceNames, nsgName => !contains(existingNetworkSecurityGroupNames, nsgName))
var existingNsgResourceNames = filter(nsgResourceNames, nsgName => contains(existingNetworkSecurityGroupNames, nsgName))

// 初回・途中再開時に不足している NSG 本体だけを作ります。
// 稼働中 AppGW の NSG へ空の properties を再 PUT すると、既存の必須規則が
// その要求に含まれず Azure の検査で拒否されます。既存本体は更新せず、下の
// 子リソースで既知の規則を毎回更新し、後続の動的規則もそのまま保持します。
resource createdNsgs 'Microsoft.Network/networkSecurityGroups@2025-01-01' = [for nsgName in newNsgResourceNames: {
  name: nsgName
  location: location
  tags: tags
  properties: {}
}]

// 専用Tags APIのscopeに使う、既存分だけの参照。NSG本体は再PUTしません。
resource nsgs 'Microsoft.Network/networkSecurityGroups@2025-01-01' existing = [for nsgName in existingNsgResourceNames: {
  name: nsgName
}]

// 既存 NSG の tags 変更も反映します。専用 Tags API は規則の配列を送らず、
// NSG 本体の再 PUT を避けたまま、通常の tags 入力を更新できます。
// https://learn.microsoft.com/rest/api/resources/tags/create-or-update-at-scope
resource nsgTagUpdates 'Microsoft.Resources/tags@2021-04-01' = [for (nsgName, i) in existingNsgResourceNames: {
  scope: nsgs[i]
  name: 'default'
  properties: { tags: tags }
  dependsOn: [createdNsgs]
}]

// 構築入力はAzure提供DNSに固定します。この基盤通信にNSGの明示Allowは不要です。
var staticNsgRules = flatten(map(range(0, length(nsgDefinitions)), i => map(nsgDefinitions[i].rules,
  rule => { nsgIndex: i, rule: rule })))

// IP 確定を待たずに設定できる Azure 基盤向け通信の許可を登録します。
@batchSize(1)
resource staticSecurityRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for entry in staticNsgRules: {
  // 作成分と既存分の両方を参照するため、共通の親名を含めた子リソース名を使います。
  name: '${nsgResourceNames[entry.nsgIndex]}/${entry.rule.name}'
  properties: entry.rule.properties
  // 初回は NSG 本体の作成後に規則を登録。既存の場合も規則の変更を省略しません。
  dependsOn: [createdNsgs, nsgTagUpdates]
}]

var routeTableDefinitions = [
  { key: 'agw', suffix: 'agw' }
  { key: 'apim', suffix: 'apim' }
  { key: 'management', suffix: 'vm' }
]

// 親ルート表の PUT は routes を省略しても既存経路を消すため、azd が読み取った
// 書込可能な既存経路を明示して保持します。初回だけ空の配列になります。
// AppGW → APIM の実 VIP /32 等は後続の子リソースで追加・更新します。
// 本体の更新は省略せず、タグや BGP 伝搬設定の変更も同時に反映します。
// true=伝搬という利用者設定を ARM の disable... へここだけで反転します。
resource routeTables 'Microsoft.Network/routeTables@2025-01-01' = [for table in routeTableDefinitions: {
  name: 'rt-${resourcePrefix}-${table.suffix}'
  location: location
  tags: tags
  properties: {
    // User settings are affirmative: true propagates, false suppresses.
    // Azure's native property is negative, so invert only at this boundary.
    // An independent VNet has no gateway routes; propagation defaults to true.
    disableBgpRoutePropagation: !(config.?bgpRoutePropagation[table.key] ?? true)
    routes: existingRouteTableRoutes[?'rt-${resourcePrefix}-${table.suffix}'] ?? []
  }
}]

resource portalRouteTable 'Microsoft.Network/routeTables@2025-01-01' = {
  name: 'rt-${resourcePrefix}-aca'
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: true
    routes: existingRouteTableRoutes[?'rt-${resourcePrefix}-aca'] ?? []
  }
}
resource portalRoutes 'Microsoft.Network/routeTables/routes@2025-01-01' = [for route in [
  { name: 'default-egress', prefix: '0.0.0.0/0' }
  { name: 'agw-return', prefix: subnetPrefixes.agw }
]: {
  parent: portalRouteTable
  name: route.name
  properties: { addressPrefix: route.prefix, nextHopType: 'VirtualAppliance', nextHopIpAddress: firewall.properties.ipConfigurations[0].properties.privateIPAddress }
}]

// ACAを含む６サブネットの VNet。予備 /24 は割り当てず、将来の拡張用に残します。
resource vnet 'Microsoft.Network/virtualNetworks@2025-01-01' = {
  name: 'vnet-${resourcePrefix}'
  location: location
  tags: tags
  // existing 参照だけでは新規 NSG の作成待ちにならないため、順序を明示します。
  dependsOn: [createdNsgs]
  properties: {
    addressSpace: { addressPrefixes: addressSpaces }
    dhcpOptions: { dnsServers: dnsServers }
    subnets: concat([
      {
        name: 'snet-${resourcePrefix}-agw'
        properties: {
          addressPrefix: subnetPrefixes.agw
          networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', nsgResourceNames[0]) }
          routeTable: { id: routeTables[0].id }
          defaultOutboundAccess: false
        }
      }
      {
        name: 'AzureFirewallSubnet'
        properties: {
          addressPrefix: subnetPrefixes.firewall
        }
      }
      {
        name: 'snet-${resourcePrefix}-apim'
        properties: {
          addressPrefix: subnetPrefixes.apim
          networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', nsgResourceNames[1]) }
          routeTable: { id: routeTables[1].id }
          defaultOutboundAccess: false
        }
      }
      {
        name: 'snet-${resourcePrefix}-pep'
        properties: {
          addressPrefix: subnetPrefixes.pep
          networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', nsgResourceNames[2]) }
          privateEndpointNetworkPolicies: 'NetworkSecurityGroupEnabled'
          defaultOutboundAccess: false
        }
      }
      {
        name: 'snet-${resourcePrefix}-vm'
        properties: {
          addressPrefix: subnetPrefixes.management
          networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', nsgResourceNames[3]) }
          routeTable: { id: routeTables[2].id }
          defaultOutboundAccess: false
        }
      }

    ], [{
      name: 'snet-${resourcePrefix}-aca'
      properties: {
        addressPrefix: subnetPrefixes.aca
        networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', nsgResourceNames[4]) }
        routeTable: { id: portalRouteTable!.id }
        delegations: [{ name: 'containerapps', properties: { serviceName: 'Microsoft.App/environments' } }]
        defaultOutboundAccess: false
      }
    }])
  }
}

// Foundry は OpenAI / Cognitive / Services で DNS suffix が異なります。１種類だけ作ると API によって外へ解決されます。
var zoneDefinitions = [
  { key: 'foundryCognitive', name: 'privatelink.cognitiveservices.azure.com' }
  { key: 'foundryOpenAi', name: 'privatelink.openai.azure.com' }
  { key: 'foundryServices', name: 'privatelink.services.ai.azure.com' }
  { key: 'keyVault', name: 'privatelink.vaultcore.azure.net' }
]

// Foundry の API 群と Key Vault の Private Link 用 DNS Zone。名前は Azure の標準 suffix に合わせます。
resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zone in zoneDefinitions: {
  name: zone.name
  location: 'global'
  tags: tags
}]

// この VNet から Private DNS を引けるように関連付けます。VM の名前を自動登録するリンクではありません。
resource privateDnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zone, i) in zoneDefinitions: {
  parent: privateDnsZones[i]
  name: 'link-${resourcePrefix}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    resolutionPolicy: 'Default'
    virtualNetwork: { id: vnet.id }
  }
}]

// Firewall の固定公開 IP。管理 VM 等の外向き通信の送信元になります。
resource firewallPublicIp 'Microsoft.Network/publicIPAddresses@2025-01-01' = {
  name: 'pip-afw-${resourcePrefix}'
  location: location
  tags: tags
  zones: zones
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

// 利用者が接続する AppGW の固定公開 IP。AppGW 本体と同じゾーン指定が必要です。
resource appGatewayPublicIp 'Microsoft.Network/publicIPAddresses@2025-01-01' = {
  name: 'pip-agw-${resourcePrefix}'
  location: location
  tags: tags
  zones: config.?appGatewayZones ?? []
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

// 通信検査の共通設定。Standard では IDPS の設定自体を送らず、Premium の場合だけ構成します。
resource firewallPolicy 'Microsoft.Network/firewallPolicies@2025-01-01' = {
  name: 'afwp-${resourcePrefix}'
  location: location
  tags: tags
  properties: {
    sku: { tier: firewallTier }
    threatIntelMode: firewallSettings.?threatIntelMode ?? 'Alert'
    // Standard does not support IDPS: omit the entire property. Premium's
    // Alert/Deny mode and reviewed signature exceptions remain standard settings.
    ...(firewallTier == 'Premium' ? {
      intrusionDetection: {
        mode: firewallSettings.?idpsMode ?? 'Alert'
        configuration: {
          privateRanges: idpsPrivateRanges
          ...(empty(firewallSettings.?signatureOverrides ?? []) ? {} : { signatureOverrides: firewallSettings.signatureOverrides })
          bypassTrafficSettings: []
        }
      }
    } : {})
    dnsSettings: {
      enableProxy: true
      servers: []
    }
    // Keep private-destination non-SNAT; IDPS classification ranges above
    // are distinct from these SNAT private ranges.
    snat: { privateRanges: snatPrivateRanges }
  }
}

var apimPlatformDependencies = [
  { name: 'storage', destinations: ['Storage'], ports: ['443'] }
  { name: 'sql', destinations: ['Sql'], ports: ['1433'] }
  { name: 'key-vault', destinations: ['AzureKeyVault'], ports: ['443'] }
  { name: 'monitor', destinations: ['AzureMonitor'], ports: ['1886', '443'] }
  // Log to Event Hubs / Azure Monitor の任意機能向け到達性を初期常設で備えます。
  { name: 'monitor-event-hubs', destinations: ['EventHub'], ports: ['443', '5671', '5672'] }
  { name: 'entra', destinations: ['AzureActiveDirectory'], ports: ['443'] }
  { name: 'credential-manager', destinations: ['AzureConnectors'], ports: ['443'] }
]
var managementPlatformDependencies = [
  { name: 'entra', destinations: ['AzureActiveDirectory'] }
  { name: 'arm', destinations: ['AzureResourceManager'] }
  { name: 'monitor', destinations: ['AzureMonitor'] }
  { name: 'agent-storage', destinations: ['Storage'] }
]

// APIM / VM が Azure 基盤を利用するための許可。SQL 1433 は APIM 内部基盤向けで、業務 DB の作成ではありません。
resource platformRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewallPolicy
  name: 'rcg-platform'
  properties: {
    priority: 300
    ruleCollections: [
      {
        name: 'allow-apim-platform'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: concat(map(apimPlatformDependencies, dependency => {
          name: dependency.name == 'storage' ? 'allow-apim-storage' : dependency.name == 'sql' ? 'allow-apim-sql' : dependency.name == 'key-vault' ? 'allow-apim-key-vault' : dependency.name == 'monitor' ? 'allow-apim-monitor' : dependency.name == 'monitor-event-hubs' ? 'allow-apim-event-hubs' : dependency.name == 'entra' ? 'allow-apim-entra' : dependency.name == 'credential-manager' ? 'allow-apim-credential-manager' : 'apim-${dependency.name}'
          ruleType: 'NetworkRule'
          sourceAddresses: [subnetPrefixes.apim]
          destinationAddresses: dependency.destinations
          destinationPorts: dependency.ports
          ipProtocols: ['TCP']
        }), [
          {
            // APIM's managed platform has its own time dependency. It is
            // independent of the Linux management host's configured NTP.
            name: 'allow-apim-ntp'
            ruleType: 'NetworkRule'
            sourceAddresses: [subnetPrefixes.apim]
            // APIM and Firewall resolve NTP independently; allow UDP123 without an FQDN.
            // The subnet NSG separately limits this UDP123 flow to Internet.
            destinationAddresses: ['*']
            destinationPorts: ['123']
            ipProtocols: ['UDP']
          }
        ])
      }
      {
        name: 'allow-vm-platform'
        priority: 210
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: map(managementPlatformDependencies, dependency => {
          name: 'allow-mgmt-vm-${dependency.name}'
          ruleType: 'NetworkRule'
          sourceAddresses: [subnetPrefixes.management]
          destinationAddresses: dependency.destinations
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        })
      }


    ]
  }
}

// APIM の TLS 証明書確認6宛先と内部 PKI 1宛先。証明書の発行・購入 API ではありません。
// 管理 VM でも既存の互換到達性を保持します。全7宛先の必須利用は未確認です。
var certificateFqdns = [
  'mscrl.microsoft.com'
  'crl.microsoft.com'
  'oneocsp.microsoft.com'
  'cacerts.digicert.com'
  'crl3.digicert.com'
  'csp.digicert.com'
  'issuer.pki.azure.com'
]
// 共通配布先は固定。CAの業務選択と、この基盤で構築する2公開Hostだけを差し込みます。
var managementServiceFqdns = union([
  certificateDirectory == 'production' ? 'acme-v02.api.letsencrypt.org' : 'acme-staging-v02.api.letsencrypt.org'
  'management.azure.com'
  'cdn.oneget.org'
  'cdn.powershellgallery.com'
  '*.powershellgallery.com'
  'go.microsoft.com'
  'aka.ms'
], [gatewayPublicHostName, portalPublicHostName])
// ACA と同じ構築リージョンの共有 Dev gateway。各資源の操作認可は MI/RBAC で制限します。
// https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py
var acaDevFqdn = '${toLower(replace(location, ' ', ''))}.azurecontainerapps.dev'
// これは APIM の Azure 管理基盤の依存先です。管理 VM が Windows という意味ではありません。
var windowsUpdateFqdns = [
  '*.update.microsoft.com'
  '*.ctldl.windowsupdate.com'
  'ctldl.windowsupdate.com'
  'download.windowsupdate.com'
  '*.windowsupdate.com'
  '*.delivery.mp.microsoft.com'
  'go.microsoft.com'
]
// 管理 VM の Ubuntu パッケージと固定版 PowerShell / Azure CLI の取得先。初期準備にも使用します。
var linuxPackageFqdns = [
  'azure.archive.ubuntu.com'
  'archive.ubuntu.com'
  'security.ubuntu.com'
  'packages.microsoft.com'
]
var apimInfrastructureFqdns = [
  'azurewatsonanalysis-prod.${environment().suffixes.storage}'
  '*.data.microsoft.com'
  'azureprofiler.trafficmanager.net'
  'shavamanifestazurecdnprod1.azureedge.net'
  'shavamanifestcdnprod1.azureedge.net'
  'wdcp.microsoft.com'
  'wdcpalt.microsoft.com'
  'dc.services.visualstudio.com'
]

// Writes to the same Firewall Policy must settle serially, including when an
// existing Firewall is attached. There is no Internet/* Network rule fallback.
// 外部依存先の FQDN 許可。Linux パッケージ、ACME、DNS API 等に限定し、任意 Internet 全許可にはしません。
resource egressRules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: firewallPolicy
  name: 'rcg-egress'
  dependsOn: [platformRules]
  properties: {
    priority: 400
    ruleCollections: [
      {
        name: 'allow-apim-egress'
        priority: 200
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [
          {
            name: 'allow-apim-certificate-validation'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: certificateFqdns
            terminateTLS: false
          }
          {
            name: 'allow-apim-windows-update'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: filter(windowsUpdateFqdns, fqdn => fqdn != 'go.microsoft.com')
            terminateTLS: false
          }
          {
            // Microsoft's FWLink host is an APIM internal infrastructure dependency.
            name: 'allow-apim-microsoft-redirect'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: filter(windowsUpdateFqdns, fqdn => fqdn == 'go.microsoft.com')
            terminateTLS: false
          }
          {
            name: 'allow-apim-platform-diagnostics'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: filter(apimInfrastructureFqdns, fqdn => !contains([
              'wdcp.microsoft.com'
              'wdcpalt.microsoft.com'
              'dc.services.visualstudio.com'
            ], fqdn))
            terminateTLS: false
          }
          {
            name: 'allow-apim-windows-defender'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: filter(apimInfrastructureFqdns, fqdn => contains([
              'wdcp.microsoft.com'
              'wdcpalt.microsoft.com'
            ], fqdn))
            terminateTLS: false
          }
          {
            // Retain the existing diagnostic allowance; the APIM instance sender is unconfirmed.
            name: 'allow-apim-portal-diagnostics'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: filter(apimInfrastructureFqdns, fqdn => fqdn == 'dc.services.visualstudio.com')
            terminateTLS: false
          }
          {
            name: 'allow-apim-approved-services'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Https', port: 443 }]
            // 常設の基底は既FIX証明書規則の同src/HTTPS443/TLSfalseと重複し、許可を拡張しません。
            targetFqdns: union(['oneocsp.microsoft.com'], generatedApimServiceFqdns)
            terminateTLS: false
          }
        ]
      }

      {
        name: 'allow-vm-egress'
        priority: 210
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: [
          {
            name: 'allow-mgmt-vm-certificate-validation'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.management]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: certificateFqdns
            terminateTLS: false
          }
          {
            name: 'allow-mgmt-vm-linux-packages'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.management]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: linuxPackageFqdns
            terminateTLS: false
          }
          {
            name: 'allow-mgmt-vm-aca-dev-https'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.management]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: [acaDevFqdn]
            terminateTLS: false
          }
          {
            name: 'allow-mgmt-vm-approved-services'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.management]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: managementServiceFqdns
            terminateTLS: false
          }
        ]
      }
    ]
  }
}

// ポリシーが完成してから Firewall を作成。割り当てられたプライベート IP を UDR に渡します。
resource firewall 'Microsoft.Network/azureFirewalls@2025-01-01' = {
  name: 'afw-${resourcePrefix}'
  location: location
  tags: tags
  zones: zones
  dependsOn: [egressRules]
  properties: {
    sku: { name: 'AZFW_VNet', tier: firewallTier }
    firewallPolicy: { id: firewallPolicy.id }
    ipConfigurations: [{
      name: 'fwipcfg'
      properties: {
        subnet: { id: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'AzureFirewallSubnet') }
        publicIPAddress: { id: firewallPublicIp.id }
      }
    }]
  }
}

// 宛先がサブネットで決まる経路は先に用意します。APIM の実 VIP を
// 必要とする往路だけを、作成後の network-bindings.bicep で追加します。
// 往路だけ FW に向けると戻りが別経路になるため APIM→AppGW も FW へ戻します。PE 宛は細かい VNet 経路を保持します。
var initialRoutes = [
  { tableIndex: 1, name: 'agw-return-via-firewall', prefix: subnetPrefixes.agw, nextHopType: 'VirtualAppliance' }
  { tableIndex: 1, name: 'default-egress', prefix: '0.0.0.0/0', nextHopType: 'VirtualAppliance' }
  { tableIndex: 1, name: 'apim-management-direct', prefix: 'ApiManagement', nextHopType: 'Internet' }
  { tableIndex: 2, name: 'default-egress', prefix: '0.0.0.0/0', nextHopType: 'VirtualAppliance' }
]

// APIM と VM の外向き通信を Firewall へ送り、APIM の管理通信には必要な直接経路を残します。
@batchSize(1)
resource routes 'Microsoft.Network/routeTables/routes@2025-01-01' = [for route in initialRoutes: {
  parent: routeTables[route.tableIndex]
  name: route.name
  properties: union({
    addressPrefix: route.prefix
    nextHopType: route.nextHopType
  }, route.nextHopType == 'VirtualAppliance' ? {
    nextHopIpAddress: firewall.properties.ipConfigurations[0].properties.privateIPAddress
  } : {})
}]

output vnetId string = vnet.id
output subnetIds object = {
  agw: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-${resourcePrefix}-agw')
  firewall: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'AzureFirewallSubnet')
  apim: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-${resourcePrefix}-apim')
  pep: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-${resourcePrefix}-pep')
  management: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-${resourcePrefix}-vm')
  aca: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-${resourcePrefix}-aca')
}
output subnetPrefixes object = subnetPrefixes
output nsgNames object = { agw: nsgResourceNames[0], apim: nsgResourceNames[1], pep: nsgResourceNames[2], management: nsgResourceNames[3], aca: nsgResourceNames[4] }
output routeTableNames object = { agw: routeTables[0].name, apim: routeTables[1].name, management: routeTables[2].name }
output privateDnsZoneIds object = {
  foundryCognitive: privateDnsZones[0].id
  foundryOpenAi: privateDnsZones[1].id
  foundryServices: privateDnsZones[2].id
  keyVault: privateDnsZones[3].id
}
output firewallId string = firewall.id
output firewallPolicyName string = firewallPolicy.name
output firewallPolicyId string = firewallPolicy.id
output monitoredResourceIds array = concat([vnet.id, firewall.id, firewallPolicy.id], map(nsgDefinitions, definition => resourceId('Microsoft.Network/networkSecurityGroups', 'nsg-${resourcePrefix}-${definition.suffix}')), map(routeTableDefinitions, definition => resourceId('Microsoft.Network/routeTables', 'rt-${resourcePrefix}-${definition.suffix}')))
output firewallPrivateIp string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
output firewallPublicIpId string = firewallPublicIp.id
output firewallPublicIpAddress string = firewallPublicIp.properties.ipAddress
output appGatewayPublicIpId string = appGatewayPublicIp.id
output appGatewayPublicIpAddress string = appGatewayPublicIp.properties.ipAddress
