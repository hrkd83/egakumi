// 基盤全体の組み立て順序を定義する入口です。azd のフックが検証済み設定を渡します。
// 最初に監視用の保存先を準備し、次にネットワーク・サービス・証明書・公開入口をつなぎます。
// Entra のアプリ登録や企業ハブ側の変更はこのファイルでは実行しません。
// 同じ製品名と環境名から同じリソース名を作り、azd down が所有する５個の RG を追跡できるようにします。

targetScope = 'subscription'

@description('params と config を結合・検証した公開設定。秘密情報は別の secure パラメーターから渡します。')
param configuration object
@allowed(['monitoring', 'core'])
@description('監視保存先だけを先に作る monitoring と、残りをつなぐ core の段階。どちらも azd up の中で実行します。')
param phase string = 'core'
@description('管理 VM の SSH 公開鍵。秘密鍵は渡さず、手元で安全に保持します。')
@secure()
param sshPublicKey string = ''
@description('DNS-01 認証用のプラグイン引数。API トークンを含むためログや通常出力に出しません。')
@secure()
param dnsPluginArgumentsJson string = ''
@description('接続プロバイダー名をキーにした OAuth client secret。公開設定から分離して渡します。')
@secure()
param credentialProviderSecrets object = {}
@description('AppGW の HTTP 書き換え経路を APIM が見分ける共有値。利用者の認証トークンとは別です。')
@secure()
param l7IngressSecret string = ''
@description('Realtime セッションの所有者を示す署名付きチケットの鍵。利用者へ鍵自体は返しません。')
@secure()
param realtimeTicketKey string = ''
@description('監査コンテナーの保存保護ポリシーをこの呼出しで管理するか。既存の Locked 保護を解除する指定ではありません。')
param manageAuditImmutabilityPolicies bool = true
@description('実状態を照合した既存 Locked 監査ポリシーの ID。追加コンテナーの配備時も既存の保持を変更しません。内部入力です。')
param retainedAuditImmutabilityPolicyIds array = []
@description('監視保存先だけを更新するときも、既に稼働中の監査出力を維持するか。')
param preserveAuditCollection bool = false
@description('azd が実在と所有を確認した NSG 名。初回は空で、再 up 時の親 NSG の再 PUT を避けます。利用者向けの設定項目ではありません。')
param existingNetworkSecurityGroupNames array = []
@description('azd が同じ環境の実ルート表から確認した既存経路。再 up 時も後続段階の経路を保ち、公開 config では指定しません。')
param existingRouteTableRoutes object = {}
@description('azd が同じ環境の APIM から確認した、実 APIM 名ごとの公開ドメイン・証明書参照。再 up で postprovision の設定を消さないための内部入力です。')
param existingApimHostnameConfigurations object = {}
@description('azd が対象 CA の配置先を照合して確認した実在状態。初回だけ初期イメージで作成し、既存アプリの更新はアプリ配備工程が担当します。内部入力です。')
param existingPortalApp bool = false
@description('監視先から読み取った取り込み変換DCRの実ID。再配備時の関連付けを保持する内部入力です。')
param existingWorkspaceDataCollectionRuleResourceId string = ''
@description('正式CLIが所有・既存宛先・分類・転送範囲を照合して生成するCore診断properties。内部入力です。')
param diagnosticSettingProperties object

