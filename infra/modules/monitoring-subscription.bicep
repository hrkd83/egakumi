// サブスクリプションの管理操作履歴を、今回の Log Analytics と監査 Storage へ送ります。
// RG 内の診断設定とは異なる Subscription スコープなので、別モジュールとして明示します。

targetScope = 'subscription'

@description('製品名と環境名を連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string
@description('診断ログと監視クエリの送信先 Workspace のリソース ID。')
param workspaceId string
@description('長期監査ログの保存先 Storage のリソース ID。')
param auditStorageId string
@description('この段階で監査ログの転送も設定するか。保存先と保護が準備できてから有効にします。')
param enableAuditCollection bool = false

var activityCategories = [
  'Administrative', 'ServiceHealth', 'ResourceHealth', 'Alert'
  'Autoscale', 'Security', 'Recommendation', 'Policy'
]
var activityLogs = [for category in activityCategories: { category: category, enabled: true }]

// サブスクリプションの管理操作履歴を共通の検索先と監査保存先へ送ります。
resource activityLog 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${resourceStem}-activity'
  properties: {
    workspaceId: workspaceId
    ...(enableAuditCollection ? { storageAccountId: auditStorageId } : {})
    logs: activityLogs
  }
}

output activityDiagnosticSettingId string = activityLog.id
