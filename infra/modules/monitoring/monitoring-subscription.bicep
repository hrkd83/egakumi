// サブスクリプションの管理操作履歴を、今回の Log Analytics と監査 Storage へ送ります。
// RG 内の診断設定とは異なる Subscription スコープなので、別モジュールとして明示します。

targetScope = 'subscription'

@description('正式CLIが既存設定を照合して生成する診断設定の書き込みプロパティ。内部入力です。')
param diagnosticSettingProperties object

@description('製品名と環境名を連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string

// サブスクリプションの管理操作履歴を共通の検索先と監査保存先へ送ります。
resource activityLog 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${resourceStem}-activity'
  properties: diagnosticSettingProperties['activity']
}
