"""取引認証Browser APIの独立した生成元照合と、有効化前の停止を検証する。"""
import copy
import unittest

from tests.gateway.publication.test_entra_publication import APIM, SyntheticTransport, response
from tests.gateway.publication.test_entra_publication_bootstrap import InitialTransport, controller_fixture
import tests.gateway.publication.test_entra_publication_bootstrap as bootstrap
import entra_publication as publication


def browser_fixture(apim=APIM):
    api_id = apim + '/apis/credential-manager-browser'
    source = {'schemaVersion': 1, 'name': 'credential-manager-browser', 'apiPolicyAbsent': True,
        'properties': {'type': 'http', 'path': 'auth/connections/browser', 'protocols': ['http', 'https'], 'subscriptionRequired': False},
        'operations': [], 'policies': []}
    routes = {'launch': ('GET', '/launch'), 'launch-post': ('POST', '/launch'),
              'identity-callback': ('GET', '/identity-callback'), 'callback': ('GET', '/callback')}
    inventory = {
        api_id: response(api_id, source['properties']),
        api_id + '/policies/policy': {'statusCode': 404, 'body': {}, 'headers': {}},
        api_id + '/revisions': response(api_id + '/revisions', value=[{'apiId': '/apis/credential-manager-browser;rev=1',
            'apiRevision': '1', 'isCurrent': True, 'isOnline': True}]),
        api_id + '/operations': response(api_id + '/operations', value=[{'id': api_id + '/operations/' + name} for name in routes]),
        **{api_id + '/' + kind: response(api_id + '/' + kind, value=[]) for kind in ('products', 'tools', 'schemas')},
    }
    for name, (method, path) in routes.items():
        operation_id = api_id + '/operations/' + name
        properties = {'displayName': name, 'method': method, 'urlTemplate': path, 'responses': []}
        source['operations'].append({'name': name, 'properties': properties})
        value = publication.OPEN_PRODUCT_POLICY.replace('<inbound>', '<inbound><set-variable name="syntheticTransactionRoute" value="' + name + '"/>', 1)
        source['policies'].append({'suffix': operation_id[len(apim):] + '/policies/policy', 'value': value})
        inventory[operation_id] = response(operation_id, properties)
        inventory[operation_id + '/policies/policy'] = response(operation_id + '/policies/policy', {'format': 'rawxml', 'value': value})
    return {'apimId': apim, 'credentialLogin': {'synthetic': True}}, source, inventory


