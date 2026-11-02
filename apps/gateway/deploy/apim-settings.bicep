// Gateway配備で用意するAPIMの名前付きの値とBroker接続先です。APIポリシーは後続の公開工程で反映します。
targetScope = 'resourceGroup'

param names object
param authentication object
@description('AppGW HTTP 経路の識別値の Key Vault URI。値そのものは渡しません。')
#disable-next-line secure-secrets-in-params
param l7IngressSecretIdentifier string = ''
@description('Realtime 所有者チケット署名鍵の Key Vault URI。値そのものは渡しません。')
#disable-next-line secure-secrets-in-params
param realtimeTicketSecretIdentifier string = ''
param credentialBrokerUrl string = ''
#disable-next-line secure-secrets-in-params
param credentialBrokerSecretIdentifier string = ''

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: names.apim }

// 共通認可で受け付ける購読キーのヘッダー名を基盤で用意します。
resource keyHeaderNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'core-subscription-key-header'
  properties: { displayName: 'core-subscription-key-header', secret: false, value: authentication.subscriptionKeyHeader }
}
resource trustedL7NamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'core-l7-ingress-secret'
  properties: {
    displayName: 'core-l7-ingress-secret'
    secret: !empty(l7IngressSecretIdentifier)
    ...(!empty(l7IngressSecretIdentifier) ? { keyVault: { secretIdentifier: l7IngressSecretIdentifier } } : { value: 'ingress-disabled' })
  }
}

// 機能を後から有効化しても全体 policy の本文を書き直さないための基盤設定です。
resource ingressMode 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'core-l7-ingress-enabled'
  properties: { displayName: 'core-l7-ingress-enabled', value: empty(l7IngressSecretIdentifier) ? 'false' : 'true', secret: false }
}

resource realtimeTicketNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = if (!empty(realtimeTicketSecretIdentifier)) {
  parent: apim
  name: 'core-realtime-ticket-key'
  properties: {
    displayName: 'core-realtime-ticket-key'
    secret: true
    keyVault: { secretIdentifier: realtimeTicketSecretIdentifier }
  }
}

resource brokerUrl 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = if (!empty(credentialBrokerUrl)) {
  parent: apim
  name: 'core-credential-broker-url'
  properties: { displayName: 'core-credential-broker-url', secret: false, value: credentialBrokerUrl }
}
resource brokerKey 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = if (!empty(credentialBrokerSecretIdentifier)) {
  parent: apim
  name: 'core-credential-broker-secret'
  properties: { displayName: 'core-credential-broker-secret', secret: true, keyVault: { secretIdentifier: credentialBrokerSecretIdentifier } }
}
resource brokerBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = if (!empty(credentialBrokerUrl)) {
  parent: apim
  name: 'credential-broker'
  properties: { title: 'Core credential broker', protocol: 'http', url: credentialBrokerUrl, tls: { validateCertificateChain: true, validateCertificateName: true } }
}
