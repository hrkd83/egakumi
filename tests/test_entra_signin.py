"""本人認証のcallbackとtoken保存の境界を検証する。

loopbackサーバー、一時ファイル、偽の認証clientを使い、state照合・対象client照合・ファイル権限・エラー出力を調べる。実際の本人サインインは起動しない。
"""
import contextlib
import base64
import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import entra_signin as signin


TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
SCOPE = "https://graph.microsoft.com/User.Read"
# WSLやCIでのスケジューリング遅延を含め、ローカルcallbackの検査を待ちます。
CALLBACK_TEST_TIMEOUT_SECONDS = 10


def callback_server():
    """本人認証の戻り先だけを受ける一時loopbackサーバーを起動し、最後に閉じる。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return signin.CallbackServer(f"http://localhost:{port}/callback", "expected-state")


def request(server, target, *, host=None, method="GET"):
    """ローカルcallbackへ合成HTTP要求を送り、認証サービスへ接続せず結果を読む。"""
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=CALLBACK_TEST_TIMEOUT_SECONDS)
    try:
        connection.request(method, target, headers={"Host": host or f"localhost:{server.server_port}"})
        response = connection.getresponse()
        return response.status, response.read().decode(), dict(response.getheaders())
    finally:
        connection.close()


class CallbackTests(unittest.TestCase):
    """本人認証のcallbackとtoken保存の境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def run_requests(self, server, targets):
        result = []
        errors = []
        def wait():
            try:
                result.append(signin.wait_for_callback(server, CALLBACK_TEST_TIMEOUT_SECONDS))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=wait)
        thread.start()
        try:
            responses = [request(server, **target) for target in targets]
        finally:
            thread.join(timeout=CALLBACK_TEST_TIMEOUT_SECONDS + 2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        return responses, result[0]

    def test_real_local_callback_accepts_state_without_echoing_code(self):
        output = io.StringIO()
        with callback_server() as server, contextlib.redirect_stderr(output):
            responses, result = self.run_requests(server, [{
                "target": "/callback?code=private-code&state=expected-state",
            }])
            self.assertEqual(server.server_address[0], "127.0.0.1")
        status, body, headers = responses[0]
        self.assertEqual(status, 200)
        self.assertNotIn("private-code", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(result, {"code": "private-code", "state": "expected-state"})
        self.assertEqual(output.getvalue(), "")

    def test_wrong_path_host_method_state_and_duplicate_fields_do_not_end_flow(self):
        with callback_server() as server:
            responses, result = self.run_requests(server, [
                {"target": "/wrong?code=bad&state=expected-state"},
                {"target": "/callback?code=bad&state=expected-state", "host": "external.example"},
                {"target": "/callback", "method": "POST"},
                {"target": "/callback?code=bad&state=wrong-state"},
                {"target": "/callback?code=bad&state=expected-state&state=wrong-state"},
                {"target": "/callback?code=valid-code&state=expected-state"},
            ])
        self.assertEqual([response[0] for response in responses], [404, 404, 405, 400, 400, 200])
        self.assertEqual(result["code"], "valid-code")

    def test_consent_denial_is_returned_only_to_the_local_flow(self):
        with callback_server() as server:
            responses, result = self.run_requests(server, [{
                "target": "/callback?error=access_denied&error_description=private-detail&state=expected-state",
            }])
        self.assertEqual(result["error"], "access_denied")
        self.assertNotIn("private-detail", responses[0][1])

    def test_timeout_is_only_a_signin_timeout(self):
        with callback_server() as server:
            with self.assertRaisesRegex(signin.SigninError, "^AUTH_SIGNIN_TIMEOUT$"):
                signin.wait_for_callback(server, 0.01)
        self.assertIsNone(server.authorization_response)

    def test_redirect_must_be_explicit_http_loopback(self):
        for value in ["https://localhost:8400", "http://external.example:8400", "http://localhost",
                      "http://user@localhost:8400", "http://localhost:8400?a=b",
                      "http://localhost:8400/#fragment", "http://localhost:8400/%2f"]:
            with self.subTest(value=value), self.assertRaises(signin.SigninError):
                signin.validated_redirect(value)


class ProtectedFileTests(unittest.TestCase):
    """本人認証のcallbackとtoken保存の境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_token_cache_and_url_are_atomic_owner_only_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [("token", "private-token"), ("cache", '{"RefreshToken":"private"}'),
                                ("url", "https://login.microsoftonline.com/authorize?state=private")]:
                path = root / name
                path.write_text("old")
                path.chmod(0o644)
                signin.protected_text(path, value)
                self.assertEqual(path.read_text(), value)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["cache", "token", "url"])

    def test_secret_read_rejects_shared_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "secret"
            target.write_text("private-secret")
            target.chmod(0o644)
            with self.assertRaisesRegex(signin.SigninError, "AUTH_UNSAFE_FILE"):
                signin.read_protected_text(target)
            target.chmod(0o600)
            self.assertEqual(signin.read_protected_text(target), "private-secret")
            link = target.with_name("link")
            link.symlink_to(target)
            with self.assertRaises(signin.SigninError):
                signin.read_protected_text(link)
            with self.assertRaises(signin.SigninError):
                signin.protected_text(link, "replace")
            self.assertEqual(target.read_text(), "private-secret")

    def test_failed_acquisition_does_not_save_token_json_or_refresh_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            cache = MagicMock()
            with self.assertRaisesRegex(signin.SigninError, "AUTH_TOKEN_NOT_ACQUIRED"):
                signin.save_result({"error_description": "private-detail", "refresh_token": "private-refresh"},
                                   path, cache, None)
            self.assertFalse(path.exists())
            cache.serialize.assert_not_called()


class SigninCommandTests(unittest.TestCase):
    """本人認証のcallbackとtoken保存の境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def arguments(self, root, *, mode="user-token"):
        arguments = [mode, "--tenant-id", TENANT, "--client-id", CLIENT,
                     "--scope", SCOPE if mode == "user-token" else "https://gateway.example.com/.default",
                     "--token-file", str(root / "token"), "--cache-file", str(root / "cache")]
        if mode == "app-token":
            arguments += ["--client-secret-file", str(root / "secret")]
        return arguments

    def sdk(self):
        sdk = MagicMock()
        cache = sdk.SerializableTokenCache.return_value
        cache.has_state_changed = True
        cache.serialize.return_value = '{"RefreshToken":"private-refresh"}'
        user = sdk.PublicClientApplication.return_value
        user.initiate_auth_code_flow.return_value = {
            "auth_uri": "https://login.microsoftonline.com/authorize?state=expected-state",
            "state": "expected-state", "code_verifier": "private-pkce-verifier",
        }
        user.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "private-user-token", "refresh_token": "private-refresh", "id_token": "private-id",
        }
        sdk.ConfidentialClientApplication.return_value.acquire_token_for_client.return_value = {
            "access_token": "private-app-token",
        }
        return sdk

    def test_user_uses_msal_pkce_flow_and_protected_url_without_browser_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = self.sdk()
            output, errors = io.StringIO(), io.StringIO()
            def callback(server, timeout):
                self.assertEqual((root / "url").read_text(),
                                 "https://login.microsoftonline.com/authorize?state=expected-state")
                self.assertEqual(stat.S_IMODE((root / "url").stat().st_mode), 0o600)
                return {"state": "expected-state", "code": "private-code"}
            with patch.object(signin, "LOGIN_URL_FILE", root / "url"), \
                    patch.object(signin, "CallbackServer"), \
                    patch.object(signin, "wait_for_callback", side_effect=callback), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = signin.main(self.arguments(root), msal_module=sdk)
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "AUTH_URL_READY\nAUTH_COMPLETE\n")
            self.assertEqual(errors.getvalue(), "")
            self.assertFalse((root / "url").exists())
            self.assertEqual((root / "token").read_text(), "private-user-token")
            self.assertEqual(stat.S_IMODE((root / "cache").stat().st_mode), 0o600)
            sdk.PublicClientApplication.return_value.initiate_auth_code_flow.assert_called_once_with(
                scopes=[SCOPE], redirect_uri="http://localhost:8400", response_mode="query")
            sdk.PublicClientApplication.return_value.acquire_token_by_auth_code_flow.assert_called_once_with(
                sdk.PublicClientApplication.return_value.initiate_auth_code_flow.return_value,
                {"state": "expected-state", "code": "private-code"})
            self.assertEqual(sdk.PublicClientApplication.call_args.kwargs["enable_pii_log"], False)

    def test_rejected_consent_and_timeout_remove_url_and_stale_token(self):
        for result, code in [({"error": "access_denied", "error_description": "private-error"},
                              "AUTH_CONSENT_OR_SIGNIN_REJECTED"),
                             (signin.SigninError("AUTH_SIGNIN_TIMEOUT"), "AUTH_SIGNIN_TIMEOUT")]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "token").write_text("stale-token")
                sdk = self.sdk()
                output, errors = io.StringIO(), io.StringIO()
                callback = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(signin, "LOGIN_URL_FILE", root / "url"), \
                        patch.object(signin, "CallbackServer"), \
                        patch.object(signin, "wait_for_callback", **callback), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = signin.main(self.arguments(root), msal_module=sdk)
                self.assertEqual(status, 2)
                self.assertEqual(errors.getvalue(), code + "\n")
                self.assertFalse((root / "url").exists())
                self.assertFalse((root / "token").exists())
                sdk.PublicClientApplication.return_value.acquire_token_by_auth_code_flow.assert_not_called()

    def test_app_secret_is_read_from_file_and_error_body_is_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signin.protected_text(root / "secret", "private-secret")
            sdk = self.sdk()
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = signin.main(self.arguments(root, mode="app-token"), msal_module=sdk)
            self.assertEqual(status, 0)
            self.assertEqual((root / "token").read_text(), "private-app-token")
            self.assertEqual(output.getvalue(), "AUTH_COMPLETE\n")
            self.assertEqual(sdk.ConfidentialClientApplication.call_args.kwargs["client_credential"], "private-secret")
            sdk.ConfidentialClientApplication.return_value.acquire_token_for_client.assert_called_once_with(
                scopes=["https://gateway.example.com/.default"])
            sdk.ConfidentialClientApplication.return_value.acquire_token_for_client.side_effect = RuntimeError(
                "HTTP 400 private-secret private-token private-response")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = signin.main(self.arguments(root, mode="app-token"), msal_module=sdk)
            self.assertEqual(status, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "AUTH_FAILED\n")
            self.assertFalse((root / "token").exists())

    def test_arguments_do_not_accept_inline_secret_or_overwrite_input_or_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.arguments(root)
            for arguments in [base + ["--client-secret", "private-secret"],
                              base + ["--token-file", str(root / "cache")],
                              self.arguments(root, mode="app-token") + ["--scope", SCOPE]]:
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = signin.main(arguments, msal_module=self.sdk())
                self.assertEqual(status, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "AUTH_INVALID_ARGUMENTS\n")


class InstalledMsalTests(unittest.TestCase):
    """本人認証のcallbackとtoken保存の境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_real_msal_creates_pkce_and_rejects_state_without_token_request(self):
        try:
            import msal
        except ImportError:
            self.skipTest("Run in the local auth-requirements.txt venv for the SDK check")
        metadata = {
            "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
            "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
        }
        http = MagicMock()
        http.get.return_value.status_code = 200
        http.get.return_value.text = json.dumps(metadata)
        app = msal.PublicClientApplication(
            CLIENT, authority=f"https://login.microsoftonline.com/{TENANT}",
            http_client=http, instance_discovery=False, enable_pii_log=False, timeout=30)
        flow = app.initiate_auth_code_flow(scopes=[SCOPE], redirect_uri="http://localhost:8400",
                                           response_mode="query")
        query = parse_qs(urlsplit(flow["auth_uri"]).query)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [flow["state"]])
        self.assertIn("nonce", query)
        with self.assertRaises(ValueError):
            app.acquire_token_by_auth_code_flow(flow, {"state": "wrong-state", "code": "private-code"})
        http.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
