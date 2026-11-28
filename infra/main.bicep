// 基盤全体の組み立て順序を定義する入口です。azd のフックが検証済み設定を渡します。
// 最初に監視用の保存先を準備し、次にネットワーク・サービス・証明書・公開入口をつなぎます。
// Entra のアプリ登録や企業ハブ側の変更はこのファイルでは実行しません。
// 同じ製品名と環境名から同じリソース名を作り、azd down が所有する５個の RG を追跡できるようにします。

targetScope = 'subscription'

@description('params と config を結合・検証した公開設定。秘密情報は別の secure パラメーターから渡します。')
param configuration object
@allowed(['monitoring', 'core'])
@description('監視保存先の準備と共用基盤の配備を分ける内部パラメーター。どちらも azd up の中で実行します。')
param phase string = 'core'
@description('管理 VM の SSH 公開鍵。秘密鍵は渡さず、手元で安全に保持します。')
@secure()
param sshPublicKey string = ''
@description('正式CLIが保護保存した初期作成証跡の内部世代。公開params/configで変更する値ではありません。')
param managementInitialSerialGeneration string = ''
@description('Firewallの中間CAの参照とAppGW用公開ルート証明書。中間CAの秘密鍵は渡しません。')
@secure()
param firewallTlsInspection object = {}
@description('接続プロバイダー名をキーにした OAuth client secret。公開設定から分離して渡します。')
@secure()
param credentialProviderSecrets object = {}
@description('AppGW の HTTP 書き換え経路を APIM が見分ける共有値。利用者の認証トークンとは別です。')
@secure()
param l7IngressSecret string = ''
@description('Realtime セッションの所有者を示す署名付きチケットの鍵。利用者へ鍵自体は返しません。')
@secure()
param realtimeTicketKey string = ''
@description('Verified internal metadata for absent ingress keys. Existing generations are never written again.')
@secure()
param ingressSecretRegistration object = {}
@description('監査コンテナーの保存保護ポリシーをこの呼出しで管理するか。既存の Locked 保護を解除する指定ではありません。')
param manageAuditImmutabilityPolicies bool = true
@description('実状態を照合した既存 Locked 監査ポリシーの ID。追加コンテナーの配備時も既存の保持を変更しません。内部入力です。')
param retainedAuditImmutabilityPolicyIds array = []
@description('監視保存先だけを更新するときも、既に稼働中の監査出力を維持するか。')
param preserveAuditCollection bool = false
@description('稼働中の同じ監査Storage向け本文トレース転送を維持するか。正式CLIが確認する内部入力です。')
param preserveAgentContentExport bool = false
@description('azd が実在と所有を確認した NSG 名。初回は空で、再 up 時の親 NSG の再 PUT を避けます。利用者向けの設定項目ではありません。')
param existingNetworkSecurityGroupNames array = []
@description('azd が同じ環境の実ルート表から確認した既存経路。再 up 時も後続段階の経路を保ち、公開 config では指定しません。')
param existingRouteTableRoutes object = {}
@description('azd が同じ環境の APIM から確認した、実 APIM 名ごとの公開ドメイン・証明書参照。再 up で postprovision の設定を消さないための内部入力です。')
param existingApimHostnameConfigurations object = {}
param existingApimCustomProperties object = {}
@description('azd が対象 CA の配置先を照合して確認した実在状態。初回だけ初期イメージで作成し、既存アプリの更新はアプリ配備工程が担当します。内部入力です。')
param existingPortalApp bool = false
@description('監視先から読み取った取り込み変換DCRの実ID。再配備時の関連付けを保持する内部入力です。')
param existingWorkspaceDataCollectionRuleResourceId string = ''
@description('正式CLIが所有・既存宛先・分類・転送範囲を照合して生成する基盤の診断設定プロパティ。内部入力です。')
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
var entraRolloutEnabled = configuration.?entraRolloutEnabled ?? false
var gatewayConfig = configuration.gateway
// 公開API配列は変更せず、基盤に必要な経路・本文形式だけを参照します。
var infrastructureApis = configuration.?infrastructureApiDefinitions ?? configuration.apiDefinitions
var realtimeEnabled = (gatewayConfig.?realtimeControlEnabled ?? false) || length(filter(configuration.apiDefinitions, api => length(filter(items(api.authorization.operationConstraints), entry => contains(['realtimeCreate', 'realtimeHangup'], entry.value.kind))) > 0)) > 0
// 文書ジョブとRealtimeは同じサーバー署名鍵を用途別の署名文脈で使用します。
var ownerTicketEnabled = realtimeEnabled || length(filter(infrastructureApis, api => length(filter(items(api.authorization.operationConstraints), entry => contains(['documentAnalysis', 'documentAnalysisStatus'], entry.value.kind))) > 0)) > 0
var certConfig = configuration.certificate
var acmeDirectoryUrl = certConfig.directory == 'staging' ? 'https://acme-staging-v02.api.letsencrypt.org/directory' : 'https://acme-v02.api.letsencrypt.org/directory'
var certificateJobName = 'caj-${stem}-tls'
var portalCertificateJobName = 'caj-${stem}-auth-cert'
// Pythonの文書受付と同じ正本POST/binary契約だけに、容量と独立した専用WAFを維持します。
var documentUploadPaths = flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'document-analysis-v1' && api.path == 'tools/documents/v1'), api => map(filter(api.operations, operation => operation.name == 'analyze_document' && operation.method == 'POST' && operation.urlTemplate == '/analyze' && api.authorization.operationConstraints[operation.name].kind == 'documentAnalysis' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'binary'), operation => '/${api.path}${operation.urlTemplate}')))
// 既存の署名付き2操作だけ、可変IDの位置をAppGW用patternへ写します。任意のtemplateは展開しません。
var signedOperationWafPaths = concat(
  flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'document-analysis-v1' && api.path == 'tools/documents/v1'), api => map(filter(api.operations, operation => operation.name == 'get_document_operation' && operation.method == 'GET' && operation.urlTemplate == '/operations/{operationId}' && api.authorization.operationConstraints[operation.name].kind == 'documentAnalysisStatus' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'none'), operation => '/tools/documents/v1/operations/*'))),
  flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'openai-realtime-control-v1' && api.path == 'models/openai-realtime/v1'), api => map(filter(api.operations, operation => operation.name == 'realtime-hangup' && operation.method == 'POST' && operation.urlTemplate == '/calls/{callId}/hangup' && api.authorization.operationConstraints[operation.name].kind == 'realtimeHangup' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'none'), operation => '/models/openai-realtime/v1/calls/*'))),
  flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'openai-realtime-observer-v1' && api.path == 'models/openai-realtime/v1/observer' && (api.?apiType ?? 'http') == 'websocket' && contains(api.?websocket ?? {}, 'realtimeObserver')), api => map(filter(api.operations, operation => operation.name == 'invoke' && operation.method == 'GET' && operation.urlTemplate == '/' && api.authorization.operationConstraints[operation.name].kind == 'registeredOperation' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'none'), operation => '/models/openai-realtime/v1/observer/')))
)
// 配備名をモデル一覧で固定しないWAF経路は、承認した画像編集・文字起こしの2操作だけです。
// API名・公開prefix・操作名・method・template・kind・本文形式・パラメーターの一致で選びます。
// 任意のtemplateをwildcardへ変換せず、他の配備名付き操作は従来の有限生成を維持します。
// 正本catalogの小さい2署名だけを読取り、型定義全体をIaCへ重複して埋め込みません。
var deploymentOperationWafContracts = loadJsonContent('../apps/api-gateway/waf-input-contracts.json', '$.modelOperationWafRoutes')
var approvedDeploymentWafRoutes = filter(deploymentOperationWafContracts, contract => length(filter(infrastructureApis, api => (api.?name ?? '') == contract.api && api.path == contract.path && (api.?apiType ?? 'http') == contract.apiType && length(filter(api.operations, operation => operation.name == contract.operation && operation.method == contract.method && operation.urlTemplate == contract.urlTemplate && api.authorization.operationConstraints[operation.name].kind == contract.kind && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == contract.bodyFormat && (api.authorization.operationConstraints[operation.name].?parameter ?? '') == contract.parameter && string(api.authorization.operationConstraints[operation.name].?contentTypes ?? []) == string(contract.contentTypes))) > 0)) > 0)
var deploymentOperationWafPaths = map(approvedDeploymentWafRoutes, contract => contract.wafPath)
// 上の正式一致が成立した2操作はprefixへ置換し、モデル別の完全パスを重ねて生成しません。
var publishedHttpWafPaths = flatten(map(infrastructureApis, api => flatten(map(filter(api.operations, operation => !contains(operation.urlTemplate, '*') && '/${api.path}${operation.urlTemplate}' != '/models/openai-realtime/v1/observer/' && empty(filter(approvedDeploymentWafRoutes, contract => (api.?name ?? '') == contract.api && api.path == contract.path && operation.name == contract.operation))), operation => contains(operation.urlTemplate, '{deployment}') ? map(api.authorization.operationConstraints[operation.name].?allowedModels ?? [], model => '/${api.path}${replace(operation.urlTemplate, '{deployment}', model)}') : (!contains(operation.urlTemplate, '{') ? ['/${api.path}${operation.urlTemplate}'] : [])))))
// Raw本文の調整はAzure Speech正本のtts/stt契約だけ。その他のSSML APIへ広げません。
var speechRawBodyPaths = flatten(map(filter(infrastructureApis, api => (api.?name ?? '') == 'azure-speech-v1' && !contains(api.path, '*') && !contains(api.path, '{') && !contains(api.path, '?')), api => map(filter(api.operations, operation => operation.method == 'POST' && api.authorization.operationConstraints[operation.name].kind == 'registeredOperation' && length(api.authorization.operationConstraints[operation.name].?contentTypes ?? []) == 1 && ((operation.name == 'tts' && operation.urlTemplate == '/synthesize' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'text' && contains(api.authorization.operationConstraints[operation.name].contentTypes, 'application/ssml+xml')) || (operation.name == 'stt' && operation.urlTemplate == '/recognize' && (api.authorization.operationConstraints[operation.name].?bodyFormat ?? '') == 'binary' && contains(api.authorization.operationConstraints[operation.name].contentTypes, 'audio/wav')))), operation => '/${api.path}${operation.urlTemplate}')))
var serviceNames = {
  keyVault: 'kv-${stem}'
  foundry: 'aif-${stem}'
  project: 'proj-${stem}'
  apim: 'apim-${stem}-${configuration.services.apimInstanceId}'
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
// APIM・Foundry・Key Vault・PortalのACR/CAE/CAをまとめるサービスRG。
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
module identity 'modules/certificates/public-tls-identity.bicep' = {
  name: '${stem}-identity'
  scope: idGroup
  params: { resourceStem: stem, location: location, tags: tags }
}

// 構築中の記録も保存できるよう、監視保存先と収集定義を最初に用意します。
module monitoring 'modules/monitoring/monitoring.bicep' = {
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
    auditImmutabilityMode: monitoringConfig.auditImmutabilityMode
    auditRetentionDays: monitoringConfig.auditRetentionDays
    manageAuditImmutabilityPolicies: manageAuditImmutabilityPolicies
    retainedAuditImmutabilityPolicyIds: retainedAuditImmutabilityPolicyIds
    enableAuditCollection: deployCore || preserveAuditCollection
    preserveAgentContentExport: preserveAgentContentExport
    existingWorkspaceDataCollectionRuleResourceId: existingWorkspaceDataCollectionRuleResourceId
  }
}


