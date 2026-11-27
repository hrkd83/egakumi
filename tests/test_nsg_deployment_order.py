"""Verify compiled NSG writes have an ordering path; use synthetic names only."""
import copy
import itertools
import unittest

from bicep_helpers import compile_bicep
from test_nsg_rule_priorities import FixedExpression


MODULES = ('portal-network', 'portal-network-bindings', 'credential-broker-network')
NSG_NAMES = {name: 'nsg-synthetic-' + name for name in (
    'agwNsgName', 'apimNsgName', 'pepNsgName', 'managementNsgName', 'acaNsgName', 'brokerNsgName')}


def ancestors(resources, name):
    """Follow symbolic compiled dependencies, rejecting cycles and missing nodes."""
    found = set()

    def visit(current, path):
        if current in path:
            raise AssertionError('Deployment dependency cycle')
        for dependency in resources[current].get('dependsOn', []):
            if dependency not in resources:
                raise AssertionError('Unknown compiled dependency')
            if dependency not in found:
                visit(dependency, path | {current})
                found.add(dependency)

    visit(name, set())
    return found


def dependency_graph(template):
    """Accept symbolic ARM templates and resolve classic ARM dependency expressions."""
    resources = template['resources']
    if isinstance(resources, dict):
        return resources, resources
    nodes = {'node-' + str(i): item for i, item in enumerate(resources)}

    def expression(value):
        return FixedExpression(value, template).tree if value.startswith('[') else ('literal', value)

    def subtrees(tree):
        yield tree
        if isinstance(tree, (tuple, list)):
            for item in tree:
                if isinstance(item, (tuple, list)):
                    yield from subtrees(item)

    names = {key: expression(resource['name']) for key, resource in nodes.items()}
    graph = {}
    for key, resource in nodes.items():
        dependencies = set()
        for dependency in resource.get('dependsOn', []):
            parts = list(subtrees(expression(dependency)))
            matches = {name for name, tree in names.items() if tree in parts}
            if not matches:
                raise AssertionError('Unknown compiled dependency expression')
            dependencies.update(matches)
        graph[key] = {'dependsOn': sorted(dependencies)}
    return graph, nodes


def same_template(left, right):
    if not isinstance(left, dict):
        return False
    left, right = copy.deepcopy(left), copy.deepcopy(right)
    left.pop('metadata', None)
    right.pop('metadata', None)
    return left == right


class NsgDeploymentOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {name: compile_bicep(f'infra/modules/network/{name}.bicep')
                         for name in MODULES}
        cls.main = compile_bicep('infra/main.bicep')

    def parents(self, template):
        result = {}
        resolve = FixedExpression('[true()]', template).resolve
        for symbol, resource in template['resources'].items():
            if resource['type'].casefold() == 'microsoft.network/networksecuritygroups/securityrules':
                name = resolve(resource['name'], NSG_NAMES)
                result[symbol] = name.rsplit('/', 1)[0]
        return result

    def test_sibling_writes_to_the_same_nsg_are_ordered(self):
        for module, template in self.templates.items():
            parents = self.parents(template)
            for left, right in itertools.combinations(parents, 2):
                if parents[left] == parents[right]:
                    with self.subTest(module=module, left=left, right=right):
                        self.assertTrue(left in ancestors(template['resources'], right)
                                        or right in ancestors(template['resources'], left))

    def test_aca_terminal_rules_wait_for_the_sql_private_endpoint_and_firewall(self):
        resources = self.templates['credential-broker-network']['resources']
        for group in ('aca', 'broker'):
            with self.subTest(group=group):
                ingress, egress = group + 'IngressDeny', group + 'EgressDeny'
                self.assertIn(ingress, ancestors(resources, egress))
                self.assertTrue({group + 'SqlOut', 'sqlIn', 'rules'} <= ancestors(resources, ingress))

    def test_modules_writing_to_the_same_nsg_are_ordered(self):
        graph, nodes = dependency_graph(self.main)
        symbols = {}
        for module, template in self.templates.items():
            matches = [key for key, node in nodes.items()
                       if same_template(node.get('properties', {}).get('template'), template)]
            self.assertEqual(len(matches), 1, module)
            symbols[module] = matches[0]
        parents = {name: set(self.parents(template).values())
                   for name, template in self.templates.items()}
        for left, right in itertools.combinations(MODULES, 2):
            if parents[left] & parents[right]:
                with self.subTest(left=left, right=right):
                    self.assertTrue(symbols[left] in ancestors(graph, symbols[right])
                                    or symbols[right] in ancestors(graph, symbols[left]))


if __name__ == '__main__':
    unittest.main()
