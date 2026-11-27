targetScope = 'resourceGroup'

param nsgNames object
param groups array
param prefixes object
param ips object
param ipSets object

var catalog = loadJsonContent('../audit-evidence-nsg.json').groups

func bind(token string, prefixes object, ips object, ipSets object) array => startsWith(token, 'literal:') ? [substring(token, 8)] : startsWith(token, 'subnet:') ? [prefixes[substring(token, 7)]] : startsWith(token, 'ip:') ? ['${ips[substring(token, 3)]}/32'] : map(ipSets[substring(token, 4)], address => '${address}/32')

func ruleProperties(entry object, source array, destination array) object => union({
  priority: entry.priority
  direction: entry.direction
  access: entry.access
  protocol: entry.protocol
  sourcePortRange: entry.sourcePort
}, length(source) == 1 ? { sourceAddressPrefix: source[0] } : { sourceAddressPrefixes: source },
   length(destination) == 1 ? { destinationAddressPrefix: destination[0] } : { destinationAddressPrefixes: destination },
   length(entry.destinationPorts) == 1 ? { destinationPortRange: entry.destinationPorts[0] } : { destinationPortRanges: entry.destinationPorts })

var rules = flatten(map(groups, group => map(items(catalog[group].rules), rule => {
  name: '${nsgNames[group]}/${rule.key}'
  properties: ruleProperties(rule.value, flatten(map(rule.value.source, token => bind(token, prefixes, ips, ipSets))), flatten(map(rule.value.destination, token => bind(token, prefixes, ips, ipSets))))
})))

@batchSize(1)
resource allowRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for rule in filter(rules, entry => entry.properties.access == 'Allow'): {
  name: rule.name
  properties: rule.properties
}]

@batchSize(1)
resource denyRules 'Microsoft.Network/networkSecurityGroups/securityRules@2025-01-01' = [for rule in filter(rules, entry => entry.properties.access == 'Deny'): {
  name: rule.name
  properties: rule.properties
  dependsOn: [allowRules]
}]