// VNet と固定規則を作成し、APIM と管理 VM の初回起動前に外向き経路を用意します。
module network 'modules/network/network.bicep' = if (deployCore) {
  name: '${stem}-network'
  scope: nwGroup
  // The local loader validates product/environment length before ARM submission.
  params: {
    #disable-next-line BCP334
    resourcePrefix: stem
    location: location
    tags: tags
    config: configuration.network
    firewallTlsInspection: {
      managedIdentityResourceId: firewallTlsInspection.?managedIdentityResourceId ?? ''
      intermediateCaSecretId: firewallTlsInspection.?intermediateCaSecretId ?? ''
    }
    generatedApimServiceFqdns: configuration.generatedApimServiceFqdns
    gatewayPublicHostName: gatewayConfig.publicHostName
    portalPublicHostName: portalConfig.publicHostName
    existingNetworkSecurityGroupNames: existingNetworkSecurityGroupNames
    existingRouteTableRoutes: existingRouteTableRoutes
    diagnosticSettingProperties: diagnosticSettingProperties
  }
  dependsOn: [monitoring]
}



// FW 通過の証跡は、サービス構築の完了を待たず、FW と保護済み保存先が揃ったら収集します。
module firewallDiagnostics 'modules/monitoring/firewall-diagnostics.bicep' = if (deployCore) {
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
module services 'modules/services/services.bicep' = if (deployCore) {
  name: '${stem}-services'
  scope: svcGroup
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    names: serviceNames
    location: location
    foundryLocation: configuration.foundryLocation
    tags: tags
    tenantId: configuration.azureTenantId
    apimSubnetId: network!.outputs.subnetIds.apim
    publisherName: servicesConfig.publisherName
    publisherEmail: servicesConfig.publisherEmail
    apimSkuName: servicesConfig.apimSkuName
    apimCapacity: servicesConfig.apimCapacity
    existingApimHostnameConfigurations: existingApimHostnameConfigurations
    existingApimCustomProperties: existingApimCustomProperties
    vaultSkuName: servicesConfig.vaultSkuName
    vaultRetentionDays: servicesConfig.vaultRetentionDays
    vaultPurgeProtection: servicesConfig.vaultPurgeProtection
    // APIM の証明書等の管理処理だけに必要な trusted services 例外。
    // KV の一般公開は無効を維持し、通常のデータ通信は PE を使います。
    allowApimKeyVaultControlPlane: true
    applicationInsightsResourceId: monitoring.outputs.monitoring.appInsightsId
    applicationInsightsConnectionString: monitoring.outputs.monitoring.appInsightsConnectionString
    modelDeployments: servicesConfig.modelDeployments
    projectDisplayName: servicesConfig.projectDisplayName
    projectDescription: servicesConfig.projectDescription
  }
}