// 名前は「種類-環境名-製品名」を基本に１か所で組み立てます。
// 値の切り捨てやランダムな接尾辞は付けません。世界で一意な名前が競合するときは、
// 環境名・製品名を見直し、Entra の登録予定 URI も構築前に同じ入力から計算します。
var product = configuration.productName
var environmentName = configuration.environmentName
var stem = '${environmentName}-${product}'
var location = configuration.location
var tags = union(configuration.tags, { product: product, environment: environmentName, 'azd-env-name': environmentName })
var groupKinds = ['id', 'nw', 'monitor', 'svc', 'maint']
var deployCore = phase == 'core'
var servicesConfig = configuration.services
var managementConfig = configuration.management
var monitoringConfig = configuration.monitoring
var portalConfig = configuration.portal
var gatewayConfig = configuration.gateway
var grpcConfig = gatewayConfig.?grpc ?? { enabled: false, publicPort: 8443, publicHostName: gatewayConfig.publicHostName, backendPort: 443 }
var grpcEnabled = grpcConfig.enabled
// 公開API配列は変更せず、基盤に必要な経路・本文形式だけを参照します。
var infrastructureApis = configuration.?infrastructureApiDefinitions ?? configuration.apiDefinitions
var realtimeEnabled = (gatewayConfig.?realtimeControlEnabled ?? false) || length(filter(configuration.apiDefinitions, api => length(filter(items(api.authorization.operationConstraints), entry => contains(['realtimeCreate', 'realtimeHangup'], entry.value.kind))) > 0)) > 0
var useIngressSecrets = grpcEnabled || realtimeEnabled
var certConfig = configuration.certificate
var authorization = configuration.authorization
// SSML/WAV は正規の API 契約で許可した POST の完全パスだけ Content-Type を調整します。
// 任意パス・ワイルドカードへ広げず、企業ごとの API prefix に追随します。
var customMimePaths = flatten(map(infrastructureApis, api => map(filter(api.operations, operation => operation.method == 'POST' && (contains(api.authorization.operationConstraints[operation.name].?contentTypes ?? [], 'application/ssml+xml') || contains(api.authorization.operationConstraints[operation.name].?contentTypes ?? [], 'audio/wav')) && !contains(operation.urlTemplate, '{') && !contains(operation.urlTemplate, '?') && !contains(operation.urlTemplate, '*') && !contains(api.path, '*') && !contains(api.path, '{')), operation => '/${api.path}${operation.urlTemplate}')))
// Pythonの文書受付と同じ固定POST/binary/1MiB契約だけに、専用WAFを維持します。
var documentUploadPaths = flatten(map(filter(infrastructureApis, api => api.path == 'tools/documents/v1'), api => map(filter(api.operations, operation => operation.name == 'analyze_document' && operation.method == 'POST' && operation.urlTemplate == '/analyze' && api.authorization.operationConstraints[operation.name].kind == 'documentAnalysis' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'binary' && (api.authorization.operationConstraints[operation.name].?maxRequestBytes ?? 0) == 1048576), operation => '/${api.path}${operation.urlTemplate}')))
// Raw本文の調整はAzure Speech正本のtts/stt契約だけ。その他のSSML APIへ広げません。
var speechRawBodyPaths = flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'azure-speech-v1' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), api => map(filter(api.operations, operation => operation.method == 'POST' && api.authorization.operationConstraints[operation.name].kind == 'registeredOperation' && length(api.authorization.operationConstraints[operation.name].?contentTypes ?? []) == 1 && ((operation.name == 'tts' && operation.urlTemplate == '/synthesize' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'text' && contains(api.authorization.operationConstraints[operation.name].contentTypes, 'application/ssml+xml')) || (operation.name == 'stt' && operation.urlTemplate == '/recognize' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'binary' && contains(api.authorization.operationConstraints[operation.name].contentTypes, 'audio/wav')))), operation => '/${api.path}${operation.urlTemplate}')))
// native MCP の従来 SSE は、最初の応答で案内された別の POST 先へ session_id を送ります。
// バックエンド側の案内を変更せず、登録済み message パスへ APIM の公開 prefix を付けます。
// WAF の session_id 調整も同じ正確な公開パスだけに限定し、他の API へ広げません。
var legacyMcpMessagePaths = map(filter(infrastructureApis, api => (api.?apiType ?? 'http') == 'mcp' && (api.?mcpProxy.?transport ?? '') == 'sse' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), api => '/${api.path}${first(filter(api.mcpProxy.endpoints, endpoint => endpoint.name == 'message')).uriTemplate}')
// 公開サンプルの vision/pdf 操作は、インライン画像/PDFを運ぶ明示的な契約です。
// 名前だけでなく種類・HTTP方式・正規URLも照合し、他のChat/Responsesへ除外を広げません。
// 画像/PDFの内容をAPIMで検査済みとは扱わず、下流モデルの入力検査も必要です。
var inlineImagePaths = flatten(map(infrastructureApis, api => map(filter(api.operations, operation => (operation.name == 'vision' || length(filter(api.authorization.operationConstraints[operation.name].?profiles ?? [], profile => contains(profile.modalities, 'image'))) > 0) && operation.method == 'POST' && operation.urlTemplate == '/chat/completions' && api.authorization.operationConstraints[operation.name].kind == 'bodyModel' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? 'json') == 'json' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), operation => '/${api.path}${operation.urlTemplate}')))
var inlinePdfPaths = flatten(map(infrastructureApis, api => map(filter(api.operations, operation => (operation.name == 'pdf' || length(filter(api.authorization.operationConstraints[operation.name].?profiles ?? [], profile => contains(profile.modalities, 'file'))) > 0) && operation.method == 'POST' && operation.urlTemplate == '/responses' && api.authorization.operationConstraints[operation.name].kind == 'statelessResponses' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? 'json') == 'json' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), operation => '/${api.path}${operation.urlTemplate}')))
// Audio Chatの標準Base64欄の調整も、明示された正規POST/JSON契約からだけ導出します。
var inlineAudioPaths = flatten(map(infrastructureApis, api => map(filter(api.operations, operation => (operation.name == 'audio-chat' || length(filter(api.authorization.operationConstraints[operation.name].?profiles ?? [], profile => contains(profile.modalities, 'audio'))) > 0) && operation.method == 'POST' && operation.urlTemplate == '/chat/completions' && api.authorization.operationConstraints[operation.name].kind == 'bodyModel' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? 'json') == 'json' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), operation => '/${api.path}${operation.urlTemplate}')))
var serviceNames = {
  keyVault: 'kv-${stem}'
  foundry: 'aif-${stem}'
  project: 'proj-${stem}'
  apim: 'apim-${stem}-${configuration.services.apimInstanceId}'
  apiCenter: 'apic-${stem}'
}

