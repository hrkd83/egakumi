// 作成済みの各リソースを監視先へ結び付け、障害・設定変更の通知を設定します。
// メトリックは数値の異常、ログアラートは記録内容、Activity Log は管理操作の変化を検出します。
// ロールは監視クエリや監査閲覧に必要な範囲へ限定し、通知そのものが環境を削除することはありません。

targetScope = 'resourceGroup'

@description('製品名と環境名を連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}
@description('先行作成した Workspace・Application Insights・Linux DCR の ID を含む出力。')
param monitoring object
@description('リージョン別の監査 Storage とコンテナーを含む出力。')
param storage object
@description('監視対象の Application Gateway のリソース ID。')
param appGatewayId string
@description('監視・連携対象 APIM の完全なリソース ID。')
param apimId string
@description('監視対象 Azure Firewall のリソース ID。')
param firewallId string
@description('管理操作の変更を検出するネットワーク資源 ID の一覧。')
param networkResourceIds array
@description('今回所有する５個の RG の ID。対象を限定して変更・削除操作を監視します。')
param resourceGroupIds array
@description('監視通知を受け取るメールアドレスの一覧。')
param operatorEmailAddresses array
@description('クエリによるログアラートを有効にするか。評価時の追加費用にも関わります。')
param enableLogAlerts bool = false
@description('APIM の SKU。Premium は本番構成、Developer は SLA のない検証向け構成です。')
param apimSkuName string = 'Premium'
@description('Application Insights の24時間取り込み量を警告する GB 閾値。')
param appInsightsIngestionWarningGb int = 80
@description('Application Insights へ記録を書き込む実行 ID。管理操作の権限は付けません。')
param telemetryPublisherPrincipalIds array = []
@description('リージョン別の監査コンテナーを読む担当者の一覧。')
param auditReaders array = []

// 日本側の監査コンテナーに閲覧権限を限定して割り当てます。
module japanAuditReaders 'audit-reader-bindings.bicep' = {
  name: '${resourceStem}-japan-audit-readers'
  params: { storageAccountName: storage.collectJapan.name, readers: filter(auditReaders, reader => reader.collector == 'collectJapan') }
}
// Foundry 側の監査コンテナーに閲覧権限を限定して割り当てます。
module foundryAuditReaders 'audit-reader-bindings.bicep' = {
  name: '${resourceStem}-foundry-audit-readers'
  params: { storageAccountName: storage.collectFoundry.name, readers: filter(auditReaders, reader => reader.collector == 'collectFoundry') }
}

// 検索先 Workspace を既存資源として参照します。
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: monitoring.workspaceName
}

// テレメトリー送信先を既存資源として参照します。
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: monitoring.appInsightsName
}

// Monitoring Metrics Publisher includes Microsoft.Insights/Telemetry/Write.
// https://learn.microsoft.com/azure/azure-monitor/app/azure-ad-authentication
// 実行 ID に監視データの送信だけを許可します。
resource telemetryPublisherGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in telemetryPublisherPrincipalIds: {
  scope: appInsights
  name: guid(appInsights.id, principalId, 'Monitoring Metrics Publisher')
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}]

// アラートのメール通知先。自動撤去や業務処理は登録しません。
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${resourceStem}-ops'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take(resourceStem, 12)
    enabled: true
    emailReceivers: [for (address, i) in operatorEmailAddresses: {
      name: 'operator-${i}'
      emailAddress: address
      useCommonAlertSchema: true
    }]
  }
}

