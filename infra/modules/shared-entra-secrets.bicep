// 旧構成から更新する場合に、本人確認で使用中のCM secret参照を維持します。
targetScope = 'resourceGroup'
param vaultName string
param profiles object
@secure()
param providerSecrets object
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for name in objectKeys(profiles): {
  parent: vault
  name: 'core-credential-${name}'
  properties: { value: providerSecrets[name], attributes: { enabled: true } }
}]
