"""APIM-only relay. Broker acknowledgement is required before forwarding."""
from contextlib import asynccontextmanager
import asyncio
import base64
from dataclasses import dataclass
import json
import logging
from uuid import UUID

from azure.identity.aio import ManagedIdentityCredential
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx
import jwt

from .claude import project_claude_request, project_claude_response
from .evidence import AuditUnavailable, Capture, EvidenceLimit, PART_CHARACTERS, Publisher, json_bytes
from .projection import evidence_media_type, project_http_evidence, project_request, project_response
from .settings import CLAUDE_ROUTES, FOUNDRY_MESSAGES_SCOPE, FOUNDRY_SCOPE, HTTP_EVIDENCE_OPERATION, ROUTES, RelaySettings
from .sse import StreamError, StreamWindow, frames, stream_projection


LOGGER = logging.getLogger('audit-relay')
_RESPONSE_HEADERS = frozenset(('content-type', 'cache-control', 'retry-after', 'retry-after-ms',
    'apim-request-id', 'x-ms-request-id', 'x-request-id', 'x-ms-client-request-id', 'x-ms-region',
    'openai-processing-ms', 'openai-version', 'x-ratelimit-limit-requests', 'x-ratelimit-limit-tokens',
    'x-ratelimit-remaining-requests', 'x-ratelimit-remaining-tokens', 'x-ratelimit-reset-requests',
    'x-ratelimit-reset-tokens'))
_ANTHROPIC_RESPONSE_HEADERS = frozenset({'request-id'} | {
    f'anthropic-ratelimit-{subject}-{attribute}'
    for subject in ('requests', 'tokens', 'input-tokens', 'output-tokens')
    for attribute in ('limit', 'remaining', 'reset')
})


class InvalidRequest(ValueError):
    pass


class ApimTokenVerifier:
    def __init__(self, settings):
        self.settings = settings
        authority = f'https://login.microsoftonline.com/{settings.tenant_id}'
        self.issuer = authority + '/v2.0'
        self.keys = jwt.PyJWKClient(authority + '/discovery/v2.0/keys', timeout=3)

    async def accepts(self, headers):
        values = headers.getlist('authorization')
        if len(values) != 1 or not values[0].startswith('Bearer ') or not 16 <= len(values[0]) <= 16384:
            return False
        return await asyncio.to_thread(self._verify, values[0][7:])

    def _verify(self, token):
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=self.settings.audience,
                issuer=self.issuer, leeway=30, options={'strict_aud': True,
                    'require': ['aud', 'exp', 'iat', 'iss', 'nbf', 'oid', 'tid', 'ver']})
            return (claims['ver'] == '2.0' and claims['tid'] == self.settings.tenant_id
                    and claims['oid'] == self.settings.apim_principal_id and 'scp' not in claims
                    and claims.get('idtyp', 'app') == 'app')
        except (jwt.PyJWTError, OSError, ValueError, TypeError):
            return False


def problem(status, code):
    return JSONResponse({'error': {'code': code}}, status_code=status, headers={'cache-control': 'no-store'})


def request_identifier(request):
    values = request.headers.getlist('x-ms-client-request-id')
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return values[0]
    except ValueError:
        raise InvalidRequest('Invalid APIM request ID') from None


def evidence_header(request, name, *, default=None):
    values = request.headers.getlist(name)
    if not values and default is not None:
        return default
    if len(values) != 1 or not 1 <= len(values[0]) <= 512:
        raise InvalidRequest('Invalid evidence header')
    return values[0]


