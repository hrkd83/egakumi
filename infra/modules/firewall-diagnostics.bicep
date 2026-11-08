// Firewall の通信許可・遮断を、検索用 LAW と既存の監査 Storage へ記録します。
// Firewall 本体や Policy を更新せず、作成済み Firewall へ診断設定だけを追加します。
// 高負荷な Flow Trace / Fat Flow は通常運用の既定では有効にしません。

targetScope = 'resourceGroup'

@description('正式CLIが実状態を照合して生成した、同名Core診断設定の書込properties。利用者入力ではありません。')
param diagnosticSettingProperties object

@description('既存 Firewall の名前。親が作成した実リソース ID から受け取ります。')
param firewallName string
@description('製品名と環境名を連結した共通名。診断設定の名前に使います。')
param resourceStem string

resource firewall 'Microsoft.Network/azureFirewalls@2025-01-01' existing = {
  name: firewallName
}

// カテゴリ名は Azure Firewall の構造化ログ仕様に合わせます。
// IDPS を利用できない Standard では、そのログカテゴリを要求しません。
// https://learn.microsoft.com/azure/firewall/monitor-firewall-reference#supported-resource-logs-for-microsoftnetworkazurefirewalls
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw'
  properties: diagnosticSettingProperties['fw-logs']
}

// 数値メトリクスを通信ログと分け、監査 Storage への不要な二重保存を避けます。
resource metrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw-metrics'
  properties: diagnosticSettingProperties['fw-metrics']
}

output diagnosticSettingIds object = { logs: logs.id, metrics: metrics.id }
