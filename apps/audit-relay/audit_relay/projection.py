"""Select visible business fields before evidence leaves the relay."""
import base64
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from html import unescape
import json
import re


MASK = "[SECRET]"
_PEM_START = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I)
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----", re.I)
_SECRET_VALUE = r'''(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;"'<>]+)'''
_SECRET_LABEL = (r"(?:authorization|api[_\s-]*key|subscription[_\s-]*key|password|passphrase|secret|"
                 r"(?:(?:access|refresh|session|id)[_\s-]*)?token|client[_\s-]*secret|credentials?|(?:set[_\s-]*)?cookie|"
                 r"account[_\s-]*key|private[_\s-]*key|connection[_\s-]*string|sharedaccesskey)")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{5,}", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:Set-)?Cookie[\"']?\s*:[^\r\n]+", re.I),
    re.compile(r"\b" + _SECRET_LABEL + r"[\"']?\s*[:=]\s*" + _SECRET_VALUE, re.I),
    re.compile(r"[?&](?:sig|signature|token|x-amz-signature|x-goog-signature|code|client_secret)=[^\s&#\"'<>]+", re.I),
    re.compile(r"\bhttps?://[^\s/:@]+:[^\s/@]+@", re.I),
    re.compile(r"\bdata:[a-z0-9.+/-]+;base64,[a-z0-9+/=]+", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.I | re.S),
)
_SECRET_KEYS = {
    'authorization', 'proxyauthorization', 'password', 'passphrase', 'secret', 'secretaccesskey',
    'accesstoken', 'refreshtoken', 'sessiontoken', 'idtoken', 'token', 'clientsecret', 'cookie', 'setcookie',
    'apikey', 'xapikey', 'subscriptionkey', 'ocpapimsubscriptionkey', 'sharedaccesssignature',
    'sig', 'signature', 'accountkey', 'privatekey', 'connectionstring', 'sharedaccesskey', 'credential',
    'credentials', 'secretkey', 'oauthclientsecret', 'oauthaccesstoken', 'oauthrefreshtoken',
    'authorizationcode', 'clientassertion', 'codeverifier', 'clientsecretexpiresat', 'proxycredential',
    'continuationtoken', 'connectiontoken', 'connectionticket', 'accessticket', 'ticket',
    'signedticket', 'sessionticket', 'stophandle', 'sdp',
}
_OPAQUE_KEYS = {'imageurl', 'imagedata', 'audio', 'inputaudio', 'filedata', 'bytes', 'encryptedcontent',
                'b64json', 'base64source', 'inputimage', *(f'inputimage{number}' for number in range(2, 9))}
_PLAIN_MEDIA = {'text/plain', 'text/vtt', 'application/x-subrip', 'application/ssml+xml'}
_MEDIA_TYPE = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")
_FORM_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,80}(?:\[\])?")
_INLINE_BINARY = re.compile(r"b64json|base64source|inputimage[2-8]?")


def normalized_key(key):
    return re.sub(r"[^a-z0-9]", "", key.lower())


def scrub_text(text, previous=""):
    """Return only the newly observed text, replacing recognized credential spans."""
    data = previous + text
    start = len(previous)
    spans = sorted((max(start, match.start()), match.end()) for pattern in _SECRET_PATTERNS
                   for match in pattern.finditer(data) if match.end() > start)
    fragments = []
    cursor = start
    for a, b in spans:
        if b <= cursor:
            continue
        if a >= cursor:
            fragments.extend((data[cursor:a], MASK))
        cursor = b
    fragments.append(data[cursor:])
    return ''.join(fragments)


def sanitize(value, depth=0):
    if depth > 32:
        return "[NESTING OMITTED]"
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value]
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = normalized_key(key)
            if normalized in _SECRET_KEYS:
                safe[scrub_text(key)] = MASK
            elif normalized not in _OPAQUE_KEYS:
                safe[scrub_text(key)] = sanitize(item, depth + 1)
        return safe
    return value


