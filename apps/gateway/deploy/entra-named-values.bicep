// Entra共通認証の設定を、API公開が上書きしない非秘密NamedValueとして構築します。
targetScope = 'resourceGroup'

param apimName string
param tenantId string
param entra object

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }

var settings = {
  'core-entra-tenant-id': tenantId
  'core-entra-api-audience': entra.audience
  'core-entra-required-scope': entra.requiredScope
  'core-entra-user-limits': string(entra.userLimits)
}

resource namedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for setting in items(settings): {
  parent: apim
  name: setting.key
  properties: { displayName: setting.key, secret: false, value: setting.value }
}]
