// 検索・通知用の Log Analytics と、監査証跡の保存用 Storage を先に作成します。
// 同じリージョンへの出力が必要な診断ログに対応するため、日本側と Foundry 側の保存先を分けます。
// WORM の Locked は保存期間中の削除を制限する運用設定です。検証用の自動撤去装置ではありません。

targetScope = 'resourceGroup'

@minLength(1)
@description('全リソース名の製品名部分。例は egakumi です。')
param productName string
@minLength(1)
@description('同じ製品内の環境を区別する名前。test / dev / prod など。名前自体で設定は切り替えません。')
param environmentName string
@description('環境名と製品名をこの順で連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string = '${environmentName}-${productName}'
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('Foundry とモデルの配置リージョン。モデル提供状況と利用枠を事前確認します。')
param foundryLocation string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}
@minValue(90)
@maxValue(730)
@description('Log Analytics 内の検索用ログ保持日数。監査 Storage の保管期間とは別です。')
param retentionInDays int = 90
@description('Log Analytics の１日取り込み上限（GB）。-1 は日次上限を設定しない指定です。')
param workspaceDailyQuotaGb int = -1
@allowed(['Standard_LRS', 'Standard_GRS', 'Standard_RAGRS', 'Standard_ZRS', 'Standard_GZRS', 'Standard_RAGZRS'])
@description('監査 Storage の冗長化方式。GRS / LRS 等を運用上の復旧要件で選びます。')
param auditStorageSku string = 'Standard_GRS'
@description('監査 Storage に直接アクセスを許可する IPv4 一覧。空配列で一般公開にはなりません。')
param auditAllowedIpAddresses array = []
@allowed(['Disabled', 'Unlocked', 'Locked'])
@description('監査 Blob の保存保護。Disabled / Unlocked / Locked の違いを理解して選択します。')
param auditImmutabilityMode string = 'Locked'
@minValue(1)
@maxValue(146000)
@description('監査 Blob の削除を制限する日数。Locked にすると保持期間中は短縮・削除できません。')
param auditRetentionDays int = 2557
@description('監査コンテナーの保存保護ポリシーをこの呼出しで管理するか。既存の Locked 保護を解除する指定ではありません。')
param manageAuditImmutabilityPolicies bool = true
@description('実状態を確認した既存 Locked ポリシーの ID。保持を変更せず、新規・未ロックの対象だけを配備します。内部入力です。')
param retainedAuditImmutabilityPolicyIds array = []
@description('この段階で監査ログの転送も設定するか。保存先と保護が準備できてから有効にします。')
param enableAuditCollection bool = false
@description('既存Workspaceの取り込み変換DCR参照。再配備で有効な関連付けを消さない内部入力です。')
param existingWorkspaceDataCollectionRuleResourceId string = ''

// 長期保存へ転送するテーブルを明示。Linux の認証は Syslog、アプリ監視は App* の各テーブルです。
var privacy = loadJsonContent('../log-privacy.json')
var exportTables = ['AppRequests', 'AppDependencies', 'AppExceptions', 'AppTraces', 'AppEvents', 'Syslog', privacy.table]
var analyticsTables = concat(exportTables, [
  'AppMetrics', 'AppPageViews', 'AppBrowserTimings', 'AppAvailabilityResults'
  'Perf', 'Heartbeat', 'AzureActivity', 'AzureMetrics', 'LAQueryLogs', 'StorageBlobLogs'
])
// Storage は小文字と数字だけを許可するためハイフンだけを取り除きます。
// 名前を途中で切らず、24 文字を超える組合せは配備前の設定検査で拒否します。
var compactName = replace(resourceStem, '-', '')
// 収集先と保全用の役割を分け、必要なリージョンに配置します。保存の冗長化・削除保護は本番運用の要件で指定します。
var auditAccounts = [
  { key: 'collectJapan', name: 'st${compactName}jp', location: location }
  { key: 'collectFoundry', name: 'st${compactName}us', location: foundryLocation }
]
var japanContainers = concat([
  'insights-activity-logs'
  'insights-logs-audit'
  'insights-logs-vmprotectionalerts'
  'insights-logs-applicationgatewayaccesslog'
  'insights-logs-applicationgatewayfirewalllog'
  'insights-logs-azfwnetworkrule'
  'insights-logs-azfwapplicationrule'
  'insights-logs-azfwidpssignature'
  'insights-logs-azfwthreatintel'
  'insights-logs-gatewaylogs'
  'insights-logs-gatewayllmlogs'
  'insights-logs-websocketconnectionlogs'
  'insights-logs-auditevent'
], map(exportTables, tableName => 'am-${toLower(tableName)}'))
var containerDefinitions = concat(
  map(japanContainers, containerName => { accountIndex: 0, name: containerName }),
  [{ accountIndex: 1, name: 'insights-logs-audit' }]
)

