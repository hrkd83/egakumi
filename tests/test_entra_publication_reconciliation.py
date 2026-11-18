"""閉じた既存API再配備を合成ARMで検証する。readbackを生成元の証拠にしない。"""
import copy
import unittest
from contextlib import ExitStack
from unittest import mock

import test_entra_publication_bootstrap as bootstrap
from test_entra_publication import xml
from test_entra_publication import response
import entra_publication as publication


class ReconciliationTransport(bootstrap.InitialTransport):
    def __init__(self, original):
        super().__init__(copy.deepcopy(original.inventory), copy.deepcopy(original.plan), copy.deepcopy(original.source))
        self.members = set(original.members)
        self.reject_global = False
        self.reject_membership = None

    def request(self, method, path, body=None, headers=None):
        resource_id = path.split('?', 1)[0]
        if method == 'PUT' and ((resource_id == self.global_id and self.reject_global)
                                or resource_id == self.reject_membership):
            self.calls.append((method, resource_id, copy.deepcopy(body), copy.deepcopy(headers)))
            return {'statusCode': 412 if resource_id == self.global_id else 500, 'body': {}, 'headers': {}}
        return super().request(method, path, body, headers)


def reconciliation_fixture():
    base, plan, authentication, initial = bootstrap.controller_fixture()
    initial.install_stage()
    apim = plan['apimId']
    source = copy.deepcopy(initial.source)
    source['schemaVersion'] = 1
    source.pop('apiChildren')
    source['apis'] = source['apis'][:1]
    for entry in source['apis']:
        entry['properties'].pop('protocols')
    represented = source['apis'][0]['name']
    source['policies'] = [entry for entry in source['policies'] if '/apis/' + represented + '/' in entry['suffix']]
    known = publication.source_manifest(source, apim, [represented], authentication['previousValue'])
    unknown = next(resource_id for resource_id in plan['publishedApiIds'] if resource_id not in known.operations)
    # This actual XML is a backup witness, never a generated expectation.
    original = publication.OPEN_PRODUCT_POLICY.replace('<base />', '<base /><set-variable name="old-source-less-api" value="old" />', 1)
    initial.refresh(unknown + '/policies/policy', {'format': 'rawxml', 'value': original})
    initial.members = set(known.operations)
    initial.sync_members()
    transport = ReconciliationTransport(initial)
    desired = publication.source_manifest(transport.source, apim,
        [entry['name'] for entry in transport.source['apis']], authentication['value'])
    return base, plan, authentication, transport, known, desired, unknown


