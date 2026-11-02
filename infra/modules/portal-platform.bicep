// Coreの共用実行基盤（CAE）とPortalのアプリ本体（CA）をサービスRGへ配置します。
// VNet・DNS・通信規則はネットワークRGのモジュールが準備します。
targetScope = 'resourceGroup'
@description('hookがサービスRGのCAを照合した結果。初回だけ初期イメージで作成します。')
param existingPortalApp bool = false
param stem string
param location string
param tags object
param subnetId string
param workspaceId string
param registryName string
param pullIdentityId string

var applicationName = 'ca-${stem}-portal'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-${stem}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { infrastructureSubnetId: subnetId, internal: true }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: { destination: 'azure-monitor' }
  }
}
// CA 自体は基盤構築時に用意します。初回は Azure CLI も使う Microsoft の
// 初期イメージで起動し、Portal 本体を配置するときに同じ CA のイメージを更新します。
// 初期画面までの疎通を確認できるよう、Ingress を初期イメージの HTTP:80 へ向けます。
// 本体の配備時には apps/portal/deploy/application.bicep が同じ CA の接続ポートを 3000 へ更新します。
// 既存 CA は hook が確認し、このリソースへの PUT を省いて配置済みアプリを保持します。
resource app 'Microsoft.App/containerApps@2025-01-01' = if (!existingPortalApp) {
  name: applicationName
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${pullIdentityId}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 80, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: pullIdentityId }]
    }
    template: {
      containers: [{
        name: 'portal'
        image: 'mcr.microsoft.com/k8se/quickstart:latest'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'portal-logs'
  properties: {
    workspaceId: workspaceId
    logs: [{ category: 'ContainerAppConsoleLogs', enabled: true }, { category: 'ContainerAppSystemLogs', enabled: true }]
  }
}

output staticIp string = environment.properties.staticIp
output defaultDomain string = environment.properties.defaultDomain
output appHost string = '${applicationName}.${environment.properties.defaultDomain}'
output platform object = {
  applicationName: applicationName
  applicationId: resourceId('Microsoft.App/containerApps', applicationName)
  environmentName: environment.name
  environmentId: environment.id
  privateIp: environment.properties.staticIp
  applicationHost: '${applicationName}.${environment.properties.defaultDomain}'
  resourceGroup: resourceGroup().name
  applicationResourceGroup: resourceGroup().name
  environmentResourceGroup: resourceGroup().name
}