// 共通policyの設定はAPI公開より先に用意し、Gateway配備と同じ生成元を使います。
module entraSettings '../apps/api-gateway/deploy/entra-named-values.bicep' = if (deployCore && contains(gatewayConfig.authentication, 'entra')) {
  name: '${stem}-core-entra-settings'
  scope: svcGroup
  params: {
    apimName: serviceNames.apim
    tenantId: configuration.?entraTenantId ?? configuration.azureTenantId
    entra: gatewayConfig.authentication.entra
  }
  dependsOn: [services]
}

// サービス作成後に Private Endpoint と DNS の対応付けを作ります。
module endpoints 'modules/network/services-private-endpoints.bicep' = if (deployCore) {
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
module management 'modules/management/management.bicep' = if (deployCore) {
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
    diagnosticSettingProperties: diagnosticSettingProperties
    vmSize: managementConfig.vmSize
    imageReference: managementConfig.imageReference
    diskControllerType: managementConfig.diskControllerType
    osDiskSizeGB: managementConfig.osDiskSizeGB
    osDiskSku: managementConfig.osDiskSku
    enableAcceleratedNetworking: managementConfig.enableAcceleratedNetworking
    encryptionAtHost: managementConfig.encryptionAtHost
    adminUsername: managementConfig.adminUsername
    sshPublicKey: sshPublicKey
    initialSerialGeneration: managementInitialSerialGeneration
    patchAssessmentMode: managementConfig.patchAssessmentMode
    patchMode: managementConfig.patchMode
  }
}

// 監査Storage本体を再定義せず、構築済み保存先にBlob PEを常設します。
module auditPrivateEndpoints 'modules/monitoring/audit-storage-private-endpoints.bicep' = if (deployCore) {
  name: '${stem}-audit-private-endpoints'
  scope: nwGroup
  params: {
    resourcePrefix: stem
    location: location
    tags: tags
    pepSubnetId: network!.outputs.subnetIds.pep
    auditStorage: monitoring.outputs.storage
  }
}

// 実 IP を使って NSG・UDR を仕上げ、AppGW → Firewall → APIM と PE 接続を許可します。
module networkBindings 'modules/network/network-bindings.bicep' = if (deployCore) {
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
    firewallPrivateIp: network!.outputs.firewallPrivateIp
    firewallPublicIpAddress: network!.outputs.firewallPublicIpAddress
    appGatewayPublicIpAddress: network!.outputs.appGatewayPublicIpAddress
    apimPrivateIp: services!.outputs.apimPrivateIpAddresses[0]
    apimGatewayFqdn: services!.outputs.apimGatewayHost
    foundryPrivateIps: endpoints!.outputs.foundryPrivateIps
    keyVaultPrivateIps: endpoints!.outputs.keyVaultPrivateIps
    managementPrivateIp: management!.outputs.managementVm.privateIp
    auditPlatformBlobPrivateIps: auditPrivateEndpoints!.outputs.privateEndpoints.collectPlatform.privateIps
    auditFoundryBlobPrivateIps: auditPrivateEndpoints!.outputs.privateEndpoints.collectFoundry.privateIps
  }
}

