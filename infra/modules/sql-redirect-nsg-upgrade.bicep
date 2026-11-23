targetScope = 'resourceGroup'

@description('Protected construction output. This module updates four existing NSG children only.')
param resourcePrefix string
@description('Verified construction subnet CIDRs, management NIC IP and all SQL PE NIC IPs.')
param bindings object
@allowed(['Redirect', 'Proxy'])
@description('Internal migration phase. Proxy is used only after policy rollback and physical connection drain.')
param targetMode string = 'Redirect'

var current = loadJsonContent('../nsg-rule-priorities.json')
var retired = loadJsonContent('../sql-proxy-nsg-retirement.json')
var groups = items(retired.groups)
var rules = flatten(map(groups, group => map(items(group.value.rules), old => {
  name: 'nsg-${resourcePrefix}-${current.groups[group.key].suffix}/${targetMode == 'Redirect' ? old.value.replacementName : old.key}'
  rule: targetMode == 'Redirect' ? current.groups[group.key].rules[old.value.replacementName] : old.value
})))

func addresses(tokens array, bindings object) array => flatten(map(tokens, token => startsWith(token, 'subnet:') ? [bindings.subnets[substring(token, 7)]] : startsWith(token, 'ip:') ? ['${bindings.ips[substring(token, 3)]}/32'] : map(bindings.ipSets[substring(token, 4)], ip => '${ip}/32')))

// Existing parents, defaults, Deny, associations and routes are not PUT or flushed.
// A fixed four-iteration loop prevents an unrelated NoChange entry in what-if
// from authorizing any fifth child PUT if a catalog is accidentally expanded.
@batchSize(1)
resource sqlRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for index in range(0, 4): {
  name: rules[index].name
  properties: union({
    description: 'Initial fixed SQL communication'
    priority: rules[index].rule.priority
    direction: rules[index].rule.direction
    access: rules[index].rule.access
    protocol: rules[index].rule.protocol
    sourcePortRange: rules[index].rule.sourcePort
    destinationPortRange: rules[index].rule.destinationPorts[0]
  }, length(addresses(rules[index].rule.source, bindings)) == 1 ? {
    sourceAddressPrefix: addresses(rules[index].rule.source, bindings)[0]
  } : {
    sourceAddressPrefixes: addresses(rules[index].rule.source, bindings)
  }, length(addresses(rules[index].rule.destination, bindings)) == 1 ? {
    destinationAddressPrefix: addresses(rules[index].rule.destination, bindings)[0]
  } : {
    destinationAddressPrefixes: addresses(rules[index].rule.destination, bindings)
  })
}]
