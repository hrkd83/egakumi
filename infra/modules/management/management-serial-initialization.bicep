// 正式CLIが基盤構築の完了後に使う初期ローカル認証資材です。
// VM UUIDとSAMI/tenantを保護pending記録へ確定した後、元の初期世代だけを指定します。
targetScope = 'resourceGroup'
param vmName string
param location string
param tags object
param expected object
@allowed(['initial', 'recover'])
param operation string
@secure()
param initialPassword string

var guestScript = replace(loadTextContent('../../guest/initialize-management-serial.py'), '\r\n', '\n')

resource vm 'Microsoft.Compute/virtualMachines@2024-11-01' existing = { name: vmName }
resource initialization 'Microsoft.Compute/virtualMachines/runCommands@2024-11-01' = {
  parent: vm
  name: 'initialize-serial-${expected.credentialGeneration}'
  location: location
  tags: tags
  properties: {
    source: { script: '#!/bin/sh\nset -eu\nexec /usr/bin/python3 -I -B - <<\'CORE_SERIAL_INITIALIZATION\'\n${guestScript}\nCORE_SERIAL_INITIALIZATION\n' }
    parameters: [
      { name: 'CORE_SERIAL_EXPECTED', value: string(expected) }
      { name: 'CORE_SERIAL_MODE', value: operation }
    ]
    // Linux named parameters become environment variables, never argv.
    protectedParameters: [{ name: 'CORE_SERIAL_PASSWORD', value: initialPassword }]
    asyncExecution: false
    timeoutInSeconds: 180
    treatFailureAsDeploymentFailure: true
  }
}
output commandId string = initialization.id
