// Key Vault、Foundry と共有プロジェクト、モデル、内部 APIM、API Center を作成します。
// サービス本体の作成と、通信・権限・API 公開は分離しています。公開準備中の API は拒否します。
// Key Vault と Foundry の通常データ通信は後続の Private Endpoint と Azure RBAC を使います。
// APIM の管理処理に必要な Key Vault の trusted services 例外だけを条件付きで許可します。

targetScope = 'resourceGroup'

@description('正式CLIが実状態を照合して生成した、同名Core診断設定の書込properties。利用者入力ではありません。')
param diagnosticSettingProperties object

@description('親モジュールで確定したサービス名の組。別モジュールでも同じ資源を参照するために使います。')
param names object
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('Foundry とモデルの配置リージョン。モデル提供状況と利用枠を事前確認します。')
param foundryLocation string = 'eastus2'
@description('API Center を配置するリージョン。基盤ネットワークとは別の対応リージョンを指定できます。')
param apiCenterLocation string = 'eastus'
@allowed(['Free', 'Standard'])
@description('API Center の料金プラン。Free でも Portal の基本検索・閲覧は利用できます。Standard の費用・APIM連携特典は別途確認します。')
param apiCenterSkuName string = 'Free'
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
@description('APIM の処理ユニット数。SKU と負荷に合わせて指定します。')
param apimCapacity int = 1
@allowed([true])
@description('常設 gRPC 用に APIM の HTTP/2 を有効にする固定指定。無効化できません。')
param enableGrpc bool = true
@description('azd が所有確認した実 APIM 名ごとの公開ドメイン設定。秘密証明書・観測した証明書情報や状態は含めず、利用者の公開 config では指定しません。')
param existingApimHostnameConfigurations object = {}
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
@description('配備するモデル名・版・SKU・利用枠等の一覧。APIM のモデル公開設定と組で検証します。')
param modelDeployments array = []
@description('Foundry 共有プロジェクトの画面上の表示名。物理リソース名とは分けて管理します。')
param projectDisplayName string = 'core-platform'
@description('Foundry 共有プロジェクトの用途説明。モデルや Agent をどの目的で配置するかを示します。')
param projectDescription string = 'Core platform project for shared model, agent, and tool capabilities.'

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

// 通常モデルを順番に配備。版と利用枠を明示して、設定外の自動版上げを防ぎます。
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
    publicNetworkAccess: 'Enabled'
    // 初回の証明書発行は postprovision。次の本体 PUT で省略するとその参照を失うため、
    // owned GET で確認した同じ APIM の公開情報だけを明示して保持します。
    // https://learn.microsoft.com/azure/azure-resource-manager/templates/deployment-modes#incremental-mode
    hostnameConfigurations: existingApimHostnameConfigurations[?names.apim] ?? []
    customProperties: {
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Ssl30': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Ssl30': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'false'
      ...(enableGrpc ? { 'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'true' } : {})
    }
  }
}

// API の発見・参照のためのカタログ本体。API 実行の中継装置ではありません。
resource apiCenter 'Microsoft.ApiCenter/services@2024-03-01' = {
  name: names.apiCenter
  location: apiCenterLocation
  tags: tags
  identity: { type: 'SystemAssigned' }
  // 作成 API は SKU を要求します。現行の Bicep 型には未収録のため当該行だけ抑制します。
  // Azure CLI も同じ API 版で top-level sku.name を明示します。
  // https://github.com/Azure/azure-cli-extensions/blob/main/src/apic-extension/azext_apic_extension/aaz/latest/apic/_create.py
  #disable-next-line BCP187
  sku: { name: apiCenterSkuName }
  properties: {}
}
// カタログ項目を整理する既定 Workspace。APIM の Workspace 機能とは別です。
resource workspace 'Microsoft.ApiCenter/services/workspaces@2024-03-01' = {
  parent: apiCenter
  name: 'default'
  properties: { title: 'Platform APIs', description: 'API definitions and public gateway endpoints.' }
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
// Foundry の管理・監査ログを Foundry 側リージョンの Storage に送ります。
resource foundryAudit 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: foundry
  properties: diagnosticSettingProperties['foundry-logs']
}
// モデルの利用量ログ。入力本文や生成本文を業務ログとして保存する設定ではありません。
resource foundryUsage 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-usage'
  scope: foundry
  properties: diagnosticSettingProperties['foundry-usage']
}
// APIM の要求処理・必要に応じ WebSocket 接続の診断記録を保存します。
resource apimLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-audit'
  scope: apim
  properties: diagnosticSettingProperties['apim-logs']
}
// APIM の性能・可用性の数値を監視先へ送ります。
resource apimMetrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'core-metrics'
  scope: apim
  properties: diagnosticSettingProperties['apim-metrics']
}

output serviceResourceIds object = { keyVault: vault.id, foundry: foundry.id, project: project.id, apim: apim.id, apiCenter: apiCenter.id }
output principalIds object = { foundry: foundry.identity.principalId, project: project.identity.principalId, apim: apim.identity.principalId, apiCenter: apiCenter.identity.principalId }
output foundryEndpoint string = 'https://${foundry.name}.services.ai.azure.com'
output foundryCognitiveEndpoint string = 'https://${foundry.name}.cognitiveservices.azure.com'
output foundryOpenAiEndpoint string = 'https://${foundry.name}.openai.azure.com'
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output keyVaultUri string = vault.properties.vaultUri
output apimGatewayHost string = '${apim.name}.azure-api.net'
output apimPrivateIpAddresses array = apim.properties.privateIPAddresses
output apimPublicIpAddresses array = apim.properties.publicIPAddresses
output modelDeploymentNames array = map(modelDeployments, model => model.name)
