// Azure をまだ作っていない段階で、Entra アプリに登録する戻り先 URI を計算する資材です。
// using none は Bicep の資源テンプレートへ直接適用せず、値の計算だけを行う指定です。
// 変数は事前準備スクリプトが環境変数として渡します。資格情報は使いません。
// main.bicep と同じ命名式を使うため、azd up の後に URI を修正する必要がない構成にします。

using none

// main.bicep と同じ「環境名-製品名」を使います。Subscription 由来の接尾辞や
// APIM だけの回避名を加えず、Azure 作成前に戻り先を確定できます。
var stem = readEnvironmentVariable('WORKFORCE_REGISTRATION_RESOURCE_STEM')
var apiCenterLocation = readEnvironmentVariable('WORKFORCE_REGISTRATION_API_CENTER_LOCATION')
var publicHostName = readEnvironmentVariable('WORKFORCE_REGISTRATION_PUBLIC_HOST_NAME')
var apimName = 'apim-${stem}'
var apiCenterName = 'apic-${stem}'

// 出力先へ返すのは公開名と redirect URI だけです。実リソースの作成や Entra 登録は行いません。
param registration = {
  resourceStem: stem
  apimName: apimName
  apiCenterName: apiCenterName
  gatewayOrigin: 'https://${publicHostName}'
  credentialManagerRedirectUri: 'https://authorization-manager.consent.azure-apim.net/redirect/apim/${apimName}'
  apiCenterPortalRedirectUri: 'https://${apiCenterName}.portal.${apiCenterLocation}.azure-apicenter.ms'
}