def binary_evidence(raw, media_type):
    """Describe an observed binary value without retaining the value."""
    return {'mediaType': media_type, 'bodyBytes': len(raw), 'sha256': sha256(raw).hexdigest()}


def evidence_media_type(content_type):
    media = content_type.split(';', 1)[0].strip().lower()
    return media if _MEDIA_TYPE.fullmatch(media) else None


def multipart_evidence(raw, content_type):
    if not content_type.isascii() or '\r' in content_type or '\n' in content_type:
        return {'bodyOmitted': 'invalid-multipart'}
    header = ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')
    message = BytesParser(policy=policy.default).parsebytes(header + raw)
    if not message.is_multipart() or message.defects:
        return {'bodyOmitted': 'invalid-multipart'}
    fields, attachments = [], []
    for index, part in enumerate(message.iter_parts()):
        name = part.get_param('name', header='content-disposition')
        if (index >= 128 or part.is_multipart() or part.get_content_disposition() != 'form-data'
                or not isinstance(name, str) or not _FORM_NAME.fullmatch(name)):
            return {'bodyOmitted': 'unsupported-multipart'}
        if normalized_key(name) in _SECRET_KEYS:
            fields.append({name: MASK})
            continue
        data = part.get_payload(decode=True)
        if part.defects or not isinstance(data, bytes):
            return {'bodyOmitted': 'invalid-multipart'}
        media = part.get_content_type()
        if part.get_filename() is not None or media != 'text/plain':
            attachments.append({'part': index} | binary_evidence(data, media))
        else:
            try:
                fields.append(sanitize({name: data.decode('utf-8')}))
            except UnicodeError:
                return {'bodyOmitted': 'invalid-multipart-text'}
    return {'body': {'fields': fields}, 'attachments': attachments}


def inline_binary_evidence(value):
    results = []
    def walk(item, depth=0):
        if depth > 32 or len(results) >= 128:
            return
        if isinstance(item, list):
            for entry in item:
                walk(entry, depth + 1)
        elif isinstance(item, dict):
            for key, entry in item.items():
                if len(results) >= 128:
                    break
                normalized = normalized_key(key)
                if _INLINE_BINARY.fullmatch(normalized) and isinstance(entry, str):
                    results.append(inline_part(entry, normalized))
                elif normalized not in _SECRET_KEYS and normalized not in _OPAQUE_KEYS:
                    walk(entry, depth + 1)
    walk(value)
    return results


def inline_part(encoded, field):
    media = 'application/octet-stream' if field == 'base64source' else 'image/unknown'
    if encoded.startswith('data:'):
        header, separator, body = encoded[5:].partition(',')
        if not separator or not header.endswith(';base64'):
            return {'bodyOmitted': 'invalid-inline-binary'}
        media = evidence_media_type(header) or media
        encoded = body
    if encoded.startswith(('https://', 'http://')):
        return {'bodyOmitted': 'binary-reference'}
    try:
        body = base64.b64decode(encoded, validate=True)
    except ValueError:
        return {'bodyOmitted': 'invalid-inline-binary'}
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        media = 'image/png'
    elif body.startswith(b'\xff\xd8\xff'):
        media = 'image/jpeg'
    elif body.startswith(b'RIFF') and body[8:12] == b'WEBP':
        media = 'image/webp'
    elif body.startswith(b'%PDF-'):
        media = 'application/pdf'
    return binary_evidence(body, media)