// 対向NSGの完了後にzone groupを作り、実Aレコードを読むまでDNSリンクを作りません。
module auditPrivateDns 'modules/monitoring/audit-storage-private-dns.bicep' = if (deployCore) {
  name: '${stem}-audit-private-dns'
  scope: nwGroup
  params: {
    tags: tags
    auditStorage: monitoring.outputs.storage
    privateEndpoints: auditPrivateEndpoints!.outputs.privateEndpoints
  }
  dependsOn: [networkBindings]
}
var auditDnsChecks = deployCore ? [
  length(auditPrivateEndpoints!.outputs.privateEndpoints.collectPlatform.privateIps) > 0
  length(auditPrivateEndpoints!.outputs.privateEndpoints.collectFoundry.privateIps) > 0
  length(auditPrivateDns!.outputs.recordPrivateIps.collectPlatform) == length(auditPrivateEndpoints!.outputs.privateEndpoints.collectPlatform.privateIps)
  length(auditPrivateDns!.outputs.recordPrivateIps.collectFoundry) == length(auditPrivateEndpoints!.outputs.privateEndpoints.collectFoundry.privateIps)
  length(intersection(auditPrivateDns!.outputs.recordPrivateIps.collectPlatform, auditPrivateEndpoints!.outputs.privateEndpoints.collectPlatform.privateIps)) == length(auditPrivateEndpoints!.outputs.privateEndpoints.collectPlatform.privateIps)
  length(intersection(auditPrivateDns!.outputs.recordPrivateIps.collectFoundry, auditPrivateEndpoints!.outputs.privateEndpoints.collectFoundry.privateIps)) == length(auditPrivateEndpoints!.outputs.privateEndpoints.collectFoundry.privateIps)
] : [false]
var auditDnsReady = empty(filter(auditDnsChecks, ready => !ready))
module auditPrivateDnsLink 'modules/monitoring/audit-storage-private-dns-link.bicep' = if (deployCore) {
  name: '${stem}-audit-private-dns-link'
  scope: nwGroup
  params: {
    resourcePrefix: stem
    tags: tags
    vnetId: network!.outputs.vnetId
    privateDnsZoneId: auditPrivateDns!.outputs.privateDnsZoneId
    // ARM validates the allowed true value after the real DNS reads resolve.
    dnsRecordsValidated: any(auditDnsReady)
  }
}

// ロールを定義し、対象資源での担当者への割当は保守時に行います。
module managementMaintenanceRoles 'modules/management/management-maintenance-roles.bicep' = if (deployCore) {
  name: '${stem}-management-maintenance-roles'
  params: { resourceStem: stem }
}
// 内部通信の準備後に、サービス ID へ用途別 Azure RBAC を付与します。
module serviceBindings 'modules/services/service-bindings.bicep' = if (deployCore) {
  name: '${stem}-service-bindings'
  scope: svcGroup
  params: {
    names: serviceNames
    principalIds: services!.outputs.principalIds
    // 通常HTTP入口保護・所有者チケットの KV 参照にも APIM 自身の ID が必要です。KV 範囲の既存組込ロールだけを使います。
    grantApimSecretAccess: true
    speechEnabled: servicesConfig.?speechEnabled ?? false
    grantApimFoundryModelsAccess: servicesConfig.?grantApimFoundryModelsAccess ?? false
  }
  dependsOn: [networkBindings]
}


// 基盤は共通SQLサーバーとスキーマ管理IDを管理します。
// Gatewayの接続台帳DB・実行ID・CM定義はアプリ配備工程で作成します。
module credentialIdentities 'modules/services/shared-sql-identity.bicep' = if (deployCore) {
  name: '${stem}-credential-identities'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}
module credentialSql 'modules/services/shared-sql.bicep' = if (deployCore) {
  name: '${stem}-credential-sql'
  scope: svcGroup
  params: {
    stem: stem
    location: location
    tags: tags
    tenantId: configuration.azureTenantId
    schemaIdentity: credentialIdentities!.outputs.schema
    diagnosticSettingProperties: diagnosticSettingProperties
  }
  dependsOn: [monitoring]
}
module sharedEntraSecrets 'modules/services/shared-entra-secrets.bicep' = if (deployCore) {
  name: '${stem}-shared-entra-secrets'
  scope: svcGroup
  params: { vaultName: serviceNames.keyVault, profiles: configuration.credentialProfiles, providerSecrets: credentialProviderSecrets }
  dependsOn: [services]
}

// 管理 VM を Azure ホスト PHC に同期し、認証ログを設定。証明書処理より先に完了します。
module auditConfiguration 'modules/management/management-audit.bicep' = if (deployCore) {
  name: '${stem}-management-audit'
  scope: maintGroup
  params: { vmName: management!.outputs.managementVm.name, location: location, tags: tags }
  dependsOn: [networkBindings]
}

// 公開TLSとPortalの署名証明書は基盤構築の後段で起動する専用Jobが扱います。
module certificateJobIdentities 'modules/certificates/certificate-identities.bicep' = if (deployCore) {
  name: '${stem}-certificate-job-identities'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}

resource dnsJobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = if (deployCore) {
  scope: resourceGroup(split(certConfig.azureDnsManagedIdentityResourceId, '/')[2], split(certConfig.azureDnsManagedIdentityResourceId, '/')[4])
  name: last(split(certConfig.azureDnsManagedIdentityResourceId, '/'))
}