// 調査時にログを検索する Workspace。監査 Storage の長期保護とは別の保持設定です。
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-${resourceStem}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionInDays
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
    workspaceCapping: { dailyQuotaGb: workspaceDailyQuotaGb }
    ...(!empty(existingWorkspaceDataCollectionRuleResourceId) ? { defaultDataCollectionRuleResourceId: existingWorkspaceDataCollectionRuleResourceId } : {})
  }
}

// 診断ログをLAWへ保存する前にquery全体を除去します。WAF自体のlogScrubbingとは別の保護です。
// 関連付けはmonitoring段階の配備後に限定PATCHし、次のcore段階へ実IDを引き継ぎます。
resource workspacePrivacy 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-${resourceStem}-workspace'
  location: location
  kind: 'WorkspaceTransforms'
  tags: tags
  properties: {
    dataSources: {}
    destinations: { logAnalytics: [{ name: 'law-core', workspaceResourceId: workspace.id }] }
    dataFlows: [{ streams: [privacy.stream], destinations: ['law-core'], transformKql: privacy.transformKql }]
  }
  dependsOn: [tables]
}

// アプリと Gateway のテレメトリー送信先。Workspace に接続して横断検索します。
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${resourceStem}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    SamplingPercentage: 100
    DisableIpMasking: false
    DisableLocalAuth: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Built-in table schemas are supplied by Azure. Explicit retention avoids stale
// table-specific overrides after an update of the workspace default.
// 使用するテーブルと保持日数を明示します。
resource tables 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = [for tableName in analyticsTables: {
  parent: workspace
  name: tableName
  properties: {
    plan: 'Analytics'
    schema: { name: tableName }
    retentionInDays: retentionInDays
    totalRetentionInDays: retentionInDays
  }
}]

// Linux の認証系 Syslog と性能カウンターだけを収集。入力全文・コマンド引数の大量収集は行いません。
resource linuxCollection 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-${resourceStem}-linux'
  location: location
  kind: 'Linux'
  tags: tags
  properties: {
    dataSources: {
      syslog: [
        {
          name: 'linux-authentication'
          streams: ['Microsoft-Syslog']
          facilityNames: ['auth', 'authpriv']
          logLevels: ['Info', 'Notice', 'Warning', 'Error', 'Critical', 'Alert', 'Emergency']
        }
        {
          name: 'linux-system-warnings'
          streams: ['Microsoft-Syslog']
          facilityNames: ['kern', 'daemon', 'syslog']
          logLevels: ['Warning', 'Error', 'Critical', 'Alert', 'Emergency']
        }
      ]
      performanceCounters: [{
        name: 'linux-performance'
        samplingFrequencyInSeconds: 60
        streams: ['Microsoft-Perf']
        counterSpecifiers: [
          'Processor(*)\\% Processor Time'
          'Memory(*)\\Available MBytes Memory'
          'Logical Disk(*)\\% Free Space'
        ]
      }]
    }
    destinations: { logAnalytics: [{ name: 'law-core', workspaceResourceId: workspace.id }] }
    dataFlows: [
      {
        streams: ['Microsoft-Syslog']
        destinations: ['law-core']
        // Never persist command arguments from privilege escalation or schedulers.
        // The guest rsyslog rule masks them before forwarding; this is a second guard.
        transformKql: 'source | extend SyslogMessage = iff((ProcessName =~ "sudo" and SyslogMessage contains "COMMAND=") or ProcessName in~ ("cron", "CRON"), "[Command details redacted at source]", SyslogMessage)'
        outputStream: 'Microsoft-Syslog'
      }
      { streams: ['Microsoft-Perf'], destinations: ['law-core'] }
    ]
  }
  // 収集規則の作成時に、Azureは出力先のSyslog・Perfテーブルを検査します。
  // Workspaceだけの作成完了では足りないため、テーブル配列の完了も待ちます。
  dependsOn: [tables]
}

