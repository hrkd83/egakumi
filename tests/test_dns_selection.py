"""DNS事業者の選択・params保存と再読込を、合成clone内だけで確認する。

DNSへ接続せず、秘密の入力・読出し・PowerShell・Azureの処理を使わない。
標準API宛先の再計算、入力中断、他プロセスによる設定変更の保護を調べる。
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers import configuration
from deployment.helpers import dns_selection


SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
IDENTITY = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns"


class DnsSelectionTests(unittest.TestCase):
    """公開params/configを一時cloneへコピーし、選択結果を実際の読込処理へ通す。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="core-dns-select-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "params/test.local.json"
        self.source.parent.mkdir()
        (self.root / "config").mkdir()
        self.raw = json.loads((CORE / "params/test.example.json").read_text())
        self.raw["subscriptionId"] = "11111111-1111-4111-8111-111111111111"
        self.raw["azureTenantId"] = "22222222-2222-4222-8222-222222222222"
        self.raw["entraTenantId"] = self.raw["azureTenantId"]
        self.source.write_text(json.dumps(self.raw, ensure_ascii=False, indent=2) + "\n")
        (self.root / "config/test.json").write_bytes((CORE / "config/test.json").read_bytes())
        self.config = configuration.load_configuration(self.source)
        self.output = io.StringIO()

    def invoke(self, answers=(), *, config=None, source=None, tty=True):
        with mock.patch.object(dns_selection.sys.stdin, "isatty", return_value=tty), \
             mock.patch("builtins.input", side_effect=answers) as prompt, \
             mock.patch.object(configuration, "_read_protected_file", side_effect=AssertionError("DNS selection must not read secrets")), \
             contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            result = dns_selection.select_dns_provider(source or self.source, self.config if config is None else config)
        return result, prompt

    def test_switching_standard_provider_updates_only_plugin_and_recalculates_api_allowlist(self):
        """旧事業者の自動宛先を残さず、保存paramsから新しい許可先を導出する。"""
        for answer, provider, hosts in (("2", "Route53", {"route53.amazonaws.com"}),
                                         ("3", "Azure", {"management.azure.com"})):
            with self.subTest(provider=provider):
                self.source.write_text(json.dumps(self.raw, ensure_ascii=False, indent=2) + "\n")
                passed = configuration.load_configuration(self.source)
                before = copy.deepcopy(passed)
                answers = [answer, SUBSCRIPTION, IDENTITY] if provider == "Azure" else [answer]
                result, prompt = self.invoke(answers, config=passed)
                saved = json.loads(self.source.read_text())
                expected = copy.deepcopy(self.raw)
                expected["certificate"]["plugin"] = provider
                if provider == "Azure":
                    expected["certificate"].update(azureDnsSubscriptionId=SUBSCRIPTION, azureDnsManagedIdentityResourceId=IDENTITY)
                self.assertEqual(saved, expected)
                self.assertEqual(result["certificate"]["plugin"], provider)
                if provider == 'Azure':
                    public = configuration.build_bicep_configuration(result)['certificate']
                    self.assertEqual(public['azureDnsSubscriptionId'], SUBSCRIPTION)
                    self.assertEqual(public['azureDnsManagedIdentityResourceId'], IDENTITY)
                    self.assertNotIn('pluginArgumentsFile', public)
                allowed = set(result["network"]["firewall"]["managementAllowedFqdns"])
                self.assertTrue(hosts.issubset(allowed))
                self.assertNotIn("api.cloudflare.com", allowed)
                self.assertEqual(passed, before)
                self.assertEqual(result, configuration.load_configuration(self.source))
                self.assertEqual(prompt.call_count, len(answers))
                self.assertFalse((self.root / "secrets").exists())

    def test_accepting_current_value_does_not_rewrite_params(self):
        """Enterと現在事業者の番号は、既存入力の内容・inode・更新時刻を変えない。"""
        for answer in ("", "1"):
            with self.subTest(answer=answer):
                before = self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns
                result, prompt = self.invoke([answer])
                self.assertIs(result, self.config)
                self.assertEqual(prompt.call_count, 1)
                self.assertEqual((self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns), before)

    def test_azure_missing_references_are_prompted_even_when_current_provider_is_azure(self):
        self.raw['certificate']['plugin'] = 'Azure'
        self.source.write_text(json.dumps(self.raw))
        config = configuration.load_configuration(self.source)
        result, prompt = self.invoke(['', SUBSCRIPTION, IDENTITY], config=config)
        self.assertEqual(prompt.call_count, 3)
        self.assertEqual(result['certificate']['azureDnsManagedIdentityResourceId'], IDENTITY)
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        result, prompt = self.invoke([''], config=result)
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime_ns), before)

    def test_azure_invalid_or_cancelled_input_never_saves_partial_selection(self):
        before = self.source.read_bytes()
        for answers in (['3', 'q'], ['3', SUBSCRIPTION, 'q'], ['3', 'not-a-guid', IDENTITY],
                        ['3', SUBSCRIPTION, SUBSCRIPTION], ['3', SUBSCRIPTION, EOFError()]):
            with self.subTest(answers=answers):
                with self.assertRaises(ValueError):
                    self.invoke(answers)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse((self.root / 'secrets').exists())

    def test_invalid_selection_is_reprompted_without_writing_supplied_text(self):
        """番号以外の入力値を設定へ混ぜず、選び直した標準事業者だけを保存する。"""
        marker = "SYNTHETIC-INVALID-SELECTION-DO-NOT-ECHO"
        result, prompt = self.invoke([marker, "9", "2"])
        self.assertEqual(result["certificate"]["plugin"], "Route53")
        self.assertEqual(prompt.call_count, 3)
        self.assertNotIn(marker, self.source.read_text())
        self.assertNotIn(marker, self.output.getvalue())

    def test_custom_provider_is_kept_by_choice_four_and_enter(self):
        """企業指定の別プラグインは、このメニューから推測して置き換えない。"""
        self.raw["certificate"]["plugin"] = "CompanyDns"
        self.raw["network"]["firewall"]["managementAllowedFqdns"].append("dns-api.example.com")
        self.source.write_text(json.dumps(self.raw))
        config = configuration.load_configuration(self.source)
        for answer in ("4", ""):
            with self.subTest(answer=answer):
                before = self.source.read_bytes()
                result, _ = self.invoke([answer], config=config)
                self.assertIs(result, config)
                self.assertEqual(result["certificate"]["plugin"], "CompanyDns")
                self.assertEqual(self.source.read_bytes(), before)

    def test_non_tty_and_prepared_mode_never_prompt_or_open_source(self):
        """CIで選択を勝手に変えず、持込み証明書の利用でもDNS選択を始めない。"""
        prepared = copy.deepcopy(self.config)
        prepared["certificate"] = {"mode": "prepared"}
        missing = self.root / "not-present.local.json"
        for tty, config in ((False, self.config), (True, prepared)):
            with self.subTest(tty=tty), mock.patch.object(dns_selection, "load_configuration") as reload_config:
                result, prompt = self.invoke(config=config, source=missing, tty=tty)
                self.assertIs(result, config)
                prompt.assert_not_called()
                reload_config.assert_not_called()
        self.assertFalse(missing.exists())

    def test_cancel_and_end_of_input_preserve_params_and_create_no_secrets(self):
        for answer in ("q", EOFError(), KeyboardInterrupt()):
            with self.subTest(kind=type(answer).__name__):
                before = self.source.read_bytes()
                with self.assertRaises((ValueError, EOFError, KeyboardInterrupt)):
                    self.invoke([answer])
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse((self.root / "secrets").exists())

    def test_public_example_and_symbolic_link_are_never_modified(self):
        original = self.source.read_bytes()
        example = self.source.with_name("test.example.json")
        example.write_bytes(original)
        link = self.source.with_name("linked.local.json")
        link.symlink_to(self.source)
        for target in (example, link):
            with self.subTest(source=target.name):
                with self.assertRaises(ValueError):
                    self.invoke(["2"], source=target)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual(example.read_bytes(), original)
                self.assertTrue(link.is_symlink())

    def test_invalid_staged_configuration_preserves_original_and_removes_staging_file(self):
        """保存予定の全体設定が不整合なら、元paramsを置換せず一時ファイルも回収する。"""
        marker = "SYNTHETIC-STAGE-ERROR-DO-NOT-ECHO"
        before = self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns
        real_load = configuration.load_configuration

        def validate_source_only(path):
            if Path(path) != self.source:
                raise configuration.ConfigurationError(marker)
            return real_load(path)

        with mock.patch.object(dns_selection, "load_configuration", side_effect=validate_source_only):
            with self.assertRaises(ValueError) as failure:
                self.invoke(["2"])
        self.assertNotIn(marker, str(failure.exception) + self.output.getvalue())
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns), before)
        self.assertEqual(list(self.source.parent.glob(".dns-provider-*")), [])
        self.assertFalse((self.root / "secrets").exists())

    def test_atomic_replace_failure_preserves_original_and_removes_staging_file(self):
        """OSが保存を拒否した場合も、元paramsと設定値を維持して安全な案内だけを出す。"""
        marker = "SYNTHETIC-REPLACE-ERROR-DO-NOT-ECHO"
        before = self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns
        with mock.patch.object(dns_selection.os, "replace", side_effect=OSError(marker)) as replace:
            with self.assertRaises(ValueError) as failure:
                self.invoke(["2"])
        replace.assert_called_once()
        self.assertNotIn(marker, str(failure.exception) + self.output.getvalue())
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns), before)
        self.assertEqual(list(self.source.parent.glob(".dns-provider-*")), [])
        self.assertEqual(self.config["certificate"]["plugin"], "Cloudflare")
        self.assertFalse((self.root / "secrets").exists())

    def test_concurrent_params_edit_during_prompt_is_preserved_instead_of_overwritten(self):
        """選択中に他の人が編集したparamsへ、自分の古い内容を上書きしない。"""
        newer = copy.deepcopy(self.raw)
        newer["tags"]["parallelEdit"] = "keep-this-edit"
        changed = json.dumps(newer, ensure_ascii=False, indent=2) + "\n"

        def choose_after_edit(prompt):
            self.source.write_text(changed)
            return "2"

        with mock.patch.object(dns_selection.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=choose_after_edit), \
             contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            with self.assertRaises(ValueError):
                dns_selection.select_dns_provider(self.source, self.config)
        self.assertEqual(self.source.read_text(), changed)
        self.assertFalse((self.root / "secrets").exists())


if __name__ == "__main__":
    unittest.main()