def continuation(request, max_capture_bytes):
    identifier = evidence_header(request, 'x-core-audit-capture-id')
    capture = UUID(identifier)
    if capture.version != 4 or str(capture) != identifier:
        raise InvalidRequest('Invalid capture ID')
    sequence = evidence_header(request, 'x-core-audit-next-sequence')
    if not sequence.isascii() or not sequence.isdecimal() or len(sequence) > 8:
        raise InvalidRequest('Invalid evidence sequence')
    sequence = int(sequence)
    if not 1 <= sequence <= (max_capture_bytes + PART_CHARACTERS - 1) // PART_CHARACTERS:
        raise InvalidRequest('Invalid evidence sequence')
    status = evidence_header(request, 'x-core-audit-status')
    if len(status) != 3 or not status.isascii() or not status.isdecimal() or not 200 <= int(status) <= 599:
        raise InvalidRequest('Invalid response status')
    return identifier, sequence, int(status)


async def bounded_bytes(chunks, capacity):
    content = bytearray()
    async for chunk in chunks:
        if len(content) + len(chunk) > capacity:
            raise EvidenceLimit('HTTP body exceeds the audit route limit')
        content.extend(chunk)
    return bytes(content)


def object_body(body):
    try:
        value = json.loads(body.decode('utf-8'))
        if isinstance(value, dict):
            return value
    except (UnicodeError, ValueError, RecursionError):
        pass
    raise InvalidRequest('Expected a UTF-8 JSON object')


def media_type(header):
    return header.split(';', 1)[0].strip().lower()


def headers_for(upstream, *, claude=False):
    allowed = _RESPONSE_HEADERS | _ANTHROPIC_RESPONSE_HEADERS if claude else _RESPONSE_HEADERS
    return {name: value for name, value in upstream.headers.items() if name in allowed}


async def failure(capture, reason):
    LOGGER.warning('audit_%s requestId=%s operation=%s', reason, capture.request_id, capture.operation)
    try:
        await capture.save('failure', {'reason': reason})
    except AuditUnavailable:
        pass  # A missing terminal marker is also an incomplete capture.


async def audited_sse(upstream, capture):
    window = StreamWindow()
    output_bytes = 0
    try:
        async for frame in frames(upstream.aiter_raw()):
            view = stream_projection(capture.operation, frame.data) if frame.data is not None else None
            window.add(frame, view)
            terminal = view.terminal if view else None
            if not terminal and not window.ready():
                continue
            raw, selected = window.commit()
            if selected is not None:
                output_bytes += len(json_bytes(selected))
                if output_bytes > capture.max_capture_bytes:
                    raise EvidenceLimit('Cumulative SSE evidence exceeds limit')
                await capture.save('stream-segment', selected)
            if terminal:
                await capture.save('stream-end', {'state': terminal})
            yield raw
            if terminal:
                return
        await failure(capture, 'sse_incomplete')
    except AuditUnavailable:
        # Headers or prior body may already be on the wire. Keep prior bytes and end without a success event.
        LOGGER.warning('audit_publish_unavailable requestId=%s operation=%s', capture.request_id, capture.operation)
    except (StreamError, EvidenceLimit, httpx.HTTPError, ValueError, RecursionError):
        await failure(capture, 'sse_capture_failed')
    finally:
        await upstream.aclose()


