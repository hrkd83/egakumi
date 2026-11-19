@description('Internal deployment scope, derived from the protected construction configuration.')
param resourcePrefix string
param location string
param tags object
@description('Owned subnet CIDRs, allocated single IPs and every owned PE NIC IP, verified immediately before deployment.')
param bindings object
@description('Internal live inventory. Rule presence is fixed and is not a configuration switch.')
param existingNetworkSecurityGroupNames array

var catalog = loadJsonContent('../nsg-rule-priorities.json')
var groups = items(catalog.groups)
var missing = filter(groups, group => !contains(existingNetworkSecurityGroupNames, 'nsg-${resourcePrefix}-${group.value.suffix}'))

func resolveAddresses(tokens array, addresses object) array => flatten(map(tokens, token => startsWith(token, 'literal:') ? [substring(token, 8)] : startsWith(token, 'tag:') ? [substring(token, 4)] : startsWith(token, 'subnet:') ? [addresses.subnets[substring(token, 7)]] : startsWith(token, 'ip:') ? ['${addresses.ips[substring(token, 3)]}/32'] : map(addresses.ipSets[substring(token, 4)], ip => '${ip}/32')))

func ruleProperties(rule object, name string, addresses object) object => union({
  description: rule.access == 'Deny' ? 'Reject communication outside the fixed initial allow rules' : 'Initial fixed communication: ${name}'
  priority: rule.priority
  direction: rule.direction
  access: rule.access
  protocol: rule.protocol
  sourcePortRange: rule.sourcePort
}, length(rule.destinationPorts) == 1 ? {
  destinationPortRange: rule.destinationPorts[0]
} : {
  destinationPortRanges: rule.destinationPorts
}, length(resolveAddresses(rule.source, addresses)) == 1 ? {
  sourceAddressPrefix: resolveAddresses(rule.source, addresses)[0]
} : {
  sourceAddressPrefixes: resolveAddresses(rule.source, addresses)
}, length(resolveAddresses(rule.destination, addresses)) == 1 ? {
  destinationAddressPrefix: resolveAddresses(rule.destination, addresses)[0]
} : {
  destinationAddressPrefixes: resolveAddresses(rule.destination, addresses)
})

// No PUT is issued to an existing parent. The initial ACA parent is created
// before its fixed children; subnet association is a separate conditional PUT.
resource createdParents 'Microsoft.Network/networkSecurityGroups@2025-01-01' = [for group in missing: {
  name: 'nsg-${resourcePrefix}-${group.value.suffix}'
  location: location
  tags: tags
  properties: { securityRules: [] }
}]

var rules = flatten(map(groups, group => map(items(group.value.rules), rule => {
  name: 'nsg-${resourcePrefix}-${group.value.suffix}/${rule.key}'
  properties: ruleProperties(rule.value, rule.key, bindings)
})))

@batchSize(1)
resource fixedRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for rule in rules: {
  name: rule.name
  properties: rule.properties
  dependsOn: [createdParents]
}]