class ExistingSourceReconciliationTests(unittest.TestCase):
    def inventory(self, plan, template, transport, known):
        return publication.read_reconciliation_inventory(plan, template, transport, known_manifest=known)

    def run_reconciliation(self, inventory, desired, transport, *, stage=None, checkpoints=None, backups=None):
        checkpoints = [] if checkpoints is None else checkpoints
        backups = [] if backups is None else backups
        def save_backup(value):
            transport.calls.append(('BACKUP', 'witness', None, None))
            backups.append(value)
        def save_checkpoint():
            transport.calls.append(('CHECKPOINT', 'source', None, None))
            checkpoints.append(desired)
        def install():
            transport.calls.append(('STAGE', 'source', None, None))
            transport.install_stage()
            return desired
        return publication.reconcile_closed_publication(inventory, desired.policies[transport.global_id], transport,
            stage or install, desired_open_api_ids=set(desired.operations), on_staged=save_checkpoint,
            backup=save_backup, sleep=lambda _: None)

    def test_partial_old_source_is_verified_but_source_less_readbacks_are_only_backup_witnesses(self):
        base, plan, _, transport, known, desired, unknown = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        self.assertNotIsInstance(inventory, publication.PolicyManifest)
        self.assertEqual(set(inventory.api_ids), set(plan['publishedApiIds']))
        self.assertEqual(set(inventory.protected_api_ids), set(desired.operations))
        self.assertEqual(set(inventory.previous_members), set(known.operations))
        self.assertIn(unknown + '/policies/policy', inventory.snapshot)
        self.assertNotIn(unknown, known.operations)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_backup_close_stage_v2_readback_checkpoint_global_and_reopen_execute_in_order(self):
        base, plan, _, transport, known, desired, _ = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        native = copy.deepcopy(transport.inventory[plan['apimId'] + '/products/tool-users'])
        backups, checkpoints = [], []
        result = self.run_reconciliation(inventory, desired, transport, checkpoints=checkpoints, backups=backups)
        self.assertTrue(result['employeePathOpen'])
        self.assertEqual(len(backups), 1)
        self.assertEqual(checkpoints, [desired])
        writes = [(index, *call) for index, call in enumerate(transport.calls) if call[0] != 'GET']
        backup = next(index for index, method, *_ in writes if method == 'BACKUP')
        closures = [index for index, method, resource_id, *_ in writes if method == 'DELETE' and resource_id.startswith(transport.open_id + '/apis/')]
        stage = next(index for index, method, *_ in writes if method == 'STAGE')
        checkpoint = next(index for index, method, *_ in writes if method == 'CHECKPOINT')
        global_write = next(index for index, method, resource_id, *_ in writes if method == 'PUT' and resource_id == transport.global_id)
        openings = [index for index, method, resource_id, *_ in writes if method == 'PUT' and resource_id.startswith(transport.open_id + '/apis/')]
        self.assertTrue(closures)
        self.assertTrue(all(backup < index < stage for index in closures))
        self.assertLess(stage, checkpoint)
        self.assertLess(checkpoint, global_write)
        self.assertTrue(all(global_write < index for index in openings))
        self.assertEqual(transport.members, set(desired.operations))
        self.assertEqual(transport.inventory[plan['apimId'] + '/products/tool-users']['body']['properties'], native['body']['properties'])
        for api_id in desired.operations:
            self.assertTrue(transport.inventory[api_id]['body']['properties']['subscriptionRequired'])
        self.assertFalse(any(call[0] in ('PUT', 'DELETE', 'PATCH') and '/Microsoft.CognitiveServices/' in call[1] for call in transport.calls))

    def test_known_generated_source_drift_stops_before_backup_or_any_mutation(self):
        base, plan, _, transport, known, _, _ = reconciliation_fixture()
        api_id = next(iter(known.operations))
        transport.refresh(api_id + '/policies/policy', {'value': xml('changed-protected-policy')})
        with self.assertRaises(ValueError):
            self.inventory(plan, base.template, transport, known)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_explanatory_named_value_comment_is_not_an_unrepresented_dependency(self):
        base, plan, _, transport, known, _, unknown = reconciliation_fixture()
        node=transport.inventory[unknown + '/policies/policy']['body']['properties']
        node['value']=node['value'].replace('<inbound>','<!-- {{...}} describes Named values syntax --><inbound>')
        self.inventory(plan,base.template,transport,known)
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_source_less_policy_references_require_captured_backends_fragments_and_named_values(self):
        for reference in ('<set-backend-service backend-id="unrepresented"/>',
                          '<include-fragment fragment-id="unrepresented"/>',
                          '<set-variable name="fixture" value="{{unrepresented}}"/>'):
            with self.subTest(reference=reference):
                base,plan,_,transport,known,_,unknown=reconciliation_fixture()
                node=transport.inventory[unknown + '/policies/policy']['body']['properties']
                node['value']=node['value'].replace('<inbound>','<inbound>'+reference)
                with self.assertRaises(ValueError):
                    self.inventory(plan,base.template,transport,known)
                self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_unknown_tools_or_schemas_stop_before_existing_api_changes(self):
        for kind in ('tools','schemas'):
            with self.subTest(kind=kind):
                base,plan,_,transport,known,_,unknown=reconciliation_fixture()
                transport.inventory[unknown+'/'+kind]['body']['value'].append({'id':unknown+'/'+kind+'/unreviewed'})
                with self.assertRaises(ValueError):
                    self.inventory(plan,base.template,transport,known)
                self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_declared_subscription_state_and_scope_cannot_overwrite_gui_changes(self):
        for mismatch in ('state','scope'):
            with self.subTest(mismatch=mismatch):
                base,plan,_,transport,known,_,_=reconciliation_fixture()
                api=plan['parameters']['apiDefinitions']['value'][0]
                api['keySubscriptions']=[{'subscriptionId':'configured-key','enabled':True}]
                rid=plan['apimId']+'/subscriptions/configured-key'
                props={'state':'active','scope':plan['apimId']+'/products/'+api['productId']}
                props[mismatch]='suspended' if mismatch=='state' else plan['apimId']+'/products/unreviewed'
                transport.inventory[plan['apimId']+'/subscriptions']['body']['value'].append({'id':rid})
                transport.refresh(rid,props)
                with self.assertRaisesRegex(ValueError,'subscription scope/state'):
                    self.inventory(plan,base.template,transport,known)
                self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_failed_backup_does_not_close_or_change_any_existing_route(self):
        base,plan,_,transport,known,desired,_=reconciliation_fixture()
        inventory=self.inventory(plan,base.template,transport,known)
        def failed_backup(value):
            raise OSError('Synthetic protected backup failure')
        with self.assertRaises(OSError):
            publication.reconcile_closed_publication(inventory,desired.policies[transport.global_id],transport,
                lambda:desired,desired_open_api_ids=set(desired.operations),on_staged=lambda:None,
                backup=failed_backup,sleep=lambda _:None)
        self.assertEqual(transport.members,set(known.operations))
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_preflight_rejects_unknown_revision_scope_product_operation_or_key_requirement(self):
        for kind in ('revision', 'foreign-api', 'product', 'operation', 'key-required', 'path', 'type'):
            with self.subTest(kind=kind):
                base, plan, _, transport, known, _, unknown = reconciliation_fixture()
                if kind == 'revision':
                    transport.inventory[unknown + '/revisions']['body']['value'].append(
                        {'apiId': '/apis/' + unknown.rsplit('/', 1)[-1] + ';rev=2', 'apiRevision': '2', 'isCurrent': False, 'isOnline': True})
                elif kind == 'foreign-api':
                    transport.inventory[plan['apimId'] + '/apis']['body']['value'].append({'id': plan['apimId'] + '/apis/foreign-api'})
                elif kind == 'product':
                    transport.inventory[unknown + '/products']['body']['value'].append({'id': plan['apimId'] + '/products/unreviewed'})
                elif kind == 'operation':
                    transport.inventory[unknown + '/operations']['body']['value'].append({'id': unknown + '/operations/unreviewed'})
                else:
                    properties = transport.inventory[unknown]['body']['properties']
                    properties[{'key-required':'subscriptionRequired','path':'path','type':'type'}[kind]] = {
                        'key-required':False,'path':'unreviewed/path','type':'websocket'}[kind]
                with self.assertRaises(ValueError):
                    self.inventory(plan, base.template, transport, known)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_snapshot_etag_conflict_does_not_overwrite_concurrent_changes(self):
        base, plan, _, transport, known, desired, unknown = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        transport.refresh(unknown, transport.inventory[unknown]['body']['properties'])
        with self.assertRaises(ValueError):
            self.run_reconciliation(inventory, desired, transport)
        self.assertFalse(any(call[0] in ('PUT', 'DELETE', 'PATCH') for call in transport.calls))

    def test_stage_failure_keeps_employee_closed_and_native_key_products_unchanged(self):
        base, plan, _, transport, known, desired, _ = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        native = copy.deepcopy(transport.inventory[plan['apimId'] + '/products/tool-users'])
        def fail():
            raise publication.DeploymentError('Synthetic staging failure')
        checkpoints = []
        with self.assertRaises(publication.TransitionError) as caught:
            self.run_reconciliation(inventory, desired, transport, stage=fail, checkpoints=checkpoints)
        self.assertTrue(caught.exception.employee_path_closed)
        self.assertFalse(transport.members)
        self.assertFalse(checkpoints)
        self.assertEqual(transport.inventory[plan['apimId'] + '/products/tool-users'], native)
        self.assertFalse(any(call[0] == 'PUT' and call[1] == transport.global_id for call in transport.calls))

    def test_source_v1_or_incomplete_staged_source_cannot_reopen_employee_path(self):
        for kind in ('source-v1', 'missing-api', 'policy-drift'):
            with self.subTest(kind=kind):
                base, plan, _, transport, known, desired, unknown = reconciliation_fixture()
                inventory = self.inventory(plan, base.template, transport, known)
                def invalid_stage():
                    transport.install_stage()
                    if kind == 'source-v1':
                        return publication.PolicyManifest(desired.apim_id, desired.policies, desired.operations, desired.products,
                            desired.backends, desired.named_values, desired.api_properties, source_version=1)
                    if kind == 'missing-api':
                        reduced = copy.deepcopy(transport.source)
                        reduced['apis'] = [entry for entry in reduced['apis'] if entry['name'] != unknown.rsplit('/', 1)[-1]]
                        return publication.source_manifest(reduced, desired.apim_id,
                            [resource_id.rsplit('/', 1)[-1] for resource_id in known.operations], desired.policies[transport.global_id])
                    transport.refresh(unknown + '/policies/policy', {'value': xml('unexpected-staged-policy')})
                    return desired
                checkpoints = []
                with self.assertRaises(publication.TransitionError) as caught:
                    self.run_reconciliation(inventory, desired, transport, stage=invalid_stage, checkpoints=checkpoints)
                self.assertTrue(caught.exception.employee_path_closed)
                self.assertFalse(transport.members)
                self.assertFalse(checkpoints)
                self.assertFalse(any(call[0] == 'PUT' and call[1] == transport.global_id for call in transport.calls))

    def test_global_etag_conflict_leaves_employee_path_closed_after_successful_stage(self):
        base, plan, _, transport, known, desired, _ = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        transport.reject_global = True
        with self.assertRaises(publication.TransitionError) as caught:
            self.run_reconciliation(inventory, desired, transport)
        self.assertTrue(caught.exception.employee_path_closed)
        self.assertFalse(transport.members)
        self.assertFalse(any(call[0] == 'PUT' and call[1].startswith(transport.open_id + '/apis/') for call in transport.calls))

    def test_membership_failure_removes_opened_members_and_restores_owned_global(self):
        base, plan, authentication, transport, known, desired, _ = reconciliation_fixture()
        inventory = self.inventory(plan, base.template, transport, known)
        transport.reject_membership = transport.open_id + '/apis/' + sorted(desired.operations)[-1].rsplit('/', 1)[-1]
        with self.assertRaises(publication.TransitionError) as caught:
            self.run_reconciliation(inventory, desired, transport)
        self.assertTrue(caught.exception.employee_path_closed)
        self.assertTrue(caught.exception.recovery_complete)
        self.assertFalse(transport.members)
        self.assertEqual(transport.inventory[transport.global_id]['body']['properties']['value'], authentication['previousValue'])


