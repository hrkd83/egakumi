// FirewallのログをLAWへ記録し、URLを含まない分類は監査Storageへも直接送ります。
// Firewall 本体や Policy を更新せず、作成済み Firewall へ診断設定だけを追加します。
// 高負荷な Flow Trace / Fat Flow は通常運用の既定では有効にしません。

targetScope = 'resourceGroup'

@description('正式CLIが既存設定を照合して生成する診断設定の書き込みプロパティ。内部入力です。')
param diagnosticSettingProperties object

@description('既存 Firewall の名前。親が作成した実リソース ID から受け取ります。')
param firewallName string
@description('製品名と環境名を連結した共通名。診断設定の名前に使います。')
param resourceStem string

resource firewall 'Microsoft.Network/azureFirewalls@2025-01-01' existing = {
  name: firewallName
}

// カテゴリ名は Azure Firewall の構造化ログ仕様に合わせます。
// PremiumのIDPS署名は変換不要の診断設定で収集します。
// https://learn.microsoft.com/azure/firewall/monitor-firewall-reference#supported-resource-logs-for-microsoftnetworkazurefirewalls
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw'
  properties: diagnosticSettingProperties['fw-logs']
}

// URLを含み得る分類は既存の直送設定から外した後、取り込み変換のあるLAWへ送ります。
resource urlLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw-url-logs'
  properties: diagnosticSettingProperties['fw-url-logs']
  dependsOn: [logs]
}

resource dnsLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw-dns-logs'
  properties: diagnosticSettingProperties['fw-dns-logs']
}

// 数値メトリクスを通信ログと分け、監査 Storage への不要な二重保存を避けます。
resource metrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'diag-${resourceStem}-fw-metrics'
  properties: diagnosticSettingProperties['fw-metrics']
}