// Every phase declares the same five groups for native azd down. Keep their
// symbols explicit: module-output references through an indexed RG collection
// can compile to copyIndex() outside an ARM resource loop.
// 共通 ID の RG。証明書参照用マネージド ID をまとめます。
resource idGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${stem}-id'
  location: location
  tags: union(tags, { layer: 'id' })
}
// VNet・Firewall・AppGW・DNS・PE をまとめるネットワーク RG。
resource nwGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${stem}-nw'
  location: location
  tags: union(tags, { layer: 'nw' })
}
// 監視・監査保存用 RG。保護済み保存先は後続の構築より先に準備します。
resource monitorGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${stem}-monitor'
  location: location
  tags: union(tags, { layer: 'monitor' })
}
// APIM・Foundry・Key Vault・API Center・PortalのACR/CAE/CAをまとめるサービスRG。
resource svcGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${stem}-svc'
  location: location
  tags: union(tags, { layer: 'svc' })
}
// Linux 管理 VM と VM 内の保守処理をまとめる RG。実行層 VMSS は作りません。
resource maintGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${stem}-maint'
  location: location
  tags: union(tags, { layer: 'maint' })
}

// 証明書参照用 ID を作成。Entra のアプリ登録とは独立して Azure 内で完結します。
module identity 'modules/identity.bicep' = {
  name: '${stem}-identity'
  scope: idGroup
  params: { resourceStem: stem, location: location, tags: tags }
}

// 構築中の記録も保存できるよう、監視保存先と収集定義を最初に用意します。
module monitoring 'modules/monitoring.bicep' = {
  name: '${stem}-monitoring'
  scope: monitorGroup
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    productName: product
    environmentName: environmentName
    resourceStem: stem
    location: location
    foundryLocation: configuration.foundryLocation
    tags: tags
    retentionInDays: monitoringConfig.retentionInDays
    workspaceDailyQuotaGb: monitoringConfig.workspaceDailyQuotaGb
    auditStorageSku: monitoringConfig.auditStorageSku
    auditAllowedIpAddresses: monitoringConfig.auditAllowedIpAddresses
    auditImmutabilityMode: monitoringConfig.auditImmutabilityMode
    auditRetentionDays: monitoringConfig.auditRetentionDays
    manageAuditImmutabilityPolicies: manageAuditImmutabilityPolicies
    retainedAuditImmutabilityPolicyIds: retainedAuditImmutabilityPolicyIds
    enableAuditCollection: deployCore || preserveAuditCollection
    existingWorkspaceDataCollectionRuleResourceId: existingWorkspaceDataCollectionRuleResourceId
  }
}


// VNet と固定規則を作成し、APIM と管理 VM の初回起動前に外向き経路を用意します。
module network 'modules/network.bicep' = if (deployCore) {
  name: '${stem}-network'
  scope: nwGroup
  // The local loader validates product/environment length before ARM submission.
  params: {
    #disable-next-line BCP334
    resourcePrefix: stem
    location: location
    tags: tags
    config: configuration.network
    existingNetworkSecurityGroupNames: existingNetworkSecurityGroupNames
    existingRouteTableRoutes: existingRouteTableRoutes
  }
}



// FW 通過の証跡は、サービス構築の完了を待たず、FW と保護済み保存先が揃ったら収集します。
module firewallDiagnostics 'modules/firewall-diagnostics.bicep' = if (deployCore) {
  name: '${stem}-firewall-diagnostics'
  scope: nwGroup
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    firewallName: last(split(network!.outputs.firewallId, '/'))
    resourceStem: stem
  }
  dependsOn: [monitoring]
}