// 入口の共有値は証明書用VaultとJobへ渡しません。
module ingressValues 'modules/ingress/ingress-values.bicep' = if (deployCore) {
  name: '${stem}-ingress-values'
  scope: svcGroup
  params: {
    vaultName: serviceNames.keyVault
    ownerTicketEnabled: ownerTicketEnabled
    registration: ingressSecretRegistration
    l7IngressSecret: l7IngressSecret
    realtimeTicketKey: realtimeTicketKey
  }
  dependsOn: [sharedEntraSecrets]
}

// Portal用IDはID用RG、レジストリと実行基盤はサービスRGに配置します。
module portalPullIdentity 'modules/container-apps/portal-identity.bicep' = if (deployCore) {
  name: '${stem}-portal-identity'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}

// Portalアプリの登録前にAzure側の署名ID・非exportable自己署名証明書を準備します。
module portalAuthIdentity '../apps/portal/deploy/auth-identity.bicep' = if (deployCore) {
  name: '${stem}-portal-auth-identity'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags }
}
module portalApiAuthIdentity '../apps/portal/deploy/auth-identity.bicep' = if (deployCore) {
  name: '${stem}-portal-api-auth-identity'
  scope: idGroup
  params: { stem: stem, location: location, tags: tags, purpose: 'portal-api-auth' }
}
module portalRegistry 'modules/container-apps/portal-registry.bicep' = if (deployCore) {
  name: '${stem}-portal-registry'
  scope: svcGroup
  params: {
    stem: stem
    location: location
    tags: tags
    pullIdentityId: portalPullIdentity!.outputs.id
    pullIdentityPrincipalId: portalPullIdentity!.outputs.principalId
    diagnosticSettingProperties: diagnosticSettingProperties
  }
  dependsOn: [monitoring]
}

// PE・Private DNS・NSG・Firewall規則を準備してから、CAEを起動します。
module portalNetwork 'modules/network/portal-network.bicep' = if (deployCore) {
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
    managementPrivateIp: management!.outputs.managementVm.privateIp
    portalNsgName: network!.outputs.nsgNames.portal
    portalPrefix: network!.outputs.subnetPrefixes.portal
    brokerNsgName: network!.outputs.nsgNames.broker
    brokerPrefix: network!.outputs.subnetPrefixes.broker
    firewallPolicyName: network!.outputs.firewallPolicyName
    pepNsgName: network!.outputs.nsgNames.pep
  }
}

module portal 'modules/container-apps/portal-platform.bicep' = if (deployCore) {
  name: '${stem}-portal'
  scope: svcGroup
  dependsOn: [monitoring, portalNetwork]
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    existingPortalApp: existingPortalApp
    stem: stem
    location: location
    tags: tags
    subnetId: network!.outputs.subnetIds.portal
    registryName: portalRegistry!.outputs.name
    pullIdentityId: portalPullIdentity!.outputs.id
  }
}

module brokerEnvironment 'modules/container-apps/broker-platform.bicep' = if (deployCore) {
  name: '${stem}-broker-platform'
  scope: svcGroup
  params: {
    stem: stem
    location: location
    tags: tags
    subnetId: network!.outputs.subnetIds.broker
    diagnosticSettingProperties: diagnosticSettingProperties.?broker ?? diagnosticSettingProperties
  }
  dependsOn: [monitoring, portalNetwork]
}

module brokerDns 'modules/container-apps/container-app-private-dns.bicep' = if (deployCore) {
  name: '${stem}-broker-dns'
  scope: nwGroup
  params: {
    stem: '${stem}-broker'
    defaultDomain: brokerEnvironment!.outputs.platform.defaultDomain
    staticIp: brokerEnvironment!.outputs.platform.privateIp
    vnetId: network!.outputs.vnetId
  }
}

module portalNetworkBindings 'modules/network/portal-network-bindings.bicep' = if (deployCore) {
  name: '${stem}-portal-network-bindings'
  scope: nwGroup
  dependsOn: [firewallDiagnostics]
  params: {
    stem: stem
    defaultDomain: portal!.outputs.defaultDomain
    staticIp: portal!.outputs.staticIp
    vnetId: network!.outputs.vnetId
    agwRouteTableName: network!.outputs.routeTableNames.agw
    agwNsgName: network!.outputs.nsgNames.agw
    managementNsgName: network!.outputs.nsgNames.management
    agwPrefix: network!.outputs.subnetPrefixes.agw
    managementPrivateIp: management!.outputs.managementVm.privateIp
    firewallIp: network!.outputs.firewallPrivateIp
    firewallPolicyName: network!.outputs.firewallPolicyName
    apimGatewayFqdn: services!.outputs.apimGatewayHost
    portalBackendHost: portal!.outputs.appHost
  }
}