def project_http_evidence(raw, content_type, encoding):
    """Retain business text and JSON, describing binary values without storing them."""
    if not raw:
        return {'bodyOmitted': 'empty'}
    if encoding not in ('', 'identity'):
        return {'bodyOmitted': 'content-encoding', 'bodyBytes': len(raw)}
    media = evidence_media_type(content_type) or ''
    if media == 'application/json' or media.endswith('+json'):
        try:
            value = json.loads(raw.decode('utf-8'))
            attachments = inline_binary_evidence(value)
            return {'body': sanitize(value)} | ({'attachments': attachments} if attachments else {})
        except (ValueError, RecursionError):
            return {'bodyOmitted': 'invalid-json', 'bodyBytes': len(raw)}
    if media in _PLAIN_MEDIA:
        try:
            text = raw.decode('utf-8')
            return {'body': scrub_text(unescape(text) if media == 'application/ssml+xml' else text)}
        except UnicodeError:
            return {'bodyOmitted': 'invalid-text', 'bodyBytes': len(raw)}
    if media == 'multipart/form-data':
        try:
            return multipart_evidence(raw, content_type)
        except (ValueError, TypeError, RecursionError):
            return {'bodyOmitted': 'invalid-multipart'}
    if media.startswith(('audio/', 'image/')) or media in ('application/pdf', 'application/octet-stream'):
        return {'bodyOmitted': 'binary'} | binary_evidence(raw, media)
    return {'bodyOmitted': 'media-type', 'bodyBytes': len(raw)}


def fields(value, allowed):
    return {name: value[name] for name in allowed if name in value} if isinstance(value, dict) else {}


def arguments(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return "[UNPARSEABLE TOOL ARGUMENTS OMITTED]"
    return value


def tool_calls(value):
    if not isinstance(value, list):
        return []
    calls = []
    for call in value:
        selected = fields(call, ('id', 'type', 'index'))
        function = fields(call.get('function'), ('name', 'arguments')) if isinstance(call, dict) else {}
        if 'arguments' in function:
            function['arguments'] = arguments(function['arguments'])
        if function:
            selected['function'] = function
        calls.append(selected)
    return calls


def message(value):
    selected = fields(value, ('id', 'type', 'role', 'name', 'text', 'refusal', 'status', 'tool_call_id', 'call_id'))
    if not isinstance(value, dict):
        return selected
    if 'content' in value:
        selected['content'] = content(value['content'])
    if 'output' in value and value.get('type') == 'function_call_output':
        selected['output'] = content(value['output'])
    if 'arguments' in value and value.get('type') == 'function_call':
        selected['arguments'] = arguments(value['arguments'])
    if 'tool_calls' in value:
        selected['tool_calls'] = tool_calls(value['tool_calls'])
    return selected


def content(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    return [part if isinstance(part, str) else section(part) for part in value]


def section(value):
    if not isinstance(value, dict):
        return {}
    kind = value.get('type')
    if kind in ('message', 'function_call', 'function_call_output') or 'role' in value:
        return message(value)
    if kind in ('input_text', 'output_text', 'text', 'refusal'):
        return fields(value, ('type', 'text', 'refusal'))
    return fields(value, ('type',))


def project_request(operation, value):
    selected = fields(value, ('model', 'stream', 'temperature', 'top_p', 'n', 'max_tokens',
                              'max_completion_tokens', 'max_output_tokens', 'store', 'background', 'conversation'))
    if operation == 'chat-completions' and isinstance(value.get('messages'), list):
        selected['messages'] = [message(item) for item in value['messages']]
    elif operation == 'responses-create':
        selected.update(fields(value, ('instructions',)))
        if 'input' in value:
            selected['input'] = content(value['input'])
    return sanitize(selected)


def project_response(operation, value, status):
    if status >= 400:
        return {'httpStatus': status, 'error': sanitize(fields(value.get('error'), ('type', 'code', 'param')))}
    selected = fields(value, ('id', 'object', 'model', 'status', 'usage', 'incomplete_details'))
    if operation == 'chat-completions' and isinstance(value.get('choices'), list):
        selected['choices'] = [fields(choice, ('index', 'finish_reason')) |
            {'message': message(choice.get('message'))} for choice in value['choices'] if isinstance(choice, dict)]
    if operation == 'responses-create' and 'output' in value:
        selected['output'] = content(value['output'])
    return sanitize(selected)
