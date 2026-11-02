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
@description('azd の構築前処理が実在を確認した、この基盤の NSG 名。公開 config では指定しません。')
param existingNetworkSecurityGroupNames array = []
@description('azd の読取結果から渡す実ルート表名と既存経路の対応。初回は空、再 up は後続段階で作った経路も保持します。')
param existingRouteTableRoutes object = {}

// Design authority: infrastructure/{virtual-network,network-security-group,
// route-table,private-dns-zone,azure-firewall,firewall-policy}. The core owns
// five base subnets, four NSGs and three base route tables. The required
// core sample portal adds a delegated ACA subnet and its own route table.
var addressSpaces = config.?addressSpaces ?? ['10.40.0.0/24', '10.40.1.0/24', '10.40.2.0/24', '10.40.3.0/24']
var subnetPrefixes = config.?subnetPrefixes ?? {
  agw: '10.40.0.0/24'
  pep: '10.40.1.0/24'
  firewall: '10.40.2.0/26'
  apim: '10.40.2.64/26'
  management: '10.40.2.128/26'
  aca: '10.40.2.192/26'
}
var dnsServers = config.?dnsServers ?? []
var firewallSettings = config.?firewall ?? {}
var firewallTier = firewallSettings.?sku ?? 'Premium'
var zones = firewallSettings.?zones ?? []
var ntpAddressPrefixes = config.?ntpAddressPrefixes ?? ['Internet']
// Azureの環境定義からStorage suffixとEntraの標準ログイン先を使います。
// 認証先を利用者の要求ヘッダーや動的な外部URLから選ぶことはありません。

// Azure の NSG API は単一宛先と複数宛先でプロパティ名が異なるため、個数に合わせて許可規則を組み立てます。
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

func withAccess(rule object, access string) object => union(rule, { properties: union(rule.properties, { access: access }) })

