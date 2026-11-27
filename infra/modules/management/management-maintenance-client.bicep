// 構築出力の対象だけを扱う保守者用clientを準備します。通信・認可の成功試験は別工程です。
targetScope = 'resourceGroup'
param vmName string
param location string
param tags object = {}
@secure()
@description('構築したVM・App・Job・環境・監視先・監査コンテナーの内部inventory。秘密tokenは含めません。')
param inventory object

var clientSource = replace(loadTextContent('../../../scripts/management_client.py'), '\r\n', '\n')
var requirements = loadTextContent('../../../scripts/maintenance-requirements.txt')
var installer = replace(loadTextContent('../../guest/prepare-management-client.sh'), '\r\n', '\n')
var script = replace(replace(replace(installer, '__CLIENT_SOURCE_BASE64__', base64(clientSource)), '__CLIENT_REQUIREMENTS_BASE64__', base64(requirements)), '__CLIENT_INVENTORY_BASE64__', base64(string(inventory)))
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: vmName }
resource client 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: vm
  name: 'prepare-maintenance-client'
  location: location
  tags: tags
  properties: {
    source: { script: script }
    asyncExecution: false
    timeoutInSeconds: 1800
    treatFailureAsDeploymentFailure: true
  }
}
