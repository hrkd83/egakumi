// プライベート IP のみを持つ Linux 管理 VM を作成します。
// 証明書の初回取得と継続更新、内部からの保守操作に使い、業務の実行層を同居させません。
// SSH 公開鍵と Entra SSH に対応します。接続元・ポートの制限はネットワーク側が担当します。

targetScope = 'resourceGroup'

@minLength(1)
@description('全リソース名の製品名部分。例は egakumi です。')
param productName string
@minLength(1)
@description('同じ製品内の環境を区別する名前。test / dev / prod など。名前自体で設定は切り替えません。')
param environmentName string
@description('環境名と製品名をこの順で連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string = '${environmentName}-${productName}'
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}
@minLength(1)
@description('この資源の NIC や内部入口を接続するサブネットの完全なリソース ID。')
param subnetId string
@minLength(1)
@description('Linux の Syslog と性能値を収集する DCR の ID。VM の AMA に関連付けます。')
param linuxDataCollectionRuleId string
@description('Linux 管理 VM のサイズ。リージョンの利用枠とディスク・NIC の対応を事前確認します。')
param vmSize string = 'Standard_D2s_v5'
@description('Ubuntu 24.04 LTS x64 Gen2 のイメージ指定。OS と証明書ランタイムの対応をそろえます。')
param imageReference object
@allowed(['SCSI', 'NVMe'])
@description('VM のディスク接続方式（SCSI / NVMe）。選択サイズとイメージの対応が必要です。')
param diskControllerType string = 'SCSI'
@minValue(128)
@description('管理 VM の OS ディスク容量（GiB）。証明書状態と運用ツールの領域を含みます。')
param osDiskSizeGB int = 128
@allowed(['Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS'])
@description('管理 VM の OS ディスク SKU。保持中は VM 停止後もディスク料金が発生します。')
param osDiskSku string = 'StandardSSD_LRS'
@description('対応 VM サイズで高速ネットワークを有効にするか。')
param enableAcceleratedNetworking bool = true
@description('対応サイズでホスト上のディスク暗号化を有効にするか。')
param encryptionAtHost bool = true
@minLength(1)
@maxLength(32)
@description('管理 VM の初期 Linux 管理ユーザー名。root の直接ログインは使用しません。')
param adminUsername string
@minLength(1)
@description('管理 VM の SSH 公開鍵。秘密鍵は渡さず、手元で安全に保持します。')
param sshPublicKey string
@description('VM ログインの組み込み Azure ロールを付与する対象。一般ログインか管理者ログインかを指定します。')
param loginAssignments array = []
@description('Azure DNS更新用の事前作成済みユーザー割り当てMIの完全なリソースID。権限はDNS側で事前付与します。')
param dnsManagedIdentityResourceId string = ''
@allowed(['ImageDefault', 'AutomaticByPlatform'])
@description('Azure による OS パッチ評価方式。使用イメージで対応する設定を指定します。')
param patchAssessmentMode string = 'ImageDefault'
@allowed(['ImageDefault', 'AutomaticByPlatform'])
@description('OS パッチ適用方式。ImageDefault など、運用方針とイメージに適合する値を指定します。')
param patchMode string = 'ImageDefault'

// Linux 内のホスト名にも同じ環境名・製品名を使い、Azure 上の VM と対応させます。
var computerName = 'mgmt-${resourceStem}'
var vmLoginUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'fb879df8-f326-4884-b1cf-06f3ad86be52')
var vmLoginAdminRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1c0163c0-47e6-4577-8991-ea5c82e286e4')

// 管理 VM のプライベート NIC。Public IP を付けず、外向きはサブネットの UDR を使います。
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-${resourceStem}-mgmt'
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: enableAcceleratedNetworking
    enableIPForwarding: false
    ipConfigurations: [{
      name: 'ipconfig-primary'
      properties: {
        primary: true
        privateIPAllocationMethod: 'Dynamic'
        privateIPAddressVersion: 'IPv4'
        subnet: { id: subnetId }
      }
    }]
  }
}

// Trusted Launch の Linux 管理 VM。SSH 秘密鍵はクラウドに渡しません。
resource vm 'Microsoft.Compute/virtualMachines@2024-11-01' = {
  name: 'vm-${resourceStem}-mgmt'
  location: location
  tags: tags
  identity: empty(dnsManagedIdentityResourceId) ? { type: 'SystemAssigned' } : {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: { '${dnsManagedIdentityResourceId}': {} }
  }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    securityProfile: {
      securityType: 'TrustedLaunch'
      encryptionAtHost: encryptionAtHost
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    osProfile: {
      computerName: computerName
      adminUsername: adminUsername
      allowExtensionOperations: true
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: { publicKeys: [{ path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: sshPublicKey }] }
        patchSettings: { patchMode: patchMode, assessmentMode: patchAssessmentMode }
      }
    }
    storageProfile: {
      diskControllerType: diskControllerType
      imageReference: imageReference
      osDisk: {
        name: 'osdisk-${resourceStem}-mgmt'
        osType: 'Linux'
        createOption: 'FromImage'
        caching: 'ReadWrite'
        diskSizeGB: osDiskSizeGB
        managedDisk: { storageAccountType: osDiskSku }
        deleteOption: 'Delete'
      }
      dataDisks: []
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id, properties: { primary: true, deleteOption: 'Delete' } }]
    }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

// Entra SSH ログイン拡張を追加。実際のログイン可否は Azure RBAC の VM ロールで制御します。
resource entraLogin 'Microsoft.Compute/virtualMachines/extensions@2024-11-01' = {
  parent: vm
  name: 'AADSSHLoginForLinux'
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADSSHLoginForLinux'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    settings: {}
  }
}

// Linux Azure Monitor Agent。DCR で指定した Syslog と性能値を送ります。
resource monitorAgent 'Microsoft.Compute/virtualMachines/extensions@2024-11-01' = {
  parent: vm
  name: 'AzureMonitorLinuxAgent'
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
    settings: {}
  }
}

// VM と Linux DCR の関連付け。エージェントを入れるだけでは収集設定は適用されません。
resource collectionAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  scope: vm
  name: '${resourceStem}-linux-monitor'
  properties: { dataCollectionRuleId: linuxDataCollectionRuleId }
}

// 指定ユーザー等へ VM 単位のログイン権限を付与。サブスクリプションの Owner は付けません。
resource logonGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for assignment in loginAssignments: {
  scope: vm
  name: guid(vm.id, assignment.principalId, assignment.role)
  properties: {
    principalId: assignment.principalId
    principalType: assignment.principalType
    roleDefinitionId: assignment.role == 'Administrator' ? vmLoginAdminRoleId : vmLoginUserRoleId
  }
}]

output managementVm object = {
  id: vm.id
  name: vm.name
  principalId: vm.identity.principalId
  nicId: nic.id
  privateIp: nic.properties.ipConfigurations[0].properties.privateIPAddress
  computerName: computerName
  osDiskId: vm.properties.storageProfile.osDisk.managedDisk.id
}