class SharedInventoryCasingTests(unittest.TestCase):
    """ARM ID casing must not weaken dependency or scope checks (synthetic only)."""

    kinds = ('policyFragments', 'backends', 'namedValues', 'subscriptions')

    def add_shared(self, plan, transport, kind, *, name='fixture-shared', variant='mixed-all', properties=None):
        apim = plan['apimId']
        observed_scope = apim.swapcase() if variant in ('mixed-scope', 'mixed-all') else apim
        observed_kind = kind.lower() if variant in ('lower-kind', 'mixed-all') else kind
        observed_name = name.upper() if variant in ('upper-leaf', 'mixed-all') else name
        raw_id = observed_scope + '/' + observed_kind + '/' + observed_name
        requested_id = apim + '/' + kind + '/' + observed_name
        if properties is None:
            properties = {
                'policyFragments': {'format': 'rawxml', 'value': '<fragment><set-variable name="fixture" value="ok" /></fragment>'},
                'backends': {'protocol': 'http', 'url': 'https://backend.example.com/inference'},
                'namedValues': {'displayName': name, 'secret': False, 'value': 'fixture-value'},
                'subscriptions': {'state': 'active', 'scope': apim + '/products/tool-users'},
            }[kind]
        transport.inventory[apim + '/' + kind]['body']['value'].append({'id': raw_id})
        transport.inventory[requested_id] = response(raw_id, properties)
        return raw_id, requested_id

    def inventory(self, base, plan, transport, known):
        return publication.read_reconciliation_inventory(plan, base.template, transport, known_manifest=known)

    def test_shared_casing_is_accepted_and_raw_ids_and_xml_remain_in_backup(self):
        for kind in self.kinds:
            for variant in ('lower-kind', 'mixed-scope', 'upper-leaf', 'mixed-all'):
                with self.subTest(kind=kind, variant=variant):
                    base, plan, _, transport, known, _, _ = reconciliation_fixture()
                    raw_id, requested_id = self.add_shared(plan, transport, kind, variant=variant)
                    original = copy.deepcopy(transport.inventory[requested_id])
                    inventory = self.inventory(base, plan, transport, known)
                    self.assertEqual(inventory.snapshot[requested_id][2], original)
                    collection = inventory.snapshot[plan['apimId'] + '/' + kind][2]['body']['value']
                    self.assertIn({'id': raw_id}, collection)
                    self.assertEqual(inventory.snapshot[requested_id][2]['body']['id'], raw_id)
                    self.assertEqual(sum(key.casefold() == requested_id.casefold() for key in inventory.snapshot), 1)
                    self.assertTrue(all(call[0] == 'GET' for call in transport.calls))
                    self.assertTrue(any(call[1] == requested_id for call in transport.calls))
                    if kind == 'policyFragments':
                        self.assertEqual(inventory.snapshot[requested_id][2]['body']['properties']['value'],
                                         original['body']['properties']['value'])
                        self.assertIn('&format=rawxml', inventory.snapshot[requested_id][0])

    def test_case_only_duplicate_shared_ids_are_rejected_without_mutation(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                base, plan, _, transport, known, _, _ = reconciliation_fixture()
                resource_id = plan['apimId'] + '/' + kind + '/fixture-shared'
                transport.inventory[plan['apimId'] + '/' + kind]['body']['value'].extend(
                    [{'id': resource_id}, {'id': resource_id.swapcase()}])
                with self.assertRaises(ValueError):
                    self.inventory(base, plan, transport, known)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_shared_inventory_reuses_the_already_verified_generated_fragment_key(self):
        base, plan, _, transport, known, _, _ = reconciliation_fixture()
        fragment = '<fragment><set-variable name="fixture" value="ok" /></fragment>'
        raw_id, requested_id = self.add_shared(plan, transport, 'policyFragments', name='fixture-fragment',
            properties={'format': 'rawxml', 'value': fragment})
        canonical_id = plan['apimId'] + '/policyFragments/fixture-fragment'
        transport.inventory[canonical_id] = transport.inventory.pop(requested_id)
        known.policies[canonical_id] = fragment
        policy_id = next(iter(known.operations)) + '/policies/policy'
        value = known.policies[policy_id].replace('<base />',
            '<base /><include-fragment fragment-id="fixture-fragment" />', 1)
        known.policies[policy_id] = value
        transport.inventory[policy_id]['body']['properties']['value'] = value
        inventory = self.inventory(base, plan, transport, known)
        self.assertEqual(inventory.snapshot[canonical_id][2]['body']['id'], raw_id)
        self.assertNotIn(requested_id, inventory.snapshot)
        self.assertEqual(sum(key.casefold() == canonical_id.casefold() for key in inventory.snapshot), 1)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_foreign_scope_nested_children_and_invalid_shared_ids_are_rejected(self):
        for kind in self.kinds:
            for mismatch in ('foreign-scope', 'nested-child', 'empty-child', 'non-string'):
                with self.subTest(kind=kind, mismatch=mismatch):
                    base, plan, _, transport, known, _, _ = reconciliation_fixture()
                    apim = plan['apimId']
                    resource_id = {
                        'foreign-scope': apim + '-other/' + kind + '/fixture-shared',
                        'nested-child': apim + '/' + kind + '/fixture-shared/children/child',
                        'empty-child': apim + '/' + kind + '/',
                        'non-string': None,
                    }[mismatch]
                    transport.inventory[apim + '/' + kind]['body']['value'].append({'id': resource_id})
                    with self.assertRaises(ValueError):
                        self.inventory(base, plan, transport, known)
                    self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_policy_and_transitive_fragment_dependencies_use_canonical_shared_ids(self):
        base, plan, _, transport, known, _, unknown = reconciliation_fixture()
        fragment = '<fragment><set-backend-service backend-id="fixture-backend" />' \
                   '<set-variable name="fixture" value="{{fixture-named}}" /></fragment>'
        resources = [self.add_shared(plan, transport, 'policyFragments', name='fixture-fragment',
                                    properties={'format': 'rawxml', 'value': fragment}),
                     self.add_shared(plan, transport, 'backends', name='fixture-backend'),
                     self.add_shared(plan, transport, 'namedValues', name='fixture-named')]
        policy = transport.inventory[unknown + '/policies/policy']['body']['properties']
        policy['value'] = policy['value'].replace('<inbound>',
            '<inbound><include-fragment fragment-id="fixture-fragment" />')
        inventory = self.inventory(base, plan, transport, known)
        for raw_id, requested_id in resources:
            self.assertEqual(inventory.snapshot[requested_id][2]['body']['id'], raw_id)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_case_variant_fragment_cannot_hide_an_uncaptured_transitive_dependency(self):
        for dependency in ('<include-fragment fragment-id="uncaptured" />',
                           '<set-backend-service backend-id="uncaptured" />',
                           '<set-variable name="fixture" value="{{uncaptured}}" />'):
            with self.subTest(dependency=dependency):
                base, plan, _, transport, known, _, unknown = reconciliation_fixture()
                self.add_shared(plan, transport, 'policyFragments', name='fixture-fragment',
                    properties={'format': 'rawxml', 'value': '<fragment>' + dependency + '</fragment>'})
                policy = transport.inventory[unknown + '/policies/policy']['body']['properties']
                policy['value'] = policy['value'].replace('<inbound>',
                    '<inbound><include-fragment fragment-id="fixture-fragment" />')
                with self.assertRaises(ValueError):
                    self.inventory(base, plan, transport, known)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_declared_subscription_uses_captured_case_variant_and_checks_scope_and_state(self):
        for mismatch in (None, 'scope', 'state'):
            with self.subTest(mismatch=mismatch):
                base, plan, _, transport, known, _, _ = reconciliation_fixture()
                api = plan['parameters']['apiDefinitions']['value'][0]
                api['keySubscriptions'] = [{'subscriptionId': 'fixture-shared', 'enabled': True}]
                properties = {'state': 'active', 'scope': (plan['apimId'] + '/products/' + api['productId']).upper()}
                if mismatch:
                    properties[mismatch] = 'suspended' if mismatch == 'state' else plan['apimId'] + '/products/unreviewed'
                raw_id, requested_id = self.add_shared(plan, transport, 'subscriptions', properties=properties)
                if mismatch:
                    with self.assertRaisesRegex(ValueError, 'subscription scope/state'):
                        self.inventory(base, plan, transport, known)
                else:
                    inventory = self.inventory(base, plan, transport, known)
                    self.assertEqual(inventory.snapshot[requested_id][2]['body']['id'], raw_id)
                    self.assertEqual(sum(key.casefold() == requested_id.casefold() for key in inventory.snapshot), 1)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))