class CredentialBrowserSourceTests(unittest.TestCase):
    def test_complete_auxiliary_source_verifies_only_reads_and_cannot_open_product_membership(self):
        for raw_policy_id_absent in (False, True):
            with self.subTest(raw_policy_id_absent=raw_policy_id_absent):
                plan, source, inventory = browser_fixture()
                if raw_policy_id_absent:
                    for rid, value in inventory.items():
                        if '/operations/' in rid and rid.endswith('/policies/policy'):
                            value['body'].pop('id')
                transport = SyntheticTransport(inventory)
                snapshot = publication.verify_browser_publication_source(plan, source, transport)
                self.assertEqual(len(snapshot), 15)
                self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))
                self.assertEqual(snapshot[APIM + '/apis/credential-manager-browser/products'][2]['body']['value'], [])
                self.assertEqual(source['properties']['subscriptionRequired'], False)

    def test_missing_malformed_or_incomplete_source_fails_before_readback(self):
        plan, source, inventory = browser_fixture()
        invalid = [None, {}, {**source, 'schemaVersion': True}, {**source, 'apiPolicyAbsent': False},
                   {**source, 'name': 'foreign-api'}, {**source, 'operations': source['operations'][:3]},
                   {**source, 'policies': source['policies'][:3]}, {**source, 'extra': 'unrepresented'}]
        for value in invalid:
            with self.subTest(source=value):
                transport = SyntheticTransport(copy.deepcopy(inventory))
                with self.assertRaises(ValueError):
                    publication.verify_browser_publication_source(plan, value, transport)
                self.assertEqual(transport.calls, [])

    def test_readback_policy_operation_product_revision_and_child_drift_are_rejected(self):
        for kind in ('policy', 'routing', 'operation-extra', 'api-policy', 'product', 'tools', 'schemas', 'revision',
                     'foreign-id', 'foreign-operation', 'foreign-policy'):
            plan, source, inventory = browser_fixture()
            api = APIM + '/apis/credential-manager-browser'
            if kind == 'policy':
                inventory[api + '/operations/callback/policies/policy']['body']['properties']['value'] = publication.OPEN_PRODUCT_POLICY
            elif kind == 'routing': inventory[api + '/operations/launch']['body']['properties']['method'] = 'DELETE'
            elif kind == 'operation-extra': inventory[api + '/operations']['body']['value'].append({'id': api + '/operations/unrepresented'})
            elif kind == 'api-policy': inventory[api + '/policies/policy'] = response(api + '/policies/policy', {'value': publication.OPEN_PRODUCT_POLICY})
            elif kind in ('product', 'tools', 'schemas'):
                collection = 'products' if kind == 'product' else kind
                inventory[api + '/' + collection]['body']['value'] = [{'id': APIM + '/products/' + publication.ENTRA_OPEN_PRODUCT_ID}]
            elif kind == 'revision': inventory[api + '/revisions']['body']['value'][0]['apiRevision'] = '2'
            elif kind == 'foreign-id': inventory[api]['body']['id'] = APIM + '/apis/foreign-api'
            elif kind == 'foreign-operation': inventory[api + '/operations/launch']['body']['id'] = api + '/operations/foreign-operation'
            else: inventory[api + '/operations/callback/policies/policy']['body']['id'] = api + '/operations/foreign-operation/policies/policy'
            with self.subTest(kind=kind):
                transport = SyntheticTransport(inventory)
                with self.assertRaises(ValueError):
                    publication.verify_browser_publication_source(plan, source, transport)
                self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))

    def test_auxiliary_source_cannot_point_to_foreign_api_policies_or_duplicate_routes(self):
        for kind in ('foreign-policy', 'duplicate-policy', 'duplicate-operation', 'wrong-route'):
            plan, source, inventory = browser_fixture()
            if kind == 'foreign-policy': source['policies'][0]['suffix'] = '/apis/foreign-api/operations/launch/policies/policy'
            elif kind == 'duplicate-policy': source['policies'][-1] = source['policies'][0]
            elif kind == 'duplicate-operation': source['operations'][-1] = source['operations'][0]
            else: source['operations'][0]['properties']['urlTemplate'] = '/foreign'
            transport = SyntheticTransport(inventory)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                publication.verify_browser_publication_source(plan, source, transport)
            self.assertEqual(transport.calls, [])

    def test_etag_change_on_second_readback_fails_closed(self):
        plan, source, inventory = browser_fixture()
        transport = SyntheticTransport(inventory)
        target, count = APIM + '/apis/credential-manager-browser', 0
        def drift(method, rid, *_):
            nonlocal count
            if method == 'GET' and rid == target:
                count += 1
                if count == 2:
                    transport.refresh(target, inventory[target]['body']['properties'])
        transport.hook = drift
        with self.assertRaisesRegex(ValueError, 'drifted'):
            publication.verify_browser_publication_source(plan, source, transport)
        self.assertTrue(all(method == 'GET' for method, *_ in transport.calls))

    def test_disabled_credentials_accept_only_absent_browser_source(self):
        plan, source, inventory = browser_fixture()
        plan['credentialLogin'] = None
        transport = SyntheticTransport(inventory)
        for value in (None, {}):
            self.assertEqual(publication.verify_browser_publication_source(plan, value, transport), {})
        with self.assertRaises(ValueError):
            publication.verify_browser_publication_source(plan, source, transport)
        self.assertEqual(transport.calls, [])