module certificateStorage 'modules/certificates/certificate-storage.bicep' = if (deployCore) {
  name: '${stem}-certificate-storage'
  scope: svcGroup
  params: {
    stem: stem
    location: location
    tags: tags
    tenantId: configuration.azureTenantId
    vaultSkuName: servicesConfig.vaultSkuName
    vaultRetentionDays: servicesConfig.vaultRetentionDays
    vaultPurgeProtection: servicesConfig.vaultPurgeProtection
    portalVaultName: serviceNames.keyVault
    portalCertificateName: portalConfig.certificate.certificateName
    prepareGatewayObo: entraRolloutEnabled
    registryName: portalRegistry!.outputs.name
    pullPrincipalId: certificateJobIdentities!.outputs.pull.principalId
    tlsPrincipalId: certificateJobIdentities!.outputs.tls.principalId
    portalPrincipalId: certificateJobIdentities!.outputs.portal.principalId
    diagnosticSettingProperties: diagnosticSettingProperties
  }
  dependsOn: [services]
}
module certificateNetwork 'modules/certificates/certificate-network.bicep' = if (deployCore) {
  name: '${stem}-certificate-network'
  scope: nwGroup
  params: {
    stem: stem
    location: location
    tags: tags
    subnetIds: network!.outputs.subnetIds
    subnetPrefixes: network!.outputs.subnetPrefixes
    nsgNames: network!.outputs.nsgNames
    firewallPolicyName: network!.outputs.firewallPolicyName
    managementPrivateIp: management!.outputs.managementVm.privateIp
    keyVaultPrivateIps: endpoints!.outputs.keyVaultPrivateIps
    registryPrivateIps: portalNetwork!.outputs.registryPrivateIps
    tlsVaultId: certificateStorage!.outputs.vaultId
    oboVaultId: certificateStorage!.outputs.oboVaultId
    storageAccountId: certificateStorage!.outputs.storageAccountId
    vaultPrivateDnsZoneId: network!.outputs.privateDnsZoneIds.keyVault
    blobPrivateDnsZoneId: auditPrivateDns!.outputs.privateDnsZoneId
    acmeIssuerHost: split(acmeDirectoryUrl, '/')[2]
  }
  dependsOn: [portalNetworkBindings, credentialNetwork, auditPrivateDnsLink]
}
module certificateEnvironment 'modules/certificates/certificate-environment.bicep' = if (deployCore) {
  name: '${stem}-certificate-environment'
  scope: svcGroup
  params: { stem: stem, location: location, tags: tags, subnetId: network!.outputs.subnetIds.certificate, diagnosticSettingProperties: diagnosticSettingProperties }
  dependsOn: [certificateNetwork]
}

module credentialNetwork 'modules/network/credential-broker-network.bicep' = if (deployCore) {
  name: '${stem}-credential-network'
  scope: nwGroup
  params: {
    stem: stem
    location: location
    tags: tags
    sqlServerId: credentialSql!.outputs.id
    vnetId: network!.outputs.vnetId
    pepSubnetId: network!.outputs.subnetIds.pep
    pepNsgName: network!.outputs.nsgNames.pep
    portalPrefix: network!.outputs.subnetPrefixes.portal
    apimPrefix: network!.outputs.subnetPrefixes.apim
    apimNsgName: network!.outputs.nsgNames.apim
    brokerPrivateIp: brokerEnvironment!.outputs.platform.privateIp
    firewallPolicyName: network!.outputs.firewallPolicyName
    managementPrefix: network!.outputs.subnetPrefixes.management
    portalNsgName: network!.outputs.nsgNames.portal
    brokerNsgName: network!.outputs.nsgNames.broker
    brokerPrefix: network!.outputs.subnetPrefixes.broker
    managementNsgName: network!.outputs.nsgNames.management
    managementPrivateIp: management!.outputs.managementVm.privateIp
  }
  dependsOn: [portalNetworkBindings, brokerDns]
}

