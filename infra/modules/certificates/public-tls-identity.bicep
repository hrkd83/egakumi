// AppGW と必要に応じて APIM が証明書等を読むためのユーザー割り当てマネージド ID を作ります。
// クライアント向け Gateway API の Entra アプリとは役割が異なります。リソースと一緒に azd up で作成します。

targetScope = 'resourceGroup'
@description('製品名と環境名を連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object

// azd up creates this UAMI for the Application Gateway certificate reader.
// APIM, Foundry, Project and management VM use SystemAssigned identities.
// 証明書 secret 等の閲覧にだけ使う共有 ID。実行 API のクライアント ID には使いません。
resource certificateIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${resourceStem}-certificate'
  location: location
  tags: tags
}
output id string = certificateIdentity.id
output principalId string = certificateIdentity.properties.principalId
output clientId string = certificateIdentity.properties.clientId
