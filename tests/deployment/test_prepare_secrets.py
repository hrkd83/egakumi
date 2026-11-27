"""現行のAzure DNS構築で必要なEntra秘密を、安全に準備する境界を検証する。"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import warnings

from deployment.helpers import prepare_secrets
from deployment.helpers.configuration import ConfigurationError


ACCIDENTAL_SECRET = "SYNTHETIC-ACCIDENTAL-INPUT"
CLIENT_SECRET = "SYNTHETIC-ENTRA-DO-NOT-DISPLAY"
UUID_ONE = "11111111-1111-4111-8111-111111111111"
UUID_TWO = "22222222-2222-4222-8222-222222222222"
UUID_THREE = "33333333-3333-4333-8333-333333333333"
IDENTITY = f"/subscriptions/{UUID_ONE}/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns"


def azure_configuration(secret_path):
    return {
        "environmentName": "test", "productName": "egakumi", "resourceStem": "test-egakumi",
        "resourceGroupNames": {"svc": "rg-test-egakumi-svc"},
        "certificate": {"mode": "dnsPlugin", "plugin": "Azure", "azureDnsSubscriptionId": UUID_ONE,
                        "azureDnsManagedIdentityResourceId": IDENTITY},
        "credentialProviders": [{"name": "provider", "clientSecretFile": str(secret_path)}],
    }


class SecretPreparationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="core-prepare-secrets-"))
        self.root = Path(directory).resolve()
        self.entra = self.root / "secrets/entra-client-secret.txt"
        self.config = azure_configuration(self.entra)
        self.output, self.errors = io.StringIO(), io.StringIO()

    def protected(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(value)
        path.chmod(0o600)

    def add_provider(self, name, path, client_id=UUID_TWO):
        self.config["credentialProviders"].append({
            "name": name, "clientId": client_id, "clientSecretFile": str(path),
        })

    def invoke(self, answers=()):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=answers) as prompt, \
                mock.patch("builtins.input", return_value=""), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            result = prepare_secrets.prepare_secrets(self.config, self.root)
        return result, prompt

    def assert_safe_output_and_no_staging(self):
        public_output = self.output.getvalue() + self.errors.getvalue()
        self.assertNotIn(ACCIDENTAL_SECRET, public_output)
        self.assertNotIn(CLIENT_SECRET, public_output)
        self.assertEqual(list(self.root.rglob(".secret-input-*")), [])

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_shared_entra_reference_creates_one_private_secret(self):
        self.add_provider("second-provider", self.entra)
        self.assertFalse(self.entra.parent.exists())
        result, prompt = self.invoke([CLIENT_SECRET])
        self.assertEqual(result, {"created": [str(self.entra)], "reused": [], "replaced": []})
        prompt.assert_called_once()
        self.assertEqual(stat.S_IMODE(self.entra.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.entra.stat().st_mode), 0o600)
        self.assertEqual(self.entra.read_text(), CLIENT_SECRET + "\n")
        self.assertNotIn(CLIENT_SECRET, json.dumps(result))
        self.assert_safe_output_and_no_staging()

    def test_no_required_inputs_is_noop_even_without_terminal(self):
        self.config["credentialProviders"] = []
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(self.output):
            result = prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(result, {"created": [], "reused": [], "replaced": []})
        self.assertFalse(self.entra.parent.exists())

    def test_missing_inputs_require_a_terminal_before_creating_directories(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as prompt, \
                contextlib.redirect_stdout(self.output):
            with self.assertRaisesRegex(ValueError, "interactive terminal"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        prompt.assert_not_called()
        self.assertFalse(self.entra.parent.exists())

    def test_getpass_cannot_fall_back_to_echoed_input(self):
        def unavailable(_prompt):
            warnings.warn("synthetic fallback", prepare_secrets.getpass.GetPassWarning)
            self.fail("getpass warning must prevent fallback from reading")

        with self.assertRaisesRegex(ValueError, "hidden terminal"):
            self.invoke(unavailable)
        self.assertFalse(self.entra.exists())
        self.assert_safe_output_and_no_staging()

    def test_empty_or_multiline_inputs_do_not_create_files(self):
        for value in ("", " ", " leading", "trailing ", "line\nnext", "nul\x00value"):
            with self.subTest(kind=repr(value[:4])), self.assertRaises(ValueError):
                self.invoke([value])
            self.assertFalse(self.entra.exists())
        self.assert_safe_output_and_no_staging()

    def test_empty_existing_entra_is_not_overwritten(self):
        self.protected(self.entra, "\n")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.invoke([])
        self.assertEqual(self.entra.read_text(), "\n")

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_shared_existing_file_is_rejected_without_chmod_or_overwrite(self):
        self.protected(self.entra, CLIENT_SECRET)
        self.entra.chmod(0o644)
        with self.assertRaises(ValueError):
            self.invoke([])
        self.assertEqual(stat.S_IMODE(self.entra.stat().st_mode), 0o644)
        self.assertEqual(self.entra.read_text(), CLIENT_SECRET)

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_shared_existing_directory_is_not_automatically_changed(self):
        self.entra.parent.mkdir(mode=0o755)
        self.entra.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "directories"):
            self.invoke([CLIENT_SECRET])
        self.assertEqual(stat.S_IMODE(self.entra.parent.stat().st_mode), 0o755)
        self.assertFalse(self.entra.exists())
        self.assert_safe_output_and_no_staging()

    def test_existing_directory_as_file_rejected(self):
        self.entra.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.invoke([])

    def test_dangling_file_link_rejected_without_target_creation(self):
        self.entra.parent.mkdir(mode=0o700)
        destination = self.root / "do-not-create"
        self.entra.symlink_to(destination)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.invoke([])
        self.assertTrue(self.entra.is_symlink())
        self.assertFalse(destination.exists())

    def test_parent_link_rejected_without_external_creation(self):
        external = self.root / "external"
        external.mkdir(mode=0o700)
        self.entra.parent.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.invoke([])
        self.assertEqual(list(external.iterdir()), [])

    def test_external_secret_reference_is_reused_but_cannot_be_created(self):
        external = self.root / "operator-managed.txt"
        self.config["credentialProviders"][0]["clientSecretFile"] = str(external)
        with self.assertRaisesRegex(ValueError, "secrets directory"):
            self.invoke([])
        self.assertFalse(external.exists())
        self.protected(external, CLIENT_SECRET)
        result, prompt = self.invoke([])
        self.assertEqual(result["reused"], [str(external)])
        prompt.assert_not_called()
        self.assert_safe_output_and_no_staging()

    def test_interrupted_hidden_input_does_not_create_file(self):
        for interruption in (KeyboardInterrupt, EOFError):
            with self.subTest(kind=interruption.__name__), self.assertRaises(interruption):
                self.invoke([interruption])
            self.assertFalse(self.entra.exists())
        self.assert_safe_output_and_no_staging()

    def test_interrupted_second_secret_retains_completed_first_secret_for_retry(self):
        second_secret = self.entra.parent / "second-client.txt"
        self.add_provider("second-provider", second_secret)
        with self.assertRaises(KeyboardInterrupt):
            self.invoke([CLIENT_SECRET, KeyboardInterrupt])
        self.assertFalse(second_secret.exists())
        before = self.entra.read_bytes(), self.entra.stat().st_ino
        result, prompt = self.invoke([ACCIDENTAL_SECRET])
        self.assertEqual(result, {"created": [str(second_secret)], "reused": [str(self.entra)], "replaced": []})
        self.assertEqual((self.entra.read_bytes(), self.entra.stat().st_ino), before)
        self.assertEqual(second_secret.read_text(), ACCIDENTAL_SECRET + "\n")
        prompt.assert_called_once()
        self.assert_safe_output_and_no_staging()

    def test_publish_race_preserves_competing_file_and_removes_staging(self):
        real_link = os.link

        def race(source, destination, **kwargs):
            Path(destination).write_text("competing-file")
            Path(destination).chmod(0o600)
            return real_link(source, destination, **kwargs)

        with mock.patch.object(prepare_secrets.os, "link", side_effect=race), \
                self.assertRaisesRegex(ValueError, "publication failed"):
            self.invoke([CLIENT_SECRET])
        self.assertEqual(self.entra.read_text(), "competing-file")
        self.assert_safe_output_and_no_staging()

    def test_failed_staged_validation_cleans_up_without_publishing(self):
        with mock.patch.object(prepare_secrets, "_validate_file", side_effect=ValueError("safe failure")), \
                self.assertRaisesRegex(ValueError, "safe failure"):
            self.invoke([CLIENT_SECRET])
        self.assertFalse(self.entra.exists())
        self.assert_safe_output_and_no_staging()

    def test_complete_guide_and_confirmation_precede_hidden_input(self):
        events = []

        def confirmation(prompt):
            guide = self.output.getvalue()
            for text in ("Entraアプリのシークレットの「値」", "接続: provider", "次の非表示欄へ入力してください",
                         "保存先: secrets/entra-client-secret.txt（構築時にAPIMへ設定）", "端末に保護して保存"):
                self.assertIn(text, guide)
            self.assertNotIn("Key Vault", guide)
            self.assertNotIn("authorizationProviders/", guide)
            self.assertIn("Enterで入力欄へ進む（qで中止）", prompt)
            self.assertFalse(self.entra.parent.exists())
            events.append("confirmation")
            return ""

        def hidden_input(prompt):
            self.assertEqual(events, ["confirmation"])
            self.assertIn("Entraアプリのシークレットの「値」（入力非表示）", prompt)
            events.append("entra")
            return CLIENT_SECRET

        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", side_effect=confirmation), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=hidden_input), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(events, ["confirmation", "entra"])
        self.assert_safe_output_and_no_staging()

    def test_readiness_cancellation_creates_nothing_and_reads_no_secret(self):
        for answer in ("q", "Q", "quit"):
            with self.subTest(answer=answer), \
                    mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", return_value=answer), \
                    mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                    mock.patch.object(prepare_secrets.os, "link") as publication, \
                    contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
                with self.assertRaisesRegex(ValueError, "中止"):
                    prepare_secrets.prepare_secrets(self.config, self.root)
            hidden.assert_not_called()
            publication.assert_not_called()
            self.assertFalse(self.entra.parent.exists())

    def test_readiness_field_does_not_echo_an_accidentally_supplied_value(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value=ACCIDENTAL_SECRET), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaisesRegex(ValueError, "確認欄") as error:
                prepare_secrets.prepare_secrets(self.config, self.root)
        hidden.assert_not_called()
        self.assertNotIn(ACCIDENTAL_SECRET, str(error.exception))
        self.assertFalse(self.entra.parent.exists())
        self.assert_safe_output_and_no_staging()

    def test_multiple_entra_inputs_show_distinct_purposes_without_client_ids(self):
        other = self.entra.parent / "another-client.txt"
        identity = self.entra.parent / "credential-client.txt"
        self.config["credentialProviders"][0].update(name="cm-provider", clientId=UUID_ONE)
        self.add_provider("another-cm-provider", other)
        self.config["gateway"] = {"authentication": {"credentialLogin": {"identity": {
            "clientId": UUID_THREE, "clientSecretFile": str(identity),
        }}}}
        result, prompt = self.invoke([CLIENT_SECRET, "SYNTHETIC-OTHER-CLIENT", "SYNTHETIC-IDENTITY-CLIENT"])
        self.assertEqual(result["created"], [str(self.entra), str(other), str(identity)])
        self.assertEqual(prompt.call_count, 3)
        guide_lines = [line.strip() for line in self.output.getvalue().splitlines()]
        for line in ("入力: CM接続: cm-provider", "入力: CM接続: another-cm-provider", "入力: CM本人確認（OIDC）",
                     "保存先: secrets/another-client.txt（構築時にAPIMへ設定）",
                     "保存先: secrets/credential-client.txt（構築時に認証窓口用Key Vaultへ設定）"):
            self.assertIn(line, guide_lines)
        terminal = self.output.getvalue() + self.errors.getvalue() + "\n".join(call.args[0] for call in prompt.call_args_list)
        for value in (UUID_ONE, UUID_TWO, UUID_THREE, "SYNTHETIC-OTHER-CLIENT", "SYNTHETIC-IDENTITY-CLIENT"):
            self.assertNotIn(value, terminal)
        self.assert_safe_output_and_no_staging()

    def test_cli_accepts_only_config_and_never_echoes_secret_arguments(self):
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaises(SystemExit) as error:
                prepare_secrets.main(["--config", "public.json", "--token", ACCIDENTAL_SECRET])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn(ACCIDENTAL_SECRET, self.errors.getvalue())
        self.assertIn("hidden interactive prompts", self.errors.getvalue())

    @contextlib.contextmanager
    def cli_workspace(self):
        config = azure_configuration(self.root / ".local/test/secrets/entra-client-secret.txt")
        with mock.patch.object(prepare_secrets, "ROOT", self.root), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(prepare_secrets, "load_configuration", return_value=config) as load, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            yield load

    def test_cli_success_prints_counts_only_and_creates_no_ssh_key(self):
        with self.cli_workspace() as load, \
                mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", return_value=CLIENT_SECRET), \
                mock.patch("builtins.input", return_value=""):
            self.assertEqual(prepare_secrets.main(["--config", ".local/test/params.local.json"]), 0)
        load.assert_called_once_with(Path(".local/test/params.local.json"))
        self.assertIn("1 created, 0 reused", self.output.getvalue())
        self.assertFalse((self.root / ".local/test/secrets/management-ssh").exists())
        self.assert_safe_output_and_no_staging()

    def test_windows_permission_branch_does_not_require_unix_fchmod(self):
        path = self.root / "synthetic-staged-input"
        with path.open("w", encoding="utf-8") as stream, \
                mock.patch.object(prepare_secrets.os, "name", "nt"), \
                mock.patch.object(prepare_secrets.os, "fchmod", side_effect=AttributeError("not available"), create=True) as unix_permission:
            prepare_secrets._restrict_staged_permissions(stream, path)
        unix_permission.assert_not_called()

    def test_cli_write_encoding_error_does_not_disclose_the_secret(self):
        real_fdopen = os.fdopen
        private_error = UnicodeEncodeError("utf-8", CLIENT_SECRET, 0, len(CLIENT_SECRET), CLIENT_SECRET)

        def broken_writer(descriptor, *arguments, **keywords):
            stream = real_fdopen(descriptor, *arguments, **keywords)
            wrapped = mock.MagicMock(wraps=stream)
            wrapped.__enter__.return_value = wrapped
            wrapped.__exit__.side_effect = lambda *exception: stream.close()
            wrapped.write.side_effect = private_error
            return wrapped

        with self.cli_workspace(), \
                mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", return_value=CLIENT_SECRET), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(prepare_secrets.os, "fdopen", side_effect=broken_writer):
            self.assertEqual(prepare_secrets.main(["--config", ".local/test/params.local.json"]), 1)
        self.assertIn("input values were suppressed", self.errors.getvalue())
        self.assertFalse((self.root / ".local/test/secrets/entra-client-secret.txt").exists())
        self.assert_safe_output_and_no_staging()

    def test_cli_interruption_is_safe_and_actionable(self):
        with self.cli_workspace(), mock.patch.object(prepare_secrets, "prepare_secrets", side_effect=KeyboardInterrupt):
            self.assertEqual(prepare_secrets.main(["--config", ".local/test/params.local.json"]), 1)
        self.assertIn("interrupted", self.errors.getvalue())
        self.assert_safe_output_and_no_staging()


class AzurePublicSecretPreparationTests(unittest.TestCase):
    """公開入口はAzure DNSだけを受け付け、DNS秘密を読まない。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="azure-public-secret-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.entra = self.root / "secrets/entra-client-secret.txt"
        self.config = azure_configuration(self.entra)
        self.output = io.StringIO()

    def test_azure_flow_collects_only_entra_and_does_not_read_dns_credentials(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", return_value=CLIENT_SECRET) as hidden, \
                mock.patch("builtins.input", return_value="") as ready, \
                mock.patch.object(prepare_secrets, "load_dns_plugin_arguments", side_effect=AssertionError("No DNS secret read")), \
                contextlib.redirect_stdout(self.output):
            result = prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(result, {"created": [str(self.entra)], "reused": [], "replaced": []})
        self.assertEqual(hidden.call_count, 1)
        self.assertEqual(ready.call_count, 1)
        self.assertEqual(self.entra.read_text(), CLIENT_SECRET + "\n")
        self.assertNotIn(CLIENT_SECRET, self.output.getvalue())
        for label in ("Cloudflare", "Route53", "DNS API token"):
            self.assertNotIn(label, self.output.getvalue())

    def test_existing_azure_entra_secret_is_reused_without_any_input(self):
        self.entra.parent.mkdir(mode=0o700)
        self.entra.write_text(CLIENT_SECRET)
        self.entra.chmod(0o600)
        before = self.entra.read_bytes(), self.entra.stat().st_ino, self.entra.stat().st_mtime_ns
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                mock.patch("builtins.input") as ready, contextlib.redirect_stdout(self.output):
            result = prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(result, {"created": [], "reused": [str(self.entra)], "replaced": []})
        self.assertEqual(before, (self.entra.read_bytes(), self.entra.stat().st_ino, self.entra.stat().st_mtime_ns))
        hidden.assert_not_called()
        ready.assert_not_called()
        guide = self.output.getvalue()
        self.assertIn("秘密情報は準備済みです", guide)
        self.assertIn("再利用", guide)
        self.assertNotIn(CLIENT_SECRET, guide)
        self.assertEqual(len(guide.strip().splitlines()), 1)

    def test_unsupported_certificate_modes_and_providers_stop_before_secret_processing(self):
        unsupported = [{"mode": "unsupported"}, {}, *({"mode": "dnsPlugin", "plugin": provider}
                        for provider in ("Cloudflare", "Route53", "OtherProvider")),
                       *({**self.config["certificate"], field: "/must/not/read/input"}
                         for field in ("pluginArgumentsFile", "unknownCertificateField"))]
        for certificate in unsupported:
            with self.subTest(certificate=certificate), \
                    mock.patch.object(prepare_secrets, "_prepare_secret_files") as lower, \
                    mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                    mock.patch("builtins.input") as ready:
                with self.assertRaises(ConfigurationError):
                    prepare_secrets.prepare_secrets({**self.config, "certificate": certificate}, self.root)
                lower.assert_not_called()
                hidden.assert_not_called()
                ready.assert_not_called()
                self.assertFalse(self.entra.parent.exists())

    def test_missing_azure_identity_stops_before_input_or_writes(self):
        del self.config["certificate"]["azureDnsManagedIdentityResourceId"]
        with mock.patch.object(prepare_secrets, "_prepare_secret_files") as lower, \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden:
            with self.assertRaises(ConfigurationError):
                prepare_secrets.prepare_secrets(self.config, self.root)
        lower.assert_not_called()
        hidden.assert_not_called()
        self.assertFalse(self.entra.parent.exists())

    def test_cli_unsupported_inputs_stop_before_workspace_creation(self):
        self.config["certificate"] = {"mode": "dnsPlugin", "plugin": "Cloudflare"}
        with mock.patch.object(prepare_secrets, "load_configuration", return_value=self.config), \
                mock.patch.object(prepare_secrets, "local_paths") as paths, \
                mock.patch.object(prepare_secrets, "prepare_secrets") as preparation, \
                contextlib.redirect_stderr(self.output):
            self.assertEqual(prepare_secrets.main(["--config", "params.json"]), 1)
        paths.assert_not_called()
        preparation.assert_not_called()
        self.assertIn("Azure Public DNS", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