class ReconciliationControllerTests(unittest.TestCase):
    def controller(self):
        base, plan, authentication, transport, known, desired, unknown = reconciliation_fixture()
        previous = {'schemaVersion':1,
            'apis':[{'name':resource_id.rsplit('/',1)[-1], 'operations':list(names),
                     'products':list(known.products[resource_id]), 'properties':known.api_properties[resource_id]}
                    for resource_id,names in known.operations.items()],
            'policies':[{'suffix':resource_id[len(plan['apimId']):], 'value':value}
                        for resource_id,value in known.policies.items() if resource_id != transport.global_id],
            'backends':[{'suffix':resource_id[len(plan['apimId']):], 'properties':value} for resource_id,value in known.backends.items()],
            'namedValues':[{'suffix':resource_id[len(plan['apimId']):], 'properties':value} for resource_id,value in known.named_values.items()]}
        base.state['apiPublicationSourceCheckpoint'] = {'schemaVersion':1, 'apimId':plan['apimId'],
            'deploymentId':plan['deploymentId'], 'source':previous}
        transport.inventory[plan['deploymentId']] = response(plan['deploymentId'], {'provisioningState':'Succeeded',
            'outputs':{'publishedApiResourceIds':{'value':list(known.operations)}, 'entraPublicationSource':{'value':previous}}})
        authentication['previousEntra'] = {'enabled':True,'apiNames':[resource_id.rsplit('/',1)[-1] for resource_id in known.operations]}
        return base, plan, authentication, transport

    def execute(self, base, plan, authentication, transport, *, opt_in=True, checkpoint=True, backup=True, firewall_changed=False):
        publish = bootstrap.publish
        checkpoints, backups = [], []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(publish, 'prepare_plan', return_value=plan))
            stack.enter_context(mock.patch.object(publish, 'verify_common_authentication', return_value=authentication))
            stack.enter_context(mock.patch.object(publish, 'source_checkpoint', side_effect=bootstrap.transaction_checkpoint))
            stack.enter_context(mock.patch.object(publish, 'merge_firewall', return_value=({'properties':{}}, firewall_changed, '"unchanged"')))
            for name in ('verify_subscription_products','verify_credential_login','verify_websocket_diagnostics','initialize_profile_settings'):
                stack.enter_context(mock.patch.object(publish,name))
            for name in ('reconcile_unreleased_apis','enable_websocket_diagnostics','finalize_descriptions','cleanup_default_samples'):
                stack.enter_context(mock.patch.object(publish,name,return_value={}))
            stack.enter_context(mock.patch.object(publish,'state_from_outputs',return_value={'serviceResourceIds':base.state['serviceResourceIds']}))
            def save_checkpoint(value):
                checkpoints.append(copy.deepcopy(value))
            def save_backup(value):
                backups.append(value)
            result = publish.execute_plan(base.config,base.state,plan,base.template,transport,sleep=lambda _:None,
                checkpoint=save_checkpoint if checkpoint else None, backup=save_backup if backup else None,
                reconcile_existing_source=opt_in)
        return result, checkpoints, backups

    def test_source_less_existing_api_requires_explicit_reconciliation_option(self):
        base, plan, authentication, transport = self.controller()
        with self.assertRaises(ValueError):
            self.execute(base,plan,authentication,transport,opt_in=False)
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_reconciliation_requires_explicit_bool_backup_and_source_checkpoint_callbacks(self):
        for options in ({'opt_in':'true'},{'opt_in':1},{'backup':False},{'checkpoint':False}):
            with self.subTest(options=options):
                base, plan, authentication, transport = self.controller()
                with self.assertRaises(ValueError):
                    self.execute(base,plan,authentication,transport,**options)
                self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_explicit_reconciliation_never_mutates_firewall_to_resolve_drift(self):
        base, plan, authentication, transport = self.controller()
        with self.assertRaises(ValueError):
            self.execute(base,plan,authentication,transport,firewall_changed=True)
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_controller_checkpoints_only_full_v2_generated_source_before_reopening(self):
        base, plan, authentication, transport = self.controller()
        result, checkpoints, backups = self.execute(base,plan,authentication,transport)
        self.assertTrue(result['entraAuthentication']['employeePathOpen'])
        self.assertEqual(len(backups),1)
        self.assertEqual(len(checkpoints),1)
        self.assertEqual(checkpoints[0]['source']['schemaVersion'],2)
        self.assertEqual(checkpoints[0]['source'],transport.source)
        self.assertFalse(any(call[0] in ('PUT','DELETE','PATCH') and '/Microsoft.CognitiveServices/' in call[1] for call in transport.calls))
        self.assertFalse(any(call[0] in ('PUT','DELETE','PATCH') and call[1] == plan['firewall']['groupId'] for call in transport.calls))


