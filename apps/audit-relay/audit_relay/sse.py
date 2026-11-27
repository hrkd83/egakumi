"""Parse a bounded audit copy while preserving the exact SSE wire frames."""
from dataclasses import dataclass
import json
import re

from .claude import (
    MESSAGE_FIELDS, OPAQUE_CONTENT_TYPES, TOOL_RESULT_TYPES, TOOL_TYPES, project_claude_tool_result, select_citation,
)
from .projection import (_PEM_END, _PEM_START, _SECRET_LABEL, fields, sanitize, scrub_text)


MAX_FRAME_BYTES = 128 * 1024
MAX_WINDOW_BYTES = 192 * 1024
MAX_WINDOW_FRAMES = 128
_NEWLINE = re.compile(rb"\r\n|\r|\n")
_OPEN_QUOTE = re.compile(r"\b" + _SECRET_LABEL + r'''["']?\s*[:=]\s*(?:"(?:\\.|[^"\\\r\n])*|'(?:\\.|[^'\\\r\n])*)\\?\Z''', re.I)
_OPEN_COOKIE = re.compile(r"\b(?:Set-)?Cookie[\"']?\s*:[^\r\n]*\Z", re.I)


class StreamError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    raw: bytes
    data: str | None


async def frames(source):
    buffer = bytearray()
    raw = bytearray()
    data = []
    first_line = True
    async def including_end():
        async for chunk in source:
            yield chunk
        yield None

    async for chunk in including_end():
        final = chunk is None
        if not final:
            buffer.extend(chunk)
        start = 0
        for match in _NEWLINE.finditer(buffer):
            if not final and match.end() == len(buffer) and match.group() == b'\r':
                break  # CRLF may cross network chunk boundaries.
            line = bytes(buffer[start:match.start()])
            raw.extend(buffer[start:match.end()])
            start = match.end()
            if len(raw) > MAX_FRAME_BYTES:
                raise StreamError('SSE frame exceeds limit')
            if first_line:
                line = line.removeprefix(b'\xef\xbb\xbf')
                first_line = False
            if not line:
                try:
                    payload = b'\n'.join(data).decode('utf-8') if data else None
                except UnicodeDecodeError:
                    raise StreamError('Invalid SSE UTF-8') from None
                yield Frame(bytes(raw), payload)
                raw.clear()
                data.clear()
            elif line == b'data' or line.startswith(b'data:'):
                data.append(line[5:].removeprefix(b' '))
        if start:
            del buffer[:start]
        if len(raw) + len(buffer) > MAX_FRAME_BYTES:
            raise StreamError('SSE frame exceeds limit')
    if raw or buffer:
        raise StreamError('Incomplete SSE frame')


@dataclass(frozen=True)
class Projection:
    metadata: dict
    text: dict
    terminal: str | None = None
    fallback: bool = False


def stream_projection(operation, data):
    if data == '[DONE]' and operation == 'chat-completions':
        return Projection({'type': 'done'}, {}, 'complete')
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        raise StreamError('Invalid SSE JSON') from None
    if not isinstance(value, dict):
        raise StreamError('Invalid SSE value')
    if 'error' in value or value.get('type') == 'error':
        return Projection({'type': 'error', 'error': sanitize(fields(value.get('error', value), ('code', 'type')))}, {}, 'upstream-error')
    if operation == 'chat-completions':
        return chat_projection(value)
    if operation == 'claude-messages':
        return claude_projection(value)
    return responses_projection(value)


def index(value):
    return value if type(value) is int and 0 <= value < 100000 else 0


def chat_projection(value):
    info = sanitize(fields(value, ('id', 'model', 'object', 'usage')))
    info['type'] = 'chat.completion.chunk'
    text = {}
    choices = value.get('choices', [])
    if not isinstance(choices, list):
        raise StreamError('Invalid SSE choices')
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(choice.get('delta'), dict):
            continue
        prefix = f'choice.{index(choice.get("index"))}'
        delta = choice['delta']
        for name in ('content', 'refusal'):
            if isinstance(delta.get(name), str):
                text[prefix + '.' + name] = delta[name]
        if choice.get('finish_reason') is not None:
            info.setdefault('finishReasons', {})[prefix] = sanitize(choice['finish_reason'])
        if 'tool_calls' in delta:
            info['toolArgumentsOmitted'] = True
    return Projection(info, text)


def responses_projection(value):
    kind = value.get('type')
    if not isinstance(kind, str) or not re.fullmatch(r'response\.[a-z_.]{1,64}', kind):
        return Projection({'type': 'unrecognized'}, {})
    info = {'type': kind}
    if kind in ('response.output_text.delta', 'response.refusal.delta',
                'response.output_text.done', 'response.refusal.done'):
        part = kind.split('.')[1]
        fallback = kind.endswith('.done')
        text = value.get(('text' if part == 'output_text' else 'refusal') if fallback else 'delta')
        prefix = f'output.{index(value.get("output_index"))}.content.{index(value.get("content_index"))}'
        return Projection(info, {prefix + '.' + part: text} if isinstance(text, str) else {}, fallback=fallback)
    if kind in ('response.completed', 'response.incomplete', 'response.failed'):
        info.update(sanitize(fields(value.get('response'), ('id', 'model', 'status', 'usage', 'incomplete_details'))))
        return Projection(info, {}, {'response.completed': 'complete', 'response.incomplete': 'upstream-incomplete',
                                     'response.failed': 'upstream-error'}[kind])
    if kind.startswith('response.function_call_arguments.'):
        info['toolArgumentsOmitted'] = True
    return Projection(info, {})