// 固定ネットワークの完成後にサービス本体を作成。実 IP の確定後に追加規則を設定します。
module services 'modules/services.bicep' = if (deployCore) {
  name: '${stem}-services'
  scope: svcGroup
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    names: serviceNames
    location: location
    foundryLocation: configuration.foundryLocation
    apiCenterLocation: configuration.apiCenterLocation
    apiCenterSkuName: servicesConfig.?apiCenterSkuName ?? 'Free'
    tags: tags
    tenantId: configuration.azureTenantId
    apimSubnetId: network!.outputs.subnetIds.apim
    publisherName: servicesConfig.publisherName
    publisherEmail: servicesConfig.publisherEmail
    apimSkuName: servicesConfig.apimSkuName
    apimCapacity: servicesConfig.apimCapacity
    enableGrpc: grpcEnabled
    existingApimHostnameConfigurations: existingApimHostnameConfigurations
    vaultSkuName: servicesConfig.vaultSkuName
    vaultRetentionDays: servicesConfig.vaultRetentionDays
    vaultPurgeProtection: servicesConfig.vaultPurgeProtection
    // APIM の証明書等の管理処理だけに必要な trusted services 例外。
    // KV の一般公開は無効を維持し、通常のデータ通信は PE を使います。
    allowApimKeyVaultControlPlane: servicesConfig.grantApimSecretAccess || useIngressSecrets || !empty(configuration.credentialProfiles)
    applicationInsightsResourceId: monitoring.outputs.monitoring.appInsightsId
    applicationInsightsConnectionString: monitoring.outputs.monitoring.appInsightsConnectionString
    modelDeployments: servicesConfig.modelDeployments
    projectDisplayName: servicesConfig.projectDisplayName
    projectDescription: servicesConfig.projectDescription
  }
}

// サービス作成後に Private Endpoint と DNS の対応付けを作ります。
module endpoints 'modules/services-private-endpoints.bicep' = if (deployCore) {
  name: '${stem}-private-endpoints'
  scope: nwGroup
  params: {
    resourcePrefix: stem
    location: location
    tags: tags
    privateEndpointSubnetId: network!.outputs.subnetIds.pep
    privateDnsZoneIds: network!.outputs.privateDnsZoneIds
    serviceResourceIds: services!.outputs.serviceResourceIds
  }
}

// プライベートな管理 VM を作成。初期 SSH 公開鍵と監視エージェントを設定します。
module management 'modules/management.bicep' = if (deployCore) {
  name: '${stem}-management'
  scope: maintGroup
  params: {
    productName: product
    environmentName: environmentName
    resourceStem: stem
    location: location
    tags: tags
    subnetId: network!.outputs.subnetIds.management
    linuxDataCollectionRuleId: monitoring.outputs.monitoring.linuxDataCollectionRuleId
    vmSize: managementConfig.vmSize
    imageReference: managementConfig.imageReference
    diskControllerType: managementConfig.diskControllerType
    osDiskSizeGB: managementConfig.osDiskSizeGB
    osDiskSku: managementConfig.osDiskSku
    enableAcceleratedNetworking: managementConfig.enableAcceleratedNetworking
    encryptionAtHost: managementConfig.encryptionAtHost
    adminUsername: managementConfig.adminUsername
    sshPublicKey: sshPublicKey
    loginAssignments: managementConfig.loginAssignments
    dnsManagedIdentityResourceId: certConfig.mode == 'dnsPlugin' && certConfig.?plugin == 'Azure' ? certConfig.azureDnsManagedIdentityResourceId : ''
    patchAssessmentMode: managementConfig.patchAssessmentMode
    patchMode: managementConfig.patchMode
  }
}

// 実 IP を使って NSG・UDR を仕上げ、AppGW → Firewall → APIM と PE 接続を許可します。
module networkBindings 'modules/network-bindings.bicep' = if (deployCore) {
  name: '${stem}-network-bindings'
  scope: nwGroup
  params: {
    resourcePrefix: stem
    tags: tags
    config: configuration.network
    vnetId: network!.outputs.vnetId
    subnetPrefixes: network!.outputs.subnetPrefixes
    nsgNames: network!.outputs.nsgNames
    routeTableNames: network!.outputs.routeTableNames
    firewallPolicyName: network!.outputs.firewallPolicyName
    firewallPrivateIp: network!.outputs.firewallPrivateIp
    firewallPublicIpAddress: network!.outputs.firewallPublicIpAddress
    apimPrivateIp: services!.outputs.apimPrivateIpAddresses[0]
    apimGatewayFqdn: services!.outputs.apimGatewayHost
    foundryPrivateIps: endpoints!.outputs.foundryPrivateIps
    keyVaultPrivateIps: endpoints!.outputs.keyVaultPrivateIps
    managementPrivateIp: management!.outputs.managementVm.privateIp
    grpcEnabled: grpcEnabled
    grpcPublicPort: grpcConfig.publicPort
    grpcBackendPort: grpcConfig.backendPort
  }
}

