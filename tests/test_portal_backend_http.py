"""既存HTTPSを保持した段階移行と、新規構築のHTTP80固定契約を合成ARMで検証する。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers.portal_backend_http import (
    ensure_portal_firewall_port, portal_ids, require_portal_http_core_ready,
    rollback_portal_backend, verify_portal_http_ready,
)
from deployment.helpers.portal_gateway import gateway_id
from test_portal_gateway import fixture as gateway_fixture

CONFIG = {
    'subscriptionId': '11111111-1111-4111-8111-111111111111', 'resourceStem': 'test-fixture',
    'resourceGroupNames': {'nw': 'rg-test-fixture-nw', 'svc': 'rg-test-fixture-svc'},
    'services': {'apimInstanceId': 'abc123'},
    'network': {'subnetPrefixes': {'aca': '10.40.2.192/26', 'agw': '10.40.0.0/24'}},
}
DIGEST = 'sha256:' + 'a' * 64
IMAGE = 'acrtestfixture.azurecr.io/portal@' + DIGEST


def fixture():
    ids = portal_ids(CONFIG)
    revision = 'ca-test-fixture-portal--fixture'
    host = 'ca-test-fixture-portal.internal.example.invalid'
    def resource(target, kind, properties):
        return {'id': target, 'name': target.rsplit('/', 1)[-1], 'type': kind, 'etag': '"v1"',
                'properties': {'provisioningState': 'Succeeded', **properties}}
    gateway = gateway_fixture(['200'], '/api/health')
    gateway['properties']['backendAddressPools'] = [{
        'name': 'bepool-portal', 'properties': {'backendAddresses': [{'ipAddress': '10.40.2.196'}]}}]
    gateway['properties']['backendHttpSettingsCollection'][0]['properties'].update(
        hostName=host, pickHostNameFromBackendAddress=False)
    gateway['properties']['probes'][1]['properties'].update(
        host=host, pickHostNameFromBackendHttpSettings=False)
    ingress = {'name': 'portal-ingress', 'priority': 100, 'ruleCollectionType': 'FirewallPolicyFilterRuleCollection',
               'action': {'type': 'Allow'}, 'rules': [{
                   'name': 'agw-aca-https', 'ruleType': 'NetworkRule',
                   'sourceAddresses': ['10.40.0.0/24'], 'destinationAddresses': ['10.40.2.192/26'],
                   'destinationPorts': ['443'], 'ipProtocols': ['TCP']}]}
    return {
        ids['gateway']: gateway,
        ids['environment']: resource(ids['environment'], 'Microsoft.App/managedEnvironments', {
            'staticIp': '10.40.2.196', 'defaultDomain': 'internal.example.invalid',
            'vnetConfiguration': {'internal': True, 'infrastructureSubnetId': ids['subnet']}}),
        ids['app']: resource(ids['app'], 'Microsoft.App/containerApps', {
            'managedEnvironmentId': ids['environment'], 'latestRevisionName': revision,
            'latestReadyRevisionName': revision,
            'configuration': {'ingress': {'external': True, 'allowInsecure': True, 'transport': 'http', 'targetPort': 3000}},
            'template': {'containers': [{'name': 'portal', 'image': IMAGE}]}}),
        ids['app'] + '/revisions/' + revision: resource(ids['app'] + '/revisions/' + revision, 'Microsoft.App/containerApps/revisions', {
            'provisioningState': 'Provisioned', 'healthState': 'Healthy', 'runningState': 'Running',
            'template': {'containers': [{'name': 'portal', 'image': IMAGE}]}}),
        ids['firewall']: resource(ids['firewall'], 'Microsoft.Network/firewallPolicies/ruleCollectionGroups', {
            'priority': 350, 'ruleCollections': [ingress, {'name': 'unrelated', 'rules': [{'name': 'preserved'}]}]}),
    }


class Transport:
    def __init__(self, bodies=None):
        self.bodies = fixture() if bodies is None else bodies
        self.calls = []
        self.fail_put = False
        self.normalize_firewall = False
        self.resize_firewall = False
        self.alter_unrelated = False

    def request(self, method, path, body=None, headers=None):
        target = path.split('?', 1)[0]
        self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': headers})
        if method == 'GET':
            return {'statusCode': 200, 'body': copy.deepcopy(self.bodies[target])} if target in self.bodies else {'statusCode': 404}
        if self.fail_put:
            return {'statusCode': 412, 'body': None}
        original = self.bodies[target]
        updated = copy.deepcopy(body)
        updated.update({key: original[key] for key in ('id', 'name', 'type')})
        updated['etag'] = '"v2"'
        updated['properties']['provisioningState'] = 'Succeeded'
        if 'firewallPolicies' in target:
            if self.resize_firewall:
                updated['properties']['size'] = '2 KB'
            if self.normalize_firewall:
                for rule in updated['properties']['ruleCollections'][0]['rules']:
                    rule.update(sourceIpGroups=[], destinationIpGroups=[], destinationFqdns=[], ipv6Rule=False)
            if self.alter_unrelated:
                updated['properties']['ruleCollections'][1]['rules'][0]['destinationPorts'] = ['443']
        self.bodies[target] = updated
        return {'statusCode': 202, 'body': None}

    def writes(self):
        return [call for call in self.calls if call['method'] != 'GET']


class PortalHttpMigrationTests(unittest.TestCase):
    def test_prepare_excludes_computed_group_size_and_accepts_its_readback_change(self):
        transport, records = Transport(), []
        transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['size'] = '1 KB'
        transport.resize_firewall = True
        result = ensure_portal_firewall_port(CONFIG, transport, execute=True,
                                            record=lambda event, value: records.append((event, value)))
        self.assertTrue(result['ruleAdded'])
        self.assertNotIn('size', transport.writes()[0]['body']['properties'])
        self.assertEqual(records[0][1]['properties']['size'], '1 KB')
        self.assertEqual(transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['size'], '2 KB')

    def test_prepare_adds_http_while_retaining_https_and_other_rules_then_is_idempotent(self):
        transport, records = Transport(), []
        before = copy.deepcopy(transport.bodies[portal_ids(CONFIG)['firewall']])
        result = ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda e, v: records.append((e, v)))
        self.assertTrue(result['ruleAdded'])
        self.assertTrue(result['existingOtherPortRetained'])
        body = transport.writes()[0]['body']
        self.assertEqual(body['properties']['ruleCollections'][1], before['properties']['ruleCollections'][1])
        self.assertEqual(body['properties']['ruleCollections'][0]['rules'][0], before['properties']['ruleCollections'][0]['rules'][0])
        self.assertEqual(body['properties']['ruleCollections'][0]['rules'][1]['name'], 'allow-appgw-aca-http')
        self.assertEqual(body['properties']['ruleCollections'][0]['rules'][1]['destinationPorts'], ['80'])
        self.assertEqual(transport.writes()[0]['headers'], {'If-Match': '"v1"'})
        self.assertEqual(records[0][0], 'firewall-before')
        self.assertEqual(records[1][0], 'firewall-after')
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertEqual(len(transport.writes()), 1)

    def test_prepare_renames_legacy_http_without_duplicate_and_retains_other_rules(self):
        transport = Transport()
        collection = transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections'][0]
        legacy = copy.deepcopy(collection['rules'][0])
        legacy.update(name='agw-aca-http', destinationPorts=['80'], destinationFqdns=[], ipv6Rule=False)
        collection['rules'].append(legacy)
        before = copy.deepcopy(transport.bodies)
        ensure_portal_firewall_port(CONFIG, transport)
        self.assertEqual(transport.bodies, before)
        self.assertFalse(transport.writes())

        result = ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertFalse(result['ruleAdded'])
        self.assertTrue(result['ruleRenamed'])
        expected = copy.deepcopy(before[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections'])
        expected[0]['rules'][1]['name'] = 'allow-appgw-aca-http'
        self.assertEqual(transport.writes()[0]['body']['properties']['ruleCollections'], expected)
        self.assertEqual(transport.writes()[0]['headers'], {'If-Match': '"v1"'})
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertEqual(len(transport.writes()), 1)

    def test_new_and_legacy_http_names_support_both_read_only_readiness_gates(self):
        for name in ('allow-appgw-aca-http', 'agw-aca-http'):
            for retain_https in (False, True):
                with self.subTest(name=name, retain_https=retain_https):
                    transport = Transport()
                    ids = portal_ids(CONFIG)
                    rules = transport.bodies[ids['firewall']]['properties']['ruleCollections'][0]['rules']
                    http = copy.deepcopy(rules[0])
                    http.update(name=name, destinationPorts=['80'])
                    rules[:] = [http, rules[0]] if retain_https else [http]
                    gateway = transport.bodies[ids['gateway']]
                    gateway['properties']['backendHttpSettingsCollection'][0]['properties'].update(protocol='Http', port=80)
                    gateway['properties']['probes'][1]['properties'].update(protocol='Http', port=80)
                    before = copy.deepcopy(transport.bodies)
                    self.assertTrue(require_portal_http_core_ready(CONFIG, transport)['httpBackendVerified'])
                    self.assertTrue(verify_portal_http_ready(CONFIG, transport, expected_image_digest=DIGEST)['firewallHttpReady'])
                    self.assertEqual(transport.bodies, before)
                    self.assertFalse(transport.writes())

    def test_http_name_collision_or_duplicate_stops_prepare_gates_and_rollback_before_write(self):
        cases = (('allow-appgw-aca-http', 'agw-aca-http'),
                 ('allow-appgw-aca-http', 'allow-appgw-aca-http'), ('agw-aca-http', 'agw-aca-http'))
        operations = ((ensure_portal_firewall_port, {'execute': True, 'record': lambda *_: None}),
                      (require_portal_http_core_ready, {}),
                      (verify_portal_http_ready, {'expected_image_digest': DIGEST}),
                      (rollback_portal_backend, {'execute': True, 'record': lambda *_: None}))
        for names in cases:
            for operation, kwargs in operations:
                with self.subTest(names=names, operation=operation.__name__):
                    transport = Transport()
                    ids = portal_ids(CONFIG)
                    rules = transport.bodies[ids['firewall']]['properties']['ruleCollections'][0]['rules']
                    for name in names:
                        http = copy.deepcopy(rules[0])
                        http.update(name=name, destinationPorts=['80'])
                        rules.append(http)
                    gateway = transport.bodies[ids['gateway']]
                    gateway['properties']['backendHttpSettingsCollection'][0]['properties'].update(protocol='Http', port=80)
                    gateway['properties']['probes'][1]['properties'].update(protocol='Http', port=80)
                    before = copy.deepcopy(transport.bodies)
                    with self.assertRaisesRegex(ValueError, 'duplicated'):
                        operation(CONFIG, transport, **kwargs)
                    self.assertEqual(transport.bodies, before)
                    self.assertFalse(transport.writes())

    def test_both_http_names_require_the_exact_owned_network_rule_before_write(self):
        changes = (('sourceAddresses', ['Internet']), ('destinationAddresses', ['10.40.3.0/24']),
                   ('destinationPorts', ['443']), ('ipProtocols', ['UDP']),
                   ('ruleType', 'ApplicationRule'), ('sourcePorts', ['*']))
        for name in ('allow-appgw-aca-http', 'agw-aca-http'):
            for field, value in changes:
                with self.subTest(name=name, field=field):
                    transport = Transport()
                    rules = transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections'][0]['rules']
                    http = copy.deepcopy(rules[0])
                    http.update(name=name, destinationPorts=['80'])
                    http[field] = value
                    rules.append(http)
                    with self.assertRaisesRegex(ValueError, 'another source, destination or owner'):
                        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
                    self.assertFalse(transport.writes())

    def test_preparation_plan_and_missing_protected_record_do_not_write(self):
        transport = Transport()
        result = ensure_portal_firewall_port(CONFIG, transport)
        self.assertEqual(result['status'], 'planned')
        self.assertFalse(transport.writes())
        with self.assertRaisesRegex(ValueError, 'protected'):
            ensure_portal_firewall_port(CONFIG, transport, execute=True)
        self.assertFalse(transport.writes())

    def test_firewall_readback_accepts_only_empty_owned_ip_group_normalization(self):
        transport = Transport()
        transport.normalize_firewall = True
        result = ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertTrue(result['ruleAdded'])
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertEqual(len(transport.writes()), 1)

        transport = Transport()
        transport.normalize_firewall = True
        transport.alter_unrelated = True
        with self.assertRaisesRegex(ValueError, 'readback differs'):
            ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)

    def test_unused_fqdn_and_disabled_ipv6_are_preserved_in_snapshot_and_other_rules(self):
        transport, records = Transport(), []
        collections = transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections']
        for rule in (collections[0]['rules'][0], collections[1]['rules'][0]):
            rule.update(destinationFqdns=[], ipv6Rule=False)
        before = copy.deepcopy(transport.bodies[portal_ids(CONFIG)['firewall']]['properties'])
        transport.normalize_firewall = True
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda e, v: records.append((e, v)))
        self.assertEqual(records[0][1]['properties'], before)
        sent = transport.writes()[0]['body']['properties']['ruleCollections']
        self.assertEqual(sent[0]['rules'][0]['destinationFqdns'], [])
        self.assertIs(sent[0]['rules'][0]['ipv6Rule'], False)
        self.assertEqual(sent[1], before['ruleCollections'][1])

    def test_owned_fqdn_or_non_disabled_ipv6_is_rejected_before_write(self):
        for field, value in (('destinationFqdns', ['api.example.invalid']), ('ipv6Rule', True),
                             ('ipv6Rule', 0), ('ipv6Rule', None), ('ipv6Rule', 'false')):
            transport = Transport()
            transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections'][0]['rules'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
            self.assertFalse(transport.writes())

    def test_collision_or_etag_conflict_never_overwrites_other_configuration(self):
        for issue in ('source', 'etag-conflict', 'priority'):
            transport = Transport()
            collection = transport.bodies[portal_ids(CONFIG)['firewall']]['properties']['ruleCollections'][0]
            if issue == 'source':
                collection['rules'][0]['sourceAddresses'] = ['Internet']
            elif issue == 'priority':
                collection['priority'] = 99
            else:
                transport.fail_put = True
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
            self.assertEqual(len(transport.writes()), 1 if issue == 'etag-conflict' else 0)

    def test_invalid_etag_or_recorder_stops_before_any_firewall_or_rollback_write(self):
        for etag in ('*', '"*"', 'W/"*"', '""', '"one", "two"', '"bad\x7f"', 'x' * 513):
            transport = Transport()
            transport.bodies[portal_ids(CONFIG)['firewall']]['etag'] = etag
            with self.subTest(etag=etag), self.assertRaisesRegex(ValueError, 'ETag'):
                ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
            self.assertFalse(transport.writes())
        for recorder in (False, True, {}, [], 'recorder'):
            for operation in (ensure_portal_firewall_port, rollback_portal_backend):
                transport = Transport()
                with self.subTest(operation=operation.__name__, recorder=recorder), self.assertRaisesRegex(ValueError, 'recorder'):
                    operation(CONFIG, transport, execute=True, record=recorder)
                self.assertFalse(transport.writes())

    def test_old_https_core_gate_stops_before_final_core_redeployment(self):
        transport = Transport()
        with self.assertRaisesRegex(ValueError, 'migrate before Core'):
            require_portal_http_core_ready(CONFIG, transport)
        self.assertFalse(transport.writes())
        self.assertEqual(len(transport.calls), 1)

    def test_missing_gateway_is_a_new_fixed_http_construction(self):
        transport = Transport({})
        self.assertTrue(require_portal_http_core_ready(CONFIG, transport)['newGateway'])
        self.assertFalse(transport.writes())

    def test_cutover_requires_new_image_ready_revision_http_ingress_and_firewall(self):
        for issue in ('old-image', 'old-revision-image', 'ingress', 'missing-firewall', 'host'):
            transport = Transport()
            ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
            transport.calls.clear()
            ids = portal_ids(CONFIG)
            if issue == 'old-image':
                transport.bodies[ids['app']]['properties']['template']['containers'][0]['image'] = 'old-healthy-image'
            elif issue == 'old-revision-image':
                revision = transport.bodies[ids['app']]['properties']['latestRevisionName']
                transport.bodies[ids['app'] + '/revisions/' + revision]['properties']['template']['containers'][0]['image'] = 'old-healthy-image'
            elif issue == 'ingress':
                transport.bodies[ids['app']]['properties']['configuration']['ingress']['allowInsecure'] = False
            elif issue == 'host':
                transport.bodies[ids['gateway']]['properties']['backendHttpSettingsCollection'][0]['properties']['hostName'] = 'another.invalid'
            else:
                transport.bodies[ids['firewall']]['properties']['ruleCollections'][0]['rules'].pop()
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                verify_portal_http_ready(CONFIG, transport, expected_image_digest=DIGEST)
            self.assertFalse(transport.writes())

    def test_validated_revision_and_firewall_can_be_cut_over_without_changing_apim(self):
        transport = Transport()
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        transport.calls.clear()
        result = verify_portal_http_ready(CONFIG, transport, expected_image_digest=DIGEST)
        self.assertTrue(result['expectedImageVerified'])
        self.assertTrue(result['firewallHttpReady'])
        self.assertFalse(transport.writes())

    def test_http_transport_readback_casing_preserves_ready_app_and_read_only_gate(self):
        for value in ('http', 'Http', 'auto', 'Auto'):
            with self.subTest(transport=value):
                transport = Transport()
                ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
                app = transport.bodies[portal_ids(CONFIG)['app']]
                app['properties']['configuration']['ingress']['transport'] = value
                before = copy.deepcopy(app)
                transport.calls.clear()
                result = verify_portal_http_ready(CONFIG, transport, expected_image_digest=DIGEST)
                self.assertTrue(result['httpIngressReady'])
                self.assertEqual(transport.bodies[portal_ids(CONFIG)['app']], before)
                self.assertFalse(transport.writes())

    def test_other_transport_modes_and_malformed_values_keep_existing_backend(self):
        for value in ('http2', 'Http2', 'tcp', 'Tcp', 'HTTP', 'AUTO', 'http ', '',
                      None, False, True, 0, 1, [], {}):
            with self.subTest(transport=value):
                transport = Transport()
                app = transport.bodies[portal_ids(CONFIG)['app']]
                app['properties']['configuration']['ingress']['transport'] = value
                before = copy.deepcopy(transport.bodies)
                with self.assertRaisesRegex(ValueError, 'retain the existing HTTPS backend'):
                    verify_portal_http_ready(CONFIG, transport, expected_image_digest=DIGEST)
                self.assertEqual(transport.bodies, before)
                self.assertFalse(transport.writes())

    def test_rollback_restores_firewall443_before_gateway_and_preserves_other_paths(self):
        transport, records = Transport(), []
        ensure_portal_firewall_port(CONFIG, transport, execute=True, record=lambda *_: None)
        ids = portal_ids(CONFIG)
        transport.bodies[ids['firewall']]['properties']['ruleCollections'][0]['rules'].pop(0)
        gateway = transport.bodies[ids['gateway']]
        gateway['properties']['backendHttpSettingsCollection'][0]['properties'].update(protocol='Http', port=80)
        gateway['properties']['probes'][1]['properties'].update(protocol='Http', port=80)
        other_probe = copy.deepcopy(gateway['properties']['probes'][0])
        routes = copy.deepcopy(gateway['properties']['requestRoutingRules'])
        transport.calls.clear()
        result = rollback_portal_backend(CONFIG, transport, execute=True, record=lambda e, v: records.append((e, v)))
        self.assertTrue(result['updated'])
        self.assertIn('firewallPolicies', transport.writes()[0]['path'])
        self.assertIn('applicationGateways', transport.writes()[1]['path'])
        firewall_rules = transport.writes()[0]['body']['properties']['ruleCollections'][0]['rules']
        self.assertEqual([rule['name'] for rule in firewall_rules], ['allow-appgw-aca-http', 'agw-aca-https'])
        self.assertEqual([rule['destinationPorts'] for rule in firewall_rules], [['80'], ['443']])
        gateway_update = transport.writes()[1]['body']['properties']
        self.assertEqual(gateway_update['requestRoutingRules'], routes)
        self.assertEqual(gateway_update['probes'][0], other_probe)
        self.assertEqual(gateway_update['backendHttpSettingsCollection'][0]['properties']['protocol'], 'Https')
        self.assertEqual(gateway_update['probes'][1]['properties']['port'], 443)
        self.assertIn('gateway-before-rollback', [r[0] for r in records])

    def test_rollback_retains_legacy_http_and_restores_owned_https_before_gateway(self):
        transport = Transport()
        ids = portal_ids(CONFIG)
        rules = transport.bodies[ids['firewall']]['properties']['ruleCollections'][0]['rules']
        rules[0].update(name='agw-aca-http', destinationPorts=['80'])
        before = copy.deepcopy(rules[0])
        gateway = transport.bodies[ids['gateway']]
        gateway['properties']['backendHttpSettingsCollection'][0]['properties'].update(protocol='Http', port=80)
        gateway['properties']['probes'][1]['properties'].update(protocol='Http', port=80)
        result = rollback_portal_backend(CONFIG, transport, execute=True, record=lambda *_: None)
        self.assertTrue(result['updated'])
        self.assertIn('firewallPolicies', transport.writes()[0]['path'])
        self.assertIn('applicationGateways', transport.writes()[1]['path'])
        sent_rules = transport.writes()[0]['body']['properties']['ruleCollections'][0]['rules']
        self.assertEqual(sent_rules[0], before)
        self.assertEqual(sent_rules[1]['name'], 'agw-aca-https')
        self.assertEqual(sent_rules[1]['destinationPorts'], ['443'])


if __name__ == '__main__':
    unittest.main()