class ProductAuthorizationPreflightTests(unittest.TestCase):
    def test_abbreviated_same_api_product_aliases_use_canonical_product_readback(self):
        _,plan,_,transport,known,_,_=reconciliation_fixture()
        api_id=next(iter(known.operations))
        transport.inventory[api_id+'/products']['body']['value']=[
            {'id':api_id+'/products/tool-users'},
            {'id':api_id+'/products/'+publication.ENTRA_OPEN_PRODUCT_ID}]
        bootstrap.publish.verify_subscription_products(plan,transport)
        self.assertTrue(any(call[0]=='GET' and call[1].startswith(plan['apimId']+'/products/tool-users') for call in transport.calls))
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_product_alias_from_another_api_or_resource_scope_is_rejected(self):
        for suffix in ('/apis/another-api/products/tool-users','/unrelated/products/tool-users'):
            with self.subTest(suffix=suffix):
                _,plan,_,transport,known,_,_=reconciliation_fixture()
                api_id=next(iter(known.operations))
                transport.inventory[api_id+'/products']['body']['value']=[{'id':plan['apimId']+suffix}]
                with self.assertRaises(ValueError):
                    bootstrap.publish.verify_subscription_products(plan,transport)
                self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_dedicated_employee_product_name_does_not_bypass_ownership_check(self):
        _,plan,_,transport,_,_,_=reconciliation_fixture()
        transport.inventory[transport.open_id]['body']['properties']['description']='unreviewed-owner'
        with self.assertRaisesRegex(ValueError,'conflicting ownership'):
            bootstrap.publish.verify_subscription_products(plan,transport)
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_abbreviated_list_or_false_list_metadata_cannot_override_actual_open_product(self):
        _,plan,_,transport,known,_,_=reconciliation_fixture()
        api_id=next(iter(known.operations))
        product_id=plan['apimId']+'/products/unreviewed-open'
        transport.inventory[api_id+'/products']['body']['value']=[{'id':product_id,'properties':{'subscriptionRequired':True}}]
        transport.refresh(product_id,{'subscriptionRequired':False})
        with self.assertRaisesRegex(ValueError,'subscription-free'):
            bootstrap.publish.verify_subscription_products(plan,transport)
        self.assertTrue(all(call[0]=='GET' for call in transport.calls))


if __name__ == '__main__':
    unittest.main()
