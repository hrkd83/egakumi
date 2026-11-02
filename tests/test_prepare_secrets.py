"""秘密の初回入力・再利用・保護を、一時領域と合成資格情報だけで確認する。

実際のユーザー入力、実secret、Azure、Entra、DNS、SSH鍵生成を一切使わない。
構築で使う読込関数まで通し、対話入力からのJSON変換が正しいことも調べる。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import prepare_secrets
from deployment.helpers.configuration import ConfigurationError, DnsProviderMismatchError, load_dns_plugin_arguments


TOKEN = "SYNTHETIC-DNS-DO-NOT-DISPLAY"
CLIENT_SECRET = "SYNTHETIC-ENTRA-DO-NOT-DISPLAY"
UUID_ONE = "11111111-1111-4111-8111-111111111111"
UUID_TWO = "22222222-2222-4222-8222-222222222222"
UUID_THREE = "33333333-3333-4333-8333-333333333333"
IDENTITY = f"/subscriptions/{UUID_ONE}/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns"


class PrepareSecretsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="core-prepare-secrets-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.dns = self.root / "secrets/dns-plugin-arguments.json"
        self.entra = self.root / "secrets/entra-client-secret.txt"
        self.config = {
            "environmentName": "test", "productName": "egakumi", "resourceStem": "test-egakumi",
            "resourceGroupNames": {"svc": "rg-test-egakumi-svc"},
            "certificate": {"mode": "dnsPlugin", "plugin": "Cloudflare", "pluginArgumentsFile": str(self.dns)},
            "credentialProviders": [{"name": "provider", "clientSecretFile": str(self.entra)}],
            "apiDefinitions": [],
        }
        self.output, self.errors = io.StringIO(), io.StringIO()

    def protected(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(value)
        path.chmod(0o600)

    def existing_dns(self, plugin=None):
        plugin = plugin or self.config["certificate"]["plugin"]
        values = {"CFToken": {"type": "secureString", "value": TOKEN}}
        if plugin != "Cloudflare":
            values = {"PluginToken": {"type": "secureString", "value": TOKEN}}
        self.protected(self.dns, json.dumps(values))

    def invoke(self, answers=(), config=None):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=answers) as prompt, \
                mock.patch("builtins.input", return_value=""), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            result = prepare_secrets.prepare_secrets(config or self.config, self.root)
        return result, prompt

    def assert_safe_output_and_no_staging(self):
        public_output = self.output.getvalue() + self.errors.getvalue()
        self.assertNotIn(TOKEN, public_output)
        self.assertNotIn(CLIENT_SECRET, public_output)
        self.assertEqual(list(self.root.rglob(".secret-input-*")), [])

    def test_fresh_clone_creates_private_directory_two_files_and_dedupes_entra(self):
        self.assertFalse(self.dns.parent.exists())
        result, prompt = self.invoke([CLIENT_SECRET, TOKEN])
        self.assertEqual(result, {"created": [str(self.entra), str(self.dns)], "reused": [], "replaced": []})
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(stat.S_IMODE(self.dns.parent.stat().st_mode), 0o700)
        for path in (self.dns, self.entra):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.entra.read_text(), CLIENT_SECRET + "\n")
        args = json.loads(load_dns_plugin_arguments(self.config["certificate"]))
        self.assertEqual(args, {"CFToken": {"type": "secureString", "value": TOKEN}})
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(CLIENT_SECRET, json.dumps(result))
        self.assert_safe_output_and_no_staging()

    def test_existing_files_are_reused_without_input_or_modification_in_non_tty(self):
        self.existing_dns()
        self.protected(self.entra, CLIENT_SECRET)
        before = [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.dns, self.entra)]
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as prompt:
            result = prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(result, {"created": [], "reused": [str(self.entra), str(self.dns)], "replaced": []})
        prompt.assert_not_called()
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.dns, self.entra)])

    def test_only_missing_entra_is_prompted(self):
        self.existing_dns()
        before = self.dns.read_bytes()
        result, prompt = self.invoke([CLIENT_SECRET])
        self.assertEqual(result["created"], [str(self.entra)])
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(self.dns.read_bytes(), before)

    def test_route53_conversion_is_accepted_by_construction_loader(self):
        self.config["certificate"]["plugin"] = "Route53"
        self.config["credentialProviders"] = []
        self.config["apiDefinitions"] = []
        result, prompt = self.invoke(["SYNTHETIC-ACCESS-ID", TOKEN])
        args = json.loads(load_dns_plugin_arguments(self.config["certificate"]))
        self.assertEqual(args, {"R53AccessKey": {"type": "string", "value": "SYNTHETIC-ACCESS-ID"},
                                "R53SecretKey": {"type": "secureString", "value": TOKEN}})
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(len(result["created"]), 1)
        self.assert_safe_output_and_no_staging()

    def test_azure_managed_identity_requires_no_dns_secret_file_or_prompt(self):
        self.config['certificate'].update(plugin='Azure', azureDnsSubscriptionId=UUID_ONE,
                                        azureDnsManagedIdentityResourceId=IDENTITY)
        self.config['credentialProviders'] = []
        self.config['apiDefinitions'] = []
        result, prompt = self.invoke([])
        prompt.assert_not_called()
        self.assertEqual(result, {'created': [], 'reused': [], 'replaced': []})
        self.assertFalse(self.dns.exists())

    def test_azure_missing_identity_stops_before_secret_input(self):
        self.config['certificate']['plugin'] = 'Azure'
        with self.assertRaises(ConfigurationError):
            self.invoke([])
        self.assertFalse(self.dns.exists())
        self.assertFalse(self.entra.exists())

    def test_prepared_certificate_does_not_prompt_for_dns_or_read_pfx(self):
        self.config["certificate"] = {"mode": "prepared", "preparedPfxFile": "/must/not/read/private.pfx"}
        result, prompt = self.invoke([CLIENT_SECRET])
        self.assertEqual(result["created"], [str(self.entra)])
        self.assertEqual(prompt.call_count, 1)
        self.assertFalse(self.dns.exists())

    def test_no_required_inputs_is_noop_even_without_terminal(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False):
            self.assertEqual(prepare_secrets.prepare_secrets({}, self.root), {"created": [], "reused": [], "replaced": []})
        self.assertFalse(self.dns.parent.exists())

    def test_missing_inputs_reject_non_tty_before_creating_any_directory(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "interactive terminal"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        prompt.assert_not_called()
        self.assertFalse(self.dns.parent.exists())

    def test_getpass_cannot_fall_back_to_echoed_input(self):
        def unavailable(prompt):
            warnings.warn("synthetic fallback", prepare_secrets.getpass.GetPassWarning)
            self.fail("getpass warning must prevent fallback from reading")
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=unavailable), \
                mock.patch("builtins.input", return_value=""):
            with self.assertRaisesRegex(ValueError, "hidden terminal"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertFalse(self.dns.exists())

    def test_empty_or_multiline_inputs_do_not_create_files(self):
        for value in ("", " ", " leading", "trailing ", "line\nnext", "nul\x00value"):
            with self.subTest(kind=repr(value[:4])):
                with self.assertRaises(ValueError):
                    self.invoke([value])
                self.assertFalse(self.dns.exists())
        self.assert_safe_output_and_no_staging()

    def test_invalid_existing_dns_preserved_and_no_further_inputs_requested(self):
        self.protected(self.dns, "not valid json")
        with mock.patch.object(prepare_secrets.getpass, "getpass") as prompt:
            with self.assertRaises(ValueError):
                prepare_secrets.prepare_secrets(self.config, self.root)
        prompt.assert_not_called()
        self.assertEqual(self.dns.read_text(), "not valid json")
        self.assertFalse(self.entra.exists())

    def test_empty_existing_entra_is_not_overwritten(self):
        self.existing_dns()
        self.protected(self.entra, "\n")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.invoke([CLIENT_SECRET])
        self.assertEqual(self.entra.read_text(), "\n")

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_shared_existing_file_is_rejected_without_chmod_or_overwrite(self):
        self.existing_dns()
        self.dns.chmod(0o644)
        with self.assertRaises(ValueError):
            self.invoke([CLIENT_SECRET, TOKEN])
        self.assertEqual(stat.S_IMODE(self.dns.stat().st_mode), 0o644)

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_shared_existing_directory_is_not_automatically_changed(self):
        self.dns.parent.mkdir(mode=0o755)
        self.dns.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "directories"):
            self.invoke([TOKEN])
        self.assertEqual(stat.S_IMODE(self.dns.parent.stat().st_mode), 0o755)
        self.assertFalse(self.dns.exists())
        self.assert_safe_output_and_no_staging()

    def test_existing_directory_as_file_rejected(self):
        self.dns.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.invoke([TOKEN])

    def test_dangling_file_link_rejected_without_target_creation(self):
        self.dns.parent.mkdir(mode=0o700)
        destination = self.root / "do-not-create"
        self.dns.symlink_to(destination)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.invoke([TOKEN])
        self.assertTrue(self.dns.is_symlink())
        self.assertFalse(destination.exists())

    def test_parent_link_rejected_without_external_creation(self):
        external = self.root / "external"
        external.mkdir(mode=0o700)
        (self.root / "secrets").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.invoke([TOKEN])
        self.assertEqual(list(external.iterdir()), [])

    def test_external_missing_reference_cannot_be_created(self):
        self.config["certificate"]["pluginArgumentsFile"] = str(self.root / "outside-secrets.json")
        with self.assertRaisesRegex(ValueError, "secrets directory"):
            self.invoke([TOKEN])
        self.assertFalse((self.root / "outside-secrets.json").exists())

    def test_external_valid_reference_can_be_reused(self):
        external = self.root / "external-token.json"
        self.protected(external, json.dumps({"CFToken": {"type": "secureString", "value": TOKEN}}))
        self.config["certificate"]["pluginArgumentsFile"] = str(external)
        result, prompt = self.invoke([CLIENT_SECRET])
        self.assertEqual(result["reused"], [str(external)])
        self.assertEqual(prompt.call_count, 1)

    def test_dns_and_entra_cannot_share_one_file(self):
        self.config["credentialProviders"][0]["clientSecretFile"] = str(self.dns)
        with self.assertRaisesRegex(ValueError, "different files"):
            self.invoke([TOKEN])
        self.assertFalse(self.dns.parent.exists())

    def test_unknown_plugin_missing_input_directs_to_official_guide(self):
        self.config["certificate"]["plugin"] = "OtherProvider"
        with self.assertRaisesRegex(ValueError, "official Posh-ACME"):
            self.invoke([TOKEN])
        self.assertFalse(self.dns.parent.exists())

    def test_unknown_plugin_existing_typed_input_is_reused(self):
        self.config["certificate"]["plugin"] = "OtherProvider"
        self.existing_dns()
        result, prompt = self.invoke([CLIENT_SECRET])
        self.assertEqual(result["reused"], [str(self.dns)])
        self.assertEqual(prompt.call_count, 1)

    def test_interruption_before_first_save_does_not_create_file(self):
        for interruption in (KeyboardInterrupt, EOFError):
            with self.subTest(kind=interruption.__name__):
                with self.assertRaises(interruption):
                    self.invoke([interruption])
                self.assertFalse(self.dns.exists())
        self.assert_safe_output_and_no_staging()

    def test_interruption_after_entra_retains_completed_file_for_retry(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke([CLIENT_SECRET, KeyboardInterrupt])
        before = self.entra.read_bytes()
        result, prompt = self.invoke([TOKEN])
        self.assertEqual(result["reused"], [str(self.entra)])
        self.assertEqual(self.entra.read_bytes(), before)
        self.assertEqual(prompt.call_count, 1)

    def test_publish_race_preserves_competing_file_and_removes_staging(self):
        real_link = os.link
        def race(source, destination, **kwargs):
            Path(destination).write_text("competing-file")
            Path(destination).chmod(0o600)
            return real_link(source, destination, **kwargs)
        with mock.patch.object(prepare_secrets.os, "link", side_effect=race):
            with self.assertRaisesRegex(ValueError, "publication failed"):
                self.invoke([TOKEN])
        self.assertEqual(self.entra.read_text(), "competing-file")
        self.assert_safe_output_and_no_staging()

    def test_failed_validation_does_not_publish_or_leave_staging(self):
        self.config["credentialProviders"] = []
        self.config["apiDefinitions"] = []
        with mock.patch.object(prepare_secrets, "load_dns_plugin_arguments", side_effect=ValueError("safe failure")):
            with self.assertRaisesRegex(ValueError, "safe failure"):
                self.invoke([TOKEN])
        self.assertFalse(self.dns.exists())
        self.assert_safe_output_and_no_staging()

    def test_complete_guide_and_confirmation_precede_any_hidden_input(self):
        events = []
        def confirmation(prompt):
            guide = self.output.getvalue()
            self.assertIn("Entraアプリのシークレットの「値」", guide)
            self.assertIn("接続: provider", guide)
            self.assertIn("DNS Edit / Zone Read", guide)
            self.assertIn("次の非表示欄へ入力してください", guide)
            self.assertNotIn("|", guide)
            self.assertNotIn("authorizationProviders/", guide)
            self.assertLess(guide.index("Entraアプリのシークレットの「値」"), guide.index("Cloudflare API token"))
            self.assertIn("Enterで入力欄へ進む（qで中止）", prompt)
            self.assertFalse(self.dns.parent.exists())
            events.append("confirmation")
            return ""
        def hidden_input(prompt):
            self.assertTrue(events)
            self.assertEqual(events[0], "confirmation")
            if len(events) == 1:
                self.assertIn("Entraアプリのシークレットの「値」（入力非表示）", prompt)
                events.append("entra")
                return CLIENT_SECRET
            self.assertIn("Cloudflare", prompt)
            events.append("dns")
            return TOKEN
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", side_effect=confirmation), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=hidden_input), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(events, ["confirmation", "entra", "dns"])
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
            self.assertFalse(self.dns.parent.exists())

    def test_readiness_field_does_not_echo_an_accidentally_supplied_value(self):
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value=TOKEN), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaisesRegex(ValueError, "確認欄") as error:
                prepare_secrets.prepare_secrets(self.config, self.root)
        hidden.assert_not_called()
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertFalse(self.dns.parent.exists())
        self.assert_safe_output_and_no_staging()

    def test_all_ready_summary_needs_no_confirmation_and_exposes_no_values(self):
        self.existing_dns()
        self.protected(self.entra, CLIENT_SECRET)
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch("builtins.input") as confirmation, \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            prepare_secrets.prepare_secrets(self.config, self.root)
        confirmation.assert_not_called()
        hidden.assert_not_called()
        self.assertIn("秘密情報は準備済みです", self.output.getvalue())
        self.assertIn("再利用", self.output.getvalue())
        self.assertIn("Cloudflare", self.output.getvalue())
        self.assertEqual(len(self.output.getvalue().strip().splitlines()), 1)
        self.assert_safe_output_and_no_staging()

    def test_multiple_entra_inputs_are_distinguished_by_public_names_and_client_ids(self):
        other = self.root / "secrets/another-client.txt"
        self.config["credentialProviders"][0].update({"name": "cm-provider", "clientId": UUID_ONE})
        self.config["credentialProviders"].append({"name": "another-cm-provider", "clientId": UUID_TWO, "clientSecretFile": str(other)})
        result, prompt = self.invoke([CLIENT_SECRET, "SYNTHETIC-OTHER-CLIENT", TOKEN])
        self.assertEqual(result["created"], [str(self.entra), str(other), str(self.dns)])
        self.assertEqual(prompt.call_count, 3)
        self.assertIn("cm-provider", self.output.getvalue())
        self.assertIn("another-cm-provider", self.output.getvalue())
        self.assertIn(UUID_ONE, self.output.getvalue())
        self.assertIn(UUID_TWO, self.output.getvalue())
        self.assertNotIn("SYNTHETIC-OTHER-CLIENT", self.output.getvalue())
        self.assert_safe_output_and_no_staging()

    def test_dns_guides_cover_provider_specific_inputs_and_acquisition_menus(self):
        for plugin, required in (
            ("Cloudflare", ("API Tokens", "DNS Edit / Zone Read", "token")),
            ("Route53", ("AWS IAM", "Access key ID", "Secret access key", "Public Hosted Zone")),
        ):
            with self.subTest(plugin=plugin):
                self.config["certificate"]["plugin"] = plugin
                specification = next(item for item in prepare_secrets._specifications(self.config) if item["kind"] == "dns")
                lines = prepare_secrets._guide_lines(specification)
                guide = "\n".join(lines)
                for item in required:
                    self.assertIn(item, guide)
                self.assertNotIn("架空", guide)
                self.assertNotIn("|", guide)
                self.assertNotIn(TOKEN, guide)

    def test_storage_destinations_are_shown_before_confirmation_and_input(self):
        events = []
        def ready(prompt):
            guide = self.output.getvalue()
            for expected in ("保存先: secrets/entra-client-secret.txt", "構築時にAPIMへ設定",
                             "保存先: secrets/dns-plugin-arguments.json", "Key Vault",
                             "端末に保護して保存"):
                self.assertIn(expected, guide)
            self.assertNotIn("authorizationProviders/", guide)
            self.assertNotIn("|", guide)
            self.assertFalse(self.dns.parent.exists())
            events.append("ready")
            return ""
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", side_effect=ready), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=[CLIENT_SECRET, TOKEN]), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(events, ["ready"])
        self.assert_safe_output_and_no_staging()

    def test_cm_only_secret_is_not_described_as_key_vault_stored(self):
        self.config["certificate"] = {"mode": "prepared"}
        self.config["apiDefinitions"] = []
        self.config["credentialProviders"][0]["clientId"] = UUID_ONE
        self.invoke([CLIENT_SECRET])
        guide = self.output.getvalue()
        self.assertIn("接続: provider", guide)
        self.assertIn("アプリID: " + UUID_ONE, guide)
        self.assertIn("証明書とシークレット", guide)
        self.assertIn("保存先: secrets/entra-client-secret.txt", guide)
        self.assertIn("構築時にAPIMへ設定", guide)
        self.assertNotIn("Key Vault", guide)
        self.assertNotIn("authorizationProviders/", guide)
        self.assertNotIn("|", guide)
        self.assertLessEqual(len([line for line in guide.splitlines() if line.strip()]), 9)
        self.assert_safe_output_and_no_staging()

    def test_existing_inputs_show_only_reuse_summary_without_prompt(self):
        self.existing_dns()
        self.protected(self.entra, CLIENT_SECRET)
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch("builtins.input") as confirmation, \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            prepare_secrets.prepare_secrets(self.config, self.root)
        confirmation.assert_not_called()
        hidden.assert_not_called()
        guide = self.output.getvalue()
        for expected in ("秘密情報は準備済みです", "再利用", "Cloudflare"):
            self.assertIn(expected, guide)
        self.assertEqual(len(guide.strip().splitlines()), 1)
        self.assertNotIn("保存先:", guide)
        self.assertNotIn("authorizationProviders/", guide)
        self.assert_safe_output_and_no_staging()

    def prepare_dns_change(self, plugin="Route53"):
        """同じ保存先に旧Cloudflareを置き、公開設定だけを変更する合成ケース。"""
        self.protected(self.entra, CLIENT_SECRET)
        self.existing_dns("Cloudflare")
        self.config["certificate"]["plugin"] = plugin
        return self.dns.read_bytes()

    def test_dns_provider_change_reprompts_only_dns_and_atomically_replaces_after_validation(self):
        before = self.prepare_dns_change()
        entra_before = (self.entra.read_bytes(), self.entra.stat().st_ino, self.entra.stat().st_mtime_ns)
        events = []
        real_replace = os.replace
        def checked_replace(source, target):
            self.assertEqual(self.dns.read_bytes(), before)
            staged = {**self.config["certificate"], "pluginArgumentsFile": str(source)}
            load_dns_plugin_arguments(staged)
            events.append("validated-before-replace")
            real_replace(source, target)
        with mock.patch.object(prepare_secrets.os, "replace", side_effect=checked_replace):
            result, prompt = self.invoke(["SYNTHETIC-NEW-ACCESS", "SYNTHETIC-NEW-SECRET"])
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(result, {"created": [], "reused": [str(self.entra)], "replaced": [str(self.dns)]})
        self.assertEqual(events, ["validated-before-replace"])
        self.assertEqual(entra_before, (self.entra.read_bytes(), self.entra.stat().st_ino, self.entra.stat().st_mtime_ns))
        self.assertIn("R53SecretKey", json.loads(load_dns_plugin_arguments(self.config["certificate"])))
        self.assertEqual(stat.S_IMODE(self.dns.stat().st_mode), 0o600)
        self.assertIn("置換対象: secrets/dns-plugin-arguments.json", self.output.getvalue())
        self.assertIn("成功するまでは既存DNSファイルを保持", self.output.getvalue())
        self.assertNotIn("SYNTHETIC-NEW-SECRET", self.output.getvalue() + self.errors.getvalue())
        self.assertFalse(list(self.root.rglob("*.replace-lock")))
        self.assert_safe_output_and_no_staging()

    def test_dns_provider_change_to_azure_preserves_and_ignores_old_credentials(self):
        before = self.prepare_dns_change('Azure')
        self.config['certificate'].update(azureDnsSubscriptionId=UUID_ONE,
                                        azureDnsManagedIdentityResourceId=IDENTITY)
        with mock.patch.object(prepare_secrets, 'load_dns_plugin_arguments', side_effect=AssertionError('must not read old DNS secrets')):
            result, prompt = self.invoke([])
        prompt.assert_not_called()
        self.assertEqual(result['replaced'], [])
        self.assertEqual(self.dns.read_bytes(), before)
        self.assert_safe_output_and_no_staging()

    def test_dns_provider_change_non_tty_preserves_original_without_prompt(self):
        before = self.prepare_dns_change()
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as prompt, \
                mock.patch("builtins.input") as ready, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaisesRegex(ValueError, "interactive terminal"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        prompt.assert_not_called(); ready.assert_not_called()
        self.assertEqual(self.dns.read_bytes(), before)
        self.assert_safe_output_and_no_staging()

    def test_dns_provider_change_cancel_keeps_original_and_does_not_ask_for_secret(self):
        before = self.prepare_dns_change()
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="q"), \
                mock.patch.object(prepare_secrets.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaisesRegex(ValueError, "中止"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        hidden.assert_not_called()
        self.assertEqual(self.dns.read_bytes(), before)
        self.assert_safe_output_and_no_staging()

    def test_dns_provider_change_interruption_or_invalid_input_keeps_original(self):
        for answer in (KeyboardInterrupt, EOFError, ""):
            before = self.prepare_dns_change()
            with self.subTest(answer=type(answer).__name__), self.assertRaises((KeyboardInterrupt, EOFError, ValueError)):
                self.invoke(["SYNTHETIC-NEW-ACCESS", answer])
            self.assertEqual(self.dns.read_bytes(), before)
            self.assert_safe_output_and_no_staging()

    def test_invalid_staged_dns_is_not_published_over_previous_provider(self):
        before = self.prepare_dns_change()
        invalid = json.dumps({"R53AccessKey": "SYNTHETIC-NEW-ACCESS"})
        with mock.patch.object(prepare_secrets, "_dns_value", return_value=invalid):
            with self.assertRaises(DnsProviderMismatchError):
                self.invoke([])
        self.assertEqual(self.dns.read_bytes(), before)
        self.assertFalse(list(self.root.rglob("*.replace-lock")))
        self.assert_safe_output_and_no_staging()

    def test_dns_changed_by_another_process_during_input_is_not_overwritten(self):
        self.prepare_dns_change()
        competing = json.dumps({"CFToken": {"type": "secureString", "value": "SYNTHETIC-COMPETING"}})
        answers = iter(["SYNTHETIC-NEW-ACCESS", "SYNTHETIC-NEW-SECRET"])
        def race(prompt):
            value = next(answers)
            if value == "SYNTHETIC-NEW-SECRET":
                self.protected(self.dns, competing)
            return value
        with mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=race), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaisesRegex(ValueError, "changed during input"):
                prepare_secrets.prepare_secrets(self.config, self.root)
        self.assertEqual(self.dns.read_text(), competing)
        self.assertFalse(list(self.root.rglob("*.replace-lock")))
        self.assert_safe_output_and_no_staging()

    def test_dns_replace_failure_preserves_original_and_cleans_up_staging_and_lock(self):
        before = self.prepare_dns_change()
        with mock.patch.object(prepare_secrets.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.invoke(["SYNTHETIC-NEW-ACCESS", "SYNTHETIC-NEW-SECRET"])
        self.assertEqual(self.dns.read_bytes(), before)
        self.assertFalse(list(self.root.rglob("*.replace-lock")))
        self.assert_safe_output_and_no_staging()

    def test_external_provider_mismatch_is_not_replaced_or_prompted(self):
        self.prepare_dns_change()
        external = self.root / "external-dns.json"
        self.protected(external, self.dns.read_text())
        self.config["certificate"]["pluginArgumentsFile"] = str(external)
        with self.assertRaisesRegex(ValueError, "secrets directory"):
            self.invoke([])
        self.assertIn("CFToken", json.loads(external.read_text()))
        self.assert_safe_output_and_no_staging()

    def test_existing_azure_managed_identity_form_is_reused_without_secret_input(self):
        self.protected(self.entra, CLIENT_SECRET)
        self.config["certificate"].update(plugin="Azure", azureDnsSubscriptionId=UUID_ONE, azureDnsManagedIdentityResourceId=IDENTITY)
        self.protected(self.dns, json.dumps({"AZSubscriptionId": UUID_ONE, "AZUseIMDS": {"type": "boolean", "value": True}}))
        before = self.dns.read_bytes()
        result, prompt = self.invoke([])
        prompt.assert_not_called()
        self.assertEqual(result["replaced"], [])
        self.assertEqual(self.dns.read_bytes(), before)
        self.assert_safe_output_and_no_staging()

    def test_general_dns_corruption_and_permission_errors_never_trigger_replacement(self):
        self.protected(self.entra, CLIENT_SECRET)
        self.protected(self.dns, "{}")
        self.config["certificate"]["plugin"] = "Route53"
        with mock.patch.object(prepare_secrets, "_replace_dns") as replace:
            with self.assertRaises(ConfigurationError) as error:
                self.invoke([])
        self.assertNotIsInstance(error.exception, DnsProviderMismatchError)
        replace.assert_not_called()
        self.assertEqual(self.dns.read_text(), "{}")
        self.assert_safe_output_and_no_staging()

    def test_simultaneous_dns_replacement_is_refused_without_removing_the_other_lock(self):
        before = self.prepare_dns_change()
        lock = self.dns.with_name("." + self.dns.name + ".replace-lock")
        lock.write_text(""); lock.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "in progress"):
            self.invoke(["SYNTHETIC-NEW-ACCESS", "SYNTHETIC-NEW-SECRET"])
        self.assertEqual(self.dns.read_bytes(), before)
        self.assertTrue(lock.exists())
        self.assert_safe_output_and_no_staging()

    def test_cli_accepts_only_config_and_never_echoes_secret_arguments(self):
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            with self.assertRaises(SystemExit) as error:
                prepare_secrets.main(["--config", "public.json", "--token", TOKEN])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn(TOKEN, self.errors.getvalue())
        self.assertIn("hidden interactive prompts", self.errors.getvalue())

    def test_cli_success_prints_counts_only_and_loads_without_ssh(self):
        with mock.patch.object(prepare_secrets, "ROOT", self.root), \
                mock.patch.object(prepare_secrets, "load_configuration", return_value=self.config) as load, \
                mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", side_effect=[CLIENT_SECRET, TOKEN]), \
                mock.patch("builtins.input", return_value=""), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            self.assertEqual(prepare_secrets.main(["--config", "params/test.local.json"]), 0)
        load.assert_called_once_with(Path("params/test.local.json"))
        self.assertIn("2 created, 0 reused", self.output.getvalue())
        self.assertNotIn(TOKEN, self.output.getvalue() + self.errors.getvalue())
        self.assertNotIn(CLIENT_SECRET, self.output.getvalue() + self.errors.getvalue())
        self.assertFalse((self.root / "secrets/management-ssh").exists())

    def test_windows_permission_branch_does_not_require_unix_fchmod(self):
        path = self.root / "synthetic-staged-input"
        with path.open("w", encoding="utf-8") as stream, \
                mock.patch.object(prepare_secrets.os, "name", "nt"), \
                mock.patch.object(prepare_secrets.os, "fchmod", side_effect=AttributeError("not available"), create=True) as unix_permission:
            prepare_secrets._restrict_staged_permissions(stream, path)
        unix_permission.assert_not_called()

    def test_cli_write_encoding_error_does_not_disclose_its_secret_arguments(self):
        real_fdopen = os.fdopen
        private_error = UnicodeEncodeError("utf-8", CLIENT_SECRET, 0, len(CLIENT_SECRET), CLIENT_SECRET)
        def broken_writer(descriptor, *arguments, **keywords):
            stream = real_fdopen(descriptor, *arguments, **keywords)
            wrapped = mock.MagicMock(wraps=stream)
            wrapped.__enter__.return_value = wrapped
            wrapped.__exit__.side_effect = lambda *exception: stream.close()
            wrapped.write.side_effect = private_error
            return wrapped
        with mock.patch.object(prepare_secrets, "ROOT", self.root), \
                mock.patch.object(prepare_secrets, "load_configuration", return_value=self.config), \
                mock.patch.object(prepare_secrets.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(prepare_secrets.getpass, "getpass", return_value=TOKEN), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(prepare_secrets.os, "fdopen", side_effect=broken_writer), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            self.assertEqual(prepare_secrets.main(["--config", "public.json"]), 1)
        self.assertIn("input values were suppressed", self.errors.getvalue())
        self.assertNotIn(TOKEN, self.output.getvalue() + self.errors.getvalue())
        self.assertNotIn(CLIENT_SECRET, self.output.getvalue() + self.errors.getvalue())
        self.assertFalse(self.dns.exists())
        self.assertEqual(list(self.root.rglob(".secret-input-*")), [])

    def test_cli_interruption_is_safe_and_actionable(self):
        with mock.patch.object(prepare_secrets, "load_configuration", return_value=self.config), \
                mock.patch.object(prepare_secrets, "prepare_secrets", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            self.assertEqual(prepare_secrets.main(["--config", "public.json"]), 1)
        self.assertIn("interrupted", self.errors.getvalue())


if __name__ == "__main__":
    unittest.main()
