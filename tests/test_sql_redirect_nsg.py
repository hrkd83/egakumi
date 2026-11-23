"""Exercise four-child SQL migration and rollback with independent synthetic ARM."""
import copy
import unittest

from bicep_helpers import compile_bicep
from test_fixed_nsg_migration import MemoryTransport, fixture_config, fixture_bindings, writable
from test_fixed_nsg_upgrade import UpgradeExpression
from deployment.helpers import fixed_nsg as nsg


class SqlUpgradeExpression(UpgradeExpression):
    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == 'call' and tree[1] == 'range':
            start, count = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            return list(range(start, start + count))
        return super().evaluate(parameters, index, tree, bindings)


class SqlTransport(MemoryTransport):
    def __init__(self, config, *, proxy=True):
        super().__init__(config)
        self.policy_id = self.groups['svc'] + '/providers/Microsoft.Sql/servers/sql-' + self.stem + '/connectionPolicies/default'
        self.add(self.policy_id, 'Microsoft.Sql/servers/connectionPolicies', {'connectionType': 'Proxy' if proxy else 'Redirect'})
        self.lose_delete = False
        if proxy:
            for row in nsg._sql_rows(self.bindings, 'Redirect'):
                parent = self.resources[self.nsg_id(row['group'])]
                parent['properties']['securityRules'] = [r for r in parent['properties']['securityRules']
                    if r['name'] != row['targetName']]
                parent['properties']['securityRules'].append(self.child(parent['id'], row['name'], row['source']))

    def request(self, method, path, body=None, headers=None):
        result = super().request(method, path, body=body, headers=headers)
        if method == 'DELETE' and self.lose_delete:
            self.lose_delete = False
            raise OSError('Synthetic dropped DELETE response')
        return result

    def deploy_sql(self, target_mode):
        """Simulate only the four compiled children, retaining every unrelated setting."""
        for row in nsg._sql_rows(self.bindings, target_mode):
            parent = self.resources[self.nsg_id(row['group'])]
            parent['properties']['securityRules'] = [r for r in parent['properties']['securityRules']
                if r['name'] != row['targetName']]
            parent['properties']['securityRules'].append(self.child(parent['id'], row['targetName'], row['target']))
            nsg._inventory(parent)


class SqlRedirectNsgTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()
        self.events = []

    def record(self, event, value):
        self.events.append((event, copy.deepcopy(value)))

    def forward(self, transport):
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        parameters = nsg.sql_nsg_upgrade_parameters(self.config, transport, baseline=baseline)
        self.assertEqual(set(parameters), {'resourcePrefix', 'bindings', 'targetMode'})
        transport.deploy_sql('Redirect')
        nsg.verify_sql_nsg_rules(self.config, transport, baseline=baseline)
        nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, execute=True,
                                   record=self.record, wait_seconds=0)
        return baseline

    def test_only_four_source_children_move_and_retire_and_other_settings_are_preserved(self):
        transport = SqlTransport(self.config)
        before = nsg._sql_without_rules(nsg._parents(self.config, transport), transport.bindings)
        self.forward(transport)
        after = nsg._sql_without_rules(nsg._parents(self.config, transport), transport.bindings)
        self.assertEqual({g: nsg._snapshot(p) for g, p in before.items()},
                         {g: nsg._snapshot(p) for g, p in after.items()})
        mutations = transport.mutations()
        self.assertEqual([r['method'] for r in mutations], ['PUT'] * 4 + ['DELETE'] * 4)
        old_ids = {transport.nsg_id(row['group']) + '/securityRules/' + row['name']
                   for row in nsg._sql_rows(transport.bindings, 'Redirect')}
        self.assertEqual({r['path'].split('?')[0] for r in mutations}, old_ids)
        self.assertTrue(all(r['headers']['If-Match'] != '*' for r in mutations))
        self.assertEqual(nsg.verify_sql_nsg_rules(self.config, transport, require_retired=True),
            {'targetMode': 'Redirect', 'sqlRulesVerified': 4, 'legacySqlRules': 0, 'runtimeVerified': False})
        self.assertEqual(sum(len(nsg._inventory(transport.resources[transport.nsg_id(g)])) for g in nsg.GROUPS), 76)
        count = len(transport.mutations())
        repeated = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record)
        nsg.complete_sql_nsg_rules(self.config, transport, baseline=repeated, execute=True, record=self.record)
        self.assertEqual(len(transport.mutations()), count)

    def test_proxy_rollback_requires_policy_and_physical_drain_and_restores_exact_old_rules(self):
        transport = SqlTransport(self.config, proxy=False)
        for confirmed in (False, None, 1, True):
            with self.subTest(confirmed=confirmed), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_sql_nsg_rules(self.config, transport, target_mode='Proxy',
                    proxy_connections_verified=confirmed, execute=True, record=self.record)
        self.assertEqual(transport.mutations(), [])
        transport.resources[transport.policy_id]['properties']['connectionType'] = 'Proxy'
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, target_mode='Proxy',
            proxy_connections_verified=True, execute=True, record=self.record, wait_seconds=0)
        nsg.sql_nsg_upgrade_parameters(self.config, transport, baseline=baseline, target_mode='Proxy',
                                       proxy_connections_verified=True)
        transport.deploy_sql('Proxy')
        nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, target_mode='Proxy',
            proxy_connections_verified=True, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(nsg.verify_sql_nsg_rules(self.config, transport, target_mode='Proxy', require_retired=True)['sqlRulesVerified'], 4)
        for row in nsg._sql_rows(transport.bindings, 'Proxy'):
            current = nsg._inventory(transport.resources[transport.nsg_id(row['group'])])
            self.assertNotIn(row['name'], current)
            self.assertEqual(nsg._properties(current[row['targetName']]), row['target'])

    def test_parameter_generation_requires_staging_and_the_matching_protected_baseline(self):
        transport = SqlTransport(self.config)
        planned = nsg.prepare_sql_nsg_rules(self.config, transport)
        self.assertEqual(transport.mutations(), [])
        with self.assertRaises(nsg.FixedNsgError):
            nsg.sql_nsg_upgrade_parameters(self.config, transport, baseline=planned)
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        bad = copy.deepcopy(baseline); bad['targetMode'] = 'Proxy'
        with self.assertRaises(nsg.FixedNsgError):
            nsg.sql_nsg_upgrade_parameters(self.config, transport, baseline=bad)

    def test_nonboolean_execution_mode_is_rejected_without_writes(self):
        transport = SqlTransport(self.config)
        for execute in ('false', 1, None):
            with self.subTest(execute=execute), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_sql_nsg_rules(self.config, transport, execute=execute, record=self.record)
        self.assertEqual(transport.mutations(), [])

    def test_proxy_policy_change_stops_retirement_before_another_broad_allow_is_removed(self):
        transport = SqlTransport(self.config, proxy=False)
        transport.resources[transport.policy_id]['properties']['connectionType'] = 'Proxy'
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, target_mode='Proxy',
            proxy_connections_verified=True, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_sql('Proxy'); transport.calls.clear()
        def record(event, value):
            if event == 'removed': transport.resources[transport.policy_id]['properties']['connectionType'] = 'Redirect'
        with self.assertRaises(nsg.FixedNsgError):
            nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, target_mode='Proxy',
                proxy_connections_verified=True, execute=True, record=record, wait_seconds=0)
        self.assertEqual([r['method'] for r in transport.mutations()], ['DELETE'])

    def test_no_deletion_before_every_target_tuple_matches(self):
        for issue in ('missing', 'port', 'source', 'priority'):
            transport = SqlTransport(self.config)
            baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
            transport.deploy_sql('Redirect')
            parent = transport.resources[transport.nsg_id('management')]
            child = next(r for r in parent['properties']['securityRules'] if r['name'] == 'allow-sql-egress')
            if issue == 'missing': parent['properties']['securityRules'].remove(child)
            elif issue == 'port': child['properties']['destinationPortRange'] = '1433'
            elif issue == 'source': child['properties']['sourceAddressPrefix'] = '192.0.2.25/32'
            else: child['properties']['priority'] = 777
            transport.calls.clear()
            with self.subTest(issue=issue), self.assertRaises(nsg.FixedNsgError):
                nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, execute=True, record=self.record)
            self.assertEqual(transport.mutations(), [])

    def test_unknown_manual_modified_rules_and_priority_collisions_stop_without_writes(self):
        for issue in ('manual', 'collision', 'modified', 'flush', 'default'):
            transport = SqlTransport(self.config)
            parent = transport.resources[transport.nsg_id('management')]
            if issue in ('manual', 'collision'):
                p = nsg._definition(nsg._catalog('nsg-rule-priorities.json')['groups']['management']['rules']['allow-entra-egress'], transport.bindings)
                p['priority'] = 1100 if issue == 'collision' else 777
                parent['properties']['securityRules'].append(transport.child(parent['id'], 'manual-retained-rule', p))
            elif issue == 'modified':
                next(r for r in parent['properties']['securityRules'] if r['name'] == 'allow-sql-1433-egress')['properties']['destinationPortRange'] = '1444'
            elif issue == 'flush': parent['properties']['flushConnection'] = True
            else: parent['properties']['defaultSecurityRules'][0]['properties']['destinationPortRange'] = '443'
            before = copy.deepcopy(transport.resources)
            with self.subTest(issue=issue), self.assertRaises(nsg.FixedNsgError):
                nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record)
            self.assertEqual(transport.mutations(), [])
            self.assertEqual(transport.resources, before)

    def test_unrelated_description_change_after_bicep_stops_before_retirement(self):
        transport = SqlTransport(self.config)
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_sql('Redirect')
        transport.resources[transport.nsg_id('apim')]['properties']['securityRules'][0]['properties']['description'] = 'Unrelated manual description'
        transport.calls.clear()
        with self.assertRaises(nsg.FixedNsgError):
            nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, execute=True, record=self.record)
        self.assertEqual(transport.mutations(), [])

    def test_lost_stage_response_stops_and_resume_does_not_repeat_completed_child(self):
        transport = SqlTransport(self.config); transport.fail_after_put = True
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(len(transport.mutations()), 1)
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(baseline['azureWrites'], 3)
        self.assertEqual(len({r['path'] for r in transport.mutations()}), 4)

    def test_lost_delete_response_stops_and_resume_removes_only_remaining_sources(self):
        transport = SqlTransport(self.config)
        baseline = nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        transport.deploy_sql('Redirect'); transport.calls.clear(); transport.lose_delete = True
        with self.assertRaises(nsg.FixedNsgError):
            nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(len(transport.mutations()), 1)
        result = nsg.complete_sql_nsg_rules(self.config, transport, baseline=baseline, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(result['azureWrites'], 3)
        self.assertEqual(len({r['path'] for r in transport.mutations()}), 4)

    def test_pending_stage_stops_without_an_extra_put(self):
        transport = SqlTransport(self.config); transport.hold_pending = True
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record, max_checks=2, wait_seconds=0)
        self.assertEqual(len(transport.mutations()), 1)

    def test_concurrent_child_etag_change_stops_before_first_put(self):
        transport = SqlTransport(self.config)
        target = transport.nsg_id('pep') + '/securityRules/allow-aca-sql-1433-ingress'
        def drift(current, identity):
            if identity == target:
                child = next(r for r in current.resources[current.nsg_id('pep')]['properties']['securityRules']
                             if r['name'] == 'allow-aca-sql-1433-ingress')
                child['etag'] = '"concurrent-child"'
        transport.get_hook = drift
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=self.record)
        self.assertEqual(transport.mutations(), [])

    def test_address_change_before_first_stage_stops_without_writes(self):
        transport = SqlTransport(self.config)
        def record(event, value):
            if event == 'sql-nsg-plan':
                nic = next(v for v in transport.resources.values() if v.get('type') == 'Microsoft.Network/networkInterfaces'
                           and v.get('properties', {}).get('privateEndpoint', {}).get('id', '').endswith('-sql'))
                nic['properties']['ipConfigurations'][0]['properties']['privateIPAddress'] = '10.70.1.25'
        with self.assertRaises(nsg.FixedNsgError):
            nsg.prepare_sql_nsg_rules(self.config, transport, execute=True, record=record)
        self.assertEqual(transport.mutations(), [])

    def test_generic_fixed_migration_recognizes_old_sql_without_reindexing_historical_slots(self):
        transport = SqlTransport(self.config)
        legacy = nsg._catalog('nsg-legacy-rule-definitions.json')
        historical = {g: nsg._temporary_priorities(r) for g, r in legacy['groups'].items()}
        result = nsg.prepare_fixed_nsg_rules(self.config, transport, execute=True, record=self.record, wait_seconds=0)
        self.assertEqual(result['azureWrites'], 4)
        self.assertEqual({g: nsg._temporary_priorities(r) for g, r in legacy['groups'].items()}, historical)
        self.assertTrue(all(r['body']['properties']['priority'] in (1100, 1101) for r in transport.mutations()))
        transport.deploy_sql('Redirect')
        self.assertEqual(nsg.complete_fixed_nsg_rules(self.config, transport, execute=True, record=self.record,
                                                    wait_seconds=0)['azureWrites'], 4)

    def test_four_child_what_if_validates_both_modes_and_nochange_readbacks(self):
        transport = SqlTransport(self.config)
        for mode in ('Redirect', 'Proxy'):
            changes = [{'resourceId': transport.nsg_id(r['group']) + '/securityRules/' + r['targetName'],
                        'changeType': 'Create', 'after': {'properties': writable(r['target'])}}
                       for r in nsg._sql_rows(transport.bindings, mode)]
            result = nsg.verify_sql_nsg_upgrade_what_if(self.config,
                {'status': 'Succeeded', 'properties': {'changes': changes}}, transport.bindings, target_mode=mode)
            self.assertEqual(result, {'sqlChildrenVerified': 4, 'expansionDiagnostics': 0, 'unrelatedWrites': 0})
            for c in changes: c['changeType'] = 'NoChange'; c['before'] = c.pop('after')
            self.assertEqual(nsg.verify_sql_nsg_upgrade_what_if(self.config,
                {'status': 'Succeeded', 'changes': changes}, transport.bindings, target_mode=mode), result)

    def test_what_if_rejects_parent_and_unrelated_writes_missing_duplicate_ignore_and_diagnostics(self):
        transport = SqlTransport(self.config)
        changes = [{'resourceId': transport.nsg_id(r['group']) + '/securityRules/' + r['targetName'],
                    'changeType': 'Create', 'after': {'properties': writable(r['target'])}}
                   for r in nsg._sql_rows(transport.bindings, 'Redirect')]
        for issue in ('parent', 'unrelated', 'missing', 'duplicate', 'ignore', 'port', 'identity', 'diagnostic', 'nested', 'conflict'):
            doc = {'status': 'Succeeded', 'changes': copy.deepcopy(changes)}
            if issue in ('parent', 'unrelated'):
                doc['changes'].append({'resourceId': transport.nsg_id('management') if issue == 'parent' else transport.policy_id,
                                       'changeType': 'Modify', 'after': {'properties': {}}})
            elif issue == 'missing': doc['changes'].pop()
            elif issue == 'duplicate': doc['changes'].append(copy.deepcopy(doc['changes'][0]))
            elif issue == 'ignore': doc['changes'][0]['changeType'] = 'Ignore'
            elif issue == 'port': doc['changes'][0]['after']['properties']['destinationPortRange'] = '*'
            elif issue == 'identity': doc['changes'][0]['after']['id'] = 'PRIVATE-IDENTITY-SENTINEL'
            elif issue == 'diagnostic': doc['diagnostics'] = [{'message': 'PRIVATE-DIAGNOSTIC-SENTINEL'}]
            elif issue == 'nested': doc['properties'] = {'changes': copy.deepcopy(changes), 'diagnostics': ['PRIVATE-DIAGNOSTIC-SENTINEL']}
            else: doc['properties'] = {'changes': []}
            with self.subTest(issue=issue), self.assertRaises(nsg.FixedNsgError) as caught:
                nsg.verify_sql_nsg_upgrade_what_if(self.config, doc, transport.bindings)
            self.assertNotIn('PRIVATE-', str(caught.exception))


class SqlRedirectNsgBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('infra/modules/sql-redirect-nsg-upgrade.bicep')

    def value(self, value, parameters, index=0):
        if isinstance(value, dict): return {k: self.value(v, parameters, index) for k, v in value.items()}
        if isinstance(value, list): return [self.value(v, parameters, index) for v in value]
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            return SqlUpgradeExpression(value, self.template).evaluate(parameters, index)
        return value

    def test_compiled_resources_emit_only_the_four_exact_scoped_children_in_both_directions(self):
        resources = self.template['resources']
        resource, = list(resources.values()) if isinstance(resources, dict) else resources
        self.assertEqual(resource['type'], nsg.NSG_TYPE + '/securityRules')
        self.assertNotIn('condition', resource)
        self.assertEqual(set(self.template['parameters']), {'resourcePrefix', 'bindings', 'targetMode'})
        for mode in ('Redirect', 'Proxy'):
            for count in (1, 2):
                bindings = fixture_bindings(fixture_config()); bindings['ipSets']['sql'] = bindings['ipSets']['sql'][:count]
                parameters = {'resourcePrefix': 'test-fixture', 'bindings': nsg._sql_binding_view(bindings), 'targetMode': mode}
                expected = {(r['group'], r['targetName']): r['target'] for r in nsg._sql_rows(bindings, mode)}
                self.assertEqual(self.value(resource['copy']['count'], parameters), 4)
                observed = {}
                for index in range(4):
                    name = self.value(resource['name'], parameters, index)
                    parent, child = name.split('/')
                    group = next(g for g in nsg.GROUPS if parent == 'nsg-test-fixture-' + nsg.SUFFIXES[g])
                    properties = self.value(resource['properties'], parameters, index)
                    observed[group, child] = nsg._properties({'properties': properties})
                with self.subTest(mode=mode, sql_ips=count): self.assertEqual(observed, expected)

    def test_expanded_catalog_rows_still_cannot_add_a_fifth_resource_put(self):
        template = copy.deepcopy(self.template)
        bindings = fixture_bindings(fixture_config())
        fixed = nsg._catalog('nsg-rule-priorities.json')
        rows = nsg._sql_rows(bindings, 'Redirect')
        template['variables']['rules'] = [
            {'name': 'nsg-test-fixture-' + nsg.SUFFIXES[r['group']] + '/' + r['targetName'],
             'rule': fixed['groups'][r['group']]['rules'][r['targetName']]}
            for r in rows]
        template['variables']['rules'].append({'name': 'nsg-test-fixture-apim/unrelated-fifth',
            'rule': fixed['groups']['apim']['rules']['allow-storage-egress']})
        resource, = template['resources'].values()
        parameters = {'resourcePrefix': 'test-fixture', 'bindings': nsg._sql_binding_view(bindings), 'targetMode': 'Redirect'}
        self.assertEqual(SqlUpgradeExpression(resource['copy']['count'], template).evaluate(parameters), 4)
        names = [SqlUpgradeExpression(resource['name'], template).evaluate(parameters, index) for index in range(4)]
        self.assertEqual(names, [r['name'] for r in template['variables']['rules'][:4]])
        self.assertNotIn(template['variables']['rules'][4]['name'], names)


if __name__ == '__main__':
    unittest.main()
