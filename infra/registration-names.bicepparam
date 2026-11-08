// Azure をまだ作っていない段階で、Entra アプリに登録する戻り先 URI を計算する資材です。
// using none は Bicep の資源テンプレートへ直接適用せず、値の計算だけを行う指定です。
// 変数は事前準備スクリプトが環境変数として渡します。資格情報は使いません。
// main.bicep と同じ命名式を使うため、azd up の後に URI を修正する必要がない構成にします。

using none

// main.bicep と同じ「環境名-製品名」と保存済みAPIM識別子を使います。
// Azure 作成前に戻り先を確定し、通常の更新では同じ名前を維持します。
var stem = readEnvironmentVariable('WORKFORCE_REGISTRATION_RESOURCE_STEM')
var apimInstanceId = readEnvironmentVariable('WORKFORCE_REGISTRATION_APIM_INSTANCE_ID')
var apiCenterLocation = readEnvironmentVariable('WORKFORCE_REGISTRATION_API_CENTER_LOCATION')
var publicHostName = readEnvironmentVariable('WORKFORCE_REGISTRATION_PUBLIC_HOST_NAME')
var apimName = 'apim-${stem}-${apimInstanceId}'
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