// 内部通信の準備後に、サービス ID へ用途別 Azure RBAC を付与します。
module serviceBindings 'modules/service-bindings.bicep' = if (deployCore) {
  name: '${stem}-service-bindings'
  scope: svcGroup
  params: {
    names: serviceNames
    principalIds: services!.outputs.principalIds
    certificateIdentityPrincipalId: identity.outputs.principalId
    managementPrincipalId: management!.outputs.managementVm.principalId
    // gRPC/Realtime の KV 参照にも APIM 自身の ID が必要です。KV 範囲の既存組込ロールだけを使います。
    grantApimSecretAccess: servicesConfig.grantApimSecretAccess || useIngressSecrets || !empty(configuration.credentialProfiles)
    speechEnabled: servicesConfig.?speechEnabled ?? false
    grantApimFoundryModelsAccess: servicesConfig.?grantApimFoundryModelsAccess ?? false
    projectAdministrators: authorization.projectAdministrators
    projectDevelopers: authorization.projectDevelopers
    foundryReaders: authorization.foundryReaders
    catalogReaders: authorization.catalogReaders
    enableApiCenterIntegration: servicesConfig.enableApiCenterIntegration
    catalogEnvironmentKind: servicesConfig.catalogEnvironmentKind
    catalogLifecycleStage: servicesConfig.catalogLifecycleStage
  }
  dependsOn: [networkBindings]
}

// 共通SQLサーバーとスキーマ管理IDだけをCoreが所有します。
// Gatewayの接続台帳DB・実行ID・CM定義はアプリ配備工程で作成します。
module credentialIdentities 'modules/shared-sql-identity.bicep' = if (deployCore) {
  name: '${stem}-credential-identities'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}
module credentialSql 'modules/shared-sql.bicep' = if (deployCore) {
  name: '${stem}-credential-sql'
  scope: svcGroup
  params: { stem: stem, location: location, tags: tags, tenantId: configuration.azureTenantId, schemaIdentity: credentialIdentities!.outputs.schema }
}
module sharedEntraSecrets 'modules/shared-entra-secrets.bicep' = if (deployCore && !empty(configuration.credentialProfiles)) {
  name: '${stem}-shared-entra-secrets'
  scope: svcGroup
  params: { vaultName: serviceNames.keyVault, profiles: configuration.credentialProfiles, providerSecrets: credentialProviderSecrets }
  dependsOn: [services]
}

// 管理 VM の時刻同期と認証ログを設定。証明書処理を始める前に監査の準備を完了します。
module auditConfiguration 'modules/management-audit.bicep' = if (deployCore) {
  name: '${stem}-management-audit'
  scope: maintGroup
  params: { vmName: management!.outputs.managementVm.name, location: location, tags: tags, ntpServers: configuration.network.ntpFqdns }
  dependsOn: [networkBindings]
}

// DNS-01 認証で証明書を取得し Key Vault へ登録。必要な通信と権限の準備が終わってから動かします。
module certificate 'modules/certificate.bicep' = if (deployCore) {
  name: '${stem}-certificate'
  scope: maintGroup
  params: {
    vmName: management!.outputs.managementVm.name
    location: location
    tags: tags
    config: {
      mode: certConfig.mode
      hostname: gatewayConfig.publicHostName
      hostnames: certConfig.domainNames
      requiredHostnames: [gatewayConfig.publicHostName, portalConfig.publicHostName]
      keyVaultName: serviceNames.keyVault
      certificateName: certConfig.certificateName
      accountEmail: certConfig.?contactEmail ?? ''
      dnsPlugin: certConfig.?plugin ?? ''
      azureDnsSubscriptionId: certConfig.?azureDnsSubscriptionId ?? ''
      azureDnsManagedIdentityResourceId: certConfig.?azureDnsManagedIdentityResourceId ?? ''
      moduleVersion: certConfig.moduleVersion
      directoryUrl: certConfig.directory == 'staging' ? 'https://acme-staging-v02.api.letsencrypt.org/directory' : 'https://acme-v02.api.letsencrypt.org/directory'
      renewBeforeDays: certConfig.renewBeforeDays
      renewIntervalHours: certConfig.checkEveryHours
      dnsSleepSeconds: certConfig.?dnsSleepSeconds ?? 60
      validationTimeoutSeconds: certConfig.?validationTimeoutSeconds ?? 120
      minimumValidDays: certConfig.?minimumValidDays ?? 7
      l7IngressEnabled: grpcEnabled
      realtimeTicketEnabled: realtimeEnabled
    }
    dnsPluginArgumentsJson: dnsPluginArgumentsJson
    l7IngressSecret: l7IngressSecret
    realtimeTicketKey: realtimeTicketKey
  }
  dependsOn: [networkBindings, serviceBindings, auditConfiguration]
}