def claude_projection(value):
    kind = value.get('type')
    if kind == 'message_start':
        return Projection({'type': kind, 'message': sanitize(fields(value.get('message'), MESSAGE_FIELDS))}, {})
    if kind == 'message_delta':
        info = fields(value.get('delta'), ('stop_reason', 'stop_sequence')) | fields(value, ('usage',))
        return Projection({'type': kind} | sanitize(info), {})
    if kind == 'message_stop':
        return Projection({'type': kind}, {}, 'complete')
    if kind == 'ping':
        return Projection({'type': kind}, {})
    if kind not in ('content_block_start', 'content_block_delta', 'content_block_stop'):
        return Projection({'type': 'unrecognized'}, {})

    block_index = value.get('index')
    if type(block_index) is not int or not 0 <= block_index < 100000:
        return Projection({'type': kind, 'bodyOmitted': 'invalid-block-index'}, {})
    info = {'type': kind, 'index': block_index}
    if kind == 'content_block_stop':
        return Projection(info, {})
    if kind == 'content_block_start':
        block = value.get('content_block')
        if not isinstance(block, dict):
            return Projection(info | {'bodyOmitted': 'unsupported-content'}, {})
        block_type = block.get('type')
        if not isinstance(block_type, str):
            return Projection(info | {'bodyOmitted': 'unsupported-content'}, {})
        if block_type in TOOL_TYPES:
            info.update(sanitize(fields(block, ('id', 'name', 'server_name'))))
            return Projection(info | {'contentType': block_type, 'toolArgumentsOmitted': True}, {})
        if block_type in TOOL_RESULT_TYPES:
            return Projection(info | {'toolResult': project_claude_tool_result(block)}, {})
        if block_type in OPAQUE_CONTENT_TYPES:
            return Projection(info | {'contentType': block_type, 'bodyOmitted': 'opaque-content'}, {})
        if block_type in ('text', 'thinking'):
            content = block.get(block_type)
            return Projection(info | {'contentType': block_type},
                              {f'content.{block_index}.{block_type}': content} if isinstance(content, str) else {})
        return Projection(info | {'bodyOmitted': 'unsupported-content'}, {})

    delta = value.get('delta')
    if not isinstance(delta, dict):
        return Projection(info | {'bodyOmitted': 'unsupported-content'}, {})
    delta_type = delta.get('type')
    if delta_type == 'input_json_delta':
        # Redaction cannot safely interpret partial strings from a JSON argument stream.
        return Projection(info | {'toolArgumentsOmitted': True}, {})
    if delta_type == 'signature_delta':
        return Projection(info | {'signatureOmitted': True}, {})
    if delta_type == 'citations_delta':
        return Projection(info | {'citation': sanitize(select_citation(delta.get('citation')))}, {})
    if delta_type in ('text_delta', 'thinking_delta'):
        field = delta_type.removesuffix('_delta')
        content = delta.get(field)
        return Projection(info, {f'content.{block_index}.{field}': content} if isinstance(content, str) else {})
    return Projection(info | {'bodyOmitted': 'unsupported-content'}, {})


class StreamWindow:
    def __init__(self):
        self.raw = []
        self.size = 0
        self.frames = []
        self.text = {}
        self.history = {}

    def add(self, frame, projection):
        self.raw.append(frame.raw)
        self.size += len(frame.raw)
        if self.size > MAX_WINDOW_BYTES or len(self.raw) > MAX_WINDOW_FRAMES:
            raise StreamError('SSE audit segment exceeds limit')
        if projection:
            self.frames.append(projection.metadata)
            for channel, item in projection.text.items():
                if projection.fallback and (channel in self.history or channel in self.text):
                    continue
                if channel not in self.history and channel not in self.text and len(self.history) + len(self.text) >= 64:
                    raise StreamError('Too many SSE output channels')
                self.text[channel] = self.text.get(channel, '') + item

    def ready(self):
        for channel, value in self.text.items():
            if value and '!' <= value[-1] <= '~':
                return False  # A token or signed URI can span multiple SSE frames.
            combined = self.history.get(channel, '') + value
            opened = list(_PEM_START.finditer(combined))
            closed = list(_PEM_END.finditer(combined))
            if (_OPEN_QUOTE.search(combined) or _OPEN_COOKIE.search(combined)
                    or (opened and (not closed or opened[-1].start() > closed[-1].start()))):
                return False
        return True

    def commit(self):
        text = {}
        for channel, value in self.text.items():
            previous = self.history.get(channel, '')
            text[channel] = scrub_text(value, previous)
            self.history[channel] = (previous + value)[-512:]
        payload = {'events': self.frames, 'text': text} if self.frames else None
        raw = b''.join(self.raw)
        self.frames = []
        self.raw = []
        self.size = 0
        self.text = {}
        return raw, payload
