"""Execute the actual inline login JavaScript with a fake DOM and HTTP response.

No browser, Azure request, or credential is used. The allowed CM destinations,
one-time fragment handling, and failure paths are checked in the shipped script.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from deployment.helpers import credential_login
from test_credential_login import settings, TENANT


POLICY = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies/credential-login/launch.xml"
TRANSACTION = "a" * 64
LAUNCH = "c" * 64
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
(async function () {
  const results = {};
  for (const item of input.cases) {
    const events = [], requests = [];
    let assigned = null;
    const message = { textContent: '' };
    const location = { hash: item.fragment, pathname: '/auth/connections/browser/launch',
      assign: value => { assigned = value; events.push('assign'); } };
    const context = {
      URL, URLSearchParams, location, atob: value => Buffer.from(value, 'base64').toString('utf8'),
      history: { replaceState: (_state, _title, value) => {
        if (value !== location.pathname) throw Error('Unexpected replacement path');
        location.hash = ''; events.push('clear-fragment');
      } },
      document: { querySelector: selector => {
        if (selector !== 'meta[name="core-identity-tenants"]') throw Error('Unexpected selector');
        return {content: input.tenants};
      }, getElementById: id => {
        if (id !== 'message') throw Error('Unexpected DOM element');
        return message;
      } },
      fetch: async (url, options) => {
        events.push('fetch'); requests.push({url, ...options});
        if (item.networkFailure) throw Error('Synthetic transport error');
        return { ok: item.ok !== false, json: async () => {
          if (item.invalidJson) throw Error('Synthetic invalid JSON');
          return {loginUrl: item.loginUrl};
        } };
      }
    };
    // Await the shipped async bootstrap; no click or other interaction is simulated.
    await vm.runInNewContext(input.script, context, {timeout: 1000});
    results[item.id] = {assigned, events, requests,
      message: message.textContent, fragment: location.hash};
  }
  process.stdout.write(JSON.stringify(results));
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""


class CredentialLoginBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = os.environ.get("WORKFORCE_NODE") or shutil.which("node")
        if not node:
            raise unittest.SkipTest("Node.js is required for the shipped login JavaScript")
        root = ET.fromstring(credential_login.render("launch.xml", settings()))
        html = root.find("inbound/return-response/set-body").text
        cls.html = html
        cls.script = re.search(r"<script>(.*?)</script>", html).group(1)
        cls.style = re.search(r"<style>(.*?)</style>", html).group(1)
        cls.csp = root.find("inbound/return-response/set-header[@name='Content-Security-Policy']/value").text
        fragment = "#transactionId=" + TRANSACTION + "&launch=" + LAUNCH
        cls.allowed = {
            "configured-tenant": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state=fixture",
            "port443": f"https://login.microsoftonline.com:443/{TENANT}/oauth2/v2.0/authorize",
        }
        cls.denied = {
            "foreign": "https://example.com/redirect",
            "other-tenant": "https://login.microsoftonline.com/22222222-2222-4222-8222-222222222222/oauth2/v2.0/authorize",
            "common": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "wrong-operation": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/logout",
            "path-suffix": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize/extra",
            "suffix": "https://logic-apis-japaneast.consent.azure-apim.net.example.com/redirect",
            "prefix": "https://evil-logic-apis-japaneast.consent.azure-apim.net/redirect",
            "empty-region": "https://logic-apis-.consent.azure-apim.net/redirect",
            "invalid-region": "https://logic-apis-japan_east.consent.azure-apim.net/redirect",
            "wrong-domain": "https://logic-apis-japaneast.consent.azure-apim.com/redirect",
            "userinfo": "https://user:fixture@logic-apis-japaneast.consent.azure-apim.net/redirect",
            "http": "http://logic-apis-japaneast.consent.azure-apim.net/redirect",
            "port444": "https://logic-apis-japaneast.consent.azure-apim.net:444/redirect",
            "fragment": "https://logic-apis-japaneast.consent.azure-apim.net/redirect#fixture",
            "relative": "/redirect",
            "null": None,
            "malformed": "https://[invalid",
        }
        cases = [{"id": name, "loginUrl": url, "fragment": fragment}
                 for name, url in {**cls.allowed, **cls.denied}.items()]
        cls.invalid_fragments = {
            "missing-fragment": "",
            "missing-launch": "#transactionId=" + TRANSACTION,
            "bad-transaction": "#transactionId=not-hex&launch=" + LAUNCH,
            "extra-field": fragment + "&unexpected=value",
            "duplicate-transaction": fragment + "&transactionId=" + TRANSACTION,
            "duplicate-launch": fragment + "&launch=" + LAUNCH,
        }
        cases += [{"id": name, "fragment": value, "loginUrl": cls.allowed["configured-tenant"]}
                  for name, value in cls.invalid_fragments.items()]
        cases += [
            {"id": "http-failure", "fragment": fragment, "ok": False, "loginUrl": cls.allowed["configured-tenant"]},
            {"id": "network-failure", "fragment": fragment, "networkFailure": True},
            {"id": "invalid-json", "fragment": fragment, "invalidJson": True},
        ]
        with tempfile.TemporaryDirectory(prefix="core-credential-browser-") as directory:
            path = Path(directory)
            harness = path / "harness.js"
            fixture = path / "input.json"
            harness.write_text(HARNESS, encoding="utf-8")
            fixture.write_text(json.dumps({"script": cls.script, "cases": cases, "tenants": credential_login.encode([TENANT])}), encoding="utf-8")
            result = subprocess.run([node, str(harness), str(fixture)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            if result.returncode:
                raise AssertionError("Login JavaScript execution failed: " + result.stderr)
            cls.results = json.loads(result.stdout)

    def test_azure_consent_hosts_navigate_successfully(self):
        for name in self.allowed:
            with self.subTest(host=name):
                result = self.results[name]
                self.assertIsNotNone(result["assigned"])
                self.assertEqual(result["message"], "Microsoftの認証画面へ移動しています…")
                self.assertEqual(result["events"], ["clear-fragment", "fetch", "assign"])

    def test_untrusted_or_malformed_login_urls_never_navigate(self):
        for name in self.denied:
            with self.subTest(url=name):
                result = self.results[name]
                self.assertIsNone(result["assigned"])
                self.assertIn("元のアプリで接続を開始し直してください", result["message"])
                self.assertEqual(result["events"], ["clear-fragment", "fetch"])

    def test_request_uses_same_origin_post_and_clears_fragment_first(self):
        result = self.results["configured-tenant"]
        self.assertEqual(result["fragment"], "")
        request, = result["requests"]
        self.assertEqual(request["url"], "/auth/connections/browser/launch")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["credentials"], "same-origin")
        self.assertEqual(request["redirect"], "error")
        self.assertEqual(request["cache"], "no-store")
        self.assertEqual(request["headers"], {"Content-Type": "application/json",
                                             "X-Core-Connection-Transaction": TRANSACTION})
        self.assertEqual(json.loads(request["body"]), {"transactionId": TRANSACTION, "launch": LAUNCH})

    def test_transport_and_json_failures_do_not_navigate(self):
        for name in ("http-failure", "network-failure", "invalid-json"):
            with self.subTest(failure=name):
                self.assertIsNone(self.results[name]["assigned"])
                self.assertIn("元のアプリで接続を開始し直してください", self.results[name]["message"])
                self.assertEqual(len(self.results[name]["requests"]), 1)

    def test_invalid_fragment_does_not_send_a_request(self):
        for name in self.invalid_fragments:
            with self.subTest(fragment=name):
                result = self.results[name]
                self.assertEqual(result["requests"], [])
                self.assertIn("元のアプリで接続を開始し直してください", result["message"])
                self.assertIsNone(result["assigned"])
                self.assertEqual(result["fragment"], "")

    def test_csp_hash_matches_the_shipped_script(self):
        digest = base64.b64encode(hashlib.sha256(self.script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + digest + "'", self.csp)
        style_digest = base64.b64encode(hashlib.sha256(self.style.encode()).digest()).decode()
        self.assertIn("style-src 'sha256-" + style_digest + "'", self.csp)
        self.assertIn("connect-src 'self'", self.csp)
        self.assertNotIn("unsafe-inline", self.csp)

    def test_login_starts_automatically_without_a_button(self):
        self.assertNotIn("<button", self.html)
        self.assertNotIn("addEventListener", self.script)
        self.assertEqual(len(self.results["configured-tenant"]["requests"]), 1)


if __name__ == "__main__":
    unittest.main()