// 監視指標・閾値・対象 ID の一覧。SKU 固有のメトリックは対応 SKU のときだけ追加します。
var metrics = [
  {
    key: 'law-export-failed'
    scope: monitoring.workspaceId
    resourceType: 'Microsoft.OperationalInsights/workspaces'
    region: location
    namespace: 'Microsoft.OperationalInsights/workspaces'
    metricName: 'ExportFailed'
    aggregation: 'Total'
    operator: 'GreaterThan'
    threshold: 0
    window: 'PT15M'
    frequency: 'PT5M'
    severity: 2
    dimensions: [{ name: 'LogAnalyticsDataExportRuleName', operator: 'Include', values: [monitoring.auditExportName] }]
  }
  {
    key: 'audit-blob-availability'
    scope: storage.collectJapan.blobServiceId
    resourceType: 'Microsoft.Storage/storageAccounts/blobServices'
    region: storage.collectJapan.location
    namespace: 'Microsoft.Storage/storageAccounts/blobServices'
    metricName: 'Availability'
    aggregation: 'Average'
    operator: 'LessThan'
    threshold: 99
    window: 'PT15M'
    frequency: 'PT5M'
    severity: 2
    dimensions: []
  }
  {
    key: 'audit-eus2-blob-availability'
    scope: storage.collectFoundry.blobServiceId
    resourceType: 'Microsoft.Storage/storageAccounts/blobServices'
    region: storage.collectFoundry.location
    namespace: 'Microsoft.Storage/storageAccounts/blobServices'
    metricName: 'Availability'
    aggregation: 'Average'
    operator: 'LessThan'
    threshold: 99
    window: 'PT15M'
    frequency: 'PT5M'
    severity: 2
    dimensions: []
  }
  {
    key: 'agw-unhealthy-backend'
    scope: appGatewayId
    resourceType: 'Microsoft.Network/applicationGateways'
    region: location
    namespace: 'Microsoft.Network/applicationGateways'
    metricName: 'UnhealthyHostCount'
    aggregation: 'Average'
    operator: 'GreaterThan'
    threshold: 0
    window: 'PT5M'
    frequency: 'PT1M'
    severity: 2
    dimensions: [{ name: 'BackendSettingsPool', operator: 'Include', values: ['*'] }]
  }
  {
    key: 'firewall-unhealthy'
    scope: firewallId
    resourceType: 'Microsoft.Network/azureFirewalls'
    region: location
    namespace: 'Microsoft.Network/azureFirewalls'
    metricName: 'FirewallHealth'
    aggregation: 'Average'
    operator: 'LessThan'
    threshold: 100
    window: 'PT5M'
    frequency: 'PT1M'
    severity: 1
    dimensions: []
  }
]
var metricDefinitions = concat(metrics, apimSkuName == 'Premium' ? [{
  key: 'apim-capacity-high'
  scope: apimId
  resourceType: 'Microsoft.ApiManagement/service'
  region: location
  namespace: 'Microsoft.ApiManagement/service'
  metricName: 'Capacity'
  aggregation: 'Average'
  operator: 'GreaterThan'
  threshold: 80
  window: 'PT15M'
  frequency: 'PT5M'
  severity: 2
  dimensions: [{ name: 'Location', operator: 'Include', values: ['*'] }]
}] : [])

// 数値指標の閾値を監視し、障害や負荷上昇を通知します。
resource metricAlerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [for definition in metricDefinitions: {
  name: 'ma-${resourceStem}-${definition.key}'
  location: 'global'
  tags: tags
  properties: {
    description: definition.key
    enabled: true
    autoMitigate: true
    severity: definition.severity
    scopes: [definition.scope]
    targetResourceType: definition.resourceType
    targetResourceRegion: definition.region
    windowSize: definition.window
    evaluationFrequency: definition.frequency
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: definition.key
        criterionType: 'StaticThresholdCriterion'
        metricNamespace: definition.namespace
        metricName: definition.metricName
        timeAggregation: definition.aggregation
        operator: definition.operator
        threshold: definition.threshold
        dimensions: definition.dimensions
      }]
    }
    actions: [{ actionGroupId: actionGroup.id }]
  }
}]

