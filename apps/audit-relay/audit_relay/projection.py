"""Select visible business fields before evidence leaves the relay."""
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
}
_OPAQUE_KEYS = {'imageurl', 'imagedata', 'audio', 'inputaudio', 'filedata', 'bytes', 'encryptedcontent'}


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
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in _SECRET_KEYS:
                safe[scrub_text(key)] = MASK
            elif normalized not in _OPAQUE_KEYS:
                safe[scrub_text(key)] = sanitize(item, depth + 1)
        return safe
    return value


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