// 基盤構築で作成した対象を渡します。アプリ配備は後段で作成したJobを追加します。
var managementClientInventory = deployCore ? {
  schemaVersion: 1
  subscriptionId: subscription().subscriptionId
  azureTenantId: configuration.azureTenantId
  managementVmId: management!.outputs.managementVm.id
  managementPrincipalId: management!.outputs.managementVm.principalId
  applications: [portal!.outputs.platform.applicationId]
  jobs: []
  // App-owned user databases are added from successful Gateway/Portal outputs.
  sqlDatabases: []
  environments: [portal!.outputs.platform.environmentId, brokerEnvironment!.outputs.platform.environmentId]
  workspace: { id: monitoring.outputs.monitoring.workspaceId, customerId: monitoring.outputs.monitoring.workspaceCustomerId }
  auditStorage: map(['collectPlatform', 'collectFoundry'], collector => {
    id: monitoring.outputs.storage[collector].id
    containers: map(filter(monitoring.outputs.storage.containerIds, id => startsWith(toLower(id), '${toLower(monitoring.outputs.storage[collector].id)}/')), id => last(split(id, '/')))
  })
} : {}
module managementClient 'modules/management/management-maintenance-client.bicep' = if (deployCore) {
  name: '${stem}-management-client'
  scope: maintGroup
  params: {
    vmName: management!.outputs.managementVm.name
    location: location
    tags: tags
    inventory: managementClientInventory
  }
  // PyPI/OSパッケージの既存の構築用経路とVMの初期設定が完成してから準備します。
  dependsOn: [credentialNetwork, auditConfiguration, auditPrivateDnsLink]
}
// The native infrastructure deployment produces nonsecret plans. Verified Job completion gates
// public-ingress.bicep in postprovision; an ARM Job PUT is not a successful run.
var useIngressSecrets = true
var certificateSecretUri = deployCore ? 'https://${certificateStorage!.outputs.vaultName}${environment().suffixes.keyvaultDns}/secrets/${certConfig.certificateName}/' : ''
var gatewayPlan = {
  networkResourceGroup: nwGroup.name
  vaultResourceGroup: svcGroup.name
  certificateName: certConfig.certificateName
  diagnosticSettingProperties: diagnosticSettingProperties
  productName: product
  environmentName: environmentName
  resourceStem: stem
  wafPolicyIdentifier: configuration.services.apimInstanceId
  location: location
  tags: tags
  portalHostName: portalConfig.publicHostName
  publicHostName: gatewayConfig.publicHostName
  skuName: gatewayConfig.skuName
  autoscaleMinCapacity: gatewayConfig.autoscaleMinCapacity
  autoscaleMaxCapacity: gatewayConfig.autoscaleMaxCapacity
  zones: gatewayConfig.zones
  wafMode: gatewayConfig.wafMode
  wafMaxRequestBodySizeInKb: gatewayConfig.?wafMaxRequestBodySizeInKb ?? 128
  wafRequestBodyInspectLimitInKb: gatewayConfig.?wafRequestBodyInspectLimitInKb ?? 128
  wafFileUploadLimitInMb: gatewayConfig.?wafFileUploadLimitInMb ?? 100
  wafCustomRules: gatewayConfig.?wafCustomRules ?? []
  wafExclusions: gatewayConfig.?wafExclusions ?? []
  publishedApiPaths: concat(publishedHttpWafPaths, deploymentOperationWafPaths, map(filter(infrastructureApis, api => contains(api, 'mcp')), api => '/${api.mcp.path}/mcp'), signedOperationWafPaths)
  documentUploadPaths: documentUploadPaths
  speechRawBodyPaths: speechRawBodyPaths
  backendRequestTimeoutSeconds: gatewayConfig.backendRequestTimeoutSeconds
}
output gatewayPlan object = deployCore ? union(gatewayPlan, {
  certificateVaultName: certificateStorage!.outputs.vaultName
  certificateReaderPrincipalId: identity.outputs.principalId
  subnetId: network!.outputs.subnetIds.agw
  publicIpId: network!.outputs.appGatewayPublicIpId
  publicIpAddress: network!.outputs.appGatewayPublicIpAddress
  apimPrivateIp: services!.outputs.apimPrivateIpAddresses[0]
  portalBackendIp: portal!.outputs.staticIp
  portalBackendHost: portal!.outputs.appHost
  apimHostName: services!.outputs.apimGatewayHost
  keyVaultCertificateSecretId: certificateSecretUri
  certificateIdentityId: identity.outputs.id
}) : {}
output ingressMonitoringPlan object = deployCore ? {
  resourceGroup: monitorGroup.name
  resourceStem: stem
  location: location
  tags: tags
  monitoring: monitoring.outputs.monitoring
  storage: monitoring.outputs.storage
  apimId: services!.outputs.serviceResourceIds.apim
  firewallId: network!.outputs.firewallId
  networkResourceIds: network!.outputs.monitoredResourceIds
  resourceGroupIds: [idGroup.id, nwGroup.id, monitorGroup.id, svcGroup.id, maintGroup.id]
  enableLogAlerts: monitoringConfig.enableLogAlerts
  apimSkuName: servicesConfig.apimSkuName
  appInsightsIngestionWarningGb: monitoringConfig.appInsightsIngestionWarningGb
  telemetryPublisherPrincipalIds: [services!.outputs.principalIds.project]
  certificateMonitoring: { environmentName: certificateEnvironment!.outputs.name, jobName: certificateJobName, checkEveryHours: certConfig.checkEveryHours }
} : {}
output certificateBootstrap object = deployCore ? {
  resourceGroup: svcGroup.name
  location: location
  environmentId: certificateEnvironment!.outputs.id
  environmentName: certificateEnvironment!.outputs.name
  subnetId: network!.outputs.subnetIds.certificate
  registryId: portalRegistry!.outputs.id
  registryName: portalRegistry!.outputs.name
  registryServer: portalRegistry!.outputs.server
  registryPrivateIps: portalNetwork!.outputs.registryPrivateIps
  storageAccountId: certificateStorage!.outputs.storageAccountId
  storageAccountName: certificateStorage!.outputs.storageAccountName
  storageUrl: certificateStorage!.outputs.storageUrl
  receiptContainerName: certificateStorage!.outputs.receiptContainerName
  certificateVaultId: certificateStorage!.outputs.vaultId
  certificateVaultName: certificateStorage!.outputs.vaultName
  certificateSecretUri: certificateSecretUri
  jobName: certificateJobName
  portalJobName: portalCertificateJobName
  jobPlan: {
    location: location
    tags: tags
    environmentId: certificateEnvironment!.outputs.id
    registryServer: portalRegistry!.outputs.server
    jobName: certificateJobName
    portalJobName: portalCertificateJobName
    pullIdentityId: certificateJobIdentities!.outputs.pull.id
    tlsIdentity: certificateJobIdentities!.outputs.tls
    portalIdentity: certificateJobIdentities!.outputs.portal
    dnsIdentityId: dnsJobIdentity!.id
    dnsClientId: dnsJobIdentity!.properties.clientId
    storageUrl: certificateStorage!.outputs.storageUrl
    receiptContainerName: certificateStorage!.outputs.receiptContainerName
    portalStateContainerName: certificateStorage!.outputs.portalStateContainerName
    tlsConfig: {
      mode: certConfig.mode
      hostname: gatewayConfig.publicHostName
      hostnames: certConfig.domainNames
      requiredHostnames: [gatewayConfig.publicHostName, portalConfig.publicHostName]
      keyVaultName: certificateStorage!.outputs.vaultName
      certificateName: certConfig.certificateName
      dnsPlugin: certConfig.plugin
      azureDnsSubscriptionId: certConfig.azureDnsSubscriptionId
      azureDnsManagedIdentityResourceId: dnsJobIdentity!.id
      moduleVersion: certConfig.moduleVersion
      directoryUrl: acmeDirectoryUrl
      renewBeforeDays: certConfig.renewBeforeDays
      renewIntervalHours: certConfig.checkEveryHours
      dnsSleepSeconds: certConfig.dnsSleepSeconds
      validationTimeoutSeconds: certConfig.validationTimeoutSeconds
      minimumValidDays: certConfig.minimumValidDays
    }
    portalConfig: { vaultName: serviceNames.keyVault, certificateName: portalConfig.certificate.certificateName, validityMonths: portalConfig.certificate.validityMonths }
    gatewayOboConfig: entraRolloutEnabled ? { vaultName: certificateStorage!.outputs.oboVaultName, certificateName: 'gateway-obo', validityMonths: 12 } : {}
  }
} : {}

// API・認証ポリシー・名前付きの値・CM・BrokerはGateway配備工程が所有します。

