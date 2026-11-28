// 作成済みサービスへ Azure RBAC を付与します。
// 呼出元の認証と、APIM が裏側の Foundry を呼ぶ認証は別です。裏側には APIM のマネージド ID を使います。
// 組み込みロールを対象資源へ限定して割り当てます。Entra の管理ロールやアプリ登録は作成しません。
// 企業利用者向けの権限は構築後にIAMで付与します。

targetScope = 'resourceGroup'

@description('親モジュールで確定したサービス名の組。別モジュールでも同じ資源を参照するために使います。')
param names object
@description('サービスのマネージド ID の Object ID 一覧。Azure RBAC の割当先に使います。')
param principalIds object
@description('APIM のシステム割り当て ID に対象 Key Vault の secret 読取権限を付けるか。')
param grantApimSecretAccess bool = false
@description('APIM に Speech 呼出し用の組み込みロールを付けるか。')
param speechEnabled bool = false
@description('APIM に対象 Foundry アカウントの組み込み Cognitive Services User を付けるか。Cohere 等の呼出しに使いますが、キー一覧取得や他のデータ操作も含むため、OpenAI 呼出し専用ロールより広い権限です。')
param grantApimFoundryModelsAccess bool = false

// 既存 Key Vault を権限割当の対象として参照します。
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: names.keyVault }
// 既存 Foundry をモデル呼出し権限等の対象として参照します。
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: names.foundry }

// 以下の GUID は Azure 組み込みロールの定義 ID です。独自ロールを新設せず、割当の scope で範囲を絞ります。
var secretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var openAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var foundryUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var languageReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7628b7b8-a8b2-4cdc-b46f-e9b35248918e')
// Official built-in role: includes SpeechServices/voicelive/realtime/*.
// https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/ai-machine-learning#cognitive-services-speech-user
var speechUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f2dc8367-1007-4938-bd23-fe263f013447')
var cognitiveServicesUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

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