// Public API source restriction is optional; omission permits all IPv4 clients.
var clientAddressPrefixes = config.?clientAddressPrefixes ?? ['0.0.0.0/0']
// 小さい priority の規則から評価されます。接続元を許可しても別段階の token/key 認証と API 認可は必要です。
var nsgDefinitions = [
  {
    key: 'agw'
    suffix: 'agw'
    rules: [
      // Keep the child resource even when the allowlist is empty. Updating a
      // populated allowlist to [] must revoke the previous permission.
      empty(clientAddressPrefixes)
        ? withAccess(allowRule('allow-client-web', 4080, 'Inbound', 'Tcp', ['*'], [subnetPrefixes.agw], ['80', '443']), 'Deny')
        : allowRule('allow-client-web', 100, 'Inbound', 'Tcp', clientAddressPrefixes, [subnetPrefixes.agw], ['80', '443'])
      allowRule('allow-gateway-manager', 110, 'Inbound', 'Tcp', ['GatewayManager'], ['*'], ['65200-65535'])
      allowRule('allow-load-balancer', 120, 'Inbound', '*', ['AzureLoadBalancer'], ['*'], ['*'])
      // The normal AGW v2 deployment requires direct Internet egress and no
      // custom outbound Deny. Its backend-specific allow is bound afterwards.
      allowRule('allow-internet', 200, 'Outbound', '*', [subnetPrefixes.agw], ['Internet'], ['*'])
    ]
  }
  {
    key: 'apim'
    suffix: 'apim'
    rules: [
      allowRule('allow-agw-api', 100, 'Inbound', 'Tcp', [subnetPrefixes.agw], [subnetPrefixes.apim], ['80'])
      allowRule('allow-apim-management', 110, 'Inbound', 'Tcp', ['ApiManagement'], [subnetPrefixes.apim], ['3443'])
      allowRule('allow-load-balancer', 120, 'Inbound', 'Tcp', ['AzureLoadBalancer'], [subnetPrefixes.apim], ['6390'])
      allowRule('allow-counter-sync-inbound', 130, 'Inbound', 'Udp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['4290'])
      allowRule('allow-storage', 200, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Storage'], ['443'])
      // This is APIM's platform dependency, not a provisioned SQL Database.
      allowRule('allow-sql-platform', 210, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Sql'], ['1433'])
      allowRule('allow-key-vault-platform', 220, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureKeyVault'], ['443'])
      allowRule('allow-monitor', 230, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureMonitor'], ['1886', '443'])
      // Azure Monitor の APIM 監査ログは Event Hubs 側にも送信します。
      // AzureMonitor タグだけではこの宛先を含まないため、APIM から必要な TCP ポートだけを許可します。
      // https://learn.microsoft.com/azure/api-management/virtual-network-reference#required-ports
      allowRule('allow-monitor-event-hubs', 235, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['EventHub'], ['443', '5671', '5672'])
      allowRule('allow-entra', 240, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureActiveDirectory'], ['443'])
      allowRule('allow-credential-manager', 250, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['AzureConnectors'], ['443'])
      // Firewall application rules enforce the reviewed FQDN allowlist.
      allowRule('allow-web-dependencies', 260, 'Outbound', 'Tcp', [subnetPrefixes.apim], ['Internet'], ['80', '443'])
      // The managed APIM platform has a separate time dependency; a private
      // Linux NTP override must not remove its required platform egress.
      allowRule('allow-time-sync', 270, 'Outbound', 'Udp', [subnetPrefixes.apim], ['Internet'], ['123'])
      allowRule('allow-counter-sync-outbound', 280, 'Outbound', 'Udp', [subnetPrefixes.apim], [subnetPrefixes.apim], ['4290'])
    ]
  }
  {
    key: 'pep'
    suffix: 'pep'
    rules: []
  }
  {
    key: 'management'
    suffix: 'vm'
    rules: concat([
      allowRule('allow-approved-entra', 300, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureActiveDirectory'], ['443'])
      allowRule('allow-approved-management-api', 310, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureResourceManager'], ['443'])
      allowRule('allow-approved-monitor', 320, 'Outbound', 'Tcp', [subnetPrefixes.management], ['AzureMonitor'], ['443'])
      allowRule('allow-approved-agent-storage', 330, 'Outbound', 'Tcp', [subnetPrefixes.management], ['Storage'], ['443'])
      allowRule('allow-web-dependencies', 350, 'Outbound', 'Tcp', [subnetPrefixes.management], ['Internet'], ['80', '443'])
      allowRule('allow-time-sync', 360, 'Outbound', 'Udp', [subnetPrefixes.management], ntpAddressPrefixes, ['123'])
    ], config.?managementAdditionalSecurityRules ?? [])
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

// Azure-provided DNS is platform traffic; AzurePlatformDNS must not be used in
// an Allow rule. Keep the two named children even with default DNS so moving
// back from custom DNS replaces (and revokes) the former custom-server allow.
// dnsServers=[] は Azure 提供 DNS。独自 IP を指定する企業構成では、その DNS に Private Link 名の解決先も用意します。
var effectiveDnsServers = empty(dnsServers) ? ['168.63.129.16'] : dnsServers
var staticNsgRules = flatten(map(range(0, length(nsgDefinitions)), i => map(concat(nsgDefinitions[i].rules,
  nsgDefinitions[i].key == 'pep' ? [] : [
    allowRule('allow-dns-tcp', 400, 'Outbound', 'Tcp', ['*'], effectiveDnsServers, ['53'])
    allowRule('allow-dns-udp', 410, 'Outbound', 'Udp', ['*'], effectiveDnsServers, ['53'])
  ]), rule => { nsgIndex: i, rule: rule })))

// IP 確定を待たずに設定できる Azure 基盤向け通信や DNS の許可を登録します。
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
    // Alert/Deny mode, ranges and reviewed signature exceptions are inputs.
    ...(firewallTier == 'Premium' ? {
      intrusionDetection: {
        mode: firewallSettings.?idpsMode ?? 'Alert'
        configuration: {
          ...(empty(firewallSettings.?idpsPrivateRanges ?? []) ? {} : { privateRanges: firewallSettings.idpsPrivateRanges })
          ...(empty(firewallSettings.?signatureOverrides ?? []) ? {} : { signatureOverrides: firewallSettings.signatureOverrides })
          ...(empty(firewallSettings.?bypassTrafficSettings ?? []) ? {} : { bypassTrafficSettings: firewallSettings.bypassTrafficSettings })
        }
      }
    } : {})
    dnsSettings: {
      enableProxy: true
      servers: firewallSettings.?dnsServers ?? dnsServers
    }
    // Keep private-destination non-SNAT; IDPS classification ranges above
    // are distinct from these SNAT private ranges.
    snat: { privateRanges: firewallSettings.?snatPrivateRanges ?? ['IANAPrivateRanges'] }
  }
}

var apimPlatformDependencies = [
  { name: 'storage', destinations: ['Storage'], ports: ['443'] }
  { name: 'sql', destinations: ['Sql'], ports: ['1433'] }
  { name: 'key-vault', destinations: ['AzureKeyVault'], ports: ['443'] }
  { name: 'monitor', destinations: ['AzureMonitor'], ports: ['1886', '443'] }
  // Azure Monitor の内部ログ転送にも使う依存先です。任意の Internet 通信は許可しません。
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
          name: 'apim-${dependency.name}'
          ruleType: 'NetworkRule'
          sourceAddresses: [subnetPrefixes.apim]
          destinationAddresses: dependency.destinations
          destinationPorts: dependency.ports
          ipProtocols: ['TCP']
        }), firewallSettings.?apimAdditionalNetworkRules ?? [])
      }
      {
        name: 'allow-vm-platform'
        priority: 210
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: concat(map(managementPlatformDependencies, dependency => {
          name: 'vm-${dependency.name}'
          ruleType: 'NetworkRule'
          sourceAddresses: [subnetPrefixes.management]
          destinationAddresses: dependency.destinations
          destinationPorts: ['443']
          ipProtocols: ['TCP']
        }), [

          {
            name: 'management-time'
            ruleType: 'NetworkRule'
            sourceAddresses: [subnetPrefixes.management]
            destinationFqdns: config.?ntpFqdns ?? ['ntp.ubuntu.com']
            destinationPorts: ['123']
            ipProtocols: ['UDP']
          }
          {
            // APIM's managed platform has its own time dependency. It is
            // independent of the Linux management host's configured NTP.
            name: 'apim-platform-time'
            ruleType: 'NetworkRule'
            sourceAddresses: [subnetPrefixes.apim]
            destinationFqdns: ['time.windows.com']
            destinationPorts: ['123']
            ipProtocols: ['UDP']
          }
        ], firewallSettings.?managementAdditionalNetworkRules ?? [])
      }

      ...(empty(dnsServers) ? [] : [
        {
          name: 'allow-custom-dns'
          priority: 220
          ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
          action: { type: 'Allow' }
          rules: [{
            name: 'custom-dns'
            ruleType: 'NetworkRule'
            sourceAddresses: [subnetPrefixes.apim, subnetPrefixes.management]
            destinationAddresses: dnsServers
            destinationPorts: ['53']
            ipProtocols: ['TCP', 'UDP']
          }]
        }
      ])
    ]
  }
}

// TLS の証明書チェーンや失効情報を確認する既知宛先。TLS の検証を無効にする代わりに必要な取得先を許可します。
var certificateFqdns = [
  'mscrl.microsoft.com'
  'crl.microsoft.com'
  'oneocsp.microsoft.com'
  'cacerts.digicert.com'
  'crl3.digicert.com'
  'csp.digicert.com'
  'issuer.pki.azure.com'
]
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
// 管理 VM の Ubuntu パッケージと固定版 PowerShell の取得先。証明書初期化前から疎通が必要です。
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
        rules: concat([
          {
            name: 'apim-certificate-and-update'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: concat(certificateFqdns, windowsUpdateFqdns)
            terminateTLS: false
          }
          {
            name: 'apim-infrastructure'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.apim]
            protocols: [{ protocolType: 'Https', port: 443 }]
            targetFqdns: apimInfrastructureFqdns
            terminateTLS: false
          }
        ], empty(firewallSettings.?apimAllowedFqdns ?? []) ? [] : [{
          name: 'apim-approved-services'
          ruleType: 'ApplicationRule'
          sourceAddresses: [subnetPrefixes.apim]
          protocols: [{ protocolType: 'Https', port: 443 }]
          targetFqdns: firewallSettings.apimAllowedFqdns
          terminateTLS: false
        }], firewallSettings.?apimAdditionalApplicationRules ?? [])
      }

      {
        name: 'allow-vm-egress'
        priority: 210
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        action: { type: 'Allow' }
        rules: concat([
          {
            name: 'vm-certificate-and-linux-packages'
            ruleType: 'ApplicationRule'
            sourceAddresses: [subnetPrefixes.management]
            protocols: [{ protocolType: 'Http', port: 80 }, { protocolType: 'Https', port: 443 }]
            targetFqdns: concat(certificateFqdns, linuxPackageFqdns)
            terminateTLS: false
          }
        ], empty(firewallSettings.?managementAllowedFqdns ?? []) ? [] : [{
          name: 'vm-approved-services'
          ruleType: 'ApplicationRule'
          sourceAddresses: [subnetPrefixes.management]
          protocols: [{ protocolType: 'Https', port: 443 }]
          // ACME CA, selected DNS provider and public hostname are supplied by
          // the certificate settings. No DNS provider is assumed here.
          targetFqdns: firewallSettings.managementAllowedFqdns
          terminateTLS: false
        }], firewallSettings.?managementAdditionalApplicationRules ?? [])
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
output nsgNames object = { agw: nsgResourceNames[0], apim: nsgResourceNames[1], pep: nsgResourceNames[2], management: nsgResourceNames[3] }
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
