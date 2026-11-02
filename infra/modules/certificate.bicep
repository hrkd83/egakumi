// Linux 管理 VM 上で公開ドメインの TLS 証明書を準備し、Key Vault に保存します。
// DNS 認証の秘密情報と PFX は protectedParameters で渡し、通常の出力には含めません。
// 初回は Run Command、運用中は VM 内の systemd timer が同じ処理を呼び出します。
// 出力する証明書 URI はバージョンを固定せず、更新した証明書を AppGW が取り込めるようにします。

targetScope = 'resourceGroup'

@description('操作対象となる既存 Linux 管理 VM の名前。')
param vmName string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object
@description('親から受け取る検証済みの設定値。このモジュールが担当する項目だけを参照します。')
param config object
@description('DNS-01 認証用のプラグイン引数。API トークンを含むためログや通常出力に出しません。')
@secure()
param dnsPluginArgumentsJson string = ''
@description('外部で準備した PFX の Base64 データ。prepared モードだけで使用します。')
@secure()
param preparedPfxBase64 string = ''
@description('外部 PFX を開くためのパスワード。証明書ファイルと同様に機密扱いです。')
@secure()
param preparedPfxPassword string = ''
@description('AppGW の HTTP 書き換え経路を APIM が見分ける共有値。利用者の認証トークンとは別です。')
@secure()
param l7IngressSecret string = ''
@description('Realtime セッションの所有者を示す署名付きチケットの鍵。利用者へ鍵自体は返しません。')
@secure()
param realtimeTicketKey string = ''
@description('Run Command の再実行を識別する値。試験用の期限や自動撤去時刻ではありません。')
param executionId string = utcNow()

// 起動済み管理 VM を参照し、証明書処理の実行先にします。
resource managementVm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: vmName }
// VM 内で shell → PowerShell の順に初回設定を実行。失敗をデプロイ失敗として返し、証明書なしで入口を開きません。
resource configureCertificate 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: managementVm
  name: 'configure-public-certificate'
  location: location
  tags: union(tags, { certificateConfiguration: executionId })
  properties: {
    // Linux Managed Run Command delivers named protectedParameters through
    // environment values. The shell installs pinned PowerShell; the worker
    // consumes/removes those values without echoing them or using argv.
    source: {
      // shebangの末尾へCRが残るとLinuxで実行できないため、読込元の改行を正規化します。
      script: replace(replace(loadTextContent('../../guest/certificate-bootstrap.sh'), '\r\n', '\n'), '__CERTIFICATE_WORKER_BASE64__', base64(replace(loadTextContent('../../guest/certificate.ps1'), '\r\n', '\n')))
    }
    parameters: [
      { name: 'Action', value: 'Install' }
      { name: 'PublicConfigJson', value: string(config) }
    ]
    protectedParameters: [
      { name: 'DnsPluginArgumentsJson', value: dnsPluginArgumentsJson }
      { name: 'PreparedPfxBase64', value: preparedPfxBase64 }
      { name: 'PreparedPfxPassword', value: preparedPfxPassword }
      { name: 'L7IngressSecret', value: l7IngressSecret }
      { name: 'RealtimeTicketKey', value: realtimeTicketKey }
    ]
    asyncExecution: false
    timeoutInSeconds: 3600
    treatFailureAsDeploymentFailure: true
  }
}

output certificateSecretUri string = 'https://${config.keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${config.certificateName}/'
output maintenanceTaskName string = 'egakumi-certificate-${config.keyVaultName}-${config.certificateName}.timer'
output runCommandResourceId string = configureCertificate.id
