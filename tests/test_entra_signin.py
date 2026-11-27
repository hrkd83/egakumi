"""本人認証のcallbackとtoken保存の境界を検証する。

loopbackサーバー、一時ファイル、偽の認証clientを使い、state照合・対象client照合・ファイル権限・エラー出力を調べる。実際の本人サインインは起動しない。
"""
import contextlib
import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from clients import entra_signin as signin
from deployment.helpers.powershell_runtime import locate_powershell, powershell_environment


TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
SCOPE = "https://graph.microsoft.com/User.Read"
# WSLやCIでのスケジューリング遅延を含め、ローカルcallbackの検査を待ちます。
CALLBACK_TEST_TIMEOUT_SECONDS = 10


def isolated_environment(values=None):
    """選択設定だけを除き、検査対象外のOSとPowerShell起動環境を保持する。"""
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"AZURE_ENV_NAME", "WORKFORCE_CONFIG"}}
    environment.update(values or {})
    return environment


@contextlib.contextmanager
def directory_link(link, target):
    """合成ディレクトリだけを結び、Windowsでは権限不要のjunctionを使う。"""
    if os.name == "nt":
        executable = locate_powershell()
        environment = powershell_environment(executable)
        environment.update({"EGAKUMI_TEST_LINK": str(link), "EGAKUMI_TEST_TARGET": str(target)})
        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command",
                                 "New-Item -ItemType Junction -Path $env:EGAKUMI_TEST_LINK -Value $env:EGAKUMI_TEST_TARGET | Out-Null"],
                                env=environment, capture_output=True, timeout=20, check=False)
        if result.returncode:
            raise AssertionError("Synthetic junction creation failed")
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield link
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


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
            root = Path(directory).resolve()
            signin.protect_new_path(root, directory=True)
            native_replace = os.replace
            for name, value in [("token", "private-token"), ("cache", '{"RefreshToken":"private"}'),
                                ("url", "https://login.microsoftonline.com/authorize?state=private")]:
                path = root / name
                signin.protected_text(path, "old")
                def replace_checked(source, target):
                    self.assertEqual(path.read_text(encoding="utf-8"), "old")
                    self.assertEqual(Path(source).read_text(encoding="utf-8"), value)
                    signin.check_private_permissions(Path(source))
                    native_replace(source, target)
                with patch.object(signin.os, "replace", side_effect=replace_checked):
                    signin.protected_text(path, value)
                self.assertEqual(signin.read_protected_text(path), value)
                signin.check_private_permissions(path)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["cache", "token", "url"])

    def test_secret_read_rejects_shared_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            signin.protect_new_path(root, directory=True)
            origin = root / "origin"
            origin.mkdir()
            signin.protect_new_path(origin, directory=True)
            target = origin / "secret"
            target.write_text("private-secret")
            if os.name != "nt":
                target.chmod(0o644)
            with self.assertRaisesRegex(signin.SigninError, "AUTH_UNSAFE_FILE"):
                signin.read_protected_text(target)
            with self.assertRaisesRegex(signin.SigninError, "AUTH_UNSAFE_FILE"):
                signin.protected_text(target, "replace")
            self.assertEqual(target.read_text(), "private-secret")
            with self.assertRaises(ValueError):
                signin.check_private_permissions(target)
            target.unlink()
            signin.protected_text(target, "private-secret")
            self.assertEqual(signin.read_protected_text(target), "private-secret")
            with directory_link(root / "link", origin) as link:
                with self.assertRaisesRegex(signin.SigninError, "AUTH_UNSAFE_FILE"):
                    signin.read_protected_text(link / "secret")
                with self.assertRaisesRegex(signin.SigninError, "AUTH_UNSAFE_FILE"):
                    signin.protected_text(link / "secret", "replace")
            self.assertEqual(target.read_text(), "private-secret")

    def test_storage_failures_preserve_original_and_remove_temporary_files(self):
        for operation, code in (("protect_new_path", "AUTH_UNSAFE_FILE"), ("fsync", "AUTH_FAILED"),
                                ("fdopen", "AUTH_FAILED")):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                signin.protect_new_path(root, directory=True)
                target = root / "token"
                signin.protected_text(target, "old")
                def fail_protection(temporary):
                    self.assertEqual(temporary.stat().st_size, 0)
                    raise ValueError("Synthetic protection failure")
                module = signin if operation == "protect_new_path" else signin.os
                failure = fail_protection if operation == "protect_new_path" else OSError("Synthetic storage failure")
                with patch.object(module, operation, side_effect=failure), patch.object(signin.os, "replace") as replaced:
                    with self.assertRaisesRegex(signin.SigninError, code):
                        signin.protected_text(target, "new")
                replaced.assert_not_called()
                self.assertEqual(signin.read_protected_text(target), "old")
                signin.check_private_permissions(target)
                self.assertEqual(list(root.iterdir()), [target])

    def test_missing_or_invalid_utf8_input_has_fixed_unavailable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            signin.protect_new_path(root, directory=True)
            target = root / "secret"
            with self.assertRaisesRegex(signin.SigninError, "AUTH_INPUT_UNAVAILABLE"):
                signin.read_protected_text(target)
            self.assertEqual(list(root.iterdir()), [])
            target.touch()
            signin.protect_new_path(target)
            target.write_bytes(b"\xff")
            with self.assertRaisesRegex(signin.SigninError, "AUTH_INPUT_UNAVAILABLE"):
                signin.read_protected_text(target)

    def test_invalid_utf8_output_preserves_original_and_has_fixed_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            signin.protect_new_path(root, directory=True)
            target = root / "token"
            signin.protected_text(target, "old")
            with patch.object(signin.os, "replace") as replaced:
                with self.assertRaisesRegex(signin.SigninError, "AUTH_FAILED"):
                    signin.protected_text(target, chr(0xD800))
            replaced.assert_not_called()
            self.assertEqual(signin.read_protected_text(target), "old")
            self.assertEqual(list(root.iterdir()), [target])

    def test_failed_acquisition_does_not_save_token_json_or_refresh_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "token"
            cache = MagicMock()
            with self.assertRaisesRegex(signin.SigninError, "AUTH_TOKEN_NOT_ACQUIRED"):
                signin.save_result({"error_description": "private-detail", "refresh_token": "private-refresh"},
                                   path, cache, None)
            self.assertFalse(path.exists())
            cache.serialize.assert_not_called()


