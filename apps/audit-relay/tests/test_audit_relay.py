import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from azure.core.exceptions import ResourceExistsError
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from starlette.datastructures import Headers

from audit_relay.evidence import AuditUnavailable
from audit_relay.projection import scrub_text
from audit_relay.relay import ApimTokenVerifier, Relay, create_relay_app
from audit_relay.settings import EventHubSettings, FOUNDRY_SCOPE, RelaySettings, origin
from audit_relay.sse import frames
from audit_relay.worker import ArchiveWorker


REQUEST_ID, TENANT, AUDIENCE, PRINCIPAL = (str(UUID(int=index, version=4)) for index in range(1, 5))
SETTINGS = RelaySettings(EventHubSettings('audit.example.com', 'test', PRINCIPAL), TENANT, AUDIENCE, PRINCIPAL,
                         'https://provider.example.com')


def frame(data, newline=b'\n'):
    return b'data: ' + data + newline * 2


class Bytes(httpx.AsyncByteStream):
    def __init__(self, *parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


class MockPublisher:
    def __init__(self, actions, failure=None):
        self.actions = actions
        self.records = []
        self.failure = failure

    async def publish(self, request_id, records):
        assert request_id == REQUEST_ID
        parsed = [json.loads(record) for record in records]
        kind = parsed[0]['kind']
        if self.failure == kind:
            raise AuditUnavailable('synthetic outage')
        self.actions.append(kind)
        self.records.extend(records)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def relay(self, response, failure=None, path='/chat/completions', body=None, query=''):
        actions = []
        publisher = MockPublisher(actions, failure)

        async def auth(headers):
            return headers.get('authorization') == 'Bearer synthetic-managed-identity'

        async def token(scope):
            self.assertEqual(scope, FOUNDRY_SCOPE)
            return SimpleNamespace(token='synthetic-provider-credential')

        async def backend(request):
            actions.append('provider-call')
            self.assertEqual(request.url.path, '/openai/v1' + path)
            self.assertEqual(request.url.query, query.removeprefix('?').encode('ascii'))
            self.assertEqual(request.headers['authorization'], 'Bearer synthetic-provider-credential')
            self.assertEqual(request.headers['x-ms-client-request-id'], REQUEST_ID)
            self.assertNotIn('cookie', request.headers)
            self.assertEqual(json.loads(await request.aread()), request_body)
            return response

        request_body = body or {'model': 'example-model', 'messages': [{'role': 'user',
            'content': 'Send a message to recipient@example.com. password: synthetic-password.'}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as provider:
            app = create_relay_app(SETTINGS)
            app.state.relay = Relay(SETTINGS, SimpleNamespace(accepts=auth), publisher,
                                    SimpleNamespace(get_token=token), provider)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://relay.example.com') as client:
                actual = await client.post(path + query, json=request_body, headers={
                    'authorization': 'Bearer synthetic-managed-identity', 'cookie': 'synthetic-cookie',
                    'x-ms-client-request-id': REQUEST_ID,
                })
        return actual, actions, b'\n'.join(publisher.records)

    async def test_json_body_preserved_and_both_sides_acknowledged(self):
        raw = b'{ "choices": [{"index":0, "message":{"content":"Actual response. api_key=synthetic-key"}}] }'
        result, actions, evidence = await self.relay(httpx.Response(200, stream=Bytes(raw),
            headers={'content-type': 'application/json', 'x-ms-request-id': 'synthetic-provider-id'}))
        self.assertEqual(actual := result.content, raw, actual)
        self.assertEqual(result.headers['x-ms-request-id'], 'synthetic-provider-id')
        self.assertEqual(actions, ['request', 'provider-call', 'response'])
        self.assertIn(b'recipient@example.com', evidence)
        self.assertIn(b'Actual response.', evidence)
        self.assertNotIn(b'synthetic-cookie', evidence)
        self.assertNotIn(b'synthetic-password', evidence)
        self.assertNotIn(b'synthetic-key', evidence)

    async def test_input_ack_failure_never_calls_provider(self):
        result, actions, evidence = await self.relay(httpx.Response(200), 'request')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(actions, [])
        self.assertEqual(evidence, b'')

    async def test_query_reaches_fixed_provider_and_stays_out_of_evidence(self):
        raw = b'{"choices":[{"message":{"content":"Actual response."}}]}'
        result, _, evidence = await self.relay(httpx.Response(200, stream=Bytes(raw),
            headers={'content-type': 'application/json'}),
            query='?api-version=example&option=synthetic%20query&option=second')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(b'synthetic%20query', evidence)

    async def test_output_ack_failure_withholds_response_body(self):
        raw = b'{"choices":[{"message":{"content":"Actual response."}}]}'
        result, actions, evidence = await self.relay(httpx.Response(200, stream=Bytes(raw),
            headers={'content-type': 'application/json'}), 'response')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(actions, ['request', 'provider-call'])
        self.assertNotIn(b'Actual response', result.content + evidence)

    async def test_sse_reconstructs_business_text_and_redacts_split_secret(self):
        def delta(value):
            return frame(json.dumps({'choices': [{'index': 0, 'delta': {'content': value}}]}).encode(), b'\r\n')

        data = (delta('Send it to ') + delta('recipient@') + delta('example.com. ') + delta('Bearer ') +
                delta('do-not-') + delta('archive-this ') + frame(b'[DONE]', b'\r\n'))
        result, actions, evidence = await self.relay(httpx.Response(200,
            headers={'content-type': 'text/event-stream'}, stream=Bytes(*[data[i:i + 23] for i in range(0, len(data), 23)])))
        self.assertEqual(result.content, data)
        self.assertEqual(actions[:3], ['request', 'provider-call', 'stream-start'])
        self.assertEqual(actions[-1], 'stream-end')
        self.assertIn(b'recipient@example.com.', evidence)
        self.assertIn(b'[SECRET]', evidence)
        self.assertNotIn(b'do-not-', evidence)
        self.assertNotIn(b'archive-this', evidence)

    async def test_sse_ack_failure_ends_without_unarchived_data(self):
        unarchived = frame(b'{"choices":[{"index":0,"delta":{"content":"Actual response. "}}]}')
        result, actions, evidence = await self.relay(httpx.Response(200,
            headers={'content-type': 'text/event-stream'}, stream=Bytes(unarchived, frame(b'[DONE]'))), 'stream-segment')
        self.assertEqual(result.content, b'')
        self.assertEqual(actions, ['request', 'provider-call', 'stream-start'])
        self.assertNotIn(b'Actual response', evidence)

    async def test_responses_uses_final_text_only_when_no_deltas_delivered(self):
        items = [
            {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'delta': 'First answer. '},
            {'type': 'response.output_text.done', 'output_index': 0, 'content_index': 0, 'text': 'First answer. '},
            {'type': 'response.output_text.done', 'output_index': 1, 'content_index': 0, 'text': 'Second answer.'},
            {'type': 'response.completed', 'response': {'status': 'completed'}},
        ]
        outputs = [frame(json.dumps(item).encode()) for item in items]
        result, actions, evidence = await self.relay(httpx.Response(200,
            headers={'content-type': 'text/event-stream'}, stream=Bytes(*outputs)), path='/responses',
            body={'model': 'example-model', 'input': 'Retrieve my policy', 'stream': True})
        self.assertEqual(result.content, b''.join(outputs))
        self.assertEqual(actions[-1], 'stream-end')
        self.assertEqual(evidence.count(b'First answer.'), 1)
        self.assertIn(b'Second answer.', evidence)

    async def test_cr_frames_flush_at_end(self):
        data = b'\xef\xbb\xbfdata: sample\rdata: example\r\r'
        actual = [item async for item in frames(Bytes(data[:-1], data[-1:]))]
        self.assertEqual(actual[0].data, 'sample\nexample')
        self.assertEqual(actual[0].raw, data)

    def test_escapes_in_quoted_secret_and_multi_value_cookie(self):
        quoted_fixture = 'sample"credential'
        body = f'password={json.dumps(quoted_fixture)}. Message was sent.\nCookie: first=value1; second=value2'
        masked = scrub_text(body)
        self.assertIn('Message was sent.', masked)
        self.assertNotIn('credential', masked)
        self.assertNotIn('value1', masked)
        self.assertNotIn('value2', masked)

    async def test_managed_identity_token_requires_exact_principal_and_no_delegation(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        verifier = ApimTokenVerifier(SETTINGS)
        verifier.keys = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key()))
        claims = {'tid': TENANT, 'oid': PRINCIPAL, 'aud': AUDIENCE, 'iss': verifier.issuer, 'ver': '2.0',
                  'iat': now, 'nbf': now, 'exp': now + timedelta(minutes=2)}

        async def accepted(extra):
            token = jwt.encode(claims | extra, key, algorithm='RS256')
            return await verifier.accepts(Headers({'authorization': 'Bearer ' + token}))

        self.assertTrue(await accepted({}))
        self.assertFalse(await accepted({'scp': 'Gateway.Invoke'}))
        self.assertFalse(await accepted({'oid': REQUEST_ID}))
        self.assertFalse(await accepted({'aud': 'api://' + AUDIENCE}))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_origin_accepts_and_normalizes_blob_endpoint_slash(self):
        sample_origin = 'https://storage.example.invalid'
        with patch.dict('os.environ', {'AUDIT_STORAGE_ACCOUNT_URL': sample_origin + '/'}):
            self.assertEqual(origin('AUDIT_STORAGE_ACCOUNT_URL', 'example.invalid'), sample_origin)
        with patch.dict('os.environ', {'AUDIT_STORAGE_ACCOUNT_URL': sample_origin + '/unexpected'}):
            with self.assertRaises(RuntimeError):
                origin('AUDIT_STORAGE_ACCOUNT_URL', 'example.invalid')

    async def test_archive_retry_precedes_checkpoint_and_duplicate_is_safe(self):
        operations = []
        errors = []

        async def store(**record):
            operations.append(('write', record['name'], record['overwrite']))
            status, code = errors.pop(0)
            error = ResourceExistsError('simulated upload failure')
            error.error_code = 'BlobAlreadyExists'  # SDK may rewrite the surface code.
            error.response = SimpleNamespace(status_code=status, headers={'x-ms-error-code': code})
            raise error

        async def checkpoint(event):
            operations.append(('checkpoint', event.sequence_number))

        partition = SimpleNamespace(partition_id='0', update_checkpoint=checkpoint)
        payload = {'version': 1, 'requestId': REQUEST_ID, 'captureId': PRINCIPAL, 'operationId': 'chat-completions',
            'sequence': 0, 'timeUtc': '2026-01-01T00:00:00.000+00:00', 'kind': 'request', 'part': 0, 'parts': 1, 'data': '{}'}
        event = SimpleNamespace(sequence_number=1, body_as_str=lambda **_kwargs: json.dumps(payload))
        async def no_wait(_):
            return
        name = f'{REQUEST_ID}/{PRINCIPAL}/000000000.json'
        for duplicate in ((409, 'BlobAlreadyExists'), (412, 'ConditionNotMet')):
            with self.subTest(duplicate=duplicate):
                operations.clear()
                errors[:] = [(409, 'BlobImmutableDueToPolicy'), (412, 'LeaseIdMissing'), duplicate]
                worker = ArchiveWorker(None, None, None, SimpleNamespace(upload_blob=store))
                with patch('audit_relay.worker.asyncio.sleep', no_wait):
                    await asyncio.wait_for(worker.process(partition, event), 2)
                self.assertEqual(operations, [('write', name, False)] * 3 + [('checkpoint', 1)])


if __name__ == '__main__':
    unittest.main()