@dataclass(frozen=True)
class Relay:
    settings: RelaySettings
    auth: ApimTokenVerifier
    publisher: Publisher
    credential: ManagedIdentityCredential
    provider: httpx.AsyncClient

    async def record(self, request):
        if not await self.auth.accepts(request.headers):
            return problem(403, 'AuditCallerDenied')
        if request.headers.get('content-encoding', 'identity').lower() != 'identity':
            return problem(400, 'AuditRequestUnsupported')
        try:
            request_id = request_identifier(request)
            operation = (evidence_header(request, 'x-core-audit-api') + '/'
                         + evidence_header(request, 'x-core-audit-operation'))
            direction = evidence_header(request, 'x-core-audit-direction')
            if not HTTP_EVIDENCE_OPERATION.fullmatch(operation) or direction not in ('request', 'response'):
                raise InvalidRequest('Invalid evidence scope')
            content_type = evidence_header(request, 'content-type', default='')
            encoding = evidence_header(request, 'x-core-audit-content-encoding', default='').lower()
            transfer = evidence_header(request, 'x-core-audit-body-transfer-encoding', default='raw')
            omission = evidence_header(request, 'x-core-audit-body-omission', default='')
            if omission not in ('', 'transport-size', 'streaming') or transfer not in ('raw', 'base64'):
                raise InvalidRequest('Invalid evidence omission')
            capture_args = {}
            document = {}
            if direction == 'response':
                capture_id, sequence, status = continuation(request, self.settings.max_capture_bytes)
                capture_args = {'capture_id': capture_id, 'sequence': sequence}
                document['httpStatus'] = status
        except ValueError:
            return problem(400, 'AuditRequestInvalid')

        try:
            maximum = self.settings.max_http_bytes
            wire_limit = ((maximum + 2) // 3) * 4 if transfer == 'base64' else maximum
            try:
                raw = await bounded_bytes(request.stream(), wire_limit)
                if transfer == 'base64':
                    raw = base64.b64decode(raw, validate=True)
                    if len(raw) > maximum:
                        raise EvidenceLimit('Decoded body exceeds the audit route limit')
            except EvidenceLimit:
                raw = b''
                omission = 'transport-size'
            if omission and raw:
                raise InvalidRequest('An omitted body must be empty')
            projection = project_http_evidence(raw, content_type, encoding)
            if omission:
                projection = {'bodyOmitted': omission}
                if media := evidence_media_type(content_type):
                    projection['mediaType'] = media
            capture = Capture(request_id, operation, self.publisher, self.settings.max_capture_bytes, **capture_args)
            try:
                await capture.save(direction, document | projection)
            except EvidenceLimit:
                await capture.save(direction, document | {'bodyOmitted': 'projection-size', 'bodyBytes': len(raw)})
        except (ValueError, RecursionError):
            return problem(400, 'AuditRequestInvalid')
        except AuditUnavailable:
            LOGGER.warning('audit_%s_unavailable requestId=%s operation=%s', direction, request_id, operation)
            return problem(503, 'AuditUnavailable')
        if direction == 'response':
            return Response(status_code=204)
        return JSONResponse({'captureId': capture.capture_id, 'nextSequence': capture.sequence},
                            status_code=201, headers={'cache-control': 'no-store'})

    async def forward(self, request, operation):
        if not await self.auth.accepts(request.headers):
            return problem(403, 'AuditCallerDenied')
        if request.headers.get('content-encoding', 'identity').lower() != 'identity':
            return problem(400, 'AuditRequestUnsupported')
        claude = operation in CLAUDE_ROUTES.values()
        try:
            request_id = request_identifier(request)
            raw = await bounded_bytes(request.stream(), self.settings.max_http_bytes)
            incoming = object_body(raw)
            selected_input = project_claude_request(incoming) if claude else project_request(operation, incoming)
            capture = Capture(request_id, operation, self.publisher, self.settings.max_capture_bytes)
            await capture.save('request', selected_input)
        except EvidenceLimit:
            return problem(413, 'AuditRequestTooLarge')
        except (ValueError, RecursionError):
            return problem(400, 'AuditRequestInvalid')
        except AuditUnavailable:
            LOGGER.warning('audit_input_unavailable requestId=%s operation=%s', request_id, operation)
            return problem(503, 'AuditUnavailable')

        try:
            async with asyncio.timeout(5):
                token = await self.credential.get_token(FOUNDRY_MESSAGES_SCOPE if claude else FOUNDRY_SCOPE)
            origin = self.settings.messages_endpoint if claude else self.settings.endpoint + '/openai/v1'
            provider_url = httpx.URL(origin + request.url.path).copy_with(query=request.scope.get('query_string') or None)
            headers = [('authorization', 'Bearer ' + token.token), ('content-type', 'application/json'),
                       ('accept', 'application/json, text/event-stream'), ('accept-encoding', 'identity'),
                       ('x-ms-client-request-id', request_id)]
            if claude:
                for name in ('anthropic-version', 'anthropic-beta'):
                    headers.extend((name, value) for value in request.headers.getlist(name))
            outbound = self.provider.build_request('POST', provider_url, content=raw, headers=headers)
            upstream = await self.provider.send(outbound, stream=True)
        except Exception:
            await failure(capture, 'provider_unavailable')
            return problem(502, 'AuditProviderUnavailable')

        is_streaming = False
        try:
            if upstream.headers.get('content-encoding', 'identity').lower() != 'identity':
                await failure(capture, 'provider_encoding_unsupported')
                return problem(502, 'AuditProviderEncodingUnsupported')
            content_type = media_type(upstream.headers.get('content-type', ''))
            if content_type == 'text/event-stream' and upstream.status_code < 400:
                await capture.save('stream-start', {'httpStatus': upstream.status_code})
                is_streaming = True
                return StreamingResponse(audited_sse(upstream, capture), status_code=upstream.status_code,
                    headers=headers_for(upstream, claude=claude))
            output = await bounded_bytes(upstream.aiter_raw(), self.settings.max_http_bytes)
            if content_type == 'application/json' or content_type.endswith('+json') or not content_type:
                body = object_body(output)
                selected = (project_claude_response(body, upstream.status_code) if claude
                            else project_response(operation, body, upstream.status_code))
            elif upstream.status_code >= 400:
                selected = {'httpStatus': upstream.status_code, 'bodyOmitted': 'non-json-error'}
            else:
                raise InvalidRequest('Unexpected provider media type')
            await capture.save('response', {'httpStatus': upstream.status_code, 'body': selected})
            return Response(output, status_code=upstream.status_code, headers=headers_for(upstream, claude=claude))
        except AuditUnavailable:
            LOGGER.warning('audit_output_unavailable requestId=%s operation=%s', request_id, operation)
            return problem(503, 'AuditUnavailable')
        except (httpx.HTTPError, ValueError, RecursionError):
            await failure(capture, 'provider_capture_failed')
            return problem(502, 'AuditProviderCaptureFailed')
        finally:
            if not is_streaming:
                await upstream.aclose()


def create_relay_app(settings=None):
    settings = settings or RelaySettings.load()

    @asynccontextmanager
    async def lifespan(app):
        async with ManagedIdentityCredential(client_id=settings.hub.client_id) as credential:
            async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=5, pool=5), trust_env=False) as provider:
                publisher = Publisher(settings.hub, credential, settings.publish_timeout)
                app.state.relay = Relay(settings, ApimTokenVerifier(settings), publisher, credential, provider)
                try:
                    yield
                finally:
                    await publisher.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    async def health():
        return Response(status_code=200)

    @app.get('/ready')
    async def ready(request: Request):
        # Verify connectivity before activation. After activation, keep returning explicit audit errors.
        if not getattr(app.state, 'publisher_warmed', False):
            try:
                await request.app.state.relay.publisher.warm_up()
                app.state.publisher_warmed = True
            except Exception:
                return Response(status_code=503)
        return Response(status_code=200)

    async def chat_completions(request: Request):
        return await request.app.state.relay.forward(request, ROUTES['/chat/completions'])

    async def responses(request: Request):
        return await request.app.state.relay.forward(request, ROUTES['/responses'])

    async def record(request: Request):
        return await request.app.state.relay.record(request)

    async def claude_messages(request: Request):
        return await request.app.state.relay.forward(request, CLAUDE_ROUTES['/anthropic/v1/messages'])

    async def claude_count_tokens(request: Request):
        return await request.app.state.relay.forward(request, CLAUDE_ROUTES['/anthropic/v1/messages/count_tokens'])

    app.add_api_route('/chat/completions', chat_completions, methods=['POST'])
    app.add_api_route('/responses', responses, methods=['POST'])
    app.add_api_route('/v1/evidence', record, methods=['POST'])
    app.add_api_route('/anthropic/v1/messages', claude_messages, methods=['POST'])
    app.add_api_route('/anthropic/v1/messages/count_tokens', claude_count_tokens, methods=['POST'])
    return app
