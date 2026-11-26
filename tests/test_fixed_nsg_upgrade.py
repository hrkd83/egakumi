"""Verify the NSG-only Bicep and conditional ACA association with synthetic ARM."""
import copy
import unittest

from bicep_helpers import compile_bicep
from test_fixed_nsg_migration import MemoryTransport, fixture_config, fixture_bindings
from test_nsg_rule_priorities import FixedExpression
from deployment.helpers import fixed_nsg as nsg


class UpgradeExpression(FixedExpression):
    def resolve(self, value, parameters):
        if isinstance(value, dict): return {k: self.resolve(v, parameters) for k, v in value.items()}
        if isinstance(value, list): return [self.resolve(v, parameters) for v in value]
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            return UpgradeExpression(value, self.template, self.cache).evaluate(parameters, 0)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] in ('items', 'startsWith', 'substring'):
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            if tree[1] == 'items': return [{'key': k, 'value': values[0][k]} for k in sorted(values[0])]
            if tree[1] == 'startsWith': return values[0].startswith(values[1])
            return values[0][values[1]:] if len(values) == 2 else values[0][values[1]:values[1] + values[2]]
        return super().evaluate(parameters, index, tree, bindings)


class AssociationTransport(MemoryTransport):
    def __init__(self, config, *, legacy=False):
        super().__init__(config, legacy=legacy)
        self.subnet_id = self.subnets['aca']
        subnet = next(s for s in self.resources[self.vnet_id]['properties']['subnets'] if s['id'] == self.subnet_id)
        subnet['properties'].pop('networkSecurityGroup', None)
        subnet['properties'].update(defaultOutboundAccess=False,
            delegations=[{'name': 'containerapps', 'properties': {'serviceName': 'Microsoft.App/environments', 'actions': ['fixture-readonly']}}],
            serviceAssociationLinks=[{'id': '/synthetic/provider-link'}],
            routeTable={'id': self.nw + 'routeTables/retained-route-table'})
        if self.nsg_id('aca') in self.resources:
            self.resources[self.nsg_id('aca')]['properties']['subnets'] = []
        self.subnet = {**copy.deepcopy(subnet), 'type': 'Microsoft.Network/virtualNetworks/subnets', 'etag': '"subnet-v1"'}
        self.subnet['properties']['provisioningState'] = 'Succeeded'
        self.puts = []; self.drift = False; self.lost = False; self.pending = False

    def request(self, method, path, body=None, headers=None):
        if path.split('?')[0] != self.subnet_id:
            return super().request(method, path, body=body, headers=headers)
        if method == 'GET':
            value = copy.deepcopy(self.subnet)
            if self.drift and self.puts: value['properties']['defaultOutboundAccess'] = True
            if self.pending and self.puts: value['properties']['provisioningState'] = 'Updating'
            return {'statusCode': 200, 'body': value, 'headers': {}}
        self.puts.append({'method': method, 'path': path, 'body': copy.deepcopy(body), 'headers': headers})
        if self.lost: raise OSError('synthetic lost response')
        self.subnet['properties'].update(copy.deepcopy(body['properties']))
        self.subnet['properties']['provisioningState'] = 'Succeeded'
        for subnet in self.resources[self.vnet_id]['properties']['subnets']:
            if subnet['id'] == self.subnet_id: subnet['properties'].update(copy.deepcopy(body['properties']))
        self.resources[self.nsg_id('aca')]['properties']['subnets'] = [{'id': self.subnet_id}]
        return {'statusCode': 200, 'body': {}, 'headers': {}}


class FixedNsgUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/modules/nsg-fixed-upgrade.bicep')

    def setUp(self):
        self.config = fixture_config(); self.bindings = fixture_bindings(self.config)

    def test_compiled_upgrade_emits_only_75_manifest_children_and_missing_aca_parent(self):
        parameters = {'resourcePrefix': self.config['resourceStem'], 'location': self.config['location'],
            'tags': {}, 'bindings': self.bindings,
            'existingNetworkSecurityGroupNames': ['nsg-' + self.config['resourceStem'] + '-' + nsg.SUFFIXES[k] for k in nsg.GROUPS if k != 'aca']}
        expression = UpgradeExpression('[createObject()]', self.template)
        rules = expression.resolve("[variables('rules')]", parameters)
        self.assertEqual(len(rules), 75)
        for row in rules:
            parent, name = row['name'].split('/')
            group = next(k for k in nsg.GROUPS if parent.endswith('-' + nsg.SUFFIXES[k]))
            wanted = nsg._catalog('nsg-rule-priorities.json')['groups'][group]['rules'][name]
            self.assertEqual(nsg._properties(row), nsg._definition(wanted, self.bindings))
        missing = expression.resolve("[variables('missing')]", parameters)
        self.assertEqual([g['key'] for g in missing], ['aca'])
        resources = self.template['resources']
        types = {r['type'] for r in (resources.values() if isinstance(resources, dict) else resources)}
        self.assertEqual(types, {nsg.NSG_TYPE, nsg.NSG_TYPE + '/securityRules'})
        parameters['existingNetworkSecurityGroupNames'].append('nsg-' + self.config['resourceStem'] + '-aca')
        self.assertEqual(UpgradeExpression('[createObject()]', self.template).resolve("[variables('missing')]", parameters), [])

    def test_stage_create_associate_complete_converges_to_all_75_without_parent_flush(self):
        transport = AssociationTransport(self.config, legacy=True); events = []
        recorder = lambda event, value: events.append(event)
        nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=recorder, sleep=lambda _: None)
        parameters = nsg.fixed_nsg_upgrade_parameters(self.config, transport)
        self.assertEqual(len(parameters['existingNetworkSecurityGroupNames']), 4)
        transport.add(transport.nsg_id('aca'), nsg.NSG_TYPE, {
            'securityRules': [], 'defaultSecurityRules': transport.defaults('aca'), 'subnets': [], 'networkInterfaces': []})
        for group in nsg.GROUPS:
            parent = transport.resources[transport.nsg_id(group)]
            for name, rule in nsg._catalog('nsg-rule-priorities.json')['groups'][group]['rules'].items():
                parent['properties']['securityRules'].append(transport.child(parent['id'], name, nsg._definition(rule, self.bindings)))
        nsg.associate_fixed_aca_nsg(self.config, transport, bindings=self.bindings, execute=True,
            record=recorder, sleep=lambda _: None)
        nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=recorder, sleep=lambda _: None)
        final = nsg._parents(self.config, transport)
        self.assertEqual(sum(len(nsg._inventory(parent)) for parent in final.values()), 75)
        self.assertEqual(nsg._classify(final, self.bindings, require_complete=True), [])
        self.assertEqual(len(transport.puts), 1)
        self.assertTrue(all('/securityRules/' in call['path'] for call in transport.mutations()))

    def what_if(self):
        transport = MemoryTransport(self.config)
        changes = [{'resourceId': child['id'], 'changeType': 'Create', 'before': None, 'after': child}
                   for parent in nsg._parents(self.config, transport).values() for child in parent['properties']['securityRules']]
        return {'status': 'Succeeded', 'error': None, 'diagnostics': [], 'potentialChanges': None, 'changes': changes}

    def test_initial_tags_match_main_union_and_only_empty_new_aca_parent_is_allowed(self):
        self.config['tags'] = {'department': 'fixture', 'product': 'different'}
        transport = MemoryTransport(self.config)
        parameters = nsg.fixed_nsg_upgrade_parameters(self.config, transport)
        self.assertEqual(parameters['tags'], {**self.config['tags'], **transport.tags})
        doc = self.what_if(); target = transport.nsg_id('aca')
        parent = {'resourceId': target, 'changeType': 'Create', 'before': None, 'after': {
            'location': self.config['location'], 'tags': parameters['tags'], 'properties': {'securityRules': []}}}
        doc['changes'].append(parent)
        nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)
        parent['after']['properties']['flushConnection'] = True
        with self.assertRaises(nsg.FixedNsgError): nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)

    def test_custom_vnet_or_firewall_dns_stops_before_any_staging_mutation(self):
        for mode in ('vnet', 'firewall', 'proxy'):
            transport = MemoryTransport(self.config, legacy=True)
            if mode == 'vnet': transport.resources[transport.vnet_id]['properties']['dhcpOptions'] = {'dnsServers': [self.bindings['ips']['management']]}
            else:
                dns = transport.resources[transport.nw + 'firewallPolicies/afwp-' + transport.stem]['properties']['dnsSettings']
                if mode == 'firewall': dns['servers'] = [self.bindings['ips']['management']]
                else: dns['enableProxy'] = False
            with self.subTest(mode=mode), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=lambda e, v: None)
            self.assertEqual(transport.mutations(), [])

    def test_rest_result_and_aggregated_aca_children_match_the_fixed_template(self):
        transport = MemoryTransport(self.config)
        doc = self.what_if()
        children = [transport.child(transport.nsg_id('aca'), name, nsg._definition(rule, self.bindings))
                    for name, rule in nsg._catalog('nsg-rule-priorities.json')['groups']['aca']['rules'].items()]
        doc['changes'].append({'resourceId': transport.nsg_id('aca'), 'changeType': 'Create', 'before': None,
            'after': {'location': self.config['location'], 'tags': transport.tags,
                      'properties': {'securityRules': children}}})
        rest = {'status': 'Succeeded', 'properties': {'changes': doc['changes']}}
        self.assertEqual(nsg.verify_fixed_nsg_upgrade_what_if(self.config, rest, self.bindings)['fixedChildrenVerified'], 75)
        for mode in ('nested-diagnostic', 'conflict', 'unexpected-child', 'duplicate-child', 'child-tuple'):
            bad = copy.deepcopy(rest)
            if mode == 'nested-diagnostic': bad['properties']['diagnostics'] = [{'code': 'Skipped'}]
            elif mode == 'conflict': bad['changes'] = []
            elif mode == 'unexpected-child': bad['properties']['changes'][-1]['after']['properties']['securityRules'].pop()
            elif mode == 'duplicate-child': bad['properties']['changes'][-1]['after']['properties']['securityRules'].append(children[0])
            else: bad['properties']['changes'][-1]['after']['properties']['securityRules'][0]['properties']['priority'] = 777
            with self.subTest(mode=mode), self.assertRaises(nsg.FixedNsgError):
                nsg.verify_fixed_nsg_upgrade_what_if(self.config, bad, self.bindings)

    def test_latest_parent_inventory_detects_rule_added_during_binding_discovery(self):
        transport = MemoryTransport(self.config); counts = {}
        def race(current, identity):
            if identity != current.nsg_id('agw'): return
            counts[identity] = counts.get(identity, 0) + 1
            if counts[identity] == 2:
                parent = current.resources[identity]
                canonical = nsg._definition(nsg._catalog('nsg-rule-priorities.json')['groups']['agw']['rules']['allow-client-http-ingress'], self.bindings)
                canonical['priority'] = 777
                parent['properties']['securityRules'].append(current.child(identity, 'unknown-manual-rule', canonical))
        transport.get_hook = race
        with self.assertRaises(nsg.FixedNsgError): nsg.fixed_nsg_upgrade_parameters(self.config, transport)
        self.assertEqual(transport.mutations(), [])

    def test_predicted_child_state_is_required_for_writes_and_before_only_for_nochange(self):
        for action in ('Create', 'Modify', 'NoChange'):
            doc = self.what_if()
            child = doc['changes'][0]
            child['changeType'] = action
            child['before'] = child.pop('after')
            if action == 'NoChange':
                self.assertEqual(nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)['fixedChildrenVerified'], 75)
            else:
                with self.subTest(action=action), self.assertRaises(nsg.FixedNsgError):
                    nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)

    def test_flattened_changes_cannot_hide_nested_diagnostics_without_nested_changes(self):
        for key, value in (('error', {'code': 'Failed'}), ('diagnostics', [{'code': 'Skipped'}]),
                           ('potentialChanges', [{'code': 'Unexpanded'}])):
            doc = self.what_if()
            doc['properties'] = {key: value}
            with self.subTest(key=key), self.assertRaises(nsg.FixedNsgError):
                nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)

    def test_what_if_requires_all_expanded_exact_children_and_no_other_writes(self):
        doc = self.what_if()
        self.assertEqual(nsg.verify_fixed_nsg_upgrade_what_if(self.config, doc, self.bindings)['fixedChildrenVerified'], 75)
        for mode in ('diagnostic', 'missing', 'ignored', 'foreign', 'parent', 'tuple'):
            bad = copy.deepcopy(doc)
            if mode == 'diagnostic': bad['diagnostics'] = [{'code': 'NestedDeploymentShortCircuited'}]
            elif mode == 'missing': bad['changes'].pop()
            elif mode == 'ignored': bad['changes'][0]['changeType'] = 'Ignore'
            elif mode == 'tuple': bad['changes'][0]['after']['properties']['priority'] = 777
            else: bad['changes'].append({'resourceId': '/unrelated' if mode == 'foreign' else bad['changes'][0]['resourceId'].split('/securityRules/')[0], 'changeType': 'Modify'})
            with self.subTest(mode=mode), self.assertRaises(nsg.FixedNsgError):
                nsg.verify_fixed_nsg_upgrade_what_if(self.config, bad, self.bindings)

    def test_association_preserves_every_supported_writable_setting_and_puts_only_subnet(self):
        transport = AssociationTransport(self.config); events = []
        result = nsg.associate_fixed_aca_nsg(self.config, transport, bindings=self.bindings,
            execute=True, record=lambda e, v: events.append(e), sleep=lambda _: None)
        self.assertTrue(result['associationVerified']); self.assertEqual(len(transport.puts), 1)
        put = transport.puts[0]
        self.assertEqual(put['headers'], {'If-Match': '"subnet-v1"'})
        self.assertFalse(put['body']['properties']['defaultOutboundAccess'])
        self.assertEqual(put['body']['properties']['routeTable'], {'id': transport.nw + 'routeTables/retained-route-table'})
        self.assertEqual(put['body']['properties']['delegations'][0]['properties'], {'serviceName': 'Microsoft.App/environments'})
        self.assertNotIn('serviceAssociationLinks', put['body']['properties'])
        self.assertEqual(events, ['aca-association-planned', 'aca-association-attempt', 'aca-association-verified'])

    def test_association_stops_before_first_write_for_unknown_properties_or_journal_failure(self):
        for mode in ('unknown', 'delegation', 'journal', 'noncallable', 'etag'):
            transport = AssociationTransport(self.config)
            record = lambda e, v: None
            if mode == 'unknown': transport.subnet['properties']['unsupportedSetting'] = False
            elif mode == 'delegation': transport.subnet['properties']['delegations'][0]['properties']['unsupportedSetting'] = False
            elif mode == 'etag': transport.subnet['etag'] = 'W/"*"'
            elif mode == 'noncallable': record = object()
            else: record = lambda e, v: (_ for _ in ()).throw(OSError('synthetic journal failure'))
            with self.subTest(mode=mode), self.assertRaises((nsg.FixedNsgError, OSError)):
                nsg.associate_fixed_aca_nsg(self.config, transport, bindings=self.bindings, execute=True, record=record)
            self.assertEqual(transport.puts, [])

    def test_accepted_drift_pending_and_lost_response_never_retry_put(self):
        for mode in ('drift', 'pending', 'lost'):
            transport = AssociationTransport(self.config); setattr(transport, mode, True)
            with self.subTest(mode=mode), self.assertRaises(nsg.FixedNsgError):
                nsg.associate_fixed_aca_nsg(self.config, transport, bindings=self.bindings, execute=True,
                    record=lambda e, v: None, max_checks=2, sleep=lambda _: None)
            self.assertEqual(len(transport.puts), 1)


if __name__ == '__main__': unittest.main()