// Portal用IDはID用RG、レジストリと実行基盤はサービスRGに配置します。
module portalPullIdentity 'modules/portal-identity.bicep' = if (deployCore) {
  name: '${stem}-portal-identity'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}

module portalRegistry 'modules/portal-registry.bicep' = if (deployCore) {
  name: '${stem}-portal-registry'
  scope: svcGroup
  params: {
    stem: stem
    location: location
    tags: tags
    pullIdentityId: portalPullIdentity!.outputs.id
    pullIdentityPrincipalId: portalPullIdentity!.outputs.principalId
  }
}

// PE・Private DNS・NSG・Firewall規則を準備してから、CAEを起動します。
module portalNetwork 'modules/portal-network.bicep' = if (deployCore) {
  name: '${stem}-portal-network'
  scope: nwGroup
  dependsOn: [networkBindings]
  params: {
    stem: stem
    location: location
    tags: tags
    registryId: portalRegistry!.outputs.id
    vnetId: network!.outputs.vnetId
    pepSubnetId: network!.outputs.subnetIds.pep
    managementPrefix: network!.outputs.subnetPrefixes.management
    managementNsgName: network!.outputs.nsgNames.management
    acaPrefix: network!.outputs.subnetPrefixes.aca
    agwPrefix: network!.outputs.subnetPrefixes.agw
    firewallPolicyName: network!.outputs.firewallPolicyName
    pepNsgName: network!.outputs.nsgNames.pep
  }
}

module portal 'modules/portal-platform.bicep' = if (deployCore) {
  name: '${stem}-portal'
  scope: svcGroup
  dependsOn: [monitoring, portalNetwork]
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    existingPortalApp: existingPortalApp
    stem: stem
    location: location
    tags: tags
    subnetId: network!.outputs.subnetIds.aca
    registryName: portalRegistry!.outputs.name
    pullIdentityId: portalPullIdentity!.outputs.id
  }
}

module portalNetworkBindings 'modules/portal-network-bindings.bicep' = if (deployCore) {
  name: '${stem}-portal-network-bindings'
  scope: nwGroup
  params: {
    stem: stem
    defaultDomain: portal!.outputs.defaultDomain
    staticIp: portal!.outputs.staticIp
    vnetId: network!.outputs.vnetId
    agwRouteTableName: network!.outputs.routeTableNames.agw
    firewallIp: network!.outputs.firewallPrivateIp
  }
}

