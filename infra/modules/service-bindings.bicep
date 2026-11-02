// 作成済みサービスへ Azure RBAC と API Center の接続設定を追加します。
// 呼出元の認証と、APIM が裏側の Foundry を呼ぶ認証は別です。裏側には APIM のマネージド ID を使います。
// 組み込みロールを対象資源へ限定して割り当てます。Entra の管理ロールやアプリ登録は作成しません。

targetScope = 'resourceGroup'

@description('親モジュールで確定したサービス名の組。別モジュールでも同じ資源を参照するために使います。')
param names object
@description('サービスのマネージド ID の Object ID 一覧。Azure RBAC の割当先に使います。')
param principalIds object
@description('証明書参照用マネージド ID の Object ID。対象 Key Vault に閲覧権限を付けます。')
param certificateIdentityPrincipalId string
@description('管理 VM のシステム割り当て ID の Object ID。証明書取得・更新時の Key Vault 操作に使います。')
param managementPrincipalId string = ''
@description('管理 VM に証明書と更新情報の登録権限を対象 Key Vault 内で付与するか。')
param grantManagementCertificateAccess bool = true
@description('APIM のシステム割り当て ID に対象 Key Vault の secret 読取権限を付けるか。')
param grantApimSecretAccess bool = false
@description('APIM に Speech 呼出し用の組み込みロールを付けるか。')
param speechEnabled bool = false
@description('APIM に対象 Foundry アカウントの組み込み Cognitive Services User を付けるか。Cohere 等の呼出しに使いますが、キー一覧取得や他のデータ操作も含むため、OpenAI 呼出し専用ロールより広い権限です。')
param grantApimFoundryModelsAccess bool = false
@description('Foundry プロジェクトの管理担当者。principalId と principalType を指定します。')
param projectAdministrators array = []
@description('Foundry プロジェクトで開発を行う利用者または ID の一覧。')
param projectDevelopers array = []
@description('Foundry アカウントの構成を参照できる利用者または ID の一覧。')
param foundryReaders array = []
@description('API Center の API カタログを参照できる利用者または ID の一覧。')
param catalogReaders array = []
@description('API Center から今回の APIM の API 一覧を同期できるようにするか。')
param enableApiCenterIntegration bool = true
@allowed(['development', 'production', 'staging', 'testing'])
@description('API Center で入口を分類する環境種別。リソース名の環境名とは独立した表示用設定です。')
param catalogEnvironmentKind string = 'production'
@allowed(['deprecated', 'design', 'development', 'preview', 'production', 'retired', 'testing'])
@description('API Center に登録する API のライフサイクル段階。')
param catalogLifecycleStage string = 'production'
// 既存 Key Vault を権限割当の対象として参照します。
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: names.keyVault }
// 既存 Foundry をモデル呼出し権限等の対象として参照します。
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: names.foundry }
// 既存の共有プロジェクトをユーザー権限の対象にします。
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = { parent: foundry, name: names.project }
// 既存 APIM の ID を読み、バックエンド用権限の割当先にします。
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: names.apim }
// 既存 API Center をカタログ参照権限の対象として参照します。
resource apiCenter 'Microsoft.ApiCenter/services@2024-03-01' existing = { name: names.apiCenter }
// API Center の既定 Workspace を参照します。
resource workspace 'Microsoft.ApiCenter/services/workspaces@2024-03-01' existing = { parent: apiCenter, name: 'default' }

// 以下の GUID は Azure 組み込みロールの定義 ID です。独自ロールを新設せず、割当の scope で範囲を絞ります。
var secretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var certificatesOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a4417e6f-fecd-4de8-b567-7b0420556985')
var secretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
var openAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var foundryUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var projectManagerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eadc314b-1a2d-4efa-be10-5d325db5065e')
var readerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
var apiManagementReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '71522526-b88f-4d52-b57f-d31fc3546d0d')
var apiCenterDataReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c7244dfb-f447-457d-b2ba-3999044d1706')
var languageReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7628b7b8-a8b2-4cdc-b46f-e9b35248918e')
// Official built-in role: includes SpeechServices/voicelive/realtime/*.
// https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/ai-machine-learning#cognitive-services-speech-user
var speechUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f2dc8367-1007-4938-bd23-fe263f013447')
var cognitiveServicesUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

