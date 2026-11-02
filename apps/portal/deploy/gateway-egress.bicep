// Portalのサーバー処理が、選択した環境の公開GatewayだけをHTTPSで呼べるようにします。
targetScope = 'resourceGroup'
param stem string
param acaPrefix string
param gatewayOrigin string

resource policy 'Microsoft.Network/firewallPolicies@2025-01-01' existing = {
  name: 'afwp-${stem}'
}
// 基盤のrcg-portalやGateway用規則を置き換えず、Portal配備がこのグループだけを所有します。
resource rules 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2025-01-01' = {
  parent: policy
  name: 'rcg-portal-gateway'
  properties: {
    priority: 360
    ruleCollections: [{
      name: 'portal-gateway'
      priority: 100
      ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
      action: { type: 'Allow' }
      rules: [{
        name: 'aca-public-gateway-https'
        ruleType: 'ApplicationRule'
        sourceAddresses: [acaPrefix]
        protocols: [{ protocolType: 'Https', port: 443 }]
        targetFqdns: [split(gatewayOrigin, '/')[2]]
        terminateTLS: false
      }]
    }]
  }
}