// 同一リージョンへの診断転送に対応する監査 Storage を用意します。
resource accounts 'Microsoft.Storage/storageAccounts@2023-05-01' = [for account in auditAccounts: {
  name: account.name
  location: account.location
  kind: 'StorageV2'
  tags: tags
  sku: { name: auditStorageSku }
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // Azure Monitor's standard service integration uses account-key support;
    // human readers use Entra/RBAC. No keys are emitted by the module.
    allowSharedKeyAccess: true
    defaultToOAuthAuthentication: true
    isHnsEnabled: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      ipRules: [for address in auditAllowedIpAddresses: { value: address, action: 'Allow' }]
      virtualNetworkRules: []
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      services: {
        blob: { enabled: true, keyType: 'Account' }
        file: { enabled: true, keyType: 'Account' }
      }
    }
  }
}]

// 各監査 Storage の Blob サービスに保管・削除保護の基礎設定を行います。
resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = [for (account, i) in auditAccounts: {
  parent: accounts[i]
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: false }
    containerDeleteRetentionPolicy: { enabled: false }
  }
}]

// ログ種類ごとの保存コンテナー。アクセス権をコンテナー単位に分離できます。
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for definition in containerDefinitions: {
  parent: blobServices[definition.accountIndex]
  name: definition.name
  properties: { publicAccess: 'None' }
}]

// Lock is an explicit storage action, not a writable ARM property. It is performed
// after compatibility checks by the common deployment workflow for production.
// WORM 保存保護。Locked は保持期間中に撤去できないため、利用者の設定どおりにのみ構成します。
resource immutabilityPolicies 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = [for (definition, i) in containerDefinitions: if (auditImmutabilityMode != 'Disabled' && manageAuditImmutabilityPolicies && !contains(map(retainedAuditImmutabilityPolicyIds, policyId => toLower(policyId)), toLower('${containers[i].id}/immutabilityPolicies/default'))) {
  parent: containers[i]
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: auditRetentionDays
    allowProtectedAppendWrites: true
  }
}]

// 監査保存先そのものへのアクセスも記録します。
resource storageLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (account, i) in auditAccounts: {
  name: 'diag-${resourceStem}-${account.key}-blob'
  scope: blobServices[i]
  properties: {
    workspaceId: workspace.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'StorageRead', enabled: true }
      { category: 'StorageWrite', enabled: true }
      { category: 'StorageDelete', enabled: true }
    ]
  }
}]

// Workspace の管理・監査操作を監査 Storage に保存します。
resource workspaceAudit 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${resourceStem}-law'
  scope: workspace
  properties: {
    workspaceId: workspace.id
    logAnalyticsDestinationType: 'Dedicated'
    ...(enableAuditCollection ? { storageAccountId: accounts[0].id } : {})
    logs: [{ category: 'Audit', enabled: true }]
  }
  dependsOn: [immutabilityPolicies]
}

// 検索用テーブルの記録を監査 Storage へ継続転送。先に保存先が用意できてから有効にします。
resource auditExport 'Microsoft.OperationalInsights/workspaces/dataExports@2023-09-01' = if (enableAuditCollection) {
  parent: workspace
  name: 'export-${resourceStem}-audit'
  properties: {
    destination: { resourceId: accounts[0].id }
    tableNames: exportTables
    enable: true
  }
  dependsOn: [containers, immutabilityPolicies, tables]
}

output monitoring object = {
  workspaceId: workspace.id
  workspaceName: workspace.name
  workspaceCustomerId: workspace.properties.customerId
  appInsightsId: appInsights.id
  appInsightsName: appInsights.name
  appInsightsConnectionString: appInsights.properties.ConnectionString
  linuxDataCollectionRuleId: linuxCollection.id
  workspacePrivacyDataCollectionRuleId: workspacePrivacy.id
  auditExportName: 'export-${resourceStem}-audit'
  auditCollectionEnabled: enableAuditCollection
  appInsightsDailyCapConfiguration: 'Set-AzApplicationInsightsDailyCap and read back before operational acceptance'
}

var containerIds = [for (definition, i) in containerDefinitions: containers[i].id]
var policyIds = [for (definition, i) in containerDefinitions: '${containers[i].id}/immutabilityPolicies/default']

output storage object = {
  collectJapan: {
    id: accounts[0].id
    name: accounts[0].name
    blobServiceId: blobServices[0].id
    blobEndpoint: accounts[0].properties.primaryEndpoints.blob
    location: auditAccounts[0].location
  }
  collectFoundry: {
    id: accounts[1].id
    name: accounts[1].name
    blobServiceId: blobServices[1].id
    blobEndpoint: accounts[1].properties.primaryEndpoints.blob
    location: auditAccounts[1].location
  }
  immutabilityMode: auditImmutabilityMode
  retentionInDays: auditRetentionDays
  containerIds: containerIds
  immutabilityPolicyIds: policyIds
}