module credentialNetwork 'modules/credential-broker-network.bicep' = if (deployCore) {
  name: '${stem}-credential-network'
  scope: nwGroup
  params: {
    stem: stem
    location: location
    tags: tags
    sqlServerId: credentialSql!.outputs.id
    keyVaultPrivateIps: endpoints!.outputs.keyVaultPrivateIps
    vnetId: network!.outputs.vnetId
    pepSubnetId: network!.outputs.subnetIds.pep
    pepNsgName: network!.outputs.nsgNames.pep
    acaPrefix: network!.outputs.subnetPrefixes.aca
    apimPrefix: network!.outputs.subnetPrefixes.apim
    apimNsgName: network!.outputs.nsgNames.apim
    acaPrivateIp: portal!.outputs.staticIp
    firewallPolicyName: network!.outputs.firewallPolicyName
    managementPrefix: network!.outputs.subnetPrefixes.management
    enableCredentialBroker: !empty(configuration.credentialProfiles)
  }
  dependsOn: [networkBindings]
}
// 証明書とネットワーク側の経路が揃ったら、公開入口を構築します。
module gateway 'modules/application-gateway.bicep' = if (deployCore) {
  name: '${stem}-gateway'
  scope: nwGroup
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    productName: product
    environmentName: environmentName
    resourceStem: stem
    location: location
    tags: tags
    subnetId: network!.outputs.subnetIds.agw
    publicIpId: network!.outputs.appGatewayPublicIpId
    publicIpAddress: network!.outputs.appGatewayPublicIpAddress
    apimPrivateIp: services!.outputs.apimPrivateIpAddresses[0]
    portalHostName: portalConfig.publicHostName
    portalBackendIp: portal!.outputs.staticIp
    portalBackendHost: portal!.outputs.appHost
    apimHostName: services!.outputs.apimGatewayHost
    publicHostName: gatewayConfig.publicHostName
    keyVaultCertificateSecretId: certificate!.outputs.certificateSecretUri
    certificateIdentityId: identity.outputs.id
    skuName: gatewayConfig.skuName
    autoscaleMinCapacity: gatewayConfig.autoscaleMinCapacity
    autoscaleMaxCapacity: gatewayConfig.autoscaleMaxCapacity
    zones: gatewayConfig.zones
    existingWafPolicyId: gatewayConfig.?existingWafPolicyId ?? ''
    wafMode: gatewayConfig.wafMode
    wafMaxRequestBodySizeInKb: gatewayConfig.?wafMaxRequestBodySizeInKb ?? 128
    wafRequestBodyInspectLimitInKb: gatewayConfig.?wafRequestBodyInspectLimitInKb ?? 128
    wafFileUploadLimitInMb: gatewayConfig.?wafFileUploadLimitInMb ?? 100
    wafCustomRules: gatewayConfig.?wafCustomRules ?? []
    wafExclusions: gatewayConfig.?wafExclusions ?? []
    publishedApiPaths: concat(flatten(map(infrastructureApis, api => flatten(map(filter(api.operations, operation => !contains(operation.urlTemplate, '*')), operation => contains(operation.urlTemplate, '{deployment}') ? map(api.authorization.operationConstraints[operation.name].?allowedModels ?? [], model => '/${api.path}${replace(operation.urlTemplate, '{deployment}', model)}') : (!contains(operation.urlTemplate, '{') ? ['/${api.path}${operation.urlTemplate}'] : []))))), map(filter(infrastructureApis, api => contains(api, 'mcp')), api => '/${api.mcp.path}/mcp'))
    customMimePaths: customMimePaths
    documentUploadPaths: documentUploadPaths
    speechRawBodyPaths: speechRawBodyPaths
    legacyMcpMessagePaths: legacyMcpMessagePaths
    inlineImagePaths: inlineImagePaths
    inlinePdfPaths: inlinePdfPaths
    inlineAudioPaths: inlineAudioPaths
    backendRequestTimeoutSeconds: gatewayConfig.backendRequestTimeoutSeconds
    grpcEnabled: grpcEnabled
    grpcPublicPort: grpcConfig.publicPort
    grpcBackendPort: grpcConfig.backendPort
    l7IngressSecret: l7IngressSecret
  }
  dependsOn: [monitoring, portalNetworkBindings]
}

// 各リソースが存在してからアラート・通知・監査閲覧権限を接続します。
module monitoringBindings 'modules/monitoring-bindings.bicep' = if (deployCore) {
  name: '${stem}-monitoring-bindings'
  scope: monitorGroup
  params: {
    resourceStem: stem
    location: location
    tags: tags
    monitoring: monitoring.outputs.monitoring
    storage: monitoring.outputs.storage
    appGatewayId: gateway!.outputs.applicationGateway.id
    apimId: services!.outputs.serviceResourceIds.apim
    firewallId: network!.outputs.firewallId
    networkResourceIds: network!.outputs.monitoredResourceIds
    resourceGroupIds: [idGroup.id, nwGroup.id, monitorGroup.id, svcGroup.id, maintGroup.id]
    operatorEmailAddresses: monitoringConfig.operatorEmailAddresses
    enableLogAlerts: monitoringConfig.enableLogAlerts
    apimSkuName: servicesConfig.apimSkuName
    appInsightsIngestionWarningGb: monitoringConfig.appInsightsIngestionWarningGb
    telemetryPublisherPrincipalIds: [services!.outputs.principalIds.project]
    auditReaders: monitoringConfig.auditReaders
  }
}

// API・認証ポリシー・名前付きの値・CM・BrokerはGateway配備工程が所有します。

// RG の外にある管理操作履歴も、今回の監査保存先へ接続します。
module subscriptionMonitoring 'modules/monitoring-subscription.bicep' = if (deployCore) {
  name: '${stem}-subscription-monitoring'
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    resourceStem: stem
  }
  dependsOn: [monitoring]
}