// 以下は KQL の監視クエリです。件数・利用量・状態を検索し、API の入力本文や生成本文を検索材料にはしません。
var auditWriteQuery = '${loadTextContent('./audit-write-failures.kql')}\nAuditWriteFailures(StorageBlobLogs, now(), dynamic(["${storage.collectJapan.name}", "${storage.collectFoundry.name}"]))'
var ingestionQuery = 'union isfuzzy=true AppRequests, AppDependencies, AppExceptions, AppTraces, AppEvents, AppMetrics, AppPageViews, AppBrowserTimings, AppAvailabilityResults\n| where TimeGenerated > ago(24h)\n| where _ResourceId =~ "${monitoring.appInsightsId}"\n| where _IsBillable == "true"\n| summarize IngestedGB = sum(_BilledSize) / 1000000000.0'
var failureRateQuery = 'AppRequests\n| where TimeGenerated > ago(15m)\n| where _ResourceId =~ "${monitoring.appInsightsId}"\n| summarize Requests=count(), Failures=countif(Success == false)\n| where Requests >= 100\n| extend FailureRate=100.0 * Failures / Requests'
var queryDefinitions = [
  // The service accepts discrete query ranges; PT30M is the first covering 15m + 5s.
  { key: 'audit-write-failed', query: auditWriteQuery, range: 'PT30M', aggregation: 'Count', measure: '', threshold: 0, severity: 2 }
  { key: 'appi-ingestion-high', query: ingestionQuery, range: 'P1D', aggregation: 'Maximum', measure: 'IngestedGB', threshold: appInsightsIngestionWarningGb, severity: 3 }
  { key: 'api-failure-rate', query: failureRateQuery, range: 'PT15M', aggregation: 'Maximum', measure: 'FailureRate', threshold: 5, severity: 2 }
]

// ログの条件に合う事象を定期評価。実行 ID を使ってクエリを認証します。
resource logAlerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [for definition in queryDefinitions: {
  name: 'la-${resourceStem}-${definition.key}'
  location: location
  kind: 'LogAlert'
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: definition.key
    enabled: enableLogAlerts
    autoMitigate: true
    severity: definition.severity
    scopes: [workspace.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    overrideQueryTimeRange: definition.range
    // Initial creation may precede ingestion. The common workflow validates the
    // queries with real data and permissions before enabling these same rules.
    skipQueryValidation: !enableLogAlerts
    criteria: {
      allOf: [union({
        query: definition.query
        timeAggregation: definition.aggregation
        operator: 'GreaterThan'
        threshold: definition.threshold
        failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
      }, empty(definition.measure) ? {} : { metricMeasureColumn: definition.measure })]
    }
    actions: { actionGroups: [actionGroup.id] }
  }
}]

// ログアラートの実行 ID に、検索先だけの参照権限を付けます。
resource queryReaderGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (definition, i) in queryDefinitions: {
  scope: workspace
  name: guid(workspace.id, logAlerts[i].id, 'Log Analytics Reader')
  properties: {
    principalId: logAlerts[i].identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
  }
}]

