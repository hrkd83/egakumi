// 既存管理VMの外部NTP許可を撤去する前に、時刻だけをAzureホストPHCへ先行移行します。
// 通常監査と同じguestロジックを使い、SSH/rsyslog設定や再起動には進みません。
targetScope = 'resourceGroup'

@description('操作対象となる既存 Linux 管理 VM の名前。')
param vmName string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: vmName }

resource hostTime 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: vm
  name: 'configure-host-time'
  location: location
  tags: tags
  properties: {
    // 固定内部モードだけを変更し、同じ時刻設定・復旧・同期検証の実装を共有します。
    source: { script: replace(replace(loadTextContent('../guest/configure-audit.sh'), '\r\n', '\n'), 'readonly core_host_time_only=false', 'readonly core_host_time_only=true') }
    asyncExecution: false
    timeoutInSeconds: 300
    treatFailureAsDeploymentFailure: true
  }
}
