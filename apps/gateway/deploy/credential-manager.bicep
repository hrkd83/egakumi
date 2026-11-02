// APIM Credential Manager に、外部サービスへ本人として接続する OAuth 設定を登録します。
// 設定の作成だけでは本人の同意は完了しません。利用者の接続操作後、APIM が接続情報を読み出します。
// clientSecret は暗号化された ARM パラメーターとして受け取り、公開設定や出力に含めません。

targetScope = 'resourceGroup'

@description('OAuth 接続設定を作成する既存 APIM の名前。')
param apimName string
@minLength(36)
@maxLength(36)
@description('接続情報の利用を許可する APIM のシステム割り当て ID の Object ID。')
param apimPrincipalId string
@minLength(1)
@maxLength(256)
@description('APIM 内で OAuth 接続元を区別する設定名。')
param providerName string
@minLength(1)
@maxLength(300)
@description('ポータル等で人が用途を識別するための表示名。')
param displayName string
@allowed(['aad', 'oauth2'])
@description('OAuth 接続元の種別。aad の場合は Entra 固有のパラメーターを組み立てます。')
param identityProvider string
@minLength(1)
@description('事前登録した OAuth アプリの Application / Client ID。secret とは別の公開識別子です。')
param clientId string
@description('OAuth アプリの秘密情報。ARM secure パラメーターで渡し、出力やログへ記録しません。')
@secure()
param clientSecret string
@description('APIMのマネージドIDが属するAzureテナントのID。接続のaccess policyに使用します。')
param tenantId string = subscription().tenantId
@description('接続先 OAuth アプリが所属するテナント ID。基盤と異なる接続先にも対応します。')
param oauthTenantId string = tenantId
@description('外部サービスに要求する OAuth scope の文字列。本人が同意した範囲で接続します。')
param scopes string = ''
@description('接続先が要求するリソース識別 URI。対象プロバイダーで必要な場合に指定します。')
param resourceUri string = ''
@description('本人認証・同意を開始する接続先 OAuth の authorization endpoint。')
param authorizationUrl string = ''
@description('authorization code をトークンへ交換する接続先 endpoint。')
param tokenUrl string = ''
@description('更新トークンで再取得する接続先 endpoint。プロバイダー定義に従って指定します。')
param refreshUrl string = ''
@description('OAuth 同意後に戻る APIM の URL。事前作成する Entra アプリにも同じ URI を登録します。')
param redirectUrl string = 'https://authorization-manager.consent.azure-apim.net/redirect/apim/${apimName}'
@description('作成する本人接続の名前。空の定義を作り、実際の本人認証は後から実施します。')
param connectionNames string[] = []

// 接続を保持する既存 APIM を参照します。
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

var authorizationCodeParameters = identityProvider == 'aad' ? {
  clientId: clientId
  clientSecret: clientSecret
  tenantId: oauthTenantId
  resourceUri: resourceUri
  scopes: scopes
} : {
  clientId: clientId
  clientSecret: clientSecret
  scopes: scopes
  authorizationUrl: authorizationUrl
  tokenUrl: tokenUrl
  refreshUrl: refreshUrl
}

// Stable API uses a secure parameter for clientSecret. keyVault.secretIdentifier
// belongs to later preview APIs and must not be invented on the stable contract.
// OAuth アプリと接続先 endpoint の定義。本人接続の token は APIM の Credential Manager が管理します。
resource provider 'Microsoft.ApiManagement/service/authorizationProviders@2024-05-01' = {
  parent: apim
  name: providerName
  properties: {
    displayName: displayName
    identityProvider: identityProvider
    oauth2: {
      redirectUrl: redirectUrl
      grantTypes: {
        authorizationCode: authorizationCodeParameters
      }
    }
  }
}

// Omit status/error/token fields: only the OAuth provider's consent flow can
// establish or recover a connected authorization-code connection.
// 接続名ごとの定義を作成。本人が同意する前は接続を利用できません。
resource connections 'Microsoft.ApiManagement/service/authorizationProviders/authorizations@2024-05-01' = [for connectionName in connectionNames: {
  parent: provider
  name: connectionName
  properties: {
    authorizationType: 'OAuth2'
    oauth2grantType: 'AuthorizationCode'
  }
}]

// This permits APIM to retrieve credentials; API policy must separately resolve
// the current verified owner/caller and reject stopped or mismatched bindings.
// 対象 APIM の MI だけが定義済み接続を利用できるよう、接続単位で許可します。
resource apimAccessPolicies 'Microsoft.ApiManagement/service/authorizationProviders/authorizations/accessPolicies@2024-05-01' = [for (connectionName, index) in connectionNames: {
  parent: connections[index]
  name: apimPrincipalId
  properties: {
    objectId: apimPrincipalId
    tenantId: tenantId
  }
}]

output providerId string = provider.id
output connectionIds array = [for (connectionName, index) in connectionNames: connections[index].id]
output accessPolicyIds array = [for (connectionName, index) in connectionNames: apimAccessPolicies[index].id]
output oauthRedirectUrl string = redirectUrl