// Activity Log の対象は今回の資源 ID に限定します。既存の別システムの変更を今回の異常として通知しません。
var terminalAdministrative = [
  { field: 'category', equals: 'Administrative' }
  { anyOf: [{ field: 'status', equals: 'Succeeded' }, { field: 'status', equals: 'Failed' }] }
]
var pipelineOperations = [
  { key: 'diagnostic-write', operationName: 'Microsoft.Insights/diagnosticSettings/write' }
  { key: 'diagnostic-delete', operationName: 'Microsoft.Insights/diagnosticSettings/delete' }
  { key: 'export-write', operationName: 'Microsoft.OperationalInsights/workspaces/dataexports/write' }
  { key: 'export-delete', operationName: 'Microsoft.OperationalInsights/workspaces/dataexports/delete' }
]
// サブスクリプション全体の Administrative 通知は、操作名を allOf の直下で限定します。
// 操作ごとに分けることで、成功した変更と失敗した変更の両方を残し、条件の曖昧な広域通知を避けます。
// 公式の Administrative テンプレートと同じ operationName equals の構造を使います。
// https://learn.microsoft.com/azure/azure-monitor/alerts/resource-manager-alerts-activity-log
var pipelineActivityDefinitions = [for operation in pipelineOperations: {
  key: 'audit-pipeline-changed-${operation.key}'
  scopes: [subscription().id]
  conditions: concat(terminalAdministrative, [{ field: 'operationName', equals: operation.operationName }])
}]
var networkOperations = [
  'Microsoft.Network/virtualNetworks/write', 'Microsoft.Network/virtualNetworks/delete'
  'Microsoft.Network/virtualNetworks/subnets/write', 'Microsoft.Network/virtualNetworks/subnets/delete'
  'Microsoft.Network/networkSecurityGroups/write', 'Microsoft.Network/networkSecurityGroups/delete'
  'Microsoft.Network/networkSecurityGroups/securityRules/write', 'Microsoft.Network/networkSecurityGroups/securityRules/delete'
  'Microsoft.Network/routeTables/write', 'Microsoft.Network/routeTables/delete'
  'Microsoft.Network/routeTables/routes/write', 'Microsoft.Network/routeTables/routes/delete'
  'Microsoft.Network/azureFirewalls/write', 'Microsoft.Network/azureFirewalls/delete'
  'Microsoft.Network/firewallPolicies/write', 'Microsoft.Network/firewallPolicies/delete'
  'Microsoft.Network/firewallPolicies/ruleCollectionGroups/write', 'Microsoft.Network/firewallPolicies/ruleCollectionGroups/delete'
]
var activityDefinitions = concat([
  {
    key: 'audit-storage-changed'
    scopes: [storage.collectJapan.id, storage.collectFoundry.id]
    conditions: terminalAdministrative
  }
], pipelineActivityDefinitions, [
  {
    key: 'network-config-changed'
    scopes: networkResourceIds
    conditions: concat(terminalAdministrative, [{ anyOf: map(networkOperations, operation => { field: 'operationName', equals: operation }) }])
  }
  {
    key: 'access-changed'
    scopes: resourceGroupIds
    conditions: concat(terminalAdministrative, [{ anyOf: [
      { field: 'operationName', equals: 'Microsoft.Authorization/roleAssignments/write' }
      { field: 'operationName', equals: 'Microsoft.Authorization/roleAssignments/delete' }
    ] }])
  }
  {
    key: 'service-health'
    scopes: [subscription().id]
    conditions: [{ field: 'category', equals: 'ServiceHealth' }]
  }
  {
    key: 'resource-health'
    scopes: [appGatewayId, apimId, firewallId]
    conditions: [
      { field: 'category', equals: 'ResourceHealth' }
      { anyOf: [
        { field: 'properties.currentHealthStatus', equals: 'Unavailable' }
        { field: 'properties.currentHealthStatus', equals: 'Degraded' }
      ] }
    ]
  }
])

// 指定リソース群の変更・削除等の管理操作を通知します。
resource activityAlerts 'Microsoft.Insights/activityLogAlerts@2020-10-01' = [for definition in activityDefinitions: {
  name: 'ala-${resourceStem}-${definition.key}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    scopes: definition.scopes
    condition: { allOf: definition.conditions }
    actions: { actionGroups: [{ actionGroupId: actionGroup.id }] }
  }
}]

var metricIds = [for (definition, i) in metricDefinitions: metricAlerts[i].id]
var logIds = [for (definition, i) in queryDefinitions: logAlerts[i].id]
var activityIds = [for (definition, i) in activityDefinitions: activityAlerts[i].id]

output alerts object = {
  actionGroupId: actionGroup.id
  metricAlertIds: metricIds
  logAlertIds: logIds
  activityAlertIds: activityIds
  logAlertsEnabled: enableLogAlerts
}

output logAlertPrincipalIds array = [for (definition, i) in queryDefinitions: logAlerts[i].identity.principalId]