// 証明書参照用 MI に対象 Key Vault の secret 読取を許可。AppGW はこの ID を使います。
resource certificateReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, certificateIdentityPrincipalId, secretsUserRoleId)
  scope: vault
  properties: { principalId: certificateIdentityPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: secretsUserRoleId }
}
// 管理 VM が対象 Key Vault 内で証明書を登録・更新できる権限です。
resource managementCertificateOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantManagementCertificateAccess && !empty(managementPrincipalId)) {
  name: guid(vault.id, managementPrincipalId, certificatesOfficerRoleId)
  scope: vault
  properties: { principalId: managementPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: certificatesOfficerRoleId }
}
// The certificate service stores its DNS credential and ACME account state in
// this Vault. No secret value is passed to or returned by this ARM template.
// 管理 VM が DNS 資格情報・更新状態等を対象 Key Vault に保存できる権限です。
resource managementSecretOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantManagementCertificateAccess && !empty(managementPrincipalId)) {
  name: guid(vault.id, managementPrincipalId, secretsOfficerRoleId)
  scope: vault
  properties: { principalId: managementPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: secretsOfficerRoleId }
}
// 必要な場合だけ APIM のシステム ID に対象 Key Vault の secret 読取を許可します。
resource apimSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantApimSecretAccess) {
  name: guid(vault.id, principalIds.apim, secretsUserRoleId)
  scope: vault
  properties: { principalId: principalIds.apim, principalType: 'ServicePrincipal', roleDefinitionId: secretsUserRoleId }
}
// APIM が OpenAI 系モデルを呼ぶための組み込みロールです。
resource modelCaller 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalIds.apim, openAiUserRoleId)
  scope: foundry
  properties: { principalId: principalIds.apim, principalType: 'ServicePrincipal', roleDefinitionId: openAiUserRoleId }
}
// The built-in role permits Language analysis without key retrieval or role
// assignment writes. The API policy separately limits the public operation to
// PII recognition; this grant is scoped to this Foundry account only.
// APIM が PII 解析を呼ぶための Language Reader。カスタムロールは作成しません。
resource piiCaller 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalIds.apim, languageReaderRoleId)
  scope: foundry
  properties: { principalId: principalIds.apim, principalType: 'ServicePrincipal', roleDefinitionId: languageReaderRoleId }
}
// Speech 有効時だけ APIM に Speech 呼出し権限を追加します。
resource speechCaller 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (speechEnabled) {
  name: guid(foundry.id, principalIds.apim, speechUserRoleId)
  scope: foundry
  properties: { principalId: principalIds.apim, principalType: 'ServicePrincipal', roleDefinitionId: speechUserRoleId }
}
// Cohere 等の利用に使う組み込み Cognitive Services User。Cohere だけの専用権限ではありません。
// モデル実行に加えてキー一覧取得や他のデータ操作も含みます。付与範囲は今回の Foundry アカウント１個に限定します。
resource foundryModelCaller 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantApimFoundryModelsAccess) {
  name: guid(foundry.id, principalIds.apim, cognitiveServicesUserRoleId)
  scope: foundry
  properties: { principalId: principalIds.apim, principalType: 'ServicePrincipal', roleDefinitionId: cognitiveServicesUserRoleId }
}
// プロジェクト自身の MI に、親 Foundry の利用権限を与えます。
resource projectRuntime 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalIds.project, foundryUserRoleId)
  scope: foundry
  properties: { principalId: principalIds.project, principalType: 'ServicePrincipal', roleDefinitionId: foundryUserRoleId }
}
// 指定管理者へプロジェクト管理権限を付与します。
resource projectManagers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in projectAdministrators: {
  name: guid(project.id, principal.principalId, projectManagerRoleId)
  scope: project
  properties: { principalId: principal.principalId, principalType: principal.principalType, roleDefinitionId: projectManagerRoleId }
}]
// 指定開発者へプロジェクト利用権限を付与します。
resource projectUsers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in projectDevelopers: {
  name: guid(project.id, principal.principalId, foundryUserRoleId)
  scope: project
  properties: { principalId: principal.principalId, principalType: principal.principalType, roleDefinitionId: foundryUserRoleId }
}]
// 指定利用者へ Foundry アカウント構成の Reader を付与します。
resource accountReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in foundryReaders: {
  name: guid(foundry.id, principal.principalId, readerRoleId)
  scope: foundry
  properties: { principalId: principal.principalId, principalType: principal.principalType, roleDefinitionId: readerRoleId }
}]
// API Center 自身の MI に APIM の定義を読む権限を付けます。
resource apiCenterApimReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableApiCenterIntegration) {
  name: guid(apim.id, principalIds.apiCenter, apiManagementReaderRoleId)
  scope: apim
  properties: { principalId: principalIds.apiCenter, principalType: 'ServicePrincipal', roleDefinitionId: apiManagementReaderRoleId }
}
// 指定利用者へ API Center のデータ参照権限を付けます。
resource apiCenterReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in catalogReaders: {
  name: guid(apiCenter.id, principal.principalId, apiCenterDataReaderRoleId)
  scope: apiCenter
  properties: { principalId: principal.principalId, principalType: principal.principalType, roleDefinitionId: apiCenterDataReaderRoleId }
}]

