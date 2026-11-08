// サブスクリプションの管理操作履歴を、今回の Log Analytics と監査 Storage へ送ります。
// RG 内の診断設定とは異なる Subscription スコープなので、別モジュールとして明示します。

targetScope = 'subscription'

@description('正式CLIが実状態を照合して生成した、同名Core診断設定の書込properties。利用者入力ではありません。')
param diagnosticSettingProperties object

@description('製品名と環境名を連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string

// サブスクリプションの管理操作履歴を共通の検索先と監査保存先へ送ります。
resource activityLog 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${resourceStem}-activity'
  properties: diagnosticSettingProperties['activity']
}

output activityDiagnosticSettingId string = activityLog.id
