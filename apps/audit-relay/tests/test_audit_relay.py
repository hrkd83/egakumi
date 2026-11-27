import asyncio
import base64
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from azure.core.exceptions import ResourceExistsError
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from starlette.datastructures import Headers

from audit_relay.evidence import AuditUnavailable
from audit_relay.projection import MASK, scrub_text
from audit_relay.relay import ApimTokenVerifier, Relay, create_relay_app
from audit_relay.settings import (EventHubSettings, FOUNDRY_MESSAGES_DOMAIN, FOUNDRY_MESSAGES_SCOPE,
                                  FOUNDRY_OPENAI_DOMAIN, FOUNDRY_SCOPE, RelaySettings, origin)
from audit_relay.sse import frames
from audit_relay.worker import ArchiveWorker, read_record


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

        claude = path.startswith('/anthropic/')
        sample_account = 'synthetic-provider'
        expected_origin = f'https://{sample_account}.{FOUNDRY_OPENAI_DOMAIN}'
        expected_domain = FOUNDRY_MESSAGES_DOMAIN if claude else FOUNDRY_OPENAI_DOMAIN

        async def token(scope):
            self.assertEqual(scope, FOUNDRY_MESSAGES_SCOPE if claude else FOUNDRY_SCOPE)
            return SimpleNamespace(token='synthetic-provider-credential')

        async def backend(request):
            actions.append('provider-call')
            self.assertEqual(request.url.path, path if claude else '/openai/v1' + path)
            self.assertEqual(request.url.host, f'{sample_account}.{expected_domain}')
            self.assertEqual(request.url.query, query.removeprefix('?').encode('ascii'))
            self.assertEqual(request.headers['authorization'], 'Bearer synthetic-provider-credential')
            self.assertEqual(request.headers['x-ms-client-request-id'], REQUEST_ID)
            self.assertNotIn('cookie', request.headers)
            self.assertNotIn('api-key', request.headers)
            if claude:
                self.assertEqual(request.headers['anthropic-version'], '2023-06-01')
                self.assertEqual(request.headers['anthropic-beta'], 'synthetic-beta')
            self.assertEqual(json.loads(await request.aread()), request_body)
            return response

        request_body = body or {'model': 'example-model', 'messages': [{'role': 'user',
            'content': 'Send a message to recipient@example.com. password: synthetic-password.'}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as provider:
            settings = replace(SETTINGS, endpoint=expected_origin)
            app = create_relay_app(settings)
            app.state.relay = Relay(settings, SimpleNamespace(accepts=auth), publisher,
                                    SimpleNamespace(get_token=token), provider)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://relay.example.com') as client:
                actual = await client.post(path + query, json=request_body, headers={
                    'authorization': 'Bearer synthetic-managed-identity', 'cookie': 'synthetic-cookie',
                    'api-key': 'sample', 'anthropic-version': '2023-06-01', 'anthropic-beta': 'synthetic-beta',
                    'x-ms-client-request-id': REQUEST_ID,
                })
        return actual, actions, b'\n'.join(publisher.records)

    async def test_claude_messages_and_count_tokens_use_same_fixed_foundry_and_keep_wire_response(self):
        for ending, payload in (('', {'content': [{'type': 'text', 'text': 'Actual Claude output.'}]}),
                                ('/count_tokens', {'input_tokens': 14})):
            with self.subTest(ending=ending):
                raw = json.dumps(payload, indent=1).encode('utf-8')
                result, actions, evidence = await self.relay(
                    httpx.Response(200, stream=Bytes(raw), headers={'content-type': 'application/json',
                            'request-id': 'synthetic-provider-id', 'anthropic-ratelimit-requests-remaining': '3'}),
                    path='/anthropic/v1/messages' + ending, query='?beta=true',
                    body={'model': 'example-model', 'messages': [{'role': 'user', 'content': 'Prompt for Claude.'}]})
                self.assertEqual(result.content, raw)
                self.assertEqual(result.headers['request-id'], 'synthetic-provider-id')
                self.assertEqual(result.headers['anthropic-ratelimit-requests-remaining'], '3')
                self.assertEqual(actions, ['request', 'provider-call', 'response'])
                self.assertIn(b'Prompt for Claude.', evidence)
                self.assertIn(b'input_tokens' if ending else b'Actual Claude output.', evidence)

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


class HttpEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def auth(headers):
            return headers.get('authorization') == 'Bearer synthetic-managed-identity'

        self.publisher = MockPublisher([])
        settings = replace(SETTINGS, max_http_bytes=32 * 1024)
        self.app = create_relay_app(settings)
        self.app.state.relay = Relay(settings, SimpleNamespace(accepts=auth), self.publisher, None, None)
        self.client = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                                  base_url='https://relay.example.invalid'))
        self.headers = {
            'authorization': 'Bearer synthetic-managed-identity',
            'x-ms-client-request-id': REQUEST_ID,
            'x-core-audit-api': 'cohere-inference-v1',
            'x-core-audit-operation': 'rerank',
            'x-core-audit-direction': 'request',
        }

    async def test_complete_exchange_uses_one_capture_and_archivable_contiguous_sequences(self):
        request_body = json.dumps({
            'documents': ['業務文書の監査。 ' * 400],
            'authorizationCode': 'synthetic-oauth-code',
            'query': 'Find the policy. Password=synthetic-password',
        }, ensure_ascii=False).encode('utf-8')
        request = await self.client.post('/v1/evidence', headers=self.headers | {
            'cookie': 'synthetic-cookie', 'content-type': 'application/json',
            'x-core-audit-content-encoding': 'identity', 'x-core-audit-body-transfer-encoding': 'base64',
        }, content=base64.b64encode(request_body))
        self.assertEqual(request.status_code, 201)
        receipt = request.json()
        self.assertGreater(receipt['nextSequence'], 1)
        response = await self.client.post('/v1/evidence', headers=self.headers | {
            'x-core-audit-direction': 'response', 'x-core-audit-capture-id': receipt['captureId'],
            'x-core-audit-next-sequence': str(receipt['nextSequence']), 'x-core-audit-status': '200',
        }, json={'results': [{'index': 0, 'relevance_score': 0.8}]})
        self.assertEqual(response.status_code, 204)

        records = [read_record(SimpleNamespace(body_as_str=lambda encoding, payload=payload: payload.decode(encoding)))[0]
                   for payload in self.publisher.records]
        self.assertEqual({record['captureId'] for record in records}, {receipt['captureId']})
        self.assertEqual({record['operationId'] for record in records}, {'cohere-inference-v1/rerank'})
        self.assertEqual([record['sequence'] for record in records], list(range(len(records))))
        self.assertEqual(records[-1]['sequence'], receipt['nextSequence'])
        self.assertEqual(self.publisher.actions, ['request', 'response'])
        input_body = json.loads(''.join(record['data'] for record in records if record['kind'] == 'request'))['body']
        self.assertEqual(input_body['documents'], ['業務文書の監査。 ' * 400])
        self.assertEqual(input_body['authorizationCode'], '[SECRET]')
        self.assertEqual(input_body['query'], 'Find the policy. [SECRET]')
        self.assertEqual(json.loads(records[-1]['data']), {'httpStatus': 200, 'body': {
            'results': [{'index': 0, 'relevance_score': 0.8}]}})
        self.assertNotIn(b'synthetic-cookie', b''.join(self.publisher.records))

    async def test_absence_or_unsupported_media_is_explicit_and_never_written_as_raw_data(self):
        for content, selected, expected in (
            (b'', {}, {'bodyOmitted': 'empty'}),
            (b'opaque payload', {'content-type': 'image/png'}, {'bodyOmitted': 'binary', 'bodyBytes': 14,
             'mediaType': 'image/png', 'sha256': sha256(b'opaque payload').hexdigest()}),
            (b'encoded payload', {'content-type': 'application/json', 'x-core-audit-content-encoding': 'gzip'},
             {'bodyOmitted': 'content-encoding', 'bodyBytes': 15}),
            (b'', {'x-core-audit-body-omission': 'streaming'}, {'bodyOmitted': 'streaming'}),
            (b'a' * 32769, {'content-type': 'text/plain'}, {'bodyOmitted': 'transport-size', 'mediaType': 'text/plain'}),
        ):
            with self.subTest(selected=selected):
                response = await self.client.post('/v1/evidence', headers=self.headers | selected, content=content)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(json.loads(json.loads(self.publisher.records[-1])['data']), expected)

    async def test_multimedia_keeps_business_text_and_integrity_without_raw_audio_or_images(self):
        raw_audio = b'synthetic-opaque-audio'
        response = await self.client.post('/v1/evidence', headers=self.headers, data={
            'model': 'example-transcription', 'prompt': 'This is an audit example.',
            'api_key': 'sample',
        }, files=[
            ('image[]', ('synthetic-a.wav', raw_audio, 'audio/wav')),
            ('image[]', ('synthetic-b.wav', raw_audio, 'audio/wav')),
            ('access_token', ('unnamed.txt', b'synthetic-uploaded-credential', 'text/plain')),
        ])
        self.assertEqual(response.status_code, 201)
        document = json.loads(json.loads(self.publisher.records[-1])['data'])
        self.assertEqual(document['body']['fields'], [
            {'model': 'example-transcription'}, {'prompt': 'This is an audit example.'},
            {'api_key': MASK}, {'access_token': MASK}])
        self.assertEqual(document['attachments'], [
            {'part': part, 'mediaType': 'audio/wav', 'bodyBytes': len(raw_audio), 'sha256': sha256(raw_audio).hexdigest()}
            for part in (3, 4)])
        self.assertNotIn(raw_audio, self.publisher.records[-1])
        self.assertNotIn(b'synthetic-a.wav', self.publisher.records[-1])
        self.assertNotIn(b'synthetic-uploaded-credential', self.publisher.records[-1])

        raw_image = b'\x89PNG\r\n\x1a\nsynthetic-image'
        encoded = base64.b64encode(raw_image).decode('ascii')
        response = await self.client.post('/v1/evidence', headers=self.headers, json={
            'data': [{'b64_json': encoded, 'revised_prompt': 'An image audit example.'}],
            'base64Source': base64.b64encode(b'%PDF-synthetic-document').decode('ascii'),
            'input_image': 'data:image/png;base64,' + encoded, 'input_image_8': encoded,
            'ticket': 'synthetic-access-ticket',
        })
        self.assertEqual(response.status_code, 201)
        document = json.loads(json.loads(self.publisher.records[-1])['data'])
        self.assertEqual(document['body'], {'data': [{'revised_prompt': 'An image audit example.'}],
                                            'ticket': '[SECRET]'})
        self.assertEqual([attachment['mediaType'] for attachment in document['attachments']],
                         ['image/png', 'application/pdf', 'image/png', 'image/png'])
        self.assertEqual(document['attachments'][0]['sha256'], sha256(raw_image).hexdigest())
        self.assertNotIn(encoded.encode('ascii'), self.publisher.records[-1])

    async def test_ssml_preserves_spoken_text_and_masks_escaped_signed_query(self):
        response = await self.client.post('/v1/evidence', headers=self.headers | {'content-type': 'application/ssml+xml'},
            content=b'<speak>Speak this text.<audio src="https://example.invalid/media?sv=example&amp;sig=sample"/></speak>')
        self.assertEqual(response.status_code, 201)
        record = self.publisher.records[-1]
        self.assertIn(b'Speak this text.', record)
        self.assertNotIn(b'sig=sample', record)
        self.assertIn(MASK.encode('ascii'), record)

    async def test_invalid_callers_envelopes_and_outage_do_not_acknowledge_evidence(self):
        for headers, status in (
            ({'authorization': 'Bearer synthetic-delegated-token'}, 403),
            ({'x-core-audit-operation': 'wrong/scope'}, 400),
            ({'x-core-audit-direction': 'response'}, 400),
            ({'x-core-audit-body-omission': 'transport-size'}, 400),
        ):
            with self.subTest(headers=headers):
                response = await self.client.post('/v1/evidence', headers=self.headers | headers, json={'sample': True})
                self.assertEqual(response.status_code, status)
        self.publisher.failure = 'request'
        response = await self.client.post('/v1/evidence', headers=self.headers, json={'sample': True})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.publisher.records, [])

    async def test_large_projection_keeps_evidence_gap_without_changing_business_limit(self):
        # 32 KiB transport budget; a legitimate body exceeding this fixture's projection budget.
        relay = self.app.state.relay
        self.app.state.relay = replace(relay, settings=replace(relay.settings, max_capture_bytes=128))
        response = await self.client.post('/v1/evidence', headers=self.headers, json={'text': 'x' * 1024})
        self.assertEqual(response.status_code, 201)
        record = json.loads(self.publisher.records[0])
        self.assertEqual(record['sequence'], 0)
        self.assertEqual(json.loads(record['data'])['bodyOmitted'], 'projection-size')


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
