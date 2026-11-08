"""Azure Public DNSの固定前提を案内し、対話やparams更新をしないことを確認する。"""
from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import dns_selection


SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
IDENTITY = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns"


class AzureDnsPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="azure-dns-prerequisite-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "params.json"
        self.config = {"certificate": {
            "mode": "dnsPlugin", "plugin": "Azure", "azureDnsSubscriptionId": SUBSCRIPTION,
            "azureDnsManagedIdentityResourceId": IDENTITY,
        }}
        self.output = io.StringIO()

    def invoke(self, config=None):
        with mock.patch("builtins.input") as prompt, contextlib.redirect_stdout(self.output):
            result = dns_selection.select_dns_provider(self.source, self.config if config is None else config)
        prompt.assert_not_called()
        return result

    def test_azure_prerequisites_are_shown_without_selection_or_params_creation(self):
        before = copy.deepcopy(self.config)
        result = self.invoke()
        self.assertIs(result, self.config)
        self.assertEqual(self.config, before)
        self.assertFalse(self.source.exists())
        text = self.output.getvalue()
        for required in ("Azure Public DNS", "NS委任", "マネージドID", "DNS Zone Contributor", "DNS-01"):
            self.assertIn(required, text)
        for unsupported in ("Cloudflare", "Route53", "PFX", "1.", "選択 ["):
            self.assertNotIn(unsupported, text)
        self.assertNotIn(SUBSCRIPTION, text)
        self.assertNotIn(IDENTITY, text)

    def test_existing_params_are_neither_read_nor_rewritten(self):
        self.source.write_text("not parsed here")
        before = self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns
        self.invoke()
        self.assertEqual(before, (self.source.read_bytes(), self.source.stat().st_ino, self.source.stat().st_mtime_ns))

    def test_noninteractive_execution_has_the_same_fixed_azure_behavior(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            self.assertIs(self.invoke(), self.config)

    def test_other_providers_and_prepared_certificates_are_rejected_without_input(self):
        for certificate in ({}, {"mode": "prepared"},
                            *({"mode": "dnsPlugin", "plugin": provider} for provider in ("Cloudflare", "Route53", "OtherProvider"))):
            with self.subTest(certificate=certificate), mock.patch("builtins.input") as prompt:
                with self.assertRaisesRegex(dns_selection.DnsSelectionError, "Azure Public DNS"):
                    self.invoke({"certificate": certificate})
                prompt.assert_not_called()
                self.assertFalse(self.source.exists())

    def test_missing_or_invalid_azure_identity_is_not_prompted_for(self):
        for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
            for bad in (None, "invalid-synthetic-identity"):
                config = copy.deepcopy(self.config)
                if bad is None:
                    del config["certificate"][key]
                else:
                    config["certificate"][key] = bad
                with self.subTest(key=key, bad=bad), mock.patch("builtins.input") as prompt:
                    with self.assertRaisesRegex(dns_selection.DnsSelectionError, "params") as error:
                        self.invoke(config)
                    self.assertNotIn("invalid-synthetic-identity", str(error.exception))
                    prompt.assert_not_called()
                    self.assertFalse(self.source.exists())


if __name__ == "__main__":
    unittest.main()
