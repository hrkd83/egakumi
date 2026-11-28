// Key Vault、Foundry と共有プロジェクト、モデル、内部 APIM を作成します。
// サービス本体の作成と、通信・権限・API 公開は分離しています。公開準備中の API は拒否します。
// Key Vault と Foundry の通常データ通信は後続の Private Endpoint と Azure RBAC を使います。
// APIM の管理処理に必要な Key Vault の trusted services 例外だけを条件付きで許可します。

targetScope = 'resourceGroup'

@description('正式CLIが実状態を照合して生成した、同名の基盤診断設定の書込properties。')
param diagnosticSettingProperties object

@description('親モジュールで確定したサービス名の組。別モジュールでも同じ資源を参照するために使います。')
param names object
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('Foundry とモデルの配置リージョン。モデル提供状況と利用枠を事前確認します。')
param foundryLocation string = 'eastus2'
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object
@description('Azureリソースが属するテナントのID。Key Vaultのテナント設定に使用します。')
param tenantId string = subscription().tenantId
@description('内部 APIM 専用サブネットの ID。APIM はこの中にプライベート入口を持ちます。')
param apimSubnetId string
@description('APIM の API 提供者として表示する組織名。')
param publisherName string
@description('APIM の提供者連絡先メールアドレス。')
param publisherEmail string
@allowed(['Premium', 'Developer'])
@description('APIM の SKU。Premium は本番構成、Developer は SLA のない検証向け構成です。')
param apimSkuName string = 'Premium'
@minValue(1)
@maxValue(12)
@description('APIM の処理ユニット数。SKU と負荷に合わせて指定します。')
param apimCapacity int = 1
@description('azd が所有確認した実 APIM 名ごとの公開ドメイン設定。秘密証明書・観測した証明書情報や状態は含めず、利用者の公開 config では指定しません。')
param existingApimHostnameConfigurations object = {}
param existingApimCustomProperties object = {}
@allowed(['standard', 'premium'])
@description('Key Vault の SKU。秘密情報・証明書の格納先の機能と料金に影響します。')
param vaultSkuName string = 'standard'
@minValue(7)
@maxValue(90)
@description('削除済み Key Vault の保持日数。Azure の設定範囲は 7〜90 日です。')
param vaultRetentionDays int = 90
@description('Key Vault の完全削除保護を有効にするか。一度有効にすると保持期間中は無効化できません。')
param vaultPurgeProtection bool = true
@description('APIM が証明書等を管理面から取得するため、Key Vault の trusted services 例外を有効にする内部指定。一般の公開アクセスは無効のままです。')
param allowApimKeyVaultControlPlane bool = false
@description('Foundry プロジェクトのテレメトリー送信先。接続設定だけで Agent は作成しません。')
param applicationInsightsResourceId string = ''
@secure()
@description('先に作成した Application Insights の実際の接続文字列。認証用 credential ではなく、Foundry 接続の必須 metadata に渡します。公開パラメーターへ転記しません。')
param applicationInsightsConnectionString string = ''
@description('新規配備するモデル名・版・SKU・容量。正式な初回構築では配備前に確認した最小値を使います。')
param modelDeployments array = []
@description('Foundry 共有プロジェクトの画面上の表示名。物理リソース名とは分けて管理します。')
param projectDisplayName string = 'egakumi'
@description('Foundry 共有プロジェクトの用途説明。モデルや Agent をどの目的で配置するかを示します。')
param projectDescription string = 'Shared project for model, agent, and tool capabilities.'

// 証明書と接続資格情報の格納先。通常のデータ操作は RBAC と PE で保護します。
// APIM の custom hostname 証明書取得は、VNet から到達できても trusted services
// 例外が必要です。PNA Disabled は維持し、認証・対象 KV 内の権限も別途必要です。
// https://learn.microsoft.com/azure/api-management/breaking-changes/trusted-service-connectivity-retirement-march-2026
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: names.keyVault
  location: location
  tags: tags
  properties: union({
    tenantId: tenantId
    sku: { family: 'A', name: vaultSkuName }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: vaultRetentionDays
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: allowApimKeyVaultControlPlane ? 'AzureServices' : 'None', ipRules: [], virtualNetworkRules: [] }
    accessPolicies: []
  }, vaultPurgeProtection ? { enablePurgeProtection: true } : {})
}

// モデルとプロジェクトを保持する Foundry アカウント。ローカル API key 認証を無効にします。
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: names.foundry
  location: foundryLocation
  tags: tags
  kind: 'AIServices'
  identity: { type: 'SystemAssigned' }
  sku: { name: 'S0' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: names.foundry
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'None', ipRules: [], virtualNetworkRules: [] }
    restrictOutboundNetworkAccess: false
    encryption: { keySource: 'Microsoft.CognitiveServices' }
  }
}

// 共有用途の初期プロジェクト。Agent 本体や個別業務のコードはここでは作成しません。
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: names.project
  location: foundryLocation
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: projectDisplayName, description: projectDescription }
}

