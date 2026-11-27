import json
import unittest

from audit_relay.claude import project_claude_request, project_claude_response
from audit_relay.projection import MASK
from audit_relay.sse import Frame, StreamWindow, stream_projection


class ClaudeAuditTests(unittest.TestCase):
    def test_json_keeps_business_messages_and_tool_results(self):
        request = {
            'model': 'example-model',
            'system': [{'type': 'text', 'text': 'Summarize for contact@example.com.'}],
            'messages': [
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Please summarize this document.'},
                    {'type': 'document', 'title': 'Example report',
                     'source': {'type': 'base64', 'data': 'synthetic-document-contents'}},
                ]},
                {'role': 'assistant', 'content': [
                    {'type': 'tool_use', 'name': 'lookup', 'id': 'tool_example',
                     'input': {'customer': 'Example customer', 'apiKey': 'sample'}},
                ]},
                {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'tool_example', 'content': [
                        {'type': 'text', 'text': 'Found a business result.'},
                    ]},
                ]},
            ],
            'tools': [{'name': 'lookup', 'input_schema': {'type': 'object'},
                       'headers': {'X-Custom-Header': 'synthetic-secret'}}],
            'mcp_servers': [{'authorization_token': 'synthetic-mcp-token'}],
        }
        result = project_claude_request(request)
        self.assertIn('contact@example.com', result['system'][0]['text'])
        document = result['messages'][0]['content'][1]
        self.assertEqual(document, {'type': 'document', 'title': 'Example report', 'bodyOmitted': 'opaque-content'})
        self.assertEqual(result['messages'][1]['content'][0]['input'], {'customer': 'Example customer', 'apiKey': MASK})
        self.assertEqual(result['messages'][2]['content'][0]['content'][0]['text'], 'Found a business result.')
        self.assertEqual(result['tools'][0], {'name': 'lookup', 'input_schema': {'type': 'object'}})
        self.assertNotIn('synthetic-', json.dumps(result))

    def test_json_response_marks_opaque_content_and_retains_token_count(self):
        response = {'content': [
            {'type': 'text', 'text': 'Approved business response.'},
            {'type': 'thinking', 'thinking': 'Consider the request.', 'signature': 'synthetic-signature'},
            {'type': 'redacted_thinking', 'data': 'synthetic-opaque-data'},
            {'type': ['unsupported'], 'data': 'synthetic-unrecognized-data'},
        ]}
        result = project_claude_response(response, 200)
        self.assertEqual(result['content'], [
            {'type': 'text', 'text': 'Approved business response.'},
            {'type': 'thinking', 'thinking': 'Consider the request.', 'signatureOmitted': True},
            {'type': 'redacted_thinking', 'bodyOmitted': 'opaque-content'},
            {'type': 'unrecognized', 'bodyOmitted': 'unsupported-content'},
        ])
        self.assertEqual(project_claude_response({'input_tokens': 17}, 200), {'input_tokens': 17})
        self.assertEqual(project_claude_response({'error': {'type': 'invalid_request_error',
                                                           'message': 'synthetic-secret'}}, 400),
                         {'httpStatus': 400, 'error': {'type': 'invalid_request_error'}})

    def test_sse_retains_wire_frames_and_redacts_split_credentials(self):
        events = [
            {'type': 'message_start', 'message': {'id': 'msg_example'}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'Result: Bear'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'er synthetic-'}},
            {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'name': 'lookup'}},
            {'type': 'content_block_delta', 'index': 1,
             'delta': {'type': 'input_json_delta', 'partial_json': '{"secret":"synthetic-tool-fragment'}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'credential approved. '}},
            {'type': 'content_block_delta', 'index': 2,
             'delta': {'type': 'thinking_delta', 'thinking': 'Considered the request. '}},
            {'type': 'content_block_delta', 'index': 2,
             'delta': {'type': 'signature_delta', 'signature': 'synthetic-signature'}},
            {'type': 'message_stop'},
        ]
        window = StreamWindow()
        raw_frames = []
        forwarded = []
        evidence = []
        for event in events:
            data = json.dumps(event)
            projection = stream_projection('claude-messages', data)
            raw = f'event: {event["type"]}\ndata: {data}\n\n'.encode()
            raw_frames.append(raw)
            window.add(Frame(raw, data), projection)
            if projection.terminal or window.ready():
                wire, payload = window.commit()
                forwarded.append(wire)
                evidence.append(payload)
        self.assertEqual(b''.join(forwarded), b''.join(raw_frames))
        self.assertEqual(projection.terminal, 'complete')
        serialized = json.dumps(evidence)
        self.assertNotIn('synthetic-', serialized)
        self.assertIn('toolArgumentsOmitted', serialized)
        self.assertIn('signatureOmitted', serialized)
        self.assertIn('Result: [SECRET] approved.', serialized)
        self.assertIn('content.2.thinking', serialized)

    def test_sse_errors_unknown_events_and_bad_block_indices(self):
        error = stream_projection('claude-messages', '{"type":"error","error":{"type":"overloaded_error","message":"synthetic-secret"}}')
        self.assertEqual(error.terminal, 'upstream-error')
        self.assertEqual(error.metadata, {'type': 'error', 'error': {'type': 'overloaded_error'}})
        unknown = stream_projection('claude-messages', '{"type":"future_event","token":"synthetic-secret"}')
        self.assertEqual(unknown.metadata, {'type': 'unrecognized'})
        invalid = stream_projection('claude-messages', '{"type":"content_block_delta","index":true,"delta":{"type":"text_delta","text":"unscoped"}}')
        self.assertEqual(invalid.metadata, {'type': 'content_block_delta', 'bodyOmitted': 'invalid-block-index'})
        self.assertEqual(invalid.text, {})

    def test_web_search_evidence_excludes_opaque_continuation_data(self):
        citation = {'type': 'web_search_result_location', 'url': 'https://example.com/result',
                    'cited_text': 'Example quote.', 'encrypted_index': 'synthetic-index'}
        result = {'type': 'web_search_tool_result', 'tool_use_id': 'tool_example', 'content': [
            {'type': 'web_search_result', 'url': citation['url'], 'title': 'Example source',
             'encrypted_content': 'synthetic-search-result'},
        ]}
        response = project_claude_response({'content': [result, {'type': 'text', 'text': 'From the source.',
                                                                'citations': [citation]}]}, 200)
        self.assertEqual(response['content'][0]['content'][0]['title'], 'Example source')
        self.assertEqual(response['content'][1]['citations'][0]['cited_text'], 'Example quote.')

        events = [
            {'type': 'content_block_start', 'index': 0, 'content_block': result},
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'citations_delta', 'citation': citation}},
        ]
        projected = [stream_projection('claude-messages', json.dumps(event)).metadata for event in events]
        self.assertEqual(projected[0]['toolResult'], response['content'][0])
        self.assertEqual(projected[1]['citation'], response['content'][1]['citations'][0])
        self.assertNotIn('synthetic-', json.dumps([response, projected]))