// RG の外にある管理操作履歴も、今回の監査保存先へ接続します。
module subscriptionMonitoring 'modules/monitoring/monitoring-subscription.bicep' = if (deployCore) {
  name: '${stem}-subscription-monitoring'
  params: {
    diagnosticSettingProperties: diagnosticSettingProperties
    resourceStem: stem
  }
  dependsOn: [monitoring]
}

// 以降は後続スクリプト用の参照情報です。秘密値や PFX は出力せず、名前・ID・URI と公開メタデータだけを返します。
output CORE_DEPLOYMENT_NAME string = deployment().name
output capacityProfile string = configuration.?capacityProfile ?? ''
output resourceGroups object = toObject(groupKinds, kind => kind, kind => 'rg-${stem}-${kind}')
output certificateIdentity object = { id: identity.outputs.id, principalId: identity.outputs.principalId, clientId: identity.outputs.clientId }
output names object = {
  keyVault: serviceNames.keyVault
  foundry: serviceNames.foundry
  project: serviceNames.project
  apim: serviceNames.apim
}
output monitoring object = monitoring.outputs.monitoring
output auditStorage object = monitoring.outputs.storage
output auditStoragePrivateAccess object = deployCore ? {
  privateEndpoints: auditPrivateEndpoints!.outputs.privateEndpoints
  privateDnsZoneId: auditPrivateDns!.outputs.privateDnsZoneId
  privateDnsZoneGroupIds: auditPrivateDns!.outputs.privateDnsZoneGroupIds
  privateDnsLinkId: auditPrivateDnsLink!.outputs.privateDnsLinkId
} : {}
output managementMaintenance object = deployCore ? {
  roleDefinitionIds: managementMaintenanceRoles!.outputs.roleDefinitionIds
} : {}
output managementClientInventory object = managementClientInventory
output services object = deployCore ? services!.outputs.serviceResourceIds : {}
output endpoints object = deployCore ? { foundry: services!.outputs.foundryEndpoint, project: services!.outputs.projectEndpoint, public: '' } : {}
output managementVm object = deployCore ? management!.outputs.managementVm : {}
// 初回404と元の基盤配備の入出力を再開時に照合する非秘密の世代証跡。秘密はOSProfileへ渡しません。
output managementInitialSerialCreation object = deployCore && !empty(managementInitialSerialGeneration) ? {
  generation: managementInitialSerialGeneration
  coreDeploymentId: subscriptionResourceId('Microsoft.Resources/deployments', deployment().name)
  managementVm: management!.outputs.managementVm
} : {}
output AZURE_LOCATION string = location
output gateway object = {}
// 通常HTTP(S)の入口保護を常設します。APIM保守は標準FQDNのHTTPSを使用します。
output ingressSecrets object = deployCore ? {
  enabled: false
  l7IngressEnabled: useIngressSecrets
  realtimeEnabled: realtimeEnabled
  ownerTicketEnabled: ownerTicketEnabled
  apimId: services!.outputs.serviceResourceIds.apim
  apimGatewayHost: services!.outputs.apimGatewayHost
  publicHostName: gatewayConfig.publicHostName
  namedValueId: '${services!.outputs.serviceResourceIds.apim}/namedValues/core-l7-ingress-secret'
} : { enabled: false }
// Keep expected IDs available if subsequent infrastructure provisioning fails.
// Cleanup verifies the actual resources at these exact IDs before acting.
// down は以下の今回分の ID を照合してから後処理します。名前が似た別環境を列挙削除するための出力ではありません。
output cleanup object = {
  activityDiagnosticSettingId: subscriptionResourceId('Microsoft.Insights/diagnosticSettings', 'diag-${stem}-activity')
  keyVaultName: serviceNames.keyVault
  certificateVaultName: 'kvtls-${stem}'
  foundryName: serviceNames.foundry
  apimName: serviceNames.apim
  serviceResourceGroup: svcGroup.name
  foundryLocation: configuration.foundryLocation
  location: location
  serviceResourceIds: union({
    keyVault: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.KeyVault/vaults', serviceNames.keyVault)
    certificateVault: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.KeyVault/vaults', 'kvtls-${stem}')
    foundry: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.CognitiveServices/accounts', serviceNames.foundry)
    apim: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.ApiManagement/service', serviceNames.apim)
  }, entraRolloutEnabled ? {
    oboVault: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.KeyVault/vaults', 'kvobo-${stem}')
  } : {})
}

output portal object = deployCore ? union(portal!.outputs.platform, {
  authIdentity: portalAuthIdentity!.outputs.identity
  apiAuthIdentity: portalApiAuthIdentity!.outputs.identity
  authCertificateJobId: resourceId(subscription().subscriptionId, svcGroup.name, 'Microsoft.App/jobs', portalCertificateJobName)
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

output brokerPlatform object = deployCore ? brokerEnvironment!.outputs.platform : {}

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
  brokerEnvironmentName: brokerEnvironment!.outputs.platform.environmentName
  brokerEnvironmentId: brokerEnvironment!.outputs.platform.environmentId
  brokerEnvironmentDomain: brokerEnvironment!.outputs.platform.defaultDomain
  brokerSubnetId: brokerEnvironment!.outputs.platform.infrastructureSubnetId
  brokerPrivateIp: brokerEnvironment!.outputs.platform.privateIp
  registryName: portalRegistry!.outputs.name
  registryServer: portalRegistry!.outputs.server
  vaultName: serviceNames.keyVault
  l7IngressSecretIdentifier: 'https://${serviceNames.keyVault}${environment().suffixes.keyvaultDns}/secrets/core-l7-ingress-secret'
  realtimeTicketSecretIdentifier: ownerTicketEnabled ? 'https://${serviceNames.keyVault}${environment().suffixes.keyvaultDns}/secrets/core-realtime-ticket-key' : ''
} : {}