class SigninCommandTests(unittest.TestCase):
    """本人認証のcallbackとtoken保存の境界を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def runtime_root(self, directory):
        """合成の選択環境を用意し、公開checkoutへ認証出力を作らない。"""
        repository = Path(directory).resolve()
        token = repository / '.local/test/secrets/token'
        signin.private_parent(token, repository)
        self.enterContext(patch.object(signin, 'CORE', repository))
        return token.parent

    def arguments(self, root, *, mode="user-token"):
        arguments = [mode, "--environment", "test", "--tenant-id", TENANT, "--client-id", CLIENT,
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
            root = self.runtime_root(directory)
            sdk = self.sdk()
            output, errors = io.StringIO(), io.StringIO()
            def callback(server, timeout):
                self.assertEqual((root.parent / "state/gateway-login-url.txt").read_text(),
                                 "https://login.microsoftonline.com/authorize?state=expected-state")
                signin.check_private_permissions(root.parent / "state/gateway-login-url.txt")
                return {"state": "expected-state", "code": "private-code"}
            with patch.object(signin, "CallbackServer"), \
                    patch.object(signin, "wait_for_callback", side_effect=callback), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = signin.main(self.arguments(root), msal_module=sdk)
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "AUTH_URL_READY\nAUTH_COMPLETE\n")
            self.assertEqual(errors.getvalue(), "")
            self.assertFalse((root.parent / "state/gateway-login-url.txt").exists())
            self.assertEqual((root / "token").read_text(), "private-user-token")
            signin.check_private_permissions(root / "cache")
            signin.check_private_permissions(root / "token")
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
                root = self.runtime_root(directory)
                signin.protected_text(root / "token", "stale-token")
                sdk = self.sdk()
                output, errors = io.StringIO(), io.StringIO()
                callback = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(signin, "CallbackServer"), \
                        patch.object(signin, "wait_for_callback", **callback), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = signin.main(self.arguments(root), msal_module=sdk)
                self.assertEqual(status, 2)
                self.assertEqual(errors.getvalue(), code + "\n")
                self.assertFalse((root.parent / "state/gateway-login-url.txt").exists())
                self.assertFalse((root / "token").exists())
                sdk.PublicClientApplication.return_value.acquire_token_by_auth_code_flow.assert_not_called()

    def test_app_secret_is_read_from_file_and_error_body_is_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.runtime_root(directory)
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
            root = self.runtime_root(directory)
            base = self.arguments(root)
            for arguments in [base + ["--client-secret", "private-secret"],
                              base + ["--token-file", str(root / "cache")],
                              base + ["--token-file", str(root.parent / "state/gateway-login-url.txt")],
                              self.arguments(root, mode="app-token") + ["--scope", SCOPE]]:
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = signin.main(arguments, msal_module=self.sdk())
                self.assertEqual(status, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "AUTH_INVALID_ARGUMENTS\n")

    def test_unsafe_existing_token_is_preserved_and_rejected_before_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.runtime_root(directory)
            token = root / "token"
            token.write_text("old")
            if os.name != "nt":
                token.chmod(0o644)
            sdk = self.sdk()
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(signin.main(self.arguments(root), msal_module=sdk), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "AUTH_UNSAFE_FILE\n")
            sdk.PublicClientApplication.assert_not_called()
            sdk.SerializableTokenCache.assert_not_called()
            self.assertEqual(token.read_text(), "old")
            with self.assertRaises(ValueError):
                signin.check_private_permissions(token)


class SigninLocalStorageTests(unittest.TestCase):
    """認証せず、選択環境と明示的な外部保護ファイルの保存境界を確認する。"""
    def arguments(self):
        return ['user-token', '--tenant-id', TENANT, '--client-id', CLIENT, '--scope', SCOPE]

    def test_explicit_and_shell_environments_choose_separate_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(signin, 'CORE', root), patch.dict(os.environ, isolated_environment({'AZURE_ENV_NAME': 'beta'}), clear=True):
                explicit = signin.parse_arguments(self.arguments() + ['--environment', 'alpha'])
                selected = signin.parse_arguments(self.arguments())
            self.assertEqual(explicit.token_file, root / '.local/alpha/secrets/gateway-access-token.txt')
            self.assertEqual(explicit.login_url_file, root / '.local/alpha/state/gateway-login-url.txt')
            self.assertEqual(selected.token_file, root / '.local/beta/secrets/gateway-access-token.txt')
            self.assertFalse((root / '.local').exists())

    def test_saved_azd_selection_does_not_require_or_read_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve();selection = root / '.azure/config.json'
            signin.private_parent(selection, root)
            selection.write_text(json.dumps({'defaultEnvironment': 'saved'}))
            with patch.object(signin, 'CORE', root), \
                    patch.dict(os.environ, isolated_environment({'WORKFORCE_CONFIG': 'must-not-be-read.json'}), clear=True):
                arguments = signin.parse_arguments(self.arguments())
            self.assertEqual(arguments.token_file, root / '.local/saved/secrets/gateway-access-token.txt')
            self.assertFalse((root / '.local').exists())

    def test_explicit_existing_external_protected_token_and_cache_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external:
            root = Path(directory).resolve();outside = Path(external).resolve()
            signin.protect_new_path(outside, directory=True)
            token, cache = outside / 'token', outside / 'cache'
            for path in (token, cache):
                signin.protected_text(path, 'synthetic-only')
            with patch.object(signin, 'CORE', root), patch.dict(os.environ, isolated_environment(), clear=True):
                arguments = signin.parse_arguments(self.arguments() + ['--environment', 'test',
                    '--token-file', str(token), '--cache-file', str(cache)])
            self.assertEqual(arguments.token_file, token)
            self.assertEqual(arguments.cache_file, cache)
            self.assertEqual(token.read_text(), 'synthetic-only')

    def test_public_and_symlink_destinations_fail_before_sdk(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external:
            root = Path(directory).resolve()
            signin.protect_new_path(root, directory=True)
            with patch.object(signin, 'CORE', root), patch.dict(os.environ, isolated_environment(), clear=True):
                for relative in ('secrets/token', '.state/token', 'apps/portal/.local/token', '.local/other/secrets/token'):
                    with self.subTest(relative=relative), self.assertRaises(signin.SigninError):
                        signin.parse_arguments(self.arguments() + ['--environment', 'test', '--token-file', str(root / relative)])
                with directory_link(root / '.local', Path(external).resolve()):
                    with self.assertRaises(signin.SigninError):
                        signin.parse_arguments(self.arguments() + ['--environment', 'test'])

    def test_no_selected_environment_fails_without_loading_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = MagicMock();output, errors = io.StringIO(), io.StringIO()
            with patch.object(signin, 'CORE', Path(directory).resolve()), patch.dict(os.environ, isolated_environment(), clear=True), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(signin.main(self.arguments(), msal_module=sdk), 2)
            sdk.PublicClientApplication.assert_not_called()
            self.assertEqual(errors.getvalue(), 'AUTH_INVALID_ARGUMENTS\n')


class SigninRuntimeRootTests(unittest.TestCase):
    """分離したソースと保存先で、認証前の境界と合成PKCEの保存を確認する。"""

    def arguments(self, runtime, *, mode="user-token"):
        return [mode, "--runtime-root", str(runtime), "--environment", "test",
                "--tenant-id", TENANT, "--client-id", CLIENT, "--scope",
                SCOPE if mode == "user-token" else "https://gateway.example.com/.default"]

    def test_explicit_runtime_keeps_source_and_places_all_defaults_in_runtime(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True):
                arguments = signin.parse_arguments(self.arguments(runtime))
                self.assertEqual(signin.CORE, source)
            self.assertEqual(arguments.token_file, runtime / ".local/test/secrets/gateway-access-token.txt")
            self.assertEqual(arguments.login_url_file, runtime / ".local/test/state/gateway-login-url.txt")
            self.assertIsNone(arguments.cache_file)
            self.assertFalse((source / ".local").exists())
            self.assertFalse((runtime / ".local").exists())

    def test_saved_selection_and_relative_files_follow_runtime_without_parameters(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            for checkout, selected in ((source, "source"), (runtime, "saved")):
                signin.protected_text(checkout / ".azure/config.json",
                                      json.dumps({"defaultEnvironment": selected}))
            arguments = self.arguments(runtime)
            del arguments[3:5]  # Use this runtime's saved environment instead.
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment({"WORKFORCE_CONFIG": "must-not-be-read.json"}), clear=True):
                parsed = signin.parse_arguments(arguments + [
                    "--token-file", ".local/saved/secrets/token",
                    "--cache-file", ".local/saved/secrets/cache"])
            self.assertEqual(parsed.token_file, runtime / ".local/saved/secrets/token")
            self.assertEqual(parsed.cache_file, runtime / ".local/saved/secrets/cache")
            self.assertEqual(parsed.login_url_file, runtime / ".local/saved/state/gateway-login-url.txt")
            self.assertFalse((source / ".local").exists())
            self.assertFalse((runtime / ".local").exists())

    def test_explicit_external_files_remain_protected_and_are_not_copied(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage, \
                tempfile.TemporaryDirectory() as external:
            source, runtime, outside = map(lambda value: Path(value).resolve(), (directory, storage, external))
            signin.protect_new_path(outside, directory=True)
            token, cache = outside / "token", outside / "cache"
            for path in (token, cache):
                signin.protected_text(path, "synthetic-only")
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True):
                parsed = signin.parse_arguments(self.arguments(runtime) + [
                    "--token-file", str(token), "--cache-file", str(cache)])
            self.assertEqual((parsed.token_file, parsed.cache_file), (token, cache))
            self.assertEqual(token.read_text(), "synthetic-only")
            self.assertEqual(cache.read_text(), "synthetic-only")
            self.assertFalse((source / ".local").exists())
            self.assertFalse((runtime / ".local").exists())

    def test_relative_app_secret_uses_runtime_and_keeps_disjoint_outputs(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True):
                parsed = signin.parse_arguments(self.arguments(runtime, mode="app-token") + [
                    "--client-secret-file", ".local/test/secrets/client-secret"])
                self.assertEqual(parsed.client_secret_file, runtime / ".local/test/secrets/client-secret")
                self.assertEqual(parsed.token_file, runtime / ".local/test/secrets/service-access-token.txt")
                with self.assertRaisesRegex(signin.SigninError, "^AUTH_INVALID_ARGUMENTS$"):
                    signin.parse_arguments(self.arguments(runtime, mode="app-token") + [
                        "--client-secret-file", ".local/test/secrets/service-access-token.txt"])
            self.assertFalse((source / ".local").exists())
            self.assertFalse((runtime / ".local").exists())

    def test_source_storage_public_runtime_and_other_environment_fail_before_sdk(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            targets = [source / ".local/test/secrets/token", runtime / "public-token",
                       runtime / "apps/.local/token", runtime / ".local/other/secrets/token",
                       runtime / ".azure/token", runtime / ".local/test/state/gateway-login-url.txt"]
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True):
                for target in targets:
                    sdk, output, errors = MagicMock(), io.StringIO(), io.StringIO()
                    with self.subTest(kind=target.name), contextlib.redirect_stdout(output), \
                            contextlib.redirect_stderr(errors):
                        self.assertEqual(signin.main(self.arguments(runtime) + [
                            "--token-file", str(target)], msal_module=sdk), 2)
                    sdk.PublicClientApplication.assert_not_called()
                    sdk.SerializableTokenCache.assert_not_called()
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn(errors.getvalue(), ("AUTH_UNSAFE_FILE\n", "AUTH_INVALID_ARGUMENTS\n"))
            self.assertFalse((source / ".local").exists())
            self.assertFalse((runtime / ".local").exists())

    def test_missing_relative_and_linked_runtime_roots_fail_before_sdk(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True):
                for selected in (source / "missing", Path("relative-runtime")):
                    with self.assertRaisesRegex(signin.SigninError, "^AUTH_INVALID_ARGUMENTS$"):
                        signin.parse_arguments(self.arguments(selected))
                with directory_link(source / "runtime-link", runtime) as linked:
                    with self.assertRaises(signin.SigninError):
                        signin.parse_arguments(self.arguments(linked))
                for name in (".local", ".azure"):
                    with directory_link(runtime / name, source):
                        with self.assertRaises(signin.SigninError):
                            signin.parse_arguments(self.arguments(runtime))
            self.assertFalse((source / ".local").exists())

    def test_user_flow_writes_only_primary_storage_and_cleans_its_url(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as storage:
            source, runtime = Path(directory).resolve(), Path(storage).resolve()
            sdk = SigninCommandTests().sdk()
            output, errors = io.StringIO(), io.StringIO()
            url = runtime / ".local/test/state/gateway-login-url.txt"

            def callback(server, timeout):
                self.assertEqual(url.read_text(), "https://login.microsoftonline.com/authorize?state=expected-state")
                signin.check_private_permissions(url)
                self.assertFalse((source / ".local").exists())
                return {"state": "expected-state", "code": "private-code"}

            with patch.object(signin, "CORE", source), \
                    patch.dict(os.environ, isolated_environment(), clear=True), \
                    patch.object(signin, "CallbackServer"), \
                    patch.object(signin, "wait_for_callback", side_effect=callback), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = signin.main(self.arguments(runtime) + [
                    "--cache-file", ".local/test/secrets/cache",
                    "--redirect-uri", "http://127.0.0.1:43127/callback"], msal_module=sdk)
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "AUTH_URL_READY\nAUTH_COMPLETE\n")
            self.assertEqual(errors.getvalue(), "")
            self.assertFalse(url.exists())
            token = runtime / ".local/test/secrets/gateway-access-token.txt"
            self.assertEqual(signin.read_protected_text(token), "private-user-token")
            signin.check_private_permissions(runtime / ".local/test/secrets/cache")
            self.assertFalse((source / ".local").exists())
            sdk.PublicClientApplication.return_value.initiate_auth_code_flow.assert_called_once_with(
                scopes=[SCOPE], redirect_uri="http://127.0.0.1:43127/callback", response_mode="query")


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