// 以降は後続スクリプト用の参照情報です。秘密値や PFX は出力せず、名前・ID・URI と公開メタデータだけを返します。
output CORE_DEPLOYMENT_NAME string = deployment().name
output resourceGroups object = toObject(groupKinds, kind => kind, kind => 'rg-${stem}-${kind}')
output certificateIdentity object = { id: identity.outputs.id, principalId: identity.outputs.principalId, clientId: identity.outputs.clientId }
output names object = serviceNames
output monitoring object = monitoring.outputs.monitoring
output auditStorage object = monitoring.outputs.storage
output services object = deployCore ? services!.outputs.serviceResourceIds : {}
output endpoints object = deployCore ? { foundry: services!.outputs.foundryEndpoint, project: services!.outputs.projectEndpoint, public: gateway!.outputs.applicationGateway.publicUrl } : {}
output managementVm object = deployCore ? management!.outputs.managementVm : {}
output AZURE_LOCATION string = location
output gateway object = deployCore ? gateway!.outputs.applicationGateway : {}
// Applied by postprovision only after the certificate exists, avoiding an
// APIM creation -> private bindings -> VM certificate bootstrap dependency cycle.
output grpc object = deployCore && grpcEnabled ? {
  enabled: true
  publicUrl: 'https://${grpcConfig.publicHostName}:${grpcConfig.publicPort}'
  publicHostName: grpcConfig.publicHostName
  apimId: services!.outputs.serviceResourceIds.apim
  certificateSecretUri: certificate!.outputs.certificateSecretUri
  namedValueId: '${services!.outputs.serviceResourceIds.apim}/namedValues/core-l7-ingress-secret'
  edgeProofTrustBoundary: 'AppGW control-plane configuration readers are trusted; WAF is not applied to the TCP listener.'
} : { enabled: false }
output ingressSecrets object = deployCore && useIngressSecrets ? {
  enabled: true
  grpcEnabled: grpcEnabled
  realtimeEnabled: realtimeEnabled
  apimId: services!.outputs.serviceResourceIds.apim
} : { enabled: false }
// Expected IDs are available even if the subsequent core deployment fails.
// Cleanup verifies the actual resources at these exact IDs before acting.
// down は以下の今回分の ID を照合してから後処理します。名前が似た別環境を列挙削除するための出力ではありません。
output cleanup object = {
  activityDiagnosticSettingId: subscriptionResourceId('Microsoft.Insights/diagnosticSettings', 'diag-${stem}-activity')
  keyVaultName: serviceNames.keyVault
  foundryName: serviceNames.foundry
  apimName: serviceNames.apim
  serviceResourceGroup: svcGroup.name
  foundryLocation: configuration.foundryLocation
  location: location
  serviceResourceIds: {
    keyVault: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.KeyVault/vaults', serviceNames.keyVault)
    foundry: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.CognitiveServices/accounts', serviceNames.foundry)
    apim: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.ApiManagement/service', serviceNames.apim)
  }
}

output portal object = deployCore ? union(portal!.outputs.platform, {
  registryName: portalRegistry!.outputs.name
  registryId: portalRegistry!.outputs.id
  registryServer: portalRegistry!.outputs.server
  registryResourceGroup: svcGroup.name
  pullIdentityId: portalPullIdentity!.outputs.id
  pullIdentityResourceGroup: idGroup.name
  networkResourceGroup: nwGroup.name
  registryPrivateEndpointId: portalNetwork!.outputs.registryPrivateEndpointId
  registryPrivateIps: portalNetwork!.outputs.registryPrivateIps
  expectedEgressIp: network!.outputs.firewallPublicIpAddress
  publicUrl: 'https://${portalConfig.publicHostName}'
  // Portal専用DB・秘密参照・アプリ更新はapps/portal/deployの別工程が所有します。
  runtimeIdentity: portalPullIdentity!.outputs.identity
  schemaIdentity: credentialIdentities!.outputs.schema
  sharedSqlServer: credentialSql!.outputs.host
  sharedSqlServerId: credentialSql!.outputs.id
  sharedSqlPrivateEndpointId: credentialNetwork!.outputs.endpointId
}) : {}

// Gateway配備が参照する共通基盤の公開メタデータ。アプリ固有リソースや秘密値は含めません。
output gatewayFoundation object = deployCore ? {
  resourceGroup: svcGroup.name
  identityResourceGroup: idGroup.name
  apimName: serviceNames.apim
  apimId: services!.outputs.serviceResourceIds.apim
  apimPrincipalId: services!.outputs.principalIds.apim
  schemaIdentity: credentialIdentities!.outputs.schema
  sqlServer: credentialSql!.outputs.host
  sqlServerId: credentialSql!.outputs.id
  sqlPrivateEndpointId: credentialNetwork!.outputs.endpointId
  environmentName: portal!.outputs.platform.environmentName
  environmentDomain: portal!.outputs.defaultDomain
  registryName: portalRegistry!.outputs.name
  registryServer: portalRegistry!.outputs.server
  vaultName: serviceNames.keyVault
  l7IngressSecretIdentifier: grpcEnabled ? 'https://${serviceNames.keyVault}${environment().suffixes.keyvaultDns}/secrets/core-l7-ingress-secret' : ''
  realtimeTicketSecretIdentifier: realtimeEnabled ? 'https://${serviceNames.keyVault}${environment().suffixes.keyvaultDns}/secrets/core-realtime-ticket-key' : ''
} : {}
