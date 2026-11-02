// 管理 VM の起動後、SSH・時刻同期・認証ログを標準の Linux 設定にそろえます。
// 設定スクリプトは Bicep に埋め込み、Azure Managed Run Command が VM 内で root として実行します。
// NTP の宛先は Firewall の許可先と同じ入力から作り、時刻ずれで TLS や認証が失敗するのを防ぎます。
// 既存 Chrony/PTP を優先し、timesyncd のイメージにも対応します。時刻サービスの置換はしません。

targetScope = 'resourceGroup'
@description('操作対象となる既存 Linux 管理 VM の名前。')
param vmName string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object
@minLength(1)
@description('Ubuntu 管理 VM に追加する外部 NTP 先 FQDN 一覧。既存 PHC/OS 設定は維持し、Firewall の NTP 許可先と一致させます。')
param ntpServers array = ['ntp.ubuntu.com']

// 監査設定を適用する既存管理 VM を参照します。
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: vmName }
// Linux 設定を再適用可能な Run Command として実行。秘密値を含む引数やシェル履歴は収集しません。
resource audit 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: vm
  name: 'configure-linux-audit'
  location: location
  tags: tags
  properties: {
    // Windowsで編集したCRLFも、Linuxへ渡す前にLFへ揃えます。
    source: { script: replace(replace(loadTextContent('../../guest/configure-audit.sh'), '\r\n', '\n'), '__CORE_NTP_SERVERS_BASE64__', base64(string(ntpServers))) }
    asyncExecution: false
    timeoutInSeconds: 300
    treatFailureAsDeploymentFailure: true
  }
}
