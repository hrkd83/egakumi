"""RunCommand Start503を再PUTせず、同一命令の読取だけで判定する。"""
import copy
import hashlib
import json
import unittest
from unittest import mock

from test_credential_broker_initial_deployment import BuildTransport as BrokerTransport, fixture as broker_fixture
from test_portal_image import BuildTransport as PortalTransport, fixture as portal_fixture
from deployment.helpers import credential_image, portal_image

SOURCE = b'independent public source fixture'
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()
DIGEST = 'sha256:' + 'a' * 64


class Start503Tests(unittest.TestCase):
    def execute(self, family, scenario, *, start_status=503, cleanup_failed=False):
        image, base, fixture = (credential_image, BrokerTransport, broker_fixture) if family == 'broker' else (portal_image, PortalTransport, portal_fixture)
        config, state = fixture()
        clock = [0]
        records = []
        class Transport(base):
            def __init__(self):
                if family == 'broker': super().__init__(config, state, SOURCE_HASH, cleanup_failed=cleanup_failed)
                else: super().__init__(cleanup_failed=cleanup_failed)
                self.command = None; self.requested = None; self.expanded = 0

            def request(self, method, path, body=None):
                result = super().request(method, path, body)
                target = path.split('?')[0]
                if method == 'PUT' and '/runCommands/' in target:
                    self.command = target; self.requested = copy.deepcopy(body)
                    if scenario == 'start-exception': raise TimeoutError('Synthetic Start delivery unknown')
                    return {'statusCode': start_status, 'headers': {}, 'body': {}}
                if method == 'GET' and target == self.command and '$expand=instanceView' in path:
                    self.expanded += 1
                    if start_status != 503:
                        result['body']['properties']['instanceView']['output'] = image.MARKER + json.dumps({'status':'built','sourceSha256':SOURCE_HASH,'imageDigest':DIGEST})
                        return result
                    if scenario == 'read-exception': raise TimeoutError('Synthetic read unknown')
                    if scenario in ('missing', 'forbidden'): return {'statusCode': 404 if scenario == 'missing' else 403, 'body': None}
                    if scenario == 'read-shape': return None
                    observed = copy.deepcopy(self.requested)
                    observed.update(id=target, name=target.rsplit('/', 1)[1], type='Microsoft.Compute/virtualMachines/runCommands')
                    properties = observed['properties']
                    properties.update(provisioningState='Succeeded', instanceView={
                        'executionState': 'Running' if scenario == 'running' and self.expanded == 1 else 'Succeeded',
                        'exitCode': 0, 'output': image.MARKER + json.dumps({'status':'built','sourceSha256':SOURCE_HASH,'imageDigest':DIGEST})})
                    if scenario == 'wrong-id': observed['id'] += '-other'
                    elif scenario == 'wrong-type': observed['type'] = 'Microsoft.Compute/virtualMachines'
                    elif scenario == 'wrong-name': observed['name'] += '-other'
                    elif scenario == 'wrong-location': observed['location'] = 'westus'
                    elif scenario == 'wrong-script': properties['source']['script'] += '\nexit 1'
                    elif scenario == 'other-source': properties['source']['scriptUri'] = 'https://example.invalid/script'
                    elif scenario == 'sync': properties['asyncExecution'] = False
                    elif scenario == 'timeout': properties['timeoutInSeconds'] = 2401
                    elif scenario == 'timeout-bool': properties['timeoutInSeconds'] = True
                    elif scenario == 'other-options': properties['treatFailureAsDeploymentFailure'] = False
                    elif scenario == 'run-as': properties['runAsUser'] = 'another-user'
                    elif scenario == 'argument': properties['parameters'] = [{'name':'other','value':'synthetic'}]
                    elif scenario == 'unknown-property': properties['unknownOption'] = True
                    elif scenario == 'unknown-execution': properties['instanceView']['executionState'] = 'Unrecognized'
                    elif scenario == 'unknown-provisioning': properties['provisioningState'] = 'Unrecognized'
                    elif scenario == 'guest-failed': properties['instanceView'] = {'executionState':'Failed','exitCode':73,'output':image.MARKER+'{"status":"failed","stage":"image-build"}'}
                    elif scenario == 'deadline': clock[0] = 2701
                    elif scenario == 'empty-defaults': properties.update(parameters=[],protectedParameters=[],runAsUser='',runAsPassword=None,outputBlobUri='',errorBlobUri=None)
                    return {'statusCode':200,'body':observed}
                return result
        transport = Transport()
        def save(value): records.append(copy.deepcopy(value))
        def sleep(seconds): clock[0] += seconds
        result = error = None
        try:
            result = image.build_image(config, state, transport, data=SOURCE, source_hash=SOURCE_HASH,
                record=save, sleep=sleep, monotonic=lambda:clock[0], progress=lambda *a, **k:None)
        except Exception as failure: error = failure
        starts = [call for call in transport.calls if call[0] == 'PUT' and '/runCommands/' in call[1]]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len([call for call in transport.calls if call[0]=='PUT' and '/roleAssignments/' in call[1]]), 1)
        self.assertEqual(len([call for call in transport.calls if call[0]=='DELETE' and '/runCommands/' in call[1]]), 1)
        self.assertEqual(len([call for call in transport.calls if call[0]=='DELETE' and '/roleAssignments/' in call[1]]), 1)
        self.assertTrue(records)
        for record in records:
            self.assertFalse(any(name in record for name in ('script','source','body','headers','output','error')))
        if not cleanup_failed:
            self.assertTrue(records[-1]['temporaryResourcesRemoved'])
            self.assertFalse(transport.present)
        return result, error, records, transport

    def test_503_matching_running_or_succeeded_uses_original_command_without_reput(self):
        for family in ('broker','portal'):
            for scenario in ('running','succeeded','empty-defaults'):
                with self.subTest(family=family,scenario=scenario):
                    result,error,records,transport=self.execute(family,scenario)
                    self.assertIsNone(error)
                    self.assertEqual(result,DIGEST)
                    self.assertEqual(records[-1]['status'],'built')
                    self.assertEqual(records[-1].get('startHttpStatus'),503)
                    self.assertEqual(records[-1].get('startAcceptance'),'existing-command-matched')
                    self.assertTrue(any(r.get('startAcceptance')=='unconfirmed' for r in records))
                    self.assertEqual(transport.expanded,2 if scenario=='running' else 1)

    def test_503_missing_unknown_or_mismatched_command_preserves_original_failure_and_cleanup(self):
        cases=('missing','forbidden','read-exception','read-shape','wrong-id','wrong-type','wrong-name','wrong-location',
               'wrong-script','other-source','sync','timeout','timeout-bool','other-options','run-as','argument',
               'unknown-property','unknown-execution','unknown-provisioning')
        for family in ('broker','portal'):
            for scenario in cases:
                with self.subTest(family=family,scenario=scenario):
                    result,error,records,transport=self.execute(family,scenario)
                    self.assertIsNone(result)
                    self.assertIsInstance(error,ValueError)
                    self.assertIn('failed (HTTP 503)',str(error))
                    self.assertEqual(records[-1]['status'],'failed')
                    self.assertEqual(records[-1].get('startHttpStatus'),503)
                    self.assertEqual(records[-1].get('startAcceptance'),'unconfirmed')
                    self.assertEqual(transport.expanded,1)

    def test_ordinary_success_has_no_added_get_or_503_receipt(self):
        for family in ('broker','portal'):
            with self.subTest(family=family):
                _,error,records,transport=self.execute(family,'normal',start_status=201)
                self.assertIsNone(error)
                self.assertEqual(transport.expanded,1)
                self.assertNotIn('startHttpStatus',records[-1])
                self.assertEqual([method for method,_,_ in transport.calls],
                    ['GET','GET','GET','PUT','PUT','GET','DELETE','GET','GET','DELETE','GET'])

    def test_other_status_and_start_exception_do_not_enter_503_readback(self):
        for family in ('broker','portal'):
            for status,scenario in [(502,'other'),(504,'other'),(403,'other'),(201,'start-exception')]:
                with self.subTest(family=family,status=status,scenario=scenario):
                    _,error,records,transport=self.execute(family,scenario,start_status=status)
                    self.assertIsNotNone(error)
                    self.assertEqual(transport.expanded,0)
                    self.assertNotIn('startHttpStatus',records[-1])

    def test_503_readback_does_not_extend_the_original_build_deadline(self):
        for family in ('broker','portal'):
            with self.subTest(family=family):
                result,error,records,_=self.execute(family,'deadline')
                self.assertIsNone(result)
                self.assertIn('wait limit',str(error))
                self.assertEqual(records[-1]['status'],'failed')
                self.assertEqual(records[-1].get('startHttpStatus'),503)

    def test_503_matched_guest_failure_or_cleanup_failure_never_returns_success(self):
        for family in ('broker','portal'):
            for scenario,cleanup in [('guest-failed',False),('succeeded',True)]:
                with self.subTest(family=family,scenario=scenario):
                    result,error,records,_=self.execute(family,scenario,cleanup_failed=cleanup)
                    self.assertIsNone(result)
                    self.assertIsNotNone(error)
                    self.assertEqual(records[-1].get('startHttpStatus'),503)
                    self.assertIn('cleanup did not finish' if cleanup else 'image-build',str(error))
