// 固定NSGルールのARMプロパティを組み立てます。
// 規則名・優先度・通信先・配置順は各配備モジュールが指定します。

@export()
func allowRule(ruleName string, priority int, direction string, protocol string, sources array, destinations array, ports array) object => {
  name: ruleName
  properties: union({
    description: 'Initial fixed communication: ${ruleName}'
    priority: priority
    direction: direction
    access: 'Allow'
    protocol: protocol
    sourcePortRange: '*'
  }, length(sources) == 1 ? {
    sourceAddressPrefix: sources[0]
  } : {
    sourceAddressPrefixes: sources
  }, length(destinations) == 1 ? {
    destinationAddressPrefix: destinations[0]
  } : {
    destinationAddressPrefixes: destinations
  }, length(ports) == 1 ? {
    destinationPortRange: ports[0]
  } : {
    destinationPortRanges: ports
  })
}

@export()
func denyProperties(direction string, priority int) object => {
  description: 'Reject communication outside the fixed initial allow rules'
  priority: priority
  direction: direction
  access: 'Deny'
  protocol: '*'
  sourcePortRange: '*'
  destinationPortRange: '*'
  sourceAddressPrefix: '*'
  destinationAddressPrefix: '*'
}
