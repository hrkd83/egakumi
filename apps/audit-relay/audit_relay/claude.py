"""Project Claude's business payload without storing attachments or signatures."""
from .projection import fields, sanitize


MESSAGE_FIELDS = ('id', 'type', 'role', 'model', 'stop_reason', 'stop_sequence', 'usage')
TOOL_TYPES = {'tool_use', 'server_tool_use', 'mcp_tool_use'}
OPAQUE_CONTENT_TYPES = {'image', 'document', 'audio', 'video', 'container_upload', 'redacted_thinking'}
TOOL_RESULT_TYPES = {'tool_result', 'mcp_tool_result', 'web_search_tool_result'}


def select_citation(value):
    return {key: item for key, item in fields(value, (
        'type', 'cited_text', 'document_index', 'document_title', 'file_id',
        'start_char_index', 'end_char_index', 'start_page_number', 'end_page_number',
        'start_block_index', 'end_block_index', 'search_result_index', 'source', 'title', 'url',
    )).items() if type(item) in (str, int)}


def _content(value, depth=0):
    if isinstance(value, str):
        return value
    if depth >= 8:
        return {'bodyOmitted': 'nesting-limit'}
    if isinstance(value, list):
        return [_block(block, depth) for block in value if isinstance(block, dict)]
    return {'bodyOmitted': 'unsupported-content'}


def _block(block, depth):
    kind = block.get('type')
    if not isinstance(kind, str):
        return {'type': 'unrecognized', 'bodyOmitted': 'unsupported-content'}
    if kind == 'text':
        selected = fields(block, ('type', 'text'))
        if isinstance(block.get('citations'), list):
            selected['citations'] = [select_citation(citation) for citation in block['citations'] if isinstance(citation, dict)]
        return selected
    if kind == 'thinking':
        return fields(block, ('type', 'thinking')) | {'signatureOmitted': 'signature' in block}
    if kind in TOOL_TYPES:
        selected = fields(block, ('type', 'id', 'name', 'server_name'))
        if isinstance(block.get('input'), dict):
            selected['input'] = block['input']
        elif 'input' in block:
            selected['toolArgumentsOmitted'] = True
        return selected
    if kind in TOOL_RESULT_TYPES:
        selected = fields(block, ('type', 'tool_use_id', 'is_error'))
        content = block.get('content')
        if kind == 'web_search_tool_result' and isinstance(content, dict):
            selected['content'] = fields(content, ('type', 'error_code'))
        elif 'content' in block:
            selected['content'] = _content(content, depth + 1)
        return selected
    if kind == 'web_search_result':
        return fields(block, ('type', 'title', 'url', 'page_age')) | {'bodyOmitted': 'opaque-content'}
    if kind in OPAQUE_CONTENT_TYPES:
        labels = {key: block[key] for key in ('title', 'context') if isinstance(block.get(key), str)}
        return {'type': kind, 'bodyOmitted': 'opaque-content'} | labels
    return {'type': 'unrecognized', 'bodyOmitted': 'unsupported-content'}


def project_claude_request(value):
    selected = fields(value, ('model', 'max_tokens', 'stream', 'temperature', 'top_k', 'top_p',
                              'stop_sequences', 'tool_choice', 'thinking', 'output_config'))
    if 'system' in value:
        selected['system'] = _content(value['system'])
    if isinstance(value.get('messages'), list):
        selected['messages'] = [
            fields(message, ('role',)) | {'content': _content(message.get('content'))}
            for message in value['messages'] if isinstance(message, dict)
        ]
    if isinstance(value.get('tools'), list):
        # The schema is part of the user's tool request. Runtime connection headers and tokens are not.
        selected['tools'] = [
            fields(tool, ('name', 'description', 'type', 'input_schema', 'strict'))
            for tool in value['tools'] if isinstance(tool, dict)
        ]
    return sanitize(selected)


def project_claude_response(value, status):
    if status >= 400:
        return {'httpStatus': status, 'error': sanitize(fields(value.get('error'), ('type', 'code')))}
    selected = fields(value, MESSAGE_FIELDS + ('input_tokens',))
    if 'content' in value:
        selected['content'] = _content(value['content'])
    return sanitize(selected)


def project_claude_tool_result(block):
    return sanitize(_block(block, 0))
