"""Verify platform TLS selection and failure before credentialed HTTP calls."""
from contextlib import asynccontextmanager
from pathlib import Path
import ssl
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers import client_tls
import integration_client as probe


def secure_context():
    return types.SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)


class VerifiedTLSFactoryTests(unittest.TestCase):
    def test_windows_uses_native_server_and_hostname_validation_without_fallback(self):
        context = secure_context()
        native = types.SimpleNamespace(SSLContext=mock.Mock(return_value=context))
        with mock.patch.object(client_tls.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"truststore": native}), \
                mock.patch.object(client_tls.ssl, "create_default_context") as fallback:
            self.assertIs(client_tls.verified_tls_context(), context)
        native.SSLContext.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        fallback.assert_not_called()

    def test_other_platforms_keep_python_defaults_without_loading_native_backend(self):
        context = secure_context()
        native = types.SimpleNamespace(SSLContext=mock.Mock())
        with mock.patch.object(client_tls.sys, "platform", "linux"), \
                mock.patch.dict(sys.modules, {"truststore": native}), \
                mock.patch.object(client_tls.ssl, "create_default_context", return_value=context) as default:
            self.assertIs(client_tls.verified_tls_context(), context)
        default.assert_called_once_with()
        native.SSLContext.assert_not_called()

    def test_missing_native_dependency_stops_without_another_trust_policy(self):
        with mock.patch.object(client_tls.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"truststore": None}), \
                mock.patch.object(client_tls.ssl, "create_default_context") as fallback:
            with self.assertRaisesRegex(RuntimeError, "^" + client_tls.ERROR + "$"):
                client_tls.verified_tls_context()
        fallback.assert_not_called()

    def test_insecure_or_malformed_context_is_refused_on_both_platforms(self):
        for context in (types.SimpleNamespace(check_hostname=False, verify_mode=ssl.CERT_REQUIRED),
                        types.SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_NONE),
                        types.SimpleNamespace(check_hostname=1, verify_mode=ssl.CERT_REQUIRED), object()):
            for platform in ("win32", "linux"):
                with self.subTest(platform=platform, context=context), \
                        mock.patch.object(client_tls.sys, "platform", platform), \
                        mock.patch.dict(sys.modules, {"truststore": types.SimpleNamespace(
                            SSLContext=mock.Mock(return_value=context))}), \
                        mock.patch.object(client_tls.ssl, "create_default_context", return_value=context):
                    with self.assertRaisesRegex(RuntimeError, "^" + client_tls.ERROR + "$"):
                        client_tls.verified_tls_context()

    def test_backend_error_details_are_not_exposed_and_do_not_trigger_fallback(self):
        native = types.SimpleNamespace(SSLContext=mock.Mock(side_effect=ValueError("SYNTHETIC-PRIVATE-ERROR")))
        with mock.patch.object(client_tls.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"truststore": native}), \
                mock.patch.object(client_tls.ssl, "create_default_context") as fallback:
            with self.assertRaises(RuntimeError) as error:
                client_tls.verified_tls_context()
        self.assertEqual(str(error.exception), client_tls.ERROR)
        fallback.assert_not_called()

    def test_factory_preserves_verification_flags_and_global_ssl_class(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        original_flags, original_class = context.verify_flags, ssl.SSLContext
        native = types.SimpleNamespace(SSLContext=mock.Mock(return_value=context))
        with mock.patch.object(client_tls.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"truststore": native}):
            self.assertIs(client_tls.verified_tls_context(), context)
        self.assertEqual(context.verify_flags, original_flags)
        self.assertIs(ssl.SSLContext, original_class)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class TLSBeforeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, kind, *, transport=None, context_error=False):
        constructed = []

        class Client:
            def __init__(self, **kwargs):
                constructed.append(kwargs)

            async def __aenter__(self):
                raise ConnectionError("SYNTHETIC-CLIENT-DISCONNECTED")

            async def __aexit__(self, *args):
                return False

        @asynccontextmanager
        async def unused_stream(*args, **kwargs):
            yield object(), object()

        http = types.ModuleType("httpx2")
        http.AsyncClient = Client
        http.AsyncHTTPTransport = mock.Mock(return_value=object())
        session = types.ModuleType("mcp.client.session")
        session.ClientSession = object
        stream = types.ModuleType("mcp.client.streamable_http")
        stream.streamable_http_client = unused_stream
        context = secure_context()
        with mock.patch.dict(sys.modules, {"httpx2": http, "mcp.client.session": session,
                                          "mcp.client.streamable_http": stream}), \
                mock.patch.object(probe, "verified_tls_context", return_value=context,
                    side_effect=RuntimeError(client_tls.ERROR) if context_error else None) as tls:
            if kind == "rest":
                result = await probe.rest_probe("https://gateway.example.com/responses", "responses", {},
                    {"Authorization": "Bearer <SYNTHETIC_TOKEN>"}, transport=transport)
            else:
                result = await probe.mcp_probe("https://gateway.example.com/mcp", {},
                    {"Authorization": "Bearer <SYNTHETIC_TOKEN>"}, transport=transport)
        return result, constructed, http.AsyncHTTPTransport, tls, context

    async def test_both_default_http_transports_use_verified_context_and_no_retries(self):
        for kind in ("rest", "mcp"):
            result, constructed, transport, tls, context = await self.run_probe(kind)
            tls.assert_called_once_with()
            transport.assert_called_once_with(verify=context, retries=0)
            self.assertEqual(len(constructed), 1)
            self.assertIs(constructed[0]["follow_redirects"], False)
            self.assertIs(constructed[0]["trust_env"], False)
            self.assertFalse(result["succeeded"])

    async def test_explicit_custom_transport_is_preserved_even_if_falsey(self):
        class FalseyTransport:
            def __bool__(self):
                return False

        custom = FalseyTransport()
        for kind in ("rest", "mcp"):
            _, constructed, transport, tls, _ = await self.run_probe(kind, transport=custom)
            tls.assert_not_called()
            transport.assert_not_called()
            self.assertIs(constructed[0]["transport"], custom)

    async def test_unavailable_verified_context_prevents_http_client_construction(self):
        for kind in ("rest", "mcp"):
            result, constructed, transport, tls, _ = await self.run_probe(kind, context_error=True)
            tls.assert_called_once_with()
            transport.assert_not_called()
            self.assertEqual(constructed, [])
            self.assertFalse(result["succeeded"])
            self.assertEqual(result["status"], None)
            self.assertEqual(result.get("httpAttempts", result.get("toolPostCount")), 0)


if __name__ == "__main__":
    unittest.main()