// 公開 Gateway の URL と環境分類をカタログに登録します。
resource catalogEnvironment 'Microsoft.ApiCenter/services/workspaces/environments@2024-03-01' = if (enableApiCenterIntegration) {
  parent: workspace
  name: 'core-gateway'
  properties: {
    title: 'Core API gateway'
    kind: catalogEnvironmentKind
    description: 'Gateway implementation. Catalog API deployment URLs must use the public Application Gateway URL.'
    server: { type: 'Azure API Management' }
  }
}

// Microsoft exposes APIM integration only in this preview API. The premium
// APIM link enables API Center Standard without an invented services.sku field.
// API Center が今回の APIM から定義を同期する接続元を登録します。
resource apiManagementSource 'Microsoft.ApiCenter/services/workspaces/apiSources@2024-06-01-preview' = if (enableApiCenterIntegration) {
  parent: workspace
  name: 'core-apim'
  properties: {
    // 公開Bicep型情報に欠けていますが、Microsoft公式CLIは同じAPI版で
    // この種別を必須送信します。既存sourceの更新時も同じ接続元種別を明示します。
    // 同じsource名で別APIMへ接続先を変えられることまでは保証しません。
    // 更新後はGETで実resourceIdと同期状態を照合し、HTTP成功だけを完了条件にしません。
    // https://github.com/Azure/azure-cli-extensions/blob/main/src/apic-extension/azext_apic_extension/command_patches.py
    #disable-next-line BCP037 // Official CreateApimIntegration uses AzureApiManagement with the 2024-06-01-preview API.
    apiSourceType: 'AzureApiManagement'
    // msiResourceId is for an explicitly selected user-assigned identity. This
    // service uses its SystemAssigned identity, already granted APIM Reader.
    azureApiManagementSource: { resourceId: apim.id }
    importSpecification: 'always'
    // この項目は ARM の完全なリソース ID ではなく、API Center 内の相対パスを受け取ります。
    // 実際に作成する Workspace と環境の名前を使い、別の環境を誤って参照しないようにします。
    targetEnvironmentId: '/workspaces/${workspace.name}/environments/${catalogEnvironment.name}'
    targetLifecycleStage: catalogLifecycleStage
  }
  dependsOn: [apiCenterApimReader]
}

output apiCenterIntegrationResourceId string = enableApiCenterIntegration ? apiManagementSource.id : ''
output apiCenterEnvironmentResourceId string = enableApiCenterIntegration ? catalogEnvironment.id : ''
output principalBindings object = { apim: principalIds.apim, project: principalIds.project, certificateReader: certificateIdentityPrincipalId, certificateOperator: managementPrincipalId }
