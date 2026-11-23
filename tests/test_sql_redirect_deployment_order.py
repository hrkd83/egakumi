"""Verify SQL Redirect has one owner and follows PE/network deployment."""
import unittest

from bicep_helpers import compile_bicep
from test_nsg_deployment_order import ancestors, dependency_graph, same_template


class SqlRedirectDeploymentOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = compile_bicep('infra/main.bicep')
        cls.network = compile_bicep('infra/modules/credential-broker-network.bicep')
        cls.policy = compile_bicep('infra/modules/shared-sql-connection-policy.bicep')
        cls.client = compile_bicep('infra/modules/management-maintenance-client.bicep')

    def module(self, nodes, template):
        matches = [key for key, node in nodes.items()
                   if same_template(node.get('properties', {}).get('template'), template)]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_policy_waits_for_sql_endpoint_dns_and_nsg_module(self):
        graph, nodes = dependency_graph(self.core)
        policy = self.module(nodes, self.policy)
        network = self.module(nodes, self.network)
        self.assertIn(network, ancestors(graph, policy))
        self.assertEqual(nodes[policy].get('condition'), nodes[network].get('condition'))

    def test_management_client_preparation_waits_for_the_final_sql_policy(self):
        graph, nodes = dependency_graph(self.core)
        policy = self.module(nodes, self.policy)
        client = self.module(nodes, self.client)
        self.assertIn(policy, ancestors(graph, client))

    def test_core_has_one_policy_owner_without_a_routine_proxy_toggle(self):
        policies = []

        def collect(template):
            resources = template.get('resources', {})
            for resource in resources.values() if isinstance(resources, dict) else resources:
                if resource['type'].casefold() == 'microsoft.sql/servers/connectionpolicies':
                    policies.append(resource)
                properties = resource.get('properties', {})
                nested = properties.get('template') if isinstance(properties, dict) else None
                if isinstance(nested, dict):
                    collect(nested)

        collect(self.core)
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0]['properties']['connectionType'], 'Redirect')


if __name__ == '__main__':
    unittest.main()