// 新規モデルを順番に配備。正式手順の再実行では、事前検査で照合した既存配備を入力から除きます。
// 版は明示して、設定外の自動版上げを防ぎます。
@batchSize(1)
resource models 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for model in modelDeployments: {
  parent: foundry
  name: model.name
  sku: { name: model.skuName, capacity: model.capacity }
  properties: {
    model: { format: model.modelFormat, name: model.modelName, version: model.modelVersion }
    versionUpgradeOption: model.versionUpgradeOption
  }
  dependsOn: [project]
}]

// Foundry プロジェクトに監視送信先を接続。AAD という汎用名ではなく、
// AppInsights category が要求する ProjectManagedIdentity を明示します。
// 送信先の ARM ID と接続文字列を metadata に登録します。接続文字列は認証用の
// credential ではなく送信先情報としても必須です。認証には Project MI を使います。
// https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/foundry-autopilot-agent/infra/modules/project.bicep
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (!empty(applicationInsightsResourceId)) {
  parent: project
  name: 'application-insights'
  properties: {
    category: 'AppInsights'
    // Azure の実 API と現行管理 SDK が受け付ける値。2025-06-01 の Bicep 型定義は未追随です。
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: applicationInsightsResourceId
    useWorkspaceManagedIdentity: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: applicationInsightsResourceId
      ApplicationInsightsConnectionString: applicationInsightsConnectionString
    }
    isSharedToAll: false
  }
  dependsOn: [models]
}

// Premium のゾーン冗長化時に内部 APIM の管理面で使う IP。API の入口は AppGW です。
// ゾーン対応リージョンではゾーン指定を省略した Standard IP もゾーン冗長です。
// https://learn.microsoft.com/azure/virtual-network/ip-services/public-ip-addresses#availability-zone
resource apimManagementIp 'Microsoft.Network/publicIPAddresses@2025-01-01' = if (apimSkuName == 'Premium') {
  name: 'pip-${names.apim}-management'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: { domainNameLabel: names.apim }
  }
}

// 内部 VNet モードの APIM。公開 API は AppGW 側から受け、バックエンドには APIM 自身の MI で認証します。
resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: names.apim
  location: location
  tags: tags
  // APIM の証明書・Named Value・モデルアクセスは APIM 自身の ID を使います。
  // KV firewall 有効時の UAMI 非対応を避けます。AppGW の証明書用 UAMI は別に維持します。
  // https://learn.microsoft.com/azure/api-management/api-management-howto-use-managed-service-identity
  identity: { type: 'SystemAssigned' }
  sku: { name: apimSkuName, capacity: apimCapacity }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
    virtualNetworkType: 'Internal'
    virtualNetworkConfiguration: { subnetResourceId: apimSubnetId }
    publicIpAddressId: apimSkuName == 'Premium' ? apimManagementIp!.id : null
    publicNetworkAccess: 'Enabled'
    developerPortalStatus: 'Disabled'
    // 他用途の既存domainはowned GETから保持し、旧gRPC専用domainは後処理で照合して撤去します。
    // https://learn.microsoft.com/azure/azure-resource-manager/templates/deployment-modes#incremental-mode
    hostnameConfigurations: existingApimHostnameConfigurations[?names.apim] ?? []
    customProperties: union({
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Ssl30': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Ssl30': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'false'
    }, existingApimCustomProperties[?names.apim] ?? {}, {
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'false'
    })
  }
}

// Key Vault の監査ログを検索先と同一リージョンの監査保存先に出力します。
resource vaultLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: vault
  properties: diagnosticSettingProperties['vault-logs']
}
// Key Vault の性能値を Workspace に出力。監査操作ログとは別の診断設定です。
resource vaultMetrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-metrics'
  scope: vault
  properties: diagnosticSettingProperties['vault-metrics']
}
// Foundryの管理・推論メタデータをLAWとFoundry側リージョンの監査Storageへ送ります。
resource foundryAudit 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: foundry
  properties: diagnosticSettingProperties['foundry-logs']
}
// モデルの利用量をLAWとFoundry側リージョンの監査Storageへ送ります。
resource foundryUsage 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-usage'
  scope: foundry
  properties: diagnosticSettingProperties['foundry-usage']
}
resource foundryProjectAudit 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: project
  properties: diagnosticSettingProperties['foundry-project-logs']
}
// WebSocket接続は直接保管します。HTTP／LLMの診断はLAWで変換してから保管します。
resource apimWebSocketLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (contains(diagnosticSettingProperties, 'apim-websocket')) {
  name: 'core-audit'
  scope: apim
  properties: diagnosticSettingProperties['apim-websocket']
}
resource apimLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-gateway-logs'
  scope: apim
  properties: diagnosticSettingProperties['apim-logs']
}
// APIM の性能・可用性の数値を監視先へ送ります。
resource apimMetrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-metrics'
  scope: apim
  properties: diagnosticSettingProperties['apim-metrics']
}

output serviceResourceIds object = { keyVault: vault.id, foundry: foundry.id, project: project.id, apim: apim.id }
output principalIds object = { foundry: foundry.identity.principalId, project: project.identity.principalId, apim: apim.identity.principalId }
output foundryEndpoint string = 'https://${foundry.name}.services.ai.azure.com'
output foundryOpenAiEndpoint string = 'https://${foundry.name}.openai.azure.com'
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output apimGatewayHost string = '${apim.name}.azure-api.net'
output apimPrivateIpAddresses array = apim.properties.privateIPAddresses
