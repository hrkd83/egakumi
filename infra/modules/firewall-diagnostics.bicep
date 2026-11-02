// Firewall の通信許可・遮断を、検索用 LAW と既存の監査 Storage へ記録します。
// Firewall 本体や Policy を更新せず、作成済み Firewall へ診断設定だけを追加します。
// 高負荷な Flow Trace / Fat Flow は通常運用の既定では有効にしません。

targetScope = 'resourceGroup'

@description('既存 Firewall の名前。親が作成した実リソース ID から受け取ります。')
param firewallName string
@description('製品名と環境名を連結した共通名。診断設定の名前に使います。')
param resourceStem string
@allowed(['Standard', 'Premium'])
@description('Firewall の実構成と同じ SKU。Premium の場合だけ IDPS ログも収集します。')
param firewallTier string
@description('検索用 Log Analytics Workspace のリソース ID。')
param workspaceId string
@description('Firewall と同じリージョンの既存監査 Storage のリソース ID。')
param auditStorageId string
@description('保護済み監査保存先にもログを送るか。メトリクスは LAW のみに送ります。')
param enableAuditCollection bool = false

resource firewall 'Microsoft.Network/azureFirewalls@2025-01-01' existing = {
  name: firewallName
}

// カテゴリ名は Azure Firewall の構造化ログ仕様に合わせます。
// IDPS を利用できない Standard では、そのログカテゴリを要求しません。
// https://learn.microsoft.com/azure/firewall/monitor-firewall-reference#supported-resource-logs-for-microsoftnetworkazurefirewalls
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw'
  properties: {
    workspaceId: workspaceId
    logAnalyticsDestinationType: 'Dedicated'
    ...(enableAuditCollection ? { storageAccountId: auditStorageId } : {})
    logs: concat([
      { category: 'AZFWNetworkRule', enabled: true }
      { category: 'AZFWApplicationRule', enabled: true }
      { category: 'AZFWThreatIntel', enabled: true }
    ], firewallTier == 'Premium' ? [{ category: 'AZFWIdpsSignature', enabled: true }] : [])
  }
}

// 数値メトリクスを通信ログと分け、監査 Storage への不要な二重保存を避けます。
resource metrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw-metrics'
  properties: {
    workspaceId: workspaceId
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output diagnosticSettingIds object = { logs: logs.id, metrics: metrics.id }
