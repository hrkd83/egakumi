targetScope = 'resourceGroup'
param vmName string
param location string
param tags object
param config object
param executionId string = utcNow()
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: vmName }
resource issue 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: vm
  name: 'initialize-portal-auth-certificate'
  location: location
  tags: union(tags, { portalAuthExecution: executionId })
  properties: {
    source: { script: replace(replace(loadTextContent('../guest/portal-auth-certificate-bootstrap.sh'), '\r\n', '\n'), '__PORTAL_AUTH_WORKER_BASE64__', base64(replace(loadTextContent('../guest/portal-auth-certificate.ps1'), '\r\n', '\n'))) }
    parameters: [{ name: 'PortalAuthConfigJson', value: string(config) }]
    asyncExecution: false
    timeoutInSeconds: 900
    treatFailureAsDeploymentFailure: true
  }
}
output runCommandId string = issue.id