class BrowserControllerTransport(InitialTransport):
    def __init__(self, original, browser_source, browser_inventory, *, emit_source=True):
        super().__init__(original.inventory, original.plan, original.source)
        self.browser_source, self.browser_inventory, self.emit_source = browser_source, browser_inventory, emit_source

    def install_stage(self):
        super().install_stage()
        self.inventory.update(copy.deepcopy(self.browser_inventory))
        api = self.apim + '/apis/credential-manager-browser'
        self.inventory[self.apim + '/apis']['body']['value'].append({'id': api})

    def sync_members(self):
        # Business Product mutations cannot manufacture browser memberships.
        api = self.apim + '/apis/credential-manager-browser'
        browser = self.inventory.pop(api, None)
        try:
            super().sync_members()
        finally:
            if browser is not None: self.inventory[api] = browser

    def request(self, method, path, body=None, headers=None):
        result = super().request(method, path, body, headers)
        if method == 'PUT' and path.split('?', 1)[0] == self.plan['deploymentId'] and self.emit_source:
            value = {'value': copy.deepcopy(self.browser_source)}
            self.inventory[self.plan['deploymentId']]['body']['properties']['outputs']['credentialBrowserPublicationSource'] = value
            result['body']['properties']['outputs']['credentialBrowserPublicationSource'] = copy.deepcopy(value)
        return result


class BrowserActivationTests(unittest.TestCase):
    def fixture(self, *, emit_source=True):
        base, plan, authentication, original = controller_fixture()
        plan['credentialLogin'] = {'synthetic': True}
        _, browser_source, inventory = browser_fixture(plan['apimId'])
        return base, plan, authentication, BrowserControllerTransport(original, browser_source, inventory, emit_source=emit_source)

    def test_browser_source_is_separately_checkpointed_before_global_activation(self):
        base, plan, authentication, transport = self.fixture()
        checkpoints = []
        result = bootstrap.BootstrapControllerTests('runTest').execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(checkpoints[0]['browserSource'], transport.browser_source)
        self.assertEqual(result['credentialBrowserPublicationSource'], transport.browser_source)
        self.assertNotIn('credential-manager-browser', {entry['name'] for entry in checkpoints[0]['source']['apis']})
        api = plan['apimId'] + '/apis/credential-manager-browser'
        self.assertEqual(transport.inventory[api + '/products']['body']['value'], [])
        checkpoint = next(index for index, call in enumerate(transport.calls) if call[0] == 'CHECKPOINT')
        activation = next(index for index, call in enumerate(transport.calls) if call[0] == 'PUT' and call[1] == transport.global_id)
        self.assertLess(checkpoint, activation)
        self.assertFalse(any(call[0] == 'PUT' and call[1].endswith('/apis/credential-manager-browser') for call in transport.calls))

    def test_missing_or_drifting_browser_source_prevents_checkpoint_and_global_activation(self):
        for mode in ('missing', 'post-verification-drift'):
            base, plan, authentication, transport = self.fixture(emit_source=mode != 'missing')
            if mode == 'post-verification-drift':
                target = plan['apimId'] + '/apis/credential-manager-browser/operations/callback/policies/policy'
                reads = 0
                def drift(method, rid, *_):
                    nonlocal reads
                    if method == 'GET' and rid == target:
                        reads += 1
                        if reads == 3:
                            transport.refresh(target, {'value': publication.OPEN_PRODUCT_POLICY})
                transport.hook = drift
            checkpoints = []
            with self.subTest(mode=mode), self.assertRaises(publication.TransitionError):
                bootstrap.BootstrapControllerTests('runTest').execute(base, plan, authentication, transport, checkpoints)
            self.assertEqual(checkpoints, [])
            self.assertFalse(any(call[0] == 'PUT' and call[1] == transport.global_id for call in transport.calls))
            self.assertFalse(transport.members)


if __name__ == '__main__':
    unittest.main()
