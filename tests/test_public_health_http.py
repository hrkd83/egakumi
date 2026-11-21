"""Synthetic platform TLS/redirect, stdin and JSON type boundaries."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment.helpers import public_health_http as health

URL = 'https://portal.example.com/api/health'
ACCEPT = 'application/json'
AGENT = 'egakumi-portal-deployment/1.0'
BODY = {'status': 'ok', 'service': 'egakumi-portal'}


def response(**values):
    value = {'statusCode': 200, 'body': json.dumps(BODY), 'cacheControl': 'no-store'}
    value.update(values)
    return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')


class PublicHealthHttpTests(unittest.TestCase):
    def windows(self, reply):
        stack = self.enterContext(mock.patch.object(health.os, 'name', 'nt'))
        self.enterContext(mock.patch.object(health, 'locate_powershell', return_value='pwsh'))
        self.enterContext(mock.patch.object(health, 'powershell_environment', return_value={}))
        run = self.enterContext(mock.patch.object(health.subprocess, 'run', return_value=reply))
        return run

    def read(self, **kwargs):
        return health.read_public_json(URL, accept=ACCEPT, user_agent=AGENT, **kwargs)

    def test_windows_metadata_and_request_values_use_stdin(self):
        run = self.windows(response())
        self.assertEqual(self.read(), (200, BODY, 'no-store'))
        arguments = run.call_args.args[0]
        self.assertNotIn(URL, arguments)
        self.assertEqual(arguments[1:4], ['-NoProfile', '-NonInteractive', '-EncodedCommand'])
        request = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(request, {'url': URL, 'accept': ACCEPT, 'userAgent': AGENT,
                                 'timeoutSeconds': 20, 'maximumBytes': health.MAX_RESPONSE_BYTES})
        program = base64.b64decode(arguments[-1]).decode('utf-16-le')
        self.assertIn('$handler.AllowAutoRedirect = $false', program)
        self.assertIn('$client.SendAsync($request)', program)
        self.assertNotIn('ServerCertificateCustomValidationCallback', program)
        self.assertNotIn('SkipCertificateCheck', program)
        self.assertNotIn('ServicePointManager', program)
        self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_windows_string_status_cannot_be_coerced(self):
        self.windows(response(statusCode='200'))
        with self.assertRaisesRegex(ValueError, '^' + health.ERROR + '$'): self.read()

    def test_windows_boolean_status_cannot_be_coerced(self):
        self.windows(response(statusCode=True))
        with self.assertRaises(ValueError): self.read()

    def test_windows_raw_body_must_be_text_before_python_json_parse(self):
        self.windows(response(body=BODY))
        with self.assertRaises(ValueError): self.read()

    def test_windows_cache_control_cannot_be_boolean(self):
        self.windows(response(cacheControl=True))
        with self.assertRaises(ValueError): self.read()

    def test_windows_unknown_envelope_fields_are_rejected(self):
        self.windows(response(extra='synthetic'))
        with self.assertRaises(ValueError): self.read()

    def test_windows_invalid_json_body_never_leaks(self):
        self.windows(response(body='synthetic-invalid-json-content'))
        with self.assertRaises(ValueError) as error: self.read()
        self.assertEqual(str(error.exception), health.ERROR)
        self.assertNotIn('synthetic-invalid', str(error.exception))

    def test_windows_process_error_does_not_leak_stdout_stderr_or_url(self):
        self.windows(SimpleNamespace(returncode=1, stdout='synthetic-response-content', stderr=URL))
        with self.assertRaises(ValueError) as error: self.read()
        self.assertEqual(str(error.exception), health.ERROR)

    def test_windows_timeout_is_fixed_error_without_url(self):
        run = self.windows(response())
        run.side_effect = subprocess.TimeoutExpired(['synthetic', URL], 30, output='synthetic')
        with self.assertRaises(ValueError) as error: self.read()
        self.assertEqual(str(error.exception), health.ERROR)

    def test_windows_failure_never_falls_back_to_other_tls(self):
        self.windows(SimpleNamespace(returncode=1, stdout='', stderr=''))
        with mock.patch.object(health, '_urllib', side_effect=AssertionError('Fallback forbidden')):
            with self.assertRaises(ValueError): self.read()

    def test_windows_body_size_bound(self):
        self.windows(response(body='x' * (health.MAX_RESPONSE_BYTES + 1)))
        with self.assertRaises(ValueError): self.read()

    def test_linux_keeps_verified_urllib_headers_and_redirect_handler(self):
        reply = io.BytesIO(json.dumps(BODY).encode())
        reply.status = 200
        reply.headers = {'Cache-Control': 'no-store'}
        opener = mock.Mock()
        opener.open.return_value = reply
        with mock.patch.object(health.os, 'name', 'posix'), \
             mock.patch.object(health, 'build_opener', return_value=opener) as build, \
             mock.patch.object(health, '_windows', side_effect=AssertionError('Windows path forbidden')):
            self.assertEqual(self.read(), (200, BODY, 'no-store'))
        self.assertIsInstance(build.call_args.args[0], health.NoRedirect)
        self.assertIsNone(build.call_args.args[0].redirect_request(None, None, None, None, None, None))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_header('Accept'), ACCEPT)
        self.assertEqual(request.get_header('User-agent'), AGENT)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)

    def test_linux_http_error_never_leaks(self):
        opener = mock.Mock()
        opener.open.side_effect = HTTPError(URL, 403, 'synthetic-private-content', {}, None)
        with mock.patch.object(health.os, 'name', 'posix'), mock.patch.object(health, 'build_opener', return_value=opener):
            with self.assertRaises(ValueError) as error: self.read()
        self.assertEqual(str(error.exception), health.ERROR)

    def test_request_credentials_fragment_cleartext_and_other_port_rejected_before_transport(self):
        credential_url = urlunsplit(('https', 'synthetic-user:synthetic-password@portal.example.com',
                                    '/api/health', '', ''))
        for url in ('http://portal.example.com/api/health', credential_url,
                    URL + '#synthetic', URL + '\r\nsynthetic', 'https://portal.example.com:8443/api/health'):
            with self.subTest(url=url), mock.patch.object(health, '_windows', side_effect=AssertionError('No request')), \
                 mock.patch.object(health, '_urllib', side_effect=AssertionError('No request')):
                with self.assertRaises(ValueError) as error:
                    health.read_public_json(url, accept=ACCEPT, user_agent=AGENT)
                self.assertEqual(str(error.exception), health.ERROR)

    def test_header_control_characters_rejected_before_transport(self):
        with mock.patch.object(health, '_windows', side_effect=AssertionError('No request')), \
             mock.patch.object(health, '_urllib', side_effect=AssertionError('No request')):
            with self.assertRaises(ValueError): health.read_public_json(URL, accept='application/json\r\nX-Synthetic: 1', user_agent=AGENT)

    def test_timeout_type_and_bounds_rejected_before_transport(self):
        for timeout in (True, '20', 0, 21):
            with self.subTest(timeout=timeout), mock.patch.object(health, '_windows', side_effect=AssertionError('No request')), \
                 mock.patch.object(health, '_urllib', side_effect=AssertionError('No request')):
                with self.assertRaises(ValueError): self.read(timeout=timeout)



@unittest.skipUnless(shutil.which('pwsh') or os.environ.get('WORKFORCE_PWSH'),
                     'PowerShell runtime is not installed')
class DotNetLoopbackTests(unittest.TestCase):
    """Run the exact PS/.NET program against an independent loopback fixture.

    The private HTTP transport is exercised directly so the fixture requires
    no certificate-store changes. Production still rejects cleartext URLs.
    """
    def setUp(self):
        self.requests = []
        seen = self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get('Accept'), self.headers.get('User-Agent')))
                body = json.dumps(BODY).encode()
                self.send_response(302 if self.path == '/redirect' else 200)
                if self.path == '/redirect': self.send_header('Location', '/follow')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        url = 'http://127.0.0.1:' + str(self.server.server_port) + path
        with mock.patch.dict(os.environ, {'NO_PROXY': '127.0.0.1'}):
            return health._windows(url, ACCEPT, AGENT, 20)

    def test_real_dotnet_stdin_headers_and_raw_json_reply(self):
        self.assertEqual(self.get('/health'), (200, BODY, 'no-store'))
        self.assertEqual(self.requests, [('/health', ACCEPT, AGENT)])

    def test_real_dotnet_does_not_follow_redirect(self):
        self.assertEqual(self.get('/redirect')[0], 302)
        self.assertEqual(self.requests, [('/redirect', ACCEPT, AGENT)])

if __name__ == '__main__':
    unittest.main()