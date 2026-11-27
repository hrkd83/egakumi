"""公開APIのURLを、承認済みの下流URLへ書き換える条件を検証する。

Bicepをローカルでコンパイルし、公開要求の任意queryや資格情報を下流URLへ混入させない構造を調べる。実AzureのAPIは呼ばない。
"""

import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import compiled_policy_value
from bicep_helpers import compile_bicep


CORE = Path(__file__).resolve().parents[1]


class BackendRewriteDeploymentTests(unittest.TestCase):
    """公開APIのURLを、承認済みの下流URLへ書き換える条件を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/api-gateway/api-publication.bicep')

    def test_compiled_operation_policy_keeps_auth_and_fixed_query_contract(self):
        definition = CORE / 'apps/api-gateway/apis/blackforest-images-v1.json'
        api = json.loads(definition.read_text(encoding='utf-8'))
        api.update(apiType='http', backendUrl='https://backend.example.invalid')
        relay = {'url': 'https://relay.example.invalid', 'audience': 'api://example-relay'}

        for audit_relay in ({}, relay):
            with self.subTest(body_audit=bool(audit_relay)):
                xml = compiled_policy_value(self.template, {'apiDefinitions': [api], 'auditRelay': audit_relay},
                                            resource_name='operationPolicies')
                policy = ET.fromstring(xml)
                inbound = list(policy.find('inbound'))
                outbound = list(policy.find('outbound'))
                audit_steps = ['set-variable', 'include-fragment'] if audit_relay else []
                # Inherit API-level authentication before choosing the registered model route.
                self.assertEqual([node.tag for node in inbound], ['base', 'choose', *audit_steps])
                self.assertEqual([node.tag for node in outbound], ['base', *audit_steps])
                routes = inbound[1].findall('when/rewrite-uri')
                self.assertEqual([node.attrib for node in routes], [
                    {'template': '/providers/blackforestlabs/v1/flux-2-flex?api-version=preview',
                     'copy-unmatched-params': 'false'},
                    {'template': '/providers/blackforestlabs/v1/flux-2-pro?api-version=preview',
                     'copy-unmatched-params': 'false'},
                ])
                for section in ('backend', 'on-error'):
                    self.assertEqual([node.tag for node in policy.find(section)], ['base'])
                if audit_relay:
                    for direction, nodes in (('request', inbound), ('response', outbound)):
                        self.assertEqual(nodes[-2].attrib, {'name': 'gatewayEvidenceDirection', 'value': direction})
                        self.assertEqual(nodes[-1].attrib, {'fragment-id': 'core-http-body-evidence'})

    def test_compiled_operation_policy_escapes_attributes_and_preserves_special_branches(self):
        resources = self.template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        operation_policy = next(resource for resource in resources
            if resource["type"] == "Microsoft.ApiManagement/service/apis/operations/policies"
            and resource.get("copy", {}).get("name") == "operationPolicies")
        self.assertIn('__bicep.operationPolicyValue(', operation_policy["properties"]["value"])
        functions = next(item for item in self.template["functions"] if item["namespace"] == "__bicep")
        expression = functions["members"]["operationPolicyValue"]["output"]["value"]
        for kind in ("pii", "realtimeCreate", "realtimeHangup"):
            self.assertIn(".constraint.kind, '" + kind + "'", expression)
        self.assertIn("__bicep.xmlText(parameters('entry').operation.backendUrlTemplate)", expression)
        escaping = functions["members"]["xmlText"]["output"]["value"]
        for pair in ("'&', '&amp;'", "'\"', '&quot;'", "'<', '&lt;'", "'>', '&gt;'"):
            self.assertIn(pair, escaping)


if __name__ == "__main__":
    unittest.main()
